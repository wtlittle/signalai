/**
 * refresh_comps_outperf.mjs
 * 
 * Computes cross-sector fundamental comps and S&P 500 outperformance
 * for ALL watchlist tickers using the data already in data-snapshot.json.
 * 
 * Cross-sector comps: For each ticker, find 5 most similar companies
 * from OTHER sectors based on: forward P/E, EV/Revenue, EV/EBITDA,
 * operating margins, revenue growth, FCF margin, beta, market cap.
 * 
 * Outperformance: Compare each ticker's rolling returns against
 * all other tickers to compute a percentile ranking over time.
 */
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://wcyirdvvuetzodiedzss.supabase.co';
const SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY;
const supabase = createClient(SUPABASE_URL, SERVICE_KEY);

// Load snapshot
const snapshotPath = resolve(__dirname, 'data-snapshot.json');
const snapshot = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
const quotes = snapshot.quotes || {};
const chartData = snapshot.tickers || {};

// Parse tickers and subsector map from utils.js
const utilsSrc = readFileSync(resolve(__dirname, 'utils.js'), 'utf-8');
function extractTickers(src) {
  const match = src.match(/const\s+DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];/);
  if (!match) return [];
  return [...new Set([...match[1].matchAll(/'([A-Z.^]+)'/g)].map(m => m[1]))];
}
function extractSubsectorMap(src) {
  const match = src.match(/const\s+SUBSECTOR_MAP\s*=\s*\{([\s\S]*?)\};/);
  if (!match) return {};
  const map = {};
  for (const m of match[1].matchAll(/'([A-Z.^]+)'\s*:\s*'([^']+)'/g)) {
    map[m[1]] = m[2];
  }
  return map;
}
const tickers = extractTickers(utilsSrc);
const subsectorMap = extractSubsectorMap(utilsSrc);
console.log(`Found ${tickers.length} tickers, ${Object.keys(subsectorMap).length} subsector mappings\n`);

// Map subsectors to broader sector categories for cross-sector comparison
const SUBSECTOR_TO_SECTOR = {
  'Cybersecurity': 'Technology', 'Cloud Infrastructure': 'Technology',
  'Enterprise Software': 'Technology', 'Data & Analytics': 'Technology',
  'DevOps & Automation': 'Technology', 'Hyperscalers': 'Technology',
  'Semiconductors': 'Technology', 'Industrial Software': 'Technology',
  'Data Storage': 'Technology', 'Data Center Infrastructure': 'Technology',
  'Vertical Software': 'Technology', 'Applied AI': 'Technology',
  'Networking': 'Technology', 'Marketing Tech': 'Technology',
  'Consumer Electronics': 'Technology', 'Health Tech': 'Technology',
  'Digital Advertising': 'Communication Services',
  'Entertainment & Media': 'Communication Services',
  'Gaming': 'Communication Services',
  'Digital Commerce': 'Consumer Discretionary', 'E-Commerce': 'Consumer Discretionary',
  'Retail': 'Consumer Discretionary', 'Automotive': 'Consumer Discretionary',
  'Consumer Staples': 'Consumer Defensive',
  'Fintech': 'Financial Services', 'Capital Markets': 'Financial Services',
  'Insurance': 'Financial Services',
  'Healthcare Services': 'Healthcare', 'Life Sciences': 'Healthcare',
  'Pharmaceuticals': 'Healthcare',
  'Industrials': 'Industrials', 'Aerospace & Defense': 'Industrials',
  'Energy': 'Energy', 'Power & Utilities': 'Utilities',
  'Materials': 'Basic Materials', 'Specialty Materials': 'Basic Materials',
};

function getSectorForTicker(ticker) {
  // First try quotes data
  const q = quotes[ticker];
  if (q?.sector) return q.sector;
  // Fall back to subsector → sector mapping
  const subsector = subsectorMap[ticker];
  if (subsector) return SUBSECTOR_TO_SECTOR[subsector] || subsector;
  return null;
}

