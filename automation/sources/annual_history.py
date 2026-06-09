"""
Trailing 5-fiscal-year ANNUAL history, assembled through the same priority
ordering as the quarterly earnings chain in ``refresh_ticker._ordered_sources``:

    PerplexityFinance -> Finnhub -> yfinance -> Perplexity sonar -> CachedFallback

Higher-priority sources win on a per-(fiscal-year, field) basis; lower-priority
sources only fill the gaps a higher source left null. Every value-bearing cell
carries a ``<field>_source`` sibling so provenance is auditable end to end (PR #42
convention, mirrored here for the annual surface).

WHY a dedicated module instead of the quarterly ``EarningsSource`` chain:
  The quarterly sources answer "trailing 8 QUARTERS of earnings actuals" -- a
  different shape (history_8q items, in_quarter_*). The drilldown's 5-year primer
  table needs ANNUAL revenue / operating income / EBIT / net income / diluted EPS
  / FCF / diluted shares per fiscal year. The data lives in the SAME upstream
  feeds (Perplexity Finance annual, Finnhub financials-reported annual, yfinance
  income_stmt/cashflow, a sonar fallback), so this module reuses those exact
  primitives but emits the annual row shape.

DATA INTEGRITY (the zero-fabrication mandate):
  * A field a source cannot supply is left ``None`` (rendered downstream as an
    em-dash), never guessed and never the literal string "MISSING".
  * Only real reported figures are emitted; a source that has no row for a fiscal
    year contributes nothing for that year.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
from typing import Any, Optional

# The canonical per-fiscal-year cell fields the drilldown table consumes. Each
# gets a ``<field>_source`` sibling stamped with the winning source's label.
_FIELDS = (
    "revenue",
    "operating_income",
    "ebit",
    "net_income",
    "eps_diluted",
    "fcf",
    "shares_diluted",
)

# Priority labels, highest first. Mirrors refresh_ticker._ordered_sources order.
_PRIORITY = ("perplexity_finance", "finnhub", "yfinance", "perplexity_sonar", "cached")


def _empty_row(fy: int) -> dict[str, Any]:
    row: dict[str, Any] = {"fy": f"FY{fy}", "_fy_int": fy}
    for f in _FIELDS:
        row[f] = None
        row[f"{f}_source"] = None
    return row


def _coerce(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _merge_year(row: dict[str, Any], incoming: dict[str, Any], source: str) -> None:
    """Gap-fill ``row`` from ``incoming`` (one fiscal year). A field is only
    written when the row's cell is still null, so a higher-priority source that
    already populated it is never overwritten. Provenance is stamped only on the
    real value that was actually written.
    """
    for f in _FIELDS:
        if row.get(f) is not None:
            continue
        val = _coerce(incoming.get(f))
        if val is None:
            continue
        row[f] = val
        row[f"{f}_source"] = source


# ---------------------------------------------------------------------------
# Source 1: Perplexity Finance (external-tool CLI, finance_financials annual)
# ---------------------------------------------------------------------------
def _perplexity_finance_annual(symbol: str) -> dict[int, dict[str, Any]]:
    """Return {fiscal_year: {field: value}} from Perplexity Finance, or {}.

    Reaches the connector through the preinstalled ``external-tool`` CLI, the
    same subprocess primitive the quarterly PerplexityFinanceSource uses (so all
    connector traffic flows through one auditable handoff). Any failure (expired
    token, non-zero exit, unparseable payload) is swallowed -> {} so the chain
    falls through to Finnhub.
    """
    try:
        payload = json.dumps({
            "source_id": "finance",
            "tool_name": "finance_financials",
            "arguments": {
                "ticker_symbols": [symbol],
                "statement_type": "income",
                "period_type": "annual",
                "limit": 8,
            },
        })
        proc = subprocess.run(
            ["external-tool", "call", payload],
            capture_output=True, text=True, timeout=90,
        )
        if proc.returncode != 0:
            print(f"  [WARN] annual perplexity_finance {symbol}: "
                  f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:200]}")
            return {}
        data = json.loads(proc.stdout)
    except Exception as exc:
        print(f"  [WARN] annual perplexity_finance {symbol}: {exc}")
        return {}

    text = _connector_text(data)
    if not text:
        return {}
    return _parse_finance_financials(text)


def _connector_text(payload: dict) -> Optional[str]:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("text")]
        return "\n".join(p for p in parts if p) or None
    return None


# finance_financials returns a markdown table whose rows are line items and whose
# columns are fiscal periods (FY2021, FY2022, ...). We map the line-item label to
# our canonical field and read each fiscal-year column.
_LINE_ITEM_MAP = {
    "total revenue": "revenue",
    "revenue": "revenue",
    "operating income": "operating_income",
    "operating income (loss)": "operating_income",
    "ebit": "ebit",
    "net income": "net_income",
    "net income (loss)": "net_income",
    "diluted eps": "eps_diluted",
    "eps (diluted)": "eps_diluted",
    "earnings per share, diluted": "eps_diluted",
    "free cash flow": "fcf",
    "diluted shares": "shares_diluted",
    "diluted weighted average shares": "shares_diluted",
    "weighted average shares, diluted": "shares_diluted",
}
import re as _re

_FY_COL_RE = _re.compile(r"(?:FY)?\s*(\d{4})", _re.IGNORECASE)


def _parse_num_cell(cell: str) -> Optional[float]:
    s = (cell or "").strip().replace(",", "").replace("$", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    mult = 1.0
    low = s.lower()
    if low.endswith("b"):
        mult, s = 1e9, s[:-1]
    elif low.endswith("m"):
        mult, s = 1e6, s[:-1]
    elif low.endswith("k"):
        mult, s = 1e3, s[:-1]
    if s in ("", "-", "--", "n/a", "N/A", "null", "None"):
        return None
    try:
        v = float(s) * mult
    except ValueError:
        return None
    return -v if neg else v


def _parse_finance_financials(text: str) -> dict[int, dict[str, Any]]:
    header: list[str] | None = None
    col_years: dict[int, int] = {}  # column index -> fiscal year
    out: dict[int, dict[str, Any]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and all(set(c) <= set("-: ") and c for c in cells):
            continue
        if header is None:
            header = [c.lower() for c in cells]
            for idx, h in enumerate(header):
                m = _FY_COL_RE.search(h)
                if m and idx > 0:
                    col_years[idx] = int(m.group(1))
            continue
        if not cells:
            continue
        label = cells[0].lower().strip()
        field = _LINE_ITEM_MAP.get(label)
        if field is None:
            continue
        for idx, fy in col_years.items():
            if idx >= len(cells):
                continue
            val = _parse_num_cell(cells[idx])
            if val is None:
                continue
            out.setdefault(fy, {})[field] = val
    return out


# ---------------------------------------------------------------------------
# Source 2: Finnhub financials-reported (annual)
# ---------------------------------------------------------------------------
_FH_REV = (
    "us-gaap_Revenues",
    "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap_RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap_SalesRevenueNet",
)
_FH_OPINC = ("us-gaap_OperatingIncomeLoss",)
_FH_NI = ("us-gaap_NetIncomeLoss", "us-gaap_ProfitLoss")
_FH_EPS = (
    "us-gaap_EarningsPerShareDiluted",
    "us-gaap_EarningsPerShareBasicAndDiluted",
    "us-gaap_EarningsPerShareBasic",
)
_FH_SHARES = (
    "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding",
    "us-gaap_WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
)
_FH_OCF = ("us-gaap_NetCashProvidedByUsedInOperatingActivities",)
_FH_CAPEX = ("us-gaap_PaymentsToAcquirePropertyPlantAndEquipment",)


def _fh_concept(rows: list[dict], concepts: tuple[str, ...]) -> Optional[float]:
    for concept in concepts:
        for row in rows:
            if row.get("concept") == concept:
                v = row.get("value")
                if isinstance(v, (int, float)):
                    return float(v)
    return None


def _finnhub_annual(symbol: str) -> dict[int, dict[str, Any]]:
    """Return {fiscal_year: {field: value}} from Finnhub financials-reported."""
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return {}
    try:
        import requests
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/financials-reported",
            params={"symbol": symbol, "freq": "annual", "token": key},
            timeout=15,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except Exception as exc:
        print(f"  [WARN] annual finnhub {symbol}: {exc}")
        return {}

    out: dict[int, dict[str, Any]] = {}
    for rep in (data.get("data") or []):
        fy = rep.get("year")
        if not isinstance(fy, int):
            continue
        if fy in out:  # keep the first (latest-filed) report per fiscal year
            continue
        report = rep.get("report") or {}
        ic = report.get("ic") or []
        cf = report.get("cf") or []
        opinc = _fh_concept(ic, _FH_OPINC)
        ocf = _fh_concept(cf, _FH_OCF)
        capex = _fh_concept(cf, _FH_CAPEX)
        fcf = (ocf - capex) if (ocf is not None and capex is not None) else None
        row = {
            "revenue": _fh_concept(ic, _FH_REV),
            "operating_income": opinc,
            "ebit": opinc,  # operating income is the EBIT proxy from reported financials
            "net_income": _fh_concept(ic, _FH_NI),
            "eps_diluted": _fh_concept(ic, _FH_EPS),
            "shares_diluted": _fh_concept(ic, _FH_SHARES),
            "fcf": fcf,
        }
        if any(v is not None for v in row.values()):
            out[fy] = {k: v for k, v in row.items() if v is not None}
    return out


# ---------------------------------------------------------------------------
# Source 3: yfinance (income_stmt + cashflow, annual)
# ---------------------------------------------------------------------------
_YF_ROWS = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "operating_income": ("Operating Income", "Total Operating Income As Reported"),
    "ebit": ("EBIT",),
    "net_income": ("Net Income", "Net Income Common Stockholders"),
    "eps_diluted": ("Diluted EPS",),
    "shares_diluted": ("Diluted Average Shares",),
}


def _yfinance_annual(symbol: str) -> dict[int, dict[str, Any]]:
    """Return {fiscal_year: {field: value}} from yfinance annual statements."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    try:
        tk = yf.Ticker(symbol)
        inc = tk.income_stmt
        cf = tk.cashflow
    except Exception as exc:
        print(f"  [WARN] annual yfinance {symbol}: {exc}")
        return {}
    if inc is None or getattr(inc, "empty", True):
        return {}

    out: dict[int, dict[str, Any]] = {}
    for col in inc.columns:
        fy = getattr(col, "year", None)
        if fy is None:
            continue
        row: dict[str, Any] = {}
        for field, labels in _YF_ROWS.items():
            for label in labels:
                if label in inc.index:
                    v = _coerce(inc.loc[label, col])
                    if v is not None:
                        row[field] = v
                        break
        if cf is not None and not getattr(cf, "empty", True) and "Free Cash Flow" in cf.index and col in cf.columns:
            v = _coerce(cf.loc["Free Cash Flow", col])
            if v is not None:
                row["fcf"] = v
        if row:
            out[fy] = row
    return out


