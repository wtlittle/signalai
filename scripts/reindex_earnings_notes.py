"""
Rebuild earnings_notes_index.json::active_pre_earnings / active_post_earnings
from the on-disk notes in notes/pre_earnings/ and notes/post_earnings/.

This is safe to re-run at any time. It does NOT touch the archive section
or other top-level keys. Only real (non-stub) notes are indexed.

A note is considered "active" if its earnings_date is within MAX_DAYS of
TODAY (default 14 days) — matching the cron job's window.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "earnings_notes_index.json"
PRE_DIR = ROOT / "notes" / "pre_earnings"
POST_DIR = ROOT / "notes" / "post_earnings"
MAX_DAYS = 14
TODAY = date.today()

STUB_SIZE_BYTES = 1000


def is_stub(path: Path) -> bool:
    if not path.exists() or path.stat().st_size > STUB_SIZE_BYTES:
        return False
    body = path.read_text()
    return body.count("N/A") >= 3 and (
        "| Bull | ? | ? | ? |" in body or body.count("N/A") >= 5
    )


def parse_note(path: Path) -> tuple[str, str, str] | None:
    """Return (ticker, company, earnings_date) parsed from filename + header.

    Filename pattern: {TICKER}_{YYYY-MM-DD}.md (ticker may contain ".", "-").
    """
    stem = path.stem
    ticker, _, edate = stem.rpartition("_")
    if not ticker or not edate or not re.match(r"^\d{4}-\d{2}-\d{2}$", edate):
        return None
    first_line = path.read_text().splitlines()[0] if path.exists() else ""
    m = re.match(r"^#\s+(.+?)\s+\(", first_line)
    company = m.group(1) if m else ticker
    return ticker, company, edate


def main():
    if not INDEX_PATH.exists():
        index = {}
    else:
        index = json.loads(INDEX_PATH.read_text())

    new_pre, new_post = [], []

    for path in sorted(PRE_DIR.glob("*.md")):
        if is_stub(path):
            continue
        parsed = parse_note(path)
        if not parsed:
            continue
        ticker, company, edate = parsed
        try:
            d = date.fromisoformat(edate)
        except ValueError:
            continue
        days_until = (d - TODAY).days
        if days_until < -1 or days_until > MAX_DAYS + 7:
            continue
        new_pre.append({
            "ticker": ticker,
            "company": company,
            "date": edate,
            "days_until": max(days_until, 0),
            "file": f"notes/pre_earnings/{path.name}",
        })

    for path in sorted(POST_DIR.glob("*.md")):
        if is_stub(path):
            continue
        parsed = parse_note(path)
        if not parsed:
            continue
        ticker, company, edate = parsed
        try:
            d = date.fromisoformat(edate)
        except ValueError:
            continue
        days_since = (TODAY - d).days
        if days_since < 0 or days_since > MAX_DAYS + 30:
            continue
        new_post.append({
            "ticker": ticker,
            "company": company,
            "date": edate,
            "day_post": max(days_since, 0),
            "expires": (d + timedelta(days=MAX_DAYS)).isoformat(),
            "note_file": f"notes/post_earnings/{path.name}",
        })

    index["active_pre_earnings"] = sorted(new_pre, key=lambda x: x["date"])
    index["active_post_earnings"] = sorted(new_post, key=lambda x: x["date"])
    index["updated"] = datetime.utcnow().isoformat() + "Z"
    index["last_updated"] = index["updated"]
    INDEX_PATH.write_text(json.dumps(index, indent=2))

    print(f"Re-indexed: active_pre={len(new_pre)} active_post={len(new_post)}")


if __name__ == "__main__":
    main()
