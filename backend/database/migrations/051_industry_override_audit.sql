-- 051_industry_override_audit.sql
--
-- Why: the quarterly `industry_dossier` recompute job now has a Phase B
-- (AI-driven research override) that updates ~9 curated globally-traded
-- industries (semis, biotech, pharma, medical devices, etc.) with
-- research-based TAM/CAGR sourced from grounded web search.
--
-- Each Phase B invocation MUST leave a breadcrumb so the operator can:
--   1. See exactly what Gemini returned for each industry this quarter.
--   2. See which rows were applied vs rejected and why (validation gate,
--      sanity-vs-Phase-A divergence, quota error, etc.).
--   3. Spot magnitude regressions over time (compare run-over-run).
--
-- The dossier table itself only holds the applied final state; this
-- audit table is the log. Public-readable (operators may want to share
-- a quarter's research summary); service-role-only write.
--
-- Idempotent — CREATE IF NOT EXISTS, DROP POLICY IF EXISTS pattern.

BEGIN;

CREATE TABLE IF NOT EXISTS public.industry_override_audit (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,                              -- groups all rows from one Phase B execution
    industry TEXT NOT NULL,
    sector TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'applied',                -- override written to industry_dossier
        'applied_with_warning',   -- written, but TAM divergence >3x from Phase A
        'rejected_validation',    -- failed one of the validation gates
        'rejected_sanity',        -- TAM divergence >10x from Phase A
        'rejected_low_confidence', -- Gemini returned confidence='low'
        'gemini_error',           -- upstream Gemini call threw or returned nothing
        'skipped_kill_switch'     -- INDUSTRY_OVERRIDE_AI_ENABLED=false
    )),
    raw_response JSONB,                                -- full Gemini output (or null on skip / error)
    phase_a_tam_b NUMERIC(14, 2),                      -- the Census/FRED TAM in place when Phase B started
    applied_tam_b NUMERIC(14, 2),                      -- the value actually written (null on reject)
    applied_cagr_pct NUMERIC(8, 4),
    applied_source_label TEXT,
    rejection_reason TEXT,
    tokens_used INTEGER,                               -- Gemini cost tracking
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_industry_override_audit_run_id
    ON public.industry_override_audit(run_id);

CREATE INDEX IF NOT EXISTS idx_industry_override_audit_computed_at
    ON public.industry_override_audit(computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_industry_override_audit_industry
    ON public.industry_override_audit(industry, computed_at DESC);

ALTER TABLE public.industry_override_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "industry_override_audit_service_write" ON public.industry_override_audit;
DROP POLICY IF EXISTS "industry_override_audit_public_read"   ON public.industry_override_audit;

CREATE POLICY "industry_override_audit_service_write" ON public.industry_override_audit
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "industry_override_audit_public_read" ON public.industry_override_audit
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.industry_override_audit TO anon, authenticated;
GRANT ALL    ON public.industry_override_audit TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.industry_override_audit_id_seq TO service_role;

COMMIT;
