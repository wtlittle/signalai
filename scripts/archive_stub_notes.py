"""
Soft-archive stub notes identified by detect_stub_notes.py.

Reads stub_notes_audit.json. For each stub:
  * MOVES (never deletes) the note from notes/<sub>/ to
    notes_archive/<sub>/ preserving the relative layout,
  * removes its entry from earnings_notes_index.json,
  * records the move in notes_archive/_archived_index.json with a timestamp,
  * nulls the stub-sourced narrative fields in earnings_intel.json
    (headline/beat_miss_quality/key_metrics/...) so the dashboard stops
    rendering stale stub prose. reaction_pct / results_vs_consensus are
    KEPT -- they are computed independently from prices, not from the stub.

All writes are atomic (temp file + os.replace). Supports --dry-run.

Usage:
    python3 scripts/archive_stub_notes.py --dry-run
    python3 scripts/archive_stub_notes.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "stub_notes_audit.json"
INDEX_PATH = ROOT / "earnings_notes_index.json"
INTEL_PATH = ROOT / "earnings_intel.json"
ARCHIVE_DIR = ROOT / "notes_archive"
ARCHIVED_INDEX = ARCHIVE_DIR / "_archived_index.json"

# Narrative fields written from the (stub) note. Nulled on archive so the
# dashboard stops showing not-yet-reported prose. NOTE: reaction_pct,
# reaction_note, results_vs_consensus, guide_vs_consensus, history_8q are
# intentionally NOT in this list -- they are sourced independently.
STUB_SOURCED_FIELDS = (
    "headline",
    "beat_miss_quality",
    "key_metrics",
    "guidance_text",
    "surprises",
    "analyst_reactions",
    "post_earnings_review",
    "bottom_line",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not AUDIT_PATH.exists():
        print("ERROR: stub_notes_audit.json missing -- run detect_stub_notes.py first")
        sys.exit(1)

    audit = json.loads(AUDIT_PATH.read_text())
    stubs = audit.get("stubs", [])
    print(f"{len(stubs)} stubs to archive (dry-run={args.dry_run})")

    index = json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else {}
    intel = json.loads(INTEL_PATH.read_text()) if INTEL_PATH.exists() else {}
    tickers = intel.get("tickers", {})

    archived_index = {}
    if ARCHIVED_INDEX.exists():
        try:
            archived_index = json.loads(ARCHIVED_INDEX.read_text())
        except json.JSONDecodeError:
            archived_index = {}
    archived_index.setdefault("entries", [])

    stub_keys = {(s["ticker"], s["reported_date"]) for s in stubs}

    moved = 0
    intel_cleaned = 0
    for s in stubs:
        src = ROOT / s["path"]
        rel = Path(s["path"])
        # notes/post_earnings/X.md -> notes_archive/post_earnings/X.md
        dest = ARCHIVE_DIR / rel.relative_to("notes")

        if not src.exists():
            print(f"  [MISS] {rel} already gone -- skipping move")
        else:
            print(f"  [MOVE] {rel} -> {dest.relative_to(ROOT)}")
            if not args.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                archived_index["entries"].append({
                    "ticker": s["ticker"],
                    "reported_date": s["reported_date"],
                    "original_path": str(rel),
                    "archived_path": str(dest.relative_to(ROOT)),
                    "reasons": s.get("reasons", []),
                    "archived_at": _now(),
                })
            moved += 1

        rec = tickers.get(s["ticker"])
        if rec:
            changed = False
            for f in STUB_SOURCED_FIELDS:
                if rec.get(f) not in (None, [], ""):
                    if not args.dry_run:
                        rec[f] = None
                    changed = True
            if changed:
                if not args.dry_run:
                    rec["note_status"] = "archived_stub"
                    rec["intel_cleaned_at"] = _now()
                intel_cleaned += 1
                print(f"  [INTEL] {s['ticker']} stub fields nulled (reaction kept)")

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
    pruned = (pre_before - len(index["active_pre_earnings"])) + \
             (post_before - len(index["active_post_earnings"]))

    print(f"\nWould move {moved} notes, clean {intel_cleaned} intel records, "
          f"prune {pruned} index entries")

    if args.dry_run:
        print("(dry-run -- no files modified)")
        return

    archived_index["last_updated"] = _now()
    archived_index["count"] = len(archived_index["entries"])
    _atomic_write(ARCHIVED_INDEX, archived_index)

    index["last_updated"] = _now()
    _atomic_write(INDEX_PATH, index)

    intel["last_updated"] = _now()
    _atomic_write(INTEL_PATH, intel)

    print(f"\nArchived {moved} notes; pruned {pruned} index entries; "
          f"cleaned {intel_cleaned} intel records.")


if __name__ == "__main__":
    main()
