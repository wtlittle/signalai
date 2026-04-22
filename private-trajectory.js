/* ===== PRIVATE-TRAJECTORY.JS — Valuation + funding history utilities =====
 *
 * Primary anchor: valuation trajectory.
 * Revenue / ARR is contextual interpretation when it exists.
 *
 * Data model (added to each private company when available):
 *   valuation_history:  [{ date, valuation_usd, round, source? }]
 *   funding_history:    [{ date, round, amount_usd, valuation_usd, lead_investors, notes? }]
 *   estimated_revenue_history: [{ date, revenue_usd, source? }]
 *
 * Missing data degrades gracefully: compact "N/A" signals are emitted.
 */
(function (global) {
  'use strict';

  // ----------------- Parse helpers -----------------
  function parseValuation(val) {
    if (typeof val === 'number') return val;
    if (typeof val !== 'string') return null;
    const s = val.replace(/[\s,]/g, '').replace(/\$/g, '').toLowerCase();
    // Forms: 42b, 1.2b, 300m, 15t
    const m = s.match(/^([\d.]+)\s*([bmkt])?\+?(?:\(ipo\))?/);
    if (!m) return null;
    const num = parseFloat(m[1]);
    const suffix = m[2] || '';
    if (Number.isNaN(num)) return null;
    const mult = suffix === 't' ? 1e12 : suffix === 'b' ? 1e9 : suffix === 'm' ? 1e6 : suffix === 'k' ? 1e3 : 1;
    return num * mult;
  }

  function formatUsd(n) {
    if (n == null || Number.isNaN(n)) return '\u2014';
    if (n >= 1e12) return '$' + (n / 1e12).toFixed(n >= 1e13 ? 0 : 2) + 'T';
    if (n >= 1e9)  return '$' + (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + 'B';
    if (n >= 1e6)  return '$' + (n / 1e6).toFixed(n >= 1e8 ? 0 : 1) + 'M';
    if (n >= 1e3)  return '$' + (n / 1e3).toFixed(0) + 'K';
    return '$' + Math.round(n);
  }

  function pctChange(newV, oldV) {
    if (!newV || !oldV) return null;
    return (newV / oldV - 1) * 100;
  }

  function formatReturn(from, to) {
    if (!from || !to) return '\u2014';
    const r = to / from;
    if (r >= 2) return '+' + r.toFixed(1) + 'x';
    const pct = (r - 1) * 100;
    const sign = pct >= 0 ? '+' : '';
    return sign + pct.toFixed(0) + '%';
  }

  // ----------------- Derive valuation_history -----------------
  /**
   * Accepts a private company object and returns an effective valuation_history
   * array. If the object already has valuation_history, it is returned.
   * Otherwise we parse last valuation from the 'valuation' string and emit
   * a single-point history (so the trajectory tab still renders meaningfully).
   */
  function getValuationHistory(co) {
    if (Array.isArray(co.valuation_history) && co.valuation_history.length) {
      return co.valuation_history.slice().sort((a, b) => {
        const da = new Date(a.date || '1970-01-01').getTime();
        const db = new Date(b.date || '1970-01-01').getTime();
        return da - db;
      });
    }
    const v = parseValuation(co.valuation);
    if (v == null) return [];
    const roundDate = extractRoundDate(co.funding) || null;
    return [{
      date: roundDate || new Date().toISOString().slice(0, 10),
      valuation_usd: v,
      round: extractRoundType(co.funding) || 'Latest round',
      source: 'inferred'
    }];
  }

  function getFundingHistory(co) {
    if (Array.isArray(co.funding_history) && co.funding_history.length) {
      return co.funding_history.slice().sort((a, b) => {
        const da = new Date(a.date || '1970-01-01').getTime();
        const db = new Date(b.date || '1970-01-01').getTime();
        return da - db;
      });
    }
    const amt = parseValuation((co.funding || '').replace(/\(.*?\)/g, ''));
    const valuation = parseValuation(co.valuation);
    const roundDate = extractRoundDate(co.funding) || null;
    const round = extractRoundType(co.funding) || 'Latest round';
    if (!valuation && !amt) return [];
    return [{
      date: roundDate || new Date().toISOString().slice(0, 10),
      round: round,
      amount_usd: amt,
      valuation_usd: valuation,
      lead_investors: co.lead_investors || '',
      source: 'inferred'
    }];
  }

  function extractRoundDate(str) {
    if (!str) return null;
    // "Series D (Jan 2026)"
    const m = str.match(/\(([^)]+)\)/);
    if (!m) return null;
    const d = new Date(m[1]);
    return isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
  }

  function extractRoundType(str) {
    if (!str) return null;
    const m = str.match(/^[^()]+/);
    return m ? m[0].trim() : null;
  }

  // ----------------- Returns summary for row display -----------------
  /**
   * Returns a compact signal for the main private table, e.g.
   *   { label: '+82% since prior round', tone: 'up' }
   *   { label: '+7.5x since Series A',   tone: 'up' }
   *   { label: 'N/A', tone: 'flat' }
   */
  function compactReturnSignal(co) {
    const vh = getValuationHistory(co);
    if (vh.length >= 2) {
      const latest = vh[vh.length - 1].valuation_usd;
      const prior = vh[vh.length - 2].valuation_usd;
      const change = pctChange(latest, prior);
      if (change == null) return { label: 'N/A', tone: 'flat' };
      const tone = change > 2 ? 'up' : change < -2 ? 'down' : 'flat';
      if (Math.abs(latest / prior) >= 2 && tone === 'up') {
        return { label: '+' + (latest / prior).toFixed(1) + 'x since prior round', tone };
      }
      return {
        label: (change >= 0 ? '+' : '') + change.toFixed(0) + '% since prior round',
        tone: tone
      };
    }
    // Fallback: since first tracked round — but we only have 1 point, so skip
    if (vh.length === 1) {
      return { label: 'Single tracked round', tone: 'flat' };
    }
    return { label: 'N/A', tone: 'flat' };
  }

  // ----------------- Public API -----------------
  global.SignalPrivateTrajectory = {
    parseValuation,
    formatUsd,
    pctChange,
    formatReturn,
    getValuationHistory,
    getFundingHistory,
    compactReturnSignal,
    extractRoundDate,
    extractRoundType
  };

})(window);
