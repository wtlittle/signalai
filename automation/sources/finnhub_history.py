"""
Finnhub trailing-8-quarter earnings history fetcher.

Exports ``fetch_history_8q(symbol) -> list[dict] | None`` returning the trailing
8 fiscal quarters (most recent first) of:

    {period_end, fiscal_quarter, fiscal_year, revenue_actual, eps_actual,
     eps_estimate, eps_surprise_pct, rev_surprise_pct}

WHY a dedicated module (not automation/shared/finnhub.py):
  The shared helper only covers the free single-call endpoints (recommendation,
  insider, /stock/earnings). Building a clean trailing-8Q series needs to JOIN
  two Finnhub endpoints and reconstruct per-quarter revenue from cumulative
  fiscal-YTD filings -- enough logic to justify its own module.

DATA INTEGRITY (the zero-fabrication mandate):
  * Revenue is reconstructed by DIFFERENCING cumulative fiscal-YTD values from
    /stock/financials-reported (Q2 = H1cum - Q1, Q3 = 9Mcum - H1cum,
    Q4 = FYannual - 9Mcum). 10-Q filings report YTD, not stand-alone-quarter,
    figures for many issuers (e.g. ACN), so using the raw value would overstate
    the quarter. Only differences with BOTH operands present are emitted; any
    quarter we cannot reconstruct cleanly is left with revenue_actual=None.
  * eps_actual is GAAP diluted EPS, reconstructed by the SAME YTD-differencing
    as revenue. This is the only EPS basis Finnhub free tier exposes for 8+
    quarters on one consistent footing, so the whole series stays comparable.
    /stock/earnings (analyst/non-GAAP basis, latest ~4 quarters only) supplies
    eps_estimate and eps_surprise_pct, but ONLY for a quarter whose GAAP actual
    is within a small tolerance of the analyst actual -- i.e. only when the two
    bases agree. When the bases diverge (one-off items: AVGO's VMware quarters),
    estimate/surprise are left null rather than pairing a GAAP actual with a
    non-GAAP estimate, which would manufacture a meaningless beat/miss.
    /stock/earnings periods are calendar-quarter-ends and differ from issuer
    fiscal period-ends by up to ~6 weeks, so rows are matched by NEAREST
    period_end within a tolerance window, never blindly.
  * Finnhub free tier exposes no revenue consensus, so rev_surprise_pct is
    always None (rendered n/a) rather than fabricated.
  * Any failure (no key, network, non-200, too few quarters) returns None so the
    caller's fallback chain can continue.

Credential: FINNHUB_API_KEY env var ONLY. Never hardcoded, never read from disk.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime
from typing import Any

import requests

BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 10  # seconds (per the spec: single helper, 10s timeout)
_RETRY_STATUSES = {429, 500, 502, 503, 504}

# Concept names Finnhub uses for top-line revenue, in preference order. Issuers
# tag revenue under different us-gaap concepts; we take the first that resolves.
_REV_CONCEPTS = (
    "us-gaap_Revenues",
    "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap_RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap_SalesRevenueNet",
    "us-gaap_SalesRevenueGoodsNet",
)
_EPS_CONCEPTS = (
    "us-gaap_EarningsPerShareDiluted",
    "us-gaap_EarningsPerShareBasic",
)
# How close the analyst (/stock/earnings) actual must be to the GAAP-diff actual
# before we trust the analyst estimate/surprise as describing the SAME quarter on
# a comparable basis. Bigger gaps mean GAAP vs non-GAAP divergence (one-offs);
# we then withhold estimate/surprise rather than fabricate a cross-basis beat.
_EPS_BASIS_AGREE_ABS = 0.15   # absolute USD/share
_EPS_BASIS_AGREE_REL = 0.10   # or within 10% of the GAAP figure


class FinnhubHistoryError(RuntimeError):
    """Raised only for a missing credential (fail-fast per the spec)."""


def _require_key() -> str:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise FinnhubHistoryError(
            "FINNHUB_API_KEY is not set. fetch_history_8q requires a Finnhub "
            "API key in the environment (never hardcoded)."
        )
    return key


def _get(endpoint: str, params: dict[str, str]) -> Any | None:
    """Single Finnhub GET with 10s timeout and ONE retry on 429/5xx.

    Returns the parsed JSON body, or None on any non-retryable failure or after
    the retry is exhausted. Structured one-line logging on every failure.
    """
    key = _require_key()
    full = dict(params)
    full["token"] = key
    url = f"{BASE_URL}/{endpoint}"
    backoff = 1.0
    for attempt in range(2):  # initial try + one retry
        try:
            resp = requests.get(url, params=full, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            print(f"  [WARN] finnhub_history {endpoint} {params.get('symbol')}: "
                  f"request error (attempt {attempt + 1}): {exc}")
            if attempt == 0:
                time.sleep(backoff)
                continue
            return None
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                print(f"  [WARN] finnhub_history {endpoint} "
                      f"{params.get('symbol')}: bad JSON: {exc}")
                return None
        if resp.status_code in _RETRY_STATUSES and attempt == 0:
            print(f"  [WARN] finnhub_history {endpoint} {params.get('symbol')}: "
                  f"HTTP {resp.status_code}, retrying after {backoff}s")
            time.sleep(backoff)
            backoff *= 2
            continue
        print(f"  [WARN] finnhub_history {endpoint} {params.get('symbol')}: "
              f"HTTP {resp.status_code} {resp.text[:120]}")
        return None
    return None


def _concept_value(ic_rows: list[dict], concepts: tuple[str, ...]) -> float | None:
    for concept in concepts:
        for row in ic_rows:
            if row.get("concept") == concept:
                val = row.get("value")
                if isinstance(val, (int, float)):
                    return float(val)
    return None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:10]).date()
    except ValueError:
        return None


def _ytd_difference(
    cum_q: dict[tuple[int, int], float | None],
    annual: dict[int, dict],
    annual_key: str,
) -> dict[tuple[int, int], float]:
    """Convert cumulative fiscal-YTD values into stand-alone quarterly values.

    cum_q maps (fy, fq) -> cumulative value from the 10-Q; annual maps fy ->
    {period_end, <annual_key>} from the 10-K (for Q4 = FY - 9M). Only quarters
    whose operands are both present are emitted; others are silently dropped so
    we never publish a guessed quarter.
    """
    out: dict[tuple[int, int], float] = {}
    for (fy, fq), cum in cum_q.items():
        val: float | None = None
        if fq == 1:
            val = cum
        elif fq in (2, 3):
            prev = cum_q.get((fy, fq - 1))
            if cum is not None and prev is not None:
                val = cum - prev
        if val is not None:
            out[(fy, fq)] = val
    for fy, ainfo in annual.items():
        q3 = cum_q.get((fy, 3))
        ann_val = ainfo.get(annual_key)
        if ann_val is not None and q3 is not None:
            out[(fy, 4)] = ann_val - q3
    return out


def _reconstruct_quarterly(symbol: str) -> dict[tuple[int, int], dict]:
    """Return {(fiscal_year, fiscal_quarter): {period_end, revenue_actual,
    gaap_eps_actual}} with stand-alone-quarter figures (see module docstring).
    """
    quarterly = _get("stock/financials-reported", {"symbol": symbol, "freq": "quarterly"})
    annual = _get("stock/financials-reported", {"symbol": symbol, "freq": "annual"})
    if not isinstance(quarterly, dict):
        return {}

    cum_rev: dict[tuple[int, int], float | None] = {}
    cum_eps: dict[tuple[int, int], float | None] = {}
    cum_end: dict[tuple[int, int], date] = {}
    for rep in quarterly.get("data", []) or []:
        fy = rep.get("year")
        fq = rep.get("quarter")
        if not isinstance(fy, int) or fq not in (1, 2, 3, 4):
            continue
        end = _parse_date(rep.get("endDate"))
        if end is None:
            continue
        key = (fy, fq)
        if key in cum_end:  # keep the first (most recent) filing seen
            continue
        ic = (rep.get("report") or {}).get("ic") or []
        cum_end[key] = end
        cum_rev[key] = _concept_value(ic, _REV_CONCEPTS)
        cum_eps[key] = _concept_value(ic, _EPS_CONCEPTS)

    annual_rev: dict[int, dict] = {}
    annual_eps: dict[int, dict] = {}
    if isinstance(annual, dict):
        for rep in annual.get("data", []) or []:
            fy = rep.get("year")
            if not isinstance(fy, int) or fy in annual_rev:
                continue
            ic = (rep.get("report") or {}).get("ic") or []
            end = _parse_date(rep.get("endDate"))
            annual_rev[fy] = {"period_end": end, "rev": _concept_value(ic, _REV_CONCEPTS)}
            annual_eps[fy] = {"period_end": end, "eps": _concept_value(ic, _EPS_CONCEPTS)}

    rev_q = _ytd_difference(cum_rev, annual_rev, "rev")
    eps_q = _ytd_difference(cum_eps, annual_eps, "eps")

    # Period-end for a Q4 comes from the annual filing; Q1-Q3 from the 10-Q.
    out: dict[tuple[int, int], dict] = {}
    for key in set(rev_q) | set(eps_q):
        fy, fq = key
        if fq == 4:
            end = annual_rev.get(fy, {}).get("period_end")
        else:
            end = cum_end.get(key)
        if end is None:
            continue
        out[key] = {
            "period_end": end,
            "revenue_actual": rev_q.get(key),
            "gaap_eps_actual": eps_q.get(key),
        }
    return out


def _eps_rows(symbol: str) -> dict[tuple[int, int], dict]:
    """Per-quarter analyst EPS keyed by the row's own (fiscal_year, quarter).

    /stock/earnings rows carry explicit ``year`` and ``quarter`` labels that
    align with the issuer's fiscal calendar (AVGO's 2025-09-30 row is labelled
    Y2025/Q3). Keying on those labels -- rather than on calendar date proximity
    -- avoids mis-pairing an analyst row to an adjacent fiscal quarter when the
    fiscal period-end sits ~1 month off the calendar quarter-end.
    """
    data = _get("stock/earnings", {"symbol": symbol})
    if not isinstance(data, list):
        return {}
    rows: dict[tuple[int, int], dict] = {}
    for r in data:
        fy = r.get("year")
        fq = r.get("quarter")
        if not isinstance(fy, int) or fq not in (1, 2, 3, 4):
            continue
        rows.setdefault((fy, fq), {
            "eps_actual": r.get("actual"),
            "eps_estimate": r.get("estimate"),
            "eps_surprise_pct": r.get("surprisePercent"),
        })
    return rows


def fetch_history_8q(symbol: str) -> list[dict] | None:
    """Return the trailing 8 fiscal quarters for ``symbol`` (most recent first).

    Returns None if Finnhub fails or fewer than 8 quarters with BOTH revenue and
    EPS actuals can be assembled, so the caller's fallback chain can continue.
    """
    symbol = symbol.upper().strip()
    try:
        recon = _reconstruct_quarterly(symbol)
    except FinnhubHistoryError:
        raise
    except Exception as exc:  # never let a parsing edge case crash the chain
        print(f"  [WARN] finnhub_history {symbol}: reconstruction failed: {exc}")
        return None

    if not recon:
        return None

    eps_rows = _eps_rows(symbol)

    quarters: list[dict] = []
    for (fy, fq), info in recon.items():
        period_end = info["period_end"]
        gaap_eps = info["gaap_eps_actual"]
        eps_estimate = None
        eps_surprise = None
        # Overlay analyst estimate/surprise only when the analyst actual agrees
        # with the GAAP actual we are publishing (same basis -> the estimate
        # describes the same number). Otherwise withhold (no cross-basis beat).
        analyst = eps_rows.get((fy, fq))
        if analyst and gaap_eps is not None and analyst.get("eps_actual") is not None:
            a_act = analyst["eps_actual"]
            tol = max(_EPS_BASIS_AGREE_ABS, abs(gaap_eps) * _EPS_BASIS_AGREE_REL)
            if abs(a_act - gaap_eps) <= tol:
                eps_estimate = analyst.get("eps_estimate")
                eps_surprise = analyst.get("eps_surprise_pct")
        quarters.append({
            "period_end": period_end.isoformat(),
            "fiscal_quarter": fq,
            "fiscal_year": fy,
            "revenue_actual": round(info["revenue_actual"]) if info["revenue_actual"] is not None else None,
            "eps_actual": round(gaap_eps, 4) if gaap_eps is not None else None,
            "eps_estimate": round(eps_estimate, 4) if eps_estimate is not None else None,
            "eps_surprise_pct": round(eps_surprise, 4) if eps_surprise is not None else None,
            "rev_surprise_pct": None,  # no free-tier revenue consensus
        })

    quarters.sort(key=lambda q: q["period_end"], reverse=True)

    # Require 8 quarters that carry BOTH a revenue and an EPS actual; a half-full
    # series must never be presented as a complete trailing-8Q (mandate).
    complete = [
        q for q in quarters
        if q["revenue_actual"] is not None and q["eps_actual"] is not None
    ]
    if len(complete) < 8:
        print(f"  [WARN] finnhub_history {symbol}: only {len(complete)} fully "
              f"populated quarter(s) (need 8); deferring to next source")
        return None
    return complete[:8]
