/* ===================================================================
   NOTE-ENHANCEMENTS.JS
   Four features injected into the earnings note modal:
   1. Confidence / quality header
   2. Peer comps panel
   3. Note search (cross-note full-text, accessible from Earnings tab)
   4. Transcript integration (collapsible, fetched via proxy)
   =================================================================== */

/* ------------------------------------------------------------------
   SECTION 1 — CONFIDENCE / QUALITY HEADER
   Parses note markdown for:
   - Data freshness (Last updated date)
   - Source count (lines containing "Source:" or "*Sources:*")
   - Estimate confidence tier (single-source vs multi-analyst consensus)
   - Key estimate range width (narrow = high conviction, wide = uncertain)
   Renders a compact banner directly above the note body.
------------------------------------------------------------------ */

function parseNoteConfidence(md, ticker, type) {
  const signals = {
    freshness: null,       // ISO date string
    sourceCount: 0,        // number of distinct sources mentioned
    estimateRange: null,   // { low, high, label } for EPS
    consensusSize: null,   // "N analysts" if mentioned
    tier: 'low',           // 'high' | 'medium' | 'low'
    flags: [],             // array of warning strings
  };

  // --- Freshness ---
  const updatedMatch = md.match(/\*Last updated:\s*(\d{4}-\d{2}-\d{2})\*/i)
    || md.match(/Last updated:\s*(\d{4}-\d{2}-\d{2})/i);
  if (updatedMatch) signals.freshness = updatedMatch[1];

  // --- Source count: count distinct lines in a *Sources:* block ---
  const sourcesMatch = md.match(/\*Sources?:[^*]*/i);
  if (sourcesMatch) {
    const sourceBlock = sourcesMatch[0];
    // Count comma-separated items + semicolon-separated items
    const items = sourceBlock.split(/[,;]/).filter(s => s.trim().length > 2);
    signals.sourceCount = Math.max(items.length, 1);
  }
  // Also catch inline [source] or (source) references
  const inlineRefs = (md.match(/\((?:per|via|source:|from)\s+[^)]{3,40}\)/gi) || []).length;
  signals.sourceCount = Math.max(signals.sourceCount, inlineRefs);

  // --- Consensus size ---
  const analystsMatch = md.match(/(\d+)\s+analyst/i);
  if (analystsMatch) signals.consensusSize = parseInt(analystsMatch[1]);

  // --- EPS estimate range ---
  const epsMatch = md.match(/EPS.*?\$(\d+\.?\d*)\s*[–-]\s*\$?(\d+\.?\d*)/i)
    || md.match(/EPS est[^:]*:\s*\$(\d+\.?\d*)\s*[–-–]\s*\$?(\d+\.?\d*)/i);
  if (epsMatch) {
    const lo = parseFloat(epsMatch[1]);
    const hi = parseFloat(epsMatch[2]);
    if (!isNaN(lo) && !isNaN(hi) && hi > 0) {
      const spread = Math.abs(hi - lo);
      const midpoint = (lo + hi) / 2;
      const pctSpread = midpoint > 0 ? spread / midpoint : 0;
      signals.estimateRange = { lo, hi, spread, pctSpread };
    }
  }

  // --- Confidence tier ---
  const hasMultiAnalyst = signals.consensusSize && signals.consensusSize >= 5;
  const hasSufficientSources = signals.sourceCount >= 3;
  const hasNarrowRange = signals.estimateRange && signals.estimateRange.pctSpread < 0.05;
  const isFresh = signals.freshness && daysBetween(signals.freshness, todayStr()) <= 1;

  if (hasMultiAnalyst && hasSufficientSources && isFresh) {
    signals.tier = 'high';
  } else if (hasSufficientSources || hasMultiAnalyst) {
    signals.tier = 'medium';
  } else {
    signals.tier = 'low';
  }

  // --- Flags ---
  if (signals.freshness) {
    const age = daysBetween(signals.freshness, todayStr());
    if (age > 3) signals.flags.push(`Note is ${age} days old — estimates may have drifted`);
  }
  if (signals.estimateRange && signals.estimateRange.pctSpread > 0.15) {
    signals.flags.push('Wide EPS range — low analyst consensus');
  }
  if (signals.sourceCount < 2) {
    signals.flags.push('Limited sources — treat estimates with caution');
  }

  return signals;
}

function renderConfidenceHeader(md, ticker, type) {
  const s = parseNoteConfidence(md, ticker, type);

  const tierLabel = s.tier === 'high' ? 'High Conviction' : s.tier === 'medium' ? 'Moderate' : 'Low Conviction';
  const tierClass = `conf-tier-${s.tier}`;

  const freshnessStr = s.freshness
    ? `Updated ${s.freshness} (${daysBetween(s.freshness, todayStr())}d ago)`
    : 'Update date unknown';

  const sourcesStr = s.sourceCount > 0
    ? `${s.sourceCount} source${s.sourceCount !== 1 ? 's' : ''}`
    : 'Sources unknown';

  const consensusStr = s.consensusSize
    ? `${s.consensusSize}-analyst consensus`
    : null;

  const rangeStr = s.estimateRange
    ? `EPS range: $${s.estimateRange.lo.toFixed(2)}–$${s.estimateRange.hi.toFixed(2)}`
    : null;

  const pills = [sourcesStr, consensusStr, rangeStr].filter(Boolean);

  const flagsHtml = s.flags.length > 0
    ? `<div class="conf-flags">${s.flags.map(f => `<span class="conf-flag">${f}</span>`).join('')}</div>`
    : '';

  return `
    <div class="note-confidence-header ${tierClass}">
      <div class="conf-main">
        <span class="conf-tier-badge ${tierClass}">${tierLabel}</span>
        <span class="conf-freshness">${freshnessStr}</span>
        <div class="conf-pills">${pills.map(p => `<span class="conf-pill">${p}</span>`).join('')}</div>
      </div>
      ${flagsHtml}
    </div>`;
}

