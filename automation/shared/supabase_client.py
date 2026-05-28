"""
Shared Supabase REST client for automation scripts.

Uses raw requests (no supabase-py dependency) with service-role key.
Follows the same pattern established in market_intel_harvest.py.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

_DEFAULT_URL = "https://wcyirdvvuetzodiedzss.supabase.co"


def _get_creds() -> tuple[str, str]:
    url = (os.environ.get("SUPABASE_URL") or _DEFAULT_URL).rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return url, key


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def fetch_rows(
    table: str,
    params: dict[str, str] | None = None,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """GET rows from a Supabase table. Returns [] on failure."""
    url, key = _get_creds()
    if not key:
        print(f"  [WARN] SUPABASE_SERVICE_KEY not set -- skipping fetch from {table}")
        return []
    try:
        resp = requests.get(
            f"{url}/rest/v1/{table}",
            headers=_headers(key),
            params=params or {},
            timeout=timeout,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json() or []
    except Exception as exc:
        print(f"  [WARN] Supabase fetch from {table} failed: {exc}")
        return []


def insert_row(
    table: str,
    row: dict[str, Any],
    timeout: int = 30,
) -> bool:
    """Insert a single row (no upsert). Returns True on success."""
    url, key = _get_creds()
    if not key:
        return False
    hdrs = _headers(key)
    hdrs["Prefer"] = "return=minimal"
    try:
        resp = requests.post(
            f"{url}/rest/v1/{table}",
            headers=hdrs,
            data=json.dumps(row, default=str),
            timeout=timeout,
        )
        return resp.status_code < 300
    except Exception:
        return False


def upsert_row(
    table: str,
    row: dict[str, Any],
    on_conflict: str,
    timeout: int = 30,
) -> bool:
    """Upsert a single row. Returns True on success."""
    url, key = _get_creds()
    if not key:
        print(f"  [WARN] SUPABASE_SERVICE_KEY not set -- skipping upsert to {table}")
        return False
    hdrs = _headers(key)
    hdrs["Prefer"] = "return=representation,resolution=merge-duplicates"
    try:
        resp = requests.post(
            f"{url}/rest/v1/{table}?on_conflict={on_conflict}",
            headers=hdrs,
            data=json.dumps(row, default=str),
            timeout=timeout,
        )
        if resp.status_code >= 300:
            print(
                f"  [WARN] Supabase upsert to {table} failed: "
                f"HTTP {resp.status_code} {resp.text[:200]}"
            )
            return False
        return True
    except Exception as exc:
        print(f"  [WARN] Supabase upsert to {table} failed: {exc}")
        return False
