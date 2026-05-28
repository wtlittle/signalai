"""
Weekly briefing generation via sonar-deep-research API.

Runs 3 Perplexity deep-research calls in parallel (value, momentum, trends),
compiles the results into weekly_briefing.json, and archives the output.

Usage:
    python -m automation.jobs.weekly_briefing
    python -m automation.jobs.weekly_briefing --output /tmp/test_briefing.json
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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


def _extract_list(result) -> list:
    """Safely extract a list from a Perplexity response."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("raw", []) if not isinstance(result.get("raw"), str) else []
    return []


def _extract_dict(result) -> dict:
    """Safely extract a dict from a Perplexity response."""
    if isinstance(result, dict) and "raw" not in result:
        return result
    return {}


def _fetch_value():
    """Deep-research call for top 5 value stocks."""
    print("  [1/3] Researching top value stocks...")
    return call_perplexity(
        "MARKET", "weekly_value",
        build_weekly_value_prompt(),
        system="You are a senior equity research analyst. Return only a JSON array.",
        max_tokens=4000,
    )


def _fetch_momentum():
    """Deep-research call for top 5 momentum stocks."""
    print("  [2/3] Researching top momentum stocks...")
    return call_perplexity(
        "MARKET", "weekly_momentum",
        build_weekly_momentum_prompt(),
        system="You are a senior equity research analyst. Return only a JSON array.",
        max_tokens=3000,
    )


def _fetch_trends(tickers: list[str]):
    """Deep-research call for market trends + watchlist movers."""
    print("  [3/3] Researching market trends and watchlist movers...")
    return call_perplexity(
        "MARKET", "weekly_trends",
        build_weekly_trends_prompt(tickers),
        system="You are a senior market strategist. Return only structured JSON.",
        max_tokens=4000,
    )


def compile_briefing(value, momentum, trends) -> dict:
    """Merge 3 API responses into the weekly_briefing.json schema."""
    value_picks = _extract_list(value)
    momentum_picks = _extract_list(momentum)
    trends_data = _extract_dict(trends)

    return {
        "generated": datetime.now(tz=__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week_ending": TODAY.isoformat(),
        "value_picks": value_picks,
        "momentum_picks": momentum_picks,
        "index_returns": trends_data.get("index_returns", {}),
        "trends": trends_data.get("trends", []),
        "risks": trends_data.get("risks", []),
        "watchlist_movers": trends_data.get("watchlist_movers", []),
        "narrative": trends_data.get("narrative", ""),
    }


def run(output_path: Path | None = None) -> dict:
    """Main entry: generate weekly market briefing with parallel deep-research calls."""
    tickers = load_tickers()
    out = output_path or WEEKLY_BRIEFING
    print(f"Generating weekly briefing for week ending {TODAY.isoformat()}...")
    print(f"Output: {out}")

    # --- Run 3 deep-research calls in parallel ---
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_value = pool.submit(_fetch_value)
        future_momentum = pool.submit(_fetch_momentum)
        future_trends = pool.submit(_fetch_trends, tickers)

        value = future_value.result()
        momentum = future_momentum.result()
        trends = future_trends.result()

    # --- Compile ---
    print("\nCompiling weekly_briefing.json...")
    briefing = compile_briefing(value, momentum, trends)

    write_json(out, briefing)
    print(f"  Saved to {out}")

    # Auto-archive (only when writing to the default location)
    if out == WEEKLY_BRIEFING:
        try:
            from automation.jobs.backfill_briefings import save_archive_briefing, patch_live_briefing_archive_index
            save_archive_briefing(TODAY, briefing)
            patch_live_briefing_archive_index([TODAY])
        except Exception as exc:
            print(f"  [weekly_briefing] archive failed: {exc}")

    print(f"  Value picks: {len(briefing['value_picks'])}")
    print(f"  Momentum picks: {len(briefing['momentum_picks'])}")
    print(f"  Trends: {len(briefing['trends'])}")

    # Emit subscriber alert
    if out == WEEKLY_BRIEFING:
        try:
            from automation.alerts import emit_alert
            wk = briefing.get("week_ending") or TODAY.isoformat()
            narrative = (briefing.get("narrative") or "Fresh weekly briefing available").strip()
            if len(narrative) > 180:
                narrative = narrative[:177] + "..."
            emit_alert(
                alert_type="weekly_briefing",
                summary=f"Week ending {wk}: {narrative}",
                ticker=None,
                severity="info",
                link="https://www.perplexity.ai/computer/a/stock-watchlist-terminal-qBSMi5vnQ1OezaO1hksi5A",
                extra={
                    "week_ending": wk,
                    "value_count": len(briefing["value_picks"]),
                    "momentum_count": len(briefing["momentum_picks"]),
                },
            )
        except Exception as exc:
            print(f"  [weekly_briefing] emit_alert failed: {exc}")

    return briefing


def main():
    parser = argparse.ArgumentParser(description="Generate weekly market briefing via sonar-deep-research")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output path for weekly_briefing.json (default: repo weekly_briefing.json)")
    args = parser.parse_args()
    output_path = Path(args.output) if args.output else None
    run(output_path=output_path)


if __name__ == "__main__":
    main()
