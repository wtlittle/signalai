#!/usr/bin/env python3
"""
Backfill the V2 consensus envelopes (results_vs_consensus / guide_vs_consensus)
onto post_earnings tickers in earnings_intel.json by parsing the actual reported
figures out of the post-earnings markdown notes in notes/post_earnings/.

WHY THIS EXISTS
  The POST earnings cards render REV / EPS beat-miss, FY Guide delta and the
  post-print % from these envelope fields (see earnings.js). For tickers seeded
  before the V2 envelopes existed, the keys were never written, so every card
  showed "—". The post-earnings notes already contain the numbers; this script
  lifts them into the structured fields the dashboard reads.

WHAT IT POPULATES (per schema earnings_intel_schema.md)
  results_vs_consensus.in_quarter_rev_actual
  results_vs_consensus.in_quarter_rev_surprise_pct   (signed %, + = beat)
  results_vs_consensus.in_quarter_eps_actual
  results_vs_consensus.in_quarter_eps_surprise_pct   (signed %, + = beat)
  guide_vs_consensus.fy_rev_change_vs_consensus_pct  (signed %, + = raise)
  post_earnings_review.stock_reaction_pct            (signed %, only if absent)

NON-FABRICATION
  A field is written ONLY when it can be computed/parsed unambiguously from the
  note. Pre-print notes (results "not yet released") yield nothing. Existing
  non-null values are never overwritten. Run with --dry-run to preview.

Usage:
  python3 scripts/backfill_results_vs_consensus.py [--dry-run] [--ticker XYZ]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "earnings_intel.json"
INDEX_PATH = ROOT / "earnings_notes_index.json"
POST_DIR = ROOT / "notes" / "post_earnings"


def _to_number(raw: str) -> float | None:
    """Parse a money/number token like '$2.542B', '1,320', '(0.24)', '0.39'."""
    if raw is None:
        return None
    s = raw.strip()
    s = s.replace("$", "").replace(",", "").replace("~", "").strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    if s.startswith("-"):
        neg = True
        s = s[1:]
    mult = 1.0
    if s and s[-1] in "BbMmKkTt":
        mult = {"t": 1e12, "b": 1e9, "m": 1e6, "k": 1e3}[s[-1].lower()]
        s = s[:-1]
    s = s.strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        return None
    val = float(s) * mult
    return -val if neg else val


_NA = re.compile(r"\b(n/?a|not yet|pending|unreported|not (?:disclosed|provided|reported|released)|tbd)\b", re.I)


def _signed_surprise(actual: float | None, est: float | None) -> float | None:
    """Signed surprise % = (actual-est)/|est|*100. Skips when est is ~0 or missing."""
    if actual is None or est is None or abs(est) < 1e-9:
        return None
    return round((actual - est) / abs(est) * 100.0, 1)


_MONEY = r"\$?[\d.,()~]+\s*[BMKT]?"


def _est_number(raw: str) -> float | None:
    """Parse an estimate token, taking the midpoint of a range like '$6.67-$6.89'."""
    if raw is None:
        return None
    parts = re.split(r"\s*(?:-|–|to)\s*", raw.strip())
    nums = [n for n in (_to_number(p) for p in parts) if n is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


# A metric line looks like (bulleted variants):
#   "- Revenue: $2.542B vs. $2.52B est (+13.5% YoY)."
#   "- Non-GAAP EPS: $1.08 vs $1.01 est."
#   "- EPS: $7.31 actual vs $6.67-$6.89 est (beat ~9-6%)"
#   "- Core EPS: $0.70 vs $0.70 est"
# Or a Scenario/results table row:
#   "| EPS | $5.94 | ~$5.40 | ~10% beat |"
#   "| Revenue | NT$1.134T ($35.9B) | NT$1.127T ($35B) | Beat |"
def _parse_metric(md: str, label_re: str) -> tuple[str | None, float | None, float | None]:
    """Return (actual_str, actual_num, surprise_pct) for the first matching line.

    Only matches lines that carry an explicit actual-vs-estimate pair with real
    numbers; returns (None, None, None) when the note is pre-print / N/A.
    """
    bullet_re = re.compile(
        rf"^[-*\s]*{label_re}\s*"
        rf"(?:\((?:non-GAAP|adjusted|core)\)\s*)?"
        rf":?\s*"
        rf"({_MONEY})\s*"
        rf"(?:actual\s*)?(?:non-GAAP\s*)?vs\.?\s*"
        rf"(?:est\.?\s*)?({_MONEY}(?:\s*(?:-|–|to)\s*{_MONEY})?)\s*est",
        re.I | re.M,
    )
    for m in bullet_re.finditer(md):
        actual_raw, est_raw = m.group(1), m.group(2)
        if _NA.search(actual_raw) or _NA.search(est_raw):
            continue
        actual = _to_number(actual_raw)
        est = _est_number(est_raw)
        if actual is None:
            continue
        return actual_raw.strip(), actual, _signed_surprise(actual, est)

    # Table-row fallback:  | <label> | <actual> | <est> | ... |
    row_re = re.compile(
        rf"^\|\s*(?:non-GAAP\s+|adjusted\s+|core\s+|total\s+)?{label_re}\s*\|"
        rf"\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
        re.I | re.M,
    )
    for m in row_re.finditer(md):
        actual_raw, est_raw = m.group(1), m.group(2)
        if _NA.search(actual_raw) or _NA.search(est_raw):
            continue
        # Prefer a USD figure in parentheses for FX-quoted lines, e.g. "NT$1.134T ($35.9B)".
        usd = re.search(r"\(\s*(\$[\d.,]+\s*[BMKT]?)\s*\)", actual_raw)
        usd_est = re.search(r"\(\s*(\$[\d.,]+\s*[BMKT]?)\s*\)", est_raw)
        a_tok = usd.group(1) if usd else actual_raw
        e_tok = usd_est.group(1) if usd_est else est_raw
        actual = _to_number(a_tok.strip())
        est = _est_number(e_tok.strip())
        if actual is None:
            continue
        return a_tok.strip(), actual, _signed_surprise(actual, est)

    # Actual-only fallback (RC-2): real notes phrase the comparator as
    # "guidance mid-point", "consensus", "Street" rather than "<est> est", or
    # state only the reported actual. Capture the actual value so the card can
    # surface "Revenue: $X". A surprise % is computed ONLY when an explicit
    # estimate token ("vs <num> [est|consensus|Street|guidance mid-point]") is
    # parseable — otherwise surprise stays None (never fabricated).
    actual_only_re = re.compile(
        rf"^[-*\s]*{label_re}\s*"
        rf"(?:\((?:non-GAAP|adjusted|core)\)\s*)?"
        rf":?\s*"
        rf"({_MONEY})\b",
        re.I | re.M,
    )
    comparator_re = re.compile(
        rf"vs\.?\s*(?:est\.?\s*|consensus\s*|Street\s*|guidance\s*)?"
        rf"({_MONEY}(?:\s*(?:-|–|to)\s*{_MONEY})?)\s*"
        rf"(?:est\.?|consensus|Street|guidance\s*mid-?point|mid-?point)",
        re.I,
    )
    for m in actual_only_re.finditer(md):
        actual_raw = m.group(1)
        if _NA.search(actual_raw):
            continue
        actual = _to_number(actual_raw)
        if actual is None:
            continue
        # Look for an estimate comparator in the remainder of the same line.
        line_tail = md[m.end():].split("\n", 1)[0]
        surprise = None
        cm = comparator_re.search(line_tail)
        if cm and not _NA.search(cm.group(1)):
            est = _est_number(cm.group(1))
            surprise = _signed_surprise(actual, est)
        return actual_raw.strip(), actual, surprise

    return None, None, None


def _fmt_actual(raw: str | None) -> str | None:
    """Normalize the actual token for display (keep B/M suffix, ensure leading $)."""
    if not raw:
        return None
    s = raw.strip()
    if not s.startswith("$") and re.match(r"^[\d.]", s):
        s = "$" + s
    return s


# FY revenue guide vs consensus, when the note states it directly, e.g.:
#   "FY27 ... revenue guide ... vs ... Street ... +3%"  -- too varied to trust.
# We only lift an explicit signed percentage that the author tied to FY revenue
# guidance relative to consensus/Street.
def _parse_fy_rev_guide_vs_consensus(md: str) -> float | None:
    patterns = [
        r"FY\s*rev(?:enue)?\s*guide[^.\n]*?vs\.?\s*(?:prior\s*)?(?:Street|consensus)[^.\n]*?([+-]\d+(?:\.\d+)?)\s*%",
        r"FY[\w\s]*?revenue guidance[^.\n]*?(?:vs\.?|versus)\s*(?:prior\s*)?(?:Street|consensus)[^.\n]*?([+-]\d+(?:\.\d+)?)\s*%",
        r"rais(?:e|ed|ing)\s+FY[\w\s]*?revenue[^.\n]*?([+-]\d+(?:\.\d+)?)\s*%\s*(?:above|vs\.?)\s*(?:Street|consensus)",
    ]
    for pat in patterns:
        m = re.search(pat, md, re.I)
        if m:
            try:
                return round(float(m.group(1)), 1)
            except ValueError:
                continue
    return None


def _iter_post_notes(only: str | None):
    """Yield (ticker, note_path) for active post-earnings notes from the index,
    falling back to all files in notes/post_earnings/ when the index lacks them."""
    seen: set[str] = set()
    if INDEX_PATH.exists():
        idx = json.loads(INDEX_PATH.read_text())
        for e in idx.get("active_post_earnings", []):
            tk = e.get("ticker")
            nf = e.get("note_file") or e.get("file")
            if not tk or not nf:
                continue
            if only and tk != only:
                continue
            p = ROOT / nf
            if p.exists():
                seen.add(tk)
                yield tk, p
    for fn in sorted(os.listdir(POST_DIR)) if POST_DIR.exists() else []:
        m = re.match(r"^([A-Z.]+)_\d{4}-\d{2}-\d{2}\.md$", fn)
        if not m:
            continue
        tk = m.group(1)
        if only and tk != only:
            continue
        if tk in seen:
            continue
        seen.add(tk)
        yield tk, POST_DIR / fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Limit to a single ticker")
    args = ap.parse_args()

    intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.get("tickers", {})

    populated, skipped_preprint, skipped_no_record = [], [], []

    for tk, note_path in _iter_post_notes(args.ticker):
        rec = tickers.get(tk)
        if not rec or rec.get("state") != "post_earnings":
            skipped_no_record.append(tk)
            continue
        md = note_path.read_text()

        _QUAL = r"(?:Total\s+|Non-GAAP\s+|GAAP\s+|Adjusted\s+|Adj\.?\s+|Core\s+|Q\d\s+)*"
        rev_raw, _rev, rev_surp = _parse_metric(md, _QUAL + r"Rev(?:enue)?")
        eps_raw, _eps, eps_surp = _parse_metric(md, _QUAL + r"EPS")
        fy_rev_guide = _parse_fy_rev_guide_vs_consensus(md)

        # Nothing extractable -> pre-print / N/A note. Leave record untouched.
        if rev_raw is None and eps_raw is None:
            skipped_preprint.append(tk)
            continue

        rvc = rec.get("results_vs_consensus") or {}
        gvc = rec.get("guide_vs_consensus") or {}
        changed = False

        def _set(d, k, v):
            nonlocal changed
            if v is not None and d.get(k) is None:
                d[k] = v
                changed = True

        _set(rvc, "in_quarter_rev_actual", _fmt_actual(rev_raw))
        if rvc.get("in_quarter_rev_actual") is not None:
            _set(rvc, "in_quarter_rev_actual_source", "note")
        _set(rvc, "in_quarter_rev_surprise_pct", rev_surp)
        _set(rvc, "in_quarter_eps_actual", _fmt_actual(eps_raw))
        if rvc.get("in_quarter_eps_actual") is not None:
            _set(rvc, "in_quarter_eps_actual_source", "note")
        _set(rvc, "in_quarter_eps_surprise_pct", eps_surp)
        _set(gvc, "fy_rev_change_vs_consensus_pct", fy_rev_guide)

        if changed:
            if rvc:
                rec["results_vs_consensus"] = rvc
            if gvc:
                rec["guide_vs_consensus"] = gvc
            populated.append(tk)

    if not args.dry_run and populated:
        INTEL_PATH.write_text(json.dumps(intel, indent=2, ensure_ascii=False))

    tag = "[DRY RUN] " if args.dry_run else "[OK] "
    print(f"{tag}populated={len(populated)} preprint_skipped={len(skipped_preprint)} "
          f"no_record={len(skipped_no_record)}")
    if populated:
        print("  populated:", ", ".join(sorted(populated)))
    if skipped_preprint:
        print("  pre-print (no actuals in note):", ", ".join(sorted(skipped_preprint)))


if __name__ == "__main__":
    main()
