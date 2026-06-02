"""
Finnhub free-tier data fetcher.

Calls https://finnhub.io/api/v1 directly using a FINNHUB_API_KEY env var
or the custom-credential proxy (api_credentials=['custom-cred:finnhub.io'])
when run from Computer. The credential is NEVER read from disk or written
to the codebase.

Free-tier endpoints covered:
  - /stock/recommendation (monthly analyst buckets, ~4 months history)
  - /stock/insider-transactions (Form 4 trades)

Paid endpoints (NOT used here):
  - /stock/upgrade-downgrade
  - /stock/price-target

All functions return None on any failure so callers can append to errors[]
without raising.
"""
from __future__ import annotations

import os
from typing import Any

import requests
import urllib3

BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 30  # seconds

# When running through the Perplexity agent-proxy (custom-credential mode),
# the proxy MITM-intercepts HTTPS and presents a self-signed cert that
# OpenSSL 3.x rejects (missing Authority Key Identifier). The proxy is
# Perplexity-managed and trusted, so we relax verification only when
# routing through it. In production cron with FINNHUB_API_KEY env var
# (no proxy), verification stays on.
_PROXY_HOST_HINT = "agent-proxy.perplexity.ai"


def _via_perplexity_proxy() -> bool:
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    )
    return _PROXY_HOST_HINT in proxy


def _verify_setting() -> bool:
    if _via_perplexity_proxy():
        # Suppress urllib3 InsecureRequestWarning noise.
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return False
    return True


def _has_credential() -> bool:
    """True if either a direct FINNHUB_API_KEY is set OR the custom-cred
    proxy is in play (HTTPS_PROXY pointing at the credential-injection
    proxy). The proxy auto-injects ?token=<key> for finnhub.io requests."""
    if os.environ.get("FINNHUB_API_KEY"):
        return True
    # When api_credentials=['custom-cred:finnhub.io'] is passed to bash,
    # the proxy intercepts outbound HTTPS to finnhub.io and adds the token.
    return bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))


def _params_with_token(params: dict[str, str]) -> dict[str, str]:
    """Add token=<key> if FINNHUB_API_KEY is set. When the custom-cred
    proxy is in use, the proxy injects the token and this is a no-op."""
    key = os.environ.get("FINNHUB_API_KEY")
    if key:
        params = dict(params)
        params["token"] = key
    return params


def get_recommendation_trends(symbol: str) -> list[dict[str, Any]] | None:
    """Monthly analyst recommendation buckets (last ~4 months).

    Returns list like:
        [{"period": "2026-05-01", "strongBuy": 24, "buy": 42, "hold": 4,
          "sell": 1, "strongSell": 0, "symbol": "NVDA"}, ...]

    Returns None on failure (no credential, network error, non-200).
    """
    if not _has_credential():
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/stock/recommendation",
            params=_params_with_token({"symbol": symbol}),
            timeout=_TIMEOUT,
            verify=_verify_setting(),
        )
        if resp.status_code != 200:
            print(
                f"  [WARN] Finnhub recommendation {symbol}: "
                f"HTTP {resp.status_code} {resp.text[:120]}"
            )
            return None
        data = resp.json()
        return data if isinstance(data, list) else None
    except Exception as exc:
        print(f"  [WARN] Finnhub recommendation {symbol}: {exc}")
        return None


def get_insider_transactions(
    symbol: str,
    from_date: str,
    to_date: str,
) -> list[dict[str, Any]] | None:
    """Insider transactions (Form 4 buys/sells) for symbol in date range.

    from_date, to_date: ISO date strings (YYYY-MM-DD).

    Returns a list of individual transaction dicts, or None on failure.
    Finnhub wraps results in {"data": [...]} — this function unwraps.
    """
    if not _has_credential():
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/stock/insider-transactions",
            params=_params_with_token(
                {"symbol": symbol, "from": from_date, "to": to_date}
            ),
            timeout=_TIMEOUT,
            verify=_verify_setting(),
        )
        if resp.status_code != 200:
            print(
                f"  [WARN] Finnhub insider {symbol}: "
                f"HTTP {resp.status_code} {resp.text[:120]}"
            )
            return None
        data = resp.json()
        if isinstance(data, dict):
            return data.get("data") or []
        return data if isinstance(data, list) else None
    except Exception as exc:
        print(f"  [WARN] Finnhub insider {symbol}: {exc}")
        return None


def get_earnings_surprises(symbol: str) -> list[dict[str, Any]] | None:
    """Per-quarter EPS actual vs estimate from /stock/earnings (free tier).

    Returns list (most recent first) like:
        [{"actual": 1.32, "estimate": 1.1945, "period": "2026-06-30",
          "quarter": 1, "year": 2027, "surprise": 0.1255,
          "surprisePercent": 10.5065, "symbol": "MDB"}, ...]

    The actual/estimate are on a single consistent basis, so
    ``surprisePercent`` is a trustworthy beat/miss magnitude even if the
    absolute basis differs from a note's non-GAAP figure. Returns None on
    failure (no credential, network error, non-200).
    """
    if not _has_credential():
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/stock/earnings",
            params=_params_with_token({"symbol": symbol}),
            timeout=_TIMEOUT,
            verify=_verify_setting(),
        )
        if resp.status_code != 200:
            print(
                f"  [WARN] Finnhub earnings {symbol}: "
                f"HTTP {resp.status_code} {resp.text[:120]}"
            )
            return None
        data = resp.json()
        return data if isinstance(data, list) else None
    except Exception as exc:
        print(f"  [WARN] Finnhub earnings {symbol}: {exc}")
        return None


def summarize_insider_tx(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate insider transactions into n_buys, n_sells, net_value_usd."""
    if not transactions:
        return {"n_buys": 0, "n_sells": 0, "net_value_usd": 0.0}
    n_buys = 0
    n_sells = 0
    net = 0.0
    for tx in transactions:
        share = tx.get("change") or 0
        price = tx.get("transactionPrice") or 0
        value = share * price  # negative share => sell, positive => buy
        net += value
        if share > 0:
            n_buys += 1
        elif share < 0:
            n_sells += 1
    return {
        "n_buys": n_buys,
        "n_sells": n_sells,
        "net_value_usd": round(net, 2),
    }
