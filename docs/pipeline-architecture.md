# Earnings Pipeline Architecture

A per-ticker, idempotent, contract-based pipeline for refreshing earnings intel.
It replaces the monolithic per-ticker loop that repeatedly broke the daily cron
when one ticker arrived with a missing field shape (e.g. latest `history_8q`).

**Data-integrity mandate:** the pipeline never fabricates values, never writes a
partial record, and never renders a placeholder as data. A ticker that cannot be
completed is **quarantined** with its prior good data preserved.

---

## Layout

```
automation/
  sources/
    base.py                      # EarningsSource ABC + SourceResponse (pydantic)
    perplexity_finance_source.py # history_8q + in-quarter actuals — FIRST in priority order
    finnhub_source.py            # history_8q + in-quarter EPS actuals/consensus
    yfinance_source.py           # quotes + quarterly revenue actuals
    perplexity_source.py         # FY guidance + revenue consensus (qualitative)
    cached_source.py             # on-disk fallback (per-field freshness) — LAST in priority order
  pipeline/
    schema.py           # EarningsIntelRecord — the ONE contract
    refresh_ticker.py   # refresh_ticker(symbol, *, force=False) -> TickerResult
    runner.py           # refresh_universe(symbols, *, parallel=8) -> RunSummary
    quarantine.py       # read/write quarantine.json
    status.py           # read/write pipeline_status.json
quarantine.json         # committed, canonical — who is held back and why
pipeline_status.json    # committed — last attempt/success per ticker + run summary
```

---

## Flow (one ticker)

```
refresh_ticker(symbol)
  1. Load prior record from earnings-intel.json (skip if it never existed*).
  2. Decide completeness policy:
       state == "post_earnings" AND hours_since_report >= 48  ->  must be COMPLETE
       otherwise                                              ->  "still settling", shape-only
  3. Walk sources in priority order, gap-filling:
       PerplexityFinance  ->  Finnhub  ->  yfinance  ->  Perplexity  ->  CachedFallback
       (higher priority wins field collisions; each accepted field is
        stamped <field>_source for provenance)
       CachedFallback is reached only when the live sources leave the record
       incomplete; it re-emits on-disk fields that are still inside their
       per-field freshness window (see "Cached fallback policy" below).
  4. Validate the merged record:
       a. structural   — pydantic EarningsIntelRecord (shape/types)
       b. completeness — required RVC fields, history_8q (8 quarters w/ rev+eps),
                         guidance fields, per the policy from step 2
  5. If valid+complete:  persist record, CLEAR any quarantine entry.
     Else:               DO NOT write, UPSERT quarantine entry (prior data kept).
  6. Record per-ticker status (last_attempt, last_success, source, missing, duration).
```

\* First-time creation of a ticker is owned by the existing notes/sync path, not
this pipeline. The pipeline refreshes and gates existing tickers.

`refresh_universe` runs the above across a `ThreadPoolExecutor` (default 8 workers),
tallies a `RunSummary`, and writes the per-run block to `pipeline_status.json`.
All JSON mutations are guarded by a lock and written atomically (`os.replace`).

---

## The contract (`schema.py`)

`EarningsIntelRecord` is the single source of truth for record shape. It is
intentionally permissive at the type level (`extra="allow"`, only `ticker` and
`state` required) because **shape validation is separate from completeness policy**:

- **Shape** is enforced by pydantic, always.
- **Completeness** (which fields must be present, and when) is time-based policy
  driven by named field lists the verifier and `refresh_ticker` both import:
  - `REQUIRED_RVC_FIELDS`
  - `GUIDANCE_FIELDS`
  - `HISTORY_8Q_REQUIRED_QUARTERS` (= 8, each quarter needs revenue AND eps)

The completeness verifier (`scripts/verify_earnings_intel_completeness.py`) consumes
the **same** model and field lists — there is no second, drifting definition.

### Short-history tickers

Some recent IPOs lack 8 quarters of public history. The pipeline maintains
an allowlist in `automation/pipeline/schema.py:SHORT_HISTORY_TICKERS` mapping
the ticker to its current minimum-acceptable quarter count. These tickers
are NOT quarantined for incomplete history; instead the pipeline accepts
their max-available history and tags the record with
`partial_history_accepted=true`. Remove an entry from the map once the
ticker has 8 fully-reported quarters.

