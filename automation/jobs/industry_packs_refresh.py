"""
Weekly industry benchmark pack generator + Supabase writer.

Builds a structured Industry Benchmark Pack for each subsector in the
watchlist and upserts it to the Supabase `industry_packs` table. Packs are
consumed by pre_earnings_context.py to ground each earnings note in current
industry context.

Usage:
    python -m automation.jobs.industry_packs_refresh                          # all subsectors
    python -m automation.jobs.industry_packs_refresh --subsector Semiconductors
    python -m automation.jobs.industry_packs_refresh --dry-run                # log only
"""
import argparse
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR, DATA_SNAPSHOT, EARNINGS_CALENDAR
from automation.shared.tickers import load_subsector_map
from automation.shared.io_helpers import read_json
from automation.perplexity.client import call_perplexity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_monday(d: date | None = None) -> str:
    """Return the Monday of the ISO week containing *d* (default: today)."""
    d = d or date.today()
    return (d - timedelta(days=d.weekday())).isoformat()


def _safe_float(val) -> float | None:
    """Coerce *val* to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _median(values: list[float | None]) -> float | None:
    """Median of non-None floats, or None if empty."""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return round(statistics.median(clean), 2)


def _load_snapshot() -> dict:
    """Load data-snapshot.json or return empty dict."""
    return read_json(DATA_SNAPSHOT)


def _load_calendar() -> dict:
    """Load earnings_calendar.json or return empty dict."""
    return read_json(EARNINGS_CALENDAR)


# ---------------------------------------------------------------------------
# Per-subsector pack builder
# ---------------------------------------------------------------------------

def _build_constituents(subsector: str, subsector_map: dict,
                        snapshot: dict) -> list[str]:
    """Return constituent tickers for *subsector*, capped at top 10 by market cap."""
    tickers = [t for t, s in subsector_map.items() if s == subsector]
    quotes = snapshot.get("quotes", {}) or {}
    # Sort by marketCap descending; tickers without data go last.
    tickers.sort(
        key=lambda t: _safe_float((quotes.get(t) or {}).get("marketCap")) or 0,
        reverse=True,
    )
    return tickers[:10]


def _build_kpi_benchmarks(tickers: list[str], snapshot: dict) -> dict:
    """Compute median KPIs across *tickers* from data-snapshot.json.

    quotes has: revenueGrowth, operatingMargins, forwardPE, enterpriseToEbitda,
                freeCashflow, totalRevenue, marketCap.
    estimates (from refresh_deep_dive.mjs) has: grossMargins, fcfMargin.
    """
    quotes = snapshot.get("quotes", {}) or {}
    estimates = snapshot.get("estimates", {}) or {}

    rev_growth = []
    gross_margin = []
    op_margin = []
    fcf_margin = []
    ntm_pe = []
    ev_ebitda = []

    for t in tickers:
        q = quotes.get(t) or {}
        e = estimates.get(t) or {}

        rev_growth.append(_safe_float(q.get("revenueGrowth")))
        op_margin.append(_safe_float(q.get("operatingMargins")))
        ntm_pe.append(_safe_float(q.get("forwardPE")))
        ev_ebitda.append(_safe_float(q.get("enterpriseToEbitda")))

        # grossMargins lives in estimates (written by refresh_deep_dive.mjs).
        # Fall back to quotes if present there on some tickers.
        gm = _safe_float(e.get("grossMargins")) or _safe_float(q.get("grossMargins"))
        gross_margin.append(gm)

        # fcfMargin: prefer estimates, else compute from quotes fields.
        fm = _safe_float(e.get("fcfMargin"))
        if fm is None:
            fcf = _safe_float(q.get("freeCashflow"))
            rev = _safe_float(q.get("totalRevenue"))
            if fcf is not None and rev and rev != 0:
                fm = round(fcf / rev * 100, 2)
        fcf_margin.append(fm)

    return {
        "median_revenue_growth_yoy_pct": _median(rev_growth),
        "median_gross_margin_pct": _median(gross_margin),
        "median_operating_margin_pct": _median(op_margin),
        "median_fcf_margin_pct": _median(fcf_margin),
        "median_ntm_pe": _median(ntm_pe),
        "median_ev_to_ebitda": _median(ev_ebitda),
        "constituents": tickers,
    }


def _build_winners_losers(tickers: list[str], snapshot: dict) -> dict:
    """5-day (1w) winners and losers from data-snapshot.json.

    data-snapshot.json uses 'change1w' for the 5-trading-day return (not
    'change5d'). Sort ascending so losers are first, winners last.
    """
    quotes = snapshot.get("quotes", {}) or {}
    scored = []
    for t in tickers:
        q = quotes.get(t) or {}
        pct = _safe_float(q.get("change1w"))
        if pct is not None:
            scored.append({"ticker": t, "pct": round(pct, 2)})
    scored.sort(key=lambda x: x["pct"])
    losers = [x for x in scored if x["pct"] < 0][:5]
    winners = [x for x in scored if x["pct"] > 0][-5:]
    winners.sort(key=lambda x: x["pct"], reverse=True)
    return {"winners": winners, "losers": losers}


def _build_key_themes(subsector: str, tickers: list[str]) -> tuple[list[dict] | None, list[str]]:
    """ONE Perplexity call for key themes shaping this subsector this week.

    Returns (themes_list_or_None, sources_list).
    If PERPLEXITY_API_KEY is not set, themes are queued and None is returned.
    """
    ticker_str = ", ".join(tickers[:10])
    prompt = (
        f"Identify 3-5 key themes shaping the {subsector} subsector this week. "
        f"Constituents: {ticker_str}. "
        f"For each theme, provide: theme (short title), evidence (1-2 sentences), "
        f"and tickers_implicated (list of ticker symbols from the constituents). "
        f'Return ONLY a JSON object: {{"themes": [...], "sources": [...]}}. '
        f"No prose, no markdown fences."
    )
    result = call_perplexity(
        ticker=subsector.upper().replace(" ", "_"),
        task="industry_pack",
        prompt=prompt,
        system="Return ONLY JSON. No prose.",
        max_tokens=1200,
    )
    if not isinstance(result, dict):
        return None, []
    # Queued or skipped — return None for themes.
    if result.get("queued") or result.get("skipped") or result.get("dry_run"):
        return None, []
    themes = result.get("themes")
    sources = result.get("sources") or []
    if not isinstance(themes, list):
        themes = None
    return themes, sources


def _build_next_week_catalysts(tickers: list[str], calendar: dict) -> list[dict]:
    """Filter earnings_calendar.json to constituents reporting in the next 7 days."""
    today = date.today()
    horizon = today + timedelta(days=7)
    catalysts = []
    for section in ("pre_earnings",):
        for entry in calendar.get(section, []):
            t = entry.get("ticker")
            if t not in tickers:
                continue
            edate_str = entry.get("earnings_date") or entry.get("date")
            if not edate_str:
                continue
            try:
                edate = date.fromisoformat(edate_str)
            except ValueError:
                continue
            if today < edate <= horizon:
                catalysts.append({
                    "date": edate.isoformat(),
                    "event": f"{t} earnings",
                    "tickers": [t],
                })
    # Deduplicate by ticker.
    seen = set()
    deduped = []
    for c in catalysts:
        key = c["tickers"][0]
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def _build_watchlist_movers(tickers: list[str], snapshot: dict) -> list[dict]:
    """Constituents with |change1w| >= 5%."""
    quotes = snapshot.get("quotes", {}) or {}
    movers = []
    for t in tickers:
        q = quotes.get(t) or {}
        pct = _safe_float(q.get("change1w"))
        if pct is not None and abs(pct) >= 5.0:
            name = q.get("longName") or t
            direction = "up" if pct > 0 else "down"
            movers.append({
                "ticker": t,
                "move_pct": round(pct, 2),
                "reason": f"{name} {direction} {abs(pct):.1f}% over the past week",
            })
    movers.sort(key=lambda x: abs(x["move_pct"]), reverse=True)
    return movers


def build_pack(subsector: str, subsector_map: dict, snapshot: dict,
               calendar: dict) -> tuple[dict, list[str], list[str]]:
    """Build the full benchmark pack for one subsector.

    Returns (pack_dict, sources_list, errors_list).
    """
    errors: list[str] = []
    sources: list[str] = []
    week_of = _iso_monday()

    # 1. Constituents
    try:
        tickers = _build_constituents(subsector, subsector_map, snapshot)
    except Exception as exc:
        errors.append(f"constituents: {exc}")
        tickers = [t for t, s in subsector_map.items() if s == subsector]

    # 2. KPI benchmarks
    kpi = None
    try:
        kpi = _build_kpi_benchmarks(tickers, snapshot)
    except Exception as exc:
        errors.append(f"kpi_benchmarks: {exc}")

    # 3. Winners / losers (5d)
    wl = None
    try:
        wl = _build_winners_losers(tickers, snapshot)
    except Exception as exc:
        errors.append(f"winners_losers_5d: {exc}")

    # 4. Key themes (Perplexity)
    themes = None
    try:
        themes, theme_sources = _build_key_themes(subsector, tickers)
        sources.extend(theme_sources)
    except Exception as exc:
        errors.append(f"key_themes: {exc}")

    # 5. Next-week catalysts
    catalysts = None
    try:
        catalysts = _build_next_week_catalysts(tickers, calendar)
    except Exception as exc:
        errors.append(f"next_week_catalysts: {exc}")

    # 6. Watchlist movers
    movers = None
    try:
        movers = _build_watchlist_movers(tickers, snapshot)
    except Exception as exc:
        errors.append(f"watch_list_movers: {exc}")

    pack = {
        "subsector": subsector,
        "week_of": week_of,
        "summary": None,  # filled by themes or a future LLM pass
        "kpi_benchmarks": kpi,
        "winners_losers_5d": wl,
        "key_themes": themes,
        "next_week_catalysts": catalysts,
        "watch_list_movers": movers,
    }
    if errors:
        pack["errors"] = errors

    # Build a narrative summary from themes if available.
    if themes and isinstance(themes, list) and len(themes) > 0:
        titles = [t.get("theme", "") for t in themes if isinstance(t, dict)]
        if titles:
            pack["summary"] = "; ".join(t for t in titles if t) + "."

    return pack, sources, errors


# ---------------------------------------------------------------------------
# Supabase writer
# ---------------------------------------------------------------------------

def _upsert_to_supabase(subsector: str, week_of: str, pack: dict,
                         sources: list[str]) -> bool:
    """Upsert one row into industry_packs via Supabase REST API.

    Returns True on success, False on failure (logged, never raises).
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print(f"  [WARN] SUPABASE_URL or SUPABASE_SERVICE_KEY not set -- skipping write")
        return False

    import requests
    endpoint = f"{url}/rest/v1/industry_packs"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    row = {
        "subsector": subsector,
        "week_of": week_of,
        "pack": pack,
        "sources": sources or [],
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        resp = requests.post(endpoint, headers=headers, json=row, timeout=30)
        if resp.status_code in (200, 201):
            print(f"  [WRITE] {subsector} / {week_of} -> Supabase (status={resp.status_code})")
            return True
        # 409 Conflict with merge-duplicates should not happen, but handle it.
        print(f"  [ERROR] Supabase upsert failed: {resp.status_code} {resp.text[:300]}")
        return False
    except Exception as exc:
        print(f"  [ERROR] Supabase upsert exception: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run(subsector_filter: str | None = None, dry_run: bool = False):
    """Generate and upsert industry packs.

    Args:
        subsector_filter: If set, only process this subsector.
        dry_run: If True, log the pack JSON but skip the Supabase write.
    """
    week_of = _iso_monday()
    print(f"{'='*60}")
    print(f"Industry Packs Refresh -- week_of={week_of}")
    if dry_run:
        print(f"  [DRY RUN] Supabase writes disabled")
    if subsector_filter:
        print(f"  Filter: {subsector_filter}")
    print(f"{'='*60}")

    subsector_map = load_subsector_map()
    if not subsector_map:
        print("  [ERROR] Could not load subsector map from utils.js")
        return

    all_subsectors = sorted(set(subsector_map.values()))
    if subsector_filter:
        if subsector_filter not in all_subsectors:
            print(f"  [ERROR] Unknown subsector: {subsector_filter}")
            print(f"  Available: {', '.join(all_subsectors)}")
            return
        all_subsectors = [subsector_filter]

    print(f"  Subsectors to process: {len(all_subsectors)}")

    snapshot = _load_snapshot()
    calendar = _load_calendar()

    ok = 0
    fail = 0
    for subsector in all_subsectors:
        print(f"\n--- {subsector} ---")
        try:
            pack, sources, errors = build_pack(subsector, subsector_map,
                                               snapshot, calendar)
            if errors:
                print(f"  [WARN] {len(errors)} non-fatal error(s): {errors}")

            if dry_run:
                print(f"  [DRY RUN] Pack JSON ({len(json.dumps(pack))} chars):")
                print(json.dumps(pack, indent=2, default=str))
            else:
                success = _upsert_to_supabase(subsector, week_of, pack, sources)
                if success:
                    ok += 1
                else:
                    fail += 1
        except Exception as exc:
            print(f"  [ERROR] {subsector} failed: {exc}")
            fail += 1

    print(f"\n{'='*60}")
    if dry_run:
        print(f"Dry run complete. {len(all_subsectors)} subsector(s) processed.")
    else:
        print(f"Done. ok={ok} fail={fail} total={len(all_subsectors)}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Weekly industry benchmark pack generator + Supabase writer.",
    )
    parser.add_argument(
        "--subsector",
        type=str,
        default=None,
        help="Process only this subsector (e.g. Semiconductors).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log pack JSON but skip Supabase writes.",
    )
    args = parser.parse_args()
    run(subsector_filter=args.subsector, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
