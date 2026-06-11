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
  d. Word count between 3500 and 6500 (text only, tags/style stripped).
  e. Every section header uses class="section-title" (and no <h2>/<h3> headers).
  f. ARR-led tickers (ARR_LED_TICKERS) surface >= 3 ARR-family metrics
     (ARR / Net new ARR / NRR / RPO / cRPO). Skipped when no ticker is passed.
  g. STRICT THESIS OPENER (VOICE CONTRACT §A): the first sentence of the
     Investment Overview body is bold-wrapped, <= 250 chars, contains a digit,
     a horizon keyword, and a directional claim, and matches none of the
     forbidden encyclopedic/consultantese opener patterns.
  h. Asymmetric payoff: the recommendation region carries a Bull/Base/Bear
     payoff table (else "missing_asymmetric_payoff").
  i. Right/wrong: the recommendation region has both "What makes us right" and
     "What makes us wrong" (else "missing_right_wrong").
  j. Banned phrases ("It is worth noting that", "Importantly,", "Notably,")
     appear zero times.

CLI:
    python -m automation.jobs.drilldown_validator <file.md>
Exit 0 = pass, exit 1 = fail (with a detailed diff printed to stderr).
"""
import argparse
import re
import sys
from pathlib import Path

MIN_CLAIMS = 20
MIN_WORDS = 3500
MAX_WORDS = 6500

# Strict thesis-opener constraints (VOICE CONTRACT §A). The first sentence of
# the Investment Overview body must be a directional thesis, not a 10-K-style
# description.
MAX_OPENER_CHARS = 250

# Horizon keyword: the opener must put a clock on the thesis.
HORIZON_RE = re.compile(
    r'\b(next (12|18|24) months|12mo|1-3yr|by FY?\d{2}|through FY?\d{2}|'
    r'into 20\d{2}|by 20\d{2}|over the next (one|two|three) years?|FY\d{2})\b',
    re.IGNORECASE,
)

# Directional claim: the opener must state a direction, not just a fact.
DIRECTION_RE = re.compile(
    r'\b(re-?rating|breakout|de-?rate|transition|expansion|compression|ramp)\b',
    re.IGNORECASE,
)

# Forbidden opener shapes (encyclopedic / consultantese tells). Matched against
# the de-marked-up first sentence, case-insensitive.
FORBIDDEN_OPENER_RES = [
    (re.compile(r'\bis an? [a-z\-]+ company\b', re.IGNORECASE),
     'definitional "is a/an [sector] company that…"'),
    (re.compile(r'is positioned at the intersection', re.IGNORECASE),
     'vague "positioned at the intersection of…"'),
    (re.compile(r'Corporation is the (leading|dominant)', re.IGNORECASE),
     'descriptive "Corporation is the leading/dominant…"'),
    (re.compile(r'operates in the .{0,40} market', re.IGNORECASE),
     '10-K boilerplate "operates in the [X] market…"'),
    (re.compile(r'investment debate centers on', re.IGNORECASE),
     'neutral "the investment debate centers on whether…"'),
]

# Phrases banned outright across the whole document (zero occurrences allowed).
BANNED_PHRASES = ['It is worth noting that', 'Importantly,', 'Notably,']

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
    ('EARNINGS SETUP', ['earnings setup', 'revision debate',
                        '2-year price', 'earnings reactions']),
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


def _strip_tags(html):
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()


def _investment_overview_opener(body):
    """Return (raw_first_unit, first_sentence_text) for the Investment Overview.

    Locates the Investment Overview section, grabs its first markdown paragraph
    or first ``<p>`` block, and isolates the first sentence. ``raw_first_unit``
    retains the markup (so we can check for bold/strong wrapping); the second
    element is the de-marked-up sentence text used for length / keyword checks.
    Returns (None, None) if the section or a leading paragraph can't be found.
    """
    # Find the Investment Overview section. Prefer the HTML section-body; the
    # first section-title named "Investment Overview" (not the Bull/Base/Bear
    # variant) anchors it.
    m = re.search(
        r'class="section-title"[^>]*>\s*Investment Overview\s*<',
        body, flags=re.IGNORECASE,
    )
    region = None
    if m:
        # Take everything from this title to the next section-title (or 4000
        # chars, whichever comes first) as the section region.
        start = m.end()
        nxt = re.search(r'class="section-title"', body[start:], flags=re.IGNORECASE)
        region = body[start:start + (nxt.start() if nxt else 4000)]
    if region is None:
        # Markdown fallback: a "## Investment Overview" heading.
        m = re.search(r'#+\s*Investment Overview\s*\n', body, flags=re.IGNORECASE)
        if m:
            start = m.end()
            nxt = re.search(r'\n#+\s', body[start:])
            region = body[start:start + (nxt.start() if nxt else 4000)]
    if region is None:
        return None, None

    # First paragraph: prefer a <p>…</p> block; else first non-empty md line.
    pm = re.search(r'<p[^>]*>(.*?)</p>', region, flags=re.DOTALL | re.IGNORECASE)
    if pm:
        para = pm.group(1)
    else:
        para = ''
        for line in region.splitlines():
            if _strip_tags(line):
                para = line
                break
    if not para.strip():
        return None, None

    # First sentence = up to the first sentence-ending period/!?. We split on
    # the de-marked-up text but keep the raw leading unit for the bold check by
    # taking the raw paragraph up to the same boundary.
    plain_para = _strip_tags(para)
    sm = re.search(r'^(.*?[.!?])(\s|$)', plain_para, flags=re.DOTALL)
    first_sentence = sm.group(1).strip() if sm else plain_para.strip()
    return para, first_sentence


def _recommendation_region(body):
    """Return the text region most likely to hold the recommendation/payoff.

    The gold layout folds the recommendation into the Bull/Base/Bear and
    Valuation sections rather than a standalone "Recommendation" header, so we
    union any explicit Recommendation section with the Bull/Base/Bear section.
    Falls back to the whole document if neither anchor is found.
    """
    titles = list(re.finditer(r'class="section-title"[^>]*>(.*?)</', body,
                              flags=re.DOTALL | re.IGNORECASE))
    chunks = []
    for i, tm in enumerate(titles):
        label = _strip_tags(tm.group(1)).lower()
        if 'recommend' in label or 'bull' in label or 'base / bear' in label:
            start = tm.end()
            end = titles[i + 1].start() if i + 1 < len(titles) else len(body)
            chunks.append(body[start:end])
    return '\n'.join(chunks) if chunks else body


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

    # (g) Strict thesis opener (VOICE CONTRACT §A).
    raw_unit, opener = _investment_overview_opener(body)
    if opener is None:
        failures.append(
            '(g) could not locate the Investment Overview opening paragraph'
        )
    else:
        # Bold/strong wrapping: the raw first unit must contain a **…** or
        # <strong>…</strong> span covering the opener text.
        bold_md = re.search(r'\*\*.+?\*\*', raw_unit, flags=re.DOTALL)
        bold_html = re.search(r'<strong>.+?</strong>', raw_unit,
                              flags=re.DOTALL | re.IGNORECASE)
        if not (bold_md or bold_html):
            failures.append(
                '(g) thesis opener is not wrapped in **…** or <strong>…</strong>'
            )
        if len(opener) > MAX_OPENER_CHARS:
            failures.append(
                f'(g) thesis opener is {len(opener)} chars — must be '
                f'<= {MAX_OPENER_CHARS}'
            )
        if not re.search(r'\d', opener):
            failures.append('(g) thesis opener contains no digit')
        if not HORIZON_RE.search(opener):
            failures.append(
                '(g) thesis opener has no horizon keyword (e.g. "next 18 '
                'months", "FY27", "into 2027")'
            )
        if not DIRECTION_RE.search(opener):
            failures.append(
                '(g) thesis opener has no directional claim (re-rating, '
                'breakout, derate, transition, expansion, compression, ramp)'
            )
        for rx, desc in FORBIDDEN_OPENER_RES:
            if rx.search(opener):
                failures.append(
                    f'(g) thesis opener matches forbidden pattern: {desc}'
                )

    # (h) Asymmetric payoff table in the recommendation region.
    rec = _recommendation_region(body)
    rec_low = rec.lower()
    if not ('bull' in rec_low and 'base' in rec_low and 'bear' in rec_low):
        failures.append(
            'missing_asymmetric_payoff: recommendation lacks a Bull/Base/Bear '
            'payoff table'
        )

    # (i) What makes us right / wrong.
    if not ('what makes us right' in rec_low
            and 'what makes us wrong' in rec_low):
        failures.append(
            'missing_right_wrong: recommendation lacks "What makes us right" '
            'and/or "What makes us wrong"'
        )

    # (j) Banned phrases anywhere in the document.
    for phrase in BANNED_PHRASES:
        n = len(re.findall(re.escape(phrase), body))
        if n:
            failures.append(
                f'(j) banned phrase {phrase!r} appears {n}x — must be 0'
            )

    # (k) Earnings chart: SVG with class="earnings-chart" must be present.
    #     This is injected post-generation by _inject_earnings_chart(); a
    #     missing SVG indicates the injection step failed or the section title
    #     could not be matched.  The check is soft (warning, not blocking) when
    #     the section itself is missing (already flagged in (b)), but hard when
    #     the section is present.
    chart_omitted = 'earnings-chart omitted' in body
    if (not chart_omitted
            and not re.search(r'class=["\']earnings-chart["\']', body, re.IGNORECASE)):
        # Only flag if the section was found (otherwise (b) already covers it).
        # An explicit omission sentinel (no real earnings event dates to
        # annotate) is an intentional, valid state — not a failure.
        if any(
            any(kw in body.lower() for kw in kws)
            for lbl, kws in REQUIRED_SECTIONS
            if lbl == 'EARNINGS SETUP'
        ):
            failures.append(
                '(k) earnings chart SVG (class="earnings-chart") not found in '
                'the Earnings Setup section — chart injection may have failed'
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
