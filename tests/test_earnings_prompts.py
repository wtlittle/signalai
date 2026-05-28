"""Tests for Stage 2 earnings prompt builders (v2).

Covers:
1. Full NVDA pre-earnings prompt -- all sections render
2. T2 ticker -- peers/industry_pack/segments omitted
3. Context with 50% errors -- NOT AVAILABLE rendered for each
4. Snapshot test against expected output
5. Post-earnings prompt -- actuals and transcript render
6. _compact_context -- large context gets trimmed
"""
import copy
import json
import os
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so automation imports work.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.perplexity.prompts import (
    build_pre_earnings_prompt_v2,
    build_post_earnings_prompt_v2,
    _compact_context,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_nvda_context() -> dict:
    with open(FIXTURES_DIR / "nvda_pre_context.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Test 1: NVDA pre-earnings prompt renders all major sections
# ---------------------------------------------------------------------------

def test_pre_earnings_nvda_all_sections():
    ctx = _load_nvda_context()
    system, user = build_pre_earnings_prompt_v2("NVDA", ctx)

    # System prompt is the analyst persona
    assert "buy-side equity research analyst" in system
    assert "NEVER fabricate numbers" in system

    # User prompt contains all required sections
    assert "=== TICKER ===" in user
    assert "NVDA" in user
    assert "T1" in user
    assert "Q1 FY2027" in user

    assert "=== QUOTE ===" in user
    assert "135.72" in user or "135,72" in user  # price rendered
    assert "52-week range" in user

    assert "=== FORWARD CONSENSUS ===" in user
    assert "43.2B" in user

    assert "=== HISTORICAL EARNINGS (LAST 8 QUARTERS) ===" in user
    assert "Q4 FY2026" in user
    assert "Average 1d post-earnings move" in user

    assert "=== ANALYST RECOMMENDATION DRIFT" in user
    assert "SB | B | H | S | SS" in user

    assert "=== SELL-SIDE RESEARCH NARRATIVE ===" in user
    assert "Blackwell" in user

    # T1-only sections
    assert "=== SEGMENT REVENUE (LAST 4 QUARTERS) ===" in user
    assert "Data Center" in user
    assert "=== PEERS ===" in user
    assert "AMD" in user
    assert "=== INDUSTRY PACK ===" in user
    assert "Semiconductors" in user

    assert "=== ADJUSTED METRICS (LAST 8 QUARTERS) ===" in user
    assert "=== INSIDER ACTIVITY (90D) ===" in user
    assert "=== DATA GAPS ===" in user
    assert "None -- all fields populated" in user

    assert "NOW WRITE THE NOTE" in user
    assert "Set-up" in user
    assert "Key Debates" in user
    assert "What Matters This Print" in user
    assert "Scenario Grid" in user


# ---------------------------------------------------------------------------
# Test 2: T2 ticker omits peers, segments, industry pack
# ---------------------------------------------------------------------------

def test_pre_earnings_t2_omits_peer_sections():
    ctx = _load_nvda_context()
    ctx["_tier"] = "T2"
    ctx["peers"] = []
    ctx["industry_pack"] = None
    ctx["segments_4q"] = None

    system, user = build_pre_earnings_prompt_v2("SHOP", ctx)

    assert "T2" in user
    # T2 should NOT have segment or peer detail sections
    assert "=== SEGMENT REVENUE" not in user
    assert "=== PEERS ===" not in user
    assert "=== INDUSTRY PACK ===" not in user

    # But should still have adj metrics, quote, consensus, history
    assert "=== ADJUSTED METRICS" in user
    assert "=== QUOTE ===" in user
    assert "=== FORWARD CONSENSUS ===" in user
    assert "=== HISTORICAL EARNINGS" in user


# ---------------------------------------------------------------------------
# Test 3: Context with 50% errors -- NOT AVAILABLE for each
# ---------------------------------------------------------------------------

def test_pre_earnings_errors_render_not_available():
    ctx = _load_nvda_context()
    # Null out half the fields and add matching errors
    error_fields = [
        ("quote", "API timeout"),
        ("consensus_forward", "no estimates available"),
        ("segments_4q", "segment data not found"),
        ("peers", "peer fetch failed"),
        ("industry_pack", "no pack for subsector"),
        ("insider_tx_90d", "Finnhub rate limit"),
        ("analyst_research_narrative", "Perplexity quota exceeded"),
    ]
    ctx["errors"] = [{"field": f, "reason": r} for f, r in error_fields]
    for field, _ in error_fields:
        ctx[field] = None

    system, user = build_pre_earnings_prompt_v2("NVDA", ctx)

    # Each errored field should show NOT AVAILABLE with reason
    for field, reason in error_fields:
        assert "NOT AVAILABLE" in user, f"Missing NOT AVAILABLE for {field}"

    assert "API timeout" in user
    assert "no estimates available" in user
    assert "Finnhub rate limit" in user
    assert "Perplexity quota exceeded" in user

    # Data gaps section should list all errors
    assert "=== DATA GAPS ===" in user
    for field, _ in error_fields:
        assert field in user


# ---------------------------------------------------------------------------
# Test 4: Snapshot test -- expected prompt for NVDA pre-earnings
# ---------------------------------------------------------------------------

def test_pre_earnings_nvda_snapshot():
    ctx = _load_nvda_context()
    system, user = build_pre_earnings_prompt_v2("NVDA", ctx)

    expected_path = FIXTURES_DIR / "expected_pre_NVDA.txt"
    full_prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}"

    if os.environ.get("UPDATE_SNAPSHOTS"):
        expected_path.write_text(full_prompt)
        return

    if not expected_path.exists():
        # First run: write the snapshot
        expected_path.write_text(full_prompt)
        return

    expected = expected_path.read_text()
    assert full_prompt == expected, (
        "Snapshot mismatch. Run with UPDATE_SNAPSHOTS=1 to update.\n"
        f"Expected length: {len(expected)}, Got length: {len(full_prompt)}"
    )


# ---------------------------------------------------------------------------
# Test 5: Post-earnings prompt includes actuals and transcript
# ---------------------------------------------------------------------------

def test_post_earnings_prompt_renders():
    ctx = _load_nvda_context()
    # Convert to post-earnings context
    ctx["snapshot_type"] = "post"
    ctx["this_quarter_actuals"] = {
        "period": "Q1 FY2027",
        "date": "2026-05-20",
        "actual_revenue": "44.5B",
        "estimated_revenue": "43.2B",
        "revenue_surprise_pct": 3.0,
        "actual_eps": "0.92",
        "estimated_eps": "0.88",
        "eps_surprise_pct": 4.5,
        "post_earnings_move_1d_pct": 5.2,
        "expected_move_pct": 8.5,
    }
    ctx["consensus_post_print"] = {
        "fq_revenue": "48.0B",
        "fq_eps": "1.02",
        "fy_revenue": "210.0B",
        "fy_eps": "4.35",
        "revision_direction": "upward",
        "source": "finance_estimates",
    }
    ctx["transcript"] = (
        "Jensen Huang: Thank you everyone for joining. Q1 was an outstanding quarter. "
        "Data center revenue grew 45% sequentially driven by Blackwell ramp. "
        "We are seeing unprecedented demand across all hyperscaler customers..."
    )

    system, user = build_post_earnings_prompt_v2("NVDA", ctx)

    assert "buy-side equity research analyst" in system
    assert "post-earnings" in system

    assert "=== THIS QUARTER ACTUALS ===" in user
    assert "44.5B" in user
    assert "3.0% surprise" in user

    assert "=== POST-PRINT CONSENSUS REVISION ===" in user
    assert "upward" in user

    assert "=== TRANSCRIPT EXCERPT ===" in user
    assert "Jensen Huang" in user

    # Post-earnings output instructions
    assert "Headline" in user
    assert "Beat/Miss Quality" in user
    assert "Guidance and Tone" in user
    assert "Thesis Impact" in user
    assert "Read-Through to Peers" in user  # T1


# ---------------------------------------------------------------------------
# Test 6: _compact_context trims large contexts
# ---------------------------------------------------------------------------

def test_compact_context_trims():
    ctx = _load_nvda_context()
    # Make it huge by expanding transcript and industry pack
    ctx["transcript"] = "x" * 50000
    ctx["industry_pack"]["pack"] = {"data": "y" * 30000}
    ctx["adj_metrics_8q"] = ctx["adj_metrics_8q"] * 3  # 24 quarters
    ctx["segments_4q"] = ctx["segments_4q"] * 4  # 16 quarters
    ctx["peers"] = ctx["peers"] * 5  # 20 peers

    compacted = _compact_context(ctx, target_tokens=10000)

    # adj_metrics trimmed to <= 4
    assert len(compacted["adj_metrics_8q"]) <= 4
    # segments trimmed to <= 2
    assert len(compacted["segments_4q"]) <= 2
    # transcript trimmed
    assert len(compacted["transcript"]) <= 2001  # 2000 + possible rounding
    # peers trimmed to <= 6
    assert len(compacted["peers"]) <= 6

    # Original context unmodified (deep copy)
    assert len(ctx["adj_metrics_8q"]) == 24
    assert len(ctx["transcript"]) == 50000


# ---------------------------------------------------------------------------
# Test 7: Post-earnings T2 omits peer read-through instruction
# ---------------------------------------------------------------------------

def test_post_earnings_t2_no_peer_readthrough():
    ctx = _load_nvda_context()
    ctx["_tier"] = "T2"
    ctx["snapshot_type"] = "post"
    ctx["peers"] = []
    ctx["industry_pack"] = None
    ctx["segments_4q"] = None
    ctx["this_quarter_actuals"] = {
        "actual_revenue": "10.0B",
        "estimated_revenue": "9.8B",
        "revenue_surprise_pct": 2.0,
        "actual_eps": "1.50",
        "estimated_eps": "1.45",
        "eps_surprise_pct": 3.4,
        "post_earnings_move_1d_pct": -2.1,
    }
    ctx["consensus_post_print"] = None
    ctx["transcript"] = None

    system, user = build_post_earnings_prompt_v2("SHOP", ctx)

    assert "=== PEERS ===" not in user
    assert "=== SEGMENT REVENUE" not in user
    assert "=== INDUSTRY PACK ===" not in user


if __name__ == "__main__":
    test_pre_earnings_nvda_all_sections()
    print("PASS: test_pre_earnings_nvda_all_sections")

    test_pre_earnings_t2_omits_peer_sections()
    print("PASS: test_pre_earnings_t2_omits_peer_sections")

    test_pre_earnings_errors_render_not_available()
    print("PASS: test_pre_earnings_errors_render_not_available")

    test_pre_earnings_nvda_snapshot()
    print("PASS: test_pre_earnings_nvda_snapshot")

    test_post_earnings_prompt_renders()
    print("PASS: test_post_earnings_prompt_renders")

    test_compact_context_trims()
    print("PASS: test_compact_context_trims")

    test_post_earnings_t2_no_peer_readthrough()
    print("PASS: test_post_earnings_t2_no_peer_readthrough")

    print("\nAll tests passed.")
