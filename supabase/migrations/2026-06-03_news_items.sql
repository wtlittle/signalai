-- Backend-served news items. Populated hourly by automation.jobs.refresh_news_items
-- (GitHub Actions cron) and read by the client via the anon SELECT policy.
CREATE TABLE IF NOT EXISTS news_items (
  id TEXT PRIMARY KEY,                    -- sha256(url) or sha256(normalized_title) when url absent
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source_origin TEXT NOT NULL,            -- 'perplexity_sonar' (room for yahoo/google later)
  ticker TEXT,                            -- primary ticker
  tickers TEXT[] NOT NULL DEFAULT '{}',   -- all tagged tickers
  title TEXT NOT NULL,
  body TEXT,                              -- 1-sentence summary
  url TEXT,
  url_host TEXT,                          -- extracted host: bloomberg.com, reuters.com
  source TEXT,                            -- publisher name: Bloomberg, Reuters
  published_at TIMESTAMPTZ,
  signal TEXT                             -- earnings|guidance_up|guidance_dn|ma|analyst|macro|general
);

CREATE INDEX IF NOT EXISTS news_items_published_at_idx ON news_items (published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS news_items_fetched_at_idx ON news_items (fetched_at DESC);
CREATE INDEX IF NOT EXISTS news_items_tickers_gin ON news_items USING GIN (tickers);

ALTER TABLE news_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY news_items_anon_read ON news_items FOR SELECT TO anon USING (true);
CREATE POLICY news_items_service_write ON news_items FOR ALL TO service_role USING (true) WITH CHECK (true);
