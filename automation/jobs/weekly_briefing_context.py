"""
Concrete-data precompute for the weekly briefing trends prompt.

The 6/07 regression shipped watchlist_updates/upcoming_catalysts/sector_summary
as thin or empty arrays because the trends prompt asked sonar-deep-research to
"scan all 123 watchlist tickers" and "find upcoming catalysts" with NO data fed
into context. The model cannot reliably pull live prices/calendars for 100+
names, so it gave up and returned [].

This module precomputes three literal blocks from local data that the daily
refresh already populates, and the trends prompt embeds them verbatim:

  1. Material movers  — watchlist tickers with |1-week move| >= MOVER_THRESHOLD,
     from data-snapshot.json quotes (change1w).
  2. Upcoming earnings — watchlist earnings in the next CATALYST_HORIZON_DAYS,
     from earnings_calendar.json pre_earnings.
  3. Subsector buckets — the SUBSECTOR_MAP from utils.js collapsed into the eight
     canonical briefing subsectors, each with its constituent tickers + 1w move.

Pure stdlib + the existing shared helpers. No network calls.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from automation.shared.paths import DATA_SNAPSHOT, EARNINGS_CALENDAR
from automation.shared.io_helpers import read_json
from automation.shared.tickers import load_subsector_map

# A 1-week move at/above this absolute percent is "material" for watchlist_updates.
MOVER_THRESHOLD = 5.0
# How many entries we want the mover list to carry even when few names clear the
# 5% bar — we top up with the next-largest movers so the LLM always has >= 20.
MOVER_TARGET = 28
# Earnings within this many days of the briefing date count as upcoming catalysts.
CATALYST_HORIZON_DAYS = 14

# Collapse the fine-grained SUBSECTOR_MAP buckets (utils.js) into the eight
# canonical briefing subsectors. Any utils.js bucket not listed here is dropped
# from the sector_summary (kept tight to the eight the briefing renders).
_SUBSECTOR_ROLLUP = {
    "Software Infrastructure": {
        "Cloud Infrastructure", "Industrial Software", "DevOps & Automation",
        "Data Center Infrastructure", "Hyperscalers", "Networking",
        "Data & Analytics", "Applied AI",
    },
    "Application Software": {
        "Enterprise Software", "Vertical Software", "HCM & Payroll",
        "IT Services", "Health Tech",
    },
    "Cybersecurity": {"Cybersecurity"},
    "Semiconductors": {
        "Semiconductors", "Data Storage", "Consumer Electronics",
        "Electronics Distribution", "Quantum Computing",
    },
    "AdTech/Digital Media": {
        "Digital Advertising", "Marketing Tech", "Entertainment & Media", "Gaming",
    },
    "Fintech": {
        "Fintech", "Payments", "Consumer Finance", "Capital Markets",
        "Investment Banking", "Diversified Banking", "Insurance",
    },
    "Consumer": {
        "Digital Commerce", "E-Commerce", "Retail", "Consumer Staples",
        "Automotive",
    },
    "Energy/Industrials": {
        "Energy", "E&P Oil & Gas", "Power & Utilities", "Industrials",
        "Aerospace & Defense", "Materials", "Specialty Chemicals",
        "Building Products", "Auto Parts", "Copper & Mining", "Steel & Metals",
        "Specialty Materials", "Agriculture", "Airlines",
    },
}

# Canonical order the prompt and sector_summary keys should follow.
SUBSECTOR_ORDER = [
    "Software Infrastructure",
    "Application Software",
    "Cybersecurity",
    "Semiconductors",
    "AdTech/Digital Media",
    "Fintech",
    "Consumer",
    "Energy/Industrials",
]


def _fmt_pct(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return f"{'+' if f >= 0 else ''}{f:.1f}%"


def _load_quotes() -> dict:
    snap = read_json(DATA_SNAPSHOT)
    quotes = snap.get("quotes") if isinstance(snap, dict) else None
    return quotes if isinstance(quotes, dict) else {}


def compute_material_movers(quotes: dict | None = None) -> list[dict]:
    """Watchlist tickers with the largest absolute 1-week move.

    Returns a list of {ticker, name, change1w, change1m, sector} dicts sorted by
    |change1w| descending. Every name clearing MOVER_THRESHOLD is included; if
    fewer than MOVER_TARGET clear it, we top up with the next-largest movers so
    the LLM always receives a substantial list to write paragraphs about.
    """
    quotes = quotes if quotes is not None else _load_quotes()
    rows = []
    for sym, q in quotes.items():
        if not isinstance(q, dict):
            continue
        try:
            c1w = float(q.get("change1w"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "ticker": sym,
            "name": q.get("longName") or q.get("shortName") or sym,
            "change1w": c1w,
            "change1m": q.get("change1m"),
            "sector": q.get("sector") or "",
        })
    rows.sort(key=lambda r: abs(r["change1w"]), reverse=True)
    material = [r for r in rows if abs(r["change1w"]) >= MOVER_THRESHOLD]
    if len(material) < MOVER_TARGET:
        # Top up with the next-largest movers (already sorted) to reach the target.
        material = rows[:MOVER_TARGET]
    return material


def compute_upcoming_earnings(today: date | None = None) -> list[dict]:
    """Watchlist earnings events within the next CATALYST_HORIZON_DAYS.

    Reads earnings_calendar.json#pre_earnings. Returns {ticker, company, date,
    timing} dicts sorted by date ascending.
    """
    today = today or date.today()
    horizon = today + timedelta(days=CATALYST_HORIZON_DAYS)
    cal = read_json(EARNINGS_CALENDAR)
    pre = cal.get("pre_earnings") if isinstance(cal, dict) else None
    if not isinstance(pre, list):
        return []
    out = []
    for e in pre:
        if not isinstance(e, dict):
            continue
        raw = e.get("earnings_date") or e.get("date")
        if not raw:
            continue
        try:
            d = datetime.fromisoformat(str(raw)[:10]).date()
        except ValueError:
            continue
        if today <= d <= horizon:
            out.append({
                "ticker": e.get("ticker") or "",
                "company": e.get("company") or e.get("ticker") or "",
                "date": d.isoformat(),
                "timing": e.get("timing") or "TBD",
            })
    out.sort(key=lambda r: r["date"])
    return out


def compute_subsector_buckets(quotes: dict | None = None) -> dict[str, list[dict]]:
    """Map the eight canonical subsectors to their constituent watchlist tickers.

    Each ticker carries its 1-week move so the LLM can ground the sector_summary
    in real price action. Only tickers present in both SUBSECTOR_MAP and the
    snapshot quotes are included. Buckets are returned in SUBSECTOR_ORDER.
    """
    quotes = quotes if quotes is not None else _load_quotes()
    submap = load_subsector_map()
    # Invert the rollup: fine bucket -> canonical subsector.
    fine_to_canon = {}
    for canon, fines in _SUBSECTOR_ROLLUP.items():
        for f in fines:
            fine_to_canon[f] = canon

    buckets: dict[str, list[dict]] = {k: [] for k in SUBSECTOR_ORDER}
    for ticker, fine in submap.items():
        canon = fine_to_canon.get(fine)
        if not canon:
            continue
        q = quotes.get(ticker)
        if not isinstance(q, dict):
            continue
        buckets[canon].append({
            "ticker": ticker,
            "name": q.get("longName") or q.get("shortName") or ticker,
            "change1w": q.get("change1w"),
        })
    # Sort each bucket by |1w move| so the most notable names lead.
    for canon, members in buckets.items():
        members.sort(
            key=lambda r: abs(r["change1w"]) if isinstance(r["change1w"], (int, float)) else 0.0,
            reverse=True,
        )
    return buckets


def render_movers_block(movers: list[dict]) -> str:
    """Literal text block of material movers for prompt embedding."""
    if not movers:
        return "(no watchlist price data available this week)"
    lines = []
    for m in movers:
        lines.append(
            f"- {m['ticker']} ({m['name']}): 1W {_fmt_pct(m['change1w'])}, "
            f"1M {_fmt_pct(m['change1m'])} [{m['sector'] or 'n/a'}]"
        )
    return "\n".join(lines)


def render_earnings_block(events: list[dict]) -> str:
    """Literal text block of upcoming earnings for prompt embedding."""
    if not events:
        return "(no watchlist earnings in the next two weeks)"
    return "\n".join(
        f"- {e['ticker']} ({e['company']}) — {e['date']} {e['timing']}"
        for e in events
    )


def render_subsector_block(buckets: dict[str, list[dict]]) -> str:
    """Literal text block of subsector buckets + constituents for prompt embedding."""
    out = []
    for canon in SUBSECTOR_ORDER:
        members = buckets.get(canon) or []
        if members:
            sample = ", ".join(
                f"{m['ticker']} ({_fmt_pct(m['change1w'])})" for m in members[:8]
            )
        else:
            sample = "(no constituents with price data)"
        out.append(f"- {canon}: {sample}")
    return "\n".join(out)


def build_context_blocks(today: date | None = None) -> dict:
    """Compute all three concrete-data blocks for the trends prompt.

    Returns a dict with both the structured lists and the rendered text blocks,
    plus the minimum-count targets used by the prompt instructions and the
    post-LLM validator.
    """
    quotes = _load_quotes()
    movers = compute_material_movers(quotes)
    earnings = compute_upcoming_earnings(today)
    buckets = compute_subsector_buckets(quotes)
    return {
        "movers": movers,
        "earnings": earnings,
        "buckets": buckets,
        "movers_block": render_movers_block(movers),
        "earnings_block": render_earnings_block(earnings),
        "subsector_block": render_subsector_block(buckets),
        # Minimum counts: watchlist_updates >= 20 (clamped to available movers),
        # upcoming_catalysts >= 6, sector_summary >= 8 (the eight canonical buckets).
        "min_watchlist": min(20, len(movers)) if movers else 0,
        "min_catalysts": 6,
        "min_sectors": 8,
        "subsector_order": SUBSECTOR_ORDER,
    }
