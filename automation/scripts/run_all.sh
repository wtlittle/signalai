#!/usr/bin/env bash
# run_all.sh — Run the full daily SignalAI pipeline locally.
# Usage: ./automation/scripts/run_all.sh
#
# Requires: .env file with PERPLEXITY_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY
# Or export those env vars before running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

# Load .env if present
if [ -f ".env" ]; then
    echo "Loading .env..."
    set -a
    source .env
    set +a
fi

echo "=== SignalAI Daily Refresh ==="
echo "Repo root: $REPO_ROOT"
echo "Date: $(date -u +%Y-%m-%d)"
echo ""

# Run the full pipeline
python -m automation.jobs.daily_refresh

echo ""
echo "=== Pipeline complete ==="
