"""
Note Diff Injector — keeps `earnings_intel.json` signal scorecards live and reactive.

For every PRE-earnings ticker tracked in earnings_intel.json this job:

  1. Pulls recent news via backend.py /news endpoint
     (falls back to yfinance directly if the backend is offline).
  2. Runs `build_news_tagging_prompt` on the article batch to get
     catalyst_tag / direction / blurb / priority.
  3. For each High-priority article published in the last 7 days, checks
     whether the headline+blurb references an existing signal_scorecard
     entry (keyword overlap heuristic). If yes, updates the signal's
     status (WATCHING -> CONFIRMED / FAILED / WATCHING+NOTE) and appends
     a `signal_changes` entry referencing the article URL.
  4. Injects any pending estimate-drift signals from
     automation/queue/estimate_drift_signals.json (Skill 2 output) as
     new WATCHING entries on the relevant ticker.
  5. Runs a peer cross-read for tickers whose sector peers (from
     backend.py SECTOR_PEERS) reported earnings in the last 5 days —
     queues build_peer_read_across_prompt and adds a WATCHING peer
     read-across signal placeholder.
  6. Writes the updated earnings_intel.json back, and (if Supabase env
     vars are set) upserts a row into the `signal_change_log` table for
     dashboard surfacing.

Usage:
    python -m automation.jobs.note_diff_injector --dry-run
    python -m automation.jobs.note_diff_injector --ticker AMZN
    BACKEND_URL=http://localhost:5001 python -m automation.jobs.note_diff_injector

Idempotency:
    - Each signal_changes entry carries a `source_url` field; we never append
      the same (signal_id, source_url) pair twice.
    - Estimate-drift signals deduplicated on (ticker, signal_label).
    - Peer cross-read signals deduplicated on (ticker, peer_ticker, peer_earnings_date).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR, EARNINGS_CALENDAR  # noqa: E402
from automation.shared.io_helpers import read_json, write_json   # noqa: E402
from automation.shared.tickers import load_common_names          # noqa: E402
from automation.perplexity.client import call_perplexity         # noqa: E402
from automation.perplexity.prompts import (                      # noqa: E402
    build_news_tagging_prompt,
    build_peer_read_across_prompt,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EARNINGS_INTEL_PATH   = ROOT_DIR / "earnings_intel.json"
DRIFT_SIGNALS_PATH    = ROOT_DIR / "automation" / "queue" / "estimate_drift_signals.json"
BACKEND_URL           = os.environ.get("BACKEND_URL", "http://127.0.0.1:5001").rstrip("/")
NEWS_LOOKBACK_DAYS    = int(os.environ.get("NOTE_DIFF_NEWS_LOOKBACK_DAYS", "7"))
PEER_LOOKBACK_DAYS    = int(os.environ.get("NOTE_DIFF_PEER_LOOKBACK_DAYS", "5"))
TABLE_CHANGE_LOG      = "signal_change_log"

# Word-level similarity threshold for matching a news article to a signal.
SIGNAL_MATCH_THRESHOLD = float(os.environ.get("SIGNAL_MATCH_THRESHOLD", "0.18"))


# ---------------------------------------------------------------------------
# News fetcher
# ---------------------------------------------------------------------------
def _fetch_news(tickers: list[str]) -> list[dict]:
    if not tickers:
        return []
    syms = ",".join(tickers[:20])
    try:
        import requests
        resp = requests.get(f"{BACKEND_URL}/news", params={"symbols": syms}, timeout=10)
        resp.raise_for_status()
        return resp.json() or []
    except Exception as exc:
        print(f"  [WARN] /news backend fetch failed ({exc}); falling back to yfinance")
    # yfinance fallback
    out: list[dict] = []
    try:
        import yfinance as yf
        for sym in tickers[:20]:
            try:
                t = yf.Ticker(sym)
                items = t.news or []
                for item in items[:5]:
                    content = item.get("content", {}) if isinstance(item, dict) else {}
                    title = content.get("title", "")
                    if not title:
                        continue
                    click = content.get("clickThroughUrl") or content.get("canonicalUrl") or {}
                    url = click.get("url", "") if isinstance(click, dict) else ""
                    out.append({
                        "ticker":   sym,
                        "title":    title,
                        "url":      url,
                        "source":   (content.get("provider") or {}).get("displayName", ""),
                        "pubDate":  content.get("pubDate", "") or content.get("displayTime", ""),
                    })
            except Exception:
                continue
    except ImportError:
        pass
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "the","a","an","and","or","of","to","in","for","on","with","at","by","from",
    "is","are","was","were","be","been","being","this","that","these","those",
    "as","it","its","into","than","not","but","so","if","than","over","under",
    "will","could","would","may","might","should","amid","while","new","more",
    "than","up","down","stock","stocks","share","shares","earnings","report",
    "company","companies","fy","q1","q2","q3","q4",
}


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]+", (text or "").lower()) if w not in _STOPWORDS and len(w) > 2}


def _similarity(a: str, b: str) -> float:
    """Jaccard-style similarity on tokenized strings."""
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _is_recent(date_str: str | None, days: int) -> bool:
    if not date_str:
        return False
    try:
        # accept both ISO and epoch-seconds-as-string
        if date_str.isdigit():
            ts = _dt.datetime.fromtimestamp(int(date_str), tz=_dt.timezone.utc)
        else:
            ts = _dt.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        return False
    age = _dt.datetime.now(_dt.timezone.utc) - ts.astimezone(_dt.timezone.utc)
    return age <= _dt.timedelta(days=days)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# News -> signal matching
# ---------------------------------------------------------------------------
def _classify_articles(ticker: str, company: str, articles: list[dict]) -> list[dict]:
    """Run build_news_tagging_prompt; returns one classification per input article (same order)."""
    if not articles:
        return []
    batch = [{"headline": a.get("title", ""), "teaser": a.get("blurb") or a.get("title", ""),
              "source": a.get("source", ""), "url": a.get("url", "")} for a in articles]
    prompt = build_news_tagging_prompt(ticker, company, batch)
    try:
        result = call_perplexity(
            ticker=ticker,
            task="news_tagging",
            prompt=prompt,
            system="Return ONLY a JSON array. No markdown fences, no commentary.",
            max_tokens=900,
            extra_meta={"article_count": len(articles)},
        )
    except Exception as exc:
        print(f"  [WARN] news_tagging call failed for {ticker}: {exc}")
        return []
    if isinstance(result, dict) and (result.get("queued") or result.get("dry_run") or result.get("skipped")):
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]
    return []


def _match_signal(article_text: str, scorecard: list[dict]) -> dict | None:
    """Return the best-matching signal_scorecard entry (or None)."""
    best = None
    best_score = SIGNAL_MATCH_THRESHOLD
    for sig in scorecard:
        candidate = " ".join([sig.get("label") or "", sig.get("note") or ""])
        score = _similarity(article_text, candidate)
        if score > best_score:
            best = sig
            best_score = score
    return best


def _new_status(direction: str, current: str) -> str:
    """Map news direction + current status to a new status."""
    if current not in ("WATCHING",):
        return current
    if direction == "Bullish":
        return "CONFIRMED"
    if direction == "Bearish":
        return "FAILED"
    return "WATCHING+NOTE"


# ---------------------------------------------------------------------------
# Estimate-drift signal injection
# ---------------------------------------------------------------------------
def _consume_drift_signals(intel: dict, dry_run: bool) -> int:
    if not DRIFT_SIGNALS_PATH.exists():
        return 0
    payload = read_json(DRIFT_SIGNALS_PATH) or {}
    signals = payload.get("signals", []) or []
    if not signals:
        return 0
    consumed = 0
    for sig in signals:
        ticker = sig.get("ticker")
        if not ticker or ticker not in intel.get("tickers", {}):
            continue
        scorecard = intel["tickers"][ticker].setdefault("signal_scorecard", [])
        label = sig.get("signal_label")
        if any(s.get("label") == label for s in scorecard):
            continue
        scorecard.append({
            "signal_id":     f"estimate_drift_{ticker.lower()}_{sig.get('earnings_date', '').replace('-', '')}",
            "label":         label,
            "status":        sig.get("signal_status", "WATCHING"),
            "note":          (
                f"Forward EPS estimate has drifted "
                f"{round((sig.get('eps_revision_4w') or 0) * 100, 1)}% over the past 4 weeks "
                f"heading into the {sig.get('earnings_date')} print."
            ),
            "watch_quarter": None,
            "source":        "estimate_revision_tracker",
            "resolved_at":   None,
            "created_at":    sig.get("created_at") or _now_iso(),
        })
        consumed += 1
    if consumed and not dry_run:
        # Empty out the consumed payload so we don't re-inject next run.
        payload["signals"] = []
        payload["consumed_at"] = _now_iso()
        write_json(DRIFT_SIGNALS_PATH, payload)
    return consumed


# ---------------------------------------------------------------------------
# Peer read-across (best-effort, queue-only)
# ---------------------------------------------------------------------------
_SECTOR_PEERS: dict[str, list[str]] = {}


def _load_sector_peers() -> dict[str, list[str]]:
    """Lazily parse backend.py SECTOR_PEERS so we don't need to import the running server."""
    global _SECTOR_PEERS
    if _SECTOR_PEERS:
        return _SECTOR_PEERS
    text = (ROOT_DIR / "backend.py").read_text(encoding="utf-8")
    m = re.search(r"SECTOR_PEERS\s*=\s*(\{.*?\n\})", text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1)
    # naive eval-safe parser: replace single-quoted strings, then ast.literal_eval
    import ast
    try:
        _SECTOR_PEERS = ast.literal_eval(raw)
    except Exception:
        _SECTOR_PEERS = {}
    return _SECTOR_PEERS


