"""Queue drainer for automation/queue/pending_tasks.json.

The watchlist app cron environment has no Perplexity API key, so tasks
queued by note generators (daily_news, news_tag, pre_earnings,
post_earnings) accumulate forever. This drainer:

1. Drops any task older than MAX_AGE_DAYS (default 7).
2. Deduplicates by (ticker, task) — keeps only the newest entry per pair.
3. Caps each task type at MAX_PER_TYPE entries (default 50, most recent).
4. Rewrites the queue file. Idempotent.

Run manually or wire into daily_refresh.py at the end of the pipeline.

Convention reminder: automation/queue/pending_tasks.json is cron-dirty —
NOT staged or committed. This script mutates it in place.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUEUE_PATH = Path(__file__).resolve().parents[2] / "automation" / "queue" / "pending_tasks.json"
MAX_AGE_DAYS = int(os.environ.get("DRAIN_MAX_AGE_DAYS", 7))
MAX_PER_TYPE = int(os.environ.get("DRAIN_MAX_PER_TYPE", 50))


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # accept Z suffix
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except Exception:
        try:
            return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None


def run():
    if not QUEUE_PATH.exists():
        print(f"[drain_queue] no queue at {QUEUE_PATH}")
        return

    with open(QUEUE_PATH) as f:
        tasks = json.load(f)
    if not isinstance(tasks, list):
        print(f"[drain_queue] unexpected queue type {type(tasks).__name__}; aborting")
        return

    before = len(tasks)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    # Stage 1: drop stale by queued_at
    fresh = []
    dropped_stale = 0
    for t in tasks:
        ts = _parse_ts(t.get("queued_at") or t.get("created_at") or t.get("timestamp"))
        if ts is None or ts >= cutoff:
            fresh.append(t)
        else:
            dropped_stale += 1

    # Stage 2: dedupe (ticker, task) — keep newest
    by_key: dict[tuple[str, str], dict] = {}
    for t in fresh:
        key = (t.get("ticker", ""), t.get("task", ""))
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = t
            continue
        prev_ts = _parse_ts(prev.get("queued_at") or "")
        t_ts = _parse_ts(t.get("queued_at") or "")
        if t_ts and (prev_ts is None or t_ts > prev_ts):
            by_key[key] = t
    deduped = list(by_key.values())
    dropped_dupes = len(fresh) - len(deduped)

    # Stage 3: per-type cap, most recent first
    by_type: dict[str, list[dict]] = {}
    for t in deduped:
        by_type.setdefault(t.get("task", "unknown"), []).append(t)

    capped = []
    dropped_cap = 0
    for task_type, items in by_type.items():
        items.sort(
            key=lambda x: _parse_ts(x.get("queued_at") or "") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        keep = items[:MAX_PER_TYPE]
        dropped_cap += len(items) - len(keep)
        capped.extend(keep)

    capped.sort(
        key=lambda x: _parse_ts(x.get("queued_at") or "") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    with open(QUEUE_PATH, "w") as f:
        json.dump(capped, f, indent=2)

    print(
        f"[drain_queue] {before} -> {len(capped)} "
        f"(stale={dropped_stale}, dupes={dropped_dupes}, capped={dropped_cap}, "
        f"max_age={MAX_AGE_DAYS}d, max_per_type={MAX_PER_TYPE})"
    )


if __name__ == "__main__":
    run()
