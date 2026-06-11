"""
Post-run audit: log weekly-briefing section counts to the cron tracking log.

Intended to run as a cron step AFTER weekly_briefing.py has written
weekly_briefing.json, so Monday-morning monitoring can confirm the three
regression-prone sections (watchlist_updates / upcoming_catalysts /
sector_summary) shipped at or above their minimum counts.

Usage:
    python -m automation.jobs.audit_briefing_sections
    python -m automation.jobs.audit_briefing_sections --input weekly_briefing.json

Exit code is 0 even when sections are short — this is a monitoring step, not a
gate. The log line carries [OK]/[WARN] so a tail/grep can surface failures.
"""
import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import WEEKLY_BRIEFING

SECTION_QUALITY_LOG = Path("/home/user/workspace/cron_tracking/4277e158/section_quality.log")

SECTION_MINIMUMS = {
    "watchlist_updates": 20,
    "upcoming_catalysts": 6,
    "sector_summary": 8,
}


def _count(briefing: dict, section: str) -> int:
    v = briefing.get(section)
    if isinstance(v, (list, dict)):
        return len(v)
    return 0


def audit(input_path: Path) -> int:
    try:
        briefing = json.loads(Path(input_path).read_text())
    except Exception as exc:
        sys.stderr.write(f"ERROR: cannot read briefing {input_path}: {exc}\n")
        return 2

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    week = briefing.get("week_ending") or date.today().isoformat()

    SECTION_QUALITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SECTION_QUALITY_LOG, "a") as fh:
        for section, minimum in SECTION_MINIMUMS.items():
            count = _count(briefing, section)
            level = "OK" if count >= minimum else "WARN"
            line = (f"{ts} [{level}] week={week} stage=audit "
                    f"{section}={count} (min {minimum})")
            fh.write(line + "\n")
            print(line)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Audit weekly-briefing section counts")
    parser.add_argument("--input", "-i", default=str(WEEKLY_BRIEFING),
                        help="Path to the generated weekly_briefing.json")
    args = parser.parse_args()
    sys.exit(audit(Path(args.input)))


if __name__ == "__main__":
    main()
