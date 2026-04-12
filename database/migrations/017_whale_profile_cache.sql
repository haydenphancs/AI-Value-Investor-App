-- =====================================================
-- Migration 017: Whale Profile Cache
-- Cache-aside table for fully assembled whale profile
-- JSON responses. 24-hour TTL.
-- =====================================================

CREATE TABLE IF NOT EXISTS whale_profile_cache (
    whale_id    UUID PRIMARY KEY REFERENCES whales(id) ON DELETE CASCADE,
    profile_json JSONB NOT NULL,
    cached_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_whale_profile_cache_ts
    ON whale_profile_cache(cached_at DESC);

COMMENT ON TABLE whale_profile_cache IS 'Cache-aside for assembled WhaleProfileResponse JSON. 24h TTL.';

-- RLS
ALTER TABLE whale_profile_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "whale_profile_cache_select_all"
    ON whale_profile_cache FOR SELECT
    USING (true);

CREATE POLICY "whale_profile_cache_service_all"
    ON whale_profile_cache FOR ALL
    USING (auth.role() = 'service_role');

-- Grants
GRANT SELECT ON TABLE public.whale_profile_cache TO anon;
GRANT SELECT ON TABLE public.whale_profile_cache TO authenticated;
GRANT ALL ON TABLE public.whale_profile_cache TO service_role;
