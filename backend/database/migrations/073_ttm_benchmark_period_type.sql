-- 073_ttm_benchmark_period_type.sql
--
-- Why: the new TTM (trailing-twelve-month) current-snapshot benchmark rows are
-- written with period_type='ttm' (period_label='TTM') — one complete-rolling-12-
-- months median per metric per industry/sector, so the "vs industry/sector avg"
-- comparison never spikes on a partial fiscal year. But sector_benchmarks has a
-- CHECK constraint that only permitted 'annual'/'quarterly', so every TTM upsert
-- was rejected (Postgres error 23514, constraint sector_benchmarks_period_type_check).
--
-- Fix: widen the CHECK to also allow 'ttm'. The fiscal annual/quarterly rows are
-- untouched — TTM rows are ADDITIVE (the 5-col UNIQUE from #072 already keys on
-- period_type, so 'ttm'/'TTM' rows never collide with the fiscal series).
--
-- Idempotent: DROP IF EXISTS then re-ADD (safe to apply multiple times).

ALTER TABLE public.sector_benchmarks
    DROP CONSTRAINT IF EXISTS sector_benchmarks_period_type_check;

ALTER TABLE public.sector_benchmarks
    ADD CONSTRAINT sector_benchmarks_period_type_check
    CHECK (period_type = ANY (ARRAY['annual'::text, 'quarterly'::text, 'ttm'::text]));
