#!/usr/bin/env python3
"""One-off: fetch PANW + GTLB Q ending 2026-04-30 actuals + consensus via Perplexity sonar.
yfinance and Finnhub free tier both lack revenue actuals for these June 2 reports.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.perplexity.client import call_perplexity

INTEL = Path(__file__).resolve().parents[1] / "earnings_intel.json"

PROMPT = """You are a buy-side equity research analyst. {ticker} ({company}) reported earnings on 2026-06-02 for the fiscal quarter ending {qend}.

Return a JSON object with these fields (use null for unknowns, never guess):
- rev_actual_usd_millions: number (in $M USD)
- rev_consensus_usd_millions: number (sell-side consensus going into the print, in $M USD)
- rev_surprise_pct: number (percentage, e.g. 0.7 for +0.7%)
- fy_rev_guide_new_low_usd_millions: number (new FY revenue guidance midpoint LOW end)
- fy_rev_guide_new_high_usd_millions: number
- fy_rev_guide_prior_low_usd_millions: number (PRIOR FY guidance the company had given before this print)
- fy_rev_guide_prior_high_usd_millions: number
- fy_rev_consensus_prior_usd_millions: number (Street FY consensus going into the print)
- fy_eps_guide_new_midpoint: number (non-GAAP EPS midpoint of new FY guidance, $)
- fy_eps_guide_prior_midpoint: number
- fy_eps_consensus_prior: number
- fy_op_metric_kind: string ("operating_income" | "operating_profit" | null)
- fy_op_profit_guide_new_midpoint_usd_millions: number
- fy_op_profit_guide_prior_midpoint_usd_millions: number
- fy_op_profit_consensus_prior_usd_millions: number
- fy_fcf_guide_new_midpoint_usd_millions: number
- fy_fcf_guide_prior_midpoint_usd_millions: number
- fy_fcf_consensus_prior_usd_millions: number
- fy_label: string (e.g. "FY2026" or "FY2027" — what the company calls it)
- source_urls: array of strings

