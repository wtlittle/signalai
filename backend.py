#!/usr/bin/env python3
"""Lightweight backend proxy for Yahoo Finance data using yfinance."""
import json
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yfinance', '-q'])
    import yfinance as yf

try:
    import numpy as np
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'numpy', '-q'])
    import numpy as np

try:
    from ddgs import DDGS
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'ddgs', '-q'])
    from ddgs import DDGS

# --- Caches ---
_cache = {}
CACHE_TTL = 3600  # 1 hour for quant factors
CACHE_TTL_SHORT = 1800  # 30 min for short interest
CACHE_TTL_OUTPERF = 86400  # 24 hours for S&P outperformance
CACHE_TTL_COMPS = 7200  # 2 hours for cross-sector comps

# Sector peer map for quant factor scoring
SECTOR_PEERS = {
    'Technology': [
        'AAPL','MSFT','NVDA','AVGO','ORCL','CRM','ADBE','AMD','CSCO','INTC',
        'NOW','INTU','AMAT','MU','LRCX','KLAC','SNPS','CDNS','PANW','CRWD',
        'FTNT','ZS','NET','DDOG','MDB','SNOW','WDAY','TEAM','HUBS','PLTR',
        'RBRK','S','OKTA','ESTC','AYX','CFLT','DOCN','FSLY','MSTR','DELL',
    ],
    'Communication Services': [
        'GOOG','GOOGL','META','NFLX','DIS','CMCSA','T','VZ','TMUS','EA',
        'TTWO','RBLX','MTCH','ZM','SNAP','PINS','SPOT','WBD','PARA','LYV',
    ],
    'Consumer Discretionary': [
        'AMZN','TSLA','HD','MCD','NKE','SBUX','LOW','TJX','BKNG','CMG',
        'ABNB','ORLY','AZO','ROST','DHI','LEN','GM','F','MAR','HLT',
    ],
}

# Flatten all known peers for S&P 500 outperformance
SP500_SAMPLE = [
    'AAPL','MSFT','AMZN','NVDA','GOOG','META','TSLA','BRK-B','JPM','JNJ',
    'V','UNH','XOM','PG','MA','HD','CVX','MRK','ABBV','LLY',
    'PEP','KO','COST','AVGO','PFE','TMO','WMT','BAC','CSCO','MCD',
    'ABT','CRM','ACN','ORCL','NKE','DHR','TXN','PM','UPS','UNP',
    'NEE','INTC','CMCSA','ADBE','AMD','LOW','GS','CAT','RTX','ISRG',
    'BLK','SPGI','SYK','MDLZ','ADI','ADP','DE','TJX','BKNG','GILD',
    'VRTX','LRCX','PANW','SLB','REGN','AMAT','MU','SNPS','MMC','NOW',
    'CDNS','KLAC','INTU','CME','ZTS','EL','PGR','APH','DXCM','MSCI',
    'MCO','HUM','NXPI','MRVL','WM','AZO','ORLY','ROST','MNST','CTAS',
    'FTNT','IDXX','ODFL','EW','IT','CPRT','KEYS','TRGP','IR','GEHC',
    'RCL','MPWR','GWW','AXON','DECK','PODD','ANSS','CDW','TEAM','WST',
    'ZS','CRWD','DDOG','NET','SNOW','MDB','PLTR','WDAY','HUBS','OKTA',
    'CRM','ADBE','PANW','FTNT','AVGO','ORCL','NOW','INTU','DELL','HPQ',
    'ABNB','UBER','DASH','SQ','COIN','SHOP','PYPL','AFRM','BILL','FOUR',
    'MCK','CAH','COR','CI','ELV','HCA','MOH','CNC','DVA','THC',
    'PLD','AMT','CCI','EQIX','SPG','PSA','WELL','DLR','O','VICI',
    'DUK','SO','NEE','AEP','D','SRE','ED','XEL','WEC','ES',
    'GD','LMT','RTX','NOC','BA','TXT','LHX','HII','LDOS','BAH',
    'LIN','APD','SHW','ECL','DD','PPG','FCX','NEM','NUE','STLD',
    'WMT','COST','TGT','DG','DLTR','KR','SYY','GPC','TSCO','ULTA',
]
# Deduplicate
SP500_SAMPLE = list(dict.fromkeys(SP500_SAMPLE))


def cache_get(key, ttl):
    if key in _cache:
        entry = _cache[key]
        if time.time() - entry['ts'] < ttl:
            return entry['data']
    return None


def cache_set(key, data):
    _cache[key] = {'data': data, 'ts': time.time()}


