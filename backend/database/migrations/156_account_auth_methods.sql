-- 156_account_auth_methods.sql
--
-- Why: an Apple/Google account has NO password. Supabase provisions it through
-- `sign_in_with_id_token`, and nothing in this codebase ever writes one, so
-- `auth.users.encrypted_password` stays NULL. But `/auth/change-password` proves the current
-- password by attempting a real sign-in, which GoTrue rejects as `invalid_credentials` — so
-- those users were told **"Your current password is incorrect"** about a password that has
-- never existed, and burned one of five per-user attempts per 15 minutes each time they tried.
-- Reported from TestFlight. Neither the backend nor the iOS client could tell the difference:
-- the provider string ("apple"/"google") is a transient argument on the inbound
-- `POST /auth/oauth` body and is never persisted, and `public.users` has no provider column.
--
-- This function is the missing truth source. PostgREST does not expose the `auth` schema, so
-- `supabase.table(...)` cannot reach either `auth.users` or `auth.identities`; a SECURITY
-- DEFINER function in `public` is the only way for the service-role backend to read them in
-- one round trip.
--
-- Why `encrypted_password` and NOT the identity list: `auth.admin.update_user_by_id({password})`
-- — which is how BOTH `/auth/reset-password` and the new `/auth/set-password` write one — sets
-- that column without necessarily creating an `email` row in `auth.identities`. An
-- identity-based check would therefore report "no password" forever after the very flow this
-- migration exists to enable. `providers` is returned alongside purely so the UI can name the
-- method ("sign in without Apple"); it is never the has-password signal.
--
-- Returns NULL — not `{"has_password": false, ...}` — when no `auth.users` row matches, because
-- "this account has no password" and "we cannot see this account" demand opposite handling:
-- `GET /users/me` fails OPEN on NULL (keeps today's behaviour) while `/auth/set-password` fails
-- CLOSED (503), since setting a password without confirming none exists would overwrite an
-- existing one with no proof of the current — the exact attack change-password guards.

CREATE OR REPLACE FUNCTION public.account_auth_methods(p_user_id uuid)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = p_user_id) THEN NULL
        ELSE jsonb_build_object(
            'has_password', (
                SELECT (u.encrypted_password IS NOT NULL AND u.encrypted_password <> '')
                  FROM auth.users u
                 WHERE u.id = p_user_id
            ),
            'providers', COALESCE((
                SELECT array_agg(DISTINCT i.provider ORDER BY i.provider)
                  FROM auth.identities i
                 WHERE i.user_id = p_user_id
            ), ARRAY[]::text[])
        )
    END;
$$;

COMMENT ON FUNCTION public.account_auth_methods(uuid) IS
    'Returns {has_password, providers} for an account, or NULL if no auth.users row matches. '
    'has_password reads auth.users.encrypted_password (authoritative: an admin password write '
    'does not necessarily create an email identity). service_role only — see the REVOKE below.';

-- Load-bearing. Without it any holder of the shipped anon key could probe an arbitrary uuid
-- for whether that account has a password, which is an account-shape oracle on a finance app.
-- The backend calls this as service_role with the id taken from the verified JWT subject,
-- never from a request body.
REVOKE ALL ON FUNCTION public.account_auth_methods(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.account_auth_methods(uuid) FROM anon;
REVOKE ALL ON FUNCTION public.account_auth_methods(uuid) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.account_auth_methods(uuid) TO service_role;
