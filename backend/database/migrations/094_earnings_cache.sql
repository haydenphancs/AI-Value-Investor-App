-- 094_earnings_cache.sql
--
-- Why: the companion to 093. earnings_service also ran on a 5-minute in-memory
-- cache alone — no Supabase tier, no in-flight dedup — while carrying the
-- HEAVIEST payload on the Financials tab: `_build_earnings` pulls SIX YEARS of
-- daily closes (`historical-price-eod/full`) plus quarterly income statements,
-- analyst estimates and the full earnings calendar on every miss, then serves a
-- `daily_price_history` array with one entry per trading day.
--
-- On a cold process (every Railway deploy) N concurrent viewers of the same
-- ticker each repeated that download. Earnings inputs only change when a company
-- reports, so 24h + earnings-date invalidation is the correct shape, matching
-- the four sibling section caches.
--
-- NOTE the JSONB payload here is larger than the siblings (the daily price
-- series dominates it). That is deliberate — it is exactly the part we do not
-- want to re-download — and Postgres TOASTs/compresses it transparently.
--
-- Schema: one row per ticker; the full EarningsResponse frozen as JSONB;
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

CREATE TABLE IF NOT EXISTS earnings_cache (
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_earnings_cache_ticker
    ON earnings_cache(ticker);

ALTER TABLE earnings_cache ENABLE ROW LEVEL SECURITY;

-- No anon/authenticated policy or GRANT by design (see header). service_role
-- bypasses RLS, but the explicit policy keeps the intent declarative.
DROP POLICY IF EXISTS "earnings_cache_service_role_all" ON earnings_cache;
CREATE POLICY "earnings_cache_service_role_all" ON earnings_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON earnings_cache FROM anon, authenticated;
GRANT ALL ON earnings_cache TO service_role;

COMMIT;
