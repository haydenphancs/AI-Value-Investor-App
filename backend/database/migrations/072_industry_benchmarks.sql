-- 072_industry_benchmarks.sql
--
-- Why: benchmark comparisons ("vs sector average") move from SECTOR-level to
-- INDUSTRY-level (with a sector fallback), computed over a broader, small-cap-
-- inclusive universe. Rather than a parallel table, the existing
-- `sector_benchmarks` table gains an `industry` dimension:
--   industry = ''      → the SECTOR aggregate row (the fallback; same shape as before)
--   industry = <name>  → an INDUSTRY aggregate row, with `sector` = its parent sector
-- The lookup prefers the industry row for a (metric, period) and falls back to the
-- '' sector row when the industry is undersampled.
--
-- The UNIQUE key must include `industry` so an industry row and its parent sector
-- row (same sector/metric/period) can coexist. ORDER MATTERS: add the column with a
-- DEFAULT first (atomically backfills every existing row to industry=''), THEN swap
-- the constraint — existing data can't violate the new 5-col key because the old
-- 4-col key was already unique and all existing rows now share industry=''.
--
-- LOCKSTEP: the writers' upsert on_conflict string changes to
-- "sector,industry,metric_name,period_type,period_label" in the same deploy, or
-- upserts throw "no unique constraint matching the ON CONFLICT specification".
--
-- RLS: unchanged — this is the same table (public read, service_role write).

ALTER TABLE public.sector_benchmarks
    ADD COLUMN IF NOT EXISTS industry TEXT NOT NULL DEFAULT '';

-- Swap the 4-col unique for the 5-col unique (industry added).
ALTER TABLE public.sector_benchmarks
    DROP CONSTRAINT IF EXISTS uq_sector_metric_period;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_sector_industry_metric_period'
    ) THEN
        ALTER TABLE public.sector_benchmarks
            ADD CONSTRAINT uq_sector_industry_metric_period
            UNIQUE (sector, industry, metric_name, period_type, period_label);
    END IF;
END$$;

-- Hot path for the industry-first lookup: fetch fresh rows for an industry/metric.
CREATE INDEX IF NOT EXISTS idx_sector_benchmarks_industry_lookup
    ON public.sector_benchmarks (industry, metric_name, period_type);
