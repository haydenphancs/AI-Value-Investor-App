-- 209_earnings_filing_cache.sql
--
-- Why: the live earnings card's filing figures lived only in process memory, and a deploy took
-- them away. `earnings_filing_service` reads the company's own 8-K Item 2.02 press release
-- within minutes of its release, while FMP fills the same figures ~1-3 h later and an
-- after-close report can wait until the next morning. So INTC files on Wednesday at 16:02 ET,
-- FMP is still empty, a deploy lands on Thursday at 09:00, and the card goes back to "results
-- aren't in yet" and drops at 12:00 ET: the SEC watch only reads reports dated today, so nothing
-- read that filing again. The outlook and the "View the filing" link, which FMP never carries,
-- went on ANY restart once FMP had completed the report, the same day included, because a
-- complete report is off the SEC watch. Owner decision 2026-11-05: "Add the table".
--
-- One row per (symbol, report_date), written by `earnings_live_service` the moment a verified
-- filing is on its record, and read back once per process, by whichever live tick runs first,
-- for the live days only (`live_days`: the previous trading day through today, weekends and
-- holidays included). A restored filing is not read again, so a restart costs SEC and Gemini
-- nothing, and a restored figure is never a landing, so a restart announces nothing.
--
-- Holds ONLY what the company's own release said: each figure is the number printed in the SEC
-- filing, verified against a verbatim quote, and the outlook sentence is grounded the same way.
-- NEVER FMP data: no consensus, no FMP actual, no company name. Those come back from FMP.
--
-- No expires_at, on purpose. Most *_cache tables here carry none (they judge age from a stamp and
-- a constant), and this one has no age to judge: a row is read only while its report_date is a
-- live day, so it ages out by its own date, and a TTL could only lose a figure over a long
-- weekend. computed_at stays, because the sheet's "updated" time is restored from it.
--
-- No clean-up job, on purpose. The tables that have one are a different case: ticker_news_cache
-- holds licensed third-party news text, and news_sentiment_log, search_pick_daily and
-- social_mentions_history gain rows per article or per ticker every day. This one holds public
-- SEC facts and gains at most EARNINGS_LIVE_SEC_MAX_SYMBOLS (25) rows a day, and only for a
-- filing actually read: about 6,300 rows a year at the very worst, each under 1 KB. Rows past
-- the live days are simply never read.
--
-- RLS is service-role ONLY. SEC data is public domain, but every cache table has been
-- service-role-only since 164: the iOS app has no Supabase client, every reader is the backend's
-- service_role, and an anon/authenticated grant is a second door nobody needs (database.md).
-- Same posture as 202.
--
-- Primary key (symbol, report_date) and no serial column, so there is no sequence to grant. The
-- index is for the one read, which filters on report_date alone.
--
-- Deploy order does not matter. Both calls are best effort: before this is applied the tick logs
-- "[earnings-live] filing restore failed" (WARNING, a retry every 2 minutes), each filing logs
-- "filing not kept" (WARNING), and a restart behaves as it did before this table.
--
-- Idempotent: IF NOT EXISTS / DROP POLICY IF EXISTS throughout; REVOKE, GRANT and COMMENT are
-- re-runnable.
--
-- AFTER APPLYING, in ONE change: re-run scripts/dump_schema.sh; remove the table from
-- `_PENDING_MIGRATION_TABLES` in tests/test_schema_doc_generator.py; hand-edit EXPECTED in
-- backend/scripts/generate_schema_doc.py (tables, public, rls and policies each +1, plus
-- anything else the re-dump picked up); re-run scripts/generate_schema_doc.py; and, once the
-- design doc names this table, add it to `_CURATED_TABLES` in
-- tests/test_system_design_doc_parity.py. Those tests fail until all of that agrees, which is
-- the intended guard.
--
-- VERIFY (read-only) right after applying → one row: t | f | f | t
-- SELECT
--   c.relrowsecurity,
--   has_table_privilege('anon', c.oid, 'SELECT'),
--   has_table_privilege('authenticated', c.oid, 'SELECT'),
--   has_table_privilege('service_role', c.oid, 'SELECT, INSERT, UPDATE')
-- FROM pg_class c WHERE c.oid = 'public.earnings_filing_cache'::regclass;

