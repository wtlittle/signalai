#!/usr/bin/env python3
"""
compute_debate_scores.py — Backfill earnings_intel.json schema and compute the
per-ticker / universe Debate Intensity (Contested Velocity) score.

Schema changes applied in place:
  1. signal_scorecard[*].resolved_at: null     (added if missing)
  2. bull_case.pushes_higher / bear_case.pushes_lower:
        plain string  ->  { "signal_id": "<id>" | null, "text": "<original>" }
     signal_id is the best fuzzy match (Jaccard on word sets, lowercased,
     stopwords removed) against signal_scorecard[].note. Threshold 0.30.

Per-ticker Contested Velocity Score:
    conflict_ratio       = cross_cited_signals / total_signals
    resolution_velocity  = resolved_with_timestamp / total_signals  (=0 when
                            all resolved_at are null, i.e. backfilled)
    score                = conflict_ratio * (1 - resolution_velocity)
    If total_signals == 0:     ticker excluded from universe average.
    If conflict_ratio == 0 and resolution_velocity == 0: score = 0.

Universe debate score = mean(per-ticker scores) * 100, rounded to int.

Writes:
    tickers[T].debate_score = {
        value, conflict_ratio, resolution_velocity,
        cross_cited_signals, unresolved_signals, total_signals, as_of
    }
    plus top-level "debate_score_universe" object.

Usage:
    python3 compute_debate_scores.py [path/to/earnings_intel.json]
"""

from __future__ import annotations
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(REPO_ROOT, "earnings_intel.json")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "is",
    "are", "was", "were", "be", "been", "being", "with", "as", "by", "from",
    "this", "that", "these", "those", "it", "its", "they", "them", "their",
    "we", "our", "us", "i", "you", "your", "he", "she", "his", "her",
    "do", "does", "did", "doing", "have", "has", "had", "having",
    "but", "if", "then", "than", "so", "not", "no", "nor", "can", "could",
    "would", "should", "may", "might", "will", "shall", "must",
    "into", "out", "up", "down", "over", "under", "about", "between",
    "after", "before", "while", "during", "through", "across", "via",
}

WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")


def _tokens(s: str) -> set[str]:
    if not s:
        return set()
    return {w for w in WORD_RE.findall(s.lower()) if w not in STOPWORDS and len(w) > 1}


