#!/usr/bin/env node
/**
 * refresh_political.mjs
 *
 * Fetches congressional stock trades from QuiverQuant and committee membership
 * from Congress.gov (with ProPublica fallback), enriches with committee-overlap
 * scoring, and writes political_data.json + merges political_exposure into
 * data-snapshot.json.
 *
 * Intended cron cadence: 0 5 * * 1 (Monday 05:00 UTC, Sunday night US)
 * Rationale: STOCK Act allows 45-day disclosure lag; weekly is sufficient.
 *
 * Env vars:
 *   PROPUBLICA_API_KEY   — ProPublica Congress API key (optional, API retired 2024)
 *   CONGRESS_GOV_API_KEY — Congress.gov API key (recommended fallback)
 *   SUPABASE_URL         — Supabase project URL (optional, for future push)
 *   SUPABASE_SERVICE_KEY — Supabase service key (optional)
 */
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// --- Parse tickers + sectors from data-snapshot.json ---
const snapshotPath = path.join(__dirname, 'data-snapshot.json');
let snapshotData = {};
let tickerUniverse = new Set();
let tickerSectorMap = {};

if (existsSync(snapshotPath)) {
  try {
    snapshotData = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
    const quotes = snapshotData.quotes || {};
    for (const [ticker, q] of Object.entries(quotes)) {
      tickerUniverse.add(ticker);
      if (q.sector) tickerSectorMap[ticker] = q.sector;
    }
    console.log(`Loaded ${tickerUniverse.size} tickers from data-snapshot.json`);
  } catch (e) {
    console.warn('Could not parse data-snapshot.json:', e.message);
  }
}

// --- Sector to committee mapping ---
const SECTOR_COMMITTEE_MAP = {
  'Technology': ['Senate Commerce', 'House Science & Technology', 'Senate Judiciary'],
  'Communication Services': ['Senate Commerce', 'House Science & Technology', 'Senate Judiciary'],
  'Industrials': ['Senate Armed Services', 'House Armed Services'],
  'Healthcare': ['Senate Finance', 'House Energy & Commerce', 'Senate HELP'],
  'Health Care': ['Senate Finance', 'House Energy & Commerce', 'Senate HELP'],
  'Financial Services': ['Senate Banking', 'House Financial Services'],
  'Financials': ['Senate Banking', 'House Financial Services'],
  'Energy': ['Senate Energy & Natural Resources', 'House Energy & Commerce'],
  'Consumer Discretionary': ['Senate Commerce', 'House Energy & Commerce'],
  'Consumer Staples': ['Senate Commerce', 'House Energy & Commerce'],
  'Real Estate': ['Senate Banking', 'House Financial Services'],
  'Utilities': ['Senate Energy & Natural Resources', 'House Energy & Commerce'],
  'Materials': ['Senate Energy & Natural Resources', 'House Energy & Commerce'],
};

function normalizeSector(sector) {
  if (!sector) return null;
  const s = sector.trim();
  // Direct match
  if (SECTOR_COMMITTEE_MAP[s]) return s;
  // Fuzzy match
  const lower = s.toLowerCase();
  if (lower.includes('tech') || lower.includes('software') || lower.includes('semiconductor')) return 'Technology';
  if (lower.includes('defense') || lower.includes('aerospace')) return 'Industrials';
  if (lower.includes('health') || lower.includes('pharma') || lower.includes('biotech')) return 'Health Care';
  if (lower.includes('financ') || lower.includes('bank') || lower.includes('insurance')) return 'Financials';
  if (lower.includes('energy') || lower.includes('oil') || lower.includes('gas')) return 'Energy';
  if (lower.includes('communication') || lower.includes('media') || lower.includes('telecom')) return 'Communication Services';
  return s;
}

function getOversightCommittees(sector) {
  const normalized = normalizeSector(sector);
  return SECTOR_COMMITTEE_MAP[normalized] || [];
}

