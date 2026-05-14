/* ===== REGIME_FACTORS.JS — Factor weight matrix per macro regime =========
 *
 * Single source of truth for how each macro regime values the six fundamental
 * factors that drive ranking in idea_engine.js and the Macro tab's Own/Avoid
 * cards. Weights are integers in [-2, +2] for readability and edit safety.
 *
 *  +2 = strong tailwind for regime         (factor is hugely additive)
 *  +1 = tailwind
 *   0 = neutral / regime-agnostic
 *  -1 = headwind
 *  -2 = strong headwind
 *
 * Factor definitions (consumed by idea_engine.computeStockFactors):
 *   beta:        Stock beta (high beta benefits from risk-on regimes)
 *   size:        Market cap percentile inverted — small = +1 size bias
 *   value:       Value-for-growth percentile (low EV/Sales adj for growth = +)
 *   growth:      Rule of 40 = revenue_growth + fcf_margin (or op_margin)
 *   momentum:    1M total return (in %)
 *   quality:     qualityScore from scores.js — earnings stability, returns,
 *                leverage, dividend coverage
 *   leverage:    Inverted debt/equity proxy (lower leverage = +)
 *   yield_carry: FCF yield (or earnings yield fallback)
 *
 * Regime catalog matches classifyRegime() in refresh_macro.mjs:
 *   Goldilocks, Reflation, Stagflation, Restrictive, Risk-Off, Transition
 *
 * To rebalance a regime, just edit the integers below. No other file needs
 * to change — idea_engine.js dynamically reads this object.
 * ========================================================================= */
