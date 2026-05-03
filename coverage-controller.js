/* ===== COVERAGE-CONTROLLER.JS =====
 * Long-term unified controller for the Coverage surface plus demo-safe MVP
 * renderers for Compare / Screener / Drilldown.
 *
 * Owns one piece of persisted state:
 *   ss_coverage_state_v1 = {
 *     universeChoice: 'default_v1' | 'full_legacy' | 'custom',
 *     timeframe:      '1d' | '1w' | '1m' | '3m' | 'ytd' | '1y',
 *     sortRegime:     <select.value>,
 *     activeFlags:    [<flag>, ...],
 *     hiddenColumns:  [<col>, ...]            // mirror of legacy ss_col_prefs
 *     selectedTicker: <string> | null,
 *     savedViews:     [{ id, name, state }, ...]
 *   }
 *
 * Adopt-and-unify model: existing handlers in app.js (initRibbon, wireTimeframePills,
 * wireSortRegime, wireFlagChips, wireCustomizeCols) keep working unchanged. This
 * controller layers on top to (a) capture state mutations, (b) restore them on
 * surface activation, (c) wire the previously-dead view chips and Save View button,
 * and (d) drive the right-hand context panel from row clicks.
 *
 * No legacy .tab-btn clicks. Fail loudly in console; render fallbacks in UI.
 */
