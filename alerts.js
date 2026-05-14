/* ===== ALERTS.JS — Alerts page (Subscriptions + Triggered Alerts + Recent Activity) =====
 *
 * Three-section page:
 *   1. Feed Subscriptions  — toggleable switches for the 9 event types Computer
 *      can push into your alert stream. Persisted to Supabase
 *      (user_alert_subscriptions table); falls back to localStorage if Supabase
 *      is unavailable so toggles still work offline.
 *   2. Triggered Alerts    — the existing threshold-rule alerts (1D/1W/1M/YTD%,
 *      earnings_pre). Stored in localStorage (key 'ss_alerts').
 *   3. Recent Activity     — last N fired alerts (stored locally; populated by
 *      emit_alert() helper once Phase 2 cron wiring lands).
 *
 * Channels per subscription: ['in_app' | 'push' | 'email']. Defaults follow the
 * "aggressive" doctrine — push for time-sensitive, email for digests, in_app
 * for everything else.
 */

// ---------- Storage keys ----------
const ALERTS_STORAGE_KEY = 'ss_alerts';
const ACTIVITY_STORAGE_KEY = 'ss_alert_activity';
const SUBS_LOCAL_KEY = 'ss_alert_subscriptions';
const USER_EMAIL_KEY = 'ss_alert_user_email';
const DEFAULT_USER_EMAIL = 'wtlittle9498@gmail.com';

// ---------- Subscription catalogue (single source of truth) ----------
// Order here drives render order on the page. Each entry maps a stable key to
// human-friendly copy, an icon glyph, and the default channel set.
const SUBSCRIPTION_CATALOG = [
  {
    key: 'pre_earnings_note',
    title: 'New pre-earnings note',
    detail: 'Fresh pre-earnings note is generated (T-3 to T-1).',
    group: 'Earnings',
    default: { enabled: true, channels: ['in_app'] }
  },
  {
    key: 'post_earnings_note',
    title: 'New post-earnings note',
    detail: 'Post-earnings note ready with stock reaction summary (T+0 to T+2).',
    group: 'Earnings',
    default: { enabled: true, channels: ['in_app'] }
  },
  {
    key: 'earnings_day',
    title: 'Earnings day heads-up',
    detail: 'Morning of report — implied vs historical move, key items to watch.',
    group: 'Earnings',
    default: { enabled: true, channels: ['push', 'in_app'] }
  },
  {
    key: 'ma_rumor',
    title: 'M&A rumor candidate',
    detail: 'A watchlist name clears the 4-gate rumor logic (Tier-1 source, named buyer, sized move).',
    group: 'M&A',
    default: { enabled: true, channels: ['push', 'in_app'] }
  },
  {
    key: 'ma_status_change',
    title: 'Deal status change',
    detail: 'Announced → pending → closed, or a deal breaks.',
    group: 'M&A',
    default: { enabled: true, channels: ['in_app'] }
  },
  {
    key: 'analyst_material',
    title: 'Material analyst change',
    detail: 'Price target moves ≥20% or sell-side rating change.',
    group: 'Coverage',
    default: { enabled: true, channels: ['in_app'] }
  },
  {
    key: 'weekly_briefing',
    title: 'Weekly briefing ready',
    detail: 'Sunday morning market briefing — value picks, momentum, watchlist movers.',
    group: 'Digests',
    default: { enabled: true, channels: ['email', 'in_app'] }
  },
  {
    key: 'big_move_10pct',
    title: 'Single-day move > 10%',
    detail: 'A watchlist name moves more than 10% in a single trading day.',
    group: 'Price',
    default: { enabled: true, channels: ['push', 'in_app'] }
  },
  {
    key: 'sector_rotation',
    title: 'Sector rotation alert',
    detail: 'A watchlist subsector beats or lags the S&P by 5%+ in a week.',
    group: 'Price',
    default: { enabled: true, channels: ['in_app'] }
  }
];

const CHANNEL_LABELS = { in_app: 'In-app', push: 'Push', email: 'Email' };
const CHANNEL_ORDER  = ['in_app', 'push', 'email'];

