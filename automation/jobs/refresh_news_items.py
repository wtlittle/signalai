"""
Refresh server-side news items into the Supabase `news_items` table.

Runs hourly via GitHub Actions (.github/workflows/refresh_news.yml). Replaces
the per-browser Perplexity key dependency: instead of every News-tab visitor
firing their own sonar call, this single batched job pulls material business
news for the coverage universe, parses it into structured rows, and upserts
them into Supabase. The client then reads pre-aggregated rows with the anon
read pattern (PR #37).

Pipeline:
    load coverage tickers (utils.js DEFAULT_TICKERS, capped ~50)
      -> single batched sonar call (model='sonar', max_tokens=4000, 60s)
      -> parse structured JSON {title, body, url, source, ticker,
         published_at_iso, signal}
      -> derive url_host + publisher name, id = sha256(url||title)[:16]
      -> upsert into news_items (merge-duplicates on id)
      -> delete stale rows (published_at < now-7d AND fetched_at < now-3d)
      -> log usage to perplexity_api_usage (source='news_refresh')

Failure modes:
    sonar API error    -> log + exit non-zero (GH Actions surfaces failure)
    malformed JSON      -> log + continue with whatever parsed
    Supabase write error -> log + exit non-zero

Usage:
    PERPLEXITY_API_KEY=... SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \\
    python3 -m automation.jobs.refresh_news_items
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

# Make `automation` importable when run as a module or script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.tickers import load_tickers  # noqa: E402
from automation.perplexity.client import _log_pplx_usage  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TABLE = "news_items"
SOURCE_ORIGIN = "perplexity_sonar"
MODEL = "sonar"  # NOT sonar-deep-research — too expensive at hourly cadence
MAX_TOKENS = 4000
TIMEOUT_S = 60
MAX_TICKERS = 50  # sonar prompt budget
BASE_URL = "https://api.perplexity.ai/chat/completions"

PERPLEXITY_API_KEY = (
    os.environ.get("PERPLEXITY_API_KEY")
    or os.environ.get("PPLX_API_KEY")
    or ""
)

VALID_SIGNALS = {
    "earnings", "guidance_up", "guidance_dn", "ma", "analyst",
    "regulatory", "product", "macro", "general",
}

# Host -> publisher display name. Capitalized-host fallback handles the rest.
PUBLISHER_BY_HOST = {
    "bloomberg.com": "Bloomberg",
    "reuters.com": "Reuters",
    "ft.com": "Financial Times",
    "wsj.com": "WSJ",
    "cnbc.com": "CNBC",
    "nytimes.com": "The New York Times",
    "marketwatch.com": "MarketWatch",
    "barrons.com": "Barron's",
    "businesswire.com": "Business Wire",
    "prnewswire.com": "PR Newswire",
    "globenewswire.com": "GlobeNewswire",
    "seekingalpha.com": "Seeking Alpha",
    "fool.com": "Motley Fool",
    "investors.com": "Investor's Business Daily",
    "theinformation.com": "The Information",
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "axios.com": "Axios",
    "forbes.com": "Forbes",
    "yahoo.com": "Yahoo Finance",
    "finance.yahoo.com": "Yahoo Finance",
    "apnews.com": "AP",
    "theguardian.com": "The Guardian",
}

# Perplexity requires response_format.type == "json_schema" (OpenAI's
# "json_object" is rejected with HTTP 400). The schema is advisory — sonar may
# return extra top-level fields (search_results, citations) alongside the
# json_schema content; the parse path tolerates that.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "url": {"type": "string"},
                            "source": {"type": "string"},
                            "ticker": {"type": "string"},
                            "published_at_iso": {"type": "string"},
                            "signal": {"type": "string"},
                        },
                        "required": ["title", "url", "ticker"],
                    },
                }
            },
            "required": ["items"],
        }
    },
}

SYSTEM_PROMPT = (
    "You are a financial news desk editor for a buy-side equity research team. "
    "Surface notable, significant business news from the last 7 days about "
    "the tickers provided. Notable news means: quarterly earnings results, "
    "forward guidance changes, mergers & acquisitions, analyst rating or price "
    "target changes, regulatory or legal actions, and major product launches. "
    "STRICTLY EXCLUDE: listicles, 'X things to know' / 'what to watch' roundups, "
    "pure stock-price or technical-trading commentary, opinion/predictions with "
    "no new fact, and anything older than 7 days. "
    "Prefer the primary publisher's URL when available; otherwise use the most "
    "authoritative reputable secondary source. "
    "Set signal to one of: earnings, guidance_up, guidance_dn, ma, analyst, "
    "regulatory, product, macro, general. "
    "Return ONLY a single valid JSON object. No markdown fences, no commentary."
)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def build_prompt(tickers: list[str]) -> str:
    """Build the user prompt listing coverage tickers and the JSON contract."""
    ticker_list = ", ".join(tickers)
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    return (
        f"Coverage tickers: {ticker_list}\n\n"
        f"Find 25-40 of the most notable, significant business-news stories from "
        f"the last 7 days about any of these tickers. One story per item; no "
        f"duplicates. Return a JSON object with exactly this shape:\n\n"
        f"{{\n"
        f'  "items": [\n'
        f"    {{\n"
        f'      "title": "<headline, no source prefix>",\n'
        f'      "body": "<one-sentence factual summary>",\n'
        f'      "url": "<direct link to the original article (must be non-empty)>",\n'
        f'      "source": "<publisher name, e.g. Bloomberg>",\n'
        f'      "ticker": "<primary ticker symbol from the coverage list>",\n'
        f'      "published_at_iso": "<ISO-8601 timestamp, UTC>",\n'
        f'      "signal": "<one of: earnings, guidance_up, guidance_dn, ma, '
        f'analyst, regulatory, product, macro, general>"\n'
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"Prefer articles from primary publishers when available — e.g. Reuters, "
        f"Bloomberg, WSJ, FT, CNBC, Barron's, The Information, Axios, AP, "
        f"NYT business, MarketWatch, SeekingAlpha (for analyst notes), and "
        f"company IR newsrooms (e.g. aboutamazon.com). This is a preference, not "
        f"a hard requirement: include a reputable secondary source rather than "
        f"dropping a real story or returning a placeholder. "
        f"Every item MUST have a non-empty url pointing to a real article. "
        f"Set signal to the single best-fitting category. Use guidance_up when "
        f"guidance was raised and guidance_dn when cut. Use ma for merger/"
        f"acquisition news, analyst for rating/PT changes, regulatory for "
        f"regulatory/legal actions, product for major product launches, macro for "
        f"sector-wide items, and general otherwise. "
        f"Include published_at_iso (ISO-8601, UTC) for every item; if the "
        f"article's date is unknown, set it to today ({today}). "
        f"Never invent a URL."
    )


# ---------------------------------------------------------------------------
# Sonar call (direct, with shared usage instrumentation)
# ---------------------------------------------------------------------------
def call_sonar(prompt: str) -> dict[str, Any]:
    """Single batched sonar call. Logs usage to perplexity_api_usage.

    Raises on transport/HTTP error so the job exits non-zero in CI.
    """
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.2,
        "response_format": RESPONSE_FORMAT,
    }

    resp = None
    resp_json = None
    status = "error"
    err: Exception | None = None
    t0 = time.perf_counter()
    try:
        resp = requests.post(BASE_URL, headers=headers, json=body, timeout=TIMEOUT_S)
        if resp.status_code >= 300:
            # raise_for_status() swallows the body; surface it for debugging.
            print(
                f"  [SONAR HTTP {resp.status_code}] response body "
                f"(first 2000 chars): {resp.text[:2000]}"
            )
        resp.raise_for_status()
        resp_json = resp.json()
        status = "success"
    except requests.exceptions.Timeout as exc:
        status = "timeout"
        err = exc
        raise
    except Exception as exc:
        status = "error"
        err = exc
        raise
    finally:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        # source='news_refresh' (task name falls through _TASK_SOURCE_MAP as-is).
        _log_pplx_usage(
            task="news_refresh",
            ticker="",
            model=MODEL,
            response_json=resp_json,
            latency_ms=latency_ms,
            status=status,
            error=err,
        )

    content = resp_json["choices"][0]["message"]["content"]
    return _parse_content(content)


def _parse_content(content: str) -> dict[str, Any]:
    """Parse the model JSON payload, tolerating fences and trailing prose."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip trailing commas, then retry.
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Extract the first {...} object.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidate = re.sub(r",\s*([}\]])", r"\1", match.group(0))
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    print(f"  [PARSE FAIL] sonar payload unparseable; first 200: {content[:200]!r}")
    return {}


