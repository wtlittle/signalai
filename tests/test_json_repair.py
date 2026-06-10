"""Tests for the JSON-repair helpers in automation/perplexity/client.py.

The weekly_trends deep-research call intermittently returns JSON with literal
tab characters inside string values (a markdown table rendered inside the
``index_returns`` field), plus trailing commas, code-fence wrapping, and prose
preambles around the object. These helpers + the repair pipeline recover a
parseable object from those payloads.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.perplexity.client import (
    _escape_control_chars_in_strings,
    _largest_json_object,
    _close_truncated_json,
)


def _repair_and_parse(text: str):
    """Replicate the client.py repair ladder for testing the composition."""
    import re

    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    text = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)

    def try_parse(t):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return None

    parsed = try_parse(text)
    if parsed is None:
        parsed = try_parse(re.sub(r",\s*([}\]])", r"\1", text))
    if parsed is None:
        repaired = _escape_control_chars_in_strings(text)
        parsed = try_parse(re.sub(r",\s*([}\]])", r"\1", repaired))
    if parsed is None:
        cand = _largest_json_object(text)
        if cand is not None:
            cand = _escape_control_chars_in_strings(cand)
            parsed = try_parse(re.sub(r",\s*([}\]])", r"\1", cand))
    if parsed is None:
        salv = _close_truncated_json(_escape_control_chars_in_strings(text))
        if salv is not None:
            parsed = try_parse(re.sub(r",\s*([}\]])", r"\1", salv))
    return parsed


# --- _escape_control_chars_in_strings ---------------------------------------

def test_tab_inside_string_is_escaped():
    payload = '{"index_returns": "SPX\t5432\t+1.8%\tNDX\t17000"}'
    fixed = _escape_control_chars_in_strings(payload)
    assert "\t" not in fixed
    obj = json.loads(fixed)
    assert obj["index_returns"].startswith("SPX")


def test_many_tabs_inside_string_all_escaped():
    blob = "col1\tcol2\tcol3\t" * 500  # thousands of literal tabs
    payload = '{"narrative": "' + blob + '"}'
    fixed = _escape_control_chars_in_strings(payload)
    assert "\t" not in fixed
    assert json.loads(fixed)["narrative"].count("\\t") == 0  # \t -> escaped form parses back to real tab
    assert "\t" in json.loads(fixed)["narrative"]


def test_structural_whitespace_untouched():
    payload = '{\n\t"a": 1,\n\t"b": 2\n}'  # tabs/newlines are STRUCTURE, not in strings
    fixed = _escape_control_chars_in_strings(payload)
    assert json.loads(fixed) == {"a": 1, "b": 2}


def test_escaped_quote_inside_string_not_terminated_early():
    payload = '{"a": "she said \\"hi\\"\there"}'
    fixed = _escape_control_chars_in_strings(payload)
    assert json.loads(fixed)["a"] == 'she said "hi"\there'


# --- _largest_json_object ----------------------------------------------------

def test_extract_object_from_prose_preamble():
    text = 'Here is the briefing you asked for:\n{"a": 1, "b": [1,2,3]}\nHope that helps!'
    cand = _largest_json_object(text)
    assert json.loads(cand) == {"a": 1, "b": [1, 2, 3]}


def test_extract_ignores_braces_in_strings():
    text = '{"note": "use {curly} braces", "n": 2}'
    cand = _largest_json_object(text)
    assert json.loads(cand) == {"note": "use {curly} braces", "n": 2}


def test_no_object_returns_none():
    assert _largest_json_object("no json here") is None


# --- full repair ladder ------------------------------------------------------

def test_repair_trailing_comma():
    assert _repair_and_parse('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_repair_code_fence_wrapping():
    assert _repair_and_parse('```json\n{"a": 1}\n```') == {"a": 1}


def test_repair_tabs_and_trailing_comma_together():
    text = '{"index_returns": "SPX\t5432\t+1.8%",}'
    out = _repair_and_parse(text)
    assert out is not None
    assert out["index_returns"] == "SPX\t5432\t+1.8%"


def test_repair_prose_wrapped_unbalanced_tail():
    # leading prose + valid object + trailing commentary with a stray brace
    text = 'Sure!\n{"trends": [{"theme": "AI", "summary": "up"}]}\nThanks } bye'
    out = _repair_and_parse(text)
    assert out["trends"][0]["theme"] == "AI"


def test_repair_clean_json_passes_through():
    assert _repair_and_parse('{"a": [1, 2], "b": {"c": 3}}') == {"a": [1, 2], "b": {"c": 3}}


# --- _close_truncated_json (response cut off at token ceiling) ---------------

def test_close_truncated_midstring_narrative():
    # The real failure mode: object cut off mid-narrative-string.
    text = ('{"index_returns": {"sp500": {"close": 5432.1, "weekly_pct": 1.8}}, '
            '"trends": [{"theme": "AI", "summary": "up", "tickers": ["NVDA"]}], '
            '"narrative": "# Weekly Briefing\\n\\n## Recap\\nMarkets rose then the text got cut off mid-sen')
    out = _repair_and_parse(text)
    assert out is not None
    assert out["index_returns"]["sp500"]["close"] == 5432.1
    assert out["trends"][0]["theme"] == "AI"
    assert out["narrative"].startswith("# Weekly Briefing")


def test_close_truncated_drops_dangling_key():
    # Cut off right after a key with no value — drop the dangling pair.
    text = '{"a": 1, "b": [1, 2, 3], "c'
    out = _repair_and_parse(text)
    assert out == {"a": 1, "b": [1, 2, 3]}


def test_close_truncated_nested_arrays():
    text = '{"risks": [{"risk": "rates", "impact": "higher for longer'
    out = _repair_and_parse(text)
    assert out is not None
    assert out["risks"][0]["risk"] == "rates"


def test_close_truncated_no_open_brace_returns_none():
    assert _close_truncated_json("plain text no json") is None