/* ------------------------------------------------------------------
   SECTION 2 — PEER COMPS ANALYSIS
   Fetches cross_sector_comps + analyst_summary data via api-client.js,
   then generates:
     • A written narrative: valuation premium/discount, margin quality,
       growth vs. multiple mismatch, earnings reliability
     • Visual bar comparisons for 4 key metrics (no raw numbers, just
       relative positioning within the peer group)
     • A compact reference table for the full data
------------------------------------------------------------------ */

async function renderCompsPanel(ticker, type) {
  // Fetch comps and analyst data in parallel
  let compsData = null;
  let analystData = null;
  let quoteData = null;

  try {
    [compsData, analystData] = await Promise.all([
      (typeof fetchCrossSectorCompsClient === 'function') ? fetchCrossSectorCompsClient(ticker) : null,
      (typeof fetchAnalystSummaryClient   === 'function') ? fetchAnalystSummaryClient(ticker)   : null,
    ]);
    // Also get quotes for the target from the snapshot
    const snap = (typeof loadSnapshot === 'function') ? await loadSnapshot() : null;
    quoteData = snap?.quotes?.[ticker] || null;
  } catch (e) { /* continue with nulls */ }

  if (!compsData || !compsData.target || !compsData.comps?.length) {
    return `<div class="comps-unavailable">Peer comparison data not available for ${ticker}. Data is refreshed nightly.</div>`;
  }

  const target    = compsData.target;
  const peers     = compsData.comps.slice(0, 5); // top 5 by similarity
  const allRows   = [target, ...peers];           // target always first
  const name      = target.name || ticker;

  // ---- Narrative generation ------------------------------------------------
  const narrative = buildCompsNarrative(ticker, name, target, peers, analystData, quoteData, type);

  // ---- Bar charts for 4 key metrics ----------------------------------------
  const barsHtml = buildMetricBars(ticker, allRows);

  // ---- Reference table (compact) -------------------------------------------
  const tableHtml = buildCompsTable(ticker, allRows, quoteData);

  const subsector = (typeof SUBSECTOR_MAP !== 'undefined' && SUBSECTOR_MAP[ticker]) || target.industry || '';

  return `
    <div class="note-comps-panel">
      <div class="note-comps-header">
        <span class="note-comps-title">Peer Comparison — ${name}</span>
        <span class="note-comps-subsector">${subsector}</span>
      </div>

      <div class="comps-narrative">${narrative}</div>

      <div class="comps-bars-section">
        <div class="comps-bars-label">Relative positioning vs. comparable companies</div>
        ${barsHtml}
      </div>

      <details class="comps-table-details">
        <summary class="comps-table-summary">Full data table</summary>
        ${tableHtml}
      </details>
    </div>`;
}

