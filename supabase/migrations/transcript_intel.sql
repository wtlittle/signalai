-- ============================================================
-- Supabase migration: transcript_intel table
-- Run in Supabase SQL Editor or via: supabase db push
-- ============================================================

create table if not exists public.transcript_intel (
  id                    bigserial primary key,

  -- Identity (composite unique key for upserts)
  ticker                text        not null,
  earnings_date         date        not null,

  -- Company metadata
  company_name          text,
  quarter               text,         -- e.g. "Q1 FY2026"

  -- Source tracking
  transcript_source     text,         -- "motley_fool" | "perplexity_native"
  transcript_url        text,         -- Direct link to Fool article (if scraped)

  -- LLM-distilled content
  beat_miss_summary     text,         -- 1-sentence headline
  management_tone       text,         -- "bullish" | "cautious" | "neutral" | "mixed"

  mgmt_key_points       jsonb default '[]'::jsonb,     -- list[str]
  guidance_statements   jsonb default '[]'::jsonb,     -- list[str]
  qa_key_exchanges      jsonb default '[]'::jsonb,     -- list[{analyst, question, answer}]
  tone_signals          jsonb default '[]'::jsonb,     -- list[str]
  key_metrics_discussed jsonb default '[]'::jsonb,     -- list[str]
  notable_quotes        jsonb default '[]'::jsonb,     -- list[{speaker, quote}]
  risk_factors_cited    jsonb default '[]'::jsonb,     -- list[str]

  -- Freshness
  harvested_at          timestamptz not null default now(),

  -- Composite unique constraint — enables upsert on conflict
  constraint transcript_intel_ticker_date_unique unique (ticker, earnings_date)
);

-- Indexes
create index if not exists idx_transcript_intel_ticker
  on public.transcript_intel (ticker);

create index if not exists idx_transcript_intel_earnings_date
  on public.transcript_intel (earnings_date desc);

create index if not exists idx_transcript_intel_harvested_at
  on public.transcript_intel (harvested_at desc);

-- RLS
alter table public.transcript_intel enable row level security;

create policy "service_role_all"
  on public.transcript_intel
  as permissive for all
  to service_role
  using (true) with check (true);

create policy "authenticated_read"
  on public.transcript_intel
  as permissive for select
  to authenticated
  using (true);

-- Comments
comment on table public.transcript_intel is
  'Earnings call transcript intelligence: scraped from Motley Fool or '
  'synthesized via Perplexity-native research. One row per (ticker, earnings_date).';

comment on column public.transcript_intel.transcript_source is
  '"motley_fool" = scraped HTML; "perplexity_native" = LLM research fallback.';

comment on column public.transcript_intel.mgmt_key_points is
  'JSON array of key bullets from management prepared remarks.';

comment on column public.transcript_intel.qa_key_exchanges is
  'JSON array of {analyst, question, answer} objects from Q&A session.';

comment on column public.transcript_intel.tone_signals is
  'Language patterns that signal tone, e.g. "macro uncertainty cited 4x".';
