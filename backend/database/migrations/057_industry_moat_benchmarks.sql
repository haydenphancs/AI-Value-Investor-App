-- 057_industry_moat_benchmarks.sql
--
-- Why: the iOS Moat radar's gray "Peer Avg" pentagon collapses to a
-- flat 5.0 across every pillar for every ticker. Investigation in
-- May 2026 confirmed `peer_score` is hardcoded to 5.0 in all three
-- fallback tiers (deterministic, grounded, ai_legacy) because the
-- 5.0 was an intentional sector-median anchor — never a real peer
-- aggregate. This table holds per-industry peer averages per moat
-- pillar so the per-report path can overlay a real value with a
-- single Supabase read (no per-request FMP cost).
--
-- Computation: `industry_moat_benchmark_service.recompute_all()`
-- iterates every industry in `data/industry_universe.json`, runs
-- the existing `score_moat_dimensions` scorer for each constituent
-- ticker (capped at top 200 by mkt cap per industry), winsorizes
-- per-pillar scores at p10/p90, and averages. Chained quarterly
-- with the existing sector benchmark / competitor intel jobs.
--
-- Lookup: `industry_moat_benchmark_lookup.get_pillar_benchmarks(industry)`
-- returns `{pillar_name: peer_average_score}`. Falls back to {} when
-- the industry has no rows yet (new ticker or pre-bootstrap state) —
-- caller leaves `_apply_peer_score_baseline`'s 5.0 floor in place.
--
-- Shape: one row per (industry, pillar_name). 156 industries × 5
-- pillars = ~780 rows steady-state.
--
-- Idempotent — CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- DROP POLICY IF EXISTS before CREATE POLICY. Replayable.

BEGIN;

CREATE TABLE IF NOT EXISTS public.industry_moat_benchmarks (
    id BIGSERIAL PRIMARY KEY,
    industry TEXT NOT NULL,
    pillar_name TEXT NOT NULL,                      -- "Switching Costs" | "Network Effects" | "Brand Power" | "Cost Advantage" | "Intangible Assets"
    peer_average_score NUMERIC(3,1) NOT NULL,       -- winsorized mean of per-peer pillar scores, 0.0-10.0
    sample_size INT NOT NULL,                       -- peers with a resolved pillar score (post-winsorize)
    score_p25 NUMERIC(3,1),                         -- 25th percentile (for future "quartile band" UI)
    score_p75 NUMERIC(3,1),                         -- 75th percentile
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_version TEXT,                             -- e.g. "moat_v1.2026-05" — bump on scorer logic changes so consumers can detect drift
    UNIQUE (industry, pillar_name)
);

CREATE INDEX IF NOT EXISTS idx_industry_moat_benchmarks_lookup
    ON public.industry_moat_benchmarks (industry);

ALTER TABLE public.industry_moat_benchmarks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "industry_moat_benchmarks_service_write" ON public.industry_moat_benchmarks;
DROP POLICY IF EXISTS "industry_moat_benchmarks_public_read"   ON public.industry_moat_benchmarks;

CREATE POLICY "industry_moat_benchmarks_service_write" ON public.industry_moat_benchmarks
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "industry_moat_benchmarks_public_read" ON public.industry_moat_benchmarks
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.industry_moat_benchmarks TO anon, authenticated;
GRANT ALL    ON public.industry_moat_benchmarks TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.industry_moat_benchmarks_id_seq TO service_role;

COMMIT;
