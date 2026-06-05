"""
[PARTIALLY DEPRECATED] The CONTRACTUAL earnings fields (in-quarter actuals,
history_8q, consensus/surprise) are now owned by the per-ticker pipeline
(automation/pipeline); this script's role is reduced to syncing the NARRATIVE
base (bull/bear, signal scorecard, qualitative prose) from notes. Do NOT extend
it to write contractual fields -- add a source under automation/sources/ instead.
Removal of the contractual-field paths is scheduled in a follow-up PR.

Sync earnings_intel.json from the markdown notes in notes/{pre,post}_earnings/.

This is the durable bridge between the cron-generated markdown notes
(automation.jobs.{pre,post}_earnings_notes) and the structured intel JSON the
dashboard's Earnings Intel tab reads.

WHAT IT DOES
  For every ticker in earnings_notes_index.json (active_pre_earnings or
  active_post_earnings), this script reads the corresponding markdown note
  and extracts:

    * company_name, state, inflection_status
    * bottom_line (Headline / Set-up / Thesis Impact)
    * bull_case   (Scenario Grid Bull row + Key Debates upside angles)
    * base_case   (Scenario Grid Base row)
    * bear_case   (Scenario Grid Bear row + Key Debates downside angles)
    * signal_scorecard (one WATCHING signal per Key Debate / What Matters bullet)
    * source_metadata.legacy_note_path / primary_sources
    * For POST notes: post_earnings_review block with takeaways from
      Thesis Impact + Near-Term Outlook + Surprises sections.

  Existing rich content is preserved when present \u2014 the merge step only
  *upgrades* a record (fills empty fields) and refreshes the header. It will
  NEVER blank-out a populated bull_case or signal_scorecard. Use
  --force-rebuild to override and re-extract from scratch.

IDEMPOTENT \u2014 safe to run on every cron tick.

Usage:
  python3 scripts/sync_earnings_intel_from_notes.py
  python3 scripts/sync_earnings_intel_from_notes.py --dry-run
  python3 scripts/sync_earnings_intel_from_notes.py --force-rebuild
  python3 scripts/sync_earnings_intel_from_notes.py --force-rebuild --ticker XYZ
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "earnings_notes_index.json"
INTEL_PATH = ROOT / "earnings_intel.json"
CALENDAR_PATH = ROOT / "earnings_calendar.json"
PRE_DIR = ROOT / "notes" / "pre_earnings"
POST_DIR = ROOT / "notes" / "post_earnings"

NOW_ISO = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _read_note(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _extract_section(md: str, *headers: str) -> str | None:
    """Return the body of the first matching `## <header>` section, trimmed.

    Stops at the next `## ` heading, the `---` rule preceding the sources
    block, the `*Sources:*` marker itself, or end of document. This prevents
    the Sources URL list from leaking into the last section on the page.
    """
    for header in headers:
        pattern = (
            rf"^##\s+{re.escape(header)}\s*\n"
            rf"(.*?)(?=^##\s|^---\s*$|^\*Sources:\*|\Z)"
        )
        m = re.search(pattern, md, flags=re.MULTILINE | re.DOTALL)
        if m:
            body = m.group(1).strip()
            if body:
                return body
    return None


def _strip_md(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    return text


def _first_paragraph(text: str, max_chars: int = 500) -> str:
    if not text:
        return ""
    for para in re.split(r"\n\s*\n", text):
        clean = _strip_md(para).strip()
        if clean and not clean.startswith("|") and not clean.startswith("#"):
            clean = re.sub(r"\s+", " ", clean)
            if len(clean) <= max_chars:
                return clean
            return clean[:max_chars].rsplit(" ", 1)[0] + "\u2026"
    return ""


def _bullets(text: str | None, max_items: int = 5) -> list[str]:
    """Pull dash/bullet/numbered list items from a block, stripped clean."""
    if not text:
        return []
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r"^(?:[-*\u2022]|\d+\.)\s+(.+)$", line)
        if not m:
            continue
        bullet = _strip_md(m.group(1)).strip()
        # Drop the leading "Label:" prefix some bullets carry while keeping the body.
        bullet = re.sub(r"^([A-Z][A-Za-z0-9 /'\-&]{2,60}):\s+", "", bullet, count=1)
        bullet = re.sub(r"\s+", " ", bullet)
        if len(bullet) >= 8:
            out.append(bullet[:320])
        if len(out) >= max_items:
            break
    return out


def _extract_scenario_row(md: str, scenario: str) -> dict | None:
    """Parse the Scenario Grid table and return the row keyed by Bull/Base/Bear."""
    grid = _extract_section(md, "Scenario Grid")
    if not grid:
        return None
    target = scenario.strip().lower()
    for line in grid.split("\n"):
        line = line.strip()
        if not line.startswith("|") or line.startswith("|--") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = _strip_md(cells[0]).strip().lower()
        if first.startswith(target):
            return {
                "scenario": cells[0] if len(cells) >= 1 else scenario,
                "probability": cells[1] if len(cells) >= 2 else "",
                "trigger": _strip_md(cells[2]) if len(cells) >= 3 else "",
                "stock_move": cells[3] if len(cells) >= 4 else "",
            }
    return None


def _extract_urls(md: str, max_urls: int = 6) -> list[dict]:
    """Pull source URLs from markdown links + bare URL lines in the Sources block."""
    seen: set[str] = set()
    out: list[dict] = []
    # First the structured Sources section (lines after "*Sources:*")
    src_block = ""
    m = re.search(r"\*Sources:\*\s*(.*)$", md, flags=re.DOTALL)
    if m:
        src_block = m.group(1)
    for src in (src_block, md):
        for mm in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", src):
            label, url = mm.group(1).strip(), mm.group(2).strip()
            if url in seen:
                continue
            seen.add(url)
            out.append({"label": label[:80], "url": url})
            if len(out) >= max_urls:
                return out
        for mm in re.finditer(r"^\s*-\s+(https?://\S+)", src, flags=re.MULTILINE):
            url = mm.group(1).strip().rstrip(",.)]")
            if url in seen:
                continue
            seen.add(url)
            domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            out.append({"label": domain[:80], "url": url})
            if len(out) >= max_urls:
                return out
    return out


def _detect_stock_reaction_pct(md: str) -> float | None:
    """Find the first explicit % stock move mentioned in the note."""
    candidates = re.findall(
        r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:pop|move|drop|gain|loss|reaction)?",
        md,
    )
    for cand in candidates[:5]:
        try:
            val = float(cand)
            if -50.0 <= val <= 50.0:
                return val
        except ValueError:
            continue
    return None


def _extract_json_envelope(md: str, label: str) -> dict | None:
    """Extract a fenced JSON block labeled ```json <label> from the note.

    Returns the parsed dict, or None if the block is missing or malformed.
    """
    pattern = rf"```json\s+{re.escape(label)}\s*\n(.*?)```"
    m = re.search(pattern, md, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_guide_num(v) -> float | None:
    """Parse a guidance value that may be a number or a string with a $ prefix
    and/or K/M/B/T magnitude suffix (e.g. "$0.04", "48.0B", "1.13T").

    Suffix scaling is consistent within a metric, so it cancels in the ratio
    used for deltaPct. Returns a float in raw units or None.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if not (v != v) else None  # reject NaN
    if not isinstance(v, str):
        return None
    s = v.strip().replace("$", "").replace(",", "").replace(" ", "")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)([kKmMbBtT]?)", s)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    scale = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(m.group(2).lower(), 1.0)
    return n * scale


def select_guidance_baseline(consensus_prior, prior_guide):
    """Return (baseline_midpoint, prior_source): prefer the company's PRIOR
    GUIDE, else prior Street consensus. Returns (None, None) when neither
    exists.

    Rationale: the card frames a guidance move as raise/flat/cut, which is a
    statement about what the company did relative to its OWN prior guide. The
    vs-Street comparison (above/in-line/below) is carried separately as a
    secondary delta. Preferring prior_guide here makes the primary delta the
    raise/cut number; the vs-Street magnitude is surfaced via _compute_metric_delta's
    streetDeltaPct when both baselines are present.
    """
    guide = _parse_guide_num(prior_guide)
    if guide is not None:
        return guide, "prior_guidance"
    cons = _parse_guide_num(consensus_prior)
    if cons is not None:
        return cons, "consensus"
    return None, None


def _compute_metric_delta(new_mid, consensus_prior, prior_guide, tiny_baseline=0.0):
    """Compute the delta dict for one metric from new/prior midpoints.

    Returns ``{deltaPct, deltaAbs, priorSource, streetDeltaPct, streetDeltaAbs}``
    where the primary delta is vs the preferred baseline (prior_guide first,
    consensus fallback). deltaPct is a fraction (None when the baseline is
    zero / near-zero so callers use deltaAbs). Returns None when the new
    midpoint or all baselines are missing.

    streetDeltaPct / streetDeltaAbs are populated ONLY when the primary baseline
    is the prior guide AND a prior Street consensus is ALSO present, so the card
    can render "Raise +X%, +Y% vs Street" without re-computing. They are None
    otherwise.
    """
    cur = _parse_guide_num(new_mid)
    if cur is None:
        return None
    base, source = select_guidance_baseline(consensus_prior, prior_guide)
    if base is None:
        return None
    delta_abs = cur - base
    delta_pct = None
    if abs(base) > tiny_baseline:
        delta_pct = delta_abs / abs(base)

    # Sanity guard: a |delta| > 40% on FY revenue/EPS guidance is essentially
    # never reality for a watchlist-grade US large/mid-cap. When sonar returns
    # numbers this extreme it is almost always a quarterly-vs-FY units mismatch
    # (e.g. NTNX 727.5M Q1 guide compared to 2.825B FY consensus = -74%). Treat
    # as untrusted and return None so the card renders n/a per the data
    # integrity mandate rather than publishing a fabricated 70%+ swing.
    SANITY_MAX_DELTA = 0.40
    if delta_pct is not None and abs(delta_pct) > SANITY_MAX_DELTA:
        return None

    street_pct = None
    street_abs = None
    if source == "prior_guidance":
        cons = _parse_guide_num(consensus_prior)
        if cons is not None:
            street_abs = cur - cons
            if abs(cons) > tiny_baseline:
                street_pct = street_abs / abs(cons)
                if abs(street_pct) > SANITY_MAX_DELTA:
                    street_pct = None
                    street_abs = None

    return {
        "deltaPct": delta_pct,
        "deltaAbs": delta_abs,
        "priorSource": source,
        "streetDeltaPct": street_pct,
        "streetDeltaAbs": street_abs,
    }


def normalize_guidance_envelope(gvc: dict | None) -> dict:
    """Derive the normalized split-guidance fields (revenue + profitability)
    from a raw ``guide_vs_consensus`` envelope.

    Returns a flat dict of the backend fields the card UI reads. deltaPct
    values are FRACTIONS (e.g. -0.007 for -0.7%) so they match the JS helpers
    in earnings.js. Profitability follows the EPS > op profit/income > FCF
    priority order and records which metric was used in
    ``guidanceProfitMetricUsed``.
    """
    out: dict = {}
    if not gvc:
        return out

    def pct_to_frac(p):
        f = _safe_float(p)
        return f / 100.0 if f is not None else None

    # --- Revenue ---
    rev = _compute_metric_delta(
        gvc.get("fy_rev_guide_midpoint_new"),
        gvc.get("fy_rev_consensus_prior"),
        gvc.get("fy_rev_guide_midpoint_prior"),
        0.0,
    )
    if rev and rev["deltaPct"] is not None:
        out["guidanceRevenueDeltaPct"] = rev["deltaPct"]
        out["guidanceRevenuePriorSource"] = rev["priorSource"]
        if rev.get("streetDeltaPct") is not None:
            out["guidanceRevenueStreetDeltaPct"] = rev["streetDeltaPct"]
    else:
        cons = pct_to_frac(gvc.get("fy_rev_change_vs_consensus_pct"))
        prior = pct_to_frac(gvc.get("fy_rev_change_vs_prior_pct"))
        if cons is not None:
            out["guidanceRevenueDeltaPct"] = cons
            out["guidanceRevenuePriorSource"] = "consensus"
        elif prior is not None:
            out["guidanceRevenueDeltaPct"] = prior
            out["guidanceRevenuePriorSource"] = "prior_guidance"

    # --- EPS (primary profitability) --- tiny baseline guard for near-zero EPS.
    eps = _compute_metric_delta(
        gvc.get("fy_eps_guide_midpoint_new"),
        gvc.get("fy_eps_consensus_prior"),
        gvc.get("fy_eps_guide_midpoint_prior"),
        0.05,
    )
    if eps:
        if eps["deltaPct"] is not None:
            out["guidanceEpsDeltaPct"] = eps["deltaPct"]
        out["guidanceEpsDeltaAbs"] = eps["deltaAbs"]
        out["guidanceEpsPriorSource"] = eps["priorSource"]
        if eps.get("streetDeltaPct") is not None:
            out["guidanceEpsStreetDeltaPct"] = eps["streetDeltaPct"]
        if eps.get("streetDeltaAbs") is not None:
            out["guidanceEpsStreetDeltaAbs"] = eps["streetDeltaAbs"]
    else:
        cons = pct_to_frac(gvc.get("fy_eps_guide_change_vs_consensus_pct"))
        prior = pct_to_frac(gvc.get("fy_eps_guide_change_vs_prior_pct"))
        if cons is not None:
            out["guidanceEpsDeltaPct"] = cons
            out["guidanceEpsPriorSource"] = "consensus"
        elif prior is not None:
            out["guidanceEpsDeltaPct"] = prior
            out["guidanceEpsPriorSource"] = "prior_guidance"

    # --- Operating profit / income (fallback profitability) ---
    op = _compute_metric_delta(
        gvc.get("fy_op_profit_guide_midpoint_new"),
        gvc.get("fy_op_profit_consensus_prior"),
        gvc.get("fy_op_profit_guide_midpoint_prior"),
        0.0,
    )
    if op:
        if op["deltaPct"] is not None:
            out["guidanceOperatingProfitDeltaPct"] = op["deltaPct"]
        out["guidanceOperatingProfitDeltaAbs"] = op["deltaAbs"]
        out["guidanceOperatingProfitPriorSource"] = op["priorSource"]
        if op.get("streetDeltaPct") is not None:
            out["guidanceOperatingProfitStreetDeltaPct"] = op["streetDeltaPct"]
        if op.get("streetDeltaAbs") is not None:
            out["guidanceOperatingProfitStreetDeltaAbs"] = op["streetDeltaAbs"]

    # --- FCF (last-resort profitability) ---
    fcf = _compute_metric_delta(
        gvc.get("fy_fcf_guide_midpoint_new"),
        gvc.get("fy_fcf_consensus_prior"),
        gvc.get("fy_fcf_guide_midpoint_prior"),
        0.0,
    )
    if fcf:
        if fcf["deltaPct"] is not None:
            out["guidanceFcfDeltaPct"] = fcf["deltaPct"]
        out["guidanceFcfDeltaAbs"] = fcf["deltaAbs"]
        out["guidanceFcfPriorSource"] = fcf["priorSource"]
        if fcf.get("streetDeltaPct") is not None:
            out["guidanceFcfStreetDeltaPct"] = fcf["streetDeltaPct"]
        if fcf.get("streetDeltaAbs") is not None:
            out["guidanceFcfStreetDeltaAbs"] = fcf["streetDeltaAbs"]

    # Record which profitability metric the card will use (EPS first).
    if "guidanceEpsDeltaPct" in out or "guidanceEpsDeltaAbs" in out:
        out["guidanceProfitMetricUsed"] = "eps"
    elif "guidanceOperatingProfitDeltaPct" in out or "guidanceOperatingProfitDeltaAbs" in out:
        kind = gvc.get("fy_op_metric_kind")
        out["guidanceProfitMetricUsed"] = kind if kind in ("operating_income", "operating_profit") else "operating_profit"
    elif "guidanceFcfDeltaPct" in out or "guidanceFcfDeltaAbs" in out:
        out["guidanceProfitMetricUsed"] = "fcf"

    # --- Next-quarter (Q+1) guidance for companies that don't guide FY.
    # Prefer vs-Street consensus baseline; fall back to the company's prior
    # next-Q guide (rare but possible when guidance was previously updated).
    # _compute_metric_delta returns priorSource='prior_guidance' when only
    # a prior guide is available and consensus is null.
    q_rev = _compute_metric_delta(
        gvc.get("q_next_rev_guide_midpoint_new"),
        gvc.get("q_next_rev_consensus_prior"),
        gvc.get("q_next_rev_guide_midpoint_prior"),
        0.0,
    )
    if q_rev and q_rev["deltaPct"] is not None:
        out["guidanceNextQRevenueDeltaPct"] = q_rev["deltaPct"]
        out["guidanceNextQRevenuePriorSource"] = q_rev.get("priorSource") or "consensus"
        if q_rev.get("streetDeltaPct") is not None:
            out["guidanceNextQRevenueStreetDeltaPct"] = q_rev["streetDeltaPct"]

    q_eps = _compute_metric_delta(
        gvc.get("q_next_eps_guide_midpoint_new"),
        gvc.get("q_next_eps_consensus_prior"),
        gvc.get("q_next_eps_guide_midpoint_prior"),
        0.05,
    )
    if q_eps:
        if q_eps["deltaPct"] is not None:
            out["guidanceNextQEpsDeltaPct"] = q_eps["deltaPct"]
        if q_eps.get("deltaAbs") is not None:
            out["guidanceNextQEpsDeltaAbs"] = q_eps["deltaAbs"]
        out["guidanceNextQEpsPriorSource"] = q_eps.get("priorSource") or "consensus"
        if q_eps.get("streetDeltaPct") is not None:
            out["guidanceNextQEpsStreetDeltaPct"] = q_eps["streetDeltaPct"]
        if q_eps.get("streetDeltaAbs") is not None:
            out["guidanceNextQEpsStreetDeltaAbs"] = q_eps["streetDeltaAbs"]

    # --- SaaS north-star metrics (ARR / cRPO / NRR / billings) ---
    # These are captured uniformly for both horizons. Each contributes a
    # guidance{Horizon}{Metric}* family computed exactly like revenue above.
    # The company's emphasized metric (north_star_metric) drives which pill the
    # card features; units let the renderer format % (NRR) vs USD-millions.
    _normalize_saas_metrics(gvc, out)

    return out


# SaaS metric tokens. Envelope prefix -> (camelCase token, default tiny-baseline).
# Tiny-baseline 0.0 for USD/$ metrics; for NRR (a percentage near 100-130) a
# 0.0 tiny baseline is fine since the baseline is never near zero.
_SAAS_METRICS = (
    ("arr", "Arr", 0.0),
    ("crpo", "Crpo", 0.0),
    ("nrr", "Nrr", 0.0),
    ("billings", "Billings", 0.0),
)

# Horizon tokens: (envelope prefix, camelCase token).
_GUIDANCE_HORIZONS = (
    ("fy", "FY"),
    ("q_next", "NextQ"),
)


def _normalize_saas_metrics(gvc: dict, out: dict) -> None:
    """Populate guidance{Horizon}{Metric}* fields for the SaaS metric set, plus
    the per-horizon north-star pointer + units. Mirrors the revenue delta logic.
    """
    for h_prefix, h_token in _GUIDANCE_HORIZONS:
        for m_prefix, m_token, tiny in _SAAS_METRICS:
            base = f"{h_prefix}_{m_prefix}"
            delta = _compute_metric_delta(
                gvc.get(f"{base}_guide_midpoint_new"),
                gvc.get(f"{base}_consensus_prior"),
                gvc.get(f"{base}_guide_midpoint_prior"),
                tiny,
            )
            if not delta:
                continue
            field = f"guidance{h_token}{m_token}"
            if delta.get("deltaPct") is not None:
                out[f"{field}DeltaPct"] = delta["deltaPct"]
            if delta.get("deltaAbs") is not None:
                out[f"{field}DeltaAbs"] = delta["deltaAbs"]
            out[f"{field}PriorSource"] = delta.get("priorSource") or "consensus"
            if delta.get("streetDeltaPct") is not None:
                out[f"{field}StreetDeltaPct"] = delta["streetDeltaPct"]
            if delta.get("streetDeltaAbs") is not None:
                out[f"{field}StreetDeltaAbs"] = delta["streetDeltaAbs"]
            units = gvc.get(f"{base}_units")
            if units:
                out[f"{field}Units"] = units

        # North-star pointer for this horizon (e.g. "ARR", "cRPO", "revenue").
        ns = gvc.get(f"{h_prefix}_north_star_metric")
        if ns:
            out[f"guidance{h_token}NorthStarMetric"] = ns
            ns_units = gvc.get(f"{h_prefix}_north_star_units")
            if ns_units:
                out[f"guidance{h_token}NorthStarUnits"] = ns_units


# Flat normalized guidance fields the card UI reads. Kept alongside the legacy
# consolidated guide_vs_consensus envelope (which stays for backward compat).
NORMALIZED_GUIDANCE_FIELDS = (
    "guidanceRevenueDeltaPct",
    "guidanceRevenuePriorSource",
    "guidanceRevenueStreetDeltaPct",
    "guidanceEpsDeltaPct",
    "guidanceEpsDeltaAbs",
    "guidanceEpsPriorSource",
    "guidanceEpsStreetDeltaPct",
    "guidanceEpsStreetDeltaAbs",
    "guidanceOperatingProfitDeltaPct",
    "guidanceOperatingProfitDeltaAbs",
    "guidanceOperatingProfitPriorSource",
    "guidanceOperatingProfitStreetDeltaPct",
    "guidanceOperatingProfitStreetDeltaAbs",
    "guidanceFcfDeltaPct",
    "guidanceFcfDeltaAbs",
    "guidanceFcfPriorSource",
    "guidanceFcfStreetDeltaPct",
    "guidanceFcfStreetDeltaAbs",
    "guidanceProfitMetricUsed",
    # Next-quarter fallback (for companies that guide one quarter out only).
    "guidanceNextQRevenueDeltaPct",
    "guidanceNextQRevenuePriorSource",
    "guidanceNextQRevenueStreetDeltaPct",
    "guidanceNextQEpsDeltaPct",
    "guidanceNextQEpsDeltaAbs",
    "guidanceNextQEpsPriorSource",
    "guidanceNextQEpsStreetDeltaPct",
    "guidanceNextQEpsStreetDeltaAbs",
) + tuple(
    # SaaS north-star metric families (ARR / cRPO / NRR / billings) x horizons.
    f"guidance{h_token}{m_token}{suffix}"
    for h_token in ("FY", "NextQ")
    for m_token in ("Arr", "Crpo", "Nrr", "Billings")
    for suffix in (
        "DeltaPct", "DeltaAbs", "PriorSource",
        "StreetDeltaPct", "StreetDeltaAbs", "Units",
    )
) + tuple(
    f"guidance{h_token}{suffix}"
    for h_token in ("FY", "NextQ")
    for suffix in ("NorthStarMetric", "NorthStarUnits")
)


def _beat_miss_label(surprise_pct: float | None, threshold: float = 1.0) -> str:
    """Derive 'beat (X%)' / 'miss (X%)' / 'in-line' from surprise percentage.

    Threshold: >+1% = beat, < -1% = miss, else in-line.
    """
    if surprise_pct is None:
        return ""
    if surprise_pct > threshold:
        return f"beat ({surprise_pct:+.1f}%)"
    if surprise_pct < -threshold:
        return f"miss ({surprise_pct:+.1f}%)"
    return "in-line"


def _split_sentences(text: str, max_items: int = 4) -> list[str]:
    """Split a paragraph into sentence-like chunks on `; ` or `. ` boundaries."""
    if not text:
        return []
    parts = re.split(r";\s+|(?<=[a-z0-9\)])\.\s+(?=[A-Z])", text)
    out: list[str] = []
    for p in parts:
        p = p.strip().rstrip(".;")
        if len(p) >= 12:
            out.append(p[:280])
        if len(out) >= max_items:
            break
    return out


def _signal_id(label: str, idx: int) -> str:
    sid = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")[:48]
    return sid or f"signal_{idx + 1}"


def _short_label(text: str, words: int = 6) -> str:
    """Derive a concise Title-Case topic label (<=38 chars) from a bullet.

    Older versions of this function returned the first N words of the bullet,
    which produced truncated sentence-fragments (e.g.
    "Whether Cloud Security TAM Expansion Can"). Now we delegate to the same
    cleaner used by the dashboard renderer + backfill script so producers,
    renderers, and historical data all share one canonical label format.
    """
    if not text:
        return "Signal"
    cleaned = re.sub(r"\s+", " ", text).strip()
    try:
        from automation.jobs.fix_intel_labels import clean_label
    except Exception:
        # Fallback if import fails: legacy 6-word truncate (rare)
        parts = cleaned.split(" ")
        label = " ".join(parts[:words]).rstrip(".,:;\u2014-")
        return label[:80] if label else "Signal"
    # clean_label expects a scorecard-shaped dict with at minimum the label
    # or a note field. Pass the full bullet as both so the cleaner can use
    # the "before-first-colon" head extraction strategy.
    label = clean_label({"label": cleaned, "note": cleaned, "signal_id": ""})
    return label or "Signal"


# ---------------------------------------------------------------------------
# Per-note extractors
# ---------------------------------------------------------------------------

def _state_and_inflection(kind: str) -> tuple[str, str]:
    if kind == "post":
        return ("post_earnings", "POST")
    return ("pre_earnings", "PRE")


def build_pre_intel(ticker: str, entry: dict, note_path: Path) -> dict:
    md = _read_note(note_path)
    earnings_date = entry.get("date") or ""
    company = _extract_company(md, entry.get("company") or ticker)
    state, inflection = _state_and_inflection("pre")

    setup_body = _extract_section(md, "Set-up", "Setup")
    bottom_line = _first_paragraph(setup_body or md, max_chars=650) or \
        f"{ticker} approaches its {earnings_date} print \u2014 see the latest note for the full setup."

    debates_body = _extract_section(md, "Key Debates & Variant Perception",
                                    "Key Debates", "Debates", "What Matters")
    what_matters_body = _extract_section(md, "What Matters This Print",
                                         "What Matters", "Key Watchpoints")
    debate_bullets = _bullets(debates_body, max_items=4)
    what_matters_bullets = _bullets(what_matters_body, max_items=4)

    bull_row = _extract_scenario_row(md, "Bull")
    base_row = _extract_scenario_row(md, "Base")
    bear_row = _extract_scenario_row(md, "Bear")

    def _row_text(row: dict | None, fallback: str) -> str:
        if not row:
            return fallback
        return _strip_md(row.get("trigger") or "").strip() or fallback

    bull_case = {
        "thesis_headline": _row_text(bull_row, f"Clean beat + raised guide re-rates {ticker}."),
        "pattern": "",
        "pushes_higher": debate_bullets[:3] or [
            "In-line-to-better print on revenue/EPS",
            "Guidance maintained or raised",
            "No new overhangs disclosed",
        ],
        "pushes_lower": [],
        "probability": (bull_row or {}).get("probability", ""),
        "stock_move": (bull_row or {}).get("stock_move", ""),
    }
    base_case = {
        "setup_headline": _row_text(base_row, "In-line print; guidance and narrative intact."),
        "pushes_higher": what_matters_bullets[:2] or debate_bullets[1:3],
        "pushes_lower": what_matters_bullets[2:4] or debate_bullets[2:4],
        "probability": (base_row or {}).get("probability", ""),
        "stock_move": (base_row or {}).get("stock_move", ""),
    }
    bear_case = {
        "thesis_headline": _row_text(bear_row, f"Miss or guide cut compresses {ticker} multiple."),
        "pattern": "",
        "pushes_higher": [],
        "pushes_lower": debate_bullets[-3:] or [
            "Revenue or EPS miss vs consensus",
            "Guidance narrowed or lowered",
            "New overhang or competitive disclosure",
        ],
        "probability": (bear_row or {}).get("probability", ""),
        "stock_move": (bear_row or {}).get("stock_move", ""),
    }

    # Signal scorecard \u2014 a WATCHING entry per Key Debate / What Matters bullet
    scorecard_seed = (debate_bullets + what_matters_bullets)[:4]
    signal_scorecard = []
    for i, bullet in enumerate(scorecard_seed):
        label = _short_label(bullet, words=6)
        signal_scorecard.append({
            "signal_id": _signal_id(label, i),
            "label": label,
            "status": "WATCHING",
            "note": bullet[:260],
            "watch_quarter": f"Q reporting {earnings_date}",
            "resolved_at": None,
        })

    sources = _extract_urls(md, max_urls=6)

    # V2 envelope: setup_vs_consensus
    setup_envelope = _extract_json_envelope(md, "setup_vs_consensus")

    rec = {
        "ticker": ticker,
        "company_name": company,
        "state": state,
        "inflection_status": inflection,
        "next_earnings_date": earnings_date,
        "intel_updated_at": NOW_ISO,
        "refresh_reason": "synced_from_pre_earnings_note",
        "bottom_line": bottom_line,
        "bull_case": bull_case,
        "base_case": base_case,
        "bear_case": bear_case,
        "signal_scorecard": signal_scorecard,
        "tone_drift": {
            "current_tone": "cautious_constructive",
            "prior_tone": "",
            "tone_notes": _first_paragraph(setup_body or "", max_chars=240),
        },
        "source_metadata": {
            "legacy_note_path": str(note_path.relative_to(ROOT)),
            "primary_sources": sources,
        },
        "previous_bottom_line": None,
        "signal_changes": [],
    }
    if setup_envelope:
        rec["setup_vs_consensus"] = setup_envelope
    return rec


def build_post_intel(ticker: str, entry: dict, note_path: Path) -> dict:
    md = _read_note(note_path)
    earnings_date = entry.get("date") or ""
    days_since = entry.get("day_post", 0)
    company = _extract_company(md, entry.get("company") or ticker)
    state, inflection = _state_and_inflection("post")

    headline_body = _extract_section(md, "Headline")
    headline_text = _first_paragraph(headline_body or "", max_chars=320)
    bm_quality = ""
    if headline_body:
        m = re.search(r"\*\*Beat/Miss Quality:\*\*\s*(.+)", headline_body)
        if m:
            bm_quality = _strip_md(m.group(1)).strip()

    metrics_body = _extract_section(md, "Key Metrics")
    metrics = _bullets(metrics_body, max_items=6)

    guide_body = _extract_section(md, "Guidance and Tone", "Guidance",
                                  "Tone and Guidance", "Outlook")
    guidance_text = _first_paragraph(guide_body or "", max_chars=320)
    tone_text = ""
    if guide_body:
        m = re.search(r"\*\*Management Tone:\*\*\s*(.+)", guide_body)
        if m:
            tone_text = _strip_md(m.group(1)).strip()

    surprises_body = _extract_section(md, "Surprises / Disappointments",
                                       "Surprises", "Disappointments")
    surprises = _bullets(surprises_body, max_items=5)

    thesis_body = _extract_section(md, "Thesis Impact", "Thesis Update",
                                   "Takeaways", "Bottom Line")
    thesis_text = _first_paragraph(thesis_body or "", max_chars=500)

    analyst_body = _extract_section(md, "Analyst Reactions", "Analyst Changes")
    analyst_bullets = _bullets(analyst_body, max_items=4)

    outlook_body = _extract_section(md, "Near-Term Outlook", "Outlook",
                                    "What to Watch", "Follow-ups", "Open Questions")
    outlook_text = _first_paragraph(outlook_body or "", max_chars=400)
    outlook_bullets = _bullets(outlook_body, max_items=4)

    # Tone classification
    pos_re = re.compile(r"(beat|strong|accelerat|raise[ds]?|above|confirmed|inflect|outperform|momentum|exceeded)", re.IGNORECASE)
    neg_re = re.compile(r"(miss(?:ed)?|fell|declin|disappoint|cut|below|weak|deceler|under-?perform|guide[d]? down)", re.IGNORECASE)
    sentiment_blob = " ".join(filter(None, [headline_text, bm_quality, thesis_text, guidance_text, tone_text]))
    pos_hit = bool(pos_re.search(sentiment_blob))
    neg_hit = bool(neg_re.search(sentiment_blob))

    # Bottom line \u2014 prefer Headline, then Thesis Impact, then Beat/Miss Quality
    bottom_line = headline_text or thesis_text or bm_quality or \
        f"{ticker} reported {earnings_date} \u2014 see the post-earnings note for details."

    # Bull / Base / Bear from observed sentiment + key metric bullets
    upside_bullets = [b for b in (metrics + surprises) if pos_re.search(b)][:3]
    downside_bullets = [b for b in (metrics + surprises) if neg_re.search(b)][:3]
    if not upside_bullets:
        upside_bullets = metrics[:2]
    if not downside_bullets:
        downside_bullets = surprises[:2]

    bull_case = {
        "thesis_headline": thesis_text if pos_hit and not neg_hit else
            f"Bull path requires repeat execution next print.",
        "pattern": "",
        "pushes_higher": upside_bullets or metrics[:2],
        "pushes_lower": [],
    }
    base_case = {
        "setup_headline": "Thesis intact post-print; watch next print for signal confirmation.",
        "pushes_higher": outlook_bullets[:2],
        "pushes_lower": outlook_bullets[2:4],
    }
    bear_case = {
        "thesis_headline": thesis_text if neg_hit and not pos_hit else
            f"Bear path requires execution slip or macro shock.",
        "pattern": "",
        "pushes_higher": [],
        "pushes_lower": downside_bullets or surprises[:2],
    }

    # Signal scorecard \u2014 one resolved signal for the headline result + one for
    # guidance, plus WATCHING signals for each open outlook bullet.
    signal_scorecard: list[dict] = []
    if headline_text:
        status = "FAILED" if neg_hit and not pos_hit else "CONFIRMED" if pos_hit and not neg_hit else "WATCHING"
        signal_scorecard.append({
            "signal_id": "headline_results",
            "label": "Headline Results",
            "status": status,
            "note": (bm_quality or headline_text)[:260],
            "watch_quarter": f"Q reported {earnings_date}",
            "resolved_at": NOW_ISO if status != "WATCHING" else None,
        })
    if guidance_text or tone_text:
        gtxt = (guidance_text or "") + (" " + tone_text if tone_text else "")
        status = "FAILED" if neg_re.search(gtxt) and not pos_re.search(gtxt) else \
                 "CONFIRMED" if pos_re.search(gtxt) and not neg_re.search(gtxt) else "WATCHING"
        signal_scorecard.append({
            "signal_id": "guidance_trajectory",
            "label": "Guidance Trajectory",
            "status": status,
            "note": (tone_text or guidance_text)[:260],
            "watch_quarter": f"Q reported {earnings_date}",
            "resolved_at": NOW_ISO if status != "WATCHING" else None,
        })
    for i, bullet in enumerate(outlook_bullets[:2]):
        label = _short_label(bullet, words=6)
        signal_scorecard.append({
            "signal_id": _signal_id(f"watch_{label}", i),
            "label": label,
            "status": "WATCHING",
            "note": bullet[:260],
            "watch_quarter": "Next print",
            "resolved_at": None,
        })

    stock_pct = _detect_stock_reaction_pct(outlook_body or md or "")
    sources = _extract_urls(md, max_urls=6)

    # V2 envelopes: results_vs_consensus, guide_vs_consensus
    results_envelope = _extract_json_envelope(md, "results_vs_consensus")
    guide_envelope = _extract_json_envelope(md, "guide_vs_consensus")

    # Next earnings \u2014 approximate +90d unless we already know it.
    try:
        ed = datetime.strptime(earnings_date, "%Y-%m-%d").date()
        next_ed = (ed + timedelta(days=90)).isoformat()
        visible_until = (ed + timedelta(days=14)).isoformat()
    except Exception:
        next_ed = None
        visible_until = None

    rec = {
        "ticker": ticker,
        "company_name": company,
        "state": state,
        "inflection_status": inflection,
        "last_earnings_date": earnings_date,
        "next_earnings_date": next_ed,
        "intel_updated_at": NOW_ISO,
        "refresh_reason": "synced_from_post_earnings_note",
        "bottom_line": bottom_line,
        "beat_miss_quality": bm_quality,
        "bull_case": bull_case,
        "base_case": base_case,
        "bear_case": bear_case,
        "signal_scorecard": signal_scorecard,
        "key_metrics": metrics,
        "surprises": surprises,
        "analyst_reactions": analyst_bullets,
        "guidance_text": guidance_text,
        "tone_drift": {
            "current_tone": "constructive" if pos_hit and not neg_hit else
                            ("cautious" if neg_hit and not pos_hit else "neutral"),
            "prior_tone": "",
            "tone_notes": tone_text or guidance_text[:240],
        },
        "post_earnings_review": {
            "active": True,
            "earnings_date": earnings_date,
            "days_since": days_since,
            "visible_until": visible_until,
            "takeaways_headline": thesis_text[:200] if thesis_text else
                f"{ticker} {earnings_date} quarter \u2014 see takeaways below.",
            "takeaways_bullets": (
                _bullets(thesis_body, max_items=4)
                or _split_sentences(thesis_text, max_items=4)
                or outlook_bullets[:4]
                or _split_sentences(outlook_text, max_items=4)
                or surprises[:4]
            )[:4],
            "what_happened_headline": headline_text[:200] if headline_text else
                f"Quarter reported {earnings_date}.",
            "what_happened_bullets": metrics[:5],
            "stock_reaction_pct": stock_pct,
        },
        "source_metadata": {
            "legacy_note_path": str(note_path.relative_to(ROOT)),
            "primary_sources": sources,
        },
        "previous_bottom_line": None,
        "signal_changes": [],
    }
    if results_envelope:
        # Per-field provenance: any actual extracted from the note envelope is
        # note-sourced unless the envelope already names a more specific source.
        # build_earnings_json reads the canonical <field>_source siblings; older
        # envelopes that predate this convention get backfilled to "note" here so
        # the card can always show where a value came from. Existing source values
        # are preserved, never overwritten.
        if results_envelope.get("in_quarter_rev_actual") is not None:
            results_envelope.setdefault("in_quarter_rev_actual_source", "note")
        if results_envelope.get("in_quarter_eps_actual") is not None:
            results_envelope.setdefault("in_quarter_eps_actual_source", "note")
        rec["results_vs_consensus"] = results_envelope
    if guide_envelope:
        rec["guide_vs_consensus"] = guide_envelope
        # Derive the normalized split-guidance fields (revenue + profitability)
        # so the post-earnings card can render two metric-specific pills. The
        # legacy consolidated envelope is preserved above for backward compat.
        normalized = normalize_guidance_envelope(guide_envelope)
        for k, v in normalized.items():
            rec[k] = v
    return rec


def _extract_company(md: str, fallback: str) -> str:
    """First line is `# Company (TICKER) \u2014 Pre/Post-Earnings Note`."""
    m = re.match(r"^#\s+(.+?)\s+\([^)]+\)", md)
    return m.group(1).strip() if m else fallback


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

