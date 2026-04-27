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

  // ----- More sub-tabs (briefing / macro / news / alerts) ---------------
  function initMoreSubtabs() {
    const panes = document.querySelectorAll('[data-more-pane]');
    document.querySelectorAll('.more-subtab').forEach(btn => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.more;
        document.querySelectorAll('.more-subtab').forEach(b => b.classList.toggle('active', b === btn));
        panes.forEach(p => p.classList.toggle('active', p.dataset.morePane === target));
      });
    });
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
    // More: when activated, click the legacy tab underlying the active sub-pane
    window.SignalRouter.register('more', {
      onActivate: () => {
        const active = document.querySelector('.more-subtab.active');
        const key = active ? active.dataset.more : 'briefing';
        const map = { briefing: 'briefing', macro: 'macro', news: 'news', alerts: 'alerts' };
        const legacyBtn = document.querySelector(`.tab-btn[data-tab="${map[key]}"]`);
        if (legacyBtn && typeof legacyBtn.click === 'function') legacyBtn.click();
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

  // ----- Coverage ribbon flag filters ----------------------------------
  // Public surface: window.SignalCoverageFilters
  //   .getActiveFlags() -> string[] of active flag names
  //   .getVisibleTickers() -> tickerList filtered by AND-combination of
  //     active flags. Returns the full tickerList when no flags are active.
  // Snapshot data is loaded once at boot via SignalSnapshot.fetchWithFallback
  // and cached on the controller. Each flag toggle re-renders the coverage
  // table + KPI tiles + universe count using the same filtered list.
  const SignalCoverageFilters = (function () {
    const activeFlags = new Set();
    let _estimates = {};
    let _shortInterest = {};
    let _ready = false;

    function getActiveFlags() {
      return Array.from(activeFlags);
    }

    function _earningsTickersWithin7d() {
      const ed = (typeof window.earningsData !== 'undefined' && window.earningsData) ? window.earningsData : null;
      const upcoming = (ed && Array.isArray(ed.upcoming)) ? ed.upcoming : [];
      const set = new Set();
      for (const u of upcoming) {
        if (u && typeof u.days_until === 'number' && u.days_until >= 0 && u.days_until <= 7 && u.ticker) {
          set.add(u.ticker);
        }
      }
      return set;
    }

    function _matchesFlag(ticker, flag, ctx) {
      if (flag === 'earnings_7d') {
        return ctx.earningsSet.has(ticker);
      }
      if (flag === 'revision_up') {
        const e = _estimates[ticker];
        if (!e) return false;
        return (e.revisionsUp7d > 0) || (e.revisionsUp30d > 0) || (e.fy1RevisionsUp30d > 0);
      }
      if (flag === 'revision_dn') {
        const e = _estimates[ticker];
        if (!e) return false;
        return (e.revisionsDown7d > 0) || (e.revisionsDown30d > 0) || (e.fy1RevisionsDown30d > 0);
      }
      if (flag === 'crowded_short') {
        const si = _shortInterest[ticker];
        const v = si && si.current && si.current.shortPercentOfFloat;
        return typeof v === 'number' && v >= 10;
      }
      // Unknown flag (e.g. insider_buy, which is inert) -> never matches.
      return false;
    }

    function getVisibleTickers() {
      const list = (typeof window.tickerList !== 'undefined' && Array.isArray(window.tickerList))
        ? window.tickerList
        : [];
      if (activeFlags.size === 0) return list.slice();
      const ctx = { earningsSet: _earningsTickersWithin7d() };
      const flags = Array.from(activeFlags);
      return list.filter(t => flags.every(f => _matchesFlag(t, f, ctx)));
    }

    function _rerender() {
      try { if (typeof window.renderTable === 'function') window.renderTable(); } catch (_) {}
      try { if (typeof window.updateCoverageSummaryTiles === 'function') window.updateCoverageSummaryTiles(); } catch (_) {}
      try { updateSummaryStripCounts(); } catch (_) {}
    }

    function _onFlagClick(btn) {
      const flag = btn.getAttribute('data-flag');
      if (!flag) return;
      // insider_buy is inert until insider_activity snapshot exists.
      if (flag === 'insider_buy') return;
      if (activeFlags.has(flag)) {
        activeFlags.delete(flag);
        btn.classList.remove('active');
      } else {
        activeFlags.add(flag);
        btn.classList.add('active');
      }
      _rerender();
    }

    async function _loadSnapshot() {
      if (!(window.SignalSnapshot && typeof window.SignalSnapshot.fetchWithFallback === 'function')) {
        return;
      }
      try {
        const resp = await window.SignalSnapshot.fetchWithFallback('data-snapshot.json', { cacheBust: true });
        if (!resp || !resp.ok) return;
        const snap = await resp.json();
        _estimates = (snap && snap.estimates) || {};
        _shortInterest = (snap && snap.short_interest) || {};
        _ready = true;
        // If user activated flags before snapshot loaded, re-apply now.
        if (activeFlags.size > 0) _rerender();
      } catch (err) {
        console.warn('[SignalCoverageFilters] snapshot load failed', err);
      }
    }

    function init() {
      const buttons = document.querySelectorAll('.ribbon-flag');
      buttons.forEach(btn => {
        const flag = btn.getAttribute('data-flag');
        if (flag === 'insider_buy') {
          btn.disabled = true;
          btn.setAttribute('title', 'Requires insider_activity snapshot');
          btn.classList.add('disabled');
          return;
        }
        btn.addEventListener('click', () => _onFlagClick(btn));
      });
      _loadSnapshot();
    }

    return { init, getActiveFlags, getVisibleTickers };
  })();
  window.SignalCoverageFilters = SignalCoverageFilters;

  // ----- Summary strip (placeholder values until M2) --------------------
  function updateSummaryStripCounts() {
    const visible = (window.SignalCoverageFilters && typeof window.SignalCoverageFilters.getVisibleTickers === 'function')
      ? window.SignalCoverageFilters.getVisibleTickers()
      : ((typeof window.tickerList !== 'undefined') ? window.tickerList : []);
    const n = Array.isArray(visible) ? visible.length : 0;
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
    SignalCoverageFilters.init();
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
