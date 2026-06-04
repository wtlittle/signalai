"""Tests for PR #31 earnings pipeline unification.

Covers:
  * daily_refresh.self_heal_state_machine -- flips a past-dated pre_earnings
    ticker, leaves a future-dated one alone, and never touches post_earnings.
  * build_earnings_json POST card sourcing -- pulls REV/EPS from
    earnings_intel.json when present, falls back to None ("n/a") otherwise.
"""
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.jobs.daily_refresh import self_heal_state_machine


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "build_earnings_json", REPO_ROOT / "build_earnings_json.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# self_heal_state_machine
# ---------------------------------------------------------------------------

def _write_intel(tmp_path, tickers):
    p = tmp_path / "earnings_intel.json"
    p.write_text(json.dumps({"tickers": tickers}, indent=2))
    return p


def test_self_heal_flips_past_dated_pre_ticker(tmp_path):
    today = date(2026, 6, 3)
    yesterday = (today - timedelta(days=1)).isoformat()
    p = _write_intel(tmp_path, {
        "PANW": {"state": "pre_earnings", "next_earnings_date": yesterday},
    })

    transitions = self_heal_state_machine(intel_path=p, today=today)

    assert len(transitions) == 1
    assert transitions[0]["ticker"] == "PANW"

    healed = json.loads(p.read_text())["tickers"]["PANW"]
    assert healed["state"] == "post_earnings"
    assert healed["last_reported_date"] == yesterday
    # Canonical field that backfill_earnings_from_finnhub.py +
    # backfill_revenue_from_yfinance.py read. Both must be populated so
    # self-healed tickers auto-backfill EPS + revenue from the data providers.
    assert healed["last_earnings_date"] == yesterday
    assert healed["next_earnings_date"] is None
    assert healed["refresh_reason"] == "self_heal_pre_to_post_transition"


def test_self_heal_leaves_future_dated_pre_ticker(tmp_path):
    today = date(2026, 6, 3)
    tomorrow = (today + timedelta(days=1)).isoformat()
    p = _write_intel(tmp_path, {
        "GTLB": {"state": "pre_earnings", "next_earnings_date": tomorrow},
    })

    transitions = self_heal_state_machine(intel_path=p, today=today)

    assert transitions == []
    unchanged = json.loads(p.read_text())["tickers"]["GTLB"]
    assert unchanged["state"] == "pre_earnings"
    assert unchanged["next_earnings_date"] == tomorrow


def test_self_heal_ignores_post_earnings_records(tmp_path):
    today = date(2026, 6, 3)
    past = (today - timedelta(days=5)).isoformat()
    p = _write_intel(tmp_path, {
        "ADP": {
            "state": "post_earnings",
            "next_earnings_date": past,
            "results_vs_consensus": {"in_quarter_rev_actual": "$5.939B"},
        },
    })

    transitions = self_heal_state_machine(intel_path=p, today=today)

    assert transitions == []
    record = json.loads(p.read_text())["tickers"]["ADP"]
    assert record["state"] == "post_earnings"
    # Real data is untouched -- self-heal never fabricates or clears actuals.
    assert record["results_vs_consensus"]["in_quarter_rev_actual"] == "$5.939B"


def test_self_heal_same_day_is_not_flipped(tmp_path):
    today = date(2026, 6, 3)
    p = _write_intel(tmp_path, {
        "MDB": {"state": "pre_earnings", "next_earnings_date": today.isoformat()},
    })
    # next_earnings_date == today is NOT strictly in the past, so no flip.
    assert self_heal_state_machine(intel_path=p, today=today) == []


# ---------------------------------------------------------------------------
# build_earnings_json POST card sourcing
# ---------------------------------------------------------------------------

def test_build_post_card_sources_from_intel():
    mod = _load_build_module()
    intel_results = {
        "PANW": {
            "results_vs_consensus": {
                "in_quarter_rev_actual": "$2.5B",
                "in_quarter_rev_surprise_pct": 1.4,
                "in_quarter_eps_actual": "$0.89",
                "in_quarter_eps_surprise_pct": 3.1,
            },
        }
    }
    card = mod.build_post_card("PANW", intel_results)
    assert card["revenue_actual"] == "$2.5B"
    assert card["revenue_surprise_pct"] == 1.4
    assert card["eps_actual"] == "$0.89"
    assert card["eps_surprise_pct"] == 3.1


def test_build_post_card_missing_intel_renders_none():
    mod = _load_build_module()
    card = mod.build_post_card("GTLB", {})
    assert card["revenue_actual"] is None
    assert card["eps_actual"] is None
    assert card["revenue_surprise_pct"] is None
    assert card["eps_surprise_pct"] is None


