-- 199_feedback_reward_credits.sql
--
-- Why: Profile → Help Us Improve becomes "Report an issue · earn free credits". A user sends a
-- report, the owner checks BY HAND that the issue is real, and only then grants about 20
-- credits. Nothing in the app or the backend can trigger a grant: the one caller is
-- `scripts/grant_feedback_reward.py --apply`, run by the owner after confirming a report.
-- The app copy promises exactly that ("If we can confirm the issue, we'll add credits"), so a
-- grant that fired on its own would make the copy false.
--
-- What this migration does:
--
--   * grant_feedback_reward(p_user_id, p_credits, p_ref_id) (new): adds p_credits to the
--     PURCHASED pool and writes one `feedback_reward` ledger row, once per (user, report code).
--     It is add_purchased_credits (117) without the credit_purchases claim: a reward is not
--     revenue, and a $0 purchase row would put a fabricated sale in the revenue table (the
--     same reason scripts/seed_testflight_testers.py never calls add_purchased_credits).
--   * A partial UNIQUE index on (user_id, ref_id) for `feedback_reward` ledger rows: a second
--     line of defence behind the function's own replay lookup.
--   * The user_credits.purchased_total comment now names every writer of the column.
--
-- WHY THE PURCHASED POOL. ensure_credit_period overwrites the granted pool (total/used) at
-- every calendar reset and every paid-period change (194, 196, 198), so a reward parked there
-- would vanish on the user's next credits read — usually before they saw it. purchased_total
-- is never touched by the tier RPCs. The reward therefore never expires, and the statement
-- says so ("Never expires", credit_history_service._pool_note).
--
-- Checks, in this order (each refusal is an 'invalid' outcome with a 'reason', never a RAISE,
-- so the script prints why instead of string-matching a PostgREST error):
--   1. guest sentinel or NULL user;
--   2. p_credits outside 1.._MAX (200). The cap catches a typo (2000 for 20), not abuse: only
--      service_role can call this;
--   3. a blank report code, or one longer than 64 characters;
--   4. no public.users row (user_credits references it, so ensure_credit_period would fail);
--   5. ensure_credit_period(p_user_id), which takes SELECT … FOR UPDATE on the balance row for
--      the rest of the transaction. Everything below runs under that lock, so two concurrent
--      grants of the same report serialise and the second one sees the first one's row;
--   6. a `feedback_reward` row already exists for (user, ref) → 'replay', nothing changes;
--   7. raise purchased_total, write the ledger row (granted_delta 0, purchased_delta
--      p_credits) → 'granted'.
--
-- Outcomes:
--   {"outcome":"granted", "credits":N, "spendable":N}
--   {"outcome":"replay",  "credits":N, "spendable":N, "granted_at":"…"}   (the earlier grant)
--   {"outcome":"invalid", "reason":"…"}
--
-- refund_credits can never pair with the reward row: it matches only debits (delta = -amount,
-- amount > 0), and this row's delta is positive.
--
-- Deploy: apply in Supabase Studio, then run scripts/check_function_grants.py. No backend
-- deploy depends on it; until it is applied the script's --apply reports PGRST202 and says
-- "apply migration 199 first".
--
-- Depends on 117 (purchased pool), 118 (ledger split columns) and 198 (the ensure_credit_period
-- in force). Idempotent: CREATE OR REPLACE (never DROP + CREATE, which would discard the ACL),
-- CREATE UNIQUE INDEX IF NOT EXISTS, and the ACL re-asserted below.

BEGIN;
SET LOCAL lock_timeout = '5s';

-- ── 0. Preflight: the ledger must carry the two-pool split ─────────────────────────────────
-- plpgsql resolves column names at call time, not at CREATE time, so applying this before 118
-- would succeed and leave a function that fails on its first grant.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'credit_transactions'
                      AND column_name = 'purchased_delta') THEN
        RAISE EXCEPTION '199: public.credit_transactions.purchased_delta does not exist; apply 117 and 118 first';
    END IF;
END $$;

-- ── 1. The second line of defence: one reward per (user, report code) ──────────────────────
-- Partial, so it stays tiny and adds nothing to the ledger's other writes (the build itself scans
-- the table once, holding a SHARE lock that briefly blocks ledger writes). The function's replay
-- lookup runs under the balance lock and is the real guard; this index turns a future writer that
-- skips that lookup into a unique violation instead of a double grant.

CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_transactions_feedback_reward_ref
    ON public.credit_transactions (user_id, ref_id)
    WHERE reason = 'feedback_reward';

