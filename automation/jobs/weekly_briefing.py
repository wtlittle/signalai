"""
Weekly briefing generation via sonar-deep-research API.

Runs 3 Perplexity deep-research calls in parallel (value, momentum, trends),
compiles the results into weekly_briefing.json, and archives the output.

Usage:
    python -m automation.jobs.weekly_briefing
    python -m automation.jobs.weekly_briefing --output /tmp/test_briefing.json
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import WEEKLY_BRIEFING
from automation.shared.tickers import load_tickers
from automation.shared.io_helpers import write_json
from automation.shared.cache import save_research_cache, load_research_cache, research_cache_exists
from automation.perplexity.client import call_perplexity
from automation.perplexity.prompts import (
    build_weekly_value_prompt,
    build_weekly_momentum_prompt,
    build_weekly_trends_prompt,
)
from automation.jobs.weekly_briefing_context import build_context_blocks

TODAY = date.today()

# Section-quality tracking log (written by the post-LLM validator AND the cron
# audit step). Lives outside the repo so the cron monitor can tail it Monday AM.
SECTION_QUALITY_LOG = Path("/home/user/workspace/cron_tracking/4277e158/section_quality.log")

# Minimum entry counts the validator enforces on the three regression-prone
# sections. watchlist_updates min is clamped to available movers at runtime.
_SECTION_MINIMUMS = {
    "watchlist_updates": 20,
    "upcoming_catalysts": 6,
    "sector_summary": 8,
}


# --- Structured-output schema for the trends call (advisory for
# sonar-deep-research, but it meaningfully reduces drift to off-spec shapes).
# Passed through call_perplexity via extra_meta["response_format"].
#
# Perplexity requires response_format.json_schema.schema to have a ROOT TYPE OF
# OBJECT ("array" roots are rejected with HTTP 400), so json_schema is only
# applied to the trends call (object). The value and momentum calls return bare
# JSON arrays and therefore rely on strong prompt-engineered JSON requirements
# (see build_weekly_value_prompt / build_weekly_momentum_prompt) instead.
_TRENDS_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"schema": {
        "type": "object",
        "properties": {
            "index_returns": {
                "type": "object",
                "properties": {
                    idx: {
                        "type": "object",
                        "properties": {
                            "close": {"type": ["number", "null"]},
                            "weekly_pct": {"type": ["number", "null"]},
                            "one_month_pct": {"type": ["number", "null"]},
                            "three_month_pct": {"type": ["number", "null"]},
                            "ytd_pct": {"type": ["number", "null"]},
                        },
                    }
                    for idx in ("sp500", "nasdaq", "dow", "russell2000", "vix")
                },
            },
            "market_summary": {"type": ["string", "null"]},
            "key_trends": {"type": "array"},
            "trends": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "theme": {"type": "string"},
                        "summary": {"type": "string"},
                        "tickers": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["theme", "summary"],
                },
            },
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "risk": {"type": "string"},
                        "impact": {"type": "string"},
                    },
                    "required": ["risk", "impact"],
                },
            },
            "watchlist_updates": {"type": "array"},
            "watchlist_movers": {"type": "array"},
            "upcoming_catalysts": {"type": "array"},
            "sector_summary": {"type": "object"},
            "narrative": {"type": "string"},
        },
        "required": ["index_returns", "trends", "risks", "narrative"],
    }},
}


def _salvage_json_array(raw_text: str) -> list | None:
    """Attempt to recover a list of JSON objects from a broken response.

    Tries four strategies in order:
    1. Direct json.loads after cleaning bad escapes
    2. Extract the outermost [...] array substring and parse it
    3. Extract individual {...} objects one by one
    4. Return None if nothing recoverable
    """
    import re as _re

    def _clean_escapes(s: str) -> str:
        return _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)

    def _try(s: str):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None

    for text in (raw_text, _clean_escapes(raw_text)):
        # Strategy 1: direct parse
        result = _try(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "raw" not in result:
            return [result]

        # Strategy 2: extract outermost array
        m = _re.search(r'\[.*\]', text, flags=_re.DOTALL)
        if m:
            result = _try(m.group(0))
            if isinstance(result, list) and result:
                return result

        # Strategy 3: extract individual objects
        objects = []
        for match in _re.finditer(
            r'\{(?:[^{}]|\{[^{}]*\})*\}', text, flags=_re.DOTALL
        ):
            obj = _try(match.group())
            if isinstance(obj, dict) and len(obj) > 1:
                objects.append(obj)
        if objects:
            return objects

    return None


def _extract_list(result) -> list:
    """Safely extract a list from a Perplexity response.

    If the response is a {"raw": ...} fallback (meaning parsing failed in
    client.py), attempt to salvage a list from the raw string before giving up.
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        raw = result.get("raw")
        if isinstance(raw, str) and raw.strip():
            salvaged = _salvage_json_array(raw)
            if salvaged:
                print(f"  [SALVAGE] Recovered {len(salvaged)} objects from raw payload")
                return salvaged
            else:
                print(f"  [SALVAGE FAILED] Could not recover objects from raw payload (len={len(raw)}); first 300 chars: {raw[:300]!r}")
                return []
        if isinstance(raw, list):
            return raw
    return []


