-- 079_lock_down_ticker_data_cache.sql
--
-- Why: Supabase Security Advisor flagged "RLS Policy Always True" on
-- public.ticker_data_cache. Migration 069 created its policy WITHOUT a role clause:
--     CREATE POLICY "Service role full access" ON ticker_data_cache
--         FOR ALL USING (true) WITH CHECK (true);      -- applies to EVERY role
-- A policy with no `TO` clause applies to `public` (all roles). Combined with 069's
-- `GRANT ALL ON ticker_data_cache TO authenticated`, this let ANY logged-in user
-- INSERT / UPDATE / DELETE the internal FMP collection cache — i.e. POISON it
-- (overwrite a ticker's cached collection with garbage the backend then serves to
-- everyone, or delete rows to force re-fetches and drain FMP quota). 069's own
-- header says this table is "INTERNAL-only — read/written by the backend service
-- role; never served to iOS" — the policy simply didn't enforce that.
--
-- Fix: scope the policy to `service_role` and REVOKE the anon/authenticated grants.
-- The backend reads/writes via get_supabase() using SUPABASE_SERVICE_ROLE_KEY
-- (service_role BYPASSES RLS and keeps its own GRANT from 069), so this changes
-- NOTHING for the backend — it only removes the authenticated write path.
--
-- Idempotent. Apply manually (Supabase Studio / CLI).
--
-- NOTE (out of scope, optional later cleanup): several other *_cache tables also
-- carry an over-broad `GRANT ALL ... TO authenticated` (crypto_*_cache, etf_*_cache,
-- snapshot_*_cache, stock_fundamentals_cache, ticker_report_cache, short_interest_cache).
-- They are NOT exploitable today because their RLS policies only grant SELECT to
-- authenticated (writes hit RLS default-deny), which is why the Advisor flagged only
-- this table. Tightening those grants to `GRANT SELECT` is belt-and-suspenders.

-- RLS is already enabled (069); include for safety — no-op if already on.
ALTER TABLE public.ticker_data_cache ENABLE ROW LEVEL SECURITY;

-- Replace the unscoped always-true policy with a service_role-scoped one.
DROP POLICY IF EXISTS "Service role full access" ON public.ticker_data_cache;
DROP POLICY IF EXISTS "ticker_data_cache_service_all" ON public.ticker_data_cache;
CREATE POLICY "ticker_data_cache_service_all" ON public.ticker_data_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Remove the over-broad table privilege (069 granted ALL to authenticated). The
-- table is backend-only; anon/authenticated need no access.
REVOKE ALL ON public.ticker_data_cache FROM anon, authenticated;
