"""
Server-side alert emitter — single seam used by every cron to fire an alert
into a user's subscription stream.

Contract:
    emit_alert(
        alert_type: str,       # one of SUBSCRIPTION_KEYS
        ticker: str | None,    # optional
        summary: str,          # human-readable (1-3 sentences)
        link: str | None,      # optional deep-link into the dashboard
        severity: str = 'info' # 'info' | 'warn' | 'alert'
    ) -> dict

Behaviour:
    1. Looks up which users are subscribed to ``alert_type`` in the
       Supabase ``user_alert_subscriptions`` table.
    2. For every subscribed user, writes an ``alert_activity`` row so the
       dashboard's Recent Activity feed picks it up on next render.
    3. Dispatches the requested channels (in_app is implicit via the
       activity row; push and email are still TODO and are recorded in the
       returned dict so the caller can log them).

Channel delivery (push / email) is intentionally deferred — the activity
row IS the in_app channel today. Once SignalAI has push / SMTP providers
wired we extend ``_dispatch_channel`` instead of touching every cron.

This module is import-safe even when SUPABASE_URL / SUPABASE_SERVICE_KEY
are not set — calls turn into no-ops and return ``{"delivered": []}``
so the calling cron does not blow up.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Iterable

# Mirror of alerts.js SUBSCRIPTION_CATALOG keys. Keep these two lists in sync
# — adding a new subscription requires touching both files.
SUBSCRIPTION_KEYS = (
    "pre_earnings_note",
    "post_earnings_note",
    "earnings_day",
    "ma_rumor",
    "ma_status_change",
    "analyst_material",
    "weekly_briefing",
    "big_move_10pct",
    "sector_rotation",
)

# Default channel sets — fallback when a user's row is missing (or empty
# channels list). Mirror of alerts.js DEFAULT channel sets.
DEFAULT_CHANNELS = {
    "pre_earnings_note":  ["in_app"],
    "post_earnings_note": ["in_app"],
    "earnings_day":       ["push", "in_app"],
    "ma_rumor":           ["push", "in_app"],
    "ma_status_change":   ["in_app"],
    "analyst_material":   ["in_app"],
    "weekly_briefing":    ["email", "in_app"],
    "big_move_10pct":     ["push", "in_app"],
    "sector_rotation":    ["in_app"],
}

# Fallback user when Supabase has no subscribers row yet — keeps the
# alert stream populated for the single-user dev account.
FALLBACK_USER_EMAIL = os.environ.get("SIGNALAI_DEFAULT_USER", "wtlittle9498@gmail.com")


def _supabase_creds() -> tuple[str | None, str | None]:
    url = os.environ.get("SUPABASE_URL")
    # Prefer service key for backend work (RLS bypass), fall back to anon.
    key = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    return url, key


def _http_json(method: str, url: str, headers: dict, body: dict | None = None,
               timeout: int = 8) -> tuple[int, object]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "null"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "null")
        except Exception:
            payload = None
        return e.code, payload
    except Exception as e:
        return 0, {"error": str(e)}


def _fetch_subscribers(alert_type: str) -> list[dict]:
    """Return all users subscribed to alert_type.

    Each element: {"user_email": "...", "channels": ["in_app", ...]}
    Falls back to FALLBACK_USER_EMAIL with default channels when Supabase
    is unreachable or returns zero rows.
    """
    sb_url, sb_key = _supabase_creds()
    fallback = [{
        "user_email": FALLBACK_USER_EMAIL,
        "channels": list(DEFAULT_CHANNELS.get(alert_type, ["in_app"])),
    }]
    if not sb_url or not sb_key:
        return fallback

    # PostgREST select=user_email,subscriptions — we filter the JSONB client-side
    # because PostgREST -> filter syntax for nested JSONB existence is gnarlier
    # than just pulling N rows and parsing in Python (N = 1 today, will grow).
    url = f"{sb_url}/rest/v1/user_alert_subscriptions?select=user_email,subscriptions"
    headers = {"apikey": sb_key, "Authorization": f"Bearer {sb_key}"}
    status, rows = _http_json("GET", url, headers)
    if status != 200 or not isinstance(rows, list):
        return fallback

    out: list[dict] = []
    for row in rows:
        subs = (row or {}).get("subscriptions") or {}
        cfg = subs.get(alert_type)
        if cfg is None:
            continue
        if cfg.get("enabled") is False:
            continue
        channels = cfg.get("channels") or DEFAULT_CHANNELS.get(alert_type, ["in_app"])
        # Defensive: drop unknown channels
        channels = [c for c in channels if c in ("in_app", "push", "email")]
        if not channels:
            continue
        out.append({"user_email": row.get("user_email"), "channels": list(channels)})

    return out or fallback


def _write_activity_row(user_email: str, alert_type: str, ticker: str | None,
                        summary: str, link: str | None, severity: str) -> bool:
    """Persist the alert into alert_activity so the dashboard picks it up."""
    sb_url, sb_key = _supabase_creds()
    if not sb_url or not sb_key:
        return False
    url = f"{sb_url}/rest/v1/alert_activity"
    headers = {
        "apikey": sb_key,
        "Authorization": f"Bearer {sb_key}",
        "Prefer": "return=minimal",
    }
    body = {
        "user_email": user_email,
        "alert_type": alert_type,
        "ticker": ticker,
        "summary": summary,
        "link": link,
        "severity": severity,
        "fired_at": datetime.now(timezone.utc).isoformat(),
    }
    status, _ = _http_json("POST", url, headers, body=body)
    return 200 <= status < 300


def _dispatch_channel(channel: str, user_email: str, payload: dict) -> bool:
    """Stub for non-in_app channels.

    Today push/email delivery is handled out-of-band by the weekly briefing
    cron (which uses the Gmail connector directly). When the user wants
    automated push/email for every event type, extend this function — the
    rest of the pipeline already routes through here.
    """
    if channel == "in_app":
        return True  # already covered by the activity row insert.
    # Log-only stub. Caller can grep for [emit_alert pending channel=push] in cron logs.
    print(f"[emit_alert pending channel={channel}] user={user_email} type={payload.get('alert_type')} ticker={payload.get('ticker')}")
    return False


def emit_alert(
    alert_type: str,
    summary: str,
    *,
    ticker: str | None = None,
    link: str | None = None,
    severity: str = "info",
    extra: dict | None = None,
) -> dict:
    """Fire ``alert_type`` to all subscribed users.

    Returns a dict with ``delivered`` (list of {user_email, channels}) and
    ``activity_rows`` (count of alert_activity inserts). Never raises —
    failures are swallowed and logged so a flaky cron does not break the
    parent pipeline.
    """
    if alert_type not in SUBSCRIPTION_KEYS:
        print(f"[emit_alert] unknown alert_type={alert_type!r}, skipping")
        return {"delivered": [], "activity_rows": 0, "error": "unknown_alert_type"}

    severity = severity if severity in ("info", "warn", "alert") else "info"
    subscribers = _fetch_subscribers(alert_type)

    delivered: list[dict] = []
    activity_rows = 0
    for sub in subscribers:
        user_email = sub.get("user_email")
        channels: Iterable[str] = sub.get("channels") or ()
        if not user_email or not channels:
            continue

        # 1. Activity row covers in_app and is the durable record.
        if _write_activity_row(user_email, alert_type, ticker, summary, link, severity):
            activity_rows += 1

        # 2. Dispatch non-in_app channels.
        delivered_channels: list[str] = []
        for ch in channels:
            ok = _dispatch_channel(ch, user_email, {
                "alert_type": alert_type,
                "ticker": ticker,
                "summary": summary,
                "link": link,
                "severity": severity,
                "extra": extra or {},
            })
            if ok:
                delivered_channels.append(ch)

        delivered.append({"user_email": user_email, "channels": delivered_channels})

    print(f"[emit_alert] type={alert_type} ticker={ticker or '-'} severity={severity} "
          f"subscribers={len(subscribers)} activity_rows={activity_rows}")
    return {"delivered": delivered, "activity_rows": activity_rows}


__all__ = ["emit_alert", "SUBSCRIPTION_KEYS", "DEFAULT_CHANNELS"]
