/**
 * refresh_deep_dive.mjs
 * 
 * Fetches deep-dive data for ALL watchlist tickers:
 * - analyst_summary (calendarEvents, earningsHistory, analyst targets)
 * - short_interest (shares short, float, short ratio)
 * - estimates (earningsTrend: next Q, FY1, FY2 estimates + revisions)
 * - cross_sector_comps (fundamental similarity across sectors)
 * - outperformance (rolling S&P 500 percentile)
 * 
 * Uses Yahoo Finance v10 quoteSummary API.
 * Writes results to data-snapshot.json and Supabase.
 */
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://wcyirdvvuetzodiedzss.supabase.co';
const SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY;
const supabase = createClient(SUPABASE_URL, SERVICE_KEY);

// --- Parse tickers ---
const utilsSrc = readFileSync(resolve(__dirname, 'utils.js'), 'utf-8');
function extractTickers(src) {
  const match = src.match(/const\s+DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];/);
  if (!match) return [];
  return [...new Set([...match[1].matchAll(/'([A-Z.^]+)'/g)].map(m => m[1]))];
}
const tickers = extractTickers(utilsSrc);
console.log(`Found ${tickers.length} tickers\n`);

// --- Yahoo crumb auth ---
async function getYahooCreds() {
  try {
    const cookieResp = await fetch('https://fc.yahoo.com/', {
      redirect: 'manual',
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    });
    const setCookies = cookieResp.headers.getSetCookie?.() || [];
    const cookieStr = setCookies.map(c => c.split(';')[0]).join('; ');
    const crumbResp = await fetch('https://query2.finance.yahoo.com/v1/test/getcrumb', {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Cookie': cookieStr },
    });
    const crumb = await crumbResp.text();
    console.log(`Got Yahoo crumb: ${crumb.substring(0, 8)}...`);
    return { cookie: cookieStr, crumb };
  } catch (e) {
    console.error('Failed to get Yahoo creds:', e.message);
    return null;
  }
}

// --- Fetch v10 quoteSummary with multiple modules ---
async function fetchQuoteSummary(ticker, modules, creds) {
  if (!creds) return null;
  const url = `https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(ticker)}?modules=${modules.join(',')}&crumb=${encodeURIComponent(creds.crumb)}`;
  try {
    const resp = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Cookie': creds.cookie },
      signal: AbortSignal.timeout(20000),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.quoteSummary?.result?.[0] || null;
  } catch (e) {
    console.warn(`  v10 failed for ${ticker}: ${e.message}`);
    return null;
  }
}

// --- Extract analyst summary ---
function extractAnalystSummary(ticker, result) {
  if (!result) return null;
  const ks = result.defaultKeyStatistics || {};
  const fd = result.financialData || {};
  const cal = result.calendarEvents || {};
  const eh = result.earningsHistory || {};
  
  // Calendar data
  const earnings = cal.earnings || {};
  const calendarData = {};
  if (earnings.earningsDate?.[0]?.fmt) calendarData.earningsDate = earnings.earningsDate[0].fmt;
  if (earnings.earningsAverage?.raw != null) calendarData.earningsAverage = earnings.earningsAverage.raw;
  if (earnings.earningsLow?.raw != null) calendarData.earningsLow = earnings.earningsLow.raw;
  if (earnings.earningsHigh?.raw != null) calendarData.earningsHigh = earnings.earningsHigh.raw;
  if (earnings.revenueAverage?.raw != null) calendarData.revenueAverage = earnings.revenueAverage.raw;
  if (earnings.revenueLow?.raw != null) calendarData.revenueLow = earnings.revenueLow.raw;
  if (earnings.revenueHigh?.raw != null) calendarData.revenueHigh = earnings.revenueHigh.raw;
  
  // Earnings history
  const earningsHistory = (eh.history || []).map(h => ({
    epsActual: h.epsActual?.raw ?? null,
    epsEstimate: h.epsEstimate?.raw ?? null,
    epsDifference: h.epsDifference?.raw ?? null,
    surprisePercent: h.surprisePercent?.raw ?? null,
    quarter: h.quarter?.fmt || null,
    period: h.period || null,
  }));

  return {
    targetMeanPrice: fd.targetMeanPrice?.raw ?? null,
    targetHighPrice: fd.targetHighPrice?.raw ?? null,
    targetLowPrice: fd.targetLowPrice?.raw ?? null,
    targetMedianPrice: fd.targetMedianPrice?.raw ?? null,
    numberOfAnalystOpinions: fd.numberOfAnalystOpinions?.raw ?? null,
    recommendationKey: fd.recommendationKey || null,
    recommendationMean: fd.recommendationMean?.raw ?? null,
    forwardEps: fd.forwardEps?.raw ?? ks.forwardEps?.raw ?? null,
    trailingEps: fd.trailingEps?.raw ?? ks.trailingEps?.raw ?? null,
    forwardPE: ks.forwardPE?.raw ?? null,
    trailingPE: ks.trailingPE?.raw ?? null,
    beta: ks.beta?.raw ?? null,
    averageVolume: ks.averageVolume?.raw ?? null,
    averageVolume10days: ks.averageVolume10days?.raw ?? null,
    volume: ks.volume?.raw ?? null,
    fiftyTwoWeekHigh: ks.fiftyTwoWeekHigh?.raw ?? null,
    fiftyTwoWeekLow: ks.fiftyTwoWeekLow?.raw ?? null,
    sharesOutstanding: ks.sharesOutstanding?.raw ?? null,
    calendar: Object.keys(calendarData).length > 0 ? calendarData : null,
    earningsHistory: earningsHistory.length > 0 ? earningsHistory : null,
  };
}

