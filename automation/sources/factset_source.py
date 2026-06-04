"""
FactSet earnings source (highest priority, currently a stub).

FactSet would be the highest-fidelity provider, but its MCP integration is only
callable from the Computer Agent runtime and no shim exists in this repo yet
(mirrors the ``_FACTSET_AVAILABLE`` gate in automation/sources/history_8q.py and
automation/jobs/execute_queue.py). Per the rebuild brief we do NOT block the
architecture on FactSet: this source is a well-formed stub whose ``fetch``
returns ``None`` so the chain transparently falls through to Finnhub.

TODO: when the FactSet MCP shim lands, implement ``fetch`` to call it and map
the response into the earnings_intel record shape (in_quarter actuals + 8Q
history + consensus), stamping provenance via ``self.tag``.
"""
from __future__ import annotations

from typing import Optional

from automation.sources.base import EarningsSource, SourceResponse

# Flip when the FactSet MCP shim is wired (matches history_8q._FACTSET_AVAILABLE).
_FACTSET_AVAILABLE = False


class FactSetSource(EarningsSource):
    name = "factset"

    def fetch(self, symbol: str) -> Optional[SourceResponse]:
        if not _FACTSET_AVAILABLE:
            return None
        # TODO: call FactSet MCP, map to record shape, stamp provenance.
        return None
