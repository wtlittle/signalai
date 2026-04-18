"""
Pre-earnings note generation with skip guards.
Only calls Perplexity for tickers that:
  1. Don't already have a note
  2. Are within the pre-earnings window
  3. Haven't been researched today (cache check)
"""
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from automation.shared.paths import PRE_EARNINGS_DIR, EARNINGS_CALENDAR, EARNINGS_INDEX
from automation.shared.cache import note_already_exists, load_research_cache
from automation.shared.tickers import load_common_names
from automation.shared.io_helpers import read_json, write_json
from automation.perplexity.client import call_perplexity
from automation.perplexity.prompts import build_pre_earnings_prompt

TODAY = date.today()
MAX_DAYS = int(os.environ.get("MAX_PRE_EARNINGS_DAYS", 14))


def get_pre_earnings_tickers() -> list[dict]:
    """Load tickers in pre-earnings window from calendar."""
    cal = read_json(EARNINGS_CALENDAR)
    tickers = []
    for entry in cal.get("pre_earnings", []):
        days = entry.get("days_until", 999)
        if days <= MAX_DAYS:
            tickers.append(entry)
    return tickers


def write_pre_earnings_note(ticker: str, company: str, earnings_date: str,
                             days_until: int, data: dict):
    """Write a markdown pre-earnings note from structured data."""
    setup = data.get("setup", "N/A")
    debates = data.get("key_debates", [])
    matters = data.get("what_matters", [])
    guidance = data.get("guidance_watch", "N/A")
    implied = data.get("options_implied_move", "N/A")
    scenarios = data.get("scenarios", {})
    news = data.get("recent_news", [])
    analyst = data.get("analyst_changes", [])
    sources = data.get("sources", [])

    bull = scenarios.get("bull", {})
    base = scenarios.get("base", {})
    bear = scenarios.get("bear", {})

    debates_md = "\n".join(f"- {d}" for d in debates) if debates else "- N/A"
    matters_md = "\n".join(f"- {m}" for m in matters) if matters else "- N/A"
    news_md = "\n".join(f"- {n}" for n in news) if news else "- No material news"
    analyst_md = "\n".join(f"- {a}" for a in analyst) if analyst else "- No recent changes"
    sources_md = "\n".join(f"- {s}" for s in sources) if sources else ""

    note = f"""# {company} ({ticker}) — Pre-Earnings Note
**Earnings Date:** {earnings_date} | **Days Until Report:** {days_until}
**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## Set-up

{setup}

---

## Key Debates & Variant Perception

{debates_md}

---

## What Matters This Print

{matters_md}

**Guidance Watch:** {guidance}

**Options-Implied Move:** {implied}

---

## Scenario Grid

| Scenario | Probability | Trigger | Stock Move |
|----------|-------------|---------|------------|
| Bull | {bull.get('probability', '?')} | {bull.get('trigger', '?')} | {bull.get('stock_move', '?')} |
| Base | {base.get('probability', '?')} | {base.get('trigger', '?')} | {base.get('stock_move', '?')} |
| Bear | {bear.get('probability', '?')} | {bear.get('trigger', '?')} | {bear.get('stock_move', '?')} |

---

## Recent News

{news_md}

---

## Analyst Changes

{analyst_md}

---

*Sources:*
{sources_md}
"""
    note_path = PRE_EARNINGS_DIR / f"{ticker}_{earnings_date}.md"
    note_path.write_text(note)
    print(f"  [WRITE] {note_path.name}")


def update_index(ticker: str, company: str, earnings_date: str, days_until: int):
    """Add or update this ticker in earnings_notes_index.json."""
    index = read_json(EARNINGS_INDEX)
    if "active_pre_earnings" not in index:
        index["active_pre_earnings"] = []

    # Remove existing entry for this ticker+date
    index["active_pre_earnings"] = [
        e for e in index["active_pre_earnings"]
        if not (e.get("ticker") == ticker and e.get("date") == earnings_date)
    ]
    index["active_pre_earnings"].append({
        "ticker": ticker,
        "company": company,
        "date": earnings_date,
        "days_until": days_until,
        "file": f"notes/pre_earnings/{ticker}_{earnings_date}.md",
    })
    index["active_pre_earnings"].sort(key=lambda x: x.get("date", ""))
    index["updated"] = datetime.now().isoformat()
    write_json(EARNINGS_INDEX, index)


def run():
    """Main entry: generate pre-earnings notes with skip guards."""
    tickers = get_pre_earnings_tickers()
    names = load_common_names()
    print(f"\nPre-earnings notes — {len(tickers)} tickers in window")

    generated = 0
    skipped = 0

    for entry in tickers:
        ticker = entry["ticker"]
        earnings_date = entry["date"]
        company = entry.get("company", names.get(ticker, ticker))
        days_until = entry.get("days_until", 0)

        # --- GUARD 1: Note already exists ---
        if note_already_exists(ticker, earnings_date, "pre"):
            print(f"  [SKIP] {ticker} pre-earnings note exists for {earnings_date}")
            # Still update the day count in existing note
            _update_day_count(ticker, earnings_date, days_until)
            skipped += 1
            continue

        # --- GUARD 2: Too far out ---
        if days_until > MAX_DAYS:
            print(f"  [SKIP] {ticker} earnings in {days_until} days — outside window")
            skipped += 1
            continue

        # --- GUARD 3: Cache hit handled inside call_perplexity ---

        # Build prompt with whatever consensus data we have
        consensus = {
            "quarter": entry.get("quarter", ""),
            "rev_est": entry.get("rev_est", "?"),
            "rev_growth": entry.get("rev_growth", "?"),
            "eps_est": entry.get("eps_est", "?"),
        }

        prompt = build_pre_earnings_prompt(
            ticker, company, earnings_date, days_until, consensus
        )
        result = call_perplexity(ticker, "pre_earnings", prompt, max_tokens=1800)

        if result and not result.get("dry_run"):
            write_pre_earnings_note(ticker, company, earnings_date, days_until, result)
            update_index(ticker, company, earnings_date, days_until)
            generated += 1

    print(f"\nPre-earnings complete: {generated} generated, {skipped} skipped")
    return generated


def _update_day_count(ticker: str, earnings_date: str, new_days: int):
    """Update the day count in an existing pre-earnings note."""
    import re
    note_path = PRE_EARNINGS_DIR / f"{ticker}_{earnings_date}.md"
    if not note_path.exists():
        return
    content = note_path.read_text()
    # Update bold markdown day count
    updated = re.sub(
        r"(\*\*Days Until Report:\*\*\s*)\d+",
        f"\\g<1>{new_days}",
        content,
    )
    if updated != content:
        note_path.write_text(updated)


if __name__ == "__main__":
    run()