// ===== CROSS-SECTOR COMPS =====
console.log('=== Computing Cross-Sector Fundamental Comps ===\n');

// Build fundamental profiles for all tickers
function getFundamentals(ticker) {
  const q = quotes[ticker];
  if (!q) return null;
  
  // Compute derived metrics
  const ev = q.enterpriseValue || (q.marketCap ? q.marketCap + (q.totalDebt || 0) - (q.totalCash || 0) : null);
  let evRevenue = q.enterpriseToRevenue;
  if (!evRevenue && ev && q.totalRevenue && q.totalRevenue > 0) evRevenue = ev / q.totalRevenue;
  let evEbitda = q.enterpriseToEbitda;
  let fcfMargin = null;
  if (q.freeCashflow && q.totalRevenue && q.totalRevenue > 0) {
    fcfMargin = (q.freeCashflow / q.totalRevenue) * 100;
  }
  
  return {
    ticker,
    name: q.longName || ticker,
    sector: getSectorForTicker(ticker),
    industry: q.industry || subsectorMap[ticker] || null,
    marketCap: q.marketCap || null,
    forwardPE: q.forwardPE || null,
    operatingMargins: q.operatingMargins || null,
    revenueGrowth: q.revenueGrowth || null,
    enterpriseToRevenue: evRevenue || null,
    enterpriseToEbitda: evEbitda || null,
    beta: q.beta || null,
    fcfMargin: fcfMargin,
  };
}

// Z-score normalization
function zScore(values) {
  const valid = values.filter(v => v != null);
  if (valid.length < 2) return values.map(() => 0);
  const mean = valid.reduce((a, b) => a + b, 0) / valid.length;
  const std = Math.sqrt(valid.reduce((a, v) => a + (v - mean) ** 2, 0) / valid.length) || 1;
  return values.map(v => v != null ? (v - mean) / std : 0);
}

// Compute similarity between two fundamental profiles
function computeSimilarity(target, comp) {
  const dims = ['forwardPE', 'enterpriseToRevenue', 'enterpriseToEbitda', 'operatingMargins', 'revenueGrowth', 'fcfMargin', 'beta'];
  let totalWeight = 0;
  let weightedDiff = 0;
  
  for (const dim of dims) {
    const tv = target[dim];
    const cv = comp[dim];
    if (tv == null || cv == null) continue;
    
    // Normalize: use relative difference
    const avg = (Math.abs(tv) + Math.abs(cv)) / 2;
    if (avg === 0) continue;
    const diff = Math.abs(tv - cv) / avg;
    totalWeight += 1;
    weightedDiff += diff;
  }
  
  if (totalWeight < 3) return null; // Need at least 3 dimensions
  const avgDiff = weightedDiff / totalWeight;
  // Convert to similarity score (0-1)
  return Math.max(0, 1 - avgDiff);
}

// Also consider market cap proximity (log scale)
function mcapSimilarity(mc1, mc2) {
  if (!mc1 || !mc2 || mc1 <= 0 || mc2 <= 0) return 0.5;
  const logRatio = Math.abs(Math.log10(mc1 / mc2));
  return Math.max(0, 1 - logRatio / 3); // Within 3 orders of magnitude
}

const allFundamentals = {};
for (const t of tickers) {
  const f = getFundamentals(t);
  if (f) allFundamentals[t] = f;
}
console.log(`  ${Object.keys(allFundamentals).length} tickers with fundamental data`);

const crossSectorComps = {};
let compsCount = 0;

