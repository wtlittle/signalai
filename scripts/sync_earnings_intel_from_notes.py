"""
Sync earnings_intel.json from the markdown notes in notes/{pre,post}_earnings/.

WHY THIS EXISTS:
  The dashboard's "Earnings Intel" tab reads from earnings_intel.json. The cron
  jobs (automation.jobs.pre_earnings_notes / post_earnings_notes) only write
  flat markdown notes \u2014 they don't touch earnings_intel.json. So every new
  ticker the cron generated a note for ended up invisible in the Earnings
  Intel tab, requiring manual reconciliation. This script closes that loop.

WHAT IT DOES:
  For every ticker with a current entry in earnings_notes_index.json
  (active_pre_earnings or active_post_earnings):

    \u2022 If earnings_intel.json["tickers"][ticker] doesn't exist \u2014 seed it with
      a minimal legacy-style entry: company name, state, inflection_status,
      next/last earnings dates, bottom_line drawn from the note's "Set-up"
      or first 280 chars, and source_metadata.legacy_note_path pointing at
      the markdown file.

    \u2022 If the entry exists but is stale (intel_updated_at older than the
      note's mtime or the index date), refresh state / inflection_status /
      next-or-last date / source_metadata so the UI header stays correct.

    \u2022 Existing rich fields (signal_scorecard, bull_case.pushes_higher,
      tone_drift, etc.) are NEVER overwritten if they have content \u2014 only
      empty/missing fields are filled in. note_diff_injector and
      compute_debate_scores handle the deeper enrichment.

  The result: a ticker shown on the dashboard always has at least a header
  strip + bottom-line in the Earnings Intel tab. The UI's markdown fallback
  in earnings-intel.js handles any remaining gap.

IDEMPOTENT \u2014 safe to run on every cron tick.

Usage:
  python3 scripts/sync_earnings_intel_from_notes.py
  python3 scripts/sync_earnings_intel_from_notes.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "earnings_notes_index.json"
INTEL_PATH = ROOT / "earnings_intel.json"
PRE_DIR = ROOT / "notes" / "pre_earnings"
POST_DIR = ROOT / "notes" / "post_earnings"

TODAY = date.today()
NOW_ISO = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_note(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _extract_company(md: str, fallback: str) -> str:
    """The first line of a note is `# Company (TICKER) \u2014 Pre/Post-Earnings Note`."""
    m = re.match(r"^#\s+(.+?)\s+\([^)]+\)", md)
    return m.group(1).strip() if m else fallback


def _extract_section(md: str, header: str) -> str | None:
    """Return the body of the first `## <header>` section, trimmed."""
    pattern = rf"^##\s+{re.escape(header)}\s*\n(.*?)(?=^##\s|\Z)"
    m = re.search(pattern, md, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return None
    body = m.group(1).strip()
    return body or None


def _first_sentence(text: str, max_chars: int = 280) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    # Cut at sentence boundary if one exists within max_chars
    cut = text[: max_chars + 1]
    m = re.search(r"^(.{40,}?[.!?])\s", cut)
    if m:
        return m.group(1).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "\u2026"


def _bottom_line(md: str, ticker: str, date_str: str, kind: str) -> str:
    """Generate a one-sentence bottom_line from the note body."""
    # Pre-earnings note has a Set-up section; post-earnings has Headline.
    for header in ("Set-up", "Setup", "Headline", "Key Metrics", "Thesis Impact"):
        body = _extract_section(md, header)
        if body:
            return _first_sentence(body)
    # Fallback: first paragraph after the metadata block.
    after_meta = re.sub(r"^.*?---\s*\n", "", md, count=2, flags=re.DOTALL)
    return _first_sentence(after_meta) or f"{ticker} {kind}-earnings note dated {date_str}."


def _state_and_inflection(kind: str, entry: dict) -> tuple[str, str]:
    if kind == "post":
        days = entry.get("day_post", 0)
        if days <= 1:
            return ("post_earnings", "POST")
        return ("post_earnings", "POST")
    # pre
    days = entry.get("days_until", 99)
    if days <= 1:
        return ("pre_earnings", "MID")
    if days <= 7:
        return ("pre_earnings", "PRE")
    return ("pre_earnings", "PRE")


def _entry_signature(entry: dict, kind: str) -> tuple[str, str, str]:
    """Stable signature used to detect when an existing intel entry is stale."""
    return (entry["ticker"], entry.get("date", ""), kind)


def build_intel_for(ticker: str, entry: dict, kind: str, note_path: Path) -> dict:
    md = _read_note(note_path)
    company = _extract_company(md, entry.get("company") or ticker)
    state, inflection = _state_and_inflection(kind, entry)
    rec: dict = {
        "ticker": ticker,
        "company_name": company,
        "state": state,
        "inflection_status": inflection,
        "intel_updated_at": NOW_ISO,
        "refresh_reason": f"synced_from_{kind}_earnings_note",
        "bottom_line": _bottom_line(md, ticker, entry.get("date", ""), kind),
        "bull_case": {"pushes_higher": [], "pushes_lower": []},
        "base_case": {"pushes_higher": [], "pushes_lower": []},
        "bear_case": {"pushes_higher": [], "pushes_lower": []},
        "signal_scorecard": [],
        "source_metadata": {
            "legacy_note_path": str(note_path.relative_to(ROOT)),
            "primary_sources": [],
        },
    }
    if kind == "post":
        rec["last_earnings_date"] = entry.get("date")
    else:
        rec["next_earnings_date"] = entry.get("date")
    return rec


def _is_empty(value) -> bool:
    if value is None or value == "" or value == []:
        return True
    if isinstance(value, dict):
        return all(_is_empty(v) for v in value.values())
    return False


def merge_preserve(existing: dict, fresh: dict) -> dict:
    """Merge `fresh` into `existing`, only filling fields that are empty.

    Refresh `intel_updated_at`, `state`, `inflection_status`, and the
    relevant date field unconditionally so the header stays current; leave
    everything else alone if it has content.
    """
    merged = dict(existing)
    for key, value in fresh.items():
        if key in ("intel_updated_at", "state", "inflection_status",
                   "next_earnings_date", "last_earnings_date", "refresh_reason"):
            merged[key] = value
            continue
        if key not in merged or _is_empty(merged.get(key)):
            merged[key] = value
            continue
        # special-case source_metadata: merge legacy_note_path even if dict exists
        if key == "source_metadata" and isinstance(merged[key], dict):
            for sub_k, sub_v in value.items():
                if sub_k not in merged[key] or _is_empty(merged[key].get(sub_k)):
                    merged[key][sub_k] = sub_v
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = ap.parse_args()

    if not INDEX_PATH.exists():
        print(f"[ERR] {INDEX_PATH} not found \u2014 run scripts/reindex_earnings_notes.py first")
        return

    index = json.loads(INDEX_PATH.read_text())
    intel = json.loads(INTEL_PATH.read_text()) if INTEL_PATH.exists() else {
        "last_updated": NOW_ISO,
        "schema_version": "1.0",
        "tickers": {},
    }
    tickers = intel.setdefault("tickers", {})

    seeded, refreshed, skipped = 0, 0, 0
    seeded_tickers, refreshed_tickers = [], []

    for kind, key in (("pre", "active_pre_earnings"), ("post", "active_post_earnings")):
        for entry in index.get(key, []):
            ticker = entry.get("ticker")
            if not ticker:
                continue
            note_rel = entry.get("file") or entry.get("note_file")
            if not note_rel:
                continue
            note_path = ROOT / note_rel
            if not note_path.exists():
                skipped += 1
                continue

            fresh = build_intel_for(ticker, entry, kind, note_path)
            if ticker not in tickers:
                tickers[ticker] = fresh
                seeded += 1
                seeded_tickers.append(ticker)
            else:
                before = json.dumps(tickers[ticker], sort_keys=True)
                tickers[ticker] = merge_preserve(tickers[ticker], fresh)
                after = json.dumps(tickers[ticker], sort_keys=True)
                if before != after:
                    refreshed += 1
                    refreshed_tickers.append(ticker)
                else:
                    skipped += 1

    intel["last_updated"] = NOW_ISO

    if args.dry_run:
        print(f"[DRY RUN] would seed={seeded} refresh={refreshed} skip={skipped}")
        if seeded_tickers:
            print(f"  seed: {', '.join(sorted(seeded_tickers))}")
        if refreshed_tickers:
            print(f"  refresh: {', '.join(sorted(refreshed_tickers))}")
        return

    INTEL_PATH.write_text(json.dumps(intel, indent=2))
    print(f"[OK] earnings_intel.json synced: seeded={seeded} refreshed={refreshed} skipped={skipped}")
    if seeded_tickers:
        print(f"  seed: {', '.join(sorted(seeded_tickers))}")
    if refreshed_tickers:
        print(f"  refresh: {', '.join(sorted(refreshed_tickers))}")


if __name__ == "__main__":
    main()
