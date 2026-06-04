"""
Finnhub earnings source (priority 2, the production workhorse).

Wraps the PR #44 helper ``finnhub_history.fetch_history_8q`` (trailing-8Q
array) and extends to the other fields Finnhub can authoritatively supply:

  * in_quarter_eps_actual / _estimate / _surprise_pct   (via /stock/earnings)
  * history_8q                                          (via PR #44 reconstruction)

It does NOT supply revenue actual or revenue consensus -- Finnhub free tier has
no revenue consensus, and reconstructing in-quarter revenue here would duplicate
the yfinance path. Those keys are left for lower-priority sources; the merge in
refresh_ticker fills them from yfinance (rev actual) and Perplexity (rev
consensus). This is reuse, not duplication: every Finnhub byte flows through the
one PR #44 module and its single 10s-timeout-plus-retry ``_get``.

DATA INTEGRITY: only real values are returned; any field Finnhub cannot supply
is omitted (left for the next source), never guessed.
"""
from __future__ import annotations

from typing import Optional

from automation.sources import finnhub_history
from automation.sources.base import EarningsSource, SourceResponse


class FinnhubSource(EarningsSource):
    name = "finnhub"

    def fetch(self, symbol: str) -> Optional[SourceResponse]:
        symbol = symbol.upper().strip()
        fields: dict = {}
        missing: list[str] = []

        # --- trailing 8Q (PR #44, unchanged) ---
        try:
            history = finnhub_history.fetch_history_8q(symbol)
        except finnhub_history.FinnhubHistoryError:
            # No credential -> the whole source cannot fulfill; let the chain
            # continue rather than crashing the runner thread.
            return None
        except Exception as exc:  # never raise into the runner
            print(f"  [WARN] finnhub_source {symbol}: history_8q raised: {exc}")
            history = None
        if history is not None:
            fields["history_8q"] = history
            fields["history_8q_source"] = self.name
        else:
            missing.append("history_8q")

        # --- in-quarter EPS actual / estimate / surprise ---
        try:
            inq = finnhub_history.fetch_in_quarter(symbol)
        except Exception as exc:
            print(f"  [WARN] finnhub_source {symbol}: in_quarter raised: {exc}")
            inq = None
        if inq:
            rvc: dict = {}
            if inq.get("eps_actual") is not None:
                rvc["in_quarter_eps_actual"] = inq["eps_actual"]
            if inq.get("eps_estimate") is not None:
                rvc["in_quarter_eps_estimate"] = inq["eps_estimate"]
                rvc["in_quarter_eps_consensus"] = inq["eps_estimate"]
            if inq.get("eps_surprise_pct") is not None:
                rvc["in_quarter_eps_surprise_pct"] = inq["eps_surprise_pct"]
            # Stamp provenance only on the values actually supplied.
            if rvc.get("in_quarter_eps_actual") is not None:
                rvc["in_quarter_eps_actual_source"] = self.name
                rvc["eps_actual_source"] = self.name
            if rvc.get("in_quarter_eps_consensus") is not None:
                rvc["eps_consensus_source"] = self.name
            if rvc.get("in_quarter_eps_surprise_pct") is not None:
                rvc["eps_surprise_source"] = self.name
            if rvc:
                fields["results_vs_consensus"] = rvc
        else:
            missing.append("in_quarter_eps_actual")

        if not fields:
            return None
        return self.response(symbol, fields, missing=missing)