def get_sector_for_ticker(ticker):
    """Get GICS sector for a ticker from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        return info.get('sector', 'Technology')
    except:
        return 'Technology'


def compute_quant_factors(symbol):
    """Compute 7 quant factor percentile scores vs sector peers."""
    cache_key = f'quant_{symbol}'
    cached = cache_get(cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        # Get ticker sector
        t = yf.Ticker(symbol)
        info = t.info
        sector = info.get('sector', 'Technology')

        # Find peers
        peers = SECTOR_PEERS.get(sector, SECTOR_PEERS.get('Technology', []))
        if symbol not in peers:
            peers = [symbol] + peers[:39]  # Keep manageable size
        peers = peers[:30]  # Cap at 30

        # Fetch info for all peers
        peer_data = {}
        for p in peers:
            try:
                pt = yf.Ticker(p)
                pi = pt.info
                # Also get 1Y price return
                hist = pt.history(period='1y')
                y1_return = None
                if hist is not None and len(hist) > 10:
                    first_close = hist['Close'].iloc[0]
                    last_close = hist['Close'].iloc[-1]
                    if first_close and first_close > 0:
                        y1_return = (last_close - first_close) / first_close

                peer_data[p] = {
                    'y1_return': y1_return,
                    'forwardPE': pi.get('forwardPE'),
                    'operatingMargins': pi.get('operatingMargins'),
                    'revenueGrowth': pi.get('revenueGrowth'),
                    'beta': pi.get('beta'),
                    'freeCashflow': pi.get('freeCashflow'),
                    'totalRevenue': pi.get('totalRevenue'),
                    'forwardEps': pi.get('forwardEps'),
                    'trailingEps': pi.get('trailingEps'),
                }
            except Exception as e:
                continue

        if symbol not in peer_data:
            return {'error': 'Could not fetch data for target ticker'}

        me = peer_data[symbol]

        def percentile_rank(values, my_val):
            if my_val is None:
                return None
            valid = [v for v in values if v is not None]
            if len(valid) < 2:
                return None
            below = sum(1 for v in valid if v < my_val)
            return round((below / len(valid)) * 100)

        # Compute factor values for all peers
        def get_factor_values(field, transform=None):
            vals = []
            for p in peer_data:
                v = peer_data[p].get(field)
                if v is not None and transform:
                    v = transform(v)
                vals.append(v)
            return vals

        factors = {}

        # 1. Momentum (1Y return, higher = better)
        momentum_vals = [peer_data[p].get('y1_return') for p in peer_data]
        my_momentum = me.get('y1_return')
        factors['Momentum'] = {
            'score': percentile_rank(momentum_vals, my_momentum),
            'value': my_momentum,
            'label': f"{'+' if my_momentum and my_momentum >= 0 else ''}{my_momentum * 100:.1f}% 1Y" if my_momentum is not None else '—',
        }

        # 2. Value (inverse forward PE, lower PE = higher value)
        value_vals = [1.0 / peer_data[p]['forwardPE'] if peer_data[p].get('forwardPE') and peer_data[p]['forwardPE'] > 0 else None for p in peer_data]
        my_value = 1.0 / me['forwardPE'] if me.get('forwardPE') and me['forwardPE'] > 0 else None
        factors['Value'] = {
            'score': percentile_rank(value_vals, my_value),
            'value': me.get('forwardPE'),
            'label': f"{me['forwardPE']:.1f}x Fwd PE" if me.get('forwardPE') else '—',
        }

        # 3. Quality (operating margin, higher = better)
        quality_vals = [peer_data[p].get('operatingMargins') for p in peer_data]
        my_quality = me.get('operatingMargins')
        factors['Quality'] = {
            'score': percentile_rank(quality_vals, my_quality),
            'value': my_quality,
            'label': f"{my_quality * 100:.1f}% Op Margin" if my_quality is not None else '—',
        }

        # 4. Growth (revenue growth, higher = better)
        growth_vals = [peer_data[p].get('revenueGrowth') for p in peer_data]
        my_growth = me.get('revenueGrowth')
        factors['Growth'] = {
            'score': percentile_rank(growth_vals, my_growth),
            'value': my_growth,
            'label': f"{my_growth * 100:.1f}% Rev Growth" if my_growth is not None else '—',
        }

        # 5. Volatility (inverse beta, lower beta = less volatile = higher score)
        vol_vals = [1.0 / peer_data[p]['beta'] if peer_data[p].get('beta') and peer_data[p]['beta'] > 0 else None for p in peer_data]
        my_vol = 1.0 / me['beta'] if me.get('beta') and me['beta'] > 0 else None
        factors['Volatility'] = {
            'score': percentile_rank(vol_vals, my_vol),
            'value': me.get('beta'),
            'label': f"{me['beta']:.2f} Beta" if me.get('beta') else '—',
        }

        # 6. Profitability (FCF margin, higher = better)
        prof_vals = []
        for p in peer_data:
            fcf = peer_data[p].get('freeCashflow')
            rev = peer_data[p].get('totalRevenue')
            if fcf is not None and rev and rev > 0:
                prof_vals.append(fcf / rev)
            else:
                prof_vals.append(None)
        my_fcf = me.get('freeCashflow')
        my_rev = me.get('totalRevenue')
        my_prof = my_fcf / my_rev if my_fcf is not None and my_rev and my_rev > 0 else None
        factors['Profitability'] = {
            'score': percentile_rank(prof_vals, my_prof),
            'value': my_prof,
            'label': f"{my_prof * 100:.1f}% FCF Margin" if my_prof is not None else '—',
        }

        # 7. Earnings Revision (forward vs trailing EPS change %)
        rev_vals = []
        for p in peer_data:
            fwd = peer_data[p].get('forwardEps')
            trail = peer_data[p].get('trailingEps')
            if fwd is not None and trail is not None and trail != 0:
                rev_vals.append((fwd - trail) / abs(trail))
            else:
                rev_vals.append(None)
        my_fwd = me.get('forwardEps')
        my_trail = me.get('trailingEps')
        my_rev_pct = (my_fwd - my_trail) / abs(my_trail) if my_fwd is not None and my_trail is not None and my_trail != 0 else None
        factors['Earnings Revision'] = {
            'score': percentile_rank(rev_vals, my_rev_pct),
            'value': my_rev_pct,
            'label': f"{'+' if my_rev_pct and my_rev_pct >= 0 else ''}{my_rev_pct * 100:.1f}% EPS Δ" if my_rev_pct is not None else '—',
        }

        result = {
            'ticker': symbol,
            'sector': sector,
            'factors': factors,
            'peerCount': len(peer_data),
        }
        cache_set(cache_key, result)
        return result

    except Exception as e:
        return {'error': str(e)}


def compute_short_interest(symbol):
    """Get current short interest data."""
    cache_key = f'short_{symbol}'
    cached = cache_get(cache_key, CACHE_TTL_SHORT)
    if cached:
        return cached

    try:
        t = yf.Ticker(symbol)
        info = t.info

        current = {
            'sharesShort': info.get('sharesShort'),
            'shortPercentOfFloat': info.get('shortPercentOfFloat'),
            'shortRatio': info.get('shortRatio'),
            'date': None,
        }
        # Convert shortPercentOfFloat from decimal to percentage if needed
        if current['shortPercentOfFloat'] and current['shortPercentOfFloat'] < 1:
            current['shortPercentOfFloat'] = current['shortPercentOfFloat'] * 100

        # Date of short interest
        date_si = info.get('dateShortInterest')
        if date_si:
            try:
                from datetime import datetime
                current['date'] = datetime.fromtimestamp(date_si).isoformat()[:10]
            except:
                pass

        prior = {
            'sharesShort': info.get('sharesShortPriorMonth'),
            'date': None,
        }
        date_prior = info.get('sharesShortPreviousMonthDate')
        if date_prior:
            try:
                from datetime import datetime
                prior['date'] = datetime.fromtimestamp(date_prior).isoformat()[:10]
            except:
                pass

        # Compute change %
        change = None
        if current['sharesShort'] and prior['sharesShort'] and prior['sharesShort'] > 0:
            change = ((current['sharesShort'] - prior['sharesShort']) / prior['sharesShort']) * 100

        result = {
            'ticker': symbol,
            'current': current,
            'priorMonth': prior,
            'sharesOutstanding': info.get('sharesOutstanding'),
            'floatShares': info.get('floatShares'),
            'change': change,
        }
        cache_set(cache_key, result)
        return result

    except Exception as e:
        return {'error': str(e)}


def compute_sp500_outperformance(symbol):
    """Compute daily % of S&P 500 outperformed on rolling 1Y basis."""
    cache_key = f'outperf_{symbol}'
    cached = cache_get(cache_key, CACHE_TTL_OUTPERF)
    if cached:
        return cached

    try:
        import pandas as pd
        from datetime import datetime, timedelta

        # We need 4 years of data (3Y display + 1Y lookback)
        end = datetime.now()
        start = end - timedelta(days=4 * 365 + 30)

        # Download target ticker + all S&P sample
        all_tickers = list(set([symbol] + SP500_SAMPLE))

        print(f"Downloading data for {len(all_tickers)} tickers for outperformance calc...")
        data = yf.download(all_tickers, start=start, end=end, group_by='ticker', auto_adjust=True, threads=True)

        if data is None or data.empty:
            return {'error': 'No data returned'}

        # Extract close prices
        closes = pd.DataFrame()
        for t in all_tickers:
            try:
                if len(all_tickers) > 1:
                    col = data[t]['Close'] if t in data.columns.get_level_values(0) else None
                else:
                    col = data['Close']
                if col is not None:
                    closes[t] = col
            except:
                continue

        if symbol not in closes.columns:
            return {'error': f'{symbol} not in downloaded data'}

        # Compute rolling 252-day returns
        returns_252 = closes.pct_change(252)
        
        # Drop NaN rows for the target ticker
        valid_mask = returns_252[symbol].notna()
        returns_252 = returns_252[valid_mask]

        # For each day, compute the percentile of the target ticker
        result_data = []
        # Sample every 5th trading day to keep data manageable
        indices = returns_252.index[::5]
        
        # Filter to last 3 years
        three_years_ago = end - timedelta(days=3 * 365)
        indices = [idx for idx in indices if idx >= pd.Timestamp(three_years_ago)]

        for idx in indices:
            row = returns_252.loc[idx]
            my_return = row[symbol]
            if pd.isna(my_return):
                continue
            
            # Count how many S&P stocks this ticker outperformed
            other_returns = row.drop(symbol).dropna()
            if len(other_returns) < 20:
                continue
            
            beaten = (my_return > other_returns).sum()
            percentile = (beaten / len(other_returns)) * 100

            result_data.append({
                'date': idx.strftime('%Y-%m-%d'),
                'percentile': round(percentile, 1),
            })

        result = {
            'ticker': symbol,
            'data': result_data,
            'stockCount': len(closes.columns) - 1,
        }
        cache_set(cache_key, result)
        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'error': str(e)}


# Broad cross-sector universe for finding fundamental comps
CROSS_SECTOR_UNIVERSE = [
    # Tech
    'AAPL','MSFT','NVDA','AVGO','ORCL','CRM','ADBE','AMD','CSCO','INTC',
    'NOW','INTU','AMAT','MU','LRCX','KLAC','SNPS','CDNS','PANW','CRWD',
    'FTNT','ZS','NET','DDOG','MDB','SNOW','WDAY','TEAM','HUBS','PLTR',
    'RBRK','S','OKTA','MRVL','ARM','TSM','SHOP','TTD','BILL','FOUR','COIN',
    # Comm Services
    'GOOG','META','NFLX','DIS','CMCSA','EA','TTWO','RBLX','SPOT',
    # Consumer Disc
    'AMZN','TSLA','HD','MCD','NKE','SBUX','LOW','TJX','BKNG','CMG',
    'ABNB','UBER','DASH',
    # Financials
    'JPM','GS','MS','BAC','V','MA','PYPL','SQ','BLK','SPGI',
    'CME','MCO','MSCI','AXP','ICE','COIN',
    # Healthcare
    'LLY','UNH','JNJ','ABBV','MRK','PFE','TMO','ABT','ISRG','DXCM',
    'VRTX','REGN','GILD','ZTS','IDXX','SYK','EW','MDT',
    # Industrials
    'CAT','DE','GE','HON','RTX','LMT','UPS','UNP','BA','WM',
    'GD','ITW','EMR','ROK','AXON',
    # Consumer Staples
    'PG','KO','PEP','COST','WMT','MNST','CL','EL',
    # Energy
    'XOM','CVX','COP','SLB','EOG','PXD',
    # Materials
    'LIN','APD','SHW','ECL','FCX','NEM',
    # REITs
    'PLD','AMT','CCI','EQIX','SPG','DLR',
    # Utilities
    'NEE','DUK','SO','AEP',
]
CROSS_SECTOR_UNIVERSE = list(dict.fromkeys(CROSS_SECTOR_UNIVERSE))  # deduplicate


def compute_cross_sector_comps(symbol):
    """Find 3-6 companies with similar fundamental profiles OUTSIDE the target's sector.
    
    Similarity is computed via normalized Euclidean distance across:
    - Forward P/E
    - Operating Margins
    - Revenue Growth
    - Beta
    - FCF Margin
    - Market Cap (log scale)
    """
    cache_key = f'comps_{symbol}'
    cached = cache_get(cache_key, CACHE_TTL_COMPS)
    if cached:
        return cached

    try:
        # Get target ticker info
        t = yf.Ticker(symbol)
        target_info = t.info
        target_sector = target_info.get('sector', '')
        target_industry = target_info.get('industry', '')

        def extract_fundamentals(info):
            """Extract the fundamental metrics we care about."""
            fwd_pe = info.get('forwardPE')
            op_margin = info.get('operatingMargins')
            rev_growth = info.get('revenueGrowth')
            beta = info.get('beta')
            fcf = info.get('freeCashflow')
            rev = info.get('totalRevenue')
            mcap = info.get('marketCap')
            ev_rev = info.get('enterpriseToRevenue')
            ev_ebitda = info.get('enterpriseToEbitda')
            trailing_pe = info.get('trailingPE')
            
            fcf_margin = None
            if fcf is not None and rev and rev > 0:
                fcf_margin = fcf / rev
            
            log_mcap = None
            if mcap and mcap > 0:
                import math
                log_mcap = math.log10(mcap)

            return {
                'forwardPE': fwd_pe if fwd_pe and 0 < fwd_pe < 500 else None,
                'trailingPE': trailing_pe if trailing_pe and 0 < trailing_pe < 500 else None,
                'operatingMargins': op_margin,
                'revenueGrowth': rev_growth,
                'beta': beta,
                'fcfMargin': fcf_margin,
                'logMarketCap': log_mcap,
                'marketCap': mcap,
                'enterpriseToRevenue': ev_rev if ev_rev and ev_rev > 0 else None,
                'enterpriseToEbitda': ev_ebitda if ev_ebitda and ev_ebitda > 0 else None,
                'totalRevenue': rev,
                'freeCashflow': fcf,
                'sector': info.get('sector', ''),
                'industry': info.get('industry', ''),
                'longName': info.get('longName') or info.get('shortName', ''),
            }

        target_funds = extract_fundamentals(target_info)

        # Build comp dimensions — the metrics we'll compare
        comp_dims = ['forwardPE', 'operatingMargins', 'revenueGrowth', 'beta', 'fcfMargin', 'logMarketCap']

        # Check that target has enough data to compare
        target_vals = {d: target_funds[d] for d in comp_dims if target_funds.get(d) is not None}
        if len(target_vals) < 3:
            return {'error': 'Insufficient fundamental data for comparison', 'ticker': symbol}

        # Fetch fundamentals for the whole universe (excluding same sector)
        candidates = []
        universe = [s for s in CROSS_SECTOR_UNIVERSE if s != symbol]

        # Batch-fetch to be efficient — use threads
        import concurrent.futures
        def fetch_one(sym):
            try:
                tk = yf.Ticker(sym)
                info = tk.info
                return (sym, info)
            except:
                return (sym, None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(fetch_one, universe))

        all_funds = {}
        for sym, info in results:
            if info is None:
                continue
            funds = extract_fundamentals(info)
            # Skip companies in the same sector
            if funds['sector'] == target_sector and target_sector:
                continue
            # Must have at least 3 of the comp dimensions
            available = sum(1 for d in comp_dims if funds.get(d) is not None)
            if available < 3:
                continue
            all_funds[sym] = funds

        if len(all_funds) < 3:
            return {'error': 'Not enough cross-sector data', 'ticker': symbol}

        # Compute statistics for normalization (z-score scaling)
        dim_vals = {d: [] for d in comp_dims}
        for sym, funds in all_funds.items():
            for d in comp_dims:
                v = funds.get(d)
                if v is not None:
                    dim_vals[d].append(v)
        # Also include target
        for d in comp_dims:
            v = target_funds.get(d)
            if v is not None:
                dim_vals[d].append(v)

        dim_mean = {}
        dim_std = {}
        for d in comp_dims:
            vals = dim_vals[d]
            if len(vals) >= 3:
                dim_mean[d] = np.mean(vals)
                dim_std[d] = np.std(vals) if np.std(vals) > 0 else 1.0
            else:
                dim_mean[d] = 0
                dim_std[d] = 1.0

        def z_score(val, dim):
            if val is None:
                return None
            return (val - dim_mean[dim]) / dim_std[dim]

        # Compute similarity (Euclidean distance in z-score space)
        target_z = {d: z_score(target_funds.get(d), d) for d in comp_dims}

        scored = []
        for sym, funds in all_funds.items():
            cand_z = {d: z_score(funds.get(d), d) for d in comp_dims}
            # Distance: sum of squared differences for shared dimensions
            shared = [d for d in comp_dims if target_z[d] is not None and cand_z[d] is not None]
            if len(shared) < 3:
                continue
            dist = sum((target_z[d] - cand_z[d]) ** 2 for d in shared)
            dist = (dist / len(shared)) ** 0.5  # Normalize by dimension count
            scored.append((sym, dist, funds))

        # Sort by distance (closest = most similar)
        scored.sort(key=lambda x: x[1])

        # Pick top 3-6, ensuring sector diversity (max 2 from same sector)
        selected = []
        sector_counts = {}
        for sym, dist, funds in scored:
            s = funds['sector']
            if sector_counts.get(s, 0) >= 2:
                continue
            selected.append((sym, dist, funds))
            sector_counts[s] = sector_counts.get(s, 0) + 1
            if len(selected) >= 6:
                break

        if len(selected) < 3:
            # Relax constraint if needed
            selected = scored[:6]

        # Build result
        comps = []
        for sym, dist, funds in selected:
            comps.append({
                'ticker': sym,
                'name': funds.get('longName', sym),
                'sector': funds.get('sector', ''),
                'industry': funds.get('industry', ''),
                'forwardPE': round(funds['forwardPE'], 1) if funds.get('forwardPE') else None,
                'trailingPE': round(funds['trailingPE'], 1) if funds.get('trailingPE') else None,
                'operatingMargins': round(funds['operatingMargins'] * 100, 1) if funds.get('operatingMargins') is not None else None,
                'revenueGrowth': round(funds['revenueGrowth'] * 100, 1) if funds.get('revenueGrowth') is not None else None,
                'beta': round(funds['beta'], 2) if funds.get('beta') else None,
                'fcfMargin': round(funds['fcfMargin'] * 100, 1) if funds.get('fcfMargin') is not None else None,
                'marketCap': funds.get('marketCap'),
                'enterpriseToRevenue': round(funds['enterpriseToRevenue'], 1) if funds.get('enterpriseToRevenue') else None,
                'enterpriseToEbitda': round(funds['enterpriseToEbitda'], 1) if funds.get('enterpriseToEbitda') else None,
                'similarity': round(1 / (1 + dist), 2),  # 0-1 score, higher = more similar
            })

        # Also include target data in same format for the comp sheet
        target_row = {
            'ticker': symbol,
            'name': target_info.get('longName') or target_info.get('shortName', symbol),
            'sector': target_sector,
            'industry': target_industry,
            'forwardPE': round(target_funds['forwardPE'], 1) if target_funds.get('forwardPE') else None,
            'trailingPE': round(target_funds['trailingPE'], 1) if target_funds.get('trailingPE') else None,
            'operatingMargins': round(target_funds['operatingMargins'] * 100, 1) if target_funds.get('operatingMargins') is not None else None,
            'revenueGrowth': round(target_funds['revenueGrowth'] * 100, 1) if target_funds.get('revenueGrowth') is not None else None,
            'beta': round(target_funds['beta'], 2) if target_funds.get('beta') else None,
            'fcfMargin': round(target_funds['fcfMargin'] * 100, 1) if target_funds.get('fcfMargin') is not None else None,
            'marketCap': target_funds.get('marketCap'),
            'enterpriseToRevenue': round(target_funds['enterpriseToRevenue'], 1) if target_funds.get('enterpriseToRevenue') else None,
            'enterpriseToEbitda': round(target_funds['enterpriseToEbitda'], 1) if target_funds.get('enterpriseToEbitda') else None,
            'similarity': 1.0,
        }

        result = {
            'ticker': symbol,
            'sector': target_sector,
            'target': target_row,
            'comps': comps,
        }
        cache_set(cache_key, result)
        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'error': str(e), 'ticker': symbol}


def lookup_private_company(name):
    """Look up a private company's details using web search (ddgs)."""
    cache_key = f'private_{name.lower().strip()}'
    cached = cache_get(cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        results_text = ''

        queries = [
            (f'{name} company valuation funding revenue 2025 2026', 8),
            (f'{name} company industry sector what does it do', 5),
            (f'{name} latest funding round series raised 2024 2025 2026', 5),
        ]
        for query, max_results in queries:
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=max_results))
                    for r in results:
                        results_text += f"{r.get('title', '')}. {r.get('body', '')}\n"
            except Exception as qe:
                print(f'[lookup] Query failed for "{query[:60]}...": {qe}')

        print(f'[lookup] {name}: collected {len(results_text)} chars of search text')

        # Parse the search results to extract structured info
        result = extract_company_info(name, results_text)
        cache_set(cache_key, result)
        return result

    except Exception as e:
        print(f'Lookup failed for {name}: {e}')
        import traceback
        traceback.print_exc()
        return {
            'name': name,
            'subsector': 'Unknown',
            'valuation': 'N/A',
            'funding': 'N/A',
            'revenue': 'N/A',
            'metrics': '',
            'error': str(e),
        }


