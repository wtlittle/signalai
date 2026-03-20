/* ===== POPUP-CHART.JS — Chart rendering for ticker detail popup ===== */

let popupChartInstance = null;

// Color scheme: ticker pops, indexes colored, peers muted grey
const CHART_COLOR_TICKER = '#00e5ff';   // vivid cyan for the main ticker
// Index colors now in INDEX_COLOR_MAP below
const CHART_COLOR_PEERS = [
  '#4b5563', // peer 1 (dark grey)
  '#6b7280', // peer 2 (medium grey)
  '#374151', // peer 3 (darker grey)
  '#9ca3af', // peer 4 (lighter grey fallback)
];

const INDEX_TICKERS = new Set(['^GSPC', '^IXIC', 'IGV']);
const INDEX_COLOR_MAP = {
  '^GSPC': '#f5f5f5', // S&P 500 (white/light)
  '^IXIC': '#f59e0b', // NASDAQ (amber)
  'IGV':   '#8b5cf6', // IGV (purple)
};

const PERIOD_MAP = {
  '1M': { range: '1mo', interval: '1d', days: 21 },
  '3M': { range: '3mo', interval: '1d', days: 63 },
  '6M': { range: '6mo', interval: '1d', days: 126 },
  '1Y': { range: '1y', interval: '1d', days: 252 },
  '3Y': { range: '3y', interval: '1wk', days: 756 },
  '5Y': { range: '5y', interval: '1wk', days: 1260 },
  'Max': { range: 'max', interval: '1wk', days: null },
};

