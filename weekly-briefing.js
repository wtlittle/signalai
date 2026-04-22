/* ===== WEEKLY-BRIEFING.JS — Weekly Market Briefing tab ===== */

let weeklyBriefingData = null;
let weeklyBriefingArchiveIndex = null; // array of { week_ending, path }
let weeklyBriefingInFlight = null;

async function loadWeeklyBriefing(path = 'weekly_briefing.json') {
  try {
    // Show skeleton while fetching if we already have content
    const $content = document.getElementById('weekly-briefing-content');
    if (weeklyBriefingData && $content) {
      renderBriefingSkeletonOverlay();
    }
    weeklyBriefingInFlight = path;
    const resp = await fetch(path + (path.includes('?') ? '&' : '?') + 'v=' + Date.now());
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    if (weeklyBriefingInFlight !== path) return; // stale
    weeklyBriefingData = data;
    renderWeeklyBriefing();
  } catch (e) {
    console.error('Failed to load weekly briefing:', e);
    const $content = document.getElementById('weekly-briefing-content');
    if ($content) {
      $content.innerHTML = '<div class="wb-empty">No saved briefing for this week. <button class="btn-sm btn-ghost" onclick="loadLatestBriefing()">Load latest briefing</button></div>';
    }
  }
}

function loadLatestBriefing() { loadWeeklyBriefing('weekly_briefing.json'); }

function renderBriefingSkeletonOverlay() {
  const $content = document.getElementById('weekly-briefing-content');
  if (!$content) return;
  const header = $content.querySelector('.wb-header');
  const narrative = $content.querySelector('.wb-narrative');
  const tldr = $content.querySelector('.wb-tldr');
  [header, narrative, tldr].forEach(el => {
    if (el) el.classList.add('wb-loading');
  });
}

// ─── TL;DR synthesis ───
function synthesizeTldr(d) {
  // Prefer explicit payload
  if (Array.isArray(d.tl_dr) && d.tl_dr.length) {
    return { bullets: d.tl_dr.slice(0, 5), chips: d.tl_dr_chips || [] };
  }
  if (d.tl_dr && typeof d.tl_dr === 'object' && Array.isArray(d.tl_dr.bullets)) {
    return { bullets: d.tl_dr.bullets.slice(0, 5), chips: d.tl_dr.chips || [] };
  }
  // Otherwise derive from trends + risks + top picks
  const bullets = [];
  const chips = [];
  const ms = d.market_summary || {};
  const ir = ms.index_returns || {};
  const sp = ir.sp500_weekly || '';
  const spPct = parseFloat((sp || '').replace('%', '').replace('+', ''));
  if (!isNaN(spPct)) {
    if (spPct >= 1.5) { chips.push('Risk-on'); bullets.push(`S&P 500 up ${sp.split(' ')[0]} this week — risk-on tone with broad breadth.`); }
    else if (spPct <= -1.5) { chips.push('Risk-off'); bullets.push(`S&P 500 down ${sp.split(' ')[0]} this week — defensive tone as risk assets retreat.`); }
    else bullets.push(`S&P 500 ${sp.split(' ')[0]} — mixed tape with leadership rotation beneath the surface.`);
  }
  const trends = (d.trends || []).slice(0, 2);
  trends.forEach(t => {
    const title = t.title || t.name || '';
    if (title) bullets.push(title.length > 110 ? title.slice(0, 107) + '…' : title);
  });
  const risks = (d.risks || []).slice(0, 1);
  risks.forEach(r => {
    const title = r.title || r.name || '';
    if (title) bullets.push('Risk: ' + (title.length > 100 ? title.slice(0, 97) + '…' : title));
  });
  if ((d.value_picks || []).length && bullets.length < 5) {
    const v = d.value_picks[0];
    bullets.push(`Value setup: ${v.ticker} ${v.pct_off_high || v.off_high_pct || ''} off 52w high with ${v.fcf_yield || 'solid FCF'}.`);
  }
  if ((d.momentum_picks || []).length && bullets.length < 5) {
    const m = d.momentum_picks[0];
    bullets.push(`Momentum leader: ${m.ticker} ${m.perf_1w || m.one_week || ''} on the week.`);
  }
  // Regime chip
  if (ms.macro_regime) chips.push(ms.macro_regime);
  return { bullets: bullets.slice(0, 5), chips };
}

// ─── Week-ending picker helpers ───
function weekEndingFriday(d) {
  const dt = (d instanceof Date) ? new Date(d) : new Date(d + 'T12:00:00');
  const dow = dt.getDay(); // 0=Sun..6=Sat
  const diff = (5 - dow + 7) % 7; // days to Friday
  dt.setDate(dt.getDate() + diff);
  return dt.toISOString().slice(0, 10);
}

