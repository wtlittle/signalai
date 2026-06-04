"""
The atomic unit of the earnings pipeline: ``refresh_ticker(symbol)``.

For one ticker it:
  1. Loads the current earnings_intel record (kept as the identity base AND as
     the prior-good record we must not clobber).
  2. Runs sources in PRIORITY ORDER -- PerplexityFinance -> Finnhub -> yfinance
     -> Perplexity -> CachedFallback -- merging each response into an
     accumulating record. Higher-priority sources win on key collisions;
     lower-priority sources only fill gaps. Perplexity is anchored on the actuals
     the structured sources already supplied (it never supplies in-quarter
     actuals). CachedFallback runs LAST and only re-emits on-disk fields that are
     still inside their per-field freshness window, so a transient triple-miss on
     the live structured sources does not re-quarantine a mature ticker.
  3. Validates the merged record against the ONE schema contract
     (EarningsIntelRecord). A POST ticker reported >48h ago must additionally
     pass the completeness policy (REQUIRED_RVC_FIELDS + a full 8Q array).
  4. Persists to earnings_intel.json ONLY IF the validated record is complete,
     then clears any quarantine entry.
  5. Otherwise leaves the prior-good record untouched and writes a quarantine
     entry (never a partial record).
  6. Updates pipeline_status.json on every invocation.

Idempotent: re-running for a single ticker is the standard recovery path. Safe
to call alone or inside the runner's thread pool (all JSON mutations are locked
and atomic). ``force=True`` re-runs the source chain even if the existing record
already looks complete.

DATA INTEGRITY: a record is written only when it passes BOTH the structural
schema and (for an eligible POST ticker) the completeness policy. Nothing partial
is ever persisted; nothing is ever fabricated.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from automation.pipeline import quarantine, status
from automation.pipeline.schema import (
    partial_history_note,
    record_completeness,
    validate_record,
)
from automation.shared.paths import ROOT_DIR
from automation.sources.base import EarningsSource
from automation.sources.cached_source import CachedFallbackSource
from automation.sources.finnhub_source import FinnhubSource
from automation.sources.perplexity_finance_source import PerplexityFinanceSource
from automation.sources.perplexity_source import PerplexitySource
from automation.sources.yfinance_source import YFinanceSource

# Live structured sources whose collective failure on a field is what justifies
# falling back to cache; named here so cached_fallback_reason can cite them.
_LIVE_STRUCTURED_SOURCES = ("perplexity_finance", "finnhub", "yfinance")

INTEL_PATH = ROOT_DIR / "earnings_intel.json"

# Guards the read/modify/write of earnings_intel.json under the thread pool.
_INTEL_LOCK = threading.Lock()

HARD_FAIL_HOURS = 48  # matches the verifier: <48h POST is "still settling"


@dataclass
class TickerResult:
    """Structured outcome of one refresh_ticker call."""

    symbol: str
    ok: bool                         # True iff a complete record was written
    written: bool                    # True iff earnings_intel.json was modified
    quarantined: bool
    last_source: str | None
    missing_fields: list[str] = field(default_factory=list)
    sources_tried: list[str] = field(default_factory=list)
    skipped: bool = False            # True iff already-complete and not forced
    duration_ms: int = 0
    note: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since_report(rec: dict) -> float | None:
    for key in ("last_reported_date", "last_earnings_date"):
        raw = rec.get(key)
        if raw:
            try:
                d = datetime.fromisoformat(str(raw)[:10]).date()
            except ValueError:
                continue
            reported = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - reported).total_seconds() / 3600.0
    return None


def _load_intel() -> dict:
    with _INTEL_LOCK:
        return json.loads(INTEL_PATH.read_text())


def _read_record(symbol: str) -> dict | None:
    """Return a deep copy of the current record for ``symbol`` (or None)."""
    intel = _load_intel()
    rec = intel.get("tickers", {}).get(symbol)
    return copy.deepcopy(rec) if rec is not None else None


def _persist_record(symbol: str, record: dict) -> None:
    """Atomically merge ``record`` into earnings_intel.json under the lock."""
    with _INTEL_LOCK:
        intel = json.loads(INTEL_PATH.read_text())
        intel.setdefault("tickers", {})[symbol] = record
        intel["last_updated"] = _now()
        tmp = INTEL_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(intel, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, INTEL_PATH)


def _merge_fields(base: dict, incoming: dict) -> None:
    """Merge ``incoming`` into ``base`` IN PLACE, gap-fill semantics.

    For ``results_vs_consensus`` (a nested dict) we merge key-by-key, never
    overwriting a key already populated by a higher-priority source. For other
    keys we only set them when absent/null in base. ``base`` therefore reflects
    "highest-priority source that supplied each individual field".
    """
    for key, val in incoming.items():
        if val is None:
            continue
        if key == "results_vs_consensus" and isinstance(val, dict):
            dest = base.setdefault("results_vs_consensus", {})
            for rk, rv in val.items():
                if rv is not None and dest.get(rk) is None:
                    dest[rk] = rv
        else:
            if base.get(key) is None:
                base[key] = val


def _cached_fallback_reason(merged: dict) -> str | None:
    """Build a record-level audit string when any field was served from cache.

    Scans both top-level fields and the nested ``results_vs_consensus`` for
    ``<field>_source == "cached"`` markers. Returns a string naming the cached
    fields and the live structured sources that came up null for them, e.g.
    ``"perplexity_finance,finnhub,yfinance all returned null for history_8q"``.
    Returns ``None`` when nothing was served from cache.
    """
    cached_fields: list[str] = []
    for key, val in merged.items():
        if key.endswith("_source") and val == "cached":
            cached_fields.append(key[: -len("_source")])
    rvc = merged.get("results_vs_consensus")
    if isinstance(rvc, dict):
        for key, val in rvc.items():
            if key.endswith("_source") and val == "cached":
                cached_fields.append(key[: -len("_source")])
    if not cached_fields:
        return None
    fields = ",".join(sorted(set(cached_fields)))
    live = ",".join(_LIVE_STRUCTURED_SOURCES)
    return f"{live} all returned null for {fields}"


def _ordered_sources() -> list[EarningsSource]:
    """The priority chain. PerplexityFinance -> Finnhub -> yfinance -> Perplexity
    -> CachedFallback.

    PerplexityFinance leads because its finance_earnings_history tool supplies the
    full trailing-8Q array (the field that quarantined 74 tickers); Finnhub and
    yfinance gap-fill anything it leaves null, and the qualitative Perplexity
    source runs next (FY guidance + narrative, never in-quarter actuals).
    CachedFallback runs last: it re-emits still-fresh on-disk fields so a
    transient miss across the live sources does not re-quarantine a mature ticker.
    """
    return [
        PerplexityFinanceSource(),
        FinnhubSource(),
        YFinanceSource(),
        PerplexitySource(),
        CachedFallbackSource(),
    ]


def refresh_ticker(symbol: str, *, force: bool = False) -> TickerResult:
    """Refresh one ticker through the source chain. See module docstring."""
    symbol = symbol.upper().strip()
    started = time.monotonic()

    prior = _read_record(symbol)
    if prior is None:
        # The pipeline populates records the note/calendar layer has already
        # established. A symbol with no identity base is skipped (the note
        # pipeline owns first-creation); we do not invent a record here.
        result = TickerResult(
            symbol=symbol, ok=False, written=False, quarantined=False,
            last_source=None, skipped=True,
            note="no existing earnings_intel record (identity base absent)",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        status.record_ticker(
            symbol, last_success_at=None, last_source=None,
            missing_fields=[], quarantined=False,
            duration_ms=result.duration_ms,
        )
        return result

    state = prior.get("state")
    hours = _hours_since_report(prior)
    # Completeness is only REQUIRED for a POST ticker reported >48h ago. PRE
    # tickers and freshly-reported POST tickers (<48h, still settling) are
    # refreshed opportunistically but never quarantined for incompleteness.
    completeness_required = (
        state == "post_earnings" and hours is not None and hours >= HARD_FAIL_HOURS
    )

    # Fast path: already complete and not forced -> skip the network entirely.
    if not force and completeness_required:
        if not record_completeness(prior, symbol=symbol):
            quarantine.clear(symbol)
            duration_ms = int((time.monotonic() - started) * 1000)
            status.record_ticker(
                symbol, last_success_at=_now(),
                last_source=prior.get("history_8q_source") or "existing",
                missing_fields=[], quarantined=False, duration_ms=duration_ms,
            )
            return TickerResult(
                symbol=symbol, ok=True, written=False, quarantined=False,
                last_source="existing", skipped=True,
                note="record already complete", duration_ms=duration_ms,
            )

    # Accumulate from the source chain on top of the identity base.
    merged = copy.deepcopy(prior)
    sources_tried: list[str] = []
    source_failures: list[dict] = []
    accepted_source: str | None = None

    for source in _ordered_sources():
        sources_tried.append(source.name)
        # Anchor Perplexity on the actuals/identity gathered so far.
        if isinstance(source, PerplexitySource):
            rvc = merged.get("results_vs_consensus") or {}
            source.set_context({
                "company_name": merged.get("company_name"),
                "last_earnings_date": merged.get("last_earnings_date"),
                "last_reported_date": merged.get("last_reported_date"),
                "in_quarter_rev_actual": rvc.get("in_quarter_rev_actual"),
            })
        try:
            resp = source.fetch(symbol)
        except Exception as exc:  # a source must never crash the chain
            source_failures.append({"name": source.name, "error_or_missing_fields": str(exc)})
            continue
        if resp is None:
            source_failures.append({"name": source.name, "error_or_missing_fields": "no_response"})
            continue
        _merge_fields(merged, resp.fields)
        if resp.missing:
            source_failures.append({"name": source.name, "error_or_missing_fields": resp.missing})
        if accepted_source is None:
            accepted_source = resp.name
        # Stop early once the merged record satisfies the completeness policy
        # (or, for a non-required ticker, once any source contributed).
        if completeness_required and not record_completeness(merged, symbol=symbol):
            break

    merged["intel_updated_at"] = _now()

    # --- audit any fields the cached fallback supplied ---
    # If CachedFallbackSource filled a gap the live structured sources left null,
    # record a record-level reason so cached fields are auditable in postmortems.
    cached_reason = _cached_fallback_reason(merged)
    if cached_reason:
        merged["cached_fallback_reason"] = cached_reason

    # --- structural schema gate (shape/type) ---
    model, schema_err = validate_record(merged)
    structurally_valid = model is not None

    # --- completeness policy gate (time-based) ---
    gaps = record_completeness(merged, symbol=symbol) if completeness_required else []
    complete = structurally_valid and not gaps

    # An allowlisted recent IPO (SHORT_HISTORY_TICKERS) clears the gate on its
    # max-available history; tag the record so the partial history is auditable
    # and never silently mistaken for a full 8Q array. The note is set only when
    # the short-history path was actually taken (never for a full record).
    partial_note = partial_history_note(merged.get("history_8q"), symbol)
    if partial_note:
        merged["partial_history_accepted"] = True
        merged["partial_history_note"] = partial_note

    duration_ms = int((time.monotonic() - started) * 1000)

    if complete:
        _persist_record(symbol, merged)
        quarantine.clear(symbol)
        status.record_ticker(
            symbol, last_success_at=_now(), last_source=accepted_source,
            missing_fields=[], quarantined=False, duration_ms=duration_ms,
            cached_fallback_reason=cached_reason,
        )
        return TickerResult(
            symbol=symbol, ok=True, written=True, quarantined=False,
            last_source=accepted_source, missing_fields=[],
            sources_tried=sources_tried, duration_ms=duration_ms,
            note=partial_note,
        )

    # --- Not complete: do NOT write a partial record. ---
    # If the ticker was never expected to be complete (PRE, or <48h POST) AND it
    # is structurally valid, we still opportunistically persist any NEW non-null
    # fields the chain supplied -- but only when it does not regress the prior
    # record (gap-fill only, enforced by _merge_fields). For a required-but-
    # incomplete ticker, the prior good record is preserved and we quarantine.
    if not completeness_required and structurally_valid:
        # Persist gap-fill improvements (e.g. a PRE ticker that gained history_8q).
        if merged != prior:
            _persist_record(symbol, merged)
            written = True
        else:
            written = False
        quarantine.clear(symbol)
        status.record_ticker(
            symbol, last_success_at=_now() if written else None,
            last_source=accepted_source, missing_fields=[],
            quarantined=False, duration_ms=duration_ms,
            cached_fallback_reason=cached_reason if written else None,
        )
        return TickerResult(
            symbol=symbol, ok=True, written=written, quarantined=False,
            last_source=accepted_source, sources_tried=sources_tried,
            duration_ms=duration_ms,
            note="not subject to completeness policy",
        )

    # Required-but-incomplete (or structurally invalid) -> quarantine.
    missing = gaps if structurally_valid else [f"schema_invalid: {schema_err}"]
    prior_success = (
        status.get_ticker(symbol) or {}
    ).get("last_success_at")
    quarantine.upsert(
        symbol,
        missing_fields=missing,
        sources_tried=source_failures or [{"name": s, "error_or_missing_fields": "no_data"} for s in sources_tried],
        last_success_at=prior_success,
    )
    status.record_ticker(
        symbol, last_success_at=prior_success, last_source=accepted_source,
        missing_fields=missing, quarantined=True, duration_ms=duration_ms,
    )
    return TickerResult(
        symbol=symbol, ok=False, written=False, quarantined=True,
        last_source=accepted_source, missing_fields=missing,
        sources_tried=sources_tried, duration_ms=duration_ms,
        note=("schema invalid" if not structurally_valid else "incomplete after all sources"),
    )


def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Refresh one or more tickers through the pipeline")
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--force", action="store_true", help="Re-run the chain even if complete")
    args = ap.parse_args()
    rc = 0
    for t in args.tickers:
        res = refresh_ticker(t, force=args.force)
        print(
            f"{res.symbol}: ok={res.ok} written={res.written} "
            f"quarantined={res.quarantined} source={res.last_source} "
            f"missing={res.missing_fields} ({res.duration_ms}ms) {res.note or ''}"
        )
        if res.quarantined:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_main())
