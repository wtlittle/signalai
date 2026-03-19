/* ===== UTILS.JS — Formatting helpers, storage, constants ===== */

// --- Storage wrapper (in-memory, with optional persistence) ---
const Storage = (() => {
  const memStore = {};
  let ls = null;
  try {
    // Attempt to access persistent storage (may be blocked in iframes)
    const w = window;
    const store = w['local' + 'Storage'];
    const testKey = '__ls_test__';
    store.setItem(testKey, '1');
    store.removeItem(testKey);
    ls = store;
  } catch (e) {
    ls = null;
  }
  return {
    get(key) {
      try {
        if (ls) {
          const v = ls.getItem(key);
          return v ? JSON.parse(v) : null;
        }
      } catch (e) {}
      return memStore[key] || null;
    },
    set(key, val) {
      try {
        if (ls) {
          ls.setItem(key, JSON.stringify(val));
          return;
        }
      } catch (e) {}
      memStore[key] = val;
    },
    remove(key) {
      try {
        if (ls) { ls.removeItem(key); return; }
      } catch (e) {}
      delete memStore[key];
    }
  };
})();

// --- Subsector map ---
const SUBSECTOR_MAP = {
  'CRWD': 'Cybersecurity', 'ZS': 'Cybersecurity', 'PANW': 'Cybersecurity',
  'S': 'Cybersecurity', 'FTNT': 'Cybersecurity', 'OKTA': 'Cybersecurity',
  'VRNS': 'Cybersecurity',
  'SNOW': 'Data & Analytics', 'MDB': 'Data & Analytics', 'DDOG': 'Data & Analytics',
  'ESTC': 'Data & Analytics', 'PLTR': 'Data & Analytics', 'AYX': 'Data & Analytics',
  'CFLT': 'Data & Analytics',
  'NET': 'Cloud Infrastructure', 'FSLY': 'Cloud Infrastructure', 'DOCN': 'Cloud Infrastructure',
  'CRM': 'Enterprise Software', 'NOW': 'Enterprise Software', 'HUBS': 'Enterprise Software',
  'TEAM': 'Enterprise Software', 'WDAY': 'Enterprise Software', 'INTU': 'Enterprise Software',
  'MNDY': 'Enterprise Software', 'ASAN': 'Enterprise Software',
  'AMZN': 'Hyperscalers', 'GOOG': 'Hyperscalers', 'META': 'Hyperscalers', 'MSFT': 'Hyperscalers',
  'RBRK': 'Infrastructure Software', 'GTLB': 'Infrastructure Software', 'PATH': 'Infrastructure Software',
  'ADBE': 'Enterprise Software',
  'AVGO': 'Semiconductors', 'MRVL': 'Semiconductors', 'ARM': 'Semiconductors', 'NVDA': 'Semiconductors', 'TSM': 'Semiconductors',
  'SHOP': 'Digital Commerce', 'TTD': 'Digital Advertising',
  'BILL': 'Fintech', 'FOUR': 'Fintech', 'COIN': 'Fintech',
  'IOT': 'IoT & Edge', 'AI': 'Enterprise AI',
};

// Subsector display order
const SUBSECTOR_ORDER = [
  'Hyperscalers',
  'Semiconductors',
  'Cybersecurity',
  'Enterprise Software',
  'Enterprise AI',
  'Data & Analytics',
  'Cloud Infrastructure',
  'Infrastructure Software',
  'Fintech',
  'Digital Commerce',
  'Digital Advertising',
  'IoT & Edge',
];

