"""Tests for CachedFallbackSource and its position in the refresh_ticker chain.

The cached fallback is the LAST source in the chain (PR #46 left a residual 24
quarantined mature tickers where the live structured sources transiently returned
null history_8q). It re-emits on-disk fields ONLY when each is inside its
per-field freshness window, never serves in-quarter actuals or stale news, and
never half-fixes a short history_8q.

Covers the 7 cases from the PR spec:
1. test_cached_within_window_returned
2. test_cached_outside_window_filtered
3. test_never_cache_in_quarter_actuals
4. test_history_8q_filters_to_reported_only
5. test_no_cache_file
6. test_chain_integration
7. test_chain_skipped_when_live_succeeds
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.pipeline import refresh_ticker as rt
from automation.sources.base import EarningsSource, SourceResponse
from automation.sources.cached_source import CachedFallbackSource


NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _eight_reported_quarters() -> list[dict]:
    """A clean 8Q array, each quarter carrying both a revenue and EPS actual."""
    return [
        {
            "period_end": f"2026-{((i % 4) + 1) * 3:02d}-01",
            "fiscal_quarter": (i % 4) + 1,
            "fiscal_year": 2026 - (i // 4),
            "revenue_actual": 1_000_000 + i,
            "eps_actual": 1.5 - i * 0.1,
        }
        for i in range(8)
    ]


def _write_cache(tmp_path: Path, symbol: str, record: dict) -> Path:
    path = tmp_path / "earnings_intel.json"
    path.write_text(json.dumps({"tickers": {symbol: record}}))
    return path


# -------------------------------------------------------------------
# 1. Field inside its window is returned, tagged _source="cached"
# -------------------------------------------------------------------
def test_cached_within_window_returned(tmp_path):
    """history_8q stamped 30 days ago (window 92d) -> returned as cached."""
    rec = {
        "ticker": "AKAM",
        "history_8q": _eight_reported_quarters(),
        "history_8q_updated_at": _iso(30),
    }
    path = _write_cache(tmp_path, "AKAM", rec)
    src = CachedFallbackSource(cache_path=path)

    resp = src.fetch("AKAM", needed_fields=["history_8q"], now=NOW)

    assert resp is not None
    assert resp.fields["history_8q_source"] == "cached"
    assert resp.fields["history_8q_source_age_days"] == 30
    assert len(resp.fields["history_8q"]) == 8


# -------------------------------------------------------------------
# 2. Field older than its window is filtered out
# -------------------------------------------------------------------
def test_cached_outside_window_filtered(tmp_path):
    """consensus_eps_next_q 30 days old (window 14d) -> NOT returned."""
    rec = {
        "ticker": "AKAM",
        "consensus_eps_next_q": 2.10,
        "consensus_eps_next_q_updated_at": _iso(30),
    }
    path = _write_cache(tmp_path, "AKAM", rec)
    src = CachedFallbackSource(cache_path=path)

    resp = src.fetch("AKAM", needed_fields=["consensus_eps_next_q"], now=NOW)

    # No fresh field to offer -> source defers (None).
    assert resp is None


# -------------------------------------------------------------------
# 3. in_quarter actuals are NEVER served from cache, even when fresh
# -------------------------------------------------------------------
def test_never_cache_in_quarter_actuals(tmp_path):
    """in_quarter_eps_actual stamped 1 day ago -> NOT returned (never cacheable)."""
    rec = {
        "ticker": "AKAM",
        "in_quarter_eps_actual": 1.61,
        "in_quarter_eps_actual_updated_at": _iso(1),
    }
    path = _write_cache(tmp_path, "AKAM", rec)
    src = CachedFallbackSource(cache_path=path)

    resp = src.fetch(
        "AKAM", needed_fields=["in_quarter_eps_actual"], now=NOW
    )

    assert resp is None


# -------------------------------------------------------------------
# 4. history_8q filters to reported-only; < 8 reported -> omitted entirely
# -------------------------------------------------------------------
def test_history_8q_filters_to_reported_only(tmp_path):
    """7 reported + 1 future quarter -> only 7 survive the filter -> omitted."""
    history = _eight_reported_quarters()[:7]
    # A future, not-yet-reported quarter: actuals are null.
    history.insert(0, {
        "period_end": "2026-09-01",
        "fiscal_quarter": 3,
        "fiscal_year": 2026,
        "revenue_actual": None,
        "eps_actual": None,
    })
    rec = {
        "ticker": "AKAM",
        "history_8q": history,
        "history_8q_updated_at": _iso(10),
    }
    path = _write_cache(tmp_path, "AKAM", rec)
    src = CachedFallbackSource(cache_path=path)

    resp = src.fetch("AKAM", needed_fields=["history_8q"], now=NOW)

    # 7 reported quarters < 8 -> no half-fix, field omitted -> source defers.
    assert resp is None


# -------------------------------------------------------------------
# 5. Missing cache file -> clean defer (None), no crash
# -------------------------------------------------------------------
def test_no_cache_file(tmp_path):
    """A missing earnings_intel.json must not raise; the source defers."""
    src = CachedFallbackSource(cache_path=tmp_path / "does_not_exist.json")

    resp = src.fetch("AKAM", needed_fields=["history_8q"], now=NOW)

    assert resp is None


# -------------------------------------------------------------------
# Chain integration helpers
# -------------------------------------------------------------------
class _NullSource(EarningsSource):
    """A live source that always defers (returns None)."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def set_context(self, ctx):  # PerplexitySource compat (anchor call)
        pass

    def fetch(self, symbol):
        self.calls.append(symbol)
        return None


