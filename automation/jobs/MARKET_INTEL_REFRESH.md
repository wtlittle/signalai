# Market Intel Refresh (per-ticker)

`automation/jobs/market_intel_refresh.py` harvests a per-**ticker** TAM /
category-CAGR / competitive-landscape fact-base into the Supabase
`market_intel_ticker` table, and the drilldown generator injects it into the
`[SIGNAL_DATA_BLOCK]` so the model writes the TAM and competitor sections from
ground-truth data instead of sourcing them live every run.

> This is distinct from the legacy per-**subsector** harvester
> (`automation/jobs/market_intel_harvest.py` → `market_intel` table). That job is
> untouched; this one is keyed on a single ticker.

## Schema

Migration: `supabase/migrations/2026-06-09_market_intel_ticker.sql`

| column              | type          | notes                                                                 |
| ------------------- | ------------- | --------------------------------------------------------------------- |
| `ticker`            | `text` **PK** | uppercase ticker symbol                                               |
| `tam_usd_bn`        | `numeric`     | total addressable market, USD billions; null if not credibly sourced |
| `tam_source_url`    | `text`        | primary source URL backing the TAM                                    |
| `category_cagr_pct` | `numeric`     | category CAGR as a percent (`18.5` = 18.5%); null if unavailable      |
| `drivers`           | `jsonb`       | array of 3-5 market growth-driver strings                            |
| `competitors`       | `jsonb`       | array of 4-7 `{name, ticker, quadrant, threat, source_url}` objects   |
| `updated_at`        | `timestamptz` | `default now()`; drives the 90-day TTL                               |

`competitors[].quadrant` ∈ {`Leader`, `Challenger`, `Visionary`, `Niche`} and
`competitors[].threat` ∈ {`Low`, `Med`, `High`} (Gartner-style labels). Values
the model cannot map are stored as `null`.

### Applying the migration

There is no SQL-exec RPC on this Supabase project, so the DDL cannot be applied
from the job. Apply it once, manually, via the **Supabase SQL editor** (or
`psql`):

```bash
psql "$SUPABASE_DB_URL" -f supabase/migrations/2026-06-09_market_intel_ticker.sql
```

The file is idempotent (`create table if not exists`, `drop policy if exists`),
so re-running is safe. Until it is applied, the harvester runs end-to-end and
logs the parsed row but the upsert returns HTTP 404 (table missing) — no data is
lost, just not persisted.

## The harvester

For each ticker the job:

1. **TTL skip** — reads the existing row; if `updated_at` is younger than
   **90 days** it is skipped (unless `--force`).
2. **Constrained sonar call** — one Perplexity `sonar` call with
   `allowed_domains = [gartner.com, forrester.com, idc.com, statista.com,
   g2.com, sec.gov]` **plus the ticker's IR domain** (e.g. `cloudflare.net`,
   `investor.nvidia.com`, `ir.rubrik.com`). IR domains live in `_IR_DOMAINS`;
   an unknown ticker simply runs against the base research domains.
3. **Strict-JSON parse + normalize** — coerces numerics (or `null`), validates
   quadrant/threat labels, and trims drivers/competitors to shape.
4. **Upsert** — `on_conflict=ticker` into `market_intel_ticker`.

**Data integrity:** every numeric is a number or `null` — never a guess, never
the literal string `"MISSING"`. A `null` renders downstream as an em-dash.

The API path requires `USE_PPLX_API=true` (or it auto-enables when
`PERPLEXITY_API_KEY` is present); otherwise calls route to the Computer queue.

### CLI

```bash
# single ticker
python -m automation.jobs.market_intel_refresh --ticker NET

# full active universe (~123 tickers)
python -m automation.jobs.market_intel_refresh --all

# bypass the 90-day TTL
python -m automation.jobs.market_intel_refresh --ticker NET --force

# preview without calling Perplexity or Supabase
python -m automation.jobs.market_intel_refresh --all --dry-run
```

Required env: `PERPLEXITY_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
`USE_PPLX_API=true`.

### Sample harvested row (NET)

```json
{
  "ticker": "NET",
  "tam_usd_bn": 196.0,
  "tam_source_url": "https://cloudflare.net/files/doc_financials/2026/q1/Q1-2026-Investor-Presentation_.pdf",
  "category_cagr_pct": null,
  "drivers": [
    "Shift from perimeter-based security to Zero Trust and SASE architectures",
    "Continued migration of traffic, applications, and workloads to cloud and edge",
    "Rising DDoS, bot, and API-abuse pressure drives integrated security demand",
    "Enterprise consolidation toward fewer, broader-platform vendors",
    "AI-driven applications and edge inference increase global network demand"
  ],
  "competitors": [
    {"name": "Fastly", "ticker": "FSLY", "quadrant": "Challenger", "threat": "Med", "source_url": "https://www.sec.gov/..."},
    {"name": "Zscaler", "ticker": "ZS", "quadrant": "Leader", "threat": "High", "source_url": "https://www.sec.gov/..."},
    {"name": "Palo Alto Networks", "ticker": "PANW", "quadrant": "Leader", "threat": "High", "source_url": "https://www.sec.gov/..."},
    {"name": "F5", "ticker": "FFIV", "quadrant": "Visionary", "threat": "Med", "source_url": "https://www.sec.gov/..."}
  ],
  "updated_at": "2026-06-09T22:27:43+00:00"
}
```

## Recommended cron

Run the full universe **weekly on the first Sunday of the month at 02:00 UTC**.
With the 90-day TTL, each ticker is only re-harvested roughly quarterly, so most
weekly runs are near-no-ops (cheap freshness skips).

```cron
# first Sunday of the month, 02:00 UTC — full per-ticker market-intel refresh
0 2 1-7 * 0  cd /path/to/repo && USE_PPLX_API=true python -m automation.jobs.market_intel_refresh --all
```

(`1-7 * 0` = a Sunday that also falls on the 1st–7th = the first Sunday.)

**Cost:** ~123 tickers × ~$0.005/`sonar` call ≈ **$0.60/run**. With the 90-day
TTL the steady-state monthly spend is well under that, since only ~1/3 of the
universe is stale on any given run.

> The cron is **not** created automatically. Review this doc, then schedule it
> yourself (e.g. via the deployment's scheduler) after the migration is applied.
