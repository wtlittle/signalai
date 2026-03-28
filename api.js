/* ===== API.JS — Data fetching via backend proxy (all requests route through Python backend) ===== */

// Backend URL — uses relative path ./api which the Express server can proxy,
// or falls back to direct backend connection in local dev
const BACKEND_URL = (() => {
  const loc = window.location;
  // Local dev: backend on port 5001
  if (loc.hostname === '127.0.0.1' || loc.hostname === 'localhost') {
    return `${loc.protocol}//${loc.hostname}:5001`;
  }
  // Deployed: backend not available, will use client-side fallback
  return '';
})();
let backendAvailable = null; // null = untested, true/false after first check

// Fetch chart data (historical prices) — backend first, client fallback
async function fetchChartData(ticker, range = '5y', interval = '1d') {
  if (await checkBackend()) {
    try {
      const url = `${BACKEND_URL}/chart?symbol=${encodeURIComponent(ticker)}&range=${encodeURIComponent(range)}&interval=${encodeURIComponent(interval)}`;
      const resp = await fetch(url, { signal: AbortSignal.timeout(30000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      return data;
    } catch (e) {
      console.error(`Backend chart fetch failed for ${ticker}:`, e);
    }
  }
  // Client-side fallback
  if (typeof fetchChartDataClient === 'function') {
    return fetchChartDataClient(ticker, range, interval);
  }
  return null;
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

// Fetch fundamentals — backend first, client fallback
async function fetchQuotesBatch(tickers) {
  if (await checkBackend()) {
    try {
      const url = `${BACKEND_URL}/quote?symbols=${tickers.join(',')}`;
      const resp = await fetch(url, { signal: AbortSignal.timeout(90000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } catch (e) {
      console.error('Backend quote fetch failed:', e);
      backendAvailable = false;
    }
  }
  // Client-side fallback
  if (typeof fetchQuotesBatchClient === 'function') {
    return fetchQuotesBatchClient(tickers);
  }
  return {};
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
    row.currency = quote.currency || row.currency;
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

  // Performance calculations — prefer pre-computed returns from quote data (avoids chart fetch on load)
  if (quote && quote.change1d != null) {
    row.d1 = quote.change1d;
    row.w1 = quote.change1w ?? null;
    row.m1 = quote.change1m ?? null;
    row.m3 = quote.change3m ?? null;
    row.y1 = quote.change1y ?? null;
    row.y3 = quote.change3y ?? null;
    row.ytd = quote.changeYtd ?? null;
  } else {
    // Fallback: compute from chart close prices if available
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

// Fetch all tickers — quotes only on initial load (chart data lazy-loaded in popups)
async function fetchAllTickers(tickers, onProgress) {
  if (onProgress) onProgress(0, tickers.length);

  // Single batch fetch for all quotes — no chart data needed on initial load
  // Performance returns come from pre-computed quote fields (change_1d, etc.)
  const quotes = await fetchQuotesBatch(tickers);

  if (onProgress) onProgress(tickers.length, tickers.length);

  // Build rows from quote data only (no chart needed for table view)
  const results = {};
  tickers.forEach(ticker => {
    const quote = quotes[ticker];
    results[ticker] = parseTickerData(ticker, null, quote);
  });

  return results;
}
