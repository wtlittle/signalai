/* ===== APP.JS — Main application logic ===== */

// --- State ---
let tickerList = Storage.get('ticker_list') || [...DEFAULT_TICKERS];
let privateCompanies = Storage.get('private_companies') || [...DEFAULT_PRIVATE_COMPANIES];
let tickerData = {}; // { TICKER: row object }
let sortCol = null;
let sortDir = 'asc';
let collapsedGroups = Storage.get('collapsed_groups') || {};
let collapsedPrivateGroups = Storage.get('collapsed_private_groups') || {};
let refreshInterval = null;
let refreshCountdown = 60;

// --- One-time migration: add new tickers/companies for returning users ---
(function migrate_v2() {
  const MIGRATION_KEY = 'migration_v2_done';
  if (Storage.get(MIGRATION_KEY)) return;

  // New public tickers to add
  const newTickers = ['AVGO','MRVL','ARM','NVDA','TSM','MNDY','ADBE','ASAN','GTLB','PATH','VRNS','BILL','FOUR','COIN','SHOP','TTD','AI','IOT'];
  let addedTicker = false;
  newTickers.forEach(t => {
    if (!tickerList.includes(t)) {
      tickerList.push(t);
      addedTicker = true;
    }
  });
  if (addedTicker) Storage.set('ticker_list', tickerList);

  // New private companies to add (match by name)
  const existingNames = new Set(privateCompanies.map(c => c.name.toLowerCase()));
  const newPrivate = DEFAULT_PRIVATE_COMPANIES.filter(c => !existingNames.has(c.name.toLowerCase()));
  if (newPrivate.length > 0) {
    // Also update existing entries with refreshed data
    privateCompanies.forEach((c, i) => {
      const updated = DEFAULT_PRIVATE_COMPANIES.find(d => d.name.toLowerCase() === c.name.toLowerCase());
      if (updated) privateCompanies[i] = { ...c, ...updated };
    });
    privateCompanies.push(...newPrivate);
    Storage.set('private_companies', privateCompanies);
  }

  Storage.set(MIGRATION_KEY, true);
})();

// --- Migration v3: Consolidate private company subsectors ---
(function migrate_v3() {
  const MIGRATION_KEY = 'migration_v3_done';
  if (Storage.get(MIGRATION_KEY)) return;

  const SUBSECTOR_REMAP = {
    'AI Foundation Models': 'AI Models & Agents',
    'AI Agents': 'AI Models & Agents',
    'AI / Data Platform': 'AI Infrastructure',
    'AI Data Infrastructure': 'AI Infrastructure',
    'AI Chips': 'AI Infrastructure',
    'AI Developer Tools': 'AI Software',
    'Enterprise AI Search': 'AI Software',
    'Fintech / Payments': 'Fintech',
    'HR Tech / Fintech': 'Fintech',
  };

  let changed = false;
  privateCompanies.forEach((c, i) => {
    if (c.subsector && SUBSECTOR_REMAP[c.subsector]) {
      privateCompanies[i] = { ...c, subsector: SUBSECTOR_REMAP[c.subsector] };
      changed = true;
    }
  });

  // Also refresh data from defaults
  privateCompanies.forEach((c, i) => {
    const updated = DEFAULT_PRIVATE_COMPANIES.find(d => d.name.toLowerCase() === c.name.toLowerCase());
    if (updated) privateCompanies[i] = { ...c, ...updated };
  });

  if (changed) Storage.set('private_companies', privateCompanies);
  Storage.set(MIGRATION_KEY, true);
})();

// --- Migration v4: PitchBook data refresh (Mar 2026) ---
// Adds headquarters, lead_investors, updated valuations/funding/revenue from PitchBook
// Also flags CoreWeave and Figma as now-public
(function migrate_v4() {
  const MIGRATION_KEY = 'migration_v4_done';
  if (Storage.get(MIGRATION_KEY)) return;

  // Refresh all existing private companies from updated defaults
  privateCompanies.forEach((c, i) => {
    const updated = DEFAULT_PRIVATE_COMPANIES.find(d => d.name.toLowerCase() === c.name.toLowerCase());
    if (updated) {
      privateCompanies[i] = { ...c, ...updated };
    }
  });

  // Add any new companies from defaults that user doesn't have
  const existingNames = new Set(privateCompanies.map(c => c.name.toLowerCase()));
  DEFAULT_PRIVATE_COMPANIES.forEach(d => {
    if (!existingNames.has(d.name.toLowerCase())) {
      privateCompanies.push(d);
    }
  });

  Storage.set('private_companies', privateCompanies);
  Storage.set(MIGRATION_KEY, true);
})();

// --- Migration v5: Remove delisted AYX ---
(function migrate_v5() {
  const MIGRATION_KEY = 'migration_v5_done';
  if (Storage.get(MIGRATION_KEY)) return;
  const idx = tickerList.indexOf('AYX');
  if (idx !== -1) {
    tickerList.splice(idx, 1);
    Storage.set('ticker_list', tickerList);
  }
  Storage.set(MIGRATION_KEY, true);
})();

// --- Migration v6: Add weekly briefing picks ---
(function migrate_v6() {
  const MIGRATION_KEY = 'migration_v6_done';
  if (Storage.get(MIGRATION_KEY)) return;
  const newTickers = ['ELV','TMO','ACGL','LPLA','SNDK','CIEN','WDC','CF'];
  let added = false;
  newTickers.forEach(t => {
    if (!tickerList.includes(t)) {
      tickerList.push(t);
      added = true;
    }
  });
  if (added) Storage.set('ticker_list', tickerList);
  Storage.set(MIGRATION_KEY, true);
})();

// --- Migration v7: Add weekly briefing picks week 2 (2026-03-22) ---
(function migrate_v7() {
  const MIGRATION_KEY = 'migration_v7_done';
  if (Storage.get(MIGRATION_KEY)) return;
  const newTickers = ['NKE','UNH','CMCSA','HD','MKC','MU','SOC','HIMS','GCT','BWXT'];
  let added = false;
  newTickers.forEach(t => {
    if (!tickerList.includes(t)) {
      tickerList.push(t);
      added = true;
    }
  });
  if (added) Storage.set('ticker_list', tickerList);
  Storage.set(MIGRATION_KEY, true);
})();

