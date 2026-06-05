"""Tests for Phase 4c card UX polish — stock_reaction_pct hydration and
peer-ticker linkify regex behavior (Python mirror of JS logic).
"""
import re


# ---------------------------------------------------------------------------
# A) stock_reaction_pct hydration — verify that enrichRecentFromIntel logic
#    reads from intel.post_earnings_review.stock_reaction_pct correctly.
#    We replicate the JS enrichment logic in a minimal Python helper.
# ---------------------------------------------------------------------------

def _enrich_stock_reaction_pct(record, intel):
    """Python mirror of the JS enrichRecentFromIntel stock_reaction_pct logic.

    stock_reaction_pct is read regardless of the review's `active` flag: a
    `print_reaction` record (a recent reporter with actuals + a short note but
    no full review block yet) carries stock_reaction_pct without active===true,
    and the card must still surface the move rather than "n/a post-print".
    Only ever fills a null card field; never overwrites.
    """
    if not intel or not intel.get("post_earnings_review"):
        return record
    rev = intel["post_earnings_review"]
    if rev.get("stock_reaction_pct") is not None and record.get("stock_reaction_pct") is None:
        record["stock_reaction_pct"] = rev["stock_reaction_pct"]
    return record


def test_stock_reaction_pct_hydrated_from_intel():
    record = {"ticker": "NVDA", "stock_reaction_pct": None}
    intel = {
        "post_earnings_review": {
            "active": True,
            "stock_reaction_pct": 5.2,
        }
    }
    result = _enrich_stock_reaction_pct(record, intel)
    assert result["stock_reaction_pct"] == 5.2


def test_stock_reaction_pct_negative():
    record = {"ticker": "AAPL"}
    intel = {
        "post_earnings_review": {
            "active": True,
            "stock_reaction_pct": -3.7,
        }
    }
    result = _enrich_stock_reaction_pct(record, intel)
    assert result["stock_reaction_pct"] == -3.7


def test_stock_reaction_pct_not_overwritten():
    """If the record already has a stock_reaction_pct, don't overwrite it."""
    record = {"ticker": "NVDA", "stock_reaction_pct": 1.5}
    intel = {
        "post_earnings_review": {
            "active": True,
            "stock_reaction_pct": 5.2,
        }
    }
    result = _enrich_stock_reaction_pct(record, intel)
    assert result["stock_reaction_pct"] == 1.5


def test_stock_reaction_pct_hydrated_when_print_reaction():
    """A `print_reaction` record (active not True, no full review block yet)
    still carries stock_reaction_pct and the card must surface it — otherwise a
    recent reporter renders 'n/a post-print' despite authoritative market data
    being present. Regression for the render-gate bug where reaction was gated
    behind active===true."""
    record = {"ticker": "AVGO", "stock_reaction_pct": None}
    intel = {
        "post_earnings_review": {
            "active": None,
            "note_status": "print_reaction",
            "stock_reaction_pct": -12.6,
        }
    }
    result = _enrich_stock_reaction_pct(record, intel)
    assert result["stock_reaction_pct"] == -12.6


def test_stock_reaction_pct_hydrated_when_active_false():
    """Even an explicitly inactive review surfaces its reaction (read is no
    longer gated on the active flag)."""
    record = {"ticker": "NVDA"}
    intel = {
        "post_earnings_review": {
            "active": False,
            "stock_reaction_pct": 5.2,
        }
    }
    result = _enrich_stock_reaction_pct(record, intel)
    assert result["stock_reaction_pct"] == 5.2


def test_stock_reaction_pct_zero():
    """Zero is a valid reaction pct (flat stock) and should be hydrated."""
    record = {"ticker": "MSFT"}
    intel = {
        "post_earnings_review": {
            "active": True,
            "stock_reaction_pct": 0.0,
        }
    }
    result = _enrich_stock_reaction_pct(record, intel)
    assert result["stock_reaction_pct"] == 0.0


# ---------------------------------------------------------------------------
# B) Peer-ticker linkify regex — Python mirror of JS _linkifyPeerTickers
# ---------------------------------------------------------------------------

_TICKER_RE = re.compile(r'\b([A-Z]{1,5}(?:\.[A-Z])?)\b')


def _linkify_peer_tickers(text, current_ticker, known_tickers):
    """Python mirror of JS _linkifyPeerTickers."""
    if not text or not isinstance(text, str):
        return text
    known = set(known_tickers) if known_tickers else set()
    if not known:
        return text

    def _replace(m):
        sym = m.group(1)
        if sym == current_ticker:
            return sym
        if sym not in known:
            return sym
        return f'<a class="peer-ticker-link" data-peer-ticker="{sym}" href="#" role="button">{sym}</a>'

    return _TICKER_RE.sub(_replace, text)


def test_linkify_known_ticker():
    result = _linkify_peer_tickers("NVDA beat estimates", "AAPL", ["NVDA", "AAPL", "MSFT"])
    assert 'peer-ticker-link' in result
    assert 'data-peer-ticker="NVDA"' in result


def test_linkify_does_not_linkify_self():
    result = _linkify_peer_tickers("AAPL beat estimates", "AAPL", ["NVDA", "AAPL", "MSFT"])
    assert 'peer-ticker-link' not in result
    assert "AAPL" in result


def test_linkify_unknown_ticker_untouched():
    result = _linkify_peer_tickers("ZZZZZ had a wild day", "AAPL", ["NVDA", "AAPL", "MSFT"])
    assert 'peer-ticker-link' not in result


def test_linkify_multiple_tickers():
    text = "NVDA and MSFT both reported strong results"
    result = _linkify_peer_tickers(text, "AAPL", ["NVDA", "AAPL", "MSFT"])
    assert result.count('peer-ticker-link') == 2
    assert 'data-peer-ticker="NVDA"' in result
    assert 'data-peer-ticker="MSFT"' in result


def test_linkify_empty_coverage():
    result = _linkify_peer_tickers("NVDA beat", "AAPL", [])
    assert result == "NVDA beat"


def test_linkify_none_text():
    result = _linkify_peer_tickers(None, "AAPL", ["NVDA"])
    assert result is None


def test_linkify_ticker_with_dot():
    """Tickers like BRK.A should be linkified."""
    result = _linkify_peer_tickers("BRK.A reported", "AAPL", ["BRK.A", "AAPL"])
    assert 'data-peer-ticker="BRK.A"' in result
