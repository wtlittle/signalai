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
    let resp;
    if (window.SignalSnapshot && window.SignalSnapshot.fetchWithFallback) {
      resp = await window.SignalSnapshot.fetchWithFallback('earnings_notes_index.json', { cacheBust: true });
    } else {
      resp = await fetch('earnings_notes_index.json?v=' + Date.now());
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    earningsNotesIndex = await resp.json();
    return earningsNotesIndex;
  } catch (e) {
    console.warn('Failed to load earnings notes index:', e);
    // fetchWithFallback marks failure internally when both primary + fallback fail.
    return null;
  }
}

// --- Enrich post-earnings cards from earnings_intel.json ---
// Covers the fallback path, where the notes index has ticker/date only and
// the calendar's post_earnings lacks revenue_actual / eps_actual. Intel's
// post_earnings_review contains what_happened_bullets in the format:
//   "Revenue · NT$1.134T ($35.9B) · NT$1.127T ($35B)"   (label · actual · estimate)
// We parse those to rehydrate Rev / EPS / reaction so cards stop showing "—".
async function enrichRecentFromIntel(recent) {
  if (!recent || !recent.length) return recent;
  if (typeof loadEarningsIntel !== 'function') return recent;
  let intelData;
  try { intelData = await loadEarningsIntel(); } catch (_) { return recent; }
  if (!intelData || !intelData.tickers) return recent;

  const parseMetricBullet = (bullet, labelRegex) => {
    if (!bullet || !labelRegex.test(bullet)) return null;
    // Split on "·" or "|" delimiters; cards use " · ".
    const parts = bullet.split(/\s[·|]\s/).map(s => s.trim());
    // Expected shape: [label, actual, estimate?]
    if (parts.length < 2) return null;
    const actual = parts[1];
    if (!actual || actual === '--' || actual === '—') return null;
    return actual;
  };

  recent.forEach(r => {
    const intel = intelData.tickers[r.ticker];
    if (!intel) return;

    // note_status drives the secondary card line and the print/call note body.
    // Surface it (and the timing flag) onto the card record regardless of
    // whether a rich post_earnings_review exists yet — a print_reaction ticker
    // has actuals + a short note but no review block.
    if (r.note_status == null && intel.note_status != null) {
      r.note_status = intel.note_status;
    }
    if ((r.timing == null || r.timing === 'TBD') && intel.timing) {
      r.timing = intel.timing;
    }
    // Print/call reaction note: prefer the short reaction note as the card
    // body when no richer post_earnings_review headline is present.
    if (!r.stock_reaction && intel.reaction_note) {
      r.stock_reaction = String(intel.reaction_note).slice(0, 220);
    }

    const rev = (intel.post_earnings_review && intel.post_earnings_review.active === true)
      ? intel.post_earnings_review
      : null;
    if (rev) {
      const bullets = rev.what_happened_bullets || [];
      // Revenue actual
      if (!r.revenue_actual) {
        for (const b of bullets) {
          const v = parseMetricBullet(b, /^revenue\b/i);
          if (v) { r.revenue_actual = v; break; }
        }
      }
      // EPS actual (look for "EPS" or "Adjusted EPS" bullet)
      if (!r.eps_actual) {
        for (const b of bullets) {
          const v = parseMetricBullet(b, /\beps\b/i);
          if (v) { r.eps_actual = v; break; }
        }
      }
      // Stock reaction pct — prominent number from post_earnings_review
      if (rev.stock_reaction_pct != null && r.stock_reaction_pct == null) {
        r.stock_reaction_pct = rev.stock_reaction_pct;
      }
      // Stock reaction fallback — use takeaways_headline so card body isn't "No data"
      if (!r.stock_reaction && rev.takeaways_headline) {
        r.stock_reaction = rev.takeaways_headline.slice(0, 180);
      }
    }
    // Fiscal quarter inference from earnings_date if missing
    if (!r.fiscal_quarter && r.earnings_date) {
      const m = /^(\d{4})-(\d{2})/.exec(r.earnings_date);
      if (m) {
        const mon = +m[2];
        const q = mon <= 3 ? 'Q4' : mon <= 6 ? 'Q1' : mon <= 9 ? 'Q2' : 'Q3';
        r.fiscal_quarter = `${q} ${m[1]}`;
      }
    }

    // Guide fields from guide_vs_consensus envelope.
    // The canonical schema field that drives "FY Guide Δ" is
    // fy_rev_change_vs_consensus_pct (see earnings_intel_schema.md). The
    // calendar pipeline carries the same value under the longer name
    // fy_rev_guide_change_vs_consensus_pct, so accept either, mapping the
    // envelope name onto the field the renderer reads (r.fy_rev_change_vs_consensus_pct).
    const gvc = intel.guide_vs_consensus;
    if (gvc) {
      if (r.fy_rev_change_vs_consensus_pct == null && gvc.fy_rev_change_vs_consensus_pct != null) {
        r.fy_rev_change_vs_consensus_pct = gvc.fy_rev_change_vs_consensus_pct;
      }
      if (r.fy_rev_guide_change_vs_consensus_pct == null && gvc.fy_rev_guide_change_vs_consensus_pct != null) {
        r.fy_rev_guide_change_vs_consensus_pct = gvc.fy_rev_guide_change_vs_consensus_pct;
      }
      if (r.fy_eps_guide_change_vs_consensus_pct == null && gvc.fy_eps_guide_change_vs_consensus_pct != null) {
        r.fy_eps_guide_change_vs_consensus_pct = gvc.fy_eps_guide_change_vs_consensus_pct;
      }
      if (r.next_q_rev_guide_vs_consensus_pct == null && gvc.next_q_rev_guide_vs_consensus_pct != null) {
        r.next_q_rev_guide_vs_consensus_pct = gvc.next_q_rev_guide_vs_consensus_pct;
      }
      if (r.next_q_eps_guide_vs_consensus_pct == null && gvc.next_q_eps_guide_vs_consensus_pct != null) {
        r.next_q_eps_guide_vs_consensus_pct = gvc.next_q_eps_guide_vs_consensus_pct;
      }
      // Derive the normalized split-guidance fields (revenue + profitability)
      // from the raw envelope so the two-pill row works even before the
      // backend recomputes them onto the calendar row.
      _applyNormalizedGuidanceFromEnvelope(r, gvc);
    }

    // Results fields from results_vs_consensus envelope. Surface the structured
    // surprise % under the schema field names the renderer reads directly
    // (r.in_quarter_rev_surprise_pct / r.in_quarter_eps_surprise_pct) and keep
    // the beat/miss string as a fallback for older calendar-only rows.
    const rvc = intel.results_vs_consensus;
    if (rvc) {
      if (!r.revenue_actual && rvc.in_quarter_rev_actual) r.revenue_actual = rvc.in_quarter_rev_actual;
      if (!r.eps_actual && rvc.in_quarter_eps_actual) r.eps_actual = rvc.in_quarter_eps_actual;
      // Per-field provenance (Guardrail F): resolve the canonical <field>_source
      // sibling, falling back to the legacy source key names the backfills
      // historically wrote, so the card tooltip can show finnhub / yfinance /
      // perplexity / note / manual. Only attribute a source when the actual
      // exists -- never label a value that is not there.
      if (!r.revenue_source && rvc.in_quarter_rev_actual) {
        r.revenue_source = rvc.in_quarter_rev_actual_source || rvc.rev_actual_source || rvc.rev_consensus_source || null;
      }
      if (!r.eps_source && rvc.in_quarter_eps_actual) {
        r.eps_source = rvc.in_quarter_eps_actual_source || rvc.eps_actual_source || rvc.eps_consensus_source || null;
      }
      if (r.in_quarter_rev_surprise_pct == null && rvc.in_quarter_rev_surprise_pct != null) {
        r.in_quarter_rev_surprise_pct = rvc.in_quarter_rev_surprise_pct;
      }
      if (r.in_quarter_eps_surprise_pct == null && rvc.in_quarter_eps_surprise_pct != null) {
        r.in_quarter_eps_surprise_pct = rvc.in_quarter_eps_surprise_pct;
      }
      if (!r.revenue_beat_miss && rvc.in_quarter_rev_surprise_pct != null) {
        const s = rvc.in_quarter_rev_surprise_pct;
        r.revenue_beat_miss = s > 1 ? `beat (+${s.toFixed(1)}%)` : s < -1 ? `miss (${s.toFixed(1)}%)` : 'in-line';
      }
      if (!r.eps_beat_miss && rvc.in_quarter_eps_surprise_pct != null) {
        const s = rvc.in_quarter_eps_surprise_pct;
        r.eps_beat_miss = s > 1 ? `beat (+${s.toFixed(1)}%)` : s < -1 ? `miss (${s.toFixed(1)}%)` : 'in-line';
      }
    }
  });
  return recent;
}

