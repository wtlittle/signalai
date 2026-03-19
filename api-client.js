/* ===== API-CLIENT.JS — Client-side data fallback (no backend needed) ===== */
/* Used when the Python backend is not available (e.g., GitHub Pages demo) */
/* Strategy: try CORS proxy to Yahoo Finance first, fall back to cached snapshot */

const YF_CHART_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart/';
const CORS_PROXIES = [
  'https://api.allorigins.win/raw?url=',
  'https://corsproxy.io/?url=',
];
let activeProxyIndex = 0;
let _proxyTestDone = false;
let _proxyWorking = false;

// --- Snapshot cache ---
let _snapshotData = null;
let _snapshotLoading = null;

async function loadSnapshot() {
  if (_snapshotData) return _snapshotData;
  if (_snapshotLoading) return _snapshotLoading;
  _snapshotLoading = (async () => {
    try {
      const resp = await fetch('data-snapshot.json', { signal: AbortSignal.timeout(10000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      _snapshotData = await resp.json();
      console.log(`Loaded snapshot: ${Object.keys(_snapshotData.tickers || {}).length} tickers, generated ${_snapshotData.generated}`);
      return _snapshotData;
    } catch (e) {
      console.warn('Failed to load data snapshot:', e.message);
      return null;
    }
  })();
  return _snapshotLoading;
}

function getProxy() {
  return CORS_PROXIES[activeProxyIndex] || CORS_PROXIES[0];
}

function rotateProxy() {
  activeProxyIndex = (activeProxyIndex + 1) % CORS_PROXIES.length;
}

// Test which proxy works best at startup
async function ensureBestProxy() {
  if (_proxyTestDone) return _proxyWorking;
  _proxyTestDone = true;
  const testUrl = 'https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d';
  for (let i = 0; i < CORS_PROXIES.length; i++) {
    try {
      const resp = await fetch(CORS_PROXIES[i] + encodeURIComponent(testUrl), { signal: AbortSignal.timeout(6000) });
      if (resp.ok) {
        activeProxyIndex = i;
        _proxyWorking = true;
        console.log(`CORS proxy working: ${CORS_PROXIES[i]}`);
        return true;
      }
    } catch (e) {}
  }
  console.log('No CORS proxy available, using snapshot data');
  _proxyWorking = false;
  return false;
}

async function fetchWithProxy(url, timeout = 15000) {
  const proxyAvailable = await ensureBestProxy();
  if (!proxyAvailable) throw new Error('No CORS proxy available');

  const proxyUrl = getProxy() + encodeURIComponent(url);
  try {
    const resp = await fetch(proxyUrl, { signal: AbortSignal.timeout(timeout) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (e) {
    // Try next proxy
    rotateProxy();
    const proxyUrl2 = getProxy() + encodeURIComponent(url);
    const resp2 = await fetch(proxyUrl2, { signal: AbortSignal.timeout(timeout) });
    if (!resp2.ok) throw new Error(`HTTP ${resp2.status}`);
    return await resp2.json();
  }
}

// --- Client-side chart data fetch ---
async function fetchChartDataClient(ticker, range = '5y', interval = '1d') {
  // Try live CORS proxy first
  try {
    const url = `${YF_CHART_BASE}${encodeURIComponent(ticker)}?range=${range}&interval=${interval}&includePrePost=false`;
    const data = await fetchWithProxy(url, 20000);
    const result = data?.chart?.result?.[0];
    if (!result) throw new Error('No result');

    const meta = result.meta || {};
    const timestamps = result.timestamp || [];
    const closes = result.indicators?.quote?.[0]?.close || [];

    return {
      meta: {
        longName: meta.longName || meta.shortName || ticker,
        shortName: meta.shortName || ticker,
        regularMarketPrice: meta.regularMarketPrice,
        chartPreviousClose: meta.chartPreviousClose,
        previousClose: meta.previousClose,
        fiftyTwoWeekHigh: meta.fiftyTwoWeekHigh,
        fiftyTwoWeekLow: meta.fiftyTwoWeekLow,
        regularMarketVolume: meta.regularMarketVolume,
        currency: meta.currency,
      },
      timestamps,
      closes,
    };
  } catch (e) {
    // Fall back to snapshot
    const snap = await loadSnapshot();
    if (snap?.tickers?.[ticker]) {
      const td = snap.tickers[ticker];
      return {
        meta: td.meta,
        timestamps: td.timestamps,
        closes: td.closes,
        _fromSnapshot: true,
      };
    }
    console.warn(`Client chart fetch failed for ${ticker}:`, e.message);
    return null;
  }
}

// --- Client-side quote data fetch (from snapshot) ---
async function fetchQuoteDataClient(ticker) {
  // Yahoo v10 quoteSummary now requires auth — go straight to snapshot
  const snap = await loadSnapshot();
  if (snap?.quotes?.[ticker]) {
    return snap.quotes[ticker];
  }
  return null;
}

// --- Client-side batch quote fetch ---
async function fetchQuotesBatchClient(tickers) {
  const snap = await loadSnapshot();
  const results = {};

  // If proxy works, try to get live prices from chart API and merge with snapshot quotes
  const proxyAvailable = await ensureBestProxy();

  if (proxyAvailable) {
    // Attempt live price updates in batches
    const batchSize = 5;
    for (let i = 0; i < tickers.length; i += batchSize) {
      const batch = tickers.slice(i, i + batchSize);
      const batchResults = await Promise.allSettled(
        batch.map(async (t) => {
          const url = `${YF_CHART_BASE}${encodeURIComponent(t)}?range=5d&interval=1d&includePrePost=false`;
          const data = await fetchWithProxy(url, 12000);
          const result = data?.chart?.result?.[0];
          if (!result) return null;
          const meta = result.meta || {};
          return {
            ticker: t,
            longName: meta.longName || meta.shortName || t,
            price: meta.regularMarketPrice,
            previousClose: meta.previousClose,
            fiftyTwoWeekHigh: meta.fiftyTwoWeekHigh,
            fiftyTwoWeekLow: meta.fiftyTwoWeekLow,
            currency: meta.currency,
          };
        })
      );
      batchResults.forEach((r, idx) => {
        const t = batch[idx];
        if (r.status === 'fulfilled' && r.value) {
          // Merge live price with snapshot fundamentals
          const snapQuote = snap?.quotes?.[t] || {};
          results[t] = { ...snapQuote, ...r.value };
        } else if (snap?.quotes?.[t]) {
          results[t] = snap.quotes[t];
        }
      });
      if (i + batchSize < tickers.length) {
        await new Promise(r => setTimeout(r, 200));
      }
    }
  } else {
    // No proxy — use snapshot for everything
    for (const t of tickers) {
      if (snap?.quotes?.[t]) {
        results[t] = snap.quotes[t];
      }
    }
  }

  return results;
}

// --- Client-side ticker search ---
async function searchTickerClient(query) {
  try {
    const url = `https://query2.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(query)}&quotesCount=8&newsCount=0`;
    const data = await fetchWithProxy(url, 8000);
    return (data.quotes || []).filter(q => q.quoteType === 'EQUITY').map(q => ({
      symbol: q.symbol,
      name: q.shortname || q.longname || q.symbol,
      exchange: q.exchange,
    }));
  } catch (e) {
    console.warn('Client search failed:', e.message);
    return [];
  }
}

// --- Client-side news fetch ---
async function fetchNewsClient(tickers) {
  try {
    const topTickers = tickers.slice(0, 5).join(',');
    const url = `https://query2.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(topTickers)}&quotesCount=0&newsCount=20`;
    const data = await fetchWithProxy(url, 10000);
    return (data.news || []).map(n => ({
      title: n.title,
      url: n.link || n.url || '#',
      source: n.publisher || 'Yahoo Finance',
      publishedAt: n.providerPublishTime ? new Date(n.providerPublishTime * 1000).toISOString() : null,
    }));
  } catch (e) {
    console.warn('Client news fetch failed:', e.message);
    return [];
  }
}

// --- Check if running in static/demo mode ---
function isStaticDemo() {
  return typeof backendAvailable !== 'undefined' && backendAvailable === false;
}
