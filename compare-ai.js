/* ===== COMPARE-AI.JS — Real AI cross-company read via Perplexity Deep Research =====
 *
 * Pattern mirrors drilldown-surface.js: open a fresh Perplexity Deep Research
 * thread, copy a comp-pack prompt to clipboard, let the analyst paste it in,
 * then paste the JSON/markdown result back into a textarea to be saved
 * locally. No backend, no API key. The "AI Read" tab in compare.js calls
 * SignalCompareAI.render(body, tickers, rows).
 */
(function (global) {
  'use strict';

  const STORAGE_PREFIX = 'signalai_compare_ai_v1::';

  // ------------------------- KEY HELPERS -------------------------
  function compKey(tickers) {
    return tickers.slice().sort().join('|');
  }

  function loadSaved(tickers) {
    try {
      const raw = localStorage.getItem(STORAGE_PREFIX + compKey(tickers));
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) { return null; }
  }

  function saveResult(tickers, result) {
    try {
      const entry = {
        tickers: tickers.slice(),
        saved_at: new Date().toISOString(),
        raw: result.raw || '',
        parsed: result.parsed || null,
      };
      localStorage.setItem(STORAGE_PREFIX + compKey(tickers), JSON.stringify(entry));
      return entry;
    } catch (e) { return null; }
  }

  // ------------------------- COMP-PACK + PROMPT -------------------------
  function buildCompPack(tickers, rows) {
    const lines = [];
    lines.push('Comparison set: ' + tickers.join(' vs '));
    lines.push('Date: ' + new Date().toISOString().slice(0, 10));
    lines.push('');
    rows.forEach((r, i) => {
      const t = tickers[i];
      const name = r.name || t;
      lines.push('--- ' + t + ' (' + name + ') ---');
      lines.push('  Subsector: ' + (r.subsector || 'n/a'));
      lines.push('  Price: ' + fmtN(r.price) + '  Market cap: ' + fmtLarge(r.marketCap) + '  EV: ' + fmtLarge(r.ev));
      lines.push('  FY1 EV/Sales: ' + fmtMult(r.evSales) + '  FY1 EV/FCF: ' + fmtMult(r.evFcf) + '  Forward P/E: ' + fmtMult(r.forwardPE));
      lines.push('  Revenue growth: ' + fmtPct(r.revenueGrowth) + '  FCF margin: ' + fmtPct(r.fcfMargin) + '  Operating margin: ' + fmtPct(r.operatingMargins));
      lines.push('  Rule of 40: ' + fmtN(r.ruleOf40) + '  TTM revenue: ' + fmtLarge(r.totalRevenue) + '  TTM FCF: ' + fmtLarge(r.freeCashflow));
      lines.push('  Returns -> 1M: ' + fmtPct(r.m1) + '  3M: ' + fmtPct(r.m3) + '  YTD: ' + fmtPct(r.ytd) + '  1Y: ' + fmtPct(r.y1) + '  3Y: ' + fmtPct(r.y3));
      lines.push('  Analyst target: ' + fmtN(r.targetMeanPrice) + ' (' + (r.numberOfAnalystOpinions || 0) + ' analysts, ' + (r.recommendationKey || 'n/a') + ')');
      lines.push('  52W: ' + fmtN(r.fiftyTwoWeekLow) + ' - ' + fmtN(r.fiftyTwoWeekHigh));
      // earnings intel
      const intel = getIntel(t);
      if (intel) {
        lines.push('  State: ' + (intel.state || 'n/a') + '  Last earnings: ' + (intel.last_earnings_date || 'n/a') + '  Next: ' + (intel.next_earnings_date || 'n/a'));
        if (intel.bottom_line) lines.push('  Bottom line: ' + truncate(intel.bottom_line, 280));
        const rv = intel.post_earnings_review || {};
        if (rv.active && rv.what_happened_headline) {
          lines.push('  Most recent reaction: ' + (rv.stock_reaction_pct != null ? rv.stock_reaction_pct.toFixed(1) + '%' : 'n/a'));
          lines.push('  What happened: ' + truncate(rv.what_happened_headline, 200));
        }
      }
      lines.push('');
    });
    return lines.join('\n');
  }

  function buildPrompt(tickers, rows) {
    const pack = buildCompPack(tickers, rows);
    return [
      'You are a buy-side equity analyst writing a head-to-head comp read for ' + tickers.join(' vs ') + '.',
      '',
      'CONSTRAINTS:',
      '- Be specific. Reference the data in the COMP PACK below; do NOT invent numbers.',
      '- Where data is missing, say "n/a" rather than estimating.',
      '- Output VALID JSON ONLY, wrapped in a single ```json fenced block. No prose outside the block.',
      '- Keep narrative tight; this is for a buy-side analyst, not a retail audience.',
      '',
      'SCHEMA:',
      '{',
      '  "valuation": "1-3 sentence framing of premium vs discount across the group, anchored to FY1 EV/Sales and EV/FCF.",',
      '  "growth": "1-3 sentence framing of who is accelerating vs decelerating; cite reported growth.",',
      '  "profitability": "1-3 sentence framing of FCF/operating margin durability and Rule of 40 quality.",',
      '  "market_underwrites": "What the current multiple implies the market believes about each name. Be candid.",',
      '  "key_debate": "The single most important debate across the comp set. One paragraph.",',
      '  "strongest_setup_today": {"ticker": "TICK", "why": "Why this setup screens best right now, with specific metrics."},',
      '  "what_invalidates": "What would break the call on the strongest-setup name. 1-2 sentences.",',
      '  "per_ticker": [',
      '    {"ticker": "TICK", "bull": "Bull case in one sentence.", "bear": "Bear case in one sentence.", "watch": "What to watch into next print."}',
      '  ]',
      '}',
      '',
      'COMP PACK:',
      '```',
      pack,
      '```',
      '',
      'Return ONLY the JSON in the fenced block.'
    ].join('\n');
  }

  // Short prefill kept under URL length limits for the search/new?q= param.
  function shortPrefill(tickers) {
    return 'Investment comp read: ' + tickers.join(' vs ') + ' — head-to-head debate, valuation, growth, durability, key risks.';
  }

  function buildDeepResearchUrl(tickers) {
    return (
      'https://www.perplexity.ai/search/new?mode=research&q=' +
      encodeURIComponent(shortPrefill(tickers))
    );
  }

  // ------------------------- CLIPBOARD -------------------------
  function copyToClipboard(text) {
    return new Promise(function (resolve) {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () { resolve(true); }, function () { resolve(fallbackCopy(text)); });
        } else {
          resolve(fallbackCopy(text));
        }
      } catch (e) { resolve(false); }
    });
  }
  function fallbackCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return !!ok;
    } catch (e) { return false; }
  }

  // ------------------------- PARSE LLM OUTPUT -------------------------
  function tryParse(text) {
    if (!text) return null;
    // Try to extract a ```json ... ``` block first
    var m = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
    var json = m ? m[1] : text;
    json = json.trim();
    // Strip leading prose like "Here's the JSON:"
    var braceIdx = json.indexOf('{');
    if (braceIdx > 0) json = json.slice(braceIdx);
    try {
      return JSON.parse(json);
    } catch (e) {
      try {
        // Repair common issue: trailing commas
        return JSON.parse(json.replace(/,(\s*[}\]])/g, '$1'));
      } catch (e2) {
        return null;
      }
    }
  }

  // ------------------------- RENDER -------------------------
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function renderResult(parsed) {
    if (!parsed) return '';
    var html = '<div class="cmp-ai-result">';
    var sections = [
      ['Valuation', parsed.valuation],
      ['Growth', parsed.growth],
      ['Profitability / durability', parsed.profitability],
      ['What the market is underwriting', parsed.market_underwrites],
      ['The key debate', parsed.key_debate],
    ];
    sections.forEach(function (s) {
      if (s[1]) {
        html += '<div class="cmp-ai-block"><h4>' + escapeHtml(s[0]) + '</h4><p>' + escapeHtml(s[1]) + '</p></div>';
      }
    });
    if (parsed.strongest_setup_today && parsed.strongest_setup_today.ticker) {
      var s = parsed.strongest_setup_today;
      html += '<div class="cmp-ai-block cmp-ai-strongest"><h4>Strongest setup today</h4>'
        + '<p><span class="cmp-ai-pill">' + escapeHtml(s.ticker) + '</span> ' + escapeHtml(s.why || '') + '</p></div>';
    }
    if (parsed.what_invalidates) {
      html += '<div class="cmp-ai-block cmp-ai-risk"><h4>What invalidates this</h4><p>' + escapeHtml(parsed.what_invalidates) + '</p></div>';
    }
    if (Array.isArray(parsed.per_ticker) && parsed.per_ticker.length) {
      html += '<div class="cmp-ai-block"><h4>Per-ticker read</h4><div class="cmp-ai-grid">';
      parsed.per_ticker.forEach(function (p) {
        if (!p || !p.ticker) return;
        html += '<div class="cmp-ai-card">'
          + '<div class="cmp-ai-card-head">' + escapeHtml(p.ticker) + '</div>'
          + (p.bull ? '<div class="cmp-ai-bull"><strong>Bull:</strong> ' + escapeHtml(p.bull) + '</div>' : '')
          + (p.bear ? '<div class="cmp-ai-bear"><strong>Bear:</strong> ' + escapeHtml(p.bear) + '</div>' : '')
          + (p.watch ? '<div class="cmp-ai-watch"><strong>Watch:</strong> ' + escapeHtml(p.watch) + '</div>' : '')
          + '</div>';
      });
      html += '</div></div>';
    }
    html += '</div>';
    return html;
  }

  function render(body, tickers, rows) {
    var saved = loadSaved(tickers);
    var savedHtml = '';
    if (saved && saved.parsed) {
      savedHtml = '<div class="cmp-ai-saved-meta">Last AI read saved ' + new Date(saved.saved_at).toLocaleString() + '. <button type="button" class="btn-link" id="cmp-ai-clear">Clear</button></div>'
        + renderResult(saved.parsed);
    } else if (saved && saved.raw) {
      savedHtml = '<div class="cmp-ai-saved-meta">Last raw output saved ' + new Date(saved.saved_at).toLocaleString() + '. JSON parse failed; raw text shown.</div>'
        + '<pre class="cmp-ai-raw">' + escapeHtml(saved.raw) + '</pre>';
    } else {
      savedHtml = '<div class="cmp-ai-empty">No AI read yet for this comp set. Click <strong>Run AI Read</strong> to generate one.</div>';
    }

    var API = global.SignalAIApi;
    var hasApi = !!(API && API.hasKey && API.hasKey());
    var apiModel = (API && API.getDefaults && API.getDefaults().compareModel) || 'sonar-reasoning-pro';
    var apiBtnHtml = hasApi
      ? '<button type="button" class="btn-sm btn-primary" id="cmp-ai-run-api" title="Call ' + apiModel + ' via your stored API key">Run via API (' + apiModel + ')</button>'
      : '<button type="button" class="btn-sm btn-link" id="cmp-ai-open-settings" title="Set your Perplexity API key to enable one-click AI Read">Set API key for one-click</button>';
    body.innerHTML = (
      '<div class="cmp-ai-shell">'
      + '<div class="cmp-ai-toolbar">'
      + apiBtnHtml
      + '<button type="button" class="btn-sm ' + (hasApi ? 'btn-ghost' : 'btn-primary') + '" id="cmp-ai-run">Open Deep Research (paste-back)</button>'
      + '<button type="button" class="btn-sm btn-ghost" id="cmp-ai-copy">Copy comp-pack prompt</button>'
      + '<span class="cmp-ai-status" id="cmp-ai-status"></span>'
      + '</div>'
      + '<div class="cmp-ai-paste-row">'
      + '<details class="cmp-ai-paste-details" id="cmp-ai-paste-details">'
      + '<summary>Paste Deep Research result to save</summary>'
      + '<textarea class="cmp-ai-paste" id="cmp-ai-paste" rows="6" placeholder="Paste the ```json ... ``` block returned by Deep Research here."></textarea>'
      + '<div class="cmp-ai-paste-actions">'
      + '<button type="button" class="btn-sm btn-primary" id="cmp-ai-save">Save AI read</button>'
      + '<span class="cmp-ai-paste-hint" id="cmp-ai-paste-hint"></span>'
      + '</div>'
      + '</details>'
      + '</div>'
      + '<div class="cmp-ai-content" id="cmp-ai-content">' + savedHtml + '</div>'
      + '<div class="cmp-ai-footer">AI-generated synthesis. Numbers in the comp pack come from the local tickerData snapshot; the narrative is the LLM\u2019s read. Cross-check before acting.</div>'
      + '</div>'
    );

    var runBtn = body.querySelector('#cmp-ai-run');
    var runApiBtn = body.querySelector('#cmp-ai-run-api');
    var openSettingsBtn = body.querySelector('#cmp-ai-open-settings');
    var copyBtn = body.querySelector('#cmp-ai-copy');
    var status = body.querySelector('#cmp-ai-status');
    var saveBtn = body.querySelector('#cmp-ai-save');
    var pasteArea = body.querySelector('#cmp-ai-paste');
    var pasteHint = body.querySelector('#cmp-ai-paste-hint');
    var clearBtn = body.querySelector('#cmp-ai-clear');
    var pasteDetails = body.querySelector('#cmp-ai-paste-details');

    if (openSettingsBtn && global.ApiSettings) {
      openSettingsBtn.addEventListener('click', function () { global.ApiSettings.open(); });
    }

    if (runApiBtn) {
      runApiBtn.addEventListener('click', function () {
        if (!API || !API.hasKey()) { setStatus('No API key. Open Settings.', 'error'); return; }
        runApiBtn.disabled = true;
        var prevLabel = runApiBtn.textContent;
        runApiBtn.textContent = 'Calling ' + apiModel + '...';
        setStatus('Calling Perplexity API (' + apiModel + ', ~10-30s)...', '');
        var prompt = buildPrompt(tickers, rows);
        var responseFormat = null;
        // sonar-reasoning-pro + sonar-pro support response_format; sonar-deep-research does not.
        if (apiModel === 'sonar-pro' || apiModel === 'sonar-reasoning-pro' || apiModel === 'sonar') {
          // Use json_schema-like loose hint; doc supports response_format: {type:"json_schema", ...}
          // For broad compatibility we omit response_format and rely on the prompt's fenced JSON contract.
          responseFormat = null;
        }
        var defaults = (API.getDefaults && API.getDefaults()) || {};
        API.call({
          model: apiModel,
          system: 'You are a buy-side equity analyst. Return only valid JSON inside a single ```json fenced block. No prose outside the block.',
          prompt: prompt,
          max_tokens: 2200,
          reasoning_effort: defaults.defaultEffort,
          response_format: responseFormat
        }).then(function (res) {
          runApiBtn.disabled = false;
          runApiBtn.textContent = prevLabel;
          if (!res.ok) {
            setStatus(res.error, 'error');
            return;
          }
          var parsed = tryParse(res.content) || (API.parseJsonLoose && API.parseJsonLoose(res.content));
          saveResult(tickers, {
            raw: res.content,
            parsed: parsed,
            source: 'api',
            model: res.model,
            usage: res.usage,
            cost_estimate: res.cost_estimate
          });
          var costStr = API.formatCost(res.cost_estimate);
          var usageStr = (res.usage && res.usage.completion_tokens) ? (res.usage.completion_tokens + ' tok') : '';
          setStatus('Done. ' + res.model + ' — ' + costStr + (usageStr ? ' (' + usageStr + ')' : ''), 'ok');
          var content = body.querySelector('#cmp-ai-content');
          if (content) {
            content.innerHTML = parsed
              ? '<div class="cmp-ai-saved-meta">' + res.model + ' &middot; ' + new Date().toLocaleString() + ' &middot; est. ' + costStr + '. <button type="button" class="btn-link" id="cmp-ai-clear">Clear</button></div>' + renderResult(parsed)
              : '<div class="cmp-ai-saved-meta">' + res.model + ' &middot; ' + new Date().toLocaleString() + ' &middot; est. ' + costStr + ' &middot; JSON parse failed. <button type="button" class="btn-link" id="cmp-ai-clear">Clear</button></div><pre class="cmp-ai-raw">' + escapeHtml(res.content) + '</pre>';
            var cb = content.querySelector('#cmp-ai-clear');
            if (cb) cb.addEventListener('click', function () {
              localStorage.removeItem(STORAGE_PREFIX + compKey(tickers));
              render(body, tickers, rows);
            });
          }
        });
      });
    }

    function setStatus(msg, cls) {
      if (!status) return;
      status.textContent = msg || '';
      status.className = 'cmp-ai-status' + (cls ? ' cmp-ai-status-' + cls : '');
    }

    runBtn.addEventListener('click', function () {
      // Open the Deep Research tab IMMEDIATELY inside the click gesture so
      // popup blockers don't kick in (mirrors drilldown-surface.js pattern).
      var url = buildDeepResearchUrl(tickers);
      var newWin = null;
      try { newWin = global.open(url, '_blank', 'noopener'); } catch (_) {}
      var prompt = buildPrompt(tickers, rows);
      copyToClipboard(prompt).then(function (ok) {
        if (ok) {
          setStatus('Prompt copied. Paste it as your first message in the Deep Research tab.', 'ok');
        } else {
          setStatus('Clipboard blocked. Use "Copy comp-pack prompt" and paste manually.', 'error');
        }
        if (pasteDetails) pasteDetails.open = true;
      });
      if (!newWin) setStatus('Popup blocked. Allow pop-ups, then click again.', 'error');
    });

    copyBtn.addEventListener('click', function () {
      copyToClipboard(buildPrompt(tickers, rows)).then(function (ok) {
        setStatus(ok ? 'Prompt copied to clipboard.' : 'Clipboard blocked.', ok ? 'ok' : 'error');
      });
    });

    saveBtn.addEventListener('click', function () {
      var txt = (pasteArea && pasteArea.value || '').trim();
      if (!txt) {
        if (pasteHint) { pasteHint.textContent = 'Paste the Deep Research output first.'; pasteHint.className = 'cmp-ai-paste-hint error'; }
        return;
      }
      var parsed = tryParse(txt);
      var entry = saveResult(tickers, { raw: txt, parsed: parsed });
      if (!entry) { pasteHint.textContent = 'Save failed (storage disabled).'; pasteHint.className = 'cmp-ai-paste-hint error'; return; }
      pasteHint.textContent = parsed ? 'Saved.' : 'Saved (raw — JSON parse failed; check formatting).';
      pasteHint.className = 'cmp-ai-paste-hint ' + (parsed ? 'ok' : 'warn');
      // Re-render the content area
      var content = body.querySelector('#cmp-ai-content');
      if (content) {
        content.innerHTML = parsed
          ? '<div class="cmp-ai-saved-meta">Saved ' + new Date().toLocaleString() + '. <button type="button" class="btn-link" id="cmp-ai-clear">Clear</button></div>' + renderResult(parsed)
          : '<div class="cmp-ai-saved-meta">Saved (raw). Could not parse JSON. <button type="button" class="btn-link" id="cmp-ai-clear">Clear</button></div><pre class="cmp-ai-raw">' + escapeHtml(txt) + '</pre>';
        var cb = content.querySelector('#cmp-ai-clear');
        if (cb) cb.addEventListener('click', function () {
          localStorage.removeItem(STORAGE_PREFIX + compKey(tickers));
          render(body, tickers, rows);
        });
      }
      if (pasteArea) pasteArea.value = '';
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        localStorage.removeItem(STORAGE_PREFIX + compKey(tickers));
        render(body, tickers, rows);
      });
    }
  }

  // ------------------------- TINY FORMATTERS -------------------------
  function fmtN(v, d) {
    if (v == null || Number.isNaN(v)) return 'n/a';
    return Number(v).toFixed(d == null ? 2 : d);
  }
  function fmtPct(v) {
    if (v == null || Number.isNaN(v)) return 'n/a';
    return Number(v).toFixed(1) + '%';
  }
  function fmtMult(v) {
    if (v == null || Number.isNaN(v)) return 'n/a';
    return Number(v).toFixed(1) + 'x';
  }
  function fmtLarge(v) {
    if (v == null || Number.isNaN(v)) return 'n/a';
    var a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(2) + 'T';
    if (a >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    return Number(v).toFixed(0);
  }
  function truncate(s, n) {
    if (!s) return '';
    s = String(s);
    return s.length <= n ? s : s.slice(0, n - 1) + '\u2026';
  }
  function getIntel(t) {
    var d = global._earningsIntelData || global.earningsIntelData;
    return d && d.tickers && d.tickers[t] ? d.tickers[t] : null;
  }

  global.SignalCompareAI = {
    render: render,
    buildPrompt: buildPrompt,
    buildCompPack: buildCompPack,
    loadSaved: loadSaved,
  };
})(window);
