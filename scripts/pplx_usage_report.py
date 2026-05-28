"""
CLI report for Perplexity API usage from the perplexity_api_usage table.

Usage:
    python -m scripts.pplx_usage_report --days 7
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from automation.shared.supabase_client import fetch_rows


def _fetch_usage(days: int) -> list[dict]:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    return fetch_rows(
        "perplexity_api_usage",
        params={
            "ts": f"gte.{cutoff}",
            "order": "ts.desc",
            "select": "source,ticker,model,estimated_cost_usd,ts,status",
        },
    )


def _fmt_usd(val: float) -> str:
    return f"${val:,.4f}"


def _print_table(headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None) -> None:
    if not col_widths:
        col_widths = [max(len(h), max((len(str(r[i])) for r in rows), default=0)) for i, h in enumerate(headers)]
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(header_line)
    print("  ".join("-" * w for w in col_widths))
    for row in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, col_widths)))


def run(days: int) -> None:
    rows = _fetch_usage(days)
    if not rows:
        print(f"No Perplexity API usage found in the last {days} day(s).")
        return

    total_cost = 0.0
    by_source: dict[str, float] = defaultdict(float)
    by_model: dict[str, float] = defaultdict(float)
    by_ticker: dict[str, float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)

    for r in rows:
        cost = float(r.get("estimated_cost_usd") or 0)
        total_cost += cost
        by_source[r.get("source", "unknown")] += cost
        by_model[r.get("model", "unknown")] += cost
        ticker = r.get("ticker")
        if ticker:
            by_ticker[ticker] += cost
        ts = r.get("ts", "")[:10]  # YYYY-MM-DD
        if ts:
            by_day[ts] += cost

    print(f"\nPerplexity API Usage Report  --  last {days} day(s)")
    print(f"Total calls: {len(rows)}")
    print(f"Total spend: {_fmt_usd(total_cost)}")
    print()

    # Spend by source
    print("Spend by source:")
    source_rows = sorted(by_source.items(), key=lambda x: x[1], reverse=True)
    _print_table(
        ["Source", "Cost"],
        [[s, _fmt_usd(c)] for s, c in source_rows],
        [30, 12],
    )
    print()

    # Spend by model
    print("Spend by model:")
    model_rows = sorted(by_model.items(), key=lambda x: x[1], reverse=True)
    _print_table(
        ["Model", "Cost"],
        [[m, _fmt_usd(c)] for m, c in model_rows],
        [25, 12],
    )
    print()

    # Top 10 tickers
    print("Top 10 most expensive tickers:")
    ticker_rows = sorted(by_ticker.items(), key=lambda x: x[1], reverse=True)[:10]
    _print_table(
        ["Ticker", "Cost"],
        [[t, _fmt_usd(c)] for t, c in ticker_rows],
        [15, 12],
    )
    print()

    # Daily trend
    print("Daily trend:")
    day_rows = sorted(by_day.items())
    _print_table(
        ["Date", "Cost"],
        [[d, _fmt_usd(c)] for d, c in day_rows],
        [12, 12],
    )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Perplexity API usage report")
    parser.add_argument("--days", type=int, default=7, help="Number of days to report on (default: 7)")
    args = parser.parse_args()
    run(args.days)


if __name__ == "__main__":
    main()