class _ActualsOnlySource(EarningsSource):
    """A live source supplying current in-quarter actuals but NO history_8q.

    Mirrors the residual-24 failure mode: the live structured sources have this
    quarter's print but transiently miss the trailing 8Q array.
    """

    def __init__(self, name):
        self.name = name

    def set_context(self, ctx):
        pass

    def fetch(self, symbol):
        return self.response(symbol, {
            "results_vs_consensus": {
                "in_quarter_rev_actual": 1_070_000,
                "in_quarter_rev_actual_source": self.name,
                "in_quarter_eps_actual": 1.61,
                "in_quarter_eps_actual_source": self.name,
                "in_quarter_rev_surprise_pct": -2.25,
                "rev_surprise_source": self.name,
                "in_quarter_eps_surprise_pct": -1.4,
                "eps_surprise_source": self.name,
            },
        }, missing=["history_8q"])


class _SpyCached(CachedFallbackSource):
    """CachedFallbackSource that records whether fetch was consulted."""

    def __init__(self, cache_path):
        super().__init__(cache_path=cache_path)
        self.consulted = False

    def fetch(self, symbol, *, needed_fields=None, now=None):
        self.consulted = True
        return super().fetch(symbol, needed_fields=needed_fields, now=now)


class _FullLiveSource(EarningsSource):
    """A live source that returns a complete record in one shot."""

    name = "perplexity_finance"

    def set_context(self, ctx):
        pass

    def fetch(self, symbol):
        return self.response(symbol, {
            "history_8q": _eight_reported_quarters(),
            "history_8q_source": self.name,
            "results_vs_consensus": {
                "in_quarter_rev_actual": 1_070_000,
                "in_quarter_eps_actual": 1.61,
                "in_quarter_rev_surprise_pct": -2.25,
                "in_quarter_eps_surprise_pct": -1.4,
            },
        })


def _post_record_reported_50h_ago() -> dict:
    """A POST record reported >48h ago (so completeness is REQUIRED)."""
    reported = (NOW - timedelta(hours=72)).date().isoformat()
    return {
        "ticker": "AKAM",
        "state": "post_earnings",
        "last_reported_date": reported,
        "results_vs_consensus": {
            "in_quarter_rev_actual": 1_070_000,
            "in_quarter_eps_actual": 1.61,
            "in_quarter_rev_surprise_pct": -2.25,
            "in_quarter_eps_surprise_pct": -1.4,
        },
        "history_8q": _eight_reported_quarters(),
        "history_8q_updated_at": _iso(20),
    }


