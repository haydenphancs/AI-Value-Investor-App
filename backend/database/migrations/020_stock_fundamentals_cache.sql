-- Stock fundamentals cache (24-hour TTL)
-- Stores slow-moving data: P/E, EPS, Beta, ownership, financial statements, etc.
-- Volatile data (quote, chart) is NOT cached here — only in-memory at 120s.

CREATE TABLE IF NOT EXISTS stock_fundamentals_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stock_fundamentals_cache_ticker ON stock_fundamentals_cache(ticker);

ALTER TABLE stock_fundamentals_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON stock_fundamentals_cache
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON stock_fundamentals_cache TO service_role;
GRANT ALL ON stock_fundamentals_cache TO authenticated;
