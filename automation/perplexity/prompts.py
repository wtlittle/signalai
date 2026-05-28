"""
ALL prompt templates for Perplexity API calls.
Centralized here for auditability and easy tuning.
Each function returns a (system_prompt, user_prompt) tuple or just a user_prompt string.
"""

import json as _json


# ---------------------------------------------------------------------------
# Legacy prompt builders (pre-Stage 1) — kept for backward compatibility.
# New callers should use build_pre_earnings_prompt_v2 / build_post_earnings_prompt_v2.
# ---------------------------------------------------------------------------


def build_pre_earnings_prompt(
    ticker: str,
    company: str,
    earnings_date: str,
    days_until: int,
    consensus: dict,
) -> str:
    """Pre-earnings research brief — structured JSON output (legacy)."""
    rev_est = consensus.get("rev_est", "?")
    rev_growth = consensus.get("rev_growth", "?")
    eps_est = consensus.get("eps_est", "?")
    quarter = consensus.get("quarter", "")
    return f"""You are a buy-side equity analyst. Generate a pre-earnings brief for {company} ({ticker}).
Earnings date: {earnings_date} ({days_until} days away). Quarter: {quarter}.
Consensus: Rev ${rev_est}B ({rev_growth}% YoY), EPS ${eps_est}.

Return ONLY this JSON structure. No prose. No markdown. No preamble.

{{
  "setup": "2-3 sentences: stock price setup, key technical levels, YTD performance vs sector",
  "key_debates": ["debate 1 (max 40 words)", "debate 2", "debate 3"],
  "what_matters": ["metric 1 with threshold", "metric 2 with threshold", "metric 3"],
  "guidance_watch": "1 sentence on the most important guidance line to watch",
  "options_implied_move": "X%",
  "scenarios": {{
    "bull": {{"probability": "25%", "trigger": "...", "stock_move": "+X%"}},
    "base": {{"probability": "50%", "trigger": "...", "stock_move": "+/-X%"}},
    "bear": {{"probability": "25%", "trigger": "...", "stock_move": "-X%"}}
  }},
  "recent_news": ["item 1 (max 20 words)", "item 2"],
  "analyst_changes": ["upgrade/downgrade 1 (max 20 words)", "item 2"],
  "sources": ["url1", "url2"]
}}"""


def build_post_earnings_prompt(
    ticker: str,
    company: str,
    earnings_date: str,
    actuals: dict,
    pre_context: dict | None = None,
) -> str:
    """Post-earnings research brief — structured JSON output (legacy)."""
    rev_actual = actuals.get("rev_actual", "?")
    rev_est = actuals.get("rev_est", "?")
    rev_beat_miss = actuals.get("rev_beat_miss", "?")
    eps_actual = actuals.get("eps_actual", "?")
    eps_est = actuals.get("eps_est", "?")
    eps_beat_miss = actuals.get("eps_beat_miss", "?")
    stock_reaction = actuals.get("stock_reaction", "?")

    pre_summary = ""
    if pre_context and not pre_context.get("dry_run"):
        setup = pre_context.get("setup", "")
        debates = pre_context.get("key_debates", [])
        if setup or debates:
            pre_summary = f"""
Pre-earnings context already known (do NOT re-research these facts):
- Setup: {setup}
- Key debates: {'; '.join(debates) if debates else 'N/A'}
Focus only on what CHANGED vs those expectations."""

    return f"""You are a buy-side equity analyst. Write a post-earnings brief for {company} ({ticker}).
Reported: {earnings_date}.
Actuals: Rev ${rev_actual} vs ${rev_est} est ({rev_beat_miss}),
EPS ${eps_actual} vs ${eps_est} est ({eps_beat_miss}),
Stock reaction: {stock_reaction}.
{pre_summary}

Return ONLY this JSON. No prose. No markdown. No preamble.

{{
  "headline": "1 sentence summary of the print",
  "beat_miss_quality": "beat/miss/in-line + was it quality or noise? (max 30 words)",
  "key_metrics": ["metric: actual vs est (1 line each)", "..."],
  "guidance": "next Q and FY guidance vs consensus (1-2 sentences)",
  "management_tone": "bullish/neutral/cautious + key quote or theme",
  "surprises": ["positive surprise 1", "negative surprise 1"],
  "thesis_impact": "reinforces/challenges/neutral + 1 sentence why",
  "analyst_reactions": ["analyst firm: action, new PT (max 15 words)", "..."],
  "stock_outlook": "1 sentence near-term view",
  "sources": ["url1", "url2"]
}}"""


# ---------------------------------------------------------------------------
# Stage 2 prompt builders — consume EarningsContext dict from Stage 1.
# ---------------------------------------------------------------------------

_SYSTEM_PRE = (
    "You are a buy-side equity research analyst at a long/short equity fund. "
    "You produce structured pre-earnings research notes. "
    "NEVER fabricate numbers -- only use the data provided in the EARNINGS CONTEXT below. "
    "If a field says NOT AVAILABLE, say so explicitly in your note. "
    "All numbers below come from finance_earnings_history, finance_estimates, and Finnhub. "
    "Use them as-is. Do NOT search the web for numbers. "
    "Always populate every field in the required output blocks. "
    "Use null only when a value is truly unknown. Never use em-dash placeholders."
)

_SYSTEM_POST = (
    "You are a buy-side equity research analyst at a long/short equity fund. "
    "You produce structured post-earnings research notes. "
    "NEVER fabricate numbers -- only use the data provided in the EARNINGS CONTEXT below. "
    "If a field says NOT AVAILABLE, say so explicitly in your note. "
    "All numbers below come from finance_earnings_history, finance_estimates, and Finnhub. "
    "Use them as-is. Do NOT search the web for numbers. "
    "If the company has reported the quarter, you MUST extract actuals from the "
    "provided context. Do NOT write 'no reported numbers' for a company that "
    "actually reported. Always populate every field in the required output blocks. "
    "Use null only when a value is truly unknown. Never use em-dash placeholders."
)

