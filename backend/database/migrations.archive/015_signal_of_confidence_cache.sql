-- Migration 015: Signal of Confidence Cache
-- Cache-aside storage for the Signal of Confidence (dividends, buybacks, shares) section.
-- Stores final JSON response; invalidated after 24 hours or past next earnings date.

-- ── Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signal_of_confidence_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,              -- Serialized SignalOfConfidenceResponse
    cached_at TIMESTAMPTZ DEFAULT now(),
    next_earnings_date TEXT                     -- yyyy-MM-dd, for staleness check
);

-- ── Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_signal_of_confidence_cache_ticker
    ON signal_of_confidence_cache (ticker);

-- ── Grants ───────────────────────────────────────────────────────────
-- Service role needs full access for upsert + read
GRANT SELECT, INSERT, UPDATE ON signal_of_confidence_cache TO service_role;
