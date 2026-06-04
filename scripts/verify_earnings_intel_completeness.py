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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "earnings_intel.json"
POST_DIR = ROOT / "notes" / "post_earnings"
GAPS_PATH = ROOT / "cron_tracking" / "earnings_intel_gaps.json"

# Strict per-ticker schema (Guardrail B). Every post_earnings ticker must carry
# these results_vs_consensus fields; tickers reported >48h ago that are missing
# any of them fail the build so bad cards never ship silently.
REQUIRED_RVC_FIELDS = (
    "in_quarter_rev_actual",
    "in_quarter_eps_actual",
    "in_quarter_rev_surprise_pct",
    "in_quarter_eps_surprise_pct",
)
GUIDANCE_FIELDS = (
    "guidanceRevenueDeltaPct",
    "guidanceEpsDeltaPct",
    "guidanceEpsDeltaAbs",
    "guidanceOperatingProfitDeltaPct",
    "guidanceOperatingProfitDeltaAbs",
    "guidanceFcfDeltaPct",
    "guidanceFcfDeltaAbs",
    "guidanceProfitDeltaPct",
)
# A ticker reported more than this many hours ago is expected to be fully
# populated; missing required fields then escalate from a warning to a build
# failure. Below the threshold (data may still be settling) we only warn.
HARD_FAIL_HOURS = 48
GUIDANCE_EXPECTED_HOURS = 24


def _reported_date(rec: dict):
    """Parse the report date, preferring last_reported_date, then
    last_earnings_date. Returns a date or None (RC-DATE-FIELD-DRIFT: accept
    either field rather than false-flagging the 126 records that only carry
    last_earnings_date).
    """
    for key in ("last_reported_date", "last_earnings_date"):
        raw = rec.get(key)
        if raw:
            try:
                return datetime.fromisoformat(str(raw)[:10]).date()
            except ValueError:
                continue
    return None


def _hours_since_report(rec: dict):
    d = _reported_date(rec)
    if d is None:
        return None
    reported = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - reported).total_seconds() / 3600.0


def schema_check(only: str | None) -> tuple[list[str], list[str], list[dict]]:
    """Strict per-ticker schema audit over EVERY post_earnings ticker.

    Unlike check() this does NOT gate on post_earnings_review.active -- the
    newest reporters (PANW/GTLB/CRDO) often have active=null yet are exactly the
    cards most likely to be wrong (RC-ACTIVE-FALSE-SKIP). Returns
    (hard_failures, soft_warnings, gap_records). hard_failures fail the build;
    soft_warnings do not (data may still be settling within HARD_FAIL_HOURS).
    """
    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})
    hard: list[str] = []
    warn: list[str] = []
    gaps: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for tk, rec in tickers.items():
        if only and tk != only:
            continue
        if rec.get("state") != "post_earnings":
            continue

        hours = _hours_since_report(rec)
        rvc = rec.get("results_vs_consensus") or {}

        # Core required fields (REV/EPS actuals + surprises + a parseable date).
        # These are the values the card renders; their absence is the bug the
        # user keeps hitting, so missing them >48h after the print is a HARD
        # failure that turns the workflow red.
        required_missing = [f for f in REQUIRED_RVC_FIELDS if rvc.get(f) is None]
        if _reported_date(rec) is None:
            required_missing.append("last_reported_date|last_earnings_date (unparseable)")

        # Guidance is tracked but SOFT: many historically-synced tickers never
        # had guidance backfilled, and fabricating it would violate the mandate.
        # We log + warn so coverage is visible, but a missing guidance pill must
        # not permanently red the build and drown out the actionable REV/EPS gaps.
        guidance_missing = []
        if hours is not None and hours >= GUIDANCE_EXPECTED_HOURS:
            if not any(rec.get(g) is not None for g in GUIDANCE_FIELDS):
                guidance_missing.append("guidance (>=1 of guidance* fields)")

        if not required_missing and not guidance_missing:
            continue

        # Hard failure only when a CORE field is missing and the print is >48h old.
        is_hard = bool(required_missing) and hours is not None and hours >= HARD_FAIL_HOURS
        all_missing = required_missing + guidance_missing
        gaps.append({
            "timestamp": now_iso,
            "ticker": tk,
            "missing_fields": all_missing,
            "missing_required": required_missing,
            "missing_guidance": guidance_missing,
            "hours_since_report": round(hours, 1) if hours is not None else None,
            "severity": "fail" if is_hard else "warn",
        })
        line = (
            f"{tk}: missing {', '.join(all_missing)} "
            f"(reported {'%.0fh' % hours if hours is not None else '?'} ago)"
        )
        (hard if is_hard else warn).append(line)

    return hard, warn, gaps