// --- Extract short interest ---
function extractShortInterest(ticker, result) {
  if (!result) return null;
  const ks = result.defaultKeyStatistics || {};
  
  const sharesShort = ks.sharesShort?.raw ?? null;
  if (!sharesShort) return null;
  
  let shortPctFloat = ks.shortPercentOfFloat?.raw ?? null;
  if (shortPctFloat && shortPctFloat < 1) shortPctFloat *= 100;
  
  const sharesShortPrior = ks.sharesShortPriorMonth?.raw ?? null;
  
  // Dates
  let currentDate = null;
  const dateRaw = ks.dateShortInterest?.raw;
  if (dateRaw) currentDate = new Date(dateRaw * 1000).toISOString().substring(0, 10);
  
  let priorDate = null;
  const priorDateRaw = ks.sharesShortPreviousMonthDate?.raw;
  if (priorDateRaw) priorDate = new Date(priorDateRaw * 1000).toISOString().substring(0, 10);
  
  // Change %
  let change = null;
  if (sharesShort && sharesShortPrior && sharesShortPrior > 0) {
    change = ((sharesShort - sharesShortPrior) / sharesShortPrior) * 100;
  }
  
  return {
    ticker,
    current: {
      sharesShort,
      shortPercentOfFloat: shortPctFloat,
      shortRatio: ks.shortRatio?.raw ?? null,
      date: currentDate,
    },
    priorMonth: {
      sharesShort: sharesShortPrior,
      date: priorDate,
    },
    sharesOutstanding: ks.sharesOutstanding?.raw ?? null,
    floatShares: ks.floatShares?.raw ?? null,
    change,
  };
}

