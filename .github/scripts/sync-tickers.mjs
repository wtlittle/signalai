/**
 * sync-tickers.mjs
 * 
 * Reads DEFAULT_TICKERS and DEFAULT_PRIVATE_COMPANIES from utils.js,
 * fetches basic quote data from Yahoo Finance for any public tickers,
 * and upserts everything into Supabase.
 * 
 * Runs as a GitHub Action step whenever utils.js changes on main.
 */

import { createClient } from '@supabase/supabase-js';
import { readFileSync } from 'fs';

const SUPABASE_URL = process.env.SUPABASE_URL;
const SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY;

if (!SUPABASE_URL || !SERVICE_KEY) {
  console.error('Missing SUPABASE_URL or SUPABASE_SERVICE_KEY env vars');
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SERVICE_KEY);

// ---------------------------------------------------------------------------
// 1. Parse tickers from utils.js (without eval — regex extraction)
// ---------------------------------------------------------------------------
const utilsSrc = readFileSync('utils.js', 'utf-8');

// Extract DEFAULT_TICKERS array
function extractTickers(src) {
  const match = src.match(/const\s+DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];/);
  if (!match) return [];
  const raw = match[1];
  return [...raw.matchAll(/'([A-Z.]+)'/g)].map(m => m[1]);
}

// Extract DEFAULT_PRIVATE_COMPANIES array (objects with name, subsector, etc.)
function extractPrivateCompanies(src) {
  const match = src.match(/const\s+DEFAULT_PRIVATE_COMPANIES\s*=\s*\[([\s\S]*?)\];/);
  if (!match) return [];
  const raw = match[1];
  const companies = [];
  const objRegex = /\{\s*name:\s*'([^']+)'[^}]*subsector:\s*'([^']+)'[^}]*valuation:\s*'([^']*)'[^}]*funding:\s*'([^']*)'[^}]*revenue:\s*'([^']*)'[^}]*metrics:\s*'([^']*)'[^}]*headquarters:\s*'([^']*)'[^}]*lead_investors:\s*'([^']*)'/g;
  let m;
  while ((m = objRegex.exec(raw)) !== null) {
    companies.push({
      name: m[1],
      subsector: m[2],
      valuation: m[3],
      funding: m[4],
      revenue: m[5],
      metrics: m[6],
      headquarters: m[7],
      lead_investors: m[8],
    });
  }
  return companies;
}

// Extract SUBSECTOR_MAP
function extractSubsectorMap(src) {
  const match = src.match(/const\s+SUBSECTOR_MAP\s*=\s*\{([\s\S]*?)\};/);
  if (!match) return {};
  const raw = match[1];
  const map = {};
  for (const m of raw.matchAll(/'([A-Z.]+)':\s*'([^']+)'/g)) {
    map[m[1]] = m[2];
  }
  return map;
}

// Extract COMMON_NAMES
function extractCommonNames(src) {
  const match = src.match(/const\s+COMMON_NAMES\s*=\s*\{([\s\S]*?)\};/);
  if (!match) return {};
  const raw = match[1];
  const map = {};
  for (const m of raw.matchAll(/'([A-Z.]+)':\s*'([^']+)'/g)) {
    map[m[1]] = m[2];
  }
  return map;
}

// Extract COMPANY_HQ
function extractCompanyHQ(src) {
  const match = src.match(/const\s+COMPANY_HQ\s*=\s*\{([\s\S]*?)\};/);
  if (!match) return {};
  const raw = match[1];
  const map = {};
  for (const m of raw.matchAll(/'([A-Z.]+)':\s*'([^']+)'/g)) {
    map[m[1]] = m[2];
  }
  return map;
}

const tickers = extractTickers(utilsSrc);
const privateCompanies = extractPrivateCompanies(utilsSrc);
const subsectorMap = extractSubsectorMap(utilsSrc);
const commonNames = extractCommonNames(utilsSrc);
const companyHQ = extractCompanyHQ(utilsSrc);

