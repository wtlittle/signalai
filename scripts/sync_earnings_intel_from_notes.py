"""
Sync earnings_intel.json from the markdown notes in notes/{pre,post}_earnings/.

This is the durable bridge between the cron-generated markdown notes
(automation.jobs.{pre,post}_earnings_notes) and the structured intel JSON the
dashboard's Earnings Intel tab reads.

WHAT IT DOES
  For every ticker in earnings_notes_index.json (active_pre_earnings or
  active_post_earnings), this script reads the corresponding markdown note
  and extracts:

    * company_name, state, inflection_status
    * bottom_line (Headline / Set-up / Thesis Impact)
    * bull_case   (Scenario Grid Bull row + Key Debates upside angles)
    * base_case   (Scenario Grid Base row)
    * bear_case   (Scenario Grid Bear row + Key Debates downside angles)
    * signal_scorecard (one WATCHING signal per Key Debate / What Matters bullet)
    * source_metadata.legacy_note_path / primary_sources
    * For POST notes: post_earnings_review block with takeaways from
      Thesis Impact + Near-Term Outlook + Surprises sections.

  Existing rich content is preserved when present \u2014 the merge step only
  *upgrades* a record (fills empty fields) and refreshes the header. It will
  NEVER blank-out a populated bull_case or signal_scorecard. Use
  --force-rebuild to override and re-extract from scratch.

IDEMPOTENT \u2014 safe to run on every cron tick.

Usage:
  python3 scripts/sync_earnings_intel_from_notes.py
  python3 scripts/sync_earnings_intel_from_notes.py --dry-run
  python3 scripts/sync_earnings_intel_from_notes.py --force-rebuild
  python3 scripts/sync_earnings_intel_from_notes.py --force-rebuild --ticker XYZ
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "earnings_notes_index.json"
INTEL_PATH = ROOT / "earnings_intel.json"
PRE_DIR = ROOT / "notes" / "pre_earnings"
POST_DIR = ROOT / "notes" / "post_earnings"

NOW_ISO = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _read_note(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _extract_section(md: str, *headers: str) -> str | None:
    """Return the body of the first matching `## <header>` section, trimmed.

    Stops at the next `## ` heading, the `---` rule preceding the sources
    block, the `*Sources:*` marker itself, or end of document. This prevents
    the Sources URL list from leaking into the last section on the page.
    """
    for header in headers:
        pattern = (
            rf"^##\s+{re.escape(header)}\s*\n"
            rf"(.*?)(?=^##\s|^---\s*$|^\*Sources:\*|\Z)"
        )
        m = re.search(pattern, md, flags=re.MULTILINE | re.DOTALL)
        if m:
            body = m.group(1).strip()
            if body:
                return body
    return None


def _strip_md(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    return text


def _first_paragraph(text: str, max_chars: int = 500) -> str:
    if not text:
        return ""
    for para in re.split(r"\n\s*\n", text):
        clean = _strip_md(para).strip()
        if clean and not clean.startswith("|") and not clean.startswith("#"):
            clean = re.sub(r"\s+", " ", clean)
            if len(clean) <= max_chars:
                return clean
            return clean[:max_chars].rsplit(" ", 1)[0] + "\u2026"
    return ""


def _bullets(text: str | None, max_items: int = 5) -> list[str]:
    """Pull dash/bullet/numbered list items from a block, stripped clean."""
    if not text:
        return []
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r"^(?:[-*\u2022]|\d+\.)\s+(.+)$", line)
        if not m:
            continue
        bullet = _strip_md(m.group(1)).strip()
        # Drop the leading "Label:" prefix some bullets carry while keeping the body.
        bullet = re.sub(r"^([A-Z][A-Za-z0-9 /'\-&]{2,60}):\s+", "", bullet, count=1)
        bullet = re.sub(r"\s+", " ", bullet)
        if len(bullet) >= 8:
            out.append(bullet[:320])
        if len(out) >= max_items:
            break
    return out


def _extract_scenario_row(md: str, scenario: str) -> dict | None:
    """Parse the Scenario Grid table and return the row keyed by Bull/Base/Bear."""
    grid = _extract_section(md, "Scenario Grid")
    if not grid:
        return None
    target = scenario.strip().lower()
    for line in grid.split("\n"):
        line = line.strip()
        if not line.startswith("|") or line.startswith("|--") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = _strip_md(cells[0]).strip().lower()
        if first.startswith(target):
            return {
                "scenario": cells[0] if len(cells) >= 1 else scenario,
                "probability": cells[1] if len(cells) >= 2 else "",
                "trigger": _strip_md(cells[2]) if len(cells) >= 3 else "",
                "stock_move": cells[3] if len(cells) >= 4 else "",
            }
    return None


def _extract_urls(md: str, max_urls: int = 6) -> list[dict]:
    """Pull source URLs from markdown links + bare URL lines in the Sources block."""
    seen: set[str] = set()
    out: list[dict] = []
    # First the structured Sources section (lines after "*Sources:*")
    src_block = ""
    m = re.search(r"\*Sources:\*\s*(.*)$", md, flags=re.DOTALL)
    if m:
        src_block = m.group(1)
    for src in (src_block, md):
        for mm in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", src):
            label, url = mm.group(1).strip(), mm.group(2).strip()
            if url in seen:
                continue
            seen.add(url)
            out.append({"label": label[:80], "url": url})
            if len(out) >= max_urls:
                return out
        for mm in re.finditer(r"^\s*-\s+(https?://\S+)", src, flags=re.MULTILINE):
            url = mm.group(1).strip().rstrip(",.)]")
            if url in seen:
                continue
            seen.add(url)
            domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            out.append({"label": domain[:80], "url": url})
            if len(out) >= max_urls:
                return out
    return out


def _detect_stock_reaction_pct(md: str) -> float | None:
    """Find the first explicit % stock move mentioned in the note."""
    candidates = re.findall(
        r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:pop|move|drop|gain|loss|reaction)?",
        md,
    )
    for cand in candidates[:5]:
        try:
            val = float(cand)
            if -50.0 <= val <= 50.0:
                return val
        except ValueError:
            continue
    return None


def _split_sentences(text: str, max_items: int = 4) -> list[str]:
    """Split a paragraph into sentence-like chunks on `; ` or `. ` boundaries."""
    if not text:
        return []
    parts = re.split(r";\s+|(?<=[a-z0-9\)])\.\s+(?=[A-Z])", text)
    out: list[str] = []
    for p in parts:
        p = p.strip().rstrip(".;")
        if len(p) >= 12:
            out.append(p[:280])
        if len(out) >= max_items:
            break
    return out


def _signal_id(label: str, idx: int) -> str:
    sid = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")[:48]
    return sid or f"signal_{idx + 1}"


def _short_label(text: str, words: int = 6) -> str:
    parts = re.sub(r"\s+", " ", text).split(" ")
    label = " ".join(parts[:words]).rstrip(".,:;\u2014-")
    return label[:80] if label else "Signal"


# ---------------------------------------------------------------------------
# Per-note extractors
# ---------------------------------------------------------------------------

def _state_and_inflection(kind: str) -> tuple[str, str]:
    if kind == "post":
        return ("post_earnings", "POST")
    return ("pre_earnings", "PRE")


def build_pre_intel(ticker: str, entry: dict, note_path: Path) -> dict:
    md = _read_note(note_path)
    earnings_date = entry.get("date") or ""
    company = _extract_company(md, entry.get("company") or ticker)
    state, inflection = _state_and_inflection("pre")

    setup_body = _extract_section(md, "Set-up", "Setup")
    bottom_line = _first_paragraph(setup_body or md, max_chars=650) or \
        f"{ticker} approaches its {earnings_date} print \u2014 see the latest note for the full setup."

    debates_body = _extract_section(md, "Key Debates & Variant Perception",
                                    "Key Debates", "Debates", "What Matters")
    what_matters_body = _extract_section(md, "What Matters This Print",
                                         "What Matters", "Key Watchpoints")
    debate_bullets = _bullets(debates_body, max_items=4)
    what_matters_bullets = _bullets(what_matters_body, max_items=4)

    bull_row = _extract_scenario_row(md, "Bull")
    base_row = _extract_scenario_row(md, "Base")
    bear_row = _extract_scenario_row(md, "Bear")

    def _row_text(row: dict | None, fallback: str) -> str:
        if not row:
            return fallback
        return _strip_md(row.get("trigger") or "").strip() or fallback

    bull_case = {
        "thesis_headline": _row_text(bull_row, f"Clean beat + raised guide re-rates {ticker}."),
        "pattern": "",
        "pushes_higher": debate_bullets[:3] or [
            "In-line-to-better print on revenue/EPS",
            "Guidance maintained or raised",
            "No new overhangs disclosed",
        ],
        "pushes_lower": [],
        "probability": (bull_row or {}).get("probability", ""),
        "stock_move": (bull_row or {}).get("stock_move", ""),
    }
    base_case = {
        "setup_headline": _row_text(base_row, "In-line print; guidance and narrative intact."),
        "pushes_higher": what_matters_bullets[:2] or debate_bullets[1:3],
        "pushes_lower": what_matters_bullets[2:4] or debate_bullets[2:4],
        "probability": (base_row or {}).get("probability", ""),
        "stock_move": (base_row or {}).get("stock_move", ""),
    }
    bear_case = {
        "thesis_headline": _row_text(bear_row, f"Miss or guide cut compresses {ticker} multiple."),
        "pattern": "",
        "pushes_higher": [],
        "pushes_lower": debate_bullets[-3:] or [
            "Revenue or EPS miss vs consensus",
            "Guidance narrowed or lowered",
            "New overhang or competitive disclosure",
        ],
        "probability": (bear_row or {}).get("probability", ""),
        "stock_move": (bear_row or {}).get("stock_move", ""),
    }

    # Signal scorecard \u2014 a WATCHING entry per Key Debate / What Matters bullet
    scorecard_seed = (debate_bullets + what_matters_bullets)[:4]
    signal_scorecard = []
    for i, bullet in enumerate(scorecard_seed):
        label = _short_label(bullet, words=6)
        signal_scorecard.append({
            "signal_id": _signal_id(label, i),
            "label": label,
            "status": "WATCHING",
            "note": bullet[:260],
            "watch_quarter": f"Q reporting {earnings_date}",
            "resolved_at": None,
        })

    sources = _extract_urls(md, max_urls=6)

    return {
        "ticker": ticker,
        "company_name": company,
        "state": state,
        "inflection_status": inflection,
        "next_earnings_date": earnings_date,
        "intel_updated_at": NOW_ISO,
        "refresh_reason": "synced_from_pre_earnings_note",
        "bottom_line": bottom_line,
        "bull_case": bull_case,
        "base_case": base_case,
        "bear_case": bear_case,
        "signal_scorecard": signal_scorecard,
        "tone_drift": {
            "current_tone": "cautious_constructive",
            "prior_tone": "",
            "tone_notes": _first_paragraph(setup_body or "", max_chars=240),
        },
        "source_metadata": {
            "legacy_note_path": str(note_path.relative_to(ROOT)),
            "primary_sources": sources,
        },
        "previous_bottom_line": None,
        "signal_changes": [],
    }


def build_post_intel(ticker: str, entry: dict, note_path: Path) -> dict:
    md = _read_note(note_path)
    earnings_date = entry.get("date") or ""
    days_since = entry.get("day_post", 0)
    company = _extract_company(md, entry.get("company") or ticker)
    state, inflection = _state_and_inflection("post")

    headline_body = _extract_section(md, "Headline")
    headline_text = _first_paragraph(headline_body or "", max_chars=320)
    bm_quality = ""
    if headline_body:
        m = re.search(r"\*\*Beat/Miss Quality:\*\*\s*(.+)", headline_body)
        if m:
            bm_quality = _strip_md(m.group(1)).strip()

    metrics_body = _extract_section(md, "Key Metrics")
    metrics = _bullets(metrics_body, max_items=6)

    guide_body = _extract_section(md, "Guidance and Tone", "Guidance",
                                  "Tone and Guidance", "Outlook")
    guidance_text = _first_paragraph(guide_body or "", max_chars=320)
    tone_text = ""
    if guide_body:
        m = re.search(r"\*\*Management Tone:\*\*\s*(.+)", guide_body)
        if m:
            tone_text = _strip_md(m.group(1)).strip()

    surprises_body = _extract_section(md, "Surprises / Disappointments",
                                       "Surprises", "Disappointments")
    surprises = _bullets(surprises_body, max_items=5)

    thesis_body = _extract_section(md, "Thesis Impact", "Thesis Update",
                                   "Takeaways", "Bottom Line")
    thesis_text = _first_paragraph(thesis_body or "", max_chars=500)

    analyst_body = _extract_section(md, "Analyst Reactions", "Analyst Changes")
    analyst_bullets = _bullets(analyst_body, max_items=4)

    outlook_body = _extract_section(md, "Near-Term Outlook", "Outlook",
                                    "What to Watch", "Follow-ups", "Open Questions")
    outlook_text = _first_paragraph(outlook_body or "", max_chars=400)
    outlook_bullets = _bullets(outlook_body, max_items=4)

    # Tone classification
    pos_re = re.compile(r"(beat|strong|accelerat|raise[ds]?|above|confirmed|inflect|outperform|momentum|exceeded)", re.IGNORECASE)
    neg_re = re.compile(r"(miss(?:ed)?|fell|declin|disappoint|cut|below|weak|deceler|under-?perform|guide[d]? down)", re.IGNORECASE)
    sentiment_blob = " ".join(filter(None, [headline_text, bm_quality, thesis_text, guidance_text, tone_text]))
    pos_hit = bool(pos_re.search(sentiment_blob))
    neg_hit = bool(neg_re.search(sentiment_blob))

    # Bottom line \u2014 prefer Headline, then Thesis Impact, then Beat/Miss Quality
    bottom_line = headline_text or thesis_text or bm_quality or \
        f"{ticker} reported {earnings_date} \u2014 see the post-earnings note for details."

    # Bull / Base / Bear from observed sentiment + key metric bullets
    upside_bullets = [b for b in (metrics + surprises) if pos_re.search(b)][:3]
    downside_bullets = [b for b in (metrics + surprises) if neg_re.search(b)][:3]
    if not upside_bullets:
        upside_bullets = metrics[:2]
    if not downside_bullets:
        downside_bullets = surprises[:2]

    bull_case = {
        "thesis_headline": thesis_text if pos_hit and not neg_hit else
            f"Bull path requires repeat execution next print.",
        "pattern": "",
        "pushes_higher": upside_bullets or metrics[:2],
        "pushes_lower": [],
    }
    base_case = {
        "setup_headline": "Thesis intact post-print; watch next print for signal confirmation.",
        "pushes_higher": outlook_bullets[:2],
        "pushes_lower": outlook_bullets[2:4],
    }
    bear_case = {
        "thesis_headline": thesis_text if neg_hit and not pos_hit else
            f"Bear path requires execution slip or macro shock.",
        "pattern": "",
        "pushes_higher": [],
        "pushes_lower": downside_bullets or surprises[:2],
    }

    # Signal scorecard \u2014 one resolved signal for the headline result + one for
    # guidance, plus WATCHING signals for each open outlook bullet.
    signal_scorecard: list[dict] = []
    if headline_text:
        status = "FAILED" if neg_hit and not pos_hit else "CONFIRMED" if pos_hit and not neg_hit else "WATCHING"
        signal_scorecard.append({
            "signal_id": "headline_results",
            "label": "Headline Results",
            "status": status,
            "note": (bm_quality or headline_text)[:260],
            "watch_quarter": f"Q reported {earnings_date}",
            "resolved_at": NOW_ISO if status != "WATCHING" else None,
        })
    if guidance_text or tone_text:
        gtxt = (guidance_text or "") + (" " + tone_text if tone_text else "")
        status = "FAILED" if neg_re.search(gtxt) and not pos_re.search(gtxt) else \
                 "CONFIRMED" if pos_re.search(gtxt) and not neg_re.search(gtxt) else "WATCHING"
        signal_scorecard.append({
            "signal_id": "guidance_trajectory",
            "label": "Guidance Trajectory",
            "status": status,
            "note": (tone_text or guidance_text)[:260],
            "watch_quarter": f"Q reported {earnings_date}",
            "resolved_at": NOW_ISO if status != "WATCHING" else None,
        })
    for i, bullet in enumerate(outlook_bullets[:2]):
        label = _short_label(bullet, words=6)
        signal_scorecard.append({
            "signal_id": _signal_id(f"watch_{label}", i),
            "label": label,
            "status": "WATCHING",
            "note": bullet[:260],
            "watch_quarter": "Next print",
            "resolved_at": None,
        })

    stock_pct = _detect_stock_reaction_pct(outlook_body or md or "")
    sources = _extract_urls(md, max_urls=6)

    # Next earnings \u2014 approximate +90d unless we already know it.
    try:
        ed = datetime.strptime(earnings_date, "%Y-%m-%d").date()
        next_ed = (ed + timedelta(days=90)).isoformat()
        visible_until = (ed + timedelta(days=14)).isoformat()
    except Exception:
        next_ed = None
        visible_until = None

    return {
        "ticker": ticker,
        "company_name": company,
        "state": state,
        "inflection_status": inflection,
        "last_earnings_date": earnings_date,
        "next_earnings_date": next_ed,
        "intel_updated_at": NOW_ISO,
        "refresh_reason": "synced_from_post_earnings_note",
        "bottom_line": bottom_line,
        "beat_miss_quality": bm_quality,
        "bull_case": bull_case,
        "base_case": base_case,
        "bear_case": bear_case,
        "signal_scorecard": signal_scorecard,
        "key_metrics": metrics,
        "surprises": surprises,
        "analyst_reactions": analyst_bullets,
        "guidance_text": guidance_text,
        "tone_drift": {
            "current_tone": "constructive" if pos_hit and not neg_hit else
                            ("cautious" if neg_hit and not pos_hit else "neutral"),
            "prior_tone": "",
            "tone_notes": tone_text or guidance_text[:240],
        },
        "post_earnings_review": {
            "active": True,
            "earnings_date": earnings_date,
            "days_since": days_since,
            "visible_until": visible_until,
            "takeaways_headline": thesis_text[:200] if thesis_text else
                f"{ticker} {earnings_date} quarter \u2014 see takeaways below.",
            "takeaways_bullets": (
                _bullets(thesis_body, max_items=4)
                or _split_sentences(thesis_text, max_items=4)
                or outlook_bullets[:4]
                or _split_sentences(outlook_text, max_items=4)
                or surprises[:4]
            )[:4],
            "what_happened_headline": headline_text[:200] if headline_text else
                f"Quarter reported {earnings_date}.",
            "what_happened_bullets": metrics[:5],
            "stock_reaction_pct": stock_pct,
        },
        "source_metadata": {
            "legacy_note_path": str(note_path.relative_to(ROOT)),
            "primary_sources": sources,
        },
        "previous_bottom_line": None,
        "signal_changes": [],
    }


def _extract_company(md: str, fallback: str) -> str:
    """First line is `# Company (TICKER) \u2014 Pre/Post-Earnings Note`."""
    m = re.match(r"^#\s+(.+?)\s+\([^)]+\)", md)
    return m.group(1).strip() if m else fallback


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

REFRESH_ALWAYS = {
    "intel_updated_at",
    "state",
    "inflection_status",
    "next_earnings_date",
    "last_earnings_date",
    "refresh_reason",
}

# Rich fields the sync now produces. If an existing record has empty values
# for these, we upgrade them. If it already has rich content, we keep it
# untouched (unless --force-rebuild is passed).
RICH_FIELDS = {
    "bottom_line",
    "bull_case",
    "base_case",
    "bear_case",
    "signal_scorecard",
    "source_metadata",
    "tone_drift",
    "post_earnings_review",
    "beat_miss_quality",
    "key_metrics",
    "surprises",
    "analyst_reactions",
    "guidance_text",
}


def _is_empty(value) -> bool:
    if value is None or value == "" or value == []:
        return True
    if isinstance(value, dict):
        # A bull_case shell with only empty pushes_higher/pushes_lower is empty.
        meaningful = {k: v for k, v in value.items()
                      if k not in ("pattern", "probability", "stock_move", "thesis_headline", "setup_headline")}
        return all(_is_empty(v) for v in meaningful.values())
    return False


def _has_real_content(existing: dict, key: str) -> bool:
    """A field has 'real content' when it's non-empty and not just a stub shell."""
    if key not in existing:
        return False
    val = existing[key]
    if _is_empty(val):
        return False
    if key in {"bull_case", "bear_case"}:
        ph = val.get("pushes_higher") or []
        pl = val.get("pushes_lower") or []
        return bool(ph or pl)
    if key == "base_case":
        return bool((val.get("pushes_higher") or []) or (val.get("pushes_lower") or []))
    if key == "signal_scorecard":
        return bool(val)
    if key == "post_earnings_review":
        return bool((val or {}).get("takeaways_bullets") or (val or {}).get("what_happened_bullets"))
    return True


