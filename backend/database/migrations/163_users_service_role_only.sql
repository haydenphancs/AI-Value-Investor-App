-- 163_users_service_role_only.sql
--
-- Why: A SIGNED-IN USER CAN UPDATE THEIR OWN `tier` — AND, ALMOST CERTAINLY, `is_admin`.
--
-- The live schema carries
--
--     CREATE POLICY users_update_own ON public.users FOR UPDATE USING (auth.uid() = id);
--
-- with no TO clause and no WITH CHECK, and Supabase projects ship
-- `ALTER DEFAULT PRIVILEGES … GRANT ALL ON TABLES TO anon, authenticated` (migrations
-- 107/109/119/131/132 all record this). So any holder of a valid user JWT can
-- `PATCH /rest/v1/users?id=eq.<self>` — the `apikey` header takes the project's anon key,
-- which Supabase treats as a PUBLIC, publishable value (it is not embedded in the iOS app
-- today, but it is not a secret either) — and set `tier = 'premium'`. `tier` is an ENTITLEMENT column: `ensure_credit_period` reads it to
-- size the monthly credit grant (`plan_credits.monthly_credits WHERE tier = v_tier`),
-- `grant_tier_upgrade` reads it, and `updates.py` gates the ticker limit on it. Its only
-- legitimate writer is `iap_service` as service_role, after Apple's signed transaction.
--
-- Migration 113 believed it had closed the same door for `is_admin`:
--
--     REVOKE UPDATE (is_admin) ON public.users FROM anon, authenticated;
--
-- It did not. Per the PostgreSQL REVOKE documentation: "if a role has been granted
-- privileges on a table, then revoking the same privileges for individual columns will have
-- no effect." A column-level REVOKE only removes column-level GRANTs; while the role holds
-- TABLE-level UPDATE (which the default privileges give it), the column stays writable. 113's
-- statement is therefore a no-op, and `is_admin` — the flag that opens every /admin/* route —
-- is very likely self-writable by any account today. The snapshot cannot show this
-- (`dump_schema.sh` ran `--no-privileges` until this change; the committed snapshot still
-- predates that fix), which is how it went unnoticed.
--
-- The fix is the TABLE-level revoke, which is correct whatever the current grant state is:
-- once `authenticated` holds no privilege on the table, no column can be written, no policy
-- can admit a row, and no future column-level slip can re-open it.
--
-- Who legitimately reads or writes public.users:
--   * the backend, through `get_supabase()` — the SERVICE_ROLE client (app/database.py:75),
--     which is re-asserted per request by `_reset_to_service_role` and never demoted;
--   * `handle_new_auth_user` (the auth.users trigger) and `account_auth_methods`, both
--     SECURITY DEFINER, so they run as their owner, not as the caller.
-- Nothing else. The iOS app has no Supabase REST client (zero `/rest/v1/` references) and
-- reaches user data only through the backend. So removing anon/authenticated access changes
-- nothing for the product and closes the escalation.
--
-- The three per-user policies are DROPPED, not left behind. With the grant gone they are
-- dead, and a dead permissive policy is a loaded gun: a later `GRANT … TO authenticated`
-- (or a Supabase Studio "enable access" click) would silently re-open the exact hole this
-- migration closes — migration 150 REVOKEd `index_detail_cache` but left
-- `index_detail_cache_public_read` behind, and it is still live today (164 is what finally
-- drops it). `users_service_all` stays. The same shape exists on `user_credits`: 115 revoked
-- the grant and left `credits_select_own` (no TO clause) in place — dropped below.
--
-- Deploy order does not matter: no application code path depends on these grants.
--
-- VERIFY AFTER APPLYING (the snapshot carries grants only once dump_schema.sh is re-run
-- after this batch; this is the live answer either way):
--
--     SELECT grantee, string_agg(privilege_type, ',')
--       FROM information_schema.role_table_grants
--      WHERE table_schema = 'public' AND table_name = 'users'
--      GROUP BY grantee;
--     -- expect: service_role only (plus postgres). No anon, no authenticated.
--
-- Idempotent: REVOKE/GRANT are declarative; every DROP POLICY is IF EXISTS.

REVOKE ALL ON public.users FROM anon, authenticated;
GRANT  ALL ON public.users TO service_role;

DROP POLICY IF EXISTS "users_select_own" ON public.users;
DROP POLICY IF EXISTS "users_insert_own" ON public.users;
DROP POLICY IF EXISTS "users_update_own" ON public.users;
-- `users_service_all` is kept as-is.

-- user_credits: 115 REVOKEd anon/authenticated but left this per-user SELECT policy; with
-- no grant it is dead, and dead permissive policies are exactly what this batch removes.
DROP POLICY IF EXISTS "credits_select_own" ON public.user_credits;

COMMENT ON COLUMN public.users.tier IS
    'Entitlement tier (free/pro/premium). Sizes the monthly credit grant via plan_credits '
    'and gates feature limits. Written ONLY by iap_service as service_role after Apple '
    'verification. Table is service-role-only since migration 163: anon/authenticated hold '
    'no privilege on public.users at all, so this column cannot be self-set through '
    'PostgREST. Do not GRANT the table back to either role.';

COMMENT ON COLUMN public.users.is_admin IS
    'Grants access to /api/v1/admin/*. Set manually — never by registration, signup trigger, '
    'or any application code path. Migration 113''s column-level REVOKE was inert while '
    'authenticated held table-level UPDATE; migration 163 revoked the TABLE, which is what '
    'actually makes this unwritable through PostgREST.';