def _ticker_to_sector(ticker: str) -> str | None:
    peers = _load_sector_peers()
    for sector, members in peers.items():
        if ticker in members:
            return sector
    return None


def _recent_peer_prints(calendar: dict) -> dict[str, str]:
    """Return {peer_ticker: earnings_date_iso} for peers who reported in last PEER_LOOKBACK_DAYS."""
    out: dict[str, str] = {}
    today = _dt.date.today()
    for entry in calendar.get("post_earnings", []) or []:
        ticker = entry.get("ticker")
        date_str = entry.get("earnings_date")
        if not ticker or not date_str:
            continue
        try:
            d = _dt.date.fromisoformat(date_str[:10])
        except ValueError:
            continue
        if 0 <= (today - d).days <= PEER_LOOKBACK_DAYS:
            out[ticker] = date_str
    return out


def _queue_peer_read_across(target_ticker: str, target_intel: dict, peers_recent: dict[str, str]) -> int:
    """Queue a Perplexity peer-read-across prompt per recently-printing sector peer."""
    sector = _ticker_to_sector(target_ticker)
    if not sector:
        return 0
    peer_list = _load_sector_peers().get(sector, [])
    next_date = target_intel.get("next_earnings_date") or target_intel.get("last_earnings_date") or ""
    common_names = load_common_names()
    company = common_names.get(target_ticker, target_ticker)
    scorecard = target_intel.setdefault("signal_scorecard", [])
    queued = 0
    for peer in peer_list:
        if peer == target_ticker:
            continue
        if peer not in peers_recent:
            continue
        peer_date = peers_recent[peer]
        signal_id = f"peer_read_{target_ticker.lower()}_{peer.lower()}_{peer_date.replace('-', '')}"
        if any(s.get("signal_id") == signal_id for s in scorecard):
            continue
        peer_company = common_names.get(peer, peer)
        prompt = build_peer_read_across_prompt(
            target_ticker=target_ticker, target_company=company, target_earnings_date=next_date,
            peer_ticker=peer, peer_company=peer_company, peer_earnings_date=peer_date,
            peer_print_summary=f"{peer_company} reported on {peer_date}. Refer to the latest "
                               f"post-earnings note in notes/post_earnings/{peer}_{peer_date}.md for details.",
        )
        try:
            call_perplexity(
                ticker=target_ticker,
                task=f"peer_read_across_{peer}",
                prompt=prompt,
                system="Return ONLY structured JSON. No markdown fences, no prose.",
                max_tokens=600,
                extra_meta={"peer_ticker": peer, "peer_earnings_date": peer_date, "target_earnings_date": next_date},
            )
            scorecard.append({
                "signal_id":     signal_id,
                "label":         f"Peer read-across: {peer}",
                "status":        "WATCHING",
                "note":          f"{peer} reported on {peer_date}. Awaiting structured read-across analysis.",
                "watch_quarter": None,
                "source":        "peer_read_across",
                "resolved_at":   None,
                "created_at":    _now_iso(),
            })
            queued += 1
        except Exception as exc:
            print(f"  [WARN] peer cross-read queue failed for {target_ticker} vs {peer}: {exc}")
    return queued


