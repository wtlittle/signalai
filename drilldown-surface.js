/* ===== DRILLDOWN-SURFACE.JS =====
 * Wires the Drilldown surface UI into the Drilldown Library.
 *
 * Modes (a single section toggles between them based on state):
 *   1. EMPTY       — no ticker selected, library is empty           -> intro CTA
 *   2. SELECTED    — ticker is in router params, no saved versions  -> Run CTA + prompt
 *   3. LIBRARY     — versions exist                                 -> versions table
 *   4. VIEW        — viewing a specific saved version               -> iframe + actions
 *
 * This file does NOT itself talk to any LLM. The "Run institutional drilldown"
 * CTA opens the canonical prompt (drilldown_prompt.md) in a new Perplexity
 * thread pre-filled with the ticker, then provides the user a paste field to
 * save the resulting HTML back into the Library. This is the same workflow a
 * buyside analyst already uses; we just give them a single keep-everything
 * surface for it.
 *
 * Refresh re-uses the Run CTA but tags the new version trigger="refresh".
 */
(function (global) {
  'use strict';

  // Lazy reference; library script loads first via <script> tag order.
  function _lib() { return global.SignalDrilldownLibrary; }

  // ----- Utilities ------------------------------------------------------

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _fmtDate(iso) {
    if (!iso) return '—';
    try {
      var d = new Date(iso);
      return d.toLocaleString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: '2-digit', minute: '2-digit'
      });
    } catch (_) { return iso; }
  }

  function _fmtDateShort(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric'
      });
    } catch (_) { return iso; }
  }

  function _fmtPrice(n) {
    if (n == null || isNaN(n)) return '—';
    return '$' + Number(n).toFixed(2);
  }

  function _pctChange(from, to) {
    if (from == null || to == null || isNaN(from) || isNaN(to) || !from) return null;
    return ((to - from) / from) * 100;
  }

  function _fmtPct(p) {
    if (p == null || isNaN(p)) return '—';
    var sign = p >= 0 ? '+' : '';
    return sign + p.toFixed(1) + '%';
  }

  function _currentTickerData(ticker) {
    var data = (typeof global.tickerData !== 'undefined' && global.tickerData) ||
               (global.SignalCoverage && global.SignalCoverage.getState && global.SignalCoverage.getState().tickerData) ||
               null;
    if (!data) return null;
    return data[ticker] || null;
  }

  // The drilldown prompt is shipped as a static file alongside the app.
  // Cache the parsed text once per session so we can deep-link with it.
  var _promptCache = null;
  function _loadPrompt() {
    if (_promptCache) return Promise.resolve(_promptCache);
    return fetch('drilldown_prompt.md?v=' + Date.now())
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (txt) { _promptCache = txt || ''; return _promptCache; })
      .catch(function () { _promptCache = ''; return ''; });
  }

  // Build a Perplexity deep-link that opens the canonical drilldown prompt
  // pre-filled with the analyst's ticker. The prompt file is the single
  // source of truth — we just append the ticker context.
  function _buildPplxUrl(ticker, promptText) {
    var header = 'Run the canonical Signal Stack drilldown engine on ' + ticker + '. ';
    var instruction =
      'Use the prompt below verbatim. When you finish, output the full HTML ' +
      'note in a single fenced ```html block so I can paste it into my ' +
      'Drilldown Library.';
    var fullPrompt = header + instruction + '\n\n---\n\n' + (promptText || '');
    // Perplexity supports `q` query param for prefilled prompts.
    return 'https://www.perplexity.ai/?q=' + encodeURIComponent(fullPrompt);
  }

  // ----- Surface markup -------------------------------------------------

  function _ensureSurface() {
    var surface = document.querySelector('[data-surface="drilldown"]');
    if (!surface) return null;

    // Replace the M3 placeholder with our scaffold the first time we render.
    if (!surface.querySelector('[data-drilldown-root]')) {
      var head = surface.querySelector('.surface-header');
      var placeholder = surface.querySelector('#drilldown-placeholder');
      if (placeholder) placeholder.remove();
      // Existing coverage-controller MVP pane is fine to keep — we render
      // BELOW it with our richer tooling. But for a clean look we replace it.
      var oldMvp = surface.querySelector('#drilldown-mvp-pane');
      if (oldMvp) oldMvp.remove();

      var root = document.createElement('div');
      root.setAttribute('data-drilldown-root', '');
      root.className = 'drilldown-root';
      surface.appendChild(root);

      // Update the heading copy to match the new spec.
      if (head) {
        var h2 = head.querySelector('h2'); if (h2) h2.textContent = 'Drilldown';
        var sub = head.querySelector('.surface-sub');
        if (sub) sub.textContent = 'Generate institutional-grade primers. Saved with version history.';
      }
    }
    return surface.querySelector('[data-drilldown-root]');
  }

  // ----- Renderers ------------------------------------------------------

  // Per-ticker masthead with current ticker selector + run / refresh / library actions.
  function _renderMasthead(state) {
    var t = state.ticker || '';
    var rec = t ? _lib().getTicker(t) : null;
    var hasSaved = !!(rec && rec.versions && rec.versions.length);
    var live = t ? _currentTickerData(t) : null;

    var stats = '';
    if (live) {
      stats =
        '<div class="dd-mast-stats">' +
          '<div class="dd-mast-stat"><span>Price</span><strong>' + _esc(_fmtPrice(live.price)) + '</strong></div>' +
          '<div class="dd-mast-stat"><span>EV/Sales</span><strong>' + _esc(live.evSales != null ? Number(live.evSales).toFixed(1) + 'x' : '—') + '</strong></div>' +
          '<div class="dd-mast-stat"><span>Rev growth</span><strong>' + _esc(live.revenueGrowth != null ? (live.revenueGrowth >= 0 ? '+' : '') + Number(live.revenueGrowth).toFixed(1) + '%' : '—') + '</strong></div>' +
        '</div>';
    }

    var refreshBtn = hasSaved
      ? '<button type="button" class="btn-sm" data-dd-act="refresh">↻ Refresh drilldown</button>'
      : '';
    var saveBtn   = '<button type="button" class="btn-sm" data-dd-act="save-html">Save HTML to library</button>';
    var runBtn    = t
      ? '<button type="button" class="btn-sm btn-primary" data-dd-act="run">Run institutional drilldown</button>'
      : '';

    return (
      '<div class="dd-mast">' +
        '<div class="dd-mast-left">' +
          '<div class="dd-mast-ticker">' + _esc(t || '—') + '</div>' +
          '<div class="dd-mast-name">' + _esc(rec ? rec.company_name || t : (t ? 'No saved drilldown yet' : 'Pick a ticker to begin')) + '</div>' +
          stats +
        '</div>' +
        '<div class="dd-mast-right">' +
          '<div class="dd-mast-input">' +
            '<input type="text" id="dd-ticker-input" placeholder="Switch ticker…" autocomplete="off" spellcheck="false">' +
            '<button type="button" class="btn-sm" data-dd-act="go">Go</button>' +
          '</div>' +
          '<div class="dd-mast-actions">' +
            runBtn + refreshBtn + saveBtn +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function _renderEmpty(state) {
    var libRows = _renderLibraryList(state, true);
    return (
      '<div class="dd-empty">' +
        '<div class="dd-empty-card">' +
          '<div class="dd-empty-eyebrow">Institutional drilldown</div>' +
          '<div class="dd-empty-title">Generate a buyside-grade research note in one click</div>' +
          '<div class="dd-empty-body">' +
            'Drilldowns follow the Signal Stack canonical prompt: valuation, KPIs, catalysts, ' +
            'management, competitive matrix, risks, and 4+ Chart.js charts. Notes are ' +
            'saved with version history so you can track how a thesis evolved.' +
          '</div>' +
          '<div class="dd-empty-actions">' +
            '<input type="text" id="dd-empty-ticker" placeholder="Enter ticker (e.g. ZS, NVDA, 1364.HK)" autocomplete="off" spellcheck="false">' +
            '<button type="button" class="btn-primary" data-dd-act="empty-go">Start drilldown</button>' +
          '</div>' +
        '</div>' +
        libRows +
      '</div>'
    );
  }

  function _renderRunPanel(state) {
    var t = state.ticker;
    return (
      '<div class="dd-run-panel">' +
        '<div class="dd-run-step">' +
          '<div class="dd-step-num">1</div>' +
          '<div class="dd-step-body">' +
            '<div class="dd-step-title">Run the canonical drilldown prompt</div>' +
            '<div class="dd-step-sub">Opens a fresh Perplexity thread pre-loaded with the institutional prompt for <strong>' + _esc(t) + '</strong>. The model will return a full HTML note in a fenced block.</div>' +
            '<button type="button" class="btn-primary" data-dd-act="run">Run institutional drilldown for ' + _esc(t) + '</button>' +
          '</div>' +
        '</div>' +
        '<div class="dd-run-step">' +
          '<div class="dd-step-num">2</div>' +
          '<div class="dd-step-body">' +
            '<div class="dd-step-title">Paste the HTML back to save it</div>' +
            '<div class="dd-step-sub">Copy the entire HTML block from the run output, paste below, and save. The Library auto-versions every save so you can compare drafts later.</div>' +
            '<textarea id="dd-html-input" class="dd-html-input" placeholder="Paste the full HTML note here…" rows="6"></textarea>' +
            '<div class="dd-run-meta">' +
              '<label>Trigger ' +
                '<select id="dd-trigger-input">' +
                  '<option value="manual">Manual run</option>' +
                  '<option value="refresh">Refresh</option>' +
                  '<option value="earnings_alert">Earnings alert</option>' +
                '</select>' +
              '</label>' +
              '<button type="button" class="btn-sm btn-primary" data-dd-act="save-pasted">Save to library</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function _renderVersionsTable(state) {
    var rec = _lib().getTicker(state.ticker);
    if (!rec || !rec.versions.length) return '';
    var sorted = rec.versions.slice().sort(function (a, b) { return b.version - a.version; });
    var rows = sorted.map(function (v) {
      var deltaPct = v.price_at_generation != null && state.livePrice != null
        ? _pctChange(v.price_at_generation, state.livePrice) : null;
      var deltaCls = deltaPct == null ? '' : (deltaPct >= 0 ? 'val-pos' : 'val-neg');
      return (
        '<tr data-dd-ver="' + v.version + '">' +
          '<td><strong>v' + v.version + '</strong></td>' +
          '<td>' + _esc(_fmtDate(v.generated_at)) + '</td>' +
          '<td><span class="dd-trigger dd-trigger-' + _esc(v.trigger) + '">' + _esc(v.trigger || 'manual') + '</span></td>' +
          '<td>' + _esc(_fmtPrice(v.price_at_generation)) + '</td>' +
          '<td class="' + deltaCls + '">' + _esc(deltaPct == null ? '—' : _fmtPct(deltaPct)) + '</td>' +
          '<td class="dd-row-actions">' +
            '<button type="button" class="btn-sm" data-dd-act="open" data-ver="' + v.version + '">Open</button>' +
            '<button type="button" class="btn-sm btn-ghost" data-dd-act="delete-ver" data-ver="' + v.version + '">Delete</button>' +
          '</td>' +
        '</tr>'
      );
    }).join('');
    return (
      '<div class="dd-card">' +
        '<div class="dd-card-head">' +
          '<div class="dd-card-title">Saved versions for ' + _esc(state.ticker) + '</div>' +
          '<div class="dd-card-sub">' + sorted.length + ' version' + (sorted.length === 1 ? '' : 's') + ' · price delta is current vs. price at generation</div>' +
        '</div>' +
        '<table class="dd-versions">' +
          '<thead><tr><th>Version</th><th>Generated</th><th>Trigger</th><th>Price at gen</th><th>Δ vs. now</th><th></th></tr></thead>' +
          '<tbody>' + rows + '</tbody>' +
        '</table>' +
      '</div>'
    );
  }

  function _renderLibraryList(state, compact) {
    var entries = _lib().list();
    if (!entries.length) {
      if (compact) return '';
      return (
        '<div class="dd-card">' +
          '<div class="dd-card-head"><div class="dd-card-title">Drilldown Library</div></div>' +
          '<div class="dd-empty-row">No saved drilldowns yet. Run one above to start your library.</div>' +
        '</div>'
      );
    }
    var rows = entries.map(function (e) {
      var live = _currentTickerData(e.ticker);
      var livePrice = live ? live.price : null;
      var delta = _pctChange(e.latest_price, livePrice);
      var deltaCls = delta == null ? '' : (delta >= 0 ? 'val-pos' : 'val-neg');
      return (
        '<tr data-dd-ticker="' + _esc(e.ticker) + '">' +
          '<td><strong>' + _esc(e.ticker) + '</strong></td>' +
          '<td>' + _esc(e.company_name || e.ticker) + '</td>' +
          '<td class="num">' + e.version_count + '</td>' +
          '<td>' + _esc(_fmtDateShort(e.latest_generated_at)) + '</td>' +
          '<td>' + _esc(_fmtPrice(e.latest_price)) + '</td>' +
          '<td>' + _esc(_fmtPrice(livePrice)) + '</td>' +
          '<td class="' + deltaCls + '">' + _esc(delta == null ? '—' : _fmtPct(delta)) + '</td>' +
          '<td class="dd-row-actions">' +
            '<button type="button" class="btn-sm" data-dd-act="select" data-ticker="' + _esc(e.ticker) + '">Open</button>' +
          '</td>' +
        '</tr>'
      );
    }).join('');
    var usage = _lib().storageUsage();
    return (
      '<div class="dd-card">' +
        '<div class="dd-card-head">' +
          '<div class="dd-card-title">Drilldown Library</div>' +
          '<div class="dd-card-sub">' + entries.length + ' ticker' + (entries.length === 1 ? '' : 's') + ' · ' + usage.kb + ' KB stored locally</div>' +
        '</div>' +
        '<table class="dd-library">' +
          '<thead><tr><th>Ticker</th><th>Name</th><th class="num">Versions</th><th>Latest</th><th>Price at gen</th><th>Price now</th><th>Δ</th><th></th></tr></thead>' +
          '<tbody>' + rows + '</tbody>' +
        '</table>' +
        '<div class="dd-library-foot">' +
          '<button type="button" class="btn-sm btn-ghost" data-dd-act="export">Export library</button>' +
          '<button type="button" class="btn-sm btn-ghost" data-dd-act="import">Import library</button>' +
          '<input type="file" id="dd-import-file" accept="application/json,.json" style="display:none">' +
        '</div>' +
      '</div>'
    );
  }

  function _renderViewer(state) {
    var v = _lib().getVersion(state.ticker, state.openVersion);
    if (!v) return '<div class="dd-card"><div class="dd-empty-row">Version not found.</div></div>';
    var live = _currentTickerData(state.ticker);
    var delta = _pctChange(v.price_at_generation, live ? live.price : null);
    var deltaCls = delta == null ? '' : (delta >= 0 ? 'val-pos' : 'val-neg');
    return (
      '<div class="dd-card dd-viewer-card">' +
        '<div class="dd-viewer-head">' +
          '<div>' +
            '<div class="dd-viewer-eyebrow">' + _esc(state.ticker) + ' · v' + v.version + ' · ' + _esc(v.trigger) + '</div>' +
            '<div class="dd-viewer-title">' + _esc(_fmtDate(v.generated_at)) + '</div>' +
            '<div class="dd-viewer-sub">Price at gen ' + _esc(_fmtPrice(v.price_at_generation)) +
              (live && live.price != null ? ' · Now ' + _esc(_fmtPrice(live.price)) + ' (<span class="' + deltaCls + '">' + _esc(_fmtPct(delta)) + '</span>)' : '') +
            '</div>' +
          '</div>' +
          '<div class="dd-viewer-actions">' +
            '<button type="button" class="btn-sm" data-dd-act="back-to-versions">← Back</button>' +
            '<button type="button" class="btn-sm" data-dd-act="open-window" data-ver="' + v.version + '">Open in new tab</button>' +
            '<button type="button" class="btn-sm" data-dd-act="copy-html" data-ver="' + v.version + '">Copy HTML</button>' +
            '<button type="button" class="btn-sm btn-primary" data-dd-act="refresh">↻ Refresh</button>' +
          '</div>' +
        '</div>' +
        '<iframe class="dd-viewer-frame" data-dd-frame sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer"></iframe>' +
      '</div>'
    );
  }

  // ----- State + render dispatch ---------------------------------------

  var state = {
    ticker: null,
    openVersion: null,    // when set, render viewer
    showRunPanel: false,  // toggled when user clicks Run / Refresh
    livePrice: null,
  };

  function setTicker(t) {
    state.ticker = t ? String(t).trim().toUpperCase() : null;
    state.openVersion = null;
    state.showRunPanel = false;
    var live = state.ticker ? _currentTickerData(state.ticker) : null;
    state.livePrice = live ? live.price : null;
    render();
  }

  function render() {
    var root = _ensureSurface();
    if (!root) return;

    var t = state.ticker;
    var lib = _lib();
    var rec = t ? lib.getTicker(t) : null;

    var html = '';
    if (!t) {
      html = _renderEmpty(state);
    } else {
      html += _renderMasthead(state);
      if (state.openVersion != null) {
        html += _renderViewer(state);
      } else {
        if (state.showRunPanel || !rec || !rec.versions.length) {
          html += _renderRunPanel(state);
        }
        html += _renderVersionsTable(state);
        html += _renderLibraryList(state, false);
      }
    }
    root.innerHTML = html;

    // Inject the iframe content if viewer is mounted (avoids huge srcdoc attrs).
    if (state.openVersion != null) {
      var frame = root.querySelector('[data-dd-frame]');
      var v = lib.getVersion(t, state.openVersion);
      if (frame && v) {
        try {
          var blob = new Blob([v.html], { type: 'text/html' });
          frame.src = URL.createObjectURL(blob);
        } catch (_) {
          frame.srcdoc = v.html;
        }
      }
    }
  }

  // ----- Action handler -------------------------------------------------

  function _handleAction(act, target) {
    var lib = _lib();
    if (act === 'go') {
      var input = document.getElementById('dd-ticker-input');
      if (input) {
        var v = (input.value || '').trim().toUpperCase();
        if (v && global.SignalRouter) global.SignalRouter.go('drilldown', { ticker: v });
      }
      return;
    }
    if (act === 'empty-go') {
      var ei = document.getElementById('dd-empty-ticker');
      if (ei) {
        var v2 = (ei.value || '').trim().toUpperCase();
        if (v2 && global.SignalRouter) global.SignalRouter.go('drilldown', { ticker: v2 });
      }
      return;
    }
    if (act === 'select') {
      var t = target.dataset.ticker;
      if (t && global.SignalRouter) global.SignalRouter.go('drilldown', { ticker: t });
      return;
    }
    if (act === 'open') {
      state.openVersion = parseInt(target.dataset.ver, 10);
      render();
      return;
    }
    if (act === 'back-to-versions') {
      state.openVersion = null;
      render();
      return;
    }
    if (act === 'open-window') {
      var v3 = lib.getVersion(state.ticker, parseInt(target.dataset.ver, 10));
      if (!v3) return;
      var blob = new Blob([v3.html], { type: 'text/html' });
      var url = URL.createObjectURL(blob);
      global.open(url, '_blank', 'noopener');
      return;
    }
    if (act === 'copy-html') {
      var v4 = lib.getVersion(state.ticker, parseInt(target.dataset.ver, 10));
      if (!v4) return;
      try {
        navigator.clipboard.writeText(v4.html).then(function () {
          target.textContent = 'Copied';
          setTimeout(function () { target.textContent = 'Copy HTML'; }, 1500);
        });
      } catch (_) {}
      return;
    }
    if (act === 'delete-ver') {
      var ver = parseInt(target.dataset.ver, 10);
      if (!confirm('Delete v' + ver + ' of ' + state.ticker + '?')) return;
      lib.removeVersion(state.ticker, ver);
      render();
      return;
    }
    if (act === 'run' || act === 'refresh') {
      _runDrilldown(act === 'refresh' ? 'refresh' : 'manual');
      return;
    }
    if (act === 'save-html') {
      state.showRunPanel = true;
      render();
      var ta = document.getElementById('dd-html-input');
      if (ta) ta.focus();
      return;
    }
    if (act === 'save-pasted') {
      _saveFromTextarea();
      return;
    }
    if (act === 'export') {
      _exportLibrary();
      return;
    }
    if (act === 'import') {
      var f = document.getElementById('dd-import-file');
      if (f) f.click();
      return;
    }
  }

  function _runDrilldown(trigger) {
    if (!state.ticker) return;
    state.showRunPanel = true;
    render();
    _loadPrompt().then(function (txt) {
      var url = _buildPplxUrl(state.ticker, txt);
      try { global.open(url, '_blank', 'noopener'); } catch (_) {}
      // Persist the trigger so the next save defaults to it.
      var sel = document.getElementById('dd-trigger-input');
      if (sel) sel.value = trigger;
      var ta = document.getElementById('dd-html-input');
      if (ta) ta.focus();
    });
  }

  function _saveFromTextarea() {
    if (!state.ticker) return;
    var ta = document.getElementById('dd-html-input');
    var sel = document.getElementById('dd-trigger-input');
    if (!ta || !ta.value.trim()) {
      alert('Paste the full HTML note before saving.');
      return;
    }
    var html = _extractHtmlBlock(ta.value);
    var live = _currentTickerData(state.ticker);
    try {
      var entry = _lib().save(state.ticker, {
        html: html,
        trigger: (sel && sel.value) || 'manual',
        price: live ? live.price : null,
        target: live ? live.priceTarget : null,
      });
      ta.value = '';
      state.showRunPanel = false;
      state.openVersion = entry.version; // jump straight into the new version
      render();
    } catch (e) {
      // _write already alerted the user on quota errors.
    }
  }

  // The model often returns the note inside ```html ... ``` fences. Strip
  // them if present so the iframe renders cleanly. If no fence is found,
  // assume the textarea already holds raw HTML.
  function _extractHtmlBlock(s) {
    if (!s) return '';
    var fenceMatch = s.match(/```(?:html)?\s*\n([\s\S]*?)\n```/i);
    if (fenceMatch) return fenceMatch[1].trim();
    return s.trim();
  }

  function _exportLibrary() {
    var json = _lib().export();
    var blob = new Blob([json], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'signalstack_drilldown_library_' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { a.remove(); URL.revokeObjectURL(a.href); }, 0);
  }

  function _importLibraryFile(file) {
    if (!file) return;
    var fr = new FileReader();
    fr.onload = function () {
      var ok = _lib().import(String(fr.result || ''));
      if (!ok) alert('Import failed — file did not contain a valid Drilldown Library JSON.');
      render();
    };
    fr.readAsText(file);
  }

  // ----- Boot -----------------------------------------------------------

  function _bindGlobalHandlers(root) {
    if (root.__ddBound) return;
    root.__ddBound = true;
    root.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('[data-dd-act]');
      if (!btn) {
        var row = e.target.closest && e.target.closest('tr[data-dd-ver], tr[data-dd-ticker]');
        if (row && !e.target.closest('button')) {
          if (row.dataset.ddVer) {
            state.openVersion = parseInt(row.dataset.ddVer, 10);
            render();
          } else if (row.dataset.ddTicker) {
            global.SignalRouter && global.SignalRouter.go('drilldown', { ticker: row.dataset.ddTicker });
          }
        }
        return;
      }
      e.preventDefault();
      _handleAction(btn.dataset.ddAct, btn);
    });

    root.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      if (e.target && (e.target.id === 'dd-ticker-input' || e.target.id === 'dd-empty-ticker')) {
        e.preventDefault();
        var act = e.target.id === 'dd-empty-ticker' ? 'empty-go' : 'go';
        _handleAction(act, e.target);
      }
    });

    root.addEventListener('change', function (e) {
      if (e.target && e.target.id === 'dd-import-file') {
        _importLibraryFile(e.target.files && e.target.files[0]);
      }
    });
  }

  function _wireSurface() {
    if (!global.SignalRouter) return;
    // Replace the Drilldown handler set by coverage-controller. We compose its
    // legacy openPopup behavior so existing flows keep working — but only
    // when the user explicitly asked for a popup-style preview.
    global.SignalRouter.register('drilldown', {
      onActivate: function (params) {
        var t = params && params.ticker ? params.ticker : null;
        setTicker(t);
        var root = _ensureSurface();
        if (root) _bindGlobalHandlers(root);
      }
    });
  }

  function boot() {
    _wireSurface();
    // Re-render when the library changes (e.g. import, delete from another tab).
    document.addEventListener('signalstack:drilldown-library-changed', function () {
      // Only re-render if we are currently looking at the drilldown surface.
      var sur = document.querySelector('[data-surface="drilldown"]');
      if (sur && sur.classList.contains('active') === false && !sur.hidden) render();
      else if (sur && !sur.hidden) render();
    });
    // Refresh price deltas when ticker data hydrates.
    document.addEventListener('signalstack:route-changed', function (e) {
      if (e && e.detail && e.detail.surface === 'drilldown') {
        // Router already invoked our onActivate above; nothing else to do.
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(boot, 60); });
  } else {
    setTimeout(boot, 60);
  }

  global.SignalDrilldownSurface = {
    setTicker: setTicker,
    rerender: render,
    state: function () { return Object.assign({}, state); },
  };
})(typeof window !== 'undefined' ? window : this);
