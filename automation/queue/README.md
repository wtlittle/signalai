# Computer Task Queue

This directory is the handoff point between the automated SignalAI pipeline
(daily refresh, earnings event detection, etc.) and **Perplexity Computer**.

## How it works

1. `automation/perplexity/client.py` no longer calls the Perplexity REST API
   by default. Instead, every `call_perplexity(...)` invocation appends a task
   entry to `pending_tasks.json`.
2. Each morning, after the daily refresh finishes, the daily_refresh job
   prints a summary of how many tasks were queued.
3. Perplexity Computer picks up this repo on its next run, reads
   `pending_tasks.json`, and processes each queued task:
   - For note-generation tasks, writes the output markdown to
     `notes/pre_earnings/<TICKER>_<DATE>.md` or
     `notes/post_earnings/<TICKER>_<DATE>.md`.
   - For research tasks, writes structured JSON to the appropriate
     cache path via the existing `automation/shared/cache.py` helpers.
4. Once a task is completed, Computer removes it from `pending_tasks.json`.

## Queue entry schema

Each entry in `pending_tasks.json` has the following **base shape**:

```json
{
  "ticker": "UNH",
  "task": "pre_earnings_note",
  "prompt": "Full prompt string sent to the LLM...",
  "system": "System message for the LLM...",
  "max_tokens": 1500,
  "queued_at": "2026-04-22T11:00:00-04:00"
}
```

Task-specific entries may carry additional fields via `extra_meta`
(merged flat into the entry at queue time).

## Supported task types

| `task`               | Output path                                   | Format   | Notes |
|----------------------|-----------------------------------------------|----------|-------|
| `pre_earnings_note`  | `notes/pre_earnings/<TICKER>_<DATE>.md`       | Markdown | Buy-side pre-earnings preview. |
| `post_earnings_note` | `notes/post_earnings/<TICKER>_<DATE>.md`      | Markdown | Post-print reaction + thesis check. |
| `daily_news`         | handled in-pipeline (cached by `client.py`)   | JSON     | Initial scan: returns `has_material_update` + `items[]`. |
| `news_tag`           | `data/news_tagged/<TICKER>.json`              | JSON     | Buy-side catalyst tagging — see below. |

### `news_tag` task (buy-side catalyst tagging)

Queued by `step_news_tagging` in `automation/jobs/daily_refresh.py` for every
ticker that had a material `daily_news` update. Extra fields on the entry:

| Field           | Description |
|-----------------|-------------|
| `article_count` | Integer — number of articles in the batch. |
| `raw_path`      | Relative path to the raw scan JSON persisted under `data/news_raw/<TICKER>.json`. |
| `output_path`   | Where Computer should write the tagged output (always `data/news_tagged/<TICKER>.json`). |
| `articles`      | Array of `{headline, teaser, url, impact}` in the SAME order as the prompt's numbered list `[1]`, `[2]`, … |

**Output contract:** Computer must return a **JSON array** the same length and
same order as `articles`. Each element has the following shape:

```json
{
  "index": 1,
  "catalyst_tag": "Earnings" | "Analyst Action" | "SEC Filing" | "M&A" | "Guidance" | "Macro" | "Exec Change" | "Legal-Regulatory" | "Capital Markets" | "Activist-Short" | "Other",
  "direction": "Bullish" | "Bearish" | "Neutral" | "Mixed",
  "priority": "High" | "Medium" | "Low",
  "blurb": "≤25 words naming the specific financial variable affected",
  "duplicate": false
}
```

Rules (enforced in the prompt): no invented numbers; `Neutral` when evidence is
insufficient; reject generic sentiment; set `duplicate: true` on the
lower-priority of any two articles covering the same event.

Write the array to `data/news_tagged/<TICKER>.json` (overwrite), then remove
the entry from `pending_tasks.json`.

## Processing a queued task in Computer

Open `pending_tasks.json`, pick the top entry, and prompt Computer to:

> Read `automation/queue/pending_tasks.json`. For the first entry, run the
> `prompt` (following `system`) and save the result to the correct output
> path based on `task` (see the task-type table above; `news_tag` tasks carry
> an explicit `output_path`). Then remove that entry from the queue and
> commit the result.

## Falling back to direct API

To temporarily re-enable the direct Perplexity REST API (for example, during
a backfill when the Computer loop is paused), set:

```bash
export USE_API_FALLBACK=true
export PERPLEXITY_API_KEY=pplx-...
```

The client will then route tasks back through `api.perplexity.ai` with the
existing 429/401 safety handling. Without this flag, every task is queued.

## Why this exists

- A single auditable handoff point for all LLM work.
- No dependency on `api.perplexity.ai` / no API key secret management in CI.
- The automated pipeline never crashes from LLM auth or rate-limit errors;
  it simply enqueues work for Computer to process on its own cadence.
- Replaces the old automated LLM generation cron entirely.
