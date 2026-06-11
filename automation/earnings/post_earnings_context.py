"""
Stage 1 deterministic data layer -- POST-earnings context builder.

Exports:
    build_post_earnings_context(ticker, fiscal_period=None, force=False) -> dict

Builds on the pre-earnings context and adds:
  - this_quarter_actuals (the just-reported print)
  - transcript (full earnings call text)
  - consensus_post_print (updated estimates after the print)
  - segments_latest (most recent segment breakdown)

Usage:
    python -m automation.earnings.post_earnings_context NVDA
    python -m automation.earnings.post_earnings_context NVDA --force
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

# Make automation importable when run as a module or script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.earnings.pre_earnings_context import (
    build_pre_earnings_context,
    _safe_call,
    _float,
    _check_cache,
    _save_to_supabase,
    _load_tiers,
    _get_tier,
)
from automation.perplexity.client import call_perplexity
from automation.data.industry_pack_fetcher import fetch_industry_pack


# ---------------------------------------------------------------------------
# Post-specific Perplexity prompts
# ---------------------------------------------------------------------------
def _finance_this_quarter_prompt(ticker: str) -> str:
    return (
        f"Look up the most recently reported quarterly earnings result for "
        f"{ticker}. Return the actual vs estimated figures for the just-reported "
        f"quarter.\n\n"
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"period": "...", "date": "YYYY-MM-DD", '
        f'"actual_revenue": <float>, "estimated_revenue": <float>, '
        f'"revenue_surprise_pct": <float>, '
        f'"actual_eps": <float>, "estimated_eps": <float>, '
        f'"eps_surprise_pct": <float>, '
        f'"post_earnings_move_1d_pct": <float>, '
        f'"expected_move_pct": <float>}}'
    )


def _finance_transcript_prompt(ticker: str) -> str:
    return (
        f"Retrieve the full text of the most recent earnings call transcript "
        f"for {ticker}. Include both prepared remarks and Q&A. "
        f"Return the complete transcript text.\n\n"
        f"Return ONLY this JSON.\n\n"
        f'{{"transcript": "<full transcript text>", '
        f'"earnings_date": "YYYY-MM-DD", "quarter": "..."}}'
    )


def _finance_post_print_estimates_prompt(ticker: str) -> str:
    return (
        f"Look up the CURRENT (post-print, updated) Wall Street consensus "
        f"estimates for {ticker} after their most recent earnings report. "
        f"These should reflect any post-earnings estimate revisions.\n\n"
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"fq_revenue": <next fiscal quarter revenue est in USD>, '
        f'"fq_eps": <next FQ EPS est>, '
        f'"fy_revenue": <current fiscal year revenue est in USD>, '
        f'"fy_eps": <current FY EPS est>, '
        f'"revision_direction": "<up|down|mixed|stable>"}}'
    )


def _finance_segments_latest_prompt(ticker: str) -> str:
    return (
        f"Look up the most recently reported quarterly business segment "
        f"revenue breakdown for {ticker}. List each reporting segment with "
        f"its revenue, YoY growth, and % of total revenue.\n\n"
        f"Return ONLY this JSON. No prose, no markdown.\n\n"
        f'{{"period": "...", "segments": [{{"name": "...", '
        f'"revenue": <float>, "yoy_growth_pct": <float>, '
        f'"pct_of_total": <float>}}, ...]}}'
    )


def _finance_earnings_schedule_last_prompt(ticker: str) -> str:
    return (
        f"Look up the most recently completed earnings report date for "
        f"{ticker} (the last quarter they reported). "
        f"Return ONLY this JSON.\n\n"
        f'{{"last_earnings_date": "YYYY-MM-DD", '
        f'"fiscal_quarter": <int 1-4>, "fiscal_year": <int>}}'
    )


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------
def build_post_earnings_context(
    ticker: str,
    fiscal_period: str | None = None,
    force: bool = False,
) -> dict:
    """Build the full post-earnings deterministic context dict.

    Starts by building the pre-earnings base, then layers on post-specific
    fields: actuals, transcript, post-print consensus, and latest segments.
    """
    errors: list[dict] = []
    tiers = _load_tiers()
    tier = _get_tier(ticker, tiers)
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    print(f"\n[post_earnings_context] {ticker} (tier={tier})")

    # -- Step 0: Determine the last-reported earnings date + fiscal period
    earnings_date_str = _dt.date.today().isoformat()
    if not fiscal_period:
        sched = _safe_call(
            ticker, "earnings_schedule_last",
            _finance_earnings_schedule_last_prompt(ticker),
            errors, "fiscal_period_auto",
        )
        if sched and isinstance(sched, dict):
            fq = sched.get("fiscal_quarter")
            fy = sched.get("fiscal_year")
            if fq and fy:
                fiscal_period = f"FQ{fq} {fy}"
            ed = sched.get("last_earnings_date")
            if ed:
                earnings_date_str = str(ed)[:10]

    if not fiscal_period:
        fiscal_period = "TBD"

    # -- Check Supabase cache (unless --force)
    if not force:
        cached = _check_cache(ticker, earnings_date_str, "post")
        if cached:
            return cached

    # -- Build pre-earnings base (force=True to skip its own cache since
    #    we want fresh data for the post context)
    pre_context = build_pre_earnings_context(
        ticker=ticker,
        fiscal_period=fiscal_period,
        force=True,
    )

    # Merge pre-earnings errors into ours
    pre_errors = pre_context.get("errors", [])
    errors.extend(pre_errors)

    # -- Post-specific Step 1: This quarter actuals
    actuals_raw = _safe_call(
        ticker, "finance_this_quarter_actuals",
        _finance_this_quarter_prompt(ticker),
        errors, "this_quarter_actuals",
    )
    this_quarter_actuals = None
    if actuals_raw and isinstance(actuals_raw, dict):
        this_quarter_actuals = {
            "period": actuals_raw.get("period"),
            "date": actuals_raw.get("date"),
            "actual_revenue": _float(actuals_raw.get("actual_revenue")),
            "estimated_revenue": _float(actuals_raw.get("estimated_revenue")),
            "revenue_surprise_pct": _float(actuals_raw.get("revenue_surprise_pct")),
            "actual_eps": _float(actuals_raw.get("actual_eps")),
            "estimated_eps": _float(actuals_raw.get("estimated_eps")),
            "eps_surprise_pct": _float(actuals_raw.get("eps_surprise_pct")),
            "post_earnings_move_1d_pct": _float(actuals_raw.get("post_earnings_move_1d_pct")),
            "expected_move_pct": _float(actuals_raw.get("expected_move_pct")),
        }

    # -- Post-specific Step 2: Transcript
    transcript_raw = _safe_call(
        ticker, "finance_earnings_transcript",
        _finance_transcript_prompt(ticker),
        errors, "transcript",
        max_tokens=8000,
    )
    transcript_text = None
    if transcript_raw and isinstance(transcript_raw, dict):
        transcript_text = transcript_raw.get("transcript") or transcript_raw.get("raw")

    # -- Post-specific Step 3: Post-print consensus
    post_est_raw = _safe_call(
        ticker, "finance_estimates_post_print",
        _finance_post_print_estimates_prompt(ticker),
        errors, "consensus_post_print",
    )
    consensus_post_print = None
    if post_est_raw and isinstance(post_est_raw, dict):
        consensus_post_print = {
            "fq_revenue": _float(post_est_raw.get("fq_revenue")),
            "fq_eps": _float(post_est_raw.get("fq_eps")),
            "fy_revenue": _float(post_est_raw.get("fy_revenue")),
            "fy_eps": _float(post_est_raw.get("fy_eps")),
            "revision_direction": post_est_raw.get("revision_direction"),
            "source": "finance_estimates_post_print",
        }

    # -- Post-specific Step 4: Latest segments
    seg_latest_raw = _safe_call(
        ticker, "finance_segments_latest",
        _finance_segments_latest_prompt(ticker),
        errors, "segments_latest",
    )
    segments_latest = None
    if seg_latest_raw and isinstance(seg_latest_raw, dict):
        segments_latest = seg_latest_raw

    # -- Assemble post context: overlay on pre_context
    context = dict(pre_context)
    context.update({
        "snapshot_type": "post",
        "fiscal_period": fiscal_period,
        "snapshot_ts": now_iso,
        "this_quarter_actuals": this_quarter_actuals,
        "transcript": transcript_text,
        "consensus_post_print": consensus_post_print,
        "segments_latest": segments_latest,
        "errors": errors,
        "_tier": tier,
    })

    # -- Post-specific Step 5: Grounded industry pack (per-ticker market intel)
    # Overrides the subsector-level pack the pre-earnings base may have set:
    # the post-earnings note cross-references TAM / CAGR / named competitors
    # for THIS ticker from Supabase market_intel_ticker. Missing or stale
    # (>14d) packs are flagged so the prompt tells the model not to invent
    # industry numbers.
    # Drop any industry_pack gap the pre-earnings base recorded against the OLD
    # subsector-level industry_packs table -- the per-ticker market_intel pack
    # supersedes it here, so a stale subsector miss must not contradict a fresh
    # per-ticker pack in DATA GAPS.
    errors[:] = [e for e in errors if e.get("field") != "industry_pack"]

    industry_pack = fetch_industry_pack(ticker)
    context["industry_pack"] = industry_pack
    if industry_pack is None:
        errors.append({
            "field": "industry_pack",
            "reason": f"no market_intel_ticker row for {ticker}",
        })
    elif industry_pack.get("is_stale"):
        errors.append({
            "field": "industry_pack",
            "reason": (f"market_intel_ticker row for {ticker} is stale "
                       f"(updated_at={industry_pack.get('updated_at')})"),
        })

    # If pre-earnings built a consensus_at_print, promote it
    if this_quarter_actuals:
        context["consensus_at_print"] = {
            "estimated_revenue": this_quarter_actuals.get("estimated_revenue"),
            "estimated_eps": this_quarter_actuals.get("estimated_eps"),
        }

    # -- Save to Supabase
    _save_to_supabase(context, earnings_date_str)

    return context


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build post-earnings deterministic context for a ticker."
    )
    p.add_argument("ticker", help="Stock ticker (e.g. NVDA)")
    p.add_argument(
        "--fiscal-period",
        default=None,
        help='Fiscal period (e.g. "FQ1 2027"). Auto-detected if omitted.',
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Bypass Supabase cache and refetch everything.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = build_post_earnings_context(
        ticker=args.ticker.upper(),
        fiscal_period=args.fiscal_period,
        force=args.force,
    )
    print(json.dumps(result, indent=2, default=str))
