"""
Detect stub earnings notes across notes/{pre,post}_earnings/ and write an audit.

A stub is a note that was generated BEFORE the print landed -- it carries
not-yet-reported prose, Beat/Miss = N/A, and null metrics, yet was persisted to
disk and now renders as a broken dashboard card (e.g. OKTA 2026-05-28).

Detection logic lives in automation/pipeline/stub_detector.py so this audit and
the runtime write-guard share one definition. This script:
  * scans every .md note,
  * enriches each with debate_count + has_reaction from earnings_intel.json,
  * classifies via classify_note_text,
  * writes stub_notes_audit.json to the repo root (atomic write).

Usage:
    python3 scripts/detect_stub_notes.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.pipeline.stub_detector import classify_note_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PRE_DIR = ROOT / "notes" / "pre_earnings"
POST_DIR = ROOT / "notes" / "post_earnings"
INTEL_PATH = ROOT / "earnings_intel.json"
AUDIT_PATH = ROOT / "stub_notes_audit.json"

TODAY = date.today()


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _load_intel() -> dict:
    if not INTEL_PATH.exists():
        return {}
    try:
        return json.loads(INTEL_PATH.read_text()).get("tickers", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _debate_count(rec: dict) -> int:
    """Extract a debate count from a ticker's intel record.

    Primary source is debate_score.value (the daily debate-scoring job). Falls
    back to len(debates) if a raw debates array is present, else 0.
    """
    if not rec:
        return 0
    ds = rec.get("debate_score")
    if isinstance(ds, dict) and isinstance(ds.get("value"), (int, float)):
        return int(ds["value"])
    if isinstance(ds, (int, float)):
        return int(ds)
    debates = rec.get("debates")
    if isinstance(debates, list):
        return len(debates)
    return 0


def _has_reaction(rec: dict) -> bool:
    """True when the print already happened (reaction computed from prices)."""
    if not rec:
        return False
    if rec.get("reaction_note"):
        return True
    rvc = rec.get("results_vs_consensus") or {}
    if isinstance(rvc, dict) and (
        rvc.get("in_quarter_eps_actual") or rvc.get("in_quarter_rev_actual")
    ):
        return True
    for k in ("reaction_pct", "reactionPct", "day_after_pct"):
        if rec.get(k) is not None:
            return True
    return False


def _parse_stem(stem: str) -> tuple[str, str]:
    ticker, _, edate = stem.rpartition("_")
    return ticker, edate


def main() -> None:
    intel = _load_intel()

    scanned = 0
    stubs: list[dict] = []
    by_ticker: dict[str, int] = {}
    fallback_debate = False

    for note_dir in (POST_DIR, PRE_DIR):
        if not note_dir.exists():
            continue
        for note_path in sorted(note_dir.glob("*.md")):
            scanned += 1
            ticker, edate = _parse_stem(note_path.stem)
            rec = intel.get(ticker, {})
            has_reaction = _has_reaction(rec)
            debate_count = _debate_count(rec)
            if debate_count == 0 and "debate_score" not in rec:
                fallback_debate = True

            text = note_path.read_text()
            is_stub, reasons = classify_note_text(
                text,
                reported_date=edate,
                today=TODAY,
                has_reaction=has_reaction,
            )
            if is_stub:
                stubs.append({
                    "ticker": ticker,
                    "path": str(note_path.relative_to(ROOT)),
                    "reported_date": edate,
                    "reasons": reasons,
                    "has_reaction": has_reaction,
                    "debate_count": debate_count,
                })
                by_ticker[ticker] = by_ticker.get(ticker, 0) + 1

    audit = {
        "generated_at": TODAY.isoformat(),
        "scanned": scanned,
        "stub_count": len(stubs),
        "non_stubs": scanned - len(stubs),
        "debate_count_fallback_used": fallback_debate,
        "stubs": sorted(
            stubs,
            key=lambda s: (-s["debate_count"], not s["has_reaction"], s["reported_date"]),
        ),
        "by_ticker": dict(sorted(by_ticker.items(), key=lambda kv: -kv[1])),
    }
    _atomic_write(AUDIT_PATH, audit)

    print(f"Scanned {scanned} notes -- {len(stubs)} stubs, {scanned - len(stubs)} clean")
    if fallback_debate:
        print("NOTE: some tickers had no debate_score; debate_count fell back to 0 for those.")
    print(f"Wrote {AUDIT_PATH.relative_to(ROOT)}")

    top = sorted(stubs, key=lambda s: (-s["debate_count"], s["reported_date"]))[:20]
    if top:
        print("\nTop 20 most-debated stub tickers:")
        print(f"  {'TICKER':<10}{'DEBATE':>7}  {'REPORTED':<12}{'REACT':<6} REASONS")
        for s in top:
            print(
                f"  {s['ticker']:<10}{s['debate_count']:>7}  "
                f"{s['reported_date']:<12}{'yes' if s['has_reaction'] else 'no':<6} "
                f"{','.join(s['reasons'])}"
            )


if __name__ == "__main__":
    main()
