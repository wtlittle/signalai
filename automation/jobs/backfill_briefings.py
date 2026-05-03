"""
Historical weekly briefing backfill.
Usage:
    python -m automation.jobs.backfill_briefings             # last 25 weeks
    python -m automation.jobs.backfill_briefings --weeks 10
    python -m automation.jobs.backfill_briefings --patch-index-only
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR, WEEKLY_BRIEFING, ARCHIVE_BRIEFINGS_DIR
from automation.shared.tickers import load_tickers
from automation.shared.io_helpers import write_json
from automation.perplexity.client import call_perplexity
from automation.perplexity.prompts import (
    build_weekly_value_prompt_for_date,
    build_weekly_momentum_prompt_for_date,
    build_weekly_trends_prompt_for_date,
)


def last_friday_on_or_before(d: date) -> date:
    offset = (d.weekday() - 4) % 7
    return d - timedelta(days=offset)


def fridays_in_range(start: date, end: date) -> list:
    first = last_friday_on_or_before(end)
    fridays = []
    current = first
    while current >= start:
        fridays.append(current)
        current -= timedelta(weeks=1)
    return sorted(fridays)


def archive_path(week_ending: date) -> Path:
    return ARCHIVE_BRIEFINGS_DIR / f"weekly_briefing_{week_ending.isoformat()}.json"


def briefing_already_archived(week_ending: date) -> bool:
    p = archive_path(week_ending)
    return p.exists() and p.stat().st_size > 1000


def generate_briefing_for_week(week_ending: date, tickers: list) -> dict:
    week_str = week_ending.isoformat()
    print(f"\n  --- Generating briefing for week ending {week_str} ---")

    value = call_perplexity(
        "MARKET", f"weekly_value_{week_str}",
        build_weekly_value_prompt_for_date(week_ending),
        system="You are a senior equity research analyst. Return only a JSON array.",
        max_tokens=2500,
    )
    momentum = call_perplexity(
        "MARKET", f"weekly_momentum_{week_str}",
        build_weekly_momentum_prompt_for_date(week_ending),
        system="You are a senior equity research analyst. Return only a JSON array.",
        max_tokens=2000,
    )
    trends = call_perplexity(
        "MARKET", f"weekly_trends_{week_str}",
        build_weekly_trends_prompt_for_date(week_ending, tickers),
        system="You are a senior market strategist. Return only structured JSON.",
        max_tokens=3000,
    )

    return {
        "generated": f"{week_str}T00:00:00+0000",
        "week_ending": week_str,
        "value_picks":     value    if isinstance(value, list)    else (value.get("raw", [])    if isinstance(value, dict)    else []),
        "momentum_picks":  momentum if isinstance(momentum, list) else (momentum.get("raw", []) if isinstance(momentum, dict) else []),
        "market_summary":  {},
        "index_returns":   trends.get("index_returns", {}) if isinstance(trends, dict) else {},
        "trends":          trends.get("trends", [])        if isinstance(trends, dict) else [],
        "risks":           trends.get("risks", [])         if isinstance(trends, dict) else [],
        "watchlist_updates": trends.get("watchlist_movers", []) if isinstance(trends, dict) else [],
        "narrative":       trends.get("narrative", "")     if isinstance(trends, dict) else "",
    }


def save_archive_briefing(week_ending: date, briefing: dict):
    ARCHIVE_BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    p = archive_path(week_ending)
    write_json(p, briefing)
    print(f"  [SAVED] {p.relative_to(ROOT_DIR)}")


def patch_live_briefing_archive_index(all_weeks: list):
    if not WEEKLY_BRIEFING.exists():
        print("  [SKIP] weekly_briefing.json not found")
        return
    try:
        live = json.loads(WEEKLY_BRIEFING.read_text())
    except Exception:
        print("  [SKIP] Could not parse weekly_briefing.json")
        return

    entries = []
    for w in sorted(all_weeks):
        p = archive_path(w)
        if p.exists() and p.stat().st_size > 500:
            entries.append({
                "week_ending": w.isoformat(),
                "path": f"archive/briefings/weekly_briefing_{w.isoformat()}.json",
            })

    live_week = live.get("week_ending")
    if live_week and not any(e["week_ending"] == live_week for e in entries):
        entries.append({"week_ending": live_week, "path": "weekly_briefing.json"})

    entries.sort(key=lambda e: e["week_ending"])
    live["archive"] = entries
    write_json(WEEKLY_BRIEFING, live)
    print(f"  [PATCHED] weekly_briefing.json → archive index has {len(entries)} entries")


def patch_index_only():
    ARCHIVE_BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    found = []
    for f in sorted(ARCHIVE_BRIEFINGS_DIR.glob("weekly_briefing_????-??-??.json")):
        week_str = f.stem.replace("weekly_briefing_", "")
        try:
            found.append(date.fromisoformat(week_str))
        except ValueError:
            pass
    print(f"Found {len(found)} archived briefings")
    patch_live_briefing_archive_index(found)


def run(weeks: int = 25, start=None, end=None):
    tickers = load_tickers()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    force   = os.environ.get("FORCE_REGENERATE", "false").lower() == "true"

    today = date.today()
    if end is None:
        end = last_friday_on_or_before(today)
    if start is None:
        start = end - timedelta(weeks=weeks - 1)

    all_fridays = fridays_in_range(start, end)
    print(f"Backfilling {len(all_fridays)} weekly briefings: {start} → {end}")

    generated = skipped = 0
    for week_ending in all_fridays:
        if not force and briefing_already_archived(week_ending):
            print(f"  [SKIP] {week_ending} already archived")
            skipped += 1
            continue
        briefing = generate_briefing_for_week(week_ending, tickers)
        if not dry_run:
            save_archive_briefing(week_ending, briefing)
        generated += 1

    if not dry_run:
        patch_live_briefing_archive_index(all_fridays)

    print(f"\n=== Backfill complete: {generated} generated, {skipped} skipped ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=25)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end",   type=str, default=None)
    parser.add_argument("--patch-index-only", action="store_true")
    args = parser.parse_args()

    if args.patch_index_only:
        patch_index_only()
    else:
        run(
            weeks=args.weeks,
            start=date.fromisoformat(args.start) if args.start else None,
            end=date.fromisoformat(args.end)     if args.end   else None,
        )
