"""
Post-earnings note generation with skip guards.
Only calls Perplexity for tickers that:
  1. Don't already have a post-earnings note
  2. Reported within the post-earnings window
  3. Haven't been researched today (cache check)
Reuses pre-earnings cache to avoid re-researching known context.

Phase 3: wires Stage 1 (context builders) and Stage 2 (prompt schema)
into the note-generation pipeline.

Flow:
  calendar → skip guards → build_post_earnings_context → sanity_check gate
  → persist snapshot to Supabase → build_post_earnings_prompt_v2
  → call_perplexity → write note + upsert index → emit alert
"""
import json
import logging
import os
import sys
from datetime import date, datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from automation.shared.paths import POST_EARNINGS_DIR, EARNINGS_CALENDAR, EARNINGS_INDEX
from automation.shared.cache import note_already_exists, load_research_cache
from automation.shared.tickers import load_common_names
from automation.shared.io_helpers import read_json, write_json
from automation.shared import supabase_client as _sb
from automation.perplexity.client import call_perplexity
from automation.perplexity.prompts import build_post_earnings_prompt_v2
from automation.earnings.post_earnings_context import build_post_earnings_context
from automation.earnings.sanity_check import sanity_check
from automation.sources import history_8q as _history_8q

logger = logging.getLogger(__name__)

TODAY = date.today()
MAX_DAYS = int(os.environ.get("MAX_POST_EARNINGS_DAYS", 14))

_SNAPSHOT_TABLE = "earnings_context_snapshots"


def _heal_history_8q(ticker: str, context: dict) -> bool:
    """Self-heal a context missing history_8q via the fallback chain.

    Returns True if the chain produced >=8 quarters and the context's
    history_8q was filled (mapped to the context's per-quarter shape). Returns
    False if the chain was exhausted -- the caller then escalates as before. The
    chain logs its own gap to cron_tracking/history_8q_gaps.json. Never fabricates.
    """
    history, source, _tried, _partial = _history_8q.fetch_chain(ticker)
    if history is None:
        return False
    context["history_8q"] = [
        {
            "period": (f"FQ{q['fiscal_quarter']} {q['fiscal_year']}"
                       if q.get("fiscal_quarter") and q.get("fiscal_year") else q.get("period_end")),
            "period_end": q.get("period_end"),
            "actual_revenue": q.get("revenue_actual"),
            "estimated_revenue": None,
            "revenue_surprise_pct": q.get("rev_surprise_pct"),
            "actual_eps": q.get("eps_actual"),
            "estimated_eps": q.get("eps_estimate"),
            "eps_surprise_pct": q.get("eps_surprise_pct"),
        }
        for q in history
    ]
    context["history_8q_source"] = source
    # Drop any history_8q error so sanity_check no longer counts it as a gap.
    context["errors"] = [e for e in (context.get("errors") or [])
                         if e.get("field") != "history_8q"]
    return True


def get_post_earnings_tickers() -> list[dict]:
    """Load tickers in post-earnings window from calendar."""
    cal = read_json(EARNINGS_CALENDAR)
    tickers = []
    for entry in cal.get("post_earnings", []):
        days = entry.get("days_since", 999)
        if days <= MAX_DAYS:
            tickers.append(entry)
    return tickers