function shiftWeek(dateStr, deltaWeeks) {
  const dt = new Date(dateStr + 'T12:00:00');
  dt.setDate(dt.getDate() + 7 * deltaWeeks);
  return dt.toISOString().slice(0, 10);
}

async function ensureArchiveIndex(d) {
  if (weeklyBriefingArchiveIndex) return weeklyBriefingArchiveIndex;
  const fromPayload = Array.isArray(d.archive) ? d.archive : [];
  const normalized = fromPayload.map(a => typeof a === 'string' ? { week_ending: a, path: `archive/briefings/weekly_briefing_${a}.json` } : a)
                                .filter(a => a && a.week_ending);
  weeklyBriefingArchiveIndex = normalized;
  return normalized;
}

function jumpToWeek(weekEnding) {
  // Current live briefing matches?
  if (weeklyBriefingData && weeklyBriefingData.week_ending === weekEnding) return;
  const idx = weeklyBriefingArchiveIndex || [];
  const match = idx.find(a => a.week_ending === weekEnding);
  if (match && match.path) {
    loadWeeklyBriefing(match.path);
  } else {
    // No archive for this week — show an inline empty state
    const $content = document.getElementById('weekly-briefing-content');
    if ($content) {
      const existingPicker = $content.querySelector('.wb-header');
      $content.innerHTML = `
        ${existingPicker ? existingPicker.outerHTML : ''}
        <div class="wb-empty wb-empty-week">
          <div>No saved briefing for week ending <strong>${weekEnding}</strong>.</div>
          <button class="btn-sm" onclick="loadLatestBriefing()">Go to latest briefing</button>
        </div>
      `;
      // Re-wire the picker
      wireWeekPicker();
    }
  }
}

function wireWeekPicker() {
  const prev = document.querySelector('.wb-week-prev');
  const next = document.querySelector('.wb-week-next');
  const input = document.querySelector('.wb-week-input');
  if (prev) prev.addEventListener('click', () => {
    const cur = input?.value || weeklyBriefingData?.week_ending;
    if (cur) jumpToWeek(weekEndingFriday(shiftWeek(cur, -1)));
  });
  if (next) next.addEventListener('click', () => {
    const cur = input?.value || weeklyBriefingData?.week_ending;
    if (cur) jumpToWeek(weekEndingFriday(shiftWeek(cur, 1)));
  });
  if (input) input.addEventListener('change', (e) => {
    const picked = weekEndingFriday(e.target.value);
    jumpToWeek(picked);
  });
}

