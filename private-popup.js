/* ===================================================================
   PRIVATE-POPUP.JS
   Click-to-detail popup for private companies.
   Triggered by clicking a company name or row in the private table.
   Four tabs:
     Overview   — description, key metrics, stage, model
     Comps      — public comparables with live data from data-snapshot
     Landscape  — competitors (private + public) with differentiation notes
     Thesis     — bull case, bear case, catalysts, key risks
   =================================================================== */

/* ------------------------------------------------------------------
   OVERLAY SETUP
   Re-uses the existing popup-overlay / popup-modal infrastructure.
   We create a second dedicated overlay to avoid conflicts with the
   public ticker popup.
------------------------------------------------------------------ */

let _privatePopupOverlay = null;
let _privatePopupModal = null;

function getOrCreatePrivatePopupDOM() {
  if (_privatePopupOverlay) return { overlay: _privatePopupOverlay, modal: _privatePopupModal };

  const overlay = document.createElement('div');
  overlay.className = 'popup-overlay';
  overlay.id = 'private-detail-overlay';

  overlay.innerHTML = `
    <div class="popup-modal private-detail-modal" id="private-detail-modal">
      <button class="popup-close" id="private-detail-close">&times;</button>
      <div class="private-detail-header" id="private-detail-header"></div>
      <div class="private-detail-tab-bar" id="private-detail-tabs">
        <button class="pd-tab active" data-tab="funding">Funding History</button>
        <button class="pd-tab" data-tab="peer-trajectory">Peer Trajectory</button>
        <button class="pd-tab" data-tab="returns">Returns</button>
        <button class="pd-tab" data-tab="overview">Overview</button>
        <button class="pd-tab" data-tab="comps">Public Comps</button>
        <button class="pd-tab" data-tab="landscape">Landscape</button>
        <button class="pd-tab" data-tab="thesis">Thesis</button>
      </div>
      <div class="private-detail-body" id="private-detail-body"></div>
    </div>`;

  document.body.appendChild(overlay);

  _privatePopupOverlay = overlay;
  _privatePopupModal = overlay.querySelector('#private-detail-modal');

  // Close handlers
  overlay.querySelector('#private-detail-close').addEventListener('click', closePrivatePopup);
  overlay.addEventListener('click', e => { if (e.target === overlay) closePrivatePopup(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closePrivatePopup(); });

  // Tab switching
  overlay.querySelectorAll('.pd-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      overlay.querySelectorAll('.pd-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderPrivateDetailTab(btn.dataset.tab);
    });
  });

  return { overlay, modal: _privatePopupModal };
}

function closePrivatePopup() {
  if (_privatePopupOverlay) {
    _privatePopupOverlay.classList.remove('active');
  }
}

/* ------------------------------------------------------------------
   MAIN ENTRY POINT
------------------------------------------------------------------ */

let _currentPrivateCo = null; // { co, intel }

async function openPrivatePopup(companyName) {
  // Find the company data
  const co = (privateCompanies || []).find(c => c.name === companyName)
    || (typeof DEFAULT_PRIVATE_COMPANIES !== 'undefined'
        ? DEFAULT_PRIVATE_COMPANIES.find(c => c.name === companyName)
        : null);

  if (!co) return;

  const intel = (typeof PRIVATE_INTEL !== 'undefined') ? PRIVATE_INTEL[companyName] : null;
  _currentPrivateCo = { co, intel };

  const { overlay } = getOrCreatePrivatePopupDOM();

  // Reset to Funding History (new default)
  overlay.querySelectorAll('.pd-tab').forEach(b => b.classList.remove('active'));
  overlay.querySelector('[data-tab="funding"]').classList.add('active');

  // Render header
  renderPrivateHeader(co, intel);

  // Render default tab
  renderPrivateDetailTab('funding');

  overlay.classList.add('active');
}

