-- Migration 014: Health Check Cache
-- Cache-aside storage for the Health Check section.
-- Stores final JSON response; invalidated after 24 hours or past next earnings date.

-- ── Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS health_check_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,              -- Serialized HealthCheckResponse
    cached_at TIMESTAMPTZ DEFAULT now(),
    next_earnings_date TEXT                     -- yyyy-MM-dd, for staleness check
);

-- ── Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_health_check_cache_ticker
    ON health_check_cache (ticker);

-- ── Grants ───────────────────────────────────────────────────────────
-- Service role needs full access for upsert + read
GRANT SELECT, INSERT, UPDATE ON health_check_cache TO service_role;