Be precise. Do not return placeholders. Return strict JSON only."""


def main():
    intel = json.loads(INTEL.read_text())
    cases = [
        ("PANW", "Palo Alto Networks", "2026-04-30"),
        ("GTLB", "GitLab Inc.", "2026-04-30"),
    ]
    for tk, name, qend in cases:
        print(f"=== {tk} ({name}, Q ending {qend}) ===")
        prompt = PROMPT.format(ticker=tk, company=name, qend=qend)
        os.environ["USE_PPLX_API"] = "true"  # bypass queue; direct API
        resp = call_perplexity(
            ticker=tk,
            task="backfill_panw_gtlb_actuals",
            prompt=prompt,
            force=True,
        )
        if not isinstance(resp, dict) or resp.get("skipped"):
            print(f"  [ERROR] skipped: {resp}")
            continue
        data = resp.get("raw") if "raw" in resp else resp
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                print(f"  [ERROR] Could not parse JSON: {data[:300]}")
                continue

        rec = intel["tickers"][tk]
        rvc = rec.setdefault("results_vs_consensus", {})
        gvc = rec.setdefault("guide_vs_consensus", {})

        rev_act = data.get("rev_actual_usd_millions")
        rev_est = data.get("rev_consensus_usd_millions")
        rev_surp = data.get("rev_surprise_pct")

        def _fmt_rev(v):
            if v is None:
                return None
            try:
                n = float(v) * 1_000_000  # input is in millions
            except (TypeError, ValueError):
                return None
            a = abs(n)
            if a >= 1e9:
                return f"${n/1e9:.2f}B"
            elif a >= 1e6:
                return f"${n/1e6:.0f}M"
            return f"${n:.0f}"

        if rev_act is not None:
            rvc["in_quarter_rev_actual"] = _fmt_rev(rev_act)
            rvc["rev_actual_source"] = "perplexity_sonar"
        if rev_est is not None:
            rvc["in_quarter_rev_estimate"] = _fmt_rev(rev_est)
            rvc["rev_consensus_source"] = "perplexity_sonar"
        if rev_surp is not None:
            try:
                rvc["in_quarter_rev_surprise_pct"] = round(float(rev_surp), 2)
                rvc["rev_surprise_source"] = "perplexity_sonar"
            except (TypeError, ValueError):
                pass
        elif rev_act is not None and rev_est is not None:
            try:
                rvc["in_quarter_rev_surprise_pct"] = round(
                    (float(rev_act) - float(rev_est)) / float(rev_est) * 100, 2
                )
                rvc["rev_surprise_source"] = "computed_from_perplexity"
            except (TypeError, ZeroDivisionError, ValueError):
                pass

        # Guidance fields -----------------------------------------------------
        new_lo = data.get("fy_rev_guide_new_low_usd_millions")
        new_hi = data.get("fy_rev_guide_new_high_usd_millions")
        if new_lo is not None and new_hi is not None:
            gvc["fy_rev_guide_midpoint_new"] = (float(new_lo) + float(new_hi)) / 2 * 1_000_000
        prior_lo = data.get("fy_rev_guide_prior_low_usd_millions")
        prior_hi = data.get("fy_rev_guide_prior_high_usd_millions")
        if prior_lo is not None and prior_hi is not None:
            gvc["fy_rev_guide_midpoint_prior"] = (float(prior_lo) + float(prior_hi)) / 2 * 1_000_000
        fy_cons = data.get("fy_rev_consensus_prior_usd_millions")
        if fy_cons is not None:
            gvc["fy_rev_consensus_prior"] = float(fy_cons) * 1_000_000

        for k_in, k_out in [
            ("fy_eps_guide_new_midpoint", "fy_eps_guide_midpoint_new"),
            ("fy_eps_guide_prior_midpoint", "fy_eps_guide_midpoint_prior"),
            ("fy_eps_consensus_prior", "fy_eps_consensus_prior"),
        ]:
            v = data.get(k_in)
            if v is not None:
                try:
                    gvc[k_out] = float(v)
                except (TypeError, ValueError):
                    pass

        for k_in, k_out in [
            ("fy_op_profit_guide_new_midpoint_usd_millions", "fy_op_profit_guide_midpoint_new"),
            ("fy_op_profit_guide_prior_midpoint_usd_millions", "fy_op_profit_guide_midpoint_prior"),
            ("fy_op_profit_consensus_prior_usd_millions", "fy_op_profit_consensus_prior"),
            ("fy_fcf_guide_new_midpoint_usd_millions", "fy_fcf_guide_midpoint_new"),
            ("fy_fcf_guide_prior_midpoint_usd_millions", "fy_fcf_guide_midpoint_prior"),
            ("fy_fcf_consensus_prior_usd_millions", "fy_fcf_consensus_prior"),
        ]:
            v = data.get(k_in)
            if v is not None:
                try:
                    gvc[k_out] = float(v) * 1_000_000
                except (TypeError, ValueError):
                    pass

        if data.get("fy_op_metric_kind"):
            gvc["fy_op_metric_kind"] = data["fy_op_metric_kind"]
        if data.get("fy_label"):
            gvc["fy_guide_fiscal_year"] = data["fy_label"]
        gvc["fy_guide_source"] = "perplexity_sonar"
        gvc["fy_guide_widened"] = True

        print(f"  REV act/est/surp: {rvc.get('in_quarter_rev_actual')} / {rvc.get('in_quarter_rev_estimate')} / {rvc.get('in_quarter_rev_surprise_pct')}")
        print(f"  EPS act/est/surp: {rvc.get('in_quarter_eps_actual')} / {rvc.get('in_quarter_eps_estimate')} / {rvc.get('in_quarter_eps_surprise_pct')}")
        print(f"  FY rev guide new/prior/cons: {gvc.get('fy_rev_guide_midpoint_new')} / {gvc.get('fy_rev_guide_midpoint_prior')} / {gvc.get('fy_rev_consensus_prior')}")
        print(f"  FY eps guide new/prior/cons: {gvc.get('fy_eps_guide_midpoint_new')} / {gvc.get('fy_eps_guide_midpoint_prior')} / {gvc.get('fy_eps_consensus_prior')}")
        print(f"  FY op guide kind={gvc.get('fy_op_metric_kind')} new={gvc.get('fy_op_profit_guide_midpoint_new')}")
        print(f"  FY fcf guide new={gvc.get('fy_fcf_guide_midpoint_new')}")

    INTEL.write_text(json.dumps(intel, indent=2, default=str))
    print("\nSaved.")


if __name__ == "__main__":
    main()
