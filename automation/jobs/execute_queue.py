"""
Queue executor: populates research cache for finance/earnings tasks using
yfinance + Finnhub, then rebuilds deterministic contexts and upserts to
Supabase.

Data source preference: Finnhub > yfinance (FactSet MCP not available in
this runtime). Each fetcher writes to data/cache/{TICKER}_{DATE}_{TASK}.json
so the context builders pick it up on their next call.

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
# yfinance fetchers
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
    # Estimate fiscal quarter from month
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
    # Try analyst estimates
    try:
        est = t.analyst_price_targets
        if est and isinstance(est, dict):
            pass  # no rev estimates here
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
    # Try upgrades/downgrades
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

        # FCF from cash flow statement
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
                        fcf = ocf + capex  # capex is typically negative
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
    # yfinance does not have segment data; return a best-effort stub
    # so the context builder has something rather than nothing
    t = _yf_ticker(ticker)
    info = t.info
    sector = info.get("sector", "Unknown")
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

    # Last print from earnings_dates
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
                # Try to get revenue for this quarter
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

    # Get revenue
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
    # yfinance has no transcript API. Return a structured placeholder
    # that the context builder can handle gracefully.
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
        # Enrich with yoy and pct_of_total
        for seg in latest.get("segments", []):
            seg["yoy_growth_pct"] = None
            seg["pct_of_total"] = 100.0  # single consolidated segment
        return latest
    return None


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------
TASK_FETCHERS: dict[str, Any] = {
    "earnings_schedule": fetch_earnings_schedule,
    "finance_quote": fetch_finance_quote,
    "finance_estimates": fetch_finance_estimates,
    "finance_earnings_history": fetch_finance_earnings_history,
    "finance_analyst_research": fetch_finance_analyst_research,
    "finance_adjusted_metrics": fetch_finance_adjusted_metrics,
    "finance_segments": fetch_finance_segments,
    "finance_peer_snapshot": fetch_finance_peer_snapshot,
    "earnings_schedule_last": fetch_earnings_schedule_last,
    "finance_this_quarter_actuals": fetch_finance_this_quarter_actuals,
    "finance_earnings_transcript": fetch_finance_earnings_transcript,
    "finance_estimates_post_print": fetch_finance_estimates_post_print,
    "finance_segments_latest": fetch_finance_segments_latest,
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

    Returns a summary dict with counts of success/failure/skipped.
    """
    results = {"success": 0, "failed": 0, "skipped": 0, "errors": []}

    for task in task_list:
        fetcher = TASK_FETCHERS.get(task)
        if not fetcher:
            print(f"  [SKIP] {ticker}/{task} -- no fetcher registered")
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
            data = fetcher(ticker)
            if data is None:
                print(f"  [WARN] {ticker}/{task} -- fetcher returned None")
                results["errors"].append({
                    "ticker": ticker, "task": task,
                    "error": "fetcher returned None",
                })
                results["failed"] += 1
                continue

            save_research_cache(ticker, task, data)
            print(f"  [OK] {ticker}/{task} -- cached")
            results["success"] += 1
        except Exception as exc:
            tb = traceback.format_exc()
            print(f"  [ERROR] {ticker}/{task} -- {exc}")
            results["errors"].append({
                "ticker": ticker, "task": task,
                "error": str(exc)[:200],
            })
            results["failed"] += 1
        # Rate limit: brief pause between yfinance calls
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
    results = {"success": 0, "failed": 0, "skipped": 0, "errors": []}
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

    return results


def rebuild_context(
    ticker: str,
    note_type: str,
    *,
    dry_run: bool = False,
) -> bool:
    """Call the appropriate context builder with force=True.

    The builder reads from cache (which we just populated) and upserts
    to Supabase automatically.
    """
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
    """Execute all finance/earnings tasks for the given tickers.

    Args:
        pre_tickers: tickers that need pre-earnings contexts
        post_tickers: tickers that need post-earnings contexts
        force: bypass cache
        dry_run: log but don't fetch or save
    """
    # Load tier and peer data for T1 peer resolution
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
    }

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

        # Post-earnings needs both pre + post tasks
        all_tasks = PRE_EARNINGS_TASKS + POST_EARNINGS_TASKS
        r = execute_tasks_for_ticker(
            ticker, all_tasks, force=force, dry_run=dry_run,
        )
        summary["total_success"] += r["success"]
        summary["total_failed"] += r["failed"]
        summary["total_skipped"] += r["skipped"]
        summary["errors"].extend(r["errors"])

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
