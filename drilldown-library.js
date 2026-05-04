/* ===== DRILLDOWN-LIBRARY.JS =====
 * Client-side versioned storage for institutional drilldown notes.
 *
 * Schema (localStorage key 'ss_drilldown_library'):
 *   {
 *     tickers: {
 *       "ZS": {
 *         ticker: "ZS",
 *         company_name: "Zscaler",
 *         latest_version: 3,
 *         versions: [
 *           {
 *             version: 1,
 *             generated_at: "2026-05-01T12:34:56Z",
 *             trigger: "manual" | "refresh" | "earnings_alert",
 *             price_at_generation: 180.45,
 *             consensus_target_at_generation: 220,
 *             html: "<!DOCTYPE html>...",      // full note HTML
 *             summary: "first 240 chars..."      // extracted body preview
 *           }
 *         ]
 *       }
 *     }
 *   }
 *
 * Public API (window.SignalDrilldownLibrary):
 *   .all()                        -> returns whole library
 *   .getTicker(ticker)            -> { ticker, company_name, latest_version, versions[] } | null
 *   .getVersion(ticker, version)  -> version object | null
 *   .save(ticker, { html, trigger, company_name, price, target })
 *                                 -> saved version object
 *   .remove(ticker)               -> boolean
 *   .removeVersion(ticker, v)     -> boolean
 *   .export()                     -> JSON blob string
 *   .import(jsonString)           -> boolean
 *   .onChange(handler)            -> unsubscribe function
 *
 * All mutations broadcast 'signalstack:drilldown-library-changed'.
 */
