"""SVG earnings-annotated price chart with 4-series overlay for drilldown notes.

Public API
----------
render_earnings_annotated_chart(ticker, earnings_events, sector=None) -> str
    Returns a self-contained inline SVG string (no JS, no external deps).
    The SVG has class="earnings-chart" on the root element so the validator
    can confirm its presence.

Design
------
- 800x340 viewBox, responsive width (width="100%" preserveAspectRatio).
- 4 price series, all rebased to 100 at the start of the 2-year window
  so they compare on a % return basis ("Indexed, start=100").
    1. Ticker      — slate-100  (#f1f5f9)  2 px  (thickest / brightest)
    2. Sector ETF  — amber-400  (#fbbf24)  1.5 px
    3. QQQ         — sky-400    (#38bdf8)  1.5 px
    4. SPY         — violet-400 (#a78bfa)  1.5 px
- Small legend panel in top-right corner (colour swatch + label).
- Vertical earnings marker at each date on the ticker line:
    green (#22c55e) when reaction_pct >= 0, red (#ef4444) otherwise.
    Label: "MM/DD: +X.X%" rotated above the chart area.
- 5 horizontal gridlines, linear Y-axis, label "Indexed (start=100)".
- Monthly X-axis tick labels rotated 30° for fit.
- Title: "<TICKER> 2-year performance vs. <SECTOR_ETF> / QQQ / SPY".
- Placeholder HTML returned when ticker price data <30 points or fails.

Sector → ETF mapping (GICS-aligned, SPDR Select Sector ETFs)
-------------------------------------------------------------
Technology          → XLK
Communication Svc   → XLC
Financials          → XLF
Healthcare          → XLV
Energy              → XLE
Industrials         → XLI
Consumer Disc       → XLY
Consumer Staples    → XLP
Utilities           → XLU
Materials           → XLB
Real Estate         → XLRE

Cache
-----
Per-symbol daily OHLC cached at data/cache/<SYMBOL>_price_2y.json with a
24-hour TTL.  SPY/QQQ/sector ETFs share the same cache files across all 123
tickers, so each is fetched at most once per day regardless of how many
drilldowns are regenerated.

Doctests  (python -m doctest drilldown_chart.py -v)
--------------------------------------------------------
>>> from automation.jobs.drilldown_chart import render_earnings_annotated_chart

>>> # (b) zero events — chart renders, no annotation elements expected
>>> import automation.jobs.drilldown_chart as _m
>>> _synthetic = [{"date": f"2024-{(i//22)+1:02d}-{(i%22)+1:02d}", "close": 100.0 + i} for i in range(60)]
>>> _orig = _m._fetch_price_series
>>> _m._fetch_price_series = lambda t: _synthetic
>>> svg = render_earnings_annotated_chart('NET', [])
>>> 'class="earnings-chart"' in svg
True
>>> 'stroke-dasharray="4,3"' not in svg
True
>>> _m._fetch_price_series = _orig

>>> # (c) yfinance fetch failure -> placeholder
>>> _m._fetch_price_series = lambda t: []
>>> out = render_earnings_annotated_chart('FAIL', [{'date': '2025-01-01', 'reaction_pct': 5.0}])
>>> 'Chart data unavailable' in out
True
>>> _m._fetch_price_series = _orig

>>> # (d) event outside price window — skips gracefully, SVG still renders
>>> _m._fetch_price_series = lambda t: _synthetic
>>> svg = render_earnings_annotated_chart('NET', [{'date': '2000-01-01', 'reaction_pct': 10.0}])
>>> 'class="earnings-chart"' in svg
True
>>> 'stroke-dasharray="4,3"' not in svg
True
>>> _m._fetch_price_series = _orig
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[2]
_CACHE_DIR = ROOT / "data" / "cache"
_CACHE_TTL_SECS = 86400  # 24 h

# ---------------------------------------------------------------------------
# Sector → ETF map  (GICS / SPDR Select Sectors)
# ---------------------------------------------------------------------------
_SECTOR_ETF: dict[str, str] = {
    "Technology":              "XLK",
    "Communication Services":  "XLC",
    "Communications":          "XLC",
    "Financials":              "XLF",
    "Financial Services":      "XLF",
    "Healthcare":              "XLV",
    "Health Care":             "XLV",
    "Energy":                  "XLE",
    "Industrials":             "XLI",
    "Consumer Discretionary":  "XLY",
    "Consumer Cyclical":       "XLY",
    "Consumer Staples":        "XLP",
    "Consumer Defensive":      "XLP",
    "Utilities":               "XLU",
    "Basic Materials":         "XLB",
    "Materials":               "XLB",
    "Real Estate":             "XLRE",
}

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
_COL_TICKER = "#f1f5f9"    # slate-100   — ticker line
_COL_SECTOR = "#fbbf24"    # amber-400   — sector ETF
_COL_QQQ    = "#38bdf8"    # sky-400     — QQQ
_COL_SPY    = "#a78bfa"    # violet-400  — SPY
_COL_GREEN  = "#22c55e"
_COL_RED    = "#ef4444"
_COL_GRID   = "#64748b"    # slate-500
_COL_LABEL  = "#cbd5e1"    # slate-300
_COL_TITLE  = "#e2e8f0"    # slate-200
_COL_LEGEND_BG = "#1e293b" # slate-800, semi-transparent

# ---------------------------------------------------------------------------
# Layout  (SVG user units, viewBox 800 x 340)
# ---------------------------------------------------------------------------
_VB_W = 800
_VB_H = 340
_PAD_LEFT   = 64   # Y-axis labels
_PAD_RIGHT  = 20
_PAD_TOP    = 38   # title + legend
_PAD_BOTTOM = 50   # X-axis labels
_PLOT_W = _VB_W - _PAD_LEFT - _PAD_RIGHT
_PLOT_H = _VB_H - _PAD_TOP  - _PAD_BOTTOM

_PLACEHOLDER = (
    '<p class="chart-placeholder" style="'
    'font-size:12px;color:#94a3b8;padding:12px 0;margin:0;">'
    "Chart data unavailable \u2014 see Earnings Intel popup for per-print detail"
    "</p>"
)


# ---------------------------------------------------------------------------
# Price-series fetch + per-symbol 24-h cache
# ---------------------------------------------------------------------------

def _cache_path(symbol: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{symbol.upper()}_price_2y.json"


def _cache_load(symbol: str) -> list[dict] | None:
    p = _cache_path(symbol)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("fetched_at", 0) < _CACHE_TTL_SECS:
            return data.get("series")
    except (ValueError, KeyError, OSError):
        pass
    return None


def _cache_save(symbol: str, series: list[dict]) -> None:
    p = _cache_path(symbol)
    try:
        p.write_text(
            json.dumps({"fetched_at": time.time(), "series": series}, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"  [WARN] drilldown_chart: could not write cache for {symbol}: {exc}")


def _fetch_price_series(symbol: str) -> list[dict]:
    """Fetch 2-year daily close prices via yfinance. Returns [] on failure.

    Each item: {"date": "YYYY-MM-DD", "close": float}
    Shares cache with other callers (SPY/QQQ/sector ETFs reuse across tickers).
    """
    symbol = symbol.upper().strip()
    cached = _cache_load(symbol)
    if cached is not None:
        return cached

    try:
        import yfinance as yf
    except ImportError:
        print("  [WARN] drilldown_chart: yfinance not installed")
        return []

    try:
        hist = yf.Ticker(symbol).history(period="2y", interval="1d")
    except Exception as exc:
        print(f"  [WARN] drilldown_chart: yfinance fetch failed for {symbol}: {exc}")
        return []

    if hist is None or getattr(hist, "empty", True):
        return []

    series: list[dict] = []
    for ts, row in hist.iterrows():
        close = row.get("Close")
        if close is None:
            continue
        try:
            c = float(close)
        except (TypeError, ValueError):
            continue
        if math.isnan(c):
            continue
        series.append({"date": str(ts)[:10], "close": round(c, 4)})

    series.sort(key=lambda x: x["date"])
    _cache_save(symbol, series)
    return series


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _rebase(series: list[dict], anchor_date: str) -> list[dict]:
    """Rebase a price series so the value on or after anchor_date = 100.

    Returns a new list {"date", "close"} with rebased closes, aligned to
    dates present in the series.  If anchor_date is before the first date,
    uses the first available date.
    """
    if not series:
        return []
    base_val: float | None = None
    for p in series:
        if p["date"] >= anchor_date:
            base_val = p["close"]
            break
    if base_val is None or base_val == 0:
        base_val = series[0]["close"]
    if base_val == 0:
        return series[:]
    return [{"date": p["date"], "close": round(p["close"] / base_val * 100, 4)}
            for p in series]


def _align_to_dates(series: list[dict], target_dates: list[str]) -> list[float | None]:
    """Map series onto target_dates using forward-fill for missing days."""
    date_map: dict[str, float] = {p["date"]: p["close"] for p in series}
    result: list[float | None] = []
    last: float | None = None
    for d in target_dates:
        v = date_map.get(d, last)
        last = v
        result.append(v)
    return result


def _nice_y_ticks(ymin: float, ymax: float, n: int = 5) -> list[float]:
    span = ymax - ymin
    if span == 0:
        return [round(ymin, 2)] * n
    raw_step = span / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw_step))
    step = mag
    for factor in (1, 2, 2.5, 5, 10):
        step = mag * factor
        if step >= raw_step:
            break
    lo = math.floor(ymin / step) * step
    ticks = []
    v = lo
    while len(ticks) < n + 4:
        if v >= ymin - 1e-9:
            ticks.append(round(v, 6))
        v += step
        if v > ymax + step:
            break
    return ticks[:n]


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_earnings_annotated_chart(
    ticker: str,
    earnings_events: list[dict],
    sector: str | None = None,
) -> str:
    """Build an inline SVG 4-series annotated chart.

    Parameters
    ----------
    ticker:
        Primary stock ticker (e.g. "NET").
    earnings_events:
        List of dicts with keys:
            date (str "YYYY-MM-DD") — earnings report date
            reaction_pct (float | None) — 1-day stock reaction in percent
    sector:
        Optional GICS sector string (e.g. "Technology"). Used to derive the
        sector ETF overlay. If None or unmapped, the sector overlay is skipped
        and only ticker + QQQ + SPY are shown.

    Returns
    -------
    str
        Inline SVG markup, or the plain-HTML placeholder on data failure.
    """
    ticker = ticker.strip().upper()

    # ---- Fetch all price series -------------------------------------------
    ticker_series = _fetch_price_series(ticker)
    if len(ticker_series) < 30:
        return _PLACEHOLDER

    # Determine sector ETF (may be None)
    sector_etf: str | None = None
    if sector:
        sector_etf = _SECTOR_ETF.get(sector)
    if sector_etf is None and sector:
        # Try partial / case-insensitive match
        sl = sector.lower()
        for k, v in _SECTOR_ETF.items():
            if sl in k.lower() or k.lower() in sl:
                sector_etf = v
                break

    spy_series  = _fetch_price_series("SPY")
    qqq_series  = _fetch_price_series("QQQ")
    sec_series  = _fetch_price_series(sector_etf) if sector_etf else []

    # ---- Establish shared date spine (from ticker series) -----------------
    anchor_date = ticker_series[0]["date"]
    dates = [p["date"] for p in ticker_series]
    n = len(dates)

    # ---- Rebase to 100 at anchor ------------------------------------------
    tk_rebased  = _rebase(ticker_series, anchor_date)
    spy_rebased = _rebase(spy_series,    anchor_date) if spy_series  else []
    qqq_rebased = _rebase(qqq_series,    anchor_date) if qqq_series  else []
    sec_rebased = _rebase(sec_series,    anchor_date) if sec_series  else []

    # ---- Align overlays onto ticker's date spine --------------------------
    tk_vals  = [p["close"] for p in tk_rebased]
    spy_vals = _align_to_dates(spy_rebased, dates) if spy_rebased else [None] * n
    qqq_vals = _align_to_dates(qqq_rebased, dates) if qqq_rebased else [None] * n
    sec_vals = _align_to_dates(sec_rebased, dates) if sec_rebased else [None] * n

    # ---- Y-axis bounds (union of all valid values) -------------------------
    all_vals = tk_vals[:]
    for lst in (spy_vals, qqq_vals, sec_vals):
        all_vals += [v for v in lst if v is not None]
    ylo_raw = min(all_vals)
    yhi_raw = max(all_vals)
    pad = (yhi_raw - ylo_raw) * 0.08 or 5.0
    ylo = ylo_raw - pad
    yhi = yhi_raw + pad

    # ---- Coordinate helpers -----------------------------------------------
    def px(idx: int) -> float:
        return _PAD_LEFT + (idx / max(n - 1, 1)) * _PLOT_W

    def py(val: float) -> float:
        frac = (val - ylo) / (yhi - ylo) if (yhi - ylo) != 0 else 0.5
        return _PAD_TOP + _PLOT_H - frac * _PLOT_H

    ax_x1 = _PAD_LEFT
    ax_y1 = _PAD_TOP
    ax_x2 = _PAD_LEFT + _PLOT_W
    ax_y2 = _PAD_TOP + _PLOT_H

    # ---- Polyline builder --------------------------------------------------
    def _polyline(vals: list[float | None], color: str, width: float,
                  css_class: str = "") -> str:
        segments: list[str] = []
        current: list[str] = []
        for i, v in enumerate(vals):
            if v is None:
                if current:
                    segments.append(" ".join(current))
                    current = []
            else:
                current.append(f"{px(i):.1f},{py(v):.1f}")
        if current:
            segments.append(" ".join(current))
        parts = []
        cls_attr = f' class="{_esc(css_class)}"' if css_class else ""
        for seg in segments:
            parts.append(
                f'  <polyline{cls_attr} points="{seg}" '
                f'fill="none" stroke="{color}" stroke-width="{width}" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )
        return "\n".join(parts)

    # ---- Earnings markers --------------------------------------------------
    date_to_idx: dict[str, int] = {d: i for i, d in enumerate(dates)}

    markers: list[dict] = []
    for ev in earnings_events:
        date_str = (ev.get("date") or "")[:10]
        if not date_str:
            continue
        reaction = ev.get("reaction_pct")
        idx = date_to_idx.get(date_str)
        if idx is None:
            # Forward-search for nearest trading day, but only within the
            # actual price window bounds.  Events before the first date or
            # after the last date are skipped.
            if date_str < dates[0] or date_str > dates[-1]:
                continue  # outside price window — skip gracefully
            for candidate in sorted(date_to_idx.keys()):
                if candidate >= date_str:
                    idx = date_to_idx[candidate]
                    break
        if idx is None:
            continue  # still unresolved — skip
        color = _COL_GREEN if (reaction is not None and reaction >= 0) else _COL_RED
        if reaction is not None:
            sign = "+" if reaction >= 0 else ""
            label = f"{date_str[5:]}: {sign}{reaction:.1f}%"
        else:
            label = date_str[5:]
        markers.append({
            "idx": idx,
            "x": px(idx),
            "y": py(tk_vals[idx]),
            "color": color,
            "label": label,
        })

    # ---- X-axis monthly ticks ---------------------------------------------
    x_ticks: list[tuple[float, str]] = []
    seen: set[str] = set()
    for i, d in enumerate(dates):
        mk = d[:7]
        if mk not in seen:
            seen.add(mk)
            try:
                lbl = datetime.strptime(d, "%Y-%m-%d").strftime("%-m/%y")
            except ValueError:
                lbl = d[:7]
            x_ticks.append((px(i), lbl))
    if len(x_ticks) > 16:
        x_ticks = x_ticks[::2]

    # ---- Y-axis ticks -----------------------------------------------------
    y_ticks = _nice_y_ticks(ylo, yhi, 5)
    y_ticks = [t for t in y_ticks if ylo - 1e-9 <= t <= yhi + 1e-9]
    if len(y_ticks) < 2:
        mid = (ylo + yhi) / 2
        y_ticks = [ylo, mid, yhi]

    # ---- Legend entries ---------------------------------------------------
    legend_entries: list[tuple[str, str]] = [
        (ticker, _COL_TICKER),
    ]
    if sec_rebased:
        legend_entries.append((sector_etf, _COL_SECTOR))  # type: ignore[arg-type]
    legend_entries.append(("QQQ", _COL_QQQ))
    legend_entries.append(("SPY", _COL_SPY))

    # ---- Title ------------------------------------------------------------
    if sector_etf and sec_rebased:
        overlay_str = f"{sector_etf} / QQQ / SPY"
    else:
        overlay_str = "QQQ / SPY"
    title_text = f"{ticker} 2-year performance vs. {overlay_str}"

    # ---- SVG assembly -----------------------------------------------------
    lines: list[str] = []
    a = lines.append

    a(f'<svg class="earnings-chart" '
      f'xmlns="http://www.w3.org/2000/svg" '
      f'viewBox="0 0 {_VB_W} {_VB_H}" '
      f'width="100%" '
      f'preserveAspectRatio="xMidYMid meet" '
      f'style="display:block;max-width:{_VB_W}px;background:transparent;" '
      f'aria-label="{_esc(title_text)}">')

    # Title
    a(f'  <text x="{_PAD_LEFT}" y="24" '
      f'font-family="ui-monospace,monospace" font-size="11" font-weight="600" '
      f'fill="{_COL_TITLE}">{_esc(title_text)}</text>')

    # Gridlines + Y labels
    for tick in y_ticks:
        yv = py(tick)
        a(f'  <line x1="{ax_x1}" y1="{yv:.1f}" x2="{ax_x2}" y2="{yv:.1f}" '
          f'stroke="{_COL_GRID}" stroke-width="0.5" stroke-dasharray="3,4"/>')
        a(f'  <text x="{ax_x1 - 4}" y="{yv + 3.5:.1f}" '
          f'text-anchor="end" font-family="ui-monospace,monospace" font-size="9" '
          f'fill="{_COL_LABEL}">{tick:.1f}</text>')

    # Y-axis label ("Indexed (start=100)")
    mid_y = ax_y1 + _PLOT_H / 2
    a(f'  <text transform="rotate(-90,12,{mid_y:.1f})" '
      f'x="12" y="{mid_y:.1f}" '
      f'text-anchor="middle" font-family="ui-monospace,monospace" font-size="9" '
      f'fill="{_COL_LABEL}">Indexed (start=100)</text>')

    # Axes
    a(f'  <line x1="{ax_x1}" y1="{ax_y1}" x2="{ax_x1}" y2="{ax_y2}" '
      f'stroke="{_COL_GRID}" stroke-width="0.8"/>')
    a(f'  <line x1="{ax_x1}" y1="{ax_y2}" x2="{ax_x2}" y2="{ax_y2}" '
      f'stroke="{_COL_GRID}" stroke-width="0.8"/>')

    # X-axis tick labels
    for xv, lbl in x_ticks:
        ybase = ax_y2 + 12
        a(f'  <text transform="rotate(-30,{xv:.1f},{ybase})" '
          f'x="{xv:.1f}" y="{ybase}" '
          f'text-anchor="end" font-family="ui-monospace,monospace" font-size="8" '
          f'fill="{_COL_LABEL}">{_esc(lbl)}</text>')

    # Overlay lines (draw behind ticker)
    if sec_rebased:
        a(_polyline(sec_vals, _COL_SECTOR, 1.5, "overlay-sector"))
    a(_polyline(qqq_vals, _COL_QQQ, 1.5, "overlay-qqq"))
    a(_polyline(spy_vals, _COL_SPY, 1.5, "overlay-spy"))

    # Ticker line (front, thickest)
    a(_polyline(tk_vals, _COL_TICKER, 2.0, "line-ticker"))

    # Earnings marker vertical lines + dots + labels
    for mk in markers:
        xv = mk["x"]
        col = mk["color"]
        a(f'  <line x1="{xv:.1f}" y1="{ax_y1}" x2="{xv:.1f}" y2="{ax_y2}" '
          f'stroke="{col}" stroke-width="1" stroke-dasharray="4,3" opacity="0.85"/>')
        a(f'  <circle cx="{xv:.1f}" cy="{mk["y"]:.1f}" r="3" '
          f'fill="{col}" opacity="0.9"/>')
        label_y = ax_y1 - 4
        a(f'  <text transform="rotate(-65,{xv:.1f},{label_y})" '
          f'x="{xv:.1f}" y="{label_y}" '
          f'text-anchor="start" font-family="ui-monospace,monospace" font-size="8" '
          f'font-weight="600" fill="{col}">{_esc(mk["label"])}</text>')

    # Legend panel (top-right)
    lgd_x = ax_x2 - 4
    lgd_y = ax_y1 + 6
    row_h = 14
    lgd_w = 64
    lgd_h = len(legend_entries) * row_h + 8
    a(f'  <rect x="{lgd_x - lgd_w}" y="{lgd_y - 2}" '
      f'width="{lgd_w}" height="{lgd_h}" rx="3" '
      f'fill="{_COL_LEGEND_BG}" fill-opacity="0.75"/>')
    for li, (lbl, col) in enumerate(legend_entries):
        ry = lgd_y + li * row_h + row_h / 2 + 1
        sw_x = lgd_x - lgd_w + 6
        txt_x = sw_x + 14
        a(f'  <line x1="{sw_x}" y1="{ry:.1f}" x2="{sw_x + 10}" y2="{ry:.1f}" '
          f'stroke="{col}" stroke-width="2.5" stroke-linecap="round"/>')
        a(f'  <text x="{txt_x}" y="{ry + 3.5:.1f}" '
          f'font-family="ui-monospace,monospace" font-size="9" font-weight="600" '
          f'fill="{col}">{_esc(lbl)}</text>')

    a('</svg>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inline doctests / unit tests  (python -m doctest drilldown_chart.py -v)
# ---------------------------------------------------------------------------

def _run_doctests():
    """
    >>> from automation.jobs.drilldown_chart import render_earnings_annotated_chart

    # (a) Happy path with 8 events — SVG contains earnings-chart class and 8 markers.
    #     Build a 500-day synthetic series spanning 2024-01-01..2025-08+ so all
    #     8 event dates fall within the price window.
    >>> import automation.jobs.drilldown_chart as _m
    >>> import datetime as _dtc
    >>> _base = _dtc.date(2024, 1, 1)
    >>> _syn500 = [{"date": str(_base + _dtc.timedelta(days=i)), "close": 100.0 + i * 0.1} for i in range(500)]
    >>> _orig = _m._fetch_price_series
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> events_8 = [
    ...     {"date": "2024-02-01", "reaction_pct": 5.2},
    ...     {"date": "2024-04-01", "reaction_pct": -3.1},
    ...     {"date": "2024-06-01", "reaction_pct": 8.0},
    ...     {"date": "2024-08-01", "reaction_pct": -1.5},
    ...     {"date": "2024-10-01", "reaction_pct": 12.3},
    ...     {"date": "2024-12-01", "reaction_pct": -0.8},
    ...     {"date": "2025-02-01", "reaction_pct": 4.4},
    ...     {"date": "2025-04-01", "reaction_pct": 7.7},
    ... ]
    >>> svg = render_earnings_annotated_chart('NET', events_8)
    >>> 'class="earnings-chart"' in svg
    True
    >>> svg.count('stroke-dasharray="4,3"') == 8
    True
    >>> _m._fetch_price_series = _orig

    # (b) zero events — chart renders, no annotation lines
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> svg = render_earnings_annotated_chart('NET', [])
    >>> 'class="earnings-chart"' in svg
    True
    >>> 'stroke-dasharray="4,3"' not in svg
    True
    >>> _m._fetch_price_series = _orig

    # (c) fetch failure -> placeholder
    >>> _m._fetch_price_series = lambda t: []
    >>> out = render_earnings_annotated_chart('FAIL', [{'date': '2025-01-01', 'reaction_pct': 5.0}])
    >>> 'Chart data unavailable' in out
    True
    >>> _m._fetch_price_series = _orig

    # (d) event outside price window — skips, chart still renders
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> svg = render_earnings_annotated_chart('NET', [{'date': '2000-01-01', 'reaction_pct': 10.0}])
    >>> 'class="earnings-chart"' in svg
    True
    >>> 'stroke-dasharray="4,3"' not in svg
    True
    >>> _m._fetch_price_series = _orig
    """


if __name__ == "__main__":
    import doctest
    doctest.testmod(verbose=True)
