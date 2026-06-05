"""
Backfill archived stub notes with the current sonar prompt, debate-weighted.

Reads notes_archive/_archived_index.json (the durable record of what
archive_stub_notes.py moved aside). For each archived stub it regenerates the
note from the REAL reported actuals already in earnings_intel
(results_vs_consensus, sourced from finnhub/yfinance/perplexity_finance -- never
from the stub) via the legacy single-JSON prompt (build_post_earnings_prompt ->
call_perplexity), then applies the write-guard (is_stub_note) to the fresh
payload:

  * if the new payload is a real note -> write it back to notes/post_earnings/,
    re-add the index entry, remove the entry from the archived index;
  * if there are no real actuals, or sonar STILL returns a stub -> do NOT write.
    Log to backfill_failures.json and leave the note archived. NEVER fabricate.

The legacy single-JSON prompt is used instead of the pipeline's v2
markdown-sections prompt because sonar returns prose for v2, which parses to
{"raw": ...} and trips the write-guard as a false stub. The legacy prompt asks
for one JSON object that maps directly to write_post_earnings_note. The default
model is the non-reasoning sonar-pro: it emits clean JSON fast, avoiding both
the <think>-block token truncation and the 120s timeout that sonar-reasoning-pro
hit on the larger notes.

Prioritization (task mandate): debate_count DESC, then has_reaction DESC
(reaction-stamped == print happened so sonar can ground), then reported_date
DESC (newer == better source availability).

Requires live API access:
    USE_API_FALLBACK=true and PERPLEXITY_API_KEY set, else every call queues
    and nothing is written.

Usage:
    python3 scripts/backfill_stub_notes.py --dry-run
    python3 scripts/backfill_stub_notes.py --limit 20
    python3 scripts/backfill_stub_notes.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.pipeline.stub_detector import is_stub_note  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = ROOT / "notes_archive"
ARCHIVED_INDEX = ARCHIVE_DIR / "_archived_index.json"
INTEL_PATH = ROOT / "earnings_intel.json"
FAILURES_PATH = ROOT / "backfill_failures.json"

SLEEP_S = float(os.environ.get("BACKFILL_SLEEP_S", "1.5"))
BACKFILL_MAX_TOKENS = int(os.environ.get("BACKFILL_MAX_TOKENS", "4000"))
BACKFILL_RETRIES = int(os.environ.get("BACKFILL_RETRIES", "3"))
# The daily pipeline uses sonar-reasoning-pro for post_earnings, but its
# <think> block both burns the token budget (truncating the JSON tail into a
# {"raw": ...} false-stub) and pushes generation past the 120s client timeout.
# For an offline backfill we want clean JSON fast, so default to the
# non-reasoning sonar-pro. Override with BACKFILL_MODEL=... if needed.
BACKFILL_MODEL = os.environ.get("BACKFILL_MODEL", "sonar-pro")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _load_intel_tickers() -> dict:
    if not INTEL_PATH.exists():
        return {}
    try:
        return json.loads(INTEL_PATH.read_text()).get("tickers", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _debate_count(rec: dict) -> int:
    ds = (rec or {}).get("debate_score")
    if isinstance(ds, dict) and isinstance(ds.get("value"), (int, float)):
        return int(ds["value"])
    if isinstance(ds, (int, float)):
        return int(ds)
    return 0


def _has_reaction(rec: dict) -> bool:
    if not rec:
        return False
    if rec.get("reaction_note"):
        return True
    rvc = rec.get("results_vs_consensus") or {}
    return bool(rvc.get("in_quarter_eps_actual") or rvc.get("in_quarter_rev_actual"))


def _fmt_num(v):
    """Render a raw revenue/eps number compactly for the prompt; pass strings through."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        if abs(v) >= 1e9:
            return f"{v/1e9:.3f}B"
        if abs(v) >= 1e6:
            return f"{v/1e6:.1f}M"
        return f"{v:.2f}"
    return str(v)


