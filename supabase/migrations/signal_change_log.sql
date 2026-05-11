-- signal_change_log: per-event log of signal_scorecard updates emitted by note_diff_injector.
-- One row per (ticker, signal_id, source_url) news/event combination.

CREATE TABLE IF NOT EXISTS public.signal_change_log (
    id            bigserial   PRIMARY KEY,
    ticker        text        NOT NULL,
    signal_id     text        NOT NULL,
    old_status    text,
    new_status    text,
    source_url    text        NOT NULL DEFAULT '',
    headline      text,
    direction     text,
    catalyst_tag  text,
    blurb         text,
    changed_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT signal_change_log_unique UNIQUE (ticker, signal_id, source_url)
);

CREATE INDEX IF NOT EXISTS signal_change_log_ticker_idx
    ON public.signal_change_log (ticker);
CREATE INDEX IF NOT EXISTS signal_change_log_changed_at_idx
    ON public.signal_change_log (changed_at DESC);

ALTER TABLE public.signal_change_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS signal_change_log_service_role ON public.signal_change_log;
CREATE POLICY signal_change_log_service_role
    ON public.signal_change_log
    FOR ALL
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS signal_change_log_anon_read ON public.signal_change_log;
CREATE POLICY signal_change_log_anon_read
    ON public.signal_change_log
    FOR SELECT
    USING (true);