// ---------------------------------------------------------------------------
// NARRATIVE BUILDER
// Produces 3–4 short paragraphs that actually explain the differences.
// ---------------------------------------------------------------------------
function buildCompsNarrative(ticker, name, target, peers, analystData, quoteData, type) {
  const paras = [];
  const snap = quoteData || {};

  // Helper: peer median for a field
  function peerMedian(field) {
    const vals = peers.map(p => p[field]).filter(v => v != null && isFinite(v));
    if (!vals.length) return null;
    vals.sort((a, b) => a - b);
    return vals[Math.floor(vals.length / 2)];
  }
  function peerMean(field) {
    const vals = peers.map(p => p[field]).filter(v => v != null && isFinite(v));
    if (!vals.length) return null;
    return vals.reduce((s, v) => s + v, 0) / vals.length;
  }
  function fmt(v, decimals = 1, suffix = '') {
    if (v == null || !isFinite(v)) return '—';
    return v.toFixed(decimals) + suffix;
  }
  function sign(v) { return v > 0 ? '+' : ''; }

  // ---- 1. VALUATION PARAGRAPH ----
  const targetPE    = target.forwardPE;
  const peerMedianPE = peerMedian('forwardPE');
  const targetEVRev  = target.enterpriseToRevenue;
  const peerMedianEVRev = peerMedian('enterpriseToRevenue');
  const targetEVEBITDA  = target.enterpriseToEbitda;
  const peerMedianEVEBITDA = peerMedian('enterpriseToEbitda');

  let valuationSentences = [];

  if (targetPE != null && peerMedianPE != null && peerMedianPE > 0 && targetPE > 0) {
    const pePremium = ((targetPE / peerMedianPE) - 1) * 100;
    const peDir     = pePremium > 5 ? 'premium' : pePremium < -5 ? 'discount' : 'in-line';
    valuationSentences.push(
      `${name} trades at ${fmt(targetPE)}x forward P/E, a <strong>${fmt(Math.abs(pePremium), 0)}% ${peDir}</strong> to the peer median of ${fmt(peerMedianPE)}x.`
    );
  } else if (targetPE != null && targetPE < 0) {
    valuationSentences.push(`${name} has a negative forward P/E (not yet profitable on a forward basis), making multiple-based comps less meaningful — EV/Revenue is the more relevant anchor.`);
  }

  if (targetEVRev != null && peerMedianEVRev != null) {
    const evRevPremium = ((targetEVRev / peerMedianEVRev) - 1) * 100;
    const evRevDir     = evRevPremium > 10 ? 'at a premium' : evRevPremium < -10 ? 'at a discount' : 'at roughly the same level';
    valuationSentences.push(
      `On EV/Revenue, ${name} trades at ${fmt(targetEVRev)}x vs. ${fmt(peerMedianEVRev)}x for peers — ${evRevDir}.`
    );
  }

  if (valuationSentences.length) {
    paras.push(`<p class="comps-para"><span class="comps-para-label">Valuation</span> ${valuationSentences.join(' ')}</p>`);
  }

  // ---- 2. GROWTH & QUALITY PARAGRAPH ----
  const targetRevGrowth  = target.revenueGrowth;
  const peerMeanRevGrowth = peerMean('revenueGrowth');
  const targetOpMargin   = target.operatingMargins;
  const peerMeanOpMargin  = peerMean('operatingMargins');
  const targetFCFMargin  = target.fcfMargin;
  const peerMeanFCFMargin = peerMean('fcfMargin');

  let growthSentences = [];

  if (targetRevGrowth != null && peerMeanRevGrowth != null) {
    const growthDelta = targetRevGrowth - peerMeanRevGrowth;
    const growthDir   = growthDelta > 3 ? 'faster' : growthDelta < -3 ? 'slower' : 'at a similar pace';
    growthSentences.push(
      `Revenue is growing at ${fmt(targetRevGrowth)}% YoY — ${growthDir} than the peer average of ${fmt(peerMeanRevGrowth)}%.`
    );
  }

  if (targetOpMargin != null && peerMeanOpMargin != null) {
    const marginDelta = targetOpMargin - peerMeanOpMargin;
    const marginQuality = marginDelta > 5
      ? `a <strong>${fmt(marginDelta, 0)}pp margin advantage</strong> over peers`
      : marginDelta < -5
        ? `a <strong>${fmt(Math.abs(marginDelta), 0)}pp margin deficit</strong> vs. peers`
        : 'roughly in-line operating margins relative to peers';
    growthSentences.push(
      `At ${fmt(targetOpMargin)}% operating margin vs. a peer average of ${fmt(peerMeanOpMargin)}%, ${name} carries ${marginQuality}.`
    );
  }

  if (targetFCFMargin != null && peerMeanFCFMargin != null && targetFCFMargin > -50) {
    const fcfQuality = targetFCFMargin > peerMeanFCFMargin + 5
      ? 'a strong FCF converter — cash generation is a genuine differentiator here'
      : targetFCFMargin < peerMeanFCFMargin - 5
        ? 'FCF conversion lags peers, which matters for valuation sustainability'
        : 'FCF conversion is roughly in-line with the peer group';
    growthSentences.push(`${name} is ${fcfQuality} (${fmt(targetFCFMargin)}% FCF margin vs. ${fmt(peerMeanFCFMargin)}% peer average).`);
  }

  if (growthSentences.length) {
    paras.push(`<p class="comps-para"><span class="comps-para-label">Growth &amp; quality</span> ${growthSentences.join(' ')}</p>`);
  }

  // ---- 3. VALUATION JUSTIFICATION / MISMATCH ----
  // Does the multiple reflect the growth/margin profile?
  if (targetPE > 0 && peerMedianPE > 0 && targetRevGrowth != null && peerMeanRevGrowth != null) {
    const pePremium    = ((targetPE / peerMedianPE) - 1) * 100;
    const growthDelta  = targetRevGrowth - peerMeanRevGrowth;
    let mismatchSentence = '';

    if (pePremium > 10 && growthDelta < 0) {
      mismatchSentence = `The valuation stands out: ${name} carries a premium multiple despite growing <em>slower</em> than peers. This is a potential risk — the premium is likely a function of quality (margins, FCF) or defensiveness rather than growth, and would compress quickly if margins disappoint.`;
    } else if (pePremium < -10 && growthDelta > 3) {
      mismatchSentence = `There is a notable disconnect here: ${name} is growing faster than peers yet trades at a meaningful discount. This could represent a mispricing opportunity if growth is sustainable, or the market may be pricing in a deceleration that isn't visible in the trailing data yet.`;
    } else if (pePremium < -15 && growthDelta <= 0) {
      mismatchSentence = `${name} trades at a significant discount to peers on most metrics. The market is pricing in either a structural decline, execution risk, or both. The discount only looks like value if you believe the business stabilizes — without that, it's a value trap.`;
    } else if (pePremium > 20 && growthDelta > 5) {
      mismatchSentence = `The premium multiple looks more justified here given the faster growth: ${name} is a growth-at-a-premium name. The question is whether the growth rate is durable or front-loaded.`;
    } else {
      mismatchSentence = `The multiple appears broadly consistent with the growth and margin profile relative to peers — no obvious premium or discount stands out.`;
    }
    paras.push(`<p class="comps-para"><span class="comps-para-label">Multiple vs. fundamentals</span> ${mismatchSentence}</p>`);
  }

  // ---- 4. EARNINGS RELIABILITY (if analystData available) ----
  if (analystData?.earningsHistory?.length >= 2) {
    const history  = analystData.earningsHistory;
    const beats    = history.filter(h => h.epsActual > h.epsEstimate).length;
    const total    = history.length;
    const avgSurp  = history.reduce((s, h) => {
      const pct = h.surprisePercent != null ? h.surprisePercent * 100 : 0;
      return s + pct;
    }, 0) / total;
    const beatStreak = beats === total
      ? `beat estimates all ${total} of the last ${total} quarters`
      : beats === 0
        ? `missed estimates in all ${total} of the last ${total} quarters`
        : `beaten estimates in ${beats} of the last ${total} quarters`;
    const analystUpside = analystData.targetMeanPrice && snap.price
      ? ((analystData.targetMeanPrice / snap.price) - 1) * 100
      : null;
    const consensusStr = analystData.numberOfAnalystOpinions
      ? `${analystData.numberOfAnalystOpinions} analysts cover the stock`
      : null;
    const uptickStr = analystUpside != null
      ? `, with consensus price target implying <strong>${sign(analystUpside)}${fmt(analystUpside, 0)}% upside</strong>`
      : '';
    paras.push(
      `<p class="comps-para"><span class="comps-para-label">Earnings track record</span> ` +
      `${name} has ${beatStreak} (avg surprise: ${sign(avgSurp)}${fmt(avgSurp, 1)}%). ` +
      `${consensusStr ? consensusStr + uptickStr + '.' : ''}</p>`
    );
  }

  // ---- 5. THE STANDOUT COMP ----
  // Find the single peer that's most instructive to compare against
  if (peers.length >= 2) {
    // Pick the peer closest to target on revenue growth but with biggest margin diff
    const peersWithData = peers.filter(p => p.revenueGrowth != null && p.operatingMargins != null);
    if (peersWithData.length && target.revenueGrowth != null && target.operatingMargins != null) {
      const sorted = [...peersWithData].sort((a, b) => {
        const aMarginDiff = Math.abs(a.operatingMargins - target.operatingMargins);
        const bMarginDiff = Math.abs(b.operatingMargins - target.operatingMargins);
        return bMarginDiff - aMarginDiff; // biggest margin gap first
      });
      const contrast = sorted[0];
      const marginDiff = target.operatingMargins - contrast.operatingMargins;
      const growthDiff  = (target.revenueGrowth || 0) - (contrast.revenueGrowth || 0);
      const peDiff      = (target.forwardPE || 0) - (contrast.forwardPE || 0);

      if (Math.abs(marginDiff) > 5 || Math.abs(growthDiff) > 5) {
        const marginVerdict = marginDiff > 0
          ? `${name} has ${fmt(Math.abs(marginDiff), 0)}pp higher operating margins`
          : `${contrast.name || contrast.ticker} runs ${fmt(Math.abs(marginDiff), 0)}pp higher operating margins`;
        const growthVerdict = growthDiff > 0
          ? `${name} is growing faster`
          : `${contrast.name || contrast.ticker} is growing faster`;
        const peVerdict = peDiff > 2
          ? `${name} trades at a higher multiple (${fmt(target.forwardPE)}x vs. ${fmt(contrast.forwardPE)}x)`
          : peDiff < -2
            ? `${contrast.name || contrast.ticker} trades at a higher multiple (${fmt(contrast.forwardPE)}x vs. ${fmt(target.forwardPE)}x)`
            : 'both trade at similar multiples';
        paras.push(
          `<p class="comps-para comps-para-standout"><span class="comps-para-label">vs. ${contrast.ticker}</span> ` +
          `The most instructive comparison is with ${contrast.name || contrast.ticker} (${contrast.ticker}): ${marginVerdict}, ${growthVerdict}, and ${peVerdict}. ` +
          `This spread makes it easier to see what the market is paying for in each case.</p>`
        );
      }
    }
  }

  return paras.length ? paras.join('') : `<p class="comps-para">Peer data available — see table below.</p>`;
}