# Pre-earnings required output sections (matches existing notes/ convention).
_PRE_SECTIONS = """\
Required sections (match the existing note format exactly):
1. Set-up (price action, valuation, key context from last quarter)
2. Key Debates & Variant Perception (3-4 debates, each with bull/bear framing)
3. What Matters This Print (table: Metric | Consensus/Guidance | Why It Matters)
4. Scenario Grid (table: Scenario | EPS | Revenue | Key Assumptions | Price Target | Probability)

Label format for Key Debates and What Matters bullets:
- Each bullet MUST start with a short Title Case noun-phrase label (<=30 chars),
  followed by a colon, then the detail. Example: "Data Center Growth: ..."
- Labels must NOT be questions or full sentences. Good: "Blackwell Ramp Timing".
  Bad: "Is guidance conservative?" or "Whether cloud growth accelerates".

Include a *Sources:* line at the end listing data sources used.

After the note body, emit a fenced JSON block labeled ```json setup_vs_consensus
with these exact keys (use null when unknown, never em-dashes):
{
  "street_rev_estimate": "<next-Q revenue consensus, e.g. 43.2B>",
  "street_eps_estimate": "<next-Q EPS consensus, e.g. 0.88>",
  "last_q_rev_surprise_pct": <number or null>,
  "last_q_eps_surprise_pct": <number or null>,
  "current_fy_consensus_rev": "<FY revenue consensus, e.g. 200.5B>",
  "current_fy_consensus_eps": "<FY EPS consensus, e.g. 4.10>",
  "prior_fy_guide_vs_current_consensus_pct": <number or null>
}
```"""

# Post-earnings required output sections.
_POST_SECTIONS = """\
Required sections (match the existing note format exactly):
1. Headline (1 sentence summary + Beat/Miss Quality assessment)
2. Key Metrics (bulleted list: metric actual vs est)
3. Guidance and Tone (guidance vs consensus + Management Tone)
4. Surprises / Disappointments (bulleted, positive and negative)
5. Thesis Impact (reinforces/challenges/neutral + reasoning)
6. Analyst Reactions (bulleted list of sell-side actions)
7. Near-Term Outlook (1 paragraph stock view)

For T1 tickers also include:
8. Read-Through to Peers (how this print affects peer setups)

Include a *Sources:* line at the end listing data sources used.

After the note body, emit TWO fenced JSON blocks.

Block 1 -- ```json results_vs_consensus
{
  "in_quarter_rev_actual": "<e.g. 44.5B>",
  "in_quarter_rev_consensus": "<e.g. 43.2B>",
  "in_quarter_rev_surprise_pct": <number>,
  "in_quarter_rev_yoy_pct": <number or null>,
  "in_quarter_eps_actual": "<e.g. 0.92>",
  "in_quarter_eps_consensus": "<e.g. 0.88>",
  "in_quarter_eps_surprise_pct": <number>
}
```

Block 2 -- ```json guide_vs_consensus
{
  "next_q_rev_guide_midpoint": "<e.g. 48.0B or null>",
  "next_q_rev_consensus_prior": "<e.g. 44.0B or null>",
  "next_q_rev_guide_vs_consensus_pct": <number or null>,
  "next_q_eps_guide_midpoint": "<or null>",
  "next_q_eps_consensus_prior": "<or null>",
  "next_q_eps_guide_vs_consensus_pct": <number or null>,
  "fy_rev_guide_midpoint_new": "<or null>",
  "fy_rev_guide_midpoint_prior": "<or null>",
  "fy_rev_consensus_prior": "<or null>",
  "fy_rev_guide_change_vs_consensus_pct": <number or null>,
  "fy_rev_guide_change_vs_prior_pct": <number or null>,
  "fy_eps_guide_midpoint_new": "<or null>",
  "fy_eps_guide_midpoint_prior": "<or null>",
  "fy_eps_consensus_prior": "<or null>",
  "fy_eps_guide_change_vs_consensus_pct": <number or null>,
  "fy_eps_guide_change_vs_prior_pct": <number or null>
}
```

IMPORTANT: You MUST populate the results_vs_consensus block with actual numbers
from the THIS QUARTER ACTUALS and FORWARD CONSENSUS data provided. If the
company reported, every field in results_vs_consensus must have a value.
Use null only when the data is genuinely not provided in the context."""


def _safe(val, fmt: str = "", fallback: str = "?") -> str:
    """Format a value, returning fallback if None."""
    if val is None:
        return fallback
    if fmt:
        try:
            return format(val, fmt)
        except (TypeError, ValueError):
            return str(val)
    return str(val)


def _na(field_name: str, errors: list[dict]) -> str:
    """Check if a field is in the errors list and return NOT AVAILABLE string."""
    for e in errors:
        if e.get("field") == field_name:
            return f"NOT AVAILABLE: {e.get('reason', 'unknown error')}"
    return ""


def _render_quote(quote: dict | None, errors: list[dict]) -> str:
    """Render the QUOTE section from context."""
    err = _na("quote", errors)
    if err:
        return f"=== QUOTE ===\n{err}"
    if not quote:
        return "=== QUOTE ===\nNOT AVAILABLE"
    price = _safe(quote.get("price"), ",.2f")
    chg = _safe(quote.get("change_1d_pct"), "+.2f")
    hi = _safe(quote.get("wk52_high"), ",.2f")
    lo = _safe(quote.get("wk52_low"), ",.2f")
    off_hi = _safe(quote.get("pct_off_52w_high"), ".1f")
    mcap = quote.get("market_cap")
    mcap_str = f"${mcap / 1e9:.1f}B" if mcap else "?"
    return (
        f"=== QUOTE ===\n"
        f"Price: ${price}  ({chg}% today)\n"
        f"52-week range: ${lo} - ${hi}\n"
        f"{off_hi}% off 52-week high\n"
        f"Market cap: {mcap_str}"
    )


