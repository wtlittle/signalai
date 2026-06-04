"""
history_8q fallback chain + earnings_intel.json repair.

Provides ONE source-of-truth for assembling a ticker's trailing-8-quarter
results array, used by the daily refresh self-heal, the post-earnings re-pull
guardrail, and the one-shot repair CLI.

PRIORITY ORDER (per the standing data-source policy)
  1. FactSet MCP  -- highest fidelity, but only callable from the Computer Agent
     runtime. A stub here (mirrors automation/jobs/execute_queue.py) returns None
     until the MCP shim is wired, so the chain transparently falls through.
  2. Finnhub      -- automation/sources/finnhub_history.fetch_history_8q.
  3. yfinance     -- last resort; quarterly_income_stmt revenue + earnings_history
     EPS. yfinance only carries ~5-6 quarters for many names, so it rarely
     satisfies the >=8 bar -- it is the floor, not the expectation.

A source is ACCEPTED only when it returns >= 8 quarters each carrying BOTH a
revenue and an EPS actual. A half-full array is never accepted as complete
(zero-fabrication mandate). When every source fails, the chain returns
(None, sources_tried, partial_count) and the caller logs the gap; history_8q is
written as null and renders n/a -- never a stub.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from automation.shared.paths import ROOT_DIR
from automation.sources import finnhub_history

INTEL_PATH = ROOT_DIR / "earnings_intel.json"
CALENDAR_PATH = ROOT_DIR / "earnings_calendar.json"
GAPS_PATH = ROOT_DIR / "cron_tracking" / "history_8q_gaps.json"

_REQUIRED_QUARTERS = 8
_VALID_SOURCES = ("factset", "finnhub", "yfinance", "manual")

# FactSet MCP is not callable outside the Computer Agent runtime; flip when the
# shim lands (matches the _FACTSET_AVAILABLE gate in execute_queue.py).
_FACTSET_AVAILABLE = False


def _is_complete(history: list[dict] | None) -> bool:
    """True iff history has >=8 quarters each with a non-null revenue AND EPS."""
    if not history or len(history) < _REQUIRED_QUARTERS:
        return False
    usable = [
        q for q in history
        if q.get("revenue_actual") is not None and q.get("eps_actual") is not None
    ]
    return len(usable) >= _REQUIRED_QUARTERS


def _count_partial(history: list[dict] | None) -> int:
    if not history:
        return 0
    return sum(
        1 for q in history
        if q.get("revenue_actual") is not None and q.get("eps_actual") is not None
    )


def _fetch_factset(symbol: str) -> list[dict] | None:
    """FactSet MCP placeholder. Always None until the MCP shim is wired."""
    if not _FACTSET_AVAILABLE:
        return None
    return None  # future: call FactSet MCP tool here


def _fetch_yfinance(symbol: str) -> list[dict] | None:
    """Last-resort yfinance history. Returns the module schema or None.

    Joins quarterly_income_stmt (revenue) with earnings_history (EPS) by
    quarter-end date. yfinance commonly returns < 8 quarters, in which case the
    completeness gate downstream rejects it and the chain has already exhausted
    its options -- so the result is logged as a gap, never padded.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        tk = yf.Ticker(symbol)
        income = tk.quarterly_income_stmt
    except Exception as exc:
        print(f"  [WARN] yfinance history {symbol}: income stmt error: {exc}")
        return None
    if income is None or getattr(income, "empty", True):
        return None

    rev_row = None
    for label in ("Total Revenue", "Operating Revenue"):
        if label in income.index:
            rev_row = income.loc[label]
            break
    if rev_row is None:
        return None

    eps_by_date: dict[str, float] = {}
    try:
        eh = tk.earnings_history
        if eh is not None and not getattr(eh, "empty", True) and "epsActual" in eh.columns:
            for idx, val in eh["epsActual"].items():
                d = str(idx)[:10]
                if val is not None:
                    eps_by_date[d] = float(val)
    except Exception:
        eps_by_date = {}

    quarters: list[dict] = []
    for col, rev in rev_row.items():
        period_end = str(col)[:10]
        if rev is None:
            continue
        try:
            rev_f = float(rev)
        except (TypeError, ValueError):
            continue
        quarters.append({
            "period_end": period_end,
            "fiscal_quarter": None,
            "fiscal_year": None,
            "revenue_actual": round(rev_f),
            "eps_actual": eps_by_date.get(period_end),
            "eps_estimate": None,
            "eps_surprise_pct": None,
            "rev_surprise_pct": None,
        })
    quarters.sort(key=lambda q: q["period_end"], reverse=True)
    return quarters or None


def fetch_chain(symbol: str) -> tuple[list[dict] | None, str | None, list[str], int]:
    """Run the FactSet -> Finnhub -> yfinance chain for ``symbol``.

    Returns (history, source, sources_tried, best_partial_count):
      * history/source are the first source's payload that satisfies the >=8
        complete-quarter bar; both None when the chain is exhausted.
      * sources_tried lists every provider attempted, in order.
      * best_partial_count is the largest count of fully populated quarters any
        source returned (for gap diagnostics).
    """
    symbol = symbol.upper().strip()
    providers = (
        ("factset", _fetch_factset),
        ("finnhub", finnhub_history.fetch_history_8q),
        ("yfinance", _fetch_yfinance),
    )
    sources_tried: list[str] = []
    best_partial = 0
    for name, fn in providers:
        sources_tried.append(name)
        try:
            history = fn(symbol)
        except Exception as exc:
            print(f"  [WARN] history_8q {symbol}: {name} raised: {exc}")
            history = None
        best_partial = max(best_partial, _count_partial(history))
        if _is_complete(history):
            return history[:_REQUIRED_QUARTERS], name, sources_tried, best_partial
    return None, None, sources_tried, best_partial