def _extract_dict(result) -> dict:
    """Safely extract a dict from a Perplexity response."""
    if isinstance(result, dict) and "raw" not in result:
        return result
    return {}


def _fetch_value():
    """Deep-research call for top 5 value stocks."""
    print("  [1/3] Researching top value stocks...")
    return call_perplexity(
        "MARKET", "weekly_value",
        build_weekly_value_prompt(),
        system="You are a senior buy-side equity research analyst. RESPOND ONLY WITH VALID JSON. Your entire response must be a JSON array starting with [ and ending with ]. Each object MUST contain all 19 value-pick keys including sector, current_price, price, fifty_two_week_high, fifty_two_week_low, key_risks, and why_could_go_lower. No markdown. No prose. No code fences. No explanation. Only JSON.",
        max_tokens=8000,
    )


def _fetch_momentum():
    """Deep-research call for top 5 momentum stocks."""
    print("  [2/3] Researching top momentum stocks...")
    return call_perplexity(
        "MARKET", "weekly_momentum",
        build_weekly_momentum_prompt(),
        system="You are a senior buy-side equity research analyst. RESPOND ONLY WITH VALID JSON. Your entire response must be a JSON array starting with [ and ending with ]. Each object MUST contain all 12 momentum-pick keys. No markdown. No prose. No code fences. No explanation. Only JSON.",
        max_tokens=6000,
    )


def _fetch_trends(tickers: list[str], context: dict | None = None, force: bool = False):
    """Deep-research call for market trends + watchlist movers."""
    print("  [3/3] Researching market trends and watchlist movers...")
    return call_perplexity(
        "MARKET", "weekly_trends",
        build_weekly_trends_prompt(tickers, context=context),
        force=force,
        system="You are a senior market strategist. RESPOND ONLY WITH VALID JSON. Your entire response must be a JSON object starting with { and ending with }. The 'narrative' field must contain a full markdown research report (15000+ chars, ## headers, citations) as an escaped JSON string. key_trends, trends, risks are arrays of objects with both old and new schema keys. watchlist_updates and watchlist_movers are arrays with one entry per material mover supplied in the prompt (minimum 20). upcoming_catalysts is an array of at least 6 entries. sector_summary is an object with EXACTLY the eight subsector keys supplied in the prompt. index_returns is a flat object of numeric fields, never a table. No markdown outside the JSON. No tabs. No prose. No code fences. Only JSON.",
        # sonar-deep-research produces 25-35K narrative + 30-60 watchlist entries; we need
        # significant token headroom. 20000 gives headroom for the full rich output.
        max_tokens=20000,
        extra_meta={"response_format": _TRENDS_RESPONSE_FORMAT},
    )