// ---------- Local storage helpers ----------
function loadAlerts() {
  return (typeof Storage !== 'undefined' && Storage.get(ALERTS_STORAGE_KEY)) || [];
}
function saveAlerts(list) {
  if (typeof Storage !== 'undefined') Storage.set(ALERTS_STORAGE_KEY, list);
}
function loadActivity() {
  return (typeof Storage !== 'undefined' && Storage.get(ACTIVITY_STORAGE_KEY)) || [];
}
function saveActivity(list) {
  if (typeof Storage !== 'undefined') Storage.set(ACTIVITY_STORAGE_KEY, list);
}
function loadLocalSubs() {
  return (typeof Storage !== 'undefined' && Storage.get(SUBS_LOCAL_KEY)) || null;
}
function saveLocalSubs(subs) {
  if (typeof Storage !== 'undefined') Storage.set(SUBS_LOCAL_KEY, subs);
}
function getUserEmail() {
  try {
    const stored = (typeof Storage !== 'undefined' && Storage.get(USER_EMAIL_KEY)) || null;
    return stored || DEFAULT_USER_EMAIL;
  } catch (e) {
    return DEFAULT_USER_EMAIL;
  }
}

// ---------- Defaults ----------
function defaultSubscriptions() {
  const out = {};
  SUBSCRIPTION_CATALOG.forEach(s => {
    out[s.key] = { enabled: s.default.enabled, channels: s.default.channels.slice() };
  });
  return out;
}

// ---------- Supabase persistence ----------
// Reads from / writes to public.user_alert_subscriptions. The publishable
// (anon) key is used directly; this table is the user's own row keyed by email.
// Schema:
//   user_email TEXT PRIMARY KEY
//   subscriptions JSONB
//   updated_at TIMESTAMPTZ DEFAULT NOW()
const _SUPABASE_URL = (typeof SUPABASE_URL !== 'undefined') ? SUPABASE_URL : 'https://wcyirdvvuetzodiedzss.supabase.co';
const _SUPABASE_KEY = (typeof SUPABASE_KEY !== 'undefined') ? SUPABASE_KEY : 'sb_publishable_VOT04H1B4O7dVBqxTOk5rw_lyYBR9SW';

async function fetchSubsFromSupabase(email) {
  try {
    const url = `${_SUPABASE_URL}/rest/v1/user_alert_subscriptions?user_email=eq.${encodeURIComponent(email)}&select=subscriptions`;
    const resp = await fetch(url, {
      headers: { 'apikey': _SUPABASE_KEY },
      signal: AbortSignal.timeout(8000)
    });
    if (!resp.ok) return null;
    const rows = await resp.json();
    if (rows && rows[0] && rows[0].subscriptions) return rows[0].subscriptions;
    return null;
  } catch (e) {
    console.warn('[alerts] fetchSubsFromSupabase failed:', e && e.message);
    return null;
  }
}

async function writeSubsToSupabase(email, subs) {
  try {
    const url = `${_SUPABASE_URL}/rest/v1/user_alert_subscriptions?on_conflict=user_email`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'apikey': _SUPABASE_KEY,
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates,return=minimal'
      },
      body: JSON.stringify({ user_email: email, subscriptions: subs }),
      signal: AbortSignal.timeout(8000)
    });
    if (!resp.ok) {
      console.warn('[alerts] writeSubsToSupabase HTTP', resp.status);
      return false;
    }
    return true;
  } catch (e) {
    console.warn('[alerts] writeSubsToSupabase failed:', e && e.message);
    return false;
  }
}

// Merge persisted subs (partial) onto defaults so newly-added subscription
// types automatically appear with their default state instead of being treated
// as disabled.
function _mergeWithDefaults(persisted) {
  const out = defaultSubscriptions();
  if (!persisted || typeof persisted !== 'object') return out;
  Object.keys(persisted).forEach(k => {
    if (out[k]) {
      const v = persisted[k] || {};
      out[k] = {
        enabled: v.enabled !== false,
        channels: Array.isArray(v.channels) && v.channels.length ? v.channels.slice() : out[k].channels
      };
    }
  });
  return out;
}

let _subsCache = null;
let _subsLoadPromise = null;

async function loadSubscriptions() {
  if (_subsCache) return _subsCache;
  if (_subsLoadPromise) return _subsLoadPromise;
  _subsLoadPromise = (async () => {
    const email = getUserEmail();
    // 1) Supabase preferred
    const remote = await fetchSubsFromSupabase(email);
    if (remote) {
      _subsCache = _mergeWithDefaults(remote);
      saveLocalSubs(_subsCache);
      return _subsCache;
    }
    // 2) localStorage fallback
    const local = loadLocalSubs();
    if (local) {
      _subsCache = _mergeWithDefaults(local);
      return _subsCache;
    }
    // 3) defaults
    _subsCache = defaultSubscriptions();
    return _subsCache;
  })();
  return _subsLoadPromise;
}

