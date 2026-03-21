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
  'VRNS': 'Cybersecurity', 'RBRK': 'Cybersecurity', 'TENB': 'Cybersecurity',
  'SNOW': 'Data & Analytics', 'MDB': 'Data & Analytics', 'DDOG': 'Data & Analytics',
  'ESTC': 'Data & Analytics', 'PLTR': 'Data & Analytics',
  'CFLT': 'Data & Analytics', 'DT': 'Data & Analytics',
  'NET': 'Cloud Infrastructure', 'FSLY': 'Cloud Infrastructure', 'DOCN': 'Cloud Infrastructure',
  'ANET': 'Cloud Infrastructure',
  'CRM': 'Enterprise Software', 'NOW': 'Enterprise Software', 'HUBS': 'Enterprise Software',
  'TEAM': 'Enterprise Software', 'WDAY': 'Enterprise Software', 'INTU': 'Enterprise Software',
  'MNDY': 'Enterprise Software', 'ASAN': 'Enterprise Software',
  'AMZN': 'Hyperscalers', 'GOOG': 'Hyperscalers', 'META': 'Hyperscalers', 'MSFT': 'Hyperscalers',
  'GTLB': 'DevOps & Automation', 'PATH': 'DevOps & Automation',
  'ADBE': 'Enterprise Software',
  'AVGO': 'Semiconductors', 'MRVL': 'Semiconductors', 'ARM': 'Semiconductors', 'NVDA': 'Semiconductors', 'TSM': 'Semiconductors',
  'AMD': 'Semiconductors', 'SNPS': 'Semiconductors', 'CDNS': 'Semiconductors',
  'SHOP': 'Digital Commerce', 'SE': 'Digital Commerce',
  'TTD': 'Digital Advertising', 'PINS': 'Digital Advertising',
  'BILL': 'Fintech', 'FOUR': 'Fintech', 'COIN': 'Fintech',
  'XYZ': 'Fintech', 'AFRM': 'Fintech',
  'IOT': 'Applied AI', 'AI': 'Applied AI',
  // Common additions (auto-classified since CORS proxy can't provide sector/industry)
  'SE': 'Digital Commerce', 'BABA': 'E-Commerce',
  'AAPL': 'Consumer Electronics', 'GOOGL': 'Hyperscalers',
  'NFLX': 'Entertainment & Media', 'DIS': 'Entertainment & Media',
  'UBER': 'Digital Commerce', 'ABNB': 'Digital Commerce',
  'XYZ': 'Fintech', 'PYPL': 'Fintech', 'V': 'Fintech', 'MA': 'Fintech',
  'AMD': 'Semiconductors', 'INTC': 'Semiconductors', 'QCOM': 'Semiconductors',
  'TSLA': 'Automotive', 'RIVN': 'Automotive',
  'SNAP': 'Digital Advertising', 'PINS': 'Digital Advertising',
  'SPOT': 'Entertainment & Media', 'ROKU': 'Entertainment & Media',
  'TWLO': 'Enterprise Software', 'ZM': 'Enterprise Software', 'DBX': 'Enterprise Software',
  'U': 'Gaming', 'RBLX': 'Gaming',
  'SOFI': 'Fintech', 'HOOD': 'Fintech', 'AFRM': 'Fintech',
  'DKNG': 'Gaming', 'DASH': 'Digital Commerce',
  'PDD': 'E-Commerce', 'JD': 'E-Commerce', 'MELI': 'E-Commerce',
};

// Subsector display order
const SUBSECTOR_ORDER = [
  'Hyperscalers',
  'Semiconductors',
  'Cybersecurity',
  'Enterprise Software',
  'Applied AI',
  'Data & Analytics',
  'Cloud Infrastructure',
  'DevOps & Automation',
  'Fintech',
  'Digital Commerce',
  'E-Commerce',
  'Digital Advertising',
  'Consumer Electronics',
  'Entertainment & Media',
  'Gaming',
  'Automotive',
  // Private company subsectors
  'AI Models & Agents',
  'AI Infrastructure',
  'AI Software',
  'Design & Creative',
];

