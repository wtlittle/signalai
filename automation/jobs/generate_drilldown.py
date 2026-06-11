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
import html as _html
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / 'data-snapshot.json'
INTEL_PATH = ROOT / 'earnings_intel.json'
PROMPT_PATH = ROOT / 'drilldown_prompt.md'

from automation.perplexity.client import call_perplexity
from automation.jobs.save_drilldown import save_drilldown
from automation.jobs.drilldown_validator import validate
from automation.jobs.drilldown_renderer import (
    render_bull_base_bear,
    validate_payload,
    BBBValidationError,
)
from automation.sources.annual_history import fetch_5yr_history
from automation.shared.supabase_client import fetch_rows
from automation.jobs.drilldown_chart import (
    render_earnings_annotated_chart,
    build_earnings_events,
)

# Supabase table holding per-ticker TAM / competitor intel harvested by
# automation/jobs/market_intel_refresh.py (90-day TTL). Distinct from the
# subsector-keyed `market_intel` table the legacy harvester writes.
_MARKET_INTEL_TABLE = "market_intel_ticker"


def fetch_market_intel(ticker: str) -> dict | None:
    """Pull the per-ticker market-intel row from Supabase, or None.

    Returns the row dict (tam_usd_bn, tam_source_url, category_cagr_pct,
    drivers[], competitors[], updated_at) so build_signal_data_block can inject
    it as the `market_intel` key. Never raises -- a missing table or row yields
    None and the block falls back to "model must source from search tools".
    """
    ticker = ticker.strip().upper()
    rows = fetch_rows(
        _MARKET_INTEL_TABLE,
        params={"ticker": f"eq.{ticker}", "limit": "1"},
    )
    return rows[0] if rows else None


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


def format_currency(value, *, allow_millions=True):
    """Format a USD value with tier-appropriate units.
    <$1B  -> "$XXXm" (no decimals)
    $1-10B -> "$X.XXB" (two decimals)
    >=$10B -> "$XX.XB" (one decimal)
    None / missing -> "n/a"
    """
    if value is None:
        return 'n/a'
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 'n/a'
    abs_v = abs(v)
    if abs_v < 1_000_000_000 and allow_millions:
        return f'${v/1_000_000:.0f}m'
    if abs_v < 10_000_000_000:
        return f'${v/1_000_000_000:.2f}B'
    return f'${v/1_000_000_000:.1f}B'


def _fmt_b(v):
    """Format a USD value using tier-appropriate units; 'MISSING' when null."""
    if v is None:
        return 'MISSING'
    result = format_currency(v)
    return result if result != 'n/a' else 'MISSING'


def _dash(v):
    """Render a value for the NEW data-block sections (history_5y / market_intel).

    Per the zero-fabrication mandate: a null/empty value renders as an em-dash,
    never the literal string "MISSING".
    """
    if v is None or v == '':
        return '—'
    return str(v)


def _dash_b(v):
    """Format a USD value using tier-appropriate units, or em-dash when null."""
    if v is None:
        return '—'
    result = format_currency(v)
    return result if result != 'n/a' else '—'


def _dash_num(v, decimals=2):
    if v is None:
        return '—'
    try:
        return f'{float(v):.{decimals}f}'
    except (TypeError, ValueError):
        return '—'


def _load_snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))


