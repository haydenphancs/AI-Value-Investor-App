-- 169_service_role_grants_for_ungranted_tables.sql
--
-- Why: THREE TABLES THE BACKEND READS EVERY DAY HAVE NO PRIVILEGE FOR service_role AT ALL.
--
-- `social_mentions_history`, `daily_briefings` and `market_insights` were created in the
-- Supabase SQL editor (no migration in this folder creates them) at a time when the project
-- carried no default privileges in `public` (the 2026-09-11 privileged dump shows
-- `ALTER DEFAULT PRIVILEGES` only for auth/extensions/graphql/realtime/storage). Migration 078
-- then enabled RLS, REVOKEd anon/authenticated and created `<table>_service_all` policies —
-- but a POLICY only filters rows for a role that already holds the TABLE privilege, and
-- nothing ever GRANTed one. Postgres checks the table GRANT before RLS (086's header), so a
-- policy over a grant-less table admits nobody. The snapshot shows no `GRANT … ON TABLE` for
-- any of the three; every other public table has one.
--
-- Production (Railway, 2026-09-11): `permission denied for table social_mentions_history
-- (42501)` on every 7-day mentions read (social_mentions_service.py) and on the daily
-- snapshot upsert — Reddit mentions were silently 0 app-wide. `daily_briefings` and
-- `market_insights` failed the same way behind a bare `except Exception: pass` in
-- home_service.py (now logged; the route falls back to SPY-derived copy).
--
-- Why nothing caught it: tests/test_table_grants_service_role_only.py replays migrations and
-- asserts the ABSENCE of anon/authenticated grants; it never asserted the PRESENCE of the
-- service_role grant, and the snapshot was dumped --no-privileges until 2026-09-11.
-- tests/test_snapshot_grants_parity.py now reads the privileged dump and fails on any
-- public table without a full service_role grant.
--
-- Also in this file:
--   * `ticker_news_cache` still grants SELECT to anon and authenticated (default-privilege
--     era; no migration ever touched its grants, so the replay test could not see it). It
--     holds FMP-licensed news (End-User Display Rights, auth.md §1a); its only policy is
--     service_role, so the grant is inert today and one permissive policy away from a second
--     door. Closed here, same posture as 164.
--   * `health_check_cache`, `profit_power_cache`, `revenue_breakdown_cache`: service_role held
--     SELECT,INSERT,UPDATE but not DELETE. Nothing deletes from them today (upsert only); a
--     future expiry sweep would 42501. Normalised to ALL so every backend table has one shape.
--   * `user_settings` and `device_tokens` (102) still carried `*_own` policies plus
--     INSERT/UPDATE(/DELETE) grants to `authenticated`. The iOS app has no Supabase client and
--     the backend writes both as service_role (user_settings_service.py, push_service.py), so
--     the client write path is a door nobody legitimately uses — the same argument 165 made
--     for the guest-partitioned tables. Closed here.
--   * Seven service-role-only tables created by migrations that never wrote an explicit
--     REVOKE (`ai_insight_budget`, `chat_usage_budget`, `geopolitical_macro_audit`,
--     `guest_report_budget`, `price_catalyst_audit`, `updates_insight_state`,
--     `agent_personas`): live grants are already service_role-only (no default privileges
--     in `public`), so these REVOKEs are no-ops that let the replay test declare them.
--     `agent_personas` keeps its (inert) `personas_select_all` policy — that is the AI
--     layer's call.
--
-- No sequences: the three Studio-born tables use `uuid DEFAULT gen_random_uuid()`; every
-- `public.*_id_seq` already grants SELECT,USAGE to service_role (167-era dump).
-- Idempotent: GRANT/REVOKE are declarative; every DROP POLICY is IF EXISTS.
--
-- VERIFY AFTER APPLYING:
--
--     SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY privilege_type)
--       FROM information_schema.role_table_grants
--      WHERE table_schema = 'public'
--        AND table_name IN ('social_mentions_history','daily_briefings','market_insights',
--                           'ticker_news_cache','health_check_cache','profit_power_cache',
--                           'revenue_breakdown_cache','user_settings','device_tokens')
--      GROUP BY 1, 2 ORDER BY 1, 2;
--     -- expect: exactly one row per table, grantee = service_role, privileges =
--     -- DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE. No anon, no authenticated.
--
-- Then re-run backend/scripts/dump_schema.sh. tests/test_snapshot_grants_parity.py carries a
-- _PENDING_* allow-list naming this migration; once the dump shows it applied the entries
-- must be deleted (the test says so), and if the dump still lacks a grant the test fails for
-- the real reason.

