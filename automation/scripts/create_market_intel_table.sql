-- ============================================================================
-- market_intel
-- ----------------------------------------------------------------------------
-- Per-subsector TAM, category-growth, and AI/ML context fact-base populated
-- weekly by automation/jobs/market_intel_harvest.py (Sunday BMO).
--
-- Primary key: (subsector, source) so we can hold multiple credible sources
-- per subsector (Gartner + IDC + investor-day, etc.) without overwriting.
--
-- Apply via Supabase SQL editor or psql. Idempotent: safe to re-run.
-- ============================================================================

create table if not exists public.market_intel (
    subsector            text        not null,
    source               text        not null,
    tam_label            text,
    tam_usd_bn           numeric,
    growth_rate_label    text,
    growth_rate_pct      numeric,
    structural_drivers   text,
    ai_ml_context        text,
    raw_excerpt          text,
    harvested_at         timestamptz not null default now(),
    constraint market_intel_pkey primary key (subsector, source)
);

create index if not exists market_intel_subsector_idx
    on public.market_intel (subsector);

create index if not exists market_intel_harvested_at_idx
    on public.market_intel (harvested_at desc);

-- Allow the service role to upsert rows. The watchlist app only reads via
-- the publishable key, which inherits row-level access from RLS policies.
alter table public.market_intel enable row level security;

drop policy if exists market_intel_read_anon on public.market_intel;
create policy market_intel_read_anon
    on public.market_intel
    for select
    using (true);

drop policy if exists market_intel_write_service on public.market_intel;
create policy market_intel_write_service
    on public.market_intel
    for all
    to service_role
    using (true)
    with check (true);

comment on table public.market_intel is
    'Per-subsector TAM / CAGR / AI-ML context, harvested weekly by automation/jobs/market_intel_harvest.py.';
comment on column public.market_intel.subsector is
    'Subsector label matching SUBSECTOR_MAP in utils.js (e.g. "Cybersecurity", "Hyperscalers").';
comment on column public.market_intel.source is
    'Primary source attributed by the LLM (Gartner, IDC, Forrester, Statista, McKinsey, investor-day, 10-K MD&A, ...).';
comment on column public.market_intel.tam_usd_bn is
    'Numeric TAM expressed in USD billions; null if no credible figure is available.';
comment on column public.market_intel.growth_rate_pct is
    'Numeric category CAGR expressed as a percent (e.g. 12.4 means 12.4%); null if not available.';
