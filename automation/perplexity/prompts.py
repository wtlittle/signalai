"""
ALL prompt templates for Perplexity API calls.
Centralized here for auditability and easy tuning.
Each function returns a (system_prompt, user_prompt) tuple or just a user_prompt string.
"""


def build_pre_earnings_prompt(
    ticker: str,
    company: str,
    earnings_date: str,
    days_until: int,
    consensus: dict,
) -> str:
    """Pre-earnings research brief — structured JSON output."""
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
    """Post-earnings research brief — reuses pre-earnings context to avoid duplicate calls."""
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