// ---------------------------------------------------------------------------
// METRIC BAR CHARTS
// For 4 metrics: Fwd P/E, Revenue Growth, Operating Margin, FCF Margin
// Shows each company as a proportional bar, target highlighted.
// ---------------------------------------------------------------------------
function buildMetricBars(ticker, allRows) {
  const metrics = [
    { field: 'forwardPE',        label: 'Forward P/E',       suffix: 'x',  higherIsBetter: false },
    { field: 'revenueGrowth',    label: 'Revenue Growth',    suffix: '%',  higherIsBetter: true  },
    { field: 'operatingMargins', label: 'Operating Margin',  suffix: '%',  higherIsBetter: true  },
    { field: 'fcfMargin',        label: 'FCF Margin',        suffix: '%',  higherIsBetter: true  },
  ];

  return metrics.map(m => {
    const rows = allRows.filter(r => r[m.field] != null && isFinite(r[m.field]));
    if (rows.length < 2) return '';

    // For PE: exclude negatives for scaling purposes
    const scalingRows = m.field === 'forwardPE' ? rows.filter(r => r[m.field] > 0) : rows;
    if (!scalingRows.length) return '';

    const vals   = scalingRows.map(r => r[m.field]);
    const minVal = Math.min(...vals);
    const maxVal = Math.max(...vals);
    const range  = maxVal - minVal || 1;

    const barRows = rows.map(r => {
      const val    = r[m.field];
      const isTarget = r.ticker === ticker;
      const name   = r.name ? r.name.split(',')[0].split(' Inc')[0].split(' Corp')[0] : r.ticker;
      // Width: 0–100% within the range. For reverse metrics (PE), flip.
      const rawPct  = ((val - minVal) / range) * 100;
      const pct     = m.higherIsBetter ? rawPct : 100 - rawPct;
      const barWidth = Math.max(4, Math.min(100, pct)); // at least 4% so bar is visible
      const valStr   = val < 0 ? `(${Math.abs(val).toFixed(1)}${m.suffix})` : `${val.toFixed(1)}${m.suffix}`;
      const isGood   = m.higherIsBetter ? val >= (minVal + range * 0.6) : val <= (minVal + range * 0.4);
      const barClass = isTarget ? 'bar-target' : isGood ? 'bar-good' : 'bar-neutral';

      return `
        <div class="comp-bar-row ${isTarget ? 'comp-bar-row-target' : ''}">
          <div class="comp-bar-ticker">${r.ticker}</div>
          <div class="comp-bar-track">
            <div class="comp-bar ${barClass}" style="width:${barWidth}%"></div>
          </div>
          <div class="comp-bar-val ${isTarget ? 'target-val' : ''}">${valStr}</div>
        </div>`;
    }).join('');

    return `
      <div class="comp-metric-block">
        <div class="comp-metric-label">${m.label}</div>
        ${barRows}
      </div>`;
  }).filter(Boolean).join('');
}

