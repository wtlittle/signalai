/* help-content.js
 * Centralized explainer copy for the SignalAI dashboard. Every (?) icon
 * rendered by sig-help.js looks up its content by key from this map.
 *
 * Content shape:
 *   {
 *     title:     "Short headline" (~3-6 words)
 *     oneLiner:  "One sentence describing the purpose."
 *     howToRead: "How to interpret what you see, what to watch for, weighting,
 *                 caveats." Can be 1-3 sentences.
 *     learnMore: optional href for deeper docs
 *   }
 *
 * Keys are namespaced by surface or concept: compare.scorecard.growth,
 * pill.northstar.topline, column.debate, macro.regime, screener.filter.value,
 * earnings.reaction, etc. Keep keys short and stable; UI references them by name.
 */
(function (global) {
  'use strict';

  var HELP = {
    // -------------------------------------------------------------
    // COMPARE TAB — top-level
    // -------------------------------------------------------------
    'compare.surface': {
      title: 'Compare',
      oneLiner: 'Stack 2-4 names side by side across valuation, growth, quality, positioning, and earnings setup.',
      howToRead: 'Pick names from the search bar at top (or check them on the watchlist). Each sub-tab on the left answers a different question. Best-in-row cells are highlighted green when applicable.'
    },
    'compare.tab.overview': {
      title: 'Overview',
      oneLiner: 'At-a-glance hero cards plus an indexed return chart for the chosen names.',
      howToRead: 'Use this to anchor on size, valuation, growth, FCF margin, and distance from highs. The chart is rebased to 100 at the start of the window. Toggle the benchmark to see relative strength vs the broader market.'
    },
    'compare.tab.trends': {
      title: 'Trends',
      oneLiner: 'Performance across multiple windows plus 6-quarter trend sparklines on the core fundamental drivers.',
      howToRead: 'Sparklines show direction, not absolute levels. Watch for inflections — accelerating revenue with widening FCF margin is the classic compounder signal.'
    },
    'compare.tab.fundamentals': {
      title: 'Fundamentals',
      oneLiner: 'Dense grouped table covering market, valuation, growth, profitability, and earnings expectations.',
      howToRead: 'Each row highlights the winning name. Forward estimates use the same north-star metric the company guides on (ARR for CRWD, cRPO for CRM, revenue for everyone else).'
    },
    'compare.tab.scorecard': {
      title: 'Scorecard',
      oneLiner: '1-5 score per pillar (peer-relative) plus an absolute tier vs software/internet thresholds.',
      howToRead: 'Pillars: Growth 30%, Quality 25%, Profitability 20%, Valuation 15%, Momentum 10%. NTM EPS growth >15% adds +0.5 to the raw Growth score (capped at 5). Weighted composite at the bottom.'
    },
    'compare.tab.radar': {
      title: 'Radar',
      oneLiner: 'Radar chart of the 5 scorecard pillars plus a winner-by-category strip.',
      howToRead: 'Bigger area = stronger overall. Toggle peer-relative vs absolute to see whether a leader is winning the cohort or winning the market.'
    },
    'compare.tab.earnings': {
      title: 'Earnings Intel',
      oneLiner: 'Most recent post-earnings reaction, bottom-line head-to-head, and the bull/bear/base shorthand from the analyst notes.',
      howToRead: 'The beat-rate strip shows X of last N quarters beat. Green at >=67%, yellow 40-66%, red <40%. Reactions are 1-day price move from the print.'
    },
    'compare.tab.airead': {
      title: 'AI Read',
      oneLiner: 'Live Perplexity-generated read on the cohort, with one accordion card per ticker (Bull / Bear / Watch).',
      howToRead: 'First card expands by default. Click "Refresh" to regenerate; results are cached locally so revisits are free. Use the clipboard/paste-back path if you want a deep-research workflow instead.'
    },

    // -------------------------------------------------------------
    // COMPARE TAB — suggestion baskets
    // -------------------------------------------------------------
    'compare.basket.movers': {
      title: 'Movers today',
      oneLiner: 'Tickers moving the most today.',
      howToRead: 'Useful for catching post-print, pre-print, or news-driven action across your watchlist. Ranked by absolute 1-day move.'
    },
    'compare.basket.debated': {
      title: 'Highly debated peers',
      oneLiner: 'Highest debate-score names where investor opinion is most split.',
      howToRead: 'Best ground for compare-and-contrast work. Debate score blends analyst dispersion, short interest, earnings-reaction volatility, and multiple compression.'
    },
    'compare.basket.favored': {
      title: 'Favored sectors',
      oneLiner: 'Subsectors the macro panel is currently flagging as favored.',
      howToRead: 'Pulled from the live macro factor tilt. Representative names from each favored sector are surfaced as a starting cohort.'
    },
    'compare.basket.unfavored': {
      title: 'Unfavored sectors',
      oneLiner: 'Subsectors flagged as unfavored by the macro panel.',
      howToRead: 'Useful for paired short ideas or risk monitoring. Mirrors the macro panel avoid list.'
    },
    'compare.basket.high-quant-debate': {
      title: 'High quant + high debate',
      oneLiner: 'Names in the top quartile on BOTH alpha/quant score and debate score.',
      howToRead: 'Good asymmetric setups: high underlying quality paired with high disagreement. Falls back to a both-above-60 cut when the universe is small.'
    },
    'compare.basket.pre-earnings': {
      title: 'Pre-earnings setup',
      oneLiner: 'Reporting this week with elevated debate.',
      howToRead: 'Often the highest-payoff compare slots. Filters the earnings calendar to names with a debate score of 60 or higher.'
    },
    'compare.basket.ma-rumors': {
      title: 'M&A rumor candidates',
      oneLiner: 'Names with pending M&A rumor flags from the rumor scan job.',
      howToRead: 'Compare to peers or rumored acquirers for context. Each chip shows the rumored buyer and the scan confidence; ranked by confidence.'
    },

    // -------------------------------------------------------------
    // PROPRIETARY PILLS
    // -------------------------------------------------------------
    'pill.northstar.topline': {
      title: 'Top-line north star',
      oneLiner: 'The growth metric the company actually guides on each quarter (not always revenue).',
      howToRead: 'ARR for CRWD/PANW/SNOW, cRPO for CRM, billings for NET, revenue for the rest. The delta basis tells you what the new guide is being compared against — prior FY actual, consensus, or prior guide.'
    },
    'pill.northstar.bottomline': {
      title: 'Bottom-line north star',
      oneLiner: 'The profit metric investors actually track for the name (FCF, adj op income, or EPS).',
      howToRead: 'FCF for CRWD/PANW, adj op income for CRM/SNOW/VEEV/DOCU, EBITDA for some, EPS as fallback. Helps avoid the trap of judging a software name by GAAP EPS when the buyside scorecards on FCF margin.'
    },
    'pill.reaction': {
      title: 'Post-earnings reaction',
      oneLiner: '1-day price move from the most recent earnings print.',
      howToRead: 'Color follows direction. Falls back through EOD -> intraday -> pre-market -> after-hours -> Finnhub if any source is missing. A blank pill means the print happened but no usable price data could be sourced.'
    },
    'pill.debate': {
      title: 'Debate score',
      oneLiner: 'How contested the name is among the buy-side — higher means more disagreement on direction.',
      howToRead: 'Driven by dispersion in sell-side targets, recent revisions, and short interest. Higher debate names tend to react more violently to prints; weight them more heavily when stress-testing your book.'
    },
    'pill.nextq': {
      title: 'Next-quarter guide',
      oneLiner: 'Management guidance for the upcoming quarter on the north-star metric, in % growth terms.',
      howToRead: 'Comparison basis ("vs consensus" / "vs prior guide" / "vs prior Q+1 guide") shows the implied bar. Beats start here.'
    },

    // -------------------------------------------------------------
    // WATCHLIST COLUMN HEADERS
    // -------------------------------------------------------------
    'column.quality': {
      title: 'Quality',
      oneLiner: 'Fundamental quality 0-100 — composite of FCF, growth, margins, balance sheet, and beat rate.',
      howToRead: 'Higher = more durable compounder profile. Pair with Debate: high quality + high debate is the classic buy-side setup where the controversy is about pace, not viability.'
    },
    'column.debate': {
      title: 'Debate (col)',
      oneLiner: 'Per-name debate score (1-100) — higher = more buy-side disagreement.',
      howToRead: 'Sort descending to surface the most contested names; these tend to have the largest earnings reactions. Built from target dispersion, short interest, earnings vol, and multiple compression.'
    },
    'column.fy_guide': {
      title: 'FY guide vs consensus',
      oneLiner: '% gap between the latest FY guidance and the consensus estimate on the north-star metric.',
      howToRead: 'Positive = guide above consensus (bullish). Hovers near zero mean the print itself will move the stock; wide gaps in either direction are pre-priced.'
    },
    'column.reaction': {
      title: 'Reaction (col)',
      oneLiner: '1-day price reaction to the most recent earnings print.',
      howToRead: 'Use alongside the FY guide column: a positive reaction with a guide-below-consensus print usually signals strong qualitative commentary or buyback announcement.'
    },
    'column.results_vs_consensus': {
      title: 'Q results vs consensus',
      oneLiner: '% beat/miss vs consensus on the reported quarter (top-line and bottom-line).',
      howToRead: 'Big beats with weak reactions usually mean the guide disappointed. Big misses with positive reactions usually mean management raised the FY despite a Q miss.'
    },
    'column.subsector': {
      title: 'Subsector',
      oneLiner: 'Auto-classified subsector tag from the daily refresh job.',
      howToRead: 'Filter or group by subsector to see peer dynamics. Subsectors are software-flavored — analytics, security, observability, dev tools, etc.'
    },

    // -------------------------------------------------------------
    // MACRO TAB
    // -------------------------------------------------------------
    'macro.regime': {
      title: 'Regime score',
      oneLiner: 'Composite gauge of growth-vs-defensive market regime.',
      howToRead: '>+30 = clear risk-on regime, <-30 = clear risk-off. Drives the per-ticker tilt scoring on the right side of this panel. Changes day-to-day are noise; trend over 5-10 sessions is signal.'
    },
    'macro.factor': {
      title: 'Factor signals',
      oneLiner: 'Per-factor read (rates, credit, breadth, dollar, vol) feeding the regime composite.',
      howToRead: 'Each tile shows the latest reading and a directional arrow. Conflicting signals (rates up + dollar down) are the most informative; aligned signals just reinforce the regime score.'
    },
    'macro.tilt': {
      title: 'Per-ticker macro tilt',
      oneLiner: 'How well-aligned each watchlist name is with the current regime.',
      howToRead: 'Tilt > 0 means the name should benefit from the prevailing regime. Combine with debate score to find pre-print setups where consensus and macro both agree (lower variance) vs disagree (lottery tickets).'
    },

    // -------------------------------------------------------------
    // SCREENER
    // -------------------------------------------------------------
    'screener.filter.value': {
      title: 'Value filter',
      oneLiner: 'Combined valuation tier vs growth (lower = cheaper relative to its growth).',
      howToRead: 'Percentile against the universe. Set Max to 30 to surface names in the bottom-third of growth-adjusted multiples.'
    },
    'screener.filter.earnings_momentum': {
      title: 'Earnings momentum',
      oneLiner: 'Trailing 4 quarters of beat/raise consistency.',
      howToRead: 'Tier A = beat-and-raised the last 4Q in a row. Tier C = mixed or missed. Pair with high debate to find under-appreciated compounders.'
    },
    'screener.filter.alpha': {
      title: 'Alpha vs subsector ETF',
      oneLiner: '1-month return minus the subsector ETF return.',
      howToRead: 'Negative alpha during a hot tape often means catalyst exhaustion or a fundamental break. Positive alpha during a weak tape is the classic relative-strength setup.'
    },
    'screener.filter.pass_through': {
      title: 'Pass-through tag',
      oneLiner: 'Which preset basket(s) the name currently passes (debate-heavy, regime-tilted, etc.).',
      howToRead: 'Use to confirm a screen result comes from a basket you trust, not a single-filter accident.'
    },

    // -------------------------------------------------------------
    // EARNINGS INTEL
    // -------------------------------------------------------------
    'earnings.beat_strip': {
      title: 'Beat strip',
      oneLiner: 'X of last N quarters that beat consensus on the top-line.',
      howToRead: 'Color: green >=67%, yellow 40-66%, red <40%. A red strip with strong reactions usually means low expectations being cleared.'
    },
    'earnings.guidance_basis': {
      title: 'Guidance basis',
      oneLiner: 'What the headline % delta is being compared against.',
      howToRead: '"vs consensus" is the most informative for short-term stock reaction. "vs prior FY actual" shows true growth. "vs prior guide" shows whether management is raising the bar.'
    },
    'earnings.ai_summary': {
      title: 'AI earnings read',
      oneLiner: 'Perplexity-generated synthesis of the call, IR commentary, and post-print analyst notes.',
      howToRead: 'Bull and Bear are the strongest cases each side could make; Watch is the asymmetric factor to monitor. Not a recommendation.'
    },

    // -------------------------------------------------------------
    // GENERIC / SHARED
    // -------------------------------------------------------------
    'shared.tabs': {
      title: 'Tab navigation',
      oneLiner: 'Each tab is a focused workflow.',
      howToRead: 'Coverage = your watchlist. Compare = 2-4 head-to-head. Screener = filter the universe. Macro = regime & factor read. Drilldown = single-name deep dive.'
    }
  };

  global.SignalHelp = {
    get: function (key) { return HELP[key] || null; },
    has: function (key) { return Object.prototype.hasOwnProperty.call(HELP, key); },
    keys: function () { return Object.keys(HELP); },
    // Register or override at runtime (useful for hot-iteration on copy).
    set: function (key, value) { HELP[key] = value; }
  };

})(window);
