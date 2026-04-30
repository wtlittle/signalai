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
    // Route the canonical briefing file through the snapshot host (R2 in prod),
    // with a same-origin repo-relative fallback when R2 returns 404. Preserve
    // full local-path fetches for archived / path-qualified briefings.
    let resp;
    if (path === 'weekly_briefing.json' && window.SignalSnapshot && window.SignalSnapshot.fetchWithFallback) {
      resp = await window.SignalSnapshot.fetchWithFallback('weekly_briefing.json', { cacheBust: true });
    } else {
      const url = path + (path.includes('?') ? '&' : '?') + 'v=' + Date.now();
      resp = await fetch(url);
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    if (weeklyBriefingInFlight !== path) return; // stale
    weeklyBriefingData = data;
    renderWeeklyBriefing();
  } catch (e) {
    console.error('Failed to load weekly briefing:', e);
    // NOTE: fetchWithFallback already calls markFailure when BOTH R2 and the
    // same-origin fallback fail, so we don't need to mark it again here.
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

function showBriefingArchive() {
  const idx = weeklyBriefingArchiveIndex || [];

  // Build modal overlay using the same pattern as index.html popups.
  const overlay = document.createElement('div');
  overlay.className = 'popup-overlay';

  const modal = document.createElement('div');
  modal.className = 'popup-modal';

  const closeBtn = document.createElement('button');
  closeBtn.className = 'popup-close';
  closeBtn.innerHTML = '&times;';
  closeBtn.setAttribute('aria-label', 'Close');

  const heading = document.createElement('h2');
  heading.textContent = 'Past Briefings';

  const list = document.createElement('div');
  list.className = 'briefing-archive-list';
  list.style.maxHeight = '60vh';
  list.style.overflowY = 'auto';

  if (idx.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'wb-empty';
    empty.textContent = 'No archived briefings available.';
    list.appendChild(empty);
  } else {
    // Newest first.
    const sorted = idx.slice().sort((a, b) => b.week_ending.localeCompare(a.week_ending));
    sorted.forEach(item => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'briefing-archive-row';
      row.textContent = item.week_ending;
      row.addEventListener('click', () => {
        try { jumpToWeek(item.week_ending); } catch (e) { console.warn('jumpToWeek failed:', e); }
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      });
      list.appendChild(row);
    });
  }

  modal.appendChild(closeBtn);
  modal.appendChild(heading);
  modal.appendChild(list);
  overlay.appendChild(modal);

  // Close interactions: close button + click outside (overlay itself).
  function dismiss() {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
  }
  closeBtn.addEventListener('click', dismiss);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) dismiss();
  });

  document.body.appendChild(overlay);
  overlay.classList.add('active');
}

window.loadLatestBriefing = loadLatestBriefing;
window.jumpToWeek = jumpToWeek;
window.showBriefingArchive = showBriefingArchive;

// ───────────────────────────────────────────────────────────
// PDF EXPORT
// ───────────────────────────────────────────────────────────

// Lightweight toast (no new libraries)
function showBriefingToast(msg, opts = {}) {
  const existing = document.getElementById('wb-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.id = 'wb-toast';
  toast.className = 'wb-toast' + (opts.type ? ' wb-toast-' + opts.type : '');
  toast.textContent = msg;
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('visible'));
  if (opts.autoHide !== false) {
    setTimeout(() => {
      toast.classList.remove('visible');
      setTimeout(() => toast.remove(), 300);
    }, opts.duration || 2200);
  }
  return toast;
}
function hideBriefingToast() {
  const t = document.getElementById('wb-toast');
  if (t) {
    t.classList.remove('visible');
    setTimeout(() => t.remove(), 250);
  }
}

