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
    base.py             # EarningsSource ABC + SourceResponse (pydantic)
    factset_source.py   # stub (returns None) — first in priority order
    finnhub_source.py   # history_8q + in-quarter EPS actuals/consensus
    yfinance_source.py  # quotes + quarterly revenue actuals
    perplexity_source.py# FY guidance + revenue consensus (qualitative)
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
       FactSet  ->  Finnhub  ->  yfinance  ->  Perplexity
       (higher priority wins field collisions; each accepted field is
        stamped <field>_source for provenance)
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
    "sources_used": {"factset": 0, "finnhub": 150, "yfinance": 188, "perplexity": 40}
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
