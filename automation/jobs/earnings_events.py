"""
Earnings event detection — identifies tickers in the pre/post earnings window.
Uses Yahoo Finance for earnings dates (free, no LLM cost).
Falls back to Perplexity only for tickers where yfinance has no data.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

# Allow running as standalone script
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from automation.shared.paths import EARNINGS_CALENDAR, EARNINGS_DATA, ROOT_DIR
from automation.shared.tickers import load_tickers, load_common_names
from automation.shared.io_helpers import write_json

TODAY = date.today()
PRE_WINDOW = int(os.environ.get("MAX_PRE_EARNINGS_DAYS", 14))
POST_WINDOW = int(os.environ.get("MAX_POST_EARNINGS_DAYS", 14))


def fetch_earnings_dates_yfinance(tickers: list[str]) -> dict:
    """Fetch earnings dates for all tickers via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [WARN] yfinance not installed — skipping Yahoo Finance lookup")
        return {}

    results = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            dates_found = set()

            # Try calendar
            cal = t.calendar
            if cal and isinstance(cal, dict):
                ed = cal.get("Earnings Date", [])
                if isinstance(ed, list):
                    dates_found.update(str(d)[:10] for d in ed)
                elif hasattr(ed, "strftime"):
                    dates_found.add(ed.strftime("%Y-%m-%d"))

            # Try earnings_dates property
            try:
                ed_series = t.earnings_dates
                if ed_series is not None and hasattr(ed_series, "index"):
                    for idx in ed_series.index:
                        dates_found.add(str(idx)[:10])
            except Exception:
                pass

            results[ticker] = sorted(dates_found)
        except Exception as e:
            results[ticker] = []

    return results


def classify_events(
    all_dates: dict[str, list[str]], names: dict[str, str]
) -> tuple[list[dict], list[dict]]:
    """Split tickers into pre-earnings and post-earnings lists."""
    pre = []
    post = []

    for ticker, dates in all_dates.items():
        for d in dates:
            try:
                ed = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                continue

            days_until = (ed - TODAY).days
            if 0 <= days_until <= PRE_WINDOW:
                pre.append({
                    "ticker": ticker,
                    "name": names.get(ticker, ticker),
                    "earnings_date": d,
                    "days_until": days_until,
                    "event_type": "pre",
                })
            elif -POST_WINDOW <= days_until < 0:
                post.append({
                    "ticker": ticker,
                    "name": names.get(ticker, ticker),
                    "earnings_date": d,
                    "days_since": abs(days_until),
                    "event_type": "post",
                })

    pre.sort(key=lambda x: x["days_until"])
    post.sort(key=lambda x: x["days_since"])
    return pre, post


def run():
    """Main entry: detect earnings events and update calendar."""
    tickers = load_tickers()
    names = load_common_names()
    print(f"Scanning {len(tickers)} tickers for earnings events...")

    all_dates = fetch_earnings_dates_yfinance(tickers)
    tickers_with_dates = sum(1 for v in all_dates.values() if v)
    print(f"  Got dates for {tickers_with_dates}/{len(tickers)} tickers")

    pre, post = classify_events(all_dates, names)
    print(f"  Pre-earnings: {len(pre)} tickers (next {PRE_WINDOW} days)")
    print(f"  Post-earnings: {len(post)} tickers (past {POST_WINDOW} days)")

    # Save earnings data
    earnings_data = {
        "as_of": TODAY.isoformat(),
        "pre_window_days": PRE_WINDOW,
        "post_window_days": POST_WINDOW,
        "all_tickers": {t: {"earnings_dates": d} for t, d in all_dates.items()},
        "summary": {
            "total_tickers": len(tickers),
            "tickers_with_dates": tickers_with_dates,
            "pre_earnings_count": len(pre),
            "post_earnings_count": len(post),
        },
    }
    write_json(EARNINGS_DATA, earnings_data)

    # Update earnings calendar
    calendar = {
        "updated": datetime.now().isoformat(),
        "pre_earnings": [
            {
                "ticker": e["ticker"],
                "company": e["name"],
                "date": e["earnings_date"],
                "days_until": e["days_until"],
            }
            for e in pre
        ],
        "post_earnings": [
            {
                "ticker": e["ticker"],
                "company": e["name"],
                "date": e["earnings_date"],
                "days_since": e["days_since"],
            }
            for e in post
        ],
    }
    write_json(EARNINGS_CALENDAR, calendar)
    print("  Earnings calendar updated.")

    return pre, post


if __name__ == "__main__":
    run()
