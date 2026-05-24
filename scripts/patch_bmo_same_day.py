import json
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
cal_path = ROOT / "earnings_calendar.json"
idx_path = ROOT / "earnings_notes_index.json"
notes_pre_dir = ROOT / "notes" / "pre_earnings"

cal = json.loads(cal_path.read_text())
idx = json.loads(idx_path.read_text())

pre = cal.get("pre_earnings", [])
post = cal.get("post_earnings", [])

moved = []
remain_pre = []
for e in pre:
    if (e.get("timing") == "BMO") and (e.get("days_until") == 0):
        ne = dict(e)
        ne.pop("days_until", None)
        ne["days_since"] = 0
        moved.append(ne)
    else:
        remain_pre.append(e)

if moved:
    cal["pre_earnings"] = remain_pre
    cal["post_earnings"] = moved + post
    cal_path.write_text(json.dumps(cal, indent=2, sort_keys=True) + "\n")

# earnings_notes_index.json stores active_* as arrays of dicts
active_pre = idx.get("active_pre_earnings", []) or []
active_post = idx.get("active_post_earnings", []) or []

moved_tickers = {e.get("ticker") for e in moved if e.get("ticker")}

if moved_tickers:
    active_pre = [x for x in active_pre if x.get("ticker") not in moved_tickers]

    existing_post = {x.get("ticker"): x for x in active_post if x.get("ticker")}
    for e in moved:
        t = e.get("ticker")
        if not t:
            continue
        # if already present in post, don't duplicate
        if t in existing_post:
            continue
        # build minimal entry
        existing_post[t] = {
            "ticker": t,
            "company": e.get("company") or t,
            "date": e.get("earnings_date") or date.today().isoformat(),
            "day_post": 0,
            "expires": None,
            "note_file": f"notes/post_earnings/{t}_{(e.get('earnings_date') or date.today().isoformat())}.md",
        }

    active_post = list(existing_post.values())

    # delete pre-earnings note(s) for today for this ticker
    today = date.today().isoformat()
    for t in moved_tickers:
        for p in notes_pre_dir.glob(f"{t}_{today}*.md"):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

idx["active_pre_earnings"] = sorted(active_pre, key=lambda x: (x.get("ticker") or ""))
idx["active_post_earnings"] = sorted(active_post, key=lambda x: (x.get("ticker") or ""))
idx_path.write_text(json.dumps(idx, indent=2, sort_keys=True) + "\n")

print(json.dumps({
    "bmo_same_day_detected_pre_patch": sorted([t for t in moved_tickers if t]),
    "bmo_same_day_moved_to_post": sorted([t for t in moved_tickers if t])
}))