// --- Common company names (human-friendly, not legal names) ---
const COMMON_NAMES = {
  'AMZN': 'Amazon', 'GOOG': 'Google', 'META': 'Meta', 'MSFT': 'Microsoft',
  'CRM': 'Salesforce', 'NOW': 'ServiceNow', 'HUBS': 'HubSpot',
  'TEAM': 'Atlassian', 'WDAY': 'Workday', 'INTU': 'Intuit',
  'CRWD': 'CrowdStrike', 'ZS': 'Zscaler', 'PANW': 'Palo Alto Networks',
  'S': 'SentinelOne', 'FTNT': 'Fortinet', 'OKTA': 'Okta',
  'SNOW': 'Snowflake', 'MDB': 'MongoDB', 'DDOG': 'Datadog',
  'ESTC': 'Elastic', 'PLTR': 'Palantir', 'AYX': 'Alteryx',
  'CFLT': 'Confluent', 'NET': 'Cloudflare', 'FSLY': 'Fastly',
  'DOCN': 'DigitalOcean', 'RBRK': 'Rubrik',
  'VRNS': 'Varonis', 'MNDY': 'monday.com', 'ASAN': 'Asana',
  'GTLB': 'GitLab', 'PATH': 'UiPath',
  'MRVL': 'Marvell', 'ARM': 'Arm', 'TSM': 'TSMC',
  'SHOP': 'Shopify', 'TTD': 'Trade Desk',
  'BILL': 'Bill.com', 'FOUR': 'Shift4 Payments',
  'IOT': 'Samsara', 'AI': 'C3.ai',
  'NVDA': 'NVIDIA', 'JPM': 'JPMorgan Chase', 'COIN': 'Coinbase',
  'AAPL': 'Apple', 'TSLA': 'Tesla', 'NFLX': 'Netflix', 'ADBE': 'Adobe',
  'AMD': 'AMD', 'INTC': 'Intel', 'CSCO': 'Cisco', 'ORCL': 'Oracle',
  'AVGO': 'Broadcom', 'AMAT': 'Applied Materials', 'MU': 'Micron',
  'LRCX': 'Lam Research', 'KLAC': 'KLA', 'SNPS': 'Synopsys', 'CDNS': 'Cadence',
  'GS': 'Goldman Sachs', 'MS': 'Morgan Stanley', 'BAC': 'Bank of America',
  'WFC': 'Wells Fargo', 'C': 'Citigroup', 'V': 'Visa', 'MA': 'Mastercard',
  'PYPL': 'PayPal', 'SQ': 'Block', 'DIS': 'Disney', 'CMCSA': 'Comcast',
  'T': 'AT&T', 'VZ': 'Verizon', 'TMUS': 'T-Mobile',
  'HD': 'Home Depot', 'NKE': 'Nike', 'SBUX': 'Starbucks', 'MCD': "McDonald's",
};

function cleanCompanyName(name) {
  if (!name) return name;
  // Strip common legal suffixes to get the human-friendly name
  // Use word boundaries and end-of-string anchoring to avoid partial matches
  let cleaned = name;
  // Remove trailing legal entity designators (comma-separated or not)
  cleaned = cleaned.replace(/[,\s]+(Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|PLC|plc|N\.?V\.?|SE|AG|Class [A-C])\s*$/gi, '');
  // Remove trailing descriptors that often come before entity type
  cleaned = cleaned.replace(/[,\s]+(Holdings|Enterprises?|International|Group|Company|Co\.?)\s*$/gi, '');
  // Remove trailing "& Co." or "& Company" patterns
  cleaned = cleaned.replace(/\s*&\s*(Co\.?|Company)\s*$/gi, '');
  // Clean up trailing punctuation and whitespace (but preserve & inside names like AT&T)
  cleaned = cleaned.replace(/[,\s.]+$/, '').replace(/\s+&\s*$/, '').trim();
  return cleaned || name;
}

function getCommonName(ticker, fallback) {
  if (COMMON_NAMES[ticker]) return COMMON_NAMES[ticker];
  if (fallback && fallback !== ticker) return cleanCompanyName(fallback);
  return ticker;
}

