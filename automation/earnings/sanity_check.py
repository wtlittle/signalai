"""
Sanity-check harness for Stage 1 context builders.

The cron-runnable context builders fetch data via Perplexity chat completions
(structured JSON prompts) which is the only path available from Python scripts.
This works in practice but the LLM can occasionally hallucinate older historical
numbers. To guard against drift, this harness re-validates the *latest 2 quarters*
of earnings history against a ground-truth source.

Two modes:

1. file-mode (`--truth-file path.json`): read the truth payload from a JSON file
   on disk. Used when calling this from a Computer agent that has already pulled
   data via the MCP `finance_earnings_history` tool and dumped the response.

2. inline-mode (`--truth-json '<json>'`): pass the truth payload inline as a
   JSON string. Convenient for quick spot checks.

The harness fetches the most recent context snapshot for `ticker` from Supabase
`earnings_context_snapshots`, compares `history_8q[0]` and `history_8q[1]` against
the truth, and reports any field where the relative divergence exceeds
`--threshold` (default 0.01 = 1%).

Exit code 0 if all checks pass, 1 if any divergence exceeds threshold.

Usage:
    python -m automation.earnings.sanity_check NVDA --truth-file /tmp/nvda_history.json
    python -m automation.earnings.sanity_check NVDA --truth-json '{"quarters": [...]}'

Expected truth JSON shape (matches finance_earnings_history CSV):
    {"quarters": [
        {
            "period": "Q1 2027",
            "date": "2026-05-22",
            "actualRevenue": 44062000000,
            "estimatedRevenue": 43000000000,
            "actualEps": 0.96,
            "estimatedEps": 0.93,
            "postEarningsMoveOneDay": 4.5,
            "expectedMovePerc": 7.2
        }, ...
    ]}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make automation importable when run as a module or script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared import supabase_client as _sb


_SNAPSHOT_TABLE = "earnings_context_snapshots"

# Map of (truth_key, context_key) pairs to compare per quarter.
_FIELD_MAP = [
    ("actualRevenue", "actual_revenue"),
    ("estimatedRevenue", "estimated_revenue"),
    ("actualEps", "actual_eps"),
    ("estimatedEps", "estimated_eps"),
    ("postEarningsMoveOneDay", "post_earnings_move_1d_pct"),
    ("expectedMovePerc", "expected_move_pct"),
]


# ---------------------------------------------------------------------------
# Lightweight sanity gate for the wiring layer (Phase 3).
# ---------------------------------------------------------------------------

# Fields that MUST be present (non-None) for a note to be useful.
_REQUIRED_PRE = ("quote", "consensus_forward", "history_8q")
_REQUIRED_POST = ("quote", "consensus_forward", "history_8q", "this_quarter_actuals")

# If the errors list is this long, we have too many data gaps to write a note.
_MAX_ACCEPTABLE_ERRORS = 4


def sanity_check(context: dict) -> tuple[bool, list[str]]:
    """Validate a Stage 1 context dict has enough data for a useful note.

    Returns (ok, issues) where ok=True means the context is good enough to
    send to the LLM, and issues is a list of human-readable problem strings.
    """
    issues: list[str] = []

    if not context:
        return False, ["context is empty or None"]

    # Defensive: caller (or cache) may have handed us a string/list. Surface
    # a clear issue instead of crashing with AttributeError so the per-ticker
    # error is contained to that ticker and the daily refresh pipeline keeps
    # going.
    if not isinstance(context, dict):
        return False, [f"context has unexpected type {type(context).__name__}, expected dict"]

    snapshot_type = context.get("snapshot_type", "pre")
    required = _REQUIRED_POST if snapshot_type == "post" else _REQUIRED_PRE

    for field in required:
        val = context.get(field)
        if val is None:
            issues.append(f"required field '{field}' is None")
        elif isinstance(val, (list, dict)) and len(val) == 0:
            issues.append(f"required field '{field}' is empty")

    errors = context.get("errors") or []
    if len(errors) > _MAX_ACCEPTABLE_ERRORS:
        issues.append(
            f"too many data errors ({len(errors)} > {_MAX_ACCEPTABLE_ERRORS}): "
            + ", ".join(e.get("field", "?") for e in errors[:6])
        )

    return (len(issues) == 0), issues


def _fetch_latest_snapshot(ticker: str) -> dict | None:
    rows = _sb.fetch_rows(
        _SNAPSHOT_TABLE,
        params={
            "ticker": f"eq.{ticker}",
            "note_type": "eq.pre",
            "select": "context,generated_at,earnings_date",
            "order": "generated_at.desc",
            "limit": "1",
        },
    )
    if not rows:
        return None
    return rows[0]


def _rel_diff(actual: float | None, truth: float | None) -> float | None:
    """Return |actual - truth| / |truth|, or None if either is None or
    truth is 0."""
    if actual is None or truth is None:
        return None
    try:
        a = float(actual)
        t = float(truth)
    except (TypeError, ValueError):
        return None
    if t == 0:
        return None
    return abs(a - t) / abs(t)


def compare(
    ticker: str,
    truth: dict,
    threshold: float = 0.01,
    n_quarters: int = 2,
) -> tuple[bool, list[dict]]:
    """Compare context snapshot to truth. Returns (all_ok, divergences)."""
    snap = _fetch_latest_snapshot(ticker)
    if not snap or not snap.get("context"):
        return False, [{"error": f"no snapshot found for {ticker}"}]

    ctx = snap["context"]
    ctx_quarters = ctx.get("history_8q") or []
    truth_quarters = truth.get("quarters") or []

    divergences: list[dict] = []

    for i in range(min(n_quarters, len(ctx_quarters), len(truth_quarters))):
        ctx_q = ctx_quarters[i]
        truth_q = truth_quarters[i]
        period = ctx_q.get("period") or truth_q.get("period") or f"idx_{i}"

        for truth_key, ctx_key in _FIELD_MAP:
            t_val = truth_q.get(truth_key)
            c_val = ctx_q.get(ctx_key)
            diff = _rel_diff(c_val, t_val)
            if diff is not None and diff > threshold:
                divergences.append({
                    "period": period,
                    "field": ctx_key,
                    "context_value": c_val,
                    "truth_value": t_val,
                    "rel_diff_pct": round(diff * 100, 3),
                })

    return (len(divergences) == 0), divergences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker")
    parser.add_argument("--truth-file", help="Path to JSON file with truth payload")
    parser.add_argument("--truth-json", help="Inline JSON truth payload")
    parser.add_argument("--threshold", type=float, default=0.01,
                        help="Relative divergence threshold (default 0.01 = 1%%)")
    parser.add_argument("--n-quarters", type=int, default=2,
                        help="Number of recent quarters to validate (default 2)")
    args = parser.parse_args()

    if args.truth_file:
        truth = json.loads(Path(args.truth_file).read_text())
    elif args.truth_json:
        truth = json.loads(args.truth_json)
    else:
        print("ERROR: must provide --truth-file or --truth-json", file=sys.stderr)
        return 2

    ok, divergences = compare(
        args.ticker.upper(), truth,
        threshold=args.threshold,
        n_quarters=args.n_quarters,
    )

    print(json.dumps({
        "ticker": args.ticker.upper(),
        "threshold_pct": args.threshold * 100,
        "all_within_threshold": ok,
        "divergences": divergences,
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