// --- HTTP helpers ---
async function fetchJson(url, opts = {}) {
  const { headers = {}, timeoutMs = 15000 } = opts;
  try {
    const resp = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', ...headers },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!resp.ok) {
      console.warn(`  HTTP ${resp.status} for ${url}`);
      return null;
    }
    return await resp.json();
  } catch (e) {
    console.warn(`  Fetch failed for ${url}: ${e.message}`);
    return null;
  }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// --- Step 1: Fetch congressional trades from QuiverQuant ---
async function fetchCongressionalTrades() {
  console.log('\n--- Fetching congressional trades from QuiverQuant ---');
  const url = 'https://api.quiverquant.com/beta/live/congresstrading';
  const data = await fetchJson(url, { timeoutMs: 30000 });
  if (!data || !Array.isArray(data)) {
    console.warn('  QuiverQuant returned no data or unexpected format');
    return [];
  }

  console.log(`  Raw trades from QuiverQuant: ${data.length}`);

  // Filter to last 90 days and tickers in our universe
  const now = new Date();
  const ninetyDaysAgo = new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000);

  const filtered = data.filter(t => {
    const ticker = (t.Ticker || t.ticker || '').toUpperCase();
    if (!tickerUniverse.has(ticker)) return false;
    const dateStr = t.TransactionDate || t.transaction_date || t.Date || t.date;
    if (!dateStr) return false;
    const tradeDate = new Date(dateStr);
    return tradeDate >= ninetyDaysAgo;
  });

  console.log(`  After filtering to watchlist + 90d: ${filtered.length} trades`);

  return filtered.map(t => {
    const ticker = (t.Ticker || t.ticker || '').toUpperCase();
    const politician = t.Representative || t.Name || t.politician || 'Unknown';
    const dateStr = t.TransactionDate || t.transaction_date || t.Date || t.date;
    const tradeDate = new Date(dateStr);
    const daysSince = Math.floor((now - tradeDate) / (1000 * 60 * 60 * 24));
    const isFamily = /spouse|dependent|child|family/i.test(t.Description || t.Filer || '');

    return {
      ticker,
      politician: isFamily ? `${politician} (family)` : politician,
      chamber: t.House || t.Chamber || t.chamber || 'Unknown',
      party: t.Party || t.party || '',
      trade_date: dateStr ? tradeDate.toISOString().slice(0, 10) : null,
      transaction_type: t.Transaction || t.Type || t.transaction_type || 'Unknown',
      amount_range: t.Amount || t.Range || t.amount || 'N/A',
      description: t.Description || t.Filer || '',
      days_since_trade: daysSince,
      _is_family: isFamily,
    };
  });
}

// --- Step 2: Fetch committee membership ---
// PRIMARY: keyless GitHub YAML feed (unitedstates/congress-legislators)
// FALLBACKS (opt-in via env): ProPublica (retired 2024), Congress.gov
async function fetchCommitteeMembership() {
  console.log('\n--- Fetching committee membership ---');

  // PRIMARY PATH — no API key needed
  try {
    const committees = await fetchUnitedStatesCommitteeFeed();
    if (committees && Object.keys(committees).length > 0) {
      return committees;
    }
    console.log('  GitHub feed returned empty; trying fallback sources');
  } catch (err) {
    console.warn(`  GitHub feed failed: ${err.message}; trying fallback sources`);
  }

  // FALLBACK 1: ProPublica (legacy)
  const propublicaKey = (process.env.PROPUBLICA_API_KEY || '').trim();
  if (propublicaKey) {
    console.log('  Trying ProPublica Congress API...');
    const committees = await fetchProPublicaCommittees(propublicaKey);
    if (committees && Object.keys(committees).length > 0) {
      console.log(`  ProPublica: ${Object.keys(committees).length} members mapped`);
      return committees;
    }
    console.log('  ProPublica failed or returned empty, falling back to Congress.gov');
  }

  // FALLBACK 2: Congress.gov
  const congressKey = (process.env.CONGRESS_GOV_API_KEY || '').trim();
  if (congressKey) {
    const committees = await fetchCongressGovCommittees(congressKey);
    if (committees && Object.keys(committees).length > 0) {
      console.log(`  Congress.gov: ${Object.keys(committees).length} members mapped`);
      return committees;
    }
  }

  console.log('  All committee sources failed; proceeding without committee data');
  return {};
}