def _is_zero_or_blank(raw) -> bool:
    """True when a deep-research metric is absent or a useless zero.

    sonar-deep-research frequently returns ``0`` / "" for both revenue_growth and
    the 1W/1M/3M return windows, which then render as "N/A". Treat those as
    missing so we can backfill from yfinance.
    """
    if raw in (None, "", "N/A", "n/a"):
        return True
    try:
        return float(str(raw).replace("%", "").replace("+", "").strip()) == 0.0
    except (TypeError, ValueError):
        return False


def _rev_growth_missing(v) -> bool:
    """True when a pick's revenue_growth is absent or a useless zero."""
    return _is_zero_or_blank(v.get("revenue_growth", v.get("rev_growth")))


def _compute_revenue_growth(ticker: str) -> str | None:
    """Best-available YoY revenue growth for a ticker as a formatted string.

    Prefers trailing-twelve-month growth (last 4 quarters vs the prior 4); falls
    back to the most recent annual YoY. Returns e.g. "+4.2%" / "-2.1%", or None
    if no revenue history is available (extremely rare for a public company).
    """
    try:
        import yfinance as yf
    except Exception:
        return None
    try:
        t = yf.Ticker(ticker)
    except Exception:
        return None

    def _fmt(curr, prior):
        if not curr or not prior or prior == 0:
            return None
        pct = (curr / prior - 1.0) * 100.0
        return f"{'+' if pct >= 0 else ''}{pct:.1f}%"

    # 1) TTM: sum of last 4 quarters vs prior 4 quarters
    try:
        qi = t.quarterly_income_stmt
        if qi is not None and not qi.empty and "Total Revenue" in qi.index:
            row = qi.loc["Total Revenue"].dropna()
            vals = [float(x) for x in row.values]
            if len(vals) >= 8:
                ttm = sum(vals[:4])
                prior_ttm = sum(vals[4:8])
                out = _fmt(ttm, prior_ttm)
                if out:
                    return out
    except Exception:
        pass

    # 2) Fallback: most recent annual YoY
    try:
        ai = t.income_stmt
        if ai is not None and not ai.empty and "Total Revenue" in ai.index:
            row = ai.loc["Total Revenue"].dropna()
            vals = [float(x) for x in row.values]
            if len(vals) >= 2:
                out = _fmt(vals[0], vals[1])
                if out:
                    return out
    except Exception:
        pass

    return None


def _backfill_revenue_growth(picks: list) -> None:
    """Fill in missing revenue_growth on a list of picks using yfinance (in place).

    weekly_briefing.json is static client data, so the card renderer has no live
    fundamentals to fall back on. We resolve revenue growth here at generation
    time so a public-company card never shows "N/A". Shared by both value and
    momentum picks.
    """
    for v in picks:
        ticker = (v.get("ticker") or "").strip()
        if not ticker or not _rev_growth_missing(v):
            continue
        computed = _compute_revenue_growth(ticker)
        if computed:
            v["revenue_growth"] = computed
            print(f"  [rev-growth] backfilled {ticker}: {computed}")
        else:
            print(f"  [rev-growth] no revenue history for {ticker}; left as-is")


# Trailing-return windows in trading days: 1 week (~5), 1 month (~21), 3 months (~63).
_RETURN_WINDOWS = (
    ("one_week_perf", 5),
    ("one_month_perf", 21),
    ("three_month_perf", 63),
)


def _compute_momentum_returns(ticker: str) -> dict:
    """Trailing 1W/1M/3M price returns for a ticker as formatted strings.

    Uses ~6 months of daily closes from yfinance and compares the latest close
    to the close N trading days back (5/21/63). Returns a dict keyed by
    one_week_perf / one_month_perf / three_month_perf, e.g. "+4.2%" / "-2.1%".
    Windows with insufficient history are omitted (extremely rare for a public
    US ticker with active trading history).
    """
    try:
        import yfinance as yf
    except Exception:
        return {}
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
    except Exception:
        return {}
    if hist is None or hist.empty or "Close" not in hist.columns:
        return {}

    closes = [float(x) for x in hist["Close"].dropna().values]
    if len(closes) < 2:
        return {}

    latest = closes[-1]
    out = {}
    for field, lookback in _RETURN_WINDOWS:
        if len(closes) <= lookback:
            continue
        prior = closes[-(lookback + 1)]
        if not prior:
            continue
        pct = (latest / prior - 1.0) * 100.0
        out[field] = f"{'+' if pct >= 0 else ''}{pct:.1f}%"
    return out


