"""
Transcript Harvest — earnings call transcript scraping + LLM distillation.

For every ticker in the post-earnings window (sourced from earnings_calendar.json),
this job:

  1. Checks Supabase for a fresh row in `transcript_intel` — skips if within TTL.
  2. Attempts to scrape the most-recent earnings call transcript from
     The Motley Fool (free, no auth, HTML scraping via BeautifulSoup).
  3. Falls back to a Perplexity-native research prompt if Fool scraping
     fails (rate-limited, CAPTCHA, or no page found).
  4. Sends the transcript text (or research prompt) to Perplexity for
     structured distillation into the canonical `transcript_intel` schema.
  5. Upserts the result into Supabase `transcript_intel` table.

Usage:
    # Dry-run (lists tickers, no API/Supabase calls)
    python -m automation.jobs.transcript_harvest --dry-run

    # Live run
    SUPABASE_URL=https://xxx.supabase.co \\
    SUPABASE_SERVICE_KEY=sb_secret_... \\
    python -m automation.jobs.transcript_harvest

    # Force-refresh specific ticker
    python -m automation.jobs.transcript_harvest --ticker MSFT --force

Acceptance tests:
    python -m automation.jobs.transcript_harvest --dry-run
    python -c "from automation.jobs.transcript_harvest import run; print('ok')"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import EARNINGS_CALENDAR, ROOT_DIR  # noqa: E402
from automation.shared.io_helpers import read_json               # noqa: E402
from automation.shared.tickers import load_common_names          # noqa: E402
from automation.perplexity.client import call_perplexity         # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TRANSCRIPT_TTL_DAYS    = int(os.environ.get("TRANSCRIPT_TTL_DAYS", "30"))
MAX_POST_EARNINGS_DAYS = int(os.environ.get("MAX_POST_EARNINGS_DAYS", "14"))
TABLE                  = "transcript_intel"
REQUEST_TIMEOUT        = int(os.environ.get("TRANSCRIPT_REQUEST_TIMEOUT", "20"))
FOOL_DELAY_SECS        = float(os.environ.get("FOOL_DELAY_SECS", "2.0"))  # polite crawl delay

FOOL_SEARCH_URL = "https://www.fool.com/earnings-call-transcripts/?ticker={ticker}"

# finance_earnings_transcript connector (priority 1 / PRIMARY). Reached through
# the preinstalled ``external-tool`` CLI (subprocess), exactly like
# PerplexityFinanceSource — there is no agent runtime in the cron, so the
# platform connector is called programmatically. A transcript is accepted only
# when it is both substantial (>= MIN chars) AND for the target earnings_date
# (never a stale prior-quarter transcript).
FINANCE_SOURCE_ID       = "finance"
FINANCE_TRANSCRIPT_TOOL = "finance_earnings_transcript"
FINANCE_TIMEOUT         = int(os.environ.get("FINANCE_TOOL_TIMEOUT", "60"))
MIN_TRANSCRIPT_CHARS    = int(os.environ.get("MIN_TRANSCRIPT_CHARS", "3000"))

SYSTEM_PROMPT = (
    "You are a senior buy-side equity research analyst. "
    "Your job is to distill earnings call transcripts into structured, "
    "actionable intelligence. Return ONLY a single valid JSON object with "
    "the exact keys requested. No markdown fences, no commentary, no prose."
)

# ---------------------------------------------------------------------------
# Transcript schema
# ---------------------------------------------------------------------------
EMPTY_SCHEMA: dict[str, Any] = {
    "ticker":               None,
    "company_name":         None,
    "earnings_date":        None,
    "quarter":              None,
    "transcript_source":    None,
    "transcript_url":       None,
    "beat_miss_summary":    None,
    "management_tone":      None,
    "mgmt_key_points":      [],
    "guidance_statements":  [],
    "qa_key_exchanges":     [],
    "tone_signals":         [],
    "key_metrics_discussed": [],
    "notable_quotes":       [],
    "risk_factors_cited":   [],
    "harvested_at":         None,
}


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def _supabase_client():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for live runs"
        )
    import requests
    headers = {
        "apikey":          key,
        "Authorization":   f"Bearer {key}",
        "Content-Type":    "application/json",
        "Prefer":          "return=representation,resolution=merge-duplicates",
    }
    return requests, url, headers


def _load_existing_rows(requests_mod, url: str, headers: dict) -> dict[str, dict]:
    """Return {ticker: row} for all rows in transcript_intel."""
    resp = requests_mod.get(
        f"{url}/rest/v1/{TABLE}",
        headers=headers,
        params={"select": "ticker,earnings_date,harvested_at"},
        timeout=30,
    )
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    rows = resp.json() or []
    result: dict[str, dict] = {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        existing = result.get(t)
        if not existing or (r.get("harvested_at", "") > existing.get("harvested_at", "")):
            result[t] = r
    return result


def _is_fresh(row: dict | None, ttl_days: int) -> bool:
    if not row:
        return False
    raw = row.get("harvested_at")
    if not raw:
        return False
    try:
        ts  = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        age = _dt.datetime.now(_dt.timezone.utc) - ts.astimezone(_dt.timezone.utc)
        return age <= _dt.timedelta(days=ttl_days)
    except ValueError:
        return False


def _upsert(requests_mod, url: str, headers: dict, row: dict) -> None:
    resp = requests_mod.post(
        f"{url}/rest/v1/{TABLE}?on_conflict=ticker,earnings_date",
        headers=headers,
        data=json.dumps(row, default=str),
        timeout=30,
    )
    if resp.status_code >= 300:
        print(
            f"  [WARN] Supabase upsert failed for {row.get('ticker')}: "
            f"HTTP {resp.status_code} {resp.text[:200]}"
        )
    else:
        print(f"  [UPSERT] {row.get('ticker')} \u2192 {TABLE}")


# ---------------------------------------------------------------------------
# Earnings calendar helpers
# ---------------------------------------------------------------------------
def _get_post_earnings_tickers() -> list[dict]:
    """Return entries from earnings_calendar.json that are in the post-earnings window."""
    cal = read_json(EARNINGS_CALENDAR)
    results = []
    for entry in cal.get("post_earnings", []):
        days = entry.get("days_since", 9999)
        if days <= MAX_POST_EARNINGS_DAYS:
            results.append(entry)
    return results


# ---------------------------------------------------------------------------
# finance_earnings_transcript connector (PRIMARY source)
# ---------------------------------------------------------------------------
def _call_finance_tool(tool_name: str, args: dict, timeout: int = FINANCE_TIMEOUT) -> dict:
    """Call one Perplexity Finance tool through the external-tool CLI.

    Mirrors automation/sources/perplexity_finance_source.py::_call_finance so
    the connector is reached the same way everywhere. Raises RuntimeError on a
    non-zero exit so the caller can fall through to the next source.
    """
    import subprocess
    payload = json.dumps({
        "source_id": FINANCE_SOURCE_ID,
        "tool_name": tool_name,
        "arguments": args,
    })
    proc = subprocess.run(
        ["external-tool", "call", payload],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"external-tool {tool_name} exit {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return json.loads(proc.stdout)


def _finance_content_text(payload: dict) -> str | None:
    """Extract the markdown ``content`` string from a connector payload.

    Connectors return ``content`` as either a plain string or a list of
    ``{type, text}`` blocks; accept both (same as perplexity_finance_source).
    """
    content = payload.get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [
            blk.get("text", "")
            for blk in content
            if isinstance(blk, dict) and blk.get("text")
        ]
        joined = "\n".join(p for p in parts if p)
        return joined or None
    return None


def _fetch_finance_transcript(
    ticker: str, earnings_date: str
) -> tuple[str | None, str | None]:
    """PRIMARY transcript source: the platform finance_earnings_transcript tool.

    Returns ``(transcript_text, payload_earnings_date)``. The transcript is
    accepted by the caller only when ``len(text) >= MIN_TRANSCRIPT_CHARS`` AND
    the returned earnings_date matches the target — otherwise the caller treats
    it as empty/stale and falls through to Motley Fool. Any failure (no
    credential, CLI non-zero, unparseable payload) returns ``(None, None)`` so
    the chain degrades gracefully and never raises into the harvest loop.
    """
    try:
        payload = _call_finance_tool(
            FINANCE_TRANSCRIPT_TOOL,
            {"ticker_symbols": [ticker.upper()], "as_of": earnings_date, "limit": 1},
        )
    except Exception as exc:
        print(f"  [FINANCE] {ticker}: connector call failed: {exc} — falling back")
        return None, None

    text = _finance_content_text(payload)
    if not text:
        print(f"  [FINANCE] {ticker}: empty content — falling back")
        return None, None

    # The connector reports the transcript's earnings_date either at the top
    # level or in a meta block; accept either, default to the target so a
    # connector that omits it is not auto-rejected.
    payload_date = (
        payload.get("earnings_date")
        or (payload.get("meta") or {}).get("earnings_date")
        or None
    )
    print(f"  [FINANCE ✓] {ticker}: {len(text):,} chars "
          f"(date={payload_date or 'n/a'})")
    return text, payload_date


# ---------------------------------------------------------------------------
# Motley Fool scraper
# ---------------------------------------------------------------------------
def _fool_search_url(ticker: str) -> str:
    return FOOL_SEARCH_URL.format(ticker=ticker.upper())


def _scrape_motley_fool(ticker: str, company: str) -> tuple[str | None, str | None]:
    """
    Attempt to scrape the most-recent earnings call transcript from Motley Fool.

    Returns:
        (transcript_text, page_url) — both None if scraping fails.

    Strategy:
        1. Fetch the ticker search/landing page to find the most-recent transcript link.
        2. Fetch that transcript page and extract the full body text.
        3. Truncate to MAX_TRANSCRIPT_CHARS to keep prompt size reasonable.
    """
    MAX_TRANSCRIPT_CHARS = 18_000
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        print("  [WARN] requests or beautifulsoup4 not installed — skipping Fool scrape")
        return None, None

    search_url = _fool_search_url(ticker)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Step 1: Find the transcript article link
    try:
        resp = requests.get(search_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            print(f"  [FOOL 429] Rate limited on search for {ticker} — falling back")
            return None, None
        if resp.status_code != 200:
            print(f"  [FOOL {resp.status_code}] Non-200 on search for {ticker} — falling back")
            return None, None
    except Exception as exc:
        print(f"  [FOOL ERROR] Search request failed for {ticker}: {exc}")
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")

    transcript_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "earnings-call-transcript" in href:
            if href.startswith("/"):
                href = "https://www.fool.com" + href
            transcript_link = href
            break

    if not transcript_link:
        for a in soup.find_all("a", href=True):
            text = (a.get_text() or "").lower()
            if "transcript" in text and ticker.lower() in text:
                href = a["href"]
                if href.startswith("/"):
                    href = "https://www.fool.com" + href
                transcript_link = href
                break

    if not transcript_link:
        print(f"  [FOOL] No transcript link found for {ticker} on search page")
        return None, None

    # Polite crawl delay
    time.sleep(FOOL_DELAY_SECS)

    # Step 2: Fetch the transcript article
    try:
        resp2 = requests.get(transcript_link, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp2.status_code != 200:
            print(f"  [FOOL {resp2.status_code}] Non-200 fetching transcript for {ticker}")
            return None, None
    except Exception as exc:
        print(f"  [FOOL ERROR] Transcript fetch failed for {ticker}: {exc}")
        return None, None

    soup2 = BeautifulSoup(resp2.text, "html.parser")

    body_div = (
        soup2.find("div", class_="article-body")
        or soup2.find("div", attrs={"data-id": "article-body"})
        or soup2.find("article")
    )
    if not body_div:
        body_div = soup2.find("main") or soup2

    paragraphs = body_div.find_all("p") if body_div else []
    lines = [p.get_text(separator=" ").strip() for p in paragraphs if p.get_text().strip()]
    full_text = "\n\n".join(lines)

    if len(full_text) < 200:
        print(f"  [FOOL] Transcript body too short ({len(full_text)} chars) for {ticker} — falling back")
        return None, None

    if len(full_text) > MAX_TRANSCRIPT_CHARS:
        cut = full_text[:MAX_TRANSCRIPT_CHARS].rfind("\n\n")
        if cut > MAX_TRANSCRIPT_CHARS // 2:
            full_text = full_text[:cut] + "\n\n[...transcript truncated for analysis...]"
        else:
            full_text = full_text[:MAX_TRANSCRIPT_CHARS] + "\n\n[...transcript truncated for analysis...]"

    print(f"  [FOOL \u2713] Scraped {len(full_text):,} chars for {ticker} from {transcript_link}")
    return full_text, transcript_link


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def _build_scrape_distill_prompt(
    ticker: str, company: str, earnings_date: str, transcript_text: str
) -> str:
    """Prompt when we have a real transcript to distill."""
    return (
        f"Below is the earnings call transcript for {company} ({ticker}), "
        f"reported on {earnings_date}. Distill it into the following JSON structure.\n\n"
        f"TRANSCRIPT:\n{transcript_text}\n\n"
        f"Return ONLY this JSON object. No prose, no markdown, no preamble.\n\n"
        + _transcript_json_schema_str(ticker, company, earnings_date)
    )


def _build_perplexity_native_prompt(
    ticker: str, company: str, earnings_date: str
) -> str:
    """Fallback: ask Perplexity to research and synthesize without source text."""
    return (
        f"Research the most recent earnings call for {company} ({ticker}), "
        f"reported around {earnings_date}. "
        f"Find the transcript or detailed coverage from Motley Fool, Seeking Alpha, "
        f"Quartr, or any reliable financial media source. "
        f"Then distill the content into the following JSON structure.\n\n"
        f"Return ONLY this JSON object. No prose, no markdown, no preamble.\n\n"
        + _transcript_json_schema_str(ticker, company, earnings_date)
    )


def _transcript_json_schema_str(
    ticker: str, company: str, earnings_date: str
) -> str:
    """Shared JSON schema template embedded in both prompt variants."""
    return f"""{{
  "ticker": "{ticker}",
  "company_name": "{company}",
  "earnings_date": "{earnings_date}",
  "quarter": "<e.g. Q1 FY2026>",
  "beat_miss_summary": "<1-sentence: beat/miss and what drove it>",
  "management_tone": "<one of: bullish | cautious | neutral | mixed>",
  "mgmt_key_points": [
    "<key point 1 from prepared remarks — max 40 words>",
    "<key point 2>",
    "<key point 3>",
    "<key point 4 (optional)>",
    "<key point 5 (optional)>"
  ],
  "guidance_statements": [
    "<verbatim or near-verbatim guidance quote 1>",
    "<guidance quote 2 (optional)>",
    "<guidance quote 3 (optional)>"
  ],
  "qa_key_exchanges": [
    {{
      "analyst": "<analyst name and firm>",
      "question": "<1-sentence question summary>",
      "answer": "<1-2 sentence answer summary>"
    }}
  ],
  "tone_signals": [
    "<language pattern signaling tone — e.g. 'macro uncertainty cited 4x'>"
  ],
  "key_metrics_discussed": [
    "<metric: actual vs. est — e.g. 'Cloud revenue: $28.5B, beat est $27.9B'>",
    "<metric 2>",
    "<metric 3>"
  ],
  "notable_quotes": [
    {{
      "speaker": "<name and role>",
      "quote": "<verbatim quote, max 50 words>"
    }}
  ],
  "risk_factors_cited": [
    "<risk 1 mentioned by management>",
    "<risk 2 (optional)>"
  ]
}}"""


# ---------------------------------------------------------------------------
# Source chain: finance connector (PRIMARY) -> Motley Fool -> sonar-native
# ---------------------------------------------------------------------------
# Canonical source-chain order. Used for documentation and assertable in tests.
TRANSCRIPT_SOURCE_CHAIN = ("finance_earnings_transcript", "motley_fool", "perplexity_native")


def _acquire_transcript(
    ticker: str, company: str, earnings_date: str
) -> tuple[str | None, str, str | None]:
    """Run the transcript source chain in priority order.

    Returns ``(transcript_text, source, transcript_url)``.

      1. finance_earnings_transcript (PRIMARY) — accepted only when the text is
         >= MIN_TRANSCRIPT_CHARS AND the returned earnings_date matches target.
      2. Motley Fool scrape (SECONDARY) — fires when (1) is empty/stale.
      3. perplexity_native (LAST) — no source text; signalled by an empty text
         with source ``perplexity_native`` so the caller uses the research prompt.

    ``transcript_text`` is None only for the perplexity_native tail; ``source``
    always names which rung produced (or will produce) the content.
    """
    # Rung 1: platform finance connector (PRIMARY).
    fin_text, fin_date = _fetch_finance_transcript(ticker, earnings_date)
    if fin_text and len(fin_text) >= MIN_TRANSCRIPT_CHARS:
        if fin_date and fin_date != earnings_date:
            print(f"  [FINANCE] {ticker}: transcript date {fin_date} != target "
                  f"{earnings_date} — treating as stale, falling back")
        else:
            return fin_text, "finance_earnings_transcript", None
    elif fin_text:
        print(f"  [FINANCE] {ticker}: transcript too short "
              f"({len(fin_text)} < {MIN_TRANSCRIPT_CHARS}) — falling back")

    # Rung 2: Motley Fool scrape (SECONDARY).
    fool_text, fool_url = _scrape_motley_fool(ticker, company)
    if fool_text:
        return fool_text, "motley_fool", fool_url

    # Rung 3: sonar-native research prompt (LAST resort).
    return None, "perplexity_native", None


# ---------------------------------------------------------------------------
# Core per-ticker harvest
# ---------------------------------------------------------------------------
def harvest_ticker(
    ticker: str,
    company: str,
    earnings_date: str,
    dry_run: bool = False,
    requests_mod=None,
    url: str = "",
    headers: dict | None = None,
    mode: str = "call_reaction",
) -> dict[str, Any]:
    """Harvest transcript intel for one ticker. Returns a status dict.

    ``mode``:
      * ``print_reaction`` — the transcript step is SKIPPED entirely (no
        connector / scrape / sonar cost). Actuals + the short print note are the
        responsibility of print_reaction_sweep; this function is a no-op here
        and returns status ``print_skipped`` so callers don't double-spend.
      * ``call_reaction`` (default) — runs the full transcript chain below.
    """
    print(f"\n  [{ticker}] Starting transcript harvest (date={earnings_date}, mode={mode})")

    if mode == "print_reaction":
        print(f"  [{ticker}] mode=print_reaction — skipping transcript step (no sonar cost)")
        return {"ticker": ticker, "status": "print_skipped"}

    if dry_run:
        print(f"  [DRY RUN] Would harvest transcript for {ticker} ({earnings_date})")
        return {"ticker": ticker, "status": "dry_run"}

    # Source chain: finance connector -> Motley Fool -> sonar-native.
    transcript_text, source, transcript_url = _acquire_transcript(
        ticker, company, earnings_date
    )

    # Build prompt: distill a real transcript, else sonar-native research.
    if transcript_text:
        prompt = _build_scrape_distill_prompt(ticker, company, earnings_date, transcript_text)
        task   = "transcript_distill"
        print(f"  [{ticker}] Distilling transcript from {source}")
    else:
        prompt = _build_perplexity_native_prompt(ticker, company, earnings_date)
        task   = "transcript_research"
        print(f"  [{ticker}] No transcript text — using Perplexity-native research prompt")

    # Step 3: Call Perplexity (queued or API fallback)
    result = call_perplexity(
        ticker=ticker,
        task=task,
        prompt=prompt,
        system=SYSTEM_PROMPT,
        max_tokens=1800,
        extra_meta={
            "earnings_date":  earnings_date,
            "transcript_url": transcript_url or "",
            "source":         source,
            "supabase_table": TABLE,
        },
    )

    if result.get("queued"):
        print(f"  [{ticker}] Queued for Computer processing")
        return {"ticker": ticker, "status": "queued"}

    if result.get("skipped") or result.get("dry_run"):
        return {"ticker": ticker, "status": "skipped"}

    # Step 4: Normalize and upsert (API fallback path)
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    row = {**EMPTY_SCHEMA, **{
        "ticker":               ticker,
        "company_name":         result.get("company_name") or company,
        "earnings_date":        result.get("earnings_date") or earnings_date,
        "quarter":              result.get("quarter"),
        "transcript_source":    source,
        "transcript_url":       transcript_url or result.get("transcript_url"),
        "beat_miss_summary":    result.get("beat_miss_summary"),
        "management_tone":      result.get("management_tone"),
        "mgmt_key_points":      _coerce_list(result.get("mgmt_key_points")),
        "guidance_statements":  _coerce_list(result.get("guidance_statements")),
        "qa_key_exchanges":     _coerce_list(result.get("qa_key_exchanges")),
        "tone_signals":         _coerce_list(result.get("tone_signals")),
        "key_metrics_discussed": _coerce_list(result.get("key_metrics_discussed")),
        "notable_quotes":       _coerce_list(result.get("notable_quotes")),
        "risk_factors_cited":   _coerce_list(result.get("risk_factors_cited")),
        "harvested_at":         now_iso,
    }}

    if requests_mod and url and headers:
        _upsert(requests_mod, url, headers, row)

    return {"ticker": ticker, "status": "harvested", "source": source}


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------
def run(
    dry_run: bool = False,
    force: bool = False,
    ticker_filter: str | None = None,
    mode: str = "call_reaction",
) -> dict[str, Any]:
    """
    Harvest transcript intel for all post-earnings tickers.

    Args:
        dry_run:       List tickers without calling any APIs or touching Supabase.
        force:         Bypass freshness check and re-harvest all tickers.
        ticker_filter: If set, only process this one ticker (case-insensitive).
        mode:          ``call_reaction`` (full transcript chain, default) or
                       ``print_reaction`` (skip the transcript step entirely).

    Returns:
        Summary dict for logging / downstream callers.
    """
    entries = _get_post_earnings_tickers()
    names   = load_common_names()

    if ticker_filter:
        ticker_filter = ticker_filter.upper()
        entries = [e for e in entries if e.get("ticker", "").upper() == ticker_filter]
        if not entries:
            print(f"[transcript_harvest] Ticker {ticker_filter} not found in post-earnings window")
            return {"error": f"{ticker_filter} not in post_earnings window"}

    summary: dict[str, Any] = {
        "total":         len(entries),
        "harvested":     0,
        "queued":        0,
        "skipped_fresh": 0,
        "skipped_other": 0,
        "errors":        0,
        "dry_run":       dry_run,
    }

    print(
        f"[transcript_harvest] {len(entries)} post-earnings tickers "
        f"(ttl={TRANSCRIPT_TTL_DAYS}d, dry_run={dry_run}, force={force})"
    )

    existing: dict[str, dict] = {}
    requests_mod = url = req_headers = None
    if not dry_run:
        try:
            requests_mod, url, req_headers = _supabase_client()
            existing = _load_existing_rows(requests_mod, url, req_headers)
            print(f"[transcript_harvest] Loaded {len(existing)} existing rows from Supabase")
        except Exception as exc:
            print(f"[transcript_harvest] [WARN] Supabase pre-fetch failed: {exc}")

    for entry in entries:
        ticker        = entry.get("ticker", "")
        earnings_date = entry.get("date", "")
        company       = entry.get("company") or names.get(ticker, ticker)

        if not ticker or not earnings_date:
            summary["skipped_other"] += 1
            continue

        if not dry_run and not force:
            row = existing.get(ticker)
            if (
                row
                and row.get("earnings_date") == earnings_date
                and _is_fresh(row, TRANSCRIPT_TTL_DAYS)
            ):
                print(
                    f"  [SKIP fresh] {ticker} earnings={earnings_date} "
                    f"(harvested {row.get('harvested_at')})"
                )
                summary["skipped_fresh"] += 1
                continue

        try:
            result = harvest_ticker(
                ticker=ticker,
                company=company,
                earnings_date=earnings_date,
                dry_run=dry_run,
                requests_mod=requests_mod,
                url=url or "",
                headers=req_headers or {},
                mode=mode,
            )
            status = result.get("status", "unknown")
            if status == "harvested":
                summary["harvested"] += 1
            elif status == "queued":
                summary["queued"] += 1
            else:
                summary["skipped_other"] += 1
        except Exception as exc:
            summary["errors"] += 1
            print(f"  [ERROR] {ticker}: {exc}")

    print(
        f"[transcript_harvest] Done — "
        f"harvested={summary['harvested']} queued={summary['queued']} "
        f"skipped_fresh={summary['skipped_fresh']} errors={summary['errors']} "
        f"total={summary['total']}"
    )
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _coerce_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if v is None:
        return []
    return [v]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Harvest earnings call transcript intel for all post-earnings tickers."
    )
    p.add_argument("--dry-run", action="store_true",
                   help="List tickers without queuing tasks or touching Supabase.")
    p.add_argument("--force", action="store_true",
                   help="Bypass freshness check and re-harvest every ticker.")
    p.add_argument("--ticker", type=str, default=None,
                   help="Process only this ticker (e.g. --ticker MSFT).")
    p.add_argument("--mode", choices=("print_reaction", "call_reaction"),
                   default="call_reaction",
                   help="print_reaction skips the transcript step entirely; "
                        "call_reaction (default) runs the full transcript chain.")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(dry_run=args.dry_run, force=args.force, ticker_filter=args.ticker, mode=args.mode)