// --- Guide row helper for post-earnings cards ---
function _guideColor(pct) {
  if (pct == null) return 'guide-neutral';
  if (pct > 0.5) return 'guide-positive';
  if (pct < -0.5) return 'guide-negative';
  return 'guide-neutral';
}

function _fmtGuidePct(pct) {
  if (pct == null) return null;
  const sign = pct > 0 ? '+' : '';
  return sign + pct.toFixed(1) + '%';
}

function _renderGuideRow(label, revPct, epsPct) {
  if (revPct == null && epsPct == null) return '';
  const parts = [];
  if (revPct != null) {
    parts.push(`<span class="guide-metric ${_guideColor(revPct)}">Rev ${_fmtGuidePct(revPct)} vs cons</span>`);
  }
  if (epsPct != null) {
    parts.push(`<span class="guide-metric ${_guideColor(epsPct)}">EPS ${_fmtGuidePct(epsPct)} vs cons</span>`);
  }
  return `<div class="earnings-guide-row"><span class="guide-label">${label}</span>${parts.join('<span class="guide-sep">&middot;</span>')}</div>`;
}

// --- Beat/miss surprise helpers for post-earnings REV / EPS rows ---
// Resolve a numeric surprise % for a metric. Prefer the structured field
// (e.g. in_quarter_rev_surprise_pct); fall back to parsing the % out of a
// Phase-4 beat/miss string like "beat (+2.1%)" / "miss (-0.8%)".
function _resolveSurprisePct(structuredPct, beatMissStr) {
  if (typeof structuredPct === 'number' && !Number.isNaN(structuredPct)) return structuredPct;
  if (typeof beatMissStr === 'string') {
    const m = beatMissStr.match(/([+-]?\d+(?:\.\d+)?)\s*%/);
    if (m) return parseFloat(m[1]);
  }
  return null;
}

// === Safe display formatters (card hardening) =============================
// Every dynamic card field flows through these. They guarantee no card can
// emit NaN / undefined / null / Infinity / "$$" / doubled signs / a bare "—"
// in place of an expected value. They NEVER fabricate a value: missing input
// degrades to an explicit "n/a", it is never backfilled from a constant.

// Tokens that should never appear inside a rendered value. Used by the audit.
const FORBIDDEN_DISPLAY_TOKENS = ['NaN', 'undefined', 'null', 'Infinity', '$$'];

// Coerce an arbitrary value to a finite number or null. Strings carrying a
// leading "$" and/or a B/M/K/T magnitude suffix are parsed; anything that
// cannot be made finite returns null (never NaN/Infinity).
function _toFiniteNumber(value) {
  if (value == null) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const s = value.trim().replace(/[$,\s]/g, '');
  const m = /^(-?\d+(?:\.\d+)?)\s*([kKmMbBtT])?$/.exec(s);
  if (!m) return null;
  const n = parseFloat(m[1]);
  if (!Number.isFinite(n)) return null;
  return n;
}

// safeMoney: render a monetary value with EXACTLY ONE "$". Strips any
// pre-existing "$" on the input before prepending so we can never produce
// "$$". The value must originate from the data layer; when it is null/blank
// or non-numeric we return 'n/a' rather than inventing a number. When `unit`
// is supplied (e.g. 'M', 'B') and the input is a bare number it is appended.
function safeMoney(value, { unit } = {}) {
  if (value == null) return 'n/a';
  if (typeof value === 'string') {
    // Already-formatted string from the data layer (e.g. "$276.7M", "276.7M").
    let s = value.trim();
    if (s === '' || s === '\u2014' || s === '--') return 'n/a';
    // Collapse any run of leading "$" to a single one we control.
    s = s.replace(/^\$+/, '');
    if (s === '') return 'n/a';
    return '$' + s;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return 'n/a';
    const suffix = unit ? unit : '';
    return '$' + value + suffix;
  }
  return 'n/a';
}

// safePct: null-safe signed percentage. Returns 'n/a' for null / non-finite
// input (never NaN / Infinity). A single leading sign only; never doubled.
function safePct(value, { decimals = 1, signed = true } = {}) {
  const n = (typeof value === 'number') ? value : _toFiniteNumber(value);
  if (n == null || !Number.isFinite(n)) return 'n/a';
  const rounded = Number(n.toFixed(decimals));
  const sign = signed && rounded > 0 ? '+' : (rounded < 0 ? '-' : '');
  return `${sign}${Math.abs(rounded).toFixed(decimals)}%`;
}

// safeReaction: resolve the post-print price reaction from a DATA-LAYER field.
// Returns { display, direction, available }. When the field is genuinely
// missing/non-finite we return available=false with display 'n/a' — we NEVER
// fabricate, remember, or interpolate a value. direction is used for color.
function safeReaction(postPrintPct, { flatThreshold = 0.05 } = {}) {
  const n = (typeof postPrintPct === 'number') ? postPrintPct : _toFiniteNumber(postPrintPct);
  if (n == null || !Number.isFinite(n)) {
    return { display: 'n/a', direction: 'neutral', available: false };
  }
  let direction;
  if (Math.abs(n) <= flatThreshold) direction = 'neutral';
  else direction = n > 0 ? 'positive' : 'negative';
  return { display: safePct(n, { decimals: 1, signed: true }), direction, available: true };
}

// classifyBeatMiss: bucket an in-quarter surprise % into beat / miss / in-line.
// flatThreshold is in percentage points (default 1.0 → ±1% is "in-line").
function classifyBeatMiss(deltaPct, flatThreshold = 1.0) {
  const n = (typeof deltaPct === 'number') ? deltaPct : _toFiniteNumber(deltaPct);
  if (n == null || !Number.isFinite(n)) return 'n/a';
  if (n > flatThreshold) return 'beat';
  if (n < -flatThreshold) return 'miss';
  return 'in-line';
}

// renderEarningsCard: compose the safe display tokens for a post-earnings
// card row. EVERY token is derived from data-layer fields on `card` (no
// hardcoded constants) and passed through the safe formatters above. Returns
// a token bag the template assembles; missing fields degrade to 'n/a' tokens,
// never to a fabricated or remembered value.
function renderEarningsCard(card) {
  const c = card || {};
  // Headline actuals — already formatted strings from the data layer; route
  // through safeMoney so we emit exactly one "$" and never a bare "—".
  const revenue = safeMoney(c.revenue_actual);
  const eps = safeMoney(c.eps_actual);
  // NO CONTRADICTORY STATES (Issue D): a surprise % may render ONLY when its
  // matching actual rendered. safeMoney returns 'n/a' when the actual is
  // missing, so a card can never show "EPS n/a +23% beat". When the actual is
  // present but consensus (surprise %) is absent, we show the actual alone —
  // that is a legitimate "reported, no consensus" state, not a contradiction.
  const revAvailable = revenue !== 'n/a';
  const epsAvailable = eps !== 'n/a';
  const revSurprisePct = revAvailable
    ? _resolveSurprisePct(c.in_quarter_rev_surprise_pct, c.revenue_beat_miss)
    : null;
  const epsSurprisePct = epsAvailable
    ? _resolveSurprisePct(c.in_quarter_eps_surprise_pct, c.eps_beat_miss)
    : null;
  // Post-print reaction — sourced FRESH from the data-layer field
  // stock_reaction_pct. safeReaction returns 'n/a' when the field is genuinely
  // missing; it never substitutes a guessed/remembered value.
  const reaction = safeReaction(c.stock_reaction_pct);

  // Render audit (Guardrail E / DATA INTEGRITY MANDATE): every rendered actual
  // must originate from the data layer, not a client-side fallback. A rendered
  // value is legitimate only when it is exactly 'n/a' OR it equals the
  // data-layer field it claims to represent. If a forbidden token (NaN /
  // undefined / null / Infinity / "$$") ever reaches a metric, surface it as a
  // console warning rather than letting a fake number ship silently.
  const audit = (label, rendered, raw) => {
    if (rendered === 'n/a') return;
    if (FORBIDDEN_DISPLAY_TOKENS.some(tok => String(rendered).indexOf(tok) !== -1)) {
      console.warn(`[earnings-card audit] ${label} rendered forbidden token: ${rendered} (raw=${raw})`);
    }
    if (raw == null || raw === '' || raw === '—' || raw === '--') {
      console.warn(`[earnings-card audit] ${label} rendered "${rendered}" but data-layer value is empty -- possible fabricated fallback`);
    }
  };
  audit('revenue', revenue, c.revenue_actual);
  audit('eps', eps, c.eps_actual);

  return { revenue, eps, reaction, revSurprisePct, epsSurprisePct };
}