// --- DOM refs ---
const $body = document.getElementById('watchlist-body');
const $privateBody = document.getElementById('private-body');
const $loading = document.getElementById('loading-overlay');
const $tickerInput = document.getElementById('ticker-input');
const $addBtn = document.getElementById('add-ticker-btn');
const $totalMcap = document.getElementById('total-mcap');
const $lastUpdated = document.getElementById('last-updated');
const $refreshTimer = document.getElementById('refresh-timer');
const $addPrivateBtn = document.getElementById('add-private-btn');
const $privateInput = document.getElementById('private-input');
const $privateModalOverlay = document.getElementById('private-modal-overlay');
const $privateModalClose = document.getElementById('private-modal-close');
const $privateForm = document.getElementById('private-form');

// --- Save state ---
function saveTickers() { Storage.set('ticker_list', tickerList); }
function savePrivate() { Storage.set('private_companies', privateCompanies); }
function saveCollapsed() { Storage.set('collapsed_groups', collapsedGroups); }
function saveCollapsedPrivate() { Storage.set('collapsed_private_groups', collapsedPrivateGroups); }

// Group private companies by subsector (returns ordered object of { subsector: [companies] })
function groupPrivateBySubsector(companies) {
  const groups = {};
  companies.forEach((co, idx) => {
    const sub = co.subsector || 'Other';
    if (!groups[sub]) groups[sub] = [];
    groups[sub].push({ ...co, _idx: idx });
  });
  // Sort by predefined order, then alphabetical
  const ordered = {};
  SUBSECTOR_ORDER.forEach(s => {
    if (groups[s]) { ordered[s] = groups[s]; delete groups[s]; }
  });
  Object.keys(groups).sort().forEach(s => { ordered[s] = groups[s]; });
  return ordered;
}

// --- Render main table ---
function renderTable() {
  $body.innerHTML = '';
  const groups = groupBySubsector(tickerList);

  // Get sorted tickers within each group
  const getSortedTickers = (tickers) => {
    if (!sortCol) return tickers;
    return [...tickers].sort((a, b) => {
      const da = tickerData[a] || {};
      const db = tickerData[b] || {};
      let va = da[sortCol];
      let vb = db[sortCol];
      // Handle strings
      if (typeof va === 'string') va = va.toLowerCase();
      if (typeof vb === 'string') vb = vb.toLowerCase();
      // Nulls last
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  };

  Object.entries(groups).forEach(([subsector, tickers]) => {
    const sorted = getSortedTickers(tickers);
    const isCollapsed = !!collapsedGroups[subsector];

    // Subsector header row
    const headerRow = document.createElement('tr');
    headerRow.className = 'subsector-header';
    headerRow.innerHTML = `
      <td colspan="16">
        <button class="subsector-toggle" data-subsector="${subsector}">
          <span class="chevron ${isCollapsed ? 'collapsed' : ''}">▼</span>
          ${subsector}
          <span class="subsector-count">(${tickers.length})</span>
        </button>
      </td>
    `;
    headerRow.querySelector('.subsector-toggle').addEventListener('click', () => {
      collapsedGroups[subsector] = !collapsedGroups[subsector];
      saveCollapsed();
      renderTable();
    });
    $body.appendChild(headerRow);

    // Data rows
    sorted.forEach(ticker => {
      const d = tickerData[ticker] || { ticker };
      const tr = document.createElement('tr');
      if (isCollapsed) tr.className = 'row-hidden';

      const hq = d.headquarters || COMPANY_HQ[ticker] || '';
      const hqHtml = hq ? `<span class="public-hq">${hq}</span>` : '';

      tr.innerHTML = `
        <td class="cell-ticker" data-ticker="${ticker}">${ticker}</td>
        <td class="cell-name" title="${getCommonName(ticker, d.name)}">
          <span class="cell-name-text">${getCommonName(ticker, d.name)}</span>
          ${hqHtml}
        </td>
        <td><span class="subsector-badge" data-ticker="${ticker}">${d.subsector || getSubsector(ticker)}</span></td>
        <td class="num">${formatPrice(d.price)}</td>
        <td class="num">${formatLargeNumber(d.marketCap)}</td>
        <td class="num">${formatLargeNumber(d.ev)}</td>
        <td class="num">${formatMultiple(d.evSales)}</td>
        <td class="num">${formatMultiple(d.evFcf)}</td>
        <td class="num ${percentClass(d.ytd)}">${formatPercent(d.ytd)}</td>
        <td class="num ${percentClass(d.d1)}">${formatPercent(d.d1)}</td>
        <td class="num ${percentClass(d.w1)}">${formatPercent(d.w1)}</td>
        <td class="num ${percentClass(d.m1)}">${formatPercent(d.m1)}</td>
        <td class="num ${percentClass(d.m3)}">${formatPercent(d.m3)}</td>
        <td class="num ${percentClass(d.y1)}">${formatPercent(d.y1)}</td>
        <td class="num ${percentClass(d.y3)}">${formatPercent(d.y3)}</td>
        <td><button class="remove-btn" data-ticker="${ticker}" title="Remove">&times;</button></td>
      `;

      // Click ticker to open popup
      tr.querySelector('.cell-ticker').addEventListener('click', () => openPopup(ticker));

      // Click or long-press subsector badge to change via picker
      const badge = tr.querySelector('.subsector-badge');
      badge.addEventListener('click', (e) => {
        e.stopPropagation();
        showSubsectorPicker(badge, ticker);
      });
      attachSubsectorLongPress(badge, ticker);

      // Remove button
      tr.querySelector('.remove-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        tickerList = tickerList.filter(t => t !== ticker);
        delete tickerData[ticker];
        saveTickers();
        renderTable();
        updateTotalMcap();
      });

      $body.appendChild(tr);
    });
  });

  // Update sort indicators
  document.querySelectorAll('.watchlist-table thead th').forEach(th => {
    th.classList.remove('sort-active');
    const arrow = th.querySelector('.sort-arrow');
    if (arrow) arrow.remove();
    if (th.dataset.col === sortCol) {
      th.classList.add('sort-active');
      const span = document.createElement('span');
      span.className = 'sort-arrow';
      span.textContent = sortDir === 'asc' ? ' ▲' : ' ▼';
      th.appendChild(span);
    }
  });
}

// --- Render private companies (grouped by subsector) ---
function renderPrivateTable() {
  $privateBody.innerHTML = '';
  const groups = groupPrivateBySubsector(privateCompanies);

  Object.entries(groups).forEach(([subsector, companies]) => {
    const isCollapsed = !!collapsedPrivateGroups[subsector];

    // Subsector header row
    const headerRow = document.createElement('tr');
    headerRow.className = 'subsector-header';
    headerRow.innerHTML = `
      <td colspan="8">
        <button class="subsector-toggle" data-subsector="${subsector}">
          <span class="chevron ${isCollapsed ? 'collapsed' : ''}">▼</span>
          ${subsector}
          <span class="subsector-count">(${companies.length})</span>
        </button>
      </td>
    `;
    headerRow.querySelector('.subsector-toggle').addEventListener('click', () => {
      collapsedPrivateGroups[subsector] = !collapsedPrivateGroups[subsector];
      saveCollapsedPrivate();
      renderPrivateTable();
    });
    $privateBody.appendChild(headerRow);

    // Data rows
    companies.forEach(co => {
      const idx = co._idx;
      const tr = document.createElement('tr');
      if (isCollapsed) tr.className = 'row-hidden';

      // Status badge for public/IPO companies
      let statusBadge = '';
      if (co.status === 'public') {
        statusBadge = `<span class="private-status-badge status-public" title="Now publicly traded as ${co.ticker || ''}">${co.ticker || 'PUBLIC'}</span>`;
      } else if (co.status === 'ipo_pending') {
        statusBadge = `<span class="private-status-badge status-ipo">IPO FILING</span>`;
      }

      // HQ as subtle line under company name
      const hqLine = co.headquarters ? `<span class="private-hq">${co.headquarters}</span>` : '';

      tr.innerHTML = `
        <td class="private-name-cell">
          <div class="private-name-wrapper">
            <span style="color:var(--text-primary);font-weight:500;">${co.name}</span>
            ${statusBadge}
          </div>
          ${hqLine}
        </td>
        <td><span class="subsector-badge" data-private-idx="${idx}">${co.subsector}</span></td>
        <td class="num" style="font-family:var(--font-mono);">${co.valuation}</td>
        <td style="color:var(--text-secondary);font-size:11px;">${co.funding}</td>
        <td class="private-investors">${co.lead_investors || '—'}</td>
        <td class="num" style="font-family:var(--font-mono);">${co.revenue}</td>
        <td style="color:var(--text-secondary);font-size:11px;">${co.metrics}</td>
        <td><button class="remove-btn remove-private" data-idx="${idx}" title="Remove">&times;</button></td>
      `;
      tr.querySelector('.remove-private').addEventListener('click', () => {
        privateCompanies.splice(idx, 1);
        savePrivate();
        renderPrivateTable();
      });
      $privateBody.appendChild(tr);
    });
  });
}

// --- Sorting ---
document.querySelectorAll('.watchlist-table thead th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.col;
    if (sortCol === col) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortCol = col;
      sortDir = col === 'ticker' || col === 'name' || col === 'subsector' ? 'asc' : 'desc';
    }
    renderTable();
  });
});

