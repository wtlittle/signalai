-- =============================================================================
-- drilldown_intel : institutional drilldown notes generated via Perplexity
--                   Deep Research, written from the watchlist app UI.
--
-- Each row stores a single Part 1 / Part 2 / merged HTML body, keyed by
-- (ticker, date, part). Upserts use ON CONFLICT(ticker, date, part) =
-- merge-duplicates so re-runs on the same day overwrite the existing row.
--
-- Run in the Supabase SQL editor at
--   https://wcyirdvvuetzodiedzss.supabase.co/project/_/sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS drilldown_intel (
    id                    bigserial PRIMARY KEY,
    ticker                text NOT NULL,
    date                  date NOT NULL,
    part                  text NOT NULL CHECK (part IN ('p1', 'p2', 'merged')),
    html                  text NOT NULL,
    trigger               text,
    price_at_generation   numeric,
    generated_at          timestamptz NOT NULL DEFAULT now(),
    markdown_path         text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, date, part)
);

CREATE INDEX IF NOT EXISTS drilldown_intel_ticker_idx
    ON drilldown_intel (ticker);

CREATE INDEX IF NOT EXISTS drilldown_intel_ticker_date_idx
    ON drilldown_intel (ticker, date DESC);

-- Auto-bump updated_at on every UPDATE so dashboard reads can show freshness.
CREATE OR REPLACE FUNCTION drilldown_intel_set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS drilldown_intel_updated_at ON drilldown_intel;
CREATE TRIGGER drilldown_intel_updated_at
    BEFORE UPDATE ON drilldown_intel
    FOR EACH ROW
    EXECUTE FUNCTION drilldown_intel_set_updated_at();

-- RLS: open read for anon (matches earnings_intel / transcript_intel pattern).
ALTER TABLE drilldown_intel ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS drilldown_intel_anon_read ON drilldown_intel;
CREATE POLICY drilldown_intel_anon_read
    ON drilldown_intel
    FOR SELECT
    USING (true);

-- Writes go through the service role only (the backend uses SUPABASE_SERVICE_KEY).
