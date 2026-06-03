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
compute_metric_delta = _sync._compute_metric_delta


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
            "streetDeltaPct": num(g.get("guidanceEpsStreetDeltaPct")),
            "streetDeltaAbs": num(g.get("guidanceEpsStreetDeltaAbs")),
        }
    if num(g.get("guidanceOperatingProfitDeltaPct")) is not None or num(g.get("guidanceOperatingProfitDeltaAbs")) is not None:
        used = "Op Inc" if g.get("guidanceProfitMetricUsed") == "operating_income" else "Op Profit"
        return {
            "label": used,
            "deltaPct": num(g.get("guidanceOperatingProfitDeltaPct")),
            "deltaAbs": num(g.get("guidanceOperatingProfitDeltaAbs")),
            "priorSource": g.get("guidanceOperatingProfitPriorSource") or "consensus",
            "streetDeltaPct": num(g.get("guidanceOperatingProfitStreetDeltaPct")),
            "streetDeltaAbs": num(g.get("guidanceOperatingProfitStreetDeltaAbs")),
        }
    if num(g.get("guidanceFcfDeltaPct")) is not None or num(g.get("guidanceFcfDeltaAbs")) is not None:
        return {
            "label": "FCF",
            "deltaPct": num(g.get("guidanceFcfDeltaPct")),
            "deltaAbs": num(g.get("guidanceFcfDeltaAbs")),
            "priorSource": g.get("guidanceFcfPriorSource") or "consensus",
            "streetDeltaPct": num(g.get("guidanceFcfStreetDeltaPct")),
            "streetDeltaAbs": num(g.get("guidanceFcfStreetDeltaAbs")),
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
                "streetDeltaPct": num(g.get("guidanceRevenueStreetDeltaPct")),
                "streetDeltaAbs": None,
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
                "streetDeltaPct": prof["streetDeltaPct"],
                "streetDeltaAbs": prof["streetDeltaAbs"],
                "display": value,
            }

    return {"revenue": revenue, "profitability": profitability}


def _raise_cut_word(direction):
    """Mirror of JS _raiseCutWord."""
    if direction == "raise":
        return "Raise"
    if direction == "cut":
        return "Cut"
    return "Flat"


def _above_below_word(delta_pct, flat_threshold=0.005):
    """Mirror of JS _aboveBelowWord."""
    if delta_pct is None or not isinstance(delta_pct, (int, float)) or math.isinf(delta_pct) or math.isnan(delta_pct):
        return "n/a"
    if abs(delta_pct) <= flat_threshold:
        return "In-line"
    return "Above" if delta_pct > 0 else "Below"


def _profit_metric_prefix(label):
    """Mirror of JS _profitMetricPrefix."""
    if not label or label == "REV":
        return ""
    return label + " "


def build_guidance_pill_text(metric, metric_prefix):
    """Mirror of JS buildGuidancePillText -> { text, direction } or None."""
    if not metric:
        return None
    prefix = metric_prefix or ""
    if metric.get("priorSource") == "prior_guidance":
        primary = metric.get("display")
        if primary is None:
            return None
        word = _raise_cut_word(metric.get("direction"))
        text = f"{word} {prefix}{primary}".rstrip()
        street = format_guide_delta(
            delta_pct=metric.get("streetDeltaPct"),
            delta_abs=metric.get("streetDeltaAbs"),
            metric=metric.get("label"),
        )
        if street is not None:
            text += f", {street} vs Street"
        return {"text": text, "direction": metric.get("direction")}
    # Consensus baseline: above / in-line / below framing, no raise/cut.
    word = _above_below_word(metric.get("deltaPct"))
    abs_delta = metric.get("deltaAbs")
    if word == "n/a" and isinstance(abs_delta, (int, float)) and not (math.isinf(abs_delta) or math.isnan(abs_delta)):
        word = "In-line" if abs(abs_delta) <= 0.0001 else ("Above" if abs_delta > 0 else "Below")
    if word == "n/a":
        return None
    label_bit = (prefix.rstrip() + " ") if prefix else ""
    if word == "In-line":
        return {"text": f"{label_bit}In-line Street".strip(), "direction": "flat"}
    mag = metric.get("display")
    if mag is None:
        return None
    direction = "raise" if word == "Above" else "cut"
    return {"text": f"{label_bit}{word} Street {mag}".strip(), "direction": direction}


def _revenue_pill_text(disp):
    """Pill text for the revenue side of a build_guidance_change_display result."""
    pill = build_guidance_pill_text(disp.get("revenue"), "")
    return pill["text"] if pill else None


def _profit_pill_text(disp):
    """Pill text for the profitability side, with the metric-label prefix."""
    prof = disp.get("profitability")
    if not prof:
        return None
    pill = build_guidance_pill_text(prof, _profit_metric_prefix(prof.get("label")))
    return pill["text"] if pill else None


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
    assert select_profitability_guide_metric(g)["label"] == "Op Profit"
    # FCF last resort.
    g = {"guidanceFcfDeltaPct": -0.04, "guidanceFcfDeltaAbs": -20}
    assert select_profitability_guide_metric(g)["label"] == "FCF"