// --- Add ticker (with search autocomplete) ---
const $searchDropdown = document.getElementById('search-dropdown');
let searchTimeout = null;
let searchResults = [];
let searchActiveIndex = -1;
let selectedSymbol = null; // when user picks from dropdown

function addTickerBySymbol(symbol) {
  const val = symbol.trim().toUpperCase();
  if (!val) return;
  closeDropdown();
  $tickerInput.value = '';
  selectedSymbol = null;
  if (tickerList.includes(val)) return;
  tickerList.push(val);
  saveTickers();

  // Fetch data for new ticker
  $loading.classList.add('active');
  fetchTickerFull(val).then(data => {
    tickerData[val] = data;
    renderTable();
    updateTotalMcap();
  }).catch(() => {
    tickerData[val] = { ticker: val, name: val, error: true };
    renderTable();
  }).finally(() => {
    $loading.classList.remove('active');
  });
}

function closeDropdown() {
  $searchDropdown.classList.remove('active');
  $searchDropdown.innerHTML = '';
  searchResults = [];
  searchActiveIndex = -1;
}

function renderDropdown(results, query) {
  searchResults = results;
  searchActiveIndex = -1;
  if (!results.length) {
    if (query.length >= 1) {
      $searchDropdown.innerHTML = '<div class="search-dropdown-empty">No results</div>';
      $searchDropdown.classList.add('active');
    } else {
      closeDropdown();
    }
    return;
  }
  $searchDropdown.innerHTML = results.map((r, i) => {
    const nameEsc = (r.name || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const symEsc = (r.symbol || '').replace(/</g, '&lt;');
    const exEsc = (r.exchange || '').replace(/</g, '&lt;');
    return `<div class="search-dropdown-item" data-index="${i}" data-symbol="${symEsc}">
      <span class="search-dropdown-symbol">${symEsc}</span>
      <span class="search-dropdown-name">${nameEsc}</span>
      <span class="search-dropdown-exchange">${exEsc}</span>
    </div>`;
  }).join('');
  $searchDropdown.classList.add('active');

  // Click handlers
  $searchDropdown.querySelectorAll('.search-dropdown-item').forEach(el => {
    el.addEventListener('mousedown', (e) => {
      e.preventDefault(); // prevent blur
      const sym = el.dataset.symbol;
      if (sym) addTickerBySymbol(sym);
    });
  });
}

function setActiveItem(index) {
  const items = $searchDropdown.querySelectorAll('.search-dropdown-item');
  items.forEach(el => el.classList.remove('active'));
  if (index >= 0 && index < items.length) {
    items[index].classList.add('active');
    items[index].scrollIntoView({ block: 'nearest' });
  }
  searchActiveIndex = index;
}

async function doSearch(query) {
  if (!query || query.length < 1) {
    closeDropdown();
    return;
  }
  $searchDropdown.innerHTML = '<div class="search-dropdown-loading">Searching...</div>';
  $searchDropdown.classList.add('active');
  try {
    let data;
    if (await checkBackend()) {
      const resp = await fetch(`${BACKEND_URL}/search?q=${encodeURIComponent(query)}`, { signal: AbortSignal.timeout(5000) });
      data = await resp.json();
    } else if (typeof searchTickerClient === 'function') {
      data = await searchTickerClient(query);
    } else {
      data = [];
    }
    // Only render if input still matches (avoid stale results)
    if ($tickerInput.value.trim().toLowerCase() === query.toLowerCase()) {
      renderDropdown(data, query);
    }
  } catch (e) {
    console.warn('Search failed:', e);
    // Fallback: treat input as raw ticker
    closeDropdown();
  }
}

$tickerInput.addEventListener('input', () => {
  const val = $tickerInput.value.trim();
  selectedSymbol = null;
  clearTimeout(searchTimeout);
  if (!val) {
    closeDropdown();
    return;
  }
  searchTimeout = setTimeout(() => doSearch(val), 250);
});

$tickerInput.addEventListener('keydown', (e) => {
  const items = $searchDropdown.querySelectorAll('.search-dropdown-item');
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (!$searchDropdown.classList.contains('active')) return;
    setActiveItem(Math.min(searchActiveIndex + 1, items.length - 1));
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (!$searchDropdown.classList.contains('active')) return;
    setActiveItem(Math.max(searchActiveIndex - 1, 0));
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (searchActiveIndex >= 0 && searchActiveIndex < searchResults.length) {
      addTickerBySymbol(searchResults[searchActiveIndex].symbol);
    } else if ($tickerInput.value.trim()) {
      // Direct add as raw ticker if no dropdown selection
      addTickerBySymbol($tickerInput.value.trim().toUpperCase());
    }
  } else if (e.key === 'Escape') {
    closeDropdown();
  }
});

