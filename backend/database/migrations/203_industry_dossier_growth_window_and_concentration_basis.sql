-- 203_industry_dossier_growth_window_and_concentration_basis.sql
--
-- Why: owner decisions of 2026-10-22 for the ticker report's "Industry & Competitive Moat"
-- market-dynamics row, after RKLB's card read "CAGR (5Yr) -0.2%, $246B (2026) → $244B (2031)"
-- for a US aerospace market that is not shrinking:
--
--  * "Measured only": the card shows the published market size at its OWN year and the
--    average yearly growth over its REAL window ("Avg/yr 2017–24"), never a projection.
--    The window's first year must be stored: for a BEA/FRED row it is the oldest valid
--    observation of the fetched series, which cannot be rebuilt on read.
--      → cagr_start_year
--
--  * "Revenue-share HHI": concentration now comes from the listed constituents' TTM
--    revenue, not their market cap — one richly valued listing (SpaceX, $1.9T of US-listed
--    Aerospace & Defense's $3.5T) would otherwise flip A&D to "oligopoly" on its own. A
--    recompute that reads a universe file without revenues KEEPS the stored market-cap
--    measure, so each row records what its hhi / top shares / label are shares of.
--      → concentration_basis
--
-- Additive and nullable; old code never writes these columns. Idempotent: ADD COLUMN IF NOT
-- EXISTS (the inline CHECK rides the same add, so a replay is a no-op), and both backfills
-- touch only NULLs.
--
-- ⚠️ Apply BEFORE deploying the backend that writes them: every industry_dossier upsert
-- from that release names both columns, and PostgREST refuses an unknown column.
--
-- No grant change: industry_dossier is service-role only at table level
-- (tests/test_table_grants_service_role_only.py). The concentration_label CHECK is
-- unchanged — 'unknown' exists only on the API wire; an unmeasured row stores NULL.

BEGIN;

ALTER TABLE public.industry_dossier
    ADD COLUMN IF NOT EXISTS cagr_start_year integer
        CHECK (cagr_start_year IS NULL OR cagr_start_year BETWEEN 1990 AND 2100);

ALTER TABLE public.industry_dossier
    ADD COLUMN IF NOT EXISTS concentration_basis text
        CHECK (concentration_basis IS NULL OR concentration_basis IN ('revenue_share', 'market_cap'));

-- Census rows: the growth baseline is always the 2017 Economic Census
-- (`integrations/census.py` `_ECN_BASELINE_YEAR`). FRED rows stay NULL until the next
-- recompute writes their real base year.
UPDATE public.industry_dossier
   SET cagr_start_year = 2017
 WHERE cagr_start_year IS NULL
   AND cagr_5y_pct IS NOT NULL
   AND source_label LIKE 'US Census AIES%';

-- Every concentration stored before this migration is a market-cap measure.
UPDATE public.industry_dossier
   SET concentration_basis = 'market_cap'
 WHERE concentration_basis IS NULL
   AND hhi IS NOT NULL;

COMMENT ON COLUMN public.industry_dossier.cagr_start_year IS
  'First year of the window cagr_5y_pct is measured over (Census: the 2017 Economic Census; FRED: the oldest valid observation). The window ends at current_year. NULL = no growth rate, or a FRED row computed before migration 203.';
COMMENT ON COLUMN public.industry_dossier.concentration_basis IS
  'What hhi / top1_share_pct / top2_share_pct / concentration_label are shares of: revenue_share (listed constituents'' TTM revenue, from 2026-10-22) or market_cap (rows before). NULL = not measured.';
COMMENT ON COLUMN public.industry_dossier.expires_at IS
  'Informational only: no reader checks it. A stored row is served until the quarterly recompute (or an admin refresh) replaces it; its zero / grain guards decide when.';

COMMIT;

-- VERIFY (read-only) → one row: t | t | t | t
-- SELECT
--   EXISTS (SELECT 1 FROM information_schema.columns
--            WHERE table_schema = 'public' AND table_name = 'industry_dossier'
--              AND column_name = 'cagr_start_year'),
--   EXISTS (SELECT 1 FROM information_schema.columns
--            WHERE table_schema = 'public' AND table_name = 'industry_dossier'
--              AND column_name = 'concentration_basis'),
--   (SELECT count(*) FROM public.industry_dossier
--     WHERE source_label LIKE 'US Census AIES%' AND cagr_5y_pct IS NOT NULL
--       AND cagr_start_year IS NULL) = 0,
--   (SELECT count(*) FROM public.industry_dossier
--     WHERE hhi IS NOT NULL AND concentration_basis IS NULL) = 0;
