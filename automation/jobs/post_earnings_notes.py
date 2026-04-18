"""
Post-earnings note generation with skip guards.
Only calls Perplexity for tickers that:
  1. Don't already have a post-earnings note
  2. Reported within the post-earnings window
  3. Haven't been researched today (cache check)
Reuses pre-earnings cache to avoid re-researching known context.
"""
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from automation.shared.paths import POST_EARNINGS_DIR, EARNINGS_CALENDAR, EARNINGS_INDEX
from automation.shared.cache import note_already_exists, load_research_cache
from automation.shared.tickers import load_common_names
from automation.shared.io_helpers import read_json, write_json
from automation.perplexity.client import call_perplexity
from automation.perplexity.prompts import build_post_earnings_prompt

TODAY = date.today()
MAX_DAYS = int(os.environ.get("MAX_POST_EARNINGS_DAYS", 14))


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

        # --- GUARD 3: Reuse pre-earnings research if available ---
        pre_cache = load_research_cache(ticker, "pre_earnings")

        actuals = {
            "rev_actual": entry.get("rev_actual", "?"),
            "rev_est": entry.get("rev_est", "?"),
            "rev_beat_miss": entry.get("rev_beat_miss", "?"),
            "eps_actual": entry.get("eps_actual", "?"),
            "eps_est": entry.get("eps_est", "?"),
            "eps_beat_miss": entry.get("eps_beat_miss", "?"),
            "stock_reaction": entry.get("stock_reaction", "?"),
        }

        prompt = build_post_earnings_prompt(
            ticker, company, earnings_date, actuals, pre_context=pre_cache
        )
        result = call_perplexity(ticker, "post_earnings", prompt, max_tokens=2000)

        if result and not result.get("dry_run") and not result.get("skipped"):
            write_post_earnings_note(ticker, company, earnings_date, days_since, result)
            update_index(ticker, company, earnings_date, days_since)
            generated += 1
        elif result and result.get("skipped"):
            skipped += 1

    print(f"\nPost-earnings complete: {generated} generated, {skipped} skipped")
    return generated


if __name__ == "__main__":
    run()