/* ------------------------------------------------------------------
   HEADER
------------------------------------------------------------------ */
function renderPrivateHeader(co, intel) {
  const $header = document.getElementById('private-detail-header');

  const statusHtml = co.status === 'public'
    ? `<span class="pd-status-badge pd-status-public">PUBLIC · ${co.ticker}</span>`
    : co.status === 'ipo_pending'
      ? `<span class="pd-status-badge pd-status-ipo">IPO FILING</span>`
      : `<span class="pd-status-badge pd-status-private">PRIVATE</span>`;

  const stageHtml = intel?.stage
    ? `<span class="pd-stage-tag">${intel.stage}</span>`
    : '';

  const hq = co.headquarters ? `<span class="pd-hq">📍 ${co.headquarters}</span>` : '';

  $header.innerHTML = `
    <div class="pd-header-top">
      <div class="pd-header-name-row">
        <h2 class="pd-company-name">${co.name}</h2>
        ${statusHtml}
        ${stageHtml}
      </div>
      <div class="pd-header-meta">
        <span class="pd-subsector-tag">${co.subsector}</span>
        ${hq}
      </div>
    </div>
    <div class="pd-header-stats">
      <div class="pd-stat">
        <span class="pd-stat-label">Valuation</span>
        <span class="pd-stat-value">${co.valuation || '—'}</span>
      </div>
      <div class="pd-stat">
        <span class="pd-stat-label">Revenue</span>
        <span class="pd-stat-value">${co.revenue || '—'}</span>
      </div>
      <div class="pd-stat">
        <span class="pd-stat-label">Funding</span>
        <span class="pd-stat-value pd-stat-funding">${co.funding || '—'}</span>
      </div>
      <div class="pd-stat">
        <span class="pd-stat-label">Lead Investors</span>
        <span class="pd-stat-value pd-stat-investors">${co.lead_investors || '—'}</span>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------
   TAB ROUTER
------------------------------------------------------------------ */
function renderPrivateDetailTab(tab) {
  const $body = document.getElementById('private-detail-body');
  const { co, intel } = _currentPrivateCo;

  switch (tab) {
    case 'funding':          $body.innerHTML = renderFundingHistoryTab(co, intel); wirePeerTrajectory($body, co); break;
    case 'peer-trajectory':  $body.innerHTML = renderPeerTrajectoryTab(co, intel); wirePeerTrajectory($body, co); break;
    case 'returns':          $body.innerHTML = renderReturnsTab(co, intel); break;
    case 'overview':         $body.innerHTML = renderOverviewTab(co, intel); break;
    case 'comps':            renderCompsTab(co, intel, $body); break;
    case 'landscape':        $body.innerHTML = renderLandscapeTab(co, intel); break;
    case 'thesis':           $body.innerHTML = renderThesisTab(co, intel); break;
  }
}

/* ------------------------------------------------------------------
   TAB — FUNDING HISTORY
------------------------------------------------------------------ */
function renderFundingHistoryTab(co, intel) {
  const traj = window.SignalPrivateTrajectory;
  if (!traj) return '<div class="pd-section">Trajectory module unavailable.</div>';

  const fh = traj.getFundingHistory(co);
  const vh = traj.getValuationHistory(co);
  if (!fh.length && !vh.length) {
    return '<div class="pd-section"><div class="pd-empty">No funding history available for this company yet.</div></div>';
  }

  // Compute cumulative returns vs first tracked round
  const firstVal = vh.length ? vh[0].valuation_usd : null;
  let html = '<div class="pd-section"><div class="pd-section-title">Round-by-Round History</div>';
  html += '<table class="pd-funding-table"><thead><tr>' +
    '<th>Date</th><th>Round</th><th class="num">Raised</th><th class="num">Post-money</th>' +
    '<th>Lead Investors</th><th class="num">Δ Prior</th><th class="num">Cum. vs First</th>' +
    '</tr></thead><tbody>';
  let priorVal = null;
  fh.forEach((r, i) => {
    const delta = priorVal && r.valuation_usd ? traj.formatReturn(priorVal, r.valuation_usd) : '—';
    const cum = firstVal && r.valuation_usd ? traj.formatReturn(firstVal, r.valuation_usd) : '—';
    html += `<tr>` +
      `<td>${r.date || '—'}</td>` +
      `<td>${r.round || '—'}</td>` +
      `<td class="num">${traj.formatUsd(r.amount_usd)}</td>` +
      `<td class="num">${traj.formatUsd(r.valuation_usd)}</td>` +
      `<td>${r.lead_investors || '—'}</td>` +
      `<td class="num ${_returnClass(priorVal, r.valuation_usd)}">${delta}</td>` +
      `<td class="num ${_returnClass(firstVal, r.valuation_usd)}">${cum}</td>` +
      `</tr>`;
    if (r.valuation_usd) priorVal = r.valuation_usd;
  });
  html += '</tbody></table></div>';

  // Inline peer trajectory preview
  html += '<div class="pd-section"><div class="pd-section-title">Valuation Trajectory</div>' +
          '<div class="pd-peer-chart-wrap"><canvas id="pd-peer-chart"></canvas></div>' +
          '<div class="pd-peer-mode-row">' +
          '  <label>Mode <select id="pd-peer-mode">' +
          '    <option value="valuation">Valuation over time</option>' +
          '    <option value="rev_mult">Valuation / revenue</option>' +
          '    <option value="return_first">Return since first tracked round</option>' +
          '    <option value="return_prior">Return since prior round</option>' +
          '  </select></label>' +
          '</div></div>';
  return html;
}

function _returnClass(fromVal, toVal) {
  if (!fromVal || !toVal) return '';
  const r = toVal / fromVal;
  return r > 1.02 ? 'positive' : r < 0.98 ? 'negative' : '';
}

/* ------------------------------------------------------------------
   TAB — PEER TRAJECTORY
------------------------------------------------------------------ */
function renderPeerTrajectoryTab(co, intel) {
  return '<div class="pd-section"><div class="pd-section-title">Peer Trajectory</div>' +
         '<div class="pd-peer-hint">Anchored on valuation trajectory. Revenue / ARR is contextual where available.</div>' +
         '<div class="pd-peer-chart-wrap pd-peer-chart-wrap-large"><canvas id="pd-peer-chart"></canvas></div>' +
         '<div class="pd-peer-mode-row">' +
         '  <label>Mode <select id="pd-peer-mode">' +
         '    <option value="valuation">Valuation over time</option>' +
         '    <option value="rev_mult">Valuation / revenue</option>' +
         '    <option value="return_first">Return since first tracked round</option>' +
         '    <option value="return_prior">Return since prior round</option>' +
         '  </select></label>' +
         '</div>' +
         '<div id="pd-peer-summary" class="pd-peer-summary"></div>' +
         '</div>';
}

/* ------------------------------------------------------------------
   TAB — RETURNS
------------------------------------------------------------------ */
function renderReturnsTab(co, intel) {
  const traj = window.SignalPrivateTrajectory;
  if (!traj) return '<div class="pd-section">Trajectory module unavailable.</div>';
  const vh = traj.getValuationHistory(co);
  if (!vh.length) return '<div class="pd-section"><div class="pd-empty">No valuation history tracked.</div></div>';
  const latest = vh[vh.length - 1];
  const first = vh[0];
  const prior = vh.length >= 2 ? vh[vh.length - 2] : null;
  const latestDate = new Date(latest.date || Date.now());
  const firstDate = new Date(first.date || latest.date);
  const years = Math.max(0.1, (latestDate - firstDate) / (365.25 * 24 * 3600 * 1000));
  const annualized = (latest.valuation_usd && first.valuation_usd && years > 0.5)
    ? ((Math.pow(latest.valuation_usd / first.valuation_usd, 1 / years) - 1) * 100).toFixed(1) + '%'
    : '—';

  const cardsHtml = [
    { label: 'Since prior round', from: prior ? prior.valuation_usd : null, to: latest.valuation_usd, note: prior ? (prior.round + ' → ' + latest.round) : 'No prior round tracked' },
    { label: 'Since first tracked round', from: first.valuation_usd, to: latest.valuation_usd, note: first.round + ' → ' + latest.round },
    { label: 'Since earliest valuation', from: first.valuation_usd, to: latest.valuation_usd, note: first.date + ' → ' + latest.date },
    { label: 'Annualized', custom: annualized, note: years.toFixed(1) + 'y tracked' }
  ].map(c => {
    const val = c.custom || (c.from && c.to ? traj.formatReturn(c.from, c.to) : '—');
    const cls = c.custom ? '' : _returnClass(c.from, c.to);
    return `<div class="pd-return-card"><div class="pd-return-label">${c.label}</div>` +
           `<div class="pd-return-value ${cls}">${val}</div>` +
           `<div class="pd-return-note">${c.note}</div></div>`;
  }).join('');

  return '<div class="pd-section"><div class="pd-section-title">Returns Summary</div>' +
         '<div class="pd-return-grid">' + cardsHtml + '</div></div>';
}

/* Wire peer-trajectory chart */
function wirePeerTrajectory($body, co) {
  const canvas = $body.querySelector('#pd-peer-chart');
  const modeEl = $body.querySelector('#pd-peer-mode');
  if (!canvas || !window.Chart) return;
  const traj = window.SignalPrivateTrajectory;
  if (!traj) return;

  // Peer set: same subsector
  const peers = (privateCompanies || []).filter(c => c.subsector === co.subsector && c.name !== co.name).slice(0, 6);
  const allCos = [co].concat(peers);

  let chart = null;
  function draw() {
    const mode = (modeEl && modeEl.value) || 'valuation';
    const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ec4899', '#a855f7', '#14b8a6', '#f43f5e'];
    const datasets = allCos.map((c, i) => {
      const vh = traj.getValuationHistory(c);
      if (!vh.length) return null;
      let points = [];
      if (mode === 'valuation') {
        points = vh.map(v => ({ x: new Date(v.date), y: v.valuation_usd }));
      } else if (mode === 'rev_mult') {
        const rev = traj.parseValuation(c.revenue) || null;
        if (!rev) return null;
        points = vh.map(v => ({ x: new Date(v.date), y: v.valuation_usd / rev }));
      } else if (mode === 'return_first' || mode === 'return_prior') {
        const base = mode === 'return_first' ? vh[0].valuation_usd : null;
        let prior = null;
        points = vh.map(v => {
          let pct;
          if (mode === 'return_first') {
            pct = base ? ((v.valuation_usd / base) - 1) * 100 : 0;
          } else {
            pct = prior ? ((v.valuation_usd / prior) - 1) * 100 : 0;
          }
          prior = v.valuation_usd;
          return { x: new Date(v.date), y: pct };
        });
      }
      if (!points.length) return null;
      const isSelected = c.name === co.name;
      return {
        label: c.name,
        data: points,
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: 'transparent',
        borderWidth: isSelected ? 3 : 1.2,
        borderDash: isSelected ? [] : [4, 3],
        pointRadius: isSelected ? 4 : 2,
        tension: 0.15
      };
    }).filter(Boolean);

    if (chart) { chart.destroy(); chart = null; }
    chart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { labels: { color: '#cbd5e1', font: { size: 11 } } },
          tooltip: {
            backgroundColor: '#0f172a', titleColor: '#e5e7eb', bodyColor: '#cbd5e1', borderColor: '#334155', borderWidth: 1,
            callbacks: {
              label: (ctx) => {
                const v = ctx.parsed.y;
                if (mode === 'valuation') return ctx.dataset.label + ': ' + traj.formatUsd(v);
                if (mode === 'rev_mult') return ctx.dataset.label + ': ' + v.toFixed(1) + 'x rev';
                return ctx.dataset.label + ': ' + (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
              }
            }
          }
        },
        scales: {
          x: { type: 'time', ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(148,163,184,0.08)' } },
          y: {
            type: mode === 'valuation' ? 'logarithmic' : 'linear',
            ticks: { color: '#64748b', font: { size: 10 }, callback: (v) => mode === 'valuation' ? traj.formatUsd(v) : (mode === 'rev_mult' ? v.toFixed(0) + 'x' : v.toFixed(0) + '%') },
            grid: { color: 'rgba(148,163,184,0.08)' }
          }
        }
      }
    });

    // Side summary
    const summary = $body.querySelector('#pd-peer-summary');
    if (summary) {
      const coVh = traj.getValuationHistory(co);
      const rev = traj.parseValuation(co.revenue);
      let txt = co.name + ' valuation: ' + (coVh.length ? traj.formatUsd(coVh[coVh.length - 1].valuation_usd) : '—');
      if (rev) txt += '  ·  Est. revenue: ' + traj.formatUsd(rev);
      if (rev && coVh.length) txt += '  ·  Implied multiple: ' + (coVh[coVh.length - 1].valuation_usd / rev).toFixed(1) + 'x';
      summary.textContent = txt;
    }
  }

  draw();
  if (modeEl) modeEl.addEventListener('change', draw);
}

/* ------------------------------------------------------------------
   TAB 1 — OVERVIEW
------------------------------------------------------------------ */
function renderOverviewTab(co, intel) {
  let html = '';

  // Description
  if (intel?.description) {
    html += `
      <div class="pd-section">
        <div class="pd-section-title">What They Do</div>
        <p class="pd-description">${intel.description}</p>
      </div>`;
  } else if (co.metrics) {
    html += `
      <div class="pd-section">
        <div class="pd-section-title">Company Summary</div>
        <p class="pd-description">${co.metrics}</p>
      </div>`;
  }

  // Business model + IPO outlook
  const modelItems = [];
  if (intel?.model) modelItems.push({ label: 'Business Model', value: intel.model });
  if (intel?.ipo_outlook) modelItems.push({ label: 'IPO Outlook', value: intel.ipo_outlook });
  if (intel?.tam) modelItems.push({ label: 'TAM', value: intel.tam });
  if (intel?.growth_rate) modelItems.push({ label: 'Growth', value: intel.growth_rate });

  if (modelItems.length) {
    html += `<div class="pd-section"><div class="pd-section-title">Business Context</div><div class="pd-kv-grid">`;
    modelItems.forEach(item => {
      html += `<div class="pd-kv-row"><span class="pd-kv-label">${item.label}</span><span class="pd-kv-value">${item.value}</span></div>`;
    });
    html += `</div></div>`;
  }

  // Key metrics
  if (intel?.key_metrics?.length) {
    html += `<div class="pd-section"><div class="pd-section-title">Key Metrics</div><div class="pd-metrics-grid">`;
    intel.key_metrics.forEach(m => {
      html += `<div class="pd-metric-card"><div class="pd-metric-value">${m.value}</div><div class="pd-metric-label">${m.label}</div></div>`;
    });
    html += `</div></div>`;
  }

  // Research links
  const links = [];
  if (intel?.pitchbook_url) links.push({ name: 'PitchBook', url: intel.pitchbook_url, icon: '📊' });
  if (intel?.crunchbase_url) links.push({ name: 'Crunchbase', url: intel.crunchbase_url, icon: '🔍' });
  // Add a news search link
  const newsUrl = `https://www.google.com/search?q=${encodeURIComponent(co.name + ' funding valuation 2026')}&tbm=nws`;
  links.push({ name: 'Latest News', url: newsUrl, icon: '📰' });

  if (links.length) {
    html += `<div class="pd-section"><div class="pd-section-title">Research Links</div><div class="pd-links-row">`;
    links.forEach(l => {
      html += `<a href="${l.url}" target="_blank" rel="noopener noreferrer" class="pd-link-btn">${l.icon} ${l.name} ↗</a>`;
    });
    html += `</div></div>`;
  }

  return html || `<div class="pd-empty">No detailed data available for ${co.name}.</div>`;
}

