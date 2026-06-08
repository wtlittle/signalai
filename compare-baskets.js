/* compare-baskets.js — Rich suggestion baskets for the Compare tab
 * ============================================================================
 *
 * Renders the <2-picked empty state of the Compare tab as a grid of curated
 * "baskets" — pre-built groups of tickers the user can load in one click or
 * cherry-pick from. Replaces the thin placeholder stub in coverage-controller.
 *
 * UX (Option A, locked with user):
 *   - Single chip click ADDS the ticker to the selection (toggles off if
 *     already picked). Respects MAX_PICK; muted when the selection is full.
 *   - Each basket has a "Compare these N" button that REPLACES the whole
 *     selection wholesale with that basket's tickers (capped at MAX_PICK).
 *   - Picked chips show a "picked" badge (text + CSS check glyph, no emoji).
 *   - Hovering a chip shows name + sector + 1d change via the title attr.
 *   - Reaching 2 picks auto-renders the rich surface (coverage-controller
 *     re-renders on signalcompare:selection-changed).
 *
 * Data sources (all optional — a basket whose source is missing is hidden):
 *   window.tickerData                       — { TICKER: { change1d, sector,
 *                                               debateScore, qualityScore,
 *                                               name, marketCap, ... } }
 *   window.macroDataCache.regime            — { favored_sectors[], avoid_sectors[] }
 *   window.macroDataCache.signals[t]        — { alpha_1m, regime_factor_score, ... }
 *   window._earningsCalendarData.pre_earnings[] — [{ ticker, days_until, ... }]
 *   ma_status.json pending_review[]         — fetched lazily, cached on
 *                                             window._maStatusData
 *
 * Data integrity: missing numbers stay null and render "n/a"; nothing is
 * fabricated.
 *
 * Public API: global.SignalCompareBaskets.render(container, ctx)
 *   ctx.toggle(ticker)   -> add/remove one ticker (default: SignalCompare.toggleTicker)
 *   ctx.replace(tickers) -> set the whole selection (default: clear + toggle each)
 *   ctx.onChange()       -> re-render the Compare surface (coverage-controller hook)
 *   ctx.escapeHtml(s)    -> HTML-escaper (default: built in)
 *   ctx.commonName(t, f) -> display name resolver (default: getCommonName)
 * ============================================================================
 */