function renderWeeklyBriefing() {
  const d = weeklyBriefingData;
  if (!d) return;
  const $content = document.getElementById('weekly-briefing-content');

  // --- Header ---
  const weekEnd = d.week_ending || '';
  const ms = d.market_summary || {};
  const ir = ms.index_returns || {};

  // Pull index returns from either the nested index_returns block or the flat top-level keys
  const sp500 = ms.sp500_weekly || ir.sp500_weekly;
  const nasdaq = ms.nasdaq_weekly || ir.nasdaq_weekly;
  const russell = ms.russell_weekly || ir.russell_weekly;

  // Build archive set for picker
  const archive = Array.isArray(d.archive) ? d.archive : [];
  weeklyBriefingArchiveIndex = archive.map(a => typeof a === 'string' ? { week_ending: a, path: `archive/briefings/weekly_briefing_${a}.json` } : a)
                                      .filter(a => a && a.week_ending);
  // Ensure current week is in the set
  if (weekEnd && !weeklyBriefingArchiveIndex.some(a => a.week_ending === weekEnd)) {
    weeklyBriefingArchiveIndex.push({ week_ending: weekEnd, path: 'weekly_briefing.json' });
  }
  // Sort ascending
  weeklyBriefingArchiveIndex.sort((a, b) => a.week_ending.localeCompare(b.week_ending));
  const earliest = weeklyBriefingArchiveIndex[0]?.week_ending || weekEnd;
  const today = new Date().toISOString().slice(0, 10);

  const indexBadge = (label, val) => {
    if (!val) {
      return `<span class="wb-index wb-index-skeleton" title="Week-over-week price return, Fri close to Fri close" aria-label="${label} weekly return loading">
        <span class="wb-index-label">${label}:</span>
        <span class="wb-skeleton-pill"></span>
      </span>`;
    }
    const num = parseFloat(String(val).replace('%', '').replace('+', ''));
    const cls = isNaN(num) ? '' : (num >= 0 ? 'positive' : 'negative');
    return `<span class="wb-index ${cls}" title="Week-over-week price return, Fri close to Fri close">${label}: ${val}</span>`;
  };

  let html = `
    <div class="wb-header">
      <div class="wb-header-left">
        <div class="wb-date-row">
          <button class="wb-week-prev" title="Previous week" aria-label="Previous week">&lsaquo;</button>
          <div class="wb-date-label">
            <span class="wb-date-prefix">Week Ending</span>
            <input class="wb-week-input" type="date" value="${weekEnd}" min="${earliest}" max="${today}" />
          </div>
          <button class="wb-week-next" title="Next week" aria-label="Next week">&rsaquo;</button>
        </div>
      </div>
      <div class="wb-indices">
        ${indexBadge('S&P 500', sp500)}
        ${indexBadge('Nasdaq', nasdaq)}
        ${indexBadge('Russell 2000', russell)}
      </div>
    </div>
  `;

  // --- TL;DR block (above narrative) ---
  const tldr = synthesizeTldr(d);
  if (tldr.bullets.length) {
    const chipsHtml = (tldr.chips || []).map(c => `<span class="wb-tldr-chip">${c}</span>`).join('');
    html += `
      <section class="wb-tldr">
        <div class="wb-tldr-head">
          <span class="wb-tldr-label">TL;DR</span>
          ${chipsHtml ? `<div class="wb-tldr-chips">${chipsHtml}</div>` : ''}
        </div>
        <ul class="wb-tldr-list">
          ${tldr.bullets.map(b => `<li>${b}</li>`).join('')}
        </ul>
      </section>
    `;
  }

  html += `<div class="wb-narrative">${ms.narrative || ''}</div>`;

  // --- Trends ---
  html += `<div class="wb-section">
    <h3 class="wb-section-title">
      <span class="wb-icon">&#8680;</span> Key Trends
    </h3>
    <div class="wb-list">`;
  (d.trends || []).forEach(t => {
    html += `
      <div class="wb-list-item">
        <div class="wb-list-title">${t.title || t.name || ''}</div>
        <div class="wb-list-detail">${t.detail || t.description || ''}</div>
      </div>`;
  });
  html += `</div></div>`;

  // --- Risks ---
  html += `<div class="wb-section">
    <h3 class="wb-section-title">
      <span class="wb-icon">&#9888;</span> Risks to Watch
    </h3>
    <div class="wb-list wb-risks">`;
  (d.risks || []).forEach(r => {
    html += `
      <div class="wb-list-item wb-risk-item">
        <div class="wb-list-title">${r.title || r.name || ''}</div>
        <div class="wb-list-detail">${r.detail || r.description || ''}</div>
      </div>`;
  });
  html += `</div></div>`;

  // --- Value Picks ---
  html += `<div class="wb-section">
    <h3 class="wb-section-title">
      <span class="wb-icon">&#9670;</span> Top 5 Value Stocks
      <span class="wb-subtitle">Near 52-week lows, strong fundamentals</span>
    </h3>
    <div class="wb-cards">`;
  (d.value_picks || []).forEach((v, i) => {
    html += `
      <div class="wb-card wb-value-card">
        <div class="wb-card-rank">#${i + 1}</div>
        <div class="wb-card-header">
          <span class="wb-card-ticker">${v.ticker}</span>
          <span class="wb-card-name">${v.name}</span>
          <span class="wb-card-price">${v.price || v.current_price || ''}</span>
        </div>
        <div class="wb-card-stats">
          <span class="wb-stat negative">${v.off_high_pct || v.pct_off_high || ''} off 52w high</span>
          <span class="wb-stat">P/E: ${v.pe_ratio || 'N/A'}</span>
          <span class="wb-stat">EV/EBITDA: ${v.ev_ebitda || 'N/A'}</span>
          <span class="wb-stat">Rev Growth: ${v.rev_growth || v.revenue_growth || 'N/A'}</span>
          <span class="wb-stat">FCF Yield: ${v.fcf_yield || 'N/A'}</span>
        </div>
        <div class="wb-card-thesis">
          <div class="wb-label">Why undervalued:</div>
          <div>${v.why_undervalued || ''}</div>
        </div>
        <div class="wb-card-bull">
          <div class="wb-label">Bull case:</div>
          <div>${v.bull_case || ''}</div>
        </div>
      </div>`;
  });
  html += `</div></div>`;

  // --- Momentum Picks ---
  html += `<div class="wb-section">
    <h3 class="wb-section-title">
      <span class="wb-icon">&#9650;</span> Top 5 Momentum Stocks
      <span class="wb-subtitle">Strongest recent performance</span>
    </h3>
    <div class="wb-cards">`;
  (d.momentum_picks || []).forEach((m, i) => {
    html += `
      <div class="wb-card wb-momentum-card">
        <div class="wb-card-rank">#${i + 1}</div>
        <div class="wb-card-header">
          <span class="wb-card-ticker">${m.ticker}</span>
          <span class="wb-card-name">${m.name}</span>
          <span class="wb-card-price">${m.price || m.current_price || ''}</span>
        </div>
        <div class="wb-card-stats">
          <span class="wb-stat ${parseFloat(((m.perf_1w||m.one_week||'0').replace('%','').replace('+',''))) >= 0 ? 'positive' : 'negative'}">1W: ${m.perf_1w||m.one_week||'N/A'}</span>
          <span class="wb-stat ${parseFloat(((m.perf_1m||m.one_month||'0').replace('%','').replace('+',''))) >= 0 ? 'positive' : 'negative'}">1M: ${m.perf_1m||m.one_month||'N/A'}</span>
          <span class="wb-stat ${parseFloat(((m.perf_3m||m.three_month||'0').replace('%','').replace('+',''))) >= 0 ? 'positive' : 'negative'}">3M: ${m.perf_3m||m.three_month||'N/A'}</span>
          <span class="wb-stat">Rev Growth: ${m.rev_growth || m.revenue_growth || 'N/A'}</span>
        </div>
        <div class="wb-card-thesis">
          <div class="wb-label">Catalyst:</div>
          <div>${m.catalyst || ''}</div>
        </div>
        <div class="wb-card-bull">
          <div class="wb-label">Risk/reward:</div>
          <div>${m.risk_reward || ''}</div>
        </div>
      </div>`;
  });
  html += `</div></div>`;

  // --- Watchlist Updates ---
  const updates = d.watchlist_updates || d.watchlist_movers || [];
  if (updates.length > 0) {
    const majorMovers = updates.filter(u => u.is_major_mover);
    const otherMovers = updates.filter(u => !u.is_major_mover);

    html += `<div class="wb-section">
      <h3 class="wb-section-title">
        <span class="wb-icon">&#9733;</span> Watchlist Updates
        <span class="wb-subtitle">${updates.length} tickers with material moves</span>
      </h3>`;

    if (majorMovers.length > 0) {
      html += `<div class="wb-sub-header">Major Movers (>10% in 30 days)</div>
        <div class="wb-updates-grid">`;
      majorMovers.forEach(u => {
        const w = parseFloat((u.price_change_1w || '0').replace('%','').replace('+',''));
        const m = parseFloat((u.price_change_30d || '0').replace('%','').replace('+',''));
        html += `
          <div class="wb-update-card ${m >= 0 ? 'wb-up' : 'wb-down'}">
            <div class="wb-update-header">
              <span class="wb-update-ticker">${u.ticker}</span>
              <div class="wb-update-perfs">
                <span class="wb-perf ${w >= 0 ? 'positive' : 'negative'}">1W: ${u.price_change_1w}</span>
                <span class="wb-perf ${m >= 0 ? 'positive' : 'negative'}">30D: ${u.price_change_30d}</span>
              </div>
            </div>
            <div class="wb-update-headline">${u.headline || ''}</div>
            <div class="wb-update-detail">${u.detail || ''}</div>
          </div>`;
      });
      html += `</div>`;
    }

    if (otherMovers.length > 0) {
      html += `<div class="wb-sub-header" style="margin-top:16px;">Other Notable Moves (>5% weekly)</div>
        <div class="wb-updates-compact">`;
      otherMovers.forEach(u => {
        const w = parseFloat((u.price_change_1w || '0').replace('%','').replace('+',''));
        html += `
          <div class="wb-compact-item">
            <span class="wb-compact-ticker">${u.ticker}</span>
            <span class="wb-perf ${w >= 0 ? 'positive' : 'negative'}">${u.price_change_1w}</span>
            <span class="wb-compact-headline">${u.headline || ''}</span>
          </div>`;
      });
      html += `</div>`;
    }

    html += `</div>`;
  }

  // --- Archive link ---
  if (weeklyBriefingArchiveIndex && weeklyBriefingArchiveIndex.length > 1) {
    html += `<div class="wb-archive-link">
      <button class="btn-sm btn-ghost" onclick="showBriefingArchive()">View Past Briefings (${weeklyBriefingArchiveIndex.length})</button>
    </div>`;
  }

  $content.innerHTML = html;
  wireWeekPicker();
}

// initWeeklyBriefing is called by the tab system when the Briefing tab is activated
function initWeeklyBriefing() {
  const section = document.getElementById('weekly-briefing-section');
  if (section) {
    loadWeeklyBriefing();
  }
}

window.loadLatestBriefing = loadLatestBriefing;
window.jumpToWeek = jumpToWeek;
