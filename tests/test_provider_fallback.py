"""Tests for the provider fallback chain in the queue executor.

Covers:
1. Provider chain falls through when higher-priority providers return None
2. Provider chain falls through when higher-priority providers raise
3. Provider chain stops at the first provider that returns data
4. Source field is added to cache results
5. FactSet MCP stub always returns None
6. All task types have a provider chain registered
7. execute_tasks_for_ticker records source attribution
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.jobs.execute_queue import (
    PROVIDER_CHAINS,
    PRE_EARNINGS_TASKS,
    POST_EARNINGS_TASKS,
    PEER_TASK,
    TASK_FETCHERS,
    _run_provider_chain,
    _add_source_field,
    _factset_stub,
    _safe_float,
    execute_tasks_for_ticker,
)


# -------------------------------------------------------------------
# 1. All task types have provider chains
# -------------------------------------------------------------------
def test_all_tasks_have_provider_chains():
    """Every pre/post/peer task type has a provider chain."""
    all_tasks = PRE_EARNINGS_TASKS + POST_EARNINGS_TASKS + [PEER_TASK]
    for task in all_tasks:
        assert task in PROVIDER_CHAINS, f"Missing chain for task: {task}"
        chain = PROVIDER_CHAINS[task]
        assert len(chain) >= 2, f"Chain for {task} should have at least 2 providers"
        # First provider should always be factset_mcp
        assert chain[0][0] == "factset_mcp", (
            f"First provider in chain for {task} should be factset_mcp"
        )


# -------------------------------------------------------------------
# 2. Legacy TASK_FETCHERS backward compat
# -------------------------------------------------------------------
def test_task_fetchers_backward_compat():
    """TASK_FETCHERS should contain an entry for every task."""
    all_tasks = PRE_EARNINGS_TASKS + POST_EARNINGS_TASKS + [PEER_TASK]
    for task in all_tasks:
        assert task in TASK_FETCHERS, f"Missing in TASK_FETCHERS: {task}"
        assert callable(TASK_FETCHERS[task])


# -------------------------------------------------------------------
# 3. FactSet stub always returns None
# -------------------------------------------------------------------
def test_factset_stub_returns_none():
    """FactSet MCP stub should always return None when not available."""
    for task in PRE_EARNINGS_TASKS + POST_EARNINGS_TASKS:
        result = _factset_stub("TEST", task)
        assert result is None


# -------------------------------------------------------------------
# 4. Provider chain falls through on None
# -------------------------------------------------------------------
def test_chain_falls_through_on_none():
    """When higher-priority providers return None, chain falls to next."""
    def _null_provider(ticker):
        return None

    def _good_provider(ticker):
        return {"test": True}

    test_chain = [
        ("factset_mcp", _null_provider),
        ("finnhub", _null_provider),
        ("yfinance", _good_provider),
    ]
    with patch.dict(PROVIDER_CHAINS, {"test_task": test_chain}):
        data, source = _run_provider_chain("TEST", "test_task")

    assert data == {"test": True}
    assert source == "yfinance"


# -------------------------------------------------------------------
# 5. Provider chain falls through on exception
# -------------------------------------------------------------------
def test_chain_falls_through_on_exception():
    """When a provider raises, chain continues to the next."""
    def _bad_provider(ticker):
        raise ConnectionError("network error")

    def _good_provider(ticker):
        return {"result": "ok"}

    test_chain = [
        ("factset_mcp", _bad_provider),
        ("yfinance", _good_provider),
    ]
    with patch.dict(PROVIDER_CHAINS, {"test_task": test_chain}):
        data, source = _run_provider_chain("TEST", "test_task")

    assert data == {"result": "ok"}
    assert source == "yfinance"


# -------------------------------------------------------------------
# 6. Provider chain stops at first success
# -------------------------------------------------------------------
def test_chain_stops_at_first_success():
    """Chain should not call lower-priority providers after a success."""
    call_log = []

    def _first(ticker):
        call_log.append("first")
        return {"from": "first"}

    def _second(ticker):
        call_log.append("second")
        return {"from": "second"}

    test_chain = [
        ("factset_mcp", _first),
        ("yfinance", _second),
    ]
    with patch.dict(PROVIDER_CHAINS, {"test_task": test_chain}):
        data, source = _run_provider_chain("TEST", "test_task")

    assert data == {"from": "first"}
    assert source == "factset_mcp"
    assert call_log == ["first"], "Should not have called the second provider"


# -------------------------------------------------------------------
# 7. All providers fail returns (None, "all_failed")
# -------------------------------------------------------------------
def test_chain_all_fail():
    """When all providers fail, return (None, 'all_failed')."""
    def _null(ticker):
        return None

    test_chain = [
        ("factset_mcp", _null),
        ("yfinance", _null),
    ]
    with patch.dict(PROVIDER_CHAINS, {"test_task": test_chain}):
        data, source = _run_provider_chain("TEST", "test_task")

    assert data is None
    assert source == "all_failed"


# -------------------------------------------------------------------
# 8. Unknown task returns (None, "no_chain")
# -------------------------------------------------------------------
def test_chain_unknown_task():
    """An unknown task type should return (None, 'no_chain')."""
    data, source = _run_provider_chain("TEST", "completely_unknown_task_xyz")
    assert data is None
    assert source == "no_chain"


# -------------------------------------------------------------------
# 9. _add_source_field on dict
# -------------------------------------------------------------------
def test_add_source_field_dict():
    """Source field should be added to dict results."""
    data = {"price": 100.0}
    result = _add_source_field(data, "yfinance")
    assert result["_source"] == "yfinance"
    assert result["price"] == 100.0


# -------------------------------------------------------------------
# 10. _add_source_field on list
# -------------------------------------------------------------------
def test_add_source_field_list():
    """Source field wraps list results in a dict."""
    data = [{"period": "Q1"}, {"period": "Q2"}]
    result = _add_source_field(data, "finnhub")
    assert isinstance(result, dict)
    assert result["_source"] == "finnhub"
    assert result["data"] == data


# -------------------------------------------------------------------
# 11. execute_tasks_for_ticker tracks sources
# -------------------------------------------------------------------
def test_execute_tasks_tracks_sources():
    """Successful fetches should record source attribution."""
    def _mock_chain(ticker, task):
        return {"test": True}, "perplexity"

    with patch(
        "automation.jobs.execute_queue.research_cache_exists",
        return_value=False,
    ), patch(
        "automation.jobs.execute_queue._run_provider_chain",
        side_effect=_mock_chain,
    ), patch(
        "automation.jobs.execute_queue.save_research_cache",
    ):
        result = execute_tasks_for_ticker("TEST", ["finance_quote"])

    assert result["success"] == 1
    assert result["sources"].get("finance_quote") == "perplexity"


# -------------------------------------------------------------------
# 12. execute_tasks_for_ticker handles all_failed
# -------------------------------------------------------------------
def test_execute_tasks_handles_all_failed():
    """When the chain returns all_failed, task should be marked failed."""
    def _mock_chain(ticker):
        return None, "all_failed"

    with patch(
        "automation.jobs.execute_queue.research_cache_exists",
        return_value=False,
    ), patch(
        "automation.jobs.execute_queue._run_provider_chain",
        return_value=(None, "all_failed"),
    ):
        result = execute_tasks_for_ticker("TEST", ["finance_quote"])

    assert result["failed"] == 1
    assert result["success"] == 0


# -------------------------------------------------------------------
# 13. Cache hit skips provider chain
# -------------------------------------------------------------------
def test_cache_hit_skips_chain():
    """Cached tasks should not invoke the provider chain."""
    with patch(
        "automation.jobs.execute_queue.research_cache_exists",
        return_value=True,
    ):
        result = execute_tasks_for_ticker(
            "TEST", ["finance_quote", "finance_estimates"],
        )
    assert result["skipped"] == 2
    assert result["success"] == 0


# -------------------------------------------------------------------
# 14. _safe_float edge cases
# -------------------------------------------------------------------
def test_safe_float_valid():
    assert _safe_float(42.5) == 42.5
    assert _safe_float("3.14") == 3.14
    assert _safe_float(0) == 0.0


def test_safe_float_none_and_nan():
    assert _safe_float(None) is None
    assert _safe_float("not a number") is None
    assert _safe_float(float("nan")) is None


# -------------------------------------------------------------------
# 15. Dry run skips all
# -------------------------------------------------------------------
def test_dry_run_skips_all():
    result = execute_tasks_for_ticker(
        "TEST", PRE_EARNINGS_TASKS, dry_run=True,
    )
    assert result["skipped"] == len(PRE_EARNINGS_TASKS)
    assert result["success"] == 0
