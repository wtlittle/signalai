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

  // Relative timestamp helper ("3h ago", "2d ago"). Returns '—' on bad input.
  function _relTime(iso) {
    if (!iso) return '—';
    var ts;
    try { ts = new Date(iso).getTime(); } catch (_) { return '—'; }
    if (!ts || isNaN(ts)) return '—';
    var diff = Math.max(0, Date.now() - ts);
    var s = Math.floor(diff / 1000);
    if (s < 60)        return s + 's ago';
    var m = Math.floor(s / 60);
    if (m < 60)        return m + 'm ago';
    var h = Math.floor(m / 60);
    if (h < 24)        return h + 'h ago';
    var d = Math.floor(h / 24);
    if (d < 30)        return d + 'd ago';
    var mo = Math.floor(d / 30);
    if (mo < 12)       return mo + 'mo ago';
    return Math.floor(mo / 12) + 'y ago';
  }

  function _currentTickerData(ticker) {
    var data = (typeof global.tickerData !== 'undefined' && global.tickerData) ||
               (global.SignalCoverage && global.SignalCoverage.getState && global.SignalCoverage.getState().tickerData) ||
               null;
    // app.js declares tickerData as a module-scope `let`; fall back to the
    // bare-identifier eval pattern used elsewhere (coverage-controller.js).
    if (!data) {
      try {
        var ref = (0, eval)('typeof tickerData !== "undefined" ? tickerData : null');
        if (ref) data = ref;
      } catch (_) { /* ignore */ }
    }
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

  // Build the SHORT prefill that goes in the Perplexity URL `q` param.
  // We deliberately do NOT URL-encode the entire 14KB prompt — that produces
  // a request-URI long enough to trigger nginx/CloudFront `414 URI Too Large`
  // on perplexity.ai. Instead, we ship a compact instruction in the URL and
  // copy the full prompt to the clipboard so the analyst pastes it as the
  // first message in the new thread.
  function _buildShortPrefill(ticker) {
    return (
      'Run the canonical Signal Stack institutional drilldown engine on ' + ticker + '. ' +
      'I will paste the full canonical prompt as my next message. ' +
      'When you finish, output the full HTML note in a single fenced ```html block ' +
      'so I can paste it into my Drilldown Library.'
    );
  }
  function _buildPplxUrl(ticker /* , promptText (unused — see clipboard path) */) {
    return 'https://www.perplexity.ai/?q=' + encodeURIComponent(_buildShortPrefill(ticker));
  }

  // Build a structured pre-fill block from the live app state. Injected into
  // the [SIGNAL_DATA_BLOCK] placeholder in the canonical prompt so the model
  // treats the numbers as ground truth rather than fetching them again.
  function _buildSignalDataBlock(ticker) {
    var live = _currentTickerData(ticker);
    if (!live) return '[SIGNAL_DATA_BLOCK]\n(No pre-fetched data available \u2014 collect all fields from search tools.)\n';

    function fmt(v, decimals) {
      if (v == null || isNaN(v)) return 'MISSING';
      return decimals != null ? Number(v).toFixed(decimals) : String(v);
    }
    function fmtPct(v) { return v == null ? 'MISSING' : (Number(v) * 100).toFixed(1) + '%'; }
    function fmtB(v)   { return v == null ? 'MISSING' : '$' + (Number(v) / 1e9).toFixed(2) + 'B'; }

    var q = live;          // quote-level fields live on the tickerData object
    var est = live.estimates || {};
    var hist = (live.earningsHistory || []).slice(-8);

    var lines = [
      '[SIGNAL_DATA_BLOCK]',
      'Generated: ' + new Date().toISOString(),
      '',
      '## QUOTE',
      'Ticker: ' + ticker,
      'Price: ' + fmt(q.price, 2),
      'MarketCap: ' + fmtB(q.marketCap),
      'EnterpriseValue: ' + fmtB(q.enterpriseValue),
      'Sector: ' + (q.sector || 'MISSING'),
      'Industry: ' + (q.industry || 'MISSING'),
      '52wHigh: ' + fmt(q.fiftyTwoWeekHigh, 2),
      '52wLow: ' + fmt(q.fiftyTwoWeekLow, 2),
      'Beta: ' + fmt(q.beta, 2),
      'ForwardPE: ' + fmt(q.forwardPE, 1),
      'TrailingPE: ' + fmt(q.trailingPE, 1),
      'EVRevenue: ' + fmt(q.enterpriseToRevenue, 2),
      'EVEBITDA: ' + fmt(q.enterpriseToEbitda, 2),
      'RevenueGrowth: ' + fmtPct(q.revenueGrowth),
      'GrossMargin: ' + fmtPct(q.grossMargins),
      'OperatingMargin: ' + fmtPct(q.operatingMargins),
      'FCF: ' + fmtB(q.freeCashflow),
      'TotalRevenue: ' + fmtB(q.totalRevenue),
      'ConsensusTarget: ' + fmt(q.targetMeanPrice, 2),
      'TargetHigh: ' + fmt(q.targetHighPrice, 2),
      'TargetLow: ' + fmt(q.targetLowPrice, 2),
      'ConsensusRating: ' + (q.recommendationKey || 'MISSING'),
      'AnalystCount: ' + fmt(q.numberOfAnalystOpinions),
      '',
      '## ESTIMATES',
      'NQ_RevEst: ' + fmtB(est.nextQRevEst),
      'NQ_RevGrowth: ' + fmtPct(est.nextQRevGrowth),
      'NQ_EpsEst: ' + fmt(est.nextQEpsEst, 2),
      'NQ_EpsGrowth: ' + fmtPct(est.nextQEpsGrowth),
      'FY1_RevEst: ' + fmtB(est.fy1RevEst),
      'FY1_RevGrowth: ' + fmtPct(est.fy1RevGrowth),
      'FY1_EpsEst: ' + fmt(est.fy1EpsEst, 2),
      'FY2_RevEst: ' + fmtB(est.fy2RevEst),
      'FY2_RevGrowth: ' + fmtPct(est.fy2RevGrowth),
      'FY2_EpsEst: ' + fmt(est.fy2EpsEst, 2),
      'GuideRevHigh: ' + fmtB(est.guideRevHigh),
      'GuideRevLow: ' + fmtB(est.guideRevLow),
      'EPSTrend_Now: ' + fmt(est.epsTrendCurrent, 2),
      'EPSTrend_30d: ' + fmt(est.epsTrend30d, 2),
      'EPSTrend_90d: ' + fmt(est.epsTrend90d, 2),
      'RevisionsUp_30d: ' + fmt(est.revisionsUp30d),
      'RevisionsDown_30d: ' + fmt(est.revisionsDown30d),
      'FCFMargin: ' + fmtPct(est.fcfMargin),
      'RevenueLTM: ' + fmtB(est.revenueLtm),
      '',
      '## EARNINGS HISTORY (last 8 quarters)',
    ];

    if (hist.length) {
      lines.push('Quarter | Rev Beat% | EPS Beat% | 1d Move | GuidanceTone');
      hist.forEach(function(h) {
        lines.push(
          (h.period || '?') + ' | ' +
          (h.revBeatPct != null ? h.revBeatPct.toFixed(1) + '%' : 'MISSING') + ' | ' +
          (h.epsBeatPct != null ? h.epsBeatPct.toFixed(1) + '%' : 'MISSING') + ' | ' +
          (h.oneDayReturn != null ? (h.oneDayReturn * 100).toFixed(1) + '%' : 'MISSING') + ' | ' +
          (h.guidanceTone || 'MISSING')
        );
      });
    } else {
      lines.push('(No earnings history cached \u2014 source from search tools)');
    }

    // Comps
    var comps = live.crossSectorComps || (live.comps && live.comps.peers) || [];
    lines.push('', '## CROSS-SECTOR COMPS');
    if (comps.length) {
      lines.push('Ticker | EVRev | PEFwd | GrossMargin | FCFMargin | RevGrowth');
      comps.forEach(function(c) {
        lines.push(
          (c.ticker || '?') + ' | ' +
          fmt(c.evRevenue, 2) + 'x | ' +
          fmt(c.forwardPE, 1) + 'x | ' +
          fmtPct(c.grossMargin) + ' | ' +
          fmtPct(c.fcfMargin) + ' | ' +
          fmtPct(c.revenueGrowth)
        );
      });
    } else {
      lines.push('(No comps cached \u2014 source from search tools)');
    }

    // Market intel (from Supabase via app state, if present)
    var mi = live.marketIntel || null;
    lines.push('', '## MARKET INTEL');
    if (mi) {
      lines.push('TAM: ' + (mi.tam_label || 'MISSING'));
      lines.push('TAMSource: ' + (mi.tam_source || 'MISSING'));
      lines.push('CategoryGrowthRate: ' + (mi.growth_rate_label || 'MISSING'));
      lines.push('HarvestedAt: ' + (mi.harvested_at || 'MISSING'));
      lines.push('StructuralDrivers: ' + (mi.structural_drivers || 'MISSING'));
      lines.push('AIMLContext: ' + (mi.ai_ml_context || 'MISSING'));
    } else {
      lines.push('(MISSING \u2014 model must source TAM and category growth from search tools)');
    }

    lines.push('', '[/SIGNAL_DATA_BLOCK]');
    return lines.join('\n');
  }

  // Build the FULL prompt text (header + instruction + canonical body) that
  // we copy to the clipboard. This is what the analyst pastes into the new
  // Perplexity thread to run the canonical drilldown. When `part === 'p2'`,
  // strips the Part 1 output spec so only Part 2 sections (7-11) remain.
  function _buildFullPrompt(ticker, promptText, part) {
    part = part || 'p1';
    var dataBlock = _buildSignalDataBlock(ticker);

    // Splice the data block into the prompt text by replacing the
    // [SIGNAL_DATA_BLOCK] placeholder token.
    var body = (promptText || '').replace('[SIGNAL_DATA_BLOCK]', dataBlock);

    // For Part 2, strip everything from "OUTPUT STRUCTURE \u2014 PART 1" down
    // to (but not including) "OUTPUT STRUCTURE \u2014 PART 2" so the model
    // only sees the Part 2 output spec, writing rules, and quality check.
    if (part === 'p2') {
      var p2Marker = 'OUTPUT STRUCTURE \u2014 PART 2 of 2';
      var idx = body.indexOf(p2Marker);
      if (idx !== -1) {
        // Keep the preamble (audience, data block, data collection rules) +
        // everything from Part 2 onward.
        var preambleEnd = body.indexOf('OUTPUT STRUCTURE \u2014 PART 1');
        var preamble = preambleEnd !== -1 ? body.slice(0, preambleEnd) : '';
        body = preamble + body.slice(idx);
      }
      body = 'This is PART 2 of the Signal Stack institutional drilldown for ' + ticker + '. ' +
             'Part 1 (Sections 1\u20136) has already been generated. ' +
             'Produce only the Part 2 sections (7\u201311) as a self-contained HTML file ' +
             'using the same branding and CSS as Part 1.\n\n' + body;
    }

    var header = 'Run the canonical Signal Stack drilldown engine on ' + ticker +
      ' (' + (part === 'p2' ? 'PART 2 of 2' : 'PART 1 of 2') + '). ';
    var instruction =
      'Use the prompt below verbatim. The [SIGNAL_DATA_BLOCK] has been pre-filled ' +
      'with live data \u2014 treat it as ground truth. When you finish, output the full HTML ' +
      'note in a single fenced ```html block.';
    return header + instruction + '\n\n---\n\n' + body;
  }

  // Convenience wrapper that forces Part 2 framing.
  function _buildPart2Prompt(ticker, promptText) {
    return _buildFullPrompt(ticker, promptText, 'p2');
  }

  // Best-effort clipboard write — navigator.clipboard requires a secure
  // context AND a user gesture; on some embedded hosts neither is true, so
  // we fall back to a hidden <textarea> + execCommand('copy') which works
  // in iframes.
  function _copyToClipboard(text) {
    if (!text) return Promise.resolve(false);
    try {
      if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
        return global.navigator.clipboard.writeText(text).then(function () { return true; }, function () {
          return _execCopyFallback(text);
        });
      }
    } catch (_) {}
    return Promise.resolve(_execCopyFallback(text));
  }
  function _execCopyFallback(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '0';
      ta.style.left = '0';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return !!ok;
    } catch (_) { return false; }
  }

  // Toast surfaced after Run-drilldown so the analyst knows the canonical
  // prompt is on their clipboard and just needs to be pasted into the new tab.
  function _toast(msg, kind) {
    var t = document.getElementById('dd-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'dd-toast';
      t.style.cssText = [
        'position:fixed', 'left:50%', 'bottom:32px', 'transform:translateX(-50%)',
        'z-index:9999', 'padding:10px 14px', 'border-radius:6px',
        'font-family:var(--font-sans, system-ui)', 'font-size:13px', 'font-weight:500',
        'box-shadow:0 8px 24px rgba(0,0,0,0.35)',
        'background:var(--bg-surface, #1a1d24)', 'color:var(--text-primary, #e8eaed)',
        'border:1px solid var(--border, #2a2f3a)',
        'opacity:0', 'transition:opacity 0.18s ease'
      ].join(';');
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.borderLeft = (kind === 'error') ? '3px solid var(--red, #ef4444)'
                       : (kind === 'ok')    ? '3px solid var(--green, #22c55e)'
                       :                       '3px solid var(--accent, #4f9eff)';
    requestAnimationFrame(function () { t.style.opacity = '1'; });
    clearTimeout(t._h);
    t._h = setTimeout(function () { t.style.opacity = '0'; }, 5000);
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
  // ─────────────────────────────────────────────────────────────────────
  // Macro Stance panel — renders between masthead and the run/versions/viewer.
  // Reads from window.MacroReconciliation[ticker]. Display-only.
  // ─────────────────────────────────────────────────────────────────────

  function _macroScoreColor(score) {
    var n = Number(score);
    if (isNaN(n))          return '#9ca3af';
    if (n >= 2)            return 'var(--teal, #14b8a6)';
    if (n >= 1)            return 'var(--green, #22c55e)';
    if (n <= -2)           return 'var(--red, #ef4444)';
    if (n <= -1)           return 'var(--yellow, #eab308)';
    return '#9ca3af';
  }

  function _macroStanceColor(stance) {
    var s = String(stance || '').trim();
    if (s === 'Build')                              return 'var(--teal, #14b8a6)';
    if (s === 'Trade')                              return 'var(--yellow, #eab308)';
    if (s === 'Reduce' || s === 'Avoid')            return 'var(--red, #ef4444)';
    return '#9ca3af'; // Hold / Monitor / unknown
  }

  function _macroAssessColor(assessment) {
    var a = String(assessment || '').toUpperCase();
    if (a === 'TAILWIND')  return 'var(--teal, #14b8a6)';
    if (a === 'HEADWIND')  return 'var(--red, #ef4444)';
    return '#9ca3af';
  }

  function _macroFmtScore(score) {
    var n = Number(score);
    if (isNaN(n)) return '—';
    return (n > 0 ? '+' : '') + n;
  }

  function _macroDriversList(drivers) {
    if (!drivers || !drivers.length) {
      return '<div class="dd-macro-drivers-empty">No drivers available.</div>';
    }
    return (
      '<ul class="dd-macro-drivers">' +
      drivers.map(function (d) {
        return '<li>' + _esc(d) + '</li>';
      }).join('') +
      '</ul>'
    );
  }

  function _renderMacroStancePanel(ticker) {
    if (!ticker) return '';

    // Loading skeleton while the fresh fetch is in flight.
    if (state.macroLoading) {
      return (
        '<div class="dd-macro-panel dd-macro-loading" data-dd-macro-skeleton>' +
          '<div class="dd-macro-header">' +
            '<div class="dd-macro-title">Macro stance</div>' +
            '<div class="dd-macro-skel-line" style="width:140px;"></div>' +
          '</div>' +
          '<div class="dd-macro-dual">' +
            '<div class="dd-macro-side"><div class="dd-macro-skel-badge"></div><div class="dd-macro-skel-line"></div><div class="dd-macro-skel-line"></div><div class="dd-macro-skel-line"></div></div>' +
            '<div class="dd-macro-side"><div class="dd-macro-skel-badge"></div><div class="dd-macro-skel-line"></div><div class="dd-macro-skel-line"></div><div class="dd-macro-skel-line"></div></div>' +
          '</div>' +
        '</div>'
      );
    }

    var rec = (global.MacroReconciliation && global.MacroReconciliation[ticker]) || null;

    if (!rec) {
      return (
        '<div class="dd-macro-panel dd-macro-empty">' +
          '<div class="dd-macro-empty-body">' +
            'No macro stance available for ' + _esc(ticker) + '. Run the reconciliation engine to generate.' +
          '</div>' +
        '</div>'
      );
    }

    // Pull tactical / strategic blocks. The Supabase row stores the score
    // and label as flat columns plus the full tactical / strategic objects
    // as jsonb under those keys. The local-fallback JSON keeps the original
    // nested shape from the reconciliation prompt. Be lenient.
    var tact = rec.tactical && typeof rec.tactical === 'object' ? rec.tactical : {};
    var strat = rec.strategic && typeof rec.strategic === 'object' ? rec.strategic : {};
    var tactScore  = (rec.tactical_score  != null) ? rec.tactical_score  : tact.score;
    var stratScore = (rec.strategic_score != null) ? rec.strategic_score : strat.score;
    var tactLabel  = rec.tactical_label  || tact.label  || '';
    var stratLabel = rec.strategic_label || strat.label || '';
    var watch      = tact.watch || '';
    var conviction = strat.conviction_question || '';

    // Footer (net stance + headline + explanation) tolerates both the flat
    // Supabase columns and the legacy `reconciled` nested object.
    var reconciled = rec.reconciled && typeof rec.reconciled === 'object' ? rec.reconciled : {};
    var stance      = rec.net_stance  || reconciled.net_stance  || '';
    var headline    = rec.headline    || reconciled.headline    || '';
    var explanation = rec.explanation || reconciled.explanation || '';
    var stanceColor = _macroStanceColor(stance);

    var regimePill = rec.regime_label
      ? '<span class="dd-macro-regime-pill">' + _esc(rec.regime_label) + '</span>'
      : '';
    var ts = rec.generated_at
      ? '<span class="dd-macro-ts" title="' + _esc(rec.generated_at) + '">' + _esc(_relTime(rec.generated_at)) + '</span>'
      : '';

    var watchHtml = watch
      ? '<div class="dd-macro-watch"><span class="dd-macro-watch-label">Watch:</span> <span class="dd-macro-watch-chip">' + _esc(watch) + '</span></div>'
      : '';
    var convictionHtml = conviction
      ? '<div class="dd-macro-conviction"><span class="dd-macro-conviction-label">Conviction Q:</span> <em>' + _esc(conviction) + '</em></div>'
      : '';

    var tacticalHtml =
      '<div class="dd-macro-side">' +
        '<div class="dd-macro-side-eyebrow">Tactical — 1\u20138 weeks</div>' +
        '<div class="dd-macro-side-head">' +
          '<span class="dd-macro-score-badge" style="background:' + _macroScoreColor(tactScore) + ';">' + _esc(_macroFmtScore(tactScore)) + '</span>' +
          '<span class="dd-macro-label">' + _esc(tactLabel || '—') + '</span>' +
        '</div>' +
        _macroDriversList(tact.key_drivers) +
        watchHtml +
      '</div>';

    var strategicHtml =
      '<div class="dd-macro-side">' +
        '<div class="dd-macro-side-eyebrow">Strategic — 6\u201324 months</div>' +
        '<div class="dd-macro-side-head">' +
          '<span class="dd-macro-score-badge" style="background:' + _macroScoreColor(stratScore) + ';">' + _esc(_macroFmtScore(stratScore)) + '</span>' +
          '<span class="dd-macro-label">' + _esc(stratLabel || '—') + '</span>' +
        '</div>' +
        _macroDriversList(strat.key_drivers) +
        convictionHtml +
      '</div>';

    var footerHtml = (stance || headline || explanation)
      ? (
          '<div class="dd-macro-footer">' +
            '<div class="dd-macro-footer-head">' +
              (stance ? '<span class="dd-macro-stance-pill" style="background:' + stanceColor + '14;color:' + stanceColor + ';border:1px solid ' + stanceColor + ';">' + _esc(stance) + '</span>' : '') +
              (headline ? '<span class="dd-macro-headline">' + _esc(headline) + '</span>' : '') +
            '</div>' +
            (explanation ? '<div class="dd-macro-explanation">' + _esc(explanation) + '</div>' : '') +
          '</div>'
        )
      : '';

    var exposureHtml = '';
    if (Array.isArray(rec.exposure_map) && rec.exposure_map.length) {
      var rows = rec.exposure_map.map(function (row) {
        if (!row || typeof row !== 'object') return '';
        var assess = String(row.assessment || '').toUpperCase();
        var color  = _macroAssessColor(assess);
        return (
          '<div class="dd-macro-exposure-row">' +
            '<div class="dd-macro-exp-dim">' + _esc(row.dimension || '—') + '</div>' +
            '<div class="dd-macro-exp-assess">' +
              '<span class="dd-macro-assess-badge" style="background:' + color + '14;color:' + color + ';border:1px solid ' + color + ';">' + _esc(assess || 'NEUTRAL') + '</span>' +
            '</div>' +
            '<div class="dd-macro-exp-tact"><span class="dd-macro-exp-label">Tactical:</span> ' + _esc(row.tactical || '—') + '</div>' +
            '<div class="dd-macro-exp-strat"><span class="dd-macro-exp-label">Strategic:</span> ' + _esc(row.strategic || '—') + '</div>' +
          '</div>'
        );
      }).join('');
      var openClass = state.macroExposureOpen ? ' is-open' : '';
      var chev = state.macroExposureOpen ? '▾' : '▸';
      exposureHtml =
        '<div class="dd-macro-exposure-wrap' + openClass + '">' +
          '<button type="button" class="dd-macro-exposure-toggle" data-dd-act="toggle-exposure">' +
            '<span class="dd-macro-exposure-chev">' + chev + '</span> Exposure map (' + rec.exposure_map.length + ' dimensions)' +
          '</button>' +
          '<div class="dd-macro-exposure">' +
            '<div class="dd-macro-exposure-head">' +
              '<div>Dimension</div><div>Assessment</div><div>Tactical commentary</div><div>Strategic commentary</div>' +
            '</div>' +
            rows +
          '</div>' +
        '</div>';
    }

    return (
      '<div class="dd-macro-panel">' +
        '<div class="dd-macro-header">' +
          '<div class="dd-macro-title">Macro stance</div>' +
          '<div class="dd-macro-meta">' + regimePill + ts + '</div>' +
        '</div>' +
        '<div class="dd-macro-dual">' +
          tacticalHtml +
          strategicHtml +
        '</div>' +
        footerHtml +
        exposureHtml +
      '</div>'
    );
  }

  function _renderMasthead(state) {
    var t = state.ticker || '';
    var rec = t ? _lib().getTicker(t) : null;
    var hasSaved = !!(rec && rec.versions && rec.versions.length);
    var live = t ? _currentTickerData(t) : null;

    var stats = '';
    if (live) {
      // Quality + Debate badges — last two KPI cards per spec
      var qBadge = '—', dBadge = '—';
      if (window.SignalScores) {
        var q = window.SignalScores.calculateQualityScore(live);
        var dd = window.SignalScores.calculateDebateIntensity(live);
        qBadge = window.SignalScores.buildBadgeHtml(q, 'quality', { compact: false });
        dBadge = window.SignalScores.buildBadgeHtml(dd, 'debate', { compact: false });
      }
      stats =
        '<div class="dd-mast-stats">' +
          '<div class="dd-mast-stat"><span>Price</span><strong>' + _esc(_fmtPrice(live.price)) + '</strong></div>' +
          '<div class="dd-mast-stat"><span>EV/Sales</span><strong>' + _esc(live.evSales != null ? Number(live.evSales).toFixed(1) + 'x' : '—') + '</strong></div>' +
          '<div class="dd-mast-stat"><span>Rev growth</span><strong>' + _esc(live.revenueGrowth != null ? (live.revenueGrowth >= 0 ? '+' : '') + Number(live.revenueGrowth).toFixed(1) + '%' : '—') + '</strong></div>' +
          '<div class="dd-mast-stat dd-mast-stat-score"><span>Quality</span>' + qBadge + '</div>' +
          '<div class="dd-mast-stat dd-mast-stat-score"><span>Debate</span>' + dBadge + '</div>' +
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
            'Drilldowns are two-stage notes pre-filled with live Supabase data (quote, ' +
            'estimates, comps). Part\u00a01 covers valuation, KPIs, industry, catalysts, and ' +
            'earnings setup. Part\u00a02 covers management, competitive landscape, risks, ' +
            'financials, and diligence questions. Both parts are saved with version history.' +
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
            '<div class="dd-step-title">Run the two-stage drilldown prompt</div>' +
            '<div class="dd-step-sub">Opens a fresh Perplexity thread for <strong>' + _esc(t) + '</strong> and copies the prompt with the <code>[SIGNAL_DATA_BLOCK]</code> pre-filled from live Supabase data. <em>Paste it as your first message in the new tab.</em> Run Part\u00a01 first (valuation, KPIs, industry, catalysts, earnings setup), then Part\u00a02 (management, competitive landscape, risks, financials, diligence).</div>' +
            '<div class="dd-run-step-actions">' +
              '<button type="button" class="btn-primary" data-dd-act="run">Run Part\u00a01 for ' + _esc(t) + '</button>' +
              '<button type="button" class="btn-primary" data-dd-act="run-p2">Run Part\u00a02 for ' + _esc(t) + '</button>' +
              '<button type="button" class="btn-sm" data-dd-act="copy-prompt" title="Copy the Part 1 prompt to your clipboard without opening a new tab">Copy Part\u00a01 prompt</button>' +
              '<button type="button" class="btn-sm" data-dd-act="copy-p2-prompt" title="Copy the Part 2 prompt to your clipboard without opening a new tab">Copy Part\u00a02 prompt</button>' +
            '</div>' +
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
    macroLoading: false,       // true while loadMacroReconciliation() is in flight
    macroExposureOpen: false,  // toggled by the Exposure map disclosure button
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
      html += _renderMacroStancePanel(state.ticker);
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
    if (act === 'toggle-exposure') {
      state.macroExposureOpen = !state.macroExposureOpen;
      render();
      return;
    }
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
      _runDrilldown(act === 'refresh' ? 'refresh' : 'manual', 'p1');
      return;
    }
    if (act === 'run-p2') {
      _runDrilldown('manual', 'p2');
      return;
    }
    if (act === 'copy-prompt') {
      if (!state.ticker) return;
      _loadPrompt().then(function (txt) {
        var fullPrompt = _buildFullPrompt(state.ticker, txt, 'p1');
        _copyToClipboard(fullPrompt).then(function (ok) {
          if (ok) _toast('Part 1 drilldown prompt copied to clipboard.', 'ok');
          else    _toast('Clipboard blocked — try the Run button instead.', 'error');
        });
      });
      return;
    }
    if (act === 'copy-p2-prompt') {
      if (!state.ticker) return;
      _loadPrompt().then(function (txt) {
        var fullPrompt = _buildPart2Prompt(state.ticker, txt);
        _copyToClipboard(fullPrompt).then(function (ok) {
          if (ok) _toast('Part 2 drilldown prompt copied to clipboard.', 'ok');
          else    _toast('Clipboard blocked — try the Run button instead.', 'error');
        });
      });
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

  function _runDrilldown(trigger, part) {
    if (!state.ticker) return;
    part = part || 'p1';
    state.showRunPanel = true;
    render();
    // Open the new tab IMMEDIATELY so the popup fires inside the click gesture
    // (Safari/Firefox block window.open from inside a Promise.then callback).
    var newWin = null;
    try { newWin = global.open('about:blank', '_blank', 'noopener'); } catch (_) {}

    _loadPrompt().then(function (txt) {
      var url = _buildPplxUrl(state.ticker);
      var fullPrompt = part === 'p2'
        ? _buildPart2Prompt(state.ticker, txt)
        : _buildFullPrompt(state.ticker, txt, 'p1');
      var label = part === 'p2' ? 'Part 2' : 'Part 1';
      // Navigate the pre-opened tab; if it was blocked, fall back to a same-
      // tab open (rare — the user gesture should always succeed here).
      if (newWin && !newWin.closed) {
        try { newWin.location.href = url; } catch (_) { try { global.open(url, '_blank', 'noopener'); } catch (_) {} }
      } else {
        try { global.open(url, '_blank', 'noopener'); } catch (_) {}
      }
      // Copy the full canonical prompt to the clipboard so the analyst can
      // paste it as their first message in the freshly-opened thread.
      _copyToClipboard(fullPrompt).then(function (ok) {
        if (ok) _toast(label + ' drilldown prompt copied — paste it into the new Perplexity tab.', 'ok');
        else    _toast('Open the new tab and paste the ' + label + ' prompt manually (clipboard blocked).', 'error');
      });
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
        // Trigger fresh macro reconciliation pull whenever a ticker is activated.
        // The loader (macro.js) populates window.MacroReconciliation[ticker]; we
        // re-render once the promise resolves so the panel swaps from skeleton
        // to populated state.
        if (t && typeof global.loadMacroReconciliation === 'function') {
          state.macroLoading = true;
          state.macroExposureOpen = false;
          render();
          try {
            var p = global.loadMacroReconciliation();
            if (p && typeof p.then === 'function') {
              p.then(function () {
                state.macroLoading = false;
                render();
              }).catch(function () {
                state.macroLoading = false;
                render();
              });
            } else {
              state.macroLoading = false;
            }
          } catch (_) {
            state.macroLoading = false;
          }
        }
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
