"""
Watchlist loading from utils.js — single source of truth.
"""
import re
from automation.shared.paths import UTILS_JS


def load_tickers() -> list[str]:
    """Extract DEFAULT_TICKERS from utils.js."""
    src = UTILS_JS.read_text()
    m = re.search(r"const\s+DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];", src)
    if not m:
        raise RuntimeError("Could not find DEFAULT_TICKERS in utils.js")
    return list(dict.fromkeys(re.findall(r"'([A-Z.^]+)'", m.group(1))))


def load_subsector_map() -> dict[str, str]:
    """Extract SUBSECTOR_MAP from utils.js."""
    src = UTILS_JS.read_text()
    m = re.search(r"const\s+SUBSECTOR_MAP\s*=\s*\{([\s\S]*?)\};", src)
    if not m:
        return {}
    return {t: s for t, s in re.findall(r"'([A-Z.^]+)':\s*'([^']+)'", m.group(1))}


def load_common_names() -> dict[str, str]:
    """Extract COMMON_NAMES from utils.js."""
    src = UTILS_JS.read_text()
    m = re.search(r"const\s+COMMON_NAMES\s*=\s*\{([\s\S]*?)\};", src)
    if not m:
        return {}
    return {t: n for t, n in re.findall(r"'([A-Z.^]+)':\s*'([^']+)'", m.group(1))}
