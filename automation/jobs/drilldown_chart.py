"""SVG earnings-annotated price chart with 4-series overlay for drilldown notes.

Public API
----------
render_earnings_annotated_chart(ticker, earnings_events, sector=None,
                                require_annotations=True) -> str | None
    Returns a self-contained inline SVG string (no JS, no external deps), or
    None when require_annotations is True and no real earnings event date is
    available to annotate (the caller then omits the chart section entirely).
    The SVG has class="earnings-chart" on the root element so the validator
    can confirm its presence.

generate_chart(ticker, require_annotations=True) -> str | None
    Convenience wrapper that resolves the ticker's sector ETF and earnings
    events from on-disk data, then calls render_earnings_annotated_chart().

Theme
-----
The drilldown surface (see FROG drilldown) is a LIGHT theme on a near-white
page (--bg #fafaf9, --surface #ffffff). Chart text therefore uses the same
dark ink tokens as the rest of the note so it is legible:
    --text       #1c1917  (title)
    --text-muted #78716c  (axis labels, legend body)
    --text-light #a8a29e  (gridlines / faint detail)
    --green      #10b981  (positive reaction)
    --red        #ef4444  (negative reaction)

Design
------
- 1100x446 viewBox, responsive width (width="100%" preserveAspectRatio).
- 4 price series, all rebased to 100 at the start of the 2-year window
  so they compare on a % return basis ("Indexed, start=100").
    1. Ticker      — indigo-700 (#4338ca)  2.5 px  (thickest / darkest)
    2. Sector ETF  — amber-600  (#d97706)  1.8 px
    3. QQQ         — sky-600     (#0284c7)  1.8 px
    4. SPY         — slate-500   (#64748b)  1.8 px
- Legend panel in top-right corner (colour swatch + label), 120px wide, 14px font.
- Filled circle (r=5) earnings marker at each real print date on the ticker
  line: green (#10b981) when reaction_pct >= 0, red (#ef4444) otherwise.
  Rotated label "Q? +X.X%" above the chart area, 12px font.
- 5 horizontal gridlines, linear Y-axis, label "Indexed (start=100)" at 12px.
- Monthly X-axis tick labels rotated 30° for fit, 13px font.
- Title: "<TICKER> 2-year performance vs. <SECTOR_ETF> / QQQ / SPY" at 16px.
- Placeholder HTML returned when ticker price data <30 points or fails.

Annotation contract (per user: "annotations for key dates, or don't have it")
------------------------------------------------------------------------------
A marker is only plotted for an event that carries a real print date
(event['real'] is True). Events whose date was reconstructed from a
fiscal-period-end fallback are never labelled as print dates. When
require_annotations is True and no real-dated event remains, the renderer
returns None so the caller can omit the chart rather than ship an
unannotated one.

Sector → ETF mapping
--------------------
Resolved per-ticker from data/ticker_sector_etf.json (generated from the
dashboard's utils.js SUBSECTOR_MAP). Highlights:
    Software / Cloud / App SW   → IGV
    Semiconductors              → SOXX
    Cybersecurity               → CIBR
    Fintech / Payments          → FINX
    Energy                      → XLE
    Industrials                 → XLI
    Consumer Discretionary      → XLY
    Healthcare                  → XLV
    Default fallback            → XLK (broad tech)
A GICS-sector fallback map (below) is used when the ticker is not in the JSON.

Cache
-----
Per-symbol daily OHLC cached at data/cache/<SYMBOL>_price_2y.json with a
24-hour TTL.  SPY/QQQ/sector ETFs share the same cache files across all 123
tickers, so each is fetched at most once per day regardless of how many
drilldowns are regenerated.

Doctests  (python -m doctest drilldown_chart.py -v)
--------------------------------------------------------
>>> from automation.jobs.drilldown_chart import render_earnings_annotated_chart

>>> # (b) zero real events + require_annotations -> chart omitted (None)
>>> import automation.jobs.drilldown_chart as _m
>>> _synthetic = [{"date": f"2024-{(i//22)+1:02d}-{(i%22)+1:02d}", "close": 100.0 + i} for i in range(60)]
>>> _orig = _m._fetch_price_series
>>> _m._fetch_price_series = lambda t: _synthetic
>>> render_earnings_annotated_chart('NET', []) is None
True
>>> _m._fetch_price_series = _orig

>>> # (c) yfinance fetch failure -> placeholder
>>> _m._fetch_price_series = lambda t: []
>>> out = render_earnings_annotated_chart('FAIL', [{'date': '2025-01-01', 'reaction_pct': 5.0}])
>>> 'Chart data unavailable' in out
True
>>> _m._fetch_price_series = _orig
"""
from __future__ import annotations

