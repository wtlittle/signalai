/* ===== API-CLIENT.JS — Client-side Yahoo Finance fallback (no backend needed) ===== */
/* Used when the Python backend is not available (e.g., GitHub Pages demo) */

const YF_CHART_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart/';
const YF_QUOTE_BASE = 'https://query2.finance.yahoo.com/v10/finance/quoteSummary/';
const CORS_PROXIES = [
  'https://api.allorigins.win/raw?url=',
  'https://corsproxy.io/?url=',
];
let activeProxyIndex = 0;

function getProxy() {
  return CORS_PROXIES[activeProxyIndex] || CORS_PROXIES[0];
}

function rotateProxy() {
  activeProxyIndex = (activeProxyIndex + 1) % CORS_PROXIES.length;
}

async function fetchWithProxy(url, timeout = 15000) {
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
  try {
    const url = `${YF_CHART_BASE}${encodeURIComponent(ticker)}?range=${range}&interval=${interval}&includePrePost=false`;
    const data = await fetchWithProxy(url, 20000);
    const result = data?.chart?.result?.[0];
    if (!result) return null;

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
    console.warn(`Client chart fetch failed for ${ticker}:`, e.message);
    return null;
  }
}

// --- Client-side quote data fetch ---
async function fetchQuoteDataClient(ticker) {
  try {
    const modules = 'price,summaryDetail,defaultKeyStatistics,financialData,assetProfile';
    const url = `${YF_QUOTE_BASE}${encodeURIComponent(ticker)}?modules=${modules}`;
    const data = await fetchWithProxy(url, 15000);
    const result = data?.quoteSummary?.result?.[0];
    if (!result) return null;

    const price = result.price || {};
    const summary = result.summaryDetail || {};
    const stats = result.defaultKeyStatistics || {};
    const fin = result.financialData || {};
    const profile = result.assetProfile || {};

    const getRaw = (obj) => obj?.raw ?? obj ?? null;

    return {
      longName: price.longName || price.shortName || ticker,
      price: getRaw(price.regularMarketPrice),
      marketCap: getRaw(price.marketCap),
      enterpriseValue: getRaw(stats.enterpriseValue),
      totalRevenue: getRaw(fin.totalRevenue),
      totalCash: getRaw(fin.totalCash),
      totalDebt: getRaw(fin.totalDebt),
      freeCashflow: getRaw(fin.freeCashflow),
      operatingCashflow: getRaw(fin.operatingCashflow),
      targetMeanPrice: getRaw(fin.targetMeanPrice),
      targetHighPrice: getRaw(fin.targetHighPrice),
      targetLowPrice: getRaw(fin.targetLowPrice),
      recommendationKey: fin.recommendationKey,
      numberOfAnalystOpinions: getRaw(fin.numberOfAnalystOpinions),
      fiftyTwoWeekHigh: getRaw(summary.fiftyTwoWeekHigh),
      fiftyTwoWeekLow: getRaw(summary.fiftyTwoWeekLow),
      averageVolume: getRaw(summary.averageVolume),
      volume: getRaw(summary.volume),
      beta: getRaw(summary.beta),
      forwardPE: getRaw(summary.forwardPE),
      trailingPE: getRaw(summary.trailingPE),
      sharesOutstanding: getRaw(stats.sharesOutstanding),
      revenueGrowth: getRaw(fin.revenueGrowth),
      earningsGrowth: getRaw(fin.earningsGrowth),
      forwardEps: getRaw(stats.forwardEps),
      trailingEps: getRaw(stats.trailingEps),
      enterpriseToRevenue: getRaw(stats.enterpriseToRevenue),
      enterpriseToEbitda: getRaw(stats.enterpriseToEbitda),
      operatingMargins: getRaw(fin.operatingMargins),
      sector: profile.sector || null,
      industry: profile.industry || null,
      city: profile.city || null,
      state: profile.state || null,
      country: profile.country || null,
    };
  } catch (e) {
    console.warn(`Client quote fetch failed for ${ticker}:`, e.message);
    return null;
  }
}

// --- Client-side batch quote fetch (sequential with small delay to avoid rate limits) ---
async function fetchQuotesBatchClient(tickers) {
  const results = {};
  const batchSize = 3;
  for (let i = 0; i < tickers.length; i += batchSize) {
    const batch = tickers.slice(i, i + batchSize);
    const batchResults = await Promise.allSettled(
      batch.map(t => fetchQuoteDataClient(t))
    );
    batchResults.forEach((r, idx) => {
      if (r.status === 'fulfilled' && r.value) {
        results[batch[idx]] = r.value;
      }
    });
    // Small delay between batches to avoid rate limiting
    if (i + batchSize < tickers.length) {
      await new Promise(r => setTimeout(r, 300));
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
    // Use Yahoo Finance RSS or search news for top tickers
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