def _slug(label: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return base[:60] or "signal"


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def best_match(text: str, scorecard: list[dict[str, Any]], threshold: float = 0.30) -> tuple[str | None, float]:
    """Return (signal_id, confidence) for best fuzzy match, or (None, 0.0)."""
    if not text or not scorecard:
        return None, 0.0
    text_tokens = _tokens(text)
    if not text_tokens:
        return None, 0.0
    best_id, best_score = None, 0.0
    for sig in scorecard:
        note_tokens = _tokens(sig.get("note") or "")
        # Also include label tokens as fallback signal
        label_tokens = _tokens(sig.get("label") or "")
        sig_tokens = note_tokens | label_tokens
        score = jaccard(text_tokens, sig_tokens)
        if score > best_score:
            best_score = score
            best_id = sig.get("signal_id")
    if best_score >= threshold:
        return best_id, best_score
    return None, best_score


def ensure_signal_ids(scorecard: list[dict[str, Any]]) -> None:
    """Make sure every signal has a signal_id; synthesize from label if missing."""
    seen: set[str] = set()
    for sig in scorecard:
        sid = sig.get("signal_id")
        if not sid:
            sid = _slug(sig.get("label") or sig.get("note", "")[:40] or "signal")
        # uniquify if collisions
        base = sid
        i = 2
        while sid in seen:
            sid = f"{base}_{i}"
            i += 1
        sig["signal_id"] = sid
        seen.add(sid)


def convert_pushes(items: list[Any], scorecard: list[dict[str, Any]],
                   side: str, ticker: str) -> tuple[list[dict[str, Any]], list[tuple[str, str | None, float]]]:
    """Convert a list of push strings/dicts into [{signal_id, text}, ...].
    Returns (new_list, audit_rows)."""
    new_list: list[dict[str, Any]] = []
    audit: list[tuple[str, str | None, float]] = []
    for item in items or []:
        if isinstance(item, dict):
            text = item.get("text") or ""
            sid = item.get("signal_id", None)
            # If signal_id missing, try to backfill via match
            if sid is None and text:
                sid, conf = best_match(text, scorecard)
                audit.append((text[:60], sid, conf))
            new_list.append({"signal_id": sid, "text": text})
        elif isinstance(item, str):
            sid, conf = best_match(item, scorecard)
            audit.append((item[:60], sid, conf))
            new_list.append({"signal_id": sid, "text": item})
        else:
            # Unknown shape — preserve as text-only
            new_list.append({"signal_id": None, "text": str(item)})
    return new_list, audit


def compute_for_ticker(rec: dict[str, Any]) -> dict[str, Any] | None:
    sc = rec.get("signal_scorecard") or []
    if not sc:
        return None

    bull_pushes = ((rec.get("bull_case") or {}).get("pushes_higher")) or []
    bear_pushes = ((rec.get("bear_case") or {}).get("pushes_lower")) or []

    bull_ids = {p.get("signal_id") for p in bull_pushes if isinstance(p, dict) and p.get("signal_id")}
    bear_ids = {p.get("signal_id") for p in bear_pushes if isinstance(p, dict) and p.get("signal_id")}

    total = len(sc)
    cross = sum(1 for s in sc if s.get("signal_id") in bull_ids and s.get("signal_id") in bear_ids)
    resolved = sum(
        1 for s in sc
        if (s.get("status") or "").upper() != "WATCHING" and s.get("resolved_at")
    )
    unresolved = total - resolved

    conflict_ratio = cross / total if total else 0.0
    resolution_velocity = resolved / total if total else 0.0

    if conflict_ratio == 0 and resolution_velocity == 0:
        score = 0.0
    else:
        score = conflict_ratio * (1.0 - resolution_velocity)

    return {
        "value": int(round(score * 100)),
        "conflict_ratio": round(conflict_ratio, 4),
        "resolution_velocity": round(resolution_velocity, 4),
        "cross_cited_signals": cross,
        "unresolved_signals": unresolved,
        "total_signals": total,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def process(intel_path: str = DEFAULT_PATH, *, verbose: bool = True) -> dict[str, Any]:
    with open(intel_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tickers = data.get("tickers") or {}
    n_total = len(tickers)
    n_with_sc = 0
    n_with_cross = 0
    universe_scores: list[float] = []

    for ticker, rec in tickers.items():
        sc = rec.get("signal_scorecard") or []
        # Pass 1: backfill resolved_at and signal_ids
        for sig in sc:
            if "resolved_at" not in sig:
                sig["resolved_at"] = None
        ensure_signal_ids(sc)

        # Pass 2: convert pushes_higher / pushes_lower
        if rec.get("bull_case") is not None:
            bh = rec["bull_case"].get("pushes_higher") or []
            new_bh, audit_bh = convert_pushes(bh, sc, "bull", ticker)
            rec["bull_case"]["pushes_higher"] = new_bh
            if verbose and audit_bh:
                for text, sid, conf in audit_bh:
                    tag = sid or "(no-match)"
                    print(f"  [{ticker}] BULL  {tag:<45} conf={conf:.2f}  \"{text}\"")
        else:
            audit_bh = []

        if rec.get("bear_case") is not None:
            bl = rec["bear_case"].get("pushes_lower") or []
            new_bl, audit_bl = convert_pushes(bl, sc, "bear", ticker)
            rec["bear_case"]["pushes_lower"] = new_bl
            if verbose and audit_bl:
                for text, sid, conf in audit_bl:
                    tag = sid or "(no-match)"
                    print(f"  [{ticker}] BEAR  {tag:<45} conf={conf:.2f}  \"{text}\"")
        else:
            audit_bl = []

        if sc:
            n_with_sc += 1

        # Pass 3: compute debate_score
        score = compute_for_ticker(rec)
        if score is not None:
            rec["debate_score"] = score
            if score["cross_cited_signals"] > 0:
                n_with_cross += 1
            universe_scores.append(score["value"] / 100.0)

    # Top-level universe summary (informational; client computes its own filtered)
    if universe_scores:
        uni_value = int(round(sum(universe_scores) / len(universe_scores) * 100))
        data["debate_score_universe"] = {
            "value": uni_value,
            "n_tickers": len(universe_scores),
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    else:
        data["debate_score_universe"] = {
            "value": None, "n_tickers": 0,
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    with open(intel_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nProcessed {n_total} tickers, {n_with_sc} had scorecards, "
          f"{n_with_cross} had cross-cited signals.")
    if universe_scores:
        print(f"Universe debate score: {data['debate_score_universe']['value']} "
              f"(across {len(universe_scores)} tickers).")
    return data


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    process(path, verbose=True)
