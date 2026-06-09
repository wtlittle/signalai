#!/usr/bin/env python3
"""Post-generation quality validator for institutional drilldown notes.

A v1 batch of NET/NVDA/RBRK drilldowns regressed badly: the model rendered the
prompt's `── N. HEADER ──` markers as visible `<h2>1. Header</h2>`, left
financial cells reading the literal "MISSING", emitted zero `claim:` citation
links, and applied no `section-title` classes. This validator is the gate that
catches those failures before a note is published to the Drilldown Library.

Checks (all must pass):
  a. ZERO occurrences of the literal string "MISSING" in the rendered note.
  b. All 14 canonical sections present (matched on section-title text). The
     first section is now titled "Investment Overview" (renamed from
     "Header / Metadata"); "header"/"metadata" stay accepted as aliases.
  c. >= 20 `claim:` link occurrences.
  d. Word count between 3000 and 8000 (text only, tags/style stripped).
  e. Every section header uses class="section-title" (and no <h2>/<h3> headers).
  f. ARR-led tickers (ARR_LED_TICKERS) surface >= 3 ARR-family metrics
     (ARR / Net new ARR / NRR / RPO / cRPO). Skipped when no ticker is passed.

CLI:
    python -m automation.jobs.drilldown_validator <file.md>
Exit 0 = pass, exit 1 = fail (with a detailed diff printed to stderr).
"""
import argparse
import re
import sys
from pathlib import Path

MIN_CLAIMS = 20
MIN_WORDS = 3000
MAX_WORDS = 8000

# ARR-led SaaS cohort: companies that report ARR (not GAAP revenue) as their
# headline growth metric. For these, the rendered note must anchor commentary on
# ARR-family metrics. Kept in sync with drilldown_prompt.md and the
# financial-metric-prioritization skill (§2a). See ARR_REQUIRED_TERMS below.
ARR_LED_TICKERS = {
    'RBRK', 'NET', 'CRWD', 'ZS', 'OKTA', 'DDOG', 'MDB', 'SNOW', 'ESTC', 'S',
    'NTNX', 'BILL', 'GTLB', 'FROG', 'CFLT', 'DT', 'PD', 'BOX', 'ASAN', 'MNDY',
    'SMAR', 'ZUO', 'AI', 'PATH', 'U', 'RNG', 'FIVN', 'TWLO', 'FSLY', 'NCNO',
    'BSY', 'AVPT', 'DOMO',
}

# Each entry is (display_label, list_of_case_insensitive_substrings). An ARR-led
# note must surface at least MIN_ARR_TERMS of these.
ARR_REQUIRED_TERMS = [
    ('ARR', ['arr']),
    ('Net new ARR', ['net new arr']),
    ('NRR', ['nrr', 'net dollar retention', 'net revenue retention']),
    ('RPO', ['rpo']),
    ('cRPO', ['crpo', 'current rpo', 'current remaining performance']),
]
MIN_ARR_TERMS = 3

# The 14 canonical sections, keyed by a stable label and matched against the
# rendered section-title text via keyword alternatives. The on-disk gold
# standard (FROG) uses these exact titles; the keyword sets tolerate minor
# wording drift while still pinning each required section.
REQUIRED_SECTIONS = [
    # First section is the report-header block (detected structurally via
    # class="report-header"); the prompt now titles it "Investment Overview".
    # "header"/"metadata" remain accepted aliases for transition compatibility
    # with notes generated under the old "Header / Metadata" title.
    ('INVESTMENT OVERVIEW (HEADER)', ['investment overview', 'header', 'metadata']),
    ('DEBATE FRAMING', ['debate framing', 'one-sentence', 'one sentence']),
    ('CATALYSTS', ['catalyst']),
    ('VALUATION', ['valuation', 'underwriting']),
    ('BUSINESS MODEL', ['business model', 'kpi dashboard']),
    ('BULL / BASE / BEAR', ['bull / base / bear', 'bull/base/bear',
                            'investment thesis', 'base / bear']),
    ('FINANCIAL MODEL SNAPSHOT', ['financial model']),
    ('SENSITIVITY', ['sensitivity']),
    ('INDUSTRY / COMPETITIVE', ['industry structure', 'competitive positioning',
                                'competitive landscape', 'market position']),
    ('EARNINGS SETUP', ['earnings setup', 'revision debate']),
    ('MANAGEMENT / CAPITAL ALLOCATION', ['management', 'capital allocation']),
    ('RISKS', ['risk']),
    ('DILIGENCE QUESTIONS', ['diligence question', 'primary diligence']),
    ('SOURCES', ['sources', 'data quality']),
]


def _strip_frontmatter(text):
    """Remove a leading YAML frontmatter block if present."""
    if text.startswith('---'):
        m = re.match(r'^---\n.*?\n---\n', text, flags=re.DOTALL)
        if m:
            return text[m.end():]
    return text


