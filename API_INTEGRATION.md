# Perplexity API Integration

This document covers how SignalAI talks to the Perplexity API for Compare AI
Read, Drilldown generation, and the Python automation cron jobs (pre-/post-
earnings notes, weekly briefing, etc.).

## TL;DR

| Surface | Default model | Cost / call (typical) | Fallback when no key |
|---|---|---|---|
| Compare → AI Read | `sonar-reasoning-pro` | ~$0.05-0.15 | Deep Research deeplink + paste-back |
| Drilldown → Run | `sonar-deep-research` | ~$0.20-0.40 (low effort) | Deep Research deeplink + paste-back |
| Pre / post-earnings cron | `sonar-reasoning-pro` | ~$0.05-0.12 | Queue to `pending_tasks.json` |
| Weekly briefing | `sonar-deep-research` | ~$0.30-0.80 per call (x3 calls/wk) | Queue to `pending_tasks.json` |
| News-tag refresh | `sonar` | <$0.01 | Queue |

Set once. After that, every LLM surface in the app routes through the API.

## Client-side setup (Compare + Drilldown)

1. Click the **API** button in the top bar (top-right of `topbar-actions`).
2. Paste your Perplexity API key (`pplx-...`). [Get one here](https://www.perplexity.ai/account/api/keys).
3. Pick a Compare model, Drilldown model, and Deep Research effort.
4. Click **Test connection** — it fires one `sonar` call (~$0.005) to confirm
   the key + CORS work. If you see a CORS error, see "Troubleshooting" below.
5. **Save**.

The key is stored only in `localStorage` under `signalai_pplx_api_key`. It is
never written to disk, never committed, and never sent anywhere except
`api.perplexity.ai` in `Authorization` headers.

After a key is set:

- Compare → AI Read shows a primary **Run via API** button (no new tab).
- Drilldown Run buttons call the API directly; the returned HTML lands in
  the existing paste textarea so the standard Save flow still writes to
  `notes/drilldown/`, the Library, and Supabase `drilldown_intel`.

Either surface still has an escape-hatch link to force the paste-back flow.

## Server-side setup (Python automation)

### GitHub Actions (production)

The `Daily Research Refresh` workflow reads `PERPLEXITY_API_KEY` from
[repo secrets](https://github.com/wtlittle/signalai/settings/secrets/actions).
Once that secret is set, every scheduled BMO + AMC run + manual dispatch
goes through the API automatically. The workflow exports
`USE_PPLX_API=true` and `PERPLEXITY_REASONING_EFFORT=low` to keep runs
cost-bounded.

### Local development

The Python client in `automation/perplexity/client.py` reads the key from
either env var:

```bash
export PPLX_API_KEY=pplx-xxxxxxxxxxxxxxxxxxxxxxxx
# legacy alias also accepted:
export PERPLEXITY_API_KEY=pplx-xxxxxxxxxxxxxxxxxxxxxxxx
```

Behavior:

- If a key is set, API mode is enabled automatically. The pre/post-earnings
  and weekly-briefing jobs now actually call the API instead of writing to
  `automation/queue/pending_tasks.json`.
- To force the legacy queue behavior even when a key is present:
  `USE_PPLX_API=false`.

### Per-task model overrides

Override any task's model via env var (rarely needed; sensible defaults
ship in `TASK_MODEL_MAP`):

```bash
PERPLEXITY_MODEL_PRE_EARNINGS=sonar-reasoning-pro
PERPLEXITY_MODEL_POST_EARNINGS=sonar-reasoning-pro
PERPLEXITY_MODEL_DRILLDOWN=sonar-deep-research
PERPLEXITY_MODEL_WEEKLY=sonar-deep-research
PERPLEXITY_MODEL_NEWS=sonar
PERPLEXITY_REASONING_EFFORT=low     # low|medium|high (deep-research only)
```

Reasoning effort defaults to `low` for automated jobs — keeps the weekly
briefing budget around $5–10/mo rather than $20–30 at default effort.

### Cost expectations (rough monthly estimates)

| Job | Frequency | Per call | Monthly |
|---|---:|---:|---:|
| Pre-earnings notes | ~45 / mo | $0.10 | $4–5 |
| Post-earnings notes (BMO + AMC crons) | ~50 / mo | $0.10 | $5–6 |
| Weekly briefing | 12 (3 × 4 wk) | $0.40 | $5 |
| News-tag classification | ~22 wkday | <$0.01 | $0.20 |
| **Server total** | | | **~$15–20** |
| Compare AI Read (ad-hoc) | 30–80 | $0.10 | $3–8 |
| Drilldown (ad-hoc, low effort) | ~30 | $0.30 | $9 |
| **Client total** | | | **~$12–17** |
| **GRAND TOTAL** | | | **~$30–40/mo** |

Heavy usage (medium/high effort everywhere, more drilldowns):
**up to ~$80–100/mo**.

Compared to the paste-back-only path, the trade is roughly **~$30/mo for
removing all manual paste steps** plus the option to use Deep Research from
the cron jobs (which were previously skipped without a key).

## Architecture

```
                   ┌────────────────────────────────┐
   Browser         │  pplx-api.js                   │
   (Compare,       │   • key in localStorage        │
   Drilldown)      │   • single fetch wrapper       │
                   │   • JSON repair                │
                   │   • cost estimator             │
                   └───────────────┬────────────────┘
                                   │ HTTPS
                                   ▼
                       api.perplexity.ai/chat/completions
                                   ▲
                                   │ HTTPS
                   ┌───────────────┴────────────────┐
   Cron jobs       │  automation/perplexity/         │
   (Python)        │     client.py                   │
                   │   • PPLX_API_KEY env            │
                   │   • TASK_MODEL_MAP per task     │
                   │   • cache + rate limit          │
                   │   • USE_PPLX_API toggle         │
                   └─────────────────────────────────┘
```

No backend proxy — the browser talks directly to Perplexity. The drilldown
backend at port 5001 is only used to *persist* the resulting HTML to
`notes/drilldown/` and Supabase, not to call the LLM.

## Troubleshooting

**`HTTP 401`** — key invalid or expired. Open API Settings and re-paste.

**`HTTP 429`** — rate-limited. Either upgrade Perplexity tier or wait. The
Python client retries once after 30s; the client-side does not auto-retry.

**`Failed to fetch` / CORS error** — Perplexity's API has supported CORS
for direct browser calls since 2024, but some browser extensions (privacy
blockers, corporate proxies) block it. Try in an incognito window or use
the paste-back fallback link.

**Deep Research timeout** — `sonar-deep-research` runs synchronous and can
take 2–5 minutes. The client-side timeout is 10 minutes; if you hit it,
either switch to `sonar-reasoning-pro` or lower the effort.

**JSON parse failed** — the raw response is preserved on screen. You can
also see it in the `cmp-ai-raw` block. The system prompt asks for a single
fenced JSON block; if the model strays, the loose parser repairs trailing
commas and smart quotes, but extreme drift will fall back to "raw shown".

## Privacy & security

- Key lives in `localStorage` only. Clearing browser data clears it.
- Each call goes browser → `api.perplexity.ai` over HTTPS. Nothing routes
  through any SignalAI-controlled server.
- Don't commit the key. The repo's `.gitignore` already excludes `.env`
  files; the `automation/.env.example` shows the format without a real
  value.
