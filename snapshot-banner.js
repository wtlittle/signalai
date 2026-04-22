/* ===== SNAPSHOT-BANNER.JS — Wires the global #snapshot-banner ===== */
/*
 * Subscribes to SignalSnapshot.onStatus() and:
 *  - Shows the banner when any snapshot fetch has failed
 *  - Updates the text to reflect which file(s) failed
 *  - Retry button → reloads the page (force-refresh snapshot fetches)
 *  - Dismiss button → hides until next failure
 */
(function () {
  'use strict';

  if (typeof window === 'undefined') return;
  if (!window.SignalSnapshot) {
    // Snapshot system not present — nothing to wire
    return;
  }

  function init() {
    var banner = document.getElementById('snapshot-banner');
    var textEl = document.getElementById('snapshot-banner-text');
    var retryBtn = document.getElementById('snapshot-banner-retry');
    var dismissBtn = document.getElementById('snapshot-banner-dismiss');
    if (!banner) return;

    var dismissed = false;

    function show(status) {
      if (dismissed) return;
      var files = (status.failures || [])
        .map(function (f) { return f.filename; })
        .filter(function (v, i, a) { return a.indexOf(v) === i; });
      var suffix = files.length
        ? ' Affected: ' + files.slice(0, 3).join(', ') + (files.length > 3 ? '…' : '')
        : '';
      if (textEl) {
        textEl.textContent = 'Data source degraded — some panels may be stale.' + suffix;
      }
      banner.hidden = false;
    }
    function hide() {
      banner.hidden = true;
    }

    window.SignalSnapshot.onStatus(function (status) {
      if (!status.ok) show(status);
    });

    // If failures already happened before we wired up, reflect them
    var current = window.SignalSnapshot.status && window.SignalSnapshot.status();
    if (current && !current.ok) show(current);

    if (retryBtn) {
      retryBtn.addEventListener('click', function () {
        try {
          // Force a hard reload so every snapshot fetch re-runs with a fresh
          // cache-bust. Simpler than trying to invalidate every cached module.
          window.location.reload();
        } catch (e) { /* no-op */ }
      });
    }
    if (dismissBtn) {
      dismissBtn.addEventListener('click', function () {
        dismissed = true;
        hide();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
