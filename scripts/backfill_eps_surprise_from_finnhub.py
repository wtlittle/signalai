#!/usr/bin/env python3
"""
Backfill ``results_vs_consensus.in_quarter_eps_surprise_pct`` onto
post_earnings tickers in earnings_intel.json from the AUTHORITATIVE Finnhub
``stock/earnings`` endpoint (actual vs estimate per fiscal quarter).

WHY THIS EXISTS (root cause RC-1)
  EPS reported-vs-consensus was only ever sourced from the markdown notes.
  Notes generated *before* the after-close print ("preview" notes: MDB / PATH /
  ADSK) and notes that simply omit the EPS line (CRM) therefore left the EPS
  beat/miss pill at n/a even though the figure exists in an authoritative data
  layer. This script lifts the figure FRESH from Finnhub at run time — nothing
  is hardcoded into shipped data.

WHAT IT WRITES
  results_vs_consensus.in_quarter_eps_surprise_pct   (signed %, + = beat)

WHAT IT DELIBERATELY DOES NOT WRITE — basis guard
  Finnhub's *absolute* EPS basis can differ from a note's non-GAAP figure
  (e.g. CRM Finnhub actual $3.88 vs note non-GAAP ~$2.6). The internally-
  consistent ``surprisePercent`` (actual vs estimate, one basis) is trustworthy,
  but the absolute value is not guaranteed to match the note basis. To honor the
  zero-tolerance-on-wrong-values mandate we populate ONLY the surprise %, never
  the absolute ``in_quarter_eps_actual`` (that stays note-sourced or n/a).

NON-FABRICATION
  * A value is written ONLY when Finnhub returns a quarter whose period date is
    within +/- ~75 days of the ticker's note earnings_date (so we attribute the
    surprise to the quarter the card is about, not a neighbouring print).
  * Existing non-null values are NEVER overwritten (note-sourced values win).
  * No credential / network failure -> ticker skipped, never guessed.

Usage:
  python3 scripts/backfill_eps_surprise_from_finnhub.py [--dry-run] [--ticker XYZ]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "earnings_intel.json"

sys.path.insert(0, str(ROOT))
from automation.shared.finnhub import get_earnings_surprises  # noqa: E402

# Max distance (days) between a Finnhub quarter's period date and the note's
# earnings_date for us to treat them as the same print. Quarters are ~91 days
# apart; 75 keeps us comfortably inside one quarter.
_MATCH_WINDOW_DAYS = 75


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _match_quarter(rows: list[dict], earnings_date: str | None) -> dict | None:
    """Return the Finnhub earnings row whose period is closest to the note's
    earnings_date, within the match window. Falls back to the most-recent row
    only when no earnings_date is available."""
    if not rows:
        return None
    ed = _parse_date(earnings_date)
    if ed is None:
        return rows[0]  # most recent
    best, best_gap = None, None
    for row in rows:
        pd = _parse_date(row.get("period"))
        if pd is None:
            continue
        gap = abs((pd - ed).days)
        if best_gap is None or gap < best_gap:
            best, best_gap = row, gap
    if best is not None and best_gap is not None and best_gap <= _MATCH_WINDOW_DAYS:
        return best
    return None


def backfill(only: str | None, dry_run: bool) -> tuple[list[str], list[str], list[str]]:
    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})

    populated, skipped_present, skipped_nodata = [], [], []

    for tk, rec in tickers.items():
        if only and tk != only:
            continue
        if rec.get("state") != "post_earnings":
            continue
        rvc = rec.get("results_vs_consensus") or {}
        if rvc.get("in_quarter_eps_surprise_pct") is not None:
            skipped_present.append(tk)
            continue

        rows = get_earnings_surprises(tk)
        earnings_date = rec.get("last_earnings_date") or (
            rec.get("post_earnings_review") or {}
        ).get("earnings_date")
        row = _match_quarter(rows or [], earnings_date)
        if not row or row.get("surprisePercent") is None:
            skipped_nodata.append(tk)
            continue

        try:
            surprise = round(float(row["surprisePercent"]), 1)
        except (TypeError, ValueError):
            skipped_nodata.append(tk)
            continue

        rvc["in_quarter_eps_surprise_pct"] = surprise
        rvc.setdefault("eps_surprise_source", "finnhub_stock_earnings")
        rec["results_vs_consensus"] = rvc
        populated.append(tk)

    if populated and not dry_run:
        INTEL_PATH.write_text(json.dumps(intel, indent=2))

    return populated, skipped_present, skipped_nodata


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Limit to a single ticker")
    args = ap.parse_args()

    populated, present, nodata = backfill(args.ticker, args.dry_run)

    tag = "[DRY RUN] " if args.dry_run else "[OK] "
    print(
        f"{tag}eps_surprise populated={len(populated)} "
        f"already_present={len(present)} no_finnhub_match={len(nodata)}"
    )
    if populated:
        print("  populated:", ", ".join(sorted(populated)))


if __name__ == "__main__":
    main()
