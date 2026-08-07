-- 115_drop_vestigial_credits_update_policy.sql
--
-- Why: `user_credits` carries an UPDATE policy that would let a client rewrite its own credit
-- balance, and nothing in the app needs it.
--
--     CREATE POLICY "credits_update_own" ON public.user_credits
--         FOR UPDATE TO ... USING (auth.uid() = user_id);
--
-- ⚠️ This is NOT currently exploitable, and the migration is hygiene rather than a fix. Verified
-- against the live database on 2026-08-07:
--
--     select grantee, privilege_type from information_schema.role_table_grants
--      where table_schema='public' and table_name='user_credits';
--     -- service_role: SELECT/INSERT/UPDATE/DELETE/...   (anon and authenticated: NOTHING)
--
-- A policy only ever NARROWS an existing table privilege — it cannot grant one. With `anon` and
-- `authenticated` holding no privileges on the table, PostgREST rejects their write before RLS
-- is consulted at all, so the policy is unreachable.
--
-- It is worth removing anyway, precisely because that reasoning is non-local: the policy reads
-- like an intentional "users may update their own credits", and it is one routine
-- `GRANT UPDATE ON public.user_credits TO authenticated` away from being exactly that. Every
-- balance write already goes through a SECURITY DEFINER RPC (`spend_credits`, `refund_credits`,
-- `ensure_credit_period`, `grant_tier_upgrade`, `revoke_tier_credits`), each of which is
-- REVOKEd from PUBLIC and granted only to `service_role`. Nothing reaches this table directly.
--
-- The SELECT policy stays: it is equally unreachable today, but it is the shape we would want
-- if a client ever did read its own balance directly, and it grants nothing.
--
-- Safe in either order with any code change — nothing reads or depends on this policy.

DROP POLICY IF EXISTS "credits_update_own" ON public.user_credits;

-- Belt and braces: make the absence of client privileges explicit rather than incidental, so a
-- future `GRANT ... TO authenticated` on a whole schema does not quietly re-open the path.
-- Idempotent, and a no-op against the current state.
REVOKE ALL ON public.user_credits FROM anon, authenticated;

COMMENT ON TABLE public.user_credits IS
    'Credit balances. SERVICE-ROLE ONLY: every write goes through a SECURITY DEFINER RPC '
    '(spend_credits / refund_credits / ensure_credit_period / grant_tier_upgrade / '
    'revoke_tier_credits). Do not GRANT to anon or authenticated — `remaining` is a generated '
    'column and the invariants live in those functions, not in constraints. See migration 115.';
