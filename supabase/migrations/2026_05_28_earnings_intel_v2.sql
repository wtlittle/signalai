-- =========================================================================
-- SignalAI v2 — Earnings Intel data layer
-- Run date: 2026-05-28
-- Purpose: Persist the deterministic context layer (Stage 1) and weekly
--          Industry Benchmark Packs (Option 4) so we can backtest and
--          continuously improve the synthesis quality.
-- =========================================================================

-- -------------------------------------------------------------------------
-- TABLE: industry_packs
-- One row per (subsector, week_of). Built by a weekly Computer cron that
-- runs wide_research per subsector and writes a JSON pack of: IT spend
-- benchmarks, hyperscaler readthrough, macro tape, subsector-specific
-- signals (e.g. CISO survey for cybersec, AI capex for semis). The pack
-- is then read by pre_earnings_context.py and post_earnings_context.py
-- when building the Industry Context section of each note.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.industry_packs (
    id              BIGSERIAL PRIMARY KEY,
    subsector       TEXT NOT NULL,
    week_of         DATE NOT NULL,           -- Monday of the ISO week
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pack            JSONB NOT NULL,          -- the full benchmark pack
    sources         TEXT[] NOT NULL DEFAULT '{}',
    cost_usd        NUMERIC(10, 4),          -- wide_research credit cost (debug)
    notes           TEXT,                    -- free-form generation notes
    UNIQUE (subsector, week_of)
);

CREATE INDEX IF NOT EXISTS idx_industry_packs_subsector
    ON public.industry_packs (subsector, week_of DESC);

CREATE INDEX IF NOT EXISTS idx_industry_packs_week
    ON public.industry_packs (week_of DESC);


-- -------------------------------------------------------------------------
-- TABLE: earnings_context_snapshots
-- One row per (ticker, earnings_date, note_type). Captures the full
-- deterministic context object (Stage 1) and the resulting LLM note
-- (Stage 2). The consensus_at_print field is broken out so we can
-- backtest "stock reaction vs guidance vs Street consensus at the
-- moment of the print" without re-parsing the JSON.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.earnings_context_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL,
    earnings_date       DATE NOT NULL,
    note_type           TEXT NOT NULL CHECK (note_type IN ('pre', 'post')),
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Stage 1 deterministic data layer (FactSet + yfinance + index)
    context             JSONB NOT NULL,
    -- The single most important field for backtesting: what was Street
    -- consensus at the moment the print landed?  Stored separately to
    -- avoid JSON gymnastics during analysis queries.
    consensus_at_print  JSONB,
    -- Stage 2 LLM output (the .md note that gets shipped to the dashboard)
    note_md             TEXT,
    -- Provenance + cost
    factset_used        BOOLEAN NOT NULL DEFAULT FALSE,
    yfinance_fallback   BOOLEAN NOT NULL DEFAULT FALSE,
    industry_pack_week  DATE,                -- which weekly pack was used
    llm_model           TEXT,                -- e.g. sonar-reasoning-pro
    llm_effort          TEXT,                -- low|medium|high
    llm_tokens_in       INTEGER,
    llm_tokens_out      INTEGER,
    llm_cost_usd        NUMERIC(10, 4),
    -- Tier (T1 cover / T2 standard) at the time of generation
    ticker_tier         TEXT,
    UNIQUE (ticker, earnings_date, note_type)
);

CREATE INDEX IF NOT EXISTS idx_earnings_ctx_ticker_date
    ON public.earnings_context_snapshots (ticker, earnings_date DESC);

CREATE INDEX IF NOT EXISTS idx_earnings_ctx_type_date
    ON public.earnings_context_snapshots (note_type, earnings_date DESC);

CREATE INDEX IF NOT EXISTS idx_earnings_ctx_factset
    ON public.earnings_context_snapshots (factset_used, earnings_date DESC)
    WHERE factset_used = FALSE;  -- partial: surface yfinance-fallback rows fast


-- -------------------------------------------------------------------------
-- RLS: keep tables service-key only for now. Dashboard reads via
-- service-role on the backend; no anon access.
-- -------------------------------------------------------------------------
ALTER TABLE public.industry_packs              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.earnings_context_snapshots  ENABLE ROW LEVEL SECURITY;

-- service_role bypasses RLS, so no permissive policy needed.
-- anon and authenticated get no access by default — explicit deny.

-- =========================================================================
-- END
-- =========================================================================
