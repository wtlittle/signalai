"""Tests for sync_earnings_intel_from_notes.py -- JSON envelope parsing,
beat/miss derivation, calendar sync, idempotency, and legacy fallback.
"""
import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.sync_earnings_intel_from_notes import (
    _beat_miss_label,
    _extract_json_envelope,
    _safe_float,
    build_post_intel,
    build_pre_intel,
    merge_intel,
    sync_calendar_from_intel,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_POST_NOTE_WITH_ENVELOPES = """\
# Acme Corp (ACME) -- Post-Earnings Note

## Headline

Acme beat Q1 revenue and EPS estimates with raised full-year guidance.

**Beat/Miss Quality:** Beat/Beat - clean quality, organic growth driven.

## Key Metrics

- Revenue $12.5B vs $12.0B estimate (+4.2% surprise)
- EPS $1.85 vs $1.75 estimate (+5.7% surprise)
- Gross margin 62.3% vs 61.0% estimate
- Free cash flow $3.2B vs $2.9B estimate

## Guidance and Tone

FY revenue guide raised to $52B from $50.5B; consensus was $51.0B.
FY EPS guide raised to $7.80 from $7.50; consensus was $7.60.

**Management Tone:** Confident and upbeat; highlighted strong demand pipeline.

## Surprises / Disappointments

- Gross margin expansion exceeded expectations by 130bp
- Backlog grew 15% QoQ, well above the 8% consensus expectation

## Thesis Impact

Beat reinforces the bull thesis that Acme can sustain above-market growth.

## Near-Term Outlook

Watch for continued momentum in cloud segment next quarter.

```json results_vs_consensus
{
  "in_quarter_rev_actual": "12.5B",
  "in_quarter_rev_consensus": "12.0B",
  "in_quarter_rev_surprise_pct": 4.2,
  "in_quarter_rev_yoy_pct": 18.5,
  "in_quarter_eps_actual": "1.85",
  "in_quarter_eps_consensus": "1.75",
  "in_quarter_eps_surprise_pct": 5.7
}
```

```json guide_vs_consensus
{
  "next_q_rev_guide_midpoint": "13.2B",
  "next_q_rev_consensus_prior": "12.8B",
  "next_q_rev_guide_vs_consensus_pct": 3.1,
  "next_q_eps_guide_midpoint": "1.95",
  "next_q_eps_consensus_prior": "1.88",
  "next_q_eps_guide_vs_consensus_pct": 3.7,
  "fy_rev_guide_midpoint_new": "52.0B",
  "fy_rev_guide_midpoint_prior": "50.5B",
  "fy_rev_consensus_prior": "51.0B",
  "fy_rev_guide_change_vs_consensus_pct": 2.0,
  "fy_rev_guide_change_vs_prior_pct": 3.0,
  "fy_eps_guide_midpoint_new": "7.80",
  "fy_eps_guide_midpoint_prior": "7.50",
  "fy_eps_consensus_prior": "7.60",
  "fy_eps_guide_change_vs_consensus_pct": 2.6,
  "fy_eps_guide_change_vs_prior_pct": 4.0
}
```
"""

_PRE_NOTE_WITH_ENVELOPE = """\
# Acme Corp (ACME) -- Pre-Earnings Note

## Set-up

Acme approaches its 2026-06-15 print with elevated expectations after a strong Q4.

## Key Debates & Variant Perception

- Cloud revenue growth sustainability above 20%
- Margin trajectory as investment cycle peaks

## What Matters This Print

- Revenue guide vs consensus
- Cloud segment margin expansion

## Scenario Grid

| Scenario | Probability | Trigger | Stock Move |
|----------|------------|---------|------------|
| Bull     | 35%        | Beat + raise + cloud inflection | +8-12% |
| Base     | 45%        | In-line print, guide maintained | -2% to +3% |
| Bear     | 20%        | Miss or guide cut on macro | -10-15% |

```json setup_vs_consensus
{
  "street_rev_estimate": "12.0B",
  "street_eps_estimate": "1.75",
  "last_q_rev_surprise_pct": 3.5,
  "last_q_eps_surprise_pct": 4.1,
  "current_fy_consensus_rev": "51.0B",
  "current_fy_consensus_eps": "7.60",
  "prior_fy_guide_vs_current_consensus_pct": -1.0
}
```
"""

_POST_NOTE_NO_ENVELOPES = """\
# Legacy Corp (LGCY) -- Post-Earnings Note

## Headline

Legacy beat revenue estimates modestly.

**Beat/Miss Quality:** Beat/In-line - Revenue beat, EPS in line.

## Key Metrics

- Revenue $5.0B vs $4.9B estimate
- EPS $0.50 vs $0.50 estimate

## Guidance and Tone

Guidance maintained.

## Thesis Impact

Thesis intact; modest beat keeps narrative alive.
"""


# ---------------------------------------------------------------------------
# Test: _extract_json_envelope
# ---------------------------------------------------------------------------

def test_extract_results_vs_consensus():
    env = _extract_json_envelope(_POST_NOTE_WITH_ENVELOPES, "results_vs_consensus")
    assert env is not None
    assert env["in_quarter_rev_actual"] == "12.5B"
    assert env["in_quarter_eps_surprise_pct"] == 5.7
    assert env["in_quarter_rev_yoy_pct"] == 18.5


def test_extract_guide_vs_consensus():
    env = _extract_json_envelope(_POST_NOTE_WITH_ENVELOPES, "guide_vs_consensus")
    assert env is not None
    assert env["fy_rev_guide_change_vs_consensus_pct"] == 2.0
    assert env["fy_eps_guide_change_vs_prior_pct"] == 4.0
    assert env["next_q_rev_guide_vs_consensus_pct"] == 3.1


def test_extract_setup_vs_consensus():
    env = _extract_json_envelope(_PRE_NOTE_WITH_ENVELOPE, "setup_vs_consensus")
    assert env is not None
    assert env["street_rev_estimate"] == "12.0B"
    assert env["last_q_rev_surprise_pct"] == 3.5


def test_extract_missing_envelope_returns_none():
    env = _extract_json_envelope(_POST_NOTE_NO_ENVELOPES, "results_vs_consensus")
    assert env is None


def test_extract_malformed_json_returns_none():
    bad_note = '```json results_vs_consensus\n{not valid json}\n```'
    env = _extract_json_envelope(bad_note, "results_vs_consensus")
    assert env is None


# ---------------------------------------------------------------------------
# Test: _beat_miss_label
# ---------------------------------------------------------------------------

def test_beat_miss_label_beat():
    assert _beat_miss_label(4.2) == "beat (+4.2%)"


def test_beat_miss_label_miss():
    assert _beat_miss_label(-3.5) == "miss (-3.5%)"


def test_beat_miss_label_inline():
    assert _beat_miss_label(0.5) == "in-line"
    assert _beat_miss_label(-0.8) == "in-line"
    assert _beat_miss_label(0.0) == "in-line"


def test_beat_miss_label_threshold_boundary():
    # Exactly at threshold -- should be in-line
    assert _beat_miss_label(1.0) == "in-line"
    assert _beat_miss_label(-1.0) == "in-line"
    # Just over threshold
    assert _beat_miss_label(1.01).startswith("beat")
    assert _beat_miss_label(-1.01).startswith("miss")


def test_beat_miss_label_none():
    assert _beat_miss_label(None) == ""


# ---------------------------------------------------------------------------
# Test: _safe_float
# ---------------------------------------------------------------------------

def test_safe_float_number():
    assert _safe_float(3.14) == 3.14
    assert _safe_float(0) == 0.0


def test_safe_float_string():
    assert _safe_float("2.5") == 2.5


def test_safe_float_none():
    assert _safe_float(None) is None


def test_safe_float_invalid():
    assert _safe_float("not a number") is None


# ---------------------------------------------------------------------------
# Test: build_post_intel with envelopes
# ---------------------------------------------------------------------------

def _write_note_under_root(content: str, subdir: str = "notes/post_earnings", name: str = "TEST_NOTE.md") -> Path:
    """Write a temp note under ROOT so relative_to(ROOT) works."""
    import scripts.sync_earnings_intel_from_notes as mod
    note_dir = mod.ROOT / subdir
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / name
    note_path.write_text(content)
    return note_path


def test_build_post_intel_extracts_envelopes():
    note_path = _write_note_under_root(_POST_NOTE_WITH_ENVELOPES, name="_test_acme_post.md")
    try:
        entry = {"ticker": "ACME", "date": "2026-06-01", "day_post": 2, "company": "Acme Corp"}
        rec = build_post_intel("ACME", entry, note_path)

        assert "results_vs_consensus" in rec
        assert rec["results_vs_consensus"]["in_quarter_rev_actual"] == "12.5B"
        assert rec["results_vs_consensus"]["in_quarter_eps_surprise_pct"] == 5.7

        assert "guide_vs_consensus" in rec
        assert rec["guide_vs_consensus"]["fy_rev_guide_change_vs_consensus_pct"] == 2.0
        assert rec["guide_vs_consensus"]["next_q_eps_guide_vs_consensus_pct"] == 3.7
    finally:
        note_path.unlink(missing_ok=True)


def test_build_post_intel_no_envelopes_fallback():
    """Old notes without JSON envelopes should still parse without errors."""
    note_path = _write_note_under_root(_POST_NOTE_NO_ENVELOPES, name="_test_lgcy_post.md")
    try:
        entry = {"ticker": "LGCY", "date": "2026-05-15", "day_post": 5, "company": "Legacy Corp"}
        rec = build_post_intel("LGCY", entry, note_path)

        assert "results_vs_consensus" not in rec
        assert "guide_vs_consensus" not in rec
        assert rec["ticker"] == "LGCY"
        assert rec["bottom_line"]  # should still have a bottom line
    finally:
        note_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test: build_pre_intel with envelope
# ---------------------------------------------------------------------------

def test_build_pre_intel_extracts_setup_envelope():
    note_path = _write_note_under_root(
        _PRE_NOTE_WITH_ENVELOPE, subdir="notes/pre_earnings", name="_test_acme_pre.md"
    )
    try:
        entry = {"ticker": "ACME", "date": "2026-06-15", "company": "Acme Corp"}
        rec = build_pre_intel("ACME", entry, note_path)

        assert "setup_vs_consensus" in rec
        assert rec["setup_vs_consensus"]["street_rev_estimate"] == "12.0B"
        assert rec["setup_vs_consensus"]["prior_fy_guide_vs_current_consensus_pct"] == -1.0
    finally:
        note_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test: merge_intel preserves envelopes
# ---------------------------------------------------------------------------

def test_merge_preserves_existing_envelope():
    existing = {
        "ticker": "ACME",
        "results_vs_consensus": {"in_quarter_rev_actual": "12.5B"},
        "guide_vs_consensus": {"fy_rev_guide_change_vs_consensus_pct": 2.0},
    }
    fresh = {
        "ticker": "ACME",
        "intel_updated_at": "2026-06-02T00:00:00+00:00",
        "state": "post_earnings",
        "inflection_status": "POST",
        "results_vs_consensus": {"in_quarter_rev_actual": "12.6B"},
    }
    merged = merge_intel(existing, fresh, force_rebuild=False)
    # Existing envelope should be preserved (not overwritten)
    assert merged["results_vs_consensus"]["in_quarter_rev_actual"] == "12.5B"
    # guide_vs_consensus should remain from existing
    assert merged["guide_vs_consensus"]["fy_rev_guide_change_vs_consensus_pct"] == 2.0


def test_merge_fills_empty_envelope():
    existing = {"ticker": "ACME", "state": "post_earnings"}
    fresh = {
        "ticker": "ACME",
        "intel_updated_at": "2026-06-02T00:00:00+00:00",
        "state": "post_earnings",
        "inflection_status": "POST",
        "results_vs_consensus": {"in_quarter_rev_actual": "12.5B"},
    }
    merged = merge_intel(existing, fresh, force_rebuild=False)
    assert merged["results_vs_consensus"]["in_quarter_rev_actual"] == "12.5B"


# ---------------------------------------------------------------------------
# Test: sync_calendar_from_intel
# ---------------------------------------------------------------------------

def test_sync_calendar_updates_post_earnings(tmp_path):
    cal = {
        "post_earnings": [
            {"ticker": "ACME", "date": "2026-06-01", "earnings_date": "2026-06-01"},
            {"ticker": "OTHER", "date": "2026-05-28", "earnings_date": "2026-05-28"},
        ],
        "pre_earnings": [],
        "updated": "2026-06-01T00:00:00",
    }
    cal_path = tmp_path / "earnings_calendar.json"
    cal_path.write_text(json.dumps(cal))

    tickers = {
        "ACME": {
            "results_vs_consensus": {
                "in_quarter_rev_actual": "12.5B",
                "in_quarter_rev_surprise_pct": 4.2,
                "in_quarter_eps_actual": "1.85",
                "in_quarter_eps_surprise_pct": 5.7,
            },
            "guide_vs_consensus": {
                "fy_rev_guide_change_vs_consensus_pct": 2.0,
                "fy_eps_guide_change_vs_consensus_pct": 2.6,
                "next_q_rev_guide_vs_consensus_pct": 3.1,
                "next_q_eps_guide_vs_consensus_pct": 3.7,
            },
        },
    }

    import scripts.sync_earnings_intel_from_notes as mod
    orig_path = mod.CALENDAR_PATH
    try:
        mod.CALENDAR_PATH = cal_path
        count = sync_calendar_from_intel(tickers, dry_run=False)
    finally:
        mod.CALENDAR_PATH = orig_path

    assert count == 1

    updated_cal = json.loads(cal_path.read_text())
    acme = updated_cal["post_earnings"][0]
    assert acme["revenue_actual"] == "12.5B"
    assert acme["eps_actual"] == "1.85"
    assert acme["revenue_beat_miss"] == "beat (+4.2%)"
    assert acme["eps_beat_miss"] == "beat (+5.7%)"
    assert acme["fy_rev_guide_change_vs_consensus_pct"] == 2.0
    assert acme["fy_eps_guide_change_vs_consensus_pct"] == 2.6
    assert acme["next_q_rev_guide_vs_consensus_pct"] == 3.1

    # OTHER ticker should be untouched
    other = updated_cal["post_earnings"][1]
    assert "revenue_actual" not in other


def test_sync_calendar_idempotent(tmp_path):
    """Running sync twice produces identical output."""
    cal = {
        "post_earnings": [
            {"ticker": "ACME", "date": "2026-06-01", "earnings_date": "2026-06-01"},
        ],
        "pre_earnings": [],
        "updated": "2026-06-01T00:00:00",
    }
    cal_path = tmp_path / "earnings_calendar.json"
    cal_path.write_text(json.dumps(cal))

    tickers = {
        "ACME": {
            "results_vs_consensus": {
                "in_quarter_rev_actual": "12.5B",
                "in_quarter_rev_surprise_pct": 4.2,
                "in_quarter_eps_actual": "1.85",
                "in_quarter_eps_surprise_pct": 5.7,
            },
            "guide_vs_consensus": {
                "fy_rev_guide_change_vs_consensus_pct": 2.0,
                "fy_eps_guide_change_vs_consensus_pct": 2.6,
            },
        },
    }

    import scripts.sync_earnings_intel_from_notes as mod
    orig_path = mod.CALENDAR_PATH
    try:
        mod.CALENDAR_PATH = cal_path
        sync_calendar_from_intel(tickers, dry_run=False)
        first_run = json.loads(cal_path.read_text())

        # Second run -- should not change anything
        count = sync_calendar_from_intel(tickers, dry_run=False)
    finally:
        mod.CALENDAR_PATH = orig_path

    # Count is 0 because fields are already populated
    assert count == 0
    second_run = json.loads(cal_path.read_text())
    # Data should be identical (excluding timestamp which only updates when count > 0)
    assert first_run["post_earnings"] == second_run["post_earnings"]


def test_sync_calendar_dry_run_no_write(tmp_path):
    cal = {
        "post_earnings": [
            {"ticker": "ACME", "date": "2026-06-01", "earnings_date": "2026-06-01"},
        ],
        "pre_earnings": [],
        "updated": "original",
    }
    cal_path = tmp_path / "earnings_calendar.json"
    cal_path.write_text(json.dumps(cal))

    tickers = {
        "ACME": {
            "results_vs_consensus": {"in_quarter_rev_surprise_pct": 4.2},
        },
    }

    import scripts.sync_earnings_intel_from_notes as mod
    orig_path = mod.CALENDAR_PATH
    try:
        mod.CALENDAR_PATH = cal_path
        count = sync_calendar_from_intel(tickers, dry_run=True)
    finally:
        mod.CALENDAR_PATH = orig_path

    assert count == 1
    # File should not have been written
    reloaded = json.loads(cal_path.read_text())
    assert reloaded["updated"] == "original"
    assert "revenue_beat_miss" not in reloaded["post_earnings"][0]
