"""
fix_intel_labels.py — One-shot backfill to repair Signal Scorecard labels in
earnings_intel.json that were saved as truncated sentence-fragments (e.g.
"Whether Cloud Security TAM Expansion Can", "FY2027 Guidance And Management
Commentary On", "Revenue Beat/Miss Vs Consensus (Model Is").

Heuristic in priority order:
  1. If label already looks clean (<=40 chars, balanced parens, no stop-word
     tail), keep as-is.
  2. Salvage the existing label: strip ":<tail>", strip ";<tail>", remove
     unbalanced parens, trim stop-word tail, truncate to <=38 chars.
  3. Extract topic from text before first ":" in `note` field.
  4. Derive from `signal_id` (snake_case → Title Case, with acronym + brand
     preservation).

Run from /home/user/workspace/watchlist-app:
    python3 -m automation.jobs.fix_intel_labels [--dry-run]

This is a one-off — once PR #11's prompt constraint is live, future notes
will produce clean labels at generation time. The renderer also has a defensive
fallback (see earnings-intel.js cleanScorecardLabel) so the dashboard stays
clean even if a future regression slips through.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEL_PATH = REPO_ROOT / "earnings_intel.json"

# Words we never allow at the end of a label.
STOP = {
    "and", "or", "on", "of", "to", "for", "with", "a", "an", "in", "at", "by",
    "but", "mai", "via", "when", "whether", "does", "do", "into", "amid",
    "amidst", "through", "that", "this", "these", "those", "be", "was", "were",
    "are", "will", "would", "could", "should", "may", "might", "did", "the",
    "can", "is", "from", "if", "as", "than", "then", "vs", "versus", "--", "-",
}

# Acronyms forced to ALL CAPS. Curated to exclude ambiguous common-word tickers
# (FAST, NET, NOW, ON, MS, MU, DE, AI, etc.).
ACRONYMS = set(
    "EPS NII TAM ARR RPU MRR EBITDA FCF IPO CAGR YoY QoQ IB GTM CXM BMO AMC "
    "OEM ASIC GPU CPU HBM DRAM HBM3 HBM4 LLM AWS GCP GUI KPI GAAP ASP USD EUR "
    "GBP JPY CFO CEO COO CTO CXO PT FY2026 FY2027 FY2028 FY25 FY26 FY27 FY28 "
    "Q1 Q2 Q3 Q4 H1 H2 2NM 3NM 5NM 7NM 18A NVDA INTC TSMC".split()
)

# Brand / proper-noun casing fixes (case-insensitive key, exact replacement).
BRAND_FIX = {
    "turbotax": "TurboTax", "quickbooks": "QuickBooks", "mailchimp": "Mailchimp",
    "freestyle": "FreeStyle", "rotce": "RoTCE", "genai": "GenAI",
    "crowdstrike": "CrowdStrike", "snowflake": "Snowflake",
    "salesforce": "Salesforce", "servicenow": "ServiceNow", "workday": "Workday",
    "datadog": "Datadog", "mongodb": "MongoDB", "gitlab": "GitLab",
    "docusign": "DocuSign", "samsara": "Samsara", "synopsys": "Synopsys",
    "guidewire": "Guidewire", "cadence": "Cadence", "braze": "Braze",
    "medtronic": "Medtronic", "c3.ai": "C3.ai", "us": "US", "eu": "EU",
    "uk": "UK", "ansys": "Ansys", "squarespace": "Squarespace", "pure": "Pure",
    "ai/datacenter": "AI/Datacenter", "ai": "AI", "ml": "ML",
}


def fix_token_case(word: str, is_first: bool = False) -> str:
    """Fix casing of a single token, preserving punctuation, $, %, decimals."""
    m = re.match(r"^([^A-Za-z0-9&]*)(.*?)([^A-Za-z0-9&%]*)$", word)
    if not m or not m.group(2):
        return word
    pre, core, post = m.groups()
    # If token contains digits or money/percent markers, preserve as-is.
    if re.search(r"\d", core) or "$" in word or "%" in word:
        return word
    upper = core.upper()
    lower = core.lower()
    if upper in ACRONYMS:
        return pre + upper + post
    if lower in BRAND_FIX:
        return pre + BRAND_FIX[lower] + post
    # Preserve existing mixed-case (proper nouns already correct).
    if any(c.isupper() for c in core[1:]):
        return word
    if lower in STOP and not is_first:
        return pre + lower + post
    return pre + core[0].upper() + core[1:].lower() + post


def title_case_preserve(s: str) -> str:
    tokens = s.split()
    return " ".join(fix_token_case(t, is_first=(i == 0)) for i, t in enumerate(tokens))


def strip_trailing_stop_and_punct(s: str) -> str:
    words = s.split()
    while words:
        w = words[-1]
        w_clean = re.sub(r"[^A-Za-z0-9&%$]", "", w).lower()
        if not w_clean or w_clean in STOP:
            words.pop()
            continue
        break
    return " ".join(words)


def remove_unbalanced_parens(s: str) -> str:
    if s.count("(") > s.count(")"):
        depth = 0
        last_open = -1
        for i, c in enumerate(s):
            if c == "(":
                depth += 1
                if depth == 1:
                    last_open = i
            elif c == ")":
                depth -= 1
        if depth > 0 and last_open >= 0:
            s = s[:last_open].rstrip()
    if s.count(")") > s.count("("):
        s = re.sub(r"\)+(?=[^(]*$)", "", s).rstrip()
    return s


def trim_to_max(s: str, max_chars: int = 38) -> str:
    if len(s) <= max_chars:
        return s
    words = s.split()
    out: list[str] = []
    n = 0
    for w in words:
        if n + len(w) + (1 if out else 0) > max_chars:
            break
        out.append(w)
        n += len(w) + (1 if len(out) > 1 else 0)
    return strip_trailing_stop_and_punct(" ".join(out))


def is_clean(label: str) -> bool:
    if not label or len(label) < 3 or len(label) > 40:
        return False
    if label.count("(") != label.count(")"):
        return False
    words = label.split()
    if not words:
        return False
    last_w = re.sub(r"[^A-Za-z0-9&%$]", "", words[-1]).lower()
    if not last_w or last_w in STOP:
        return False
    return True


def normalize_label(label: str) -> str | None:
    if not label:
        return None
    s = label.strip()
    if ":" in s:
        head = s.split(":", 1)[0].strip()
        if 3 < len(head) <= 50:
            s = head
    if ";" in s:
        head = s.split(";", 1)[0].strip()
        if 3 < len(head) <= 50 and is_clean(head):
            s = head
    s = remove_unbalanced_parens(s)
    s = strip_trailing_stop_and_punct(s)
    if len(s) > 38:
        s = trim_to_max(s, 38)
    s = title_case_preserve(s) if s else s
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s if is_clean(s) else None


def from_note_head(note: str) -> str | None:
    if not note or ":" not in note:
        return None
    head = note.split(":", 1)[0].strip()
    if 3 < len(head) <= 60:
        return normalize_label(head)
    return None


def from_signal_id(sid: str) -> str | None:
    if not sid:
        return None
    m = re.match(r"^peer_read_([a-z\.\-]+)_([a-z\.\-]+)_(\d{8})$", sid, re.I)
    if m:
        return f"Peer Read: {m.group(2).upper()}"
    cleaned = re.sub(r"^watch_(watch_)?", "", sid)
    s = cleaned.replace("_", " ")
    s = strip_trailing_stop_and_punct(s)
    s = trim_to_max(s, 38)
    return title_case_preserve(s) if s else None


def clean_label(item: dict) -> str:
    """Return a clean, dashboard-safe label for a signal_scorecard item."""
    label = (item.get("label") or "").strip()
    if is_clean(label):
        return label
    salvaged = normalize_label(label)
    if salvaged and is_clean(salvaged):
        return salvaged
    h = from_note_head(item.get("note") or "")
    if h and is_clean(h):
        return h
    s = from_signal_id(item.get("signal_id") or "")
    if s and is_clean(s):
        return s
    return salvaged or h or s or (label[:38] if label else (item.get("signal_id", "")[:38]))


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    if not INTEL_PATH.exists():
        print(f"ERROR: {INTEL_PATH} not found", file=sys.stderr)
        return 1
    data = json.loads(INTEL_PATH.read_text())
    tickers = data.get("tickers", {})

    changes: list[tuple[str, str, str, str]] = []
    for ticker, info in tickers.items():
        if not isinstance(info, dict):
            continue
        scorecard = info.get("signal_scorecard") or []
        for item in scorecard:
            orig = (item.get("label") or "").strip()
            new = clean_label(item)
            if new != orig:
                changes.append((ticker, item.get("signal_id", ""), orig, new))
                if not dry_run:
                    item["label"] = new

    print(f"Scanned {sum(len(t.get('signal_scorecard') or []) for t in tickers.values())} scorecard items")
    print(f"Cleaned {len(changes)} labels")
    if changes:
        print("\nSample (first 20):")
        for t, sid, o, n in changes[:20]:
            print(f"  {t:8} | {o[:46]:46} -> {n}")

    if dry_run:
        print("\n[dry-run] No changes written.")
        return 0

    if changes:
        INTEL_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"\nWrote {INTEL_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
