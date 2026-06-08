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
      part: opts.part || null,  // 'p1' | 'p2' | 'merged' | null (legacy)
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

  // ----- Manifest hydration (cross-device seeding) ----------------------
  // The dashboard reads from localStorage, which is per-device. Committed
  // notes live in notes/drilldown/index.json + per-note markdown files. On
  // load we seed localStorage from that manifest so committed drilldowns
  // appear on every device. Idempotent: each manifest id is recorded in
  // SEEDED_KEY so re-visits never duplicate a version.
  var SEEDED_KEY = 'ss_drilldown_seeded';
  var MANIFEST_URL = 'notes/drilldown/index.json';

  function _readSeeded() {
    try {
      var raw = localStorage.getItem(SEEDED_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (_) { return []; }
  }

  function _markSeeded(id) {
    var seen = _readSeeded();
    if (seen.indexOf(id) === -1) {
      seen.push(id);
      try { localStorage.setItem(SEEDED_KEY, JSON.stringify(seen)); } catch (_) {}
    }
  }

  // Strip the leading YAML frontmatter block backend.py / save_drilldown.py
  // emit, returning just the embedded HTML body.
  function _stripFrontmatter(text) {
    if (typeof text !== 'string') return '';
    var m = text.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n+/);
    return m ? text.slice(m[0].length) : text;
  }

  var _hydrating = null;

  function hydrateFromManifest() {
    if (typeof fetch !== 'function') return Promise.resolve(false);
    // Coalesce concurrent calls (boot auto-run + a manual invocation) so a
    // single manifest entry can't be seeded twice in one session.
    if (_hydrating) return _hydrating;
    _hydrating = _doHydrate().then(function (r) { _hydrating = null; return r; },
                                  function (e) { _hydrating = null; throw e; });
    return _hydrating;
  }

  function _doHydrate() {
    var seeded = _readSeeded();
    return fetch(MANIFEST_URL, { cache: 'no-cache' })
      .then(function (res) {
        if (!res.ok) throw new Error('manifest ' + res.status);
        return res.json();
      })
      .then(function (manifest) {
        var entries = (manifest && Array.isArray(manifest.drilldowns)) ? manifest.drilldowns : [];
        var pending = entries.filter(function (e) {
          return e && e.id && seeded.indexOf(e.id) === -1 && e.markdown_path;
        });
        if (!pending.length) return false;
        return Promise.all(pending.map(function (e) {
          return fetch(e.markdown_path, { cache: 'no-cache' })
            .then(function (r) { if (!r.ok) throw new Error(e.markdown_path + ' ' + r.status); return r.text(); })
            .then(function (text) {
              // Re-check seeded state synchronously: another in-flight fetch
              // for the same id may have completed while this one awaited.
              if (_readSeeded().indexOf(e.id) !== -1) return false;
              var html = _stripFrontmatter(text);
              if (!html) { _markSeeded(e.id); return false; }
              save(e.ticker, {
                html: html,
                trigger: e.trigger || 'manual',
                part: e.part || null,
                company_name: e.title || null,
                generated_at: e.saved_at_utc || undefined,
              });
              _markSeeded(e.id);
              return true;
            })
            .catch(function (err) {
              console.warn('[drilldown-library] hydrate skipped', e.id, err);
              return false;
            });
        })).then(function (results) {
          return results.some(Boolean);
        });
      })
      .catch(function (err) {
        // Manifest 404 / network failure: keep empty-state, log once.
        console.warn('[drilldown-library] manifest hydration unavailable', err);
        return false;
      });
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
    hydrateFromManifest: hydrateFromManifest,
  };

  // Seed localStorage from the committed manifest on boot. save() already
  // broadcasts 'signalstack:drilldown-library-changed', so the drilldown
  // surface re-renders automatically once seeding completes.
  if (typeof document !== 'undefined') {
    var _boot = function () { try { hydrateFromManifest(); } catch (_) {} };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', _boot);
    } else {
      _boot();
    }
  }
})(typeof window !== 'undefined' ? window : this);