def _word_count(html):
    text = re.sub(r'<script[\s\S]*?</script>', ' ', html, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return len(text.split()) if text else 0


def _section_titles(html):
    """Return the list of rendered section-title text strings."""
    titles = re.findall(
        r'class="section-title"[^>]*>(.*?)</', html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = []
    for t in titles:
        t = re.sub(r'<[^>]+>', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        if t:
            cleaned.append(t)
    return cleaned


def _section_labels(html):
    """All text that can name a section for presence-detection.

    The HEADER and SOURCES sections of the gold-standard layout are rendered
    via `report-header` / `footer-head` rather than `section-title`, so we also
    harvest the footer head text and the page <title> to detect them, while
    section presence for the 12 body sections still relies on section-title.
    """
    labels = list(_section_titles(html))
    labels += re.findall(
        r'class="footer-head"[^>]*>(.*?)</', html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    labels += re.findall(r'<title[^>]*>(.*?)</title>', html,
                         flags=re.DOTALL | re.IGNORECASE)
    cleaned = []
    for t in labels:
        t = re.sub(r'<[^>]+>', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        if t:
            cleaned.append(t)
    return cleaned


def validate(text, ticker=None):
    """Run all checks. Return (ok: bool, failures: list[str]).

    When ``ticker`` is supplied and belongs to the ARR-led cohort
    (ARR_LED_TICKERS), an extra check (f) requires the note to surface at least
    MIN_ARR_TERMS ARR-family metrics. Omitting ``ticker`` skips that check so
    callers that lack the symbol keep working unchanged.
    """
    body = _strip_frontmatter(text)
    failures = []

    # (a) No literal "MISSING".
    missing_hits = len(re.findall(r'MISSING', body))
    if missing_hits:
        failures.append(
            f'(a) literal "MISSING" appears {missing_hits}x — use "—" or "n/a" instead'
        )

    # (e) Section headers use section-title; no heading tags for sections.
    titles = _section_titles(body)
    if not titles:
        failures.append('(e) no class="section-title" headers found')
    heading_tags = re.findall(r'<h[1-3][\s>]', body, flags=re.IGNORECASE)
    if heading_tags:
        failures.append(
            f'(e) {len(heading_tags)} <h1>/<h2>/<h3> heading tag(s) present — '
            'sections must use <div class="section-title">'
        )
    numbered = [t for t in titles if re.match(r'^\s*\d+\s*\.', t)]
    if numbered:
        failures.append(
            f'(e) {len(numbered)} section-title(s) start with a number '
            f'(e.g. {numbered[0]!r}) — strip the leading "N."'
        )

    # (b) All 14 sections present. HEADER is rendered via the report-header
    # block (no section-title), so detect it structurally.
    haystack = ' '.join(_section_labels(body)).lower()
    has_report_header = bool(
        re.search(r'class="report-header"', body, flags=re.IGNORECASE)
    )
    missing_sections = []
    for label, keywords in REQUIRED_SECTIONS:
        if label == 'INVESTMENT OVERVIEW (HEADER)' and has_report_header:
            continue
        if not any(kw in haystack for kw in keywords):
            missing_sections.append(label)
    if missing_sections:
        failures.append(
            f'(b) missing {len(missing_sections)}/14 sections: '
            + ', '.join(missing_sections)
        )

    # (c) >= 20 claim: links.
    claim_count = len(re.findall(r'claim:', body))
    if claim_count < MIN_CLAIMS:
        failures.append(
            f'(c) only {claim_count} claim: links — need >= {MIN_CLAIMS}'
        )

    # (d) Word count window.
    wc = _word_count(body)
    if wc < MIN_WORDS or wc > MAX_WORDS:
        failures.append(
            f'(d) word count {wc} outside [{MIN_WORDS}, {MAX_WORDS}]'
        )

    # (f) ARR-led tickers must anchor on ARR-family metrics.
    if ticker and ticker.strip().upper() in ARR_LED_TICKERS:
        low = body.lower()
        present = [
            label for label, subs in ARR_REQUIRED_TERMS
            if any(s in low for s in subs)
        ]
        if len(present) < MIN_ARR_TERMS:
            failures.append(
                f'(f) {ticker.strip().upper()} is ARR-led but only '
                f'{len(present)}/{len(ARR_REQUIRED_TERMS)} ARR metrics present '
                f'({present or "none"}) — need >= {MIN_ARR_TERMS} of '
                'ARR / Net new ARR / NRR / RPO / cRPO'
            )

    return (not failures), failures


def validate_file(path, ticker=None):
    text = Path(path).read_text(encoding='utf-8')
    if ticker is None:
        # Infer ticker from a leading "TICKER_" filename (e.g. NET_2026-06-09_p1.md).
        m = re.match(r'^([A-Za-z0-9.]+)_', Path(path).name)
        if m:
            ticker = m.group(1)
    return validate(text, ticker=ticker)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Validate an institutional drilldown markdown/HTML note.'
    )
    parser.add_argument('file', help='path to the drilldown .md file')
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f'[validator] file not found: {path}', file=sys.stderr)
        return 1

    ok, failures = validate_file(path)
    if ok:
        print(f'[validator] PASS  {path}')
        return 0

    print(f'[validator] FAIL  {path}', file=sys.stderr)
    for f in failures:
        print(f'  - {f}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
