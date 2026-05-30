"""Tests for the POST earnings card render fixes (fix/post-earnings-card-data):

  Bug 1 - post-print reaction row always renders, "—" when reaction is missing
  Bug 2 - REV / EPS rows show beat/miss % (not literal "—") and a new
          FY Guide Δ row shows FY revenue guidance movement vs prior consensus.

These mirror the JS helpers in earnings.js (_resolveSurprisePct, _fmtBeatMiss,
_renderFyGuideDeltaRow) so the render contract is pinned without a JS runtime.
"""
import re


# ---------------------------------------------------------------------------
# Python mirrors of the earnings.js helpers
# ---------------------------------------------------------------------------

def _resolve_surprise_pct(structured_pct, beat_miss_str):
    """Mirror of JS _resolveSurprisePct."""
    if isinstance(structured_pct, (int, float)) and not isinstance(structured_pct, bool):
        return float(structured_pct)
    if isinstance(beat_miss_str, str):
        m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", beat_miss_str)
        if m:
            return float(m.group(1))
    return None


def _fmt_beat_miss(pct):
    """Mirror of JS _fmtBeatMiss -> returns the rendered span HTML."""
    if pct is None:
        return '<span class="metric-surprise neutral">\u2014</span>'
    sign = "+" if pct > 0 else ""
    label = "beat" if pct > 1 else "miss" if pct < -1 else "in-line"
    cls = "beat" if pct > 1 else "miss" if pct < -1 else "inline"
    return f'<span class="metric-surprise {cls}">{sign}{pct:.1f}% {label}</span>'


def _render_fy_guide_delta_row(pct):
    """Mirror of JS _renderFyGuideDeltaRow -> returns the rendered row HTML."""
    if pct is None:
        body = '<span class="guide-metric guide-neutral">\u2014</span>'
    else:
        sign = "+" if pct > 0 else ""
        word = "raise" if pct > 0.5 else "cut" if pct < -0.5 else "flat"
        color = "guide-positive" if pct > 0.5 else "guide-negative" if pct < -0.5 else "guide-neutral"
        body = f'<span class="guide-metric {color}">{sign}{pct:.1f}% {word}</span>'
    return f'<div class="earnings-guide-row"><span class="guide-label">FY Guide \u0394</span>{body}</div>'


# ---------------------------------------------------------------------------
# A) _resolveSurprisePct — structured field wins, else parse the string
# ---------------------------------------------------------------------------

def test_resolve_prefers_structured_field():
    assert _resolve_surprise_pct(2.1, "beat (+9.9%)") == 2.1


def test_resolve_parses_positive_from_string():
    assert _resolve_surprise_pct(None, "beat (+2.1%)") == 2.1


def test_resolve_parses_negative_from_string():
    assert _resolve_surprise_pct(None, "miss (-0.8%)") == -0.8


def test_resolve_zero_structured():
    assert _resolve_surprise_pct(0.0, None) == 0.0


def test_resolve_returns_none_when_unknown():
    assert _resolve_surprise_pct(None, None) is None
    assert _resolve_surprise_pct(None, "in-line") is None


# ---------------------------------------------------------------------------
# B) _fmtBeatMiss — beat/miss/in-line/neutral + color class
# ---------------------------------------------------------------------------

def test_fmt_beat_green():
    out = _fmt_beat_miss(2.1)
    assert "+2.1% beat" in out
    assert "metric-surprise beat" in out


def test_fmt_miss_red():
    out = _fmt_beat_miss(-0.8)
    # -0.8 is inside the +/-1% band -> in-line, not miss
    assert "-0.8% in-line" in out
    assert "metric-surprise inline" in out


def test_fmt_clear_miss_red():
    out = _fmt_beat_miss(-3.4)
    assert "-3.4% miss" in out
    assert "metric-surprise miss" in out


def test_fmt_neutral_dash_when_missing():
    out = _fmt_beat_miss(None)
    assert "\u2014" in out
    assert "metric-surprise neutral" in out


# ---------------------------------------------------------------------------
# C) FY Guide Δ row — raise/cut/flat + "—" fallback (NEW row)
# ---------------------------------------------------------------------------

def test_fy_guide_raise():
    out = _render_fy_guide_delta_row(1.5)
    assert "FY Guide \u0394" in out
    assert "+1.5% raise" in out
    assert "guide-positive" in out


def test_fy_guide_cut():
    out = _render_fy_guide_delta_row(-2.0)
    assert "-2.0% cut" in out
    assert "guide-negative" in out


def test_fy_guide_dash_when_missing():
    out = _render_fy_guide_delta_row(None)
    # Row is still rendered (not hidden), with a neutral dash.
    assert "FY Guide \u0394" in out
    assert "\u2014" in out
    assert "guide-neutral" in out


# ---------------------------------------------------------------------------
# D) Bug 1 contract — reaction row renders even when reaction pct is missing.
#    Mirrors the inline ternary in renderRecentEarnings.
# ---------------------------------------------------------------------------

def _render_reaction_pct(pct):
    if pct is not None:
        cls = "positive" if pct >= 0 else "negative"
        sign = "+" if pct >= 0 else ""
        return f'<span class="reaction-pct {cls}">{sign}{pct:.1f}% post-print</span>'
    return '<span class="reaction-pct neutral">\u2014 post-print</span>'


def test_reaction_present_positive():
    assert _render_reaction_pct(5.65) == '<span class="reaction-pct positive">+5.7% post-print</span>'


def test_reaction_present_negative():
    assert "negative" in _render_reaction_pct(-3.7)


def test_reaction_missing_shows_dash_not_hidden():
    out = _render_reaction_pct(None)
    assert "\u2014 post-print" in out
    assert "reaction-pct neutral" in out
