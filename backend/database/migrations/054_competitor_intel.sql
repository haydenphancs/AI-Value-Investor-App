-- 054_competitor_intel.sql
--
-- Why: the TickerReport "Competitors" section's peer source is FMP's
-- `/stock-peers` augmented from `data/industry_universe.json` (the
-- Phase-1 fallback). That deterministic path is structurally too narrow
-- because FMP's `industry` field reflects a company's PRIMARY
-- classification, not its revenue-mix overlap with another company. For
-- Oracle (ORCL), the real competitors — Microsoft, Amazon (AWS),
-- Salesforce, SAP, IBM, Adobe, Google (GCP), Broadcom (post-VMware) —
-- span 4-5 different FMP industries and can never all surface from a
-- single-industry-bucket query.
--
-- Phase 2: revenue-mix-aware competitor selection via Gemini grounded
-- research, validated against FMP, audited per run. Mirrors the existing
-- industry_dossier (Phase A) + industry_override (Phase B) architecture
-- — same quarterly cadence (first Sunday of Jan/Apr/Jul/Oct 02:00 UTC),
-- same anti-fabrication guardrails (no ticker survives without an FMP
-- /profile resolution + positive mktCap), same audit-log discipline.
--
-- Two tables:
--   competitor_intel_cache — served-from-cache state (one row per ticker)
--   competitor_intel_audit — breadcrumb trail of every extraction attempt
--
-- The cache row is the source of truth read by
-- ticker_report_data_collector._fetch_dependent (Phase 2 primary path).
-- When a row is missing or expired, the service falls back to the
-- Phase-1 deterministic peer-augmentation path (which still applies the
-- $27.3B mkt-cap floor and 7-row cap as its quality gates).
--
-- Idempotent — CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- DROP POLICY IF EXISTS before each CREATE POLICY. Replayable.

BEGIN;

-- ── Cache table ──────────────────────────────────────────────────────
--
-- One row per focal ticker, holding the validated competitor list +
-- publisher attributions. `expires_at` is set ~100 days out by the
-- service (one quarter + a few days of safety margin) so the next
-- quarterly batch on the first Sunday of Jan/Apr/Jul/Oct overwrites
-- before the row expires.

CREATE TABLE IF NOT EXISTS public.competitor_intel_cache (
    ticker TEXT PRIMARY KEY,
    competitor_tickers TEXT[] NOT NULL,        -- ordered by FMP mktCap desc
    source_labels TEXT[] NOT NULL DEFAULT '{}',-- publisher attributions (Reuters/SEC.gov/etc)
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    model_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_competitor_intel_cache_expires
    ON public.competitor_intel_cache(expires_at);

ALTER TABLE public.competitor_intel_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "competitor_intel_cache_service_write" ON public.competitor_intel_cache;
DROP POLICY IF EXISTS "competitor_intel_cache_public_read"   ON public.competitor_intel_cache;

CREATE POLICY "competitor_intel_cache_service_write" ON public.competitor_intel_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "competitor_intel_cache_public_read" ON public.competitor_intel_cache
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.competitor_intel_cache TO anon, authenticated;
GRANT ALL    ON public.competitor_intel_cache TO service_role;

-- ── Audit table ──────────────────────────────────────────────────────
--
-- Every Gemini extraction attempt leaves a row. Operators inspect this
-- after the quarterly batch to:
--   1. See exactly what Gemini returned for each ticker (raw_response).
--   2. See which tickers Gemini suggested vs which survived validation.
--   3. See rejection reasons (unknown ticker, zero mktCap, etc).
--   4. Spot quarter-over-quarter drift (a competitor disappearing from
--      MSFT's list two quarters running is a signal worth investigating).

CREATE TABLE IF NOT EXISTS public.competitor_intel_audit (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,                              -- groups all rows from one batch execution
    ticker TEXT NOT NULL,                              -- focal ticker the research was about
    status TEXT NOT NULL CHECK (status IN (
        'applied',                  -- validated_tickers written to cache
        'applied_with_rejections',  -- some Gemini suggestions dropped in validation, others kept
        'rejected_no_validated',    -- zero tickers survived validation (no cache write)
        'gemini_error',             -- upstream Gemini call threw / no parseable JSON
        'skipped_kill_switch'       -- COMPETITOR_INTEL_AI_ENABLED=false
    )),
    raw_response JSONB,                                -- full Gemini text + grounding + search queries
    suggested_tickers TEXT[],                          -- what Gemini returned, pre-validation
    validated_tickers TEXT[],                          -- post-validation survivors (subset of suggested)
    rejected JSONB,                                    -- [{ticker, reason}, ...]
    source_labels TEXT[],                              -- publisher attributions derived from grounding
    tokens_used INTEGER,                               -- Gemini cost tracking
    model_version TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_competitor_intel_audit_run_id
    ON public.competitor_intel_audit(run_id);

CREATE INDEX IF NOT EXISTS idx_competitor_intel_audit_ticker
    ON public.competitor_intel_audit(ticker, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_competitor_intel_audit_computed_at
    ON public.competitor_intel_audit(computed_at DESC);

ALTER TABLE public.competitor_intel_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "competitor_intel_audit_service_write" ON public.competitor_intel_audit;
DROP POLICY IF EXISTS "competitor_intel_audit_public_read"   ON public.competitor_intel_audit;

CREATE POLICY "competitor_intel_audit_service_write" ON public.competitor_intel_audit
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "competitor_intel_audit_public_read" ON public.competitor_intel_audit
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.competitor_intel_audit TO anon, authenticated;
GRANT ALL    ON public.competitor_intel_audit TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.competitor_intel_audit_id_seq TO service_role;

COMMIT;
