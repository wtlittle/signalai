"""
Universal cache ledger for Perplexity API calls.
Cache key: {ticker}_{date}_{task_type}.json
"""
import json
from datetime import date, datetime
from pathlib import Path
from automation.shared.paths import CACHE_DIR, PRE_EARNINGS_DIR, POST_EARNINGS_DIR


def note_already_exists(ticker: str, earnings_date: str, note_type: str) -> bool:
    """Return True if a note for this ticker/date/type was already generated.

    Checks both the file system and the earnings_notes_index.json.
    A note counts as "existing" only if it has > 500 bytes of content
    (guards against empty/stub files).
    """
    if note_type == "pre":
        note_path = PRE_EARNINGS_DIR / f"{ticker}_{earnings_date}.md"
    else:
        note_path = POST_EARNINGS_DIR / f"{ticker}_{earnings_date}.md"

    if note_path.exists() and note_path.stat().st_size > 500:
        return True

    # Also check earnings_notes_index.json
    from automation.shared.paths import EARNINGS_INDEX
    try:
        index = json.loads(EARNINGS_INDEX.read_text())
        for key in ("active_pre_earnings", "active_post_earnings"):
            for entry in index.get(key, []):
                if entry.get("ticker") == ticker and entry.get("date") == earnings_date:
                    return True
    except Exception:
        pass
    return False


def research_cache_exists(ticker: str, task: str, max_age_hours: int = 20) -> bool:
    """Return True if fresh cached research exists for this ticker/task today."""
    cache_file = CACHE_DIR / f"{ticker}_{date.today().isoformat()}_{task}.json"
    if not cache_file.exists():
        return False
    age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
    return age_hours < max_age_hours


def load_research_cache(ticker: str, task: str) -> dict | None:
    """Load cached research result, or None if not found."""
    cache_file = CACHE_DIR / f"{ticker}_{date.today().isoformat()}_{task}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            return None
    return None


def save_research_cache(ticker: str, task: str, data: dict):
    """Save research result to cache."""
    cache_file = CACHE_DIR / f"{ticker}_{date.today().isoformat()}_{task}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data, indent=2, default=str))


def clear_stale_cache(max_age_days: int = 3):
    """Remove cache files older than max_age_days."""
    cutoff = datetime.now().timestamp() - (max_age_days * 86400)
    removed = 0
    for f in CACHE_DIR.glob("*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        print(f"  [CACHE] Cleared {removed} stale cache files")
