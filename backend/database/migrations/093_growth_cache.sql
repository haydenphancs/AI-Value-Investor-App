-- 093_growth_cache.sql
--
-- Why: growth_service was the only Financials-tab section (with earnings, see
-- 094) still running on a 5-minute IN-MEMORY cache alone — no Supabase tier and
-- no in-flight dedup — in violation of the cache-aside invariant the other four
-- sections follow (profit_power_cache, health_check_cache,
-- revenue_breakdown_cache, signal_of_confidence_cache).
--
-- That matters more here than anywhere else on the tab: one growth cache MISS
-- costs TEN FMP calls (profile + annual/quarterly income + annual/quarterly cash
-- flow), and the in-memory tier is per-process and lost on every Railway deploy.
-- With N users opening the same cold ticker inside the 5-minute window, each
-- request fired the whole fan-out. Growth inputs only change when a company
-- files, so a 24h TTL invalidated early by the next earnings date is the right
-- shape — identical to the sibling caches.
--
-- Schema: one row per ticker; the full GrowthResponse frozen as JSONB;
-- `next_earnings_date` invalidates the row the moment the company reports,
-- rather than waiting out the 24h TTL.
--
-- Read posture: service_role ONLY, matching the four sibling section caches.
-- Migration 078 REVOKEs anon/authenticated on health_check_cache /
-- profit_power_cache / revenue_breakdown_cache / signal_of_confidence_cache and
-- documents why: these tables are served THROUGH the backend API (iOS has no
-- Supabase client), so granting public read would expand access for no reason.
--
-- Idempotent; safe to re-apply. Apply manually (Supabase Studio / CLI).

BEGIN;

CREATE TABLE IF NOT EXISTS growth_cache (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker             TEXT        NOT NULL,
    response_json      JSONB       NOT NULL,
    cached_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- yyyy-MM-dd; TEXT to match the sibling caches (FMP returns a date string)
    next_earnings_date TEXT
);

-- The service upserts with on_conflict="ticker", which REQUIRES a unique
-- constraint/index on that column — declared here rather than inline in CREATE
-- TABLE so it is also created when an earlier draft of the table already exists
-- (CREATE TABLE IF NOT EXISTS would be a silent no-op in that case, and the
-- upsert would then fail 42P10 into the best-effort handler, leaving tier 2
-- permanently non-persisting with only a warning line).
CREATE UNIQUE INDEX IF NOT EXISTS idx_growth_cache_ticker
    ON growth_cache(ticker);

ALTER TABLE growth_cache ENABLE ROW LEVEL SECURITY;

-- No anon/authenticated policy or GRANT by design (see header). service_role
-- bypasses RLS, but the explicit policy keeps the intent declarative.
DROP POLICY IF EXISTS "growth_cache_service_role_all" ON growth_cache;
CREATE POLICY "growth_cache_service_role_all" ON growth_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON growth_cache FROM anon, authenticated;
GRANT ALL ON growth_cache TO service_role;

COMMIT;
