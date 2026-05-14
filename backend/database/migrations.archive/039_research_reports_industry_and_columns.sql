-- 039_research_reports_industry_and_columns.sql
--
-- Reports tab end-to-end wiring:
--   1. Adds the `industry` column so the Reports list card can render
--      "TICKER • Industry" (e.g. "TSLA • Automotive") instead of an
--      empty trailing bullet.
--   2. Idempotently ensures `ticker_report_data`, `overall_score`, and
--      `fair_value_estimate` columns exist. These are written by
--      research_service.py and read by ticker_report.py's cache-aside
--      path; they were added by an out-of-tree migration in production
--      and are restated here so a fresh environment matches.
--   3. Adds an index that backs the cache-aside lookup in
--      ticker_report.py:_check_report_cache (filter by ticker +
--      investor_persona on completed reports, ordered by completed_at).
--
-- Safe to re-run: every statement is `IF NOT EXISTS`.

ALTER TABLE public.research_reports
    ADD COLUMN IF NOT EXISTS industry TEXT,
    ADD COLUMN IF NOT EXISTS ticker_report_data JSONB,
    ADD COLUMN IF NOT EXISTS overall_score NUMERIC,
    ADD COLUMN IF NOT EXISTS fair_value_estimate NUMERIC;

CREATE INDEX IF NOT EXISTS idx_reports_ticker_persona_completed
    ON public.research_reports (ticker, investor_persona, completed_at DESC)
    WHERE status = 'completed';