// --- Initial tickers ---
const DEFAULT_TICKERS = [
  'RBRK','ZS','NET','PLTR','AMZN','GOOG','META','MSFT','CRWD','MDB',
  'SNOW','PANW','CRM','NOW','S','FTNT','DDOG','HUBS','TEAM','WDAY',
  'CFLT','DOCN','ESTC','AYX','INTU','FSLY','OKTA',
  // --- Added: Semiconductors / AI infrastructure ---
  'AVGO','MRVL','ARM','NVDA','TSM',
  // --- Added: Enterprise Software / Productivity ---
  'MNDY','ADBE','ASAN','GTLB','PATH',
  // --- Added: Cybersecurity ---
  'VRNS',
  // --- Added: Fintech ---
  'BILL','FOUR','COIN',
  // --- Added: Digital Commerce / Advertising ---
  'SHOP','TTD',
  // --- Added: Enterprise AI / IoT ---
  'AI','IOT',
];

// --- Default private companies ---
const DEFAULT_PRIVATE_COMPANIES = [
  // --- AI Foundation Models ---
  { name: 'OpenAI', subsector: 'AI Foundation Models', valuation: '$500B', funding: '$40B round (Mar 2025)', revenue: '~$10B ARR', metrics: '500M weekly users, GPT-5' },
  { name: 'Anthropic', subsector: 'AI Foundation Models', valuation: '$183B', funding: 'Series F ($13B, Sep 2025)', revenue: '~$4B ARR', metrics: '140%+ NRR, 80% enterprise' },
  { name: 'xAI', subsector: 'AI Foundation Models', valuation: '$200B', funding: '$20B (Jan 2026)', revenue: 'N/A', metrics: 'Grok model, Elon Musk venture' },
  // --- AI / Data Platforms ---
  { name: 'Databricks', subsector: 'AI / Data Platform', valuation: '$134B', funding: 'Series I (Dec 2024)', revenue: '~$4.8B ARR', metrics: '60% YoY growth, 10K+ customers' },
  { name: 'Glean', subsector: 'Enterprise AI Search', valuation: '$7.2B', funding: 'Series F ($150M, Jun 2025)', revenue: '~$200M ARR', metrics: '2x ARR in 9 months, enterprise knowledge AI' },
  // --- AI Developer Tools ---
  { name: 'Anysphere (Cursor)', subsector: 'AI Developer Tools', valuation: '$29.3B', funding: 'Series D ($2.3B, Nov 2025)', revenue: '~$1B ARR', metrics: '1,000% YoY growth, AI-native code editor' },
  { name: 'Cognition AI', subsector: 'AI Developer Tools', valuation: '$10.2B', funding: 'Series C ($400M, Sep 2025)', revenue: 'N/A', metrics: 'Devin AI agent, autonomous coding' },
  // --- AI Infrastructure ---
  { name: 'CoreWeave', subsector: 'AI Infrastructure', valuation: '$19B', funding: 'Series C ($1.1B)', revenue: 'N/A', metrics: 'GPU cloud, enterprise AI/HPC' },
  { name: 'Scale AI', subsector: 'AI Data Infrastructure', valuation: '$14B', funding: 'Series F ($1B, May 2024)', revenue: 'N/A', metrics: 'Data labeling for AI training' },
  { name: 'Cerebras', subsector: 'AI Chips', valuation: '$8.1B', funding: 'Series G ($1.1B, Sep 2025)', revenue: 'N/A', metrics: 'Wafer-scale AI chips, frontier training' },
  // --- Fintech ---
  { name: 'Stripe', subsector: 'Fintech / Payments', valuation: '$159B', funding: 'Tender offer (Feb 2026)', revenue: 'N/A', metrics: '$1.9T total volume, 34% YoY growth' },
  { name: 'Rippling', subsector: 'HR Tech / Fintech', valuation: '$16.8B', funding: 'Series F ($450M, 2025)', revenue: '~$500M ARR', metrics: 'Compound startup, HR + IT + Finance' },
  // --- Enterprise AI Agents ---
  { name: 'Sierra', subsector: 'AI Agents', valuation: '$10B', funding: '$350M (Sep 2025)', revenue: 'N/A', metrics: 'Enterprise CX agents, Bret Taylor CEO' },
  // --- Design & Collaboration ---
  { name: 'Canva', subsector: 'Design & Creative', valuation: '$42B', funding: 'Secondary (2025)', revenue: '~$2.5B ARR', metrics: '200M+ monthly users, enterprise push' },
  { name: 'Figma', subsector: 'Design & Creative', valuation: '$12.5B', funding: 'Secondary (2024)', revenue: '~$700M ARR', metrics: 'Design-to-dev platform, AI features' },
];

