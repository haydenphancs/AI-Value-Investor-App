-- Dedicated cache for valuation snapshot (Price section in Snapshots).
-- Same pattern as snapshot_profitability_cache and snapshot_growth_cache.
-- TTL: 24 hours (checked in application code via cached_at).
-- RLS disabled — matches other cache tables.

CREATE TABLE IF NOT EXISTS snapshot_valuation_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

-- Grant permissions (same as other cache tables)
GRANT ALL ON snapshot_valuation_cache TO service_role;
GRANT ALL ON snapshot_valuation_cache TO authenticated;
