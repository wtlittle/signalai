# SignalAI Automation

All research generation scripts live here. The dashboard in the repo root reads these outputs.

## Architecture

```
GitHub Actions (daily-research.yml)
└─ automation/jobs/daily_refresh.py           ← main entry point
   ├─ refresh_quotes.mjs                      ← Yahoo Finance → Supabase (no LLM)
   ├─ refresh_deep_dive.mjs                   ← Yahoo Finance → Supabase (no LLM)
   ├─ refresh_charts.mjs                      ← Yahoo Finance → Supabase (no LLM)
   ├─ refresh_comps_outperf.mjs               ← Yahoo Finance → Supabase (no LLM)
   ├─ refresh_macro.mjs                       ← Yahoo Finance → macro_data.json (no LLM)
   ├─ jobs/earnings_events.py                 ← yfinance → earnings_calendar.json (no LLM)
   ├─ jobs/pre_earnings_notes.py              ← Perplexity (with skip guards + cache)
   ├─ jobs/post_earnings_notes.py             ← Perplexity (with skip guards + cache)
   └─ update_subsectors.mjs                   ← local classification (no LLM)
```

## Credit-Saving Rules

| Rule | Implementation |
|------|---------------|
| Perplexity is NEVER called for tickers outside the pre/post earnings window | `daily_refresh.py` filters active tickers before any API call |
| Notes are NEVER regenerated if the file exists and is > 500 bytes | `cache.note_already_exists()` checks file + index |
| All Perplexity results are cached for 20 hours | `cache.research_cache_exists()` in `client.py` |
| Post-earnings notes reuse pre-earnings cache | `load_research_cache(ticker, "pre_earnings")` passed as context |
| Prompts return structured JSON only | System prompt: "Return only structured JSON. No prose." |
| Market data uses Yahoo Finance, not LLM | `.mjs` scripts hit Yahoo Finance APIs directly |
| `DRY_RUN=true` prevents all API calls | Checked in `client.py` before every request |

### Estimated API Calls Per Day

| Scenario | Before (full watchlist) | After (event-gated) |
|----------|------------------------|---------------------|
| No earnings this week | ~150+ calls | 0 calls |
| 5 tickers in window | ~150+ calls | ~10 calls (5 news + 5 notes) |
| Bank earnings week (10 tickers) | ~150+ calls | ~20 calls |
| Maximum (14 pre + 7 post) | ~150+ calls | ~42 calls |

## Folder Structure

```
automation/
  README_AUTOMATION.md      ← this file
  .env.example              ← template for required secrets
  shared/
    paths.py                ← repo-relative path constants (no absolute paths)
    tickers.py              ← watchlist loading from utils.js
    io_helpers.py           ← JSON read/write helpers
    cache.py                ← cache key logic: {ticker}_{date}_{task}
  perplexity/
    client.py               ← single API wrapper for ALL Perplexity calls
    prompts.py              ← ALL prompt templates as Python functions
    rate_limiter.py         ← token bucket rate limit guard
  jobs/
    daily_refresh.py        ← entry: full daily pipeline
    earnings_events.py      ← entry: detect upcoming/recent earnings
    pre_earnings_notes.py   ← entry: generate pre-earnings notes
    post_earnings_notes.py  ← entry: generate post-earnings notes
    weekly_briefing.py      ← entry: generate weekly market briefing
  scripts/
    run_all.sh              ← run full daily pipeline locally
    run_notes_only.sh       ← run only note generation
```

## Running Locally

```bash
# 1. Clone the repo
git clone https://github.com/wtlittle/signalai.git
cd signalai

# 2. Set up environment
cp automation/.env.example .env
# Edit .env with your actual keys

# 3. Install dependencies
pip install requests yfinance python-dotenv
npm install @supabase/supabase-js

# 4. Run the full pipeline
python -m automation.jobs.daily_refresh

# 5. Or run just notes (cheaper — only Perplexity calls)
python -m automation.jobs.pre_earnings_notes
python -m automation.jobs.post_earnings_notes

# 6. Dry run (no API calls, no file writes)
DRY_RUN=true python -m automation.jobs.daily_refresh

# 7. Force regenerate everything (ignore cache)
FORCE_REGENERATE=true python -m automation.jobs.daily_refresh
```

## Running in GitHub Actions

