"""
Move tickers from active_pre_earnings to active_post_earnings (and delete
the stale pre-earnings markdown) as soon as their print has happened.

Three triggers for migration:
  1. earnings_calendar.json pre_earnings entry with timing == 'BMO'
     AND days_until == 0  (BMO print today, runs after the 9 AM BMO cron)
  2. earnings_calendar.json pre_earnings entry with timing == 'AMC'
     AND days_until == 0  (AMC print today, runs after the 6:15 PM AMC cron)
  3. earnings_notes_index.json active_pre_earnings entry whose date is
     strictly in the past relative to the calendar's post_earnings list
     (catches anything that slipped through the BMO/AMC same-day window
     because a cron failed or earnings_date was null).

Idempotent: safe to run from every cron tick. Writes earnings_calendar.json
+ earnings_notes_index.json in place. Prints a JSON summary on stdout.

Used by automation/jobs/daily_refresh.py:step_migrate_pre_to_post(); also
runnable directly via:  python scripts/migrate_pre_to_post.py
"""
import json
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
CAL_PATH = ROOT / "earnings_calendar.json"
IDX_PATH = ROOT / "earnings_notes_index.json"
PRE_NOTES_DIR = ROOT / "notes" / "pre_earnings"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _delete_pre_note(ticker: str, earnings_date: str) -> bool:
    """Delete notes/pre_earnings/{TICKER}_{DATE}.md if it exists."""
    target = PRE_NOTES_DIR / f"{ticker}_{earnings_date}.md"
    if target.exists():
        try:
            target.unlink()
            return True
        except OSError:
            return False
    return False


def run() -> dict:
    cal = _load_json(CAL_PATH, {"pre_earnings": [], "post_earnings": []})
    idx = _load_json(IDX_PATH, {"active_pre_earnings": [], "active_post_earnings": []})

    pre_cal = cal.get("pre_earnings", []) or []
    post_cal = cal.get("post_earnings", []) or []
    active_pre = idx.get("active_pre_earnings", []) or []
    active_post = idx.get("active_post_earnings", []) or []

    today = date.today().isoformat()

    # --- Trigger 1 + 2: same-day BMO/AMC reporters in pre with days_until == 0 ---
    moved_from_cal = []
    remain_pre_cal = []
    for e in pre_cal:
        timing = (e.get("timing") or "").upper()
        du = e.get("days_until")
        if timing in ("BMO", "AMC") and du == 0:
            ne = dict(e)
            ne.pop("days_until", None)
            ne["days_since"] = 0
            # If earnings_date is missing, stamp today so downstream code keys correctly.
            if not ne.get("earnings_date"):
                ne["earnings_date"] = today
            moved_from_cal.append(ne)
        else:
            remain_pre_cal.append(e)

    if moved_from_cal:
        # De-duplicate against existing post_cal entries (same ticker+date).
        existing_post_keys = {
            (p.get("ticker"), p.get("earnings_date") or today)
            for p in post_cal
        }
        for m in moved_from_cal:
            key = (m.get("ticker"), m.get("earnings_date") or today)
            if key not in existing_post_keys:
                post_cal.append(m)
                existing_post_keys.add(key)
        cal["pre_earnings"] = remain_pre_cal
        cal["post_earnings"] = post_cal
        CAL_PATH.write_text(json.dumps(cal, indent=2, sort_keys=True) + "\n")

    moved_tickers = {m.get("ticker") for m in moved_from_cal if m.get("ticker")}

    # --- Trigger 3: stale active_pre_earnings entries whose ticker is now in
    # calendar.post_earnings (catches anything that slipped through). ---
    post_ticker_dates = {}
    for p in post_cal:
        t = p.get("ticker")
        d = p.get("earnings_date")
        if t and d:
            post_ticker_dates[t] = d
        elif t:
            # Date missing but ticker is in post — use the active_pre date if we can match
            post_ticker_dates.setdefault(t, None)

    stale_promoted = []
    fresh_active_pre = []
    for entry in active_pre:
        t = entry.get("ticker")
        d = entry.get("date")
        if t in post_ticker_dates:
            # Promote: the print has happened.
            stale_promoted.append({"ticker": t, "date": d or post_ticker_dates.get(t) or today})
            moved_tickers.add(t)
        else:
            fresh_active_pre.append(entry)

    # --- Rebuild active_post_earnings, adding all moved tickers (idempotent) ---
    existing_post_idx = {(x.get("ticker"), x.get("date")): x for x in active_post}
    for m in moved_from_cal:
        t = m.get("ticker")
        d = m.get("earnings_date") or today
        key = (t, d)
        if key not in existing_post_idx:
            existing_post_idx[key] = {
                "ticker": t,
                "company": m.get("company") or m.get("name") or t,
                "date": d,
                "day_post": 0,
                "expires": None,
                "note_file": f"notes/post_earnings/{t}_{d}.md",
            }
    for sp in stale_promoted:
        t = sp["ticker"]
        d = sp["date"]
        key = (t, d)
        if key not in existing_post_idx:
            existing_post_idx[key] = {
                "ticker": t,
                "company": t,
                "date": d,
                "day_post": 0,
                "expires": None,
                "note_file": f"notes/post_earnings/{t}_{d}.md",
            }

    # --- Delete stale pre-earnings markdown for any promoted ticker ---
    deleted_files = []
    for t in moved_tickers:
        # Try the date from active_pre first, then today as fallback.
        candidates = set()
        for entry in active_pre:
            if entry.get("ticker") == t and entry.get("date"):
                candidates.add(entry["date"])
        candidates.add(today)
        for d in candidates:
            if _delete_pre_note(t, d):
                deleted_files.append(f"{t}_{d}.md")

    # --- Write index ---
    idx["active_pre_earnings"] = sorted(fresh_active_pre, key=lambda x: x.get("date", ""))
    idx["active_post_earnings"] = sorted(
        existing_post_idx.values(), key=lambda x: x.get("date", "")
    )
    IDX_PATH.write_text(json.dumps(idx, indent=2, sort_keys=True) + "\n")

    summary = {
        "same_day_pre_to_post": sorted(t for t in {m.get("ticker") for m in moved_from_cal} if t),
        "stale_pre_promoted": sorted(t for t in {sp["ticker"] for sp in stale_promoted} if t),
        "pre_notes_deleted": sorted(deleted_files),
        "active_pre_count": len(idx["active_pre_earnings"]),
        "active_post_count": len(idx["active_post_earnings"]),
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    run()