def write_post_earnings_note(ticker: str, company: str, earnings_date: str,
                              days_since: int, data: dict):
    """Write a markdown post-earnings note from structured data."""
    headline = data.get("headline", "N/A")
    bm_quality = data.get("beat_miss_quality", "N/A")
    metrics = data.get("key_metrics", [])
    guidance = data.get("guidance", "N/A")
    tone = data.get("management_tone", "N/A")
    surprises = data.get("surprises", [])
    thesis = data.get("thesis_impact", "N/A")
    analyst_rx = data.get("analyst_reactions", [])
    outlook = data.get("stock_outlook", "N/A")
    sources = data.get("sources", [])

    metrics_md = "\n".join(f"- {m}" for m in metrics) if metrics else "- N/A"
    surprise_md = "\n".join(f"- {s}" for s in surprises) if surprises else "- N/A"
    analyst_md = "\n".join(f"- {a}" for a in analyst_rx) if analyst_rx else "- N/A"
    sources_md = "\n".join(f"- {s}" for s in sources) if sources else ""

    note = f"""# {company} ({ticker}) — Post-Earnings Note
**Reported:** {earnings_date} | **Day Post:** {days_since}
**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## Headline

{headline}

**Beat/Miss Quality:** {bm_quality}

---

## Key Metrics

{metrics_md}

---

## Guidance and Tone

{guidance}

**Management Tone:** {tone}

---

## Surprises / Disappointments

{surprise_md}

---

## Thesis Impact

{thesis}

---

## Analyst Reactions

{analyst_md}

---

## Near-Term Outlook

{outlook}

---

*Sources:*
{sources_md}
"""
    note_path = POST_EARNINGS_DIR / f"{ticker}_{earnings_date}.md"
    note_path.write_text(note)
    print(f"  [WRITE] {note_path.name}")


def update_index(ticker: str, company: str, earnings_date: str, days_since: int):
    """Add or update this ticker in earnings_notes_index.json."""
    index = read_json(EARNINGS_INDEX)
    if "active_post_earnings" not in index:
        index["active_post_earnings"] = []

    # Remove existing entry for this ticker+date
    index["active_post_earnings"] = [
        e for e in index["active_post_earnings"]
        if not (e.get("ticker") == ticker and e.get("date") == earnings_date)
    ]

    expires = date.today().__class__.fromisoformat(earnings_date)
    from datetime import timedelta
    expires_date = (expires + timedelta(days=MAX_DAYS)).isoformat()

    index["active_post_earnings"].append({
        "ticker": ticker,
        "company": company,
        "date": earnings_date,
        "day_post": days_since,
        "expires": expires_date,
        "note_file": f"notes/post_earnings/{ticker}_{earnings_date}.md",
    })
    index["active_post_earnings"].sort(key=lambda x: x.get("date", ""))
    index["updated"] = datetime.now().isoformat()
    write_json(EARNINGS_INDEX, index)


def _persist_snapshot(ticker: str, earnings_date: str, context: dict) -> bool:
    """Persist the Stage 1 context to Supabase earnings_context_snapshots."""
    row = {
        "ticker": ticker,
        "earnings_date": earnings_date,
        "note_type": "post",
        "generated_at": datetime.now().astimezone().isoformat(),
        "context": json.dumps(context, default=str),
        "ticker_tier": context.get("_tier", "T2"),
    }
    # Include consensus_at_print if available
    cap = context.get("consensus_at_print")
    if cap:
        row["consensus_at_print"] = json.dumps(cap, default=str)
    return _sb.upsert_row(
        _SNAPSHOT_TABLE, row, on_conflict="ticker,earnings_date,note_type"
    )


