# QA Pass — 2026-04-27

Scope: verified-real bugs surfaced during the buy-side QA review. Items
in the original review that assumed a React/Vite codebase were skipped
since SignalStack AI is vanilla JS + custom CSS.

## What's now functional

### 1. Earnings Intel renders proper text (was `[object Object]`)
- **Root cause:** `earnings_intel.json` stores bullets as either a plain
  string OR `{signal_id, text}`. The renderer was concatenating the
  object, producing `[object Object]` across 27 tickers (162 bullet
  occurrences) including ADP, AMZN, …
- **Fix:** `earnings-intel.js`
  - New `bulletText(b)` helper handles both shapes.
  - De-duplicate within `pushes_higher` and `pushes_lower` by
    lower-cased trimmed text.
  - Cross-direction de-dup: drop `pushes_lower` items already shown in
    `pushes_higher` (higher wins) so the same point doesn't argue both
    ways.
- **Verified:** AMZN popup → Earnings Intel tab. `[object Object]`
  occurrences in the panel = 0; real Bull / Bear / Base bullets render.

### 2. Drilldown popup price matches Coverage row
- **Root cause:** Popup was re-parsing quote data via `parseTickerData()`
  which could diverge from the row the user just clicked. Observed for
  GOOG.
- **Fix:** `popup.js` now overrides `data.price`, `data.d1`, and
  `data.name` from `tickerData[ticker]` (the row data) after parse.
  Row-as-source-of-truth.
- **Verified:** GOOG row $337.75 == popup $337.75.

### 3. Bank EV / EV-Sales / EV-FCF cells show "n/a"
- **Root cause:** Banks fund themselves with deposits + short-term
  borrowings. The `marketCap + totalDebt − totalCash` math doesn't carry
  the same meaning as for non-financial corporates, so EV << marketCap
  for JPM, BAC, WFC, MS, GS — looked broken.
- **Fix:**
  - `utils.js`: `EV_NON_MEANINGFUL_SUBSECTORS` set (Diversified Banking,
    Investment Banking, Regional Banking, Banking, Banks - Diversified,
    Banks - Regional) plus `isEvNonMeaningfulSubsector()` /
    `isEvNonMeaningfulTicker()` helpers.
  - `app.js`: render `<span class="cell-na">n/a</span>` with a tooltip
    in EV / EV-Sales / EV-FCF cells when subsector is in that set, and
    exclude these tickers from the median FY1 EV/Sales KPI tile.
  - `styles.css`: `.cell-na` (muted, italic).
- **Verified:** JPM, BAC, WFC, MS, GS all render "n/a" with tooltip.

### 4. TSM EV no longer $7.6T (currency-mismatch guard)
- **Root cause:** Yahoo Finance returns `enterpriseValue`,
  `totalRevenue`, `totalCash`, `totalDebt`, `freeCashflow`,
  `operatingCashflow` in the REPORTING currency (TWD for TSMC), while
  `marketCap` and `price` are in the LISTING currency (USD). Mixing
  TWD + USD produced EV $7.6T.
- **Fix (belt-and-suspenders):**
  - `utils.js`: `CURRENCY_MISMATCH_TICKERS` set + helper.
  - `api-client.js mapQuoteRow()`: null EV-related fields for
    mismatch tickers when reading from Supabase (handles cached data
    until the next daily refresh).
  - `api.js parseTickerData()`: null `row.ev` for mismatch tickers and
    skip the marketCap+debt−cash fallback that would re-mix scales.
  - `refresh_quotes.mjs`: detect mismatch at source
    (`listingCurrency !== financialCurrency`), null EV-related fields,
    persist `financial_currency` and `listing_currency` for downstream
    visibility on next refresh.
  - Supabase row directly PATCHed: TSM `enterprise_value`,
    `total_revenue`, `total_cash`, `total_debt`, `free_cashflow`,
    `operating_cashflow`, `enterprise_to_revenue`,
    `enterprise_to_ebitda` set NULL.
- **Verified:** TSM cells now render "—" instead of $7.6T.

## Still placeholder / out-of-scope

- Items in the original QA review tied to a React/Vite codebase
  (`.tsx` files, hash routing, etc.) — not applicable; SignalStack AI
  is vanilla JS + custom CSS.
- Insider activity panel — implementation plan delivered earlier this
  session in `docs/insider-activity-plan.md` (commit 1a0c85b on
  `fix/coverage-flags-wiring`); data pipeline and frontend hook are not
  yet built.

## Cache busters bumped to v=20260427b
- `app.js`, `shell.js`, `popup.js`, `earnings-intel.js`, `utils.js`,
  `styles.css`, `api-client.js`, `api.js`
