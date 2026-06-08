"""
Today's forward-look generator — a single cheap sonar call that surfaces the
real, market-moving events SCHEDULED TODAY (US/Eastern): Fed meetings/speakers,
CPI/PPI/NFP releases, Trump press conferences, tariff deadlines, congressional
hearings, OPEC/ECB/BOJ decisions, geopolitical flashpoints, and major earnings
(with watchlist earnings called out).

This owns the "what to watch" forward look that daily_narrative.py used to
produce as its ``tomorrow`` field. The daily email renders this at the TOP of
the briefing, above the "what moved yesterday" recap.

Cost: ~$0.005/run. Model ``sonar``, ~600 input + ~700 output tokens, one search.

DATA INTEGRITY MANDATE: never fabricate events. If the call fails or returns
nothing usable, write an empty-but-valid payload and exit 0 so the renderer
shows "None scheduled." rather than invented calendar entries.

Usage:
    python -m automation.jobs.today_events --output today_events.json [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.shared.tickers import load_tickers  # noqa: E402
from automation.perplexity.client import call_perplexity  # noqa: E402

VALID_TYPES = {
    "macro_release", "fed", "political", "earnings",
    "central_bank", "geopolitical", "other",
}


def _today_et(date_override: str | None) -> str:
    """Return the target date (YYYY-MM-DD) in US/Eastern.

    If ``date_override`` is supplied it is trusted verbatim (already a date
    string). Otherwise the current UTC instant is converted to America/New_York
    so a pre-market UTC cron tick still resolves to the correct trading day.
    """
    if date_override:
        return date_override.strip()
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        now = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        pass
    return now.strftime("%Y-%m-%d")


def _empty_payload(date_et: str, error: str | None = None) -> dict:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "date_et": date_et,
        "events": [],
        "watchlist_earnings": [],
        "key_debates": [],
        "raw_citations": [],
    }
    if error:
        payload["error"] = error
    return payload


def build_prompt(date_et: str, watchlist: list[str]) -> str:
    """Sonar prompt asking for events SCHEDULED TODAY in US/Eastern."""
    # Pretty date for the model ("Monday, June 8, 2026"); falls back to ISO.
    try:
        pretty = datetime.strptime(date_et, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    except ValueError:
        pretty = date_et
    watch_str = ", ".join(watchlist) if watchlist else "(none)"
    return f"""You are briefing a buy-side fundamental analyst (1-3 year horizon) on what to watch TODAY. Today is {pretty} ({date_et}), US/Eastern.

List ONLY events that are actually SCHEDULED FOR TODAY ({date_et}, US/Eastern). Cover: Federal Reserve meetings/decisions/speakers, macro data releases (CPI, PPI, NFP/jobs, retail sales, PCE, GDP, ISM, jobless claims), US political events (Trump press conferences, executive actions, tariff deadlines), congressional hearings, central-bank decisions (ECB, BOJ, BOE, PBOC), OPEC meetings, geopolitical flashpoints with market impact, and major corporate earnings.

CRITICAL DATA-INTEGRITY RULES:
- Include an event ONLY if you have evidence it is scheduled for TODAY. If nothing material is scheduled, return an empty events array. NEVER invent an event, a time, or a consensus number.
- Times must be US/Eastern. Use "HH:MM" 24h ET (e.g. "08:30"), or "All day", or "TBD" when the time is genuinely unknown. Do not guess a precise time you do not know.
- Order events by market relevance (most important first). Return 5 to 10 events when that many genuinely exist; fewer (or zero) is fine if the calendar is light.

The analyst's watchlist tickers are: {watch_str}
When an event affects watchlist tickers or their sectors, prefer naming those tickers in tickers_sectors.

Separately, identify which of the watchlist tickers above report earnings TODAY. For each, give timing ("BMO" before open / "AMC" after close / "TBD") and consensus EPS and revenue (USD millions) IF you actually know them — use null when you do not. Do not list a ticker unless it genuinely reports today.

Also give 1-3 key market debates framed for TODAY — the bull vs bear question the tape is actually arguing about (e.g. "Will May CPI cool enough to keep a July cut in play?"). Each: a short topic question, a one-line bull case, a one-line bear case.

Constraints: no emoji, no markdown, plain text in every field. Each what_to_watch is ONE sentence under 30 words focused on what the analyst should track. Each bull/bear is one clause.

