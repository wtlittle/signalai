"""Tests for the earnings-card n/a audit fixes (branch fix/earnings-cards-na-audit).

Covers the three distinct root causes:

  RC-2  scripts/backfill_results_vs_consensus._parse_metric — now captures a
        reported actual even when the comparator is phrased as "guidance
        mid-point" / "consensus" / standalone, and NEVER fabricates a surprise %
        when no real estimate token is present. Preview notes still yield
        nothing.

  RC-1  scripts/backfill_earnings_from_finnhub — matches the reported quarter
        to the note's earnings_date and writes the FULL Finnhub triplet
        (actual + estimate + surprise %) from the SAME row, so the card can
        never show a surprise % without its matching actual (the CRM
        "EPS n/a +23.2% beat" contradiction). It declines when no quarter is
        within the match window and never overwrites a note-sourced actual.

Fixture notes (tests/fixtures/) mirror two real, differently-shaped notes:
  * MDB_2026-05-28.md — a PREVIEW note (actuals "not yet reported")
  * CRM_2026-05-27.md — a real note with label-prefixed prose + "consensus not
    disclosed"
"""
import json
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO_ROOT))

from scripts.backfill_results_vs_consensus import _parse_metric  # noqa: E402
import scripts.backfill_earnings_from_finnhub as eps_bf  # noqa: E402

_QUAL = r"(?:Total\s+|Non-GAAP\s+|GAAP\s+|Adjusted\s+|Adj\.?\s+|Core\s+|Q\d\s+)*"
_REV = _QUAL + r"Rev(?:enue)?"
_EPS = _QUAL + r"EPS"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


# --------------------------------------------------------------------------
# RC-2 — prose actual extraction
# --------------------------------------------------------------------------

def test_crm_label_prefixed_revenue_actual_is_captured():
    """CRM: 'Revenue: $11.13B, +13% y/y ...; consensus not disclosed' must
    surface the actual; surprise must stay None (no consensus stated -> never
    fabricated)."""
    md = _read("CRM_2026-05-27.md")
    actual_raw, actual_num, surprise = _parse_metric(md, _REV)
    assert actual_raw == "$11.13B"
    assert actual_num == 11.13e9
    assert surprise is None  # "consensus not disclosed" -> no fabricated surprise


def test_crm_eps_absent_from_prose_yields_nothing():
    """CRM note states no EPS line -> parser returns nothing (EPS surprise is
    sourced from Finnhub instead, see RC-1)."""
    md = _read("CRM_2026-05-27.md")
    assert _parse_metric(md, _EPS) == (None, None, None)


def test_mdb_preview_note_yields_no_actuals():
    """MDB preview note ('not yet reported') must yield nothing for both REV
    and EPS — n/a is correct, never guessed."""
    md = _read("MDB_2026-05-28.md")
    assert _parse_metric(md, _REV) == (None, None, None)
    assert _parse_metric(md, _EPS) == (None, None, None)


def test_guidance_midpoint_phrasing_captures_actual_without_surprise():
    """MRVL-style phrasing 'Revenue: $2.42B vs ~$2.40B guidance mid-point' must
    capture the reported actual. The comparator is a *guidance* mid-point, not a
    consensus, so the consensus-surprise % stays None — we never present a
    guidance-relative delta as a consensus beat/miss."""
    md = "## Key Metrics\n- Revenue: $2.42B vs ~$2.40B guidance mid-point (28% y/y).\n"
    actual_raw, actual_num, surprise = _parse_metric(md, _REV)
    assert actual_raw == "$2.42B"
    assert actual_num == 2.42e9
    assert surprise is None  # vs guidance mid-point != vs consensus -> no surprise


def test_consensus_est_phrasing_in_line_surprise():
    """'$0.80 vs $0.80 consensus est' -> 0.0% (in-line), exact value parsed."""
    md = "## Key Metrics\n- Non-GAAP EPS: $0.80 vs $0.80 consensus est.\n"
    actual_raw, actual_num, surprise = _parse_metric(md, _EPS)
    assert actual_num == 0.80
    assert surprise == 0.0


def test_na_token_in_actual_is_not_captured():
    """A line whose 'actual' is N/A/pending must never be captured."""
    md = "## Key Metrics\n- Q1 FY27 EPS: N/A vs consensus ~$2.84 (actuals pending).\n"
    assert _parse_metric(md, _EPS) == (None, None, None)


