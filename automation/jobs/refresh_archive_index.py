"""
Canonical weekly-briefing archive index.

Writes ``weekly_briefing_archive_index.json`` at the repo root: a flat, sorted
list of every archived weekly briefing plus the current live one. The front-end
fetches THIS file independently to build the week picker, instead of trusting
the ``archive[]`` array baked into an individual briefing payload (those are
snapshots frozen at generation time and go stale the moment a newer week ships,
which is what broke arrow navigation).

Each entry:
    {
      "week_ending": "2026-05-31",
      "file": "archive/briefings/weekly_briefing_2026-05-31.json",
      "week_label": "Week ending May 31, 2026",
      "tldr_excerpt": "S&P 500 +1.2% ..."   # first TL;DR bullet, or null
    }

Run standalone or import ``refresh_archive_index()``. Called by both the weekly
briefing job and the daily refresh so the index is always current.

    python -m automation.jobs.refresh_archive_index
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import (
    ROOT_DIR,
    WEEKLY_BRIEFING,
    WEEKLY_ARCHIVE_INDEX,
    ARCHIVE_BRIEFINGS_DIR,
)
from automation.shared.io_helpers import write_json

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _week_label(week_ending: str) -> str:
    """'2026-05-31' -> 'Week ending May 31, 2026' (falls back to the raw date)."""
    try:
        d = date.fromisoformat(week_ending)
        return f"Week ending {_MONTHS[d.month - 1]} {d.day}, {d.year}"
    except (ValueError, IndexError):
        return f"Week ending {week_ending}"


def _tldr_excerpt(briefing: dict) -> str | None:
    """First TL;DR bullet (or first market-summary sentence) as a short teaser."""
    tldr = briefing.get("tl_dr")
    if isinstance(tldr, list) and tldr:
        first = tldr[0]
        if isinstance(first, str) and first.strip():
            return _clip(first.strip())
    if isinstance(tldr, dict):
        bullets = tldr.get("bullets")
        if isinstance(bullets, list) and bullets and isinstance(bullets[0], str):
            return _clip(bullets[0].strip())
    ms = briefing.get("market_summary")
    if isinstance(ms, str) and ms.strip():
        sentence = ms.strip().split(". ")[0]
        return _clip(sentence)
    return None


def _clip(s: str, n: int = 140) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def build_index() -> list[dict]:
    """Scan the archive directory + live file and return sorted index entries."""
    entries: dict[str, dict] = {}

    for f in sorted(ARCHIVE_BRIEFINGS_DIR.glob("weekly_briefing_????-??-??.json")):
        week_str = f.stem.replace("weekly_briefing_", "")
        try:
            date.fromisoformat(week_str)
        except ValueError:
            continue
        if f.stat().st_size <= 500:
            continue
        briefing = _read_json(f) or {}
        entries[week_str] = {
            "week_ending": week_str,
            "file": f"archive/briefings/weekly_briefing_{week_str}.json",
            "week_label": _week_label(week_str),
            "tldr_excerpt": _tldr_excerpt(briefing),
        }

    # Fold in the current live briefing. If its week is already archived, prefer
    # the archived file path (stable URL); otherwise point at the live file.
    live = _read_json(WEEKLY_BRIEFING) or {}
    live_week = live.get("week_ending")
    if live_week:
        if live_week not in entries:
            entries[live_week] = {
                "week_ending": live_week,
                "file": "weekly_briefing.json",
                "week_label": _week_label(live_week),
                "tldr_excerpt": _tldr_excerpt(live),
            }
        elif entries[live_week].get("tldr_excerpt") is None:
            entries[live_week]["tldr_excerpt"] = _tldr_excerpt(live)

    return [entries[k] for k in sorted(entries)]


def refresh_archive_index() -> list[dict]:
    """Build and write the canonical archive index. Returns the entries."""
    ARCHIVE_BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    entries = build_index()
    payload = {
        "generated": date.today().isoformat(),
        "count": len(entries),
        "weeks": entries,
    }
    write_json(WEEKLY_ARCHIVE_INDEX, payload)
    print(
        f"  [archive-index] wrote {WEEKLY_ARCHIVE_INDEX.relative_to(ROOT_DIR)} "
        f"with {len(entries)} weeks "
        f"({entries[0]['week_ending']} -> {entries[-1]['week_ending']})"
        if entries else "  [archive-index] wrote empty index"
    )
    return entries


if __name__ == "__main__":
    refresh_archive_index()