def test_build_calendar_recent_uses_intel(monkeypatch):
    mod = _load_build_module()
    # Pin TODAY so the date math is deterministic.
    fixed_today = mod.datetime(2026, 6, 3)
    monkeypatch.setattr(mod, "TODAY", fixed_today)

    reported = (date(2026, 6, 3) - timedelta(days=1)).isoformat()
    yf_data = {"all_tickers": {"PANW": {"earnings_dates": [reported]}}}
    intel_results = {
        "PANW": {
            "results_vs_consensus": {
                "in_quarter_rev_actual": "$2.5B",
                "in_quarter_eps_actual": "$0.89",
            },
        }
    }
    cal = mod.build_calendar(yf_data, intel_results, timings={})
    assert len(cal["recent"]) == 1
    row = cal["recent"][0]
    assert row["ticker"] == "PANW"
    assert row["revenue_actual"] == "$2.5B"
    assert row["eps_actual"] == "$0.89"


# ---------------------------------------------------------------------------
# Guardrail A/C: per-field provenance + no-fabrication
# ---------------------------------------------------------------------------

def test_build_post_card_resolves_canonical_source():
    mod = _load_build_module()
    intel_results = {
        "PANW": {
            "results_vs_consensus": {
                "in_quarter_rev_actual": "$3.00B",
                "in_quarter_rev_actual_source": "perplexity",
                "in_quarter_eps_actual": "$0.85",
                "in_quarter_eps_actual_source": "finnhub",
            },
        }
    }
    card = mod.build_post_card("PANW", intel_results)
    assert card["revenue_source"] == "perplexity"
    assert card["eps_source"] == "finnhub"


def test_build_post_card_falls_back_to_legacy_source_names():
    # RC-PROVENANCE-NAME-MISMATCH: existing records carry rev_actual_source /
    # eps_consensus_source (legacy). build must still surface them so the card
    # tooltip works without rewriting earnings_intel.json.
    mod = _load_build_module()
    intel_results = {
        "MDB": {
            "results_vs_consensus": {
                "in_quarter_rev_actual": "$687.62M",
                "rev_actual_source": "yfinance_quarterly_income_stmt",
                "in_quarter_eps_actual": "$1.32",
                "eps_consensus_source": "finnhub_stock_earnings",
            },
        }
    }
    card = mod.build_post_card("MDB", intel_results)
    assert card["revenue_source"] == "yfinance_quarterly_income_stmt"
    assert card["eps_source"] == "finnhub_stock_earnings"


def test_no_source_when_actual_missing():
    # A source must NEVER be attributed to a value that does not exist.
    mod = _load_build_module()
    intel_results = {
        "PSTG": {
            "results_vs_consensus": {
                # No rev/eps actual at all, but a stray legacy source key.
                "rev_actual_source": "yfinance_quarterly_income_stmt",
            },
        }
    }
    card = mod.build_post_card("PSTG", intel_results)
    assert card["revenue_actual"] is None
    assert card["revenue_source"] is None
    assert card["eps_actual"] is None
    assert card["eps_source"] is None


def test_no_numeric_card_field_when_intel_value_is_none():
    """Core DATA INTEGRITY assertion (Guardrail C): when an intel actual is
    None, the corresponding card field must be None -- never a literal number,
    zero, or any fabricated default. Asserts across a matrix of partial intel.
    """
    mod = _load_build_module()
    cases = [
        {},  # no results_vs_consensus at all
        {"results_vs_consensus": {}},  # empty envelope
        {"results_vs_consensus": {"in_quarter_eps_actual": "$1.16"}},  # rev only missing
        {"results_vs_consensus": {"in_quarter_rev_actual": "$264M"}},  # eps only missing
    ]
    numeric_fields = (
        "revenue_actual", "eps_actual",
        "revenue_surprise_pct", "eps_surprise_pct",
    )
    for entry in cases:
        card = mod.build_post_card("X", {"X": entry})
        rvc = entry.get("results_vs_consensus") or {}
        for field, intel_key in (
            ("revenue_actual", "in_quarter_rev_actual"),
            ("eps_actual", "in_quarter_eps_actual"),
            ("revenue_surprise_pct", "in_quarter_rev_surprise_pct"),
            ("eps_surprise_pct", "in_quarter_eps_surprise_pct"),
        ):
            if rvc.get(intel_key) is None:
                assert card[field] is None, (
                    f"{field} fabricated a value ({card[field]!r}) when intel "
                    f"{intel_key} was None"
                )
        # And whatever IS present must equal the intel value verbatim (no coercion).
        for field, intel_key in (
            ("revenue_actual", "in_quarter_rev_actual"),
            ("eps_actual", "in_quarter_eps_actual"),
        ):
            if rvc.get(intel_key) is not None:
                assert card[field] == rvc[intel_key]
        # Sanity: ensure no numeric field is a bare 0 substituted for a gap.
        for field in numeric_fields:
            assert card[field] != 0
