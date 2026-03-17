-- 011_sector_benchmarks_formalize.sql
-- Formalize the sector_benchmarks table (originally created dynamically by Python service)
-- and add indexes for the lookup query pattern used by SectorBenchmarkLookup.

CREATE TABLE IF NOT EXISTS public.sector_benchmarks (
    id bigint generated always as identity primary key,
    sector text not null,
    metric_name text not null,
    period_type text not null,        -- 'annual' or 'quarterly'
    period_label text not null,       -- '2024' or "Q1'24"
    median_value double precision,
    sample_size integer,
    computed_at timestamptz,
    UNIQUE (sector, metric_name, period_type, period_label)
);

-- Composite index for the lookup query pattern (sector + period_type + metric_name)
CREATE INDEX IF NOT EXISTS idx_sector_benchmarks_lookup
    ON public.sector_benchmarks (sector, period_type, metric_name);

-- Row Level Security
ALTER TABLE public.sector_benchmarks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public read access"
    ON public.sector_benchmarks FOR SELECT
    USING (true);

CREATE POLICY "Allow service_role full access"
    ON public.sector_benchmarks FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Grants (match pattern from 010_ticker_news_cache_grants.sql)
GRANT ALL ON TABLE public.sector_benchmarks TO service_role;
GRANT SELECT ON TABLE public.sector_benchmarks TO anon;
GRANT SELECT ON TABLE public.sector_benchmarks TO authenticated;
