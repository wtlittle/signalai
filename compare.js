/* ===== COMPARE.JS — Cross-company comparison popup =====
 *
 * Entry UX:
 *   - A "Compare" toggle button sits in the watchlist header.
 *   - When compare-mode is on, a checkbox column is injected into every
 *     public row. Selecting 2-4 tickers shows a sticky tray with a CTA
 *     that opens the comparison popup.
 *
 * Popup structure (tab order is exact):
 *   Overview | AI Diff | Scorecard | Radar
 */
(function (global) {
  'use strict';

  const MIN_PICK = 2;
  const MAX_PICK = 4;

  const state = {
    mode: false,
    selected: new Set(),          // tickers chosen in current session
    popupOpen: false,
    popupTicker: null,            // [t1,t2,...] while popup open
    activeTab: 'overview',
    chart: null,
    radarChart: null,
    chartRange: '3M',
    benchmarkMode: 'none',        // none | sector_median | etf_proxy | equal_weight | all
    priceCache: {},               // { TICKER_RANGE: { dates, closes } }
  };

  // ======================= COMPARE MODE TOGGLE =======================
  function toggleMode() {
    state.mode = !state.mode;
    state.selected.clear();
    renderMode();
    updateTray();
    if (typeof global.renderTable === 'function') {
      // Re-render to add/remove checkbox column.
      global.renderTable();
    }
  }

  function renderMode() {
    const btn = document.getElementById('compare-toggle-btn');
    if (!btn) return;
    btn.classList.toggle('compare-active', state.mode);
    btn.textContent = state.mode ? 'Exit Compare' : 'Compare';
  }

  function isModeOn() { return state.mode; }
  function isSelected(t) { return state.selected.has(t); }
  function toggleTicker(t) {
    if (state.selected.has(t)) {
      state.selected.delete(t);
    } else if (state.selected.size < MAX_PICK) {
      state.selected.add(t);
    } else {
      // Max reached; brief flash in tray
      const tray = document.getElementById('compare-tray');
      if (tray) {
        tray.classList.add('shake');
        setTimeout(() => tray.classList.remove('shake'), 320);
      }
      return;
    }
    updateTray();
    // Update row checkbox visual without full re-render.
    const cb = document.querySelector('.compare-checkbox[data-ticker="' + t + '"]');
    if (cb) cb.checked = state.selected.has(t);
  }

  // ======================= TRAY =======================
  function ensureTray() {
    let tray = document.getElementById('compare-tray');
    if (tray) return tray;
    tray = document.createElement('div');
    tray.id = 'compare-tray';
    tray.className = 'compare-tray';
    tray.innerHTML = `
      <div class="compare-tray-inner">
        <div class="compare-tray-left">
          <span class="compare-tray-label">Compare</span>
          <div class="compare-tray-chips" id="compare-tray-chips"></div>
          <span class="compare-tray-hint" id="compare-tray-hint">Pick 2–4 tickers</span>
        </div>
        <div class="compare-tray-right">
          <button type="button" class="btn-sm btn-ghost" id="compare-tray-clear">Clear</button>
          <button type="button" class="btn-sm btn-primary" id="compare-tray-go" disabled>Compare \u2192</button>
        </div>
      </div>`;
    document.body.appendChild(tray);
    tray.querySelector('#compare-tray-clear').addEventListener('click', () => {
      state.selected.clear();
      updateTray();
      document.querySelectorAll('.compare-checkbox').forEach(cb => { cb.checked = false; });
    });
    tray.querySelector('#compare-tray-go').addEventListener('click', () => {
      if (state.selected.size < MIN_PICK) return;
      openPopup(Array.from(state.selected));
    });
    return tray;
  }

  function updateTray() {
    const tray = ensureTray();
    const chips = tray.querySelector('#compare-tray-chips');
    const hint = tray.querySelector('#compare-tray-hint');
    const go = tray.querySelector('#compare-tray-go');
    const visible = state.mode && state.selected.size > 0;
    const alwaysWhileMode = state.mode; // show tray whenever compare mode is on
    tray.classList.toggle('visible', !!alwaysWhileMode);
    chips.innerHTML = Array.from(state.selected).map(t =>
      `<span class="compare-chip">${t}<button class="compare-chip-x" data-ticker="${t}" title="Remove">&times;</button></span>`
    ).join('');
    chips.querySelectorAll('.compare-chip-x').forEach(b => {
      b.addEventListener('click', () => toggleTicker(b.dataset.ticker));
    });
    const n = state.selected.size;
    if (n === 0) hint.textContent = 'Pick 2\u20134 tickers';
    else if (n < MIN_PICK) hint.textContent = `Pick ${MIN_PICK - n} more`;
    else hint.textContent = `${n} selected`;
    go.disabled = n < MIN_PICK;
  }

  // ======================= ROW CHECKBOX RENDER HELPER =======================
  /**
   * Called by app.js row renderer. Returns HTML for the leading compare cell,
   * or empty string if compare mode is off.
   */
  function rowCheckboxHtml(ticker) {
    if (!state.mode) return '';
    const checked = state.selected.has(ticker) ? 'checked' : '';
    return `<td class="col-compare"><input type="checkbox" class="compare-checkbox" data-ticker="${ticker}" ${checked}></td>`;
  }

  function headerCheckboxHtml() {
    if (!state.mode) return '';
    return '<th class="col-compare"></th>';
  }

  function wireRowCheckboxes(root) {
    if (!state.mode) return;
    (root || document).querySelectorAll('.compare-checkbox').forEach(cb => {
      cb.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleTicker(cb.dataset.ticker);
      });
    });
  }

  // ======================= POPUP SHELL =======================
  function openPopup(tickers) {
    state.popupTicker = tickers.slice();
    state.popupOpen = true;
    state.activeTab = 'overview';
    state.chartRange = '3M';
    state.benchmarkMode = 'none';
    ensureOverlay();
    document.body.style.overflow = 'hidden';
    const overlay = document.getElementById('compare-popup-overlay');
    overlay.classList.add('active');
    renderPopup();
  }

  function closePopup() {
    state.popupOpen = false;
    state.popupTicker = null;
    document.body.style.overflow = '';
    const overlay = document.getElementById('compare-popup-overlay');
    if (overlay) overlay.classList.remove('active');
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    if (state.radarChart) { state.radarChart.destroy(); state.radarChart = null; }
  }

  function ensureOverlay() {
    let overlay = document.getElementById('compare-popup-overlay');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'popup-overlay compare-popup-overlay';
    overlay.id = 'compare-popup-overlay';
    overlay.innerHTML = `
      <div class="popup-modal compare-modal" id="compare-modal">
        <button class="popup-close" id="compare-close">&times;</button>
        <div class="compare-header">
          <h2 id="compare-title">Compare</h2>
          <div class="compare-subtitle" id="compare-subtitle"></div>
        </div>
        <div class="compare-tabs" role="tablist">
          <button class="compare-tab active" data-tab="overview">Overview</button>
          <button class="compare-tab" data-tab="ai-diff">AI Diff</button>
          <button class="compare-tab" data-tab="scorecard">Scorecard</button>
          <button class="compare-tab" data-tab="radar">Radar</button>
        </div>
        <div class="compare-body" id="compare-body"></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#compare-close').addEventListener('click', closePopup);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closePopup(); });
    overlay.querySelectorAll('.compare-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        overlay.querySelectorAll('.compare-tab').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        state.activeTab = btn.dataset.tab;
        renderActiveTab();
      });
    });
    return overlay;
  }

  function renderPopup() {
    const tickers = state.popupTicker || [];
    document.getElementById('compare-title').textContent = tickers.join(' vs ');
    const names = tickers.map(t => {
      const d = (global.tickerData || {})[t] || {};
      const name = (typeof global.getCommonName === 'function') ? global.getCommonName(t, d.name) : (d.name || t);
      return name;
    }).join('  \u00b7  ');
    document.getElementById('compare-subtitle').textContent = names;
    renderActiveTab();
  }

  function renderActiveTab() {
    const body = document.getElementById('compare-body');
    if (!body) return;
    if (state.activeTab === 'overview') renderOverview(body);
    else if (state.activeTab === 'ai-diff') renderAiDiff(body);
    else if (state.activeTab === 'scorecard') renderScorecard(body);
    else if (state.activeTab === 'radar') renderRadar(body);
  }

  // ======================= OVERVIEW TAB =======================
  function renderOverview(body) {
    const tickers = state.popupTicker || [];
    body.innerHTML = `
      <div class="cmp-section cmp-chart-section">
        <div class="cmp-section-head">
          <h3>Indexed Return <span class="cmp-subtle">(rebased to 100)</span></h3>
          <div class="cmp-chart-controls">
            <div class="cmp-range-toggle" id="cmp-range-toggle">
              ${['1D','1W','1M','3M','YTD','1Y'].map(r =>
                `<button type="button" class="cmp-range-btn${state.chartRange===r?' active':''}" data-range="${r}">${r}</button>`).join('')}
            </div>
            <select class="cmp-benchmark-select" id="cmp-benchmark-select">
              <option value="none">No benchmark</option>
              <option value="sector_median">Subsector median</option>
              <option value="etf_proxy">ETF proxy</option>
              <option value="equal_weight">Equal-weight basket</option>
              <option value="all">All benchmarks</option>
            </select>
          </div>
        </div>
        <div class="cmp-chart-wrap">
          <canvas id="cmp-chart"></canvas>
          <div class="cmp-chart-loading" id="cmp-chart-loading">Loading price history\u2026</div>
        </div>
      </div>
      <div class="cmp-section cmp-fund-section">
        <div class="cmp-section-head"><h3>Fundamentals</h3></div>
        <div class="cmp-fund-wrap" id="cmp-fund-wrap"></div>
      </div>
    `;
    // wire range buttons
    body.querySelectorAll('.cmp-range-btn').forEach(b => {
      b.addEventListener('click', () => {
        state.chartRange = b.dataset.range;
        body.querySelectorAll('.cmp-range-btn').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        drawIndexedChart();
      });
    });
    body.querySelector('#cmp-benchmark-select').addEventListener('change', (e) => {
      state.benchmarkMode = e.target.value;
      drawIndexedChart();
    });
    // build the fundamentals table synchronously
    body.querySelector('#cmp-fund-wrap').innerHTML = buildFundamentalsTable(tickers);
    // draw chart (async)
    drawIndexedChart();
  }

  function buildFundamentalsTable(tickers) {
    const rowDefs = [
      { key: 'price',       label: 'Price',          fmt: (d) => fmt.price(d.price),        winner: null },
      { key: 'marketCap',   label: 'Market Cap',     fmt: (d) => fmt.large(d.marketCap),    winner: null },
      { key: 'ev',          label: 'EV',             fmt: (d) => fmt.large(d.ev),           winner: null },
      { key: 'evSales',     label: 'FY1 EV/Sales',   fmt: (d) => fmt.mult(d.evSales),       winner: 'low' },
      { key: 'evFcf',       label: 'FY1 EV/FCF',     fmt: (d) => fmt.mult(d.evFcf),         winner: 'low' },
      { key: 'ytd',         label: 'YTD',            fmt: (d) => fmt.pct(d.ytd),            winner: 'high' },
      { key: 'm1',          label: '1M',             fmt: (d) => fmt.pct(d.m1),             winner: 'high' },
      { key: 'm3',          label: '3M',             fmt: (d) => fmt.pct(d.m3),             winner: 'high' },
      { key: 'y1',          label: '1Y',             fmt: (d) => fmt.pct(d.y1),             winner: 'high' },
      { key: 'y3',          label: '3Y',             fmt: (d) => fmt.pct(d.y3),             winner: 'high' },
      { key: 'subsector',   label: 'Subsector',      fmt: (d, t) => (d.subsector || (typeof global.getSubsector === 'function' ? global.getSubsector(t) : '\u2014')), winner: null },
      { key: 'revGrowth',   label: 'Rev Growth',     fmt: (d) => fmt.pct(d.revenueGrowth),  winner: 'high' },
      { key: 'fcfMargin',   label: 'FCF Margin',     fmt: (d) => fmt.pct(d.fcfMargin),      winner: 'high' },
      { key: 'ruleOf40',    label: 'Rule of 40',     fmt: (d) => fmt.num(d.ruleOf40, 1),    winner: 'high' },
      { key: 'earningsMove',label: 'Last EPS reaction', fmt: (d) => fmt.pct(d.lastEarningsMove), winner: null },
    ];
    const rows = tickers.map(t => global.tickerData && global.tickerData[t] ? global.tickerData[t] : { ticker: t });
    // header
    let html = '<table class="cmp-fund-table"><thead><tr><th class="cmp-fund-metric">Metric</th>';
    tickers.forEach(t => { html += `<th>${t}</th>`; });
    html += '</tr></thead><tbody>';
    rowDefs.forEach(def => {
      html += `<tr><td class="cmp-fund-metric">${def.label}</td>`;
      // Compute winner index if numeric
      let winnerIdx = -1;
      if (def.winner) {
        const vals = rows.map((d) => {
          const v = d[def.key];
          return (typeof v === 'number' && !Number.isNaN(v)) ? v : null;
        });
        const validVals = vals.filter(v => v != null);
        if (validVals.length > 1) {
          const target = def.winner === 'high' ? Math.max.apply(null, validVals) : Math.min.apply(null, validVals);
          winnerIdx = vals.indexOf(target);
        }
      }
      rows.forEach((d, i) => {
        const cell = def.fmt(d, tickers[i]);
        const isWinner = i === winnerIdx;
        html += `<td class="${isWinner ? 'cmp-winner' : ''}">${cell}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
  }

  // ======================= CHART =======================
  function rangeToYahoo(range) {
    // Yahoo chart API range+interval mapping
    switch (range) {
      case '1D': return { range: '1d', interval: '5m' };
      case '1W': return { range: '5d', interval: '15m' };
      case '1M': return { range: '1mo', interval: '1d' };
      case '3M': return { range: '3mo', interval: '1d' };
      case 'YTD': return { range: 'ytd', interval: '1d' };
      case '1Y': return { range: '1y', interval: '1d' };
      default: return { range: '3mo', interval: '1d' };
    }
  }

  async function fetchPriceSeries(ticker, range) {
    const key = ticker + '_' + range;
    if (state.priceCache[key]) return state.priceCache[key];
    const { range: r, interval } = rangeToYahoo(range);
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=${r}&interval=${interval}`;
    try {
      let data = null;
      // Try direct first, then CORS proxies if configured in api-client.js
      try {
        const resp = await fetch(url, { signal: AbortSignal.timeout(8000) });
        if (resp.ok) data = await resp.json();
      } catch (e) { /* fall through */ }
      if (!data && typeof global.CORS_PROXIES !== 'undefined' && Array.isArray(global.CORS_PROXIES)) {
        for (const proxy of global.CORS_PROXIES) {
          try {
            const r2 = await fetch(proxy + encodeURIComponent(url), { signal: AbortSignal.timeout(8000) });
            if (r2.ok) { data = await r2.json(); break; }
          } catch (e) { /* try next */ }
        }
      }
      if (!data || !data.chart || !data.chart.result || !data.chart.result[0]) return null;
      const r0 = data.chart.result[0];
      const closes = (r0.indicators && r0.indicators.quote && r0.indicators.quote[0] && r0.indicators.quote[0].close) || [];
      const ts = r0.timestamp || [];
      // Drop nulls in lockstep
      const outDates = [], outCloses = [];
      for (let i = 0; i < ts.length; i++) {
        if (closes[i] == null) continue;
        outDates.push(new Date(ts[i] * 1000));
        outCloses.push(closes[i]);
      }
      const series = { dates: outDates, closes: outCloses };
      state.priceCache[key] = series;
      return series;
    } catch (e) {
      console.warn('Price fetch failed for', ticker, range, e);
      return null;
    }
  }

  function indexSeries(closes) {
    if (!closes || !closes.length) return [];
    const base = closes[0];
    if (!base) return closes.map(() => null);
    return closes.map(c => (c == null ? null : (c / base) * 100));
  }

  function benchmarkTickersForSubsector(subsector) {
    const map = {
      'Software - Infrastructure': 'IGV',
      'Software - Application': 'IGV',
      'Semiconductors': 'SOXX',
      'Internet': 'FDN',
      'Cloud Infrastructure': 'SKYY',
      'AI / ML': 'BOTZ',
      'Cybersecurity': 'HACK',
      'Fintech': 'FINX',
      'Biotech': 'IBB',
      'Pharma': 'XPH',
      'Banks': 'KBE',
      'Consumer Discretionary': 'XLY',
      'Consumer Staples': 'XLP',
      'Energy': 'XLE',
      'Healthcare': 'XLV',
      'Industrials': 'XLI',
      'Utilities': 'XLU',
      'Materials': 'XLB',
      'Real Estate': 'XLRE'
    };
    return map[subsector] || 'SPY';
  }

  async function drawIndexedChart() {
    const canvas = document.getElementById('cmp-chart');
    const loading = document.getElementById('cmp-chart-loading');
    if (!canvas) return;
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    if (loading) loading.style.display = 'flex';

    const tickers = state.popupTicker || [];
    const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ec4899'];

    // Fetch all series in parallel
    const seriesResults = await Promise.all(tickers.map(t => fetchPriceSeries(t, state.chartRange)));
    const datasets = [];
    let refDates = null;
    tickers.forEach((t, i) => {
      const s = seriesResults[i];
      if (!s || !s.closes.length) return;
      if (!refDates || s.dates.length > refDates.length) refDates = s.dates;
      datasets.push({
        label: t,
        data: s.dates.map((d, idx) => ({ x: d, y: indexSeries(s.closes)[idx] })),
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: 'transparent',
        borderWidth: 2,
        tension: 0.15,
        pointRadius: 0
      });
    });

    // Benchmarks
    const mode = state.benchmarkMode;
    const subsectors = tickers.map(t => {
      const d = (global.tickerData || {})[t] || {};
      return d.subsector || (typeof global.getSubsector === 'function' ? global.getSubsector(t) : null);
    });
    const primarySub = subsectors.find(s => !!s) || null;

    const addBenchmark = async (label, bmTicker, style) => {
      if (!bmTicker) return;
      const s = await fetchPriceSeries(bmTicker, state.chartRange);
      if (!s || !s.closes.length) return;
      datasets.push({
        label: label + ' (' + bmTicker + ')',
        data: s.dates.map((d, idx) => ({ x: d, y: indexSeries(s.closes)[idx] })),
        borderColor: style.color,
        backgroundColor: 'transparent',
        borderWidth: 1.2,
        borderDash: [4, 3],
        tension: 0.15,
        pointRadius: 0,
        order: 99
      });
    };

    if (mode === 'etf_proxy' || mode === 'all') {
      const etf = benchmarkTickersForSubsector(primarySub);
      await addBenchmark('ETF proxy', etf, { color: '#64748b' });
    }
    if (mode === 'sector_median' || mode === 'all') {
      // Approximate subsector median with SPY since a real median basket needs peer universe
      await addBenchmark('Subsector median (SPY fallback)', 'SPY', { color: '#94a3b8' });
    }
    if (mode === 'equal_weight' || mode === 'all') {
      // Equal-weight = average of the selected series themselves (net-zero vs their own mean)
      if (refDates && seriesResults.every(s => s && s.closes.length)) {
        const N = refDates.length;
        const indexed = seriesResults.map(s => indexSeries(s.closes));
        // Align to length min
        const minLen = Math.min.apply(null, indexed.map(a => a.length));
        const ewDates = refDates.slice(-minLen);
        const ew = [];
        for (let i = 0; i < minLen; i++) {
          let sum = 0, c = 0;
          indexed.forEach(arr => {
            const v = arr[arr.length - minLen + i];
            if (v != null) { sum += v; c++; }
          });
          ew.push(c ? sum / c : null);
        }
        datasets.push({
          label: 'Equal-weight basket',
          data: ewDates.map((d, i) => ({ x: d, y: ew[i] })),
          borderColor: '#e5e7eb',
          backgroundColor: 'transparent',
          borderWidth: 1.2,
          borderDash: [2, 4],
          tension: 0.15,
          pointRadius: 0,
          order: 100
        });
      }
    }

    if (loading) loading.style.display = 'none';
    if (!datasets.length) {
      if (loading) {
        loading.textContent = 'Price history unavailable. Try a different range or remove CORS blockers.';
        loading.style.display = 'flex';
      }
      return;
    }

    state.chart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'top',
            align: 'start',
            labels: { color: '#cbd5e1', boxWidth: 10, boxHeight: 2, usePointStyle: false, font: { size: 11 } }
          },
          tooltip: {
            backgroundColor: '#0f172a',
            borderColor: '#334155',
            borderWidth: 1,
            titleColor: '#e5e7eb',
            bodyColor: '#cbd5e1',
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y == null ? '\u2014' : ctx.parsed.y.toFixed(2)}`
            }
          }
        },
        scales: {
          x: { type: 'time', time: { tooltipFormat: 'MMM d, yyyy' }, ticks: { color: '#64748b', maxRotation: 0, font: { size: 10 } }, grid: { color: 'rgba(148,163,184,0.08)' } },
          y: { ticks: { color: '#64748b', font: { size: 10 }, callback: v => v.toFixed(0) }, grid: { color: 'rgba(148,163,184,0.08)' } }
        }
      }
    });
  }

  // ======================= AI DIFF TAB =======================
  function renderAiDiff(body) {
    const tickers = state.popupTicker || [];
    const rows = tickers.map(t => (global.tickerData || {})[t] || { ticker: t });

    // Heuristic thesis: compute a cheap framework from available metrics.
    function pick(keyFn, preference) {
      const vals = rows.map(r => keyFn(r));
      const withIdx = vals.map((v, i) => ({ v, i })).filter(x => typeof x.v === 'number' && !Number.isNaN(x.v));
      if (withIdx.length < 2) return null;
      withIdx.sort((a, b) => preference === 'low' ? (a.v - b.v) : (b.v - a.v));
      return { winner: tickers[withIdx[0].i], loser: tickers[withIdx[withIdx.length - 1].i], spread: Math.abs(withIdx[0].v - withIdx[withIdx.length - 1].v) };
    }

    const valWinner = pick(r => r.evSales, 'low');
    const growthWinner = pick(r => r.revenueGrowth, 'high');
    const momentumWinner = pick(r => r.y1, 'high');
    const qualityWinner = pick(r => r.fcfMargin, 'high');

    function line(label, pick, highFmt, lowFmt) {
      if (!pick) return `<li><strong>${label}:</strong> insufficient data across the peer set.</li>`;
      const tag = `<span class="cmp-winner-tag">${pick.winner}</span>`;
      return `<li><strong>${label}:</strong> ${tag} screens strongest; ${pick.loser} is the weakest on this axis.</li>`;
    }

    body.innerHTML = `
      <div class="cmp-aidiff">
        <div class="cmp-aidiff-block">
          <h3>What screens better right now</h3>
          <ul class="cmp-aidiff-list">
            ${line('Valuation (EV/Sales)', valWinner)}
            ${line('Growth (rev YoY)', growthWinner)}
            ${line('Momentum (1Y return)', momentumWinner)}
            ${line('Quality (FCF margin)', qualityWinner)}
          </ul>
        </div>
        <div class="cmp-aidiff-block">
          <h3>Where the market is giving credit</h3>
          <p class="cmp-aidiff-prose">${describeCredit(rows, tickers)}</p>
        </div>
        <div class="cmp-aidiff-block">
          <h3>The key debate</h3>
          <p class="cmp-aidiff-prose">${describeDebate(rows, tickers)}</p>
        </div>
        <div class="cmp-aidiff-block">
          <h3>Why one setup currently looks stronger</h3>
          <p class="cmp-aidiff-prose">${describeStrongerSetup(rows, tickers)}</p>
        </div>
        <div class="cmp-aidiff-block">
          <h3>What could invalidate this framing</h3>
          <ul class="cmp-aidiff-list cmp-aidiff-risks">
            <li>Forward growth disappointments versus currently-embedded expectations.</li>
            <li>Multiple compression in the group on a rate or risk shock.</li>
            <li>Margin reset from re-acceleration of AI / infrastructure spend.</li>
            <li>Idiosyncratic earnings miss on the stronger-screening name.</li>
          </ul>
        </div>
        <div class="cmp-aidiff-footer">
          Framework is mechanical; use the Scorecard tab for pillar-weighted detail.
        </div>
      </div>
    `;
  }

  function describeCredit(rows, tickers) {
    const byMult = rows.map((r, i) => ({ t: tickers[i], v: r.evSales })).filter(x => typeof x.v === 'number');
    if (byMult.length < 2) return 'The peer set has too little multiple data to draw a clear premium/discount read.';
    byMult.sort((a, b) => b.v - a.v);
    return `${byMult[0].t} carries the richest FY1 EV/Sales multiple in the set at ${byMult[0].v.toFixed(1)}x versus ${byMult[byMult.length-1].t} at ${byMult[byMult.length-1].v.toFixed(1)}x. That spread typically tracks forward growth or durability expectations embedded in consensus.`;
  }
  function describeDebate(rows, tickers) {
    return 'The debate across this group sits between durability of growth and valuation support. Names with stronger FCF conversion have more room to absorb a growth reset; richer-multiple names need the revenue line to keep compounding to justify the setup.';
  }
  function describeStrongerSetup(rows, tickers) {
    const scores = rows.map((r, i) => {
      const g = typeof r.revenueGrowth === 'number' ? r.revenueGrowth : 0;
      const q = typeof r.fcfMargin === 'number' ? r.fcfMargin : 0;
      const m = typeof r.y1 === 'number' ? r.y1 : 0;
      const v = typeof r.evSales === 'number' ? r.evSales : 20;
      return { t: tickers[i], s: g*0.3 + q*0.25 + m*0.1 - v*1.5 };
    });
    scores.sort((a, b) => b.s - a.s);
    return `On a mechanical blend of growth, profitability, momentum, and valuation (peer-relative), ${scores[0].t} screens as the strongest setup today. That is not a price target \u2014 it is a pillar-weighted read of where the fundamentals and multiple most favor a long.`;
  }

  // ======================= SCORECARD TAB =======================
  // Weights: Growth 30, Quality 25, Profitability 20, Valuation 15, Momentum 10
  const WEIGHTS = { valuation: 0.15, growth: 0.30, profitability: 0.20, momentum: 0.10, quality: 0.25 };

  function scoreOne(pillar, ticker, rows, tickers) {
    const r = rows[tickers.indexOf(ticker)];
    // Helper: peer-relative rank normalized to 1-5
    function rankScore(key, preference) {
      const vals = rows.map(x => x[key]).filter(v => typeof v === 'number' && !Number.isNaN(v));
      if (vals.length < 2) return { score: 3, reason: 'Insufficient peer data; defaulted to 3.' };
      const v = r[key];
      if (typeof v !== 'number' || Number.isNaN(v)) return { score: 2, reason: 'Metric unavailable.' };
      const sorted = vals.slice().sort((a, b) => preference === 'low' ? a - b : b - a);
      const rank = sorted.indexOf(v); // 0 = best
      const n = sorted.length;
      // 1-5 scale
      const score = Math.round(5 - (rank / Math.max(1, n - 1)) * 4);
      return { score, reason: `${preference === 'low' ? 'Lower' : 'Higher'}-is-better on ${key}; ranked #${rank + 1} of ${n} (${v.toFixed(1)}).` };
    }

    if (pillar === 'valuation') {
      const es = rankScore('evSales', 'low');
      const ef = rankScore('evFcf', 'low');
      const avg = Math.round((es.score + ef.score) / 2);
      return { score: avg, reason: `EV/Sales rank: ${es.score}/5. EV/FCF rank: ${ef.score}/5.` };
    }
    if (pillar === 'growth') {
      const rg = rankScore('revenueGrowth', 'high');
      return { score: rg.score, reason: rg.reason };
    }
    if (pillar === 'profitability') {
      const fm = rankScore('fcfMargin', 'high');
      const ro = rankScore('ruleOf40', 'high');
      const avg = Math.round((fm.score + ro.score) / 2);
      return { score: avg, reason: `FCF margin rank: ${fm.score}/5. Rule of 40 rank: ${ro.score}/5.` };
    }
    if (pillar === 'momentum') {
      const y1 = rankScore('y1', 'high');
      const m3 = rankScore('m3', 'high');
      const avg = Math.round((y1.score + m3.score) / 2);
      return { score: avg, reason: `1Y rank: ${y1.score}/5. 3M rank: ${m3.score}/5.` };
    }
    if (pillar === 'quality') {
      // Use earnings beat pattern if available, else FCF margin durability proxy.
      const intel = (global.earningsIntelData && global.earningsIntelData.tickers && global.earningsIntelData.tickers[ticker]) || null;
      let base = rankScore('fcfMargin', 'high');
      let extra = '';
      if (intel && Array.isArray(intel.history) && intel.history.length) {
        const beats = intel.history.filter(h => (h.surprise_pct || 0) > 0).length;
        const total = intel.history.length;
        const hitRate = beats / total;
        const bonus = hitRate >= 0.75 ? 1 : hitRate <= 0.33 ? -1 : 0;
        base.score = Math.max(1, Math.min(5, base.score + bonus));
        extra = ` Beat pattern: ${beats}/${total} quarters.`;
      }
      return { score: base.score, reason: base.reason + extra };
    }
    return { score: 3, reason: 'Unknown pillar.' };
  }

  function renderScorecard(body) {
    const tickers = state.popupTicker || [];
    const rows = tickers.map(t => (global.tickerData || {})[t] || {});
    const pillars = ['valuation', 'growth', 'profitability', 'momentum', 'quality'];
    const labels = { valuation: 'Valuation', growth: 'Growth', profitability: 'Profitability', momentum: 'Momentum', quality: 'Quality / Durability' };
    const results = {};
    tickers.forEach(t => {
      results[t] = { pillars: {}, total: 0 };
      pillars.forEach(p => {
        const s = scoreOne(p, t, rows, tickers);
        results[t].pillars[p] = s;
        results[t].total += s.score * WEIGHTS[p];
      });
    });

    let html = `<div class="cmp-scorecard">
      <div class="cmp-scorecard-legend">
        <span class="cmp-weight">Growth 30% · Quality 25% · Profitability 20% · Valuation 15% · Momentum 10%</span>
        <span class="cmp-weight-note">Scale: 1 (weakest) to 5 (strongest). Peer-relative.</span>
      </div>
      <table class="cmp-scorecard-table">
        <thead>
          <tr>
            <th class="cmp-fund-metric">Pillar (weight)</th>
            ${tickers.map(t => `<th>${t}</th>`).join('')}
          </tr>
        </thead>
        <tbody>`;
    pillars.forEach(p => {
      html += `<tr><td class="cmp-fund-metric"><span class="cmp-pillar-name">${labels[p]}</span><span class="cmp-pillar-weight">${Math.round(WEIGHTS[p]*100)}%</span></td>`;
      tickers.forEach(t => {
        const s = results[t].pillars[p];
        html += `<td class="cmp-score-cell"><div class="cmp-score-bar"><div class="cmp-score-fill cmp-score-${s.score}" style="width:${s.score*20}%"></div><span class="cmp-score-val">${s.score}/5</span></div><div class="cmp-score-reason">${s.reason}</div></td>`;
      });
      html += '</tr>';
    });
    // Weighted total
    html += `<tr class="cmp-score-total-row"><td class="cmp-fund-metric">Weighted total</td>`;
    tickers.forEach(t => {
      const total = results[t].total;
      html += `<td class="cmp-score-cell"><strong class="cmp-score-total">${total.toFixed(2)} / 5.00</strong></td>`;
    });
    html += '</tr>';
    html += '</tbody></table></div>';
    body.innerHTML = html;
  }

  // ======================= RADAR TAB =======================
  function renderRadar(body) {
    body.innerHTML = `
      <div class="cmp-radar-wrap">
        <canvas id="cmp-radar-canvas"></canvas>
      </div>
      <div class="cmp-radar-note">Each axis is the peer-relative 1\u20135 score from the Scorecard tab. Larger polygon \u2192 more dominant fundamental profile across the pillars.</div>
    `;
    const tickers = state.popupTicker || [];
    const rows = tickers.map(t => (global.tickerData || {})[t] || {});
    const pillars = ['valuation', 'growth', 'profitability', 'momentum', 'quality'];
    const labels = ['Valuation', 'Growth', 'Profitability', 'Momentum', 'Quality'];
    const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ec4899'];

    const datasets = tickers.map((t, i) => ({
      label: t,
      data: pillars.map(p => scoreOne(p, t, rows, tickers).score),
      borderColor: COLORS[i % COLORS.length],
      backgroundColor: COLORS[i % COLORS.length] + '33',
      borderWidth: 2,
      pointRadius: 3,
      pointBackgroundColor: COLORS[i % COLORS.length]
    }));

    const ctx = document.getElementById('cmp-radar-canvas').getContext('2d');
    if (state.radarChart) { state.radarChart.destroy(); state.radarChart = null; }
    state.radarChart = new Chart(ctx, {
      type: 'radar',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#cbd5e1', font: { size: 11 } } },
          tooltip: {
            backgroundColor: '#0f172a',
            borderColor: '#334155',
            borderWidth: 1,
            titleColor: '#e5e7eb',
            bodyColor: '#cbd5e1'
          }
        },
        scales: {
          r: {
            angleLines: { color: 'rgba(148,163,184,0.15)' },
            grid: { color: 'rgba(148,163,184,0.15)' },
            pointLabels: { color: '#cbd5e1', font: { size: 11 } },
            ticks: { color: '#64748b', backdropColor: 'transparent', stepSize: 1, min: 0, max: 5 },
            min: 0,
            max: 5
          }
        }
      }
    });
  }

  // ======================= FORMATTERS =======================
  const fmt = {
    price: (v) => (typeof global.formatPrice === 'function' ? global.formatPrice(v) : (v == null ? '\u2014' : '$' + v.toFixed(2))),
    large: (v) => (typeof global.formatLargeNumber === 'function' ? global.formatLargeNumber(v) : (v == null ? '\u2014' : String(v))),
    mult: (v) => (typeof global.formatMultiple === 'function' ? global.formatMultiple(v) : (v == null ? '\u2014' : v.toFixed(1) + 'x')),
    pct: (v) => (typeof global.formatPercent === 'function' ? global.formatPercent(v) : (v == null ? '\u2014' : v.toFixed(1) + '%')),
    num: (v, d) => (v == null || Number.isNaN(v)) ? '\u2014' : Number(v).toFixed(d || 0)
  };

  // ======================= PUBLIC API =======================
  global.SignalCompare = {
    toggleMode,
    isModeOn,
    isSelected,
    toggleTicker,
    rowCheckboxHtml,
    headerCheckboxHtml,
    wireRowCheckboxes,
    openPopup,
    closePopup,
    updateTray,
    refreshTrayVisibility: function () {
      // Re-evaluate whether the tray should be visible based on current state.
      // Used by the tab-switcher to restore the tray when returning to Watchlist.
      updateTray();
    }
  };

  // Wire toggle button when DOM ready
  function attach() {
    const btn = document.getElementById('compare-toggle-btn');
    if (btn) {
      btn.addEventListener('click', toggleMode);
      renderMode();
    }
    ensureTray();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }

})(window);
