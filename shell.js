/* ===== SHELL.JS =====
 * Top-bar nav + mode toggle + router + "more" sub-tabs + universe selector.
 *
 * Runs AFTER all existing modules so it can safely read / mutate state like
 * `tickerList` (from app.js) and call into existing modules via their
 * public DOM surfaces.
 */
(function () {
  'use strict';

  // ----- Universe selector ----------------------------------------------
  // Key: 'ss_universe_choice' = 'default_v1' | 'full_legacy' | 'custom'
  // On first load, if user had NO ticker_list override, we default them to
  // default_v1. If they already had a custom watchlist, we preserve it and
  // mark the selector as 'custom'.
  const UNIVERSE_CHOICE_KEY = 'ss_universe_choice';

  function getUniverseChoice() {
    try { return localStorage.getItem(UNIVERSE_CHOICE_KEY) || 'default_v1'; } catch (_) { return 'default_v1'; }
  }
  function setUniverseChoice(v) {
    try { localStorage.setItem(UNIVERSE_CHOICE_KEY, v); } catch (_) {}
  }

  function applyUniverseChoice(choice, opts) {
    opts = opts || {};
    if (choice === 'default_v1' && window.UNIVERSE_V1_TICKERS) {
      // Replace watchlist with curated default
      if (typeof window.tickerList !== 'undefined') {
        window.tickerList.length = 0;
        window.UNIVERSE_V1_TICKERS.forEach(t => window.tickerList.push(t));
        if (typeof window.saveTickers === 'function') window.saveTickers();
        if (typeof window.loadAllData === 'function') window.loadAllData();
        else if (typeof window.renderTable === 'function') window.renderTable();
      }
    } else if (choice === 'full_legacy' && window.DEFAULT_TICKERS) {
      if (typeof window.tickerList !== 'undefined') {
        window.tickerList.length = 0;
        window.DEFAULT_TICKERS.forEach(t => window.tickerList.push(t));
        if (typeof window.saveTickers === 'function') window.saveTickers();
        if (typeof window.loadAllData === 'function') window.loadAllData();
        else if (typeof window.renderTable === 'function') window.renderTable();
      }
    }
    // 'custom' = no-op, let whatever the user built stand.
    setUniverseChoice(choice);
    updateSummaryStripCounts();
  }

  function initUniverseSelector() {
    const sel = document.getElementById('ribbon-universe');
    if (!sel) return;

    // Never silently replace the user's coverage list on load. Earnings,
    // Screener, and Coverage all read the shared global `tickerList`
    // (app.js), and that is initialized from localStorage.ticker_list with
    // a fallback to DEFAULT_TICKERS. The universe selector is a manual
    // switch — it only mutates state when the user picks a new option.
    let hasChoice = false;
    try { hasChoice = !!localStorage.getItem(UNIVERSE_CHOICE_KEY); } catch (_) {}
    if (!hasChoice) {
      // Default selector label to 'custom' so it reflects whatever the user
      // has (saved list or the app.js DEFAULT_TICKERS fallback). No mutation.
      setUniverseChoice('custom');
    }

    sel.value = getUniverseChoice();
    sel.addEventListener('change', (e) => {
      const v = e.target.value;
      if (v === 'custom') {
        // no-op — user keeps their current list
        setUniverseChoice('custom');
        return;
      }
      if (v === 'default_v1' || v === 'full_legacy') {
        if (!confirm('Switching universe replaces your current coverage list with the ' +
                     (v === 'default_v1' ? 'curated default (~120 names)' : 'full legacy universe (~160 names)') +
                     '. Continue?')) {
          sel.value = getUniverseChoice();
          return;
        }
        applyUniverseChoice(v);
      }
    });
  }

  // ----- Top-bar nav ---------------------------------------------------
  function initNav() {
    document.querySelectorAll('[data-nav-surface]').forEach(btn => {
      btn.addEventListener('click', () => {
        window.SignalRouter.go(btn.dataset.navSurface);
      });
    });

    // Coverage <-> Compare shortcuts
    const goCoverage = () => window.SignalRouter.go('coverage');
    document.getElementById('compare-go-coverage')?.addEventListener('click', goCoverage);
    document.getElementById('screener-go-coverage')?.addEventListener('click', goCoverage);

    // Top-bar action shortcuts
    document.getElementById('topbar-add')?.addEventListener('click', () => {
      window.SignalRouter.go('coverage');
      setTimeout(() => document.getElementById('ticker-input')?.focus(), 50);
    });
    document.getElementById('topbar-compare')?.addEventListener('click', () => {
      window.SignalRouter.go('coverage');
      setTimeout(() => document.getElementById('compare-toggle-btn')?.click(), 50);
    });
    document.getElementById('topbar-save-view')?.addEventListener('click', () => {
      alert('Save view lands in M3 — saved views let you snapshot current ribbon + column state.');
    });

    // Global search: lightweight pass-through to the Coverage search input
    const gs = document.getElementById('global-search');
    if (gs) {
      gs.addEventListener('focus', () => {
        // Surface must be active for Coverage search dropdown to render anywhere useful
        if (window.SignalRouter.getCurrent().surface !== 'coverage') {
          window.SignalRouter.go('coverage');
        }
      });
      gs.addEventListener('input', (e) => {
        const tInput = document.getElementById('ticker-input');
        if (tInput) {
          tInput.value = e.target.value;
          tInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });
      gs.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          const tInput = document.getElementById('ticker-input');
          if (tInput) tInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        }
      });
    }
  }

  // ----- Mode toggle ---------------------------------------------------
  function initModeToggle() {
    function refresh() {
      const cur = window.SignalMode.get();
      document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === cur);
      });
      // Update mode-specific summary tile
      const modeLabel = document.getElementById('stat-mode-label');
      const modeValue = document.getElementById('stat-mode-value');
      const modeSub = document.getElementById('stat-mode-sub');
      if (modeLabel && modeValue && modeSub) {
        if (cur === 'hf') {
          modeLabel.textContent = 'Debate intensity';
          modeSub.textContent = 'HF mode';
        } else {
          modeLabel.textContent = 'Quality score';
          modeSub.textContent = 'LO mode';
        }
      }
      // Re-compute mode-sensitive tile value (Debate score in HF, TBD in LO)
      if (typeof window.updateCoverageSummaryTiles === 'function') {
        try { window.updateCoverageSummaryTiles(); } catch (_) {}
      }
    }
    document.querySelectorAll('.mode-btn').forEach(btn => {
      btn.addEventListener('click', () => window.SignalMode.set(btn.dataset.mode));
    });
    window.SignalMode.onChange(refresh);
    refresh();
  }

  // ===== More-surface controller =======================================
  // Self-contained, state-machine driven. Each pane has one of three states:
  //   idle    — never loaded
  //   loading — loader is in flight
  //   loaded  — render produced visible content
  //   error   — loader threw / rejected; will be re-tried on next activation
  //
  // The previous implementation set a single boolean flag BEFORE the loader
  // ran and never re-attempted on transient failures. That left panes stuck
  // showing "Loading..." forever once anything went wrong (Yahoo proxy
  // hiccup, late script load, etc.). The new design retries automatically on
  // every activation when the pane isn't in the loaded state, and surfaces a
  // visible Retry button on error or after a watchdog timeout.

  var MORE_PANES = ['briefing', 'macro', 'news', 'alerts'];
  var MORE_LS_KEY = 'ss_more_pane';
  var MORE_WATCHDOG_MS = 12000; // show retry UI if a loader hasn't rendered by then

  // Map each pane key to (a) its loader resolver and (b) the inner
  // content container we render fallback messages into.
  var MORE_PANE_CONFIG = {
    briefing: {
      flag: '_briefingLoaded',
      contentId: 'weekly-briefing-content',
      loaderName: 'loadWeeklyBriefing',
      label: 'Weekly Briefing',
    },
    macro: {
      flag: '_macroLoaded',
      contentId: 'macro-content',
      loaderName: 'renderMacroTab',
      label: 'Macro',
    },
    news: {
      flag: '_newsLoaded',
      contentId: 'news-feed',
      loaderName: 'fetchNews',
      label: 'News',
    },
    alerts: {
      flag: '_alertsLoaded',
      contentId: 'alerts-content',
      loaderName: 'renderAlertsTab',
      label: 'Alerts',
    },
  };

  // Per-pane runtime state. Survives the whole session.
  var moreState = {
    briefing: { status: 'idle', watchdog: null, attempt: 0 },
    macro:    { status: 'idle', watchdog: null, attempt: 0 },
    news:     { status: 'idle', watchdog: null, attempt: 0 },
    alerts:   { status: 'idle', watchdog: null, attempt: 0 },
  };

  function _resolveMoreLoader(name) {
    if (typeof window[name] === 'function') return window[name];
    try {
      var bare = (0, eval)(name);
      if (typeof bare === 'function') return bare;
    } catch (e) { /* ReferenceError -> truly not present */ }
    return null;
  }

  function _paneContent(key) {
    var cfg = MORE_PANE_CONFIG[key];
    return cfg ? document.getElementById(cfg.contentId) : null;
  }

  // True if the loader has put real content into the pane (anything beyond
  // the inline "Loading" sentinel set in index.html). Used by the watchdog
  // to decide whether to surface a retry CTA.
  function _paneHasRealContent(key) {
    var el = _paneContent(key);
    if (!el) return false;
    var html = el.innerHTML || '';
    if (html.length < 100) return false;
    if (/(macro|news|alerts|wb)-loading/.test(html)) return false;
    if (/wb-empty/.test(html) && html.length < 250) return false;
    return true;
  }

  function _renderMoreError(key, message) {
    var cfg = MORE_PANE_CONFIG[key];
    if (!cfg) return;
    var el = _paneContent(key);
    if (!el) return;
    el.innerHTML =
      '<div class="more-pane-fallback" style="padding:24px;color:#94a3b8;' +
      'font-size:13px;line-height:1.5;">' +
      '<strong style="color:#e2e8f0;display:block;margin-bottom:6px;">' +
      cfg.label + ' did not load.</strong>' +
      '<div style="margin-bottom:10px;">' + String(message || 'The module may have hit a transient error.') + '</div>' +
      '<button type="button" data-more-retry="' + key + '" ' +
      'style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;' +
      'border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer;">' +
      'Retry</button>' +
      '</div>';
  }

  // After the watchdog fires, if the pane STILL doesn't have real content
  // we soft-overlay a retry hint without nuking whatever the loader has put
  // in place (some loaders progressively render).
  function _renderMoreWatchdogHint(key) {
    var cfg = MORE_PANE_CONFIG[key];
    if (!cfg) return;
    var el = _paneContent(key);
    if (!el) return;
    if (el.querySelector('[data-more-retry]')) return; // already shown
    var hint = document.createElement('div');
    hint.setAttribute('data-more-watchdog-hint', key);
    hint.style.cssText = 'padding:14px 24px;color:#94a3b8;font-size:12px;' +
      'border-top:1px solid #1e293b;display:flex;align-items:center;' +
      'justify-content:space-between;gap:12px;';
    hint.innerHTML =
      '<span>Still loading ' + cfg.label.toLowerCase() + '… If this hangs, try again.</span>' +
      '<button type="button" data-more-retry="' + key + '" ' +
      'style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;' +
      'border-radius:6px;padding:5px 12px;font-size:12px;cursor:pointer;">Retry</button>';
    el.appendChild(hint);
  }

  function _clearWatchdog(key) {
    var s = moreState[key];
    if (!s) return;
    if (s.watchdog) { clearTimeout(s.watchdog); s.watchdog = null; }
  }

  function _armWatchdog(key) {
    _clearWatchdog(key);
    var s = moreState[key];
    if (!s) return;
    s.watchdog = setTimeout(function () {
      // If the loader actually rendered, mark loaded and stand down.
      if (_paneHasRealContent(key)) {
        s.status = 'loaded';
        return;
      }
      // Else surface a retry hint without clobbering partial UI.
      _renderMoreWatchdogHint(key);
    }, MORE_WATCHDOG_MS);
  }

  function getMorePaneKey() {
    var active = document.querySelector('.more-subtab.active');
    var key = active && active.dataset && active.dataset.more;
    return MORE_PANES.indexOf(key) !== -1 ? key : 'briefing';
  }

  function showMorePane(key) {
    if (MORE_PANES.indexOf(key) === -1) key = 'briefing';
    document.querySelectorAll('.more-subtab').forEach(function (b) {
      b.classList.toggle('active', b.dataset.more === key);
    });
    document.querySelectorAll('[data-more-pane]').forEach(function (p) {
      p.classList.toggle('active', p.dataset.morePane === key);
    });
    try { localStorage.setItem(MORE_LS_KEY, key); } catch (e) { /* private mode */ }
  }

  // Run a pane's loader. `force=true` bypasses the loaded-state check and is
  // used by the Retry button. Otherwise we skip if the pane is already loaded.
  // We INTENTIONALLY do not skip on `loading` — re-arming the watchdog when
  // the user revisits is fine and harmless.
  function loadMorePane(key, opts) {
    opts = opts || {};
    var cfg = MORE_PANE_CONFIG[key];
    if (!cfg) return;
    var s = moreState[key];
    if (!s) return;

    // If a previous boot left the legacy boolean flag set, treat the pane as
    // loaded only when the DOM actually has real content. Otherwise reset.
    if (window[cfg.flag] && s.status === 'idle') {
      s.status = _paneHasRealContent(key) ? 'loaded' : 'idle';
      if (s.status === 'idle') window[cfg.flag] = false;
    }

    if (!opts.force && s.status === 'loaded' && _paneHasRealContent(key)) {
      return; // truly loaded, leave it alone
    }

    // Resolve loader. If still missing, try again shortly — modules may load
    // async-ish on slow networks. Past that we surface a clear error.
    var loader = _resolveMoreLoader(cfg.loaderName);
    if (typeof loader !== 'function') {
      s.attempt = (s.attempt || 0) + 1;
      if (s.attempt < 5) {
        setTimeout(function () { loadMorePane(key, { force: true }); }, 250);
        return;
      }
      s.status = 'error';
      window[cfg.flag] = false;
      _renderMoreError(key,
        cfg.loaderName + '() never registered. The ' + cfg.label.toLowerCase() +
        ' script may have failed to load — try a hard reload (Cmd/Ctrl+Shift+R).');
      return;
    }

    s.status = 'loading';
    s.attempt = 0;
    window[cfg.flag] = true; // back-compat: legacy callers still read this
    _armWatchdog(key);

    var settled = false;
    function _onSuccess() {
      if (settled) return;
      settled = true;
      _clearWatchdog(key);
      // Re-check on next tick — most loaders mutate the DOM right after
      // resolve, but jsPDF / pre-render paths sometimes hop a microtask.
      setTimeout(function () {
        if (_paneHasRealContent(key)) {
          s.status = 'loaded';
        } else {
          // Loader resolved but produced empty UI. Don't lock — let the
          // user retry without a page reload.
          s.status = 'error';
          window[cfg.flag] = false;
          if (!_paneContent(key) || !_paneContent(key).querySelector('[data-more-retry]')) {
            _renderMoreError(key, 'No data available right now. Try again in a moment.');
          }
        }
      }, 50);
    }
    function _onError(err) {
      if (settled) return;
      settled = true;
      _clearWatchdog(key);
      console.error('[more] ' + cfg.loaderName + '() failed:', err);
      s.status = 'error';
      window[cfg.flag] = false; // allow retry
      _renderMoreError(key, (err && err.message) ? err.message : 'Transient error. Tap retry.');
    }

    try {
      var ret = loader();
      if (ret && typeof ret.then === 'function') {
        ret.then(_onSuccess, _onError);
      } else {
        // Sync loader — give the DOM a beat to settle, then verify.
        _onSuccess();
      }
    } catch (err) {
      _onError(err);
    }
  }

  function activateMorePane(key) {
    if (MORE_PANES.indexOf(key) === -1) key = 'briefing';
    showMorePane(key);
    loadMorePane(key);
  }

  // Public retry: forces a fresh load and clears the legacy boolean flag.
  function retryMorePane(key) {
    if (MORE_PANES.indexOf(key) === -1) return;
    var cfg = MORE_PANE_CONFIG[key];
    var s = moreState[key];
    if (s) { s.status = 'idle'; s.attempt = 0; _clearWatchdog(key); }
    if (cfg) window[cfg.flag] = false;
    var el = _paneContent(key);
    if (el) {
      el.innerHTML = '<div class="more-pane-loading" style="padding:24px;color:#94a3b8;font-size:13px;">Reloading ' + cfg.label.toLowerCase() + '...</div>';
    }
    loadMorePane(key, { force: true });
  }

  // Back-compat aliases for prior shell.js releases.
  window.activateMorePane = activateMorePane;
  window._activateMorePane = activateMorePane;
  window.loadMorePane = loadMorePane;
  window._loadMorePane = loadMorePane;
  window.showMorePane = showMorePane;
  window.getMorePaneKey = getMorePaneKey;
  window.retryMorePane = retryMorePane;

  function initMoreSubtabs() {
    document.querySelectorAll('.more-subtab').forEach(function (btn) {
      btn.addEventListener('click', function () {
        activateMorePane(btn.dataset.more);
      });
    });

    // Delegated retry handler — works for both error fallback and watchdog hint.
    document.addEventListener('click', function (e) {
      var t = e.target.closest && e.target.closest('[data-more-retry]');
      if (!t) return;
      e.preventDefault();
      retryMorePane(t.getAttribute('data-more-retry'));
    });

    // Restore last-selected pane from localStorage; default to briefing.
    var stored = null;
    try { stored = localStorage.getItem(MORE_LS_KEY); } catch (e) { /* private mode */ }
    var initial = MORE_PANES.indexOf(stored) !== -1 ? stored : 'briefing';
    activateMorePane(initial);
  }

  // ----- Router integration --------------------------------------------
  function registerSurfaces() {
    // Coverage: ensure the public table is refreshed when returning
    window.SignalRouter.register('coverage', {
      onActivate: () => {
        // Nothing special — app.js already keeps the table live.
        updateSummaryStripCounts();
      },
    });
    // Earnings: trigger legacy earnings load if not yet done
    window.SignalRouter.register('earnings', {
      onActivate: () => {
        // Simulate clicking legacy 'research' tab so earnings.js loads.
        const legacyBtn = document.querySelector('.tab-btn[data-tab="research"]');
        if (legacyBtn && typeof legacyBtn.click === 'function') legacyBtn.click();
      },
    });
    // Private Markets: trigger any lazy loaders
    window.SignalRouter.register('private', {
      onActivate: () => {
        if (typeof window.renderPrivateTable === 'function') window.renderPrivateTable();
      },
    });
    // More: self-contained controller — never clicks legacy .tab-btn.
    // Resolves the target pane via (1) localStorage, (2) current active
    // subtab, (3) 'briefing' default, then dispatches to activateMorePane.
    window.SignalRouter.register('more', {
      onActivate: function () {
        var stored = null;
        try { stored = localStorage.getItem(MORE_LS_KEY); } catch (e) { /* private mode */ }
        var key;
        if (MORE_PANES.indexOf(stored) !== -1) {
          key = stored;
        } else {
          key = getMorePaneKey();
        }
        activateMorePane(key);
      },
    });
    // Drilldown: ticker param -> open existing popup
    window.SignalRouter.register('drilldown', {
      onActivate: (params) => {
        if (params && params.ticker && typeof window.openPopup === 'function') {
          window.openPopup(params.ticker);
        }
      },
    });
  }

  // ----- Private input quick-select dropdown ---------------------------
  // When the user focuses the private-companies search input, show their
  // saved list as a dropdown so they don't have to type to see coverage.
  function initPrivateInputDropdown() {
    const input = document.getElementById('private-input');
    const dd = document.getElementById('private-input-dropdown');
    if (!input || !dd) return;

    function render(query) {
      // `privateCompanies` is a top-level `let` in app.js so it's script-scope
      // accessible as a bare identifier (but NOT on window). Feature-detect
      // both so we survive future refactors.
      let companies = [];
      try {
        // eslint-disable-next-line no-undef
        if (typeof privateCompanies !== 'undefined' && Array.isArray(privateCompanies)) {
          companies = privateCompanies;
        } else if (typeof window.privateCompanies !== 'undefined' && Array.isArray(window.privateCompanies)) {
          companies = window.privateCompanies;
        }
      } catch (_) { /* swallow */ }
      if (!companies.length) {
        dd.innerHTML = '<div class="pid-empty">No private companies yet. Type a name and click Add.</div>';
        return;
      }
      const q = (query || '').trim().toLowerCase();
      const matches = q
        ? companies.filter(c => (c.name || '').toLowerCase().includes(q) ||
                                 (c.subsector || '').toLowerCase().includes(q))
        : companies;
      if (!matches.length) {
        dd.innerHTML = `<div class="pid-empty">No matches in your coverage for “${q}”</div>`;
        return;
      }
      // Cap at 60 to keep the dropdown sane; group by subsector for scanability.
      const bySector = {};
      matches.slice(0, 60).forEach(c => {
        const key = c.subsector || 'Other';
        (bySector[key] = bySector[key] || []).push(c);
      });
      dd.innerHTML = Object.keys(bySector).sort().map(sector => {
        const rows = bySector[sector].map(c => {
          const val = c.valuation ? ` · ${c.valuation}` : '';
          const safeName = (c.name || '').replace(/"/g, '&quot;');
          return `<div class="pid-row" data-name="${safeName}" role="option" tabindex="0">
            <span class="pid-name">${c.name}</span>
            <span class="pid-meta">${sector}${val}</span>
          </div>`;
        }).join('');
        return `<div class="pid-group-label">${sector}</div>${rows}`;
      }).join('');
    }

    function open() { render(input.value); dd.hidden = false; }
    function close() { dd.hidden = true; }

    input.addEventListener('focus', open);
    input.addEventListener('input', () => render(input.value));
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

    // Click-outside to close
    document.addEventListener('mousedown', (e) => {
      if (!dd.contains(e.target) && e.target !== input) close();
    });

    // Row click -> jump the drilldown (open popup for that private co).
    dd.addEventListener('click', (e) => {
      const row = e.target.closest('.pid-row');
      if (!row) return;
      const name = row.dataset.name;
      input.value = name;
      close();
      // Prefer the existing private popup if available
      if (typeof window.openPrivatePopup === 'function') {
        window.openPrivatePopup(name);
      } else {
        // Fallback: scroll the row into view in the private table
        const table = document.getElementById('private-table');
        if (table) {
          const tr = [...table.querySelectorAll('tr')].find(r =>
            (r.textContent || '').toLowerCase().includes(name.toLowerCase()));
          if (tr) tr.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
    });
  }

  // ----- Summary strip (placeholder values until M2) --------------------
  function updateSummaryStripCounts() {
    const n = (typeof window.tickerList !== 'undefined') ? window.tickerList.length : 0;
    const el = document.getElementById('stat-universe-count');
    if (el) el.textContent = n || '—';
    // Median EV/Sales, avg move, earnings-7d, debate score — all derived
    // from the same visible universe used to render the coverage table.
    if (typeof updateCoverageSummaryTiles === 'function') {
      try { updateCoverageSummaryTiles(); } catch (_) {}
    } else if (typeof window.updateCoverageSummaryTiles === 'function') {
      try { window.updateCoverageSummaryTiles(); } catch (_) {}
    }
  }

  // ----- Boot ----------------------------------------------------------
  function boot() {
    initUniverseSelector();
    initNav();
    initModeToggle();
    initMoreSubtabs();
    initPrivateInputDropdown();
    registerSurfaces();
    window.SignalRouter.start();
    updateSummaryStripCounts();

    // Re-run summary counts after any watchlist mutation. The cleanest hook
    // is to wrap saveTickers (called by every add/remove/import path in
    // app.js). Falls back to button-click hooks if saveTickers isn't global.
    if (typeof window.saveTickers === 'function') {
      const _origSave = window.saveTickers;
      window.saveTickers = function () {
        const r = _origSave.apply(this, arguments);
        try { updateSummaryStripCounts(); } catch (_) {}
        return r;
      };
    }
    ['add-ticker-btn', 'compare-toggle-btn'].forEach(id => {
      document.getElementById(id)?.addEventListener('click', () => setTimeout(updateSummaryStripCounts, 400));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
