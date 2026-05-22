-- 050_industry_dossier_cache.sql
--
-- Why: today the TickerReport "Moat & Competition" section calls FRED +
-- Census live from ticker_report_data_collector._fetch_dependent — two
-- upstream API hits per report. With a growing user base this is fragile
-- (rate-limit pressure) and slow (network latency on the critical path).
--
-- This cache table holds pre-computed TAM / CAGR / Lifecycle /
-- Concentration data for every FMP industry our users will encounter.
-- A weekly Sunday 2 AM job (industry_dossier_service.recompute_all)
-- refreshes the entire table.
--
-- Coverage guarantee: every discovered industry (~100-150 distinct FMP
-- strings, enumerated by backend/scripts/discover_industries.py from
-- S&P 500 + Nasdaq + Dow constituents) gets a row. When industry-precise
-- data isn't available (NAICS isn't mapped, FRED series doesn't cover
-- it), the compute job falls back through a 3-tier chain:
--
--   1. source_grain='industry'    — Census 4-digit NAICS or industry-
--                                   specific FRED series (most precise).
--   2. source_grain='sector'      — sector-level FRED series (e.g., the
--                                   broader Information sector for any
--                                   software industry that doesn't have
--                                   its own NAICS mapping).
--   3. source_grain='all_industry'— USNGSP (US all-industry total) as
--                                   the ultimate floor.
--
-- iOS reads `source_grain` and renders a "⚠ Broader than industry"
-- chip when it isn't 'industry', so users know the figure is a proxy.
--
-- Idempotent — CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- DROP POLICY IF EXISTS before each CREATE POLICY. Replayable.

BEGIN;

CREATE TABLE IF NOT EXISTS public.industry_dossier (
    id BIGSERIAL PRIMARY KEY,
    industry TEXT NOT NULL,                              -- exact FMP industry string ("Software - Infrastructure")
    sector TEXT NOT NULL,                                -- canonical app sector ("Technology")
    -- TAM (billions USD)
    current_tam_b NUMERIC(14, 2),
    future_tam_b NUMERIC(14, 2),
    current_year TEXT,
    future_year TEXT,
    cagr_5y_pct NUMERIC(8, 4),                           -- realized 5y CAGR, percent (e.g. 12.5 = 12.5%)
    lifecycle_phase TEXT NOT NULL DEFAULT 'mature'
        CHECK (lifecycle_phase IN ('emerging','secular_growth','mature','declining')),
    -- Concentration (computed from S&P 500 constituents in this industry)
    hhi NUMERIC(10, 2),                                  -- Herfindahl 0..10000
    top1_share_pct NUMERIC(6, 2),
    top2_share_pct NUMERIC(6, 2),
    concentration_label TEXT
        CHECK (concentration_label IN ('monopoly','duopoly','oligopoly','fragmented')),
    constituent_count INTEGER,                           -- # of companies used for HHI (audit aid)
    -- Source attribution
    source_grain TEXT NOT NULL
        CHECK (source_grain IN ('industry','sector','all_industry')),
    source_label TEXT NOT NULL,                          -- e.g. "US Census AIES — Software publishers (NAICS 5112)"
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '8 days'),
    UNIQUE(industry)
);

CREATE INDEX IF NOT EXISTS idx_industry_dossier_sector
    ON public.industry_dossier(sector);

CREATE INDEX IF NOT EXISTS idx_industry_dossier_computed_at
    ON public.industry_dossier(computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_industry_dossier_lookup
    ON public.industry_dossier(industry, expires_at);

-- RLS — public-readable cache, service-role write only
ALTER TABLE public.industry_dossier ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "industry_dossier_service_write" ON public.industry_dossier;
DROP POLICY IF EXISTS "industry_dossier_public_read"   ON public.industry_dossier;

CREATE POLICY "industry_dossier_service_write" ON public.industry_dossier
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "industry_dossier_public_read" ON public.industry_dossier
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.industry_dossier TO anon, authenticated;
GRANT ALL    ON public.industry_dossier TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.industry_dossier_id_seq TO service_role;

COMMIT;
