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


def _salvage_json_array(raw_text: str) -> list | None:
    """Attempt to recover a list of JSON objects from a broken response.

    Tries four strategies in order:
    1. Direct json.loads after cleaning bad escapes
    2. Extract the outermost [...] array substring and parse it
    3. Extract individual {...} objects one by one
    4. Return None if nothing recoverable
    """
    import re as _re

    def _clean_escapes(s: str) -> str:
        return _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)

    def _try(s: str):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None

    for text in (raw_text, _clean_escapes(raw_text)):
        # Strategy 1: direct parse
        result = _try(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "raw" not in result:
            return [result]

        # Strategy 2: extract outermost array
        m = _re.search(r'\[.*\]', text, flags=_re.DOTALL)
        if m:
            result = _try(m.group(0))
            if isinstance(result, list) and result:
                return result

        # Strategy 3: extract individual objects
        objects = []
        for match in _re.finditer(
            r'\{(?:[^{}]|\{[^{}]*\})*\}', text, flags=_re.DOTALL
        ):
            obj = _try(match.group())
            if isinstance(obj, dict) and len(obj) > 1:
                objects.append(obj)
        if objects:
            return objects

    return None


def _extract_list(result) -> list:
    """Safely extract a list from a Perplexity response.

    If the response is a {"raw": ...} fallback (meaning parsing failed in
    client.py), attempt to salvage a list from the raw string before giving up.
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        raw = result.get("raw")
        if isinstance(raw, str) and raw.strip():
            salvaged = _salvage_json_array(raw)
            if salvaged:
                print(f"  [SALVAGE] Recovered {len(salvaged)} objects from raw payload")
                return salvaged
            else:
                print(f"  [SALVAGE FAILED] Could not recover objects from raw payload (len={len(raw)}); first 300 chars: {raw[:300]!r}")
                return []
        if isinstance(raw, list):
            return raw
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
        system="You are a senior equity research analyst. RESPOND ONLY WITH VALID JSON. Your entire response must be a JSON array starting with [ and ending with ]. No markdown. No prose. No code fences. No explanation. Only JSON.",
        max_tokens=8000,
    )


def _fetch_momentum():
    """Deep-research call for top 5 momentum stocks."""
    print("  [2/3] Researching top momentum stocks...")
    return call_perplexity(
        "MARKET", "weekly_momentum",
        build_weekly_momentum_prompt(),
        system="You are a senior equity research analyst. RESPOND ONLY WITH VALID JSON. Your entire response must be a JSON array starting with [ and ending with ]. No markdown. No prose. No code fences. No explanation. Only JSON.",
        max_tokens=6000,
    )


def _fetch_trends(tickers: list[str]):
    """Deep-research call for market trends + watchlist movers."""
    print("  [3/3] Researching market trends and watchlist movers...")
    return call_perplexity(
        "MARKET", "weekly_trends",
        build_weekly_trends_prompt(tickers),
        system="You are a senior market strategist. RESPOND ONLY WITH VALID JSON. Your entire response must be a JSON object starting with { and ending with }. No markdown. No prose. No code fences. No explanation. Only JSON.",
        max_tokens=6000,
    )


def _is_zero_or_blank(raw) -> bool:
    """True when a deep-research metric is absent or a useless zero.

    sonar-deep-research frequently returns ``0`` / "" for both revenue_growth and
    the 1W/1M/3M return windows, which then render as "N/A". Treat those as
    missing so we can backfill from yfinance.
    """
    if raw in (None, "", "N/A", "n/a"):
        return True
    try:
        return float(str(raw).replace("%", "").replace("+", "").strip()) == 0.0
    except (TypeError, ValueError):
        return False


def _rev_growth_missing(v) -> bool:
    """True when a pick's revenue_growth is absent or a useless zero."""
    return _is_zero_or_blank(v.get("revenue_growth", v.get("rev_growth")))


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


def _backfill_revenue_growth(picks: list) -> None:
    """Fill in missing revenue_growth on a list of picks using yfinance (in place).

    weekly_briefing.json is static client data, so the card renderer has no live
    fundamentals to fall back on. We resolve revenue growth here at generation
    time so a public-company card never shows "N/A". Shared by both value and
    momentum picks.
    """
    for v in picks:
        ticker = (v.get("ticker") or "").strip()
        if not ticker or not _rev_growth_missing(v):
            continue
        computed = _compute_revenue_growth(ticker)
        if computed:
            v["revenue_growth"] = computed
            print(f"  [rev-growth] backfilled {ticker}: {computed}")
        else:
            print(f"  [rev-growth] no revenue history for {ticker}; left as-is")


# Trailing-return windows in trading days: 1 week (~5), 1 month (~21), 3 months (~63).
_RETURN_WINDOWS = (
    ("one_week_perf", 5),
    ("one_month_perf", 21),
    ("three_month_perf", 63),
)


def _compute_momentum_returns(ticker: str) -> dict:
    """Trailing 1W/1M/3M price returns for a ticker as formatted strings.

    Uses ~6 months of daily closes from yfinance and compares the latest close
    to the close N trading days back (5/21/63). Returns a dict keyed by
    one_week_perf / one_month_perf / three_month_perf, e.g. "+4.2%" / "-2.1%".
    Windows with insufficient history are omitted (extremely rare for a public
    US ticker with active trading history).
    """
    try:
        import yfinance as yf
    except Exception:
        return {}
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
    except Exception:
        return {}
    if hist is None or hist.empty or "Close" not in hist.columns:
        return {}

    closes = [float(x) for x in hist["Close"].dropna().values]
    if len(closes) < 2:
        return {}

    latest = closes[-1]
    out = {}
    for field, lookback in _RETURN_WINDOWS:
        if len(closes) <= lookback:
            continue
        prior = closes[-(lookback + 1)]
        if not prior:
            continue
        pct = (latest / prior - 1.0) * 100.0
        out[field] = f"{'+' if pct >= 0 else ''}{pct:.1f}%"
    return out


def _backfill_momentum_returns(momentum_picks: list) -> None:
    """Fill in missing 1W/1M/3M returns on momentum picks using yfinance (in place).

    Deep-research ranks momentum names by recent performance but often emits ``0``
    for the displayed return fields, so the card renders "1W/1M/3M: N/A". We
    recompute the trailing windows from price history at generation time.
    """
    for m in momentum_picks:
        ticker = (m.get("ticker") or "").strip()
        if not ticker:
            continue
        needed = [
            field for field, _ in _RETURN_WINDOWS
            if _is_zero_or_blank(m.get(field))
        ]
        if not needed:
            continue
        computed = _compute_momentum_returns(ticker)
        if not computed:
            print(f"  [returns] no price history for {ticker}; left as-is")
            continue
        for field in needed:
            if field in computed:
                m[field] = computed[field]
        print(
            f"  [returns] backfilled {ticker}: "
            + ", ".join(f"{f}={computed[f]}" for f in needed if f in computed)
        )


def compile_briefing(value, momentum, trends) -> dict:
    """Merge 3 API responses into the weekly_briefing.json schema."""
    value_picks = _extract_list(value)
    momentum_picks = _extract_list(momentum)
    trends_data = _extract_dict(trends)

    if not value_picks:
        print("  [WARNING] value_picks is EMPTY — value section will be absent from this week's briefing. Check the raw cache for the weekly_value response.")
    if not momentum_picks:
        print("  [WARNING] momentum_picks is EMPTY — momentum section will be absent from this week's briefing. Check the raw cache for the weekly_momentum response.")
    if not trends_data:
        print("  [WARNING] trends_data is EMPTY — narrative, index_returns, trends, and risks will be absent. Check the raw cache for the weekly_trends response.")

    # Resolve revenue growth for any pick where deep-research left it missing or
    # zero, so cards never render "Rev Growth: N/A" (shared by value + momentum).
    _backfill_revenue_growth(value_picks)
    _backfill_revenue_growth(momentum_picks)

    # Recompute trailing 1W/1M/3M returns for momentum picks from price history
    # so cards never render "1W/1M/3M: N/A".
    _backfill_momentum_returns(momentum_picks)

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
            from automation.jobs.backfill_briefings import save_archive_briefing, patch_index_only
            save_archive_briefing(TODAY, briefing)
            patch_index_only()
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
    parser.add_argument("--week-ending", type=str, default=None,
                        help="Regenerate a specific past week (YYYY-MM-DD); overrides today's date")
    args = parser.parse_args()
    if args.week_ending:
        global TODAY
        TODAY = date.fromisoformat(args.week_ending)
    output_path = Path(args.output) if args.output else None
    run(output_path=output_path)


if __name__ == "__main__":
    main()
