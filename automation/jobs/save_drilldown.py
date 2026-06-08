#!/usr/bin/env python3
"""Save an institutional drilldown note to the on-disk library.

Mirrors the canonical writer in backend.py's POST /drilldown/save handler:
writes notes/drilldown/<TICKER>_<DATE>_<PART>.md with YAML frontmatter and the
raw HTML embedded verbatim, then updates notes/drilldown/index.json (the manifest
the dashboard hydrates from so committed notes appear on every device).

Usage:
    python3 -m automation.jobs.save_drilldown \
        --ticker FROG \
        --date 2026-06-08 \
        --part p1 \
        --html-file drilldown_data/FROG_drilldown_v1.html \
        --title "JFrog (FROG) — Institutional Drilldown"
"""
import argparse
import datetime as _dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = ROOT / 'notes' / 'drilldown'
MANIFEST_PATH = NOTES_DIR / 'index.json'

VALID_PARTS = ('p1', 'p2', 'merged')


def _word_count(html):
    text = re.sub(r'<script[\s\S]*?</script>', ' ', html, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return len(text.split()) if text else 0


def write_markdown(ticker, part, date_str, html, trigger, price_at_gen=None):
    """Write the markdown file exactly as backend.py's writer does."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().astimezone().isoformat(timespec='seconds')
    md_filename = f'{ticker}_{date_str}_{part}.md'
    md_path = NOTES_DIR / md_filename
    md_body = (
        f'---\n'
        f'ticker: {ticker}\n'
        f'part: {part}\n'
        f'trigger: {trigger}\n'
        f'generated_at: {stamp}\n'
        + (f'price_at_generation: {price_at_gen}\n' if price_at_gen is not None else '')
        + f'---\n\n{html}\n'
    )
    md_path.write_text(md_body, encoding='utf-8')
    return md_path, md_filename, stamp


def update_manifest(entry):
    """Append-or-replace a manifest entry by id, then write index.json back."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {'generated_at_utc': None, 'drilldowns': []}
    if MANIFEST_PATH.exists():
        try:
            loaded = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
            if isinstance(loaded, dict) and isinstance(loaded.get('drilldowns'), list):
                manifest = loaded
        except (json.JSONDecodeError, OSError):
            pass

    drilldowns = [d for d in manifest['drilldowns'] if d.get('id') != entry['id']]
    drilldowns.append(entry)
    drilldowns.sort(key=lambda d: (d.get('date', ''), d.get('ticker', ''), d.get('part', '')))
    manifest['drilldowns'] = drilldowns
    manifest['generated_at_utc'] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def save_drilldown(ticker, html, part='p1', date_str=None, trigger='Deep Research',
                   title=None, html_path=None, price_at_gen=None):
    ticker = ticker.strip().upper()
    part = part.strip().lower()
    if part not in VALID_PARTS:
        part = 'p1'
    if not date_str:
        date_str = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d')
    if not title:
        title = f'{ticker} — Institutional Drilldown'

    md_path, md_filename, stamp = write_markdown(
        ticker, part, date_str, html, trigger, price_at_gen
    )

    saved_at_utc = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    entry = {
        'id': f'{ticker}-{date_str}-{part}',
        'ticker': ticker,
        'date': date_str,
        'part': part,
        'title': title,
        'trigger': trigger,
        'markdown_path': f'notes/drilldown/{md_filename}',
        'html_path': html_path,
        'size_bytes': len(html.encode('utf-8')),
        'word_count': _word_count(html),
        'saved_at_utc': saved_at_utc,
    }
    update_manifest(entry)

    # TODO: dual-write to the Supabase `drilldown_intel` table once it exists.
    # The table currently 404s, so manifest-based hydration is the source of
    # truth. Columns expected by backend.py's upsert:
    #   ticker, part, date, html, trigger, price_at_generation,
    #   generated_at, markdown_path

    return md_path, entry


def main(argv=None):
    parser = argparse.ArgumentParser(description='Save a drilldown note to the on-disk library.')
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--date', default=None, help='YYYY-MM-DD (default: today UTC)')
    parser.add_argument('--part', default='p1', choices=list(VALID_PARTS))
    parser.add_argument('--html-file', required=True)
    parser.add_argument('--trigger', default='Deep Research')
    parser.add_argument('--title', default=None, help='auto-constructed from ticker if missing')
    parser.add_argument('--price-at-generation', default=None, type=float)
    args = parser.parse_args(argv)

    html_file = Path(args.html_file)
    if not html_file.is_absolute():
        html_file = (ROOT / html_file).resolve()
    if not html_file.exists():
        parser.error(f'HTML file not found: {html_file}')
    html = html_file.read_text(encoding='utf-8')

    # Record html_path relative to repo root when the file lives inside it.
    try:
        html_path = str(html_file.relative_to(ROOT))
    except ValueError:
        html_path = str(html_file)

    md_path, entry = save_drilldown(
        ticker=args.ticker,
        html=html,
        part=args.part,
        date_str=args.date,
        trigger=args.trigger,
        title=args.title,
        html_path=html_path,
        price_at_gen=args.price_at_generation,
    )

    print(f'Wrote {md_path.relative_to(ROOT)}')
    print(f'Manifest entry: {json.dumps(entry, indent=2)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
