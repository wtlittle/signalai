-- Track every Perplexity API call for per-source / per-ticker spend visibility.
CREATE TABLE IF NOT EXISTS perplexity_api_usage (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source TEXT NOT NULL,
  ticker TEXT,
  model TEXT NOT NULL,
  prompt_tokens INT,
  completion_tokens INT,
  citation_tokens INT,
  reasoning_tokens INT,
  total_tokens INT,
  num_search_queries INT,
  search_context_size TEXT,
  estimated_cost_usd NUMERIC(10,6),
  latency_ms INT,
  status TEXT NOT NULL,
  error_message TEXT,
  metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_pplx_usage_ts ON perplexity_api_usage (ts DESC);
CREATE INDEX IF NOT EXISTS idx_pplx_usage_source ON perplexity_api_usage (source);
CREATE INDEX IF NOT EXISTS idx_pplx_usage_ticker ON perplexity_api_usage (ticker);