def _render_consensus(consensus: dict | None, errors: list[dict]) -> str:
    """Render forward consensus section."""
    err = _na("consensus_forward", errors)
    if err:
        return f"=== FORWARD CONSENSUS ===\n{err}"
    if not consensus:
        return "=== FORWARD CONSENSUS ===\nNOT AVAILABLE"
    fq_rev = _safe(consensus.get("fq_revenue"))
    fq_eps = _safe(consensus.get("fq_eps"))
    fy_rev = _safe(consensus.get("fy_revenue"))
    fy_eps = _safe(consensus.get("fy_eps"))
    return (
        f"=== FORWARD CONSENSUS ===\n"
        f"Next FQ: rev ${fq_rev}, EPS ${fq_eps}\n"
        f"Current FY: rev ${fy_rev}, EPS ${fy_eps}"
    )


def _render_history(history: list | None, avg_move: float | None,
                    implied: dict | None, errors: list[dict]) -> str:
    """Render historical earnings table."""
    err = _na("history_8q", errors)
    if err:
        return f"=== HISTORICAL EARNINGS (LAST 8 QUARTERS) ===\n{err}"
    if not history:
        return "=== HISTORICAL EARNINGS (LAST 8 QUARTERS) ===\nNOT AVAILABLE"
    lines = ["=== HISTORICAL EARNINGS (LAST 8 QUARTERS) ==="]
    lines.append("Period | Act Rev | Est Rev | Rev Surp% | Act EPS | Est EPS | EPS Surp% | 1d Move | IV Move")
    lines.append("--- | --- | --- | --- | --- | --- | --- | --- | ---")
    for q in history:
        period = _safe(q.get("period"))
        ar = _safe(q.get("actual_revenue"))
        er = _safe(q.get("estimated_revenue"))
        rs = _safe(q.get("revenue_surprise_pct"))
        ae = _safe(q.get("actual_eps"))
        ee = _safe(q.get("estimated_eps"))
        es = _safe(q.get("eps_surprise_pct"))
        mv = _safe(q.get("post_earnings_move_1d_pct"))
        iv = _safe(q.get("expected_move_pct"))
        lines.append(f"{period} | {ar} | {er} | {rs}% | {ae} | {ee} | {es}% | {mv}% | {iv}%")
    avg_str = _safe(avg_move, ".1f")
    iv_val = _safe(implied.get("value_pct") if implied else None, ".1f")
    lines.append(f"\nAverage 1d post-earnings move: {avg_str}%")
    lines.append(f"This quarter's IV-implied move: {iv_val}%")
    return "\n".join(lines)


def _render_rec_drift(drift: list | None, errors: list[dict]) -> str:
    """Render analyst recommendation drift."""
    err = _na("recommendation_drift", errors)
    if err:
        return f"=== ANALYST RECOMMENDATION DRIFT (LAST 4 MONTHS) ===\n{err}"
    if not drift:
        return "=== ANALYST RECOMMENDATION DRIFT (LAST 4 MONTHS) ===\nNOT AVAILABLE"
    lines = ["=== ANALYST RECOMMENDATION DRIFT (LAST 4 MONTHS) ==="]
    lines.append("Period | SB | B | H | S | SS")
    lines.append("--- | --- | --- | --- | --- | ---")
    for d in drift:
        period = _safe(d.get("period"))
        sb = _safe(d.get("strong_buy"))
        b = _safe(d.get("buy"))
        h = _safe(d.get("hold"))
        s = _safe(d.get("sell"))
        ss = _safe(d.get("strong_sell"))
        lines.append(f"{period} | {sb} | {b} | {h} | {s} | {ss}")
    return "\n".join(lines)


def _render_narrative(narrative: str | None, errors: list[dict]) -> str:
    """Render sell-side research narrative."""
    err = _na("analyst_research_narrative", errors)
    if err:
        return f"=== SELL-SIDE RESEARCH NARRATIVE ===\n{err}"
    if not narrative:
        return "=== SELL-SIDE RESEARCH NARRATIVE ===\nNOT AVAILABLE"
    return f"=== SELL-SIDE RESEARCH NARRATIVE ===\n{narrative}"


def _render_segments(segments: list | None, errors: list[dict]) -> str:
    """Render segment revenue table."""
    err = _na("segments_4q", errors)
    if err:
        return f"=== SEGMENT REVENUE (LAST 4 QUARTERS) ===\n{err}"
    if not segments:
        return "=== SEGMENT REVENUE (LAST 4 QUARTERS) ===\nNOT AVAILABLE"
    lines = ["=== SEGMENT REVENUE (LAST 4 QUARTERS) ==="]
    for q in segments:
        period = _safe(q.get("period"))
        segs = q.get("segments", [])
        seg_parts = [f"{s.get('name', '?')}: ${_safe(s.get('revenue'))}" for s in segs]
        lines.append(f"{period}: {', '.join(seg_parts)}")
    return "\n".join(lines)


def _render_adj_metrics(metrics: list | None, errors: list[dict]) -> str:
    """Render adjusted metrics table."""
    err = _na("adj_metrics_8q", errors)
    if err:
        return f"=== ADJUSTED METRICS (LAST 8 QUARTERS) ===\n{err}"
    if not metrics:
        return "=== ADJUSTED METRICS (LAST 8 QUARTERS) ===\nNOT AVAILABLE"
    lines = ["=== ADJUSTED METRICS (LAST 8 QUARTERS) ==="]
    lines.append("Period | Adj EPS | Adj EBITDA | FCF | Gross Margin | Op Margin")
    lines.append("--- | --- | --- | --- | --- | ---")
    for m in metrics:
        period = _safe(m.get("period"))
        eps = _safe(m.get("adj_eps"))
        ebitda = _safe(m.get("adj_ebitda"))
        fcf = _safe(m.get("fcf"))
        gm = _safe(m.get("gross_margin_pct"))
        om = _safe(m.get("operating_margin_pct"))
        lines.append(f"{period} | {eps} | {ebitda} | {fcf} | {gm}% | {om}%")
    return "\n".join(lines)


