"""
Print-reaction sweep — fast, sonar-free hydration of today's AMC reporters.

Runs at 4:30 PM ET (cron A), right after the press release. For every ticker
that reported in the target timing/date window it:

  1. Hydrates actuals (revenue, EPS, FY guide direction) from the finance
     connector (finance_earnings_history) with a Finnhub EPS cross-check.
  2. Composes a 2-3 sentence "print reaction" note: beat/miss vs consensus,
     FY guide direction, after-hours move. NEVER fabricates — a missing actual
     renders as "n/a" and is simply omitted from the prose.
  3. Stamps note_status='print_reaction', note_generated_at, note_source.
  4. Writes the record into earnings_intel.json and upserts it to Supabase.

This job NEVER calls Perplexity sonar. The cost ceiling per run is a single
connector call per AMC ticker (plus one free-tier Finnhub call).

Idempotency: re-running produces the same record state. It NEVER downgrades a
ticker that has already escalated to note_status='call_reaction' — the richer
note wins.

Usage:
    python -m automation.jobs.print_reaction_sweep --timing AMC --date today
    python -m automation.jobs.print_reaction_sweep --ticker AVGO --date 2026-06-03
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.shared.paths import EARNINGS_INTEL, EARNINGS_CALENDAR  # noqa: E402
from automation.shared.io_helpers import read_json, write_json          # noqa: E402
from automation.shared.supabase_client import upsert_row                # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_TABLE   = "earnings_intel"
NOTE_SOURCE      = "print_reaction_sweep"
FINANCE_SOURCE_ID = "finance"
FINANCE_TIMEOUT  = 60


def _et_today() -> str:
    """Today's date in America/New_York (the trading calendar's clock)."""
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        # Fallback: UTC date (cron anchors are UTC); acceptable for backfills.
        return _dt.datetime.utcnow().date().isoformat()


def _resolve_date(arg: str) -> str:
    """Map the --date CLI value to an ISO date in ET."""
    if not arg or arg == "today":
        return _et_today()
    if arg == "yesterday":
        try:
            from zoneinfo import ZoneInfo
            return (_dt.datetime.now(ZoneInfo("America/New_York")).date()
                    - _dt.timedelta(days=1)).isoformat()
        except Exception:
            return (_dt.datetime.utcnow().date() - _dt.timedelta(days=1)).isoformat()
    return arg  # assume YYYY-MM-DD


# ---------------------------------------------------------------------------
# Actuals hydration (connector + Finnhub) — NEVER fabricate
# ---------------------------------------------------------------------------
def _call_finance_tool(tool_name: str, args: dict, timeout: int = FINANCE_TIMEOUT) -> dict:
    payload = json.dumps({
        "source_id": FINANCE_SOURCE_ID,
        "tool_name": tool_name,
        "arguments": args,
    })
    proc = subprocess.run(
        ["external-tool", "call", payload],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"external-tool {tool_name} exit {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return json.loads(proc.stdout)


def hydrate_actuals(ticker: str, earnings_date: str) -> dict[str, Any]:
    """Fetch in-quarter actuals via the finance source chain.

    Returns a dict of the canonical results_vs_consensus fields it could fill.
    Reuses automation.sources.perplexity_finance_source.PerplexityFinanceSource
    (the same connector wiring the daily pipeline uses) so we never duplicate
    the markdown-table parsing. Missing values are simply absent — never zeroed.
    """
    out: dict[str, Any] = {}
    try:
        from automation.sources.perplexity_finance_source import PerplexityFinanceSource
        resp = PerplexityFinanceSource().fetch(ticker)
        if resp and resp.fields:
            rvc = resp.fields.get("results_vs_consensus")
            if isinstance(rvc, dict):
                out.update(rvc)
            hist = resp.fields.get("history_8q")
            if hist:
                out["_history_8q"] = hist
                out["_history_8q_source"] = resp.fields.get("history_8q_source")
    except Exception as exc:
        print(f"  [WARN] {ticker}: finance actuals fetch failed: {exc}")

    # Finnhub EPS cross-check — only fills a GAP, never overrides the connector.
    try:
        from automation.shared.finnhub import get_earnings_surprises
        rows = get_earnings_surprises(ticker)
        if rows and out.get("in_quarter_eps_actual") in (None, ""):
            latest = rows[0]
            if latest.get("actual") is not None:
                out["in_quarter_eps_actual"] = latest["actual"]
                out["in_quarter_eps_actual_source"] = "finnhub"
                if latest.get("estimate") is not None:
                    out["in_quarter_eps_estimate"] = latest["estimate"]
                    out["eps_consensus_source"] = "finnhub"
                if latest.get("surprisePercent") is not None:
                    out["in_quarter_eps_surprise_pct"] = round(
                        float(latest["surprisePercent"]), 2)
                    out["eps_surprise_source"] = "finnhub"
    except Exception as exc:
        print(f"  [WARN] {ticker}: finnhub EPS cross-check failed: {exc}")

    return out


# ---------------------------------------------------------------------------
# Note composition — formulaic, 2-3 sentences, zero fabrication
# ---------------------------------------------------------------------------
def _fmt_surprise(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return None
    if p > 1.0:
        return f"beat by {p:.1f}%"
    if p < -1.0:
        return f"missed by {abs(p):.1f}%"
    return "came in in-line"


def _beat_miss_clause(metric: str, surprise_pct: Optional[float]) -> Optional[str]:
    phrase = _fmt_surprise(surprise_pct)
    if phrase is None:
        return None
    return f"{metric} {phrase} vs consensus"


def compose_note(
    ticker: str,
    actuals: dict[str, Any],
    *,
    fy_guide_direction: Optional[str] = None,
    after_hours_move_pct: Optional[float] = None,
) -> str:
    """Compose a 2-3 sentence print-reaction note from real actuals only.

    Data-integrity mandate: every clause is gated on its underlying value being
    present. A field that is missing is OMITTED — we never write a speculative
    number, and we never claim a beat/miss we cannot substantiate. The note
    always names the ticker and the print event so it reads as a sentence even
    when only EPS (or nothing quantitative) is available.
    """
    eps_clause = _beat_miss_clause("EPS", actuals.get("in_quarter_eps_surprise_pct"))
    rev_clause = _beat_miss_clause("revenue", actuals.get("in_quarter_rev_surprise_pct"))

    # Sentence 1 — the print and the headline beat/miss.
    metric_clauses = [c for c in (eps_clause, rev_clause) if c]
    if metric_clauses:
        s1 = f"{ticker} reported: " + "; ".join(metric_clauses) + "."
    elif actuals.get("in_quarter_eps_actual") not in (None, ""):
        s1 = f"{ticker} reported EPS of {actuals['in_quarter_eps_actual']}; consensus comparison n/a."
    else:
        s1 = f"{ticker} reported; actuals not yet available (n/a)."

    sentences = [s1]

    # Sentence 2 — FY guide direction (only when management actually signalled).
    if fy_guide_direction:
        d = fy_guide_direction.strip().lower()
        if d in ("raised", "up", "above"):
            sentences.append("Full-year guidance was raised.")
        elif d in ("lowered", "cut", "down", "below"):
            sentences.append("Full-year guidance was lowered.")
        elif d in ("maintained", "reaffirmed", "in-line", "unchanged"):
            sentences.append("Full-year guidance was reaffirmed.")

    # Sentence 3 — initial after-hours move (only when a real number exists).
    if after_hours_move_pct is not None:
        try:
            m = float(after_hours_move_pct)
            arrow = "up" if m > 0 else "down" if m < 0 else "flat"
            if arrow == "flat":
                sentences.append("Shares were roughly flat after hours.")
            else:
                sentences.append(f"Shares moved {arrow} {abs(m):.1f}% after hours.")
        except (TypeError, ValueError):
            pass

    # Keep to at most 3 sentences.
    return " ".join(sentences[:3])


# ---------------------------------------------------------------------------
# Ticker selection
# ---------------------------------------------------------------------------
def _select_tickers(
    timing: Optional[str], date: str, ticker: Optional[str]
) -> list[dict[str, Any]]:
    """Resolve which calendar entries to sweep.

    --ticker overrides everything (a targeted backfill); otherwise we take every
    post_earnings entry whose date matches `date` and (when given) whose timing
    matches `timing`.
    """
    cal = read_json(EARNINGS_CALENDAR)
    post = cal.get("post_earnings", []) or []
    if ticker:
        t = ticker.upper()
        hits = [e for e in post if e.get("ticker", "").upper() == t]
        if hits:
            return hits
        # Backfill for a ticker not in the calendar: synthesize a minimal entry
        # from earnings_intel.json so targeted backfills still work.
        intel = read_json(EARNINGS_INTEL).get("tickers", {})
        rec = intel.get(t)
        if rec:
            return [{
                "ticker": t,
                "company": rec.get("company_name") or t,
                "date": date,
                "earnings_date": date,
                "timing": rec.get("timing") or timing,
            }]
        return []
    out = []
    for e in post:
        if e.get("date") != date and e.get("earnings_date") != date:
            continue
        if timing and (e.get("timing") or "").upper() != timing.upper():
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# Per-ticker write
# ---------------------------------------------------------------------------
def _sweep_one(
    entry: dict[str, Any], date: str, intel: dict[str, Any], dry_run: bool
) -> dict[str, Any]:
    ticker = entry.get("ticker", "").upper()
    company = entry.get("company") or ticker
    timing = entry.get("timing")
    earnings_date = entry.get("date") or entry.get("earnings_date") or date

    tickers = intel.setdefault("tickers", {})
    existing = tickers.get(ticker, {})

    # NEVER downgrade a call_reaction note to a print_reaction.
    if existing.get("note_status") == "call_reaction":
        print(f"  [SKIP] {ticker}: already note_status=call_reaction (no downgrade)")
        return {"ticker": ticker, "status": "skipped_call_reaction"}

    # Seed from any actuals the daily pipeline already persisted (real, stored
    # values — not fabricated), then let a live connector call gap-fill. This
    # keeps a backfill correct even when the live connector is unreachable.
    actuals = dict(existing.get("results_vs_consensus") or {})
    fetched = hydrate_actuals(ticker, earnings_date)
    for k, v in fetched.items():
        if actuals.get(k) in (None, ""):
            actuals[k] = v
    note = compose_note(
        ticker, actuals,
        fy_guide_direction=entry.get("fy_guide_direction"),
        after_hours_move_pct=entry.get("after_hours_move_pct"),
    )

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    # Merge actuals into results_vs_consensus (gap-fill; keep existing sources).
    rvc = dict(existing.get("results_vs_consensus") or {})
    for k, v in actuals.items():
        if k.startswith("_"):
            continue
        if rvc.get(k) in (None, ""):
            rvc[k] = v

    record = dict(existing)
    record.update({
        "ticker": ticker,
        "company_name": existing.get("company_name") or company,
        "state": "post_earnings",
        "timing": timing or existing.get("timing"),
        "last_earnings_date": earnings_date,
        "results_vs_consensus": rvc,
        "note_status": "print_reaction",
        "reaction_note": note,
        "note_generated_at": now_iso,
        "note_source": NOTE_SOURCE,
        "intel_updated_at": now_iso,
    })
    if actuals.get("_history_8q") and not record.get("history_8q"):
        record["history_8q"] = actuals["_history_8q"]
        record["history_8q_source"] = actuals.get("_history_8q_source")

    print(f"  [{ticker}] note_status=print_reaction note=\"{note}\"")

    if dry_run:
        return {"ticker": ticker, "status": "dry_run", "note": note}

    tickers[ticker] = record

    # Supabase upsert (conflict on ticker). Push only the columns the table owns.
    upsert_row(
        SUPABASE_TABLE,
        {
            "ticker": ticker,
            "company_name": record["company_name"],
            "last_earnings_date": earnings_date,
            "timing": record.get("timing"),
            "note_status": "print_reaction",
            "reaction_note": note,
            "note_generated_at": now_iso,
            "note_source": NOTE_SOURCE,
            "results_vs_consensus": rvc,
        },
        on_conflict="ticker",
    )
    return {"ticker": ticker, "status": "print_reaction", "note": note}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run(
    timing: Optional[str] = "AMC",
    date: str = "today",
    ticker: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    resolved_date = _resolve_date(date)
    entries = _select_tickers(timing if not ticker else None, resolved_date, ticker)

    print(f"[print_reaction_sweep] timing={timing} date={resolved_date} "
          f"ticker={ticker or '*'} -> {len(entries)} ticker(s)")

    if not entries:
        print("[print_reaction_sweep] No matching tickers.")
        return {"total": 0, "written": 0, "skipped": 0}

    intel = read_json(EARNINGS_INTEL)
    results = []
    for e in entries:
        try:
            results.append(_sweep_one(e, resolved_date, intel, dry_run))
        except Exception as exc:
            print(f"  [ERROR] {e.get('ticker')}: {exc}")
            results.append({"ticker": e.get("ticker"), "status": "error"})

    if not dry_run:
        intel["last_updated"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        write_json(EARNINGS_INTEL, intel)

    written = sum(1 for r in results if r.get("status") == "print_reaction")
    skipped = sum(1 for r in results if r.get("status", "").startswith("skipped"))
    print(f"[print_reaction_sweep] Done — written={written} skipped={skipped} "
          f"total={len(entries)}")
    return {"total": len(entries), "written": written, "skipped": skipped,
            "results": results}


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Print-reaction sweep (no sonar).")
    p.add_argument("--timing", choices=("AMC", "BMO"), default="AMC")
    p.add_argument("--date", type=str, default="today",
                   help="today | yesterday | YYYY-MM-DD")
    p.add_argument("--ticker", type=str, default=None,
                   help="Backfill a single ticker (overrides --timing filter).")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(timing=args.timing, date=args.date, ticker=args.ticker, dry_run=args.dry_run)
