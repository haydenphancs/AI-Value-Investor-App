-- Permanent cache for dynamically resolved crypto symbol → CoinGecko ID mappings.
-- Used for coins outside the top 100 hardcoded list.
-- Mappings rarely change, so no TTL — cached forever.

CREATE TABLE IF NOT EXISTS crypto_coin_id_cache (
    symbol TEXT PRIMARY KEY,
    coingecko_id TEXT NOT NULL,
    name TEXT,
    cached_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE crypto_coin_id_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON crypto_coin_id_cache
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON crypto_coin_id_cache TO service_role;
GRANT ALL ON crypto_coin_id_cache TO authenticated;
