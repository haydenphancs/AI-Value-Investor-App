-- 211_deleted_account_fingerprints.sql
--
-- Why (sign-in audit CC-01, 2026-11-26): every new public.users row is seeded the free tier's
-- welcome grant (50 credits, `create_user_credits`, from `plan_credits`), and DELETE /users/me
-- hard-deletes the auth row. So the same person could mint another 50 credits — two AI reports,
-- ~17 Gemini + ~20 FMP calls each — as often as they liked:
--   * delete the account in Settings, then sign up again with the same email, Apple ID or
--     Google account (a captured Google id_token can even be replayed: native Google sends no
--     nonce), or
--   * register Gmail aliases of one inbox (foo+1@gmail.com, f.oo@gmail.com, ...). The register
--     limiter keys on the literal address, so every alias was a fresh account and a fresh grant.
-- Unbounded free spend, and a loop that saturates REPORT_GET_MAX_INFLIGHT gives PAYING users
-- 409 SYSTEM_BUSY.
--
-- Owner decisions, 2026-11-26: a returning account opens with "0 credits (Recommended)" (the
-- normal monthly allowance still arrives at the next ET month boundary), and a deleted account's
-- fingerprints are kept "90 days (Recommended)".
--
-- What this adds:
--   1. A Vault secret, `deleted_account_fingerprint_key`: a random 32-byte HMAC key generated
--      HERE, inside the database, so nobody ever sees it. Vault keeps it encrypted at rest and
--      out of a plain dump of the tables. NEVER rotate or delete it: every stored fingerprint
--      would stop matching (the protection silently turns off for 90 days).
--   2. `public.deleted_account_fingerprints`: one row per HMAC-SHA256 fingerprint of a DELETED
--      account's canonical email (lowercased, +tag stripped, Gmail dots removed,
--      googlemail.com -> gmail.com) and of each of its Apple/Google subjects
--      (`auth.identities.provider_id`). No user id, no email: the row cannot be read back into
--      an address, and it cannot be linked to the deleted account. Kept 90 days from the last
--      deletion (`expires_at`), then removed by the first sign-up or deletion after that, and
--      never matched once expired. SERVICE-ROLE ONLY.
--   3. `record_deleted_account(uuid)` — called by DELETE /users/me BEFORE `admin.delete_user`,
--      while auth.users and auth.identities still hold the account. service_role only.
--   4. A DEFERRED constraint trigger on auth.users AFTER INSERT. It runs at COMMIT of GoTrue's
--      sign-up transaction, when the new account's identity rows exist too (GoTrue inserts the
--      user row first and its identity second, in one transaction, so an ordinary AFTER INSERT
--      trigger cannot see the Apple/Google subject). If one of the new account's fingerprints
--      matches an unexpired row, OR another live account has the same canonical email (an
--      alias of a live account), the untouched welcome grant is withheld: a `welcome_withheld`
--      ledger row (granted pool only, -50) and user_credits.total 0 (tier_alloc stays at the
--      period's allowance, or ensure_credit_period would top the 50 back up). The purchased
--      pool is never read or written (App Store 3.1.1, SYSTEM_DESIGN_GUIDELINES §9b.1).
--
-- FAILS OPEN, deliberately, everywhere on the sign-up path (the 044 pattern): an error inside
-- the trigger is caught, logged as a Postgres WARNING, and the account keeps its normal grant.
-- Blocking sign-up over a credit control would be the worse failure. `record_deleted_account`
-- RAISES instead (a missing Vault key, say) — the backend logs that at ERROR and still deletes
-- the account, because deletion is an App Store 5.1.1(v) and privacy obligation.
--
-- ORDER: apply BEFORE deploying the backend that calls `record_deleted_account`. Until it is
-- applied that call answers PGRST202 and the backend logs a WARNING and deletes anyway, so the
-- other order is safe too — it just records nothing.
--
-- Idempotent: the secret is created only when absent, every object uses IF NOT EXISTS /
-- CREATE OR REPLACE / DROP ... IF EXISTS, and the backfill-free design needs no data step.
--
-- PRIVACY POLICY: needs one sentence (owner): on deletion Caydex keeps, for 90 days, a one-way
-- keyed fingerprint of the account's email address and Apple/Google sign-in ID, used only to
-- decide whether a new account receives the free welcome credits.
--
-- VERIFY after applying (SQL editor):
--   SELECT count(*) FROM vault.decrypted_secrets WHERE name = 'deleted_account_fingerprint_key';  -- 1
--   SELECT tgname, tgdeferrable, tginitdeferred FROM pg_trigger
--    WHERE tgrelid = 'auth.users'::regclass AND tgname = 'trg_withhold_returning_welcome';      -- t, t
--   SELECT public.canonical_account_email('  F.Oo+news@GoogleMail.com ');                        -- foo@gmail.com
--   SELECT has_function_privilege('anon', 'public.record_deleted_account(uuid)', 'EXECUTE');      -- f
--   SELECT has_table_privilege('authenticated', 'public.deleted_account_fingerprints', 'SELECT'); -- f
--
-- AFTER APPLYING: re-run scripts/dump_schema.sh and scripts/generate_schema_doc.py (the table is
-- curated in scripts/schema_curation.py).

-- ── 1. The HMAC key, generated inside the database ──────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM vault.decrypted_secrets WHERE name = 'deleted_account_fingerprint_key'
    ) THEN
        PERFORM vault.create_secret(
            encode(extensions.gen_random_bytes(32), 'hex'),
            'deleted_account_fingerprint_key',
            'HMAC key for public.deleted_account_fingerprints (migration 211). Never rotate or '
            'delete it: every stored fingerprint would stop matching.'
        );
    END IF;
