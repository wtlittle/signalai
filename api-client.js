/* ===== API-CLIENT.JS — Client-side data layer ===== */
/* Strategy: Supabase (primary) → CORS proxy (live prices) → cached snapshot (fallback) */

// --- Supabase configuration ---
const SUPABASE_URL = 'https://wcyirdvvuetzodiedzss.supabase.co';
const SUPABASE_KEY = 'sb_publishable_VOT04H1B4O7dVBqxTOk5rw_lyYBR9SW';

async function supabaseGet(table, params = '', maxRows = 1000) {
  try {
    const headers = { 'apikey': SUPABASE_KEY };
    // For queries that may exceed 1000 rows, use offset pagination.
    // BUG FIX (2026-05-06): the prior `while (offset < maxRows)` guard with
    // pageSize=1000 capped the result at exactly maxRows even when more rows
    // existed (e.g. DOCN chart_data had 2508 rows but only 2000 were
    // returned, truncating the series at 2025-04-07 and producing a bogus
    // +442.9% 1D return in the popup). The loop now keeps paginating until a
    // short page comes back, with maxRows acting purely as a safety cap on
    // the cumulative row count.
    if (maxRows > 1000) {
      let allRows = [];
      let offset = 0;
      const pageSize = 1000;
      // Strip any caller-supplied limit/offset so PostgREST honours our paging.
      const cleanParams = params
        .split('&')
        .filter(p => p && !/^limit=/i.test(p) && !/^offset=/i.test(p))
        .join('&');
      while (true) {
        const url = `${SUPABASE_URL}/rest/v1/${table}?${cleanParams}&limit=${pageSize}&offset=${offset}`;
        const resp = await fetch(url, { headers, signal: AbortSignal.timeout(15000) });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const page = await resp.json();
        if (!page || page.length === 0) break;
        allRows = allRows.concat(page);
        if (page.length < pageSize) break; // last page
        if (allRows.length >= maxRows) break; // safety cap
        offset += pageSize;
      }
      return allRows;
    }
    const url = `${SUPABASE_URL}/rest/v1/${table}?${params}`;
    const resp = await fetch(url, { headers, signal: AbortSignal.timeout(15000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (e) {
    console.warn(`Supabase ${table} fetch failed:`, e.message);
    return null;
  }
}

// --- Supabase connection test (deduped: single in-flight request) ---
let _supabaseAvailable = null;
let _supabaseCheckPromise = null;
let _supabaseGenerated = null;

async function checkSupabase() {
  if (_supabaseAvailable !== null) return _supabaseAvailable;
  if (_supabaseCheckPromise) return _supabaseCheckPromise;
  _supabaseCheckPromise = (async () => {
    try {
      const data = await supabaseGet('metadata', 'select=key,value&key=eq.generated');
      _supabaseAvailable = !!(data && data.length > 0);
      if (_supabaseAvailable) {
        console.log('Supabase connected, data generated:', data[0].value);
        _supabaseGenerated = data[0].value;
      }
    } catch {
      _supabaseAvailable = false;
    }
    _supabaseCheckPromise = null;
    return _supabaseAvailable;
  })();
  return _supabaseCheckPromise;
}

// --- CORS proxy for live chart prices (best-effort enhancement) ---
const YF_CHART_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart/';
const CORS_PROXIES = [
  'https://api.allorigins.win/raw?url=',
  'https://corsproxy.io/?url=',
];
let activeProxyIndex = 0;
let _proxyTestDone = false;
let _proxyWorking = false;

// --- Snapshot cache (last-resort fallback) ---
let _snapshotData = null;
let _snapshotLoading = null;

async function loadSnapshot() {
  if (_snapshotData) return _snapshotData;
  if (_snapshotLoading) return _snapshotLoading;
  _snapshotLoading = (async () => {
    try {
      let resp;
      if (window.SignalSnapshot && window.SignalSnapshot.fetchWithFallback) {
        resp = await window.SignalSnapshot.fetchWithFallback('data-snapshot.json', { timeoutMs: 15000, cacheBust: true });
      } else {
        resp = await fetch('data-snapshot.json', { signal: AbortSignal.timeout(10000) });
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      _snapshotData = await resp.json();
      console.log(`Loaded snapshot: ${Object.keys(_snapshotData.tickers || {}).length} tickers, generated ${_snapshotData.generated}`);
      return _snapshotData;
    } catch (e) {
      console.warn('Failed to load data snapshot:', e.message);
      // fetchWithFallback marks failure internally when both primary + fallback fail.
      return null;
    }
  })();
  return _snapshotLoading;
}

function getProxy() { return CORS_PROXIES[activeProxyIndex] || CORS_PROXIES[0]; }
function rotateProxy() { activeProxyIndex = (activeProxyIndex + 1) % CORS_PROXIES.length; }

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
  console.log('No CORS proxy available, using Supabase/snapshot data');
  _proxyWorking = false;
  return false;
}

// parse: 'json' (default) or 'text'. Tries the active proxy, then rotates and
// retries once on failure.
async function fetchViaProxy(url, timeout = 15000, parse = 'json') {
  const proxyAvailable = await ensureBestProxy();
  if (!proxyAvailable) throw new Error('No CORS proxy available');
  const read = (resp) => (parse === 'text' ? resp.text() : resp.json());
  try {
    const resp = await fetch(getProxy() + encodeURIComponent(url), { signal: AbortSignal.timeout(timeout) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await read(resp);
  } catch (e) {
    rotateProxy();
    const resp2 = await fetch(getProxy() + encodeURIComponent(url), { signal: AbortSignal.timeout(timeout) });
    if (!resp2.ok) throw new Error(`HTTP ${resp2.status}`);
    return await read(resp2);
  }
}

function fetchWithProxy(url, timeout = 15000) { return fetchViaProxy(url, timeout, 'json'); }
function fetchTextWithProxy(url, timeout = 15000) { return fetchViaProxy(url, timeout, 'text'); }

// --- Client-side chart data fetch ---
// Priority: Supabase chart_data → CORS proxy → snapshot
async function fetchChartDataClient(ticker, range = '5y', interval = '1d') {
  // 1. Try Supabase
  if (await checkSupabase()) {
    try {
      const [metaArr, points] = await Promise.all([
        supabaseGet('chart_meta', `select=*&ticker=eq.${encodeURIComponent(ticker)}`),
        // 5Y daily series can run ~2500+ bars; pass a high maxRows cap and let
        // the paginator stop on the first short page rather than truncating.
        supabaseGet('chart_data', `select=ts,close&ticker=eq.${encodeURIComponent(ticker)}&order=ts.asc`, 5000),
      ]);
      if (metaArr?.length && points?.length) {
        const m = metaArr[0];
        return {
          meta: {
            longName: m.long_name || ticker,
            shortName: m.short_name || ticker,
            regularMarketPrice: m.regular_market_price,
            chartPreviousClose: m.chart_previous_close,
            previousClose: m.previous_close,
            fiftyTwoWeekHigh: m.fifty_two_week_high,
            fiftyTwoWeekLow: m.fifty_two_week_low,
            regularMarketVolume: m.regular_market_volume,
            currency: m.currency,
          },
          timestamps: points.map(p => p.ts),
          closes: points.map(p => p.close),
          _fromSupabase: true,
        };
      }
    } catch (e) {
      console.warn(`Supabase chart fetch failed for ${ticker}:`, e.message);
    }
  }

  // 2. Try live CORS proxy
  try {
    const url = `${YF_CHART_BASE}${encodeURIComponent(ticker)}?range=${range}&interval=${interval}&includePrePost=false`;
    const data = await fetchWithProxy(url, 20000);
    const result = data?.chart?.result?.[0];
    if (!result) throw new Error('No result');
    const meta = result.meta || {};
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
      timestamps: result.timestamp || [],
      closes: result.indicators?.quote?.[0]?.close || [],
    };
  } catch (e) {
    // 3. Fall back to snapshot
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

// --- Client-side quote data fetch ---
// Priority: Supabase quotes → snapshot
async function fetchQuoteDataClient(ticker) {
  if (await checkSupabase()) {
    const data = await supabaseGet('quotes', `select=*&ticker=eq.${encodeURIComponent(ticker)}`);
    if (data?.length) return mapQuoteRow(data[0]);
  }
  const snap = await loadSnapshot();
  if (snap?.quotes?.[ticker]) return snap.quotes[ticker];
  return null;
}

// Map Supabase snake_case quote row to camelCase for app compatibility
function mapQuoteRow(r) {
  return {
    ticker: r.ticker, longName: r.long_name, price: r.price,
    previousClose: r.previous_close,
    fiftyTwoWeekHigh: r.fifty_two_week_high, fiftyTwoWeekLow: r.fifty_two_week_low,
    currency: r.currency,
    change1d: r.change_1d, change1w: r.change_1w, change1m: r.change_1m,
    change3m: r.change_3m, change1y: r.change_1y, change3y: r.change_3y,
    changeYtd: r.change_ytd,
    marketCap: r.market_cap, enterpriseValue: r.enterprise_value,
    totalRevenue: r.total_revenue, totalCash: r.total_cash,
    totalDebt: r.total_debt, freeCashflow: r.free_cashflow,
    operatingCashflow: r.operating_cashflow,
    targetMeanPrice: r.target_mean_price, targetHighPrice: r.target_high_price,
    targetLowPrice: r.target_low_price, recommendationKey: r.recommendation_key,
    numberOfAnalystOpinions: r.number_of_analyst_opinions,
    averageVolume: r.average_volume, volume: r.volume,
    beta: r.beta, forwardPE: r.forward_pe, trailingPE: r.trailing_pe,
    sharesOutstanding: r.shares_outstanding,
    revenueGrowth: r.revenue_growth, earningsGrowth: r.earnings_growth,
    forwardEps: r.forward_eps, trailingEps: r.trailing_eps,
    enterpriseToRevenue: r.enterprise_to_revenue, enterpriseToEbitda: r.enterprise_to_ebitda,
    operatingMargins: r.operating_margins,
    sector: r.sector, industry: r.industry,
    city: r.city, state: r.state, country: r.country,
  };
}

// --- Client-side batch quote fetch ---
async function fetchQuotesBatchClient(tickers) {
  const results = {};

  // 1. Try Supabase (all quotes in one call)
  if (await checkSupabase()) {
    const data = await supabaseGet('quotes', `select=*&ticker=in.(${tickers.map(t => encodeURIComponent(t)).join(',')})`);
    if (data?.length) {
      data.forEach(r => { results[r.ticker] = mapQuoteRow(r); });
      // If we got all tickers, return immediately
      if (Object.keys(results).length >= tickers.length * 0.9) {
        return results;
      }
    }
  }

  // 2. Enhance with live prices from CORS proxy if available
  const proxyAvailable = await ensureBestProxy();
  const missingTickers = tickers.filter(t => !results[t]);

  if (proxyAvailable && missingTickers.length > 0) {
    const snap = await loadSnapshot();
    const batchSize = 5;
    for (let i = 0; i < missingTickers.length; i += batchSize) {
      const batch = missingTickers.slice(i, i + batchSize);
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
          const snapQuote = snap?.quotes?.[t] || {};
          results[t] = { ...snapQuote, ...r.value };
        } else if (!results[t] && snap?.quotes?.[t]) {
          results[t] = snap.quotes[t];
        }
      });
      if (i + batchSize < missingTickers.length) {
        await new Promise(r => setTimeout(r, 200));
      }
    }
  } else if (missingTickers.length > 0) {
    // Snapshot fallback for any remaining
    const snap = await loadSnapshot();
    for (const t of missingTickers) {
      if (!results[t] && snap?.quotes?.[t]) {
        results[t] = snap.quotes[t];
      }
    }
  }

  return results;
}

// --- Supabase-backed data accessors for popup/deep-dive views ---

async function fetchShortInterestClient(ticker) {
  if (await checkSupabase()) {
    const data = await supabaseGet('short_interest', `select=*&ticker=eq.${encodeURIComponent(ticker)}`);
    if (data?.length) {
      const r = data[0];
      return {
        ticker: r.ticker,
        current: {
          sharesShort: r.current_shares_short,
          shortPercentOfFloat: r.current_short_pct_float,
          shortRatio: r.current_short_ratio,
          date: r.current_date_val,
        },
        priorMonth: {
          sharesShort: r.prior_shares_short,
          date: r.prior_date,
        },
        sharesOutstanding: r.shares_outstanding,
        floatShares: r.float_shares,
        change: r.change_pct,
      };
    }
  }
  const snap = await loadSnapshot();
  return snap?.short_interest?.[ticker] || null;
}

async function fetchOutperformanceClient(ticker) {
  if (await checkSupabase()) {
    const data = await supabaseGet('outperformance', `select=date,percentile,stock_count&ticker=eq.${encodeURIComponent(ticker)}&order=date.asc`);
    if (data?.length) {
      return {
        ticker,
        data: data.map(d => ({ date: d.date, percentile: d.percentile })),
        stockCount: data[0].stock_count,
      };
    }
  }
  const snap = await loadSnapshot();
  return snap?.outperformance?.[ticker] || null;
}

async function fetchCrossSectorCompsClient(ticker) {
  if (await checkSupabase()) {
    const [targetArr, comps] = await Promise.all([
      supabaseGet('cross_sector_targets', `select=*&ticker=eq.${encodeURIComponent(ticker)}`),
      supabaseGet('cross_sector_comps', `select=*&target_ticker=eq.${encodeURIComponent(ticker)}`),
    ]);
    if (targetArr?.length && comps?.length) {
      const t = targetArr[0];
      return {
        ticker,
        sector: t.sector,
        target: {
          ticker: t.ticker, name: t.name, sector: t.sector, industry: t.industry,
          marketCap: t.market_cap, forwardPE: t.forward_pe,
          operatingMargins: t.operating_margins, revenueGrowth: t.revenue_growth,
          enterpriseToRevenue: t.enterprise_to_revenue, enterpriseToEbitda: t.enterprise_to_ebitda,
          beta: t.beta, fcfMargin: t.fcf_margin,
        },
        comps: comps.map(c => ({
          ticker: c.comp_ticker, name: c.name, sector: c.sector, industry: c.industry,
          marketCap: c.market_cap, forwardPE: c.forward_pe,
          operatingMargins: c.operating_margins, revenueGrowth: c.revenue_growth,
          enterpriseToRevenue: c.enterprise_to_revenue, enterpriseToEbitda: c.enterprise_to_ebitda,
          beta: c.beta, fcfMargin: c.fcf_margin, similarity: c.similarity,
        })),
      };
    }
  }
  const snap = await loadSnapshot();
  return snap?.cross_sector_comps?.[ticker] || null;
}

async function fetchAnalystSummaryClient(ticker) {
  let supaResult = null;
  if (await checkSupabase()) {
    const data = await supabaseGet('analyst_summary', `select=*&ticker=eq.${encodeURIComponent(ticker)}`);
    if (data?.length) {
      const r = data[0];
      supaResult = {
        targetMeanPrice: r.target_mean_price, targetHighPrice: r.target_high_price,
        targetLowPrice: r.target_low_price, targetMedianPrice: r.target_median_price,
        numberOfAnalystOpinions: r.number_of_analyst_opinions,
        recommendationKey: r.recommendation_key, recommendationMean: r.recommendation_mean,
        forwardEps: r.forward_eps, trailingEps: r.trailing_eps,
        forwardPE: r.forward_pe, trailingPE: r.trailing_pe,
        beta: r.beta, averageVolume: r.average_volume,
        averageVolume10days: r.average_volume_10days, volume: r.volume,
        fiftyTwoWeekHigh: r.fifty_two_week_high, fiftyTwoWeekLow: r.fifty_two_week_low,
        sharesOutstanding: r.shares_outstanding,
        calendar: r.calendar, earningsHistory: r.earnings_history,
      };
    }
  }
  // Get snapshot data (always, to fill gaps from Supabase staleness)
  const snap = await loadSnapshot();
  const snapResult = snap?.analyst_summary?.[ticker] || null;
  if (!supaResult) return snapResult;
  if (!snapResult) return supaResult;
  // Merge: Supabase values take priority, snapshot fills gaps
  const merged = { ...snapResult, ...supaResult };
  return merged;
}

// --- Client-side estimates data fetch ---
async function fetchEstimatesClient(ticker) {
  let supaResult = null;
  // Try Supabase first (if estimates_data table exists)
  if (await checkSupabase()) {
    const data = await supabaseGet('estimates_data', `select=*&ticker=eq.${encodeURIComponent(ticker)}`);
    if (data?.length) {
      const r = data[0];
      supaResult = {
        revenueLtm: r.revenue_ltm, revenueGrowth: r.revenue_growth,
        grossMargins: r.gross_margins, operatingMargins: r.operating_margins,
        fcf: r.fcf, fcfMargin: r.fcf_margin,
        nextQRevEst: r.next_q_rev_est, nextQRevGrowth: r.next_q_rev_growth,
        nextQEpsEst: r.next_q_eps_est, nextQEpsGrowth: r.next_q_eps_growth,
        fy1RevEst: r.fy1_rev_est, fy1RevGrowth: r.fy1_rev_growth,
        fy1EpsEst: r.fy1_eps_est, fy1EpsGrowth: r.fy1_eps_growth,
        fy2RevEst: r.fy2_rev_est, fy2RevGrowth: r.fy2_rev_growth,
        fy2EpsEst: r.fy2_eps_est, fy2EpsGrowth: r.fy2_eps_growth,
        guideRevHigh: r.guide_rev_high, guideRevLow: r.guide_rev_low,
        guideEpsHigh: r.guide_eps_high, guideEpsLow: r.guide_eps_low,
        consensusRev: r.consensus_rev, consensusEps: r.consensus_eps,
        epsTrendCurrent: r.eps_trend_current, epsTrend7d: r.eps_trend_7d,
        epsTrend30d: r.eps_trend_30d, epsTrend60d: r.eps_trend_60d,
        epsTrend90d: r.eps_trend_90d,
        revisionsUp7d: r.revisions_up_7d, revisionsDown7d: r.revisions_down_7d,
        revisionsUp30d: r.revisions_up_30d, revisionsDown30d: r.revisions_down_30d,
        // Revenue estimate ranges
        nextQRevLow: r.next_q_rev_low, nextQRevHigh: r.next_q_rev_high,
        nextQRevAnalysts: r.next_q_rev_analysts, nextQRevSpread: r.next_q_rev_spread,
        fy1RevLow: r.fy1_rev_low, fy1RevHigh: r.fy1_rev_high,
        fy1RevAnalysts: r.fy1_rev_analysts, fy1RevSpread: r.fy1_rev_spread,
        fy2RevLow: r.fy2_rev_low, fy2RevHigh: r.fy2_rev_high,
        fy2RevAnalysts: r.fy2_rev_analysts, fy2RevSpread: r.fy2_rev_spread,
        // FY1 EPS trend + revision counts
        fy1RevisionsUp30d: r.fy1_revisions_up_30d, fy1RevisionsDown30d: r.fy1_revisions_down_30d,
        fy1EpsTrendCurrent: r.fy1_eps_trend_current, fy1EpsTrend7d: r.fy1_eps_trend_7d,
        fy1EpsTrend30d: r.fy1_eps_trend_30d, fy1EpsTrend60d: r.fy1_eps_trend_60d,
        fy1EpsTrend90d: r.fy1_eps_trend_90d,
      };
    }
  }
  // Get snapshot data (always, to fill gaps from Supabase schema lag)
  const snap = await loadSnapshot();
  const snapResult = snap?.estimates?.[ticker] || null;
  if (!supaResult) return snapResult;
  if (!snapResult) return supaResult;
  // Merge: Supabase values take priority, but fill nulls from snapshot
  const merged = { ...snapResult };
  for (const [k, v] of Object.entries(supaResult)) {
    if (v != null) merged[k] = v;
  }
  return merged;
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

// --- Yahoo Finance news (single search call across top tickers) ---
async function fetchYahooNewsClient(tickers) {
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
    console.warn('Yahoo news fetch failed:', e.message);
    return [];
  }
}

// --- Google News RSS (per-ticker; pulls from dozens of publishers, no key) ---
async function fetchGoogleNewsClient(tickers) {
  const top = tickers.slice(0, 5);
  if (typeof DOMParser === 'undefined') return []; // non-browser env
  const parser = new DOMParser();
  const perTicker = await Promise.allSettled(top.map(async (ticker) => {
    const q = encodeURIComponent(`$${ticker} stock`);
    const url = `https://news.google.com/rss/search?q=${q}&hl=en-US&gl=US&ceid=US%3Aen`;
    const xml = await fetchTextWithProxy(url, 10000);
    const doc = parser.parseFromString(xml, 'text/xml');
    const items = Array.from(doc.querySelectorAll('item')).slice(0, 15);
    return items.map((item) => {
      const pub = item.querySelector('pubDate')?.textContent || '';
      let publishedAt = null;
      const d = new Date(pub);
      if (!isNaN(d.getTime())) publishedAt = d.toISOString();
      return {
        title: item.querySelector('title')?.textContent || '',
        url: item.querySelector('link')?.textContent || '',
        source: item.querySelector('source')?.textContent || 'Google News',
        publishedAt,
        ticker,
      };
    });
  }));
  const out = [];
  perTicker.forEach((r) => {
    if (r.status === 'fulfilled' && Array.isArray(r.value)) out.push(...r.value);
    else if (r.status === 'rejected') console.warn('Google News fetch failed:', r.reason && r.reason.message);
  });
  return out;
}

// --- Perplexity sonar news (read from Supabase news_items, populated by the
// GH Actions hourly cron; real-publisher URLs) ---
// Maps common publisher hosts to display names; unknown hosts fall back to a
// capitalized host label. Keyed by registrable domain (host with leading www. /
// m. / news. stripped).
const PPLX_HOST_NAMES = {
  'bloomberg.com': 'Bloomberg',
  'reuters.com': 'Reuters',
  'ft.com': 'Financial Times',
  'wsj.com': 'WSJ',
  'nytimes.com': 'NY Times',
  'cnbc.com': 'CNBC',
  'marketwatch.com': 'MarketWatch',
  'barrons.com': "Barron's",
  'businesswire.com': 'Business Wire',
  'prnewswire.com': 'PR Newswire',
  'seekingalpha.com': 'Seeking Alpha',
  'theinformation.com': 'The Information',
  'forbes.com': 'Forbes',
  'fool.com': 'Motley Fool',
  'investors.com': "Investor's Business Daily",
  'apnews.com': 'AP',
  'theverge.com': 'The Verge',
  'techcrunch.com': 'TechCrunch',
  'axios.com': 'Axios',
  'finance.yahoo.com': 'Yahoo Finance',
  'yahoo.com': 'Yahoo Finance',
};

function sourceFromUrl(url) {
  try {
    let host = new URL(url).hostname.toLowerCase();
    host = host.replace(/^(www\.|m\.|news\.|amp\.)/, '');
    if (PPLX_HOST_NAMES[host]) return PPLX_HOST_NAMES[host];
    // Strip subdomains down to the registrable domain for a second lookup.
    const parts = host.split('.');
    if (parts.length > 2) {
      const base = parts.slice(-2).join('.');
      if (PPLX_HOST_NAMES[base]) return PPLX_HOST_NAMES[base];
    }
    // Fallback: capitalize the second-level label (e.g. "example.com" -> "Example").
    const label = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
    return label ? label.charAt(0).toUpperCase() + label.slice(1) : 'Web';
  } catch (e) {
    return 'Web';
  }
}

// Reads material news from the Supabase `news_items` table, which the GH Actions
// hourly cron populates from a backend Perplexity sonar call. Replaces the old
// per-browser sonar call so users no longer need a localStorage API key.
async function fetchPerplexityNewsClient(tickers) {
  if (!(await checkSupabase())) return [];
  const tickerSet = new Set((tickers || []).map((t) => String(t).toUpperCase()));
  const since = new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString();
  try {
    const rows = await supabaseGet(
      'news_items',
      `select=*&published_at=gte.${since}&order=published_at.desc&limit=100`,
      200
    );
    return (rows || [])
      .filter((r) => Array.isArray(r.tickers) && r.tickers.some((t) => tickerSet.has(String(t).toUpperCase())))
      .map((r) => ({
        title: r.title,
        body: r.body || '',
        url: r.url || '',
        source: r.source || (r.url_host ? r.url_host : 'News'),
        publishedAt: r.published_at,
        ticker: r.ticker || (Array.isArray(r.tickers) && r.tickers[0]) || '',
        tickers: r.tickers || [],
        signal: r.signal,
      }));
  } catch (e) {
    console.warn('Supabase news fetch failed:', e && e.message);
    return [];
  }
}

// --- Client-side news fetch (Perplexity + Yahoo + Google News, merged + deduped) ---
function normalizeTitleKey(title) {
  return String(title || '').toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim().slice(0, 60);
}

async function fetchNewsClient(tickers) {
  const [pplx, yahoo, google] = await Promise.allSettled([
    fetchPerplexityNewsClient(tickers),
    fetchYahooNewsClient(tickers),
    fetchGoogleNewsClient(tickers),
  ]);
  const pplxItems = pplx.status === 'fulfilled' ? pplx.value : [];
  const yahooItems = yahoo.status === 'fulfilled' ? yahoo.value : [];
  const googleItems = google.status === 'fulfilled' ? google.value : [];

  const merged = [];
  const seenUrls = new Set();
  const seenTitles = new Set();
  // Perplexity first so its real-publisher URLs (bloomberg.com, reuters.com,
  // etc.) win URL/title ties against Yahoo's aggregator mirrors. Yahoo+Google
  // then fill coverage gaps.
  for (const item of pplxItems.concat(yahooItems).concat(googleItems)) {
    if (!item || !item.title) continue;
    const urlKey = (item.url || '').trim();
    if (urlKey && urlKey !== '#' && seenUrls.has(urlKey)) continue;
    const titleKey = normalizeTitleKey(item.title);
    if (titleKey && seenTitles.has(titleKey)) continue;
    if (urlKey && urlKey !== '#') seenUrls.add(urlKey);
    if (titleKey) seenTitles.add(titleKey);
    merged.push(item);
  }
  merged.sort((a, b) => (Date.parse(b.publishedAt || 0) || 0) - (Date.parse(a.publishedAt || 0) || 0));
  return merged;
}

// --- Data source status ---
function isStaticDemo() {
  return typeof backendAvailable !== 'undefined' && backendAvailable === false;
}

function getDataSourceInfo() {
  if (_supabaseAvailable) return { source: 'supabase', generated: _supabaseGenerated };
  if (_snapshotData) return { source: 'snapshot', generated: _snapshotData.generated };
  return { source: 'none', generated: null };
}
