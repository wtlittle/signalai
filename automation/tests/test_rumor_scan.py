"""Smoke tests for rumor_scan target extraction + cross-listing."""
import json
from pathlib import Path

import automation.jobs.rumor_scan as rs


def _adsk_ptc_result():
    """Sonar-style response: Autodesk (ADSK) in talks to acquire PTC."""
    return {
        "ticker": "ADSK",
        "confirmed_rumor": True,
        "confidence": 0.82,
        "buyer": "Autodesk, Inc.",
        "buyer_ticker": "ADSK",
        "target": "PTC Inc.",
        "target_ticker": "PTC",
        "deal_type": "strategic",
        "headline": "Autodesk in talks to acquire PTC",
        "rationale": "Reuters reported Autodesk approached PTC about a deal.",
        "sources": [
            {"label": "Reuters", "url": "https://reuters.com/x", "published_at": "2026-06-08", "tier": 1}
        ],
        "gates": {"gate1_move": True, "gate2_tier1": True, "gate3_named_buyer": True, "gate4_timing": True},
    }


def _setup_paths(tmp_path, monkeypatch):
    snapshot = {"quotes": {"ADSK": {"longName": "Autodesk, Inc.", "change1d": 4.2, "price": 300.0, "marketCap": 6e10}}}
    snap_path = tmp_path / "data-snapshot.json"
    snap_path.write_text(json.dumps(snapshot))
    ma_path = tmp_path / "ma_status.json"

    monkeypatch.setattr(rs, "DATA_SNAPSHOT", snap_path)
    monkeypatch.setattr(rs, "MA_STATUS_PATH", ma_path)
    monkeypatch.setattr(rs, "load_tickers", lambda: ["ADSK", "PTC"])

    alerts = []
    monkeypatch.setattr(rs, "emit_alert", lambda **kw: alerts.append(kw))
    return ma_path, alerts


def test_two_sided_write_for_adsk_ptc(tmp_path, monkeypatch):
    ma_path, alerts = _setup_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(rs, "call_perplexity", lambda **kw: _adsk_ptc_result())

    rs.run()

    pending = json.loads(ma_path.read_text())["pending_review"]
    by_ticker = {p["ticker"]: p for p in pending}

    # Both sides present
    assert "ADSK" in by_ticker
    assert "PTC" in by_ticker

    adsk = by_ticker["ADSK"]
    assert adsk["buyer"] == "Autodesk, Inc."
    assert adsk["target"] == "PTC Inc."
    assert adsk["target_ticker"] == "PTC"
    assert adsk.get("cross_ref") is not True

    ptc = by_ticker["PTC"]
    assert ptc["cross_ref"] is True
    assert ptc["cross_ref_from"] == "ADSK"
    assert ptc["target_ticker"] == "PTC"

    # Alert fired and carries both sides
    assert len(alerts) == 1
    assert alerts[0]["extra"]["target_ticker"] == "PTC"
    assert alerts[0]["extra"]["buyer_ticker"] == "ADSK"


def test_self_referential_dropped(tmp_path, monkeypatch):
    ma_path, alerts = _setup_paths(tmp_path, monkeypatch)
    # buyer == company, no distinct target — the ADSK→"Autodesk" data bug.
    bad = _adsk_ptc_result()
    bad.update({"buyer": "Autodesk", "buyer_ticker": "ADSK", "target": None, "target_ticker": None})
    monkeypatch.setattr(rs, "call_perplexity", lambda **kw: bad)

    rs.run()

    pending = json.loads(ma_path.read_text())["pending_review"]
    assert pending == []
    assert alerts == []
