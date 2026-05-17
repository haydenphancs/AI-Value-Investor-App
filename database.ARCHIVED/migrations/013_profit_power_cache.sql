-- Migration 013: Profit Power Cache
-- Cache-aside storage for the Profit Power (margins) section.
-- Stores final JSON response; invalidated after 24 hours or past next earnings date.

-- ── Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profit_power_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,              -- Serialized ProfitPowerResponse
    cached_at TIMESTAMPTZ DEFAULT now(),
    next_earnings_date TEXT                     -- yyyy-MM-dd, for staleness check
);

-- ── Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_profit_power_cache_ticker
    ON profit_power_cache (ticker);

-- ── Grants ───────────────────────────────────────────────────────────
-- Service role needs full access for upsert + read
GRANT SELECT, INSERT, UPDATE ON profit_power_cache TO service_role;
