-- 078_enable_rls_unprotected_tables.sql
--
-- Why: the repo is PUBLIC, so the schema is world-readable — RLS is the only wall.
-- A security audit found 9 public tables with NO Row Level Security, including 5
-- `*_cache` tables that violate the invariant in .claude/rules/database.md ("all
-- *_cache tables ... must have RLS enabled"). Supabase's Security Advisor flags
-- these as "RLS disabled in public".
--
-- Exposure today: LOW — none of these 9 are GRANTed to `anon`/`authenticated`, so
-- only the backend (service_role) can reach them. But with RLS OFF, a single stray
-- `GRANT ... TO anon` in the Studio UI (Supabase's #1 footgun) would instantly
-- expose the whole table. Enabling RLS closes that door for good.
--
-- Safety: the backend reads/writes these via get_supabase() using
-- SUPABASE_SERVICE_ROLE_KEY (app/database.py) — service_role BYPASSES RLS, so this
-- migration does NOT affect any backend read/write. anon/authenticated get nothing
-- (no grant + RLS default-deny).
--
-- Policy choice: these tables are served to the app THROUGH the backend API (not
-- read directly by iOS via the anon key), so they get a service_role-only policy —
-- deliberately NO public-read / anon GRANT (that would EXPAND access they don't have
-- today). If a table ever needs direct client read, add the public-read + GRANT
-- from the cache-table template in .claude/rules/database.md instead.
--
-- Idempotent: ENABLE ROW LEVEL SECURITY is a no-op when already on; policies are
-- DROP ... IF EXISTS then CREATE. Apply manually (Supabase Studio / CLI).

-- Belt-and-suspenders: make the "no anon/authenticated access" state DECLARATIVE
-- rather than merely assumed from today's absence of grants. RLS already blocks ROW
-- access even if a GRANT is later added by mistake; this REVOKE also strips any
-- table-level privilege. Harmless no-op when no grant exists. If a table later needs
-- direct client read, GRANT SELECT + a public-read policy explicitly (see below).
REVOKE ALL ON public.health_check_cache FROM anon, authenticated;
REVOKE ALL ON public.holders_cache FROM anon, authenticated;
REVOKE ALL ON public.profit_power_cache FROM anon, authenticated;
REVOKE ALL ON public.revenue_breakdown_cache FROM anon, authenticated;
REVOKE ALL ON public.signal_of_confidence_cache FROM anon, authenticated;
REVOKE ALL ON public.daily_briefings FROM anon, authenticated;
REVOKE ALL ON public.hedge_fund_quarters FROM anon, authenticated;
REVOKE ALL ON public.market_insights FROM anon, authenticated;
REVOKE ALL ON public.social_mentions_history FROM anon, authenticated;

-- ── Cache tables (must have RLS per database.md) ──────────────────────
ALTER TABLE public.health_check_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "health_check_cache_service_all" ON public.health_check_cache;
CREATE POLICY "health_check_cache_service_all" ON public.health_check_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.holders_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "holders_cache_service_all" ON public.holders_cache;
CREATE POLICY "holders_cache_service_all" ON public.holders_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.profit_power_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "profit_power_cache_service_all" ON public.profit_power_cache;
CREATE POLICY "profit_power_cache_service_all" ON public.profit_power_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.revenue_breakdown_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "revenue_breakdown_cache_service_all" ON public.revenue_breakdown_cache;
CREATE POLICY "revenue_breakdown_cache_service_all" ON public.revenue_breakdown_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.signal_of_confidence_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "signal_of_confidence_cache_service_all" ON public.signal_of_confidence_cache;
CREATE POLICY "signal_of_confidence_cache_service_all" ON public.signal_of_confidence_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ── Non-cache backend-owned tables ────────────────────────────────────
-- These are populated + served by the backend (daily briefings, 13F hedge-fund
-- quarters, AI market insights, social-mention history). Backend-only today →
-- service_role policy. If you decide any is safe/desirable for direct client read
-- (e.g. non-sensitive market data), ADD a public-read policy + GRANT SELECT here.
ALTER TABLE public.daily_briefings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "daily_briefings_service_all" ON public.daily_briefings;
CREATE POLICY "daily_briefings_service_all" ON public.daily_briefings
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.hedge_fund_quarters ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "hedge_fund_quarters_service_all" ON public.hedge_fund_quarters;
CREATE POLICY "hedge_fund_quarters_service_all" ON public.hedge_fund_quarters
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.market_insights ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "market_insights_service_all" ON public.market_insights;
CREATE POLICY "market_insights_service_all" ON public.market_insights
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.social_mentions_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "social_mentions_history_service_all" ON public.social_mentions_history;
CREATE POLICY "social_mentions_history_service_all" ON public.social_mentions_history
    FOR ALL TO service_role USING (true) WITH CHECK (true);