for (const ticker of tickers) {
  const target = allFundamentals[ticker];
  if (!target || !target.sector) continue;
  
  // Find comps from OTHER sectors
  const candidates = [];
  for (const [t, f] of Object.entries(allFundamentals)) {
    if (t === ticker) continue;
    if (f.sector === target.sector) continue; // Different sector only
    
    const fundSim = computeSimilarity(target, f);
    if (fundSim == null) continue;
    
    const mcSim = mcapSimilarity(target.marketCap, f.marketCap);
    // Combined score: 80% fundamental similarity, 20% market cap proximity
    const score = fundSim * 0.8 + mcSim * 0.2;
    candidates.push({ ...f, similarity: score });
  }
  
  // Sort by similarity (descending) and take top 5
  candidates.sort((a, b) => b.similarity - a.similarity);
  const top5 = candidates.slice(0, 5);
  
  if (top5.length >= 2) {
    crossSectorComps[ticker] = {
      ticker,
      sector: target.sector,
      target: {
        ticker,
        name: target.name,
        sector: target.sector,
        industry: target.industry,
        marketCap: target.marketCap,
        forwardPE: target.forwardPE,
        operatingMargins: target.operatingMargins,
        revenueGrowth: target.revenueGrowth,
        enterpriseToRevenue: target.enterpriseToRevenue,
        enterpriseToEbitda: target.enterpriseToEbitda,
        beta: target.beta,
        fcfMargin: target.fcfMargin,
      },
      comps: top5.map(c => ({
        ticker: c.ticker,
        name: c.name,
        sector: c.sector,
        industry: c.industry,
        marketCap: c.marketCap,
        forwardPE: c.forwardPE,
        operatingMargins: c.operatingMargins,
        revenueGrowth: c.revenueGrowth,
        enterpriseToRevenue: c.enterpriseToRevenue,
        enterpriseToEbitda: c.enterpriseToEbitda,
        beta: c.beta,
        fcfMargin: c.fcfMargin,
        similarity: c.similarity,
      })),
    };
    compsCount++;
  }
}
console.log(`  Cross-sector comps computed: ${compsCount}/${tickers.length}\n`);


// ===== S&P 500 OUTPERFORMANCE PERCENTILE =====
console.log('=== Computing Outperformance Percentile ===\n');

// For each ticker with chart data, compute rolling 1Y return percentile
// compared against all other tickers in the watchlist

// Step 1: For each ticker, compute a time series of rolling 1Y returns
function computeRollingReturns(timestamps, closes, lookbackDays = 252) {
  if (!timestamps || !closes || timestamps.length < lookbackDays) return [];
  
  const results = [];
  for (let i = lookbackDays; i < timestamps.length; i++) {
    const current = closes[i];
    const past = closes[i - lookbackDays];
    if (current != null && past != null && past > 0) {
      results.push({
        ts: timestamps[i],
        date: new Date(timestamps[i] * 1000).toISOString().substring(0, 10),
        return1y: ((current - past) / past) * 100,
      });
    }
  }
  return results;
}

// Build rolling returns for all tickers with chart data
const allReturns = {};
let chartCount = 0;
for (const ticker of tickers) {
  const td = chartData[ticker];
  if (!td?.timestamps?.length || !td?.closes?.length) continue;
  const returns = computeRollingReturns(td.timestamps, td.closes);
  if (returns.length > 0) {
    allReturns[ticker] = returns;
    chartCount++;
  }
}
console.log(`  ${chartCount} tickers with chart data for outperformance calc`);

// Step 2: For each date, compute percentile rank of each ticker
// Sample monthly to avoid excessive data
const outperformanceData = {};
let outperfCount = 0;

for (const ticker of tickers) {
  if (!allReturns[ticker]) continue;
  
  const myReturns = allReturns[ticker];
  const percentiles = [];
  
  // Sample at monthly intervals (roughly every 21 trading days)
  for (let i = 0; i < myReturns.length; i += 21) {
    const dp = myReturns[i];
    const targetDate = dp.ts;
    
    // Find how many other tickers we beat on this date
    let below = 0;
    let total = 0;
    for (const [otherTicker, otherReturns] of Object.entries(allReturns)) {
      if (otherTicker === ticker) continue;
      // Find the closest date in other ticker's returns
      const closest = otherReturns.find(r => Math.abs(r.ts - targetDate) < 86400 * 5);
      if (closest) {
        total++;
        if (dp.return1y > closest.return1y) below++;
      }
    }
    
    if (total >= 5) {
      percentiles.push({
        date: dp.date,
        percentile: Math.round((below / total) * 100),
      });
    }
  }
  
  if (percentiles.length >= 3) {
    outperformanceData[ticker] = {
      ticker,
      data: percentiles,
      stockCount: chartCount,
    };
    outperfCount++;
  }
}
console.log(`  Outperformance computed: ${outperfCount}/${tickers.length}\n`);


