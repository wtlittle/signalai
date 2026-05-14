/**
 * live-quotes.js — Tier 2 live-ish quote polling
 *
 * Every 60s during US market hours, re-fetches the watchlist's quote
 * batch from Supabase (already done by fetchQuotesBatch / fetchAllTickers)
 * and surgically updates price/change cells in the visible table without
 * a full DOM re-render.
 *
 * Pauses when:
 *   - The tab is hidden (document.hidden)
 *   - Outside US market hours (9:30am-4:30pm ET, weekdays)
 *   - The user is interacting with a popup overlay
 *
 * Updates the refresh-timer chip with "Live · Xs" countdown so the user
 * knows it's working.
 */
(function () {
  const POLL_INTERVAL_MS = 60 * 1000;
  const TICK_INTERVAL_MS = 1000;
  let _intervalId = null;
  let _countdown = 60;
  let _isPolling = false;

  function isMarketHoursET() {
    // Crude US market-hours check using the browser clock. We convert to
    // America/New_York via Intl so DST is handled correctly.
    try {
      const now = new Date();
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        weekday: 'short',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
      }).formatToParts(now);
      const weekday = parts.find((p) => p.type === 'weekday').value;
      const hour = parseInt(parts.find((p) => p.type === 'hour').value, 10);
      const minute = parseInt(parts.find((p) => p.type === 'minute').value, 10);
      if (weekday === 'Sat' || weekday === 'Sun') return false;
      const mins = hour * 60 + minute;
      // 9:30 = 570, 16:30 = 990 (give a 30-min post-close buffer for AMC)
      return mins >= 570 && mins <= 990;
    } catch (_) {
      return true; // fail open
    }
  }

  function fmtPercent(n) {
    if (typeof window.formatPercent === 'function') return window.formatPercent(n);
    if (n == null || isNaN(n)) return '—';
    const sign = n > 0 ? '+' : '';
    return `${sign}${Number(n).toFixed(2)}%`;
  }
  function fmtPrice(n) {
    if (typeof window.formatPrice === 'function') return window.formatPrice(n);
    if (n == null || isNaN(n)) return '—';
    return Number(n).toFixed(2);
  }
  function percentClass(n) {
    if (typeof window.percentClass === 'function') return window.percentClass(n);
    if (n == null || isNaN(n)) return '';
    return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
  }

  function updateRowDom(ticker, row) {
    if (!row) return;
    const tickerCell = document.querySelector(`.cell-ticker[data-ticker="${ticker}"]`);
    if (!tickerCell) return;
    const tr = tickerCell.closest('tr');
    if (!tr) return;
    const cells = tr.children;
    // Layout from app.js row template (0-indexed):
    //   0 ticker, 1 name, 2 subsector, 3 price, 4 mcap, 5 ev, 6 ev/sales,
    //   7 ev/fcf, 8 ytd, 9 1d, 10 1w, 11 1m, 12 3m, 13 1y, 14 3y, ...
    try {
      if (cells[3]) cells[3].textContent = fmtPrice(row.price);
      const heatTargets = [
        [8, row.ytd], [9, row.d1], [10, row.w1], [11, row.m1],
        [12, row.m3], [13, row.y1], [14, row.y3],
      ];
      heatTargets.forEach(([idx, val]) => {
        const td = cells[idx];
        if (!td) return;
        td.textContent = fmtPercent(val);
        td.classList.remove('pos', 'neg');
        const cls = percentClass(val);
        if (cls) td.classList.add(cls);
      });
      tr.classList.add('row-flash');
      setTimeout(() => tr.classList.remove('row-flash'), 600);
    } catch (e) {
      // Layout drift — bail silently. A full renderTable() will fix it.
    }
  }

  async function pollOnce() {
    if (_isPolling) return;
    if (typeof window.fetchAllTickers !== 'function') return;
    if (!Array.isArray(window.tickerList) || !window.tickerList.length) return;
    _isPolling = true;
    try {
      const fresh = await window.fetchAllTickers(window.tickerList);
      if (!fresh || typeof fresh !== 'object') return;
      Object.keys(fresh).forEach((ticker) => {
        const row = fresh[ticker];
        if (!row) return;
        if (window.tickerData) window.tickerData[ticker] = row;
        updateRowDom(ticker, row);
      });
      const chip = document.getElementById('refresh-timer');
      if (chip) chip.title = `Last live update: ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      // Network hiccup — try again next tick
      console.debug('[live-quotes] poll failed', e && e.message);
    } finally {
      _isPolling = false;
    }
  }

  function tick() {
    if (document.hidden) {
      // Reset the countdown so we don't fire immediately when tab regains focus
      _countdown = POLL_INTERVAL_MS / 1000;
      return;
    }
    if (!isMarketHoursET()) {
      _countdown = POLL_INTERVAL_MS / 1000;
      const chip = document.getElementById('refresh-timer');
      if (chip && !chip.dataset.liveOff) {
        chip.dataset.liveOff = '1';
        chip.title = 'Live updates paused outside US market hours';
      }
      return;
    }
    const chip = document.getElementById('refresh-timer');
    if (chip) {
      delete chip.dataset.liveOff;
      chip.textContent = `Live · ${_countdown}s`;
    }
    _countdown -= 1;
    if (_countdown <= 0) {
      _countdown = POLL_INTERVAL_MS / 1000;
      pollOnce();
    }
  }

  function start() {
    if (_intervalId) return;
    _intervalId = setInterval(tick, TICK_INTERVAL_MS);
  }
  function stop() {
    if (_intervalId) {
      clearInterval(_intervalId);
      _intervalId = null;
    }
  }

  function init() {
    // Defer to allow app.js renderTable + fetchAllTickers to finish first.
    setTimeout(start, 3000);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.LiveQuotes = { pollNow: pollOnce, start: start, stop: stop };
})();
