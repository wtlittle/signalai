#!/usr/bin/env python3
"""
Seed earnings_intel.json from existing pre- and post-earnings note markdown files.

Extracts key sections mechanically and fills the canonical schema defined in
earnings_intel_schema.md. Produces a valid seed file — richer per-ticker
curation happens on subsequent scheduled refreshes.
"""
import json
import os
import re
from datetime import date, datetime, timedelta

ROOT = '/home/user/workspace/watchlist-app'
PRE_DIR = os.path.join(ROOT, 'notes', 'pre_earnings')
POST_DIR = os.path.join(ROOT, 'notes', 'post_earnings')
OUT_FILE = os.path.join(ROOT, 'earnings_intel.json')

INTEL_TIMESTAMP = '2026-04-21T08:00:00-04:00'

# Company names — from the watchlist table
COMPANY_NAMES = {
    'UNH': 'UnitedHealth Group', 'DHR': 'Danaher Corporation', 'COF': 'Capital One Financial',
    'SYF': 'Synchrony Financial', 'IBM': 'International Business Machines', 'NEM': 'Newmont Corporation',
    'INTC': 'Intel Corporation', 'BKR': 'Baker Hughes', 'ZBH': 'Zimmer Biomet Holdings',
    'ADP': 'Automatic Data Processing', 'GNRC': 'Generac Holdings', 'AMZN': 'Amazon.com',
    'GOOG': 'Alphabet', 'META': 'Meta Platforms', 'MSFT': 'Microsoft Corporation',
    'CAT': 'Caterpillar Inc.', 'VRTX': 'Vertex Pharmaceuticals',
    'STZ': 'Constellation Brands', 'GS': 'Goldman Sachs', 'JPM': 'JPMorgan Chase',
    'C': 'Citigroup', 'WFC': 'Wells Fargo', 'MS': 'Morgan Stanley', 'BAC': 'Bank of America',
    'ABT': 'Abbott Laboratories', 'TSM': 'Taiwan Semiconductor', 'NFLX': 'Netflix',
}


def read_note(path):
    with open(path, 'r') as f:
        return f.read()


def extract_section(md, heading_pattern, stop_pattern=r'\n##\s'):
    """Return text between a heading match and the next heading."""
    m = re.search(heading_pattern + r'.*?\n(.*?)(?=' + stop_pattern + r'|\Z)', md, re.DOTALL)
    return m.group(1).strip() if m else ''


def extract_bullets(text, max_items=5):
    """Extract dashed or numbered list bullets from a text block, stripped."""
    bullets = []
    for line in text.split('\n'):
        line = line.strip()
        m = re.match(r'^(?:[-*]|\d+\.)\s+(.+)$', line)
        if m:
            bullet = m.group(1).strip()
            # Strip leading bold heading "**Label:** " pattern while keeping content
            bullet = re.sub(r'\*\*', '', bullet)
            if bullet and len(bullet) > 5:
                bullets.append(bullet[:300])
                if len(bullets) >= max_items:
                    break
    return bullets


def extract_first_paragraph(text, max_chars=500):
    """First non-empty paragraph."""
    for para in text.split('\n\n'):
        para = para.strip()
        if para and not para.startswith('|') and not para.startswith('#'):
            # Strip markdown formatting
            clean = re.sub(r'\*\*', '', para)
            clean = re.sub(r'\*', '', clean)
            clean = re.sub(r'\s+', ' ', clean)
            return clean[:max_chars]
    return ''


def extract_urls(md, max_urls=3):
    """Extract first few source URLs from note footer."""
    urls = []
    for m in re.finditer(r'\[([^\]]+)\]\((https?://[^)]+)\)', md):
        label, url = m.group(1), m.group(2)
        if not any(u['url'] == url for u in urls):
            urls.append({'label': label[:80], 'url': url})
            if len(urls) >= max_urls:
                break
    return urls


def extract_scenario_row(md, keyword):
    """Find a line in a scenario grid table matching bull/base/bear and return the cells."""
    # Find scenario grid table
    for line in md.split('\n'):
        if line.strip().startswith('|') and keyword.lower() in line.lower():
            cells = [c.strip().replace('*', '').strip() for c in line.split('|') if c.strip()]
            return cells
    return []