def run():
    """Main entry: generate post-earnings notes with skip guards."""
    tickers = get_post_earnings_tickers()
    names = load_common_names()
    print(f"\nPost-earnings notes — {len(tickers)} tickers in window")

    generated = 0
    skipped = 0

    for entry in tickers:
        ticker = entry["ticker"]
        earnings_date = entry["date"]
        company = entry.get("company", names.get(ticker, ticker))
        days_since = entry.get("days_since", 0)

        # --- GUARD 1: Note already exists ---
        if note_already_exists(ticker, earnings_date, "post"):
            print(f"  [SKIP] {ticker} post-earnings note exists for {earnings_date}")
            skipped += 1
            continue

        # --- GUARD 2: Too old ---
        if days_since > MAX_DAYS:
            print(f"  [SKIP] {ticker} reported {days_since} days ago — outside window")
            skipped += 1
            continue

        # --- STAGE 1: Build deterministic context ---
        print(f"  [CONTEXT] {ticker} — building post-earnings context...")
        context = build_post_earnings_context(ticker)

        # --- SANITY CHECK GATE ---
        ok, issues = sanity_check(context)
        # SELF-HEAL: a missing/empty history_8q used to fail the gate and write a
        # stub (the 2026-06-04 ACN/AVGO failure). Instead, run the fallback chain
        # to populate it, then re-check. Only escalate if the chain also fails.
        if not ok and any("history_8q" in i for i in issues):
            print(f"  [HEAL] {ticker} history_8q missing -- running fallback chain")
            if _heal_history_8q(ticker, context):
                print(f"  [HEAL] {ticker} history_8q populated "
                      f"({context.get('history_8q_source')}); re-running sanity_check")
                ok, issues = sanity_check(context)
            else:
                print(f"  [HEAL] {ticker} history_8q chain exhausted -- escalating")
        if not ok:
            for issue in issues:
                logger.warning("[%s] sanity_check: %s", ticker, issue)
                print(f"  [WARN] {ticker} sanity_check: {issue}")
            # Write a stub note so the ticker isn't retried every run
            stub_path = POST_EARNINGS_DIR / f"{ticker}_{earnings_date}.md"
            stub_path.write_text(
                f"# {company} ({ticker}) — Post-Earnings Note\n"
                f"**Reported:** {earnings_date} | **Day Post:** {days_since}\n"
                f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"---\n\n"
                f"*Context sanity check failed — insufficient data for full note.*\n\n"
                f"Issues:\n" + "\n".join(f"- {i}" for i in issues) + "\n"
            )
            print(f"  [STUB] {ticker} — wrote stub note (sanity check failed)")
            skipped += 1
            continue

        # --- PERSIST CONTEXT SNAPSHOT ---
        _persist_snapshot(ticker, earnings_date, context)

        # --- STAGE 2: Build prompt from context ---
        system_prompt, user_prompt = build_post_earnings_prompt_v2(ticker, context)

        # --- LLM CALL ---
        # Reasoning models burn most of max_tokens on the <think> block, so
        # 2000 was too tight — the model never emitted the JSON tail. 4000
        # gives the chain-of-thought room while still keeping the cost bounded.
        result = call_perplexity(
            ticker, "post_earnings", user_prompt,
            system=system_prompt, max_tokens=4000,
        )

        # A queued result means the API key is not configured and the task
        # was handed off to Computer's pending_tasks queue. It is NOT a real
        # research result — writing it would produce an all-"N/A" stub note,
        # which then poisons the index and blocks future regeneration. Treat
        # it as skipped.
        if result and result.get("queued"):
            print(f"  [QUEUED] {ticker} post-earnings — handed to Computer queue; no note written")
            skipped += 1
        elif result and not result.get("dry_run") and not result.get("skipped"):
            # Reject obviously-empty payloads so a malformed API response
            # can't write a 403-byte stub on top of nothing.
            has_content = any(
                result.get(k)
                for k in ("headline", "key_metrics", "guidance", "thesis_impact",
                          "surprises", "analyst_reactions", "stock_outlook")
            )
            if not has_content:
                print(f"  [SKIP] {ticker} post-earnings result has no usable fields — not writing stub")
                skipped += 1
                continue
            write_post_earnings_note(ticker, company, earnings_date, days_since, result)
            update_index(ticker, company, earnings_date, days_since)
            generated += 1
            # Emit subscriber alert (Phase 2)
            try:
                from automation.alerts import emit_alert
                headline = (result.get("headline") or f"{company} earnings note refreshed").strip()
                emit_alert(
                    alert_type="post_earnings_note",
                    summary=f"{ticker} post-earnings note: {headline}",
                    ticker=ticker,
                    severity="info",
                    extra={"earnings_date": earnings_date, "days_since": days_since},
                )
            except Exception as _exc:
                print(f"  [{ticker}] emit_alert failed: {_exc}")
        elif result and result.get("skipped"):
            skipped += 1

    print(f"\nPost-earnings complete: {generated} generated, {skipped} skipped")
    return generated


if __name__ == "__main__":
    run()
