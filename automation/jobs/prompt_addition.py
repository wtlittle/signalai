# ============================================================
# ADD THESE FUNCTIONS TO automation/perplexity/prompts.py
# Place after build_post_earnings_prompt()
# Then delete this file.
# ============================================================


def build_transcript_distill_prompt(
    ticker: str,
    company: str,
    earnings_date: str,
    transcript_text: str,
) -> tuple[str, str]:
    """
    Distill a scraped earnings call transcript into structured JSON.

    Args:
        ticker:           Stock ticker symbol.
        company:          Full company name.
        earnings_date:    ISO date of the earnings report (YYYY-MM-DD).
        transcript_text:  Raw transcript body (pre-truncated to ~18K chars).

    Returns:
        (system_prompt, user_prompt) tuple for call_perplexity().
    """
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
    """
    Perplexity-native fallback: research and synthesize transcript content
    without providing source text directly (used when Fool scraping fails).

    Returns:
        (system_prompt, user_prompt) tuple for call_perplexity().
    """
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
