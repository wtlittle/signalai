"""Tests for the SHORT_HISTORY_TICKERS allowlist in automation/pipeline/schema.py.

Recent IPOs (SNDK, SOC) lack 8 quarters of public history. The pipeline accepts
their max-available history instead of quarantining them, but ONLY for symbols
explicitly listed in SHORT_HISTORY_TICKERS -- every other ticker still needs the
full 8Q array. These tests pin that contract:

  * an allowlisted symbol with >= its per-ticker minimum is complete and tagged
    partial_history_accepted;
  * a NON-allowlisted symbol with the same short history is still incomplete;
  * an allowlisted symbol BELOW its per-ticker minimum is still incomplete.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.pipeline.schema import (  # noqa: E402
    HISTORY_8Q_REQUIRED_QUARTERS,
    SHORT_HISTORY_TICKERS,
    partial_history_accepted,
    partial_history_note,
    record_completeness,
)


def _raw_with_history(n_quarters: int) -> dict:
    """A post_earnings record whose results_vs_consensus is fully populated and
    whose history_8q carries ``n_quarters`` fully-reported quarters."""
    return {
        "ticker": "TEST",
        "state": "post_earnings",
        "results_vs_consensus": {
            "in_quarter_rev_actual": 100.0,
            "in_quarter_eps_actual": 1.0,
            "in_quarter_rev_surprise_pct": 2.0,
            "in_quarter_eps_surprise_pct": 3.0,
        },
        "history_8q": [
            {"period_end": f"2025-{q:02d}-01", "revenue_actual": 10.0, "eps_actual": 0.5}
            for q in range(1, n_quarters + 1)
        ],
    }


def test_sndk_four_quarters_is_complete():
    """SNDK is allowlisted at 4 quarters: a 4Q history clears the gate and is
    tagged as accepted partial history."""
    assert SHORT_HISTORY_TICKERS["SNDK"] == 4
    raw = _raw_with_history(4)
    assert record_completeness(raw, symbol="SNDK") == []
    assert partial_history_accepted(raw["history_8q"], "SNDK") is True
    assert partial_history_note(raw["history_8q"], "SNDK") == (
        f"partial history accepted: 4/{HISTORY_8Q_REQUIRED_QUARTERS} (recent IPO)"
    )


def test_aapl_four_quarters_is_incomplete():
    """AAPL is NOT allowlisted, so a 4Q history is still an incomplete record."""
    assert "AAPL" not in SHORT_HISTORY_TICKERS
    raw = _raw_with_history(4)
    gaps = record_completeness(raw, symbol="AAPL")
    assert gaps == [f"history_8q (4/{HISTORY_8Q_REQUIRED_QUARTERS} quarters)"]
    assert partial_history_accepted(raw["history_8q"], "AAPL") is False
    assert partial_history_note(raw["history_8q"], "AAPL") is None


def test_sndk_three_quarters_is_incomplete():
    """SNDK below its 4-quarter minimum is still incomplete (no free pass under
    the allowlisted threshold)."""
    raw = _raw_with_history(3)
    gaps = record_completeness(raw, symbol="SNDK")
    assert gaps == [f"history_8q (3/{HISTORY_8Q_REQUIRED_QUARTERS} quarters)"]
    assert partial_history_accepted(raw["history_8q"], "SNDK") is False
    assert partial_history_note(raw["history_8q"], "SNDK") is None
