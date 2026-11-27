-- 213_apple_sign_in_grants.sql
--
-- Why: account deletion never revoked a user's Sign in with Apple grant (sign-in audit SOC-04,
-- 2026-11-26). Apple's account-deletion guidance asks an app that offers Sign in with Apple to
-- revoke the user's tokens through its REST API (`POST https://appleid.apple.com/auth/revoke`)
-- when they delete their account — a review risk on every submission. Without it Caydex stays
-- authorised on the person's Apple ID after deletion, so a later "Continue with Apple" is a
-- RETURNING authorization: Apple sends no name and the new account is named after the relay
-- address.
--
-- Revocation needs a token Apple issued to US. GoTrue's id_token grant (how `/auth/oauth` signs
-- in) never receives one, so Supabase cannot revoke on our behalf. From app build 1.04 the app
-- sends the sign-in's single-use `authorizationCode` with `/auth/oauth`; the backend exchanges it
-- at `/auth/token` inside that request (`app/services/apple_sign_in_service.py`) and keeps the
-- refresh token here. `DELETE /users/me` reads it and revokes BEFORE `admin.delete_user` — this
-- table cascades from public.users, so afterwards it is gone with the account.
--
-- Shape: ONE row per Caydex user (the newest Apple grant wins — revoking any refresh token of the
-- grant ends the whole authorization for the app). `apple_sub` = the Apple user id (the identity
-- token's `sub`, equal to the app's `credential.user`); `client_id` = the bundle id the token was
-- issued to (`/auth/revoke` must name the same client).
--
-- The refresh token is stored as plain text, service-role-only. It is useless on its own: every
-- call Apple accepts with it must also carry a client_secret signed by our Sign in with Apple
-- `.p8` key, which lives only in Railway's environment (APPLE_SIGN_IN_PRIVATE_KEY), not here.
-- Encrypting it with SECRET_KEY was rejected: rotating that key is the global kill switch for app
-- and widget tokens (auth.md §8a), and it would silently make every stored grant unrevocable.
--
-- Account-only (an Apple sign-in always has a public.users row — `/auth/oauth` checks it before
-- storing), so the FK to public.users with ON DELETE CASCADE is right; it is NOT in
-- `_UNLINKED_USER_TABLES`. Service-role-only (database.md): the iOS app has no Supabase client.
--
-- Apply BEFORE or AFTER the web deploy — either order is safe: the code's upsert failing on a
-- missing table is logged (STORE_FAILED) and sign-in continues; the deletion read failing is
-- logged (READ_FAILED) and deletion continues. Until the owner sets APPLE_SIGN_IN_KEY_ID /
-- APPLE_SIGN_IN_PRIVATE_KEY on Railway nothing is written here at all.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, DROP POLICY IF EXISTS before CREATE, declarative
-- REVOKE / GRANT, re-runnable COMMENTs. One transaction.

BEGIN;

CREATE TABLE IF NOT EXISTS public.apple_sign_in_grants (
    user_id       UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    apple_sub     TEXT NOT NULL CHECK (char_length(apple_sub) BETWEEN 1 AND 255),
    client_id     TEXT NOT NULL CHECK (char_length(client_id) BETWEEN 1 AND 255),
    refresh_token TEXT NOT NULL CHECK (char_length(refresh_token) BETWEEN 1 AND 4096),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.apple_sign_in_grants IS
    'Sign in with Apple refresh token per user, kept ONLY so DELETE /users/me can revoke the '
    'Apple grant (Apple account-deletion guidance; SOC-04). Written by /auth/oauth from the '
    'sign-in''s authorization code (app >= 1.04), read once at deletion, cascades with the '
    'account. Service-role-only; the token is unusable without the .p8-signed client secret.';
COMMENT ON COLUMN public.apple_sign_in_grants.apple_sub IS
    'Apple user id (identity token sub = the app''s credential.user). The exchange stores a '
    'token only when the code''s subject equals the verified identity token''s.';
COMMENT ON COLUMN public.apple_sign_in_grants.refresh_token IS
    'Apple refresh token. A credential: never logged, never returned by any route.';

-- ── RLS + grants: service_role only ──────────────────────────────────────────────────────
ALTER TABLE public.apple_sign_in_grants ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "apple_sign_in_grants_service_all" ON public.apple_sign_in_grants;
CREATE POLICY "apple_sign_in_grants_service_all" ON public.apple_sign_in_grants
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.apple_sign_in_grants FROM anon, authenticated;

-- A table with no GRANT is owner-only, not open (169's lesson): grant service_role explicitly.
-- UUID primary key: no sequence to grant.
GRANT ALL ON public.apple_sign_in_grants TO service_role;

COMMIT;

-- VERIFY (read-only) right after applying → one row: t | t | t | t | t
-- SELECT
--   to_regclass('public.apple_sign_in_grants') IS NOT NULL,
--   (SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass('public.apple_sign_in_grants')),
--   NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants
--                WHERE table_schema = 'public' AND table_name = 'apple_sign_in_grants'
--                  AND grantee IN ('anon', 'authenticated')),
--   (SELECT count(DISTINCT privilege_type) FROM information_schema.role_table_grants
--     WHERE table_schema = 'public' AND table_name = 'apple_sign_in_grants'
--       AND grantee = 'service_role'
--       AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')) = 4,
--   EXISTS (SELECT 1 FROM pg_constraint
--            WHERE conrelid = to_regclass('public.apple_sign_in_grants') AND contype = 'f'
--              AND confdeltype = 'c');
