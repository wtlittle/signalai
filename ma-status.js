/* ===== MA-STATUS.JS — M&A status registry client =====
 *
 * Loads ma_status.json and exposes lookups for:
 *   - status pill HTML (ACQ / BID / RUMOR) on watchlist + earnings rows
 *   - full deal blurb HTML for the drilldown popup (replaces earnings note slot
 *     when a ticker has status='closed', 'pending', or 'announced')
 *
 * Data shape: see ma_status.json _meta block.
 *
 * Source-quality rule (enforced upstream during ingestion, but reaffirmed here):
 *   "If no credible deal is rumored then there should be no flag."
 * The client trusts the JSON — only Tier-1 verified entries (SEC filings,
 * issuer press releases) appear in ma_status.json. Rumors must clear the 4-gate
 * rumor logic before being added.
 *
 * Exposed API (window.MaStatus):
 *   load()                    → Promise<object>     Loads and caches the registry.
 *   get(ticker)               → deal | null         Lookup by ticker.
 *   getStatus(ticker)         → 'closed'|'pending'|... | null
 *   pillHtml(ticker, opts)    → string              ACQ/BID/RUMOR badge HTML (or '')
 *   dealBlurbHtml(ticker)     → string              Full deal blurb panel HTML
 *   hasBlurb(ticker)          → boolean             true if deal blurb should replace note
 *   onLoaded(fn)              → unsubscribe         Subscribe to load completion.
 */
