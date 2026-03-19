/* ===== EARNINGS.JS — Earnings section logic ===== */

// --- DOM refs ---
const $earningsStatus = document.getElementById('earnings-status');
const $recentCards = document.getElementById('recent-earnings-cards');
const $upcomingCards = document.getElementById('upcoming-earnings-cards');
const $earningsArchiveBtn = document.getElementById('earnings-archive-btn');
const $earningsNoteOverlay = document.getElementById('earnings-note-overlay');
const $earningsNoteModal = document.getElementById('earnings-note-modal');
const $earningsNoteClose = document.getElementById('earnings-note-close');
const $earningsNoteContent = document.getElementById('earnings-note-content');
const $earningsArchiveOverlay = document.getElementById('earnings-archive-overlay');
const $earningsArchiveClose = document.getElementById('earnings-archive-close');
const $earningsArchiveContent = document.getElementById('earnings-archive-content');

let earningsData = null;

// --- Simple markdown to HTML (no external lib) ---
function mdToHtml(md) {
  let html = md
    // Headers
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Horizontal rules
    .replace(/^---$/gm, '<hr>')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    // Unordered lists
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    // Wrap consecutive <li> in <ul>
    .replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
    // Tables
    .replace(/^\|(.+)\|$/gm, (match) => {
      const cells = match.split('|').filter(c => c.trim());
      return '<tr>' + cells.map(c => {
        const trimmed = c.trim();
        // Check if it's a separator row
        if (/^[-:]+$/.test(trimmed)) return null;
        return `<td>${trimmed}</td>`;
      }).filter(Boolean).join('') + '</tr>';
    });

  // Wrap table rows
  html = html.replace(/((?:<tr>.*<\/tr>\n?)+)/g, (match) => {
    // Check if first row should be header
    const rows = match.trim().split('\n').filter(r => r.includes('<tr>'));
    if (rows.length >= 2) {
      // Remove separator rows (rows with only dashes)
      const filtered = rows.filter(r => !r.includes('---'));
      if (filtered.length > 0) {
        const header = filtered[0].replace(/<td>/g, '<th>').replace(/<\/td>/g, '</th>');
        const body = filtered.slice(1).join('\n');
        return `<table class="earnings-md-table"><thead>${header}</thead><tbody>${body}</tbody></table>`;
      }
    }
    return `<table class="earnings-md-table">${match}</table>`;
  });

  // Paragraphs (lines not already wrapped)
  html = html.split('\n').map(line => {
    const trimmed = line.trim();
    if (!trimmed) return '';
    if (trimmed.startsWith('<')) return line;
    return `<p>${line}</p>`;
  }).join('\n');

  return html;
}

// --- Render earnings cards ---
function renderRecentEarnings(recent) {
  if (!recent || recent.length === 0) {
    $recentCards.innerHTML = '<div class="earnings-empty">No recent earnings in the last 14 days</div>';
    return;
  }
  $recentCards.innerHTML = recent.map(r => {
    const name = (COMMON_NAMES && COMMON_NAMES[r.ticker]) || r.name || r.ticker;
    const revBeat = (r.revenue_beat_miss || '').toLowerCase();
    const epsBeat = (r.eps_beat_miss || '').toLowerCase();
    const revClass = revBeat.includes('beat') ? 'beat' : revBeat.includes('miss') ? 'miss' : 'inline';
    const epsClass = epsBeat.includes('beat') ? 'beat' : epsBeat.includes('miss') ? 'miss' : 'inline';
    const stockRx = r.stock_reaction || '';
    const stockClass = stockRx.includes('+') ? 'positive' : stockRx.includes('-') ? 'negative' : '';

    return `<div class="earnings-card reported" data-ticker="${r.ticker}" data-date="${r.earnings_date}" data-type="post">
      <div class="earnings-card-header">
        <span class="earnings-card-ticker">${r.ticker}</span>
        <span class="earnings-card-name">${name}</span>
        <span class="earnings-card-date">${r.earnings_date} · ${r.days_since}d ago</span>
      </div>
      <div class="earnings-card-metrics">
        <div class="earnings-metric">
          <span class="metric-label">Rev</span>
          <span class="metric-value">${r.revenue_actual || '—'}</span>
          <span class="metric-tag ${revClass}">${r.revenue_beat_miss || ''}</span>
        </div>
        <div class="earnings-metric">
          <span class="metric-label">EPS</span>
          <span class="metric-value">${r.eps_actual || '—'}</span>
          <span class="metric-tag ${epsClass}">${r.eps_beat_miss || ''}</span>
        </div>
      </div>
      ${r.fiscal_quarter ? `<div class="earnings-card-fq">${r.fiscal_quarter}</div>` : ''}
      <div class="earnings-card-reaction ${stockClass}">${stockRx || 'No data'}</div>
      <button class="earnings-note-btn" title="View post-earnings note">View Note →</button>
    </div>`;
  }).join('');

  // Attach click handlers
  $recentCards.querySelectorAll('.earnings-card').forEach(card => {
    card.querySelector('.earnings-note-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      openEarningsNote(card.dataset.ticker, card.dataset.date, card.dataset.type);
    });
  });
}