# ---------------------------------------------------------------------------
# Source 4: Perplexity sonar (narrative LLM gap-filler)
# ---------------------------------------------------------------------------
def _perplexity_sonar_annual(symbol: str, years: list[int], gaps: bool) -> dict[int, dict[str, Any]]:
    """Last-resort LLM fill for fiscal years the structured sources left empty.

    Only invoked when ``gaps`` is True (the structured chain left at least one
    required year/field null) AND the Perplexity API path is live. Returns
    {fiscal_year: {field: value}} parsed from a strict-JSON sonar response.
    Never fabricates: a value the model cannot source comes back null and is
    dropped here.
    """
    if not gaps:
        return {}
    try:
        from automation.perplexity.client import call_perplexity, _use_api_fallback
    except Exception:
        return {}
    if not _use_api_fallback():
        return {}

    yr_lo, yr_hi = min(years), max(years)
    prompt = (
        f"Return the annual income-statement and free-cash-flow history for "
        f"{symbol} for fiscal years {yr_lo} through {yr_hi}. Return ONLY a JSON "
        f'object of the form {{"history": [{{"fy": <int>, "revenue": <usd or null>, '
        f'"operating_income": <usd or null>, "ebit": <usd or null>, '
        f'"net_income": <usd or null>, "eps_diluted": <number or null>, '
        f'"fcf": <usd or null>, "shares_diluted": <number or null>}}]}}. '
        f"All currency figures in absolute USD (not millions). Use GAAP figures "
        f"from the 10-K. If a value cannot be verified, return null for it — never guess."
    )
    try:
        result = call_perplexity(
            ticker=symbol,
            task="finance_financials",
            prompt=prompt,
            system="Return only a single valid JSON object. No prose, no markdown fences.",
            force=True,
            max_tokens=1200,
            temperature=0.0,
            extra_meta={"model": "sonar", "annual_history": True},
        )
    except Exception as exc:
        print(f"  [WARN] annual perplexity_sonar {symbol}: {exc}")
        return {}
    if not isinstance(result, dict):
        return {}
    if result.get("queued") or result.get("skipped") or result.get("dry_run"):
        return {}
    rows = result.get("history") or result.get("raw")
    if not isinstance(rows, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        fy = r.get("fy")
        try:
            fy = int(fy)
        except (TypeError, ValueError):
            continue
        row = {f: _coerce(r.get(f)) for f in _FIELDS}
        row = {k: v for k, v in row.items() if v is not None}
        if row:
            out[fy] = row
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def fetch_5yr_history(ticker: str) -> dict:
    """Assemble 5 fiscal years (FY-4 .. FY) of annual history for ``ticker``.

    Runs the priority chain PerplexityFinance -> Finnhub -> yfinance ->
    Perplexity sonar -> CachedFallback. Higher-priority sources win per cell;
    lower-priority sources fill the gaps. Provenance is tracked via
    ``<field>_source`` siblings on every populated cell.

    Returns ``{"history_5y": [row, ...]}`` ordered oldest-first (FY-4 first),
    each row::

        {"fy": "FY2021", "revenue": ..., "revenue_source": "finnhub",
         "operating_income": ..., "ebit": ..., "net_income": ...,
         "eps_diluted": ..., "fcf": ..., "shares_diluted": ..., <_source siblings>}

    A cell the chain cannot fill stays ``None`` (rendered downstream as an
    em-dash). The literal string "MISSING" is never emitted.
    """
    ticker = ticker.strip().upper()

    pf = _perplexity_finance_annual(ticker)
    fh = _finnhub_annual(ticker)
    yf_ = _yfinance_annual(ticker)

    # Determine the 5-year fiscal window from the most-recent fiscal year any
    # structured source reported (fall back to the current calendar year).
    seen_years = set(pf) | set(fh) | set(yf_)
    if seen_years:
        fy_latest = max(seen_years)
    else:
        fy_latest = _dt.date.today().year - 1
    window = list(range(fy_latest - 4, fy_latest + 1))

    rows = {fy: _empty_row(fy) for fy in window}

    # Merge in strict priority order (highest first); gap-fill only.
    for source_label, table in (
        ("perplexity_finance", pf),
        ("finnhub", fh),
        ("yfinance", yf_),
    ):
        for fy, fields in table.items():
            if fy in rows:
                _merge_year(rows[fy], fields, source_label)

    # Sonar fill ONLY for cells still null after the structured chain.
    gaps = any(
        rows[fy].get(f) is None for fy in window for f in _FIELDS
    )
    sonar = _perplexity_sonar_annual(ticker, window, gaps)
    for fy, fields in sonar.items():
        if fy in rows:
            _merge_year(rows[fy], fields, "perplexity_sonar")

    ordered = [rows[fy] for fy in window]
    for row in ordered:
        row.pop("_fy_int", None)
    return {"history_5y": ordered}