REFRESH_ALWAYS = {
    "intel_updated_at",
    "state",
    "inflection_status",
    "next_earnings_date",
    "last_earnings_date",
    "refresh_reason",
    # Normalized split-guidance fields are cheap derived scalars — always
    # refresh them from the latest envelope rather than treating as sticky.
    *NORMALIZED_GUIDANCE_FIELDS,
}

# Rich fields the sync now produces. If an existing record has empty values
# for these, we upgrade them. If it already has rich content, we keep it
# untouched (unless --force-rebuild is passed).
RICH_FIELDS = {
    "bottom_line",
    "bull_case",
    "base_case",
    "bear_case",
    "signal_scorecard",
    "source_metadata",
    "tone_drift",
    "post_earnings_review",
    "beat_miss_quality",
    "key_metrics",
    "surprises",
    "analyst_reactions",
    "guidance_text",
    "setup_vs_consensus",
    "results_vs_consensus",
    "guide_vs_consensus",
}


def _is_empty(value) -> bool:
    if value is None or value == "" or value == []:
        return True
    if isinstance(value, dict):
        # A bull_case shell with only empty pushes_higher/pushes_lower is empty.
        meaningful = {k: v for k, v in value.items()
                      if k not in ("pattern", "probability", "stock_move", "thesis_headline", "setup_headline")}
        return all(_is_empty(v) for v in meaningful.values())
    return False


