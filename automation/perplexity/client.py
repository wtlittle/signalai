"""
Single Perplexity research-task wrapper for ALL LLM work.

By default, LLM tasks are NOT sent to the Perplexity REST API anymore.
Instead, each call is queued to automation/queue/pending_tasks.json so that
Perplexity Computer can pick them up and process them manually or via its
own agent loop. This keeps all LLM work under a single, auditable handoff
point and removes dependency on api.perplexity.ai.

To preserve the old direct-API behavior as a fallback, set the environment
variable USE_API_FALLBACK=true. Otherwise every call is queued.
"""
import os
import json
import time
import datetime as _dt
from pathlib import Path
import requests
from automation.shared.cache import (
    research_cache_exists,
    load_research_cache,
    save_research_cache,
)

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar-pro")
BASE_URL = "https://api.perplexity.ai/chat/completions"

# --- Queue location (Computer handoff) ---
_QUEUE_DIR = Path(__file__).resolve().parent.parent / "queue"
QUEUE_FILE = _QUEUE_DIR / "pending_tasks.json"

# --- Rate limiter state ---
_last_call_time = 0.0
MIN_CALL_INTERVAL = 0.6  # seconds between calls


def _use_api_fallback() -> bool:
    """Return True only if USE_API_FALLBACK is explicitly enabled."""
    return os.environ.get("USE_API_FALLBACK", "false").lower() == "true"


def _queue_task(ticker: str, task: str, prompt: str, system: str, max_tokens: int) -> dict:
    """Append a task to automation/queue/pending_tasks.json for Computer to process.

    Read-modify-write pattern: loads the existing queue (empty array if missing
    or corrupt), appends the new entry, and writes it back atomically.
    Returns a status dict indicating the task was queued.
    """
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = []

    entry = {
        "ticker": ticker,
        "task": task,
        "prompt": prompt,
        "system": system,
        "max_tokens": max_tokens,
        "queued_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    existing.append(entry)

    # Write atomically: write to tmp then replace
    tmp = QUEUE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(existing, f, indent=2)
    tmp.replace(QUEUE_FILE)

    print(f"  [QUEUED] {ticker} / {task} \u2192 {QUEUE_FILE.name} (total pending: {len(existing)})")
    return {"queued": True, "ticker": ticker, "task": task}


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

    # --- Route to Computer queue by default (no direct API calls) ---
    if not _use_api_fallback():
        return _queue_task(ticker, task, prompt, system, max_tokens)

    if not PERPLEXITY_API_KEY:
        print(f"  [NO KEY] {ticker} / {task} \u2014 PERPLEXITY_API_KEY not set, skipping")
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
        elif resp.status_code == 401:
            print(f"  [AUTH ERROR] PERPLEXITY_API_KEY is invalid or expired — skipping all Perplexity calls")
            os.environ["PERPLEXITY_API_KEY"] = ""
            globals()["PERPLEXITY_API_KEY"] = ""
            return {"skipped": True, "reason": "invalid_api_key", "ticker": ticker, "task": task}
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
