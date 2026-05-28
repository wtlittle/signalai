"""
Queue executor: populates research cache for finance/earnings tasks using
a tiered provider chain: FactSet MCP > Finnhub > Perplexity > yfinance.

Each fetcher tries providers in priority order and falls back automatically.
The provider that served the data is recorded in the cache file via a
``_source`` field so downstream consumers and cost-tracking can distinguish
data provenance.

Data source preference (user-stated):
    1. FactSet MCP  -- highest fidelity, but requires Computer Agent runtime
    2. Finnhub      -- free-tier REST, good for recommendations + insider tx
    3. Perplexity   -- LLM-powered web search, good for transcripts + segments
    4. yfinance     -- always-available fallback

Usage:
    python -m automation.jobs.execute_queue
    python -m automation.jobs.execute_queue --tickers CRWD,PANW
    python -m automation.jobs.execute_queue --dry-run
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import CACHE_DIR
from automation.shared.cache import save_research_cache, research_cache_exists

# ---------------------------------------------------------------------------
# Task type registry
# ---------------------------------------------------------------------------
PRE_EARNINGS_TASKS = [
    "earnings_schedule",
    "finance_quote",
    "finance_estimates",
    "finance_earnings_history",
    "finance_analyst_research",
    "finance_adjusted_metrics",
    "finance_segments",
]

POST_EARNINGS_TASKS = [
    "earnings_schedule_last",
    "finance_this_quarter_actuals",
    "finance_earnings_transcript",
    "finance_estimates_post_print",
    "finance_segments_latest",
]

PEER_TASK = "finance_peer_snapshot"


# ---------------------------------------------------------------------------
# Provider failure logging (structured, never blocks)
# ---------------------------------------------------------------------------
def _log_provider_failure(ticker: str, task: str, source: str, exc: Exception) -> None:
    """Emit a structured log line for a provider failure. Never raises."""
    try:
        print(
            f"  [PROVIDER FAIL] {ticker}/{task} source={source} "
            f"error={str(exc)[:120]}"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# FactSet MCP stubs
#
# FactSet MCP requires the Anthropic Computer Agent runtime to invoke tools.
# In a cron/CLI context these stubs always return None, letting the chain
# fall through to Finnhub or yfinance. When FactSet MCP becomes callable
# from batch scripts (e.g. via an HTTP shim or subagent handoff), replace
# the stub bodies with real MCP tool calls.
# ---------------------------------------------------------------------------
_FACTSET_AVAILABLE = False  # flip when MCP shim is wired up


def _factset_stub(ticker: str, task: str) -> dict | None:
    """Placeholder for all FactSet MCP calls. Always returns None."""
    if not _FACTSET_AVAILABLE:
        return None
    # Future: call FactSet MCP tool via HTTP shim here
    return None


# ---------------------------------------------------------------------------
# Finnhub fetchers (free-tier endpoints)
# ---------------------------------------------------------------------------
def _finnhub_earnings_history(ticker: str) -> dict | None:
    """Finnhub /stock/earnings -- basic EPS history."""
    from automation.shared import finnhub as _fh
    if not _fh._has_credential():
        return None
    import requests
    try:
        resp = requests.get(
            f"{_fh.BASE_URL}/stock/earnings",
            params=_fh._params_with_token({"symbol": ticker, "limit": 8}),
            timeout=_fh._TIMEOUT,
            verify=_fh._verify_setting(),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None
        quarters = []
        for item in data[:8]:
            period_str = f"Q{item.get('quarter', '?')} {item.get('year', '?')}"
            actual_eps = _safe_float(item.get("actual"))
            est_eps = _safe_float(item.get("estimate"))
            surprise = _safe_float(item.get("surprisePercent"))
            quarters.append({
                "period": period_str,
                "date": item.get("period", ""),
                "actual_revenue": None,
                "estimated_revenue": None,
                "revenue_surprise_pct": None,
                "actual_eps": actual_eps,
                "estimated_eps": est_eps,
                "eps_surprise_pct": surprise,
                "post_earnings_move_1d_pct": None,
                "expected_move_pct": None,
            })
        if not quarters:
            return None
        return {
            "quarters": quarters,
            "average_post_earnings_move_1d_pct": None,
            "expected_move_pct": None,
        }
    except Exception:
        return None


def _finnhub_basic_financials(ticker: str) -> dict | None:
    """Finnhub /stock/metric -- basic financial metrics (free tier)."""
    from automation.shared import finnhub as _fh
    if not _fh._has_credential():
        return None
    import requests
    try:
        resp = requests.get(
            f"{_fh.BASE_URL}/stock/metric",
            params=_fh._params_with_token({"symbol": ticker, "metric": "all"}),
            timeout=_fh._TIMEOUT,
            verify=_fh._verify_setting(),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        metric = data.get("metric", {})
        if not metric:
            return None
        return metric
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Perplexity-powered fetchers (web search via LLM)
# ---------------------------------------------------------------------------
def _perplexity_available() -> bool:
    """True if Perplexity API can be used for direct calls."""
    from automation.perplexity.client import _use_api_fallback, PERPLEXITY_API_KEY
    return _use_api_fallback() and bool(PERPLEXITY_API_KEY)


def _pplx_call(ticker: str, task: str, prompt: str, max_tokens: int = 2000) -> dict | None:
    """Call Perplexity API and return parsed result, or None on failure."""
    if not _perplexity_available():
        return None
    try:
        from automation.perplexity.client import call_perplexity
        result = call_perplexity(
            ticker=ticker,
            task=task,
            prompt=prompt,
            max_tokens=max_tokens,
        )
        if not result:
            return None
        if result.get("queued") or result.get("skipped") or result.get("dry_run"):
            return None
        # Reject raw/unparsed responses
        if isinstance(result, dict) and "raw" in result and len(result) == 1:
            return None
        return result
    except Exception:
        return None


def _pplx_earnings_transcript(ticker: str) -> dict | None:
    """Fetch earnings transcript via Perplexity web search."""
    prompt = (
        f"Retrieve the full text of the most recent earnings call transcript "
        f"for {ticker}. Include both prepared remarks and Q&A. "
        f"Return ONLY this JSON.\n\n"
        f'{{"transcript": "<full transcript text>", '
        f'"earnings_date": "YYYY-MM-DD", "quarter": "..."}}'
    )
    return _pplx_call(ticker, "finance_earnings_transcript", prompt, max_tokens=8000)


def _pplx_segments(ticker: str) -> list | None:
    """Fetch segment breakdowns via Perplexity web search."""
    prompt = (
        f"Look up the last 4 quarters of business segment revenue breakdowns "
        f"for {ticker}. List each reporting segment with its revenue.\n\n"
        f"Return ONLY a JSON array of quarterly objects.\n\n"
        f'[{{"period": "Q1 2027", "segments": [{{"name": "...", '
        f'"revenue": <float>}}, ...]}}, ...]'
    )
    result = _pplx_call(ticker, "finance_segments", prompt, max_tokens=2000)
    if isinstance(result, list):
        return result
    return None


def _pplx_segments_latest(ticker: str) -> dict | None:
    """Fetch latest segment breakdown via Perplexity web search."""
    prompt = (
        f"Look up the most recently reported quarterly business segment "
        f"revenue breakdown for {ticker}. List each reporting segment with "
        f"its revenue, YoY growth, and % of total revenue.\n\n"
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"period": "...", "segments": [{{"name": "...", '
        f'"revenue": <float>, "yoy_growth_pct": <float>, '
        f'"pct_of_total": <float>}}, ...]}}'
    )
    return _pplx_call(ticker, "finance_segments_latest", prompt, max_tokens=2000)


def _pplx_estimates(ticker: str) -> dict | None:
    """Fetch consensus estimates via Perplexity web search."""
    prompt = (
        f"Look up the current Wall Street consensus estimates for {ticker}. "
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"fq_revenue": <next fiscal quarter revenue est in USD>, '
        f'"fq_eps": <next FQ EPS est>, '
        f'"fy_revenue": <current fiscal year revenue est in USD>, '
        f'"fy_eps": <current FY EPS est>}}'
    )
    return _pplx_call(ticker, "finance_estimates", prompt, max_tokens=1500)


def _pplx_earnings_history(ticker: str) -> dict | None:
    """Fetch earnings history via Perplexity web search."""
    prompt = (
        f"Look up the last 8 quarters of earnings history for {ticker}. "
        f"For each quarter include: period label, earnings date, actual and "
        f"estimated revenue, revenue surprise %, actual and estimated EPS, "
        f"EPS surprise %, the 1-day post-earnings stock move %, and the "
        f"options-implied expected move % before the print.\n\n"
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
    return _pplx_call(ticker, "finance_earnings_history", prompt, max_tokens=3000)


def _pplx_this_quarter_actuals(ticker: str) -> dict | None:
    """Fetch most recent quarter actuals via Perplexity web search."""
    prompt = (
        f"Look up the most recently reported quarterly earnings result for "
        f"{ticker}. Return the actual vs estimated figures.\n\n"
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"period": "...", "date": "YYYY-MM-DD", '
        f'"actual_revenue": <float>, "estimated_revenue": <float>, '
        f'"revenue_surprise_pct": <float>, '
        f'"actual_eps": <float>, "estimated_eps": <float>, '
        f'"eps_surprise_pct": <float>, '
        f'"post_earnings_move_1d_pct": <float>, '
        f'"expected_move_pct": <float>}}'
    )
    return _pplx_call(ticker, "finance_this_quarter_actuals", prompt, max_tokens=1500)


def _pplx_earnings_schedule_last(ticker: str) -> dict | None:
    """Fetch last earnings date via Perplexity web search."""
    prompt = (
        f"Look up the most recently completed earnings report date for "
        f"{ticker} (the last quarter they reported). "
        f"Return ONLY this JSON.\n\n"
        f'{{"last_earnings_date": "YYYY-MM-DD", '
        f'"fiscal_quarter": <int 1-4>, "fiscal_year": <int>}}'
    )
    return _pplx_call(ticker, "earnings_schedule_last", prompt, max_tokens=500)


# ---------------------------------------------------------------------------
# yfinance fetchers (last resort)
# ---------------------------------------------------------------------------
def _yf_ticker(symbol: str):
    import yfinance as yf
    return yf.Ticker(symbol)


def fetch_earnings_schedule(ticker: str) -> dict | None:
    """Next upcoming earnings date + fiscal period."""
    t = _yf_ticker(ticker)
    info = t.info
    ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
    if not ts:
        return None
    from datetime import datetime, timezone
    dt_obj = datetime.fromtimestamp(ts, tz=timezone.utc)
    month = dt_obj.month
    fq = (month - 1) // 3 + 1
    return {
        "next_earnings_date": dt_obj.strftime("%Y-%m-%d"),
        "fiscal_quarter": fq,
        "fiscal_year": dt_obj.year,
    }


def fetch_earnings_schedule_last(ticker: str) -> dict | None:
    """Most recently completed earnings date."""
    t = _yf_ticker(ticker)
    try:
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return None
    except Exception:
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    past = [idx for idx in ed.index if idx.to_pydatetime().replace(tzinfo=_dt.timezone.utc) < now]
    if not past:
        return None
    last = max(past)
    last_dt = last.to_pydatetime()
    fq = (last_dt.month - 1) // 3 + 1
    return {
        "last_earnings_date": last_dt.strftime("%Y-%m-%d"),
        "fiscal_quarter": fq,
        "fiscal_year": last_dt.year,
    }


def fetch_finance_quote(ticker: str) -> dict | None:
    """Current stock quote."""
    t = _yf_ticker(ticker)
    info = t.info
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return None
    return {
        "price": float(price),
        "change_1d_pct": _safe_float(info.get("regularMarketChangePercent")),
        "wk52_high": _safe_float(info.get("fiftyTwoWeekHigh")),
        "wk52_low": _safe_float(info.get("fiftyTwoWeekLow")),
        "market_cap": _safe_float(info.get("marketCap")),
    }


def fetch_finance_estimates(ticker: str) -> dict | None:
    """Forward consensus estimates from yfinance."""
    t = _yf_ticker(ticker)
    info = t.info
    fq_eps = _safe_float(info.get("epsCurrentYear"))
    fy_eps = _safe_float(info.get("epsForward"))
    fq_rev = _safe_float(info.get("revenueEstimate"))
    fy_rev = _safe_float(info.get("totalRevenue"))
    try:
        est = t.analyst_price_targets
        if est and isinstance(est, dict):
            pass
    except Exception:
        pass
    return {
        "fq_revenue": fq_rev,
        "fq_eps": fq_eps,
        "fy_revenue": fy_rev,
        "fy_eps": fy_eps,
    }


def fetch_finance_earnings_history(ticker: str) -> dict | None:
    """Last 8 quarters of earnings history with surprise data."""
    t = _yf_ticker(ticker)
    try:
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return None
    except Exception:
        return None

    now = _dt.datetime.now(_dt.timezone.utc)
    quarters = []
    for idx, row in ed.iterrows():
        dt_val = idx.to_pydatetime()
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=_dt.timezone.utc)
        if dt_val > now:
            continue
        eps_est = _safe_float(row.get("EPS Estimate"))
        eps_act = _safe_float(row.get("Reported EPS"))
        surprise = _safe_float(row.get("Surprise(%)"))
        month = dt_val.month
        fq = (month - 1) // 3 + 1
        quarters.append({
            "period": f"Q{fq} {dt_val.year}",
            "date": dt_val.strftime("%Y-%m-%d"),
            "actual_revenue": None,
            "estimated_revenue": None,
            "revenue_surprise_pct": None,
            "actual_eps": eps_act,
            "estimated_eps": eps_est,
            "eps_surprise_pct": surprise,
            "post_earnings_move_1d_pct": None,
            "expected_move_pct": None,
        })
        if len(quarters) >= 8:
            break

    # Try to enrich with revenue from quarterly income statement
    try:
        qi = t.quarterly_income_stmt
        if qi is not None and not qi.empty and "Total Revenue" in qi.index:
            rev_row = qi.loc["Total Revenue"]
            for q in quarters:
                qdate = _dt.datetime.strptime(q["date"], "%Y-%m-%d")
                for col in rev_row.index:
                    col_dt = col.to_pydatetime()
                    if abs((col_dt - qdate).days) < 45:
                        q["actual_revenue"] = _safe_float(rev_row[col])
                        break
    except Exception:
        pass

    avg_move = None
    moves = [q["post_earnings_move_1d_pct"] for q in quarters
             if q["post_earnings_move_1d_pct"] is not None]
    if moves:
        avg_move = round(sum(abs(m) for m in moves) / len(moves), 2)

    return {
        "quarters": quarters,
        "average_post_earnings_move_1d_pct": avg_move,
        "expected_move_pct": None,
    }


def fetch_finance_analyst_research(ticker: str) -> dict | None:
    """Build analyst narrative from yfinance data."""
    t = _yf_ticker(ticker)
    info = t.info
    rating = info.get("averageAnalystRating", "N/A")
    target_mean = info.get("targetMeanPrice")
    target_high = info.get("targetHighPrice")
    target_low = info.get("targetLowPrice")
    n_analysts = info.get("numberOfAnalystOpinions", 0)
    rec_key = info.get("recommendationKey", "N/A")
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")

    lines = []
    lines.append(
        f"Analyst consensus on {ticker} is {rating} based on {n_analysts} analysts "
        f"(recommendation: {rec_key})."
    )
    if target_mean and current_price:
        upside = round((target_mean - current_price) / current_price * 100, 1)
        lines.append(
            f"The mean price target is ${target_mean:.2f} (range ${target_low:.2f}-"
            f"${target_high:.2f}), implying {upside:+.1f}% upside from the current "
            f"${current_price:.2f}."
        )
    try:
        upgrades = t.upgrades_downgrades
        if upgrades is not None and not upgrades.empty:
            recent = upgrades.head(5)
            changes = []
            for idx, row in recent.iterrows():
                firm = row.get("Firm", "Unknown")
                grade = row.get("ToGrade", "")
                action = row.get("Action", "")
                changes.append(f"{firm}: {action} to {grade}")
            if changes:
                lines.append(
                    "Recent rating changes: " + "; ".join(changes) + "."
                )
    except Exception:
        pass

    return {"narrative": " ".join(lines)}


def fetch_finance_adjusted_metrics(ticker: str) -> list | None:
    """Last 8 quarters of adjusted financial metrics."""
    t = _yf_ticker(ticker)
    try:
        qi = t.quarterly_income_stmt
        if qi is None or qi.empty:
            return None
    except Exception:
        return None

    quarters = []
    for col in qi.columns[:8]:
        col_dt = col.to_pydatetime()
        month = col_dt.month
        fq = (month - 1) // 3 + 1
        period = f"Q{fq} {col_dt.year}"

        rev = _safe_float(_get_row(qi, "Total Revenue", col))
        gross = _safe_float(_get_row(qi, "Gross Profit", col))
        op_income = _safe_float(_get_row(qi, "Operating Income", col))
        ebitda = _safe_float(_get_row(qi, "EBITDA", col))
        diluted_eps = _safe_float(_get_row(qi, "Diluted EPS", col))

        gross_margin = round(gross / rev * 100, 1) if gross and rev else None
        op_margin = round(op_income / rev * 100, 1) if op_income and rev else None

        fcf = None
        try:
            qcf = t.quarterly_cashflow
            if qcf is not None and not qcf.empty:
                if "Free Cash Flow" in qcf.index and col in qcf.columns:
                    fcf = _safe_float(qcf.loc["Free Cash Flow", col])
                elif "Operating Cash Flow" in qcf.index and col in qcf.columns:
                    ocf = _safe_float(qcf.loc["Operating Cash Flow", col])
                    capex = _safe_float(_get_row(qcf, "Capital Expenditure", col))
                    if ocf is not None and capex is not None:
                        fcf = ocf + capex
        except Exception:
            pass

        quarters.append({
            "period": period,
            "adj_eps": diluted_eps,
            "adj_ebitda": ebitda,
            "fcf": fcf,
            "gross_margin_pct": gross_margin,
            "operating_margin_pct": op_margin,
        })

    return quarters if quarters else None


def fetch_finance_segments(ticker: str) -> list | None:
    """Last 4 quarters of segment breakdowns (limited in yfinance)."""
    t = _yf_ticker(ticker)
    info = t.info
    industry = info.get("industry", "Unknown")

    try:
        qi = t.quarterly_income_stmt
        if qi is None or qi.empty:
            return None
    except Exception:
        return None

    quarters = []
    for col in qi.columns[:4]:
        col_dt = col.to_pydatetime()
        month = col_dt.month
        fq = (month - 1) // 3 + 1
        rev = _safe_float(_get_row(qi, "Total Revenue", col))
        quarters.append({
            "period": f"Q{fq} {col_dt.year}",
            "segments": [{"name": f"{industry} (consolidated)", "revenue": rev}],
        })

    return quarters if quarters else None


def fetch_finance_peer_snapshot(ticker: str) -> dict | None:
    """Most recent earnings + forward consensus for a peer ticker."""
    t = _yf_ticker(ticker)
    info = t.info

    last_print = None
    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            now = _dt.datetime.now(_dt.timezone.utc)
            past = [idx for idx in ed.index
                    if idx.to_pydatetime().replace(tzinfo=_dt.timezone.utc) < now]
            if past:
                last_idx = max(past)
                last_row = ed.loc[last_idx]
                last_dt = last_idx.to_pydatetime()
                fq = (last_dt.month - 1) // 3 + 1
                last_print = {
                    "period": f"Q{fq} {last_dt.year}",
                    "date": last_dt.strftime("%Y-%m-%d"),
                    "actual_eps": _safe_float(last_row.get("Reported EPS")),
                    "estimated_eps": _safe_float(last_row.get("EPS Estimate")),
                    "eps_surprise_pct": _safe_float(last_row.get("Surprise(%)")),
                    "actual_revenue": None,
                    "estimated_revenue": None,
                    "revenue_surprise_pct": None,
                }
                try:
                    qi = t.quarterly_income_stmt
                    if qi is not None and not qi.empty and "Total Revenue" in qi.index:
                        rev_row = qi.loc["Total Revenue"]
                        for col in rev_row.index:
                            col_dt = col.to_pydatetime()
                            if abs((col_dt - last_dt).days) < 45:
                                last_print["actual_revenue"] = _safe_float(rev_row[col])
                                break
                except Exception:
                    pass
    except Exception:
        pass

    forward_consensus = {
        "fq_revenue": None,
        "fq_eps": _safe_float(info.get("epsCurrentYear")),
        "fy_revenue": _safe_float(info.get("totalRevenue")),
        "fy_eps": _safe_float(info.get("epsForward")),
    }

    return {
        "last_print": last_print,
        "forward_consensus": forward_consensus,
    }


def fetch_finance_this_quarter_actuals(ticker: str) -> dict | None:
    """Most recently reported quarter actuals."""
    t = _yf_ticker(ticker)
    try:
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return None
    except Exception:
        return None

    now = _dt.datetime.now(_dt.timezone.utc)
    past = [(idx, ed.loc[idx]) for idx in ed.index
            if idx.to_pydatetime().replace(tzinfo=_dt.timezone.utc) < now]
    if not past:
        return None

    last_idx, last_row = max(past, key=lambda x: x[0])
    last_dt = last_idx.to_pydatetime()
    fq = (last_dt.month - 1) // 3 + 1

    result = {
        "period": f"Q{fq} {last_dt.year}",
        "date": last_dt.strftime("%Y-%m-%d"),
        "actual_revenue": None,
        "estimated_revenue": None,
        "revenue_surprise_pct": None,
        "actual_eps": _safe_float(last_row.get("Reported EPS")),
        "estimated_eps": _safe_float(last_row.get("EPS Estimate")),
        "eps_surprise_pct": _safe_float(last_row.get("Surprise(%)")),
        "post_earnings_move_1d_pct": None,
        "expected_move_pct": None,
    }

    try:
        qi = t.quarterly_income_stmt
        if qi is not None and not qi.empty and "Total Revenue" in qi.index:
            rev_row = qi.loc["Total Revenue"]
            for col in rev_row.index:
                col_dt = col.to_pydatetime()
                if abs((col_dt - last_dt).days) < 45:
                    result["actual_revenue"] = _safe_float(rev_row[col])
                    break
    except Exception:
        pass

    return result


def fetch_finance_earnings_transcript(ticker: str) -> dict | None:
    """Transcript stub -- yfinance does not provide transcripts."""
    t = _yf_ticker(ticker)
    try:
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return None
    except Exception:
        return None

    now = _dt.datetime.now(_dt.timezone.utc)
    past = [idx for idx in ed.index
            if idx.to_pydatetime().replace(tzinfo=_dt.timezone.utc) < now]
    if not past:
        return None
    last = max(past)
    last_dt = last.to_pydatetime()
    fq = (last_dt.month - 1) // 3 + 1
    return {
        "transcript": f"[Transcript not available via yfinance for {ticker} "
                       f"Q{fq} {last_dt.year}. Use FactSet or manual source.]",
        "earnings_date": last_dt.strftime("%Y-%m-%d"),
        "quarter": f"Q{fq} {last_dt.year}",
    }


def fetch_finance_estimates_post_print(ticker: str) -> dict | None:
    """Post-print forward estimates (same as pre, marked as post)."""
    result = fetch_finance_estimates(ticker)
    if result:
        result["revision_direction"] = "stable"
    return result


def fetch_finance_segments_latest(ticker: str) -> dict | None:
    """Most recent segment breakdown."""
    segments = fetch_finance_segments(ticker)
    if segments and len(segments) > 0:
        latest = segments[0]
        for seg in latest.get("segments", []):
            seg["yoy_growth_pct"] = None
            seg["pct_of_total"] = 100.0
        return latest
    return None


# ---------------------------------------------------------------------------
# Provider chain: maps each task to an ordered list of (source, fetcher)
# ---------------------------------------------------------------------------
PROVIDER_CHAINS: dict[str, list[tuple[str, Any]]] = {
    "earnings_schedule": [
        ("factset_mcp", lambda t: _factset_stub(t, "earnings_schedule")),
        ("yfinance", fetch_earnings_schedule),
    ],
    "finance_quote": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_quote")),
        ("yfinance", fetch_finance_quote),
    ],
    "finance_estimates": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_estimates")),
        ("perplexity", _pplx_estimates),
        ("yfinance", fetch_finance_estimates),
    ],
    "finance_earnings_history": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_earnings_history")),
        ("finnhub", _finnhub_earnings_history),
        ("perplexity", _pplx_earnings_history),
        ("yfinance", fetch_finance_earnings_history),
    ],
    "finance_analyst_research": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_analyst_research")),
        ("yfinance", fetch_finance_analyst_research),
    ],
    "finance_adjusted_metrics": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_adjusted_metrics")),
        ("yfinance", fetch_finance_adjusted_metrics),
    ],
    "finance_segments": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_segments")),
        ("perplexity", _pplx_segments),
        ("yfinance", fetch_finance_segments),
    ],
    "finance_peer_snapshot": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_peer_snapshot")),
        ("yfinance", fetch_finance_peer_snapshot),
    ],
    "earnings_schedule_last": [
        ("factset_mcp", lambda t: _factset_stub(t, "earnings_schedule_last")),
        ("perplexity", _pplx_earnings_schedule_last),
        ("yfinance", fetch_earnings_schedule_last),
    ],
    "finance_this_quarter_actuals": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_this_quarter_actuals")),
        ("perplexity", _pplx_this_quarter_actuals),
        ("yfinance", fetch_finance_this_quarter_actuals),
    ],
    "finance_earnings_transcript": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_earnings_transcript")),
        ("perplexity", _pplx_earnings_transcript),
        ("yfinance", fetch_finance_earnings_transcript),
    ],
    "finance_estimates_post_print": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_estimates_post_print")),
        ("perplexity", _pplx_estimates),
        ("yfinance", fetch_finance_estimates_post_print),
    ],
    "finance_segments_latest": [
        ("factset_mcp", lambda t: _factset_stub(t, "finance_segments_latest")),
        ("perplexity", _pplx_segments_latest),
        ("yfinance", fetch_finance_segments_latest),
    ],
}

# Legacy flat dispatch table (used by tests and backward compat)
TASK_FETCHERS: dict[str, Any] = {
    task: chain[-1][1]  # last provider in each chain (yfinance)
    for task, chain in PROVIDER_CHAINS.items()
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _get_row(df, label: str, col):
    """Safely get a value from a DataFrame row."""
    try:
        if label in df.index and col in df.columns:
            return df.loc[label, col]
    except Exception:
        pass
    return None


def _run_provider_chain(ticker: str, task: str) -> tuple[Any, str]:
    """Try each provider in the chain for a task.

    Returns (data, source_name). If all providers fail, returns (None, "all_failed").
    """
    chain = PROVIDER_CHAINS.get(task)
    if not chain:
        return None, "no_chain"

    for source, fn in chain:
        try:
            result = fn(ticker)
            if result is not None:
                return result, source
        except Exception as exc:
            _log_provider_failure(ticker, task, source, exc)

    return None, "all_failed"


def _add_source_field(data: Any, source: str) -> Any:
    """Inject _source into the result for provenance tracking."""
    if isinstance(data, dict):
        data["_source"] = source
    elif isinstance(data, list):
        # Wrap list results in a dict with source metadata
        return {"data": data, "_source": source}
    return data


# ---------------------------------------------------------------------------
# Core executor
# ---------------------------------------------------------------------------
def execute_tasks_for_ticker(
    ticker: str,
    task_list: list[str],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Execute a set of tasks for a single ticker, populating the cache.

    Returns a summary dict with counts of success/failure/skipped and
    per-task source attribution.
    """
    results = {
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
        "sources": {},  # task -> source that served the data
    }

    for task in task_list:
        if task not in PROVIDER_CHAINS:
            print(f"  [SKIP] {ticker}/{task} -- no provider chain registered")
            results["skipped"] += 1
            continue

        if not force and research_cache_exists(ticker, task):
            print(f"  [CACHE HIT] {ticker}/{task} -- already cached")
            results["skipped"] += 1
            continue

        if dry_run:
            print(f"  [DRY RUN] {ticker}/{task}")
            results["skipped"] += 1
            continue

        try:
            print(f"  [FETCH] {ticker}/{task} ...")
            data, source = _run_provider_chain(ticker, task)

            if data is None:
                print(f"  [WARN] {ticker}/{task} -- all providers returned None")
                results["errors"].append({
                    "ticker": ticker, "task": task,
                    "error": "all providers returned None",
                })
                results["failed"] += 1
                continue

            # Stamp provenance and save
            data = _add_source_field(data, source)
            save_research_cache(ticker, task, data)
            print(f"  [OK] {ticker}/{task} -- cached (source={source})")
            results["success"] += 1
            results["sources"][task] = source
        except Exception as exc:
            print(f"  [ERROR] {ticker}/{task} -- {exc}")
            results["errors"].append({
                "ticker": ticker, "task": task,
                "error": str(exc)[:200],
            })
            results["failed"] += 1
        # Rate limit: brief pause between calls
        time.sleep(0.3)

    return results


