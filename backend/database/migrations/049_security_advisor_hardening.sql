-- 049_security_advisor_hardening.sql
--
-- Why: Supabase Security Advisor (post-047/048) flagged 34 warnings.
-- This migration closes the SQL-fixable subset:
--
--   A. function_search_path_mutable (13 functions)
--      Functions ran with the caller's search_path, which is a known
--      escalation vector when combined with SECURITY DEFINER. Pin each
--      to `public, pg_temp` so the resolution order is deterministic.
--
--   B. anon_security_definer_function_executable +
--      authenticated_security_definer_function_executable (4 functions × 2)
--      Four SECURITY DEFINER functions had the default PUBLIC EXECUTE
--      grant, meaning anyone with the anon or authenticated API key
--      could call them through PostgREST RPC and run with elevated
--      privileges. Revoke PUBLIC + role grants; grant only service_role.
--      handle_new_auth_user() is trigger-only and never needs RPC.
--
--   C. rls_policy_always_true (11 cache tables)
--      Each had a single policy `Service role full access` (or
--      `service_role_all`) defined with the default `TO public`, making
--      the `USING (true)` apply to every role — anon and authenticated
--      could read AND write. Replace each with the canonical cache pair:
--        - `<table>_service_write`  FOR ALL TO service_role
--        - `<table>_public_read`    FOR SELECT TO anon, authenticated
--      This matches the template in .claude/rules/database.md and the
--      pattern used in migration 048.
--
-- Out of scope (cannot/should not fix here):
--   - extension_in_public (vector). Moving extensions can break index
--     references and is high-risk; keep as a separate, careful migration.
--   - auth_leaked_password_protection. Supabase dashboard toggle, not SQL.
--
-- Idempotent throughout. ALTER FUNCTION ... SET is replayable. REVOKE is
-- a no-op if already revoked. CREATE POLICY is guarded by DROP POLICY
-- IF EXISTS so re-runs don't error on the new policy names.

BEGIN;

-- =============================================================================
-- A. Pin search_path on every public function
-- =============================================================================

ALTER FUNCTION public.charge_user_credits(uuid, integer)         SET search_path = public, pg_temp;
ALTER FUNCTION public.refund_user_credits(uuid, integer)         SET search_path = public, pg_temp;
ALTER FUNCTION public.create_user_credits()                      SET search_path = public, pg_temp;
ALTER FUNCTION public.cleanup_expired_news_cache()               SET search_path = public, pg_temp;
ALTER FUNCTION public.cleanup_old_social_mentions()              SET search_path = public, pg_temp;
ALTER FUNCTION public.get_top_watchlist_tickers(integer)         SET search_path = public, pg_temp;
ALTER FUNCTION public.increment_chat_message_count()             SET search_path = public, pg_temp;
ALTER FUNCTION public.update_updated_at_column()                 SET search_path = public, pg_temp;
ALTER FUNCTION public.update_whale_followers_count()             SET search_path = public, pg_temp;
ALTER FUNCTION public.handle_new_auth_user()                     SET search_path = public, pg_temp;
ALTER FUNCTION public.search_all_chunks(public.vector, double precision, integer)
    SET search_path = public, pg_temp;
ALTER FUNCTION public.search_article_chunks(public.vector, double precision, integer)
    SET search_path = public, pg_temp;
ALTER FUNCTION public.search_book_chunks(public.vector, double precision, integer, uuid)
    SET search_path = public, pg_temp;
ALTER FUNCTION public.search_filing_chunks(public.vector, double precision, integer, text, text)
    SET search_path = public, pg_temp;


-- =============================================================================
-- B. Revoke public RPC access from SECURITY DEFINER functions
--    These run with the function-owner's privileges; allowing anon /
--    authenticated to invoke them is a privilege-escalation path.
-- =============================================================================

REVOKE EXECUTE ON FUNCTION public.cleanup_expired_news_cache()       FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.cleanup_expired_news_cache()       TO service_role;

