"""Guardrail tests for watchlist sector/subsector categorization.

SUBSECTOR_MAP in utils.js is the canonical categorization store; getSubsector()
falls back to "Other" for anything missing, which the user sees as an
uncategorized company. These tests fail the build if a watchlist ticker slips
in without a subsector (the FROG regression), so it cannot happen silently.
"""
from automation.shared.tickers import load_tickers, load_subsector_map
from automation.jobs.audit_categories import find_uncategorized


def test_no_watchlist_ticker_is_uncategorized():
    """Every DEFAULT_TICKERS entry must have a SUBSECTOR_MAP subsector."""
    uncategorized = find_uncategorized()
    assert uncategorized == [], (
        f"Watchlist tickers missing a subsector: {uncategorized}. "
        "Add each to SUBSECTOR_MAP in utils.js."
    )


def test_every_subsector_is_non_empty():
    """No ticker resolves to an empty/whitespace subsector."""
    submap = load_subsector_map()
    for ticker in load_tickers():
        sub = submap.get(ticker)
        assert sub and sub.strip(), f"{ticker} has empty subsector: {sub!r}"


def test_frog_resolves_to_devops():
    """FROG (JFrog) — the flagged regression — resolves to its DevOps
    subsector, matching its GTLB/PATH peers in the existing taxonomy."""
    submap = load_subsector_map()
    assert "FROG" in submap, "FROG missing from SUBSECTOR_MAP"
    assert submap["FROG"] == "DevOps & Automation", (
        f"FROG should be 'DevOps & Automation', got {submap['FROG']!r}"
    )
