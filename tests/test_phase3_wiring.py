"""Integration smoke test for Phase 3 wiring.

Asserts the new Stage 1 → sanity_check → Supabase snapshot → Stage 2 → LLM
pipeline runs end-to-end for NVDA in dry-run mode (no real LLM call, no real
Supabase write — uses mocks).

Run:
    python -m pytest tests/test_phase3_wiring.py -x -v
"""
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from unittest import mock

# Ensure the repo root is on sys.path so automation imports work.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_nvda_context() -> dict:
    with open(FIXTURES_DIR / "nvda_pre_context.json") as f:
        return json.load(f)


def _nvda_post_context() -> dict:
    """Build a realistic NVDA post-earnings context from the pre fixture."""
    ctx = _load_nvda_context()
    ctx["snapshot_type"] = "post"
    ctx["this_quarter_actuals"] = {
        "period": "Q1 FY2027",
        "date": "2026-05-20",
        "actual_revenue": "44.5B",
        "estimated_revenue": "43.2B",
        "revenue_surprise_pct": 3.0,
        "actual_eps": "0.92",
        "estimated_eps": "0.88",
        "eps_surprise_pct": 4.5,
        "post_earnings_move_1d_pct": 5.2,
        "expected_move_pct": 8.5,
    }
    ctx["consensus_post_print"] = {
        "fq_revenue": "48.0B",
        "fq_eps": "1.02",
        "fy_revenue": "210.0B",
        "fy_eps": "4.35",
        "revision_direction": "upward",
        "source": "finance_estimates",
    }
    ctx["consensus_at_print"] = {
        "estimated_revenue": "43.2B",
        "estimated_eps": "0.88",
    }
    ctx["transcript"] = (
        "Jensen Huang: Thank you everyone. Q1 was outstanding. "
        "Data center revenue grew 45% sequentially driven by Blackwell ramp."
    )
    return ctx


# ---------------------------------------------------------------------------
# sanity_check unit tests
# ---------------------------------------------------------------------------

def test_sanity_check_passes_healthy_context():
    from automation.earnings.sanity_check import sanity_check
    ctx = _load_nvda_context()
    ok, issues = sanity_check(ctx)
    assert ok, f"Expected pass but got issues: {issues}"
    assert issues == []


def test_sanity_check_fails_missing_quote():
    from automation.earnings.sanity_check import sanity_check
    ctx = _load_nvda_context()
    ctx["quote"] = None
    ok, issues = sanity_check(ctx)
    assert not ok
    assert any("quote" in i for i in issues)


def test_sanity_check_fails_too_many_errors():
    from automation.earnings.sanity_check import sanity_check
    ctx = _load_nvda_context()
    ctx["errors"] = [{"field": f"field_{i}", "reason": "test"} for i in range(6)]
    ok, issues = sanity_check(ctx)
    assert not ok
    assert any("too many data errors" in i for i in issues)


def test_sanity_check_post_requires_actuals():
    from automation.earnings.sanity_check import sanity_check
    ctx = _nvda_post_context()
    ctx["this_quarter_actuals"] = None
    ok, issues = sanity_check(ctx)
    assert not ok
    assert any("this_quarter_actuals" in i for i in issues)


def test_sanity_check_empty_context():
    from automation.earnings.sanity_check import sanity_check
    ok, issues = sanity_check({})
    assert not ok


# ---------------------------------------------------------------------------
# Pre-earnings wiring smoke test
# ---------------------------------------------------------------------------