# ---------------------------------------------------------------------------
# Field derivation
# ---------------------------------------------------------------------------
def extract_host(url: str) -> str:
    """Return the bare host (no www.) for a URL, or '' if unparseable."""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def capitalize_host(host: str) -> str:
    """Fallback publisher name from a host, e.g. 'example.com' -> 'Example'."""
    if not host:
        return "News"
    base = host.split(".")[0]
    return base.capitalize() if base else "News"


def derive_source(url_host: str, given: str) -> str:
    """Publisher display name: explicit value > host map > capitalized host."""
    if url_host in PUBLISHER_BY_HOST:
        return PUBLISHER_BY_HOST[url_host]
    if given and given.strip():
        return given.strip()
    return capitalize_host(url_host)


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def make_id(url: str, title: str) -> str:
    """id = sha256(url || normalized_title)[:16]."""
    basis = url.strip() if url and url.strip() else _normalize_title(title)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


# Defensive filler filter. The loosened prompt phrasing lands more results on
# quiet windows but also lets sonar slip in the listicle/comparison filler the
# prompt asks it to exclude. Drop the obvious offenders by title/host so the
# substance bar stays where the prompt intends.
_FILLER_TITLE_RE = re.compile(
    r"stocks?\s+to\s+watch"
    r"|things?\s+to\s+know"
    r"|what\s+to\s+watch"
    r"|\btop\s+\d+\b"
    r"|\bbest\s+\d+\b"
    r"|\b\w+(?:\s*,\s*\w+)*\s+vs\.?\s+\w+"  # "OKTA vs CRWD", "A, B, C vs D"
    r"|no\s+qualifying\s+stories",
    re.IGNORECASE,
)
_FILLER_HOSTS = {"youtube.com", "youtu.be"}