// _sourceTitle: build the title= tooltip text for a card metric (Guardrail F).
// Shows where the rendered value came from so a future debugging session is
// instant: hover -> "Source: finnhub" vs "Source: manual" vs "Source: missing".
// When the value is n/a we say "missing"; we never invent a source for a value
// that does not exist.
function _sourceTitle(displayValue, source) {
  if (displayValue === 'n/a' || displayValue == null) return 'Source: missing';
  if (!source) return 'Source: unknown';
  return `Source: ${source}`;
}

// HTML-escape a string for safe inclusion in a title= attribute.
function _escAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Render a compact beat/miss span: "+2.1% beat" (green) / "-0.8% miss" (red),
// or a neutral "\u2014" placeholder when the surprise is unknown. Within the
// +/-1% band we label "in-line" (neutral) rather than beat/miss.
function _fmtBeatMiss(pct) {
  const label = classifyBeatMiss(pct);
  if (label === 'n/a') {
    return '<span class="metric-surprise neutral">\u2014</span>';
  }
  const cls = label === 'beat' ? 'beat' : label === 'miss' ? 'miss' : 'inline';
  return `<span class="metric-surprise ${cls}">${safePct(pct)} ${label}</span>`;
}

// Render the FY Guide \u0394 row: how much FY revenue guidance moved vs prior
// Street consensus. "+1.5% raise" (green) / "-2.0% cut" (red) / "\u2014" when
// unavailable. Accepts the structured field under either the calendar name
// (fy_rev_guide_change_vs_consensus_pct) or the envelope name
// (fy_rev_change_vs_consensus_pct).
function _renderFyGuideDeltaRow(pct) {
  let body;
  if (pct == null) {
    body = '<span class="guide-metric guide-neutral">\u2014</span>';
  } else {
    const sign = pct > 0 ? '+' : '';
    const word = pct > 0.5 ? 'raise' : pct < -0.5 ? 'cut' : 'flat';
    body = `<span class="guide-metric ${_guideColor(pct)}">${sign}${pct.toFixed(1)}% ${word}</span>`;
  }
  return `<div class="earnings-guide-row"><span class="guide-label">FY Guide \u0394</span>${body}</div>`;
}

// === Split FY guidance: revenue + profitability metric-specific pills ===
// The legacy single "FY Guide Δ" line above blended one number. POST cards now
// render TWO independent pills: revenue guidance Δ and the relevant
// profitability guidance Δ (EPS preferred; operating profit / FCF as
// fallbacks). Each metric selects its own baseline (prior consensus when
// available, else prior company guide) and tracks which via priorSource.

// Bucket a fractional delta (e.g. -0.007 for -0.7%) into raise / cut / flat.
// flatThreshold is in the same fractional units (0.005 = 0.5%). Returns 'n/a'
// when the delta is null or non-finite.
function classifyGuideDelta(deltaPct, flatThreshold = 0.005) {
  if (deltaPct == null || !Number.isFinite(deltaPct)) return 'n/a';
  if (Math.abs(deltaPct) <= flatThreshold) return 'flat';
  return deltaPct > 0 ? 'raise' : 'cut';
}

// Build the "+2.1%" / "-0.7%" / "+$0.02" string for a metric. Prefers a
// percentage when the delta is finite and meaningful. For profit metrics
// (EPS / OP PROFIT / OP INC / FCF) where the percentage is missing or not
// finite (tiny / zero-crossing baseline) it falls back to a signed absolute
// change so the UI never shows NaN/Infinity/absurd %.
function formatGuideDelta({ deltaPct, deltaAbs, metric }) {
  const isProfit = metric && metric !== 'REV';
  const pctOk = deltaPct != null && Number.isFinite(deltaPct);
  if (pctOk) {
    const pct = deltaPct * 100;
    // Guard against absurd magnitudes from a near-zero baseline on profit
    // metrics — prefer the absolute $ change instead.
    if (isProfit && Math.abs(pct) >= 100 && deltaAbs != null && Number.isFinite(deltaAbs)) {
      return _fmtGuideAbs(deltaAbs);
    }
    const sign = pct > 0 ? '+' : (pct < 0 ? '-' : '');
    return `${sign}${Math.abs(pct).toFixed(1)}%`;
  }
  if (deltaAbs != null && Number.isFinite(deltaAbs)) {
    return _fmtGuideAbs(deltaAbs);
  }
  return null;
}

// Format a signed absolute dollar change like "+$0.02" / "-$0.03".
function _fmtGuideAbs(deltaAbs) {
  const sign = deltaAbs > 0 ? '+' : (deltaAbs < 0 ? '-' : '');
  return `${sign}$${Math.abs(deltaAbs).toFixed(2)}`;
}

// Pick the profitability metric to display, in priority order:
//   EPS > Operating Profit / Operating Income > FCF.
// `guidance` is the raw card row. Returns a normalized metric descriptor or
// null when no profitability guidance is available.
function selectProfitabilityGuideMetric(g) {
  if (!g) return null;
  const num = (v) => (typeof v === 'number' && Number.isFinite(v)) ? v : null;
  const hasEps = num(g.guidanceEpsDeltaPct) != null || num(g.guidanceEpsDeltaAbs) != null;
  if (hasEps) {
    return {
      label: 'EPS',
      deltaPct: num(g.guidanceEpsDeltaPct),
      deltaAbs: num(g.guidanceEpsDeltaAbs),
      priorSource: g.guidanceEpsPriorSource || 'consensus',
      streetDeltaPct: num(g.guidanceEpsStreetDeltaPct),
      streetDeltaAbs: num(g.guidanceEpsStreetDeltaAbs),
    };
  }
  const hasOp = num(g.guidanceOperatingProfitDeltaPct) != null || num(g.guidanceOperatingProfitDeltaAbs) != null;
  if (hasOp) {
    // The metric used (operating profit vs operating income) is recorded on
    // guidanceProfitMetricUsed; default to "Op Profit".
    const used = (g.guidanceProfitMetricUsed === 'operating_income') ? 'Op Inc' : 'Op Profit';
    return {
      label: used,
      deltaPct: num(g.guidanceOperatingProfitDeltaPct),
      deltaAbs: num(g.guidanceOperatingProfitDeltaAbs),
      priorSource: g.guidanceOperatingProfitPriorSource || 'consensus',
      streetDeltaPct: num(g.guidanceOperatingProfitStreetDeltaPct),
      streetDeltaAbs: num(g.guidanceOperatingProfitStreetDeltaAbs),
    };
  }
  const hasFcf = num(g.guidanceFcfDeltaPct) != null || num(g.guidanceFcfDeltaAbs) != null;
  if (hasFcf) {
    return {
      label: 'FCF',
      deltaPct: num(g.guidanceFcfDeltaPct),
      deltaAbs: num(g.guidanceFcfDeltaAbs),
      priorSource: g.guidanceFcfPriorSource || 'consensus',
      streetDeltaPct: num(g.guidanceFcfStreetDeltaPct),
      streetDeltaAbs: num(g.guidanceFcfStreetDeltaAbs),
    };
  }
  return null;
}