def _has_real_content(existing: dict, key: str) -> bool:
    """A field has 'real content' when it's non-empty and not just a stub shell."""
    if key not in existing:
        return False
    val = existing[key]
    if _is_empty(val):
        return False
    if key in {"bull_case", "bear_case"}:
        ph = val.get("pushes_higher") or []
        pl = val.get("pushes_lower") or []
        return bool(ph or pl)
    if key == "base_case":
        return bool((val.get("pushes_higher") or []) or (val.get("pushes_lower") or []))
    if key == "signal_scorecard":
        return bool(val)
    if key == "post_earnings_review":
        return bool((val or {}).get("takeaways_bullets") or (val or {}).get("what_happened_bullets"))
    return True


def merge_intel(existing: dict, fresh: dict, force_rebuild: bool = False) -> dict:
    """Combine existing record with freshly-extracted record.

    Without force_rebuild: fill empty rich fields, refresh header/dates, never
    overwrite rich content already present.
    With force_rebuild: take fresh verbatim, but keep schema-only fields that
    aren't part of the extractor's output (e.g. theme_lifecycle, debate_intensity).
    """
    if force_rebuild:
        merged = dict(fresh)
        # Preserve auxiliary fields the extractor doesn't touch.
        for key in ("theme_lifecycle", "inflection_library", "guidance_profile",
                    "debate_intensity", "debate_score", "previous_bottom_line"):
            if key in existing:
                merged.setdefault(key, existing[key])
        # Preserve any signal_changes log (signal history is append-only).
        merged["signal_changes"] = existing.get("signal_changes") or []
        return merged

    merged = dict(existing)
    for key, value in fresh.items():
        if key in REFRESH_ALWAYS:
            merged[key] = value
            continue
        if key in RICH_FIELDS:
            if not _has_real_content(existing, key):
                merged[key] = value
            elif key == "source_metadata" and isinstance(merged.get(key), dict):
                # Always keep legacy_note_path current; merge missing primary_sources.
                src = merged[key]
                src["legacy_note_path"] = value.get("legacy_note_path", src.get("legacy_note_path"))
                if not src.get("primary_sources"):
                    src["primary_sources"] = value.get("primary_sources", [])
            continue
        # Unknown / aux field \u2014 fill only if missing.
        if key not in merged or _is_empty(merged.get(key)):
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def iter_note_entries(index: dict, only_ticker: str | None) -> Iterable[tuple[str, dict, Path]]:
    for kind, key in (("pre", "active_pre_earnings"), ("post", "active_post_earnings")):
        for entry in index.get(key, []):
            ticker = entry.get("ticker")
            if not ticker:
                continue
            if only_ticker and ticker != only_ticker:
                continue
            note_rel = entry.get("file") or entry.get("note_file")
            if not note_rel:
                continue
            note_path = ROOT / note_rel
            if not note_path.exists():
                continue
            yield kind, entry, note_path