(function () {
  'use strict';

  const REGIME_FACTOR_WEIGHTS = {
    // Growth accelerating + inflation muted + accommodative policy
    'Goldilocks': {
      beta:        +1,
      size:         0,   // mega-caps lead Goldilocks just as often
      value:        0,
      growth:      +2,   // R40 is king when growth is funded cheap
      momentum:    +2,   // trend-following works in Goldilocks
      quality:     +1,
      leverage:     0,
      yield_carry: -1,   // cash returns punished vs reinvestment
    },

    // Growth + inflation up; rates rising; commodities lead
    'Reflation': {
      beta:        +2,
      size:        +1,   // small caps usually outperform in reflation
      value:       +1,
      growth:       0,   // growth still works but no premium
      momentum:    +1,
      quality:      0,
      leverage:    +1,   // levered cyclicals re-rate
      yield_carry:  0,
    },

    // Slowing growth + inflation sticky — the toughest tape
    'Stagflation': {
      beta:        -1,
      size:        -1,   // large caps + brand pricing power win
      value:       +1,
      growth:      -1,
      momentum:    +1,   // momentum still helpful, narrow leadership
      quality:     +2,   // pricing-power compounders
      leverage:    -2,   // levered names get repriced hard
      yield_carry: +1,
    },

    // Tight policy choking liquidity; multiples compress
    'Restrictive': {
      beta:        -1,
      size:        -1,
      value:       +1,
      growth:      -1,   // duration penalty on long-dated FCF
      momentum:     0,
      quality:     +2,
      leverage:    -2,
      yield_carry: +2,   // FCF yield bites hardest when bonds compete
    },

    // VIX > 25, credit widening, broad de-risking
    'Risk-Off': {
      beta:        -2,
      size:        -1,
      value:       +1,
      growth:      -1,
      momentum:    -1,
      quality:     +2,
      leverage:    -2,
      yield_carry: +1,
    },

    // Mixed signals — let factor neutrality reign, lean quality
    'Transition': {
      beta:         0,
      size:         0,
      value:        0,
      growth:       0,
      momentum:     0,
      quality:     +1,
      leverage:     0,
      yield_carry:  0,
    },
  };

  // Macro pass-through tags — categorical labels we attach to each stock so
  // the regime engine and reason text can speak idiosyncratically.
  // Mapping is keyed by SUBSECTOR (from utils.js SUBSECTOR_MAP) — falls back
  // to sector ETF if subsector is unknown.
  const SUBSECTOR_PASSTHROUGH = {
    // ── Energy ────────────────────────────────────────────────────────────
    'Oil & Gas E&P':       ['oil-beneficiary', 'commodity-linked', 'pricing-power'],
    'Oil & Gas Integrated': ['oil-beneficiary', 'commodity-linked'],
    'Oil Services':         ['oil-beneficiary', 'cyclical'],
    'Refining':             ['oil-beneficiary', 'cyclical'],
    'Pipelines':            ['oil-beneficiary', 'rate-sensitive', 'yield-vehicle'],

    // ── Materials & Industrials ──────────────────────────────────────────
    'Copper Mining':        ['commodity-linked', 'cyclical', 'china-sensitive'],
    'Gold Mining':          ['inflation-hedge', 'commodity-linked', 'USD-inverse'],
    'Fertilizers':          ['commodity-linked', 'agriculture-cycle', 'food-inflation'],
    'Chemicals':            ['cyclical', 'commodity-input', 'global-trade'],
    'Steel':                ['cyclical', 'commodity-linked', 'tariff-sensitive'],
    'Industrial Machinery': ['cyclical', 'capex-cycle'],
    'Aerospace & Defense':  ['defense-spend', 'late-cycle', 'cycle-resilient'],
    'Building Products':    ['cyclical', 'housing-sensitive', 'rate-sensitive'],
    'Trucking':             ['cyclical', 'oil-cost-sensitive', 'consumer-proxy'],

    // ── Financials ────────────────────────────────────────────────────────
    'Money Center Banks':   ['rate-beneficiary', 'credit-cycle', 'yield-curve-steepener'],
    'Regional Banks':       ['rate-beneficiary', 'credit-cycle', 'CRE-exposure'],
    'Insurance':            ['rate-beneficiary', 'underwriting-cycle'],
    'Capital Markets':      ['risk-on-beneficiary', 'cyclical', 'fee-income'],
    'Asset Managers':       ['risk-on-beneficiary', 'AUM-fee-sensitive'],

    // ── Tech / Software ──────────────────────────────────────────────────
    'Application Software':       ['duration-long', 'rate-sensitive', 'AI-beneficiary'],
    'Infrastructure Software':    ['duration-long', 'rate-sensitive', 'enterprise-IT'],
    'Cybersecurity':              ['duration-long', 'AI-beneficiary', 'secular-growth'],
    'Data Analytics':             ['duration-long', 'AI-beneficiary', 'enterprise-IT'],
    'Cloud Infrastructure':       ['duration-long', 'AI-beneficiary', 'capex-heavy'],
    'Vertical Software':          ['duration-long', 'rate-sensitive', 'recurring-rev'],
    'IT Services':                ['enterprise-IT', 'labor-arbitrage', 'cycle-resilient'],

    // ── Semis & Hardware ──────────────────────────────────────────────────
    'Semiconductors':             ['AI-beneficiary', 'cyclical', 'cycle-leading'],
    'Semi Equipment':             ['AI-beneficiary', 'capex-cycle', 'china-sensitive'],
    'Hardware':                   ['cyclical', 'consumer-spend-sensitive'],
    'Networking Equipment':       ['AI-beneficiary', 'enterprise-IT'],

    // ── Internet & Media ──────────────────────────────────────────────────
    'Internet Search & Ads':      ['ad-spend-sensitive', 'duration-long', 'AI-beneficiary'],
    'Internet Retail':            ['consumer-spend-sensitive', 'cyclical', 'logistics-cost'],
    'Streaming Media':            ['subscription-resilience', 'content-spend-cycle'],
    'Social Media':               ['ad-spend-sensitive', 'consumer-spend-sensitive'],
    'Gaming':                     ['consumer-spend-sensitive', 'discretionary'],

    // ── Consumer ──────────────────────────────────────────────────────────
    'Mass Retail':                ['consumer-spend-sensitive', 'inflation-pass-through'],
    'Specialty Retail':           ['consumer-spend-sensitive', 'cyclical', 'discretionary'],
    'Restaurants':                ['consumer-spend-sensitive', 'labor-cost-sensitive', 'food-inflation'],
    'Packaged Food':              ['food-inflation', 'pricing-power', 'cycle-resilient'],
    'Household Products':         ['pricing-power', 'cycle-resilient'],
    'Tobacco':                    ['yield-vehicle', 'pricing-power', 'cycle-resilient'],
    'Beverages':                  ['pricing-power', 'cycle-resilient'],
    'Apparel & Luxury':           ['consumer-spend-sensitive', 'discretionary', 'china-sensitive'],

    // ── Healthcare ────────────────────────────────────────────────────────
    'Pharma':                     ['cycle-resilient', 'IP-cliff-risk', 'FX-sensitive'],
    'Biotech':                    ['rate-sensitive', 'binary-trial-risk', 'duration-long'],
    'Medical Devices':            ['cycle-resilient', 'procedure-volume'],
    'Healthcare Services':        ['cycle-resilient', 'policy-sensitive'],
    'Managed Care':               ['cycle-resilient', 'policy-sensitive', 'medical-cost-trend'],
    'Healthcare Distribution':    ['cycle-resilient', 'thin-margin'],
    'Life Sciences Tools':        ['biotech-funding-cycle', 'enterprise-spend'],

    // ── Real Estate / Utilities ──────────────────────────────────────────
    'REITs':                      ['rate-sensitive', 'yield-vehicle', 'duration-long'],
    'Utilities':                  ['rate-sensitive', 'yield-vehicle', 'regulated'],
    'Independent Power':          ['rate-sensitive', 'commodity-linked', 'AI-beneficiary'],

    // ── Comms / Telecom ──────────────────────────────────────────────────
    'Telecom':                    ['yield-vehicle', 'rate-sensitive', 'capex-heavy'],

    // ── Fintech / Payments ────────────────────────────────────────────────
    'Payments':                   ['consumer-spend-sensitive', 'cross-border-FX', 'rate-sensitive'],
    'Consumer Finance':           ['rate-sensitive', 'credit-cycle', 'consumer-spend-sensitive'],
  };

  // Sector ETF -> default tags when subsector unknown
  const SECTOR_PASSTHROUGH = {
    'XLE': ['oil-beneficiary', 'commodity-linked', 'cyclical'],
    'XLB': ['commodity-linked', 'cyclical'],
    'XLI': ['cyclical', 'capex-cycle'],
    'XLF': ['rate-beneficiary', 'credit-cycle'],
    'XLK': ['duration-long', 'rate-sensitive'],
    'XLC': ['ad-spend-sensitive', 'duration-long'],
    'XLY': ['consumer-spend-sensitive', 'cyclical', 'discretionary'],
    'XLP': ['pricing-power', 'cycle-resilient'],
    'XLV': ['cycle-resilient'],
    'XLU': ['rate-sensitive', 'yield-vehicle', 'regulated'],
    'XLRE': ['rate-sensitive', 'yield-vehicle', 'duration-long'],
  };

  // Regime tag boosts — pass-through tags that get explicit credit in the
  // ranking model under specific regimes. Used by idea_engine.applyRegimeTagBoost
  const REGIME_TAG_BOOSTS = {
    'Goldilocks':  { 'AI-beneficiary': +2, 'secular-growth': +1, 'duration-long': +1, 'rate-sensitive': -1 },
    'Reflation':   { 'oil-beneficiary': +2, 'commodity-linked': +2, 'cyclical': +1, 'rate-beneficiary': +2, 'inflation-hedge': +1, 'duration-long': -1, 'rate-sensitive': -1, 'yield-vehicle': -1 },
    'Stagflation': { 'oil-beneficiary': +2, 'inflation-hedge': +2, 'pricing-power': +2, 'commodity-linked': +1, 'food-inflation': +1, 'cycle-resilient': +1, 'duration-long': -2, 'discretionary': -2, 'rate-sensitive': -1 },
    'Restrictive': { 'cycle-resilient': +1, 'pricing-power': +1, 'yield-vehicle': +1, 'duration-long': -2, 'rate-sensitive': -2, 'capex-heavy': -1, 'binary-trial-risk': -1 },
    'Risk-Off':    { 'cycle-resilient': +2, 'pricing-power': +1, 'yield-vehicle': +1, 'inflation-hedge': +1, 'cyclical': -2, 'discretionary': -2, 'duration-long': -1, 'binary-trial-risk': -2, 'consumer-spend-sensitive': -1 },
    'Transition':  { 'cycle-resilient': +1, 'pricing-power': +1, 'binary-trial-risk': -1 },
  };

  // Human-readable factor labels (used by reason text and screener UI)
  const FACTOR_LABELS = {
    beta:        'Beta',
    size:        'Size (small-cap tilt)',
    value:       'Value (cheap on adj. multiple)',
    growth:      'Growth quality (R40)',
    momentum:    'Momentum',
    quality:     'Quality',
    leverage:    'Low leverage',
    yield_carry: 'FCF yield',
  };

  // Friendly descriptions for pass-through tags shown in tooltips and reasons
  const TAG_LABELS = {
    'oil-beneficiary':         'oil-linked',
    'commodity-linked':        'commodity-linked',
    'pricing-power':           'pricing power',
    'cyclical':                'cyclical',
    'cycle-resilient':         'cycle-resilient',
    'rate-beneficiary':        'benefits from higher rates',
    'rate-sensitive':          'rate-sensitive',
    'duration-long':           'long-duration cash flows',
    'AI-beneficiary':          'AI tailwind',
    'secular-growth':          'secular growth',
    'capex-heavy':             'capex-heavy',
    'capex-cycle':             'capex-cycle',
    'consumer-spend-sensitive':'consumer-spend exposed',
    'ad-spend-sensitive':      'ad-spend exposed',
    'discretionary':           'discretionary spend',
    'food-inflation':          'food-inflation linked',
    'inflation-hedge':         'inflation hedge',
    'yield-vehicle':           'yield vehicle',
    'china-sensitive':         'China-sensitive',
    'tariff-sensitive':        'tariff-sensitive',
    'binary-trial-risk':       'binary trial risk',
    'biotech-funding-cycle':   'biotech funding cycle',
    'medical-cost-trend':      'medical-cost-trend exposed',
    'policy-sensitive':        'policy-sensitive',
    'IP-cliff-risk':           'IP-cliff risk',
    'enterprise-IT':           'enterprise IT spend',
    'enterprise-spend':        'enterprise spend',
    'subscription-resilience': 'subscription resilience',
    'thin-margin':             'thin-margin',
    'labor-cost-sensitive':    'labor cost sensitive',
    'labor-arbitrage':         'labor arbitrage',
    'logistics-cost':          'logistics cost sensitive',
    'cross-border-FX':         'FX-sensitive',
    'FX-sensitive':            'FX-sensitive',
    'USD-inverse':             'inverse to USD',
    'credit-cycle':            'credit-cycle exposed',
    'CRE-exposure':            'CRE-exposed',
    'underwriting-cycle':      'underwriting-cycle exposed',
    'fee-income':              'fee-income driven',
    'risk-on-beneficiary':     'risk-on beneficiary',
    'AUM-fee-sensitive':       'AUM-fee sensitive',
    'agriculture-cycle':       'ag-cycle exposed',
    'commodity-input':         'commodity cost input',
    'housing-sensitive':       'housing-sensitive',
    'global-trade':            'global-trade exposed',
    'cycle-leading':           'cycle-leading',
    'regulated':               'rate-regulated',
    'recurring-rev':           'recurring revenue model',
    'defense-spend':           'defense-spend linked',
    'late-cycle':              'late-cycle',
    'oil-cost-sensitive':      'oil-cost sensitive',
    'consumer-proxy':          'consumer-proxy',
    'yield-curve-steepener':   'yield-curve steepener',
    'inflation-pass-through':  'inflation pass-through',
    'china-sensitive':         'China-sensitive',
    'content-spend-cycle':     'content-spend cycle',
    'procedure-volume':        'procedure-volume driven',
  };

  // Export to global so it's consumable by both browser and Node (refresh_macro.mjs)
  const api = {
    REGIME_FACTOR_WEIGHTS,
    REGIME_TAG_BOOSTS,
    SUBSECTOR_PASSTHROUGH,
    SECTOR_PASSTHROUGH,
    FACTOR_LABELS,
    TAG_LABELS,
  };

  if (typeof window !== 'undefined') window.RegimeFactors = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