def _is_filler(title: str, url_host: str) -> bool:
    if url_host in _FILLER_HOSTS:
        return True
    return bool(_FILLER_TITLE_RE.search(title or ""))


def _coerce_signal(value: Any) -> str:
    v = (value or "").strip().lower()
    return v if v in VALID_SIGNALS else "general"


def _coerce_ts(value: Any) -> str | None:
    """Normalize an ISO-8601-ish timestamp to ISO string, or None."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        ts = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return ts.isoformat()


def normalize_item(item: dict[str, Any], coverage: set[str]) -> dict[str, Any] | None:
    """Turn one raw model item into a news_items row, or None if unusable."""
    if not isinstance(item, dict):
        return None
    title = (item.get("title") or "").strip()
    if not title:
        return None

    url = (item.get("url") or "").strip()
    if not url:
        # Placeholder-style emissions ("Insufficient live-news coverage...")
        # arrive with an empty URL; drop them regardless of title wording.
        return None
    url_host = extract_host(url)
    if _is_filler(title, url_host):
        return None
    source = derive_source(url_host, item.get("source") or "")

    primary = (item.get("ticker") or "").strip().upper()
    tickers: list[str] = []
    if primary:
        tickers.append(primary)
    # Pick up any other coverage tickers explicitly mentioned in title/body.
    haystack = f"{title} {item.get('body') or ''}".upper()
    for sym in coverage:
        if sym == primary:
            continue
        if re.search(rf"\b{re.escape(sym)}\b", haystack):
            tickers.append(sym)
    tickers = list(dict.fromkeys(tickers))  # dedupe, keep order

    fetched_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    # Never write NULL published_at: PostgREST's gte filter excludes NULLs, so a
    # row with no publish date is invisible to the 48h client read. When sonar
    # omits/returns an unparseable date, fall back to fetched_at (≈ now).
    published_at = _coerce_ts(item.get("published_at_iso")) or fetched_at

    return {
        "id": make_id(url, title),
        "fetched_at": fetched_at,
        "source_origin": SOURCE_ORIGIN,
        "ticker": primary or (tickers[0] if tickers else None),
        "tickers": tickers,
        "title": title,
        "body": (item.get("body") or "").strip() or None,
        "url": url or None,
        "url_host": url_host or None,
        "source": source,
        "published_at": published_at,
        "signal": _coerce_signal(item.get("signal")),
    }


# ---------------------------------------------------------------------------
# Supabase REST helpers
# ---------------------------------------------------------------------------
def _normalize_supabase_url(raw: str) -> str:
    """Strip trailing slashes and ensure an http(s):// scheme is present.

    The SUPABASE_URL secret is sometimes set without a scheme (e.g.
    'proj.supabase.co'), which makes requests raise 'No connection adapters
    were found'. Default to https:// when no scheme is given.
    """
    url = (raw or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _supabase() -> tuple[str, dict[str, str]]:
    url = _normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for live runs"
        )
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    return url, headers


def _existing_ids(url: str, headers: dict[str, str], ids: list[str]) -> set[str]:
    """Return the subset of ids that already exist (to count new vs updated)."""
    if not ids:
        return set()
    found: set[str] = set()
    # Chunk the in.() filter to keep URLs short.
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        in_list = ",".join(chunk)
        resp = requests.get(
            f"{url}/rest/v1/{TABLE}",
            headers=headers,
            params={"select": "id", "id": f"in.({in_list})"},
            timeout=30,
        )
        if resp.status_code == 404:
            return set()
        resp.raise_for_status()
        for row in resp.json() or []:
            if row.get("id"):
                found.add(row["id"])
    return found


def _upsert_rows(url: str, headers: dict[str, str], rows: list[dict[str, Any]]) -> None:
    """Bulk upsert rows, merging duplicates on id. Raises on HTTP error."""
    hdrs = dict(headers)
    hdrs["Prefer"] = "resolution=merge-duplicates,return=minimal"
    resp = requests.post(
        f"{url}/rest/v1/{TABLE}?on_conflict=id",
        headers=hdrs,
        data=json.dumps(rows, default=str),
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Supabase upsert failed: HTTP {resp.status_code} {resp.text[:300]}"
        )


def _delete_stale(url: str, headers: dict[str, str]) -> int:
    """Delete rows with published_at < now-7d AND fetched_at < now-3d.

    Returns the number of rows deleted.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    pub_cutoff = (now - _dt.timedelta(days=7)).isoformat()
    fetch_cutoff = (now - _dt.timedelta(days=3)).isoformat()
    hdrs = dict(headers)
    hdrs["Prefer"] = "return=representation"
    resp = requests.delete(
        f"{url}/rest/v1/{TABLE}",
        headers=hdrs,
        params={
            "published_at": f"lt.{pub_cutoff}",
            "fetched_at": f"lt.{fetch_cutoff}",
            "select": "id",
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Supabase stale-delete failed: HTTP {resp.status_code} {resp.text[:300]}"
        )
    try:
        return len(resp.json() or [])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------
def run() -> int:
    if not PERPLEXITY_API_KEY:
        print("[refresh_news_items] FATAL: PERPLEXITY_API_KEY not set")
        return 1

    normalized_url = _normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    print(f"[refresh_news_items] SUPABASE_URL (normalized): {normalized_url or '(unset)'}")

    all_tickers = load_tickers()
    tickers = all_tickers[:MAX_TICKERS]
    coverage = set(tickers)
    print(f"[refresh_news_items] coverage: {len(tickers)} tickers (of {len(all_tickers)})")

    prompt = build_prompt(tickers)

    try:
        parsed = call_sonar(prompt)
    except Exception as exc:
        print(f"[refresh_news_items] FATAL: sonar call failed: {exc}")
        return 1

    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        # Tolerate a bare list, or a single object.
        if isinstance(parsed, list):
            raw_items = parsed
        else:
            raw_items = []
    print(f"[refresh_news_items] parsed {len(raw_items)} raw items")

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_items:
        row = normalize_item(item, coverage)
        if not row:
            continue
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        rows.append(row)

    if not rows:
        print("[refresh_news_items] no usable items after normalization; nothing to upsert")
        # Still attempt stale cleanup below for table hygiene.

    try:
        sb_url, sb_headers = _supabase()
    except Exception as exc:
        print(f"[refresh_news_items] FATAL: {exc}")
        return 1

    new_count = updated_count = 0
    try:
        if rows:
            existing = _existing_ids(sb_url, sb_headers, [r["id"] for r in rows])
            updated_count = sum(1 for r in rows if r["id"] in existing)
            new_count = len(rows) - updated_count
            _upsert_rows(sb_url, sb_headers, rows)
    except Exception as exc:
        print(f"[refresh_news_items] FATAL: Supabase write failed: {exc}")
        return 1

    try:
        stale_deleted = _delete_stale(sb_url, sb_headers)
    except Exception as exc:
        print(f"[refresh_news_items] FATAL: Supabase cleanup failed: {exc}")
        return 1

    print(
        f"Refreshed {len(rows)} items "
        f"({new_count} new, {updated_count} updated, {stale_deleted} stale-deleted)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