(function (global) {
  'use strict';

  var LS_KEY = 'ss_drilldown_library';
  var listeners = [];

  function _read() {
    try {
      var raw = localStorage.getItem(LS_KEY);
      if (!raw) return { tickers: {} };
      var obj = JSON.parse(raw);
      if (!obj || typeof obj !== 'object' || !obj.tickers) return { tickers: {} };
      return obj;
    } catch (e) {
      console.warn('[drilldown-library] read failed', e);
      return { tickers: {} };
    }
  }

  function _write(lib) {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(lib));
    } catch (e) {
      // Quota exceeded is the likely cause when many HTML notes are stored.
      console.error('[drilldown-library] write failed — likely storage quota:', e);
      try {
        var msg = 'Drilldown Library is full. Remove older versions from the Library panel to save new notes.';
        if (typeof global.alert === 'function') global.alert(msg);
      } catch (_) {}
      throw e;
    }
  }

  function _normTicker(t) {
    return String(t || '').trim().toUpperCase();
  }

  function _notify(eventName, detail) {
    try {
      document.dispatchEvent(new CustomEvent('signalstack:drilldown-library-changed', {
        detail: Object.assign({ event: eventName }, detail || {})
      }));
    } catch (_) {}
    listeners.slice().forEach(function (fn) {
      try { fn({ event: eventName, detail: detail }); } catch (e) { console.error(e); }
    });
  }

  function _extractSummary(html) {
    if (!html || typeof html !== 'string') return '';
    // Strip tags and collapse whitespace — cheap preview.
    var txt = html
      .replace(/<script[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    return txt.slice(0, 240);
  }

  function _tryExtractCompanyName(html) {
    if (!html) return null;
    // Look for common header patterns emitted by the drilldown engine.
    var title = html.match(/<title[^>]*>\s*([^<]+?)\s*<\/title>/i);
    if (title && title[1] && title[1].length < 120) {
      var clean = title[1].replace(/\s*[\|\-–—]\s*(Signal\s*Stack|Drilldown|Institutional).*$/i, '').trim();
      if (clean.length >= 2) return clean;
    }
    var h1 = html.match(/<h1[^>]*>\s*([^<]+?)\s*<\/h1>/i);
    if (h1 && h1[1] && h1[1].length < 120) return h1[1].trim();
    return null;
  }

  // ----- Public API -----------------------------------------------------

  function all() {
    return _read();
  }

  function getTicker(ticker) {
    var t = _normTicker(ticker);
    if (!t) return null;
    var lib = _read();
    return lib.tickers[t] || null;
  }

  function getVersion(ticker, version) {
    var rec = getTicker(ticker);
    if (!rec) return null;
    return rec.versions.find(function (v) { return v.version === version; }) || null;
  }

  function list() {
    var lib = _read();
    return Object.keys(lib.tickers).map(function (t) {
      var r = lib.tickers[t];
      return {
        ticker: r.ticker,
        company_name: r.company_name,
        latest_version: r.latest_version,
        version_count: r.versions.length,
        latest_generated_at: r.versions.length ? r.versions[r.versions.length - 1].generated_at : null,
        latest_price: r.versions.length ? r.versions[r.versions.length - 1].price_at_generation : null,
        latest_target: r.versions.length ? r.versions[r.versions.length - 1].consensus_target_at_generation : null,
      };
    }).sort(function (a, b) {
      return (b.latest_generated_at || '').localeCompare(a.latest_generated_at || '');
    });
  }

  function save(ticker, opts) {
    opts = opts || {};
    var t = _normTicker(ticker);
    if (!t) throw new Error('save: ticker is required');
    if (!opts.html || typeof opts.html !== 'string') {
      throw new Error('save: html is required');
    }
    var lib = _read();
    var rec = lib.tickers[t] || {
      ticker: t,
      company_name: null,
      latest_version: 0,
      versions: [],
    };
    var nextVer = (rec.latest_version || 0) + 1;
    var companyName = opts.company_name || rec.company_name || _tryExtractCompanyName(opts.html) || t;
    var entry = {
      version: nextVer,
      generated_at: opts.generated_at || new Date().toISOString(),
      trigger: opts.trigger || 'manual',
      price_at_generation: (opts.price != null ? Number(opts.price) : null),
      consensus_target_at_generation: (opts.target != null ? Number(opts.target) : null),
      html: opts.html,
      summary: _extractSummary(opts.html),
    };
    rec.ticker = t;
    rec.company_name = companyName;
    rec.latest_version = nextVer;
    rec.versions.push(entry);
    lib.tickers[t] = rec;
    _write(lib);
    _notify('save', { ticker: t, version: nextVer });
    return entry;
  }

  function remove(ticker) {
    var t = _normTicker(ticker);
    var lib = _read();
    if (!lib.tickers[t]) return false;
    delete lib.tickers[t];
    _write(lib);
    _notify('remove', { ticker: t });
    return true;
  }

  function removeVersion(ticker, version) {
    var t = _normTicker(ticker);
    var lib = _read();
    var rec = lib.tickers[t];
    if (!rec) return false;
    var before = rec.versions.length;
    rec.versions = rec.versions.filter(function (v) { return v.version !== version; });
    if (rec.versions.length === before) return false;
    if (!rec.versions.length) {
      delete lib.tickers[t];
    } else {
      rec.latest_version = Math.max.apply(null, rec.versions.map(function (v) { return v.version; }));
    }
    _write(lib);
    _notify('remove-version', { ticker: t, version: version });
    return true;
  }

  function exportJson() {
    return JSON.stringify(_read(), null, 2);
  }

  function importJson(jsonString) {
    try {
      var parsed = JSON.parse(jsonString);
      if (!parsed || typeof parsed !== 'object' || !parsed.tickers) return false;
      _write(parsed);
      _notify('import', {});
      return true;
    } catch (e) {
      console.error('[drilldown-library] import failed', e);
      return false;
    }
  }

  function onChange(fn) {
    if (typeof fn !== 'function') return function () {};
    listeners.push(fn);
    return function () {
      var i = listeners.indexOf(fn);
      if (i !== -1) listeners.splice(i, 1);
    };
  }

  // Rough storage-usage estimate for UI.
  function storageUsage() {
    try {
      var raw = localStorage.getItem(LS_KEY) || '';
      return { bytes: raw.length, kb: Math.round(raw.length / 1024) };
    } catch (_) { return { bytes: 0, kb: 0 }; }
  }

  global.SignalDrilldownLibrary = {
    all: all,
    list: list,
    getTicker: getTicker,
    getVersion: getVersion,
    save: save,
    remove: remove,
    removeVersion: removeVersion,
    export: exportJson,
    import: importJson,
    onChange: onChange,
    storageUsage: storageUsage,
  };
})(typeof window !== 'undefined' ? window : this);