def test_select_guidance_baseline_prefers_prior_guide():
    # Industry framing: raise/cut is measured vs the company's OWN prior guide,
    # so when both baselines exist the prior guide wins. The consensus baseline
    # is the fallback (initial-quarter guide with no prior guide).
    assert select_guidance_baseline("1236.0M", "1200M") == (1200000000.0, "prior_guidance")
    assert select_guidance_baseline("1236.0M", None) == (1236000000.0, "consensus")
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

    # Both metrics are vs prior CONSENSUS (no prior company guide) -> the card
    # renders ABOVE/IN-LINE/BELOW Street framing, never raise/cut.
    disp = build_guidance_change_display(g)
    assert _revenue_pill_text(disp) == "Below Street -0.7%"
    assert _profit_pill_text(disp) == "EPS Above Street +2.1%"


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
    # Revenue (vs consensus) -> Below Street; EPS (vs prior guide, no prior
    # consensus) -> Raise EPS, with no ", vs Street" suffix.
    assert _revenue_pill_text(disp) == "Below Street -0.7%"
    assert _profit_pill_text(disp) == "Raise EPS +3.2%"


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
    assert _revenue_pill_text(disp) == "In-line Street"
    assert disp["profitability"]["label"] == "FCF"
    assert disp["profitability"]["direction"] == "cut"
    assert _profit_pill_text(disp) == "FCF Below Street -10.0%"


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
    # Consensus baseline + suppressed pct -> the absolute $ delta still carries
    # the direction (Above Street), and the magnitude is the signed $ change.
    pill = _profit_pill_text(disp)
    assert pill == "EPS Above Street +$0.02"
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
    assert disp["profitability"]["label"] == "Op Profit"
    assert _profit_pill_text(disp) == "Op Profit Above Street +5.0%"


def test_case5_operating_income_label_variant():
    env = {
        "fy_op_profit_guide_midpoint_new": "630M",
        "fy_op_profit_consensus_prior": "600M",
        "fy_op_metric_kind": "operating_income",
    }
    g = normalize_guidance_envelope(env)
    assert g["guidanceProfitMetricUsed"] == "operating_income"
    disp = build_guidance_change_display(g)
    assert disp["profitability"]["label"] == "Op Inc"
    assert _profit_pill_text(disp) == "Op Inc Above Street +5.0%"


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
    assert _revenue_pill_text(disp) == "Below Street -0.7%"
    assert _profit_pill_text(disp) == "EPS Above Street +2.1%"


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


# ---------------------------------------------------------------------------
# New behavior: prior-guide-first baseline + vs-Street secondary delta
# ---------------------------------------------------------------------------

def test_compute_metric_delta_returns_both_deltas_when_both_baselines_exist():
    """When BOTH the prior guide and prior consensus exist, the primary delta is
    vs the prior guide (raise/cut) and streetDeltaPct carries the vs-Street move.
    """
    # new 1240, prior guide 1230 (+0.81% raise), prior consensus 1236 (+0.32% vs Street).
    d = compute_metric_delta("1240M", "1236M", "1230M")
    assert d["priorSource"] == "prior_guidance"
    assert round(d["deltaPct"], 4) == round((1240 - 1230) / 1230, 4)
    assert d["streetDeltaPct"] is not None
    assert round(d["streetDeltaPct"], 4) == round((1240 - 1236) / 1236, 4)
    assert round(d["streetDeltaAbs"] / 1e6, 2) == 4.0


def test_compute_metric_delta_no_street_when_only_prior_guide():
    """Prior guide present, no consensus -> primary is raise/cut, no Street delta."""
    d = compute_metric_delta("1240M", None, "1230M")
    assert d["priorSource"] == "prior_guidance"
    assert d["streetDeltaPct"] is None
    assert d["streetDeltaAbs"] is None


def test_compute_metric_delta_consensus_only_has_no_street_delta():
    """Only consensus -> primary is vs-Street, streetDelta stays None (it IS the
    Street number; there is no separate prior-guide baseline to compare)."""
    d = compute_metric_delta("1240M", "1236M", None)
    assert d["priorSource"] == "consensus"
    assert d["streetDeltaPct"] is None


# ---------------------------------------------------------------------------
# Render snapshots for the three framing states
# ---------------------------------------------------------------------------

def test_render_state_both_baselines_raise_plus_vs_street():
    """prior_guidance primary + vs-Street secondary -> 'Raise +X%, +Y% vs Street'."""
    env = {
        "fy_rev_guide_midpoint_new": "1240M",
        "fy_rev_guide_midpoint_prior": "1230M",   # +0.81% raise vs own guide
        "fy_rev_consensus_prior": "1236M",        # +0.32% vs Street
    }
    g = normalize_guidance_envelope(env)
    assert g["guidanceRevenuePriorSource"] == "prior_guidance"
    assert g["guidanceRevenueStreetDeltaPct"] is not None
    disp = build_guidance_change_display(g)
    assert _revenue_pill_text(disp) == "Raise +0.8%, +0.3% vs Street"


def test_render_state_prior_guide_only_raise_no_street():
    """prior_guidance primary, no consensus -> 'Raise +X%' (no vs-Street suffix)."""
    env = {
        "fy_eps_guide_midpoint_new": "1.10",
        "fy_eps_guide_midpoint_prior": "1.00",    # +10% raise vs own guide
    }
    g = normalize_guidance_envelope(env)
    assert g["guidanceEpsPriorSource"] == "prior_guidance"
    assert g.get("guidanceEpsStreetDeltaPct") is None
    disp = build_guidance_change_display(g)
    assert _profit_pill_text(disp) == "Raise EPS +10.0%"


def test_render_state_consensus_only_above_below_street():
    """consensus primary -> 'Above/Below Street +Y%', never raise/cut wording."""
    env = {
        "fy_rev_guide_midpoint_new": "1227.5M",
        "fy_rev_consensus_prior": "1236.0M",      # -0.7% vs Street
    }
    g = normalize_guidance_envelope(env)
    assert g["guidanceRevenuePriorSource"] == "consensus"
    disp = build_guidance_change_display(g)
    text = _revenue_pill_text(disp)
    assert text == "Below Street -0.7%"
    assert "Raise" not in text and "Cut" not in text and "Flat" not in text
