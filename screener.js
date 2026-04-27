/* ===== SCREENER.JS — Screening/Filtering, Alerts, Export, Performance Viz ===== */

// ============================================================
// 1. SCREENING & FILTERING
// ============================================================

(function initScreener() {
  // State
  let screenerFilters = Storage.get('screener_filters') || [];
  let screenerOpen = false;
  let activeFilters = []; // currently applied

  // Available filter columns — match data keys from tickerData
  const FILTER_COLUMNS = [
    { key: 'subsector', label: 'Subsector', type: 'select' },
    { key: 'marketCap', label: 'Market Cap', type: 'range', format: 'largeMoney' },
    { key: 'ev', label: 'Enterprise Value', type: 'range', format: 'largeMoney' },
    { key: 'evSales', label: 'FY1 EV/Sales', type: 'range', format: 'multiple' },
    { key: 'evFcf', label: 'FY1 EV/FCF', type: 'range', format: 'multiple' },
    { key: 'forwardPE', label: 'Forward P/E', type: 'range', format: 'multiple' },
    { key: 'revenueGrowth', label: 'Revenue Growth %', type: 'range', format: 'percent' },
    { key: 'operatingMargins', label: 'Operating Margin %', type: 'range', format: 'percent' },
    { key: 'ytd', label: 'YTD %', type: 'range', format: 'percent' },
    { key: 'd1', label: '1D %', type: 'range', format: 'percent' },
    { key: 'w1', label: '1W %', type: 'range', format: 'percent' },
    { key: 'm1', label: '1M %', type: 'range', format: 'percent' },
    { key: 'm3', label: '3M %', type: 'range', format: 'percent' },
    { key: 'y1', label: '1Y %', type: 'range', format: 'percent' },
    { key: 'price', label: 'Price', type: 'range', format: 'money' },
  ];

  // Inject the screener toolbar. Historically this anchored to
  // `.public-section .section-header-bar`, but in the current SPA layout
  // that selector no longer exists — the screener now lives on a
  // dedicated [data-surface="screener"] surface. To keep this IIFE's
  // logic untouched, we always build the bar into a hidden stash node
  // and let `window.ScreenerModule.init(containerEl)` relocate it.
  const sectionHeader = document.querySelector('.public-section .section-header-bar');
  let screenerStash = null;
  if (!sectionHeader) {
    screenerStash = document.createElement('div');
    screenerStash.id = 'screener-stash';
    screenerStash.style.display = 'none';
    document.body.appendChild(screenerStash);
  }

  // Create screener bar
  const screenerBar = document.createElement('div');
  screenerBar.className = 'screener-bar';
  screenerBar.id = 'screener-bar';
  screenerBar.innerHTML = `
    <div class="screener-controls">
      <button class="btn-sm btn-ghost screener-toggle" id="screener-toggle">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M1.5 2h13l-5 6.5V14l-3-1.5V8.5L1.5 2z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
        Screen
      </button>
      <div class="screener-active-pills" id="screener-active-pills"></div>
      <button class="screener-clear-all" id="screener-clear-all" style="display:none;">Clear All</button>
    </div>
    <div class="screener-panel" id="screener-panel" style="display:none;">
      <div class="screener-add-row">
        <select class="screener-col-select" id="screener-col-select">
          <option value="">+ Add filter...</option>
        </select>
      </div>
      <div class="screener-filter-rows" id="screener-filter-rows"></div>
      <div class="screener-results-count" id="screener-results-count"></div>
    </div>
  `;

  // Insert after section header, or into hidden stash for relocation by ScreenerModule.
  if (sectionHeader) {
    sectionHeader.parentNode.insertBefore(screenerBar, sectionHeader.nextSibling);
  } else {
    screenerStash.appendChild(screenerBar);
  }

  // Populate column select
  const colSelect = document.getElementById('screener-col-select');
  FILTER_COLUMNS.forEach(col => {
    const opt = document.createElement('option');
    opt.value = col.key;
    opt.textContent = col.label;
    colSelect.appendChild(opt);
  });

  // Toggle panel
  document.getElementById('screener-toggle').addEventListener('click', () => {
    screenerOpen = !screenerOpen;
    document.getElementById('screener-panel').style.display = screenerOpen ? 'block' : 'none';
    document.getElementById('screener-toggle').classList.toggle('active', screenerOpen);
  });

  // Add filter
  colSelect.addEventListener('change', () => {
    const key = colSelect.value;
    if (!key) return;
    const col = FILTER_COLUMNS.find(c => c.key === key);
    if (!col) return;
    // Don't duplicate
    if (activeFilters.some(f => f.key === key)) { colSelect.value = ''; return; }
    addFilterRow(col);
    colSelect.value = '';
  });

  // Clear all
  document.getElementById('screener-clear-all').addEventListener('click', () => {
    activeFilters = [];
    renderFilterRows();
    applyFilters();
  });

  function addFilterRow(col, existingValues) {
    const filter = { key: col.key, label: col.label, type: col.type, format: col.format };
    if (col.type === 'range') {
      filter.min = existingValues?.min ?? '';
      filter.max = existingValues?.max ?? '';
    } else if (col.type === 'select') {
      filter.values = existingValues?.values ?? [];
    }
    activeFilters.push(filter);
    renderFilterRows();
  }

  function renderFilterRows() {
    const container = document.getElementById('screener-filter-rows');
    const pills = document.getElementById('screener-active-pills');
    const clearBtn = document.getElementById('screener-clear-all');
    container.innerHTML = '';
    pills.innerHTML = '';

    const hasActive = activeFilters.some(f => {
      if (f.type === 'range') return f.min !== '' || f.max !== '';
      if (f.type === 'select') return f.values.length > 0;
      return false;
    });
    clearBtn.style.display = hasActive ? 'inline-block' : 'none';

    activeFilters.forEach((filter, idx) => {
      // Panel row
      const row = document.createElement('div');
      row.className = 'screener-filter-row';

      if (filter.type === 'range') {
        row.innerHTML = `
          <span class="screener-filter-label">${filter.label}</span>
          <div class="screener-range-inputs">
            <input type="number" step="any" placeholder="Min" class="screener-input" data-idx="${idx}" data-field="min" value="${filter.min}">
            <span class="screener-range-to">to</span>
            <input type="number" step="any" placeholder="Max" class="screener-input" data-idx="${idx}" data-field="max" value="${filter.max}">
          </div>
          <button class="screener-remove-filter" data-idx="${idx}">&times;</button>
        `;
      } else if (filter.type === 'select') {
        // Get unique values
        const values = getUniqueValues(filter.key);
        const checkboxes = values.map(v => {
          const checked = filter.values.includes(v) ? 'checked' : '';
          return `<label class="screener-checkbox-label"><input type="checkbox" ${checked} data-idx="${idx}" data-val="${v}" class="screener-checkbox"> ${v}</label>`;
        }).join('');
        row.innerHTML = `
          <span class="screener-filter-label">${filter.label}</span>
          <div class="screener-checkboxes">${checkboxes}</div>
          <button class="screener-remove-filter" data-idx="${idx}">&times;</button>
        `;
      }

      container.appendChild(row);

      // Active pill (only if filter has values)
      const isActive = filter.type === 'range' ? (filter.min !== '' || filter.max !== '') :
                        filter.type === 'select' ? filter.values.length > 0 : false;
      if (isActive) {
        const pill = document.createElement('span');
        pill.className = 'screener-pill';
        let text = filter.label + ': ';
        if (filter.type === 'range') {
          if (filter.min !== '' && filter.max !== '') text += `${filter.min} - ${filter.max}`;
          else if (filter.min !== '') text += `>= ${filter.min}`;
          else text += `<= ${filter.max}`;
        } else if (filter.type === 'select') {
          text += filter.values.length <= 2 ? filter.values.join(', ') : `${filter.values.length} selected`;
        }
        pill.innerHTML = `${text} <span class="pill-close" data-idx="${idx}">&times;</span>`;
        pills.appendChild(pill);
      }
    });

    // Bind events
    container.querySelectorAll('.screener-input').forEach(input => {
      input.addEventListener('input', (e) => {
        const idx = parseInt(e.target.dataset.idx);
        const field = e.target.dataset.field;
        activeFilters[idx][field] = e.target.value === '' ? '' : parseFloat(e.target.value);
        applyFilters();
        renderPills();
      });
    });

    container.querySelectorAll('.screener-checkbox').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const idx = parseInt(e.target.dataset.idx);
        const val = e.target.dataset.val;
        if (e.target.checked) {
          if (!activeFilters[idx].values.includes(val)) activeFilters[idx].values.push(val);
        } else {
          activeFilters[idx].values = activeFilters[idx].values.filter(v => v !== val);
        }
        applyFilters();
        renderPills();
      });
    });

    container.querySelectorAll('.screener-remove-filter').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const idx = parseInt(e.target.dataset.idx);
        activeFilters.splice(idx, 1);
        renderFilterRows();
        applyFilters();
      });
    });

    pills.querySelectorAll('.pill-close').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(e.target.dataset.idx);
        if (activeFilters[idx].type === 'range') {
          activeFilters[idx].min = '';
          activeFilters[idx].max = '';
        } else {
          activeFilters[idx].values = [];
        }
        renderFilterRows();
        applyFilters();
      });
    });
  }

  function renderPills() {
    const pills = document.getElementById('screener-active-pills');
    const clearBtn = document.getElementById('screener-clear-all');
    pills.innerHTML = '';
    let hasActive = false;
    activeFilters.forEach((filter, idx) => {
      const isActive = filter.type === 'range' ? (filter.min !== '' || filter.max !== '') :
                        filter.type === 'select' ? filter.values.length > 0 : false;
      if (isActive) {
        hasActive = true;
        const pill = document.createElement('span');
        pill.className = 'screener-pill';
        let text = filter.label + ': ';
        if (filter.type === 'range') {
          if (filter.min !== '' && filter.max !== '') text += `${filter.min} - ${filter.max}`;
          else if (filter.min !== '') text += `>= ${filter.min}`;
          else text += `<= ${filter.max}`;
        } else if (filter.type === 'select') {
          text += filter.values.length <= 2 ? filter.values.join(', ') : `${filter.values.length} selected`;
        }
        pill.innerHTML = `${text} <span class="pill-close" data-idx="${idx}">&times;</span>`;
        pills.appendChild(pill);
      }
    });
    clearBtn.style.display = hasActive ? 'inline-block' : 'none';
    // Re-bind pill close buttons
    pills.querySelectorAll('.pill-close').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(e.target.dataset.idx);
        if (activeFilters[idx].type === 'range') {
          activeFilters[idx].min = '';
          activeFilters[idx].max = '';
        } else {
          activeFilters[idx].values = [];
        }
        renderFilterRows();
        applyFilters();
      });
    });
  }

  function getUniqueValues(key) {
    const vals = new Set();
    if (typeof tickerList === 'undefined') return [];
    tickerList.forEach(t => {
      const d = tickerData[t] || {};
      let v;
      if (key === 'subsector') v = d.subsector || getSubsector(t);
      else v = d[key];
      if (v != null && v !== '' && v !== 'N/A') vals.add(v);
    });
    return [...vals].sort();
  }

  function applyFilters() {
    if (typeof tickerList === 'undefined') return;
    const rows = document.querySelectorAll('#watchlist-body tr');
    let matchCount = 0;
    let totalCount = 0;

    // Build a set of tickers that pass filters
    const passSet = new Set();
    const failSet = new Set();

    tickerList.forEach(ticker => {
      const d = tickerData[ticker] || {};
      let pass = true;

      for (const filter of activeFilters) {
        let val;
        if (filter.key === 'subsector') {
          val = d.subsector || getSubsector(ticker);
        } else {
          val = d[filter.key];
        }

        if (filter.type === 'range') {
          if (filter.min !== '' && filter.min !== null) {
            if (val == null || isNaN(val) || val < filter.min) { pass = false; break; }
          }
          if (filter.max !== '' && filter.max !== null) {
            if (val == null || isNaN(val) || val > filter.max) { pass = false; break; }
          }
        } else if (filter.type === 'select' && filter.values.length > 0) {
          if (!filter.values.includes(val)) { pass = false; break; }
        }
      }

      if (pass) passSet.add(ticker);
      else failSet.add(ticker);
    });

    // Any active filter?
    const hasAnyFilter = activeFilters.some(f => {
      if (f.type === 'range') return f.min !== '' || f.max !== '';
      if (f.type === 'select') return f.values.length > 0;
      return false;
    });

    // Apply visibility to table rows
    rows.forEach(tr => {
      if (tr.classList.contains('subsector-header')) return;
      const tickerCell = tr.querySelector('.cell-ticker');
      if (!tickerCell) return;
      totalCount++;
      const ticker = tickerCell.dataset.ticker;

      if (!hasAnyFilter) {
        tr.classList.remove('screener-hidden');
        tr.classList.remove('screener-highlight');
        matchCount++;
      } else if (passSet.has(ticker)) {
        tr.classList.remove('screener-hidden');
        tr.classList.add('screener-highlight');
        matchCount++;
      } else {
        tr.classList.add('screener-hidden');
        tr.classList.remove('screener-highlight');
      }
    });

    // Show/hide subsector headers based on visible children
    if (hasAnyFilter) {
      rows.forEach(tr => {
        if (!tr.classList.contains('subsector-header')) return;
        let hasVisible = false;
        let next = tr.nextElementSibling;
        while (next && !next.classList.contains('subsector-header')) {
          if (!next.classList.contains('screener-hidden') && !next.classList.contains('row-hidden')) {
            hasVisible = true;
            break;
          }
          next = next.nextElementSibling;
        }
        tr.style.display = hasVisible ? '' : 'none';
      });
    } else {
      rows.forEach(tr => {
        if (tr.classList.contains('subsector-header')) tr.style.display = '';
      });
    }

    // Update results count
    const resultsEl = document.getElementById('screener-results-count');
    if (hasAnyFilter) {
      resultsEl.textContent = `${matchCount} of ${tickerList.length} companies match`;
      resultsEl.style.display = 'block';
    } else {
      resultsEl.style.display = 'none';
    }

    // Save
    Storage.set('screener_filters', activeFilters.map(f => ({
      key: f.key, type: f.type, min: f.min, max: f.max, values: f.values
    })));
  }

  // Expose for re-application after renders
  window._applyScreenerFilters = applyFilters;

  // Public API for the preset save/load bar (screener-presets.js)
  window.SignalScreener = window.SignalScreener || {};
  window.SignalScreener.FILTER_COLUMNS = FILTER_COLUMNS;
  window.SignalScreener.getFilters = function () {
    // Return a deep-cloned snapshot of the currently active filters
    return activeFilters.map(function (f) {
      return {
        key: f.key, type: f.type, format: f.format, label: f.label,
        min: f.min, max: f.max,
        values: Array.isArray(f.values) ? f.values.slice() : undefined
      };
    });
  };
  window.SignalScreener.loadFilters = function (preset) {
    // Replace the active filter set with the given preset and re-render.
    activeFilters = [];
    renderFilterRows();
    (preset || []).forEach(function (sf) {
      var col = FILTER_COLUMNS.find(function (c) { return c.key === sf.key; });
      if (col) addFilterRow(col, sf);
    });
    applyFilters();
    // Ensure panel is visible so the user sees what got loaded
    var panel = document.getElementById('screener-panel');
    if (panel) {
      panel.style.display = 'block';
      screenerOpen = true;
      var toggle = document.getElementById('screener-toggle');
      if (toggle) toggle.classList.add('active');
    }
  };
  window.SignalScreener.clearFilters = function () {
    activeFilters = [];
    renderFilterRows();
    applyFilters();
  };

  // Load saved filters
  const saved = Storage.get('screener_filters');
  if (saved && saved.length > 0) {
    saved.forEach(sf => {
      const col = FILTER_COLUMNS.find(c => c.key === sf.key);
      if (col) {
        addFilterRow(col, sf);
      }
    });
    // Check if any saved filter has values
    const hasSavedActive = saved.some(f => {
      if (f.type === 'range') return (f.min !== '' && f.min != null) || (f.max !== '' && f.max != null);
      if (f.type === 'select') return f.values && f.values.length > 0;
      return false;
    });
    if (hasSavedActive) {
      setTimeout(() => applyFilters(), 500);
    }
  }

  // Re-apply filters after each table render
  const origRender = window.renderTable || renderTable;
  if (typeof renderTable !== 'undefined') {
    const _prevRender = renderTable;
    renderTable = function() {
      _prevRender();
      if (window._applyScreenerFilters) window._applyScreenerFilters();
    };
  }
})();


