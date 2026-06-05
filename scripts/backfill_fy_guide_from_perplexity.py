#!/usr/bin/env python3
"""
[DEPRECATED] Subsumed by the per-ticker pipeline (automation/pipeline +
automation/sources/perplexity_source.py). Dormant under the new architecture;
kept for one cycle for manual debugging. Removal scheduled in a follow-up PR
after 7 days of clean new-pipeline runs.

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
import os
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
        f"forward guidance, both full-year {fy_label} AND the next fiscal "
        f"quarter (Q+1).\n\n"
        f"IMPORTANT: Some companies only guide next quarter (e.g. AVGO, MRVL, "
        f"NVDA), not the full year. Always return next-Q numbers when given, "
        f"even if no FY numbers are guided.\n\n"
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
        f'midpoint, or null>,\n'
        f'  "q_next_rev_guide_midpoint": <new NEXT-QUARTER revenue guidance '
        f'midpoint in USD dollars, or null>,\n'
        f'  "q_next_rev_guide_midpoint_prior": <company PRIOR NEXT-QUARTER '
        f'revenue guide midpoint if previously updated, or null>,\n'
        f'  "q_next_rev_consensus_prior": <prior NEXT-QUARTER revenue Street '
        f'consensus midpoint (Yahoo Finance / Visible Alpha / Refinitiv / FactSet '
        f'/ Bloomberg / Zacks / StreetAccount), or null>,\n'
        f'  "q_next_eps_guide_midpoint": <new NEXT-QUARTER non-GAAP EPS guidance '
        f'midpoint in USD, or null>,\n'
        f'  "q_next_eps_guide_midpoint_prior": <company PRIOR NEXT-QUARTER non-GAAP '
        f'EPS guide midpoint if previously updated, or null>,\n'
        f'  "q_next_eps_consensus_prior": <prior NEXT-QUARTER non-GAAP EPS Street '
        f'consensus midpoint, or null>,\n'
        f"  // ---- SaaS north-star metrics ----\n"
        f"  // Many software companies do NOT emphasize revenue. They guide and "
        f"the Street tracks ARR (annual recurring revenue), cRPO (current "
        f"remaining performance obligations), billings, and/or NRR/NDR (net "
        f"revenue/dollar retention). Capture every one the company actually "
        f"guided or that the Street has a consensus for, for BOTH the next "
        f"fiscal quarter (q_next_*) and the full year (fy_*). ARR / cRPO / "
        f"billings are USD MILLIONS (e.g. 4200 for $4.2B). NRR is a PERCENT "
        f"(e.g. 118 for 118%). Return null for any the company does not use.\n"
        f'  "fy_arr_guide_midpoint_new": <new FY ARR midpoint USD millions, or null>,\n'
        f'  "fy_arr_guide_midpoint_prior": <company PRIOR FY ARR guide midpoint, or null>,\n'
        f'  "fy_arr_consensus_prior": <prior FY ARR Street consensus midpoint, or null>,\n'
        f'  "q_next_arr_guide_midpoint_new": <new NEXT-Q ARR midpoint USD millions, or null>,\n'
        f'  "q_next_arr_guide_midpoint_prior": <company PRIOR NEXT-Q ARR guide, or null>,\n'
        f'  "q_next_arr_consensus_prior": <prior NEXT-Q ARR Street consensus, or null>,\n'
        f'  "fy_crpo_guide_midpoint_new": <new FY cRPO midpoint USD millions, or null>,\n'
        f'  "fy_crpo_guide_midpoint_prior": <company PRIOR FY cRPO guide, or null>,\n'
        f'  "fy_crpo_consensus_prior": <prior FY cRPO Street consensus, or null>,\n'
        f'  "q_next_crpo_guide_midpoint_new": <new NEXT-Q cRPO midpoint USD millions, or null>,\n'
        f'  "q_next_crpo_guide_midpoint_prior": <company PRIOR NEXT-Q cRPO guide, or null>,\n'
        f'  "q_next_crpo_consensus_prior": <prior NEXT-Q cRPO Street consensus, or null>,\n'
        f'  "fy_billings_guide_midpoint_new": <new FY billings midpoint USD millions, or null>,\n'
        f'  "fy_billings_guide_midpoint_prior": <company PRIOR FY billings guide, or null>,\n'
        f'  "fy_billings_consensus_prior": <prior FY billings Street consensus, or null>,\n'
        f'  "q_next_billings_guide_midpoint_new": <new NEXT-Q billings midpoint USD millions, or null>,\n'
        f'  "q_next_billings_guide_midpoint_prior": <company PRIOR NEXT-Q billings guide, or null>,\n'
        f'  "q_next_billings_consensus_prior": <prior NEXT-Q billings Street consensus, or null>,\n'
        f'  "fy_nrr_guide_midpoint_new": <new FY NRR/NDR percent (e.g. 118), or null>,\n'
        f'  "fy_nrr_guide_midpoint_prior": <company PRIOR FY NRR guide percent, or null>,\n'
        f'  "fy_nrr_consensus_prior": <prior FY NRR Street consensus percent, or null>,\n'
        f'  "q_next_nrr_guide_midpoint_new": <new NEXT-Q NRR/NDR percent, or null>,\n'
        f'  "q_next_nrr_guide_midpoint_prior": <company PRIOR NEXT-Q NRR guide percent, or null>,\n'
        f'  "q_next_nrr_consensus_prior": <prior NEXT-Q NRR Street consensus percent, or null>,\n'
        f"  // ---- bottom-line (profitability) north-star metrics ----\n"
        f"  // Some companies care about FCF, adjusted EBITDA, or adjusted "
        f"operating income (non-GAAP operating income) MORE than EPS for their "
        f"profitability guidance. Capture every one the company actually guided "
        f"or that the Street tracks, for BOTH the next fiscal quarter "
        f"(q_next_*) and the full year (fy_*). FCF / adj EBITDA / adj operating "
        f"income are USD MILLIONS (e.g. 1200 for $1.2B). Return null for any the "
        f"company does not use. (FCF fy_fcf_* may already be requested above -- "
        f"reuse the same grounded figure.)\n"
        f'  "fy_adj_ebitda_guide_midpoint_new": <new FY adj EBITDA midpoint USD millions, or null>,\n'
        f'  "fy_adj_ebitda_guide_midpoint_prior": <company PRIOR FY adj EBITDA guide, or null>,\n'
        f'  "fy_adj_ebitda_consensus_prior": <prior FY adj EBITDA Street consensus, or null>,\n'
        f'  "q_next_adj_ebitda_guide_midpoint_new": <new NEXT-Q adj EBITDA midpoint USD millions, or null>,\n'
        f'  "q_next_adj_ebitda_guide_midpoint_prior": <company PRIOR NEXT-Q adj EBITDA guide, or null>,\n'
        f'  "q_next_adj_ebitda_consensus_prior": <prior NEXT-Q adj EBITDA Street consensus, or null>,\n'
        f'  "fy_adj_op_income_guide_midpoint_new": <new FY adj (non-GAAP) operating income midpoint USD millions, or null>,\n'
        f'  "fy_adj_op_income_guide_midpoint_prior": <company PRIOR FY adj op income guide, or null>,\n'
        f'  "fy_adj_op_income_consensus_prior": <prior FY adj op income Street consensus, or null>,\n'
        f'  "q_next_adj_op_income_guide_midpoint_new": <new NEXT-Q adj (non-GAAP) operating income midpoint USD millions, or null>,\n'
        f'  "q_next_adj_op_income_guide_midpoint_prior": <company PRIOR NEXT-Q adj op income guide, or null>,\n'
        f'  "q_next_adj_op_income_consensus_prior": <prior NEXT-Q adj op income Street consensus, or null>,\n'
        f'  "fy_top_line_north_star_metric": <the ONE TOP-LINE metric this '
        f'company emphasizes as its primary FY guidance KPI: one of "revenue", '
        f'"ARR", "cRPO", "billings", "NRR", or null if it leads with revenue>,\n'
        f'  "q_next_top_line_north_star_metric": <the ONE TOP-LINE metric this '
        f'company emphasizes for its NEXT-QUARTER guide: "revenue", "ARR", '
        f'"cRPO", "billings", "NRR", or null>,\n'
        f'  "fy_bottom_line_north_star_metric": <the ONE BOTTOM-LINE '
        f'(profitability) metric this company emphasizes for its FY guide: one '
        f'of "EPS", "FCF", "adj_EBITDA", "adj_op_income", or null if it leads '
        f'with EPS>,\n'
        f'  "q_next_bottom_line_north_star_metric": <the ONE BOTTOM-LINE metric '
        f'this company emphasizes for its NEXT-QUARTER guide: "EPS", "FCF", '
        f'"adj_EBITDA", "adj_op_income", or null>,\n'
        f'  "north_star_evidence": <a ONE-SENTENCE citation grounding your '
        f'top-line and bottom-line north-star choices in the company\'s actual '
        f'IR materials, e.g. "CRWD Q3 FY24 press-release headline reads \'Record '
        f'Net New ARR of $223.1M\'; FCF margin featured in prepared remarks." '
        f"Quote the headline / lead KPI you saw. null only if you truly could "
        f'not find the company\'s emphasis.>\n'
        f'}}\n'
        f"NORTH STAR -- IDENTIFY WHAT THE COMPANY LEADS WITH, NOT THE MOST "
        f"GENERICALLY-QUOTED METRIC: For top_line_north_star_metric, identify "
        f"which metric the COMPANY LEADS WITH in its quarterly press-release "
        f"headline, prepared-remarks opening, and investor-day framing. Many "
        f"SaaS companies lead with ARR, cRPO, billings, or product revenue "
        f"rather than total revenue -- return the metric the company itself "
        f"featured most prominently in its most recent earnings communication. "
        f"Same instruction for bottom_line_north_star_metric: identify which "
        f"profitability metric the company leads with (e.g. Net New ARR / FCF / "
        f"adj op income / adj EBITDA / adj EPS), NOT the most universally "
        f"tracked metric (GAAP EPS). The top-line side is "
        f"revenue/ARR/cRPO/billings/NRR; the bottom-line side is "
        f"EPS/FCF/adj_EBITDA/adj_op_income. Never invent a metric the company "
        f"did not report.\n"
        f"ANTI-PATTERN -- DO NOT DEFAULT TO 'revenue + EPS': Do NOT default to "
        f"'revenue + EPS' when the company has a non-GAAP or non-revenue "
        f"headline metric. GAAP EPS is rarely the metric SaaS companies lead "
        f"with -- if you cannot find a more company-emphasized profitability "
        f"metric, return that anyway only after explicitly searching the "
        f"company's IR materials for FCF / adj op income / adj EBITDA "
        f"references.\n"
        f"DIRECTIVE EVIDENCE RULE for bottom_line_north_star_metric:\n"
        f"- If the company's most recent 10-Q, earnings press release, "
        f"prepared remarks, or current-quarter guidance explicitly references "
        f"non-GAAP operating income, non-GAAP operating margin, free cash flow, "
        f"or adjusted EBITDA as a GUIDED metric (i.e., the company itself "
        f"provides a forward range or target for that metric), you MUST return "
        f"that metric -- NOT EPS.\n"
        f"- For SaaS / health-tech / vertical-software companies in particular "
        f"(e.g., VEEV, DOCU, SNOW, NOW, CRM, MDB, GWRE), assume the company "
        f"guides non-GAAP operating income or FCF unless evidence proves "
        f"otherwise. Confirm via the most recent press release header bullets "
        f"or the 'Business Outlook' / 'Guidance' section.\n"
        f"- north_star_evidence MUST quote or paraphrase the company's actual "
        f"guidance language (e.g., 'guided Q2 FY27 non-GAAP operating income to "
        f"$XXM-$YYM'). If you cannot find a non-EPS guided profitability metric "
        f"after this directed search, only THEN return EPS as the bottom-line "
        f"north star, and the evidence string must state 'confirmed no non-GAAP "
        f"op income/FCF/EBITDA guidance in [source]'.\n"
        f"EXAMPLES OF NORTH STARS BY COMPANY (verify against the company's most "
        f"recent earnings press release before returning):\n"
        f"  - CRWD: top=ARR (Net New ARR is the headline), bottom=FCF\n"
        f"  - CRM (Salesforce): top=cRPO, bottom=adj_op_income (non-GAAP "
        f"operating income/margin)\n"
        f"  - NOW (ServiceNow): top=cRPO, bottom=adj_op_income (non-GAAP "
        f"operating margin)\n"
        f"  - DDOG: top=revenue, bottom=FCF (FCF and FCF margin are headline)\n"
        f"  - PANW: top=ARR (NGS ARR) + RPO, bottom=FCF\n"
        f"  - ZS: top=billings, bottom=FCF\n"
        f"  - NET (Cloudflare): top=revenue, bottom=FCF\n"
        f"  - VEEV: top=revenue, bottom=non-GAAP operating income (Veeva "
        f"guides non-GAAP operating income explicitly each quarter)\n"
        f"  - DOCU: top=revenue (subscription), bottom=non-GAAP operating "
        f"margin or FCF (DocuSign guides non-GAAP operating margin range each "
        f"quarter)\n"
        f"  - SNOW: top=revenue (product revenue), bottom=adj_op_income / FCF\n"
        f"  - MDB: top=revenue (Atlas), bottom=adj_op_income\n"
        f"  - GWRE: top=ARR, bottom=adj_op_income\n"
        f"  - CIEN: top=revenue, bottom=adj_op_income (already correct)\n"
        f"  - AVGO: top=revenue, bottom=adj_EBITDA (already correct)\n"
        f"  - RBRK: top=ARR, bottom=FCF / adj op margin (already correct)\n"
        f"  For tickers NOT in this list: research the most recent earnings "
        f"press release and identify what the company leads with.\n"
        f"CRITICAL -- GROUND THE NORTH-STAR VALUE: If you set north_star_metric "
        f"to ARR, cRPO, billings, or NRR, you MUST also return that metric's "
        f"actual reported figure and its prior-period value. These are disclosed "
        f"every quarter in the earnings press release financial tables and the "
        f"prepared remarks (e.g. CrowdStrike reports ending ARR and net-new ARR "
        f"in dollars; Snowflake reports product revenue and RPO; many report "
        f"cRPO and a dollar-based net retention rate). The current-quarter "
        f"ENDING level of the metric IS the _guide_midpoint_new for that metric, "
        f"and the year-ago / prior-quarter ENDING level is the "
        f"_guide_midpoint_prior. Do not return a north_star_metric of ARR/cRPO/"
        f"billings/NRR while leaving its midpoint fields null -- if you cannot "
        f'ground the figure, set north_star_metric to "revenue" instead.\n'
        f"UNITS: ARR / cRPO / billings in USD MILLIONS (number only). NRR as a "
        f"plain percent number. Keep units consistent with the prior/consensus "
        f"values for the same metric so the comparison is apples-to-apples.\n"
        f"CONSENSUS GROUNDING: Wall Street consensus midpoints are publicly "
        f"available for every major US-listed name. Before returning null for "
        f"any *_consensus_prior field, search Yahoo Finance Analysts tab, Zacks, "
        f"StreetAccount, Visible Alpha, Refinitiv estimates, FactSet, or "
        f"Bloomberg consensus. For the chosen north-star metric specifically, "
        f"consensus must come from Visible Alpha / StreetAccount / Bloomberg / "
        f"Yahoo; for SaaS-specific metrics (ARR, cRPO, billings) Visible Alpha "
        f"and StreetAccount are the right aggregators. Only return null when the "
        f"company itself is covered by zero sell-side analysts (extremely rare "
        f"for any name on a buy-side watchlist).\n\n"
        f"If the company gave no guidance at all on this date, return null for "
        f"every guidance field. Never guess a number you cannot ground in the "
        f"actual release or in a reputable estimate aggregator. Return only the "
        f"JSON object, nothing else."
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


# SaaS metric prefixes -> canonical units. Units are intrinsic to the metric
# (ARR/cRPO/billings reported in USD millions, NRR in percent), so we stamp them
# deterministically rather than trusting a free-text units field from the model.
_SAAS_METRIC_UNITS = {
    "arr": "USD millions",
    "crpo": "USD millions",
    "billings": "USD millions",
    "nrr": "%",
}


def _extract_saas_metrics(res: dict) -> dict:
    """Pull the SaaS north-star metric families from a sonar response.

    Returns {"fields": {envelope_key: value, ...}, "any_value": bool}. Only
    grounded (non-null) midpoints produce fields; prior/consensus baselines and
    the per-metric units are attached alongside any present new midpoint. The
    per-horizon north_star pointer is carried through when the model named one.
    """
    fields: dict = {}
    any_value = False
    for horizon in ("fy", "q_next"):
        for metric, units in _SAAS_METRIC_UNITS.items():
            base = f"{horizon}_{metric}"
            new = _num(res.get(f"{base}_guide_midpoint_new"))
            if new is None:
                continue
            any_value = True
            fields[f"{base}_guide_midpoint_new"] = new
            prior = _num(res.get(f"{base}_guide_midpoint_prior"))
            cons = _num(res.get(f"{base}_consensus_prior"))
            if prior is not None:
                fields[f"{base}_guide_midpoint_prior"] = prior
            if cons is not None:
                fields[f"{base}_consensus_prior"] = cons
            fields[f"{base}_units"] = units
        # Top-line north-star pointer. Accept the new field name and the legacy
        # `north_star_metric` (pre-rename) for back-compat.
        ns = res.get(f"{horizon}_top_line_north_star_metric")
        if not (isinstance(ns, str) and ns.strip()):
            ns = res.get(f"{horizon}_north_star_metric")
        if isinstance(ns, str):
            ns = ns.strip()
            if ns and ns.lower() not in ("null", "none", "n/a"):
                # Persist under both keys so existing readers (which look at
                # *_north_star_metric) and the new top-line readers both work.
                fields[f"{horizon}_north_star_metric"] = ns
                fields[f"{horizon}_top_line_north_star_metric"] = ns
                ns_units = _north_star_units(ns)
                if ns_units:
                    fields[f"{horizon}_north_star_units"] = ns_units
                    fields[f"{horizon}_top_line_north_star_units"] = ns_units
    return {"fields": fields, "any_value": any_value}


# Bottom-line (profitability) metric prefixes -> canonical units. All three new
# dollar metrics report in USD millions; the cap/baseline logic treats them the
# same as the SaaS USD-millions metrics.
_BOTTOM_LINE_METRIC_UNITS = {
    "fcf": "USD millions",
    "adj_ebitda": "USD millions",
    "adj_op_income": "USD millions",
}


def _extract_bottom_line_metrics(res: dict) -> dict:
    """Pull the bottom-line north-star metric families from a sonar response.

    Parallel to _extract_saas_metrics: FCF / adj EBITDA / adj op income for both
    horizons, plus the per-horizon bottom_line_north_star pointer (+ units).
    Returns {"fields": {...}, "any_value": bool}. FCF's fy_fcf_* family may also
    be populated by the legacy FY block; _set is fill-only so there is no clash.
    """
    fields: dict = {}
    any_value = False
    for horizon in ("fy", "q_next"):
        for metric, units in _BOTTOM_LINE_METRIC_UNITS.items():
            base = f"{horizon}_{metric}"
            new = _num(res.get(f"{base}_guide_midpoint_new"))
            if new is None:
                continue
            any_value = True
            fields[f"{base}_guide_midpoint_new"] = new
            prior = _num(res.get(f"{base}_guide_midpoint_prior"))
            cons = _num(res.get(f"{base}_consensus_prior"))
            if prior is not None:
                fields[f"{base}_guide_midpoint_prior"] = prior
            if cons is not None:
                fields[f"{base}_consensus_prior"] = cons
            fields[f"{base}_units"] = units
        ns = res.get(f"{horizon}_bottom_line_north_star_metric")
        if isinstance(ns, str):
            ns = ns.strip()
            if ns and ns.lower() not in ("null", "none", "n/a"):
                fields[f"{horizon}_bottom_line_north_star_metric"] = ns
                ns_units = _bottom_line_north_star_units(ns)
                if ns_units:
                    fields[f"{horizon}_bottom_line_north_star_units"] = ns_units
    return {"fields": fields, "any_value": any_value}


def _bottom_line_north_star_units(metric: str) -> str | None:
    """Map a bottom_line_north_star_metric label to its display units."""
    m = metric.strip().lower()
    if m in ("fcf", "free_cash_flow", "adj_ebitda", "adj_op_income",
             "adj_operating_income", "non_gaap_op_income"):
        return "USD millions"
    if m == "eps":
        return "USD"
    return None


def _north_star_units(metric: str) -> str | None:
    """Map a north_star_metric label to its display units."""
    m = metric.strip().lower()
    if m in ("arr", "crpo", "billings", "revenue", "rev"):
        return "USD millions"
    if m in ("nrr", "ndr"):
        return "%"
    if m == "eps":
        return "USD"
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


def _candidates(tickers: dict, only: str | None, force_gate: bool = False):
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
        # Active POST cards include records flagged active=True OR records that
        # have already been stamped with note_status=print_reaction/
        # call_reaction (these land active=None until the post_earnings_notes
        # job promotes them — mirror PR #52's render-layer ungate so the
        # guidance backfill doesn't skip freshly-printed cards).
        note_status = rec.get("note_status")
        active = review.get("active")
        reactioned = note_status in ("print_reaction", "call_reaction")
        # Also include cards where the reaction populator has stamped a
        # stock_reaction_pct -- proxy for an active post-print card whose note
        # hasn't been promoted yet (e.g. stub-note cases). Without this the
        # next-Q backfill skips fresh AMC reporters until enrichment lands.
        has_reaction_stamp = (
            review.get("stock_reaction_pct") is not None
            and review.get("stock_reaction_calc_method") is not None
        )
        if active is not True and not reactioned and not has_reaction_stamp:
            continue
        # --force-gate: bypass the "already populated / already widened" skips
        # below so an operator can deliberately re-fetch a card (paired with
        # FORCE_REGENERATE=true, which bypasses the response cache). The
        # state/active checks above still apply -- we only ever regen real,
        # active POST cards.
        if force_gate:
            out.append((tk, rec))
            continue
        gvc = rec.get("guide_vs_consensus") or {}
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
        q_next_present = any(
            gvc.get(k) is not None
            for k in ("q_next_rev_guide_midpoint_new", "q_next_eps_guide_midpoint_new")
        )
        # A card is fully populated only when we have BOTH the FY widened pass
        # AND the next-Q pass (next_q_added marker). The widened-only flag is
        # legacy; if widened is true but no next-Q fields exist, refetch once.
        fully_done = (
            gvc.get("fy_guide_widened") is True
            and gvc.get("next_q_added") is True
        )
        if fully_done:
            continue
        # Original skip: a card with all FY pills filled AND widened is done.
        # Now also require next-Q presence to skip (handles AVGO-class).
        if rev_present and eps_present and profit_present and q_next_present:
            continue
        out.append((tk, rec))
    return out


def backfill(only: str | None, dry_run: bool, cap: int, force_gate: bool = False):
    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})

    cands = _candidates(tickers, only, force_gate=force_gate)
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
                    # The JSON schema carries ~60 fields (revenue/EPS/op/FCF +
                    # SaaS top-line set + bottom-line FCF/EBITDA/adj-op-income +
                    # both north-star pointers + a free-text north_star_evidence
                    # citation). 500 tokens truncates it mid-object, which then
                    # fails JSON parsing and silently drops every metric. The
                    # evidence sentence pushed several responses past 900 (CXM
                    # truncated at ~850 completion tokens), so 1200 restores
                    # headroom. Use sonar-pro: plain `sonar` cannot ground the
                    # ARR/cRPO/FCF/EBITDA figures the north-star pills depend on
                    # (it returns all-null + default revenue/EPS pointers), so
                    # the dual pills never light up.
                    max_tokens=1200,
                    extra_meta={"model": "sonar-pro", "purpose": "fy_guide_backfill"},
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
        # Next-quarter guidance (for companies that only guide one quarter out).
        q_rev_mid = _num(res.get("q_next_rev_guide_midpoint"))
        q_rev_prior = _num(res.get("q_next_rev_guide_midpoint_prior"))
        q_rev_cons = _num(res.get("q_next_rev_consensus_prior"))
        q_eps_mid = _num(res.get("q_next_eps_guide_midpoint"))
        q_eps_prior = _num(res.get("q_next_eps_guide_midpoint_prior"))
        q_eps_cons = _num(res.get("q_next_eps_consensus_prior"))
        # SaaS north-star metrics (ARR / cRPO / billings / NRR) for both horizons.
        saas = _extract_saas_metrics(res)
        # Bottom-line north-star metrics (FCF / adj EBITDA / adj op income).
        bottom = _extract_bottom_line_metrics(res)
        if (rev_mid is None and eps_mid is None and op_mid is None
                and fcf_mid is None and q_rev_mid is None and q_eps_mid is None
                and not saas["any_value"] and not bottom["any_value"]):
            no_data.append(tk)
            continue

        gvc = rec.get("guide_vs_consensus") or {}

        def _set(k, v):
            # Normally fill-only (never clobber a prior grounded value). With
            # --force-gate the operator is deliberately re-fetching this card,
            # so a freshly grounded value replaces the stale one.
            if v is not None and (force_gate or gvc.get(k) is None):
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
        # Next-quarter midpoints. Prior-guide is rarely set (companies usually
        # only guide each quarter once) but kept as a fallback baseline when
        # Street consensus is missing.
        if q_rev_mid is not None:
            _set("q_next_rev_guide_midpoint_new", q_rev_mid)
            _set("q_next_rev_guide_midpoint_prior", q_rev_prior)
            _set("q_next_rev_consensus_prior", q_rev_cons)
        if q_eps_mid is not None:
            _set("q_next_eps_guide_midpoint_new", q_eps_mid)
            _set("q_next_eps_guide_midpoint_prior", q_eps_prior)
            _set("q_next_eps_consensus_prior", q_eps_cons)
        # SaaS north-star metrics: write each metric/horizon family + the
        # per-horizon north_star pointer (+ units) when present.
        for key, val in saas["fields"].items():
            _set(key, val)
        # Bottom-line north-star metrics: write FCF / adj EBITDA / adj op income
        # families + the per-horizon bottom_line_north_star pointer (+ units).
        for key, val in bottom["fields"].items():
            _set(key, val)
        # North-star evidence: a free-text citation grounding the north-star
        # choices. Stored for human review/audit, not parsed into any pill.
        evidence = res.get("north_star_evidence")
        if isinstance(evidence, str):
            evidence = evidence.strip()
            if evidence and evidence.lower() not in ("null", "none", "n/a"):
                if force_gate or gvc.get("north_star_evidence") is None:
                    gvc["north_star_evidence"] = evidence
        gvc.setdefault("fy_guide_source", "perplexity_sonar")
        gvc.setdefault("fy_guide_fiscal_year", fy_label)
        # Mark the widened pass complete so this card is not refetched again,
        # even if the company guided only some of the four metrics.
        gvc["fy_guide_widened"] = True
        gvc["next_q_added"] = True
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
        # Atomic write: serialize to a sibling .tmp then os.replace so a crash
        # mid-write can never leave a torn earnings_intel.json on disk.
        tmp = INTEL_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(intel, indent=2))
        os.replace(tmp, INTEL_PATH)

    return cands, written, no_data, errored, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Limit to a single ticker")
    ap.add_argument("--max-backfills", type=int, default=_DEFAULT_CAP)
    ap.add_argument(
        "--force-gate",
        action="store_true",
        help="Bypass the already-populated/already-widened candidate gate so a "
        "card is re-fetched even after a prior pass. Pair with "
        "FORCE_REGENERATE=true to also bypass the response cache.",
    )
    args = ap.parse_args()

    # When a single ticker is named for a forced cache-bypass run, also bypass
    # the candidate gate by default -- the prior pass's markers would otherwise
    # silently skip the very card the operator asked to regenerate.
    force_gate = args.force_gate or (
        bool(args.ticker)
        and os.environ.get("FORCE_REGENERATE", "false").lower() == "true"
    )

    cands, written, no_data, errored, used = backfill(
        args.ticker, args.dry_run, args.max_backfills, force_gate=force_gate
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
