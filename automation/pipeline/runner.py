"""
Parallel universe runner: ``refresh_universe(symbols)``.

Replaces the monolithic per-ticker loop that lived in daily_refresh.py. Fans the
tickers out across a ThreadPoolExecutor (each task is one idempotent
``refresh_ticker`` call), collects structured results, writes the top-level run
summary into pipeline_status.json, and returns a ``RunSummary``.

Concurrency is safe because every shared-file mutation in refresh_ticker /
quarantine / status is lock-guarded and written atomically; the sources are I/O
bound (HTTP), so threads -- not processes -- are the right tool.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from automation.pipeline import status
from automation.pipeline.refresh_ticker import TickerResult, refresh_ticker


@dataclass
class RunSummary:
    run_id: str
    started_at: str
    finished_at: str
    total: int
    succeeded: int
    quarantined: int
    skipped: int
    wall_time_ms: int
    sources_used: dict = field(default_factory=dict)
    results: list[TickerResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "succeeded": self.succeeded,
            "quarantined": self.quarantined,
            "skipped": self.skipped,
            "wall_time_ms": self.wall_time_ms,
            "sources_used": self.sources_used,
        }


def refresh_universe(symbols: list[str], *, parallel: int = 8, force: bool = False) -> RunSummary:
    """Refresh every symbol in ``symbols`` concurrently. See module docstring."""
    symbols = [s.upper().strip() for s in symbols if s and s.strip()]
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    results: list[TickerResult] = []
    workers = max(1, min(parallel, len(symbols) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(refresh_ticker, s, force=force): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # a worker should not crash the run
                print(f"  [WARN] refresh_universe {sym}: worker raised: {exc}")
                results.append(TickerResult(
                    symbol=sym, ok=False, written=False, quarantined=False,
                    last_source=None, note=f"worker error: {exc}",
                ))

    wall_ms = int((time.monotonic() - t0) * 1000)
    finished_at = datetime.now(timezone.utc).isoformat()

    succeeded = sum(1 for r in results if r.ok and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    quarantined = sum(1 for r in results if r.quarantined)
    sources_used: dict[str, int] = {"perplexity_finance": 0, "finnhub": 0, "yfinance": 0, "perplexity": 0, "cached": 0}
    for r in results:
        if r.last_source in sources_used:
            sources_used[r.last_source] += 1

    summary = RunSummary(
        run_id=run_id, started_at=started_at, finished_at=finished_at,
        total=len(symbols), succeeded=succeeded, quarantined=quarantined,
        skipped=skipped, wall_time_ms=wall_ms, sources_used=sources_used,
        results=results,
    )
    status.record_run(summary.as_dict())
    return summary


def _main() -> int:
    import argparse
    from automation.shared.tickers import load_tickers

    ap = argparse.ArgumentParser(description="Refresh the earnings universe through the pipeline")
    ap.add_argument("tickers", nargs="*", help="Symbols (default: full watchlist)")
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    symbols = args.tickers or load_tickers()
    summary = refresh_universe(symbols, parallel=args.parallel, force=args.force)
    print(
        f"run {summary.run_id}: total={summary.total} succeeded={summary.succeeded} "
        f"quarantined={summary.quarantined} skipped={summary.skipped} "
        f"wall={summary.wall_time_ms}ms sources_used={summary.sources_used}"
    )
    return 1 if summary.quarantined else 0


if __name__ == "__main__":
    raise SystemExit(_main())
