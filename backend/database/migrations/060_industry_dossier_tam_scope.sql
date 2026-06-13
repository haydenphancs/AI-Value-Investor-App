-- 060_industry_dossier_tam_scope.sql
--
-- Why: the Ticker Report's "Industry & Competitive Moat" section shows a
-- Market Size (TAM) that is silently a MIX of scopes — US-domestic (Census
-- NAICS / FRED BEA, the default Phase A path) for most industries, but GLOBAL
-- (Gemini grounded research, the Phase B `industry_override_service`) for a
-- curated list of globally-competitive industries (semis, pharma, autos, ...).
-- Users can't tell which figure is which.
--
-- This adds an explicit per-industry scope marker so the report can ALWAYS
-- label the TAM "US" or "Global". Scope follows the data source that produced
-- the row:
--   * Phase A (Census/FRED)      -> 'us'     (the column default)
--   * Phase B (global override)  -> 'global' (set by industry_override_service)
--
-- Existing rows default to 'us' (correct — they're all Census/FRED). The
-- curated override rows flip to 'global' on the next Phase B run.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS (the inline CHECK rides the same add,
-- so a replay where the column already exists is a no-op).

BEGIN;

ALTER TABLE public.industry_dossier
    ADD COLUMN IF NOT EXISTS tam_scope TEXT NOT NULL DEFAULT 'us'
        CHECK (tam_scope IN ('us', 'global'));

COMMIT;
