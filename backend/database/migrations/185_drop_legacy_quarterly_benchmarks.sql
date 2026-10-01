-- 185_drop_legacy_quarterly_benchmarks.sql
--
-- ⚠️ APPLY ONLY AFTER the first calendar-quarter recompute has finished and been checked
-- (documents/OWNER_TASKS.md, "Calendar-quarter peer benchmarks go-live"), with the code
-- that reads 'calendar_quarter' deployed. The guard below refuses to run before every
-- sector has its calendar-quarter rows, so applying it too early fails with an error and
-- changes nothing.
--
-- Why: migration 184 moved the quarterly sector/industry benchmarks to
-- period_type 'calendar_quarter' (keyed by the calendar quarter a period ENDS in). The
-- legacy period_type 'quarterly' rows were keyed by FISCAL quarter number + calendar year
-- of the period end, so for every off-calendar company (Microsoft, Apple, Nvidia, the
-- Jan-year-end retailers) they pooled, and were joined to, peer quarters 3-10 months
-- away. Once the calendar-quarter code is deployed nothing reads them
-- (sector_benchmark_lookup.CALENDAR_QUARTER_PERIOD_TYPE is the only quarterly period_type
-- a reader asks for; tests/test_calendar_quarter_benchmarks.py fails the build on a
-- 'quarterly' read), so they are dead weight — about as many rows as the replacement set.
--
-- DESTRUCTIVE: deletes every period_type = 'quarterly' row of sector_benchmarks. The data
-- lost is the fiscal-keyed quarterly medians, which are WRONG for off-calendar companies
-- and fully superseded by the 'calendar_quarter' rows. The deleted rows themselves can
-- only come back from a Supabase backup (or a code rollback plus a full recompute) — the
-- current code no longer knows the old key — so take a backup/dump first if you want to
-- keep them. 'annual', 'ttm' and 'calendar_quarter' rows are untouched. The CHECK still
-- allows 'quarterly' (184), so a code rollback can write them again.
--
-- Guard: the sector aggregate row (industry = '') is the LAST thing the recompute writes
-- for a sector, so a sector that has '' calendar_quarter rows finished its run. The DO
-- block raises — rolling the whole migration back — if any sector that holds legacy
-- '' quarterly rows has no '' calendar_quarter rows yet (a partial recompute, or one
-- blocked before 184 was applied).
--
-- Idempotent: a second run deletes nothing (and the guard passes: no legacy rows remain).
--
-- BEFORE applying, look at both sides (the guard enforces the second query):
--   SELECT period_type, count(*) FROM public.sector_benchmarks
--    WHERE period_type IN ('quarterly', 'calendar_quarter') GROUP BY period_type;
--     -- two rows, calendar_quarter close to quarterly. ONE row (no calendar_quarter) = not recomputed.
--   SELECT DISTINCT q.sector FROM public.sector_benchmarks q
--    WHERE q.period_type = 'quarterly' AND q.industry = ''
--      AND NOT EXISTS (SELECT 1 FROM public.sector_benchmarks c
--                       WHERE c.period_type = 'calendar_quarter' AND c.industry = ''
--                         AND c.sector = q.sector);
--     -- zero rows. Any sector listed has not finished its calendar-quarter recompute.
--
-- VERIFY (after applying):
--   SELECT count(*) FILTER (WHERE period_type = 'quarterly')        AS legacy_left,   -- 0
--          count(*) FILTER (WHERE period_type = 'calendar_quarter') AS calendar_rows  -- unchanged
--     FROM public.sector_benchmarks;

BEGIN;

DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(DISTINCT q.sector, ', ' ORDER BY q.sector)
      INTO missing
      FROM public.sector_benchmarks q
     WHERE q.period_type = 'quarterly'
       AND q.industry = ''
       AND NOT EXISTS (
           SELECT 1 FROM public.sector_benchmarks c
            WHERE c.period_type = 'calendar_quarter'
              AND c.industry = ''
              AND c.sector = q.sector
       );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'calendar-quarter recompute incomplete — no calendar_quarter sector rows for: %. '
            'Run the recompute (OWNER_TASKS) before deleting the legacy quarterly rows.', missing;
    END IF;
END$$;

-- DESTRUCTIVE: removes the superseded fiscal-keyed quarterly medians (see header).
DELETE FROM public.sector_benchmarks
 WHERE period_type = 'quarterly';

COMMIT;

-- Refresh planner statistics after removing about half of the table.
ANALYZE public.sector_benchmarks;