$tickerInput.addEventListener('blur', () => {
  // Small delay so mousedown on dropdown item fires first
  setTimeout(() => closeDropdown(), 150);
});

$addBtn.addEventListener('click', () => {
  if (searchActiveIndex >= 0 && searchActiveIndex < searchResults.length) {
    addTickerBySymbol(searchResults[searchActiveIndex].symbol);
  } else if ($tickerInput.value.trim()) {
    addTickerBySymbol($tickerInput.value.trim().toUpperCase());
  }
});

// --- Private company modal ---
const $privateSubmitBtn = document.getElementById('private-submit-btn');
const $privateSubmitText = document.getElementById('private-submit-text');
const $privateSubmitSpinner = document.getElementById('private-submit-spinner');
const $privateLookupStatus = document.getElementById('private-lookup-status');

// --- Inline private company add ---
let privateAddInProgress = false;

async function addPrivateFromInline() {
  const name = $privateInput.value.trim();
  if (!name || privateAddInProgress) return;

  if (privateCompanies.some(c => c.name.toLowerCase() === name.toLowerCase())) {
    $privateInput.value = '';
    return;
  }

  privateAddInProgress = true;
  $privateInput.disabled = true;
  $addPrivateBtn.disabled = true;
  $addPrivateBtn.textContent = 'Looking up...';

  try {
    const co = await lookupPrivateCompany(name);
    co.name = capitalizeCompanyName(co.name || name);
    privateCompanies.push(co);
    savePrivate();
    renderPrivateTable();
    $privateInput.value = '';
  } catch (err) {
    console.error('Inline private lookup error:', err);
    const co = { name: capitalizeCompanyName(name), subsector: 'Unknown', valuation: 'N/A', funding: 'N/A', revenue: 'N/A', metrics: '' };
    privateCompanies.push(co);
    savePrivate();
    renderPrivateTable();
    $privateInput.value = '';
  } finally {
    privateAddInProgress = false;
    $privateInput.disabled = false;
    $addPrivateBtn.disabled = false;
    $addPrivateBtn.textContent = '+ Add Private Company';
    $privateInput.focus();
  }
}

$addPrivateBtn.addEventListener('click', () => {
  const name = $privateInput.value.trim();
  if (name) {
    addPrivateFromInline();
  } else {
    $privateModalOverlay.classList.add('active');
    $privateLookupStatus.style.display = 'none';
  }
});
$privateInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    addPrivateFromInline();
  }
});
$privateModalClose.addEventListener('click', () => {
  $privateModalOverlay.classList.remove('active');
});
$privateModalOverlay.addEventListener('click', (e) => {
  if (e.target === $privateModalOverlay) $privateModalOverlay.classList.remove('active');
});

function setPrivateFormLoading(loading) {
  $privateSubmitBtn.disabled = loading;
  $privateSubmitText.textContent = loading ? 'Looking up...' : 'Look Up & Add';
  $privateSubmitSpinner.style.display = loading ? 'inline-block' : 'none';
  $privateForm.querySelector('input[name="name"]').disabled = loading;
}

function showLookupStatus(msg, type) {
  $privateLookupStatus.style.display = 'block';
  $privateLookupStatus.className = 'lookup-status status-' + type;
  $privateLookupStatus.textContent = msg;
}

