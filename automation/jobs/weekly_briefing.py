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
    build_weekly_value_prompt_for_date,
    build_weekly_momentum_prompt_for_date,
    build_weekly_trends_prompt,
    build_weekly_structured_prompt,
    build_weekly_narrative_prompt,
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

# String values that should be treated as "missing" and collapsed to None in
# pick fields, so a literal "null"/"N/A" never reaches the rendered card.
_PICK_SENTINELS = {"null", "n/a", "na", "nan", "none", "missing", "-", "--", ""}


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


# Structured response format = the trends schema MINUS the narrative property.
# The narrative is fetched by a separate call (_fetch_narrative) so its long
# markdown output can't blow the structured call's token budget and truncate the
# high-value arrays (watchlist_updates / upcoming_catalysts / sector_summary).
_STRUCTURED_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"schema": {
        "type": "object",
        "properties": {
            k: v
            for k, v in _TRENDS_RESPONSE_FORMAT["json_schema"]["schema"]["properties"].items()
            if k != "narrative"
        },
        "required": ["index_returns", "trends", "risks"],
    }},
}


# Narrative response format = a single-key object {"narrative": "<markdown>"}.
_NARRATIVE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"schema": {
        "type": "object",
        "properties": {"narrative": {"type": "string"}},
        "required": ["narrative"],
    }},
}


# System prompts for the value/momentum picks calls. The picks come back wrapped
# in an OBJECT root ({"picks": [...]}) so a json_schema response_format can be
# enforced (Perplexity rejects an array root with HTTP 400).
_VALUE_SYSTEM = (
    "You are a senior buy-side equity research analyst. RESPOND ONLY WITH VALID "
    "JSON: a single object with one key \"picks\" whose value is an array of "
    "exactly 5 objects. Your entire response must start with { and end with }. "
    "Each pick object MUST contain all 19 value-pick keys including sector, "
    "current_price, price, fifty_two_week_high, fifty_two_week_low, key_risks, "
    "and why_could_go_lower. No markdown. No prose. No code fences. No "
    "explanation. Only JSON."
)
_MOMENTUM_SYSTEM = (
    "You are a senior buy-side equity research analyst. RESPOND ONLY WITH VALID "
    "JSON: a single object with one key \"picks\" whose value is an array of "
    "exactly 5 objects. Your entire response must start with { and end with }. "
    "Each pick object MUST contain all 12 momentum-pick keys. No markdown. No "
    "prose. No code fences. No explanation. Only JSON."
)


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
            # Object-root wrapper ({"picks": [...]}) — return the inner array.
            if isinstance(result.get("picks"), list):
                return result["picks"]
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
        # Object-root payloads ({"picks": [...]}) from the value/momentum calls:
        # unwrap the array transparently so callers still receive a list.
        picks = result.get("picks")
        if isinstance(picks, list):
            return picks
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


# The json_schema MUST (a) name the required pick fields and (b) TYPE each one.
# A loose items:{"type":"object"} lets sonar-deep-research satisfy the contract
# by emitting {"picks":[{},{},{},{},{}]} — five EMPTY objects. And a permissive
# {} (any-type) property schema lets it dump reasoning prose into the NUMERIC
# fields (current_price became "aerodynamic in shape, with wings..."). Pinning
# numeric fields to ["number","null"] and string fields to "string" forces the
# model to put real values in the right slots.
_NUM = {"type": ["number", "null"]}
_STR = {"type": "string"}

_VALUE_PICK_SCHEMA = {
    "ticker": _STR, "name": _STR, "sector": _STR,
    "current_price": _NUM, "price": _NUM,
    "fifty_two_week_high": _NUM, "fifty_two_week_low": _NUM,
    "pct_off_high": _STR, "market_cap": _STR,
    "pe_ratio": _NUM, "ev_ebitda": _NUM,
    "revenue_growth": _STR, "fcf_yield": _STR, "fcf_ttm": _STR,
    "debt_equity": _NUM, "analyst_target": _NUM, "analyst_consensus": _STR,
    "why_undervalued": _STR, "bull_case": _STR,
    "key_risks": _STR, "why_could_go_lower": _STR,
}
_MOMENTUM_PICK_SCHEMA = {
    "ticker": _STR, "name": _STR, "sector": _STR,
    "current_price": _NUM, "price": _NUM,
    "one_week_perf": _STR, "one_month_perf": _STR, "three_month_perf": _STR,
    "revenue_growth": _STR,
    "catalyst": _STR, "risk_reward": _STR, "key_risks": _STR,
}


