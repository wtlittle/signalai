"""
Perplexity earnings source (priority 4, gap-filler only).

Perplexity is the LAST resort and is restricted to fields no structured source
can supply: revenue consensus (and the revenue surprise derived from it) and FY
guidance. It is NEVER used for in-quarter actuals -- those come from
Finnhub/yfinance, and asking an LLM to "find the actual" is exactly the
fabrication risk this rebuild eliminates.

REUSE: the consensus prompt + money parsing are lifted from
``scripts/backfill_rev_consensus_from_perplexity.py`` so producer and the legacy
backfill agree on wording and parsing. All traffic goes through
``automation.perplexity.client.call_perplexity`` (never raw requests), honoring
the cache + rate-limit + queue routing that client owns.

DATA INTEGRITY: a consensus is only emitted when Perplexity returns a real
number; rev_surprise_pct is computed (actual/consensus-1) only when BOTH a real
actual (already in the merged record) and a real consensus are present. No
number, no field.
"""
from __future__ import annotations

import re
from typing import Optional

from automation.perplexity.client import call_perplexity
from automation.sources.base import EarningsSource, SourceResponse


def _consensus_prompt(ticker: str, company: str, date: str, actual_str: str) -> str:
    """Revenue-consensus prompt (kept in sync with the legacy backfill script)."""
    return (
        f"{company} ({ticker}) reported quarterly earnings on or around "
        f"{date} with revenue of {actual_str}. Find what Wall Street analysts "
        f"were expecting for revenue that quarter (the sell-side consensus / "
        f"estimate). News coverage of the print commonly states this in the "
        f'form "beat revenue estimates of $X" or "missed the $X estimate". '
        f"You can also derive it from a stated beat/miss percentage if news "
        f"coverage reports both the actual and the surprise.\n\n"
        f"Return ONLY this JSON (numbers only, no units; revenue in USD "
        f"dollars, e.g. 10870000000 for $10.87B):\n"
        f"{{\n"
        f'  "rev_consensus_usd": <consensus revenue in USD dollars, or null>,\n'
        f'  "surprise_pct": <reported beat/miss percent as a number, or null>,\n'
        f'  "source_note": "<short note on where the figure came from>"\n'
        f"}}\n"
        f"Return null only if no credible source reports the consensus or a "
        f"beat/miss percentage. Never fabricate."
    )


def _num(v) -> Optional[float]:
    """Coerce a Perplexity scalar to float, or None (no fabrication on junk)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f else None
    if isinstance(v, str):
        s = v.strip().replace("$", "").replace(",", "")
        m = re.match(r"^([\-+]?\d+(?:\.\d+)?)\s*([bBmMkK])?", s)
        if not m:
            return None
        val = float(m.group(1))
        suf = (m.group(2) or "").lower()
        return val * {"b": 1e9, "m": 1e6, "k": 1e3}.get(suf, 1.0)
    return None


class PerplexitySource(EarningsSource):
    name = "perplexity"

    def fetch(self, symbol: str) -> Optional[SourceResponse]:
        """Supply revenue consensus only. Requires identity context (company,
        date, and the already-known revenue actual) which ``refresh_ticker``
        threads in via ``set_context``; without it, Perplexity cannot anchor the
        consensus and the source declines (returns None).
        """
        symbol = symbol.upper().strip()
        ctx = getattr(self, "_context", {}) or {}
        company = ctx.get("company_name") or symbol
        date = ctx.get("last_earnings_date") or ctx.get("last_reported_date") or ""
        actual_str = ctx.get("in_quarter_rev_actual")
        if not date or actual_str in (None, ""):
            # No anchor -> cannot responsibly ask for a consensus.
            return None

        res = call_perplexity(
            ticker=symbol,
            task="rev_consensus_estimate",
            prompt=_consensus_prompt(symbol, company, str(date), str(actual_str)),
            extra_meta={"model": "sonar", "purpose": "pipeline_rev_consensus"},
        )
        if not isinstance(res, dict) or res.get("skipped") or res.get("queued"):
            return None

        consensus = _num(res.get("rev_consensus_usd"))
        if consensus is None:
            return None

        rvc: dict = {
            "in_quarter_rev_consensus": consensus,
            "in_quarter_rev_estimate": consensus,
            "rev_consensus_source": "perplexity_sonar",
        }
        # Derive rev surprise only if we also know the real actual.
        actual_num = _num(actual_str)
        if actual_num is not None and consensus:
            rvc["in_quarter_rev_surprise_pct"] = round((actual_num / consensus - 1.0) * 100, 2)
            rvc["rev_surprise_source"] = "computed_from_perplexity"

        return self.response(symbol, {"results_vs_consensus": rvc})

    def set_context(self, context: dict) -> "PerplexitySource":
        """Thread per-ticker identity (company, date, revenue actual) so the
        consensus query can be anchored. Returns self for chaining.
        """
        self._context = context
        return self
