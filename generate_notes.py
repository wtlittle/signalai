#!/usr/bin/env python3
"""Generate post-earnings markdown notes from research data."""
import csv
import os
from datetime import datetime

from dateutil import parser as date_parser

import os as _os
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
NOTES_DIR = _os.path.join(_SCRIPT_DIR, 'notes', 'post_earnings')

GENERATED = datetime.today().strftime('%Y-%m-%d')

# Read research results
rows = []
with open(_os.path.join(_SCRIPT_DIR, 'data', 'research', 'post_earnings_results.csv')) as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

written = 0
for row in rows:
    ticker = row.get('Ticker', '').strip()
    company = row.get('Company Name', '').strip()
    raw_date = row.get('Earnings Date', '').strip()
    try:
        date_str = date_parser.parse(raw_date).strftime('%Y-%m-%d')
    except (ValueError, OverflowError, TypeError) as exc:
        print(f'  [WARN] {ticker}: could not parse date {raw_date!r} ({exc}); skipping row')
        continue

    fq = row.get('Fiscal Quarter', '')
    rev_actual = row.get('Revenue (Actual)', '')
    rev_est = row.get('Revenue (Estimate)', '')
    rev_bm = row.get('Revenue Beat/Miss', '')
    eps_actual = row.get('EPS (Actual)', '')
    eps_est = row.get('EPS (Estimate)', '')
    eps_bm = row.get('EPS Beat/Miss', '')
    guide_q = row.get('Revenue Guidance (Next Q)', '')
    guide_fy = row.get('Revenue Guidance (FY)', '')
    key_metrics = row.get('Key Metrics', '')
    tone = row.get('Management Tone', '')
    stock_rx = row.get('Stock Reaction', '')
    analyst_rx = row.get('Analyst Reactions', '')
    surprises = row.get('Key Surprises', '')
    thesis = row.get('Thesis Impact', '')
    sources = row.get('Sources', '')

    # Format key metrics as bullet list
    metrics_bullets = ''
    if key_metrics:
        items = [m.strip() for m in key_metrics.split(';')]
        metrics_bullets = '\n'.join(f'- {m}' for m in items if m)

    # Format analyst reactions  
    analyst_bullets = ''
    if analyst_rx:
        items = [a.strip() for a in analyst_rx.split(';')]
        analyst_bullets = '\n'.join(f'- {a}' for a in items if a)

    # Format surprises
    surprise_bullets = ''
    if surprises:
        items = [s.strip() for s in surprises.split(';')]
        surprise_bullets = '\n'.join(f'- {s}' for s in items if s)

    note = f"""# {company} ({ticker}) — {fq} Post-Earnings Note
**Reported: {date_str}** | Generated: {GENERATED}

---

## Headline Results vs Expectations

| Metric | Actual | Estimate | Result |
|--------|--------|----------|--------|
| Revenue | {rev_actual} | {rev_est} | {rev_bm} |
| EPS | {eps_actual} | {eps_est} | {eps_bm} |

**Stock Reaction:** {stock_rx}

---

## Key Operating Metrics

{metrics_bullets}

---

## Guidance and Tone

**Next Quarter:** {guide_q}

**Full Year:** {guide_fy}

**Management Tone:** {tone}

---

## Analyst Reactions

{analyst_bullets}

---

## Key Surprises / Disappointments

{surprise_bullets}

---

## Thesis Impact

{thesis}

---

## Follow-ups

- Monitor next quarter execution against guidance
- Track key metric trends (ARR growth, margin trajectory)
- Watch for management commentary shifts on AI/macro
- Review consensus estimate revisions post-earnings

---

*Sources: {sources}*
"""

    filename = f'{ticker}_{date_str}.md'
    filepath = os.path.join(NOTES_DIR, filename)
    with open(filepath, 'w') as f:
        f.write(note)
    written += 1
    print(f'  Written: {filename}')

print(f'\nAll {written} post-earnings notes generated.')