// ===== UPDATE SNAPSHOT =====
console.log('Updating snapshot...');
snapshot.cross_sector_comps = { ...(snapshot.cross_sector_comps || {}), ...crossSectorComps };
snapshot.outperformance = { ...(snapshot.outperformance || {}), ...outperformanceData };
writeFileSync(snapshotPath, JSON.stringify(snapshot));
console.log(`  Snapshot: ${Object.keys(snapshot.cross_sector_comps).length} cross-sector, ${Object.keys(snapshot.outperformance).length} outperformance`);


// ===== PUSH TO SUPABASE =====
console.log('\nPushing to Supabase...');

async function upsertBatch(table, rows, conflict = 'ticker', batchSize = 50) {
  if (rows.length === 0) { console.log(`  ${table}: 0 rows (skipped)`); return; }
  let inserted = 0;
  for (let i = 0; i < rows.length; i += batchSize) {
    const batch = rows.slice(i, i + batchSize);
    const { error } = await supabase.from(table).upsert(batch, { onConflict: conflict });
    if (error) console.error(`  Error in ${table} batch ${i}: ${error.message}`);
    else inserted += batch.length;
  }
  console.log(`  ${table}: ${inserted} rows upserted`);
}

// Cross-sector targets
const targetRows = Object.entries(crossSectorComps).map(([ticker, cs]) => ({
  ticker,
  name: cs.target?.name,
  sector: cs.target?.sector || cs.sector,
  industry: cs.target?.industry,
  market_cap: cs.target?.marketCap,
  forward_pe: cs.target?.forwardPE,
  operating_margins: cs.target?.operatingMargins,
  revenue_growth: cs.target?.revenueGrowth,
  enterprise_to_revenue: cs.target?.enterpriseToRevenue,
  enterprise_to_ebitda: cs.target?.enterpriseToEbitda,
  beta: cs.target?.beta,
  fcf_margin: cs.target?.fcfMargin,
}));
await upsertBatch('cross_sector_targets', targetRows);

// Cross-sector comps
const compRows = [];
for (const [ticker, cs] of Object.entries(crossSectorComps)) {
  for (const comp of (cs.comps || [])) {
    compRows.push({
      target_ticker: ticker,
      comp_ticker: comp.ticker,
      name: comp.name,
      sector: comp.sector,
      industry: comp.industry,
      market_cap: comp.marketCap,
      forward_pe: comp.forwardPE,
      operating_margins: comp.operatingMargins,
      revenue_growth: comp.revenueGrowth,
      enterprise_to_revenue: comp.enterpriseToRevenue,
      enterprise_to_ebitda: comp.enterpriseToEbitda,
      beta: comp.beta,
      fcf_margin: comp.fcfMargin,
      similarity: comp.similarity,
    });
  }
}
await upsertBatch('cross_sector_comps', compRows, 'target_ticker,comp_ticker');

// Outperformance
const opRows = [];
for (const [ticker, op] of Object.entries(outperformanceData)) {
  for (const dp of op.data) {
    opRows.push({
      ticker,
      date: dp.date,
      percentile: dp.percentile,
      stock_count: op.stockCount,
    });
  }
}
await upsertBatch('outperformance', opRows, 'ticker,date', 200);

console.log('\n=== Comps & Outperformance Refresh Complete ===');
