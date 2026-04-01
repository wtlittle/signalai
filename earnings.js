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
let earningsNotesIndex = null;

// --- Simple markdown to HTML (no external lib) ---
function mdToHtml(md) {
  let html = md
    // Headers
    .replace(/^#### (.+)$/gm, '<h5>$1</h5>')
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
    // Numbered lists
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // Wrap consecutive <li> in <ul>
    .replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
    // Tables
    .replace(/^\|(.+)\|$/gm, (match) => {
      const cells = match.split('|').filter(c => c.trim());
      return '<tr>' + cells.map(c => {
        const trimmed = c.trim();
        if (/^[-:]+$/.test(trimmed)) return null;
        return `<td>${trimmed}</td>`;
      }).filter(Boolean).join('') + '</tr>';
    });

  // Wrap table rows
  html = html.replace(/((?:<tr>.*<\/tr>\n?)+)/g, (match) => {
    const rows = match.trim().split('\n').filter(r => r.includes('<tr>'));
    if (rows.length >= 2) {
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

// --- Load earnings notes index ---
async function loadEarningsNotesIndex() {
  if (earningsNotesIndex) return earningsNotesIndex;
  try {
    const resp = await fetch('earnings_notes_index.json?v=' + Date.now());
    earningsNotesIndex = await resp.json();
    return earningsNotesIndex;
  } catch (e) {
    console.warn('Failed to load earnings notes index:', e);
    return null;
  }
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
  const soon = (upcoming || []).filter(u => u.days_until <= 14);
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
// Now loads from static .md files first, falls back to backend
async function openEarningsNote(ticker, date, type) {
  $earningsNoteContent.innerHTML = '<div class="earnings-note-loading">Loading note...</div>';
  $earningsNoteOverlay.classList.add('active');
  document.body.style.overflow = 'hidden';

  try {
    // Strategy 1: Look up the note path from the index
    const index = await loadEarningsNotesIndex();
    let notePath = null;
    if (index && index.notes) {
      const match = index.notes.find(n =>
        n.ticker === ticker && n.earnings_date === date && n.type === type
      );
      if (match) notePath = match.path;
    }
    // Also check active_pre_earnings / active_post_earnings
    if (!notePath && index) {
      const activeList = type === 'post' ? (index.active_post_earnings || []) : (index.active_pre_earnings || []);
      const match = activeList.find(n => n.ticker === ticker && n.earnings_date === date);
      if (match) notePath = match.note_path;
      // Check archived
      if (!notePath && index.archived) {
        const archiveMatch = index.archived.find(n => n.ticker === ticker && n.earnings_date === date);
        if (archiveMatch) notePath = archiveMatch.archive_path;
      }
    }

    // Strategy 2: Try conventional file paths
    if (!notePath) {
      const prefix = type === 'post' ? 'notes/post_earnings' : 'notes/pre_earnings';
      notePath = `${prefix}/${ticker}_${date}.md`;
    }

    // Try loading from static file
    let loaded = false;
    const paths = [
      notePath,
      // Also try archive path
      notePath.replace('notes/', 'archive/')
    ];

    for (const p of paths) {
      try {
        const resp = await fetch(p + '?v=' + Date.now(), { signal: AbortSignal.timeout(5000) });
        if (resp.ok) {
          const contentType = resp.headers.get('content-type') || '';
          // Check it's actually markdown, not an HTML error page
          if (!contentType.includes('html') || contentType.includes('text/plain')) {
            const text = await resp.text();
            if (text.startsWith('#') || text.includes('## ')) {
              $earningsNoteContent.innerHTML = mdToHtml(text);
              loaded = true;
              break;
            }
          }
        }
      } catch (e) { /* try next path */ }
    }

    if (!loaded) {
      // Strategy 3: Fall back to backend
      try {
        const url = `${BACKEND_URL}/earnings-note?ticker=${encodeURIComponent(ticker)}&date=${encodeURIComponent(date)}&type=${encodeURIComponent(type)}`;
        const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
        const data = await resp.json();
        if (!data.error) {
          $earningsNoteContent.innerHTML = mdToHtml(data.content);
          loaded = true;
        }
      } catch (e) { /* backend unavailable */ }
    }

    if (!loaded) {
      $earningsNoteContent.innerHTML = `<div class="earnings-note-error">Note not available for ${ticker} (${date})</div>`;
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

// --- Archive modal — now shows PAST earnings notes, not just upcoming calendar ---
async function openArchive() {
  $earningsArchiveOverlay.classList.add('active');
  document.body.style.overflow = 'hidden';

  const index = await loadEarningsNotesIndex();
  
  // Build a flat notes array from either format
  let allNotes = [];
  if (index && index.notes && index.notes.length > 0) {
    allNotes = index.notes;
  } else if (index) {
    // Build from structured arrays
    (index.active_pre_earnings || []).forEach(n => {
      allNotes.push({ ...n, type: 'pre', status: 'active', name: n.name || n.ticker });
    });
    (index.active_post_earnings || []).forEach(n => {
      allNotes.push({ ...n, type: 'post', status: 'active', name: n.name || n.ticker });
    });
    (index.archived || []).forEach(n => {
      const type = (n.type || '').includes('post') ? 'post' : 'pre';
      allNotes.push({ ...n, type, status: 'archived', name: n.name || n.ticker,
        path: n.archive_path || n.note_path });
    });
  }

  if (allNotes.length === 0) {
    $earningsArchiveContent.innerHTML = '<div class="earnings-empty">No archived earnings notes</div>';
    return;
  }

  // Get all post-earnings notes (both active and archived)
  const postNotes = allNotes
    .filter(n => n.type === 'post')
    .sort((a, b) => b.earnings_date.localeCompare(a.earnings_date));

  const archivedNotes = postNotes.filter(n => n.status === 'archived');
  const activeNotes = postNotes.filter(n => n.status === 'active');

  // Also get pre-earnings notes
  const preNotes = allNotes
    .filter(n => n.type === 'pre')
    .sort((a, b) => a.earnings_date.localeCompare(b.earnings_date));

  let html = '';

  // Active post-earnings
  if (activeNotes.length > 0) {
    html += `<div class="archive-section">
      <h3 class="archive-section-title">Active Post-Earnings Notes</h3>
      <div class="archive-notes-grid">
        ${activeNotes.map(n => renderArchiveCard(n)).join('')}
      </div>
    </div>`;
  }

  // Pre-earnings
  if (preNotes.length > 0) {
    html += `<div class="archive-section">
      <h3 class="archive-section-title">Pre-Earnings Notes</h3>
      <div class="archive-notes-grid">
        ${preNotes.map(n => renderArchiveCard(n)).join('')}
      </div>
    </div>`;
  }

  // Archived
  if (archivedNotes.length > 0) {
    html += `<div class="archive-section">
      <h3 class="archive-section-title">Archived Notes (older than 14 days)</h3>
      <div class="archive-notes-grid">
        ${archivedNotes.map(n => renderArchiveCard(n)).join('')}
      </div>
    </div>`;
  }

  $earningsArchiveContent.innerHTML = html;

  // Attach click handlers
  $earningsArchiveContent.querySelectorAll('.archive-note-card').forEach(card => {
    card.addEventListener('click', () => {
      closeArchive();
      openEarningsNote(card.dataset.ticker, card.dataset.date, card.dataset.type);
    });
  });
}

function renderArchiveCard(note) {
  const name = (COMMON_NAMES && COMMON_NAMES[note.ticker]) || note.name || note.ticker;
  const isPost = note.type === 'post';
  const reactionClass = (note.reaction || '').includes('+') ? 'positive'
    : (note.reaction || '').includes('-') ? 'negative' : '';

  return `<div class="archive-note-card ${note.status === 'archived' ? 'archived' : ''}" 
    data-ticker="${note.ticker}" data-date="${note.earnings_date}" data-type="${note.type}">
    <div class="archive-card-header">
      <span class="archive-card-ticker">${note.ticker}</span>
      <span class="archive-card-name">${name}</span>
      <span class="archive-card-type ${note.type}">${isPost ? 'Post' : 'Pre'}</span>
    </div>
    <div class="archive-card-date">${note.earnings_date}</div>
    ${note.headline ? `<div class="archive-card-headline">${note.headline}</div>` : ''}
    ${isPost ? `<div class="archive-card-metrics">
      ${note.revenue ? `<span class="archive-metric">Rev: ${note.revenue}</span>` : ''}
      ${note.eps ? `<span class="archive-metric">EPS: ${note.eps}</span>` : ''}
      ${note.reaction ? `<span class="archive-metric ${reactionClass}">Rx: ${note.reaction}</span>` : ''}
    </div>` : ''}
    <div class="archive-card-cta">View Note →</div>
  </div>`;
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

// --- Fetch earnings data from backend / Supabase ---
async function fetchEarnings() {
  // Try backend first
  if (await checkBackend()) {
    try {
      $earningsStatus.textContent = 'updating...';
      const url = `${BACKEND_URL}/earnings`;
      const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
      const data = await resp.json();
      if (!data.error || data.recent) {
        earningsData = data;
        renderRecentEarnings(data.recent || []);
        renderUpcomingEarnings(data.upcoming || []);
        const total = (data.recent || []).length + ((data.upcoming || []).filter(u => u.days_until <= 14)).length;
        $earningsStatus.textContent = total > 0 ? `${total} active` : 'up to date';
        return;
      }
    } catch (e) {
      console.warn('Backend earnings fetch failed:', e);
    }
  }

  // Fallback: build earnings data from the notes index + calendar
  try {
    $earningsStatus.textContent = 'loading...';
    const index = await loadEarningsNotesIndex();
    const calResp = await fetch('earnings_calendar.json?v=' + Date.now());
    const calData = await calResp.json();

    const now = new Date();
    const recent = [];
    const upcoming = [];

    // Build recent from post-earnings notes in index
    // Support both flat notes[] array and active_post_earnings[] structure
    let postNotes = [];
    if (index && index.notes) {
      postNotes = index.notes.filter(n => n.type === 'post' && n.status === 'active');
    } else if (index && index.active_post_earnings) {
      postNotes = index.active_post_earnings.map(n => ({
        ticker: n.ticker,
        name: n.name || n.ticker,
        type: 'post',
        earnings_date: n.earnings_date,
        status: 'active',
        revenue: n.revenue || '',
        eps: n.eps || '',
        reaction: n.reaction || ''
      }));
    }

    postNotes.forEach(n => {
      const earningsDate = new Date(n.earnings_date + 'T00:00:00');
      const daysSince = Math.floor((now - earningsDate) / 86400000);
      if (daysSince >= 0 && daysSince <= 14) {
        recent.push({
          ticker: n.ticker,
          name: n.name,
          earnings_date: n.earnings_date,
          days_since: daysSince,
          revenue_actual: n.revenue || '',
          eps_actual: n.eps || '',
          revenue_beat_miss: '',
          eps_beat_miss: '',
          stock_reaction: n.reaction || '',
          fiscal_quarter: n.fiscal_quarter || ''
        });
      }
    });

    // Also pull recent from calendar post_earnings if not already added
    if (calData && calData.post_earnings) {
      calData.post_earnings.forEach(p => {
        if (!recent.some(r => r.ticker === p.ticker && r.earnings_date === (p.date || p.earnings_date))) {
          const date = p.date || p.earnings_date;
          const earningsDate = new Date(date + 'T00:00:00');
          const daysSince = Math.floor((now - earningsDate) / 86400000);
          if (daysSince >= 0 && daysSince <= 14) {
            const surprise = p.surprise_pct;
            const epsBeat = surprise > 0 ? `Beat +${surprise.toFixed(1)}%` : surprise < 0 ? `Miss ${surprise.toFixed(1)}%` : '';
            const revActual = p.revenue_actual ? ('$' + p.revenue_actual) : '';
            const revBeat = p.revenue_actual && p.revenue_estimate ? 
              (parseFloat(String(p.revenue_actual).replace(/[^0-9.]/g,'')) > parseFloat(String(p.revenue_estimate).replace(/[^0-9.]/g,'')) ? 'Beat' : 'Miss') : '';
            recent.push({
              ticker: p.ticker,
              name: p.name,
              earnings_date: date,
              days_since: daysSince,
              revenue_actual: revActual || (p.revenue_actual || ''),
              eps_actual: p.eps_actual != null ? '$' + p.eps_actual : '',
              revenue_beat_miss: revBeat,
              eps_beat_miss: epsBeat,
              stock_reaction: p.note || '',
              fiscal_quarter: p.quarter || ''
            });
          }
        }
      });
    }

    // Build upcoming from calendar
    // Support both upcoming[] and pre_earnings[] + upcoming_notable[]
    if (calData && calData.upcoming && calData.upcoming.length > 0) {
      calData.upcoming.forEach(u => {
        const date = u.earnings_date || u.date;
        const earningsDate = new Date(date + 'T00:00:00');
        const daysUntil = Math.floor((earningsDate - now) / 86400000);
        if (daysUntil >= 0) {
          upcoming.push({
            ticker: u.ticker,
            name: u.name,
            earnings_date: date,
            days_until: daysUntil,
            status: u.status
          });
        }
      });
    } else {
      // Fallback: combine pre_earnings + upcoming_notable
      const combined = [...(calData.pre_earnings || []), ...(calData.upcoming_notable || [])];
      const seen = new Set();
      combined.forEach(u => {
        const date = u.date || u.earnings_date;
        if (!date || seen.has(u.ticker)) return;
        seen.add(u.ticker);
        const earningsDate = new Date(date + 'T00:00:00');
        const daysUntil = Math.floor((earningsDate - now) / 86400000);
        if (daysUntil >= 0) {
          upcoming.push({
            ticker: u.ticker,
            name: u.name,
            earnings_date: date,
            days_until: daysUntil,
            status: u.status || 'upcoming'
          });
        }
      });
    }

    earningsData = { recent, upcoming };
    renderRecentEarnings(recent);
    renderUpcomingEarnings(upcoming);
    const total = recent.length + upcoming.filter(u => u.days_until <= 14).length;
    $earningsStatus.textContent = total > 0 ? `${total} active` : 'up to date';
  } catch (e) {
    console.warn('Earnings fallback failed:', e);
    $earningsStatus.textContent = 'error';
  }
}

// --- Earnings Calendar Grid ---
let ecalData = null;
let ecalViewMonth = null;

async function loadEarningsCalendarData() {
  try {
    const resp = await fetch('earnings_calendar.json?v=' + Date.now());
    ecalData = await resp.json();
  } catch (e) {
    console.warn('Calendar data load failed:', e);
    ecalData = { upcoming: [] };
  }
}

function renderEarningsCalendarGrid() {
  const $grid = document.getElementById('earnings-calendar-grid');
  const $status = document.getElementById('calendar-status');
  if (!$grid || !ecalData) return;

  const now = new Date();
  if (!ecalViewMonth) ecalViewMonth = new Date(now.getFullYear(), now.getMonth(), 1);

  const year = ecalViewMonth.getFullYear();
  const month = ecalViewMonth.getMonth();
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const startDow = firstDay.getDay();
  const daysInMonth = lastDay.getDate();

  const recentTickers = (earningsData && earningsData.recent) ? earningsData.recent : [];

  // Build a map of date -> [{ticker, type}]
  const dateMap = {};
  // Support both upcoming[] and pre_earnings[]/upcoming_notable[] + post_earnings[]
  let calEntries = ecalData.upcoming || [];
  if (calEntries.length === 0) {
    // Fallback: combine pre_earnings + upcoming_notable, dedup
    const combined = [...(ecalData.pre_earnings || []), ...(ecalData.upcoming_notable || [])];
    const seen = new Set();
    calEntries = combined.filter(u => {
      const key = u.ticker + '_' + (u.date || u.earnings_date);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).map(u => ({ ...u, earnings_date: u.date || u.earnings_date }));
  }
  calEntries.forEach(u => {
    const date = u.earnings_date || u.date;
    if (!dateMap[date]) dateMap[date] = [];
    dateMap[date].push({ ticker: u.ticker, name: u.name, type: 'pre' });
  });
  // Also add post_earnings to the calendar
  (ecalData.post_earnings || []).forEach(p => {
    const date = p.date || p.earnings_date;
    if (!dateMap[date]) dateMap[date] = [];
    if (!dateMap[date].some(x => x.ticker === p.ticker)) {
      dateMap[date].push({ ticker: p.ticker, name: p.name, type: 'post' });
    }
  });
  recentTickers.forEach(r => {
    if (!dateMap[r.earnings_date]) dateMap[r.earnings_date] = [];
    if (!dateMap[r.earnings_date].some(x => x.ticker === r.ticker)) {
      dateMap[r.earnings_date].push({ ticker: r.ticker, name: r.name, type: 'post' });
    }
  });

  // Navigation
  const monthLabel = firstDay.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
  if ($status) {
    $status.innerHTML = `
      <div class="ecal-nav">
        <button class="ecal-nav-btn" id="ecal-prev">&#8592;</button>
        <span class="ecal-month-label">${monthLabel}</span>
        <button class="ecal-nav-btn" id="ecal-next">&#8594;</button>
      </div>`;
  }

  // Day headers
  const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  let html = dayNames.map(d => `<div class="ecal-day-header">${d}</div>`).join('');

  // Leading blanks
  const prevMonth = new Date(year, month, 0);
  for (let i = startDow - 1; i >= 0; i--) {
    const d = prevMonth.getDate() - i;
    const dateStr = fmtDate(new Date(year, month - 1, d));
    const tickers = dateMap[dateStr] || [];
    html += renderCalCell(d, dateStr, tickers, true, now);
  }

  // Current month days
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = fmtDate(new Date(year, month, d));
    const tickers = dateMap[dateStr] || [];
    html += renderCalCell(d, dateStr, tickers, false, now);
  }

  // Trailing blanks
  const totalCells = startDow + daysInMonth;
  const remainder = totalCells % 7;
  if (remainder > 0) {
    for (let d = 1; d <= 7 - remainder; d++) {
      const dateStr = fmtDate(new Date(year, month + 1, d));
      const tickers = dateMap[dateStr] || [];
      html += renderCalCell(d, dateStr, tickers, true, now);
    }
  }

  $grid.innerHTML = html;

  // Nav handlers
  const prevBtn = document.getElementById('ecal-prev');
  const nextBtn = document.getElementById('ecal-next');
  if (prevBtn) prevBtn.addEventListener('click', () => {
    ecalViewMonth = new Date(year, month - 1, 1);
    renderEarningsCalendarGrid();
  });
  if (nextBtn) nextBtn.addEventListener('click', () => {
    ecalViewMonth = new Date(year, month + 1, 1);
    renderEarningsCalendarGrid();
  });

  // Click on ticker chips
  $grid.querySelectorAll('.ecal-ticker-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      openEarningsNote(chip.dataset.ticker, chip.dataset.date, chip.dataset.type);
    });
  });
}

function renderCalCell(day, dateStr, tickers, isOtherMonth, now) {
  const todayStr = fmtDate(now);
  const isToday = dateStr === todayStr;
  const cls = ['ecal-cell'];
  if (isToday) cls.push('ecal-today');
  if (isOtherMonth) cls.push('ecal-other-month');
  let chips = '';
  if (tickers.length > 0) {
    chips = '<div class="ecal-tickers">' + tickers.map(t =>
      `<span class="ecal-ticker-chip ecal-${t.type}" data-ticker="${t.ticker}" data-date="${dateStr}" data-type="${t.type}" title="${t.name || t.ticker}">${t.ticker}</span>`
    ).join('') + '</div>';
  }
  return `<div class="${cls.join(' ')}"><div class="ecal-date">${day}</div>${chips}</div>`;
}

function fmtDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

async function renderEarningsCalendar() {
  if (!ecalData) await loadEarningsCalendarData();
  renderEarningsCalendarGrid();
}
