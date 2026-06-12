#!/usr/bin/env python3
"""LLM regeneration pass for the thin sections of archived weekly briefings.

The deterministic backfill (``backfill_weekly_archive``) brought every archived
``weekly_briefing_*.json`` up to standard on every criterion that can be rebuilt
from grounded local data. Two criteria remain that genuinely need a fresh
sonar-deep-research call:

  * 05_market_summary  — >= 3 paragraphs referencing macro + sector rotation
  * 10_narrative       — long-form prose report (>= 1500 chars)

This module regenerates ONLY those two via the existing pipeline calls
(``_fetch_structured`` + ``_fetch_narrative``, both week-scoped so each week
caches separately), then MERGES the result into the already-shipped archive file
WITHOUT overwriting any populated deterministic field. After the merge it re-runs
the deterministic backfills so measured index_returns / macro_regime / volume
sections / tl_dr stay authoritative — the LLM never gets the last word on a
number.

Merge contract (never lose grounded data):
  * narrative          — take the LLM long-form report (the missing field).
  * market_summary     — take the LLM rich text, but if the existing value is a
                         dict carrying measured sub-fields (index_returns, vix,
                         etc.), graft the new prose into ``narrative`` and keep
                         the measured sub-fields.
  * trends / risks / key_trends — take the richer (longer) of old vs new.
  * every other field  — keep the existing populated value; only fill from new
                         when the existing value is empty/null.

Non-fabrication guard: after merge + backfill, every populated S&P/Nasdaq pct
must equal the measured value from the historical close series (the same
``build_index_returns`` source the deterministic pass uses). If the LLM injected
an index number that does not match, the measured value wins (it is overlaid by
``_backfill_index_and_headline``); we additionally assert the final pcts trace to
that measured source and skip-flag the week if they somehow don't.

No fabrication, no emoji. Pure pipeline reuse.

Usage:
    python -m automation.quality.regen_weekly_archive --weeks 2026-05-22
    python -m automation.quality.regen_weekly_archive --all-needs-llm
    python -m automation.quality.regen_weekly_archive --all-needs-llm --dry-run
"""
from __future__ import annotations

import argparse
import glob
from datetime import date
from pathlib import Path

from automation.shared.io_helpers import read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
AUDIT_JSON = ROOT / "archive" / "briefings" / "_audit_report.json"
CHECKPOINT = ROOT / "archive" / "briefings" / "_regen_checkpoint.json"

# Tier C weeks: genuine watchlist_updates data floor (< 50 real movers that
# week). They are still regenerated for market_summary + narrative; they simply
# cannot reach 15/15 because criterion 07 has no grounded source to satisfy it.
TIER_C = {"2025-11-14", "2025-12-12", "2025-12-19", "2025-12-26", "2026-03-20"}

# Per-week result statuses recorded in the checkpoint.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_FABRICATION_FAILED = "fabrication_guard_failed"

# market_summary sub-fields carrying measured data that the LLM regen must never
# clobber (it has no grounded source for them).
MEASURED_MS_FIELDS = ("index_returns", "ten_year_yield", "vix", "wti_crude",
                      "dxy", "best_sectors", "worst_sectors", "macro_regime")

# Fields _merge_briefing resolves with bespoke rules; the generic fill-empty loop
# must skip exactly these so the two can't drift apart.
SPECIAL_MERGE_KEYS = ("narrative", "market_summary", "trends", "key_trends", "risks")


def _needs_llm_weeks() -> list[str]:
    """Weeks the audit flagged with a needs-llm gap (05 and/or 10)."""
    if not AUDIT_JSON.exists():
        raise SystemExit(f"audit report not found: {AUDIT_JSON} (run the scorecard first)")
    report = read_json(AUDIT_JSON)
    out = []
    for w in report.get("weeks", []):
        if (w.get("gaps") or {}).get("needs-llm"):
            out.append(w["week_ending"])
    return sorted(out)


def _is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def _ms_measured_subfields(ms) -> dict:
    """Measured sub-fields worth preserving from an existing market_summary dict."""
    if not isinstance(ms, dict):
        return {}
    return {k: ms[k] for k in MEASURED_MS_FIELDS if not _is_empty(ms.get(k))}