REVOKE EXECUTE ON FUNCTION public.cleanup_old_social_mentions()      FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.cleanup_old_social_mentions()      TO service_role;

REVOKE EXECUTE ON FUNCTION public.get_top_watchlist_tickers(integer) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_top_watchlist_tickers(integer) TO service_role;

-- handle_new_auth_user is invoked by the auth.users INSERT trigger only.
-- It never needs RPC exposure to anyone.
REVOKE EXECUTE ON FUNCTION public.handle_new_auth_user()             FROM PUBLIC, anon, authenticated;


-- =============================================================================
-- C. Rewrite the 11 over-permissive cache-table policies.
--    Pattern: drop the bad policy → create scoped service_role write +
--    anon/authenticated read. Mirrors migration 048.
-- =============================================================================

-- ---- crypto_coin_id_cache ----
DROP POLICY IF EXISTS "Service role full access"          ON public.crypto_coin_id_cache;
DROP POLICY IF EXISTS "crypto_coin_id_cache_service_write" ON public.crypto_coin_id_cache;
DROP POLICY IF EXISTS "crypto_coin_id_cache_public_read"   ON public.crypto_coin_id_cache;
CREATE POLICY "crypto_coin_id_cache_service_write" ON public.crypto_coin_id_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "crypto_coin_id_cache_public_read"   ON public.crypto_coin_id_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.crypto_coin_id_cache TO anon, authenticated;
GRANT ALL    ON public.crypto_coin_id_cache TO service_role;

-- ---- crypto_fundamentals_cache ----
DROP POLICY IF EXISTS "Service role full access"               ON public.crypto_fundamentals_cache;
DROP POLICY IF EXISTS "crypto_fundamentals_cache_service_write" ON public.crypto_fundamentals_cache;
DROP POLICY IF EXISTS "crypto_fundamentals_cache_public_read"   ON public.crypto_fundamentals_cache;
CREATE POLICY "crypto_fundamentals_cache_service_write" ON public.crypto_fundamentals_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "crypto_fundamentals_cache_public_read"   ON public.crypto_fundamentals_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.crypto_fundamentals_cache TO anon, authenticated;
GRANT ALL    ON public.crypto_fundamentals_cache TO service_role;

-- ---- crypto_snapshots ----
DROP POLICY IF EXISTS "Service role full access"       ON public.crypto_snapshots;
DROP POLICY IF EXISTS "crypto_snapshots_service_write" ON public.crypto_snapshots;
DROP POLICY IF EXISTS "crypto_snapshots_public_read"   ON public.crypto_snapshots;
CREATE POLICY "crypto_snapshots_service_write" ON public.crypto_snapshots
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "crypto_snapshots_public_read"   ON public.crypto_snapshots
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.crypto_snapshots TO anon, authenticated;
GRANT ALL    ON public.crypto_snapshots TO service_role;

-- ---- etf_detail_cache ----
DROP POLICY IF EXISTS "Service role full access"       ON public.etf_detail_cache;
DROP POLICY IF EXISTS "etf_detail_cache_service_write" ON public.etf_detail_cache;
DROP POLICY IF EXISTS "etf_detail_cache_public_read"   ON public.etf_detail_cache;
CREATE POLICY "etf_detail_cache_service_write" ON public.etf_detail_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "etf_detail_cache_public_read"   ON public.etf_detail_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.etf_detail_cache TO anon, authenticated;
GRANT ALL    ON public.etf_detail_cache TO service_role;

-- ---- etf_snapshot_cache ----
DROP POLICY IF EXISTS "Service role full access on etf_snapshot_cache" ON public.etf_snapshot_cache;
DROP POLICY IF EXISTS "etf_snapshot_cache_service_write"               ON public.etf_snapshot_cache;
DROP POLICY IF EXISTS "etf_snapshot_cache_public_read"                 ON public.etf_snapshot_cache;
CREATE POLICY "etf_snapshot_cache_service_write" ON public.etf_snapshot_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "etf_snapshot_cache_public_read"   ON public.etf_snapshot_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.etf_snapshot_cache TO anon, authenticated;
GRANT ALL    ON public.etf_snapshot_cache TO service_role;

