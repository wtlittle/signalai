#!/usr/bin/env python3
"""Backfill post_earnings_review.stock_reaction_pct from yfinance.

For every ticker in earnings_intel.json whose state == "post_earnings" and whose
post_earnings_review.stock_reaction_pct is null (or missing), compute the actual
post-print stock reaction from authoritative market data (yfinance / Yahoo
Finance) and populate the field.

Reaction definition
--------------------
- BMO (before market open) print on date D:
      reaction = (close[D] - close[D-1 trading day]) / close[D-1 trading day]
- AMC (after market close) print on date D:
      reaction = (close[D+1 trading day] - close[D]) / close[D]
- Unknown timing: default to "next trading day close vs print-date prior close",
  i.e. the 1-day move from the last session at/before the print to the next
  session after it.

Data integrity
--------------
- Values are computed only from yfinance closes. Nothing is ever fabricated.
- If market data cannot be fetched, the earnings date is missing, or the
  required session has not closed yet (print/next session in the future),
  the field is left null and the ticker is logged with a reason.
- Idempotent: existing non-null stock_reaction_pct values are never overwritten.

Auditability
------------
Alongside stock_reaction_pct the script also records (without touching any other
post_earnings_review field):
  - stock_reaction_basis_date  -> the close date used as the denominator
  - stock_reaction_calc_method -> "bmo_d_minus_1" | "amc_d_plus_1"

Usage
-----
    python -m scripts.backfill_post_print_reaction              # backfill nulls
    python -m scripts.backfill_post_print_reaction --ticker S   # single ticker
    python -m scripts.backfill_post_print_reaction --dry-run    # preview only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "earnings_intel.json"
CALENDAR_PATH = ROOT / "earnings_calendar.json"

# yfinance is imported lazily inside fetch_closes so that --help and dry-run
# bookkeeping work even in environments without the package installed.


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def dump_intel(path: Path, data: dict) -> None:
    # earnings_intel.json is 2-space indented, ascii-escaped, no trailing
    # newline. json.dumps(data, indent=2) reproduces that exactly, so the diff
    # is limited to the fields we actually change.
    path.write_text(json.dumps(data, indent=2))


def resolve_earnings_date(rec: dict) -> str | None:
    """Pick the most specific earnings date available for a POST record.

    Preference order matches the spec: reported_date > last_earnings_date, with
    the post_earnings_review.earnings_date as a final fallback (it mirrors the
    print date for POST records).
    """
    per = rec.get("post_earnings_review") or {}
    for field in ("reported_date", "most_recent_earnings_date",
                  "last_earnings_date"):
        val = rec.get(field)
        if val:
            return val
    val = per.get("earnings_date")
    if val:
        return val
    return None


def resolve_timing(ticker: str, rec: dict, calendar_timing: dict) -> str | None:
    """Resolve BMO/AMC timing if known, else None.

    Checks per-record fields first, then the earnings_calendar.json mapping.
    """
    per = rec.get("post_earnings_review") or {}
    for src in (rec, per):
        for field in ("earnings_time", "timing", "report_time", "print_timing"):
            val = src.get(field)
            if isinstance(val, str) and val.strip().upper() in ("BMO", "AMC"):
                return val.strip().upper()
    cal = calendar_timing.get(ticker)
    if isinstance(cal, str) and cal.strip().upper() in ("BMO", "AMC"):
        return cal.strip().upper()
    return None


def build_calendar_timing(calendar: dict) -> dict:
    out: dict[str, str] = {}
    for entry in calendar.get("post_earnings", []) or []:
        t = entry.get("ticker")
        timing = entry.get("timing")
        if t and isinstance(timing, str):
            out[t] = timing.strip().upper()
    return out


def fetch_closes(ticker: str, earnings_date: date):
    """Return a date->close dict over [earnings_date-5d, earnings_date+5d].

    Returns None on any fetch failure. Never fabricates data.
    """
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(f"yfinance not available: {exc}")

    start = (earnings_date - timedelta(days=5)).isoformat()
    # yfinance end is exclusive; pad to capture earnings_date + 5 calendar days.
    end = (earnings_date + timedelta(days=6)).isoformat()
    try:
        df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
    except Exception:  # noqa: BLE001 - yfinance raises many network error types
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    closes: dict[date, float] = {}
    for idx, row in df.iterrows():
        try:
            d = idx.date()
        except AttributeError:
            d = datetime.fromisoformat(str(idx)).date()
        close = row.get("Close")
        if close is None:
            continue
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if close == close:  # filter NaN
            closes[d] = close
    return closes or None


def compute_reaction(ticker: str, earnings_date: date, timing: str | None,
                     closes: dict[date, float], today: date):
    """Compute the signed post-print reaction.

    Returns (pct, basis_date, calc_method) on success, else (None, None, reason).
    pct is rounded to 1 decimal, signed (positive = up).
    """
    sessions = sorted(closes.keys())
    if not sessions:
        return None, None, "no_market_data"

    def prior_session(d: date):
        cands = [s for s in sessions if s < d]
        return cands[-1] if cands else None

    def next_session(d: date):
        cands = [s for s in sessions if s > d]
        return cands[0] if cands else None

    def session_at_or_before(d: date):
        cands = [s for s in sessions if s <= d]
        return cands[-1] if cands else None

    if timing == "BMO":
        # close[D] vs close[D-1]
        if earnings_date not in closes:
            return None, None, "no_close_on_print_date"
        prior = prior_session(earnings_date)
        if prior is None:
            return None, None, "no_prior_close"
        denom = closes[prior]
        numer = closes[earnings_date]
        basis = prior
        method = "bmo_d_minus_1"
    elif timing == "AMC":
        # close[D+1] vs close[D]
        if earnings_date not in closes:
            return None, None, "no_close_on_print_date"
        nxt = next_session(earnings_date)
        if nxt is None:
            return None, None, "next_session_not_closed_yet"
        denom = closes[earnings_date]
        numer = closes[nxt]
        basis = earnings_date
        method = "amc_d_plus_1"
    else:
        # Unknown timing: next-trading-day close vs print-date prior close.
        # "prior close" = last session at or before the print date; the move is
        # measured to the next session after it. Modelled as AMC d+1.
        base = session_at_or_before(earnings_date)
        if base is None:
            return None, None, "no_close_at_or_before_print_date"
        nxt = next_session(base)
        if nxt is None:
            return None, None, "next_session_not_closed_yet"
        denom = closes[base]
        numer = closes[nxt]
        basis = base
        method = "amc_d_plus_1"

    if denom == 0:
        return None, None, "zero_denominator"

    pct = round((numer - denom) / denom * 100.0, 1)
    return pct, basis.isoformat(), method


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill stock_reaction_pct for POST tickers from yfinance.")
    parser.add_argument("--ticker", help="Only process this single ticker.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing the JSON file.")
    args = parser.parse_args(argv)

    today = date.today()
    intel = load_json(INTEL_PATH)
    tickers = intel.get("tickers", {})

    calendar_timing: dict[str, str] = {}
    if CALENDAR_PATH.exists():
        try:
            calendar_timing = build_calendar_timing(load_json(CALENDAR_PATH))
        except (json.JSONDecodeError, OSError):
            calendar_timing = {}

    post_tickers = [t for t, v in tickers.items()
                    if (v or {}).get("state") == "post_earnings"]
    if args.ticker:
        if args.ticker not in tickers:
            print(f"ERROR: ticker {args.ticker} not found in earnings_intel.json")
            return 1
        target = [args.ticker]
    else:
        target = post_tickers

    total_post = len(post_tickers)
    null_before: list[str] = []
    populated: list[tuple[str, float, str, str]] = []
    skipped_existing: list[str] = []
    still_null: list[tuple[str, str]] = []

    for t in target:
        rec = tickers.get(t) or {}
        if rec.get("state") != "post_earnings":
            print(f"[skip] {t}: state != post_earnings ({rec.get('state')})")
            continue
        per = rec.setdefault("post_earnings_review", {})
        current = per.get("stock_reaction_pct")

        if current is not None:
            skipped_existing.append(t)
            continue

        null_before.append(t)

        ed_raw = resolve_earnings_date(rec)
        if not ed_raw:
            still_null.append((t, "missing_earnings_date"))
            print(f"[null] {t}: missing earnings date")
            continue
        try:
            earnings_date = date.fromisoformat(str(ed_raw)[:10])
        except ValueError:
            still_null.append((t, f"unparseable_earnings_date:{ed_raw}"))
            print(f"[null] {t}: unparseable earnings date {ed_raw!r}")
            continue

        if earnings_date > today:
            still_null.append((t, f"earnings_date_in_future:{earnings_date}"))
            print(f"[null] {t}: earnings date {earnings_date} is in the future")
            continue

        timing = resolve_timing(t, rec, calendar_timing)

        try:
            closes = fetch_closes(t, earnings_date)
        except RuntimeError as exc:
            still_null.append((t, f"yfinance_unavailable:{exc}"))
            print(f"[null] {t}: {exc}")
            continue

        if not closes:
            still_null.append((t, "yfinance_fetch_failed"))
            print(f"[null] {t}: yfinance returned no usable closes")
            continue

        pct, basis, method = compute_reaction(t, earnings_date, timing,
                                               closes, today)
        if pct is None:
            still_null.append((t, method or "compute_failed"))
            print(f"[null] {t}: {method}")
            continue

        timing_label = timing or "UNKNOWN->next_day"
        print(f"[ok]   {t}: {pct:+.1f}% (timing={timing_label}, "
              f"basis={basis}, method={method})")
        populated.append((t, pct, basis, method))

        if not args.dry_run:
            per["stock_reaction_pct"] = pct
            per["stock_reaction_basis_date"] = basis
            per["stock_reaction_calc_method"] = method

    if populated and not args.dry_run:
        dump_intel(INTEL_PATH, intel)

    # ------------------------------------------------------------------ summary
    print("\n=== SUMMARY ===")
    print(f"Total POST tickers:        {total_post}")
    print(f"Null before (in scope):    {len(null_before)}")
    print(f"Populated this run:        {len(populated)}")
    print(f"Skipped (already set):     {len(skipped_existing)}")
    print(f"Still null (with reasons): {len(still_null)}")
    if still_null:
        for t, reason in sorted(still_null):
            print(f"    - {t}: {reason}")
    if args.dry_run:
        print("\n(dry-run: no changes written)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
