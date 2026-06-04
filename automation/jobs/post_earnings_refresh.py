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
from automation.sources import history_8q as _history_8q

LOG_DIR = ROOT_DIR / "cron_tracking" / "post_earnings_refresh"
LOG_RETENTION = 30

# Backfill chain, run in order for each newly-flipped POST ticker.
BACKFILL_SCRIPTS = [
    "backfill_earnings_from_finnhub.py",
    "backfill_revenue_from_yfinance.py",
    "backfill_rev_consensus_from_perplexity.py",
    "backfill_fy_guide_from_perplexity.py",
]


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


def backfill_ticker(ticker, fh=None):
    """Run the full backfill chain for a single ticker."""
    _log(f"\n  --- backfill chain for {ticker} ---", fh)
    for script in BACKFILL_SCRIPTS:
        _run(["python3", str(ROOT_DIR / "scripts" / script), "--ticker", ticker], fh)


def _verify_ticker(ticker, fh=None):
    """Run the completeness verifier scoped to one ticker.

    Returns True when the ticker satisfies the schema (verifier exit 0), False
    on a schema violation. The verifier itself logs the missing fields to
    cron_tracking/earnings_intel_gaps.json and stderr.
    """
    rc = _run(
        ["python3", str(ROOT_DIR / "scripts" / "verify_earnings_intel_completeness.py"),
         "--ticker", ticker],
        fh,
    )
    return rc == 0


def _repair_history_8q(ticker, fh=None):
    """Run the history_8q fallback chain (FactSet -> Finnhub -> yfinance) for one
    ticker and persist the result into earnings_intel.json.

    Must run AFTER step_sync_earnings_intel, because the note resync rebuilds the
    record from markdown and would otherwise clobber a freshly written
    history_8q. The non-force-rebuild merge preserves keys the extractor does not
    emit (history_8q is one), so the order sync -> repair -> verify is safe.
    """
    ok = _history_8q.repair_ticker(ticker)
    if ok:
        _log(f"  [HISTORY_8Q] {ticker} populated via fallback chain.", fh)
    else:
        _log(f"  [HISTORY_8Q] {ticker} chain exhausted -- history_8q left null "
             f"(renders n/a; never fabricated). See "
             f"cron_tracking/history_8q_gaps.json.", fh)
    return ok


def backfill_with_repull(ticker, fh=None):
    """Backfill a ticker, then re-pull ONCE if the verifier still fails.

    Guardrail D: a single upstream API hiccup (Finnhub stale quarter, yfinance
    10-Q not yet filed, a dropped sonar response) otherwise leaves a card
    permanently broken until the next scheduled run. After the first chain we
    resync intel so the verifier sees the freshly written fields, populate
    history_8q via its own fallback chain (post-resync so it is not clobbered),
    check the schema, and if it is still violated run the chain a second and
    final time.
    """
    backfill_ticker(ticker, fh)
    # Resync so note-derived fields land before we verify, THEN repair history_8q
    # (the resync preserves but does not produce it).
    step_sync_earnings_intel()
    _repair_history_8q(ticker, fh)
    if _verify_ticker(ticker, fh):
        return
    _log(f"  [RE-PULL] {ticker} failed schema verification after first chain -- "
         f"re-running backfill chain once.", fh)
    backfill_ticker(ticker, fh)
    step_sync_earnings_intel()
    _repair_history_8q(ticker, fh)
    if _verify_ticker(ticker, fh):
        _log(f"  [RE-PULL] {ticker} recovered after second chain.", fh)
    else:
        _log(f"  [RE-PULL] {ticker} STILL failing schema after re-pull -- "
             f"leaving fields null (renders n/a; never fabricated). "
             f"See cron_tracking/earnings_intel_gaps.json.", fh)


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
            _log("  No newly-flipped POST tickers -- nothing to backfill.", fh)
            _log("  Idempotent no-op; skipping commit.", fh)
            _prune_logs()
            return []

        for ticker in targets:
            # Guardrail D: backfill, then auto re-pull once on schema violation.
            backfill_with_repull(ticker, fh)

        _log("\n  --- resync earnings_intel from notes ---", fh)
        step_sync_earnings_intel()

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