def merge_intel(existing: dict, fresh: dict, force_rebuild: bool = False) -> dict:
    """Combine existing record with freshly-extracted record.

    Without force_rebuild: fill empty rich fields, refresh header/dates, never
    overwrite rich content already present.
    With force_rebuild: take fresh verbatim, but keep schema-only fields that
    aren't part of the extractor's output (e.g. theme_lifecycle, debate_intensity).
    """
    if force_rebuild:
        merged = dict(fresh)
        # Preserve auxiliary fields the extractor doesn't touch.
        for key in ("theme_lifecycle", "inflection_library", "guidance_profile",
                    "debate_intensity", "debate_score", "previous_bottom_line"):
            if key in existing:
                merged.setdefault(key, existing[key])
        # Preserve any signal_changes log (signal history is append-only).
        merged["signal_changes"] = existing.get("signal_changes") or []
        return merged

    merged = dict(existing)
    for key, value in fresh.items():
        if key in REFRESH_ALWAYS:
            merged[key] = value
            continue
        if key in RICH_FIELDS:
            if not _has_real_content(existing, key):
                merged[key] = value
            elif key == "source_metadata" and isinstance(merged.get(key), dict):
                # Always keep legacy_note_path current; merge missing primary_sources.
                src = merged[key]
                src["legacy_note_path"] = value.get("legacy_note_path", src.get("legacy_note_path"))
                if not src.get("primary_sources"):
                    src["primary_sources"] = value.get("primary_sources", [])
            continue
        # Unknown / aux field \u2014 fill only if missing.
        if key not in merged or _is_empty(merged.get(key)):
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def iter_note_entries(index: dict, only_ticker: str | None) -> Iterable[tuple[str, dict, Path]]:
    for kind, key in (("pre", "active_pre_earnings"), ("post", "active_post_earnings")):
        for entry in index.get(key, []):
            ticker = entry.get("ticker")
            if not ticker:
                continue
            if only_ticker and ticker != only_ticker:
                continue
            note_rel = entry.get("file") or entry.get("note_file")
            if not note_rel:
                continue
            note_path = ROOT / note_rel
            if not note_path.exists():
                continue
            yield kind, entry, note_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show changes without writing")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="Re-extract rich fields from notes even when existing records already have content")
    ap.add_argument("--ticker", help="Limit to a single ticker")
    args = ap.parse_args()

    if not INDEX_PATH.exists():
        print(f"[ERR] {INDEX_PATH} not found \u2014 run scripts/reindex_earnings_notes.py first")
        return

    index = json.loads(INDEX_PATH.read_text())
    intel = json.loads(INTEL_PATH.read_text()) if INTEL_PATH.exists() else {
        "last_updated": NOW_ISO,
        "schema_version": "1.0",
        "tickers": {},
    }
    tickers = intel.setdefault("tickers", {})

    seeded, refreshed, upgraded, skipped = 0, 0, 0, 0
    seeded_t, upgraded_t, refreshed_t = [], [], []

    for kind, entry, note_path in iter_note_entries(index, args.ticker):
        ticker = entry["ticker"]
        if kind == "pre":
            fresh = build_pre_intel(ticker, entry, note_path)
        else:
            fresh = build_post_intel(ticker, entry, note_path)

        if ticker not in tickers:
            tickers[ticker] = fresh
            seeded += 1
            seeded_t.append(ticker)
            continue

        existing = tickers[ticker]
        had_rich = any(_has_real_content(existing, k) for k in
                       ("bull_case", "bear_case", "signal_scorecard"))
        before = json.dumps(existing, sort_keys=True)
        merged = merge_intel(existing, fresh, force_rebuild=args.force_rebuild)
        after = json.dumps(merged, sort_keys=True)

        if before == after:
            skipped += 1
            continue

        tickers[ticker] = merged
        if not had_rich and any(_has_real_content(merged, k) for k in
                                ("bull_case", "bear_case", "signal_scorecard")):
            upgraded += 1
            upgraded_t.append(ticker)
        else:
            refreshed += 1
            refreshed_t.append(ticker)

    intel["last_updated"] = NOW_ISO

    msg = (f"seeded={seeded} upgraded={upgraded} refreshed={refreshed} skipped={skipped}"
           + (" (force-rebuild)" if args.force_rebuild else ""))
    if args.dry_run:
        print(f"[DRY RUN] {msg}")
    else:
        INTEL_PATH.write_text(json.dumps(intel, indent=2))
        print(f"[OK] earnings_intel.json synced \u2014 {msg}")
    if seeded_t:
        print(f"  seeded:    {', '.join(sorted(seeded_t))}")
    if upgraded_t:
        print(f"  upgraded:  {', '.join(sorted(upgraded_t))}")
    if refreshed_t and len(refreshed_t) <= 20:
        print(f"  refreshed: {', '.join(sorted(refreshed_t))}")
    elif refreshed_t:
        print(f"  refreshed: {len(refreshed_t)} tickers")


if __name__ == "__main__":
    main()
