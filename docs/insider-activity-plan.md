# Insider Activity — Implementation Plan

Status: planning only. No production UI behavior changes. The Coverage ribbon
button `data-flag="insider_buy"` remains inert (`disabled=true`,
`title="Requires insider_activity snapshot"`) until this plan ships.

This document specifies the snapshot file, schema, upstream data source
options, filtering rule, where the generation job lives in this repo's
automation flow, and the frontend hook needed to enable the flag.

---

## 1. Pipeline context (current state)

The Coverage ribbon flag controller (`shell.js → window.SignalCoverageFilters`)
already loads `data-snapshot.json` once at boot via
`window.SignalSnapshot.fetchWithFallback('data-snapshot.json', { cacheBust: true })`
and caches `snap.estimates` and `snap.short_interest`. Each existing flag is a
pure predicate over those maps — for example:

- `revision_up`: `e.revisionsUp7d > 0 || e.revisionsUp30d > 0 || e.fy1RevisionsUp30d > 0`
- `crowded_short`: `si.current?.shortPercentOfFloat >= 10`

Snapshot writers run inside the daily refresh under `automation/jobs/daily_refresh.py`.
The relevant Node refreshers are at the repo root and merge keyed-by-ticker
maps into `data-snapshot.json` (see `refresh_deep_dive.mjs` line 376–377 for
the merge pattern). New snapshot data must follow the same pattern.

The `insider_buy` predicate will follow the same shape as `revision_up` /
`crowded_short`: load a per-ticker map at boot, evaluate a synchronous
predicate per ticker. Therefore we need:

1. A new ticker-keyed JSON snapshot file: `insider_activity.json`.
2. A generator job that fetches and normalizes SEC Form 4 data.
3. A small frontend extension to the existing controller.

We deliberately use a **separate** snapshot file (not a new key inside
`data-snapshot.json`) because:

- Insider data has a different cadence (daily, but driven by SEC filings, not
  market data) and different upstream source than `estimates` /
  `short_interest`.
- Keeping it standalone allows the snapshot to be hot-swapped or rebuilt
  without touching the larger `data-snapshot.json` (currently ~1MB).
- Backfilling Form 4 history is heavier than the rest of the snapshot and
  benefits from independent versioning.

---

## 2. Proposed snapshot file: `insider_activity.json`

Location: repo root, alongside `data-snapshot.json` and `earnings_intel.json`.

### 2.1 Schema (ticker-keyed, JSON Schema 2020-12)

```jsonc
{
  "schema_version": "1.0",
  "generated_at": "2026-04-27T11:45:00-04:00",
  "lookback_days": 90,
  "source": "sec_form4_normalized",
  "tickers": {
    "AAPL": {
      "ticker": "AAPL",
      "as_of": "2026-04-25",            // most recent transaction date considered
      "lookback_days": 90,              // window used for the rollup below

      // Rollup of NON-DERIVATIVE OPEN-MARKET PURCHASES only (P transactions).
      // Sales (S), gifts (G), tax withholdings (F), exercises (M), and any
      // derivative (Table II) rows are EXCLUDED from these counts.
      "buys_30d":  { "count": 0, "filers": 0, "shares": 0,      "notional_usd": 0 },
      "buys_90d":  { "count": 1, "filers": 1, "shares": 5000,   "notional_usd": 925000 },
      "buys_180d": { "count": 2, "filers": 2, "shares": 8500,   "notional_usd": 1540000 },

      // Sells included for context / future flags (e.g. cluster_sell).
      "sells_90d": { "count": 4, "filers": 3, "shares": 120000, "notional_usd": 22000000 },

      // Net buy/sell ratio over 90d, in dollars. Positive = net buying.
      "net_buy_usd_90d": -21075000,
      "net_buy_shares_90d": -111500,

      // Cluster signal: distinct insiders who bought open-market in lookback.
      // This is what most institutional desks key off of.
      "distinct_buyers_90d": 1,
      "distinct_sellers_90d": 3,

      // Whether any C-suite (CEO/CFO/COO/President) or 10% holder bought.
      "officer_buyer_90d": false,
      "director_buyer_90d": true,
      "ten_percent_holder_buyer_90d": false,

      // Up to 10 most recent NON-DERIVATIVE OPEN-MARKET buys for popup detail.
      // `role` uses the SEC reporting-relationship enum we normalize to.
      "recent_buys": [
        {
          "filed_at": "2026-04-22",
          "transaction_date": "2026-04-19",
          "filer_name": "Smith John",
          "filer_cik": "0001234567",
          "role": "director",            // director | officer | ten_percent | other
          "officer_title": null,         // populated if role=officer
          "shares": 5000,
          "price_per_share": 185.00,
          "notional_usd": 925000,
          "post_transaction_shares": 12345,
          "form_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000045/0000320193-26-000045-index.htm"
        }
      ]
    }
  },

  // Tickers we attempted but found no data for, with reason. Lets us
  // distinguish "no insider activity in window" from "fetch failed".
  "missing": {
    "FOO": { "reason": "no_form4_filings_in_window" },
    "BAR": { "reason": "issuer_cik_not_found" }
  }
}
```

