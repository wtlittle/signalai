"""
Market Intel Refresh — per-TICKER TAM / category-CAGR / competitive-landscape
harvester backed by Supabase ``market_intel_ticker`` (90-day TTL).

For one ticker (``--ticker NET``) or the full watchlist (``--all``) this job:

  1. Skips the ticker if its existing row's ``updated_at`` is younger than the
     90-day TTL (unless ``--force``).
  2. Issues a single Perplexity ``sonar`` call constrained to authoritative
     market-research and primary domains (Gartner / Forrester / IDC / Statista /
     G2 / SEC) PLUS the ticker's own investor-relations domain, asking for a
     strict-JSON object:

         {
           "tam_usd_bn": <number | null>,
           "tam_source_url": "<url | null>",
           "category_cagr_pct": <number | null>,
           "drivers": ["<3-5 market growth drivers>"],
           "competitors": [
             {"name": "...", "ticker": "...|null",
              "quadrant": "Leader|Challenger|Visionary|Niche",
              "threat": "Low|Med|High", "source_url": "<url|null>"}
           ]
         }

  3. Upserts the normalized row into ``market_intel_ticker`` keyed by ``ticker``.

DATA INTEGRITY: every numeric field is a number or null — never a guess. A value
the model cannot source comes back null and is stored as null (rendered as an
em-dash downstream), never the literal string "MISSING".

Usage:
    python -m automation.jobs.market_intel_refresh --ticker NET
    python -m automation.jobs.market_intel_refresh --all
    python -m automation.jobs.market_intel_refresh --ticker NET --force
    python -m automation.jobs.market_intel_refresh --all --dry-run
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.perplexity.client import call_perplexity  # noqa: E402
from automation.shared.supabase_client import fetch_rows, upsert_row  # noqa: E402
from automation.shared.tickers import load_active_universe, load_common_names  # noqa: E402

TABLE = "market_intel_ticker"
TTL_DAYS = 90

# Authoritative research + primary domains every call is constrained to. The
# ticker's own IR domain is appended per ticker (see _ir_domain).
_BASE_DOMAINS = [
    "gartner.com",
    "forrester.com",
    "idc.com",
    "statista.com",
    "g2.com",
    "sec.gov",
]

# Known investor-relations domains for tickers where the IR host is not trivially
# derivable from the company name. Extend as coverage grows; an unknown ticker
# falls back to a best-effort guess (see _ir_domain).
_IR_DOMAINS = {
    "NET": "cloudflare.net",
    "NVDA": "investor.nvidia.com",
    "RBRK": "ir.rubrik.com",
    "CRWD": "ir.crowdstrike.com",
    "ZS": "ir.zscaler.com",
    "DDOG": "investors.datadoghq.com",
    "SNOW": "investors.snowflake.com",
    "PANW": "investors.paloaltonetworks.com",
    "MDB": "investors.mongodb.com",
    "S": "investors.sentinelone.com",
}

SYSTEM_PROMPT = (
    "You are a senior buy-side technology equity analyst building a per-company "
    "market fact-base. Return ONLY a single valid JSON object with the requested "
    "keys — no markdown fences, no commentary. Every numeric field must be a "
    "number or null. Cite a primary or near-primary source URL for the TAM and "
    "for each competitor where possible (Gartner, Forrester, IDC, Statista, G2, "
    "SEC filings, or the company's investor-relations site). Never fabricate a "
    "figure: if you cannot source it, use null."
)


def _ir_domain(ticker: str) -> Optional[str]:
    """Best-effort investor-relations domain for ``ticker``.

    Returns a known mapping when present; otherwise None (the call still runs
    against the base research domains). We deliberately do NOT guess a domain we
    cannot stand behind — a wrong allowed_domain silently filters out good
    sources, which is worse than omitting it.
    """
    return _IR_DOMAINS.get(ticker.upper())


def _build_prompt(ticker: str, company: str) -> str:
    return (
        f"Research the primary addressable market for {company} ({ticker}). "
        f"Time horizon 2026-2030. Return JSON with EXACTLY these keys:\n"
        f'  "tam_usd_bn": <total addressable market in USD billions, or null>,\n'
        f'  "tam_source_url": "<url of the primary source for the TAM, or null>",\n'
        f'  "category_cagr_pct": <category CAGR as a percent number, e.g. 18.5, or null>,\n'
        f'  "drivers": ["<3 to 5 concise structural market growth drivers>"],\n'
        f'  "competitors": [\n'
        f'     {{"name": "<competitor>", "ticker": "<ticker or null>", '
        f'"quadrant": "<Leader|Challenger|Visionary|Niche>", '
        f'"threat": "<Low|Med|High>", "source_url": "<url or null>"}}\n'
        f"     // 4 to 7 competitors, Gartner-style quadrant labels\n"
        f"  ]\n"
        f"Use the most recent verifiable data. Return null for any numeric you "
        f"cannot source with confidence; still return the qualitative fields."
    )


def _coerce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().replace("%", "").replace("$", "").replace(",", "")
        if v == "":
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_VALID_QUADRANTS = {"leader": "Leader", "challenger": "Challenger",
                    "visionary": "Visionary", "niche": "Niche"}
_VALID_THREAT = {"low": "Low", "med": "Med", "medium": "Med", "high": "High"}


def _normalize_competitors(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name:
            continue
        quad = _VALID_QUADRANTS.get(str(c.get("quadrant", "")).strip().lower())
        threat = _VALID_THREAT.get(str(c.get("threat", "")).strip().lower())
        out.append({
            "name": str(name),
            "ticker": (str(c["ticker"]) if c.get("ticker") else None),
            "quadrant": quad,
            "threat": threat,
            "source_url": (str(c["source_url"]) if c.get("source_url") else None),
        })
    return out


def _normalize_drivers(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    return [str(d).strip() for d in raw if str(d).strip()]


def _is_fresh(row: dict | None) -> bool:
    if not row:
        return False
    raw = row.get("updated_at")
    if not raw:
        return False
    try:
        ts = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    age = _dt.datetime.now(_dt.timezone.utc) - ts.astimezone(_dt.timezone.utc)
    return age <= _dt.timedelta(days=TTL_DAYS)


def _existing_row(ticker: str) -> dict | None:
    rows = fetch_rows(TABLE, params={"ticker": f"eq.{ticker}", "limit": "1"})
    return rows[0] if rows else None


def refresh_one(ticker: str, company: str, *, force: bool = False,
                dry_run: bool = False) -> dict[str, Any]:
    """Harvest + upsert one ticker's market intel. Returns a status dict."""
    ticker = ticker.strip().upper()

    if not force:
        existing = _existing_row(ticker)
        if _is_fresh(existing):
            print(f"  [SKIP fresh] {ticker} (updated_at={existing.get('updated_at')})")
            return {"ticker": ticker, "status": "skipped_fresh"}

    domains = list(_BASE_DOMAINS)
    ir = _ir_domain(ticker)
    if ir:
        domains.append(ir)

    prompt = _build_prompt(ticker, company)

    if dry_run:
        print(f"  [DRY RUN] {ticker} ({company}) — domains={domains}")
        return {"ticker": ticker, "status": "dry_run"}

    print(f"  [HARVEST] {ticker} ({company}) — allowed_domains={domains}")
    result = call_perplexity(
        ticker=ticker,
        task="market_intel_harvest",
        prompt=prompt,
        system=SYSTEM_PROMPT,
        force=True,
        max_tokens=1500,
        temperature=0.1,
        extra_meta={
            "model": "sonar",
            "search_domain_filter": domains,
            "allowed_domains": domains,
            "supabase_table": TABLE,
            "ttl_days": TTL_DAYS,
        },
    )

    if not isinstance(result, dict):
        return {"ticker": ticker, "status": "no_result"}
    if result.get("queued"):
        return {"ticker": ticker, "status": "queued"}
    if result.get("skipped") or result.get("dry_run"):
        return {"ticker": ticker, "status": result.get("reason", "skipped")}
    if "raw" in result and len(result) == 1:
        print(f"  [WARN] {ticker}: model returned unparseable payload; not upserting")
        return {"ticker": ticker, "status": "unparseable"}

    row = {
        "ticker": ticker,
        "tam_usd_bn": _coerce_float(result.get("tam_usd_bn")),
        "tam_source_url": result.get("tam_source_url") or None,
        "category_cagr_pct": _coerce_float(result.get("category_cagr_pct")),
        "drivers": _normalize_drivers(result.get("drivers")),
        "competitors": _normalize_competitors(result.get("competitors")),
        "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }

    ok = upsert_row(TABLE, row, on_conflict="ticker")
    if ok:
        print(f"  [OK] {ticker}: tam={row['tam_usd_bn']} cagr={row['category_cagr_pct']} "
              f"drivers={len(row['drivers'])} competitors={len(row['competitors'])}")
        return {"ticker": ticker, "status": "upserted", "row": row}
    print(f"  [WARN] {ticker}: Supabase upsert failed (table missing? apply the migration)")
    return {"ticker": ticker, "status": "upsert_failed", "row": row}


def run(tickers: list[str], *, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    names = load_common_names()
    summary = {"total": len(tickers), "upserted": 0, "skipped_fresh": 0,
               "queued": 0, "errors": 0, "rows": []}
    print(f"[market_intel_refresh] {len(tickers)} ticker(s) "
          f"(ttl={TTL_DAYS}d, force={force}, dry_run={dry_run})")
    for t in tickers:
        company = names.get(t.upper(), t.upper())
        try:
            res = refresh_one(t, company, force=force, dry_run=dry_run)
        except Exception as exc:
            summary["errors"] += 1
            print(f"  [ERROR] {t}: {exc}")
            continue
        status = res.get("status")
        if status == "upserted":
            summary["upserted"] += 1
            summary["rows"].append(res.get("row"))
        elif status == "skipped_fresh":
            summary["skipped_fresh"] += 1
        elif status == "queued":
            summary["queued"] += 1
        elif status in ("upsert_failed", "unparseable", "no_result"):
            summary["errors"] += 1
    print(f"[market_intel_refresh] Done — upserted={summary['upserted']} "
          f"skipped_fresh={summary['skipped_fresh']} queued={summary['queued']} "
          f"errors={summary['errors']}")
    return summary


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Per-ticker TAM / competitor harvester.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticker", help="Harvest a single ticker.")
    g.add_argument("--all", action="store_true", help="Harvest the full active universe.")
    p.add_argument("--force", action="store_true", help="Bypass the 90-day TTL skip.")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be harvested without calling Perplexity or Supabase.")
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.all:
        tickers = load_active_universe()
    else:
        tickers = [args.ticker]
    run(tickers, force=args.force, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