async function saveSubscriptions(subs) {
  _subsCache = subs;
  saveLocalSubs(subs);
  const email = getUserEmail();
  // Fire-and-forget Supabase write; UI already reflects the toggle change.
  writeSubsToSupabase(email, subs).catch(() => { /* swallow */ });
}

// ---------- Public emit helper (Phase 2 crons call this) ----------
// emit_alert({ type, ticker, summary, link, severity }) appends to local
// activity log and renders if the page is visible. Cron-side delivery to push/
// email channels is handled out-of-band by the cron itself (it has the
// subscriptions table in Supabase).
function emitAlert(payload) {
  if (!payload || !payload.type) return;
  const entry = {
    id: 'al_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
    type: payload.type,
    ticker: payload.ticker || null,
    summary: payload.summary || '',
    link: payload.link || null,
    severity: payload.severity || 'info',
    at: payload.at || new Date().toISOString()
  };
  const list = loadActivity();
  list.unshift(entry);
  // Cap at 200 to keep localStorage small.
  saveActivity(list.slice(0, 200));
  // Re-render if the alerts surface is visible.
  if (document.querySelector('.surface-alerts:not([hidden])')) {
    try { renderAlertsTab(); } catch (e) { /* ignore */ }
  }
}

// ---------- Rendering ----------
async function renderAlertsTab() {
  const $content = document.getElementById('alerts-content');
  if (!$content) return;
  $content.innerHTML = '<div class="alerts-loading">Loading alerts...</div>';

  const subs = await loadSubscriptions();
  const alerts = loadAlerts();
  const activity = loadActivity();

  $content.innerHTML = `
    <div class="alerts-page">
      ${renderSubscriptionsSection(subs)}
      ${renderTriggeredSection(alerts)}
      ${renderActivitySection(activity)}
    </div>
  `;

  wireSubscriptionsSection();
  wireTriggeredSection();
  wireActivitySection();
}

function renderSubscriptionsSection(subs) {
  // Group catalog entries by .group for visual grouping.
  const groups = {};
  SUBSCRIPTION_CATALOG.forEach(s => {
    const g = s.group || 'Other';
    if (!groups[g]) groups[g] = [];
    groups[g].push(s);
  });
  const groupHtml = Object.keys(groups).map(g => {
    const rows = groups[g].map(s => renderSubscriptionRow(s, subs[s.key])).join('');
    return `
      <div class="alerts-sub-group">
        <div class="alerts-sub-group-title">${g}</div>
        ${rows}
      </div>
    `;
  }).join('');

  const userEmail = getUserEmail();
  return `
    <section class="alerts-section-block" data-block="subscriptions">
      <div class="alerts-block-header">
        <h3>Feed Subscriptions</h3>
        <span class="alerts-block-sub">Pick what Computer should push into your alert stream. Saved to Supabase.</span>
      </div>
      <div class="alerts-sub-account">
        <span class="alerts-sub-account-label">Delivering to</span>
        <input type="email" id="alerts-user-email" class="alerts-sub-account-input" value="${userEmail}" autocomplete="off" spellcheck="false">
      </div>
      ${groupHtml}
    </section>
  `;
}

function renderSubscriptionRow(catEntry, sub) {
  sub = sub || { enabled: false, channels: [] };
  const channelsHtml = CHANNEL_ORDER.map(ch => {
    const on = sub.channels.indexOf(ch) !== -1;
    return `<button type="button" class="alerts-channel-chip ${on ? 'on' : ''}" data-channel="${ch}" data-sub-key="${catEntry.key}" ${sub.enabled ? '' : 'disabled'}>${CHANNEL_LABELS[ch]}</button>`;
  }).join('');
  return `
    <div class="alerts-sub-row" data-sub-key="${catEntry.key}">
      <label class="alerts-sub-toggle">
        <input type="checkbox" data-sub-toggle="${catEntry.key}" ${sub.enabled ? 'checked' : ''}>
        <span class="alerts-sub-toggle-track"></span>
      </label>
      <div class="alerts-sub-body">
        <div class="alerts-sub-title">${catEntry.title}</div>
        <div class="alerts-sub-detail">${catEntry.detail}</div>
      </div>
      <div class="alerts-sub-channels">${channelsHtml}</div>
    </div>
  `;
}

