"""
Perplexity Finance earnings source (priority 1, the structured-data workhorse).

Replaces the FactSet stub at the head of the chain. The Perplexity Finance
connector exposes a ``finance_earnings_history`` tool that returns the trailing
quarters of actual revenue / EPS plus the consensus the quarter was measured
against -- exactly the ``history_8q`` array (and the most-recent ``in_quarter_*``
actuals) that took down the 2026-06-04 universe refresh (74 tickers quarantined
on a null/short ``history_8q``).

WHY shell out to the ``external-tool`` CLI instead of an SDK:
  The cron runs the pipeline as plain ``python3 -m automation.jobs.daily_refresh``
  -- there is no agent runtime in scope, so the connector is reached through the
  preinstalled ``external-tool`` CLI (subprocess), which carries the
  ``external-tools`` credential preset. This mirrors the documented
  programmatic-tool-calling pattern and keeps every connector byte flowing
  through one helper (``_call_finance``).

DATA INTEGRITY (the zero-fabrication mandate):
  * Only fully-reported quarters land in ``history_8q``. A row whose actual
    revenue or actual EPS cell is empty (the next, not-yet-reported quarter) is
    SKIPPED, never zero-filled.
  * A quarter is emitted with whatever the source supplied; a missing estimate or
    surprise cell stays ``None`` rather than being reconstructed cross-basis.
  * Fewer than 8 fully-populated quarters -> ``history_8q`` is omitted (left for
    the next source / the completeness gate), never padded.
  * Any failure (no credential, CLI non-zero, unparseable payload) is swallowed
    and the source returns ``None`` so the chain falls through to Finnhub.

PROVENANCE: every value-bearing field this source supplies is stamped
``<field>_source = "perplexity_finance"`` via ``self.tag`` / explicit siblings.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Optional

from automation.pipeline.schema import min_required_quarters
from automation.sources.base import EarningsSource, SourceResponse

_SOURCE_ID = "finance"
_TIMEOUT = 60  # seconds; the external-tools token is short-lived, fail fast.

# Verified column headers from finance_earnings_history. The markdown table the
# connector returns uses these exact (camelCase) header cells; we map them to the
# canonical history_8q item shape (see automation/sources/finnhub_history.py).
_COL_PERIOD = "period"
_COL_DATE = "date"
_COL_ACT_REV = "actualrevenue"
_COL_EST_REV = "estimatedrevenue"
_COL_REV_SURPRISE = "revenuesurprise"
_COL_ACT_EPS = "actualeps"
_COL_EST_EPS = "estimatedeps"
_COL_EPS_SURPRISE = "epssurprise"

# "Q1 2026" / "Q4 2025" -> (fiscal_quarter, fiscal_year).
_PERIOD_RE = re.compile(r"Q([1-4])\s+(\d{4})", re.IGNORECASE)


def _call_finance(tool_name: str, args: dict, timeout: int = _TIMEOUT) -> dict:
    """Call one Perplexity Finance tool through the external-tool CLI.

    Raises RuntimeError on a non-zero exit so the caller can decide whether the
    whole source should defer (return None). The returned dict is the connector's
    JSON payload (top-level ``content`` markdown + ``csv_files`` presigned URLs).
    """
    payload = json.dumps({
        "source_id": _SOURCE_ID,
        "tool_name": tool_name,
        "arguments": args,
    })
    proc = subprocess.run(
        ["external-tool", "call", payload],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"external-tool {tool_name} exit {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return json.loads(proc.stdout)


def _content_text(payload: dict) -> Optional[str]:
    """Extract the markdown ``content`` string from a connector payload.

    Connectors return ``content`` either as a plain string or as a list of
    ``{type, text}`` blocks; accept both rather than assuming one shape.
    """
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            blk.get("text", "")
            for blk in content
            if isinstance(blk, dict) and blk.get("text")
        ]
        joined = "\n".join(p for p in parts if p)
        return joined or None
    return None


def _parse_number(cell: str) -> Optional[float]:
    """Parse a numeric markdown cell to float, or None for an empty/dash cell.

    Tolerates thousands separators and a leading currency/sign; an empty cell or
    a placeholder dash means "not reported" -> None (never 0, never fabricated).
    """
    if cell is None:
        return None
    s = cell.strip().replace(",", "").replace("$", "")
    if s in ("", "-", "--", "n/a", "N/A", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_markdown_table(text: str) -> list[dict[str, str]]:
    """Parse a GitHub-style markdown table into a list of header-keyed row dicts.

    Header keys are lower-cased and stripped so lookups are case-insensitive. The
    separator row (``| --- | --- |``) and any non-table prose are ignored. Cells
    are kept as raw strings; numeric coercion happens at the mapping layer.
    """
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # A separator row is all dashes/colons -> skip, do not treat as data.
        if cells and all(set(c) <= set("-: ") and c for c in cells):
            continue
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def _history_from_rows(rows: list[dict[str, str]]) -> list[dict]:
    """Map parsed table rows to the canonical history_8q item shape.

    Only rows with BOTH an actual revenue and an actual EPS are emitted (a future
    quarter has empty actuals). Output is sorted most-recent-first to match the
    finnhub_history contract.
    """
    out: list[dict] = []
    for row in rows:
        act_rev = _parse_number(row.get(_COL_ACT_REV, ""))
        act_eps = _parse_number(row.get(_COL_ACT_EPS, ""))
        # Not-yet-reported quarter (empty actuals) -> skip, never zero-fill.
        if act_rev is None or act_eps is None:
            continue

        period_raw = (row.get(_COL_PERIOD) or "").strip()
        fq: Optional[int] = None
        fy: Optional[int] = None
        m = _PERIOD_RE.search(period_raw)
        if m:
            fq = int(m.group(1))
            fy = int(m.group(2))

        # period_end: keep the ISO date portion of the report timestamp.
        date_raw = (row.get(_COL_DATE) or "").strip()
        period_end = date_raw[:10] if date_raw else period_raw

        est_eps = _parse_number(row.get(_COL_EST_EPS, ""))
        eps_surprise_abs = _parse_number(row.get(_COL_EPS_SURPRISE, ""))
        # epsSurprise is reported as an absolute USD/share delta; convert to the
        # percent the schema field (eps_surprise_pct) expects, but only when the
        # estimate is a non-zero base (otherwise leave null rather than divide).
        eps_surprise_pct: Optional[float] = None
        if eps_surprise_abs is not None and est_eps not in (None, 0):
            eps_surprise_pct = round(eps_surprise_abs / abs(est_eps) * 100.0, 4)

        est_rev = _parse_number(row.get(_COL_EST_REV, ""))
        rev_surprise_abs = _parse_number(row.get(_COL_REV_SURPRISE, ""))
        rev_surprise_pct: Optional[float] = None
        if rev_surprise_abs is not None and est_rev not in (None, 0):
            rev_surprise_pct = round(rev_surprise_abs / abs(est_rev) * 100.0, 4)

        out.append({
            "period_end": period_end,
            "fiscal_quarter": fq,
            "fiscal_year": fy,
            "revenue_actual": round(act_rev),
            "eps_actual": round(act_eps, 4),
            "eps_estimate": round(est_eps, 4) if est_eps is not None else None,
            "eps_surprise_pct": eps_surprise_pct,
            "rev_surprise_pct": rev_surprise_pct,
        })

    out.sort(key=lambda q: q["period_end"], reverse=True)
    return out


class PerplexityFinanceSource(EarningsSource):
    """Highest-priority source: Perplexity Finance structured earnings data.

    Supplies ``history_8q`` (trailing 8 fully-reported quarters) and the
    most-recent quarter's ``in_quarter_*`` actuals/consensus/surprise. Forward
    consensus is left to the qualitative Perplexity source; this source only
    publishes figures it can read directly from ``finance_earnings_history``.
    """

    name = "perplexity_finance"

    def fetch(self, symbol: str) -> Optional[SourceResponse]:
        symbol = symbol.upper().strip()
        try:
            # NOTE: tool expects ticker_symbols (array) and limit must exceed 8
            # because the first N rows are future/non-yet-reported quarters
            # (filtered out below). Some tickers publish consensus rows up to 3
            # quarters out (e.g. PSTG had 3 future rows + 7 actuals at limit=10),
            # so we request 16 to guarantee at least 8 fully-reported quarters
            # land in history_8q for all tickers in the universe.
            payload = _call_finance(
                "finance_earnings_history",
                {"ticker_symbols": [symbol], "period_type": "quarterly", "limit": 16},
            )
        except Exception as exc:  # never raise into the runner thread.
            print(f"  [WARN] perplexity_finance {symbol}: earnings_history "
                  f"call failed: {exc}")
            return None

        text = _content_text(payload)
        if not text:
            print(f"  [WARN] perplexity_finance {symbol}: empty content")
            return None

        rows = _parse_markdown_table(text)
        if not rows:
            print(f"  [WARN] perplexity_finance {symbol}: no table rows parsed")
            return None

        history = _history_from_rows(rows)

        fields: dict[str, Any] = {}
        missing: list[str] = []

        # --- trailing 8Q ---
        # Normally 8; an allowlisted recent IPO (SHORT_HISTORY_TICKERS) accepts
        # its max-available count. Every emitted quarter is still fully populated
        # -- we publish fewer real quarters, never a padded/fabricated one.
        required = min_required_quarters(symbol)
        if len(history) >= required:
            fields["history_8q"] = history[:required]
            fields["history_8q_source"] = self.name
        else:
            # A short series is not published as a complete history; defer the field.
            missing.append("history_8q")

        # --- most-recent in-quarter actuals/consensus/surprise ---
        if history:
            latest = history[0]
            rvc: dict[str, Any] = {}
            if latest.get("revenue_actual") is not None:
                rvc["in_quarter_rev_actual"] = latest["revenue_actual"]
                rvc["in_quarter_rev_actual_source"] = self.name
                rvc["rev_actual_source"] = self.name
            if latest.get("eps_actual") is not None:
                rvc["in_quarter_eps_actual"] = latest["eps_actual"]
                rvc["in_quarter_eps_actual_source"] = self.name
                rvc["eps_actual_source"] = self.name
            if latest.get("eps_estimate") is not None:
                rvc["in_quarter_eps_estimate"] = latest["eps_estimate"]
                rvc["in_quarter_eps_consensus"] = latest["eps_estimate"]
                rvc["eps_consensus_source"] = self.name
            if latest.get("eps_surprise_pct") is not None:
                rvc["in_quarter_eps_surprise_pct"] = latest["eps_surprise_pct"]
                rvc["eps_surprise_source"] = self.name
            if latest.get("rev_surprise_pct") is not None:
                rvc["in_quarter_rev_surprise_pct"] = latest["rev_surprise_pct"]
                rvc["rev_surprise_source"] = self.name
            if rvc:
                fields["results_vs_consensus"] = rvc
        else:
            missing.append("in_quarter_eps_actual")

        if not fields:
            return None
        return self.response(symbol, fields, missing=missing)
