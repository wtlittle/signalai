"""Tests for the two-pass reaction cadence:

  * transcript_harvest source-chain ordering (finance connector PRIMARY ->
    Motley Fool -> sonar-native), and that --mode print_reaction skips the
    transcript step entirely.
  * print_reaction_sweep.compose_note() producing a 2-3 sentence note from
    synthetic actuals, and never fabricating from missing values.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.jobs import transcript_harvest as th
from automation.jobs import print_reaction_sweep as prs


# ---------------------------------------------------------------------------
# Transcript source-chain ordering
# ---------------------------------------------------------------------------
LONG = "x" * 5000  # comfortably above MIN_TRANSCRIPT_CHARS


def test_chain_order_constant():
    """The canonical order is finance connector first, sonar-native last."""
    assert th.TRANSCRIPT_SOURCE_CHAIN == (
        "finance_earnings_transcript", "motley_fool", "perplexity_native",
    )


def test_finance_connector_is_primary():
    """When the finance connector returns a fresh, long transcript it wins and
    the Motley Fool scrape is never attempted."""
    with patch.object(th, "_fetch_finance_transcript",
                      return_value=(LONG, "2026-06-03")) as fin, \
         patch.object(th, "_scrape_motley_fool") as fool:
        text, source, url = th._acquire_transcript("AVGO", "Broadcom", "2026-06-03")
    assert source == "finance_earnings_transcript"
    assert text == LONG
    fin.assert_called_once()
    fool.assert_not_called()


def test_falls_through_to_fool_when_finance_short():
    """A finance transcript below MIN_TRANSCRIPT_CHARS is rejected; Fool fires."""
    with patch.object(th, "_fetch_finance_transcript",
                      return_value=("tiny", "2026-06-03")), \
         patch.object(th, "_scrape_motley_fool",
                      return_value=(LONG, "http://fool/x")) as fool:
        text, source, url = th._acquire_transcript("AVGO", "Broadcom", "2026-06-03")
    assert source == "motley_fool"
    assert url == "http://fool/x"
    fool.assert_called_once()


def test_falls_through_to_fool_when_finance_stale():
    """A finance transcript for the wrong earnings_date is treated as stale."""
    with patch.object(th, "_fetch_finance_transcript",
                      return_value=(LONG, "2026-03-01")), \
         patch.object(th, "_scrape_motley_fool",
                      return_value=(LONG, "http://fool/x")):
        text, source, url = th._acquire_transcript("AVGO", "Broadcom", "2026-06-03")
    assert source == "motley_fool"


def test_sonar_native_is_last_resort():
    """When both finance and Fool fail, the chain signals perplexity_native."""
    with patch.object(th, "_fetch_finance_transcript", return_value=(None, None)), \
         patch.object(th, "_scrape_motley_fool", return_value=(None, None)):
        text, source, url = th._acquire_transcript("AVGO", "Broadcom", "2026-06-03")
    assert source == "perplexity_native"
    assert text is None


def test_print_reaction_mode_skips_transcript():
    """--mode print_reaction must not touch any transcript source."""
    with patch.object(th, "_acquire_transcript") as acq:
        result = th.harvest_ticker("AVGO", "Broadcom", "2026-06-03",
                                   mode="print_reaction")
    assert result["status"] == "print_skipped"
    acq.assert_not_called()


# ---------------------------------------------------------------------------
# print_reaction_sweep.compose_note
# ---------------------------------------------------------------------------
def test_compose_note_beat_and_guide_and_move():
    actuals = {
        "in_quarter_eps_surprise_pct": 4.2,
        "in_quarter_rev_surprise_pct": 2.1,
    }
    note = prs.compose_note("AVGO", actuals,
                            fy_guide_direction="raised",
                            after_hours_move_pct=6.5)
    # 2-3 sentences (count sentence terminators that end a sentence, i.e. a
    # period followed by a space or end-of-string, so "4.2%" is not miscounted).
    import re
    n_sentences = len(re.findall(r"\.(?:\s|$)", note))
    assert 2 <= n_sentences <= 3, note
    assert "AVGO" in note
    assert "EPS beat by 4.2%" in note
    assert "revenue beat by 2.1%" in note
    assert "guidance was raised" in note.lower()
    assert "up 6.5% after hours" in note.lower()


def test_compose_note_miss():
    note = prs.compose_note("CRWD", {"in_quarter_eps_surprise_pct": -3.0})
    assert "missed by 3.0%" in note
    assert note.startswith("CRWD reported:")


def test_compose_note_never_fabricates_missing_actuals():
    """No surprise data -> no beat/miss claim, no invented numbers."""
    note = prs.compose_note("XYZ", {})
    assert "beat" not in note.lower()
    assert "missed" not in note.lower()
    assert "%" not in note  # no fabricated percentage
    assert "n/a" in note.lower()


def test_compose_note_eps_only_when_no_surprise():
    """EPS actual present but no consensus surprise -> report it, flag n/a."""
    note = prs.compose_note("XYZ", {"in_quarter_eps_actual": "$1.10"})
    assert "$1.10" in note
    assert "n/a" in note.lower()


def test_compose_note_in_line():
    note = prs.compose_note("ABC", {"in_quarter_eps_surprise_pct": 0.2})
    assert "in-line" in note.lower()


# ---------------------------------------------------------------------------
# --backfill-days selection (Bug 3)
# ---------------------------------------------------------------------------
def _intel_fixture():
    """A spread of intel rows: a recent reporter with actuals + null status,
    one already escalated, one without actuals, and one outside the window."""
    return {
        "tickers": {
            # recent + actuals + null note_status -> selected
            "MDT": {
                "company_name": "Medtronic",
                "last_earnings_date": "2026-06-03",
                "note_status": None,
                "results_vs_consensus": {"in_quarter_rev_actual": 9017000000},
            },
            # recent but already has a note_status -> skipped
            "AVGO": {
                "company_name": "Broadcom",
                "last_earnings_date": "2026-06-03",
                "note_status": "print_reaction",
                "results_vs_consensus": {"in_quarter_rev_actual": 19311000000},
            },
            # recent + null status but NO actuals -> skipped (never fabricate)
            "NOACT": {
                "company_name": "No Actuals Inc",
                "last_earnings_date": "2026-06-02",
                "note_status": None,
                "results_vs_consensus": {},
            },
            # outside the 14-day window -> skipped
            "OLD": {
                "company_name": "Old Reporter",
                "last_earnings_date": "2026-04-01",
                "note_status": None,
                "results_vs_consensus": {"in_quarter_eps_actual": "$1.00"},
            },
        }
    }


def test_select_backfill_filters_by_window_status_and_actuals():
    with patch.object(prs, "read_json", return_value=_intel_fixture()), \
         patch.object(prs, "_et_today", return_value="2026-06-04"):
        entries = prs._select_backfill(14)
    tickers = {e["ticker"] for e in entries}
    assert tickers == {"MDT"}, tickers
    sel = entries[0]
    assert sel["earnings_date"] == "2026-06-03"
    assert sel["company"] == "Medtronic"


def test_has_actuals_detects_rev_or_eps():
    assert prs._has_actuals({"results_vs_consensus": {"in_quarter_rev_actual": 1}})
    assert prs._has_actuals({"results_vs_consensus": {"in_quarter_eps_actual": "$1"}})
    assert not prs._has_actuals({"results_vs_consensus": {}})
    assert not prs._has_actuals({})
