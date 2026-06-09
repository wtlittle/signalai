#!/usr/bin/env python3
"""One-step institutional drilldown generator.

Server-side mirror of the client flow in drilldown-surface.js: build the
[SIGNAL_DATA_BLOCK] from data-snapshot.json, splice it into the canonical
drilldown_prompt.md template, call Perplexity (sonar-deep-research) via the
single shared client, extract the returned HTML, and save it through
save_drilldown.save_drilldown() so the on-disk markdown + notes/drilldown/index.json
manifest stay the source of truth the dashboard hydrates from.

Usage:
    python3 -m automation.jobs.generate_drilldown --tickers NET NVDA RBRK
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / 'data-snapshot.json'
PROMPT_PATH = ROOT / 'drilldown_prompt.md'

from automation.perplexity.client import call_perplexity
from automation.jobs.save_drilldown import save_drilldown


def _fmt(v, decimals=None):
    if v is None:
        return 'MISSING'
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return str(v)
    if fv != fv:  # NaN
        return 'MISSING'
    return f'{fv:.{decimals}f}' if decimals is not None else str(v)


def _fmt_pct(v):
    """Format a fraction (0.335) as a percent. Use for fraction-valued fields."""
    if v is None:
        return 'MISSING'
    try:
        return f'{float(v) * 100:.1f}%'
    except (TypeError, ValueError):
        return 'MISSING'


def _fmt_pct_raw(v):
    """Format an already-percent value (33.5 -> 33.5%).

    data-snapshot.json stores revenueGrowth, margins, growth estimates, and
    fcfMargin in whole-percent units, unlike the fraction-valued fields the
    client receives post-normalization. Append '%' without rescaling.
    """
    if v is None:
        return 'MISSING'
    try:
        return f'{float(v):.1f}%'
    except (TypeError, ValueError):
        return 'MISSING'


def _fmt_b(v):
    if v is None:
        return 'MISSING'
    try:
        return f'${float(v) / 1e9:.2f}B'
    except (TypeError, ValueError):
        return 'MISSING'


def _load_snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))


def build_signal_data_block(ticker, snap):
    """Replicate drilldown-surface.js _buildSignalDataBlock from snapshot data."""
    q = (snap.get('quotes') or {}).get(ticker) or {}
    est = (snap.get('estimates') or {}).get(ticker) or {}
    asum = (snap.get('analyst_summary') or {}).get(ticker) or {}
    hist = (asum.get('earningsHistory') or [])[-8:]
    comps_row = (snap.get('cross_sector_comps') or {}).get(ticker) or {}
    mi = (snap.get('market_intel') or {}).get(ticker) if isinstance(snap.get('market_intel'), dict) else None

    if not q:
        return ('[SIGNAL_DATA_BLOCK]\n(No pre-fetched data available '
                '— collect all fields from search tools.)\n')

    lines = [
        '[SIGNAL_DATA_BLOCK]',
        'Generated: ' + _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        '',
        '## QUOTE',
        'Ticker: ' + ticker,
        'Price: ' + _fmt(q.get('price'), 2),
        'MarketCap: ' + _fmt_b(q.get('marketCap')),
        'EnterpriseValue: ' + _fmt_b(q.get('enterpriseValue')),
        'Sector: ' + (q.get('sector') or 'MISSING'),
        'Industry: ' + (q.get('industry') or 'MISSING'),
        '52wHigh: ' + _fmt(q.get('fiftyTwoWeekHigh'), 2),
        '52wLow: ' + _fmt(q.get('fiftyTwoWeekLow'), 2),
        'Beta: ' + _fmt(q.get('beta'), 2),
        'ForwardPE: ' + _fmt(q.get('forwardPE'), 1),
        'TrailingPE: ' + _fmt(q.get('trailingPE'), 1),
        'EVRevenue: ' + _fmt(q.get('enterpriseToRevenue'), 2),
        'EVEBITDA: ' + _fmt(q.get('enterpriseToEbitda'), 2),
        'RevenueGrowth: ' + _fmt_pct_raw(q.get('revenueGrowth')),
        'GrossMargin: ' + _fmt_pct_raw(q.get('grossMargins') if q.get('grossMargins') is not None else est.get('grossMargins')),
        'OperatingMargin: ' + _fmt_pct_raw(q.get('operatingMargins')),
        'FCF: ' + _fmt_b(q.get('freeCashflow')),
        'TotalRevenue: ' + _fmt_b(q.get('totalRevenue')),
        'ConsensusTarget: ' + _fmt(q.get('targetMeanPrice') or asum.get('targetMeanPrice'), 2),
        'TargetHigh: ' + _fmt(q.get('targetHighPrice') or asum.get('targetHighPrice'), 2),
        'TargetLow: ' + _fmt(q.get('targetLowPrice') or asum.get('targetLowPrice'), 2),
        'ConsensusRating: ' + (q.get('recommendationKey') or asum.get('recommendationKey') or 'MISSING'),
        'AnalystCount: ' + _fmt(q.get('numberOfAnalystOpinions') or asum.get('numberOfAnalystOpinions')),
        '',
        '## ESTIMATES',
        'NQ_RevEst: ' + _fmt_b(est.get('nextQRevEst')),
        'NQ_RevGrowth: ' + _fmt_pct_raw(est.get('nextQRevGrowth')),
        'NQ_EpsEst: ' + _fmt(est.get('nextQEpsEst'), 2),
        'NQ_EpsGrowth: ' + _fmt_pct_raw(est.get('nextQEpsGrowth')),
        'FY1_RevEst: ' + _fmt_b(est.get('fy1RevEst')),
        'FY1_RevGrowth: ' + _fmt_pct_raw(est.get('fy1RevGrowth')),
        'FY1_EpsEst: ' + _fmt(est.get('fy1EpsEst'), 2),
        'FY2_RevEst: ' + _fmt_b(est.get('fy2RevEst')),
        'FY2_RevGrowth: ' + _fmt_pct_raw(est.get('fy2RevGrowth')),
        'FY2_EpsEst: ' + _fmt(est.get('fy2EpsEst'), 2),
        'GuideRevHigh: ' + _fmt_b(est.get('guideRevHigh')),
        'GuideRevLow: ' + _fmt_b(est.get('guideRevLow')),
        'EPSTrend_Now: ' + _fmt(est.get('epsTrendCurrent'), 2),
        'EPSTrend_30d: ' + _fmt(est.get('epsTrend30d'), 2),
        'EPSTrend_90d: ' + _fmt(est.get('epsTrend90d'), 2),
        'RevisionsUp_30d: ' + _fmt(est.get('revisionsUp30d')),
        'RevisionsDown_30d: ' + _fmt(est.get('revisionsDown30d')),
        'FCFMargin: ' + _fmt_pct_raw(est.get('fcfMargin')),
        'RevenueLTM: ' + _fmt_b(est.get('revenueLtm')),
        '',
        '## EARNINGS HISTORY (last 8 quarters)',
    ]

    if hist:
        lines.append('Quarter | Rev Beat% | EPS Beat% | 1d Move | GuidanceTone')
        for h in hist:
            rev = h.get('revBeatPct')
            eps = h.get('epsBeatPct')
            mv = h.get('oneDayReturn')
            lines.append(
                (h.get('period') or '?') + ' | ' +
                (f'{rev:.1f}%' if rev is not None else 'MISSING') + ' | ' +
                (f'{eps:.1f}%' if eps is not None else 'MISSING') + ' | ' +
                (f'{mv * 100:.1f}%' if mv is not None else 'MISSING') + ' | ' +
                (h.get('guidanceTone') or 'MISSING')
            )
    else:
        lines.append('(No earnings history cached — source from search tools)')

    lines += ['', '## CROSS-SECTOR COMPS']
    peers = comps_row.get('comps') or []
    if peers:
        lines.append('Ticker | EVRev | PEFwd | OpMargin | FCFMargin | RevGrowth')
        for c in peers:
            lines.append(
                (c.get('ticker') or '?') + ' | ' +
                _fmt(c.get('enterpriseToRevenue'), 2) + 'x | ' +
                _fmt(c.get('forwardPE'), 1) + 'x | ' +
                _fmt_pct_raw(c.get('operatingMargins')) + ' | ' +
                _fmt_pct_raw(c.get('fcfMargin')) + ' | ' +
                _fmt_pct_raw(c.get('revenueGrowth'))
            )
    else:
        lines.append('(No comps cached — source from search tools)')

    lines += ['', '## MARKET INTEL']
    if mi:
        lines.append('TAM: ' + (mi.get('tam_label') or 'MISSING'))
        lines.append('TAMSource: ' + (mi.get('tam_source') or 'MISSING'))
        lines.append('CategoryGrowthRate: ' + (mi.get('growth_rate_label') or 'MISSING'))
        lines.append('HarvestedAt: ' + (mi.get('harvested_at') or 'MISSING'))
        lines.append('StructuralDrivers: ' + (mi.get('structural_drivers') or 'MISSING'))
    else:
        lines.append('(MISSING — model must source TAM and category growth from search tools)')

    lines += ['', '[/SIGNAL_DATA_BLOCK]']
    return '\n'.join(lines)


def _extract_html(content):
    """Pull the HTML document out of the model response.

    The canonical prompt asks for a single fenced ```html block. Be lenient:
    accept a fenced block, or fall back to the first <!DOCTYPE/<html ... </html>.
    """
    if not isinstance(content, str):
        # call_perplexity returns parsed JSON; deep-research HTML usually lands
        # under {"raw": "..."} because it is not JSON.
        if isinstance(content, dict):
            content = content.get('raw') or content.get('html') or json.dumps(content)
        else:
            content = str(content)

    # sonar-deep-research prepends an internal <think>...</think> chain-of-thought
    # before the document. Strip it so it never leaks into the saved note.
    content = re.sub(r'<think>.*?</think>\s*', '', content, flags=re.DOTALL | re.IGNORECASE)

    m = re.search(r'```html\s*(.*?)```', content, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'```\s*(<!DOCTYPE.*?|<html.*?)```', content, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'(<!DOCTYPE html.*?</html>)', content, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'(<html[\s\S]*?</html>)', content, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return content.strip()


def generate_one(ticker, snap, prompt_template, company_name=None):
    ticker = ticker.strip().upper()
    data_block = build_signal_data_block(ticker, snap)
    prompt_body = prompt_template.replace('[SIGNAL_DATA_BLOCK]', data_block)
    header = (
        f'Run the canonical Signal Stack institutional drilldown engine on {ticker}. '
        'Use the prompt below verbatim. The [SIGNAL_DATA_BLOCK] has been pre-filled '
        'with live data — treat it as ground truth. Output ONE complete '
        'self-contained HTML document covering all 14 sections inside a single '
        'fenced ```html block, and nothing else.\n\n'
    )
    full_prompt = header + prompt_body

    system = (
        'You are Signal Stack AI\'s institutional Drilldown engine. Output a single '
        'self-contained HTML document inside one fenced ```html block. No prose outside '
        'the block. No <script> tags.'
    )

    # A complete 14-section institutional primer (~3,500-6,000 words of HTML)
    # plus the model's reasoning tokens needs ample completion headroom; 16k
    # truncated NET/NVDA mid-document. Default high, allow env override.
    max_tokens = int(os.environ.get('DRILLDOWN_MAX_TOKENS', '32000'))
    print(f'\n=== {ticker}: calling Perplexity deep research (max_tokens={max_tokens}) ===')
    result = call_perplexity(
        ticker=ticker,
        task='drilldown',
        prompt=full_prompt,
        system=system,
        force=True,
        max_tokens=max_tokens,
        temperature=0.2,
        extra_meta={
            'model': 'sonar-deep-research',
            # 'low' keeps reasoning-token spend bounded so the completion budget
            # is spent on the document. 'medium' burned ~196k reasoning tokens and
            # truncated the HTML before </html>.
            'reasoning_effort': os.environ.get('DRILLDOWN_REASONING_EFFORT', 'low'),
        },
    )

    # Surface queue / skip / error states instead of fabricating output.
    if isinstance(result, dict):
        if result.get('queued'):
            raise RuntimeError(
                f'{ticker}: call was QUEUED rather than executed. The Perplexity client '
                'routed to the Computer queue (USE_PPLX_API not enabled / no key). '
                'Aborting so we do not save a placeholder.'
            )
        if result.get('skipped'):
            raise RuntimeError(f'{ticker}: Perplexity call skipped: {result.get("reason")}')
        if result.get('dry_run'):
            raise RuntimeError(f'{ticker}: DRY_RUN set; no content generated.')

    html = _extract_html(result)
    if not html or len(html) < 1000 or '<' not in html:
        raise RuntimeError(
            f'{ticker}: model returned no usable HTML (len={len(html) if html else 0}). '
            f'First 300 chars: {str(result)[:300]!r}'
        )
    if '</html>' not in html.lower():
        raise RuntimeError(
            f'{ticker}: HTML appears truncated (no closing </html>; len={len(html)}). '
            'Likely hit the completion-token cap. Raise DRILLDOWN_MAX_TOKENS and retry; '
            'not saving a partial note.'
        )

    q = (snap.get('quotes') or {}).get(ticker) or {}
    price = q.get('price')
    company = company_name or q.get('longName') or ticker
    title = f'{company} ({ticker}) — Institutional Drilldown'

    md_path, entry = save_drilldown(
        ticker=ticker,
        html=html,
        part='p1',
        trigger='Deep Research',
        title=title,
        html_path=None,
        price_at_gen=price,
    )
    print(f'  Saved {md_path.relative_to(ROOT)} ({entry["size_bytes"]} bytes, '
          f'{entry["word_count"]} words)')
    return md_path, entry


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate institutional drilldowns.')
    parser.add_argument('--tickers', nargs='+', required=True)
    args = parser.parse_args(argv)

    snap = _load_snapshot()
    prompt_template = PROMPT_PATH.read_text(encoding='utf-8')

    results = []
    errors = []
    for t in args.tickers:
        try:
            md_path, entry = generate_one(t, snap, prompt_template)
            results.append(entry)
        except Exception as exc:
            print(f'  [ERROR] {t}: {exc}', file=sys.stderr)
            errors.append((t, str(exc)))

    print('\n=== SUMMARY ===')
    for e in results:
        print(f'  OK   {e["ticker"]}: {e["markdown_path"]} '
              f'({e["word_count"]} words)')
    for t, msg in errors:
        print(f'  FAIL {t}: {msg}')

    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
