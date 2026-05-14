/* ===== IDEA_ENGINE.JS — Per-stock signal computation for Macro + Screener
 *
 * Computes five idiosyncratic signals per ticker that feed both the Macro
 * tab's Own/Avoid cards and the screener filter columns. Pure function — no
 * DOM, no I/O — so it runs identically in the browser and from
 * refresh_macro.mjs in Node.
 *
 * The five signals (see Will + AI conversation 2026-05-14):
 *   1. alpha1m              Sector-relative momentum: stock 1M% - sector ETF 1M%
 *   2. regimeFactorScore    0..100 composite of regime-weighted factor z-scores
 *   3. earningsMomentumTag  tailwind | headwind | watching | inflecting | breaking | neutral
 *   4. valueForGrowthPctile 0..100 subsector-aware value-vs-growth percentile
 *                           (R40 / EV_Sales for multiple names; PEG-yield etc.)
 *   5. passthroughTags      array of categorical macro-exposure tags
 *
 * Inputs (one ticker):
 *   row             = data-snapshot fundamentals (revenueGrowth, evSales, ...)
 *   sectorEtf       = string e.g. 'XLK', 'XLE'
 *   sectorMonthPct  = sector ETF's 1M % (from macroData.sectors[etf].change_1m)
 *   peers           = array of peer row objects in the same subsector
 *   universe        = full set of rows for cross-sectional z-scoring
 *   regimeName      = 'Goldilocks' | 'Reflation' | ... matching factor matrix
 *   intelEntry      = earnings_intel.json tickers[TICKER] entry (may be null)
 *   factorWeights   = REGIME_FACTOR_WEIGHTS[regimeName] from regime_factors.js
 *   tagBoosts       = REGIME_TAG_BOOSTS[regimeName]
 *   passthroughTags = pre-resolved tags from regime_factors.SUBSECTOR_PASSTHROUGH
 * ========================================================================= */
