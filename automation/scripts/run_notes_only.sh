#!/usr/bin/env bash
# run_notes_only.sh — Run only earnings note generation (skip market data refresh).
# Useful for testing prompts or regenerating specific notes.
#
# Usage: ./automation/scripts/run_notes_only.sh
#        FORCE_REGENERATE=true ./automation/scripts/run_notes_only.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

# Load .env if present
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

echo "=== SignalAI Notes Generation Only ==="
echo "Date: $(date -u +%Y-%m-%d)"
echo "Force: ${FORCE_REGENERATE:-false}"
echo ""

python -m automation.jobs.earnings_events
python -m automation.jobs.pre_earnings_notes
python -m automation.jobs.post_earnings_notes

echo ""
echo "=== Notes generation complete ==="