// ============================================================
// 2. ALERTS SYSTEM
// ============================================================

(function initAlerts() {
  // GUARD: alerts.js is the canonical Alerts tab and index.html already ships
  // a static `.tab-btn[data-tab="alerts"]` and `#tab-alerts` pane. This legacy
  // IIFE used to inject a DUPLICATE Alerts tab button + pane at runtime. Short
  // circuit when the canonical tab exists so we don't render two Alerts tabs.
  const existingAlertBtn = document.querySelector('.tab-btn[data-tab="alerts"]');
  const existingAlertPane = document.getElementById('tab-alerts');
  if (existingAlertBtn || existingAlertPane) {
    // Leave canonical alerts.js in charge; nothing to do here.
    return;
  }

  // Alerts are stored in localStorage, checked on each data refresh
  let alerts = Storage.get('signalstack_alerts') || [];
  let alertHistory = Storage.get('signalstack_alert_history') || [];

  const ALERT_TYPES = [
    { key: 'price_above', label: 'Price above', unit: '$' },
    { key: 'price_below', label: 'Price below', unit: '$' },
    { key: 'day_change_above', label: '1D change above', unit: '%' },
    { key: 'day_change_below', label: '1D change below', unit: '%' },
    { key: 'week_change_above', label: '1W change above', unit: '%' },
    { key: 'week_change_below', label: '1W change below', unit: '%' },
    { key: 'month_change_above', label: '1M change above', unit: '%' },
    { key: 'month_change_below', label: '1M change below', unit: '%' },
  ];

  // Add Alerts tab button to tab bar
  const tabBar = document.getElementById('tab-bar');
  if (!tabBar) return;

  const alertTabBtn = document.createElement('button');
  alertTabBtn.className = 'tab-btn';
  alertTabBtn.dataset.tab = 'alerts';
  alertTabBtn.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 1.5c-2.5 0-4 1.5-4 4v2.5L1.5 10v.5h11V10L11 8V5.5c0-2.5-1.5-4-4-4z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5.5 11.5a1.5 1.5 0 003 0" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
    Alerts
    <span class="alert-badge" id="alert-badge" style="display:none;">0</span>
  `;
  tabBar.appendChild(alertTabBtn);

  // Create alerts tab pane
  const main = document.querySelector('.main-content');
  const alertPane = document.createElement('div');
  alertPane.className = 'tab-pane';
  alertPane.id = 'tab-alerts';
  alertPane.innerHTML = `
    <section class="alerts-section">
      <div class="section-header-bar">
        <h2>Price Alerts</h2>
        <button class="btn-sm" id="add-alert-btn">+ New Alert</button>
      </div>
      <div class="alerts-description">
        Set alerts on price levels and performance thresholds. Alerts are checked on each data refresh and appear as notifications within the app.
      </div>
      <div class="alerts-active" id="alerts-active">
        <div class="alerts-empty" id="alerts-empty">No active alerts. Click "+ New Alert" to create one.</div>
      </div>
      <div class="alerts-history-section" id="alerts-history-section" style="display:none;">
        <h3 class="alerts-history-title">Triggered Alerts</h3>
        <div class="alerts-history" id="alerts-history"></div>
      </div>
    </section>
  `;
  main.appendChild(alertPane);

  // Create alert modal
  const alertModal = document.createElement('div');
  alertModal.className = 'popup-overlay';
  alertModal.id = 'alert-modal-overlay';
  alertModal.innerHTML = `
    <div class="popup-modal alert-modal">
      <button class="popup-close" id="alert-modal-close">&times;</button>
      <h2 style="margin-bottom:16px;color:#e5e7eb;">Create Alert</h2>
      <form id="alert-form" class="alert-form">
        <label>Ticker
          <input type="text" name="ticker" required placeholder="e.g. CRWD" autocomplete="off" spellcheck="false" class="alert-input" id="alert-ticker-input">
        </label>
        <label>Condition
          <select name="type" class="alert-input" id="alert-type-select">
            ${ALERT_TYPES.map(t => `<option value="${t.key}">${t.label}</option>`).join('')}
          </select>
        </label>
        <label>Value
          <div class="alert-value-row">
            <span class="alert-value-unit" id="alert-value-unit">$</span>
            <input type="number" step="any" name="value" required placeholder="0" class="alert-input" id="alert-value-input">
          </div>
        </label>
        <button type="submit" class="btn-primary alert-submit">Create Alert</button>
      </form>
    </div>
  `;
  document.body.appendChild(alertModal);

  // Wire up tab
  alertTabBtn.addEventListener('click', () => {
    // Deactivate all other tabs/panes
    tabBar.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    alertTabBtn.classList.add('active');
    alertPane.classList.add('active');
    Storage.set('active_tab', 'alerts');
    history.replaceState(null, '', '#alerts');
    renderAlerts();
  });

  // Ensure alerts pane hides when any other tab is clicked
  tabBar.querySelectorAll('.tab-btn:not([data-tab="alerts"])').forEach(btn => {
    btn.addEventListener('click', () => {
      alertTabBtn.classList.remove('active');
      alertPane.classList.remove('active');
    });
  });

  // Handle hash for alerts tab
  if (location.hash === '#alerts') {
    setTimeout(() => {
      tabBar.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      alertTabBtn.classList.add('active');
      alertPane.classList.add('active');
      renderAlerts();
    }, 100);
  }

  // Modal open/close
  document.getElementById('add-alert-btn').addEventListener('click', () => {
    alertModal.classList.add('active');
  });
  document.getElementById('alert-modal-close').addEventListener('click', () => {
    alertModal.classList.remove('active');
  });
  alertModal.addEventListener('click', (e) => {
    if (e.target === alertModal) alertModal.classList.remove('active');
  });

  // Update unit label when type changes
  document.getElementById('alert-type-select').addEventListener('change', (e) => {
    const type = ALERT_TYPES.find(t => t.key === e.target.value);
    document.getElementById('alert-value-unit').textContent = type ? type.unit : '$';
  });

  // Create alert
  document.getElementById('alert-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const form = e.target;
    const ticker = form.ticker.value.trim().toUpperCase();
    const type = form.type.value;
    const value = parseFloat(form.value.value);
    if (!ticker || isNaN(value)) return;

    const typeInfo = ALERT_TYPES.find(t => t.key === type);
    alerts.push({
      id: Date.now().toString(36),
      ticker,
      type,
      value,
      label: typeInfo?.label || type,
      unit: typeInfo?.unit || '',
      created: new Date().toISOString(),
      active: true
    });
    saveAlerts();
    renderAlerts();
    form.reset();
    alertModal.classList.remove('active');
  });

  function saveAlerts() {
    Storage.set('signalstack_alerts', alerts);
    Storage.set('signalstack_alert_history', alertHistory);
    updateBadge();
  }

  function updateBadge() {
    const badge = document.getElementById('alert-badge');
    const recent = alertHistory.filter(h => {
      const age = Date.now() - new Date(h.triggeredAt).getTime();
      return age < 24 * 60 * 60 * 1000; // Last 24h
    });
    if (recent.length > 0) {
      badge.textContent = recent.length;
      badge.style.display = 'inline-flex';
    } else {
      badge.style.display = 'none';
    }
  }

  function renderAlerts() {
    const container = document.getElementById('alerts-active');
    const emptyEl = document.getElementById('alerts-empty');
    const historySection = document.getElementById('alerts-history-section');
    const historyContainer = document.getElementById('alerts-history');

    if (alerts.length === 0) {
      emptyEl.style.display = 'block';
      container.innerHTML = '';
      container.appendChild(emptyEl);
    } else {
      emptyEl.style.display = 'none';
      container.innerHTML = alerts.map((alert, idx) => {
        const d = tickerData[alert.ticker] || {};
        const currentPrice = d.price != null ? '$' + d.price.toFixed(2) : '--';
        return `
          <div class="alert-card">
            <div class="alert-card-header">
              <span class="alert-card-ticker">${alert.ticker}</span>
              <span class="alert-card-current">${currentPrice}</span>
            </div>
            <div class="alert-card-condition">
              ${alert.label} <strong>${alert.unit}${alert.value}</strong>
            </div>
            <div class="alert-card-actions">
              <button class="alert-delete-btn" data-idx="${idx}">Delete</button>
            </div>
          </div>
        `;
      }).join('');

      container.querySelectorAll('.alert-delete-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const idx = parseInt(btn.dataset.idx);
          alerts.splice(idx, 1);
          saveAlerts();
          renderAlerts();
        });
      });
    }

    // History
    if (alertHistory.length > 0) {
      historySection.style.display = 'block';
      historyContainer.innerHTML = alertHistory.slice().reverse().slice(0, 50).map(h => {
        const ago = timeAgoShort(h.triggeredAt);
        return `
          <div class="alert-history-item ${h.seen ? '' : 'alert-unseen'}">
            <span class="alert-history-ticker">${h.ticker}</span>
            <span class="alert-history-msg">${h.message}</span>
            <span class="alert-history-time">${ago}</span>
          </div>
        `;
      }).join('');
    } else {
      historySection.style.display = 'none';
    }
  }

  function timeAgoShort(dateStr) {
    const d = new Date(dateStr);
    const diff = Date.now() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return mins + 'm';
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + 'h';
    const days = Math.floor(hrs / 24);
    return days + 'd';
  }

  // Check alerts against current data
  function checkAlerts() {
    if (typeof tickerData === 'undefined') return;
    let triggered = false;

    alerts.forEach(alert => {
      if (!alert.active) return;
      const d = tickerData[alert.ticker];
      if (!d) return;

      let current = null;
      let conditionMet = false;

      switch (alert.type) {
        case 'price_above':
          current = d.price;
          conditionMet = current != null && current > alert.value;
          break;
        case 'price_below':
          current = d.price;
          conditionMet = current != null && current < alert.value;
          break;
        case 'day_change_above':
          current = d.d1;
          conditionMet = current != null && current > alert.value;
          break;
        case 'day_change_below':
          current = d.d1;
          conditionMet = current != null && current < alert.value;
          break;
        case 'week_change_above':
          current = d.w1;
          conditionMet = current != null && current > alert.value;
          break;
        case 'week_change_below':
          current = d.w1;
          conditionMet = current != null && current < alert.value;
          break;
        case 'month_change_above':
          current = d.m1;
          conditionMet = current != null && current > alert.value;
          break;
        case 'month_change_below':
          current = d.m1;
          conditionMet = current != null && current < alert.value;
          break;
      }

      if (conditionMet) {
        const formattedCurrent = alert.unit === '$' ? '$' + (current?.toFixed(2) || '?') : (current?.toFixed(1) || '?') + '%';
        const msg = `${alert.label} ${alert.unit}${alert.value} (current: ${formattedCurrent})`;

        // Avoid duplicate triggers within 1 hour
        const recentDupe = alertHistory.find(h =>
          h.alertId === alert.id &&
          (Date.now() - new Date(h.triggeredAt).getTime()) < 60 * 60 * 1000
        );
        if (!recentDupe) {
          alertHistory.push({
            alertId: alert.id,
            ticker: alert.ticker,
            message: msg,
            triggeredAt: new Date().toISOString(),
            seen: false
          });
          triggered = true;

          // Show toast notification
          showAlertToast(alert.ticker, msg);
        }
      }
    });

    if (triggered) {
      saveAlerts();
    }
    updateBadge();
  }

  function showAlertToast(ticker, message) {
    const toast = document.createElement('div');
    toast.className = 'alert-toast';
    toast.innerHTML = `
      <div class="alert-toast-header">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 1.5c-2.5 0-4 1.5-4 4v2.5L1.5 10v.5h11V10L11 8V5.5c0-2.5-1.5-4-4-4z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5.5 11.5a1.5 1.5 0 003 0" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
        <strong>${ticker}</strong>
      </div>
      <div class="alert-toast-msg">${message}</div>
    `;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 300);
    }, 5000);
  }

  // Check alerts after each data load
  window._checkAlerts = checkAlerts;
  // Hook into loadAllData
  const origLoadAll = window.loadAllData;
  if (typeof loadAllData !== 'undefined') {
    const _prevLoad = loadAllData;
    loadAllData = async function() {
      await _prevLoad();
      if (window._checkAlerts) window._checkAlerts();
    };
  }

  // Initial render
  updateBadge();
  setTimeout(() => { checkAlerts(); renderAlerts(); }, 2000);
})();


// ============================================================
// 3. DATA EXPORT (CSV)
// ============================================================

(function initExport() {
  // Add export button to header
  const headerRight = document.querySelector('.header-right');
  if (!headerRight) return;

  const exportBtn = document.createElement('button');
  exportBtn.className = 'btn-sm btn-ghost export-btn';
  exportBtn.id = 'export-btn';
  exportBtn.innerHTML = `
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M2 11v2.5h12V11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 2v8M5 7l3 3 3-3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
    Export
  `;
  headerRight.insertBefore(exportBtn, headerRight.firstChild);

  // Export dropdown
  const dropdown = document.createElement('div');
  dropdown.className = 'export-dropdown';
  dropdown.id = 'export-dropdown';
  dropdown.style.display = 'none';
  dropdown.innerHTML = `
    <div class="export-dropdown-item" data-format="csv-public">Public Companies (CSV)</div>
    <div class="export-dropdown-item" data-format="csv-private">Private Companies (CSV)</div>
    <div class="export-dropdown-item" data-format="csv-screened">Screened Results (CSV)</div>
    <div class="export-dropdown-divider"></div>
    <div class="export-dropdown-item" data-format="csv-full">Full Data Export (CSV)</div>
  `;
  exportBtn.style.position = 'relative';
  exportBtn.appendChild(dropdown);

  let dropdownOpen = false;
  exportBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdownOpen = !dropdownOpen;
    dropdown.style.display = dropdownOpen ? 'block' : 'none';
  });
  document.addEventListener('click', () => {
    dropdownOpen = false;
    dropdown.style.display = 'none';
  });

  dropdown.querySelectorAll('.export-dropdown-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      const format = item.dataset.format;
      exportData(format);
      dropdownOpen = false;
      dropdown.style.display = 'none';
    });
  });

  function exportData(format) {
    let csv = '';
    const now = new Date().toISOString().slice(0, 10);

    if (format === 'csv-public' || format === 'csv-full' || format === 'csv-screened') {
      const headers = ['Ticker', 'Company', 'Subsector', 'HQ', 'Price', 'Market Cap', 'Enterprise Value',
        'FY1 EV/Sales', 'FY1 EV/FCF', 'Forward P/E', 'Revenue Growth %', 'Operating Margin %',
        'YTD %', '1D %', '1W %', '1M %', '3M %', '1Y %', '3Y %',
        'Analyst Target', 'Analyst Rating', '# Analysts', '52W High', '52W Low',
        'Free Cash Flow', 'Total Revenue', 'Total Cash', 'Total Debt'];
      csv = headers.join(',') + '\n';

      let tickers = tickerList;

      // If screened, only export visible tickers
      if (format === 'csv-screened') {
        const visibleTickers = new Set();
        document.querySelectorAll('#watchlist-body tr:not(.subsector-header):not(.screener-hidden):not(.row-hidden)').forEach(tr => {
          const cell = tr.querySelector('.cell-ticker');
          if (cell) visibleTickers.add(cell.dataset.ticker);
        });
        tickers = tickerList.filter(t => visibleTickers.has(t));
      }

      tickers.forEach(t => {
        const d = tickerData[t] || {};
        const row = [
          t,
          '"' + (getCommonName(t, d.name) || '').replace(/"/g, '""') + '"',
          '"' + (d.subsector || getSubsector(t)).replace(/"/g, '""') + '"',
          '"' + (d.headquarters || COMPANY_HQ[t] || '').replace(/"/g, '""') + '"',
          d.price != null ? d.price.toFixed(2) : '',
          d.marketCap || '',
          d.ev || d.enterpriseValue || '',
          d.evSales != null ? d.evSales.toFixed(2) : '',
          d.evFcf != null ? d.evFcf.toFixed(2) : '',
          d.forwardPE != null ? d.forwardPE.toFixed(2) : '',
          d.revenueGrowth != null ? d.revenueGrowth.toFixed(2) : '',
          d.operatingMargins != null ? d.operatingMargins.toFixed(2) : '',
          d.ytd != null ? d.ytd.toFixed(2) : '',
          d.d1 != null ? d.d1.toFixed(2) : '',
          d.w1 != null ? d.w1.toFixed(2) : '',
          d.m1 != null ? d.m1.toFixed(2) : '',
          d.m3 != null ? d.m3.toFixed(2) : '',
          d.y1 != null ? d.y1.toFixed(2) : '',
          d.y3 != null ? d.y3.toFixed(2) : '',
          d.targetMeanPrice != null ? d.targetMeanPrice.toFixed(2) : '',
          d.recommendationKey || '',
          d.numberOfAnalystOpinions || '',
          d.fiftyTwoWeekHigh || '',
          d.fiftyTwoWeekLow || '',
          d.freeCashflow || '',
          d.totalRevenue || '',
          d.totalCash || '',
          d.totalDebt || '',
        ];
        csv += row.join(',') + '\n';
      });
    }

    if (format === 'csv-private') {
      const headers = ['Company', 'Subsector', 'HQ', 'Valuation', 'Funding', 'Revenue', 'Key Metrics', 'Lead Investors', 'Status'];
      csv = headers.join(',') + '\n';
      privateCompanies.forEach(co => {
        const row = [
          '"' + (co.name || '').replace(/"/g, '""') + '"',
          '"' + (co.subsector || '').replace(/"/g, '""') + '"',
          '"' + (co.headquarters || '').replace(/"/g, '""') + '"',
          '"' + (co.valuation || '').replace(/"/g, '""') + '"',
          '"' + (co.funding || '').replace(/"/g, '""') + '"',
          '"' + (co.revenue || '').replace(/"/g, '""') + '"',
          '"' + (co.metrics || '').replace(/"/g, '""') + '"',
          '"' + (co.lead_investors || '').replace(/"/g, '""') + '"',
          co.status || 'private',
        ];
        csv += row.join(',') + '\n';
      });
    }

    if (format === 'csv-full') {
      // Already exported public above, append private as a separate section
      csv += '\n\n--- Private Companies ---\n';
      const pHeaders = ['Company', 'Subsector', 'HQ', 'Valuation', 'Funding', 'Revenue', 'Key Metrics', 'Lead Investors'];
      csv += pHeaders.join(',') + '\n';
      privateCompanies.forEach(co => {
        csv += [
          '"' + (co.name || '').replace(/"/g, '""') + '"',
          '"' + (co.subsector || '').replace(/"/g, '""') + '"',
          '"' + (co.headquarters || '').replace(/"/g, '""') + '"',
          '"' + (co.valuation || '').replace(/"/g, '""') + '"',
          '"' + (co.funding || '').replace(/"/g, '""') + '"',
          '"' + (co.revenue || '').replace(/"/g, '""') + '"',
          '"' + (co.metrics || '').replace(/"/g, '""') + '"',
          '"' + (co.lead_investors || '').replace(/"/g, '""') + '"',
        ].join(',') + '\n';
      });
    }

    // Download
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const labelMap = {
      'csv-public': 'public-companies',
      'csv-private': 'private-companies',
      'csv-screened': 'screened-results',
      'csv-full': 'full-export',
    };
    a.download = `signalstack-${labelMap[format] || 'export'}-${now}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);

    // Brief feedback
    exportBtn.querySelector('svg').style.color = 'var(--green)';
    setTimeout(() => { exportBtn.querySelector('svg').style.color = ''; }, 1500);
  }
})();


