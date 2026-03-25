-- Dedicated cache for growth snapshot with sector-relative scoring.
-- TTL: 24 hours (checked in application code via cached_at).
-- RLS disabled — matches other cache tables.

CREATE TABLE IF NOT EXISTS snapshot_growth_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);
