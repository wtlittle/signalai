#!/usr/bin/env python3
"""Section-by-section institutional drilldown generator (v2).

The v1 generator asked sonar-deep-research for the entire fourteen-section HTML
document in a single call. That model burns 145-170k reasoning tokens on a
drilldown and then truncates the HTML mid-document regardless of ``max_tokens``
-- an upstream behaviour we cannot fix from here. The result was notes that
shipped with the closing sections (and sometimes the </html>) silently cut off.

v2 makes truncation structurally impossible by decomposing the note into seven
small JSON payloads (see ``drilldown_schemas``). Each payload is requested with
the cheapest model that can produce it, validated locally against a strict
schema, retried ONCE on failure, then -- if still invalid -- quarantined as a
single SECTION (rendered as a graceful "unavailable" block) rather than failing
the whole note. The validated payloads are stitched into the full document by
``drilldown_assembler.assemble_document``: every byte of HTML is produced
locally from validated JSON, so no model call can truncate the rendered note.

After assembly the note runs through:
  * ``generate_drilldown._inject_earnings_chart`` (the deterministic SVG chart);
  * ``drilldown_validator.validate`` (the publish gate, gates a-k);
  * ``automation.quality.scorecard.score_drilldown`` (0-100 quality score).

A note that fails the validator OR scores below the quarantine threshold is NOT
published; it is quarantined and a push/in-app alert is fired
(``automation.alerts.quarantine_alert``). A note with the Bull/Base/Bear section
quarantined is also never published -- that section is the spine of the
recommendation and the validator's payoff / right-wrong gates cannot pass
without it.

Usage:
    python3 -m automation.jobs.drilldown_generator_v2 --tickers NET NVDA RBRK ORCL
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from automation.perplexity.client import call_perplexity
from automation.jobs.save_drilldown import save_drilldown
from automation.jobs.drilldown_validator import validate
from automation.jobs.drilldown_assembler import assemble_document
from automation.jobs.drilldown_schemas import SECTION_SCHEMAS, SCHEMA_BY_KEY
from automation.jobs.generate_drilldown import (
    _load_snapshot,
    build_signal_data_block,
    fetch_market_intel,
    _inject_earnings_chart,
)
from automation.sources.annual_history import fetch_5yr_history

# Quarantine threshold for the 0-100 quality scorecard. A note scoring below
# this is withheld even if it passes the binary validator gates -- the validator
# is pass/fail on structure, the scorecard catches thin-but-structurally-valid
# notes. Kept in sync with automation/quality/scorecard.py QUARANTINE_BELOW.
SCORECARD_QUARANTINE_BELOW = 80

QUARANTINE_PATH = ROOT / "quarantine.json"
FAILED_DIR = ROOT / "notes" / "drilldown" / "_failed"


# --------------------------------------------------------------------------- #
# Per-section generation
# --------------------------------------------------------------------------- #
def _build_section_prompt(schema: dict, ticker: str, company: str, data_block: str) -> str:
    """Compose the per-section user prompt.

    Each section gets the shared SIGNAL_DATA_BLOCK (ground-truth live data) plus
    the schema's own instruction text and a hard requirement to return ONLY the
    JSON object for that section. The schema's ``json_schema`` is also attached
    as an advisory ``response_format`` on the call itself.
    """
    instruction = schema.get("prompt") or (
        f'Produce the "{schema["title"]}" section for an institutional '
        f'equity drilldown on {company} ({ticker}).'
    )
    return (
        f"{instruction}\n\n"
        f"Subject: {company} ({ticker}).\n"
        "The data block below is pre-filled with live, ground-truth figures -- "
        "treat it as authoritative and do not contradict it. Where a figure is "
        "not present, source it from your search tools; never invent numbers.\n\n"
        f"{data_block}\n\n"
        "Return ONLY a single JSON object matching the required schema for this "
        "section. No prose, no markdown fences, no commentary outside the JSON."
    )


def _generate_section(schema: dict, ticker: str, company: str, data_block: str,
                      force: bool) -> tuple[dict | None, list[str]]:
    """Generate and validate one section payload.

    Returns ``(payload, problems)``. ``payload`` is the validated dict on success
    or None on failure; ``problems`` is the list of validator problems from the
    final attempt (empty on success). Retries ONCE on validation failure with a
    correction prefix before giving up.
    """
    key = schema["key"]
    model = schema["model"]
    max_chars = schema.get("max_chars", 8000)
    # Token budget: ~4 chars/token, with headroom. Capped so even the largest
    # section (12k chars) never asks for a runaway completion.
    max_tokens = min(8000, max(1500, (max_chars // 3) + 800))
    json_schema = schema.get("json_schema")
    validate_fn = schema["validate"]

    base_prompt = _build_section_prompt(schema, ticker, company, data_block)
    system = (
        "You are Signal Stack AI's institutional research engine. Return ONLY a "
        "single valid JSON object for the requested section. No prose, no "
        "markdown fences, no <script> tags."
    )
    extra_meta = {"model": model, "section": key}
    if json_schema:
        # The schema entry already stores a complete response_format body
        # ({"type": "json_schema", "json_schema": {...}}). Pass it through verbatim.
        extra_meta["response_format"] = json_schema
    if model == "sonar-deep-research":
        # The quant section is the only deep-research call; keep effort low so it
        # does not burn the reasoning budget that caused v1's truncation.
        extra_meta["reasoning_effort"] = "low"

    prompt = base_prompt
    last_problems: list[str] = ["no response"]
    for attempt in (1, 2):
        task = f"drilldown_section_{key}"
        resp = call_perplexity(
            ticker=ticker,
            task=task,
            prompt=prompt,
            system=system,
            force=force or attempt == 2,
            max_tokens=max_tokens,
            temperature=0.1,
            extra_meta=extra_meta,
        )
        if not isinstance(resp, dict) or resp.get("raw") is not None or \
                resp.get("queued") or resp.get("skipped") or resp.get("dry_run"):
            last_problems = [
                f"section '{key}' returned no usable JSON "
                f"({'queued' if resp.get('queued') else 'unparseable/raw'})"
            ]
            # A queued/skipped response cannot be retried into a payload here.
            if resp.get("queued") or resp.get("skipped") or resp.get("dry_run"):
                return None, last_problems
            payload = None
        else:
            payload = resp

        if payload is not None:
            try:
                problems = list(validate_fn(payload) or [])
            except Exception as exc:  # a validator that raises is a hard fail
                problems = [f"section '{key}' validator raised: {exc}"]
            if not problems:
                return payload, []
            last_problems = problems

        if attempt == 1:
            prompt = (
                "PREVIOUS ATTEMPT FAILED these schema checks:\n  - "
                + "\n  - ".join(last_problems)
                + "\nFix every one and resubmit. Return ONLY the corrected JSON "
                "object for this section, no prose.\n\n"
                + base_prompt
            )

    return None, last_problems


# --------------------------------------------------------------------------- #
# Identity / meta assembly
# --------------------------------------------------------------------------- #
def _build_meta(ticker: str, snap: dict, company: str | None) -> dict:
    """Assemble the header/identity meta dict the assembler needs."""
    q = (snap.get("quotes") or {}).get(ticker) or {}
    asum = (snap.get("analyst_summary") or {}).get(ticker) or {}
    company = company or q.get("longName") or ticker
    return {
        "ticker": ticker,
        "company": company,
        "exchange": q.get("exchange") or q.get("fullExchangeName") or "NASDAQ",
        "sector": q.get("sector") or "Technology",
        "date": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d"),
        "price": q.get("price"),
        "target_mean": q.get("targetMeanPrice") or asum.get("targetMeanPrice"),
        "target_high": q.get("targetHighPrice") or asum.get("targetHighPrice"),
        "target_low": q.get("targetLowPrice") or asum.get("targetLowPrice"),
        "rating": (q.get("recommendationKey") or asum.get("recommendationKey") or "").title() or None,
        "kpi_cards": [],  # populated from the quant section's KPIs post-generation
        "management_note": None,
    }


def _kpi_cards_from_quant(quant: dict) -> list[dict]:
    """Build up to six header KPI cards from the validated quant KPI list."""
    cards = []
    for k in (quant.get("kpis") or [])[:6]:
        cards.append({
            "label": k.get("name", ""),
            "value": k.get("value", ""),
            "sub": k.get("trend", ""),
        })
    return cards


# --------------------------------------------------------------------------- #
# Quarantine bookkeeping
# --------------------------------------------------------------------------- #
def _record_quarantine(ticker: str, reason: str, detail) -> Path:
    """Append a v2 quarantine record to quarantine.json (never raises)."""
    try:
        data = {}
        if QUARANTINE_PATH.exists():
            data = json.loads(QUARANTINE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        bucket = data.setdefault("drilldown_failures_v2", [])
        bucket.append({
            "ticker": ticker,
            "reason": reason,
            "detail": detail,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        })
        QUARANTINE_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"  [WARN] {ticker}: could not record quarantine: {exc}", file=sys.stderr)
    return QUARANTINE_PATH


def _save_failed_html(ticker: str, html: str, failures: list[str]) -> Path:
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = FAILED_DIR / f"{ticker}_{stamp}_v2.html"
    note = "<!-- v2 validator failures:\n" + "\n".join(failures) + "\n-->\n"
    path.write_text(note + html, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def generate_one_v2(ticker: str, snap: dict, *, company: str | None = None,
                    force: bool = False, dry_publish: bool = False) -> dict:
    """Generate one drilldown via the section-by-section pipeline.

    Returns a result dict: {ticker, published(bool), score, scorecard,
    validator_ok, validator_failures, quarantined_sections, md_path, reasons}.
    Never raises for a single-ticker failure -- the failure is reported in the
    result so a batch run can continue.
    """
    ticker = ticker.strip().upper()
    print(f"\n=== v2 drilldown: {ticker} ===")

    # ---- Build the shared data block (ground-truth live data) -------------
    try:
        history_5y = fetch_5yr_history(ticker).get("history_5y")
    except Exception as exc:
        print(f"  [WARN] {ticker}: 5yr history enrichment failed: {exc}")
        history_5y = None
    try:
        market_intel = fetch_market_intel(ticker)
    except Exception as exc:
        print(f"  [WARN] {ticker}: market_intel fetch failed: {exc}")
        market_intel = None

    data_block = build_signal_data_block(
        ticker, snap, history_5y=history_5y, market_intel=market_intel
    )
    meta = _build_meta(ticker, snap, company)
    company = meta["company"]

    # ---- Generate each section independently ------------------------------
    sections: dict[str, object] = {}
    quarantined: list[str] = []
    for schema in SECTION_SCHEMAS:
        key = schema["key"]
        print(f"  [section] {key} ({schema['model']})...")
        payload, problems = _generate_section(schema, ticker, company, data_block, force)
        if payload is not None:
            sections[key] = payload
        else:
            reason = "; ".join(problems) or "section generation failed"
            print(f"    [QUARANTINE SECTION] {key}: {reason}", file=sys.stderr)
            sections[key] = reason  # string reason -> graceful unavailable block
            quarantined.append(key)

    # Promote quant KPIs into header KPI cards when available.
    if isinstance(sections.get("quant"), dict):
        meta["kpi_cards"] = _kpi_cards_from_quant(sections["quant"])

    # ---- Assemble the full document deterministically ---------------------
    html = assemble_document(meta, sections)

    # ---- Inject the deterministic earnings chart --------------------------
    try:
        html = _inject_earnings_chart(html, ticker, snap)
    except Exception as exc:
        print(f"  [WARN] {ticker}: chart injection failed: {exc}")

    result = {
        "ticker": ticker,
        "published": False,
        "score": None,
        "scorecard": None,
        "validator_ok": False,
        "validator_failures": [],
        "quarantined_sections": quarantined,
        "md_path": None,
        "reasons": [],
    }

    # ---- Hard rule: a quarantined BBB section cannot be published ---------
    if "bbb" in quarantined:
        reason = ("Bull/Base/Bear section was quarantined; the recommendation "
                  "spine is required, so the note is withheld.")
        result["reasons"].append(reason)
        _save_failed_html(ticker, html, [reason])
        _record_quarantine(ticker, "bbb_section_quarantined", sections.get("bbb"))
        _fire_quarantine_alert(ticker, [reason], quarantined)
        print(f"  [WITHHELD] {ticker}: {reason}")
        return result

    # ---- Validator gate (a-k) --------------------------------------------
    ok, failures = validate(html, ticker=ticker)
    result["validator_ok"] = ok
    result["validator_failures"] = failures
    if not ok:
        result["reasons"].append(f"validator failed: {failures}")
        path = _save_failed_html(ticker, html, failures)
        _record_quarantine(ticker, "validator_failed", failures)
        _fire_quarantine_alert(ticker, failures, quarantined)
        print(f"  [WITHHELD] {ticker}: validator failed -> {path.relative_to(ROOT)}")
        return result

    # ---- Scorecard gate (0-100) ------------------------------------------
    try:
        from automation.quality.scorecard import score_drilldown
        card = score_drilldown(html=html, ticker=ticker)
        result["score"] = card.get("score")
        result["scorecard"] = card
        if card.get("score", 0) < SCORECARD_QUARANTINE_BELOW:
            reason = (f"scorecard {card.get('score')} < {SCORECARD_QUARANTINE_BELOW}: "
                      f"{card.get('issues')}")
            result["reasons"].append(reason)
            path = _save_failed_html(ticker, html, [reason])
            _record_quarantine(ticker, "low_scorecard", card)
            _fire_quarantine_alert(ticker, [reason], quarantined)
            print(f"  [WITHHELD] {ticker}: {reason}")
            return result
    except Exception as exc:
        # A scorecard failure must not block a validator-passing note, but record it.
        print(f"  [WARN] {ticker}: scorecard scoring failed: {exc}")
        result["reasons"].append(f"scorecard error: {exc}")

    # ---- Publish ----------------------------------------------------------
    if dry_publish:
        result["published"] = False
        result["reasons"].append("dry_publish: not saved")
        out = ROOT / "drilldown_data" / f"{ticker}_v2_preview.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        result["md_path"] = str(out.relative_to(ROOT))
        print(f"  [DRY] {ticker}: preview at {out.relative_to(ROOT)} (score={result['score']})")
        return result

    title = f"{company} ({ticker}) — Institutional Drilldown"
    md_path, entry = save_drilldown(
        ticker=ticker, html=html, part="p1", trigger="Deep Research v2",
        title=title, html_path=None, price_at_gen=meta.get("price"),
    )
    result["published"] = True
    result["md_path"] = str(md_path.relative_to(ROOT))
    print(f"  [PUBLISHED] {ticker}: {md_path.relative_to(ROOT)} "
          f"(score={result['score']}, {entry['word_count']} words)")
    return result


def _fire_quarantine_alert(ticker: str, reasons: list[str], sections: list[str]) -> None:
    """Best-effort push/in-app alert on a drilldown quarantine. Never raises."""
    try:
        from automation.alerts.quarantine_alert import alert_drilldown_quarantine
        alert_drilldown_quarantine(ticker, reasons, quarantined_sections=sections)
    except Exception as exc:
        print(f"  [WARN] {ticker}: quarantine alert failed: {exc}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Section-by-section drilldown generator (v2).")
    parser.add_argument("--tickers", nargs="+", help="one or more tickers")
    parser.add_argument("--ticker", help="single ticker (alias for --tickers)")
    parser.add_argument("--force", action="store_true", help="bypass research cache")
    parser.add_argument("--dry-publish", action="store_true",
                        help="assemble + validate + score but write a preview instead of publishing")
    args = parser.parse_args(argv)

    tickers = list(args.tickers or [])
    if args.ticker:
        tickers.append(args.ticker)
    if not tickers:
        parser.error("provide --ticker TICKER or --tickers T1 T2 ...")

    snap = _load_snapshot()
    results = []
    for t in tickers:
        try:
            results.append(generate_one_v2(t, snap, force=args.force, dry_publish=args.dry_publish))
        except Exception as exc:
            print(f"  [ERROR] {t}: {exc}", file=sys.stderr)
            results.append({"ticker": t, "published": False, "reasons": [str(exc)]})

    print("\n=== v2 SUMMARY ===")
    any_fail = False
    for r in results:
        status = "PUBLISHED" if r.get("published") else "WITHHELD"
        if not r.get("published"):
            any_fail = True
        print(f"  {status:10s} {r['ticker']}: score={r.get('score')} "
              f"validator_ok={r.get('validator_ok')} "
              f"quarantined_sections={r.get('quarantined_sections')}")
        for reason in r.get("reasons", []):
            print(f"       - {reason}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
