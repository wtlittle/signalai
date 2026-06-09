"""Structural-quality diff for institutional drilldown HTML notes.

A drilldown is a single-file HTML report (markdown front-matter + embedded HTML)
with a fixed 14-section skeleton, dense claim:N citation links, color-coded
delta spans, and right-aligned numeric table cells. Regressions in the prompt
or generator tend to show up as *structural* drift — a section silently dropped,
claim links collapsing, tables half-rendered — long before anyone notices the
prose. This module counts those structural features and diffs a candidate
against the committed golden snapshot.

Not wired into CI: parsing + counting a full note is comparatively expensive and
the golden is a single ticker, so it is run on demand — by a human or by the
validator — rather than on every push. See the README note at the bottom of
this file's directory (automation/tests) for invocation.

CLI:
    python -m automation.tests.test_drilldown_quality <candidate.md>

Exits 0 if every metric is within tolerance of the golden, 1 otherwise, and
prints a per-metric diff table either way.
"""
from __future__ import annotations

import os
import re
import sys

GOLDEN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "golden",
    "FROG_drilldown_golden.html",
)

# Metrics held to a percentage tolerance (content can legitimately vary a bit
# ticker to ticker). Everything else is an exact-match structural invariant.
TOLERANCE_METRICS = {"word_count", "table_count", "claim_link_count"}
EXACT_METRICS = {"section_count", "span_pos", "span_neg", "span_num"}


def _strip_style_and_tags(html: str) -> str:
    """Drop <style> blocks then all tags, leaving visible text for word count."""
    no_style = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", no_style)


def count_metrics(html: str) -> dict[str, int]:
    """Return the structural counters for a drilldown's raw text."""
    return {
        "section_count": len(re.findall(r"<section\b", html, flags=re.I)),
        "claim_link_count": len(re.findall(r'href="claim:', html)),
        "table_count": len(re.findall(r"<table\b", html, flags=re.I)),
        "word_count": len(_strip_style_and_tags(html).split()),
        "span_pos": len(re.findall(r'class="[^"]*\bpos\b', html)),
        "span_neg": len(re.findall(r'class="[^"]*\bneg\b', html)),
        "span_num": len(re.findall(r'class="[^"]*\bnum\b', html)),
    }


def count_metrics_for_path(path: str) -> dict[str, int]:
    with open(path, encoding="utf-8") as fh:
        return count_metrics(fh.read())


def structural_diff(
    candidate_path: str,
    golden_path: str = GOLDEN_PATH,
    tolerance: float = 0.05,
) -> dict[str, dict]:
    """Diff a candidate note against the golden, per metric.

    Returns {metric: {golden, candidate, delta_pct, tolerance, pass}}. Metrics in
    TOLERANCE_METRICS pass within ±`tolerance` (fractional, so 0.05 = 5%); metrics
    in EXACT_METRICS must match exactly. delta_pct is candidate-vs-golden as a
    fraction; None when the golden value is 0.
    """
    golden = count_metrics_for_path(golden_path)
    candidate = count_metrics_for_path(candidate_path)

    result: dict[str, dict] = {}
    for metric, gval in golden.items():
        cval = candidate[metric]
        delta_pct = None if gval == 0 else (cval - gval) / gval
        if metric in EXACT_METRICS:
            passed = cval == gval
        else:
            passed = delta_pct is not None and abs(delta_pct) <= tolerance
        result[metric] = {
            "golden": gval,
            "candidate": cval,
            "delta_pct": delta_pct,
            "tolerance": tolerance if metric in TOLERANCE_METRICS else 0.0,
            "pass": passed,
        }
    return result


# ── Tests on the golden itself (cheap, lock the structural baseline) ──

EXPECTED = {
    "section_count": 14,
    "claim_link_count": 111,
    "table_count": 10,
    "word_count": 5348,
    "span_pos": 11,
    "span_neg": 10,
    "span_num": 224,
}


def test_golden_section_count():
    assert count_metrics_for_path(GOLDEN_PATH)["section_count"] == 14


def test_golden_structural_counters_match_baseline():
    metrics = count_metrics_for_path(GOLDEN_PATH)
    for metric, expected in EXPECTED.items():
        assert metrics[metric] == expected, (
            f"{metric}: golden now {metrics[metric]}, baseline {expected}. "
            "If this change is intentional, update EXPECTED and the golden snapshot."
        )


def test_golden_diffs_clean_against_itself():
    diff = structural_diff(GOLDEN_PATH)
    failed = {m: d for m, d in diff.items() if not d["pass"]}
    assert not failed, f"Golden should diff clean against itself: {failed}"


# ── CLI ──

def _format_diff(diff: dict[str, dict]) -> str:
    lines = [f"{'metric':<20}{'golden':>10}{'candidate':>12}{'delta':>10}  result"]
    for metric, d in diff.items():
        if d["delta_pct"] is None:
            delta = "n/a"
        else:
            delta = f"{d['delta_pct'] * 100:+.1f}%"
        flag = "PASS" if d["pass"] else "FAIL"
        lines.append(
            f"{metric:<20}{d['golden']:>10}{d['candidate']:>12}{delta:>10}  {flag}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(
            "usage: python -m automation.tests.test_drilldown_quality <candidate.md>",
            file=sys.stderr,
        )
        return 2
    candidate_path = argv[0]
    if not os.path.exists(candidate_path):
        print(f"candidate not found: {candidate_path}", file=sys.stderr)
        return 2

    diff = structural_diff(candidate_path)
    print(_format_diff(diff))
    failed = [m for m, d in diff.items() if not d["pass"]]
    if failed:
        print(f"\nOUT OF TOLERANCE: {', '.join(failed)}")
        return 1
    print("\nwithin tolerance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