import json
import math
import sys as _sys
import time
from datetime import datetime, timedelta as _timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[2]
_CACHE_DIR = ROOT / "data" / "cache"
_CACHE_TTL_SECS = 86400  # 24 h
_ETF_MAP_PATH = ROOT / "data" / "ticker_sector_etf.json"

# ---------------------------------------------------------------------------
# Broad GICS-sector → ETF fallback (used only when a ticker is absent from the
# per-ticker JSON map). The JSON map (data/ticker_sector_etf.json, generated
# from utils.js SUBSECTOR_MAP) is the primary, finer-grained resolver.
# ---------------------------------------------------------------------------
_DEFAULT_ETF = "XLK"  # broad tech

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

# Per-ticker sector-ETF map, loaded once from data/ticker_sector_etf.json.
_TICKER_ETF_CACHE: dict[str, str] | None = None


def _ticker_etf_map() -> dict[str, str]:
    """Load (and memoise) the ticker → sector-ETF map from disk.

    Generated from the dashboard's utils.js SUBSECTOR_MAP. Returns {} if the
    file is missing or malformed so callers fall back to the GICS-sector map.
    """
    global _TICKER_ETF_CACHE
    if _TICKER_ETF_CACHE is not None:
        return _TICKER_ETF_CACHE
    try:
        data = json.loads(_ETF_MAP_PATH.read_text(encoding="utf-8"))
        _TICKER_ETF_CACHE = {
            k.upper(): v for k, v in (data.get("ticker_etf") or {}).items()
        }
    except (OSError, ValueError, AttributeError):
        _TICKER_ETF_CACHE = {}
    return _TICKER_ETF_CACHE


def resolve_sector_etf(ticker: str, sector: str | None = None) -> str:
    """Resolve a ticker to its overlay sector ETF.

    Resolution order:
      1. Per-ticker JSON map (data/ticker_sector_etf.json) — finest grained.
      2. Broad GICS-sector fallback map, exact then fuzzy match.
      3. _DEFAULT_ETF (XLK, broad tech).
    """
    etf = _ticker_etf_map().get(ticker.upper())
    if etf:
        return etf
    if sector:
        etf = _SECTOR_ETF.get(sector)
        if etf:
            return etf
        sl = sector.lower()
        for k, v in _SECTOR_ETF.items():
            if sl in k.lower() or k.lower() in sl:
                return v
    return _DEFAULT_ETF

# ---------------------------------------------------------------------------
# Palette  (LIGHT theme — matches the drilldown surface --text/--muted tokens)
# ---------------------------------------------------------------------------
# Series lines: saturated, dark enough to read on a near-white (#ffffff) page.
_COL_TICKER = "#4338ca"    # indigo-700  — ticker line (darkest / thickest)
_COL_SECTOR = "#d97706"    # amber-600   — sector ETF
_COL_QQQ    = "#0284c7"    # sky-600     — QQQ
_COL_SPY    = "#64748b"    # slate-500   — SPY
# Earnings-reaction markers (match FROG --green / --red).
_COL_GREEN  = "#10b981"
_COL_RED    = "#ef4444"
# Text + chrome: the note's own ink tokens so the chart reads like the page.
_COL_GRID   = "#e7e5e4"    # --border    — gridlines
_COL_AXIS   = "#a8a29e"    # --text-light — axis rule
_COL_LABEL  = "#78716c"    # --text-muted — axis labels + legend body
_COL_TITLE  = "#1c1917"    # --text       — title
_COL_LEGEND_BG = "#fafaf9" # --bg, semi-transparent panel