// --- Extract estimates ---
function extractEstimates(ticker, result, quotesData) {
  if (!result) return null;
  const et = result.earningsTrend?.trend || [];
  const fd = result.financialData || {};
  const ks = result.defaultKeyStatistics || {};
  
  // Find periods: 0q = next quarter, +1q, 0y = current year, +1y
  const findPeriod = (p) => et.find(t => t.period === p);
  const nextQ = findPeriod('0q');
  const fy1 = findPeriod('0y');
  const fy2 = findPeriod('+1y');
  
  const qData = quotesData || {};
  
  // Revenue LTM from quotes data
  const revenueLtm = qData.totalRevenue ?? null;
  const revenueGrowth = fd.revenueGrowth?.raw ? fd.revenueGrowth.raw * 100 : (qData.revenueGrowth ?? null);
  const grossMargins = fd.grossMargins?.raw ? fd.grossMargins.raw * 100 : null;
  const operatingMargins = fd.operatingMargins?.raw ? fd.operatingMargins.raw * 100 : (qData.operatingMargins ?? null);
  
  // FCF
  const fcf = fd.freeCashflow?.raw ?? qData.freeCashflow ?? null;
  let fcfMargin = null;
  if (fcf && revenueLtm && revenueLtm > 0) fcfMargin = (fcf / revenueLtm) * 100;
  
  const estimates = {
    revenueLtm,
    revenueGrowth,
    grossMargins,
    operatingMargins,
    fcf,
    fcfMargin,
  };
  
  // Next quarter estimates
  if (nextQ) {
    const re = nextQ.revenueEstimate || {};
    const ee = nextQ.earningsEstimate || {};
    estimates.nextQRevEst = re.avg?.raw ?? null;
    estimates.nextQRevLow = re.low?.raw ?? null;
    estimates.nextQRevHigh = re.high?.raw ?? null;
    estimates.nextQRevAnalysts = re.numberOfAnalysts?.raw ?? null;
    if (re.low?.raw && re.high?.raw && re.avg?.raw && re.avg.raw > 0) {
      estimates.nextQRevSpread = ((re.high.raw - re.low.raw) / re.avg.raw) * 100;
    }
    estimates.nextQRevGrowth = re.growth?.raw ? re.growth.raw * 100 : null;
    estimates.nextQEpsEst = ee.avg?.raw ?? null;
    estimates.nextQEpsGrowth = ee.growth?.raw ? ee.growth.raw * 100 : null;
  }
  
  // FY1 estimates
  if (fy1) {
    const re = fy1.revenueEstimate || {};
    const ee = fy1.earningsEstimate || {};
    const et1 = fy1.epsTrend || {};
    const er = fy1.epsRevisions || {};
    estimates.fy1RevEst = re.avg?.raw ?? null;
    estimates.fy1RevLow = re.low?.raw ?? null;
    estimates.fy1RevHigh = re.high?.raw ?? null;
    estimates.fy1RevAnalysts = re.numberOfAnalysts?.raw ?? null;
    if (re.low?.raw && re.high?.raw && re.avg?.raw && re.avg.raw > 0) {
      estimates.fy1RevSpread = ((re.high.raw - re.low.raw) / re.avg.raw) * 100;
    }
    estimates.fy1RevGrowth = re.growth?.raw ? re.growth.raw * 100 : null;
    estimates.fy1EpsEst = ee.avg?.raw ?? null;
    estimates.fy1EpsGrowth = ee.growth?.raw ? ee.growth.raw * 100 : null;
    // EPS trends
    estimates.fy1EpsTrendCurrent = et1.current?.raw ?? null;
    estimates.fy1EpsTrend7d = et1['7daysAgo']?.raw ?? null;
    estimates.fy1EpsTrend30d = et1['30daysAgo']?.raw ?? null;
    estimates.fy1EpsTrend60d = et1['60daysAgo']?.raw ?? null;
    estimates.fy1EpsTrend90d = et1['90daysAgo']?.raw ?? null;
    // Revisions
    estimates.fy1RevisionsUp30d = er.upLast30days?.raw ?? null;
    estimates.fy1RevisionsDown30d = er.downLast30days?.raw ?? null;
  }
  
  // FY2 estimates
  if (fy2) {
    const re = fy2.revenueEstimate || {};
    const ee = fy2.earningsEstimate || {};
    estimates.fy2RevEst = re.avg?.raw ?? null;
    estimates.fy2RevLow = re.low?.raw ?? null;
    estimates.fy2RevHigh = re.high?.raw ?? null;
    estimates.fy2RevAnalysts = re.numberOfAnalysts?.raw ?? null;
    if (re.low?.raw && re.high?.raw && re.avg?.raw && re.avg.raw > 0) {
      estimates.fy2RevSpread = ((re.high.raw - re.low.raw) / re.avg.raw) * 100;
    }
    estimates.fy2RevGrowth = re.growth?.raw ? re.growth.raw * 100 : null;
    estimates.fy2EpsEst = ee.avg?.raw ?? null;
    estimates.fy2EpsGrowth = ee.growth?.raw ? ee.growth.raw * 100 : null;
  }
  
  // Next quarter EPS trend (from 0q period)
  if (nextQ) {
    const et0 = nextQ.epsTrend || {};
    const er0 = nextQ.epsRevisions || {};
    estimates.epsTrendCurrent = et0.current?.raw ?? null;
    estimates.epsTrend7d = et0['7daysAgo']?.raw ?? null;
    estimates.epsTrend30d = et0['30daysAgo']?.raw ?? null;
    estimates.epsTrend60d = et0['60daysAgo']?.raw ?? null;
    estimates.epsTrend90d = et0['90daysAgo']?.raw ?? null;
    estimates.revisionsUp7d = er0.upLast7days?.raw ?? null;
    estimates.revisionsDown7d = er0.downLast7days?.raw ?? null;
    estimates.revisionsUp30d = er0.upLast30days?.raw ?? null;
    estimates.revisionsDown30d = er0.downLast30days?.raw ?? null;
  }
  
  // Check if we got any meaningful data
  const hasData = estimates.nextQEpsEst != null || estimates.fy1EpsEst != null || estimates.fy2EpsEst != null;
  return hasData ? estimates : null;
}