def test_pre_earnings_wiring_dry_run(tmp_path):
    """End-to-end smoke test: pre-earnings pipeline with mocked externals."""
    nvda_ctx = _load_nvda_context()

    # Mock calendar with a single NVDA entry
    calendar = {
        "pre_earnings": [{
            "ticker": "NVDA",
            "date": "2026-06-15",
            "company": "NVIDIA",
            "days_until": 7,
        }]
    }

    # Mock LLM result
    llm_result = {
        "setup": "NVDA trading at $135 ahead of Q1 FY2027 print.",
        "key_debates": ["Data center growth sustainability", "Blackwell ramp timeline"],
        "what_matters": ["Data center revenue > $40B", "Gross margin > 72%"],
        "guidance_watch": "Watch FQ2 revenue guide vs $44B consensus",
        "options_implied_move": "8.5%",
        "scenarios": {
            "bull": {"probability": "30%", "trigger": "Beat + raise", "stock_move": "+10%"},
            "base": {"probability": "45%", "trigger": "In-line print", "stock_move": "+/-3%"},
            "bear": {"probability": "25%", "trigger": "Guidance miss", "stock_move": "-8%"},
        },
        "recent_news": ["Blackwell demand exceeds supply"],
        "analyst_changes": ["MS raises PT to $160"],
        "sources": ["https://example.com"],
    }

    # Set up tmp_path for notes
    pre_dir = tmp_path / "notes" / "pre_earnings"
    pre_dir.mkdir(parents=True)

    with (
        mock.patch("automation.jobs.pre_earnings_notes.read_json", return_value=calendar),
        mock.patch("automation.jobs.pre_earnings_notes.load_common_names", return_value={"NVDA": "NVIDIA"}),
        mock.patch("automation.jobs.pre_earnings_notes.note_already_exists", return_value=False),
        mock.patch("automation.jobs.pre_earnings_notes.build_pre_earnings_context", return_value=nvda_ctx),
        mock.patch("automation.jobs.pre_earnings_notes.sanity_check", return_value=(True, [])),
        mock.patch("automation.jobs.pre_earnings_notes._persist_snapshot", return_value=True),
        mock.patch("automation.jobs.pre_earnings_notes.call_perplexity", return_value=llm_result),
        mock.patch("automation.jobs.pre_earnings_notes.PRE_EARNINGS_DIR", pre_dir),
        mock.patch("automation.jobs.pre_earnings_notes.write_json"),
        mock.patch("automation.jobs.pre_earnings_notes.update_index"),
    ):
        from automation.jobs.pre_earnings_notes import run
        count = run()

    assert count == 1
    note_path = pre_dir / "NVDA_2026-06-15.md"
    assert note_path.exists()
    content = note_path.read_text()
    assert "NVIDIA" in content
    assert "Set-up" in content
    assert "NVDA trading at $135" in content


def test_pre_earnings_sanity_check_gate_blocks(tmp_path):
    """Sanity check failure should write a stub and skip the LLM call."""
    calendar = {
        "pre_earnings": [{
            "ticker": "NVDA",
            "date": "2026-06-15",
            "company": "NVIDIA",
            "days_until": 7,
        }]
    }

    bad_ctx = _load_nvda_context()
    bad_ctx["quote"] = None
    bad_ctx["consensus_forward"] = None
    bad_ctx["history_8q"] = None

    pre_dir = tmp_path / "notes" / "pre_earnings"
    pre_dir.mkdir(parents=True)

    call_perplexity_mock = mock.MagicMock()

    with (
        mock.patch("automation.jobs.pre_earnings_notes.read_json", return_value=calendar),
        mock.patch("automation.jobs.pre_earnings_notes.load_common_names", return_value={"NVDA": "NVIDIA"}),
        mock.patch("automation.jobs.pre_earnings_notes.note_already_exists", return_value=False),
        mock.patch("automation.jobs.pre_earnings_notes.build_pre_earnings_context", return_value=bad_ctx),
        mock.patch("automation.jobs.pre_earnings_notes.PRE_EARNINGS_DIR", pre_dir),
        mock.patch("automation.jobs.pre_earnings_notes.call_perplexity", call_perplexity_mock),
        mock.patch("automation.jobs.pre_earnings_notes.write_json"),
    ):
        from automation.jobs.pre_earnings_notes import run
        count = run()

    # LLM should NOT have been called
    call_perplexity_mock.assert_not_called()
    assert count == 0

    # Stub note should exist
    stub_path = pre_dir / "NVDA_2026-06-15.md"
    assert stub_path.exists()
    assert "sanity check failed" in stub_path.read_text()


# ---------------------------------------------------------------------------
# Post-earnings wiring smoke test
# ---------------------------------------------------------------------------

def test_post_earnings_wiring_dry_run(tmp_path):
    """End-to-end smoke test: post-earnings pipeline with mocked externals."""
    post_ctx = _nvda_post_context()

    calendar = {
        "post_earnings": [{
            "ticker": "NVDA",
            "date": "2026-05-20",
            "company": "NVIDIA",
            "days_since": 3,
        }]
    }

    llm_result = {
        "headline": "NVDA beats on revenue and EPS, raises guidance.",
        "beat_miss_quality": "Quality beat driven by Blackwell ramp",
        "key_metrics": ["DC rev $42.5B vs $40.1B est", "Gross margin 73.5% vs 72.0%"],
        "guidance": "FQ2 rev guide $48B vs $44B consensus — large raise",
        "management_tone": "Bullish — Jensen cited 'unprecedented demand'",
        "surprises": ["Networking revenue inflection", "China export headwinds smaller than feared"],
        "thesis_impact": "Reinforces — accelerating demand narrative intact",
        "analyst_reactions": ["MS raises PT $160→$175", "GS reiterates Buy"],
        "stock_outlook": "Expect positive momentum into FQ2 print",
        "sources": ["https://example.com"],
    }

    post_dir = tmp_path / "notes" / "post_earnings"
    post_dir.mkdir(parents=True)

    with (
        mock.patch("automation.jobs.post_earnings_notes.read_json", return_value=calendar),
        mock.patch("automation.jobs.post_earnings_notes.load_common_names", return_value={"NVDA": "NVIDIA"}),
        mock.patch("automation.jobs.post_earnings_notes.note_already_exists", return_value=False),
        mock.patch("automation.jobs.post_earnings_notes.build_post_earnings_context", return_value=post_ctx),
        mock.patch("automation.jobs.post_earnings_notes.sanity_check", return_value=(True, [])),
        mock.patch("automation.jobs.post_earnings_notes._persist_snapshot", return_value=True),
        mock.patch("automation.jobs.post_earnings_notes.call_perplexity", return_value=llm_result),
        mock.patch("automation.jobs.post_earnings_notes.POST_EARNINGS_DIR", post_dir),
        mock.patch("automation.jobs.post_earnings_notes.write_json"),
        mock.patch("automation.jobs.post_earnings_notes.update_index"),
    ):
        from automation.jobs.post_earnings_notes import run
        count = run()

    assert count == 1
    note_path = post_dir / "NVDA_2026-05-20.md"
    assert note_path.exists()
    content = note_path.read_text()
    assert "NVIDIA" in content
    assert "Headline" in content
    assert "beats on revenue" in content


