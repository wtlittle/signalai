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
