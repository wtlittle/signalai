-- revision_history: weekly snapshots of sell-side consensus EPS/revenue estimates per ticker.
-- Populated by automation.jobs.estimate_revision_tracker

CREATE TABLE IF NOT EXISTS public.revision_history (
    ticker             text        NOT NULL,
    date               date        NOT NULL,
    fwd_eps_est        numeric,
    fwd_rev_est        numeric,         -- in USD billions
    num_analysts       integer,
    target_mean        numeric,
    eps_revision_1w    numeric,         -- decimal (e.g. 0.012 = +1.2%)
    eps_revision_4w    numeric,
    rev_revision_1w    numeric,
    rev_revision_4w    numeric,
    direction          text,            -- upward | downward | stable | mixed
    narrative          text,             -- optional qualitative summary from Perplexity
    harvested_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT revision_history_pk PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS revision_history_ticker_idx
    ON public.revision_history (ticker);

CREATE INDEX IF NOT EXISTS revision_history_date_idx
    ON public.revision_history (date DESC);

CREATE INDEX IF NOT EXISTS revision_history_harvested_at_idx
    ON public.revision_history (harvested_at DESC);

ALTER TABLE public.revision_history ENABLE ROW LEVEL SECURITY;

-- Allow service-role full access (writes from automation jobs)
DROP POLICY IF EXISTS revision_history_service_role
    ON public.revision_history;
CREATE POLICY revision_history_service_role
    ON public.revision_history
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Read-only anon access (for backend.py / dashboard fetch)
DROP POLICY IF EXISTS revision_history_anon_read
    ON public.revision_history;
CREATE POLICY revision_history_anon_read
    ON public.revision_history
    FOR SELECT
    USING (true);
