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
5. Thesis Impact (reinforces/challenges/neutral + reasoning). Cross-reference
   the INDUSTRY PACK here: frame in-quarter growth against the category CAGR to
   imply share gain/loss, and name the most relevant competitor benchmark. Cite
   the TAM (with currency + year) and CAGR (with window) and their sources. If
   the pack is STALE or MISSING, write 'industry data refresh pending' rather
   than inventing any TAM, CAGR, or competitor figure.
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
  "fy_rev_change_vs_consensus_pct": <number or null>,
  "fy_rev_change_vs_prior_pct": <number or null>,
  "fy_eps_guide_midpoint_new": "<or null>",
  "fy_eps_guide_midpoint_prior": "<or null>",
  "fy_eps_consensus_prior": "<or null>",
  "fy_eps_guide_change_vs_consensus_pct": <number or null>,
  "fy_eps_guide_change_vs_prior_pct": <number or null>,
  "fy_op_metric_kind": "<operating_profit | operating_income | null>",
  "fy_op_profit_guide_midpoint_new": "<or null>",
  "fy_op_profit_guide_midpoint_prior": "<or null>",
  "fy_op_profit_consensus_prior": "<or null>",
  "fy_fcf_guide_midpoint_new": "<or null>",
  "fy_fcf_guide_midpoint_prior": "<or null>",
  "fy_fcf_consensus_prior": "<or null>"
}
```

The EPS / operating-profit / FCF midpoints feed the profitability guidance
pill on the POST card (EPS preferred; operating profit/income and FCF are
fallbacks). The card frames each guidance line two ways: RAISE / FLAT / CUT vs
the company's OWN PRIOR guide midpoint (*_guide_midpoint_prior), and ABOVE /
IN-LINE / BELOW vs the prior Street consensus midpoint (*_consensus_prior).
Provide BOTH baselines when known -- the prior-guide midpoint drives raise/cut
and the prior-consensus midpoint drives the "vs Street" magnitude. For
operating profit, set fy_op_metric_kind to match how the company reports it
("operating_income" or "operating_profit").

IMPORTANT: Both blocks are REQUIRED, not optional. You MUST populate the
results_vs_consensus block with actual numbers from the THIS QUARTER ACTUALS and
FORWARD CONSENSUS data provided. If the company reported, every field in
results_vs_consensus must have a value. The guide_vs_consensus block must use the
exact field names above -- in particular fy_rev_change_vs_consensus_pct (the FY
revenue guide move vs prior Street consensus, signed: + = raise, - = cut) and
fy_rev_change_vs_prior_pct -- because those drive the "FY Guide Δ" line on the
POST card. Use null only when the data is genuinely not provided in the context."""


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


_INDUSTRY_PACK_INSTRUCTION = (
    "Cross-reference the Industry Pack below in your post-earnings analysis. "
    "Cite specific TAM, category CAGR, and competitor benchmarks where relevant "
    "(for example, frame in-quarter growth against the category CAGR to imply "
    "share gain or loss). Every industry claim must carry its source. CAGR must "
    "name its time window; TAM must name its currency and year of estimate; "
    "competitors must be named (never \"various competitors\"). If the pack is "
    "marked STALE (>14 days) or MISSING, do NOT invent industry numbers -- say "
    "'industry data refresh pending' instead."
)


def _render_industry_pack_legacy(pack: dict, errors: list[dict]) -> str:
    """Render the legacy subsector-level industry pack (industry_packs table).

    Pre-earnings notes still consume the subsector pack shape
    ``{subsector, week_of, pack, sources}`` populated by industry_packs_refresh.
    Kept verbatim so wiring the per-ticker market_intel pack into post-earnings
    does not change pre-earnings output.
    """
    subsector = _safe(pack.get("subsector"))
    week = _safe(pack.get("week_of"))
    data = pack.get("pack")
    data_str = _json.dumps(data, indent=2) if data else "no pack data"
    return f"=== INDUSTRY PACK ===\nSubsector: {subsector} (week of {week})\n{data_str}"


def _render_industry_pack(pack: dict | None, errors: list[dict]) -> str:
    """Render the per-ticker grounded industry pack (market_intel_ticker).

    Handles three states explicitly so the model never invents numbers:
      - MISSING: no Supabase row (also surfaced via the errors list).
      - STALE: a row older than 14 days (``is_stale=True``).
      - FRESH: a current row -- TAM, CAGR, drivers, named competitors, sources.

    A legacy subsector-shaped pack (``subsector``/``pack`` keys, from the
    pre-earnings industry_packs table) is delegated to the legacy renderer so
    pre-earnings notes are unaffected by the post-earnings market_intel wiring.
    """
    if pack is not None and ("subsector" in pack or "week_of" in pack):
        return _render_industry_pack_legacy(pack, errors)

    header = "=== INDUSTRY PACK (grounded market intel) ==="
    instruction = f"INSTRUCTION: {_INDUSTRY_PACK_INSTRUCTION}"

    err = _na("industry_pack", errors)
    if pack is None:
        body = (err or "MISSING: no market_intel row for this ticker.") + \
            "\nDo NOT invent TAM, CAGR, or competitor figures. Write "
        body += "'industry data refresh pending' for any industry claim."
        return f"{header}\n{instruction}\n{body}"

    lines = [header, instruction]

    if pack.get("is_stale"):
        lines.append(
            f"STATUS: STALE -- last refreshed {_safe(pack.get('updated_at'))} "
            f"(>14 days old). Do NOT cite the numbers below as current. Write "
            f"'industry data refresh pending' instead of inventing figures."
        )
    else:
        lines.append(f"STATUS: FRESH -- refreshed {_safe(pack.get('updated_at'))}.")

    tam = pack.get("tam_usd_bn")
    tam_src = pack.get("tam_source_url")
    if tam is not None:
        tam_line = f"TAM: ${_safe(tam)}B USD (per source)"
        if tam_src:
            tam_line += f" [{tam_src}]"
        lines.append(tam_line)
    else:
        lines.append("TAM: -- (not sourced; do not invent)")

    cagr = pack.get("category_cagr_pct")
    if cagr is not None:
        lines.append(f"Category CAGR: {_safe(cagr)}% (state the window when citing)")
    else:
        lines.append("Category CAGR: -- (not sourced; do not invent)")

    drivers = pack.get("drivers") or []
    if drivers:
        lines.append("Demand drivers:")
        lines.extend(f"  - {d}" for d in drivers)
    else:
        lines.append("Demand drivers: -- (none provided)")

    competitors = pack.get("competitors") or []
    if competitors:
        lines.append("Named competitors (quadrant / threat / source):")
        for c in competitors:
            name = _safe(c.get("name"))
            tk = c.get("ticker")
            name_disp = f"{name} ({tk})" if tk else name
            quad = _safe(c.get("quadrant"), fallback="--")
            threat = _safe(c.get("threat"), fallback="--")
            src = c.get("source_url")
            line = f"  - {name_disp}: {quad}, threat {threat}"
            if src:
                line += f" [{src}]"
            lines.append(line)
    else:
        lines.append("Named competitors: -- (none provided)")

    srcs = pack.get("source_urls") or []
    if srcs:
        lines.append("Sources: " + "; ".join(srcs))

    return "\n".join(lines)


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

    # Grounded industry pack ships for EVERY tier in post-earnings notes and is
    # placed before the thesis instructions (_POST_SECTIONS) so the model can
    # cross-reference TAM / CAGR / competitor benchmarks while writing.
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
    """Weekly briefing — top 5 value stocks near 52-week lows.

    Buy-side value-screen note. The dashboard renders a 19-field card per pick,
    so EVERY key below is required for every pick. Do not collapse to a thin
    thesis/catalyst/risk shape -- that breaks the card.
    """
    return """OUTPUT CONTRACT (read first): This is a DATA-EXTRACTION task, NOT an essay. Do NOT write a research report, framework, headings, or any prose outside the JSON. Any text that is not part of the JSON object will be discarded and the response treated as a failure. Your response must BEGIN with the character { and END with the character }.

HALLUCINATION GUARD: If you cannot source a metric from real public data within the last 7 days, return null. Do not hallucinate analyst targets, FCF figures, or price data. Every number must come from a verifiable public source (Yahoo Finance, SEC filing, Bloomberg, FactSet, or equivalent).

You are a buy-side value-equity analyst. Screen the full US stock market and select the 5 best value stocks currently trading near their 52-week lows that still have strong, defensible fundamentals (positive or improving free cash flow, manageable leverage, a credible re-rating path).

Return ONLY a single JSON object with one key "picks" whose value is an array of EXACTLY 5 objects. Every pick object MUST contain ALL 19 keys below, with these EXACT key names. Do NOT rename, omit, or add keys. Do NOT substitute a thin {thesis, catalyst, risk, pb_ratio, dividend_yield} shape -- that schema is WRONG and will be rejected.

{
 "picks": [
  {
    "ticker": "<symbol>",
    "name": "<full company name>",
    "sector": "<GICS sector, e.g. Technology / Financials / Healthcare / Energy / Industrials / Consumer Discretionary / Consumer Staples / Utilities / Materials / Real Estate / Communication Services>",
    "current_price": 0.0,
    "price": 0.0,
    "fifty_two_week_high": 0.0,
    "fifty_two_week_low": 0.0,
    "pct_off_high": "-X.X%",
    "market_cap": "<e.g. 12.4B>",
    "pe_ratio": 0.0,
    "ev_ebitda": 0.0,
    "revenue_growth": "X.X%",
    "fcf_yield": "X.X%",
    "fcf_ttm": "<e.g. 1.2B>",
    "debt_equity": 0.0,
    "analyst_target": 0.0,
    "analyst_consensus": "Strong Buy/Buy/Hold/Sell",
    "why_undervalued": "<3-5 full sentences: the specific valuation gap vs history/peers and WHY the market is mispricing it>",
    "bull_case": "<3-5 full sentences: the concrete catalysts and fundamental drivers that re-rate the stock higher>",
    "key_risks": "<3-5 full sentences: the dominant bear thesis -- the specific risks or structural issues that could drive the stock further down. Must be DISTINCT from why_undervalued.>",
    "why_could_go_lower": "<3-5 full sentences: the dominant bear thesis -- the specific risk or structural issue that could drive the stock further down. Must be DISTINCT from why_undervalued; this is what could make the discount deserved or wider>"
  }
 ]
}

Field rules:
- "current_price" and "price" must be set to the SAME numeric value (both are required for backward compatibility). Same for "fifty_two_week_high"/"fifty_two_week_low" which are also required as the canonical names.
- "current_price", "price", "fifty_two_week_high", "fifty_two_week_low", "pe_ratio", "ev_ebitda", "debt_equity", "analyst_target" are NUMBERS (no quotes, no currency symbols). Use null only if genuinely unavailable.
- "pct_off_high", "revenue_growth", "fcf_yield" are percent STRINGS like "-23.4%" / "12.5%".
- "market_cap" and "fcf_ttm" are magnitude STRINGS like "12.4B".
- "why_undervalued", "bull_case", "key_risks", "why_could_go_lower" must each be 3-5 complete sentences of genuine buy-side reasoning -- never one-liners, never empty. "key_risks" and "why_could_go_lower" may be identical text.
- NEVER fabricate a number; use null when you cannot verify it. Citations in brackets are allowed inside the prose string fields.

CRITICAL OUTPUT INSTRUCTION: Your ENTIRE response must be a single valid JSON object {"picks": [...]} parseable by Python json.loads() without preprocessing. No markdown, no prose, no section headers, no code fences. Do NOT wrap in ```json or ``` markers. The first character must be { and the last character must be }. Emit all 5 picks; never truncate a pick mid-object."""


def build_weekly_momentum_prompt() -> str:
    """Weekly briefing — top 5 momentum stocks.

    The dashboard renders a 12-field card per pick, so EVERY key below is
    required for every pick.
    """
    return """OUTPUT CONTRACT (read first): This is a DATA-EXTRACTION task, NOT an essay. Do NOT write a research report, framework, headings, or any prose outside the JSON. Any text that is not part of the JSON object will be discarded and the response treated as a failure. Your response must BEGIN with the character { and END with the character }.

HALLUCINATION GUARD: If you cannot source a metric from real public data within the last 7 days, return null. Do not hallucinate price performance, analyst targets, or FCF figures. Every number must come from a verifiable public source (Yahoo Finance, SEC filing, Bloomberg, FactSet, or equivalent).

You are a buy-side momentum analyst. Screen the full US stock market and select the 5 strongest momentum stocks -- names with the best recent trailing performance that is backed by a genuine fundamental catalyst (not a low-float squeeze).

Return ONLY a single JSON object with one key "picks" whose value is an array of EXACTLY 5 objects. Every pick object MUST contain ALL 12 keys below, with these EXACT key names. Do NOT rename, omit, or add keys.

{
 "picks": [
  {
    "ticker": "<symbol>",
    "name": "<full company name>",
    "sector": "<GICS sector, e.g. Technology / Financials / Healthcare / Energy / Industrials / Consumer Discretionary / Consumer Staples / Utilities / Materials / Real Estate / Communication Services>",
    "current_price": 0.0,
    "price": 0.0,
    "one_week_perf": "+X.X%",
    "one_month_perf": "+X.X%",
    "three_month_perf": "+X.X%",
    "revenue_growth": "+X.X%",
    "catalyst": "<2-3 full sentences on the specific fundamental catalyst driving the move>",
    "risk_reward": "<2-3 full sentences on the risk/reward setup from the current price>",
    "key_risks": "<2-3 full sentences: the main risks that could reverse the momentum or make the current valuation untenable>"
  }
 ]
}

Field rules:
- "current_price" and "price" must be set to the SAME numeric value (both required for backward compatibility).
- "current_price", "price" are NUMBERS (no quotes, no currency symbol). Use null only if genuinely unavailable.
- "one_week_perf", "one_month_perf", "three_month_perf", "revenue_growth" are percent STRINGS like "+8.3%" / "-2.1%".
- "catalyst", "risk_reward", "key_risks" must each be 2-3 complete sentences -- never one-liners, never empty.
- NEVER fabricate a number; use a best-available figure or null. Citations in brackets are allowed inside the prose string fields.

CRITICAL OUTPUT INSTRUCTION: Your ENTIRE response must be a single valid JSON object {"picks": [...]} parseable by Python json.loads() without preprocessing. No markdown, no prose, no section headers, no code fences. Do NOT wrap in ```json or ``` markers. The first character must be { and the last character must be }. Emit all 5 picks; never truncate a pick mid-object."""


def build_weekly_trends_prompt(watchlist_tickers: list[str], context: dict | None = None) -> str:
    """Weekly briefing — market trends, risks, watchlist movers, upcoming catalysts,
    sector summary, and a full markdown research narrative.

    The dashboard renders the narrative as a multi-section research report, so
    it must be a long markdown document (## headers + inline citations), NOT a
    2-3 sentence blurb. trends/risks/watchlist are lists of OBJECTS, not strings.
    Target output size: 80KB+ JSON (comparable to the 5/22 briefing at 85KB).

    ``context`` is the precomputed concrete-data dict from
    weekly_briefing_context.build_context_blocks(). When supplied, the three
    historically-empty sections (watchlist_updates / upcoming_catalysts /
    sector_summary) are grounded in literal mover/earnings/subsector lists so the
    model writes about real names instead of returning []. When absent (legacy
    callers), the prompt falls back to the old "scan the watchlist yourself"
    instructions.
    """
    # Use all tickers for watchlist coverage (target 30-60 movers)
    tickers_str = ", ".join(watchlist_tickers)
    tickers_count = len(watchlist_tickers)

    # --- Concrete-data blocks (the fix for the 6/07 empty-section regression) ---
    if context:
        movers_block = context.get("movers_block", "")
        earnings_block = context.get("earnings_block", "")
        subsector_block = context.get("subsector_block", "")
        min_watchlist = context.get("min_watchlist", 20)
        min_catalysts = context.get("min_catalysts", 6)
        min_sectors = context.get("min_sectors", 8)
        subsector_order = context.get("subsector_order") or []
        subsector_keys = ", ".join(f'"{s}"' for s in subsector_order)
        context_section = f"""
PRECOMPUTED DATA (authoritative — use these EXACT names; do NOT substitute your own ticker list):

MATERIAL WATCHLIST MOVERS THIS WEEK (computed from our price snapshot; 1W/1M are real):
{movers_block}

UPCOMING WATCHLIST EARNINGS (next 14 days, from our calendar):
{earnings_block}

SUBSECTOR BUCKETS (each constituent's real 1-week move in parentheses):
{subsector_block}
"""
    else:
        movers_block = earnings_block = subsector_block = ""
        min_watchlist, min_catalysts, min_sectors = 20, 6, 8
        subsector_order = []
        subsector_keys = ""
        context_section = ""

    return f"""OUTPUT CONTRACT (read first): This is a DATA-EXTRACTION task, NOT an essay. Every character of your response that is not part of the single JSON object will be discarded and the response treated as a failure. Your response must BEGIN with the character {{ and END with the character }}.

HALLUCINATION GUARD: If you cannot source a metric from real public data within the last 7 days, return null. Do not hallucinate analyst targets, FCF figures, sector breadth statistics, or price data. Every number must come from a verifiable public source.

You are a senior market strategist writing a buy-side weekly market briefing. Produce a deep, comprehensive, well-sourced summary of this week's US equity market covering ALL of the sections described below. This briefing should be rich and detailed -- the output JSON should be substantial (target at minimum 80KB of JSON data).

Watchlist tickers ({tickers_count} total): {tickers_str}
{context_section}

RETURN STRICTLY THIS JSON SHAPE. Return ONLY a single JSON object with EXACTLY these keys, EXACT key names, ALL KEYS REQUIRED, NO TRAILING COMMAS:

{{
  "index_returns": {{
    "sp500":       {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "nasdaq":      {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "dow":         {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "russell2000": {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "vix":         {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}}
  }},
  "market_summary": "<TIGHT 2-paragraph summary (600-900 characters, NOT more) covering macro backdrop, index performance, sector rotation, and the look-ahead. A separate call writes the long report, so keep THIS field short so the structured arrays below are not truncated. STRING, not an object.>",
  "key_trends": [
    {{"rank": 1, "title": "<short trend title>", "detail": "<3-5 sentences on the trend with specific tickers, numbers, and context>", "theme": "<short theme title>", "summary": "<2-4 sentences on the theme>", "tickers": ["TICK1", "TICK2"]}}
  ],
  "trends": [
    {{"theme": "<short theme title>", "summary": "<2-4 sentences on the theme>", "tickers": ["TICK1", "TICK2"]}}
  ],
  "risks": [
    {{"rank": 1, "title": "<short risk title>", "detail": "<2-4 sentences on the specific market impact and what to watch>", "risk": "<short risk title>", "impact": "<2-4 sentences on the specific market impact and what to watch>"}}
  ],
  "watchlist_updates": [
    {{"ticker": "...", "weekly_change_pct": 0.0, "thirty_day_change_pct": 0.0, "headline": "<1-2 sentence summary of what happened>", "summary": "<1-2 sentence detail>", "tags": []}}
  ],
  "watchlist_movers": [
    {{"ticker": "...", "weekly_move": "+X.X%", "thirty_day_move": "+X.X%", "catalyst": "<what happened>", "detail": "<1-2 sentence detail>"}}
  ],
  "upcoming_catalysts": [
    {{"ticker": "<ticker or MACRO>", "date": "YYYY-MM-DD", "event_type": "<earnings|conference|macro|IPO|FDA|other>", "event": "<brief event description>", "importance": "<High|Medium|Low>", "context": "<2-4 sentences on why this matters and what to watch>"}}
  ],
  "sector_summary": {{
    "Software Infrastructure": "<4-6 sentences: week's price action, major company news, year-end positioning read; cite >= 2 tickers>",
    "Application Software": "<4-6 sentences>",
    "Cybersecurity": "<4-6 sentences>",
    "Semiconductors": "<4-6 sentences>",
    "AdTech/Digital Media": "<4-6 sentences>",
    "Fintech": "<4-6 sentences>",
    "Consumer": "<4-6 sentences>",
    "Energy/Industrials": "<4-6 sentences>"
  }},
  "narrative": "<FULL MARKDOWN RESEARCH REPORT — see requirements below>"
}}

DETAILED SECTION REQUIREMENTS:

index_returns RULES:
- index_returns is a FLAT JSON OBJECT of five named index objects (sp500, nasdaq, dow, russell2000, vix). It is NEVER a markdown table, NEVER a string, NEVER an array, NEVER a code block.
- Each index object has EXACTLY five NUMERIC fields: "close" is the index closing LEVEL (number, e.g. 5432.1); "weekly_pct", "one_month_pct", "three_month_pct", "ytd_pct" are signed percent NUMBERS (e.g. 1.8 for +1.8%, -2.4 for -2.4%) — NO quotes, NO "%" sign, NO "+" sign, NO commas inside the number.
- Do NOT render index data as a table. Do NOT put tab characters anywhere in the response.
- Use null for any single value you cannot verify; never fabricate a number.

key_trends / trends RULES:
- Provide 5-7 trend objects. Each MUST have BOTH the old-schema keys (rank, title, detail) AND new-schema keys (theme, summary, tickers). "rank" is an integer 1-7, "title" and "theme" may be identical, "detail" and "summary" may be identical.
- NEVER return plain strings in these arrays.

risks RULES:
- Provide 4-6 risk objects. Each MUST have BOTH old-schema keys (rank, title, detail) AND new-schema keys (risk, impact). "rank" is an integer 1-6, "title" and "risk" may be identical, "detail" and "impact" may be identical.

watchlist_updates RULES (CRITICAL — this is the most important volume section):
- Write ONE entry for each of the TOP {min_watchlist}-40 movers in the "MATERIAL WATCHLIST MOVERS THIS WEEK" list above (largest absolute moves first). Do not invent tickers that are not on that list.
- For each ticker, write a TIGHT 2-3 sentence note in the "headline" field (and copy it into "summary") covering: (a) WHAT moved it this week — the specific catalyst (earnings, guidance, analyst action, product/regulatory news, sector rotation); (b) the TAKEAWAY (add/trim/hold/watch). Research the real catalyst — do not just restate the percent. Keep each note under 60 words.
- weekly_change_pct and thirty_day_change_pct: copy the 1W and 1M numbers given for that ticker in the list above (signed floats, e.g. -29.9, +44.6). These are authoritative; do NOT recompute or override them.
- tags: array of short strings like ["earnings", "analyst_action", "major_mover", "downgrade", "guidance"].
- MINIMUM {min_watchlist} entries (target 30-40). A response with fewer than {min_watchlist} watchlist_updates entries is a FAILURE. Keep notes tight so the JSON does not truncate before the array closes.

watchlist_movers RULES:
- Also populate watchlist_movers as an alias for watchlist_updates, in a slightly different shape: each entry has ticker, weekly_move (string like "+3.2%"), thirty_day_move (string), catalyst (string short phrase), detail (one short sentence).
- Include the same tickers as watchlist_updates (target 30-40 entries). Keep each catalyst/detail short.

upcoming_catalysts RULES:
- START with EVERY event in the "UPCOMING WATCHLIST EARNINGS" list above — write one entry per listed earnings event, using the exact ticker and date given.
- THEN ADD macro/market catalysts for the next 14 days (FOMC decisions, CPI, PCE, jobs/NFP, retail sales, key auctions, major conferences/investor days, notable non-watchlist mega-cap prints) until you reach the minimum. Use "MACRO" as the ticker for non-company events.
- For EACH event, write a 2-3 sentence "context" note covering: (a) the key DEBATE going into the event, (b) what the BUY-SIDE cares about, and (c) one specific KPI / data point to WATCH.
- Each entry MUST have: ticker, date (YYYY-MM-DD), event_type (earnings|conference|macro|IPO|FDA|other), event (brief description), importance (High|Medium|Low), context (the 2-3 sentence note above).
- MINIMUM {min_catalysts} entries. A response with fewer than {min_catalysts} upcoming_catalysts entries is a FAILURE.
- Use a verifiable real date; do not fabricate a date you cannot source.

sector_summary RULES:
- Cover EXACTLY these eight subsectors, using these EXACT key names: {subsector_keys}. Use the "SUBSECTOR BUCKETS" list above as the constituent universe for each — the tickers and their real 1-week moves are given there.
- For EACH subsector write 4-6 sentences covering: (a) the week's PRICE ACTION (lean on the constituent moves listed above — which names led, which lagged, the dispersion); (b) any major COMPANY NEWS (earnings, guidance, M&A, analyst actions on those names); (c) what the move tells us about POSITIONING into year-end.
- CITE AT LEAST 2 tickers per subsector, drawn from that subsector's bucket above.
- sector_summary is a JSON OBJECT with the eight subsector-name keys and string values (NOT an array).
- MINIMUM {min_sectors} subsector entries (all eight). A response with fewer than {min_sectors} sector_summary keys is a FAILURE.

NARRATIVE REQUIREMENTS (most important field for depth):
- Must be a substantial markdown research report of AT LEAST 15000 characters (target 25000-35000).
- Must open with a single "# " title line, then use multiple "## " section headers: ## Market Overview, ## Sector Leadership & Rotation, ## Top Movers & Earnings Recap, ## Macro & Rates Environment, ## Watchlist Deep Dives (cover 15+ individual names), ## Upcoming Catalysts, ## What To Watch Next Week.
- Write in flowing buy-side analyst prose with concrete numbers (index levels, % moves, yields, notable single-stock moves) and inline bracketed citations like [3] tied to your sources.
- The narrative MUST NOT be collapsed into a 2-3 sentence summary -- a short narrative will be rejected.
- The narrative is a SINGLE Markdown string value. Escape every newline as \\n inside the JSON string. MUST NOT contain literal tab characters.

RETURN ONLY VALID JSON. NO MARKDOWN FENCES. NO TABS. NO COMMENTARY OUTSIDE JSON.
CRITICAL OUTPUT INSTRUCTION: Your ENTIRE response must be a single valid JSON object parseable by Python json.loads() without preprocessing. Do NOT wrap the whole response in ```json or ``` markers. NO trailing commas. The first character of your response must be {{ and the last character must be }}."""


def build_weekly_structured_prompt(watchlist_tickers: list[str], context: dict | None = None) -> str:
    """Weekly briefing — STRUCTURED sections only (NO narrative).

    Split from build_weekly_trends_prompt so the long-form prose report can't
    exhaust the token budget and truncate the high-value arrays
    (watchlist_updates / upcoming_catalysts / sector_summary) — the 6/07
    regression. This asks for index_returns, market_summary (TIGHT), key_trends,
    trends, risks, sector_summary (EXACTLY the eight subsector keys from
    context), upcoming_catalysts (>=6, including MACRO events), watchlist_updates
    and watchlist_movers — as a single JSON OBJECT with NO "narrative" field.
    The fixed-size sections are emitted BEFORE the long watchlist arrays.
    """
    tickers_str = ", ".join(watchlist_tickers)
    tickers_count = len(watchlist_tickers)

    if context:
        movers_block = context.get("movers_block", "")
        earnings_block = context.get("earnings_block", "")
        subsector_block = context.get("subsector_block", "")
        min_watchlist = context.get("min_watchlist", 20)
        min_catalysts = context.get("min_catalysts", 6)
        min_sectors = context.get("min_sectors", 8)
        subsector_order = context.get("subsector_order") or []
        subsector_keys = ", ".join(f'"{s}"' for s in subsector_order)
        context_section = f"""
PRECOMPUTED DATA (authoritative — use these EXACT names; do NOT substitute your own ticker list):

MATERIAL WATCHLIST MOVERS THIS WEEK (computed from our price snapshot; 1W/1M are real):
{movers_block}

UPCOMING WATCHLIST EARNINGS (next 14 days, from our calendar):
{earnings_block}

SUBSECTOR BUCKETS (each constituent's real 1-week move in parentheses):
{subsector_block}
"""
    else:
        movers_block = earnings_block = subsector_block = ""
        min_watchlist, min_catalysts, min_sectors = 20, 6, 8
        subsector_order = []
        subsector_keys = ""
        context_section = ""

    return f"""OUTPUT CONTRACT (read first): This is a DATA-EXTRACTION task, NOT an essay. Do NOT write a research report, framework, or any long-form prose outside the JSON. Every character of your response that is not part of the single JSON object will be discarded and the response treated as a failure. Your response must BEGIN with the character {{ and END with the character }}. Do NOT include a "narrative" field — a separate call writes the long-form report.

HALLUCINATION GUARD: If you cannot source a metric from real public data within the last 7 days, return null. Do not hallucinate analyst targets, FCF figures, sector breadth statistics, or price data. Every number must come from a verifiable public source.

You are a senior market strategist writing a buy-side weekly market briefing. Produce a comprehensive, well-sourced STRUCTURED summary of this week's US equity market covering ALL of the sections described below.

Watchlist tickers ({tickers_count} total): {tickers_str}
{context_section}
RETURN STRICTLY THIS JSON SHAPE. Return ONLY a single JSON object with EXACTLY these keys, EXACT key names, ALL KEYS REQUIRED, NO TRAILING COMMAS:

{{
  "index_returns": {{
    "sp500":       {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "nasdaq":      {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "dow":         {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "russell2000": {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}},
    "vix":         {{"close": 0.0, "weekly_pct": 0.0, "one_month_pct": 0.0, "three_month_pct": 0.0, "ytd_pct": 0.0}}
  }},
  "market_summary": "<TIGHT 2-paragraph summary (600-900 characters, NOT more) covering macro backdrop, index performance, sector rotation, and the look-ahead. A separate call writes the long report, so keep THIS field short so the structured arrays below are not truncated. STRING, not an object.>",
  "key_trends": [
    {{"rank": 1, "title": "<short trend title>", "detail": "<3-5 sentences on the trend with specific tickers, numbers, and context>", "theme": "<short theme title>", "summary": "<2-4 sentences on the theme>", "tickers": ["TICK1", "TICK2"]}}
  ],
  "trends": [
    {{"theme": "<short theme title>", "summary": "<2-4 sentences on the theme>", "tickers": ["TICK1", "TICK2"]}}
  ],
  "risks": [
    {{"rank": 1, "title": "<short risk title>", "detail": "<2-4 sentences on the specific market impact and what to watch>", "risk": "<short risk title>", "impact": "<2-4 sentences on the specific market impact and what to watch>"}}
  ],
  "sector_summary": {{
    "Software Infrastructure": "<4-6 sentences: week's price action, major company news, year-end positioning read; cite >= 2 tickers>",
    "Application Software": "<4-6 sentences>",
    "Cybersecurity": "<4-6 sentences>",
    "Semiconductors": "<4-6 sentences>",
    "AdTech/Digital Media": "<4-6 sentences>",
    "Fintech": "<4-6 sentences>",
    "Consumer": "<4-6 sentences>",
    "Energy/Industrials": "<4-6 sentences>"
  }},
  "upcoming_catalysts": [
    {{"ticker": "<ticker or MACRO>", "date": "YYYY-MM-DD", "event_type": "<earnings|conference|macro|IPO|FDA|other>", "event": "<brief event description>", "importance": "<High|Medium|Low>", "context": "<2-4 sentences on why this matters and what to watch>"}}
  ],
  "watchlist_updates": [
    {{"ticker": "...", "weekly_change_pct": 0.0, "thirty_day_change_pct": 0.0, "headline": "<1-2 sentence summary of what happened>", "summary": "<1-2 sentence detail>", "tags": []}}
  ],
  "watchlist_movers": [
    {{"ticker": "...", "weekly_move": "+X.X%", "thirty_day_move": "+X.X%", "catalyst": "<what happened>", "detail": "<1-2 sentence detail>"}}
  ]
}}

SECTION ORDER IS DELIBERATE: emit the keys in EXACTLY the order shown above (index_returns, market_summary, key_trends, trends, risks, sector_summary, upcoming_catalysts, watchlist_updates, watchlist_movers). The fixed-size sections come first so they are always complete; the long watchlist arrays come last. There is NO narrative field.

DETAILED SECTION REQUIREMENTS:

index_returns RULES:
- index_returns is a FLAT JSON OBJECT of five named index objects (sp500, nasdaq, dow, russell2000, vix). It is NEVER a markdown table, NEVER a string, NEVER an array, NEVER a code block.
- Each index object has EXACTLY five NUMERIC fields: "close" is the index closing LEVEL (number); "weekly_pct", "one_month_pct", "three_month_pct", "ytd_pct" are signed percent NUMBERS — NO quotes, NO "%" sign, NO "+" sign, NO commas inside the number.
- Use null for any single value you cannot verify; never fabricate a number.

market_summary RULES:
- A TIGHT 2-paragraph plain-text string, 600-900 characters (NOT more), covering macro backdrop, index performance, sector rotation, and the look-ahead. STRING, not an object. No markdown headers. Keep it short — the long report is a separate call, and an over-long market_summary truncates the arrays that follow.

key_trends / trends RULES:
- Provide 5-7 trend objects. Each MUST have BOTH the old-schema keys (rank, title, detail) AND new-schema keys (theme, summary, tickers). NEVER return plain strings in these arrays.

risks RULES:
- Provide 4-6 risk objects. Each MUST have BOTH old-schema keys (rank, title, detail) AND new-schema keys (risk, impact).

sector_summary RULES:
- Cover EXACTLY these eight subsectors, using these EXACT key names: {subsector_keys}. Use the "SUBSECTOR BUCKETS" list above as the constituent universe for each.
- For EACH subsector write 4-6 sentences covering price action, company news, and positioning. CITE AT LEAST 2 tickers per subsector.
- sector_summary is a JSON OBJECT with the eight subsector-name keys and string values (NOT an array).
- MINIMUM {min_sectors} subsector entries (all eight). Fewer is a FAILURE.

upcoming_catalysts RULES:
- START with EVERY event in the "UPCOMING WATCHLIST EARNINGS" list above — one entry per listed earnings event, using the exact ticker and date.
- THEN ADD macro/market catalysts (FOMC, CPI, PCE, jobs/NFP, OPEX, major conferences) until you reach the minimum. Use "MACRO" as the ticker for non-company events.
- Each entry MUST have: ticker, date (YYYY-MM-DD), event_type, event, importance, context (2-3 sentences).
- MINIMUM {min_catalysts} entries. Fewer is a FAILURE.

watchlist_updates RULES (CRITICAL — this is the most important volume section):
- Write ONE entry for each of the TOP {min_watchlist}-40 movers in the "MATERIAL WATCHLIST MOVERS THIS WEEK" list above (largest absolute moves first). Do not invent tickers that are not on that list.
- For each ticker, write a TIGHT 2-3 sentence note in the "headline" field (and copy it into "summary"): WHAT moved it and the TAKEAWAY. Keep each note under 60 words.
- weekly_change_pct and thirty_day_change_pct: copy the 1W and 1M numbers given for that ticker (signed floats). These are authoritative.
- tags: array of short strings.
- MINIMUM {min_watchlist} entries (target 30-40). Fewer is a FAILURE. Keep notes tight so the JSON does not truncate before the array closes.

watchlist_movers RULES:
- Also populate watchlist_movers as an alias for watchlist_updates: each entry has ticker, weekly_move (string), thirty_day_move (string), catalyst (short phrase), detail (one short sentence). Same tickers (target 30-40).

RETURN ONLY VALID JSON. NO MARKDOWN FENCES. NO TABS. NO COMMENTARY OUTSIDE JSON. NO "narrative" FIELD.
CRITICAL OUTPUT INSTRUCTION: Your ENTIRE response must be a single valid JSON object parseable by Python json.loads() without preprocessing. NO trailing commas. The first character must be {{ and the last must be }}."""


def build_weekly_narrative_prompt(watchlist_tickers: list[str], context: dict | None = None) -> str:
    """Weekly briefing — the long-form markdown NARRATIVE report ONLY.

    Returns a single-key JSON object {{"narrative": "<markdown>"}}. Isolated from
    the structured call so its 15000-35000 char output has its own token budget
    and can't starve the structured arrays.
    """
    tickers_str = ", ".join(watchlist_tickers)
    tickers_count = len(watchlist_tickers)

    if context:
        movers_block = context.get("movers_block", "")
        earnings_block = context.get("earnings_block", "")
        subsector_block = context.get("subsector_block", "")
        context_section = f"""
PRECOMPUTED DATA (authoritative — reference these real names and moves in your prose):

MATERIAL WATCHLIST MOVERS THIS WEEK:
{movers_block}

UPCOMING WATCHLIST EARNINGS (next 14 days):
{earnings_block}

SUBSECTOR BUCKETS:
{subsector_block}
"""
    else:
        context_section = ""

    return f"""OUTPUT CONTRACT (read first): Return ONLY a single JSON object with EXACTLY one key, "narrative", whose value is a long markdown research report (a single escaped JSON string). Your response must BEGIN with the character {{ and END with the character }}. No other keys. No prose outside the JSON.

HALLUCINATION GUARD: Ground every number in real, verifiable public data from the last 7 days. Use specific index levels, % moves, yields, and single-stock moves; never fabricate.

You are a senior market strategist writing the long-form prose section of a buy-side weekly market briefing.

Watchlist tickers ({tickers_count} total): {tickers_str}
{context_section}
RETURN STRICTLY THIS JSON SHAPE:

{{
  "narrative": "<FULL MARKDOWN RESEARCH REPORT — see requirements below>"
}}

NARRATIVE REQUIREMENTS:
- A substantial markdown research report of AT LEAST 15000 characters (target 25000-35000).
- Open with a single "# " title line, then multiple "## " section headers: ## Market Overview, ## Sector Leadership & Rotation, ## Top Movers & Earnings Recap, ## Macro & Rates Environment, ## Watchlist Deep Dives (cover 15+ individual names), ## Upcoming Catalysts, ## What To Watch Next Week.
- Write in flowing buy-side analyst prose with concrete numbers and inline bracketed citations like [3].
- A short narrative (2-3 sentences) will be REJECTED.
- The narrative is a SINGLE Markdown string value. Escape every newline as \\n inside the JSON string. MUST NOT contain literal tab characters.

RETURN ONLY VALID JSON. NO MARKDOWN FENCES OUTSIDE THE STRING. NO TABS.
CRITICAL OUTPUT INSTRUCTION: Your ENTIRE response must be a single valid JSON object {{"narrative": "..."}} parseable by Python json.loads(). The first character must be {{ and the last must be }}."""


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
