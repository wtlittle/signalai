"""
The one source contract every earnings source implements.

A source answers exactly one question: "for this symbol, what slice of the
earnings_intel record can I supply, and with what provenance?" It returns a
``SourceResponse`` carrying the partial record it can fill, or ``None`` meaning
"I cannot fulfill -- try the next source in the chain."

CONTRACT
  * ``EarningsSource.fetch(symbol) -> SourceResponse | None``
  * ``SourceResponse.fields`` is a partial earnings_intel record dict. It need
    NOT be a complete record -- a source supplies only what it authoritatively
    knows (Finnhub: actuals + history_8q; Perplexity: FY guidance + qualitative).
    ``refresh_ticker`` MERGES responses down the priority chain and validates the
    MERGED result against the schema, accepting the first merge that is complete.
  * Every value-bearing field carries a ``<field>_source`` sibling (PR #42
    convention). The base class offers ``tag()`` so implementations don't have to
    repeat the bookkeeping.

DATA INTEGRITY
  A source must NEVER fabricate. If it cannot obtain a real value it omits the
  field (leaving it for a lower-priority source) or returns ``None``. Returning a
  guessed number to "fill the shape" is the exact failure this whole rebuild
  exists to prevent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SourceResponse(BaseModel):
    """A source's partial contribution to a ticker's earnings_intel record.

    ``fields`` is merged into the accumulating record by ``refresh_ticker`` in
    priority order (higher-priority sources win on key collisions). ``name`` is
    the provenance label that lands in ``<field>_source`` and in
    pipeline_status.json's ``last_source``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Source provenance label, e.g. 'finnhub'.")
    symbol: str
    fields: dict = Field(default_factory=dict)
    # Fields this source attempted but could not supply (diagnostics only).
    missing: list[str] = Field(default_factory=list)


class EarningsSource(ABC):
    """Abstract base for all earnings sources.

    Subclasses implement ``fetch`` and set ``name``. Subclasses must be safe to
    call from a thread pool: ``fetch`` should perform no shared mutable-state
    writes and must swallow its own network/parse errors, returning ``None`` so
    the chain continues rather than raising into the runner.
    """

    #: Provenance label used in <field>_source siblings and status artifacts.
    name: str = "base"

    @abstractmethod
    def fetch(self, symbol: str) -> Optional[SourceResponse]:
        """Return this source's partial record for ``symbol`` or ``None``."""
        raise NotImplementedError

    # --- helpers shared by implementations -------------------------------

    def tag(self, fields: dict, source_keys: list[str]) -> dict:
        """Attach ``<key>_source = self.name`` for each key in ``source_keys``
        that is present and non-null in ``fields``. Returns ``fields`` mutated
        in place for convenience.

        Provenance is only stamped on real values: a null field gets no source
        tag (a source tag on a null value would imply the source vouched for an
        absence it did not actually observe).
        """
        for key in source_keys:
            if fields.get(key) is not None:
                fields[f"{key}_source"] = self.name
        return fields

    def response(
        self,
        symbol: str,
        fields: dict,
        *,
        missing: Optional[list[str]] = None,
    ) -> SourceResponse:
        """Build a SourceResponse stamped with this source's name."""
        return SourceResponse(
            name=self.name,
            symbol=symbol,
            fields=fields,
            missing=missing or [],
        )
