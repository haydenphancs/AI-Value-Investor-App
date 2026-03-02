-- =====================================================
-- Caydex - Multi-Agent Deep Research Migration
-- Adds ticker_report_data JSONB column + cache index
-- Date: March 1, 2026
-- =====================================================
--
-- Run AFTER supabase_schema.sql and migration_home_feed.sql
-- Safe to re-run (uses IF NOT EXISTS / DO blocks)
--
-- Adds:
-- 1. ticker_report_data JSONB column on research_reports
--    Stores the full TickerReportResponse JSON so the iOS app
--    can display it in TickerReportView without regenerating.
-- 2. Cache lookup index for GET /stocks/{ticker}/report endpoint
--    Enables instant cache hits when a recent research report exists.
-- =====================================================


-- =====================================================
-- 1. ADD ticker_report_data COLUMN
-- =====================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'research_reports'
          AND column_name = 'ticker_report_data'
    ) THEN
        ALTER TABLE research_reports
            ADD COLUMN ticker_report_data JSONB;
        COMMENT ON COLUMN research_reports.ticker_report_data
            IS 'Full TickerReportResponse JSON from multi-agent research. '
               'Matches the exact schema expected by iOS TickerReportView.';
    END IF;
END $$;


-- =====================================================
-- 2. CACHE LOOKUP INDEX
-- =====================================================
-- Used by GET /stocks/{ticker}/report to find recent
-- completed research reports and serve them from cache.
-- Query pattern: WHERE ticker = X AND investor_persona = Y
--   AND status = 'completed' AND completed_at >= cutoff
--   ORDER BY completed_at DESC LIMIT 1

CREATE INDEX IF NOT EXISTS idx_research_reports_cache
    ON research_reports(ticker, investor_persona, completed_at DESC)
    WHERE status = 'completed'
      AND ticker_report_data IS NOT NULL;

COMMENT ON INDEX idx_research_reports_cache
    IS 'Partial index for ticker report cache lookups. '
       'Covers completed reports with stored TickerReportResponse data.';


-- =====================================================
-- VERIFICATION
-- =====================================================

DO $$
DECLARE
    trd_exists BOOLEAN;
    idx_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'research_reports'
          AND column_name = 'ticker_report_data'
    ) INTO trd_exists;

    SELECT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_research_reports_cache'
    ) INTO idx_exists;

    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Multi-Agent Research Migration — Results';
    RAISE NOTICE 'research_reports.ticker_report_data: %', CASE WHEN trd_exists THEN 'OK' ELSE 'MISSING' END;
    RAISE NOTICE 'idx_research_reports_cache index:     %', CASE WHEN idx_exists THEN 'OK' ELSE 'MISSING' END;
    RAISE NOTICE '=================================================';
END $$;
