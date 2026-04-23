#!/usr/bin/env python3
"""
Build earnings_calendar.json for the dashboard.
Contains upcoming earnings, recent earnings, and archive data.
"""
import json
import csv
from datetime import datetime

TODAY = datetime(2026, 3, 19)

# Load yfinance earnings dates
import os as _os
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
with open(_os.path.join(_SCRIPT_DIR, 'earnings_data.json')) as f:
    yf_data = json.load(f)


def _fetch_timings(tickers):
    """Best-effort BMO/AMC lookup via yfinance. Returns {ticker: {date: tag}}.

    Maps US/Eastern hour-of-day → reporting window:
      00–08:59 ET → BMO, 16:00–22:59 ET → AMC, else TBD.
    Silently returns {} if yfinance / zoneinfo are unavailable.
    """
    try:
        import yfinance as yf
        from zoneinfo import ZoneInfo
    except Exception:
        return {}
    ET = ZoneInfo("America/New_York")
    out = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            per_date = {}
            try:
                ed = t.earnings_dates
                if ed is not None and hasattr(ed, "index"):
                    for idx in ed.index:
                        ds = str(idx)[:10]
                        try:
                            h = idx.tz_convert(ET).hour if hasattr(idx, "tz_convert") else idx.hour
                        except Exception:
                            h = None
                        if h is None:
                            tag = "TBD"
                        elif 0 <= h < 9:
                            tag = "BMO"
                        elif 16 <= h < 23:
                            tag = "AMC"
                        else:
                            tag = "TBD"
                        if per_date.get(ds, "TBD") == "TBD":
                            per_date[ds] = tag
            except Exception:
                pass
            out[ticker] = per_date
        except Exception:
            out[ticker] = {}
    return out


# Load timings (optional; proceeds with TBDs if unavailable).
_TIMINGS = _fetch_timings(list(yf_data['all_tickers'].keys()))


def _timing_for(ticker, date_str):
    return (_TIMINGS.get(ticker) or {}).get(date_str, "TBD")

# Load research results for post-earnings tickers
research = {}
try:
    with open(_os.path.join(_SCRIPT_DIR, 'data', 'research', 'post_earnings_results.csv')) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get('Ticker', '').strip()
            if ticker:
                research[ticker] = row
except:
    pass

# Common names (we'll add more in JS)
NAMES = {
    'RBRK': 'Rubrik', 'ZS': 'Zscaler', 'NET': 'Cloudflare', 'PLTR': 'Palantir',
    'AMZN': 'Amazon', 'GOOG': 'Alphabet', 'META': 'Meta', 'MSFT': 'Microsoft',
    'CRWD': 'CrowdStrike', 'MDB': 'MongoDB', 'SNOW': 'Snowflake', 'PANW': 'Palo Alto',
    'CRM': 'Salesforce', 'NOW': 'ServiceNow', 'S': 'SentinelOne', 'FTNT': 'Fortinet',
    'DDOG': 'Datadog', 'HUBS': 'HubSpot', 'TEAM': 'Atlassian', 'WDAY': 'Workday',
    'CFLT': 'Confluent', 'DOCN': 'DigitalOcean', 'ESTC': 'Elastic', 'AYX': 'Alteryx',
    'INTU': 'Intuit', 'FSLY': 'Fastly', 'OKTA': 'Okta', 'AVGO': 'Broadcom',
    'MRVL': 'Marvell', 'ARM': 'Arm Holdings', 'NVDA': 'NVIDIA', 'TSM': 'TSMC',
    'MNDY': 'monday.com', 'ADBE': 'Adobe', 'ASAN': 'Asana', 'GTLB': 'GitLab',
    'PATH': 'UiPath', 'VRNS': 'Varonis', 'BILL': 'BILL Holdings', 'FOUR': 'Shift4',
    'COIN': 'Coinbase', 'SHOP': 'Shopify', 'TTD': 'The Trade Desk', 'AI': 'C3.ai',
    'IOT': 'Samsara'
}

# Build upcoming earnings (next earnings date for each ticker)
upcoming = []
for ticker, info in yf_data['all_tickers'].items():
    future_dates = []
    for d in info['earnings_dates']:
        try:
            dt = datetime.strptime(d, '%Y-%m-%d')
            diff = (dt - TODAY).days
            if diff > 0:
                future_dates.append((d, diff))
        except:
            pass
    future_dates.sort(key=lambda x: x[1])
    if future_dates:
        upcoming.append({
            'ticker': ticker,
            'name': NAMES.get(ticker, ticker),
            'earnings_date': future_dates[0][0],
            'days_until': future_dates[0][1],
            'timing': _timing_for(ticker, future_dates[0][0]),
            'status': 'upcoming'
        })

upcoming.sort(key=lambda x: x['days_until'])

# Build recent earnings (reported in last 14 days)
recent = []
for ticker, info in yf_data['all_tickers'].items():
    for d in info['earnings_dates']:
        try:
            dt = datetime.strptime(d, '%Y-%m-%d')
            diff = (TODAY - dt).days
            if 0 < diff <= 14:
                r = research.get(ticker, {})
                recent.append({
                    'ticker': ticker,
                    'name': NAMES.get(ticker, ticker),
                    'earnings_date': d,
                    'days_since': diff,
                    'timing': _timing_for(ticker, d),
                    'status': 'reported',
                    'revenue_actual': r.get('Revenue (Actual)', ''),
                    'revenue_beat_miss': r.get('Revenue Beat/Miss', ''),
                    'eps_actual': r.get('EPS (Actual)', ''),
                    'eps_beat_miss': r.get('EPS Beat/Miss', ''),
                    'stock_reaction': r.get('Stock Reaction', ''),
                    'fiscal_quarter': r.get('Fiscal Quarter', ''),
                    'note_file': f'notes/post_earnings/{ticker}_{d}.md'
                })
        except:
            pass

recent.sort(key=lambda x: x['days_since'])

calendar = {
    'generated': TODAY.strftime('%Y-%m-%d'),
    'upcoming': upcoming,
    'recent': recent,
    'next_reporter': upcoming[0] if upcoming else None,
    'summary': {
        'upcoming_count': len(upcoming),
        'recent_count': len(recent),
        'next_report_ticker': upcoming[0]['ticker'] if upcoming else None,
        'next_report_date': upcoming[0]['earnings_date'] if upcoming else None,
    }
}

with open(_os.path.join(_SCRIPT_DIR, 'earnings_calendar.json'), 'w') as f:
    json.dump(calendar, f, indent=2)

print(f"Upcoming: {len(upcoming)} tickers")
print(f"Recent reporters: {len(recent)} tickers")
print(f"Next to report: {upcoming[0]['ticker']} on {upcoming[0]['earnings_date']}" if upcoming else "None")