def _backfill_momentum_returns(momentum_picks: list) -> None:
    """Fill in missing 1W/1M/3M returns on momentum picks using yfinance (in place).

    Deep-research ranks momentum names by recent performance but often emits ``0``
    for the displayed return fields, so the card renders "1W/1M/3M: N/A". We
    recompute the trailing windows from price history at generation time.
    """
    for m in momentum_picks:
        ticker = (m.get("ticker") or "").strip()
        if not ticker:
            continue
        needed = [
            field for field, _ in _RETURN_WINDOWS
            if _is_zero_or_blank(m.get(field))
        ]
        if not needed:
            continue
        computed = _compute_momentum_returns(ticker)
        if not computed:
            print(f"  [returns] no price history for {ticker}; left as-is")
            continue
        for field in needed:
            if field in computed:
                m[field] = computed[field]
        print(
            f"  [returns] backfilled {ticker}: "
            + ", ".join(f"{f}={computed[f]}" for f in needed if f in computed)
        )


def _normalize_value_pick(v: dict) -> dict:
    """Ensure value pick has both old-schema and new-schema field names.

    Old schema (5/03-5/22): current_price, fifty_two_week_high, fifty_two_week_low, key_risks, sector
    New schema (5/31+): price, 52_week_high, 52_week_low, why_could_go_lower
    We write BOTH so the renderer handles either schema without breaking.
    """
    out = dict(v)
    # price aliases
    cp = out.get("current_price") or out.get("price")
    out["current_price"] = cp
    out["price"] = cp
    # 52-week aliases
    hi = out.get("fifty_two_week_high") or out.get("52_week_high")
    lo = out.get("fifty_two_week_low") or out.get("52_week_low")
    out["fifty_two_week_high"] = hi
    out["fifty_two_week_low"] = lo
    out["52_week_high"] = hi
    out["52_week_low"] = lo
    # key_risks / why_could_go_lower aliases
    kr = out.get("key_risks") or out.get("why_could_go_lower")
    out["key_risks"] = kr
    out["why_could_go_lower"] = kr
    return out


def _normalize_trends_entry(t: dict) -> dict:
    """Ensure trend entry has both old-schema (rank, title, detail) and new-schema (theme, summary, tickers)."""
    out = dict(t)
    # title / theme aliases
    title = out.get("title") or out.get("theme") or out.get("name") or ""
    out["title"] = title
    out["theme"] = title
    # detail / summary aliases
    detail = out.get("detail") or out.get("summary") or out.get("description") or ""
    out["detail"] = detail
    out["summary"] = detail
    # ensure rank exists
    if "rank" not in out:
        out["rank"] = None
    # ensure tickers exists
    if "tickers" not in out:
        out["tickers"] = []
    return out


def _normalize_risk_entry(r: dict) -> dict:
    """Ensure risk entry has both old-schema (rank, title, detail) and new-schema (risk, impact)."""
    out = dict(r)
    title = out.get("title") or out.get("risk") or out.get("name") or ""
    out["title"] = title
    out["risk"] = title
    detail = out.get("detail") or out.get("impact") or out.get("description") or ""
    out["detail"] = detail
    out["impact"] = detail
    if "rank" not in out:
        out["rank"] = None
    return out


def _section_count(briefing: dict, section: str) -> int:
    """Count entries in a briefing section (list len, or dict key count)."""
    v = briefing.get(section)
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        return len(v)
    return 0


