"""
Cached fallback earnings source (priority 5, the LAST resort in the chain).

WHY this source exists:
  PR #46 (PerplexityFinance -> Finnhub -> yfinance -> sonar) dropped quarantines
  from 74 to 24. The residual 24 are mature tickers where ALL three structured
  sources transiently returned null/partial ``history_8q`` on a single run. For a
  large cap we have ALREADY fetched a clean 8-quarter history on a prior run, a
  transient triple-miss should NOT re-quarantine the ticker: the numbers are sat
  on disk in ``earnings_intel.json``. This source re-emits those on-disk fields,
  stamped ``<field>_source = "cached"``, but ONLY while each field is inside a
  per-field freshness window. Graceful degradation, never stale numbers.

DATA INTEGRITY (the zero-fabrication mandate, extended to cache):
  * A field is served from cache ONLY if it is younger than its per-field max age
    (FIELD_MAX_AGE). Older -> omitted (left null -> rendered n/a downstream).
  * ``in_quarter_*`` actuals/surprises and ``latest_news_*`` are NEVER served from
    cache (NEVER_CACHE): the in-quarter print is the live current-quarter number
    and sonar news must be live or absent. Serving a prior quarter's actual as the
    current print would be a fabrication.
  * ``history_8q`` is filtered to fully-reported quarters (both revenue and EPS
    actual present); if fewer than 8 remain the field is omitted entirely -- no
    half-fixed short array (which the completeness gate would reject anyway).
  * Any read/parse failure is swallowed and the source returns ``None`` so the
    chain ends cleanly rather than raising into the runner.

KNOWN LIMITATION (timestamp resolution):
  Freshness is measured against a per-field ``<field>_updated_at`` timestamp when
  present. The current corpus does NOT yet carry per-field timestamps, so we fall
  back to a record-level timestamp (``last_updated_at`` then ``intel_updated_at``).
  This means every cacheable field on a record shares the record's age until the
  pipeline starts stamping per-field timestamps. We deliberately fall back rather
  than refuse-all, because a record-level age is a sound UPPER bound on each
  field's age (a field is never OLDER than the record). See docs/pipeline-architecture.md.

PROVENANCE: each emitted field carries ``<field>_source = "cached"`` plus a
``<field>_source_age_days`` diagnostic. ``refresh_ticker`` additionally records a
record-level ``cached_fallback_reason`` describing which live sources came up null.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from automation.shared.paths import EARNINGS_INTEL
from automation.sources.base import EarningsSource, SourceResponse

# Per-field maximum age. A field older than its window is NOT served from cache.
# Windows come straight from the PR spec's freshness table; the rationale for each
# lives in docs/pipeline-architecture.md ("Cached fallback policy").
FIELD_MAX_AGE: dict[str, timedelta] = {
    # Reported quarters do not change after the conference call.
    "history_8q": timedelta(days=92),
    # FY guidance bullets: updated quarterly, sticky between earnings.
    "fy_guide_revenue": timedelta(days=95),
    "fy_guide_eps": timedelta(days=95),
    "fy_guide_bullets": timedelta(days=95),
    # Street consensus for next quarter: drifts, but slowly.
    "consensus_eps_next_q": timedelta(days=14),
    "consensus_rev_next_q": timedelta(days=14),
    "consensus_estimates": timedelta(days=14),
    # Sticky once announced.
    "next_earnings_date": timedelta(days=30),
    # Slow-changing identity.
    "company_name": timedelta(days=365),
    "name": timedelta(days=365),
    "sector": timedelta(days=365),
    "subsector": timedelta(days=365),
}

# Fields that are NEVER served from cache, at any age. The in-quarter print is the
# live current-quarter number; sonar news must be live or absent.
NEVER_CACHE: frozenset[str] = frozenset({
    "in_quarter_eps_actual",
    "in_quarter_eps_surprise_pct",
    "in_quarter_rev_actual",
    "in_quarter_rev_surprise_pct",
    "latest_news_summary",
    "latest_news_url",
})

# Default set of fields this source will try to supply from cache when the caller
# does not narrow it. It is exactly the cacheable policy keys.
_DEFAULT_NEEDED: tuple[str, ...] = tuple(FIELD_MAX_AGE.keys())


def _parse_ts(raw: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``) to aware UTC.

    Returns ``None`` for an unparseable value rather than raising -- an
    unparseable timestamp means "age unknown", which we treat as not-fresh.
    """
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class CachedFallbackSource(EarningsSource):
    """Final source: re-emit on-disk fields that are still inside their window.

    Reads the previous, persisted record for ``symbol`` from
    ``earnings_intel.json`` and returns each requested cacheable field whose age
    is within ``FIELD_MAX_AGE``. Never serves ``NEVER_CACHE`` fields. Returns
    ``None`` when there is no cache file, no record, or no fresh field to offer.
    """

    name = "cached"

    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = Path(cache_path) if cache_path is not None else EARNINGS_INTEL

    def _load_record(self, symbol: str) -> Optional[dict]:
        """Return the persisted record for ``symbol`` or ``None``.

        Accepts the canonical ``{"tickers": {symbol: rec}}`` layout this repo
        uses, and also tolerates a bare top-level ``{symbol: rec}`` or a
        ``{"records": {symbol: rec}}`` shape so the source is robust to the
        reader patterns referenced in the spec.
        """
        if not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        rec = (
            (data.get("tickers") or {}).get(symbol)
            or data.get(symbol)
            or (data.get("records") or {}).get(symbol)
        )
        return rec if isinstance(rec, dict) else None

    def _field_age(self, rec: dict, field: str, now: datetime) -> Optional[timedelta]:
        """Age of ``field`` measured against the best available timestamp.

        Resolution order: per-field ``<field>_updated_at`` -> record-level
        ``last_updated_at`` -> ``intel_updated_at`` (the only timestamp the
        current corpus carries). Returns ``None`` when no timestamp resolves --
        age-unknown is treated as not-fresh (the field is dropped).
        """
        for key in (f"{field}_updated_at", "last_updated_at", "intel_updated_at"):
            raw = rec.get(key)
            if raw:
                ts = _parse_ts(str(raw))
                if ts is not None:
                    return now - ts
        return None

    def fetch(
        self,
        symbol: str,
        *,
        needed_fields: Optional[list[str]] = None,
        now: Optional[datetime] = None,
    ) -> Optional[SourceResponse]:
        symbol = symbol.upper().strip()
        now = now or datetime.now(timezone.utc)
        needed = list(needed_fields) if needed_fields is not None else list(_DEFAULT_NEEDED)

        rec = self._load_record(symbol)
        if rec is None:
            return None

        out: dict = {}
        for f in needed:
            if f in NEVER_CACHE:
                continue  # never serve from cache, regardless of age
            max_age = FIELD_MAX_AGE.get(f)
            if max_age is None:
                continue  # unknown / non-cacheable field -> skip
            val = rec.get(f)
            if val is None:
                continue
            age = self._field_age(rec, f, now)
            if age is None or age > max_age:
                continue  # age unknown or stale -> drop (never serve)

            # history_8q: keep only fully-reported quarters (both actuals present).
            # If fewer than 8 survive, omit the field rather than emit a short,
            # half-fixed array (the completeness gate would reject it anyway).
            if f == "history_8q":
                if not isinstance(val, list):
                    continue
                reported = [
                    q for q in val
                    if isinstance(q, dict)
                    and q.get("revenue_actual") is not None
                    and q.get("eps_actual") is not None
                ]
                if len(reported) < 8:
                    continue
                val = reported[:8]

            out[f] = val
            out[f"{f}_source"] = self.name
            out[f"{f}_source_age_days"] = age.days

        if not out:
            return None

        missing = [f for f in needed if f not in out and f not in NEVER_CACHE]
        return self.response(symbol, out, missing=missing)
