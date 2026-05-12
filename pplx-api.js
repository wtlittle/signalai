/* pplx-api.js — shared client-side Perplexity API helper for SignalAI.
 *
 * Key is stored ONLY in localStorage under `signalai_pplx_api_key`. It never
 * touches the repo, never leaves the user's browser except in Authorization
 * headers to api.perplexity.ai.
 *
 * Public API exposed on window.SignalAIApi:
 *   - hasKey()                       -> boolean
 *   - getKey() / setKey(k) / clearKey()
 *   - getDefaults()                  -> { compareModel, drilldownModel, defaultEffort }
 *   - setDefault(key, value)
 *   - call({model, system, prompt, max_tokens, reasoning_effort, response_format})
 *       -> Promise<{ ok, content, raw, usage, cost_estimate, error }>
 *   - parseJsonLoose(text)           -> any | null   (repair-aware)
 *   - estimateCost({model, usage})   -> number       (USD)
 *
 * No external dependencies. Runs in evergreen browsers.
 */
(function (global) {
  'use strict';

  var STORAGE_KEY = 'signalai_pplx_api_key';
  var DEFAULTS_KEY = 'signalai_pplx_defaults_v1';
  var BASE_URL = 'https://api.perplexity.ai/chat/completions';

  // --- Pricing table (USD per 1M tokens / per 1K requests). Sourced from
  // docs.perplexity.ai/docs/getting-started/pricing as of May 2026. Used
  // ONLY for cost-estimate UI; not authoritative billing.
  var PRICING = {
    'sonar':                { in: 1,  out: 1,   req: 0.005,  reasoning: 0, citation: 0 },
    'sonar-pro':            { in: 3,  out: 15,  req: 0.014,  reasoning: 0, citation: 0 },
    'sonar-reasoning':      { in: 1,  out: 5,   req: 0.005,  reasoning: 0, citation: 0 },
    'sonar-reasoning-pro':  { in: 2,  out: 8,   req: 0.014,  reasoning: 0, citation: 0 },
    'sonar-deep-research':  { in: 2,  out: 8,   req: 0.005,  reasoning: 3, citation: 2 }
  };

  var DEFAULT_PREFS = {
    compareModel:    'sonar-reasoning-pro',
    drilldownModel:  'sonar-deep-research',
    defaultEffort:   'medium'   // 'low' | 'medium' | 'high'
  };

  // ---------- Key + prefs ----------
  function getKey() {
    try { return localStorage.getItem(STORAGE_KEY) || ''; } catch (_) { return ''; }
  }
  function setKey(k) {
    try {
      if (k && typeof k === 'string') localStorage.setItem(STORAGE_KEY, k.trim());
      else localStorage.removeItem(STORAGE_KEY);
    } catch (_) {}
  }
  function clearKey() { try { localStorage.removeItem(STORAGE_KEY); } catch (_) {} }
  function hasKey() { return !!getKey(); }

  function getDefaults() {
    try {
      var raw = localStorage.getItem(DEFAULTS_KEY);
      if (!raw) return Object.assign({}, DEFAULT_PREFS);
      var parsed = JSON.parse(raw);
      return Object.assign({}, DEFAULT_PREFS, parsed || {});
    } catch (_) { return Object.assign({}, DEFAULT_PREFS); }
  }
  function setDefault(k, v) {
    try {
      var cur = getDefaults();
      cur[k] = v;
      localStorage.setItem(DEFAULTS_KEY, JSON.stringify(cur));
    } catch (_) {}
  }

  // ---------- JSON repair ----------
  function stripFences(text) {
    if (!text) return '';
    var t = String(text).trim();
    if (t.indexOf('```') === 0) {
      // remove ```json\n ... ```
      var firstNl = t.indexOf('\n');
      if (firstNl > 0) t = t.slice(firstNl + 1);
      var lastFence = t.lastIndexOf('```');
      if (lastFence >= 0) t = t.slice(0, lastFence);
    }
    return t.trim();
  }
  function extractJsonBlock(text) {
    var t = stripFences(text);
    var startObj = t.indexOf('{'), startArr = t.indexOf('[');
    var start = -1;
    if (startObj === -1) start = startArr;
    else if (startArr === -1) start = startObj;
    else start = Math.min(startObj, startArr);
    if (start < 0) return t;
    var open = t[start];
    var close = open === '{' ? '}' : ']';
    var depth = 0, inStr = false, escape = false, end = -1;
    for (var i = start; i < t.length; i++) {
      var c = t[i];
      if (inStr) {
        if (escape) escape = false;
        else if (c === '\\') escape = true;
        else if (c === '"') inStr = false;
      } else {
        if (c === '"') inStr = true;
        else if (c === open) depth++;
        else if (c === close) {
          depth--;
          if (depth === 0) { end = i; break; }
        }
      }
    }
    return end > 0 ? t.slice(start, end + 1) : t.slice(start);
  }
  function parseJsonLoose(text) {
    if (text == null) return null;
    var candidate = extractJsonBlock(text);
    try { return JSON.parse(candidate); } catch (_) {}
    // Try trimming trailing commas
    try { return JSON.parse(candidate.replace(/,\s*([}\]])/g, '$1')); } catch (_) {}
    // Last-ditch: remove smart quotes
    try {
      var cleaned = candidate
        .replace(/[\u201C\u201D]/g, '"')
        .replace(/[\u2018\u2019]/g, "'")
        .replace(/,\s*([}\]])/g, '$1');
      return JSON.parse(cleaned);
    } catch (_) {}
    return null;
  }

  // ---------- Cost estimate ----------
  function estimateCost(opts) {
    opts = opts || {};
    var model = opts.model || 'sonar-pro';
    var usage = opts.usage || {};
    var p = PRICING[model];
    if (!p) return 0;
    var inTok = (usage.prompt_tokens || 0) / 1e6;
    var outTok = (usage.completion_tokens || 0) / 1e6;
    var reaTok = (usage.reasoning_tokens || 0) / 1e6;
    var citTok = (usage.citation_tokens || 0) / 1e6;
    var queries = (usage.num_search_queries || 1);
    var cost = inTok * p.in + outTok * p.out + reaTok * (p.reasoning || 0) + citTok * (p.citation || 0);
    // request fee billed per search query for deep-research, per call otherwise
    if (model === 'sonar-deep-research') cost += queries * (p.req || 0);
    else cost += (p.req || 0);
    return cost;
  }

  function formatCost(n) {
    if (!n || n < 0.001) return '<$0.001';
    if (n < 0.01)  return '$' + n.toFixed(4);
    if (n < 1)     return '$' + n.toFixed(3);
    return '$' + n.toFixed(2);
  }

  // ---------- Core call ----------
  function call(opts) {
    opts = opts || {};
    var key = getKey();
    if (!key) {
      return Promise.resolve({
        ok: false,
        error: 'No API key set. Open Settings (gear icon) to add your Perplexity API key.'
      });
    }
    var model = opts.model || DEFAULT_PREFS.compareModel;
    var body = {
      model: model,
      messages: [
        { role: 'system', content: opts.system || 'You are a precise financial analyst. Return only valid JSON. No prose, no markdown fences.' },
        { role: 'user', content: opts.prompt || '' }
      ],
      temperature: opts.temperature != null ? opts.temperature : 0.1
    };
    if (opts.max_tokens) body.max_tokens = opts.max_tokens;
    // reasoning_effort is only meaningful for sonar-deep-research
    if (model === 'sonar-deep-research' && opts.reasoning_effort) {
      body.reasoning_effort = opts.reasoning_effort;
    }
    if (opts.response_format) body.response_format = opts.response_format;

    var ctrl = (global.AbortController ? new global.AbortController() : null);
    var timeoutMs = opts.timeout_ms || (model === 'sonar-deep-research' ? 600000 : 120000);
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, timeoutMs);

    return fetch(BASE_URL, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body),
      signal: ctrl ? ctrl.signal : undefined
    }).then(function (resp) {
      clearTimeout(timer);
      return resp.text().then(function (txt) {
        if (!resp.ok) {
          var detail = txt;
          try { var j = JSON.parse(txt); detail = j.error && j.error.message ? j.error.message : detail; } catch (_) {}
          var hint = '';
          if (resp.status === 401) hint = ' (key invalid — re-check Settings)';
          else if (resp.status === 429) hint = ' (rate limit — wait or upgrade tier)';
          else if (resp.status === 400) hint = ' (model name may be wrong: ' + model + ')';
          return { ok: false, error: 'HTTP ' + resp.status + hint + ': ' + (detail || resp.statusText) };
        }
        var data; try { data = JSON.parse(txt); } catch (e) { return { ok: false, error: 'Bad JSON response: ' + e.message }; }
        var choice = data && data.choices && data.choices[0];
        var content = choice && choice.message && choice.message.content || '';
        var usage = data && data.usage || {};
        return {
          ok: true,
          content: content,
          raw: data,
          usage: usage,
          model: data.model || model,
          cost_estimate: estimateCost({ model: data.model || model, usage: usage })
        };
      });
    }).catch(function (err) {
      clearTimeout(timer);
      if (err && err.name === 'AbortError') {
        return { ok: false, error: 'Request timed out after ' + Math.round(timeoutMs / 1000) + 's. Deep Research can take 2-5 min — try increasing timeout or switch to sonar-reasoning-pro for faster results.' };
      }
      // CORS errors typically present as TypeError with "Failed to fetch"
      var msg = (err && err.message) || String(err);
      if (/Failed to fetch|NetworkError/i.test(msg)) {
        return { ok: false, error: 'Network/CORS error reaching api.perplexity.ai. If you see this consistently, your browser may be blocking the request — try a different browser or use the paste-back fallback.' };
      }
      return { ok: false, error: msg };
    });
  }

  global.SignalAIApi = {
    hasKey: hasKey,
    getKey: getKey,
    setKey: setKey,
    clearKey: clearKey,
    getDefaults: getDefaults,
    setDefault: setDefault,
    PRICING: PRICING,
    DEFAULT_PREFS: DEFAULT_PREFS,
    call: call,
    parseJsonLoose: parseJsonLoose,
    estimateCost: estimateCost,
    formatCost: formatCost
  };
})(typeof window !== 'undefined' ? window : globalThis);
