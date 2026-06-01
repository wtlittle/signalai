"""Regression tests for the post-print reaction fix + earnings card hardening
(fix/earnings-card-hardening).

Pins the hardened render contract from earnings.js without a JS runtime:

  - safeMoney / safePct / safeReaction / classifyBeatMiss safe formatters
  - Bug 1: the post-print reaction is sourced FRESH from the data-layer field
    ``stock_reaction_pct``. When present it renders a signed, color-coded %;
    when genuinely missing it renders an explicit ``n/a`` (never a bare ``\u2014``,
    never a fabricated/remembered value such as the prose "-8.2%" for S).
  - Bug 2: monetary headlines render exactly one ``$`` (never ``$$``).
  - Full-set audit: every active post-earnings card in earnings_intel.json is
    run through the mirrored renderer and asserted to emit no forbidden token
    (NaN / undefined / null / Infinity / $$ / bare em-dash), and every value
    traces to a data-layer field (no hardcoded numeric constant).

The Python mirrors below are kept byte-for-byte faithful to the JS helpers so
the contract is enforceable in CI.
"""
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_DISPLAY_TOKENS = ["NaN", "undefined", "null", "Infinity", "$$"]


# ---------------------------------------------------------------------------
# Python mirrors of the earnings.js safe formatters
# ---------------------------------------------------------------------------

def _to_finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    s = re.sub(r"[$,\s]", "", value.strip())
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([kKmMbBtT])?$", s)
    if not m:
        return None
    n = float(m.group(1))
    return n if math.isfinite(n) else None


def safe_money(value, unit=None):
    if value is None:
        return "n/a"
    if isinstance(value, str):
        s = value.strip()
        if s in ("", "\u2014", "--"):
            return "n/a"
        s = re.sub(r"^\$+", "", s)
        return "$" + s if s else "n/a"
    if isinstance(value, bool):
        return "n/a"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return "n/a"
        return "$" + str(value) + (unit or "")
    return "n/a"


def safe_pct(value, decimals=1, signed=True):
    n = value if isinstance(value, (int, float)) and not isinstance(value, bool) else _to_finite_number(value)
    if n is None or not math.isfinite(n):
        return "n/a"
    rounded = round(n, decimals)
    sign = "+" if (signed and rounded > 0) else ("-" if rounded < 0 else "")
    return f"{sign}{abs(rounded):.{decimals}f}%"


def safe_reaction(post_print_pct, flat_threshold=0.05):
    n = post_print_pct if isinstance(post_print_pct, (int, float)) and not isinstance(post_print_pct, bool) else _to_finite_number(post_print_pct)
    if n is None or not math.isfinite(n):
        return {"display": "n/a", "direction": "neutral", "available": False}
    direction = "neutral" if abs(n) <= flat_threshold else ("positive" if n > 0 else "negative")
    return {"display": safe_pct(n), "direction": direction, "available": True}


def classify_beat_miss(delta_pct, flat_threshold=1.0):
    n = delta_pct if isinstance(delta_pct, (int, float)) and not isinstance(delta_pct, bool) else _to_finite_number(delta_pct)
    if n is None or not math.isfinite(n):
        return "n/a"
    if n > flat_threshold:
        return "beat"
    if n < -flat_threshold:
        return "miss"
    return "in-line"


def render_earnings_card(card):
    """Mirror of JS renderEarningsCard: compose tokens from data-layer fields."""
    c = card or {}
    return {
        "revenue": safe_money(c.get("revenue_actual")),
        "eps": safe_money(c.get("eps_actual")),
        "reaction": safe_reaction(c.get("stock_reaction_pct")),
    }


# ---------------------------------------------------------------------------
# A) safeMoney — exactly one '$'; 'n/a' for missing
# ---------------------------------------------------------------------------

def test_money_single_dollar_preserved():
    assert safe_money("$276.7M") == "$276.7M"


def test_money_adds_dollar_to_bare():
    assert safe_money("276.7M") == "$276.7M"


def test_money_collapses_double_dollar():
    # Bug 2: an already-prefixed value must never become "$$".
    assert safe_money("$$276.7M") == "$276.7M"
    assert "$$" not in safe_money("$$0.04")


def test_money_missing_is_na():
    assert safe_money(None) == "n/a"
    assert safe_money("") == "n/a"
    assert safe_money("\u2014") == "n/a"


def test_money_numeric_with_unit():
    assert safe_money(276.7, unit="M") == "$276.7M"


def test_money_non_finite_is_na():
    assert safe_money(float("inf")) == "n/a"
    assert safe_money(float("nan")) == "n/a"


# ---------------------------------------------------------------------------
# B) safePct — signed, null-safe, never NaN/Infinity
# ---------------------------------------------------------------------------

def test_pct_signed_positive():
    assert safe_pct(5.8) == "+5.8%"


def test_pct_signed_negative_single_sign():
    out = safe_pct(-8.2)
    assert out == "-8.2%"
    assert "--" not in out and "+-" not in out


def test_pct_rounds_to_one_decimal():
    assert safe_pct(5.65) == "+5.7%"


def test_pct_missing_and_non_finite():
    assert safe_pct(None) == "n/a"
    assert safe_pct(float("inf")) == "n/a"
    assert safe_pct(float("nan")) == "n/a"