// Title-case word for a raise/flat/cut direction. Used in the new
// industry-standard pill text ("Raise", "Cut", "Flat").
function _raiseCutWord(direction) {
  if (direction === 'raise') return 'Raise';
  if (direction === 'cut') return 'Cut';
  return 'Flat';
}

// Above / In-line / Below word for a vs-consensus delta (initial-quarter guide
// with no prior company guide). flatThreshold is fractional (0.005 = 0.5%).
function _aboveBelowWord(deltaPct, flatThreshold = 0.005) {
  if (deltaPct == null || !Number.isFinite(deltaPct)) return 'n/a';
  if (Math.abs(deltaPct) <= flatThreshold) return 'In-line';
  return deltaPct > 0 ? 'Above' : 'Below';
}

// Build the human-facing pill text for one metric, applying the industry
// framing rule:
//   * priorSource === 'prior_guidance' AND a vs-Street delta exists ->
//     "Raise +X%, +Y% vs Street" (Cut/Flat by the sign of the vs-prior-guide X).
//   * priorSource === 'prior_guidance' with no Street delta ->
//     "Raise +X%" (raise/cut vs prior guide only).
//   * priorSource === 'consensus' (initial-quarter guide, no prior guide) ->
//     "Above Street +Y%" / "In-line Street" / "Below Street -Y%". Never
//     raise/cut wording in this branch.
// `metricPrefix` ('' for revenue, 'EPS '/'Op Inc '/'FCF ' for profit) precedes
// the raise/cut word. Returns { text, direction } or null when nothing renders.
function buildGuidancePillText(metric, metricPrefix) {
  if (!metric) return null;
  const prefix = metricPrefix || '';
  if (metric.priorSource === 'prior_guidance') {
    const primary = metric.display;
    if (primary == null) return null;
    const word = _raiseCutWord(metric.direction);
    let text = `${word} ${prefix}${primary}`.trimEnd();
    const street = formatGuideDelta({
      deltaPct: metric.streetDeltaPct,
      deltaAbs: metric.streetDeltaAbs,
      metric: metric.label,
    });
    if (street != null) text += `, ${street} vs Street`;
    return { text, direction: metric.direction };
  }
  // Consensus baseline: above / in-line / below framing, no raise/cut.
  // Near a tiny baseline the pct is suppressed but an absolute $ delta still
  // carries the direction (e.g. EPS guide of $0.03 vs $0.01 consensus) -> fall
  // back to the abs sign so the magnitude is not silently dropped.
  let word = _aboveBelowWord(metric.deltaPct);
  if (word === 'n/a' && metric.deltaAbs != null && Number.isFinite(metric.deltaAbs)) {
    word = Math.abs(metric.deltaAbs) <= 0.0001 ? 'In-line' : (metric.deltaAbs > 0 ? 'Above' : 'Below');
  }
  if (word === 'n/a') return null;
  const labelBit = prefix ? prefix.trimEnd() + ' ' : '';
  if (word === 'In-line') {
    return { text: `${labelBit}In-line Street`.trim(), direction: 'flat' };
  }
  const mag = metric.display;
  if (mag == null) return null;
  const dir = word === 'Above' ? 'raise' : 'cut';
  return { text: `${labelBit}${word} Street ${mag}`.trim(), direction: dir };
}

// Resolve { baselineMidpoint, priorSource } for a metric: prefer the company's
// PRIOR GUIDE (so the primary delta is a raise/cut number), else fall back to
// prior Street consensus. Used by the backend mirror / any client-side
// recompute path. Returns null when neither baseline exists.
function selectGuidanceBaseline(metricData) {
  if (!metricData) return null;
  const num = (v) => (typeof v === 'number' && Number.isFinite(v)) ? v : null;
  const guide = num(metricData.priorGuideMidpoint);
  if (guide != null) return { baselineMidpoint: guide, priorSource: 'prior_guidance' };
  const cons = num(metricData.consensusPriorMidpoint);
  if (cons != null) return { baselineMidpoint: cons, priorSource: 'consensus' };
  return null;
}

// Normalize a raw card row into { revenue, profitability } display objects.
// Each side is null when its data is unavailable so callers can omit the pill.
function buildGuidanceChangeDisplay(g) {
  const num = (v) => (typeof v === 'number' && Number.isFinite(v)) ? v : null;

  let revenue = null;
  const revPct = num(g && g.guidanceRevenueDeltaPct);
  if (revPct != null) {
    const direction = classifyGuideDelta(revPct);
    const value = formatGuideDelta({ deltaPct: revPct, deltaAbs: null, metric: 'REV' });
    if (value != null) {
      revenue = {
        label: 'REV',
        deltaPct: revPct,
        direction,
        priorSource: (g.guidanceRevenuePriorSource) || 'consensus',
        streetDeltaPct: num(g.guidanceRevenueStreetDeltaPct),
        streetDeltaAbs: null,
        display: value,
      };
    }
  }

  let profitability = null;
  const prof = selectProfitabilityGuideMetric(g);
  if (prof) {
    const direction = classifyGuideDelta(prof.deltaPct);
    const value = formatGuideDelta({ deltaPct: prof.deltaPct, deltaAbs: prof.deltaAbs, metric: prof.label });
    if (value != null) {
      // When the rendered value is an absolute $ change, classify direction
      // off the absolute delta (pct may be null / unreliable near zero).
      const dir = direction === 'n/a' && prof.deltaAbs != null
        ? (Math.abs(prof.deltaAbs) <= 0.0001 ? 'flat' : (prof.deltaAbs > 0 ? 'raise' : 'cut'))
        : direction;
      profitability = {
        label: prof.label,
        deltaPct: prof.deltaPct,
        deltaAbs: prof.deltaAbs,
        direction: dir,
        priorSource: prof.priorSource || 'consensus',
        streetDeltaPct: prof.streetDeltaPct,
        streetDeltaAbs: prof.streetDeltaAbs,
        display: value,
      };
    }
  }

  return { revenue, profitability };
}

// Map a normalized direction to the pill color class.
function _guideDirClass(direction) {
  if (direction === 'raise') return 'guide-positive';
  if (direction === 'cut') return 'guide-negative';
  return 'guide-neutral';
}

// Render one metric pill using the industry-standard framing:
//   * vs prior guide  -> "Raise +X%, +Y% vs Street" (or Cut/Flat)
//   * vs consensus    -> "Above Street +Y%" / "In-line Street" / "Below Street -Y%"
// metricPrefix is '' for revenue and 'EPS '/'Op Inc '/'Op Profit '/'FCF '
// for the profitability pill. Returns '' when nothing meaningful renders.
function _renderGuidePill(metric, extraClass, metricPrefix) {
  if (!metric) return '';
  const pill = buildGuidancePillText(metric, metricPrefix);
  if (!pill) return '';
  const dirClass = _guideDirClass(pill.direction);
  const basisTitle = metric.priorSource === 'prior_guidance'
    ? ' title="raise/cut vs prior guide; vs Street shown when available"'
    : ' title="vs Street consensus"';
  return `<span class="guide-metric ${extraClass} ${dirClass}"${basisTitle}>` +
    `<span class="guide-metric-value">${pill.text}</span>` +
    `</span>`;
}

// Profit-pill prefix from the metric label (EPS / Op Inc / Op Profit / FCF).
function _profitMetricPrefix(label) {
  if (!label || label === 'REV') return '';
  return label + ' ';
}

// Render the split FY guidance row from a normalized { revenue, profitability }
// object. Returns '' when neither metric is present (caller hides the line).
function renderGuidanceChangeRow(g) {
  const { revenue, profitability } = buildGuidanceChangeDisplay(g);
  if (!revenue && !profitability) return '';
  const pills = [];
  const revPill = _renderGuidePill(revenue, 'guide-revenue', '');
  if (revPill) pills.push(revPill);
  const profPill = profitability
    ? _renderGuidePill(profitability, 'guide-profit', _profitMetricPrefix(profitability.label))
    : '';
  if (profPill) pills.push(profPill);
  if (!pills.length) return '';
  const body = pills.join('<span class="guide-sep">&middot;</span>');
  return `<div class="earnings-guide-row"><span class="guide-label">FY Guide</span>${body}</div>`;
}

