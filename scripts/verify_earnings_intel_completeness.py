#!/usr/bin/env python3
"""
Fail-loud completeness guard for POST earnings cards.

Mirrors the "no fabricated literals" audit pattern from PR #25, but inverts it:
instead of asserting that no card shows a *made-up* value, it asserts that no
active POST card shows **n/a for a field whose value is demonstrably present in
the source note**. This catches regressions where the note→earnings_intel.json
extraction silently drops a number the card should be surfacing (root causes
RC-1/RC-2/RC-3 from the n/a audit).

WHAT IT CHECKS (per active post_earnings ticker in earnings_intel.json)
  For each ticker whose markdown note prose contains a parseable REV or EPS
  actual (using the SAME parser the backfill uses, so producer and guard agree):
    * the intel record must carry results_vs_consensus.in_quarter_rev_actual
      (resp. in_quarter_eps_actual) — otherwise it's a dropped-field regression.

WHAT IT DELIBERATELY DOES NOT FLAG (justified n/a — not regressions)
  * Preview notes with no actuals (results "not yet reported").
  * Surprise % / guidance Δ that have no consensus baseline stated in the note
    and no authoritative free source — fabricating these would violate the
    zero-tolerance mandate, so their n/a is correct.
  * EPS surprise sourced from Finnhub (network) — this guard is offline and
    only asserts note-derivable fields, so it never flakes on connectivity.

Exit code 0 = clean. Exit code 1 = at least one regression (details printed).

Usage:
  python3 scripts/verify_earnings_intel_completeness.py [--ticker XYZ]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "earnings_intel.json"
POST_DIR = ROOT / "notes" / "post_earnings"

sys.path.insert(0, str(ROOT))
from scripts.backfill_results_vs_consensus import _parse_metric  # noqa: E402

_QUAL = r"(?:Total\s+|Non-GAAP\s+|GAAP\s+|Adjusted\s+|Adj\.?\s+|Core\s+|Q\d\s+)*"
_REV_LABEL = _QUAL + r"Rev(?:enue)?"
_EPS_LABEL = _QUAL + r"EPS"


def _note_path_for(rec: dict) -> Path | None:
    meta = rec.get("source_metadata") or {}
    rel = meta.get("legacy_note_path")
    if rel:
        p = ROOT / rel
        if p.exists():
            return p
    # Fallback: derive from ticker + last_earnings_date.
    tk = rec.get("ticker")
    ed = rec.get("last_earnings_date")
    if tk and ed:
        p = POST_DIR / f"{tk}_{ed}.md"
        if p.exists():
            return p
    return None


def check(only: str | None) -> list[str]:
    """Return a list of regression messages (empty == clean)."""
    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})
    regressions: list[str] = []

    for tk, rec in tickers.items():
        if only and tk != only:
            continue
        if rec.get("state") != "post_earnings":
            continue
        review = rec.get("post_earnings_review") or {}
        if review.get("active") is not True:
            continue
        note_path = _note_path_for(rec)
        if not note_path:
            continue
        md = note_path.read_text()
        rvc = rec.get("results_vs_consensus") or {}

        rev_raw, _, _ = _parse_metric(md, _REV_LABEL)
        if rev_raw is not None and rvc.get("in_quarter_rev_actual") is None:
            regressions.append(
                f"{tk}: note prose has REV actual ({rev_raw!r}) but "
                f"results_vs_consensus.in_quarter_rev_actual is n/a"
            )

        eps_raw, _, _ = _parse_metric(md, _EPS_LABEL)
        if eps_raw is not None and rvc.get("in_quarter_eps_actual") is None:
            regressions.append(
                f"{tk}: note prose has EPS actual ({eps_raw!r}) but "
                f"results_vs_consensus.in_quarter_eps_actual is n/a"
            )

        # NO CONTRADICTORY STATES invariant (Issue D): a surprise % must never
        # exist without its matching absolute. The card renders "EPS <surprise>
        # beat" off the surprise %; without the actual it shows "EPS n/a +X%
        # beat" — a logical impossibility. Fail loud so this can't ship again.
        # This check is offline/source-independent (applies to EVERY active POST
        # card, not just note-derivable ones).
        if rvc.get("in_quarter_eps_surprise_pct") is not None and \
                rvc.get("in_quarter_eps_actual") is None:
            regressions.append(
                f"{tk}: in_quarter_eps_surprise_pct is "
                f"{rvc.get('in_quarter_eps_surprise_pct')!r} but "
                f"in_quarter_eps_actual is null (contradictory pair)"
            )
        if rvc.get("in_quarter_rev_surprise_pct") is not None and \
                rvc.get("in_quarter_rev_actual") is None:
            regressions.append(
                f"{tk}: in_quarter_rev_surprise_pct is "
                f"{rvc.get('in_quarter_rev_surprise_pct')!r} but "
                f"in_quarter_rev_actual is null (contradictory pair)"
            )

    return regressions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="Limit to a single ticker")
    args = ap.parse_args()

    regressions = check(args.ticker)
    if regressions:
        print(f"[FAIL] {len(regressions)} note-has-field-but-card-shows-n/a regression(s):")
        for r in regressions:
            print(f"  - {r}")
        return 1
    print("[OK] no note-derivable POST-card field is being dropped to n/a")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
