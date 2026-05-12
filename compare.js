/* ===== COMPARE.JS — Cross-company comparison popup (v2) =====
 *
 * Entry UX:
 *   - A "Compare" toggle button sits in the watchlist header.
 *   - When compare-mode is on, a checkbox column is injected into every
 *     public row. Selecting 2-4 tickers shows a sticky tray with a CTA
 *     that opens the comparison popup.
 *
 * Popup tabs (v2):
 *   Overview | Trends | Fundamentals | Scorecard | Radar | Earnings Intel | AI Read
 *
 * Notable v2 changes:
 *   - Real AI Read tab routed through Perplexity Deep Research (compare-ai.js)
 *   - Honest benchmark labels (true equal-weight comp basket, never a fake
 *     "subsector median" silently backed by SPY)
 *   - Grouped/expanded Fundamentals table (Valuation / Growth / Profitability /
 *     Market / Earnings) with section headers and winner highlight
 *   - Scorecard adds absolute tier alongside peer-relative score
 *   - Radar supports relative <-> absolute toggle
 *   - Earnings Intel tab compares last reactions, beat/miss cadence, themes
 *   - Persistence: copy as markdown / save snapshot to localStorage,
 *     preserves active tab between opens
 *
 * Public API on window.SignalCompare is preserved.
 */
