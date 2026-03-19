/* ===== POPUP-DEEP-DIVE.JS — "See More" section: Quant Factors, Short Interest, S&P 500 Outperformance ===== */

let deepDiveExpanded = false;
let shortInterestChart = null;
let outperformanceChart = null;

function createSeeMoreButton() {
  return `
    <div class="see-more-wrapper" id="see-more-wrapper">
      <button class="see-more-btn" id="see-more-btn">
        <span>Deep Dive</span>
        <span class="see-more-arrow">▼</span>
      </button>
    </div>
    <div class="deep-dive-container" id="deep-dive-container" style="display:none;">
      <div class="deep-dive-loading" id="deep-dive-loading">
        <div class="popup-loading">Loading deep dive data</div>
      </div>
      <div class="deep-dive-content" id="deep-dive-content" style="display:none;"></div>
    </div>
  `;
}

async function toggleDeepDive(ticker, data) {
  const container = document.getElementById('deep-dive-container');
  const btn = document.getElementById('see-more-btn');
  const arrow = btn.querySelector('.see-more-arrow');
  const loadingEl = document.getElementById('deep-dive-loading');
  const contentEl = document.getElementById('deep-dive-content');

  if (deepDiveExpanded) {
    container.style.display = 'none';
    arrow.textContent = '▼';
    arrow.classList.remove('rotated');
    deepDiveExpanded = false;
    destroyDeepDiveCharts();
    return;
  }

  deepDiveExpanded = true;
  container.style.display = 'block';
  arrow.textContent = '▲';
  arrow.classList.add('rotated');
  loadingEl.style.display = 'block';
  contentEl.style.display = 'none';

  try {
    // Fetch all 4 data sources in parallel
    const [quantData, shortData, outperfData, compsData] = await Promise.allSettled([
      fetchQuantFactors(ticker),
      fetchShortInterest(ticker),
      fetchOutperformance(ticker),
      fetchCrossSectorComps(ticker),
    ]);

    const quant = quantData.status === 'fulfilled' ? quantData.value : null;
    const short_ = shortData.status === 'fulfilled' ? shortData.value : null;
    const outperf = outperfData.status === 'fulfilled' ? outperfData.value : null;
    const comps = compsData.status === 'fulfilled' ? compsData.value : null;

    loadingEl.style.display = 'none';
    contentEl.style.display = 'block';
    renderDeepDiveContent(ticker, quant, short_, outperf, data, comps);
  } catch (e) {
    console.error('Deep dive error:', e);
    loadingEl.innerHTML = '<div style="color:var(--red);padding:20px;text-align:center;">Failed to load deep dive data</div>';
  }
}

function destroyDeepDiveCharts() {
  if (shortInterestChart) { shortInterestChart.destroy(); shortInterestChart = null; }
  if (outperformanceChart) { outperformanceChart.destroy(); outperformanceChart = null; }
}

// --- Data fetching ---