async function lookupPrivateCompany(name) {
  // Try backend first
  if (await checkBackend()) {
    try {
      const resp = await fetch(`${BACKEND_URL}/lookup-private?name=${encodeURIComponent(name)}`, {
        signal: AbortSignal.timeout(30000),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (!data.error) return data;
      }
    } catch (e) {
      console.warn('Backend lookup failed:', e);
    }
  }

  // Client-side fallback: use CORS-proxied search
  try {
    const query = encodeURIComponent(`${name} company valuation funding round revenue 2025`);
    const searchUrl = `https://api.duckduckgo.com/?q=${query}&format=json&no_html=1`;
    const proxyUrl = `https://corsproxy.io/?url=${encodeURIComponent(searchUrl)}`;
    const resp = await fetch(proxyUrl, { signal: AbortSignal.timeout(10000) });
    if (resp.ok) {
      const data = await resp.json();
      const abstract = data.Abstract || data.AbstractText || '';
      const relatedTopics = (data.RelatedTopics || []).map(t => t.Text || '').join(' ');
      const fullText = abstract + ' ' + relatedTopics;
      if (fullText.trim().length > 20) {
        return parseCompanyFromText(name, fullText);
      }
    }
  } catch (e) {
    console.warn('Client-side search failed:', e);
  }

  // Final fallback: just the name
  return { name, subsector: 'Technology', valuation: 'N/A', funding: 'N/A', revenue: 'N/A', metrics: '' };
}

function parseCompanyFromText(name, text) {
  const info = { name, subsector: 'Technology', valuation: 'N/A', funding: 'N/A', revenue: 'N/A', metrics: '' };
  const textLower = text.toLowerCase();

  // Valuation
  const valMatch = text.match(/(?:valued|valuation|worth)[^$]*\$([\d,.]+)\s*(billion|B|trillion|T)/i) ||
                   text.match(/\$([\d,.]+)\s*(billion|B|trillion|T)\s*valuation/i);
  if (valMatch) {
    const suffix = valMatch[2][0].toUpperCase() === 'T' ? 'T' : 'B';
    info.valuation = `$${valMatch[1].replace(/,/g, '')}${suffix}`;
  }

  // Revenue
  const revMatch = text.match(/(?:revenue|ARR)[^$]*\$([\d,.]+)\s*(billion|B|million|M)/i) ||
                   text.match(/\$([\d,.]+)\s*(billion|B|million|M)\s*(?:in\s+)?(?:revenue|ARR)/i);
  if (revMatch) {
    const suffix = revMatch[2][0].toUpperCase() === 'B' ? 'B' : 'M';
    info.revenue = `~$${revMatch[1].replace(/,/g, '')}${suffix}`;
  }

  // Subsector keywords
  const sectorMap = {
    'AI Models & Agents': ['language model', 'llm', 'generative ai', 'foundation model', 'ai agent', 'chatbot', 'conversational ai'],
    'AI Infrastructure': ['gpu cloud', 'ai infrastructure', 'ai compute', 'data platform', 'data lakehouse', 'data analytics', 'data labeling', 'ai chip', 'ai hardware'],
    'AI Software': ['ai-powered', 'ai code', 'ai search', 'ai assistant', 'developer tool', 'code editor', 'copilot'],
    'Cybersecurity': ['cybersecurity', 'security platform', 'threat detection'],
    'Fintech': ['fintech', 'payments', 'financial technology', 'neobank', 'payroll', 'hr tech'],
    'Design & Creative': ['design tool', 'creative platform', 'collaboration tool', 'design software'],
    'Space Technology': ['rocket', 'satellite', 'space'],
    'Enterprise Software': ['enterprise software', 'saas', 'crm', 'erp'],
    'Healthcare': ['healthcare', 'biotech', 'pharmaceutical', 'medical'],
    'E-Commerce': ['e-commerce', 'marketplace', 'retail tech'],
  };
  for (const [sector, keywords] of Object.entries(sectorMap)) {
    if (keywords.some(kw => textLower.includes(kw))) {
      info.subsector = sector;
      break;
    }
  }

  return info;
}

$privateForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $privateForm.querySelector('input[name="name"]').value.trim();
  if (!name) return;

  // Check if already exists
  if (privateCompanies.some(c => c.name.toLowerCase() === name.toLowerCase())) {
    showLookupStatus(`${name} is already in your watchlist.`, 'error');
    return;
  }

  setPrivateFormLoading(true);
  showLookupStatus(`Looking up ${name}...`, 'loading');

  try {
    const co = await lookupPrivateCompany(name);
    co.name = capitalizeCompanyName(co.name || name);
    privateCompanies.push(co);
    savePrivate();
    renderPrivateTable();
    $privateForm.reset();

    const details = [co.valuation, co.subsector, co.revenue].filter(v => v && v !== 'N/A').join(' · ');
    showLookupStatus(`Added ${co.name}${details ? ': ' + details : ''}`, 'success');
    
    // Close modal after a brief delay
    setTimeout(() => {
      $privateModalOverlay.classList.remove('active');
    }, 1500);
  } catch (err) {
    console.error('Lookup error:', err);
    // Still add with just the name
    const properName = capitalizeCompanyName(name);
    const co = { name: properName, subsector: 'Unknown', valuation: 'N/A', funding: 'N/A', revenue: 'N/A', metrics: '' };
    privateCompanies.push(co);
    savePrivate();
    renderPrivateTable();
    $privateForm.reset();
    showLookupStatus(`Added ${properName} (lookup failed, details can be edited later)`, 'error');
    setTimeout(() => {
      $privateModalOverlay.classList.remove('active');
    }, 2000);
  } finally {
    setPrivateFormLoading(false);
  }
});

// --- Total market cap ---
function updateTotalMcap() {
  let total = 0;
  tickerList.forEach(t => {
    const d = tickerData[t];
    if (d && d.marketCap) total += d.marketCap;
  });
  $totalMcap.textContent = `Total MCap: ${formatLargeNumber(total)}`;
}

// --- Refresh ---
function updateTimestamp(snapshotDate) {
  // Check data source priority: backend > Supabase > snapshot
  const dsInfo = typeof getDataSourceInfo === 'function' ? getDataSourceInfo() : { source: 'none' };
  if (dsInfo.source === 'supabase') {
    const genDate = dsInfo.generated ? new Date(dsInfo.generated) : null;
    $lastUpdated.textContent = genDate ? `Supabase: ${formatDateShort(genDate)}` : 'Supabase: connected';
    $lastUpdated.title = 'Data from Supabase database — refreshed daily by cron';
  } else if (snapshotDate) {
    const d = new Date(snapshotDate);
    $lastUpdated.textContent = `Snapshot: ${formatDateShort(d)}`;
    $lastUpdated.title = 'Data from cached snapshot — connect backend for live data';
  } else {
    $lastUpdated.textContent = `Updated: ${formatDateShort(new Date())}`;
    $lastUpdated.title = '';
  }
}

