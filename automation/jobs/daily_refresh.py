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


def step_sync_earnings_intel():
    """Step 6.4: Sync earnings_intel.json from markdown notes.

    The pre/post-earnings note generators only write flat markdown files +
    earnings_notes_index.json. The Earnings Intel tab in the dashboard reads
    from earnings_intel.json, which historically was only seeded manually.
    This step closes that loop so every ticker with a fresh note shows up in
    the tab. Idempotent and safe to run on every tick.

    Must run AFTER step_generate_notes (so new notes exist on disk) and
    BEFORE step_compute_debate_scores (which reads earnings_intel.json).
    """
    print("\n=== Step 6.4: Sync Earnings Intel from Notes ===")
    try:
        import sys as _sys
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "sync_earnings_intel_from_notes",
            ROOT_DIR / "scripts" / "sync_earnings_intel_from_notes.py",
        )
        if _spec is None or _spec.loader is None:
            raise ImportError("sync_earnings_intel_from_notes.py not found")
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        # main() uses argparse on sys.argv; isolate it from daily_refresh's argv.
        _saved_argv = _sys.argv
        _sys.argv = ["sync_earnings_intel_from_notes.py"]
        try:
            _mod.main()
        finally:
            _sys.argv = _saved_argv
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] earnings_intel sync failed: {exc}")


def step_compute_debate_scores():
    """Compute Debate Intensity (Contested Velocity) scores in earnings_intel.json.

    Backfills resolved_at on signal_scorecard entries, converts pushes_higher /
    pushes_lower to {signal_id, text} objects, and writes per-ticker +
    universe debate_score blocks. Idempotent.
    """
    print("\n=== Step 6.5: Debate Scores ===")
    intel_path = ROOT_DIR / "earnings_intel.json"
    if not intel_path.exists():
        print("  [WARN] earnings_intel.json not found at", intel_path)
        return
    try:
        # Load compute_debate_scores.py by absolute path so this works
        # regardless of the runner's current working directory.
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "compute_debate_scores",
            ROOT_DIR / "compute_debate_scores.py",
        )
        if _spec is None or _spec.loader is None:
            raise ImportError(f"compute_debate_scores.py not found at {ROOT_DIR}")
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.process(str(intel_path), verbose=False)
        print("  Debate scores updated")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] debate score compute failed: {exc}")


def step_market_intel_harvest():
    """Harvest TAM / category-growth / AI-ML context per subsector to Supabase.

    Sunday-only by default — the underlying research is slow-changing
    (TAM and CAGR estimates rarely move week-to-week) and each row costs a
    Perplexity research task. Skipped on every other weekday to keep daily
    credit consumption flat.
    """
    print("\n=== Step 7: Market Intel Harvest ===")
    if TODAY.weekday() != 6:
        print(f"  [SKIP] Not Sunday (today is {TODAY.strftime('%A')}); harvest is Sunday-only.")
        return
    try:
        from automation.jobs.market_intel_harvest import run as _harvest_run
        _harvest_run(dry_run=False, force=False)
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] market_intel_harvest failed: {exc}")


# NOTE: A previous step_supabase_push() function attempted to invoke a
# top-level populate_supabase.mjs that does not exist in this repo.
# Supabase writes are already performed directly inside each refresh
# script (refresh_quotes.mjs, refresh_deep_dive.mjs, refresh_charts.mjs,
# refresh_comps_outperf.mjs, refresh_macro.mjs), so the standalone push
# step was a silent no-op every run. Removed to eliminate dead code.


def run():
    """Daily pipeline — gated by REFRESH_MODE env var.

    Modes:
      * "bmo"  — 6 AM ET pre-market run. Detects new earnings events,
                generates pre-earnings notes (today's reporters) and
                post-earnings notes for any prior-day AMC reporters,
                refreshes macro context, recomputes subsectors, and
                updates debate scores. NO market-data refresh (the market
                is closed) and NO news scan.
      * "amc"  — 5:30 PM ET post-close run. Refreshes quotes / deep-dive /
                charts / comps, re-detects earnings events with AMC
                timing, runs the news scan + tagging for active tickers,
                generates post-earnings notes for today's AMC reporters,
                and updates debate scores. NO macro refresh and NO
                subsector refresh (those are owned by the BMO run).
      * "full" — Legacy / dev-dispatch behavior: runs every step. This is
                the implicit default when REFRESH_MODE is unset, used by
                manual workflow_dispatch invocations.
    """
    mode = os.environ.get("REFRESH_MODE", "full").lower().strip()
    if mode not in ("bmo", "amc", "full"):
        print(f"[WARN] Unknown REFRESH_MODE={mode!r}, falling back to 'full'")
        mode = "full"

    print(f"{'='*60}")
    print(f"SignalAI Daily Refresh — {TODAY.isoformat()} (mode={mode})")
    print(f"{'='*60}")

    # Housekeeping (always)
    clear_stale_cache()

    # Step 1: Market data (no LLM) — AMC + full only. Skipped at 6 AM ET
    # because the market hasn't opened and Yahoo's quote data still
    # reflects yesterday's close.
    if mode in ("amc", "full"):
        step_market_data()

    # Step 2: Macro (no LLM) — BMO + full only. Macro indicators (rates,
    # VIX, regime) refresh once per day pre-market; AMC re-pull would
    # be redundant.
    if mode in ("bmo", "full"):
        step_macro_refresh()

    # Step 3: Earnings events (no LLM) — always. The detection logic is
    # cheap and is the source of truth for downstream steps.
    pre, post = step_earnings_events()
    active = pre + post

    # Step 4 / 4b: News scan + tagging (Perplexity) — AMC + full only.
    # We scan once per day after the close so that the news set covers a
    # full trading day's worth of catalysts.
    if mode in ("amc", "full"):
        news_updates = step_news_scan(active)
        step_news_tagging(news_updates)

    # Step 5: Notes (Perplexity, with skip guards) — always. Both
    # pre_earnings_notes and post_earnings_notes already filter their
    # own work using the freshly-detected calendar; running in both BMO
    # and AMC slots ensures same-day BMO/AMC reporters get post-earnings
    # notes generated within hours of their print.
    step_generate_notes()

    # Step 6: Subsectors (no LLM) — BMO + full only. Subsector
    # classifications change at most once per day; pinning them to BMO
    # avoids a redundant Node spawn at 5:30 PM.
    if mode in ("bmo", "full"):
        step_subsector_refresh()

    # Step 6.4: Sync earnings_intel.json from the markdown notes on disk.
    # Must run AFTER step_generate_notes so the latest pre/post notes are
    # reflected as tickers in earnings_intel.json before debate scores read
    # from it. This is what makes the Earnings Intel tab self-healing.
    step_sync_earnings_intel()

    # Step 6.5: Debate scores (derived from earnings_intel.json) —
    # always. Must run AFTER any step that writes earnings_intel.json.
    # Supabase writes are already performed inside each refresh_*.mjs
    # script invoked above, so there is no separate Supabase-push step.
    step_compute_debate_scores()

    # Step 7: Market intel harvest (Perplexity, Sunday-only) — BMO + full.
    # The harvester self-skips when today is not Sunday, but we additionally
    # gate on mode so AMC runs never trigger the import even on a Sunday.
    if mode in ("bmo", "full"):
        step_market_intel_harvest()

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