async function fetchQuantFactors(ticker) {
  // Try backend first
  if (await checkBackend()) {
    try {
      const resp = await fetch(`${BACKEND_URL}/quant-factors?symbol=${ticker}`, { signal: AbortSignal.timeout(60000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.error) return data;
    } catch (e) {
      console.warn('Quant factors backend failed:', e);
    }
  }
  // Always try client-side fallback
  return computeClientSideQuant(ticker);
}

async function fetchShortInterest(ticker) {
  if (await checkBackend()) {
    try {
      const resp = await fetch(`${BACKEND_URL}/short-interest?symbol=${ticker}`, { signal: AbortSignal.timeout(30000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.error) return data;
    } catch (e) {
      console.warn('Short interest backend failed:', e);
    }
  }
  // Client-side fallback: Supabase → snapshot
  if (typeof fetchShortInterestClient === 'function') {
    return fetchShortInterestClient(ticker);
  }
  return null;
}

async function fetchOutperformance(ticker) {
  // Try backend first
  if (await checkBackend()) {
    try {
      const resp = await fetch(`${BACKEND_URL}/sp500-outperformance?symbol=${ticker}`, { signal: AbortSignal.timeout(120000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.error && data.data && data.data.length > 0) return data;
    } catch (e) {
      console.warn('Outperformance backend failed:', e);
    }
  }
  // Client-side fallback: Supabase → snapshot
  if (typeof fetchOutperformanceClient === 'function') {
    const result = await fetchOutperformanceClient(ticker);
    if (result) return result;
  }
  // Last resort: client-side calculation
  return computeClientSideOutperformance(ticker);
}

// --- Client-side fallback for quant factors ---
// Uses available data from tickerData (chart-based performance metrics)
function computeClientSideQuant(ticker) {
  const d = tickerData[ticker] || {};
  const sub = getSubsector(ticker);
  // Use all watchlist tickers as the peer universe
  const allPeers = tickerList.filter(t => tickerData[t]);
  const sectorPeers = tickerList.filter(t => getSubsector(t) === sub && tickerData[t]);
  const peers = sectorPeers.length >= 3 ? sectorPeers : allPeers;
  
  if (peers.length < 2) return null;

  function percentileRank(values, myVal) {
    if (myVal == null) return null;
    const valid = values.filter(v => v != null);
    if (valid.length < 2) return null;
    const below = valid.filter(v => v < myVal).length;
    return Math.round((below / valid.length) * 100);
  }

  const factors = {};

  // 1. Momentum (1Y return)
  const momVals = peers.map(t => tickerData[t]?.y1);
  factors['Momentum'] = {
    score: percentileRank(momVals, d.y1),
    value: d.y1,
    label: d.y1 != null ? `${d.y1 >= 0 ? '+' : ''}${d.y1.toFixed(1)}% 1Y` : '—',
  };

  // 2. Value (EV/Sales inverse — lower multiple = higher value score)
  const valVals = peers.map(t => {
    const es = tickerData[t]?.evSales;
    return (es && es > 0) ? (1 / es) : null;
  });
  const myVal = (d.evSales && d.evSales > 0) ? (1 / d.evSales) : null;
  factors['Value'] = {
    score: percentileRank(valVals, myVal),
    value: d.evSales,
    label: d.evSales != null ? `${d.evSales.toFixed(1)}x EV/Sales` : '—',
  };

  // 3. Quality (EV/FCF inverse — lower = higher quality cash generation)
  const qualVals = peers.map(t => {
    const ef = tickerData[t]?.evFcf;
    return (ef && ef > 0 && ef < 500) ? (1 / ef) : null;
  });
  const myQual = (d.evFcf && d.evFcf > 0 && d.evFcf < 500) ? (1 / d.evFcf) : null;
  factors['Quality'] = {
    score: percentileRank(qualVals, myQual),
    value: d.evFcf,
    label: d.evFcf != null ? `${d.evFcf.toFixed(1)}x EV/FCF` : '—',
  };

  // 4. Growth (3M momentum as proxy for growth acceleration)
  const growthVals = peers.map(t => tickerData[t]?.m3);
  factors['Growth'] = {
    score: percentileRank(growthVals, d.m3),
    value: d.m3,
    label: d.m3 != null ? `${d.m3 >= 0 ? '+' : ''}${d.m3.toFixed(1)}% 3M` : '—',
  };

  // 5. Volatility (YTD absolute return inverse — lower vol = higher score)
  // Use difference between 1M and 3M as a crude vol proxy
  const volVals = peers.map(t => {
    const pd = tickerData[t];
    if (pd?.m1 != null && pd?.m3 != null) {
      // Negative of absolute spread = less volatile gets higher rank
      return -Math.abs(pd.m1 - pd.m3 / 3);
    }
    return null;
  });
  const myVol = (d.m1 != null && d.m3 != null) ? -Math.abs(d.m1 - d.m3 / 3) : null;
  factors['Volatility'] = {
    score: percentileRank(volVals, myVol),
    value: d.beta,
    label: d.beta != null ? `${d.beta.toFixed(2)} Beta` : (d.m1 != null ? 'From price data' : '—'),
  };

  // 6. Profitability (Market cap / EV ratio — higher means less debt = more profitable proxy)
  const profVals = peers.map(t => {
    const pd = tickerData[t];
    return (pd?.marketCap && pd?.ev && pd.ev > 0) ? pd.marketCap / pd.ev : null;
  });
  const myProf = (d.marketCap && d.ev && d.ev > 0) ? d.marketCap / d.ev : null;
  factors['Profitability'] = {
    score: percentileRank(profVals, myProf),
    value: myProf,
    label: myProf != null ? `${(myProf * 100).toFixed(0)}% MCap/EV` : '—',
  };

  // 7. Earnings Revision (1W performance as proxy — recent momentum reflects revisions)
  const revVals = peers.map(t => tickerData[t]?.w1);
  factors['Earnings Revision'] = {
    score: percentileRank(revVals, d.w1),
    value: d.w1,
    label: d.w1 != null ? `${d.w1 >= 0 ? '+' : ''}${d.w1.toFixed(1)}% 1W` : '—',
  };

  return {
    ticker,
    sector: sub + (sectorPeers.length >= 3 ? '' : ' (all watchlist)'),
    factors,
    peerCount: peers.length,
  };
}

// --- Client-side S&P 500 outperformance approximation ---
async function computeClientSideOutperformance(ticker) {
  // Fetch 4Y daily data for the ticker and S&P 500
  // Then compute rolling 1Y return difference and approximate percentile
  try {
    const [tickerChart, spChart] = await Promise.all([
      fetchChartData(ticker, '5y', '1wk'),
      fetchChartData('^GSPC', '5y', '1wk'),
    ]);

    if (!tickerChart || !spChart) return null;

    const tickerCloses = tickerChart.closes;
    const tickerTs = tickerChart.timestamps;
    const spCloses = spChart.closes;
    const spTs = spChart.timestamps;

    // Build date-aligned map
    const spMap = {};
    spTs.forEach((t, i) => {
      const d = new Date(t * 1000).toISOString().split('T')[0];
      spMap[d] = spCloses[i];
    });

    // For each weekly point (after first 52 weeks), compute rolling return vs S&P
    const data = [];
    const lookback = 52; // ~1 year in weeks

    for (let i = lookback; i < tickerCloses.length; i++) {
      if (!tickerCloses[i] || !tickerCloses[i - lookback]) continue;
      const tickerReturn = (tickerCloses[i] - tickerCloses[i - lookback]) / tickerCloses[i - lookback];
      
      const dateStr = new Date(tickerTs[i] * 1000).toISOString().split('T')[0];
      
      // Find matching S&P data
      const spDateKey = Object.keys(spMap).reduce((closest, d) => {
        if (!closest) return d;
        return Math.abs(new Date(d) - new Date(dateStr)) < Math.abs(new Date(closest) - new Date(dateStr)) ? d : closest;
      }, null);

      const spIdx = spTs.findIndex(t => {
        const d = new Date(t * 1000).toISOString().split('T')[0];
        return d === spDateKey;
      });
      
      if (spIdx < lookback || !spCloses[spIdx] || !spCloses[spIdx - lookback]) continue;
      const spReturn = (spCloses[spIdx] - spCloses[spIdx - lookback]) / spCloses[spIdx - lookback];

      // Approximate percentile based on how much we beat the index
      // Using a heuristic: excess return maps to percentile via normal distribution approximation
      // Typical stock dispersion around S&P is ~20% std dev
      const excessReturn = tickerReturn - spReturn;
      const stdDev = 0.25; // approximate annual cross-sectional std dev of S&P 500 stock returns
      const zScore = excessReturn / stdDev;
      // Convert z-score to percentile using approximation
      const percentile = Math.min(99, Math.max(1, Math.round(normCDF(zScore) * 100)));

      data.push({ date: dateStr, percentile });
    }

    // Filter to last 3 years
    const threeYearsAgo = new Date();
    threeYearsAgo.setFullYear(threeYearsAgo.getFullYear() - 3);
    const filtered = data.filter(d => new Date(d.date) >= threeYearsAgo);

    return { ticker, data: filtered, approximate: true };
  } catch (e) {
    console.error('Client-side outperformance calc failed:', e);
    return null;
  }
}

// Normal CDF approximation
function normCDF(z) {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = z < 0 ? -1 : 1;
  z = Math.abs(z) / Math.sqrt(2);
  const t = 1.0 / (1.0 + p * z);
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1.0 + sign * y);
}

// --- Render deep dive content ---

function renderDeepDiveContent(ticker, quant, short_, outperf, data, comps) {
  const contentEl = document.getElementById('deep-dive-content');
  let html = '';

  // Section A: Quant Factor Scorecard
  html += renderQuantFactors(ticker, quant);

  // Section B: Short Interest
  html += renderShortInterest(ticker, short_);

  // Section C: S&P 500 Outperformance
  html += renderOutperformance(ticker, outperf);

  // Section D: Cross-Sector Fundamental Comps
  html += renderCrossSectorComps(ticker, comps);

  contentEl.innerHTML = html;

  // Initialize charts after DOM is ready
  setTimeout(() => {
    if (short_ && short_.current) initShortInterestChart(short_);
    if (outperf && outperf.data && outperf.data.length > 0) initOutperformanceChart(outperf);
  }, 50);
}

// --- Section A: Quant Factors ---

function getFactorBarColor(score) {
  if (score == null) return 'var(--text-muted)';
  if (score >= 75) return '#22c55e';
  if (score >= 50) return '#3b82f6';
  if (score >= 25) return '#f59e0b';
  return '#ef4444';
}

function getFactorGrade(score) {
  if (score == null) return '—';
  if (score >= 80) return 'A';
  if (score >= 60) return 'B';
  if (score >= 40) return 'C';
  if (score >= 20) return 'D';
  return 'F';
}

function renderQuantFactors(ticker, quant) {
  if (!quant || !quant.factors) {
    return `<div class="deep-dive-section">
      <div class="popup-section-title">Quant Factor Scorecard</div>
      <div style="color:var(--text-muted);padding:16px 0;font-size:12px;">
        Quant factor data unavailable. Run the backend server for full analysis.
      </div>
    </div>`;
  }

  const factorOrder = ['Momentum', 'Value', 'Quality', 'Growth', 'Volatility', 'Profitability', 'Earnings Revision'];

  // Compute composite score (average of available factors)
  const scores = factorOrder.map(f => quant.factors[f]?.score).filter(s => s != null);
  const composite = scores.length > 0 ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null;

  let html = `<div class="deep-dive-section">
    <div class="popup-section-title">Quant Factor Scorecard</div>
    <div class="quant-subtitle">vs. ${quant.sector || 'sector'} peers (${quant.peerCount} stocks)</div>
    <div class="quant-composite">
      <span class="composite-label">Composite</span>
      <span class="composite-score" style="color:${getFactorBarColor(composite)}">${composite != null ? composite : '—'}</span>
    </div>
    <div class="quant-factors-grid">`;

  factorOrder.forEach(factorName => {
    const f = quant.factors[factorName];
    const score = f?.score;
    const label = f?.label || '—';
    const barColor = getFactorBarColor(score);
    const width = score != null ? score : 0;

    html += `
      <div class="factor-row">
        <div class="factor-name">${factorName}</div>
        <div class="factor-bar-wrapper">
          <div class="factor-bar-track">
            <div class="factor-bar-fill" style="width:${width}%;background:${barColor};"></div>
          </div>
          <span class="factor-score" style="color:${barColor}">${score != null ? score : '—'}</span>
        </div>
        <div class="factor-label">${label}</div>
      </div>`;
  });

  html += '</div></div>';
  return html;
}

// --- Section B: Short Interest ---

function renderShortInterest(ticker, short_) {
  if (!short_ || !short_.current) {
    return `<div class="deep-dive-section">
      <div class="popup-section-title">Short Interest</div>
      <div style="color:var(--text-muted);padding:16px 0;font-size:12px;">
        Short interest data unavailable. Run the backend server for this data.
      </div>
    </div>`;
  }

  const cur = short_.current;
  const prior = short_.priorMonth || {};
  const siPctFloat = cur.shortPercentOfFloat;
  const shortRatio = cur.shortRatio;
  const changePct = short_.change;
  const curShares = cur.sharesShort;
  const priorShares = prior.sharesShort;

  let html = `<div class="deep-dive-section">
    <div class="popup-section-title">Short Interest</div>
    <div class="short-interest-metrics">
      <div class="si-big-number">
        <div class="si-value">${siPctFloat != null ? siPctFloat.toFixed(2) + '%' : '—'}</div>
        <div class="si-label">% of Float Shorted</div>
      </div>
      <div class="si-cards">
        <div class="metric-card">
          <div class="metric-label">Short Ratio (Days to Cover)</div>
          <div class="metric-value">${shortRatio != null ? shortRatio.toFixed(1) : '—'}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Shares Short</div>
          <div class="metric-value">${curShares ? formatLargeNumber(curShares).replace('$', '') : '—'}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">MoM Change</div>
          <div class="metric-value ${percentClass(changePct)}">${changePct != null ? (changePct >= 0 ? '+' : '') + changePct.toFixed(1) + '%' : '—'}</div>
        </div>
      </div>
    </div>
    <div class="short-interest-chart-area" id="short-interest-chart-area">
      <canvas id="short-interest-canvas" height="180"></canvas>
    </div>
    <div class="si-context">
      ${generateShortInterestNarrative(siPctFloat, shortRatio, changePct)}
    </div>
  </div>`;

  return html;
}

function generateShortInterestNarrative(pctFloat, ratio, changePct) {
  const parts = [];
  if (pctFloat != null) {
    if (pctFloat > 10) parts.push(`Heavily shorted at ${pctFloat.toFixed(1)}% of float — elevated short interest suggests significant bearish positioning.`);
    else if (pctFloat > 5) parts.push(`Moderately shorted at ${pctFloat.toFixed(1)}% of float — above-average short interest.`);
    else if (pctFloat > 2) parts.push(`Short interest of ${pctFloat.toFixed(1)}% of float is within normal range for this type of stock.`);
    else parts.push(`Light short interest at ${pctFloat.toFixed(1)}% of float.`);
  }
  if (ratio != null) {
    if (ratio > 5) parts.push(`Days to cover of ${ratio.toFixed(1)} is elevated, suggesting potential squeeze dynamics.`);
    else if (ratio > 3) parts.push(`Days to cover of ${ratio.toFixed(1)} is moderate.`);
  }
  if (changePct != null) {
    if (changePct > 10) parts.push(`Short interest increased ${changePct.toFixed(0)}% month-over-month — bears are adding.`);
    else if (changePct < -10) parts.push(`Short interest decreased ${Math.abs(changePct).toFixed(0)}% month-over-month — shorts are covering.`);
  }
  return `<div class="narrative-text">${parts.join(' ') || 'Insufficient short interest data for commentary.'}</div>`;
}

function initShortInterestChart(short_) {
  const canvas = document.getElementById('short-interest-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const cur = short_.current || {};
  const prior = short_.priorMonth || {};
  const curShares = cur.sharesShort || 0;
  const priorShares = prior.sharesShort || 0;

  const curDate = cur.date ? new Date(cur.date).toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : 'Current';
  const priorDate = prior.date ? new Date(prior.date).toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : 'Prior Month';

  shortInterestChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: [priorDate, curDate],
      datasets: [{
        label: 'Shares Short',
        data: [priorShares, curShares],
        backgroundColor: [
          'rgba(59,130,246,0.4)',
          curShares > priorShares ? 'rgba(239,68,68,0.5)' : 'rgba(34,197,94,0.5)',
        ],
        borderColor: [
          'rgba(59,130,246,0.8)',
          curShares > priorShares ? 'rgba(239,68,68,0.8)' : 'rgba(34,197,94,0.8)',
        ],
        borderWidth: 1,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1f2e',
          titleColor: '#e5e7eb',
          bodyColor: '#9ca3af',
          borderColor: '#374151',
          borderWidth: 1,
          bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
          callbacks: {
            label: (ctx) => {
              const val = ctx.parsed.y;
              return (val / 1e6).toFixed(2) + 'M shares';
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: '#6b7280', font: { family: "'JetBrains Mono', monospace", size: 10 } },
        },
        y: {
          grid: { color: 'rgba(30,41,59,0.5)', drawBorder: false },
          ticks: {
            color: '#6b7280',
            font: { family: "'JetBrains Mono', monospace", size: 10 },
            callback: (v) => (v / 1e6).toFixed(1) + 'M',
          },
        },
      },
    },
  });
}

// --- Section C: S&P 500 Outperformance ---

function renderOutperformance(ticker, outperf) {
  if (!outperf || !outperf.data || outperf.data.length === 0) {
    return `<div class="deep-dive-section">
      <div class="popup-section-title">S&P 500 Outperformance Percentile</div>
      <div style="color:var(--text-muted);padding:16px 0;font-size:12px;">
        Outperformance data unavailable. Run the backend server for full S&P 500 analysis.
        ${outperf?.approximate ? '<br>Client-side approximation had insufficient data.' : ''}
      </div>
    </div>`;
  }

  const latest = outperf.data[outperf.data.length - 1];
  const currentPctile = latest ? latest.percentile : null;

  let html = `<div class="deep-dive-section">
    <div class="popup-section-title">S&P 500 Outperformance Percentile</div>
    <div class="outperf-header">
      <div class="outperf-big-number">
        <div class="outperf-value" style="color:${getFactorBarColor(currentPctile)}">${currentPctile != null ? Math.round(currentPctile) : '—'}<span class="outperf-unit">%ile</span></div>
        <div class="outperf-label">Rolling 1Y return vs S&P 500 constituents</div>
        ${outperf.approximate ? '<div class="outperf-approx">Approximated from index-relative performance</div>' : ''}
      </div>
    </div>
    <div class="outperf-chart-container" id="outperf-chart-area">
      <canvas id="outperf-canvas" style="width:100%;height:280px;"></canvas>
    </div>
  </div>`;

  return html;
}

function initOutperformanceChart(outperf) {
  const canvas = document.getElementById('outperf-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const labels = outperf.data.map(d => d.date);
  const values = outperf.data.map(d => d.percentile);

  // Create gradient
  const gradient = ctx.createLinearGradient(0, 0, 0, 280);
  gradient.addColorStop(0, 'rgba(34,197,94,0.15)');
  gradient.addColorStop(0.5, 'rgba(59,130,246,0.05)');
  gradient.addColorStop(1, 'rgba(239,68,68,0.15)');

  outperformanceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Outperformance %ile',
        data: values,
        borderColor: '#3b82f6',
        backgroundColor: gradient,
        fill: true,
        borderWidth: 2,
        pointRadius: 0,
        pointHitRadius: 6,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1f2e',
          titleColor: '#e5e7eb',
          bodyColor: '#9ca3af',
          borderColor: '#374151',
          borderWidth: 1,
          titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
          bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
          callbacks: {
            label: (ctx) => `Outperformed ${ctx.parsed.y.toFixed(0)}% of S&P 500`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(30,41,59,0.5)', drawBorder: false },
          ticks: {
            color: '#6b7280',
            font: { family: "'JetBrains Mono', monospace", size: 9 },
            maxTicksLimit: 8,
            maxRotation: 0,
          },
        },
        y: {
          min: 0,
          max: 100,
          grid: { color: 'rgba(30,41,59,0.5)', drawBorder: false },
          ticks: {
            color: '#6b7280',
            font: { family: "'JetBrains Mono', monospace", size: 10 },
            stepSize: 25,
            callback: (v) => v + '%',
          },
        },
      },
    },
    plugins: [{
      // Draw horizontal reference lines at 25%, 50%, 75%
      id: 'referenceLines',
      beforeDraw: (chart) => {
        const ctx = chart.ctx;
        const yAxis = chart.scales.y;
        const xAxis = chart.scales.x;
        
        [25, 50, 75].forEach(val => {
          const y = yAxis.getPixelForValue(val);
          ctx.save();
          ctx.beginPath();
          ctx.setLineDash([4, 4]);
          ctx.strokeStyle = val === 50 ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.07)';
          ctx.lineWidth = 1;
          ctx.moveTo(xAxis.left, y);
          ctx.lineTo(xAxis.right, y);
          ctx.stroke();
          ctx.restore();

          // Label
          if (val === 50) {
            ctx.save();
            ctx.fillStyle = 'rgba(255,255,255,0.25)';
            ctx.font = "9px 'JetBrains Mono', monospace";
            ctx.fillText('Median', xAxis.right - 40, y - 4);
            ctx.restore();
          }
        });
      },
    }],
  });
}