/* ------------------------------------------------------------------
   TAB 2 — PUBLIC COMPS
   Pulls live data from data-snapshot.json for each ticker in
   intel.public_comps, and renders a compare-and-contrast section.
------------------------------------------------------------------ */
async function renderCompsTab(co, intel, $body) {
  $body.innerHTML = '<div class="pd-loading">Loading comparable companies...</div>';

  const tickers = intel?.public_comps || [];
  if (!tickers.length) {
    $body.innerHTML = '<div class="pd-empty">No public comparables mapped for this company.</div>';
    return;
  }

  // Load snapshot data
  let snap = null;
  try {
    snap = (typeof loadSnapshot === 'function') ? await loadSnapshot() : null;
  } catch (e) {}

  const quotes = snap?.quotes || {};
  const compsData = snap?.cross_sector_comps || {};
  const analystData = snap?.analyst_summary || {};

  // Build rows for each mapped ticker
  const rows = tickers.map(ticker => {
    const q = quotes[ticker] || {};
    const a = analystData[ticker] || {};
    const c = compsData[ticker]?.target || {};
    return {
      ticker,
      name: (typeof COMMON_NAMES !== 'undefined' && COMMON_NAMES[ticker]) || q.name || ticker,
      price: q.price,
      changeYtd: q.changeYtd,
      forwardPE: c.forwardPE || q.forwardPE,
      evRev: c.enterpriseToRevenue || q.enterpriseToRevenue,
      evEbitda: c.enterpriseToEbitda || q.enterpriseToEbitda,
      revenueGrowth: c.revenueGrowth || q.revenueGrowth,
      opMargin: c.operatingMargins || q.operatingMargins,
      fcfMargin: c.fcfMargin || q.fcfMargin,
      targetPrice: a.targetMeanPrice,
      recommendation: a.recommendationKey,
      numAnalysts: a.numberOfAnalystOpinions,
    };
  });

  const fmt = (v, d = 1, sfx = '') => (v != null && isFinite(v)) ? v.toFixed(d) + sfx : '—';
  const fmtPct = v => (v != null && isFinite(v)) ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '—';
  const pxClass = v => v == null ? '' : v >= 0 ? 'pos' : 'neg';

  let html = `
    <div class="pd-section">
      <div class="pd-section-title">Public Comparable Companies</div>
      <p class="pd-comps-note">Based on business model, growth profile, and sector. Data from last snapshot refresh.</p>
    </div>
    <div class="pd-comps-table-wrap">
      <table class="pd-comps-table">
        <thead>
          <tr>
            <th>Company</th>
            <th class="num">Price</th>
            <th class="num">YTD</th>
            <th class="num">Fwd P/E</th>
            <th class="num">EV/Rev</th>
            <th class="num">Rev Growth</th>
            <th class="num">Op Margin</th>
            <th class="num">Consensus</th>
          </tr>
        </thead>
        <tbody>`;

  rows.forEach(r => {
    const recBadge = r.recommendation
      ? `<span class="pd-rec pd-rec-${r.recommendation.toLowerCase().replace(' ', '-')}">${r.recommendation.replace('_', ' ')}</span>`
      : '—';
    html += `
          <tr class="pd-comp-row" data-ticker="${r.ticker}" title="Click to open ${r.ticker} detail">
            <td>
              <div class="pd-comp-name-cell">
                <span class="pd-comp-ticker">${r.ticker}</span>
                <span class="pd-comp-name">${r.name.split(',')[0].split(' Inc')[0].split(' Corp')[0]}</span>
              </div>
            </td>
            <td class="num">${r.price ? '$' + r.price.toFixed(2) : '—'}</td>
            <td class="num ${pxClass(r.changeYtd)}">${fmtPct(r.changeYtd)}</td>
            <td class="num">${r.forwardPE > 0 ? fmt(r.forwardPE) + 'x' : r.forwardPE < 0 ? 'NM' : '—'}</td>
            <td class="num">${fmt(r.evRev, 2) !== '—' ? fmt(r.evRev, 2) + 'x' : '—'}</td>
            <td class="num ${pxClass(r.revenueGrowth)}">${fmt(r.revenueGrowth, 1, '%')}</td>
            <td class="num">${fmt(r.opMargin, 1, '%')}</td>
            <td class="num">${recBadge}</td>
          </tr>`;
  });

  html += `</tbody></table></div>`;

  // Narrative: how does the private company compare to these?
  const hasMetrics = rows.some(r => r.forwardPE != null || r.evRev != null);
  if (hasMetrics && intel) {
    html += renderCompsNarrative(co, intel, rows);
  }

  $body.innerHTML = html;

  // Wire up click handlers to open the public popup
  $body.querySelectorAll('.pd-comp-row').forEach(row => {
    row.addEventListener('click', () => {
      const ticker = row.dataset.ticker;
      if (typeof openPopup === 'function') {
        closePrivatePopup();
        setTimeout(() => openPopup(ticker), 150);
      }
    });
  });
}