function showDemoBanner() {
  // Show subtle indicator about data source
  const existing = document.getElementById('demo-banner');
  if (existing) existing.remove();
  const dsInfo = typeof getDataSourceInfo === 'function' ? getDataSourceInfo() : { source: 'none' };
  const banner = document.createElement('div');
  banner.id = 'demo-banner';
  if (dsInfo.source === 'supabase') {
    banner.style.cssText = 'background:rgba(59,130,246,0.08);color:rgba(59,130,246,0.7);text-align:center;padding:4px 12px;font-size:11px;letter-spacing:0.5px;font-family:var(--font-mono);border-bottom:1px solid rgba(59,130,246,0.1);';
    const genStr = dsInfo.generated ? ` · Data as of ${new Date(dsInfo.generated).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}` : '';
    banner.textContent = `SUPABASE — Live database connected${genStr}`;
  } else {
    banner.style.cssText = 'background:rgba(0,200,150,0.08);color:rgba(0,200,150,0.7);text-align:center;padding:4px 12px;font-size:11px;letter-spacing:0.5px;font-family:var(--font-mono);border-bottom:1px solid rgba(0,200,150,0.1);';
    banner.textContent = 'DEMO MODE — Showing cached market data · Run locally with Python backend for live prices';
  }
  // Insert after the header (not before body) to avoid breaking sticky offsets
  const header = document.querySelector('.app-header');
  if (header && header.nextSibling) {
    header.parentNode.insertBefore(banner, header.nextSibling);
  } else {
    document.body.prepend(banner);
  }
  // Adjust sticky thead offset to account for the banner height
  requestAnimationFrame(() => {
    const bannerHeight = banner.offsetHeight;
    const headerHeight = header ? header.offsetHeight : 45;
    const totalOffset = headerHeight + bannerHeight;
    document.documentElement.style.setProperty('--sticky-thead-top', totalOffset + 'px');
  });
}

function startRefreshCycle() {
  // In demo/snapshot-only mode, don't auto-refresh (data is static)
  // Supabase mode allows refresh since data may be updated by cron
  const dsInfo = typeof getDataSourceInfo === 'function' ? getDataSourceInfo() : { source: 'none' };
  if (dsInfo.source === 'snapshot' && !_proxyWorking) {
    $refreshTimer.textContent = 'Static';
    $refreshTimer.title = 'Snapshot data — no auto-refresh';
    return;
  }
  refreshCountdown = 60;
  if (refreshInterval) clearInterval(refreshInterval);
  refreshInterval = setInterval(() => {
    refreshCountdown--;
    $refreshTimer.textContent = `↻ ${refreshCountdown}s`;
    if (refreshCountdown <= 0) {
      loadAllData();
    }
  }, 1000);
}

// --- Initial load ---
async function loadAllData() {
  $loading.classList.add('active');
  refreshCountdown = 60;
  $refreshTimer.textContent = `↻ ${refreshCountdown}s`;

  try {
    const results = await fetchAllTickers(tickerList, (done, total) => {
      $refreshTimer.textContent = `Loading ${done}/${total}...`;
    });
    tickerData = results;
    renderTable();
    updateTotalMcap();

    // Detect data source and update UI accordingly
    const dsInfo = typeof getDataSourceInfo === 'function' ? getDataSourceInfo() : { source: 'none' };
    const usingSnapshot = dsInfo.source === 'snapshot' &&
      typeof backendAvailable !== 'undefined' && backendAvailable === false;
    if (dsInfo.source === 'supabase') {
      updateTimestamp();
      // No banner needed for Supabase — source info shown in header timestamp
    } else if (usingSnapshot) {
      updateTimestamp(_snapshotData?.generated);
      showDemoBanner();
    } else {
      updateTimestamp();
    }
  } catch (e) {
    console.error('Failed to load data:', e);
  } finally {
    $loading.classList.remove('active');
    startRefreshCycle();
  }
}

// === SUBSECTOR PICKER ===
let activeSubsectorPicker = null;

function dismissSubsectorPicker() {
  if (activeSubsectorPicker) {
    activeSubsectorPicker.remove();
    activeSubsectorPicker = null;
  }
}

function getAllSubsectors() {
  // Collect all unique subsectors from: predefined order, current tickers, and overrides
  const subs = new Set(SUBSECTOR_ORDER);
  tickerList.forEach(t => subs.add(getSubsector(t)));
  // Remove 'Other' from the set — it'll be at the end if needed
  subs.delete('Other');
  return [...subs];
}

function showSubsectorPicker(badgeEl, ticker) {
  dismissSubsectorPicker();
  dismissContextMenu();

  const currentSub = getSubsector(ticker);
  const subsectors = getAllSubsectors();

  const picker = document.createElement('div');
  picker.className = 'subsector-picker';

  // Header
  const header = document.createElement('div');
  header.className = 'subsector-picker-header';
  header.textContent = `Subsector · ${ticker}`;
  picker.appendChild(header);

  // Subsector options
  subsectors.forEach(sub => {
    const opt = document.createElement('div');
    opt.className = 'subsector-picker-item' + (sub === currentSub ? ' active' : '');
    opt.innerHTML = `<span class="subsector-picker-check">${sub === currentSub ? '✓' : ''}</span>${sub}`;
    opt.addEventListener('click', (e) => {
      e.stopPropagation();
      applySubsector(ticker, sub);
      dismissSubsectorPicker();
    });
    picker.appendChild(opt);
  });

  // Divider
  const divider = document.createElement('div');
  divider.className = 'subsector-picker-divider';
  picker.appendChild(divider);

  // Custom option
  const customOpt = document.createElement('div');
  customOpt.className = 'subsector-picker-item subsector-picker-custom';
  customOpt.innerHTML = `<span class="subsector-picker-check"></span>Custom...`;
  customOpt.addEventListener('click', (e) => {
    e.stopPropagation();
    // Replace the custom option with an inline input
    showInlineSubsectorInput(picker, ticker);
  });
  picker.appendChild(customOpt);

  document.body.appendChild(picker);
  activeSubsectorPicker = picker;

  // Position relative to the badge
  const badgeRect = badgeEl.getBoundingClientRect();
  const pickerRect = picker.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let left = badgeRect.left;
  let top = badgeRect.bottom + 4;

  // Keep within viewport
  if (left + pickerRect.width > vw - 8) left = vw - pickerRect.width - 8;
  if (left < 8) left = 8;
  if (top + pickerRect.height > vh - 8) top = badgeRect.top - pickerRect.height - 4;

  picker.style.left = left + 'px';
  picker.style.top = top + 'px';
}