def _render_peers(peers: list | None, errors: list[dict]) -> str:
    """Render peer pack table."""
    err = _na("peers", errors)
    if err:
        return f"=== PEERS ===\n{err}"
    if not peers:
        return "=== PEERS ===\nNone (T2 tier or no peer data available)"
    lines = ["=== PEERS ==="]
    for p in peers:
        tk = _safe(p.get("ticker"))
        rel = _safe(p.get("relation"))
        lp = p.get("last_print")
        lp_str = _json.dumps(lp) if lp else "no data"
        fc = p.get("forward_consensus")
        fc_str = _json.dumps(fc) if fc else "no data"
        lines.append(f"- {tk} ({rel}): last print={lp_str}, fwd consensus={fc_str}")
    return "\n".join(lines)


def _render_insider(insider: dict | None, errors: list[dict]) -> str:
    """Render insider activity."""
    err = _na("insider_tx_90d", errors)
    if err:
        return f"=== INSIDER ACTIVITY (90D) ===\n{err}"
    if not insider:
        return "=== INSIDER ACTIVITY (90D) ===\nNOT AVAILABLE"
    buys = _safe(insider.get("n_buys"))
    sells = _safe(insider.get("n_sells"))
    net = _safe(insider.get("net_value_usd"))
    return (
        f"=== INSIDER ACTIVITY (90D) ===\n"
        f"Buys: {buys}  Sells: {sells}  Net: ${net}"
    )


def _render_industry_pack(pack: dict | None, errors: list[dict]) -> str:
    """Render industry pack."""
    err = _na("industry_pack", errors)
    if err:
        return f"=== INDUSTRY PACK ===\n{err}"
    if not pack:
        return "=== INDUSTRY PACK ===\nNot available"
    subsector = _safe(pack.get("subsector"))
    week = _safe(pack.get("week_of"))
    data = pack.get("pack")
    data_str = _json.dumps(data, indent=2) if data else "no pack data"
    return f"=== INDUSTRY PACK ===\nSubsector: {subsector} (week of {week})\n{data_str}"


def _render_errors(errors: list[dict]) -> str:
    """Render data gaps section."""
    if not errors:
        return "=== DATA GAPS ===\nNone -- all fields populated."
    lines = ["=== DATA GAPS ==="]
    for e in errors:
        lines.append(f"- {e.get('field', '?')}: {e.get('reason', 'unknown')}")
    return "\n".join(lines)


def _render_actuals(actuals: dict | None, errors: list[dict]) -> str:
    """Render this-quarter actuals (post-earnings only)."""
    err = _na("this_quarter_actuals", errors)
    if err:
        return f"=== THIS QUARTER ACTUALS ===\n{err}"
    if not actuals:
        return "=== THIS QUARTER ACTUALS ===\nNOT AVAILABLE"
    lines = ["=== THIS QUARTER ACTUALS ==="]
    ar = _safe(actuals.get("actual_revenue"))
    er = _safe(actuals.get("estimated_revenue"))
    rs = _safe(actuals.get("revenue_surprise_pct"))
    ae = _safe(actuals.get("actual_eps"))
    ee = _safe(actuals.get("estimated_eps"))
    es = _safe(actuals.get("eps_surprise_pct"))
    mv = _safe(actuals.get("post_earnings_move_1d_pct"))
    lines.append(f"Revenue: ${ar} vs ${er} est ({rs}% surprise)")
    lines.append(f"EPS: ${ae} vs ${ee} est ({es}% surprise)")
    lines.append(f"Post-earnings 1d move: {mv}%")
    return "\n".join(lines)


def _render_post_consensus(consensus: dict | None, errors: list[dict]) -> str:
    """Render post-print consensus revisions."""
    err = _na("consensus_post_print", errors)
    if err:
        return f"=== POST-PRINT CONSENSUS REVISION ===\n{err}"
    if not consensus:
        return "=== POST-PRINT CONSENSUS REVISION ===\nNOT AVAILABLE"
    fq_rev = _safe(consensus.get("fq_revenue"))
    fq_eps = _safe(consensus.get("fq_eps"))
    fy_rev = _safe(consensus.get("fy_revenue"))
    fy_eps = _safe(consensus.get("fy_eps"))
    direction = _safe(consensus.get("revision_direction"))
    return (
        f"=== POST-PRINT CONSENSUS REVISION ===\n"
        f"Next FQ: rev ${fq_rev}, EPS ${fq_eps}\n"
        f"Current FY: rev ${fy_rev}, EPS ${fy_eps}\n"
        f"Revision direction: {direction}"
    )


def _render_transcript_excerpt(transcript: str | None, errors: list[dict],
                                max_chars: int = 3000) -> str:
    """Render transcript excerpt (post-earnings only)."""
    err = _na("transcript", errors)
    if err:
        return f"=== TRANSCRIPT EXCERPT ===\n{err}"
    if not transcript:
        return "=== TRANSCRIPT EXCERPT ===\nNOT AVAILABLE"
    excerpt = transcript[:max_chars]
    if len(transcript) > max_chars:
        excerpt += "\n[... truncated ...]"
    return f"=== TRANSCRIPT EXCERPT ===\n{excerpt}"


