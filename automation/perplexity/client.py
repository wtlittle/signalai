"""
Single Perplexity API wrapper for ALL research tasks.
Every call goes through here — caching, rate limiting, and error handling in one place.
"""
import os
import json
import time
import requests
from automation.shared.cache import (
    research_cache_exists,
    load_research_cache,
    save_research_cache,
)

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar-pro")
BASE_URL = "https://api.perplexity.ai/chat/completions"

# --- Rate limiter state ---
_last_call_time = 0.0
MIN_CALL_INTERVAL = 0.6  # seconds between calls


def call_perplexity(
    ticker: str,
    task: str,
    prompt: str,
    system: str = "Return only structured JSON. No prose, no markdown fences.",
    force: bool = False,
    max_tokens: int = 1500,
    temperature: float = 0.1,
) -> dict:
    """Single entry point for ALL Perplexity calls.

    - Checks cache first (by ticker + date + task).
    - Respects rate limits with a minimum interval between calls.
    - Parses JSON responses; wraps non-JSON in {"raw": ...}.
    - Saves result to cache on success.

    Set force=True or FORCE_REGENERATE=true env var to bypass cache.
    """
    global _last_call_time

    force = force or os.environ.get("FORCE_REGENERATE", "false").lower() == "true"

    # --- Cache check ---
    if not force and research_cache_exists(ticker, task):
        print(f"  [CACHE HIT] {ticker} / {task} — skipping Perplexity call")
        return load_research_cache(ticker, task)

    if not PERPLEXITY_API_KEY:
        print(f"  [NO KEY] {ticker} / {task} — PERPLEXITY_API_KEY not set, skipping")
        return {"skipped": True, "reason": "no_api_key", "ticker": ticker, "task": task}

    # --- Rate limit ---
    elapsed = time.time() - _last_call_time
    if elapsed < MIN_CALL_INTERVAL:
        time.sleep(MIN_CALL_INTERVAL - elapsed)

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "return_citations": False,
    }

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry_run:
        print(f"  [DRY RUN] Would call Perplexity for {ticker} / {task}")
        print(f"            Model: {MODEL}, max_tokens: {max_tokens}")
        return {"dry_run": True, "ticker": ticker, "task": task}

    print(f"  [API CALL] {ticker} / {task} — calling Perplexity ({MODEL})...")
    _last_call_time = time.time()

    try:
        resp = requests.post(BASE_URL, headers=headers, json=body, timeout=90)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 429:
            print(f"  [RATE LIMIT] Sleeping 30s before retry...")
            time.sleep(30)
            resp = requests.post(BASE_URL, headers=headers, json=body, timeout=90)
            resp.raise_for_status()
        else:
            raise

    raw_content = resp.json()["choices"][0]["message"]["content"]

    # Try to parse JSON; strip markdown fences if present
    text = raw_content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"raw": raw_content}

    save_research_cache(ticker, task, parsed)
    return parsed
