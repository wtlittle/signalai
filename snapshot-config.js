/* ===== SNAPSHOT-CONFIG.JS — Centralized snapshot hosting ===== */
/*
 * Snapshots live in Cloudflare R2 (public dev bucket). This module is the
 * single source of truth for where to fetch snapshot JSON from.
 *
 * To force local file usage during dev, set `window.SIGNALAI_USE_LOCAL = true`
 * in the browser console BEFORE page load, or add ?local=1 to the URL.
 */
(function (global) {
  'use strict';

  // Cloudflare R2 public dev URL (configured CORS allows github.io + localhost)
  var R2_BASE = 'https://pub-2e23479367774577a65757b8f638478a.r2.dev';

  // Explicit list of snapshot files migrated to R2.
  // Add new snapshot filenames here as they migrate.
  var MIGRATED_FILES = [
    'data-snapshot.json',
    'earnings_data.json',
    'earnings_intel.json',
    'earnings_calendar.json',
    'macro_data.json',
    'weekly_briefing.json',
    'earnings_notes_index.json'
  ];

  function useLocal() {
    try {
      if (global.SIGNALAI_USE_LOCAL === true) return true;
      if (typeof global.location !== 'undefined' &&
          /[?&](local|dev)=1\b/.test(global.location.search || '')) {
        return true;
      }
      if (typeof global.location !== 'undefined' &&
          (global.location.hostname === 'localhost' ||
           global.location.hostname === '127.0.0.1') &&
          global.SIGNALAI_PREFER_LOCAL !== false) {
        // On localhost default to local files unless explicitly told otherwise
        return true;
      }
    } catch (e) { /* no-op */ }
    return false;
  }

  function baseUrl() {
    return useLocal() ? '' : R2_BASE;
  }

  /**
   * Build the canonical URL for a snapshot JSON.
   * @param {string} filename - bare filename, e.g. "earnings_data.json"
   * @param {object} [opts]   - { cacheBust: boolean }
   */
  function getSnapshotUrl(filename, opts) {
    opts = opts || {};
    var base = baseUrl();
    var url = base ? (base.replace(/\/$/, '') + '/' + filename) : filename;
    if (opts.cacheBust) {
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'v=' + Date.now();
    }
    return url;
  }

  /**
   * Fetch a snapshot with consistent error handling.
   * Returns parsed JSON or throws (caller decides what fallback UI to show).
   */
  async function fetchSnapshot(filename, opts) {
    opts = opts || {};
    var url = getSnapshotUrl(filename, { cacheBust: opts.cacheBust !== false });
    var timeoutMs = opts.timeoutMs || 15000;
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
    try {
      var resp = await fetch(url, controller ? { signal: controller.signal } : undefined);
      if (!resp.ok) {
        throw new Error('Snapshot fetch failed: ' + filename + ' (HTTP ' + resp.status + ')');
      }
      return await resp.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  // Visible status for banner UI; readers can subscribe via onStatus().
  var _status = { ok: true, failures: [] };
  var _listeners = [];
  function markFailure(filename, err) {
    _status.ok = false;
    _status.failures.push({ filename: filename, error: String(err && err.message || err), at: new Date().toISOString() });
    _listeners.forEach(function (fn) { try { fn(_status); } catch (e) {} });
  }
  function onStatus(fn) { _listeners.push(fn); return function () { _listeners = _listeners.filter(function (x) { return x !== fn; }); }; }
  function status() { return _status; }

  global.SignalSnapshot = {
    R2_BASE: R2_BASE,
    MIGRATED_FILES: MIGRATED_FILES,
    getSnapshotUrl: getSnapshotUrl,
    fetchSnapshot: fetchSnapshot,
    useLocal: useLocal,
    markFailure: markFailure,
    onStatus: onStatus,
    status: status
  };
})(typeof window !== 'undefined' ? window : this);