def extract_company_info(name, text):
    """Extract structured company info from search result text."""
    import re

    info = {
        'name': name,
        'subsector': 'Technology',
        'valuation': 'N/A',
        'funding': 'N/A',
        'revenue': 'N/A',
        'metrics': '',
    }

    text_lower = text.lower()
    name_lower = name.lower()

    # ---------- Extract valuation ----------
    # Web snippets often lack spaces: "approximately$800 billionin" or "at$350billion"
    val_patterns = [
        # "valuation of/reached about $159 billion"
        r'valuation\s+(?:of\s+|reached\s+)?(?:about\s+|approximately\s+|around\s+|~)?\$?\s?([\d,.]+)\s*(billion|trillion|B|T|bn)',
        # "valued at $159 billion" (allow text between 'valued' and 'at')
        r'valued\s+.*?at\s*\$?\s?([\d,.]+)\s*(billion|trillion|B|T|bn)',
        # "$159 billion valuation"
        r'\$\s?([\d,.]+)\s*(billion|trillion|B|T|bn)\s*(?:,\s*)?(?:valuation|in\s+value)',
        # "worth $159 billion"
        r'worth\s+(?:about\s+|approximately\s+|around\s+|~)?\$?\s?([\d,.]+)\s*(billion|trillion|B|T|bn)',
        # Generic: any "$N billion" near valuation context words (limit to 40 chars distance)
        r'(?:valuation|valued|worth|value)\S*\s+[^$]{0,40}\$\s?([\d,.]+)\s*(billion|trillion|B|T|bn)',
        # Compact: "$91B" standalone — only B, not T (trillion amounts are usually volume/revenue, not valuation)
        r'\$([\d,.]+)\s*(B)\b',
    ]
    # Try each pattern, collect all matches, then pick the best one
    val_candidates = []
    for pat in val_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            num_str = m.group(1).replace(',', '')
            try:
                num = float(num_str)
            except ValueError:
                continue
            if num < 0.1:  # Skip implausible valuations
                continue
            unit = m.group(2).upper()[0]
            suffix = 'T' if unit == 'T' else 'B'
            val_usd_b = num * 1000 if suffix == 'T' else num
            # Skip if this is clearly a payment/transaction volume, not a valuation
            context_after = text[m.end():m.end()+80].lower()
            context_before = text[max(0, m.start()-80):m.start()].lower()
            skip_after = ['payment volume', 'transaction volume', 'in payment', 'in transaction', 'volume processed', 'processed in']
            skip_before = ['payment volume', 'transaction volume', 'volume', 'processed', 'reached', 'generated by']
            if any(x in context_after for x in skip_after):
                continue
            if any(x in context_before for x in skip_before) and 'valuation' not in context_before:
                continue
            # Score: prefer matches near company name, prefer valuation-context words
            context = text_lower[max(0, m.start()-80):m.end()+40]
            score = 0
            if name_lower in context:
                score += 10
            if 'valuation' in context or 'valued' in context or 'worth' in context:
                score += 5
            # Prefer bigger valuations for private companies (more likely to be correct)
            if val_usd_b >= 1:
                score += 2
            val_candidates.append((score, m.start(), num, suffix))
    
    if val_candidates:
        # Pick highest score, then earliest position in text as tiebreaker
        val_candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        _, _, num, suffix = val_candidates[0]
        if num == int(num):
            info['valuation'] = f'${int(num)}{suffix}'
        else:
            info['valuation'] = f'${num}{suffix}'

    # ---------- Extract funding round ----------
    # Look for structured patterns like "Series D ($1.2 billion)", "raised $500M in Series C"
    fund_patterns = [
        # "Series X ($NM/B)" or "Series X round ($NM/B)" — use word boundary to avoid "Series Df"
        r'(Series\s+[A-Z](?:[0-9])?)\b\s*(?:round)?\s*[^.]{0,40}?\(?\$([\d,.]+)\s*(billion|million|B|M|bn|mn)\)?',
        # "raised $N in a Series X"
        r'raised\s+\$([\d,.]+)\s*(billion|million|B|M|bn|mn)\s*(?:in\s+)?(?:a\s+|its\s+)?(Series\s+[A-Z](?:[0-9])?)\b',
        # "$N Series X" 
        r'\$([\d,.]+)\s*(billion|million|B|M|bn|mn)\s+(Series\s+[A-Z](?:[0-9])?)\b\s*(?:round|funding)?',
        # Just "Series X" without amount
        r'(Series\s+[A-Z](?:[0-9])?)\b\s*(?:funding|round|investment)',
        # "raised $N billion/million"
        r'raised\s+(?:about\s+|over\s+|more\s+than\s+)?\$([\d,.]+)\s*(billion|million|B|M|bn|mn)',
        # "$N funding round"
        r'\$([\d,.]+)\s*(billion|million|B|M|bn|mn)\s+(?:funding|round|raise|investment)',
    ]
    for pat in fund_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            # Build a clean funding string
            has_series = any(g and g.lower().startswith('series') for g in groups if g)
            has_amount = any(g and g[0].isdigit() for g in groups if g)
            
            if has_series and has_amount:
                # Extract series and amount separately
                series = None
                amount = None
                unit = None
                for g in groups:
                    if g and g.lower().startswith('series'):
                        series = g
                    elif g and g[0].isdigit():
                        amount = g.replace(',', '')
                    elif g and g[0].isalpha() and not g.lower().startswith('series'):
                        unit = g.upper()[0]
                if series and amount and unit:
                    unit_str = 'B' if unit == 'B' else 'M'
                    info['funding'] = f'{series} (${amount}{unit_str})'
                elif series:
                    info['funding'] = series
            elif has_series:
                series = [g for g in groups if g and g.lower().startswith('series')][0]
                info['funding'] = series
            elif has_amount:
                amt_parts = [g for g in groups if g and (g[0].isdigit() or g[0].isalpha())]
                if len(amt_parts) >= 2:
                    amount = amt_parts[0].replace(',', '')
                    unit = amt_parts[1].upper()[0]
                    unit_str = 'B' if unit == 'B' else 'M'
                    info['funding'] = f'${amount}{unit_str} round'
            break

    # Look for funding year context near the match
    if info['funding'] != 'N/A':
        # Try to find a year near the funding mention
        fund_lower = info['funding'].lower()
        idx = text_lower.find(fund_lower[0:10].lower()) if len(fund_lower) >= 10 else -1
        if idx == -1:
            # Fallback: search for 'series' near a year
            year_m = re.search(r'(?:series\s+[a-z]+|raised|funding)[^.]{0,60}(202[3-6])', text_lower)
            if year_m and '(' not in info['funding']:
                info['funding'] += f' ({year_m.group(1)})'

    # ---------- Extract revenue ----------
    rev_patterns = [
        # "$1.9 trillion in payment volume" — skip this, look for revenue/ARR specifically
        # "revenue of $N" or "ARR of $N"
        r'(?:revenue|ARR|annual\s+recurring\s+revenue|annualized\s+revenue)\s*(?:of\s+)?(?:about\s+|approximately\s+|~\s*)?\$([\d,.]+)\s*(billion|million|B|M|bn|mn)',
        # "$N in revenue/ARR"
        r'\$([\d,.]+)\s*(billion|million|B|M|bn|mn)\s+(?:in\s+)?(?:annual\s+)?(?:revenue|ARR|annual\s+recurring)',
        # "generating $N"
        r'(?:generating|generates|generated|earns|earning)\s+(?:about\s+|approximately\s+|~\s*)?\$([\d,.]+)\s*(billion|million|B|M|bn|mn)(?:\s+(?:in\s+)?(?:annual\s+)?(?:revenue|ARR))?',
        # "revenue exceeded $N" or "run-rate" patterns
        r'(?:revenue|run.?rate)\s+(?:exceeded|surpassed|reached|crossed|hit)\s+\$([\d,.]+)\s*(billion|million|B|M|bn|mn)',
        # "$N ARR" compact
        r'\$([\d,.]+)\s*(B|M)\s+ARR',
    ]
    for pat in rev_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            num = m.group(1).replace(',', '')
            unit = m.group(2).upper()[0]
            suffix = 'B' if unit == 'B' else 'M'
            context = text_lower[max(0, m.start()-40):m.end()+40]
            is_arr = 'arr' in context or 'annual recurring' in context or 'annualized' in context or 'run-rate' in context or 'run rate' in context
            info['revenue'] = f'~${num}{suffix}' + (' ARR' if is_arr else '')
            break

    # ---------- Extract subsector / industry ----------
    # Score-based classification: count keyword hits per sector, highest score wins.
    # Each keyword has a weight (default 1, higher for very specific terms).
    sector_keyword_list = [
        ('AI Foundation Models', [('large language model', 3), ('foundation model', 3), ('chatbot ai company', 3), ('trains ai models', 2), ('ai safety', 2)]),
        ('AI / Data Platform', [('data platform', 2), ('data lakehouse', 3), ('machine learning platform', 3), ('ml platform', 2), ('data analytics platform', 2)]),
        ('AI Infrastructure', [('gpu cloud', 3), ('ai compute', 2), ('ai infrastructure company', 3), ('cloud gpu', 3), ('ai chips', 3), ('ai hardware', 2)]),
        ('AI Data Infrastructure', [('data labeling', 3), ('data annotation', 3), ('training data company', 3)]),
        ('Space Technology', [('rocket', 2), ('launch vehicle', 3), ('spacex', 3), ('orbital', 2), ('space launch', 3), ('space industry', 2), ('spacecraft', 3), ('starship', 3), ('starlink', 3)]),
        ('Defense Tech', [('defense technology', 3), ('defense tech', 2), ('defense contractor', 3)]),
        ('Cybersecurity', [('cybersecurity', 3), ('security platform', 2), ('threat detection', 3), ('endpoint security', 3), ('zero trust', 2)]),
        ('Autonomous Vehicles', [('self-driving', 3), ('autonomous vehicle', 3), ('robotaxi', 3), ('autonomous driving', 3)]),
        ('Robotics', [('robotics company', 3), ('humanoid robot', 3)]),
        ('Fintech', [('fintech', 3), ('financial technology company', 3), ('payment processing', 3), ('payments platform', 3), ('payment infrastructure', 3), ('neobank', 3), ('digital banking', 2), ('financial infrastructure', 2), ('accept payments', 2)]),
        ('Crypto / Web3', [('cryptocurrency exchange', 3), ('blockchain platform', 3), ('web3 company', 3), ('defi protocol', 3), ('crypto exchange', 3)]),
        ('Insurtech', [('insurtech', 3), ('insurance technology', 3), ('digital insurance', 3)]),
        ('Cloud Infrastructure', [('cloud infrastructure company', 3), ('iaas provider', 3), ('paas provider', 3)]),
        ('Enterprise Software', [('enterprise software', 2), ('saas platform', 2), ('crm platform', 3), ('erp platform', 3), ('project management platform', 2), ('workflow automation', 2), ('collaboration software', 2), ('productivity software', 2)]),
        ('Developer Tools', [('developer tools', 2), ('devops platform', 3), ('code editor', 3), ('developer platform', 2)]),
        ('Design & Creative', [('design platform', 3), ('design tool', 3), ('creative platform', 2), ('graphic design', 2), ('visual communication', 2), ('online design', 2)]),
        ('E-Commerce', [('e-commerce company', 3), ('ecommerce platform', 3), ('online marketplace', 3), ('online retail', 2)]),
        ('Social Media', [('social media platform', 3), ('social network', 3)]),
        ('Gaming', [('game studio', 3), ('video game company', 3), ('gaming platform', 2)]),
        ('Healthtech', [('healthtech', 3), ('health tech company', 3), ('digital health', 2), ('telemedicine', 3)]),
        ('Biotech', [('biotechnology company', 3), ('biotech company', 3), ('drug discovery', 3), ('gene therapy', 3), ('genomics company', 3)]),
        ('Edtech', [('edtech', 3), ('education technology', 3), ('online learning platform', 3)]),
        ('Clean Energy', [('clean energy company', 3), ('renewable energy', 2), ('solar energy', 2), ('battery technology', 2), ('ev charging', 3)]),
        ('Supply Chain', [('supply chain platform', 3), ('logistics platform', 3), ('freight platform', 3)]),
        ('HR Tech', [('hr tech', 3), ('human resources platform', 3), ('hr platform', 3), ('recruiting platform', 3), ('talent management', 2), ('employee management', 2), ('workforce management', 2), ('payroll', 2), ('hr software', 2)]),
    ]
    # Score each sector
    sector_scores = {}
    for sector, kw_weights in sector_keyword_list:
        score = 0
        for kw, weight in kw_weights:
            count = text_lower.count(kw)
            if count > 0:
                score += weight * min(count, 3)  # Cap at 3 occurrences
        if score > 0:
            sector_scores[sector] = score
    
    if sector_scores:
        best_sector = max(sector_scores, key=sector_scores.get)
        info['subsector'] = best_sector

    # ---------- Extract key metrics ----------
    metric_parts = []

    # Growth rate
    growth_m = re.search(r'(\d+)[%]?\s*(?:%\s*)?(?:YoY|year.over.year|annual|revenue)\s*(?:revenue\s+)?growth', text, re.IGNORECASE)
    if growth_m:
        metric_parts.append(f'{growth_m.group(1)}% YoY growth')

    # NRR
    nrr_m = re.search(r'(\d+)[%+]+\s*(?:NRR|net\s+revenue\s+retention|net\s+retention)', text, re.IGNORECASE)
    if nrr_m:
        metric_parts.append(f'{nrr_m.group(1)}%+ NRR')

    # Customer / user count
    cust_m = re.search(r'([\d,]+(?:\.\d+)?\s*[KkMm]?)\+?\s*(?:paying\s+)?(?:customers|clients|enterprises|users|businesses|companies)', text, re.IGNORECASE)
    if cust_m:
        count = cust_m.group(1).strip()
        label = 'users' if 'user' in text_lower[max(0,cust_m.start()-5):cust_m.end()+10] else 'customers'
        metric_parts.append(f'{count}+ {label}')

    # Employee count
    emp_m = re.search(r'([\d,]+)\+?\s*employees', text, re.IGNORECASE)
    if emp_m:
        metric_parts.append(f'{emp_m.group(1)} employees')

    # Payment volume (specific for fintech)
    vol_m = re.search(r'\$([\d,.]+)\s*(trillion|billion|T|B)\s+(?:in\s+)?(?:payment|transaction)\s+volume', text, re.IGNORECASE)
    if vol_m:
        num = vol_m.group(1).replace(',', '')
        unit = vol_m.group(2).upper()[0]
        suffix = 'T' if unit == 'T' else 'B'
        metric_parts.append(f'${num}{suffix} payment volume')

    info['metrics'] = ', '.join(metric_parts[:3]) if metric_parts else ''

    return info


class QuoteHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress logs

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET')
        self.end_headers()

        if parsed.path == '/quote':
            symbols = params.get('symbols', [''])[0]
            if not symbols:
                self.wfile.write(json.dumps({'error': 'No symbols'}).encode())
                return
            tickers = [s.strip() for s in symbols.split(',') if s.strip()]
            results = {}
            for sym in tickers:
                try:
                    t = yf.Ticker(sym)
                    info = t.info
                    results[sym] = {
                        'symbol': sym,
                        'longName': info.get('longName') or info.get('shortName', sym),
                        'price': info.get('currentPrice') or info.get('regularMarketPrice'),
                        'marketCap': info.get('marketCap'),
                        'enterpriseValue': info.get('enterpriseValue'),
                        'totalRevenue': info.get('totalRevenue'),
                        'totalCash': info.get('totalCash'),
                        'totalDebt': info.get('totalDebt'),
                        'freeCashflow': info.get('freeCashflow'),
                        'operatingCashflow': info.get('operatingCashflow'),
                        'targetMeanPrice': info.get('targetMeanPrice'),
                        'targetHighPrice': info.get('targetHighPrice'),
                        'targetLowPrice': info.get('targetLowPrice'),
                        'recommendationKey': info.get('recommendationKey'),
                        'numberOfAnalystOpinions': info.get('numberOfAnalystOpinions'),
                        'fiftyTwoWeekHigh': info.get('fiftyTwoWeekHigh'),
                        'fiftyTwoWeekLow': info.get('fiftyTwoWeekLow'),
                        'averageVolume': info.get('averageVolume'),
                        'volume': info.get('volume'),
                        'beta': info.get('beta'),
                        'forwardPE': info.get('forwardPE'),
                        'trailingPE': info.get('trailingPE'),
                        'sharesOutstanding': info.get('sharesOutstanding'),
                        'revenueGrowth': info.get('revenueGrowth'),
                        'earningsGrowth': info.get('earningsGrowth'),
                        'forwardEps': info.get('forwardEps'),
                        'trailingEps': info.get('trailingEps'),
                        'enterpriseToRevenue': info.get('enterpriseToRevenue'),
                        'enterpriseToEbitda': info.get('enterpriseToEbitda'),
                        'operatingMargins': info.get('operatingMargins'),
                        'sector': info.get('sector'),
                        'industry': info.get('industry'),
                        'city': info.get('city'),
                        'state': info.get('state'),
                        'country': info.get('country'),
                    }
                except Exception as e:
                    results[sym] = {'symbol': sym, 'error': str(e)}
            self.wfile.write(json.dumps(results).encode())

        elif parsed.path == '/summary':
            symbol = params.get('symbol', [''])[0]
            if not symbol:
                self.wfile.write(json.dumps({'error': 'No symbol'}).encode())
                return
            try:
                t = yf.Ticker(symbol)
                info = t.info
                # Get calendar and earnings data
                cal = {}
                try:
                    cal_data = t.calendar
                    if cal_data is not None:
                        if isinstance(cal_data, dict):
                            for k, v in cal_data.items():
                                if isinstance(v, list):
                                    cal[k] = [str(item) for item in v]
                                elif hasattr(v, 'isoformat'):
                                    cal[k] = v.isoformat()
                                else:
                                    cal[k] = str(v)
                        else:
                            cal = {'data': str(cal_data)}
                except:
                    pass

                earnings_hist = []
                try:
                    eh = t.earnings_history
                    if eh is not None and hasattr(eh, 'to_dict'):
                        records = eh.reset_index().to_dict('records')
                        for r in records:
                            entry = {}
                            for rk, rv in r.items():
                                if hasattr(rv, 'isoformat'):
                                    entry[rk] = rv.isoformat()
                                elif hasattr(rv, 'item'):
                                    entry[rk] = rv.item()
                                else:
                                    entry[rk] = rv
                            earnings_hist.append(entry)
                except:
                    pass

                result = {
                    'info': {
                        'longName': info.get('longName'),
                        'price': info.get('currentPrice') or info.get('regularMarketPrice'),
                        'marketCap': info.get('marketCap'),
                        'enterpriseValue': info.get('enterpriseValue'),
                        'totalRevenue': info.get('totalRevenue'),
                        'totalCash': info.get('totalCash'),
                        'totalDebt': info.get('totalDebt'),
                        'freeCashflow': info.get('freeCashflow'),
                        'operatingCashflow': info.get('operatingCashflow'),
                        'targetMeanPrice': info.get('targetMeanPrice'),
                        'targetHighPrice': info.get('targetHighPrice'),
                        'targetLowPrice': info.get('targetLowPrice'),
                        'recommendationKey': info.get('recommendationKey'),
                        'numberOfAnalystOpinions': info.get('numberOfAnalystOpinions'),
                        'fiftyTwoWeekHigh': info.get('fiftyTwoWeekHigh'),
                        'fiftyTwoWeekLow': info.get('fiftyTwoWeekLow'),
                        'averageVolume': info.get('averageVolume'),
                        'volume': info.get('volume'),
                        'beta': info.get('beta'),
                        'forwardPE': info.get('forwardPE'),
                        'forwardEps': info.get('forwardEps'),
                        'trailingEps': info.get('trailingEps'),
                        'revenueGrowth': info.get('revenueGrowth'),
                        'earningsGrowth': info.get('earningsGrowth'),
                        'sharesOutstanding': info.get('sharesOutstanding'),
                        'enterpriseToRevenue': info.get('enterpriseToRevenue'),
                        'enterpriseToEbitda': info.get('enterpriseToEbitda'),
                        'operatingMargins': info.get('operatingMargins'),
                    },
                    'calendar': cal,
                    'earningsHistory': earnings_hist,
                }
                self.wfile.write(json.dumps(result, default=str).encode())
            except Exception as e:
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif parsed.path == '/quant-factors':
            symbol = params.get('symbol', [''])[0]
            if not symbol:
                self.wfile.write(json.dumps({'error': 'No symbol'}).encode())
                return
            result = compute_quant_factors(symbol)
            self.wfile.write(json.dumps(result, default=str).encode())

        elif parsed.path == '/short-interest':
            symbol = params.get('symbol', [''])[0]
            if not symbol:
                self.wfile.write(json.dumps({'error': 'No symbol'}).encode())
                return
            result = compute_short_interest(symbol)
            self.wfile.write(json.dumps(result, default=str).encode())

        elif parsed.path == '/sp500-outperformance':
            symbol = params.get('symbol', [''])[0]
            if not symbol:
                self.wfile.write(json.dumps({'error': 'No symbol'}).encode())
                return
            result = compute_sp500_outperformance(symbol)
            self.wfile.write(json.dumps(result, default=str).encode())

        elif parsed.path == '/cross-sector-comps':
            symbol = params.get('symbol', [''])[0]
            if not symbol:
                self.wfile.write(json.dumps({'error': 'No symbol'}).encode())
                return
            result = compute_cross_sector_comps(symbol)
            self.wfile.write(json.dumps(result, default=str).encode())

        elif parsed.path == '/lookup-private':
            name = params.get('name', [''])[0]
            if not name:
                self.wfile.write(json.dumps({'error': 'No company name'}).encode())
                return
            result = lookup_private_company(name)
            self.wfile.write(json.dumps(result, default=str).encode())

        elif parsed.path == '/chart':
            symbol = params.get('symbol', [''])[0]
            range_param = params.get('range', ['5y'])[0]
            interval_param = params.get('interval', ['1d'])[0]
            if not symbol:
                self.wfile.write(json.dumps({'error': 'No symbol'}).encode())
                return
            # Check cache
            cache_key = f'chart_{symbol}_{range_param}_{interval_param}'
            cached = cache_get(cache_key, 1800)  # 30 min cache
            if cached:
                self.wfile.write(json.dumps(cached).encode())
                return
            try:
                t = yf.Ticker(symbol)
                hist = t.history(period=range_param, interval=interval_param)
                if hist is None or len(hist) == 0:
                    self.wfile.write(json.dumps({'error': f'No data for {symbol}'}).encode())
                    return
                # Build response matching the format fetchChartData expects
                timestamps = [int(ts.timestamp()) for ts in hist.index]
                closes = hist['Close'].tolist()
                # Get meta from info (non-blocking, use cache)
                meta = {'symbol': symbol, 'currency': 'USD'}
                try:
                    info = t.info
                    meta.update({
                        'currency': info.get('currency', 'USD'),
                        'regularMarketPrice': info.get('currentPrice') or info.get('regularMarketPrice'),
                        'previousClose': info.get('previousClose'),
                        'chartPreviousClose': info.get('previousClose'),
                        'fiftyTwoWeekHigh': info.get('fiftyTwoWeekHigh'),
                        'fiftyTwoWeekLow': info.get('fiftyTwoWeekLow'),
                        'regularMarketVolume': info.get('volume'),
                        'longName': info.get('longName'),
                        'shortName': info.get('shortName'),
                    })
                except:
                    # If info fails, at least get price from history
                    valid_closes = [c for c in closes if c == c]
                    if valid_closes:
                        meta['regularMarketPrice'] = valid_closes[-1]

                def safe_float(v):
                    try:
                        f = float(v)
                        return f if f == f else None  # NaN check
                    except:
                        return None

                def safe_int(v):
                    try:
                        i = int(v)
                        return i if i == i else None
                    except:
                        return None

                result = {
                    'timestamps': timestamps,
                    'closes': [safe_float(v) for v in closes],
                    'opens': [safe_float(v) for v in hist['Open'].tolist()],
                    'highs': [safe_float(v) for v in hist['High'].tolist()],
                    'lows': [safe_float(v) for v in hist['Low'].tolist()],
                    'volumes': [safe_int(v) for v in hist['Volume'].tolist()],
                    'meta': meta,
                }
                cache_set(cache_key, result)
                self.wfile.write(json.dumps(result, default=str).encode())
            except Exception as e:
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif parsed.path == '/search':
            query = params.get('q', [''])[0].strip()
            if not query or len(query) < 1:
                self.wfile.write(json.dumps([]).encode())
                return
            try:
                sr = yf.Search(query, max_results=8)
                results = []
                seen = set()
                for q in (sr.quotes if hasattr(sr, 'quotes') else []):
                    sym = q.get('symbol', '')
                    if not sym or sym in seen:
                        continue
                    # Only include equities (filter out funds, options, futures, etc.)
                    qtype = q.get('quoteType', '').upper()
                    if qtype and qtype not in ('EQUITY', 'ETF', ''):
                        continue
                    seen.add(sym)
                    results.append({
                        'symbol': sym,
                        'name': q.get('shortname') or q.get('longname') or sym,
                        'exchange': q.get('exchange', ''),
                    })
                self.wfile.write(json.dumps(results).encode())
            except Exception as e:
                print(f'Search error: {e}')
                self.wfile.write(json.dumps([]).encode())

        elif parsed.path == '/news':
            # Fetch news for a comma-separated list of tickers
            symbols_str = params.get('symbols', [''])[0]
            if not symbols_str:
                self.wfile.write(json.dumps([]).encode())
                return
            symbols = [s.strip().upper() for s in symbols_str.split(',') if s.strip()]
            if not symbols:
                self.wfile.write(json.dumps([]).encode())
                return

            # Check cache (15 min TTL)
            cache_key = f'news_{"_".join(sorted(symbols))}'
            cached = cache_get(cache_key, 900)
            if cached:
                self.wfile.write(json.dumps(cached).encode())
                return

            all_news = []
            seen_ids = set()
            for sym in symbols[:20]:  # cap at 20 tickers to avoid overload
                try:
                    t = yf.Ticker(sym)
                    news_items = t.news or []
                    for item in news_items[:5]:  # max 5 per ticker
                        content = item.get('content', {})
                        item_id = content.get('id', '')
                        if item_id in seen_ids:
                            continue
                        seen_ids.add(item_id)

                        title = content.get('title', '')
                        if not title:
                            continue

                        # Get URL
                        click = content.get('clickThroughUrl', {}) or content.get('canonicalUrl', {})
                        url = click.get('url', '') if isinstance(click, dict) else ''

                        # Get provider
                        provider = content.get('provider', {})
                        source = provider.get('displayName', '') if isinstance(provider, dict) else ''

                        # Get pubDate
                        pub_date = content.get('pubDate', '') or content.get('displayTime', '')

                        # Get thumbnail
                        thumb = content.get('thumbnail', {})
                        thumb_url = ''
                        if isinstance(thumb, dict):
                            resolutions = thumb.get('resolutions', []) or []
                            if resolutions:
                                # pick smallest
                                thumb_url = resolutions[-1].get('url', '')
                            if not thumb_url:
                                thumb_url = thumb.get('originalUrl', '')

                        all_news.append({
                            'id': item_id,
                            'ticker': sym,
                            'title': title,
                            'url': url,
                            'source': source,
                            'pubDate': pub_date,
                            'thumbnail': thumb_url,
                        })
                except Exception as e:
                    print(f'News fetch error for {sym}: {e}')
                    continue

            # Sort by date descending
            all_news.sort(key=lambda x: x.get('pubDate', ''), reverse=True)
            # Limit to 40 total
            all_news = all_news[:40]
            cache_set(cache_key, all_news)
            self.wfile.write(json.dumps(all_news).encode())

        elif parsed.path == '/earnings':
            # Serve earnings calendar JSON
            try:
                import os
                earnings_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'earnings_calendar.json')
                if os.path.exists(earnings_file):
                    with open(earnings_file) as ef:
                        data = json.load(ef)
                    self.wfile.write(json.dumps(data).encode())
                else:
                    self.wfile.write(json.dumps({'error': 'No earnings data yet', 'upcoming': [], 'recent': []}).encode())
            except Exception as e:
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        elif parsed.path == '/earnings-note':
            # Serve a specific earnings note markdown file
            import os
            note_type = params.get('type', ['post'])[0]  # 'pre' or 'post'
            ticker = params.get('ticker', [''])[0].upper()
            date = params.get('date', [''])[0]
            if not ticker or not date:
                self.wfile.write(json.dumps({'error': 'ticker and date required'}).encode())
                return
            base_dir = os.path.dirname(os.path.abspath(__file__))
            note_path = os.path.join(base_dir, 'notes', f'{note_type}_earnings', f'{ticker}_{date}.md')
            archive_path = os.path.join(base_dir, 'archive', f'{note_type}_earnings', f'{ticker}_{date}.md')
            path_to_read = note_path if os.path.exists(note_path) else (archive_path if os.path.exists(archive_path) else None)
            if path_to_read:
                with open(path_to_read) as nf:
                    content = nf.read()
                self.wfile.write(json.dumps({'ticker': ticker, 'date': date, 'type': note_type, 'content': content}).encode())
            else:
                self.wfile.write(json.dumps({'error': f'Note not found: {ticker}_{date}'}).encode())

        elif parsed.path == '/health':
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        else:
            self.wfile.write(json.dumps({'error': 'Unknown endpoint'}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()


class ThreadedHTTPServer(HTTPServer):
    """Handle requests in separate threads for concurrent chart fetches."""
    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        t.daemon = True
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


if __name__ == '__main__':
    port = 5001
    server = ThreadedHTTPServer(('0.0.0.0', port), QuoteHandler)
    print(f'Backend running on port {port} (threaded)')
    server.serve_forever()
