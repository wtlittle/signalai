#!/usr/bin/env python3
"""0-15 / 0-100 quality scorecard for the WEEKLY briefing archive JSON.

The drilldown scorecard (``automation.quality.scorecard``) grades 5000-word HTML
notes. This is its sibling for the weekly briefing JSON archive: it scores each
``archive/briefings/weekly_briefing_*.json`` against the 15-criterion production
standard shipped through commit ab6cea9 (the TL;DR macro-regime headline +
measured index returns work).

The 15 criteria (1 point each; also reported as /100):

  Headline + TLDR
    1.  macro_regime populated (<=90 chars)
    2.  tl_dr has >= 5 substantive bullets

  Indices
    3.  index_returns has MEASURED 1W/1M/3M/YTD for S&P (^GSPC) and Nasdaq (^IXIC)
    4.  Dow / Russell / VIX may be null (always passes — null renders em-dash)

  Sections
    5.  market_summary references macro + rotation, >= 3 paragraphs
    6.  sector_summary >= 5 entries (dict or list)
    7.  watchlist_updates >= 50 entries
    8.  upcoming_catalysts >= 3 entries
    9.  picks include industry context + comp + earnings reaction + 2-3 catalysts
    10. narrative field present (long-form prose, >= 1500 chars)

  Hygiene
    11. NO "For Institutional Use Only" string
    12. Non-advice disclaimer present (UI-rendered — scored ui-only, always passes)
    13. No literal "MISSING" / "N/A" strings
    14. Sources present if narrative uses [N] citations
    15. No fabricated numbers (heuristic: measured index source_note present when
        index pcts are populated)

Each criterion records: pass/fail, observed value, and a gap classification:
  * "deterministic"  — fixable with no LLM spend (indices, macro, tl_dr,
                       volume sections, hygiene normalization)
  * "needs-llm"      — requires a new sonar-deep-research call (narrative,
                       rich market_summary, rich pick context)
  * "ui-only"        — rendered by the dashboard UI, not stored in JSON

No emoji anywhere (user rule). Pure standard library.

Usage:
    python -m automation.quality.weekly_briefing_scorecard
    python -m automation.quality.weekly_briefing_scorecard --json   # report only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIEFING_GLOB = str(ROOT / "archive" / "briefings" / "weekly_briefing_*.json")
REPORT_JSON = ROOT / "archive" / "briefings" / "_audit_report.json"
REPORT_MD = ROOT / "archive" / "briefings" / "_audit_report.md"

DETERMINISTIC = "deterministic"
NEEDS_LLM = "needs-llm"
UI_ONLY = "ui-only"

# Minimum thresholds (mirror the stated standard).
TLDR_MIN = 5
SECTOR_MIN = 5
WATCHLIST_MIN = 50
CATALYST_MIN = 3
NARRATIVE_MIN_CHARS = 1500
MARKET_SUMMARY_MIN_PARAS = 3
MACRO_MAX_CHARS = 90

# Ordered criterion name -> short legend label. Single source of truth: the
# scorers below register under these exact names, and the report table/legend
# derive their columns from this so a rename can't silently desync the two.
CRITERIA = (
    ("01_macro_regime", "macro_regime"),
    ("02_tl_dr", "tl_dr>=5"),
    ("03_measured_indices", "measured S&P+Nasdaq"),
    ("04_secondary_indices_nullable", "secondary idx nullable"),
    ("05_market_summary", "market_summary 3para+macro+rotation"),
    ("06_sector_summary", "sector_summary>=5"),
    ("07_watchlist_updates", "watchlist>=50"),
    ("08_upcoming_catalysts", "catalysts>=3"),
    ("09_pick_context", "pick context"),
    ("10_narrative", "narrative>=1500c"),
    ("11_no_institutional", "no institutional"),
    ("12_disclaimer_ui", "disclaimer (ui)"),
    ("13_no_literal_sentinels", "no MISSING/NA"),
    ("14_sources_if_cited", "sources if cited"),
    ("15_no_fabrication", "no fabrication"),
)

_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "missing"}
_INSTITUTIONAL_RE = re.compile(r"for\s+institutional\s+use\s+only", re.IGNORECASE)
_LITERAL_SENTINEL_RE = re.compile(r'"\s*(MISSING|N/?A)\s*"')
_ROTATION_RE = re.compile(r"rotat|breadth|leadership|defensiv|risk-o[nf]+|cyclical|"
                          r"sector", re.IGNORECASE)
_MACRO_RE = re.compile(r"fed|rate|inflation|cpi|macro|yield|tariff|gdp|jobs|"
                       r"payroll|treasur|recession|growth", re.IGNORECASE)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _clean(raw) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    return "" if s.lower() in _MISSING_TOKENS else s


def _measured_index_ok(ir: dict, key: str) -> bool:
    """True when index `key` carries all four measured pcts as numbers."""
    blk = ir.get(key)
    if not isinstance(blk, dict):
        return False
    return all(
        _is_num(blk.get(f))
        for f in ("weekly_pct", "one_month_pct", "three_month_pct", "ytd_pct")
    )


def _market_summary_text(d: dict) -> str:
    ms = d.get("market_summary")
    if isinstance(ms, str):
        return ms
    if isinstance(ms, dict):
        return ms.get("narrative") or json.dumps(ms)
    return ""


def _paragraph_count(text: str) -> int:
    if not text:
        return 0
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if len(p.strip()) > 80]
    if paras:
        return len(paras)
    # Single block with no blank lines: approximate by sentence-dense length.
    return 1 if len(text) > 240 else 0


def _pick_context_ok(picks: list) -> tuple[bool, str]:
    """A pick is 'rich' when it carries narrative context beyond raw price.

    Looks for any of: an industry/comp/positioning string, an earnings-reaction
    note, and a catalysts list. We require >= half the picks to be rich.
    """
    if not picks:
        return False, "no picks"
    rich = 0
    for p in picks:
        if not isinstance(p, dict):
            continue
        ctx_keys = ("why_undervalued", "thesis", "industry_context", "positioning",
                    "comp_positioning", "competitive", "catalyst", "catalysts",
                    "risk_reward", "earnings_reaction", "recent_earnings", "context",
                    "narrative")
        has_ctx = any(_clean(p.get(k)) for k in ctx_keys if not isinstance(p.get(k), list))
        has_list = any(isinstance(p.get(k), list) and p.get(k) for k in ("catalysts",))
        if has_ctx or has_list:
            rich += 1
    ok = rich >= max(1, len(picks) // 2)
    return ok, f"{rich}/{len(picks)} picks carry context"


def score_briefing(d: dict) -> dict:
    """Score one weekly briefing dict. Returns {week, score, score100, criteria}."""
    crit: dict[str, dict] = {}
    blob = json.dumps(d, ensure_ascii=False)

    def rec(n, ok, observed, gap):
        crit[n] = {"pass": bool(ok), "observed": observed, "gap": gap if not ok else None}

    # 1. macro_regime populated, <= 90 chars.
    mr = _clean(d.get("macro_regime"))
    rec("01_macro_regime", bool(mr) and len(mr) <= MACRO_MAX_CHARS,
        f"macro_regime={mr[:60]!r} len={len(mr)}", DETERMINISTIC)

    # 2. tl_dr >= 5 substantive bullets.
    tldr = [b for b in (d.get("tl_dr") or []) if isinstance(b, str) and len(b.strip()) > 8]
    rec("02_tl_dr", len(tldr) >= TLDR_MIN, f"tl_dr={len(tldr)} substantive bullets",
        DETERMINISTIC)

    # 3. Measured S&P + Nasdaq 1W/1M/3M/YTD.
    ir = d.get("index_returns") if isinstance(d.get("index_returns"), dict) else {}
    sp_ok = _measured_index_ok(ir, "sp500")
    nq_ok = _measured_index_ok(ir, "nasdaq")
    rec("03_measured_indices", sp_ok and nq_ok,
        f"sp500_measured={sp_ok} nasdaq_measured={nq_ok}", DETERMINISTIC)

    # 4. Dow/Russell/VIX may be null — always passes.
    rec("04_secondary_indices_nullable", True, "secondary indices nullable (em-dash)", None)

    # 5. market_summary references macro + rotation, >= 3 paragraphs.
    ms_text = _market_summary_text(d)
    ms_paras = _paragraph_count(ms_text)
    ms_macro = bool(_MACRO_RE.search(ms_text))
    ms_rot = bool(_ROTATION_RE.search(ms_text))
    # market_summary can be satisfied by the long narrative when prose lives there.
    narr = d.get("narrative") or ""
    eff_text = ms_text if len(ms_text) >= len(narr) else (ms_text + "\n\n" + narr)
    eff_macro = bool(_MACRO_RE.search(eff_text))
    eff_rot = bool(_ROTATION_RE.search(eff_text))
    eff_paras = max(ms_paras, _paragraph_count(narr))
    ms_ok = eff_paras >= MARKET_SUMMARY_MIN_PARAS and eff_macro and eff_rot
    rec("05_market_summary", ms_ok,
        f"paras={eff_paras} macro={eff_macro} rotation={eff_rot} ms_len={len(ms_text)}",
        NEEDS_LLM)

    # 6. sector_summary >= 5 entries.
    ss = d.get("sector_summary")
    ss_n = len(ss) if isinstance(ss, (dict, list)) else 0
    rec("06_sector_summary", ss_n >= SECTOR_MIN, f"sector_summary={ss_n} entries",
        DETERMINISTIC)

    # 7. watchlist_updates >= 50.
    wl = d.get("watchlist_updates") or []
    wl_n = len(wl) if isinstance(wl, list) else 0
    rec("07_watchlist_updates", wl_n >= WATCHLIST_MIN, f"watchlist_updates={wl_n}",
        DETERMINISTIC)

    # 8. upcoming_catalysts >= 3.
    uc = d.get("upcoming_catalysts") or []
    uc_n = len(uc) if isinstance(uc, list) else 0
    rec("08_upcoming_catalysts", uc_n >= CATALYST_MIN, f"upcoming_catalysts={uc_n}",
        DETERMINISTIC)

    # 9. picks carry context.
    picks = (d.get("value_picks") or []) + (d.get("momentum_picks") or [])
    picks_ok, picks_obs = _pick_context_ok(picks)
    rec("09_pick_context", picks_ok, picks_obs, NEEDS_LLM)

    # 10. narrative present, long-form.
    narr_ok = isinstance(narr, str) and len(narr) >= NARRATIVE_MIN_CHARS
    rec("10_narrative", narr_ok, f"narrative={len(narr) if isinstance(narr,str) else 0} chars",
        NEEDS_LLM)

    # 11. No "For Institutional Use Only".
    rec("11_no_institutional", not _INSTITUTIONAL_RE.search(blob),
        "institutional phrase absent" if not _INSTITUTIONAL_RE.search(blob)
        else "FOUND institutional phrase", DETERMINISTIC)

    # 12. Disclaimer — UI-rendered footer, not stored in JSON. Scored ui-only.
    rec("12_disclaimer_ui", True, "disclaimer rendered by dashboard UI footer (not in JSON)",
        UI_ONLY)

    # 13. No literal "MISSING"/"N/A" strings.
    sentinel_hits = _LITERAL_SENTINEL_RE.findall(blob)
    rec("13_no_literal_sentinels", not sentinel_hits,
        "no literal MISSING/N/A" if not sentinel_hits else f"{len(sentinel_hits)} literal sentinels",
        DETERMINISTIC)

    # 14. Sources present if narrative cites [N]. A real sources list is either a
    # populated `sources`/`sources_list` field OR a "References"/"Sources" heading
    # in the narrative that actually maps the [N] markers. Incidental prose uses of
    # the word "source" (e.g. "open-source") do NOT count. When the narrative
    # carries bare orphan [N] markers with no resolving list, the criterion fails;
    # the deterministic remedy is to strip the orphan markers (no spend).
    cites = re.findall(r"\[\d+\]", narr) if isinstance(narr, str) else []
    has_sources_field = bool(_clean(d.get("sources"))) or bool(d.get("sources_list"))
    has_refs_heading = bool(re.search(
        r"(?:^|\n)\s*#{0,3}\s*\**\s*(?:sources|references)\b", narr, re.IGNORECASE)
    ) if isinstance(narr, str) else False
    src_ok = (not cites) or has_sources_field or has_refs_heading
    rec("14_sources_if_cited", src_ok,
        f"narrative_citations={len(cites)} sources_field={has_sources_field} "
        f"refs_heading={has_refs_heading}", DETERMINISTIC)

    # 15. No fabricated index numbers (measured pcts must carry a source_note).
    fab_ok = True
    fab_obs = "index pcts traceable to measured source"
    for key in ("sp500", "nasdaq"):
        blk = ir.get(key) if isinstance(ir.get(key), dict) else {}
        any_pct = any(_is_num(blk.get(f)) for f in
                      ("weekly_pct", "one_month_pct", "three_month_pct", "ytd_pct"))
        if any_pct and not _clean(blk.get("source_note")):
            fab_ok = False
            fab_obs = f"{key} has pcts but no source_note (untraceable)"
            break
    rec("15_no_fabrication", fab_ok, fab_obs, DETERMINISTIC)

    raw = sum(1 for c in crit.values() if c["pass"])
    total = len(crit)
    return {
        "week_ending": d.get("week_ending"),
        "score": raw,
        "max": total,
        "score100": round(100 * raw / total),
        "criteria": crit,
    }


def _failing_gaps(card: dict) -> dict:
    """Group failing criteria names by gap type."""
    out = {DETERMINISTIC: [], NEEDS_LLM: [], UI_ONLY: []}
    for name, c in card["criteria"].items():
        if not c["pass"] and c["gap"] in out:
            out[c["gap"]].append(name)
    return out


def build_report() -> dict:
    files = sorted(glob.glob(BRIEFING_GLOB))
    weeks = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        card = score_briefing(d)
        card["file"] = os.path.basename(f)
        card["gaps"] = _failing_gaps(card)
        weeks.append(card)

    weeks.sort(key=lambda w: w.get("week_ending") or "")
    n = len(weeks)
    perfect = sum(1 for w in weeks if w["score"] == w["max"])
    # Per-criterion fail tallies.
    fail_tally: dict[str, int] = {}
    for w in weeks:
        for name, c in w["criteria"].items():
            if not c["pass"]:
                fail_tally[name] = fail_tally.get(name, 0) + 1
    # Weeks needing LLM regen = any needs-llm gap.
    needs_llm = [w["week_ending"] for w in weeks if w["gaps"][NEEDS_LLM]]
    return {
        "summary": {
            "total_weeks": n,
            "perfect_15_15": perfect,
            "mean_score100": round(sum(w["score100"] for w in weeks) / n) if n else 0,
            "weeks_needing_llm": needs_llm,
            "fail_tally_by_criterion": dict(sorted(fail_tally.items())),
        },
        "weeks": weeks,
    }


def _md_table(report: dict) -> str:
    crit_names = [name for name, _ in CRITERIA]
    lines = []
    s = report["summary"]
    lines.append("# Weekly Briefing Audit Report")
    lines.append("")
    lines.append(f"- Total weeks audited: **{s['total_weeks']}**")
    lines.append(f"- Weeks at 15/15: **{s['perfect_15_15']}**")
    lines.append(f"- Mean score: **{s['mean_score100']}/100**")
    lines.append(f"- Weeks needing LLM regen (any needs-llm gap): **{len(s['weeks_needing_llm'])}**")
    lines.append("")
    lines.append("## Criteria legend")
    for name, label in CRITERIA:
        lines.append(f"- **{name[:2]}** — {label}")
    lines.append("")
    lines.append("## Per-week scorecard")
    lines.append("")
    header = "| Week | Score | " + " | ".join(c[:2] for c in crit_names) + " |"
    sep = "|" + "---|" * (len(crit_names) + 2)
    lines.append(header)
    lines.append(sep)
    for w in report["weeks"]:
        cells = []
        for c in crit_names:
            cells.append("P" if w["criteria"][c]["pass"] else "F")
        lines.append(f"| {w['week_ending']} | {w['score']}/{w['max']} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Per-week gaps")
    lines.append("")
    for w in report["weeks"]:
        det = w["gaps"][DETERMINISTIC]
        llm = w["gaps"][NEEDS_LLM]
        if not det and not llm:
            lines.append(f"- **{w['week_ending']}** — clean (15/15 or ui-only gaps)")
            continue
        parts = []
        if det:
            parts.append("deterministic: " + ", ".join(det))
        if llm:
            parts.append("needs-llm: " + ", ".join(llm))
        lines.append(f"- **{w['week_ending']}** ({w['score']}/{w['max']}) — " + "; ".join(parts))
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score the weekly briefing archive.")
    parser.add_argument("--json", action="store_true", help="print JSON summary only")
    args = parser.parse_args(argv)

    report = build_report()
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    REPORT_MD.write_text(_md_table(report), encoding="utf-8")

    s = report["summary"]
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    print(f"Audited {s['total_weeks']} weeks. {s['perfect_15_15']} at 15/15. "
          f"Mean {s['mean_score100']}/100.")
    print(f"Weeks needing LLM regen: {len(s['weeks_needing_llm'])}")
    print("Fail tally by criterion:")
    for k, v in s["fail_tally_by_criterion"].items():
        print(f"  {k}: {v} weeks fail")
    print(f"\nReports written:\n  {REPORT_JSON}\n  {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
