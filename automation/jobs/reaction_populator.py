"""Note-independent post-print reaction populator.

For every entry in ``earnings_calendar.json#post_earnings``, guarantee that
``post_earnings_review.stock_reaction_pct`` is populated using the most accurate
available market data, falling back through a strict precedence chain when the
canonical EOD close is not yet available.

Why this exists
---------------
The dashboard's "post-print" pill is the single most important card-level
signal. The previous backfill path was bolted onto ``sync_earnings_intel``,
which requires a parseable post-earnings note. When the per-ticker pipeline
fails to produce a real note (e.g. ``history_8q`` empty, "context sanity check
failed"), the sync step skips the ticker, no ``post_earnings_review`` block is
written, and the card renders n/a -- even when settled market data is sitting
in yfinance.

This job is deliberately note-independent. Its only input is the calendar's
``post_earnings`` list (the canonical "who reported in the last 14d" set) and
its only output is the populated ``stock_reaction_pct`` (plus a method tag and
the basis date for auditability). It does not touch any other PER field, never
fabricates a number, and never overwrites a higher-precedence value with a
lower-precedence fallback.

Fallback precedence (highest to lowest)
---------------------------------------
1. ``eod_d_plus_1``       -- yfinance daily history close[D+1] vs close[D].
                             This is the canonical, settled reaction.
2. ``intraday_d_plus_1``  -- yfinance info.regularMarketPrice (during the D+1
                             regular session) vs info.regularMarketPreviousClose
                             (= close[D]). Used when D+1 has opened but not
                             closed.
3. ``premarket_d_plus_1`` -- yfinance info.preMarketPrice vs
                             info.regularMarketPreviousClose. The user-mandated
                             pre-market proxy for AMC reporters reading the
                             dashboard before the open on D+1.
4. ``postmarket_d``       -- yfinance info.postMarketPrice vs
                             info.regularMarketPrice. After-hours print
                             reaction on the print date itself for AMC
                             reporters before D+1 pre-market starts.
5. ``finnhub_quote``      -- Finnhub /quote current price (c) vs previous
                             close (pc) when yfinance is silent.

For BMO prints the analogue chain compares to the prior session's close.

Each populated record stores:
    stock_reaction_pct          rounded to 1 decimal, signed
    stock_reaction_basis_date   ISO date of the close used as denominator
    stock_reaction_calc_method  one of the method tags above

Overwrite policy
----------------
Higher-precedence methods always replace lower-precedence values from a prior
run. Method ranks are defined in METHOD_RANK; a value is rewritten when the
freshly-computed method has a STRICTLY LOWER rank index (more authoritative).
This is what migrates a card from premarket_d_plus_1 (today's 7am cron) to
eod_d_plus_1 (after the next-day close lands in history).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CALENDAR_PATH = ROOT / "earnings_calendar.json"
INTEL_PATH = ROOT / "earnings_intel.json"

# Lower index == higher precedence (more authoritative).
METHOD_RANK = {
    "eod_d_plus_1": 0,
    "eod_bmo": 0,            # close[D] vs close[D-1], same authority class
    "intraday_d_plus_1": 1,
    "intraday_bmo": 1,
    "premarket_d_plus_1": 2,
    "premarket_bmo": 2,
    "postmarket_d": 3,
    "finnhub_quote": 4,
    # Legacy method tags from the old backfill -- ranked so the new chain can
    # supersede them on the next pass without re-creating their values.
    "amc_d_plus_1": 0,
    "amc_d_plus_1_live": 1,
    "bmo_d_minus_1": 0,
}


def _finite(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f == 0.0:
        return None
    return f


def _load_json(p: Path) -> dict:
    with p.open() as f:
        return json.load(f)


def _dump_json(p: Path, obj: dict) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(p)


# ----------------------------------------------------------------- yfinance ---

def _yf():
    import yfinance as yf  # noqa: WPS433 - deferred import for fast --help
    return yf


def fetch_history_closes(ticker: str, anchor: date) -> dict[date, float]:
    """Return date->close for [anchor-5d, anchor+5d]; empty dict on failure."""
    yf = _yf()
    start = (anchor - timedelta(days=5)).isoformat()
    end = (anchor + timedelta(days=6)).isoformat()
    closes: dict[date, float] = {}
    try:
        df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
    except Exception:  # noqa: BLE001 - yfinance raises varied network errors
        return closes
    if df is None or df.empty or "Close" not in df.columns:
        return closes
    for idx, row in df.iterrows():
        try:
            d = idx.date()
        except AttributeError:
            try:
                d = datetime.fromisoformat(str(idx)).date()
            except ValueError:
                continue
        c = _finite(row.get("Close"))
        if c is not None:
            closes[d] = c
    return closes


def _ts_to_et_date(ts) -> date | None:
    """Yahoo quote timestamps are UNIX seconds in UTC; convert to a US/Eastern
    calendar date so we can compare against the print's earnings_date.
    Approximation: subtract 4h (EDT). Off by 1h half the year, never enough to
    flip the date for the windows we care about (premarket and AH)."""
    if not ts:
        return None
    try:
        return (datetime.utcfromtimestamp(float(ts)) - timedelta(hours=4)).date()
    except (ValueError, OverflowError, TypeError):
        return None


def fetch_yf_quote(ticker: str) -> dict[str, Any]:
    """Return a snapshot of the yfinance quote fields used by the fallbacks.

    Each price comes paired with a US/Eastern calendar date (when known) so we
    can decide whether the tick belongs to the print session, the post-print
    after-hours session, or the next trading session's pre-market / regular
    window. None denotes "field unavailable".
    """
    yf = _yf()
    tk = yf.Ticker(ticker)
    out: dict[str, Any] = {
        "regular_market_price": None,
        "regular_market_date": None,
        "pre_market_price": None,
        "pre_market_date": None,
        "post_market_price": None,
        "post_market_date": None,
        "regular_market_previous_close": None,
    }
    try:
        info = tk.info or {}
    except Exception:  # noqa: BLE001
        info = {}
    out["regular_market_price"] = _finite(info.get("regularMarketPrice"))
    out["regular_market_date"] = _ts_to_et_date(info.get("regularMarketTime"))
    out["pre_market_price"] = _finite(info.get("preMarketPrice"))
    out["pre_market_date"] = _ts_to_et_date(info.get("preMarketTime"))
    out["post_market_price"] = _finite(info.get("postMarketPrice"))
    out["post_market_date"] = _ts_to_et_date(info.get("postMarketTime"))
    out["regular_market_previous_close"] = _finite(info.get("regularMarketPreviousClose"))
    return out


# ------------------------------------------------------------------- finnhub ---

def fetch_finnhub_quote(ticker: str) -> dict[str, float] | None:
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return None
    try:
        import requests  # noqa: WPS433
    except ImportError:
        return None
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": api_key},
            timeout=8,
        )
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json() or {}
    except ValueError:
        return None
    cur = _finite(d.get("c"))
    prev = _finite(d.get("pc"))
    if cur is None or prev is None:
        return None
    return {"current": cur, "previous_close": prev}


# ---------------------------------------------------------------- computer ---

def _round_signed_pct(numer: float, denom: float) -> float | None:
    if denom == 0:
        return None
    return round((numer - denom) / denom * 100.0, 1)


def compute_reaction(
    ticker: str,
    earnings_date: date,
    timing: str | None,
    today: date,
) -> tuple[float | None, str | None, str | None]:
    """Return (pct, basis_iso_str, method) or (None, None, reason).

    The denominator for an AMC print is close[D] (the print-day's regular close);
    the numerator is any authentic post-print quote. Yahoo's timestamped
    quote fields are how we tell whether a quote belongs to D+1 (pre-market /
    regular) or D (after-hours).
    """
    closes = fetch_history_closes(ticker, earnings_date)
    q = fetch_yf_quote(ticker)
    rmp = q["regular_market_price"]
    rmd = q["regular_market_date"]
    pre = q["pre_market_price"]
    pmd = q["pre_market_date"]
    post = q["post_market_price"]
    pod = q["post_market_date"]
    rmpc = q["regular_market_previous_close"]

    sessions = sorted(closes.keys())

    def prior(d: date) -> date | None:
        xs = [s for s in sessions if s < d]
        return xs[-1] if xs else None

    def nxt(d: date) -> date | None:
        xs = [s for s in sessions if s > d]
        return xs[0] if xs else None

    if timing == "BMO":
        # Denominator = close[D-1]; numerator preference = close[D] or live D price
        denom_date = prior(earnings_date) if earnings_date in closes else None
        denom = closes.get(denom_date) if denom_date else None
        # When close[D] hasn't landed yet but rmpc is yahoo's settled prior close,
        # rmpc is the right denom only if rmd == earnings_date (BMO regular open).
        if denom is None and rmpc is not None and rmd == earnings_date:
            denom = rmpc
            denom_date = earnings_date - timedelta(days=1)

        if denom is None:
            return None, None, "no_baseline_close"

        # 1. Settled close[D]
        if earnings_date in closes:
            p = _round_signed_pct(closes[earnings_date], denom)
            if p is not None:
                return p, denom_date.isoformat(), "eod_bmo"
        # 2. Intraday D regular session
        if rmp is not None and rmd == earnings_date:
            p = _round_signed_pct(rmp, denom)
            if p is not None:
                return p, denom_date.isoformat(), "intraday_bmo"
        # 3. Pre-market D (BMO print, pre-open)
        if pre is not None and pmd == earnings_date:
            p = _round_signed_pct(pre, denom)
            if p is not None:
                return p, denom_date.isoformat(), "premarket_bmo"
    else:
        # AMC / unknown timing
        # Denominator = close[D]. Source order:
        #   a) history close[D]  -- canonical
        #   b) regularMarketPrice when rmd == earnings_date  -- D's settled close
        #      that yahoo's daily history hasn't ingested yet
        denom: float | None = closes.get(earnings_date)
        denom_used_quote = False
        if denom is None and rmp is not None and rmd == earnings_date:
            denom = rmp
            denom_used_quote = True

        if denom is None:
            return None, None, "no_close_d"

        basis_iso = earnings_date.isoformat()

        # 1. Settled close[D+1] from history
        n = nxt(earnings_date)
        if n is not None and not denom_used_quote:
            p = _round_signed_pct(closes[n], denom)
            if p is not None:
                return p, basis_iso, "eod_d_plus_1"
        # 2. Intraday D+1 regular session: rmd > earnings_date
        if rmp is not None and rmd is not None and rmd > earnings_date:
            p = _round_signed_pct(rmp, denom)
            if p is not None:
                return p, basis_iso, "intraday_d_plus_1"
        # 3. Pre-market D+1
        if pre is not None and pmd is not None and pmd > earnings_date:
            p = _round_signed_pct(pre, denom)
            if p is not None:
                return p, basis_iso, "premarket_d_plus_1"
        # 4. After-hours D (same-day post-print before D+1 pre-market starts)
        if post is not None and pod is not None and pod >= earnings_date:
            p = _round_signed_pct(post, denom)
            if p is not None:
                return p, basis_iso, "postmarket_d"

    # ---- 5. Finnhub quote (any timing): current price vs prev close
    fq = fetch_finnhub_quote(ticker)
    if fq is not None:
        p = _round_signed_pct(fq["current"], fq["previous_close"])
        if p is not None:
            return p, earnings_date.isoformat(), "finnhub_quote"

    return None, None, "no_market_data"


# ------------------------------------------------------------------- driver ---

def parse_timing(entry: dict) -> str | None:
    v = entry.get("timing") or entry.get("time_of_day")
    if not isinstance(v, str):
        return None
    v = v.strip().upper()
    return v if v in ("AMC", "BMO") else None


def parse_date(s: Any) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", help="Only process this single ticker.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-age-days", type=int, default=5,
                        help="Skip tickers whose earnings_date is more than N days old.")
    args = parser.parse_args(argv)

    today = date.today()

    if not CALENDAR_PATH.exists():
        print(f"ERROR: {CALENDAR_PATH} not found", file=sys.stderr)
        return 1
    if not INTEL_PATH.exists():
        print(f"ERROR: {INTEL_PATH} not found", file=sys.stderr)
        return 1

    calendar = _load_json(CALENDAR_PATH)
    intel = _load_json(INTEL_PATH)
    intel_tickers = intel.setdefault("tickers", {})

    entries = calendar.get("post_earnings") or []
    if not entries:
        print("No post_earnings entries in calendar; nothing to do.")
        return 0

    populated: list[tuple[str, float, str]] = []
    upgraded: list[tuple[str, float, str, str]] = []  # (ticker, pct, old_method, new_method)
    unchanged: list[str] = []
    still_null: list[tuple[str, str]] = []

    # Pre-pass: idempotent calendar<->intel sync for ALL entries (no age guard).
    # This ensures cal_per.stock_reaction_pct is never null when intel has a value.
    sync_changed = 0
    if not args.dry_run:
        for entry in entries:
            t = entry.get("ticker")
            if not t:
                continue
            cal_per = entry.setdefault("post_earnings_review", {})
            intel_per = (intel_tickers.get(t) or {}).get("post_earnings_review") or {}
            if not intel_per:
                continue
            for fld in ("stock_reaction_pct", "stock_reaction_basis_date",
                        "stock_reaction_calc_method", "price_basis",
                        "price_basis_close", "price_d_plus_1_close"):
                if intel_per.get(fld) is not None and cal_per.get(fld) is None:
                    cal_per[fld] = intel_per[fld]
                    sync_changed += 1
                elif cal_per.get(fld) is not None and intel_per.get(fld) is None:
                    intel_per[fld] = cal_per[fld]
                    sync_changed += 1

    for entry in entries:
        t = entry.get("ticker")
        if not t:
            continue
        if args.ticker and t != args.ticker:
            continue

        ed = parse_date(entry.get("earnings_date") or entry.get("reported_date"))
        if ed is None:
            still_null.append((t, "no_earnings_date"))
            continue
        if (today - ed).days > args.max_age_days:
            continue  # already-settled prints stay as-is unless explicitly targeted
        if ed > today:
            still_null.append((t, "earnings_date_in_future"))
            continue

        timing = parse_timing(entry)
        pct, basis, method = compute_reaction(t, ed, timing, today)

        if pct is None:
            still_null.append((t, method or "unknown"))
            print(f"[null]  {t}: {method}")
            continue

        # Existing values on calendar entry and on intel PER
        cal_per = entry.setdefault("post_earnings_review", {})
        intel_rec = intel_tickers.setdefault(t, {"ticker": t})
        intel_per = intel_rec.setdefault("post_earnings_review", {})

        # Take the higher-precedence of the two existing values as "incumbent"
        existing_method = None
        existing_pct = None
        for src in (cal_per, intel_per):
            m = src.get("stock_reaction_calc_method")
            p = src.get("stock_reaction_pct")
            if m and p is not None and (existing_method is None or
                                         METHOD_RANK.get(m, 99) < METHOD_RANK.get(existing_method, 99)):
                existing_method = m
                existing_pct = p

        new_rank = METHOD_RANK.get(method, 99)
        existing_rank = METHOD_RANK.get(existing_method, 99) if existing_method else 99

        if existing_pct is not None and existing_rank <= new_rank:
            # Idempotent sync: ensure cal_per mirrors intel_per even when no change
            if not args.dry_run:
                for fld in ("stock_reaction_pct", "stock_reaction_basis_date",
                            "stock_reaction_calc_method", "price_basis",
                            "price_basis_close", "price_d_plus_1_close"):
                    if intel_per.get(fld) is not None and cal_per.get(fld) is None:
                        cal_per[fld] = intel_per[fld]
                    elif cal_per.get(fld) is not None and intel_per.get(fld) is None:
                        intel_per[fld] = cal_per[fld]
            unchanged.append(t)
            continue

        timing_label = timing or "UNK"
        if existing_pct is not None:
            upgraded.append((t, pct, existing_method or "none", method))
            print(f"[upgr]  {t}: {existing_pct:+.1f}%/{existing_method} -> {pct:+.1f}%/{method} (timing={timing_label}, basis={basis})")
        else:
            populated.append((t, pct, method))
            print(f"[ok]    {t}: {pct:+.1f}% (timing={timing_label}, basis={basis}, method={method})")

        if not args.dry_run:
            for tgt in (cal_per, intel_per):
                tgt["stock_reaction_pct"] = pct
                tgt["stock_reaction_basis_date"] = basis
                tgt["stock_reaction_calc_method"] = method

    if not args.dry_run and (populated or upgraded or sync_changed):
        _dump_json(CALENDAR_PATH, calendar)
        _dump_json(INTEL_PATH, intel)
        if sync_changed and not populated and not upgraded:
            print(f"[sync]  mirrored {sync_changed} field(s) between calendar and intel")

    print()
    print("=== SUMMARY ===")
    print(f"Newly populated: {len(populated)}")
    print(f"Upgraded:        {len(upgraded)}")
    print(f"Unchanged:       {len(unchanged)}")
    print(f"Still null:      {len(still_null)}")
    if still_null:
        for t, reason in sorted(still_null):
            print(f"   - {t}: {reason}")
    if args.dry_run:
        print("(dry-run -- no files written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
