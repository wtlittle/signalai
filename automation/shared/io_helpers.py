"""
File read/write helpers with deterministic naming.
"""
import json
from datetime import date
from pathlib import Path
from automation.shared.paths import RESEARCH_DIR, EARNINGS_INDEX, EARNINGS_CALENDAR


def read_json(path: Path) -> dict:
    """Read a JSON file, return empty dict on failure."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def write_json(path: Path, data: dict):
    """Write JSON with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def research_path(task: str, fmt: str = "json") -> Path:
    """Deterministic research output path: data/research/{YYYY-MM-DD}_{task}.{fmt}"""
    return RESEARCH_DIR / f"{date.today().isoformat()}_{task}.{fmt}"


def load_earnings_index() -> dict:
    return read_json(EARNINGS_INDEX)


def save_earnings_index(data: dict):
    write_json(EARNINGS_INDEX, data)


def load_earnings_calendar() -> dict:
    return read_json(EARNINGS_CALENDAR)


def save_earnings_calendar(data: dict):
    write_json(EARNINGS_CALENDAR, data)
