"""
Per-ticker, contract-based earnings pipeline.

This package replaces the monolithic per-ticker loop that used to live inside
``automation/jobs/daily_refresh.py``. The atomic unit is now
``refresh_ticker.refresh_ticker(symbol)``: it fetches from sources in priority
order, validates the assembled record against the single pydantic contract in
``schema.EarningsIntelRecord``, and persists to ``earnings_intel.json`` ONLY IF
the validated record is complete. Incomplete records go to quarantine instead
of being written half-populated.

Public surface:
  * schema.EarningsIntelRecord       -- the one contract both the pipeline and
                                        the verifier honor.
  * refresh_ticker.refresh_ticker    -- the idempotent per-ticker unit.
  * runner.refresh_universe          -- parallel batch over many tickers.
"""
