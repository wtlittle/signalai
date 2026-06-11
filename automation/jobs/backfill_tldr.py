#!/usr/bin/env python3
"""Deterministic tl_dr backfill for weekly briefing archive JSON files.

Reads an archive JSON, derives 3-5 explicit tl_dr bullets from available
signals (index_returns, trends, risks, picks, narrative) — no LLM needed —
and writes the tl_dr field back to the file.

This removes client-side dependence on synthesizeTldr for archived weeks.

Usage:
    python -m automation.jobs.backfill_tldr archive/briefings/weekly_briefing_2026-05-31.json
    python -m automation.jobs.backfill_tldr archive/briefings/weekly_briefing_2026-06-07.json

    # Run on ALL archive files (idempotent — skips if tl_dr already populated):
    python -m automation.jobs.backfill_tldr --all

Pipeline integration (called automatically from weekly_briefing.py):
    from automation.jobs.backfill_tldr import inject_tl_dr
    briefing = inject_tl_dr(briefing)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _fmt_pct(raw) -> str | None:
    """Return signed percent string or None if absent/zero."""
    if raw is None or raw == "" or str(raw).upper() in ("N/A", "NONE"):
        return None
    try:
        num = float(str(raw).replace("%", "").replace("+", "").strip())
    except (ValueError, TypeError):
        return None
    if not (-200 < num < 200) or num == 0:
        return None
    return ("+" if num >= 0 else "") + f"{num:.1f}%"


_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--"}


def _clean(raw) -> str:
    """Return a trimmed display string, or '' for missing/sentinel values."""
    if raw is None:
        return ""
    s = str(raw).strip()
    return "" if s.lower() in _MISSING_TOKENS else s


def _narrative_sentences(text: str, limit: int, max_len: int = 150) -> list[str]:
    """Extract up to `limit` distinct substantive sentences from narrative prose.

    Used to top tl_dr up to the 5-bullet target when trends/risks come back
    empty. These are real sentences lifted verbatim from the generated
    narrative — no fabrication.
    """
    if not text:
        return []
    cleaned = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\[\d+\]", "", cleaned)
    cleaned = re.sub(r"[*_`>#]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"[A-Z][^.!?]{40,}[.!?]", cleaned):
        s = m.group(0).strip()
        key = s[:40].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s[:max_len] + "…" if len(s) > max_len else s)
        if len(out) >= limit:
            break
    return out


def _first_sentence(text: str, max_len: int = 130) -> str | None:
    """Extract the first meaningful sentence from markdown/plain text."""
    if not text:
        return None
    # Strip markdown headers and citation footnotes
    cleaned = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\[\d+\]", "", cleaned).strip()
    # Find first sentence that is at least 30 chars (avoids abbreviations like "U.S.")
    m = re.search(r"[A-Z][^.!?]{25,}[.!?]", cleaned)
    if m:
        sentence = m.group(0).strip()
    else:
        # Fallback: take first 200 chars of cleaned text
        sentence = cleaned[:200].strip()
    if len(sentence) < 20:
        return None
    return sentence[:max_len] + "\u2026" if len(sentence) > max_len else sentence


def inject_tl_dr(d: dict, force: bool = False) -> dict:
    """Derive and inject a deterministic tl_dr list into a briefing dict.

    Parameters
    ----------
    d:      Weekly briefing payload (mutated in place).
    force:  If True, overwrite even if tl_dr already exists.

    Returns the (possibly mutated) dict.
    """
    # Skip if already populated (unless forced)
    existing = d.get("tl_dr")
    if not force and existing and (
        (isinstance(existing, list) and len(existing) >= 1)
        or (isinstance(existing, dict) and existing.get("bullets"))
    ):
        return d

    bullets: list[str] = []

    # ── 1. Index return bullet ───────────────────────────────────────────────
    ms = d.get("market_summary") if isinstance(d.get("market_summary"), dict) else {}
    ir = d.get("index_returns") or ms.get("index_returns") or {}

    sp_raw = (
        ir.get("sp500_weekly")
        or ir.get("S&P 500")
        or ms.get("sp500_weekly")
        or (
            _fmt_pct(ir.get("sp500", {}).get("weekly_pct"))
            if isinstance(ir.get("sp500"), dict)
            else None
        )
    )
    sp = _fmt_pct(sp_raw)
    if sp:
        sp_val = float(sp.replace("%", "").replace("+", ""))
        if sp_val >= 1.5:
            bullets.append(
                f"S&P 500 {sp} this week \u2014 risk-on tone with broad breadth."
            )
        elif sp_val <= -1.5:
            bullets.append(
                f"S&P 500 {sp} this week \u2014 defensive tone as risk assets retreat."
            )
        else:
            bullets.append(
                f"S&P 500 {sp} \u2014 mixed tape with leadership rotation beneath the surface."
            )

    # ── 2. Trend bullets (up to 2) ───────────────────────────────────────────
    trends = d.get("trends") or d.get("key_trends") or []
    for t in trends[:2]:
        if not isinstance(t, dict):
            continue
        title = t.get("title") or t.get("name") or t.get("theme") or ""
        if title:
            bullets.append(
                title[:107] + "\u2026" if len(title) > 110 else title
            )

    # ── 3. Risk bullet ───────────────────────────────────────────────────────
    risks = d.get("risks") or []
    for r in risks[:1]:
        if not isinstance(r, dict):
            continue
        title = r.get("title") or r.get("name") or r.get("risk") or ""
        if title:
            bullets.append(
                "Risk: " + (title[:97] + "\u2026" if len(title) > 100 else title)
            )

    # ── 4. Value pick bullet ─────────────────────────────────────────────────
    value_picks = d.get("value_picks") or []
    if value_picks and len(bullets) < 5:
        v = value_picks[0]
        ticker = _clean(v.get("ticker"))
        if ticker:
            off_high = _clean(v.get("pct_off_high") or v.get("off_high_pct"))
            fcf = _clean(v.get("fcf_yield"))
            parts = [f"Value setup: {ticker}"]
            if off_high:
                parts.append(f"{off_high} off its 52-week high")
            if fcf:
                parts.append(f"{fcf} FCF yield")
            bullets.append(
                " — ".join(parts) + "."
                if len(parts) > 1
                else f"{ticker} screens as a value setup."
            )

    # ── 5. Momentum pick bullet ──────────────────────────────────────────────
    momentum_picks = d.get("momentum_picks") or []
    if momentum_picks and len(bullets) < 5:
        m = momentum_picks[0]
        ticker = _clean(m.get("ticker"))
        if ticker:
            w1 = _fmt_pct(
                m.get("one_week_perf") or m.get("perf_1w") or m.get("one_week")
            )
            bullets.append(
                f"Momentum leader: {ticker}{' ' + w1 if w1 else ''} on the week."
            )

    # ── 6. Narrative top-up: pull distinct sentences until we reach 5 bullets.
    #       Trends/risks arrays are frequently empty (sonar burns its budget on
    #       reasoning), which leaves tl_dr short. The narrative is reliably rich,
    #       so lift real sentences from it — no fabrication. ─────────────────────
    if len(bullets) < 5:
        narrative = (
            (d.get("market_summary") if isinstance(d.get("market_summary"), str) else None)
            or ms.get("narrative")
            or d.get("narrative")
            or ""
        )
        for sentence in _narrative_sentences(narrative, limit=5 - len(bullets)):
            if sentence not in bullets:
                bullets.append(sentence)
            if len(bullets) >= 5:
                break

    # ── 7. Last resort for salvaged weeks ────────────────────────────────────
    if not bullets and d.get("_salvaged"):
        bullets.append("Limited data this week \u2014 see narrative below.")

    # Clamp to 5
    bullets = bullets[:5]

    d["tl_dr"] = bullets
    return d


def backfill_file(path: Path, force: bool = False) -> bool:
    """Read a JSON file, inject tl_dr, write back. Returns True if modified."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)

    original_tl_dr = d.get("tl_dr")
    d = inject_tl_dr(d, force=force)

    if d.get("tl_dr") == original_tl_dr and not force:
        print(f"  [skip] {path.name} — tl_dr already set ({original_tl_dr!r:.60})")
        return False

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    bullets = d.get("tl_dr") or []
    print(f"  [ok] {path.name} — wrote {len(bullets)} bullets")
    return True


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]

    if "--all" in argv:
        archive_dir = ROOT / "archive" / "briefings"
        paths = sorted(archive_dir.glob("weekly_briefing_*.json"))
        if not paths:
            print(f"No archive files found in {archive_dir}")
            return
        modified = 0
        for p in paths:
            if backfill_file(p, force=force):
                modified += 1
        print(f"Done — {modified}/{len(paths)} files updated.")
    else:
        # Also handle weekly_briefing.json (current week)
        paths = [ROOT / a if not Path(a).is_absolute() else Path(a) for a in argv if not a.startswith("--")]
        if not paths:
            print("Usage: python -m automation.jobs.backfill_tldr <path> [<path> ...] [--force] [--all]")
            sys.exit(1)
        for p in paths:
            backfill_file(Path(p), force=force)


if __name__ == "__main__":
    main()