def _build_actuals(rec: dict) -> dict:
    """Assemble the legacy-prompt ``actuals`` dict from an intel record.

    Pulls real reported figures from results_vs_consensus (sourced from
    finnhub/yfinance/perplexity_finance -- NOT from the stub note) and the
    1-day reaction. Returns {} when no real actuals exist, so the caller can
    record an honest failure rather than fabricate.
    """
    rvc = rec.get("results_vs_consensus") or {}
    rev_actual = rvc.get("in_quarter_rev_actual")
    eps_actual = rvc.get("in_quarter_eps_actual")
    if rev_actual is None and eps_actual is None:
        return {}

    rev_surp = rvc.get("in_quarter_rev_surprise_pct")
    eps_surp = rvc.get("in_quarter_eps_surprise_pct")
    reaction = rec.get("reaction_pct")
    if reaction is None:
        # reaction_note carries the prose; fall back to it if no numeric pct.
        reaction = rec.get("reaction_note")

    def _bm(surp):
        if surp is None:
            return "?"
        return "beat" if surp > 0 else ("miss" if surp < 0 else "in-line")

    return {
        "rev_actual": _fmt_num(rev_actual),
        "rev_est": _fmt_num(rvc.get("in_quarter_rev_consensus") or rvc.get("in_quarter_rev_estimate")),
        "rev_beat_miss": _bm(rev_surp),
        "eps_actual": _fmt_num(eps_actual),
        "eps_est": _fmt_num(rvc.get("in_quarter_eps_consensus") or rvc.get("in_quarter_eps_estimate")),
        "eps_beat_miss": _bm(eps_surp),
        "stock_reaction": (f"{reaction}" if reaction is not None else "?"),
    }


