/* ===== SCREENER-PRESETS.JS — Save/Load named screens ============== */
/*
 * Surfaces a lightweight toolbar just above the screener panel. Users can:
 *   - Save the current filter set under a name
 *   - Select a saved preset from the dropdown to load it
 *   - Delete the currently selected preset
 *
 * Storage strategy:
 *   - Remote: Supabase REST (`saved_screens` table) if SUPABASE_URL and an
 *     anon key are available on window. Service key is NEVER hardcoded here.
 *   - Local fallback: localStorage under key `ss_saved_screens` (key per
 *     spec). Used when Supabase unreachable or misconfigured.
 *
 * Failure mode: fail *loudly* in the save/load workflow via status text.
 *               UI silently degrades to local-only if Supabase is disabled.
 */
(function () {
  'use strict';

  if (typeof window === 'undefined') return;
  if (!window.SignalScreener) {
    console.warn('[screener-presets] SignalScreener API not ready; skipping init');
    return;
  }

  var LOCAL_KEY = 'ss_saved_screens';
  var TABLE = 'saved_screens';

  // ---------- Supabase wiring (optional) ----------
  // Read config if the app exposes it (the app doesn't currently ship a
  // browser-safe anon key, so saves route to localStorage unless the user
  // sets window.SUPABASE_URL / window.SUPABASE_ANON_KEY explicitly).
  function supabaseConfig() {
    var url = window.SUPABASE_URL || null;
    var key = window.SUPABASE_ANON_KEY || null;
    if (!url || !key) return null;
    return { url: url.replace(/\/$/, ''), key: key };
  }

  async function remoteList() {
    var cfg = supabaseConfig();
    if (!cfg) return null;
    var resp = await fetch(cfg.url + '/rest/v1/' + TABLE + '?select=id,name,filters,updated_at&order=name.asc', {
      headers: {
        apikey: cfg.key,
        Authorization: 'Bearer ' + cfg.key,
        'Content-Type': 'application/json'
      }
    });
    if (!resp.ok) throw new Error('Supabase list failed (HTTP ' + resp.status + ')');
    return await resp.json();
  }

  async function remoteUpsert(name, filters) {
    var cfg = supabaseConfig();
    if (!cfg) return null;
    var body = [{ name: name, filters: filters, updated_at: new Date().toISOString() }];
    var resp = await fetch(cfg.url + '/rest/v1/' + TABLE + '?on_conflict=name', {
      method: 'POST',
      headers: {
        apikey: cfg.key,
        Authorization: 'Bearer ' + cfg.key,
        'Content-Type': 'application/json',
        Prefer: 'resolution=merge-duplicates,return=representation'
      },
      body: JSON.stringify(body)
    });
    if (!resp.ok) throw new Error('Supabase upsert failed (HTTP ' + resp.status + ')');
    return await resp.json();
  }

  async function remoteDelete(name) {
    var cfg = supabaseConfig();
    if (!cfg) return null;
    var resp = await fetch(cfg.url + '/rest/v1/' + TABLE + '?name=eq.' + encodeURIComponent(name), {
      method: 'DELETE',
      headers: { apikey: cfg.key, Authorization: 'Bearer ' + cfg.key }
    });
    if (!resp.ok) throw new Error('Supabase delete failed (HTTP ' + resp.status + ')');
    return true;
  }

  // ---------- Local fallback ----------
  function localList() {
    try {
      var raw = localStorage.getItem(LOCAL_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      console.warn('[screener-presets] local parse failed', e);
      return [];
    }
  }
  function localWrite(list) {
    try { localStorage.setItem(LOCAL_KEY, JSON.stringify(list)); }
    catch (e) { console.warn('[screener-presets] local write failed', e); }
  }
  function localUpsert(name, filters) {
    var list = localList();
    var idx = list.findIndex(function (p) { return p.name === name; });
    var entry = { name: name, filters: filters, updated_at: new Date().toISOString() };
    if (idx >= 0) list[idx] = entry; else list.push(entry);
    list.sort(function (a, b) { return a.name.localeCompare(b.name); });
    localWrite(list);
    return entry;
  }
  function localDelete(name) {
    var list = localList().filter(function (p) { return p.name !== name; });
    localWrite(list);
  }

  // ---------- Unified API with graceful Supabase→local fallback ----------
  async function listPresets() {
    try {
      var r = await remoteList();
      if (r) return { source: 'remote', presets: r };
    } catch (e) {
      console.warn('[screener-presets] remote list failed; falling back', e);
    }
    return { source: 'local', presets: localList() };
  }
  async function savePreset(name, filters) {
    try {
      var r = await remoteUpsert(name, filters);
      if (r) {
        // Mirror to local cache so offline reload still works
        localUpsert(name, filters);
        return { source: 'remote' };
      }
    } catch (e) {
      console.warn('[screener-presets] remote save failed; saving locally', e);
    }
    localUpsert(name, filters);
    return { source: 'local' };
  }
  async function deletePreset(name) {
    try {
      var r = await remoteDelete(name);
      if (r) { localDelete(name); return { source: 'remote' }; }
    } catch (e) {
      console.warn('[screener-presets] remote delete failed; deleting locally', e);
    }
    localDelete(name);
    return { source: 'local' };
  }

  // ---------- UI ----------
  function init() {
    // Mount above the screener-panel, inside the screener-bar
    var panel = document.getElementById('screener-panel');
    if (!panel) {
      // Wait one tick in case screener.js injected later
      return setTimeout(init, 120);
    }

    var bar = document.createElement('div');
    bar.className = 'screener-save-bar';
    bar.id = 'screener-save-bar';
    bar.innerHTML = [
      '<label>Preset</label>',
      '<select class="screener-save-select" id="ss-preset-select">',
      '  <option value="">— Select saved screen —</option>',
      '</select>',
      '<button type="button" class="btn-sm btn-ghost" id="ss-preset-load">Load</button>',
      '<button type="button" class="btn-sm btn-ghost" id="ss-preset-delete" title="Delete selected">Delete</button>',
      '<span style="color:var(--text-muted); padding:0 6px;">|</span>',
      '<input class="screener-save-input" id="ss-preset-name" type="text" placeholder="Name this screen…" maxlength="80">',
      '<button type="button" class="btn-sm btn-primary" id="ss-preset-save">Save</button>',
      '<span class="screener-save-status" id="ss-preset-status"></span>'
    ].join('');

    // Insert at top of panel
    panel.insertBefore(bar, panel.firstChild);

    var selectEl = document.getElementById('ss-preset-select');
    var nameEl   = document.getElementById('ss-preset-name');
    var statusEl = document.getElementById('ss-preset-status');

    function setStatus(msg, kind) {
      statusEl.textContent = msg || '';
      statusEl.style.color = kind === 'error' ? 'var(--red)'
                           : kind === 'ok'    ? 'var(--green)'
                           : 'var(--text-muted)';
      if (msg) setTimeout(function () {
        if (statusEl.textContent === msg) setStatus('');
      }, 3200);
    }

    async function refreshList() {
      try {
        var r = await listPresets();
        selectEl.innerHTML = '<option value="">— Select saved screen —</option>';
        (r.presets || []).forEach(function (p) {
          var opt = document.createElement('option');
          opt.value = p.name;
          opt.textContent = p.name + (r.source === 'local' ? ' (local)' : '');
          selectEl.appendChild(opt);
        });
      } catch (e) {
        setStatus('Failed to list presets: ' + (e.message || e), 'error');
      }
    }

    document.getElementById('ss-preset-save').addEventListener('click', async function () {
      var name = (nameEl.value || '').trim();
      if (!name) { setStatus('Enter a name first', 'error'); return; }
      var filters = window.SignalScreener.getFilters();
      if (!filters.length) { setStatus('No active filters to save', 'error'); return; }
      setStatus('Saving…');
      try {
        var r = await savePreset(name, filters);
        setStatus('Saved' + (r.source === 'local' ? ' (local)' : ''), 'ok');
        nameEl.value = '';
        await refreshList();
        selectEl.value = name;
      } catch (e) {
        setStatus('Save failed: ' + (e.message || e), 'error');
      }
    });

    document.getElementById('ss-preset-load').addEventListener('click', async function () {
      var name = selectEl.value;
      if (!name) { setStatus('Pick a preset', 'error'); return; }
      setStatus('Loading…');
      try {
        var r = await listPresets();
        var p = (r.presets || []).find(function (x) { return x.name === name; });
        if (!p) { setStatus('Preset not found', 'error'); return; }
        window.SignalScreener.loadFilters(p.filters || []);
        setStatus('Loaded "' + name + '"', 'ok');
      } catch (e) {
        setStatus('Load failed: ' + (e.message || e), 'error');
      }
    });

    document.getElementById('ss-preset-delete').addEventListener('click', async function () {
      var name = selectEl.value;
      if (!name) { setStatus('Pick a preset to delete', 'error'); return; }
      if (!confirm('Delete preset "' + name + '"?')) return;
      setStatus('Deleting…');
      try {
        await deletePreset(name);
        setStatus('Deleted', 'ok');
        await refreshList();
      } catch (e) {
        setStatus('Delete failed: ' + (e.message || e), 'error');
      }
    });

    // Initial list load (non-blocking)
    refreshList();

    // Mount the regime preset packs row below the save bar.
    mountRegimePresetPacks(panel);
  }

  // ============================================================
  // REGIME PRESET PACKS
  // Curated screens that pair with the macro idea engine. The pack
  // matching the current regime is highlighted as "Suggested".
  // ============================================================
  var REGIME_PRESET_PACKS = [
    {
      id: 'reflation-long',
      name: 'Reflation Long Book',
      blurb: 'Cyclicals + commodities with positive 1M alpha and regime-favored factor exposure.',
      regimes: ['Reflation', 'Goldilocks'],
      filters: [
        { key: 'subsector', type: 'select', values: ['Oil & Gas E&P', 'Oilfield Services', 'Diversified Energy', 'Refining', 'Industrial Conglomerates', 'Aerospace & Defense', 'Machinery', 'Construction Materials', 'Chemicals', 'Mining & Metals', 'Banks', 'Capital Markets'] },
        { key: 'alpha_1m', type: 'range', min: 0, max: '' },
        { key: 'regime_factor_score', type: 'range', min: 60, max: '' },
        { key: 'earnings_momentum_tag', type: 'select', values: ['tailwind', 'inflecting', 'watching'] }
      ]
    },
    {
      id: 'restrictive-short',
      name: 'Restrictive Short Book',
      blurb: 'Rate-sensitive growth names with negative alpha and stretched value-for-growth.',
      regimes: ['Restrictive', 'Stagflation', 'Risk-Off'],
      filters: [
        { key: 'subsector', type: 'select', values: ['Application Software', 'Cybersecurity', 'Cloud Infrastructure', 'Software Infrastructure', 'Internet Retail', 'Communication Services', 'Consumer Discretionary'] },
        { key: 'alpha_1m', type: 'range', min: '', max: 0 },
        { key: 'value_for_growth_percentile', type: 'range', min: '', max: 30 },
        { key: 'earnings_momentum_tag', type: 'select', values: ['headwind', 'breaking'] }
      ]
    },
    {
      id: 'stagflation-hedges',
      name: 'Stagflation Hedges',
      blurb: 'Pricing-power energy/materials/staples that bend but do not break in stagflation.',
      regimes: ['Stagflation'],
      filters: [
        { key: 'subsector', type: 'select', values: ['Oil & Gas E&P', 'Refining', 'Diversified Energy', 'Mining & Metals', 'Chemicals', 'Agricultural Inputs', 'Tobacco', 'Beverages', 'Household Products', 'Packaged Foods'] },
        { key: 'passthrough_tags', type: 'select', values: ['pricing-power', 'inflation-hedge', 'cycle-resilient', 'oil-beneficiary', 'commodity-pricing'] }
      ]
    },
    {
      id: 'risk-off-defensive',
      name: 'Risk-Off Defensive',
      blurb: 'Quality staples/utilities/healthcare with low debate intensity for capital preservation.',
      regimes: ['Risk-Off', 'Restrictive'],
      filters: [
        { key: 'subsector', type: 'select', values: ['Beverages', 'Household Products', 'Packaged Foods', 'Tobacco', 'Utilities', 'Renewable Utilities', 'Health Care Equipment', 'Pharmaceuticals', 'Managed Care', 'Insurance'] },
        { key: 'qualityScore', type: 'range', min: 70, max: '' },
        { key: 'debateScore', type: 'range', min: '', max: 40 }
      ]
    }
  ];

  function currentRegime() {
    try {
      var mc = window.macroDataCache;
      return (mc && mc.regime && mc.regime.regime) || null;
    } catch (e) { return null; }
  }

  function mountRegimePresetPacks(panel) {
    var bar = document.createElement('div');
    bar.className = 'screener-regime-packs';
    bar.id = 'screener-regime-packs';
    bar.innerHTML = [
      '<div class="regime-packs-header">',
      '  <span class="regime-packs-title">Regime Presets</span>',
      '  <span class="regime-packs-current" id="regime-packs-current"></span>',
      '</div>',
      '<div class="regime-packs-row" id="regime-packs-row"></div>'
    ].join('');
    // Insert after save bar (first child) but before the add-filter row.
    var saveBar = document.getElementById('screener-save-bar');
    if (saveBar && saveBar.nextSibling) {
      panel.insertBefore(bar, saveBar.nextSibling);
    } else {
      panel.insertBefore(bar, panel.firstChild);
    }

    function render() {
      var regime = currentRegime();
      var currentEl = document.getElementById('regime-packs-current');
      if (currentEl) currentEl.textContent = regime ? ('Current regime: ' + regime) : '';
      var row = document.getElementById('regime-packs-row');
      if (!row) return;
      row.innerHTML = '';
      REGIME_PRESET_PACKS.forEach(function (pack) {
        var suggested = regime && pack.regimes.indexOf(regime) >= 0;
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'regime-pack-btn' + (suggested ? ' suggested' : '');
        btn.title = pack.blurb + (suggested ? '\n\nSuggested for ' + regime + ' regime' : '');
        btn.innerHTML = (suggested ? '<span class="regime-pack-badge">SUGGESTED</span>' : '') +
                        '<span class="regime-pack-name">' + pack.name + '</span>';
        btn.addEventListener('click', function () {
          window.SignalScreener.loadFilters(pack.filters);
          var statusEl = document.getElementById('ss-preset-status');
          if (statusEl) {
            statusEl.textContent = 'Loaded pack "' + pack.name + '"';
            statusEl.style.color = 'var(--green)';
            setTimeout(function () {
              if (statusEl.textContent.indexOf(pack.name) >= 0) statusEl.textContent = '';
            }, 3200);
          }
        });
        row.appendChild(btn);
      });
    }

    render();
    // Re-render once macro data loads (so the "Suggested" badge appears).
    if (window.loadMacroData && !window.macroDataCache) {
      try { window.loadMacroData().then(render); } catch (e) { /* noop */ }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