// ============================================================
// 4. RELATIVE PERFORMANCE VISUALIZATION
// ============================================================

(function initPerfViz() {
  // Add performance viz section to watchlist tab, after the private companies section
  const watchlistPane = document.getElementById('tab-watchlist');
  if (!watchlistPane) return;

  const vizSection = document.createElement('section');
  vizSection.className = 'perfviz-section';
  vizSection.innerHTML = `
    <div class="section-header-bar">
      <h2>Performance Map</h2>
      <div class="perfviz-controls">
        <select class="perfviz-select" id="perfviz-period">
          <option value="d1">1D</option>
          <option value="w1">1W</option>
          <option value="m1" selected>1M</option>
          <option value="m3">3M</option>
          <option value="ytd">YTD</option>
          <option value="y1">1Y</option>
        </select>
        <select class="perfviz-select" id="perfviz-view">
          <option value="heatmap" selected>Heatmap</option>
          <option value="scatter">Scatter</option>
        </select>
      </div>
    </div>
    <div class="perfviz-container" id="perfviz-container">
      <div class="perfviz-loading">Loading visualization...</div>
    </div>
  `;
  watchlistPane.appendChild(vizSection);

  const container = document.getElementById('perfviz-container');
  const periodSelect = document.getElementById('perfviz-period');
  const viewSelect = document.getElementById('perfviz-view');

  periodSelect.addEventListener('change', renderViz);
  viewSelect.addEventListener('change', renderViz);

  function renderViz() {
    if (typeof tickerData === 'undefined' || Object.keys(tickerData).length === 0) {
      container.innerHTML = '<div class="perfviz-loading">Waiting for data...</div>';
      return;
    }

    const period = periodSelect.value;
    const view = viewSelect.value;

    if (view === 'heatmap') renderHeatmap(period);
    else renderScatter(period);
  }

  function renderHeatmap(period) {
    // Group by subsector, show each ticker as a tile colored by performance
    const groups = {};
    tickerList.forEach(t => {
      const d = tickerData[t] || {};
      const sub = d.subsector || getSubsector(t);
      if (!groups[sub]) groups[sub] = [];
      const perf = d[period];
      groups[sub].push({ ticker: t, perf: perf != null ? perf : 0, marketCap: d.marketCap || 0 });
    });

    // Sort subsectors by predefined order
    const orderedSubs = [];
    SUBSECTOR_ORDER.forEach(s => { if (groups[s]) orderedSubs.push(s); });
    Object.keys(groups).sort().forEach(s => { if (!orderedSubs.includes(s)) orderedSubs.push(s); });

    let html = '<div class="heatmap-grid">';
    orderedSubs.forEach(sub => {
      const items = groups[sub].sort((a, b) => b.marketCap - a.marketCap);
      html += `<div class="heatmap-group">`;
      html += `<div class="heatmap-group-label">${sub}</div>`;
      html += `<div class="heatmap-tiles">`;
      items.forEach(item => {
        const color = perfColor(item.perf);
        const textColor = Math.abs(item.perf) < 1 ? 'var(--text-secondary)' : '#fff';
        const sign = item.perf > 0 ? '+' : '';
        html += `<div class="heatmap-tile" style="background:${color};color:${textColor};" title="${item.ticker}: ${sign}${item.perf.toFixed(1)}%" data-ticker="${item.ticker}">
          <span class="heatmap-ticker">${item.ticker}</span>
          <span class="heatmap-perf">${sign}${item.perf.toFixed(1)}%</span>
        </div>`;
      });
      html += `</div></div>`;
    });
    html += '</div>';

    // Legend
    html += `<div class="heatmap-legend">
      <span class="heatmap-legend-label">-20%+</span>
      <div class="heatmap-legend-bar"></div>
      <span class="heatmap-legend-label">+20%+</span>
    </div>`;

    container.innerHTML = html;

    // Click to open popup
    container.querySelectorAll('.heatmap-tile').forEach(tile => {
      tile.addEventListener('click', () => {
        const t = tile.dataset.ticker;
        if (typeof openPopup === 'function') openPopup(t);
      });
    });
  }

  function renderScatter(period) {
    // Scatter: X = FY1 EV/Sales (valuation), Y = Revenue Growth, color = performance, size = market cap
    const points = [];
    tickerList.forEach(t => {
      const d = tickerData[t] || {};
      const evSales = d.evSales;
      const revGrowth = d.revenueGrowth;
      const perf = d[period];
      const mcap = d.marketCap || 0;
      if (evSales != null && !isNaN(evSales) && evSales > 0 && evSales < 100 &&
          revGrowth != null && !isNaN(revGrowth)) {
        points.push({ ticker: t, x: evSales, y: revGrowth, perf: perf || 0, mcap });
      }
    });

    if (points.length === 0) {
      container.innerHTML = '<div class="perfviz-loading">Not enough data for scatter plot</div>';
      return;
    }

    // Canvas-based scatter
    const width = container.clientWidth || 800;
    const height = 450;
    const padding = { top: 30, right: 30, bottom: 50, left: 60 };

    const xMin = 0;
    const xMax = Math.min(Math.max(...points.map(p => p.x)) * 1.1, 80);
    const yMin = Math.min(-10, Math.min(...points.map(p => p.y)));
    const yMax = Math.max(...points.map(p => p.y)) * 1.1;

    const scaleX = (v) => padding.left + (v - xMin) / (xMax - xMin) * (width - padding.left - padding.right);
    const scaleY = (v) => height - padding.bottom - (v - yMin) / (yMax - yMin) * (height - padding.top - padding.bottom);

    const canvas = document.createElement('canvas');
    canvas.width = width * 2;
    canvas.height = height * 2;
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(2, 2);

    // Background
    ctx.fillStyle = '#111827';
    ctx.fillRect(0, 0, width, height);

    // Grid
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 0.5;
    for (let x = 0; x <= xMax; x += 10) {
      const px = scaleX(x);
      ctx.beginPath(); ctx.moveTo(px, padding.top); ctx.lineTo(px, height - padding.bottom); ctx.stroke();
    }
    for (let y = Math.ceil(yMin / 10) * 10; y <= yMax; y += 10) {
      const py = scaleY(y);
      ctx.beginPath(); ctx.moveTo(padding.left, py); ctx.lineTo(width - padding.right, py); ctx.stroke();
    }

    // Zero line for Y
    const zeroY = scaleY(0);
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padding.left, zeroY); ctx.lineTo(width - padding.right, zeroY); ctx.stroke();

    // Axes labels
    ctx.fillStyle = '#9ca3af';
    ctx.font = '11px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('FY1 EV/Sales (x)', width / 2, height - 8);
    ctx.save();
    ctx.translate(14, height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Revenue Growth (%)', 0, 0);
    ctx.restore();

    // Axis ticks
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let x = 0; x <= xMax; x += 10) {
      ctx.fillText(x + 'x', scaleX(x), height - padding.bottom + 6);
    }
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let y = Math.ceil(yMin / 10) * 10; y <= yMax; y += 10) {
      ctx.fillText(y + '%', padding.left - 6, scaleY(y));
    }

    // Points
    const mcapMax = Math.max(...points.map(p => p.mcap));
    points.forEach(p => {
      const px = scaleX(p.x);
      const py = scaleY(p.y);
      const radius = 4 + Math.sqrt(p.mcap / mcapMax) * 14;
      const color = perfColor(p.perf);

      ctx.globalAlpha = 0.7;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(px, py, radius, 0, Math.PI * 2);
      ctx.fill();

      // Label
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#e5e7eb';
      ctx.font = '10px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(p.ticker, px, py - radius - 2);
    });

    container.innerHTML = '';

    // Scatter legend
    const legend = document.createElement('div');
    legend.className = 'scatter-legend';
    legend.innerHTML = `
      <span class="scatter-legend-item">Bubble size = Market Cap</span>
      <span class="scatter-legend-item">Color = ${periodLabel(period)} performance</span>
    `;
    container.appendChild(legend);
    container.appendChild(canvas);
  }

  const PERIOD_LABELS = { d1: '1-Day', w1: '1-Week', m1: '1-Month', m3: '3-Month', ytd: 'YTD', y1: '1-Year' };
  function periodLabel(p) { return PERIOD_LABELS[p] || p; }

  function perfColor(perf) {
    // Green for positive, red for negative, scale -20 to +20
    const clamped = Math.max(-20, Math.min(20, perf));
    if (clamped >= 0) {
      const intensity = clamped / 20;
      const r = Math.round(17 + (34 - 17) * (1 - intensity));
      const g = Math.round(24 + (197 - 24) * intensity);
      const b = Math.round(39 + (94 - 39) * intensity * 0.3);
      return `rgb(${r},${g},${b})`;
    } else {
      const intensity = Math.abs(clamped) / 20;
      const r = Math.round(17 + (239 - 17) * intensity);
      const g = Math.round(24 + (68 - 24) * intensity * 0.3);
      const b = Math.round(39 + (68 - 39) * intensity * 0.3);
      return `rgb(${r},${g},${b})`;
    }
  }

  // Expose and render after data loads
  window._renderPerfViz = renderViz;

  // Hook into data loading
  const origLoad = loadAllData;
  if (typeof loadAllData !== 'undefined') {
    const _prevLoad2 = loadAllData;
    loadAllData = async function() {
      await _prevLoad2();
      if (window._renderPerfViz) window._renderPerfViz();
    };
  }

  // Initial render on delay
  setTimeout(renderViz, 3000);
})();