function renderCompsNarrative(co, intel, rows) {
  const validRows = rows.filter(r => r.forwardPE != null || r.evRev != null || r.revenueGrowth != null);
  if (!validRows.length) return '';

  const median = arr => {
    const sorted = [...arr].filter(v => v != null && isFinite(v)).sort((a, b) => a - b);
    if (!sorted.length) return null;
    return sorted[Math.floor(sorted.length / 2)];
  };
  const fmt = (v, d = 1, sfx = '') => (v != null && isFinite(v)) ? v.toFixed(d) + sfx : '—';

  const medPE = median(validRows.map(r => r.forwardPE).filter(v => v > 0));
  const medEvRev = median(validRows.map(r => r.evRev));
  const medRevGrowth = median(validRows.map(r => r.revenueGrowth));
  const medOpMargin = median(validRows.map(r => r.opMargin));

  // Derive implied valuation from comps
  const revenueRaw = intel?.key_metrics?.find(m =>
    m.label.toLowerCase().includes('revenue') || m.label.toLowerCase().includes('arr'))?.value;

  const paras = [];

  // Valuation context
  if (medEvRev != null && co.valuation && revenueRaw) {
    paras.push(`<p class="pd-narrative-para"><span class="pd-narrative-label">Valuation context</span> The public comparable set trades at a median <strong>${fmt(medEvRev, 1)}x EV/Revenue</strong>. ${co.name} is valued at ${co.valuation} on ${revenueRaw} — the implied EV/Revenue multiple depends on revenue recognition timing, but public comparables provide a reference frame for where the market might anchor this at IPO.</p>`);
  } else if (medPE != null) {
    paras.push(`<p class="pd-narrative-para"><span class="pd-narrative-label">Valuation context</span> Public comparables trade at a median <strong>${fmt(medPE, 1)}x forward P/E</strong>. As a private company, ${co.name}'s implied multiple relative to these comps will depend on growth rate, margin profile, and IPO market conditions.</p>`);
  }

  // Growth context
  if (medRevGrowth != null && intel?.growth_rate) {
    paras.push(`<p class="pd-narrative-para"><span class="pd-narrative-label">Growth comparison</span> The public comp set has a median revenue growth of <strong>${fmt(medRevGrowth, 0)}% YoY</strong>. ${co.name}'s growth trajectory (${intel.growth_rate}) suggests ${intel.growth_rate.includes('%') ? 'it is growing faster than the median public comparable, which typically justifies a premium multiple at IPO' : 'it is in the early stages where revenue metrics are less comparable to mature public software companies'}.</p>`);
  }

  // Margin context
  if (medOpMargin != null) {
    const marginVerdict = medOpMargin > 15
      ? `The public comps carry healthy <strong>${fmt(medOpMargin, 0)}% median operating margins</strong> — ${co.name} will be measured against this benchmark at IPO, with margin improvement trajectory being a key investor focus.`
      : `Public comparables show a median operating margin of <strong>${fmt(medOpMargin, 0)}%</strong>. ${co.name} is likely in investment mode, and investors will focus on the path to ${co.name.includes('AI') || intel?.model?.includes('API') ? '20-30%+ software-normalized margins' : 'profitability'} rather than current margin levels.`;
    paras.push(`<p class="pd-narrative-para"><span class="pd-narrative-label">Margin profile</span> ${marginVerdict}</p>`);
  }

  if (!paras.length) return '';

  return `
    <div class="pd-section">
      <div class="pd-section-title">Implied Positioning</div>
      <div class="pd-narrative">${paras.join('')}</div>
    </div>`;
}

