"""Regression tests for the split FY guidance feature (fix/post-earnings-guidance-split).

The single blended "FY Guide Δ" line on POST earnings cards is split into TWO
metric-specific pills: revenue guidance Δ AND profitability guidance Δ (EPS
preferred; operating profit / operating income / FCF as fallbacks).

Two layers are pinned here:

  1. The backend normalizer ``normalize_guidance_envelope`` in
     scripts/sync_earnings_intel_from_notes.py, which derives the flat
     guidance* fields from a raw guide_vs_consensus envelope.

  2. Python mirrors of the JS card helpers in earnings.js
     (classifyGuideDelta / formatGuideDelta / selectProfitabilityGuideMetric /
     selectGuidanceBaseline / buildGuidanceChangeDisplay) so the rendered
     "REV Δ ... / EPS Δ ..." contract is pinned without a JS runtime.

Covers the six spec cases:
  1. prior consensus available for both metrics
  2. consensus missing for profitability only (falls back to prior guide)
  3. revenue flat + EPS unavailable + FCF cut
  4. EPS near-zero baseline (absolute $ change, not a misleading 200%)
  5. missing revenue, available operating profit
  6. no guidance data
"""
import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Load the backend normalizer from the sync script (no package import path).
# ---------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location(
    "sync_earnings_intel_from_notes",
    ROOT / "scripts" / "sync_earnings_intel_from_notes.py",
)
_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sync)
normalize_guidance_envelope = _sync.normalize_guidance_envelope
select_guidance_baseline = _sync.select_guidance_baseline


# ---------------------------------------------------------------------------
# Python mirrors of the earnings.js card helpers
# ---------------------------------------------------------------------------

def classify_guide_delta(delta_pct, flat_threshold=0.005):
    """Mirror of JS classifyGuideDelta. delta_pct is a fraction."""
    if delta_pct is None or not isinstance(delta_pct, (int, float)) or math.isinf(delta_pct) or math.isnan(delta_pct):
        return "n/a"
    if abs(delta_pct) <= flat_threshold:
        return "flat"
    return "raise" if delta_pct > 0 else "cut"


def _fmt_guide_abs(delta_abs):
    sign = "+" if delta_abs > 0 else ("-" if delta_abs < 0 else "")
    return f"{sign}${abs(delta_abs):.2f}"


def format_guide_delta(delta_pct=None, delta_abs=None, metric="REV"):
    """Mirror of JS formatGuideDelta -> the value string ("+2.1%" / "+$0.02")."""
    is_profit = metric and metric != "REV"
    pct_ok = isinstance(delta_pct, (int, float)) and not math.isinf(delta_pct) and not math.isnan(delta_pct)
    if pct_ok:
        pct = delta_pct * 100
        if is_profit and abs(pct) >= 100 and delta_abs is not None and not math.isinf(delta_abs):
            return _fmt_guide_abs(delta_abs)
        sign = "+" if pct > 0 else ("-" if pct < 0 else "")
        return f"{sign}{abs(pct):.1f}%"
    if delta_abs is not None and not math.isinf(delta_abs) and not math.isnan(delta_abs):
        return _fmt_guide_abs(delta_abs)
    return None


def select_profitability_guide_metric(g):
    """Mirror of JS selectProfitabilityGuideMetric. EPS > op profit/inc > FCF."""
    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    if num(g.get("guidanceEpsDeltaPct")) is not None or num(g.get("guidanceEpsDeltaAbs")) is not None:
        return {
            "label": "EPS",
            "deltaPct": num(g.get("guidanceEpsDeltaPct")),
            "deltaAbs": num(g.get("guidanceEpsDeltaAbs")),
            "priorSource": g.get("guidanceEpsPriorSource") or "consensus",
        }
    if num(g.get("guidanceOperatingProfitDeltaPct")) is not None or num(g.get("guidanceOperatingProfitDeltaAbs")) is not None:
        used = "OP INC" if g.get("guidanceProfitMetricUsed") == "operating_income" else "OP PROFIT"
        return {
            "label": used,
            "deltaPct": num(g.get("guidanceOperatingProfitDeltaPct")),
            "deltaAbs": num(g.get("guidanceOperatingProfitDeltaAbs")),
            "priorSource": g.get("guidanceOperatingProfitPriorSource") or "consensus",
        }
    if num(g.get("guidanceFcfDeltaPct")) is not None or num(g.get("guidanceFcfDeltaAbs")) is not None:
        return {
            "label": "FCF",
            "deltaPct": num(g.get("guidanceFcfDeltaPct")),
            "deltaAbs": num(g.get("guidanceFcfDeltaAbs")),
            "priorSource": g.get("guidanceFcfPriorSource") or "consensus",
        }
    return None


