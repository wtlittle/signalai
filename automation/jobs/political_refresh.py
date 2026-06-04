"""
Political intelligence refresh — thin wrapper that invokes refresh_political.mjs.

Intended cron: 0 5 * * 1 (Monday 05:00 UTC, Sunday night US)
Rationale: STOCK Act mandates disclosure within 45 days of trade. Congressional
trade filings cluster around month-end. A weekly Monday-morning refresh captures
the latest batch while keeping API usage minimal.

Env vars passed through:
  PROPUBLICA_API_KEY   — ProPublica Congress API key (optional, retired 2024)
  CONGRESS_GOV_API_KEY — Congress.gov API key (recommended)
  SUPABASE_URL         — Supabase project URL (optional)
  SUPABASE_SERVICE_KEY — Supabase service key (optional)
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR


def run():
    script = ROOT_DIR / "refresh_political.mjs"
    if not script.exists():
        print(f"[political_refresh] Script not found: {script}")
        return False

    result = subprocess.run(
        ["node", str(script)],
        cwd=str(ROOT_DIR),
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=180,
    )

    if result.stdout.strip():
        for line in result.stdout.strip().split("\n")[-10:]:
            print(f"  {line}")

    if result.returncode != 0:
        print(f"[political_refresh] FAILED (exit {result.returncode})")
        if result.stderr.strip():
            print(f"  stderr: {result.stderr[:500]}")
        return False

    print("[political_refresh] OK")
    return True


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