Current entries: SNDK (4 quarters, IPO Feb 2025), SOC (5 quarters, IPO 2025).

---

## What quarantine means

A quarantine entry is *"we attempted this ticker, it could not be completed to
contract, so we are NOT publishing new (incomplete) data."* The previously-good
record in `earnings-intel.json` is left untouched. Entry shape:

```json
{
  "symbol": "AMZN",
  "quarantined_at": "2026-06-04T15:10:00Z",
  "last_success_at": "2026-04-30T12:00:00Z",
  "sources_tried": [{"name": "finnhub", "error_or_missing_fields": "no api key"}],
  "missing_fields": ["history_8q"]
}
```

The entry is removed automatically on the next successful refresh.

---

## Reading `pipeline_status.json`

```json
{
  "generated_at": "2026-06-04T15:10:05Z",
  "tickers": {
    "AMZN": {
      "last_attempt_at": "...", "last_success_at": "...",
      "last_source": "yfinance", "missing_fields": ["history_8q"],
      "quarantined": true, "duration_ms": 812
    }
  },
  "run": {
    "run_id": "...", "started_at": "...", "finished_at": "...",
    "total": 192, "succeeded": 188, "quarantined": 4, "skipped": 0,
    "sources_used": {"perplexity_finance": 170, "finnhub": 150, "yfinance": 188, "perplexity": 40, "cached": 6}
  }
}
```

The dashboard reads both files on boot: a health pill
(`Pipeline: 188/192 healthy · 4 quarantined`) opens a modal listing quarantined
tickers, and each affected card shows a quarantine badge with the reason + last
good timestamp.

---

## How to add a source

1. Create `automation/sources/<name>_source.py` with a class extending
   `EarningsSource`; implement `fetch(symbol) -> SourceResponse | None`.
2. Use `self.tag(fields, keys)` so every field you supply carries `<field>_source`.
   Return `self.response(symbol, fields, missing=[...])`.
3. Register it in `refresh_ticker._ordered_sources()` at the right priority
   (higher = wins collisions; the chain only gap-fills, so a later source can
   only fill what earlier ones left blank).
4. Never supply a field you cannot stand behind — omit it and let it be `missing`.
   A missing required field quarantines the ticker; a fabricated one corrupts it.

---

## Surgically re-fetch one ticker

```bash
python -c "from automation.pipeline.refresh_ticker import refresh_ticker; \
print(refresh_ticker('ACN', force=True))"
```

`force=True` bypasses any freshness short-circuit and re-runs the full source
chain. The result (written / quarantined / missing fields / source) prints as a
`TickerResult`. To re-run a set:

```bash
python -c "from automation.pipeline.runner import refresh_universe; \
print(refresh_universe(['AMZN','ACN','AVGO'], parallel=4).as_dict())"
```

---

## Source matrix

| Source | Priority | Supplies | Reaches data via |
|---|---|---|---|
| PerplexityFinance | 1 | `history_8q` (trailing 8Q), in-quarter rev/eps actual + eps consensus + surprise | `external-tool` CLI (subprocess) -> `finance_earnings_history` |
| Finnhub | 2 | `history_8q` (GAAP reconstruction), in-quarter EPS actual/estimate/surprise | `FINNHUB_API_KEY` HTTP |
| yfinance | 3 | in-quarter revenue actual, last-resort short history | `yfinance` package |
| Perplexity (sonar) | 4 | FY guidance + qualitative narrative only (never in-quarter actuals) | `PERPLEXITY_API_KEY` HTTP |
| CachedFallback | 5 | on-disk `history_8q` / FY guidance / consensus / `next_earnings_date` / identity, each only inside its freshness window | reads the prior `earnings_intel.json` record |

PerplexityFinance replaced the former FactSet stub at the head of the chain.
Because it covers `history_8q` for nearly every ticker, the lower sources only
gap-fill what it leaves null (e.g. yfinance revenue actual when the table omits a
revenue cell).

### `external-tool` CLI dependency (cron environments)

