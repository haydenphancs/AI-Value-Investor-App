-- Unified snapshot cache — replaces 5 separate tables with one.
-- Each row stores one snapshot category for one ticker.
-- TTL: 24 hours (checked in application code via cached_at).

CREATE TABLE IF NOT EXISTS snapshot_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT NOT NULL,
    category TEXT NOT NULL,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(ticker, category)
);

GRANT ALL ON snapshot_cache TO service_role;
GRANT ALL ON snapshot_cache TO authenticated;

-- Drop old per-category tables
DROP TABLE IF EXISTS snapshot_profitability_cache;
DROP TABLE IF EXISTS snapshot_growth_cache;
DROP TABLE IF EXISTS snapshot_valuation_cache;
DROP TABLE IF EXISTS snapshot_health_cache;
DROP TABLE IF EXISTS snapshot_ownership_cache;
