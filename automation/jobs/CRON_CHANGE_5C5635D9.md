# Cron 5c5635d9 — Pending Step Update

## Status

The `pplx-tool schedule_cron` tool was not available in this agent session.
This file documents the required change so the parent agent can apply it.

## Required Change

Cron ID: `5c5635d9` (daily briefing, runs daily on weekdays)

Current step structure (from runlog):
```
git_pull → render_email → email_lock → send_email
```

**Required new step structure:**

- Step 2 (was `render_email`): Replace with:
  ```
  python3 -m automation.jobs.send_briefing --mode daily --date "$DATE" --output /tmp/daily_body.txt
  ```
  This internally renders + checks freshness + either writes body (exit 0) or fires push alert (exit 5).

- Step 3 (was `send_email`): Remove the duplicate send_email block.
  `send_briefing.py` owns the email lock and the send via `automation.alerts.quarantine_alert`.

## Why

When `render_email.py` is called directly (old cron), the freshness-abort exit code (5)
is not handled — the cron continues and sends a stale briefing. `send_briefing.py`
wraps `render_email` and routes exit code 5 to a push/in_app operator alert instead of
proceeding with the send.

## Confirmation

`automation/jobs/send_briefing.py` exists and exposes `--mode daily` (confirmed).
