"""Tests for today_events generation + the renderer's WHAT TO EXPECT TODAY block."""
import json
from datetime import datetime, timezone

import automation.jobs.today_events as te
import automation.jobs.render_email as re


def _sonar_payload():
    """A well-formed sonar response with events, watchlist earnings, debates."""
    return {
        "events": [
            {
                "time_et": "08:30",
                "name": "May CPI",
                "type": "macro_release",
                "what_to_watch": "Watch core m/m; a hot print pushes the first cut later.",
                "tickers_sectors": ["SPY", "XLF"],
            },
            {
                "time_et": "10:00",
                "name": "Powell Senate testimony",
                "type": "fed",
                "what_to_watch": "Tariff-passthrough language is the key tell.",
                "tickers_sectors": [],
            },
            # Duplicate of the first event — must be de-duped away.
            {
                "time_et": "08:30",
                "name": "May CPI",
                "type": "macro_release",
                "what_to_watch": "dup",
                "tickers_sectors": [],
            },
            # Malformed: no name — must be dropped.
            {"time_et": "12:00", "type": "other"},
        ],
        "watchlist_earnings": [
            {"ticker": "ADBE", "time": "AMC", "consensus_eps": 4.97, "consensus_rev_m": 5290},
            # Off-watchlist ticker — must be filtered out.
            {"ticker": "ZZZZ", "time": "BMO", "consensus_eps": 1.0, "consensus_rev_m": 100},
        ],
        "key_debates": [
            {"topic": "Will May CPI cool enough to keep a July cut in play?",
             "bull": "shelter disinflation broadens",
             "bear": "services ex-shelter stays sticky"},
        ],
    }


def test_run_writes_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(te, "load_tickers", lambda: ["ADBE", "ORCL", "NVDA"])
    monkeypatch.setattr(te, "call_perplexity", lambda *a, **k: _sonar_payload())

    out = tmp_path / "today_events.json"
    rc = te.run(str(out), date_override="2026-06-08")
    assert rc == 0

    data = json.loads(out.read_text())
    # Top-level schema keys present
    for key in ("generated_at_utc", "date_et", "events", "watchlist_earnings", "key_debates", "raw_citations"):
        assert key in data
    assert data["date_et"] == "2026-06-08"

    # Duplicate + malformed events dropped → 2 clean events
    assert len(data["events"]) == 2
    names = [e["name"] for e in data["events"]]
    assert names == ["May CPI", "Powell Senate testimony"]
    assert data["events"][0]["type"] == "macro_release"
    assert data["events"][0]["tickers_sectors"] == ["SPY", "XLF"]

    # Off-watchlist earnings filtered; ADBE kept
    assert len(data["watchlist_earnings"]) == 1
    assert data["watchlist_earnings"][0]["ticker"] == "ADBE"

    assert len(data["key_debates"]) == 1


def test_run_empty_fallback_on_queued(tmp_path, monkeypatch):
    """When the client queues instead of calling, write an empty valid payload."""
    monkeypatch.setattr(te, "load_tickers", lambda: ["ADBE"])
    monkeypatch.setattr(te, "call_perplexity", lambda *a, **k: {"queued": True})

    out = tmp_path / "today_events.json"
    rc = te.run(str(out), date_override="2026-06-08")
    assert rc == 0

    data = json.loads(out.read_text())
    assert data["events"] == []
    assert data["watchlist_earnings"] == []
    assert data["key_debates"] == []
    assert data.get("error") == "queued"


def test_run_empty_fallback_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(te, "load_tickers", lambda: ["ADBE"])

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(te, "call_perplexity", _boom)
    out = tmp_path / "today_events.json"
    rc = te.run(str(out), date_override="2026-06-08")
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["events"] == []
    assert "network down" in data.get("error", "")


# --------------------------------------------------------------------------- #
# Renderer tests
# --------------------------------------------------------------------------- #
def _fresh(payload):
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return payload


def test_render_populated():
    payload = _fresh({
        "date_et": "2026-06-08",
        "events": [
            {"time_et": "08:30", "name": "May CPI", "type": "macro_release",
             "what_to_watch": "Watch core m/m print.", "tickers_sectors": ["SPY", "XLF"]},
        ],
        "watchlist_earnings": [
            {"ticker": "ADBE", "time": "AMC", "consensus_eps": 4.97, "consensus_rev_m": 5290},
        ],
        "key_debates": [
            {"topic": "Will May CPI cool?", "bull": "shelter eases", "bear": "services sticky"},
        ],
    })
    lines = re._render_today_events(payload, "2026-06-08")
    text = "\n".join(lines)
    assert "WHAT TO EXPECT TODAY — Monday, June 8, 2026" in text
    assert "08:30 ET  May CPI  [SPY, XLF]" in text
    assert "Watch core m/m print." in text
    assert "Watchlist earnings today:" in text
    assert "ADBE" in text and "EPS $4.97e" in text and "Rev $5.29Be" in text
    assert "Key debates today:" in text
    assert "Bull: shelter eases" in text


def test_render_empty_events_none_scheduled():
    payload = _fresh({"date_et": "2026-06-08", "events": [],
                      "watchlist_earnings": [], "key_debates": []})
    lines = re._render_today_events(payload, "2026-06-08")
    assert "None scheduled." in "\n".join(lines)


def test_render_stale_payload_none_scheduled():
    """A payload older than 24h must render as None scheduled."""
    payload = {
        "generated_at_utc": "2026-06-06T00:00:00+00:00",
        "date_et": "2026-06-06",
        "events": [{"time_et": "08:30", "name": "Old event", "type": "other",
                    "what_to_watch": "stale", "tickers_sectors": []}],
        "watchlist_earnings": [], "key_debates": [],
    }
    now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    lines = re._render_today_events(payload, "2026-06-08", now_utc=now)
    assert "None scheduled." in "\n".join(lines)
    assert "Old event" not in "\n".join(lines)


def test_render_missing_payload_none_scheduled():
    lines = re._render_today_events(None, "2026-06-08")
    assert "None scheduled." in "\n".join(lines)


def test_render_omits_empty_subsections():
    payload = _fresh({
        "date_et": "2026-06-08",
        "events": [{"time_et": "All day", "name": "OPEC meeting", "type": "geopolitical",
                    "what_to_watch": "Quota decision.", "tickers_sectors": ["XLE"]}],
        "watchlist_earnings": [],
        "key_debates": [],
    })
    text = "\n".join(re._render_today_events(payload, "2026-06-08"))
    assert "OPEC meeting" in text
    assert "Watchlist earnings today:" not in text
    assert "Key debates today:" not in text
    # "All day" has no "ET" suffix appended
    assert "All day   OPEC meeting" in text
