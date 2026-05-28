"""Tests for the weekly briefing module."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from automation.jobs.weekly_briefing import compile_briefing, run, _extract_list, _extract_dict


# --- Fixtures ---

FIXTURE_VALUE = [
    {
        "ticker": "GPN",
        "name": "Global Payments Inc.",
        "price": 73.26,
        "high_52w": 90.64,
        "low_52w": 62.45,
        "pct_off_high": "-19.2%",
        "market_cap": "$18.5B",
        "pe_ratio": 26.9,
        "ev_ebitda": 9.3,
        "revenue_growth": "35%",
        "fcf_yield": "6.1%",
        "fcf_ttm": "$2.1B",
        "debt_equity": 0.83,
        "analyst_target": 89.18,
        "analyst_consensus": "Buy",
        "why_undervalued": "Trades at 9.3x EV/EBITDA vs 15-18x historical range.",
        "bull_case": "Worldpay integration unlocks margin expansion."
    },
    {
        "ticker": "STZ",
        "name": "Constellation Brands",
        "price": 149.5,
        "high_52w": 186.4,
        "low_52w": 126.45,
        "pct_off_high": "-19.8%",
        "market_cap": "$28B",
        "pe_ratio": 15.6,
        "ev_ebitda": 11.9,
        "revenue_growth": "4%",
        "fcf_yield": "7.0%",
        "fcf_ttm": "$3.1B",
        "debt_equity": 1.16,
        "analyst_target": 175.93,
        "analyst_consensus": "Buy",
        "why_undervalued": "Beer pure-play after divestitures not yet repriced.",
        "bull_case": "FIFA World Cup 2026 catalyst for Modelo brand."
    },
]

FIXTURE_MOMENTUM = [
    {
        "ticker": "ARM",
        "name": "Arm Holdings plc",
        "price": 306.51,
        "one_week_perf": "46%",
        "one_month_perf": "55%",
        "three_month_perf": "120%",
        "revenue_growth": "25%",
        "catalyst": "NVIDIA earnings confirmed AI capex cycle.",
        "risk_reward": "90x forward P/E leaves little room for error."
    },
]

FIXTURE_TRENDS = {
    "index_returns": {
        "sp500": {"close": 7473.47, "weekly_return": "0.88%", "ytd_return": "9.14%"},
        "nasdaq": {"close": 26343.97, "weekly_return": "0.45%", "ytd_return": "17.03%"},
        "russell2000": {"close": 2851.0, "weekly_return": "2.71%", "ytd_return": "14.61%"},
    },
    "trends": [
        "AI capex super-cycle continues",
        "Cybersecurity re-rates higher",
        "Fed transition risk under Warsh",
    ],
    "risks": [
        "Hawkish Fed surprise",
        "Long-end yield breakout above 5.25%",
    ],
    "watchlist_movers": [
        {"ticker": "ARM", "weekly_move": "+46%", "thirty_day_move": "+55%", "catalyst": "NVIDIA earnings halo", "detail": "AGI CPU $2B commitments"},
    ],
    "narrative": "S&P 500 posted eighth consecutive weekly gain as AI capex cycle remained intact.",
}


class TestExtractHelpers(unittest.TestCase):
    """Test the extraction helpers for Perplexity response handling."""

    def test_extract_list_from_list(self):
        self.assertEqual(_extract_list([1, 2, 3]), [1, 2, 3])

    def test_extract_list_from_dict_with_raw_list(self):
        self.assertEqual(_extract_list({"raw": [1, 2]}), [1, 2])

    def test_extract_list_from_dict_with_raw_string(self):
        self.assertEqual(_extract_list({"raw": "not json"}), [])

    def test_extract_list_from_none(self):
        self.assertEqual(_extract_list(None), [])

    def test_extract_dict_normal(self):
        d = {"index_returns": {}, "trends": []}
        self.assertEqual(_extract_dict(d), d)

    def test_extract_dict_from_raw_response(self):
        self.assertEqual(_extract_dict({"raw": "some text"}), {})

    def test_extract_dict_from_non_dict(self):
        self.assertEqual(_extract_dict("string"), {})


class TestCompileBriefing(unittest.TestCase):
    """Test the compile step merges responses correctly."""

    def test_compile_with_valid_data(self):
        result = compile_briefing(FIXTURE_VALUE, FIXTURE_MOMENTUM, FIXTURE_TRENDS)

        self.assertIn("generated", result)
        self.assertIn("week_ending", result)
        self.assertEqual(len(result["value_picks"]), 2)
        self.assertEqual(result["value_picks"][0]["ticker"], "GPN")
        self.assertEqual(len(result["momentum_picks"]), 1)
        self.assertEqual(result["momentum_picks"][0]["ticker"], "ARM")
        self.assertEqual(len(result["trends"]), 3)
        self.assertEqual(len(result["risks"]), 2)
        self.assertEqual(len(result["watchlist_movers"]), 1)
        self.assertIn("sp500", result["index_returns"])
        self.assertIn("narrative", result)

    def test_compile_with_empty_responses(self):
        result = compile_briefing([], [], {})
        self.assertEqual(result["value_picks"], [])
        self.assertEqual(result["momentum_picks"], [])
        self.assertEqual(result["index_returns"], {})
        self.assertEqual(result["trends"], [])
        self.assertEqual(result["risks"], [])

    def test_compile_with_raw_fallback(self):
        """When Perplexity returns unparsed text, compile handles gracefully."""
        result = compile_briefing(
            {"raw": "unparsed value text"},
            {"raw": "unparsed momentum text"},
            {"raw": "unparsed trends text"},
        )
        self.assertEqual(result["value_picks"], [])
        self.assertEqual(result["momentum_picks"], [])
        self.assertEqual(result["index_returns"], {})

    def test_compile_schema_keys(self):
        """Output has all required top-level keys."""
        result = compile_briefing(FIXTURE_VALUE, FIXTURE_MOMENTUM, FIXTURE_TRENDS)
        required_keys = {
            "generated", "week_ending", "value_picks", "momentum_picks",
            "index_returns", "trends", "risks", "watchlist_movers", "narrative",
        }
        self.assertEqual(set(result.keys()), required_keys)


def _mock_call_perplexity(ticker, task, prompt, **kwargs):
    """Dispatch mock responses by task name (thread-safe for parallel calls)."""
    if task == "weekly_value":
        return FIXTURE_VALUE
    elif task == "weekly_momentum":
        return FIXTURE_MOMENTUM
    elif task == "weekly_trends":
        return FIXTURE_TRENDS
    return {}


class TestRunFunction(unittest.TestCase):
    """Test the full run() function with mocked Perplexity calls."""

    @patch("automation.jobs.weekly_briefing.call_perplexity", side_effect=_mock_call_perplexity)
    @patch("automation.jobs.weekly_briefing.load_tickers", return_value=["AAPL", "MSFT", "GOOG"])
    def test_run_writes_output(self, mock_tickers, mock_pplx):
        """run() writes valid JSON to the output path."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test_briefing.json"
            result = run(output_path=out)

            self.assertTrue(out.exists())
            data = json.loads(out.read_text())
            self.assertEqual(len(data["value_picks"]), 2)
            self.assertEqual(len(data["momentum_picks"]), 1)
            self.assertIn("sp500", data["index_returns"])

    @patch("automation.jobs.weekly_briefing.call_perplexity", side_effect=_mock_call_perplexity)
    @patch("automation.jobs.weekly_briefing.load_tickers", return_value=["AAPL"])
    def test_run_calls_three_tasks(self, mock_tickers, mock_pplx):
        """run() makes exactly 3 Perplexity calls."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.json"
            run(output_path=out)
            self.assertEqual(mock_pplx.call_count, 3)

            tasks = [call[0][1] for call in mock_pplx.call_args_list]
            self.assertIn("weekly_value", tasks)
            self.assertIn("weekly_momentum", tasks)
            self.assertIn("weekly_trends", tasks)


class TestTaskSourceMap(unittest.TestCase):
    """Verify weekly briefing tasks are mapped in the usage logger."""

    def test_weekly_tasks_in_source_map(self):
        from automation.perplexity.client import _TASK_SOURCE_MAP
        for task in ("weekly_value", "weekly_momentum", "weekly_trends",
                     "weekly_briefing_value", "weekly_briefing_momentum", "weekly_briefing_trends"):
            self.assertEqual(_TASK_SOURCE_MAP[task], "weekly_briefing",
                             f"Task {task} not mapped to weekly_briefing")

    def test_weekly_tasks_in_model_map(self):
        from automation.perplexity.client import TASK_MODEL_MAP
        for task in ("weekly_value", "weekly_momentum", "weekly_trends"):
            self.assertEqual(TASK_MODEL_MAP[task], "sonar-deep-research",
                             f"Task {task} should use sonar-deep-research")


@unittest.skipUnless(os.environ.get("RUN_INTEGRATION") == "1", "Set RUN_INTEGRATION=1 to run")
class TestIntegration(unittest.TestCase):
    """Integration test that hits the real Perplexity API."""

    def test_deep_research_response_shape(self):
        """Verify sonar-deep-research returns a parseable response."""
        result = call_perplexity(
            "TEST", "weekly_value",
            'Return a JSON array with one object: [{"ticker": "AAPL", "name": "Apple"}]',
            system="Return only a JSON array.",
            max_tokens=500,
            force=True,
        )
        # Should be either a list or a dict (with parsed content)
        self.assertIsInstance(result, (list, dict))
        # Should not be a queue/skip response in integration mode
        if isinstance(result, dict):
            self.assertNotIn("queued", result)
            self.assertNotIn("skipped", result)


if __name__ == "__main__":
    unittest.main()