/* ------------------------------------------------------------------
   TAB 3 — COMPETITIVE LANDSCAPE
------------------------------------------------------------------ */
function renderLandscapeTab(co, intel) {
  if (!intel?.competitors?.length) {
    return `<div class="pd-empty">No competitive landscape data available for ${co.name}.</div>`;
  }

  const publicComps = intel.competitors.filter(c => c.type === 'public');
  const privateComps = intel.competitors.filter(c => c.type === 'private');

  let html = `<div class="pd-section"><div class="pd-section-title">Competitive Landscape</div>`;

  // Private competitors
  if (privateComps.length) {
    html += `<div class="pd-landscape-group-label">Private Competitors</div>`;
    privateComps.forEach(c => {
      html += `
        <div class="pd-competitor-card pd-comp-private">
          <div class="pd-competitor-header">
            <span class="pd-competitor-name">${c.name}</span>
            <span class="pd-comp-type-badge pd-comp-type-private">PRIVATE</span>
          </div>
          <p class="pd-competitor-note">${c.note}</p>
        </div>`;
    });
  }

  // Public competitors
  if (publicComps.length) {
    html += `<div class="pd-landscape-group-label" style="margin-top:16px;">Public Competitors</div>`;
    publicComps.forEach(c => {
      const tickerHtml = c.ticker
        ? `<button class="pd-comp-ticker-btn" data-ticker="${c.ticker}">${c.ticker} ↗</button>`
        : '';
      html += `
        <div class="pd-competitor-card pd-comp-public">
          <div class="pd-competitor-header">
            <span class="pd-competitor-name">${c.name}</span>
            ${tickerHtml}
            <span class="pd-comp-type-badge pd-comp-type-public">PUBLIC${c.ticker ? ' · ' + c.ticker : ''}</span>
          </div>
          <p class="pd-competitor-note">${c.note}</p>
        </div>`;
    });
  }

  html += `</div>`;

  // Competitive summary
  html += `
    <div class="pd-section">
      <div class="pd-section-title">Key Differentiators</div>
      <div class="pd-diff-grid">
        <div class="pd-diff-card pd-diff-moat">
          <div class="pd-diff-label">Potential Moat</div>
          <div class="pd-diff-content">${getCompetitiveMoat(co, intel)}</div>
        </div>
        <div class="pd-diff-card pd-diff-threat">
          <div class="pd-diff-label">Biggest Threat</div>
          <div class="pd-diff-content">${getBiggestThreat(co, intel)}</div>
        </div>
      </div>
    </div>`;

  return html;
}