The workflow at `.github/workflows/daily-research.yml` runs automatically at 7am ET on weekdays.

**Required secrets** (set in repo Settings → Secrets):
- `PERPLEXITY_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`

**Manual triggers** via `workflow_dispatch`:
- `force_regenerate`: Skip all caches
- `dry_run`: Print without calling APIs
- `notes_only`: Skip market data refresh

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PERPLEXITY_API_KEY` | Yes | — | API key for Perplexity Sonar |
| `PERPLEXITY_MODEL` | No | `sonar-pro` | Model to use (`sonar` for cheaper) |
| `SUPABASE_URL` | Yes | — | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | — | Supabase service role key |
| `DRY_RUN` | No | `false` | Print actions without executing |
| `FORCE_REGENERATE` | No | `false` | Bypass all cache guards |
| `MAX_PRE_EARNINGS_DAYS` | No | `14` | Days before earnings to start notes |
| `MAX_POST_EARNINGS_DAYS` | No | `14` | Days after earnings to keep notes active |

## Cache Layout

```
data/cache/
  AAPL_2026-04-17_pre_earnings.json
  AAPL_2026-04-17_daily_news.json
  MARKET_2026-04-17_weekly_value.json
  ...
```

Cache files are keyed by `{ticker}_{date}_{task}.json` and expire after 20 hours.
Stale files (>3 days old) are auto-cleaned at the start of each run.

## Market Intel Harvest

`automation/jobs/market_intel_harvest.py` populates the Supabase `market_intel`
table with per-subsector TAM, category CAGR, structural demand drivers, and
AI/ML context. The drilldown surface splices these rows into the
`[SIGNAL_DATA_BLOCK]` of the canonical prompt so notes start with a credible
market-sizing fact-base instead of asking the model to source it from scratch.

### When it runs

The harvester is wired into `daily_refresh.run()` as `step_market_intel_harvest()`
and is **Sunday-only** in production:

- Mode gate: only triggered in `bmo` and `full` modes (skipped on `amc` runs).
- Day gate: the function self-skips unless `today.weekday() == 6` (Sunday).
- Freshness gate: rows whose `harvested_at` is within `MARKET_INTEL_TTL_DAYS`
  (default 30) are skipped automatically — pass `--force` to override.

This means a normal weekday run is a no-op, the Sunday BMO run touches every
stale subsector, and ad-hoc backfills can be triggered manually.

### Schema

Defined in `automation/scripts/create_market_intel_table.sql`:

| Column | Type | Notes |
|---|---|---|
| `subsector` | text (PK) | Matches `SUBSECTOR_MAP` in `utils.js`. |
| `source` | text (PK) | Gartner, IDC, Forrester, Statista, McKinsey, 10-K MD&A, etc. |
| `tam_label` | text | One-line TAM, e.g. `"$185B by 2028"`. |
| `tam_usd_bn` | numeric | TAM in USD billions, or null. |
| `growth_rate_label` | text | One-line CAGR string. |
| `growth_rate_pct` | numeric | CAGR as a percent (12.4 = 12.4%), or null. |
| `structural_drivers` | text | 2-4 sentences on demand drivers. |
| `ai_ml_context` | text | 2-4 sentences on AI/ML disruption. |
| `raw_excerpt` | text | Short verbatim quote/stat from the source. |
| `harvested_at` | timestamptz | Used for the freshness gate. |

Apply the schema once via Supabase SQL editor or `psql`; the file is idempotent.

### Manual usage

```bash
# Dry-run: list every subsector that would be harvested, no Supabase / API calls.
python -m automation.jobs.market_intel_harvest --dry-run

# Live run: queues a Perplexity research task per stale subsector and upserts
# any direct-API results into the market_intel table.
SUPABASE_URL=https://...supabase.co \
SUPABASE_SERVICE_KEY=sb_secret_... \
python -m automation.jobs.market_intel_harvest

# Force re-harvest every subsector regardless of freshness.
python -m automation.jobs.market_intel_harvest --force
```

### Relevant env vars

| Variable | Default | Purpose |
|---|---|---|
| `MARKET_INTEL_TTL_DAYS` | `30` | How fresh a row must be to be skipped. |
| `SUPABASE_URL` | — | Required for live runs. |
| `SUPABASE_SERVICE_KEY` | — | Required for live runs. |