(function (global) {
  'use strict';

  var _state = {
    loaded: false,
    loading: null,
    deals: {},
    meta: null
  };
  var _listeners = [];

  function _emit() {
    _listeners.forEach(function (fn) {
      try { fn(); } catch (e) { /* ignore */ }
    });
  }

  function _fmtUsd(n) {
    if (typeof n !== 'number' || !isFinite(n)) return null;
    if (n >= 1e9) return '$' + (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + 'B';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(0) + 'M';
    return '$' + n.toLocaleString();
  }

  function _fmtDate(s) {
    if (!s) return '';
    // s expected as YYYY-MM-DD
    try {
      var parts = s.split('-');
      if (parts.length !== 3) return s;
      var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      var mi = parseInt(parts[1], 10) - 1;
      if (mi < 0 || mi > 11) return s;
      return months[mi] + ' ' + parseInt(parts[2], 10) + ', ' + parts[0];
    } catch (e) { return s; }
  }

  function _statusToPillLabel(status) {
    switch (status) {
      case 'closed':
      case 'pending':
      case 'announced': return 'ACQ';
      case 'bid':       return 'BID';
      case 'rumor':     return 'RUMOR';
      default:          return null;
    }
  }

  function _statusToPillClass(status) {
    switch (status) {
      case 'closed':
      case 'pending':
      case 'announced': return 'status-acq';
      case 'bid':       return 'status-bid';
      case 'rumor':     return 'status-rumor';
      default:          return null;
    }
  }

  function _statusToPillTitle(deal) {
    if (!deal) return '';
    var parts = [];
    if (deal.buyer) parts.push(deal.buyer);
    if (deal.status === 'closed' && deal.closed_date) {
      parts.push('closed ' + _fmtDate(deal.closed_date));
    } else if (deal.status === 'pending' && deal.expected_close) {
      parts.push('pending — exp. close ' + _fmtDate(deal.expected_close));
    } else if (deal.status === 'announced' && deal.announced_date) {
      parts.push('announced ' + _fmtDate(deal.announced_date));
    } else if (deal.status === 'bid') {
      parts.push('unsolicited bid');
    } else if (deal.status === 'rumor') {
      parts.push('credible rumor');
    }
    return parts.join(' · ');
  }

  async function load() {
    if (_state.loaded) return _state.deals;
    if (_state.loading) return _state.loading;
    _state.loading = (async function () {
      try {
        // ma_status.json is bundled in the repo only (not migrated to R2 yet).
        var resp;
        if (global.SignalSnapshot && typeof global.SignalSnapshot.fetchWithFallback === 'function' &&
            (global.SignalSnapshot.MIGRATED_FILES || []).indexOf('ma_status.json') !== -1) {
          resp = await global.SignalSnapshot.fetchWithFallback('ma_status.json', { cacheBust: true });
        } else {
          resp = await fetch('ma_status.json?v=' + Date.now());
        }
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var data = await resp.json();
        _state.deals = (data && data.deals) || {};
        _state.meta = (data && data._meta) || null;
        _state.loaded = true;
      } catch (e) {
        // Soft fail — no pills, no blurbs. Don't blank the UI.
        console.warn('[MaStatus] load failed:', e && e.message);
        _state.deals = {};
        _state.loaded = true;
      } finally {
        _state.loading = null;
        _emit();
      }
      return _state.deals;
    })();
    return _state.loading;
  }

  function get(ticker) {
    if (!ticker) return null;
    return _state.deals[ticker] || null;
  }

  function getStatus(ticker) {
    var d = get(ticker);
    return d ? d.status : null;
  }

  function pillHtml(ticker, opts) {
    opts = opts || {};
    var deal = get(ticker);
    if (!deal || !deal.status) return '';
    var label = _statusToPillLabel(deal.status);
    var cls   = _statusToPillClass(deal.status);
    if (!label || !cls) return '';
    var title = _statusToPillTitle(deal).replace(/"/g, '&quot;');
    var extra = opts.compact ? ' ma-status-pill--compact' : '';
    return '<span class="private-status-badge ' + cls + ' ma-status-pill' + extra +
           '" title="' + title + '" data-ma-ticker="' + ticker + '">' + label + '</span>';
  }

  function hasBlurb(ticker) {
    var d = get(ticker);
    if (!d) return false;
    return d.status === 'closed' || d.status === 'pending' || d.status === 'announced';
  }

  function _renderPriceLine(deal) {
    var parts = [];
    if (typeof deal.price_per_share_usd === 'number') {
      parts.push('<strong>$' + deal.price_per_share_usd.toFixed(2) + '/share</strong>');
    }
    if (deal.structure) parts.push(deal.structure);
    if (typeof deal.premium_pct === 'number') parts.push(deal.premium_pct.toFixed(0) + '% premium');
    return parts.join(' · ');
  }

  function _renderValueLine(deal) {
    var parts = [];
    var ev  = _fmtUsd(deal.enterprise_value_usd);
    var eq  = _fmtUsd(deal.equity_value_usd);
    if (ev) parts.push(ev + ' EV');
    else if (eq) parts.push(eq + ' equity value');
    return parts.join(' · ');
  }

  function _renderTimelineLine(deal) {
    var parts = [];
    if (deal.announced_date) parts.push('Announced ' + _fmtDate(deal.announced_date));
    if (deal.status === 'closed' && deal.closed_date) parts.push('Closed ' + _fmtDate(deal.closed_date));
    else if (deal.status === 'pending' && deal.expected_close) parts.push('Expected close ' + _fmtDate(deal.expected_close));
    return parts.join(' · ');
  }

  function _escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function dealBlurbHtml(ticker) {
    var deal = get(ticker);
    if (!deal) return '';

    var statusLabel = (deal.status || '').toUpperCase();
    var buyerLine = deal.buyer ? ('Buyer: <strong>' + _escapeHtml(deal.buyer) + '</strong>' +
      (deal.buyer_ticker ? ' (' + _escapeHtml(deal.buyer_ticker) + ')' : '') +
      (deal.deal_type ? ' · ' + _escapeHtml(deal.deal_type) : '')) : '';

    var priceLine    = _renderPriceLine(deal);
    var valueLine    = _renderValueLine(deal);
    var timelineLine = _renderTimelineLine(deal);

    var prosHtml = (deal.pros && deal.pros.length)
      ? '<ul class="ma-blurb-list ma-blurb-pros">' + deal.pros.map(function (p) { return '<li>' + _escapeHtml(p) + '</li>'; }).join('') + '</ul>'
      : '';
    var consHtml = (deal.cons && deal.cons.length)
      ? '<ul class="ma-blurb-list ma-blurb-cons">' + deal.cons.map(function (c) { return '<li>' + _escapeHtml(c) + '</li>'; }).join('') + '</ul>'
      : '';

    var compHtml = '';
    if (deal.competitor_impact && deal.competitor_impact.length) {
      compHtml = '<table class="ma-blurb-comp-table">' +
        deal.competitor_impact.map(function (ci) {
          return '<tr><td class="ma-blurb-comp-ticker">' + _escapeHtml(ci.ticker || '') + '</td>' +
                 '<td>' + _escapeHtml(ci.note || '') + '</td></tr>';
        }).join('') +
      '</table>';
    }

    var sourcesHtml = '';
    if (deal.sources && deal.sources.length) {
      sourcesHtml = '<ul class="ma-blurb-sources">' +
        deal.sources.map(function (s) {
          var tier = (typeof s.tier === 'number') ? (' <span class="ma-blurb-tier">Tier ' + s.tier + '</span>') : '';
          if (s.url) {
            return '<li><a href="' + _escapeHtml(s.url) + '" target="_blank" rel="noopener">' +
                   _escapeHtml(s.label || s.url) + '</a>' + tier + '</li>';
          }
          return '<li>' + _escapeHtml(s.label || '') + tier + '</li>';
        }).join('') +
      '</ul>';
    }

    return (
      '<div class="ma-deal-blurb" data-ma-ticker="' + _escapeHtml(deal.ticker) + '">' +
        '<div class="ma-blurb-header">' +
          '<span class="ma-blurb-status-badge ma-blurb-status-' + _escapeHtml(deal.status) + '">' + _escapeHtml(statusLabel) + '</span>' +
          '<span class="ma-blurb-headline">' + _escapeHtml(deal.company || deal.ticker) + ' — Deal Status</span>' +
        '</div>' +
        (buyerLine    ? '<div class="ma-blurb-line">' + buyerLine + '</div>' : '') +
        (priceLine    ? '<div class="ma-blurb-line">' + priceLine + '</div>' : '') +
        (valueLine    ? '<div class="ma-blurb-line">' + valueLine + '</div>' : '') +
        (timelineLine ? '<div class="ma-blurb-line ma-blurb-timeline">' + timelineLine + '</div>' : '') +
        (deal.strategic_rationale
          ? '<div class="ma-blurb-section"><h4>Strategic rationale</h4><p>' + _escapeHtml(deal.strategic_rationale) + '</p></div>'
          : '') +
        (prosHtml ? '<div class="ma-blurb-section"><h4>Pros</h4>' + prosHtml + '</div>' : '') +
        (consHtml ? '<div class="ma-blurb-section"><h4>Cons</h4>' + consHtml + '</div>' : '') +
        (compHtml ? '<div class="ma-blurb-section"><h4>Competitor impact</h4>' + compHtml + '</div>' : '') +
        (sourcesHtml ? '<div class="ma-blurb-section ma-blurb-sources-section"><h4>Sources</h4>' + sourcesHtml + '</div>' : '') +
        (deal.delisted
          ? '<div class="ma-blurb-footer ma-blurb-delisted">This security has been delisted. Earnings notes are no longer generated.</div>'
          : '') +
      '</div>'
    );
  }

  function onLoaded(fn) {
    _listeners.push(fn);
    if (_state.loaded) {
      try { fn(); } catch (e) { /* ignore */ }
    }
    return function () { _listeners = _listeners.filter(function (x) { return x !== fn; }); };
  }

  // Eager load — most callers depend on this being ready before first render.
  // Re-render hooks subscribe via onLoaded() to repaint pills once loaded.
  if (global.document && global.document.readyState !== 'loading') {
    load();
  } else if (global.document) {
    global.document.addEventListener('DOMContentLoaded', function () { load(); });
  }

  global.MaStatus = {
    load: load,
    get: get,
    getStatus: getStatus,
    pillHtml: pillHtml,
    hasBlurb: hasBlurb,
    dealBlurbHtml: dealBlurbHtml,
    onLoaded: onLoaded
  };
})(typeof window !== 'undefined' ? window : this);
