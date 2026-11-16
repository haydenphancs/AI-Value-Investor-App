-- cleanup_sector_benchmarks_orphans_2026_11_15.sql
--
-- One-off DATA cleanup of public.sector_benchmarks (not a migration: no DDL on the live table).
-- Run by the OWNER in Supabase Studio → SQL Editor, one numbered block at a time, reading each
-- result before the next. Claude never runs it.
--
-- ═══ WHY ═══
-- The median producer (`industry_benchmark_service`) is UPSERT-ONLY and writes a cell only when
-- it pools MIN_SAMPLE_SIZE (5) companies. A cell it can no longer produce is never rewritten and
-- never deleted, so it keeps whatever an older run wrote. After the 2026-11-15 recompute
-- (fiscal 18:35-18:53 UTC, 340,409 rows; weekly TTM 06:xx UTC, 2,252 rows) the table still held
-- 39,732 rows those runs did not write, all from older code and the OLD universe:
--   * 2026-10-04: the June universe (funds, ETFs, Toronto listings) and pre-10-11 margin,
--     pre-10-21 FCF zero-fill and pre-11-06 SG&A blank rules;
--   * 2026-10-21: the US-only universe but pre-11-06 SG&A rule (zeros counted as figures);
--   * a handful from 06-24 / 07-05 and stale weekly TTM rows (08-09 .. 11-08).
-- Measured read-only on 2026-11-15 from a full copy of the table (382,393 rows):
--
--   D1  dead peer groups — no row from either 11-15 run; the universe no longer produces them:
--         Asset Management - Income (FS) 2,484 fiscal · Asset Management - Bonds (FS) 1,299 ·
--         Construction (Industrials) 2,768 · Real Estate - Development stored under sector
--         'Financial Services' 2,603 (its live series is under 'Real Estate') · Banks (FS) 1,831 ·
--         Software - Services 1,118 · Pollution & Treatment Controls 845 · Consumer Electronics 786 ·
--         Financial - Conglomerates 74 · + 136 TTM rows of these groups          = 13,944
--   D2  live groups, fiscal (annual + calendar_quarter) cells the 11-15 run did not rewrite:
--         a cell that fell under 5 companies (SG&A blanks dropped, FCF zero-fills dropped, the
--         US-only universe), a metric now gated off for that industry, or a period the US
--         universe cannot fill (mostly Q3'06 and older, 2009-2010)                 = 25,645
--   D3  live groups, TTM rows the 11-15 weekly run did not rewrite (38 already hidden by the
--         21-day rule; 9 are n=5 cells from 11-01 / 11-08 that no reader uses)   =     47
--                                                                       TOTAL = 39,636
--   KEPT on purpose (not deleted):
--   K1  the Utilities SECTOR sga_to_revenue series (96 rows, 2026-10-21; 2024/2025 = 0.0).
--       Deliberate (owner decision 2026-11-06, memory project_sga_blank_line): the moat scorer
--       reads a 0.0 median as "no peer" and STOPS there, so these rows also keep it from
--       reaching back to the pooled pre-rule 2016-2023 values (0.44-1.42). SG&A is gated off
--       for utilities anyway (`financials_metric_gate.sga_line_applicable`).
--   K2  Financial Services / REIT - Mortgage annual cells the floor watch keeps on purpose
--       (`industry_benchmark_service._PeerFloorWatch`: a newest-3-year cell with n >= 10 is
--       never dropped on one run's word). NONE existed on 2026-11-15; the clause is a guard.
--
-- Of the 221 exact-zero SG&A medians, 167 are deleted (D1-D3) and the 54 of K1 stay.
--
-- ═══ WHAT CHANGES ON SCREEN (simulated with the readers' rules on the measured copy) ═══
--   * No card value and no moat median changes for any company in the 5,554-ticker industry
--     universe, and no reader reaches back to an older year because a row went away.
--   * Tickers whose FMP industry is Asset Management - Income or - Bonds (closed-end and bond
--     funds; no such ticker in reports or watchlists on 11-15) lose their peer values: 10-04
--     fund medians served under the Financial Services rule (n up to 66). They then show no peer,
--     like every other FS industry under 10 companies.
--   * 10 FS peer lines drawn only from stale points disappear (Financial - Mortgages margins /
--     asset turnover, three insurers' SG&A quarters, one Investment Banking P/FCF quarter).
--   * About 3,400 peer lines lose old LEFT-EDGE points (mostly Q3'06 and earlier, 2009-2010),
--     which came from a different population (the June universe).
--   Served values reach the app within an hour (the lookup's in-process cache); frozen report
--   collections keep their peer lines until they re-collect.
--
-- ═══ WHEN ═══
-- Any time EXCEPT while a benchmark job runs: not Sunday 02:00-08:00 UTC (weekly TTM), not
-- during the quarterly chain (first Sunday of Jan/Apr/Jul/Oct) or a manual recompute. Block 3
-- refuses to delete a row that changed after block 2 (the backup).
--
-- ═══ BACKUP ═══
-- Block 2 copies every row to be deleted into public.sector_benchmarks_orphans_20261115 (RLS on,
-- no anon/authenticated access). Block 5 restores from it. Drop it with block 6 after a week,
-- and ALWAYS before the next `scripts/dump_schema.sh` (the schema atlas and the grants tests
-- fail on an uncurated public table).


-- ─── 0. Preconditions — read only. Do not continue unless both answers match. ───────────────

-- 0a. The 11-15 runs are in the table. Expect: annual + calendar_quarter >= 340,409 (exactly
--     340,409 until the next fiscal run), ttm >= 2,252.
SELECT period_type, count(*) AS rows_written_by_the_2026_11_15_runs
FROM public.sector_benchmarks
WHERE (period_type = 'ttm' AND computed_at >= timestamptz '2026-11-15 05:00:00+00')
   OR (period_type IN ('annual', 'calendar_quarter')
       AND computed_at >= timestamptz '2026-11-15 18:30:00+00')
GROUP BY period_type
ORDER BY period_type;

-- 0b. Every peer group the 11-15 runs did NOT write is one of the nine reviewed dead groups.
--     Expect: ZERO rows. A row here is a group a run skipped (a failure?) — stop and look.
WITH dead(sector, industry) AS (VALUES
    ('Financial Services', 'Asset Management - Bonds'),
    ('Financial Services', 'Asset Management - Income'),
    ('Financial Services', 'Banks'),
    ('Financial Services', 'Financial - Conglomerates'),
    ('Financial Services', 'Real Estate - Development'),
    ('Industrials', 'Construction'),
    ('Industrials', 'Industrial - Pollution & Treatment Controls'),
    ('Technology', 'Consumer Electronics'),
    ('Technology', 'Software - Services')
),
live AS (
    SELECT DISTINCT sector, industry
    FROM public.sector_benchmarks
    WHERE (period_type = 'ttm' AND computed_at >= timestamptz '2026-11-15 05:00:00+00')
       OR (period_type IN ('annual', 'calendar_quarter')
           AND computed_at >= timestamptz '2026-11-15 18:30:00+00')
)
SELECT s.sector, s.industry, count(*) AS rows, max(s.computed_at) AS newest
FROM public.sector_benchmarks s
WHERE (s.sector, s.industry) NOT IN (SELECT sector, industry FROM live)
  AND (s.sector, s.industry) NOT IN (SELECT sector, industry FROM dead)
GROUP BY s.sector, s.industry
ORDER BY s.sector, s.industry;


-- ─── 1. Inventory — read only, re-runnable. Expected answers as measured on 2026-11-15. ──────

-- 1a. Exact-zero SG&A medians. Expect 221 in all: 54 Utilities sector (K1), 120 in the
--     gated insurer / utility industries, 47 elsewhere (Railroads 19 quarters, Grocery Stores 9,
--     Medical - Healthcare Plans 11, Asset Management - Income 4, FS sector 2, REIT - Mortgage 2).
SELECT
    CASE
        WHEN sector = 'Utilities' AND industry = '' THEN 'K1 Utilities sector (kept)'
        WHEN lower(industry) IN (
            'insurance - brokers', 'insurance - diversified', 'insurance - life',
            'insurance - property & casualty', 'insurance - reinsurance', 'insurance - specialty',
            'diversified utilities', 'independent power producers', 'regulated electric',
            'regulated gas', 'regulated water', 'renewable utilities')
          OR lower(sector) = 'utilities' THEN 'gated insurer / utility industry'
        ELSE 'not gated'
    END AS bucket,
    sector, industry, period_type, count(*) AS rows,
    (min(computed_at) AT TIME ZONE 'UTC')::date AS oldest, (max(computed_at) AT TIME ZONE 'UTC')::date AS newest
FROM public.sector_benchmarks
WHERE metric_name = 'sga_to_revenue' AND median_value = 0
GROUP BY 1, sector, industry, period_type
ORDER BY 1, sector, industry, period_type;

-- 1b. The Utilities sector SG&A series (K1). Expect 96 rows (80 calendar_quarter + 16 annual),
--     all computed 2026-10-21; annual 2024 and 2025 = 0.000000.
SELECT period_type, period_label, median_value, sample_size, computed_at
FROM public.sector_benchmarks
WHERE sector = 'Utilities' AND industry = '' AND metric_name = 'sga_to_revenue'
ORDER BY period_type, period_label;

-- 1c. Real Estate - Development: the stale 'Financial Services' series beside the live
--     'Real Estate' one. The reader matches the industry NAME only and the last id wins, so the
--     FS rows (ids 200,483-994,727) fill the 1,844 fiscal cells the live series (ids from
--     1,172,726) does not hold. Expect FS 2,620 rows (10-04), Real Estate 775.
SELECT sector, period_type, count(*) AS rows, min(id) AS min_id, max(id) AS max_id,
       (min(computed_at) AT TIME ZONE 'UTC')::date AS oldest, (max(computed_at) AT TIME ZONE 'UTC')::date AS newest
FROM public.sector_benchmarks
WHERE industry = 'Real Estate - Development'
GROUP BY sector, period_type
ORDER BY sector, period_type;

-- 1d. The rows this file deletes, by category. Expect D1 13,944 · D2 25,645 · D3 47 = 39,636.
--     (The same predicate as block 2; keep the two identical if you edit either.)
WITH dead(sector, industry) AS (VALUES
    ('Financial Services', 'Asset Management - Bonds'),
    ('Financial Services', 'Asset Management - Income'),
    ('Financial Services', 'Banks'),
    ('Financial Services', 'Financial - Conglomerates'),
    ('Financial Services', 'Real Estate - Development'),
    ('Industrials', 'Construction'),
    ('Industrials', 'Industrial - Pollution & Treatment Controls'),
    ('Technology', 'Consumer Electronics'),
    ('Technology', 'Software - Services')
),
live AS (
    SELECT DISTINCT sector, industry
    FROM public.sector_benchmarks
    WHERE (period_type = 'ttm' AND computed_at >= timestamptz '2026-11-15 05:00:00+00')
       OR (period_type IN ('annual', 'calendar_quarter')
           AND computed_at >= timestamptz '2026-11-15 18:30:00+00')
),
orphans AS (
    SELECT s.*,
           CASE
               WHEN (s.sector, s.industry) IN (SELECT sector, industry FROM dead) THEN 'D1 dead group'
               WHEN s.period_type = 'ttm' THEN 'D3 live group, ttm'
               ELSE 'D2 live group, fiscal'
           END AS category
    FROM public.sector_benchmarks s
    WHERE s.computed_at < CASE WHEN s.period_type = 'ttm'
                               THEN timestamptz '2026-11-15 05:00:00+00'
                               ELSE timestamptz '2026-11-15 18:30:00+00' END
      -- K1: the deliberate Utilities sector SG&A series stays.
      AND NOT (s.sector = 'Utilities' AND s.industry = '' AND s.metric_name = 'sga_to_revenue')
      AND (
          (s.sector, s.industry) IN (SELECT sector, industry FROM dead)
          OR (
              (s.sector, s.industry) IN (SELECT sector, industry FROM live)
              -- K2: a floor-watch cell (FS / REIT - Mortgage, newest 3 years, n >= 10) stays.
              AND NOT (
                  s.period_type = 'annual' AND s.sample_size >= 10
                  AND s.period_label IN ('2023', '2024', '2025')
                  AND ((s.sector = 'Financial Services' AND s.industry <> '')
                       OR lower(s.industry) = 'reit - mortgage')
              )
          )
      )
)
SELECT category, period_type, count(*) AS rows,
       count(*) FILTER (WHERE metric_name = 'sga_to_revenue' AND median_value = 0) AS zero_sga,
       (min(computed_at) AT TIME ZONE 'UTC')::date AS oldest, (max(computed_at) AT TIME ZONE 'UTC')::date AS newest
FROM orphans
GROUP BY ROLLUP (category, period_type)
ORDER BY category NULLS LAST, period_type NULLS LAST;


-- ─── 2. Backup — copies the rows to be deleted. Nothing is deleted yet. ─────────────────────
-- Expect the last SELECT to answer 39,636 (and 167 zero-SG&A rows). Any other number: stop.
CREATE TABLE IF NOT EXISTS public.sector_benchmarks_orphans_20261115 AS
WITH dead(sector, industry) AS (VALUES
    ('Financial Services', 'Asset Management - Bonds'),
    ('Financial Services', 'Asset Management - Income'),
    ('Financial Services', 'Banks'),
    ('Financial Services', 'Financial - Conglomerates'),
    ('Financial Services', 'Real Estate - Development'),
    ('Industrials', 'Construction'),
    ('Industrials', 'Industrial - Pollution & Treatment Controls'),
    ('Technology', 'Consumer Electronics'),
    ('Technology', 'Software - Services')
),
live AS (
    SELECT DISTINCT sector, industry
    FROM public.sector_benchmarks
    WHERE (period_type = 'ttm' AND computed_at >= timestamptz '2026-11-15 05:00:00+00')
       OR (period_type IN ('annual', 'calendar_quarter')
           AND computed_at >= timestamptz '2026-11-15 18:30:00+00')
)
SELECT s.*
FROM public.sector_benchmarks s
WHERE s.computed_at < CASE WHEN s.period_type = 'ttm'
                           THEN timestamptz '2026-11-15 05:00:00+00'
                           ELSE timestamptz '2026-11-15 18:30:00+00' END
  AND NOT (s.sector = 'Utilities' AND s.industry = '' AND s.metric_name = 'sga_to_revenue')
  AND (
      (s.sector, s.industry) IN (SELECT sector, industry FROM dead)
      OR (
          (s.sector, s.industry) IN (SELECT sector, industry FROM live)
          AND NOT (
              s.period_type = 'annual' AND s.sample_size >= 10
              AND s.period_label IN ('2023', '2024', '2025')
              AND ((s.sector = 'Financial Services' AND s.industry <> '')
                   OR lower(s.industry) = 'reit - mortgage')
          )
      )
  );

ALTER TABLE public.sector_benchmarks_orphans_20261115 ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sector_benchmarks_orphans_20261115 FROM anon, authenticated;
COMMENT ON TABLE public.sector_benchmarks_orphans_20261115 IS
    'TEMPORARY backup of the sector_benchmarks orphan rows removed on the 2026-11-15 cleanup '
    '(backend/database/queries/cleanup_sector_benchmarks_orphans_2026_11_15.sql). Drop after a '
    'week and before the next scripts/dump_schema.sh.';

SELECT count(*) AS backed_up,
       count(*) FILTER (WHERE metric_name = 'sga_to_revenue' AND median_value = 0) AS zero_sga
FROM public.sector_benchmarks_orphans_20261115;


-- ─── 3. Delete. ─────────────────────────────────────────────────────────────────────────────
-- DESTRUCTIVE: deletes the 39,636 orphan rows listed by blocks 1d / 2 (old-universe and
-- pre-rule peer medians that no recompute rewrites). Their only copy afterwards is the block-2
-- backup table, so run block 2 first and check its count. A row is deleted only if it is still
-- exactly the row that was backed up (same id AND same computed_at); if any row changed since
-- block 2 (a run rewrote it), the counts differ and the whole block rolls back — drop the
-- backup (block 6) and start again at block 0.
DO $$
DECLARE
    v_backup  bigint;
    v_deleted bigint;
BEGIN
    SELECT count(*) INTO v_backup FROM public.sector_benchmarks_orphans_20261115;
    DELETE FROM public.sector_benchmarks s
    USING public.sector_benchmarks_orphans_20261115 b
    WHERE s.id = b.id
      AND s.computed_at = b.computed_at;
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    IF v_deleted <> v_backup THEN
        RAISE EXCEPTION 'deleted % row(s) but the backup holds % — rolled back, nothing deleted',
            v_deleted, v_backup;
    END IF;
    RAISE NOTICE 'deleted % orphan row(s) from sector_benchmarks', v_deleted;
END
$$;


-- ─── 4. Verify — read only. ─────────────────────────────────────────────────────────────────
-- 4a. Re-run block 1d: expect NO rows (only the empty ROLLUP total, or nothing).
-- 4b. Zero SG&A medians left: expect 54, all sector 'Utilities' / industry '' (K1).
SELECT sector, industry, count(*) AS zero_sga_left
FROM public.sector_benchmarks
WHERE metric_name = 'sga_to_revenue' AND median_value = 0
GROUP BY sector, industry;
-- 4c. Rows older than the 11-15 runs: expect 96, all the Utilities SG&A series (K1).
SELECT sector, industry, metric_name, count(*) AS stale_rows_left
FROM public.sector_benchmarks
WHERE computed_at < CASE WHEN period_type = 'ttm'
                         THEN timestamptz '2026-11-15 05:00:00+00'
                         ELSE timestamptz '2026-11-15 18:30:00+00' END
GROUP BY sector, industry, metric_name;


-- ─── 5. Restore — ONLY if something has to come back. ───────────────────────────────────────
-- Puts every backed-up row back with its old id. A cell a later run has written since keeps the
-- newer row (ON CONFLICT on the unique key). `id` is GENERATED ALWAYS, hence OVERRIDING.
-- INSERT INTO public.sector_benchmarks
--     (id, sector, metric_name, period_type, period_label, median_value, sample_size,
--      computed_at, industry)
-- OVERRIDING SYSTEM VALUE
-- SELECT id, sector, metric_name, period_type, period_label, median_value, sample_size,
--        computed_at, industry
-- FROM public.sector_benchmarks_orphans_20261115
-- ON CONFLICT (sector, industry, metric_name, period_type, period_label) DO NOTHING;


-- ─── 6. Drop the backup — after a week, and before the next scripts/dump_schema.sh. ─────────
-- DESTRUCTIVE: drops the only copy of the deleted orphan rows. Run it once nothing needed
-- restoring.
-- DROP TABLE IF EXISTS public.sector_benchmarks_orphans_20261115;
