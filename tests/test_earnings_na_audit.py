"""Tests for the earnings-card n/a audit fixes (branch fix/earnings-cards-na-audit).

Covers the three distinct root causes:

  RC-2  scripts/backfill_results_vs_consensus._parse_metric — now captures a
        reported actual even when the comparator is phrased as "guidance
        mid-point" / "consensus" / standalone, and NEVER fabricates a surprise %
        when no real estimate token is present. Preview notes still yield
        nothing.

  RC-1  scripts/backfill_eps_surprise_from_finnhub — matches the reported
        quarter to the note's earnings_date, writes ONLY the surprise %
        (basis guard: never the absolute EPS), never overwrites a non-null,
        and declines when no quarter is within the match window.

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
import scripts.backfill_eps_surprise_from_finnhub as eps_bf  # noqa: E402

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
# RC-1 — Finnhub EPS surprise quarter matching + basis guard
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


def test_finnhub_matches_reported_quarter_and_writes_only_surprise(tmp_path):
    """RC-1: surprise % is written for the quarter nearest the note date; the
    absolute EPS actual is NEVER written (basis guard)."""
    intel_path = tmp_path / "earnings_intel.json"
    intel_path.write_text(json.dumps(_intel_with("MDB", "2026-05-28")))

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=_FINNHUB_MDB):
        populated, present, nodata = eps_bf.backfill(only="MDB", dry_run=False)

    assert populated == ["MDB"]
    rvc = json.loads(intel_path.read_text())["tickers"]["MDB"]["results_vs_consensus"]
    assert rvc["in_quarter_eps_surprise_pct"] == 10.5  # nearest quarter (2026-06-30)
    assert "in_quarter_eps_actual" not in rvc  # basis guard: no absolute written


def test_finnhub_never_overwrites_note_sourced_value(tmp_path):
    """An existing (note-sourced) surprise must win over Finnhub."""
    intel_path = tmp_path / "earnings_intel.json"
    intel_path.write_text(json.dumps(_intel_with(
        "MDB", "2026-05-28", rvc={"in_quarter_eps_surprise_pct": 4.2})))

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=_FINNHUB_MDB):
        populated, present, nodata = eps_bf.backfill(only="MDB", dry_run=False)

    assert present == ["MDB"] and populated == []
    rvc = json.loads(intel_path.read_text())["tickers"]["MDB"]["results_vs_consensus"]
    assert rvc["in_quarter_eps_surprise_pct"] == 4.2  # untouched


def test_finnhub_declines_when_no_quarter_in_window(tmp_path):
    """When the latest Finnhub quarter is >75 days from the note date, the
    surprise must NOT be attributed (wrong-quarter guard) -> n/a stays."""
    intel_path = tmp_path / "earnings_intel.json"
    # Note dated months after the only available Finnhub quarter.
    intel_path.write_text(json.dumps(_intel_with("OS", "2026-05-07")))
    stale = [{"actual": 1.0, "estimate": 0.3, "period": "2025-12-31",
              "quarter": 4, "year": 2025, "surprisePercent": 231.9, "symbol": "OS"}]

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=stale):
        populated, present, nodata = eps_bf.backfill(only="OS", dry_run=False)

    assert nodata == ["OS"] and populated == []
    rec = json.loads(intel_path.read_text())["tickers"]["OS"]
    assert (rec.get("results_vs_consensus") or {}).get("in_quarter_eps_surprise_pct") is None


def test_finnhub_unavailable_is_safe(tmp_path):
    """No Finnhub data (None / network failure) -> ticker skipped, never guessed."""
    intel_path = tmp_path / "earnings_intel.json"
    intel_path.write_text(json.dumps(_intel_with("CSU.TO", "2026-05-12")))

    with mock.patch.object(eps_bf, "INTEL_PATH", intel_path), \
         mock.patch.object(eps_bf, "get_earnings_surprises", return_value=None):
        populated, present, nodata = eps_bf.backfill(only="CSU.TO", dry_run=False)

    assert nodata == ["CSU.TO"] and populated == []
