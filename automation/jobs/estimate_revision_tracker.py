"""
Estimate Revision Tracker — weekly consensus EPS/revenue revision history per ticker.

For every ticker in the watchlist this job:

  1. Pulls live forward EPS / forward revenue / analyst count from yfinance
     (the same source backend.py already uses for the /quote endpoint).
  2. Compares against the most-recent row already in Supabase
     `revision_history` to compute 1-week and 4-week revision deltas.
  3. Optionally queues a Perplexity research prompt
     (build_estimate_revision_prompt) to capture qualitative narrative for
     deltas larger than NARRATIVE_THRESHOLD_PCT.
  4. Upserts a new row keyed on (ticker, date) into Supabase.
  5. Flags any ticker whose forward EPS estimate has drifted more than
     ESTIMATE_DRIFT_FLAG_PCT in the 2 weeks before earnings — emits a
     WATCHING signal payload to a daily diff file that
     `note_diff_injector.py` can pick up.

Usage:
    # Dry-run (no API or Supabase writes)
    python -m automation.jobs.estimate_revision_tracker --dry-run

    # Live run
    SUPABASE_URL=https://xxx.supabase.co \
    SUPABASE_SERVICE_KEY=sb_secret_... \
    python -m automation.jobs.estimate_revision_tracker

    # Single ticker
    python -m automation.jobs.estimate_revision_tracker --ticker MSFT --force
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import ROOT_DIR, EARNINGS_CALENDAR  # noqa: E402
from automation.shared.tickers import load_tickers, load_common_names  # noqa: E402
from automation.shared.io_helpers import read_json  # noqa: E402
from automation.perplexity.client import call_perplexity  # noqa: E402
from automation.perplexity.prompts import build_estimate_revision_prompt  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TABLE                     = "revision_history"
REVISION_TTL_DAYS         = int(os.environ.get("REVISION_TTL_DAYS", "6"))  # ~weekly
NARRATIVE_THRESHOLD_PCT   = float(os.environ.get("NARRATIVE_THRESHOLD_PCT", "0.02"))
ESTIMATE_DRIFT_FLAG_PCT   = float(os.environ.get("ESTIMATE_DRIFT_FLAG_PCT", "0.03"))
PRE_EARNINGS_WINDOW_DAYS  = int(os.environ.get("PRE_EARNINGS_WINDOW_DAYS", "14"))
SIGNAL_DIFFS_OUT          = ROOT_DIR / "automation" / "queue" / "estimate_drift_signals.json"


# ---------------------------------------------------------------------------
# yfinance fetcher (lazy import)
# ---------------------------------------------------------------------------
def _fetch_yf_estimate(ticker: str) -> dict[str, Any] | None:
    try:
        import yfinance as yf  # noqa: F401
    except ImportError:
        return None
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        return {
            "fwd_eps_est": _coerce_float(info.get("forwardEps")),
            "fwd_rev_est": _coerce_float_billions(info.get("totalRevenue") or info.get("revenueEstimate")),
            "num_analysts": _coerce_int(info.get("numberOfAnalystOpinions")),
            "target_mean": _coerce_float(info.get("targetMeanPrice")),
        }
    except Exception as exc:
        print(f"  [WARN] yfinance fetch failed for {ticker}: {exc}")
        return None


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


def _load_latest_row(requests_mod, url: str, headers: dict, ticker: str) -> dict | None:
    """Return the most-recent revision_history row for a ticker, or None."""
    resp = requests_mod.get(
        f"{url}/rest/v1/{TABLE}",
        headers=headers,
        params={
            "ticker": f"eq.{ticker}",
            "order":  "date.desc",
            "limit":  "1",
            "select": "*",
        },
        timeout=20,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 300:
        print(f"  [WARN] Supabase select failed for {ticker}: HTTP {resp.status_code}")
        return None
    rows = resp.json() or []
    return rows[0] if rows else None


def _load_history(requests_mod, url: str, headers: dict, ticker: str, limit: int = 8) -> list[dict]:
    """Return last N revision_history rows for the ticker, newest first."""
    resp = requests_mod.get(
        f"{url}/rest/v1/{TABLE}",
        headers=headers,
        params={
            "ticker": f"eq.{ticker}",
            "order":  "date.desc",
            "limit":  str(limit),
            "select": "*",
        },
        timeout=20,
    )
    if resp.status_code >= 300:
        return []
    return resp.json() or []


def _upsert(requests_mod, url: str, headers: dict, row: dict) -> None:
    resp = requests_mod.post(
        f"{url}/rest/v1/{TABLE}?on_conflict=ticker,date",
        headers=headers,
        data=json.dumps(row),
        timeout=20,
    )
    if resp.status_code >= 300:
        print(f"  [WARN] Supabase upsert failed for {row.get('ticker')}: HTTP {resp.status_code} {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Revision math
# ---------------------------------------------------------------------------
def _compute_revisions(current: dict, history: list[dict]) -> dict[str, float | None]:
    """Compute 1-week and 4-week revisions vs historical rows.

    history is sorted newest-first. We look for the row closest to (today - 7d)
    and (today - 28d).
    """
    today = _dt.date.today()
    targets = {"1w": 7, "4w": 28}
    out: dict[str, float | None] = {
        "eps_revision_1w": None,
        "eps_revision_4w": None,
        "rev_revision_1w": None,
        "rev_revision_4w": None,
    }
    for tag, days in targets.items():
        target = today - _dt.timedelta(days=days)
        # Pick the historical row with `date` closest to target (with `date <= target` preferred).
        best: dict | None = None
        best_age = None
        for row in history:
            raw_date = row.get("date")
            if not raw_date:
                continue
            try:
                row_date = _dt.date.fromisoformat(raw_date[:10])
            except ValueError:
                continue
            if row_date > today:
                continue
            age = abs((row_date - target).days)
            if best is None or age < best_age:
                best = row
                best_age = age
        if not best or best_age is None or best_age > days * 2:
            continue

        eps_prev = best.get("fwd_eps_est")
        rev_prev = best.get("fwd_rev_est")
        eps_cur = current.get("fwd_eps_est")
        rev_cur = current.get("fwd_rev_est")
        if eps_prev and eps_cur:
            try:
                out[f"eps_revision_{tag}"] = round((float(eps_cur) - float(eps_prev)) / float(eps_prev), 5)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        if rev_prev and rev_cur:
            try:
                out[f"rev_revision_{tag}"] = round((float(rev_cur) - float(rev_prev)) / float(rev_prev), 5)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return out


# ---------------------------------------------------------------------------
# Pre-earnings drift signal emission
# ---------------------------------------------------------------------------
def _load_pre_earnings_calendar() -> dict[str, str]:
    """Return {ticker: earnings_date} for tickers entering the pre-earnings window."""
    calendar = read_json(EARNINGS_CALENDAR) or {}
    out: dict[str, str] = {}
    today = _dt.date.today()
    for entry in calendar.get("pre_earnings", []) or []:
        ticker = entry.get("ticker")
        date_str = entry.get("earnings_date")
        if not ticker or not date_str:
            continue
        try:
            edate = _dt.date.fromisoformat(date_str[:10])
        except ValueError:
            continue
        if 0 <= (edate - today).days <= PRE_EARNINGS_WINDOW_DAYS:
            out[ticker] = date_str
    return out


def _emit_drift_signals(signals: list[dict]) -> None:
    SIGNAL_DIFFS_OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "signals": signals,
    }
    SIGNAL_DIFFS_OUT.write_text(json.dumps(payload, indent=2))
    print(f"[estimate_revision_tracker] Wrote {len(signals)} drift signals to {SIGNAL_DIFFS_OUT.relative_to(ROOT_DIR)}")


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------
def run(
    dry_run: bool = False,
    force: bool = False,
    single_ticker: str | None = None,
    queue_narratives: bool = False,
) -> dict[str, Any]:
    tickers = [single_ticker] if single_ticker else load_tickers()
    common_names = load_common_names()
    pre_earnings = _load_pre_earnings_calendar()

    summary: dict[str, Any] = {
        "tickers_total":    len(tickers),
        "rows_upserted":    0,
        "skipped_no_data":  0,
        "skipped_fresh":    0,
        "narratives_queued": 0,
        "drift_signals":    0,
        "errors":           0,
        "dry_run":          dry_run,
    }

    print(f"[estimate_revision_tracker] {len(tickers)} tickers to check "
          f"(ttl={REVISION_TTL_DAYS}d, dry_run={dry_run}, force={force})")

    requests_mod = url = headers = None
    if not dry_run:
        try:
            requests_mod, url, headers = _supabase_client()
        except Exception as exc:
            print(f"[estimate_revision_tracker] [ERROR] Supabase setup failed: {exc}")
            return summary

    drift_signals: list[dict] = []
    today_iso = _dt.date.today().isoformat()

    for ticker in tickers:
        # Skip-fresh check
        if not dry_run and not force:
            prev = _load_latest_row(requests_mod, url, headers, ticker) if requests_mod else None
            if prev and _is_fresh(prev.get("date"), REVISION_TTL_DAYS):
                summary["skipped_fresh"] += 1
                continue

        snapshot = _fetch_yf_estimate(ticker)
        if not snapshot or not snapshot.get("fwd_eps_est"):
            summary["skipped_no_data"] += 1
            continue

        # Compute revisions vs history
        history: list[dict] = []
        if not dry_run and requests_mod:
            history = _load_history(requests_mod, url, headers, ticker, limit=8)
        revisions = _compute_revisions(snapshot, history)

        row = {
            "ticker":            ticker,
            "date":              today_iso,
            "fwd_eps_est":       snapshot.get("fwd_eps_est"),
            "fwd_rev_est":       snapshot.get("fwd_rev_est"),
            "num_analysts":      snapshot.get("num_analysts"),
            "target_mean":       snapshot.get("target_mean"),
            "eps_revision_1w":   revisions.get("eps_revision_1w"),
            "eps_revision_4w":   revisions.get("eps_revision_4w"),
            "rev_revision_1w":   revisions.get("rev_revision_1w"),
            "rev_revision_4w":   revisions.get("rev_revision_4w"),
            "direction":         _direction_from_revisions(revisions),
            "narrative":         None,
            "harvested_at":      _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }

        if dry_run:
            print(f"  [DRY] {ticker} fwd_eps={row['fwd_eps_est']} 1w={row['eps_revision_1w']} 4w={row['eps_revision_4w']}")
            summary["rows_upserted"] += 1
            continue

        # Drift signal: 4-week EPS revision > threshold and ticker enters pre-earnings window
        eps_4w = revisions.get("eps_revision_4w")
        if eps_4w is not None and abs(eps_4w) >= ESTIMATE_DRIFT_FLAG_PCT and ticker in pre_earnings:
            direction = "upward" if eps_4w > 0 else "downward"
            drift_signals.append({
                "ticker":         ticker,
                "earnings_date":  pre_earnings[ticker],
                "signal_label":   f"Estimate drift: {direction} {round(eps_4w * 100, 1)}% (4w)",
                "signal_status":  "WATCHING",
                "signal_type":    "estimate_revision",
                "eps_revision_4w": eps_4w,
                "rev_revision_4w": revisions.get("rev_revision_4w"),
                "created_at":     row["harvested_at"],
            })

        # Optional qualitative narrative
        if queue_narratives and (
            (revisions.get("eps_revision_4w") is not None and abs(revisions["eps_revision_4w"]) >= NARRATIVE_THRESHOLD_PCT)
            or (revisions.get("rev_revision_4w") is not None and abs(revisions["rev_revision_4w"]) >= NARRATIVE_THRESHOLD_PCT)
        ):
            company = common_names.get(ticker, ticker)
            prompt = build_estimate_revision_prompt(ticker, company)
            try:
                call_perplexity(
                    ticker=ticker,
                    task="estimate_revision",
                    prompt=prompt,
                    system="Return ONLY structured JSON. No markdown fences, no prose, no preamble.",
                    max_tokens=700,
                    extra_meta={"supabase_table": TABLE, "date": today_iso},
                )
                summary["narratives_queued"] += 1
            except Exception as exc:
                summary["errors"] += 1
                print(f"  [ERROR] narrative queue failed for {ticker}: {exc}")

        try:
            _upsert(requests_mod, url, headers, row)
            summary["rows_upserted"] += 1
        except Exception as exc:
            summary["errors"] += 1
            print(f"  [ERROR] upsert failed for {ticker}: {exc}")

    if drift_signals and not dry_run:
        _emit_drift_signals(drift_signals)
        summary["drift_signals"] = len(drift_signals)

    print(
        f"[estimate_revision_tracker] Done — "
        f"upserted={summary['rows_upserted']} skipped_fresh={summary['skipped_fresh']} "
        f"skipped_no_data={summary['skipped_no_data']} drift_signals={summary['drift_signals']} "
        f"narratives_queued={summary['narratives_queued']} errors={summary['errors']}"
    )
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float_billions(v: Any) -> float | None:
    """Convert raw revenue (in dollars) to billions for storage."""
    raw = _coerce_float(v)
    if raw is None:
        return None
    return round(raw / 1_000_000_000.0, 3) if raw > 1_000_000 else raw


def _is_fresh(date_str: str | None, ttl_days: int) -> bool:
    if not date_str:
        return False
    try:
        d = _dt.date.fromisoformat(date_str[:10])
    except ValueError:
        return False
    return (_dt.date.today() - d).days < ttl_days


def _direction_from_revisions(revisions: dict[str, float | None]) -> str:
    eps_4w = revisions.get("eps_revision_4w")
    rev_4w = revisions.get("rev_revision_4w")
    candidates = [v for v in (eps_4w, rev_4w) if v is not None]
    if not candidates:
        return "stable"
    avg = sum(candidates) / len(candidates)
    if avg > 0.005:
        return "upward"
    if avg < -0.005:
        return "downward"
    return "stable"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Track sell-side estimate revisions per ticker.")
    p.add_argument("--dry-run", action="store_true", help="List tickers without writing to Supabase.")
    p.add_argument("--force",   action="store_true", help="Bypass freshness skip and re-harvest every ticker.")
    p.add_argument("--ticker",  type=str, default=None, help="Restrict to a single ticker.")
    p.add_argument("--queue-narratives", action="store_true", help="Queue Perplexity narrative calls for material revisions.")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(
        dry_run=args.dry_run,
        force=args.force,
        single_ticker=(args.ticker.upper() if args.ticker else None),
        queue_narratives=args.queue_narratives,
    )
