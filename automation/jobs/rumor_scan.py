"""
Rumor scan job — detect potential M&A rumors among watchlist tickers.

Logic (4 gates, ALL must hold to enter pending_review):
  1. Intraday move >= 3% (using data-snapshot.json quotes.change1d)
  2. Tier-1 source (Reuters/WSJ/Bloomberg/FT/CNBC) within last 24h
  3. Story names a specific buyer or transaction
  4. Story published before or coincident with the move

Process:
  - Filter watchlist tickers to those with |change1d| >= 3.0
  - Skip tickers already in ma_status.json deals (already tracked)
  - For each candidate, queue a Perplexity research task to gather and
    score recent news (the LLM applies gates 2-4 and returns a confidence)
  - Write resulting candidates to ma_status.json `pending_review` array
  - Emit ma_rumor alert for each candidate with confidence >= 0.6
  - Pending_review entries auto-expire after 14 days unless corroborated
    (handled at render time)

Outputs:
  - Updates ma_status.json pending_review[]
  - Calls emit_alert(ma_rumor, ...) for each high-confidence candidate
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR, DATA_SNAPSHOT
from automation.shared.io_helpers import write_json
from automation.shared.tickers import load_tickers, load_common_names
from automation.perplexity.client import call_perplexity
from automation.alerts import emit_alert


MA_STATUS_PATH = ROOT_DIR / "ma_status.json"
MIN_INTRADAY_MOVE_PCT = float(os.environ.get("RUMOR_MIN_MOVE_PCT", "3.0"))
PENDING_TTL_DAYS = int(os.environ.get("RUMOR_PENDING_TTL_DAYS", "14"))
ALERT_CONFIDENCE_THRESHOLD = float(os.environ.get("RUMOR_ALERT_CONFIDENCE", "0.6"))
TIER1_DOMAINS = ("reuters.com", "wsj.com", "bloomberg.com", "ft.com", "cnbc.com")


def _load_ma_status() -> dict:
    if not MA_STATUS_PATH.exists():
        return {"_meta": {}, "pending_review": [], "deals": {}}
    return json.loads(MA_STATUS_PATH.read_text())


def _save_ma_status(data: dict) -> None:
    write_json(MA_STATUS_PATH, data)


def _expire_old_pending(ma: dict) -> None:
    """Drop pending_review entries older than PENDING_TTL_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PENDING_TTL_DAYS)
    pending = ma.get("pending_review", []) or []
    fresh = []
    for entry in pending:
        flagged = entry.get("flagged_at")
        try:
            ts = datetime.fromisoformat(flagged.replace("Z", "+00:00"))
        except Exception:
            ts = datetime.now(timezone.utc)
        if ts >= cutoff:
            fresh.append(entry)
    ma["pending_review"] = fresh


def _candidate_tickers(snapshot: dict, tracked: set[str]) -> list[dict]:
    """Return tickers with |change1d| >= threshold and not already tracked."""
    quotes = snapshot.get("quotes", {}) or {}
    candidates = []
    for ticker, q in quotes.items():
        if ticker in tracked:
            continue
        change1d = q.get("change1d")
        if change1d is None:
            continue
        if abs(float(change1d)) < MIN_INTRADAY_MOVE_PCT:
            continue
        candidates.append({
            "ticker": ticker,
            "name": q.get("longName") or ticker,
            "change1d": float(change1d),
            "price": q.get("price"),
            "marketCap": q.get("marketCap"),
            "sector": q.get("sector"),
        })
    # Largest moves first
    candidates.sort(key=lambda c: abs(c["change1d"]), reverse=True)
    return candidates


def _rumor_prompt(ticker: str, name: str, change1d: float) -> str:
    direction = "higher" if change1d > 0 else "lower"
    return f"""You are a buy-side M&A rumor screener. {ticker} ({name}) moved {change1d:+.2f}% in the last trading session ({direction}).

Search ONLY Tier-1 financial news from the last 24 hours: Reuters, WSJ, Bloomberg, Financial Times, CNBC. Look for any M&A rumor, takeover speculation, strategic review, activist campaign, or confirmed deal news.

Apply these 4 gates. ALL must hold for confirmed_rumor=true:
1. Move >= 3% intraday (already verified — assume true)
2. Tier-1 source (Reuters/WSJ/Bloomberg/FT/CNBC) story published within last 24h
3. Story names a specific buyer OR specific transaction (e.g., \"private equity in talks\" is NOT enough; \"Blackstone in talks\" IS)
4. Story published before or coincident with the price move (not after, which would be a reaction not a cause)

CRITICAL: If you cannot find a credible Tier-1 source with a named buyer published within 24h, return confirmed_rumor=false. Do NOT speculate or fabricate. Better to return false than invent details.

Return ONLY this JSON shape — no prose, no markdown fences:
{{
  "ticker": "{ticker}",
  "confirmed_rumor": true | false,
  "confidence": 0.0 to 1.0,
  "buyer": "string or null",
  "deal_type": "strategic | take-private | bid | unsolicited | strategic-review | null",
  "headline": "one-line summary or null",
  "rationale": "2-3 sentences explaining which gates held and why, or which failed",
  "sources": [
    {{"label": "publication name", "url": "https://...", "published_at": "YYYY-MM-DD or null", "tier": 1}}
  ],
  "gates": {{"gate1_move": true, "gate2_tier1": bool, "gate3_named_buyer": bool, "gate4_timing": bool}}
}}"""


