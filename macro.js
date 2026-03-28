/* ===== MACRO.JS — Macro tab rendering ===== */
/* Renders: pillar tiles, regime bar, sector/factor heatmap, stock ideas, commodities */

let macroDataCache = null;

async function loadMacroData() {
  // Try Supabase first
  if (await checkSupabase()) {
    try {
      const rows = await supabaseGet('metadata', 'select=key,value&key=eq.macro_snapshot');
      if (rows?.length && rows[0].value) {
        const parsed = typeof rows[0].value === 'string' ? JSON.parse(rows[0].value) : rows[0].value;
        macroDataCache = parsed;
        return parsed;
      }
    } catch (e) {
      console.warn('Supabase macro fetch failed:', e.message);
    }
  }
  // Fall back to local JSON
  try {
    const resp = await fetch('macro_data.json', { signal: AbortSignal.timeout(8000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    macroDataCache = await resp.json();
    return macroDataCache;
  } catch (e) {
    console.warn('Local macro_data.json not found:', e.message);
    return null;
  }
}

function renderMacroTab() {
  const container = document.getElementById('macro-content');
  if (!container) return;

  if (!macroDataCache) {
    container.innerHTML = '<div class="macro-loading">Loading macro data...</div>';
    loadMacroData().then(data => {
      if (data) renderMacroContent(container, data);
      else container.innerHTML = '<div class="macro-empty">No macro data available. Run refresh_macro.mjs to generate.</div>';
    });
    return;
  }
  renderMacroContent(container, macroDataCache);
}

function renderMacroContent(container, data) {
  const html = [];

  // ── Regime Banner ──
  html.push(renderRegimeBanner(data.regime, data.generated));

  // ── Pillar Tiles ──
  html.push('<div class="macro-pillars">');
  html.push(renderPillarTile('Growth', data.pillars.growth, '📈'));
  html.push(renderPillarTile('Inflation', data.pillars.inflation, '🔥'));
  html.push(renderPillarTile('Policy', data.pillars.policy, '🏛'));
  html.push(renderPillarTile('Sentiment', data.pillars.sentiment, '📊'));
  html.push('</div>');

  // ── Market Indices Row ──
  html.push(renderIndicesBar(data.indices));

  // ── Two-column layout: Heatmaps + Ideas ──
  html.push('<div class="macro-grid-2col">');

  // Left: Sector + Factor Heatmaps
  html.push('<div class="macro-col">');
  html.push(renderSectorHeatmap(data.sectors, data.regime));
  html.push(renderFactorHeatmap(data.factors, data.regime));
  html.push('</div>');

  // Right: Stock Ideas + Commodities
  html.push('<div class="macro-col">');
  html.push(renderStockIdeas(data.ideas, data.regime));
  html.push(renderCommodities(data.commodities));
  html.push(renderRates(data.rates));
  html.push('</div>');

  html.push('</div>');

  container.innerHTML = html.join('');
}

// ── Regime Banner ──
function renderRegimeBanner(regime, generated) {
  if (!regime) return '';
  const genDate = generated ? new Date(generated).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  return `
    <div class="macro-regime-banner" style="border-left: 4px solid ${regime.color}">
      <div class="regime-header">
        <div class="regime-label-group">
          <span class="regime-dot" style="background:${regime.color}"></span>
          <span class="regime-label">${regime.regime}</span>
        </div>
        <span class="regime-updated">Updated ${genDate}</span>
      </div>
      <p class="regime-desc">${regime.desc}</p>
      <div class="regime-tilts">
        ${regime.favored_sectors?.length ? `<span class="tilt-tag tilt-favor">Favor: ${regime.favored_sectors.map(s => sectorName(s)).join(', ')}</span>` : ''}
        ${regime.avoid_sectors?.length ? `<span class="tilt-tag tilt-avoid">Avoid: ${regime.avoid_sectors.map(s => sectorName(s)).join(', ')}</span>` : ''}
      </div>
    </div>`;
}

function sectorName(etf) {
  const map = { 'XLK': 'Tech', 'XLF': 'Fins', 'XLV': 'HC', 'XLE': 'Energy', 'XLI': 'Indust', 'XLC': 'Comm', 'XLY': 'Disc', 'XLP': 'Staples', 'XLRE': 'RE', 'XLU': 'Utils', 'XLB': 'Matls' };
  return map[etf] || etf;
}

// ── Pillar Tile ──
function renderPillarTile(name, pillar, icon) {
  if (!pillar) return `<div class="macro-pillar-tile"><h4>${icon} ${name}</h4><span class="pillar-na">No data</span></div>`;
  const colorVar = pillar.color === 'green' ? 'var(--green)' : pillar.color === 'red' ? 'var(--red)' : 'var(--yellow)';
  const bgVar = pillar.color === 'green' ? 'var(--green-dim)' : pillar.color === 'red' ? 'var(--red-dim)' : 'var(--yellow-dim)';

  let signalRows = '';
  if (pillar.signals && pillar.signals.length > 0) {
    signalRows = pillar.signals.map(s => {
      return `<div class="pillar-signal">
        <span class="signal-name">${s.name}</span>
        <span class="signal-price">${fmtNum(s.price)}</span>
        <span class="signal-dir ${s.trend === 'up' ? 'val-pos' : s.trend === 'down' ? 'val-neg' : 'val-neutral'}">${s.direction}</span>
        <span class="signal-chg ${pctCls(s.change1d)}">${fmtPctS(s.change1d)}</span>
        <span class="signal-chg ${pctCls(s.change1w)}">${fmtPctS(s.change1w)}</span>
        <span class="signal-chg ${pctCls(s.change1m)}">${fmtPctS(s.change1m)}</span>
        <span class="signal-chg ${pctCls(s.change3m)}">${fmtPctS(s.change3m)}</span>
        <span class="signal-chg ${pctCls(s.change6m)}">${fmtPctS(s.change6m)}</span>
        <span class="signal-chg ${pctCls(s.change1y)}">${fmtPctS(s.change1y)}</span>
      </div>`;
    }).join('');
  }

  return `
    <div class="macro-pillar-tile" style="border-top: 3px solid ${colorVar}">
      <div class="pillar-header">
        <span class="pillar-icon">${icon}</span>
        <h4>${name}</h4>
        <span class="pillar-badge" style="background:${bgVar};color:${colorVar}">${pillar.label}</span>
      </div>
      <div class="pillar-score-bar">
        <div class="score-track">
          <div class="score-fill" style="width:${Math.abs(pillar.score)*50}%;${pillar.score >= 0 ? 'left:50%' : `right:50%`};background:${colorVar}"></div>
          <div class="score-center"></div>
        </div>
        <span class="score-val">${pillar.score > 0 ? '+' : ''}${pillar.score.toFixed(2)}</span>
      </div>
      ${signalRows ? `<div class="pillar-signals">
        <div class="pillar-signal pillar-signal-header">
          <span class="signal-name">Indicator</span>
          <span class="signal-price">Level</span>
          <span class="signal-dir">Trend</span>
          <span class="signal-chg">1D</span>
          <span class="signal-chg">1W</span>
          <span class="signal-chg">1M</span>
          <span class="signal-chg">3M</span>
          <span class="signal-chg">6M</span>
          <span class="signal-chg">1Y</span>
        </div>
        ${signalRows}
      </div>` : ''}
    </div>`;
}

// ── Market Indices Bar ──
function renderIndicesBar(indices) {
  if (!indices) return '';
  const items = Object.entries(indices).filter(([k]) => k !== '^VIX').map(([ticker, d]) => {
    const chg = d.change1d;
    return `<div class="index-chip ${pctCls(chg)}">
      <span class="index-name">${d.name}</span>
      <span class="index-price">${fmtNum(d.price)}</span>
      <span class="index-chg">${fmtPct(chg)}</span>
    </div>`;
  });
  // VIX separate
  const vix = indices['^VIX'];
  if (vix) {
    const vixCls = vix.price > 30 ? 'vix-high' : vix.price > 20 ? 'vix-elevated' : 'vix-low';
    items.push(`<div class="index-chip ${vixCls}">
      <span class="index-name">VIX</span>
      <span class="index-price">${vix.price?.toFixed(1)}</span>
      <span class="index-chg">${fmtPct(vix.change1d)}</span>
    </div>`);
  }
  return `<div class="macro-indices-bar">${items.join('')}</div>`;
}

// ── Composite Regime Score ──
// Combines regime alignment (FAVOR/AVOID) with relative momentum into one number.
// Score range: -100 (strong avoid) to +100 (strong favor)
// Components:
//   Regime alignment: FAVOR = +50, AVOID = -50, Neutral = 0
//   Relative momentum: rank-based percentile of 1M return, scaled -50 to +50
function computeRegimeScores(entries, favoredSet, avoidSet) {
  // Rank by 1M performance (best = highest rank)
  const byPerf = entries.slice().sort((a, b) => (a[1].change_1m || 0) - (b[1].change_1m || 0));
  const n = byPerf.length;
  const rankMap = {};
  byPerf.forEach(([t], i) => { rankMap[t] = n > 1 ? (i / (n - 1)) * 100 - 50 : 0; }); // -50 to +50

  return entries.map(([ticker, d]) => {
    const isFavor = favoredSet.has(ticker);
    const isAvoid = avoidSet.has(ticker);
    const regimeBase = isFavor ? 50 : isAvoid ? -50 : 0;
    const momRank = rankMap[ticker] || 0;
    const score = Math.round(regimeBase + momRank);
    return [ticker, d, score, isFavor, isAvoid];
  });
}

function renderScoreBar(score) {
  // Score: -100 to +100. Bar fills from center outward.
  const absScore = Math.abs(score);
  const barWidth = Math.min(50, (absScore / 100) * 50);
  const barColor = score >= 0 ? 'var(--green)' : 'var(--red)';
  const barSide = score >= 0
    ? `left:50%;width:${barWidth}%`
    : `right:50%;width:${barWidth}%`;
  const labelColor = score >= 0 ? 'var(--green)' : 'var(--red)';
  return `<span class="hm-diverge-wrap">
    <span class="hm-diverge-center"></span>
    <span class="hm-diverge-bar" style="${barSide};background:${barColor}"></span>
    <span class="hm-diverge-label" style="color:${labelColor}">${score > 0 ? '+' : ''}${score}</span>
  </span>`;
}

// ── Sector Heatmap ──
function renderSectorHeatmap(sectors, regime) {
  if (!sectors || Object.keys(sectors).length === 0) return '';
  const entries = Object.entries(sectors);
  const favoredSet = new Set(regime?.favored_sectors || []);
  const avoidSet = new Set(regime?.avoid_sectors || []);

  const scored = computeRegimeScores(entries, favoredSet, avoidSet);
  // Sort by composite score descending
  scored.sort((a, b) => b[2] - a[2]);

  const rows = scored.map(([ticker, d, score, isFavor, isAvoid]) => {
    const tag = isFavor ? '<span class="hm-tag hm-favor">FAVOR</span>' :
                isAvoid ? '<span class="hm-tag hm-avoid">AVOID</span>' : '';
    return `<div class="hm-row">
      <span class="hm-ticker">${ticker}</span>
      <span class="hm-name">${d.sectorName || d.name}</span>
      ${tag}
      ${renderScoreBar(score)}
      <span class="hm-val ${pctCls(d.change1d)}">${fmtPct(d.change1d)}</span>
      <span class="hm-val ${pctCls(d.change_1w)}">${fmtPct(d.change_1w)}</span>
      <span class="hm-val ${pctCls(d.change_1m)}">${fmtPct(d.change_1m)}</span>
      <span class="hm-val ${pctCls(d.change_3m)}">${fmtPct(d.change_3m)}</span>
      <span class="hm-val ${pctCls(d.change_6m)}">${fmtPct(d.change_6m)}</span>
      <span class="hm-val ${pctCls(d.change_1y)}">${fmtPct(d.change_1y)}</span>
    </div>`;
  }).join('');

  return `
    <div class="macro-card">
      <h3 class="macro-card-title">Sector Heatmap</h3>
      <div class="hm-scroll-wrap">
        <div class="hm-header">
          <span class="hm-ticker">ETF</span>
          <span class="hm-name">Sector</span>
          <span class="hm-diverge-wrap">Regime Score</span>
          <span class="hm-val">1D</span>
          <span class="hm-val">1W</span>
          <span class="hm-val">1M</span>
          <span class="hm-val">3M</span>
          <span class="hm-val">6M</span>
          <span class="hm-val">1Y</span>
        </div>
        ${rows}
      </div>
    </div>`;
}

// ── Factor Heatmap ──
function renderFactorHeatmap(factors, regime) {
  if (!factors || Object.keys(factors).length === 0) return '';
  const entries = Object.entries(factors);
  const favoredSet = new Set(regime?.favored_factors || []);
  const avoidSet = new Set(regime?.avoid_factors || []);

  const scored = computeRegimeScores(entries, favoredSet, avoidSet);
  scored.sort((a, b) => b[2] - a[2]);

  const rows = scored.map(([ticker, d, score, isFavor, isAvoid]) => {
    const tag = isFavor ? '<span class="hm-tag hm-favor">FAVOR</span>' :
                isAvoid ? '<span class="hm-tag hm-avoid">AVOID</span>' : '';
    return `<div class="hm-row">
      <span class="hm-ticker">${ticker}</span>
      <span class="hm-name">${d.factorName || d.name}</span>
      ${tag}
      ${renderScoreBar(score)}
      <span class="hm-val ${pctCls(d.change1d)}">${fmtPct(d.change1d)}</span>
      <span class="hm-val ${pctCls(d.change_1w)}">${fmtPct(d.change_1w)}</span>
      <span class="hm-val ${pctCls(d.change_1m)}">${fmtPct(d.change_1m)}</span>
      <span class="hm-val ${pctCls(d.change_3m)}">${fmtPct(d.change_3m)}</span>
      <span class="hm-val ${pctCls(d.change_6m)}">${fmtPct(d.change_6m)}</span>
      <span class="hm-val ${pctCls(d.change_1y)}">${fmtPct(d.change_1y)}</span>
    </div>`;
  }).join('');

  return `
    <div class="macro-card">
      <h3 class="macro-card-title">Factor Performance</h3>
      <div class="hm-scroll-wrap">
        <div class="hm-header">
          <span class="hm-ticker">ETF</span>
          <span class="hm-name">Factor</span>
          <span class="hm-diverge-wrap">Regime Score</span>
          <span class="hm-val">1D</span>
          <span class="hm-val">1W</span>
          <span class="hm-val">1M</span>
          <span class="hm-val">3M</span>
          <span class="hm-val">6M</span>
          <span class="hm-val">1Y</span>
        </div>
        ${rows}
      </div>
    </div>`;
}

// ── Stock Ideas ──
function renderStockIdeas(ideas, regime) {
  if (!ideas) return '';
  const regimeLabel = regime?.regime || 'Current Regime';

  let html = `<div class="macro-card">
    <h3 class="macro-card-title">Stock Ideas — ${regimeLabel}</h3>`;

  if (ideas.own?.length > 0) {
    html += `<div class="ideas-section">
      <h4 class="ideas-heading ideas-own-heading">Stocks to Own</h4>
      ${ideas.own.map(i => `<div class="idea-row idea-own">
        <span class="idea-ticker">${i.ticker}</span>
        <span class="idea-name">${i.name}</span>
        <span class="idea-subsector">${i.subsector}</span>
        <span class="idea-reason">${i.reason}</span>
      </div>`).join('')}
    </div>`;
  }

  if (ideas.avoid?.length > 0) {
    html += `<div class="ideas-section">
      <h4 class="ideas-heading ideas-avoid-heading">Stocks to Avoid</h4>
      ${ideas.avoid.map(i => `<div class="idea-row idea-avoid">
        <span class="idea-ticker">${i.ticker}</span>
        <span class="idea-name">${i.name}</span>
        <span class="idea-subsector">${i.subsector}</span>
        <span class="idea-reason">${i.reason}</span>
      </div>`).join('')}
    </div>`;
  }

  if (!ideas.own?.length && !ideas.avoid?.length) {
    html += '<div class="macro-empty">No directional ideas in Transition regime. Focus on Quality factor.</div>';
  }

  html += '</div>';
  return html;
}

// ── Commodities ──
function renderCommodities(commodities) {
  if (!commodities || Object.keys(commodities).length === 0) return '';
  const rows = Object.entries(commodities).map(([key, d]) => `
    <div class="commodity-row">
      <span class="commodity-name">${d.commodityName || d.name}</span>
      <span class="commodity-price">$${d.price?.toFixed(2)}</span>
      <span class="commodity-chg ${pctCls(d.change1d)}">${fmtPct(d.change1d)}</span>
      <span class="commodity-chg ${pctCls(d.change_1w)}">${fmtPct(d.change_1w)}</span>
      <span class="commodity-chg ${pctCls(d.change_1m)}">${fmtPct(d.change_1m)}</span>
      <span class="commodity-chg ${pctCls(d.change_3m)}">${fmtPct(d.change_3m)}</span>
      <span class="commodity-chg ${pctCls(d.change_6m)}">${fmtPct(d.change_6m)}</span>
      <span class="commodity-chg ${pctCls(d.change_1y)}">${fmtPct(d.change_1y)}</span>
    </div>`).join('');

  return `
    <div class="macro-card">
      <h3 class="macro-card-title">Commodities</h3>
      <div class="commodity-scroll-wrap">
        <div class="commodity-header">
          <span class="commodity-name">Name</span>
          <span class="commodity-price">Price</span>
          <span class="commodity-chg">1D</span>
          <span class="commodity-chg">1W</span>
          <span class="commodity-chg">1M</span>
          <span class="commodity-chg">3M</span>
          <span class="commodity-chg">6M</span>
          <span class="commodity-chg">1Y</span>
        </div>
        ${rows}
      </div>
    </div>`;
}

// ── Rates ──
function renderRates(rates) {
  if (!rates || Object.keys(rates).length === 0) return '';
  const rows = Object.entries(rates).map(([ticker, d]) => `
    <div class="commodity-row">
      <span class="commodity-name">${d.rateName || d.name}</span>
      <span class="commodity-price">${d.price?.toFixed(2)}${ticker.startsWith('^') ? '%' : ''}</span>
      <span class="commodity-chg ${pctCls(d.change1d)}">${fmtPct(d.change1d)}</span>
      <span class="commodity-chg ${pctCls(d.change_1w)}">${fmtPct(d.change_1w)}</span>
      <span class="commodity-chg ${pctCls(d.change_1m)}">${fmtPct(d.change_1m)}</span>
      <span class="commodity-chg ${pctCls(d.change_3m)}">${fmtPct(d.change_3m)}</span>
      <span class="commodity-chg ${pctCls(d.change_6m)}">${fmtPct(d.change_6m)}</span>
      <span class="commodity-chg ${pctCls(d.change_1y)}">${fmtPct(d.change_1y)}</span>
    </div>`).join('');

  return `
    <div class="macro-card">
      <h3 class="macro-card-title">Rates & Policy</h3>
      <div class="commodity-scroll-wrap">
        <div class="commodity-header">
          <span class="commodity-name">Indicator</span>
          <span class="commodity-price">Level</span>
          <span class="commodity-chg">1D</span>
          <span class="commodity-chg">1W</span>
          <span class="commodity-chg">1M</span>
          <span class="commodity-chg">3M</span>
          <span class="commodity-chg">6M</span>
          <span class="commodity-chg">1Y</span>
        </div>
        ${rows}
      </div>
    </div>`;
}

// ── Formatting helpers ──
function fmtPct(v) {
  if (v == null || isNaN(v)) return '—';
  return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
}
function fmtPctS(v) {
  if (v == null || isNaN(v)) return '';
  return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
}
function fmtNum(v) {
  if (v == null || isNaN(v)) return '—';
  if (v >= 1000) return v.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  if (v >= 100) return v.toFixed(1);
  return v.toFixed(2);
}
function pctCls(v) {
  if (v == null || isNaN(v) || Math.abs(v) < 0.05) return 'val-neutral';
  return v > 0 ? 'val-pos' : 'val-neg';
}