def _compact_context(context: dict, target_tokens: int = 10000) -> dict:
    """Return a compacted copy of context to fit within token budget.

    Heuristic: 1 token ~ 4 chars. We estimate the rendered prompt size and
    progressively trim the largest variable-length fields if we exceed the
    target.
    """
    import copy
    ctx = copy.deepcopy(context)
    char_budget = target_tokens * 4

    def _estimate_chars(c: dict) -> int:
        """Rough character count of the serializable fields."""
        total = 0
        for key in ("history_8q", "adj_metrics_8q", "segments_4q", "peers",
                     "recommendation_drift", "analyst_research_narrative",
                     "industry_pack", "transcript"):
            val = c.get(key)
            if val is not None:
                total += len(_json.dumps(val, default=str))
        # Fixed overhead for quote, consensus, insider, errors, metadata
        total += 2000
        return total

    est = _estimate_chars(ctx)
    if est <= char_budget:
        return ctx

    # Step 1: trim adj_metrics_8q to 4 quarters
    if ctx.get("adj_metrics_8q") and len(ctx["adj_metrics_8q"]) > 4:
        ctx["adj_metrics_8q"] = ctx["adj_metrics_8q"][:4]

    # Step 2: trim segments_4q to 2 quarters
    if ctx.get("segments_4q") and len(ctx["segments_4q"]) > 2:
        ctx["segments_4q"] = ctx["segments_4q"][:2]

    est = _estimate_chars(ctx)
    if est <= char_budget:
        return ctx

    # Step 3: trim transcript to 2000 chars
    if ctx.get("transcript") and len(ctx["transcript"]) > 2000:
        ctx["transcript"] = ctx["transcript"][:2000]

    # Step 4: trim history_8q to 4 quarters
    if ctx.get("history_8q") and len(ctx["history_8q"]) > 4:
        ctx["history_8q"] = ctx["history_8q"][:4]

    est = _estimate_chars(ctx)
    if est <= char_budget:
        return ctx

    # Step 5: truncate industry pack data
    if ctx.get("industry_pack") and ctx["industry_pack"].get("pack"):
        pack_str = _json.dumps(ctx["industry_pack"]["pack"], default=str)
        if len(pack_str) > 2000:
            ctx["industry_pack"]["pack"] = {"_truncated": True,
                                            "summary": pack_str[:2000]}

    # Step 6: limit peers to 6
    if ctx.get("peers") and len(ctx["peers"]) > 6:
        ctx["peers"] = ctx["peers"][:6]

    return ctx


def build_pre_earnings_prompt_v2(
    ticker: str,
    context: dict,
) -> tuple[str, str]:
    """Build a pre-earnings prompt from the Stage 1 EarningsContext dict.

    Returns (system_prompt, user_prompt) tuple.

    The context dict is the output of build_pre_earnings_context(ticker).
    Tier-aware: T1 includes full peers/segments/industry pack; T2 omits them.
    """
    ctx = _compact_context(context)
    errors = ctx.get("errors", [])
    tier = ctx.get("_tier", "T2")
    fiscal = ctx.get("fiscal_period", "?")

    sections = []
    sections.append(f"=== TICKER ===\n{ticker}  (Tier: {tier}, Fiscal period: {fiscal})")
    sections.append(_render_quote(ctx.get("quote"), errors))
    sections.append(_render_consensus(ctx.get("consensus_forward"), errors))
    sections.append(_render_history(ctx.get("history_8q"),
                                    ctx.get("post_earnings_move_avg_1d_pct"),
                                    ctx.get("implied_move"), errors))
    sections.append(_render_rec_drift(ctx.get("recommendation_drift"), errors))
    sections.append(_render_narrative(ctx.get("analyst_research_narrative"), errors))

    # Tier-aware: T1 gets segments, peers, industry pack; T2 skips them
    if tier == "T1":
        sections.append(_render_segments(ctx.get("segments_4q"), errors))
        sections.append(_render_adj_metrics(ctx.get("adj_metrics_8q"), errors))
        sections.append(_render_peers(ctx.get("peers"), errors))
    else:
        sections.append(_render_adj_metrics(ctx.get("adj_metrics_8q"), errors))

    sections.append(_render_insider(ctx.get("insider_tx_90d"), errors))

    if tier == "T1":
        sections.append(_render_industry_pack(ctx.get("industry_pack"), errors))

    sections.append(_render_errors(errors))
    sections.append("=" * 50)
    sections.append("NOW WRITE THE NOTE.\n")
    sections.append(_PRE_SECTIONS)

    user_prompt = "\n\n".join(sections)
    return (_SYSTEM_PRE, user_prompt)


def build_post_earnings_prompt_v2(
    ticker: str,
    context: dict,
) -> tuple[str, str]:
    """Build a post-earnings prompt from the Stage 1 EarningsContext dict.

    Returns (system_prompt, user_prompt) tuple.

    The context dict is the output of build_post_earnings_context(ticker).
    Tier-aware: T1 includes full peers/segments/industry pack; T2 omits them.
    """
    ctx = _compact_context(context)
    errors = ctx.get("errors", [])
    tier = ctx.get("_tier", "T2")
    fiscal = ctx.get("fiscal_period", "?")

    sections = []
    sections.append(f"=== TICKER ===\n{ticker}  (Tier: {tier}, Fiscal period: {fiscal})")
    sections.append(_render_quote(ctx.get("quote"), errors))
    sections.append(_render_actuals(ctx.get("this_quarter_actuals"), errors))
    sections.append(_render_consensus(ctx.get("consensus_forward"), errors))
    sections.append(_render_post_consensus(ctx.get("consensus_post_print"), errors))
    sections.append(_render_history(ctx.get("history_8q"),
                                    ctx.get("post_earnings_move_avg_1d_pct"),
                                    ctx.get("implied_move"), errors))
    sections.append(_render_rec_drift(ctx.get("recommendation_drift"), errors))
    sections.append(_render_narrative(ctx.get("analyst_research_narrative"), errors))

    if tier == "T1":
        sections.append(_render_segments(ctx.get("segments_4q"), errors))
        sections.append(_render_adj_metrics(ctx.get("adj_metrics_8q"), errors))
        sections.append(_render_peers(ctx.get("peers"), errors))
    else:
        sections.append(_render_adj_metrics(ctx.get("adj_metrics_8q"), errors))

    sections.append(_render_insider(ctx.get("insider_tx_90d"), errors))

    if tier == "T1":
        sections.append(_render_industry_pack(ctx.get("industry_pack"), errors))

    sections.append(_render_transcript_excerpt(ctx.get("transcript"), errors))
    sections.append(_render_errors(errors))
    sections.append("=" * 50)
    sections.append("NOW WRITE THE NOTE.\n")
    sections.append(_POST_SECTIONS)

    user_prompt = "\n\n".join(sections)
    return (_SYSTEM_POST, user_prompt)