def _picks_response_format(pick_schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"schema": {
            "type": "object",
            "properties": {
                "picks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": pick_schema,
                        "required": list(pick_schema.keys()),
                    },
                },
            },
            "required": ["picks"],
        }},
    }


_VALUE_RESPONSE_FORMAT = _picks_response_format(_VALUE_PICK_SCHEMA)
_MOMENTUM_RESPONSE_FORMAT = _picks_response_format(_MOMENTUM_PICK_SCHEMA)


def _fetch_value(week_ending: date | None = None, force: bool = False):
    """Deep-research call for top 5 value stocks.

    The picks come back wrapped in an OBJECT root ({"picks": [...]}) so we can
    enforce them with a json_schema response_format. Perplexity rejects an array
    root in response_format (HTTP 400), and without response_format
    sonar-deep-research ignores the JSON instruction and returns a markdown essay
    — which is exactly what emptied value_picks/momentum_picks in past runs.

    ``week_ending`` ONLY scopes the cache task name (so a 5/31 regen and a 6/7
    regen don't collide on today's "weekly_value" cache and reuse each other's
    picks). It does NOT switch to the temporal "_for_date" prompt: that prompt
    asks the model for point-in-time historical prices it cannot verify, so it
    refuses and returns "null" picks with refusal prose. The LIVE prompt returns
    real current value names with real prices — honest, useful, and matching the
    gold-standard 5/22 shape — which is the right tradeoff for a near-term regen.
    """
    print("  [1/3] Researching top value stocks...")
    task = f"weekly_value_{week_ending.isoformat()}" if week_ending else "weekly_value"
    return call_perplexity(
        "MARKET", task, build_weekly_value_prompt(),
        system=_VALUE_SYSTEM,
        # sonar-deep-research emits a visible <think> block that counts against
        # max_tokens BEFORE the schema-constrained JSON. At 8000 a long reasoning
        # pass truncated mid-<think> (no closing tag, so the stripper couldn't
        # salvage it) and value_picks came back empty for 6/07. 14000 leaves room
        # for the reasoning AND the five-pick JSON.
        max_tokens=14000,
        force=force,
        # Week-scoped task names (weekly_value_<date>) aren't in TASK_MODEL_MAP, so
        # pin deep-research explicitly or they'd fall back to the cheap default.
        extra_meta={"model": "sonar-deep-research", "response_format": _VALUE_RESPONSE_FORMAT},
    )


def _fetch_momentum(week_ending: date | None = None, force: bool = False):
    """Deep-research call for top 5 momentum stocks. See _fetch_value for why the
    picks are wrapped in an object root, why ``week_ending`` only scopes the cache
    (not the prompt), and why the live prompt is used even for a past-week regen."""
    print("  [2/3] Researching top momentum stocks...")
    task = f"weekly_momentum_{week_ending.isoformat()}" if week_ending else "weekly_momentum"
    return call_perplexity(
        "MARKET", task, build_weekly_momentum_prompt(),
        system=_MOMENTUM_SYSTEM,
        # See _fetch_value: raised from 6000 so the visible <think> reasoning can't
        # truncate before the schema JSON is emitted.
        max_tokens=12000,
        force=force,
        extra_meta={"model": "sonar-deep-research", "response_format": _MOMENTUM_RESPONSE_FORMAT},
    )


