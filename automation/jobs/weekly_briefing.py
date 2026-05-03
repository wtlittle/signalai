"""
Weekly briefing generation.
Calls Perplexity for value picks, momentum picks, and market trends.
Results are compiled into weekly_briefing.json.
"""
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from automation.shared.paths import WEEKLY_BRIEFING
from automation.shared.tickers import load_tickers
from automation.shared.io_helpers import write_json
from automation.shared.cache import save_research_cache, load_research_cache, research_cache_exists
from automation.perplexity.client import call_perplexity
from automation.perplexity.prompts import (
    build_weekly_value_prompt,
    build_weekly_momentum_prompt,
    build_weekly_trends_prompt,
)

TODAY = date.today()


def run():
    """Main entry: generate weekly market briefing."""
    tickers = load_tickers()
    print(f"Generating weekly briefing for week ending {TODAY.isoformat()}...")

    # --- 1. Value picks ---
    print("\n1. Researching top value stocks...")
    value = call_perplexity(
        "MARKET", "weekly_value",
        build_weekly_value_prompt(),
        system="You are a senior equity research analyst. Return only a JSON array.",
        max_tokens=2500,
    )

    # --- 2. Momentum picks ---
    print("\n2. Researching top momentum stocks...")
    momentum = call_perplexity(
        "MARKET", "weekly_momentum",
        build_weekly_momentum_prompt(),
        system="You are a senior equity research analyst. Return only a JSON array.",
        max_tokens=2000,
    )

    # --- 3. Market trends + watchlist scan ---
    print("\n3. Researching market trends and watchlist movers...")
    trends = call_perplexity(
        "MARKET", "weekly_trends",
        build_weekly_trends_prompt(tickers),
        system="You are a senior market strategist. Return only structured JSON.",
        max_tokens=3000,
    )

    # --- 4. Compile ---
    print("\n4. Compiling weekly_briefing.json...")
    briefing = {
        "generated": datetime.now().isoformat(),
        "week_ending": TODAY.isoformat(),
        "value_picks": value if isinstance(value, list) else value.get("raw", []) if isinstance(value, dict) else [],
        "momentum_picks": momentum if isinstance(momentum, list) else momentum.get("raw", []) if isinstance(momentum, dict) else [],
        "index_returns": trends.get("index_returns", {}) if isinstance(trends, dict) else {},
        "trends": trends.get("trends", []) if isinstance(trends, dict) else [],
        "risks": trends.get("risks", []) if isinstance(trends, dict) else [],
        "watchlist_movers": trends.get("watchlist_movers", []) if isinstance(trends, dict) else [],
        "narrative": trends.get("narrative", "") if isinstance(trends, dict) else "",
    }

    write_json(WEEKLY_BRIEFING, briefing)
    print(f"  Weekly briefing saved to {WEEKLY_BRIEFING.name}")

    # Auto-archive this week's briefing
    from automation.jobs.backfill_briefings import save_archive_briefing, patch_live_briefing_archive_index
    from datetime import date as _date
    save_archive_briefing(_date.today(), briefing)
    patch_live_briefing_archive_index([_date.today()])
    print(f"  Value picks: {len(briefing['value_picks'])}")
    print(f"  Momentum picks: {len(briefing['momentum_picks'])}")
    print(f"  Trends: {len(briefing['trends'])}")

    return briefing


if __name__ == "__main__":
    run()
