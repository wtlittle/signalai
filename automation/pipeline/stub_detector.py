"""
Shared stub-note detection logic.

A "stub note" is an earnings note that was generated BEFORE the actual print
hit the research sources, so it contains placeholder / not-yet-reported prose
("Q1 FY27 results are not yet released, so a post-earnings assessment cannot be
made") with Beat/Miss = N/A and null metrics. These were nonetheless persisted
to disk and now render as broken cards on the dashboard.

This module is the single source of truth for stub detection. It is imported by
BOTH:
  * scripts/detect_stub_notes.py   (the offline audit / archive driver)
  * automation/jobs/post_earnings_notes.py  (the runtime write-guard)

so the audit and the guard can never drift apart.

Two entry points:
  * is_stub_note(note_data)  -- structured-payload check, used by the runtime
    guard BEFORE a note is written to disk. Operates on the dict the LLM
    returned (headline / beat_miss_quality / key_metrics / ...).
  * classify_note_text(text, ...) -- rendered-markdown check, used by the audit
    to scan notes already on disk. Returns (is_stub, reasons).
"""
from __future__ import annotations

import re
from datetime import date

# Phrases that only appear when the note was written before the print landed.
# Matched case-insensitively against the headline / beat-miss text.
STUB_PHRASES = (
    "not yet released",
    "not yet reported",
    "cannot be made",
    "have not yet reported",
    "has not yet reported",
    "not yet announced",
    "not yet available",
    "results still pending",
    "scheduled for release",
    "will only be available after",
    "will only be knowable",
)

# A note whose body is just the sanity-check failure scaffold (written by the
# pipeline's STAGE-1 gate) is also a stub.
SANITY_STUB_MARKER = "Context sanity check failed"


def _contains_stub_phrase(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in STUB_PHRASES)


def _is_na(value) -> bool:
    """True when a field is missing or an N/A placeholder."""
    if value is None:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        return s == "" or s == "n/a" or s.startswith("n/a")
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return True
        return all(_is_na(v) for v in value)
    return False


def is_stub_note(note_data: dict) -> bool:
    """Structured-payload stub check for the runtime write-guard.

    Returns True when the payload the LLM produced is a not-yet-reported stub
    and therefore must NOT be persisted. Conservative: a real note that merely
    omits one optional field will not trip this.
    """
    if not isinstance(note_data, dict):
        return False

    # The post-earnings prompt asks sonar for a MARKDOWN note (numbered sections)
    # plus trailing fenced JSON blocks -- not a single JSON object. The client's
    # json.loads therefore fails and wraps the whole response as {"raw": <md>}.
    # That is the NORMAL successful shape for a post-earnings payload, so a raw
    # payload must be judged on its prose, not on absent structured keys
    # (judging on absent keys flags every real note as a stub).
    raw = note_data.get("raw")
    if isinstance(raw, str) and raw.strip():
        if _contains_stub_phrase(raw):
            return True
        if SANITY_STUB_MARKER in raw:
            return True
        # A real note quantifies the print: look for actual-vs-estimate prose.
        # If none of that is present, treat as an empty/placeholder payload.
        low = raw.lower()
        has_substance = any(tok in low for tok in (" vs ", "beat", "miss", "actual", "reported", "guidance"))
        return not has_substance

    headline = note_data.get("headline") or ""
    bm = note_data.get("beat_miss_quality") or ""

    # Strong signal: the canonical not-yet-reported language in the headline or
    # the beat/miss verdict. Either alone is sufficient.
    if _contains_stub_phrase(headline) or _contains_stub_phrase(bm):
        return True

    # Weaker signals -- require 3+ to fire to avoid false positives on a real
    # note that happens to be light on one section.
    signals = 0
    if _is_na(bm):
        signals += 1
    if _is_na(note_data.get("key_metrics")):
        signals += 1
    if _is_na(note_data.get("guidance")):
        signals += 1
    if _is_na(note_data.get("thesis_impact")):
        signals += 1
    return signals >= 3


# --- rendered-markdown audit path -------------------------------------------

_HEADLINE_RE = re.compile(r"##\s*Headline\s*\n+(.+?)(?:\n\s*\n|\Z)", re.DOTALL)
_BEATMISS_RE = re.compile(r"\*\*Beat/Miss Quality:\*\*\s*(.+)")


def _extract_headline(text: str) -> str:
    m = _HEADLINE_RE.search(text)
    return m.group(1).strip() if m else ""


def _extract_beat_miss(text: str) -> str:
    m = _BEATMISS_RE.search(text)
    return m.group(1).strip() if m else ""


def _count_sources(text: str) -> int:
    """Count bullet sources under the *Sources:* footer."""
    idx = text.rfind("*Sources:*")
    if idx == -1:
        return 0
    tail = text[idx:]
    return len(re.findall(r"^\s*-\s+http", tail, re.MULTILINE))


def classify_note_text(
    text: str,
    reported_date: str | None = None,
    today: date | None = None,
    has_reaction: bool = False,
) -> tuple[bool, list[str]]:
    """Audit a rendered markdown note. Returns (is_stub, reasons).

    Combines the signals from the task spec:
      * headline_pattern  -- headline/beat-miss carries not-yet-reported prose
      * sanity_stub       -- body is the sanity-check failure scaffold
      * beat_miss_na      -- Beat/Miss == N/A and reported_date < today
      * single_source     -- exactly one source cited (limited-sources tell)
      * empty_metrics     -- metrics block is all N/A (print happened, note empty)

    Classification: stub if the headline pattern (or sanity scaffold) matches
    alone, OR if 2+ of the remaining signals fire together.
    """
    reasons: list[str] = []
    today = today or date.today()

    rdate: date | None = None
    if reported_date:
        try:
            rdate = date.fromisoformat(reported_date)
        except ValueError:
            rdate = None
    reported_in_past = rdate is not None and rdate < today

    headline = _extract_headline(text)
    bm = _extract_beat_miss(text)

    # Strong, standalone signals.
    if _contains_stub_phrase(headline) or _contains_stub_phrase(bm):
        reasons.append("headline_pattern")
    if SANITY_STUB_MARKER in text:
        reasons.append("sanity_stub")

    # Supporting signals (need 2+ together when no strong signal present).
    support: list[str] = []
    if _is_na(bm) and reported_in_past:
        support.append("beat_miss_na")
    if _count_sources(text) == 1:
        support.append("single_source")

    metrics_block = ""
    mm = re.search(r"##\s*Key Metrics\s*\n+(.*?)\n\s*---", text, re.DOTALL)
    if mm:
        metrics_block = mm.group(1)
    metric_lines = [l for l in metrics_block.splitlines() if l.strip().startswith("-")]
    if metric_lines and all(_is_na(l.lstrip("- ").strip()) for l in metric_lines):
        if has_reaction or reported_in_past:
            support.append("empty_metrics")

    if reasons:
        reasons.extend(support)
        return True, reasons

    if len(support) >= 2:
        return True, support

    return False, support
