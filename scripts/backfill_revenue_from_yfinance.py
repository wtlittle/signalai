#!/usr/bin/env python3
"""
[DEPRECATED] Subsumed by the per-ticker pipeline (automation/pipeline +
automation/sources/yfinance_source.py). Dormant under the new architecture; kept
for one cycle for manual debugging. Removal scheduled in a follow-up PR after
7 days of clean new-pipeline runs.

Yfinance fallback for revenue ACTUAL on POST cards where Finnhub had no
revenue data. Reads ``Ticker.quarterly_income_stmt`` -> ``Total Revenue`` row
and matches the reporting date to ``last_earnings_date`` within a window.

WHY THIS EXISTS
  ``backfill_earnings_from_finnhub.py`` writes the EPS+REV triplet but
  Finnhub's revenue coverage is uneven. yfinance's ``earnings_history``
  endpoint is EPS-only. ``quarterly_income_stmt`` reliably exposes reported
  revenue for the just-closed quarter.

WHAT IT WRITES
  Into ``earnings_intel.json``'s ``tickers[T].results_vs_consensus``:
    in_quarter_rev_actual    ($XX.XXB string, only if missing)
    rev_actual_source        = "yfinance_quarterly_income_stmt"

NON-FABRICATION
  * Never overwrites an existing non-null value.
  * Matches by date within ±20 days (FY-Q1 reporting lag can run >10 days).
  * Skips if quarterly_income_stmt missing or row not found.

Usage:
  python3 scripts/backfill_revenue_from_yfinance.py [--dry-run] [--ticker XYZ]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
INTEL = ROOT / "earnings_intel.json"
MATCH_WINDOW_DAYS = 45  # quarter-end to print can be ~30-45 days


def _money_str(v: float) -> str:
    if v is None:
        return None
    av = abs(v)
    if av >= 1e9:
        return f"${v/1e9:.2f}B"
    if av >= 1e6:
        return f"${v/1e6:.2f}M"
    if av >= 1e3:
        return f"${v/1e3:.2f}K"
    return f"${v:.2f}"


def _parse_date(s: str):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Limit to one ticker")
    args = ap.parse_args()

    data = json.loads(INTEL.read_text())
    tickers = data.get("tickers", {})

    written = 0
    skipped_complete = 0
    no_match = 0
    errors = 0

    candidates = []
    for t, v in tickers.items():
        if args.ticker and t != args.ticker:
            continue
        if v.get("state") != "post_earnings":
            continue
        rvc = v.setdefault("results_vs_consensus", {}) if not args.dry_run else (v.get("results_vs_consensus") or {})
        if rvc.get("in_quarter_rev_actual"):
            skipped_complete += 1
            continue
        candidates.append((t, v, rvc))

    print(f"[INFO] candidates needing rev_actual: {len(candidates)}")

    for t, v, rvc in candidates:
        last_date = _parse_date(v.get("last_earnings_date") or "")
        if not last_date:
            print(f"  [SKIP] {t}: no last_earnings_date")
            no_match += 1
            continue
        try:
            qs = yf.Ticker(t).quarterly_income_stmt
            if qs is None or qs.empty or "Total Revenue" not in qs.index:
                no_match += 1
                continue
        except Exception as e:
            print(f"  [ERR] {t}: yfinance failed: {e}")
            errors += 1
            continue

        rev_actual = None
        best_diff = None
        for col in qs.columns:
            try:
                rd = col.date() if hasattr(col, "date") else _parse_date(str(col))
            except Exception:
                rd = _parse_date(str(col))
            if not rd:
                continue
            # quarter-end date in yfinance is BEFORE the print date; print = quarter-end + ~30 days
            diff = (last_date - rd).days
            if 0 <= diff <= MATCH_WINDOW_DAYS:
                try:
                    val = float(qs.loc["Total Revenue", col])
                    if val and val == val:  # not NaN
                        if best_diff is None or diff < best_diff:
                            rev_actual = val
                            best_diff = diff
                except Exception:
                    pass

        if rev_actual is None:
            no_match += 1
            print(f"  [NOMATCH] {t}: no Total Revenue row within {MATCH_WINDOW_DAYS}d of {last_date}")
            continue

        rvc["in_quarter_rev_actual"] = _money_str(rev_actual)
        rvc["rev_actual_source"] = "yfinance_quarterly_income_stmt"
        # Canonical per-field provenance sibling build_earnings_json reads.
        rvc["in_quarter_rev_actual_source"] = "yfinance"
        written += 1
        print(f"  [WRITE] {t}: rev_actual={_money_str(rev_actual)} (Δ{best_diff}d)")

    if not args.dry_run:
        INTEL.write_text(json.dumps(data, indent=2) + "\n")

    print(f"[OK] rev_actual_written={written} already_complete={skipped_complete} no_match={no_match} errors={errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
