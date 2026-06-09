-- ============================================================================
-- market_intel_ticker
-- ----------------------------------------------------------------------------
-- Per-TICKER TAM / category-growth / competitive-landscape fact-base populated
-- by automation/jobs/market_intel_refresh.py (90-day TTL, first Sunday 02:00 UTC).
--
-- Distinct from the legacy per-SUBSECTOR `market_intel` table
-- (automation/scripts/create_market_intel_table.sql), which is still written by
-- market_intel_harvest.py. This table is keyed on a single ticker and feeds the
-- drilldown SIGNAL_DATA_BLOCK `market_intel` section per company.
--
-- Apply via the Supabase SQL editor or psql. Idempotent: safe to re-run.
-- ============================================================================

create table if not exists public.market_intel_ticker (
    ticker            text        not null,
    tam_usd_bn        numeric,
    tam_source_url    text,
    category_cagr_pct numeric,
    drivers           jsonb,
    competitors       jsonb,
    updated_at        timestamptz not null default now(),
    constraint market_intel_ticker_pkey primary key (ticker)
);

create index if not exists market_intel_ticker_updated_at_idx
    on public.market_intel_ticker (updated_at desc);

-- Service role upserts; the watchlist app reads via the publishable key.
alter table public.market_intel_ticker enable row level security;

drop policy if exists market_intel_ticker_read_anon on public.market_intel_ticker;
create policy market_intel_ticker_read_anon
    on public.market_intel_ticker
    for select
    using (true);

drop policy if exists market_intel_ticker_write_service on public.market_intel_ticker;
create policy market_intel_ticker_write_service
    on public.market_intel_ticker
    for all
    to service_role
    using (true)
    with check (true);

comment on table public.market_intel_ticker is
    'Per-ticker TAM / category CAGR / competitive quadrant, harvested on a 90-day TTL by automation/jobs/market_intel_refresh.py.';
comment on column public.market_intel_ticker.tam_usd_bn is
    'Numeric TAM in USD billions for the ticker''s primary market; null if no credible figure.';
comment on column public.market_intel_ticker.tam_source_url is
    'URL of the primary source backing the TAM figure (Gartner/Forrester/IDC/Statista/SEC/IR site).';
comment on column public.market_intel_ticker.category_cagr_pct is
    'Numeric category CAGR as a percent (12.4 means 12.4%); null if not available.';
comment on column public.market_intel_ticker.drivers is
    'JSON array of 3-5 market growth-driver strings.';
comment on column public.market_intel_ticker.competitors is
    'JSON array of 4-7 objects: {name, ticker, quadrant, threat, source_url}. quadrant in {Leader,Challenger,Visionary,Niche}; threat in {Low,Med,High}.';