// Curated peer overrides — buy-side analyst quality comps.
// Key = ticker, Value = ordered list of best comps (first = closest).
// These override the subsector-based auto-peers when available.
const PEER_OVERRIDES = {
  // --- Cybersecurity ---
  'RBRK': ['CRWD', 'ZS', 'S'],       // Data security — high-growth cyber peers, S similar stage/profile
  'CRWD': ['PANW', 'S', 'ZS'],       // Endpoint platform — PANW direct comp, S competitor, ZS cloud
  'ZS':   ['CRWD', 'PANW', 'FTNT'],  // Zero trust — platform peers + network security baseline
  'PANW': ['CRWD', 'FTNT', 'ZS'],    // Platform cyber — CRWD direct, FTNT legacy comp, ZS cloud
  'S':    ['CRWD', 'RBRK', 'TENB'],  // Endpoint challenger — CRWD incumbent, RBRK similar stage, TENB vuln mgmt
  'FTNT': ['PANW', 'CRWD', 'ZS'],    // Network security — PANW direct, CRWD/ZS next-gen
  'OKTA': ['CRWD', 'ZS', 'PANW'],    // Identity — cross-ref to platform cyber
  'VRNS': ['RBRK', 'TENB', 'S'],     // Data security — RBRK data protection, TENB vuln mgmt, S similar size
  'TENB': ['VRNS', 'RBRK', 'S'],     // Vulnerability mgmt — data security peers, similar size
  // --- Hyperscalers ---
  'AMZN': ['GOOG', 'MSFT', 'META'],
  'GOOG': ['META', 'MSFT', 'AMZN'],
  'META': ['GOOG', 'AMZN', 'MSFT'],
  'MSFT': ['AMZN', 'GOOG', 'META'],
  // --- Semiconductors ---
  'NVDA': ['AMD', 'AVGO', 'TSM'],    // GPU — AMD direct comp, AVGO AI/networking, TSM foundry
  'AMD':  ['NVDA', 'AVGO', 'MRVL'],  // Compute — NVDA direct, AVGO networking, MRVL custom silicon
  'AVGO': ['MRVL', 'AMD', 'NVDA'],   // Custom/networking — MRVL closest, AMD/NVDA compute peers
  'ARM':  ['NVDA', 'SNPS', 'CDNS'],  // IP/design — NVDA licensing comp, SNPS/CDNS EDA ecosystem
  'TSM':  ['NVDA', 'AVGO', 'AMD'],   // Foundry — top 3 customers
  'MRVL': ['AVGO', 'AMD', 'NVDA'],   // Custom silicon — AVGO closest, then compute peers
  'SNPS': ['CDNS', 'ARM', 'NVDA'],   // EDA — CDNS direct comp, ARM ecosystem, NVDA design tools
  'CDNS': ['SNPS', 'ARM', 'NVDA'],   // EDA — SNPS direct comp, ARM ecosystem
  // --- Data & Analytics ---
  'SNOW': ['MDB', 'DDOG', 'CFLT'],
  'MDB':  ['SNOW', 'ESTC', 'CFLT'],
  'DDOG': ['DT', 'SNOW', 'ESTC'],    // Observability — DT direct comp, then data platform peers
  'DT':   ['DDOG', 'ESTC', 'SNOW'],  // Observability — DDOG direct comp
  'PLTR': ['SNOW', 'AI', 'DDOG'],
  'ESTC': ['MDB', 'DDOG', 'DT'],
  'CFLT': ['MDB', 'SNOW', 'ESTC'],
  // --- Enterprise Software ---
  'CRM':  ['NOW', 'WDAY', 'HUBS'],
  'NOW':  ['CRM', 'WDAY', 'TEAM'],
  'HUBS': ['CRM', 'MNDY', 'TEAM'],
  'WDAY': ['NOW', 'CRM', 'INTU'],
  'INTU': ['ADBE', 'CRM', 'WDAY'],
  'ADBE': ['INTU', 'CRM', 'NOW'],
  'TEAM': ['MNDY', 'GTLB', 'ASAN'],
  'MNDY': ['TEAM', 'ASAN', 'HUBS'],
  'ASAN': ['MNDY', 'TEAM', 'HUBS'],
  // --- Cloud Infrastructure ---
  'NET':  ['ANET', 'FSLY', 'DOCN'],  // CDN/edge — ANET networking, FSLY direct CDN comp
  'ANET': ['NET', 'DOCN', 'FSLY'],   // Networking — NET edge/CDN, DOCN cloud
  'FSLY': ['NET', 'DOCN', 'ANET'],
  'DOCN': ['NET', 'FSLY', 'ANET'],
  // --- DevOps & Automation ---
  'GTLB': ['TEAM', 'PATH', 'DDOG'],  // DevOps — TEAM direct comp, PATH automation, DDOG DevOps adjacent
  'PATH': ['GTLB', 'AI', 'TEAM'],    // Automation — GTLB DevOps, AI enterprise AI comp
  // --- Fintech ---
  'COIN': ['XYZ', 'AFRM', 'FOUR'],   // Crypto/payments — SQ (crypto exposure), AFRM fintech disruptor
  'XYZ':   ['COIN', 'FOUR', 'BILL'],  // Payments — COIN crypto, FOUR merchant payments
  'BILL': ['FOUR', 'XYZ', 'COIN'],    // B2B payments — FOUR merchant, SQ SMB payments
  'FOUR': ['XYZ', 'BILL', 'COIN'],    // Merchant payments — SQ direct, BILL B2B
  'AFRM': ['XYZ', 'COIN', 'SHOP'],   // BNPL/fintech — SQ fintech peer, COIN disruptor, SHOP (BNPL partner)
  // --- Digital Commerce ---
  'SHOP': ['SE', 'XYZ', 'AFRM'],     // E-commerce platform — SE direct comp, SQ merchant, AFRM BNPL
  'SE':   ['SHOP', 'COIN', 'XYZ'],    // E-commerce — SHOP direct, fintech peers (Sea Money)
  // --- Digital Advertising ---
  'TTD':  ['PINS', 'META', 'GOOG'],  // Programmatic — PINS intent-based, META/GOOG ad platforms
  'PINS': ['TTD', 'SHOP', 'META'],   // Social commerce/ads — TTD ad-tech, SHOP commerce, META social
  // --- Applied AI ---
  'AI':   ['PLTR', 'IOT', 'PATH'],   // Enterprise AI — PLTR analytics, IOT IoT/AI, PATH automation
  'IOT':  ['AI', 'DDOG', 'DT'],      // IoT/operational — AI enterprise peer, DDOG/DT monitoring comps
};