// Parse a guidance numeric value that may be a bare number or a string with a
// $ prefix and/or B/M/K/T suffix (e.g. "$0.04", "48.0B", "1.13T"). Returns a
// float in raw units or null. Suffix scaling is consistent within a metric so
// it cancels in the ratio used for deltaPct.
function _parseGuideNum(v) {
  if (v == null) return null;
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v !== 'string') return null;
  const s = v.trim().replace(/[$,\s]/g, '');
  const m = /^(-?\d+(?:\.\d+)?)\s*([kKmMbBtT])?$/.exec(s);
  if (!m) return null;
  let n = parseFloat(m[1]);
  if (!Number.isFinite(n)) return null;
  const suf = (m[2] || '').toLowerCase();
  const scale = { k: 1e3, m: 1e6, b: 1e9, t: 1e12 }[suf] || 1;
  return n * scale;
}

// Compute { deltaPct, deltaAbs, priorSource } for one metric from new/prior
// midpoints, preferring a prior-consensus baseline over prior-guide. Returns
// null when the new midpoint or all baselines are missing. deltaPct is a
// fraction; null when the baseline is zero/near-zero (caller uses deltaAbs).
function _computeMetricDelta(newMid, consensusPrior, guidePrior, tinyBaseline) {
  const cur = _parseGuideNum(newMid);
  if (cur == null) return null;
  const base = selectGuidanceBaseline({
    consensusPriorMidpoint: _parseGuideNum(consensusPrior),
    priorGuideMidpoint: _parseGuideNum(guidePrior),
  });
  if (!base) return null;
  const deltaAbs = cur - base.baselineMidpoint;
  const tiny = tinyBaseline != null ? tinyBaseline : 0;
  let deltaPct = null;
  if (Math.abs(base.baselineMidpoint) > tiny) {
    deltaPct = deltaAbs / Math.abs(base.baselineMidpoint);
  }
  // When the primary baseline is the prior guide AND a prior Street consensus
  // also exists, also carry the vs-Street delta so the card can render
  // "Raise +X%, +Y% vs Street" without re-computing.
  let streetDeltaPct = null;
  let streetDeltaAbs = null;
  if (base.priorSource === 'prior_guidance') {
    const cons = _parseGuideNum(consensusPrior);
    if (cons != null) {
      streetDeltaAbs = cur - cons;
      if (Math.abs(cons) > tiny) streetDeltaPct = streetDeltaAbs / Math.abs(cons);
    }
  }
  return { deltaPct, deltaAbs, priorSource: base.priorSource, streetDeltaPct, streetDeltaAbs };
}

// Map a raw guide_vs_consensus envelope onto the normalized split-guidance
// fields the card renderer reads. Prefers precomputed deltas already on the
// row; only fills gaps. Percent envelope fields (e.g. -0.7) are converted to
// the fractional units the helpers expect (-0.007).
function _applyNormalizedGuidanceFromEnvelope(r, gvc) {
  if (!gvc) return;
  const setIf = (key, val) => {
    if (val != null && Number.isFinite(val) && r[key] == null) r[key] = val;
  };
  const pctToFrac = (p) => (typeof p === 'number' && Number.isFinite(p)) ? p / 100 : null;

  // --- Revenue ---
  // Prefer a midpoint-derived delta; else fall back to the precomputed pct.
  let rev = _computeMetricDelta(
    gvc.fy_rev_guide_midpoint_new,
    gvc.fy_rev_consensus_prior,
    gvc.fy_rev_guide_midpoint_prior,
    0,
  );
  if (rev && rev.deltaPct != null) {
    setIf('guidanceRevenueDeltaPct', rev.deltaPct);
    if (r.guidanceRevenuePriorSource == null) r.guidanceRevenuePriorSource = rev.priorSource;
    setIf('guidanceRevenueStreetDeltaPct', rev.streetDeltaPct);
  } else {
    const consPct = pctToFrac(gvc.fy_rev_change_vs_consensus_pct);
    const priorPct = pctToFrac(gvc.fy_rev_change_vs_prior_pct);
    if (consPct != null) {
      setIf('guidanceRevenueDeltaPct', consPct);
      if (r.guidanceRevenuePriorSource == null) r.guidanceRevenuePriorSource = 'consensus';
    } else if (priorPct != null) {
      setIf('guidanceRevenueDeltaPct', priorPct);
      if (r.guidanceRevenuePriorSource == null) r.guidanceRevenuePriorSource = 'prior_guidance';
    }
  }

  // --- EPS (primary profitability metric) ---
  // EPS baselines are small; treat |baseline| <= 0.05 as "tiny" so we surface
  // an absolute $ change instead of a misleading percentage.
  const eps = _computeMetricDelta(
    gvc.fy_eps_guide_midpoint_new,
    gvc.fy_eps_consensus_prior,
    gvc.fy_eps_guide_midpoint_prior,
    0.05,
  );
  if (eps) {
    setIf('guidanceEpsDeltaPct', eps.deltaPct);
    setIf('guidanceEpsDeltaAbs', eps.deltaAbs);
    if (r.guidanceEpsPriorSource == null) r.guidanceEpsPriorSource = eps.priorSource;
    setIf('guidanceEpsStreetDeltaPct', eps.streetDeltaPct);
    setIf('guidanceEpsStreetDeltaAbs', eps.streetDeltaAbs);
  } else {
    const epsPct = pctToFrac(gvc.fy_eps_guide_change_vs_consensus_pct);
    const epsPriorPct = pctToFrac(gvc.fy_eps_guide_change_vs_prior_pct);
    if (epsPct != null) {
      setIf('guidanceEpsDeltaPct', epsPct);
      if (r.guidanceEpsPriorSource == null) r.guidanceEpsPriorSource = 'consensus';
    } else if (epsPriorPct != null) {
      setIf('guidanceEpsDeltaPct', epsPriorPct);
      if (r.guidanceEpsPriorSource == null) r.guidanceEpsPriorSource = 'prior_guidance';
    }
  }

  // --- Operating profit / income (fallback profitability) ---
  const op = _computeMetricDelta(
    gvc.fy_op_profit_guide_midpoint_new,
    gvc.fy_op_profit_consensus_prior,
    gvc.fy_op_profit_guide_midpoint_prior,
    0,
  );
  if (op) {
    setIf('guidanceOperatingProfitDeltaPct', op.deltaPct);
    setIf('guidanceOperatingProfitDeltaAbs', op.deltaAbs);
    if (r.guidanceOperatingProfitPriorSource == null) r.guidanceOperatingProfitPriorSource = op.priorSource;
    setIf('guidanceOperatingProfitStreetDeltaPct', op.streetDeltaPct);
    setIf('guidanceOperatingProfitStreetDeltaAbs', op.streetDeltaAbs);
    if (r.guidanceProfitMetricUsed == null && gvc.fy_op_metric_kind) {
      r.guidanceProfitMetricUsed = gvc.fy_op_metric_kind;
    }
  }

  // --- FCF (last-resort profitability) ---
  const fcf = _computeMetricDelta(
    gvc.fy_fcf_guide_midpoint_new,
    gvc.fy_fcf_consensus_prior,
    gvc.fy_fcf_guide_midpoint_prior,
    0,
  );
  if (fcf) {
    setIf('guidanceFcfDeltaPct', fcf.deltaPct);
    setIf('guidanceFcfDeltaAbs', fcf.deltaAbs);
    if (r.guidanceFcfPriorSource == null) r.guidanceFcfPriorSource = fcf.priorSource;
    setIf('guidanceFcfStreetDeltaPct', fcf.streetDeltaPct);
    setIf('guidanceFcfStreetDeltaAbs', fcf.streetDeltaAbs);
  }
}

