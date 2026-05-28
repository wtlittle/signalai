"""
Stage 1 deterministic data layer -- PRE-earnings context builder.

Exports:
    build_pre_earnings_context(ticker, fiscal_period=None, force=False) -> dict

Fetches all structured earnings data from Perplexity finance tools + Finnhub
Pipedream connector, assembles the EarningsContext dict, and caches to
Supabase earnings_context_snapshots.

Usage:
    python -m automation.earnings.pre_earnings_context NVDA
    python -m automation.earnings.pre_earnings_context NVDA --force
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any

# Make automation importable when run as a module or script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR, DATA_DIR
from automation.shared.tickers import load_subsector_map
from automation.shared import finnhub as _finnhub
from automation.shared import supabase_client as _sb
from automation.perplexity.client import call_perplexity

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_SNAPSHOT_TABLE = "earnings_context_snapshots"
_INDUSTRY_TABLE = "industry_packs"
_CACHE_TTL_HOURS = 6
_TIERS_PATH = DATA_DIR / "ticker_tiers.json"
_PEERS_PATH = DATA_DIR / "peer_overrides.json"


# ---------------------------------------------------------------------------
# Tier + peer helpers
# ---------------------------------------------------------------------------
def _load_tiers() -> dict[str, str]:
    """Return {ticker: tier} map. Tickers not listed default to T2."""
    try:
        raw = json.loads(_TIERS_PATH.read_text())
    except Exception:
        return {}
    out: dict[str, str] = {}
    for t in raw.get("T1", []):
        out[t] = "T1"
    for t in raw.get("T2_explicit", []):
        out[t] = "T2"
    return out


def _get_tier(ticker: str, tiers: dict[str, str]) -> str:
    return tiers.get(ticker, "T2")


def _load_peers(ticker: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(_PEERS_PATH.read_text())
    except Exception:
        return None
    return raw.get(ticker)


# ---------------------------------------------------------------------------
# Perplexity finance tool prompts
# ---------------------------------------------------------------------------
def _finance_quote_prompt(ticker: str) -> str:
    return (
        f"Look up the current stock quote for {ticker}. "
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"price": <float>, "change_1d_pct": <float>, '
        f'"wk52_high": <float>, "wk52_low": <float>, '
        f'"market_cap": <float in USD>}}'
    )


def _finance_estimates_prompt(ticker: str) -> str:
    return (
        f"Look up the current Wall Street consensus estimates for {ticker}. "
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"fq_revenue": <next fiscal quarter revenue est in USD>, '
        f'"fq_eps": <next FQ EPS est>, '
        f'"fy_revenue": <current fiscal year revenue est in USD>, '
        f'"fy_eps": <current FY EPS est>}}'
    )


def _finance_earnings_history_prompt(ticker: str) -> str:
    return (
        f"Look up the last 8 quarters of earnings history for {ticker}. "
        f"For each quarter include: period label, earnings date, actual and "
        f"estimated revenue, revenue surprise %, actual and estimated EPS, "
        f"EPS surprise %, the 1-day post-earnings stock move %, and the "
        f"options-implied expected move % before the print.\n\n"
        f"Also include the average 1-day post-earnings move across all "
        f"quarters and the expected move % for the upcoming print.\n\n"
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"quarters": [{{"period": "Q1 2027", "date": "2026-05-22", '
        f'"actual_revenue": <float>, "estimated_revenue": <float>, '
        f'"revenue_surprise_pct": <float>, "actual_eps": <float>, '
        f'"estimated_eps": <float>, "eps_surprise_pct": <float>, '
        f'"post_earnings_move_1d_pct": <float>, '
        f'"expected_move_pct": <float>}}, ...], '
        f'"average_post_earnings_move_1d_pct": <float>, '
        f'"expected_move_pct": <float>}}'
    )


def _finance_earnings_schedule_prompt(ticker: str) -> str:
    return (
        f"Look up the next upcoming earnings report date for {ticker}. "
        f"Return ONLY this JSON.\n\n"
        f'{{"next_earnings_date": "YYYY-MM-DD", '
        f'"fiscal_quarter": <int 1-4>, "fiscal_year": <int>}}'
    )


def _finance_analyst_research_prompt(ticker: str) -> str:
    return (
        f"Summarize the most recent sell-side analyst research and commentary "
        f"on {ticker} from the last 30 days. Focus on rating changes, price "
        f"target revisions, and key thesis points.\n\n"
        f"Return ONLY this JSON.\n\n"
        f'{{"narrative": "<2-4 paragraph summary of recent analyst views>"}}'
    )


def _finance_adjusted_metrics_prompt(ticker: str) -> str:
    return (
        f"Look up the last 8 quarters of adjusted financial metrics for {ticker}. "
        f"Include: adjusted EPS, adjusted EBITDA, free cash flow, gross margin, "
        f"operating margin for each quarter.\n\n"
        f"Return ONLY a JSON array of quarterly objects.\n\n"
        f'[{{"period": "Q1 2027", "adj_eps": <float>, "adj_ebitda": <float>, '
        f'"fcf": <float>, "gross_margin_pct": <float>, '
        f'"operating_margin_pct": <float>}}, ...]'
    )


def _finance_segments_prompt(ticker: str) -> str:
    return (
        f"Look up the last 4 quarters of business segment revenue breakdowns "
        f"for {ticker}. List each reporting segment with its revenue.\n\n"
        f"Return ONLY a JSON array of quarterly objects.\n\n"
        f'[{{"period": "Q1 2027", "segments": [{{"name": "...", '
        f'"revenue": <float>}}, ...]}}, ...]'
    )


def _finance_peer_snapshot_prompt(peer_ticker: str) -> str:
    return (
        f"Look up the most recent earnings result and current forward "
        f"consensus for {peer_ticker}. Return ONLY this JSON.\n\n"
        f'{{"last_print": {{"period": "...", "date": "...", '
        f'"actual_eps": <float>, "estimated_eps": <float>, '
        f'"eps_surprise_pct": <float>, "actual_revenue": <float>, '
        f'"estimated_revenue": <float>, "revenue_surprise_pct": <float>}}, '
        f'"forward_consensus": {{"fq_revenue": <float>, "fq_eps": <float>, '
        f'"fy_revenue": <float>, "fy_eps": <float>}}}}'
    )


# ---------------------------------------------------------------------------
# Safe Perplexity caller with error isolation
# ---------------------------------------------------------------------------
def _safe_call(
    ticker: str,
    task: str,
    prompt: str,
    errors: list[dict],
    field_name: str,
    max_tokens: int = 2000,
) -> dict | list | None:
    """Call Perplexity and return parsed result. On failure, append to errors
    and return None -- never raise."""
    try:
        result = call_perplexity(
            ticker=ticker,
            task=task,
            prompt=prompt,
            max_tokens=max_tokens,
        )
        if not result:
            errors.append({"field": field_name, "reason": "empty response"})
            return None
        if result.get("queued"):
            errors.append({"field": field_name, "reason": "queued to Computer (no API key)"})
            return None
        if result.get("skipped"):
            errors.append({"field": field_name, "reason": result.get("reason", "skipped")})
            return None
        if result.get("dry_run"):
            errors.append({"field": field_name, "reason": "dry run"})
            return None
        return result
    except Exception as exc:
        errors.append({"field": field_name, "reason": str(exc)[:200]})
        return None


# ---------------------------------------------------------------------------
# Supabase cache
# ---------------------------------------------------------------------------
def _check_cache(
    ticker: str, earnings_date: str, note_type: str
) -> dict | None:
    """Return cached context payload if fresh (within TTL), else None."""
    rows = _sb.fetch_rows(
        _SNAPSHOT_TABLE,
        params={
            "ticker": f"eq.{ticker}",
            "earnings_date": f"eq.{earnings_date}",
            "note_type": f"eq.{note_type}",
            "select": "context,generated_at",
            "order": "generated_at.desc",
            "limit": "1",
        },
    )
    if not rows:
        return None
    row = rows[0]
    gen_at = row.get("generated_at")
    if not gen_at:
        return None
    try:
        ts = _dt.datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
        age = _dt.datetime.now(_dt.timezone.utc) - ts.astimezone(_dt.timezone.utc)
        if age <= _dt.timedelta(hours=_CACHE_TTL_HOURS):
            ctx = row.get("context")
            if ctx:
                print(f"  [CACHE HIT] {ticker}/{note_type} from {gen_at}")
                return ctx
    except Exception:
        pass
    return None


def _save_to_supabase(context: dict, earnings_date: str) -> None:
    """Persist context snapshot to Supabase."""
    ticker = context.get("ticker", "")
    note_type = context.get("snapshot_type", "pre")
    tier = context.get("_tier", "T2")

    # Detect data provenance from cache _source fields.
    # If any cache entry was served by factset_mcp, flag it.
    # If yfinance was the sole source, flag yfinance_fallback.
    sources_used = set()
    try:
        from automation.shared.cache import load_research_cache
        from automation.jobs.execute_queue import PRE_EARNINGS_TASKS, POST_EARNINGS_TASKS
        all_tasks = PRE_EARNINGS_TASKS + POST_EARNINGS_TASKS
        for task in all_tasks:
            cached = load_research_cache(ticker, task)
            if cached and isinstance(cached, dict):
                src = cached.get("_source")
                if src:
                    sources_used.add(src)
    except Exception:
        pass

    factset_used = "factset_mcp" in sources_used
    yfinance_only = sources_used == {"yfinance"} or not sources_used

    row = {
        "ticker": ticker,
        "earnings_date": earnings_date,
        "note_type": note_type,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "context": json.loads(json.dumps(context, default=str)),
        "ticker_tier": tier,
        "yfinance_fallback": yfinance_only,
        "factset_used": factset_used,
    }
    ok = _sb.upsert_row(
        _SNAPSHOT_TABLE,
        row,
        on_conflict="ticker,earnings_date,note_type",
    )
    if ok:
        print(f"  [SUPABASE] Saved {ticker}/{note_type} context snapshot")
    else:
        print(f"  [WARN] Failed to save {ticker}/{note_type} to Supabase")


# ---------------------------------------------------------------------------
# Industry pack lookup
# ---------------------------------------------------------------------------
def _lookup_industry_pack(
    ticker: str, errors: list[dict]
) -> dict | None:
    """Fetch the most recent industry pack for this ticker's subsector."""
    subsector_map = load_subsector_map()
    subsector = subsector_map.get(ticker)
    if not subsector:
        # Also check peer_overrides for subsector
        peers_data = _load_peers(ticker)
        if peers_data:
            subsector = peers_data.get("subsector")
    if not subsector:
        errors.append({"field": "industry_pack", "reason": f"no subsector mapping for {ticker}"})
        return None

    rows = _sb.fetch_rows(
        _INDUSTRY_TABLE,
        params={
            "subsector": f"eq.{subsector}",
            "select": "subsector,week_of,pack,sources",
            "order": "week_of.desc",
            "limit": "1",
        },
    )
    if not rows:
        errors.append({"field": "industry_pack", "reason": f"no industry pack for subsector {subsector}"})
        return None
    return rows[0]


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------
def build_pre_earnings_context(
    ticker: str,
    fiscal_period: str | None = None,
    force: bool = False,
) -> dict:
    """Build the full pre-earnings deterministic context dict.

    If fiscal_period is None, auto-detects via earnings schedule lookup.
    Returns a fully-populated EarningsContext dict.
    """
    errors: list[dict] = []
    tiers = _load_tiers()
    tier = _get_tier(ticker, tiers)
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    print(f"\n[pre_earnings_context] {ticker} (tier={tier})")

    # -- Step 0: Auto-detect fiscal period + earnings date
    earnings_date_str = _dt.date.today().isoformat()
    if not fiscal_period:
        sched = _safe_call(
            ticker, "earnings_schedule",
            _finance_earnings_schedule_prompt(ticker),
            errors, "fiscal_period_auto",
        )
        if sched and isinstance(sched, dict):
            fq = sched.get("fiscal_quarter")
            fy = sched.get("fiscal_year")
            if fq and fy:
                fiscal_period = f"FQ{fq} {fy}"
            ed = sched.get("next_earnings_date")
            if ed:
                earnings_date_str = str(ed)[:10]

    if not fiscal_period:
        fiscal_period = "TBD"

    # -- Check Supabase cache (unless --force)
    if not force:
        cached = _check_cache(ticker, earnings_date_str, "pre")
        if cached:
            return cached

    # -- Step 1: Quote
    quote_raw = _safe_call(
        ticker, "finance_quote",
        _finance_quote_prompt(ticker),
        errors, "quote",
    )
    quote = None
    if quote_raw and isinstance(quote_raw, dict):
        price = _float(quote_raw.get("price"))
        wk52_high = _float(quote_raw.get("wk52_high"))
        pct_off = None
        if price and wk52_high and wk52_high > 0:
            pct_off = round((price - wk52_high) / wk52_high * 100, 1)
        quote = {
            "price": price,
            "change_1d_pct": _float(quote_raw.get("change_1d_pct")),
            "wk52_high": wk52_high,
            "wk52_low": _float(quote_raw.get("wk52_low")),
            "pct_off_52w_high": pct_off,
            "market_cap": _float(quote_raw.get("market_cap")),
        }

    # -- Step 2: Forward consensus estimates
    est_raw = _safe_call(
        ticker, "finance_estimates",
        _finance_estimates_prompt(ticker),
        errors, "consensus_forward",
    )
    consensus_forward = None
    if est_raw and isinstance(est_raw, dict):
        consensus_forward = {
            "fq_revenue": _float(est_raw.get("fq_revenue")),
            "fq_eps": _float(est_raw.get("fq_eps")),
            "fy_revenue": _float(est_raw.get("fy_revenue")),
            "fy_eps": _float(est_raw.get("fy_eps")),
            "source": "finance_estimates",
        }

    # -- Step 3: Earnings history (8Q)
    hist_raw = _safe_call(
        ticker, "finance_earnings_history",
        _finance_earnings_history_prompt(ticker),
        errors, "history_8q",
        max_tokens=3000,
    )
    history_8q: list[dict] = []
    post_earnings_move_avg = None
    implied_move_value = None
    if hist_raw and isinstance(hist_raw, dict):
        quarters = hist_raw.get("quarters") or []
        if isinstance(quarters, list):
            history_8q = quarters[:8]
        post_earnings_move_avg = _float(hist_raw.get("average_post_earnings_move_1d_pct"))
        implied_move_value = _float(hist_raw.get("expected_move_pct"))

    implied_move = {
        "method": "prior_quarter_baseline",
        "value_pct": implied_move_value,
        "source": "finance_earnings_history.expectedMovePerc",
    }

    # -- Step 4: Recommendation drift (Finnhub)
    rec_raw = _finnhub.get_recommendation_trends(ticker)
    recommendation_drift: list[dict] = []
    if rec_raw and isinstance(rec_raw, list):
        for r in rec_raw[:4]:
            recommendation_drift.append({
                "period": r.get("period", ""),
                "strong_buy": r.get("strongBuy", 0),
                "buy": r.get("buy", 0),
                "hold": r.get("hold", 0),
                "sell": r.get("sell", 0),
                "strong_sell": r.get("strongSell", 0),
            })
    else:
        errors.append({"field": "recommendation_drift", "reason": "Finnhub recommendation trends unavailable"})

    # -- Step 5: Analyst research narrative
    analyst_raw = _safe_call(
        ticker, "finance_analyst_research",
        _finance_analyst_research_prompt(ticker),
        errors, "analyst_research_narrative",
        max_tokens=2000,
    )
    analyst_narrative = None
    if analyst_raw and isinstance(analyst_raw, dict):
        analyst_narrative = analyst_raw.get("narrative") or analyst_raw.get("raw")

    # -- Step 6: Adjusted metrics (8Q)
    adj_raw = _safe_call(
        ticker, "finance_adjusted_metrics",
        _finance_adjusted_metrics_prompt(ticker),
        errors, "adj_metrics_8q",
        max_tokens=2500,
    )
    adj_metrics_8q: list[dict] = []
    if adj_raw:
        if isinstance(adj_raw, list):
            adj_metrics_8q = adj_raw[:8]
        elif isinstance(adj_raw, dict) and adj_raw.get("raw"):
            # Sonar sometimes returns raw text; leave as is.
            pass

    # -- Step 7: Segments (4Q)
    seg_raw = _safe_call(
        ticker, "finance_segments",
        _finance_segments_prompt(ticker),
        errors, "segments_4q",
        max_tokens=2000,
    )
    segments_4q: list[dict] = []
    if seg_raw:
        if isinstance(seg_raw, list):
            segments_4q = seg_raw[:4]

    # -- Step 8: Peer pack (T1 only)
    peers: list[dict] = []
    if tier == "T1":
        peer_data = _load_peers(ticker)
        if peer_data:
            all_peers: list[tuple[str, str]] = []
            for p in peer_data.get("core_peers", []):
                all_peers.append((p, "core_peer"))
            for p in peer_data.get("competitive_overlap", []):
                if not any(x[0] == p for x in all_peers):
                    all_peers.append((p, "competitive_overlap"))
            for p in peer_data.get("read_through", []):
                if not any(x[0] == p for x in all_peers):
                    all_peers.append((p, "read_through"))

            for peer_ticker, relation in all_peers:
                snap = _safe_call(
                    peer_ticker, "finance_peer_snapshot",
                    _finance_peer_snapshot_prompt(peer_ticker),
                    errors, f"peer_{peer_ticker}",
                    max_tokens=1500,
                )
                peer_entry: dict[str, Any] = {
                    "ticker": peer_ticker,
                    "relation": relation,
                    "last_print": None,
                    "forward_consensus": None,
                }
                if snap and isinstance(snap, dict):
                    peer_entry["last_print"] = snap.get("last_print")
                    peer_entry["forward_consensus"] = snap.get("forward_consensus")
                peers.append(peer_entry)
        else:
            errors.append({"field": "peers", "reason": f"no peer_overrides entry for {ticker}"})

    # -- Step 9: Insider transactions (90 days, Finnhub)
    today = _dt.date.today()
    from_date = (today - _dt.timedelta(days=90)).isoformat()
    to_date = today.isoformat()
    insider_raw = _finnhub.get_insider_transactions(ticker, from_date, to_date)
    insider_tx_90d: dict[str, Any] = {
        "n_buys": 0,
        "n_sells": 0,
        "net_value_usd": 0,
        "raw": [],
    }
    if insider_raw and isinstance(insider_raw, list):
        buys = 0
        sells = 0
        net_val = 0.0
        for tx in insider_raw:
            change = _float(tx.get("change") or tx.get("share") or 0) or 0
            price = _float(tx.get("transactionPrice") or 0) or 0
            val = abs(change * price)
            tc = (tx.get("transactionCode") or "").upper()
            if tc in ("P", "A", "M") or change > 0:
                buys += 1
                net_val += val
            elif tc in ("S", "F") or change < 0:
                sells += 1
                net_val -= val
        insider_tx_90d = {
            "n_buys": buys,
            "n_sells": sells,
            "net_value_usd": round(net_val),
            "raw": insider_raw[:20],  # cap to avoid huge payloads
        }
    else:
        errors.append({"field": "insider_tx_90d", "reason": "Finnhub insider transactions unavailable"})

    # -- Step 10: Industry pack
    industry_pack = _lookup_industry_pack(ticker, errors)

    # -- Assemble context dict
    context: dict[str, Any] = {
        "ticker": ticker,
        "snapshot_type": "pre",
        "fiscal_period": fiscal_period,
        "snapshot_ts": now_iso,
        "quote": quote,
        "consensus_forward": consensus_forward,
        "implied_move": implied_move,
        "history_8q": history_8q,
        "post_earnings_move_avg_1d_pct": post_earnings_move_avg,
        "recommendation_drift": recommendation_drift,
        "analyst_research_narrative": analyst_narrative,
        "adj_metrics_8q": adj_metrics_8q,
        "segments_4q": segments_4q,
        "peers": peers,
        "insider_tx_90d": insider_tx_90d,
        "industry_pack": industry_pack,
        "errors": errors,
        "_tier": tier,
    }

    # -- Save to Supabase
    _save_to_supabase(context, earnings_date_str)

    return context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build pre-earnings deterministic context for a ticker."
    )
    p.add_argument("ticker", help="Stock ticker (e.g. NVDA)")
    p.add_argument(
        "--fiscal-period",
        default=None,
        help='Fiscal period (e.g. "FQ2 2027"). Auto-detected if omitted.',
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Bypass Supabase cache and refetch everything.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = build_pre_earnings_context(
        ticker=args.ticker.upper(),
        fiscal_period=args.fiscal_period,
        force=args.force,
    )
    print(json.dumps(result, indent=2, default=str))
