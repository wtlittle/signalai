"""Tests for Perplexity API usage logging and cost estimation."""
import unittest
from unittest.mock import patch, MagicMock

from automation.shared.perplexity_pricing import estimate_cost, PRICING
from automation.perplexity.client import _log_pplx_usage


class TestEstimateCost(unittest.TestCase):
    """estimate_cost returns correct values for known token counts."""

    def test_sonar_basic(self):
        cost = estimate_cost(
            model="sonar",
            prompt_tokens=1000,
            completion_tokens=500,
            search_context_size="low",
        )
        # input: 1000 * 1.0 / 1M = 0.001
        # output: 500 * 1.0 / 1M = 0.0005
        # request: 5 / 1K = 0.005
        self.assertAlmostEqual(cost, 0.0065, places=6)

    def test_sonar_pro_medium_context(self):
        cost = estimate_cost(
            model="sonar-pro",
            prompt_tokens=2000,
            completion_tokens=1000,
            search_context_size="medium",
        )
        # input: 2000 * 3.0 / 1M = 0.006
        # output: 1000 * 15.0 / 1M = 0.015
        # request: 10 / 1K = 0.01
        self.assertAlmostEqual(cost, 0.031, places=6)

    def test_sonar_reasoning_pro(self):
        cost = estimate_cost(
            model="sonar-reasoning-pro",
            prompt_tokens=5000,
            completion_tokens=2000,
            reasoning_tokens=1000,
            search_context_size="high",
        )
        # input: 5000 * 2.0 / 1M = 0.01
        # output: 2000 * 8.0 / 1M = 0.016
        # reasoning: no reasoning key for sonar-reasoning-pro
        # request: 14 / 1K = 0.014
        self.assertAlmostEqual(cost, 0.04, places=6)

    def test_sonar_deep_research(self):
        cost = estimate_cost(
            model="sonar-deep-research",
            prompt_tokens=10000,
            completion_tokens=5000,
            reasoning_tokens=3000,
            citation_tokens=2000,
            num_search_queries=10,
        )
        # input: 10000 * 2.0 / 1M = 0.02
        # output: 5000 * 8.0 / 1M = 0.04
        # reasoning: 3000 * 3.0 / 1M = 0.009
        # citation: 2000 * 2.0 / 1M = 0.004
        # search: 10 * 5.0 / 1K = 0.05
        # no request fee for deep-research
        self.assertAlmostEqual(cost, 0.123, places=6)

    def test_unknown_model_returns_none(self):
        cost = estimate_cost(model="gpt-4", prompt_tokens=100, completion_tokens=100)
        self.assertIsNone(cost)

    def test_zero_tokens(self):
        cost = estimate_cost(model="sonar", prompt_tokens=0, completion_tokens=0)
        # Only request fee: 5 / 1K = 0.005
        self.assertAlmostEqual(cost, 0.005, places=6)

    def test_none_tokens(self):
        cost = estimate_cost(model="sonar", prompt_tokens=None, completion_tokens=None)
        # Only request fee
        self.assertAlmostEqual(cost, 0.005, places=6)

    def test_all_models_have_pricing(self):
        for model in PRICING:
            cost = estimate_cost(model=model, prompt_tokens=1000, completion_tokens=500)
            self.assertIsNotNone(cost)
            self.assertGreater(cost, 0)


class TestLogPplxUsage(unittest.TestCase):
    """Logger handles edge cases gracefully."""

    @patch("automation.perplexity.client.insert_row")
    def test_handles_missing_usage_field(self, mock_insert):
        """Logger works when response has no 'usage' key."""
        mock_insert.return_value = True
        _log_pplx_usage(
            task="pre_earnings",
            ticker="AAPL",
            model="sonar-pro",
            response_json={"choices": [{"message": {"content": "{}"}}]},
            latency_ms=500,
            status="success",
        )
        mock_insert.assert_called_once()
        row = mock_insert.call_args[0][1]
        self.assertEqual(row["source"], "pre_earnings_notes")
        self.assertIsNone(row["prompt_tokens"])

    @patch("automation.perplexity.client.insert_row")
    def test_handles_none_response(self, mock_insert):
        """Logger works when response_json is None (error case)."""
        mock_insert.return_value = True
        _log_pplx_usage(
            task="daily_news",
            ticker="MSFT",
            model="sonar",
            response_json=None,
            latency_ms=100,
            status="error",
            error=Exception("connection timeout"),
        )
        mock_insert.assert_called_once()
        row = mock_insert.call_args[0][1]
        self.assertEqual(row["status"], "error")
        self.assertIn("connection timeout", row["error_message"])

    @patch("automation.perplexity.client.insert_row", side_effect=Exception("DB down"))
    def test_does_not_raise_on_insert_failure(self, mock_insert):
        """Logger swallows exceptions from Supabase insert."""
        # This must NOT raise
        _log_pplx_usage(
            task="news_tag",
            ticker="GOOG",
            model="sonar",
            response_json={"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            latency_ms=200,
            status="success",
        )
        mock_insert.assert_called_once()

    @patch("automation.perplexity.client.insert_row")
    def test_source_mapping(self, mock_insert):
        """Task names map to correct source labels."""
        mock_insert.return_value = True
        _log_pplx_usage(
            task="weekly_value",
            ticker="MARKET",
            model="sonar-deep-research",
            response_json={"usage": {}},
            latency_ms=300,
            status="success",
        )
        row = mock_insert.call_args[0][1]
        self.assertEqual(row["source"], "weekly_briefing")

    @patch("automation.perplexity.client.insert_row")
    def test_full_usage_fields_captured(self, mock_insert):
        """All usage fields from the response are captured."""
        mock_insert.return_value = True
        usage = {
            "prompt_tokens": 500,
            "completion_tokens": 200,
            "citation_tokens": 100,
            "reasoning_tokens": 150,
            "total_tokens": 950,
            "num_search_queries": 3,
        }
        _log_pplx_usage(
            task="post_earnings",
            ticker="NVDA",
            model="sonar-reasoning-pro",
            response_json={"usage": usage},
            latency_ms=1200,
            status="success",
            search_context_size="medium",
        )
        row = mock_insert.call_args[0][1]
        self.assertEqual(row["prompt_tokens"], 500)
        self.assertEqual(row["completion_tokens"], 200)
        self.assertEqual(row["citation_tokens"], 100)
        self.assertEqual(row["reasoning_tokens"], 150)
        self.assertEqual(row["total_tokens"], 950)
        self.assertEqual(row["num_search_queries"], 3)
        self.assertEqual(row["search_context_size"], "medium")
        self.assertIsNotNone(row["estimated_cost_usd"])
        self.assertEqual(row["source"], "post_earnings_notes")


if __name__ == "__main__":
    unittest.main()