# --------------------------------------------------------------------------
# RC-1 — Finnhub FULL-triplet quarter matching (NO contradictory pair)
# --------------------------------------------------------------------------

_FINNHUB_MDB = [
    {"actual": 1.32, "estimate": 1.1945, "period": "2026-06-30",
     "quarter": 1, "year": 2027, "surprisePercent": 10.5065, "symbol": "MDB"},
    {"actual": 1.65, "estimate": 1.4645, "period": "2026-03-31",
     "quarter": 4, "year": 2026, "surprisePercent": 12.6664, "symbol": "MDB"},
]


def _intel_with(ticker, earnings_date, rvc=None):
    return {
        "tickers": {
            ticker: {
                "ticker": ticker,
                "state": "post_earnings",
                "last_earnings_date": earnings_date,
                "post_earnings_review": {"active": True, "earnings_date": earnings_date},
                **({"results_vs_consensus": rvc} if rvc is not None else {}),
            }
        }
    }


def test_finnhub_writes_full_triplet_for_reported_quarter(tmp_path):
    """RC-1 (fixed): the FULL triplet (actual + estimate + surprise) is written
    from the row nearest the note date, so the card can never show a surprise %
    without its actual. The old 'surprise only / basis guard' behavior was the
    CRM 'EPS n/a +23.2% beat' bug and is explicitly undone here."""
    intel_path = tmp_path / "earnings_intel.json"
    intel_path.write_text(json.dumps(_intel_with("MDB", "2026-05-28")))

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=_FINNHUB_MDB):
        eps_written, rev_written, present, nodata = eps_bf.backfill(only="MDB", dry_run=False)

    assert eps_written == ["MDB"]
    rvc = json.loads(intel_path.read_text())["tickers"]["MDB"]["results_vs_consensus"]
    assert rvc["in_quarter_eps_surprise_pct"] == 10.5      # nearest quarter (2026-06-30)
    assert rvc["in_quarter_eps_actual"] == "$1.32"          # absolute IS written now
    assert rvc["in_quarter_eps_estimate"] == "$1.19"        # estimate from same row
    # Invariant: surprise present implies actual present (no contradiction).
    assert rvc["in_quarter_eps_actual"] is not None


def test_finnhub_never_overwrites_note_sourced_actual(tmp_path):
    """A note-sourced EPS actual (company headline non-GAAP basis) must win;
    Finnhub does not clobber it."""
    intel_path = tmp_path / "earnings_intel.json"
    intel_path.write_text(json.dumps(_intel_with(
        "MDB", "2026-05-28", rvc={"in_quarter_eps_actual": "$1.00"})))

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=_FINNHUB_MDB):
        eps_written, rev_written, present, nodata = eps_bf.backfill(only="MDB", dry_run=False)

    rvc = json.loads(intel_path.read_text())["tickers"]["MDB"]["results_vs_consensus"]
    assert rvc["in_quarter_eps_actual"] == "$1.00"  # untouched
    assert "MDB" not in eps_written


def test_finnhub_declines_when_no_quarter_in_window(tmp_path):
    """When the latest Finnhub quarter is >75 days from the note date, nothing
    is attributed (wrong-quarter guard) -> n/a stays, no contradictory pair."""
    intel_path = tmp_path / "earnings_intel.json"
    intel_path.write_text(json.dumps(_intel_with("OS", "2026-05-07")))
    stale = [{"actual": 1.0, "estimate": 0.3, "period": "2025-12-31",
              "quarter": 4, "year": 2025, "surprisePercent": 231.9, "symbol": "OS"}]

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=stale):
        eps_written, rev_written, present, nodata = eps_bf.backfill(only="OS", dry_run=False)

    assert "OS" in nodata and eps_written == []
    rec = json.loads(intel_path.read_text())["tickers"]["OS"]
    assert (rec.get("results_vs_consensus") or {}).get("in_quarter_eps_surprise_pct") is None
    assert (rec.get("results_vs_consensus") or {}).get("in_quarter_eps_actual") is None


def test_finnhub_unavailable_is_safe(tmp_path):
    """No Finnhub data (None / network failure) -> ticker skipped, never guessed."""
    intel_path = tmp_path / "earnings_intel.json"
    intel_path.write_text(json.dumps(_intel_with("CSU.TO", "2026-05-12")))

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=None):
        eps_written, rev_written, present, nodata = eps_bf.backfill(only="CSU.TO", dry_run=False)

    assert "CSU.TO" in nodata and eps_written == []