# ---------------------------------------------------------------------------
# Supabase change-log upsert (best-effort)
# ---------------------------------------------------------------------------
def _supabase_log(rows: list[dict]) -> None:
    if not rows:
        return
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return
    try:
        import requests
    except ImportError:
        return
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=merge-duplicates",
    }
    try:
        resp = requests.post(
            f"{url}/rest/v1/{TABLE_CHANGE_LOG}?on_conflict=ticker,signal_id,source_url",
            headers=headers, data=json.dumps(rows), timeout=20,
        )
        if resp.status_code >= 300:
            print(f"  [WARN] Supabase change-log upsert HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        print(f"  [WARN] Supabase change-log upsert failed: {exc}")


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------
def run(
    dry_run: bool = False,
    single_ticker: str | None = None,
    skip_news: bool = False,
    skip_peers: bool = False,
) -> dict[str, Any]:
    if not EARNINGS_INTEL_PATH.exists():
        print(f"[note_diff_injector] [ERROR] {EARNINGS_INTEL_PATH} not found")
        return {"error": "earnings_intel_missing"}

    intel = read_json(EARNINGS_INTEL_PATH) or {}
    calendar = read_json(EARNINGS_CALENDAR) or {}

    pre_tickers: list[str] = []
    for ticker, payload in (intel.get("tickers") or {}).items():
        if payload.get("state") == "pre_earnings":
            pre_tickers.append(ticker)
    if single_ticker:
        pre_tickers = [t for t in pre_tickers if t == single_ticker]

    summary: dict[str, Any] = {
        "pre_tickers":            len(pre_tickers),
        "signal_changes":         0,
        "drift_signals_injected": 0,
        "peer_signals_queued":    0,
        "articles_classified":    0,
        "dry_run":                dry_run,
    }
    print(f"[note_diff_injector] {len(pre_tickers)} pre-earnings tickers under review "
          f"(dry_run={dry_run})")

    # Step A: inject estimate-drift signals first.
    summary["drift_signals_injected"] = _consume_drift_signals(intel, dry_run)

    common_names = load_common_names()
    peers_recent = _recent_peer_prints(calendar) if not skip_peers else {}
    change_log_rows: list[dict] = []

    for ticker in pre_tickers:
        payload = intel["tickers"][ticker]
        scorecard = payload.setdefault("signal_scorecard", [])
        signal_changes = payload.setdefault("signal_changes", [])
        already_logged: set[tuple[str, str]] = {
            (sc.get("signal_id") or "", sc.get("source_url") or "")
            for sc in signal_changes
        }

        # Step B: news -> signal updates.
        if not skip_news and scorecard:
            articles = _fetch_news([ticker])
            recent_articles = [a for a in articles if _is_recent(a.get("pubDate"), NEWS_LOOKBACK_DAYS)][:10]
            if recent_articles:
                classifications = _classify_articles(ticker, common_names.get(ticker, ticker), recent_articles)
                summary["articles_classified"] += len(classifications)
                for art, tag in zip(recent_articles, classifications):
                    if not isinstance(tag, dict):
                        continue
                    if tag.get("priority") != "High":
                        continue
                    if tag.get("duplicate"):
                        continue
                    blurb = tag.get("blurb") or ""
                    direction = tag.get("direction") or "Neutral"
                    matched = _match_signal(f"{art.get('title','')} {blurb}", scorecard)
                    if not matched:
                        continue
                    sig_id = matched.get("signal_id") or matched.get("label") or ""
                    src_url = art.get("url") or ""
                    if (sig_id, src_url) in already_logged:
                        continue
                    new_status = _new_status(direction, matched.get("status", "WATCHING"))
                    old_status = matched.get("status")
                    matched["status"] = new_status
                    if new_status != "WATCHING":
                        matched["resolved_at"] = matched.get("resolved_at") or _now_iso()
                    change = {
                        "signal_id":    sig_id,
                        "old_status":   old_status,
                        "new_status":   new_status,
                        "headline":     art.get("title", ""),
                        "source":       art.get("source", ""),
                        "source_url":   src_url,
                        "direction":    direction,
                        "blurb":        blurb,
                        "catalyst_tag": tag.get("catalyst_tag", ""),
                        "changed_at":   _now_iso(),
                    }
                    signal_changes.append(change)
                    already_logged.add((sig_id, src_url))
                    summary["signal_changes"] += 1
                    change_log_rows.append({
                        "ticker":      ticker,
                        "signal_id":   sig_id,
                        "old_status":  old_status,
                        "new_status":  new_status,
                        "source_url":  src_url,
                        "headline":    art.get("title", ""),
                        "direction":   direction,
                        "catalyst_tag": tag.get("catalyst_tag", ""),
                        "blurb":       blurb,
                        "changed_at":  change["changed_at"],
                    })

        # Step C: peer read-across.
        if not skip_peers and peers_recent:
            summary["peer_signals_queued"] += _queue_peer_read_across(ticker, payload, peers_recent)

        # Step D: refresh debate_score quick counters
        unresolved = sum(1 for s in scorecard if s.get("status") == "WATCHING")
        debate = payload.setdefault("debate_score", {})
        debate["unresolved_signals"] = unresolved
        debate["total_signals"]      = len(scorecard)
        debate["as_of"]              = _now_iso()

    intel["last_updated"] = _now_iso()

    if dry_run:
        print(f"[note_diff_injector] [DRY] would write {summary['signal_changes']} signal changes")
    else:
        write_json(EARNINGS_INTEL_PATH, intel)
        _supabase_log(change_log_rows)

    print(
        f"[note_diff_injector] Done — "
        f"signal_changes={summary['signal_changes']} "
        f"drift_signals_injected={summary['drift_signals_injected']} "
        f"peer_signals_queued={summary['peer_signals_queued']} "
        f"articles_classified={summary['articles_classified']}"
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Inject news + estimate + peer signal updates into earnings_intel.json.")
    p.add_argument("--dry-run",   action="store_true")
    p.add_argument("--ticker",    type=str, default=None, help="Restrict to a single ticker.")
    p.add_argument("--skip-news", action="store_true", help="Skip the news classification step.")
    p.add_argument("--skip-peers", action="store_true", help="Skip the peer cross-read step.")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(
        dry_run=args.dry_run,
        single_ticker=(args.ticker.upper() if args.ticker else None),
        skip_news=args.skip_news,
        skip_peers=args.skip_peers,
    )
