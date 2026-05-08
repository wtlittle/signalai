"""
Market Intel Harvest — Supabase-backed TAM / category-growth / AI-ML context
research per subsector.

For each unique subsector represented in the watchlist (DEFAULT_TICKERS via
SUBSECTOR_MAP in utils.js), this job queues a Perplexity research task that
returns structured JSON with:

    {
        "subsector": "...",
        "source": "...",                 # e.g. "Gartner", "IDC", "Statista"
        "tam_label": "$X.YB by 20XX",
        "tam_usd_bn": <float | null>,
        "growth_rate_label": "XX.X% CAGR through 20XX",
        "growth_rate_pct": <float | null>,
        "structural_drivers": "...",
        "ai_ml_context": "...",
        "raw_excerpt": "...",
        "harvested_at": "ISO-8601 timestamp"
    }

Each row is upserted into the Supabase `market_intel` table keyed by
(subsector, source). Rows older than HARVEST_TTL_DAYS are refreshed; fresher
rows are skipped to keep API/credit cost low. The `--dry-run` flag prints
which subsectors would be harvested without touching Supabase or queuing
tasks.

Usage:
    # Dry-run (lists subsectors only, no API/Supabase calls)
    python -m automation.jobs.market_intel_harvest --dry-run

    # Live run — queues Perplexity tasks + upserts to Supabase
    SUPABASE_URL=https://...supabase.co \\
    SUPABASE_SERVICE_KEY=sb_secret_... \\
    python -m automation.jobs.market_intel_harvest

Acceptance tests:
    python -m automation.jobs.market_intel_harvest --dry-run
    python -c "from automation.jobs.market_intel_harvest import run; print('ok')"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

# Make `automation` importable when run as a module or script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR  # noqa: E402
from automation.shared.tickers import load_tickers, load_subsector_map  # noqa: E402
from automation.perplexity.client import call_perplexity  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HARVEST_TTL_DAYS = int(os.environ.get("MARKET_INTEL_TTL_DAYS", "30"))
TABLE = "market_intel"

SYSTEM_PROMPT = (
    "You are a senior buy-side equity research analyst building a TAM and "
    "category-growth fact-base for a software/internet-sector watchlist. "
    "Return ONLY a single valid JSON object with the keys requested. "
    "No markdown fences, no commentary, no trailing prose. "
    "Every numeric field must be either a number or null. "
    "Cite the most credible primary or near-primary source per row "
    "(Gartner, IDC, Forrester, Statista, McKinsey, BCG, Bain, "
    "investor-day decks, 10-K MD&A, S-1 market sections)."
)


def _build_prompt(subsector: str) -> str:
    return (
        f"Research the global TAM, category growth rate, structural demand "
        f"drivers, and AI/ML adoption posture for the {subsector!r} subsector. "
        f"Time horizon is 2026-2030. "
        f"Return JSON with exactly these keys:\n\n"
        f'  "subsector": "{subsector}",\n'
        f'  "source": "<single best primary or near-primary research firm or filing>",\n'
        f'  "tam_label": "<one-line human-readable TAM, e.g. \'$185B by 2028\'>",\n'
        f'  "tam_usd_bn": <numeric TAM in USD billions, or null>,\n'
        f'  "growth_rate_label": "<one-line CAGR, e.g. \'12.4% CAGR through 2028\'>",\n'
        f'  "growth_rate_pct": <numeric CAGR as a percent, e.g. 12.4, or null>,\n'
        f'  "structural_drivers": "<2-4 sentences on what is structurally driving demand>",\n'
        f'  "ai_ml_context": "<2-4 sentences on AI/ML disruption: who is winning, what is at risk>",\n'
        f'  "raw_excerpt": "<verbatim 1-2 sentence quote or stat from the cited source>"\n\n'
        f"Use the most recent data you can verify. If a number cannot be sourced "
        f"with reasonable confidence, return null for that numeric field but still "
        f"populate the matching label with the best qualitative summary you have."
    )


# ---------------------------------------------------------------------------
# Subsector enumeration
# ---------------------------------------------------------------------------
def _enumerate_subsectors() -> list[str]:
    """Return the deduplicated list of subsectors covered by the watchlist."""
    tickers = load_tickers()
    subsector_map = load_subsector_map()
    subs: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        s = subsector_map.get(t)
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        subs.append(s)
    return sorted(subs)


# ---------------------------------------------------------------------------
# Supabase helpers (lazy import — keep dry-run dependency-free)
# ---------------------------------------------------------------------------
def _supabase_client():
    """Build a Supabase REST client using requests; raises if env not set."""
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for live runs"
        )
    import requests  # local import keeps dry-run dependency-free

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }
    return requests, url, headers


def _load_existing(requests_mod, url: str, headers: dict) -> dict[tuple[str, str], dict]:
    """Fetch all current market_intel rows so we can skip fresh ones."""
    resp = requests_mod.get(
        f"{url}/rest/v1/{TABLE}",
        headers=headers,
        params={"select": "subsector,source,harvested_at"},
        timeout=30,
    )
    if resp.status_code == 404:
        # Table doesn't exist yet — first run.
        return {}
    resp.raise_for_status()
    rows = resp.json() or []
    return {
        (r.get("subsector", ""), r.get("source", "")): r
        for r in rows
        if r.get("subsector")
    }


def _is_fresh(row: dict | None, ttl_days: int) -> bool:
    if not row:
        return False
    raw = row.get("harvested_at")
    if not raw:
        return False
    try:
        ts = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = _dt.datetime.now(_dt.timezone.utc) - ts.astimezone(_dt.timezone.utc)
    return age <= _dt.timedelta(days=ttl_days)


def _upsert(requests_mod, url: str, headers: dict, row: dict) -> None:
    """Upsert a single row keyed by (subsector, source)."""
    resp = requests_mod.post(
        f"{url}/rest/v1/{TABLE}?on_conflict=subsector,source",
        headers=headers,
        data=json.dumps(row),
        timeout=30,
    )
    if resp.status_code >= 300:
        print(
            f"  [WARN] Supabase upsert failed for "
            f"{row.get('subsector')}: HTTP {resp.status_code} {resp.text[:200]}"
        )


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------
def run(dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    """Harvest market intel for every watchlist subsector.

    Returns a summary dict for logging / downstream callers.
    """
    subsectors = _enumerate_subsectors()
    summary: dict[str, Any] = {
        "subsectors_total": len(subsectors),
        "queued": 0,
        "skipped_fresh": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    print(f"[market_intel_harvest] {len(subsectors)} subsectors queued for review "
          f"(ttl={HARVEST_TTL_DAYS}d, dry_run={dry_run}, force={force})")

    existing: dict[tuple[str, str], dict] = {}
    requests_mod = url = headers = None
    if not dry_run:
        try:
            requests_mod, url, headers = _supabase_client()
            existing = _load_existing(requests_mod, url, headers)
            print(f"[market_intel_harvest] Loaded {len(existing)} existing rows from Supabase")
        except Exception as exc:
            print(f"[market_intel_harvest] [WARN] Supabase pre-fetch failed: {exc}")
            existing = {}

    for sub in subsectors:
        # In live mode, skip if any existing row for this subsector is fresh.
        if not dry_run and not force:
            fresh_match = next(
                (r for (s, _src), r in existing.items() if s == sub and _is_fresh(r, HARVEST_TTL_DAYS)),
                None,
            )
            if fresh_match:
                summary["skipped_fresh"] += 1
                print(f"  [SKIP fresh] {sub} (last harvested {fresh_match.get('harvested_at')})")
                continue

        prompt = _build_prompt(sub)

        if dry_run:
            print(f"  [DRY RUN] Would harvest market intel for subsector: {sub}")
            summary["queued"] += 1
            continue

        print(f"  [QUEUE] {sub}")
        try:
            result = call_perplexity(
                ticker=sub,                 # the queue uses ticker as the entity key
                task="market_intel_harvest",
                prompt=prompt,
                system=SYSTEM_PROMPT,
                max_tokens=900,
                extra_meta={
                    "subsector": sub,
                    "supabase_table": TABLE,
                    "ttl_days": HARVEST_TTL_DAYS,
                },
            )
        except Exception as exc:
            summary["errors"] += 1
            print(f"  [ERROR] queue failed for {sub}: {exc}")
            continue

        summary["queued"] += 1

        # If the call_perplexity wrapper returned a real JSON result (API
        # fallback path), upsert it immediately. The default queue path
        # returns {"queued": True, ...} — Computer will run the task and the
        # processor is responsible for writing back to Supabase.
        if not isinstance(result, dict):
            continue
        if result.get("queued") or result.get("dry_run") or result.get("skipped"):
            continue

        # Result is a parsed JSON dict from the live API. Normalize and upsert.
        row = {
            "subsector": sub,
            "source": result.get("source") or "unknown",
            "tam_label": result.get("tam_label"),
            "tam_usd_bn": _coerce_float(result.get("tam_usd_bn")),
            "growth_rate_label": result.get("growth_rate_label"),
            "growth_rate_pct": _coerce_float(result.get("growth_rate_pct")),
            "structural_drivers": result.get("structural_drivers"),
            "ai_ml_context": result.get("ai_ml_context"),
            "raw_excerpt": result.get("raw_excerpt"),
            "harvested_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }
        if requests_mod and url and headers:
            try:
                _upsert(requests_mod, url, headers, row)
            except Exception as exc:
                summary["errors"] += 1
                print(f"  [ERROR] Supabase upsert failed for {sub}: {exc}")

    print(
        f"[market_intel_harvest] Done — "
        f"queued={summary['queued']} skipped_fresh={summary['skipped_fresh']} "
        f"errors={summary['errors']} subsectors_total={summary['subsectors_total']}"
    )
    return summary


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Harvest TAM / growth / AI-ML context per subsector.")
    p.add_argument("--dry-run", action="store_true", help="List subsectors without queuing tasks or touching Supabase.")
    p.add_argument("--force", action="store_true", help="Bypass the freshness skip and re-harvest every subsector.")
    return p.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    run(dry_run=args.dry_run, force=args.force)
