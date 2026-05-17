-- Crypto fundamentals cache (6-hour TTL)
-- Stores CoinGecko coin data: market_data, supply, ATH/ATL, etc.
-- Volatile chart data is NOT cached here — only in-memory.

CREATE TABLE IF NOT EXISTS crypto_fundamentals_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crypto_fundamentals_cache_symbol ON crypto_fundamentals_cache(symbol);

ALTER TABLE crypto_fundamentals_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON crypto_fundamentals_cache
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON crypto_fundamentals_cache TO service_role;
GRANT ALL ON crypto_fundamentals_cache TO authenticated;
