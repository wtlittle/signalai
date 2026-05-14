-- =============================================================================
-- user_alert_subscriptions
-- =============================================================================
-- Stores per-user alert feed subscriptions. The alerts page upserts into this
-- table whenever a toggle / channel chip changes; Phase 2 cron jobs read from
-- it to decide which subscribers to notify on each event.
--
-- Schema design notes:
--   * Single JSONB column keeps the subscription catalogue evolvable without
--     migrations. Each key maps a stable subscription_id (see
--     alerts.js SUBSCRIPTION_CATALOG) to:
--       { "enabled": <bool>, "channels": ["in_app"|"push"|"email"] }
--   * RLS is enabled with a permissive policy because the publishable anon key
--     is used directly from the browser. To tighten later, swap to
--     auth.uid() = user_id and migrate user_email -> user_id.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.user_alert_subscriptions (
  user_email     TEXT PRIMARY KEY,
  subscriptions  JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Touch updated_at on every write
CREATE OR REPLACE FUNCTION public.user_alert_subscriptions_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_alert_subscriptions_touch ON public.user_alert_subscriptions;
CREATE TRIGGER user_alert_subscriptions_touch
  BEFORE UPDATE ON public.user_alert_subscriptions
  FOR EACH ROW
  EXECUTE FUNCTION public.user_alert_subscriptions_touch_updated_at();

-- Permissive RLS for the single-user app phase. Replace with auth-bound policy
-- when multi-user support lands.
ALTER TABLE public.user_alert_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_alert_subscriptions_all ON public.user_alert_subscriptions;
CREATE POLICY user_alert_subscriptions_all
  ON public.user_alert_subscriptions
  FOR ALL
  USING (true)
  WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.user_alert_subscriptions
  TO anon, authenticated;

-- =============================================================================
-- alert_activity (optional Phase 2 — Recent Activity persistence)
-- =============================================================================
-- Currently the client keeps Recent Activity in localStorage. When Phase 2
-- crons start emitting alerts server-side they will also insert here so
-- activity survives across devices.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.alert_activity (
  id            BIGSERIAL PRIMARY KEY,
  user_email    TEXT NOT NULL,
  alert_type    TEXT NOT NULL,
  ticker        TEXT,
  summary       TEXT,
  link          TEXT,
  severity      TEXT NOT NULL DEFAULT 'info',
  fired_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS alert_activity_user_fired_idx
  ON public.alert_activity (user_email, fired_at DESC);

ALTER TABLE public.alert_activity ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS alert_activity_all ON public.alert_activity;
CREATE POLICY alert_activity_all
  ON public.alert_activity
  FOR ALL
  USING (true)
  WITH CHECK (true);

GRANT SELECT, INSERT
  ON public.alert_activity
  TO anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE public.alert_activity_id_seq TO anon, authenticated;

-- =============================================================================
-- Verify
-- =============================================================================
-- SELECT * FROM public.user_alert_subscriptions LIMIT 5;
-- SELECT * FROM public.alert_activity ORDER BY fired_at DESC LIMIT 20;
