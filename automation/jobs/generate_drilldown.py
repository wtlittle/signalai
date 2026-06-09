#!/usr/bin/env python3
"""Headless institutional drilldown generator.

Mirrors the browser flow in drilldown-surface.js: build a [SIGNAL_DATA_BLOCK]
from live market data, inject it into the canonical drilldown_prompt.md, call
Perplexity sonar-deep-research for a single self-contained HTML primer, then
persist via automation.jobs.save_drilldown (markdown + index.json manifest).

Quote and valuation fields come from Finnhub. Any field the data block marks
MISSING is sourced by the model from web search, exactly as the prompt directs.

Usage:
    python3 -m automation.jobs.generate_drilldown --tickers NET NVDA RBRK
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / 'drilldown_prompt.md'
HTML_OUT_DIR = ROOT / 'drilldown_data'

sys.path.insert(0, str(ROOT))
from automation.jobs.save_drilldown import save_drilldown  # noqa: E402

PPLX_URL = 'https://api.perplexity.ai/chat/completions'
PPLX_MODEL = os.environ.get('PERPLEXITY_MODEL_DRILLDOWN', 'sonar-deep-research')
FINNHUB_BASE = 'https://finnhub.io/api/v1'

SYSTEM_PROMPT = (
    "You are Signal Stack AI's institutional stock drilldown engine. Return "
    "only a complete standalone HTML document inside one ```html fenced block. "
    "No prose outside the block."
)


def _http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'signalai-drilldown/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _fmt(v, decimals=None):
    if v is None:
        return 'MISSING'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:  # NaN
        return 'MISSING'
    return f'{f:.{decimals}f}' if decimals is not None else str(v)


def _fmt_pct(v):
    return 'MISSING' if v is None else f'{float(v):.1f}%'


def _fmt_b_from_millions(v):
    return 'MISSING' if v is None else f'${float(v) / 1e3:.2f}B'


def fetch_finnhub(ticker, api_key):
    """Pull quote, fundamental metrics, and profile from Finnhub."""
    data = {}
    try:
        data['quote'] = _http_get_json(f'{FINNHUB_BASE}/quote?symbol={ticker}&token={api_key}')
    except Exception as exc:
        data['quote'] = {}
        print(f'  [WARN] {ticker}: quote fetch failed: {exc}')
    try:
        data['metric'] = _http_get_json(
            f'{FINNHUB_BASE}/stock/metric?symbol={ticker}&metric=all&token={api_key}'
        ).get('metric', {})
    except Exception as exc:
        data['metric'] = {}
        print(f'  [WARN] {ticker}: metric fetch failed: {exc}')
    try:
        data['profile'] = _http_get_json(
            f'{FINNHUB_BASE}/stock/profile2?symbol={ticker}&token={api_key}'
        )
    except Exception as exc:
        data['profile'] = {}
        print(f'  [WARN] {ticker}: profile fetch failed: {exc}')
    return data


def build_signal_data_block(ticker, fh):
    """Assemble the [SIGNAL_DATA_BLOCK] from Finnhub data.

    Field names and MISSING semantics match drilldown-surface.js so the model
    treats present numbers as ground truth and sources MISSING ones via search.
    """
    quote = fh.get('quote', {}) or {}
    m = fh.get('metric', {}) or {}
    prof = fh.get('profile', {}) or {}

    price = quote.get('c')
    lines = [
        '[SIGNAL_DATA_BLOCK]',
        'Generated: ' + _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        '',
        '## QUOTE',
        f'Ticker: {ticker}',
        f'Company: {prof.get("name") or "MISSING"}',
        f'Exchange: {prof.get("exchange") or "MISSING"}',
        f'Price: {_fmt(price, 2)}',
        f'MarketCap: {_fmt_b_from_millions(m.get("marketCapitalization") or prof.get("marketCapitalization"))}',
        f'EnterpriseValue: {_fmt_b_from_millions(m.get("enterpriseValue"))}',
        f'Sector: {prof.get("finnhubIndustry") or "MISSING"}',
        f'Industry: {prof.get("finnhubIndustry") or "MISSING"}',
        f'52wHigh: {_fmt(m.get("52WeekHigh"), 2)}',
        f'52wLow: {_fmt(m.get("52WeekLow"), 2)}',
        f'Beta: {_fmt(m.get("beta"), 2)}',
        f'TrailingPE: {_fmt(m.get("peTTM"), 1)}',
        f'EVRevenue: {_fmt(m.get("currentEv/salesTTM") or m.get("psTTM"), 2)}',
        f'EVEBITDA: {_fmt(m.get("currentEv/ebitdaTTM"), 2)}',
        f'PS_TTM: {_fmt(m.get("psTTM"), 2)}',
        f'RevenueGrowthTTMYoY: {_fmt_pct(m.get("revenueGrowthTTMYoy"))}',
        f'GrossMarginTTM: {_fmt_pct(m.get("grossMarginTTM"))}',
        f'OperatingMarginTTM: {_fmt_pct(m.get("operatingMarginTTM"))}',
        f'NetMarginTTM: {_fmt_pct(m.get("netProfitMarginTTM"))}',
        f'EV_FCF_TTM: {_fmt(m.get("currentEv/freeCashFlowTTM"), 1)}',
        f'RevenuePerShareTTM: {_fmt(m.get("revenuePerShareTTM"), 2)}',
        f'SharesOutstanding(M): {_fmt(prof.get("shareOutstanding"), 2)}',
        f'52wPriceReturn: {_fmt_pct(m.get("52WeekPriceReturnDaily"))}',
        '',
        '## ESTIMATES',
        '(Forward estimates, analyst targets, and consensus rating not in feed '
        '— source the latest from sell-side / company filings via search.)',
        '',
        '## EARNINGS HISTORY (last 8 quarters)',
        '(Not cached — source revenue/EPS beat-miss, 1-day move, and guidance '
        'tone from earnings releases and transcripts via search.)',
        '',
        '## CROSS-SECTOR COMPS',
        '(Not cached — select the most relevant public peers and source their '
        'EV/Revenue, forward P/E, gross margin, FCF margin, and revenue growth '
        'via search.)',
        '',
        '## MARKET INTEL',
        '(MISSING — source TAM, category growth rate, and structural drivers '
        'from Gartner / IDC / company filings via search.)',
        '',
        '[/SIGNAL_DATA_BLOCK]',
    ]
    return '\n'.join(lines)


def build_full_prompt(ticker, data_block):
    prompt_text = PROMPT_PATH.read_text(encoding='utf-8')
    body = prompt_text.replace('[SIGNAL_DATA_BLOCK]', data_block)
    header = (
        f'Generate the complete institutional drilldown for {ticker}. '
        'Use the prompt below verbatim. The [SIGNAL_DATA_BLOCK] has been '
        'pre-filled from live market data; treat its numbers as ground truth '
        'and source only the fields marked MISSING. Return ONE complete '
        'standalone HTML document inside a single ```html fenced block.\n\n'
    )
    return header + body


def extract_html_block(content):
    if not content:
        return ''
    # Reasoning models (sonar-deep-research) wrap chain-of-thought in
    # <think>...</think> before the answer. Strip it first, mirroring
    # automation/perplexity/client.py, or it leaks into the saved HTML.
    content = re.sub(r'<think>.*?</think>\s*', '', content, flags=re.DOTALL).strip()
    match = re.search(r'```(?:html)?\s*\n([\s\S]*?)\n```', content, flags=re.I)
    if match:
        content = match.group(1).strip()
    # Defensive: if any preamble survives, start at the first HTML root tag.
    doc = re.search(r'<!DOCTYPE html|<html\b', content, flags=re.I)
    if doc:
        content = content[doc.start():].strip()
    return content


MAX_TOKENS = int(os.environ.get('DRILLDOWN_MAX_TOKENS', '20000'))


def call_perplexity(prompt, api_key, effort='medium', timeout=900):
    body = json.dumps({
        'model': PPLX_MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ],
        'max_tokens': MAX_TOKENS,
        'temperature': 0.1,
        'reasoning_effort': effort,
    }).encode('utf-8')
    req = urllib.request.Request(
        PPLX_URL,
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def generate_one(ticker, pplx_key, finnhub_key, date_str):
    print(f'\n=== {ticker} ===')
    fh = fetch_finnhub(ticker, finnhub_key)
    price = (fh.get('quote') or {}).get('c')
    data_block = build_signal_data_block(ticker, fh)
    full_prompt = build_full_prompt(ticker, data_block)

    print(f'  [API CALL] {ticker} -> Perplexity {PPLX_MODEL} (this can take 2-5 min)...')
    t0 = time.time()
    resp = call_perplexity(full_prompt, pplx_key)
    elapsed = time.time() - t0

    choices = resp.get('choices') or []
    if not choices:
        raise RuntimeError(f'{ticker}: Perplexity returned no choices: {json.dumps(resp)[:400]}')
    content = choices[0]['message']['content']
    usage = resp.get('usage', {})
    finish = choices[0].get('finish_reason')
    print(f'  [DONE] {ticker} in {elapsed:.0f}s; '
          f"completion_tokens={usage.get('completion_tokens')} "
          f"queries={usage.get('num_search_queries')} finish={finish}")

    html = extract_html_block(content)
    if not html or len(html) < 2000:
        raise RuntimeError(
            f'{ticker}: extracted HTML too short ({len(html)} chars). '
            f'First 300 chars of raw content: {content[:300]!r}'
        )

    # The model occasionally hits max_tokens before emitting closing tags.
    # The iframe viewer tolerates this, but close them so the saved document
    # is well-formed. Warn so truncation is visible rather than silent.
    if finish == 'length' or '</html>' not in html.lower():
        print(f'  [WARN] {ticker}: response truncated (finish_reason={finish}); '
              'closing open tags.')
        if '</body>' not in html.lower():
            html += '\n</body>'
        if '</html>' not in html.lower():
            html += '\n</html>'

    company = (fh.get('profile') or {}).get('name') or ticker
    title = f'{company} ({ticker}) — Institutional Drilldown'

    HTML_OUT_DIR.mkdir(parents=True, exist_ok=True)
    html_file = HTML_OUT_DIR / f'{ticker}_drilldown_{date_str}.html'
    html_file.write_text(html, encoding='utf-8')
    html_rel = str(html_file.relative_to(ROOT))

    md_path, entry = save_drilldown(
        ticker=ticker,
        html=html,
        part='full',
        date_str=date_str,
        trigger='Deep Research',
        title=title,
        html_path=html_rel,
        price_at_gen=price,
    )
    print(f'  [SAVED] {md_path.relative_to(ROOT)} '
          f"({entry['word_count']} words, {entry['size_bytes']} bytes)")
    return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate institutional drilldowns headlessly.')
    parser.add_argument('--tickers', nargs='+', required=True)
    parser.add_argument('--date', default=None, help='YYYY-MM-DD (default: today UTC)')
    args = parser.parse_args(argv)

    pplx_key = (os.environ.get('PERPLEXITY_API_KEY') or os.environ.get('PPLX_API_KEY') or '').strip()
    finnhub_key = (os.environ.get('FINNHUB_API_KEY') or '').strip()
    if not pplx_key:
        parser.error('PERPLEXITY_API_KEY not set')
    if not finnhub_key:
        parser.error('FINNHUB_API_KEY not set')

    date_str = args.date or _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d')

    results = []
    errors = []
    for ticker in args.tickers:
        ticker = ticker.strip().upper()
        try:
            results.append(generate_one(ticker, pplx_key, finnhub_key, date_str))
        except Exception as exc:
            print(f'  [ERROR] {ticker}: {exc}')
            errors.append((ticker, str(exc)))

    print('\n=== SUMMARY ===')
    for e in results:
        print(f"  OK  {e['ticker']}: {e['markdown_path']} ({e['word_count']} words)")
    for t, msg in errors:
        print(f'  ERR {t}: {msg}')

    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
