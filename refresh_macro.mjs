#!/usr/bin/env node
/* ===== refresh_macro.mjs — Fetch macro data, compute regime, generate ideas ===== */
/* Uses Yahoo Finance for market data + rates, and public data APIs for macro indicators */

import https from 'https';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SUPABASE_URL = (process.env.SUPABASE_URL || '').trim() || 'https://wcyirdvvuetzodiedzss.supabase.co';
const SUPABASE_KEY = (process.env.SUPABASE_SERVICE_KEY || '').trim();

// ── Yahoo Finance tickers for macro data ──
// Rates & Policy
const RATES_TICKERS = {
  '^TNX':    { name: '10Y Treasury Yield', pillar: 'policy' },
  '^FVX':    { name: '5Y Treasury Yield', pillar: 'policy' },
  '^TYX':    { name: '30Y Treasury Yield', pillar: 'policy' },
  '^IRX':    { name: '13-Week T-Bill', pillar: 'policy' },
  'DX-Y.NYB': { name: 'US Dollar Index (DXY)', pillar: 'policy' },
  'TLT':     { name: '20+ Year Treasury Bond ETF', pillar: 'policy' },
  'HYG':     { name: 'High Yield Corporate Bond ETF', pillar: 'policy' },
  'LQD':     { name: 'Investment Grade Bond ETF', pillar: 'policy' },
};

// Sector ETFs (SPDR)
const SECTOR_ETFS = {
  'XLK': 'Technology', 'XLF': 'Financials', 'XLV': 'Health Care',
  'XLE': 'Energy', 'XLI': 'Industrials', 'XLC': 'Communication Svcs',
  'XLY': 'Consumer Disc', 'XLP': 'Consumer Staples',
  'XLRE': 'Real Estate', 'XLU': 'Utilities', 'XLB': 'Materials',
};

// Factor ETFs
const FACTOR_ETFS = {
  'MTUM': 'Momentum', 'VLUE': 'Value', 'QUAL': 'Quality',
  'SIZE': 'Size (Small)', 'USMV': 'Low Volatility',
  'IWM': 'Russell 2000', 'IWF': 'Russell Growth', 'IWD': 'Russell Value',
};

// Market indices
const INDEX_TICKERS = {
  '^GSPC': 'S&P 500', '^IXIC': 'Nasdaq Composite',
  '^RUT': 'Russell 2000', '^VIX': 'VIX',
};

// Commodities
const COMMODITY_TICKERS = {
  'CL=F': { name: 'WTI Crude', key: 'WTI' },
  'BZ=F': { name: 'Brent Crude', key: 'BRENT' },
  'NG=F': { name: 'Natural Gas', key: 'NATGAS' },
  'HG=F': { name: 'Copper', key: 'COPPER' },
  'GC=F': { name: 'Gold', key: 'GOLD' },
  'SI=F': { name: 'Silver', key: 'SILVER' },
};

// Inflation proxy tickers
const INFLATION_TICKERS = {
  'TIP':  { name: 'TIPS ETF (Inflation-Protected)', pillar: 'inflation' },
  'RINF': { name: 'ProShares Inflation Expectations', pillar: 'inflation' },
};

// Sentiment/Breadth proxies
const SENTIMENT_TICKERS = {
  'SPHB': { name: 'S&P 500 High Beta ETF', pillar: 'sentiment' },
  'SPLV': { name: 'S&P 500 Low Vol ETF', pillar: 'sentiment' },
};

