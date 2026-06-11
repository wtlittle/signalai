#!/usr/bin/env python3
"""Cron entrypoint that renders a briefing behind the freshness gate.

The daily/weekly briefing crons used to call ``render_email.py`` directly and
email whatever it produced. That is exactly how a stale snapshot shipped on
2026-06-11: the renderer had no way to refuse, and the cron had no way to know
it should have. This wrapper closes that loop.

It runs the freshness gate first (via ``render_email``). On a clean pass it
writes the rendered body to ``--output`` and exits 0 -- the cron then proceeds
to its existing lock-file + send_email step unchanged. On a freshness abort it:

  * does NOT write a body (so the cron's ``-s`` non-empty test fails and the
    send never happens),
  * writes the freshness_alert breadcrumb (handled inside render_email), and
  * fires a push / in_app operator alert via ``alert_briefing_skipped`` -- NOT
    an email, because email is the very channel the stale briefing would have
    used (see automation.alerts.quarantine_alert).

Exit codes mirror render_email plus one: 0 ok, 2 input unreadable, 3 missing
key, 4 output unwritable, 5 freshness gate aborted (push fired), 6 unexpected.

No emoji anywhere (user rule). Pure standard library + project imports.
"""
from __future__ import annotations

import argparse
import sys

from automation.jobs.render_email import (
    CANONICAL_DIR,
    FreshnessGateError,
    WEEKLY_FRESHNESS_ALERT_DIR,
    check_weekly_freshness,
    render_daily,
    render_weekly,
    render_weekly_html,
    write_freshness_alert,
    _load_json,
    _require_keys,
    WEEKLY_REQUIRED_KEYS,
)

# Manual-refresh link an operator can hit when a briefing is skipped.
MANUAL_REFRESH_LINK = "https://github.com/wtlittle/signalai/actions"


def _fire_skip_alert(kind: str, exc: FreshnessGateError) -> None:
    """Fire the operator push/in_app alert. Never raises (import-safe stub)."""
    try:
        from automation.alerts.quarantine_alert import alert_briefing_skipped
        alert_briefing_skipped(
            kind, exc.reason,
            hours_stale=exc.hours_stale,
            link=MANUAL_REFRESH_LINK,
        )
    except Exception as e:  # pragma: no cover - alert path must never cascade
        sys.stderr.write(f"WARN: skip-alert dispatch failed: {e}\n")


def _run_daily(args) -> int:
    try:
        body = render_daily(args.data_dir,
                            enforce_freshness=not args.no_freshness_gate)
    except FreshnessGateError as exc:
        write_freshness_alert(exc.reason, send_date=exc.send_date,
                              hours_stale=exc.hours_stale)
        sys.stderr.write(
            f"FRESHNESS GATE: daily briefing skipped -- {exc.reason}\n")
        _fire_skip_alert("daily", exc)
        return 5
    return _write_body(body, args.output)


def _run_weekly(args) -> int:
    input_path = args.input or "weekly_briefing.json"
    data = _load_json(input_path)
    if data is None:
        sys.stderr.write(f"ERROR: cannot read weekly input JSON: {input_path}\n")
        return 2
    missing = _require_keys(data, WEEKLY_REQUIRED_KEYS)
    if missing:
        sys.stderr.write(
            "ERROR: weekly_briefing.json missing required top-level keys: "
            + ", ".join(missing) + "\n")
        return 3
    if not args.no_freshness_gate:
        try:
            check_weekly_freshness(args.data_dir)
        except FreshnessGateError as exc:
            write_freshness_alert(exc.reason, send_date=exc.send_date,
                                  hours_stale=exc.hours_stale,
                                  alert_dir=WEEKLY_FRESHNESS_ALERT_DIR)
            sys.stderr.write(
                f"FRESHNESS GATE: weekly briefing skipped -- {exc.reason}\n")
            _fire_skip_alert("weekly", exc)
            return 5
    body = render_weekly_html(data) if args.mode == "weekly_html" else render_weekly(data)
    return _write_body(body, args.output)


def _write_body(body: str, output: str) -> int:
    try:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError as exc:
        sys.stderr.write(f"ERROR: cannot write output: {exc}\n")
        return 4
    print(output)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a SignalAI briefing behind the freshness gate; "
                    "fire a push alert (not email) when the send is skipped.")
    parser.add_argument("--mode", required=True,
                        choices=["weekly", "daily", "weekly_html"])
    parser.add_argument("--input", default=None,
                        help="Input JSON path (weekly mode)")
    parser.add_argument("--output", required=True, help="Output text file path")
    parser.add_argument("--data-dir", default=CANONICAL_DIR,
                        help="Canonical data dir for the freshness gate")
    parser.add_argument("--no-freshness-gate", action="store_true",
                        help="Bypass the gate (ad-hoc use only)")
    args = parser.parse_args(argv)

    try:
        if args.mode == "daily":
            return _run_daily(args)
        return _run_weekly(args)
    except Exception as exc:  # pragma: no cover - last-resort guard
        sys.stderr.write(f"ERROR: unexpected failure: {exc}\n")
        return 6


if __name__ == "__main__":
    sys.exit(main())
