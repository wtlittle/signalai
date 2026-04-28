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
  function _loadMorePane(key) {
    if (key === 'briefing' && !window._briefingLoaded) {
      window._briefingLoaded = true;
      if (typeof loadWeeklyBriefing === 'function') loadWeeklyBriefing();
      else if (typeof window.loadWeeklyBriefing === 'function') window.loadWeeklyBriefing();
    }
    if (key === 'macro' && !window._macroLoaded) {
      window._macroLoaded = true;
      if (typeof renderMacroTab === 'function') renderMacroTab();
      else if (typeof window.renderMacroTab === 'function') window.renderMacroTab();
    }
    if (key === 'news' && !window._newsLoaded) {
      window._newsLoaded = true;
      if (typeof fetchNews === 'function') fetchNews();
      else if (typeof window.fetchNews === 'function') window.fetchNews();
    }
    if (key === 'alerts' && !window._alertsLoaded) {
      window._alertsLoaded = true;
      if (typeof renderAlertsTab === 'function') renderAlertsTab();
      else if (typeof window.renderAlertsTab === 'function') window.renderAlertsTab();
    }
  }

  function initMoreSubtabs() {
    const panes = document.querySelectorAll('[data-more-pane]');
    function activateMorePane(target) {
      document.querySelectorAll('.more-subtab').forEach(b =>
        b.classList.toggle('active', b.dataset.more === target)
      );
      panes.forEach(p =>
        p.classList.toggle('active', p.dataset.morePane === target)
      );
      _loadMorePane(target);
    }
    document.querySelectorAll('.more-subtab').forEach(btn => {
      btn.addEventListener('click', () => activateMorePane(btn.dataset.more));
    });
    window._activateMorePane = activateMorePane;
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
    // More: lazy-load the active sub-pane via _loadMorePane (no legacy clicks)
    window.SignalRouter.register('more', {
      onActivate: () => {
        const active = document.querySelector('.more-subtab.active');
        const key = active ? active.dataset.more : 'briefing';
        _loadMorePane(key);
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
