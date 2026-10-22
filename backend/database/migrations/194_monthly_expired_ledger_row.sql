-- 194_monthly_expired_ledger_row.sql
--
-- Why: the monthly reset is the ONE change to a balance that never got a ledger row. On the
-- first touch after `resets_at`, `ensure_credit_period` overwrites the granted pool
-- (total := allocation, used := 0) and ledgers only `monthly_reset` (+allocation) — the
-- `total - used` the old period still held simply vanishes. So SUM(credit_transactions.delta)
-- overstates the balance by every month's unspent allocation, and the Credit History screen
-- stops adding up after the first reset (owner decision 2026-10-12: "Expired-credits history
-- row").
--
-- The single change: in the reset branch, BEFORE the UPDATE and before the `monthly_reset`
-- row, the unspent remainder of the old period is ledgered as a `monthly_expired` row:
--   delta = granted_delta = -(total - used), purchased_delta = 0, ref_id NULL, balance_after 0.
-- Everything else is migration 139's body byte for byte (verified against
-- schema_snapshot.sql, whose dump of this function is identical to 139's), plus the one
-- `v_expired` declaration the branch needs.
--
--   * ref_id NULL: `refund_credits` pairs a refund with a debit on (user_id, ref_id, delta)
--     and `NULL = anything` is never true, so no refund can ever pair with an expiry. That is
--     why `refund_credits` is untouched (its clawback exclusion list stays as 139 left it).
--   * balance_after 0: the granted-pool writers (grant, monthly_reset, tier_upgrade,
--     tier_revoked) record the GRANTED remaining, and that is 0 right after an expiry — the
--     sibling `monthly_reset` row then records the new allocation. The two conventions for
--     this column are documented below rather than "fixed": nothing reads it.
--   * Purchased pool untouched (App Store Guideline 3.1.1): the UPDATE still names only the
--     granted columns, and the expiry row carries purchased_delta 0.
--   * Nothing expired, no row: a fully spent month writes no zero-delta row (the same rule
--     `revoke_tier_credits` follows with `IF v_delta <> 0`).
--   * Written first, so id order (the history's keyset order) reads expire, then reset;
--     newest-first the screen shows "+50 Monthly credits" above "-30 Unused monthly credits
--     expired".
--
-- Edge cases, all from the same arithmetic on the pre-UPDATE row read FOR UPDATE:
--   first touch (no user_credits row): the NOT FOUND branch, no expiry row · a downgrade or a
--   revoke's high-water mark: exactly what the old period held (used <= total is a CHECK, so
--   it is never negative; GREATEST is belt and braces) · NULL resets_at or several missed
--   months: ONE expiry of the old period, then ONE reset · concurrent callers: unchanged — the
--   waiter re-reads the committed row and finds resets_at in the future · guest: still returns
--   early · return value: unchanged.
--
-- Not a backfill: resets that already happened stay unledgered (the old body kept no record
-- of the pre-reset `used`). History written after this migration ties out:
-- SUM(delta) = user_credits.spendable.
--
-- Reader: app/services/credit_history_service.py maps `monthly_expired` to kind `expire`,
-- "Unused monthly credits expired" (tests/test_credit_history_reason_coverage.py fails the
-- build if this file lands without that mapping).
--
-- ⚠️ This is now the base definition: the next change to ensure_credit_period copies THIS
-- body, not 139's (tests/test_monthly_expired_ledger_row.py reads the latest definition).
--
-- Deploy order: backend first, then this, and BEFORE 2026-11-01 00:00 ET (04:00 UTC), the next
-- reset. Applied before the deploy, the new rows read "Credit adjustment" until it lands
-- (harmless). Applied after Nov 1, the October -> November transition is never ledgered.
--
-- Idempotent: CREATE OR REPLACE + COMMENT re-run; the ACL is re-asserted.
--
-- ⚠️ Never after 196 (196_paid_period_credit_grants.sql). Both replace ensure_credit_period and
-- the later one wins, so 194 applied last would silently put back the calendar-only body. The
-- guard below refuses that: once 196's table exists, this file raises and changes nothing
-- (re-apply the newest migration that defines ensure_credit_period instead: 200 when 200 is
-- applied, else 198 when 198 is, else 196 — each refuses to run after the next). On a first
-- apply, or a re-run before 196, it is a no-op.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.subscription_credit_periods') IS NOT NULL THEN
        RAISE EXCEPTION '194 must not be applied after 196: it would replace the newer ensure_credit_period. Re-apply the newest migration that defines it (200 when 200 is applied, else 198 when 198 is applied, else 196) instead.';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.ensure_credit_period(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST       CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_tier       public.user_tier;
    v_alloc      INTEGER;
    v_now_et     TIMESTAMP;
    v_next_reset TIMESTAMPTZ;
    v_row        public.user_credits%ROWTYPE;
    v_remaining  INTEGER;
    v_expired    INTEGER;
BEGIN
    IF p_user_id = _GUEST THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, 0);
    END IF;

    SELECT u.tier INTO v_tier FROM public.users u WHERE u.id = p_user_id;
    IF v_tier IS NULL THEN
        v_tier := 'free';
    END IF;
    SELECT monthly_credits INTO v_alloc FROM public.plan_credits WHERE tier = v_tier;
    IF v_alloc IS NULL THEN
        v_alloc := 0;
    END IF;

    v_now_et     := (now() AT TIME ZONE 'America/New_York');
    v_next_reset := (date_trunc('month', v_now_et) + INTERVAL '1 month')
                        AT TIME ZONE 'America/New_York';

    SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO public.user_credits (user_id, total, used, tier_alloc, resets_at, updated_at)
        VALUES (p_user_id, v_alloc, 0, v_alloc, v_next_reset, NOW())
        ON CONFLICT (user_id) DO NOTHING;
        IF FOUND THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, v_alloc, v_alloc, 0, 'grant',
                    to_char(v_now_et, 'YYYY-MM'), v_alloc);
            RETURN v_alloc;
        END IF;
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, v_alloc);
    END IF;

    IF v_row.resets_at IS NULL OR now() >= v_row.resets_at THEN
        -- 194: ledger what the old period still held BEFORE it is wiped. v_row is the
        -- pre-UPDATE row, read FOR UPDATE above. GRANTED pool only.
        v_expired := GREATEST(v_row.total - v_row.used, 0);
        IF v_expired > 0 THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, -v_expired, -v_expired, 0, 'monthly_expired', NULL, 0);
        END IF;

        UPDATE public.user_credits
           SET total      = v_alloc,
               used       = 0,
               tier_alloc = v_alloc,
               resets_at  = v_next_reset,
               updated_at = NOW()
         WHERE user_id = p_user_id;
        INSERT INTO public.credit_transactions
            (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
        VALUES (p_user_id, v_alloc, v_alloc, 0, 'monthly_reset',
                to_char(v_now_et, 'YYYY-MM'), v_alloc);
        RETURN v_alloc;
    END IF;

    RETURN (v_row.total - v_row.used);
END;
$$;

COMMENT ON FUNCTION public.ensure_credit_period(UUID) IS
    'Rolls the GRANTED pool into the current ET month on the first touch after resets_at: '
    'ledgers the unspent remainder of the old period as monthly_expired (migration 194; '
    'delta = granted_delta = -(total - used), ref_id NULL so no refund can pair with it), then '
    'resets total/used/tier_alloc and ledgers monthly_reset (+allocation). Never touches the '
    'purchased pool (App Store 3.1.1). Returns the granted remaining. service_role only.';

COMMENT ON COLUMN public.credit_transactions.balance_after IS
    'The balance after this row, as its WRITER saw it. Two conventions, kept for history: '
    'spend_credits / refund_credits / add_purchased_credits / revoke_purchased_credits / the '
    'tester seed write spendable (both pools); the granted-pool writers (grant, monthly_reset, '
    'monthly_expired, tier_upgrade, tier_revoked) write the GRANTED remaining only. Nothing '
    'reads it — the balance is user_credits.spendable, and SUM(delta) ties out to it for '
    'history written after migration 194.';

-- CREATE OR REPLACE keeps the existing grants; re-asserted so a future DROP + re-create of this
-- body can never come back EXECUTE-to-PUBLIC (a SECURITY DEFINER function that resets a
-- victim's period through PostgREST RPC).
REVOKE ALL ON FUNCTION public.ensure_credit_period(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ensure_credit_period(UUID) TO service_role;

COMMIT;

-- VERIFY (run after applying — one row: t, f, f, t):
--   SELECT pg_get_functiondef('public.ensure_credit_period(uuid)'::regprocedure)
--            LIKE '%monthly_expired%'                                                AS has_expired_row,
--          has_function_privilege('anon', 'public.ensure_credit_period(uuid)', 'EXECUTE')          AS anon_exec,
--          has_function_privilege('authenticated', 'public.ensure_credit_period(uuid)', 'EXECUTE') AS auth_exec,
--          has_function_privilege('service_role', 'public.ensure_credit_period(uuid)', 'EXECUTE')  AS service_exec;
--
-- OPTIONAL dry run on YOUR OWN account (ends in ROLLBACK, writes nothing). Expect, newest first:
-- monthly_reset (+allocation, ref 'YYYY-MM'), then monthly_expired (-(total - used), ref NULL,
-- balance_after 0) when the month was not fully spent:
--   BEGIN;
--   UPDATE public.user_credits SET resets_at = now() - interval '1 second' WHERE user_id = '<uuid>';
--   SELECT public.ensure_credit_period('<uuid>');
--   SELECT id, reason, delta, granted_delta, purchased_delta, ref_id, balance_after
--     FROM public.credit_transactions WHERE user_id = '<uuid>' ORDER BY id DESC LIMIT 3;
--   ROLLBACK;
--
-- AFTER the 2026-11-01 reset (expect malformed = 0 and expired <= resets):
--   SELECT count(*) FILTER (WHERE reason = 'monthly_expired')                         AS expired,
--          count(*) FILTER (WHERE reason = 'monthly_reset')                           AS resets,
--          count(*) FILTER (WHERE reason = 'monthly_expired'
--                             AND (delta >= 0 OR delta <> granted_delta
--                                  OR purchased_delta <> 0 OR ref_id IS NOT NULL))  AS malformed
--     FROM public.credit_transactions
--    WHERE created_at >= '2026-11-01 04:00+00';