// Keyless committee feed via unitedstates/congress-legislators GitHub Pages
// Returns { fullName: [committeeName, ...] } — same shape as ProPublica/Congress.gov
async function fetchUnitedStatesCommitteeFeed() {
  console.log('  Trying unitedstates/congress-legislators (keyless GitHub feed)...');
  const base = 'https://unitedstates.github.io/congress-legislators/';
  const [committees, membership, legislators] = await Promise.all([
    fetchJson(base + 'committees-current.json', { timeoutMs: 25000 }),
    fetchJson(base + 'committee-membership-current.json', { timeoutMs: 25000 }),
    fetchJson(base + 'legislators-current.json', { timeoutMs: 30000 }),
  ]);
  if (!committees || !membership || !legislators) {
    console.warn('  GitHub feed: one or more endpoints returned no data');
    return {};
  }

  // Build committee_id -> display name (recurse subcommittees as "Parent - Child")
  const committeeNameById = {};
  function walk(c) {
    const id = c.thomas_id;
    const rawName = (c.name || '').trim();
    // Keep full name ("Senate Committee on Finance") so downstream fuzzy match works
    if (id) committeeNameById[id] = rawName;
    for (const sub of (c.subcommittees || [])) {
      const sid = (id || '') + (sub.thomas_id || '');
      if (sid) committeeNameById[sid] = `${rawName} - ${sub.name}`;
    }
  }
  for (const c of committees) walk(c);

  // Build bioguide -> { fullName }
  const legByBioguide = {};
  for (const l of legislators) {
    const bg = (l.id || {}).bioguide;
    if (!bg) continue;
    const nm = l.name || {};
    const fn = nm.official_full || `${nm.first || ''} ${nm.last || ''}`.trim();
    if (fn) legByBioguide[bg] = fn;
  }

  // Compose name -> [committee names]
  const nameMap = {};
  for (const [cid, members] of Object.entries(membership)) {
    const cname = committeeNameById[cid];
    if (!cname || !Array.isArray(members)) continue;
    for (const m of members) {
      const fn = legByBioguide[m.bioguide];
      if (!fn) continue;
      if (!nameMap[fn]) nameMap[fn] = [];
      if (!nameMap[fn].includes(cname)) nameMap[fn].push(cname);
    }
  }
  console.log(`  GitHub feed: ${Object.keys(nameMap).length} legislators across ${Object.keys(committeeNameById).length} committees`);
  return nameMap;
}

async function fetchProPublicaCommittees(apiKey) {
  const memberMap = {};
  const headers = { 'X-API-Key': apiKey };
  const chambers = ['senate', 'house'];

  for (const chamber of chambers) {
    const url = `https://api.propublica.org/congress/v1/119/${chamber}/committees.json`;
    const data = await fetchJson(url, { headers, timeoutMs: 15000 });
    if (!data || !data.results) continue;

    for (const result of data.results) {
      const committees = result.committees || [];
      for (const committee of committees) {
        const committeeName = committee.name || '';
        const membersUrl = committee.api_uri;
        if (!membersUrl) continue;

        const memberData = await fetchJson(membersUrl, { headers, timeoutMs: 15000 });
        if (!memberData || !memberData.results) continue;

        for (const mr of memberData.results) {
          const members = mr.current_members || [];
          for (const m of members) {
            const name = m.name || `${m.first_name || ''} ${m.last_name || ''}`.trim();
            if (!name) continue;
            if (!memberMap[name]) memberMap[name] = [];
            if (!memberMap[name].includes(committeeName)) {
              memberMap[name].push(committeeName);
            }
          }
        }
        await sleep(200);
      }
    }
  }
  return memberMap;
}

