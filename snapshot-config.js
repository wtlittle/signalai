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

  /**
   * Repo-relative fallback URL for a snapshot. Used when R2 is unreachable or
   * returns 404 — the JSON files are also committed to the repo root and ship
   * with GitHub Pages, so a same-origin fetch is a reliable fallback.
   */
  function getFallbackUrl(filename, opts) {
    opts = opts || {};
    var url = filename;
    if (opts.cacheBust) {
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'v=' + Date.now();
    }
    return url;
  }

  /**
   * Fetch a snapshot and transparently fall back to the repo-relative same-origin
   * path on network error or non-ok response. Returns a Fetch Response so callers
   * that currently do `fetch(...)` can drop this in with a one-line change.
   *
   * Only calls markFailure when BOTH the primary and fallback fail — so the
   * degraded-data banner only surfaces on REAL outages, not on transient R2 404s
   * that recover via fallback.
   *
   * @param {string} filename
   * @param {object} [opts] - { cacheBust: boolean, timeoutMs: number }
   * @returns {Promise<Response>}
   */
  async function fetchWithFallback(filename, opts) {
    opts = opts || {};
    var cacheBust = opts.cacheBust !== false;
    var timeoutMs = opts.timeoutMs || 15000;
    var primaryUrl = getSnapshotUrl(filename, { cacheBust: cacheBust });
    var fallbackUrl = getFallbackUrl(filename, { cacheBust: cacheBust });
    var primaryErr = null;
    // If useLocal() is true the primary URL is already repo-relative — skip the
    // duplicate attempt.
    if (primaryUrl !== fallbackUrl) {
      try {
        var controller1 = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        var timer1 = controller1 ? setTimeout(function () { controller1.abort(); }, timeoutMs) : null;
        try {
          var resp1 = await fetch(primaryUrl, controller1 ? { signal: controller1.signal } : undefined);
          if (resp1.ok) return resp1;
          primaryErr = new Error('HTTP ' + resp1.status);
        } finally {
          if (timer1) clearTimeout(timer1);
        }
      } catch (e) {
        primaryErr = e;
      }
    }
    // Fallback: same-origin
    try {
      var controller2 = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      var timer2 = controller2 ? setTimeout(function () { controller2.abort(); }, timeoutMs) : null;
      try {
        var resp2 = await fetch(fallbackUrl, controller2 ? { signal: controller2.signal } : undefined);
        if (resp2.ok) return resp2;
        var err = new Error('Snapshot fetch failed (both R2 and fallback): ' + filename + ' primary=' + (primaryErr && primaryErr.message || 'ok') + ' fallback=HTTP ' + resp2.status);
        markFailure(filename, err);
        return resp2; // caller can inspect .ok / .status
      } finally {
        if (timer2) clearTimeout(timer2);
      }
    } catch (e) {
      markFailure(filename, e);
      throw e;
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
    getFallbackUrl: getFallbackUrl,
    fetchSnapshot: fetchSnapshot,
    fetchWithFallback: fetchWithFallback,
    useLocal: useLocal,
    markFailure: markFailure,
    onStatus: onStatus,
    status: status
  };
})(typeof window !== 'undefined' ? window : this);
