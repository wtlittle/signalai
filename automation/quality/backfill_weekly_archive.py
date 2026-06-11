#!/usr/bin/env python3
"""Deterministic backfill pass over the weekly briefing archive.

Applies the SAME deterministic helpers the live pipeline uses (no LLM spend) to
every archived ``weekly_briefing_*.json`` so historical weeks meet the current
production standard for the fields that can be reconstructed from grounded data:

  * sentinel normalization        — null any literal "MISSING"/"N/A" leaf strings
  * _backfill_volume_sections     — watchlist_updates / sector_summary /
                                    upcoming_catalysts from as-of price+calendar
  * _backfill_index_and_headline  — measured S&P+Nasdaq 1W/1M/3M/YTD + macro_regime
  * inject_tl_dr                  — >= 5 deterministic TL;DR bullets

Does NOT touch narrative / rich market_summary / rich pick context — those
require a new sonar-deep-research call (needs-llm) and are out of scope here.

No fabrication: every number traces to the historical close series or the real
earnings/macro calendar. A field with no grounded source stays null (em-dash).

Usage:
    python -m automation.quality.backfill_weekly_archive          # all weeks
    python -m automation.quality.backfill_weekly_archive --dry-run
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIEFING_GLOB = str(ROOT / "archive" / "briefings" / "weekly_briefing_*.json")

_SENTINELS = {"missing", "n/a", "na"}
_INSTITUTIONAL_RE = re.compile(r"for\s+institutional\s+use\s+only", re.IGNORECASE)
_ORPHAN_CITE_RE = re.compile(r"\s?\[\d+\]")
_REFS_HEADING_RE = re.compile(
    r"(?:^|\n)\s*#{0,3}\s*\**\s*(?:sources|references)\b", re.IGNORECASE)


def _null_sentinels(obj):
    """Recursively replace literal MISSING/N/A leaf strings with None."""
    if isinstance(obj, dict):
        return {k: _null_sentinels(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_null_sentinels(v) for v in obj]
    if isinstance(obj, str) and obj.strip().lower() in _SENTINELS:
        return None
    return obj


def _strip_institutional(obj):
    """Recursively strip the 'For Institutional Use Only' phrase from strings."""
    if isinstance(obj, dict):
        return {k: _strip_institutional(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_institutional(v) for v in obj]
    if isinstance(obj, str) and _INSTITUTIONAL_RE.search(obj):
        return _INSTITUTIONAL_RE.sub("", obj).strip()
    return obj


def _strip_orphan_citations(d: dict) -> None:
    """Remove bare [N] markers from narrative/market_summary when no sources list
    resolves them.

    sonar-deep-research embeds inline [N] markers in the prose but the weekly
    pipeline never stored the matching reference list, so they render as dangling
    "[8]" text (the orphan-citation defect). When there is no `sources` field and
    no References/Sources heading, the markers point nowhere — strip them. This is
    deterministic text hygiene, not fabrication: it only removes unresolvable
    pointers, never adds or alters a claim.
    """
    has_sources = bool(d.get("sources")) or bool(d.get("sources_list"))
    for key in ("narrative", "market_summary"):
        val = d.get(key)
        if not isinstance(val, str) or "[" not in val:
            continue
        if has_sources or _REFS_HEADING_RE.search(val):
            continue  # a real reference list exists; leave markers intact
        d[key] = _ORPHAN_CITE_RE.sub("", val)


def _rebuild_watchlist_from_movers(d: dict, context: dict) -> None:
    """Rebuild watchlist_updates from the FULL grounded mover set when more movers
    exist than the stored list carries.

    The shared _backfill_volume_sections only fires when the stored count is below
    min(20, movers); a week that already has, say, 36 entries but 67 grounded
    movers is left thin. Reuse the same construction helper to widen the list
    toward the >=50 bar. No fabrication: a week with <50 real movers stays below 50.
    """
    from automation.jobs.weekly_briefing import _movers_to_watchlist

    movers = context.get("movers") or []
    stored = d.get("watchlist_updates") or []
    if len(movers) <= len(stored):
        return
    updates, projection = _movers_to_watchlist(movers)
    if len(updates) > len(stored):
        d["watchlist_updates"] = updates
        d["watchlist_movers"] = projection


def _week_date(d: dict, fallback_name: str) -> date:
    raw = d.get("week_ending")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", fallback_name)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    raise ValueError(f"cannot determine week date for {fallback_name}")


def backfill_one(path: Path, dry_run: bool = False) -> dict:
    """Apply deterministic backfills to one archive file. Returns a change log."""
    # Local imports so a missing pipeline module doesn't break the whole module.
    from automation.jobs.weekly_briefing_context import build_context_blocks_asof
    from automation.jobs.weekly_briefing import (
        _backfill_volume_sections,
        _backfill_index_and_headline,
    )
    from automation.jobs.backfill_tldr import inject_tl_dr

    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)

    before = {
        "macro_regime": bool(d.get("macro_regime")),
        "tl_dr": len(d.get("tl_dr") or []),
        "watchlist_updates": len(d.get("watchlist_updates") or []),
        "sector_summary": len(d.get("sector_summary") or []),
        "upcoming_catalysts": len(d.get("upcoming_catalysts") or []),
        "sp500_measured": isinstance((d.get("index_returns") or {}).get("sp500"), dict)
        and isinstance((d.get("index_returns") or {})["sp500"].get("weekly_pct"), (int, float)),
    }

    week = _week_date(d, path.name)

    # 1. Hygiene: sentinel nulling + institutional strip + orphan-citation strip.
    d = _null_sentinels(d)
    d = _strip_institutional(d)
    _strip_orphan_citations(d)

    # 2. Grounded as-of context + volume sections.
    context = build_context_blocks_asof(week)
    _backfill_volume_sections(d, context, target_week=week)
    _rebuild_watchlist_from_movers(d, context)

    # 3. Measured indices + macro_regime headline.
    _backfill_index_and_headline(d, week)

    # 4. TL;DR top-up (force so short/empty arrays get rebuilt).
    d = inject_tl_dr(d, force=True)

    after = {
        "macro_regime": bool(d.get("macro_regime")),
        "tl_dr": len(d.get("tl_dr") or []),
        "watchlist_updates": len(d.get("watchlist_updates") or []),
        "sector_summary": len(d.get("sector_summary") or []),
        "upcoming_catalysts": len(d.get("upcoming_catalysts") or []),
        "sp500_measured": isinstance((d.get("index_returns") or {}).get("sp500"), dict)
        and isinstance((d.get("index_returns") or {})["sp500"].get("weekly_pct"), (int, float)),
    }

    if not dry_run:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    return {"week": week.isoformat(), "before": before, "after": after}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deterministic weekly archive backfill.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    files = sorted(glob.glob(BRIEFING_GLOB))
    print(f"Backfilling {len(files)} archive files (dry_run={args.dry_run})...\n")
    for f in files:
        try:
            log = backfill_one(Path(f), dry_run=args.dry_run)
        except Exception as exc:
            print(f"  [ERROR] {Path(f).name}: {exc}")
            continue
        b, a = log["before"], log["after"]
        print(
            f"  {log['week']}: macro {b['macro_regime']}->{a['macro_regime']}  "
            f"tldr {b['tl_dr']}->{a['tl_dr']}  wl {b['watchlist_updates']}->{a['watchlist_updates']}  "
            f"sect {b['sector_summary']}->{a['sector_summary']}  "
            f"cat {b['upcoming_catalysts']}->{a['upcoming_catalysts']}  "
            f"spMeasured {b['sp500_measured']}->{a['sp500_measured']}"
        )
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
