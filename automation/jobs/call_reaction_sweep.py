"""
Call-reaction sweep — escalate today's reporters to a transcript-backed note.

Runs at 9:00 PM ET for AMC reporters (cron B) and 12:00 PM ET for BMO reporters
(cron C). For each ticker matching the timing/date filter it invokes the updated
transcript_harvest with ``--mode call_reaction``:

  * If the transcript chain (finance connector -> Motley Fool -> sonar-native)
    succeeds, the ticker is escalated to note_status='call_reaction' with the
    rich note body, and the record is pushed to Supabase.
  * If all three transcript sources fail, the existing print_reaction note is
    LEFT IN PLACE. We log `transcript_unavailable` to the per-run log and do NOT
    downgrade — a print_reaction note is better than nothing.

Idempotency + no-downgrade: re-running never replaces an existing
call_reaction note with a weaker one. If transcript_harvest returns no usable
content on a retry, the prior call_reaction content stays.

Usage:
    python -m automation.jobs.call_reaction_sweep --timing AMC --date today
    python -m automation.jobs.call_reaction_sweep --timing BMO --date today
    python -m automation.jobs.call_reaction_sweep --ticker AVGO --date 2026-06-03
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import EARNINGS_INTEL, EARNINGS_CALENDAR, ROOT_DIR  # noqa: E402
from automation.shared.io_helpers import read_json, write_json                   # noqa: E402
from automation.shared.supabase_client import upsert_row, fetch_rows             # noqa: E402

SUPABASE_TABLE = "earnings_intel"
NOTE_SOURCE    = "call_reaction_sweep"
LOG_DIR        = ROOT_DIR / "cron_tracking" / "call_reaction_sweep"


def _et_date(offset_days: int = 0) -> str:
    try:
        from zoneinfo import ZoneInfo
        base = _dt.datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        base = _dt.datetime.utcnow().date()
    return (base - _dt.timedelta(days=offset_days)).isoformat()


def _resolve_date(arg: str) -> str:
    if not arg or arg == "today":
        return _et_date(0)
    if arg == "yesterday":
        return _et_date(1)
    return arg


def _log(line: str) -> None:
    """Append a line to today's per-run log (per cron-tracking convention)."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        (LOG_DIR / f"run_{_et_date(0)}.log").open("a").write(f"{stamp} {line}\n")
    except Exception:
        pass
    print(f"  {line}")


def _select_tickers(
    timing: Optional[str], date: str, ticker: Optional[str]
) -> list[dict[str, Any]]:
    cal = read_json(EARNINGS_CALENDAR)
    post = cal.get("post_earnings", []) or []
    if ticker:
        t = ticker.upper()
        hits = [e for e in post if e.get("ticker", "").upper() == t]
        if hits:
            return hits
        intel = read_json(EARNINGS_INTEL).get("tickers", {})
        rec = intel.get(t)
        if rec:
            return [{
                "ticker": t,
                "company": rec.get("company_name") or t,
                "date": date, "earnings_date": date,
                "timing": rec.get("timing") or timing,
            }]
        return []
    out = []
    for e in post:
        if e.get("date") != date and e.get("earnings_date") != date:
            continue
        if timing and (e.get("timing") or "").upper() != timing.upper():
            continue
        out.append(e)
    return out


def _fetch_transcript_row(ticker: str, earnings_date: str) -> Optional[dict[str, Any]]:
    """Read the freshest transcript_intel row for this ticker/date from Supabase."""
    rows = fetch_rows(
        "transcript_intel",
        params={"ticker": f"eq.{ticker}", "select": "*",
                "order": "harvested_at.desc", "limit": "1"},
    )
    if rows:
        row = rows[0]
        if row.get("earnings_date") in (earnings_date, None):
            return row
    return None


def _compose_call_note(tr: dict[str, Any]) -> str:
    """Compose the rich call-reaction note body from a transcript_intel row."""
    parts: list[str] = []
    if tr.get("beat_miss_summary"):
        parts.append(str(tr["beat_miss_summary"]).strip())
    tone = tr.get("management_tone")
    pts = tr.get("mgmt_key_points") or []
    if tone and pts:
        parts.append(f"Management tone {tone}: " + "; ".join(str(p) for p in pts[:2]) + ".")
    elif pts:
        parts.append("; ".join(str(p) for p in pts[:2]) + ".")
    guides = tr.get("guidance_statements") or []
    if guides:
        parts.append("Guidance: " + str(guides[0]).strip())
    return " ".join(p for p in parts if p).strip()


