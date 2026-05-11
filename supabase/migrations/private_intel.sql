-- private_intel: structured private/pre-IPO company intelligence.
-- Populated by automation.jobs.private_company_refresh

CREATE TABLE IF NOT EXISTS public.private_intel (
    name                  text        NOT NULL,
    subsector             text,
    valuation             jsonb,        -- {amount, unit, as_of}
    last_funding_round    jsonb,        -- {series, amount, date, lead_investor}
    arr_or_revenue        jsonb,        -- {amount, type, as_of}
    investors             jsonb,        -- string[]
    growth_signals        jsonb,        -- string[]
    ipo_signals           text,
    competitive_context   text,
    hq                    text,
    sources               jsonb,        -- string[] of URLs
    refresh_date          date        NOT NULL,
    harvested_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT private_intel_pk PRIMARY KEY (name, refresh_date)
);

CREATE INDEX IF NOT EXISTS private_intel_name_idx
    ON public.private_intel (name);

CREATE INDEX IF NOT EXISTS private_intel_subsector_idx
    ON public.private_intel (subsector);

CREATE INDEX IF NOT EXISTS private_intel_refresh_date_idx
    ON public.private_intel (refresh_date DESC);

ALTER TABLE public.private_intel ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS private_intel_service_role ON public.private_intel;
CREATE POLICY private_intel_service_role
    ON public.private_intel
    FOR ALL
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS private_intel_anon_read ON public.private_intel;
CREATE POLICY private_intel_anon_read
    ON public.private_intel
    FOR SELECT
    USING (true);
