"""
Daily refresh — main entry point for the full daily pipeline.

Architecture:
- Market data (quotes, charts, estimates) → Yahoo Finance (no LLM cost)
- Earnings event detection → yfinance (no LLM cost)
- News scan → Perplexity, but ONLY for earnings-active tickers
- Note generation → Perplexity, with skip guards + caching
- Subsector refresh → local Node script (no LLM cost)
- Supabase push → local Node script (no LLM cost)

This means on a quiet day with no earnings events, this pipeline makes
ZERO Perplexity API calls.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR, EARNINGS_CALENDAR, MACRO_DATA
from automation.shared.cache import clear_stale_cache
from automation.shared.tickers import load_tickers, load_common_names
from automation.shared.io_helpers import read_json
from automation.perplexity.client import call_perplexity
from automation.perplexity.prompts import build_news_prompt, build_news_tagging_prompt

TODAY = date.today()
PRE_WINDOW = int(os.environ.get("MAX_PRE_EARNINGS_DAYS", 14))
POST_WINDOW = int(os.environ.get("MAX_POST_EARNINGS_DAYS", 14))


def run_node_script(script: str, env_extra: dict | None = None):
    """Run a Node.js script from the repo root."""
    script_path = ROOT_DIR / script
    if not script_path.exists():
        print(f"  [WARN] Script not found: {script}")
        return False

    env = {**os.environ}
    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        ["node", str(script_path)],
        cwd=str(ROOT_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"  [ERROR] {script}: {result.stderr[:500]}")
        return False
    if result.stdout.strip():
        # Print last 5 lines of output
        for line in result.stdout.strip().split("\n")[-5:]:
            print(f"  {line}")
    return True


def step_market_data():
    """Step 1: Refresh market data from Yahoo Finance (no LLM cost)."""
    print("\n=== Step 1: Market Data Refresh (Yahoo Finance) ===")

    supabase_env = {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
        "SUPABASE_SERVICE_KEY": os.environ.get("SUPABASE_SERVICE_KEY", ""),
    }

    print("  Running refresh_quotes.mjs...")
    run_node_script("refresh_quotes.mjs", supabase_env)

    print("  Running refresh_deep_dive.mjs...")
    run_node_script("refresh_deep_dive.mjs", supabase_env)

    print("  Running refresh_charts.mjs...")
    run_node_script("refresh_charts.mjs", supabase_env)

    print("  Running refresh_comps_outperf.mjs...")
    run_node_script("refresh_comps_outperf.mjs", supabase_env)


def step_macro_refresh():
    """Step 2: Refresh macro indicators and regime classification."""
    print("\n=== Step 2: Macro Refresh ===")
    supabase_env = {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
        "SUPABASE_SERVICE_KEY": os.environ.get("SUPABASE_SERVICE_KEY", ""),
    }
    run_node_script("refresh_macro.mjs", supabase_env)

    # Print regime
    macro = read_json(MACRO_DATA)
    regime = macro.get("regime", {})
    print(f"  Regime: {regime.get('name', 'Unknown')}")


def step_earnings_events():
    """Step 3: Detect earnings events (yfinance, no LLM cost)."""
    print("\n=== Step 3: Earnings Event Detection ===")
    from automation.jobs.earnings_events import run as detect_events
    return detect_events()


# --- News raw + tagged output paths ---------------------------------------
NEWS_RAW_DIR = ROOT_DIR / "data" / "news_raw"
NEWS_TAGGED_DIR = ROOT_DIR / "data" / "news_tagged"


def _persist_news_raw(ticker: str, payload: dict) -> Path:
    """Write the raw news scan response to data/news_raw/<TICKER>.json.

    Persisted so the subsequent tagging step has a concrete input to reference
    (the queue entry stores only the ticker + path, not the full article
    payload, which keeps pending_tasks.json compact).
    """
    NEWS_RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = NEWS_RAW_DIR / f"{ticker}.json"
    record = {
        "ticker": ticker,
        "scanned_at": datetime.utcnow().isoformat() + "Z",
        "payload": payload if isinstance(payload, dict) else {},
    }
    with open(out, "w") as f:
        json.dump(record, f, indent=2)
    return out


def step_news_scan(active_tickers: list[dict]) -> list[dict]:
    """Step 4: Scan news ONLY for earnings-active tickers (Perplexity).

    Returns the list of tickers that had material updates, with their
    persisted article paths so step_news_tagging can queue tagging tasks.
    """
    print(f"\n=== Step 4: News Scan ({len(active_tickers)} active tickers) ===")
    if not active_tickers:
        print("  No earnings-active tickers — skipping news scan")
        return []

    names = load_common_names()
    with_updates: list[dict] = []
    for entry in active_tickers:
        ticker = entry["ticker"]
        company = entry.get("name", names.get(ticker, ticker))
        sector = entry.get("sector", "")

        news = call_perplexity(
            ticker, "daily_news",
            build_news_prompt(ticker, company, sector),
            max_tokens=600,
        )
        has_update = news.get("has_material_update", False) if isinstance(news, dict) else False
        if has_update:
            items = news.get("items", []) or []
            raw_path = _persist_news_raw(ticker, news)
            with_updates.append({
                "ticker": ticker,
                "company": company,
                "articles": items,
                "raw_path": str(raw_path.relative_to(ROOT_DIR)),
            })
            print(f"  [NEWS] {ticker}: {len(items)} material update(s) → {raw_path.relative_to(ROOT_DIR)}")
        else:
            print(f"  [NO NEWS] {ticker}")
    return with_updates


def step_news_tagging(news_updates: list[dict]) -> int:
    """Step 4b: For each ticker with material news, queue a news_tag task.

    The tagging task takes the article batch from Step 4 and asks the LLM
    to classify each article with catalyst_tag, direction, priority, and
    a financial-variable blurb. Output is written to
    data/news_tagged/<TICKER>.json by Computer when it processes the queue.
    """
    print(f"\n=== Step 4b: News Tagging ({len(news_updates)} tickers with updates) ===")
    if not news_updates:
        print("  Nothing to tag — skipping")
        return 0

    NEWS_TAGGED_DIR.mkdir(parents=True, exist_ok=True)

    # Normalize article dicts: the daily_news scanner emits
    # {headline, impact, url}. We remap to {headline, teaser} so the tagging
    # prompt gets the shape it expects. If a teaser is missing, fall back to
    # the headline itself (tagger will down-rank with Neutral direction).
    queued = 0
    for upd in news_updates:
        ticker = upd["ticker"]
        company = upd["company"]
        raw_articles = upd.get("articles") or []
        articles = []
        for a in raw_articles:
            if not isinstance(a, dict):
                continue
            articles.append({
                "headline": a.get("headline", ""),
                "teaser": a.get("teaser") or a.get("body") or a.get("summary") or a.get("headline", ""),
                "url": a.get("url", ""),
                "impact": a.get("impact", ""),
            })
        if not articles:
            print(f"  [SKIP] {ticker}: no articles to tag")
            continue

        call_perplexity(
            ticker, "news_tag",
            build_news_tagging_prompt(ticker, company, articles),
            max_tokens=700,
            extra_meta={
                "article_count": len(articles),
                "raw_path": upd.get("raw_path", ""),
                "output_path": f"data/news_tagged/{ticker}.json",
                "articles": articles,
            },
        )
        queued += 1
        print(f"  [QUEUED] {ticker}: news_tag for {len(articles)} article(s)")
    return queued


def step_generate_notes():
    """Step 5: Generate/update earnings notes (Perplexity, with guards)."""
    print("\n=== Step 5: Earnings Note Generation ===")
    from automation.jobs.pre_earnings_notes import run as run_pre
    from automation.jobs.post_earnings_notes import run as run_post
    pre_count = run_pre()
    post_count = run_post()
    return pre_count, post_count


def step_subsector_refresh():
    """Step 6: Refresh subsector classifications."""
    print("\n=== Step 6: Subsector Refresh ===")
    run_node_script("update_subsectors.mjs")


def step_supabase_push():
    """Step 7: Push all data to Supabase."""
    print("\n=== Step 7: Supabase Push ===")
    # populate_supabase.mjs lives in the workspace root in the current setup
    # After migration it should be in the repo
    populate_script = ROOT_DIR.parent / "populate_supabase.mjs"
    if not populate_script.exists():
        populate_script = ROOT_DIR / "populate_supabase.mjs"
    if populate_script.exists():
        supabase_env = {
            "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
            "SUPABASE_SERVICE_KEY": os.environ.get("SUPABASE_SERVICE_KEY", ""),
        }
        subprocess.run(
            ["node", str(populate_script)],
            cwd=str(ROOT_DIR),
            env={**os.environ, **supabase_env},
            capture_output=True,
            text=True,
            timeout=300,
        )
        print("  Supabase push complete")
    else:
        print("  [WARN] populate_supabase.mjs not found")


def run():
    """Full daily pipeline."""
    print(f"{'='*60}")
    print(f"SignalAI Daily Refresh — {TODAY.isoformat()}")
    print(f"{'='*60}")

    # Housekeeping
    clear_stale_cache()

    # Step 1: Market data (no LLM)
    step_market_data()

    # Step 2: Macro (no LLM)
    step_macro_refresh()

    # Step 3: Earnings events (no LLM)
    pre, post = step_earnings_events()
    active = pre + post

    # Step 4: News — only for active tickers (Perplexity)
    news_updates = step_news_scan(active)

    # Step 4b: News tagging — queue buy-side catalyst tagging for updated tickers
    step_news_tagging(news_updates)

    # Step 5: Notes — with skip guards (Perplexity)
    step_generate_notes()

    # Step 6: Subsectors (no LLM)
    step_subsector_refresh()

    # Step 7: Supabase (no LLM)
    step_supabase_push()

    # --- Queue summary: tasks written to automation/queue/pending_tasks.json ---
    queue_file = ROOT_DIR / "automation" / "queue" / "pending_tasks.json"
    queued_entries = []
    if queue_file.exists():
        try:
            with open(queue_file, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    queued_entries = data
        except (json.JSONDecodeError, OSError):
            queued_entries = []

    print(f"\n{'='*60}")
    print(f"Daily refresh complete.")
    print(f"  Pre-earnings tickers: {len(pre)}")
    print(f"  Post-earnings tickers: {len(post)}")
    print(f"{'='*60}")

    print(f"\n=== {len(queued_entries)} tasks queued for Computer review ===")
    if queued_entries:
        for entry in queued_entries:
            ticker = entry.get("ticker", "?")
            task = entry.get("task", "?")
            queued_at = entry.get("queued_at", "")
            print(f"  • {ticker:<6} {task:<24} queued_at={queued_at}")
        print(f"\n  Queue file: {queue_file}")
        print(f"  Open this file in Perplexity Computer to process the pending tasks.")
    else:
        print("  (No LLM tasks queued this run.)")
    print(f"{'='*60}")


if __name__ == "__main__":
    run()