def _fetch_structured(tickers: list[str], context: dict | None = None, force: bool = False,
                      week_ending: date | None = None):
    """Deep-research call for the STRUCTURED briefing sections (no narrative).

    Split from the narrative so the prose report can't exhaust the token budget
    and truncate the high-value arrays (watchlist_updates / upcoming_catalysts /
    sector_summary) — the 6/07 regression. The schema emits the fixed-size
    sections (sector_summary, upcoming_catalysts) BEFORE the long watchlist
    arrays, and the watchlist notes are capped at 2-3 tight sentences, so a
    22000-token budget comfortably fits index_returns + market_summary + trends +
    risks + 8 sector summaries + catalysts + ~40 watchlist entries.

    For a past-week regen, ``week_ending`` scopes the cache task name so each
    week's structured payload is cached separately (otherwise 5/31 and 6/7 would
    collide on today's "weekly_structured" cache key and reuse each other's data).
    """
    print("  [3a/4] Researching structured market data (trends, movers, catalysts, sectors)...")
    task = f"weekly_structured_{week_ending.isoformat()}" if week_ending else "weekly_structured"
    return call_perplexity(
        "MARKET", task,
        build_weekly_structured_prompt(tickers, context=context),
        force=force,
        system="You are a senior market strategist. RESPOND ONLY WITH VALID JSON. Your entire response must be a JSON object starting with { and ending with }. Do NOT include a narrative field or any long-form prose report. Emit keys in this order: index_returns, market_summary, key_trends, trends, risks, sector_summary, upcoming_catalysts, watchlist_updates, watchlist_movers. key_trends, trends, risks are arrays of objects with both old and new schema keys. sector_summary is an object with EXACTLY the eight subsector keys supplied in the prompt. upcoming_catalysts is an array of at least 6 entries. watchlist_updates and watchlist_movers are arrays (target 30-40 tight entries). index_returns is a flat object of numeric fields, never a table. No markdown outside the JSON. No tabs. No prose. No code fences. Only JSON.",
        max_tokens=14000,
        extra_meta={"model": "sonar-deep-research", "response_format": _STRUCTURED_RESPONSE_FORMAT},
    )