def build_transcript_distill_prompt(
    ticker: str,
    company: str,
    earnings_date: str,
    transcript_text: str,
) -> tuple[str, str]:
    """Distill a scraped earnings call transcript into structured JSON."""
    system = (
        "You are a senior buy-side equity research analyst. "
        "Your job is to distill earnings call transcripts into structured, "
        "actionable intelligence. Return ONLY a single valid JSON object "
        "with the exact keys requested. No markdown fences, no commentary, no prose."
    )
    user = (
        f"Below is the earnings call transcript for {company} ({ticker}), "
        f"reported on {earnings_date}. Distill it into the following JSON structure.\n\n"
        f"TRANSCRIPT:\n{transcript_text}\n\n"
        f"Return ONLY this JSON object. No prose, no markdown, no preamble.\n\n"
        + _transcript_schema_block(ticker, company, earnings_date)
    )
    return system, user


def build_transcript_research_prompt(
    ticker: str,
    company: str,
    earnings_date: str,
) -> tuple[str, str]:
    """Perplexity-native fallback: research and synthesize transcript content
    without providing source text directly (used when Fool scraping fails)."""
    system = (
        "You are a senior buy-side equity research analyst. "
        "Your job is to research earnings calls and distill key intelligence "
        "into structured JSON. Return ONLY a single valid JSON object "
        "with the exact keys requested. No markdown fences, no commentary, no prose."
    )
    user = (
        f"Research the most recent earnings call for {company} ({ticker}), "
        f"reported around {earnings_date}. "
        f"Find the transcript or detailed coverage from Motley Fool, Seeking Alpha, "
        f"Quartr, or any reliable financial media source. "
        f"Then distill the content into the following JSON structure.\n\n"
        f"Return ONLY this JSON object. No prose, no markdown, no preamble.\n\n"
        + _transcript_schema_block(ticker, company, earnings_date)
    )
    return system, user


def _transcript_schema_block(ticker: str, company: str, earnings_date: str) -> str:
    """Shared JSON schema block embedded in both transcript prompt variants."""
    return f"""{{
  "ticker": "{ticker}",
  "company_name": "{company}",
  "earnings_date": "{earnings_date}",
  "quarter": "<e.g. Q1 FY2026>",
  "beat_miss_summary": "<1-sentence: beat/miss and what drove it>",
  "management_tone": "<one of: bullish | cautious | neutral | mixed>",
  "mgmt_key_points": [
    "<key point 1 from prepared remarks — max 40 words>",
    "<key point 2>",
    "<key point 3>",
    "<key point 4 (optional)>",
    "<key point 5 (optional)>"
  ],
  "guidance_statements": [
    "<verbatim or near-verbatim guidance quote 1>",
    "<guidance quote 2 (optional)>",
    "<guidance quote 3 (optional)>"
  ],
  "qa_key_exchanges": [
    {{
      "analyst": "<analyst name and firm>",
      "question": "<1-sentence question summary>",
      "answer": "<1-2 sentence answer summary>"
    }}
  ],
  "tone_signals": [
    "<language pattern signaling tone — e.g. 'macro uncertainty cited 4x'>"
  ],
  "key_metrics_discussed": [
    "<metric: actual vs. est — e.g. 'Cloud revenue: $28.5B, beat est $27.9B'>",
    "<metric 2>",
    "<metric 3>"
  ],
  "notable_quotes": [
    {{
      "speaker": "<name and role>",
      "quote": "<verbatim quote, max 50 words>"
    }}
  ],
  "risk_factors_cited": [
    "<risk 1 mentioned by management>",
    "<risk 2 (optional)>"
  ]
}}"""


def build_estimate_revision_prompt(ticker: str, company: str) -> str:
    """Estimate revision research — pull current consensus and 1w/4w revision deltas."""
    return f"""You are a buy-side equity analyst. Research current sell-side consensus estimates and recent revision activity for {company} ({ticker}).

Return ONLY this JSON object. No markdown fences, no preamble, no prose.

{{
  "ticker": "{ticker}",
  "fwd_eps_est": <number or null>,
  "fwd_rev_est": <number in billions USD or null>,
  "num_analysts": <integer or null>,
  "eps_revision_1w": <percent change over last 1 week as decimal, e.g. 0.012 for +1.2%, or null>,
  "eps_revision_4w": <percent change over last 4 weeks as decimal, or null>,
  "rev_revision_1w": <percent change over last 1 week as decimal, or null>,
  "rev_revision_4w": <percent change over last 4 weeks as decimal, or null>,
  "direction": "<one of: upward | downward | stable | mixed>",
  "narrative": "<1-2 sentences on what drove the recent revisions>",
  "sources": ["url1", "url2"]
}}

Look at: Visible Alpha, Estimize free tier, Yahoo Finance analysis tab, MarketBeat consensus EPS history, recent sell-side notes within the past 30 days.
If a number cannot be verified with reasonable confidence, return null. Do not invent revisions."""


