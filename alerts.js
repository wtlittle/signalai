/* ===== ALERTS.JS — Alerts tab (local-only, email coming soon) ===== */
/* Storage: Storage key 'ss_alerts' = array of alert objects.
 * Alert shape:
 *   { id, scope: 'ticker'|'watchlist', ticker?, type: '1D%'|'1W%'|'1M%'|'YTD%'|'earnings_pre',
 *     op?: '<='|'>=', value?: number, days?: number, created_at }
 */

const ALERTS_STORAGE_KEY = 'ss_alerts';

function loadAlerts() {
  return Storage.get(ALERTS_STORAGE_KEY) || [];
}
function saveAlerts(list) {
  Storage.set(ALERTS_STORAGE_KEY, list);
}

function renderAlertsTab() {
  const $content = document.getElementById('alerts-content');
  if (!$content) return;
  const alerts = loadAlerts();
  if (!alerts.length) {
    $content.innerHTML = renderAlertsEmptyState();
    wireAlertsEmptyState();
    return;
  }
  $content.innerHTML = `
    <div class="alerts-list">
      ${alerts.map(a => renderAlertCard(a)).join('')}
    </div>
    <div class="alerts-footer">Alerts appear in-app on each data refresh. Email delivery coming soon.</div>
  `;
  // Wire remove buttons
  $content.querySelectorAll('.alert-remove').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.id;
      const updated = loadAlerts().filter(a => a.id !== id);
      saveAlerts(updated);
      renderAlertsTab();
    });
  });
}

function renderAlertsEmptyState() {
  return `
    <div class="alerts-empty">
      <div class="alerts-empty-head">Set your first alert</div>
      <div class="alerts-empty-sub">Get notified when a name or your full watchlist crosses a threshold — or ahead of upcoming earnings.</div>
      <div class="alerts-template-grid">
        <button class="alerts-template-btn" data-prefill='{"scope":"ticker","ticker":"NVDA","type":"1D%","op":"<=","value":-5}'>
          <span class="alerts-template-title">NVDA drops 5% in a day</span>
          <span class="alerts-template-sub">Single-name intraday move alert</span>
        </button>
        <button class="alerts-template-btn" data-prefill='{"scope":"ticker","ticker":"MSFT","type":"earnings_pre","days":1}'>
          <span class="alerts-template-title">MSFT earnings in 1 day</span>
          <span class="alerts-template-sub">Pre-earnings heads-up</span>
        </button>
        <button class="alerts-template-btn" data-prefill='{"scope":"watchlist","type":"YTD%","op":"<=","value":-10}'>
          <span class="alerts-template-title">Any watchlist name down 10% YTD</span>
          <span class="alerts-template-sub">Broad drawdown screen</span>
        </button>
      </div>
      <div class="alerts-footer">Alerts appear in-app on each data refresh. Email delivery coming soon.</div>
    </div>
  `;
}

function wireAlertsEmptyState() {
  document.querySelectorAll('.alerts-template-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      try {
        const prefill = JSON.parse(btn.dataset.prefill);
        openNewAlertModal(prefill);
      } catch (e) {
        openNewAlertModal();
      }
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

  // Defaults then prefill
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
