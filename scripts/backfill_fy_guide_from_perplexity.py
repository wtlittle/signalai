#!/usr/bin/env python3
"""
Targeted, cost-gated FY-guidance backfill for POST earnings cards.

WHY THIS EXISTS (Issue C)
  The POST card renders two "FY Guide Δ" pills (revenue + profitability) from
  the normalized guidance fields, which in turn derive from the raw
  ``guide_vs_consensus`` envelope (see normalize_guidance_envelope in
  sync_earnings_intel_from_notes.py). For most POST tickers the legacy notes
  never captured FY guidance midpoints, so both pills are empty ("—"). Every
  public company that reports gives forward guidance and there is a sell-side
  consensus for it; the numbers exist, they were just never lifted into the
  data layer. This script fetches them FRESH from a single Perplexity ``sonar``
  call per ticker and writes the envelope fields the normalizer consumes.

WHAT IT WRITES (into results record ``guide_vs_consensus``)
  fy_rev_guide_midpoint_new    fy_rev_guide_midpoint_prior   fy_rev_consensus_prior
  fy_eps_guide_midpoint_new    fy_eps_guide_midpoint_prior   fy_eps_consensus_prior
  fy_op_metric_kind
  fy_op_profit_guide_midpoint_new   fy_op_profit_guide_midpoint_prior   fy_op_profit_consensus_prior
  fy_fcf_guide_midpoint_new    fy_fcf_guide_midpoint_prior   fy_fcf_consensus_prior
  fy_guide_source = "perplexity_sonar"  (+ fy_guide_fiscal_year, fy_guide_widened)
  The downstream sync's normalize_guidance_envelope turns these into the
  guidance*DeltaPct fields (revenue + EPS/OpInc/FCF profitability cascade) and,
  when both a prior guide and prior consensus exist, the *StreetDeltaPct fields
  that drive the "Raise +X%, +Y% vs Street" pills.

SCOPE / COST
  * Runs for EVERY active POST ticker missing EITHER guidance pill (revenue OR
    profitability) — the mandate is no blank fields, so a card with one empty
    pill is still a candidate. Cards already showing both pills are skipped.
  * NO per-run cap by default (``--max-backfills 0`` == unlimited): the sweep
    must cascade across all POST cards. API cost is accepted for this backfill.
    A positive ``--max-backfills`` can still bound an ad-hoc test run.
  * Uses the cheap ``sonar`` model, not sonar-pro / deep-research.
  * Requires USE_PPLX_API=true (or a key) — otherwise calls are queued, not
    billed, and this script reports that and exits cleanly.

NON-FABRICATION
  * The model is instructed to return null for any figure it cannot ground in
    the company's actual earnings release / call for the given date. A field is
    written ONLY when the model returns a real number; nulls are dropped.
  * Existing non-null envelope fields are never overwritten.

Usage:
  USE_PPLX_API=true PERPLEXITY_API_KEY=... \
    python3 scripts/backfill_fy_guide_from_perplexity.py [--dry-run] \
      [--ticker XYZ] [--max-backfills N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "earnings_intel.json"

sys.path.insert(0, str(ROOT))
from automation.perplexity.client import call_perplexity  # noqa: E402
from automation.shared.cache import (  # noqa: E402
    load_research_cache,
    research_cache_exists,
)
from scripts.sync_earnings_intel_from_notes import (  # noqa: E402
    NORMALIZED_GUIDANCE_FIELDS,
    normalize_guidance_envelope,
)

# No per-run cap by default: the FY-guide sweep must cascade across EVERY POST
# card with an empty guidance pill (API cost is accepted for this backfill). A
# positive --max-backfills can still bound a single run for ad-hoc testing.
_DEFAULT_CAP = 0  # 0 == unlimited

_SYSTEM = (
    "You are a sell-side equity data extractor. Return ONLY a JSON object, no "
    "prose, no markdown fences. Every number must be grounded in the company's "
    "actual earnings release or call for the stated date; if you cannot ground "
    "a figure, return null for it. Never guess."
)


def _prompt(ticker: str, company: str, date: str, fy_label: str) -> str:
    return (
        f"Search for {company} ({ticker}) earnings reported on {date} and its "
        f"full-year {fy_label} guidance.\n\n"
        f"The card frames each guidance line two ways:\n"
        f"  * RAISE / FLAT / CUT  -- the NEW guidance midpoint vs the company's "
        f"OWN PRIOR full-year guidance midpoint (the one it gave the previous "
        f"quarter for this same fiscal year).\n"
        f"  * ABOVE / IN-LINE / BELOW -- the NEW guidance midpoint vs the Wall "
        f"Street CONSENSUS that stood JUST BEFORE this print.\n"
        f"So for every metric we want THREE numbers: the new midpoint, the "
        f"company's prior-guide midpoint, and the prior Street consensus "
        f"midpoint. Return whichever you can ground; null for the rest.\n\n"
        f"Cover four metrics: FY revenue, FY non-GAAP EPS, FY operating "
        f"income/profit, and FY free cash flow (FCF). Many companies guide only "
        f"some of these -- return null for any metric the company did not guide.\n\n"
        f"Return ONLY this JSON (numbers only, no units; revenue / operating "
        f"income / FCF in USD dollars e.g. 41200000000 for $41.2B; EPS in "
        f"dollars e.g. 11.20):\n"
        f'{{\n'
        f'  "fy_rev_guide_midpoint": <new FY revenue guidance midpoint, or null>,\n'
        f'  "fy_rev_guide_midpoint_prior": <company PRIOR FY revenue guide '
        f'midpoint (previous quarter), or null>,\n'
        f'  "fy_rev_consensus_prior": <prior FY revenue Street consensus '
        f'midpoint, or null>,\n'
        f'  "fy_eps_guide_midpoint": <new FY non-GAAP EPS guidance midpoint, or null>,\n'
        f'  "fy_eps_guide_midpoint_prior": <company PRIOR FY non-GAAP EPS guide '
        f'midpoint, or null>,\n'
        f'  "fy_eps_consensus_prior": <prior FY non-GAAP EPS Street consensus '
        f'midpoint, or null>,\n'
        f'  "fy_op_metric_kind": <"operating_income" or "operating_profit" '
        f'matching how the company reports it, or null>,\n'
        f'  "fy_op_profit_guide_midpoint_new": <new FY operating income/profit '
        f'guidance midpoint, or null>,\n'
        f'  "fy_op_profit_guide_midpoint_prior": <company PRIOR FY operating '
        f'income/profit guide midpoint, or null>,\n'
        f'  "fy_op_profit_consensus_prior": <prior FY operating income/profit '
        f'Street consensus midpoint, or null>,\n'
        f'  "fy_fcf_guide_midpoint_new": <new FY free cash flow guidance '
        f'midpoint, or null>,\n'
        f'  "fy_fcf_guide_midpoint_prior": <company PRIOR FY free cash flow '
        f'guide midpoint, or null>,\n'
        f'  "fy_fcf_consensus_prior": <prior FY free cash flow Street consensus '
        f'midpoint, or null>\n'
        f'}}\n'
        f"If the company gave no FY guidance on this date, return null for every "
        f"field. Never guess a number you cannot ground in the actual release."
    )


def _num(v):
    """Accept a JSON number or numeric string; reject everything else."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f else None  # reject NaN
    if isinstance(v, str):
        s = v.strip().replace("$", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _fiscal_year_label(rec: dict) -> str:
    fq = rec.get("fiscal_quarter") or ""
    # Try to surface an "FYxx" hint from the fiscal_quarter string; fall back to
    # a generic "current fiscal year" phrasing the model can resolve from date.
    import re
    m = re.search(r"FY\s*'?(\d{2,4})", fq, re.I)
    if m:
        return f"FY{m.group(1)}"
    return "the current fiscal year"


def _candidates(tickers: dict, only: str | None):
    """Active POST tickers that still need the widened FY-guide fetch.

    A card is a candidate when EITHER guidance pill is empty OR it has not yet
    been through the widened pass (which adds prior-guide baselines + the
    OpInc/FCF profitability cascade). The ``fy_guide_widened`` marker caps each
    ticker at ONE widened pass: once set, the card is skipped even if some
    fields came back null (the company simply did not guide them), so we never
    refetch indefinitely.
    """
    out = []
    for tk, rec in tickers.items():
        if only and tk != only:
            continue
        if rec.get("state") != "post_earnings":
            continue
        review = rec.get("post_earnings_review") or {}
        if review.get("active") is not True:
            continue
        gvc = rec.get("guide_vs_consensus") or {}
        # Cap: a card that already went through the widened pass is done, even
        # if some metrics returned null (not every company guides OpInc/FCF).
        if gvc.get("fy_guide_widened") is True:
            continue
        rev_present = any(
            gvc.get(k) is not None
            for k in ("fy_rev_guide_midpoint_new", "fy_rev_change_vs_consensus_pct")
        )
        eps_present = any(
            gvc.get(k) is not None
            for k in ("fy_eps_guide_midpoint_new", "fy_eps_guide_change_vs_consensus_pct")
        )
        profit_present = any(
            gvc.get(k) is not None
            for k in ("fy_op_profit_guide_midpoint_new", "fy_fcf_guide_midpoint_new")
        )
        # Fix a card if any pill is missing data the widened pass can supply:
        # revenue, EPS, or the OpInc/FCF profitability fallback. A card already
        # carrying all three (and the widened marker) is skipped above.
        if rev_present and eps_present and profit_present:
            continue
        out.append((tk, rec))
    return out


def backfill(only: str | None, dry_run: bool, cap: int):
    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})

    cands = _candidates(tickers, only)
    written, no_data, errored = [], [], []
    used = 0

    for tk, rec in cands:
        if cap and used >= cap:  # cap == 0 -> unlimited
            break
        used += 1
        date = rec.get("last_earnings_date") or (
            rec.get("post_earnings_review") or {}
        ).get("earnings_date") or ""
        company = rec.get("company_name") or tk
        fy_label = _fiscal_year_label(rec)

        # On a dry-run, only consult the local cache — NEVER call_perplexity,
        # because a cache miss would queue a task into pending_tasks.json (a
        # write side-effect a dry-run must not have). On a real run we issue the
        # call (cache hit short-circuits inside call_perplexity anyway).
        if dry_run:
            if research_cache_exists(tk, "finance_estimates"):
                res = load_research_cache(tk, "finance_estimates")
            else:
                no_data.append(tk)
                continue
        else:
            try:
                res = call_perplexity(
                    ticker=tk,
                    task="finance_estimates",
                    prompt=_prompt(tk, company, date, fy_label),
                    system=_SYSTEM,
                    max_tokens=500,
                    extra_meta={"model": "sonar", "purpose": "fy_guide_backfill"},
                )
            except Exception as exc:  # noqa: BLE001
                errored.append(f"{tk}: {exc}")
                continue

        if not isinstance(res, dict) or res.get("queued") or res.get("skipped") or res.get("dry_run"):
            no_data.append(tk)
            continue

        rev_mid = _num(res.get("fy_rev_guide_midpoint"))
        rev_prior = _num(res.get("fy_rev_guide_midpoint_prior"))
        rev_cons = _num(res.get("fy_rev_consensus_prior"))
        eps_mid = _num(res.get("fy_eps_guide_midpoint"))
        eps_prior = _num(res.get("fy_eps_guide_midpoint_prior"))
        eps_cons = _num(res.get("fy_eps_consensus_prior"))
        op_kind = res.get("fy_op_metric_kind")
        op_mid = _num(res.get("fy_op_profit_guide_midpoint_new"))
        op_prior = _num(res.get("fy_op_profit_guide_midpoint_prior"))
        op_cons = _num(res.get("fy_op_profit_consensus_prior"))
        fcf_mid = _num(res.get("fy_fcf_guide_midpoint_new"))
        fcf_prior = _num(res.get("fy_fcf_guide_midpoint_prior"))
        fcf_cons = _num(res.get("fy_fcf_consensus_prior"))
        if rev_mid is None and eps_mid is None and op_mid is None and fcf_mid is None:
            no_data.append(tk)
            continue

        gvc = rec.get("guide_vs_consensus") or {}

        def _set(k, v):
            if v is not None and gvc.get(k) is None:
                gvc[k] = v

        if rev_mid is not None:
            _set("fy_rev_guide_midpoint_new", rev_mid)
            _set("fy_rev_guide_midpoint_prior", rev_prior)
            _set("fy_rev_consensus_prior", rev_cons)
        if eps_mid is not None:
            _set("fy_eps_guide_midpoint_new", eps_mid)
            _set("fy_eps_guide_midpoint_prior", eps_prior)
            _set("fy_eps_consensus_prior", eps_cons)
        if op_mid is not None:
            _set("fy_op_profit_guide_midpoint_new", op_mid)
            _set("fy_op_profit_guide_midpoint_prior", op_prior)
            _set("fy_op_profit_consensus_prior", op_cons)
            if op_kind in ("operating_income", "operating_profit"):
                _set("fy_op_metric_kind", op_kind)
        if fcf_mid is not None:
            _set("fy_fcf_guide_midpoint_new", fcf_mid)
            _set("fy_fcf_guide_midpoint_prior", fcf_prior)
            _set("fy_fcf_consensus_prior", fcf_cons)
        gvc.setdefault("fy_guide_source", "perplexity_sonar")
        gvc.setdefault("fy_guide_fiscal_year", fy_label)
        # Mark the widened pass complete so this card is not refetched again,
        # even if the company guided only some of the four metrics.
        gvc["fy_guide_widened"] = True
        rec["guide_vs_consensus"] = gvc

        # Derive the card-facing pill fields now, so they persist with the
        # envelope. Without this the raw midpoints sit on the record but the
        # POST card (which reads the normalized guidanceRevenueDeltaPct /
        # guidanceEpsDeltaPct) still renders "—".
        normalized = normalize_guidance_envelope(gvc)
        for k in NORMALIZED_GUIDANCE_FIELDS:
            if k in normalized and normalized[k] is not None:
                rec[k] = normalized[k]

        written.append(tk)

    if written and not dry_run:
        INTEL_PATH.write_text(json.dumps(intel, indent=2))

    return cands, written, no_data, errored, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Limit to a single ticker")
    ap.add_argument("--max-backfills", type=int, default=_DEFAULT_CAP)
    args = ap.parse_args()

    cands, written, no_data, errored, used = backfill(
        args.ticker, args.dry_run, args.max_backfills
    )

    tag = "[DRY RUN] " if args.dry_run else "[OK] "
    print(
        f"{tag}candidates(empty pills)={len(cands)} calls_used={used} "
        f"written={len(written)} no_guidance={len(no_data)} errors={len(errored)}"
    )
    if written:
        print("  written:", ", ".join(sorted(written)))
    if errored:
        print("  errors:", "; ".join(errored[:10]))


if __name__ == "__main__":
    main()
