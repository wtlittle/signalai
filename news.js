/* news.js — SignalAI News surface module.
 *
 * Owns all News surface logic: init, render, refresh. Replaces the passive
 * news dump that previously lived in app.js. The M&A rumor queue (formerly a
 * separate #ma-pending-section) is folded into this unified feed as M&A-signal
 * cards, reusing window.MaPending.load/approveEntry/rejectEntry.
 *
 * Each story is signal-tagged (earnings / guidance / M&A / analyst / macro /
 * general), coverage-scoped against window.tickerList, filterable by
 * scope / signal / time, and (for in-coverage stories) annotated with a
 * one-sentence "why it matters to my book" line via window.SignalAIApi.
 *
 * Public API: window.NewsModule = { init, render, refresh }
 * Back-compat: window.fetchNews -> NewsModule.refresh (shell.js loader).
 */
(function (global) {
  'use strict';

  // ---------- Module state ----------
  let stories = [];
  let filters = { scope: 'coverage', signal: 'all', time: '24h', premarket: false };
  let initialized = false;
  let relevanceRequested = false;
  let earningsRecent = [];        // earnings_calendar.json recent[] (reporters)
  let earningsRecentByTicker = {}; // ticker -> recent entry
  let aliasIndex = null;          // built lazily: ticker -> { tickerForm, names[] }

  // Hand-maintained company-name aliases for high-traffic names whose headline
  // form differs from the COMMON_NAMES/longName mapping. Pure name-stripping
  // misses these (e.g. "Nvidia" vs "NVIDIA Corporation", "Facebook" for META),
  // so they are listed explicitly. Keyed by ticker; values are headline forms.
  const MANUAL_ALIASES = {
    GOOG: ['Alphabet', 'Google'], GOOGL: ['Alphabet', 'Google'],
    META: ['Meta', 'Meta Platforms', 'Facebook'],
    'BRK.B': ['Berkshire', 'Berkshire Hathaway'], 'BRK.A': ['Berkshire', 'Berkshire Hathaway'],
    WMT: ['Walmart'],
    JPM: ['JPMorgan', 'JP Morgan'],
    DIS: ['Disney', 'Walt Disney'],
    X: ['United States Steel', 'US Steel'],
    PEP: ['Pepsi', 'PepsiCo'],
    KO: ['Coca-Cola', 'Coke'],
    HD: ['Home Depot'],
    LOW: ["Lowe's"],
    V: ['Visa'],
    MA: ['Mastercard'],
    BAC: ['Bank of America'],
    WFC: ['Wells Fargo'],
    C: ['Citigroup', 'Citibank'],
    PFE: ['Pfizer'],
    LLY: ['Eli Lilly'],
    MRK: ['Merck'],
    JNJ: ['Johnson & Johnson'],
    BABA: ['Alibaba'],
    TSM: ['Taiwan Semiconductor', 'TSMC'],
    ASML: ['ASML'],
    NVDA: ['Nvidia', 'NVIDIA'],
    AMD: ['AMD', 'Advanced Micro Devices'],
    MSFT: ['Microsoft'],
    AAPL: ['Apple'],
    TSLA: ['Tesla'],
    AMZN: ['Amazon'],
    NFLX: ['Netflix'],
    CRM: ['Salesforce'],
    ORCL: ['Oracle'],
    ADBE: ['Adobe'],
    INTU: ['Intuit'],
    NOW: ['ServiceNow'],
    PANW: ['Palo Alto Networks', 'Palo Alto'],
    ZS: ['Zscaler'],
    CRWD: ['CrowdStrike'],
    NET: ['Cloudflare'],
    SHOP: ['Shopify'],
    PYPL: ['PayPal'],
    SQ: ['Block', 'Square'], XYZ: ['Block', 'Square'],
    COIN: ['Coinbase'],
    HOOD: ['Robinhood'],
    PLTR: ['Palantir'],
    SNOW: ['Snowflake'],
    MDB: ['MongoDB'],
    DDOG: ['Datadog'],
    TEAM: ['Atlassian'],
    GTLB: ['GitLab'],
    DOCN: ['DigitalOcean'],
    HUBS: ['HubSpot'],
    WDAY: ['Workday'],
    VEEV: ['Veeva'],
  };


  // ---------- DOM lookups (resolved lazily; markup may load after script) ----------
  function $feed() { return document.getElementById('news-feed'); }
  function $status() { return document.getElementById('news-status'); }
  function $filterBar() { return document.getElementById('news-filter-bar'); }
  function $premarketBtn() { return document.getElementById('news-premarket-btn'); }

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
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  // djb2 hash for stable story IDs.
  function djb2(str) {
    let h = 5381;
    for (let i = 0; i < str.length; i++) {
      h = ((h << 5) + h) + str.charCodeAt(i);
      h = h & h; // keep 32-bit
    }
    return 'n' + (h >>> 0).toString(36);
  }

  function coverageList() {
    const list = (typeof global.tickerList !== 'undefined' && Array.isArray(global.tickerList))
      ? global.tickerList
      : (Array.isArray(global.DEFAULT_TICKERS) ? global.DEFAULT_TICKERS : []);
    // DEFAULT_TICKERS may be strings or {ticker} objects depending on build.
    return list.map((t) => (typeof t === 'string' ? t : (t && t.ticker) || '')).filter(Boolean);
  }

  function openTicker(ticker) {
    if (!ticker) return;
    if (typeof global.popupOpen === 'function') global.popupOpen(ticker);
    else if (typeof global.openPopup === 'function') global.openPopup(ticker);
  }

  // ---------- Signal classification ----------
  const SIGNAL_BADGES = {
    earnings:     { label: 'EARNINGS',    cls: 'badge-earnings' },
    guidance_up:  { label: 'GUIDANCE ↑', cls: 'badge-guidance-up' },
    guidance_dn:  { label: 'GUIDANCE ↓', cls: 'badge-guidance-dn' },
    ma:           { label: 'M&A RUMOR',   cls: 'badge-ma' },
    analyst:      { label: 'ANALYST',     cls: 'badge-analyst' },
    macro:        { label: 'MACRO',       cls: 'badge-macro' },
    general:      { label: 'NEWS',        cls: 'badge-general' },
  };

  // Coarse signal family used by the Signal filter pills (guidance_up/dn -> guidance).
  function signalFamily(signal) {
    if (signal === 'guidance_up' || signal === 'guidance_dn') return 'guidance';
    return signal;
  }

  function classifySignal(headline) {
    const h = (headline || '').toLowerCase();
    if (/raises guidance|raises fy|raises full[- ]year|lifts guidance|boosts guidance/.test(h)) return 'guidance_up';
    if (/cuts guidance|lowers fy|lowers guidance|slashes guidance|trims guidance|lowers full[- ]year/.test(h)) return 'guidance_dn';
    if (/\bbeats\b|\bmisses\b|\bq[1-4]\b|earnings report|quarterly results|reports earnings/.test(h)) return 'earnings';
    if (/upgrade|downgrade|price target|initiates coverage|reiterates|raised to|cut to|maintains buy|maintains sell/.test(h)) return 'analyst';
    if (/\bfed\b|\bcpi\b|\bppi\b|jobs report|nonfarm|\bfomc\b|rate cut|rate hike|inflation/.test(h)) return 'macro';
    return 'general';
  }

  // ---------- Ticker tagging + coverage scoping ----------
  function escapeRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  // Resolve the human-friendly company name for a ticker (defers to utils.js).
  function companyName(ticker) {
    if (typeof global.getCommonName === 'function') {
      const n = global.getCommonName(ticker, null);
      if (n && n !== ticker) return n;
    }
    return null;
  }

  // Build (once) a per-ticker alias index with PRE-COMPILED regexes. Without
  // this, matchTickers would recompile ~2 regexes per ticker for every story.
  // names come from: company name, suffix-stripped short name (via utils
  // cleanCompanyName), and MANUAL_ALIASES. Sub-4-char aliases are dropped from
  // name matching to avoid false positives (e.g. "Visa" tagging unrelated text);
  // those tickers only match the uppercase ticker form.
  function buildAliasIndex(coverage) {
    const idx = {};
    coverage.forEach((raw) => {
      const t = raw.toUpperCase();
      const names = new Set();
      const name = companyName(t);
      if (name) {
        names.add(name);
        const short = (typeof global.cleanCompanyName === 'function')
          ? global.cleanCompanyName(name) : name;
        if (short && short.toLowerCase() !== name.toLowerCase()) names.add(short);
      }
      (MANUAL_ALIASES[t] || []).forEach((a) => names.add(a));
      const reNames = Array.from(names)
        .map((n) => n.toLowerCase())
        .filter((n) => n.length >= 4)
        .map((n) => new RegExp('(^|[^a-z])' + escapeRe(n) + '([^a-z]|$)'));
      idx[t] = {
        reTicker: new RegExp('(^|[^A-Z0-9$])\\$?' + escapeRe(t) + '([^A-Z0-9]|$)'),
        reNames,
      };
    });
    return idx;
  }

  function aliasIndexFor(coverage) {
    if (!aliasIndex) aliasIndex = buildAliasIndex(coverage);
    return aliasIndex;
  }

  // Return the set of coverage tickers matched in the given text via ticker
  // form (uppercase, word-boundary) or company-name aliases (lowercase).
  function matchTickers(text, coverage) {
    const idx = aliasIndexFor(coverage);
    const upper = String(text || '').toUpperCase();
    const lower = String(text || '').toLowerCase();
    const found = new Set();
    Object.keys(idx).forEach((t) => {
      const entry = idx[t];
      if (entry.reTicker.test(upper)) { found.add(t); return; }
      for (const re of entry.reNames) {
        if (re.test(lower)) { found.add(t); return; }
      }
    });
    return found;
  }

  function tagStoryTickers(story, coverage) {
    const cov = coverage.map((t) => t.toUpperCase());
    const found = matchTickers(`${story.headline || ''} ${story.body || ''} ${story.ticker || ''}`, cov);
    // Always include any explicit ticker field first.
    if (story.ticker) found.add(String(story.ticker).toUpperCase());
    story.tickers = Array.from(found);
    story.inCoverage = story.tickers.length > 0;
    applyEarningsSignal(story, cov);
    return story;
  }

  // Force signal='earnings' if a matched ticker reported within +/- 3 days of
  // this story's date. Overrides the headline-keyword classifier.
  function applyEarningsSignal(story, cov) {
    if (!earningsRecent.length || !story.pubDate) return;
    const storyMs = Date.parse(story.pubDate);
    if (isNaN(storyMs)) return;
    for (const t of story.tickers) {
      const ent = earningsRecentByTicker[t];
      if (!ent || !ent.earnings_date) continue;
      const repMs = Date.parse(ent.earnings_date + 'T00:00:00Z');
      if (isNaN(repMs)) continue;
      if (Math.abs(storyMs - repMs) <= 3 * 24 * 3600 * 1000) {
        story.signal = 'earnings';
        story.isEarningsAuto = true;
        return;
      }
    }
  }

  // ---------- Normalization ----------
  function normalizeNewsItem(raw) {
    const headline = raw.title || raw.headline || '';
    const pubDate = raw.pubDate || raw.publishedAt || raw.published_at || null;
    const source = raw.source || raw.publisher || raw.source_name || '';
    const url = raw.url || raw.link || '';
    const id = djb2(`${source}|${headline}|${pubDate || ''}`);
    const signal = classifySignal(headline);
    return {
      id,
      headline,
      body: raw.body || raw.summary || '',
      source,
      url,
      ticker: raw.ticker || '',
      pubDate,
      signal,
      tickers: [],
      inCoverage: false,
      relevance: null,
      isMa: false,
      maEntry: null,
    };
  }

  function normalizeMaEntry(entry) {
    const headline = entry.headline || (entry.buyer && entry.target
      ? `${entry.buyer} in talks to acquire ${entry.target}`
      : `${entry.ticker} M&A rumor`);
    const pubDate = entry.flagged_at || entry.detected_at || null;
    const srcName = (entry.sources && entry.sources[0] && entry.sources[0].label)
      || entry.source_name || 'Rumor scan';
    const srcUrl = (entry.sources && entry.sources[0] && entry.sources[0].url)
      || entry.source_url || '';
    const tickerUp = String(entry.ticker || '').toUpperCase();
    const id = djb2(`ma|${tickerUp}|${headline}`);
    return {
      id,
      headline,
      // Fold buyer/target into the matchable body so the counterparty (e.g.
      // PTC in the Autodesk rumor) and a company-name buyer both get tagged.
      body: [entry.rationale, entry.buyer, entry.target].filter(Boolean).join(' '),
      source: srcName,
      url: srcUrl,
      ticker: tickerUp,
      pubDate,
      signal: 'ma',
      tickers: tickerUp ? [tickerUp] : [],
      inCoverage: true, // rumors are always within coverage interest
      relevance: null,
      isMa: true,
      maEntry: entry,
      peers: [],
    };
  }

  // Same-subsector peers of the M&A-tagged tickers (read-through chips).
  // Skips tagged tickers themselves; caps at 4. Returns [] if no subsector map.
  function readThroughPeers(taggedTickers, coverage) {
    if (typeof global.getSubsector !== 'function') return [];
    const tagged = new Set(taggedTickers);
    const subs = new Set();
    taggedTickers.forEach((t) => {
      const s = global.getSubsector(t);
      if (s && s !== 'Other') subs.add(s);
    });
    if (!subs.size) return [];
    const peers = [];
    for (const t of coverage) {
      const up = t.toUpperCase();
      if (tagged.has(up)) continue;
      if (subs.has(global.getSubsector(up))) {
        peers.push(up);
        if (peers.length >= 4) break;
      }
    }
    return peers;
  }

  // ---------- Data fetching ----------
  async function fetchNewsStories() {
    const cov = coverageList();
    const newsTickers = cov.slice(0, 20);
    let data = [];
    try {
      if (typeof global.checkBackend === 'function' && await global.checkBackend()
          && typeof global.BACKEND_URL !== 'undefined') {
        const url = `${global.BACKEND_URL}/news?symbols=${encodeURIComponent(newsTickers.join(','))}`;
        const resp = await fetch(url, { signal: AbortSignal.timeout(60000) });
        data = await resp.json();
      } else if (typeof global.fetchNewsClient === 'function') {
        data = await global.fetchNewsClient(newsTickers);
      }
    } catch (e) {
      console.warn('[news] story fetch failed:', e);
      data = [];
    }
    return Array.isArray(data) ? data : [];
  }

  async function fetchMaRumors() {
    try {
      if (global.MaPending && typeof global.MaPending.load === 'function') {
        const entries = await global.MaPending.load();
        return Array.isArray(entries) ? entries : [];
      }
    } catch (e) {
      console.warn('[news] M&A rumor load failed:', e);
    }
    return [];
  }

  // Load earnings_calendar.json once; cache recent[] reporters for signal
  // upgrade + synthetic post-print cards.
  async function loadEarningsCalendar() {
    try {
      let data = null;
      if (global.SignalSnapshot && global.SignalSnapshot.fetchWithFallback) {
        const resp = await global.SignalSnapshot.fetchWithFallback('earnings_calendar.json', { timeoutMs: 10000, cacheBust: true });
        if (resp && resp.ok) data = await resp.json();
      }
      if (!data) {
        const resp = await fetch('earnings_calendar.json?ts=' + Date.now(), { signal: AbortSignal.timeout(10000) });
        if (resp.ok) data = await resp.json();
      }
      earningsRecent = (data && Array.isArray(data.recent)) ? data.recent : [];
    } catch (e) {
      console.warn('[news] earnings_calendar load failed:', e);
      earningsRecent = [];
    }
    earningsRecentByTicker = {};
    earningsRecent.forEach((r) => {
      if (r && r.ticker) earningsRecentByTicker[String(r.ticker).toUpperCase()] = r;
    });
  }

  // change1d lookup from the cached snapshot quotes.
  async function change1dFor(ticker) {
    try {
      if (typeof loadSnapshot === 'function') {
        const snap = await loadSnapshot();
        const q = snap && snap.quotes && snap.quotes[ticker];
        if (q && typeof q.change1d === 'number') return q.change1d;
      }
    } catch (e) { /* ignore */ }
    if (global._snapshotData && global._snapshotData.quotes && global._snapshotData.quotes[ticker]) {
      const v = global._snapshotData.quotes[ticker].change1d;
      if (typeof v === 'number') return v;
    }
    return null;
  }

  // Synthesize "post-print move" cards for recent reporters that moved >= 5%.
  // Built synchronously from a pre-fetched change1d map (see init/refresh).
  let _change1dCache = {};
  function buildSyntheticEarningsCards(coverage, newsStories) {
    const cov = new Set(coverage.map((t) => t.toUpperCase()));
    const cards = [];
    // Days printed within last week, dated same day as a real earnings story.
    const realEarningsDays = new Set(
      newsStories
        .filter((s) => s.signal === 'earnings' && s.pubDate)
        .flatMap((s) => s.tickers.map((t) => `${t}|${(s.pubDate || '').slice(0, 10)}`))
    );
    earningsRecent.forEach((ent) => {
      const t = String(ent.ticker || '').toUpperCase();
      if (!t || !cov.has(t)) return;
      const ds = ent.days_since;
      if (typeof ds !== 'number' || ds < 0 || ds > 7) return;
      const chg = _change1dCache[t];
      if (typeof chg !== 'number' || Math.abs(chg) < 5) return;
      const dayKey = `${t}|${ent.earnings_date}`;
      if (realEarningsDays.has(dayKey)) return; // prefer the real story
      const name = companyName(t) || ent.name || t;
      const arrow = chg >= 0 ? '+' : '-';
      const pct = Math.abs(chg).toFixed(1);
      const headline = `${name} ${arrow}${pct}% post ${quarterLabel(ent.earnings_date)}`;
      const body = buildPrintBody(ent);
      const note = ent.note_file;
      // Earnings date at US market close (20:00 UTC ~= 16:00 ET).
      const pubDate = ent.earnings_date ? `${ent.earnings_date}T20:00:00Z` : null;
      cards.push({
        id: djb2(`synth|${t}|${ent.earnings_date}`),
        headline,
        body,
        source: 'SignalAI · Auto',
        url: note || '#',
        ticker: t,
        pubDate,
        signal: 'earnings',
        tickers: [t],
        inCoverage: true,
        relevance: null,
        isMa: false,
        maEntry: null,
        isEarningsAuto: true,
        isSynthetic: true,
        peers: [],
        signal_meta: { kind: 'post_print_move', change_1d: chg },
      });
    });
    return cards;
  }

  function quarterLabel(dateStr) {
    if (!dateStr) return 'print';
    const m = parseInt(String(dateStr).slice(5, 7), 10);
    if (!m) return 'print';
    const q = Math.floor((m - 1) / 3) + 1;
    return `Q${q} print`;
  }

  function buildPrintBody(ent) {
    const rev = ent.revenue_actual;
    const revS = ent.revenue_surprise_pct;
    const eps = ent.eps_actual;
    const epsS = ent.eps_surprise_pct;
    if (rev == null && eps == null) return '';
    const parts = [];
    if (rev != null) {
      parts.push(`Revenue ${rev}${revS != null ? ` (${revS > 0 ? '+' : ''}${revS}% vs Street)` : ''}.`);
    }
    if (eps != null) {
      parts.push(`EPS ${eps}${epsS != null ? ` (${epsS > 0 ? '+' : ''}${epsS}% vs Street)` : ''}.`);
    }
    return parts.join(' ');
  }

  async function loadStories() {
    const cov = coverageList();
    aliasIndex = null; // rebuild against current coverage
    // Pre-fetch change1d for recent reporters so synthetic-card build is sync.
    _change1dCache = {};
    await Promise.all(earningsRecent.map(async (ent) => {
      const t = String(ent.ticker || '').toUpperCase();
      if (t && typeof ent.days_since === 'number' && ent.days_since >= 0 && ent.days_since <= 7) {
        _change1dCache[t] = await change1dFor(t);
      }
    }));
    const [newsRaw, maRaw] = await Promise.all([fetchNewsStories(), fetchMaRumors()]);
    const newsStories = newsRaw.map(normalizeNewsItem).map((s) => tagStoryTickers(s, cov));
    const maStories = maRaw.map(normalizeMaEntry).map((s) => {
      tagStoryTickers(s, cov);
      // tagStoryTickers may have flipped signal->earnings; M&A always wins.
      s.signal = 'ma';
      s.isEarningsAuto = false;
      s.peers = readThroughPeers(s.tickers, cov);
      return s;
    });
    const synthetic = buildSyntheticEarningsCards(cov, newsStories);
    // M&A rumors first (highest-signal), then news by recency.
    newsStories.sort((a, b) => (Date.parse(b.pubDate || 0) || 0) - (Date.parse(a.pubDate || 0) || 0));
    stories = maStories.concat(synthetic, newsStories);
    relevanceRequested = false;
  }

  // ---------- Filtering ----------
  function timeCutoffMs(time) {
    switch (time) {
      case '24h': return 24 * 3600 * 1000;
      case '48h': return 48 * 3600 * 1000;
      case '1w':  return 7 * 24 * 3600 * 1000;
      default:    return 24 * 3600 * 1000;
    }
  }

  function visibleStories() {
    const now = Date.now();
    let out = stories.slice();

    if (filters.premarket) {
      const cutoff = now - 12 * 3600 * 1000;
      out = out.filter((s) => {
        const t = Date.parse(s.pubDate || 0);
        // keep undated items (e.g. fresh rumors without timestamps)
        return !t || t >= cutoff;
      });
      out.sort((a, b) => b.tickers.length - a.tickers.length);
      return out;
    }

    // Time filter. Synthetic post-print cards are exempt: they cover the last
    // print (up to 7 days back) and should stay visible inside the 24h window.
    const cutoff = now - timeCutoffMs(filters.time);
    out = out.filter((s) => {
      if (s.isSynthetic) return true;
      const t = Date.parse(s.pubDate || 0);
      return !t || t >= cutoff;
    });

    // Signal filter
    if (filters.signal !== 'all') {
      out = out.filter((s) => signalFamily(s.signal) === filters.signal);
    }

    // Scope filter
    if (filters.scope === 'coverage') {
      out = out.filter((s) => s.inCoverage);
    }
    return out;
  }

  // ---------- Card rendering ----------
  function cardHtml(story) {
    const badge = SIGNAL_BADGES[story.signal] || SIGNAL_BADGES.general;
    const tickerTags = story.tickers.map((t) =>
      `<span class="news-ticker-tag" data-ticker="${escapeHtml(t)}">${escapeHtml(t)}</span>`
    ).join('');
    const ago = escapeHtml(timeAgo(story.pubDate));
    const off = !story.inCoverage;

    const primaryTicker = story.tickers[0] || story.ticker || '';
    const drilldown = primaryTicker
      ? `<button class="news-card-drilldown" data-ticker="${escapeHtml(primaryTicker)}">Open ${escapeHtml(primaryTicker)} →</button>`
      : '';

    const autoCls = story.isSynthetic ? ' news-card-source-auto' : '';
    const hasUrl = story.url && story.url !== '#';
    const sourceLink = hasUrl
      ? `<a class="news-card-source${autoCls}" href="${escapeHtml(story.url)}" target="_blank" rel="noopener">${escapeHtml(story.source || 'Source')}</a>`
      : `<span class="news-card-source${autoCls}">${escapeHtml(story.source || '')}</span>`;

    const peerTags = (story.peers && story.peers.length)
      ? `<span class="news-readthrough"><span class="news-readthrough-label">Read-through:</span>${story.peers.map((t) =>
          `<span class="news-ticker-tag news-ticker-tag-peer" data-ticker="${escapeHtml(t)}">${escapeHtml(t)}</span>`).join('')}</span>`
      : '';

    const maActions = story.isMa
      ? `<div class="news-ma-actions">
           <button class="btn-sm news-ma-approve" data-id="${escapeHtml(story.id)}">✓ Flag as Rumor</button>
           <button class="btn-sm btn-ghost news-ma-reject" data-id="${escapeHtml(story.id)}">✕ Dismiss</button>
         </div>`
      : '';

    // Relevance block: only for in-coverage cards. Off-coverage never shows one.
    // Filled cards show the annotation; unfilled show a loading placeholder that
    // requestRelevance() will populate or strip.
    let relevanceBlock = '';
    if (!off) {
      relevanceBlock = story.relevance
        ? `<div class="news-card-relevance">${escapeHtml(story.relevance)}</div>`
        : `<div class="news-card-relevance news-relevance-loading">Analyzing relevance to your book…</div>`;
    }

    return `<div class="news-card${off ? ' news-card-offcoverage' : ''}" data-signal="${escapeHtml(story.signal)}" data-id="${escapeHtml(story.id)}" data-tickers="${escapeHtml(story.tickers.join(','))}">
      <div class="news-card-header">
        <span class="news-signal-badge ${badge.cls}">${badge.label}</span>
        <span class="news-ticker-tags">${tickerTags}</span>
        <span class="news-card-time">${ago}</span>
      </div>
      <div class="news-card-headline">${escapeHtml(story.headline)}</div>
      ${story.isSynthetic && story.body ? `<div class="news-card-body">${escapeHtml(story.body)}</div>` : ''}
      ${peerTags}
      ${relevanceBlock}
      <div class="news-card-footer">
        ${sourceLink}
        ${drilldown}
        ${maActions}
      </div>
    </div>`;
  }

  function render() {
    const feed = $feed();
    if (!feed) return;

    const list = visibleStories();
    feed.classList.toggle('premarket-mode', !!filters.premarket);

    if (!list.length) {
      const msg = filters.scope === 'coverage'
        ? 'No recent news for your coverage. Try All Market or a wider time window.'
        : 'No news available. News requires the backend or client fetch.';
      feed.innerHTML = `<div class="news-empty">${msg}</div>`;
      setStatus(`0 stories`);
      return;
    }

    let html = '';
    if (filters.premarket) {
      const date = new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
      html += `<div class="news-premarket-header">Pre-market Brief — ${escapeHtml(date)} — ${list.length} stories touching your coverage</div>`;
    }
    html += list.map(cardHtml).join('');
    feed.innerHTML = html;

    wireCardEvents(feed);
    setStatus(`${list.length} ${list.length === 1 ? 'story' : 'stories'}`);

    // Kick off the AI relevance pass for in-coverage cards (batched, once).
    requestRelevance();
  }

  function setStatus(text) {
    const s = $status();
    if (s) s.textContent = text;
  }

  // ---------- Card event wiring ----------
  function wireCardEvents(feed) {
    feed.querySelectorAll('.news-ticker-tag, .news-card-drilldown').forEach((el) => {
      el.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        openTicker(el.getAttribute('data-ticker'));
      });
    });

    feed.querySelectorAll('.news-ma-approve').forEach((btn) => {
      btn.addEventListener('click', () => handleMaAction(btn, 'approve'));
    });
    feed.querySelectorAll('.news-ma-reject').forEach((btn) => {
      btn.addEventListener('click', () => handleMaAction(btn, 'reject'));
    });
  }

  async function handleMaAction(btn, action) {
    const id = btn.getAttribute('data-id');
    const story = stories.find((s) => s.id === id);
    if (!story || !story.maEntry) return;
    const card = btn.closest('.news-card');
    btn.disabled = true;
    btn.textContent = action === 'approve' ? 'Flagging…' : 'Dismissing…';
    try {
      if (action === 'approve' && global.MaPending && global.MaPending.approveEntry) {
        await global.MaPending.approveEntry(story.maEntry);
      } else if (action === 'reject' && global.MaPending && global.MaPending.rejectEntry) {
        await global.MaPending.rejectEntry(story.maEntry);
      }
      // Drop the story from state + remove the card from the feed.
      stories = stories.filter((s) => s.id !== id);
      if (card) card.remove();
      const remaining = visibleStories().length;
      setStatus(`${remaining} ${remaining === 1 ? 'story' : 'stories'}`);
    } catch (e) {
      console.error('[news] M&A action failed', e);
      btn.disabled = false;
      btn.textContent = action === 'approve' ? '✓ Flag as Rumor' : '✕ Dismiss';
    }
  }

  // ---------- AI relevance annotation (batched) ----------
  async function requestRelevance() {
    if (relevanceRequested) return;
    if (!global.SignalAIApi || typeof global.SignalAIApi.hasKey !== 'function' || !global.SignalAIApi.hasKey()) {
      // No key: strip loading placeholders so cards render cleanly.
      stripRelevancePlaceholders();
      return;
    }
    const inCov = stories.filter((s) => s.inCoverage);
    if (!inCov.length) return;
    relevanceRequested = true;

    const tickerList = coverageList().join(', ');
    const storyList = inCov
      .map((s) => `[${s.id}] ${s.tickers.join('/')} — ${s.headline}`)
      .join('\n');

    const system = 'You are a precise buy-side equity research assistant. Return only valid JSON. No prose, no markdown fences.';
    const prompt = `You are a buy-side equity research assistant. For each news item below, write exactly one sentence explaining why it matters to an investor who holds these tickers: ${tickerList}.

News items:
${storyList}

Return a JSON array: [{"id": "...", "relevance": "..."}]`;

    try {
      const resp = await global.SignalAIApi.call({ model: 'sonar', system, prompt, max_tokens: 1200 });
      if (!resp || !resp.ok) { stripRelevancePlaceholders(); return; }
      const parsed = global.SignalAIApi.parseJsonLoose(resp.content);
      if (!Array.isArray(parsed)) { stripRelevancePlaceholders(); return; }
      const byId = {};
      parsed.forEach((p) => { if (p && p.id) byId[p.id] = p.relevance; });
      injectRelevance(byId);
    } catch (e) {
      console.warn('[news] relevance call failed:', e);
      stripRelevancePlaceholders();
    }
  }

  function injectRelevance(byId) {
    const feed = $feed();
    if (!feed) return;
    stories.forEach((s) => {
      if (!s.inCoverage) return;
      const text = byId[s.id];
      const card = feed.querySelector(`.news-card[data-id="${s.id}"]`);
      const block = card && card.querySelector('.news-card-relevance');
      if (text) {
        s.relevance = text;
        if (block) {
          block.classList.remove('news-relevance-loading');
          block.textContent = text;
        }
      } else if (block) {
        block.remove();
      }
    });
  }

  function stripRelevancePlaceholders() {
    const feed = $feed();
    if (!feed) return;
    feed.querySelectorAll('.news-card-relevance.news-relevance-loading').forEach((el) => el.remove());
  }

  // ---------- Filter bar + premarket wiring ----------
  function wireFilterBar() {
    const bar = $filterBar();
    if (!bar || bar._newsWired) return;
    bar._newsWired = true;

    bar.querySelectorAll('.news-filter-pill').forEach((pill) => {
      pill.addEventListener('click', () => {
        let group = null;
        if (pill.dataset.scope) { filters.scope = pill.dataset.scope; group = 'scope'; }
        else if (pill.dataset.signal) { filters.signal = pill.dataset.signal; group = 'signal'; }
        else if (pill.dataset.time) { filters.time = pill.dataset.time; group = 'time'; }
        if (group) {
          bar.querySelectorAll(`.news-filter-pill[data-${group}]`).forEach((p) => p.classList.remove('active'));
          pill.classList.add('active');
        }
        render();
      });
    });

    const pm = $premarketBtn();
    if (pm) {
      pm.addEventListener('click', () => {
        filters.premarket = !filters.premarket;
        pm.classList.toggle('active', filters.premarket);
        render();
      });
    }
  }

  // ---------- Public lifecycle ----------
  async function init() {
    wireFilterBar();
    const feed = $feed();
    if (feed && !feed.querySelector('.news-card')) {
      feed.innerHTML = '<div class="news-loading">Loading news...</div>';
    }
    setStatus('updating…');
    await loadEarningsCalendar();
    await loadStories();
    render();
    initialized = true;
    return true;
  }

  async function refresh() {
    wireFilterBar();
    setStatus('updating…');
    await loadEarningsCalendar();
    await loadStories();
    render();
    initialized = true;
    return true;
  }

  // ---------- Dev assertions (opt-in via window.__NEWS_DEBUG) ----------
  function runDebugAssertions() {
    const cov = ['NVDA', 'ADSK', 'PTC', 'V', 'CRM'];
    aliasIndex = null;
    const m1 = matchTickers('Nvidia surges on Blackwell demand', cov);
    console.assert(m1.has('NVDA'), '[news][test] "Nvidia ..." should tag NVDA');
    const m2 = matchTickers('Autodesk is considering a cash-and-stock acquisition of PTC', cov);
    console.assert(m2.has('ADSK') && m2.has('PTC'), '[news][test] Autodesk/PTC should tag both ADSK and PTC');
    const m4 = matchTickers('Shares of $V rallied', cov);
    console.assert(m4.has('V'), '[news][test] ticker form $V should match V');
    const m5 = matchTickers('Salesforce raises full-year guidance', cov);
    console.assert(m5.has('CRM'), '[news][test] "Salesforce" should tag CRM');
    console.log('[news][test] alias assertions done');
  }
  if (typeof global !== 'undefined' && global.__NEWS_DEBUG) runDebugAssertions();

  global.NewsModule = {
    init, render, refresh,
    _matchTickers: matchTickers,
    _runDebugAssertions: runDebugAssertions,
    _stories: () => stories,
  };
  // Back-compat: shell.js MORE_PANE_CONFIG.news.loaderName === 'fetchNews'.
  global.fetchNews = refresh;
})(typeof window !== 'undefined' ? window : globalThis);