def build_guidance_change_display(g):
    """Mirror of JS buildGuidanceChangeDisplay -> { revenue, profitability }."""
    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    revenue = None
    rev_pct = num(g.get("guidanceRevenueDeltaPct"))
    if rev_pct is not None:
        direction = classify_guide_delta(rev_pct)
        value = format_guide_delta(delta_pct=rev_pct, metric="REV")
        if value is not None:
            revenue = {
                "label": "REV",
                "deltaPct": rev_pct,
                "direction": direction,
                "priorSource": g.get("guidanceRevenuePriorSource") or "consensus",
                "display": value,
            }

    profitability = None
    prof = select_profitability_guide_metric(g)
    if prof:
        direction = classify_guide_delta(prof["deltaPct"])
        value = format_guide_delta(delta_pct=prof["deltaPct"], delta_abs=prof["deltaAbs"], metric=prof["label"])
        if value is not None:
            if direction == "n/a" and prof["deltaAbs"] is not None:
                direction = "flat" if abs(prof["deltaAbs"]) <= 0.0001 else ("raise" if prof["deltaAbs"] > 0 else "cut")
            profitability = {
                "label": prof["label"],
                "deltaPct": prof["deltaPct"],
                "deltaAbs": prof["deltaAbs"],
                "direction": direction,
                "priorSource": prof["priorSource"] or "consensus",
                "display": value,
            }

    return {"revenue": revenue, "profitability": profitability}


def _pill_text(metric):
    """Compact text form a pill renders, e.g. "REV Δ -0.7% cut"."""
    if not metric:
        return None
    return f"{metric['label']} \u0394 {metric['display']} {metric['direction']}"


# ---------------------------------------------------------------------------
# Helper-level unit checks
# ---------------------------------------------------------------------------

def test_classify_flat_raise_cut_and_na():
    assert classify_guide_delta(0.0) == "flat"
    assert classify_guide_delta(0.003) == "flat"      # inside 0.5% band
    assert classify_guide_delta(0.021) == "raise"
    assert classify_guide_delta(-0.007) == "cut"
    assert classify_guide_delta(None) == "n/a"
    assert classify_guide_delta(float("inf")) == "n/a"


def test_format_pct_and_abs_fallback():
    assert format_guide_delta(delta_pct=0.021, metric="EPS") == "+2.1%"
    assert format_guide_delta(delta_pct=-0.007, metric="REV") == "-0.7%"
    # Absurd pct on a profit metric with abs available -> use $ change.
    assert format_guide_delta(delta_pct=2.0, delta_abs=0.02, metric="EPS") == "+$0.02"
    # No pct at all -> abs fallback.
    assert format_guide_delta(delta_pct=None, delta_abs=-0.03, metric="EPS") == "-$0.03"
    # Nothing usable.
    assert format_guide_delta(delta_pct=None, delta_abs=None, metric="EPS") is None


def test_profitability_priority_order():
    # EPS wins over op profit and FCF when present.
    g = {"guidanceEpsDeltaPct": 0.02, "guidanceOperatingProfitDeltaPct": 0.05, "guidanceFcfDeltaPct": -0.1}
    assert select_profitability_guide_metric(g)["label"] == "EPS"
    # Op profit wins over FCF when EPS absent.
    g = {"guidanceOperatingProfitDeltaPct": 0.05, "guidanceFcfDeltaPct": -0.1}
    assert select_profitability_guide_metric(g)["label"] == "OP PROFIT"
    # FCF last resort.
    g = {"guidanceFcfDeltaPct": -0.04, "guidanceFcfDeltaAbs": -20}
    assert select_profitability_guide_metric(g)["label"] == "FCF"


def test_select_guidance_baseline_prefers_consensus():
    assert select_guidance_baseline("1236.0M", "1200M") == (1236000000.0, "consensus")
    assert select_guidance_baseline(None, "1200M") == (1200000000.0, "prior_guidance")
    assert select_guidance_baseline(None, None) == (None, None)


# ---------------------------------------------------------------------------
# The six spec regression cases
# ---------------------------------------------------------------------------

def test_case1_prior_consensus_both_metrics():
    """Rev guide vs prior consensus -0.7%, EPS guide vs prior consensus +2.1%."""
    env = {
        "fy_rev_guide_midpoint_new": "1227.5M",
        "fy_rev_consensus_prior": "1236.0M",
        "fy_eps_guide_midpoint_new": "0.98",
        "fy_eps_consensus_prior": "0.96",
    }
    g = normalize_guidance_envelope(env)
    assert g["guidanceRevenuePriorSource"] == "consensus"
    assert g["guidanceEpsPriorSource"] == "consensus"
    assert g["guidanceProfitMetricUsed"] == "eps"

    disp = build_guidance_change_display(g)
    assert _pill_text(disp["revenue"]) == "REV \u0394 -0.7% cut"
    assert _pill_text(disp["profitability"]) == "EPS \u0394 +2.1% raise"