-- ---- Studio-born tables: the missing privilege ------------------------------------------
GRANT  ALL ON public.social_mentions_history TO service_role;
GRANT  ALL ON public.daily_briefings         TO service_role;
GRANT  ALL ON public.market_insights         TO service_role;
-- Restated from 078 so this file is the complete posture on its own.
REVOKE ALL ON public.social_mentions_history FROM anon, authenticated;
REVOKE ALL ON public.daily_briefings         FROM anon, authenticated;
REVOKE ALL ON public.market_insights         FROM anon, authenticated;

-- ---- ticker_news_cache: close the default-privilege-era read grant ----------------------
REVOKE ALL ON public.ticker_news_cache FROM anon, authenticated;
GRANT  ALL ON public.ticker_news_cache TO service_role;

-- ---- partial service_role grants → ALL --------------------------------------------------
GRANT  ALL ON public.health_check_cache      TO service_role;
GRANT  ALL ON public.profit_power_cache      TO service_role;
GRANT  ALL ON public.revenue_breakdown_cache TO service_role;

-- ---- user_settings / device_tokens: service-role-only, like every other user table -------
DROP POLICY IF EXISTS "user_settings_select_own" ON public.user_settings;
DROP POLICY IF EXISTS "user_settings_insert_own" ON public.user_settings;
DROP POLICY IF EXISTS "user_settings_update_own" ON public.user_settings;
REVOKE ALL ON public.user_settings FROM anon, authenticated;
GRANT  ALL ON public.user_settings TO service_role;

DROP POLICY IF EXISTS "device_tokens_select_own" ON public.device_tokens;
DROP POLICY IF EXISTS "device_tokens_insert_own" ON public.device_tokens;
DROP POLICY IF EXISTS "device_tokens_update_own" ON public.device_tokens;
DROP POLICY IF EXISTS "device_tokens_delete_own" ON public.device_tokens;
REVOKE ALL ON public.device_tokens FROM anon, authenticated;
GRANT  ALL ON public.device_tokens TO service_role;

-- ---- explicit REVOKEs for tables whose creating migration wrote none (no-ops live) ------
REVOKE ALL ON public.ai_insight_budget        FROM anon, authenticated;
REVOKE ALL ON public.chat_usage_budget        FROM anon, authenticated;
REVOKE ALL ON public.geopolitical_macro_audit FROM anon, authenticated;
REVOKE ALL ON public.guest_report_budget      FROM anon, authenticated;
REVOKE ALL ON public.price_catalyst_audit     FROM anon, authenticated;
REVOKE ALL ON public.updates_insight_state    FROM anon, authenticated;
REVOKE ALL ON public.agent_personas           FROM anon, authenticated;
GRANT  ALL ON public.ai_insight_budget        TO service_role;
GRANT  ALL ON public.chat_usage_budget        TO service_role;
GRANT  ALL ON public.geopolitical_macro_audit TO service_role;
GRANT  ALL ON public.guest_report_budget      TO service_role;
GRANT  ALL ON public.price_catalyst_audit     TO service_role;
GRANT  ALL ON public.updates_insight_state    TO service_role;
GRANT  ALL ON public.agent_personas           TO service_role;

COMMENT ON TABLE public.social_mentions_history IS
    'Daily ApeWisdom Reddit-mention snapshots per ticker (30-day retention), written by the '
    'social snapshot job and read by social_mentions_service for the 7-day counts. '
    'Service-role-only; migration 169 added the table GRANT that 078 omitted.';
