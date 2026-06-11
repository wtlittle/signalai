#!/usr/bin/env python3
"""Push / in-app alerts when a quality gate quarantines an artifact.

When the v2 drilldown generator withholds a note (validator failure, low
scorecard, or a quarantined Bull/Base/Bear section) or the briefing freshness
gate skips a send, an operator needs to know NOW -- the whole point of the gate
is that a degraded artifact must never ship silently. These alerts go to the
in_app activity feed and the push channel, and deliberately NOT to email: email
is the channel the degraded artifact itself would have used, so routing the
"we blocked it" alert to email would be self-defeating (and noisy).

This module reuses ``automation.alerts.emit`` primitives (Supabase activity-row
writer + the push dispatch stub) but bypasses the subscription-key catalog,
because a quarantine is an operational alert, not a user-facing subscription
type. It is import- and call-safe with no Supabase creds configured -- in that
case it logs and returns a no-op result rather than raising, so a quarantine in
the generator never cascades into a second failure.

No emoji anywhere (user rule).
"""
from __future__ import annotations

from datetime import datetime, timezone

from automation.alerts.emit import (
    FALLBACK_USER_EMAIL,
    _fetch_subscribers,
    _write_activity_row,
    _dispatch_channel,
)

# Quarantine alerts never use email (see module docstring).
QUARANTINE_CHANNELS = ("in_app", "push")

# alert_activity.alert_type value for quarantine events. Not in SUBSCRIPTION_KEYS
# on purpose -- it is an operational alert, written directly.
QUARANTINE_ALERT_TYPE = "quality_gate"


def _recipients() -> list[str]:
    """Operator email(s) to notify. Reuses the subscriber lookup for any feed,
    falling back to the single-user default. We only need an address to key the
    activity row + push; quarantine is not a real subscription type."""
    emails: list[str] = []
    try:
        for sub in _fetch_subscribers("weekly_briefing"):
            e = sub.get("user_email")
            if e and e not in emails:
                emails.append(e)
    except Exception:
        pass
    if not emails:
        emails = [FALLBACK_USER_EMAIL]
    return emails


def _emit_quarantine(title: str, body: str, *, ticker: str | None,
                     link: str | None, severity: str) -> dict:
    """Write the in_app activity row and dispatch push to each operator.

    Never raises. Returns {delivered: [...], activity_rows: int}.
    """
    summary = f"{title} -- {body}" if body else title
    delivered: list[dict] = []
    activity_rows = 0
    for user_email in _recipients():
        try:
            if _write_activity_row(user_email, QUARANTINE_ALERT_TYPE, ticker,
                                   summary, link, severity):
                activity_rows += 1
        except Exception:
            pass
        chans: list[str] = []
        for ch in QUARANTINE_CHANNELS:
            try:
                ok = _dispatch_channel(ch, user_email, {
                    "alert_type": QUARANTINE_ALERT_TYPE,
                    "ticker": ticker,
                    "summary": summary,
                    "link": link,
                    "severity": severity,
                })
                if ok:
                    chans.append(ch)
            except Exception:
                pass
        delivered.append({"user_email": user_email, "channels": chans})

    print(f"[quarantine_alert] {QUARANTINE_ALERT_TYPE} ticker={ticker or '-'} "
          f"severity={severity} activity_rows={activity_rows} :: {summary}")
    return {"delivered": delivered, "activity_rows": activity_rows,
            "fired_at": datetime.now(timezone.utc).isoformat()}


def alert_drilldown_quarantine(ticker: str, reasons: list[str], *,
                               quarantined_sections: list[str] | None = None,
                               link: str | None = None) -> dict:
    """Fire when a drilldown note is withheld by the quality gate."""
    title = f"Quality gate triggered: {ticker} drilldown quarantined"
    bits = []
    if quarantined_sections:
        bits.append("withheld sections: " + ", ".join(quarantined_sections))
    if reasons:
        bits.append("; ".join(str(r) for r in reasons))
    body = " | ".join(bits)[:600]
    return _emit_quarantine(title, body, ticker=ticker, link=link, severity="alert")


def alert_briefing_skipped(kind: str, reason: str, *,
                           hours_stale: float | None = None,
                           link: str | None = None) -> dict:
    """Fire when a daily/weekly briefing send is skipped by the freshness gate.

    ``kind`` is 'daily' or 'weekly'. ``reason`` is the human-readable cause.
    """
    title = f"{kind.title()} briefing skipped: stale data"
    parts = [reason]
    if hours_stale is not None:
        parts.append(f"data is {hours_stale:.1f}h old")
    body = " | ".join(parts)[:600]
    return _emit_quarantine(title, body, ticker=None, link=link, severity="alert")


__all__ = [
    "alert_drilldown_quarantine",
    "alert_briefing_skipped",
    "QUARANTINE_CHANNELS",
    "QUARANTINE_ALERT_TYPE",
]