(function (global) {
  'use strict';

  const MIN_PICK = 2;
  const MAX_PICK = 4;

  const state = {
    mode: false,
    selected: new Set(),
    popupOpen: false,
    _persistKey: 'signalai_compare_selected_v1',
    _tabKey: 'signalai_compare_tab_v1',
    popupTicker: null,
    activeTab: 'overview',
    chart: null,
    radarChart: null,
    chartRange: '3M',
    benchmarkMode: 'none',           // none | comp_basket | etf_proxy | spy | all
    priceCache: {},
    radarMode: 'relative',           // relative | absolute
    earningsReactionsCache: {},      // { TICKER: [{date, move_pct}, ...] }
  };

  const TAB_ORDER = ['overview', 'trends', 'fundamentals', 'scorecard', 'radar', 'intel', 'ai-read'];
  const TAB_LABELS = {
    overview: 'Overview',
    trends: 'Trends',
    fundamentals: 'Fundamentals',
    scorecard: 'Scorecard',
    radar: 'Radar',
    intel: 'Earnings Intel',
    'ai-read': 'AI Read',
  };

  // ======================= PERSISTENCE =======================
  function persistSelection() {
    try {
      const arr = Array.from(state.selected);
      if (arr.length) localStorage.setItem(state._persistKey, JSON.stringify(arr));
      else localStorage.removeItem(state._persistKey);
    } catch (e) { /* storage disabled */ }
  }

  function hydrateSelection() {
    try {
      const raw = localStorage.getItem(state._persistKey);
      if (!raw) return;
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) arr.slice(0, MAX_PICK).forEach(t => state.selected.add(t));
    } catch (e) { /* ignore */ }
  }

  function persistTab(tab) {
    try { localStorage.setItem(state._tabKey, tab); } catch (e) {}
  }
  function hydrateTab() {
    try {
      const t = localStorage.getItem(state._tabKey);
      if (t && TAB_ORDER.indexOf(t) >= 0) state.activeTab = t;
    } catch (e) {}
  }

  // ======================= COMPARE MODE TOGGLE =======================
  function toggleMode() {
    state.mode = !state.mode;
    if (!state.mode) {
      state.selected.clear();
      persistSelection();
    }
    renderMode();
    updateTray();
    if (typeof global.renderTable === 'function') global.renderTable();
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
      const tray = document.getElementById('compare-tray');
      if (tray) {
        tray.classList.add('shake');
        setTimeout(() => tray.classList.remove('shake'), 320);
      }
      return;
    }
    persistSelection();
    updateTray();
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
      persistSelection();
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
    tray.classList.toggle('visible', !!state.mode);
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
    hydrateTab();
    if (!TAB_ORDER.includes(state.activeTab)) state.activeTab = 'overview';
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
          <div class="compare-header-main">
            <h2 id="compare-title">Compare</h2>
            <div class="compare-subtitle" id="compare-subtitle"></div>
          </div>
          <div class="compare-header-actions">
            <button type="button" class="btn-sm btn-ghost" id="cmp-export-md" title="Copy comparison as Markdown">Copy as MD</button>
            <button type="button" class="btn-sm btn-ghost" id="cmp-snapshot" title="Save current view to local notes">Save snapshot</button>
          </div>
        </div>
        <div class="compare-tabs" role="tablist" id="compare-tabs"></div>
        <div class="compare-body" id="compare-body"></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#compare-close').addEventListener('click', closePopup);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closePopup(); });
    overlay.querySelector('#cmp-export-md').addEventListener('click', exportAsMarkdown);
    overlay.querySelector('#cmp-snapshot').addEventListener('click', saveSnapshot);

    const tabsContainer = overlay.querySelector('#compare-tabs');
    tabsContainer.innerHTML = TAB_ORDER.map(t =>
      `<button class="compare-tab${state.activeTab === t ? ' active' : ''}" data-tab="${t}">${TAB_LABELS[t]}</button>`
    ).join('');
    tabsContainer.querySelectorAll('.compare-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        tabsContainer.querySelectorAll('.compare-tab').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        state.activeTab = btn.dataset.tab;
        persistTab(state.activeTab);
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
    // Sync active tab highlight
    document.querySelectorAll('#compare-tabs .compare-tab').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === state.activeTab);
    });
    renderActiveTab();
  }

  function renderActiveTab() {
    const body = document.getElementById('compare-body');
    if (!body) return;
    const tickers = state.popupTicker || [];
    const rows = tickers.map(t => augmentRow(t, (global.tickerData || {})[t] || { ticker: t }));
    switch (state.activeTab) {
      case 'overview': return renderOverview(body, tickers, rows);
      case 'trends': return renderTrends(body, tickers, rows);
      case 'fundamentals': return renderFundamentals(body, tickers, rows);
      case 'scorecard': return renderScorecard(body, tickers, rows);
      case 'radar': return renderRadar(body, tickers, rows);
      case 'intel': return renderIntel(body, tickers, rows);
      case 'ai-read':
        if (global.SignalCompareAI && global.SignalCompareAI.render) {
          return global.SignalCompareAI.render(body, tickers, rows);
        }
        body.innerHTML = '<div class="cmp-empty">AI Read module failed to load.</div>';
        return;
      default: return renderOverview(body, tickers, rows);
    }
  }

  // ======================= ROW AUGMENTATION =======================
  // Compute derived metrics that aren't always pre-set on tickerData rows.
  function augmentRow(ticker, src) {
    const r = Object.assign({}, src, { ticker });
    // fcfMargin = FCF / Revenue * 100
    if (r.fcfMargin == null && r.freeCashflow != null && r.totalRevenue) {
      r.fcfMargin = (r.freeCashflow / r.totalRevenue) * 100;
    }
    // operatingMargins on yfinance is already a fraction in some sources, percent in others.
    // Normalize: if abs < 2, treat as fraction; else treat as already-percent.
    if (r.operatingMargins != null && Math.abs(r.operatingMargins) <= 2) {
      r.operatingMargin = r.operatingMargins * 100;
    } else {
      r.operatingMargin = r.operatingMargins;
    }
    // Rule of 40 = revenue growth + FCF margin
    if (r.ruleOf40 == null && typeof r.revenueGrowth === 'number' && typeof r.fcfMargin === 'number') {
      r.ruleOf40 = r.revenueGrowth + r.fcfMargin;
    }
    // % off 52-week high
    if (r.price && r.fiftyTwoWeekHigh) {
      r.pctOffHigh = ((r.price - r.fiftyTwoWeekHigh) / r.fiftyTwoWeekHigh) * 100;
    }
    // Implied upside to consensus target
    if (r.price && r.targetMeanPrice) {
      r.upsideToTarget = ((r.targetMeanPrice - r.price) / r.price) * 100;
    }
    // Last earnings reaction from intel
    const intel = getIntel(ticker);
    if (intel && intel.post_earnings_review && intel.post_earnings_review.stock_reaction_pct != null) {
      r.lastEarningsMove = intel.post_earnings_review.stock_reaction_pct;
    }
    return r;
  }

  // ======================= OVERVIEW TAB =======================
  function renderOverview(body, tickers, rows) {
    // Compact header card with current price, % off high, market cap, target upside.
    const heroHtml = tickers.map((t, i) => {
      const r = rows[i];
      const tone = (r.upsideToTarget != null && r.upsideToTarget > 0) ? 'cmp-up' : (r.upsideToTarget != null ? 'cmp-down' : '');
      return `<div class="cmp-hero ${tone}">
        <div class="cmp-hero-ticker">${t}</div>
        <div class="cmp-hero-name">${escapeHtml((r.name || '').slice(0, 36))}</div>
        <div class="cmp-hero-price">${fmt.price(r.price)}</div>
        <div class="cmp-hero-grid">
          <div><span class="cmp-hero-k">Mkt cap</span><span class="cmp-hero-v">${fmt.large(r.marketCap)}</span></div>
          <div><span class="cmp-hero-k">FY1 EV/Sales</span><span class="cmp-hero-v">${fmt.mult(r.evSales)}</span></div>
          <div><span class="cmp-hero-k">Rev growth</span><span class="cmp-hero-v">${fmt.pct(r.revenueGrowth)}</span></div>
          <div><span class="cmp-hero-k">FCF margin</span><span class="cmp-hero-v">${fmt.pct(r.fcfMargin)}</span></div>
          <div><span class="cmp-hero-k">% off 52W high</span><span class="cmp-hero-v ${r.pctOffHigh < -20 ? 'cmp-down' : ''}">${fmt.pct(r.pctOffHigh)}</span></div>
          <div><span class="cmp-hero-k">Target upside</span><span class="cmp-hero-v">${fmt.pct(r.upsideToTarget)}</span></div>
        </div>
      </div>`;
    }).join('');

    body.innerHTML = `
      <div class="cmp-section cmp-hero-section">
        <div class="cmp-hero-grid-wrap">${heroHtml}</div>
      </div>
      <div class="cmp-section cmp-chart-section">
        <div class="cmp-section-head">
          <h3>Indexed Return <span class="cmp-subtle">(rebased to 100)</span></h3>
          <div class="cmp-chart-controls">
            <div class="cmp-range-toggle" id="cmp-range-toggle">
              ${['1W','1M','3M','YTD','1Y','3Y'].map(r =>
                `<button type="button" class="cmp-range-btn${state.chartRange===r?' active':''}" data-range="${r}">${r}</button>`).join('')}
            </div>
            <select class="cmp-benchmark-select" id="cmp-benchmark-select">
              <option value="none"${state.benchmarkMode==='none'?' selected':''}>No benchmark</option>
              <option value="comp_basket"${state.benchmarkMode==='comp_basket'?' selected':''}>Equal-weight comp basket</option>
              <option value="etf_proxy"${state.benchmarkMode==='etf_proxy'?' selected':''}>Subsector ETF proxy</option>
              <option value="spy"${state.benchmarkMode==='spy'?' selected':''}>S&amp;P 500 (SPY)</option>
              <option value="all"${state.benchmarkMode==='all'?' selected':''}>All benchmarks</option>
            </select>
          </div>
        </div>
        <div class="cmp-chart-wrap">
          <canvas id="cmp-chart"></canvas>
          <div class="cmp-chart-loading" id="cmp-chart-loading">Loading price history\u2026</div>
        </div>
        <div class="cmp-benchmark-legend" id="cmp-benchmark-legend"></div>
      </div>
    `;
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
    drawIndexedChart();
  }

  // ======================= TRENDS TAB =======================
  function renderTrends(body, tickers, rows) {
    body.innerHTML = `
      <div class="cmp-trends-section">
        <div class="cmp-section-head"><h3>Performance ladder</h3>
          <span class="cmp-subtle">Direction across windows. Bars are clipped at \u00b1100% for readability.</span></div>
        <div class="cmp-perf-ladder" id="cmp-perf-ladder"></div>
      </div>
      <div class="cmp-trends-section">
        <div class="cmp-section-head"><h3>Earnings reaction history</h3>
          <span class="cmp-subtle">Approximate close-vs-close move on the trading day after each reported quarter.</span></div>
        <div class="cmp-reactions-grid" id="cmp-reactions-grid"></div>
      </div>
      <div class="cmp-trends-section">
        <div class="cmp-section-head"><h3>Fundamental snapshot</h3>
          <span class="cmp-subtle">Quarterly history isn\u2019t in the local data layer, so this is a point-in-time peer comparison. Use the AI Read tab to add narrative on direction.</span></div>
        <div class="cmp-snap-bars" id="cmp-snap-bars"></div>
      </div>
    `;
    renderPerfLadder(body.querySelector('#cmp-perf-ladder'), tickers, rows);
    renderReactionHistory(body.querySelector('#cmp-reactions-grid'), tickers, rows);
    renderSnapshotBars(body.querySelector('#cmp-snap-bars'), tickers, rows);
  }

  function renderPerfLadder(root, tickers, rows) {
    const windows = [
      { k: 'w1',  label: '1W' },
      { k: 'm1',  label: '1M' },
      { k: 'm3',  label: '3M' },
      { k: 'ytd', label: 'YTD' },
      { k: 'y1',  label: '1Y' },
      { k: 'y3',  label: '3Y' },
    ];
    let html = '<table class="cmp-perf-table"><thead><tr><th></th>';
    windows.forEach(w => { html += `<th>${w.label}</th>`; });
    html += '</tr></thead><tbody>';
    tickers.forEach((t, i) => {
      html += `<tr><td class="cmp-perf-ticker">${t}</td>`;
      windows.forEach(w => {
        const v = rows[i][w.k];
        const cls = (typeof v === 'number') ? (v >= 0 ? 'positive' : 'negative') : 'na';
        const widthPct = (typeof v === 'number') ? Math.min(100, Math.abs(v) / 1.0) : 0;
        const valTxt = (typeof v === 'number') ? (v.toFixed(1) + '%') : '\u2014';
        html += `<td class="cmp-perf-cell"><div class="cmp-perf-bar"><div class="cmp-perf-fill ${cls}" style="width:${widthPct}%"></div></div><span class="cmp-perf-val ${cls}">${valTxt}</span></td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    root.innerHTML = html;
  }

  // Compute earnings reactions from yfinance chart data + known earnings dates.
  async function renderReactionHistory(root, tickers, rows) {
    root.innerHTML = tickers.map(t => `<div class="cmp-react-card" data-ticker="${t}"><div class="cmp-react-head">${t}</div><div class="cmp-react-body">Loading\u2026</div></div>`).join('');
    const results = await Promise.all(tickers.map(t => computeEarningsReactions(t)));
    tickers.forEach((t, i) => {
      const card = root.querySelector(`.cmp-react-card[data-ticker="${t}"]`);
      if (!card) return;
      const reactions = results[i];
      const bodyEl = card.querySelector('.cmp-react-body');
      if (!reactions || !reactions.length) {
        bodyEl.innerHTML = '<div class="cmp-react-empty">No earnings reaction history available.</div>';
        return;
      }
      const beats = reactions.filter(r => r.move_pct > 0).length;
      const total = reactions.length;
      const cadenceLabel = `${beats}/${total} positive next-day reactions`;
      // bar mini-chart
      const maxAbs = Math.max(2, Math.max.apply(null, reactions.map(r => Math.abs(r.move_pct))));
      const bars = reactions.map(r => {
        const h = Math.min(100, (Math.abs(r.move_pct) / maxAbs) * 100);
        const dir = r.move_pct >= 0 ? 'up' : 'down';
        const title = `${r.date}: ${r.move_pct >= 0 ? '+' : ''}${r.move_pct.toFixed(1)}%`;
        return `<div class="cmp-react-bar ${dir}" style="height:${h}%" title="${title}"><span class="cmp-react-bar-label">${(r.move_pct >= 0 ? '+' : '') + r.move_pct.toFixed(0)}%</span></div>`;
      }).join('');
      bodyEl.innerHTML = `<div class="cmp-react-cadence">${cadenceLabel}</div><div class="cmp-react-bars">${bars}</div>`;
    });
  }

  async function computeEarningsReactions(ticker) {
    if (state.earningsReactionsCache[ticker]) return state.earningsReactionsCache[ticker];
    // Source dates from earnings_data.json (window.earningsData) if loaded;
    // else fall back to whatever's in earningsIntelData.
    let dates = [];
    const ed = global.earningsData || global._earningsData || null;
    if (ed && ed.all_tickers && ed.all_tickers[ticker] && Array.isArray(ed.all_tickers[ticker].earnings_dates)) {
      dates = ed.all_tickers[ticker].earnings_dates.slice();
    }
    if (!dates.length) {
      try {
        // Try fetching earnings_data.json once
        const resp = await fetch('earnings_data.json', { cache: 'force-cache' });
        if (resp.ok) {
          const j = await resp.json();
          global._earningsData = j;
          if (j.all_tickers && j.all_tickers[ticker]) dates = j.all_tickers[ticker].earnings_dates || [];
        }
      } catch (e) { /* ignore */ }
    }
    if (!dates.length) {
      state.earningsReactionsCache[ticker] = [];
      return [];
    }
    // Filter to past dates within 3y; take the most recent 6.
    const today = new Date();
    const cutoff = new Date(today.getTime() - 1000 * 60 * 60 * 24 * 365 * 3);
    const pastDates = dates.map(d => new Date(d + 'T16:00:00Z'))
      .filter(d => d <= today && d >= cutoff)
      .sort((a, b) => a - b);
    if (!pastDates.length) {
      state.earningsReactionsCache[ticker] = [];
      return [];
    }
    // Fetch 3y of daily closes
    const series = await fetchPriceSeries(ticker, '3Y');
    if (!series || !series.dates.length) {
      state.earningsReactionsCache[ticker] = [];
      return [];
    }
    // For each earnings date, find the close on/right-before and right-after.
    const reactions = [];
    pastDates.slice(-8).forEach(eDate => {
      // Find the close index <= eDate (close on or before earnings)
      let beforeIdx = -1;
      for (let i = 0; i < series.dates.length; i++) {
        if (series.dates[i] <= eDate) beforeIdx = i; else break;
      }
      if (beforeIdx < 0 || beforeIdx >= series.dates.length - 1) return;
      const beforeClose = series.closes[beforeIdx];
      const afterClose = series.closes[beforeIdx + 1];
      if (!beforeClose || !afterClose) return;
      const move = ((afterClose - beforeClose) / beforeClose) * 100;
      reactions.push({ date: eDate.toISOString().slice(0, 10), move_pct: move });
    });
    state.earningsReactionsCache[ticker] = reactions;
    return reactions;
  }

  function renderSnapshotBars(root, tickers, rows) {
    const metrics = [
      { k: 'revenueGrowth', label: 'Rev growth', unit: '%', max: 50 },
      { k: 'fcfMargin',     label: 'FCF margin', unit: '%', max: 50 },
      { k: 'ruleOf40',      label: 'Rule of 40', unit: '',  max: 80 },
      { k: 'evSales',       label: 'FY1 EV/Sales', unit: 'x', max: 25 },
    ];
    let html = '<table class="cmp-snap-table"><thead><tr><th></th>';
    tickers.forEach(t => { html += `<th>${t}</th>`; });
    html += '</tr></thead><tbody>';
    metrics.forEach(m => {
      html += `<tr><td class="cmp-snap-metric">${m.label}</td>`;
      const vals = rows.map(r => (typeof r[m.k] === 'number' ? r[m.k] : null));
      const maxAbs = Math.max(m.max, Math.max.apply(null, vals.map(v => v == null ? 0 : Math.abs(v))) || m.max);
      tickers.forEach((t, i) => {
        const v = vals[i];
        if (v == null) { html += `<td class="cmp-snap-cell"><span class="cmp-snap-val cmp-na">\u2014</span></td>`; return; }
        const w = Math.min(100, (Math.abs(v) / maxAbs) * 100);
        const cls = v >= 0 ? 'positive' : 'negative';
        const valTxt = v.toFixed(1) + m.unit;
        html += `<td class="cmp-snap-cell"><div class="cmp-snap-bar"><div class="cmp-snap-fill ${cls}" style="width:${w}%"></div></div><span class="cmp-snap-val">${valTxt}</span></td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    root.innerHTML = html;
  }

  // ======================= FUNDAMENTALS TAB =======================
  // Grouped, dense, with explicit section headers. Winner highlight per row.
  function renderFundamentals(body, tickers, rows) {
    const groups = [
      { title: 'Market', rows: [
        { key: 'price',          label: 'Price',                fmt: r => fmt.price(r.price),            winner: null },
        { key: 'marketCap',      label: 'Market cap',           fmt: r => fmt.large(r.marketCap),        winner: 'high' },
        { key: 'ev',             label: 'EV',                   fmt: r => fmt.large(r.ev),               winner: 'high' },
        { key: 'pctOffHigh',     label: '% off 52W high',       fmt: r => fmt.pct(r.pctOffHigh),         winner: 'high' },
        { key: 'subsector',      label: 'Subsector',            fmt: (r, t) => escapeHtml(r.subsector || (typeof global.getSubsector === 'function' ? global.getSubsector(t) : '\u2014')), winner: null },
      ]},
      { title: 'Valuation', rows: [
        { key: 'evSales',        label: 'FY1 EV/Sales',         fmt: r => fmt.mult(r.evSales),           winner: 'low' },
        { key: 'evFcf',          label: 'FY1 EV/FCF',           fmt: r => fmt.mult(r.evFcf),             winner: 'low' },
        { key: 'forwardPE',      label: 'Forward P/E',          fmt: r => fmt.mult(r.forwardPE),         winner: 'low' },
        { key: 'enterpriseToEbitda', label: 'EV/EBITDA',        fmt: r => fmt.mult(r.enterpriseToEbitda),winner: 'low' },
      ]},
      { title: 'Growth', rows: [
        { key: 'revenueGrowth',  label: 'Revenue growth (TTM YoY)', fmt: r => fmt.pct(r.revenueGrowth), winner: 'high' },
        { key: 'earningsGrowth', label: 'Earnings growth',      fmt: r => fmt.pct(r.earningsGrowth),    winner: 'high' },
        { key: 'totalRevenue',   label: 'TTM revenue',          fmt: r => fmt.large(r.totalRevenue),    winner: 'high' },
      ]},
      { title: 'Profitability', rows: [
        { key: 'fcfMargin',      label: 'FCF margin',           fmt: r => fmt.pct(r.fcfMargin),         winner: 'high' },
        { key: 'operatingMargin',label: 'Operating margin',     fmt: r => fmt.pct(r.operatingMargin),   winner: 'high' },
        { key: 'ruleOf40',       label: 'Rule of 40',           fmt: r => fmt.num(r.ruleOf40, 1),       winner: 'high' },
        { key: 'freeCashflow',   label: 'TTM FCF',              fmt: r => fmt.large(r.freeCashflow),    winner: 'high' },
        { key: 'totalDebt',      label: 'Total debt',           fmt: r => fmt.large(r.totalDebt),       winner: 'low' },
      ]},
      { title: 'Market performance', rows: [
        { key: 'ytd',            label: 'YTD',                  fmt: r => fmt.pct(r.ytd),               winner: 'high' },
        { key: 'm1',             label: '1M',                   fmt: r => fmt.pct(r.m1),                winner: 'high' },
        { key: 'm3',             label: '3M',                   fmt: r => fmt.pct(r.m3),                winner: 'high' },
        { key: 'y1',             label: '1Y',                   fmt: r => fmt.pctWithFlag(r.y1),        winner: 'high' },
        { key: 'y3',             label: '3Y',                   fmt: r => fmt.pctWithFlag(r.y3),        winner: 'high' },
      ]},
      { title: 'Earnings & sell side', rows: [
        { key: 'lastEarningsMove', label: 'Last EPS reaction',  fmt: r => fmt.pct(r.lastEarningsMove),  winner: 'high' },
        { key: 'targetMeanPrice',  label: 'Consensus target',   fmt: r => fmt.price(r.targetMeanPrice), winner: 'high' },
        { key: 'upsideToTarget',   label: 'Implied upside',     fmt: r => fmt.pct(r.upsideToTarget),    winner: 'high' },
        { key: 'numberOfAnalystOpinions', label: 'Analyst count', fmt: r => (r.numberOfAnalystOpinions || '\u2014'), winner: 'high' },
        { key: 'recommendationKey',label: 'Consensus rating',    fmt: r => escapeHtml(r.recommendationKey || '\u2014'), winner: null },
      ]},
    ];

    let html = '<div class="cmp-section cmp-fund-section">'
      + '<div class="cmp-section-head"><h3>Fundamentals</h3>'
      + '<span class="cmp-subtle">Winner per row highlighted. Missing fields render as &mdash;.</span></div>'
      + '<table class="cmp-fund-table"><thead><tr><th class="cmp-fund-metric">Metric</th>';
    tickers.forEach(t => { html += `<th>${t}</th>`; });
    html += '</tr></thead><tbody>';
    groups.forEach(group => {
      html += `<tr class="cmp-fund-group"><td colspan="${tickers.length + 1}">${group.title}</td></tr>`;
      group.rows.forEach(def => {
        let winnerIdx = -1;
        if (def.winner) {
          const vals = rows.map(r => {
            const v = r[def.key];
            return (typeof v === 'number' && !Number.isNaN(v)) ? v : null;
          });
          const valid = vals.filter(v => v != null);
          if (valid.length > 1) {
            const target = def.winner === 'high' ? Math.max.apply(null, valid) : Math.min.apply(null, valid);
            winnerIdx = vals.indexOf(target);
          }
        }
        html += `<tr><td class="cmp-fund-metric">${def.label}</td>`;
        rows.forEach((r, i) => {
          const cell = def.fmt(r, tickers[i]);
          const isWinner = i === winnerIdx;
          html += `<td class="${isWinner ? 'cmp-winner' : ''}">${cell}</td>`;
        });
        html += '</tr>';
      });
    });
    html += '</tbody></table></div>';
    body.innerHTML = html;
  }

  // ======================= CHART =======================
  function rangeToYahoo(range) {
    switch (range) {
      case '1D':  return { range: '1d',  interval: '5m' };
      case '1W':  return { range: '5d',  interval: '15m' };
      case '1M':  return { range: '1mo', interval: '1d' };
      case '3M':  return { range: '3mo', interval: '1d' };
      case 'YTD': return { range: 'ytd', interval: '1d' };
      case '1Y':  return { range: '1y',  interval: '1d' };
      case '3Y':  return { range: '3y',  interval: '1d' };
      default:    return { range: '3mo', interval: '1d' };
    }
  }

  async function fetchPriceSeries(ticker, range) {
    const key = ticker + '_' + range;
    if (state.priceCache[key]) return state.priceCache[key];
    const { range: r, interval } = rangeToYahoo(range);
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=${r}&interval=${interval}`;
    try {
      let data = null;
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

  function etfForSubsector(subsector) {
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
    return map[subsector] || null;
  }

  async function drawIndexedChart() {
    const canvas = document.getElementById('cmp-chart');
    const loading = document.getElementById('cmp-chart-loading');
    const legend = document.getElementById('cmp-benchmark-legend');
    if (!canvas) return;
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    if (loading) loading.style.display = 'flex';
    if (legend) legend.innerHTML = '';

    const tickers = state.popupTicker || [];
    const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ec4899'];

    const seriesResults = await Promise.all(tickers.map(t => fetchPriceSeries(t, state.chartRange)));
    const datasets = [];
    tickers.forEach((t, i) => {
      const s = seriesResults[i];
      if (!s || !s.closes.length) return;
      datasets.push({
        label: t,
        data: s.dates.map((d, idx) => ({ x: d, y: indexSeries(s.closes)[idx] })),
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: 'transparent',
        borderWidth: 2,
        tension: 0.15,
        pointRadius: 0,
      });
    });

    // ---------- Benchmarks (honest labels) ----------
    const mode = state.benchmarkMode;
    const subsectors = tickers.map(t => {
      const d = (global.tickerData || {})[t] || {};
      return d.subsector || (typeof global.getSubsector === 'function' ? global.getSubsector(t) : null);
    });
    // Primary subsector = most common across the comp set
    const subCounts = {};
    subsectors.forEach(s => { if (s) subCounts[s] = (subCounts[s] || 0) + 1; });
    const primarySub = Object.keys(subCounts).sort((a, b) => subCounts[b] - subCounts[a])[0] || null;

    const legendBits = [];

    if (mode === 'etf_proxy' || mode === 'all') {
      const etf = etfForSubsector(primarySub);
      if (etf) {
        const s = await fetchPriceSeries(etf, state.chartRange);
        if (s && s.closes.length) {
          datasets.push(benchmarkDataset(`Subsector ETF \u2014 ${etf}`, s, '#64748b', [4, 3]));
          legendBits.push(`Subsector ETF proxy: <strong>${etf}</strong> (${primarySub || 'n/a'})`);
        } else {
          legendBits.push(`Subsector ETF unavailable for ${primarySub}.`);
        }
      } else {
        legendBits.push(`No ETF mapping for subsector "${primarySub || 'n/a'}".`);
      }
    }
    if (mode === 'spy' || mode === 'all') {
      const s = await fetchPriceSeries('SPY', state.chartRange);
      if (s && s.closes.length) {
        datasets.push(benchmarkDataset('S&P 500 (SPY)', s, '#94a3b8', [2, 2]));
        legendBits.push('S&amp;P 500 benchmark: <strong>SPY</strong>');
      }
    }
    if (mode === 'comp_basket' || mode === 'all') {
      // True equal-weight basket of the SELECTED tickers (uses already-fetched
      // series). This is an honest comp-set basket; it does not pretend to be
      // a subsector median.
      const valid = seriesResults.filter(s => s && s.closes.length);
      if (valid.length >= 2) {
        // Build a date->indexed-value map per series, then align on common dates.
        const indexed = valid.map(s => ({ dates: s.dates, vals: indexSeries(s.closes) }));
        // Use the shortest series' date axis to align
        let shortestIdx = 0;
        for (let i = 1; i < indexed.length; i++) {
          if (indexed[i].dates.length < indexed[shortestIdx].dates.length) shortestIdx = i;
        }
        const baseDates = indexed[shortestIdx].dates;
        const ew = [];
        baseDates.forEach((d, idx) => {
          let sum = 0, c = 0;
          indexed.forEach(seq => {
            // For aligned-length series use the same index; for longer, take last N.
            const offset = seq.dates.length - baseDates.length;
            const v = seq.vals[offset + idx];
            if (v != null) { sum += v; c++; }
          });
          ew.push(c ? sum / c : null);
        });
        datasets.push({
          label: 'Equal-weight comp basket',
          data: baseDates.map((d, i) => ({ x: d, y: ew[i] })),
          borderColor: '#e5e7eb',
          backgroundColor: 'transparent',
          borderWidth: 1.4,
          borderDash: [3, 3],
          tension: 0.15,
          pointRadius: 0,
          order: 100
        });
        legendBits.push('Equal-weight comp basket: ' + tickers.join(' + '));
      }
    }

    if (legend) legend.innerHTML = legendBits.map(b => `<span class="cmp-bench-bit">${b}</span>`).join('');

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

  function benchmarkDataset(label, series, color, dash) {
    return {
      label: label,
      data: series.dates.map((d, idx) => ({ x: d, y: indexSeries(series.closes)[idx] })),
      borderColor: color,
      backgroundColor: 'transparent',
      borderWidth: 1.2,
      borderDash: dash,
      tension: 0.15,
      pointRadius: 0,
      order: 99
    };
  }

  // ======================= SCORECARD =======================
  // Peer-relative 1-5 score per pillar AND absolute tier per pillar.
  const WEIGHTS = { valuation: 0.15, growth: 0.30, profitability: 0.20, momentum: 0.10, quality: 0.25 };
  const PILLAR_LABELS = { valuation: 'Valuation', growth: 'Growth', profitability: 'Profitability', momentum: 'Momentum', quality: 'Quality / Durability' };
  const PILLAR_ORDER = ['valuation', 'growth', 'profitability', 'momentum', 'quality'];

  // Absolute-tier thresholds calibrated for software/internet/tech names.
  // tiers: 5=excellent, 4=good, 3=okay, 2=weak, 1=poor
  const ABS_THRESHOLDS = {
    evSales:        { dir: 'low',  ladder: [4, 8, 12, 18] },      // <4=5, 4-8=4, 8-12=3, 12-18=2, >18=1
    evFcf:          { dir: 'low',  ladder: [15, 25, 40, 70] },
    revenueGrowth:  { dir: 'high', ladder: [5, 12, 20, 30] },      // <5=1, 5-12=2, 12-20=3, 20-30=4, >30=5
    fcfMargin:      { dir: 'high', ladder: [0, 10, 20, 30] },
    ruleOf40:       { dir: 'high', ladder: [20, 30, 40, 55] },
    y1:             { dir: 'high', ladder: [-10, 0, 15, 35] },
    m3:             { dir: 'high', ladder: [-5, 0, 5, 15] },
  };

  function absTier(key, val) {
    const cfg = ABS_THRESHOLDS[key];
    if (!cfg || typeof val !== 'number' || Number.isNaN(val)) return null;
    const ladder = cfg.ladder;
    if (cfg.dir === 'high') {
      if (val < ladder[0]) return 1;
      if (val < ladder[1]) return 2;
      if (val < ladder[2]) return 3;
      if (val < ladder[3]) return 4;
      return 5;
    } else {
      if (val < ladder[0]) return 5;
      if (val < ladder[1]) return 4;
      if (val < ladder[2]) return 3;
      if (val < ladder[3]) return 2;
      return 1;
    }
  }

  function relRank(rows, key, preference) {
    const vals = rows.map(x => x[key]).filter(v => typeof v === 'number' && !Number.isNaN(v));
    return { vals, preference };
  }

  function relScore(rows, key, preference, idx) {
    const all = rows.map(x => x[key]);
    const valid = all.filter(v => typeof v === 'number' && !Number.isNaN(v));
    if (valid.length < 2) return { score: 3, reason: 'Not enough peer data; neutral.' };
    const v = all[idx];
    if (typeof v !== 'number' || Number.isNaN(v)) return { score: 2, reason: 'Metric unavailable.' };
    const sorted = valid.slice().sort((a, b) => preference === 'low' ? a - b : b - a);
    const rank = sorted.indexOf(v);
    const n = sorted.length;
    const score = Math.round(5 - (rank / Math.max(1, n - 1)) * 4);
    return { score, reason: `${preference === 'low' ? 'Lower' : 'Higher'} is better: rank ${rank + 1} of ${n}.` };
  }

  function pillarScore(pillar, rows, idx) {
    const r = rows[idx];
    if (pillar === 'valuation') {
      const es = relScore(rows, 'evSales', 'low', idx);
      const ef = relScore(rows, 'evFcf', 'low', idx);
      const score = Math.round((es.score + ef.score) / 2);
      const absES = absTier('evSales', r.evSales);
      const absEF = absTier('evFcf', r.evFcf);
      const absT = (absES != null && absEF != null) ? Math.round((absES + absEF) / 2) : (absES || absEF || null);
      return {
        score,
        absTier: absT,
        reason: `EV/Sales: ${es.score}/5 peer (abs ${absES || 'n/a'}). EV/FCF: ${ef.score}/5 peer (abs ${absEF || 'n/a'}).`,
        plain: explainValuation(r),
      };
    }
    if (pillar === 'growth') {
      const g = relScore(rows, 'revenueGrowth', 'high', idx);
      const absG = absTier('revenueGrowth', r.revenueGrowth);
      return {
        score: g.score,
        absTier: absG,
        reason: `Revenue growth: ${g.score}/5 peer (abs ${absG || 'n/a'}).`,
        plain: explainGrowth(r),
      };
    }
    if (pillar === 'profitability') {
      const fm = relScore(rows, 'fcfMargin', 'high', idx);
      const ro = relScore(rows, 'ruleOf40', 'high', idx);
      const score = Math.round((fm.score + ro.score) / 2);
      const absFM = absTier('fcfMargin', r.fcfMargin);
      const absR = absTier('ruleOf40', r.ruleOf40);
      const absT = (absFM != null && absR != null) ? Math.round((absFM + absR) / 2) : (absFM || absR || null);
      return {
        score,
        absTier: absT,
        reason: `FCF margin: ${fm.score}/5 peer (abs ${absFM || 'n/a'}). Rule of 40: ${ro.score}/5 peer (abs ${absR || 'n/a'}).`,
        plain: explainProfitability(r),
      };
    }
    if (pillar === 'momentum') {
      const y1 = relScore(rows, 'y1', 'high', idx);
      const m3 = relScore(rows, 'm3', 'high', idx);
      const score = Math.round((y1.score + m3.score) / 2);
      const absY = absTier('y1', r.y1);
      const absM = absTier('m3', r.m3);
      const absT = (absY != null && absM != null) ? Math.round((absY + absM) / 2) : (absY || absM || null);
      return {
        score,
        absTier: absT,
        reason: `1Y return: ${y1.score}/5 peer (abs ${absY || 'n/a'}). 3M return: ${m3.score}/5 peer (abs ${absM || 'n/a'}).`,
        plain: explainMomentum(r),
      };
    }
    if (pillar === 'quality') {
      // Quality = FCF margin durability + beat rate + estimate support (revisions)
      const fm = relScore(rows, 'fcfMargin', 'high', idx);
      let score = fm.score;
      let absT = absTier('fcfMargin', r.fcfMargin);
      let extra = '';
      const intel = getIntel(r.ticker);
      // Beat-rate component: use locally-cached earnings reactions if we have them; else intel signal_scorecard
      const reactions = state.earningsReactionsCache[r.ticker] || [];
      if (reactions.length >= 3) {
        const beats = reactions.filter(x => x.move_pct > 0).length;
        const hit = beats / reactions.length;
        const bonus = hit >= 0.66 ? 1 : hit <= 0.33 ? -1 : 0;
        score = Math.max(1, Math.min(5, score + bonus));
        extra = ` Reaction hit-rate ${(hit * 100).toFixed(0)}% (${beats}/${reactions.length}).`;
      }
      // Estimate-support component: forward EPS support
      if (typeof r.forwardEps === 'number' && typeof r.trailingEps === 'number' && r.trailingEps > 0) {
        const epsGrowthFwd = ((r.forwardEps - r.trailingEps) / r.trailingEps) * 100;
        if (epsGrowthFwd >= 15) extra += ' Forward EPS growth >15%.';
        if (epsGrowthFwd < 0) { score = Math.max(1, score - 1); extra += ' Forward EPS below trailing (negative revision pressure).'; }
      }
      return {
        score,
        absTier: absT,
        reason: `FCF margin durability: ${fm.score}/5 peer (abs ${absT || 'n/a'}).${extra}`,
        plain: explainQuality(r, intel, reactions),
      };
    }
    return { score: 3, absTier: null, reason: '', plain: '' };
  }

  function explainValuation(r) {
    const parts = [];
    if (typeof r.evSales === 'number') parts.push('FY1 EV/Sales ' + r.evSales.toFixed(1) + 'x');
    if (typeof r.evFcf === 'number') parts.push('FY1 EV/FCF ' + r.evFcf.toFixed(1) + 'x');
    if (typeof r.forwardPE === 'number') parts.push('Fwd P/E ' + r.forwardPE.toFixed(1) + 'x');
    return parts.length ? 'At ' + parts.join(', ') + '.' : 'Valuation data sparse.';
  }
  function explainGrowth(r) {
    if (typeof r.revenueGrowth !== 'number') return 'Revenue growth not available.';
    const v = r.revenueGrowth;
    if (v >= 30) return 'Hyper-growth (' + v.toFixed(0) + '% YoY) — multiple has to be earned.';
    if (v >= 20) return 'Strong growth (' + v.toFixed(0) + '% YoY) for a software/internet name.';
    if (v >= 10) return 'Mid-growth (' + v.toFixed(0) + '% YoY) — durability matters more than reacceleration story.';
    if (v >= 0) return 'Low-single-digit growth (' + v.toFixed(0) + '%) — value lens applies.';
    return 'Contracting (' + v.toFixed(0) + '%).';
  }
  function explainProfitability(r) {
    const parts = [];
    if (typeof r.fcfMargin === 'number') parts.push(r.fcfMargin.toFixed(0) + '% FCF margin');
    if (typeof r.operatingMargin === 'number') parts.push(r.operatingMargin.toFixed(0) + '% op margin');
    if (typeof r.ruleOf40 === 'number') parts.push('Rule of 40 = ' + r.ruleOf40.toFixed(0));
    return parts.length ? parts.join(', ') + '.' : 'Profitability fields not available.';
  }
  function explainMomentum(r) {
    const parts = [];
    if (typeof r.m3 === 'number') parts.push('3M ' + r.m3.toFixed(0) + '%');
    if (typeof r.y1 === 'number') parts.push('1Y ' + r.y1.toFixed(0) + '%');
    return parts.length ? parts.join(', ') + '.' : 'Return data not available.';
  }
  function explainQuality(r, intel, reactions) {
    const parts = [];
    if (reactions && reactions.length >= 3) {
      const beats = reactions.filter(x => x.move_pct > 0).length;
      parts.push((beats / reactions.length * 100).toFixed(0) + '% positive next-day reactions over last ' + reactions.length + ' prints');
    }
    if (intel && intel.tone_drift && intel.tone_drift.current_tone) parts.push('tone: ' + intel.tone_drift.current_tone);
    if (typeof r.fcfMargin === 'number') parts.push(r.fcfMargin.toFixed(0) + '% FCF margin');
    return parts.length ? parts.join(' · ') + '.' : 'Quality signal sparse.';
  }

  function renderScorecard(body, tickers, rows) {
    const results = {};
    tickers.forEach((t, i) => {
      results[t] = { pillars: {}, total: 0, absTotal: 0, absN: 0 };
      PILLAR_ORDER.forEach(p => {
        const s = pillarScore(p, rows, i);
        results[t].pillars[p] = s;
        results[t].total += s.score * WEIGHTS[p];
        if (s.absTier != null) { results[t].absTotal += s.absTier * WEIGHTS[p]; results[t].absN += WEIGHTS[p]; }
      });
    });

    let html = `<div class="cmp-scorecard">
      <div class="cmp-scorecard-legend">
        <span class="cmp-weight">Growth 30% \u00b7 Quality 25% \u00b7 Profitability 20% \u00b7 Valuation 15% \u00b7 Momentum 10%</span>
        <span class="cmp-weight-note">Peer-relative (1\u20135 vs comp set) + absolute tier (1\u20135 vs software/internet thresholds).</span>
      </div>
      <table class="cmp-scorecard-table">
        <thead>
          <tr>
            <th class="cmp-fund-metric">Pillar (weight)</th>
            ${tickers.map(t => `<th>${t}</th>`).join('')}
          </tr>
        </thead>
        <tbody>`;
    PILLAR_ORDER.forEach(p => {
      html += `<tr><td class="cmp-fund-metric"><span class="cmp-pillar-name">${PILLAR_LABELS[p]}</span><span class="cmp-pillar-weight">${Math.round(WEIGHTS[p] * 100)}%</span></td>`;
      tickers.forEach(t => {
        const s = results[t].pillars[p];
        const absChip = s.absTier != null
          ? `<span class="cmp-abs-chip cmp-abs-${s.absTier}" title="Absolute tier">${absLabel(s.absTier)}</span>`
          : '<span class="cmp-abs-chip cmp-abs-na" title="No absolute tier">abs n/a</span>';
        html += `<td class="cmp-score-cell">
          <div class="cmp-score-bar">
            <div class="cmp-score-fill cmp-score-${s.score}" style="width:${s.score * 20}%"></div>
            <span class="cmp-score-val">${s.score}/5</span>
          </div>
          <div class="cmp-score-chips">${absChip}</div>
          <div class="cmp-score-reason">${escapeHtml(s.plain || s.reason)}</div>
        </td>`;
      });
      html += '</tr>';
    });
    html += `<tr class="cmp-score-total-row"><td class="cmp-fund-metric">Weighted total</td>`;
    tickers.forEach(t => {
      const tot = results[t].total;
      const absTot = results[t].absN > 0 ? (results[t].absTotal / results[t].absN) : null;
      html += `<td class="cmp-score-cell">
        <strong class="cmp-score-total">${tot.toFixed(2)} / 5.00</strong>
        <div class="cmp-score-abs-total">${absTot != null ? 'abs ' + absTot.toFixed(2) + ' / 5.00' : 'abs n/a'}</div>
      </td>`;
    });
    html += '</tr></tbody></table></div>';
    body.innerHTML = html;
  }

  function absLabel(tier) {
    return ({ 5: 'abs excellent', 4: 'abs good', 3: 'abs okay', 2: 'abs weak', 1: 'abs poor' })[tier] || 'abs n/a';
  }

  // ======================= RADAR =======================
  function renderRadar(body, tickers, rows) {
    body.innerHTML = `
      <div class="cmp-radar-toolbar">
        <div class="cmp-radar-mode">
          <button type="button" class="cmp-radar-mode-btn${state.radarMode==='relative'?' active':''}" data-mode="relative">Peer-relative</button>
          <button type="button" class="cmp-radar-mode-btn${state.radarMode==='absolute'?' active':''}" data-mode="absolute">Absolute</button>
        </div>
        <span class="cmp-radar-mode-hint">${state.radarMode === 'relative' ? 'Each axis ranks the comp set 1\u20135.' : 'Each axis is the absolute tier vs software/internet thresholds.'}</span>
      </div>
      <div class="cmp-winner-strip" id="cmp-winner-strip"></div>
      <div class="cmp-radar-wrap"><canvas id="cmp-radar-canvas"></canvas></div>
      <div class="cmp-radar-detail" id="cmp-radar-detail"></div>
    `;
    body.querySelectorAll('.cmp-radar-mode-btn').forEach(b => {
      b.addEventListener('click', () => {
        state.radarMode = b.dataset.mode;
        renderRadar(body, tickers, rows);
      });
    });

    const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ec4899'];
    const scoreSet = {};
    tickers.forEach((t, i) => {
      scoreSet[t] = PILLAR_ORDER.map(p => {
        const s = pillarScore(p, rows, i);
        const v = state.radarMode === 'absolute' ? (s.absTier == null ? 0 : s.absTier) : s.score;
        return { pillar: p, value: v, reason: s.reason, plain: s.plain };
      });
    });

    // Winner-by-category strip
    const stripEl = body.querySelector('#cmp-winner-strip');
    let stripHtml = '';
    PILLAR_ORDER.forEach((p, i) => {
      const ranked = tickers.map((t, idx) => ({ t, v: scoreSet[t][i].value })).filter(x => x.v > 0);
      ranked.sort((a, b) => b.v - a.v);
      const winner = ranked.length ? ranked[0].t : null;
      stripHtml += `<div class="cmp-winner-cell"><div class="cmp-winner-pillar">${PILLAR_LABELS[p]}</div>
        <div class="cmp-winner-name">${winner || '\u2014'}</div>
        <div class="cmp-winner-score">${winner ? ranked[0].v + '/5' : ''}</div></div>`;
    });
    stripEl.innerHTML = stripHtml;

    const datasets = tickers.map((t, i) => ({
      label: t,
      data: scoreSet[t].map(s => s.value),
      borderColor: COLORS[i % COLORS.length],
      backgroundColor: COLORS[i % COLORS.length] + '33',
      borderWidth: 2,
      pointRadius: 3,
      pointBackgroundColor: COLORS[i % COLORS.length]
    }));

    const ctx = body.querySelector('#cmp-radar-canvas').getContext('2d');
    if (state.radarChart) { state.radarChart.destroy(); state.radarChart = null; }
    state.radarChart = new Chart(ctx, {
      type: 'radar',
      data: { labels: PILLAR_ORDER.map(p => PILLAR_LABELS[p]), datasets },
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
            bodyColor: '#cbd5e1',
            callbacks: {
              label: function (ctx) {
                const t = ctx.dataset.label;
                const p = PILLAR_ORDER[ctx.dataIndex];
                const s = scoreSet[t][ctx.dataIndex];
                return [`${t} \u2014 ${PILLAR_LABELS[p]}: ${s.value}/5`, s.plain || s.reason];
              }
            }
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

    // Detail table: full rationale per ticker per axis
    const detailEl = body.querySelector('#cmp-radar-detail');
    let dh = '<table class="cmp-radar-detail-table"><thead><tr><th></th>';
    tickers.forEach(t => dh += `<th>${t}</th>`);
    dh += '</tr></thead><tbody>';
    PILLAR_ORDER.forEach((p, i) => {
      dh += `<tr><td class="cmp-fund-metric">${PILLAR_LABELS[p]}</td>`;
      tickers.forEach(t => {
        const s = scoreSet[t][i];
        dh += `<td><div class="cmp-radar-cell"><span class="cmp-radar-score">${s.value}/5</span><span class="cmp-radar-plain">${escapeHtml(s.plain || s.reason)}</span></div></td>`;
      });
      dh += '</tr>';
    });
    dh += '</tbody></table>';
    detailEl.innerHTML = dh;
  }

  // ======================= EARNINGS INTEL TAB =======================
  function renderIntel(body, tickers, rows) {
    const intelMap = {};
    tickers.forEach(t => { intelMap[t] = getIntel(t); });

    const anyIntel = tickers.some(t => !!intelMap[t]);
    if (!anyIntel) {
      body.innerHTML = '<div class="cmp-empty">No earnings intelligence loaded. The Earnings Intel cache (earnings_intel.json) hasn\u2019t hydrated for any of the selected names.</div>';
      return;
    }

    let html = '<div class="cmp-intel-shell">';
    // 1. Status strip
    html += '<div class="cmp-intel-strip">';
    tickers.forEach(t => {
      const intel = intelMap[t];
      if (!intel) {
        html += `<div class="cmp-intel-strip-cell cmp-intel-empty"><div class="cmp-intel-strip-tick">${t}</div><div class="cmp-intel-strip-detail">No intel</div></div>`;
        return;
      }
      const state_ = intel.state || 'n/a';
      const stateTone = state_ === 'post_earnings' ? 'post' : state_ === 'pre_earnings' ? 'pre' : 'idle';
      html += `<div class="cmp-intel-strip-cell">
        <div class="cmp-intel-strip-tick">${t}</div>
        <div class="cmp-intel-strip-state cmp-intel-state-${stateTone}">${escapeHtml(state_.replace('_', ' '))}</div>
        <div class="cmp-intel-strip-detail">Last: ${intel.last_earnings_date || '\u2014'} \u00b7 Next: ${intel.next_earnings_date || '\u2014'}</div>
      </div>`;
    });
    html += '</div>';

    // 2. Last reaction comparison
    html += '<div class="cmp-section-head"><h3>Most recent post-earnings reaction</h3></div>';
    html += '<div class="cmp-intel-reactions">';
    tickers.forEach(t => {
      const intel = intelMap[t];
      if (!intel) {
        html += `<div class="cmp-intel-react-card cmp-intel-empty"><div class="cmp-intel-react-tick">${t}</div><div class="cmp-intel-empty-msg">No intel</div></div>`;
        return;
      }
      const rv = intel.post_earnings_review || {};
      const move = rv.stock_reaction_pct;
      const moveCls = (typeof move === 'number') ? (move >= 0 ? 'positive' : 'negative') : 'na';
      html += `<div class="cmp-intel-react-card">
        <div class="cmp-intel-react-head"><span class="cmp-intel-react-tick">${t}</span>${typeof move === 'number' ? `<span class="cmp-intel-react-move ${moveCls}">${move >= 0 ? '+' : ''}${move.toFixed(1)}%</span>` : ''}</div>
        ${rv.what_happened_headline ? `<div class="cmp-intel-react-headline">${escapeHtml(rv.what_happened_headline.slice(0, 280))}</div>` : '<div class="cmp-intel-react-headline cmp-intel-empty-msg">No recent reaction summary.</div>'}
        ${Array.isArray(rv.takeaways_bullets) && rv.takeaways_bullets.length ? `<ul class="cmp-intel-bullets">${rv.takeaways_bullets.slice(0, 4).map(b => `<li>${escapeHtml(b)}</li>`).join('')}</ul>` : ''}
      </div>`;
    });
    html += '</div>';

    // 3. Bottom-line head-to-head
    html += '<div class="cmp-section-head"><h3>Bottom line into next print</h3></div>';
    html += '<div class="cmp-intel-bottomline">';
    tickers.forEach(t => {
      const intel = intelMap[t];
      if (!intel || !intel.bottom_line) {
        html += `<div class="cmp-intel-bl-card cmp-intel-empty"><div class="cmp-intel-bl-tick">${t}</div><div class="cmp-intel-empty-msg">No bottom line.</div></div>`;
        return;
      }
      const tone = (intel.tone_drift && intel.tone_drift.current_tone) || '';
      html += `<div class="cmp-intel-bl-card">
        <div class="cmp-intel-bl-tick">${t}</div>
        <div class="cmp-intel-bl-text">${escapeHtml(intel.bottom_line)}</div>
        ${tone ? `<div class="cmp-intel-bl-tone">Tone: ${escapeHtml(tone)}</div>` : ''}
      </div>`;
    });
    html += '</div>';

    // 4. Bull / bear shorthand
    html += '<div class="cmp-section-head"><h3>Bull / bear shorthand</h3></div>';
    html += '<table class="cmp-intel-cases"><thead><tr><th></th>';
    tickers.forEach(t => { html += `<th>${t}</th>`; });
    html += '</tr></thead><tbody>';
    ['bull_case', 'base_case', 'bear_case'].forEach(c => {
      const label = c === 'bull_case' ? 'Bull' : c === 'base_case' ? 'Base' : 'Bear';
      html += `<tr><td class="cmp-fund-metric">${label}</td>`;
      tickers.forEach(t => {
        const intel = intelMap[t];
        const obj = intel && intel[c];
        if (!obj) { html += '<td>\u2014</td>'; return; }
        const head = obj.thesis_headline || obj.setup_headline || '\u2014';
        const drivers = (obj.pushes_higher || []).concat(obj.pushes_lower || []).slice(0, 3);
        html += `<td><div class="cmp-intel-case-head">${escapeHtml(head)}</div>${drivers.length ? `<ul class="cmp-intel-case-list">${drivers.map(d => `<li>${escapeHtml(d.text || '')}</li>`).join('')}</ul>` : ''}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';

    html += '</div>';
    body.innerHTML = html;
  }

  // ======================= EXPORT / SNAPSHOT =======================
  function exportAsMarkdown() {
    const tickers = state.popupTicker || [];
    const rows = tickers.map(t => augmentRow(t, (global.tickerData || {})[t] || { ticker: t }));
    const md = buildMarkdown(tickers, rows);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(md).then(() => flashAction('Copied as Markdown'), () => flashAction('Clipboard blocked', 'error'));
    } else {
      flashAction('Clipboard unavailable', 'error');
    }
  }

  function buildMarkdown(tickers, rows) {
    const lines = [];
    lines.push('# Compare: ' + tickers.join(' vs '));
    lines.push('');
    lines.push('_Generated ' + new Date().toISOString() + '_');
    lines.push('');
    // Hero table
    lines.push('| Metric | ' + tickers.join(' | ') + ' |');
    lines.push('| --- | ' + tickers.map(() => '---').join(' | ') + ' |');
    const fields = [
      ['Name',          r => r.name || ''],
      ['Price',         r => fmt.priceTxt(r.price)],
      ['Market cap',    r => fmt.large(r.marketCap)],
      ['FY1 EV/Sales',  r => fmt.mult(r.evSales)],
      ['FY1 EV/FCF',    r => fmt.mult(r.evFcf)],
      ['Rev growth',    r => fmt.pct(r.revenueGrowth)],
      ['FCF margin',    r => fmt.pct(r.fcfMargin)],
      ['Rule of 40',    r => fmt.num(r.ruleOf40, 1)],
      ['1Y',            r => fmt.pct(r.y1)],
      ['YTD',           r => fmt.pct(r.ytd)],
      ['Target upside', r => fmt.pct(r.upsideToTarget)],
    ];
    fields.forEach(([label, fn]) => {
      lines.push('| ' + label + ' | ' + rows.map(r => fn(r)).join(' | ') + ' |');
    });
    lines.push('');
    // Scorecard
    lines.push('## Scorecard (peer-relative 1-5, absolute tier in parens)');
    lines.push('');
    lines.push('| Pillar | ' + tickers.join(' | ') + ' |');
    lines.push('| --- | ' + tickers.map(() => '---').join(' | ') + ' |');
    PILLAR_ORDER.forEach(p => {
      const cells = tickers.map((t, i) => {
        const s = pillarScore(p, rows, i);
        return s.score + '/5 (' + (s.absTier != null ? 'abs ' + s.absTier : 'abs n/a') + ')';
      });
      lines.push('| ' + PILLAR_LABELS[p] + ' | ' + cells.join(' | ') + ' |');
    });
    lines.push('');
    // AI Read if cached
    if (global.SignalCompareAI && typeof global.SignalCompareAI.loadSaved === 'function') {
      const saved = global.SignalCompareAI.loadSaved(tickers);
      if (saved && saved.parsed) {
        const p = saved.parsed;
        lines.push('## AI Read');
        lines.push('');
        if (p.valuation) lines.push('**Valuation:** ' + p.valuation);
        if (p.growth) lines.push('');
        if (p.growth) lines.push('**Growth:** ' + p.growth);
        if (p.profitability) lines.push('');
        if (p.profitability) lines.push('**Profitability:** ' + p.profitability);
        if (p.key_debate) lines.push('');
        if (p.key_debate) lines.push('**Key debate:** ' + p.key_debate);
        if (p.strongest_setup_today && p.strongest_setup_today.ticker) {
          lines.push('');
          lines.push('**Strongest setup:** ' + p.strongest_setup_today.ticker + ' \u2014 ' + (p.strongest_setup_today.why || ''));
        }
      }
    }
    return lines.join('\n');
  }

  function saveSnapshot() {
    const tickers = state.popupTicker || [];
    const rows = tickers.map(t => augmentRow(t, (global.tickerData || {})[t] || { ticker: t }));
    const md = buildMarkdown(tickers, rows);
    try {
      const key = 'signalai_compare_snapshots_v1';
      const existing = JSON.parse(localStorage.getItem(key) || '[]');
      existing.unshift({ tickers: tickers.slice(), saved_at: new Date().toISOString(), markdown: md });
      // keep the latest 25
      localStorage.setItem(key, JSON.stringify(existing.slice(0, 25)));
      flashAction('Snapshot saved');
    } catch (e) {
      flashAction('Snapshot save failed', 'error');
    }
  }

  function flashAction(msg, tone) {
    const header = document.querySelector('.compare-header-actions');
    if (!header) return;
    const note = document.createElement('span');
    note.className = 'cmp-flash-note' + (tone === 'error' ? ' cmp-flash-error' : '');
    note.textContent = msg;
    header.appendChild(note);
    setTimeout(() => { try { note.remove(); } catch (e) {} }, 1800);
  }

  // ======================= HELPERS =======================
  function getIntel(t) {
    const d = global._earningsIntelData || global.earningsIntelData;
    return d && d.tickers && d.tickers[t] ? d.tickers[t] : null;
  }
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // ======================= FORMATTERS =======================
  const fmt = {
    price: (v) => (typeof global.formatPrice === 'function' ? global.formatPrice(v) : (v == null ? '\u2014' : '$' + v.toFixed(2))),
    priceTxt: (v) => (v == null ? '—' : '$' + v.toFixed(2)),
    large: (v) => (typeof global.formatLargeNumber === 'function' ? global.formatLargeNumber(v) : (v == null ? '\u2014' : String(v))),
    mult: (v) => (typeof global.formatMultiple === 'function' ? global.formatMultiple(v) : (v == null ? '\u2014' : v.toFixed(1) + 'x')),
    pct: (v) => (typeof global.formatPercent === 'function' ? global.formatPercent(v) : (v == null ? '\u2014' : v.toFixed(1) + '%')),
    pctWithFlag: (v) => {
      if (v == null || Number.isNaN(v)) return '\u2014';
      const str = v.toFixed(1) + '%';
      if (Math.abs(v) >= 500) {
        return `<span title="Extreme return \u2014 may reflect recent spinoff, IPO, or structural corporate action. Cross-reference before comparing." class="cmp-pct-flagged">${str}<sup>!</sup></span>`;
      }
      return str;
    },
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
    refreshTrayVisibility: function () { updateTray(); }
  };

  // Wire toggle button when DOM ready
  function attach() {
    hydrateSelection();
    hydrateTab();
    const btn = document.getElementById('compare-toggle-btn');
    if (btn) {
      btn.addEventListener('click', toggleMode);
      renderMode();
    }
    ensureTray();
    if (state.selected.size > 0) {
      state.mode = true;
      renderMode();
      updateTray();
      if (typeof global.renderTable === 'function') global.renderTable();
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }

})(window);