def build_private_company_prompt(name: str, subsector: str | None = None) -> str:
    """Private-company enrichment prompt for `private_intel` Supabase rows."""
    subsector_hint = f" (subsector: {subsector})" if subsector else ""
    return f"""You are a buy-side equity research analyst covering private/pre-IPO companies. Research the private company {name!r}{subsector_hint}.

Return ONLY this JSON object. No markdown fences, no commentary, no preamble.

{{
  "name": "{name}",
  "subsector": "<best subsector classification>",
  "valuation": {{"amount": <number or null>, "unit": "B|M", "as_of": "<YYYY-Q? or YYYY-MM>"}},
  "last_funding_round": {{"series": "<e.g. Series D>", "amount": "<$XXXM>", "date": "<YYYY-MM>", "lead_investor": "<lead investor>"}},
  "arr_or_revenue": {{"amount": "<$X.YB>", "type": "<ARR|Revenue|Bookings>", "as_of": "<YYYY or YYYY-Q?>"}},
  "investors": ["<investor 1>", "<investor 2>"],
  "growth_signals": ["<signal 1>", "<signal 2>"],
  "ipo_signals": "<S-1 filed | rumored H? YYYY | no signal>",
  "competitive_context": "<2-3 sentences on closest public comps and competitive positioning>",
  "hq": "<city, state/country>",
  "sources": ["url1", "url2"]
}}

Prefer Crunchbase, PitchBook coverage, recent press releases, S-1/F-1 filings (if any), credible tech press (TechCrunch, The Information). Do not fabricate funding amounts — return null if not verifiable."""


def build_peer_read_across_prompt(
    target_ticker: str,
    target_company: str,
    target_earnings_date: str,
    peer_ticker: str,
    peer_company: str,
    peer_earnings_date: str,
    peer_print_summary: str,
) -> str:
    """Cross-read prompt: how does PEER's print affect TARGET's setup."""
    return f"""You are a buy-side equity analyst. {peer_company} ({peer_ticker}) reported earnings on {peer_earnings_date}.

Peer print summary:
{peer_print_summary}

How does this print affect the setup for {target_company} ({target_ticker}) earnings on {target_earnings_date}? Be concrete about read-across to revenue, gross margin, opex leverage, guidance bar, multiple compression risk.

Return ONLY this JSON. No prose, no markdown, no preamble.

{{
  "peer_ticker": "{peer_ticker}",
  "target_ticker": "{target_ticker}",
  "direction": "<bullish|bearish|neutral|mixed>",
  "confidence": "<high|medium|low>",
  "read_across": "<2-3 sentences explaining the direct read-across to TARGET>",
  "affected_metrics": ["<metric 1, e.g. 'data center rev growth'>", "<metric 2>"],
  "signal_status": "WATCHING",
  "signal_label": "Peer read-across: <peer ticker>"
}}"""


def build_news_tagging_prompt(ticker: str, company: str, articles: list[dict]) -> str:
    """Buy-side news tagging brief.

    Takes a batch of already-collected articles (headline + teaser) and asks
    the LLM to classify each one with catalyst_tag, direction, priority,
    and a specific financial-variable blurb. Output is a JSON array, one
    object per input article, in the same order.

    Each input article dict must have keys: headline, teaser. Optional:
    source, url, published_at.
    """
    lines = []
    for i, a in enumerate(articles, start=1):
        headline = (a.get("headline") or "").strip().replace("\n", " ")
        teaser = (a.get("teaser") or a.get("body") or "").strip().replace("\n", " ")
        # Keep teasers compact so the prompt stays small at scale.
        if len(teaser) > 400:
            teaser = teaser[:397] + "..."
        lines.append(f"[{i}] {headline} | {teaser}")
    article_block = "\n".join(lines) if lines else "(no articles)"

    return f"""You are a buy-side equity research assistant. You will receive a batch of financial news articles (headline + teaser/body) tagged to {company} ({ticker}).

For each article, output ONLY a single JSON array with one object per input article, in the SAME order as the input. Each object has the following fields:

{{
  "catalyst_tag": one of [Earnings, Analyst Action, SEC Filing, M&A, Guidance, Macro, Exec Change, Legal/Regulatory, Capital Markets, Activist/Short, Other],
  "direction": one of [Bullish, Bearish, Neutral, Mixed],
  "blurb": a single sentence (max 25 words) answering: what specifically happened, and what financial variable does it affect (revenue, EPS, margins, guidance, competitive position, float)?,
  "priority": one of [High, Medium, Low],
  "duplicate": boolean (true if a higher-priority item earlier in the batch already covered this same event)
}}

Priority rules:
- High: earnings result, rating change by bulge bracket, 8-K material event, M&A, insider buy >$500K.
- Medium: guidance commentary, sector read-across, secondary analyst note.
- Low: general market color, opinion piece.

Hard rules:
- Never invent numbers not present in the source text.
- If the teaser is insufficient to determine direction, set direction to "Neutral".
- blurb MUST name a specific financial variable (revenue, EPS, gross/op/FCF margin, guidance, share count, leverage, market share, etc.). Reject generic sentiment language like "stock could rally" or "investors concerned".
- If two articles cover the same event, flag duplicate=true on the lower-priority item (the higher-priority one stays duplicate=false).
- Return ONLY the JSON array. No prose. No markdown. No preamble.

Article batch (one per line, format: [N] HEADLINE | TEASER):
{article_block}"""


def build_news_prompt(ticker: str, company: str, sector: str) -> str:
    """Daily news scan — only material updates in last 48 hours."""
    return f"""Search for material news about {company} ({ticker}, {sector}) in the last 48 hours only.

Return ONLY this JSON. No prose. No preamble. If no material news, return {{"has_material_update": false, "items": [], "catalyst_type": "none"}}.

{{
  "has_material_update": true,
  "items": [
    {{"headline": "...", "impact": "positive/negative/neutral", "url": "..."}}
  ],
  "catalyst_type": "earnings/guidance/analyst/product/regulatory/macro/none"
}}"""


def build_earnings_date_prompt(ticker: str, company: str) -> str:
    """Lookup next earnings date for a ticker."""
    return f"""What is the next earnings report date for {company} ({ticker})?
Return ONLY this JSON. No prose.

{{
  "ticker": "{ticker}",
  "company": "{company}",
  "next_earnings_date": "YYYY-MM-DD",
  "time": "Before Open / After Close / Unknown",
  "quarter": "Q? FY20XX",
  "confirmed": true,
  "source": "url"
}}"""