-- ---- index_detail_cache ----
DROP POLICY IF EXISTS "Service role full access"         ON public.index_detail_cache;
DROP POLICY IF EXISTS "index_detail_cache_service_write" ON public.index_detail_cache;
DROP POLICY IF EXISTS "index_detail_cache_public_read"   ON public.index_detail_cache;
CREATE POLICY "index_detail_cache_service_write" ON public.index_detail_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "index_detail_cache_public_read"   ON public.index_detail_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.index_detail_cache TO anon, authenticated;
GRANT ALL    ON public.index_detail_cache TO service_role;

-- ---- index_macro_forecast_cache ----
DROP POLICY IF EXISTS service_role_all                          ON public.index_macro_forecast_cache;
DROP POLICY IF EXISTS "index_macro_forecast_cache_service_write" ON public.index_macro_forecast_cache;
DROP POLICY IF EXISTS "index_macro_forecast_cache_public_read"   ON public.index_macro_forecast_cache;
CREATE POLICY "index_macro_forecast_cache_service_write" ON public.index_macro_forecast_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "index_macro_forecast_cache_public_read"   ON public.index_macro_forecast_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.index_macro_forecast_cache TO anon, authenticated;
GRANT ALL    ON public.index_macro_forecast_cache TO service_role;

-- ---- market_deep_dive_cache ----
DROP POLICY IF EXISTS service_role_all                       ON public.market_deep_dive_cache;
DROP POLICY IF EXISTS "market_deep_dive_cache_service_write" ON public.market_deep_dive_cache;
DROP POLICY IF EXISTS "market_deep_dive_cache_public_read"   ON public.market_deep_dive_cache;
CREATE POLICY "market_deep_dive_cache_service_write" ON public.market_deep_dive_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "market_deep_dive_cache_public_read"   ON public.market_deep_dive_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.market_deep_dive_cache TO anon, authenticated;
GRANT ALL    ON public.market_deep_dive_cache TO service_role;

-- ---- short_interest_cache ----
DROP POLICY IF EXISTS "Service role full access"           ON public.short_interest_cache;
DROP POLICY IF EXISTS "short_interest_cache_service_write" ON public.short_interest_cache;
DROP POLICY IF EXISTS "short_interest_cache_public_read"   ON public.short_interest_cache;
CREATE POLICY "short_interest_cache_service_write" ON public.short_interest_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "short_interest_cache_public_read"   ON public.short_interest_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.short_interest_cache TO anon, authenticated;
GRANT ALL    ON public.short_interest_cache TO service_role;

-- ---- stock_fundamentals_cache ----
DROP POLICY IF EXISTS "Service role full access"               ON public.stock_fundamentals_cache;
DROP POLICY IF EXISTS "stock_fundamentals_cache_service_write" ON public.stock_fundamentals_cache;
DROP POLICY IF EXISTS "stock_fundamentals_cache_public_read"   ON public.stock_fundamentals_cache;
CREATE POLICY "stock_fundamentals_cache_service_write" ON public.stock_fundamentals_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "stock_fundamentals_cache_public_read"   ON public.stock_fundamentals_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.stock_fundamentals_cache TO anon, authenticated;
GRANT ALL    ON public.stock_fundamentals_cache TO service_role;

-- ---- ticker_report_cache ----
DROP POLICY IF EXISTS "Service role full access"          ON public.ticker_report_cache;
DROP POLICY IF EXISTS "ticker_report_cache_service_write" ON public.ticker_report_cache;
DROP POLICY IF EXISTS "ticker_report_cache_public_read"   ON public.ticker_report_cache;
CREATE POLICY "ticker_report_cache_service_write" ON public.ticker_report_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "ticker_report_cache_public_read"   ON public.ticker_report_cache
    FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.ticker_report_cache TO anon, authenticated;
GRANT ALL    ON public.ticker_report_cache TO service_role;

COMMIT;
