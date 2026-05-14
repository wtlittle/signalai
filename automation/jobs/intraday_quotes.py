"""
Intraday quotes-only refresh (Tier 2).

Runs hourly during US market hours. Refreshes ONLY quotes (price, change1d)
via Yahoo Finance and pushes to Supabase. Skips:
  - Deep dive (financial statements — change ~quarterly)
  - Charts (full history — daily is enough)
  - Comps (cross-sector — daily is enough)
  - Macro / earnings / notes / news / debate scores

This is intentionally minimal so it can run many times per day at very low
cost. The dashboard's live-quotes.js polls Supabase every 60s to pick up
fresh prices without a page reload.

Also emits big_move_10pct + sector_rotation alerts when the refreshed
quotes cross thresholds.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR
from automation.jobs.daily_refresh import run_node_script, step_emit_alerts

TODAY = date.today()


def run():
    print(f"[intraday_quotes] start {TODAY.isoformat()}")
    supabase_env = {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
        "SUPABASE_SERVICE_KEY": os.environ.get("SUPABASE_SERVICE_KEY", ""),
    }
    print("  Running refresh_quotes.mjs...")
    run_node_script("refresh_quotes.mjs", supabase_env)

    # Emit alerts based on the freshly-refreshed quotes. We pass empty
    # earnings lists because this cron does NOT touch the earnings
    # calendar; earnings_day alerts are already handled by the BMO/AMC
    # crons and the 7am daily refresh.
    try:
        step_emit_alerts(pre=[], post=[])
    except Exception as exc:
        print(f"[intraday_quotes] alerts failed (non-fatal): {exc}")

    print("[intraday_quotes] done")


if __name__ == "__main__":
    run()