def _log_section_quality(briefing: dict, context: dict | None, stage: str) -> dict:
    """Append per-section counts vs. minimums to SECTION_QUALITY_LOG.

    Returns a dict {section: {count, minimum, ok}} for the three tracked sections.
    Never raises — quality logging must not break briefing generation.
    """
    mins = dict(_SECTION_MINIMUMS)
    if context and context.get("min_watchlist"):
        # Can't write more watchlist entries than we have movers for.
        mins["watchlist_updates"] = context["min_watchlist"]

    report = {}
    for section, minimum in mins.items():
        count = _section_count(briefing, section)
        report[section] = {"count": count, "minimum": minimum, "ok": count >= minimum}

    try:
        SECTION_QUALITY_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        week = briefing.get("week_ending") or TODAY.isoformat()
        with open(SECTION_QUALITY_LOG, "a") as fh:
            for section, r in report.items():
                level = "OK" if r["ok"] else "WARN"
                fh.write(
                    f"{ts} [{level}] week={week} stage={stage} "
                    f"{section}={r['count']} (min {r['minimum']})\n"
                )
    except Exception as exc:
        print(f"  [section-quality] log write failed: {exc}")

    for section, r in report.items():
        if not r["ok"]:
            print(f"  [SECTION QUALITY WARN] {section}: {r['count']} < min {r['minimum']}")
    return report


def _failing_sections(report: dict) -> list[str]:
    return [s for s, r in report.items() if not r["ok"]]