console.log(`Found ${tickers.length} public tickers`);
console.log(`Found ${privateCompanies.length} private companies`);

// ---------------------------------------------------------------------------
// 2. Fetch basic quote data from Yahoo Finance (batch, server-side)
// ---------------------------------------------------------------------------
async function fetchYahooQuotes(tickerList) {
  const results = {};
  // Process in batches of 20
  for (let i = 0; i < tickerList.length; i += 20) {
    const batch = tickerList.slice(i, i + 20);
    const symbols = batch.join(',');
    try {
      const url = `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${symbols}&fields=regularMarketPrice,marketCap,shortName,longName,regularMarketChangePercent,fiftyTwoWeekHigh,fiftyTwoWeekLow,sector,industry`;
      const resp = await fetch(url, {
        headers: { 'User-Agent': 'Mozilla/5.0' },
        signal: AbortSignal.timeout(15000),
      });
      if (resp.ok) {
        const data = await resp.json();
        for (const q of (data.quoteResponse?.result || [])) {
          results[q.symbol] = {
            price: q.regularMarketPrice,
            marketCap: q.marketCap,
            longName: q.longName || q.shortName,
            change1d: q.regularMarketChangePercent,
            fiftyTwoWeekHigh: q.fiftyTwoWeekHigh,
            fiftyTwoWeekLow: q.fiftyTwoWeekLow,
            sector: q.sector,
            industry: q.industry,
          };
        }
      } else {
        console.warn(`Yahoo batch ${i} returned HTTP ${resp.status}`);
      }
    } catch (err) {
      console.warn(`Yahoo batch ${i} error: ${err.message}`);
    }
    // Small delay between batches
    if (i + 20 < tickerList.length) await new Promise(r => setTimeout(r, 500));
  }
  return results;
}

console.log('Fetching Yahoo Finance data...');
const quotes = await fetchYahooQuotes(tickers);
console.log(`Got quotes for ${Object.keys(quotes).length} tickers`);

// ---------------------------------------------------------------------------
// 3. Upsert public tickers to Supabase quotes table
// ---------------------------------------------------------------------------
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

// Build quote rows — merge Yahoo data with our static maps
const quoteRows = tickers.map(ticker => {
  const yq = quotes[ticker] || {};
  return {
    ticker,
    long_name: commonNames[ticker] || yq.longName || ticker,
    price: yq.price || null,
    market_cap: yq.marketCap || null,
    change_1d: yq.change1d || null,
    fifty_two_week_high: yq.fiftyTwoWeekHigh || null,
    fifty_two_week_low: yq.fiftyTwoWeekLow || null,
    sector: yq.sector || null,
    industry: yq.industry || null,
    city: companyHQ[ticker]?.split(',')[0]?.trim() || null,
    state: companyHQ[ticker]?.split(',')[1]?.trim() || null,
  };
});

console.log('Upserting public tickers...');
await upsertBatch('quotes', quoteRows);

// ---------------------------------------------------------------------------
// 4. Upsert private companies to Supabase private_companies table
// ---------------------------------------------------------------------------
// Ensure the private_companies table exists — if not, the upsert will fail
// gracefully and we log it. The table should have: name (PK), subsector,
// valuation, funding, revenue, metrics, headquarters, lead_investors.
const privateRows = privateCompanies.map(pc => ({
  name: pc.name,
  subsector: pc.subsector,
  valuation: pc.valuation,
  funding: pc.funding,
  revenue: pc.revenue,
  metrics: pc.metrics,
  headquarters: pc.headquarters,
  lead_investors: pc.lead_investors,
}));

if (privateRows.length > 0) {
  console.log('Upserting private companies...');
  await upsertBatch('private_companies', privateRows, 'name');
}

// ---------------------------------------------------------------------------
// 5. Update metadata timestamp
// ---------------------------------------------------------------------------
await supabase.from('metadata').upsert({
  key: 'last_github_sync',
  value: new Date().toISOString(),
  updated_at: new Date().toISOString(),
});

console.log('\\nSync complete!');
