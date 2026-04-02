-- ETF detail cache (24-hour TTL)
-- Stores slow-moving ETF data: expense ratio, holdings, sectors, snapshots, etc.
-- Volatile data (quote, chart) is NOT cached here — only in-memory at 300s.

CREATE TABLE IF NOT EXISTS etf_detail_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_etf_detail_cache_symbol ON etf_detail_cache(symbol);

ALTER TABLE etf_detail_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON etf_detail_cache
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON etf_detail_cache TO service_role;
GRANT ALL ON etf_detail_cache TO authenticated;