-- ── 2. grant_feedback_reward ──────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.grant_feedback_reward(
    p_user_id UUID,
    p_credits INTEGER,
    p_ref_id  TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST      CONSTANT UUID    := '00000000-0000-4000-8000-00000000dead';
    -- Mirrors scripts/grant_feedback_reward.py MAX_CREDITS (tests/test_feedback_reward_grant.py).
    _MAX        CONSTANT INTEGER := 200;
    v_ref       TEXT;
    v_spendable INTEGER;
    v_prior     public.credit_transactions%ROWTYPE;
BEGIN
    -- Every signed-out install shares the sentinel; crediting it would hand the reward to all
    -- of them. Same guard as ensure_credit_period / add_purchased_credits.
    IF p_user_id IS NULL OR p_user_id = _GUEST THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'guest_or_null_user');
    END IF;

    IF p_credits IS NULL OR p_credits < 1 OR p_credits > _MAX THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'credits_out_of_range');
    END IF;

    -- The report code is the idempotency key. A NULL would defeat the unique index (NULLs are
    -- distinct), so the same report could be rewarded repeatedly.
    v_ref := NULLIF(btrim(p_ref_id), '');
    IF v_ref IS NULL OR length(v_ref) > 64 THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'bad_ref');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.users WHERE id = p_user_id) THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'unknown_user');
    END IF;

    -- Creates the balance row on a first touch, rolls a due period, and holds FOR UPDATE on
    -- the row until COMMIT. The replay lookup below must come AFTER it: before the lock, two
    -- concurrent grants of one report could both find nothing.
    PERFORM public.ensure_credit_period(p_user_id);

    SELECT * INTO v_prior
      FROM public.credit_transactions
     WHERE user_id = p_user_id AND reason = 'feedback_reward' AND ref_id = v_ref
     ORDER BY id
     LIMIT 1;
    IF FOUND THEN
        SELECT spendable INTO v_spendable FROM public.user_credits WHERE user_id = p_user_id;
        RETURN jsonb_build_object(
            'outcome',    'replay',
            'credits',    v_prior.delta,
            'spendable',  COALESCE(v_spendable, 0),
            'granted_at', v_prior.created_at
        );
    END IF;

    UPDATE public.user_credits
       SET purchased_total = purchased_total + p_credits,
           updated_at      = NOW()
     WHERE user_id = p_user_id
    RETURNING spendable INTO v_spendable;

    IF v_spendable IS NULL THEN
        -- ensure_credit_period guarantees a row for a real user. RAISE rather than report a
        -- grant that moved nothing.
        RAISE EXCEPTION
            'grant_feedback_reward: no user_credits row for % after ensure_credit_period',
            p_user_id;
    END IF;

    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
    VALUES
        (p_user_id, p_credits, 0, p_credits, 'feedback_reward', v_ref, v_spendable);

    RETURN jsonb_build_object(
        'outcome',   'granted',
        'credits',   p_credits,
        'spendable', v_spendable
    );
END;
$$;

COMMENT ON FUNCTION public.grant_feedback_reward(UUID, INTEGER, TEXT) IS
    'MANUAL ONLY (199): the owner runs scripts/grant_feedback_reward.py in apply mode after '
    'confirming a Help Us Improve report by hand. Nothing in app/ or the iOS app calls it, and nothing may: '
    'the app promises credits only for an issue the owner has confirmed. Adds p_credits (1..200) '
    'to the never-expiring purchased pool and writes one feedback_reward ledger row (granted_delta '
    '0, purchased_delta p_credits, ref_id = the report code). Idempotent per (user, report code): '
    'a repeat returns ''replay'' and changes nothing. Writes no credit_purchases row (a reward is '
    'not revenue). service_role only.';

COMMENT ON COLUMN public.user_credits.purchased_total IS
    'Lifetime credits in the NEVER-EXPIRING pool. NEVER reset by ensure_credit_period / '
    'grant_tier_upgrade / revoke_tier_credits / revoke_paid_period / reinstate_paid_carry: App '
    'Store Guideline 3.1.1 forbids purchased credits expiring. Writers: add_purchased_credits '
    'raises it for a consumable in-app purchase (117); grant_feedback_reward raises it for an '
    'issue report the owner confirmed, manual-only (199); scripts/seed_testflight_testers.py '
    'raises it for TestFlight accounts (a direct UPDATE, never concurrently with a reward grant); '
    'revoke_purchased_credits lowers it on an Apple REFUND (117, 139). Nothing else writes it. '
    'See migrations 117, 199.';

-- ── ACL ───────────────────────────────────────────────────────────────────────────────────
-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by default and PostgREST exposes every
-- public function at /rest/v1/rpc/<name>. This one MINTS CREDITS: left open, anyone holding the
-- shipped anon key could reward themselves 200 credits per invented report code.

REVOKE ALL ON FUNCTION public.grant_feedback_reward(UUID, INTEGER, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.grant_feedback_reward(UUID, INTEGER, TEXT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.grant_feedback_reward(UUID, INTEGER, TEXT) TO service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;

-- VERIFY (run after applying):
--   1. One row: t, t, f, f, t.
--   SELECT to_regprocedure('public.grant_feedback_reward(uuid, integer, text)') IS NOT NULL AS has_fn,
--          to_regclass('public.idx_credit_transactions_feedback_reward_ref') IS NOT NULL AS has_index,
--          has_function_privilege('anon', 'public.grant_feedback_reward(uuid, integer, text)', 'EXECUTE') AS anon_exec,
--          has_function_privilege('authenticated', 'public.grant_feedback_reward(uuid, integer, text)', 'EXECUTE') AS auth_exec,
--          has_function_privilege('service_role', 'public.grant_feedback_reward(uuid, integer, text)', 'EXECUTE') AS service_exec;
--
--   2. Every reward so far, newest first (also what the script's dry run lists per user):
--   SELECT user_id, delta, ref_id, created_at FROM public.credit_transactions
--    WHERE reason = 'feedback_reward' ORDER BY id DESC LIMIT 50;
--
--   3. No report rewarded twice. Expect 0 rows.
--   SELECT user_id, ref_id, count(*) FROM public.credit_transactions
--    WHERE reason = 'feedback_reward' GROUP BY user_id, ref_id HAVING count(*) > 1;
