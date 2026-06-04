"""
yfinance earnings source (priority 3, the traditional fallback).

Supplies what the free yfinance feed can: the most-recent in-quarter revenue
actual (from quarterly_income_stmt) and, as a last-resort, a trailing-quarter
history array (reusing the existing ``history_8q._fetch_yfinance`` joiner so the
8Q logic is not duplicated). yfinance commonly returns < 8 quarters, in which
case the merged record simply stays short and the completeness gate -- not this
source -- decides quarantine.

DATA INTEGRITY: revenue actual is the raw reported top-line; no consensus and no
surprise are emitted (yfinance has no revenue consensus, and a surprise without
a real consensus would be fabricated). EPS is left to Finnhub, which supplies a
same-basis estimate/surprise.
"""
from __future__ import annotations

from typing import Optional

from automation.sources import history_8q as _history_8q
from automation.sources.base import EarningsSource, SourceResponse


class YFinanceSource(EarningsSource):
    name = "yfinance"

    def _latest_revenue(self, symbol: str) -> Optional[float]:
        """Most-recent quarterly Total Revenue from yfinance, or None."""
        try:
            import yfinance as yf
        except ImportError:
            return None
        try:
            income = yf.Ticker(symbol).quarterly_income_stmt
        except Exception as exc:
            print(f"  [WARN] yfinance_source {symbol}: income stmt error: {exc}")
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
        # Columns are quarter-end timestamps; the first is the most recent.
        for _, val in rev_row.items():
            if val is None:
                continue
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            if f != f:  # NaN guard (yfinance pads missing cells with NaN)
                continue
            return f
        return None

    def fetch(self, symbol: str) -> Optional[SourceResponse]:
        symbol = symbol.upper().strip()
        fields: dict = {}
        missing: list[str] = []

        rev = self._latest_revenue(symbol)
        if rev is not None:
            rvc = {
                "in_quarter_rev_actual": round(rev),
                "in_quarter_rev_actual_source": self.name,
                "rev_actual_source": self.name,
            }
            fields["results_vs_consensus"] = rvc
        else:
            missing.append("in_quarter_rev_actual")

        # Last-resort 8Q history (only used if a higher-priority source did not
        # already supply a complete array; the merge keeps the higher one).
        try:
            history = _history_8q._fetch_yfinance(symbol)
        except Exception as exc:
            print(f"  [WARN] yfinance_source {symbol}: history raised: {exc}")
            history = None
        if history:
            fields["history_8q"] = history
            fields["history_8q_source"] = self.name
        else:
            missing.append("history_8q")

        if not fields:
            return None
        return self.response(symbol, fields, missing=missing)