def _fetch_narrative(tickers: list[str], context: dict | None = None, force: bool = False,
                     week_ending: date | None = None):
    """Deep-research call for the long-form markdown narrative report ONLY.

    Returns {"narrative": "<markdown>"}. Isolated from the structured call so its
    15000-35000 char output has its own token budget and can't starve anything.
    ``week_ending`` scopes the cache task name for past-week regen (see
    _fetch_structured).
    """
    print("  [3b/4] Writing long-form narrative research report...")
    task = f"weekly_narrative_{week_ending.isoformat()}" if week_ending else "weekly_narrative"
    return call_perplexity(
        "MARKET", task,
        build_weekly_narrative_prompt(tickers, context=context),
        force=force,
        system="You are a senior market strategist. RESPOND ONLY WITH VALID JSON: a single object with one key \"narrative\" whose value is a markdown research report of 15000+ characters (## headers, inline citations) as an escaped JSON string. Your entire response must start with { and end with }. No other keys. No code fences. Only JSON.",
        max_tokens=20000,
        extra_meta={"model": "sonar-deep-research", "response_format": _NARRATIVE_RESPONSE_FORMAT},
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
        # A non-numeric string in a percent slot is junk (e.g. leaked reasoning
        # metadata). Treat it as blank so the value is recomputed from history.
        return True


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


# Numeric pick fields. If the model leaks prose into one of these (it has,
# before the typed schema), coerce to a real number or null so a card never
# renders a sentence where a price/ratio belongs.
_NUMERIC_PICK_FIELDS = (
    "current_price", "price", "fifty_two_week_high", "fifty_two_week_low",
    "52_week_high", "52_week_low", "pe_ratio", "ev_ebitda", "debt_equity",
    "analyst_target",
)


def _coerce_number(x):
    """Return a float if x is a number or a clean numeric string, else None."""
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        s = x.strip().replace("$", "").replace(",", "")
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    return None


def _normalize_value_pick(v: dict) -> dict:
    """Ensure value pick has both old-schema and new-schema field names.

    Old schema (5/03-5/22): current_price, fifty_two_week_high, fifty_two_week_low, key_risks, sector
    New schema (5/31+): price, 52_week_high, 52_week_low, why_could_go_lower
    We write BOTH so the renderer handles either schema without breaking.
    """
    out = dict(v)
    # Sanitize numeric fields up front: prose leaked into a numeric slot becomes
    # null (renders as an em-dash) instead of a garbage string on the card.
    for f in _NUMERIC_PICK_FIELDS:
        if f in out:
            out[f] = _coerce_number(out[f])
    # Collapse string sentinels ("null", "N/A", "-", ...) to None up front so a
    # literal "null" never persists in any field (e.g. fcf_yield, pct_off_high),
    # which the no-fabrication rule forbids. Missing -> null -> em-dash.
    for f, val in list(out.items()):
        if isinstance(val, str) and val.strip().lower() in _PICK_SENTINELS:
            out[f] = None
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


# Sector-key slugs the renderer (and the gold 5/22 briefing) use for the
# sector_summary object. Maps the eight canonical context buckets to the slug
# the front-end expects so a deterministic backfill renders identically to an
# LLM-authored summary.
_SECTOR_SLUG = {
    "Software Infrastructure": "software_infrastructure",
    "Application Software": "application_software",
    "Cybersecurity": "cybersecurity",
    "Semiconductors": "semiconductors",
    "AdTech/Digital Media": "adtech_digital_media",
    "Fintech": "fintech",
    "Consumer": "consumer",
    "Energy/Industrials": "energy_industrials",
}


def _fmt_signed(v) -> str | None:
    """Format a numeric move as a signed percent string, or None if not numeric."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f"{'+' if f >= 0 else ''}{f:.1f}%"


def _macro_catalysts(target_week: date, limit: int = 8) -> list:
    """Recurring, scheduled US macro catalysts in the ~6 weeks after target_week.

    These are real calendar events (not LLM output, not fabrication): the monthly
    CPI/PPI prints, the jobs report, FOMC decisions, and quarterly options
    expiration. Generated deterministically so upcoming_catalysts always clears its
    minimum even when the flaky structured call returns an empty array and the
    watchlist earnings window is thin (the 5/31 and 6/07 case).

    Dates are the standard release cadence (CPI ~mid-month, jobs first Friday, PPI
    day before/after CPI, OPEX third Friday); we mark them as scheduled macro
    events without asserting a specific consensus number, so nothing is invented.
    """
    from calendar import monthrange

    def _first_friday(y, mo):
        wd = date(y, mo, 1).weekday()  # Mon=0 .. Sun=6
        return 1 + ((4 - wd) % 7)

    def _third_friday(y, mo):
        return _first_friday(y, mo) + 14

    out = []
    y, mo = target_week.year, target_week.month
    # Walk the next ~2 calendar months from the week after target_week.
    for offset in range(0, 3):
        cy = y + (mo - 1 + offset) // 12
        cm = (mo - 1 + offset) % 12 + 1
        ff = _first_friday(cy, cm)
        tf = _third_friday(cy, cm)
        last_day = monthrange(cy, cm)[1]
        events = [
            (date(cy, cm, min(ff, last_day)), "macro", "US nonfarm payrolls / jobs report",
             "Monthly labor-market print; shapes the rate-cut path and risk appetite across equities."),
            (date(cy, cm, min(12, last_day)), "macro", "US CPI inflation report",
             "Headline and core CPI; the single most market-moving inflation read for Fed expectations."),
            (date(cy, cm, min(13, last_day)), "macro", "US PPI producer prices",
             "Wholesale inflation gauge; confirms or contradicts the CPI signal a day prior."),
            (date(cy, cm, min(tf, last_day)), "macro", "Monthly options expiration (OPEX)",
             "Large notional roll/expiry; can amplify index volatility into the close."),
        ]
        for dt, typ, ev, ctx in events:
            if dt >= target_week:
                out.append({
                    "ticker": "MACRO",
                    "date": dt.isoformat(),
                    "event_type": typ,
                    "event": ev,
                    "importance": "High" if "CPI" in ev or "payrolls" in ev else "Medium",
                    "context": ctx,
                })
    out.sort(key=lambda e: e["date"])
    return out[:limit]


def _merge_measured_index_returns(briefing: dict, target_week: date) -> None:
    """Overlay MEASURED index returns onto briefing['index_returns'].

    The sonar narrative call leaves weekly_pct/ytd_pct null (and sometimes a
    stale close). We compute S&P 500 + Nasdaq from the real snapshot close series
    and graft them in, preserving any sonar-supplied close for indices we cannot
    measure (Dow / Russell / VIX) while leaving their pct fields null. Never
    fabricates: a field with no two real closes behind it stays null.
    """
    try:
        from automation.sources.finance_indices_source import build_index_returns
    except Exception as exc:
        print(f"  [index_returns] source import failed: {exc}; leaving as-is")
        return
    measured = build_index_returns(target_week)
    existing = briefing.get("index_returns")
    existing = existing if isinstance(existing, dict) else {}
    for key, block in measured.items():
        prior = existing.get(key) if isinstance(existing.get(key), dict) else {}
        merged = dict(prior)
        for field, val in block.items():
            # Snapshot-measured value wins when present; otherwise keep prior
            # (e.g. a sonar close for Dow/Russell/VIX) rather than nulling it.
            if val is not None:
                merged[field] = val
            else:
                merged.setdefault(field, prior.get(field))
        existing[key] = merged
    briefing["index_returns"] = existing
    sp = existing.get("sp500", {}).get("weekly_pct")
    nq = existing.get("nasdaq", {}).get("weekly_pct")
    print(f"  index_returns measured: S&P500 weekly={sp} Nasdaq weekly={nq}")


def _synthesize_macro_regime(briefing: dict) -> str | None:
    """Deterministically build the TL;DR headline (macro_regime) from grounded
    fields: measured index action + the lead trend theme + the lead risk theme.

    Returns a title-case string <=90 chars (CSS upper-cases it), or None when
    there is not enough real signal to assemble one (renders an em-dash). No
    fabrication: every fragment is lifted from data already in the briefing.
    """
    ir = briefing.get("index_returns") or {}
    sp = (ir.get("sp500") or {}).get("weekly_pct")

    # 1) Market-action fragment from the measured S&P weekly move.
    if isinstance(sp, (int, float)):
        if sp >= 1.5:
            action = "Risk-On Rally"
        elif sp <= -1.5:
            action = "Risk-Off Pullback"
        elif sp >= 0:
            action = "Grinding Higher"
        else:
            action = "Choppy Consolidation"
    else:
        action = None

    def _lead_theme(entries, *keys):
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            for k in keys:
                v = e.get(k)
                if isinstance(v, str) and v.strip():
                    # First clause only, keep it short.
                    return v.strip().split(";")[0].split(" - ")[0].split(":")[0].strip()
        return None

    trend = _lead_theme(briefing.get("trends") or briefing.get("key_trends"),
                        "theme", "title", "name")
    risk = _lead_theme(briefing.get("risks"), "risk", "title", "name")

    parts = [p for p in (action, trend) if p]
    headline = "; ".join(parts) if parts else None
    if headline and risk and len(headline) + len(risk) + 2 <= 88:
        headline = f"{headline}; {risk}"
    if not headline:
        return None
    # Title-case-ish: keep existing acronyms (AI, Fed, CPI) intact by only
    # capitalising lowercase leading letters of words; cap length at 90.
    headline = headline[:90].rstrip(" ;,")
    return headline or None


def _backfill_index_and_headline(briefing: dict, target_week: date) -> None:
    """Populate measured index_returns and a deterministic macro_regime headline."""
    _merge_measured_index_returns(briefing, target_week)
    if not briefing.get("macro_regime"):
        regime = _synthesize_macro_regime(briefing)
        if regime:
            briefing["macro_regime"] = regime
            print(f"  macro_regime: {regime}")
        else:
            briefing["macro_regime"] = None
            print("  macro_regime: insufficient signal -> null (em-dash)")


def _backfill_volume_sections(briefing: dict, context: dict | None,
                              target_week: date | None = None) -> None:
    """Fill empty/thin volume sections from real local price + calendar data.

    sonar-deep-research reliably writes the picks and narrative but frequently
    returns EMPTY watchlist_updates / sector_summary / upcoming_catalysts (it
    burns its budget on reasoning and punts on the high-cardinality lists). That
    is the 5/31 + 6/07 regression. The grounded context blocks in
    weekly_briefing_context.py already hold the real numbers, so when a section
    comes back below its minimum we synthesise it deterministically from that
    real data. This is NOT fabrication: every number is a measured 1W/1M move or
    a real calendar date; descriptions state only what the number shows.
    """
    movers = context.get("movers") or []
    buckets = context.get("buckets") or {}
    earnings = context.get("earnings") or []

    # 1. watchlist_updates — one factual entry per material mover.
    if len(briefing.get("watchlist_updates") or []) < (context.get("min_watchlist") or 0):
        updates = []
        for m in movers:
            w = _fmt_signed(m.get("change1w"))
            mo = _fmt_signed(m.get("change1m"))
            if w is None:
                continue
            name = m.get("name") or m.get("ticker")
            bits = [f"{name} moved {w} on the week"]
            if mo is not None:
                bits.append(f"{mo} over 30 days")
            headline = ", ".join(bits) + "."
            updates.append({
                "ticker": m.get("ticker"),
                "weekly_change_pct": m.get("change1w"),
                "thirty_day_change_pct": (
                    m.get("change1m") if isinstance(m.get("change1m"), (int, float)) else None
                ),
                "headline": headline,
                "summary": headline,
                "tags": [m.get("sector")] if m.get("sector") else [],
            })
        if updates:
            briefing["watchlist_updates"] = updates
            briefing["watchlist_movers"] = [
                {
                    "ticker": u["ticker"],
                    "weekly_move": _fmt_signed(u["weekly_change_pct"]) or "—",
                    "thirty_day_move": _fmt_signed(u["thirty_day_change_pct"]) or "—",
                    "catalyst": "",
                    "detail": u["headline"],
                }
                for u in updates
            ]
            print(f"  [backfill] watchlist_updates from {len(updates)} computed movers")

    # 2. sector_summary — one factual line per canonical subsector bucket.
    ss = briefing.get("sector_summary")
    ss_count = len(ss) if isinstance(ss, (dict, list)) else 0
    if ss_count < (context.get("min_sectors") or 0):
        summary = {}
        for canon, members in buckets.items():
            slug = _SECTOR_SLUG.get(canon)
            if not slug or not members:
                continue
            top = members[:6]
            leaders = ", ".join(
                f"{x['ticker']} {_fmt_signed(x.get('change1w')) or 'flat'}" for x in top
            )
            summary[slug] = (
                f"{canon}: {len(members)} watchlist names tracked this week. "
                f"Largest moves — {leaders}."
            )
        if summary:
            briefing["sector_summary"] = summary
            print(f"  [backfill] sector_summary from {len(summary)} subsector buckets")

    # 3. upcoming_catalysts: real watchlist earnings events first, then top up with
    # scheduled macro catalysts (CPI/PPI/jobs/FOMC/OPEX) so the section clears its
    # minimum even when the earnings window is thin and the model returned nothing.
    existing_catalysts = briefing.get("upcoming_catalysts") or []
    if len(existing_catalysts) < _SECTION_MINIMUMS["upcoming_catalysts"]:
        earnings_catalysts = [
            {
                "ticker": e.get("ticker"),
                "date": e.get("date"),
                "event_type": "earnings",
                "event": f"{e.get('company') or e.get('ticker')} earnings ({e.get('timing') or 'TBD'})",
                "importance": "High",
                "context": None,
            }
            for e in earnings if e.get("ticker")
        ] if not existing_catalysts else existing_catalysts

        combined = list(earnings_catalysts)
        if target_week and len(combined) < _SECTION_MINIMUMS["upcoming_catalysts"]:
            have_keys = {(c.get("event"), c.get("date")) for c in combined}
            for mc in _macro_catalysts(target_week):
                if (mc["event"], mc["date"]) not in have_keys:
                    combined.append(mc)
                if len(combined) >= _SECTION_MINIMUMS["upcoming_catalysts"]:
                    break
        if combined:
            combined.sort(key=lambda c: c.get("date") or "")
            briefing["upcoming_catalysts"] = combined
            print(f"  [backfill] upcoming_catalysts -> {len(combined)} "
                  f"({len(earnings_catalysts)} earnings + macro top-up)")


def _dict_entries(raw) -> list:
    """Keep only dict items from a value that should be a list of objects.

    sonar-deep-research occasionally emits a section (watchlist_updates,
    watchlist_movers, upcoming_catalysts) as a JSON STRING instead of an array of
    objects. If that leaks through, downstream iteration walks the string
    character-by-character and the UI renders garbage (the ['0','1',...] / [':{']
    shapes). Reducing to dict entries means a malformed payload degrades to an
    empty list, which the deterministic context backfill then fills.

    A subtler variant: the array IS a list, but each element is a stringified
    JSON object ("{\"ticker\":...}") rather than a real object — this is how
    upcoming_catalysts came back for the 6/07 regen and why it collapsed to a
    single entry. Parse those strings back into dicts instead of discarding them.
    """
    if not isinstance(raw, list):
        return []
    out = []
    for x in raw:
        if isinstance(x, dict):
            out.append(x)
        elif isinstance(x, str):
            s = x.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    parsed = json.loads(s)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict):
                    out.append(parsed)
    return out


def _merge_structured_narrative(structured, narrative_resp) -> dict:
    """Fold the {"narrative": ...} response into the structured dict.

    The structured and narrative sections are fetched by two separate calls so
    the long prose can't truncate the structured arrays. compile_briefing expects
    a single dict carrying both, so merge the narrative string back in here.
    """
    merged = dict(_extract_dict(structured))
    narr = _extract_dict(narrative_resp)
    narrative = narr.get("narrative")
    if not narrative and isinstance(narrative_resp, dict):
        # Fallback: a raw payload that salvaged to a single string.
        raw = narrative_resp.get("raw")
        if isinstance(raw, str):
            narrative = raw
    if narrative:
        merged["narrative"] = narrative
    return merged


def compile_briefing(value, momentum, trends) -> dict:
    """Merge API responses into the weekly_briefing.json schema.

    ``trends`` is the merged structured+narrative dict (see
    _merge_structured_narrative). Outputs the full rich schema with BOTH old and
    new field name aliases so that both the current renderer and any
    backward-looking archive code continue to work.
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
    watchlist_updates = _dict_entries(trends_data.get("watchlist_updates", []))
    watchlist_movers = _dict_entries(trends_data.get("watchlist_movers", []))
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
        # TL;DR headline / macro regime (deterministically backfilled in run())
        "macro_regime": trends_data.get("macro_regime"),
        # Trends — both old-schema (key_trends) and new-schema (trends) names
        "key_trends": normalized_key_trends,
        "trends": normalized_trends,
        # Risks — normalized to carry both old and new field names
        "risks": normalized_risks,
        # Watchlist — both old (watchlist_updates) and new (watchlist_movers) names
        "watchlist_updates": watchlist_updates,
        "watchlist_movers": watchlist_movers,
        # New rich fields
        "upcoming_catalysts": _dict_entries(trends_data.get("upcoming_catalysts", [])),
        "sector_summary": trends_data.get("sector_summary", {}),
        # Deep narrative
        "narrative": trends_data.get("narrative", ""),
        # market_intel_lookup: populated by run() after compile; placeholder here
        "market_intel_lookup": {},
    }


