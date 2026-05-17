-- =====================================================
-- Migration 016: Whale Alerts
-- Alert banners for large whale activities shown on the
-- Whales tab in TrackingView.
-- =====================================================

CREATE TABLE IF NOT EXISTS whale_alerts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    ticker      TEXT,
    action_title TEXT NOT NULL DEFAULT 'View Full Alert',
    whale_id    UUID REFERENCES whales(id) ON DELETE CASCADE,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    expires_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_whale_alerts_active
    ON whale_alerts(is_active, created_at DESC)
    WHERE is_active = true;

COMMENT ON TABLE whale_alerts IS 'Whale activity alert banners for the Whales tab';

-- RLS
ALTER TABLE whale_alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "whale_alerts_select_all"
    ON whale_alerts FOR SELECT
    USING (true);

CREATE POLICY "whale_alerts_service_all"
    ON whale_alerts FOR ALL
    USING (auth.role() = 'service_role');

-- Grants
GRANT SELECT ON TABLE public.whale_alerts TO anon;
GRANT SELECT ON TABLE public.whale_alerts TO authenticated;
GRANT ALL ON TABLE public.whale_alerts TO service_role;
