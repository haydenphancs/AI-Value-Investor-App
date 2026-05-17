-- Migration 015: Social Mentions History
-- Stores daily snapshots of Reddit mention counts from ApeWisdom API.
-- Used to compute 7-day rolling social mention metrics for sentiment analysis.

-- ── Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS social_mentions_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL,
    mentions INT NOT NULL DEFAULT 0,
    upvotes INT NOT NULL DEFAULT 0,
    rank INT,
    source TEXT DEFAULT 'apewisdom',
    snapshot_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(ticker, snapshot_date, source)
);

-- ── Indexes ──────────────────────────────────────────────────────────
-- Fast lookups: get recent mention history for a ticker
CREATE INDEX IF NOT EXISTS idx_social_mentions_ticker_date
    ON social_mentions_history (ticker, snapshot_date DESC);

-- ── Cleanup function ─────────────────────────────────────────────────
-- Keep 30 days max. Call periodically (pg_cron or app-level).
CREATE OR REPLACE FUNCTION cleanup_old_social_mentions()
RETURNS void AS $$
BEGIN
    DELETE FROM social_mentions_history
    WHERE snapshot_date < CURRENT_DATE - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
