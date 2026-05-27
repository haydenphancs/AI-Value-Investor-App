-- 055_moat_intel.sql
--
-- Why: Phase 3A (moat_scoring_service) replaced LLM-judgment moat
-- dimension scores with deterministic sector-relative percentile-rank
-- scoring grounded in real FMP financials + sector benchmarks. For
-- pillars where <2 metrics resolve (Switching Costs always today;
-- Brand Power / Intangible Assets occasionally for sparse-data tickers
-- like small caps or foreign listings), the service returns
-- confidence='low' and the collector currently falls through to the
-- legacy Gemini Stage A dimension — which is ungrounded LLM judgment,
-- the exact thing Phase 3 set out to replace.
--
-- Phase 3D: when a pillar would otherwise fall back to legacy AI, call
-- Gemini grounded research (Google Search + grounding citations) and
-- use the cited score instead. Sources (Reuters, SEC, 10-K filings,
-- analyst reports) are extracted from the grounding metadata and
-- written to the audit log — never invented.
--
-- One Gemini call covers all 5 pillars per ticker, cached for ~100 days
-- (moat strength is stable quarter-over-quarter; aligned with the
-- existing quarterly research window used by industry_override_audit
-- and competitor_intel_cache).
--
-- Two tables, mirrors 054_competitor_intel.sql shape:
--   moat_intel_cache  — served-from-cache pillar scores per ticker
--   moat_intel_audit  — breadcrumb of every grounded extraction attempt
--
-- Idempotent — CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- DROP POLICY IF EXISTS before CREATE POLICY. Replayable.

BEGIN;

-- ── Cache table ──────────────────────────────────────────────────────
--
-- One row per ticker. `pillar_scores` holds the full {pillar_name: {
-- score, rationale, drivers, source_labels}} payload Gemini returned,
-- so downstream code can pick out whichever subset the deterministic
-- pipeline left as low-confidence. expires_at ~= computed_at + 100
-- days, aligned to the quarterly recompute anchor.

CREATE TABLE IF NOT EXISTS public.moat_intel_cache (
    ticker TEXT PRIMARY KEY,
    pillar_scores JSONB NOT NULL,                       -- {pillar: {score, rationale, drivers, source_labels}}
    source_labels TEXT[] NOT NULL DEFAULT '{}',         -- top-level publisher attribution (Reuters/SEC/etc.)
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    model_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_moat_intel_cache_expires
    ON public.moat_intel_cache(expires_at);

ALTER TABLE public.moat_intel_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "moat_intel_cache_service_write" ON public.moat_intel_cache;
DROP POLICY IF EXISTS "moat_intel_cache_public_read"   ON public.moat_intel_cache;

CREATE POLICY "moat_intel_cache_service_write" ON public.moat_intel_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "moat_intel_cache_public_read" ON public.moat_intel_cache
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.moat_intel_cache TO anon, authenticated;
GRANT ALL    ON public.moat_intel_cache TO service_role;

-- ── Audit table ──────────────────────────────────────────────────────
--
-- Every grounded extraction attempt leaves a row. Operators inspect to
-- diagnose:
--   1. What Gemini returned for each pillar (raw_response).
--   2. Which pillars actually got scored vs were dropped in validation.
--   3. Rejection reasons (numeric bounds, missing pillar key, JSON
--      parse failure, etc.).
--   4. Quarter-over-quarter score drift on a per-ticker basis.

CREATE TABLE IF NOT EXISTS public.moat_intel_audit (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,                               -- groups rows from a single batch run (or single on-demand request)
    ticker TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'applied',                  -- pillar_scores written to cache
        'applied_with_rejections',  -- some pillars dropped in validation; rest kept
        'rejected_no_validated',    -- no pillar survived validation; no cache write
        'gemini_error',             -- upstream Gemini call threw / no parseable JSON
        'skipped_kill_switch'       -- MOAT_INTEL_AI_ENABLED=false
    )),
    raw_response JSONB,                                 -- full Gemini text + grounding + search queries
    pillars_requested TEXT[],                           -- which pillars the caller asked grounded scoring for
    pillars_resolved TEXT[],                            -- subset that survived validation
    rejected JSONB,                                     -- [{pillar, reason}, ...]
    source_labels TEXT[],
    tokens_used INTEGER,                                -- cost tracking
    model_version TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_moat_intel_audit_run_id
    ON public.moat_intel_audit(run_id);

CREATE INDEX IF NOT EXISTS idx_moat_intel_audit_ticker
    ON public.moat_intel_audit(ticker, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_moat_intel_audit_computed_at
    ON public.moat_intel_audit(computed_at DESC);

ALTER TABLE public.moat_intel_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "moat_intel_audit_service_write" ON public.moat_intel_audit;
DROP POLICY IF EXISTS "moat_intel_audit_public_read"   ON public.moat_intel_audit;

CREATE POLICY "moat_intel_audit_service_write" ON public.moat_intel_audit
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "moat_intel_audit_public_read" ON public.moat_intel_audit
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.moat_intel_audit TO anon, authenticated;
GRANT ALL    ON public.moat_intel_audit TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.moat_intel_audit_id_seq TO service_role;

COMMIT;