// Get peer tickers — curated overrides first, then subsector fallback
function getPeers(ticker, maxPeers = 3) {
  // Use curated peers if available
  if (PEER_OVERRIDES[ticker]) {
    const curated = PEER_OVERRIDES[ticker].filter(t => tickerList.includes(t) && t !== ticker);
    if (curated.length > 0) return curated.slice(0, maxPeers);
  }
  // Fallback: same subsector, excluding self
  const sub = getSubsector(ticker);
  const peers = tickerList.filter(t => t !== ticker && getSubsector(t) === sub);
  return peers.slice(0, maxPeers);
}

// Fetch and normalize chart data for comparison
async function fetchNormalizedSeries(ticker, range, interval) {
  const data = await fetchChartData(ticker, range, interval);
  if (!data || !data.closes.length) return null;

  const timestamps = data.timestamps;
  const closes = data.closes;
  const basePrice = closes[0];
  if (!basePrice) return null;

  const normalized = closes.map(c => c ? ((c - basePrice) / basePrice) * 100 : null);
  // Convert timestamps to ISO date strings for alignment
  const dateKeys = timestamps.map(t => {
    const d = new Date(t * 1000);
    return d.toISOString().split('T')[0]; // "YYYY-MM-DD"
  });

  return { ticker, dateKeys, values: normalized, basePrice, lastPrice: closes[closes.length - 1] };
}