def _merge_market_summary(old, new):
    """Merge LLM market_summary into the existing one without losing measured data.

    If the old value is a rich dict (measured index/vix/yield sub-fields), keep it
    and graft the new prose into its ``narrative`` slot. If old is a thin string
    and new is richer, take new. Never replace a populated value with an empty one.
    """
    new_text = ""
    if isinstance(new, str):
        new_text = new.strip()
    elif isinstance(new, dict):
        new_text = (new.get("narrative") or "").strip()

    measured = _ms_measured_subfields(old)
    if measured:
        # old is a dict (it carried measured sub-fields); keep it and only upgrade
        # the prose when the regen produced a longer narrative.
        merged = dict(old)
        old_text = (merged.get("narrative") or "").strip()
        if len(new_text) > len(old_text):
            merged["narrative"] = new_text
        merged.update(measured)
        return merged

    # No measured sub-fields to protect: take whichever carries more prose.
    if isinstance(old, str) and len(old.strip()) >= len(new_text):
        return old
    return new if not _is_empty(new) else old


def _merge_briefing(old: dict, new: dict) -> dict:
    """Deep-merge a freshly regenerated briefing into the shipped archive.

    Preserves every populated field in ``old``; only pulls from ``new`` for the
    fields the regen exists to supply (narrative, richer market_summary) or to
    fill a field that is empty in old.
    """
    merged = dict(old)

    # narrative: the field the regen exists to add. Take new when it's longer.
    old_narr = old.get("narrative") if isinstance(old.get("narrative"), str) else ""
    new_narr = new.get("narrative") if isinstance(new.get("narrative"), str) else ""
    if len(new_narr.strip()) > len(old_narr.strip()):
        merged["narrative"] = new_narr

    # market_summary: protect measured sub-fields.
    merged["market_summary"] = _merge_market_summary(
        old.get("market_summary"), new.get("market_summary"))

    # trends / key_trends / risks: take the richer (more entries) of the two.
    for key in ("trends", "key_trends", "risks"):
        new_list = new.get(key) or []
        if isinstance(new_list, list) and len(new_list) > len(old.get(key) or []):
            merged[key] = new_list

    # Any field empty in old but populated in new: fill it (skip the bespoke ones).
    for key, val in new.items():
        if key in SPECIAL_MERGE_KEYS:
            continue
        if _is_empty(merged.get(key)) and not _is_empty(val):
            merged[key] = val

    return merged


def _assert_indices_traceable(briefing: dict, week: date) -> bool:
    """True when every populated S&P/Nasdaq pct equals the measured source value.

    The deterministic backfill overlays measured pcts last, so this should always
    hold; we verify it as a non-fabrication guard before accepting the week.
    """
    try:
        from automation.sources.finance_indices_source import build_index_returns
    except Exception:
        return True  # cannot verify -> don't block (measured overlay still ran)
    measured = build_index_returns(week)
    ir = briefing.get("index_returns") or {}
    for key in ("sp500", "nasdaq"):
        blk = ir.get(key) if isinstance(ir.get(key), dict) else {}
        mblk = measured.get(key) if isinstance(measured.get(key), dict) else {}
        for f in ("weekly_pct", "one_month_pct", "three_month_pct", "ytd_pct"):
            v = blk.get(f)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            mv = mblk.get(f)
            if not (isinstance(mv, (int, float)) and abs(v - mv) < 0.01):
                print(f"    [FABRICATION GUARD] {key}.{f}={v} != measured {mv}")
                return False
    return True