def test_case2_consensus_missing_for_profitability_only():
    """Rev consensus available; EPS consensus missing but prior EPS guide present."""
    env = {
        "fy_rev_guide_midpoint_new": "1227.5M",
        "fy_rev_consensus_prior": "1236.0M",
        "fy_eps_guide_midpoint_new": "0.98",
        "fy_eps_guide_midpoint_prior": "0.95",   # only prior guide, no consensus
    }
    g = normalize_guidance_envelope(env)
    assert g["guidanceRevenuePriorSource"] == "consensus"
    assert g["guidanceEpsPriorSource"] == "prior_guidance"

    disp = build_guidance_change_display(g)
    assert disp["revenue"] is not None
    assert disp["profitability"] is not None
    assert disp["profitability"]["label"] == "EPS"
    assert disp["profitability"]["priorSource"] == "prior_guidance"


def test_case3_revenue_flat_eps_unavailable_fcf_cut():
    """Rev 0.0% flat, EPS null, FCF negative -> REV flat + FCF cut."""
    env = {
        "fy_rev_guide_midpoint_new": "1000M",
        "fy_rev_consensus_prior": "1000M",        # exactly flat
        "fy_fcf_guide_midpoint_new": "180M",
        "fy_fcf_consensus_prior": "200M",         # -10% cut
    }
    g = normalize_guidance_envelope(env)
    assert "guidanceEpsDeltaPct" not in g and "guidanceEpsDeltaAbs" not in g
    assert g["guidanceProfitMetricUsed"] == "fcf"

    disp = build_guidance_change_display(g)
    assert _pill_text(disp["revenue"]) == "REV \u0394 0.0% flat"
    assert disp["profitability"]["label"] == "FCF"
    assert disp["profitability"]["direction"] == "cut"


def test_case4_eps_near_zero_baseline_uses_absolute():
    """Baseline EPS 0.01, current 0.03 -> no misleading +200.0%; show "+$0.02"."""
    env = {
        "fy_eps_guide_midpoint_new": "0.03",
        "fy_eps_consensus_prior": "0.01",         # tiny baseline (<= 0.05)
    }
    g = normalize_guidance_envelope(env)
    # deltaPct suppressed near a tiny baseline; absolute change preserved.
    assert "guidanceEpsDeltaPct" not in g
    assert round(g["guidanceEpsDeltaAbs"], 2) == 0.02

    disp = build_guidance_change_display(g)
    pill = _pill_text(disp["profitability"])
    assert pill == "EPS \u0394 +$0.02 raise"
    assert "%" not in pill            # never a misleading percentage
    assert "200" not in pill


def test_case5_missing_revenue_available_operating_profit():
    """Revenue null, op profit available -> OP PROFIT pill, no empty REV pill."""
    env = {
        "fy_op_profit_guide_midpoint_new": "630M",
        "fy_op_profit_consensus_prior": "600M",   # +5%
        "fy_op_metric_kind": "operating_profit",
    }
    g = normalize_guidance_envelope(env)
    assert "guidanceRevenueDeltaPct" not in g
    assert g["guidanceProfitMetricUsed"] == "operating_profit"

    disp = build_guidance_change_display(g)
    assert disp["revenue"] is None
    assert disp["profitability"]["label"] == "OP PROFIT"
    assert _pill_text(disp["profitability"]) == "OP PROFIT \u0394 +5.0% raise"


def test_case5_operating_income_label_variant():
    env = {
        "fy_op_profit_guide_midpoint_new": "630M",
        "fy_op_profit_consensus_prior": "600M",
        "fy_op_metric_kind": "operating_income",
    }
    g = normalize_guidance_envelope(env)
    assert g["guidanceProfitMetricUsed"] == "operating_income"
    disp = build_guidance_change_display(g)
    assert disp["profitability"]["label"] == "OP INC"


def test_case6_no_guidance_data():
    """Revenue null, all profitability null -> no broken pills, no NaN/undefined."""
    g = normalize_guidance_envelope({})
    assert g == {}
    disp = build_guidance_change_display(g)
    assert disp["revenue"] is None
    assert disp["profitability"] is None
    # And the same holds for a completely empty / None envelope.
    assert normalize_guidance_envelope(None) == {}


def test_precomputed_pct_fallback_when_no_midpoints():
    """When only precomputed *_pct values exist (legacy notes), still split."""
    env = {
        "fy_rev_change_vs_consensus_pct": -0.7,
        "fy_eps_guide_change_vs_consensus_pct": 2.1,
    }
    g = normalize_guidance_envelope(env)
    disp = build_guidance_change_display(g)
    assert _pill_text(disp["revenue"]) == "REV \u0394 -0.7% cut"
    assert _pill_text(disp["profitability"]) == "EPS \u0394 +2.1% raise"


def test_no_nan_or_infinity_in_any_display():
    """Defensive: zero-baseline midpoints must never leak Infinity/NaN."""
    env = {
        "fy_rev_guide_midpoint_new": "100M",
        "fy_rev_consensus_prior": "0",            # zero baseline -> pct suppressed
    }
    g = normalize_guidance_envelope(env)
    # Revenue has no abs fallback field, so with a zero baseline it is omitted
    # rather than rendering Infinity.
    assert "guidanceRevenueDeltaPct" not in g
    disp = build_guidance_change_display(g)
    assert disp["revenue"] is None