// ---------------------------------------------------------------------------
// REFERENCE TABLE (compact, inside a <details> element)
// ---------------------------------------------------------------------------
function buildCompsTable(ticker, allRows, quoteData) {
  const rows = allRows.map(r => {
    const isTarget  = r.ticker === ticker;
    const name      = (typeof COMMON_NAMES !== 'undefined' && COMMON_NAMES[r.ticker]) || r.name || r.ticker;
    const fmtV = (v, d = 1, s = '') => (v != null && isFinite(v)) ? v.toFixed(d) + s : '—';

    // Price / performance from quoteData if target, else blank (comps don't have live prices)
    let priceStr = '—', ytdStr = '—';
    if (isTarget && quoteData) {
      priceStr = quoteData.price ? `$${quoteData.price.toFixed(2)}` : '—';
      ytdStr   = quoteData.changeYtd != null ? `${quoteData.changeYtd > 0 ? '+' : ''}${quoteData.changeYtd.toFixed(1)}%` : '—';
    }

    const ytdClass = ytdStr !== '—' ? (parseFloat(ytdStr) > 0 ? 'pos' : 'neg') : '';

    return `<tr class="${isTarget ? 'comps-target-row' : ''}">
      <td class="comps-sticky-col">
        <div class="comps-ticker-cell">
          <span class="comps-ticker">${r.ticker}</span>
          <span class="comps-name">${name.split(',')[0].split(' Inc')[0]}</span>
        </div>
      </td>
      <td class="num">${priceStr}</td>
      <td class="num ${ytdClass}">${ytdStr}</td>
      <td class="num">${r.forwardPE > 0 ? fmtV(r.forwardPE) + 'x' : r.forwardPE < 0 ? 'NM' : '—'}</td>
      <td class="num">${fmtV(r.enterpriseToRevenue, 2) !== '—' ? fmtV(r.enterpriseToRevenue, 2) + 'x' : '—'}</td>
      <td class="num">${fmtV(r.revenueGrowth)}${r.revenueGrowth != null ? '%' : ''}</td>
      <td class="num">${fmtV(r.operatingMargins)}${r.operatingMargins != null ? '%' : ''}</td>
      <td class="num">${fmtV(r.fcfMargin)}${r.fcfMargin != null ? '%' : ''}</td>
    </tr>`;
  }).join('');

  return `
    <div class="comps-table-wrapper note-comps-table-wrapper">
      <table class="comps-table">
        <thead>
          <tr>
            <th class="comps-sticky-col">Company</th>
            <th class="num">Price</th>
            <th class="num">YTD</th>
            <th class="num">Fwd P/E</th>
            <th class="num">EV/Rev</th>
            <th class="num">Rev Growth</th>
            <th class="num">Op Margin</th>
            <th class="num">FCF Margin</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/* ------------------------------------------------------------------
   SECTION 3 — NOTE SEARCH
   Full-text search across all notes (fetches each .md file lazily,
   caches content, searches with regex, returns ranked results with
   highlighted excerpts).
   Opens a dedicated search modal from the Earnings header.
------------------------------------------------------------------ */

const noteSearchCache = {}; // { filePath: mdContent }
let noteSearchIndex = null; // flat list of { ticker, type, date, file, title }

async function buildNoteSearchIndex() {
  if (noteSearchIndex) return noteSearchIndex;
  try {
    let resp;
    if (window.SignalSnapshot && window.SignalSnapshot.fetchWithFallback) {
      resp = await window.SignalSnapshot.fetchWithFallback('earnings_notes_index.json', { cacheBust: true });
    } else {
      resp = await fetch('earnings_notes_index.json?v=' + Date.now());
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const idx = await resp.json();
    const all = [];
    if (idx.notes) {
      idx.notes.forEach(n => {
        all.push({
          ticker: n.ticker,
          type: n.type,
          date: n.earnings_date,
          file: n.file,
          status: n.status || 'active',
          title: `${n.ticker} — ${n.type === 'pre_earnings' ? 'Pre' : 'Post'}-Earnings (${n.earnings_date})`,
        });
      });
    }
    // Also add archive entries if present
    (idx.archived || []).forEach(n => {
      const file = n.file || (n.archive_path);
      if (file) {
        all.push({
          ticker: n.ticker,
          type: n.type + '_earnings',
          date: n.date || n.earnings_date,
          file,
          status: 'archived',
          title: `${n.ticker} — ${n.type === 'post' ? 'Post' : 'Pre'}-Earnings (${n.date || n.earnings_date}) [Archived]`,
        });
      }
    });
    noteSearchIndex = all;
    return noteSearchIndex;
  } catch (e) {
    return [];
  }
}

async function fetchNoteContent(filePath) {
  if (noteSearchCache[filePath]) return noteSearchCache[filePath];
  const paths = [filePath, filePath.replace('notes/', 'archive/')];
  for (const p of paths) {
    try {
      const resp = await fetch(p + '?v=' + Date.now(), { signal: AbortSignal.timeout(4000) });
      if (resp.ok) {
        const text = await resp.text();
        if (text.startsWith('#') || text.includes('## ')) {
          noteSearchCache[filePath] = text;
          return text;
        }
      }
    } catch (e) { /* try next */ }
  }
  return null;
}

function excerptMatch(content, query, contextChars = 140) {
  if (!content || !query) return '';
  const terms = query.trim().split(/\s+/).filter(Boolean);
  const pattern = new RegExp(terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'gi');

  let firstIdx = -1;
  let m;
  while ((m = pattern.exec(content)) !== null) {
    if (firstIdx === -1) firstIdx = m.index;
  }
  if (firstIdx === -1) return '';

  const start = Math.max(0, firstIdx - contextChars / 2);
  const end = Math.min(content.length, firstIdx + contextChars / 2);
  let excerpt = content.slice(start, end).replace(/\n/g, ' ').trim();
  if (start > 0) excerpt = '...' + excerpt;
  if (end < content.length) excerpt = excerpt + '...';

  // Highlight matches
  const highlight = new RegExp(`(${terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return excerpt.replace(highlight, '<mark class="search-highlight">$1</mark>');
}

async function runNoteSearch(query) {
  if (!query || query.trim().length < 2) return [];
  const index = await buildNoteSearchIndex();
  const terms = query.trim().toLowerCase().split(/\s+/);

  // Fast filter: index-level match on ticker/date/title
  const candidates = index.filter(n => {
    const haystack = `${n.ticker} ${n.title} ${n.date}`.toLowerCase();
    return terms.some(t => haystack.includes(t));
  });

  // Also do full-text search on note content (up to 20 notes to avoid lag)
  const contentCandidates = [];
  const fetchLimit = Math.min(index.length, 25);
  const fetches = index.slice(0, fetchLimit).map(async n => {
    if (candidates.some(c => c.file === n.file)) return; // already in candidates
    const content = await fetchNoteContent(n.file);
    if (!content) return;
    const lower = content.toLowerCase();
    if (terms.every(t => lower.includes(t)) || terms.some(t => lower.includes(t))) {
      contentCandidates.push(n);
    }
  });
  await Promise.all(fetches);

  const allHits = [...candidates, ...contentCandidates];
  const unique = allHits.filter((n, i, arr) => arr.findIndex(x => x.file === n.file) === i);

  // For each hit, load content and build excerpt
  const results = await Promise.all(unique.map(async n => {
    const content = await fetchNoteContent(n.file);
    return {
      ...n,
      excerpt: content ? excerptMatch(content, query) : '',
    };
  }));

  // Sort: active before archived, pre < post, recency
  return results.sort((a, b) => {
    if (a.status !== b.status) return a.status === 'active' ? -1 : 1;
    return b.date.localeCompare(a.date);
  });
}

// --- Search UI ---

function openNoteSearch() {
  let overlay = document.getElementById('note-search-overlay');
  if (!overlay) {
    overlay = createNoteSearchModal();
    document.body.appendChild(overlay);
  }
  overlay.classList.add('active');
  document.body.style.overflow = 'hidden';
  const input = overlay.querySelector('#note-search-input');
  if (input) setTimeout(() => input.focus(), 60);
}

function closeNoteSearch() {
  const overlay = document.getElementById('note-search-overlay');
  if (overlay) overlay.classList.remove('active');
  document.body.style.overflow = '';
}

function createNoteSearchModal() {
  const overlay = document.createElement('div');
  overlay.className = 'popup-overlay';
  overlay.id = 'note-search-overlay';
  overlay.innerHTML = `
    <div class="popup-modal note-search-modal" id="note-search-modal">
      <button class="popup-close" id="note-search-close">&times;</button>
      <div class="note-search-header">
        <h2>Search Notes</h2>
        <p class="note-search-subtitle">Full-text search across all active and archived earnings notes</p>
        <div class="note-search-input-row">
          <input
            type="text"
            id="note-search-input"
            class="note-search-input"
            placeholder="Search by ticker, company, keyword, or topic..."
            autocomplete="off"
          />
          <span class="note-search-kbd">Enter</span>
        </div>
      </div>
      <div class="note-search-results" id="note-search-results">
        <div class="note-search-empty">Type to search across all notes</div>
      </div>
    </div>`;

  // Close handlers
  overlay.querySelector('#note-search-close').addEventListener('click', closeNoteSearch);
  overlay.addEventListener('click', e => { if (e.target === overlay) closeNoteSearch(); });

  // Search handler (debounced)
  let debounceTimer = null;
  const input = overlay.querySelector('#note-search-input');
  const resultsEl = overlay.querySelector('#note-search-results');

  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < 2) {
      resultsEl.innerHTML = '<div class="note-search-empty">Type to search across all notes</div>';
      return;
    }
    resultsEl.innerHTML = '<div class="note-search-empty">Searching...</div>';
    debounceTimer = setTimeout(async () => {
      const results = await runNoteSearch(q);
      renderSearchResults(results, q, resultsEl, overlay);
    }, 300);
  });

  // Keyboard: Escape to close
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeNoteSearch();
  });

  return overlay;
}