@pytest.fixture
def _isolated_paths(tmp_path, monkeypatch):
    """Redirect every committed JSON artifact into tmp_path for the test."""
    intel = tmp_path / "earnings_intel.json"
    status_path = tmp_path / "pipeline_status.json"
    quar_path = tmp_path / "quarantine.json"
    monkeypatch.setattr(rt, "INTEL_PATH", intel)
    monkeypatch.setattr(rt.status, "STATUS_PATH", status_path)
    monkeypatch.setattr(rt.quarantine, "QUARANTINE_PATH", quar_path)
    return intel


# -------------------------------------------------------------------
# 6. Full chain: live sources null + fresh cache -> ok, cached fields, all 5 tried
# -------------------------------------------------------------------
def test_chain_integration(_isolated_paths, monkeypatch):
    """All live structured sources null, cache fresh -> ok via cached fallback."""
    intel = _isolated_paths
    rec = _post_record_reported_50h_ago()
    # Wipe the live-supplied payload from the prior record so the chain must
    # actually re-source history_8q + rvc (the live sources return null here).
    base = {
        "ticker": "AKAM",
        "state": "post_earnings",
        "last_reported_date": rec["last_reported_date"],
    }
    intel.write_text(json.dumps({"tickers": {"AKAM": base}}))

    # Separate cache file holding the prior-good, still-fresh record.
    cache_file = _isolated_paths.parent / "cache_copy.json"
    cache_file.write_text(json.dumps({"tickers": {"AKAM": rec}}))

    # Live sources supply this quarter's actuals but miss history_8q (the
    # residual-24 failure mode); cache supplies the still-fresh history_8q.
    live_pf = _ActualsOnlySource("perplexity_finance")
    null_fh = _NullSource("finnhub")
    null_yf = _NullSource("yfinance")
    null_px = _NullSource("perplexity")
    cached = _SpyCached(cache_path=cache_file)

    monkeypatch.setattr(
        rt, "_ordered_sources",
        lambda: [live_pf, null_fh, null_yf, null_px, cached],
    )

    result = rt.refresh_ticker("AKAM", force=True)

    assert result.ok is True
    assert cached.consulted is True
    assert result.sources_tried == [
        "perplexity_finance", "finnhub", "yfinance", "perplexity", "cached",
    ]
    written = json.loads(intel.read_text())["tickers"]["AKAM"]
    assert written["history_8q_source"] == "cached"
    assert "cached_fallback_reason" in written
    assert "history_8q" in written["cached_fallback_reason"]


# -------------------------------------------------------------------
# 7. Live success short-circuits before the cached source is consulted
# -------------------------------------------------------------------
def test_chain_skipped_when_live_succeeds(_isolated_paths, monkeypatch):
    """A full live record completes the chain -> cached source is never consulted."""
    intel = _isolated_paths
    reported = (NOW - timedelta(hours=72)).date().isoformat()
    base = {"ticker": "AKAM", "state": "post_earnings", "last_reported_date": reported}
    intel.write_text(json.dumps({"tickers": {"AKAM": base}}))

    cache_file = _isolated_paths.parent / "cache_copy.json"
    cache_file.write_text(json.dumps({"tickers": {"AKAM": _post_record_reported_50h_ago()}}))

    full = _FullLiveSource()
    cached = _SpyCached(cache_path=cache_file)
    cached.fetch = MagicMock(wraps=cached.fetch)

    monkeypatch.setattr(
        rt, "_ordered_sources",
        lambda: [full, _NullSource("finnhub"), _NullSource("yfinance"),
                 _NullSource("perplexity"), cached],
    )

    result = rt.refresh_ticker("AKAM", force=True)

    assert result.ok is True
    cached.fetch.assert_not_called()
    written = json.loads(intel.read_text())["tickers"]["AKAM"]
    assert written["history_8q_source"] == "perplexity_finance"
    assert "cached_fallback_reason" not in written
