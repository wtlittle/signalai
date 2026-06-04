"""
Quarantine ledger for tickers that could not produce a complete record.

``quarantine.json`` (repo root, committed) is the canonical list of tickers the
pipeline could NOT refresh to a complete, schema-passing record after exhausting
every source. It is read by the dashboard (a static GitHub Pages asset) to badge
affected cards.

SEMANTICS (the data-integrity contract):
  * Quarantine means "could not REFRESH" -- NOT "delete". If a ticker already
    had a valid ``earnings_intel[symbol]`` record, that prior-good record is left
    untouched; quarantine just records that today's refresh could not improve it.
  * ``upsert`` adds/updates an entry: {symbol, quarantined_at, last_success_at,
    sources_tried:[{name, error_or_missing_fields}], missing_fields:[...]}.
  * ``clear`` removes a ticker's entry -- called when a later run DOES produce a
    complete record, so a self-healed ticker drops out of quarantine.

Thread-safe: the runner refreshes tickers in a thread pool, so every read/modify/
write of quarantine.json is guarded by a module-level lock and written
atomically (temp file + os.replace) to avoid a torn file under concurrency.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from automation.shared.paths import ROOT_DIR

QUARANTINE_PATH = ROOT_DIR / "quarantine.json"

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    """Load the ledger as {symbol: entry}. Tolerates a missing/corrupt file."""
    if not QUARANTINE_PATH.exists():
        return {}
    try:
        data = json.loads(QUARANTINE_PATH.read_text())
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and "tickers" in data:
        return data.get("tickers") or {}
    return data if isinstance(data, dict) else {}


def _atomic_write(tickers: dict) -> None:
    payload = {
        "generated_at": _now(),
        "count": len(tickers),
        "tickers": tickers,
    }
    tmp = QUARANTINE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, QUARANTINE_PATH)


def upsert(
    symbol: str,
    *,
    missing_fields: list[str],
    sources_tried: list[dict],
    last_success_at: str | None = None,
) -> dict:
    """Add or update ``symbol``'s quarantine entry. Returns the written entry.

    ``sources_tried`` is a list of {name, error_or_missing_fields}. If the ticker
    was already quarantined, its original ``quarantined_at`` is preserved so the
    badge can show how long it has been broken.
    """
    symbol = symbol.upper().strip()
    with _LOCK:
        tickers = _load()
        prior = tickers.get(symbol, {})
        entry = {
            "symbol": symbol,
            "quarantined_at": prior.get("quarantined_at") or _now(),
            "last_success_at": last_success_at if last_success_at is not None
            else prior.get("last_success_at"),
            "sources_tried": sources_tried,
            "missing_fields": missing_fields,
            "updated_at": _now(),
        }
        tickers[symbol] = entry
        _atomic_write(tickers)
        return entry


def clear(symbol: str) -> bool:
    """Remove ``symbol`` from quarantine. Returns True if an entry was removed."""
    symbol = symbol.upper().strip()
    with _LOCK:
        tickers = _load()
        if symbol not in tickers:
            return False
        del tickers[symbol]
        _atomic_write(tickers)
        return True


def is_quarantined(symbol: str) -> bool:
    with _LOCK:
        return symbol.upper().strip() in _load()


def load_all() -> dict:
    """Public read of the full {symbol: entry} ledger."""
    with _LOCK:
        return _load()