def _log_gap(symbol: str, sources_tried: list[str], partial: int) -> None:
    """Append a history_8q gap record (append-only). Never raises."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": symbol,
        "sources_tried": sources_tried,
        "partial_quarters_returned": partial,
    }
    try:
        GAPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if GAPS_PATH.exists():
            try:
                existing = json.loads(GAPS_PATH.read_text())
                if not isinstance(existing, list):
                    existing = []
            except ValueError:
                existing = []
        existing.append(record)
        GAPS_PATH.write_text(json.dumps(existing, indent=2) + "\n")
    except OSError as exc:
        print(f"  [WARN] could not write {GAPS_PATH}: {exc}")


def _calendar_identity(symbol: str) -> dict | None:
    """Look up a ticker's identity (company, state, next/last earnings date) from
    earnings_calendar.json. Returns None if the calendar has no entry.
    """
    try:
        cal = json.loads(CALENDAR_PATH.read_text())
    except (OSError, ValueError):
        return None
    for entry in cal.get("post_earnings", []) or []:
        if entry.get("ticker") == symbol:
            return {
                "company_name": entry.get("company") or symbol,
                "state": "post_earnings",
                "last_earnings_date": entry.get("earnings_date") or entry.get("date"),
                "last_reported_date": entry.get("earnings_date") or entry.get("date"),
            }
    for entry in cal.get("pre_earnings", []) or []:
        if entry.get("ticker") == symbol:
            return {
                "company_name": entry.get("company") or symbol,
                "state": "pre_earnings",
                "next_earnings_date": entry.get("earnings_date") or entry.get("date"),
            }
    return None


def _ensure_record(symbol: str, tickers: dict) -> dict | None:
    """Return the intel record for symbol, creating a MINIMAL identity-only stub
    (from the calendar) if absent. The stub carries no fabricated analysis -- only
    ticker/company/state/dates -- so history_8q can be surfaced for a ticker the
    note pipeline has not yet written a full record for (e.g. a pre-earnings name).
    Returns None if the ticker is unknown to the calendar too.
    """
    rec = tickers.get(symbol)
    if rec is not None:
        return rec
    identity = _calendar_identity(symbol)
    if identity is None:
        return None
    rec = {
        "ticker": symbol,
        "refresh_reason": "history_8q_repair_minimal_stub",
        "intel_updated_at": datetime.now(timezone.utc).isoformat(),
        **identity,
    }
    tickers[symbol] = rec
    print(f"  [INFO] history_8q repair: created minimal identity record for {symbol} "
          f"(state={identity.get('state')})")
    return rec


def repair_ticker(symbol: str, intel: dict | None = None, persist: bool = True) -> bool:
    """Populate tickers[symbol].history_8q in earnings_intel.json via the chain.

    Returns True when a complete 8Q array was written, False when the chain was
    exhausted (history_8q set to null + gap logged). Writes history_8q_source
    alongside (provenance convention). When persist is False the caller owns the
    write (used by the daily-refresh self-heal which batches its own save).
    """
    symbol = symbol.upper().strip()
    owns_io = intel is None
    if intel is None:
        intel = json.loads(INTEL_PATH.read_text())
    tickers = intel.setdefault("tickers", {})
    rec = _ensure_record(symbol, tickers)
    if rec is None:
        print(f"  [WARN] history_8q repair: {symbol} not in earnings_intel.json "
              f"or earnings_calendar.json -- cannot place history_8q")
        return False

    history, source, tried, partial = fetch_chain(symbol)
    if history is not None:
        rec["history_8q"] = history
        rec["history_8q_source"] = source
        print(f"  [OK] history_8q {symbol}: {len(history)} quarters from {source}")
        result = True
    else:
        rec["history_8q"] = None
        rec["history_8q_source"] = None
        _log_gap(symbol, tried, partial)
        print(f"  [GAP] history_8q {symbol}: chain exhausted (tried {tried}, "
              f"best {partial} complete quarters) -- wrote null, logged gap")
        result = False

    if persist and owns_io:
        INTEL_PATH.write_text(json.dumps(intel, indent=2, ensure_ascii=False) + "\n")
    return result


def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Populate history_8q via the fallback chain")
    ap.add_argument("tickers", nargs="+", help="Ticker symbol(s) to repair")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch and print but do not write earnings_intel.json")
    args = ap.parse_args()

    if args.dry_run:
        for t in args.tickers:
            history, source, tried, partial = fetch_chain(t)
            n = 0 if history is None else len(history)
            print(f"{t}: {n} quarters, source={source}, tried={tried}, partial={partial}")
        return 0

    intel = json.loads(INTEL_PATH.read_text())
    ok_all = True
    for t in args.tickers:
        ok = repair_ticker(t, intel=intel, persist=False)
        ok_all = ok_all and ok
    INTEL_PATH.write_text(json.dumps(intel, indent=2, ensure_ascii=False) + "\n")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(_main())