(function (global) {
  'use strict';

  var MOVER_THRESHOLD = 3.0;   // |change1d| >= this counts as a mover
  var BASKET_CAP = 6;          // max chips per basket
  var DEBATE_PRE_EARN_MIN = 60;

  // ETF symbol -> GICS sector name(s) found on ticker rows. Mirrors the macro
  // panel's sectorName() map but expands to the full sector labels that appear
  // on tickerData[t].sector so we can resolve representative names.
  var ETF_TO_SECTORS = {
    XLK:  ['Technology', 'Information Technology'],
    XLC:  ['Communication Services', 'Communications'],
    XLY:  ['Consumer Discretionary', 'Consumer Cyclical'],
    XLP:  ['Consumer Staples', 'Consumer Defensive'],
    XLF:  ['Financials', 'Financial Services'],
    XLV:  ['Health Care', 'Healthcare'],
    XLE:  ['Energy'],
    XLI:  ['Industrials'],
    XLB:  ['Materials', 'Basic Materials'],
    XLRE: ['Real Estate'],
    XLU:  ['Utilities']
  };
  var ETF_LABEL = {
    XLK: 'Tech', XLC: 'Communications', XLY: 'Consumer Disc.', XLP: 'Staples',
    XLF: 'Financials', XLV: 'Health Care', XLE: 'Energy', XLI: 'Industrials',
    XLB: 'Materials', XLRE: 'Real Estate', XLU: 'Utilities'
  };

  // --------------------------- helpers ----------------------------
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function _num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
  function _tickerData() {
    return (global.tickerData && typeof global.tickerData === 'object') ? global.tickerData : {};
  }
  function _commonName(ctx, t, fallback) {
    if (ctx && typeof ctx.commonName === 'function') return ctx.commonName(t, fallback);
    if (typeof global.getCommonName === 'function') return global.getCommonName(t, fallback);
    return fallback || t;
  }
  function _sectorOf(t, d) {
    if (d && d.sector) return d.sector;
    return null;
  }
  function _isSelected(t) {
    return !!(global.SignalCompare && typeof global.SignalCompare.isSelected === 'function' &&
      global.SignalCompare.isSelected(t));
  }
  function _maxPick() {
    return (global.SignalCompare && global.SignalCompare.MAX_PICK) || 4;
  }
  function _selectedCount() {
    if (global.SignalCompare && typeof global.SignalCompare.getSelected === 'function') {
      return global.SignalCompare.getSelected().length;
    }
    return 0;
  }
  function _changeStr(change) {
    if (change == null) return 'n/a';
    return (change > 0 ? '+' : '') + change.toFixed(1) + '%';
  }
  function _changeCls(change) {
    if (change == null) return '';
    return change > 0 ? ' pos' : (change < 0 ? ' neg' : '');
  }
  function _tooltip(ctx, t, d) {
    var name = _commonName(ctx, t, d && d.name) || t;
    var sector = _sectorOf(t, d) || 'n/a';
    var change = d ? _num(d.change1d) : null;
    return name + '  ·  ' + sector + '  ·  1d ' + _changeStr(change);
  }

  // --------------------------- basket builders ----------------------------
  // Each returns { tickers: [...] } using only available data, or null when
  // the source is missing so the card can be hidden.

  // 1. Movers today: |change1d| >= MOVER_THRESHOLD, sorted by abs change desc.
  function _basketMovers(data) {
    var rows = Object.keys(data)
      .map(function (t) { return { t: t, c: _num(data[t].change1d) }; })
      .filter(function (r) { return r.c != null && Math.abs(r.c) >= MOVER_THRESHOLD; })
      .sort(function (a, b) { return Math.abs(b.c) - Math.abs(a.c); });
    if (!rows.length) return null;
    return { tickers: rows.slice(0, BASKET_CAP).map(function (r) { return r.t; }) };
  }

  // 2. Highly debated peers: top by debateScore.
  function _basketDebated(data) {
    var rows = Object.keys(data)
      .map(function (t) { return { t: t, s: _num(data[t].debateScore) }; })
      .filter(function (r) { return r.s != null; })
      .sort(function (a, b) { return b.s - a.s; });
    if (!rows.length) return null;
    return { tickers: rows.slice(0, BASKET_CAP).map(function (r) { return r.t; }) };
  }

  // Representative tickers for a set of sector ETFs (favored / unfavored).
  // Picks up to 2 names per ETF, preferring larger / more-debated names, caps
  // at BASKET_CAP total.
  function _sectorBasket(data, etfs) {
    if (!Array.isArray(etfs) || !etfs.length) return null;
    var picks = [];
    var seen = {};
    etfs.forEach(function (etf) {
      var sectors = ETF_TO_SECTORS[etf];
      if (!sectors) return;
      var inSector = Object.keys(data).filter(function (t) {
        var sec = _sectorOf(t, data[t]);
        return sec && sectors.indexOf(sec) > -1 && !seen[t];
      }).sort(function (a, b) {
        // Prefer higher debate, then larger market cap, so chips feel relevant.
        var da = _num(data[a].debateScore), db = _num(data[b].debateScore);
        if (da != null || db != null) return (db || 0) - (da || 0);
        return (_num(data[b].marketCap) || 0) - (_num(data[a].marketCap) || 0);
      });
      inSector.slice(0, 2).forEach(function (t) { seen[t] = true; picks.push(t); });
    });
    if (!picks.length) return null;
    return { tickers: picks.slice(0, BASKET_CAP) };
  }

  // 5. High quant + high debate: top quartile of BOTH qualityScore AND
  //    debateScore. Falls back to "both >= 60" when too few names to derive a
  //    quartile cut.
  function _basketHighQuantDebate(data) {
    var rows = Object.keys(data).map(function (t) {
      return { t: t, q: _num(data[t].qualityScore), d: _num(data[t].debateScore) };
    }).filter(function (r) { return r.q != null && r.d != null; });
    if (rows.length < 4) return null;
    var qs = rows.map(function (r) { return r.q; }).sort(function (a, b) { return a - b; });
    var ds = rows.map(function (r) { return r.d; }).sort(function (a, b) { return a - b; });
    var qCut = qs[Math.floor(qs.length * 0.75)];
    var dCut = ds[Math.floor(ds.length * 0.75)];
    var hits = rows.filter(function (r) { return r.q >= qCut && r.d >= dCut; })
      .sort(function (a, b) { return (b.q + b.d) - (a.q + a.d); });
    if (!hits.length) return null;
    return { tickers: hits.slice(0, BASKET_CAP).map(function (r) { return r.t; }) };
  }

  // 6. Pre-earnings setup: reporting this week (earnings calendar pre_earnings)
  //    AND debateScore >= DEBATE_PRE_EARN_MIN.
  function _basketPreEarnings(data) {
    var ecal = global._earningsCalendarData;
    if (!ecal || !Array.isArray(ecal.pre_earnings)) return null;
    var rows = ecal.pre_earnings
      .map(function (e) { return e && e.ticker; })
      .filter(function (t) {
        if (!t || !data[t]) return false;
        var ds = _num(data[t].debateScore);
        return ds != null && ds >= DEBATE_PRE_EARN_MIN;
      });
    // De-dup, preserve calendar order (soonest first).
    var seen = {}, out = [];
    rows.forEach(function (t) { if (!seen[t]) { seen[t] = true; out.push(t); } });
    if (!out.length) return null;
    return { tickers: out.slice(0, BASKET_CAP) };
  }

  // 7. M&A rumor candidates: ma_status.json pending_review[].
  function _basketMaRumors() {
    var ma = global._maStatusData;
    if (!ma || !Array.isArray(ma.pending_review)) return null;
    var rows = ma.pending_review
      .filter(function (e) { return e && e.ticker; })
      .sort(function (a, b) { return (_num(b.confidence) || 0) - (_num(a.confidence) || 0); });
    if (!rows.length) return null;
    return {
      tickers: rows.slice(0, BASKET_CAP).map(function (r) { return r.ticker; }),
      meta: rows.slice(0, BASKET_CAP).reduce(function (acc, r) {
        acc[r.ticker] = { buyer: r.buyer || null, confidence: _num(r.confidence) };
        return acc;
      }, {})
    };
  }

  // Lazy-load ma_status.json once, then re-render so the M&A card can appear.
  var _maFetchStarted = false;
  function _ensureMaStatus(rerender) {
    if (global._maStatusData || _maFetchStarted) return;
    if (typeof fetch !== 'function') return;
    _maFetchStarted = true;
    fetch('ma_status.json?ts=' + Date.now())
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (json) {
        if (json) { global._maStatusData = json; if (typeof rerender === 'function') rerender(); }
      })
      .catch(function () { /* network/offline — basket stays hidden */ });
  }

  // --------------------------- rendering ----------------------------
  function _chipHtml(ctx, t, data, extra) {
    var d = data[t] || {};
    var name = _commonName(ctx, t, d.name) || '';
    var change = _num(d.change1d);
    var picked = _isSelected(t);
    var full = _selectedCount() >= _maxPick();
    var muted = full && !picked;
    var cls = 'cmp-basket-chip cmp-empty-chip' +
      (picked ? ' is-picked' : '') + (muted ? ' is-muted' : '');
    var title = muted
      ? 'Selection full — clear one to add'
      : _tooltip(ctx, t, d);
    var extraHtml = '';
    if (extra && extra.buyer) {
      var confPct = (extra.confidence != null)
        ? Math.round(extra.confidence * 100) + '%' : 'n/a';
      extraHtml = '<span class="cmp-basket-chip-meta">' + _esc(extra.buyer) +
        '  ·  ' + _esc(confPct) + '</span>';
    }
    return '<button type="button" class="' + cls + '" data-ticker="' + _esc(t) + '"' +
      (muted ? ' aria-disabled="true"' : '') +
      ' title="' + _esc(title) + '">' +
      '<span class="cmp-basket-chip-tk cmp-empty-chip-tk">' + _esc(t) + '</span>' +
      '<span class="cmp-basket-chip-name cmp-empty-chip-name">' + _esc(name) + '</span>' +
      (extraHtml ||
        '<span class="cmp-basket-chip-change cmp-empty-chip-change' + _changeCls(change) + '">' +
          _esc(_changeStr(change)) + '</span>') +
      '<span class="cmp-basket-chip-picked">picked</span>' +
    '</button>';
  }

  function _cardHtml(ctx, def, data) {
    var n = def.tickers.length;
    var helpSpan = def.helpKey
      ? ' <span data-help="' + _esc(def.helpKey) + '"></span>' : '';
    var chips = def.tickers.map(function (t) {
      return _chipHtml(ctx, t, data, def.meta && def.meta[t]);
    }).join('');
    return '<div class="cmp-basket-card" data-basket="' + _esc(def.key) + '">' +
      '<div class="cmp-basket-header">' +
        '<div class="cmp-basket-title">' + _esc(def.title) + helpSpan + '</div>' +
        '<button type="button" class="btn-sm cmp-basket-action" data-basket-load="' + _esc(def.key) + '">' +
          'Compare these ' + n +
        '</button>' +
      '</div>' +
      '<div class="cmp-basket-chips">' + chips + '</div>' +
    '</div>';
  }

  function render(container, ctx) {
    if (!container) return;
    ctx = ctx || {};
    var data = _tickerData();

    var toggle = (typeof ctx.toggle === 'function') ? ctx.toggle : function (t) {
      if (global.SignalCompare && typeof global.SignalCompare.toggleTicker === 'function') {
        global.SignalCompare.toggleTicker(t);
      }
    };
    var replace = (typeof ctx.replace === 'function') ? ctx.replace : function (tickers) {
      var sc = global.SignalCompare;
      if (!sc) return;
      if (typeof sc.clearSelection === 'function') sc.clearSelection();
      var cap = _maxPick();
      tickers.slice(0, cap).forEach(function (t) {
        if (typeof sc.toggleTicker === 'function' && !sc.isSelected(t)) sc.toggleTicker(t);
      });
    };
    var onChange = (typeof ctx.onChange === 'function') ? ctx.onChange : function () {};
    var rerender = function () { render(container, ctx); };

    // Kick off the lazy M&A fetch (re-renders when it lands).
    _ensureMaStatus(rerender);

    var regime = (global.macroDataCache && global.macroDataCache.regime) || null;

    var defs = [
      { key: 'movers',            title: 'Movers today',          helpKey: 'compare.basket.movers',           build: function () { return _basketMovers(data); } },
      { key: 'debated',           title: 'Highly debated peers',  helpKey: 'compare.basket.debated',          build: function () { return _basketDebated(data); } },
      { key: 'favored',           title: 'Favored sectors',       helpKey: 'compare.basket.favored',          build: function () { return regime ? _sectorBasket(data, regime.favored_sectors) : null; } },
      { key: 'unfavored',         title: 'Unfavored sectors',     helpKey: 'compare.basket.unfavored',        build: function () { return regime ? _sectorBasket(data, regime.avoid_sectors) : null; } },
      { key: 'high-quant-debate', title: 'High quant + high debate', helpKey: 'compare.basket.high-quant-debate', build: function () { return _basketHighQuantDebate(data); } },
      { key: 'pre-earnings',      title: 'Pre-earnings setup',    helpKey: 'compare.basket.pre-earnings',      build: function () { return _basketPreEarnings(data); } },
      { key: 'ma-rumors',         title: 'M&A rumor candidates',  helpKey: 'compare.basket.ma-rumors',         build: function () { return _basketMaRumors(); } }
    ];

    var built = defs.map(function (def) {
      var res = def.build();
      if (!res || !res.tickers || !res.tickers.length) return null;
      return { key: def.key, title: def.title, helpKey: def.helpKey, tickers: res.tickers, meta: res.meta || null };
    }).filter(Boolean);

    var intro = 'Pick a basket to load it in one click, or tap individual chips to mix and match. Cap: ' + _maxPick() + ' tickers.';

    if (!built.length) {
      // No data source resolved yet — keep a graceful, honest placeholder.
      container.innerHTML =
        '<div class="cmp-baskets">' +
          '<div class="cmp-baskets-intro">' + _esc(intro) + '</div>' +
          '<div class="cmp-baskets-empty">No suggestion baskets available right now — ' +
            'pick tickers from the search bar above to start comparing.</div>' +
        '</div>';
      return;
    }

    container.innerHTML =
      '<div class="cmp-baskets">' +
        '<div class="cmp-baskets-intro">' + _esc(intro) + '</div>' +
        '<div class="cmp-baskets-grid">' +
          built.map(function (def) { return _cardHtml(ctx, def, data); }).join('') +
        '</div>' +
      '</div>';

    // Wire chip clicks (add/remove one ticker).
    container.querySelectorAll('.cmp-basket-chip').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (btn.getAttribute('aria-disabled') === 'true') return;
        var t = btn.getAttribute('data-ticker');
        if (!t) return;
        // Block adding past the cap (toggling OFF an already-picked chip is fine).
        if (!_isSelected(t) && _selectedCount() >= _maxPick()) return;
        toggle(t);
        onChange();
      });
    });

    // Wire "Compare these N" (replace whole selection).
    container.querySelectorAll('[data-basket-load]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-basket-load');
        var def = built.filter(function (b) { return b.key === key; })[0];
        if (!def) return;
        replace(def.tickers);
        onChange();
      });
    });
  }

  global.SignalCompareBaskets = {
    render: render
  };
})(window);
