#!/usr/bin/env python3
"""0-100 quality scorecard for institutional drilldown notes.

The binary validator (``automation.jobs.drilldown_validator``) answers a single
question: is this note structurally publishable? It does not distinguish a
gold-standard note from one that barely clears the bar. The scorecard does -- it
returns a graded 0-100 score across six weighted categories so the v2 generator
can quarantine notes that pass the validator but are thin, and so a human can see
at a glance how a note compares to the FROG gold standard.

Categories and weights (sum 100):
  * Structural fidelity   35  -- the FROG class contract: three-column
                                 Bull/Base/Bear cards (col-bull/col-base/col-bear),
                                 per-card price targets (col-target), and the
                                 fourteen section-title headers.
  * Citation density      15  -- >= 30 claim:N references (the gold has ~100).
  * Word count            10  -- 4000-7000 words, and no body section under 200
                                 words (a truncation / stub tell).
  * Voice contract        15  -- a directional thesis opener (digit + horizon +
                                 direction) and zero banned consultantese phrases.
  * Chart presence        10  -- an annotated earnings chart OR an explicit,
                                 reasoned omission sentinel.
  * Section completeness  15  -- all fourteen canonical sections present and none
                                 rendered as a quarantined "unavailable" block.

Calibration targets (see __main__ self-test):
  * golden/FROG_drilldown_golden.html  -> 95+
  * a healthy POET note                -> 80+
  * a degraded note (no three-col BBB) -> < 80   (quarantine)

``score_drilldown`` accepts either an ``html`` string or a ``path``. Returns:
  {score: int, breakdown: {category: {score, max, ...}}, issues: [str], ...}

No emoji anywhere (user rule). Pure standard library + the validator's helpers.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from automation.jobs.drilldown_validator import (
    REQUIRED_SECTIONS,
    BANNED_PHRASES,
    HORIZON_RE,
    DIRECTION_RE,
    _word_count,
    _strip_frontmatter,
    _section_labels,
    _investment_overview_opener,
)

# A note scoring below this is withheld by the v2 generator even when the binary
# validator passes. Kept in sync with drilldown_generator_v2.SCORECARD_QUARANTINE_BELOW.
QUARANTINE_BELOW = 80

WEIGHTS = {
    "structural_fidelity": 35,
    "citation_density": 15,
    "word_count": 10,
    "voice_contract": 15,
    "chart_presence": 10,
    "section_completeness": 15,
}

# Citation / word-count calibration.
CLAIM_FULL = 30      # >= this many claim: refs earns full citation credit
CLAIM_FLOOR = 10     # below this earns zero
WORD_MIN, WORD_MAX = 4000, 7000
WORD_HARD_MIN, WORD_HARD_MAX = 3000, 8000
MIN_SECTION_WORDS = 200


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, re.IGNORECASE))


# --------------------------------------------------------------------------- #
# Category scorers -- each returns (earned, max, notes_list)
# --------------------------------------------------------------------------- #
def _score_structural(html: str) -> tuple[float, int, list[str]]:
    """FROG class contract: three-col Bull/Base/Bear cards + targets + headers."""
    mx = WEIGHTS["structural_fidelity"]
    notes: list[str] = []
    earned = 0.0

    n_bull = _count(r'col-bull', html)
    n_base = _count(r'col-base', html)
    n_bear = _count(r'col-bear', html)
    n_threecol = _count(r'three-col', html)
    n_target = _count(r'col-target', html)
    n_titles = len(re.findall(r'class="section-title"', html, re.IGNORECASE))

    # Three-column Bull/Base/Bear cards (15 pts): the single most important
    # structural tell. A degraded note renders BBB as flat prose with no cards.
    if n_bull >= 1 and n_base >= 1 and n_bear >= 1 and n_threecol >= 1:
        earned += 15
    else:
        notes.append(
            f"missing three-column Bull/Base/Bear cards "
            f"(col-bull={n_bull}, col-base={n_base}, col-bear={n_bear}, "
            f"three-col={n_threecol})"
        )

    # Per-card price-target boxes (8 pts).
    if n_target >= 3:
        earned += 8
    elif n_target >= 1:
        earned += 4
        notes.append(f"only {n_target} col-target box(es); expected 3")
    else:
        notes.append("no col-target price boxes")

    # Section-title headers (12 pts): proportional to how many of the 14 are
    # rendered with the correct class (no <h2>/<h3>).
    title_credit = min(n_titles, 12) / 12 * 12
    earned += title_credit
    if n_titles < 12:
        notes.append(f"only {n_titles} section-title headers (expected >= 12)")
    if re.search(r'<h[123][\s>]', html, re.IGNORECASE):
        earned = max(0.0, earned - 4)
        notes.append("raw <h1>/<h2>/<h3> headers present (should be section-title)")

    return min(earned, mx), mx, notes


def _score_citations(html: str) -> tuple[float, int, list[str]]:
    mx = WEIGHTS["citation_density"]
    n = _count(r'claim:', html)
    if n >= CLAIM_FULL:
        return mx, mx, []
    if n <= CLAIM_FLOOR:
        return 0.0, mx, [f"only {n} claim: refs (need >= {CLAIM_FULL})"]
    frac = (n - CLAIM_FLOOR) / (CLAIM_FULL - CLAIM_FLOOR)
    return round(mx * frac, 1), mx, [f"{n} claim: refs (full credit at {CLAIM_FULL})"]


def _section_word_counts(html: str) -> list[tuple[str, int]]:
    """Approximate per-section body word counts, keyed by section-title text."""
    out: list[tuple[str, int]] = []
    titles = list(re.finditer(
        r'class="section-title"[^>]*>(.*?)</', html, re.DOTALL | re.IGNORECASE))
    for i, tm in enumerate(titles):
        label = re.sub(r'<[^>]+>', ' ', tm.group(1))
        label = re.sub(r'\s+', ' ', label).strip()
        start = tm.end()
        end = titles[i + 1].start() if i + 1 < len(titles) else len(html)
        out.append((label, _word_count(html[start:end])))
    return out


def _score_word_count(html: str) -> tuple[float, int, list[str]]:
    mx = WEIGHTS["word_count"]
    notes: list[str] = []
    wc = _word_count(html)
    earned = 0.0

    # Length band (6 pts): full inside [4000,7000], partial in the hard band.
    if WORD_MIN <= wc <= WORD_MAX:
        earned += 6
    elif WORD_HARD_MIN <= wc <= WORD_HARD_MAX:
        earned += 3
        notes.append(f"word count {wc} outside ideal [{WORD_MIN}, {WORD_MAX}]")
    else:
        notes.append(f"word count {wc} outside hard band [{WORD_HARD_MIN}, {WORD_HARD_MAX}]")

    # No stub sections (4 pts): a body section under 200 words is a truncation /
    # unavailable-block tell. The footer Sources and the always-short Earnings
    # Setup blurb are exempted by name.
    exempt = ("sources", "earnings setup", "2-year price",
              "one-sentence", "one sentence", "debate framing")
    stubs = [
        (lbl, n) for lbl, n in _section_word_counts(html)
        if n < MIN_SECTION_WORDS and not any(e in lbl.lower() for e in exempt)
    ]
    if not stubs:
        earned += 4
    else:
        notes.append("stub sections under {}w: {}".format(
            MIN_SECTION_WORDS,
            ", ".join(f"{lbl}={n}" for lbl, n in stubs[:5]),
        ))
    return min(earned, mx), mx, notes


def _score_voice(html: str) -> tuple[float, int, list[str]]:
    mx = WEIGHTS["voice_contract"]
    notes: list[str] = []
    earned = 0.0

    # Directional thesis opener (9 pts): digit + horizon + direction keyword.
    raw_unit, first_sentence = _investment_overview_opener(html)
    if first_sentence:
        has_digit = any(c.isdigit() for c in first_sentence)
        has_horizon = bool(HORIZON_RE.search(first_sentence))
        has_dir = bool(DIRECTION_RE.search(first_sentence))
        hits = sum([has_digit, has_horizon, has_dir])
        earned += 9 * hits / 3
        if hits < 3:
            miss = [n for n, ok in
                    (("digit", has_digit), ("horizon", has_horizon), ("direction", has_dir))
                    if not ok]
            notes.append(f"thesis opener missing: {', '.join(miss)}")
    else:
        notes.append("could not locate the Investment Overview thesis opener")

    # Banned phrases (6 pts): zero earns full credit.
    plain = re.sub(r'<[^>]+>', ' ', html)
    banned_hits = [p for p in BANNED_PHRASES if p.lower() in plain.lower()]
    if not banned_hits:
        earned += 6
    else:
        notes.append(f"banned phrases present: {banned_hits}")
    return min(earned, mx), mx, notes


def _score_chart(html: str) -> tuple[float, int, list[str]]:
    mx = WEIGHTS["chart_presence"]
    if re.search(r'class=["\']earnings-chart["\']', html, re.IGNORECASE):
        return mx, mx, []
    if re.search(r'earnings-chart omitted', html, re.IGNORECASE):
        return mx, mx, ["chart explicitly omitted (no real event dates) -- accepted"]
    return 0.0, mx, ["no annotated earnings chart and no omission sentinel"]


def _score_completeness(html: str) -> tuple[float, int, list[str]]:
    mx = WEIGHTS["section_completeness"]
    notes: list[str] = []
    labels = " || ".join(_section_labels(html)).lower()
    present = 0
    for name, keywords in REQUIRED_SECTIONS:
        if any(kw.lower() in labels for kw in keywords):
            present += 1
        else:
            notes.append(f"missing section: {name}")
    total = len(REQUIRED_SECTIONS)
    earned = mx * present / total

    # Penalize quarantined "unavailable" blocks: each one is a real content gap
    # even if the section header is present.
    n_unavail = _count(r'class="unavailable"', html) + _count(r'Section temporarily unavailable', html)
    # The two markers co-occur; collapse to a single count.
    n_unavail = _count(r'Section temporarily unavailable', html)
    if n_unavail:
        earned = max(0.0, earned - 3 * n_unavail)
        notes.append(f"{n_unavail} quarantined 'unavailable' section block(s)")
    return min(earned, mx), mx, notes


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def score_drilldown(path: str | None = None, *, html: str | None = None,
                    ticker: str | None = None) -> dict:
    """Score a drilldown note 0-100. Pass ``path`` OR ``html``.

    Returns {score, breakdown, issues, word_count, claim_count, ticker}.
    """
    if html is None:
        if path is None:
            raise ValueError("score_drilldown requires either path or html")
        html = Path(path).read_text(encoding="utf-8")
    html = _strip_frontmatter(html)

    scorers = {
        "structural_fidelity": _score_structural,
        "citation_density": _score_citations,
        "word_count": _score_word_count,
        "voice_contract": _score_voice,
        "chart_presence": _score_chart,
        "section_completeness": _score_completeness,
    }

    breakdown: dict[str, dict] = {}
    issues: list[str] = []
    total = 0.0
    for name, fn in scorers.items():
        earned, mx, notes = fn(html)
        breakdown[name] = {"score": round(earned, 1), "max": mx, "notes": notes}
        total += earned
        issues.extend(notes)

    score = int(round(total))
    return {
        "score": score,
        "breakdown": breakdown,
        "issues": issues,
        "word_count": _word_count(html),
        "claim_count": _count(r'claim:', html),
        "ticker": (ticker or "").upper() or None,
        "quarantine": score < QUARANTINE_BELOW,
    }


# --------------------------------------------------------------------------- #
# Daily briefing scorecard (lighter)
# --------------------------------------------------------------------------- #
# The daily briefing is short plain text, not a 5000-word HTML note, so it gets
# a far simpler gate than the drilldown. It answers one question: did the
# briefing render with real content, or did it degrade into a wall of empty
# sections? The freshness gate (automation.jobs.render_email) already protects
# against stale data; this protects against a structurally hollow briefing that
# is fresh but empty.
BRIEFING_SECTIONS = (
    "MARKET SNAPSHOT",
    "TOP MOVERS",
    "EARNINGS TODAY",
    "YESTERDAY'S REACTIONS",
    "OPEN M&A RUMORS",
    "MACRO TILT",
)
# A section that rendered its empty fallback. Both daily fallbacks are covered.
_EMPTY_MARKERS = ("None today.", "None on watchlist.", "n/a")
BRIEFING_QUARANTINE_BELOW = 70
# More empty sections than this (out of the expected set) means the briefing is
# effectively hollow even if it is fresh.
BRIEFING_MAX_EMPTY = 3


def score_briefing(path: str | None = None, *, text: str | None = None,
                   send_date: str | None = None) -> dict:
    """Score a rendered daily briefing 0-100 (lighter than the drilldown gate).

    Pass ``path`` OR ``text``. Checks three things:
      * section presence (50): how many of the expected headers rendered.
      * non-hollow content (35): too many empty "None today." sections fails.
      * date sanity (15): the title carries ``send_date`` when one is supplied.

    Returns {score, issues, present_sections, empty_sections, quarantine}.
    """
    if text is None:
        if path is None:
            raise ValueError("score_briefing requires either path or text")
        text = Path(path).read_text(encoding="utf-8")

    issues: list[str] = []
    lines = text.splitlines()

    # Section presence (50 pts, proportional).
    present = [s for s in BRIEFING_SECTIONS if any(s in ln for ln in lines)]
    missing = [s for s in BRIEFING_SECTIONS if s not in present]
    presence = 50.0 * len(present) / len(BRIEFING_SECTIONS)
    if missing:
        issues.append(f"missing sections: {', '.join(missing)}")

    # Hollow-content check (35 pts). Count sections whose body is the empty
    # fallback. We approximate by counting empty-marker lines.
    empty = sum(1 for ln in lines if ln.strip() in _EMPTY_MARKERS)
    if empty <= BRIEFING_MAX_EMPTY:
        content = 35.0
    else:
        over = empty - BRIEFING_MAX_EMPTY
        content = max(0.0, 35.0 - 10.0 * over)
        issues.append(
            f"{empty} empty sections (max {BRIEFING_MAX_EMPTY}) -- briefing is hollow")

    # Date sanity (15 pts).
    date_pts = 15.0
    if send_date:
        if any(send_date in ln for ln in lines[:3]):
            pass
        else:
            date_pts = 0.0
            issues.append(f"send date {send_date} not in title")

    score = int(round(presence + content + date_pts))
    return {
        "score": score,
        "issues": issues,
        "present_sections": present,
        "empty_sections": empty,
        "quarantine": score < BRIEFING_QUARANTINE_BELOW,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score a drilldown note 0-100.")
    parser.add_argument("path", help="path to an HTML or markdown drilldown note")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--briefing", action="store_true",
                        help="score a rendered daily briefing instead of a drilldown")
    parser.add_argument("--send-date", default=None,
                        help="expected send date (briefing mode date-sanity check)")
    args = parser.parse_args(argv)
    if args.briefing:
        card = score_briefing(args.path, send_date=args.send_date)
        print(f"BRIEFING SCORE: {card['score']}/100  "
              f"({'QUARANTINE' if card['quarantine'] else 'PASS'})")
        print(f"  sections present={len(card['present_sections'])}/"
              f"{len(BRIEFING_SECTIONS)}  empty={card['empty_sections']}")
        for n in card["issues"]:
            print(f"        - {n}")
        return 0 if not card["quarantine"] else 1
    card = score_drilldown(args.path, ticker=args.ticker)
    print(f"SCORE: {card['score']}/100  "
          f"({'QUARANTINE' if card['quarantine'] else 'PASS'})")
    print(f"  words={card['word_count']}  claims={card['claim_count']}")
    for name, b in card["breakdown"].items():
        print(f"  {name:22s} {b['score']:>5}/{b['max']}")
        for n in b["notes"]:
            print(f"        - {n}")
    return 0 if not card["quarantine"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