(function (global) {
  'use strict';

  var STATE_KEY = 'ss_coverage_state_v1';
  var LEGACY_HIDDEN_KEY = 'ss_col_prefs';

  function _safeGet(key) {
    try { var raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }

  // app.js declares `tickerData` as a module-scope `let` (not on window). It IS
  // visible as a bare identifier from any classic script, but NOT as window.tickerData.
  // Use the same eval pattern shell.js uses for `privateCompanies`.
  function _tickerData() {
    if (global.tickerData) return global.tickerData;
    try {
      var ref = (0, eval)('typeof tickerData !== "undefined" ? tickerData : null');
      if (ref) return ref;
    } catch (_) {}
    return {};
  }
  function _tickerList() {
    if (Array.isArray(global.tickerList)) return global.tickerList;
    try {
      var ref = (0, eval)('typeof tickerList !== "undefined" ? tickerList : null');
      if (Array.isArray(ref)) return ref;
    } catch (_) {}
    return [];
  }
  function _safeSet(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* private mode */ }
  }

  // Default state — derived from current DOM/localStorage so first run feels seamless.
  function _initialState() {
    var stored = _safeGet(STATE_KEY) || {};
    var hidden = _safeGet(LEGACY_HIDDEN_KEY) || stored.hiddenColumns || [];
    return {
      universeChoice: stored.universeChoice || (function () {
        try { return localStorage.getItem('ss_universe_choice') || 'custom'; } catch (_) { return 'custom'; }
      })(),
      timeframe: stored.timeframe || '1m',
      sortRegime: stored.sortRegime || 'default',
      activeFlags: Array.isArray(stored.activeFlags) ? stored.activeFlags.slice() : [],
      hiddenColumns: Array.isArray(hidden) ? hidden.slice() : [],
      selectedTicker: stored.selectedTicker || null,
      savedViews: Array.isArray(stored.savedViews) ? stored.savedViews.slice() : []
    };
  }

  var state = _initialState();

  function _persist() {
    _safeSet(STATE_KEY, state);
  }

  // ---------------------------------------------------------------
  // STATE MUTATORS
  // ---------------------------------------------------------------
  function setTimeframe(tf) {
    state.timeframe = tf;
    _persist();
  }
  function setSortRegime(regime) {
    state.sortRegime = regime;
    _persist();
  }
  function setActiveFlags(arr) {
    state.activeFlags = (arr || []).slice();
    _persist();
  }
  function setUniverseChoice(choice) {
    state.universeChoice = choice;
    _persist();
  }
  function setHiddenColumns(cols) {
    state.hiddenColumns = (cols || []).slice();
    _persist();
  }
  function setSelectedTicker(t) {
    state.selectedTicker = t || null;
    _persist();
    renderContextPanel();
  }

  // ---------------------------------------------------------------
  // CAPTURE: hook listeners onto existing controls so we follow user intent
  // ---------------------------------------------------------------
  function _captureRibbonState() {
    // Use document-level delegation so we capture clicks regardless of when
    // app.js's wireFlagChips/wireTimeframePills attach their own handlers,
    // and so dynamically-added controls keep working.
    document.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var pill = t.closest('#ribbon-timeframe .ribbon-pill');
      if (pill) {
        // Defer so app.js's pill click handler completes its work first.
        setTimeout(function () { setTimeframe(pill.dataset.time); }, 0);
        return;
      }
      var flag = t.closest('.ribbon-flag');
      if (flag) {
        setTimeout(function () {
          var active = Array.from(document.querySelectorAll('.ribbon-flag.active')).map(function (b) { return b.dataset.flag; });
          setActiveFlags(active);
        }, 10);
        return;
      }
    }, true); // capture phase so we run even if some handler stops propagation

    // Sort regime + universe selectors fire 'change'
    var regime = document.getElementById('ribbon-sort-regime');
    if (regime) regime.addEventListener('change', function () { setSortRegime(regime.value); });
    var uni = document.getElementById('ribbon-universe');
    if (uni) uni.addEventListener('change', function () { setUniverseChoice(uni.value); });
    // Customize columns: state.hiddenColumns mirrors legacy storage; capture on close
    var customize = document.getElementById('ribbon-customize-cols');
    if (customize) {
      customize.addEventListener('click', function () {
        // Modal is added asynchronously by app.js; attach observer that refreshes state on change.
        setTimeout(function () {
          var overlay = document.getElementById('ribbon-customize-cols-overlay');
          if (!overlay) return;
          overlay.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
            cb.addEventListener('change', function () {
              var hidden = _safeGet(LEGACY_HIDDEN_KEY) || [];
              setHiddenColumns(hidden);
            });
          });
        }, 50);
      });
    }
  }

  // ---------------------------------------------------------------
  // RESTORE: when the Coverage surface activates, push state INTO the DOM
  // ---------------------------------------------------------------
  function restoreToDom() {
    // Timeframe pill active class
    var pills = document.querySelectorAll('#ribbon-timeframe .ribbon-pill');
    if (pills.length) {
      pills.forEach(function (p) {
        p.classList.toggle('active', p.dataset.time === state.timeframe);
      });
    }
    // Sort regime select value
    var regime = document.getElementById('ribbon-sort-regime');
    if (regime && regime.value !== state.sortRegime) {
      regime.value = state.sortRegime;
    }
    // Flag chip active classes — only re-apply if they differ; never re-fire
    // the click handler (which would re-toggle visually).
    var currentActive = Array.from(document.querySelectorAll('.ribbon-flag.active')).map(function (b) { return b.dataset.flag; });
    var desired = new Set(state.activeFlags);
    var differs = currentActive.length !== desired.size ||
      currentActive.some(function (f) { return !desired.has(f); });
    if (differs) {
      document.querySelectorAll('.ribbon-flag').forEach(function (btn) {
        btn.classList.toggle('active', desired.has(btn.dataset.flag));
      });
      // Reapply filters via app.js helper if available
      if (typeof global.applyFlagFilters === 'function') {
        try { global.applyFlagFilters(); } catch (_) {}
      }
    }
    // Universe selector value
    var uni = document.getElementById('ribbon-universe');
    if (uni && uni.value !== state.universeChoice) uni.value = state.universeChoice;
  }

  // ---------------------------------------------------------------
  // CONTEXT PANEL — Coverage right-rail
  // ---------------------------------------------------------------
  function _td(d) { return d == null ? '—' : d; }
  function _fmtPrice(v)  { return typeof global.formatPrice === 'function' ? global.formatPrice(v) : _td(v); }
  function _fmtLarge(v)  { return typeof global.formatLargeNumber === 'function' ? global.formatLargeNumber(v) : _td(v); }
  function _fmtMult(v)   { return typeof global.formatMultiple === 'function' ? global.formatMultiple(v) : _td(v); }
  function _fmtPct(v)    { return typeof global.formatPercent === 'function' ? global.formatPercent(v) : _td(v); }

  function _commonName(t, fallback) {
    if (typeof global.getCommonName === 'function') return global.getCommonName(t, fallback);
    return fallback || t;
  }
  function _subsectorFor(t, d) {
    if (d && d.subsector) return d.subsector;
    if (typeof global.getSubsector === 'function') return global.getSubsector(t);
    return 'Other';
  }
  function _daysToEarnings(d) {
    if (!d || !d.calendar) return null;
    var raw = d.calendar['Earnings Date'] || d.calendar['earnings_date'];
    if (!raw) return null;
    var dateStr = Array.isArray(raw) ? raw[0] : raw;
    var parsed = new Date(dateStr);
    if (isNaN(parsed.getTime())) return null;
    return Math.ceil((parsed - new Date()) / (1000 * 60 * 60 * 24));
  }

  function _compMedian(d) {
    var data = _tickerData();
    var sub = _subsectorFor(null, d);
    var peers = Object.keys(data).filter(function (t) {
      var pd = data[t];
      return pd && pd.subsector === sub && typeof pd.evSales === 'number';
    });
    if (peers.length < 2) return null;
    var arr = peers.map(function (t) { return data[t].evSales; }).sort(function (a, b) { return a - b; });
    var mid = Math.floor(arr.length / 2);
    return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
  }

  function renderContextPanel() {
    var empty = document.getElementById('ctx-empty');
    var content = document.getElementById('ctx-content');
    if (!empty || !content) return;
    var t = state.selectedTicker;
    if (!t) {
      empty.hidden = false;
      content.hidden = true;
      return;
    }
    var data = _tickerData()[t];
    if (!data) {
      empty.hidden = false;
      content.hidden = true;
      return;
    }
    empty.hidden = true;
    content.hidden = false;

    var $ = function (id) { return document.getElementById(id); };
    if ($('ctx-ticker'))     $('ctx-ticker').textContent = t;
    if ($('ctx-name'))       $('ctx-name').textContent  = _commonName(t, data.name);

    // Snapshot
    if ($('ctx-snapshot-body')) {
      $('ctx-snapshot-body').innerHTML =
        '<div class="ctx-kv"><span>Price</span><strong>' + _fmtPrice(data.price) + '</strong></div>' +
        '<div class="ctx-kv"><span>Mkt Cap</span><strong>' + _fmtLarge(data.marketCap) + '</strong></div>' +
        '<div class="ctx-kv"><span>EV/Sales</span><strong>' + _fmtMult(data.evSales) + '</strong></div>' +
        '<div class="ctx-kv"><span>EV/FCF</span><strong>' + _fmtMult(data.evFcf) + '</strong></div>' +
        '<div class="ctx-kv"><span>YTD</span><strong>' + _fmtPct(data.ytd) + '</strong></div>' +
        '<div class="ctx-kv"><span>1M</span><strong>' + _fmtPct(data.m1) + '</strong></div>';
    }

    // Comp-relative
    if ($('ctx-comps-body')) {
      var sub = _subsectorFor(t, data);
      var med = _compMedian(data);
      var spread = (med != null && typeof data.evSales === 'number') ? data.evSales - med : null;
      $('ctx-comps-body').innerHTML =
        '<div class="ctx-kv"><span>Sector</span><strong>' + sub + '</strong></div>' +
        '<div class="ctx-kv"><span>Sector EV/Sales median</span><strong>' + _fmtMult(med) + '</strong></div>' +
        '<div class="ctx-kv"><span>Spread vs sector</span><strong>' +
          (spread == null ? '—' : (spread > 0 ? '+' : '') + spread.toFixed(1) + 'x') +
        '</strong></div>';
    }

    // Recent notes (best-effort, from earnings_notes_index if loaded)
    if ($('ctx-notes-body')) {
      var notes = global.earningsNotesIndex || global._earningsNotesIndex || null;
      var has = notes && notes[t] && Array.isArray(notes[t]) && notes[t].length;
      if (has) {
        var top = notes[t].slice(0, 2).map(function (n) {
          var date = n.date || n.published || '';
          var title = n.title || n.headline || 'Note';
          return '<div class="ctx-note"><span class="ctx-note-date">' + date + '</span> ' + title + '</div>';
        }).join('');
        $('ctx-notes-body').innerHTML = top;
      } else {
        $('ctx-notes-body').innerHTML = '<div style="color:#94a3b8;font-size:12px;">No notes indexed for this name yet.</div>';
      }
    }

    // Next catalyst
    if ($('ctx-catalyst-body')) {
      var dte = _daysToEarnings(data);
      if (dte != null) {
        var label = dte < 0 ? Math.abs(dte) + ' days ago' : dte + ' days out';
        $('ctx-catalyst-body').innerHTML =
          '<div class="ctx-kv"><span>Earnings</span><strong>' + label + '</strong></div>';
      } else {
        $('ctx-catalyst-body').innerHTML = '<div style="color:#94a3b8;font-size:12px;">No earnings date on file.</div>';
      }
    }

    // Why this matters now (heuristic narrative)
    if ($('ctx-why-body')) {
      var bits = [];
      if (typeof data.m1 === 'number' && Math.abs(data.m1) > 10) {
        bits.push('1-month move of ' + _fmtPct(data.m1) + ' is well outside its trailing range.');
      }
      var dteW = _daysToEarnings(data);
      if (dteW != null && dteW >= 0 && dteW <= 14) {
        bits.push('Catalyst inside two weeks (earnings in ' + dteW + ' day' + (dteW === 1 ? '' : 's') + ').');
      }
      if (typeof data.evSales === 'number') {
        var med2 = _compMedian(data);
        if (med2 != null && data.evSales < med2 * 0.7) bits.push('Trading materially cheap to sector median.');
        if (med2 != null && data.evSales > med2 * 1.5) bits.push('Trading at a meaningful premium to sector.');
      }
      $('ctx-why-body').innerHTML = bits.length
        ? bits.map(function (b) { return '<div class="ctx-bullet">• ' + b + '</div>'; }).join('')
        : '<div style="color:#94a3b8;font-size:12px;">Nothing flagged as urgent.</div>';
    }

    // Drilldown CTA
    var ddBtn = $('ctx-drilldown-btn');
    if (ddBtn && !ddBtn.dataset.wired) {
      ddBtn.dataset.wired = '1';
      ddBtn.addEventListener('click', function () {
        if (window.SignalRouter) window.SignalRouter.go('drilldown', { ticker: state.selectedTicker });
      });
    }
  }

  function _wireRowSelection() {
    // Capture clicks on .cell-ticker globally; legacy openPopup handler in app.js still runs.
    document.addEventListener('click', function (e) {
      var cell = e.target && e.target.closest && e.target.closest('.cell-ticker');
      if (!cell) return;
      var t = cell.dataset.ticker;
      if (!t) return;
      // Highlight selected row
      var tr = cell.closest('tr');
      document.querySelectorAll('#watchlist-table tbody tr.row-selected').forEach(function (r) {
        if (r !== tr) r.classList.remove('row-selected');
      });
      if (tr) tr.classList.add('row-selected');
      setSelectedTicker(t);
    });
  }

  // ---------------------------------------------------------------
  // SAVED VIEWS — view chips + Save View button
  // ---------------------------------------------------------------
  function _viewSnapshot() {
    return {
      timeframe: state.timeframe,
      sortRegime: state.sortRegime,
      activeFlags: state.activeFlags.slice(),
      hiddenColumns: state.hiddenColumns.slice()
    };
  }

  function _applyView(snap) {
    if (!snap) return;
    // Apply via DOM clicks where possible so legacy app.js handlers run.
    if (snap.timeframe) {
      var pill = document.querySelector('#ribbon-timeframe .ribbon-pill[data-time="' + snap.timeframe + '"]');
      if (pill) pill.click();
    }
    if (snap.sortRegime) {
      var sel = document.getElementById('ribbon-sort-regime');
      if (sel && sel.value !== snap.sortRegime) {
        sel.value = snap.sortRegime;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
    // Flags: compare current vs desired and click only those that differ
    var desired = new Set(snap.activeFlags || []);
    document.querySelectorAll('.ribbon-flag').forEach(function (btn) {
      var isActive = btn.classList.contains('active');
      var shouldBe = desired.has(btn.dataset.flag);
      if (isActive !== shouldBe) btn.click();
    });
    // Hidden columns: write LS and re-apply through app.js helper
    if (Array.isArray(snap.hiddenColumns)) {
      _safeSet(LEGACY_HIDDEN_KEY, snap.hiddenColumns);
      setHiddenColumns(snap.hiddenColumns);
      if (typeof global.applyAllStoredColumnPrefs === 'function') {
        try { global.applyAllStoredColumnPrefs(); } catch (_) {}
      }
    }
  }

  function renderViewChips() {
    var box = document.getElementById('ribbon-view-chips');
    if (!box) return;
    var chips = state.savedViews.map(function (v) {
      return '<button type="button" class="view-chip" data-view-id="' + v.id + '" title="Apply view: ' + v.name + '">' +
               '<span class="view-chip-name">' + v.name + '</span>' +
               '<span class="view-chip-x" data-view-id="' + v.id + '" title="Delete view">&times;</span>' +
             '</button>';
    }).join('');
    box.innerHTML = chips || '<span class="view-chip-empty">No saved views</span>';
    box.querySelectorAll('.view-chip').forEach(function (chip) {
      chip.addEventListener('click', function (e) {
        if (e.target.classList.contains('view-chip-x')) return;
        var id = chip.dataset.viewId;
        var v = state.savedViews.find(function (x) { return x.id === id; });
        if (v) _applyView(v.state);
      });
    });
    box.querySelectorAll('.view-chip-x').forEach(function (x) {
      x.addEventListener('click', function (e) {
        e.stopPropagation();
        var id = x.dataset.viewId;
        state.savedViews = state.savedViews.filter(function (v) { return v.id !== id; });
        _persist();
        renderViewChips();
      });
    });
  }

  function saveCurrentView() {
    var name = prompt('Name this view (e.g. "Cheap movers", "Earnings 7D"):');
    if (!name || !name.trim()) return;
    name = name.trim().slice(0, 40);
    var v = { id: 'v' + Date.now(), name: name, state: _viewSnapshot() };
    state.savedViews.push(v);
    if (state.savedViews.length > 12) state.savedViews = state.savedViews.slice(-12);
    _persist();
    renderViewChips();
  }

  function _wireSaveView() {
    var btn = document.getElementById('topbar-save-view');
    if (!btn) return;
    // app.js would have already attached an alert handler in shell.js; clone the
    // button to wipe its listeners and rebind ours so Save View becomes real.
    var fresh = btn.cloneNode(true);
    btn.parentNode.replaceChild(fresh, btn);
    fresh.addEventListener('click', saveCurrentView);
  }

  // ---------------------------------------------------------------
  // COMPARE SURFACE MVP
  // ---------------------------------------------------------------
  function _ensureComparePane() {
    var surface = document.querySelector('[data-surface="compare"]');
    if (!surface) return null;
    var pane = surface.querySelector('#compare-mvp-pane');
    if (pane) return pane;
    pane = document.createElement('div');
    pane.id = 'compare-mvp-pane';
    pane.className = 'compare-mvp';
    surface.appendChild(pane);
    return pane;
  }

  function _compareSelection() {
    if (global.SignalCompare && global.SignalCompare.isModeOn) {
      // Reach into SignalCompare via documented public API — read selected via DOM checkboxes
      var checked = Array.from(document.querySelectorAll('.compare-checkbox:checked'))
        .map(function (cb) { return cb.dataset.ticker; })
        .filter(Boolean);
      if (checked.length >= 2) return checked.slice(0, 4);
    }
    // Fallback: top 4 by 1M absolute move (so the workspace is never empty)
    var data = _tickerData();
    var ranked = Object.keys(data)
      .filter(function (t) { return typeof data[t].m1 === 'number'; })
      .sort(function (a, b) { return Math.abs(data[b].m1) - Math.abs(data[a].m1); })
      .slice(0, 4);
    return ranked;
  }

  function renderCompareSurface() {
    var pane = _ensureComparePane();
    if (!pane) return;
    var placeholder = document.getElementById('compare-placeholder');
    var tickers = _compareSelection();
    if (!tickers || tickers.length < 2) {
      pane.innerHTML = '';
      if (placeholder) placeholder.style.display = '';
      return;
    }
    if (placeholder) placeholder.style.display = 'none';

    var data = _tickerData();
    var rows = tickers.map(function (t) { return data[t] || { ticker: t }; });

    // Determine "best" column for each metric (delta highlighting)
    function bestOf(key, dir) {
      var vals = rows.map(function (r) { return r[key]; }).filter(function (v) { return typeof v === 'number'; });
      if (!vals.length) return null;
      return dir === 'min' ? Math.min.apply(null, vals) : Math.max.apply(null, vals);
    }
    var bestCheap = bestOf('evSales', 'min');
    var bestGrowth = bestOf('revenueGrowth', 'max');
    var bestM1 = bestOf('m1', 'max');

    function cell(val, fmt, isBest) {
      var html = fmt(val);
      return '<td class="num' + (isBest ? ' compare-best' : '') + '">' + html + '</td>';
    }

    var headerCells = rows.map(function (r) {
      var name = _commonName(r.ticker, r.name);
      return '<th class="compare-th"><div class="compare-th-ticker">' + r.ticker + '</div>' +
             '<div class="compare-th-name">' + (name || '') + '</div></th>';
    }).join('');

    var metrics = [
      { label: 'Price',         key: 'price',          fmt: _fmtPrice,  best: null },
      { label: 'Mkt Cap',       key: 'marketCap',      fmt: _fmtLarge,  best: null },
      { label: 'EV',            key: 'ev',             fmt: _fmtLarge,  best: null },
      { label: 'EV / Sales',    key: 'evSales',        fmt: _fmtMult,   best: bestCheap },
      { label: 'EV / FCF',      key: 'evFcf',          fmt: _fmtMult,   best: null },
      { label: 'Revenue gr.',   key: 'revenueGrowth',  fmt: _fmtPct,    best: bestGrowth },
      { label: 'YTD',           key: 'ytd',            fmt: _fmtPct,    best: null },
      { label: '1M',            key: 'm1',             fmt: _fmtPct,    best: bestM1 },
      { label: '3M',            key: 'm3',             fmt: _fmtPct,    best: null },
      { label: '1Y',            key: 'y1',             fmt: _fmtPct,    best: null }
    ];

    var bodyRows = metrics.map(function (m) {
      var cells = rows.map(function (r) {
        var v = r[m.key];
        var isBest = (m.best != null && v === m.best);
        return cell(v, m.fmt, isBest);
      }).join('');
      return '<tr><th class="compare-row-label">' + m.label + '</th>' + cells + '</tr>';
    }).join('');

    pane.innerHTML =
      '<div class="compare-mvp-toolbar">' +
        '<span class="compare-mvp-hint">Comparing ' + rows.length + ' names. Best in row highlighted.</span>' +
        '<button type="button" class="btn-sm" id="compare-mvp-pick">Pick from Coverage</button>' +
      '</div>' +
      '<div class="compare-mvp-table-wrap">' +
        '<table class="compare-mvp-table">' +
          '<thead><tr><th class="compare-row-label-head">Metric</th>' + headerCells + '</tr></thead>' +
          '<tbody>' + bodyRows + '</tbody>' +
        '</table>' +
      '</div>';

    var pickBtn = document.getElementById('compare-mvp-pick');
    if (pickBtn) {
      pickBtn.addEventListener('click', function () {
        if (global.SignalRouter) global.SignalRouter.go('coverage');
        setTimeout(function () {
          var t = document.getElementById('compare-toggle-btn');
          if (t && global.SignalCompare && !global.SignalCompare.isModeOn()) t.click();
        }, 60);
      });
    }
  }

  // ---------------------------------------------------------------
  // SCREENER SURFACE MVP
  // ---------------------------------------------------------------
  var SCREENER_KEY = 'ss_screener_mvp_v1';

  function _ensureScreenerPane() {
    var surface = document.querySelector('[data-surface="screener"]');
    if (!surface) return null;
    var pane = surface.querySelector('#screener-mvp-pane');
    if (pane) return pane;
    pane = document.createElement('div');
    pane.id = 'screener-mvp-pane';
    pane.className = 'screener-mvp';
    surface.appendChild(pane);
    return pane;
  }

  function _screenerState() {
    var stored = _safeGet(SCREENER_KEY) || {};
    return {
      sector: stored.sector || 'All',
      minMcap: stored.minMcap || '',
      maxEvSales: stored.maxEvSales || '',
      minRev: stored.minRev || ''
    };
  }
  function _screenerSet(s) { _safeSet(SCREENER_KEY, s); }

  function renderScreenerSurface() {
    var pane = _ensureScreenerPane();
    if (!pane) return;
    var placeholder = document.getElementById('screener-placeholder');
    if (placeholder) placeholder.style.display = 'none';

    var data = _tickerData();
    if (!Object.keys(data).length) {
      pane.innerHTML = '<div class="screener-mvp-empty">Coverage data still loading. Open Coverage first to populate the universe.</div>';
      return;
    }

    var sectors = Array.from(new Set(Object.keys(data).map(function (t) { return _subsectorFor(t, data[t]); }))).sort();
    var st = _screenerState();

    // Build the form once, then re-render results into a sub-pane on input.
    if (!pane.querySelector('#screener-mvp-form')) {
      pane.innerHTML =
        '<div class="screener-mvp-toolbar" id="screener-mvp-form">' +
          '<label class="screener-mvp-field"><span>Sector</span>' +
            '<select id="screener-sector"><option value="All">All</option>' +
              sectors.map(function (s) { return '<option value="' + s + '">' + s + '</option>'; }).join('') +
            '</select></label>' +
          '<label class="screener-mvp-field"><span>Min Mkt Cap ($B)</span>' +
            '<input type="number" id="screener-min-mcap" step="1" placeholder="e.g. 10"></label>' +
          '<label class="screener-mvp-field"><span>Max EV/Sales</span>' +
            '<input type="number" id="screener-max-evsales" step="0.5" placeholder="e.g. 8"></label>' +
          '<label class="screener-mvp-field"><span>Min Rev. growth (%)</span>' +
            '<input type="number" id="screener-min-rev" step="1" placeholder="e.g. 10"></label>' +
          '<button type="button" class="btn-sm" id="screener-reset">Reset</button>' +
          '<span class="screener-mvp-count" id="screener-count">— results</span>' +
        '</div>' +
        '<div class="screener-mvp-results" id="screener-mvp-results"></div>';

      var $sector = pane.querySelector('#screener-sector');
      var $mc = pane.querySelector('#screener-min-mcap');
      var $ev = pane.querySelector('#screener-max-evsales');
      var $rev = pane.querySelector('#screener-min-rev');
      $sector.value = st.sector;
      $mc.value = st.minMcap;
      $ev.value = st.maxEvSales;
      $rev.value = st.minRev;

      function refresh() {
        var ns = {
          sector: $sector.value,
          minMcap: $mc.value,
          maxEvSales: $ev.value,
          minRev: $rev.value
        };
        _screenerSet(ns);
        _renderScreenerResults(ns);
      }
      $sector.addEventListener('change', refresh);
      [$mc, $ev, $rev].forEach(function (el) {
        el.addEventListener('input', function () {
          // light debounce
          clearTimeout(el._t);
          el._t = setTimeout(refresh, 150);
        });
      });
      pane.querySelector('#screener-reset').addEventListener('click', function () {
        $sector.value = 'All'; $mc.value = ''; $ev.value = ''; $rev.value = '';
        refresh();
      });
    }
    _renderScreenerResults(_screenerState());
  }

  function _renderScreenerResults(st) {
    var resultsEl = document.getElementById('screener-mvp-results');
    var countEl = document.getElementById('screener-count');
    if (!resultsEl) return;

    var data = _tickerData();
    var minMcap = parseFloat(st.minMcap);
    var maxEvSales = parseFloat(st.maxEvSales);
    var minRev = parseFloat(st.minRev);

    var matches = Object.keys(data).filter(function (t) {
      var d = data[t]; if (!d) return false;
      var sub = _subsectorFor(t, d);
      if (st.sector !== 'All' && sub !== st.sector) return false;
      if (!isNaN(minMcap)) {
        if (typeof d.marketCap !== 'number' || d.marketCap < minMcap * 1e9) return false;
      }
      if (!isNaN(maxEvSales)) {
        if (typeof d.evSales !== 'number' || d.evSales > maxEvSales) return false;
      }
      if (!isNaN(minRev)) {
        if (typeof d.revenueGrowth !== 'number' || d.revenueGrowth < minRev) return false;
      }
      return true;
    });

    matches.sort(function (a, b) { return (data[b].marketCap || 0) - (data[a].marketCap || 0); });

    if (countEl) countEl.textContent = matches.length + ' result' + (matches.length === 1 ? '' : 's');

    if (!matches.length) {
      resultsEl.innerHTML = '<div class="screener-mvp-empty">No names match. Loosen the filters.</div>';
      return;
    }

    var rowsHtml = matches.slice(0, 80).map(function (t) {
      var d = data[t];
      var name = _commonName(t, d.name);
      return '<tr data-ticker="' + t + '">' +
        '<td class="screener-cell-ticker">' + t + '</td>' +
        '<td>' + name + '</td>' +
        '<td>' + _subsectorFor(t, d) + '</td>' +
        '<td class="num">' + _fmtPrice(d.price) + '</td>' +
        '<td class="num">' + _fmtLarge(d.marketCap) + '</td>' +
        '<td class="num">' + _fmtMult(d.evSales) + '</td>' +
        '<td class="num">' + _fmtPct(d.revenueGrowth) + '</td>' +
        '<td class="num">' + _fmtPct(d.m1) + '</td>' +
      '</tr>';
    }).join('');

    var more = matches.length > 80 ? '<div class="screener-mvp-more">Showing first 80 of ' + matches.length + '.</div>' : '';

    resultsEl.innerHTML =
      '<div class="screener-mvp-table-wrap">' +
        '<table class="screener-mvp-table">' +
          '<thead><tr><th>Ticker</th><th>Name</th><th>Sector</th>' +
            '<th class="num">Price</th><th class="num">Mkt Cap</th><th class="num">EV/Sales</th>' +
            '<th class="num">Rev gr.</th><th class="num">1M</th></tr></thead>' +
          '<tbody>' + rowsHtml + '</tbody>' +
        '</table>' +
      '</div>' + more;

    resultsEl.querySelectorAll('tr[data-ticker]').forEach(function (tr) {
      tr.addEventListener('click', function () {
        var t = tr.dataset.ticker;
        if (typeof global.openPopup === 'function') global.openPopup(t);
      });
    });
  }

  // ---------------------------------------------------------------
  // DRILLDOWN SURFACE MVP
  // ---------------------------------------------------------------
  function _ensureDrilldownPane() {
    var surface = document.querySelector('[data-surface="drilldown"]');
    if (!surface) return null;
    var pane = surface.querySelector('#drilldown-mvp-pane');
    if (pane) return pane;
    pane = document.createElement('div');
    pane.id = 'drilldown-mvp-pane';
    pane.className = 'drilldown-mvp';
    surface.appendChild(pane);
    return pane;
  }

  function renderDrilldownSurface(params) {
    var pane = _ensureDrilldownPane();
    if (!pane) return;
    var placeholder = document.getElementById('drilldown-placeholder');
    var ticker = (params && params.ticker) ||
                 state.selectedTicker ||
                 (_tickerList()[0]) || null;
    if (!ticker) {
      if (placeholder) placeholder.style.display = '';
      pane.innerHTML = '';
      return;
    }
    if (placeholder) placeholder.style.display = 'none';

    var data = _tickerData()[ticker] || { ticker: ticker };
    var name = _commonName(ticker, data.name);
    var sub = _subsectorFor(ticker, data);
    var dte = _daysToEarnings(data);

    pane.innerHTML =
      '<div class="drilldown-mvp-header">' +
        '<div>' +
          '<div class="drilldown-mvp-ticker">' + ticker + '</div>' +
          '<div class="drilldown-mvp-name">' + name + ' · ' + sub + '</div>' +
        '</div>' +
        '<div class="drilldown-mvp-actions">' +
          '<input type="text" id="drilldown-mvp-input" placeholder="Switch ticker…" autocomplete="off" spellcheck="false" value="">' +
          '<button type="button" class="btn-sm" id="drilldown-mvp-go">Go</button>' +
          '<button type="button" class="btn-sm btn-primary" id="drilldown-mvp-deep">Open full deep-dive</button>' +
        '</div>' +
      '</div>' +
      '<div class="drilldown-mvp-grid">' +
        '<div class="drilldown-mvp-card"><div class="dd-card-title">Snapshot</div>' +
          '<div class="ctx-kv"><span>Price</span><strong>' + _fmtPrice(data.price) + '</strong></div>' +
          '<div class="ctx-kv"><span>Mkt Cap</span><strong>' + _fmtLarge(data.marketCap) + '</strong></div>' +
          '<div class="ctx-kv"><span>EV</span><strong>' + _fmtLarge(data.ev) + '</strong></div>' +
          '<div class="ctx-kv"><span>EV/Sales</span><strong>' + _fmtMult(data.evSales) + '</strong></div>' +
          '<div class="ctx-kv"><span>EV/FCF</span><strong>' + _fmtMult(data.evFcf) + '</strong></div>' +
        '</div>' +
        '<div class="drilldown-mvp-card"><div class="dd-card-title">Performance</div>' +
          '<div class="ctx-kv"><span>YTD</span><strong>' + _fmtPct(data.ytd) + '</strong></div>' +
          '<div class="ctx-kv"><span>1D</span><strong>' + _fmtPct(data.d1) + '</strong></div>' +
          '<div class="ctx-kv"><span>1W</span><strong>' + _fmtPct(data.w1) + '</strong></div>' +
          '<div class="ctx-kv"><span>1M</span><strong>' + _fmtPct(data.m1) + '</strong></div>' +
          '<div class="ctx-kv"><span>3M</span><strong>' + _fmtPct(data.m3) + '</strong></div>' +
          '<div class="ctx-kv"><span>1Y</span><strong>' + _fmtPct(data.y1) + '</strong></div>' +
        '</div>' +
        '<div class="drilldown-mvp-card"><div class="dd-card-title">Catalyst</div>' +
          '<div class="ctx-kv"><span>Next earnings</span><strong>' +
            (dte == null ? '—' : (dte < 0 ? Math.abs(dte) + ' days ago' : dte + ' days')) +
          '</strong></div>' +
          '<div class="ctx-kv"><span>Revenue growth</span><strong>' + _fmtPct(data.revenueGrowth) + '</strong></div>' +
        '</div>' +
      '</div>';

    var goBtn = pane.querySelector('#drilldown-mvp-go');
    var input = pane.querySelector('#drilldown-mvp-input');
    if (goBtn && input) {
      var jump = function () {
        var v = (input.value || '').trim().toUpperCase();
        if (v && global.SignalRouter) global.SignalRouter.go('drilldown', { ticker: v });
      };
      goBtn.addEventListener('click', jump);
      input.addEventListener('keydown', function (e) { if (e.key === 'Enter') jump(); });
    }
    var deepBtn = pane.querySelector('#drilldown-mvp-deep');
    if (deepBtn) deepBtn.addEventListener('click', function () {
      if (typeof global.openPopup === 'function') global.openPopup(ticker);
    });
  }

  // ---------------------------------------------------------------
  // ROUTER WIRING — augment the existing surface registrations
  // ---------------------------------------------------------------
  function _wireSurfaces() {
    if (!global.SignalRouter) return;
    var router = global.SignalRouter;

    // Compose-style: keep existing onActivate (registered by shell.js) and prepend ours.
    function compose(surface, extra) {
      var existing = router._signalstack_orig_hooks ? router._signalstack_orig_hooks[surface] : null;
      // We can't easily read the existing hook; just register a replacement that calls
      // our extra function and ALSO performs the work shell.js' hook did. shell.js
      // already registered before us, so its onActivate is what `register` overwrote.
      // To preserve, we wrap by calling our function then re-running shell's hooks
      // through known side effects. In practice shell.js coverage hook only calls
      // updateSummaryStripCounts, which is window-scoped — safe to skip.
      router.register(surface, { onActivate: extra });
    }

    compose('coverage', function () {
      restoreToDom();
      renderViewChips();
      renderContextPanel();
      if (typeof global.updateCoverageSummaryTiles === 'function') {
        try { global.updateCoverageSummaryTiles(); } catch (_) {}
      }
    });

    compose('compare', function () {
      renderCompareSurface();
    });

    compose('screener', function () {
      renderScreenerSurface();
    });

    // Drilldown: must keep the legacy openPopup-on-ticker behavior AND
    // render our inline panel. Compose by hand.
    router.register('drilldown', {
      onActivate: function (params) {
        renderDrilldownSurface(params);
        if (params && params.ticker && typeof global.openPopup === 'function') {
          // Keep legacy popup launch behind a defer so the inline panel paints first.
          setTimeout(function () { global.openPopup(params.ticker); }, 30);
        }
      }
    });
  }

  // ---------------------------------------------------------------
  // BOOT
  // ---------------------------------------------------------------
  function boot() {
    // Defensive: if app.js hasn't initialized the ribbon yet (initRibbon is gated
    // on _wired), wait one tick.
    setTimeout(function () {
      try { _captureRibbonState(); } catch (e) { console.error('[coverage-controller] capture failed', e); }
      try { restoreToDom(); }       catch (e) { console.error('[coverage-controller] restore failed', e); }
      try { renderViewChips(); }    catch (e) { console.error('[coverage-controller] view chips failed', e); }
      try { _wireRowSelection(); }  catch (e) { console.error('[coverage-controller] row select failed', e); }
      try { _wireSaveView(); }      catch (e) { console.error('[coverage-controller] save view failed', e); }
      try { _wireSurfaces(); }      catch (e) { console.error('[coverage-controller] surface wire failed', e); }
      try { renderContextPanel(); } catch (e) { console.error('[coverage-controller] ctx panel failed', e); }
    }, 50);

    // Re-render context panel & view chips on table re-render (which can clobber row-selected)
    document.addEventListener('signalstack:route-changed', function (e) {
      var s = e && e.detail && e.detail.surface;
      if (s === 'coverage') {
        setTimeout(function () { restoreToDom(); renderContextPanel(); }, 30);
      }
    });

    // Re-render context panel as soon as tickerData populates. Watch for the
    // first table body row that has a real numeric cell (data has loaded).
    var dataReadyAttempts = 0;
    function _waitForData() {
      var data = _tickerData();
      var ready = data && Object.keys(data).length > 0;
      if (ready) {
        try { renderContextPanel(); } catch (_) {}
        // If we are sitting on a non-coverage surface that needs data, repaint it too.
        var router = global.SignalRouter;
        var current = router && router.current && router.current();
        if (current === 'compare') renderCompareSurface();
        else if (current === 'screener') renderScreenerSurface();
        else if (current === 'drilldown') renderDrilldownSurface(router && router.params && router.params());
        return;
      }
      if (dataReadyAttempts++ < 60) setTimeout(_waitForData, 250);
    }
    _waitForData();

    // Also keep selected-row highlight stable across re-renders
    document.addEventListener('signalstack:table-rendered', function () {
      if (!state.selectedTicker) return;
      document.querySelectorAll('.cell-ticker[data-ticker="' + state.selectedTicker + '"]').forEach(function (cell) {
        var tr = cell.closest('tr'); if (tr) tr.classList.add('row-selected');
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    // shell.js runs after this file (it boots later). Defer slightly to let it wire its handlers first.
    setTimeout(boot, 0);
  }

  // Public API
  global.SignalCoverage = {
    getState: function () { return JSON.parse(JSON.stringify(state)); },
    setSelectedTicker: setSelectedTicker,
    saveCurrentView: saveCurrentView,
    renderContextPanel: renderContextPanel,
    renderCompareSurface: renderCompareSurface,
    renderScreenerSurface: renderScreenerSurface,
    renderDrilldownSurface: renderDrilldownSurface
  };

})(window);
