-- Short interest cache (Yahoo Finance data, 24-hour TTL)
-- Short interest updates bi-monthly via FINRA reports, so aggressive caching is safe.

CREATE TABLE IF NOT EXISTS short_interest_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_short_interest_cache_ticker ON short_interest_cache(ticker);

-- RLS policy: service role full access
ALTER TABLE short_interest_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON short_interest_cache
    FOR ALL
    USING (true)
    WITH CHECK (true);

GRANT ALL ON short_interest_cache TO service_role;
GRANT ALL ON short_interest_cache TO authenticated;
