-- 056_ip_intel_cache.sql
--
-- Why: Phase 3C of the moat scoring rebuild adds USPTO PatentsView +
-- FDA OpenFDA data to the Intangible Assets pillar of moat_scoring_
-- service. Patents per employee (tech / industrials) and FDA active
-- drug approvals (pharma) are the strongest known proxies for the
-- "regulatory / IP moat" sub-component.
--
-- Patents + FDA approvals change very slowly (a few new patents per
-- quarter for big tech; FDA drug approvals are annual events). 180-day
-- cache TTL is appropriate — re-fetched quarterly by the chained
-- ip_intel_service.refresh_top_tickers() batch in main.py.
--
-- Single table per ticker: payload JSONB holds the full extraction
-- (patent counts + FDA approval counts + per-source raw data for
-- audit). audit log is per-batch-run, mirroring 054_competitor_intel
-- and 055_moat_intel.
--
-- Idempotent — CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- DROP POLICY IF EXISTS pattern. Replayable.

BEGIN;

-- ── Cache table ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.ip_intel_cache (
    ticker TEXT PRIMARY KEY,
    payload JSONB NOT NULL,                              -- {patents_recent_5y, patents_total, fda_active_approvals, ...}
    source_labels TEXT[] NOT NULL DEFAULT '{}',          -- ['USPTO', 'OpenFDA'] etc.
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ip_intel_cache_expires
    ON public.ip_intel_cache(expires_at);

ALTER TABLE public.ip_intel_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ip_intel_cache_service_write" ON public.ip_intel_cache;
DROP POLICY IF EXISTS "ip_intel_cache_public_read"   ON public.ip_intel_cache;

CREATE POLICY "ip_intel_cache_service_write" ON public.ip_intel_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "ip_intel_cache_public_read" ON public.ip_intel_cache
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.ip_intel_cache TO anon, authenticated;
GRANT ALL    ON public.ip_intel_cache TO service_role;

-- ── Audit table ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.ip_intel_audit (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    ticker TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'applied',                  -- payload written to cache
        'applied_partial',          -- one source (USPTO or FDA) succeeded; other empty or errored
        'rejected_no_data',         -- both sources empty; no cache write
        'uspto_error',              -- USPTO call threw / unavailable
        'fda_error',                -- OpenFDA call threw / unavailable
        'skipped'                   -- ticker classified out-of-scope (e.g., no R&D-relevant sector)
    )),
    payload JSONB,                                       -- snapshot of returned counts (for diffing quarter-to-quarter)
    uspto_total INTEGER,                                 -- patent total hits at refresh time
    uspto_recent_5y INTEGER,                             -- patents granted in last 5 years
    fda_active INTEGER,                                  -- active drug approval count
    assignee_name TEXT,                                  -- the canonical name we queried USPTO with
    sponsor_name TEXT,                                   -- the canonical name we queried FDA with
    error_detail TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ip_intel_audit_run_id
    ON public.ip_intel_audit(run_id);

CREATE INDEX IF NOT EXISTS idx_ip_intel_audit_ticker
    ON public.ip_intel_audit(ticker, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_ip_intel_audit_computed_at
    ON public.ip_intel_audit(computed_at DESC);

ALTER TABLE public.ip_intel_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ip_intel_audit_service_write" ON public.ip_intel_audit;
DROP POLICY IF EXISTS "ip_intel_audit_public_read"   ON public.ip_intel_audit;

CREATE POLICY "ip_intel_audit_service_write" ON public.ip_intel_audit
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "ip_intel_audit_public_read" ON public.ip_intel_audit
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.ip_intel_audit TO anon, authenticated;
GRANT ALL    ON public.ip_intel_audit TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.ip_intel_audit_id_seq TO service_role;

COMMIT;
