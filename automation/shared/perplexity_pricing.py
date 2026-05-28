"""
Perplexity API pricing constants and cost estimation.

Prices verified against docs.perplexity.ai/guides/pricing (May 2026).
All token prices are per 1M tokens. Request prices are per 1K requests.
"""

# fmt: off
PRICING = {
    "sonar": {
        "input": 1.0,
        "output": 1.0,
        "request": {"low": 5, "medium": 8, "high": 12},
    },
    "sonar-pro": {
        "input": 3.0,
        "output": 15.0,
        "request": {"low": 6, "medium": 10, "high": 14},
    },
    "sonar-reasoning": {
        "input": 1.0,
        "output": 5.0,
        "request": {"low": 5, "medium": 8, "high": 12},
    },
    "sonar-reasoning-pro": {
        "input": 2.0,
        "output": 8.0,
        "request": {"low": 6, "medium": 10, "high": 14},
    },
    "sonar-deep-research": {
        "input": 2.0,
        "output": 8.0,
        "reasoning": 3.0,
        "citation": 2.0,
        "search": 5.0,  # per 1K queries
    },
}
# fmt: on


def estimate_cost(
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    reasoning_tokens: int | None = None,
    citation_tokens: int | None = None,
    num_search_queries: int | None = None,
    search_context_size: str | None = None,
) -> float | None:
    """Estimate USD cost for a single Perplexity API call.

    Returns None if model is unknown or required token counts are missing.
    """
    spec = PRICING.get(model)
    if spec is None:
        return None

    cost = 0.0

    # Token costs
    if prompt_tokens:
        cost += prompt_tokens * spec["input"] / 1_000_000
    if completion_tokens:
        cost += completion_tokens * spec["output"] / 1_000_000

    # Reasoning tokens (deep-research only)
    if reasoning_tokens and "reasoning" in spec:
        cost += reasoning_tokens * spec["reasoning"] / 1_000_000

    # Citation tokens (deep-research only)
    if citation_tokens and "citation" in spec:
        cost += citation_tokens * spec["citation"] / 1_000_000

    # Search queries (deep-research only)
    if num_search_queries and "search" in spec:
        cost += num_search_queries * spec["search"] / 1_000

    # Per-request fee (sonar, sonar-pro, sonar-reasoning-pro)
    if "request" in spec:
        ctx = (search_context_size or "low").lower()
        if ctx not in spec["request"]:
            ctx = "low"
        cost += spec["request"][ctx] / 1_000  # 1 request

    return round(cost, 6)