function getCompetitiveMoat(co, intel) {
  if (!intel) return 'Insufficient data to assess competitive moat.';
  // Extract moat hints from bull case
  if (intel.bull_case) {
    const sentences = intel.bull_case.split('. ');
    return sentences[0] + '.';
  }
  return `${co.name} operates in ${co.subsector} with a differentiated product and growth-stage market position.`;
}

function getBiggestThreat(co, intel) {
  if (!intel) return 'Insufficient data to assess primary threat.';
  if (intel.bear_case) {
    const sentences = intel.bear_case.split('. ');
    return sentences[0] + '.';
  }
  if (intel.key_risks?.length) {
    return intel.key_risks[0];
  }
  return 'Well-resourced incumbents with distribution advantages.';
}

/* ------------------------------------------------------------------
   TAB 4 — THESIS
------------------------------------------------------------------ */
function renderThesisTab(co, intel) {
  if (!intel) {
    return `<div class="pd-empty">No thesis data available for ${co.name}.</div>`;
  }

  let html = '';

  // Bull case
  if (intel.bull_case) {
    html += `
      <div class="pd-section">
        <div class="pd-section-title pd-bull-title">🐂 Bull Case</div>
        <p class="pd-thesis-para pd-bull-para">${intel.bull_case}</p>
      </div>`;
  }

  // Bear case
  if (intel.bear_case) {
    html += `
      <div class="pd-section">
        <div class="pd-section-title pd-bear-title">🐻 Bear Case</div>
        <p class="pd-thesis-para pd-bear-para">${intel.bear_case}</p>
      </div>`;
  }

  // Catalysts + risks side by side
  const hasCatalysts = intel.catalysts?.length > 0;
  const hasRisks = intel.key_risks?.length > 0;

  if (hasCatalysts || hasRisks) {
    html += `<div class="pd-section"><div class="pd-catalysts-risks-grid">`;

    if (hasCatalysts) {
      html += `
        <div class="pd-catalysts">
          <div class="pd-section-title">Near-term Catalysts</div>
          <ul class="pd-list pd-list-catalyst">
            ${intel.catalysts.map(c => `<li>${c}</li>`).join('')}
          </ul>
        </div>`;
    }

    if (hasRisks) {
      html += `
        <div class="pd-risks">
          <div class="pd-section-title">Key Risks</div>
          <ul class="pd-list pd-list-risk">
            ${intel.key_risks.map(r => `<li>${r}</li>`).join('')}
          </ul>
        </div>`;
    }

    html += `</div></div>`;
  }

  // IPO outlook as a callout
  if (intel.ipo_outlook) {
    html += `
      <div class="pd-section">
        <div class="pd-ipo-callout">
          <span class="pd-ipo-label">IPO Outlook</span>
          <span class="pd-ipo-text">${intel.ipo_outlook}</span>
        </div>
      </div>`;
  }

  return html || `<div class="pd-empty">No thesis data available.</div>`;
}

