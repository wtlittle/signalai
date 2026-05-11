"""
Private Company Refresh — enrichment for DEFAULT_PRIVATE_COMPANIES.

For every company in `utils.js DEFAULT_PRIVATE_COMPANIES` this job:

  1. Checks Supabase `private_intel` for a fresh row (skip if within TTL).
  2. Queues a Perplexity research prompt (build_private_company_prompt)
     that returns structured JSON with valuation, last funding round,
     ARR / revenue, investors, growth signals, IPO signals, competitive
     context, and HQ.
  3. Upserts the result (in API-fallback mode) keyed by (name, refresh_date).

The default path simply queues a task to Computer via pending_tasks.json;
Computer's processor writes the structured result back to Supabase.

Usage:
    # Dry-run
    python -m automation.jobs.private_company_refresh --dry-run

    # Live run
    SUPABASE_URL=https://xxx.supabase.co \
    SUPABASE_SERVICE_KEY=sb_secret_... \
    python -m automation.jobs.private_company_refresh

    # Single company
    python -m automation.jobs.private_company_refresh --name "OpenAI" --force
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR  # noqa: E402
from automation.perplexity.client import call_perplexity  # noqa: E402
from automation.perplexity.prompts import build_private_company_prompt  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TABLE                = "private_intel"
PRIVATE_TTL_DAYS     = int(os.environ.get("PRIVATE_INTEL_TTL_DAYS", "60"))
UTILS_JS             = ROOT_DIR / "utils.js"

SYSTEM_PROMPT = (
    "You are a senior buy-side equity research analyst covering private/pre-IPO "
    "companies. Return ONLY a single valid JSON object with the exact keys "
    "requested. No markdown fences, no commentary, no prose."
)


# ---------------------------------------------------------------------------
# Enumeration — parse utils.js DEFAULT_PRIVATE_COMPANIES
# ---------------------------------------------------------------------------
_PRIVATE_OBJ_RE = re.compile(r"\{\s*name:\s*'([^']+)'[^}]*?subsector:\s*'([^']+)'", re.DOTALL)


def _enumerate_private_companies() -> list[dict[str, str]]:
    """Parse utils.js DEFAULT_PRIVATE_COMPANIES into [{name, subsector}, ...]."""
    if not UTILS_JS.exists():
        print(f"[private_company_refresh] [ERROR] {UTILS_JS} not found")
        return []
    text = UTILS_JS.read_text(encoding="utf-8")
    # Slice to the DEFAULT_PRIVATE_COMPANIES literal
    start = text.find("DEFAULT_PRIVATE_COMPANIES")
    if start < 0:
        return []
    bracket = text.find("[", start)
    if bracket < 0:
        return []
    depth = 0
    end = bracket
    for i in range(bracket, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = text[bracket:end + 1]
    rows = []
    seen: set[str] = set()
    for m in _PRIVATE_OBJ_RE.finditer(block):
        name, subsector = m.group(1).strip(), m.group(2).strip()
        if not name or name in seen:
            continue
        # Skip entries that have already gone public (have ticker key)
        chunk = block[m.start():m.start() + 600]
        if "status: 'public'" in chunk:
            continue
        seen.add(name)
        rows.append({"name": name, "subsector": subsector})
    return rows


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def _supabase_client():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    import requests
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }
    return requests, url, headers


def _load_latest(requests_mod, url: str, headers: dict, name: str) -> dict | None:
    resp = requests_mod.get(
        f"{url}/rest/v1/{TABLE}",
        headers=headers,
        params={
            "name":   f"eq.{name}",
            "order":  "refresh_date.desc",
            "limit":  "1",
            "select": "*",
        },
        timeout=20,
    )
    if resp.status_code == 404 or resp.status_code >= 400:
        return None
    rows = resp.json() or []
    return rows[0] if rows else None


def _is_fresh(row: dict | None, ttl_days: int) -> bool:
    if not row:
        return False
    raw = row.get("refresh_date") or row.get("harvested_at")
    if not raw:
        return False
    try:
        d = _dt.date.fromisoformat(raw[:10])
    except ValueError:
        return False
    return (_dt.date.today() - d).days < ttl_days


def _upsert(requests_mod, url: str, headers: dict, row: dict) -> None:
    resp = requests_mod.post(
        f"{url}/rest/v1/{TABLE}?on_conflict=name,refresh_date",
        headers=headers,
        data=json.dumps(row),
        timeout=20,
    )
    if resp.status_code >= 300:
        print(f"  [WARN] Supabase upsert failed for {row.get('name')}: HTTP {resp.status_code} {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------
def run(
    dry_run: bool = False,
    force: bool = False,
    single_name: str | None = None,
) -> dict[str, Any]:
    companies = _enumerate_private_companies()
    if single_name:
        companies = [c for c in companies if c["name"].lower() == single_name.lower()]
        if not companies:
            companies = [{"name": single_name, "subsector": ""}]

    summary: dict[str, Any] = {
        "companies_total": len(companies),
        "queued":          0,
        "skipped_fresh":   0,
        "upserted":        0,
        "errors":          0,
        "dry_run":         dry_run,
    }

    print(f"[private_company_refresh] {len(companies)} private companies to refresh "
          f"(ttl={PRIVATE_TTL_DAYS}d, dry_run={dry_run}, force={force})")

    requests_mod = url = headers = None
    if not dry_run:
        try:
            requests_mod, url, headers = _supabase_client()
        except Exception as exc:
            print(f"[private_company_refresh] [WARN] Supabase setup failed: {exc}")

    today_iso = _dt.date.today().isoformat()

    for c in companies:
        name, subsector = c["name"], c.get("subsector")

        if not dry_run and requests_mod and not force:
            prev = _load_latest(requests_mod, url, headers, name)
            if _is_fresh(prev, PRIVATE_TTL_DAYS):
                summary["skipped_fresh"] += 1
                continue

        if dry_run:
            print(f"  [DRY] {name} ({subsector})")
            summary["queued"] += 1
            continue

        prompt = build_private_company_prompt(name=name, subsector=subsector)
        try:
            result = call_perplexity(
                ticker=name,                  # use name as the queue entity key
                task="private_company_refresh",
                prompt=prompt,
                system=SYSTEM_PROMPT,
                max_tokens=1100,
                extra_meta={
                    "supabase_table":  TABLE,
                    "name":            name,
                    "subsector_hint":  subsector,
                    "refresh_date":    today_iso,
                    "ttl_days":        PRIVATE_TTL_DAYS,
                },
            )
        except Exception as exc:
            summary["errors"] += 1
            print(f"  [ERROR] queue failed for {name}: {exc}")
            continue

        summary["queued"] += 1

        # Live API fallback path — direct upsert when the prompt returned a parsed dict.
        if not isinstance(result, dict):
            continue
        if result.get("queued") or result.get("dry_run") or result.get("skipped"):
            continue

        row = _build_row(result, name, subsector, today_iso)
        if requests_mod and url and headers:
            try:
                _upsert(requests_mod, url, headers, row)
                summary["upserted"] += 1
            except Exception as exc:
                summary["errors"] += 1
                print(f"  [ERROR] upsert failed for {name}: {exc}")

    print(
        f"[private_company_refresh] Done — "
        f"queued={summary['queued']} upserted={summary['upserted']} "
        f"skipped_fresh={summary['skipped_fresh']} errors={summary['errors']}"
    )
    return summary


def _build_row(result: dict, name: str, subsector: str | None, refresh_date: str) -> dict:
    return {
        "name":                result.get("name") or name,
        "subsector":           result.get("subsector") or subsector,
        "valuation":           result.get("valuation"),
        "last_funding_round":  result.get("last_funding_round"),
        "arr_or_revenue":      result.get("arr_or_revenue"),
        "investors":           result.get("investors") or [],
        "growth_signals":      result.get("growth_signals") or [],
        "ipo_signals":         result.get("ipo_signals"),
        "competitive_context": result.get("competitive_context"),
        "hq":                  result.get("hq"),
        "sources":             result.get("sources") or [],
        "refresh_date":        refresh_date,
        "harvested_at":        _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Refresh structured intelligence for private/pre-IPO companies.")
    p.add_argument("--dry-run", action="store_true", help="List companies without queueing tasks.")
    p.add_argument("--force",   action="store_true", help="Bypass freshness skip and re-queue every company.")
    p.add_argument("--name",    type=str, default=None, help="Restrict to a single private company name.")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(dry_run=args.dry_run, force=args.force, single_name=args.name)