function wireSubscriptionsSection() {
  const $emailInput = document.getElementById('alerts-user-email');
  if ($emailInput) {
    $emailInput.addEventListener('change', async () => {
      const v = ($emailInput.value || '').trim();
      if (v && /\S+@\S+\.\S+/.test(v)) {
        if (typeof Storage !== 'undefined') Storage.set(USER_EMAIL_KEY, v);
        // Re-load subs for the new email.
        _subsCache = null;
        _subsLoadPromise = null;
        await renderAlertsTab();
      }
    });
  }
  document.querySelectorAll('[data-sub-toggle]').forEach(box => {
    box.addEventListener('change', async () => {
      const key = box.dataset.subToggle;
      const subs = await loadSubscriptions();
      subs[key] = subs[key] || { enabled: false, channels: [] };
      subs[key].enabled = !!box.checked;
      // When enabling for the first time, seed with the default channels.
      if (subs[key].enabled && (!subs[key].channels || !subs[key].channels.length)) {
        const cat = SUBSCRIPTION_CATALOG.find(c => c.key === key);
        if (cat) subs[key].channels = cat.default.channels.slice();
      }
      await saveSubscriptions(subs);
      renderAlertsTab();
    });
  });
  document.querySelectorAll('.alerts-channel-chip').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      const key = btn.dataset.subKey;
      const ch = btn.dataset.channel;
      const subs = await loadSubscriptions();
      subs[key] = subs[key] || { enabled: true, channels: [] };
      const idx = subs[key].channels.indexOf(ch);
      if (idx === -1) subs[key].channels.push(ch);
      else subs[key].channels.splice(idx, 1);
      await saveSubscriptions(subs);
      renderAlertsTab();
    });
  });
}

function renderTriggeredSection(alerts) {
  const body = alerts.length
    ? `<div class="alerts-list">${alerts.map(a => renderAlertCard(a)).join('')}</div>`
    : `<div class="alerts-empty-inline">No threshold alerts yet. Use “New Alert” to add a price or earnings trigger.</div>`;
  return `
    <section class="alerts-section-block" data-block="triggered">
      <div class="alerts-block-header">
        <h3>Triggered Alerts</h3>
        <span class="alerts-block-sub">Threshold rules — 1D/1W/1M/YTD% moves and earnings countdowns.</span>
      </div>
      ${body}
    </section>
  `;
}

function wireTriggeredSection() {
  document.querySelectorAll('.alert-remove').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.id;
      const updated = loadAlerts().filter(a => a.id !== id);
      saveAlerts(updated);
      renderAlertsTab();
    });
  });
}

function renderAlertCard(a) {
  let desc = '';
  if (a.type === 'earnings_pre') {
    desc = `${a.ticker || 'Watchlist'} earnings within ${a.days}d`;
  } else {
    const scope = a.scope === 'watchlist' ? 'Any watchlist name' : a.ticker;
    const opLabel = a.op === '<=' ? '≤' : '≥';
    desc = `${scope} · ${a.type} ${opLabel} ${a.value}%`;
  }
  return `<div class="alert-card">
    <span class="alert-desc">${desc}</span>
    <button class="alert-remove" data-id="${a.id}" title="Remove" aria-label="Remove alert">&times;</button>
  </div>`;
}

function renderActivitySection(activity) {
  if (!activity.length) {
    return `
      <section class="alerts-section-block" data-block="activity">
        <div class="alerts-block-header">
          <h3>Recent Activity</h3>
          <span class="alerts-block-sub">The last 50 alerts that fired across your subscriptions.</span>
        </div>
        <div class="alerts-empty-inline">No alerts have fired yet.</div>
      </section>
    `;
  }
  const rows = activity.slice(0, 50).map(e => {
    const when = _formatWhen(e.at);
    const tk = e.ticker ? `<span class="alerts-activity-ticker">${e.ticker}</span>` : '';
    const link = e.link ? ` <a class="alerts-activity-link" href="${e.link}" target="_blank" rel="noopener">open →</a>` : '';
    return `
      <div class="alerts-activity-row sev-${e.severity || 'info'}">
        <div class="alerts-activity-meta">
          <span class="alerts-activity-type">${e.type}</span>
          ${tk}
          <span class="alerts-activity-when">${when}</span>
        </div>
        <div class="alerts-activity-summary">${e.summary || ''}${link}</div>
      </div>
    `;
  }).join('');
  return `
    <section class="alerts-section-block" data-block="activity">
      <div class="alerts-block-header">
        <h3>Recent Activity</h3>
        <span class="alerts-block-sub">The last 50 alerts that fired across your subscriptions.</span>
        <button class="btn-sm alerts-activity-clear" id="alerts-activity-clear">Clear</button>
      </div>
      <div class="alerts-activity-list">${rows}</div>
    </section>
  `;
}

