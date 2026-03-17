-- Migration 012: Revenue Breakdown Cache
-- Cache-aside storage for "How [TICKER] Makes Money" section.
-- Stores final JSON response; invalidated after 24 hours or past next earnings date.

-- ── Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS revenue_breakdown_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,              -- Serialized RevenueBreakdownResponse
    cached_at TIMESTAMPTZ DEFAULT now(),
    next_earnings_date TEXT                     -- yyyy-MM-dd, for staleness check
);

-- ── Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_revenue_breakdown_cache_ticker
    ON revenue_breakdown_cache (ticker);

-- ── Grants ───────────────────────────────────────────────────────────
-- Service role needs full access for upsert + read
GRANT SELECT, INSERT, UPDATE ON revenue_breakdown_cache TO service_role;
