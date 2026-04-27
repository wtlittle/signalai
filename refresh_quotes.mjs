/**
 * refresh_quotes.mjs
 * 
 * Fetches fresh quote data from Yahoo Finance for all watchlist tickers
 * and pushes directly to Supabase. Also regenerates data-snapshot.json quotes section.
 * 
 * Two-step approach:
 * 1. v8 chart data for prices + computed performance returns
 * 2. v10 quoteSummary for fundamentals (market cap, EV, revenue, FCF, etc.)
 */
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync, existsSync } from 'fs';

const SUPABASE_URL = (process.env.SUPABASE_URL || '').trim() || 'https://wcyirdvvuetzodiedzss.supabase.co';
const SERVICE_KEY = (process.env.SUPABASE_SERVICE_KEY || '').trim();
const supabase = createClient(SUPABASE_URL, SERVICE_KEY);

// --- Parse tickers from utils.js ---
const scriptDir = new URL('.', import.meta.url).pathname;
const utilsSrc = readFileSync(scriptDir + 'utils.js', 'utf-8');
function extractTickers(src) {
  const match = src.match(/const\s+DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];/);
  if (!match) return [];
  return [...new Set([...match[1].matchAll(/'([A-Z.^]+)'/g)].map(m => m[1]))];
}
const tickers = extractTickers(utilsSrc);
console.log(`Found ${tickers.length} tickers in watchlist`);

