# Weekly Briefing Cron Update

Replaces cron `4277e158` (3 Computer subagents) with a single Python module
that makes 3 `sonar-deep-research` API calls in parallel.

## New cron task definition

```
Schedule: 0 10 * * 0   (Sundays at 10:00 AM)

Steps:
  1. python -m automation.jobs.weekly_briefing --output /home/user/workspace/watchlist-app/weekly_briefing.json
  2. node /home/user/workspace/watchlist-app/update_subsectors.mjs
  3. node /home/user/workspace/populate_supabase.mjs
  4. cd /home/user/workspace/watchlist-app && git add -A && git commit -m "weekly briefing $(date +%Y-%m-%d)" && git push
  5. (email delivery via gcal connector -- unchanged)
```

## Environment variables required

```
PERPLEXITY_API_KEY=<your key>
USE_PPLX_API=true
SUPABASE_URL=https://wcyirdvvuetzodiedzss.supabase.co
SUPABASE_SERVICE_KEY=<your key>
```

## What changed

| Before (cron 4277e158)          | After (this module)                      |
|---------------------------------|------------------------------------------|
| 3 Computer subagents in parallel| 3 `sonar-deep-research` API calls        |
| ~$3-5 per run (Computer credits)| ~$0.30-0.50 per run (API tokens+search)  |
| 10-15 min runtime               | 3-8 min runtime (parallel, 600s timeout) |
| Results compiled by subagents   | Single Python process compiles JSON      |

## Cost estimate

Per run (3 deep-research calls at `reasoning_effort=low`):

| Component        | Tokens (est.) | Rate              | Cost       |
|------------------|---------------|-------------------|------------|
| Input tokens     | ~30K total    | $2/1M             | $0.06      |
| Output tokens    | ~10K total    | $8/1M             | $0.08      |
| Reasoning tokens | ~5K total     | $3/1M             | $0.015     |
| Citation tokens  | ~3K total     | $2/1M             | $0.006     |
| Search queries   | ~30 total     | $5/1K             | $0.15      |
| **Total**        |               |                   | **~$0.31** |

Compare to ~$3-5/run for 3 Computer subagents (assuming ~15 min each at
Perplexity Computer pricing).

## Manual smoke test

```bash
PERPLEXITY_API_KEY=<key> USE_PPLX_API=true \
  python -m automation.jobs.weekly_briefing --output /tmp/test_briefing.json
cat /tmp/test_briefing.json | python -m json.tool | head -50
```

## Rollback

If issues arise, restore cron `4277e158` to its original definition.
The Python module does not modify any state beyond writing
`weekly_briefing.json` to the specified output path.