### 2.2 Field semantics & invariants

- `tickers` is the only map the frontend reads. All counts are **non-negative
  integers**; dollar fields are integers (rounded to nearest dollar).
- `notional_usd` for each transaction = `shares * price_per_share`. Where SEC
  filing reports a price range, use the weighted average reported on the form.
- A "buy" means **Table I (non-derivative) Code P (open-market purchase)**.
  Code M (option exercise), Code A (grant), Code F (tax-withholding sale),
  Code G (gift), Code J (other), Code S (open-market sale) are **excluded**
  from `buys_*`.
- `filers` counts distinct CIKs; `count` counts transaction rows.
- `as_of` is the latest `transaction_date` we incorporated, NOT generation
  time. This lets the frontend show "last insider buy 5d ago".

### 2.3 Versioning

- `schema_version` is mandatory. Frontend must guard with
  `if (!snap.schema_version || snap.schema_version.split('.')[0] !== '1') ...`.
- Schema-breaking changes bump the major version. Additive fields do not.

---

## 3. Upstream source options (recommended order)

The data source must produce **normalized SEC Form 4** rows: each row already
parsed to `transaction_code`, `is_derivative`, `shares`, `price_per_share`,
`reporting_owner_cik`, `relationship` (officer/director/10pct), and
`transaction_date`. Raw EDGAR XML parsing is a fallback, not the recommended
path.

### Option A (recommended): SEC EDGAR full-text + on-repo Form 4 parser

- Source of truth: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&CIK={cik}&dateb=&owner=include&count=40`
  for filing index, then per-filing primary doc XML.
- Parse Form 4 XML (`<nonDerivativeTransaction>` / `<derivativeTransaction>`,
  `<transactionCoding><transactionCode>`, `<transactionAmounts>`).
- Pros: free, authoritative, no rate-limit beyond SEC fair-use (10 req/sec
  with proper `User-Agent: SignalAI/1.0 contact@example.com` header), no
  vendor lock-in.
- Cons: must implement an XML parser, handle amended filings (Form 4/A),
  handle multiple-line transactions, and maintain a ticker→issuer-CIK map
  (we already have this implicitly via Yahoo metadata; we'd need to add a
  one-time lookup table).
- This is the **recommended primary path**. Form 4 is structurally simple
  and the SEC schema is stable.

### Option B: Financial Modeling Prep `/v4/insider-trading`

- Endpoint: `https://financialmodelingprep.com/api/v4/insider-trading?symbol={TICKER}&apikey=...`
- Returns pre-normalized rows including `transactionType` ("P-Purchase",
  "S-Sale"), `securitiesTransacted`, `price`, `reportingName`,
  `typeOfOwner`.
- Pros: zero parsing, ticker-keyed (no CIK lookup), single HTTP call.
- Cons: paid tier ($30+/mo) for usable rate limits; vendor-lock; we'd
  re-introduce an external API key requirement that the daily refresh has
  been deliberately moving away from.
- Acceptable as a **bootstrap** while the EDGAR parser is being built and
  validated.

### Option C: OpenInsider scraper

- `http://openinsider.com/screener?...` returns CSV of filtered Form 4
  transactions including price, shares, role.
- Pros: free, already filtered to non-derivative trades, includes
  cluster-buy view.
- Cons: third-party scrape; site terms restrict automated use; outages
  break the pipeline; not authoritative.
- **Not recommended** for production. Useful for spot-checking Option A.

### Option D: Polygon.io `/vX/reference/insider-transactions`

- Pros: clean schema, bundled with their existing market-data tier.
- Cons: paid, partial coverage of older filings, 10% holder data
  inconsistent.
- **Defer** unless we already have a Polygon contract for other features.

### Decision

Build against **Option A (SEC EDGAR + on-repo parser)** as the primary
generator. Keep the parser modular so Option B can be plugged in as a
fallback or backfill source if EDGAR is unreachable.

---