// --- Fetch Yahoo Finance v8 chart data ---
async function fetchYahooChart(ticker, range = '5y', interval = '1d') {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=${range}&interval=${interval}&includePrePost=false`;
  try {
    const resp = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.chart?.result?.[0] || null;
  } catch (e) {
    console.warn(`  Failed to fetch chart ${ticker}: ${e.message}`);
    return null;
  }
}

// --- Get Yahoo crumb for v10 API ---
let _yahooCredsCache = null;
async function getYahooCreds() {
  if (_yahooCredsCache) return _yahooCredsCache;
  try {
    // Step 1: Get cookies
    const cookieResp = await fetch('https://fc.yahoo.com/', {
      redirect: 'manual',
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    });
    const setCookies = cookieResp.headers.getSetCookie?.() || [];
    const cookieStr = setCookies.map(c => c.split(';')[0]).join('; ');
    
    // Step 2: Get crumb
    const crumbResp = await fetch('https://query2.finance.yahoo.com/v1/test/getcrumb', {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Cookie': cookieStr,
      },
    });
    const crumb = await crumbResp.text();
    _yahooCredsCache = { cookie: cookieStr, crumb };
    console.log(`Got Yahoo crumb: ${crumb.substring(0, 8)}...`);
    return _yahooCredsCache;
  } catch (e) {
    console.warn('Failed to get Yahoo creds:', e.message);
    return null;
  }
}

// --- Fetch fundamentals via v10 quoteSummary ---
async function fetchFundamentals(ticker, creds) {
  if (!creds) return null;
  const url = `https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(ticker)}?modules=defaultKeyStatistics,financialData,price,assetProfile&crumb=${encodeURIComponent(creds.crumb)}`;
  try {
    const resp = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Cookie': creds.cookie,
      },
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    const result = data.quoteSummary?.result?.[0];
    if (!result) return null;

    const ks = result.defaultKeyStatistics || {};
    const fd = result.financialData || {};
    const pr = result.price || {};
    const ap = result.assetProfile || {};

    // Currency unit guard for ADRs / foreign-listed tickers.
    // Yahoo reports `marketCap` in the LISTING currency (e.g. USD for TSM
    // ADR), but `totalDebt` / `totalCash` / `enterpriseValue` in the
    // company's `financialCurrency` (TWD for TSM). Mixing produces a
    // nonsense EV (TSM showed MC $2.0T but EV $7.6T because Yahoo's EV
    // is in TWD). When the two currencies disagree we drop the unit-
    // mismatched fields rather than ship a misleading number.
    const listingCurrency = pr.currency || null;
    const financialCurrency = fd.financialCurrency || null;
    const currencyMismatch = !!(listingCurrency && financialCurrency && listingCurrency !== financialCurrency);
    if (currencyMismatch) {
      console.warn(`  ${ticker}: currency mismatch listing=${listingCurrency} financial=${financialCurrency} — EV/debt/cash nulled`);
    }

    return {
      market_cap: pr.marketCap?.raw || null,
      enterprise_value: currencyMismatch ? null : (ks.enterpriseValue?.raw || null),
      total_revenue: currencyMismatch ? null : (fd.totalRevenue?.raw || null),
      total_cash: currencyMismatch ? null : (fd.totalCash?.raw || null),
      total_debt: currencyMismatch ? null : (fd.totalDebt?.raw || null),
      free_cashflow: currencyMismatch ? null : (fd.freeCashflow?.raw || null),
      operating_cashflow: currencyMismatch ? null : (fd.operatingCashflow?.raw || null),
      financial_currency: financialCurrency,
      listing_currency: listingCurrency,
      target_mean_price: fd.targetMeanPrice?.raw || null,
      target_high_price: fd.targetHighPrice?.raw || null,
      target_low_price: fd.targetLowPrice?.raw || null,
      recommendation_key: fd.recommendationKey || null,
      number_of_analyst_opinions: fd.numberOfAnalystOpinions?.raw || null,
      average_volume: pr.averageDailyVolume3Month?.raw || null,
      volume: pr.regularMarketVolume?.raw || null,
      beta: ks.beta?.raw || null,
      forward_pe: ks.forwardPE?.raw || null,
      trailing_pe: pr.trailingPE?.raw || ks.trailingPE?.raw || null,
      shares_outstanding: ks.sharesOutstanding?.raw || null,
      revenue_growth: fd.revenueGrowth?.raw ? fd.revenueGrowth.raw * 100 : null,
      earnings_growth: fd.earningsGrowth?.raw ? fd.earningsGrowth.raw * 100 : null,
      forward_eps: fd.forwardEps?.raw || ks.forwardEps?.raw || null,
      trailing_eps: pr.trailingEps?.raw || ks.trailingEps?.raw || null,
      enterprise_to_revenue: currencyMismatch ? null : (ks.enterpriseToRevenue?.raw || null),
      enterprise_to_ebitda: currencyMismatch ? null : (ks.enterpriseToEbitda?.raw || null),
      operating_margins: fd.operatingMargins?.raw ? fd.operatingMargins.raw * 100 : null,
      sector: ap.sector || pr.sector || null,
      industry: ap.industry || pr.industry || null,
      city: ap.city || null,
      state: ap.state || null,
      country: ap.country || null,
    };
  } catch (e) {
    console.warn(`  Failed fundamentals for ${ticker}: ${e.message}`);
    return null;
  }
}

// --- Compute returns from close prices ---
function computeReturns(timestamps, closes, currentPrice) {
  const valid = [];
  for (let i = 0; i < timestamps.length; i++) {
    if (closes[i] != null) valid.push({ ts: timestamps[i], close: closes[i] });
  }
  if (valid.length === 0) return {};

  const current = currentPrice || valid[valid.length - 1].close;
  const findClose = (daysBack) => {
    if (daysBack >= valid.length) return valid[0].close;
    return valid[valid.length - 1 - daysBack].close;
  };
  const calcReturn = (prev) => {
    if (!prev || !current) return null;
    return Number(((current - prev) / prev * 100).toFixed(2));
  };

  // YTD
  const now = new Date();
  const yearStart = new Date(now.getFullYear(), 0, 1).getTime() / 1000;
  let ytdPrice = null;
  for (let i = 0; i < valid.length; i++) {
    if (valid[i].ts >= yearStart) {
      ytdPrice = i > 0 ? valid[i - 1].close : valid[0].close;
      break;
    }
  }

  return {
    change1d: calcReturn(findClose(1)),
    change1w: calcReturn(findClose(5)),
    change1m: calcReturn(findClose(21)),
    change3m: calcReturn(findClose(63)),
    change1y: calcReturn(findClose(252)),
    change3y: calcReturn(findClose(756)),
    changeYtd: calcReturn(ytdPrice),
  };
}

// --- Batch upsert ---
async function upsertBatch(table, rows, conflict = 'ticker', batchSize = 50) {
  let inserted = 0;
  for (let i = 0; i < rows.length; i += batchSize) {
    const batch = rows.slice(i, i + batchSize);
    const { error } = await supabase.from(table).upsert(batch, { onConflict: conflict });
    if (error) {
      console.error(`  Error in ${table} batch ${i}: ${error.message}`);
    } else {
      inserted += batch.length;
    }
  }
  console.log(`  ${table}: ${inserted} rows upserted`);
}

// ===== MAIN =====
console.log('\n=== Refreshing quote data from Yahoo Finance ===\n');

// Step 1: Get crumb for v10 API
console.log('Getting Yahoo auth crumb...');
const creds = await getYahooCreds();

const quoteRows = [];
const snapshotQuotes = {};
let successCount = 0;
let failCount = 0;
let fundSuccess = 0;

// Process tickers
for (let i = 0; i < tickers.length; i++) {
  const ticker = tickers[i];
  process.stdout.write(`  [${i + 1}/${tickers.length}] ${ticker}...`);

  // Fetch chart data (v8) for price + returns
  const result = await fetchYahooChart(ticker);

  if (result) {
    const meta = result.meta || {};
    const timestamps = result.timestamp || [];
    const closes = result.indicators?.quote?.[0]?.close || [];
    const price = meta.regularMarketPrice;
    const returns = computeReturns(timestamps, closes, price);

    const row = {
      ticker,
      long_name: meta.longName || meta.shortName || ticker,
      price: price || null,
      previous_close: meta.previousClose || meta.chartPreviousClose || null,
      fifty_two_week_high: meta.fiftyTwoWeekHigh || null,
      fifty_two_week_low: meta.fiftyTwoWeekLow || null,
      currency: meta.currency || 'USD',
      change_1d: returns.change1d ?? null,
      change_1w: returns.change1w ?? null,
      change_1m: returns.change1m ?? null,
      change_3m: returns.change3m ?? null,
      change_1y: returns.change1y ?? null,
      change_3y: returns.change3y ?? null,
      change_ytd: returns.changeYtd ?? null,
    };

    // Fetch fundamentals (v10) 
    const fund = await fetchFundamentals(ticker, creds);
    if (fund) {
      Object.assign(row, fund);
      fundSuccess++;
    }

    quoteRows.push(row);

    // Build snapshot-compatible format
    const snapQuote = {
      ticker,
      longName: meta.longName || meta.shortName || ticker,
      price,
      previousClose: meta.previousClose,
      fiftyTwoWeekHigh: meta.fiftyTwoWeekHigh,
      fiftyTwoWeekLow: meta.fiftyTwoWeekLow,
      currency: meta.currency || 'USD',
      change1d: returns.change1d,
      change1w: returns.change1w,
      change1m: returns.change1m,
      change3m: returns.change3m,
      change1y: returns.change1y,
      change3y: returns.change3y,
      changeYtd: returns.changeYtd,
    };
    if (fund) {
      snapQuote.marketCap = fund.market_cap;
      snapQuote.enterpriseValue = fund.enterprise_value;
      snapQuote.totalRevenue = fund.total_revenue;
      snapQuote.freeCashflow = fund.free_cashflow;
      snapQuote.operatingCashflow = fund.operating_cashflow;
      snapQuote.totalCash = fund.total_cash;
      snapQuote.totalDebt = fund.total_debt;
      snapQuote.targetMeanPrice = fund.target_mean_price;
      snapQuote.targetHighPrice = fund.target_high_price;
      snapQuote.targetLowPrice = fund.target_low_price;
      snapQuote.recommendationKey = fund.recommendation_key;
      snapQuote.numberOfAnalystOpinions = fund.number_of_analyst_opinions;
      snapQuote.beta = fund.beta;
      snapQuote.forwardPE = fund.forward_pe;
      snapQuote.trailingPE = fund.trailing_pe;
      snapQuote.sharesOutstanding = fund.shares_outstanding;
      snapQuote.revenueGrowth = fund.revenue_growth;
      snapQuote.earningsGrowth = fund.earnings_growth;
      snapQuote.forwardEps = fund.forward_eps;
      snapQuote.trailingEps = fund.trailing_eps;
      snapQuote.enterpriseToRevenue = fund.enterprise_to_revenue;
      snapQuote.enterpriseToEbitda = fund.enterprise_to_ebitda;
      snapQuote.operatingMargins = fund.operating_margins;
      snapQuote.sector = fund.sector;
      snapQuote.industry = fund.industry;
    }
    snapshotQuotes[ticker] = snapQuote;

    successCount++;
    const mcapStr = fund?.market_cap ? `MCap: $${(fund.market_cap / 1e9).toFixed(1)}B` : 'No MCap';
    console.log(` $${price?.toFixed(2)} | 1D: ${returns.change1d ?? '—'}% | ${mcapStr}`);
  } else {
    failCount++;
    console.log(' FAILED');
  }

  // Rate limiting: pause every 3 tickers (slightly more aggressive with 2 API calls per ticker)
  if (i % 3 === 2) await new Promise(r => setTimeout(r, 800));
}

console.log(`\nFetched: ${successCount} success (${fundSuccess} with fundamentals), ${failCount} failed out of ${tickers.length}`);

// Push to Supabase
console.log('\nPushing to Supabase quotes table...');
await upsertBatch('quotes', quoteRows);

// Update metadata
await supabase.from('metadata').upsert({
  key: 'generated',
  value: new Date().toISOString(),
  updated_at: new Date().toISOString(),
});
console.log('  metadata: updated generated timestamp');

// Update snapshot quotes section
const snapshotPath = scriptDir + 'data-snapshot.json';
if (existsSync(snapshotPath)) {
  try {
    const snapshot = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
    // Merge: keep existing fundamental data if new fetch didn't get it
    const existingQuotes = snapshot.quotes || {};
    for (const [ticker, newQuote] of Object.entries(snapshotQuotes)) {
      const existing = existingQuotes[ticker] || {};
      // For each field, prefer new non-null value, otherwise keep existing
      snapshotQuotes[ticker] = {};
      for (const key of new Set([...Object.keys(existing), ...Object.keys(newQuote)])) {
        snapshotQuotes[ticker][key] = newQuote[key] != null ? newQuote[key] : existing[key];
      }
    }
    snapshot.quotes = { ...existingQuotes, ...snapshotQuotes };
    snapshot.generated = new Date().toISOString();
    writeFileSync(snapshotPath, JSON.stringify(snapshot));
    console.log(`\nSnapshot updated: ${Object.keys(snapshot.quotes).length} quotes`);
  } catch (e) {
    console.warn('Could not update snapshot:', e.message);
  }
}

console.log('\n=== Done! ===');