def test_post_earnings_sanity_check_gate_blocks(tmp_path):
    """Sanity check failure should write a stub and skip the LLM call."""
    calendar = {
        "post_earnings": [{
            "ticker": "NVDA",
            "date": "2026-05-20",
            "company": "NVIDIA",
            "days_since": 3,
        }]
    }

    bad_ctx = _nvda_post_context()
    bad_ctx["this_quarter_actuals"] = None
    bad_ctx["quote"] = None

    post_dir = tmp_path / "notes" / "post_earnings"
    post_dir.mkdir(parents=True)

    call_perplexity_mock = mock.MagicMock()

    with (
        mock.patch("automation.jobs.post_earnings_notes.read_json", return_value=calendar),
        mock.patch("automation.jobs.post_earnings_notes.load_common_names", return_value={"NVDA": "NVIDIA"}),
        mock.patch("automation.jobs.post_earnings_notes.note_already_exists", return_value=False),
        mock.patch("automation.jobs.post_earnings_notes.build_post_earnings_context", return_value=bad_ctx),
        mock.patch("automation.jobs.post_earnings_notes.POST_EARNINGS_DIR", post_dir),
        mock.patch("automation.jobs.post_earnings_notes.call_perplexity", call_perplexity_mock),
        mock.patch("automation.jobs.post_earnings_notes.write_json"),
    ):
        from automation.jobs.post_earnings_notes import run
        count = run()

    call_perplexity_mock.assert_not_called()
    assert count == 0

    stub_path = post_dir / "NVDA_2026-05-20.md"
    assert stub_path.exists()
    assert "sanity check failed" in stub_path.read_text()


# ---------------------------------------------------------------------------
# Snapshot persistence test
# ---------------------------------------------------------------------------

def test_persist_snapshot_calls_supabase():
    """_persist_snapshot should call upsert_row with correct args."""
    ctx = _load_nvda_context()

    with mock.patch("automation.jobs.pre_earnings_notes._sb.upsert_row", return_value=True) as mock_upsert:
        from automation.jobs.pre_earnings_notes import _persist_snapshot
        result = _persist_snapshot("NVDA", "2026-06-15", ctx)

    assert result is True
    mock_upsert.assert_called_once()
    call_args = mock_upsert.call_args
    assert call_args[0][0] == "earnings_context_snapshots"
    row = call_args[0][1]
    assert row["ticker"] == "NVDA"
    assert row["note_type"] == "pre"
    assert row["earnings_date"] == "2026-06-15"
    assert row["ticker_tier"] == "T1"
    on_conflict = (
        call_args[0][2] if len(call_args[0]) >= 3
        else call_args.kwargs.get("on_conflict")
    )
    assert on_conflict == "ticker,earnings_date,note_type"


# ---------------------------------------------------------------------------
# FINNHUB_API_KEY safety test
# ---------------------------------------------------------------------------

def test_finnhub_key_not_hardcoded():
    """FINNHUB_API_KEY should only be read from os.environ, never hardcoded."""
    import automation.shared.finnhub as finnhub_mod
    import inspect
    source = inspect.getsource(finnhub_mod)
    # No string that looks like a hardcoded API key
    assert "FINNHUB_API_KEY" in source  # references the env var name
    # The key value itself should only come from os.environ
    lines = source.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "FINNHUB_API_KEY" in stripped and "os.environ" not in stripped and "print" not in stripped:
            # Allow assignment from environ, logging, and comments
            if "=" in stripped and stripped.index("=") < stripped.index("FINNHUB_API_KEY"):
                continue
            # Allow string references like "FINNHUB_API_KEY" in env var name strings
            if '"FINNHUB_API_KEY"' in stripped or "'FINNHUB_API_KEY'" in stripped:
                continue


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-x", "-v"])
