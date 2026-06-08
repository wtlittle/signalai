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


def _render_value_pick(p):
    """Render a single value pick block.

    Header:  "TICKER — Name (P/E X, Yield Y%)"
    Then Thesis / Catalyst / Risk on their own lines.
    """
    ticker = _na(p.get("ticker"))
    name = _na(_first(p, "name", "company"))
    pe = _first(p, "pe_ratio", "pe", "forward_pe")
    yld = _first(p, "dividend_yield", "yield")

    pe_str = _na(pe)
    yld_str = _na(yld)
    # Dividend yield may already include a % in the data; only append when absent.
    if yld_str != NA and not yld_str.endswith("%"):
        yld_str = f"{yld_str}%"

    header = f"{ticker} — {name} (P/E {pe_str}, Yield {yld_str})"
    lines = [header]
    lines.append(f"Thesis: {_na(p.get('thesis'))}")
    lines.append(f"Catalyst: {_na(p.get('catalyst'))}")
    lines.append(f"Risk: {_na(p.get('risk'))}")
    return "\n".join(lines)


def _render_momentum_pick(p):
    """Render a single momentum pick block.

    Header:  "TICKER — Name"
    Then:    "1M: ... Rev: ...", "Catalyst: ...", "R/R: ..."
    Truncated upstream values are preserved as-is (no padding).
    """
    ticker = _na(p.get("ticker"))
    name = _na(_first(p, "name", "company"))
    one_month = _first(p, "one_month_perf", "1m", "one_month")
    rev = _first(p, "revenue_growth", "rev_growth", "rev")
    catalyst = p.get("catalyst")
    rr = _first(p, "risk_reward", "r_r", "rr")

    lines = [f"{ticker} — {name}"]
    lines.append(f"1M: {_na(one_month)}  Rev: {_na(rev)}")
    lines.append(f"Catalyst: {_na(catalyst)}")
    lines.append(f"R/R: {_na(rr)}")
    return "\n".join(lines)


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

    # TOP 5 VALUE PICKS
    out.append("TOP 5 VALUE PICKS")
    value_picks = data.get("value_picks") or []
    if value_picks:
        for i, p in enumerate(value_picks):
            out.append(_render_value_pick(p))
            if i < len(value_picks) - 1:
                out.append("")
    else:
        out.append(NA)
    out.append("")

    # TOP 5 MOMENTUM PICKS
    out.append("TOP 5 MOMENTUM PICKS")
    momentum_picks = data.get("momentum_picks") or []
    if momentum_picks:
        for i, p in enumerate(momentum_picks):
            out.append(_render_momentum_pick(p))
            if i < len(momentum_picks) - 1:
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


def render_daily(data_dir):
    """Render the full daily briefing body as plain text from canonical paths."""
    snapshot = _load_json(os.path.join(data_dir, "data-snapshot.json")) or {}
    calendar = _load_json(os.path.join(data_dir, "earnings_calendar.json"))
    intel = _load_json(os.path.join(data_dir, "earnings_intel.json"))
    ma = _load_json(os.path.join(data_dir, "ma_status.json"))
    macro = _load_json(os.path.join(data_dir, "macro_data.json"))

    quotes = snapshot.get("quotes") or {}
    indices = (macro or {}).get("indices") if isinstance(macro, dict) else {}
    today = _format_et(snapshot.get("generated"))  # YYYY-MM-DD in ET

    out = []
    out.append(f"SignalAI Daily Briefing — {today}")
    out.append("=" * 64)
    out.append("")

    for section in (
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
    parser.add_argument("--mode", required=True, choices=["weekly", "daily"])
    parser.add_argument("--input", default=None,
                        help="Input JSON path (weekly mode; default weekly_briefing.json)")
    parser.add_argument("--output", required=True, help="Output text file path")
    parser.add_argument("--data-dir", default=CANONICAL_DIR,
                        help="Canonical data dir for daily mode")
    args = parser.parse_args(argv)

    if args.mode == "weekly":
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
        body = render_weekly(data)
    else:  # daily
        body = render_daily(args.data_dir)

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
