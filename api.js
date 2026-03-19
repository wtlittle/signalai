/* ===== API.JS — Data fetching via backend proxy (all requests route through Python backend) ===== */

// Backend URL — uses __PORT_5001__ which deploy_website replaces with the proxy path
const BACKEND_URL = (() => {
  const marker = '__PORT_5001__';
  // If the marker was replaced by deploy_website, use the proxy path
  if (!marker.startsWith('__')) return marker;
  // Local dev: backend on port 5001
  const loc = window.location;
  if (loc.port === '5000') return 'http://127.0.0.1:5001';
  return `${loc.protocol}//${loc.hostname}:5001`;
})();
let backendAvailable = null; // null = untested, true/false after first check

// Fetch chart data (historical prices) via backend proxy
async function fetchChartData(ticker, range = '5y', interval = '1d') {
  if (!(await checkBackend())) {
    console.warn(`Chart fetch skipped for ${ticker}: backend not available`);
    return null;
  }
  try {
    const url = `${BACKEND_URL}/chart?symbol=${encodeURIComponent(ticker)}&range=${encodeURIComponent(range)}&interval=${encodeURIComponent(interval)}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(30000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    return data;
  } catch (e) {
    console.error(`Chart fetch failed for ${ticker}:`, e);
    return null;
  }
}

// Check if backend is available
async function checkBackend() {
  if (backendAvailable !== null) return backendAvailable;
  try {
    const resp = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(3000) });
    backendAvailable = resp.ok;
  } catch {
    backendAvailable = false;
  }
  console.log('Backend available:', backendAvailable);
  return backendAvailable;
}

// Fetch fundamentals from local backend (batch)
async function fetchQuotesBatch(tickers) {
  if (!(await checkBackend())) return {};
  try {
    const url = `${BACKEND_URL}/quote?symbols=${tickers.join(',')}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(90000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (e) {
    console.error('Backend quote fetch failed:', e);
    backendAvailable = false;
    return {};
  }
}

// Fetch detailed summary from local backend (single ticker)
async function fetchSummaryFromBackend(ticker) {
  if (!(await checkBackend())) return null;
  try {
    const url = `${BACKEND_URL}/summary?symbol=${ticker}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(30000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (e) {
    console.error(`Backend summary fetch failed for ${ticker}:`, e);
    return null;
  }
}

// Parse all data for a ticker into a flat row object
function parseTickerData(ticker, chart, quote) {
  const row = { ticker };

  // From chart meta
  const meta = chart?.meta || {};
  row.name = meta.longName || meta.shortName || ticker;
  row.price = meta.regularMarketPrice ?? null;
  row.currency = meta.currency || 'USD';
  row.previousClose = meta.chartPreviousClose ?? meta.previousClose ?? null;
  row.fiftyTwoWeekHigh = meta.fiftyTwoWeekHigh ?? null;
  row.fiftyTwoWeekLow = meta.fiftyTwoWeekLow ?? null;
  row.volume = meta.regularMarketVolume ?? null;

  // From backend quote data (richer data)
  if (quote) {
    row.name = quote.longName || row.name;
    row.price = quote.price ?? row.price;
    row.marketCap = quote.marketCap ?? null;
    row.enterpriseValue = quote.enterpriseValue ?? null;
    row.totalRevenue = quote.totalRevenue ?? null;
    row.totalCash = quote.totalCash ?? null;
    row.totalDebt = quote.totalDebt ?? null;
    row.freeCashflow = quote.freeCashflow ?? null;
    row.operatingCashflow = quote.operatingCashflow ?? null;
    row.targetMeanPrice = quote.targetMeanPrice ?? null;
    row.targetHighPrice = quote.targetHighPrice ?? null;
    row.targetLowPrice = quote.targetLowPrice ?? null;
    row.recommendationKey = quote.recommendationKey ?? null;
    row.numberOfAnalystOpinions = quote.numberOfAnalystOpinions ?? null;
    row.fiftyTwoWeekHigh = quote.fiftyTwoWeekHigh ?? row.fiftyTwoWeekHigh;
    row.fiftyTwoWeekLow = quote.fiftyTwoWeekLow ?? row.fiftyTwoWeekLow;
    row.averageVolume = quote.averageVolume ?? null;
    row.volume = quote.volume ?? row.volume;
    row.beta = quote.beta ?? null;
    row.forwardPE = quote.forwardPE ?? null;
    row.trailingPE = quote.trailingPE ?? null;
    row.sharesOutstanding = quote.sharesOutstanding ?? null;
    row.revenueGrowth = quote.revenueGrowth ?? null;
    row.earningsGrowth = quote.earningsGrowth ?? null;
    row.forwardEps = quote.forwardEps ?? null;
    row.trailingEps = quote.trailingEps ?? null;
    row.enterpriseToRevenue = quote.enterpriseToRevenue ?? null;
    row.enterpriseToEbitda = quote.enterpriseToEbitda ?? null;
    row.sector = quote.sector ?? null;
    row.industry = quote.industry ?? null;
    // Build headquarters string from city/state/country
    if (quote.city) {
      const parts = [quote.city];
      if (quote.state) parts.push(quote.state);
      else if (quote.country && quote.country !== 'United States') parts.push(quote.country);
      row.headquarters = parts.join(', ');
    }
  }

  // Fallback to static HQ map if backend didn't provide location
  if (!row.headquarters && typeof COMPANY_HQ !== 'undefined' && COMPANY_HQ[ticker]) {
    row.headquarters = COMPANY_HQ[ticker];
  }

  // EV
  row.ev = row.enterpriseValue;
  if (!row.ev && row.marketCap) {
    row.ev = row.marketCap + (row.totalDebt || 0) - (row.totalCash || 0);
  }

  // FY1 EV/Sales
  row.evSales = (row.ev && row.totalRevenue && row.totalRevenue > 0) ? row.ev / row.totalRevenue : null;
  if (!row.evSales && row.enterpriseToRevenue) row.evSales = row.enterpriseToRevenue;

  // FY1 EV/FCF
  let fcf = row.freeCashflow;
  if (!fcf && row.operatingCashflow) fcf = row.operatingCashflow * 0.85;
  row.evFcf = (row.ev && fcf && fcf > 0) ? row.ev / fcf : null;

  // Performance calculations from chart data
  const closes = chart?.closes || [];
  const timestamps = chart?.timestamps || [];
  const len = closes.length;

  const validCloses = [];
  const validTimestamps = [];
  for (let i = 0; i < len; i++) {
    if (closes[i] != null) {
      validCloses.push(closes[i]);
      validTimestamps.push(timestamps[i]);
    }
  }
  const vlen = validCloses.length;

  if (vlen > 0) {
    const current = row.price || validCloses[vlen - 1];
    const findClose = (daysBack) => {
      if (daysBack >= vlen) return validCloses[0];
      return validCloses[vlen - 1 - daysBack];
    };
    const calcReturn = (prev) => {
      if (!prev || !current) return null;
      return ((current - prev) / prev) * 100;
    };

    row.d1 = calcReturn(findClose(1));
    row.w1 = calcReturn(findClose(5));
    row.m1 = calcReturn(findClose(21));
    row.m3 = calcReturn(findClose(63));
    row.y1 = calcReturn(findClose(252));
    row.y3 = calcReturn(findClose(756));

    // YTD
    const now = new Date();
    const yearStart = new Date(now.getFullYear(), 0, 1).getTime() / 1000;
    let ytdPrice = null;
    for (let i = 0; i < validTimestamps.length; i++) {
      if (validTimestamps[i] >= yearStart) {
        ytdPrice = validCloses[i > 0 ? i - 1 : 0];
        break;
      }
    }
    row.ytd = calcReturn(ytdPrice);
  }

  // Subsector — auto-classify from sector/industry if not already mapped
  row.subsector = getSubsector(ticker);
  if (row.subsector === 'Other' && (row.sector || row.industry)) {
    const autoSub = autoClassifySubsector(row.sector, row.industry);
    if (autoSub) {
      row.subsector = autoSub;
      setSubsectorOverride(ticker, autoSub);
      // Add to SUBSECTOR_ORDER if new
      if (typeof SUBSECTOR_ORDER !== 'undefined' && !SUBSECTOR_ORDER.includes(autoSub)) {
        SUBSECTOR_ORDER.push(autoSub);
      }
    }
  }

  return row;
}

// Fetch all data for a single ticker (used when adding one ticker)
async function fetchTickerFull(ticker) {
  const [chart, quotes] = await Promise.all([
    fetchChartData(ticker, '5y', '1d'),
    fetchQuotesBatch([ticker]),
  ]);
  return parseTickerData(ticker, chart, quotes[ticker]);
}

// Fetch all tickers — chart data batched, fundamentals batched via backend
async function fetchAllTickers(tickers, onProgress) {
  // First, fetch all fundamentals in one batch from backend
  if (onProgress) onProgress(0, tickers.length);

  // Start fundamentals fetch (single HTTP call for all tickers)
  const quotesPromise = fetchQuotesBatch(tickers);

  // Fetch chart data in parallel batches
  const chartResults = {};
  const batchSize = 5;
  for (let i = 0; i < tickers.length; i += batchSize) {
    const batch = tickers.slice(i, i + batchSize);
    const batchResults = await Promise.allSettled(
      batch.map(t => fetchChartData(t, '5y', '1d'))
    );
    batchResults.forEach((r, idx) => {
      chartResults[batch[idx]] = r.status === 'fulfilled' ? r.value : null;
    });
    if (onProgress) onProgress(Math.min(i + batchSize, tickers.length), tickers.length);
  }

  // Await fundamentals
  const quotes = await quotesPromise;

  // Merge
  const results = {};
  tickers.forEach(ticker => {
    const chart = chartResults[ticker];
    const quote = quotes[ticker];
    results[ticker] = parseTickerData(ticker, chart, quote);
  });

  return results;
}