def _record_failure(failures: list, entry: dict, reason: str) -> None:
    failures.append({
        "ticker": entry["ticker"],
        "reported_date": entry["reported_date"],
        "reason": reason,
        "logged_at": _now(),
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the top N by debate-weighted priority")
    args = ap.parse_args()

    if not ARCHIVED_INDEX.exists():
        print("ERROR: notes_archive/_archived_index.json missing -- run archive first")
        sys.exit(1)

    archived = json.loads(ARCHIVED_INDEX.read_text())
    entries = [e for e in archived.get("entries", []) if "post_earnings" in e.get("original_path", "")]
    pre_only = [e for e in archived.get("entries", []) if "post_earnings" not in e.get("original_path", "")]
    if pre_only:
        print(f"Skipping {len(pre_only)} pre-earnings archived notes "
              f"(backfill targets post-earnings only): "
              f"{[e['ticker'] for e in pre_only]}")

    intel = _load_intel_tickers()
    for e in entries:
        rec = intel.get(e["ticker"], {})
        e["_debate"] = _debate_count(rec)
        e["_reaction"] = _has_reaction(rec)

    # Priority: debate DESC, reaction-stamped first, reported_date DESC.
    entries.sort(key=lambda e: e["reported_date"], reverse=True)
    entries.sort(key=lambda e: (-e["_debate"], not e["_reaction"]))

    if args.limit is not None:
        entries = entries[:args.limit]

    print(f"Backfill candidates: {len(entries)} (dry-run={args.dry_run}, "
          f"limit={args.limit})")
    print(f"  {'TICKER':<8}{'DEBATE':>7}{'REACT':>7}  REPORTED")
    for e in entries:
        print(f"  {e['ticker']:<8}{e['_debate']:>7}{'yes' if e['_reaction'] else 'no':>7}  "
              f"{e['reported_date']}")

    if args.dry_run:
        print("\n(dry-run -- no API calls, no writes)")
        return

    # Lazy imports: only needed for the live run.
    from automation.shared.tickers import load_common_names
    from automation.perplexity.client import call_perplexity
    from automation.perplexity.prompts import build_post_earnings_prompt
    from automation.jobs.post_earnings_notes import write_post_earnings_note, update_index

    names = load_common_names()
    intel_full = _load_intel_tickers()
    failures: list = []
    succeeded = 0
    still_stub = 0
    today = date.today()

    remaining_archive = list(archived.get("entries", []))

    for e in entries:
        ticker = e["ticker"]
        edate = e["reported_date"]
        company = names.get(ticker, ticker)
        try:
            days_since = (today - date.fromisoformat(edate)).days
        except ValueError:
            days_since = 0

        print(f"\n[BACKFILL] {ticker} {edate} (debate={e['_debate']}, "
              f"reaction={e['_reaction']})")

        # Build the legacy single-JSON prompt from the REAL reported actuals
        # already in earnings_intel (results_vs_consensus, sourced from
        # finnhub/yfinance/perplexity_finance -- never from the stub). The v2
        # markdown-sections prompt was abandoned here: sonar returns prose for
        # it, which parses to {"raw": ...} and trips the write-guard as a false
        # stub. The legacy prompt asks for ONE JSON object that maps directly to
        # write_post_earnings_note.
        rec = intel_full.get(ticker, {})
        actuals = _build_actuals(rec)
        if not actuals:
            print(f"  [NO-ACTUALS] {ticker}: no reported figures in intel -- left archived")
            _record_failure(failures, e, "no_real_actuals_in_intel")
            still_stub += 1
            continue

        pre_context = {
            "setup": (rec.get("bottom_line") or {}).get("set_up")
            if isinstance(rec.get("bottom_line"), dict) else None,
            "key_debates": [],
        }
        user_prompt = build_post_earnings_prompt(ticker, company, edate, actuals, pre_context)

        # A transient API failure (read timeout, 5xx) must fail only THIS
        # ticker, not abort the sweep. Retry on timeout with backoff.
        result = None
        last_exc = None
        for attempt in range(1, BACKFILL_RETRIES + 1):
            try:
                result = call_perplexity(
                    ticker, "post_earnings", user_prompt,
                    max_tokens=BACKFILL_MAX_TOKENS, force=True,
                    extra_meta={"model": BACKFILL_MODEL},
                )
                break
            except Exception as exc:
                last_exc = exc
                print(f"  [RETRY] {ticker} attempt {attempt}/{BACKFILL_RETRIES} "
                      f"failed: {type(exc).__name__}; backing off")
                time.sleep(SLEEP_S * attempt * 2)
        if result is None:
            print(f"  [API-ERROR] {ticker}: {type(last_exc).__name__}: {last_exc} -- left archived")
            _record_failure(failures, e, f"api_error: {type(last_exc).__name__}: {last_exc}")
            still_stub += 1
            continue

        if not result or result.get("queued") or result.get("dry_run") or result.get("skipped"):
            reason = "no_api_result"
            if result:
                reason = next((k for k in ("queued", "dry_run", "skipped") if result.get(k)), reason)
            print(f"  [NO-RESULT] {ticker}: {reason} -- left archived")
            _record_failure(failures, e, reason)
            still_stub += 1
            time.sleep(SLEEP_S)
            continue

        # WRITE-GUARD: same logic as the runtime pipeline.
        if is_stub_note(result):
            print(f"  [STILL-STUB] {ticker}: sonar returned a stub -- left archived")
            _record_failure(failures, e, "regenerated_payload_is_stub")
            still_stub += 1
            time.sleep(SLEEP_S)
            continue

        write_post_earnings_note(ticker, company, edate, days_since, result)
        update_index(ticker, company, edate, days_since)
        succeeded += 1
        print(f"  [OK] {ticker}: real note written")

        remaining_archive = [
            a for a in remaining_archive
            if not (a["ticker"] == ticker and a["reported_date"] == edate)
        ]
        # Persist progress incrementally so a later crash never loses a note
        # that was already written + de-archived.
        archived["entries"] = remaining_archive
        archived["count"] = len(remaining_archive)
        archived["last_updated"] = _now()
        _atomic_write(ARCHIVED_INDEX, archived)
        time.sleep(SLEEP_S)

    archived["entries"] = remaining_archive
    archived["count"] = len(remaining_archive)
    archived["last_updated"] = _now()
    _atomic_write(ARCHIVED_INDEX, archived)

    existing_failures = []
    if FAILURES_PATH.exists():
        try:
            existing_failures = json.loads(FAILURES_PATH.read_text()).get("failures", [])
        except json.JSONDecodeError:
            existing_failures = []
    _atomic_write(FAILURES_PATH, {
        "last_updated": _now(),
        "count": len(existing_failures) + len(failures),
        "failures": existing_failures + failures,
    })

    print(f"\nBackfill complete: {succeeded} succeeded, {still_stub} still-stub/failed "
          f"(left archived).")


if __name__ == "__main__":
    main()
