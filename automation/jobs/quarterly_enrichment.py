"""
Quarterly trend enrichment for the Compare tab sparklines.

Fetches trailing-quarter income + cashflow statements via yfinance (which
handles Yahoo's Crumb cookie auth that anonymous browser fetches can no longer
satisfy) and derives three series per ticker:

  * revGrowth - revenue growth %, YoY (4 quarters back) where >=4 quarters of
                history exist, else QoQ for the early indices.
  * opMargin  - operating income / total revenue * 100
  * fcfMargin - (operating cash flow + capex) / total revenue * 100
                (capex is negative on Yahoo, so adding it yields free cash flow)

The math mirrors the client-side derivation in compare.js fetchQuarterly so the
snapshot-served series are identical to what the live path used to produce.

Results are attached to data-snapshot.json under BOTH:
  * tickers.<T>.quarterly  (per the snapshot ticker-payload convention)
  * quotes.<T>.quarterly   (so it flows into the dashboard row via
                            parseTickerData, where compare.js reads it)

Never fabricates: a ticker that fails or has no usable data gets quarterly=None.
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yfinance as yf

from automation.shared.paths import DATA_SNAPSHOT
from automation.shared.io_helpers import read_json

MAX_WORKERS = 8
MAX_QUARTERS = 6

# Statement row labels, in preference order. yfinance occasionally renames rows
# between vendors/versions, so we probe a small ordered list and take the first
# row that exists with usable values.
REVENUE_ROWS = ("Total Revenue", "Operating Revenue")
OP_INCOME_ROWS = ("Operating Income", "Total Operating Income As Reported")
OP_CASH_ROWS = ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
CAPEX_ROWS = ("Capital Expenditure",)


def _series_from_frame(frame, row_labels):
    """Return the first matching row from a yfinance statement DataFrame as a
    chronological (oldest -> newest) list of floats/None.

    yfinance returns columns newest-first, so we reverse. Missing or NaN cells
    become None. Returns None if no candidate row is present.
    """
    if frame is None or getattr(frame, "empty", True):
        return None
    for label in row_labels:
        if label in frame.index:
            row = frame.loc[label]
            vals = list(row)[::-1]
            out = []
            for v in vals:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    out.append(None)
                    continue
                # NaN check without importing numpy/math explicitly.
                out.append(None if fv != fv else fv)
            return out
    return None


def _align(primary, secondary):
    """Pad the shorter list on the LEFT with None so both lists end aligned at
    the most recent quarter. Income and cashflow statements can have different
    quarter counts; aligning on the newest quarter keeps per-index math
    (margins) comparing the same period.
    """
    if primary is None:
        primary = []
    if secondary is None:
        secondary = []
    n = max(len(primary), len(secondary))
    pad_p = [None] * (n - len(primary)) + list(primary)
    pad_s = [None] * (n - len(secondary)) + list(secondary)
    return pad_p, pad_s


def compute_quarterly(ticker):
    """Fetch + compute the three series for one ticker.

    Returns a dict {revGrowth, fcfMargin, opMargin} with each list having at
    most MAX_QUARTERS entries (nulls dropped, last 6 kept), or None when no
    usable data is available. Raises on hard yfinance failures so the caller can
    log per-ticker.
    """
    tkr = yf.Ticker(ticker)

    income = None
    try:
        income = tkr.quarterly_income_stmt
    except Exception:
        income = None
    if income is None or getattr(income, "empty", True):
        try:
            income = tkr.quarterly_financials
        except Exception:
            income = None

    try:
        cashflow = tkr.quarterly_cashflow
    except Exception:
        cashflow = None

    revenue = _series_from_frame(income, REVENUE_ROWS)
    op_income = _series_from_frame(income, OP_INCOME_ROWS)
    op_cash = _series_from_frame(cashflow, OP_CASH_ROWS)
    capex = _series_from_frame(cashflow, CAPEX_ROWS)

    if not revenue or all(v is None for v in revenue):
        return None

    # Align margin inputs to revenue's quarter axis (newest-aligned).
    revenue, op_income = _align(revenue, op_income)
    _, op_cash = _align(revenue, op_cash)
    _, capex = _align(revenue, capex)

    rev_growth = []
    for i, rev in enumerate(revenue):
        if rev is None:
            rev_growth.append(None)
            continue
        if i >= 4:
            prior = revenue[i - 4]
        elif i >= 1:
            prior = revenue[i - 1]
        else:
            prior = None
        if prior is None or prior == 0:
            rev_growth.append(None)
        else:
            rev_growth.append(((rev - prior) / abs(prior)) * 100)

    op_margin = []
    for i, rev in enumerate(revenue):
        oi = op_income[i] if i < len(op_income) else None
        if rev and oi is not None:
            op_margin.append((oi / rev) * 100)
        else:
            op_margin.append(None)

    fcf_margin = []
    for i, rev in enumerate(revenue):
        oc = op_cash[i] if i < len(op_cash) else None
        cx = capex[i] if i < len(capex) else None
        if not rev or oc is None:
            fcf_margin.append(None)
        else:
            fcf = oc + (cx or 0)  # capex is negative on Yahoo
            fcf_margin.append((fcf / rev) * 100)

    series = {
        "revGrowth": [v for v in rev_growth if v is not None][-MAX_QUARTERS:],
        "fcfMargin": [v for v in fcf_margin if v is not None][-MAX_QUARTERS:],
        "opMargin": [v for v in op_margin if v is not None][-MAX_QUARTERS:],
    }
    if not any(series.values()):
        return None
    return series


def enrich(tickers, snapshot_path=None, max_workers=MAX_WORKERS):
    """Compute quarterly series for every ticker and write them into the
    snapshot. Per-ticker failures are isolated and logged; the rest of the
    universe is unaffected.

    Returns (ok_count, fail_list) where fail_list is [(ticker, reason), ...].
    """
    path = Path(snapshot_path) if snapshot_path else DATA_SNAPSHOT
    snapshot = read_json(path)
    # read_json returns {} on ANY read/parse failure. An empty dict here means
    # the on-disk snapshot is missing or corrupt -- writing back would clobber a
    # valid ~8MB file with a near-empty one. Bail without writing instead.
    if not isinstance(snapshot, dict) or not snapshot:
        print("  [WARN] snapshot missing or unreadable; skipping quarterly enrichment (no write)")
        return 0, [(t, "snapshot-unreadable") for t in tickers]

    results = {}
    failures = []

    def _job(t):
        return t, compute_quarterly(t)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_job, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                _t, series = fut.result()
                results[t] = series
            except Exception as exc:  # noqa: BLE001
                failures.append((t, str(exc)[:120]))
                results[t] = None

    snapshot.setdefault("tickers", {})
    snapshot.setdefault("quotes", {})

    ok = 0
    for t in tickers:
        series = results.get(t)
        # Attach to the chart-payload section (per snapshot convention) AND the
        # quote section (the row source the client actually reads). Do NOT
        # fabricate: a None result is written as null on whichever sections
        # already track this ticker.
        if isinstance(snapshot["tickers"].get(t), dict):
            snapshot["tickers"][t]["quarterly"] = series
        if isinstance(snapshot["quotes"].get(t), dict):
            snapshot["quotes"][t]["quarterly"] = series
        if series:
            ok += 1

    # Match the .mjs writers (refresh_quotes.mjs et al.): compact, no indent.
    # The snapshot is ~8MB; pretty-printing would balloon the file and produce
    # an enormous diff on every run. Write atomically so an interrupted write
    # cannot truncate the live snapshot.
    payload = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    os.replace(tmp, path)
    return ok, failures
