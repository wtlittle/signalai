/* scores.js — Signal Stack AI quality + debate scoring engine
 * ============================================================================
 *
 * Two composite scores on a 0–100 scale, computed on every data refresh.
 *
 * QUALITY SCORE (calculateQualityScore)
 *   Measures fundamental business quality. Five weighted pillars:
 *     1. Profitability (FCF margin)                     25 pts
 *     2. Growth quality (revenue growth + NRR bonus)    20 pts
 *     3. Margin structure (gross margin)                20 pts
 *     4. Balance sheet (net cash vs net debt / EBITDA)  20 pts
 *     5. Earnings consistency (revenue beat rate)       15 pts
 *   If a pillar's input is unavailable, that pillar is dropped and the score
 *   is reweighted proportionally over the remaining pillars (no penalty).
 *
 * DEBATE INTENSITY (calculateDebateIntensity)
 *   Measures controversy / disagreement. Four weighted signals:
 *     1. Analyst target dispersion                      30 pts
 *     2. Short interest as % of float                   25 pts
 *     3. Earnings reaction volatility (last 4 prints)   25 pts
 *     4. EV/Sales multiple compression vs 5y high       20 pts
 *        (fallback: target std-dev / mean ratio)
 *   Same partial-data handling as Quality Score.
 *
 * INPUT CONVENTIONS
 *   Margin / growth ratios may arrive as either decimals (0.45) or percents
 *   (45). The internal _toPct() helper auto-normalizes anything between
 *   ±1.5 by multiplying by 100, so callers don't need to coerce.
 *
 * FIELD NAMES (best-effort, accepts aliases)
 *   Quality fields:    fcfMargin | freeCashflow + totalRevenue,
 *                      revenueGrowth, nrr, grossMargins, totalCash,
 *                      totalDebt, marketCap, enterpriseToEbitda + ev,
 *                      earningsHistory[].surprisePercent
 *   Debate fields:     targetHighPrice, targetLowPrice, targetMeanPrice,
 *                      price, shortPercentOfFloat, earningsHistory[],
 *                      evSales (current), evSales5yHigh,
 *                      numberOfAnalystOpinions
 *
 * USAGE
 *   const q = calculateQualityScore(tickerData[ticker]);
 *   //  -> { score: 78, tier: 'High', tierClass: 'high', breakdown: [...] }
 *   const d = calculateDebateIntensity(tickerData[ticker]);
 *   //  -> { score: 64, tier: 'Active', tierClass: 'active', breakdown: [...] }
 *
 *   Both functions never throw on partial / missing data; they return
 *   { score: null, tier: 'N/A', ... } when too few pillars resolve.
 *
 * ============================================================================
 */