// --- Render earnings cards ---
function renderRecentEarnings(recent) {
  const hasCoverage = typeof tickerList !== 'undefined' && Array.isArray(tickerList) && tickerList.length > 0;
  const filtered = hasCoverage
    ? (recent || []).filter(r => tickerList.indexOf(r.ticker) !== -1)
    : (recent || []);
  if (!filtered || filtered.length === 0) {
    const scope = hasCoverage ? ' in your coverage' : '';
    $recentCards.innerHTML = `<div class="earnings-empty">No recent earnings in the last 14 days${scope}</div>`;
    return;
  }
  $recentCards.innerHTML = filtered.map(r => {
    const name = (COMMON_NAMES && COMMON_NAMES[r.ticker]) || r.name || r.ticker;
    // Compose every dynamic token through the safe formatters so no card can
    // emit "$$" / NaN / undefined / a bare "—" where a value is expected. Each
    // value is sourced FRESH from the data-layer row (r.*) — none is hardcoded.
    // renderEarningsCard also gates the surprise % on its matching actual being
    // present (NO CONTRADICTORY STATES) — see Issue D.
    const tokens = renderEarningsCard(r);
    const revSurprise = tokens.revSurprisePct;
    const epsSurprise = tokens.epsSurprisePct;
    // Split FY guidance into revenue + profitability pills from the normalized
    // backend fields. Falls back to the legacy single "FY Guide Δ" line only
    // when no normalized guidance data is present on the row.
    const guideRowHtml = renderGuidanceChangeRow(r);
    // Legacy single-line fallback: FY revenue guidance move vs prior consensus.
    const fyGuideDelta = (typeof r.fy_rev_guide_change_vs_consensus_pct === 'number')
      ? r.fy_rev_guide_change_vs_consensus_pct
      : (typeof r.fy_rev_change_vs_consensus_pct === 'number' ? r.fy_rev_change_vs_consensus_pct : null);
    // Timing + wall-clock aware label. earningsCardLabel (utils.js) keeps an
    // AMC reporter future-tense ("Reports today · AMC") until 4:05 PM ET and a
    // BMO reporter until 9:30 AM ET, then flips to "Reported today · …". It
    // reads record.report_date, so map the card's earnings_date onto it.
    const _labelRecord = { report_date: r.earnings_date, timing: r.timing, note_status: r.note_status };
    const cardLabel = (typeof earningsCardLabel === 'function')
      ? earningsCardLabel(_labelRecord)
      : (r.days_since === 0 ? 'Reported today' : r.days_since === 1 ? 'Reported yesterday' : `${r.days_since}d ago`);
    // Secondary note-status line (replaces the old "Awaiting post-earnings note"
    // placeholder): print_reaction / call_reaction / stale / pre_earnings.
    const noteStatusLabel = (typeof earningsNoteStatusLabel === 'function')
      ? earningsNoteStatusLabel(_labelRecord)
      : '';

    const maPill = (window.MaStatus && window.MaStatus.pillHtml) ? window.MaStatus.pillHtml(r.ticker, { compact: true }) : '';
    // Provenance tooltips (Guardrail F): hover a metric to see its data source.
    const revTitle = _escAttr(_sourceTitle(tokens.revenue, r.revenue_source));
    const epsTitle = _escAttr(_sourceTitle(tokens.eps, r.eps_source));
    const qBadge = (typeof quarantineBadgeHtml === 'function') ? quarantineBadgeHtml(r.ticker) : '';
    return `<div class="earnings-card reported" data-ticker="${r.ticker}" data-date="${r.earnings_date}" data-type="post">
      <div class="earnings-card-header">
        <span class="earnings-card-ticker">${r.ticker}${maPill}${qBadge}</span>
        <span class="earnings-card-name">${name}</span>
        <span class="earnings-card-date">${r.earnings_date} · ${cardLabel}</span>
      </div>
      <div class="earnings-card-metrics">
        <div class="earnings-metric">
          <span class="metric-label">Rev</span>
          <span class="metric-value" title="${revTitle}">${tokens.revenue}</span>
          ${_fmtBeatMiss(revSurprise)}
        </div>
        <div class="earnings-metric">
          <span class="metric-label">EPS</span>
          <span class="metric-value" title="${epsTitle}">${tokens.eps}</span>
          ${_fmtBeatMiss(epsSurprise)}
        </div>
      </div>
      ${guideRowHtml || _renderFyGuideDeltaRow(fyGuideDelta)}
      ${_renderGuideRow('Next Q Guide', r.next_q_rev_guide_vs_consensus_pct, r.next_q_eps_guide_vs_consensus_pct)}
      ${r.fiscal_quarter ? `<div class="earnings-card-fq">${r.fiscal_quarter}</div>` : ''}
      <div class="earnings-card-reaction-row">
        <span class="reaction-pct ${tokens.reaction.direction}">${tokens.reaction.display} post-print</span>
        ${r.stock_reaction
          ? `<span class="reaction-text">${r.stock_reaction}</span>`
          : (noteStatusLabel ? `<span class="reaction-text muted note-status-${(r.note_status || 'pre_earnings')}">${noteStatusLabel}</span>` : (!tokens.reaction.available ? `<span class="reaction-text muted">Awaiting post-earnings note</span>` : ''))}
      </div>
      <div class="earnings-card-footer">
        <span class="earnings-card-inflection-slot" data-ticker="${r.ticker}"></span>
        <button class="earnings-intel-btn" title="Open Earnings Intel">Open Earnings Intel →</button>
      </div>
    </div>`;
  }).join('');

  // Attach click handlers
  $recentCards.querySelectorAll('.earnings-card').forEach(card => {
    const intelBtn = card.querySelector('.earnings-intel-btn');
    if (intelBtn) {
      intelBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (typeof openPopup === 'function') openPopup(card.dataset.ticker, { initialTab: 'earnings-intel' });
      });
    }
  });

  // Hydrate inflection slots from earnings_intel.json
  hydrateEarningsCardInflections();
}

// Build an urgency badge comparing implied move (from options) to historical
// average absolute earnings move. Returns HTML or empty string.
function _impliedVsHistoricalBadge(ticker) {
  const intel = (window.earningsIntelData && window.earningsIntelData.tickers && window.earningsIntelData.tickers[ticker]) || null;
  if (!intel) return '';
  const implied = intel.implied_move_pct;
  const hist = intel.historical_avg_abs_move_pct;
  if (typeof implied !== 'number' && typeof hist !== 'number') return '';
  if (typeof implied === 'number' && typeof hist === 'number') {
    const ratio = implied / Math.max(hist, 0.1);
    let tone = 'flat';
    let label;
    if (ratio > 1.25) { tone = 'hot'; label = `Implied ${implied.toFixed(1)}% > hist ${hist.toFixed(1)}%`; }
    else if (ratio < 0.8) { tone = 'cold'; label = `Implied ${implied.toFixed(1)}% < hist ${hist.toFixed(1)}%`; }
    else { label = `Implied ${implied.toFixed(1)}% ≈ hist ${hist.toFixed(1)}%`; }
    return `<span class="earnings-urgency-badge urg-${tone}" title="Options-implied vs historical avg">${label}</span>`;
  }
  if (typeof implied === 'number') return `<span class="earnings-urgency-badge urg-flat">Implied ${implied.toFixed(1)}%</span>`;
  return `<span class="earnings-urgency-badge urg-flat">Hist ${hist.toFixed(1)}%</span>`;
}