`PerplexityFinanceSource` shells out to the preinstalled `external-tool` CLI,
which requires the **`external-tools` credential preset**. The pipeline runs in
cron as plain `python3 -m automation.jobs.daily_refresh`, so the cron bash call
that launches it MUST pass `api_credentials=["github","external-tools"]` (the
preset injects a short-lived token into `/tmp/.tools_service_endpoint` that the
CLI reads). Without the preset the source logs a warning and the chain falls
through to Finnhub -- no crash, but `history_8q` coverage drops back to the
Finnhub reconstruction. The source never fabricates: a missing credential simply
defers to the next source.

---

## Cached fallback policy

`CachedFallbackSource` (`automation/sources/cached_source.py`) is the LAST source
in the chain. It exists because PR #46 (PerplexityFinance -> Finnhub -> yfinance
-> sonar) left a residual ~24 quarantined tickers: mature large caps where ALL
three structured sources transiently returned a null/short `history_8q` on a
single run. For those tickers we already hold a clean trailing-8Q on disk from a
prior successful run; a transient triple-miss should not re-quarantine them.

The source re-emits a previously-persisted field **only while it is inside a
per-field freshness window**, and stamps each emitted field
`<field>_source = "cached"` plus a `<field>_source_age_days` diagnostic. It never
fabricates: missing/stale/never-cacheable -> the field is left null and rendered
`n/a` downstream.

### Per-field max age

| Field | Max age | Cacheable? | Rationale |
|---|---|---|---|
| `history_8q` (reported quarters) | 92 days | YES | Reported quarters do not change after the conference call |
| `fy_guide_*` (FY revenue/EPS guidance bullets) | 95 days | YES | Updated quarterly; sticky between earnings |
| `consensus_estimates` (street EPS/rev consensus next quarter) | 14 days | YES | Drifts but slowly; recent value better than null |
| `in_quarter_eps_actual`, `in_quarter_rev_actual` | n/a | NO | Live current-quarter print — never serve from cache |
| `in_quarter_eps_surprise_pct`, `in_quarter_rev_surprise_pct` | n/a | NO | Derived from current actuals |
| `next_earnings_date` | 30 days | YES | Sticky once announced |
| `latest_news_summary` / `latest_news_url` | n/a | NO | Sonar-only; stale news is never cached |
| Identity (`name`, `sector`, `subsector`) | 365 days | YES | Slow-changing |

### Never-cache list

`in_quarter_eps_actual`, `in_quarter_rev_actual`, `in_quarter_eps_surprise_pct`,
`in_quarter_rev_surprise_pct`, `latest_news_summary`, `latest_news_url`. These are
dropped regardless of age — serving a prior quarter's print as the current one
would be a fabrication, which is exactly what this pipeline exists to prevent.

### `history_8q` is never half-fixed

The cached `history_8q` is filtered to fully-reported quarters (both
`revenue_actual` and `eps_actual` present). If fewer than 8 survive, the field is
omitted entirely rather than emitted as a short array — the completeness gate
would reject a short array anyway, and a partial backfill would mask the gap.

### Freshness timestamp resolution (known limitation)

Age is measured against a per-field `<field>_updated_at` timestamp when present.
The current corpus does NOT yet carry per-field timestamps, so the source falls
back to a record-level timestamp (`last_updated_at`, then `intel_updated_at`).
Until the pipeline starts stamping per-field timestamps, every cacheable field on
a record therefore shares the record's age. This is a sound UPPER bound (a field
is never older than its record), so the policy stays conservative: it can drop a
field that is actually fresher than the record, but it will never serve one that
is stale.

### Audit trail

When the cached fallback fills a gap the live sources left null, `refresh_ticker`
stamps a record-level `cached_fallback_reason` (e.g.
`"perplexity_finance,finnhub,yfinance all returned null for history_8q"`) on the
record and in `pipeline_status.json`, so cached fields are auditable in
postmortems.

---

## Integration points

- `automation/jobs/daily_refresh.py` — calls `refresh_universe(load_tickers())`
  in place of the old per-ticker post-earnings loop; all other daily work is
  unchanged. The completeness verifier still runs last and exits non-zero if a
  ticker is incomplete-but-not-quarantined.
- `automation/jobs/post_earnings_refresh.py` — calls `refresh_ticker(symbol,
  force=True)` per target after syncing notes.

Legacy backfill scripts (`backfill_*`) remain in place, marked `[DEPRECATED]`;
they are scheduled for removal in a follow-up PR once this pipeline has run
cleanly for 7 days.
