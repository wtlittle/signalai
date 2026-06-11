"""
Deterministic index-returns source for the weekly briefing.

The weekly briefing's ``index_returns`` block (S&P 500, Nasdaq, Dow, Russell
2000, VIX) used to be whatever the sonar narrative call happened to emit, which
meant ``weekly_pct`` / ``ytd_pct`` came back null and the header index badges
rendered em-dashes. This source replaces that with MEASURED returns computed
from the real close series the rest of the pipeline already trusts
(``data-snapshot.json#tickers[*].{timestamps,closes}``), anchored to the trading
day on or before the target week-ending date.

ZERO-FABRICATION CONTRACT:
  * Every percentage is computed from two real closes in the snapshot. A symbol
    with no usable close on or before the anchor (or its lookback) yields ``None``
    for that field -- never a guessed or carried-forward number.
  * The snapshot only carries ``^GSPC`` (S&P 500), ``^IXIC`` (Nasdaq Composite)
    and ``SPY`` today. Dow / Russell 2000 / VIX have no series here, so their
    ``*_pct`` stay ``None`` unless the Perplexity Finance platform tool is
    reachable (token present) to supply them. Missing -> None -> em-dash in the
    renderer, per the project mandate.

The platform augmentation goes through the same ``external-tool`` CLI used by
``perplexity_finance_source`` (the cron has no agent runtime; the connector is
reached as a subprocess carrying the ``external-tools`` credential preset). If
the token is expired/absent the call is swallowed and we return snapshot-only
values rather than failing the briefing.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from automation.shared.io_helpers import read_json
from automation.shared.paths import DATA_SNAPSHOT

# Snapshot symbol -> canonical index_returns key.
_SNAPSHOT_SYMBOLS = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
}

# Canonical key -> Perplexity Finance ticker (used only when the platform token
# is live; the snapshot has no series for these three).
_PLATFORM_SYMBOLS = {
    "dow": "^DJI",
    "russell2000": "^RUT",
    "vix": "^VIX",
}

_FINANCE_SOURCE_ID = "finance"
_FINANCE_TIMEOUT = 45  # short-lived token; fail fast and fall back to snapshot.


def _new_year_anchor(end: date) -> date:
    """Last calendar day of the prior year (YTD baseline)."""
    return date(end.year - 1, 12, 31)


def _close_on_or_before(series: dict, target: date) -> Optional[tuple[date, float]]:
    """(date, close) for the last trading day on or before ``target``, or None."""
    ts = series.get("timestamps") or []
    cl = series.get("closes") or []
    best = None
    for i, epoch in enumerate(ts):
        if i >= len(cl) or cl[i] is None:
            continue
        try:
            d = datetime.fromtimestamp(epoch, timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            continue
        if d <= target:
            best = (d, cl[i])
    return best


def _pct(series: dict, end: date, prev_target: date) -> Optional[float]:
    cur = _close_on_or_before(series, end)
    prev = _close_on_or_before(series, prev_target)
    if not cur or not prev or not prev[1]:
        return None
    try:
        return round((cur[1] / prev[1] - 1.0) * 100.0, 2)
    except ZeroDivisionError:
        return None


def _returns_from_series(series: dict, end: date) -> dict:
    """All MEASURED returns for one index series as of ``end``."""
    cur = _close_on_or_before(series, end)
    return {
        "close": round(cur[1], 2) if cur else None,
        "weekly_pct": _pct(series, end, end - timedelta(days=7)),
        "one_month_pct": _pct(series, end, end - timedelta(days=30)),
        "three_month_pct": _pct(series, end, end - timedelta(days=91)),
        "ytd_pct": _pct(series, end, _new_year_anchor(end)),
        "source_note": "Measured from data-snapshot close series",
    }


def _empty_block() -> dict:
    return {
        "close": None,
        "weekly_pct": None,
        "one_month_pct": None,
        "three_month_pct": None,
        "ytd_pct": None,
    }


def _call_finance_quotes(symbols: list[str]) -> dict[str, Any]:
    """Pull live quotes from the Perplexity Finance platform tool.

    Returns a {symbol: payload} map, or {} if the token is expired/absent or the
    call fails. Never raises -- the briefing must not depend on this.
    """
    payload = json.dumps({
        "source_id": _FINANCE_SOURCE_ID,
        "tool_name": "finance_quotes",
        "arguments": {"ticker_symbols": symbols},
    })
    try:
        proc = subprocess.run(
            ["external-tool", "call", payload],
            capture_output=True,
            text=True,
            timeout=_FINANCE_TIMEOUT,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except Exception:
        return {}


def build_index_returns(week_ending: date, *, try_platform: bool = True) -> dict:
    """Build the index_returns block for ``week_ending``.

    S&P 500 and Nasdaq are computed from the snapshot close series (always
    measured). Dow / Russell 2000 / VIX are filled from the platform when the
    token is live, otherwise left as null blocks (em-dash in the renderer).
    """
    snap = read_json(DATA_SNAPSHOT)
    tickers = snap.get("tickers", {}) if isinstance(snap, dict) else {}

    out: dict[str, dict] = {}
    for key, sym in _SNAPSHOT_SYMBOLS.items():
        series = tickers.get(sym)
        out[key] = _returns_from_series(series, week_ending) if series else _empty_block()

    for key in _PLATFORM_SYMBOLS:
        out[key] = _empty_block()

    if try_platform:
        quotes = _call_finance_quotes(list(_PLATFORM_SYMBOLS.values()))
        # quotes shape is connector-defined; we only graft fields we can verify
        # are numeric, never fabricate. The structure is intentionally defensive.
        if isinstance(quotes, dict) and quotes:
            content = quotes.get("content") or quotes
            for key, sym in _PLATFORM_SYMBOLS.items():
                q = None
                if isinstance(content, dict):
                    q = content.get(sym) or content.get(sym.lstrip("^"))
                if isinstance(q, dict):
                    close = q.get("price") or q.get("close") or q.get("last")
                    chg = q.get("weekly_pct") or q.get("week_change_pct")
                    if isinstance(close, (int, float)):
                        out[key]["close"] = round(float(close), 2)
                    if isinstance(chg, (int, float)):
                        out[key]["weekly_pct"] = round(float(chg), 2)

    return out
