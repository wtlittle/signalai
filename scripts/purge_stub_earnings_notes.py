"""
One-shot cleanup: remove placeholder earnings notes that were created when
call_perplexity returned {"queued": True} (no API key) and the orchestrator
mistook that for a real result. Those stubs are characterized by:
  - file size <= STUB_SIZE_BYTES (default 500)
  - body is just N/A scaffolding with no real content

Also strips the corresponding entry from earnings_notes_index.json so the
next cron run can regenerate the note from scratch.

Usage:
    python3 scripts/purge_stub_earnings_notes.py --apply
    python3 scripts/purge_stub_earnings_notes.py            # dry-run
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "earnings_notes_index.json"
PRE_DIR = ROOT / "notes" / "pre_earnings"
POST_DIR = ROOT / "notes" / "post_earnings"

# Real notes are always >2,000 bytes; observed stubs are 400-600 bytes.
# Use a generous safety margin (1,000) AND require the N/A scaffold signature.
STUB_SIZE_BYTES = 1000


def is_stub(path: Path) -> bool:
    """A stub is a placeholder note with no real content.

    Stubs are recognized by:
      - file size <= STUB_SIZE_BYTES (real notes are always > 2 KB), AND
      - body contains the canonical N/A scaffold across multiple sections.

    Pre-earnings stubs have 3-5 N/A sentinels plus an all-? scenario grid;
    post-earnings stubs have 5-7 N/A sentinels. Counting any N/A token is
    the safest signal: real notes never use the literal string "N/A".
    """
    if not path.exists():
        return False
    if path.stat().st_size > STUB_SIZE_BYTES:
        return False
    body = path.read_text()
    na_count = body.count("N/A")
    # Pre-earnings stubs: at least 3 N/A; post-earnings stubs: at least 5.
    # The scenario-grid "?" pattern is an additional pre-earnings tell.
    has_blank_scenario_grid = "| Bull | ? | ? | ? |" in body
    return na_count >= 3 and (has_blank_scenario_grid or na_count >= 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually delete files and update the index")
    args = ap.parse_args()

    removed_files: list[Path] = []
    for note_dir in (PRE_DIR, POST_DIR):
        for note_path in sorted(note_dir.glob("*.md")):
            if is_stub(note_path):
                removed_files.append(note_path)

    print(f"Identified {len(removed_files)} stub notes:")
    for p in removed_files:
        print(f"  - {p.relative_to(ROOT)}  ({p.stat().st_size} bytes)")

    # Build the deletion set keyed by (ticker, date).
    stub_keys: set[tuple[str, str]] = set()
    for p in removed_files:
        stem = p.stem  # e.g. NOW_2026-04-22
        # Split on the LAST underscore to support tickers like CSU.TO
        ticker, _, edate = stem.rpartition("_")
        if ticker and edate:
            stub_keys.add((ticker, edate))

    index = json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else {}
    pre_before = len(index.get("active_pre_earnings", []))
    post_before = len(index.get("active_post_earnings", []))

    index["active_pre_earnings"] = [
        e for e in index.get("active_pre_earnings", [])
        if (e.get("ticker"), e.get("date")) not in stub_keys
    ]
    index["active_post_earnings"] = [
        e for e in index.get("active_post_earnings", [])
        if (e.get("ticker"), e.get("date")) not in stub_keys
    ]
    pre_after = len(index["active_pre_earnings"])
    post_after = len(index["active_post_earnings"])

    print(f"\nIndex entries to remove: pre={pre_before - pre_after}, post={post_before - post_after}")

    if not args.apply:
        print("\n(dry run \u2014 re-run with --apply to actually delete files and write the index)")
        return

    for p in removed_files:
        p.unlink()
    index["last_updated"] = datetime.utcnow().isoformat() + "Z"
    INDEX_PATH.write_text(json.dumps(index, indent=2))
    print(f"\nDeleted {len(removed_files)} stub notes and pruned earnings_notes_index.json.")


if __name__ == "__main__":
    main()