## 4. Filtering rule for `insider_buy`

The Coverage ribbon flag is a **single-ticker boolean predicate** evaluated
client-side, mirroring `revision_up` / `crowded_short`.

### 4.1 Default rule (v1)

A ticker satisfies `insider_buy` if **all** of:

1. The ticker exists in `insider_activity.json → tickers`.
2. `buys_90d.count >= 1`.
3. `distinct_buyers_90d >= 1` (redundant with #2 in v1, kept for parity with
   v2 cluster threshold).
4. `net_buy_usd_90d > 0` — net dollar flow is positive over the lookback.

This codifies "recent non-derivative open-market purchase, with no offsetting
larger sells". The `net_buy_usd_90d > 0` guard avoids flagging a ticker where
a director bought $100k while the CEO sold $50M.

### 4.2 Stricter rule (v2, behind a setting)

For an "Institutional cluster buy" mode (future), require:

- `distinct_buyers_90d >= 2` (cluster signal), **OR**
- `officer_buyer_90d === true || ten_percent_holder_buyer_90d === true`
  (high-signal filer), **AND**
- `buys_90d.notional_usd >= 250000` (filters out token grants).

v2 is configuration on the controller, not a separate flag, so the ribbon
button stays single-purpose.

### 4.3 Frontend predicate (target implementation)

In `shell.js → SignalCoverageFilters._matchesFlag`:

```js
if (flag === 'insider_buy') {
  const ia = _insiderActivity[ticker];
  if (!ia) return false;
  return (ia.buys_90d?.count >= 1)
      && (ia.distinct_buyers_90d >= 1)
      && (ia.net_buy_usd_90d > 0);
}
```

`_insiderActivity` is populated identically to `_estimates` /
`_shortInterest`, by an additional `fetchWithFallback('insider_activity.json',
{ cacheBust: true })` call inside `_loadSnapshot()`.

---

## 5. Where the generation job lives

### 5.1 New module

```
automation/jobs/insider_activity.py
```

Public entry point:

```python
def run(tickers: list[str], lookback_days: int = 90) -> dict:
    """Build insider_activity.json for the given tickers."""
```

Internal layout:

```
automation/jobs/insider_activity.py        # orchestrator
automation/shared/sec_edgar.py             # NEW: EDGAR client (rate-limited fetch + Form 4 XML parse)
automation/shared/insider_normalize.py     # NEW: Form 4 row → normalized record + rollups
```

Why a Python job (not a `.mjs`): the existing Yahoo-based refreshers are
Node because they reuse a `@supabase/supabase-js` client; our other parser /
normalizer logic (`automation/jobs/earnings_events.py`,
`pre_earnings_notes.py`, etc.) is Python. SEC parsing fits the Python side
because we want `lxml`/`xml.etree` and clean test coverage, neither of which
is needed for the snapshot upsert.

### 5.2 Hook into `daily_refresh.py`

Add a step **between Step 1 (market data) and Step 2 (macro)** so insider
data is fresh before any downstream notes that may want to reference it,
but does not block the existing critical path:

```python
def step_insider_activity():
    """Step 1b: Insider activity (no LLM). Fetches Form 4 filings, normalizes,
    writes insider_activity.json. Failure here MUST NOT block the rest of
    the daily refresh — log loudly and continue."""
    print("\n--- Step 1b: Insider activity refresh ---")
    try:
        from automation.jobs.insider_activity import run as run_insider
        from automation.shared.tickers import load_tickers
        tickers = load_tickers()
        result = run_insider(tickers, lookback_days=90)
        print(f"  Insider activity: {result['written']} tickers, {result['skipped']} skipped")
    except Exception as e:
        # Fail loudly in workflow but do not abort daily refresh.
        print(f"  [WARN] insider_activity step failed: {e}")
```

Wired into `run()`:

```python
step_market_data()
step_insider_activity()    # NEW
step_macro_refresh()
...
```

### 5.3 Push to git + Supabase

- The daily refresh's commit step (`git add data/ ...`) must be extended to
  include `insider_activity.json` so GitHub Pages serves the latest snapshot.
- A new Supabase table `insider_activity` (columns: `ticker text pk`,
  `as_of date`, `buys_90d_count int`, `buys_90d_notional bigint`,
  `distinct_buyers_90d int`, `net_buy_usd_90d bigint`,
  `officer_buyer_90d bool`, `payload jsonb`, `updated_at timestamptz`).
  Push happens in the same `step_supabase_push` hook used by other tables.
  Supabase push is **optional for v1**; the JSON snapshot on Pages is the
  primary serving path because the frontend already reads JSON snapshots
  via `SignalSnapshot.fetchWithFallback`.