END
$$;

-- ── 2. The ledger ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.deleted_account_fingerprints (
    fingerprint       TEXT PRIMARY KEY CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    kind              TEXT NOT NULL CHECK (kind ~ '^[a-z0-9_]{1,32}$'),
    first_recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL,
    times_recorded    INTEGER NOT NULL DEFAULT 1 CHECK (times_recorded >= 1),
    times_matched     INTEGER NOT NULL DEFAULT 0 CHECK (times_matched >= 0),
    last_matched_at   TIMESTAMPTZ
);

COMMENT ON TABLE public.deleted_account_fingerprints IS
    'HMAC-SHA256 fingerprints (Vault key deleted_account_fingerprint_key) of DELETED accounts: '
    'canonical email and Apple/Google subject. No user id, no email. Read only by the sign-up '
    'trigger that withholds the free welcome grant from a returning account; written by '
    'record_deleted_account() from DELETE /users/me. Kept 90 days from the last deletion '
    '(expires_at), never matched after that. Migration 211, audit CC-01. SERVICE-ROLE ONLY.';

CREATE INDEX IF NOT EXISTS idx_deleted_account_fingerprints_expires
    ON public.deleted_account_fingerprints (expires_at);

ALTER TABLE public.deleted_account_fingerprints ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "deleted_account_fingerprints_service_all" ON public.deleted_account_fingerprints;
CREATE POLICY "deleted_account_fingerprints_service_all" ON public.deleted_account_fingerprints
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.deleted_account_fingerprints FROM anon, authenticated;
GRANT  ALL ON public.deleted_account_fingerprints TO service_role;

