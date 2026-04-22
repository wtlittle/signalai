/* ===== MODE.JS =====
 * Mode toggle: Hedge Fund (default) / Long Only
 *
 * Mode is persisted in localStorage. Listeners are notified when mode changes
 * so surfaces can swap defaults (saved views, default columns, summary strip
 * metrics, right-panel emphasis, etc.).
 */
(function (global) {
  'use strict';

  const STORAGE_KEY = 'ss_mode_v1';
  const MODES = { HF: 'hf', LO: 'lo' };
  const LABELS = { hf: 'Hedge Fund', lo: 'Long Only' };
  const SHORT_LABELS = { hf: 'HF', lo: 'LO' };

  function _get() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw === MODES.HF || raw === MODES.LO) return raw;
    } catch (_) {}
    return MODES.HF; // default
  }

  function _set(m) {
    try { localStorage.setItem(STORAGE_KEY, m); } catch (_) {}
  }

  let current = _get();
  const listeners = [];

  function get() { return current; }
  function isHF() { return current === MODES.HF; }
  function isLO() { return current === MODES.LO; }
  function label() { return LABELS[current]; }
  function shortLabel() { return SHORT_LABELS[current]; }

  function set(m) {
    if (m !== MODES.HF && m !== MODES.LO) return;
    if (m === current) return;
    current = m;
    _set(m);
    listeners.forEach(fn => { try { fn(m); } catch (e) { console.error(e); } });
    // Broadcast a DOM event too for non-JS listeners (CSS attr hook)
    try {
      document.documentElement.setAttribute('data-mode', m);
      document.dispatchEvent(new CustomEvent('signalstack:mode-changed', { detail: { mode: m } }));
    } catch (_) {}
  }

  function toggle() { set(current === MODES.HF ? MODES.LO : MODES.HF); }

  function onChange(fn) {
    if (typeof fn === 'function') listeners.push(fn);
  }

  // Apply on load so CSS can key off [data-mode="hf"|"lo"]
  try { document.documentElement.setAttribute('data-mode', current); } catch (_) {}

  global.SignalMode = {
    MODES: MODES,
    LABELS: LABELS,
    get: get,
    set: set,
    toggle: toggle,
    isHF: isHF,
    isLO: isLO,
    label: label,
    shortLabel: shortLabel,
    onChange: onChange,
  };
})(typeof window !== 'undefined' ? window : global);
