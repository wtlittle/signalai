"""Smoke test: run ONLY the weekly_trends deep-research call and dump the
parsed result shape. Used to confirm the hardened build_weekly_trends_prompt +
client.py JSON-repair produce deterministic, parseable JSON.

Usage:
    PERPLEXITY_API_KEY=... USE_PPLX_API=true python3 -m automation.jobs._smoke_weekly_trends
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Force a fresh API call every run (bypass the ticker+task cache).
os.environ["FORCE_REGENERATE"] = "true"

from automation.perplexity.client import call_perplexity  # noqa: E402
from automation.perplexity.prompts import build_weekly_trends_prompt  # noqa: E402
from automation.jobs.weekly_briefing import _TRENDS_RESPONSE_FORMAT  # noqa: E402

TICKERS = ["NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "TSLA", "AMD"]


def main() -> int:
    prompt = build_weekly_trends_prompt(TICKERS)
    result = call_perplexity(
        "MARKET", "weekly_trends", prompt,
        system=("You are a senior market strategist. RESPOND ONLY WITH VALID JSON. "
                "Your entire response must be a JSON object starting with { and ending with }. "
                "The 'narrative' field must contain a full markdown research report "
                "(4000+ chars, ## headers, citations) as an escaped JSON string. "
                "trends and risks are arrays of objects. index_returns is a flat object of "
                "numeric fields, never a table. No markdown outside the JSON. No tabs. Only JSON."),
        max_tokens=14000,
        force=True,
        extra_meta={"response_format": _TRENDS_RESPONSE_FORMAT},
    )

    serialized = json.dumps(result)
    is_raw = isinstance(result, dict) and "raw" in result
    ir = result.get("index_returns") if isinstance(result, dict) else None
    trends = result.get("trends") if isinstance(result, dict) else None
    risks = result.get("risks") if isinstance(result, dict) else None
    narrative = result.get("narrative", "") if isinstance(result, dict) else ""

    print("=" * 60)
    print(f"PARSE_OK            : {not is_raw}")
    print(f"payload_bytes       : {len(serialized)}")
    print(f"top_level_keys      : {sorted(result.keys()) if isinstance(result, dict) else type(result)}")
    print(f"literal_tabs_present: {chr(9) in serialized}")
    print(f"index_returns_type  : {type(ir).__name__}")
    if isinstance(ir, dict):
        print(f"index_returns_keys  : {sorted(ir.keys())}")
    print(f"trends_count        : {len(trends) if isinstance(trends, list) else 'N/A'}")
    if isinstance(trends, list) and trends:
        print(f"trends[0]_keys      : {sorted(trends[0].keys()) if isinstance(trends[0], dict) else 'STRING!'}")
    print(f"risks_count         : {len(risks) if isinstance(risks, list) else 'N/A'}")
    if isinstance(risks, list) and risks:
        print(f"risks[0]_keys       : {sorted(risks[0].keys()) if isinstance(risks[0], dict) else 'STRING!'}")
    print(f"narrative_len       : {len(narrative)}")
    print(f"narrative_has_##    : {'##' in narrative}")
    print("=" * 60)

    return 0 if not is_raw else 1


if __name__ == "__main__":
    sys.exit(main())