function renderUpcomingEarnings(upcoming) {
  // Only show tickers reporting in next 14 days
  const soon = (upcoming || []).filter(u => u.days_until <= 14);
  // Also show a "next up" preview of tickers 15-45 days out
  const nextUp = (upcoming || []).filter(u => u.days_until > 14 && u.days_until <= 45);

  if (soon.length === 0 && nextUp.length === 0) {
    $upcomingCards.innerHTML = '<div class="earnings-empty">No upcoming earnings in the next 14 days</div>';
    return;
  }

  let html = '';

  if (soon.length > 0) {
    html += soon.map(u => {
      const name = (COMMON_NAMES && COMMON_NAMES[u.ticker]) || u.name || u.ticker;
      return `<div class="earnings-card upcoming" data-ticker="${u.ticker}" data-date="${u.earnings_date}" data-type="pre">
        <div class="earnings-card-header">
          <span class="earnings-card-ticker">${u.ticker}</span>
          <span class="earnings-card-name">${name}</span>
          <span class="earnings-card-date">${u.earnings_date} · in ${u.days_until}d</span>
        </div>
        <div class="earnings-card-countdown">Reports in <strong>${u.days_until}</strong> day${u.days_until !== 1 ? 's' : ''}</div>
        <button class="earnings-note-btn" title="View pre-earnings note">View Note →</button>
      </div>`;
    }).join('');
  }

  if (nextUp.length > 0) {
    html += `<div class="earnings-next-up">
      <h4 class="earnings-next-up-title">Next Up (15–45 days)</h4>
      <div class="earnings-next-up-list">
        ${nextUp.map(u => {
          const name = (COMMON_NAMES && COMMON_NAMES[u.ticker]) || u.name || u.ticker;
          return `<div class="earnings-next-up-item">
            <span class="nui-ticker">${u.ticker}</span>
            <span class="nui-name">${name}</span>
            <span class="nui-date">${u.earnings_date}</span>
            <span class="nui-days">${u.days_until}d</span>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }

  $upcomingCards.innerHTML = html;

  // Attach note handlers for soon cards
  $upcomingCards.querySelectorAll('.earnings-card').forEach(card => {
    const btn = card.querySelector('.earnings-note-btn');
    if (btn) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        openEarningsNote(card.dataset.ticker, card.dataset.date, card.dataset.type);
      });
    }
  });
}

// --- Open earnings note in modal ---
async function openEarningsNote(ticker, date, type) {
  $earningsNoteContent.innerHTML = '<div class="earnings-note-loading">Loading note...</div>';
  $earningsNoteOverlay.classList.add('active');
  document.body.style.overflow = 'hidden';

  try {
    const url = `${BACKEND_URL}/earnings-note?ticker=${encodeURIComponent(ticker)}&date=${encodeURIComponent(date)}&type=${encodeURIComponent(type)}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const data = await resp.json();
    if (data.error) {
      $earningsNoteContent.innerHTML = `<div class="earnings-note-error">Note not available: ${data.error}</div>`;
    } else {
      $earningsNoteContent.innerHTML = mdToHtml(data.content);
    }
  } catch (e) {
    $earningsNoteContent.innerHTML = `<div class="earnings-note-error">Failed to load note</div>`;
  }
}

function closeEarningsNote() {
  $earningsNoteOverlay.classList.remove('active');
  document.body.style.overflow = '';
}

$earningsNoteClose.addEventListener('click', closeEarningsNote);
$earningsNoteOverlay.addEventListener('click', (e) => {
  if (e.target === $earningsNoteOverlay) closeEarningsNote();
});

// --- Archive modal ---
function openArchive() {
  $earningsArchiveOverlay.classList.add('active');
  document.body.style.overflow = 'hidden';
  // Show all upcoming earnings as a full calendar view
  if (!earningsData) {
    $earningsArchiveContent.innerHTML = '<div class="earnings-empty">No data</div>';
    return;
  }
  const all = earningsData.upcoming || [];
  if (all.length === 0) {
    $earningsArchiveContent.innerHTML = '<div class="earnings-empty">No upcoming earnings data</div>';
    return;
  }

  // Group by month
  const byMonth = {};
  all.forEach(u => {
    const month = u.earnings_date.substring(0, 7); // YYYY-MM
    if (!byMonth[month]) byMonth[month] = [];
    byMonth[month].push(u);
  });

  let html = '';
  Object.keys(byMonth).sort().forEach(month => {
    const monthName = new Date(month + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    const items = byMonth[month];
    html += `<div class="archive-month">
      <h3 class="archive-month-title">${monthName}</h3>
      <table class="archive-table">
        <thead><tr><th>Date</th><th>Ticker</th><th>Company</th><th>Days</th></tr></thead>
        <tbody>
          ${items.map(u => {
            const name = (COMMON_NAMES && COMMON_NAMES[u.ticker]) || u.name || u.ticker;
            return `<tr>
              <td>${u.earnings_date}</td>
              <td class="archive-ticker">${u.ticker}</td>
              <td>${name}</td>
              <td>${u.days_until}d</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
  });

  $earningsArchiveContent.innerHTML = html;
}

function closeArchive() {
  $earningsArchiveOverlay.classList.remove('active');
  document.body.style.overflow = '';
}

$earningsArchiveBtn.addEventListener('click', openArchive);
$earningsArchiveClose.addEventListener('click', closeArchive);
$earningsArchiveOverlay.addEventListener('click', (e) => {
  if (e.target === $earningsArchiveOverlay) closeArchive();
});

// --- Fetch earnings data from backend ---
async function fetchEarnings() {
  if (!(await checkBackend())) return;
  try {
    $earningsStatus.textContent = 'updating...';
    const url = `${BACKEND_URL}/earnings`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const data = await resp.json();
    if (data.error && !data.recent) {
      $earningsStatus.textContent = 'no data';
      return;
    }
    earningsData = data;
    renderRecentEarnings(data.recent || []);
    renderUpcomingEarnings(data.upcoming || []);
    const total = (data.recent || []).length + ((data.upcoming || []).filter(u => u.days_until <= 14)).length;
    $earningsStatus.textContent = total > 0 ? `${total} active` : 'up to date';
  } catch (e) {
    console.warn('Earnings fetch failed:', e);
    $earningsStatus.textContent = 'error';
  }
}