def _validate_candidate(result: dict, candidate: dict) -> dict | None:
    """Return a pending_review entry only if all gates hold."""
    if not isinstance(result, dict):
        return None
    if not result.get("confirmed_rumor"):
        return None
    gates = result.get("gates", {}) or {}
    if not all(gates.get(k) for k in ("gate1_move", "gate2_tier1", "gate3_named_buyer", "gate4_timing")):
        return None
    sources = result.get("sources") or []
    tier1 = [s for s in sources if isinstance(s, dict) and (
        s.get("tier") == 1 or any(d in (s.get("url") or "") for d in TIER1_DOMAINS)
    )]
    if not tier1:
        return None
    confidence = result.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "ticker": candidate["ticker"],
        "company": candidate["name"],
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "intraday_move_pct": candidate["change1d"],
        "price_at_flag": candidate["price"],
        "buyer": result.get("buyer"),
        "deal_type": result.get("deal_type"),
        "headline": result.get("headline"),
        "rationale": result.get("rationale"),
        "confidence": max(0.0, min(1.0, confidence)),
        "sources": tier1,
        "gates": gates,
        "status": "pending_review",
    }


def run() -> dict:
    print(f"[rumor_scan] start min_move={MIN_INTRADAY_MOVE_PCT}% threshold={ALERT_CONFIDENCE_THRESHOLD}")
    if not DATA_SNAPSHOT.exists():
        print("[rumor_scan] data-snapshot.json missing — skip")
        return {"candidates": 0, "added": 0, "skipped_no_snapshot": True}

    snapshot = json.loads(DATA_SNAPSHOT.read_text())
    ma = _load_ma_status()
    _expire_old_pending(ma)

    tracked = set((ma.get("deals") or {}).keys())
    pending = ma.get("pending_review") or []
    already_pending = {p.get("ticker") for p in pending}

    candidates = _candidate_tickers(snapshot, tracked | already_pending)
    print(f"[rumor_scan] {len(candidates)} candidate(s) over {MIN_INTRADAY_MOVE_PCT}% intraday")

    if not candidates:
        ma["_meta"]["last_rumor_scan"] = datetime.now(timezone.utc).isoformat()
        _save_ma_status(ma)
        return {"candidates": 0, "added": 0}

    added = []
    for c in candidates:
        prompt = _rumor_prompt(c["ticker"], c["name"], c["change1d"])
        try:
            result = call_perplexity(
                ticker=c["ticker"],
                task="news_tag",
                prompt=prompt,
                system="You are a buy-side analyst. Return only structured JSON.",
                max_tokens=900,
                temperature=0.1,
                extra_meta={"rumor_scan": True, "model": "sonar"},
            )
        except Exception as exc:
            print(f"  [{c['ticker']}] perplexity error: {exc}")
            continue

        # Queued tasks return placeholder — skip scoring this run
        if isinstance(result, dict) and result.get("queued"):
            print(f"  [{c['ticker']}] queued to Computer — will score next run")
            continue

        entry = _validate_candidate(result, c)
        if entry is None:
            print(f"  [{c['ticker']}] no credible rumor (change1d={c['change1d']:+.2f}%)")
            continue

        pending.append(entry)
        added.append(entry)
        print(f"  [{c['ticker']}] FLAGGED rumor: buyer={entry['buyer']!r} conf={entry['confidence']:.2f}")

        # Emit alert when high-confidence
        if entry["confidence"] >= ALERT_CONFIDENCE_THRESHOLD:
            try:
                emit_alert(
                    alert_type="ma_rumor",
                    summary=f"{c['ticker']} potential M&A: {entry.get('buyer') or 'unnamed buyer'} — {entry.get('headline') or 'rumor flagged'}",
                    ticker=c["ticker"],
                    severity="warning",
                    link=(entry["sources"][0].get("url") if entry["sources"] else None),
                    extra={"confidence": entry["confidence"], "deal_type": entry.get("deal_type")},
                )
            except Exception as exc:
                print(f"  [{c['ticker']}] emit_alert failed: {exc}")

    ma["pending_review"] = pending
    ma["_meta"]["last_rumor_scan"] = datetime.now(timezone.utc).isoformat()
    _save_ma_status(ma)

    print(f"[rumor_scan] done — added {len(added)} pending_review entries (total pending: {len(pending)})")
    return {"candidates": len(candidates), "added": len(added), "pending_total": len(pending)}


if __name__ == "__main__":
    run()
