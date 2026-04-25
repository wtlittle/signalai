/* ===== EARNINGS-INTEL.JS — Canonical Earnings Intel page logic =====
   One persistent page per ticker. Pre-earnings and post-earnings are states
   of the same page, not separate notes. Full spec:
   /home/user/workspace/watchlist-app/earnings_intel_schema.md
*/

let earningsIntelData = null;
let earningsIntelLoading = null;

/** Load and cache the canonical earnings_intel.json */
async function loadEarningsIntel() {
  if (earningsIntelData) return earningsIntelData;
  if (earningsIntelLoading) return earningsIntelLoading;
  earningsIntelLoading = (async () => {
    try {
      let resp;
      if (window.SignalSnapshot && window.SignalSnapshot.fetchWithFallback) {
        resp = await window.SignalSnapshot.fetchWithFallback('earnings_intel.json', { cacheBust: true });
      } else {
        resp = await fetch('earnings_intel.json?v=' + Date.now());
      }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      earningsIntelData = await resp.json();
      window._earningsIntelData = earningsIntelData;
      // Once intel data is in memory, refresh the coverage summary tiles
      // so the Debate Intensity tile populates without waiting for the
      // next universe toggle.
      if (typeof window.updateCoverageSummaryTiles === 'function') {
        try { window.updateCoverageSummaryTiles(); } catch (_) {}
      }
      return earningsIntelData;
    } catch (e) {
      console.warn('Failed to load earnings_intel.json:', e);
      // fetchWithFallback marks failure internally when both primary + fallback fail.
      earningsIntelData = { tickers: {}, last_updated: null };
      return earningsIntelData;
    } finally {
      earningsIntelLoading = null;
    }
  })();
  return earningsIntelLoading;
}

/** Get the intel record for a ticker (may return null if no coverage yet) */
async function getEarningsIntel(ticker) {
  const d = await loadEarningsIntel();
  return (d && d.tickers && d.tickers[ticker]) || null;
}

// ---------------------------------------------------------------------------
// Debate Intensity — Contested Velocity Score (universe-level KPI)
//
// Per-ticker score = conflict_ratio * (1 - resolution_velocity)
//   conflict_ratio       = cross_cited_signals / total_signals
//   resolution_velocity  = resolved_with_timestamp / total_signals
// Server pre-computes per-ticker via compute_debate_scores.py and writes it
// onto each ticker as `debate_score.value` (0-100). The client consumes that
// value when present; otherwise it falls back to a conflict-only client-side
// calculation (no resolved_at means we cannot measure velocity in the browser).
// ---------------------------------------------------------------------------
window.SignalIntel = window.SignalIntel || {};
window.SignalIntel.computeDebateScore = function(tickers) {
  const data = window._earningsIntelData || earningsIntelData;
  if (!data) return null;
  const intel = data.tickers || {};
  const scores = [];
  (tickers || []).forEach(ticker => {
    const rec = intel[ticker];
    if (!rec) return;
    // Prefer pre-computed server-side value
    if (rec.debate_score && typeof rec.debate_score.value === 'number') {
      scores.push(rec.debate_score.value / 100);
      return;
    }
    // Client-side fallback: conflict_ratio only
    const sc = rec.signal_scorecard || [];
    if (!sc.length) return;
    const bullIds = new Set(
      ((rec.bull_case && rec.bull_case.pushes_higher) || [])
        .map(p => (p && typeof p === 'object') ? p.signal_id : null)
        .filter(Boolean)
    );
    const bearIds = new Set(
      ((rec.bear_case && rec.bear_case.pushes_lower) || [])
        .map(p => (p && typeof p === 'object') ? p.signal_id : null)
        .filter(Boolean)
    );
    const cross = sc.filter(s => bullIds.has(s.signal_id) && bearIds.has(s.signal_id)).length;
    scores.push(cross / sc.length);
  });
  if (!scores.length) return null;
  return Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 100);
};

