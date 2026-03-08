-- Migration 009: Ticker News Cache
-- Hybrid "First User Pays" + Watchlist Pre-computation caching for AI-enriched news
-- Stores FMP news articles enriched with Gemini AI summaries and sentiment analysis

-- ── Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ticker_news_cache (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL,
    external_id TEXT,                                 -- FMP article identifier for dedup
    headline TEXT NOT NULL,
    summary TEXT,                                     -- Original article summary from FMP
    summary_bullets JSONB DEFAULT '[]'::jsonb,        -- AI-generated 3 bullet points
    sentiment TEXT CHECK (sentiment IN ('bullish', 'bearish', 'neutral')),
    sentiment_confidence INT DEFAULT 0,
    source_name TEXT,
    source_logo_url TEXT,
    published_at TIMESTAMPTZ,
    thumbnail_url TEXT,
    article_url TEXT,
    related_tickers JSONB DEFAULT '[]'::jsonb,
    ai_processed BOOLEAN DEFAULT FALSE,               -- Whether Gemini has processed this
    ai_model TEXT,                                    -- Which Gemini model was used
    cached_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ DEFAULT (now() + INTERVAL '6 hours'),
    UNIQUE(ticker, external_id)
);

-- ── Indexes ──────────────────────────────────────────────────────────
-- Fast lookups: get fresh cached news for a ticker
CREATE INDEX IF NOT EXISTS idx_ticker_news_cache_ticker_expires
    ON ticker_news_cache (ticker, expires_at DESC);

-- TTL cleanup: find expired rows efficiently
CREATE INDEX IF NOT EXISTS idx_ticker_news_cache_expires
    ON ticker_news_cache (expires_at);

-- ── Cleanup function ─────────────────────────────────────────────────
-- Call periodically (pg_cron or app-level) to prune expired cache entries
CREATE OR REPLACE FUNCTION cleanup_expired_news_cache()
RETURNS void AS $$
BEGIN
    DELETE FROM ticker_news_cache WHERE expires_at < now();
END;
$$ LANGUAGE plpgsql;

-- ── Watchlist popularity RPC ─────────────────────────────────────────
-- Returns top N most-tracked tickers across all user watchlists
-- Used by the background pre-warmer to decide which tickers to cache
CREATE OR REPLACE FUNCTION get_top_watchlist_tickers(n INT DEFAULT 20)
RETURNS TABLE(ticker TEXT, watch_count BIGINT) AS $$
BEGIN
    RETURN QUERY
    SELECT wi.ticker, COUNT(*) as watch_count
    FROM watchlist_items wi
    GROUP BY wi.ticker
    ORDER BY watch_count DESC
    LIMIT n;
END;
$$ LANGUAGE plpgsql;
