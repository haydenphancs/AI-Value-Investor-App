-- =====================================================
-- Caydex - Home Feed Migration
-- Adds tables and columns required by GET /api/v1/home/feed
-- Date: March 1, 2026
-- =====================================================
--
-- Run AFTER supabase_schema.sql (safe to re-run; uses IF NOT EXISTS)
--
-- Adds:
-- 1. market_insights table     (AI-generated market summaries)
-- 2. daily_briefings table     (Alerts / briefing cards)
-- 3. overall_score column      on research_reports
-- 4. fair_value_estimate column on research_reports
-- =====================================================

-- =====================================================
-- 1. MARKET INSIGHTS
-- =====================================================
-- Source: HomeService._get_market_insight()
-- Stores AI-generated or curated market summaries shown
-- on the Home screen's MarketInsightsCard.

CREATE TABLE IF NOT EXISTS market_insights (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    headline      TEXT NOT NULL,
    bullet_points JSONB NOT NULL DEFAULT '[]'::JSONB,
    sentiment     TEXT NOT NULL DEFAULT 'Neutral'
                  CHECK (sentiment IN ('Bullish', 'Bearish', 'Neutral')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_market_insights_created
    ON market_insights(created_at DESC);

COMMENT ON TABLE market_insights
    IS 'AI-generated market summaries for the Home screen insight card';
COMMENT ON COLUMN market_insights.bullet_points
    IS 'JSONB array of strings: ["point 1", "point 2"]';
COMMENT ON COLUMN market_insights.sentiment
    IS 'Overall market sentiment: Bullish, Bearish, or Neutral';


-- =====================================================
-- 2. DAILY BRIEFINGS
-- =====================================================
-- Source: HomeService._get_daily_briefings()
-- Configurable alert/briefing cards for the Home screen.

CREATE TABLE IF NOT EXISTS daily_briefings (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type       TEXT NOT NULL DEFAULT 'wiser_trending'
               CHECK (type IN ('whales_alert', 'earnings_alert',
                               'whales_following', 'wiser_trending')),
    title      TEXT NOT NULL,
    subtitle   TEXT NOT NULL,
    date       TIMESTAMPTZ,
    badge_text TEXT,
    is_active  BOOLEAN NOT NULL DEFAULT true,
    priority   INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_daily_briefings_active
    ON daily_briefings(is_active, priority DESC)
    WHERE is_active = true;

COMMENT ON TABLE daily_briefings
    IS 'Configurable briefing cards for the Home screen daily briefings section';
COMMENT ON COLUMN daily_briefings.badge_text
    IS 'Optional date badge, e.g. "24\nFEB" for earnings dates';
COMMENT ON COLUMN daily_briefings.priority
    IS 'Higher priority items appear first (DESC order)';


-- =====================================================
-- 3. RESEARCH REPORTS — ADD MISSING COLUMNS
-- =====================================================
-- The HomeService queries overall_score and fair_value_estimate
-- from research_reports for the RecentResearchResponse.
-- These columns were referenced by the backend but not in the
-- original schema. Add them safely with IF NOT EXISTS via DO block.

DO $$
BEGIN
    -- Add overall_score (0-100 quality rating)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'research_reports'
          AND column_name = 'overall_score'
    ) THEN
        ALTER TABLE research_reports
            ADD COLUMN overall_score NUMERIC
            CHECK (overall_score >= 0 AND overall_score <= 100);
        COMMENT ON COLUMN research_reports.overall_score
            IS 'AI-generated quality score 0-100 (used in home feed & report cards)';
    END IF;

    -- Add fair_value_estimate (estimated fair value per share)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'research_reports'
          AND column_name = 'fair_value_estimate'
    ) THEN
        ALTER TABLE research_reports
            ADD COLUMN fair_value_estimate NUMERIC;
        COMMENT ON COLUMN research_reports.fair_value_estimate
            IS 'Estimated fair value per share (used in home feed & report cards)';
    END IF;
END $$;

-- Index for the Home feed query: user's completed reports by date
CREATE INDEX IF NOT EXISTS idx_reports_user_completed
    ON research_reports(user_id, created_at DESC)
    WHERE status = 'completed';


-- =====================================================
-- SEED: Sample briefings (optional — remove in production)
-- =====================================================
-- Uncomment below to seed sample data for testing:
--
-- INSERT INTO daily_briefings (type, title, subtitle, priority, is_active) VALUES
--     ('whales_alert', 'Whales Alert', 'Large institutional investor moved $50M into AAPL', 10, true),
--     ('earnings_alert', 'Earnings Alert', 'NVDA reports earnings tomorrow after market close', 9, true),
--     ('whales_following', 'Whales Your Following', '3 hedge funds you follow bought GOOGL this week', 8, true),
--     ('wiser_trending', 'Wiser: Trending', 'How can I invest in AI companies?', 7, true)
-- ON CONFLICT DO NOTHING;


-- =====================================================
-- VERIFICATION
-- =====================================================

DO $$
DECLARE
    mi_exists BOOLEAN;
    db_exists BOOLEAN;
    os_exists BOOLEAN;
    fv_exists BOOLEAN;
BEGIN
    SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'market_insights') INTO mi_exists;
    SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'daily_briefings') INTO db_exists;
    SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'research_reports' AND column_name = 'overall_score') INTO os_exists;
    SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'research_reports' AND column_name = 'fair_value_estimate') INTO fv_exists;

    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Home Feed Migration — Results';
    RAISE NOTICE 'market_insights table:        %', CASE WHEN mi_exists THEN 'OK' ELSE 'MISSING' END;
    RAISE NOTICE 'daily_briefings table:        %', CASE WHEN db_exists THEN 'OK' ELSE 'MISSING' END;
    RAISE NOTICE 'research_reports.overall_score:      %', CASE WHEN os_exists THEN 'OK' ELSE 'MISSING' END;
    RAISE NOTICE 'research_reports.fair_value_estimate: %', CASE WHEN fv_exists THEN 'OK' ELSE 'MISSING' END;
    RAISE NOTICE '=================================================';
END $$;