def derive_pre_earnings_record(ticker, earnings_date, md):
    """Build a pre-earnings intel record from the note markdown."""
    name = COMPANY_NAMES.get(ticker, ticker)

    # Bottom line — first paragraph of Set-up, or Thesis Impact
    setup = extract_section(md, r'##\s+(?:Set-up|Setup|The Set-up|Key Setup)')
    bottom_line = extract_first_paragraph(setup, max_chars=650) or f"{ticker} approaches its {earnings_date} print with the setup defined in the linked legacy note."

    # Scenario grid rows
    bull_row = extract_scenario_row(md, 'Bull')
    base_row = extract_scenario_row(md, 'Base')
    bear_row = extract_scenario_row(md, 'Bear')

    # Key debates / pushes
    debates_section = extract_section(md, r'##\s+(?:Key Debates|Debates|What Matters|Variants|Key Debates and Variants)')
    debate_bullets = extract_bullets(debates_section, max_items=4)

    bull_case = {
        'thesis_headline': (bull_row[4] if len(bull_row) >= 5 else '') or f'Clean quarter + maintained guide re-rates {ticker}.',
        'pattern': '',
        'pushes_higher': debate_bullets[:3] if debate_bullets else ['In-line-to-better print', 'Guidance maintained or raised', 'No new overhangs disclosed'],
        'pushes_lower': [],
    }
    base_case = {
        'setup_headline': (base_row[4] if len(base_row) >= 5 else '') or 'In-line print, guidance maintained, narrative intact.',
        'pushes_higher': debate_bullets[1:3] if len(debate_bullets) > 1 else [],
        'pushes_lower': debate_bullets[2:4] if len(debate_bullets) > 2 else [],
    }
    bear_case = {
        'thesis_headline': (bear_row[4] if len(bear_row) >= 5 else '') or 'Miss + guide cut pressures multiple.',
        'pattern': '',
        'pushes_higher': [],
        'pushes_lower': debate_bullets[-3:] if debate_bullets else ['Revenue/EPS miss', 'Guide narrowed or lowered', 'New overhang disclosed'],
    }

    # Signal scorecard — derive from first 2-3 key debates as WATCHING signals
    signal_scorecard = []
    for i, bullet in enumerate(debate_bullets[:3]):
        # Use first 4 words as label
        words = bullet.split()
        label_raw = ' '.join(words[:5]).rstrip('.,:')
        label = label_raw if len(label_raw) > 3 else f'Signal {i+1}'
        sid = re.sub(r'[^a-z0-9_]+', '_', label.lower()).strip('_')[:40] or f'signal_{i+1}'
        signal_scorecard.append({
            'signal_id': sid,
            'label': label,
            'status': 'WATCHING',
            'note': bullet[:220],
            'watch_quarter': f'Q1 FY{earnings_date[:4]}',
        })

    # Sources
    sources = extract_urls(md, max_urls=4)

    # Legacy path
    legacy_path = f'notes/pre_earnings/{ticker}_{earnings_date}.md'

    # Previous earnings date — estimate ~3 months before
    try:
        ed = datetime.strptime(earnings_date, '%Y-%m-%d').date()
        prev_ed = (ed - timedelta(days=90)).strftime('%Y-%m-%d')
    except Exception:
        prev_ed = None

    return {
        'ticker': ticker,
        'company_name': name,
        'state': 'pre_earnings',
        'inflection_status': 'PRE',
        'last_earnings_date': prev_ed,
        'next_earnings_date': earnings_date,
        'intel_updated_at': INTEL_TIMESTAMP,
        'refresh_reason': 'scheduled_pre_earnings',
        'bottom_line': bottom_line,
        'bull_case': bull_case,
        'base_case': base_case,
        'bear_case': bear_case,
        'signal_scorecard': signal_scorecard,
        'guidance_profile': {
            'fy_guide_eps_low': None,
            'fy_guide_eps_high': None,
            'fy_guide_revenue_low': None,
            'fy_guide_revenue_high': None,
            'last_changed': None,
            'guide_style': None,
        },
        'tone_drift': {
            'current_tone': 'cautious_constructive',
            'prior_tone': 'cautious',
            'tone_notes': f'Tone drift pending detailed refresh; see legacy note for full setup.',
        },
        'theme_lifecycle': [],
        'inflection_library': [],
        'source_metadata': {
            'primary_sources': sources,
            'legacy_note_path': legacy_path,
        },
        'post_earnings_review': {
            'active': False,
            'earnings_date': None,
            'visible_until': None,
            'takeaways_headline': None,
            'takeaways_bullets': [],
            'what_happened_headline': None,
            'what_happened_bullets': [],
            'stock_reaction_pct': None,
        },
        'previous_bottom_line': None,
        'signal_changes': [],
    }


