#!/usr/bin/env python3
"""
Fetch earnings dates for all watchlist tickers using yfinance.
Outputs JSON with next/most-recent earnings dates.
"""
import json
import sys
from datetime import datetime, timedelta
import yfinance as yf

TICKERS = [
    'RBRK','ZS','NET','PLTR','AMZN','GOOG','META','MSFT','CRWD','MDB',
    'SNOW','PANW','CRM','NOW','S','FTNT','DDOG','HUBS','TEAM','WDAY',
    'CFLT','DOCN','ESTC','AYX','INTU','FSLY','OKTA',
    'AVGO','MRVL','ARM','NVDA','TSM',
    'MNDY','ADBE','ASAN','GTLB','PATH',
    'VRNS',
    'BILL','FOUR','COIN',
    'SHOP','TTD',
    'AI','IOT',
]

TODAY = datetime(2026, 3, 19)
WINDOW_START = TODAY - timedelta(days=14)   # March 5
WINDOW_END   = TODAY + timedelta(days=14)   # April 2

results = {}

for ticker in TICKERS:
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        
        # Try to get earnings date from calendar
        earnings_date = None
        earnings_dates_list = []
        
        # yfinance calendar can return different structures
        if cal is not None:
            if isinstance(cal, dict):
                # Check for Earnings Date key
                if 'Earnings Date' in cal:
                    ed = cal['Earnings Date']
                    if isinstance(ed, list) and len(ed) > 0:
                        earnings_dates_list = [str(d)[:10] for d in ed]
                    elif hasattr(ed, 'strftime'):
                        earnings_dates_list = [ed.strftime('%Y-%m-%d')]
                # Also check for other keys
                for key in cal:
                    if 'earning' in str(key).lower() and 'date' in str(key).lower():
                        val = cal[key]
                        if isinstance(val, list):
                            earnings_dates_list = [str(d)[:10] for d in val]
                        elif hasattr(val, 'strftime'):
                            earnings_dates_list = [val.strftime('%Y-%m-%d')]
            elif hasattr(cal, 'to_dict'):
                # DataFrame
                cal_dict = cal.to_dict()
                for key in cal_dict:
                    if 'earning' in str(key).lower():
                        earnings_dates_list = [str(v)[:10] for v in cal_dict[key].values()]
        
        # Also try earnings_dates property  
        try:
            ed_series = t.earnings_dates
            if ed_series is not None and hasattr(ed_series, 'index'):
                for idx in ed_series.index:
                    dt_str = str(idx)[:10]
                    if dt_str not in earnings_dates_list:
                        earnings_dates_list.append(dt_str)
        except:
            pass
            
        # Also try get_earnings_dates method
        try:
            hist_earnings = t.get_earnings_dates(limit=8)
            if hist_earnings is not None and hasattr(hist_earnings, 'index'):
                for idx in hist_earnings.index:
                    dt_str = str(idx)[:10]
                    if dt_str not in earnings_dates_list:
                        earnings_dates_list.append(dt_str)
        except:
            pass

        results[ticker] = {
            'earnings_dates': sorted(set(earnings_dates_list)),
            'calendar_raw': str(cal)[:500] if cal is not None else None,
            'error': None
        }
        
        print(f"  {ticker}: {len(earnings_dates_list)} dates found", file=sys.stderr)
        
    except Exception as e:
        results[ticker] = {
            'earnings_dates': [],
            'calendar_raw': None,
            'error': str(e)
        }
        print(f"  {ticker}: ERROR - {e}", file=sys.stderr)

# Classify tickers
pre_earnings = []   # reporting in next 14 days
post_earnings = []  # reported in last 14 days

for ticker, data in results.items():
    for date_str in data['earnings_dates']:
        try:
            ed = datetime.strptime(date_str, '%Y-%m-%d')
            days_until = (ed - TODAY).days
            
            if 0 <= days_until <= 14:
                pre_earnings.append({
                    'ticker': ticker,
                    'earnings_date': date_str,
                    'days_until': days_until
                })
            elif -14 <= days_until < 0:
                post_earnings.append({
                    'ticker': ticker,
                    'earnings_date': date_str,
                    'days_since': abs(days_until)
                })
        except:
            pass

# Sort
pre_earnings.sort(key=lambda x: x['days_until'])
post_earnings.sort(key=lambda x: x['days_since'])

output = {
    'as_of': TODAY.strftime('%Y-%m-%d'),
    'window': {
        'start': WINDOW_START.strftime('%Y-%m-%d'),
        'end': WINDOW_END.strftime('%Y-%m-%d')
    },
    'pre_earnings': pre_earnings,
    'post_earnings': post_earnings,
    'all_tickers': results,
    'summary': {
        'total_tickers': len(TICKERS),
        'pre_earnings_count': len(pre_earnings),
        'post_earnings_count': len(post_earnings),
        'tickers_with_dates': sum(1 for d in results.values() if d['earnings_dates']),
        'tickers_with_errors': sum(1 for d in results.values() if d['error'])
    }
}

# Write to file
with open('/home/user/workspace/watchlist-app/earnings_data.json', 'w') as f:
    json.dump(output, f, indent=2)

print(json.dumps(output['summary'], indent=2), file=sys.stderr)
print(f"\nPre-earnings ({len(pre_earnings)}):", file=sys.stderr)
for p in pre_earnings:
    print(f"  {p['ticker']}: {p['earnings_date']} ({p['days_until']} days)", file=sys.stderr)
print(f"\nPost-earnings ({len(post_earnings)}):", file=sys.stderr)
for p in post_earnings:
    print(f"  {p['ticker']}: {p['earnings_date']} ({p['days_since']} days ago)", file=sys.stderr)