def build_weekly_value_prompt() -> str:
    """Weekly briefing — top 5 value stocks near 52-week lows."""
    return """Search the full US stock market for the top 5 value stocks currently trading near their 52-week lows that have strong fundamentals.

For each stock return this JSON array. No prose. No preamble.

[
  {
    "ticker": "...",
    "name": "...",
    "price": 0.0,
    "high_52w": 0.0,
    "low_52w": 0.0,
    "pct_off_high": "-X%",
    "market_cap": "...",
    "pe_ratio": 0.0,
    "ev_ebitda": 0.0,
    "revenue_growth": "X%",
    "fcf_yield": "X%",
    "fcf_ttm": "...",
    "debt_equity": 0.0,
    "analyst_target": 0.0,
    "analyst_consensus": "Buy/Hold/Sell",
    "why_undervalued": "2 sentences max",
    "bull_case": "2 sentences max"
  }
]"""


def build_weekly_momentum_prompt() -> str:
    """Weekly briefing — top 5 momentum stocks."""
    return """Search the full US stock market for the top 5 momentum stocks showing the strongest recent performance with fundamental catalysts.

For each stock return this JSON array. No prose. No preamble.

[
  {
    "ticker": "...",
    "name": "...",
    "price": 0.0,
    "one_week_perf": "X%",
    "one_month_perf": "X%",
    "three_month_perf": "X%",
    "revenue_growth": "X%",
    "catalyst": "1-2 sentences on what is driving the move",
    "risk_reward": "1-2 sentences on risk/reward from here"
  }
]"""


def build_weekly_trends_prompt(watchlist_tickers: list[str]) -> str:
    """Weekly briefing — market trends, risks, watchlist movers."""
    tickers_str = ", ".join(watchlist_tickers[:50])
    return f"""Summarize this week's US equity market. Include:
1. Weekly index returns (S&P 500, Nasdaq, Russell 2000) — closing values and % change
2. 3-5 key market trends/themes
3. 3-5 risks to watch next week
4. Material updates for these watchlist tickers (only if earnings, analyst changes, or >5% weekly move): {tickers_str}
5. A 2-3 sentence market narrative summary

Return ONLY this JSON. No prose.

{{
  "index_returns": {{
    "sp500": {{"close": 0.0, "weekly_return": "X%", "ytd_return": "X%"}},
    "nasdaq": {{"close": 0.0, "weekly_return": "X%", "ytd_return": "X%"}},
    "russell2000": {{"close": 0.0, "weekly_return": "X%", "ytd_return": "X%"}}
  }},
  "trends": ["trend 1 (max 40 words)", "trend 2", "trend 3"],
  "risks": ["risk 1 (max 40 words)", "risk 2", "risk 3"],
  "watchlist_movers": [
    {{"ticker": "...", "weekly_move": "+X%", "thirty_day_move": "+X%", "catalyst": "...", "detail": "..."}}
  ],
  "narrative": "2-3 sentence summary"
}}"""


def _temporal_header(week_ending) -> str:
    from datetime import timedelta
    mon = (week_ending - timedelta(days=4)).isoformat()
    return (
        f"IMPORTANT: You are researching historical market data for the week of "
        f"{mon} through {week_ending.isoformat()}. "
        f"All data, prices, and analysis MUST reflect conditions as of that specific week. "
        f"Do NOT use current data. Search for news and market data from {mon}–{week_ending.isoformat()} only.\n\n"
    )


def build_weekly_value_prompt_for_date(week_ending) -> str:
    from datetime import timedelta
    header = _temporal_header(week_ending)
    return (
        header
        + f"As of the week ending {week_ending.isoformat()}, search for the top 5 value stocks "
        f"that were trading near their 52-week lows with strong fundamentals at that time.\n\n"
        + build_weekly_value_prompt().split("\n", 1)[1]
    )


def build_weekly_momentum_prompt_for_date(week_ending) -> str:
    header = _temporal_header(week_ending)
    return (
        header
        + f"As of the week ending {week_ending.isoformat()}, identify the top 5 momentum stocks "
        f"showing the strongest recent performance with fundamental catalysts during that week.\n\n"
        + build_weekly_momentum_prompt().split("\n", 1)[1]
    )


def build_weekly_trends_prompt_for_date(week_ending, watchlist_tickers: list) -> str:
    from datetime import timedelta
    header = _temporal_header(week_ending)
    tickers_str = ", ".join(watchlist_tickers[:50])
    mon = (week_ending - timedelta(days=4)).isoformat()
    return f"""{header}Summarize the US equity market for the week of {mon} through {week_ending.isoformat()}. Include:
1. Weekly index returns (S&P 500, Nasdaq, Russell 2000) — closing values and % change for that week
2. 3-5 key market trends/themes from that week
3. 3-5 risks that were being watched heading into the following week
4. Material updates for these watchlist tickers during that week (only earnings, analyst changes, or >5% weekly move): {tickers_str}
5. A 2-3 sentence market narrative summary for that week

Return ONLY this JSON. No prose.

{{
  "index_returns": {{
    "sp500": {{"close": 0.0, "weekly_return": "X%", "ytd_return": "X%"}},
    "nasdaq": {{"close": 0.0, "weekly_return": "X%", "ytd_return": "X%"}},
    "russell2000": {{"close": 0.0, "weekly_return": "X%", "ytd_return": "X%"}}
  }},
  "trends": ["trend 1 (max 40 words)", "trend 2", "trend 3"],
  "risks": ["risk 1 (max 40 words)", "risk 2", "risk 3"],
  "watchlist_movers": [
    {{"ticker": "...", "weekly_move": "+X%", "thirty_day_move": "+X%", "catalyst": "...", "detail": "..."}}
  ],
  "narrative": "2-3 sentence summary of that specific week"
}}"""