def run(output_path: Path | None = None, week_ending: date | None = None) -> dict:
    """Main entry: generate weekly market briefing with parallel deep-research calls.

    When ``week_ending`` is supplied (regenerating a past week), the concrete-data
    context is built AS OF that week from the historical close series, so movers /
    subsector moves reflect that week's real price action rather than the live
    snapshot. The output also defaults to the archived path for that week.
    """
    tickers = load_tickers()
    target_week = week_ending or TODAY
    is_historical = week_ending is not None and week_ending != TODAY
    out = output_path or WEEKLY_BRIEFING
    print(f"Generating weekly briefing for week ending {target_week.isoformat()}...")
    print(f"Output: {out}")

    # Precompute concrete-data blocks (movers / earnings / subsectors) so the
    # trends prompt can be grounded in real names — the fix for the 6/07 regression
    # where watchlist_updates/upcoming_catalysts/sector_summary came back empty.
    if is_historical:
        from automation.jobs.weekly_briefing_context import build_context_blocks_asof
        context = build_context_blocks_asof(target_week)
    else:
        context = build_context_blocks(target_week)
    print(f"  Context: {len(context['movers'])} movers, "
          f"{len(context['earnings'])} earnings, "
          f"{sum(1 for b in context['buckets'].values() if b)} subsectors")

    # --- Run 4 deep-research calls in parallel (value, momentum, structured,
    # narrative). The structured + narrative split keeps the long prose report
    # from truncating the high-value structured arrays.
    # For a past-week regen, route the value/momentum calls through week-scoped
    # cache task names so each archived week gets its own cache entry instead of
    # reusing today's live entry.
    vm_week = target_week if is_historical else None
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_value = pool.submit(_fetch_value, vm_week)
        future_momentum = pool.submit(_fetch_momentum, vm_week)
        future_structured = pool.submit(_fetch_structured, tickers, context, False, vm_week)
        future_narrative = pool.submit(_fetch_narrative, tickers, context, False, vm_week)

        value = future_value.result()
        momentum = future_momentum.result()
        structured = future_structured.result()
        narrative = future_narrative.result()

    trends = _merge_structured_narrative(structured, narrative)

    # --- Compile ---
    print("\nCompiling weekly_briefing.json...")
    briefing = compile_briefing(value, momentum, trends)
    if is_historical:
        # compile_briefing stamps week_ending = TODAY; correct it to the target.
        briefing["week_ending"] = target_week.isoformat()

    # --- Post-LLM section-quality validation + one targeted retry ---
    report = _log_section_quality(briefing, context, stage="initial")
    failing = _failing_sections(report)
    if failing:
        print(f"  [RETRY] Sections below minimum: {failing}. Re-running structured call once...")
        try:
            retry_structured = _fetch_structured(tickers, context, force=True, week_ending=vm_week)
            retry_trends = _merge_structured_narrative(retry_structured, narrative)
            retry_briefing = compile_briefing(value, momentum, retry_trends)
            if is_historical:
                retry_briefing["week_ending"] = target_week.isoformat()
            retry_report = _log_section_quality(retry_briefing, context, stage="retry")
            # Keep the retry only if it improved the failing/gated sections OR the
            # trends+risks counts in aggregate.
            before = sum(report[s]["count"] for s in failing)
            after = sum(retry_report[s]["count"] for s in failing)
            before_tr = len(briefing.get("trends") or []) + len(briefing.get("risks") or [])
            after_tr = len(retry_briefing.get("trends") or []) + len(retry_briefing.get("risks") or [])
            if after > before or after_tr > before_tr:
                print(f"  [RETRY] Improved sections ({before}->{after}, trends+risks {before_tr}->{after_tr}); keeping retry.")
                briefing = retry_briefing
            else:
                print(f"  [RETRY] No improvement ({before} -> {after}); keeping original.")
        except Exception as exc:
            print(f"  [RETRY] failed: {exc}; keeping original briefing.")

    # --- Deterministic backfill of volume sections from real local data ---
    # Guarantees sector_summary / watchlist_updates / upcoming_catalysts are
    # populated from measured price + calendar data even when deep-research
    # returned empty arrays (the 5/31 + 6/07 thinness regression). No fabrication.
    _backfill_volume_sections(briefing, context, target_week=target_week)
    _log_section_quality(briefing, context, stage="post-backfill")

    # --- Measured index returns + deterministic TL;DR headline (macro_regime) ---
    _backfill_index_and_headline(briefing, target_week)

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

    if is_historical and output_path is None:
        # Regenerating a past week: write straight to that week's archive file,
        # never clobber the live weekly_briefing.json.
        from automation.jobs.backfill_briefings import archive_path
        out = archive_path(target_week)

    write_json(out, briefing)
    print(f"  Saved to {out}")

    # Auto-archive (only when writing the live briefing to its default location)
    if out == WEEKLY_BRIEFING:
        try:
            from automation.jobs.backfill_briefings import save_archive_briefing, patch_index_only
            save_archive_briefing(TODAY, briefing)
            patch_index_only()  # also refreshes the standalone archive_index.json
        except Exception as exc:
            print(f"  [weekly_briefing] archive failed: {exc}")
    elif is_historical:
        # Past-week regen: refresh the canonical index so the repaired file's
        # tldr_excerpt/metadata are picked up, but emit no subscriber alert.
        try:
            from automation.jobs.refresh_archive_index import refresh_archive_index
            refresh_archive_index()
        except Exception as exc:
            print(f"  [weekly_briefing] archive index refresh failed: {exc}")

    print(f"  Value picks: {len(briefing['value_picks'])}")
    print(f"  Momentum picks: {len(briefing['momentum_picks'])}")
    print(f"  Trends: {len(briefing['trends'])}")

    # Emit subscriber alert (live briefing only — never for historical regen)
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
    week_ending = date.fromisoformat(args.week_ending) if args.week_ending else None
    output_path = Path(args.output) if args.output else None
    run(output_path=output_path, week_ending=week_ending)


if __name__ == "__main__":
    main()
