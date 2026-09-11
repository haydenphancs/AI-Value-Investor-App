-- 165_guest_partitioned_tables_service_role_only.sql
--
-- Why: THE DATABASE DOES NOT MATCH THE RULE THE PROJECT ALREADY WROTE DOWN.
--
-- `.claude/rules/database.md` (user-scoped table template, item 4) states, of the
-- guest-partitioned tables: "`REVOKE ALL … FROM anon, authenticated` plus a service_role-only
-- policy. These tables are service-role-only by design now; widening `USING` to `true` to
-- 'make guests work' would expose every user's rows to any holder of the shipped anon key."
--
-- Of the tables that sentence describes, ONE conforms (`user_investor_profile`, 131).
-- The other seven still carry their creating migration's grant and a full set of per-user
-- policies, verified in the migration set and the live snapshot:
--
--     watchlist_items      GRANT ALL TO authenticated (018)   watchlist_{select,insert,update,delete}_own
--     portfolios           GRANT ALL TO authenticated (037)   portfolios_owner + "Users manage own portfolios"
--     portfolio_items      (inherits 037's posture)           portfolio_items_owner + "Users manage own portfolio items"
--     research_reports     never revoked (default ALL)        reports_{select,insert,update,delete}_own
--     chat_sessions        never revoked (default ALL)        chat_sessions_{select,insert,update,delete}_own
--     chat_messages        never revoked (default ALL)        chat_messages_{select,insert}_own
--     user_learn_progress  GRANT SELECT,INSERT,UPDATE,DELETE TO authenticated (067)   *_own ×4
--
-- So a signed-in user can INSERT, UPDATE and DELETE their own rows on all of these straight
-- through PostgREST with the anon key from the binary — bypassing every backend rule those
-- writes are supposed to pass: the tier caps, the guest-claim bookkeeping, the deletion
-- accounting in `_UNLINKED_USER_TABLES`, the `research_reports` credit precharge, the
-- chat-turn budget. The `auth.uid() = user_id` predicate limits the blast radius to the
-- caller's OWN rows, which is why this is a posture defect and not a leak — but a user
-- inserting a `research_reports` row with `status = 'completed'` and arbitrary
-- `ticker_report_data`, or a `chat_sessions` row with a forged `context_snapshot`, is not
-- something the backend was written to expect.
--
-- Why the per-user policies could never be the design anyway: migrations 108/110/111 dropped
-- the `user_id` FK on these tables so a signed-out install could be partitioned on a
-- synthetic uuid5. That id never equals `auth.uid()`, so for the guest rows the policies
-- were dead on arrival; for account rows they were an unintended second write path. The
-- backend reaches all of them through `get_supabase()` — service_role — and the iOS app
-- has no Supabase REST client, so nothing loses access.
--
-- Policies are DROPPED rather than left dead behind the REVOKE, for the reason 151 gives:
-- a permissive policy behind a revoked grant is one GRANT away from re-opening. Each table
-- keeps its service_role policy (`*_service_all` / "Service role full access on …").
--
-- Idempotent: REVOKE/GRANT declarative; every DROP POLICY is IF EXISTS.
--
-- VERIFY AFTER APPLYING:
--
--     SELECT table_name, grantee FROM information_schema.role_table_grants
--      WHERE table_schema = 'public' AND grantee IN ('anon','authenticated')
--        AND table_name IN ('watchlist_items','portfolios','portfolio_items',
--                           'research_reports','chat_sessions','chat_messages',
--                           'user_learn_progress');
--     -- expect: no rows.

-- ---- watchlist_items (018 → 108) ----
DROP POLICY IF EXISTS "watchlist_select_own" ON public.watchlist_items;
DROP POLICY IF EXISTS "watchlist_insert_own" ON public.watchlist_items;
DROP POLICY IF EXISTS "watchlist_update_own" ON public.watchlist_items;
DROP POLICY IF EXISTS "watchlist_delete_own" ON public.watchlist_items;
REVOKE ALL ON public.watchlist_items FROM anon, authenticated;
GRANT  ALL ON public.watchlist_items TO service_role;

-- ---- portfolios / portfolio_items (037 → 108) ----
DROP POLICY IF EXISTS "Users manage own portfolios" ON public.portfolios;
DROP POLICY IF EXISTS "portfolios_owner" ON public.portfolios;
REVOKE ALL ON public.portfolios FROM anon, authenticated;
GRANT  ALL ON public.portfolios TO service_role;

DROP POLICY IF EXISTS "Users manage own portfolio items" ON public.portfolio_items;
DROP POLICY IF EXISTS "portfolio_items_owner" ON public.portfolio_items;
REVOKE ALL ON public.portfolio_items FROM anon, authenticated;
GRANT  ALL ON public.portfolio_items TO service_role;

-- ---- research_reports (→ 110) ----
DROP POLICY IF EXISTS "reports_select_own" ON public.research_reports;
DROP POLICY IF EXISTS "reports_insert_own" ON public.research_reports;
DROP POLICY IF EXISTS "reports_update_own" ON public.research_reports;
DROP POLICY IF EXISTS "reports_delete_own" ON public.research_reports;
REVOKE ALL ON public.research_reports FROM anon, authenticated;
GRANT  ALL ON public.research_reports TO service_role;

-- ---- chat_sessions / chat_messages (→ 111) ----
DROP POLICY IF EXISTS "chat_sessions_select_own" ON public.chat_sessions;
DROP POLICY IF EXISTS "chat_sessions_insert_own" ON public.chat_sessions;
DROP POLICY IF EXISTS "chat_sessions_update_own" ON public.chat_sessions;
DROP POLICY IF EXISTS "chat_sessions_delete_own" ON public.chat_sessions;
REVOKE ALL ON public.chat_sessions FROM anon, authenticated;
GRANT  ALL ON public.chat_sessions TO service_role;

DROP POLICY IF EXISTS "chat_messages_select_own" ON public.chat_messages;
DROP POLICY IF EXISTS "chat_messages_insert_own" ON public.chat_messages;
REVOKE ALL ON public.chat_messages FROM anon, authenticated;
GRANT  ALL ON public.chat_messages TO service_role;

-- ---- user_learn_progress (066/067) ----
DROP POLICY IF EXISTS "user_learn_progress_select_own" ON public.user_learn_progress;
DROP POLICY IF EXISTS "user_learn_progress_insert_own" ON public.user_learn_progress;
DROP POLICY IF EXISTS "user_learn_progress_update_own" ON public.user_learn_progress;
DROP POLICY IF EXISTS "user_learn_progress_delete_own" ON public.user_learn_progress;
REVOKE ALL ON public.user_learn_progress FROM anon, authenticated;
GRANT  ALL ON public.user_learn_progress TO service_role;
