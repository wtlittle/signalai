"""JSON schemas for the section-by-section drilldown generator (v2).

The single-shot ``sonar-deep-research`` generator is broken by an upstream
truncation bug (the model burns 145-170k reasoning tokens then runs out of
completion budget before emitting ``</html>``, regardless of max_tokens). The
v2 architecture sidesteps this entirely: each logical section is requested as a
small, strictly-typed JSON object from a cheaper model, validated locally, and
rendered to deterministic HTML by ``drilldown_renderer``. No single model call
ever produces the full document, so truncation cannot corrupt a note.

There are SEVEN logical sections. They render into the FOURTEEN canonical
validator sections (``drilldown_validator.REQUIRED_SECTIONS``) because several
validator sections are sub-blocks of one logical section (e.g. logical
"Quant Snapshot" renders the Financial Model Snapshot AND the Sensitivity
table; logical "Valuation" renders both Valuation and Industry/Competitive).

Each schema entry carries:
  - ``key``: stable identifier used by the orchestrator + renderer.
  - ``model``: which Perplexity model to call. sonar-pro is the default
    workhorse (reliable JSON, no deep-research truncation); sonar-deep-research
    is used ONLY for the quant snapshot (section 3) where multi-quarter
    financial sourcing benefits from deep search; sonar-reasoning-pro for the
    Bull/Base/Bear section where structured numeric reasoning matters.
  - ``max_chars``: a soft target so the orchestrator can flag a section whose
    JSON came back implausibly large (the design goal is < 25k chars per call;
    most land 5-10k).
  - ``json_schema``: a Perplexity ``response_format.json_schema`` body. It is
    advisory for deep-research but meaningfully reduces drift to off-spec
    shapes for sonar-pro / sonar-reasoning-pro.
  - ``validate``: a Python validator (returns list[str] of problems; empty =
    valid). This is the authoritative gate -- response_format is best-effort.

No emoji anywhere (user rule). Pure standard library.
"""

from __future__ import annotations

# Bull/Base/Bear (logical section 6) keeps its own locked schema +
# validator in drilldown_renderer.py; we re-export the validator here so the
# orchestrator can treat all seven sections uniformly.
from automation.jobs.drilldown_renderer import (
    validate_payload as _validate_bbb_payload,
    BBBValidationError,
)


# --------------------------------------------------------------------------- #
# Small validation helpers
# --------------------------------------------------------------------------- #
def _is_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _wc(s: str) -> int:
    return len((s or "").split())


def _check_list(problems, payload, key, min_len, max_len, item_check, label):
    items = payload.get(key)
    if not isinstance(items, list):
        problems.append(f"{key} must be a list")
        return
    if not (min_len <= len(items) <= max_len):
        problems.append(f"{key} must have {min_len}-{max_len} items, got {len(items)}")
    for i, it in enumerate(items):
        err = item_check(it)
        if err:
            problems.append(f"{key}[{i}] {err} ({label})")


# --------------------------------------------------------------------------- #
# Section 1 -- Executive Summary / Thesis
# --------------------------------------------------------------------------- #
def validate_summary(p: dict) -> list[str]:
    """1 thesis paragraph (bold opener with digit/horizon/direction) + 5 takeaways."""
    out: list[str] = []
    if not isinstance(p, dict):
        return ["payload is not an object"]
    thesis = p.get("thesis_paragraph")
    if not _is_str(thesis):
        out.append("thesis_paragraph missing")
    elif not (40 <= _wc(thesis) <= 180):
        out.append(f"thesis_paragraph word count {_wc(thesis)} outside [40, 180]")
    opener = p.get("thesis_opener")
    if not _is_str(opener):
        out.append("thesis_opener missing")
    else:
        # The validator gates the opener hard (VOICE CONTRACT A): <=250 chars,
        # has a digit, a horizon keyword and a directional claim. We pre-check
        # length + digit here; the horizon/direction keyword lists live in the
        # prompt instructions so the model supplies them.
        if len(opener) > 240:
            out.append(f"thesis_opener {len(opener)} chars (must be <= 240)")
        if not any(c.isdigit() for c in opener):
            out.append("thesis_opener has no digit")

    def _bullet(b):
        if not _is_str(b):
            return "not a non-empty string"
        if not (6 <= _wc(b) <= 60):
            return f"word count {_wc(b)} outside [6, 60]"
        return ""

    _check_list(out, p, "key_takeaways", 5, 5, _bullet, "key takeaway")
    return out