### 5.4 Rate limiting & politeness

EDGAR fair-use: 10 req/sec, with a descriptive `User-Agent`. The client in
`automation/shared/sec_edgar.py` must:

- Set `User-Agent: SignalAI/1.0 (contact: wtl2111@columbia.edu)`.
- Sleep at least 100ms between requests.
- Cache filing index responses for 6h in `automation/shared/cache.py` to
  avoid re-fetching unchanged ticker filings between weekday runs.
- Tolerate per-ticker errors and continue (record in `missing`).

---

## 6. Frontend hook to enable `data-flag="insider_buy"`

The current ribbon button in `index.html`:

```html
<button type="button" class="ribbon-flag" data-flag="insider_buy">Insider buy</button>
```

is set inert by `SignalCoverageFilters.init()` in `shell.js`:

```js
if (flag === 'insider_buy') {
  btn.disabled = true;
  btn.setAttribute('title', 'Requires insider_activity snapshot');
  btn.classList.add('disabled');
  return;
}
```

### 6.1 Activation diff (when snapshot ships)

1. Inside `_loadSnapshot()`, add a second fetch:

   ```js
   const iaResp = await window.SignalSnapshot.fetchWithFallback(
     'insider_activity.json', { cacheBust: true }
   );
   if (iaResp && iaResp.ok) {
     const ia = await iaResp.json();
     if (ia && ia.schema_version && ia.schema_version.split('.')[0] === '1') {
       _insiderActivity = ia.tickers || {};
     } else {
       console.warn('[SignalCoverageFilters] insider_activity schema mismatch', ia?.schema_version);
     }
   }
   ```

2. Add the predicate to `_matchesFlag` (see §4.3).

3. Remove the inert branch in `init()` for `insider_buy`. The button becomes
   a normal toggle.

4. Add a tooltip when hovering the button — `Insiders made open-market
   purchases in the last 90 days (net positive flow)` — by setting
   `btn.setAttribute('title', ...)` in `init()`.

5. Bump cache-buster on `shell.js` in `index.html`
   (`?v=20260427a` → next date code).

### 6.2 Popup integration (later, not part of `insider_buy` flag)

The per-ticker popup can render a small "Insider activity (90d)" panel from
`insider_activity.json → tickers[T].recent_buys` once the snapshot is
shipping. This is independent of the ribbon flag and can be a follow-up
ticket.

### 6.3 Fail-safe

If `insider_activity.json` is missing / 404 / schema mismatch, the flag
**must** revert to the current inert state at runtime: no button toggle, no
filtering. Concretely: if `_insiderActivity` is `null` after `_loadSnapshot`,
re-apply the disabled treatment in a post-load step. This preserves the
"fail loudly in workflows, silently in UX" rule.

---

## 7. Acceptance criteria for the implementation ticket

When this plan is executed, the following must hold:

1. `insider_activity.json` is written by the daily refresh and committed to
   the repo (visible at `https://wtlittle.github.io/signalai/insider_activity.json`).
2. Schema matches §2.1, validated by a JSON Schema in `docs/schemas/insider_activity.schema.json`.
3. `automation/jobs/insider_activity.py` has unit tests covering the Form 4
   parser (purchases vs sales vs derivatives vs amendments).
4. The Coverage ribbon `Insider buy` button toggles, filters correctly, and
   combines with other flags via the existing AND logic in
   `SignalCoverageFilters.getVisibleTickers`.
5. KPI tiles (Median EV/Sales, Avg 1M move, Earnings 7D, Debate Intensity)
   recompute against the filtered list when `insider_buy` is active —
   already wired generically in `app.js → updateCoverageSummaryTiles`, no
   change needed.
6. Empty-state row "No names match the active flags." renders when an
   `insider_buy` AND-combination yields zero matches — already wired in
   `app.js → renderTable`, no change needed.
7. If the snapshot is missing or malformed, the button reverts to inert and
   no console errors are emitted from filter evaluation.

---

## 8. Out of scope (explicit non-goals for v1)

- 13F-based institutional flows (different cadence, different filing).
- Insider sales as a flag. Sells are captured for context but no `insider_sell`
  ribbon button is planned in v1.
- Real-time / intraday Form 4 alerts (the daily-refresh cadence is sufficient
  for a 1–3 year horizon buy-side analyst).
- Backfill beyond 180 days. The snapshot is rolling-window; longer history
  belongs in Supabase, not the JSON snapshot.