// --- Common company names (human-friendly, not legal names) ---
const COMMON_NAMES = {
  'AMZN': 'Amazon', 'GOOG': 'Google', 'META': 'Meta', 'MSFT': 'Microsoft',
  'CRM': 'Salesforce', 'NOW': 'ServiceNow', 'HUBS': 'HubSpot',
  'TEAM': 'Atlassian', 'WDAY': 'Workday', 'INTU': 'Intuit',
  'CRWD': 'CrowdStrike', 'ZS': 'Zscaler', 'PANW': 'Palo Alto Networks',
  'S': 'SentinelOne', 'FTNT': 'Fortinet', 'OKTA': 'Okta',
  'SNOW': 'Snowflake', 'MDB': 'MongoDB', 'DDOG': 'Datadog',
  'ESTC': 'Elastic', 'PLTR': 'Palantir',
  'CFLT': 'Confluent', 'NET': 'Cloudflare', 'FSLY': 'Fastly',
  'DOCN': 'DigitalOcean', 'RBRK': 'Rubrik',
  'VRNS': 'Varonis', 'MNDY': 'monday.com', 'ASAN': 'Asana',
  'GTLB': 'GitLab', 'PATH': 'UiPath',
  'MRVL': 'Marvell', 'ARM': 'Arm', 'TSM': 'TSMC',
  'SHOP': 'Shopify', 'TTD': 'Trade Desk',
  'BILL': 'Bill.com', 'FOUR': 'Shift4 Payments',
  'IOT': 'Samsara', 'AI': 'C3.ai', 'TENB': 'Tenable', 'DT': 'Dynatrace',
  'ANET': 'Arista Networks', 'SE': 'Sea Limited', 'PINS': 'Pinterest', 'AFRM': 'Affirm',
  'XYZ': 'Block',
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

// --- Company headquarters (static map for known tickers, fallback for fast rendering) ---
const COMPANY_HQ = {
  'RBRK': 'Palo Alto, CA', 'ZS': 'San Jose, CA', 'NET': 'San Francisco, CA',
  'PLTR': 'Denver, CO', 'AMZN': 'Seattle, WA', 'GOOG': 'Mountain View, CA',
  'META': 'Menlo Park, CA', 'MSFT': 'Redmond, WA', 'CRWD': 'Austin, TX',
  'MDB': 'New York, NY', 'SNOW': 'Bozeman, MT', 'PANW': 'Santa Clara, CA',
  'CRM': 'San Francisco, CA', 'NOW': 'Santa Clara, CA', 'S': 'Mountain View, CA',
  'FTNT': 'Sunnyvale, CA', 'DDOG': 'New York, NY', 'HUBS': 'Cambridge, MA',
  'TEAM': 'Sydney, Australia', 'WDAY': 'Pleasanton, CA', 'CFLT': 'Mountain View, CA',
  'DOCN': 'New York, NY', 'ESTC': 'Mountain View, CA',
  'INTU': 'Mountain View, CA', 'FSLY': 'San Francisco, CA', 'OKTA': 'San Francisco, CA',
  'AVGO': 'Palo Alto, CA', 'MRVL': 'Wilmington, DE', 'ARM': 'Cambridge, UK',
  'NVDA': 'Santa Clara, CA', 'TSM': 'Hsinchu, Taiwan', 'MNDY': 'Tel Aviv, Israel',
  'ADBE': 'San Jose, CA', 'ASAN': 'San Francisco, CA', 'GTLB': 'San Francisco, CA',
  'PATH': 'New York, NY', 'VRNS': 'New York, NY', 'BILL': 'San Jose, CA',
  'FOUR': 'Allentown, PA', 'COIN': 'New York, NY', 'SHOP': 'Ottawa, Canada',
  'TTD': 'Ventura, CA', 'AI': 'Redwood City, CA', 'IOT': 'San Francisco, CA',
  'AMD': 'Santa Clara, CA', 'SNPS': 'Sunnyvale, CA', 'CDNS': 'San Jose, CA',
  'TENB': 'Columbia, MD', 'DT': 'Waltham, MA', 'ANET': 'Santa Clara, CA',
  'SE': 'Singapore', 'PINS': 'San Francisco, CA', 'XYZ': 'Oakland, CA', 'AFRM': 'San Francisco, CA',
};

// --- Initial tickers ---
const DEFAULT_TICKERS = [
  // --- Hyperscalers ---
  'AMZN','GOOG','META','MSFT',
  // --- Semiconductors ---
  'NVDA','AVGO','AMD','TSM','ARM','MRVL','SNPS','CDNS',
  // --- Cybersecurity ---
  'CRWD','PANW','ZS','FTNT','S','OKTA','RBRK','VRNS','TENB',
  // --- Enterprise Software ---
  'CRM','NOW','WDAY','INTU','ADBE','HUBS','TEAM','MNDY','ASAN',
  // --- Data & Analytics / Observability ---
  'SNOW','MDB','DDOG','PLTR','ESTC','CFLT','DT',
  // --- Cloud Infrastructure ---
  'NET','ANET','DOCN','FSLY',
  // --- DevOps & Automation ---
  'GTLB','PATH',
  // --- Fintech ---
  'COIN','BILL','FOUR','XYZ','AFRM',
  // --- Digital Commerce ---
  'SHOP','SE',
  // --- Digital Advertising ---
  'TTD','PINS',
  // --- Applied AI ---
  'AI','IOT',
  // --- Weekly Briefing Picks ---
  'ELV','TMO','ACGL','LPLA',   // Value picks
  'SNDK','CIEN','WDC','CF',    // Momentum picks
];

// --- Default private companies ---
const DEFAULT_PRIVATE_COMPANIES = [
  // --- AI Models & Agents ---
  { name: 'OpenAI', subsector: 'AI Models & Agents', valuation: '$840B', funding: '$110B Later Stage VC (Feb 2026)', revenue: '~$25B TTM', metrics: '4,000 employees, profitable', headquarters: 'San Francisco, CA', lead_investors: 'Amazon, SoftBank, Microsoft' },
  { name: 'Anthropic', subsector: 'AI Models & Agents', valuation: '$380B', funding: '$30.6B Later Stage VC (Feb 2026)', revenue: '~$20B TTM', metrics: '2,500 employees, Claude models', headquarters: 'San Francisco, CA', lead_investors: 'Amazon, Alphabet, Spark Capital' },
  { name: 'xAI', subsector: 'AI Models & Agents', valuation: '$250B', funding: 'Merged with X Corp (Feb 2026)', revenue: '~$2B TTM', metrics: '4,900 employees, Grok model', headquarters: 'Palo Alto, CA', lead_investors: 'X Corp (merger)' },
  { name: 'Sierra', subsector: 'AI Models & Agents', valuation: '$10B', funding: '$350M Later Stage VC (Sep 2025)', revenue: '~$20M TTM', metrics: '359 employees, enterprise CX agents', headquarters: 'San Francisco, CA', lead_investors: 'SoftBank, Sequoia, Benchmark' },
  // --- AI Infrastructure ---
  { name: 'Databricks', subsector: 'AI Infrastructure', valuation: '$134B', funding: '$7B Later Stage VC (Feb 2026)', revenue: '~$5.4B TTM', metrics: '8,000 employees, data + AI platform', headquarters: 'San Francisco, CA', lead_investors: 'a16z, Lightspeed, Sands Capital' },
  { name: 'CoreWeave', subsector: 'AI Infrastructure', valuation: '$18.6B (IPO)', funding: 'IPO Mar 2025 · $2B PIPE (Jan 2026)', revenue: '~$5.1B TTM', metrics: '2,189 employees · NOW PUBLIC (CRWV)', headquarters: 'Livingston, NJ', lead_investors: 'Nvidia, Magnetar, OpenAI', status: 'public', ticker: 'CRWV' },
  { name: 'Scale AI', subsector: 'AI Infrastructure', valuation: '$74.1B', funding: '$14.3B Later Stage VC (Jun 2025)', revenue: '~$2B TTM', metrics: '1,000 employees, AI data platform', headquarters: 'San Francisco, CA', lead_investors: 'Accel, AWS, Founders Fund' },
  { name: 'Cerebras', subsector: 'AI Infrastructure', valuation: '$23B', funding: '$1.1B VC (Feb 2026) · IPO filing (Mar 2026)', revenue: '~$273M TTM', metrics: '784 employees · IN IPO REGISTRATION', headquarters: 'Sunnyvale, CA', lead_investors: 'Fidelity, Alpha Wave Global', status: 'ipo_pending' },
  // --- AI Software ---
  { name: 'Anysphere (Cursor)', subsector: 'AI Software', valuation: '$29.3B', funding: '$2.3B Later Stage VC (Nov 2025)', revenue: '~$2B TTM', metrics: '300 employees, AI-native code editor', headquarters: 'San Francisco, CA', lead_investors: 'a16z, Alphabet, Nvidia, Thrive' },
  { name: 'Cognition AI', subsector: 'AI Software', valuation: '$10.2B', funding: '$400M Later Stage VC (Sep 2025)', revenue: 'N/A', metrics: 'Devin AI agent, autonomous coding', headquarters: 'New York, NY', lead_investors: 'Khosla, Lux Capital, Bain Capital' },
  { name: 'Glean', subsector: 'AI Software', valuation: '$7.2B', funding: '$150M Later Stage VC (Jun 2025)', revenue: '~$250M TTM', metrics: '1,000 employees, enterprise knowledge AI', headquarters: 'Palo Alto, CA', lead_investors: 'Sequoia, Lightspeed, Kleiner Perkins' },
  // --- Fintech ---
  { name: 'Stripe', subsector: 'Fintech', valuation: '$159B', funding: 'Secondary (Feb 2026)', revenue: '~$1B net rev TTM', metrics: '8,500 employees, payments infrastructure', headquarters: 'South San Francisco, CA', lead_investors: 'Sequoia, a16z, Founders Fund' },
  { name: 'Rippling', subsector: 'Fintech', valuation: '$16.8B', funding: 'Later Stage VC (Dec 2025)', revenue: 'N/A', metrics: '6,473 employees, HR + IT + Finance', headquarters: 'San Francisco, CA', lead_investors: 'Bain Capital, GIC, Goldman Sachs' },
  // --- Design & Creative ---
  { name: 'Canva', subsector: 'Design & Creative', valuation: '$42B', funding: 'Secondary (Sep 2025)', revenue: '~$4B TTM', metrics: '11,813 employees, 200M+ monthly users', headquarters: 'Surry Hills, Australia', lead_investors: 'Sequoia, Blackbird, CapitalG' },
  { name: 'Figma', subsector: 'Design & Creative', valuation: '$16.1B (IPO)', funding: 'IPO Jul 2025 (NYSE: FIG)', revenue: '~$1.1B TTM', metrics: '1,886 employees · NOW PUBLIC (FIG)', headquarters: 'San Francisco, CA', lead_investors: 'a16z, Greylock, Index Ventures', status: 'public', ticker: 'FIG' },
  // --- T. Rowe Price Private Portfolio ---
  // Aerospace & Defense
  { name: 'SpaceX', subsector: 'Aerospace & Defense', valuation: '$800B+', funding: 'Multiple growth rounds', revenue: '~$15B TTM', metrics: 'Starlink, Starship, 13,000+ employees', headquarters: 'Hawthorne, CA', lead_investors: 'Founders Fund, a16z, T. Rowe Price' },
  { name: 'Anduril', subsector: 'Aerospace & Defense', valuation: '$30.5B', funding: '$2.5B Series G (Jun 2025)', revenue: '~$1B TTM', metrics: 'Autonomous defense systems, 4,000+ employees', headquarters: 'Costa Mesa, CA', lead_investors: 'Founders Fund, a16z, T. Rowe Price' },
  { name: 'ABL Space Systems', subsector: 'Aerospace & Defense', valuation: '$2B', funding: 'Later Stage VC', revenue: 'N/A', metrics: 'RS1 launch vehicle, small satellite deployment', headquarters: 'El Segundo, CA', lead_investors: 'T. Rowe Price, Fidelity' },
  // AI Infrastructure & Hardware
  { name: 'ClickHouse', subsector: 'AI Infrastructure', valuation: '$15B', funding: '$400M Series D (Jan 2026)', revenue: 'N/A', metrics: 'Real-time analytics DB, used by Meta, Anthropic', headquarters: 'San Francisco, CA', lead_investors: 'Index Ventures, Coatue, T. Rowe Price' },
  { name: 'Lightmatter', subsector: 'AI Infrastructure', valuation: '$4B', funding: 'Later Stage VC', revenue: 'N/A', metrics: 'Photonic AI chips using light instead of electricity', headquarters: 'Mountain View, CA', lead_investors: 'T. Rowe Price, GV, Viking Global' },
  { name: 'SandboxAQ', subsector: 'AI Infrastructure', valuation: '$5.75B', funding: 'Series E (Apr 2025)', revenue: 'N/A', metrics: 'Quantum-AI, Alphabet spinout 2022', headquarters: 'Palo Alto, CA', lead_investors: 'T. Rowe Price, Rizvi Traverse, Eric Schmidt' },
  // AI Software
  { name: 'Perplexity', subsector: 'AI Software', valuation: '$20B', funding: '$500M Later Stage VC (Sep 2025)', revenue: '~$100M+ TTM', metrics: 'AI search platform, 100M+ users', headquarters: 'San Francisco, CA', lead_investors: 'IVP, NEA, T. Rowe Price' },
  { name: 'Harvey', subsector: 'AI Software', valuation: '$8B', funding: '$160M (Dec 2025)', revenue: 'N/A', metrics: 'Legal AI, 50%+ Am Law 100 adoption', headquarters: 'San Francisco, CA', lead_investors: 'a16z, Sequoia, T. Rowe Price' },
  // Autonomous Vehicles
  { name: 'Waymo', subsector: 'Autonomous Vehicles', valuation: '$126B', funding: '$16B (Feb 2026)', revenue: 'N/A', metrics: '15M+ rides in 2025, Alphabet subsidiary', headquarters: 'Mountain View, CA', lead_investors: 'Alphabet, a16z, T. Rowe Price' },
  // Fintech
  { name: 'iCapital', subsector: 'Fintech', valuation: '$7.5B', funding: '$820M Series E (Jul 2025)', revenue: 'N/A', metrics: 'Alt investment access for wealth advisors', headquarters: 'New York, NY', lead_investors: 'SurgoCap, T. Rowe Price, WestCap' },
  // Identity & Security
  { name: 'Socure', subsector: 'Identity & Security', valuation: '$5B', funding: 'Later Stage VC', revenue: 'N/A', metrics: 'AI identity verification for banks & gov', headquarters: 'Midtown, NY', lead_investors: 'Accel, T. Rowe Price, Bain Capital' },
  // Legal Technology
  { name: 'Clio', subsector: 'Legal Technology', valuation: '$5B', funding: '$900M Series F (Jul 2024)', revenue: '~$250M TTM', metrics: '150K+ law professionals, cloud practice mgmt', headquarters: 'Burnaby, Canada', lead_investors: 'NEA, TCV, T. Rowe Price' },
  // Healthcare & Biotech
  { name: 'Color', subsector: 'Healthcare & Biotech', valuation: '$5B', funding: 'Later Stage VC', revenue: 'N/A', metrics: 'Population health, genetic screening at scale', headquarters: 'Burlingame, CA', lead_investors: 'T. Rowe Price, General Catalyst, Viking' },
  // Consumer Software
  { name: 'Bending Spoons', subsector: 'Consumer Software', valuation: '$11B', funding: 'Later Stage VC', revenue: '~$1B TTM', metrics: 'Acquired Evernote, Splice, Issuu; 800+ employees', headquarters: 'Milan, Italy', lead_investors: 'T. Rowe Price' },
  // Clean Energy & Mining
  { name: 'KoBold Metals', subsector: 'Clean Energy & Mining', valuation: '$2.96B', funding: '$537M Series C (Oct 2024)', revenue: 'N/A', metrics: 'AI-powered critical mineral exploration', headquarters: 'Berkeley, CA', lead_investors: 'T. Rowe Price, Durable Capital, Andreessen' },
];

// --- Proper capitalization for company names ---
const CAPITALIZE_EXCEPTIONS = new Set(['ai', 'of', 'the', 'and', 'in', 'for', 'by', 'on', 'at', 'to', 'a', 'an', 'or', 'is']);
const CAPITALIZE_ALWAYS_UPPER = new Set(['AI', 'ML', 'API', 'CEO', 'CTO', 'HR', 'IT', 'UI', 'UX', 'XR', 'AR', 'VR', 'EV', 'IoT', 'SaaS', 'PaaS', 'IaaS', 'DeFi', 'NFT', 'DAO', 'LLM', 'GPU', 'HPC']);
// Known proper names for companies (lowercase key → correct casing)
const KNOWN_COMPANY_NAMES = {
  'openai': 'OpenAI', 'xai': 'xAI', 'coreweave': 'CoreWeave', 'scaleai': 'Scale AI',
  'scale ai': 'Scale AI', 'deepmind': 'DeepMind', 'youtube': 'YouTube',
  'linkedin': 'LinkedIn', 'github': 'GitHub', 'gitlab': 'GitLab', 'hubspot': 'HubSpot',
  'coinbase': 'Coinbase', 'mongodb': 'MongoDB', 'snowflake': 'Snowflake',
  'datadog': 'Datadog', 'crowdstrike': 'CrowdStrike', 'sentinelone': 'SentinelOne',
  'pagerduty': 'PagerDuty', 'hashicorp': 'HashiCorp', 'cockroachdb': 'CockroachDB',
  'airbnb': 'Airbnb', 'doordash': 'DoorDash', 'instacart': 'Instacart',
  'spacex': 'SpaceX', 'palantir': 'Palantir', 'uipath': 'UiPath',
  'monday.com': 'monday.com', 'clickup': 'ClickUp', 'webflow': 'Webflow',
  'mistral ai': 'Mistral AI', 'mistralai': 'Mistral AI', 'midjourney': 'Midjourney',
  'perplexity': 'Perplexity', 'hugging face': 'Hugging Face', 'huggingface': 'Hugging Face',
  'anyscale': 'Anyscale', 'langchain': 'LangChain', 'pinecone': 'Pinecone',
  'supabase': 'Supabase', 'vercel': 'Vercel', 'netlify': 'Netlify',
  'plaid': 'Plaid', 'brex': 'Brex', 'ramp': 'Ramp', 'klarna': 'Klarna',
  'revolut': 'Revolut', 'nubank': 'Nubank', 'chime': 'Chime',
  'spacex': 'SpaceX', 'clickhouse': 'ClickHouse', 'sandboxaq': 'SandboxAQ',
  'icapital': 'iCapital', 'kobold metals': 'KoBold Metals',
  'abl space systems': 'ABL Space Systems', 'bending spoons': 'Bending Spoons',
};
function capitalizeCompanyName(name) {
  if (!name || typeof name !== 'string') return name;
  const trimmed = name.trim();
  if (!trimmed) return trimmed;
  // Check known names first (case-insensitive)
  const knownKey = trimmed.toLowerCase().replace(/\s+/g, ' ');
  if (KNOWN_COMPANY_NAMES[knownKey]) return KNOWN_COMPANY_NAMES[knownKey];
  // Also try without spaces
  const noSpaceKey = knownKey.replace(/\s/g, '');
  if (KNOWN_COMPANY_NAMES[noSpaceKey]) return KNOWN_COMPANY_NAMES[noSpaceKey];
  // If name is already properly mixed-case (has upper beyond first char), keep it
  // This preserves intentional casing like "xAI", "CoreWeave", "monday.com"
  const hasIntentionalCase = /[a-z][A-Z]/.test(trimmed) || /\.[a-z]/.test(trimmed);
  if (hasIntentionalCase) return trimmed;
  // If name is all-lowercase or ALL-CAPS, apply title case
  const isAllLower = trimmed === trimmed.toLowerCase();
  const isAllUpper = trimmed === trimmed.toUpperCase() && trimmed.length > 2;
  if (!isAllLower && !isAllUpper) return trimmed;
  return trimmed.split(/\s+/).map((word, i) => {
    const upper = word.toUpperCase();
    // Check if it's a known abbreviation
    for (const abbr of CAPITALIZE_ALWAYS_UPPER) {
      if (upper === abbr.toUpperCase()) return abbr;
    }
    // First word always capitalized; small words lowercase in middle
    if (i > 0 && CAPITALIZE_EXCEPTIONS.has(word.toLowerCase())) {
      return word.toLowerCase();
    }
    // Standard title case
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
  }).join(' ');
}

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
  // Guard: reject literal "undefined"/"null" strings
  if (industry === 'undefined' || industry === 'null') industry = null;
  if (sector === 'undefined' || sector === 'null') sector = null;

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
  const override = overrides[ticker];
  // Guard: reject stored 'undefined'/'null' strings from bad data
  if (override && override !== 'undefined' && override !== 'null') return override;
  return SUBSECTOR_MAP[ticker] || 'Other';
}

function setSubsectorOverride(ticker, subsector) {
  // Guard: never store undefined/null/empty subsector
  if (!subsector || subsector === 'undefined' || subsector === 'null') return;
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