async function fetchCongressGovCommittees(apiKey) {
  const memberMap = {};
  const chambers = ['senate', 'house'];

  for (const chamber of chambers) {
    const listUrl = `https://api.congress.gov/v3/committee/${chamber}?api_key=${encodeURIComponent(apiKey)}&format=json&limit=250`;
    const listData = await fetchJson(listUrl, { timeoutMs: 20000 });
    if (!listData || !listData.committees) continue;

    for (const committee of listData.committees) {
      const committeeName = committee.name || '';
      const systemCode = committee.systemCode || '';
      if (!systemCode) continue;

      const membersUrl = `https://api.congress.gov/v3/committee/${chamber}/${systemCode}/members?api_key=${encodeURIComponent(apiKey)}&format=json&limit=250`;
      const membersData = await fetchJson(membersUrl, { timeoutMs: 15000 });
      if (!membersData) continue;

      const members = membersData.members || membersData.committeeMemberships || [];
      for (const m of members) {
        const name = m.name || `${m.firstName || ''} ${m.lastName || ''}`.trim();
        if (!name) continue;
        if (!memberMap[name]) memberMap[name] = [];
        if (!memberMap[name].includes(committeeName)) {
          memberMap[name].push(committeeName);
        }
      }
      await sleep(300);
    }
  }
  return memberMap;
}

// --- Step 3: Enrich trades with committee overlap + exposure score ---
function enrichTrades(trades, committeeLookup) {
  return trades.map(trade => {
    const sector = tickerSectorMap[trade.ticker] || null;
    const oversightCommittees = getOversightCommittees(sector);

    // Look up politician committees (try exact name and variants)
    const baseName = trade.politician.replace(/\s*\(family\)\s*$/, '').trim();
    const politicianCommittees = committeeLookup[baseName] || [];

    // Check for committee overlap
    const overlap = politicianCommittees.filter(c => {
      const cLower = c.toLowerCase();
      return oversightCommittees.some(oc => {
        const ocLower = oc.toLowerCase();
        return cLower.includes(ocLower) || ocLower.includes(cLower) ||
               cLower.replace(/committee on /i, '').includes(ocLower.replace(/senate |house /i, '')) ||
               ocLower.replace(/senate |house /i, '').includes(cLower.replace(/committee on /i, ''));
      });
    });

    // Scoring:
    // 3 = trader sits on a committee directly overseeing the stock's sector
    // 2 = family-member trade
    // 1 = high-profile political figure, no direct overlap
    let exposureScore = 1;
    let committeeOverlap = false;
    if (overlap.length > 0) {
      exposureScore = 3;
      committeeOverlap = true;
    } else if (trade._is_family) {
      exposureScore = 2;
    }

    return {
      ticker: trade.ticker,
      politician: trade.politician,
      chamber: trade.chamber,
      party: trade.party,
      trade_date: trade.trade_date,
      transaction_type: trade.transaction_type,
      amount_range: trade.amount_range,
      committees: politicianCommittees.length > 0 ? politicianCommittees : undefined,
      sector: sector,
      committee_overlap: committeeOverlap,
      exposure_score: exposureScore,
      days_since_trade: trade.days_since_trade,
    };
  });
}

// --- Step 4: Build by_ticker summary ---
function buildByTicker(enrichedTrades) {
  const byTicker = {};

  for (const trade of enrichedTrades) {
    if (!byTicker[trade.ticker]) {
      byTicker[trade.ticker] = {
        recent_buyers: [],
        recent_sellers: [],
        exposure_score: 0,
        committee_overlap: [],
        last_trade_date: null,
        trade_count_90d: 0,
      };
    }

    const entry = byTicker[trade.ticker];
    entry.trade_count_90d++;

    // Track max exposure score
    if (trade.exposure_score > entry.exposure_score) {
      entry.exposure_score = trade.exposure_score;
    }

    // Track latest trade date
    if (!entry.last_trade_date || trade.trade_date > entry.last_trade_date) {
      entry.last_trade_date = trade.trade_date;
    }

    // Collect committee overlaps
    if (trade.committee_overlap && trade.committees) {
      for (const c of trade.committees) {
        if (!entry.committee_overlap.includes(c)) {
          entry.committee_overlap.push(c);
        }
      }
    }

    // Format politician string: "Name (Party-State, Committee)"
    const partyState = trade.party ? `${trade.party}` : '';
    const committeeNote = trade.committees && trade.committees.length > 0
      ? `, ${trade.committees[0]}`
      : '';
    const label = `${trade.politician} (${partyState}${committeeNote})`;

    const isPurchase = /purchase|buy/i.test(trade.transaction_type);
    const isSale = /sale|sell/i.test(trade.transaction_type);

    if (isPurchase) {
      if (!entry.recent_buyers.includes(label)) entry.recent_buyers.push(label);
    } else if (isSale) {
      if (!entry.recent_sellers.includes(label)) entry.recent_sellers.push(label);
    } else {
      // Default to buyers for unknown transaction types
      if (!entry.recent_buyers.includes(label)) entry.recent_buyers.push(label);
    }
  }

  return byTicker;
}

