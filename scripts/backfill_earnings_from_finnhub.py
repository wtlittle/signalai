#!/usr/bin/env python3
"""
Backfill the FULL Finnhub earnings triplet onto post_earnings tickers in
earnings_intel.json from the AUTHORITATIVE Finnhub ``/stock/earnings`` endpoint.

WHY THIS EXISTS (supersedes backfill_eps_surprise_from_finnhub.py)
  PR #28 wrote ONLY ``in_quarter_eps_surprise_pct`` and deliberately withheld the
  absolute EPS ("basis guard"). That produced a logical contradiction on the
  card: ``EPS n/a +23.2% beat`` (surprise rendered, actual missing). The guard
  was wrong: Finnhub ``/stock/earnings`` returns ``actual``, ``estimate`` and
  ``surprisePercent`` in ONE row on a SINGLE consistent basis, so there is no
  cross-basis ambiguity when all three are taken together. This script writes
  the whole triplet so the card can never show a surprise without its actual.

WHAT IT WRITES (per ticker, matched by (ticker, period) within ±75 days)
  results_vs_consensus.in_quarter_eps_actual        (e.g. "$3.88")
  results_vs_consensus.in_quarter_eps_estimate      (e.g. "$3.15")
  results_vs_consensus.in_quarter_eps_surprise_pct  (signed %, + = beat)
  results_vs_consensus.eps_consensus_source = "finnhub_stock_earnings"

REVENUE
  Finnhub free tier ``/stock/earnings`` does NOT return revenueActual /
  revenueEstimate (confirmed 0/126 in the audit). When a paid plan DOES return
  them this script will write the revenue triplet too (revenueActual /
  revenueEstimate / computed surprise). On free tier these stay note-derived,
  never fabricated.

NON-FABRICATION / SAFETY
  * A value is written ONLY from a Finnhub row whose period is within ±75 days
    of the ticker's note earnings_date (attribute to the right quarter).
  * A note-derived ``in_quarter_eps_actual`` is NEVER clobbered (note basis,
    typically the company's headline non-GAAP figure, wins). BUT: if the
    surprise % was sourced from Finnhub and the note never supplied an actual,
    we MUST write the Finnhub actual+estimate so the pair is consistent — this
    is the core fix.
  * If we write a Finnhub actual we ALSO (re)write the matching Finnhub
    estimate and surprise % from the SAME row, so the rendered triplet is
    internally consistent (no mixing a note actual with a Finnhub surprise).
  * No credential / network failure -> ticker skipped, never guessed.

Usage:
  python3 scripts/backfill_earnings_from_finnhub.py [--dry-run] [--ticker XYZ]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "earnings_intel.json"

sys.path.insert(0, str(ROOT))
from automation.shared.finnhub import get_earnings_surprises  # noqa: E402

# Quarters are ~91 days apart; 75 keeps us comfortably inside one quarter.
_MATCH_WINDOW_DAYS = 75
# Finnhub free tier is 60 req/min. Sleep between calls and retry once on a
# transient miss so a rate-limit blip never silently drops a ticker.
_CALL_SLEEP_S = 1.1
_RETRY_SLEEP_S = 2.0


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _match_quarter(rows: list[dict], earnings_date: str | None) -> dict | None:
    """Finnhub row whose period is closest to the note's earnings_date, within
    the match window. Falls back to the most-recent row only when no
    earnings_date is available."""
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


def _fmt_money(v) -> str | None:
    """Render a numeric EPS/revenue figure as a display string with one '$'.

    EPS-scale numbers (< 1000) render as plain dollars ("$3.88"); large
    revenue-scale numbers render with a B/M magnitude suffix.
    """
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    a = abs(n)
    sign = "-" if n < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    return f"{sign}${a:.2f}"


def _surprise_pct(actual, estimate) -> float | None:
    try:
        a, e = float(actual), float(estimate)
    except (TypeError, ValueError):
        return None
    if abs(e) < 1e-9:
        return None
    return round((a - e) / abs(e) * 100.0, 1)


def _fetch(tk: str) -> list[dict] | None:
    # FINNHUB_NO_THROTTLE lets the test suite (mocked network) skip the
    # rate-limit sleeps; production runs leave it unset.
    throttle = os.environ.get("FINNHUB_NO_THROTTLE") != "1"
    rows = get_earnings_surprises(tk)
    if rows is None:
        if throttle:
            time.sleep(_RETRY_SLEEP_S)
        rows = get_earnings_surprises(tk)
    if throttle:
        time.sleep(_CALL_SLEEP_S)
    return rows


def backfill(only: str | None, dry_run: bool):
    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})

    eps_written, rev_written, skipped_present, skipped_nodata = [], [], [], []

    for tk, rec in tickers.items():
        if only and tk != only:
            continue
        if rec.get("state") != "post_earnings":
            continue
        rvc = rec.get("results_vs_consensus") or {}

        # An EPS pair is already fully consistent (actual present) -> leave it.
        eps_actual_present = rvc.get("in_quarter_eps_actual") is not None
        rev_actual_present = rvc.get("in_quarter_rev_actual") is not None
        if eps_actual_present and rev_actual_present:
            skipped_present.append(tk)
            continue

        earnings_date = rec.get("last_earnings_date") or (
            rec.get("post_earnings_review") or {}
        ).get("earnings_date")
        rows = _fetch(tk)
        row = _match_quarter(rows or [], earnings_date)
        if not row:
            skipped_nodata.append(tk)
            continue

        changed = False

        # --- EPS triplet ---------------------------------------------------
        # Only fill when the note never supplied an actual (don't clobber the
        # company's headline non-GAAP basis when the note has it). When we DO
        # fill the actual, (re)write estimate + surprise from the SAME row so
        # the triplet is internally consistent.
        if not eps_actual_present:
            eps_actual = row.get("actual")
            eps_est = row.get("estimate")
            eps_actual_str = _fmt_money(eps_actual)
            if eps_actual_str is not None:
                rvc["in_quarter_eps_actual"] = eps_actual_str
                est_str = _fmt_money(eps_est)
                if est_str is not None:
                    rvc["in_quarter_eps_estimate"] = est_str
                surp = (
                    round(float(row["surprisePercent"]), 1)
                    if row.get("surprisePercent") is not None
                    else _surprise_pct(eps_actual, eps_est)
                )
                if surp is not None:
                    rvc["in_quarter_eps_surprise_pct"] = surp
                rvc["eps_consensus_source"] = "finnhub_stock_earnings"
                # Canonical per-field provenance sibling build_earnings_json reads.
                rvc["in_quarter_eps_actual_source"] = "finnhub"
                eps_written.append(tk)
                changed = True

        # --- Revenue triplet (paid-tier only; free tier returns neither) ---
        if not rev_actual_present and row.get("revenueActual") is not None:
            rev_actual_str = _fmt_money(row.get("revenueActual"))
            if rev_actual_str is not None:
                rvc["in_quarter_rev_actual"] = rev_actual_str
                rev_est_str = _fmt_money(row.get("revenueEstimate"))
                if rev_est_str is not None:
                    rvc["in_quarter_rev_estimate"] = rev_est_str
                rev_surp = _surprise_pct(
                    row.get("revenueActual"), row.get("revenueEstimate")
                )
                if rev_surp is not None:
                    rvc["in_quarter_rev_surprise_pct"] = rev_surp
                rvc["rev_consensus_source"] = "finnhub_stock_earnings"
                # Canonical per-field provenance sibling build_earnings_json reads.
                rvc["in_quarter_rev_actual_source"] = "finnhub"
                rev_written.append(tk)
                changed = True

        if changed:
            rec["results_vs_consensus"] = rvc
        else:
            skipped_nodata.append(tk)

    if (eps_written or rev_written) and not dry_run:
        INTEL_PATH.write_text(json.dumps(intel, indent=2))

    return eps_written, rev_written, skipped_present, skipped_nodata


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Limit to a single ticker")
    args = ap.parse_args()

    eps_w, rev_w, present, nodata = backfill(args.ticker, args.dry_run)

    tag = "[DRY RUN] " if args.dry_run else "[OK] "
    print(
        f"{tag}eps_triplet_written={len(eps_w)} rev_triplet_written={len(rev_w)} "
        f"already_complete={len(present)} no_finnhub_match={len(nodata)}"
    )
    if eps_w:
        print("  eps written:", ", ".join(sorted(eps_w)))
    if rev_w:
        print("  rev written:", ", ".join(sorted(rev_w)))


if __name__ == "__main__":
    main()
