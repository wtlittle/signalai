# render_email.py — deterministic briefing email renderer

`automation/jobs/render_email.py` renders a fully-formed **plain-text email
body** from the canonical SignalAI JSON. It exists to remove the LLM from the
email-composition step of the weekly/daily cron jobs.

## Why this exists

On Sunday 2026-06-07 the weekly briefing email failed: the cron prompt asked the
LLM to compose the email body inline from `weekly_briefing.json`, and the model
emitted the literal placeholder string `PLACEHOLDER_WILL_REPLACE` as the body.
The send lock file then prevented an automatic retry, so the email never went
out. A manual recovery email was sent at 18:01 UTC.

Composing prose with an LLM at send time is the root cause: it is
non-deterministic and can silently substitute a placeholder. This script
replaces that step with a pure-Python renderer — same JSON in, same text out,
every time. If a required field is missing it **fails loudly** (non-zero exit,
stderr message) so the cron aborts instead of sending garbage.

## Data-integrity mandate

- **Never fabricate.** Missing / null fields render as `n/a`.
- Truncated upstream values are preserved **as-is** (no padding, no cleanup).
- The daily "yesterday's reactions" section only reports a stock-price reaction
  percent when it is explicitly anchored to the stock (e.g. "Stock down ~7%
  post-print"). Fundamental figures ("revenue up 30% YoY") are rejected so a
  growth number is never misrepresented as a price move; absent that, `n/a`.

## Usage

Pure standard library — no dependencies. Run from the repo root.

### Weekly

```bash
python3 -m automation.jobs.render_email \
  --mode weekly \
  --input weekly_briefing.json \
  --output /tmp/weekly_email_body.txt
```

Sections, in order:

1. Title — `SignalAI Weekly Briefing — Week Ending YYYY-MM-DD`
2. `MARKET SUMMARY`
3. `INDEX RETURNS (WEEK)` — S&P 500 / NASDAQ Composite / Dow Jones
4. `TOP 5 VALUE PICKS` — `TICKER — Name (P/E X, Yield Y%)` + Thesis / Catalyst / Risk
5. `TOP 5 MOMENTUM PICKS` — `TICKER — Name`, `1M: … Rev: …`, `Catalyst: …`, `R/R: …`
6. `KEY TRENDS`
7. `RISKS`
8. `NOTABLE WATCHLIST MOVERS`
9. Dashboard link + GitHub Pages link

Exit codes: `0` success, `2` input unreadable, `3` required top-level key
missing, `4` output not writable.

### Daily

```bash
python3 -m automation.jobs.render_email \
  --mode daily \
  --output /tmp/daily_email_body.txt
```

No input file — reads the canonical working copy at
`/home/user/workspace/watchlist-app/` (override with `--data-dir`):
`data-snapshot.json`, `earnings_calendar.json`, `earnings_intel.json`,
`ma_status.json`, `macro_data.json`.

Sections, in order: title, `MARKET SNAPSHOT` (SPY/QQQ/DIA/IWM if present),
`TOP MOVERS (today)` (top 10 by |change1d|, ≥3%, indices skipped),
`EARNINGS TODAY`, `YESTERDAY'S REACTIONS` (top 5 by |reaction|),
`OPEN M&A RUMORS`, `MACRO TILT` (top 3 favored / unfavored sectors), footer.

Each section degrades gracefully to `None today.` (or `None on watchlist.` for
earnings) when its source is missing or empty.

Known data quirk handled: `ma_status.json` `pending_review` sometimes contains
self-referential rows where the company name equals the buyer (e.g. buyer
"Autodesk" for ticker ADSK). These are filtered (comparison ignores corporate
suffixes like `, Inc.`), with a single `WARN` logged to stderr.

## New weekly cron step 8 pattern

Replace the old "ask the LLM to compose the body" step with:

```
8a. Render body deterministically:
    cd /home/user/workspace/watchlist-app
    python3 -m automation.jobs.render_email --mode weekly --input weekly_briefing.json --output /tmp/weekly_email_body.txt
    if [ ! -s /tmp/weekly_email_body.txt ]; then echo "ABORT: empty body" >&2; exit 1; fi
8b. Read body into a variable:
    EMAIL_BODY=$(cat /tmp/weekly_email_body.txt)
8c. Lock file gate (existing logic — unchanged)
8d. Call send_email ONCE with body=<exact contents of /tmp/weekly_email_body.txt>
```

The same pattern applies to the daily cron, swapping `--mode daily` (no
`--input`) and `/tmp/daily_email_body.txt`.

The `-s` test guarantees a non-empty file before the lock gate, and the renderer
exits non-zero on a missing key — together these ensure the cron fails loudly
rather than ever sending a placeholder again.
