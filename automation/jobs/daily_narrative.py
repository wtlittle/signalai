"""
Daily market narrative — small Perplexity sonar call that writes a 2-sentence
"what happened today" recap into ``macro_data.json#daily_narrative``.

The forward look ("what to watch") is no longer produced here — it is owned by
automation/jobs/today_events.py, which surfaces real scheduled events. This job
now produces only the ``happened`` recap.

Cheap (~$0.004/run): sonar model, ~300 input + 150 output tokens. The renderer
layers this on top of the deterministic mechanical narrative. If this step
fails for any reason (API down, parse error), the renderer falls back to
mechanical-only — no email failure.

Usage:
    python -m automation.jobs.daily_narrative
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.perplexity.client import call_perplexity  # noqa: E402

MACRO_PATH = ROOT / "macro_data.json"


def _fmt_pct(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return f"{'+' if f >= 0 else ''}{f:.1f}%"


def _build_context(macro: dict, snapshot: dict) -> str:
    """Compact context block fed to the sonar prompt — only observed numbers."""
    idx = macro.get("indices") or {}
    factors = macro.get("factors") or {}
    sectors = macro.get("sectors") or {}
    quotes = snapshot.get("quotes") or {}

    parts = []

    # Indices
    for sym, label in (("^GSPC", "S&P 500"), ("^IXIC", "NASDAQ"), ("^RUT", "Russell 2000"), ("^VIX", "VIX")):
        q = idx.get(sym) or {}
        if not q:
            continue
        parts.append(
            f"{label}: 1D {_fmt_pct(q.get('change1d'))}, "
            f"1W {_fmt_pct(q.get('change_1w') or q.get('change1w'))}, "
            f"1M {_fmt_pct(q.get('change_1m') or q.get('change1m'))}"
        )

    # Top sector ETFs by 1D move
    sec_rows = []
    for etf, meta in sectors.items():
        if not isinstance(meta, dict):
            continue
        c = meta.get("change1d")
        try:
            sec_rows.append((float(c), etf, meta.get("sectorName") or meta.get("name") or etf))
        except (TypeError, ValueError):
            continue
    sec_rows.sort(key=lambda x: x[0])
    if sec_rows:
        worst = sec_rows[:3]
        best = sec_rows[-3:][::-1]
        parts.append("Sector ETFs (worst 1D): " + ", ".join(f"{lbl} {_fmt_pct(c)}" for c, _, lbl in worst))
        parts.append("Sector ETFs (best 1D): " + ", ".join(f"{lbl} {_fmt_pct(c)}" for c, _, lbl in best))

    # Top movers (watchlist) — 5 worst, 5 best by change1d
    movers = []
    for sym, q in quotes.items():
        if sym in {"SPY", "QQQ", "DIA", "IWM", "VIX", "RUT"}:
            continue
        try:
            c = float(q.get("change1d"))
        except (TypeError, ValueError):
            continue
        if abs(c) >= 3.0:
            movers.append((c, sym, q.get("sector") or ""))
    movers.sort(key=lambda x: x[0])
    if movers:
        worst = movers[:5]
        best = movers[-5:][::-1]
        parts.append("Watchlist losers >=3%: " + ", ".join(f"{s} {_fmt_pct(c)} ({sec})" for c, s, sec in worst))
        parts.append("Watchlist gainers >=3%: " + ", ".join(f"{s} {_fmt_pct(c)} ({sec})" for c, s, sec in best))

    # Factor 1M
    f_parts = []
    for code, label in (("MTUM", "Momentum"), ("VLUE", "Value"), ("QUAL", "Quality"), ("IWF", "Growth")):
        f = factors.get(code) or {}
        m1 = f.get("change_1m") or f.get("change1m")
        if m1 is not None:
            f_parts.append(f"{label} 1M {_fmt_pct(m1)}")
    if f_parts:
        parts.append("Factor 1M: " + ", ".join(f_parts))

    regime = (macro.get("regime") or {})
    if regime.get("regime"):
        parts.append(f"Regime: {regime['regime']}")

    return "\n".join(parts)


PROMPT_TEMPLATE = """You are writing a 3-sentence end-of-day briefing for a buy-side analyst with a 1-3 year horizon. You will be given today's observed market data. Do NOT fabricate numbers. Only synthesize what the data implies and what was reported in the news today that is likely driving the action.

OBSERVED DATA:
{context}

Write exactly 2 sentences (What happened): (a) The headline move and what is actually driving it (cite the data and the named news catalyst from your knowledge of today's session if obvious). (b) What it means in context — connect to the regime, factor leadership, or the sector rotation visible in the data above.

Constraints: No emoji. No markdown headers or bold. Plain prose. Each sentence under 35 words. Do not invent specific company news that is not consistent with the data above. If unsure about a catalyst, say "the move appears to reflect" rather than asserting a cause. Do NOT include any forward-looking "tomorrow" or "what to watch" content — that is handled elsewhere.

Return JSON only:
{{"happened": "Sentence 1. Sentence 2."}}
"""


def run():
    print("\n=== Daily Narrative ===")
    if not MACRO_PATH.exists():
        print("  macro_data.json missing — skipping")
        return 1

    macro = json.loads(MACRO_PATH.read_text())
    snap_path = ROOT / "data-snapshot.json"
    snapshot = json.loads(snap_path.read_text()) if snap_path.exists() else {}

    context = _build_context(macro, snapshot)
    if not context.strip():
        print("  no observed data to summarize — skipping")
        return 1

    prompt = PROMPT_TEMPLATE.format(context=context)
    print(f"  Context: {len(context)} chars, calling sonar...")

    try:
        resp = call_perplexity(
            "_macro", "daily_narrative", prompt,
            max_tokens=400, force=True,
            extra_meta={"model": "sonar"},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: sonar call failed: {exc}")
        return 2

    happened = None
    if isinstance(resp, dict):
        happened = resp.get("happened")
        if not happened and "raw" in resp:
            try:
                happened = json.loads(resp["raw"]).get("happened")
            except (ValueError, TypeError):
                pass
        # Back-compat fallback: old single-text field
        if not happened:
            happened = resp.get("text") or resp.get("narrative")

    if not happened:
        print(f"  ERROR: no usable text in response")
        return 3

    happened = str(happened).strip()
    macro["daily_narrative"] = {
        "happened": happened,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": "sonar",
    }
    MACRO_PATH.write_text(json.dumps(macro, indent=2))
    print(f"  Wrote {len(happened)} chars to macro_data.daily_narrative (happened={len(happened)})")
    print(f"  Happened: {happened[:140]}{'...' if len(happened) > 140 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
