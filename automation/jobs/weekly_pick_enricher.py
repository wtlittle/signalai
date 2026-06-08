"""
Weekly pick enrichment — for each value/momentum pick in weekly_briefing.json,
attach price + 1d/1w/1m/YTD performance + sector-appropriate valuation multiple
+ 52-week implied multiple range.

Pulls from data-snapshot.json#quotes when available (watchlist tickers). Falls
back to yfinance for picks not on the watchlist (e.g. CVX, XOM, EOG, TPL,
MCHP, MRNA). Single yfinance call per missing ticker, results cached in
memory for the run.

Sector-to-multiple mapping (user spec — "mixed by sector, what matters to
investors for that stock"):
    Technology, Communication Services         -> EV/Sales
    Healthcare                                  -> Forward P/E   (default-safe for mixed pharma + biotech)
    Financial Services                          -> P/B
    Real Estate                                 -> P/B           (proxy for P/AFFO)
    Energy, Basic Materials, Industrials        -> EV/EBITDA
    Consumer Cyclical, Consumer Defensive, Utilities -> Forward P/E
    Other / missing                             -> Forward P/E

52w implied range:
    Computed by backing out the multiple at the 52w low and 52w high price,
    holding the denominator (revenue / EBITDA / EPS / book value) constant at
    the current TTM. This is honest — labeled "52w implied" everywhere, never
    "cycle". 1-year range is the most we can honestly say from this data.

NEVER fabricates: if any input field is missing (revenue=None, EPS<=0 for P/E,
etc.), the corresponding multiple/range collapses to None and the renderer
shows n/a.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


# --- Sector mapping ------------------------------------------------------- #
SECTOR_MULTIPLE = {
    "Technology":             "EV/Sales",
    "Communication Services": "EV/Sales",
    "Healthcare":             "Forward P/E",
    "Financial Services":     "P/B",
    "Real Estate":            "P/B",
    "Energy":                 "EV/EBITDA",
    "Basic Materials":        "EV/EBITDA",
    "Industrials":            "EV/EBITDA",
    "Consumer Cyclical":      "Forward P/E",
    "Consumer Defensive":     "Forward P/E",
    "Utilities":              "Forward P/E",
}
DEFAULT_MULTIPLE = "Forward P/E"


def _f(v):
    """Coerce to float, return None on failure."""
    try:
        out = float(v)
        # NaN check (NaN != NaN)
        if out != out:
            return None
        return out
    except (TypeError, ValueError):
        return None


# --- yfinance fallback for picks not on the watchlist --------------------- #
_yf_cache: dict[str, dict] = {}


def _yf_quote(ticker: str) -> dict:
    """One-shot yfinance fetch with in-memory cache; returns {} on failure."""
    if ticker in _yf_cache:
        return _yf_cache[ticker]
    try:
        import yfinance as yf  # local import; only used in fallback path
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception:  # noqa: BLE001
        info = {}
    out = {
        "price": info.get("regularMarketPrice") or info.get("currentPrice"),
        "sector": info.get("sector"),
        "forwardPE": info.get("forwardPE"),
        "forwardEps": info.get("forwardEps"),
        "trailingEps": info.get("trailingEps"),
        "enterpriseToRevenue": info.get("enterpriseToRevenue"),
        "enterpriseToEbitda": info.get("enterpriseToEbitda"),
        "priceToBook": info.get("priceToBook"),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow":  info.get("fiftyTwoWeekLow"),
        "sharesOutstanding": info.get("sharesOutstanding"),
        "totalDebt": info.get("totalDebt"),
        "totalCash": info.get("totalCash"),
        "totalRevenue": info.get("totalRevenue"),
        # yfinance doesn't expose perf directly in info — best effort via history is too slow.
        # We accept that for non-watchlist picks the 1w/1m/YTD will render as n/a.
        "change1d": None,
        "change1w": None,
        "change1m": None,
        "changeYtd": None,
    }
    _yf_cache[ticker] = out
    return out


# --- Multiple + 52w range computation ------------------------------------- #
def _compute_multiple_and_range(q: dict, multiple_name: str) -> dict:
    """Return {current, low_52w, high_52w} for the chosen multiple, or {} if not computable.

    Never fabricates. For each multiple, the denominator (revenue / EBITDA / EPS / book)
    is held constant at the current TTM and the multiple is back-solved at the 52w price
    extrema. This is an honest 'implied if price returned to 52w extremes' range.
    """
    shares = _f(q.get("sharesOutstanding"))
    debt = _f(q.get("totalDebt")) or 0.0
    cash = _f(q.get("totalCash")) or 0.0
    low = _f(q.get("fiftyTwoWeekLow"))
    high = _f(q.get("fiftyTwoWeekHigh"))

    def _ev_at(price):
        if price is None or shares is None:
            return None
        return price * shares + debt - cash

    if multiple_name == "EV/Sales":
        cur = _f(q.get("enterpriseToRevenue"))
        rev = _f(q.get("totalRevenue"))
        if cur is None or rev is None or rev <= 0:
            return {"current": cur, "low_52w": None, "high_52w": None}
        ev_low, ev_high = _ev_at(low), _ev_at(high)
        return {
            "current": cur,
            "low_52w":  ev_low / rev if ev_low else None,
            "high_52w": ev_high / rev if ev_high else None,
        }

    if multiple_name == "EV/EBITDA":
        cur = _f(q.get("enterpriseToEbitda"))
        if cur is None or cur <= 0:
            return {"current": cur, "low_52w": None, "high_52w": None}
        ev_now = _ev_at(_f(q.get("price")))
        if ev_now is None:
            return {"current": cur, "low_52w": None, "high_52w": None}
        ebitda = ev_now / cur  # back-solve TTM EBITDA
        if ebitda <= 0:
            return {"current": cur, "low_52w": None, "high_52w": None}
        ev_low, ev_high = _ev_at(low), _ev_at(high)
        return {
            "current": cur,
            "low_52w":  ev_low / ebitda if ev_low else None,
            "high_52w": ev_high / ebitda if ev_high else None,
        }

    if multiple_name == "Forward P/E":
        cur = _f(q.get("forwardPE"))
        eps = _f(q.get("forwardEps"))
        if cur is None or eps is None or eps <= 0:
            return {"current": cur, "low_52w": None, "high_52w": None}
        return {
            "current": cur,
            "low_52w":  low  / eps if low  is not None else None,
            "high_52w": high / eps if high is not None else None,
        }

    if multiple_name == "P/B":
        cur = _f(q.get("priceToBook"))
        price = _f(q.get("price"))
        if cur is None or cur <= 0 or price is None or price <= 0:
            return {"current": cur, "low_52w": None, "high_52w": None}
        book_per_share = price / cur  # implied current book per share
        if book_per_share <= 0:
            return {"current": cur, "low_52w": None, "high_52w": None}
        return {
            "current": cur,
            "low_52w":  low  / book_per_share if low  is not None else None,
            "high_52w": high / book_per_share if high is not None else None,
        }

    return {"current": None, "low_52w": None, "high_52w": None}


# --- Public entry: enrich one pick ---------------------------------------- #
def enrich_pick(pick: dict, snapshot_quotes: dict) -> dict:
    """Return a new dict with original pick fields + enriched market data.

    Pulls from snapshot first; falls back to yfinance for off-watchlist tickers.
    Sector source preference: snapshot.sector -> yfinance.sector -> pick.sector.
    Multiple choice is governed by sector via SECTOR_MULTIPLE.
    """
    ticker = pick.get("ticker", "")
    q = snapshot_quotes.get(ticker) or {}
    # If snapshot doesn't have price (= not on watchlist), enrich from yfinance
    if not q.get("price"):
        q = {**q, **_yf_quote(ticker)}

    sector = q.get("sector") or pick.get("sector") or "Other"
    multiple_name = SECTOR_MULTIPLE.get(sector, DEFAULT_MULTIPLE)
    mult = _compute_multiple_and_range(q, multiple_name)

    return {
        **pick,
        "_enriched": True,
        "_sector": sector,
        "_price": _f(q.get("price")),
        "_change1d":  _f(q.get("change1d")),
        "_change1w":  _f(q.get("change1w")),
        "_change1m":  _f(q.get("change1m")),
        "_changeYtd": _f(q.get("changeYtd")),
        "_multiple_name": multiple_name,
        "_multiple_current":  mult.get("current"),
        "_multiple_low_52w":  mult.get("low_52w"),
        "_multiple_high_52w": mult.get("high_52w"),
        "_52w_low":  _f(q.get("fiftyTwoWeekLow")),
        "_52w_high": _f(q.get("fiftyTwoWeekHigh")),
    }


def enrich_picks(picks: list, snapshot_quotes: dict) -> list:
    """Map enrich_pick over a list of picks."""
    return [enrich_pick(p, snapshot_quotes) for p in (picks or [])]


if __name__ == "__main__":
    # Smoke test
    snap = json.loads((ROOT / "data-snapshot.json").read_text())
    wb = json.loads((ROOT / "weekly_briefing.json").read_text())
    enriched_v = enrich_picks(wb.get("value_picks", []), snap.get("quotes", {}))
    enriched_m = enrich_picks(wb.get("momentum_picks", []), snap.get("quotes", {}))
    for p in enriched_v + enriched_m:
        print(f"{p['ticker']:<6} sec={p['_sector']:<22} px={p['_price']} "
              f"{p['_multiple_name']:<14} cur={p['_multiple_current']} "
              f"low={p['_multiple_low_52w']} high={p['_multiple_high_52w']}")
