-- Holders cache table for the cache-aside pattern
-- Stores the full JSON response keyed by ticker symbol
-- TTL: 24 hours (holder data changes infrequently)

CREATE TABLE IF NOT EXISTS holders_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_holders_cache_ticker
    ON holders_cache (ticker);

GRANT SELECT, INSERT, UPDATE ON holders_cache TO service_role;
