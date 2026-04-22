/* ===== UNIVERSE-V1.JS =====
 * Curated default institutional coverage universe (~110 names).
 *
 * Design principles:
 *  - Broad institutional sector span with real comp-set depth in each bucket
 *  - Merges fragmented subsectors (e.g. Industrial Software + Vertical Software + Cloud Infra)
 *  - Preserves tech / tech-adjacent depth (the product's original strength)
 *  - Preserves enough non-tech breadth to feel institutional and cross-sector
 *  - Removes singleton subsectors from the default view (they remain accessible
 *    via screener / custom baskets / the full historical universe)
 *
 * This is the DEFAULT coverage. Users can always add more names via the screener,
 * custom baskets, or the full ticker library (see DEFAULT_TICKERS in utils.js).
 */
(function (global) {
  'use strict';

  // ============================================================
  // SECTOR TAXONOMY — 15 institutional sectors
  // ============================================================
  //
  // Each sector must have enough names to run real comp work (min 4 in tech,
  // min 3 in non-tech where coverage is intentionally narrower).

  const UNIVERSE_V1 = {
    'Hyperscalers': [
      'MSFT', 'GOOG', 'AMZN', 'META',
    ],
    'Semiconductors': [
      // Design / leaders / foundry
      'NVDA', 'AVGO', 'AMD', 'TSM', 'ARM', 'MRVL', 'INTC',
      // EDA + memory + test
      'SNPS', 'CDNS', 'MU', 'TER',
    ],
    'Cybersecurity': [
      // Platform leaders
      'CRWD', 'PANW', 'ZS', 'FTNT',
      // Identity + mid-cap
      'OKTA', 'S', 'RBRK',
      // Legacy / data security
      'VRNS', 'QLYS',
    ],
    'Enterprise / Workflow Software': [
      // SaaS mega-caps
      'CRM', 'ORCL', 'ADBE', 'NOW', 'WDAY', 'INTU',
      // Collaboration / workflow
      'TEAM', 'HUBS', 'MNDY',
      // Vertical enterprise
      'VEEV',
    ],
    'Data & Analytics': [
      // Data platforms
      'SNOW', 'MDB', 'PLTR',
      // Observability
      'DDOG', 'DT',
      // Data infra / streaming
      'CFLT', 'ESTC',
      // Scoring / applied
      'FICO',
    ],
    'Cloud / Infrastructure Software': [
      // Networking / edge
      'NET', 'ANET', 'AKAM',
      // Hybrid / storage
      'NTNX', 'PSTG',
      // Data center infra (AI power/cooling)
      'VRT',
    ],
    'Industrial / Vertical Software': [
      // Design / simulation
      'ADSK', 'PTC', 'ANSS',
      // Vertical SaaS
      'TYL', 'GWRE', 'TRMB',
      // Diversified software holdings
      'ROP',
    ],
    'Fintech / Payments': [
      // Networks / processors
      'V', 'MA', 'PYPL',
      // Next-gen payments
      'XYZ', 'FOUR', 'AFRM',
      // Crypto exposure
      'COIN',
    ],
    'Commerce / Advertising / Marketing Platforms': [
      // Commerce
      'SHOP', 'SE', 'MELI',
      // Ad-tech
      'TTD', 'APP', 'PINS',
    ],
    'Consumer Staples': [
      // Beverages / food
      'KO', 'PEP', 'MDLZ', 'MKC', 'KHC',
      // Household / tobacco
      'PG', 'CL', 'PM',
    ],
    'Financials': [
      // Money-centers
      'JPM', 'BAC', 'WFC', 'C',
      // Investment banks
      'GS', 'MS',
      // Card issuers / specialty
      'COF', 'AXP',
      // Insurance
      'BRK.B',
    ],
    'Energy': [
      // Super-majors
      'XOM', 'CVX',
      // E&P
      'OXY', 'DVN', 'FANG', 'EOG',
      // Refiners / services
      'VLO', 'BKR', 'SLB',
    ],
    'Healthcare': [
      // Managed care
      'UNH', 'ELV',
      // Pharma
      'LLY', 'JNJ', 'PFE', 'MRK', 'NVO', 'VRTX',
      // Life sciences / devices
      'TMO', 'DHR', 'ABT', 'ISRG',
    ],
    'Industrials / Aerospace & Defense': [
      // A&D primes
      'RTX', 'LMT', 'NOC', 'BA', 'GE',
      // Diversified industrial
      'CAT', 'HON', 'DE',
      // Transports
      'UNP',
    ],
    'Materials': [
      // Industrial metals
      'FCX', 'NUE',
      // Diversified chemicals
      'LIN', 'APD',
      // Gold / specialty
      'NEM',
    ],
  };

  // ============================================================
  // FLATTEN + EXPORT
  // ============================================================

  function flatten() {
    const out = [];
    for (const sector in UNIVERSE_V1) {
      for (const t of UNIVERSE_V1[sector]) out.push(t);
    }
    return out;
  }

  function buildSectorMap() {
    const out = {};
    for (const sector in UNIVERSE_V1) {
      for (const t of UNIVERSE_V1[sector]) out[t] = sector;
    }
    return out;
  }

  const UNIVERSE_V1_TICKERS = flatten();
  const UNIVERSE_V1_SECTOR_MAP = buildSectorMap();

  // Useful metadata for the ribbon's universe selector
  const UNIVERSE_V1_META = {
    id: 'default_v1',
    name: 'Default Coverage',
    description: 'Curated institutional coverage (~' + UNIVERSE_V1_TICKERS.length + ' names across ' + Object.keys(UNIVERSE_V1).length + ' sectors).',
    size: UNIVERSE_V1_TICKERS.length,
    sectors: Object.keys(UNIVERSE_V1),
  };

  global.UNIVERSE_V1 = UNIVERSE_V1;
  global.UNIVERSE_V1_TICKERS = UNIVERSE_V1_TICKERS;
  global.UNIVERSE_V1_SECTOR_MAP = UNIVERSE_V1_SECTOR_MAP;
  global.UNIVERSE_V1_META = UNIVERSE_V1_META;
})(typeof window !== 'undefined' ? window : global);
