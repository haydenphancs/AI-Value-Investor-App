-- 214_auth_refresh_tokens.sql
--
-- Why: signing out did nothing on the server, and a leaked refresh token lived forever
-- (audit 2026-11-26: SEC-3, AUTH-BE-10).
--
--   * The app's refresh tokens were stateless JWTs with no id. `/auth/refresh` minted a new
--     7-day refresh token on every call and never retired the one it was given, so anyone who
--     copied one (a device backup, a debug log, a shared phone) could refresh before day 7, get
--     another 7 days, and repeat forever — after the owner tapped Sign Out on every device.
--   * `/auth/logout` only logged.
--   * The only kill switches were `users.password_changed_at` (migration 105) and rotating
--     SECRET_KEY for every user at once. An Apple/Google account has no password, so it had no
--     lever at all short of deleting the account.
--
-- What this adds:
--
--   1. `auth_refresh_families` — one row per SESSION: a sign-in on one device. The refresh
--      tokens a session rotates through all share its `family_id`, which the tokens carry as
--      their `sid` claim (access tokens too). Revoking a session is ONE update of this row.
--   2. `auth_refresh_tokens` — one row per refresh token (`jti`). A token is single-use:
--      `/auth/refresh` retires it (`revoked_at`, `replaced_by`) and mints its successor. A
--      retired token presented AGAIN is reuse: someone else holds a copy. Then the whole
--      session is revoked, so the thief and the real device both have to sign in again —
--      unless it arrives within a short grace window (a lost response retried by the app, or
--      the app's restore and pre-flight refreshes racing), when a sibling is issued instead.
--   3. `users.sessions_revoked_at` — "Sign out of all devices": every token issued before it
--      is refused, on refresh and on every route that reads the users row, exactly like
--      `password_changed_at`. This is the lever an Apple/Google account was missing.
--   4. Four SECURITY DEFINER functions that do each state change in ONE transaction, under a
--      per-user advisory lock, so a rotation can never interleave with a sign-out or a
--      sign-out-all of the same account (a rotation that read "alive" and wrote after a
--      revocation would resurrect the session). PostgREST cannot express a lock or a
--      multi-statement transaction; an RPC is the only way to get both in one round trip.
--   5. `account_has_identity` — re-authentication with Apple/Google before an account deletion
--      checks that the provider subject is ALREADY linked to the caller before the token goes
--      to GoTrue, because GoTrue's id-token sign-in CREATES an account for an unknown subject.
--      PostgREST cannot reach `auth.identities`, so this is a definer function (like 156).
--
-- Legacy tokens (minted before this migration) carry no `jti`. They keep working until they
-- expire (7 days): the backend derives an id from a hash of the token and records it here on
-- first use, so a legacy token is single-use too from the first refresh after deploy — never
-- a mass sign-out on deploy.
--
-- Expired rows are pruned by the functions themselves, per user, on every sign-in and refresh
-- (a token past its `exp` is refused by the JWT check before any row is read, so its row is
-- only history). No background job.
--
-- Schema: both tables FK to public.users ON DELETE CASCADE, so account deletion removes them
-- (`auth.users` → `public.users` → here). Account-only by construction: guests hold no tokens.
--
-- DEPLOY ORDER: apply this BEFORE deploying the backend that uses it. The backend degrades
-- (stateless tokens, logged at ERROR) if it runs first, so the order is not a sign-in outage,
-- but nothing is revocable until this lands.
--
-- Idempotent: IF NOT EXISTS / OR REPLACE throughout; the grants are re-runnable.

-- ── users.sessions_revoked_at ───────────────────────────────────────────────

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS sessions_revoked_at TIMESTAMPTZ;

COMMENT ON COLUMN public.users.sessions_revoked_at IS
    'Set by "Sign out of all devices" (POST /auth/sign-out-all). Every app token whose iat is '
    'before it (whole seconds) is refused, on refresh and on every route that reads this row — '
    'the same rule as password_changed_at (migration 105). Migration 214.';

-- ── Sessions (refresh-token families) ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.auth_refresh_families (
    family_id     UUID PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- When the credential that started this session was verified (sign-in, provider sign-in,
    -- confirm-email, password change). Carried into every token as `auth_time`; account
    -- deletion treats a recent one as recent authentication. NULL for a session that began
    -- from a legacy token, whose sign-in time is unknown.
    auth_time     TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    revoke_reason TEXT,
    CONSTRAINT auth_refresh_families_revoke_reason_check
        CHECK (revoke_reason IS NULL OR revoke_reason IN ('logout', 'reuse', 'signout_all')),
    CONSTRAINT auth_refresh_families_revoked_pair_check
        CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_auth_refresh_families_user
    ON public.auth_refresh_families(user_id);

COMMENT ON TABLE public.auth_refresh_families IS
    'One row per app session (a sign-in on one device): the family of refresh tokens it rotates '
    'through, carried in every token as `sid`. revoked_at ends the session: logout, reuse of a '
    'retired token, or sign-out-all. Written only by the 214 functions. Migration 214.';

-- ── Refresh tokens ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.auth_refresh_tokens (
    jti          UUID PRIMARY KEY,
    family_id    UUID NOT NULL REFERENCES public.auth_refresh_families(family_id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    -- The token this one was minted from (NULL for the first token of a session).
    parent_jti   UUID,
    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    -- Set when this token was USED (rotated). A second use is reuse: see rotate_refresh_token.
    revoked_at   TIMESTAMPTZ,
    -- Its first successor. Later siblings (grace-window re-issues) point back via parent_jti.
    replaced_by  UUID,
    -- Successors minted from this token: 1 after rotation, +1 per grace-window re-issue.
    child_count  INTEGER NOT NULL DEFAULT 0 CHECK (child_count >= 0),
    -- A pre-214 token with no jti, recorded on first use under an id derived from its hash.
    legacy       BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_family
    ON public.auth_refresh_tokens(family_id);
CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_user_expiry
    ON public.auth_refresh_tokens(user_id, expires_at);

COMMENT ON TABLE public.auth_refresh_tokens IS
    'One row per app refresh token (jti claim). Single-use: /auth/refresh sets revoked_at + '
    'replaced_by and mints the successor; a retired token presented again outside the grace '
    'window revokes its whole family (auth_refresh_families). Rows past expires_at are pruned '
    'per user by the 214 functions. Written only by those functions. Migration 214.';

-- ── RLS + grants: SERVICE-ROLE ONLY (database.md; the iOS app has no Supabase client) ───────

ALTER TABLE public.auth_refresh_families ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_refresh_tokens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "auth_refresh_families_service_all" ON public.auth_refresh_families;
CREATE POLICY "auth_refresh_families_service_all" ON public.auth_refresh_families
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "auth_refresh_tokens_service_all" ON public.auth_refresh_tokens;
CREATE POLICY "auth_refresh_tokens_service_all" ON public.auth_refresh_tokens
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.auth_refresh_families FROM anon, authenticated;
REVOKE ALL ON public.auth_refresh_tokens FROM anon, authenticated;
GRANT ALL ON public.auth_refresh_families TO service_role;
GRANT ALL ON public.auth_refresh_tokens TO service_role;

-- ── Helpers ─────────────────────────────────────────────────────────────────

-- Serialises every session change of ONE account. Every function below takes it first, so a
-- rotation, a logout and a sign-out-all of the same account run one at a time; different
-- accounts never wait on each other (a hash collision only makes two of them take turns).
CREATE OR REPLACE FUNCTION public._lock_refresh_sessions(p_user_id uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT pg_advisory_xact_lock(hashtextextended('auth_refresh_sessions:' || p_user_id::text, 0));
$$;

-- Deletes this account's refresh-token rows that expired more than a day ago, then its
-- sessions left with no token. A token past its `exp` is refused by the JWT check before any
-- row is read, so nothing that could still be presented is ever pruned.
CREATE OR REPLACE FUNCTION public._prune_refresh_sessions(p_user_id uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    DELETE FROM public.auth_refresh_tokens t
     WHERE t.user_id = p_user_id
       AND t.expires_at < now() - interval '1 day';
    DELETE FROM public.auth_refresh_families f
     WHERE f.user_id = p_user_id
       AND f.created_at < now() - interval '1 day'
       AND NOT EXISTS (
           SELECT 1 FROM public.auth_refresh_tokens t WHERE t.family_id = f.family_id
       );
$$;

-- ── start_refresh_family: a new session (every token-minting sign-in) ───────

CREATE OR REPLACE FUNCTION public.start_refresh_family(
    p_user_id    uuid,
    p_family_id  uuid,
    p_jti        uuid,
    p_expires_at timestamptz,
    p_auth_time  timestamptz
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
BEGIN
    IF p_user_id IS NULL OR p_family_id IS NULL OR p_jti IS NULL OR p_expires_at IS NULL THEN
        RAISE EXCEPTION 'start_refresh_family: a required argument is NULL';
    END IF;

    PERFORM public._lock_refresh_sessions(p_user_id);

    IF NOT EXISTS (SELECT 1 FROM public.users u WHERE u.id = p_user_id) THEN
        RETURN jsonb_build_object('status', 'account_missing');
    END IF;

    INSERT INTO public.auth_refresh_families (family_id, user_id, created_at, auth_time)
    VALUES (p_family_id, p_user_id, now(), p_auth_time);

    INSERT INTO public.auth_refresh_tokens (jti, family_id, user_id, parent_jti, issued_at, expires_at)
    VALUES (p_jti, p_family_id, p_user_id, NULL, now(), p_expires_at);

    PERFORM public._prune_refresh_sessions(p_user_id);

    RETURN jsonb_build_object('status', 'started', 'family_id', p_family_id);
END;
$$;

-- ── rotate_refresh_token: /auth/refresh ─────────────────────────────────────
--
-- Statuses (the backend maps each one; anything else is a 503):
--   rotated          the presented token is retired; p_new_jti is its successor
--   grace            the presented token was already used, within p_grace_seconds, and its
--                    session is alive: p_new_jti is issued as a SIBLING (a lost response retried,
--                    or two refreshes racing). At most p_max_children successors per token.
--   reuse            the presented token was already used, outside the grace window (or past
--                    the sibling cap): the session is revoked NOW, reason 'reuse'
--   revoked          the session had already ended (logout / reuse / signout_all)
--   evicted          the token predates password_changed_at or sessions_revoked_at
--   expired          the token's row says it has expired (the JWT check normally refuses first)
--   unknown          a tracked jti with no row (pruned, or the table was emptied)
--   mismatch         the row belongs to another account (impossible without SECRET_KEY)
--   account_missing  no public.users row
CREATE OR REPLACE FUNCTION public.rotate_refresh_token(
    p_user_id        uuid,
    p_jti            uuid,
    p_token_iat      bigint,
    p_token_exp      bigint,
    p_legacy         boolean,
    p_new_jti        uuid,
    p_new_family_id  uuid,
    p_new_expires_at timestamptz,
    p_grace_seconds  integer,
    p_max_children   integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_now        timestamptz := now();
    v_pw         timestamptz;
    v_sr         timestamptz;
    v_stamp      timestamptz;
    v_tok        public.auth_refresh_tokens%ROWTYPE;
    v_fam        public.auth_refresh_families%ROWTYPE;
    v_age        double precision;
    v_auth_epoch bigint;
BEGIN
    IF p_user_id IS NULL OR p_jti IS NULL OR p_new_jti IS NULL OR p_token_iat IS NULL
       OR p_new_expires_at IS NULL THEN
        RAISE EXCEPTION 'rotate_refresh_token: a required argument is NULL';
    END IF;

    PERFORM public._lock_refresh_sessions(p_user_id);

    -- Re-checked HERE, under the lock, although the backend checks both before calling: a
    -- sign-out-all that commits between that check and this rotation must still win.
    SELECT u.password_changed_at, u.sessions_revoked_at INTO v_pw, v_sr
      FROM public.users u WHERE u.id = p_user_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'account_missing');
    END IF;
    v_stamp := GREATEST(v_pw, v_sr);   -- GREATEST ignores NULLs
    -- Whole seconds, as everywhere else: `iat` has no fraction, the stamp has microseconds.
    IF v_stamp IS NOT NULL AND to_timestamp(p_token_iat) < date_trunc('second', v_stamp) THEN
        RETURN jsonb_build_object('status', 'evicted');
    END IF;

    SELECT * INTO v_tok FROM public.auth_refresh_tokens t WHERE t.jti = p_jti;

    IF NOT FOUND THEN
        IF NOT COALESCE(p_legacy, false) THEN
            RETURN jsonb_build_object('status', 'unknown');
        END IF;
        -- First use of a pre-214 token: record it as already used, under a new session, and
        -- mint its successor there. A second use of the same legacy token then finds this row.
        IF p_new_family_id IS NULL OR p_token_exp IS NULL THEN
            RAISE EXCEPTION 'rotate_refresh_token: a legacy token needs p_new_family_id and p_token_exp';
        END IF;
        INSERT INTO public.auth_refresh_families (family_id, user_id, created_at, auth_time)
        VALUES (p_new_family_id, p_user_id, v_now, NULL);
        INSERT INTO public.auth_refresh_tokens
            (jti, family_id, user_id, parent_jti, issued_at, expires_at,
             revoked_at, replaced_by, child_count, legacy)
        VALUES
            (p_jti, p_new_family_id, p_user_id, NULL, to_timestamp(p_token_iat),
             to_timestamp(p_token_exp), v_now, p_new_jti, 1, true);
        INSERT INTO public.auth_refresh_tokens (jti, family_id, user_id, parent_jti, issued_at, expires_at)
        VALUES (p_new_jti, p_new_family_id, p_user_id, p_jti, v_now, p_new_expires_at);
        PERFORM public._prune_refresh_sessions(p_user_id);
        RETURN jsonb_build_object(
            'status', 'rotated', 'family_id', p_new_family_id, 'auth_time', NULL, 'legacy', true
        );
    END IF;

    IF v_tok.user_id <> p_user_id THEN
        RETURN jsonb_build_object('status', 'mismatch');
    END IF;

    SELECT * INTO v_fam FROM public.auth_refresh_families f WHERE f.family_id = v_tok.family_id;
    IF v_fam.revoked_at IS NOT NULL THEN
        RETURN jsonb_build_object(
            'status', 'revoked', 'reason', v_fam.revoke_reason, 'family_id', v_fam.family_id
        );
    END IF;

    IF v_tok.expires_at <= v_now THEN
        RETURN jsonb_build_object('status', 'expired', 'family_id', v_fam.family_id);
    END IF;

    v_auth_epoch := floor(EXTRACT(EPOCH FROM v_fam.auth_time))::bigint;

    IF v_tok.revoked_at IS NULL THEN
        UPDATE public.auth_refresh_tokens
           SET revoked_at = v_now, replaced_by = p_new_jti, child_count = child_count + 1
         WHERE jti = p_jti;
        INSERT INTO public.auth_refresh_tokens (jti, family_id, user_id, parent_jti, issued_at, expires_at)
        VALUES (p_new_jti, v_fam.family_id, p_user_id, p_jti, v_now, p_new_expires_at);
        PERFORM public._prune_refresh_sessions(p_user_id);
        RETURN jsonb_build_object(
            'status', 'rotated', 'family_id', v_fam.family_id, 'auth_time', v_auth_epoch
        );
    END IF;

    v_age := EXTRACT(EPOCH FROM (v_now - v_tok.revoked_at));
    IF v_age <= GREATEST(COALESCE(p_grace_seconds, 0), 0)
       AND v_tok.child_count < GREATEST(COALESCE(p_max_children, 1), 1) THEN
        UPDATE public.auth_refresh_tokens SET child_count = child_count + 1 WHERE jti = p_jti;
        INSERT INTO public.auth_refresh_tokens (jti, family_id, user_id, parent_jti, issued_at, expires_at)
        VALUES (p_new_jti, v_fam.family_id, p_user_id, p_jti, v_now, p_new_expires_at);
        RETURN jsonb_build_object(
            'status', 'grace', 'family_id', v_fam.family_id, 'auth_time', v_auth_epoch,
            'age_seconds', v_age, 'child_count', v_tok.child_count + 1
        );
    END IF;

    UPDATE public.auth_refresh_families
       SET revoked_at = v_now, revoke_reason = 'reuse'
     WHERE family_id = v_fam.family_id;
    RETURN jsonb_build_object(
        'status', 'reuse', 'family_id', v_fam.family_id, 'age_seconds', v_age,
        'child_count', v_tok.child_count
    );
END;
$$;

-- ── revoke_refresh_family: /auth/logout (one session) ───────────────────────

CREATE OR REPLACE FUNCTION public.revoke_refresh_family(
    p_user_id   uuid,
    p_family_id uuid,
    p_reason    text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_fam public.auth_refresh_families%ROWTYPE;
BEGIN
    IF p_user_id IS NULL OR p_family_id IS NULL OR p_reason IS NULL THEN
        RAISE EXCEPTION 'revoke_refresh_family: a required argument is NULL';
    END IF;
    IF p_reason NOT IN ('logout', 'reuse') THEN
        RAISE EXCEPTION 'revoke_refresh_family: unknown reason %', p_reason;
    END IF;

    PERFORM public._lock_refresh_sessions(p_user_id);

    -- Scoped to the caller: another account's session id reads as unknown, never as revoked.
    SELECT * INTO v_fam FROM public.auth_refresh_families f
     WHERE f.family_id = p_family_id AND f.user_id = p_user_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'unknown');
    END IF;
    IF v_fam.revoked_at IS NOT NULL THEN
        RETURN jsonb_build_object('status', 'already_revoked', 'reason', v_fam.revoke_reason);
    END IF;

    UPDATE public.auth_refresh_families
       SET revoked_at = now(), revoke_reason = p_reason
     WHERE family_id = p_family_id;
    RETURN jsonb_build_object('status', 'revoked');
END;
$$;

-- ── revoke_all_refresh_sessions: "Sign out of all devices" ──────────────────

CREATE OR REPLACE FUNCTION public.revoke_all_refresh_sessions(p_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_stamp    timestamptz;
    v_families integer;
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'revoke_all_refresh_sessions: p_user_id is NULL';
    END IF;

    PERFORM public._lock_refresh_sessions(p_user_id);

    -- The NEXT whole second, read AFTER the lock (review 2026-11-26). Every eviction check floors
    -- the stamp to whole seconds because `iat` has none, so a stamp inside second S spared every
    -- token minted earlier in S — e.g. a thief refreshing once a second kept an access token for
    -- up to 60 minutes. Rounding UP evicts all of S. Nothing legitimate is minted in S: this call
    -- hands the caller no tokens. `clock_timestamp()`, not `now()`: `now()` is the transaction
    -- start, which can precede a rotation that committed while this call waited for the lock.
    v_stamp := date_trunc('second', clock_timestamp()) + interval '1 second';

    -- The stamp is what evicts: every token minted before it is refused on refresh and on
    -- every route that reads the users row — legacy tokens with no session row included.
    UPDATE public.users SET sessions_revoked_at = v_stamp WHERE id = p_user_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'account_missing');
    END IF;

    -- The session rows are revoked too, so they read as ended rather than merely stale.
    UPDATE public.auth_refresh_families
       SET revoked_at = v_stamp, revoke_reason = 'signout_all'
     WHERE user_id = p_user_id AND revoked_at IS NULL;
    GET DIAGNOSTICS v_families = ROW_COUNT;

    PERFORM public._prune_refresh_sessions(p_user_id);

    RETURN jsonb_build_object(
        'status', 'revoked', 'families', v_families,
        'sessions_revoked_at', to_char(v_stamp AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
    );
END;
$$;

-- ── account_has_identity: re-authentication with Apple / Google ─────────────
--
-- TRUE when this account already has an identity with this provider and subject. Checked
-- BEFORE an Apple/Google id token is handed to GoTrue for re-authentication: GoTrue's id-token
-- sign-in creates a new account for a subject it does not know, and a re-authentication must
-- never do that. `provider_id` is the provider's subject in current GoTrue; `identity_data->>sub`
-- covers rows written before that column was populated.
CREATE OR REPLACE FUNCTION public.account_has_identity(
    p_user_id  uuid,
    p_provider text,
    p_subject  text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM auth.identities i
         WHERE i.user_id = p_user_id
           AND i.provider = p_provider
           AND (i.provider_id = p_subject OR i.identity_data->>'sub' = p_subject)
    );
$$;

-- ── Function grants: service_role only (tests/test_security_definer_grants.py) ─────────────
--
-- Load-bearing. A SECURITY DEFINER function on the default ACL is executable by anyone holding
-- the shipped anon key, through PostgREST RPC: these would let them revoke any account's
-- sessions, mint session rows, or probe which Apple/Google subject owns an account.

REVOKE ALL ON FUNCTION public._lock_refresh_sessions(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public._lock_refresh_sessions(uuid) FROM anon;
REVOKE ALL ON FUNCTION public._lock_refresh_sessions(uuid) FROM authenticated;
GRANT EXECUTE ON FUNCTION public._lock_refresh_sessions(uuid) TO service_role;

REVOKE ALL ON FUNCTION public._prune_refresh_sessions(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public._prune_refresh_sessions(uuid) FROM anon;
REVOKE ALL ON FUNCTION public._prune_refresh_sessions(uuid) FROM authenticated;
GRANT EXECUTE ON FUNCTION public._prune_refresh_sessions(uuid) TO service_role;

REVOKE ALL ON FUNCTION public.start_refresh_family(uuid, uuid, uuid, timestamptz, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.start_refresh_family(uuid, uuid, uuid, timestamptz, timestamptz) FROM anon;
REVOKE ALL ON FUNCTION public.start_refresh_family(uuid, uuid, uuid, timestamptz, timestamptz) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.start_refresh_family(uuid, uuid, uuid, timestamptz, timestamptz) TO service_role;

REVOKE ALL ON FUNCTION public.rotate_refresh_token(uuid, uuid, bigint, bigint, boolean, uuid, uuid, timestamptz, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.rotate_refresh_token(uuid, uuid, bigint, bigint, boolean, uuid, uuid, timestamptz, integer, integer) FROM anon;
REVOKE ALL ON FUNCTION public.rotate_refresh_token(uuid, uuid, bigint, bigint, boolean, uuid, uuid, timestamptz, integer, integer) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.rotate_refresh_token(uuid, uuid, bigint, bigint, boolean, uuid, uuid, timestamptz, integer, integer) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_refresh_family(uuid, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.revoke_refresh_family(uuid, uuid, text) FROM anon;
REVOKE ALL ON FUNCTION public.revoke_refresh_family(uuid, uuid, text) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.revoke_refresh_family(uuid, uuid, text) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_all_refresh_sessions(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.revoke_all_refresh_sessions(uuid) FROM anon;
REVOKE ALL ON FUNCTION public.revoke_all_refresh_sessions(uuid) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.revoke_all_refresh_sessions(uuid) TO service_role;

REVOKE ALL ON FUNCTION public.account_has_identity(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.account_has_identity(uuid, text, text) FROM anon;
REVOKE ALL ON FUNCTION public.account_has_identity(uuid, text, text) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.account_has_identity(uuid, text, text) TO service_role;