def derive_post_earnings_record(ticker, earnings_date, md):
    """Build a post-earnings intel record from the note markdown."""
    name = COMPANY_NAMES.get(ticker, ticker)

    # What Happened / Story of the Quarter
    story = extract_section(md, r'##\s+(?:The Story of the Quarter|Story of the Quarter|What Happened|Key Takeaways)')
    story_para = extract_first_paragraph(story, max_chars=450)

    # Why the Stock Reacted
    reaction = extract_section(md, r'##\s+(?:Why the Stock Reacted|Stock Reaction|The Reaction)')
    reaction_para = extract_first_paragraph(reaction, max_chars=400)

    # Thesis Impact / Takeaways
    thesis = extract_section(md, r'##\s+(?:Thesis Impact|Thesis Update|Takeaways|Bottom Line|Conclusion)')
    thesis_para = extract_first_paragraph(thesis, max_chars=500)

    # Guidance / Tone
    guide = extract_section(md, r'##\s+(?:Guidance and Tone|Guidance|Tone and Guidance|Outlook)')
    guide_bullets = extract_bullets(guide, max_items=4)
    if not guide_bullets:
        guide_bullets = [extract_first_paragraph(guide, max_chars=200)] if guide else []

    # Follow-ups
    followups = extract_section(md, r'##\s+(?:Follow-ups|Follow Ups|Open Questions|What to Watch)')
    followup_bullets = extract_bullets(followups, max_items=4)

    # Story bullets (what happened)
    story_bullets = extract_bullets(story, max_items=5)
    if len(story_bullets) < 3:
        story_bullets = []
        # Try headline results table if available — extract rows
        for line in md.split('\n'):
            if line.strip().startswith('|') and '|' in line and not line.strip().startswith('|--'):
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if len(cells) >= 3 and 'Metric' not in cells[0] and '---' not in cells[0]:
                    story_bullets.append(' · '.join(cells[:3]))
                    if len(story_bullets) >= 5:
                        break

    # Extract stock reaction %
    stock_pct = None
    m = re.search(r'(?:fell|dropped|declined|gained|rose|jumped|up|down)\s+(?:approximately\s+|~|about\s+)?([+-]?\d+(?:\.\d+)?)\s*%', reaction or md, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if re.search(r'(fell|dropped|declined|down)', m.group(0), re.IGNORECASE):
            val = -abs(val)
        stock_pct = val

    # Bottom line
    bottom_line = thesis_para or story_para or f'{ticker} reported {earnings_date} — see details below.'

    # Takeaways bullets — from thesis / follow-ups
    takeaways_bullets = extract_bullets(thesis, max_items=4) or followup_bullets[:4]

    # Sources
    sources = extract_urls(md, max_urls=4)

    # Next earnings (approx +90 days)
    try:
        ed = datetime.strptime(earnings_date, '%Y-%m-%d').date()
        next_ed = (ed + timedelta(days=90)).strftime('%Y-%m-%d')
        visible_until = (ed + timedelta(days=7)).strftime('%Y-%m-%d')
    except Exception:
        next_ed = None
        visible_until = None

    legacy_path = f'notes/post_earnings/{ticker}_{earnings_date}.md'

    # Take a rough signal scorecard — 2 signals, CONFIRMED if thesis_para is positive, FAILED if negative
    neg = bool(re.search(r'(miss|fell|declined|disappointed|cut|below|weak)', (thesis_para + reaction_para).lower()))
    pos = bool(re.search(r'(beat|strong|accelerat|raised|above|confirmed|inflect)', (thesis_para + reaction_para).lower()))

    signals = []
    if story_bullets:
        signals.append({
            'signal_id': 'headline_results',
            'label': 'Headline Results',
            'status': 'FAILED' if neg and not pos else 'CONFIRMED',
            'note': story_bullets[0][:220] if story_bullets else '',
            'watch_quarter': f'Q reported {earnings_date}',
        })
    if guide_bullets:
        signals.append({
            'signal_id': 'guidance_trajectory',
            'label': 'Guidance Trajectory',
            'status': 'FAILED' if neg else ('CONFIRMED' if pos else 'WATCHING'),
            'note': guide_bullets[0][:220] if guide_bullets else '',
            'watch_quarter': f'Q reported {earnings_date}',
        })

    return {
        'ticker': ticker,
        'company_name': name,
        'state': 'post_earnings',
        'inflection_status': 'POST',
        'last_earnings_date': earnings_date,
        'next_earnings_date': next_ed,
        'intel_updated_at': INTEL_TIMESTAMP,
        'refresh_reason': 'post_earnings_update',
        'bottom_line': bottom_line,
        'bull_case': {
            'thesis_headline': (story_para[:180] if pos else '') or 'Bull path requires consistent execution in next print.',
            'pattern': '',
            'pushes_higher': followup_bullets[:3] if followup_bullets else [],
            'pushes_lower': [],
        },
        'base_case': {
            'setup_headline': 'Thesis intact post-print; watch next quarter for signal confirmation.',
            'pushes_higher': [],
            'pushes_lower': [],
        },
        'bear_case': {
            'thesis_headline': (reaction_para[:180] if neg else '') or 'Bear path requires execution slip or macro shock.',
            'pattern': '',
            'pushes_higher': [],
            'pushes_lower': followup_bullets[-3:] if followup_bullets else [],
        },
        'signal_scorecard': signals,
        'guidance_profile': {
            'fy_guide_eps_low': None,
            'fy_guide_eps_high': None,
            'fy_guide_revenue_low': None,
            'fy_guide_revenue_high': None,
            'last_changed': earnings_date,
            'guide_style': None,
        },
        'tone_drift': {
            'current_tone': 'constructive' if pos and not neg else ('cautious' if neg else 'neutral'),
            'prior_tone': 'cautious',
            'tone_notes': guide_bullets[0][:220] if guide_bullets else 'Tone assessed from post-earnings language.',
        },
        'theme_lifecycle': [],
        'inflection_library': [],
        'source_metadata': {
            'primary_sources': sources,
            'legacy_note_path': legacy_path,
        },
        'post_earnings_review': {
            'active': True,
            'earnings_date': earnings_date,
            'visible_until': visible_until,
            'takeaways_headline': thesis_para[:180] if thesis_para else f'{ticker} {earnings_date} quarter — see takeaways below.',
            'takeaways_bullets': takeaways_bullets,
            'what_happened_headline': story_para[:180] if story_para else f'Quarter reported {earnings_date}.',
            'what_happened_bullets': story_bullets,
            'stock_reaction_pct': stock_pct,
        },
        'previous_bottom_line': None,
        'signal_changes': [],
    }


def main():
    tickers = {}

    # Process pre-earnings notes
    for fname in sorted(os.listdir(PRE_DIR)):
        if not fname.endswith('.md'):
            continue
        m = re.match(r'^([A-Z]+)_(\d{4}-\d{2}-\d{2})\.md$', fname)
        if not m:
            continue
        ticker, edate = m.group(1), m.group(2)
        md = read_note(os.path.join(PRE_DIR, fname))
        tickers[ticker] = derive_pre_earnings_record(ticker, edate, md)

    # Process post-earnings notes (overwrites if same ticker appears in both — post wins)
    for fname in sorted(os.listdir(POST_DIR)):
        if not fname.endswith('.md'):
            continue
        m = re.match(r'^([A-Z]+)_(\d{4}-\d{2}-\d{2})\.md$', fname)
        if not m:
            continue
        ticker, edate = m.group(1), m.group(2)
        md = read_note(os.path.join(POST_DIR, fname))
        tickers[ticker] = derive_post_earnings_record(ticker, edate, md)

    out = {
        'last_updated': datetime.now().astimezone().isoformat(timespec='seconds'),
        'schema_version': 1,
        'tickers': tickers,
    }

    with open(OUT_FILE, 'w') as f:
        json.dump(out, f, indent=2)

    print(f'Wrote {OUT_FILE}')
    print(f'Tickers: {len(tickers)}  ({sorted(tickers.keys())})')
    size = os.path.getsize(OUT_FILE)
    print(f'Size: {size:,} bytes')


if __name__ == '__main__':
    main()