function wireActivitySection() {
  const btn = document.getElementById('alerts-activity-clear');
  if (btn) {
    btn.addEventListener('click', () => {
      saveActivity([]);
      renderAlertsTab();
    });
  }
}

function _formatWhen(iso) {
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.round(diff / 60) + 'm ago';
    if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
    if (diff < 604800) return Math.round(diff / 86400) + 'd ago';
    return d.toLocaleDateString();
  } catch (e) { return iso || ''; }
}

// ---------- New Alert modal (unchanged behaviour) ----------
function openNewAlertModal(prefill) {
  const overlay = document.getElementById('alert-modal-overlay');
  if (!overlay) return;
  const $scope = document.getElementById('alert-scope');
  const $ticker = document.getElementById('alert-ticker');
  const $type = document.getElementById('alert-type');
  const $op = document.getElementById('alert-op');
  const $value = document.getElementById('alert-value');
  const $days = document.getElementById('alert-days');
  const $tickerRow = document.querySelector('.alert-ticker-row');
  const $threshRow = document.getElementById('alert-thresh-row');
  const $daysRow = document.getElementById('alert-days-row');

  $scope.value = 'ticker';
  $ticker.value = '';
  $type.value = '1D%';
  $op.value = '<=';
  $value.value = '';
  $days.value = '';

  if (prefill) {
    if (prefill.scope) $scope.value = prefill.scope;
    if (prefill.ticker) $ticker.value = prefill.ticker;
    if (prefill.type) $type.value = prefill.type;
    if (prefill.op) $op.value = prefill.op;
    if (prefill.value != null) $value.value = String(prefill.value);
    if (prefill.days != null) $days.value = String(prefill.days);
  }

  const refreshVisibility = () => {
    const isEarnings = $type.value === 'earnings_pre';
    $threshRow.style.display = isEarnings ? 'none' : '';
    $daysRow.style.display = isEarnings ? '' : 'none';
    $tickerRow.style.display = $scope.value === 'watchlist' ? 'none' : '';
  };
  refreshVisibility();
  $scope.onchange = refreshVisibility;
  $type.onchange = refreshVisibility;

  overlay.classList.add('active');
}

function closeNewAlertModal() {
  const overlay = document.getElementById('alert-modal-overlay');
  if (overlay) overlay.classList.remove('active');
}

document.addEventListener('DOMContentLoaded', () => {
  const newBtn = document.getElementById('alerts-new-btn');
  if (newBtn) newBtn.addEventListener('click', () => openNewAlertModal());
  const closeBtn = document.getElementById('alert-modal-close');
  if (closeBtn) closeBtn.addEventListener('click', closeNewAlertModal);
  const overlay = document.getElementById('alert-modal-overlay');
  if (overlay) overlay.addEventListener('click', (e) => { if (e.target === overlay) closeNewAlertModal(); });
  const form = document.getElementById('alert-form');
  if (form) form.addEventListener('submit', (e) => {
    e.preventDefault();
    const scope = document.getElementById('alert-scope').value;
    const type = document.getElementById('alert-type').value;
    const ticker = (document.getElementById('alert-ticker').value || '').trim().toUpperCase();
    const op = document.getElementById('alert-op').value;
    const value = parseFloat(document.getElementById('alert-value').value);
    const days = parseInt(document.getElementById('alert-days').value, 10);
    const rec = {
      id: 'a_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7),
      scope, type,
      created_at: new Date().toISOString(),
    };
    if (scope === 'ticker') rec.ticker = ticker;
    if (type === 'earnings_pre') rec.days = isNaN(days) ? 1 : days;
    else { rec.op = op; rec.value = isNaN(value) ? 0 : value; }
    const next = loadAlerts().concat([rec]);
    saveAlerts(next);
    closeNewAlertModal();
    renderAlertsTab();
  });
});

window.renderAlertsTab = renderAlertsTab;
window.openNewAlertModal = openNewAlertModal;
window.SignalAlerts = {
  emitAlert: emitAlert,
  loadSubscriptions: loadSubscriptions,
  saveSubscriptions: saveSubscriptions,
  SUBSCRIPTION_CATALOG: SUBSCRIPTION_CATALOG
};
