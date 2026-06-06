#!/usr/bin/env python3
"""
[DEPRECATED] Subsumed by the per-ticker pipeline (automation/pipeline +
automation/sources/perplexity_source.py). Dormant under the new architecture;
kept for one cycle for manual debugging. Removal scheduled in a follow-up PR
after 7 days of clean new-pipeline runs.

Perplexity sonar backfill for IN-QUARTER revenue consensus on POST cards that
have a real ``in_quarter_rev_actual`` but no ``in_quarter_rev_surprise_pct``.

Uses the same instrumented ``call_perplexity`` helper as
``backfill_fy_guide_from_perplexity.py`` so usage is logged to the
``perplexity_api_usage`` Supabase table and responses are cached.

NON-FABRICATION
  * Skips any ticker without ``in_quarter_rev_actual``.
  * Single sonar call per ticker, JSON response.
  * Model is instructed to return null when no consensus was published.
  * Writes only when a real number comes back.

Usage:
  USE_PPLX_API=true PERPLEXITY_API_KEY=... \
    python3 scripts/backfill_rev_consensus_from_perplexity.py \
      [--dry-run] [--ticker XYZ] [--max-backfills N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from automation.perplexity.client import call_perplexity  # noqa: E402
from automation.shared.cache import research_cache_exists, load_research_cache  # noqa: E402

INTEL = ROOT / "earnings_intel.json"

_SYSTEM = (
    "You are a sell-side equity data extractor. Return ONLY a JSON object, no "
    "prose, no markdown fences. Every number must be grounded in the company's "
    "actual earnings release or sell-side consensus for the stated reported "
    "quarter; if you cannot ground a figure, return null for it. Never guess."
)


def _prompt(ticker: str, company: str, date: str, actual_str: str) -> str:
    return (
        f"{company} ({ticker}) reported quarterly earnings on or around "
        f"{date} with revenue of {actual_str}. Find what Wall Street analysts "
        f"were expecting for revenue that quarter (the sell-side consensus / "
        f"estimate). News coverage of the print commonly states this in the "
        f"form \"beat revenue estimates of $X\" or \"missed the $X estimate\". "
        f"You can also derive it from a stated beat/miss percentage if news "
        f"coverage reports both the actual and the surprise.\n\n"
        f"Return ONLY this JSON (numbers only, no units; revenue in USD "
        f"dollars, e.g. 10870000000 for $10.87B):\n"
        f'{{\n'
        f'  "rev_consensus_usd": <consensus revenue in USD dollars, or null>,\n'
        f'  "surprise_pct": <reported beat/miss percent as a number, or null>,\n'
        f'  "period_label": "<e.g. Q1 FY26 ending 2026-04-30>",\n'
        f'  "source_note": "<short note on where the figure came from>"\n'
        f'}}\n'
        f"Return null only if no credible source reports the consensus or a "
        f"beat/miss percentage. Never fabricate. If the company reported a "
        f"3% revenue beat on news coverage, that means consensus was roughly "
        f"actual / 1.03 — but only report the consensus if a real number is "
        f"cited in a source."
    )


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f else None
    if isinstance(v, str):
        s = v.strip().replace("$", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _parse_money(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str):
        return None
    s = s.strip().replace(",", "").replace("$", "")
    m = re.match(r"^([\-+]?\d+(?:\.\d+)?)\s*([bBmMkK])?", s)
    if m:
        val = float(m.group(1))
        suf = (m.group(2) or "").lower()
        if suf == "b":
            return val * 1e9
        if suf == "m":
            return val * 1e6
        if suf == "k":
            return val * 1e3
        return val
    return None


def _money_str(v):
    if v is None:
        return None
    av = abs(v)
    if av >= 1e9:
        return f"${v/1e9:.2f}B"
    if av >= 1e6:
        return f"${v/1e6:.2f}M"
    if av >= 1e3:
        return f"${v/1e3:.2f}K"
    return f"${v:.2f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Limit to one ticker")
    ap.add_argument("--max-backfills", type=int, default=0, help="0 = unlimited")
    args = ap.parse_args()

    data = json.loads(INTEL.read_text())
    tickers = data.get("tickers", {})

    candidates = []
    for t, v in tickers.items():
        if args.ticker and t != args.ticker:
            continue
        if v.get("state") != "post_earnings":
            continue
        rvc = v.get("results_vs_consensus") or {}
        if not rvc.get("in_quarter_rev_actual"):
            continue
        if rvc.get("in_quarter_rev_surprise_pct") is not None:
            continue
        candidates.append((t, v, rvc))

    if args.max_backfills and args.max_backfills > 0:
        candidates = candidates[: args.max_backfills]

    print(f"[INFO] candidates needing rev_consensus: {len(candidates)}")

    written = 0
    no_consensus = 0
    errored = 0

    for t, v, rvc in candidates:
        company = v.get("company_name") or t
        date = v.get("last_earnings_date") or ""
        try:
            if args.dry_run:
                if research_cache_exists(t, "rev_consensus_estimate"):
                    res = load_research_cache(t, "rev_consensus_estimate")
                else:
                    no_consensus += 1
                    continue
            else:
                res = call_perplexity(
                    ticker=t,
                    task="rev_consensus_estimate",
                    prompt=_prompt(t, company, date, rvc.get("in_quarter_rev_actual", "")),
                    system=_SYSTEM,
                    max_tokens=400,
                    extra_meta={"model": "sonar", "purpose": "rev_consensus_backfill"},
                )
        except Exception as e:
            print(f"  [ERR] {t}: {e}")
            errored += 1
            continue

        if not isinstance(res, dict) or res.get("queued") or res.get("skipped") or res.get("dry_run"):
            no_consensus += 1
            continue

        est_raw = res.get("rev_consensus_usd")
        est_val = _num(est_raw)
        # Heuristic: if model returned a small number (<1e5), it probably
        # meant billions/millions raw.
        if est_val is not None and 0 < est_val < 1e5:
            est_val = est_val * 1e9 if est_val < 1e3 else est_val * 1e6

        actual_val = _parse_money(rvc.get("in_quarter_rev_actual"))

        # Fallback path: model reported a surprise % but no absolute estimate.
        # Back-derive the consensus from actual / (1 + surprise/100).
        if (not est_val or est_val <= 0) and actual_val:
            surprise_raw = res.get("surprise_pct")
            surprise_num = _num(surprise_raw)
            if surprise_num is not None and -50 < surprise_num < 200:
                est_val = actual_val / (1.0 + surprise_num / 100.0)
                rvc["rev_consensus_source"] = "perplexity_sonar_derived_from_surprise_pct"
            else:
                no_consensus += 1
                print(f"  [NO CONSENSUS] {t}: model returned null/zero")
                continue
        else:
            rvc["rev_consensus_source"] = "perplexity_sonar"

        if not est_val or est_val <= 0 or not actual_val:
            no_consensus += 1
            continue

        rvc["in_quarter_rev_estimate"] = _money_str(est_val)
        surprise = (actual_val - est_val) / est_val * 100.0
        rvc["in_quarter_rev_surprise_pct"] = round(surprise, 2)
        rvc["rev_surprise_source"] = "computed_from_perplexity"
        written += 1
        print(
            f"  [WRITE] {t}: est={_money_str(est_val)} actual={rvc['in_quarter_rev_actual']} "
            f"surprise={surprise:+.2f}%"
        )

    if not args.dry_run:
        # Atomic write: .tmp + os.replace so a crash mid-write cannot tear the file.
        _tmp = INTEL.with_suffix(INTEL.suffix + ".tmp")
        _tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(_tmp, INTEL)

    print(f"[OK] written={written} no_consensus={no_consensus} errors={errored}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
