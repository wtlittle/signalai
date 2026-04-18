/**
 * refresh_charts.mjs
 * 
 * Fetches 5Y chart data for tickers missing from snapshot.tickers.
 * Updates data-snapshot.json and pushes chart_meta + chart_data to Supabase.
 */
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://wcyirdvvuetzodiedzss.supabase.co';
const SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY;
const supabase = createClient(SUPABASE_URL, SERVICE_KEY);

const snapshotPath = resolve(__dirname, 'data-snapshot.json');
const snapshot = JSON.parse(readFileSync(snapshotPath, 'utf-8'));

// Find missing tickers
const utilsSrc = readFileSync(resolve(__dirname, 'utils.js'), 'utf-8');
const match = utilsSrc.match(/const\s+DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];/);
const allTickers = [...new Set([...match[1].matchAll(/'([A-Z.^]+)'/g)].map(m => m[1]))];
const existingCharts = new Set(Object.keys(snapshot.tickers || {}));
const missing = allTickers.filter(t => !existingCharts.has(t));

console.log(`Total tickers: ${allTickers.length}, existing charts: ${existingCharts.size}, missing: ${missing.length}\n`);

if (missing.length === 0) {
  console.log('All tickers have chart data!');
  process.exit(0);
}

async function fetchChart(ticker) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=5y&interval=1d&includePrePost=false`;
  try {
    const resp = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.chart?.result?.[0] || null;
  } catch (e) {
    return null;
  }
}

let success = 0;
const newChartMeta = [];
const newChartData = [];

for (let i = 0; i < missing.length; i++) {
  const ticker = missing[i];
  process.stdout.write(`  [${i + 1}/${missing.length}] ${ticker}...`);
  
  const result = await fetchChart(ticker);
  if (result) {
    const meta = result.meta || {};
    const timestamps = result.timestamp || [];
    const closes = result.indicators?.quote?.[0]?.close || [];
    
    // Add to snapshot
    snapshot.tickers[ticker] = {
      meta: {
        longName: meta.longName || meta.shortName || ticker,
        shortName: meta.shortName || ticker,
        regularMarketPrice: meta.regularMarketPrice,
        chartPreviousClose: meta.chartPreviousClose,
        previousClose: meta.previousClose,
        fiftyTwoWeekHigh: meta.fiftyTwoWeekHigh,
        fiftyTwoWeekLow: meta.fiftyTwoWeekLow,
        regularMarketVolume: meta.regularMarketVolume,
        currency: meta.currency || 'USD',
      },
      timestamps,
      closes,
    };
    
    // Supabase chart_meta
    newChartMeta.push({
      ticker,
      long_name: meta.longName || meta.shortName || ticker,
      short_name: meta.shortName || ticker,
      regular_market_price: meta.regularMarketPrice,
      chart_previous_close: meta.chartPreviousClose,
      previous_close: meta.previousClose,
      fifty_two_week_high: meta.fiftyTwoWeekHigh,
      fifty_two_week_low: meta.fiftyTwoWeekLow,
      regular_market_volume: meta.regularMarketVolume,
      currency: meta.currency || 'USD',
    });
    
    // Supabase chart_data
    for (let j = 0; j < timestamps.length; j++) {
      if (closes[j] != null) {
        newChartData.push({ ticker, ts: timestamps[j], close: closes[j] });
      }
    }
    
    success++;
    console.log(` ${timestamps.length} data points`);
  } else {
    console.log(' FAILED');
  }
  
  if (i % 3 === 2) await new Promise(r => setTimeout(r, 600));
}

console.log(`\nFetched: ${success}/${missing.length} tickers`);

// Save snapshot
writeFileSync(snapshotPath, JSON.stringify(snapshot));
console.log(`Snapshot updated: ${Object.keys(snapshot.tickers).length} tickers with charts`);

// Push to Supabase
async function upsertBatch(table, rows, conflict, batchSize = 500) {
  if (rows.length === 0) return;
  let inserted = 0;
  for (let i = 0; i < rows.length; i += batchSize) {
    const batch = rows.slice(i, i + batchSize);
    const { error } = await supabase.from(table).upsert(batch, { onConflict: conflict });
    if (error) console.error(`  Error in ${table}: ${error.message}`);
    else inserted += batch.length;
  }
  console.log(`  ${table}: ${inserted} rows upserted`);
}

console.log('\nPushing to Supabase...');
await upsertBatch('chart_meta', newChartMeta, 'ticker');
console.log(`  Total chart data points: ${newChartData.length}`);
await upsertBatch('chart_data', newChartData, 'ticker,ts');

console.log('\n=== Chart Refresh Complete ===');