function showInlineSubsectorInput(picker, ticker) {
  // Remove the Custom... option and add an input
  const customEl = picker.querySelector('.subsector-picker-custom');
  if (customEl) customEl.remove();

  const inputRow = document.createElement('div');
  inputRow.className = 'subsector-picker-input-row';
  inputRow.innerHTML = `
    <input type="text" class="subsector-picker-input" placeholder="Enter subsector..." />
    <button class="subsector-picker-confirm">✓</button>
  `;
  picker.appendChild(inputRow);

  const input = inputRow.querySelector('input');
  const confirmBtn = inputRow.querySelector('button');

  input.focus();

  const apply = () => {
    const val = input.value.trim();
    if (val) {
      applySubsector(ticker, val);
      dismissSubsectorPicker();
    }
  };

  confirmBtn.addEventListener('click', (e) => { e.stopPropagation(); apply(); });
  input.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Enter') apply();
    if (e.key === 'Escape') dismissSubsectorPicker();
  });
  input.addEventListener('click', (e) => e.stopPropagation());
}

function applySubsector(ticker, subsector) {
  setSubsectorOverride(ticker, subsector);
  if (tickerData[ticker]) tickerData[ticker].subsector = subsector;
  // Add to SUBSECTOR_ORDER if new
  if (!SUBSECTOR_ORDER.includes(subsector)) {
    SUBSECTOR_ORDER.push(subsector);
  }
  renderTable();
}

function attachSubsectorLongPress(el, ticker) {
  let lpTimer = null;
  let startX = 0, startY = 0;
  const MOVE_THRESHOLD = 10;

  el.addEventListener('touchstart', (e) => {
    e.stopPropagation(); // Prevent row long-press
    const touch = e.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
    lpTimer = setTimeout(() => {
      if (navigator.vibrate) navigator.vibrate(30);
      showSubsectorPicker(el, ticker);
      lpTimer = null;
    }, LONG_PRESS_MS);
  }, { passive: true });

  el.addEventListener('touchmove', (e) => {
    if (!lpTimer) return;
    const touch = e.touches[0];
    if (Math.abs(touch.clientX - startX) > MOVE_THRESHOLD ||
        Math.abs(touch.clientY - startY) > MOVE_THRESHOLD) {
      clearTimeout(lpTimer);
      lpTimer = null;
    }
  }, { passive: true });

  el.addEventListener('touchend', () => {
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
  });
  el.addEventListener('touchcancel', () => {
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
  });
}

// Dismiss picker on click anywhere else
document.addEventListener('click', (e) => {
  if (activeSubsectorPicker && !activeSubsectorPicker.contains(e.target)) {
    dismissSubsectorPicker();
  }
});
document.addEventListener('scroll', dismissSubsectorPicker, true);

// === CONTEXT MENU (long-press + right-click) ===
let activeContextMenu = null;
let longPressTimer = null;
let longPressRow = null;
const LONG_PRESS_MS = 500;

function dismissContextMenu() {
  if (activeContextMenu) {
    activeContextMenu.remove();
    activeContextMenu = null;
  }
  if (longPressRow) {
    longPressRow.classList.remove('long-pressing');
    longPressRow = null;
  }
}

function showContextMenu(x, y, items) {
  dismissContextMenu();
  const menu = document.createElement('div');
  menu.className = 'context-menu';

  items.forEach(item => {
    if (item.divider) {
      const div = document.createElement('div');
      div.className = 'context-menu-divider';
      menu.appendChild(div);
      return;
    }
    const el = document.createElement('div');
    el.className = 'context-menu-item' + (item.danger ? ' danger' : '');
    el.innerHTML = `<span class="ctx-icon">${item.icon || ''}</span>${item.label}`;
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      dismissContextMenu();
      item.action();
    });
    menu.appendChild(el);
  });

  document.body.appendChild(menu);
  activeContextMenu = menu;

  // Position — keep within viewport
  const rect = menu.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  menu.style.left = Math.min(x, vw - rect.width - 8) + 'px';
  menu.style.top = Math.min(y, vh - rect.height - 8) + 'px';
}

// Dismiss on click/touch anywhere else
document.addEventListener('click', dismissContextMenu);
document.addEventListener('touchstart', (e) => {
  if (activeContextMenu && !activeContextMenu.contains(e.target)) dismissContextMenu();
});
document.addEventListener('scroll', dismissContextMenu, true);

// Build context menu items for a public ticker row
function getTickerMenuItems(ticker) {
  return [
    {
      icon: '📊', label: 'View Details',
      action: () => openPopup(ticker)
    },
    {
      icon: '🏷️', label: 'Change Subsector',
      action: () => {
        // Find the badge for this ticker and show picker near it
        const badge = document.querySelector(`.subsector-badge[data-ticker="${ticker}"]`);
        if (badge) {
          showSubsectorPicker(badge, ticker);
        }
      }
    },
    { divider: true },
    {
      icon: '✕', label: 'Remove from Watchlist', danger: true,
      action: () => {
        tickerList = tickerList.filter(t => t !== ticker);
        delete tickerData[ticker];
        saveTickers();
        renderTable();
        updateTotalMcap();
      }
    }
  ];
}