/** Inflection badge classes */
function inflectionBadge(status) {
  const map = {
    PRE:  { label: 'PRE',  cls: 'inflection-pre',  title: 'Pre-earnings window' },
    MID:  { label: 'MID',  cls: 'inflection-mid',  title: 'Earnings day / day after' },
    POST: { label: 'POST', cls: 'inflection-post', title: 'Post-earnings review active' },
    NONE: { label: '—',    cls: 'inflection-none', title: 'No upcoming event' },
  };
  return map[status] || map.NONE;
}

/** Human-friendly relative time */
function relativeTime(isoStr) {
  if (!isoStr) return '';
  try {
    const then = new Date(isoStr);
    const diffMs = Date.now() - then.getTime();
    const h = Math.floor(diffMs / 3600000);
    if (h < 1) return 'just now';
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    if (d < 7) return `${d}d ago`;
    return then.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch (e) { return ''; }
}

/** Signal scorecard compact summary e.g. "1C · 0F · 2W" */
function signalSummary(scorecard) {
  if (!scorecard || scorecard.length === 0) return '';
  const c = scorecard.filter(s => s.status === 'CONFIRMED').length;
  const f = scorecard.filter(s => s.status === 'FAILED').length;
  const w = scorecard.filter(s => s.status === 'WATCHING').length;
  return `${c}C · ${f}F · ${w}W`;
}

/** Build only the header strip (fast, no heavy sections) from any intel record. */
function renderEarningsIntelHeaderHtml(ticker, intel) {
  if (!intel) {
    return `<div class="ei-header">
      <div class="ei-header-left">
        <span class="ei-inflection inflection-none">—</span>
        <div class="ei-header-meta">
          <div class="ei-company-line"><strong>${ticker}</strong></div>
          <div class="ei-header-sub"><span>No coverage yet</span></div>
        </div>
      </div>
    </div>`;
  }
  const state = intel.state || 'idle';
  const inflection = inflectionBadge(intel.inflection_status || 'NONE');
  return `
    <div class="ei-header">
      <div class="ei-header-left">
        <span class="ei-inflection ${inflection.cls}" title="${inflection.title}">${inflection.label}</span>
        <div class="ei-header-meta">
          <div class="ei-company-line">
            <strong>${intel.company_name || ticker}</strong>
            <span class="ei-state-pill ei-state-${state}">${state.replace('_', ' ')}</span>
          </div>
          <div class="ei-header-sub">
            ${intel.next_earnings_date ? `<span>Next earnings: <strong>${intel.next_earnings_date}</strong></span>` : ''}
            ${intel.last_earnings_date ? `<span>Last: ${intel.last_earnings_date}</span>` : ''}
            <span>Signals: ${signalSummary(intel.signal_scorecard)}</span>
          </div>
        </div>
      </div>
      <div class="ei-header-right">
        <div class="ei-updated-label">Updated</div>
        <div class="ei-updated-value">${relativeTime(intel.intel_updated_at) || '—'}</div>
        ${intel.refresh_reason ? `<div class="ei-refresh-reason">${intel.refresh_reason.replace(/_/g, ' ')}</div>` : ''}
      </div>
    </div>`;
}

/** Skeleton body for sections still loading */
function renderEarningsIntelSkeletonBody() {
  return `
    <section class="ei-section ei-bottom-line ei-skeleton-block" data-ei-skeleton="bottom_line">
      <div class="ei-section-label">Bottom Line</div>
      <div class="ei-skeleton-lines">
        <div class="ei-skeleton-line"></div>
        <div class="ei-skeleton-line"></div>
        <div class="ei-skeleton-line"></div>
        <div class="ei-skeleton-line ei-skeleton-line-short"></div>
      </div>
    </section>
    <section class="ei-cases ei-skeleton-block" data-ei-skeleton="cases">
      ${['Bull Case', 'Base Case', 'Bear Case'].map(lbl => `
        <div class="ei-case">
          <div class="ei-case-title">${lbl}</div>
          <div class="ei-skeleton-lines">
            ${Array.from({length: 5}).map(() => '<div class="ei-skeleton-line"></div>').join('')}
          </div>
        </div>`).join('')}
    </section>`;
}

/** Build the Earnings Intel HTML for a ticker. Returns '' if no intel exists. */
function renderEarningsIntelHtml(ticker, intel) {
  if (!intel) {
    return `
      <div class="ei-empty">
        <div class="ei-empty-icon">◇</div>
        <div class="ei-empty-title">No Earnings Intel yet for ${ticker}</div>
        <div class="ei-empty-body">This page will populate automatically as the next earnings window approaches, or when a pre-earnings refresh is scheduled.</div>
      </div>`;
  }

  const state = intel.state || 'idle';
  const inflection = inflectionBadge(intel.inflection_status || 'NONE');
  const review = intel.post_earnings_review || {};
  const reviewActive = state === 'post_earnings' && review.active;

  // --- HEADER STRIP ---
  let html = `<div class="ei-root">`;
  html += `
    <div class="ei-header">
      <div class="ei-header-left">
        <span class="ei-inflection ${inflection.cls}" title="${inflection.title}">${inflection.label}</span>
        <div class="ei-header-meta">
          <div class="ei-company-line">
            <strong>${intel.company_name || ticker}</strong>
            <span class="ei-state-pill ei-state-${state}">${state.replace('_', ' ')}</span>
          </div>
          <div class="ei-header-sub">
            ${intel.next_earnings_date ? `<span>Next earnings: <strong>${intel.next_earnings_date}</strong></span>` : ''}
            ${intel.last_earnings_date ? `<span>Last: ${intel.last_earnings_date}</span>` : ''}
            <span>Signals: ${signalSummary(intel.signal_scorecard)}</span>
          </div>
        </div>
      </div>
      <div class="ei-header-right">
        <div class="ei-updated-label">Updated</div>
        <div class="ei-updated-value">${relativeTime(intel.intel_updated_at) || '—'}</div>
        ${intel.refresh_reason ? `<div class="ei-refresh-reason">${intel.refresh_reason.replace(/_/g, ' ')}</div>` : ''}
      </div>
    </div>`;

  // --- POST-EARNINGS REVIEW (top, prominent, 3-7 days) ---
  if (reviewActive) {
    const until = review.visible_until ? ` · visible through ${review.visible_until}` : '';
    html += `
      <section class="ei-review">
        <div class="ei-review-badge">Post-Earnings Review${until}</div>
        <div class="ei-review-grid">
          <div class="ei-review-col">
            <div class="ei-subhead">Takeaways</div>
            ${review.takeaways_headline ? `<div class="ei-headline">${review.takeaways_headline}</div>` : ''}
            ${Array.isArray(review.takeaways_bullets) && review.takeaways_bullets.length ? `<ul class="ei-bullets">${review.takeaways_bullets.map(b => `<li>${b}</li>`).join('')}</ul>` : ''}
          </div>
          <div class="ei-review-col">
            <div class="ei-subhead">What Happened</div>
            ${review.what_happened_headline ? `<div class="ei-headline">${review.what_happened_headline}</div>` : ''}
            ${Array.isArray(review.what_happened_bullets) && review.what_happened_bullets.length ? `<ul class="ei-bullets">${review.what_happened_bullets.map(b => `<li>${b}</li>`).join('')}</ul>` : ''}
            ${review.stock_reaction_pct != null ? `<div class="ei-stock-reaction ${review.stock_reaction_pct >= 0 ? 'val-pos' : 'val-neg'}">Stock reaction: ${review.stock_reaction_pct >= 0 ? '+' : ''}${review.stock_reaction_pct.toFixed(1)}%</div>` : ''}
          </div>
        </div>
        ${intel.previous_bottom_line ? `
          <details class="ei-prev-bl">
            <summary>Previous bottom line (pre-print)</summary>
            <div class="ei-prev-bl-body">${intel.previous_bottom_line}</div>
          </details>` : ''}
        ${Array.isArray(intel.signal_changes) && intel.signal_changes.length ? `
          <div class="ei-signal-changes">
            <div class="ei-subhead">Signal Changes</div>
            <ul class="ei-changes">${intel.signal_changes.map(c => `<li><span class="ei-change-signal">${c.signal_id.replace(/_/g, ' ')}</span>: <span class="ei-status ei-status-${(c.old_status||'').toLowerCase()}">${c.old_status}</span> <span class="ei-arrow">→</span> <span class="ei-status ei-status-${(c.new_status||'').toLowerCase()}">${c.new_status}</span>${c.note ? ` — ${c.note}` : ''}</li>`).join('')}</ul>
          </div>` : ''}
      </section>`;
  }

  // --- BOTTOM LINE ---
  if (intel.bottom_line) {
    html += `
      <section class="ei-section ei-bottom-line">
        <div class="ei-section-label">Bottom Line</div>
        <div class="ei-bl-body">${intel.bottom_line}</div>
      </section>`;
  }

  // --- BULL / BASE / BEAR CASES ---
  const cases = [
    { kind: 'bull', data: intel.bull_case, label: 'Bull Case', cls: 'ei-case-bull' },
    { kind: 'base', data: intel.base_case, label: 'Base Case', cls: 'ei-case-base' },
    { kind: 'bear', data: intel.bear_case, label: 'Bear Case', cls: 'ei-case-bear' },
  ].filter(c => c.data);

  if (cases.length) {
    html += `<section class="ei-cases">`;
    cases.forEach(c => {
      const d = c.data;
      const headline = d.thesis_headline || d.setup_headline || '';
      html += `
        <div class="ei-case ${c.cls}">
          <div class="ei-case-title">${c.label}</div>
          ${headline ? `<div class="ei-case-headline">${headline}</div>` : ''}
          ${d.pattern ? `<div class="ei-case-pattern">${d.pattern}</div>` : ''}
          ${Array.isArray(d.pushes_higher) && d.pushes_higher.length ? `
            <div class="ei-push-block">
              <div class="ei-push-label ei-push-higher">Pushes higher</div>
              <ul class="ei-bullets">${d.pushes_higher.map(b => `<li>${b}</li>`).join('')}</ul>
            </div>` : ''}
          ${Array.isArray(d.pushes_lower) && d.pushes_lower.length ? `
            <div class="ei-push-block">
              <div class="ei-push-label ei-push-lower">Pushes lower</div>
              <ul class="ei-bullets">${d.pushes_lower.map(b => `<li>${b}</li>`).join('')}</ul>
            </div>` : ''}
        </div>`;
    });
    html += `</section>`;
  }

  // --- SIGNAL SCORECARD ---
  if (Array.isArray(intel.signal_scorecard) && intel.signal_scorecard.length) {
    html += `
      <section class="ei-section ei-scorecard">
        <div class="ei-section-label">Signal Scorecard</div>
        <div class="ei-scorecard-grid">`;
    intel.signal_scorecard.forEach(s => {
      const status = (s.status || 'WATCHING').toLowerCase();
      const statusLabel = (s.status || 'WATCHING');
      html += `
        <div class="ei-signal ei-signal-${status}">
          <div class="ei-signal-head">
            <span class="ei-signal-label">${s.label || s.signal_id || ''}</span>
            <span class="ei-status ei-status-${status}">${statusLabel}</span>
          </div>
          ${s.note ? `<div class="ei-signal-note">${s.note}</div>` : ''}
          <div class="ei-signal-meta">
            ${s.watch_quarter ? `<span>Watch: <strong>${s.watch_quarter}</strong></span>` : ''}
            ${s.confirmed_threshold ? `<span class="ei-threshold val-pos">✓ ${s.confirmed_threshold}</span>` : ''}
            ${s.failed_threshold ? `<span class="ei-threshold val-neg">✗ ${s.failed_threshold}</span>` : ''}
          </div>
        </div>`;
    });
    html += `</div></section>`;
  }

  // --- GUIDANCE PROFILE + TONE DRIFT (side by side) ---
  const gp = intel.guidance_profile;
  const td = intel.tone_drift;
  if (gp || td) {
    html += `<section class="ei-split">`;
    if (gp) {
      const fmt = v => v == null ? '—' : v;
      const fmtRev = v => v == null ? '—' : `$${(v / 1000).toFixed(1)}B`;
      const epsRange = gp.fy_guide_eps_low != null && gp.fy_guide_eps_high != null
        ? `$${gp.fy_guide_eps_low.toFixed(2)} – $${gp.fy_guide_eps_high.toFixed(2)}`
        : gp.fy_guide_eps_low != null ? `≥ $${gp.fy_guide_eps_low.toFixed(2)}` : '—';
      const revRange = gp.fy_guide_revenue_low != null && gp.fy_guide_revenue_high != null
        ? `${fmtRev(gp.fy_guide_revenue_low)} – ${fmtRev(gp.fy_guide_revenue_high)}`
        : gp.fy_guide_revenue_low != null ? `≥ ${fmtRev(gp.fy_guide_revenue_low)}` : '—';
      html += `
        <div class="ei-split-col">
          <div class="ei-section-label">Guidance Profile</div>
          <div class="ei-guide-grid">
            <div><span class="ei-guide-k">FY EPS Guide</span><span class="ei-guide-v">${epsRange}</span></div>
            <div><span class="ei-guide-k">FY Revenue Guide</span><span class="ei-guide-v">${revRange}</span></div>
            ${gp.fy_mcr_target_midpoint != null ? `<div><span class="ei-guide-k">FY MCR Target</span><span class="ei-guide-v">${gp.fy_mcr_target_midpoint}% ${gp.fy_mcr_target_range_bps ? `±${gp.fy_mcr_target_range_bps}bps` : ''}</span></div>` : ''}
            <div><span class="ei-guide-k">Style</span><span class="ei-guide-v">${fmt(gp.guide_style)}</span></div>
            <div><span class="ei-guide-k">Last Changed</span><span class="ei-guide-v">${fmt(gp.last_changed)}</span></div>
          </div>
        </div>`;
    }
    if (td) {
      html += `
        <div class="ei-split-col">
          <div class="ei-section-label">Tone Drift</div>
          <div class="ei-tone-chips">
            <span class="ei-tone-chip ei-tone-prior">${(td.prior_tone || '—').replace(/_/g, ' ')}</span>
            <span class="ei-tone-arrow">→</span>
            <span class="ei-tone-chip ei-tone-current">${(td.current_tone || '—').replace(/_/g, ' ')}</span>
          </div>
          ${td.tone_notes ? `<div class="ei-tone-notes">${td.tone_notes}</div>` : ''}
        </div>`;
    }
    html += `</section>`;
  }

  // --- THEME LIFECYCLE ---
  if (Array.isArray(intel.theme_lifecycle) && intel.theme_lifecycle.length) {
    html += `
      <section class="ei-section">
        <div class="ei-section-label">Theme Lifecycle</div>
        <div class="ei-themes">`;
    intel.theme_lifecycle.forEach(t => {
      const stage = (t.stage || '').toLowerCase();
      html += `<div class="ei-theme ei-theme-${stage}">
        <span class="ei-theme-name">${t.theme}</span>
        <span class="ei-theme-stage">${t.stage || ''}</span>
        ${t.since ? `<span class="ei-theme-since">since ${t.since}</span>` : ''}
      </div>`;
    });
    html += `</div></section>`;
  }

  // --- INFLECTION LIBRARY ---
  if (Array.isArray(intel.inflection_library) && intel.inflection_library.length) {
    html += `
      <section class="ei-section">
        <div class="ei-section-label">Inflection Library</div>
        <div class="ei-inflections">`;
    intel.inflection_library.forEach(inf => {
      const react = inf.stock_reaction_pct;
      const reactCls = react == null ? '' : react >= 0 ? 'val-pos' : 'val-neg';
      const reactStr = react == null ? '' : `${react >= 0 ? '+' : ''}${react.toFixed(1)}%`;
      html += `<div class="ei-inflection-item">
        <div class="ei-inflection-date">${inf.date || ''}</div>
        <div class="ei-inflection-body">
          <div class="ei-inflection-type">${(inf.type || '').replace(/_/g, ' ')}</div>
          <div class="ei-inflection-headline">${inf.headline || ''}</div>
        </div>
        ${reactStr ? `<div class="ei-inflection-react ${reactCls}">${reactStr}</div>` : ''}
      </div>`;
    });
    html += `</div></section>`;
  }

  // --- SOURCES ---
  const srcs = (intel.source_metadata && intel.source_metadata.primary_sources) || [];
  if (srcs.length || (intel.source_metadata && intel.source_metadata.legacy_note_path)) {
    html += `<section class="ei-section ei-sources">
      <div class="ei-section-label">Sources</div>
      <ul class="ei-sources-list">`;
    srcs.forEach(s => {
      html += `<li><a href="${s.url}" target="_blank" rel="noopener noreferrer">${s.label || s.url}</a></li>`;
    });
    if (intel.source_metadata && intel.source_metadata.legacy_note_path) {
      html += `<li><a href="${intel.source_metadata.legacy_note_path}" target="_blank" rel="noopener noreferrer" class="ei-legacy-link">Legacy note (archive)</a></li>`;
    }
    html += `</ul></section>`;
  }

  html += `</div>`; // .ei-root
  return html;
}

/** Entry point called from popup.js when the Earnings Intel tab is activated.
 * Renders header synchronously from cache (non-blocking). Shows skeleton body
 * while full intel fetches. Close X is never blocked. */
async function renderEarningsIntelTab(container, ticker) {
  if (!container) return;

  // 1. Try to render header immediately from already-loaded cache
  const cached = (earningsIntelData && earningsIntelData.tickers && earningsIntelData.tickers[ticker]) || null;
  container.innerHTML = `<div class="ei-root">
    ${renderEarningsIntelHeaderHtml(ticker, cached)}
    <div class="ei-body-zone" id="ei-body-zone-${ticker}">
      ${renderEarningsIntelSkeletonBody()}
    </div>
  </div>`;

  // 2. Fetch (or re-use) full intel and replace body
  try {
    const intel = await getEarningsIntel(ticker);
    // Header may have changed; re-render whole thing for full fidelity
    container.innerHTML = renderEarningsIntelHtml(ticker, intel);
  } catch (e) {
    console.error('Earnings Intel render error:', e);
    const zone = document.getElementById(`ei-body-zone-${ticker}`);
    if (zone) {
      zone.innerHTML = `<div class="ei-section-error">
        <div>Failed to load Earnings Intel sections.</div>
        <button class="btn-sm" onclick="renderEarningsIntelTab(document.getElementById('popup-panel-earnings-intel'), '${ticker}')">Retry</button>
      </div>`;
    } else {
      container.innerHTML = `<div class="ei-error">Failed to load Earnings Intel for ${ticker}. <button class="btn-sm" onclick="renderEarningsIntelTab(this.closest('.popup-tab-panel, #popup-panel-earnings-intel') || document.getElementById('popup-panel-earnings-intel'), '${ticker}')">Retry</button></div>`;
    }
  }
}

// Expose globals for popup.js and earnings.js
window.loadEarningsIntel = loadEarningsIntel;
window.getEarningsIntel = getEarningsIntel;
window.renderEarningsIntelTab = renderEarningsIntelTab;
window.inflectionBadge = inflectionBadge;
window.signalSummary = signalSummary;
