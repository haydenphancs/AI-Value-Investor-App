-- 105_password_changed_at.sql
--
-- Why: password reset is being added (POST /auth/forgot-password, /auth/reset-password,
-- /auth/change-password), and without this column a reset would NOT log out a thief.
--
-- The app does not use Supabase's own session tokens — it mints its OWN JWTs
-- (`core/security.create_access_token` / `create_refresh_token`) and validates them with
-- nothing but a signature + expiry check. So changing the Supabase password leaves every
-- previously issued app token fully valid: an attacker holding a stolen refresh token keeps
-- access for up to REFRESH_TOKEN_EXPIRE_MINUTES (7 days) AFTER the victim resets their
-- password. That defeats the main reason people reset passwords in the first place.
--
-- Fix: record when the password last changed, and reject any token minted before that
-- moment. Our JWTs already carry an `iat` claim, so no token-shape change is needed:
--
--     if user.password_changed_at and token.iat < user.password_changed_at: reject
--
-- Enforced in THREE places (see app/dependencies.py and app/api/v1/endpoints/auth.py):
--   * `get_current_user` — already does `select("*")` on public.users, so the check costs
--     nothing extra.
--   * `get_current_user_or_guest` — same check, same free `select("*")`. This is the
--     dependency MOST authenticated routes actually use (chat, ticker reports, research,
--     analytics, users/me/credits) plus everything reached via `get_watchlist_identity` and
--     `get_learn_identity` (watchlist, portfolios, home, learn, updates).
--   * `POST /auth/refresh` — blocks minting NEW access tokens from a pre-reset refresh
--     token, which is what actually caps an attacker's window.
--
-- CORRECTION (2026-08-01): this header previously listed only the first and third, and
-- claimed `get_current_user` "covers every endpoint that resolves a full user row". That was
-- false — `get_current_user_or_guest` resolves a full user row and did NOT check, so a stolen
-- access token kept reading and writing the victim's data, and spending their credits, for
-- its full 24h life after a reset. Added in app/dependencies.py; regression test at
-- backend/tests/test_access_token_guards.py. No DDL change; comment only.
--
-- Known, accepted limitation: endpoints that use the decode-only `get_current_user_id`
-- (no DB round-trip) will still accept an unexpired pre-reset ACCESS token until it
-- expires. Refresh is blocked immediately, so the worst case shrinks from 7 days to one
-- access-token lifetime. Closing it fully would mean a DB lookup on every request; not
-- worth it at this scale, and documented rather than silently ignored.
--
-- NULL means "never changed since signup" and is treated as no restriction, so existing
-- users and sessions are unaffected by applying this.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

COMMENT ON COLUMN public.users.password_changed_at IS
    'When the account password was last changed. Any app JWT whose `iat` predates this is '
    'rejected, so a password reset invalidates sessions an attacker may hold. NULL = never '
    'changed since signup (no restriction).';

-- No index: the column is only ever read via the existing single-row
-- `SELECT * FROM users WHERE id = ?` lookup, which uses the primary key.
