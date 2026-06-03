#!/usr/bin/env python3
"""
Build earnings_calendar.json for the dashboard.
Contains upcoming earnings, recent earnings, and archive data.

POST card data (REV/EPS actuals + surprises) is sourced from
earnings_intel.json -- the single source of truth. The calendar JSON keeps
its scheduling role (dates, BMO/AMC timing) but no longer carries its own
REV/EPS actuals. Missing intel renders as None ("n/a" in the card UI); values
are never fabricated.
"""
import json
import os as _os
from datetime import date, datetime, time

TODAY = datetime.combine(date.today(), time())

_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))

# Guidance fields are populated by PR #30 (split EPS/OpInc/FCF cascade).
# build_earnings_json reads them defensively (None until normalized in intel).
GUIDANCE_KEYS = (
    # Revenue (PR #30: + Street/PriorSource split)
    'guidanceRevenueDeltaPct',
    'guidanceRevenuePriorSource',
    'guidanceRevenueStreetDeltaPct',
    # Profitability (PR #30: EPS -> OpInc -> FCF cascade)
    'guidanceProfitMetricUsed',
    'guidanceEpsDeltaPct',
    'guidanceEpsDeltaAbs',
    'guidanceEpsPriorSource',
    'guidanceEpsStreetDeltaPct',
    'guidanceEpsStreetDeltaAbs',
    'guidanceOperatingProfitDeltaPct',
    'guidanceOperatingProfitDeltaAbs',
    'guidanceOperatingProfitPriorSource',
    'guidanceOperatingProfitStreetDeltaPct',
    'guidanceOperatingProfitStreetDeltaAbs',
    'guidanceFcfDeltaPct',
    'guidanceFcfDeltaAbs',
    'guidanceFcfPriorSource',
    'guidanceFcfStreetDeltaPct',
    'guidanceFcfStreetDeltaAbs',
    # Legacy compat (still emitted by some upstreams)
    'guidanceProfitDeltaPct',
)


def _load_json(name):
    with open(_os.path.join(_SCRIPT_DIR, name)) as f:
        return json.load(f)


def load_intel_results():
    """Return {ticker: intel entry dict} from earnings_intel.json.

    The calendar build reads POST card fields straight from these entries, so
    it surfaces whatever the data layer has -- and only that.
    """
    try:
        intel = _load_json('earnings_intel.json')
    except (OSError, ValueError):
        return {}
    return {
        ticker: entry
        for ticker, entry in (intel.get('tickers') or {}).items()
        if isinstance(entry, dict)
    }


def build_post_card(ticker, intel_results):
    """Build the POST-earnings card payload for one ticker from intel.

    Reads REV/EPS actuals + surprises from tickers[T].results_vs_consensus. Any
    missing field falls back to None (renders "n/a"); values are never invented.
    """
    entry = intel_results.get(ticker) or {}
    rvc = entry.get('results_vs_consensus') or {}
    card = {
        'revenue_actual': rvc.get('in_quarter_rev_actual'),
        'revenue_surprise_pct': rvc.get('in_quarter_rev_surprise_pct'),
        'eps_actual': rvc.get('in_quarter_eps_actual'),
        'eps_surprise_pct': rvc.get('in_quarter_eps_surprise_pct'),
        'revenue_source': rvc.get('in_quarter_rev_actual_source'),
        'eps_source': rvc.get('in_quarter_eps_actual_source'),
    }
    card.update({k: entry.get(k) for k in GUIDANCE_KEYS})
    return card


def _fetch_timings(tickers):
    """Best-effort BMO/AMC lookup via yfinance. Returns {ticker: {date: tag}}.

    Maps US/Eastern hour-of-day -> reporting window:
      00-08:59 ET -> BMO, 16:00-22:59 ET -> AMC, else TBD.
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


def build_calendar(yf_data, intel_results, timings):
    """Assemble the calendar dict from yfinance dates + intel POST data."""

    def _timing_for(ticker, date_str):
        return (timings.get(ticker) or {}).get(date_str, "TBD")

    upcoming = []
    for ticker, info in yf_data['all_tickers'].items():
        future_dates = []
        for d in info['earnings_dates']:
            try:
                dt = datetime.strptime(d, '%Y-%m-%d')
                diff = (dt - TODAY).days
                if diff > 0:
                    future_dates.append((d, diff))
            except ValueError:
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

    recent = []
    for ticker, info in yf_data['all_tickers'].items():
        for d in info['earnings_dates']:
            try:
                dt = datetime.strptime(d, '%Y-%m-%d')
                diff = (TODAY - dt).days
                if 0 < diff <= 14:
                    card = build_post_card(ticker, intel_results)
                    recent.append({
                        'ticker': ticker,
                        'name': NAMES.get(ticker, ticker),
                        'earnings_date': d,
                        'days_since': diff,
                        'timing': _timing_for(ticker, d),
                        'status': 'reported',
                        'note_file': f'notes/post_earnings/{ticker}_{d}.md',
                        **card,
                    })
            except ValueError:
                pass

    recent.sort(key=lambda x: x['days_since'])

    return {
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


def main():
    yf_data = _load_json('earnings_data.json')
    intel_results = load_intel_results()
    timings = _fetch_timings(list(yf_data['all_tickers'].keys()))
    calendar = build_calendar(yf_data, intel_results, timings)

    with open(_os.path.join(_SCRIPT_DIR, 'earnings_calendar.json'), 'w') as f:
        json.dump(calendar, f, indent=2)

    upcoming = calendar['upcoming']
    print(f"Upcoming: {len(upcoming)} tickers")
    print(f"Recent reporters: {len(calendar['recent'])} tickers")
    print(
        f"Next to report: {upcoming[0]['ticker']} on {upcoming[0]['earnings_date']}"
        if upcoming else "None"
    )


if __name__ == "__main__":
    main()
