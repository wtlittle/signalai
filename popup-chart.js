/* ===== POPUP-CHART.JS — Chart rendering for ticker detail popup ===== */

let popupChartInstance = null;

// Color scheme: ticker pops, indexes colored, peers muted grey
const CHART_COLOR_TICKER = '#00e5ff';   // vivid cyan for the main ticker
const CHART_COLOR_INDEXES = [
  '#f5f5f5', // S&P 500 (white/light)
  '#f59e0b', // NASDAQ (amber)
  '#8b5cf6', // IGV (purple)
];
const CHART_COLOR_PEERS = [
  '#4b5563', // peer 1 (dark grey)
  '#6b7280', // peer 2 (medium grey)
  '#374151', // peer 3 (darker grey)
];

const PERIOD_MAP = {
  '1M': { range: '1mo', interval: '1d', days: 21 },
  '3M': { range: '3mo', interval: '1d', days: 63 },
  '6M': { range: '6mo', interval: '1d', days: 126 },
  '1Y': { range: '1y', interval: '1d', days: 252 },
  '3Y': { range: '3y', interval: '1wk', days: 756 },
  '5Y': { range: '5y', interval: '1wk', days: 1260 },
};

// Get peer tickers from same subsector
function getPeers(ticker, maxPeers = 2) {
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
  const dates = timestamps.map(t => new Date(t * 1000));

  return { ticker, dates, values: normalized, basePrice, lastPrice: closes[closes.length - 1] };
}

// Render the comparison chart
async function renderPopupChart(container, ticker, period = '1Y') {
  const cfg = PERIOD_MAP[period] || PERIOD_MAP['1Y'];

  // Show loading
  container.innerHTML = '<div class="popup-loading">Loading chart data</div>';

  // Determine comparison tickers
  const peers = getPeers(ticker);
  const compTickers = [ticker, '^GSPC', '^IXIC', 'IGV', ...peers];

  // Fetch all series
  const seriesPromises = compTickers.map(t => fetchNormalizedSeries(t, cfg.range, cfg.interval));
  const allSeries = await Promise.allSettled(seriesPromises);

  const datasets = [];
  const labels = [];
  let maxLen = 0;

  allSeries.forEach((result, idx) => {
    if (result.status !== 'fulfilled' || !result.value) return;
    const s = result.value;
    if (s.dates.length > maxLen) {
      maxLen = s.dates.length;
      labels.length = 0;
      labels.push(...s.dates);
    }

    const displayName = s.ticker === '^GSPC' ? 'S&P 500' :
                        s.ticker === '^IXIC' ? 'NASDAQ' :
                        s.ticker;
    const returnVal = s.values[s.values.length - 1];
    const returnStr = returnVal != null ? ` (${returnVal >= 0 ? '+' : ''}${returnVal.toFixed(1)}%)` : '';

    // Color logic: idx 0 = ticker (vivid), 1-3 = indexes (colored), 4+ = peers (grey)
    let lineColor, lineWidth, dash;
    if (idx === 0) {
      lineColor = CHART_COLOR_TICKER;
      lineWidth = 3;
      dash = [];
    } else if (idx >= 1 && idx <= 3) {
      lineColor = CHART_COLOR_INDEXES[idx - 1];
      lineWidth = 1.5;
      dash = [];
    } else {
      lineColor = CHART_COLOR_PEERS[(idx - 4) % CHART_COLOR_PEERS.length];
      lineWidth = 1.5;
      dash = [5, 4];
    }

    datasets.push({
      label: `${displayName}${returnStr}`,
      data: s.values,
      borderColor: lineColor,
      backgroundColor: 'transparent',
      borderWidth: lineWidth,
      pointRadius: 0,
      pointHitRadius: 6,
      tension: 0.1,
      borderDash: dash,
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
      labels: labels.map(d => d.toISOString().split('T')[0]),
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