def build_signal_data_block(ticker, snap, history_5y=None, market_intel=None):
    """Replicate drilldown-surface.js _buildSignalDataBlock from snapshot data.

    ``history_5y`` (list of annual rows from fetch_5yr_history) and
    ``market_intel`` (per-ticker Supabase row from fetch_market_intel) are
    injected as their own sections when supplied. Both are sourced from live
    data and treated as ground truth by the model.
    """
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
        lines.append('Quarter | EPS Actual | EPS Est | EPS Beat% | Rev Beat% | 1d Move | GuidanceTone')
        for h in hist:
            rev = h.get('revBeatPct')
            # EPS beat: prefer an explicit beat%, else the snapshot's
            # surprisePercent, else compute from actual vs estimate. Finnhub
            # rows carry epsActual/epsEstimate/surprisePercent (not epsBeatPct),
            # so without this fallback the latest print rendered as MISSING and
            # the model never saw the concrete actual-vs-estimate beat.
            eps = h.get('epsBeatPct')
            act = h.get('epsActual')
            est_eps = h.get('epsEstimate')
            if eps is None and act is not None and est_eps not in (None, 0):
                try:
                    eps = (act - est_eps) / abs(est_eps) * 100.0
                except (TypeError, ZeroDivisionError):
                    eps = None
            if eps is None and h.get('surprisePercent') is not None:
                # Finnhub surprisePercent is a fraction (0.0752 == 7.52%).
                eps = h['surprisePercent'] * 100.0
            mv = h.get('oneDayReturn')
            lines.append(
                (h.get('period') or '?') + ' | ' +
                (f'{act:.2f}' if act is not None else 'MISSING') + ' | ' +
                (f'{est_eps:.2f}' if est_eps is not None else 'MISSING') + ' | ' +
                (f'{eps:.1f}%' if eps is not None else 'MISSING') + ' | ' +
                (f'{rev:.1f}%' if rev is not None else 'MISSING') + ' | ' +
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

    lines += ['', '## 5-YEAR ANNUAL HISTORY']
    if history_5y:
        lines.append('FiscalYear | Revenue | OperatingIncome | EBIT | NetIncome | '
                     'DilutedEPS | FCF | DilutedShares  (em-dash = not reported; '
                     'never substitute estimates)')
        for row in history_5y:
            lines.append(
                _dash(row.get('fy')) + ' | ' +
                _dash_b(row.get('revenue')) + ' | ' +
                _dash_b(row.get('operating_income')) + ' | ' +
                _dash_b(row.get('ebit')) + ' | ' +
                _dash_b(row.get('net_income')) + ' | ' +
                _dash_num(row.get('eps_diluted'), 2) + ' | ' +
                _dash_b(row.get('fcf')) + ' | ' +
                _dash_num(row.get('shares_diluted'), 0)
            )
        # Per-cell provenance so the model can attribute figures and we can audit.
        prov = []
        for row in history_5y:
            tags = sorted({
                v for k, v in row.items()
                if k.endswith('_source') and v
            })
            if tags:
                prov.append(f"{row.get('fy')}={','.join(tags)}")
        if prov:
            lines.append('Provenance: ' + '; '.join(prov))
    else:
        lines.append('(No 5-year history assembled — source FY-4..FY annual '
                     'revenue/operating income/EBIT/net income/diluted EPS/FCF/'
                     'diluted shares from search tools; show em-dash for any cell '
                     'you cannot verify.)')

    lines += ['', '## MARKET INTEL (per-ticker, Supabase market_intel_ticker)']
    if market_intel:
        lines.append('TAM_USD_Bn: ' + _dash_num(market_intel.get('tam_usd_bn'), 1))
        lines.append('TAM_SourceURL: ' + _dash(market_intel.get('tam_source_url')))
        lines.append('Category_CAGR_Pct: ' + _dash_num(market_intel.get('category_cagr_pct'), 1))
        lines.append('UpdatedAt: ' + _dash(market_intel.get('updated_at')))
        drivers = market_intel.get('drivers') or []
        if isinstance(drivers, str):
            try:
                drivers = json.loads(drivers)
            except (ValueError, TypeError):
                drivers = [drivers]
        lines.append('Drivers:')
        if drivers:
            for d in drivers:
                lines.append('  - ' + _dash(d))
        else:
            lines.append('  —')
        competitors = market_intel.get('competitors') or []
        if isinstance(competitors, str):
            try:
                competitors = json.loads(competitors)
            except (ValueError, TypeError):
                competitors = []
        lines.append('Competitors (name | ticker | quadrant | threat | source):')
        if competitors:
            for c in competitors:
                if not isinstance(c, dict):
                    continue
                lines.append(
                    '  ' + _dash(c.get('name')) + ' | ' +
                    _dash(c.get('ticker')) + ' | ' +
                    _dash(c.get('quadrant')) + ' | ' +
                    _dash(c.get('threat')) + ' | ' +
                    _dash(c.get('source_url'))
                )
        else:
            lines.append('  —')
    else:
        lines.append('(No per-ticker market intel cached — source TAM, category '
                     'CAGR, growth drivers, and the competitive quadrant from '
                     'search tools.)')

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


def _call_model(ticker, full_prompt, system):
    """Single Perplexity deep-research call returning extracted, validated HTML.

    Raises RuntimeError on queue/skip/dry-run states or unusable/truncated HTML.
    """
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
    return html


def _find_section_body_content(html: str, search_start: int
                               ) -> tuple[int, int, int]:
    """Locate the section-body open tag and the end of the section.

    Searches from *search_start* forward for the pattern::

        <div … class="section-body" …>
          … content …
        </div>   ← closing div for the section-body
    </section>

    Returns (content_start, section_body_close, section_close) as absolute
    positions in *html*, or (-1, -1, -1) if the section-body cannot be found.

    *content_start* is the index of the first character of section-body content
    (immediately after the opening ``>`` of the ``<div class="section-body">`` tag).
    *section_body_close* is the start of ``</div>`` that closes section-body.
    *section_close* is the start of ``</section>`` that closes the section.
    """
    body_re = re.compile(r'<div[^>]*class=["\']section-body["\'][^>]*>', re.IGNORECASE)
    bm = body_re.search(html, search_start)
    if not bm:
        return -1, -1, -1

    content_start = bm.end()

    # Find the </div> that closes the section-body by tracking div depth.
    pos = content_start
    depth = 1
    div_open  = re.compile(r'<div[\s>]', re.IGNORECASE)
    div_close = re.compile(r'</div>', re.IGNORECASE)
    section_close_re = re.compile(r'</section>', re.IGNORECASE)
    body_close = -1
    while pos < len(html) and depth > 0:
        o = div_open.search(html, pos)
        c = div_close.search(html, pos)
        if c is None:
            break
        if o and o.start() < c.start():
            depth += 1
            pos = o.start() + 1
        else:
            depth -= 1
            if depth == 0:
                body_close = c.start()
            pos = c.start() + 1

    sc = section_close_re.search(html, bm.start())
    section_close = sc.start() if sc else len(html)
    return content_start, body_close, section_close


def _load_intel_record(ticker: str) -> dict:
    """Return the earnings_intel.json record for `ticker` (empty dict if absent)."""
    try:
        data = json.loads(INTEL_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return (data.get('tickers') or {}).get(ticker) or {}


def _earnings_events(ticker: str, hist: list[dict], intel: dict) -> list[dict]:
    """Build chart marker events with actual print dates, newest-aligned.

    Delegates to drilldown_chart.build_earnings_events (the single source of
    truth). Each event carries a `real` flag — True only when the print date
    came from an actual field or a measured reporting lag, False when only the
    fiscal-period-end is known. The chart never labels a fallback date as a
    print date and omits the chart entirely when no real date exists.
    """
    snap = {'analyst_summary': {ticker: {'earningsHistory': hist}}}
    return build_earnings_events(ticker, snap=snap, intel=intel)


def _build_beat_miss_table(hist: list[dict]) -> str:
    """Build a deterministic earnings-history table with beat/miss coloring.

    Each row applies class="beat" (green) or class="miss" (red) to the
    Rev Beat/Miss and EPS result cells, matching FROG's earnings-section
    style.  Returns an HTML table string, or empty string when hist is empty.
    """
    def _bm_fmt_num(v, decimals=1, prefix=''):
        if v is None:
            return '&mdash;'
        try:
            return prefix + f'{float(v):,.{decimals}f}'
        except (TypeError, ValueError):
            return _html.escape(str(v))

    def _beat_miss_class(val):
        if val is None:
            return '', '&mdash;'
        try:
            f = float(val)
            if f >= 0:
                return 'beat', f'+{f:.1f}%'
            else:
                return 'miss', f'{f:.1f}%'
        except (TypeError, ValueError):
            return '', '&mdash;'

    if not hist:
        return ''
    rows = []
    for h in hist[-8:]:  # most recent 8 quarters
        period = h.get('period') or '?'
        act_rev = h.get('revActual')
        est_rev = h.get('revEstimate')
        rev_beat = h.get('revBeatPct')
        act_eps = h.get('epsActual')
        est_eps_val = h.get('epsEstimate')
        eps_beat = h.get('epsBeatPct')
        # fallback: compute eps beat from actual vs estimate
        if eps_beat is None and act_eps is not None and est_eps_val not in (None, 0):
            try:
                eps_beat = (act_eps - est_eps_val) / abs(est_eps_val) * 100.0
            except (TypeError, ZeroDivisionError):
                pass
        if eps_beat is None and h.get('surprisePercent') is not None:
            eps_beat = h['surprisePercent'] * 100.0
        mv = h.get('oneDayReturn')
        guidance = h.get('guidanceTone') or '&mdash;'

        rev_cls, rev_str = _beat_miss_class(rev_beat)
        eps_cls, eps_str = _beat_miss_class(eps_beat)
        try:
            mv_cls = 'pos' if mv is not None and float(mv) >= 0 else 'neg'
            mv_str = f'{float(mv)*100:+.1f}%' if mv is not None else '&mdash;'
        except (TypeError, ValueError):
            mv_cls = 'pos'
            mv_str = '&mdash;'

        rev_cell = f'<span class="{rev_cls}">{rev_str}</span>' if rev_cls else rev_str
        eps_label = 'Beat' if eps_cls == 'beat' else 'Miss'
        eps_cell = (
            f'<span class="{eps_cls}">{eps_label}</span>'
            if eps_cls else '&mdash;'
        )
        mv_cell = f'<span class="{mv_cls}">{mv_str}</span>'

        rows.append(
            '        <tr>'
            f'<td>{_html.escape(period)}</td>'
            f'<td class="num">{_bm_fmt_num(act_rev, 0, "$") if act_rev is not None else "&mdash;"}</td>'
            f'<td class="num">{_bm_fmt_num(est_rev, 0, "$") if est_rev is not None else "&mdash;"}</td>'
            f'<td class="num">{rev_cell}</td>'
            f'<td class="num">{_bm_fmt_num(act_eps, 2)}</td>'
            f'<td class="num">{eps_cell}</td>'
            f'<td class="num">{mv_cell}</td>'
            f'<td>{_html.escape(str(guidance))}</td>'
            '</tr>'
        )
    if not rows:
        return ''
    header = (
        '    <table style="margin-bottom:16px;">\n'
        '      <thead>\n'
        '        <tr>\n'
        '          <th>Quarter</th>\n'
        '          <th class="num">Actual Rev</th>\n'
        '          <th class="num">Consensus Est.</th>\n'
        '          <th class="num">Rev Beat/Miss</th>\n'
        '          <th class="num">EPS Actual</th>\n'
        '          <th class="num">EPS Result</th>\n'
        '          <th class="num">1D Stock Move</th>\n'
        '          <th>Guidance Tone</th>\n'
        '        </tr>\n'
        '      </thead>\n'
        '      <tbody>\n'
        + '\n'.join(rows)
        + '\n      </tbody>\n    </table>\n'
    )
    return header


def _inject_earnings_chart(html: str, ticker: str, snap: dict) -> str:
    """Post-process the LLM-generated HTML to insert a deterministic SVG
    annotated price chart into the Earnings Setup section.

    Strategy:
    1. Locate the section with an Earnings Setup / 2-Year Price title.
    2. Find the section-body boundaries precisely (handles LLM SVG placeholders,
       tables, and varied structure robustly).
    3. Remove any <table>, <svg class="earnings-chart">, or empty LLM
       "earnings-chart-container" twin div already in the section-body.
    4. Prepend the deterministic SVG chart at the start of section-body content
       \u2014 UNLESS the chart was omitted (no real earnings event dates), in which
       case no chart is added and an HTML sentinel comment marks the intentional
       omission so the validator does not flag a missing chart.
    5. Rename the section-title to "2-Year Price &amp; Earnings Reactions".

    Returns the modified HTML, or the original if the section cannot be found.
    """
    # ---- Build chart SVG (may be None when no real event dates) -----------
    asum = (snap.get('analyst_summary') or {}).get(ticker, {})
    hist = asum.get('earningsHistory') or []
    intel = _load_intel_record(ticker)
    events = _earnings_events(ticker, hist, intel)

    sector = (snap.get('quotes') or {}).get(ticker, {}).get('sector')

    chart_svg: str | None
    try:
        chart_svg = render_earnings_annotated_chart(
            ticker, events, sector=sector, require_annotations=True
        )
    except Exception as exc:
        print(f'  [WARN] {ticker}: chart render failed: {exc}')
        chart_svg = ('<p class="chart-placeholder" style="font-size:12px;'
                     'color:#94a3b8;padding:12px 0;margin:0;">'
                     'Chart data unavailable \u2014 see Earnings Intel popup '
                     'for per-print detail</p>')

    if chart_svg is None:
        print(f'  [INFO] {ticker}: earnings chart omitted (no real event dates '
              f'to annotate) \u2014 section will carry no chart')

    # ---- Find the section title -------------------------------------------
    section_title_re = re.compile(
        r'class="section-title"[^>]*>\s*(?:[^<]*'
        r'(?:earnings\s+setup|revision\s+debate|2-year\s+price|earnings\s+reactions)'
        r'[^<]*)\s*</',
        re.IGNORECASE,
    )
    title_m = section_title_re.search(html)
    if not title_m:
        print(f'  [WARN] {ticker}: could not locate Earnings Setup section-title')
        return html

    search_start = title_m.end()

    # ---- Precisely locate section-body content range ----------------------
    content_start, body_close, section_close = _find_section_body_content(
        html, search_start
    )
    if content_start == -1:
        print(f'  [WARN] {ticker}: section-body not found after Earnings Setup title')
        return html

    # section_close is the </section> — cap body_close defensively
    if body_close == -1 or body_close > section_close:
        body_close = section_close

    # ---- Extract current section-body content ----------------------------
    body_content = html[content_start:body_close]

    # Remove any <table>...</table> blocks (old surprise tables)
    body_content = re.sub(
        r'<table[^>]*>.*?</table>', '', body_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove any existing <svg class="earnings-chart"> blocks (LLM placeholders)
    body_content = re.sub(
        r'<svg[^>]*class=["\']earnings-chart["\'][^>]*>.*?</svg>', '',
        body_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove the broken empty "earnings-chart-container" twin the LLM sometimes
    # emits below the real chart (often preceded by a "Price and earnings
    # reaction pattern" subsection-title). It is always empty/duplicative — the
    # deterministic chart above is the single source of truth.
    body_content = re.sub(
        r'(?:<div[^>]*class=["\']subsection-title["\'][^>]*>[^<]*'
        r'(?:price and earnings|earnings reaction)[^<]*</div>\s*)?'
        r'<div[^>]*class=["\']earnings-chart-container["\'][^>]*>\s*</div>',
        '', body_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Tidy up extra blank lines left by removals
    body_content = re.sub(r'\n{3,}', '\n\n', body_content)

    # ---- Build new section-body content ----------------------------------
    if chart_svg is None:
        # No annotatable chart: omit it entirely. Leave a sentinel comment so
        # the validator treats this as an intentional omission, not a failure.
        chart_block = (
            f'\n<!-- earnings-chart omitted: no real earnings event dates for '
            f'{_html.escape(ticker)} (require_annotations) -->\n'
        )
    else:
        chart_block = (
            '\n<div class="earnings-chart-wrap" '
            'style="margin:0 0 16px 0;overflow:hidden;">\n'
            + chart_svg
            + '\n</div>\n'
        )
    # ---- Build deterministic beat/miss table from snap earnings history ----
    asum2 = (snap.get('analyst_summary') or {}).get(ticker, {})
    hist2 = asum2.get('earningsHistory') or []
    beat_miss_table = _build_beat_miss_table(hist2)

    new_body_content = chart_block + beat_miss_table + body_content

    # ---- Splice into HTML ------------------------------------------------
    html = html[:content_start] + new_body_content + html[body_close:]

    # ---- Rename section title --------------------------------------------
    # Re-find after content splice (positions before title_m.start() are unchanged)
    title_m2 = section_title_re.search(html)
    if title_m2:
        html = (
            html[:title_m2.start()]
            + re.sub(
                r'>\s*[^<]+\s*</', '>2-Year Price &amp; Earnings Reactions</',
                html[title_m2.start():title_m2.end()], count=1,
                flags=re.IGNORECASE,
            )
            + html[title_m2.end():]
        )
    return html


def _save_failed(ticker, html, failures):
    """Persist a note that failed validation for inspection (never published)."""
    failed_dir = ROOT / 'notes' / 'drilldown' / '_failed'
    failed_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
    path = failed_dir / f'{ticker}_{stamp}.md'
    note = (
        f'<!-- VALIDATION FAILED:\n  - ' + '\n  - '.join(failures) + '\n-->\n' + html
    )
    path.write_text(note, encoding='utf-8')
    return path


BBB_OPEN = '<BULL_BASE_BEAR_JSON>'
BBB_CLOSE = '</BULL_BASE_BEAR_JSON>'


def _extract_bbb_json(html: str) -> dict | None:
    """Pull the Bull/Base/Bear JSON payload from between the delimiters.

    Returns the parsed dict, or None if the delimiters are absent or the
    enclosed text is not valid JSON.
    """
    start = html.find(BBB_OPEN)
    end = html.find(BBB_CLOSE)
    if start == -1 or end == -1 or end < start:
        return None
    raw = html[start + len(BBB_OPEN):end].strip()
    # Tolerate a stray fenced ```json wrapper around the payload.
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', raw, flags=re.DOTALL)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _strip_bbb_block(html: str) -> str:
    """Remove the raw JSON delimiter block from the document.

    Also strips any redundant three-column Bull/Base/Bear HTML the model emitted
    on its own inside Section 6's body so only the deterministic render remains.
    """
    html = re.sub(
        re.escape(BBB_OPEN) + r'.*?' + re.escape(BBB_CLOSE),
        '',
        html,
        flags=re.DOTALL,
    )
    return html


def _inject_bull_base_bear(html: str, payload: dict, current_price: float) -> str:
    """Replace Section 6's body content with the deterministic BBB render.

    Locates the "Investment Overview — Bull / Base / Bear" section by its
    section-title, finds the section-body, and swaps its inner content for the
    rendered FROG-style block. Raises if the section cannot be located.
    """
    title_re = re.compile(
        r'<div[^>]*class=["\']section-title["\'][^>]*>\s*Investment Overview[^<]*'
        r'Bull\s*/\s*Base\s*/\s*Bear\s*<',
        re.IGNORECASE,
    )
    tm = title_re.search(html)
    if not tm:
        raise RuntimeError(
            'Section 6 (Investment Overview — Bull / Base / Bear) section-title '
            'not found; cannot inject deterministic render'
        )
    content_start, body_close, _section_close = _find_section_body_content(
        html, tm.end()
    )
    if content_start == -1 or body_close == -1:
        raise RuntimeError('Section 6 section-body could not be located')

    rendered = render_bull_base_bear(payload, current_price)
    return html[:content_start] + '\n' + rendered + '\n  ' + html[body_close:]


def _quarantine_bbb(ticker: str, reason: str, raw_payload) -> Path:
    """Append a quarantine entry instead of overwriting an existing note."""
    qpath = ROOT / 'quarantine.json'
    try:
        data = json.loads(qpath.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            data = [data]
    except (OSError, ValueError):
        data = []
    data.append({
        'ticker': ticker,
        'stage': 'bull_base_bear_json',
        'reason': reason,
        'ts': _dt.datetime.now(_dt.timezone.utc).isoformat(),
        'payload_excerpt': (str(raw_payload)[:1000] if raw_payload is not None else None),
    })
    qpath.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return qpath


class BBBProcessingError(RuntimeError):
    """Raised when Section 6 JSON cannot be extracted, validated, or injected."""


def _process_bbb(ticker: str, html: str, current_price: float) -> str:
    """Turn the model's Section 6 JSON into the deterministic FROG render.

    Steps: extract the delimited JSON, validate it against the strict schema,
    render the FROG-style block, inject it into Section 6, and strip the raw
    delimiter block. Raises ``BBBProcessingError`` on any failure so the caller
    can retry the JSON-only prompt once and then quarantine.
    """
    payload = _extract_bbb_json(html)
    if payload is None:
        raise BBBProcessingError(
            f'{ticker}: Bull/Base/Bear JSON not found between '
            f'{BBB_OPEN}/{BBB_CLOSE} or not valid JSON'
        )
    try:
        validate_payload(payload)
    except BBBValidationError as exc:
        raise BBBProcessingError(f'{ticker}: BBB JSON schema invalid: {exc}') from exc

    html = _strip_bbb_block(html)
    return _inject_bull_base_bear(html, payload, current_price or 0.0)


def generate_one(ticker, snap, prompt_template, company_name=None):
    ticker = ticker.strip().upper()

    # Enrich the block with 5-year annual history (priority source chain) and
    # the per-ticker market-intel row (Supabase). Both degrade gracefully: a
    # failure leaves the section telling the model to source from search tools.
    try:
        history_5y = fetch_5yr_history(ticker).get('history_5y')
    except Exception as exc:
        print(f'  [WARN] {ticker}: 5yr history enrichment failed: {exc}')
        history_5y = None
    try:
        market_intel = fetch_market_intel(ticker)
    except Exception as exc:
        print(f'  [WARN] {ticker}: market_intel fetch failed: {exc}')
        market_intel = None

    data_block = build_signal_data_block(
        ticker, snap, history_5y=history_5y, market_intel=market_intel
    )
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

    # Current price feeds the deterministic Bull/Base/Bear payoff returns.
    q = (snap.get('quotes') or {}).get(ticker) or {}
    price = q.get('price')

    # Generate, validate, and retry once on validation failure with a strict
    # re-instruction prefix. A note that still fails is quarantined, not saved.
    html = _call_model(ticker, full_prompt, system)
    try:
        html = _process_bbb(ticker, html, price)
    except BBBProcessingError as exc:
        print(f'  [bbb] {ticker} v1 FAILED: {exc}; retrying JSON-only prompt once',
              file=sys.stderr)
        bbb_retry_prefix = (
            'PREVIOUS ATTEMPT had an invalid Section 6 block. Section 6 '
            '("Investment Overview — Bull / Base / Bear") MUST be emitted as '
            f'JSON delimited by {BBB_OPEN} and {BBB_CLOSE}, matching the schema '
            'in the prompt exactly: three cases (bull_case/base_case/bear_case) '
            'each with price_target_low, price_target_high, probability_pct, '
            'horizon_months, thesis_bullets (exactly 3 full-sentence strings), '
            'and claim_refs; plus a probability_rationale paragraph. The three '
            'probability_pct values MUST sum to 100. Do NOT render Section 6 as '
            'HTML. Resubmit the full document with a corrected Section 6 JSON '
            'block inside one fenced ```html block.\n\n'
        )
        html = _call_model(ticker, bbb_retry_prefix + full_prompt, system)
        try:
            html = _process_bbb(ticker, html, price)
        except BBBProcessingError as exc2:
            qpath = _quarantine_bbb(ticker, str(exc2), _extract_bbb_json(html))
            raise RuntimeError(
                f'{ticker}: Bull/Base/Bear JSON still invalid after one retry: '
                f'{exc2}. Quarantined at {qpath.relative_to(ROOT)}; existing note '
                f'left untouched.'
            ) from exc2
    ok, failures = validate(html, ticker=ticker)
    if not ok:
        print(f'  [validator] {ticker} v1 FAILED: {failures}', file=sys.stderr)
        # When the only/primary failure is a word-count overshoot, the model
        # reliably under-trims (e.g. 7000 -> 6758) unless given a concrete,
        # below-ceiling target. Compute one and demand it explicitly.
        wc_directive = ''
        for f in failures:
            m = re.search(r'word count (\d+) outside \[\d+, (\d+)\]', f)
            if m:
                current, ceiling = int(m.group(1)), int(m.group(2))
                if current > ceiling:
                    target = ceiling - 500
                    cut = current - target
                    wc_directive = (
                        f'\nLENGTH IS THE PRIMARY FAILURE: the previous draft ran '
                        f'{current} words, over the {ceiling}-word hard ceiling. '
                        f'You MUST cut at least {cut} words and land at or below '
                        f'{target} words. Tighten prose, drop redundant sentences, '
                        f'and merge repetitive commentary — do NOT delete any of '
                        f'the 14 sections, the payoff table, or the "What makes us '
                        f'right/wrong" bullets. Preserve all section structure.'
                    )
        retry_prefix = (
            'PREVIOUS ATTEMPT FAILED these automated checks:\n  - '
            + '\n  - '.join(failures)
            + wc_directive
            + '\nFix every one and resubmit. Do NOT output the literal string '
            '"MISSING" — use an em-dash "—" or search the web to fill the gap. '
            'Every section header MUST be <div class="section-title">…</div> '
            '(never <h2>). Every financial cell MUST carry a <a href="claim:N"> '
            'citation. Emit ONLY the corrected HTML in one fenced ```html block.\n\n'
        )
        html = _call_model(ticker, retry_prefix + full_prompt, system)
        html = _process_bbb(ticker, html, price)
        ok, failures = validate(html, ticker=ticker)
        if not ok:
            failed_path = _save_failed(ticker, html, failures)
            raise RuntimeError(
                f'{ticker}: drilldown failed validation after one retry: {failures}. '
                f'Quarantined at {failed_path.relative_to(ROOT)}; not published.'
            )

    # Post-process: replace the LLM earnings-surprise table with a
    # deterministic SVG annotated price chart.  This runs after validation
    # (so the validator still sees the old section-title for section-presence
    # checks), and before save so the published note carries the chart.
    try:
        html = _inject_earnings_chart(html, ticker, snap)
    except Exception as exc:
        print(f'  [WARN] {ticker}: chart injection failed, keeping original HTML: {exc}')

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
          f'{entry["word_count"]} words) — validator PASS')
    return md_path, entry


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate institutional drilldowns.')
    parser.add_argument('--tickers', nargs='+', help='one or more tickers')
    parser.add_argument('--ticker', help='single ticker (alias for --tickers)')
    parser.add_argument(
        '--legacy', action='store_true',
        help='use the v1 single-shot sonar-deep-research generator (truncation-'
             'prone; default is the section-by-section v2 pipeline)',
    )
    parser.add_argument('--force', action='store_true', help='bypass research cache')
    args = parser.parse_args(argv)

    tickers = list(args.tickers or [])
    if args.ticker:
        tickers.append(args.ticker)
    if not tickers:
        parser.error('provide --ticker TICKER or --tickers T1 T2 ...')

    # Default path: route through the section-by-section v2 generator, which
    # makes truncation structurally impossible and gates every note through the
    # validator + quality scorecard before publishing. --legacy keeps the v1
    # single-shot generator available for comparison / fallback.
    if not args.legacy:
        from automation.jobs.drilldown_generator_v2 import generate_one_v2
        snap = _load_snapshot()
        results = []
        any_fail = False
        for t in tickers:
            try:
                r = generate_one_v2(t, snap, force=args.force)
            except Exception as exc:
                print(f'  [ERROR] {t}: {exc}', file=sys.stderr)
                r = {'ticker': t, 'published': False, 'reasons': [str(exc)]}
            results.append(r)
            if not r.get('published'):
                any_fail = True
        print('\n=== SUMMARY (v2) ===')
        for r in results:
            status = 'OK  ' if r.get('published') else 'FAIL'
            print(f'  {status} {r["ticker"]}: score={r.get("score")} '
                  f'{r.get("md_path") or r.get("reasons")}')
        return 1 if any_fail else 0

    snap = _load_snapshot()
    prompt_template = PROMPT_PATH.read_text(encoding='utf-8')

    results = []
    errors = []
    for t in tickers:
        try:
            md_path, entry = generate_one(t, snap, prompt_template)
            results.append(entry)
        except Exception as exc:
            print(f'  [ERROR] {t}: {exc}', file=sys.stderr)
            errors.append((t, str(exc)))

    print('\n=== SUMMARY (legacy v1) ===')
    for e in results:
        print(f'  OK   {e["ticker"]}: {e["markdown_path"]} '
              f'({e["word_count"]} words)')
    for t, msg in errors:
        print(f'  FAIL {t}: {msg}')

    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
