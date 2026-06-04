"""
Observable pipeline status artifact.

``pipeline_status.json`` (repo root, committed) is the system-health-at-a-glance
snapshot the dashboard renders. It has two parts:

  * ``tickers[symbol]`` -- updated on EVERY ``refresh_ticker`` call:
        {symbol, last_attempt_at, last_success_at, last_source,
         missing_fields:[], quarantined: bool, duration_ms}
  * ``run`` -- a top-level summary written once per ``refresh_universe`` call:
        {run_id, started_at, finished_at, total, succeeded, quarantined,
         skipped, sources_used:{perplexity_finance,finnhub,yfinance,perplexity,cached}}

Thread-safe and atomic for the same reason quarantine.py is: the runner updates
per-ticker status from worker threads concurrently.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from automation.shared.paths import ROOT_DIR

STATUS_PATH = ROOT_DIR / "pipeline_status.json"

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not STATUS_PATH.exists():
        return {"tickers": {}, "run": None}
    try:
        data = json.loads(STATUS_PATH.read_text())
    except (OSError, ValueError):
        return {"tickers": {}, "run": None}
    if not isinstance(data, dict):
        return {"tickers": {}, "run": None}
    data.setdefault("tickers", {})
    data.setdefault("run", None)
    return data


def _atomic_write(data: dict) -> None:
    data["generated_at"] = _now()
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, STATUS_PATH)


def record_ticker(
    symbol: str,
    *,
    last_success_at: str | None,
    last_source: str | None,
    missing_fields: list[str],
    quarantined: bool,
    duration_ms: int,
    cached_fallback_reason: str | None = None,
) -> None:
    """Append/update one ticker's status entry. Called by every refresh_ticker.

    ``cached_fallback_reason`` is recorded when the cached fallback source filled
    a gap the live sources left null, so postmortems can audit cached fields.
    """
    symbol = symbol.upper().strip()
    with _LOCK:
        data = _load()
        prior = data["tickers"].get(symbol, {})
        entry = {
            "symbol": symbol,
            "last_attempt_at": _now(),
            # Preserve the most recent real success timestamp across attempts.
            "last_success_at": last_success_at if last_success_at is not None
            else prior.get("last_success_at"),
            "last_source": last_source,
            "missing_fields": missing_fields,
            "quarantined": quarantined,
            "duration_ms": duration_ms,
        }
        if cached_fallback_reason:
            entry["cached_fallback_reason"] = cached_fallback_reason
        data["tickers"][symbol] = entry
        _atomic_write(data)


def record_run(summary: dict) -> None:
    """Write the top-level run summary (one per refresh_universe)."""
    with _LOCK:
        data = _load()
        data["run"] = summary
        _atomic_write(data)


def get_ticker(symbol: str) -> dict | None:
    with _LOCK:
        return _load()["tickers"].get(symbol.upper().strip())
