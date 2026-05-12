/* api-settings.js — small modal for managing the Perplexity API key + model
 * preferences. Mounts a gear button in the header (#header-actions or .header
 * row). Opens a centered popup. Saves to localStorage via SignalAIApi. */
(function (global) {
  'use strict';
  if (!global.SignalAIApi) {
    console.warn('[api-settings] SignalAIApi not loaded yet');
    return;
  }
  var API = global.SignalAIApi;
  var MODAL_ID = 'api-settings-modal';
  var BTN_ID = 'api-settings-btn';

  var MODELS = [
    { id: 'sonar',                label: 'Sonar (fast, cheap)' },
    { id: 'sonar-pro',            label: 'Sonar Pro' },
    { id: 'sonar-reasoning',      label: 'Sonar Reasoning' },
    { id: 'sonar-reasoning-pro',  label: 'Sonar Reasoning Pro (recommended for Compare)' },
    { id: 'sonar-deep-research',  label: 'Sonar Deep Research (recommended for Drilldown)' }
  ];

  function el(tag, attrs, html) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'className') n.className = attrs[k];
      else if (k === 'style') n.setAttribute('style', attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    if (html != null) n.innerHTML = html;
    return n;
  }

  function ensureButton() {
    if (document.getElementById(BTN_ID)) return;
    // Attach next to existing header buttons. Prefer the topbar actions area.
    var host =
      document.querySelector('.topbar-actions') ||
      document.querySelector('.header-actions') ||
      document.querySelector('header .right') ||
      document.querySelector('header') ||
      document.body;
    if (!host) return;
    var btn = el('button', {
      id: BTN_ID,
      type: 'button',
      title: 'Perplexity API Settings — set key, choose models',
      className: 'topbar-action api-settings-btn'
    }, 'API');
    btn.addEventListener('click', open);
    // Insert before the meta block when available so it sits with other action buttons.
    var meta = host.querySelector('.topbar-meta');
    if (meta && meta.parentNode === host) host.insertBefore(btn, meta);
    else host.appendChild(btn);
  }

  function build() {
    var k = API.getKey();
    var prefs = API.getDefaults();
    var maskedHelp = k
      ? '<span class="api-status-ok">Key stored: ' + k.slice(0, 6) + '...' + k.slice(-4) + ' (' + k.length + ' chars)</span>'
      : '<span class="api-status-warn">No key set. Paste your Perplexity API key below.</span>';

    var modelOptions = function (sel) {
      return MODELS.map(function (m) {
        return '<option value="' + m.id + '"' + (m.id === sel ? ' selected' : '') + '>' + m.label + '</option>';
      }).join('');
    };

    var html =
      '<div class="api-settings-card">' +
      '  <div class="api-settings-head">' +
      '    <h3>Perplexity API Settings</h3>' +
      '    <button type="button" class="api-settings-close" aria-label="Close">&times;</button>' +
      '  </div>' +
      '  <p class="api-settings-intro">Stored only in this browser (<code>localStorage</code>). Used for Compare AI Read, Drilldown, and other on-page LLM features. <a href="https://www.perplexity.ai/account/api/keys" target="_blank" rel="noopener">Get a key</a>.</p>' +
      '  <div class="api-settings-row">' +
      '    <label for="api-settings-key-input">API key</label>' +
      '    <input id="api-settings-key-input" type="password" autocomplete="off" placeholder="pplx-..." value="' + (k ? k.replace(/"/g, '&quot;') : '') + '">' +
      '    <div class="api-settings-hint">' + maskedHelp + '</div>' +
      '  </div>' +
      '  <div class="api-settings-grid">' +
      '    <div class="api-settings-col">' +
      '      <label for="api-compare-model">Compare model</label>' +
      '      <select id="api-compare-model">' + modelOptions(prefs.compareModel) + '</select>' +
      '    </div>' +
      '    <div class="api-settings-col">' +
      '      <label for="api-drilldown-model">Drilldown model</label>' +
      '      <select id="api-drilldown-model">' + modelOptions(prefs.drilldownModel) + '</select>' +
      '    </div>' +
      '    <div class="api-settings-col">' +
      '      <label for="api-default-effort">Deep Research effort</label>' +
      '      <select id="api-default-effort">' +
      '        <option value="low"' + (prefs.defaultEffort === 'low' ? ' selected' : '') + '>low (fastest, cheapest)</option>' +
      '        <option value="medium"' + (prefs.defaultEffort === 'medium' ? ' selected' : '') + '>medium</option>' +
      '        <option value="high"' + (prefs.defaultEffort === 'high' ? ' selected' : '') + '>high (deepest, slowest)</option>' +
      '      </select>' +
      '    </div>' +
      '  </div>' +
      '  <div class="api-settings-actions">' +
      '    <button type="button" class="btn-link api-settings-clear">Clear key</button>' +
      '    <div class="api-settings-actions-right">' +
      '      <button type="button" class="btn-link api-settings-test">Test connection</button>' +
      '      <button type="button" class="btn-primary api-settings-save">Save</button>' +
      '    </div>' +
      '  </div>' +
      '  <div class="api-settings-test-out" id="api-settings-test-out"></div>' +
      '  <details class="api-settings-cost">' +
      '    <summary>Estimated per-call cost</summary>' +
      '    <ul>' +
      '      <li><strong>Compare AI Read</strong> (sonar-reasoning-pro): ~$0.05-0.15</li>' +
      '      <li><strong>Drilldown</strong> (sonar-deep-research, low): ~$0.20-0.40</li>' +
      '      <li><strong>Drilldown</strong> (sonar-deep-research, medium): ~$0.40-0.80</li>' +
      '      <li><strong>Earnings note refresh</strong> (sonar-deep-research, low): ~$0.20-0.30</li>' +
      '    </ul>' +
      '    <p class="api-settings-cost-note">Estimates are post-call; the actual response includes token usage and an estimated cost. Track real spend in your <a href="https://www.perplexity.ai/account/api/billing" target="_blank" rel="noopener">Perplexity billing dashboard</a>.</p>' +
      '  </details>' +
      '</div>';

    var overlay = el('div', { id: MODAL_ID, className: 'api-settings-overlay' }, html);
    overlay.addEventListener('click', function (ev) { if (ev.target === overlay) close(); });
    overlay.querySelector('.api-settings-close').addEventListener('click', close);
    overlay.querySelector('.api-settings-clear').addEventListener('click', function () {
      API.clearKey();
      close(); open();
    });
    overlay.querySelector('.api-settings-save').addEventListener('click', function () {
      var v = overlay.querySelector('#api-settings-key-input').value.trim();
      if (v) API.setKey(v); else API.clearKey();
      API.setDefault('compareModel', overlay.querySelector('#api-compare-model').value);
      API.setDefault('drilldownModel', overlay.querySelector('#api-drilldown-model').value);
      API.setDefault('defaultEffort', overlay.querySelector('#api-default-effort').value);
      close();
    });
    overlay.querySelector('.api-settings-test').addEventListener('click', function () {
      var out = overlay.querySelector('#api-settings-test-out');
      var v = overlay.querySelector('#api-settings-key-input').value.trim();
      if (!v) { out.innerHTML = '<span class="api-status-warn">Enter a key first.</span>'; return; }
      // Temporarily set, run test, restore if it fails.
      var prev = API.getKey();
      API.setKey(v);
      out.innerHTML = '<span class="api-status-info">Testing with sonar (1 call, ~$0.005)...</span>';
      API.call({
        model: 'sonar',
        max_tokens: 64,
        system: 'Reply with the JSON {"ok": true} and nothing else.',
        prompt: 'Confirm the API key works.'
      }).then(function (res) {
        if (res.ok) {
          out.innerHTML = '<span class="api-status-ok">OK. Model: ' + (res.model || 'sonar') + '. Estimated cost: ' + API.formatCost(res.cost_estimate) + '.</span>';
        } else {
          out.innerHTML = '<span class="api-status-error">' + escapeHtml(res.error || 'Unknown error') + '</span>';
          if (prev) API.setKey(prev); else API.clearKey();
        }
      });
    });
    return overlay;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function open() {
    close();
    document.body.appendChild(build());
  }
  function close() {
    var n = document.getElementById(MODAL_ID);
    if (n && n.parentNode) n.parentNode.removeChild(n);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensureButton);
  } else {
    ensureButton();
  }

  global.ApiSettings = { open: open, close: close };
})(typeof window !== 'undefined' ? window : globalThis);