(function () {
  'use strict';

  // ── Number helpers (defensive) ────────────────────────────────────────
  function num(v) {
    if (v == null) return null;
    if (typeof v === 'number') return Number.isFinite(v) ? v : null;
    const n = parseFloat(String(v).replace(/[%,]/g, ''));
    return Number.isFinite(n) ? n : null;
  }
  function pctOf(v) {
    // Treat 0..1 as a fraction (e.g. operatingMargins=0.45 = 45%) and
    // 1..200 as an already-formatted percent.
    const n = num(v);
    if (n == null) return null;
    if (Math.abs(n) <= 1.5) return n * 100;
    return n;
  }
  function safeMean(arr) {
    const a = arr.filter(x => Number.isFinite(x));
    return a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
  }
  function safeStd(arr) {
    const a = arr.filter(x => Number.isFinite(x));
    if (a.length < 2) return null;
    const m = safeMean(a);
    return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / a.length);
  }
  function percentileRank(values, value) {
    if (value == null || !Number.isFinite(value)) return null;
    const sorted = values.filter(v => Number.isFinite(v)).sort((a, b) => a - b);
    if (!sorted.length) return null;
    let below = 0;
    for (const v of sorted) if (v <= value) below++; else break;
    return Math.round((below / sorted.length) * 100);
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  // ── Universe stats (computed once, passed in by caller) ───────────────
  function buildUniverseStats(rows) {
    const collect = (key) => rows.map(r => num(r[key])).filter(Number.isFinite);
    const beta = collect('beta');
    const mcap = collect('marketCap');
    const fcfY = collect('fcfYield');
    const opMrg = collect('operatingMargins').map(pctOf).filter(Number.isFinite);
    const revG = collect('revenueGrowth').map(pctOf).filter(Number.isFinite);
    const evSls = collect('evSales');
    return {
      beta:        { mean: safeMean(beta),  std: safeStd(beta) },
      marketCap:   { values: mcap },
      fcfYield:    { mean: safeMean(fcfY),  std: safeStd(fcfY) },
      operating:   { mean: safeMean(opMrg), std: safeStd(opMrg) },
      revGrowth:   { mean: safeMean(revG),  std: safeStd(revG) },
      evSales:     { values: evSls },
    };
  }
  function zscore(value, mean, std) {
    if (value == null || mean == null || std == null || std === 0) return 0;
    return (value - mean) / std;
  }

  // ── Signal 1: alpha vs sector ETF ──────────────────────────────────────
  function computeAlpha1M(row, sectorMonthPct) {
    const stock1m = num(row.m1);
    if (stock1m == null) return null;
    const sec = num(sectorMonthPct);
    if (sec == null) return stock1m; // fallback to raw 1M
    return parseFloat((stock1m - sec).toFixed(2));
  }

  // ── Signal 4a: R40 ─────────────────────────────────────────────────────
  function computeR40(row) {
    const rg = pctOf(row.revenueGrowth);
    const fcfm = pctOf(row.fcfMargin);
    const om = pctOf(row.operatingMargins);
    if (rg == null) return { value: null, formula: 'unavailable' };
    if (fcfm != null) return { value: parseFloat((rg + fcfm).toFixed(1)), formula: 'rev_growth + fcf_margin' };
    if (om != null) return { value: parseFloat((rg + om).toFixed(1)), formula: 'rev_growth + op_margin' };
    return { value: rg, formula: 'rev_growth (margin missing)' };
  }

  // ── Subsector classifier — picks the right value-for-growth formula ────
  const SOFTWARE_SUBSECTORS = new Set([
    'Application Software', 'Infrastructure Software', 'Cybersecurity',
    'Data Analytics', 'Cloud Infrastructure', 'Vertical Software',
    'Internet Search & Ads', 'Internet Retail', 'Streaming Media',
    'Social Media', 'Gaming', 'Payments', 'Semiconductors',
  ]);
  const EARNINGS_SUBSECTORS = new Set([
    'Money Center Banks', 'Regional Banks', 'Insurance', 'Capital Markets',
    'Asset Managers', 'Industrial Machinery', 'Aerospace & Defense',
    'Building Products', 'Trucking', 'Steel', 'Chemicals',
    'Mass Retail', 'Specialty Retail', 'Restaurants', 'Apparel & Luxury',
    'Hardware', 'Networking Equipment', 'IT Services', 'Consumer Finance',
  ]);
  const YIELD_SUBSECTORS = new Set([
    'Oil & Gas E&P', 'Oil & Gas Integrated', 'Oil Services', 'Refining',
    'Pipelines', 'REITs', 'Utilities', 'Telecom', 'Tobacco', 'Beverages',
    'Packaged Food', 'Household Products', 'Healthcare Distribution',
    'Independent Power', 'Copper Mining', 'Gold Mining', 'Fertilizers',
  ]);

  function valueForGrowthScore(row) {
    const subsector = row.subsector || '';
    const evSales = num(row.evSales);
    const fwdPE = num(row.forwardPE);
    const rg = pctOf(row.revenueGrowth);
    const r40 = computeR40(row).value;
    const fcfYield = num(row.fcfYield);
    const opM = pctOf(row.operatingMargins);

    if (SOFTWARE_SUBSECTORS.has(subsector)) {
      if (evSales != null && evSales > 0 && r40 != null) {
        return { score: parseFloat((r40 / evSales).toFixed(2)), formula: 'R40 / EV·Sales (higher = better)', kind: 'r40_yield' };
      }
    }
    if (EARNINGS_SUBSECTORS.has(subsector)) {
      if (fwdPE != null && fwdPE > 0 && rg != null) {
        // PEG-yield: growth per dollar of P/E
        return { score: parseFloat((rg / fwdPE).toFixed(2)), formula: 'Growth / Fwd P/E (PEG-yield)', kind: 'peg_yield' };
      }
    }
    if (YIELD_SUBSECTORS.has(subsector)) {
      const yld = fcfYield != null ? fcfYield : null;
      if (yld != null) {
        const growthFactor = 1 + Math.max(0, (rg || 0) / 100);
        return { score: parseFloat((yld * growthFactor).toFixed(2)), formula: 'FCF yield × (1 + growth) — yield-style', kind: 'fcf_yield' };
      }
    }
    // Generic fallback: prefer R40 yield if EV/Sales exists, else PEG-yield, else FCF yield
    if (evSales != null && evSales > 0 && r40 != null) {
      return { score: parseFloat((r40 / evSales).toFixed(2)), formula: 'R40 / EV·Sales (generic)', kind: 'r40_yield' };
    }
    if (fwdPE != null && fwdPE > 0 && rg != null) {
      return { score: parseFloat((rg / fwdPE).toFixed(2)), formula: 'Growth / Fwd P/E (generic)', kind: 'peg_yield' };
    }
    if (fcfYield != null) {
      return { score: parseFloat(fcfYield.toFixed(2)), formula: 'FCF yield (fallback)', kind: 'fcf_yield' };
    }
    return { score: null, formula: 'insufficient data', kind: null };
  }

  // ── Signal 4: value-for-growth percentile within subsector ─────────────
  function computeValueForGrowthPercentile(row, peers) {
    const me = valueForGrowthScore(row);
    if (me.score == null) return { percentile: null, details: me };
    // Only compare to peers using the same scoring kind (mixing PEG-yield and
    // FCF-yield would be apples-to-oranges).
    const peerScores = peers
      .filter(p => p.ticker !== row.ticker)
      .map(p => valueForGrowthScore(p))
      .filter(p => p.kind === me.kind && p.score != null)
      .map(p => p.score);
    if (peerScores.length < 3) {
      // Not enough peers for a meaningful percentile — fall back to universe
      return { percentile: null, details: me, peerCount: peerScores.length };
    }
    const pct = percentileRank(peerScores, me.score);
    return { percentile: pct, details: me, peerCount: peerScores.length };
  }

  // ── Signal 3: earnings momentum tag ────────────────────────────────────
  function classifyEarningsMomentum(intelEntry) {
    if (!intelEntry) return { tag: 'neutral', note: 'No earnings intel record' };
    const state = (intelEntry.state || '').toLowerCase();
    const inflection = (intelEntry.inflection_status || '').toLowerCase();
    const tone = ((intelEntry.tone_drift || {}).current_tone || '').toLowerCase();
    const scorecard = intelEntry.signal_scorecard || [];

    // Inflection trumps everything
    if (inflection === 'confirmed') return { tag: 'inflecting', note: 'Inflection confirmed' };
    if (inflection === 'broken') return { tag: 'breaking', note: 'Inflection broken' };

    if (state === 'post_earnings') {
      const headline = scorecard.find(s => (s.signal_id || '') === 'headline_results');
      const guide = scorecard.find(s => (s.signal_id || '') === 'guidance_trajectory');
      const hStatus = (headline && headline.status || '').toUpperCase();
      const gStatus = (guide && guide.status || '').toUpperCase();
      const positiveTone = tone === 'constructive';
      const negativeTone = tone === 'cautious';

      if (hStatus === 'CONFIRMED' && !negativeTone) return { tag: 'tailwind', note: 'Headline beat + ' + (positiveTone ? 'constructive tone' : 'neutral tone') };
      if (hStatus === 'FAILED' || negativeTone || gStatus === 'FAILED') return { tag: 'headwind', note: 'Miss or cautious tone' };
      if (positiveTone) return { tag: 'tailwind', note: 'Constructive tone post-print' };
      return { tag: 'neutral', note: 'Mixed post-earnings signals' };
    }
    if (state === 'pre_earnings') {
      return { tag: 'watching', note: 'Upcoming print — debates open' };
    }
    return { tag: 'neutral', note: 'No active earnings signal' };
  }

  // ── Signal 2: regime factor score (0..100) ─────────────────────────────
  function computeRegimeFactorScore(row, stats, factorWeights, passthroughTags, tagBoosts, alpha1m) {
    if (!factorWeights) return { score: 50, components: [] };

    // 1) Compute factor z-scores (relative to universe)
    const beta = num(row.beta);
    const fcfY = num(row.fcfYield);
    const opM = pctOf(row.operatingMargins);
    const revG = pctOf(row.revenueGrowth);
    const r40 = computeR40(row).value;
    const debtEq = num(row.debtEquity);
    const qualityScore = num(row.qualityScore);
    const m1 = num(row.m1);
    const mcap = num(row.marketCap);

    const factorZ = {
      beta:        zscore(beta, stats.beta.mean, stats.beta.std),
      size:        mcap != null && stats.marketCap.values.length
                     ? clamp(((50 - (percentileRank(stats.marketCap.values, mcap) || 50)) / 25), -2, 2)
                     : 0,
      value:       row._valueForGrowthZ != null ? row._valueForGrowthZ : 0,
      growth:      r40 != null && stats.revGrowth.mean != null
                     ? clamp((r40 - (stats.revGrowth.mean + (stats.operating.mean || 0))) / 25, -2, 2)
                     : 0,
      momentum:    m1 != null ? clamp(m1 / 10, -2, 2) : 0,
      quality:     qualityScore != null ? clamp((qualityScore - 50) / 15, -2, 2) : 0,
      leverage:    debtEq != null ? clamp((1 - debtEq) / 1, -2, 2) : 0,
      yield_carry: fcfY != null && stats.fcfYield.mean != null
                     ? clamp(zscore(fcfY, stats.fcfYield.mean, stats.fcfYield.std), -2, 2)
                     : 0,
    };

    // 2) Weighted sum from regime matrix
    const components = [];
    let weightedSum = 0;
    let weightAbs = 0;
    Object.keys(factorWeights).forEach(f => {
      const w = factorWeights[f];
      const z = factorZ[f] || 0;
      const contrib = w * z;
      weightedSum += contrib;
      weightAbs += Math.abs(w) * 2; // max contribution per factor = weight * 2 (clamped z)
      if (Math.abs(contrib) >= 0.15) {
        components.push({ factor: f, weight: w, z: parseFloat(z.toFixed(2)), contribution: parseFloat(contrib.toFixed(2)) });
      }
    });

    // 3) Apply pass-through tag boosts
    let tagDelta = 0;
    const tagsApplied = [];
    if (tagBoosts && Array.isArray(passthroughTags)) {
      for (const t of passthroughTags) {
        if (tagBoosts[t]) {
          tagDelta += tagBoosts[t] * 0.4; // each tag boost worth ~0.4 z-units
          tagsApplied.push({ tag: t, boost: tagBoosts[t] });
        }
      }
    }
    weightedSum += tagDelta;
    weightAbs += 2; // tag headroom

    // 4) Normalize to 0..100. Map [-weightAbs, +weightAbs] -> [0, 100]
    const normalized = weightAbs > 0
      ? Math.round(((weightedSum + weightAbs) / (2 * weightAbs)) * 100)
      : 50;

    return {
      score: clamp(normalized, 0, 100),
      components: components.sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)).slice(0, 4),
      tagsApplied,
      raw: parseFloat(weightedSum.toFixed(2)),
    };
  }

  // ── Pass-through tag resolution ────────────────────────────────────────
  function resolvePassthroughTags(row, sectorEtf, subsectorMap, sectorMap, ssm) {
    const subsector = row.subsector;
    if (subsector && ssm.SUBSECTOR_PASSTHROUGH[subsector]) return ssm.SUBSECTOR_PASSTHROUGH[subsector].slice();
    if (sectorEtf && ssm.SECTOR_PASSTHROUGH[sectorEtf]) return ssm.SECTOR_PASSTHROUGH[sectorEtf].slice();
    return [];
  }

  // ── Reason bullets (the user-facing explanation) ───────────────────────
  function buildReasonBullets(row, signals, regimeName, regimeFactorScore, tagLabels) {
    const bullets = [];

    // Bullet 1 — Valuation / Growth
    if (signals.valueForGrowth.percentile != null) {
      const p = signals.valueForGrowth.percentile;
      const det = signals.valueForGrowth.details;
      const r40Str = signals.r40.value != null ? ` R40 ${signals.r40.value >= 0 ? '+' : ''}${signals.r40.value.toFixed(0)}` : '';
      const verdict = p >= 70 ? 'cheap-for-growth' : p >= 50 ? 'fair-for-growth' : p >= 30 ? 'rich-for-growth' : 'expensive-for-growth';
      bullets.push(`${verdict} (${p}th pctile peers,${r40Str}; ${det.formula})`);
    } else if (signals.r40.value != null) {
      const r = signals.r40.value;
      const verdict = r >= 40 ? 'R40-positive' : r >= 20 ? 'sub-R40 (mid-cycle margin)' : 'R40-deficit';
      bullets.push(`${verdict}: ${r >= 0 ? '+' : ''}${r.toFixed(0)} (${signals.r40.formula})`);
    }

    // Bullet 2 — Sector-relative momentum
    if (signals.alpha1m != null) {
      const a = signals.alpha1m;
      const sec = row._sectorEtfName || row._sectorEtf || 'sector';
      const verb = a > 1 ? 'leading' : a < -1 ? 'lagging' : 'in-line vs';
      bullets.push(`${verb} ${sec} (1M alpha ${a >= 0 ? '+' : ''}${a.toFixed(1)}%)`);
    }

    // Bullet 3 — Earnings momentum
    const em = signals.earningsMomentum;
    if (em && em.tag !== 'neutral') {
      const label = ({
        tailwind:   'earnings tailwind',
        headwind:   'earnings headwind',
        inflecting: 'inflection confirmed',
        breaking:   'inflection broken',
        watching:   'pre-earnings setup',
      })[em.tag] || em.tag;
      bullets.push(`${label}: ${em.note}`);
    }

    // Bullet 4 — Regime fit (factor score + dominant component)
    if (regimeFactorScore && regimeFactorScore.components && regimeFactorScore.components.length) {
      const top = regimeFactorScore.components[0];
      const dir = top.contribution > 0 ? 'tailwind' : 'headwind';
      const fLabel = ({
        beta: 'beta', size: 'size', value: 'value', growth: 'growth-quality',
        momentum: 'momentum', quality: 'quality', leverage: 'leverage', yield_carry: 'FCF yield',
      })[top.factor] || top.factor;
      bullets.push(`${regimeName} regime score ${regimeFactorScore.score}/100 — ${fLabel} ${dir}`);
    } else if (regimeFactorScore) {
      bullets.push(`${regimeName} regime score ${regimeFactorScore.score}/100`);
    }

    // Bullet 5 (optional) — Pass-through tags
    if (regimeFactorScore && regimeFactorScore.tagsApplied && regimeFactorScore.tagsApplied.length) {
      const top = regimeFactorScore.tagsApplied
        .slice()
        .sort((a, b) => Math.abs(b.boost) - Math.abs(a.boost))[0];
      if (top) {
        const label = (tagLabels && tagLabels[top.tag]) || top.tag;
        bullets.push(`Tag fit: ${label} ${top.boost > 0 ? '(positive)' : '(headwind)'}`);
      }
    }

    return bullets.slice(0, 5);
  }

  // ── Top-level per-stock scorer ─────────────────────────────────────────
  function scoreStock(row, opts) {
    const stats = opts.stats;
    const sectorEtf = opts.sectorEtf;
    const sectorMonthPct = opts.sectorMonthPct;
    const regimeName = opts.regimeName;
    const factorWeights = opts.factorWeights;
    const tagBoosts = opts.tagBoosts;
    const intelEntry = opts.intelEntry;
    const peers = opts.peers || [];
    const passthroughTags = opts.passthroughTags || [];
    const tagLabels = opts.tagLabels || {};

    const alpha1m = computeAlpha1M(row, sectorMonthPct);
    const r40 = computeR40(row);
    const vfg = computeValueForGrowthPercentile(row, peers);
    if (vfg.percentile != null) {
      row._valueForGrowthZ = (vfg.percentile - 50) / 25;
    }
    const earningsMomentum = classifyEarningsMomentum(intelEntry);
    const regimeFactorScore = computeRegimeFactorScore(row, stats, factorWeights, passthroughTags, tagBoosts, alpha1m);

    const signals = { alpha1m, r40, valueForGrowth: vfg, earningsMomentum, passthroughTags };
    const reasonBullets = buildReasonBullets(row, signals, regimeName, regimeFactorScore, tagLabels);

    return {
      ticker: row.ticker,
      alpha1m,
      r40: r40.value,
      r40_formula: r40.formula,
      valueForGrowthPercentile: vfg.percentile,
      valueForGrowthDetails: vfg.details,
      earningsMomentumTag: earningsMomentum.tag,
      earningsMomentumNote: earningsMomentum.note,
      regimeFactorScore: regimeFactorScore.score,
      regimeComponents: regimeFactorScore.components,
      regimeTagsApplied: regimeFactorScore.tagsApplied,
      passthroughTags,
      reasonBullets,
    };
  }

  // ── Convenience: rank a list of rows and return top-N Own + Avoid ──────
  function rankIdeas(rows, opts) {
    const ssm = opts.regimeFactorsApi;
    const favored = new Set(opts.favoredSectors || []);
    const avoided = new Set(opts.avoidSectors || []);
    const sectorEtfFor = opts.sectorEtfFor; // fn(ticker)->ETF
    const sectorMonthFor = opts.sectorMonthFor; // fn(ETF)->1M%
    const subsectorMap = opts.subsectorMap || {};
    const intelMap = opts.intelMap || {};
    const factorWeights = ssm.REGIME_FACTOR_WEIGHTS[opts.regimeName] || ssm.REGIME_FACTOR_WEIGHTS['Transition'];
    const tagBoosts = ssm.REGIME_TAG_BOOSTS[opts.regimeName] || {};

    // Pre-compute universe stats once
    const stats = buildUniverseStats(rows);

    // Group peers by subsector for the value-for-growth percentile
    const peersBySubsector = {};
    for (const r of rows) {
      const s = r.subsector || '__unknown__';
      (peersBySubsector[s] = peersBySubsector[s] || []).push(r);
    }

    const scored = rows.map(row => {
      const etf = sectorEtfFor(row.ticker);
      row._sectorEtf = etf;
      row._sectorEtfName = opts.sectorEtfName ? opts.sectorEtfName(etf) : etf;
      const peers = peersBySubsector[row.subsector || '__unknown__'] || [];
      const passthroughTags = resolvePassthroughTags(row, etf, subsectorMap, opts.sectorMap, ssm);

      return Object.assign({ row }, scoreStock(row, {
        stats,
        sectorEtf: etf,
        sectorMonthPct: sectorMonthFor(etf),
        regimeName: opts.regimeName,
        factorWeights,
        tagBoosts,
        intelEntry: intelMap[row.ticker],
        peers,
        passthroughTags,
        tagLabels: ssm.TAG_LABELS,
      }));
    });

    // ── Own pool: favored sectors OR strong regime score, AND not bleeding
    //   - Earnings must not be in headwind/breaking (one bad print kills the
    //     long thesis until the next set-up).
    //   - Either sits in a favored sector OR has a regime score >= 70.
    const ownPool = scored.filter(s => {
      const etf = s.row._sectorEtf;
      const earningsOK = !['headwind', 'breaking'].includes(s.earningsMomentumTag);
      return earningsOK && (favored.has(etf) || s.regimeFactorScore >= 70);
    }).sort((a, b) => {
      // Composite rank: regime score (70%) + alpha1m (20%) + value-for-growth (10%)
      const rA = a.regimeFactorScore * 0.7 + (a.alpha1m || 0) * 2 + (a.valueForGrowthPercentile || 50) * 0.1;
      const rB = b.regimeFactorScore * 0.7 + (b.alpha1m || 0) * 2 + (b.valueForGrowthPercentile || 50) * 0.1;
      return rB - rA;
    });

    // ── Avoid pool: stricter gates so high-momentum names don't get caught
    // in the dragnet just because they live in a slightly out-of-favor sector.
    // Three tiers of inclusion:
    //   (a) inflection broken — always avoid
    //   (b) regime score < 30 — the model genuinely doesn't like it
    //   (c) avoid-sector resident, AND (regime score < 55 OR negative alpha)
    //   (d) earnings headwind AND regime score < 60 AND non-positive alpha
    const avoidPool = scored.filter(s => {
      const etf = s.row._sectorEtf;
      const tag = s.earningsMomentumTag;
      const alpha = s.alpha1m || 0;
      if (tag === 'breaking') return true;
      if (s.regimeFactorScore <= 30) return true;
      if (avoided.has(etf) && (s.regimeFactorScore < 55 || alpha <= 0)) return true;
      if (tag === 'headwind' && s.regimeFactorScore < 60 && alpha <= 0) return true;
      return false;
    }).sort((a, b) => {
      const headwindPenA = ['headwind', 'breaking'].includes(a.earningsMomentumTag) ? -15 : 0;
      const headwindPenB = ['headwind', 'breaking'].includes(b.earningsMomentumTag) ? -15 : 0;
      const rA = a.regimeFactorScore - (a.alpha1m || 0) * 2 + headwindPenA;
      const rB = b.regimeFactorScore - (b.alpha1m || 0) * 2 + headwindPenB;
      return rA - rB;
    });

    return { own: ownPool.slice(0, 10), avoid: avoidPool.slice(0, 10), scored };
  }

  const api = {
    scoreStock,
    rankIdeas,
    buildUniverseStats,
    computeR40,
    computeAlpha1M,
    computeRegimeFactorScore,
    valueForGrowthScore,
    classifyEarningsMomentum,
    resolvePassthroughTags,
    buildReasonBullets,
  };

  if (typeof window !== 'undefined') window.IdeaEngine = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