// ── HTTP Helper ──
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try { resolve(JSON.parse(data)); }
          catch { reject(new Error(`Parse error for ${url}`)); }
        } else reject(new Error(`HTTP ${res.statusCode}`));
      });
    });
    req.on('error', reject);
    req.setTimeout(15000, () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

// ── Yahoo Finance chart fetch ──
async function fetchYahoo(ticker, range = '1y', interval = '1d') {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=${range}&interval=${interval}&includePrePost=false`;
  try {
    const data = await httpGet(url);
    const r = data?.chart?.result?.[0];
    if (!r) return null;
    const m = r.meta || {};
    const ts = r.timestamp || [];
    const cl = r.indicators?.quote?.[0]?.close || [];
    // Filter nulls from the end
    const validCloses = [];
    const validTs = [];
    for (let i = 0; i < cl.length; i++) {
      if (cl[i] != null) { validCloses.push(cl[i]); validTs.push(ts[i]); }
    }
    const price = m.regularMarketPrice;
    // previousClose is NOT available in chart API with range=1y;
    // chartPreviousClose is the price at the START of the range (1y ago), not yesterday.
    // Compute true 1-day change from the last two valid closes.
    const prevDayClose = validCloses.length >= 2 ? validCloses[validCloses.length - 2] : null;
    const change1d = price && prevDayClose ? ((price - prevDayClose) / prevDayClose * 100) : null;
    return {
      ticker, name: m.longName || m.shortName || ticker,
      price, previousClose: prevDayClose,
      change1d,
      timestamps: validTs, closes: validCloses,
    };
  } catch (e) {
    console.warn(`  ✗ ${ticker}: ${e.message}`);
    return null;
  }
}

// ── Performance from closes ──
function perfFromCloses(closes) {
  if (!closes || closes.length < 2) return {};
  const c = closes;
  const lat = c[c.length - 1];
  const pct = (n) => {
    const idx = Math.max(0, c.length - 1 - n);
    return c[idx] ? ((lat - c[idx]) / c[idx] * 100) : null;
  };
  return {
    change_1w: pct(5), change_1m: pct(21), change_3m: pct(63),
    change_6m: pct(126), change_1y: pct(252),
  };
}

// ── Trend direction from closes (recent vs older) ──
function trendDir(closes, shortWindow = 5, longWindow = 20) {
  if (!closes || closes.length < longWindow + 1) return 'neutral';
  const shortAvg = closes.slice(-shortWindow).reduce((a, b) => a + b, 0) / shortWindow;
  const longAvg = closes.slice(-(longWindow + shortWindow), -shortWindow).reduce((a, b) => a + b, 0) / Math.min(longWindow, closes.length - shortWindow);
  const diff = (shortAvg - longAvg) / longAvg;
  if (diff > 0.005) return 'up';
  if (diff < -0.005) return 'down';
  return 'flat';
}

// ── Regime classification ──
function classifyRegime(signals) {
  const { growthTrend, inflationTrend, vix, yieldCurveTrend, creditTrend } = signals;

  // Risk-off: VIX > 25 + credit widening = risk off
  const riskOff = vix > 25 && creditTrend === 'down';

  // Growth: cyclical sectors + small cap performance
  const growthExpanding = growthTrend === 'up';
  const growthContracting = growthTrend === 'down';

  // Inflation: commodity prices + TIPS performance
  const inflationRising = inflationTrend === 'up';
  const inflationFalling = inflationTrend === 'down';

  if (growthExpanding && inflationRising) return {
    regime: 'Reflation', color: '#22c55e',
    desc: 'Growth accelerating with rising inflation. Favors Cyclicals, Value, Energy, Financials. Underweight long-duration growth.',
    favored_sectors: ['XLE', 'XLF', 'XLB', 'XLI'], avoid_sectors: ['XLU', 'XLRE'],
    favored_factors: ['VLUE', 'MTUM', 'IWD'], avoid_factors: ['USMV'],
  };
  if (growthExpanding && !inflationRising) return {
    regime: 'Goldilocks', color: '#3b82f6',
    desc: 'Strong growth, contained inflation. Best environment for equities. Favors Growth, Quality, broad exposure.',
    favored_sectors: ['XLK', 'XLC', 'XLY'], avoid_sectors: ['XLE', 'XLU'],
    favored_factors: ['QUAL', 'MTUM', 'IWF'], avoid_factors: [],
  };
  if (growthContracting && inflationRising) return {
    regime: 'Stagflation', color: '#ef4444',
    desc: 'Slowing growth with persistent inflation. Worst environment for equities. Favor real assets, cash, energy. Avoid long-duration.',
    favored_sectors: ['XLE', 'XLP'], avoid_sectors: ['XLK', 'XLRE', 'XLY'],
    favored_factors: ['VLUE', 'USMV'], avoid_factors: ['IWF', 'MTUM'],
  };
  if (growthContracting && inflationFalling) return {
    regime: 'Deflation Risk', color: '#eab308',
    desc: 'Weakening growth and falling prices. Favors Treasuries, Defensives, Quality. Reduce cyclical exposure.',
    favored_sectors: ['XLU', 'XLV', 'XLP'], avoid_sectors: ['XLE', 'XLF', 'XLB'],
    favored_factors: ['QUAL', 'USMV'], avoid_factors: ['VLUE', 'IWM'],
  };
  if (growthContracting) return {
    regime: 'Slowdown', color: '#f97316',
    desc: 'Growth decelerating. Lean defensive: Quality, Low Vol, Healthcare, Staples. Trim cyclicals.',
    favored_sectors: ['XLV', 'XLP', 'XLU'], avoid_sectors: ['XLY', 'XLI', 'XLB'],
    favored_factors: ['QUAL', 'USMV'], avoid_factors: ['IWM'],
  };
  if (growthExpanding) return {
    regime: 'Expansion', color: '#22c55e',
    desc: 'Broad economic expansion. Favors equities broadly, tilt Momentum and Growth.',
    favored_sectors: ['XLK', 'XLF', 'XLI', 'XLY'], avoid_sectors: ['XLU'],
    favored_factors: ['MTUM', 'IWF'], avoid_factors: [],
  };
  if (riskOff) return {
    regime: 'Risk-Off', color: '#ef4444',
    desc: 'Elevated volatility and widening credit spreads. Reduce risk. Favor cash, Treasuries, Defensives.',
    favored_sectors: ['XLV', 'XLP', 'XLU'], avoid_sectors: ['XLY', 'XLK'],
    favored_factors: ['USMV', 'QUAL'], avoid_factors: ['IWM', 'MTUM'],
  };

  return {
    regime: 'Transition', color: '#9ca3af',
    desc: 'Mixed signals across pillars. Monitor for directional clarity before adjusting positioning.',
    favored_sectors: [], avoid_sectors: [],
    favored_factors: ['QUAL'], avoid_factors: [],
  };
}

// ── Pillar scoring from ETF-based signals ──
function scorePillarFromETFs(data, config) {
  // Each config item: { ticker, weight, invert }
  let totalScore = 0; let totalWeight = 0;
  for (const item of config) {
    const etf = data[item.ticker];
    if (!etf || !etf.closes || etf.closes.length < 21) continue;
    const trend = trendDir(etf.closes);
    let score = trend === 'up' ? 1 : trend === 'down' ? -1 : 0;
    if (item.invert) score = -score;
    totalScore += score * (item.weight || 1);
    totalWeight += (item.weight || 1);
  }
  return totalWeight > 0 ? totalScore / totalWeight : 0;
}

// ── Map ticker to sector ETF ──
function mapTickerToSector(ticker, subsectorMap) {
  const sub = subsectorMap[ticker] || 'Other';
  const m = {
    'Hyperscalers': 'XLK', 'Semiconductors': 'XLK', 'Enterprise Software': 'XLK',
    'Cybersecurity': 'XLK', 'Data & Analytics': 'XLK', 'Cloud Infrastructure': 'XLK',
    'DevOps & Automation': 'XLK', 'Applied AI': 'XLK', 'Industrial Software': 'XLK',
    'Vertical Software': 'XLK', 'Marketing Tech': 'XLK', 'Data Storage': 'XLK',
    'Data Center Infrastructure': 'XLK', 'Consumer Electronics': 'XLK',
    'Fintech': 'XLF', 'Capital Markets': 'XLF', 'Insurance': 'XLF',
    'Digital Commerce': 'XLY', 'E-Commerce': 'XLY', 'Retail': 'XLY', 'Automotive': 'XLY',
    'Digital Advertising': 'XLC', 'Entertainment & Media': 'XLC', 'Gaming': 'XLC', 'Networking': 'XLC',
    'Healthcare Services': 'XLV', 'Life Sciences': 'XLV', 'Pharmaceuticals': 'XLV', 'Health Tech': 'XLV',
    'Energy': 'XLE', 'Power & Utilities': 'XLU',
    'Aerospace & Defense': 'XLI', 'Industrials': 'XLI',
    'Consumer Staples': 'XLP', 'Materials': 'XLB', 'Specialty Materials': 'XLB',
  };
  return m[sub] || 'XLK';
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Main ──
async function main() {
  console.log('=== SignalAI Macro Refresh ===');
  console.log(`Time: ${new Date().toISOString()}\n`);

  const allData = {}; // ticker → Yahoo result
  const macroData = {
    generated: new Date().toISOString(),
    pillars: { growth: {}, inflation: {}, policy: {}, sentiment: {} },
    regime: null,
    sectors: {}, factors: {}, indices: {}, commodities: {}, rates: {},
    ideas: { own: [], avoid: [] },
  };

  // ── Fetch all Yahoo data ──
  const allTickers = [
    ...Object.keys(RATES_TICKERS),
    ...Object.keys(SECTOR_ETFS),
    ...Object.keys(FACTOR_ETFS),
    ...Object.keys(INDEX_TICKERS),
    ...Object.keys(COMMODITY_TICKERS),
    ...Object.keys(INFLATION_TICKERS),
    ...Object.keys(SENTIMENT_TICKERS),
  ];

  console.log(`Fetching ${allTickers.length} tickers from Yahoo Finance...`);
  const batchSize = 4;
  for (let i = 0; i < allTickers.length; i += batchSize) {
    const batch = allTickers.slice(i, i + batchSize);
    const results = await Promise.all(batch.map(t => fetchYahoo(t, '1y', '1d')));
    results.forEach((r, idx) => {
      if (r) {
        allData[batch[idx]] = r;
        const perf = perfFromCloses(r.closes);
        const entry = { name: r.name, price: r.price, change1d: r.change1d, ...perf };

        // Route to correct bucket
        if (SECTOR_ETFS[batch[idx]]) {
          macroData.sectors[batch[idx]] = { ...entry, sectorName: SECTOR_ETFS[batch[idx]] };
          console.log(`  ✓ ${SECTOR_ETFS[batch[idx]]} (${batch[idx]}): $${r.price?.toFixed(2)}, 1d ${r.change1d?.toFixed(1)}%`);
        } else if (FACTOR_ETFS[batch[idx]]) {
          macroData.factors[batch[idx]] = { ...entry, factorName: FACTOR_ETFS[batch[idx]] };
          console.log(`  ✓ ${FACTOR_ETFS[batch[idx]]} (${batch[idx]}): $${r.price?.toFixed(2)}`);
        } else if (INDEX_TICKERS[batch[idx]]) {
          macroData.indices[batch[idx]] = entry;
          console.log(`  ✓ ${INDEX_TICKERS[batch[idx]]}: ${r.price?.toFixed(2)}`);
        } else if (COMMODITY_TICKERS[batch[idx]]) {
          const cfg = COMMODITY_TICKERS[batch[idx]];
          macroData.commodities[cfg.key] = { ...entry, ticker: batch[idx], commodityName: cfg.name };
          console.log(`  ✓ ${cfg.name}: $${r.price?.toFixed(2)}`);
        } else if (RATES_TICKERS[batch[idx]]) {
          macroData.rates[batch[idx]] = { ...entry, rateName: RATES_TICKERS[batch[idx]].name };
          console.log(`  ✓ ${RATES_TICKERS[batch[idx]].name}: ${r.price?.toFixed(2)}`);
        } else {
          allData[batch[idx]] = r;
        }
      }
    });
    if (i + batchSize < allTickers.length) await sleep(300);
  }

  // ── Score pillars using market-based signals ──
  console.log('\n--- Scoring pillars ---');

  // Growth: cyclical sectors outperforming defensive = expansion
  const growthScore = scorePillarFromETFs(allData, [
    { ticker: 'XLI', weight: 1 },         // industrials up = growth
    { ticker: 'XLY', weight: 1 },         // consumer disc up = growth
    { ticker: 'IWM', weight: 1 },         // small caps up = growth
    { ticker: 'XLP', weight: 0.5, invert: true }, // staples up = defensive = weak growth
    { ticker: 'XLU', weight: 0.5, invert: true }, // utilities up = defensive
  ]);

  // Inflation: commodities + TIPS direction
  const inflationScore = scorePillarFromETFs(allData, [
    { ticker: 'CL=F', weight: 1 },    // oil up = inflation
    { ticker: 'HG=F', weight: 0.7 },  // copper up = inflation
    { ticker: 'GC=F', weight: 0.5 },  // gold up = inflation hedge
    { ticker: 'TIP', weight: 0.8 },   // TIPS up = inflation expectations
    { ticker: 'DX-Y.NYB', weight: 0.5, invert: true }, // dollar down = inflationary
  ]);

  // Policy: yields falling + TLT rising = accommodative
  const policyScore = scorePillarFromETFs(allData, [
    { ticker: 'TLT', weight: 1 },         // TLT up = rates falling = accommodative
    { ticker: '^TNX', weight: 1, invert: true }, // yields up = restrictive
    { ticker: 'LQD', weight: 0.8 },       // IG credit up = easy conditions
    { ticker: 'HYG', weight: 0.8 },       // HY up = easy conditions
  ]);

  // Sentiment: high beta vs low vol spread + VIX
  const vixPrice = allData['^VIX']?.price || 20;
  const sentimentFromETFs = scorePillarFromETFs(allData, [
    { ticker: 'SPHB', weight: 1 },         // high beta up = risk-on
    { ticker: 'SPLV', weight: 0.5, invert: true }, // low vol up = risk-off
    { ticker: 'IWM', weight: 0.5 },        // small caps up = risk-on
  ]);
  // VIX adjustment
  const vixAdj = vixPrice > 30 ? -0.5 : vixPrice > 25 ? -0.25 : vixPrice < 15 ? 0.25 : 0;
  const sentimentScore = sentimentFromETFs + vixAdj;

  const scoreToLabel = (score, posLabel, negLabel) => {
    if (score > 0.3) return { label: posLabel, color: 'green' };
    if (score < -0.3) return { label: negLabel, color: 'red' };
    return { label: 'Neutral', color: 'yellow' };
  };

  macroData.pillars.growth = {
    score: parseFloat(growthScore.toFixed(3)),
    ...scoreToLabel(growthScore, 'Expanding', 'Contracting'),
    signals: buildPillarSignals('growth', allData, macroData),
  };
  macroData.pillars.inflation = {
    score: parseFloat(inflationScore.toFixed(3)),
    ...scoreToLabel(inflationScore, 'Rising', 'Falling'),
    signals: buildPillarSignals('inflation', allData, macroData),
  };
  macroData.pillars.policy = {
    score: parseFloat(policyScore.toFixed(3)),
    ...scoreToLabel(policyScore, 'Accommodative', 'Restrictive'),
    signals: buildPillarSignals('policy', allData, macroData),
  };
  macroData.pillars.sentiment = {
    score: parseFloat(sentimentScore.toFixed(3)),
    ...scoreToLabel(sentimentScore, 'Risk-On', 'Risk-Off'),
    signals: buildPillarSignals('sentiment', allData, macroData),
  };

  console.log(`  Growth:    ${growthScore.toFixed(3)} → ${macroData.pillars.growth.label}`);
  console.log(`  Inflation: ${inflationScore.toFixed(3)} → ${macroData.pillars.inflation.label}`);
  console.log(`  Policy:    ${policyScore.toFixed(3)} → ${macroData.pillars.policy.label}`);
  console.log(`  Sentiment: ${sentimentScore.toFixed(3)} → ${macroData.pillars.sentiment.label}`);

  // ── Classify regime ──
  const growthTrend = growthScore > 0.3 ? 'up' : growthScore < -0.3 ? 'down' : 'flat';
  const inflationTrend = inflationScore > 0.3 ? 'up' : inflationScore < -0.3 ? 'down' : 'flat';
  const creditTrend = allData['HYG'] ? trendDir(allData['HYG'].closes) : 'flat';

  macroData.regime = classifyRegime({ growthTrend, inflationTrend, vix: vixPrice, yieldCurveTrend: 'flat', creditTrend });
  console.log(`\n  REGIME: ${macroData.regime.regime}`);
  console.log(`  ${macroData.regime.desc}`);
  console.log(`  Favored: ${macroData.regime.favored_sectors.map(s => SECTOR_ETFS[s] || s).join(', ')}`);
  console.log(`  Avoid:   ${macroData.regime.avoid_sectors.map(s => SECTOR_ETFS[s] || s).join(', ')}`);

  // ── Generate stock ideas ──
  console.log('\n--- Generating stock ideas ---');
  const utilsPath = path.join(__dirname, 'utils.js');
  const utilsContent = fs.readFileSync(utilsPath, 'utf-8');

  const subsectorMap = {};
  const mapMatch = utilsContent.match(/const SUBSECTOR_MAP\s*=\s*\{([\s\S]*?)\};/);
  if (mapMatch) for (const m of mapMatch[1].matchAll(/'([^']+)'\s*:\s*'([^']+)'/g)) subsectorMap[m[1]] = m[2];

  const watchlist = [];
  const tickerMatch = utilsContent.match(/const DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];/);
  if (tickerMatch) for (const m of tickerMatch[1].matchAll(/'([A-Z.]+)'/g)) watchlist.push(m[1]);
  const uniqueWatchlist = [...new Set(watchlist)];
  console.log(`  Watchlist: ${uniqueWatchlist.length} tickers`);

  const commonNames = {};
  const namesMatch = utilsContent.match(/const COMMON_NAMES\s*=\s*\{([\s\S]*?)\};/);
  if (namesMatch) for (const m of namesMatch[1].matchAll(/'([^']+)'\s*:\s*'([^']+)'/g)) commonNames[m[1]] = m[2];

  // Map each to sector ETF
  const tickerSectorMap = {};
  for (const t of uniqueWatchlist) tickerSectorMap[t] = mapTickerToSector(t, subsectorMap);

  const { favored_sectors, avoid_sectors, favored_factors, avoid_factors } = macroData.regime;

  const ownCandidates = uniqueWatchlist.filter(t => favored_sectors.includes(tickerSectorMap[t]));
  const avoidCandidates = uniqueWatchlist.filter(t => avoid_sectors.includes(tickerSectorMap[t]));

  macroData.ideas.own = ownCandidates.slice(0, 5).map(t => ({
    ticker: t, name: commonNames[t] || t,
    subsector: subsectorMap[t] || 'Other',
    sectorEtf: tickerSectorMap[t],
    sectorName: SECTOR_ETFS[tickerSectorMap[t]] || tickerSectorMap[t],
    reason: `In favored sector (${SECTOR_ETFS[tickerSectorMap[t]]}) for ${macroData.regime.regime} regime`,
  }));

  macroData.ideas.avoid = avoidCandidates.slice(0, 5).map(t => ({
    ticker: t, name: commonNames[t] || t,
    subsector: subsectorMap[t] || 'Other',
    sectorEtf: tickerSectorMap[t],
    sectorName: SECTOR_ETFS[tickerSectorMap[t]] || tickerSectorMap[t],
    reason: `In unfavored sector (${SECTOR_ETFS[tickerSectorMap[t]]}) for ${macroData.regime.regime} regime`,
  }));

  console.log(`  Own:   ${macroData.ideas.own.map(i => i.ticker).join(', ') || '(none)'}`);
  console.log(`  Avoid: ${macroData.ideas.avoid.map(i => i.ticker).join(', ') || '(none)'}`);

  // ── Save ──
  const outPath = path.join(__dirname, 'macro_data.json');
  fs.writeFileSync(outPath, JSON.stringify(macroData, null, 2));
  console.log(`\n✓ Saved macro_data.json (${(fs.statSync(outPath).size / 1024).toFixed(1)} KB)`);

  // ── Push to Supabase ──
  if (SUPABASE_KEY) {
    console.log('\n--- Pushing to Supabase ---');
    try {
      await upsertSupabaseMacro(macroData);
      console.log('  ✓ Pushed macro_data to Supabase');
    } catch (e) {
      console.warn('  ✗ Supabase push failed:', e.message);
    }
  }

  console.log('\n=== Macro refresh complete ===');
}

// ── Build pillar signal details for frontend ──
function buildPillarSignals(pillar, allData, macroData) {
  const signals = [];
  const addSignal = (ticker, customName) => {
    const d = allData[ticker];
    if (!d) return;
    const perf = perfFromCloses(d.closes);
    const trend = trendDir(d.closes);
    signals.push({
      ticker, name: customName || d.name,
      price: d.price, change1d: d.change1d,
      change1w: perf.change_1w, change1m: perf.change_1m,
      change3m: perf.change_3m, change6m: perf.change_6m,
      change1y: perf.change_1y,
      trend, direction: trend === 'up' ? '↑' : trend === 'down' ? '↓' : '→',
    });
  };

  if (pillar === 'growth') {
    addSignal('XLI', 'Industrials ETF');
    addSignal('XLY', 'Consumer Disc ETF');
    addSignal('IWM', 'Russell 2000');
    if (macroData.indices['^GSPC']) signals.push({
      ticker: '^GSPC', name: 'S&P 500',
      price: macroData.indices['^GSPC'].price,
      change1d: macroData.indices['^GSPC'].change1d,
      change1w: macroData.indices['^GSPC'].change_1w,
      change1m: macroData.indices['^GSPC'].change_1m,
      change3m: macroData.indices['^GSPC'].change_3m,
      change6m: macroData.indices['^GSPC'].change_6m,
      change1y: macroData.indices['^GSPC'].change_1y,
      trend: allData['^GSPC'] ? trendDir(allData['^GSPC'].closes) : 'flat',
      direction: '→',
    });
  } else if (pillar === 'inflation') {
    addSignal('CL=F', 'WTI Crude');
    addSignal('HG=F', 'Copper');
    addSignal('GC=F', 'Gold');
    addSignal('TIP', 'TIPS ETF');
    addSignal('DX-Y.NYB', 'US Dollar (DXY)');
  } else if (pillar === 'policy') {
    addSignal('^TNX', '10Y Treasury Yield');
    addSignal('^FVX', '5Y Treasury Yield');
    addSignal('TLT', 'Long Bond ETF');
    addSignal('HYG', 'High Yield ETF');
    addSignal('LQD', 'IG Credit ETF');
    addSignal('DX-Y.NYB', 'US Dollar (DXY)');
  } else if (pillar === 'sentiment') {
    addSignal('SPHB', 'S&P High Beta');
    addSignal('SPLV', 'S&P Low Vol');
    signals.push({
      ticker: '^VIX', name: 'VIX',
      price: macroData.indices['^VIX']?.price,
      change1d: macroData.indices['^VIX']?.change1d,
      change1w: macroData.indices['^VIX']?.change_1w,
      change1m: macroData.indices['^VIX']?.change_1m,
      change3m: macroData.indices['^VIX']?.change_3m,
      change6m: macroData.indices['^VIX']?.change_6m,
      change1y: macroData.indices['^VIX']?.change_1y,
      trend: allData['^VIX'] ? trendDir(allData['^VIX'].closes) : 'flat',
      direction: (macroData.indices['^VIX']?.price || 20) > 25 ? '⚠' : '→',
    });
  }
  return signals;
}

// ── Supabase upsert ──
async function upsertSupabaseMacro(macroData) {
  const body = JSON.stringify([
    { key: 'macro_snapshot', value: JSON.stringify(macroData), updated_at: new Date().toISOString() }
  ]);
  return new Promise((resolve, reject) => {
    const urlObj = new URL(`${SUPABASE_URL}/rest/v1/metadata`);
    const opts = {
      hostname: urlObj.hostname, path: urlObj.pathname + '?on_conflict=key',
      method: 'POST',
      headers: {
        'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}`,
        'Content-Type': 'application/json', 'Prefer': 'resolution=merge-duplicates',
        'Content-Length': Buffer.byteLength(body),
      },
    };
    const req = https.request(opts, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => res.statusCode < 300 ? resolve(d) : reject(new Error(`HTTP ${res.statusCode}: ${d.slice(0,200)}`)));
    });
    req.on('error', reject);
    req.write(body); req.end();
  });
}

main().catch(e => { console.error('Fatal:', e); process.exit(1); });