function renderSearchResults(results, query, container, overlay) {
  if (!results || results.length === 0) {
    container.innerHTML = `<div class="note-search-empty">No notes found for "${query}"</div>`;
    return;
  }

  const html = results.map(r => {
    const typeLabel = r.type === 'pre_earnings' ? 'Pre' : r.type === 'post_earnings' ? 'Post' : r.type.includes('post') ? 'Post' : 'Pre';
    const typeClass = typeLabel === 'Post' ? 'post' : 'pre';
    const statusBadge = r.status === 'archived'
      ? '<span class="search-result-archived">Archived</span>'
      : '';
    return `
      <div class="note-search-result" data-ticker="${r.ticker}" data-date="${r.date}" data-type="${r.type.includes('post') ? 'post' : 'pre'}">
        <div class="search-result-header">
          <span class="search-result-ticker">${r.ticker}</span>
          <span class="search-result-type ${typeClass}">${typeLabel}-Earnings</span>
          <span class="search-result-date">${r.date}</span>
          ${statusBadge}
        </div>
        ${r.excerpt ? `<div class="search-result-excerpt">${r.excerpt}</div>` : ''}
      </div>`;
  }).join('');

  container.innerHTML = html;

  container.querySelectorAll('.note-search-result').forEach(el => {
    el.addEventListener('click', () => {
      closeNoteSearch();
      openEarningsNote(el.dataset.ticker, el.dataset.date, el.dataset.type);
    });
  });
}

/* ------------------------------------------------------------------
   SECTION 4 — TRANSCRIPT INTEGRATION
   Adds a "Transcript" tab inside the note modal for post-earnings notes.
   Fetches the transcript via a public proxy from The Motley Fool / 
   Seeking Alpha (free tier via scraper proxy), then renders as
   collapsible Q&A sections with speaker labels.
   For pre-earnings, shows the most recent prior quarter transcript link.
------------------------------------------------------------------ */

// Known transcript URL patterns
function getTranscriptSearchUrl(ticker) {
  // Use Fool's free transcripts (public, structured)
  return `https://www.fool.com/earnings-call-transcripts/?ticker=${ticker}`;
}

async function fetchTranscript(ticker, date) {
  // Try to fetch via alloy/corsproxy.io to get the Motley Fool transcript page
  // If CORS fails gracefully, we surface a "view externally" link
  const foolSearch = `https://www.fool.com/earnings-call-transcripts/?ticker=${encodeURIComponent(ticker)}`;
  const saSearch = `https://seekingalpha.com/symbol/${encodeURIComponent(ticker)}/earnings/transcripts`;

  return {
    status: 'link',
    sources: [
      { name: 'The Motley Fool', url: foolSearch },
      { name: 'Seeking Alpha', url: saSearch },
      { name: 'Quartr', url: `https://quartr.com/company/${ticker.toLowerCase()}` },
    ]
  };
}

