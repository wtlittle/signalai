"""Tests for the queue executor dispatch logic.

Covers:
1. TASK_FETCHERS dispatch table completeness
2. Individual fetcher return shape validation
3. execute_tasks_for_ticker skips cached tasks
4. execute_tasks_for_ticker handles fetcher returning None
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.jobs.execute_queue import (
    TASK_FETCHERS,
    PRE_EARNINGS_TASKS,
    POST_EARNINGS_TASKS,
    PEER_TASK,
    execute_tasks_for_ticker,
    _safe_float,
)


# -------------------------------------------------------------------
# 1. Dispatch table covers all known task types
# -------------------------------------------------------------------
def test_dispatch_table_completeness():
    """Every pre/post/peer task type has a registered fetcher."""
    all_tasks = PRE_EARNINGS_TASKS + POST_EARNINGS_TASKS + [PEER_TASK]
    for task in all_tasks:
        assert task in TASK_FETCHERS, f"Missing fetcher for task: {task}"
        assert callable(TASK_FETCHERS[task]), f"Fetcher for {task} is not callable"


# -------------------------------------------------------------------
# 2. _safe_float edge cases
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
# 3. execute_tasks_for_ticker uses cache
# -------------------------------------------------------------------
def test_execute_tasks_skips_cached():
    """Tasks with existing cache entries should be skipped."""
    with patch(
        "automation.jobs.execute_queue.research_cache_exists",
        return_value=True,
    ):
        result = execute_tasks_for_ticker(
            "TEST", ["finance_quote", "finance_estimates"],
        )
    assert result["skipped"] == 2
    assert result["success"] == 0
    assert result["failed"] == 0


# -------------------------------------------------------------------
# 4. execute_tasks_for_ticker handles None from fetcher
# -------------------------------------------------------------------
def test_execute_tasks_records_none_as_failure():
    """A fetcher returning None should be recorded as a failure."""
    def _null_fetcher(ticker):
        return None

    with patch(
        "automation.jobs.execute_queue.research_cache_exists",
        return_value=False,
    ), patch.dict(TASK_FETCHERS, {"finance_quote": _null_fetcher}):
        result = execute_tasks_for_ticker("TEST", ["finance_quote"])

    assert result["failed"] == 1
    assert result["success"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["error"] == "fetcher returned None"


# -------------------------------------------------------------------
# 5. execute_tasks_for_ticker handles fetcher exceptions
# -------------------------------------------------------------------
def test_execute_tasks_records_exception_as_failure():
    """A fetcher that raises should be caught and recorded."""
    def _bad_fetcher(ticker):
        raise ValueError("test error")

    with patch(
        "automation.jobs.execute_queue.research_cache_exists",
        return_value=False,
    ), patch.dict(TASK_FETCHERS, {"finance_quote": _bad_fetcher}):
        result = execute_tasks_for_ticker("TEST", ["finance_quote"])

    assert result["failed"] == 1
    assert "test error" in result["errors"][0]["error"]


# -------------------------------------------------------------------
# 6. execute_tasks_for_ticker dry_run mode
# -------------------------------------------------------------------
def test_execute_tasks_dry_run():
    """Dry run should skip all tasks without fetching."""
    result = execute_tasks_for_ticker(
        "TEST", PRE_EARNINGS_TASKS, dry_run=True,
    )
    assert result["skipped"] == len(PRE_EARNINGS_TASKS)
    assert result["success"] == 0
    assert result["failed"] == 0


# -------------------------------------------------------------------
# 7. Unknown task type is skipped
# -------------------------------------------------------------------
def test_execute_tasks_unknown_task_skipped():
    """A task type not in TASK_FETCHERS should be skipped."""
    result = execute_tasks_for_ticker("TEST", ["nonexistent_task_xyz"])
    assert result["skipped"] == 1