(function (global) {
  'use strict';

  // --- Helpers -------------------------------------------------------------

  // Normalize margin/growth ratios. Accepts 0.20 -> 20 or 20 -> 20.
  function _toPct(v) {
    if (v == null || !isFinite(v)) return null;
    const n = Number(v);
    if (!isFinite(n)) return null;
    // Treat |x| <= 1.5 as decimal form (e.g. 0.20 = 20%), otherwise leave.
    return Math.abs(n) <= 1.5 ? n * 100 : n;
  }

  function _isFiniteNum(v) {
    return typeof v === 'number' && isFinite(v);
  }

  function _fmtPct(v) {
    if (v == null || !isFinite(v)) return '—';
    return (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%';
  }

  // Weighted reduction. Pillars: [{ score, weight, max, label, datum, skipped }]
  // Score is sum of (score * weight/maxPossibleWeight) renormalized to 100.
  function _reduce(pillars) {
    const active = pillars.filter(p => !p.skipped);
    if (active.length === 0) return { score: null, breakdown: pillars };
    const totalWeight = active.reduce((s, p) => s + p.weight, 0);
    // Each active pillar earns (its raw score / its max) * its weight.
    const earned = active.reduce((s, p) => {
      const ratio = p.max > 0 ? Math.max(0, Math.min(1, p.score / p.max)) : 0;
      return s + ratio * p.weight;
    }, 0);
    const score = totalWeight > 0 ? Math.round((earned / totalWeight) * 100) : null;
    return { score, breakdown: pillars };
  }

  // --- Tier classification -------------------------------------------------

  function _qualityTier(score) {
    if (score == null) return { tier: 'N/A', tierClass: 'na' };
    if (score >= 80) return { tier: 'Elite',    tierClass: 'elite' };
    if (score >= 65) return { tier: 'High',     tierClass: 'high' };
    if (score >= 45) return { tier: 'Moderate', tierClass: 'moderate' };
    if (score >= 25) return { tier: 'Low',      tierClass: 'low' };
    return                  { tier: 'Weak',     tierClass: 'weak' };
  }

  function _debateTier(score) {
    if (score == null) return { tier: 'N/A', tierClass: 'na' };
    if (score >= 75) return { tier: 'High',     tierClass: 'high' };
    if (score >= 45) return { tier: 'Active',   tierClass: 'active' };
    if (score >= 20) return { tier: 'Moderate', tierClass: 'moderate' };
    return                  { tier: 'Low',      tierClass: 'low' };
  }

  // --- Quality Score pillars ----------------------------------------------

  function _pillarProfitability(d) {
    // FCF Margin
    let fcfMarginPct = _toPct(d.fcfMargin);
    if (fcfMarginPct == null && _isFiniteNum(d.freeCashflow) && _isFiniteNum(d.totalRevenue) && d.totalRevenue > 0) {
      fcfMarginPct = (d.freeCashflow / d.totalRevenue) * 100;
    }
    if (fcfMarginPct == null) {
      return { label: 'Profitability', metric: 'FCF Margin', datum: '—', score: 0, max: 25, weight: 25, skipped: true };
    }
    let score;
    if (fcfMarginPct >= 25) score = 25;
    else if (fcfMarginPct >= 15) score = 18;
    else if (fcfMarginPct >= 5) score = 10;
    else if (fcfMarginPct >= 0) score = 5;
    else score = 0;
    return { label: 'Profitability', metric: 'FCF Margin', datum: _fmtPct(fcfMarginPct), score, max: 25, weight: 25, skipped: false };
  }

  function _pillarGrowth(d) {
    const revGrowthPct = _toPct(d.revenueGrowth);
    if (revGrowthPct == null) {
      return { label: 'Growth Quality', metric: 'Rev Growth', datum: '—', score: 0, max: 20, weight: 20, skipped: true };
    }
    let score;
    if (revGrowthPct >= 30) score = 20;
    else if (revGrowthPct >= 20) score = 15;
    else if (revGrowthPct >= 10) score = 10;
    else if (revGrowthPct >= 5) score = 5;
    else score = 2;
    let datum = _fmtPct(revGrowthPct);
    const nrrPct = _toPct(d.nrr);
    if (nrrPct != null && nrrPct >= 120) {
      score = Math.min(20, score + 5);
      datum += `, NRR ${Math.round(nrrPct)}%`;
    }
    return { label: 'Growth Quality', metric: 'Rev Growth (LTM)', datum, score, max: 20, weight: 20, skipped: false };
  }

  function _pillarMargins(d) {
    const gm = _toPct(d.grossMargins);
    if (gm == null) {
      return { label: 'Margin Structure', metric: 'Gross Margin', datum: '—', score: 0, max: 20, weight: 20, skipped: true };
    }
    let score;
    if (gm >= 75) score = 20;
    else if (gm >= 60) score = 14;
    else if (gm >= 40) score = 8;
    else score = 3;
    return { label: 'Margin Structure', metric: 'Gross Margin', datum: _fmtPct(gm), score, max: 20, weight: 20, skipped: false };
  }

  function _pillarBalanceSheet(d) {
    const cash = _isFiniteNum(d.totalCash) ? d.totalCash : null;
    const debt = _isFiniteNum(d.totalDebt) ? d.totalDebt : null;
    const mcap = _isFiniteNum(d.marketCap) ? d.marketCap : null;
    if (cash == null || debt == null) {
      return { label: 'Balance Sheet', metric: 'Net Cash / Debt', datum: '—', score: 0, max: 20, weight: 20, skipped: true };
    }
    const netCash = cash - debt;
    let score, datum;
    if (netCash > 0) {
      if (mcap && mcap > 0) {
        const ratio = netCash / mcap;
        if (ratio > 0.10) { score = 20; datum = `Net cash ${(ratio * 100).toFixed(1)}% of mcap`; }
        else { score = 14; datum = `Net cash ${(ratio * 100).toFixed(1)}% of mcap`; }
      } else {
        score = 14; datum = `Net cash $${(netCash / 1e9).toFixed(1)}B`;
      }
    } else {
      // Net debt — score by leverage if EBITDA available
      const evToEbitda = d.enterpriseToEbitda;
      const ev = _isFiniteNum(d.ev) ? d.ev : (_isFiniteNum(d.enterpriseValue) ? d.enterpriseValue : null);
      let ebitda = null;
      if (_isFiniteNum(evToEbitda) && evToEbitda > 0 && ev) ebitda = ev / evToEbitda;
      if (ebitda && ebitda > 0) {
        const lev = (-netCash) / ebitda;
        if (lev < 1) { score = 8; datum = `Net debt ${lev.toFixed(1)}x EBITDA`; }
        else if (lev <= 3) { score = 4; datum = `Net debt ${lev.toFixed(1)}x EBITDA`; }
        else { score = 0; datum = `Net debt ${lev.toFixed(1)}x EBITDA`; }
      } else {
        // No EBITDA — score conservatively as moderate net debt
        score = 4; datum = `Net debt $${((-netCash) / 1e9).toFixed(1)}B`;
      }
    }
    return { label: 'Balance Sheet', metric: 'Net Cash / Debt', datum, score, max: 20, weight: 20, skipped: false };
  }

  function _pillarEarningsConsistency(d) {
    const hist = Array.isArray(d.earningsHistory) ? d.earningsHistory : null;
    if (!hist || hist.length === 0) {
      return { label: 'Earnings Consistency', metric: 'Beat Rate', datum: '—', score: 0, max: 15, weight: 15, skipped: true };
    }
    const slice = hist.slice(-8);
    const beats = slice.filter(e => {
      const s = e.surprisePercent != null ? e.surprisePercent : e.surprise;
      return _isFiniteNum(s) && s > 0;
    }).length;
    const rate = slice.length > 0 ? beats / slice.length : null;
    if (rate == null) {
      return { label: 'Earnings Consistency', metric: 'Beat Rate', datum: '—', score: 0, max: 15, weight: 15, skipped: true };
    }
    let score;
    if (rate >= 0.875) score = 15;
    else if (rate >= 0.75) score = 10;
    else if (rate >= 0.625) score = 6;
    else score = 2;
    return { label: 'Earnings Consistency', metric: 'Beat Rate', datum: `${beats}/${slice.length} (${Math.round(rate * 100)}%)`, score, max: 15, weight: 15, skipped: false };
  }

  // --- Debate Intensity signals -------------------------------------------

  function _signalTargetDispersion(d) {
    const hi = _isFiniteNum(d.targetHighPrice) ? d.targetHighPrice : null;
    const lo = _isFiniteNum(d.targetLowPrice) ? d.targetLowPrice : null;
    const px = _isFiniteNum(d.price) ? d.price : null;
    if (hi == null || lo == null || px == null || px <= 0) {
      return { label: 'Target Dispersion', metric: '(High − Low) / Price', datum: '—', score: 0, max: 30, weight: 30, skipped: true };
    }
    const disp = ((hi - lo) / px) * 100;
    let score;
    if (disp > 80) score = 30;
    else if (disp >= 50) score = 22;
    else if (disp >= 25) score = 14;
    else if (disp >= 10) score = 7;
    else score = 2;
    return { label: 'Target Dispersion', metric: '(High − Low) / Price', datum: `${disp.toFixed(0)}% spread`, score, max: 30, weight: 30, skipped: false };
  }

  function _signalShortInterest(d) {
    let si = d.shortPercentOfFloat;
    if (si == null) si = d.shortPercent;
    if (si == null) return { label: 'Short Interest', metric: '% of Float', datum: '—', score: 0, max: 25, weight: 25, skipped: true };
    // Normalize: if value < 1, treat as decimal fraction
    const siPct = Math.abs(si) <= 1 ? si * 100 : si;
    let score;
    if (siPct > 15) score = 25;
    else if (siPct >= 8) score = 18;
    else if (siPct >= 4) score = 10;
    else if (siPct >= 1) score = 4;
    else score = 1;
    return { label: 'Short Interest', metric: '% of Float', datum: `${siPct.toFixed(1)}%`, score, max: 25, weight: 25, skipped: false };
  }

  function _signalEarningsVol(d) {
    // Average absolute 1-day move on last 4 earnings prints.
    // Accepts either earningsHistory[].priceReaction1d / earningsReaction
    // or absoluteMove.
    const hist = Array.isArray(d.earningsHistory) ? d.earningsHistory.slice(-4) : null;
    if (!hist || hist.length === 0) {
      return { label: 'Earnings Vol', metric: 'Avg |1d move|', datum: '—', score: 0, max: 25, weight: 25, skipped: true };
    }
    const moves = hist.map(e => {
      const m = e.priceReaction1d != null ? e.priceReaction1d
              : e.earningsReaction != null ? e.earningsReaction
              : e.absoluteMove != null ? e.absoluteMove
              : null;
      return _isFiniteNum(m) ? Math.abs(_toPct(m)) : null;
    }).filter(m => m != null);
    if (moves.length === 0) {
      return { label: 'Earnings Vol', metric: 'Avg |1d move|', datum: '—', score: 0, max: 25, weight: 25, skipped: true };
    }
    const avg = moves.reduce((s, x) => s + x, 0) / moves.length;
    let score;
    if (avg > 15) score = 25;
    else if (avg >= 10) score = 18;
    else if (avg >= 5) score = 11;
    else if (avg >= 2) score = 5;
    else score = 1;
    return { label: 'Earnings Vol', metric: 'Avg |1d move|', datum: `${avg.toFixed(1)}% (n=${moves.length})`, score, max: 25, weight: 25, skipped: false };
  }

  function _signalMultipleRange(d) {
    // Primary: 5y EV/Sales high vs current.
    const cur = _isFiniteNum(d.evSales) ? d.evSales : null;
    const hi5 = _isFiniteNum(d.evSales5yHigh) ? d.evSales5yHigh : null;
    if (cur != null && hi5 != null && cur > 0 && hi5 > cur) {
      const compression = (hi5 - cur) / cur;
      let score;
      if (compression > 0.7) score = 20;
      else if (compression >= 0.4) score = 14;
      else if (compression >= 0.15) score = 8;
      else score = 3;
      return { label: 'Multiple Range', metric: '5y high → now compression', datum: `${(compression * 100).toFixed(0)}% compressed`, score, max: 20, weight: 20, skipped: false };
    }
    // Fallback: analyst dispersion (treat (high - low) / (2 * sqrt(3) * mean) as approx std/mean)
    const hi = _isFiniteNum(d.targetHighPrice) ? d.targetHighPrice : null;
    const lo = _isFiniteNum(d.targetLowPrice) ? d.targetLowPrice : null;
    const mean = _isFiniteNum(d.targetMeanPrice) ? d.targetMeanPrice : null;
    if (hi != null && lo != null && mean != null && mean > 0 && hi >= lo) {
      const cv = (hi - lo) / (2 * Math.sqrt(3) * mean); // approx std-dev / mean for uniform
      let score;
      if (cv > 0.25) score = 20;
      else if (cv >= 0.15) score = 13;
      else score = 5;
      return { label: 'Multiple Range', metric: 'Target σ / mean', datum: `${(cv * 100).toFixed(1)}%`, score, max: 20, weight: 20, skipped: false };
    }
    return { label: 'Multiple Range', metric: '5y compression', datum: '—', score: 0, max: 20, weight: 20, skipped: true };
  }

  // --- Public API ----------------------------------------------------------

  function calculateQualityScore(financials) {
    const d = financials || {};
    const pillars = [
      _pillarProfitability(d),
      _pillarGrowth(d),
      _pillarMargins(d),
      _pillarBalanceSheet(d),
      _pillarEarningsConsistency(d),
    ];
    const { score, breakdown } = _reduce(pillars);
    const tierInfo = _qualityTier(score);
    return {
      score,
      tier: tierInfo.tier,
      tierClass: tierInfo.tierClass,
      breakdown,
      activePillars: pillars.filter(p => !p.skipped).length,
    };
  }

  function calculateDebateIntensity(marketData) {
    const d = marketData || {};
    const signals = [
      _signalTargetDispersion(d),
      _signalShortInterest(d),
      _signalEarningsVol(d),
      _signalMultipleRange(d),
    ];
    const { score, breakdown } = _reduce(signals);
    const tierInfo = _debateTier(score);
    return {
      score,
      tier: tierInfo.tier,
      tierClass: tierInfo.tierClass,
      breakdown,
      activePillars: signals.filter(p => !p.skipped).length,
    };
  }

  // --- Display helpers (shared badge / tooltip rendering) -----------------

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function buildBreakdownTooltip(result, kind) {
    if (!result || !result.breakdown) return '';
    const title = kind === 'quality' ? 'Quality Score breakdown' : 'Debate Intensity breakdown';
    const rows = result.breakdown.map(p => {
      const bar = p.skipped
        ? '<span class="ss-bd-skip">— no data</span>'
        : `<span class="ss-bd-score">${p.score}/${p.max}</span>`;
      return `<div class="ss-bd-row${p.skipped ? ' ss-bd-row-skip' : ''}">
        <div class="ss-bd-label">${_esc(p.label)}</div>
        <div class="ss-bd-meta">${_esc(p.metric)}: ${_esc(p.datum)}</div>
        ${bar}
      </div>`;
    }).join('');
    const headline = result.score != null
      ? `${result.score} · ${_esc(result.tier)}`
      : 'Insufficient data';
    return `<div class="ss-bd-card ss-bd-${kind}">
      <div class="ss-bd-head">
        <span class="ss-bd-title">${_esc(title)}</span>
        <span class="ss-bd-headline">${headline}</span>
      </div>
      <div class="ss-bd-body">${rows}</div>
      <div class="ss-bd-foot">Active pillars: ${result.activePillars}/${result.breakdown.length}</div>
    </div>`;
  }

  // Compact pill badge with hover tooltip. `kind` = 'quality' | 'debate'.
  function buildBadgeHtml(result, kind, opts) {
    opts = opts || {};
    const compact = !!opts.compact;
    const showLabel = opts.showLabel !== false;
    const labelPrefix = kind === 'quality' ? 'Quality' : 'Debate';
    const cls = `ss-score-badge ss-score-${kind} ss-score-${result.tierClass || 'na'}${compact ? ' ss-score-compact' : ''}`;
    if (result.score == null) {
      return `<span class="${cls}" title="${_esc(labelPrefix)}: insufficient data" data-score-kind="${kind}">
        <span class="ss-score-num">—</span>
        ${showLabel ? `<span class="ss-score-tier">${_esc(labelPrefix)}</span>` : ''}
      </span>`;
    }
    // Native title= for compact; richer tooltip element for non-compact
    const compactTitle = `${labelPrefix}: ${result.score} · ${result.tier}\n` +
      result.breakdown.map(p => `  ${p.label}: ${p.skipped ? '—' : (p.score + '/' + p.max)} (${p.datum})`).join('\n');
    if (compact) {
      return `<span class="${cls}" title="${_esc(compactTitle)}" data-score-kind="${kind}">
        <span class="ss-score-num">${result.score}</span>
        ${showLabel ? `<span class="ss-score-tier">${_esc(result.tier)}</span>` : ''}
      </span>`;
    }
    const tip = buildBreakdownTooltip(result, kind);
    return `<span class="${cls} ss-score-rich" data-score-kind="${kind}" tabindex="0">
      <span class="ss-score-num">${result.score}</span>
      <span class="ss-score-sep">·</span>
      <span class="ss-score-tier">${_esc(result.tier)}</span>
      <span class="ss-score-tip">${tip}</span>
    </span>`;
  }

  // Convenience: render both badges side by side
  function buildPairHtml(financials, opts) {
    const q = calculateQualityScore(financials);
    const dd = calculateDebateIntensity(financials);
    return buildBadgeHtml(q, 'quality', opts) + buildBadgeHtml(dd, 'debate', opts);
  }

  // --- Export --------------------------------------------------------------
  global.SignalScores = {
    calculateQualityScore,
    calculateDebateIntensity,
    buildBadgeHtml,
    buildBreakdownTooltip,
    buildPairHtml,
    // also expose tiers for tests / external consumers
    _qualityTier,
    _debateTier,
  };
})(typeof window !== 'undefined' ? window : this);
