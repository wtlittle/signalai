"""
Industry-pack fetcher: per-ticker grounded market intel for prompt assembly.

``fetch_industry_pack(ticker)`` reads the most recent ``market_intel_ticker``
row (TAM, category CAGR, drivers, competitors) populated by the weekly
``automation.jobs.market_intel_refresh`` harvester and normalizes it into the
shape the post-earnings prompt consumes.

DATA INTEGRITY: this layer never invents a figure. A row absent from Supabase
returns None; a row whose ``updated_at`` is older than ``STALE_DAYS`` (14) is
returned with ``is_stale=True`` so the prompt can tell the model NOT to cite the
numbers. Numerics arrive from the harvester already coerced to number-or-null.

The harvester stores a per-row ``tam_source_url`` and a ``source_url`` on each
competitor; there is no single ``source_urls`` column. We synthesize the
``source_urls`` list (deduped, order-preserved) from those fields so downstream
callers have one provenance list to cite.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from automation.shared.supabase_client import fetch_rows

TABLE = "market_intel_ticker"
STALE_DAYS = 14


def _parse_ts(raw: Any) -> _dt.datetime | None:
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_stale(updated_at: Any) -> bool:
    """True when the row is missing a timestamp or older than STALE_DAYS."""
    ts = _parse_ts(updated_at)
    if ts is None:
        return True
    age = _dt.datetime.now(_dt.timezone.utc) - ts.astimezone(_dt.timezone.utc)
    return age > _dt.timedelta(days=STALE_DAYS)


def _collect_source_urls(row: dict) -> list[str]:
    """Deduped, order-preserved provenance list from TAM + competitor URLs."""
    urls: list[str] = []
    seen: set[str] = set()
    for candidate in [row.get("tam_source_url")]:
        if candidate and candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
    for comp in row.get("competitors") or []:
        if not isinstance(comp, dict):
            continue
        u = comp.get("source_url")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _normalize_competitors(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name:
            continue
        out.append({
            "name": str(name),
            "ticker": c.get("ticker"),
            "quadrant": c.get("quadrant"),
            "threat": c.get("threat"),
            "source_url": c.get("source_url"),
        })
    return out


def _normalize_drivers(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    return [str(d).strip() for d in raw if str(d).strip()]


def fetch_industry_pack(ticker: str) -> dict | None:
    """Return the normalized industry pack for ``ticker``, or None if absent.

    The returned dict has the keys::

        ticker, tam_usd_bn, tam_source_url, category_cagr_pct, drivers,
        competitors, updated_at, source_urls, is_stale

    ``is_stale`` is True when ``updated_at`` is older than 14 days (the
    note-freshness bar, independent of the harvester's 90-day refresh TTL). A
    missing Supabase row -- or any fetch failure -- yields None; the caller
    renders an em-dash / "n/a" block rather than inventing numbers.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    rows = fetch_rows(
        TABLE,
        params={
            "ticker": f"eq.{ticker}",
            "select": ("ticker,tam_usd_bn,tam_source_url,category_cagr_pct,"
                       "drivers,competitors,updated_at"),
            "order": "updated_at.desc",
            "limit": "1",
        },
    )
    if not rows:
        return None

    row = rows[0]
    return {
        "ticker": ticker,
        "tam_usd_bn": row.get("tam_usd_bn"),
        "tam_source_url": row.get("tam_source_url"),
        "category_cagr_pct": row.get("category_cagr_pct"),
        "drivers": _normalize_drivers(row.get("drivers")),
        "competitors": _normalize_competitors(row.get("competitors")),
        "updated_at": row.get("updated_at"),
        "source_urls": _collect_source_urls(row),
        "is_stale": _is_stale(row.get("updated_at")),
    }


if __name__ == "__main__":
    import json
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "SNOW"
    print(json.dumps(fetch_industry_pack(sym), indent=2, default=str))
