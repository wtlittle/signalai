/**
 * ma-pending.js — Pending M&A rumor review queue
 *
 * Renders entries from ma_status.json `pending_review[]` inside the News tab.
 * Approve copies the entry into ma_status.json `deals[ticker]` with
 * status='rumor' and a 14-day expiry note. Reject just removes from the queue.
 *
 * Persistence: since this is a static dashboard, approve/reject is mirrored
 * to a Supabase table (ma_pending_decisions) and reflected locally. The
 * server-side rumor cron reads decisions on next run and respects them
 * (won't re-flag rejected tickers for 30 days).
 *
 * Fallback when Supabase is unreachable: localStorage so the UI still works.
 */
(function () {
  const PENDING_URL = 'ma_status.json?ts=' + Date.now();
  const LOCAL_DECISIONS_KEY = 'signalai_ma_pending_decisions_v1';
  const REJECT_COOLDOWN_DAYS = 30;

  function readLocalDecisions() {
    try {
      const raw = localStorage.getItem(LOCAL_DECISIONS_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  }
  function writeLocalDecisions(map) {
    try {
      localStorage.setItem(LOCAL_DECISIONS_KEY, JSON.stringify(map));
    } catch (_) {}
  }

  async function supabaseUpsertDecision(decision) {
    try {
      const cfg = window.SUPABASE_CONFIG || (window.SignalAlerts && window.SignalAlerts.supabaseConfig());
      if (!cfg || !cfg.url || !cfg.anonKey) return false;
      const url = `${cfg.url}/rest/v1/ma_pending_decisions`;
      const r = await fetch(url, {
        method: 'POST',
        headers: {
          'apikey': cfg.anonKey,
          'Authorization': 'Bearer ' + cfg.anonKey,
          'Content-Type': 'application/json',
          'Prefer': 'resolution=merge-duplicates',
        },
        body: JSON.stringify(decision),
      });
      return r.ok;
    } catch (_) {
      return false;
    }
  }

  function fmtPct(n) {
    if (n == null) return '—';
    const sign = n > 0 ? '+' : '';
    return `${sign}${Number(n).toFixed(2)}%`;
  }
  function fmtConfidence(c) {
    if (c == null) return '—';
    const pct = Math.round(Number(c) * 100);
    return `${pct}% conf`;
  }
  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[ch]);
  }
  function timeAgo(iso) {
    if (!iso) return '';
    const t = Date.parse(iso);
    if (!t) return '';
    const mins = Math.round((Date.now() - t) / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.round(hrs / 24);
    return `${days}d ago`;
  }

  function buildCardHtml(entry) {
    const confPct = Math.round(Number(entry.confidence || 0) * 100);
    const confClass = confPct >= 80 ? 'high' : confPct >= 60 ? 'mid' : 'low';
    const sources = (entry.sources || []).map((s) => {
      return `<a class="ma-pending-source" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.label || s.url)}</a>`;
    }).join('');

    const gates = entry.gates || {};
    const gatesRow = [
      ['Move', gates.gate1_move],
      ['Tier-1', gates.gate2_tier1],
      ['Named buyer', gates.gate3_named_buyer],
      ['Timing', gates.gate4_timing],
    ].map(([label, ok]) => {
      const cls = ok ? 'pass' : 'fail';
      const mark = ok ? '\u2713' : '\u2717';
      return `<span class="ma-gate ${cls}">${mark} ${label}</span>`;
    }).join('');

    return `
      <article class="ma-pending-card" data-ticker="${escapeHtml(entry.ticker)}">
        <header class="ma-pending-head">
          <div class="ma-pending-title">
            <strong>${escapeHtml(entry.ticker)}</strong>
            <span class="ma-pending-company">${escapeHtml(entry.company || '')}</span>
          </div>
          <div class="ma-pending-meta">
            <span class="ma-pending-conf ${confClass}">${confPct}% conf</span>
            <span class="ma-pending-move">${fmtPct(entry.intraday_move_pct)}</span>
            <span class="ma-pending-when">${escapeHtml(timeAgo(entry.flagged_at))}</span>
          </div>
        </header>
        <div class="ma-pending-body">
          <div class="ma-pending-headline">${escapeHtml(entry.headline || '(no headline)')}</div>
          <div class="ma-pending-rationale">${escapeHtml(entry.rationale || '')}</div>
          <div class="ma-pending-buyer">
            <span class="ma-pending-label">Buyer:</span>
            <strong>${escapeHtml(entry.buyer || 'Unnamed')}</strong>
            <span class="ma-pending-sep">·</span>
            <span class="ma-pending-label">Type:</span>
            <span>${escapeHtml(entry.deal_type || '—')}</span>
          </div>
          <div class="ma-pending-gates">${gatesRow}</div>
          ${sources ? `<div class="ma-pending-sources">${sources}</div>` : ''}
        </div>
        <footer class="ma-pending-actions">
          <button class="btn btn-approve" data-action="approve" data-ticker="${escapeHtml(entry.ticker)}">Approve as RUMOR</button>
          <button class="btn btn-reject" data-action="reject" data-ticker="${escapeHtml(entry.ticker)}">Reject</button>
        </footer>
      </article>
    `;
  }

  function applyLocalDecisions(entries) {
    const decisions = readLocalDecisions();
    const cutoff = Date.now() - REJECT_COOLDOWN_DAYS * 24 * 60 * 60 * 1000;
    return entries.filter((e) => {
      const d = decisions[e.ticker];
      if (!d) return true;
      if (d.action === 'approved') return false; // already promoted
      if (d.action === 'rejected' && Date.parse(d.at) > cutoff) return false;
      return true;
    });
  }

  async function approveEntry(entry) {
    const decisions = readLocalDecisions();
    decisions[entry.ticker] = { action: 'approved', at: new Date().toISOString() };
    writeLocalDecisions(decisions);
    await supabaseUpsertDecision({
      ticker: entry.ticker,
      decision: 'approved',
      flagged_at: entry.flagged_at,
      decided_at: new Date().toISOString(),
      payload: entry,
    });
    // Update in-memory MaStatus so the pill renders immediately
    if (window.MaStatus && typeof window.MaStatus.injectRumor === 'function') {
      window.MaStatus.injectRumor(entry);
    }
    // Trigger row re-render so RUMOR pill appears
    document.dispatchEvent(new CustomEvent('ma-status:updated'));
  }
  async function rejectEntry(entry) {
    const decisions = readLocalDecisions();
    decisions[entry.ticker] = { action: 'rejected', at: new Date().toISOString() };
    writeLocalDecisions(decisions);
    await supabaseUpsertDecision({
      ticker: entry.ticker,
      decision: 'rejected',
      flagged_at: entry.flagged_at,
      decided_at: new Date().toISOString(),
      payload: null,
    });
  }

  let _cached = [];

  async function load() {
    try {
      const r = await fetch(PENDING_URL, { cache: 'no-store' });
      if (!r.ok) return [];
      const data = await r.json();
      _cached = Array.isArray(data.pending_review) ? data.pending_review : [];
      return _cached;
    } catch (_) {
      return [];
    }
  }

  function render(entries) {
    const section = document.getElementById('ma-pending-section');
    const list = document.getElementById('ma-pending-list');
    const count = document.getElementById('ma-pending-count');
    if (!section || !list || !count) return;
    const visible = applyLocalDecisions(entries || []);
    count.textContent = String(visible.length);
    if (!visible.length) {
      section.hidden = true;
      list.innerHTML = '';
      return;
    }
    section.hidden = false;
    list.innerHTML = visible.map(buildCardHtml).join('');
    list.querySelectorAll('[data-action]').forEach((btn) => {
      btn.addEventListener('click', async (ev) => {
        ev.preventDefault();
        const action = btn.getAttribute('data-action');
        const ticker = btn.getAttribute('data-ticker');
        const entry = visible.find((e) => e.ticker === ticker);
        if (!entry) return;
        btn.disabled = true;
        btn.textContent = action === 'approve' ? 'Approving...' : 'Rejecting...';
        try {
          if (action === 'approve') await approveEntry(entry);
          else await rejectEntry(entry);
        } catch (e) {
          console.error('[ma-pending] action failed', e);
        }
        // Re-render with the latest local decisions
        render(_cached);
      });
    });
  }

  async function init() {
    const entries = await load();
    render(entries);
    // Light auto-refresh every 5 minutes so newly-flagged items appear without reload.
    setInterval(async () => {
      const fresh = await load();
      render(fresh);
    }, 5 * 60 * 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.MaPending = { load, render, approveEntry, rejectEntry };
})();
