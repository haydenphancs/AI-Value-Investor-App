-- Unified ETF snapshot cache — replaces separate per-section tables.
-- Each row stores one snapshot category for one ETF symbol.
-- Categories: identity_rating, strategy, net_yield, holdings_risk, dividend_history
-- TTL: 24 hours (checked in application code via cached_at).

CREATE TABLE IF NOT EXISTS etf_snapshot_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    category TEXT NOT NULL,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(symbol, category)
);

CREATE INDEX IF NOT EXISTS idx_etf_snapshot_cache_symbol ON etf_snapshot_cache (symbol);

ALTER TABLE etf_snapshot_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on etf_snapshot_cache"
    ON etf_snapshot_cache
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Drop old per-section tables
DROP TABLE IF EXISTS etf_dividend_cache;
DROP TABLE IF EXISTS etf_holdings_risk_cache;