# ---------------------------------------------------------------------------
# Layout  (SVG user units, viewBox 1100 x 446)
# ---------------------------------------------------------------------------
_VB_W = 1100
_VB_H = 446
_PAD_LEFT   = 72   # Y-axis labels
_PAD_RIGHT  = 24
_PAD_TOP    = 70   # title + rotated earnings-reaction labels + legend
_PAD_BOTTOM = 58   # X-axis labels
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
    require_annotations: bool = True,
) -> str | None:
    """Build an inline SVG 4-series annotated chart.

    Parameters
    ----------
    ticker:
        Primary stock ticker (e.g. "NET").
    earnings_events:
        List of dicts with keys:
            date (str "YYYY-MM-DD") — earnings report date
            reaction_pct (float | None) — 1-day stock reaction in percent
            real (bool) — True when `date` is a genuine print date (from
                event_date / earnings_date / measured lag). Markers are ONLY
                drawn for real-dated events; a fiscal-period-end fallback is
                never labelled as a print date. Missing key is treated as
                real for backward compatibility with callers/tests that pass
                pre-resolved real dates.
            fiscal (str) — optional fiscal-quarter label (e.g. "Q1") for the
                marker text. Falls back to MM/DD when absent.
    sector:
        Optional GICS sector string (e.g. "Technology"). Used as a fallback to
        derive the sector ETF overlay when the ticker is not in the per-ticker
        JSON map. The overlay always resolves to an ETF (default XLK).
    require_annotations:
        When True (default), return None if no real-dated earnings event is
        available to annotate — the caller then omits the chart entirely
        rather than shipping an unannotated chart (per user requirement).
        When False, render the chart even with zero markers.

    Returns
    -------
    str | None
        Inline SVG markup, the plain-HTML placeholder on data failure, or
        None when require_annotations is True and no real event date exists.
    """
    ticker = ticker.strip().upper()

    # ---- Gate on annotation availability BEFORE any network fetch ---------
    # A marker is only valid when the event carries a real print date. Events
    # missing the `real` key default to real (back-compat with callers that
    # already pass resolved print dates, e.g. unit tests).
    real_events = [
        ev for ev in earnings_events
        if ev.get("real", True) and (ev.get("date") or "")[:10]
    ]
    if require_annotations and not real_events:
        print(f"  [INFO] drilldown_chart: {ticker} has no real earnings event "
              f"dates — omitting chart (require_annotations=True)", file=_sys.stderr)
        return None

    # ---- Fetch all price series -------------------------------------------
    ticker_series = _fetch_price_series(ticker)
    if len(ticker_series) < 30:
        return _PLACEHOLDER

    # Resolve sector ETF (per-ticker JSON map first, GICS fallback, then XLK).
    sector_etf: str = resolve_sector_etf(ticker, sector)

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
    for ev in real_events:
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
        # Label: prefer "<fiscal> <±reaction>" (e.g. "Q1 +12.0%"); fall back to
        # MM/DD when no fiscal label, and to just the period when no reaction.
        period = ev.get("fiscal") or date_str[5:]
        if reaction is not None:
            sign = "+" if reaction >= 0 else ""
            label = f"{period} {sign}{reaction:.1f}%"
        else:
            label = str(period)
        markers.append({
            "idx": idx,
            "x": px(idx),
            "y": py(tk_vals[idx]),
            "color": color,
            "label": label,
        })

    # If, after windowing, no real marker survived and annotations are
    # required, omit the chart rather than ship an unannotated one.
    if require_annotations and not markers:
        print(f"  [INFO] drilldown_chart: {ticker} real events all fell outside "
              f"the price window — omitting chart", file=_sys.stderr)
        return None

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
      f'height="auto" '
      f'preserveAspectRatio="xMidYMid meet" '
      f'style="display:block;max-width:100%;height:auto;background:transparent;" '
      f'aria-label="{_esc(title_text)}">')

    # Title
    a(f'  <text x="{_PAD_LEFT}" y="28" '
      f'font-family="ui-monospace,monospace" font-size="16" font-weight="600" '
      f'fill="{_COL_TITLE}">{_esc(title_text)}</text>')

    # Gridlines + Y labels
    for tick in y_ticks:
        yv = py(tick)
        a(f'  <line x1="{ax_x1}" y1="{yv:.1f}" x2="{ax_x2}" y2="{yv:.1f}" '
          f'stroke="{_COL_GRID}" stroke-width="0.5" stroke-dasharray="3,4"/>')
        a(f'  <text x="{ax_x1 - 5}" y="{yv + 4.5:.1f}" '
          f'text-anchor="end" font-family="ui-monospace,monospace" font-size="13" '
          f'fill="{_COL_LABEL}">{tick:.1f}</text>')

    # Y-axis label ("Indexed (start=100)")
    mid_y = ax_y1 + _PLOT_H / 2
    a(f'  <text transform="rotate(-90,14,{mid_y:.1f})" '
      f'x="14" y="{mid_y:.1f}" '
      f'text-anchor="middle" font-family="ui-monospace,monospace" font-size="12" '
      f'fill="{_COL_LABEL}">Indexed (start=100)</text>')

    # Axes
    a(f'  <line x1="{ax_x1}" y1="{ax_y1}" x2="{ax_x1}" y2="{ax_y2}" '
      f'stroke="{_COL_AXIS}" stroke-width="0.8"/>')
    a(f'  <line x1="{ax_x1}" y1="{ax_y2}" x2="{ax_x2}" y2="{ax_y2}" '
      f'stroke="{_COL_AXIS}" stroke-width="0.8"/>')

    # X-axis tick labels
    for xv, lbl in x_ticks:
        ybase = ax_y2 + 16
        a(f'  <text transform="rotate(-30,{xv:.1f},{ybase})" '
          f'x="{xv:.1f}" y="{ybase}" '
          f'text-anchor="end" font-family="ui-monospace,monospace" font-size="13" '
          f'fill="{_COL_LABEL}">{_esc(lbl)}</text>')

    # Overlay lines (draw behind ticker)
    if sec_rebased:
        a(_polyline(sec_vals, _COL_SECTOR, 1.8, "overlay-sector"))
    a(_polyline(qqq_vals, _COL_QQQ, 1.8, "overlay-qqq"))
    a(_polyline(spy_vals, _COL_SPY, 1.8, "overlay-spy"))

    # Ticker line (front, thickest)
    a(_polyline(tk_vals, _COL_TICKER, 2.5, "line-ticker"))

    # Earnings markers: a filled dot on the ticker line at each real print
    # date, with a short rotated reaction label leadered up from the dot.
    for mk in markers:
        xv = mk["x"]
        yv = mk["y"]
        col = mk["color"]
        # Short leader from the dot up toward the label (keeps the chart legible
        # without a full-height vertical rule).
        a(f'  <line class="earnings-marker" x1="{xv:.1f}" y1="{yv:.1f}" '
          f'x2="{xv:.1f}" y2="{ax_y1 + 2:.1f}" '
          f'stroke="{col}" stroke-width="1" stroke-dasharray="4,3" opacity="0.55"/>')
        # Outlined filled circle (4-5px) — white halo so it reads over any line.
        a(f'  <circle cx="{xv:.1f}" cy="{yv:.1f}" r="5" '
          f'fill="{col}" stroke="#ffffff" stroke-width="1.2"/>')
        # Rotated reaction label above the plot, anchored at the dot's x.
        label_y = ax_y1 - 6
        a(f'  <text transform="rotate(-60,{xv:.1f},{label_y})" '
          f'x="{xv:.1f}" y="{label_y}" '
          f'text-anchor="start" font-family="ui-monospace,monospace" font-size="12" '
          f'font-weight="700" fill="{col}">{_esc(mk["label"])}</text>')

    # Legend panel (top-right)
    lgd_x = ax_x2 - 4
    lgd_y = ax_y1 + 8
    row_h = 17
    lgd_w = 120
    lgd_h = len(legend_entries) * row_h + 10
    a(f'  <rect x="{lgd_x - lgd_w}" y="{lgd_y - 2}" '
      f'width="{lgd_w}" height="{lgd_h}" rx="3" '
      f'fill="{_COL_LEGEND_BG}" fill-opacity="0.9" '
      f'stroke="{_COL_GRID}" stroke-width="1"/>')
    for li, (lbl, col) in enumerate(legend_entries):
        ry = lgd_y + li * row_h + row_h / 2 + 1
        sw_x = lgd_x - lgd_w + 8
        txt_x = sw_x + 16
        a(f'  <line x1="{sw_x}" y1="{ry:.1f}" x2="{sw_x + 12}" y2="{ry:.1f}" '
          f'stroke="{col}" stroke-width="2.5" stroke-linecap="round"/>')
        a(f'  <text x="{txt_x}" y="{ry + 4.5:.1f}" '
          f'font-family="ui-monospace,monospace" font-size="14" font-weight="600" '
          f'fill="{col}">{_esc(lbl)}</text>')

    a('</svg>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone data resolution + convenience wrapper
# ---------------------------------------------------------------------------
_SNAPSHOT_PATH = ROOT / "data-snapshot.json"
_INTEL_PATH = ROOT / "earnings_intel.json"

# Quarter-end month → fiscal-quarter label (calendar-year approximation used
# only for the marker text; the actual fiscal year is not needed here).
_QTR_LABEL = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_earnings_events(ticker: str, snap: dict | None = None,
                          intel: dict | None = None) -> list[dict]:
    """Resolve a ticker's earnings markers to real print dates.

    Mirrors generate_drilldown._earnings_events but is self-contained so the
    chart can be generated/tested without importing the generator. Each event:
        {date, reaction_pct, real, fiscal}
    `real` is True only when the print date came from an actual field
    (last_earnings_date / event_date / earnings_date) or a measured reporting
    lag projected from a known recent print — never from a bare period-end.
    """
    if snap is None:
        snap = _load_json(_SNAPSHOT_PATH)
    if intel is None:
        intel = (_load_json(_INTEL_PATH).get("tickers") or {}).get(ticker, {}) or {}

    asum = (snap.get("analyst_summary") or {}).get(ticker, {})
    hist = asum.get("earningsHistory") or []

    period_ends = [(h.get("quarter") or h.get("period_end") or "")[:10] for h in hist]
    newest_pe = max((p for p in period_ends if p), default="")

    actual_recent = (intel.get("last_earnings_date")
                     or (intel.get("post_earnings_review") or {}).get("earnings_date")
                     or "")
    lag_days: int | None = None
    if actual_recent and newest_pe:
        try:
            lag_days = (datetime.fromisoformat(str(actual_recent)[:10]).date()
                        - datetime.fromisoformat(newest_pe).date()).days
        except ValueError:
            lag_days = None

    events: list[dict] = []
    for h in hist:
        period_end = (h.get("quarter") or h.get("period_end") or "")[:10]
        if not period_end:
            continue
        explicit = (h.get("event_date") or h.get("earnings_date") or "")
        ev_date = ""
        real = False
        if period_end == newest_pe and actual_recent:
            ev_date, real = str(actual_recent)[:10], True
        elif explicit:
            ev_date, real = str(explicit)[:10], True
        elif lag_days is not None:
            try:
                pe = datetime.fromisoformat(period_end).date()
                ev_date = (pe + _timedelta(days=lag_days)).isoformat()
                real = True
            except ValueError:
                ev_date, real = period_end, False
        else:
            # No anchor at all → bare period-end. NOT a real print date.
            ev_date, real = period_end, False

        try:
            month = datetime.fromisoformat(period_end).month
        except ValueError:
            month = 0
        fiscal = _QTR_LABEL.get(month, "")

        sp = h.get("surprisePercent")
        reaction = round(float(sp) * 100, 2) if sp is not None else None
        events.append({"date": ev_date, "reaction_pct": reaction,
                       "real": real, "fiscal": fiscal})
    return events


def generate_chart(ticker: str, require_annotations: bool = True) -> str | None:
    """Resolve a ticker's sector ETF + earnings events from disk and render.

    Convenience entry point used by tooling/tests. Returns the inline SVG, the
    HTML placeholder on price-data failure, or None when require_annotations is
    True and the ticker has no real earnings event dates to annotate.
    """
    ticker = ticker.strip().upper()
    snap = _load_json(_SNAPSHOT_PATH)
    intel = (_load_json(_INTEL_PATH).get("tickers") or {}).get(ticker, {}) or {}
    events = build_earnings_events(ticker, snap, intel)
    sector = (snap.get("quotes") or {}).get(ticker, {}).get("sector")
    return render_earnings_annotated_chart(
        ticker, events, sector=sector, require_annotations=require_annotations
    )


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
    >>> svg.count('class="earnings-marker"') == 8
    True
    >>> _m._fetch_price_series = _orig

    # (b) zero real events + require_annotations -> None (chart omitted)
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> render_earnings_annotated_chart('NET', []) is None
    True
    >>> _m._fetch_price_series = _orig

    # (b2) zero events but require_annotations=False -> chart renders, no markers
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> svg = render_earnings_annotated_chart('NET', [], require_annotations=False)
    >>> 'class="earnings-chart"' in svg
    True
    >>> 'class="earnings-marker"' not in svg
    True
    >>> _m._fetch_price_series = _orig

    # (b3) only fallback (real=False) events -> None even though dates present
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> _fb = [{'date': '2024-06-30', 'reaction_pct': 3.0, 'real': False}]
    >>> render_earnings_annotated_chart('NET', _fb) is None
    True
    >>> _m._fetch_price_series = _orig

    # (c) fetch failure -> placeholder
    >>> _m._fetch_price_series = lambda t: []
    >>> out = render_earnings_annotated_chart('FAIL', [{'date': '2025-01-01', 'reaction_pct': 5.0}])
    >>> 'Chart data unavailable' in out
    True
    >>> _m._fetch_price_series = _orig

    # (d) real event outside price window + require_annotations -> None
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> render_earnings_annotated_chart('NET', [{'date': '2000-01-01', 'reaction_pct': 10.0}]) is None
    True
    >>> _m._fetch_price_series = _orig

    # (e) fiscal label renders as "Q1 +12.0%"
    >>> _m._fetch_price_series = lambda t: _syn500
    >>> svg = render_earnings_annotated_chart('NET', [{'date': '2024-05-01', 'reaction_pct': 12.0, 'fiscal': 'Q1'}])
    >>> 'Q1 +12.0%' in svg
    True
    >>> _m._fetch_price_series = _orig
    """


if __name__ == "__main__":
    import doctest
    doctest.testmod(verbose=True)