function renderUpcomingEarnings(upcoming) {
  // Soft-filter to the user's coverage list when present. When tickerList is
  // empty we show the full market; when the user has built out a watchlist
  // they only want to see cards for names they cover.
  const hasCoverage = typeof tickerList !== 'undefined' && Array.isArray(tickerList) && tickerList.length > 0;
  const inCoverage = (t) => !hasCoverage || tickerList.indexOf(t) !== -1;
  const soon = (upcoming || []).filter(u => u.days_until <= 14 && inCoverage(u.ticker));
  const nextUp = (upcoming || []).filter(u => u.days_until > 14 && u.days_until <= 45 && inCoverage(u.ticker));

  if (soon.length === 0 && nextUp.length === 0) {
    const scope = hasCoverage ? ' in your coverage' : '';
    $upcomingCards.innerHTML = `<div class="earnings-empty">No upcoming earnings in the next 14 days${scope}</div>`;
    return;
  }

  let html = '';

  if (soon.length > 0) {
    html += soon.map(u => {
      const name = (COMMON_NAMES && COMMON_NAMES[u.ticker]) || u.name || u.ticker;
      const isWatch = (typeof tickerList !== 'undefined' && Array.isArray(tickerList) && tickerList.indexOf(u.ticker) !== -1);
      const starHtml = isWatch ? '<span class="earnings-watch-star" title="In your watchlist">★</span>' : '';
      const urg = _impliedVsHistoricalBadge(u.ticker);

      // Human-friendly header pill (right of company name):
      //   0 → "today", 1 → "tomorrow", n → "in nd"
      let headerDays;
      if (u.days_until === 0) headerDays = 'today';
      else if (u.days_until === 1) headerDays = 'tomorrow';
      else headerDays = `in ${u.days_until}d`;

      // Countdown line:
      //   0 → "Reports today· BMO/AMC", 1 → "Reports tomorrow", n → "Reports in n days"
      const timingTag = (u.timing && u.timing !== 'TBD') ? ` · ${u.timing}` : '';
      let countdown;
      if (u.days_until === 0) countdown = `Reports <strong>today</strong>${timingTag}`;
      else if (u.days_until === 1) countdown = `Reports <strong>tomorrow</strong>${timingTag}`;
      else countdown = `Reports in <strong>${u.days_until}</strong> days${timingTag}`;

      return `<div class="earnings-card upcoming${isWatch ? ' is-watchlist' : ''}" data-ticker="${u.ticker}" data-date="${u.earnings_date}" data-type="pre">
        <div class="earnings-card-header">
          <span class="earnings-card-ticker">${starHtml}${u.ticker}${(window.MaStatus && window.MaStatus.pillHtml) ? window.MaStatus.pillHtml(u.ticker, { compact: true }) : ''}</span>
          <span class="earnings-card-name">${name}</span>
          <span class="earnings-card-date">${u.earnings_date} · ${headerDays}</span>
          ${urg}
        </div>
        <div class="earnings-card-countdown">${countdown}</div>
        <div class="earnings-card-footer">
          <span class="earnings-card-inflection-slot" data-ticker="${u.ticker}"></span>
          <button class="earnings-intel-btn" title="Open Earnings Intel">Open Earnings Intel →</button>
        </div>
      </div>`;
    }).join('');
  }

  if (nextUp.length > 0) {
    html += `<div class="earnings-next-up">
      <h4 class="earnings-next-up-title">Next Up (15–45 days)</h4>
      <div class="earnings-next-up-list">
        ${nextUp.map(u => {
          const name = (COMMON_NAMES && COMMON_NAMES[u.ticker]) || u.name || u.ticker;
          // Match the main card phrasing for days_until=0/1.
          let niuDays;
          if (u.days_until === 0) niuDays = 'today';
          else if (u.days_until === 1) niuDays = 'tomorrow';
          else niuDays = `${u.days_until}d`;
          return `<div class="earnings-next-up-item">
            <span class="nui-ticker">${u.ticker}</span>
            <span class="nui-name">${name}</span>
            <span class="nui-date">${u.earnings_date}</span>
            <span class="nui-days">${niuDays}</span>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }

  $upcomingCards.innerHTML = html;

  // Attach Earnings Intel handlers for soon cards
  $upcomingCards.querySelectorAll('.earnings-card').forEach(card => {
    const btn = card.querySelector('.earnings-intel-btn');
    if (btn) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (typeof openPopup === 'function') openPopup(card.dataset.ticker, { initialTab: 'earnings-intel' });
      });
    }
  });

  // Hydrate inflection + signal summary slots
  hydrateEarningsCardInflections();
}

// Hydrate inflection badges and signal summaries on earnings cards from earnings_intel.json
async function hydrateEarningsCardInflections() {
  if (typeof loadEarningsIntel !== 'function') return;
  try {
    const intelData = await loadEarningsIntel();
    if (!intelData || !intelData.tickers) return;
    document.querySelectorAll('.earnings-card-inflection-slot').forEach(slot => {
      const ticker = slot.dataset.ticker;
      const intel = intelData.tickers[ticker];
      if (!intel) return;
      const badgeMap = {
        PRE: { label: 'PRE', cls: 'inflection-pre' },
        MID: { label: 'MID', cls: 'inflection-mid' },
        POST: { label: 'POST', cls: 'inflection-post' },
        NONE: { label: '—', cls: 'inflection-none' },
      };
      const inf = badgeMap[intel.inflection_status] || badgeMap.NONE;
      const summary = (typeof signalSummary === 'function') ? signalSummary(intel.signal_scorecard) : '';
      const updated = intel.intel_updated_at ? 'Updated' : '';
      slot.innerHTML = `
        <span class="ei-inflection ${inf.cls}">${inf.label}</span>
        ${summary ? `<span class="earnings-card-signals">${summary}</span>` : ''}
        ${updated ? `<span class="earnings-card-updated">${updated}</span>` : ''}
      `;
    });
  } catch (e) { /* ignore */ }
}