// --- Main ---
async function main() {
  console.log('=== SignalAI Political Intelligence Refresh ===');
  console.log(`Time: ${new Date().toISOString()}`);

  // Preserve prior political_data.json path for fallback
  const politicalPath = path.join(__dirname, 'political_data.json');
  let priorData = null;
  if (existsSync(politicalPath)) {
    try {
      priorData = JSON.parse(readFileSync(politicalPath, 'utf-8'));
    } catch (e) {
      console.warn('Could not read prior political_data.json:', e.message);
    }
  }

  let trades = [];
  let committeeLookup = {};
  let upstreamFailed = false;

  // Fetch trades
  try {
    trades = await fetchCongressionalTrades();
  } catch (e) {
    console.error('  Congressional trades fetch failed:', e.message);
    upstreamFailed = true;
  }

  if (trades.length === 0 && !upstreamFailed) {
    console.log('  No trades matched watchlist tickers in the last 90 days');
  }

  // Fetch committee membership
  try {
    committeeLookup = await fetchCommitteeMembership();
  } catch (e) {
    console.error('  Committee membership fetch failed:', e.message);
    // Not a fatal error; we can still score without committee data
  }

  // If trade fetch completely failed, preserve prior data
  if (upstreamFailed && priorData) {
    console.log('\n  Upstream failure detected; preserving prior political_data.json');
    console.log('  NOT updating data-snapshot.json');
    console.log('\n=== Political refresh complete (degraded) ===');
    return;
  }

  // Enrich and build output
  const enrichedTrades = enrichTrades(trades, committeeLookup);
  const byTicker = buildByTicker(enrichedTrades);

  const politicalData = {
    last_updated: new Date().toISOString(),
    trades: enrichedTrades,
    by_ticker: byTicker,
  };

  // Write political_data.json
  writeFileSync(politicalPath, JSON.stringify(politicalData, null, 2));
  const fileSize = Buffer.byteLength(JSON.stringify(politicalData, null, 2));
  console.log(`\nWrote political_data.json (${(fileSize / 1024).toFixed(1)} KB, ${enrichedTrades.length} trades, ${Object.keys(byTicker).length} tickers)`);

  // Merge political_exposure into data-snapshot.json
  if (existsSync(snapshotPath)) {
    try {
      const snapshot = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
      const quotes = snapshot.quotes || {};

      for (const [ticker, summary] of Object.entries(byTicker)) {
        if (quotes[ticker]) {
          quotes[ticker].political_exposure = {
            exposure_score: summary.exposure_score,
            committee_overlap: summary.committee_overlap,
            recent_buyers: summary.recent_buyers,
            recent_sellers: summary.recent_sellers,
            last_trade_date: summary.last_trade_date,
            trade_count_90d: summary.trade_count_90d,
          };
        }
      }

      snapshot.quotes = quotes;
      writeFileSync(snapshotPath, JSON.stringify(snapshot));
      console.log(`Updated data-snapshot.json with political_exposure for ${Object.keys(byTicker).length} tickers`);
    } catch (e) {
      console.warn('Could not update data-snapshot.json:', e.message);
    }
  }

  console.log('\n=== Political refresh complete ===');
}

main().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
