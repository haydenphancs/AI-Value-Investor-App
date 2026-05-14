-- Dedicated cache for profitability snapshot (separate from fundamentals cache)
-- so it can be refreshed independently with sector-relative scoring.
-- TTL: 24 hours (checked in application code via cached_at).
-- RLS disabled — matches other cache tables (profit_power_cache, etc.)

CREATE TABLE IF NOT EXISTS snapshot_profitability_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);