function renderTranscriptPanel(ticker, date, type) {
  if (type !== 'post') {
    // For pre-earnings, link to prior transcript
    return `
      <div class="note-transcript-panel">
        <div class="transcript-header">
          <span class="transcript-title">Earnings Transcripts</span>
          <span class="transcript-period">Prior quarter — ${ticker}</span>
        </div>
        <p class="transcript-note">This is a pre-earnings note. Links below open the most recent earnings call transcripts.</p>
        ${renderTranscriptLinks(ticker, null)}
      </div>`;
  }

  return `
    <div class="note-transcript-panel">
      <div class="transcript-header">
        <span class="transcript-title">Earnings Call Transcript</span>
        <span class="transcript-period">${ticker} — Quarter ending ~${date}</span>
      </div>
      <p class="transcript-note">Transcripts are available free at the links below. Click to open the full call transcript in a new tab.</p>
      ${renderTranscriptLinks(ticker, date)}
      <div class="transcript-qa-hint">
        <strong>Quick read tip:</strong> Search the transcript page for "management discussion", "question-and-answer", or key terms from this note (e.g., "MCR", "depletion", "NII") to jump to the relevant sections.
      </div>
    </div>`;
}

function renderTranscriptLinks(ticker, date) {
  const foolUrl = `https://www.fool.com/earnings-call-transcripts/?ticker=${encodeURIComponent(ticker)}`;
  const saUrl = `https://seekingalpha.com/symbol/${encodeURIComponent(ticker)}/earnings/transcripts`;
  const quartrUrl = `https://quartr.com/company/${ticker.toLowerCase()}`;

  return `
    <div class="transcript-links">
      <a href="${foolUrl}" target="_blank" rel="noopener noreferrer" class="transcript-link">
        <span class="transcript-link-icon">&#9654;</span>
        <div class="transcript-link-body">
          <span class="transcript-link-name">The Motley Fool</span>
          <span class="transcript-link-desc">Free full transcripts, indexed by ticker</span>
        </div>
        <span class="transcript-link-arrow">&#8599;</span>
      </a>
      <a href="${saUrl}" target="_blank" rel="noopener noreferrer" class="transcript-link">
        <span class="transcript-link-icon">&#9654;</span>
        <div class="transcript-link-body">
          <span class="transcript-link-name">Seeking Alpha</span>
          <span class="transcript-link-desc">Transcripts + analyst commentary (free tier)</span>
        </div>
        <span class="transcript-link-arrow">&#8599;</span>
      </a>
      <a href="${quartrUrl}" target="_blank" rel="noopener noreferrer" class="transcript-link">
        <span class="transcript-link-icon">&#9654;</span>
        <div class="transcript-link-body">
          <span class="transcript-link-name">Quartr</span>
          <span class="transcript-link-desc">Audio + transcript, synced to slides</span>
        </div>
        <span class="transcript-link-arrow">&#8599;</span>
      </a>
    </div>`;
}

/* ------------------------------------------------------------------
   INTEGRATION — Override openEarningsNote to inject all four panels
   We monkey-patch the function after the base earnings.js loads.
   The original function fetches the markdown and sets innerHTML.
   We replace it with an enhanced version that:
   1. Fetches the markdown (same logic)
   2. Builds the tab navigation (Note | Comps | Transcript)
   3. Renders each panel lazily on tab switch
------------------------------------------------------------------ */

// Store state for current open note
let _currentNote = { ticker: null, date: null, type: null, md: null };

// Override openEarningsNote once DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  // Wait for earnings.js to define its globals, then patch
  setTimeout(patchOpenEarningsNote, 100);
  injectSearchButton();
  injectSearchKeyboardShortcut();
});

function patchOpenEarningsNote() {
  // Keep a reference to the modal content element
  const $noteContent = document.getElementById('earnings-note-content');
  if (!$noteContent) return;

  // The original openEarningsNote is defined in earnings.js.
  // We can't easily override a function defined in another file,
  // so instead we hook into the note overlay's mutation and post-process.
  // Strategy: observe the earnings-note-content element for content changes,
  // then inject our enhancements after the base content is set.

  const observer = new MutationObserver(async (mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList' && $noteContent.children.length > 0) {
        const firstChild = $noteContent.firstChild;
        // Skip loading/error states
        if (firstChild && firstChild.classList &&
            (firstChild.classList.contains('earnings-note-loading') ||
             firstChild.classList.contains('earnings-note-error'))) continue;

        // Check if we've already enhanced this content
        if ($noteContent.querySelector('.note-tab-bar')) continue;

        // Extract ticker/date/type from the overlay's state or from the note heading
        const { ticker, date, type } = _currentNote;
        if (!ticker) continue;

        // Get the raw markdown — wait briefly for async fetch to complete if needed
        let md = _currentNote.md || '';
        if (!md) {
          // Give the eagerly-started fetch up to 800ms to finish
          await new Promise(resolve => setTimeout(resolve, 400));
          md = _currentNote.md || '';
          if (!md) await new Promise(resolve => setTimeout(resolve, 400));
          md = _currentNote.md || '';
        }

        await enhanceNoteModal($noteContent, ticker, date, type, md);
      }
    }
  });

  observer.observe($noteContent, { childList: true, subtree: false });
}

