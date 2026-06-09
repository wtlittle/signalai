/* news.js — SignalAI News surface module (lean redesign).
 *
 * Three simple, honest sections:
 *   1. M&A Rumors        — ma_status.json pending_review[] (+ confirmed deals[])
 *   2. Watchlist News    — live headlines (Perplexity/Supabase + Yahoo + Google),
 *                          filtered to DEFAULT_TICKERS, max 30 days old, newest first
 *   3. Recent Earnings Notes — earnings_notes_index.json, opens existing note modal
 *
 * No filter pills, no scope toggles, no synthetic cards. Empty sections render
 * an honest empty state — news data is never fabricated.
 *
 * Public API: window.NewsModule = { init, render, refresh }
 * Back-compat: window.fetchNews -> NewsModule.refresh (shell.js loader).
 */
(function (global) {
  'use strict';

  // Drop any headline article older than this. Prevents the stale
  // "random 2025 article" from undated/misdated aggregator items.
  const MAX_HEADLINE_AGE_MS = 30 * 24 * 3600 * 1000;
  const MAX_HEADLINES = 30;
  const MAX_EARNINGS_NOTES = 18;

  // ---------- DOM lookups (resolved lazily; markup may load after script) ----------
  function $feed() { return document.getElementById('news-feed'); }
  function $ma() { return document.getElementById('news-ma-list'); }
  function $headlines() { return document.getElementById('news-headlines-list'); }
  function $notes() { return document.getElementById('news-notes-list'); }
  function $status() { return document.getElementById('news-status'); }

  // ---------- Helpers ----------
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[ch]);
  }

  function timeAgo(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '';
    const diff = Date.now() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 30) return `${days}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function fmtDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return String(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function watchlist() {
    const list = (typeof global.tickerList !== 'undefined' && Array.isArray(global.tickerList) && global.tickerList.length)
      ? global.tickerList
      : (Array.isArray(global.DEFAULT_TICKERS) ? global.DEFAULT_TICKERS : []);
    return new Set(list
      .map((t) => (typeof t === 'string' ? t : (t && t.ticker) || ''))
      .filter(Boolean)
      .map((t) => t.toUpperCase()));
  }

  function openTicker(ticker) {
    if (!ticker) return;
    if (typeof global.popupOpen === 'function') global.popupOpen(ticker);
    else if (typeof global.openPopup === 'function') global.openPopup(ticker);
  }

  function setStatus(text) {
    const s = $status();
    if (s) s.textContent = text;
  }

  // =====================================================================
  // SECTION 1 — M&A RUMORS
  // =====================================================================
  async function loadMa() {
    let data = null;
    try {
      const resp = await fetch('ma_status.json?ts=' + Date.now(), { cache: 'no-store' });
      if (resp.ok) data = await resp.json();
    } catch (e) {
      console.warn('[news] ma_status load failed:', e);
    }
    if (!data) return [];
    const out = [];

    (Array.isArray(data.pending_review) ? data.pending_review : []).forEach((e) => {
      const src = (e.sources && e.sources[0]) || {};
      out.push({
        ticker: String(e.ticker || '').toUpperCase(),
        company: e.company || '',
        headline: e.headline || (e.buyer ? `${e.buyer} in talks to acquire ${e.company || e.ticker}` : `${e.ticker} M&A rumor`),
        buyer: e.buyer || '',
        dealType: e.deal_type || '',
        confidence: typeof e.confidence === 'number' ? e.confidence : null,
        sourceLabel: src.label || 'Rumor scan',
        sourceUrl: src.url || '',
        date: e.flagged_at || src.published_at || null,
        status: 'RUMOR',
      });
    });

    // Confirmed deals[] (object keyed by ticker) — show as ACQ/announced.
    const deals = data.deals && typeof data.deals === 'object' ? data.deals : {};
    Object.keys(deals).forEach((k) => {
      const d = deals[k] || {};
      const src = (d.sources && d.sources[0]) || {};
      const statusLabel = (d.status === 'closed' || d.status === 'pending' || d.status === 'announced') ? 'CONFIRMED' : 'DEAL';
      const headline = d.buyer
        ? `${d.buyer} ${d.status === 'closed' ? 'completed acquisition of' : 'to acquire'} ${d.company || d.ticker}`
        : `${d.company || d.ticker} deal`;
      out.push({
        ticker: String(d.ticker || k).toUpperCase(),
        company: d.company || '',
        headline,
        buyer: d.buyer || '',
        dealType: d.deal_type || '',
        confidence: null,
        sourceLabel: src.label || 'Filing',
        sourceUrl: src.url || '',
        date: d.announced_date || d.closed_date || null,
        status: statusLabel,
      });
    });

    out.sort((a, b) => (Date.parse(b.date || 0) || 0) - (Date.parse(a.date || 0) || 0));
    return out;
  }

  function maCardHtml(item) {
    const conf = item.confidence != null ? `<span class="news-ma-conf">${Math.round(item.confidence * 100)}% conf</span>` : '';
    const statusCls = item.status === 'RUMOR' ? 'news-ma-status-rumor' : 'news-ma-status-confirmed';
    const buyer = item.buyer
      ? `<span class="news-ma-buyer"><span class="news-ma-label">Buyer:</span> ${escapeHtml(item.buyer)}${item.dealType ? ` <span class="news-ma-sep">&middot;</span> ${escapeHtml(item.dealType)}` : ''}</span>`
      : '';
    const source = item.sourceUrl
      ? `<a class="news-source" href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noopener">${escapeHtml(item.sourceLabel)}</a>`
      : `<span class="news-source">${escapeHtml(item.sourceLabel)}</span>`;
    return `<article class="news-card news-ma-card">
      <div class="news-card-top">
        <span class="news-chip" data-ticker="${escapeHtml(item.ticker)}">${escapeHtml(item.ticker)}</span>
        <span class="news-ma-status ${statusCls}">${escapeHtml(item.status)}</span>
        ${conf}
        <span class="news-card-time">${escapeHtml(fmtDate(item.date))}</span>
      </div>
      <div class="news-card-headline">${escapeHtml(item.headline)}</div>
      ${buyer}
      <div class="news-card-foot">${source}</div>
    </article>`;
  }

  function renderMa(items) {
    const el = $ma();
    if (!el) return;
    if (!items.length) {
      el.innerHTML = '<div class="news-empty">No active rumors. The hourly scan will surface new ones here.</div>';
      return;
    }
    el.innerHTML = items.map(maCardHtml).join('');
    wireChips(el);
  }

  // =====================================================================
  // SECTION 2 — WATCHLIST NEWS HEADLINES
  // =====================================================================
  // Tags a headline with watchlist tickers it mentions. Uses explicit ticker
  // fields when present, otherwise a ticker-symbol scan on the title.
  function tagHeadline(raw, wl) {
    const found = new Set();
    const explicit = []
      .concat(Array.isArray(raw.tickers) ? raw.tickers : [])
      .concat(raw.ticker ? [raw.ticker] : []);
    explicit.forEach((t) => {
      const up = String(t || '').toUpperCase();
      if (up && wl.has(up)) found.add(up);
    });
    if (!found.size) {
      // Symbol scan: $TICK or standalone uppercase token that is in the watchlist.
      const title = String(raw.title || raw.headline || '');
      const tokens = title.match(/\$?[A-Z]{1,5}(?:\.[A-Z])?/g) || [];
      tokens.forEach((tok) => {
        const up = tok.replace(/^\$/, '').toUpperCase();
        if (wl.has(up)) found.add(up);
      });
    }
    return Array.from(found);
  }

  async function loadHeadlines() {
    const wl = watchlist();
    const tickers = Array.from(wl);
    let raw = [];
    try {
      if (typeof global.fetchNewsClient === 'function') {
        raw = await global.fetchNewsClient(tickers.slice(0, 20));
      }
    } catch (e) {
      console.warn('[news] headline fetch failed:', e);
      raw = [];
    }
    if (!Array.isArray(raw)) raw = [];

    const now = Date.now();
    const seen = new Set();
    const out = [];
    raw.forEach((r) => {
      const title = r.title || r.headline || '';
      if (!title) return;
      // Hard age gate: drop undated and anything older than 30 days.
      const ts = Date.parse(r.publishedAt || r.pubDate || r.published_at || '');
      if (!ts || isNaN(ts) || (now - ts) > MAX_HEADLINE_AGE_MS) return;
      const tags = tagHeadline(r, wl);
      if (!tags.length) return; // must touch a watchlist ticker
      const key = title.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().slice(0, 60);
      if (seen.has(key)) return;
      seen.add(key);
      out.push({
        title,
        url: r.url || r.link || '',
        source: r.source || r.publisher || 'News',
        date: new Date(ts).toISOString(),
        ts,
        tickers: tags,
      });
    });
    out.sort((a, b) => b.ts - a.ts);
    return out.slice(0, MAX_HEADLINES);
  }

  function headlineCardHtml(item) {
    const chips = item.tickers.map((t) =>
      `<span class="news-chip" data-ticker="${escapeHtml(t)}">${escapeHtml(t)}</span>`).join('');
    const title = item.url
      ? `<a class="news-card-headline news-card-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>`
      : `<span class="news-card-headline">${escapeHtml(item.title)}</span>`;
    return `<article class="news-card news-headline-card">
      <div class="news-card-top">
        <span class="news-chips">${chips}</span>
        <span class="news-card-time">${escapeHtml(timeAgo(item.date))}</span>
      </div>
      ${title}
      <div class="news-card-foot"><span class="news-source">${escapeHtml(item.source)}</span></div>
    </article>`;
  }

  function renderHeadlines(items) {
    const el = $headlines();
    if (!el) return;
    if (!items.length) {
      el.innerHTML = '<div class="news-empty">No fresh watchlist headlines today.</div>';
      return;
    }
    el.innerHTML = items.map(headlineCardHtml).join('');
    wireChips(el);
  }

  // =====================================================================
  // SECTION 3 — RECENT EARNINGS NOTES
  // =====================================================================
  async function loadNotes() {
    let idx = null;
    try {
      if (typeof global.loadEarningsNotesIndex === 'function') {
        idx = await global.loadEarningsNotesIndex();
      }
      if (!idx) {
        const resp = await fetch('earnings_notes_index.json?ts=' + Date.now(), { cache: 'no-store' });
        if (resp.ok) idx = await resp.json();
      }
    } catch (e) {
      console.warn('[news] earnings notes index load failed:', e);
    }
    if (!idx) return [];

    const out = [];
    (Array.isArray(idx.active_post_earnings) ? idx.active_post_earnings : []).forEach((n) => {
      out.push({
        ticker: String(n.ticker || '').toUpperCase(),
        company: n.company || '',
        date: n.date || n.earnings_date || '',
        type: 'post',
        path: n.note_file || n.note_path || n.file || '',
      });
    });
    (Array.isArray(idx.active_pre_earnings) ? idx.active_pre_earnings : []).forEach((n) => {
      out.push({
        ticker: String(n.ticker || '').toUpperCase(),
        company: n.company || '',
        date: n.date || n.earnings_date || '',
        type: 'pre',
        path: n.file || n.note_file || n.note_path || '',
      });
    });

    // Newest first by note date; cap at MAX_EARNINGS_NOTES.
    out.sort((a, b) => (Date.parse(b.date || 0) || 0) - (Date.parse(a.date || 0) || 0));
    // De-dupe by ticker+date+type.
    const seen = new Set();
    const deduped = [];
    for (const n of out) {
      const key = `${n.ticker}|${n.date}|${n.type}`;
      if (seen.has(key)) continue;
      seen.add(key);
      deduped.push(n);
      if (deduped.length >= MAX_EARNINGS_NOTES) break;
    }
    return deduped;
  }

  function noteCardHtml(item) {
    const typeLabel = item.type === 'pre' ? 'PRE-EARNINGS' : 'POST-EARNINGS';
    const typeCls = item.type === 'pre' ? 'news-note-type-pre' : 'news-note-type-post';
    const sub = item.company ? `<span class="news-note-company">${escapeHtml(item.company)}</span>` : '';
    return `<article class="news-card news-note-card" data-ticker="${escapeHtml(item.ticker)}" data-date="${escapeHtml(item.date)}" data-type="${escapeHtml(item.type)}" role="button" tabindex="0">
      <div class="news-card-top">
        <span class="news-chip">${escapeHtml(item.ticker)}</span>
        <span class="news-note-type ${typeCls}">${typeLabel}</span>
        <span class="news-card-time">${escapeHtml(fmtDate(item.date))}</span>
      </div>
      <div class="news-note-line">${sub}<span class="news-note-open">Open note &rarr;</span></div>
    </article>`;
  }

  function renderNotes(items) {
    const el = $notes();
    if (!el) return;
    if (!items.length) {
      el.innerHTML = '<div class="news-empty">No recent earnings notes.</div>';
      return;
    }
    el.innerHTML = items.map(noteCardHtml).join('');
    el.querySelectorAll('.news-note-card').forEach((card) => {
      const open = () => {
        const t = card.getAttribute('data-ticker');
        const d = card.getAttribute('data-date');
        const ty = card.getAttribute('data-type');
        if (typeof global.openEarningsNote === 'function') {
          global.openEarningsNote(t, d, ty);
        } else {
          openTicker(t);
        }
      };
      card.addEventListener('click', open);
      card.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
      });
    });
  }

  // ---------- Shared wiring ----------
  function wireChips(root) {
    root.querySelectorAll('.news-chip[data-ticker]').forEach((el) => {
      el.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        openTicker(el.getAttribute('data-ticker'));
      });
    });
  }

  // ---------- Lifecycle ----------
  async function loadAll() {
    setStatus('updating…');
    const [ma, headlines, notes] = await Promise.all([loadMa(), loadHeadlines(), loadNotes()]);
    renderMa(ma);
    renderHeadlines(headlines);
    renderNotes(notes);
    setStatus(`M&A ${ma.length} · Headlines ${headlines.length} · Notes ${notes.length}`);
  }

  async function init() {
    await loadAll();
    return true;
  }

  async function refresh() {
    await loadAll();
    return true;
  }

  // Public-API contract retains render(); a fresh load is the only paint path.
  global.NewsModule = { init, render: refresh, refresh };
  // Back-compat: shell.js MORE_PANE_CONFIG.news.loaderName === 'fetchNews'.
  global.fetchNews = refresh;
})(typeof window !== 'undefined' ? window : globalThis);
