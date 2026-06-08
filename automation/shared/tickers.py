"""
Watchlist loading from utils.js / universe-v1.js — single source of truth.

The dashboard's default coverage universe is UNIVERSE_V1 (universe-v1.js,
~120 names), NOT DEFAULT_TICKERS (utils.js, ~180 names — the historical
superset). Audits and category-validation must check what the user actually
sees, which is UNIVERSE_V1 by default.
"""
import re
from automation.shared.paths import UTILS_JS


def load_tickers() -> list[str]:
    """Extract DEFAULT_TICKERS from utils.js (full historical superset)."""
    src = UTILS_JS.read_text()
    m = re.search(r"const\s+DEFAULT_TICKERS\s*=\s*\[([\s\S]*?)\];", src)
    if not m:
        raise RuntimeError("Could not find DEFAULT_TICKERS in utils.js")
    return list(dict.fromkeys(re.findall(r"'([A-Z.^]+)'", m.group(1))))


def load_universe_v1_tickers() -> list[str]:
    """Extract UNIVERSE_V1 tickers from universe-v1.js — the actual default
    coverage shown on the dashboard."""
    path = UTILS_JS.parent / "universe-v1.js"
    if not path.exists():
        return []
    src = path.read_text()
    m = re.search(r"const\s+UNIVERSE_V1\s*=\s*\{([\s\S]*?)\n\s*\};", src)
    if not m:
        return []
    return list(dict.fromkeys(re.findall(r"'([A-Z.^]+)'", m.group(1))))


def load_active_universe() -> list[str]:
    """Return the union of UNIVERSE_V1 (default coverage) and DEFAULT_TICKERS
    (full historical superset). Audits should cover the union because both
    are reachable from the universe selector."""
    v1 = load_universe_v1_tickers()
    full = load_tickers()
    return list(dict.fromkeys(v1 + full))


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