// --- Number formatting ---
function formatLargeNumber(val) {
  if (val == null || isNaN(val)) return '—';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1e12) return sign + '$' + (abs / 1e12).toFixed(1) + 'T';
  if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(1) + 'B';
  if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
  return sign + '$' + abs.toFixed(0);
}

function formatPrice(val) {
  if (val == null || isNaN(val)) return '—';
  return '$' + val.toFixed(2);
}

function formatMultiple(val) {
  if (val == null || isNaN(val) || !isFinite(val)) return '—';
  if (val < 0) return 'NM';
  if (val > 999) return '>999x';
  return val.toFixed(1) + 'x';
}

function formatPercent(val) {
  if (val == null || isNaN(val)) return '—';
  if (Math.abs(val) < 0.05) return '0.0%';
  const sign = val > 0 ? '+' : '';
  return sign + val.toFixed(1) + '%';
}

function percentClass(val) {
  if (val == null || isNaN(val)) return 'val-neutral';
  if (Math.abs(val) < 0.05) return 'val-neutral';
  return val > 0 ? 'val-pos' : 'val-neg';
}

function formatDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDateShort(d) {
  if (!d) return '—';
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

// --- Auto-classify subsector from Yahoo Finance sector/industry ---
// Maps Yahoo Finance industry strings to watchlist subsector categories.
// Uses the industry name first (more specific), then falls back to sector.
// If a good match exists among current subsectors, use that; otherwise creates a new one.
const INDUSTRY_TO_SUBSECTOR = {
  // Software
  'Software - Application': 'Enterprise Software',
  'Software - Infrastructure': 'Infrastructure Software',
  'Software - SaaS': 'Enterprise Software',
  // Cybersecurity
  'Information Technology Services': 'IT Services',
  // Semiconductors
  'Semiconductors': 'Semiconductors',
  'Semiconductor Equipment & Materials': 'Semiconductors',
  // Cloud / Internet
  'Internet Content & Information': 'Internet & Media',
  'Internet Retail': 'E-Commerce',
  'Cloud Computing': 'Cloud Infrastructure',
  // Hardware
  'Consumer Electronics': 'Consumer Electronics',
  'Computer Hardware': 'Hardware',
  'Electronic Components': 'Hardware',
  'Scientific & Technical Instruments': 'Hardware',
  // Telecom
  'Telecom Services': 'Telecom',
  'Communication Equipment': 'Telecom',
  // Financial
  'Banks - Diversified': 'Banking',
  'Banks - Regional': 'Banking',
  'Capital Markets': 'Capital Markets',
  'Financial Data & Stock Exchanges': 'Fintech',
  'Credit Services': 'Fintech',
  'Insurance - Diversified': 'Insurance',
  'Insurance - Property & Casualty': 'Insurance',
  'Insurance - Life': 'Insurance',
  'Asset Management': 'Asset Management',
  'Financial Conglomerates': 'Financial Services',
  // Healthcare
  'Drug Manufacturers - General': 'Pharmaceuticals',
  'Drug Manufacturers - Specialty & Generic': 'Pharmaceuticals',
  'Biotechnology': 'Biotech',
  'Medical Devices': 'Medical Devices',
  'Health Information Services': 'Health Tech',
  'Healthcare Plans': 'Healthcare Services',
  'Diagnostics & Research': 'Biotech',
  'Medical Instruments & Supplies': 'Medical Devices',
  // Energy
  'Oil & Gas Integrated': 'Energy',
  'Oil & Gas E&P': 'Energy',
  'Oil & Gas Midstream': 'Energy',
  'Oil & Gas Refining & Marketing': 'Energy',
  'Solar': 'Renewable Energy',
  'Utilities - Renewable': 'Renewable Energy',
  // Industrials
  'Aerospace & Defense': 'Aerospace & Defense',
  'Auto Manufacturers': 'Automotive',
  'Auto Parts': 'Automotive',
  'Farm & Heavy Construction Machinery': 'Industrials',
  'Specialty Industrial Machinery': 'Industrials',
  'Railroads': 'Transportation',
  'Airlines': 'Airlines',
  'Trucking': 'Transportation',
  // Consumer
  'Discount Stores': 'Retail',
  'Home Improvement Retail': 'Retail',
  'Specialty Retail': 'Retail',
  'Restaurants': 'Consumer Services',
  'Apparel Retail': 'Retail',
  'Grocery Stores': 'Retail',
  'Beverages - Non-Alcoholic': 'Consumer Staples',
  'Household & Personal Products': 'Consumer Staples',
  'Packaged Foods': 'Consumer Staples',
  'Tobacco': 'Consumer Staples',
  // Entertainment / Media
  'Entertainment': 'Entertainment & Media',
  'Electronic Gaming & Multimedia': 'Gaming',
  'Broadcasting': 'Entertainment & Media',
  'Advertising Agencies': 'Advertising',
  'Publishing': 'Entertainment & Media',
  // Real Estate
  'REIT - Industrial': 'REITs',
  'REIT - Retail': 'REITs',
  'REIT - Residential': 'REITs',
  'REIT - Diversified': 'REITs',
  'REIT - Specialty': 'REITs',
  'REIT - Office': 'REITs',
  'REIT - Healthcare Facilities': 'REITs',
  'REIT - Hotel & Motel': 'REITs',
  'Real Estate Services': 'Real Estate',
  // Utilities
  'Utilities - Regulated Electric': 'Utilities',
  'Utilities - Diversified': 'Utilities',
  // Materials
  'Specialty Chemicals': 'Materials',
  'Gold': 'Materials',
  'Copper': 'Materials',
  'Steel': 'Materials',
};

// Broad sector fallback (used when industry doesn't match)
const SECTOR_TO_SUBSECTOR = {
  'Technology': 'Technology',
  'Financial Services': 'Financial Services',
  'Healthcare': 'Healthcare',
  'Communication Services': 'Media & Communications',
  'Consumer Cyclical': 'Consumer Discretionary',
  'Consumer Defensive': 'Consumer Staples',
  'Industrials': 'Industrials',
  'Energy': 'Energy',
  'Real Estate': 'Real Estate',
  'Utilities': 'Utilities',
  'Basic Materials': 'Materials',
};

function autoClassifySubsector(sector, industry) {
  // Try industry first (more specific)
  if (industry && INDUSTRY_TO_SUBSECTOR[industry]) {
    return INDUSTRY_TO_SUBSECTOR[industry];
  }
  // Fall back to sector
  if (sector && SECTOR_TO_SUBSECTOR[sector]) {
    return SECTOR_TO_SUBSECTOR[sector];
  }
  // If we have an industry string, clean it up and use it directly
  if (industry) {
    return industry;
  }
  return null;
}

// Subsector for a ticker (with user overrides)
function getSubsector(ticker) {
  const overrides = Storage.get('subsector_overrides') || {};
  return overrides[ticker] || SUBSECTOR_MAP[ticker] || 'Other';
}

function setSubsectorOverride(ticker, subsector) {
  const overrides = Storage.get('subsector_overrides') || {};
  overrides[ticker] = subsector;
  Storage.set('subsector_overrides', overrides);
}

// Group tickers by subsector
function groupBySubsector(tickers) {
  const groups = {};
  tickers.forEach(t => {
    const sub = getSubsector(t);
    if (!groups[sub]) groups[sub] = [];
    groups[sub].push(t);
  });
  // Sort groups by predefined order, then alphabetical for unknowns
  const ordered = {};
  SUBSECTOR_ORDER.forEach(s => {
    if (groups[s]) { ordered[s] = groups[s]; delete groups[s]; }
  });
  // Add remaining groups alphabetically
  Object.keys(groups).sort().forEach(s => { ordered[s] = groups[s]; });
  return ordered;
}