async function exportBriefingPDF() {
  if (!weeklyBriefingData) {
    showBriefingToast('No briefing loaded yet', { type: 'error' });
    return;
  }
  if (typeof window.jspdf === 'undefined' && typeof window.jsPDF === 'undefined') {
    showBriefingToast('PDF library failed to load', { type: 'error' });
    return;
  }

  const processingToast = showBriefingToast('Generating PDF…', { autoHide: false });

  try {
    const { jsPDF } = window.jspdf || window;
    // Letter 8.5" x 11" in points (72 pt/in): 612 x 792
    const doc = new jsPDF({ unit: 'pt', format: 'letter', compress: true });
    const PAGE_W = 612, PAGE_H = 792;
    const MARGIN = 54; // 0.75"
    const CONTENT_W = PAGE_W - MARGIN * 2;
    const FOOTER_RESERVE = 28;

    // Colors (match app accents)
    const ACCENT_GREEN = [34, 197, 94];    // --green
    const ACCENT_BLUE  = [59, 130, 246];   // --accent
    const TEXT_PRIMARY = [31, 41, 55];
    const TEXT_MUTED   = [107, 114, 128];
    const RED          = [239, 68, 68];
    const BORDER       = [229, 231, 235];

    let y = MARGIN;

    const d = weeklyBriefingData;
    const weekEnd = d.week_ending || new Date().toISOString().slice(0, 10);

    // ── Helpers ──
    function setFont(size, style = 'normal') {
      doc.setFont('helvetica', style);
      doc.setFontSize(size);
    }
    function setColor(rgb) { doc.setTextColor(rgb[0], rgb[1], rgb[2]); }
    function ensureSpace(needed) {
      if (y + needed > PAGE_H - MARGIN - FOOTER_RESERVE) {
        doc.addPage();
        y = MARGIN;
      }
    }
    function drawSectionHeader(label, rgb = ACCENT_GREEN) {
      ensureSpace(30);
      // Accent left bar
      doc.setFillColor(rgb[0], rgb[1], rgb[2]);
      doc.rect(MARGIN, y - 10, 3, 18, 'F');
      setFont(13, 'bold');
      setColor(rgb);
      doc.text(label, MARGIN + 10, y + 3);
      y += 14;
      doc.setDrawColor(BORDER[0], BORDER[1], BORDER[2]);
      doc.setLineWidth(0.5);
      doc.line(MARGIN, y, PAGE_W - MARGIN, y);
      y += 12;
    }
    function writeParagraph(text, size = 11, style = 'normal', color = TEXT_PRIMARY, maxW) {
      if (!text) return;
      setFont(size, style);
      setColor(color);
      const lines = doc.splitTextToSize(String(text), maxW || CONTENT_W);
      const lineH = size * 1.35;
      lines.forEach(line => {
        ensureSpace(lineH);
        doc.text(line, MARGIN, y);
        y += lineH;
      });
    }
    function writeBullet(text, size = 11) {
      if (!text) return;
      setFont(size, 'normal');
      setColor(TEXT_PRIMARY);
      const indent = 14;
      const lines = doc.splitTextToSize(String(text), CONTENT_W - indent);
      const lineH = size * 1.4;
      lines.forEach((line, i) => {
        ensureSpace(lineH);
        if (i === 0) {
          setColor(ACCENT_GREEN);
          doc.text('•', MARGIN, y);
          setColor(TEXT_PRIMARY);
        }
        doc.text(line, MARGIN + indent, y);
        y += lineH;
      });
    }
    function writeLabelValue(label, value) {
      setFont(9, 'bold');
      setColor(TEXT_MUTED);
      const labelText = String(label).toUpperCase();
      doc.text(labelText, MARGIN, y);
      const labelW = doc.getTextWidth(labelText) + 8;
      setFont(10, 'normal');
      setColor(TEXT_PRIMARY);
      const valueLines = doc.splitTextToSize(String(value || '—'), CONTENT_W - labelW);
      doc.text(valueLines[0], MARGIN + labelW, y);
      y += 13;
      valueLines.slice(1).forEach(l => {
        ensureSpace(13);
        doc.text(l, MARGIN + labelW, y);
        y += 13;
      });
    }

    // ── HEADER BAND ──
    doc.setFillColor(17, 24, 39); // dark band
    doc.rect(0, 0, PAGE_W, MARGIN - 10, 'F');
    // Logo squircle
    doc.setFillColor(ACCENT_BLUE[0], ACCENT_BLUE[1], ACCENT_BLUE[2]);
    doc.roundedRect(MARGIN, 16, 20, 20, 3, 3, 'F');
    doc.setDrawColor(ACCENT_GREEN[0], ACCENT_GREEN[1], ACCENT_GREEN[2]);
    doc.setLineWidth(1.4);
    doc.line(MARGIN + 5, 30, MARGIN + 9, 24);
    doc.line(MARGIN + 9, 24, MARGIN + 12, 27);
    doc.line(MARGIN + 12, 27, MARGIN + 16, 21);
    setFont(14, 'bold');
    doc.setTextColor(255, 255, 255);
    doc.text('SignalStack AI — Weekly Briefing', MARGIN + 28, 30);
    setFont(10, 'normal');
    doc.setTextColor(156, 163, 175);
    const dateLabel = `Week ending ${weekEnd}`;
    const dateW = doc.getTextWidth(dateLabel);
    doc.text(dateLabel, PAGE_W - MARGIN - dateW, 30);

    y = MARGIN + 18;

    // ── TL;DR ──
    const tldr = synthesizeTldr(d);
    if (tldr.bullets.length) {
      drawSectionHeader('TL;DR', ACCENT_BLUE);
      if (tldr.chips && tldr.chips.length) {
        setFont(9, 'bold');
        setColor(ACCENT_BLUE);
        let cx = MARGIN;
        tldr.chips.forEach(chip => {
          const w = doc.getTextWidth(chip) + 12;
          if (cx + w > PAGE_W - MARGIN) { y += 16; cx = MARGIN; ensureSpace(16); }
          doc.setDrawColor(ACCENT_BLUE[0], ACCENT_BLUE[1], ACCENT_BLUE[2]);
          doc.setLineWidth(0.6);
          doc.roundedRect(cx, y - 8, w, 13, 6, 6, 'S');
          doc.text(chip.toUpperCase(), cx + 6, y + 1);
          cx += w + 4;
        });
        y += 14;
      }
      tldr.bullets.forEach(b => writeBullet(b, 11));
      y += 6;
    }

    // ── MACRO OVERVIEW ──
    const ms = d.market_summary || {};
    drawSectionHeader('Macro Overview', ACCENT_GREEN);
    const ir = ms.index_returns || {};
    const sp = ms.sp500_weekly || ir.sp500_weekly || '—';
    const nq = ms.nasdaq_weekly || ir.nasdaq_weekly || '—';
    const ru = ms.russell_weekly || ir.russell_weekly || '—';
    writeLabelValue('S&P 500', sp);
    writeLabelValue('Nasdaq', nq);
    writeLabelValue('Russell 2000', ru);
    if (ms.macro_regime) writeLabelValue('Regime', `${ms.macro_regime}${ms.regime_description ? ' — ' + ms.regime_description : ''}`);
    y += 4;
    if (ms.narrative) {
      writeParagraph(ms.narrative, 10.5, 'normal', TEXT_PRIMARY);
      y += 6;
    }

    // Trends
    if (Array.isArray(d.trends) && d.trends.length) {
      drawSectionHeader('Key Trends', ACCENT_GREEN);
      d.trends.forEach(t => {
        const title = t.title || t.name || '';
        const detail = t.detail || t.description || '';
        if (title) { writeParagraph(title, 11, 'bold'); }
        if (detail) { writeParagraph(detail, 10.5); y += 2; }
      });
    }

    // Risks
    if (Array.isArray(d.risks) && d.risks.length) {
      drawSectionHeader('Risks to Watch', RED);
      d.risks.forEach(r => {
        const title = r.title || r.name || '';
        const detail = r.detail || r.description || '';
        if (title) { writeParagraph(title, 11, 'bold'); }
        if (detail) { writeParagraph(detail, 10.5); y += 2; }
      });
    }

    // Value Picks
    if (Array.isArray(d.value_picks) && d.value_picks.length) {
      drawSectionHeader('Top Value Stocks', ACCENT_GREEN);
      d.value_picks.forEach((v, i) => {
        ensureSpace(40);
        setFont(12, 'bold');
        setColor(TEXT_PRIMARY);
        const head = `#${i + 1}  ${v.ticker}  —  ${v.name || ''}`;
        doc.text(head, MARGIN, y);
        // price right-aligned
        const price = v.price || v.current_price || '';
        if (price) {
          setFont(11, 'bold');
          setColor(ACCENT_BLUE);
          const pStr = String(price);
          doc.text(pStr, PAGE_W - MARGIN - doc.getTextWidth(pStr), y);
        }
        y += 14;
        setFont(9, 'normal');
        setColor(TEXT_MUTED);
        const stats = [
          `${v.off_high_pct || v.pct_off_high || ''} off 52w high`,
          `P/E ${v.pe_ratio || 'N/A'}`,
          `EV/EBITDA ${v.ev_ebitda || 'N/A'}`,
          `Rev Gr ${v.rev_growth || v.revenue_growth || 'N/A'}`,
          `FCF Yld ${v.fcf_yield || 'N/A'}`,
        ].filter(s => s && !s.startsWith(' off')).join('   ·   ');
        if (stats) { doc.text(stats, MARGIN, y); y += 12; }
        if (v.why_undervalued) {
          setFont(9, 'bold'); setColor(ACCENT_GREEN);
          doc.text('WHY UNDERVALUED', MARGIN, y); y += 11;
          writeParagraph(v.why_undervalued, 10, 'normal', TEXT_PRIMARY);
        }
        if (v.bull_case) {
          setFont(9, 'bold'); setColor(ACCENT_GREEN);
          doc.text('BULL CASE', MARGIN, y); y += 11;
          writeParagraph(v.bull_case, 10, 'normal', TEXT_PRIMARY);
        }
        y += 6;
      });
    }

    // Momentum Picks
    if (Array.isArray(d.momentum_picks) && d.momentum_picks.length) {
      drawSectionHeader('Top Momentum Stocks', ACCENT_BLUE);
      d.momentum_picks.forEach((m, i) => {
        ensureSpace(40);
        setFont(12, 'bold');
        setColor(TEXT_PRIMARY);
        const head = `#${i + 1}  ${m.ticker}  —  ${m.name || ''}`;
        doc.text(head, MARGIN, y);
        y += 14;
        setFont(9, 'normal'); setColor(TEXT_MUTED);
        const stats = [
          `1W ${m.perf_1w || m.one_week || '—'}`,
          `1M ${m.perf_1m || m.one_month || '—'}`,
          `3M ${m.perf_3m || m.three_month || '—'}`,
          `Rev Gr ${m.rev_growth || m.revenue_growth || 'N/A'}`,
        ].join('   ·   ');
        doc.text(stats, MARGIN, y); y += 12;
        if (m.catalyst) {
          setFont(9, 'bold'); setColor(ACCENT_BLUE);
          doc.text('CATALYST', MARGIN, y); y += 11;
          writeParagraph(m.catalyst, 10, 'normal', TEXT_PRIMARY);
        }
        if (m.risk_reward) {
          setFont(9, 'bold'); setColor(ACCENT_BLUE);
          doc.text('RISK / REWARD', MARGIN, y); y += 11;
          writeParagraph(m.risk_reward, 10, 'normal', TEXT_PRIMARY);
        }
        y += 6;
      });
    }

    // Earnings highlights (from watchlist updates if present)
    const updates = d.watchlist_updates || d.watchlist_movers || d.earnings_highlights || [];
    if (updates.length) {
      drawSectionHeader('Earnings & Watchlist Highlights', ACCENT_GREEN);
      updates.forEach(u => {
        ensureSpace(30);
        // Beat / miss / move signal
        let signalLabel = '';
        let signalColor = TEXT_MUTED;
        if (u.result) {
          const r = String(u.result).toLowerCase();
          if (r.includes('beat')) { signalLabel = 'BEAT'; signalColor = ACCENT_GREEN; }
          else if (r.includes('miss')) { signalLabel = 'MISS'; signalColor = RED; }
          else { signalLabel = r.toUpperCase(); }
        } else if (u.price_change_30d) {
          const pct = parseFloat(String(u.price_change_30d).replace('%', '').replace('+', ''));
          if (!isNaN(pct)) {
            signalLabel = `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}% 30D`;
            signalColor = pct >= 0 ? ACCENT_GREEN : RED;
          }
        }
        setFont(11, 'bold'); setColor(TEXT_PRIMARY);
        doc.text(`${u.ticker || ''}  —  ${u.company_name || u.name || ''}`, MARGIN, y);
        if (signalLabel) {
          setFont(9, 'bold'); setColor(signalColor);
          const lw = doc.getTextWidth(signalLabel) + 10;
          doc.setDrawColor(signalColor[0], signalColor[1], signalColor[2]);
          doc.setLineWidth(0.6);
          doc.roundedRect(PAGE_W - MARGIN - lw, y - 9, lw, 13, 3, 3, 'S');
          doc.text(signalLabel, PAGE_W - MARGIN - lw + 5, y + 1);
        }
        y += 14;
        if (u.headline) { writeParagraph(u.headline, 10.5, 'bold', TEXT_PRIMARY); }
        if (u.detail) { writeParagraph(u.detail, 10, 'normal', TEXT_PRIMARY); y += 2; }
        y += 4;
      });
    }

    // ── FOOTER: page numbers (Page X of Y) ──
    const pageCount = doc.internal.getNumberOfPages();
    for (let p = 1; p <= pageCount; p++) {
      doc.setPage(p);
      setFont(9, 'normal');
      setColor(TEXT_MUTED);
      const footerY = PAGE_H - 28;
      // left: app name
      doc.text('SignalStack AI', MARGIN, footerY);
      // center: week ending
      const centerLabel = `Week ending ${weekEnd}`;
      doc.text(centerLabel, PAGE_W / 2 - doc.getTextWidth(centerLabel) / 2, footerY);
      // right: page X of Y
      const pageLabel = `Page ${p} of ${pageCount}`;
      doc.text(pageLabel, PAGE_W - MARGIN - doc.getTextWidth(pageLabel), footerY);
      doc.setDrawColor(BORDER[0], BORDER[1], BORDER[2]);
      doc.setLineWidth(0.3);
      doc.line(MARGIN, footerY - 8, PAGE_W - MARGIN, footerY - 8);
    }

    const filename = `SignalStack_Briefing_${weekEnd}.pdf`;
    doc.save(filename);

    hideBriefingToast();
    showBriefingToast('PDF ready', { type: 'success', duration: 2200 });
  } catch (err) {
    console.error('PDF export failed:', err);
    hideBriefingToast();
    showBriefingToast('Failed to generate PDF', { type: 'error' });
  }
}

// Wire up the Export PDF button
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('wb-export-pdf');
  if (btn) btn.addEventListener('click', exportBriefingPDF);
});

window.exportBriefingPDF = exportBriefingPDF;

window.loadWeeklyBriefing = loadWeeklyBriefing;