Return ONLY this JSON shape (no prose, no markdown fences):
{{
  "events": [
    {{"time_et": "08:30", "name": "May CPI", "type": "macro_release", "what_to_watch": "Watch core m/m; a hot print pushes the first cut later.", "tickers_sectors": ["SPY","XLF"]}}
  ],
  "watchlist_earnings": [
    {{"ticker": "ADBE", "time": "AMC", "consensus_eps": 4.97, "consensus_rev_m": 5290}}
  ],
  "key_debates": [
    {{"topic": "Will May CPI cool enough to keep a July cut in play?", "bull": "shelter disinflation broadens", "bear": "services ex-shelter stays sticky"}}
  ]
}}"""


def _coerce_events(raw, watchlist: set[str]) -> list[dict]:
    """Validate + de-duplicate the events array. Drops malformed entries.

    Never fabricates: an event missing a name is discarded rather than padded.
    De-dupes on (lowercased name, time_et) so the model repeating an event does
    not surface twice.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(raw, list):
        return out
    for e in raw:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        time_et = str(e.get("time_et") or "TBD").strip() or "TBD"
        key = (name.lower(), time_et.lower())
        if key in seen:
            continue
        seen.add(key)
        etype = str(e.get("type") or "other").strip().lower()
        if etype not in VALID_TYPES:
            etype = "other"
        tickers = e.get("tickers_sectors")
        if isinstance(tickers, str):
            tickers = [tickers]
        if not isinstance(tickers, list):
            tickers = []
        tickers = [str(t).strip() for t in tickers if str(t).strip()]
        out.append({
            "time_et": time_et,
            "name": name,
            "type": etype,
            "what_to_watch": str(e.get("what_to_watch") or "").strip(),
            "tickers_sectors": tickers,
        })
    return out


def _coerce_watchlist_earnings(raw, watchlist: set[str]) -> list[dict]:
    """Keep only well-formed rows whose ticker is on the watchlist."""
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for e in raw:
        if not isinstance(e, dict):
            continue
        ticker = str(e.get("ticker") or "").strip().upper()
        if not ticker or (watchlist and ticker not in watchlist) or ticker in seen:
            continue
        seen.add(ticker)

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        out.append({
            "ticker": ticker,
            "time": str(e.get("time") or "TBD").strip() or "TBD",
            "consensus_eps": _num(e.get("consensus_eps")),
            "consensus_rev_m": _num(e.get("consensus_rev_m")),
        })
    return out


def _coerce_debates(raw) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for d in raw[:3]:
        if not isinstance(d, dict):
            continue
        topic = str(d.get("topic") or "").strip()
        if not topic:
            continue
        out.append({
            "topic": topic,
            "bull": str(d.get("bull") or "").strip(),
            "bear": str(d.get("bear") or "").strip(),
        })
    return out


def _extract_citations(resp: dict) -> list[str]:
    """Best-effort pull of citation URLs from the parsed response."""
    cites = resp.get("citations") or resp.get("raw_citations")
    if isinstance(cites, list):
        return [str(c) for c in cites if c]
    return []


def run(output_path: str, date_override: str | None = None) -> int:
    date_et = _today_et(date_override)
    try:
        watchlist = load_tickers()
    except Exception as exc:  # noqa: BLE001
        print(f"[today_events] could not load watchlist: {exc}")
        watchlist = []
    watch_set = {t.upper() for t in watchlist}

    prompt = build_prompt(date_et, watchlist)
    print(f"[today_events] date_et={date_et} watchlist={len(watchlist)} — calling sonar...")

    out_path = Path(output_path)
    try:
        resp = call_perplexity(
            "_today", "today_events", prompt,
            system="You are a buy-side analyst. Return only structured JSON. Never fabricate events.",
            max_tokens=1400, temperature=0.1, force=True,
            extra_meta={"model": "sonar", "date_et": date_et},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[today_events] ERROR: sonar call failed: {exc}")
        from automation.shared.io_helpers import write_json
        write_json(out_path, _empty_payload(date_et, error=str(exc)[:300]))
        return 0

    # The shared client queues instead of calling when no API key is configured;
    # a queued/dry-run/skipped placeholder is not a real result — emit empty.
    if not isinstance(resp, dict) or any(
        resp.get(k) for k in ("queued", "dry_run", "skipped")
    ):
        reason = next((k for k in ("queued", "dry_run", "skipped") if isinstance(resp, dict) and resp.get(k)), "no_response")
        print(f"[today_events] no live result ({reason}) — writing empty payload")
        from automation.shared.io_helpers import write_json
        write_json(out_path, _empty_payload(date_et, error=reason))
        return 0

    # Some responses arrive wrapped as {"raw": "...json..."}; try to recover.
    if "events" not in resp and isinstance(resp.get("raw"), str):
        try:
            resp = json.loads(resp["raw"])
        except (ValueError, TypeError):
            pass

    events = _coerce_events(resp.get("events"), watch_set)
    watchlist_earnings = _coerce_watchlist_earnings(resp.get("watchlist_earnings"), watch_set)
    key_debates = _coerce_debates(resp.get("key_debates"))

    payload = _empty_payload(date_et)
    payload["events"] = events
    payload["watchlist_earnings"] = watchlist_earnings
    payload["key_debates"] = key_debates
    payload["raw_citations"] = _extract_citations(resp)

    from automation.shared.io_helpers import write_json
    write_json(out_path, payload)
    print(f"[today_events] wrote {len(events)} event(s), "
          f"{len(watchlist_earnings)} watchlist earning(s), "
          f"{len(key_debates)} debate(s) -> {out_path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate today's forward-look events JSON.")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--date", default=None, help="Override target date (YYYY-MM-DD, US/Eastern)")
    args = parser.parse_args(argv)
    return run(args.output, args.date)


if __name__ == "__main__":
    sys.exit(main())
