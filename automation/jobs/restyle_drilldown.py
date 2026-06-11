"""Post-hoc style upgrader for existing v2 drilldowns.

Applies FROG-grade style parity to notes that were rendered before the
style-parity lockdown, without requiring a full AI regen:

1. Replaces the <style> block with the current _STYLE from drilldown_assembler.
2. Upgrades kpi-value divs to carry kpi-positive / kpi-neutral / kpi-negative.
3. Applies pos/neg post-processor (from assembler) to prose text nodes.
4. Injects the deterministic beat/miss earnings table from snap data.

NOTE: quote-block requires a ceo_quote from the debate section (LLM output);
it will be absent on restyle runs.  Full regen remains the gold standard.
Run this only when full regen is blocked by API key / credit constraints.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.jobs.drilldown_assembler import (
    _STYLE,
    _apply_pos_neg,
    _kpi_direction_class,
    _render_sensitivity,
    _Citer,
)
from automation.jobs.generate_drilldown import (
    _build_beat_miss_table,
    _find_section_body_content,
    _load_snapshot,
)


_STYLE_RE = re.compile(
    r'<style>\n.*?</style>',
    re.DOTALL | re.IGNORECASE,
)

_KPI_VALUE_RE = re.compile(
    r'(<div class="kpi-value">)(.*?)(</div>)',
    re.DOTALL | re.IGNORECASE,
)


def _upgrade_kpi_values(html: str) -> str:
    """Upgrade <div class="kpi-value"> to include kpi-positive/neutral/negative."""
    def _replace(m: re.Match) -> str:
        content = m.group(2)
        # Extract plain text from content (strip tags)
        text = re.sub(r'<[^>]+>', '', content)
        cls = _kpi_direction_class(text.strip(), '')
        return f'<div class="kpi-value {cls}">{content}</div>'
    return _KPI_VALUE_RE.sub(_replace, html)


def _inject_beat_miss(html: str, ticker: str, snap: dict) -> str:
    """Inject a beat/miss table into the Earnings Setup section."""
    asum = (snap.get('analyst_summary') or {}).get(ticker, {})
    hist = asum.get('earningsHistory') or []
    beat_miss_table = _build_beat_miss_table(hist)
    if not beat_miss_table:
        return html

    section_title_re = re.compile(
        r'class="section-title"[^>]*>\s*(?:[^<]*'
        r'(?:earnings\s+setup|revision\s+debate|2-year\s+price|earnings\s+reactions)'
        r'[^<]*)\s*</',
        re.IGNORECASE,
    )
    title_m = section_title_re.search(html)
    if not title_m:
        return html

    content_start, body_close, section_close = _find_section_body_content(
        html, title_m.end()
    )
    if content_start == -1:
        return html

    if body_close == -1 or body_close > section_close:
        body_close = section_close

    body_content = html[content_start:body_close]
    # Remove any existing tables (old beat/miss tables from earlier restyle)
    body_content = re.sub(
        r'<table[^>]*style="margin-bottom:16px;".*?</table>\s*', '',
        body_content, flags=re.DOTALL | re.IGNORECASE,
    )
    new_body = beat_miss_table + body_content
    return html[:content_start] + new_body + html[body_close:]


def _replace_sens_table(html: str, meta: dict) -> str:
    """Replace the existing sensitivity table section body with a freshly
    rendered one that uses the new sens-mid-high class gradient."""
    section_title_re = re.compile(
        r'class="section-title"[^>]*>\s*[^<]*(?:sensitivity\s+table|implied\s+price|ev.?rev)[^<]*\s*</',
        re.IGNORECASE,
    )
    title_m = section_title_re.search(html)
    if not title_m:
        return html

    content_start, body_close, section_close = _find_section_body_content(
        html, title_m.end()
    )
    if content_start == -1:
        return html

    if body_close == -1 or body_close > section_close:
        body_close = section_close

    # Render the new sensitivity section body (just the body content)
    citer = _Citer(start=9999)  # high start to avoid claim id collisions
    new_section_html = _render_sensitivity(meta, citer)
    # Extract just the section-body content from the newly rendered section
    new_content_m = re.search(r'<div class="section-body">(.*?)</div>\s*</section>',
                              new_section_html, re.DOTALL)
    if not new_content_m:
        return html
    new_body = new_content_m.group(1)
    return html[:content_start] + new_body + html[body_close:]


def restyle(path: Path, snap: dict) -> str:
    """Apply FROG-grade style upgrades to a drilldown note file."""
    content = path.read_text(encoding='utf-8')

    # Split frontmatter from HTML
    if content.startswith('---'):
        end = content.find('\n---\n', 3)
        if end != -1:
            frontmatter = content[:end + 5]
            html = content[end + 5:]
        else:
            frontmatter = ''
            html = content
    else:
        frontmatter = ''
        html = content

    ticker = ''
    price_at_gen = None
    for line in (frontmatter or '').splitlines():
        if line.startswith('ticker:'):
            ticker = line.split(':', 1)[1].strip()
        if line.startswith('price_at_generation:'):
            try:
                price_at_gen = float(line.split(':', 1)[1].strip())
            except (ValueError, TypeError):
                pass

    # 1. Replace style block
    new_style = f'<style>\n{_STYLE}\n</style>'
    html = _STYLE_RE.sub(new_style, html)

    # 2. Upgrade kpi-value classes
    html = _upgrade_kpi_values(html)

    # 3. Strip banned compliance phrase from meta-line and footer.
    # The current assembler does not emit this phrase; older notes may carry it.
    # Handle both the &nbsp; format and the bare <div> format.
    html = re.sub(
        r'\s*&nbsp;&middot;&nbsp;\s*For [Ii]nstitutional [Uu]se [Oo]nly',
        '',
        html,
    )
    html = re.sub(
        r'<div>\s*For [Ii]nstitutional [Uu]se [Oo]nly\s*</div>',
        '',
        html,
    )

    # 4. Apply pos/neg post-processor
    html = _apply_pos_neg(html)

    # 5. Regenerate the sensitivity table with new sens-mid-high gradient.
    if price_at_gen:
        meta = {'price': price_at_gen}
        html = _replace_sens_table(html, meta)

    # 6. Fix sub-$1B revenue formatting: $0.XXB -> $XXXm
    def _fix_sub_billion(m: re.Match) -> str:
        val = float(m.group(1))
        millions = round(val * 1000)
        return f'${millions}m'
    html = re.sub(
        r'\$0\.([0-9]{2})B\b',
        lambda m: f'${round(float("0." + m.group(1)) * 1000)}m',
        html,
    )

    # 7. Inject beat/miss table
    if ticker:
        html = _inject_beat_miss(html, ticker, snap)

    return frontmatter + html


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Restyle a drilldown note for FROG parity.')
    parser.add_argument('paths', nargs='+', help='Paths to drilldown .md files')
    args = parser.parse_args(argv)

    snap = _load_snapshot()

    for p in args.paths:
        path = Path(p)
        if not path.exists():
            print(f'  [SKIP] {p}: not found')
            continue
        print(f'  [RESTYLE] {path.name}...')
        upgraded = restyle(path, snap)
        path.write_text(upgraded, encoding='utf-8')
        print(f'  [DONE] {path.name}')


if __name__ == '__main__':
    raise SystemExit(main())