CREATE TABLE IF NOT EXISTS public.earnings_filing_cache (
    symbol            TEXT             NOT NULL,
    report_date       DATE             NOT NULL,
    accession         TEXT             NOT NULL,
    accepted_at       TIMESTAMPTZ,
    url               TEXT,
    revenue           DOUBLE PRECISION,
    eps_adjusted      DOUBLE PRECISION,
    eps_gaap          DOUBLE PRECISION,
    fiscal_period     TEXT,
    guidance          TEXT,
    revenue_rounding  DOUBLE PRECISION,
    eps_rounding      DOUBLE PRECISION,
    computed_at       TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, report_date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_filing_cache_report_date
    ON public.earnings_filing_cache (report_date);

COMMENT ON TABLE public.earnings_filing_cache IS
    'What each company''s own 8-K Item 2.02 earnings release said, for the live earnings card: '
    'FMP can take until the next morning to fill the same figures, and FMP never carries the '
    'outlook or the filing link. One row per (symbol, report date), written when a verified '
    'filing reaches its record and read back once per process for the live days, so a restart '
    'neither loses a figure nor reads the filing again. Figures from SEC filings only, never '
    'FMP data. report_date is its TTL: no expires_at and no clean-up job. service_role only. '
    'Written and read only by app/services/earnings_live_service.py.';

COMMENT ON COLUMN public.earnings_filing_cache.symbol IS
    'The ticker, upper case, as the live records key it.';
COMMENT ON COLUMN public.earnings_filing_cache.report_date IS
    'The report''s date on the earnings calendar: the ET day whose record holds this filing.';
COMMENT ON COLUMN public.earnings_filing_cache.accession IS
    'SEC accession number of the 8-K, e.g. 0000050863-26-000123. A restored accession is not '
    'read again.';
COMMENT ON COLUMN public.earnings_filing_cache.accepted_at IS
    'When EDGAR accepted the filing. NULL when SEC gave no readable time.';
COMMENT ON COLUMN public.earnings_filing_cache.url IS
    'The filing''s index page on sec.gov, behind "View the filing".';
COMMENT ON COLUMN public.earnings_filing_cache.revenue IS
    'Quarterly revenue in dollars, the number printed in the release. NULL when none verified.';
COMMENT ON COLUMN public.earnings_filing_cache.eps_adjusted IS
    'Adjusted (non-GAAP) diluted EPS as printed. NULL when none verified.';
COMMENT ON COLUMN public.earnings_filing_cache.eps_gaap IS
    'GAAP diluted EPS as printed. NULL when none verified.';
COMMENT ON COLUMN public.earnings_filing_cache.fiscal_period IS
    'The quarter as the release names it, e.g. Q3 2026.';
COMMENT ON COLUMN public.earnings_filing_cache.guidance IS
    'One sentence of the company''s own outlook, grounded in a verbatim quote.';
COMMENT ON COLUMN public.earnings_filing_cache.revenue_rounding IS
    'Half the last digit revenue was printed to, in dollars: $17.1 billion is any figure within '
    '$50M, so a consensus inside that band is neither beaten nor missed.';
COMMENT ON COLUMN public.earnings_filing_cache.eps_rounding IS
    'Half the last digit adjusted EPS was printed to, in dollars. Same rule as revenue_rounding.';
COMMENT ON COLUMN public.earnings_filing_cache.computed_at IS
    'The moment the figures reached their record. The sheet''s "updated" time comes back from it.';

ALTER TABLE public.earnings_filing_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "earnings_filing_cache_service_all" ON public.earnings_filing_cache;
CREATE POLICY "earnings_filing_cache_service_all" ON public.earnings_filing_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.earnings_filing_cache FROM anon, authenticated;
GRANT ALL ON public.earnings_filing_cache TO service_role;
