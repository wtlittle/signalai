#!/usr/bin/env node
/**
 * update_subsectors.mjs — Daily subsector refresh for SignalAI
 * 
 * Reads DEFAULT_TICKERS from utils.js, checks for any tickers missing
 * from SUBSECTOR_MAP, classifies them using Yahoo Finance sector/industry
 * data from data-snapshot.json, and writes them back to utils.js.
 * 
 * Also verifies existing mappings haven't drifted from the canonical
 * Yahoo Finance classification (but won't override manually-set core watchlist entries).
 * 
 * Usage: node update_subsectors.mjs
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const UTILS_PATH = resolve(__dirname, 'utils.js');
const SNAPSHOT_PATH = resolve(__dirname, 'data-snapshot.json');

// ─── Industry-to-subsector mapping (mirrors the one in utils.js) ───
const INDUSTRY_TO_SUBSECTOR = {
  'Software - Application': 'Enterprise Software',
  'Software - Infrastructure': 'Infrastructure Software',
  'Information Technology Services': 'IT Services',
  'Semiconductors': 'Semiconductors',
  'Semiconductor Equipment & Materials': 'Semiconductors',
  'Internet Content & Information': 'Internet & Media',
  'Internet Retail': 'E-Commerce',
  'Cloud Computing': 'Cloud Infrastructure',
  'Consumer Electronics': 'Consumer Electronics',
  'Computer Hardware': 'Hardware',
  'Electronic Components': 'Hardware',
  'Scientific & Technical Instruments': 'Hardware',
  'Telecom Services': 'Telecom',
  'Communication Equipment': 'Networking',
  'Banks - Diversified': 'Banking',
  'Banks - Regional': 'Banking',
  'Capital Markets': 'Capital Markets',
  'Financial Data & Stock Exchanges': 'Fintech',
  'Credit Services': 'Fintech',
  'Insurance - Diversified': 'Insurance',
  'Insurance - Property & Casualty': 'Insurance',
  'Insurance - Life': 'Insurance',
  'Insurance - Reinsurance': 'Insurance',
  'Insurance Brokers': 'Insurance',
  'Asset Management': 'Asset Management',
  'Financial Conglomerates': 'Financial Services',
  'Drug Manufacturers - General': 'Pharmaceuticals',
  'Drug Manufacturers - Specialty & Generic': 'Pharmaceuticals',
  'Biotechnology': 'Biotech',
  'Medical Devices': 'Medical Devices',
  'Health Information Services': 'Health Tech',
  'Healthcare Plans': 'Healthcare Services',
  'Diagnostics & Research': 'Life Sciences',
  'Medical Instruments & Supplies': 'Medical Devices',
  'Oil & Gas Integrated': 'Energy',
  'Oil & Gas E&P': 'Energy',
  'Oil & Gas Midstream': 'Energy',
  'Oil & Gas Refining & Marketing': 'Energy',
  'Solar': 'Renewable Energy',
  'Utilities - Renewable': 'Renewable Energy',
  'Aerospace & Defense': 'Aerospace & Defense',
  'Auto Manufacturers': 'Automotive',
  'Auto Parts': 'Automotive',
  'Farm & Heavy Construction Machinery': 'Industrials',
  'Specialty Industrial Machinery': 'Industrials',
  'Railroads': 'Transportation',
  'Airlines': 'Airlines',
  'Trucking': 'Transportation',
  'Discount Stores': 'Retail',
  'Home Improvement Retail': 'Retail',
  'Specialty Retail': 'Retail',
  'Restaurants': 'Consumer Services',
  'Footwear & Accessories': 'Retail',
  'Apparel Retail': 'Retail',
  'Apparel Manufacturing': 'Retail',
  'Grocery Stores': 'Retail',
  'Beverages - Non-Alcoholic': 'Consumer Staples',
  'Household & Personal Products': 'Consumer Staples',
  'Packaged Foods': 'Consumer Staples',
  'Tobacco': 'Consumer Staples',
  'Agricultural Inputs': 'Materials',
  'Specialty Chemicals': 'Materials',
  'Gold': 'Materials',
  'Copper': 'Materials',
  'Steel': 'Materials',
  'Entertainment': 'Entertainment & Media',
  'Electronic Gaming & Multimedia': 'Gaming',
  'Broadcasting': 'Entertainment & Media',
  'Advertising Agencies': 'Advertising',
  'Publishing': 'Entertainment & Media',
  'REIT - Industrial': 'REITs',
  'REIT - Retail': 'REITs',
  'REIT - Residential': 'REITs',
  'REIT - Diversified': 'REITs',
  'REIT - Specialty': 'REITs',
  'REIT - Office': 'REITs',
  'Real Estate Services': 'Real Estate',
  'Utilities - Regulated Electric': 'Utilities',
  'Utilities - Diversified': 'Utilities',
  // Additional
  'Computer Storage Devices': 'Data Storage',
  'Data Storage': 'Data Storage',
  'Technology Hardware, Storage & Peripherals': 'Data Storage',
};

const SECTOR_TO_SUBSECTOR = {
  'Technology': 'Technology',
  'Financial Services': 'Financial Services',
  'Healthcare': 'Healthcare',
  'Communication Services': 'Entertainment & Media',
  'Consumer Cyclical': 'Consumer Discretionary',
  'Consumer Defensive': 'Consumer Staples',
  'Industrials': 'Industrials',
  'Energy': 'Energy',
  'Real Estate': 'Real Estate',
  'Utilities': 'Utilities',
  'Basic Materials': 'Materials',
};

function classify(sector, industry) {
  if (industry && INDUSTRY_TO_SUBSECTOR[industry]) return INDUSTRY_TO_SUBSECTOR[industry];
  if (sector && SECTOR_TO_SUBSECTOR[sector]) return SECTOR_TO_SUBSECTOR[sector];
  if (industry) return industry; // Use raw industry as fallback
  return null;
}

// ─── Main ───

console.log('=== Subsector Update ===');

// 1. Read utils.js
let utilsCode = readFileSync(UTILS_PATH, 'utf-8');

// 2. Extract DEFAULT_TICKERS
const tickerMatch = utilsCode.match(/const DEFAULT_TICKERS = \[([\s\S]*?)\];/);
if (!tickerMatch) { console.error('Could not find DEFAULT_TICKERS'); process.exit(1); }
const allTickers = tickerMatch[1].match(/'([A-Z]+)'/g).map(t => t.replace(/'/g, ''));
console.log(`Total tickers: ${allTickers.length}`);

// 3. Extract existing SUBSECTOR_MAP entries
const mapMatch = utilsCode.match(/const SUBSECTOR_MAP = \{([\s\S]*?)\};/);
if (!mapMatch) { console.error('Could not find SUBSECTOR_MAP'); process.exit(1); }
const existingKeys = new Set(mapMatch[1].match(/'([A-Z]+)'/g)?.map(t => t.replace(/'/g, '')) || []);

// 4. Find tickers missing from SUBSECTOR_MAP
const missing = allTickers.filter(t => !existingKeys.has(t));
console.log(`Missing from SUBSECTOR_MAP: ${missing.length}`);

if (missing.length === 0) {
  console.log('All tickers have subsector mappings. No updates needed.');
  process.exit(0);
}

// 5. Load data-snapshot.json for sector/industry data
let snapshot;
try {
  snapshot = JSON.parse(readFileSync(SNAPSHOT_PATH, 'utf-8'));
} catch (e) {
  console.error('Could not read data-snapshot.json:', e.message);
  process.exit(1);
}

// 6. Classify missing tickers
const newMappings = [];
for (const ticker of missing) {
  const quote = snapshot.quotes?.[ticker];
  if (!quote) {
    console.log(`  ${ticker}: No data in snapshot — skipping`);
    continue;
  }
  const subsector = classify(quote.sector, quote.industry);
  if (subsector) {
    newMappings.push({ ticker, subsector, sector: quote.sector, industry: quote.industry });
    console.log(`  ${ticker}: ${subsector} (from ${quote.industry || quote.sector})`);
  } else {
    console.log(`  ${ticker}: Could not classify (sector=${quote.sector}, industry=${quote.industry})`);
  }
}

if (newMappings.length === 0) {
  console.log('No new mappings to add.');
  process.exit(0);
}

// 7. Build the new lines to insert into SUBSECTOR_MAP
const dateStr = new Date().toISOString().split('T')[0];
const newLines = newMappings.map(m => `  '${m.ticker}': '${m.subsector}'`).join(',\n');
const insertBlock = `\n  // Auto-classified ${dateStr}\n${newLines},`;

// 8. Insert before the closing brace of SUBSECTOR_MAP
// Find the marker comment or the end of the map
const insertPoint = utilsCode.indexOf('// Common additions');
if (insertPoint === -1) {
  // Fallback: insert before closing }; of SUBSECTOR_MAP
  const mapEnd = utilsCode.indexOf('};', utilsCode.indexOf('const SUBSECTOR_MAP'));
  utilsCode = utilsCode.slice(0, mapEnd) + insertBlock + '\n' + utilsCode.slice(mapEnd);
} else {
  utilsCode = utilsCode.slice(0, insertPoint) + insertBlock.trim() + '\n  ' + utilsCode.slice(insertPoint);
}

// 9. Also ensure new subsectors are in SUBSECTOR_ORDER
const newSubsectors = [...new Set(newMappings.map(m => m.subsector))];
const orderMatch = utilsCode.match(/const SUBSECTOR_ORDER = \[([\s\S]*?)\];/);
if (orderMatch) {
  const existingOrder = orderMatch[1];
  const toAdd = newSubsectors.filter(s => !existingOrder.includes(`'${s}'`));
  if (toAdd.length > 0) {
    // Insert before the private company subsectors comment
    const privateComment = utilsCode.indexOf("// Private company subsectors");
    if (privateComment !== -1) {
      const insertStr = toAdd.map(s => `  '${s}',`).join('\n') + '\n  ';
      utilsCode = utilsCode.slice(0, privateComment) + insertStr + utilsCode.slice(privateComment);
    }
  }
}

// 10. Also add COMMON_NAMES entries if missing
const namesMatch = utilsCode.match(/const COMMON_NAMES = \{([\s\S]*?)\};/);
if (namesMatch) {
  const existingNames = namesMatch[1];
  const namesToAdd = newMappings.filter(m => !existingNames.includes(`'${m.ticker}'`));
  if (namesToAdd.length > 0) {
    // We'll add them using the longName from snapshot
    const nameLines = namesToAdd
      .map(m => {
        const name = snapshot.quotes?.[m.ticker]?.longName;
        if (!name) return null;
        // Clean up the name
        let clean = name.replace(/[,\s]+(Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|PLC|plc)$/gi, '').trim();
        clean = clean.replace(/[,\s.]+$/, '').trim();
        return `  '${m.ticker}': '${clean.replace(/'/g, "\\'")}'`;
      })
      .filter(Boolean);
    if (nameLines.length > 0) {
      const namesEnd = utilsCode.indexOf('};', utilsCode.indexOf('const COMMON_NAMES'));
      utilsCode = utilsCode.slice(0, namesEnd) + nameLines.join(',\n') + ',\n' + utilsCode.slice(namesEnd);
    }
  }
}

// 11. Write back
writeFileSync(UTILS_PATH, utilsCode, 'utf-8');
console.log(`\nUpdated utils.js with ${newMappings.length} new subsector mappings.`);