def execute_peer_tasks(
    ticker: str,
    peers_data: dict,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Execute finance_peer_snapshot for all peers of a T1 ticker."""
    results = {"success": 0, "failed": 0, "skipped": 0, "errors": [], "sources": {}}
    all_peers: list[str] = []
    seen = set()
    for key in ("core_peers", "competitive_overlap", "read_through"):
        for p in peers_data.get(key, []):
            if p not in seen and not p.endswith("-PRIVATE"):
                seen.add(p)
                all_peers.append(p)

    for peer in all_peers:
        r = execute_tasks_for_ticker(
            peer, [PEER_TASK], force=force, dry_run=dry_run,
        )
        results["success"] += r["success"]
        results["failed"] += r["failed"]
        results["skipped"] += r["skipped"]
        results["errors"].extend(r["errors"])
        results["sources"].update(r.get("sources", {}))

    return results


def rebuild_context(
    ticker: str,
    note_type: str,
    *,
    dry_run: bool = False,
) -> bool:
    """Call the appropriate context builder with force=True."""
    if dry_run:
        print(f"  [DRY RUN] Would rebuild {ticker}/{note_type} context")
        return True

    try:
        if note_type == "pre":
            from automation.earnings.pre_earnings_context import (
                build_pre_earnings_context,
            )
            ctx = build_pre_earnings_context(ticker, force=True)
        else:
            from automation.earnings.post_earnings_context import (
                build_post_earnings_context,
            )
            ctx = build_post_earnings_context(ticker, force=True)

        errors = ctx.get("errors", [])
        n_errors = len(errors)
        print(f"  [CONTEXT] {ticker}/{note_type} -- built ({n_errors} errors)")
        return True
    except Exception as exc:
        print(f"  [ERROR] {ticker}/{note_type} context build failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def run(
    pre_tickers: list[str] | None = None,
    post_tickers: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Execute all finance/earnings tasks for the given tickers."""
    tiers_path = Path(__file__).resolve().parents[2] / "data" / "ticker_tiers.json"
    peers_path = Path(__file__).resolve().parents[2] / "data" / "peer_overrides.json"

    tiers = {}
    try:
        raw = json.loads(tiers_path.read_text())
        for t in raw.get("T1", []):
            tiers[t] = "T1"
        for t in raw.get("T2_explicit", []):
            tiers[t] = "T2"
    except Exception:
        pass

    peers_map = {}
    try:
        peers_map = json.loads(peers_path.read_text())
    except Exception:
        pass

    summary = {
        "pre_tickers": pre_tickers or [],
        "post_tickers": post_tickers or [],
        "total_success": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "contexts_built": 0,
        "contexts_failed": 0,
        "errors": [],
        "source_counts": {},  # source_name -> count
    }

    def _tally_sources(sources: dict) -> None:
        for _task, src in sources.items():
            summary["source_counts"][src] = summary["source_counts"].get(src, 0) + 1

    # -- Pre-earnings tickers
    for ticker in (pre_tickers or []):
        tier = tiers.get(ticker, "T2")
        print(f"\n{'='*60}")
        print(f"[PRE-EARNINGS] {ticker} (tier={tier})")
        print(f"{'='*60}")

        r = execute_tasks_for_ticker(
            ticker, PRE_EARNINGS_TASKS, force=force, dry_run=dry_run,
        )
        summary["total_success"] += r["success"]
        summary["total_failed"] += r["failed"]
        summary["total_skipped"] += r["skipped"]
        summary["errors"].extend(r["errors"])
        _tally_sources(r.get("sources", {}))

        # Peer snapshots for T1
        if tier == "T1" and ticker in peers_map:
            print(f"\n  [PEERS] Fetching peer snapshots for {ticker} ...")
            pr = execute_peer_tasks(
                ticker, peers_map[ticker], force=force, dry_run=dry_run,
            )
            summary["total_success"] += pr["success"]
            summary["total_failed"] += pr["failed"]
            summary["total_skipped"] += pr["skipped"]
            summary["errors"].extend(pr["errors"])
            _tally_sources(pr.get("sources", {}))

        # Rebuild context
        ok = rebuild_context(ticker, "pre", dry_run=dry_run)
        if ok:
            summary["contexts_built"] += 1
        else:
            summary["contexts_failed"] += 1

    # -- Post-earnings tickers
    for ticker in (post_tickers or []):
        tier = tiers.get(ticker, "T2")
        print(f"\n{'='*60}")
        print(f"[POST-EARNINGS] {ticker} (tier={tier})")
        print(f"{'='*60}")

        all_tasks = PRE_EARNINGS_TASKS + POST_EARNINGS_TASKS
        r = execute_tasks_for_ticker(
            ticker, all_tasks, force=force, dry_run=dry_run,
        )
        summary["total_success"] += r["success"]
        summary["total_failed"] += r["failed"]
        summary["total_skipped"] += r["skipped"]
        summary["errors"].extend(r["errors"])
        _tally_sources(r.get("sources", {}))

        # Peer snapshots for T1
        if tier == "T1" and ticker in peers_map:
            print(f"\n  [PEERS] Fetching peer snapshots for {ticker} ...")
            pr = execute_peer_tasks(
                ticker, peers_map[ticker], force=force, dry_run=dry_run,
            )
            summary["total_success"] += pr["success"]
            summary["total_failed"] += pr["failed"]
            summary["total_skipped"] += pr["skipped"]
            summary["errors"].extend(pr["errors"])
            _tally_sources(pr.get("sources", {}))

        # Rebuild context
        ok = rebuild_context(ticker, "post", dry_run=dry_run)
        if ok:
            summary["contexts_built"] += 1
        else:
            summary["contexts_failed"] += 1

    # -- Summary
    print(f"\n{'='*60}")
    print(f"EXECUTION SUMMARY")
    print(f"{'='*60}")
    print(f"  Tasks succeeded:  {summary['total_success']}")
    print(f"  Tasks failed:     {summary['total_failed']}")
    print(f"  Tasks skipped:    {summary['total_skipped']}")
    print(f"  Contexts built:   {summary['contexts_built']}")
    print(f"  Contexts failed:  {summary['contexts_failed']}")
    if summary["source_counts"]:
        print(f"\n  Source attribution:")
        for src, cnt in sorted(summary["source_counts"].items()):
            print(f"    {src}: {cnt} tasks")
    if summary["errors"]:
        print(f"\n  Errors ({len(summary['errors'])}):")
        for err in summary["errors"][:20]:
            print(f"    - {err['ticker']}/{err['task']}: {err['error'][:100]}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Execute queued finance/earnings tasks.")
    p.add_argument(
        "--pre", default="",
        help="Comma-separated pre-earnings tickers (e.g. CRWD,PANW)",
    )
    p.add_argument(
        "--post", default="",
        help="Comma-separated post-earnings tickers (e.g. MRVL,SNOW)",
    )
    p.add_argument("--force", action="store_true", help="Bypass cache.")
    p.add_argument("--dry-run", action="store_true", help="Log only, no fetch.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    pre = [t.strip() for t in args.pre.split(",") if t.strip()]
    post = [t.strip() for t in args.post.split(",") if t.strip()]
    if not pre and not post:
        # Default: the 10 primary tickers from the briefing
        pre = ["CRWD", "PANW", "AVGO", "MDT", "VEEV"]
        post = ["MRVL", "SNOW", "ZS", "SNPS", "CRM"]
    run(pre_tickers=pre, post_tickers=post, force=args.force, dry_run=args.dry_run)