/* ===== ScreenerModule public API (Bug 6) =====
 * The existing screener IIFEs (above) attach their UI to the Coverage
 * surface's section header, so the dedicated #/screener surface used to
 * show only the placeholder stub. This wrapper exposes a small surface
 * the router can call to relocate the screener controls into the
 * Screener surface container on activation, and put them back when the
 * user navigates away. No changes to the screener business logic.
 */
(function () {
  'use strict';

  // Track the original parent so we can restore on deactivate.
  let _origScreenerBarParent = null;
  let _origScreenerBarNextSibling = null;

  function moveScreenerBarTo(containerEl) {
    const bar = document.getElementById('screener-bar');
    if (!bar || !containerEl) return false;
    if (_origScreenerBarParent == null) {
      _origScreenerBarParent = bar.parentNode;
      _origScreenerBarNextSibling = bar.nextSibling;
    }
    if (bar.parentNode !== containerEl) {
      containerEl.appendChild(bar);
    }
    // Hide the placeholder stub once we've populated the surface.
    const ph = document.getElementById('screener-placeholder');
    if (ph) ph.hidden = true;
    // Auto-open the panel on the dedicated surface so users land on the
    // filter UI, not an empty bar.
    const panel = document.getElementById('screener-panel');
    const toggle = document.getElementById('screener-toggle');
    if (panel) panel.style.display = 'block';
    if (toggle) toggle.classList.add('active');
    return true;
  }

  function restoreScreenerBar() {
    const bar = document.getElementById('screener-bar');
    if (!bar || !_origScreenerBarParent) return;
    if (bar.parentNode !== _origScreenerBarParent) {
      _origScreenerBarParent.insertBefore(bar, _origScreenerBarNextSibling || null);
    }
    // Re-show placeholder so the surface isn't blank if user navigates back.
    const ph = document.getElementById('screener-placeholder');
    if (ph) ph.hidden = false;
  }

  window.ScreenerModule = {
    /**
     * Render the screener UI into the given container.
     * @param {Element} [containerEl] Defaults to <body> for backward compat.
     */
    init: function (containerEl) {
      const target = containerEl || document.body;
      // The screener IIFE may not yet have built #screener-bar (it returns
      // early when `.public-section .section-header-bar` isn't found, e.g.
      // on a deep-linked first paint). Retry once after the next frame.
      if (!moveScreenerBarTo(target)) {
        requestAnimationFrame(() => moveScreenerBarTo(target));
      }
    },
    deactivate: restoreScreenerBar,
  };
})();