// Build context menu items for a private company row
function getPrivateMenuItems(idx) {
  const co = privateCompanies[idx];
  if (!co) return [];
  return [
    {
      icon: '✏️', label: 'Edit Company',
      action: () => {
        const name = prompt('Company Name:', co.name);
        if (name === null) return;
        co.name = name || co.name;
        co.valuation = prompt('Valuation:', co.valuation) || co.valuation;
        co.subsector = prompt('Subsector:', co.subsector) || co.subsector;
        co.funding = prompt('Last Funding Round:', co.funding) || co.funding;
        co.revenue = prompt('Est. Revenue:', co.revenue) || co.revenue;
        co.metrics = prompt('Key Metrics:', co.metrics) || co.metrics;
        savePrivate();
        renderPrivateTable();
      }
    },
    { divider: true },
    {
      icon: '✕', label: 'Remove Company', danger: true,
      action: () => {
        privateCompanies.splice(idx, 1);
        savePrivate();
        renderPrivateTable();
      }
    }
  ];
}

// Attach long-press and right-click to table rows
function attachRowContextMenu(tr, getItems) {
  // Right-click (desktop)
  tr.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    showContextMenu(e.clientX, e.clientY, getItems());
  });

  // Long-press (touch)
  let lpTimer = null;
  let startX = 0, startY = 0;
  const MOVE_THRESHOLD = 10;

  tr.addEventListener('touchstart', (e) => {
    const touch = e.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
    longPressRow = tr;
    lpTimer = setTimeout(() => {
      tr.classList.add('long-pressing');
      // Small vibration feedback if available
      if (navigator.vibrate) navigator.vibrate(30);
      showContextMenu(touch.clientX, touch.clientY, getItems());
      lpTimer = null;
    }, LONG_PRESS_MS);
  }, { passive: true });

  tr.addEventListener('touchmove', (e) => {
    if (!lpTimer) return;
    const touch = e.touches[0];
    if (Math.abs(touch.clientX - startX) > MOVE_THRESHOLD ||
        Math.abs(touch.clientY - startY) > MOVE_THRESHOLD) {
      clearTimeout(lpTimer);
      lpTimer = null;
      tr.classList.remove('long-pressing');
    }
  }, { passive: true });

  tr.addEventListener('touchend', () => {
    if (lpTimer) {
      clearTimeout(lpTimer);
      lpTimer = null;
    }
    tr.classList.remove('long-pressing');
  });

  tr.addEventListener('touchcancel', () => {
    if (lpTimer) {
      clearTimeout(lpTimer);
      lpTimer = null;
    }
    tr.classList.remove('long-pressing');
  });
}

// Patch renderTable to attach context menus
const _origRenderTable = renderTable;
renderTable = function() {
  _origRenderTable();
  // Attach context menus to all data rows (not headers)
  document.querySelectorAll('#watchlist-body tr:not(.subsector-header)').forEach(tr => {
    const tickerCell = tr.querySelector('.cell-ticker');
    if (tickerCell) {
      const ticker = tickerCell.dataset.ticker;
      attachRowContextMenu(tr, () => getTickerMenuItems(ticker));
    }
  });
};

// Patch renderPrivateTable to attach context menus
const _origRenderPrivate = renderPrivateTable;
renderPrivateTable = function() {
  _origRenderPrivate();
  document.querySelectorAll('#private-body tr').forEach(tr => {
    // Skip subsector header rows
    if (tr.classList.contains('subsector-header')) return;
    const removeBtn = tr.querySelector('.remove-private');
    if (!removeBtn) return;
    const idx = parseInt(removeBtn.dataset.idx, 10);
    if (!isNaN(idx)) {
      attachRowContextMenu(tr, () => getPrivateMenuItems(idx));
    }
  });
};

// --- News Feed ---
const $newsFeed = document.getElementById('news-feed');
const $newsStatus = document.getElementById('news-status');

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '';
  const now = Date.now();
  const diff = now - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function renderNews(items) {
  if (!items || items.length === 0) {
    $newsFeed.innerHTML = '<div class="news-empty">No recent news for your watchlist</div>';
    return;
  }
  $newsFeed.innerHTML = items.map(n => {
    const t = (n.title || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const src = (n.source || '').replace(/</g, '&lt;');
    const ticker = (n.ticker || '').replace(/</g, '&lt;');
    const ago = timeAgo(n.pubDate);
    const href = n.url ? ` href="${n.url.replace(/"/g, '&quot;')}" target="_blank" rel="noopener noreferrer"` : '';
    return `<a class="news-item"${href}>
      <span class="news-ticker-badge">${ticker}</span>
      <span class="news-title">${t}</span>
      <span class="news-meta">
        <span class="news-source">${src}</span>
        <span class="news-time">${ago}</span>
      </span>
    </a>`;
  }).join('');
}

async function fetchNews() {
  const newsTickers = tickerList.slice(0, 20);
  if (newsTickers.length === 0) return;
  try {
    $newsStatus.textContent = 'updating...';
    let data;
    if (await checkBackend()) {
      const url = `${BACKEND_URL}/news?symbols=${encodeURIComponent(newsTickers.join(','))}`;
      const resp = await fetch(url, { signal: AbortSignal.timeout(60000) });
      data = await resp.json();
    } else if (typeof fetchNewsClient === 'function') {
      data = await fetchNewsClient(newsTickers);
    } else {
      data = [];
    }
    if (data && data.length > 0) {
      renderNews(data);
      $newsStatus.textContent = `${data.length} articles`;
    } else {
      $newsFeed.innerHTML = '<div class="news-empty">News requires the backend server</div>';
      $newsStatus.textContent = '';
    }
  } catch (e) {
    console.warn('News fetch failed:', e);
    $newsFeed.innerHTML = '<div class="news-empty">Could not load news</div>';
    $newsStatus.textContent = '';
  }
}

// --- Boot ---
renderTable();
renderPrivateTable();
loadAllData();
// Load earnings data after a short delay
setTimeout(() => fetchEarnings(), 2000);
// Load news shortly after main data (staggered to not block)
setTimeout(() => fetchNews(), 4000);
