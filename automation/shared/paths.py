"""
Repo-relative path constants for all automation scripts.
No absolute paths — works on any machine that clones the repo.
"""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]  # repo root (watchlist-app/)

# --- Notes ---
NOTES_DIR         = ROOT_DIR / "notes"
PRE_EARNINGS_DIR  = NOTES_DIR / "pre_earnings"
POST_EARNINGS_DIR = NOTES_DIR / "post_earnings"
ARCHIVE_DIR       = ROOT_DIR / "archive"
ARCHIVE_PRE_DIR   = ARCHIVE_DIR / "pre_earnings"
ARCHIVE_POST_DIR  = ARCHIVE_DIR / "post_earnings"

# --- Data ---
DATA_DIR       = ROOT_DIR / "data"
CACHE_DIR      = DATA_DIR / "cache"
RESEARCH_DIR   = DATA_DIR / "research"

# --- JSON config files ---
EARNINGS_INDEX    = ROOT_DIR / "earnings_notes_index.json"
EARNINGS_CALENDAR = ROOT_DIR / "earnings_calendar.json"
EARNINGS_DATA     = ROOT_DIR / "earnings_data.json"
WEEKLY_BRIEFING   = ROOT_DIR / "weekly_briefing.json"
MACRO_DATA        = ROOT_DIR / "macro_data.json"
DATA_SNAPSHOT     = ROOT_DIR / "data-snapshot.json"
UTILS_JS          = ROOT_DIR / "utils.js"

# --- Ensure directories exist ---
for d in [PRE_EARNINGS_DIR, POST_EARNINGS_DIR, ARCHIVE_PRE_DIR,
          ARCHIVE_POST_DIR, CACHE_DIR, RESEARCH_DIR]:
    d.mkdir(parents=True, exist_ok=True)
