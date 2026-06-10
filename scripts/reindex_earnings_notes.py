"""
Rebuild earnings_notes_index.json::active_pre_earnings / active_post_earnings
from the on-disk notes in notes/pre_earnings/ and notes/post_earnings/.

This is safe to re-run at any time. It does NOT touch the archive section
or other top-level keys. Only real (non-stub) notes are indexed.

A note is considered "active" if its earnings_date is within MAX_DAYS of
TODAY (default 14 days) — matching the cron job's window.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "earnings_notes_index.json"
INTEL_PATH = ROOT / "earnings_intel.json"
PRE_DIR = ROOT / "notes" / "pre_earnings"
POST_DIR = ROOT / "notes" / "post_earnings"
MAX_DAYS = 14
TODAY = date.today()

STUB_SIZE_BYTES = 1000

_HEADLINE_MAX_LEN = 180


def is_stub(path: Path) -> bool:
    if not path.exists() or path.stat().st_size > STUB_SIZE_BYTES:
        return False
    body = path.read_text()
    return body.count("N/A") >= 3 and (
        "| Bull | ? | ? | ? |" in body or body.count("N/A") >= 5
    )


def parse_note(path: Path) -> tuple[str, str, str] | None:
    """Return (ticker, company, earnings_date) parsed from filename + header.

    Filename pattern: {TICKER}_{YYYY-MM-DD}.md (ticker may contain ".", "-").
    """
    stem = path.stem
    ticker, _, edate = stem.rpartition("_")
    if not ticker or not edate or not re.match(r"^\d{4}-\d{2}-\d{2}$", edate):
        return None
    first_line = path.read_text().splitlines()[0] if path.exists() else ""
    m = re.match(r"^#\s+(.+?)\s+\(", first_line)
    company = m.group(1) if m else ticker
    return ticker, company, edate


def _strip_citations(s: str) -> str:
    """Remove inline citation markers like [1] [2] etc."""
    return re.sub(r"\[\d+\]", "", str(s or "")).strip()


def _trunc(s: str) -> str:
    """Truncate to _HEADLINE_MAX_LEN chars, appending ellipsis if needed."""
    s = _strip_citations(s)
    if len(s) > _HEADLINE_MAX_LEN:
        return s[: _HEADLINE_MAX_LEN - 3] + "..."
    return s


def derive_post_earnings_reaction_headline(per: dict) -> str | None:
    """Compose a stock-reaction-led headline from post_earnings_review block.

    Reads ``stock_reaction.pct`` (signed float, e.g. -19.4) and
    ``stock_reaction.driver`` (1-sentence explanation string) from the block.
    If either field is missing or null, returns None so the caller can fall back
    to the v1 what_happened_headline.

    Conjunction logic:
      - "despite" when reaction sign disagrees with print quality
        (negative reaction + beat, or positive reaction + miss).
      - "on" when sign agrees (positive + beat, negative + miss).
    Beat/miss is inferred from the ``what_happened_headline`` text: the word
    "beat" (case-insensitive) indicates a beat; "miss" indicates a miss.
    When the print quality cannot be determined, default to "on".

    Format: "Stock {+X.X%|−X.X%} {despite|on} {print descriptor}; {driver}"

    Returns a string ≤ 180 chars (citations stripped) or None.
    Never returns the literal string "MISSING" or "n/a".

    Examples:
    >>> per = {'stock_reaction': {'pct': -19.4, 'driver': 'Q2 guide cautious on memory test demand'},
    ...        'what_happened_headline': 'Teradyne posts record Q1 with strong beats.'}
    >>> derive_post_earnings_reaction_headline(per)
    'Stock -19.4% despite Q1 beat; Q2 guide cautious on memory test demand'

    >>> per = {'stock_reaction': {'pct': 5.0, 'driver': 'data center reaccelerates above $20B'},
    ...        'what_happened_headline': 'NVDA beats and raises.'}
    >>> derive_post_earnings_reaction_headline(per)
    'Stock +5.0% on Q1 beat + raise; data center reaccelerates above $20B'

    >>> # Missing driver -> fall back
    >>> derive_post_earnings_reaction_headline({'stock_reaction': {'pct': -5.0}})

    >>> # Missing pct -> fall back
    >>> derive_post_earnings_reaction_headline({'stock_reaction': {'driver': 'something'}})

    """
    sr = per.get("stock_reaction") or {}
    pct = sr.get("pct")
    driver = (sr.get("driver") or "").strip()

    # Graceful degradation: require both fields
    if pct is None or not driver:
        return None

    # Format the reaction sign
    # Use Unicode minus for negative to match the example style
    if pct >= 0:
        pct_str = f"+{pct:.1f}%"
    else:
        pct_str = f"{pct:.1f}%"  # float already carries the minus sign

    # Infer print quality from what_happened_headline
    wh = (per.get("what_happened_headline") or "").lower()
    is_beat = "beat" in wh
    is_miss = "miss" in wh

    # Conjunction: "despite" when sign disagrees with quality
    if pct < 0 and is_beat and not is_miss:
        conjunction = "despite"
    elif pct > 0 and is_miss and not is_beat:
        conjunction = "despite"
    else:
        conjunction = "on"

    # Short print descriptor — derive from what_happened_headline if usable,
    # otherwise use a minimal fallback
    wh_orig = (per.get("what_happened_headline") or "").strip()
    wh_lower = wh_orig.lower()
    is_generic = (
        not wh_orig
        or wh_lower.startswith("quarter reported")
        or wh_lower.startswith("insufficient data")
        or wh_lower.startswith("unable to")
    )
    if is_generic or not wh_orig:
        # Minimal fallback descriptor
        if is_beat:
            descriptor = "earnings beat"
        elif is_miss:
            descriptor = "earnings miss"
        else:
            descriptor = "earnings print"
    else:
        # Shorten to at most 60 chars so the combined headline stays readable
        short = _strip_citations(wh_orig)
        if len(short) > 60:
            # Try to cut at a sentence or clause boundary
            for stop in (". ", "; ", ", "):
                idx = short.find(stop)
                if 0 < idx <= 60:
                    short = short[:idx]
                    break
            else:
                short = short[:57] + "..."
        descriptor = short.rstrip(".,;")

    # Strip citations from driver before composing
    driver_clean = _strip_citations(driver)

    result = f"Stock {pct_str} {conjunction} {descriptor}; {driver_clean}"
    return _trunc(result)


def derive_card_headline(entry_type: str, ticker_data: dict) -> str | None:
    """Derive a one-line card headline for a news-tab earnings card.

    POST-EARNINGS precedence:
      1. ``post_earnings_review.stock_reaction.pct`` +
         ``post_earnings_review.stock_reaction.driver`` — reaction-led format
         via ``derive_post_earnings_reaction_headline()``.
      2. ``post_earnings_review.what_happened_headline`` — if non-generic
         (i.e. does not start with "Quarter reported" or "Insufficient data")
      3. ``post_earnings_review.takeaways_headline`` — if non-empty
      4. Compose "Stock {+/-}{pct}% post-earnings" from stock_reaction_pct
      5. None — card renders without headline line

    PRE-EARNINGS precedence:
      1. ``signal_scorecard[0].note`` — first watching-signal note (key debate)
      2. None

    Returns a string ≤ 180 chars (citations stripped) or None.
    Never returns the literal string "MISSING".

    Examples (post-earnings, reaction-led when stock_reaction block present):
    >>> td = {'post_earnings_review': {
    ...     'stock_reaction': {'pct': -19.4, 'driver': 'guide cautious on memory'},
    ...     'what_happened_headline': 'Strong Q1 beat on revenue.',
    ...     'takeaways_headline': '', 'stock_reaction_pct': -19.4}}
    >>> derive_card_headline('post', td)
    'Stock -19.4% despite Strong Q1 beat on revenue; guide cautious on memory'

    Examples (post-earnings, falls back to what_happened when no stock_reaction block):
    >>> td = {'post_earnings_review': {'what_happened_headline': 'Beat; stock +12%.',
    ...                                 'takeaways_headline': '', 'stock_reaction_pct': 12.0}}
    >>> derive_card_headline('post', td)
    'Beat; stock +12%.'

    Examples (post-earnings, falls back to takeaways):
    >>> td = {'post_earnings_review': {'what_happened_headline': 'Quarter reported 2026-04-16.',
    ...                                 'takeaways_headline': 'Healthy business; EPS quality distorted.[1]',
    ...                                 'stock_reaction_pct': -9.7}}
    >>> derive_card_headline('post', td)
    'Healthy business; EPS quality distorted.'

    Examples (pre-earnings, signal_scorecard):
    >>> td = {'signal_scorecard': [{'note': 'Can NRR rebound above 110%?[2]'}]}
    >>> derive_card_headline('pre', td)
    'Can NRR rebound above 110%?'

    Examples (no data):
    >>> derive_card_headline('post', {})
    """
    if entry_type == "post":
        per = ticker_data.get("post_earnings_review") or {}
        pct = per.get("stock_reaction_pct")
        wh = per.get("what_happened_headline") or ""
        th = per.get("takeaways_headline") or ""

        # Attempt reaction-led headline first (new v2 format)
        reaction_headline = derive_post_earnings_reaction_headline(per)
        if reaction_headline:
            return reaction_headline

        # Determine if what_happened_headline is generic/unusable
        wh_lower = wh.strip().lower()
        is_generic = (
            not wh
            or wh_lower.startswith("quarter reported")
            or wh_lower.startswith("insufficient data")
            or wh_lower.startswith("unable to")
        )

        if not is_generic:
            return _trunc(wh)

        if th and not th.lower().strip().startswith("neutral pending"):
            return _trunc(th)

        if pct is not None:
            sign = "+" if pct > 0 else ""
            return f"Stock {sign}{pct:.1f}% post-earnings"

        return None

    if entry_type == "pre":
        ss = ticker_data.get("signal_scorecard") or []
        if isinstance(ss, list):
            for sig in ss:
                note = sig.get("note") or ""
                if note:
                    return _trunc(note)
        return None

    return None


def load_intel() -> dict:
    """Load earnings_intel.json and return the tickers dict (empty if missing)."""
    if not INTEL_PATH.exists():
        return {}
    try:
        data = json.loads(INTEL_PATH.read_text())
        return data.get("tickers", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def main():
    if not INDEX_PATH.exists():
        index = {}
    else:
        index = json.loads(INDEX_PATH.read_text())

    intel = load_intel()

    new_pre, new_post = [], []

    for path in sorted(PRE_DIR.glob("*.md")):
        if is_stub(path):
            continue
        parsed = parse_note(path)
        if not parsed:
            continue
        ticker, company, edate = parsed
        try:
            d = date.fromisoformat(edate)
        except ValueError:
            continue
        days_until = (d - TODAY).days
        if days_until < -1 or days_until > MAX_DAYS + 7:
            continue
        headline = derive_card_headline("pre", intel.get(ticker, {}))
        entry: dict = {
            "ticker": ticker,
            "company": company,
            "date": edate,
            "days_until": max(days_until, 0),
            "file": f"notes/pre_earnings/{path.name}",
        }
        if headline is not None:
            entry["headline"] = headline
        new_pre.append(entry)

    for path in sorted(POST_DIR.glob("*.md")):
        if is_stub(path):
            continue
        parsed = parse_note(path)
        if not parsed:
            continue
        ticker, company, edate = parsed
        try:
            d = date.fromisoformat(edate)
        except ValueError:
            continue
        days_since = (TODAY - d).days
        if days_since < 0 or days_since > MAX_DAYS + 30:
            continue
        ticker_intel = intel.get(ticker, {})
        headline = derive_card_headline("post", ticker_intel)
        per = ticker_intel.get("post_earnings_review") or {}
        pct = per.get("stock_reaction_pct")
        entry = {
            "ticker": ticker,
            "company": company,
            "date": edate,
            "day_post": max(days_since, 0),
            "expires": (d + timedelta(days=MAX_DAYS)).isoformat(),
            "note_file": f"notes/post_earnings/{path.name}",
        }
        if headline is not None:
            entry["headline"] = headline
        if pct is not None:
            entry["reaction_pct"] = pct
        new_post.append(entry)

    index["active_pre_earnings"] = sorted(new_pre, key=lambda x: x["date"])
    index["active_post_earnings"] = sorted(new_post, key=lambda x: x["date"])
    index["updated"] = datetime.utcnow().isoformat() + "Z"
    index["last_updated"] = index["updated"]
    INDEX_PATH.write_text(json.dumps(index, indent=2))

    print(f"Re-indexed: active_pre={len(new_pre)} active_post={len(new_post)}")
    headlines_pre = sum(1 for e in new_pre if e.get("headline"))
    headlines_post = sum(1 for e in new_post if e.get("headline"))
    print(f"Headlines generated: pre={headlines_pre}/{len(new_pre)} post={headlines_post}/{len(new_post)}")


if __name__ == "__main__":
    main()