-- ── 3. Canonical email (pure; the same address for every alias of one inbox) ───────────────
-- lower + trim; the local part loses everything from its first '+'; for gmail.com and
-- googlemail.com it also loses every '.', and googlemail.com becomes gmail.com. NULL for
-- anything without a non-empty local part and domain. A local part that would become empty
-- (a '+tag'-only address) is kept as it was rather than collapsing every such address into one.
-- IMMUTABLE: `idx_users_canonical_email` below is built on it — change the body and you must
-- REINDEX that index in the same migration.
CREATE OR REPLACE FUNCTION public.canonical_account_email(p_email text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_email  text;
    v_at     integer;
    v_local  text;
    v_domain text;
    v_plus   integer;
BEGIN
    IF p_email IS NULL THEN
        RETURN NULL;
    END IF;
    v_email := lower(btrim(p_email, E' \t\r\n'));
    IF strpos(v_email, '@') = 0 THEN
        RETURN NULL;
    END IF;
    -- Split at the LAST '@' (a quoted local part may hold another one).
    v_at := length(v_email) - strpos(reverse(v_email), '@') + 1;
    v_local := left(v_email, v_at - 1);
    v_domain := substr(v_email, v_at + 1);
    IF v_local = '' OR v_domain = '' THEN
        RETURN NULL;
    END IF;
    v_plus := strpos(v_local, '+');
    IF v_plus > 1 THEN
        v_local := left(v_local, v_plus - 1);
    END IF;
    IF v_domain IN ('gmail.com', 'googlemail.com') THEN
        IF replace(v_local, '.', '') <> '' THEN
            v_local := replace(v_local, '.', '');
        END IF;
        v_domain := 'gmail.com';
    END IF;
    RETURN v_local || '@' || v_domain;
END;
$$;

COMMENT ON FUNCTION public.canonical_account_email(text) IS
    'One address per inbox: lower(trim), local part cut at its first +, Gmail dots removed, '
    'googlemail.com -> gmail.com. NULL without a local part and a domain. IMMUTABLE (an '
    'index uses it). Migration 211.';

REVOKE ALL ON FUNCTION public.canonical_account_email(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.canonical_account_email(text) FROM anon;
REVOKE ALL ON FUNCTION public.canonical_account_email(text) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.canonical_account_email(text) TO service_role;

-- The alias check reads this. Without it every sign-up would scan public.users.
CREATE INDEX IF NOT EXISTS idx_users_canonical_email
    ON public.users (public.canonical_account_email(email));

-- ── 4. Fingerprints (internal; nobody but the two functions below may call these) ──────────
-- HMAC-SHA256 over "<kind>:<value>" with the Vault key. RAISES when the key is missing, so a
-- missing key can never produce an unkeyed (dictionary-reversible) fingerprint.
CREATE OR REPLACE FUNCTION public._account_fingerprint(p_kind text, p_value text)
RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_key bytea;
BEGIN
    IF p_kind IS NULL OR p_value IS NULL OR p_value = '' THEN
        RETURN NULL;
    END IF;
    SELECT decode(s.decrypted_secret, 'hex') INTO v_key
      FROM vault.decrypted_secrets s
     WHERE s.name = 'deleted_account_fingerprint_key';
    IF v_key IS NULL OR length(v_key) < 32 THEN
        RAISE EXCEPTION 'deleted_account_fingerprint_key is missing from Vault (migration 211)';
    END IF;
    RETURN encode(
        extensions.hmac(convert_to(p_kind || ':' || p_value, 'UTF8'), v_key, 'sha256'),
        'hex'
    );
END;
$$;

REVOKE ALL ON FUNCTION public._account_fingerprint(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public._account_fingerprint(text, text) FROM anon;
REVOKE ALL ON FUNCTION public._account_fingerprint(text, text) FROM authenticated;
REVOKE ALL ON FUNCTION public._account_fingerprint(text, text) FROM service_role;

-- Every fingerprint of one account, read from the auth schema: the canonical form of its
-- email and of each identity's email, and each non-email identity's provider subject
-- ("apple", "google", ...). An 'email' or 'phone' identity's provider_id is the user's own
-- uuid or number, which a new account never repeats, so it is not a key.
CREATE OR REPLACE FUNCTION public._account_fingerprints(p_user_id uuid)
RETURNS TABLE (kind text, fingerprint text)
LANGUAGE sql
STABLE
SET search_path TO 'public', 'pg_temp'
AS $$
    WITH keys AS (
        SELECT 'email'::text AS kind, public.canonical_account_email(u.email) AS value
          FROM auth.users u
         WHERE u.id = p_user_id
        UNION
        SELECT 'email', public.canonical_account_email(i.identity_data ->> 'email')
          FROM auth.identities i
         WHERE i.user_id = p_user_id
        UNION
        SELECT lower(i.provider), i.provider_id
          FROM auth.identities i
         WHERE i.user_id = p_user_id
           AND lower(i.provider) NOT IN ('email', 'phone')
           AND lower(i.provider) ~ '^[a-z0-9_]{1,32}$'
           AND COALESCE(i.provider_id, '') <> ''
    )
    SELECT DISTINCT k.kind, public._account_fingerprint(k.kind, k.value)
      FROM keys k
     WHERE k.value IS NOT NULL AND k.value <> '';
$$;

REVOKE ALL ON FUNCTION public._account_fingerprints(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public._account_fingerprints(uuid) FROM anon;
REVOKE ALL ON FUNCTION public._account_fingerprints(uuid) FROM authenticated;
REVOKE ALL ON FUNCTION public._account_fingerprints(uuid) FROM service_role;

-- ── 5. Recording a deletion (DELETE /users/me, before admin.delete_user) ───────────────────
-- Returns how many fingerprints were written or refreshed: 0 means the auth row was already
-- gone (the backend logs a WARNING). Raises on a real failure; the backend logs ERROR and still
-- deletes the account.
CREATE OR REPLACE FUNCTION public.record_deleted_account(p_user_id uuid)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
SET row_security TO 'off'
AS $$
DECLARE
    v_n integer := 0;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN 0;
    END IF;

    -- Retention: nothing older than 90 days survives a write.
    DELETE FROM public.deleted_account_fingerprints WHERE expires_at <= now();

    INSERT INTO public.deleted_account_fingerprints AS f (fingerprint, kind, expires_at)
    SELECT DISTINCT ON (fp.fingerprint) fp.fingerprint, fp.kind, now() + INTERVAL '90 days'
      FROM public._account_fingerprints(p_user_id) fp
     WHERE fp.fingerprint IS NOT NULL
     ORDER BY fp.fingerprint, fp.kind
    ON CONFLICT (fingerprint) DO UPDATE
       SET last_recorded_at = now(),
           expires_at       = now() + INTERVAL '90 days',
           times_recorded   = f.times_recorded + 1;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END;
$$;

COMMENT ON FUNCTION public.record_deleted_account(uuid) IS
    'Records the fingerprints of an account about to be deleted (DELETE /users/me, before '
    'admin.delete_user) for 90 days, and prunes expired rows. Returns the rows written. '
    'Raises on failure. service_role only. Migration 211, audit CC-01.';

REVOKE ALL ON FUNCTION public.record_deleted_account(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_deleted_account(uuid) FROM anon;
REVOKE ALL ON FUNCTION public.record_deleted_account(uuid) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.record_deleted_account(uuid) TO service_role;

-- ── 6. Withholding the welcome grant at sign-up (deferred: runs at COMMIT) ─────────────────
CREATE OR REPLACE FUNCTION public.withhold_returning_welcome_credits()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
SET row_security TO 'off'
AS $$
DECLARE
    _GUEST   CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_hits   integer := 0;
    v_alias  boolean := FALSE;
    v_canon  text;
    v_row    public.user_credits%ROWTYPE;
    v_others integer;
BEGIN
    IF NEW.id = _GUEST THEN
        RETURN NULL;
    END IF;

    -- Retention: an expired row is removed here too, and never matched below.
    DELETE FROM public.deleted_account_fingerprints WHERE expires_at <= now();

    UPDATE public.deleted_account_fingerprints f
       SET times_matched = f.times_matched + 1,
           last_matched_at = now()
     WHERE f.expires_at > now()
       AND f.fingerprint IN (
           SELECT fp.fingerprint FROM public._account_fingerprints(NEW.id) fp
            WHERE fp.fingerprint IS NOT NULL
       );
    GET DIAGNOSTICS v_hits = ROW_COUNT;

    -- An alias of a LIVE account (foo+2@gmail.com beside foo@gmail.com) is the same inbox: it
    -- has had its welcome grant already.
    IF v_hits = 0 THEN
        v_canon := public.canonical_account_email(NEW.email);
        IF v_canon IS NOT NULL THEN
            -- Two alias sign-ups committing at the same moment would each miss the other's
            -- uncommitted row. This lock (held to commit) serialises them on the canonical
            -- address, so the second one's EXISTS, a new statement under READ COMMITTED, sees
            -- the first one's committed row. A hash collision only serialises two sign-ups.
            PERFORM pg_advisory_xact_lock(hashtext('welcome:' || v_canon));
            SELECT EXISTS (
                SELECT 1 FROM public.users u
                 WHERE public.canonical_account_email(u.email) = v_canon
                   AND u.id <> NEW.id
                   AND u.id <> _GUEST
            ) INTO v_alias;
        END IF;
    END IF;

    IF v_hits = 0 AND NOT v_alias THEN
        RETURN NULL;
    END IF;

    SELECT * INTO v_row FROM public.user_credits WHERE user_id = NEW.id FOR UPDATE;
    IF NOT FOUND THEN
        -- create_user_credits failed (it fails soft). ensure_credit_period will seed this account
        -- on first read; say so rather than guess.
        RAISE WARNING 'withhold_returning_welcome_credits: no user_credits row for returning user %', NEW.id;
        RETURN NULL;
    END IF;

    -- Only the untouched opening grant, in the GRANTED pool. Anything else means this is not
    -- the brand-new account the trigger was written for: leave it alone and say so.
    IF v_row.used <> 0 OR v_row.purchased_total <> 0 OR v_row.purchased_used <> 0
       OR v_row.paid_period_id IS NOT NULL OR v_row.total <= 0
       OR v_row.total <> v_row.tier_alloc THEN
        RAISE WARNING 'withhold_returning_welcome_credits: user % is not an untouched opening grant (total %, used %, tier_alloc %); left alone',
            NEW.id, v_row.total, v_row.used, v_row.tier_alloc;
        RETURN NULL;
    END IF;
    SELECT count(*) INTO v_others
      FROM public.credit_transactions
     WHERE user_id = NEW.id AND reason <> 'grant';
    IF v_others > 0 THEN
        RAISE WARNING 'withhold_returning_welcome_credits: user % already has % ledger row(s) besides the opening grant; left alone',
            NEW.id, v_others;
        RETURN NULL;
    END IF;

    -- Append, never rewrite: the opening grant row stays, and this row takes it back, so the
    -- Credit History reads "+50 Welcome credits, -50 Welcome credits already claimed" and
    -- SUM(delta) still equals `spendable`. ref_id NULL so no refund can ever pair with it.
    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
    VALUES (NEW.id, -v_row.total, -v_row.total, 0, 'welcome_withheld', NULL, 0);

    -- `total` ONLY. `tier_alloc` stays at the period's allowance on purpose: ensure_credit_period
    -- tops a calendar account up whenever `tier_alloc` is below its plan's allocation (the
    -- mid-month raise branch, 139/196), so zeroing it re-granted the 50 as `tier_upgrade` on the
    -- very next balance read (found by the local end-to-end run, 2026-11-26). Left at 50 it says
    -- "this period's allowance is accounted for", and a real mid-month raise (a comp tier) still
    -- tops up to the full new allocation, because that branch adds `alloc - total`.
    UPDATE public.user_credits
       SET total = 0, updated_at = now()
     WHERE user_id = NEW.id;

    RAISE LOG 'withhold_returning_welcome_credits: welcome grant withheld for user % (fingerprint hits %, live alias %)',
        NEW.id, v_hits, v_alias;
    RETURN NULL;
EXCEPTION WHEN OTHERS THEN
    -- Never block a sign-up over a credit control (the 044 pattern). The block's own writes are
    -- rolled back with it, so a half-withheld grant cannot be left behind.
    RAISE WARNING 'withhold_returning_welcome_credits failed for user %: % (SQLSTATE %)',
        NEW.id, SQLERRM, SQLSTATE;
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION public.withhold_returning_welcome_credits() IS
    'DEFERRED AFTER INSERT on auth.users (runs at commit, when the identity rows exist): '
    'withholds the untouched free welcome grant (granted pool only) from an account whose '
    'email/Apple/Google fingerprint matches an unexpired deleted_account_fingerprints row, or '
    'whose canonical email belongs to another live account. Fails open. Migration 211, CC-01.';

REVOKE ALL ON FUNCTION public.withhold_returning_welcome_credits() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.withhold_returning_welcome_credits() FROM anon;
REVOKE ALL ON FUNCTION public.withhold_returning_welcome_credits() FROM authenticated;

DROP TRIGGER IF EXISTS trg_withhold_returning_welcome ON auth.users;
CREATE CONSTRAINT TRIGGER trg_withhold_returning_welcome
    AFTER INSERT ON auth.users
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.withhold_returning_welcome_credits();
