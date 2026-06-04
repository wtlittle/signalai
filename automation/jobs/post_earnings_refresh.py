"""
Post-AMC earnings refresh.

Runs after the prior day's after-market-close reports have settled. Catches
tickers that have just reported but are still parked in pre_earnings, flips
them, and backfills their POST card data from the data layer:

  Finnhub EPS triplet -> yfinance revenue -> Perplexity sonar rev consensus
  -> Perplexity sonar FY guide.

Then resyncs earnings_intel.json, rebuilds earnings_calendar.json, verifies
completeness, and commits + pushes the result.

Idempotent: a run with no newly-flipped POST tickers performs no backfills and
no commit. Use --ticker T to force a single ticker through the backfill chain
on demand (e.g. to flip PANW / GTLB by hand).

DATA INTEGRITY: this job only orchestrates the existing backfill scripts; it
never writes REV/EPS values itself. Missing data stays None -> renders "n/a".
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR
from automation.jobs.daily_refresh import self_heal_state_machine, step_sync_earnings_intel
from automation.pipeline.refresh_ticker import refresh_ticker

LOG_DIR = ROOT_DIR / "cron_tracking" / "post_earnings_refresh"
LOG_RETENTION = 30


def _log(msg, fh=None):
    print(msg)
    if fh is not None:
        fh.write(msg + "\n")
        fh.flush()


def _run(cmd, fh=None):
    """Run a subprocess from the repo root, streaming a one-line summary."""
    _log(f"  $ {' '.join(cmd)}", fh)
    result = subprocess.run(
        cmd, cwd=str(ROOT_DIR), capture_output=True, text=True
    )
    tail = (result.stdout or "").strip().splitlines()[-5:]
    for line in tail:
        _log(f"    {line}", fh)
    if result.returncode != 0:
        err = (result.stderr or "").strip().splitlines()[-5:]
        for line in err:
            _log(f"    [stderr] {line}", fh)
        _log(f"  [WARN] exit {result.returncode}: {' '.join(cmd)}", fh)
    return result.returncode


def _git_pull(fh=None):
    _run(["git", "pull", "--rebase", "--autostash"], fh)


def refresh_one(ticker, fh=None):
    """Refresh a single ticker through the per-ticker pipeline.

    Replaces the old per-field backfill chain + history_8q repair + auto-re-pull.
    The pipeline (automation/pipeline/refresh_ticker) now owns the entire source
    chain (FactSet -> Finnhub -> yfinance -> Perplexity), the schema gate, and
    quarantine -- so a single call both populates the record and, if it cannot be
    completed, routes the ticker to quarantine.json without ever writing a partial
    record. Re-running this for one ticker is the standard recovery path, so the
    old "re-pull once" loop is subsumed by simply calling it.
    """
    _log(f"\n  --- pipeline refresh for {ticker} ---", fh)
    res = refresh_ticker(ticker, force=True)
    if res.ok:
        _log(f"  [PIPELINE] {ticker}: complete via {res.last_source} "
             f"(written={res.written}).", fh)
    elif res.quarantined:
        _log(f"  [PIPELINE] {ticker}: QUARANTINED -- missing "
             f"{', '.join(res.missing_fields)}. Prior good data preserved; "
             f"renders n/a, never fabricated. See quarantine.json.", fh)
    else:
        _log(f"  [PIPELINE] {ticker}: {res.note} (no record written).", fh)
    return res.ok


def _commit_and_push(fh=None):
    """Stage the data outputs (NOT pending_tasks.json) and push if changed."""
    paths = [
        "earnings_intel.json",
        "earnings_calendar.json",
        "notes/post_earnings/",
        "earnings_notes_index.json",
    ]
    _run(["git", "add", *paths], fh)
    staged = subprocess.run(
        ["git", "diff", "--staged", "--quiet"], cwd=str(ROOT_DIR)
    )
    if staged.returncode == 0:
        _log("  No changes to commit -- no-op.", fh)
        return False
    msg = f"auto: post-earnings refresh {datetime.utcnow():%Y-%m-%d}"
    _run(["git", "commit", "-m", msg], fh)
    _git_pull(fh)
    _run(["git", "push"], fh)
    return True


def _prune_logs(keep=LOG_RETENTION):
    """Keep only the most recent `keep` log files; the dir is git-committed."""
    logs = sorted(LOG_DIR.glob("*.log"))
    for stale in logs[:-keep]:
        stale.unlink(missing_ok=True)


def run(only_ticker=None):
    """Orchestrate the post-AMC refresh. Returns the list of tickers backfilled."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.utcnow():%Y-%m-%dT%H%M%S}.log"

    with open(log_path, "w") as fh:
        _log(f"{'='*60}", fh)
        _log(f"Post-earnings refresh -- {datetime.utcnow().isoformat()}Z", fh)
        if only_ticker:
            _log(f"Single-ticker mode: {only_ticker}", fh)
        _log(f"{'='*60}", fh)

        _git_pull(fh)

        transitions = self_heal_state_machine()
        for t in transitions:
            _log(f"  [HEAL] {t['ticker']}: pre_earnings -> post_earnings "
                 f"(reported {t['from_date']})", fh)

        if only_ticker:
            targets = [only_ticker]
        else:
            targets = [t["ticker"] for t in transitions]

        if not targets:
            _log("  No newly-flipped POST tickers -- nothing to refresh.", fh)
            _log("  Idempotent no-op; skipping commit.", fh)
            _prune_logs()
            return []

        # Sync the narrative base from notes FIRST so the pipeline has identity +
        # qualitative context, THEN refresh each target through the per-ticker
        # pipeline (which owns the source chain, schema gate, and quarantine).
        _log("\n  --- resync earnings_intel from notes ---", fh)
        step_sync_earnings_intel()

        for ticker in targets:
            refresh_one(ticker, fh)

        _log("\n  --- rebuild earnings_calendar.json ---", fh)
        _run(["python3", str(ROOT_DIR / "build_earnings_json.py")], fh)

        _log("\n  --- verify earnings_intel completeness ---", fh)
        _run(["python3", str(ROOT_DIR / "scripts" / "verify_earnings_intel_completeness.py")], fh)

        _log("\n  --- commit + push ---", fh)
        _commit_and_push(fh)

        _log(f"\n  Backfilled: {', '.join(targets)}", fh)
        _prune_logs()
        return targets


def main():
    ap = argparse.ArgumentParser(description="Post-AMC earnings refresh")
    ap.add_argument("--ticker", help="Backfill a single ticker on demand")
    args = ap.parse_args()
    run(only_ticker=args.ticker)


if __name__ == "__main__":
    main()
