"""Tests for the daily freshness gate (automation.jobs.render_email).

The gate must distinguish a STALE calendar (file too old, or zero rows across
all dates -- a refresh failure / corruption) from a fresh calendar that simply
has no rows for the send date (a quiet session, e.g. a June Friday after Q1
season). The latter is a valid "None today." render and must NOT be blocked.
This is the 2026-06-12 regression these tests pin down.
"""
import json
import os

import pytest

import automation.jobs.render_email as re


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _fresh_snapshot(data_dir):
    """A data-snapshot.json with a current mtime (gate check 1 passes)."""
    _write(os.path.join(data_dir, "data-snapshot.json"), {"quotes": {}})


def _calendar_with_other_dates():
    """A healthy calendar: rows for nearby dates, none for the send date."""
    return {
        "updated": "2026-06-12T11:07:06",
        "pre_earnings": [
            {"ticker": "ACN", "company": "Accenture", "date": "2026-06-18",
             "earnings_date": "2026-06-18", "timing": "BMO"},
        ],
        "post_earnings": [
            {"ticker": "ORCL", "company": "Oracle", "date": "2026-06-10",
             "earnings_date": "2026-06-10", "timing": "AMC"},
            {"ticker": "ADBE", "company": "Adobe", "date": "2026-06-11",
             "earnings_date": "2026-06-11", "timing": "AMC"},
        ],
    }


def test_fresh_snapshot_fresh_calendar_none_today_passes(tmp_path):
    """Fresh snapshot + fresh calendar with rows (but none for the send date)
    must PASS -- a quiet session is a legitimate empty render, not staleness."""
    d = str(tmp_path)
    _fresh_snapshot(d)
    _write(os.path.join(d, "earnings_calendar.json"), _calendar_with_other_dates())

    # No rows for 2026-06-12, but plenty for 6/10, 6/11, 6/18 -> must not raise.
    re.check_daily_freshness(d, send_date="2026-06-12")


def test_stale_calendar_mtime_fails(tmp_path):
    """Fresh snapshot + calendar file older than the 24h window must FAIL."""
    d = str(tmp_path)
    _fresh_snapshot(d)
    cal_path = os.path.join(d, "earnings_calendar.json")
    _write(cal_path, _calendar_with_other_dates())
    # Backdate the calendar's mtime past the 24h ceiling.
    old = os.path.getmtime(cal_path) - (
        re.DAILY_CALENDAR_MAX_AGE_HOURS + 1) * 3600
    os.utime(cal_path, (old, old))

    with pytest.raises(re.FreshnessGateError) as exc:
        re.check_daily_freshness(d, send_date="2026-06-12")
    assert "earnings_calendar.json" in exc.value.reason
    assert "stale" in exc.value.reason


def test_stale_snapshot_fails(tmp_path):
    """Snapshot older than the 4h window must FAIL regardless of calendar."""
    d = str(tmp_path)
    snap_path = os.path.join(d, "data-snapshot.json")
    _write(snap_path, {"quotes": {}})
    old = os.path.getmtime(snap_path) - (
        re.DAILY_SNAPSHOT_MAX_AGE_HOURS + 1) * 3600
    os.utime(snap_path, (old, old))
    _write(os.path.join(d, "earnings_calendar.json"), _calendar_with_other_dates())

    with pytest.raises(re.FreshnessGateError) as exc:
        re.check_daily_freshness(d, send_date="2026-06-12")
    assert "data-snapshot.json" in exc.value.reason


def test_empty_calendar_fails(tmp_path):
    """Fresh snapshot + a calendar with zero rows across all dates must FAIL."""
    d = str(tmp_path)
    _fresh_snapshot(d)
    _write(os.path.join(d, "earnings_calendar.json"),
           {"updated": "2026-06-12T11:07:06", "pre_earnings": [], "post_earnings": []})

    with pytest.raises(re.FreshnessGateError) as exc:
        re.check_daily_freshness(d, send_date="2026-06-12")
    assert "zero rows" in exc.value.reason


def test_missing_calendar_fails(tmp_path):
    """Fresh snapshot but no calendar file at all must FAIL."""
    d = str(tmp_path)
    _fresh_snapshot(d)

    with pytest.raises(re.FreshnessGateError) as exc:
        re.check_daily_freshness(d, send_date="2026-06-12")
    assert "missing" in exc.value.reason