// --- Open earnings note in modal ---
// Now loads from static .md files first, falls back to backend
async function openEarningsNote(ticker, date, type) {
  $earningsNoteContent.innerHTML = '<div class="earnings-note-loading">Loading note...</div>';
  $earningsNoteOverlay.classList.add('active');
  document.body.style.overflow = 'hidden';

  // M&A short-circuit: if this ticker has a verified deal blurb (closed/pending/
  // announced), show the deal blurb panel instead of the earnings note. Earnings
  // notes are not generated for ACQ-status names.
  try {
    if (window.MaStatus) {
      if (typeof window.MaStatus.load === 'function') {
        await window.MaStatus.load();
      }
      if (window.MaStatus.hasBlurb && window.MaStatus.hasBlurb(ticker)) {
        $earningsNoteContent.innerHTML = window.MaStatus.dealBlurbHtml(ticker);
        return;
      }
    }
  } catch (e) { /* fall through to normal note flow */ }

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
      allNotes.push({
        ...n,
        earnings_date: n.date || n.earnings_date,
        type: 'pre',
        status: 'active',
        name: n.company || n.name || n.ticker,
        path: n.file || n.note_file || n.note_path
      });
    });
    (index.active_post_earnings || []).forEach(n => {
      allNotes.push({
        ...n,
        earnings_date: n.date || n.earnings_date,
        type: 'post',
        status: 'active',
        name: n.company || n.name || n.ticker,
        path: n.note_file || n.note_path
      });
    });
    if (index.archive) {
      const archiveStrings = [
        ...(index.archive.pre_earnings || []).map(s => ({ s, type: 'pre' })),
        ...(index.archive.post_earnings || []).map(s => ({ s, type: 'post' }))
      ];
      archiveStrings.forEach(({ s, type }) => {
        const parts = s.split('_');
        const date = parts[parts.length - 1]; // last segment is YYYY-MM-DD
        const ticker = parts.slice(0, -1).join('_');
        const prefix = type === 'post' ? 'notes/post_earnings' : 'notes/pre_earnings';
        allNotes.push({
          ticker,
          earnings_date: date,
          type,
          status: 'archived',
          name: ticker,
          path: `${prefix}/${s}.md`
        });
      });
    }
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
        window.earningsData = earningsData;
        if (typeof updateCoverageSummaryTiles === 'function') updateCoverageSummaryTiles();
        await enrichRecentFromIntel(data.recent || []);
        renderRecentEarnings(data.recent || []);
        renderUpcomingEarnings(data.upcoming || []);
        const hasCoverage = typeof tickerList !== 'undefined' && Array.isArray(tickerList) && tickerList.length > 0;
        const inCov = (t) => !hasCoverage || tickerList.indexOf(t) !== -1;
        const total = (data.recent || []).filter(r => inCov(r.ticker)).length +
          (data.upcoming || []).filter(u => u.days_until <= 14 && inCov(u.ticker)).length;
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
    let calResp;
    if (window.SignalSnapshot && window.SignalSnapshot.fetchWithFallback) {
      calResp = await window.SignalSnapshot.fetchWithFallback('earnings_calendar.json', { cacheBust: true });
    } else {
      calResp = await fetch('earnings_calendar.json?v=' + Date.now());
    }
    if (!calResp.ok) throw new Error('HTTP ' + calResp.status);
    const calData = await calResp.json();

    const now = new Date();
    // Calendar-day "today" (local tz) — used for day-diff math so that a
    // report dated 2026-04-23 resolves to days_until=0 throughout the whole
    // day, not -1 the moment local time moves past midnight.
    const todayLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const dayDiffFrom = (isoDate) => Math.floor(
      (new Date(isoDate + 'T00:00:00') - todayLocal) / 86400000
    );
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
      // Use calendar-day math so a note dated today shows days_since=0 (not 1 or -1).
      const daysSince = -dayDiffFrom(n.earnings_date);
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
          fiscal_quarter: n.fiscal_quarter || '',
          timing: n.timing || 'TBD'
        });
      }
    });

    // Also pull recent from calendar post_earnings if not already added
    if (calData && calData.post_earnings) {
      calData.post_earnings.forEach(p => {
        if (!recent.some(r => r.ticker === p.ticker && r.earnings_date === (p.date || p.earnings_date))) {
          const date = p.date || p.earnings_date;
          const daysSince = -dayDiffFrom(date);
          if (daysSince >= 0 && daysSince <= 14) {
            // Guard the surprise % so an undefined/non-finite calendar field
            // can never produce "NaN%" in the beat/miss label.
            const surprise = (typeof p.surprise_pct === 'number' && Number.isFinite(p.surprise_pct)) ? p.surprise_pct : null;
            const epsBeat = surprise == null ? '' : surprise > 0 ? `Beat +${surprise.toFixed(1)}%` : surprise < 0 ? `Miss ${surprise.toFixed(1)}%` : '';
            // safeMoney strips any pre-existing "$" before prepending, so a
            // calendar value that already carries "$" cannot become "$$".
            const revActual = p.revenue_actual ? safeMoney(p.revenue_actual) : '';
            const revBeat = p.revenue_actual && p.revenue_estimate ?
              (parseFloat(String(p.revenue_actual).replace(/[^0-9.]/g,'')) > parseFloat(String(p.revenue_estimate).replace(/[^0-9.]/g,'')) ? 'Beat' : 'Miss') : '';
            recent.push({
              ticker: p.ticker,
              name: p.name || p.company,
              earnings_date: date,
              days_since: daysSince,
              revenue_actual: revActual || (p.revenue_actual || ''),
              eps_actual: p.eps_actual != null ? safeMoney(p.eps_actual) : '',
              revenue_beat_miss: p.revenue_beat_miss || revBeat,
              eps_beat_miss: p.eps_beat_miss || epsBeat,
              stock_reaction: p.note || '',
              fiscal_quarter: p.quarter || '',
              timing: p.timing || 'TBD',
              fy_rev_guide_change_vs_consensus_pct: p.fy_rev_guide_change_vs_consensus_pct || null,
              fy_eps_guide_change_vs_consensus_pct: p.fy_eps_guide_change_vs_consensus_pct || null,
              next_q_rev_guide_vs_consensus_pct: p.next_q_rev_guide_vs_consensus_pct || null,
              next_q_eps_guide_vs_consensus_pct: p.next_q_eps_guide_vs_consensus_pct || null,
              // Normalized split-guidance fields (revenue + profitability).
              guidanceRevenueDeltaPct: p.guidanceRevenueDeltaPct != null ? p.guidanceRevenueDeltaPct : null,
              guidanceRevenuePriorSource: p.guidanceRevenuePriorSource || null,
              guidanceRevenueStreetDeltaPct: p.guidanceRevenueStreetDeltaPct != null ? p.guidanceRevenueStreetDeltaPct : null,
              guidanceEpsDeltaPct: p.guidanceEpsDeltaPct != null ? p.guidanceEpsDeltaPct : null,
              guidanceEpsDeltaAbs: p.guidanceEpsDeltaAbs != null ? p.guidanceEpsDeltaAbs : null,
              guidanceEpsPriorSource: p.guidanceEpsPriorSource || null,
              guidanceEpsStreetDeltaPct: p.guidanceEpsStreetDeltaPct != null ? p.guidanceEpsStreetDeltaPct : null,
              guidanceEpsStreetDeltaAbs: p.guidanceEpsStreetDeltaAbs != null ? p.guidanceEpsStreetDeltaAbs : null,
              guidanceOperatingProfitDeltaPct: p.guidanceOperatingProfitDeltaPct != null ? p.guidanceOperatingProfitDeltaPct : null,
              guidanceOperatingProfitDeltaAbs: p.guidanceOperatingProfitDeltaAbs != null ? p.guidanceOperatingProfitDeltaAbs : null,
              guidanceOperatingProfitPriorSource: p.guidanceOperatingProfitPriorSource || null,
              guidanceOperatingProfitStreetDeltaPct: p.guidanceOperatingProfitStreetDeltaPct != null ? p.guidanceOperatingProfitStreetDeltaPct : null,
              guidanceOperatingProfitStreetDeltaAbs: p.guidanceOperatingProfitStreetDeltaAbs != null ? p.guidanceOperatingProfitStreetDeltaAbs : null,
              guidanceFcfDeltaPct: p.guidanceFcfDeltaPct != null ? p.guidanceFcfDeltaPct : null,
              guidanceFcfDeltaAbs: p.guidanceFcfDeltaAbs != null ? p.guidanceFcfDeltaAbs : null,
              guidanceFcfPriorSource: p.guidanceFcfPriorSource || null,
              guidanceFcfStreetDeltaPct: p.guidanceFcfStreetDeltaPct != null ? p.guidanceFcfStreetDeltaPct : null,
              guidanceFcfStreetDeltaAbs: p.guidanceFcfStreetDeltaAbs != null ? p.guidanceFcfStreetDeltaAbs : null,
              guidanceProfitMetricUsed: p.guidanceProfitMetricUsed || null
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
        const daysUntil = dayDiffFrom(date);
        if (daysUntil >= 0) {
          upcoming.push({
            ticker: u.ticker,
            name: u.name || u.company,
            earnings_date: date,
            days_until: daysUntil,
            timing: u.timing || 'TBD',
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
        const daysUntil = dayDiffFrom(date);
        if (daysUntil >= 0) {
          upcoming.push({
            ticker: u.ticker,
            name: u.name || u.company,
            earnings_date: date,
            days_until: daysUntil,
            timing: u.timing || 'TBD',
            status: u.status || 'upcoming'
          });
        }
      });
    }

    earningsData = { recent, upcoming };
    window.earningsData = earningsData;
    if (typeof updateCoverageSummaryTiles === 'function') updateCoverageSummaryTiles();
    await enrichRecentFromIntel(recent);
    renderRecentEarnings(recent);
    renderUpcomingEarnings(upcoming);
    const hasCoverage = typeof tickerList !== 'undefined' && Array.isArray(tickerList) && tickerList.length > 0;
    const inCov = (t) => !hasCoverage || tickerList.indexOf(t) !== -1;
    const total = recent.filter(r => inCov(r.ticker)).length +
      upcoming.filter(u => u.days_until <= 14 && inCov(u.ticker)).length;
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
    let resp;
    if (window.SignalSnapshot && window.SignalSnapshot.fetchWithFallback) {
      resp = await window.SignalSnapshot.fetchWithFallback('earnings_calendar.json', { cacheBust: true });
    } else {
      resp = await fetch('earnings_calendar.json?v=' + Date.now());
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    ecalData = await resp.json();
  } catch (e) {
    // fetchWithFallback marks failure internally when both primary + fallback fail.
    console.warn('Calendar data load failed:', e);
    ecalData = { upcoming: [] };
  }
  // Expose to other modules (app.js daysToEarnings, etc.) without wiring a
  // dedicated event. This is the only module that owns ecalData today.
  window._earningsCalendarData = ecalData;
  // Re-run the Coverage flag filter so the Earnings 7D chip reflects newly
  // loaded data without requiring a user click.
  if (typeof window.applyFlagFilters === 'function') {
    try { window.applyFlagFilters(); } catch (_) {}
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
      if (typeof openPopup === 'function') {
        openPopup(chip.dataset.ticker, { initialTab: 'earnings-intel' });
      } else {
        openEarningsNote(chip.dataset.ticker, chip.dataset.date, chip.dataset.type);
      }
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