// Render the comparison chart
async function renderPopupChart(container, ticker, period = '1Y') {
  const cfg = PERIOD_MAP[period] || PERIOD_MAP['1Y'];

  // Show loading
  container.innerHTML = '<div class="popup-loading">Loading chart data</div>';

  // Determine comparison tickers
  const peers = getPeers(ticker);
  const compTickers = [ticker, '^GSPC', '^IXIC', 'IGV', ...peers];

  // Fetch all series in parallel
  const seriesPromises = compTickers.map(t => fetchNormalizedSeries(t, cfg.range, cfg.interval));
  const allSeries = await Promise.allSettled(seriesPromises);

  const validSeries = [];
  const failedIndexes = [];
  allSeries.forEach((result, idx) => {
    if (result.status !== 'fulfilled' || !result.value) {
      if (INDEX_TICKERS.has(compTickers[idx])) failedIndexes.push(compTickers[idx]);
      return;
    }
    validSeries.push({ idx, series: result.value });
  });

  // Retry failed indexes once (they should always be present)
  if (failedIndexes.length > 0) {
    console.warn('Chart: retrying failed indexes:', failedIndexes);
    const retries = await Promise.allSettled(
      failedIndexes.map(t => fetchNormalizedSeries(t, cfg.range, cfg.interval))
    );
    retries.forEach((result, i) => {
      if (result.status === 'fulfilled' && result.value) {
        const origIdx = compTickers.indexOf(failedIndexes[i]);
        validSeries.push({ idx: origIdx, series: result.value });
      } else {
        console.error('Chart: index still failed after retry:', failedIndexes[i]);
      }
    });
  }

  if (!validSeries.length) {
    container.innerHTML = '<div style="color:var(--text-muted);padding:20px;text-align:center;">No chart data available</div>';
    return;
  }

  // --- Date alignment: build a unified date axis from ALL series ---
  const dateSet = new Set();
  validSeries.forEach(({ series }) => {
    series.dateKeys.forEach(dk => dateSet.add(dk));
  });
  const allDates = Array.from(dateSet).sort(); // chronological order

  // For period-based filtering (1M, 3M, etc.), trim the unified date axis
  let filteredDates = allDates;
  if (cfg.days != null) {
    // Keep only the last N trading days worth of dates
    filteredDates = allDates.slice(-Math.min(cfg.days, allDates.length));
  }

  // --- Build datasets aligned to the unified date axis ---
  const datasets = [];

  validSeries.forEach(({ idx, series }) => {
    const s = series;
    // Create a map from dateKey → value
    const dateValueMap = new Map();
    for (let i = 0; i < s.dateKeys.length; i++) {
      dateValueMap.set(s.dateKeys[i], s.values[i]);
    }

    // Find the first available date in our filtered range to re-base the series
    let baseDate = null;
    let baseOrigIdx = null;
    for (const dk of filteredDates) {
      if (dateValueMap.has(dk)) {
        baseDate = dk;
        baseOrigIdx = s.dateKeys.indexOf(dk);
        break;
      }
    }
    if (baseDate == null) return; // No overlap at all, skip

    // Re-normalize from the start of the filtered window
    const basePrice = s.basePrice;
    const baseOrigValue = dateValueMap.get(baseDate); // This is already % from original base
    // We need to re-base: new_value = ((1 + old_value/100) / (1 + baseOrigValue/100) - 1) * 100
    const baseFactor = 1 + (baseOrigValue || 0) / 100;

    // Align data to filtered dates
    const alignedData = filteredDates.map(dk => {
      const val = dateValueMap.get(dk);
      if (val == null) return null;
      return ((1 + val / 100) / baseFactor - 1) * 100;
    });

    // Compute total return for legend
    let lastVal = null;
    for (let i = alignedData.length - 1; i >= 0; i--) {
      if (alignedData[i] != null) { lastVal = alignedData[i]; break; }
    }
    const displayName = s.ticker === '^GSPC' ? 'S&P 500' :
                        s.ticker === '^IXIC' ? 'NASDAQ' :
                        s.ticker;
    const returnStr = lastVal != null ? ` (${lastVal >= 0 ? '+' : ''}${lastVal.toFixed(1)}%)` : '';

    // Color logic: based on ticker identity, not position
    let lineColor, lineWidth, dash;
    if (s.ticker === ticker) {
      // Main ticker: vivid cyan, thick
      lineColor = CHART_COLOR_TICKER;
      lineWidth = 3;
      dash = [];
    } else if (INDEX_COLOR_MAP[s.ticker]) {
      // Index: colored, medium weight
      lineColor = INDEX_COLOR_MAP[s.ticker];
      lineWidth = 1.5;
      dash = [];
    } else {
      // Peer: grey, dashed
      const peerIdx = compTickers.filter(t => !INDEX_TICKERS.has(t) && t !== ticker).indexOf(s.ticker);
      lineColor = CHART_COLOR_PEERS[Math.max(0, peerIdx) % CHART_COLOR_PEERS.length];
      lineWidth = 1.5;
      dash = [5, 4];
    }

    datasets.push({
      label: `${displayName}${returnStr}`,
      data: alignedData,
      borderColor: lineColor,
      backgroundColor: 'transparent',
      borderWidth: lineWidth,
      pointRadius: 0,
      pointHitRadius: 6,
      tension: 0.1,
      borderDash: dash,
      spanGaps: true, // connect across null gaps for cleaner lines
    });
  });

  if (!datasets.length) {
    container.innerHTML = '<div style="color:var(--text-muted);padding:20px;text-align:center;">No chart data available</div>';
    return;
  }

  // Build chart
  container.innerHTML = '<canvas id="popup-chart-canvas" style="width:100%;height:320px;"></canvas>';
  const canvas = container.querySelector('canvas');
  const ctx = canvas.getContext('2d');

  if (popupChartInstance) {
    popupChartInstance.destroy();
    popupChartInstance = null;
  }

  popupChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: filteredDates,
      datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: {
            color: '#9ca3af',
            font: { family: "'Inter', sans-serif", size: 10 },
            boxWidth: 14,
            padding: 8,
            usePointStyle: true,
            pointStyle: 'line',
          },
        },
        tooltip: {
          backgroundColor: '#1a1f2e',
          titleColor: '#e5e7eb',
          bodyColor: '#9ca3af',
          borderColor: '#374151',
          borderWidth: 1,
          titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
          bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
          callbacks: {
            label: (ctx) => {
              const val = ctx.parsed.y;
              if (val == null) return null;
              return `${ctx.dataset.label.split(' (')[0]}: ${val >= 0 ? '+' : ''}${val.toFixed(1)}%`;
            },
          },
        },
      },
      scales: {
        x: {
          display: true,
          grid: { color: 'rgba(30,41,59,0.5)', drawBorder: false },
          ticks: {
            color: '#6b7280',
            font: { family: "'JetBrains Mono', monospace", size: 9 },
            maxTicksLimit: 8,
            maxRotation: 0,
          },
        },
        y: {
          display: true,
          grid: { color: 'rgba(30,41,59,0.5)', drawBorder: false },
          ticks: {
            color: '#6b7280',
            font: { family: "'JetBrains Mono', monospace", size: 10 },
            callback: (v) => (v >= 0 ? '+' : '') + v.toFixed(0) + '%',
          },
        },
      },
    },
  });
}
