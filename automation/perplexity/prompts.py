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
    "perf_1w": "X%",
    "perf_1m": "X%",
    "perf_3m": "X%",
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
    {{"ticker": "...", "move": "+X%", "catalyst": "...", "detail": "..."}}
  ],
  "narrative": "2-3 sentence summary"
}}"""
