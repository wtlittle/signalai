/* ===== WEEKLY-BRIEFING.JS — Weekly Market Briefing tab ===== */

let weeklyBriefingData = null;

async function loadWeeklyBriefing() {
  try {
    const resp = await fetch('weekly_briefing.json?v=' + Date.now());
    weeklyBriefingData = await resp.json();
    renderWeeklyBriefing();
  } catch (e) {
    console.error('Failed to load weekly briefing:', e);
    document.getElementById('weekly-briefing-content').innerHTML =
      '<div class="wb-empty">No weekly briefing available yet.</div>';
  }
}

function renderWeeklyBriefing() {
  const d = weeklyBriefingData;
  if (!d) return;
  const $content = document.getElementById('weekly-briefing-content');

  // --- Header ---
  const weekEnd = d.week_ending || 'N/A';
  const ms = d.market_summary || {};

  let html = `
    <div class="wb-header">
      <div class="wb-date">Week Ending ${weekEnd}</div>
      <div class="wb-indices">
        <span class="wb-index ${parseFloat((ms.sp500_weekly||'').replace('%','')) >= 0 ? 'positive' : 'negative'}">
          S&P 500: ${ms.sp500_weekly || 'N/A'}
        </span>
        <span class="wb-index ${parseFloat((ms.nasdaq_weekly||'').replace('%','')) >= 0 ? 'positive' : 'negative'}">
          Nasdaq: ${ms.nasdaq_weekly || 'N/A'}
        </span>
        <span class="wb-index ${parseFloat((ms.russell_weekly||'').replace('%','')) >= 0 ? 'positive' : 'negative'}">
          Russell 2000: ${ms.russell_weekly || 'N/A'}
        </span>
      </div>
    </div>
    <div class="wb-narrative">${ms.narrative || ''}</div>
  `;

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
          <span class="wb-card-price">${v.price}</span>
        </div>
        <div class="wb-card-stats">
          <span class="wb-stat negative">${v.off_high_pct} off 52w high</span>
          <span class="wb-stat">P/E: ${v.pe_ratio || 'N/A'}</span>
          <span class="wb-stat">EV/EBITDA: ${v.ev_ebitda || 'N/A'}</span>
          <span class="wb-stat">Rev Growth: ${v.rev_growth || 'N/A'}</span>
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
          <span class="wb-card-price">${m.price}</span>
        </div>
        <div class="wb-card-stats">
          <span class="wb-stat ${parseFloat((m.perf_1w||'').replace('%','').replace('+','')) >= 0 ? 'positive' : 'negative'}">1W: ${m.perf_1w}</span>
          <span class="wb-stat ${parseFloat((m.perf_1m||'').replace('%','').replace('+','')) >= 0 ? 'positive' : 'negative'}">1M: ${m.perf_1m}</span>
          <span class="wb-stat ${parseFloat((m.perf_3m||'').replace('%','').replace('+','')) >= 0 ? 'positive' : 'negative'}">3M: ${m.perf_3m}</span>
          <span class="wb-stat">Rev Growth: ${m.rev_growth || 'N/A'}</span>
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

  // --- Trends ---
  html += `<div class="wb-section">
    <h3 class="wb-section-title">
      <span class="wb-icon">&#8680;</span> Key Trends
    </h3>
    <div class="wb-list">`;
  (d.trends || []).forEach(t => {
    html += `
      <div class="wb-list-item">
        <div class="wb-list-title">${t.title}</div>
        <div class="wb-list-detail">${t.detail}</div>
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
        <div class="wb-list-title">${r.title}</div>
        <div class="wb-list-detail">${r.detail}</div>
      </div>`;
  });
  html += `</div></div>`;

  // --- Watchlist Updates ---
  const updates = d.watchlist_updates || [];
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
  if (d.archive && d.archive.length > 0) {
    html += `<div class="wb-archive-link">
      <button class="btn-sm btn-ghost" onclick="showBriefingArchive()">View Past Briefings (${d.archive.length})</button>
    </div>`;
  }

  $content.innerHTML = html;
}

// Load on section visibility
function initWeeklyBriefing() {
  const section = document.getElementById('weekly-briefing-section');
  if (section) {
    loadWeeklyBriefing();
  }
}

// Auto-init
document.addEventListener('DOMContentLoaded', () => {
  initWeeklyBriefing();
});
