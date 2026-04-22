/* ===== ROUTER.JS =====
 * Minimal hash-based router for SignalStack AI surfaces.
 *
 * Surfaces:
 *   #/coverage       (default / home)
 *   #/compare
 *   #/screener
 *   #/earnings
 *   #/private
 *   #/drilldown/:ticker
 *   #/more           (briefing, macro, news, alerts — legacy tabs preserved)
 *
 * The router manages showing/hiding surface containers with a simple
 * data attribute convention: <section data-surface="coverage">...</section>.
 * Each surface is also expected to implement an optional init()/activate()
 * hook registered via SignalRouter.register(surface, { onActivate, onDeactivate }).
 */
(function (global) {
  'use strict';

  const DEFAULT_SURFACE = 'coverage';
  const KNOWN_SURFACES = new Set([
    'coverage', 'compare', 'screener', 'earnings', 'private', 'drilldown', 'more'
  ]);

  const hooks = {}; // { surface: { onActivate, onDeactivate } }
  let current = null;
  let currentParams = null;

  function parseHash() {
    const h = (location.hash || '').replace(/^#\/?/, '').trim();
    if (!h) return { surface: DEFAULT_SURFACE, params: {} };
    const parts = h.split('/').filter(Boolean);
    const surface = parts[0];
    if (!KNOWN_SURFACES.has(surface)) return { surface: DEFAULT_SURFACE, params: {} };
    const params = {};
    if (surface === 'drilldown' && parts[1]) params.ticker = parts[1].toUpperCase();
    return { surface: surface, params: params };
  }

  function setHash(surface, params) {
    let h = '#/' + surface;
    if (surface === 'drilldown' && params && params.ticker) h += '/' + params.ticker;
    if (location.hash !== h) location.hash = h;
  }

  function go(surface, params) {
    if (!KNOWN_SURFACES.has(surface)) surface = DEFAULT_SURFACE;
    setHash(surface, params || {});
    // Hashchange listener will do the rest.
  }

  function register(surface, h) {
    hooks[surface] = h || {};
  }

  function _activate(surface, params) {
    // Hide ALL surfaces
    document.querySelectorAll('[data-surface]').forEach(el => {
      el.hidden = el.dataset.surface !== surface;
    });
    // Update nav active state
    document.querySelectorAll('[data-nav-surface]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.navSurface === surface);
    });
    // Set top-level attribute for CSS hooks
    try { document.documentElement.setAttribute('data-active-surface', surface); } catch (_) {}

    // Fire hooks
    if (current && current !== surface && hooks[current] && hooks[current].onDeactivate) {
      try { hooks[current].onDeactivate(); } catch (e) { console.error(e); }
    }
    if (hooks[surface] && hooks[surface].onActivate) {
      try { hooks[surface].onActivate(params || {}); } catch (e) { console.error(e); }
    }

    current = surface;
    currentParams = params || {};
    try {
      document.dispatchEvent(new CustomEvent('signalstack:route-changed', {
        detail: { surface: surface, params: params }
      }));
    } catch (_) {}
  }

  function _onHashChange() {
    const r = parseHash();
    _activate(r.surface, r.params);
  }

  function start() {
    window.addEventListener('hashchange', _onHashChange);
    // Initial activate
    _onHashChange();
  }

  function getCurrent() { return { surface: current, params: currentParams }; }

  global.SignalRouter = {
    go: go,
    register: register,
    start: start,
    getCurrent: getCurrent,
    KNOWN_SURFACES: Array.from(KNOWN_SURFACES),
    DEFAULT_SURFACE: DEFAULT_SURFACE,
  };
})(typeof window !== 'undefined' ? window : global);