def compile_briefing(value, momentum, trends) -> dict:
    """Merge 3 API responses into the weekly_briefing.json schema.

    Outputs the full rich schema with BOTH old and new field name aliases so that
    both the current renderer and any backward-looking archive code continue to work.
    Target size: 80KB+ (comparable to 5/22 at 85KB).
    """
    value_picks = _extract_list(value)
    momentum_picks = _extract_list(momentum)
    trends_data = _extract_dict(trends)

    if not value_picks:
        print("  [WARNING] value_picks is EMPTY — value section will be absent from this week's briefing. Check the raw cache for the weekly_value response.")
    if not momentum_picks:
        print("  [WARNING] momentum_picks is EMPTY — momentum section will be absent from this week's briefing. Check the raw cache for the weekly_momentum response.")
    if not trends_data:
        print("  [WARNING] trends_data is EMPTY — narrative, index_returns, trends, and risks will be absent. Check the raw cache for the weekly_trends response.")

    # Normalize picks to carry both old and new field names
    value_picks = [_normalize_value_pick(v) for v in value_picks]
    momentum_picks = [_normalize_value_pick(m) for m in momentum_picks]

    # Resolve revenue growth for any pick where deep-research left it missing or
    # zero, so cards never render "Rev Growth: N/A" (shared by value + momentum).
    _backfill_revenue_growth(value_picks)
    _backfill_revenue_growth(momentum_picks)

    # Recompute trailing 1W/1M/3M returns for momentum picks from price history
    # so cards never render "1W/1M/3M: N/A".
    _backfill_momentum_returns(momentum_picks)

    # Pull trends and risks, normalizing to dual-schema objects
    raw_trends = trends_data.get("trends", []) or trends_data.get("key_trends", [])
    raw_key_trends = trends_data.get("key_trends", []) or raw_trends
    normalized_trends = [_normalize_trends_entry(t) for t in raw_trends if isinstance(t, dict)]
    normalized_key_trends = [_normalize_trends_entry(t) for t in raw_key_trends if isinstance(t, dict)]
    # If key_trends came back empty but trends has data, copy it over
    if not normalized_key_trends and normalized_trends:
        normalized_key_trends = normalized_trends

    raw_risks = trends_data.get("risks", [])
    normalized_risks = [_normalize_risk_entry(r) for r in raw_risks if isinstance(r, dict)]

    # Watchlist: prefer the new watchlist_updates field (richer), fall back to watchlist_movers
    watchlist_updates = trends_data.get("watchlist_updates", []) or []
    watchlist_movers = trends_data.get("watchlist_movers", []) or []
    # If only movers came back, synthesize watchlist_updates from them
    if watchlist_updates and not watchlist_movers:
        watchlist_movers = [
            {
                "ticker": u.get("ticker"),
                "weekly_move": ("+" if (u.get("weekly_change_pct") or 0) >= 0 else "") + str(u.get("weekly_change_pct", "0")) + "%",
                "thirty_day_move": ("+" if (u.get("thirty_day_change_pct") or 0) >= 0 else "") + str(u.get("thirty_day_change_pct", "0")) + "%",
                "catalyst": u.get("headline", ""),
                "detail": u.get("summary", ""),
            }
            for u in watchlist_updates if isinstance(u, dict)
        ]
    elif watchlist_movers and not watchlist_updates:
        watchlist_updates = [
            {
                "ticker": m.get("ticker"),
                "weekly_change_pct": None,
                "thirty_day_change_pct": None,
                "headline": m.get("catalyst") or m.get("detail", ""),
                "summary": m.get("detail", ""),
                "tags": [],
            }
            for m in watchlist_movers if isinstance(m, dict)
        ]

    print(f"  Trends: {len(normalized_key_trends)}, Risks: {len(normalized_risks)}, "
          f"Watchlist updates: {len(watchlist_updates)}, Watchlist movers: {len(watchlist_movers)}")

    return {
        "generated": datetime.now(tz=__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week_ending": TODAY.isoformat(),
        # Market summary: prefer string form; old schema had it as a dict with .narrative
        "market_summary": trends_data.get("market_summary", ""),
        # Value / momentum picks
        "value_picks": value_picks,
        "momentum_picks": momentum_picks,
        # Index returns
        "index_returns": trends_data.get("index_returns", {}),
        # Trends — both old-schema (key_trends) and new-schema (trends) names
        "key_trends": normalized_key_trends,
        "trends": normalized_trends,
        # Risks — normalized to carry both old and new field names
        "risks": normalized_risks,
        # Watchlist — both old (watchlist_updates) and new (watchlist_movers) names
        "watchlist_updates": watchlist_updates,
        "watchlist_movers": watchlist_movers,
        # New rich fields
        "upcoming_catalysts": trends_data.get("upcoming_catalysts", []),
        "sector_summary": trends_data.get("sector_summary", {}),
        # Deep narrative
        "narrative": trends_data.get("narrative", ""),
        # market_intel_lookup: populated by run() after compile; placeholder here
        "market_intel_lookup": {},
    }


def run(output_path: Path | None = None) -> dict:
    """Main entry: generate weekly market briefing with parallel deep-research calls."""
    tickers = load_tickers()
    out = output_path or WEEKLY_BRIEFING
    print(f"Generating weekly briefing for week ending {TODAY.isoformat()}...")
    print(f"Output: {out}")

    # Precompute concrete-data blocks (movers / earnings / subsectors) so the
    # trends prompt can be grounded in real names — the fix for the 6/07 regression
    # where watchlist_updates/upcoming_catalysts/sector_summary came back empty.
    context = build_context_blocks(TODAY)
    print(f"  Context: {len(context['movers'])} movers, "
          f"{len(context['earnings'])} earnings, "
          f"{sum(1 for b in context['buckets'].values() if b)} subsectors")

    # --- Run 3 deep-research calls in parallel ---
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_value = pool.submit(_fetch_value)
        future_momentum = pool.submit(_fetch_momentum)
        future_trends = pool.submit(_fetch_trends, tickers, context)

        value = future_value.result()
        momentum = future_momentum.result()
        trends = future_trends.result()

    # --- Compile ---
    print("\nCompiling weekly_briefing.json...")
    briefing = compile_briefing(value, momentum, trends)

    # --- Post-LLM section-quality validation + one targeted retry ---
    report = _log_section_quality(briefing, context, stage="initial")
    failing = _failing_sections(report)
    if failing:
        print(f"  [RETRY] Sections below minimum: {failing}. Re-running trends call once...")
        try:
            retry_trends = _fetch_trends(tickers, context, force=True)
            retry_briefing = compile_briefing(value, momentum, retry_trends)
            retry_report = _log_section_quality(retry_briefing, context, stage="retry")
            # Keep the retry only if it improved the failing sections in aggregate.
            before = sum(report[s]["count"] for s in failing)
            after = sum(retry_report[s]["count"] for s in failing)
            if after > before:
                print(f"  [RETRY] Improved failing sections {before} -> {after}; keeping retry.")
                briefing = retry_briefing
            else:
                print(f"  [RETRY] No improvement ({before} -> {after}); keeping original.")
        except Exception as exc:
            print(f"  [RETRY] failed: {exc}; keeping original briefing.")

    # --- Inject market_intel_lookup (per-ticker TAM/CAGR/competitors from Supabase) ---
    try:
        from automation.shared.supabase_client import fetch_rows as _fetch_rows
        mi_rows = _fetch_rows("market_intel_ticker", params={"limit": "500"})
        mi_map = {r["ticker"]: r for r in mi_rows if "ticker" in r}
        all_tickers = set()
        for pick in briefing.get("value_picks", []) + briefing.get("momentum_picks", []):
            t = (pick.get("ticker") or "").strip().upper()
            if t:
                all_tickers.add(t)
        briefing["market_intel_lookup"] = {
            t: {
                "tam_usd_bn": mi_map[t].get("tam_usd_bn"),
                "tam_source_url": mi_map[t].get("tam_source_url"),
                "category_cagr_pct": mi_map[t].get("category_cagr_pct"),
                "drivers": mi_map[t].get("drivers") or [],
                "competitors": mi_map[t].get("competitors") or [],
            }
            for t in all_tickers if t in mi_map
        }
        found = len(briefing["market_intel_lookup"])
        print(f"  market_intel_lookup: {found}/{len(all_tickers)} tickers enriched")
    except Exception as exc:
        print(f"  [market_intel_lookup] failed: {exc}; leaving empty dict")
        briefing.setdefault("market_intel_lookup", {})

    # --- Inject deterministic tl_dr field (removes client-side synthesis dependence) ---
    try:
        from automation.jobs.backfill_tldr import inject_tl_dr as _inject_tl_dr
        briefing = _inject_tl_dr(briefing, force=True)
        print(f"  tl_dr: {len(briefing.get('tl_dr', []))} bullets")
    except Exception as exc:
        print(f"  [tl_dr] failed: {exc}")

    write_json(out, briefing)
    print(f"  Saved to {out}")

    # Auto-archive (only when writing to the default location)
    if out == WEEKLY_BRIEFING:
        try:
            from automation.jobs.backfill_briefings import save_archive_briefing, patch_index_only
            save_archive_briefing(TODAY, briefing)
            patch_index_only()
        except Exception as exc:
            print(f"  [weekly_briefing] archive failed: {exc}")

    print(f"  Value picks: {len(briefing['value_picks'])}")
    print(f"  Momentum picks: {len(briefing['momentum_picks'])}")
    print(f"  Trends: {len(briefing['trends'])}")

    # Emit subscriber alert
    if out == WEEKLY_BRIEFING:
        try:
            from automation.alerts import emit_alert
            wk = briefing.get("week_ending") or TODAY.isoformat()
            narrative = (briefing.get("narrative") or "Fresh weekly briefing available").strip()
            if len(narrative) > 180:
                narrative = narrative[:177] + "..."
            emit_alert(
                alert_type="weekly_briefing",
                summary=f"Week ending {wk}: {narrative}",
                ticker=None,
                severity="info",
                link="https://www.perplexity.ai/computer/a/stock-watchlist-terminal-qBSMi5vnQ1OezaO1hksi5A",
                extra={
                    "week_ending": wk,
                    "value_count": len(briefing["value_picks"]),
                    "momentum_count": len(briefing["momentum_picks"]),
                },
            )
        except Exception as exc:
            print(f"  [weekly_briefing] emit_alert failed: {exc}")

    return briefing


def main():
    parser = argparse.ArgumentParser(description="Generate weekly market briefing via sonar-deep-research")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output path for weekly_briefing.json (default: repo weekly_briefing.json)")
    parser.add_argument("--week-ending", type=str, default=None,
                        help="Regenerate a specific past week (YYYY-MM-DD); overrides today's date")
    args = parser.parse_args()
    if args.week_ending:
        global TODAY
        TODAY = date.fromisoformat(args.week_ending)
    output_path = Path(args.output) if args.output else None
    run(output_path=output_path)


if __name__ == "__main__":
    main()