// --- Batch upsert ---
async function upsertBatch(table, rows, conflict = 'ticker', batchSize = 50) {
  if (rows.length === 0) return;
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
console.log('=== Refreshing Deep-Dive Data for All Tickers ===\n');

const creds = await getYahooCreds();
if (!creds) {
  console.error('Cannot proceed without Yahoo credentials');
  process.exit(1);
}

// Load existing snapshot
const snapshotPath = resolve(__dirname, 'data-snapshot.json');
const snapshot = JSON.parse(readFileSync(snapshotPath, 'utf-8'));

const allAnalyst = {};
const allShortInterest = {};
const allEstimates = {};
let analystCount = 0, shortCount = 0, estCount = 0;

// Modules we need per ticker
const MODULES = [
  'defaultKeyStatistics', 'financialData',
  'calendarEvents', 'earningsHistory', 'earningsTrend',
];

for (let i = 0; i < tickers.length; i++) {
  const ticker = tickers[i];
  process.stdout.write(`  [${i + 1}/${tickers.length}] ${ticker}...`);
  
  const result = await fetchQuoteSummary(ticker, MODULES, creds);
  
  if (result) {
    // Analyst summary
    const analyst = extractAnalystSummary(ticker, result);
    if (analyst) {
      allAnalyst[ticker] = analyst;
      analystCount++;
    }
    
    // Short interest
    const short_ = extractShortInterest(ticker, result);
    if (short_) {
      allShortInterest[ticker] = short_;
      shortCount++;
    }
    
    // Estimates (pass quotes data for revenue/margins fallback)
    const est = extractEstimates(ticker, result, snapshot.quotes?.[ticker]);
    if (est) {
      allEstimates[ticker] = est;
      estCount++;
    }
    
    const targetStr = analyst?.targetMeanPrice ? `$${analyst.targetMeanPrice.toFixed(2)}` : '—';
    const shortStr = short_?.current?.sharesShort ? `${(short_.current.sharesShort / 1e6).toFixed(1)}M` : '—';
    const estStr = est?.nextQEpsEst != null ? `$${est.nextQEpsEst.toFixed(2)}` : '—';
    console.log(` target: ${targetStr} | short: ${shortStr} | nextQ EPS: ${estStr}`);
  } else {
    console.log(' NO DATA');
  }
  
  // Rate limiting
  if (i % 3 === 2) await new Promise(r => setTimeout(r, 600));
}

console.log(`\n=== Results ===`);
console.log(`  Analyst Summary: ${analystCount}/${tickers.length}`);
console.log(`  Short Interest:  ${shortCount}/${tickers.length}`);
console.log(`  Estimates:       ${estCount}/${tickers.length}`);

// --- Update snapshot ---
console.log('\nUpdating snapshot...');
// Merge: keep existing data, update with new
snapshot.analyst_summary = { ...(snapshot.analyst_summary || {}), ...allAnalyst };
snapshot.short_interest = { ...(snapshot.short_interest || {}), ...allShortInterest };
snapshot.estimates = { ...(snapshot.estimates || {}), ...allEstimates };
writeFileSync(snapshotPath, JSON.stringify(snapshot));
console.log(`  Snapshot: ${Object.keys(snapshot.analyst_summary).length} analyst, ${Object.keys(snapshot.short_interest).length} short, ${Object.keys(snapshot.estimates).length} estimates`);

// --- Push to Supabase ---
console.log('\nPushing to Supabase...');

// Analyst Summary
const asRows = Object.entries(allAnalyst).map(([ticker, as_]) => ({
  ticker,
  target_mean_price: as_.targetMeanPrice,
  target_high_price: as_.targetHighPrice,
  target_low_price: as_.targetLowPrice,
  target_median_price: as_.targetMedianPrice,
  number_of_analyst_opinions: as_.numberOfAnalystOpinions,
  recommendation_key: as_.recommendationKey,
  recommendation_mean: as_.recommendationMean,
  forward_eps: as_.forwardEps,
  trailing_eps: as_.trailingEps,
  forward_pe: as_.forwardPE,
  trailing_pe: as_.trailingPE,
  beta: as_.beta,
  average_volume: as_.averageVolume,
  average_volume_10days: as_.averageVolume10days,
  volume: as_.volume,
  fifty_two_week_high: as_.fiftyTwoWeekHigh,
  fifty_two_week_low: as_.fiftyTwoWeekLow,
  shares_outstanding: as_.sharesOutstanding,
  calendar: as_.calendar || null,
  earnings_history: as_.earningsHistory || null,
}));
await upsertBatch('analyst_summary', asRows);

// Short Interest
const siRows = Object.entries(allShortInterest).map(([ticker, si]) => ({
  ticker,
  current_shares_short: si.current?.sharesShort,
  current_short_pct_float: si.current?.shortPercentOfFloat,
  current_short_ratio: si.current?.shortRatio,
  current_date_val: si.current?.date,
  prior_shares_short: si.priorMonth?.sharesShort,
  prior_date: si.priorMonth?.date,
  shares_outstanding: si.sharesOutstanding,
  float_shares: si.floatShares,
  change_pct: si.change,
}));
await upsertBatch('short_interest', siRows);

// Estimates
const estRows = Object.entries(allEstimates).map(([ticker, e]) => ({
  ticker,
  revenue_ltm: e.revenueLtm, revenue_growth: e.revenueGrowth,
  gross_margins: e.grossMargins, operating_margins: e.operatingMargins,
  fcf: e.fcf, fcf_margin: e.fcfMargin,
  next_q_rev_est: e.nextQRevEst, next_q_rev_growth: e.nextQRevGrowth,
  next_q_eps_est: e.nextQEpsEst, next_q_eps_growth: e.nextQEpsGrowth,
  fy1_rev_est: e.fy1RevEst, fy1_rev_growth: e.fy1RevGrowth,
  fy1_eps_est: e.fy1EpsEst, fy1_eps_growth: e.fy1EpsGrowth,
  fy2_rev_est: e.fy2RevEst, fy2_rev_growth: e.fy2RevGrowth,
  fy2_eps_est: e.fy2EpsEst, fy2_eps_growth: e.fy2EpsGrowth,
  guide_rev_high: e.guideRevHigh ?? null, guide_rev_low: e.guideRevLow ?? null,
  guide_eps_high: e.guideEpsHigh ?? null, guide_eps_low: e.guideEpsLow ?? null,
  consensus_rev: e.consensusRev ?? null, consensus_eps: e.consensusEps ?? null,
  eps_trend_current: e.epsTrendCurrent, eps_trend_7d: e.epsTrend7d,
  eps_trend_30d: e.epsTrend30d, eps_trend_60d: e.epsTrend60d,
  eps_trend_90d: e.epsTrend90d,
  revisions_up_7d: e.revisionsUp7d, revisions_down_7d: e.revisionsDown7d,
  revisions_up_30d: e.revisionsUp30d, revisions_down_30d: e.revisionsDown30d,
  next_q_rev_low: e.nextQRevLow, next_q_rev_high: e.nextQRevHigh,
  next_q_rev_analysts: e.nextQRevAnalysts, next_q_rev_spread: e.nextQRevSpread,
  fy1_rev_low: e.fy1RevLow, fy1_rev_high: e.fy1RevHigh,
  fy1_rev_analysts: e.fy1RevAnalysts, fy1_rev_spread: e.fy1RevSpread,
  fy2_rev_low: e.fy2RevLow, fy2_rev_high: e.fy2RevHigh,
  fy2_rev_analysts: e.fy2RevAnalysts, fy2_rev_spread: e.fy2RevSpread,
  fy1_revisions_up_30d: e.fy1RevisionsUp30d, fy1_revisions_down_30d: e.fy1RevisionsDown30d,
  fy1_eps_trend_current: e.fy1EpsTrendCurrent, fy1_eps_trend_7d: e.fy1EpsTrend7d,
  fy1_eps_trend_30d: e.fy1EpsTrend30d, fy1_eps_trend_60d: e.fy1EpsTrend60d,
  fy1_eps_trend_90d: e.fy1EpsTrend90d,
}));
await upsertBatch('estimates_data', estRows);

console.log('\n=== Deep-Dive Refresh Complete ===');