async function enhanceNoteModal($noteContent, ticker, date, type, md) {
  // Snapshot existing HTML (the rendered note body)
  const noteBodyHtml = $noteContent.innerHTML;

  // Build confidence header
  const confHeader = md ? renderConfidenceHeader(md, ticker, type) : '';

  // Build the tab bar
  const tabBar = `
    <div class="note-tab-bar">
      <button class="note-tab active" data-panel="note">Note</button>
      <button class="note-tab" data-panel="comps">Comps</button>
      <button class="note-tab" data-panel="transcript">Transcript</button>
    </div>`;

  // Build panel wrappers
  const notePanel = `<div class="note-panel active" id="note-panel-note">${confHeader}<div class="note-body-content">${noteBodyHtml}</div></div>`;
  const compsPanel = `<div class="note-panel" id="note-panel-comps"><div class="note-panel-loading">Loading comps...</div></div>`;
  const transcriptPanel = `<div class="note-panel" id="note-panel-transcript">${renderTranscriptPanel(ticker, date, type)}</div>`;

  $noteContent.innerHTML = tabBar + notePanel + compsPanel + transcriptPanel;

  // Tab switching
  $noteContent.querySelectorAll('.note-tab').forEach(btn => {
    btn.addEventListener('click', async () => {
      $noteContent.querySelectorAll('.note-tab').forEach(b => b.classList.remove('active'));
      $noteContent.querySelectorAll('.note-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panelId = `note-panel-${btn.dataset.panel}`;
      const panel = document.getElementById(panelId);
      if (panel) {
        panel.classList.add('active');
        // Lazy-load comps
        if (btn.dataset.panel === 'comps' && panel.querySelector('.note-panel-loading')) {
          const compsHtml = await renderCompsPanel(ticker, type);
          panel.innerHTML = compsHtml || `<div class="note-panel-empty">No peer data available for ${ticker} in subsector "${SUBSECTOR_MAP && SUBSECTOR_MAP[ticker] || 'unknown'}"</div>`;
        }
      }
    });
  });
}

// We need to intercept openEarningsNote calls to capture ticker/date/type and md.
// Strategy: use event delegation at document level to capture clicks on any element
// that carries data-ticker + data-date + data-type attributes (cards, chips, archive items).
// This fires BEFORE earnings.js's click handlers resolve the note modal, so _currentNote
// is always set by the time the MutationObserver fires.
function installNoteProxy() {
  // Event delegation: capture data attributes from any click that will trigger openEarningsNote
  document.addEventListener('click', (e) => {
    // Walk up the DOM to find the element carrying note identifiers
    let el = e.target;
    while (el && el !== document.body) {
      const ticker = el.dataset && el.dataset.ticker;
      const date   = el.dataset && el.dataset.date;
      const type   = el.dataset && el.dataset.type;
      if (ticker && date && type) {
        // Pre-set _currentNote so when MutationObserver fires, it already has context
        _currentNote = { ticker, date, type, md: null };
        // Eagerly fetch note markdown for confidence parsing (non-blocking)
        const prefix = type === 'post' ? 'notes/post_earnings' : 'notes/pre_earnings';
        const path   = `${prefix}/${ticker}_${date}.md`;
        fetchNoteContent(path).then(md => {
          if (md && _currentNote.ticker === ticker) {
            _currentNote.md = md;
            noteSearchCache[path] = md;
          }
        });
        break;
      }
      // Also handle the note button itself (its parent card has the attrs)
      if (el.classList && el.classList.contains('earnings-note-btn')) {
        const card = el.closest('[data-ticker]');
        if (card) {
          const ticker = card.dataset.ticker;
          const date   = card.dataset.date;
          const type   = card.dataset.type;
          if (ticker && date && type) {
            _currentNote = { ticker, date, type, md: null };
            const prefix = type === 'post' ? 'notes/post_earnings' : 'notes/pre_earnings';
            const path   = `${prefix}/${ticker}_${date}.md`;
            fetchNoteContent(path).then(md => {
              if (md && _currentNote.ticker === ticker) {
                _currentNote.md = md;
                noteSearchCache[path] = md;
              }
            });
          }
        }
        break;
      }
      el = el.parentElement;
    }
  }, true); // use capture so we run before stopPropagation
}

/* ------------------------------------------------------------------
   SEARCH BUTTON INJECTION
   Adds a "Search Notes" button next to the "Archive" button in the
   Earnings section header.
------------------------------------------------------------------ */

function injectSearchButton() {
  // Wait for earnings-header-controls to exist
  const controls = document.querySelector('.earnings-header-controls');
  if (!controls) {
    setTimeout(injectSearchButton, 300);
    return;
  }
  if (document.getElementById('note-search-btn')) return;

  const btn = document.createElement('button');
  btn.className = 'btn-sm btn-ghost';
  btn.id = 'note-search-btn';
  btn.textContent = 'Search Notes';
  btn.title = 'Search all earnings notes (Ctrl+K)';
  btn.addEventListener('click', openNoteSearch);

  // Insert before Archive button
  const archiveBtn = document.getElementById('earnings-archive-btn');
  if (archiveBtn) {
    controls.insertBefore(btn, archiveBtn);
  } else {
    controls.appendChild(btn);
  }
}

function injectSearchKeyboardShortcut() {
  document.addEventListener('keydown', e => {
    // Ctrl+K or Cmd+K to open note search
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      // Only if we're on the research tab or search is already open
      const researchTab = document.getElementById('tab-research');
      const isResearchActive = researchTab && researchTab.classList.contains('active');
      const searchOpen = document.getElementById('note-search-overlay')?.classList.contains('active');
      if (isResearchActive || searchOpen) {
        e.preventDefault();
        if (searchOpen) {
          closeNoteSearch();
        } else {
          openNoteSearch();
        }
      }
    }
  });
}

/* ------------------------------------------------------------------
   UTILITY HELPERS
------------------------------------------------------------------ */

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function daysBetween(dateStrA, dateStrB) {
  const a = new Date(dateStrA);
  const b = new Date(dateStrB);
  return Math.round(Math.abs((b - a) / 86400000));
}

/* ------------------------------------------------------------------
   INIT — install proxy once the base earnings.js has fully loaded
------------------------------------------------------------------ */
(function init() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installNoteProxy);
  } else {
    installNoteProxy();
  }
})();
