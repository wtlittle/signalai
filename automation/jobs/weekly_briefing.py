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


def _rev_growth_missing(v) -> bool:
    """True when a deep-research revenue_growth value is absent or a useless zero.

    sonar-deep-research frequently returns ``0`` or an empty string for
    revenue_growth on value names (e.g. GPN, STZ, CTVA), which the card then
    renders as "N/A". Treat those as missing so we can backfill from yfinance.
    """
    raw = v.get("revenue_growth", v.get("rev_growth"))
    if raw in (None, "", "N/A", "n/a"):
        return True
    try:
        return float(str(raw).replace("%", "").replace("+", "").strip()) == 0.0
    except (TypeError, ValueError):
        return False


def _compute_revenue_growth(ticker: str) -> str | None:
    """Best-available YoY revenue growth for a ticker as a formatted string.

    Prefers trailing-twelve-month growth (last 4 quarters vs the prior 4); falls
    back to the most recent annual YoY. Returns e.g. "+4.2%" / "-2.1%", or None
    if no revenue history is available (extremely rare for a public company).
    """
    try:
        import yfinance as yf
    except Exception:
        return None
    try:
        t = yf.Ticker(ticker)
    except Exception:
        return None

    def _fmt(curr, prior):
        if not curr or not prior or prior == 0:
            return None
        pct = (curr / prior - 1.0) * 100.0
        return f"{'+' if pct >= 0 else ''}{pct:.1f}%"

    # 1) TTM: sum of last 4 quarters vs prior 4 quarters
    try:
        qi = t.quarterly_income_stmt
        if qi is not None and not qi.empty and "Total Revenue" in qi.index:
            row = qi.loc["Total Revenue"].dropna()
            vals = [float(x) for x in row.values]
            if len(vals) >= 8:
                ttm = sum(vals[:4])
                prior_ttm = sum(vals[4:8])
                out = _fmt(ttm, prior_ttm)
                if out:
                    return out
    except Exception:
        pass

    # 2) Fallback: most recent annual YoY
    try:
        ai = t.income_stmt
        if ai is not None and not ai.empty and "Total Revenue" in ai.index:
            row = ai.loc["Total Revenue"].dropna()
            vals = [float(x) for x in row.values]
            if len(vals) >= 2:
                out = _fmt(vals[0], vals[1])
                if out:
                    return out
    except Exception:
        pass

    return None


def _backfill_revenue_growth(value_picks: list) -> None:
    """Fill in missing revenue_growth on value picks using yfinance (in place).

    weekly_briefing.json is static client data, so the card renderer has no live
    fundamentals to fall back on. We resolve revenue growth here at generation
    time so a public-company card never shows "N/A".
    """
    for v in value_picks:
        ticker = (v.get("ticker") or "").strip()
        if not ticker or not _rev_growth_missing(v):
            continue
        computed = _compute_revenue_growth(ticker)
        if computed:
            v["revenue_growth"] = computed
            print(f"  [rev-growth] backfilled {ticker}: {computed}")
        else:
            print(f"  [rev-growth] no revenue history for {ticker}; left as-is")


def compile_briefing(value, momentum, trends) -> dict:
    """Merge 3 API responses into the weekly_briefing.json schema."""
    value_picks = _extract_list(value)
    momentum_picks = _extract_list(momentum)
    trends_data = _extract_dict(trends)

    # Resolve revenue growth for any value pick where deep-research left it
    # missing or zero, so cards never render "Rev Growth: N/A".
    _backfill_revenue_growth(value_picks)

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
