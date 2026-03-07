-- =====================================================
-- Migration 008: Whale Hydration Engine Support
-- Adds tracking and caching columns for the pre-computation
-- hydration pipeline.
-- =====================================================

-- 1. Track when each whale was last hydrated
ALTER TABLE whales ADD COLUMN IF NOT EXISTS last_hydrated_at TIMESTAMPTZ;
COMMENT ON COLUMN whales.last_hydrated_at IS 'Timestamp of last successful hydration run';

-- 2. Cache logo lookups to avoid redundant FMP profile calls
ALTER TABLE whale_filing_snapshots
    ADD COLUMN IF NOT EXISTS logo_cache JSONB NOT NULL DEFAULT '{}';
COMMENT ON COLUMN whale_filing_snapshots.logo_cache IS 'Cached {ticker: logo_url} from FMP company profiles';