SUMMARY_SCHEMA = {
    "key": "summary",
    "title": "Executive Summary / Thesis",
    "model": "sonar-pro",
    "max_chars": 6000,
    "validate": validate_summary,
    "prompt": (
        "Write the executive summary / thesis for an institutional equity "
        "drilldown. Return JSON with: thesis_opener, thesis_paragraph, "
        "key_takeaways.\n"
        "- thesis_opener: ONE punchy directional sentence, AT MOST 240 characters. "
        "It MUST contain a number/percent, a TIME HORIZON keyword (use one of: "
        "'next 12 months', 'next 18 months', 'next 24 months', 'into 2027', "
        "'by 2027', 'by FY27', 'through FY27', 'over the next two years'), and a "
        "DIRECTIONAL claim keyword (use one of: re-rating, breakout, de-rate, "
        "transition, expansion, compression, ramp). Do NOT open with "
        "'<Company> is a ... company', 'positioned at the intersection', "
        "'operates in the ... market', or 'the investment debate centers on'.\n"
        "- thesis_paragraph: 40-180 words expanding the opener with the core "
        "numbers from the data block.\n"
        "- key_takeaways: EXACTLY 5 bullets, each 6-60 words, each carrying a "
        "concrete figure. Never write the literal word MISSING."
    ),
    "json_schema": {
        "type": "json_schema",
        "json_schema": {
            "name": "drilldown_summary",
            "schema": {
                "type": "object",
                "properties": {
                    "thesis_opener": {"type": "string"},
                    "thesis_paragraph": {"type": "string"},
                    "key_takeaways": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 5,
                        "maxItems": 5,
                    },
                },
                "required": ["thesis_opener", "thesis_paragraph", "key_takeaways"],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Section 2 -- Business Snapshot
# --------------------------------------------------------------------------- #
def validate_business(p: dict) -> list[str]:
    out: list[str] = []
    if not isinstance(p, dict):
        return ["payload is not an object"]
    desc = p.get("description")
    if not _is_str(desc):
        out.append("description missing")
    elif not (50 <= _wc(desc) <= 260):
        out.append(f"description word count {_wc(desc)} outside [50, 260]")
    gtm = p.get("gtm")
    if not _is_str(gtm) or _wc(gtm) < 20:
        out.append("gtm missing or too short (>= 20 words)")
    debate = p.get("debate_question")
    if not _is_str(debate):
        out.append("debate_question missing")

    def _seg(s):
        if not isinstance(s, dict):
            return "not an object"
        if not _is_str(s.get("name")):
            return "name missing"
        if not _is_str(s.get("detail")):
            return "detail missing"
        return ""

    _check_list(out, p, "segments", 2, 6, _seg, "segment")
    return out


BUSINESS_SCHEMA = {
    "key": "business",
    "title": "Business Snapshot",
    "model": "sonar-pro",
    "max_chars": 8000,
    "validate": validate_business,
    "prompt": (
        "Describe the business model and the single sharpest debate. Return JSON "
        "with: description (50-260 words: what the company sells, to whom, and how "
        "it monetizes, grounded in the data block), gtm (>= 20 words on the "
        "go-to-market motion), debate_question (ONE crisp either/or sentence that "
        "names the bull/bear fault line with a number in it), and segments (2-6 "
        "objects, each {name, detail}, covering the reportable or product "
        "segments). Never write the literal word MISSING; use an em-dash."
    ),
    "json_schema": {
        "type": "json_schema",
        "json_schema": {
            "name": "drilldown_business",
            "schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "gtm": {"type": "string"},
                    "debate_question": {"type": "string"},
                    "segments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                            "required": ["name", "detail"],
                        },
                    },
                },
                "required": ["description", "gtm", "debate_question", "segments"],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Section 3 -- Quant Snapshot (8q financials, KPIs, sensitivity)
# --------------------------------------------------------------------------- #
def validate_quant(p: dict) -> list[str]:
    out: list[str] = []
    if not isinstance(p, dict):
        return ["payload is not an object"]

    def _frow(r):
        if not isinstance(r, dict):
            return "not an object"
        if not _is_str(r.get("period")):
            return "period missing"
        # revenue / growth / margins / fcf may be strings ("$531.8M", "+24.1%")
        # or em-dash; require the headline fields to be present as strings.
        for fld in ("revenue", "rev_growth", "fcf"):
            if not _is_str(r.get(fld)):
                return f"{fld} missing"
        return ""

    # 8 quarterly OR up to 5 annual rows are both acceptable evidence; require
    # at least 5 financial rows total.
    _check_list(out, p, "financial_rows", 5, 12, _frow, "financial row")

    def _kpi(k):
        if not isinstance(k, dict):
            return "not an object"
        if not _is_str(k.get("name")):
            return "name missing"
        if not _is_str(k.get("value")):
            return "value missing"
        return ""

    _check_list(out, p, "kpis", 4, 12, _kpi, "kpi")

    gaap = p.get("gaap_vs_nongaap")
    if not _is_str(gaap) or _wc(gaap) < 15:
        out.append("gaap_vs_nongaap missing or too short")
    return out


QUANT_SCHEMA = {
    "key": "quant",
    "title": "Quant Snapshot",
    # The ONLY section that uses deep-research: multi-quarter financial sourcing
    # benefits from broad search. The JSON payload is small (rows of strings),
    # so even if deep-research is verbose the response stays well under the
    # truncation threshold that wrecked the single-shot HTML generator.
    "model": "sonar-deep-research",
    "max_chars": 12000,
    "validate": validate_quant,
    "prompt": (
        "Build the quantitative snapshot from verified filings and the data block. "
        "Return JSON with: financial_rows (5-12 rows, each {period, revenue, "
        "rev_growth, gross_margin, op_income, fcf, fcf_margin}; period/revenue/"
        "rev_growth/fcf are required; mix the last several quarters with FY actuals "
        "and clearly-marked FYxxE estimates; every value is a SHORT STRING like "
        "'$531.8M' or '+24.1%'), kpis (4-12 objects, each {name, value, trend}; "
        "for ARR-led SaaS names you MUST include ARR, Net new ARR, NRR, RPO and "
        "where disclosed cRPO), and gaap_vs_nongaap (>= 15 words distinguishing "
        "GAAP from non-GAAP and noting SBC). Use an em-dash for any cell you cannot "
        "verify; never write the literal word MISSING and never invent a number."
    ),
    "json_schema": {
        "type": "json_schema",
        "json_schema": {
            "name": "drilldown_quant",
            "schema": {
                "type": "object",
                "properties": {
                    "financial_rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "period": {"type": "string"},
                                "revenue": {"type": "string"},
                                "rev_growth": {"type": "string"},
                                "gross_margin": {"type": "string"},
                                "op_income": {"type": "string"},
                                "fcf": {"type": "string"},
                                "fcf_margin": {"type": "string"},
                            },
                            "required": ["period", "revenue", "rev_growth", "fcf"],
                        },
                    },
                    "kpis": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                                "trend": {"type": "string"},
                            },
                            "required": ["name", "value"],
                        },
                    },
                    "gaap_vs_nongaap": {"type": "string"},
                },
                "required": ["financial_rows", "kpis", "gaap_vs_nongaap"],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Section 4 -- Strategic Catalysts
# --------------------------------------------------------------------------- #
def validate_catalysts(p: dict) -> list[str]:
    out: list[str] = []
    if not isinstance(p, dict):
        return ["payload is not an object"]

    def _cat(c):
        if not isinstance(c, dict):
            return "not an object"
        for fld in ("date", "event", "what_to_watch", "bull_signal", "bear_signal"):
            if not _is_str(c.get(fld)):
                return f"{fld} missing"
        return ""

    _check_list(out, p, "catalysts", 3, 8, _cat, "catalyst")
    tape = p.get("top_catalyst_note")
    if not _is_str(tape) or _wc(tape) < 25:
        out.append("top_catalyst_note missing or too short (>= 25 words)")
    return out


CATALYSTS_SCHEMA = {
    "key": "catalysts",
    "title": "Strategic Catalysts",
    "model": "sonar-pro",
    "max_chars": 9000,
    "validate": validate_catalysts,
    "prompt": (
        "Identify the upcoming catalysts and watch items. Return JSON with: "
        "catalysts (3-8 objects, each {date, event, what_to_watch, bull_signal, "
        "bear_signal}; date is an ISO date or quarter label; order soonest-first; "
        "what_to_watch/bull_signal/bear_signal are concrete and specific to a "
        "number or threshold), and top_catalyst_note (>= 25 words naming the "
        "single highest-probability tape-mover and why). Never write MISSING."
    ),
    "json_schema": {
        "type": "json_schema",
        "json_schema": {
            "name": "drilldown_catalysts",
            "schema": {
                "type": "object",
                "properties": {
                    "catalysts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string"},
                                "event": {"type": "string"},
                                "what_to_watch": {"type": "string"},
                                "bull_signal": {"type": "string"},
                                "bear_signal": {"type": "string"},
                            },
                            "required": [
                                "date", "event", "what_to_watch",
                                "bull_signal", "bear_signal",
                            ],
                        },
                    },
                    "top_catalyst_note": {"type": "string"},
                },
                "required": ["catalysts", "top_catalyst_note"],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Section 5 -- Valuation (multiples, comps, DCF-lite) + competitive landscape
# --------------------------------------------------------------------------- #
def validate_valuation(p: dict) -> list[str]:
    out: list[str] = []
    if not isinstance(p, dict):
        return ["payload is not an object"]

    def _vrow(r):
        if not isinstance(r, dict):
            return "not an object"
        if not _is_str(r.get("metric")):
            return "metric missing"
        if not _is_str(r.get("value")):
            return "value missing"
        return ""

    _check_list(out, p, "valuation_rows", 5, 16, _vrow, "valuation row")

    def _comp(c):
        if not isinstance(c, dict):
            return "not an object"
        if not _is_str(c.get("company")):
            return "company missing"
        return ""

    _check_list(out, p, "comps", 2, 8, _comp, "comp")

    underwriting = p.get("underwriting")
    if not _is_str(underwriting) or _wc(underwriting) < 40:
        out.append("underwriting missing or too short (>= 40 words)")

    tam = p.get("tam_note")
    if not _is_str(tam) or _wc(tam) < 20:
        out.append("tam_note missing or too short (>= 20 words)")

    def _competitor(c):
        if not isinstance(c, dict):
            return "not an object"
        if not _is_str(c.get("name")):
            return "name missing"
        if not _is_str(c.get("threat")):
            return "threat missing"
        return ""

    _check_list(out, p, "competitors", 3, 8, _competitor, "competitor")
    return out


VALUATION_SCHEMA = {
    "key": "valuation",
    "title": "Valuation and Competitive Positioning",
    "model": "sonar-pro",
    "max_chars": 12000,
    "validate": validate_valuation,
    "prompt": (
        "Lay out valuation and competitive positioning. Return JSON with: "
        "valuation_rows (5-16 objects, each {metric, value, notes}; cover EV/Rev, "
        "EV/FCF, PEG or Rule-of-40, net cash and similar, using the data block's "
        "multiples), comps (2-8 peer objects, each {company, revenue, rev_growth, "
        "gross_margin, fcf_margin, ev_rev, threat}), underwriting (>= 40 words on "
        "what today's multiple implies the market is assuming), tam_note (>= 20 "
        "words on TAM and category growth with a source), and competitors (3-8 "
        "objects, each {name, position, threat, differentiation}). Em-dash for "
        "unverifiable cells; never write the literal word MISSING."
    ),
    "json_schema": {
        "type": "json_schema",
        "json_schema": {
            "name": "drilldown_valuation",
            "schema": {
                "type": "object",
                "properties": {
                    "valuation_rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "metric": {"type": "string"},
                                "value": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                            "required": ["metric", "value"],
                        },
                    },
                    "comps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "company": {"type": "string"},
                                "revenue": {"type": "string"},
                                "rev_growth": {"type": "string"},
                                "gross_margin": {"type": "string"},
                                "fcf_margin": {"type": "string"},
                                "ev_rev": {"type": "string"},
                                "threat": {"type": "string"},
                            },
                            "required": ["company"],
                        },
                    },
                    "underwriting": {"type": "string"},
                    "tam_note": {"type": "string"},
                    "competitors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "position": {"type": "string"},
                                "threat": {"type": "string"},
                                "differentiation": {"type": "string"},
                            },
                            "required": ["name", "threat"],
                        },
                    },
                },
                "required": [
                    "valuation_rows", "comps", "underwriting",
                    "tam_note", "competitors",
                ],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Section 6 -- Bull / Base / Bear (LOCKED schema, lives in drilldown_renderer)