/* ------------------------------------------------------------------
   CLICK HANDLER INSTALLATION
   Patches the private table render to make company names clickable.
------------------------------------------------------------------ */
function installPrivateClickHandlers() {
  const $privateBody = document.getElementById('private-body');
  if (!$privateBody) {
    setTimeout(installPrivateClickHandlers, 400);
    return;
  }

  // Use event delegation on the tbody
  $privateBody.addEventListener('click', (e) => {
    // Check if click is on a company name cell or within it
    const nameCell = e.target.closest('.private-name-cell');
    if (!nameCell) return;

    // Don't trigger if clicking a badge (status badge)
    if (e.target.closest('.private-status-badge')) return;

    // Get company name from the row (via data attribute set in renderPrivateTable)
    const companyName = nameCell.dataset.companyName;
    if (companyName) {
      openPrivatePopup(companyName);
    }
  });

  // Also add clickable cursor style via JS
  addPrivateNameClickStyle();
}

function addPrivateNameClickStyle() {
  // Inject a quick style rule for the name cell hover
  const style = document.createElement('style');
  style.textContent = `.private-name-cell { cursor: pointer; }
.private-name-cell:hover span[style], .private-name-cell:hover span:first-child { color: var(--accent) !important; text-decoration: underline; text-decoration-style: dotted; }`;
  document.head.appendChild(style);
}

/* ------------------------------------------------------------------
   WIRE UP PUBLIC TICKER BUTTONS INSIDE LANDSCAPE TAB
   Uses event delegation on the detail body.
------------------------------------------------------------------ */
document.addEventListener('click', e => {
  const btn = e.target.closest('.pd-comp-ticker-btn');
  if (!btn) return;
  const ticker = btn.dataset.ticker;
  if (ticker && typeof openPopup === 'function') {
    closePrivatePopup();
    setTimeout(() => openPopup(ticker), 150);
  }
});

/* ------------------------------------------------------------------
   INIT
------------------------------------------------------------------ */
(function initPrivatePopup() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installPrivateClickHandlers);
  } else {
    installPrivateClickHandlers();
  }
})();
