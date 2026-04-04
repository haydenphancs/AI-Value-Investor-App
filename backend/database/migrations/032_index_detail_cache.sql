-- Index detail cache (24-hour TTL)
-- Stores full index detail responses keyed by symbol + chart range.
-- The app layer enforces the 24h TTL; rows are upserted on each fresh fetch.

CREATE TABLE IF NOT EXISTS index_detail_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    cache_key TEXT NOT NULL UNIQUE,          -- e.g. "^GSPC_3M"
    symbol TEXT NOT NULL,
    chart_range TEXT NOT NULL,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_index_detail_cache_symbol ON index_detail_cache(symbol);

ALTER TABLE index_detail_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON index_detail_cache
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON index_detail_cache TO service_role;
GRANT ALL ON index_detail_cache TO authenticated;