# --------------------------------------------------------------------------- #
def validate_bbb(p: dict) -> list[str]:
    try:
        _validate_bbb_payload(p)
        return []
    except BBBValidationError as exc:
        return [str(exc)]


BBB_SCHEMA = {
    "key": "bbb",
    "title": "Investment Overview - Bull / Base / Bear",
    "model": "sonar-reasoning-pro",
    "max_chars": 9000,
    "validate": validate_bbb,
    "prompt": (
        "Construct the Bull / Base / Bear scenario framework. Return JSON with "
        "bull_case, base_case, bear_case, and probability_rationale.\n"
        "- Each case is an object with: price_target_low and price_target_high "
        "(numbers, dollars; low <= high), probability_pct (integer; the three "
        "MUST sum to EXACTLY 100), horizon_months (integer, typically 12), and "
        "thesis_bullets (EXACTLY 3 full-sentence strings, each 20-120 words, each "
        "grounded in a number from the data block).\n"
        "- claim_refs: a list of integers for each case (citation ids; 1,2,3 is "
        "fine if you have no specific source ids).\n"
        "- probability_rationale: 60-240 words explaining the weighting and the "
        "asymmetry of the payoff. Anchor the targets to the current price in the "
        "data block. Never write the literal word MISSING."
    ),
    "json_schema": {
        "type": "json_schema",
        "json_schema": {
            "name": "drilldown_bbb",
            "schema": {
                "type": "object",
                "properties": {
                    **{
                        case: {
                            "type": "object",
                            "properties": {
                                "price_target_low": {"type": "number"},
                                "price_target_high": {"type": "number"},
                                "probability_pct": {"type": "integer"},
                                "horizon_months": {"type": "integer"},
                                "thesis_bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 3,
                                    "maxItems": 3,
                                },
                                "claim_refs": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                            },
                            "required": [
                                "price_target_low", "price_target_high",
                                "probability_pct", "horizon_months",
                                "thesis_bullets", "claim_refs",
                            ],
                        }
                        for case in ("bull_case", "base_case", "bear_case")
                    },
                    "probability_rationale": {"type": "string"},
                },
                "required": [
                    "bull_case", "base_case", "bear_case",
                    "probability_rationale",
                ],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Section 7 -- Debate / Key Risks (longs vs shorts) + diligence questions
# --------------------------------------------------------------------------- #
def validate_debate(p: dict) -> list[str]:
    out: list[str] = []
    if not isinstance(p, dict):
        return ["payload is not an object"]

    def _risk(r):
        if not isinstance(r, dict):
            return "not an object"
        if not _is_str(r.get("title")):
            return "title missing"
        if not _is_str(r.get("detail")):
            return "detail missing"
        return ""

    _check_list(out, p, "bear_risks", 3, 7, _risk, "bear risk")

    def _rebut(r):
        if not isinstance(r, dict):
            return "not an object"
        if not _is_str(r.get("detail")):
            return "detail missing"
        return ""

    _check_list(out, p, "bull_rebuttals", 3, 7, _rebut, "bull rebuttal")

    def _dq(q):
        if not _is_str(q):
            return "not a non-empty string"
        if _wc(q) < 8:
            return "too short (>= 8 words)"
        return ""

    _check_list(out, p, "diligence_questions", 3, 8, _dq, "diligence question")
    return out


DEBATE_SCHEMA = {
    "key": "debate",
    "title": "Debate / Key Risks",
    "model": "sonar-pro",
    "max_chars": 12000,
    "validate": validate_debate,
    "prompt": (
        "Map the long/short debate and the diligence agenda. Return JSON with: "
        "bear_risks (3-7 objects, each {title, quant, detail}; quant is a short "
        "magnitude tag like '-$20M' or '-3pts'; detail is 1-3 sentences), "
        "bull_rebuttals (3-7 objects, each {title, detail} answering the bear "
        "risks directly), and diligence_questions (3-8 strings, each >= 8 words, "
        "each a specific question an analyst would ask management). Never write "
        "the literal word MISSING."
    ),
    "json_schema": {
        "type": "json_schema",
        "json_schema": {
            "name": "drilldown_debate",
            "schema": {
                "type": "object",
                "properties": {
                    "bear_risks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "quant": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                            "required": ["title", "detail"],
                        },
                    },
                    "bull_rebuttals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                            "required": ["detail"],
                        },
                    },
                    "diligence_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "bear_risks", "bull_rebuttals", "diligence_questions",
                ],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Ordered registry. The orchestrator iterates this list.
# --------------------------------------------------------------------------- #
SECTION_SCHEMAS = [
    SUMMARY_SCHEMA,
    BUSINESS_SCHEMA,
    QUANT_SCHEMA,
    CATALYSTS_SCHEMA,
    VALUATION_SCHEMA,
    BBB_SCHEMA,
    DEBATE_SCHEMA,
]

SCHEMA_BY_KEY = {s["key"]: s for s in SECTION_SCHEMAS}


__all__ = [
    "SECTION_SCHEMAS",
    "SCHEMA_BY_KEY",
    "SUMMARY_SCHEMA",
    "BUSINESS_SCHEMA",
    "QUANT_SCHEMA",
    "CATALYSTS_SCHEMA",
    "VALUATION_SCHEMA",
    "BBB_SCHEMA",
    "DEBATE_SCHEMA",
    "validate_summary",
    "validate_business",
    "validate_quant",
    "validate_catalysts",
    "validate_valuation",
    "validate_bbb",
    "validate_debate",
]
