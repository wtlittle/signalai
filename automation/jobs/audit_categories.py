"""
audit_categories — guardrail against uncategorized watchlist tickers.

Every ticker in DEFAULT_TICKERS (utils.js) must have a subsector in
SUBSECTOR_MAP. SUBSECTOR_MAP is the canonical categorization store: the
dashboard groups rows by it (groupBySubsector) and getSubsector() falls back
to "Other" for anything missing, which is what the user sees as an
"uncategorized" company. This job flags any ticker that would fall through.

Two modes:
  * default (warn)   — print uncategorized tickers, always exit 0. Wired into
                       the daily refresh so a regression is logged but never
                       blocks the pipeline.
  * --strict         — exit non-zero if any ticker is uncategorized. Use in
                       CI / pre-commit to hard-fail on a regression.

Usage:
    python -m automation.jobs.audit_categories          # warn-only
    python -m automation.jobs.audit_categories --strict # fail on regression
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.tickers import (  # noqa: E402
    load_active_universe,
    load_universe_v1_tickers,
    load_tickers,
    load_subsector_map,
    load_common_names,
)


def find_uncategorized() -> list[str]:
    """Return active-universe tickers with no SUBSECTOR_MAP entry.

    The active universe is the union of UNIVERSE_V1 (default coverage on
    the dashboard) and DEFAULT_TICKERS (full historical list). Auditing
    only DEFAULT_TICKERS is the bug that let KO/PEP/LLY/JNJ/XOM/... — all
    UNIVERSE_V1 names — show up under "Other" on the dashboard.
    """
    tickers = load_active_universe()
    submap = load_subsector_map()
    return [t for t in tickers if t not in submap]


def run(strict: bool = False) -> int:
    tickers = load_active_universe()
    v1 = set(load_universe_v1_tickers())
    full = set(load_tickers())
    submap = load_subsector_map()
    names = load_common_names()
    uncategorized = [t for t in tickers if t not in submap]

    print(
        f"=== Category Audit: {len(tickers)} active-universe tickers "
        f"({len(v1)} UNIVERSE_V1 + {len(full)} DEFAULT_TICKERS, deduped) ==="
    )
    print(f"  categorized:   {len(tickers) - len(uncategorized)}")
    print(f"  uncategorized: {len(uncategorized)}")

    if uncategorized:
        print("\n  [WARN] Tickers with NO subsector (fall back to 'Other'):")
        for t in uncategorized:
            print(f"    - {t} ({names.get(t, t)})")
        print(
            "\n  Fix: add each to SUBSECTOR_MAP in utils.js, or let the daily "
            "refresh (step_subsector_refresh -> update_subsectors.mjs) backfill "
            "it from snapshot/Finnhub."
        )
        if strict:
            return 1
    else:
        print("  All watchlist tickers are categorized. ✔")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    strict = "--strict" in args
    return run(strict=strict)


if __name__ == "__main__":
    raise SystemExit(main())