def _safe_float(val) -> float | None:
    """Coerce a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def sync_calendar_from_intel(tickers: dict, dry_run: bool = False) -> int:
    """Push results/guide envelope fields onto earnings_calendar.json post_earnings[].

    Returns the number of calendar entries updated.
    """
    if not CALENDAR_PATH.exists():
        return 0
    cal = json.loads(CALENDAR_PATH.read_text())
    updated = 0

    for entry in cal.get("post_earnings", []):
        tk = entry.get("ticker")
        if not tk or tk not in tickers:
            continue
        intel = tickers[tk]
        rvc = intel.get("results_vs_consensus")
        gvc = intel.get("guide_vs_consensus")
        if not rvc and not gvc:
            continue

        changed = False

        # Populate from results_vs_consensus
        if rvc:
            if rvc.get("in_quarter_rev_actual") and not entry.get("revenue_actual"):
                entry["revenue_actual"] = rvc["in_quarter_rev_actual"]
                changed = True
            if rvc.get("in_quarter_eps_actual") and not entry.get("eps_actual"):
                entry["eps_actual"] = rvc["in_quarter_eps_actual"]
                changed = True
            rev_surp = _safe_float(rvc.get("in_quarter_rev_surprise_pct"))
            if rev_surp is not None and not entry.get("revenue_beat_miss"):
                entry["revenue_beat_miss"] = _beat_miss_label(rev_surp)
                changed = True
            eps_surp = _safe_float(rvc.get("in_quarter_eps_surprise_pct"))
            if eps_surp is not None and not entry.get("eps_beat_miss"):
                entry["eps_beat_miss"] = _beat_miss_label(eps_surp)
                changed = True

        # Populate guide fields from guide_vs_consensus
        if gvc:
            guide_fields = [
                "fy_rev_guide_change_vs_consensus_pct",
                "fy_rev_guide_change_vs_prior_pct",
                "fy_eps_guide_change_vs_consensus_pct",
                "fy_eps_guide_change_vs_prior_pct",
                "next_q_rev_guide_vs_consensus_pct",
                "next_q_eps_guide_vs_consensus_pct",
            ]
            for field in guide_fields:
                val = _safe_float(gvc.get(field))
                if val is not None and field not in entry:
                    entry[field] = val
                    changed = True

        # Push the normalized split-guidance fields onto the calendar entry so
        # the card renderer can build revenue + profitability pills directly.
        # Prefer fields already computed on the intel record; recompute from
        # the envelope only as a fallback.
        normalized = {k: intel[k] for k in NORMALIZED_GUIDANCE_FIELDS if k in intel}
        if not normalized and gvc:
            normalized = normalize_guidance_envelope(gvc)
        # Always reconcile the full normalized set: write new values AND clear
        # any stale value the entry already has but the latest intel does not.
        # Without this, a previously-published bogus delta (e.g. NTNX -74% from
        # a quarterly/FY units mismatch caught by the sanity guard) would stick
        # on the calendar entry indefinitely because val=None never overwrites.
        for field in NORMALIZED_GUIDANCE_FIELDS:
            new_val = normalized.get(field)
            if entry.get(field) != new_val:
                if new_val is None and field in entry:
                    entry[field] = None
                    changed = True
                elif new_val is not None:
                    entry[field] = new_val
                    changed = True

        if changed:
            updated += 1

    if updated > 0 and not dry_run:
        cal["updated"] = NOW_ISO
        CALENDAR_PATH.write_text(json.dumps(cal, indent=2, ensure_ascii=False))

    return updated


def _run_consensus_backfills(only_ticker: str | None = None) -> None:
    """Invoke the structured-field backfills after the note sync.

    Each backfill is best-effort: a failure (e.g. no Finnhub credential, network
    error) is logged and never aborts the sync. They only ever FILL nulls — an
    existing note-sourced value is never overwritten.
    """
    try:
        from scripts.backfill_results_vs_consensus import main as _prose_main
    except Exception:
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "backfill_results_vs_consensus",
                ROOT / "scripts" / "backfill_results_vs_consensus.py",
            )
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _prose_main = _mod.main
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] prose consensus backfill unavailable: {exc}")
            _prose_main = None

    import sys as _sys
    saved = _sys.argv
    for label, fn, path in (
        ("prose", _prose_main, None),
        ("finnhub_earnings", None, ROOT / "scripts" / "backfill_earnings_from_finnhub.py"),
    ):
        try:
            if fn is None and path is not None:
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location(path.stem, path)
                _mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                fn = _mod.main
            if fn is None:
                continue
            _sys.argv = [label] + (["--ticker", only_ticker] if only_ticker else [])
            try:
                fn()
            finally:
                _sys.argv = saved
        except SystemExit:
            _sys.argv = saved
        except Exception as exc:  # noqa: BLE001
            _sys.argv = saved
            print(f"  [WARN] {label} consensus backfill failed: {exc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show changes without writing")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="Re-extract rich fields from notes even when existing records already have content")
    ap.add_argument("--ticker", help="Limit to a single ticker")
    args = ap.parse_args()

    if not INDEX_PATH.exists():
        print(f"[ERR] {INDEX_PATH} not found \u2014 run scripts/reindex_earnings_notes.py first")
        return

    index = json.loads(INDEX_PATH.read_text())
    intel = json.loads(INTEL_PATH.read_text()) if INTEL_PATH.exists() else {
        "last_updated": NOW_ISO,
        "schema_version": "1.0",
        "tickers": {},
    }
    tickers = intel.setdefault("tickers", {})

    seeded, refreshed, upgraded, skipped = 0, 0, 0, 0
    seeded_t, upgraded_t, refreshed_t = [], [], []

    for kind, entry, note_path in iter_note_entries(index, args.ticker):
        ticker = entry["ticker"]
        if kind == "pre":
            fresh = build_pre_intel(ticker, entry, note_path)
        else:
            fresh = build_post_intel(ticker, entry, note_path)

        if ticker not in tickers:
            tickers[ticker] = fresh
            seeded += 1
            seeded_t.append(ticker)
            continue

        existing = tickers[ticker]
        had_rich = any(_has_real_content(existing, k) for k in
                       ("bull_case", "bear_case", "signal_scorecard"))
        before = json.dumps(existing, sort_keys=True)
        merged = merge_intel(existing, fresh, force_rebuild=args.force_rebuild)
        after = json.dumps(merged, sort_keys=True)

        if before == after:
            skipped += 1
            continue

        tickers[ticker] = merged
        if not had_rich and any(_has_real_content(merged, k) for k in
                                ("bull_case", "bear_case", "signal_scorecard")):
            upgraded += 1
            upgraded_t.append(ticker)
        else:
            refreshed += 1
            refreshed_t.append(ticker)

    intel["last_updated"] = NOW_ISO

    msg = (f"seeded={seeded} upgraded={upgraded} refreshed={refreshed} skipped={skipped}"
           + (" (force-rebuild)" if args.force_rebuild else ""))
    if args.dry_run:
        print(f"[DRY RUN] {msg}")
    else:
        # ensure_ascii=False preserves the file's raw-UTF-8 on-disk encoding
        # (em-dashes etc.) so the sync diff stays limited to changed fields
        # instead of re-escaping every Unicode character.
        INTEL_PATH.write_text(json.dumps(intel, indent=2, ensure_ascii=False))
        print(f"[OK] earnings_intel.json synced -- {msg}")

    # Backfill structured results_vs_consensus / guide_vs_consensus fields the
    # POST cards read. These run AFTER the note-sync so they only fill gaps the
    # notes leave behind, and they never overwrite a non-null value:
    #   1. prose backfill   — lifts REV/EPS actuals + surprise from note prose
    #   2. finnhub backfill — fills EPS surprise % from the authoritative
    #                         stock/earnings feed when the note omits it
    # Both re-read/write earnings_intel.json on disk; skipped on --dry-run.
    if not args.dry_run:
        _run_consensus_backfills(only_ticker=args.ticker)
        # Re-read so the calendar sync below sees the backfilled fields.
        intel = json.loads(INTEL_PATH.read_text())
        tickers = intel.setdefault("tickers", {})

    # Sync derived fields to earnings_calendar.json
    cal_updated = sync_calendar_from_intel(tickers, dry_run=args.dry_run)
    if cal_updated:
        label = "[DRY RUN] " if args.dry_run else "[OK] "
        print(f"{label}earnings_calendar.json -- {cal_updated} post_earnings entries updated")

    if seeded_t:
        print(f"  seeded:    {', '.join(sorted(seeded_t))}")
    if upgraded_t:
        print(f"  upgraded:  {', '.join(sorted(upgraded_t))}")
    if refreshed_t and len(refreshed_t) <= 20:
        print(f"  refreshed: {', '.join(sorted(refreshed_t))}")
    elif refreshed_t:
        print(f"  refreshed: {len(refreshed_t)} tickers")


if __name__ == "__main__":
    main()
