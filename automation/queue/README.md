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

Each entry in `pending_tasks.json` has the following shape:

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

## Processing a queued task in Computer

Open `pending_tasks.json`, pick the top entry, and prompt Computer to:

> Read `automation/queue/pending_tasks.json`. For the first entry, run the
> `prompt` (following `system`) and save the result to the correct output
> path based on `task`. Then remove that entry from the queue and commit the
> result.

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
