-- 216_widget_holdings_grants.sql
--
-- Why: the Home Screen "Holdings" tile only changes while the app is running (owner report
-- 2026-11-27: the medium widget showed a stale NVDA move hours after the Updates card had moved
-- on). The widget extension is a separate process with no session, and `/widget/portfolio-mover`
-- is session-only, so with the app closed or force-quit nothing could refresh the holdings.
--
-- Owner decisions 2026-11-27: "Own key, 30-day life (Recommended)"; "Yes, add push too";
-- "Yes, issue it in refresh (Recommended)" — the key is minted ONLY inside a successful
-- `POST /auth/refresh` rotation, never from a bare access token.
--
-- auth.md §8a / core/security.py's WIDGET TOKEN block say the market widget token is defensible
-- only because it reaches market-wide data, and that a credential reaching USER data "needs a
-- server-side revocation list first". This migration IS that list:
--
--   * `widget_holdings_grants` — one row per device key. The server stores only the SHA-256 of
--     the 256-bit opaque key (`cwh1_` + 43 base64url characters); the key itself is never stored.
--     Each row records the account, the 214 session (`sid` = auth_refresh_families.family_id) it
--     was minted under and the minting access token's `iat` (eviction compares THAT, never
--     created_at). Life: 30 days (CHECK ≤ 31).
--   * ONE liveness predicate, `widget_holdings_grant_is_live(g)`: not revoked, not expired, the
--     users row exists, and `minted_iat` ≥ the later of password_changed_at / sessions_revoked_at
--     floored to whole seconds — the same expression as 214's rotate (214:269-273). The check,
--     push registration and push targeting all call it; nothing restates it by hand (the mint
--     and the check's evicted branch repeat only its whole-second stamp comparison, pinned by
--     tests/test_widget_holdings_grant_sql.py), and the backend NEVER uses the fail-open Python
--     helper (`_reject_if_password_changed_since_issue`).
--   * A trigger on `auth_refresh_families` (AFTER UPDATE OF revoked_at): every grant of a session
--     dies in the SAME transaction that ends the session — logout (214 revoke_refresh_family),
--     refresh-token reuse (214 rotate's reuse branch) and sign-out-all (214
--     revoke_all_refresh_sessions). 214's functions are NOT redefined.
--     ⚠️ REUSE DETECTION NOW RUNS THIS TRIGGER. A failing trigger would roll back the reuse
--     revocation (the route answers 503 and the thief's successor keeps rotating), so the
--     trigger is written so it CANNOT fail on data: the reason is mapped through a CASE whose
--     ELSE is 'session_ended' (every value is in the grants CHECK list), and it has no EXCEPTION
--     block — database.md's "EXCEPTION WHEN OTHERS → WARNING" rule protects a parent INSERT; here
--     the downstream write IS the security property, so it is never swallowed.
--   * Account deletion: user_id FK → public.users ON DELETE CASCADE (admin.delete_user cascades
--     auth.users → public.users → here). NOT in users._UNLINKED_USER_TABLES.
--   * Renewal: a new mint under the same sid SUPERSEDES the previous grant (its push token and
--     head ticker move to the successor; its life is clamped to ≤ 24 h) instead of revoking it, so
--     a lost refresh answer or a slow device never darkens the tile. The predecessor is revoked
--     'renewed' on the successor's first check, by the app (revoke-by-key), or at the next mint.
--   * WidgetKit push (iOS 26+): the device's widget push token and the grant's last head ticker
--     live ON the grant row. Every revocation path nulls them, so a dead grant is never a push
--     target and no holding is left behind on a dead row.
--   * Eviction REVOKES too (review 2026-11-27): a grant the users stamps evicted (a password
--     change / reset / set, or a sign-out-all whose family was already pruned, so the trigger
--     never saw it) is revoked 'session_ended' — head ticker and push token nulled — the first
--     time its key is checked, and every evicted grant of the account at the account's next
--     mint. Without that it kept a holding and a push token for up to ~31 days: the device
--     cannot revoke a key that no longer authenticates.
--
-- The `sid` column has NO FK: families are pruned (214:159-175) and a grant must outlive an idle
-- session by owner decision ("keeps updating for 30 days without the app being opened").
--
-- ── RPC RETURN SHAPES (the Python callers in app/services/widget_holdings_grant_service.py) ──
--
--   mint_widget_holdings_grant(p_user_id uuid, p_sid uuid, p_minted_iat bigint, p_key_hash text,
--                              p_ttl_days int (1..30), p_max_live int (1..10)) → jsonb
--       {"status":"minted","grant_id":"<uuid>","expires_at":"YYYY-MM-DDTHH:MM:SSZ"}
--     | {"status":"account_missing"} | {"status":"evicted"}
--     | {"status":"session_unknown"} | {"status":"session_revoked"}
--     RAISEs (SQLSTATE 22004 / 22023) on a NULL or out-of-range argument.
--     Side effect on EVERY non-raising answer past "account_missing" (the evicted one too): the
--     account's evicted grants are revoked 'session_ended', push columns and head ticker nulled.
--
--   check_widget_holdings_grant(p_key_hash text) → jsonb
--       {"status":"live","grant_id":"<uuid>","user_id":"<uuid>",
--        "expires_at":"YYYY-MM-DDTHH:MM:SSZ","superseded":<bool>}
--     | {"status":"unknown"|"revoked"|"expired"|"account_missing"|"evicted"}
--     Never raises on a malformed hash (→ "unknown"). "evicted" also REVOKES that grant
--     ('session_ended', push columns and head ticker nulled), so the same key checked again
--     reads "revoked" — both map to 401 AUTH_SESSION_EXPIRED.
--
--   revoke_widget_holdings_grant(p_grant_id uuid, p_user_id uuid, p_reason text) → jsonb
--       {"status":"revoked"} | {"status":"already_revoked_or_unknown"}
--     p_reason ∈ {'app_revoked','widget_removed'}; anything else RAISEs 22023.
--
--   note_widget_holdings_head(p_grant_id uuid, p_ticker text) → jsonb
--       {"status":"noted"} | {"status":"unchanged"}     (unchanged = same ticker, revoked, unknown)
--     The ticker is stored upper-cased and trimmed; NULL clears it; > 32 chars RAISEs 22023.
--
--   widget_push_register(p_user_id uuid, p_grant_id uuid, p_token text, p_environment text) → jsonb
--       {"stored":<bool>,"grant_usable":<bool>}
--     {false,false} for an unknown, foreign, dead or superseded grant (indistinguishable on
--     purpose). p_token NULL clears the row's token ({true,true}). A malformed token or
--     environment RAISEs 22023 (the route validates first, so that is a bug, not a client).
--
--   widget_push_targets(p_scopes text[], p_all boolean, p_grant_ids uuid[], p_after uuid,
--                       p_limit int) → SETOF (grant_id uuid, user_id uuid, push_token text,
--                       push_environment text, last_head_ticker text,
--                       push_last_sent_at timestamptz, push_sent_day date, push_sent_count int)
--     Live (predicate), non-superseded, tokened rows matching p_all OR grant_id = ANY(p_grant_ids)
--     OR last_head_ticker = ANY(upper(p_scopes)); keyset `grant_id > p_after`, ORDER BY grant_id,
--     at most LEAST(GREATEST(p_limit,1),1000) rows. push_sent_day is the ET date of the count.
--
--   widget_push_settle(p_outcomes jsonb) → jsonb
--     p_outcomes: a JSON array of ≤ 100 objects
--       {"grant_id":"<uuid>","token":"<hex>","outcome":"accepted"|"gone"|"bad"|"retry"|"not_sent",
--        "apns_ts":<int ms since epoch, APNs' 410 `timestamp`> | null,
--        "count":<bool, accepted only, default true — false = stamp without counting (dry run)>,
--        "error":"<≤200 chars, bad/retry only>"}
--     Every UPDATE is keyed `grant_id = … AND push_token = token`, so a token re-registered
--     meanwhile is never cleared by a stale outcome. Malformed elements are skipped and counted.
--       → {"status":"settled","accepted":n,"gone":n,"bad":n,"cleared_bad":n,"retry":n,
--          "not_sent":n,"unmatched":n,"skipped":n}
--     accepted: push_last_sent_at = now(); push_sent_count +1 (reset to 1 on a new ET day);
--               push_bad_since / push_last_error cleared.
--     gone:     token cleared only WHERE push_token_updated_at <= apns_ts (APNs: stop "unless your
--               app retrieves the same token" again).
--     bad:      counted like a send, push_bad_since set if unset; the token is cleared once
--               push_bad_since is more than 24 h old.
--     retry:    push_last_error only.    not_sent: no write.
--
-- ── DEPLOY ORDER ────────────────────────────────────────────────────────────────────────────
-- 1. Apply this file (after 214, which is live) — first on the local Supabase E2E stack, then
--    prod — and run the VERIFY block below. The trigger is live at once (a no-op on an empty
--    table), so check one logout and one sign-out-all right after.
-- 2. Deploy the backend with WIDGET_HOLDINGS_GRANTS_ENABLED / WIDGET_PUSH_ENABLED unset (False).
--    A backend deployed BEFORE this file fails closed: no mint (PGRST202, throttled ERROR), the
--    holdings route answers 503, and logout is unaffected (no trigger yet).
-- 3. WIDGET_HOLDINGS_GRANTS_ENABLED=true on Railway (a restart). Push later, after a week.
--
-- ── TEARDOWN ORDER (never another) ──────────────────────────────────────────────────────────
--   switch off (WIDGET_HOLDINGS_GRANTS_ENABLED=false, restart) → revoke-all SQL →
--   DROP TRIGGER trg_auth_refresh_families_revoke_widget_grants → DROP TABLE.
-- Never drop the table while the trigger exists: every logout, sign-out-all and refresh reuse
-- would then raise. Any RE-ENABLE after a window with no trigger runs the revoke-all SQL first
-- (sessions ended in that window did not kill their grants).
--   Revoke-all (incident / teardown; the owner runs it, never Claude):
--     UPDATE public.widget_holdings_grants
--        SET revoked_at = now(), revoke_reason = 'app_revoked',
--            push_token = NULL, push_environment = NULL, last_head_ticker = NULL
--      WHERE revoked_at IS NULL;
--
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP POLICY /
-- DROP TRIGGER IF EXISTS before CREATE, declarative REVOKE / GRANT. One transaction.

BEGIN;

-- ── Table ───────────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.widget_holdings_grants (
    grant_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    key_hash              TEXT NOT NULL,
    -- The 214 session (auth_refresh_families.family_id) the grant was minted under. No FK.
    sid                   UUID NOT NULL,
    -- The `iat` of the access token minted in the same refresh. Eviction compares THIS.
    minted_iat            BIGINT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at            TIMESTAMPTZ NOT NULL,
    last_used_at          TIMESTAMPTZ,
    revoked_at            TIMESTAMPTZ,
    revoke_reason         TEXT,
    -- The successor minted under the same sid (renewal). No FK: rows are pruned.
    superseded_by         UUID,
    -- The ticker the device's tile last led with — push targeting for ticker cards.
    last_head_ticker      TEXT,
    -- WidgetKit push token (iOS 26+), uploaded by the APP via PUT /widget/push-token.
    push_token            TEXT,
    push_environment      TEXT,
    push_token_updated_at TIMESTAMPTZ,
    push_last_sent_at     TIMESTAMPTZ,
    push_sent_day         DATE,
    push_sent_count       INTEGER NOT NULL DEFAULT 0,
    push_bad_since        TIMESTAMPTZ,
    push_last_error       TEXT,
    CONSTRAINT widget_holdings_grants_key_hash_key UNIQUE (key_hash),
    CONSTRAINT widget_holdings_grants_key_hash_check
        CHECK (key_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT widget_holdings_grants_minted_iat_check
        CHECK (minted_iat > 0),
    CONSTRAINT widget_holdings_grants_life_check
        CHECK (expires_at > created_at AND expires_at <= created_at + interval '31 days'),
    -- 'logout', 'reuse', 'signout_all' are 214's family reasons (214:79-80), copied by the
    -- trigger; any other family reason is written as 'session_ended' (never a CHECK failure),
    -- as is a grant the users stamps evicted (revoked by the check or the next mint).
    CONSTRAINT widget_holdings_grants_revoke_reason_check
        CHECK (revoke_reason IS NULL OR revoke_reason IN (
            'logout', 'reuse', 'signout_all', 'session_ended',
            'renewed', 'cap', 'app_revoked', 'widget_removed'
        )),
    CONSTRAINT widget_holdings_grants_revoked_pair_check
        CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL)),
    CONSTRAINT widget_holdings_grants_head_ticker_check
        CHECK (last_head_ticker IS NULL OR length(last_head_ticker) BETWEEN 1 AND 32),
    CONSTRAINT widget_holdings_grants_push_token_check
        CHECK (push_token IS NULL
               OR (push_token ~ '^[0-9a-f]+$' AND length(push_token) BETWEEN 32 AND 400
                   AND length(push_token) % 2 = 0)),
    CONSTRAINT widget_holdings_grants_push_environment_check
        CHECK (push_environment IS NULL OR push_environment IN ('sandbox', 'production')),
    CONSTRAINT widget_holdings_grants_push_pair_check
        CHECK ((push_token IS NULL) = (push_environment IS NULL)),
    CONSTRAINT widget_holdings_grants_push_count_check
        CHECK (push_sent_count >= 0),
    CONSTRAINT widget_holdings_grants_push_error_check
        CHECK (push_last_error IS NULL OR length(push_last_error) <= 200)
);

-- ── Indexes ─────────────────────────────────────────────────────────────────────────────────

-- The trigger and the mint's same-session lookups.
CREATE INDEX IF NOT EXISTS idx_widget_holdings_grants_sid_live
    ON public.widget_holdings_grants(sid) WHERE revoked_at IS NULL;
-- The per-account cap.
CREATE INDEX IF NOT EXISTS idx_widget_holdings_grants_user_live
    ON public.widget_holdings_grants(user_id, created_at DESC) WHERE revoked_at IS NULL;
-- The FK cascade on account deletion.
CREATE INDEX IF NOT EXISTS idx_widget_holdings_grants_user
    ON public.widget_holdings_grants(user_id);
-- The successor's first check revokes its predecessors.
CREATE INDEX IF NOT EXISTS idx_widget_holdings_grants_superseded_live
    ON public.widget_holdings_grants(superseded_by)
    WHERE superseded_by IS NOT NULL AND revoked_at IS NULL;
-- The bounded cross-user prune.
CREATE INDEX IF NOT EXISTS idx_widget_holdings_grants_dead_since
    ON public.widget_holdings_grants((COALESCE(revoked_at, expires_at)));
-- One row per device push token.
CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_holdings_grants_push_token
    ON public.widget_holdings_grants(push_token) WHERE push_token IS NOT NULL;
-- Push targeting by head ticker.
CREATE INDEX IF NOT EXISTS idx_widget_holdings_grants_push_head
    ON public.widget_holdings_grants(last_head_ticker)
    WHERE push_token IS NOT NULL AND revoked_at IS NULL;

COMMENT ON TABLE public.widget_holdings_grants IS
    'Per-device Home Screen widget Holdings credential (owner 2026-11-27, "Own key, 30-day life"): '
    'the SHA-256 of an opaque cwh1_ key, minted only inside a POST /auth/refresh rotation, sent by '
    'the widget extension to GET /widget/holdings-mover. Revoked by the auth_refresh_families '
    'trigger (logout, reuse, sign-out-all), evicted by the users stamps (one predicate, '
    'widget_holdings_grant_is_live), superseded on renewal, cascades with the account. Also holds '
    'the device''s WidgetKit push token and head ticker. Service-role only. Migration 216.';
COMMENT ON COLUMN public.widget_holdings_grants.key_hash IS
    'Lower-case hex SHA-256 of the full cwh1_ key. The key itself is never stored or logged.';
COMMENT ON COLUMN public.widget_holdings_grants.sid IS
    'The 214 session (auth_refresh_families.family_id) this grant was minted under. No FK: '
    'families are pruned and a grant outlives an idle session (30 days, owner decision).';
COMMENT ON COLUMN public.widget_holdings_grants.minted_iat IS
    'iat of the access token minted in the same refresh. Eviction compares this against the '
    'later of users.password_changed_at / sessions_revoked_at, whole seconds — never created_at.';
COMMENT ON COLUMN public.widget_holdings_grants.superseded_by IS
    'The successor minted under the same sid. A superseded grant keeps working for at most 24 h '
    '(expires_at clamped) and is revoked ''renewed'' on the successor''s first check.';
COMMENT ON COLUMN public.widget_holdings_grants.last_head_ticker IS
    'The ticker the device''s Holdings tile last led with (push targeting). Nulled on revocation.';
COMMENT ON COLUMN public.widget_holdings_grants.push_token IS
    'WidgetKit push token of the device holding this grant (iOS 26+). Uploaded by the APP via '
    'PUT /widget/push-token. Content-free wakes only; useless without our APNs .p8; never logged '
    'in full. Nulled on every revocation.';
COMMENT ON COLUMN public.widget_holdings_grants.push_token_updated_at IS
    'Set on every registration; compared with APNs'' 410 timestamp before a token is cleared.';
COMMENT ON COLUMN public.widget_holdings_grants.push_sent_day IS
    'ET calendar day push_sent_count counts (the daily cap survives a restart).';

-- ── RLS + grants: SERVICE-ROLE ONLY (database.md; the iOS app has no Supabase client) ───────

ALTER TABLE public.widget_holdings_grants ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "widget_holdings_grants_service_all" ON public.widget_holdings_grants;
CREATE POLICY "widget_holdings_grants_service_all" ON public.widget_holdings_grants
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.widget_holdings_grants FROM anon, authenticated;
-- A table with no GRANT is owner-only, not open (169's lesson). UUID PK: no sequence to grant.
GRANT ALL ON public.widget_holdings_grants TO service_role;

-- ── 1. The ONE liveness predicate ───────────────────────────────────────────────────────────
--
-- Superseded rows ARE live here (they keep working ≤ 24 h); push registration and targeting
-- exclude them separately.
CREATE OR REPLACE FUNCTION public.widget_holdings_grant_is_live(g public.widget_holdings_grants)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT g.revoked_at IS NULL
       AND g.expires_at > now()
       AND EXISTS (
           SELECT 1
             FROM public.users u
            WHERE u.id = g.user_id
              AND (
                  -- GREATEST ignores NULLs; whole seconds, as 214 (iat has no fraction).
                  GREATEST(u.password_changed_at, u.sessions_revoked_at) IS NULL
                  OR to_timestamp(g.minted_iat)
                     >= date_trunc('second', GREATEST(u.password_changed_at, u.sessions_revoked_at))
              )
       );
$$;

-- ── 2. mint_widget_holdings_grant: inside POST /auth/refresh, status `rotated` only ─────────

CREATE OR REPLACE FUNCTION public.mint_widget_holdings_grant(
    p_user_id    uuid,
    p_sid        uuid,
    p_minted_iat bigint,
    p_key_hash   text,
    p_ttl_days   integer,
    p_max_live   integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_now       timestamptz;
    v_pw        timestamptz;
    v_sr        timestamptz;
    v_stamp     timestamptz;
    v_fam       public.auth_refresh_families%ROWTYPE;
    v_pred      public.widget_holdings_grants%ROWTYPE;
    v_has_pred  boolean := false;
    v_new       uuid := gen_random_uuid();
    v_expires   timestamptz;
    v_capped    uuid[];
BEGIN
    IF p_user_id IS NULL OR p_sid IS NULL OR p_minted_iat IS NULL OR p_key_hash IS NULL
       OR p_ttl_days IS NULL OR p_max_live IS NULL THEN
        RAISE EXCEPTION 'mint_widget_holdings_grant: a required argument is NULL'
            USING ERRCODE = '22004';
    END IF;
    IF p_key_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'mint_widget_holdings_grant: p_key_hash is not 64 lower-case hex'
            USING ERRCODE = '22023';
    END IF;
    IF p_ttl_days < 1 OR p_ttl_days > 30 THEN
        RAISE EXCEPTION 'mint_widget_holdings_grant: p_ttl_days % outside 1..30', p_ttl_days
            USING ERRCODE = '22023';
    END IF;
    IF p_max_live < 1 OR p_max_live > 10 THEN
        RAISE EXCEPTION 'mint_widget_holdings_grant: p_max_live % outside 1..10', p_max_live
            USING ERRCODE = '22023';
    END IF;
    IF p_minted_iat <= 0 THEN
        RAISE EXCEPTION 'mint_widget_holdings_grant: p_minted_iat must be positive'
            USING ERRCODE = '22023';
    END IF;

    -- Serialised with rotation, logout and sign-out-all of the same account (214:147-154): a
    -- revocation that commits while this waits must win.
    PERFORM public._lock_refresh_sessions(p_user_id);
    -- Read AFTER the lock: `now()` is the transaction start, which can precede a mint that
    -- committed while this one waited — and the cap below orders by created_at.
    v_now := clock_timestamp();

    -- Bounded CROSS-USER prune of rows dead for more than a day. SKIP LOCKED: a mint never waits
    -- on another account's row (it runs under a 3 s client bound).
    DELETE FROM public.widget_holdings_grants
     WHERE ctid IN (
         SELECT d.ctid
           FROM public.widget_holdings_grants d
          WHERE COALESCE(d.revoked_at, d.expires_at) < now() - interval '1 day'
          LIMIT 500
          FOR UPDATE SKIP LOCKED
     );

    SELECT u.password_changed_at, u.sessions_revoked_at INTO v_pw, v_sr
      FROM public.users u WHERE u.id = p_user_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'account_missing');
    END IF;
    v_stamp := GREATEST(v_pw, v_sr);

    -- Every grant of the account the stamps evicted is REVOKED here, under the lock (D4: no
    -- holding left behind on a dead row). A password change / reset / set or a sign-out-all
    -- whose family was already pruned evicts a grant without revoking it, and an evicted device
    -- can no longer revoke by key — so a later mint by ANY device of the account sweeps them
    -- (the check revokes one the first time it is presented). The same comparison as the
    -- predicate: whole seconds, minted_iat.
    IF v_stamp IS NOT NULL THEN
        UPDATE public.widget_holdings_grants g
           SET revoked_at = v_now, revoke_reason = 'session_ended',
               push_token = NULL, push_environment = NULL, last_head_ticker = NULL
         WHERE g.user_id = p_user_id
           AND g.revoked_at IS NULL
           AND to_timestamp(g.minted_iat) < date_trunc('second', v_stamp);
    END IF;

    IF v_stamp IS NOT NULL AND to_timestamp(p_minted_iat) < date_trunc('second', v_stamp) THEN
        RETURN jsonb_build_object('status', 'evicted');
    END IF;

    SELECT * INTO v_fam
      FROM public.auth_refresh_families f
     WHERE f.family_id = p_sid AND f.user_id = p_user_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'session_unknown');
    END IF;
    IF v_fam.revoked_at IS NOT NULL THEN
        RETURN jsonb_build_object('status', 'session_revoked');
    END IF;

    -- Older superseded rows of this session → 'renewed' (at most two keys per session live).
    UPDATE public.widget_holdings_grants g
       SET revoked_at = v_now, revoke_reason = 'renewed',
           push_token = NULL, push_environment = NULL, last_head_ticker = NULL
     WHERE g.sid = p_sid
       AND g.user_id = p_user_id
       AND g.revoked_at IS NULL
       AND g.superseded_by IS NOT NULL;

    -- The live predecessor: its push columns and head ticker move to the successor.
    SELECT * INTO v_pred
      FROM public.widget_holdings_grants g
     WHERE g.sid = p_sid
       AND g.user_id = p_user_id
       AND g.revoked_at IS NULL
       AND g.superseded_by IS NULL
       AND g.expires_at > v_now
     ORDER BY g.created_at DESC
     LIMIT 1
     FOR UPDATE;
    v_has_pred := FOUND;

    -- Every non-superseded row of the session is superseded (normally exactly one). Push
    -- columns are nulled BEFORE the INSERT: the push token is UNIQUE.
    UPDATE public.widget_holdings_grants g
       SET superseded_by = v_new,
           expires_at = LEAST(g.expires_at, v_now + interval '24 hours'),
           push_token = NULL, push_environment = NULL, push_token_updated_at = NULL,
           push_last_sent_at = NULL, push_sent_day = NULL, push_sent_count = 0,
           push_bad_since = NULL, push_last_error = NULL, last_head_ticker = NULL
     WHERE g.sid = p_sid
       AND g.user_id = p_user_id
       AND g.revoked_at IS NULL
       AND g.superseded_by IS NULL;

    v_expires := v_now + make_interval(days => p_ttl_days);
    INSERT INTO public.widget_holdings_grants (
        grant_id, user_id, key_hash, sid, minted_iat, created_at, expires_at,
        last_head_ticker, push_token, push_environment, push_token_updated_at,
        push_last_sent_at, push_sent_day, push_sent_count, push_bad_since, push_last_error
    ) VALUES (
        v_new, p_user_id, p_key_hash, p_sid, p_minted_iat, v_now, v_expires,
        CASE WHEN v_has_pred THEN v_pred.last_head_ticker END,
        CASE WHEN v_has_pred THEN v_pred.push_token END,
        CASE WHEN v_has_pred THEN v_pred.push_environment END,
        CASE WHEN v_has_pred THEN v_pred.push_token_updated_at END,
        CASE WHEN v_has_pred THEN v_pred.push_last_sent_at END,
        CASE WHEN v_has_pred THEN v_pred.push_sent_day END,
        CASE WHEN v_has_pred THEN v_pred.push_sent_count ELSE 0 END,
        CASE WHEN v_has_pred THEN v_pred.push_bad_since END,
        CASE WHEN v_has_pred THEN v_pred.push_last_error END
    );

    -- The per-account cap counts live NON-superseded rows; the new row is never capped. A capped
    -- row's own superseded predecessors die with it.
    SELECT array_agg(c.grant_id) INTO v_capped
      FROM (
          SELECT g.grant_id
            FROM public.widget_holdings_grants g
           WHERE g.user_id = p_user_id
             AND g.revoked_at IS NULL
             AND g.superseded_by IS NULL
             AND g.expires_at > v_now
             AND g.grant_id <> v_new
           ORDER BY g.created_at DESC, g.grant_id DESC
          OFFSET p_max_live - 1
      ) c;
    IF v_capped IS NOT NULL THEN
        UPDATE public.widget_holdings_grants g
           SET revoked_at = v_now, revoke_reason = 'cap',
               push_token = NULL, push_environment = NULL, last_head_ticker = NULL
         WHERE g.user_id = p_user_id
           AND g.revoked_at IS NULL
           AND (g.grant_id = ANY(v_capped) OR g.superseded_by = ANY(v_capped));
    END IF;

    RETURN jsonb_build_object(
        'status', 'minted',
        'grant_id', v_new,
        'expires_at', to_char(v_expires AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    );
END;
$$;

-- ── 3. check_widget_holdings_grant: every holdings-mover call ───────────────────────────────
--
-- No advisory lock: a response racing a revocation has the same window an access token has.
CREATE OR REPLACE FUNCTION public.check_widget_holdings_grant(p_key_hash text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v       public.widget_holdings_grants%ROWTYPE;
    v_pw    timestamptz;
    v_sr    timestamptz;
    v_stamp timestamptz;
BEGIN
    IF p_key_hash IS NULL OR p_key_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN jsonb_build_object('status', 'unknown');
    END IF;

    SELECT * INTO v FROM public.widget_holdings_grants g WHERE g.key_hash = p_key_hash;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'unknown');
    END IF;

    -- The predicate DECIDES; the branches below only name why a dead grant is dead.
    IF NOT public.widget_holdings_grant_is_live(v) THEN
        IF v.revoked_at IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'revoked');
        END IF;
        IF v.expires_at <= now() THEN
            RETURN jsonb_build_object('status', 'expired');
        END IF;
        SELECT u.password_changed_at, u.sessions_revoked_at INTO v_pw, v_sr
          FROM public.users u WHERE u.id = v.user_id;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'account_missing');
        END IF;
        v_stamp := GREATEST(v_pw, v_sr);
        IF v_stamp IS NOT NULL AND to_timestamp(v.minted_iat) < date_trunc('second', v_stamp) THEN
            -- An evicted grant is REVOKED the first time it is seen (D4: no holding left behind
            -- on a dead row). Nothing else would: the device's revoke-by-key is refused before
            -- the RPC runs (the key no longer authenticates), and the prune keys on revoked_at /
            -- expires_at. One row, keyed by grant_id: no lock is needed and none is held.
            UPDATE public.widget_holdings_grants g
               SET revoked_at = now(), revoke_reason = 'session_ended',
                   push_token = NULL, push_environment = NULL, last_head_ticker = NULL
             WHERE g.grant_id = v.grant_id
               AND g.revoked_at IS NULL;
            RETURN jsonb_build_object('status', 'evicted');
        END IF;
        -- Unreachable while the predicate and the branches agree; a refusal either way.
        RETURN jsonb_build_object('status', 'revoked');
    END IF;

    -- The successor's FIRST live use ends its predecessors (renewal overlap, ≤ 24 h).
    IF v.last_used_at IS NULL THEN
        UPDATE public.widget_holdings_grants g
           SET revoked_at = now(), revoke_reason = 'renewed',
               push_token = NULL, push_environment = NULL, last_head_ticker = NULL
         WHERE g.superseded_by = v.grant_id
           AND g.user_id = v.user_id
           AND g.revoked_at IS NULL;
    END IF;

    -- At most one write an hour per grant.
    UPDATE public.widget_holdings_grants g
       SET last_used_at = now()
     WHERE g.grant_id = v.grant_id
       AND g.revoked_at IS NULL
       AND (g.last_used_at IS NULL OR g.last_used_at < now() - interval '1 hour');

    RETURN jsonb_build_object(
        'status', 'live',
        'grant_id', v.grant_id,
        'user_id', v.user_id,
        'expires_at', to_char(v.expires_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'superseded', v.superseded_by IS NOT NULL
    );
END;
$$;

-- ── 4. revoke_widget_holdings_grant: revoke-by-key from the device ──────────────────────────

CREATE OR REPLACE FUNCTION public.revoke_widget_holdings_grant(
    p_grant_id uuid,
    p_user_id  uuid,
    p_reason   text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_n integer;
BEGIN
    IF p_grant_id IS NULL OR p_user_id IS NULL OR p_reason IS NULL THEN
        RAISE EXCEPTION 'revoke_widget_holdings_grant: a required argument is NULL'
            USING ERRCODE = '22004';
    END IF;
    IF p_reason NOT IN ('app_revoked', 'widget_removed') THEN
        RAISE EXCEPTION 'revoke_widget_holdings_grant: unknown reason %', p_reason
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.widget_holdings_grants g
       SET revoked_at = now(), revoke_reason = p_reason,
           push_token = NULL, push_environment = NULL, last_head_ticker = NULL
     WHERE g.grant_id = p_grant_id
       AND g.user_id = p_user_id
       AND g.revoked_at IS NULL;
    GET DIAGNOSTICS v_n = ROW_COUNT;

    RETURN jsonb_build_object(
        'status', CASE WHEN v_n > 0 THEN 'revoked' ELSE 'already_revoked_or_unknown' END
    );
END;
$$;

-- ── 5. note_widget_holdings_head: the tile's head ticker, for push targeting ────────────────

CREATE OR REPLACE FUNCTION public.note_widget_holdings_head(
    p_grant_id uuid,
    p_ticker   text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_ticker text := NULLIF(upper(btrim(p_ticker)), '');
    v_n      integer;
BEGIN
    IF p_grant_id IS NULL THEN
        RAISE EXCEPTION 'note_widget_holdings_head: p_grant_id is NULL' USING ERRCODE = '22004';
    END IF;
    IF v_ticker IS NOT NULL AND length(v_ticker) > 32 THEN
        RAISE EXCEPTION 'note_widget_holdings_head: ticker longer than 32' USING ERRCODE = '22023';
    END IF;

    UPDATE public.widget_holdings_grants g
       SET last_head_ticker = v_ticker
     WHERE g.grant_id = p_grant_id
       AND g.revoked_at IS NULL
       AND g.last_head_ticker IS DISTINCT FROM v_ticker;
    GET DIAGNOSTICS v_n = ROW_COUNT;

    RETURN jsonb_build_object('status', CASE WHEN v_n > 0 THEN 'noted' ELSE 'unchanged' END);
END;
$$;

-- ── 6. widget_push_register: PUT /widget/push-token (strict session route) ──────────────────

CREATE OR REPLACE FUNCTION public.widget_push_register(
    p_user_id     uuid,
    p_grant_id    uuid,
    p_token       text,
    p_environment text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v public.widget_holdings_grants%ROWTYPE;
BEGIN
    IF p_user_id IS NULL OR p_grant_id IS NULL THEN
        RAISE EXCEPTION 'widget_push_register: a required argument is NULL' USING ERRCODE = '22004';
    END IF;
    IF p_token IS NOT NULL THEN
        -- Postgres caps a regex repetition count at 255, hence `+` and an explicit length.
        IF p_token !~ '^[0-9a-f]+$' OR length(p_token) NOT BETWEEN 32 AND 400
           OR length(p_token) % 2 <> 0 THEN
            RAISE EXCEPTION 'widget_push_register: malformed token' USING ERRCODE = '22023';
        END IF;
        IF p_environment IS NULL OR p_environment NOT IN ('sandbox', 'production') THEN
            RAISE EXCEPTION 'widget_push_register: unknown environment' USING ERRCODE = '22023';
        END IF;
    END IF;

    -- Ownership FIRST, under a row lock, before any other row is touched.
    SELECT * INTO v
      FROM public.widget_holdings_grants g
     WHERE g.grant_id = p_grant_id AND g.user_id = p_user_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('stored', false, 'grant_usable', false);
    END IF;
    -- Unknown, foreign, dead and superseded are one indistinguishable answer.
    IF v.superseded_by IS NOT NULL OR NOT public.widget_holdings_grant_is_live(v) THEN
        RETURN jsonb_build_object('stored', false, 'grant_usable', false);
    END IF;

    IF p_token IS NULL THEN
        UPDATE public.widget_holdings_grants g
           SET push_token = NULL, push_environment = NULL, push_token_updated_at = now(),
               push_bad_since = NULL, push_last_error = NULL
         WHERE g.grant_id = p_grant_id;
        RETURN jsonb_build_object('stored', true, 'grant_usable', true);
    END IF;

    -- One row per device token: detach it from any other row, then set it here.
    UPDATE public.widget_holdings_grants g
       SET push_token = NULL, push_environment = NULL, push_token_updated_at = now(),
           push_bad_since = NULL, push_last_error = 'moved to another grant'
     WHERE g.push_token = p_token
       AND g.grant_id <> p_grant_id;

    UPDATE public.widget_holdings_grants g
       SET push_token = p_token, push_environment = p_environment,
           push_token_updated_at = now(), push_bad_since = NULL, push_last_error = NULL
     WHERE g.grant_id = p_grant_id;

    RETURN jsonb_build_object('stored', true, 'grant_usable', true);
END;
$$;

-- ── 7. widget_push_targets: the sweeper's paged target read ─────────────────────────────────

CREATE OR REPLACE FUNCTION public.widget_push_targets(
    p_scopes    text[],
    p_all       boolean,
    p_grant_ids uuid[],
    p_after     uuid,
    p_limit     integer
)
RETURNS TABLE (
    grant_id          uuid,
    user_id           uuid,
    push_token        text,
    push_environment  text,
    last_head_ticker  text,
    push_last_sent_at timestamptz,
    push_sent_day     date,
    push_sent_count   integer
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT g.grant_id, g.user_id, g.push_token, g.push_environment, g.last_head_ticker,
           g.push_last_sent_at, g.push_sent_day, g.push_sent_count
      FROM public.widget_holdings_grants g
     WHERE g.push_token IS NOT NULL
       AND g.superseded_by IS NULL
       AND public.widget_holdings_grant_is_live(g)
       AND (p_after IS NULL OR g.grant_id > p_after)
       AND (
           COALESCE(p_all, false)
           OR g.grant_id = ANY(COALESCE(p_grant_ids, '{}'::uuid[]))
           OR g.last_head_ticker = ANY(
               ARRAY(SELECT upper(btrim(s)) FROM unnest(COALESCE(p_scopes, '{}'::text[])) AS s)
           )
       )
     ORDER BY g.grant_id
     LIMIT LEAST(GREATEST(COALESCE(p_limit, 1000), 1), 1000);
$$;

-- ── 8. widget_push_settle: one batch of APNs outcomes (≤ 100) ───────────────────────────────

CREATE OR REPLACE FUNCTION public.widget_push_settle(p_outcomes jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    e           jsonb;
    v_gid       uuid;
    v_tok       text;
    v_out       text;
    v_ts        timestamptz;
    v_err       text;
    v_count     boolean;
    v_n         integer;
    v_today     date := (now() AT TIME ZONE 'America/New_York')::date;
    n_accepted  integer := 0;
    n_gone      integer := 0;
    n_bad       integer := 0;
    n_cleared   integer := 0;
    n_retry     integer := 0;
    n_not_sent  integer := 0;
    n_unmatched integer := 0;
    n_skipped   integer := 0;
BEGIN
    IF p_outcomes IS NULL OR jsonb_typeof(p_outcomes) <> 'array' THEN
        RAISE EXCEPTION 'widget_push_settle: p_outcomes must be a JSON array' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(p_outcomes) > 100 THEN
        RAISE EXCEPTION 'widget_push_settle: at most 100 outcomes per call' USING ERRCODE = '22023';
    END IF;

    FOR e IN SELECT value FROM jsonb_array_elements(p_outcomes) LOOP
        -- Shape checks by pattern, never by a failing cast (no subtransaction per element).
        IF jsonb_typeof(e) <> 'object'
           OR jsonb_typeof(e->'grant_id') IS DISTINCT FROM 'string'
           OR (e->>'grant_id') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR jsonb_typeof(e->'token') IS DISTINCT FROM 'string'
           OR (e->>'token') !~ '^[0-9a-f]+$'
           OR length(e->>'token') NOT BETWEEN 32 AND 400
           OR jsonb_typeof(e->'outcome') IS DISTINCT FROM 'string' THEN
            n_skipped := n_skipped + 1;
            CONTINUE;
        END IF;
        v_gid := (e->>'grant_id')::uuid;
        v_tok := e->>'token';
        v_out := e->>'outcome';
        v_err := left(NULLIF(e->>'error', ''), 200);

        IF v_out = 'accepted' THEN
            -- CASE, not OR: SQL guarantees no evaluation order, and a cast of a non-boolean
            -- string would raise.
            v_count := CASE WHEN jsonb_typeof(e->'count') = 'boolean'
                            THEN (e->>'count') = 'true' ELSE true END;
            UPDATE public.widget_holdings_grants g
               SET push_last_sent_at = now(),
                   push_sent_count = CASE
                       WHEN NOT v_count THEN g.push_sent_count
                       WHEN g.push_sent_day = v_today THEN g.push_sent_count + 1
                       ELSE 1 END,
                   push_sent_day = CASE WHEN v_count THEN v_today ELSE g.push_sent_day END,
                   push_bad_since = NULL, push_last_error = NULL
             WHERE g.grant_id = v_gid AND g.push_token = v_tok;
            GET DIAGNOSTICS v_n = ROW_COUNT;
            IF v_n > 0 THEN n_accepted := n_accepted + 1; ELSE n_unmatched := n_unmatched + 1; END IF;

        ELSIF v_out = 'gone' THEN
            -- APNs' 410 `timestamp`: milliseconds since the epoch. Absent or out of range →
            -- now(). Nested IFs: a cast must never run on a value the type check refused.
            v_ts := now();
            IF jsonb_typeof(e->'apns_ts') = 'number' THEN
                IF (e->>'apns_ts')::numeric BETWEEN 0 AND 100000000000000 THEN
                    v_ts := to_timestamp((e->>'apns_ts')::double precision / 1000.0);
                END IF;
            END IF;
            UPDATE public.widget_holdings_grants g
               SET push_token = NULL, push_environment = NULL, push_bad_since = NULL,
                   push_last_error = left(COALESCE(v_err, '410 Unregistered'), 200)
             WHERE g.grant_id = v_gid AND g.push_token = v_tok
               AND (g.push_token_updated_at IS NULL OR g.push_token_updated_at <= v_ts);
            GET DIAGNOSTICS v_n = ROW_COUNT;
            IF v_n > 0 THEN n_gone := n_gone + 1; ELSE n_unmatched := n_unmatched + 1; END IF;

        ELSIF v_out = 'bad' THEN
            UPDATE public.widget_holdings_grants g
               SET push_last_sent_at = now(),
                   push_sent_count = CASE WHEN g.push_sent_day = v_today
                                          THEN g.push_sent_count + 1 ELSE 1 END,
                   push_sent_day = v_today,
                   push_bad_since = COALESCE(g.push_bad_since, now()),
                   push_last_error = COALESCE(v_err, 'bad device token')
             WHERE g.grant_id = v_gid AND g.push_token = v_tok;
            GET DIAGNOSTICS v_n = ROW_COUNT;
            IF v_n > 0 THEN n_bad := n_bad + 1; ELSE n_unmatched := n_unmatched + 1; END IF;
            UPDATE public.widget_holdings_grants g
               SET push_token = NULL, push_environment = NULL, push_bad_since = NULL,
                   push_last_error = 'cleared: refused by APNs for over 24 h'
             WHERE g.grant_id = v_gid AND g.push_token = v_tok
               AND g.push_bad_since < now() - interval '24 hours';
            GET DIAGNOSTICS v_n = ROW_COUNT;
            n_cleared := n_cleared + v_n;

        ELSIF v_out = 'retry' THEN
            UPDATE public.widget_holdings_grants g
               SET push_last_error = COALESCE(v_err, 'retry')
             WHERE g.grant_id = v_gid AND g.push_token = v_tok;
            n_retry := n_retry + 1;

        ELSIF v_out = 'not_sent' THEN
            n_not_sent := n_not_sent + 1;

        ELSE
            n_skipped := n_skipped + 1;
        END IF;
    END LOOP;

    RETURN jsonb_build_object(
        'status', 'settled',
        'accepted', n_accepted, 'gone', n_gone, 'bad', n_bad, 'cleared_bad', n_cleared,
        'retry', n_retry, 'not_sent', n_not_sent, 'unmatched', n_unmatched, 'skipped', n_skipped
    );
END;
$$;

-- ── 9. The trigger: a session that ends takes its grants with it ────────────────────────────
--
-- Fires inside 214's three revocations — reuse (rotate_refresh_token), logout
-- (revoke_refresh_family) and sign-out-all (revoke_all_refresh_sessions) — in their transaction,
-- under their per-user lock. 214's prune DELETEs families; a DELETE never fires it. One indexed
-- UPDATE; NO EXCEPTION block and no value that can fail a CHECK (see the header).
CREATE OR REPLACE FUNCTION public._revoke_widget_grants_for_session()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
BEGIN
    UPDATE public.widget_holdings_grants g
       SET revoked_at = now(),
           revoke_reason = CASE NEW.revoke_reason
                               WHEN 'logout' THEN 'logout'
                               WHEN 'reuse' THEN 'reuse'
                               WHEN 'signout_all' THEN 'signout_all'
                               ELSE 'session_ended'
                           END,
           push_token = NULL, push_environment = NULL, last_head_ticker = NULL
     WHERE g.sid = NEW.family_id
       AND g.user_id = NEW.user_id
       AND g.revoked_at IS NULL;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_auth_refresh_families_revoke_widget_grants ON public.auth_refresh_families;
CREATE TRIGGER trg_auth_refresh_families_revoke_widget_grants
    AFTER UPDATE OF revoked_at ON public.auth_refresh_families
    FOR EACH ROW
    WHEN (OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL)
    EXECUTE FUNCTION public._revoke_widget_grants_for_session();

-- ── Function grants: service_role only (tests/test_security_definer_grants.py) ─────────────
--
-- Load-bearing: on the default ACL anyone holding the shipped anon key could mint, check, revoke
-- or list grants through PostgREST RPC. The TRIGGER function gets the REVOKE and NO grant.

REVOKE ALL ON FUNCTION public.widget_holdings_grant_is_live(public.widget_holdings_grants) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.widget_holdings_grant_is_live(public.widget_holdings_grants) FROM anon;
REVOKE ALL ON FUNCTION public.widget_holdings_grant_is_live(public.widget_holdings_grants) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.widget_holdings_grant_is_live(public.widget_holdings_grants) TO service_role;

REVOKE ALL ON FUNCTION public.mint_widget_holdings_grant(uuid, uuid, bigint, text, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.mint_widget_holdings_grant(uuid, uuid, bigint, text, integer, integer) FROM anon;
REVOKE ALL ON FUNCTION public.mint_widget_holdings_grant(uuid, uuid, bigint, text, integer, integer) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.mint_widget_holdings_grant(uuid, uuid, bigint, text, integer, integer) TO service_role;

REVOKE ALL ON FUNCTION public.check_widget_holdings_grant(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.check_widget_holdings_grant(text) FROM anon;
REVOKE ALL ON FUNCTION public.check_widget_holdings_grant(text) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.check_widget_holdings_grant(text) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_widget_holdings_grant(uuid, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.revoke_widget_holdings_grant(uuid, uuid, text) FROM anon;
REVOKE ALL ON FUNCTION public.revoke_widget_holdings_grant(uuid, uuid, text) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.revoke_widget_holdings_grant(uuid, uuid, text) TO service_role;

REVOKE ALL ON FUNCTION public.note_widget_holdings_head(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.note_widget_holdings_head(uuid, text) FROM anon;
REVOKE ALL ON FUNCTION public.note_widget_holdings_head(uuid, text) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.note_widget_holdings_head(uuid, text) TO service_role;

REVOKE ALL ON FUNCTION public.widget_push_register(uuid, uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.widget_push_register(uuid, uuid, text, text) FROM anon;
REVOKE ALL ON FUNCTION public.widget_push_register(uuid, uuid, text, text) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.widget_push_register(uuid, uuid, text, text) TO service_role;

REVOKE ALL ON FUNCTION public.widget_push_targets(text[], boolean, uuid[], uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.widget_push_targets(text[], boolean, uuid[], uuid, integer) FROM anon;
REVOKE ALL ON FUNCTION public.widget_push_targets(text[], boolean, uuid[], uuid, integer) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.widget_push_targets(text[], boolean, uuid[], uuid, integer) TO service_role;

REVOKE ALL ON FUNCTION public.widget_push_settle(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.widget_push_settle(jsonb) FROM anon;
REVOKE ALL ON FUNCTION public.widget_push_settle(jsonb) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.widget_push_settle(jsonb) TO service_role;

REVOKE ALL ON FUNCTION public._revoke_widget_grants_for_session() FROM PUBLIC;
REVOKE ALL ON FUNCTION public._revoke_widget_grants_for_session() FROM anon;
REVOKE ALL ON FUNCTION public._revoke_widget_grants_for_session() FROM authenticated;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ── VERIFY ──────────────────────────────────────────────────────────────────────────────────
--
-- (A) FIRST on the local Supabase E2E stack (reference_local_supabase_e2e_stack.md), with the
--     trigger live: sign in twice (two sessions), mint a grant on each through the app's
--     POST /auth/refresh with {"widget_holdings_grant": true} and WIDGET_HOLDINGS_GRANTS_ENABLED
--     on, then:
--       * POST /auth/logout on session 1 → only session 1's grant reads revoked 'logout';
--       * present a retired refresh token of session 2 outside the 120 s grace → its grant reads
--         revoked 'reuse';
--       * mint again, POST /auth/sign-out-all → every grant reads revoked 'signout_all';
--       * GET /api/v1/widget/holdings-mover with no header → 401 AUTH_REQUIRED.
--     SELECT grant_id, sid, revoked_at, revoke_reason FROM public.widget_holdings_grants;
--
-- (B) Read-only, right after applying (local, then prod) → one row, every column t:
-- SELECT
--   to_regclass('public.widget_holdings_grants') IS NOT NULL                          AS table_ok,
--   (SELECT relrowsecurity FROM pg_class
--     WHERE oid = to_regclass('public.widget_holdings_grants'))                       AS rls_on,
--   NOT has_table_privilege('anon', 'public.widget_holdings_grants', 'SELECT')        AS anon_no_select,
--   NOT has_table_privilege('authenticated', 'public.widget_holdings_grants', 'SELECT') AS auth_no_select,
--   NOT has_function_privilege('anon', 'public.check_widget_holdings_grant(text)', 'EXECUTE')
--                                                                                     AS anon_no_check,
--   NOT has_function_privilege('anon',
--       'public.widget_push_targets(text[],boolean,uuid[],uuid,integer)', 'EXECUTE')  AS anon_no_targets,
--   (public.check_widget_holdings_grant(repeat('0', 64))->>'status') = 'unknown'      AS check_unknown,
--   EXISTS (SELECT 1 FROM pg_trigger
--            WHERE tgname = 'trg_auth_refresh_families_revoke_widget_grants'
--              AND tgrelid = 'public.auth_refresh_families'::regclass
--              AND tgenabled = 'O')                                                   AS trigger_on,
--   EXISTS (SELECT 1 FROM pg_constraint
--            WHERE conrelid = to_regclass('public.widget_holdings_grants')
--              AND contype = 'f' AND confdeltype = 'c')                               AS fk_cascades;
--
-- (C) If (B) ran before PostgREST picked the functions up, run once more:
--   NOTIFY pgrst, 'reload schema';