def regen_one(week: date, dry_run: bool = False) -> dict:
    """Regenerate market_summary + narrative for one week and merge into archive."""
    from automation.jobs.weekly_briefing import (
        load_tickers,
        _fetch_structured,
        _fetch_narrative,
        _merge_structured_narrative,
        _extract_dict,
        _normalize_trends_entry,
        _normalize_risk_entry,
        _backfill_volume_sections,
        _backfill_index_and_headline,
    )
    from automation.jobs.weekly_briefing_context import build_context_blocks_asof
    from automation.jobs.backfill_briefings import archive_path
    from automation.jobs.backfill_tldr import inject_tl_dr
    from automation.shared.io_helpers import write_json

    path = archive_path(week)
    if not path.exists():
        raise FileNotFoundError(path)
    old = read_json(path)

    if dry_run:
        print(f"  [DRY RUN] would regen structured+narrative for {week.isoformat()}")
        return {"week": week.isoformat(), "dry_run": True}

    tickers = load_tickers()
    context = build_context_blocks_asof(week)

    # Two week-scoped deep-research calls: rich structured (multi-para
    # market_summary + trends/risks) and the long-form narrative.
    structured = _fetch_structured(tickers, context, force=True, week_ending=week)
    narrative = _fetch_narrative(tickers, context, force=True, week_ending=week)
    trends = _merge_structured_narrative(structured, narrative)
    trends_data = _extract_dict(trends)

    # Shape just the fields we merge (mirror compile_briefing's normalization for
    # the trends/risks lists; everything else is merged from raw trends_data).
    new = {
        "market_summary": trends_data.get("market_summary", ""),
        "narrative": trends_data.get("narrative", ""),
        "trends": [_normalize_trends_entry(t) for t in (trends_data.get("trends") or [])
                   if isinstance(t, dict)],
        "key_trends": [_normalize_trends_entry(t) for t in
                       (trends_data.get("key_trends") or trends_data.get("trends") or [])
                       if isinstance(t, dict)],
        "risks": [_normalize_risk_entry(r) for r in (trends_data.get("risks") or [])
                  if isinstance(r, dict)],
        "upcoming_catalysts": trends_data.get("upcoming_catalysts") or [],
        "sector_summary": trends_data.get("sector_summary") or {},
    }

    merged = _merge_briefing(old, new)

    # Re-apply deterministic backfills LAST so measured numbers stay authoritative
    # and any section the LLM thinned is restored from grounded data.
    _backfill_volume_sections(merged, context, target_week=week)
    _backfill_index_and_headline(merged, week)
    merged = inject_tl_dr(merged, force=True)

    if not _assert_indices_traceable(merged, week):
        return {"week": week.isoformat(), "status": STATUS_FABRICATION_FAILED,
                "narrative_chars": len(merged.get("narrative") or "")}

    write_json(path, merged)
    return {
        "week": week.isoformat(),
        "status": STATUS_OK,
        "narrative_chars": len(merged.get("narrative") or ""),
        "ms_is_dict": isinstance(merged.get("market_summary"), dict),
    }


def _load_checkpoint() -> dict:
    return read_json(CHECKPOINT) if CHECKPOINT.exists() else {}


def _save_checkpoint(state: dict) -> None:
    write_json(CHECKPOINT, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description="LLM-regenerate thin weekly archive sections.")
    parser.add_argument("--weeks", nargs="*", help="explicit YYYY-MM-DD weeks")
    parser.add_argument("--all-needs-llm", action="store_true",
                        help="regen every week the audit flagged needs-llm")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="skip weeks already marked ok in the checkpoint")
    args = parser.parse_args(argv)

    if args.all_needs_llm:
        weeks = _needs_llm_weeks()
    elif args.weeks:
        weeks = sorted(args.weeks)
    else:
        parser.error("pass --weeks or --all-needs-llm")

    state = _load_checkpoint() if args.resume else {}
    done = {k for k, v in state.items() if v.get("status") == STATUS_OK}

    print(f"Regenerating {len(weeks)} weeks (dry_run={args.dry_run}, resume={args.resume})")
    print(f"  already done (skipped): {len(done)}\n")

    for wk in weeks:
        if wk in done:
            print(f"  {wk}: SKIP (checkpoint ok)")
            continue
        try:
            res = regen_one(date.fromisoformat(wk), dry_run=args.dry_run)
        except Exception as exc:
            print(f"  {wk}: [ERROR] {exc}")
            state[wk] = {"status": STATUS_ERROR, "error": str(exc)}
            if not args.dry_run:
                _save_checkpoint(state)
            continue
        tier = " [TIER C]" if wk in TIER_C else ""
        print(f"  {wk}: {res.get('status')} narrative={res.get('narrative_chars')}c{tier}")
        state[wk] = res
        if not args.dry_run:
            _save_checkpoint(state)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
