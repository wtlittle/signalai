"""
Deterministic plain-text email body renderer for SignalAI briefings.

Replaces the previous LLM-composition step in the weekly/daily cron prompts,
which once sent the literal placeholder "PLACEHOLDER_WILL_REPLACE" as the email
body. This renderer reads the canonical JSON and produces a fully-formed body so
the cron never has to ask an LLM to compose prose inline.

Two modes:
  --mode weekly --input weekly_briefing.json --output /tmp/weekly_email_body.txt
  --mode daily  --output /tmp/daily_email_body.txt   (reads canonical paths)

Data-integrity mandate: NEVER fabricate. Missing / null fields render as "n/a".
Truncated values from upstream JSON are preserved as-is (no padding, no cleanup).

No emoji anywhere in output (user rule).

Pure standard library only (json, datetime, argparse, sys, os, re).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# Canonical data dir for daily mode (the live watchlist-app working copy).
CANONICAL_DIR = "/home/user/workspace/watchlist-app"

DASHBOARD_LINK = "https://www.perplexity.ai/computer/a/stock-watchlist-terminal-qBSMi5vnQ1OezaO1hksi5A"
GITHUB_PAGES_LINK = "https://wtlittle.github.io/signalai/"

NA = "n/a"

# Freshness gate: the daily briefing must never ship a stale snapshot silently.
# On 2026-06-11 the daily_refresh cron crashed (silent OOM) and the renderer
# happily emailed the prior day's snapshot stamped with the wrong date. The gate
# below aborts the send when the snapshot is too old or the "earnings today"
# tickers carry a date other than the actual send date.
DAILY_SNAPSHOT_MAX_AGE_HOURS = 4.0
# The earnings calendar refreshes on a slower cadence than the price snapshot and
# legitimately has zero rows on quiet days, so it is gated on file freshness
# (mtime) and total emptiness -- NOT on whether the send date happens to appear.
DAILY_CALENDAR_MAX_AGE_HOURS = 24.0
FRESHNESS_ALERT_DIR = "/home/user/workspace/cron_tracking/daily_briefing"

# The weekly briefing enriches its picks from the same data-snapshot.json. It
# runs on a weekly cadence so it tolerates a wider window than the daily, but a
# multi-day-old snapshot still means the pick prices/quotes are stale and must
# not ship silently.
WEEKLY_SNAPSHOT_MAX_AGE_HOURS = 36.0
WEEKLY_FRESHNESS_ALERT_DIR = "/home/user/workspace/cron_tracking/weekly_briefing"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _na(value):
    """Return a clean string for a field, or 'n/a' when missing/blank.

    NEVER fabricates. None, empty string, and whitespace-only collapse to n/a.
    Non-empty values are preserved verbatim (including upstream truncation).
    """
    if value is None:
        return NA
    s = str(value).strip()
    return s if s else NA


def _first(d, *keys):
    """First non-empty value among the given keys (supports schema drift)."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _load_json(path):
    """Load JSON from path, or return None if missing/unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _require_keys(data, keys):
    """Return the list of required top-level keys absent from data."""
    return [k for k in keys if k not in data]


# --------------------------------------------------------------------------- #
# Weekly mode
# --------------------------------------------------------------------------- #
WEEKLY_REQUIRED_KEYS = [
    "week_ending",
    "value_picks",
    "momentum_picks",
    "index_returns",
    "trends",
    "risks",
    "watchlist_movers",
]


# --------------------------------------------------------------------------- #
# Enriched pick rendering helpers (price, sector valuation, 52w range)
# --------------------------------------------------------------------------- #
def _fmt_pct_signed(v):
    if v is None:
        return NA
    try:
        f = float(v)
    except (TypeError, ValueError):
        return NA
    return f"{'+' if f >= 0 else ''}{f:.1f}%"


def _fmt_price(v):
    if v is None:
        return NA
    try:
        f = float(v)
    except (TypeError, ValueError):
        return NA
    return f"${f:,.2f}"


def _fmt_mult(v):
    """Format a valuation multiple as e.g. '14.1x'. Returns n/a on failure."""
    if v is None:
        return NA
    try:
        return f"{float(v):.1f}x"
    except (TypeError, ValueError):
        return NA


def _fmt_mult_range(low, high):
    lo = _fmt_mult(low)
    hi = _fmt_mult(high)
    if lo == NA and hi == NA:
        return NA
    return f"{lo}–{hi}"


def _price_line(p):
    """One-liner: '$188.32  1D +0.5%  1W -1.2%  1M -3.4%  YTD -8.1%'."""
    parts = [_fmt_price(p.get("_price"))]
    for lbl, key in (("1D", "_change1d"), ("1W", "_change1w"), ("1M", "_change1m"), ("YTD", "_changeYtd")):
        v = p.get(key)
        if v is not None:
            parts.append(f"{lbl} {_fmt_pct_signed(v)}")
    return "  ".join(parts)


def _valuation_line(p):
    """One-liner: 'EV/Sales 7.4x (52w implied 5.3x–8.2x)' or 'Forward P/E n/a'."""
    name = p.get("_multiple_name") or DEFAULT_MULTIPLE
    cur = _fmt_mult(p.get("_multiple_current"))
    rng = _fmt_mult_range(p.get("_multiple_low_52w"), p.get("_multiple_high_52w"))
    if rng == NA:
        return f"{name} {cur}"
    return f"{name} {cur}  (52w implied {rng})"


def _render_value_pick(p):
    """Render an ENRICHED value pick block (plain text, pretty).

    Layout:
      TICKER — Name (Sector)
        Price:      $X.XX  1D ±X%  1W ±X%  1M ±X%  YTD ±X%
        Valuation:  EV/Sales/EV-EBITDA/P/E  X.Xx  (52w implied X.Xx–Y.Yx)
        Thesis:     ...
        Catalyst:   ...
        Risk:       ...
    Yield shown only when present in the source JSON. Multi-line wrapping NOT
    performed (email clients handle it). Truncated upstream values preserved.
    """
    ticker = _na(p.get("ticker"))
    name = _na(_first(p, "name", "company"))
    sector = _na(p.get("_sector"))
    yld = _first(p, "dividend_yield", "yield")
    yld_str = _na(yld) if yld is not None else NA
    if yld_str != NA and not yld_str.endswith("%"):
        yld_str = f"{yld_str}%"

    lines = []
    header = f"{ticker} — {name}"
    if sector != NA:
        header += f"  ({sector})"
    lines.append(header)
    lines.append(f"  Price:      {_price_line(p)}")
    val_line = _valuation_line(p)
    if yld_str != NA:
        val_line += f"  |  Div Yield {yld_str}"
    lines.append(f"  Valuation:  {val_line}")
    lines.append(f"  Thesis:     {_na(p.get('thesis'))}")
    lines.append(f"  Catalyst:   {_na(p.get('catalyst'))}")
    lines.append(f"  Risk:       {_na(p.get('risk'))}")
    return "\n".join(lines)


def _render_momentum_pick(p):
    """Render an ENRICHED momentum pick block (plain text, pretty).

    Layout:
      TICKER — Name (Sector)
        Price:      $X.XX  1D ±X%  1W ±X%  1M ±X%  YTD ±X%
        Valuation:  EV/Sales/EV-EBITDA/P/E  X.Xx  (52w implied X.Xx–Y.Yx)
        Rev:        Y%  |  1W (briefing): ...  |  3M (briefing): ...
        Catalyst:   ...
        R/R:        ...
    Falls back to briefing-supplied perf strings only when the structured
    perf fields are absent (preserves 'pulled from briefing prose' fidelity).
    """
    ticker = _na(p.get("ticker"))
    name = _na(_first(p, "name", "company"))
    sector = _na(p.get("_sector"))
    rev = _first(p, "revenue_growth", "rev_growth", "rev")
    catalyst = p.get("catalyst")
    rr = _first(p, "risk_reward", "r_r", "rr")
    one_week = _first(p, "one_week_perf")
    three_month = _first(p, "three_month_perf")

    lines = []
    header = f"{ticker} — {name}"
    if sector != NA:
        header += f"  ({sector})"
    lines.append(header)
    lines.append(f"  Price:      {_price_line(p)}")
    lines.append(f"  Valuation:  {_valuation_line(p)}")

    extras = []
    if rev is not None:
        extras.append(f"Rev growth {_na(rev)}")
    # Surface briefing-prose perf only if structured 1d/1w/1m didn't render anything useful
    if p.get("_change1w") is None and one_week:
        extras.append(f"1W (briefing) {_na(one_week)}")
    if p.get("_change1m") is None and three_month:
        extras.append(f"3M (briefing) {_na(three_month)}")
    if extras:
        lines.append(f"  Context:    " + "  |  ".join(extras))

    lines.append(f"  Catalyst:   {_na(catalyst)}")
    lines.append(f"  R/R:        {_na(rr)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTML weekly renderer (color-coded, table layouts)
# --------------------------------------------------------------------------- #
HTML_STYLES = """
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1d1d1f; line-height: 1.5; max-width: 740px; margin: 0 auto; padding: 24px; background: #fafafa; }
  h1 { font-size: 22px; margin: 0 0 4px 0; font-weight: 600; }
  .sub { color: #666; font-size: 13px; margin-bottom: 24px; }
  h2 { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #555; margin: 28px 0 12px 0; padding-bottom: 6px; border-bottom: 1px solid #d0d0d0; }
  .narrative { background: #fff; border-left: 3px solid #0070f3; padding: 14px 16px; margin: 12px 0; border-radius: 4px; }
  .narrative p { margin: 0 0 8px 0; }
  .narrative p:last-child { margin: 0; }
  .tomorrow { color: #0070f3; font-weight: 500; }
  table.idx { border-collapse: collapse; width: 100%; font-size: 13px; }
  table.idx td { padding: 6px 10px; border-bottom: 1px solid #eee; }
  table.idx td.lbl { font-weight: 500; }
  table.idx td.num { font-variant-numeric: tabular-nums; text-align: right; }
  .pick { background: #fff; padding: 14px 18px; margin: 10px 0; border-radius: 6px; border: 1px solid #e5e5e5; }
  .pick-head { font-weight: 600; font-size: 15px; margin-bottom: 8px; }
  .pick-sector { color: #777; font-weight: 400; font-size: 13px; }
  .pick table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 6px 0; }
  .pick table td { padding: 3px 6px 3px 0; vertical-align: top; }
  .pick td.k { color: #666; width: 90px; font-weight: 500; }
  .pick td.v { font-variant-numeric: tabular-nums; }
  .up { color: #007a3d; font-weight: 500; }
  .dn { color: #c41a1a; font-weight: 500; }
  .multline { display: inline-block; padding: 2px 6px; background: #f4f4f4; border-radius: 3px; font-size: 12px; }
  .narr-line { font-size: 13px; color: #444; margin: 4px 0 0 0; }
  ul.trends, ul.risks { margin: 6px 0; padding-left: 20px; font-size: 13px; }
  ul.trends li, ul.risks li { margin: 4px 0; }
  .mover { font-size: 13px; padding: 4px 0; border-bottom: 1px dotted #eee; }
  .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #ddd; font-size: 12px; color: #888; }
  .footer a { color: #0070f3; text-decoration: none; }
"""


def _html_pct(v):
    if v is None:
        return f'<span style="color:#999">n/a</span>'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return f'<span style="color:#999">n/a</span>'
    cls = "up" if f >= 0 else "dn"
    sign = "+" if f >= 0 else ""
    return f'<span class="{cls}">{sign}{f:.1f}%</span>'


def _html_pct_str(s):
    """Color-code a free-form percent string like '+12%' or '≈+35%' or '-3.5%'."""
    if not s:
        return '<span style="color:#999">n/a</span>'
    s = str(s)
    # Look for first +/- digit pattern to decide color
    import re
    m = re.search(r"([+-]?)\s*(\d+(?:\.\d+)?)\s*%", s)
    if not m:
        return s
    sign = m.group(1)
    if sign == "-":
        return f'<span class="dn">{s}</span>'
    return f'<span class="up">{s}</span>'


def _render_value_pick_html(p):
    ticker = _na(p.get("ticker"))
    name = _na(_first(p, "name", "company"))
    sector = _na(p.get("_sector"))
    price = _fmt_price(p.get("_price"))
    yld = _first(p, "dividend_yield", "yield")
    yld_str = _na(yld) if yld is not None else NA
    if yld_str != NA and not yld_str.endswith("%"):
        yld_str = f"{yld_str}%"

    perf = "&nbsp;&nbsp;".join(
        f"{lbl} {_html_pct(p.get(key))}"
        for lbl, key in (("1D", "_change1d"), ("1W", "_change1w"), ("1M", "_change1m"), ("YTD", "_changeYtd"))
        if p.get(key) is not None
    )
    if not perf:
        perf = '<span style="color:#999">n/a</span>'

    mult_name = p.get("_multiple_name") or DEFAULT_MULTIPLE
    mult_cur = _fmt_mult(p.get("_multiple_current"))
    mult_range = _fmt_mult_range(p.get("_multiple_low_52w"), p.get("_multiple_high_52w"))
    val_html = f'<strong>{mult_name}</strong> {mult_cur}'
    if mult_range != NA:
        val_html += f' &nbsp;<span class="multline">52w implied {mult_range}</span>'
    if yld_str != NA:
        val_html += f' &nbsp;<span class="multline">Div Yield {yld_str}</span>'

    return f"""
<div class="pick">
  <div class="pick-head">{ticker} — {name} <span class="pick-sector">({sector})</span></div>
  <table>
    <tr><td class="k">Price</td><td class="v">{price} &nbsp; {perf}</td></tr>
    <tr><td class="k">Valuation</td><td class="v">{val_html}</td></tr>
    <tr><td class="k">Thesis</td><td>{_na(p.get('thesis'))}</td></tr>
    <tr><td class="k">Catalyst</td><td>{_na(p.get('catalyst'))}</td></tr>
    <tr><td class="k">Risk</td><td>{_na(p.get('risk'))}</td></tr>
  </table>
</div>"""


def _render_momentum_pick_html(p):
    ticker = _na(p.get("ticker"))
    name = _na(_first(p, "name", "company"))
    sector = _na(p.get("_sector"))
    price = _fmt_price(p.get("_price"))
    rev = _first(p, "revenue_growth", "rev_growth", "rev")
    one_week_brief = _first(p, "one_week_perf")
    three_month_brief = _first(p, "three_month_perf")

    perf = "&nbsp;&nbsp;".join(
        f"{lbl} {_html_pct(p.get(key))}"
        for lbl, key in (("1D", "_change1d"), ("1W", "_change1w"), ("1M", "_change1m"), ("YTD", "_changeYtd"))
        if p.get(key) is not None
    )
    if not perf:
        perf = '<span style="color:#999">n/a</span>'

    mult_name = p.get("_multiple_name") or DEFAULT_MULTIPLE
    mult_cur = _fmt_mult(p.get("_multiple_current"))
    mult_range = _fmt_mult_range(p.get("_multiple_low_52w"), p.get("_multiple_high_52w"))
    val_html = f'<strong>{mult_name}</strong> {mult_cur}'
    if mult_range != NA:
        val_html += f' &nbsp;<span class="multline">52w implied {mult_range}</span>'

    context_bits = []
    if rev is not None:
        context_bits.append(f'Rev growth {_html_pct_str(rev)}')
    if p.get("_change1w") is None and one_week_brief:
        context_bits.append(f'1W (briefing) {_html_pct_str(one_week_brief)}')
    if p.get("_change1m") is None and three_month_brief:
        context_bits.append(f'3M (briefing) {_html_pct_str(three_month_brief)}')
    ctx_row = ""
    if context_bits:
        ctx_row = f'<tr><td class="k">Context</td><td class="v">{" &nbsp;|&nbsp; ".join(context_bits)}</td></tr>'

    return f"""
<div class="pick">
  <div class="pick-head">{ticker} — {name} <span class="pick-sector">({sector})</span></div>
  <table>
    <tr><td class="k">Price</td><td class="v">{price} &nbsp; {perf}</td></tr>
    <tr><td class="k">Valuation</td><td class="v">{val_html}</td></tr>
    {ctx_row}
    <tr><td class="k">Catalyst</td><td>{_na(p.get('catalyst'))}</td></tr>
    <tr><td class="k">R/R</td><td>{_na(_first(p, 'risk_reward', 'r_r', 'rr'))}</td></tr>
  </table>
</div>"""


def render_weekly_html(data):
    """Render the full weekly briefing as HTML (table-based, color-coded).

    Shares the same enriched picks that the plain-text path uses. Inline
    CSS only (Gmail strips <style> in some clients; we include both for
    safety).
    """
    from automation.jobs.weekly_pick_enricher import enrich_picks
    snap_path = os.path.join(CANONICAL_DIR, "data-snapshot.json")
    snap = _load_json(snap_path) or {}
    quotes = snap.get("quotes") or {}

    value_picks_e = enrich_picks(data.get("value_picks") or [], quotes)
    momentum_picks_e = enrich_picks(data.get("momentum_picks") or [], quotes)

    week_ending = _na(data.get("week_ending"))
    market_summary = _na(_first(data, "market_summary", "narrative"))

    # Indices block
    idx = data.get("index_returns") or {}
    idx_rows = ""
    for label in ("S&P 500", "NASDAQ Composite", "Dow Jones"):
        if label in idx:
            idx_rows += f'<tr><td class="lbl">{label}</td><td class="num">{_html_pct_str(idx[label])}</td></tr>'
    for label, v in idx.items():
        if label not in ("S&P 500", "NASDAQ Composite", "Dow Jones"):
            idx_rows += f'<tr><td class="lbl">{label}</td><td class="num">{_html_pct_str(v)}</td></tr>'

    # Value & momentum picks
    value_html = "".join(_render_value_pick_html(p) for p in value_picks_e) or "<p>n/a</p>"
    momentum_html = "".join(_render_momentum_pick_html(p) for p in momentum_picks_e) or "<p>n/a</p>"

    # Trends
    trends = data.get("trends") or []
    trends_html = "".join(
        f'<li><strong>{_na(_first(t, "theme", "name"))}:</strong> {_na(_first(t, "summary", "description"))} '
        f'<span style="color:#888">[{", ".join(str(x) for x in (t.get("tickers") or []))}]</span></li>'
        for t in trends
    ) or "<li>n/a</li>"

    # Risks
    risks = data.get("risks") or []
    risks_html = "".join(
        f'<li><strong>{_na(_first(r, "risk", "name"))}:</strong> {_na(r.get("impact"))}</li>'
        for r in risks
    ) or "<li>n/a</li>"

    # Movers
    movers = data.get("watchlist_movers") or []
    movers_html = "".join(
        f'<div class="mover"><strong>{_na(m.get("ticker"))}</strong> {_html_pct_str(_first(m, "move_pct", "change"))} — {_na(_first(m, "reason", "note"))}</div>'
        for m in movers
    ) or "<p>n/a</p>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{HTML_STYLES}</style></head>
<body>
<h1>SignalAI Weekly Briefing</h1>
<div class="sub">Week Ending {week_ending}</div>

<h2>Market Summary</h2>
<div class="narrative"><p>{market_summary}</p></div>

<h2>Index Returns (Week)</h2>
<table class="idx">{idx_rows or '<tr><td>n/a</td></tr>'}</table>

<h2>Top 5 Value Picks</h2>
{value_html}

<h2>Top 5 Momentum Picks</h2>
{momentum_html}

<h2>Key Trends</h2>
<ul class="trends">{trends_html}</ul>

<h2>Risks</h2>
<ul class="risks">{risks_html}</ul>

<h2>Notable Watchlist Movers</h2>
{movers_html}

<div class="footer">
  <a href="{DASHBOARD_LINK}">Dashboard</a> &nbsp;|&nbsp;
  <a href="{GITHUB_PAGES_LINK}">GitHub Pages</a>
</div>

</body></html>
"""


def render_weekly(data):
    """Render the full weekly briefing body as plain text.

    Section order:
      title, MARKET SUMMARY, INDEX RETURNS (WEEK), TOP 5 VALUE PICKS,
      TOP 5 MOMENTUM PICKS, KEY TRENDS, RISKS, NOTABLE WATCHLIST MOVERS,
      Dashboard link, GitHub Pages link.
    """
    week_ending = _na(data.get("week_ending"))
    out = []

    out.append(f"SignalAI Weekly Briefing — Week Ending {week_ending}")
    out.append("")

    # MARKET SUMMARY
    out.append("MARKET SUMMARY")
    out.append(_na(_first(data, "market_summary", "narrative")))
    out.append("")

    # INDEX RETURNS (WEEK)
    out.append("INDEX RETURNS (WEEK)")
    idx = data.get("index_returns") or {}
    if idx:
        for label in ("S&P 500", "NASDAQ Composite", "Dow Jones"):
            if label in idx:
                out.append(f"{label}: {_na(idx.get(label))}")
        # Include any additional indices present, preserving JSON order.
        for label, val in idx.items():
            if label not in ("S&P 500", "NASDAQ Composite", "Dow Jones"):
                out.append(f"{label}: {_na(val)}")
    else:
        out.append(NA)
    out.append("")

    # Enrich picks once (shared across both pick sections)
    from automation.jobs.weekly_pick_enricher import enrich_picks
    snap = _load_json(os.path.join(CANONICAL_DIR, "data-snapshot.json")) or {}
    snap_quotes = snap.get("quotes") or {}
    value_picks_e = enrich_picks(data.get("value_picks") or [], snap_quotes)
    momentum_picks_e = enrich_picks(data.get("momentum_picks") or [], snap_quotes)

    DIV = "─" * 64

    # TOP 5 VALUE PICKS
    out.append("TOP 5 VALUE PICKS")
    out.append(DIV)
    if value_picks_e:
        for i, p in enumerate(value_picks_e):
            out.append(_render_value_pick(p))
            if i < len(value_picks_e) - 1:
                out.append("")
    else:
        out.append(NA)
    out.append("")

    # TOP 5 MOMENTUM PICKS
    out.append("TOP 5 MOMENTUM PICKS")
    out.append(DIV)
    if momentum_picks_e:
        for i, p in enumerate(momentum_picks_e):
            out.append(_render_momentum_pick(p))
            if i < len(momentum_picks_e) - 1:
                out.append("")
    else:
        out.append(NA)
    out.append("")

    # KEY TRENDS
    out.append("KEY TRENDS")
    trends = data.get("trends") or []
    if trends:
        for t in trends:
            theme = _na(_first(t, "theme", "name"))
            summary = _na(_first(t, "summary", "description"))
            tickers = t.get("tickers") or []
            tick_str = ", ".join(str(x) for x in tickers) if tickers else NA
            out.append(f"{theme}: {summary} [{tick_str}]")
    else:
        out.append(NA)
    out.append("")

    # RISKS
    out.append("RISKS")
    risks = data.get("risks") or []
    if risks:
        for r in risks:
            label = _na(_first(r, "risk", "name"))
            impact = _na(r.get("impact"))
            out.append(f"{label}: {impact}")
    else:
        out.append(NA)
    out.append("")

    # NOTABLE WATCHLIST MOVERS
    out.append("NOTABLE WATCHLIST MOVERS")
    movers = data.get("watchlist_movers") or []
    if movers:
        for m in movers:
            ticker = _na(m.get("ticker"))
            change = _na(_first(m, "move_pct", "change"))
            note = _na(_first(m, "reason", "note"))
            out.append(f"{ticker} ({change}) — {note}")
    else:
        out.append(NA)
    out.append("")

    # Footer links
    out.append(f"Dashboard: {DASHBOARD_LINK}")
    out.append(f"GitHub Pages: {GITHUB_PAGES_LINK}")

    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Daily mode
# --------------------------------------------------------------------------- #
def _today_et():
    """The actual send date as YYYY-MM-DD in US Eastern.

    This is the date the email is being composed/sent -- NOT the snapshot's
    ``generated`` timestamp. The two diverge exactly when the snapshot is stale,
    which is the bug the freshness gate exists to catch.
    """
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        now = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        pass
    return now.strftime("%Y-%m-%d")


class FreshnessGateError(Exception):
    """Raised when the daily briefing must NOT ship because its data is stale.

    Carries the structured detail an operator alert needs: a short ``reason``,
    the snapshot age in hours (when known), and the send date. The cron handler
    catches this, writes the freshness_alert file, and fires a push instead of
    emailing a stale briefing.
    """

    def __init__(self, reason, *, hours_stale=None, send_date=None):
        super().__init__(reason)
        self.reason = reason
        self.hours_stale = hours_stale
        self.send_date = send_date


def _file_age_hours(path):
    """Age in hours of `path` by mtime, or None if it is missing/unreadable."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    age_s = datetime.now(timezone.utc).timestamp() - mtime
    return age_s / 3600.0


def _snapshot_age_hours(data_dir):
    """Age in hours of data-snapshot.json by mtime, or None if it is missing."""
    return _file_age_hours(os.path.join(data_dir, "data-snapshot.json"))


def _calendar_total_rows(calendar):
    """Total earnings rows across ALL dates (both pre/post buckets).

    Zero means the calendar is fully empty -- a corruption / failed-refresh tell,
    distinct from a healthy calendar that merely has no rows for one date.
    """
    if not isinstance(calendar, dict):
        return 0
    return sum(len(calendar.get(b) or []) for b in ("pre_earnings", "post_earnings"))


def write_freshness_alert(reason, *, send_date, hours_stale=None, alert_dir=None):
    """Persist a freshness-gate abort so the cron handler can alert on it.

    Writes ``freshness_alert_<send_date>.txt`` into ``alert_dir`` (default
    FRESHNESS_ALERT_DIR, the daily location). Never raises -- a failure to write
    the breadcrumb must not mask the original gate decision. Returns the path
    written, or None on failure.
    """
    alert_dir = alert_dir or FRESHNESS_ALERT_DIR
    try:
        os.makedirs(alert_dir, exist_ok=True)
        path = os.path.join(alert_dir, f"freshness_alert_{send_date}.txt")
        lines = [
            f"send_date={send_date}",
            f"reason={reason}",
        ]
        if hours_stale is not None:
            lines.append(f"hours_stale={hours_stale:.1f}")
        lines.append(f"written_at={datetime.now(timezone.utc).isoformat()}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path
    except Exception:
        return None


def check_daily_freshness(data_dir, *, send_date=None):
    """Raise FreshnessGateError if the daily snapshot or calendar is stale.

    Three independent checks, any of which aborts the send:
      1. data-snapshot.json mtime older than DAILY_SNAPSHOT_MAX_AGE_HOURS (the
         price quotes the whole briefing leans on are stale).
      2. earnings_calendar.json mtime older than DAILY_CALENDAR_MAX_AGE_HOURS
         (the calendar refresh failed to run -- the FILE itself is stale).
      3. earnings_calendar.json has ZERO rows across ALL dates (full corruption
         or a wiped calendar).

    Critically, a fresh calendar that simply has no rows for the send date is
    NOT a freshness failure: quiet sessions (e.g. Fridays after Q1 season) report
    no earnings, and the briefing renders an honest "None today." That false
    positive blocked the 2026-06-12 daily; the date-membership check is gone.
    """
    send_date = send_date or _today_et()

    age = _snapshot_age_hours(data_dir)
    if age is None:
        raise FreshnessGateError(
            "data-snapshot.json is missing", send_date=send_date)
    if age > DAILY_SNAPSHOT_MAX_AGE_HOURS:
        raise FreshnessGateError(
            f"data-snapshot.json is {age:.1f}h old "
            f"(max {DAILY_SNAPSHOT_MAX_AGE_HOURS:.0f}h)",
            hours_stale=age, send_date=send_date)

    cal_path = os.path.join(data_dir, "earnings_calendar.json")
    cal_age = _file_age_hours(cal_path)
    if cal_age is None:
        raise FreshnessGateError(
            "earnings_calendar.json is missing", send_date=send_date)
    if cal_age > DAILY_CALENDAR_MAX_AGE_HOURS:
        raise FreshnessGateError(
            f"earnings_calendar.json is {cal_age:.1f}h old "
            f"(max {DAILY_CALENDAR_MAX_AGE_HOURS:.0f}h) -- calendar refresh stale",
            hours_stale=cal_age, send_date=send_date)

    calendar = _load_json(cal_path)
    if _calendar_total_rows(calendar) == 0:
        raise FreshnessGateError(
            "earnings_calendar.json has zero rows across all dates "
            "-- calendar is empty/corrupt",
            send_date=send_date)


def check_weekly_freshness(data_dir=CANONICAL_DIR, *, send_date=None):
    """Raise FreshnessGateError if the snapshot backing weekly picks is stale.

    The weekly renderer enriches its value/momentum picks from the same
    data-snapshot.json the daily uses. A multi-day-old snapshot means stale pick
    prices, so we gate on WEEKLY_SNAPSHOT_MAX_AGE_HOURS. There is no earnings
    date check here (weekly carries no "earnings today" section).
    """
    send_date = send_date or _today_et()
    age = _snapshot_age_hours(data_dir)
    if age is None:
        raise FreshnessGateError(
            "data-snapshot.json is missing", send_date=send_date)
    if age > WEEKLY_SNAPSHOT_MAX_AGE_HOURS:
        raise FreshnessGateError(
            f"data-snapshot.json is {age:.1f}h old "
            f"(max {WEEKLY_SNAPSHOT_MAX_AGE_HOURS:.0f}h)",
            hours_stale=age, send_date=send_date)


def _format_et(iso_ts):
    """Format an ISO timestamp as a YYYY-MM-DD date string in US Eastern.

    Falls back to a naive date slice if zoneinfo is unavailable. Used only for
    the title line, so a graceful slice is acceptable when tz data is missing.
    """
    if not iso_ts:
        return NA
    raw = str(iso_ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        # Best-effort: take the leading date portion verbatim.
        return raw[:10] if len(raw) >= 10 else NA
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _pct(value):
    """Format a numeric percent like +3.2% / -1.4%, or n/a. Never fabricates."""
    if value is None:
        return NA
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _na(value)
    return f"{'+' if f >= 0 else ''}{f:.1f}%"


# --------------------------------------------------------------------------- #
# Market narrative (top-of-email blurb)
# --------------------------------------------------------------------------- #
def _mechanical_narrative(indices, quotes, factors):
    """Deterministic 'what moved + leadership/laggards' blurb.

    Built from observed numbers only — NEVER fabricates 'why'. Synthesizes:
      - index direction (S&P / NASDAQ / Russell + VIX)
      - top 3 down and top 3 up sectors of movers among watchlist
      - factor leadership snapshot (1M MTUM vs VLUE vs QUAL)
    Returns a list of sentence strings (typically 2-4). Empty list if no data.
    """
    sentences = []
    idx = indices or {}

    # Sentence 1: index move
    def _pct_val(d, key):
        if not isinstance(d, dict):
            return None
        v = _first(d, key, key.replace("_", ""))
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    sp = _pct_val(idx.get("^GSPC"), "change1d")
    ndq = _pct_val(idx.get("^IXIC"), "change1d")
    rut = _pct_val(idx.get("^RUT"),  "change1d")
    vix = _pct_val(idx.get("^VIX"),  "change1d")

    if sp is not None or ndq is not None:
        parts = []
        if sp  is not None: parts.append(f"S&P {_pct(sp)}")
        if ndq is not None: parts.append(f"NASDAQ {_pct(ndq)}")
        if rut is not None: parts.append(f"Russell {_pct(rut)}")
        if vix is not None: parts.append(f"VIX {_pct(vix)}")
        sentences.append("Tape: " + ", ".join(parts) + ".")

    # Sentence 2: leadership / laggard among watchlist top-movers by sector
    sector_buckets = {}
    for sym, q in (quotes or {}).items():
        if sym in _INDEX_TICKERS:
            continue
        try:
            c = float(q.get("change1d"))
        except (TypeError, ValueError):
            continue
        if abs(c) < 2.0:
            continue
        sec = q.get("sector") or "Other"
        sector_buckets.setdefault(sec, []).append((c, sym))
    if sector_buckets:
        # Mean move per sector among large movers
        ranked = sorted(
            ((sum(c for c, _ in v) / len(v), s, v) for s, v in sector_buckets.items() if len(v) >= 2),
            key=lambda x: x[0],
        )
        if ranked:
            worst = ranked[0]
            best = ranked[-1]
            bits = []
            if worst[0] < -1.5:
                names = ", ".join(sym for _, sym in sorted(worst[2])[:3])
                bits.append(f"{worst[1]} under pressure (avg {_pct(worst[0])} across {len(worst[2])} names: {names})")
            if best[0] > 1.5 and best[1] != worst[1]:
                names = ", ".join(sym for _, sym in sorted(best[2], reverse=True)[:3])
                bits.append(f"{best[1]} bid (avg {_pct(best[0])}: {names})")
            if bits:
                sentences.append("Leadership: " + "; ".join(bits) + ".")

    # Sentence 3: factor regime snapshot
    if isinstance(factors, dict):
        fbits = []
        for code, label in (("MTUM", "Momentum"), ("VLUE", "Value"), ("QUAL", "Quality"), ("IWF", "Growth")):
            f = factors.get(code) or {}
            m1 = _first(f, "change_1m", "change1m")
            if m1 is not None:
                fbits.append(f"{label} {_pct(m1)}")
        if fbits:
            sentences.append("Factor 1M: " + ", ".join(fbits) + ".")

    return sentences


def _render_narrative(macro_data, indices, quotes):
    """Render the top-of-email narrative block.

    Layered: LLM-composed blurb (when present in macro_data.daily_narrative)
    sits on top; mechanical observed-indicators sentences sit below as a
    transparent 'what the tape actually did' check. Either layer alone is
    valid; missing layers degrade gracefully.
    """
    # The forward look ("what to watch") now lives in the WHAT TO EXPECT TODAY
    # block (rendered above this one), so this section is the recap only.
    lines = ["WHAT MOVED YESTERDAY"]
    happened = None
    legacy_text = None
    if isinstance(macro_data, dict):
        dn = macro_data.get("daily_narrative")
        if isinstance(dn, dict):
            happened = dn.get("happened")
            legacy_text = dn.get("text") or dn.get("narrative")
        elif isinstance(dn, str):
            legacy_text = dn
    if happened:
        lines.append(str(happened).strip())
        lines.append("")
    if not happened and legacy_text:
        for para in str(legacy_text).strip().split("\n"):
            para = para.strip()
            if para:
                lines.append(para)
        lines.append("")

    factors = (macro_data or {}).get("factors") if isinstance(macro_data, dict) else None
    mech = _mechanical_narrative(indices, quotes, factors)
    if mech:
        lines.append("Indicators:")
        for s in mech:
            lines.append(f"  {s}")

    if len(lines) == 1:
        lines.append("No narrative available.")
    return lines


_INDEX_DISPLAY = [
    ("^GSPC", "S&P 500"),
    ("^IXIC", "NASDAQ"),
    ("^RUT",  "Russell 2000"),
    ("^VIX",  "VIX"),
]


def _render_market_snapshot(indices):
    """Major indices line — price + 1d/1w/1m/YTD when available.

    Pulls from macro_data.json#indices (^GSPC, ^IXIC, ^RUT, ^VIX). Never
    fabricates: missing keys collapse to n/a per field.
    """
    lines = ["MARKET SNAPSHOT"]
    if not isinstance(indices, dict) or not indices:
        lines.append("None today.")
        return lines
    for sym, label in _INDEX_DISPLAY:
        q = indices.get(sym)
        if not isinstance(q, dict):
            continue
        price = q.get("price")
        try:
            price_str = f"{float(price):,.2f}"
        except (TypeError, ValueError):
            price_str = _na(price)
        c1d = _pct(q.get("change1d"))
        c1w = _pct(_first(q, "change1w", "change_1w"))
        c1m = _pct(_first(q, "change1m", "change_1m"))
        lines.append(f"{label:<13} {price_str:>10}   1D {c1d:>7}  1W {c1w:>7}  1M {c1m:>7}")
    if len(lines) == 1:
        lines.append("None today.")
    return lines


_INDEX_TICKERS = {"SPY", "QQQ", "DIA", "IWM", "RUT", "VIX"}


def _render_top_movers(quotes):
    """Top 10 watchlist tickers by |change1d| with >= 3% absolute move.

    Format: "TICKER (±X.X%) Name — sector". Skips index ETFs.
    """
    lines = ["TOP MOVERS (today)"]
    if not quotes:
        lines.append("None today.")
        return lines

    candidates = []
    for sym, q in quotes.items():
        if sym in _INDEX_TICKERS:
            continue
        chg = q.get("change1d")
        try:
            c = float(chg)
        except (TypeError, ValueError):
            continue
        if abs(c) >= 3.0:
            candidates.append((abs(c), c, sym, q))

    if not candidates:
        lines.append("None today.")
        return lines

    candidates.sort(key=lambda x: x[0], reverse=True)
    for _, c, sym, q in candidates[:10]:
        name = _na(_first(q, "longName", "shortName", "name"))
        sector = _na(q.get("sector"))
        lines.append(f"{sym} ({_pct(c)}) {name} — {sector}")
    return lines


def _render_earnings_today(calendar, today):
    """Tickers reporting today (date == today) across pre/post lists.

    Format: "TICKER — Name (BMO/AMC/—)".
    """
    lines = ["EARNINGS TODAY"]
    if not calendar:
        lines.append("None on watchlist.")
        return lines

    rows = []
    for bucket in ("pre_earnings", "post_earnings"):
        for e in calendar.get(bucket) or []:
            edate = _first(e, "date", "earnings_date")
            if edate and str(edate)[:10] == today:
                rows.append(e)

    if not rows:
        lines.append("None on watchlist.")
        return lines

    seen = set()
    for e in rows:
        ticker = _na(e.get("ticker"))
        if ticker in seen:
            continue
        seen.add(ticker)
        name = _na(_first(e, "company", "name"))
        timing = _na(e.get("timing"))
        if timing == NA:
            timing = "—"
        lines.append(f"{ticker} — {name} ({timing})")
    return lines


# Extracting a *price* reaction from free-text intel is only safe when the
# percent is anchored to the stock itself. The intel JSON is dense with
# fundamental figures ("revenue up 30% YoY", "EPS +9%"), so we require an
# explicit price anchor (stock / shares / price) within a short window of the
# percent, and reject any clause carrying a fundamental marker. Reporting a
# revenue figure as a stock reaction would fabricate, which is forbidden.
_PRICE_ANCHOR = r"stock|shares?|price"
_DOWN_RE = re.compile(
    r"sold off|sell[\s-]?off|fell|fall|drop|down|plunge|sank|tumble|slid|"
    r"slip|declin|lower|loss",
    re.IGNORECASE,
)
_FUNDAMENTAL_RE = re.compile(
    r"yoy|year[\s-]?over[\s-]?year|y/y|revenue|rev\b|sales|arr|eps|margin|"
    r"bookings|backlog|guidance raise|estimate|consensus|growth",
    re.IGNORECASE,
)
# stock-then-percent  ("Stock down ~7% post-print", "shares fell 4%")
_PRICE_RE_A = re.compile(
    r"(" + _PRICE_ANCHOR + r")\b([^.%]{0,40}?)([+-]?\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
# percent-then-stock  ("-7.3% as the stock sold off", "+5% for the shares")
_PRICE_RE_B = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*%([^.]{0,40}?)(" + _PRICE_ANCHOR + r")\b",
    re.IGNORECASE,
)


def _signed(num_str, context):
    """Float value, signing an unsigned number from down-language in context."""
    try:
        val = float(num_str)
    except ValueError:
        return None
    if num_str.strip()[:1] in "+-":
        return val
    return -abs(val) if _DOWN_RE.search(context or "") else val


def _extract_reaction_pct(intel):
    """Best-available *price* reaction percent from a ticker's intel block.

    Requires the percent to be anchored to the stock (stock/shares/price) and
    rejects fundamental clauses (revenue/EPS/YoY/ARR/margin/guidance). Returns a
    float or None. NEVER fabricates — None when no genuine price move is stated.
    The post-earnings narrative is searched first (it describes the reaction),
    then key_metrics / surprises as a fallback.
    """
    candidates = []
    per = intel.get("post_earnings_review") or {}
    if isinstance(per, dict):
        for key in ("what_happened_headline", "takeaways_headline"):
            if per.get(key):
                candidates.append(str(per[key]))
    for field in ("key_metrics", "surprises"):
        seq = intel.get(field)
        if isinstance(seq, list):
            candidates.extend(str(x) for x in seq)

    for text in candidates:
        m = _PRICE_RE_A.search(text)
        if m and not _FUNDAMENTAL_RE.search(m.group(0)):
            out = _signed(m.group(3), m.group(0))
            if out is not None:
                return out
        m = _PRICE_RE_B.search(text)
        if m and not _FUNDAMENTAL_RE.search(m.group(0)):
            out = _signed(m.group(1), m.group(0))
            if out is not None:
                return out
    return None


def _classify_beat_miss(intel):
    """Classify a reaction as beat/miss/mixed from intel text. Never fabricates."""
    text = str(_first(intel, "beat_miss_quality") or "").lower()
    if not text:
        return NA
    if text.startswith("mixed") or "mixed" in text:
        return "mixed"
    if "beat" in text and "miss" in text:
        return "mixed"
    if "beat" in text:
        return "beat"
    if "miss" in text:
        return "miss"
    return NA


def _prior_session_date(today):
    """Most recent weekday before `today` (skips Sat/Sun). today is YYYY-MM-DD."""
    try:
        d = datetime.strptime(today, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:  # 5=Sat, 6=Sun
        prev -= timedelta(days=1)
    return prev.isoformat()


def _render_yesterday_reactions(intel_data, today):
    """Top 5 tickers (by |reaction|) that reported in the prior session.

    Format: "TICKER ±X.X% — beat/miss/mixed; one-line note".
    """
    lines = ["YESTERDAY'S REACTIONS"]
    tickers = (intel_data or {}).get("tickers") if isinstance(intel_data, dict) else None
    if not tickers:
        lines.append("None today.")
        return lines

    prior = _prior_session_date(today)
    rows = []
    for sym, intel in tickers.items():
        if not isinstance(intel, dict):
            continue
        last = str(_first(intel, "last_earnings_date", "earnings_date") or "")[:10]
        if not last or (prior and last != prior):
            continue
        pct = _extract_reaction_pct(intel)
        classification = _classify_beat_miss(intel)
        note = _na(_first(intel, "bottom_line"))
        if note != NA:
            note = note.split(". ")[0].strip()
            if len(note) > 140:
                note = note[:137] + "..."
        rows.append((abs(pct) if pct is not None else -1.0, pct, sym, classification, note))

    if not rows:
        lines.append("None today.")
        return lines

    rows.sort(key=lambda x: x[0], reverse=True)
    for _, pct, sym, classification, note in rows[:5]:
        pct_str = _pct(pct) if pct is not None else NA
        lines.append(f"{sym} {pct_str} — {classification}; {note}")
    return lines


def _render_yesterday_reactions_v2(intel_data, today):
    """Top 5 tickers (by |reaction|) that reported in the prior session.

    Reads ``post_earnings_review.stock_reaction_pct`` directly (populated by
    automation/jobs/reaction_populator.py) instead of regex-extracting from
    prose. Falls back to the regex path only if the structured field is
    missing. Never fabricates.
    """
    lines = ["YESTERDAY'S REACTIONS"]
    tickers = (intel_data or {}).get("tickers") if isinstance(intel_data, dict) else None
    if not tickers:
        lines.append("None today.")
        return lines

    prior = _prior_session_date(today)
    rows = []
    for sym, intel in tickers.items():
        if not isinstance(intel, dict):
            continue
        last = str(_first(intel, "last_earnings_date", "earnings_date") or "")[:10]
        if not last or (prior and last != prior):
            continue
        per = intel.get("post_earnings_review") or {}
        pct = per.get("stock_reaction_pct") if isinstance(per, dict) else None
        if pct is None:
            pct = _extract_reaction_pct(intel)
        classification = _classify_beat_miss(intel)
        note = _na(_first(intel, "bottom_line"))
        if note != NA:
            note = note.split(". ")[0].strip()
            if len(note) > 140:
                note = note[:137] + "..."
        rows.append((abs(pct) if pct is not None else -1.0, pct, sym, classification, note))

    if not rows:
        lines.append("None today.")
        return lines

    rows.sort(key=lambda x: x[0], reverse=True)
    for _, pct, sym, classification, note in rows[:5]:
        try:
            pct_str = f"{'+' if float(pct) >= 0 else ''}{float(pct):.1f}%"
        except (TypeError, ValueError):
            pct_str = NA
        lines.append(f"{sym} {pct_str} — {classification}; {note}")
    return lines


_CORP_SUFFIX_RE = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|"
    r"holdings|group|sa|nv|ag|lp|llc)\b",
    re.IGNORECASE,
)


def _norm_company(name):
    """Normalize a company/buyer name for self-referential comparison.

    Strips corporate suffixes and punctuation so "Autodesk" matches
    "Autodesk, Inc." and "Newmont" matches "Newmont Corporation".
    """
    if not name:
        return ""
    s = _CORP_SUFFIX_RE.sub(" ", str(name).lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _render_ma_rumors(ma_data):
    """Open M&A rumors from pending_review.

    Format: "TICKER — buyer; confidence 0.XX; flagged YYYY-MM-DD".
    Skips self-referential rows where company name == buyer (known data bug);
    logs a single stderr WARN when any such row is filtered.
    """
    lines = ["OPEN M&A RUMORS"]
    pending = (ma_data or {}).get("pending_review") if isinstance(ma_data, dict) else None
    if not pending:
        lines.append("None today.")
        return lines

    warned = False
    rendered = 0
    for r in pending:
        if not isinstance(r, dict):
            continue
        ticker = _na(r.get("ticker"))
        buyer_raw = r.get("buyer")
        name_raw = _first(r, "company", "name")
        # Self-referential bug: the named buyer is the company itself
        # (compared after stripping corporate suffixes like ", Inc.").
        nb, nn = _norm_company(buyer_raw), _norm_company(name_raw)
        if nb and nn and nb == nn:
            if not warned:
                sys.stderr.write(
                    "WARN: render_email daily — filtering self-referential M&A "
                    "pending_review row(s) where company name == buyer\n"
                )
                warned = True
            continue
        buyer = _na(buyer_raw)
        conf = r.get("confidence")
        conf_str = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else _na(conf)
        flagged = _na(r.get("flagged_at"))
        if flagged != NA:
            flagged = flagged[:10]
        lines.append(f"{ticker} — {buyer}; confidence {conf_str}; flagged {flagged}")
        rendered += 1

    if rendered == 0:
        lines.append("None today.")
    return lines


def _render_macro_tilt(macro_data):
    """Regime + the sectors the regime model says should lead vs lag.

    Logic explained inline in the email so the user knows what they're reading:
      - regime is the macro model's current call (e.g. Goldilocks)
      - regime-favored sectors are the ones the model says SHOULD lead under
        the current regime; the % is their actual 1M ETF change (a confirmation
        / disconfirmation signal, not a recommendation)
      - regime-avoid sectors are the model's expected laggards.
    Never fabricates.
    """
    lines = ["MACRO TILT"]
    if not isinstance(macro_data, dict):
        lines.append("None today.")
        return lines
    regime = macro_data.get("regime") or {}
    sectors = macro_data.get("sectors") or {}

    regime_name = _na(regime.get("regime"))
    regime_desc = _na(regime.get("desc"))
    if regime_desc != NA and len(regime_desc) > 110:
        regime_desc = regime_desc[:107] + "..."

    def _sector_line(etf):
        meta = sectors.get(etf) or {}
        label = _na(_first(meta, "sectorName", "name")) if meta else NA
        score = _first(meta, "change_1m", "change1m") if meta else None
        score_str = _pct(score) if score is not None else NA
        return f"{etf} ({label}): 1M {score_str}"

    favored = regime.get("favored_sectors") or []
    avoid = _first(regime, "avoid_sectors", "unfavored_sectors") or []

    lines.append(f"Regime: {regime_name} — {regime_desc}")

    if not favored and not avoid:
        lines.append("No regime tilts available.")
        return lines

    lines.append("Regime-favored sectors (model expects leadership; 1M shows whether it is playing out):")
    if favored:
        for etf in favored[:3]:
            lines.append(f"  {_sector_line(etf)}")
    else:
        lines.append(f"  {NA}")

    lines.append("Regime-avoid sectors (model expects lagging):")
    if avoid:
        for etf in avoid[:3]:
            lines.append(f"  {_sector_line(etf)}")
    else:
        lines.append(f"  {NA}")
    return lines


def _today_events_is_fresh(payload, now_utc=None):
    """True when the today_events payload was generated within the last 24h.

    A stale file (e.g. yesterday's, if the producer failed to run) must not
    surface as today's forward look, so the renderer treats it as absent.
    """
    if not isinstance(payload, dict):
        return False
    gen = payload.get("generated_at_utc")
    if not gen:
        return False
    raw = str(gen).strip().replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now_utc or datetime.now(timezone.utc)
    return (now - ts).total_seconds() < 24 * 3600


def _pretty_date(iso_date):
    """Format YYYY-MM-DD as 'Monday, June 8, 2026'. Falls back to the input."""
    try:
        d = datetime.strptime(str(iso_date)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(iso_date)
    # %-d (no leading zero) is platform-specific; strip manually for safety.
    return d.strftime("%A, %B ") + str(d.day) + d.strftime(", %Y")


def _render_today_events(payload, today, now_utc=None):
    """Forward-look block: events scheduled TODAY + watchlist earnings + debates.

    Renders 'None scheduled.' when the payload is missing, stale (>24h), or has
    no events. Watchlist-earnings and debates subsections are omitted when empty.
    Never fabricates — every line comes straight from the today_events JSON.
    """
    heading = f"WHAT TO EXPECT TODAY — {_pretty_date(today)}"
    lines = [heading]
    lines.append("-" * len(heading))

    if not _today_events_is_fresh(payload, now_utc):
        lines.append("None scheduled.")
        return lines

    events = payload.get("events") or []
    if not events:
        lines.append("None scheduled.")
        return lines

    for ev in events:
        if not isinstance(ev, dict):
            continue
        time_et = _na(ev.get("time_et"))
        name = _na(ev.get("name"))
        tickers = ev.get("tickers_sectors") or []
        tick_str = ""
        if isinstance(tickers, list) and tickers:
            tick_str = "  [" + ", ".join(str(t) for t in tickers) + "]"
        # Append " ET" only to clock times (HH:MM); "All day"/"TBD"/n/a stand alone.
        if re.match(r"^\d{1,2}:\d{2}$", time_et):
            time_label = f"{time_et} ET"
        else:
            time_label = time_et
        lines.append(f"{time_label:<9} {name}{tick_str}")
        watch = _na(ev.get("what_to_watch"))
        if watch != NA:
            lines.append(f"{'':<9} {watch}")
        lines.append("")
    # Drop the trailing blank line left by the loop.
    if lines and lines[-1] == "":
        lines.pop()

    earnings = payload.get("watchlist_earnings") or []
    if earnings:
        lines.append("")
        lines.append("Watchlist earnings today:")
        for e in earnings:
            if not isinstance(e, dict):
                continue
            ticker = _na(e.get("ticker"))
            timing = _na(e.get("time"))
            eps = e.get("consensus_eps")
            rev = e.get("consensus_rev_m")
            bits = []
            if isinstance(eps, (int, float)):
                bits.append(f"EPS ${eps:.2f}e")
            if isinstance(rev, (int, float)):
                # Render revenue in billions when large, else millions.
                if abs(rev) >= 1000:
                    bits.append(f"Rev ${rev / 1000:.2f}Be")
                else:
                    bits.append(f"Rev ${rev:.0f}Me")
            consensus = ", ".join(bits) if bits else "consensus n/a"
            lines.append(f"  {ticker:<6} {timing:<6} {consensus}")

    debates = payload.get("key_debates") or []
    if debates:
        lines.append("")
        lines.append("Key debates today:")
        for d in debates:
            if not isinstance(d, dict):
                continue
            topic = _na(d.get("topic"))
            lines.append(f"  - {topic}")
            bull = _na(d.get("bull"))
            bear = _na(d.get("bear"))
            if bull != NA:
                lines.append(f"      Bull: {bull}")
            if bear != NA:
                lines.append(f"      Bear: {bear}")

    return lines


def render_daily(data_dir, *, enforce_freshness=True, send_date=None):
    """Render the full daily briefing body as plain text from canonical paths.

    When ``enforce_freshness`` is True (the default, and the path the cron
    takes), the freshness gate runs first and raises FreshnessGateError when the
    snapshot is stale or misdated -- the caller must catch it and alert rather
    than send. Set False only for ad-hoc renders of a known-stale snapshot.

    ``send_date`` (YYYY-MM-DD) overrides the date the briefing is composed for;
    it drives the freshness gate's earnings-date check, the title line, and the
    "earnings today" / "yesterday's reactions" windows. Defaults to today in US
    Eastern when not supplied, preserving the original cron behavior.
    """
    send_date = send_date or _today_et()
    if enforce_freshness:
        check_daily_freshness(data_dir, send_date=send_date)

    snapshot = _load_json(os.path.join(data_dir, "data-snapshot.json")) or {}
    calendar = _load_json(os.path.join(data_dir, "earnings_calendar.json"))
    intel = _load_json(os.path.join(data_dir, "earnings_intel.json"))
    ma = _load_json(os.path.join(data_dir, "ma_status.json"))
    macro = _load_json(os.path.join(data_dir, "macro_data.json"))
    today_events = _load_json(os.path.join(data_dir, "today_events.json"))

    quotes = snapshot.get("quotes") or {}
    indices = (macro or {}).get("indices") if isinstance(macro, dict) else {}
    # The title/date MUST reflect the actual send date, not the snapshot's
    # generated stamp (which would silently mislabel a stale snapshot).
    today = send_date

    out = []
    out.append(f"SignalAI Daily Briefing — {today}")
    out.append("=" * 64)
    out.append("")

    for section in (
        _render_today_events(today_events, today),
        _render_narrative(macro, indices, quotes),
        _render_market_snapshot(indices),
        _render_top_movers(quotes),
        _render_earnings_today(calendar, today),
        _render_yesterday_reactions_v2(intel, today),
        _render_ma_rumors(ma),
        _render_macro_tilt(macro),
    ):
        out.extend(section)
        out.append("")

    out.append(f"Dashboard: {DASHBOARD_LINK}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a deterministic plain-text SignalAI email body."
    )
    parser.add_argument("--mode", required=True, choices=["weekly", "daily", "weekly_html"])
    parser.add_argument("--input", default=None,
                        help="Input JSON path (weekly mode; default weekly_briefing.json)")
    parser.add_argument("--output", required=True, help="Output text file path")
    parser.add_argument("--data-dir", default=CANONICAL_DIR,
                        help="Canonical data dir for daily mode")
    parser.add_argument("--date", default=None,
                        help="Send date YYYY-MM-DD for daily mode "
                             "(default: today US Eastern). Supports backfills.")
    parser.add_argument("--no-freshness-gate", action="store_true",
                        help="Render daily even if the snapshot is stale "
                             "(ad-hoc use only; the cron must NOT pass this)")
    args = parser.parse_args(argv)

    if args.mode in ("weekly", "weekly_html"):
        input_path = args.input or "weekly_briefing.json"
        data = _load_json(input_path)
        if data is None:
            sys.stderr.write(f"ERROR: cannot read weekly input JSON: {input_path}\n")
            return 2
        missing = _require_keys(data, WEEKLY_REQUIRED_KEYS)
        if missing:
            sys.stderr.write(
                "ERROR: weekly_briefing.json missing required top-level keys: "
                + ", ".join(missing) + "\n"
            )
            return 3
        if not args.no_freshness_gate:
            try:
                check_weekly_freshness(args.data_dir)
            except FreshnessGateError as exc:
                path = write_freshness_alert(
                    exc.reason, send_date=exc.send_date,
                    hours_stale=exc.hours_stale,
                    alert_dir=WEEKLY_FRESHNESS_ALERT_DIR)
                sys.stderr.write(
                    "FRESHNESS GATE: weekly briefing send aborted -- "
                    f"{exc.reason}\n")
                if path:
                    sys.stderr.write(f"  wrote freshness alert: {path}\n")
                return 5
        body = render_weekly_html(data) if args.mode == "weekly_html" else render_weekly(data)
    else:  # daily
        try:
            body = render_daily(args.data_dir,
                                enforce_freshness=not args.no_freshness_gate,
                                send_date=args.date)
        except FreshnessGateError as exc:
            path = write_freshness_alert(
                exc.reason, send_date=exc.send_date, hours_stale=exc.hours_stale)
            sys.stderr.write(
                "FRESHNESS GATE: daily briefing send aborted -- "
                f"{exc.reason}\n")
            if path:
                sys.stderr.write(f"  wrote freshness alert: {path}\n")
            return 5

    try:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError as exc:
        sys.stderr.write(f"ERROR: cannot write output: {exc}\n")
        return 4

    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