def _sweep_one(
    entry: dict[str, Any], date: str, intel: dict[str, Any], dry_run: bool
) -> dict[str, Any]:
    ticker = entry.get("ticker", "").upper()
    company = entry.get("company") or ticker
    timing = entry.get("timing")
    earnings_date = entry.get("date") or entry.get("earnings_date") or date

    tickers = intel.setdefault("tickers", {})
    existing = tickers.get(ticker, {})

    # Run the full transcript chain (finance -> Fool -> sonar) for this ticker.
    if not dry_run:
        try:
            from automation.jobs.transcript_harvest import run as run_transcripts
            run_transcripts(ticker_filter=ticker, mode="call_reaction", force=True)
        except Exception as exc:
            _log(f"{ticker} transcript_harvest error: {exc}")

    tr = None if dry_run else _fetch_transcript_row(ticker, earnings_date)
    if not tr:
        # No usable transcript — NEVER downgrade. Leave the prior note in place.
        prior = existing.get("note_status")
        _log(f"{ticker} transcript_unavailable (prior note_status={prior or 'none'}; "
             f"not downgraded)")
        return {"ticker": ticker, "status": "transcript_unavailable",
                "prior_note_status": prior}

    note = _compose_call_note(tr)
    if not note:
        _log(f"{ticker} transcript row had no usable content; not downgraded")
        return {"ticker": ticker, "status": "transcript_empty"}

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    record = dict(existing)
    record.update({
        "ticker": ticker,
        "company_name": existing.get("company_name") or company,
        "state": "post_earnings",
        "timing": timing or existing.get("timing"),
        "last_earnings_date": earnings_date,
        "note_status": "call_reaction",
        "reaction_note": note,
        "note_generated_at": now_iso,
        "note_source": NOTE_SOURCE,
        "transcript_source": tr.get("transcript_source"),
        "intel_updated_at": now_iso,
    })

    _log(f"{ticker} escalated to call_reaction (source={tr.get('transcript_source')})")

    if dry_run:
        return {"ticker": ticker, "status": "dry_run", "note": note}

    tickers[ticker] = record
    upsert_row(
        SUPABASE_TABLE,
        {
            "ticker": ticker,
            "company_name": record["company_name"],
            "last_earnings_date": earnings_date,
            "timing": record.get("timing"),
            "note_status": "call_reaction",
            "reaction_note": note,
            "note_generated_at": now_iso,
            "note_source": NOTE_SOURCE,
            "transcript_source": tr.get("transcript_source"),
        },
        on_conflict="ticker",
    )
    return {"ticker": ticker, "status": "call_reaction", "note": note}


def run(
    timing: Optional[str] = "AMC",
    date: str = "today",
    ticker: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    resolved_date = _resolve_date(date)
    entries = _select_tickers(timing if not ticker else None, resolved_date, ticker)

    print(f"[call_reaction_sweep] timing={timing} date={resolved_date} "
          f"ticker={ticker or '*'} -> {len(entries)} ticker(s)")
    if not entries:
        print("[call_reaction_sweep] No matching tickers.")
        return {"total": 0, "escalated": 0, "unavailable": 0}

    intel = read_json(EARNINGS_INTEL)
    results = []
    for e in entries:
        try:
            results.append(_sweep_one(e, resolved_date, intel, dry_run))
        except Exception as exc:
            _log(f"{e.get('ticker')} error: {exc}")
            results.append({"ticker": e.get("ticker"), "status": "error"})

    if not dry_run:
        intel["last_updated"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        write_json(EARNINGS_INTEL, intel)

    escalated = sum(1 for r in results if r.get("status") == "call_reaction")
    unavailable = sum(1 for r in results if r.get("status") == "transcript_unavailable")
    print(f"[call_reaction_sweep] Done — escalated={escalated} "
          f"unavailable={unavailable} total={len(entries)}")
    return {"total": len(entries), "escalated": escalated,
            "unavailable": unavailable, "results": results}


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Call-reaction sweep (transcript-backed).")
    p.add_argument("--timing", choices=("AMC", "BMO"), default="AMC")
    p.add_argument("--date", type=str, default="today",
                   help="today | yesterday | YYYY-MM-DD")
    p.add_argument("--ticker", type=str, default=None,
                   help="Backfill a single ticker (overrides --timing filter).")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(timing=args.timing, date=args.date, ticker=args.ticker, dry_run=args.dry_run)
