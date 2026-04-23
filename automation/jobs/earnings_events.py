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


def _classify_timing_from_hour(hour_et: int | None) -> str:
    """Map a US/Eastern hour-of-day into a reporting window tag.

    Yahoo Finance exposes earnings times as timestamps. For US issuers the
    convention is: BMO prints roughly 04:00–09:00 ET (well before 09:30 open),
    AMC prints roughly 16:00–20:30 ET (after 16:00 close), and anything
    else is ambiguous (TBD). Missing input also returns TBD so we don't
    invent a window.
    """
    if hour_et is None:
        return "TBD"
    if 0 <= hour_et < 9:
        return "BMO"
    if 16 <= hour_et < 23:
        return "AMC"
    return "TBD"


def fetch_earnings_dates_yfinance(tickers: list[str]) -> tuple[dict, dict]:
    """Fetch earnings dates + reporting timing (BMO/AMC/TBD) via yfinance.

    Returns (dates_by_ticker, timing_by_ticker_date):
        dates_by_ticker[ticker]             = ["YYYY-MM-DD", ...]
        timing_by_ticker_date[ticker][date] = "BMO" | "AMC" | "TBD"
    """
    try:
        import yfinance as yf
    except ImportError:
        print("  [WARN] yfinance not installed — skipping Yahoo Finance lookup")
        return {}, {}

    # US/Eastern tz for mapping the raw timestamp hour to BMO/AMC.
    try:
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
    except Exception:
        ET = None

    results: dict[str, list[str]] = {}
    timings: dict[str, dict[str, str]] = {}

    for ticker in tickers:
        dates_found: set[str] = set()
        timing_for_ticker: dict[str, str] = {}
        try:
            t = yf.Ticker(ticker)

            # --- Source 1: calendar.Earnings Date (list of datetime)
            cal = t.calendar
            if cal and isinstance(cal, dict):
                ed = cal.get("Earnings Date", [])
                cal_items = ed if isinstance(ed, list) else [ed] if ed else []
                for d in cal_items:
                    try:
                        ds = str(d)[:10]
                        dates_found.add(ds)
                        # yfinance calendar returns naive dates (no hour info);
                        # do NOT infer timing from these — fall through to earnings_dates.
                    except Exception:
                        pass

            # --- Source 2: earnings_dates DataFrame (pd DatetimeIndex, tz-aware)
            try:
                ed_series = t.earnings_dates
                if ed_series is not None and hasattr(ed_series, "index"):
                    for idx in ed_series.index:
                        ds = str(idx)[:10]
                        dates_found.add(ds)
                        # Convert the tz-aware Timestamp to ET and map hour -> window.
                        hour_et = None
                        try:
                            if ET is not None and hasattr(idx, "tz_convert"):
                                hour_et = idx.tz_convert(ET).hour
                            elif hasattr(idx, "tz_localize") and hasattr(idx, "tz"):
                                # Fallback: assume UTC, subtract 4h for EDT (approx).
                                hour_et = (idx.hour - 4) % 24
                            elif hasattr(idx, "hour"):
                                hour_et = idx.hour
                        except Exception:
                            hour_et = None
                        tag = _classify_timing_from_hour(hour_et)
                        # Do not overwrite a non-TBD classification already seen.
                        if timing_for_ticker.get(ds, "TBD") == "TBD":
                            timing_for_ticker[ds] = tag
            except Exception:
                pass

            # --- Source 3: info.earningsTimestamp / earningsTimestampStart (epoch seconds)
            try:
                info = t.info or {}
                for key in ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd"):
                    ts = info.get(key)
                    if not ts:
                        continue
                    from datetime import datetime as _dt, timezone as _tz
                    dt_utc = _dt.fromtimestamp(int(ts), tz=_tz.utc)
                    if ET is not None:
                        dt_et = dt_utc.astimezone(ET)
                    else:
                        dt_et = dt_utc  # best-effort fallback
                    ds = dt_et.date().isoformat()
                    dates_found.add(ds)
                    tag = _classify_timing_from_hour(dt_et.hour)
                    if timing_for_ticker.get(ds, "TBD") == "TBD":
                        timing_for_ticker[ds] = tag
            except Exception:
                pass

            results[ticker] = sorted(dates_found)
            timings[ticker] = timing_for_ticker
        except Exception:
            results[ticker] = []
            timings[ticker] = {}

    return results, timings


def classify_events(
    all_dates: dict[str, list[str]],
    names: dict[str, str],
    timings: dict[str, dict[str, str]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split tickers into pre-earnings and post-earnings lists.

    When `timings` is provided, each output row carries a `timing` field
    ("BMO" | "AMC" | "TBD") sourced from yfinance; falls back to "TBD".
    """
    pre = []
    post = []
    timings = timings or {}

    for ticker, dates in all_dates.items():
        for d in dates:
            try:
                ed = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                continue

            timing = (timings.get(ticker) or {}).get(d, "TBD")
            days_until = (ed - TODAY).days
            if 0 <= days_until <= PRE_WINDOW:
                pre.append({
                    "ticker": ticker,
                    "name": names.get(ticker, ticker),
                    "earnings_date": d,
                    "days_until": days_until,
                    "timing": timing,
                    "event_type": "pre",
                })
            elif -POST_WINDOW <= days_until < 0:
                post.append({
                    "ticker": ticker,
                    "name": names.get(ticker, ticker),
                    "earnings_date": d,
                    "days_since": abs(days_until),
                    "timing": timing,
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

    all_dates, all_timings = fetch_earnings_dates_yfinance(tickers)
    tickers_with_dates = sum(1 for v in all_dates.values() if v)
    print(f"  Got dates for {tickers_with_dates}/{len(tickers)} tickers")

    pre, post = classify_events(all_dates, names, all_timings)
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

    # Preserve any pre-existing manual timing overrides from the current
    # calendar file — if a user has hand-tagged a ticker/date as BMO/AMC,
    # don't clobber it with a fresh TBD from yfinance.
    existing_timings: dict[tuple[str, str], str] = {}
    try:
        if EARNINGS_CALENDAR.exists():
            with open(EARNINGS_CALENDAR) as _f:
                _prev = json.load(_f)
            for section in ("pre_earnings", "post_earnings"):
                for entry in _prev.get(section, []) or []:
                    t = entry.get("ticker"); d = entry.get("date"); tm = entry.get("timing")
                    if t and d and tm and tm != "TBD":
                        existing_timings[(t, d)] = tm
    except Exception:
        existing_timings = {}

    def _timing_for(entry: dict) -> str:
        override = existing_timings.get((entry["ticker"], entry["earnings_date"]))
        return override or entry.get("timing", "TBD")

    # Update earnings calendar
    calendar = {
        "updated": datetime.now().isoformat(),
        "pre_earnings": [
            {
                "ticker": e["ticker"],
                "company": e["name"],
                "date": e["earnings_date"],
                "days_until": e["days_until"],
                "timing": _timing_for(e),
            }
            for e in pre
        ],
        "post_earnings": [
            {
                "ticker": e["ticker"],
                "company": e["name"],
                "date": e["earnings_date"],
                "days_since": e["days_since"],
                "timing": _timing_for(e),
            }
            for e in post
        ],
    }
    write_json(EARNINGS_CALENDAR, calendar)
    print("  Earnings calendar updated.")

    return pre, post


if __name__ == "__main__":
    run()