# ---------------------------------------------------------------------------
# C) safeReaction — fresh from data layer; n/a when missing; never fabricated
# ---------------------------------------------------------------------------

def test_reaction_positive_color():
    r = safe_reaction(5.8)
    assert r == {"display": "+5.8%", "direction": "positive", "available": True}


def test_reaction_negative_color():
    r = safe_reaction(-3.7)
    assert r["direction"] == "negative" and r["display"] == "-3.7%"


def test_reaction_flat_neutral():
    r = safe_reaction(0.0)
    assert r["direction"] == "neutral" and r["available"] is True


def test_reaction_missing_is_na_not_dash():
    r = safe_reaction(None)
    assert r == {"display": "n/a", "direction": "neutral", "available": False}
    assert "\u2014" not in r["display"]


# ---------------------------------------------------------------------------
# D) classifyBeatMiss
# ---------------------------------------------------------------------------

def test_classify_beat_miss_inline():
    assert classify_beat_miss(-0.2) == "in-line"
    assert classify_beat_miss(100.0) == "beat"
    assert classify_beat_miss(-3.4) == "miss"
    assert classify_beat_miss(None) == "n/a"


# ---------------------------------------------------------------------------
# Spec regression cases
# ---------------------------------------------------------------------------

def _load_intel():
    return json.loads((ROOT / "earnings_intel.json").read_text())


def _row_from_intel(tk, intel):
    v = intel["tickers"][tk]
    rvc = v.get("results_vs_consensus") or {}
    per = v.get("post_earnings_review") or {}
    return {
        "ticker": tk,
        "revenue_actual": rvc.get("in_quarter_rev_actual") or "",
        "eps_actual": rvc.get("in_quarter_eps_actual") or "",
        "stock_reaction_pct": per.get("stock_reaction_pct"),
    }


def test_case1_sentinelone_post_print_sourced_from_data_layer():
    """S: post-print read FRESH from data layer. The authoritative field is
    null at source, so the contract is an explicit 'n/a' — NOT the remembered
    prose value of -8.2%, and NOT a bare dash."""
    intel = _load_intel()
    row = _row_from_intel("S", intel)
    # Confirm the data layer genuinely lacks a sourced reaction for S.
    assert row["stock_reaction_pct"] is None
    tokens = render_earnings_card(row)
    assert tokens["revenue"] == "$276.7M"
    assert tokens["eps"] == "$0.04"
    assert tokens["reaction"]["display"] == "n/a"
    assert tokens["reaction"]["available"] is False
    # Never fabricate the remembered -8.2%.
    assert "8.2" not in tokens["reaction"]["display"]


def test_case2_post_print_genuinely_missing_renders_na():
    row = {"ticker": "ZZZ", "revenue_actual": "$10M", "eps_actual": "$1.00", "stock_reaction_pct": None}
    tokens = render_earnings_card(row)
    assert tokens["reaction"]["display"] == "n/a"
    assert "\u2014" not in tokens["reaction"]["display"]


def test_case3_no_hardcoded_values_in_render_path():
    """Static check: the JS render path derives every displayed value from the
    card row via the safe formatters and contains no hardcoded numeric literal
    standing in for a data-layer field (e.g. the remembered -8.2% for S)."""
    src = (ROOT / "earnings.js").read_text()
    # The renderEarningsCard composer must read from the card argument, not a
    # constant, for each token.
    m = re.search(r"function renderEarningsCard\(card\)\s*\{(.*?)\n\}", src, re.S)
    assert m, "renderEarningsCard not found"
    body = m.group(1)
    assert "c.revenue_actual" in body
    assert "c.eps_actual" in body
    assert "c.stock_reaction_pct" in body
    # No fabricated reaction constant anywhere in the file.
    assert "-8.2" not in src and "8.2%" not in src


def test_case4_single_dollar_sign():
    intel = _load_intel()
    row = _row_from_intel("S", intel)
    tokens = render_earnings_card(row)
    assert tokens["revenue"] == "$276.7M" and "$$" not in tokens["revenue"]
    assert tokens["eps"] == "$0.04" and "$$" not in tokens["eps"]


def test_case5_okta_card_unchanged():
    intel = _load_intel()
    row = _row_from_intel("OKTA", intel)
    tokens = render_earnings_card(row)
    assert tokens["reaction"]["display"] == "+5.8%"
    assert tokens["reaction"]["direction"] == "positive"
    assert tokens["reaction"]["available"] is True


def test_case6_full_set_audit_no_forbidden_tokens():
    """Every active post-earnings card runs clean through the renderer: no
    forbidden token, no bare em-dash where a value is expected."""
    intel = _load_intel()
    tickers = intel.get("tickers", {})
    failures = []
    audited = 0
    for tk, v in tickers.items():
        per = v.get("post_earnings_review") or {}
        if per.get("active") is not True:
            continue
        audited += 1
        tokens = render_earnings_card(_row_from_intel(tk, intel))
        blob = " ".join([tokens["revenue"], tokens["eps"], tokens["reaction"]["display"]])
        for bad in FORBIDDEN_DISPLAY_TOKENS:
            if bad in blob:
                failures.append((tk, bad, blob))
        if "\u2014" in blob:
            failures.append((tk, "bare-em-dash", blob))
    assert audited > 0, "no active cards audited"
    assert not failures, f"forbidden tokens in cards: {failures}"