def _append_gaps(gaps: list[dict]) -> None:
    """Append this run's gap records to cron_tracking/earnings_intel_gaps.json.

    The file is a flat append-only list so a human (or a follow-up job) can see
    which fields have been missing over time. Never raises -- a logging failure
    must not mask the underlying schema failure.
    """
    if not gaps:
        return
    try:
        GAPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if GAPS_PATH.exists():
            try:
                existing = json.loads(GAPS_PATH.read_text())
                if not isinstance(existing, list):
                    existing = []
            except ValueError:
                existing = []
        existing.extend(gaps)
        GAPS_PATH.write_text(json.dumps(existing, indent=2) + "\n")
    except OSError as e:
        print(f"[WARN] could not write {GAPS_PATH}: {e}", file=sys.stderr)

sys.path.insert(0, str(ROOT))
from scripts.backfill_results_vs_consensus import _parse_metric  # noqa: E402

_QUAL = r"(?:Total\s+|Non-GAAP\s+|GAAP\s+|Adjusted\s+|Adj\.?\s+|Core\s+|Q\d\s+)*"
_REV_LABEL = _QUAL + r"Rev(?:enue)?"
_EPS_LABEL = _QUAL + r"EPS"


def revenue_pill_complete(rec: dict) -> bool:
    """A revenue guidance pill is complete when a revenue delta is present, OR
    a prior-source is recorded alongside a delta (either vs-prior-guide or the
    vs-Street magnitude). Mirrors the card's render gate so producer and guard
    agree on what "the pill renders" means.
    """
    if rec.get("guidanceRevenueDeltaPct") is not None:
        return True
    return bool(rec.get("guidanceRevenuePriorSource")) and (
        rec.get("guidanceRevenueDeltaPct") is not None
        or rec.get("guidanceRevenueStreetDeltaPct") is not None
    )


def profit_pill_complete(rec: dict) -> bool:
    """A profitability guidance pill is complete when ANY of the EPS / operating
    profit / FCF deltas is non-null (the card cascades EPS > OpInc > FCF).
    """
    return any(
        rec.get(k) is not None
        for k in (
            "guidanceEpsDeltaPct",
            "guidanceEpsDeltaAbs",
            "guidanceOperatingProfitDeltaPct",
            "guidanceOperatingProfitDeltaAbs",
            "guidanceFcfDeltaPct",
            "guidanceFcfDeltaAbs",
        )
    )


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


def guidance_pill_coverage(only: str | None) -> tuple[int, int, int]:
    """Return (n_active_post, n_revenue_pill_complete, n_profit_pill_complete).

    Informational only — does NOT fail the build. The spec tracks how many POST
    cards render each guidance pill so the backfill's effect is observable.
    """
    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})
    total = rev_ok = prof_ok = 0
    for tk, rec in tickers.items():
        if only and tk != only:
            continue
        if rec.get("state") != "post_earnings":
            continue
        if (rec.get("post_earnings_review") or {}).get("active") is not True:
            continue
        total += 1
        if revenue_pill_complete(rec):
            rev_ok += 1
        if profit_pill_complete(rec):
            prof_ok += 1
    return total, rev_ok, prof_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="Limit to a single ticker")
    args = ap.parse_args()

    total, rev_ok, prof_ok = guidance_pill_coverage(args.ticker)
    print(
        f"[INFO] guidance pill coverage: revenue {rev_ok}/{total}, "
        f"profit {prof_ok}/{total} active POST cards"
    )

    # Guardrail B: strict per-ticker schema gate over every POST ticker.
    hard, warn, gaps = schema_check(args.ticker)
    _append_gaps(gaps)
    for w in warn:
        print(f"[WARN] schema gap (still settling, <48h): {w}", file=sys.stderr)
    for h in hard:
        print(f"[FAIL] schema gap (>48h, should be complete): {h}", file=sys.stderr)
    if gaps:
        print(
            f"[INFO] logged {len(gaps)} schema gap record(s) to "
            f"{GAPS_PATH.relative_to(ROOT)}"
        )

    regressions = check(args.ticker)
    if regressions:
        print(f"[FAIL] {len(regressions)} note-has-field-but-card-shows-n/a regression(s):")
        for r in regressions:
            print(f"  - {r}")

    exit_code = 0
    if regressions:
        exit_code = 1
    if hard:
        print(
            f"[FAIL] {len(hard)} POST ticker(s) reported >48h ago are missing "
            f"required schema fields -- failing the build.",
            file=sys.stderr,
        )
        exit_code = 1

    if exit_code == 0:
        print("[OK] no note-derivable POST-card field is being dropped to n/a")
        print("[OK] all POST tickers reported >48h ago satisfy the required schema")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
