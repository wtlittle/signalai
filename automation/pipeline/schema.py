"""
Schema-as-contract for ``earnings_intel.json`` records.

ONE pydantic model -- ``EarningsIntelRecord`` -- defines the full required shape
for ``earnings_intel["tickers"][symbol]``. Both the pipeline (which validates
before writing) and the completeness verifier
(``scripts/verify_earnings_intel_completeness.py``) consume THIS model so the
contract has a single definition. The verifier extracts its required-field list
from ``EarningsIntelRecord`` rather than re-declaring its own.

DESIGN -- why some fields are "optional" in the pydantic sense:
  The pydantic model enforces SHAPE and TYPE correctness (a present field must
  have the right type; identity fields must exist). It deliberately does NOT
  encode the *time-based completeness policy* ("a post_earnings ticker reported
  >48h ago must carry REV/EPS actuals and a full 8Q array"). That policy is a
  function of wall-clock time, not record shape, so it lives in the verifier and
  in ``refresh_ticker`` -- both of which read the canonical field lists exported
  here (``REQUIRED_RVC_FIELDS``, ``HISTORY_8Q_REQUIRED_QUARTERS``). This split is
  what lets the model ACCEPT the records PR #44 repaired (e.g. AMZN currently has
  history_8q populated lazily) without the structural validator re-quarantining
  a record that is merely awaiting a time-gated backfill.

DATA INTEGRITY:
  Validation failure => the record is NOT written to earnings_intel.json; the
  ticker is routed to quarantine (see automation/pipeline/quarantine.py). A
  partial record is never persisted as if it were complete.

PROVENANCE (PR #42 convention, extended universally):
  Every value-bearing field may carry a ``<field>_source`` sibling recording the
  source that supplied it. The model types these explicitly (no ``Any``).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# --- Canonical field lists (the single source of truth the verifier imports) ---

# results_vs_consensus fields that a fully-populated POST card renders. The
# verifier hard-fails a post_earnings ticker reported >48h ago that is missing
# any of these; the pipeline routes such a ticker to quarantine.
REQUIRED_RVC_FIELDS: tuple[str, ...] = (
    "in_quarter_rev_actual",
    "in_quarter_eps_actual",
    "in_quarter_rev_surprise_pct",
    "in_quarter_eps_surprise_pct",
)

# The trailing-8-quarter results array length. null or a short array is the gap
# that took down the 2026-06-04 refresh (ACN/AVGO), so a complete record carries
# exactly this many quarters each with BOTH a revenue and an EPS actual.
HISTORY_8Q_REQUIRED_QUARTERS = 8

# Guidance fields (tracked, SOFT -- never a hard gate; fabricating guidance would
# violate the zero-fabrication mandate).
GUIDANCE_FIELDS: tuple[str, ...] = (
    "guidanceRevenueDeltaPct",
    "guidanceEpsDeltaPct",
    "guidanceEpsDeltaAbs",
    "guidanceOperatingProfitDeltaPct",
    "guidanceOperatingProfitDeltaAbs",
    "guidanceFcfDeltaPct",
    "guidanceFcfDeltaAbs",
    "guidanceProfitDeltaPct",
)


class HistoryQuarter(BaseModel):
    """One trailing quarter in ``history_8q`` (most-recent-first ordering).

    revenue_actual / eps_actual are the only fields a *complete* quarter must
    carry; estimate / surprise are optional because the free data tier cannot
    always supply a same-basis consensus (see finnhub_history docstring), and
    fabricating a cross-basis surprise is forbidden.
    """

    model_config = ConfigDict(extra="allow")

    period_end: str
    fiscal_quarter: Optional[int] = None
    fiscal_year: Optional[int] = None
    revenue_actual: Optional[float] = None
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    eps_surprise_pct: Optional[float] = None
    rev_surprise_pct: Optional[float] = None


class ResultsVsConsensus(BaseModel):
    """In-quarter actuals vs consensus, each value carrying a ``*_source``.

    Actuals are typed ``str | float | None`` because the existing corpus stores
    pre-formatted display strings ("$181.52B", "$1.61") alongside numeric values
    written by newer sources -- the contract accepts both rather than forcing a
    lossy migration of 154 historical records.
    """

    model_config = ConfigDict(extra="allow")

    in_quarter_rev_actual: Optional[str | float] = None
    in_quarter_eps_actual: Optional[str | float] = None
    in_quarter_rev_estimate: Optional[str | float] = None
    in_quarter_eps_estimate: Optional[str | float] = None
    in_quarter_rev_consensus: Optional[str | float] = None
    in_quarter_eps_consensus: Optional[str | float] = None
    in_quarter_rev_surprise_pct: Optional[float] = None
    in_quarter_eps_surprise_pct: Optional[float] = None
    in_quarter_rev_yoy_pct: Optional[float] = None

    # Provenance siblings (PR #42 convention).
    rev_actual_source: Optional[str] = None
    eps_actual_source: Optional[str] = None
    in_quarter_rev_actual_source: Optional[str] = None
    in_quarter_eps_actual_source: Optional[str] = None
    rev_consensus_source: Optional[str] = None
    eps_consensus_source: Optional[str] = None
    rev_surprise_source: Optional[str] = None
    eps_surprise_source: Optional[str] = None


class EarningsIntelRecord(BaseModel):
    """Full contract for one ``earnings_intel["tickers"][symbol]`` record.

    REQUIRED (structural identity -- absence is always invalid):
        ticker, state
    TYPED-OPTIONAL (present-but-may-be-null; the time-based completeness policy
    in the verifier decides when their absence escalates to a failure):
        everything else, including results_vs_consensus and history_8q.

    ``extra="allow"`` keeps the model forward-compatible with the many narrative
    fields (bull_case, signal_scorecard, inflection_library, ...) that the note
    pipeline writes and that are out of scope for this contract -- they pass
    through untouched rather than being stripped on a validate/serialize round
    trip.
    """

    model_config = ConfigDict(extra="allow")

    # --- Required structural identity ---
    ticker: str
    state: str

    # --- Identity / dates (typed-optional) ---
    company_name: Optional[str] = None
    last_earnings_date: Optional[str] = None
    last_reported_date: Optional[str] = None
    next_earnings_date: Optional[str] = None
    intel_updated_at: Optional[str] = None
    refresh_reason: Optional[str] = None

    # --- The contractual payload ---
    results_vs_consensus: Optional[ResultsVsConsensus] = None
    history_8q: Optional[list[HistoryQuarter]] = None
    history_8q_source: Optional[str] = None

    @classmethod
    def required_field_names(cls) -> list[str]:
        """Names of the structurally-required (no-default) top-level fields.

        The verifier imports this so the set of always-required keys has ONE
        definition. Today this is {ticker, state}; adding a required field to the
        model automatically tightens the verifier with no second edit.
        """
        return [
            name
            for name, fld in cls.model_fields.items()
            if fld.is_required()
        ]


def history_8q_complete(history: list | None) -> bool:
    """True iff ``history`` is a full 8Q array, each quarter carrying BOTH a
    non-null revenue and EPS actual. The single definition of "complete 8Q" that
    the pipeline, the sources, and the verifier all honor.
    """
    if not isinstance(history, list) or len(history) < HISTORY_8Q_REQUIRED_QUARTERS:
        return False
    usable = [
        q for q in history
        if isinstance(q, dict)
        and q.get("revenue_actual") is not None
        and q.get("eps_actual") is not None
    ]
    return len(usable) >= HISTORY_8Q_REQUIRED_QUARTERS


def rvc_missing_fields(rvc: dict | None) -> list[str]:
    """Return the REQUIRED_RVC_FIELDS that are null/absent in ``rvc``."""
    rvc = rvc or {}
    return [f for f in REQUIRED_RVC_FIELDS if rvc.get(f) is None]


def validate_record(raw: dict) -> tuple[Optional[EarningsIntelRecord], Optional[str]]:
    """Structurally validate one raw record dict.

    Returns (model, None) on success or (None, error_message) on a pydantic
    ValidationError. This is the shape gate; it does NOT enforce the time-based
    completeness policy (see ``record_completeness`` for that).
    """
    try:
        return EarningsIntelRecord.model_validate(raw), None
    except Exception as exc:  # pydantic.ValidationError (kept broad: never crash)
        return None, str(exc)


def record_completeness(raw: dict, *, require_history_8q: bool = True) -> list[str]:
    """Return the list of completeness gaps for a POST record (empty == complete).

    This is the policy a POST ticker must satisfy before the pipeline accepts a
    source's response as authoritative. It is intentionally separate from
    ``validate_record`` (shape) so callers can apply it only when the record's
    state + report-age warrant a full payload.
    """
    gaps: list[str] = []
    rvc = raw.get("results_vs_consensus") or {}
    gaps.extend(rvc_missing_fields(rvc))
    if require_history_8q and not history_8q_complete(raw.get("history_8q")):
        h = raw.get("history_8q")
        n = len(h) if isinstance(h, list) else 0
        gaps.append(
            f"history_8q ({'null' if h is None else f'{n}/{HISTORY_8Q_REQUIRED_QUARTERS} quarters'})"
        )
    return gaps