// --- Section D: Cross-Sector Fundamental Comps ---

async function fetchCrossSectorComps(ticker) {
  if (await checkBackend()) {
    try {
      const resp = await fetch(`${BACKEND_URL}/cross-sector-comps?symbol=${ticker}`, { signal: AbortSignal.timeout(120000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.error) return data;
    } catch (e) {
      console.warn('Cross-sector comps backend failed:', e);
    }
  }
  // Client-side fallback: Supabase → snapshot
  if (typeof fetchCrossSectorCompsClient === 'function') {
    return fetchCrossSectorCompsClient(ticker);
  }
  return null;
}

function renderCrossSectorComps(ticker, comps) {
  if (!comps || !comps.comps || comps.comps.length === 0) {
    return `<div class="deep-dive-section">
      <div class="popup-section-title">Cross-Sector Fundamental Comps</div>
      <div style="color:var(--text-muted);padding:16px 0;font-size:12px;">
        Cross-sector comparable data unavailable. Run the backend server for this analysis.
      </div>
    </div>`;
  }

  const target = comps.target;
  const peers = comps.comps;

  // Build the comp sheet table
  const allRows = [target, ...peers];

  function fmtMcap(v) {
    if (!v) return '\u2014';
    if (v >= 1e12) return '$' + (v / 1e12).toFixed(1) + 'T';
    if (v >= 1e9) return '$' + (v / 1e9).toFixed(0) + 'B';
    if (v >= 1e6) return '$' + (v / 1e6).toFixed(0) + 'M';
    return '$' + v.toFixed(0);
  }
  function fmtPE(v) { return v != null ? v.toFixed(1) + 'x' : '\u2014'; }
  function fmtMult(v) { return v != null ? v.toFixed(1) + 'x' : '\u2014'; }
  function fmtPct(v) { return v != null ? v.toFixed(1) + '%' : '\u2014'; }
  function fmtBeta(v) { return v != null ? v.toFixed(2) : '\u2014'; }
  function fmtSim(v) { return v != null ? Math.round(v * 100) + '%' : '\u2014'; }

  function simBarColor(v) {
    if (v == null) return 'var(--text-muted)';
    const pct = v * 100;
    if (pct >= 70) return '#22c55e';
    if (pct >= 50) return '#3b82f6';
    if (pct >= 30) return '#f59e0b';
    return '#ef4444';
  }

  function shortName(row) {
    const cname = typeof getCommonName === 'function' ? getCommonName(row.ticker, row.name) : row.name;
    if (cname && cname.length > 22) return cname.slice(0, 20) + '\u2026';
    return cname || row.ticker;
  }

  function shortSector(sector) {
    if (!sector) return '\u2014';
    const map = {
      'Technology': 'Tech', 'Financial Services': 'Financials',
      'Healthcare': 'Healthcare', 'Communication Services': 'Comm Svcs',
      'Consumer Cyclical': 'Consumer', 'Consumer Defensive': 'Staples',
      'Industrials': 'Industrials', 'Energy': 'Energy',
      'Real Estate': 'Real Estate', 'Basic Materials': 'Materials',
      'Utilities': 'Utilities',
    };
    return map[sector] || sector;
  }

  let html = `<div class="deep-dive-section">
    <div class="popup-section-title">Cross-Sector Fundamental Comps</div>
    <div class="comps-subtitle">Companies with similar fundamental profiles outside ${shortSector(comps.sector)}</div>
    <div class="comps-table-wrapper">
      <table class="comps-table">
        <thead><tr>
          <th class="comps-sticky-col">Company</th>
          <th>Sector</th>
          <th class="num">Mkt Cap</th>
          <th class="num">Fwd P/E</th>
          <th class="num">EV/Rev</th>
          <th class="num">EV/EBITDA</th>
          <th class="num">Op Margin</th>
          <th class="num">Rev Growth</th>
          <th class="num">FCF Margin</th>
          <th class="num">Beta</th>
          <th class="num">Match</th>
        </tr></thead>
        <tbody>`;

  allRows.forEach((row, idx) => {
    const isTarget = idx === 0;
    const rowClass = isTarget ? 'comps-target-row' : '';
    const simColor = simBarColor(row.similarity);

    html += `<tr class="${rowClass}">
      <td class="comps-sticky-col">
        <span class="comps-ticker">${row.ticker}</span>
        <span class="comps-name">${shortName(row)}</span>
      </td>
      <td class="comps-sector-cell">${shortSector(row.sector)}</td>
      <td class="num">${fmtMcap(row.marketCap)}</td>
      <td class="num">${fmtPE(row.forwardPE)}</td>
      <td class="num">${fmtMult(row.enterpriseToRevenue)}</td>
      <td class="num">${fmtMult(row.enterpriseToEbitda)}</td>
      <td class="num">${fmtPct(row.operatingMargins)}</td>
      <td class="num ${row.revenueGrowth != null ? (row.revenueGrowth >= 0 ? 'val-pos' : 'val-neg') : ''}">${fmtPct(row.revenueGrowth)}</td>
      <td class="num">${fmtPct(row.fcfMargin)}</td>
      <td class="num">${fmtBeta(row.beta)}</td>
      <td class="num">${isTarget ? '<span class="comps-target-badge">TARGET</span>' : `<span class="comps-sim-badge" style="background:${simColor}20;color:${simColor};border:1px solid ${simColor}40;">${fmtSim(row.similarity)}</span>`}</td>
    </tr>`;
  });

  html += `</tbody></table></div>`;

  // Narrative explanation
  html += `<div class="comps-narrative">${generateCompsNarrative(ticker, target, peers)}</div>`;
  html += '</div>';

  return html;
}

function generateCompsNarrative(ticker, target, peers) {
  if (!peers || peers.length === 0) return '';
  const parts = [];

  const best = peers[0];
  const bestName = typeof getCommonName === 'function' ? getCommonName(best.ticker, best.name) : best.name;
  parts.push(`The closest cross-sector fundamental match is <strong>${bestName}</strong> (${best.sector || 'N/A'})`);

  const sharedMetrics = [];
  if (target.forwardPE && best.forwardPE) {
    sharedMetrics.push(`forward P/E of ${best.forwardPE}x vs ${target.forwardPE}x`);
  }
  if (target.operatingMargins != null && best.operatingMargins != null) {
    sharedMetrics.push(`operating margin of ${best.operatingMargins}% vs ${target.operatingMargins}%`);
  }
  if (sharedMetrics.length > 0) {
    parts[0] += `, with ${sharedMetrics.join(' and ')}.`;
  } else {
    parts[0] += '.';
  }

  const uniqueSectors = [...new Set(peers.map(p => p.sector).filter(Boolean))];
  if (uniqueSectors.length > 1) {
    parts.push(`Comparables span ${uniqueSectors.length} sectors: ${uniqueSectors.join(', ')}.`);
  }

  const peerPEs = peers.filter(p => p.forwardPE).map(p => p.forwardPE);
  if (target.forwardPE && peerPEs.length >= 2) {
    const avgPE = peerPEs.reduce((a, b) => a + b, 0) / peerPEs.length;
    const diff = ((target.forwardPE - avgPE) / avgPE) * 100;
    if (Math.abs(diff) > 15) {
      parts.push(`${ticker} trades at a ${diff > 0 ? 'premium' : 'discount'} of ${Math.abs(diff).toFixed(0)}% on forward P/E relative to these fundamental peers.`);
    }
  }

  return `<div class="narrative-text">${parts.join(' ')}</div>`;
}
