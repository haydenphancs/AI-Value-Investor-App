-- 200_credit_refund_roll_late_renewal_clawback_cover.sql
--
-- Why: three credit defects from the IAP composition audit (2026-10-17, phase 2), fixed in one
-- file because two of them redefine the same function (ensure_credit_period) and two the other
-- (refund_credits); separate files would each have to re-copy the previous one's body.
--
--   F10. refund_credits never rolled the credit period. A report charged just before a boundary
--        (the ET 1st for Free / complimentary accounts, or the start of a paid renewal) and
--        refunded after it, with no credit call in between, was paired against the ENDED
--        period's pool and answered 'refunded'; the next roll then expired those 20 credits
--        (monthly_expired). No REFUND LEAK page, and the Reports card said "Refunded". Had any
--        read or spend run first, the same refund answered capped_to_zero and paged (K42), so
--        the outcome depended on whether something else rolled the period first.
--   F11. A paid renewal that no credit call saw while its lineage row was active was never
--        granted. Apple renews early, so the renewal (P2) waits for P1 to end; if the row is in
--        billing grace or retry by then, or still 'active' past its end, the candidate key
--        (status 'active' with a future end) and the <<pick>> retarget both missed it. HOLD then
--        kept P1's leftovers (often 0: a 402 while Apple still entitles the user) and P2 never
--        got its plan_period row. Nearly dormant while App Store billing grace is OFF (nothing
--        writes grace_period or billing_retry then; only a row left 'active' past its end before
--        the expiry sweep flips it can hit it); it must be applied before grace is ever turned ON.
--   F15. After an Apple refund, the clawback's calendar floor paid back charges the refunded
--        allocation had already paid for. 196's REFUND branch writes off the spend above the
--        floor, but refund_credits caps a payback only by the current `used`: (a) a report
--        charged before the Apple refund and refunded after it was paid back again out of the
--        floor; (b) after a keyed clawback that started the next period, the same refund answered
--        capped_to_zero — a false "user is OWED" page whose runbook minted the credits; (c) a
--        REFUND_REVERSED reinstated the period without deducting what was spent from the floor
--        while it stood refunded.
--
-- What this migration does:
--
--   * subscription_credit_periods: revoked_cover (what the refunded allocation paid for above the
--     floor, still left to absorb later refunds of charges from that period) and
--     revoked_ledger_id (the user's last credit_transactions id at the clawback). Both are set by
--     ensure_credit_period's REFUND branch, read by refund_credits and the REINSTATE branch, and
--     cleared at reinstatement. No FK on revoked_ledger_id. applied_ledger_id (final review,
--     2026-10-21): the id of the period's own plan_period ledger row, set when the period starts;
--     refund_credits reads "charged inside this period" from it (ledger order, which follows the
--     user_credits lock) instead of transaction-start timestamps, which a spend that began before a
--     concurrent period start but took the lock after it could invert. NULL on periods started
--     before 200: those keep the applied_at timestamp rule.
--   * credit_transactions.covered (INTEGER NOT NULL DEFAULT 0, review round 2026-10-21): on a
--     refund row, how much of the charge a clawback had already paid for. A replay of a refund
--     (already_refunded) adds it to the credits the refund moved and reports any `short` amount, so
--     a lost reply's retry never reads a short refund as settled — a partly covered one (new in 200)
--     or a plain partial refund (inherited from 124 and 142). The ADD COLUMN with a constant default
--     is a catalog-only change, but it takes credit_transactions' ACCESS EXCLUSIVE lock for an
--     instant (lock_timeout 5 s: re-run if it times out).
--   * ensure_credit_period: 198's body with five edits.
--       (F11) The candidate key is also new when the in-force period's own lineage row records a
--       LATER period than the one in force, whatever its status, unless the register holds an
--       unreversed refund of it; the retarget to that row needs only that later period. Such a
--       period starts on the first credit touch after the one in force ends, even if its own end
--       has passed (HOLD then holds it).
--       (F15) The REFUND branch stamps revoked_cover / revoked_ledger_id; REINSTATE deducts the
--       granted-pool spend after that mark (up to the next paid period's start) from what it
--       restores, and adds back the refunds in that window of charges made BEFORE the clawback
--       (they went to the floor, which expires; a never-refunded pool would have received them).
--       A period clawed back before 200 has no mark and is reinstated exactly as before.
--       (ledger order) A period start records applied_ledger_id.
--     Everything else is 198's text verbatim, the calendar block and 194's monthly_expired row
--     included.
--   * refund_credits: 142's body with four edits.
--       (F10) It runs ensure_credit_period first — after its own FOR UPDATE and no_credits_row
--       check, then re-reads the pools — exactly as spend_credits does, so a refund after any
--       boundary is paired against the rolled pool and a straddling charge takes the documented
--       capped_to_zero path (owed, paged). Owner decision 2026-10-21: "Documented 'owed' path".
--       (F15) A charge an Apple-refunded allocation already paid for — its period clawed back
--       after the debit, the debit charged inside that period — is absorbed by the period's
--       revoked_cover first and credited to its revoked_remaining (a reversal gives it back).
--       Wholly covered, the new outcome covered_by_clawback (nothing owed) writes a 0/0/0 row
--       that pairs the debit, so a replay answers already_refunded. A reply carries `covered`
--       only when a clawback absorbed some of the charge; every other reply keeps 142's shape.
--       (replay) already_refunded carries `short` when the earlier refund settled less than the
--       charge (credits moved + covered); credit_service then pages it as a partial refund.
--   * Comments: both functions, the table, research_reports.refund_outcome (the new label).
--
-- Unchanged on purpose: revoke_paid_period (it reaches the new stamp through its two
-- ensure_credit_period calls), reinstate_paid_carry (it moves only the upgrade carry and needs no
-- stamp), revoke_tier_credits (Sandbox and the legacy path keep the old arithmetic: no cover, no
-- mark — a known gap), spend_credits, record_subscription_transaction.
--
-- Known limits (SDG §9b.12 "Migration 200 leaves"): "charged inside this period" is read in
-- ledger order from applied_ledger_id (periods started after 200) or from applied_at (periods
-- started before it, whose cover is then exposed to a millisecond start-time race). A period
-- REINSTATED after another lineage's period was in force keeps its start mark, so a debit charged in
-- that other period could be absorbed by this period's cover at a later clawback (two lineages plus
-- a reinstatement plus a second refund). The owner's D2 decision ("one allocation" for a
-- same-lineage resubscription) is kept.
--
-- Deploy order: deploy the backend first (credit_service knows covered_by_clawback and the
-- `covered` key), then apply this in Supabase Studio as `postgres`. Applied before the deploy it
-- is still money-safe — the old backend reads covered_by_clawback as an unrecognised outcome and
-- a partly covered refund as `partial`, and pages a REFUND LEAK for both (fails closed, but
-- noisy) — and F10/F11 need no deploy. Apply it before App
-- Store billing grace is ever turned ON.
--
-- Depends on 142 (refund_credits returns JSONB) and 198 (subscription_transactions and 198's
-- ensure_credit_period). Idempotent: ADD COLUMN IF NOT EXISTS, a guarded constraint, CREATE OR
-- REPLACE; re-running it is safe. After it, re-running 142, 194, 196 or 198 is refused (each
-- would bring back an older body): roll back with a NEW migration that redefines the functions.

BEGIN;
SET LOCAL lock_timeout = '5s';

-- ── 0. Preflight ───────────────────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF to_regclass('public.subscription_transactions') IS NULL THEN
        RAISE EXCEPTION '200 needs migration 198 (subscription_transactions) — apply 198 first';
    END IF;
    IF to_regprocedure('public.ensure_credit_period(uuid)') IS NULL THEN
        RAISE EXCEPTION '200 needs public.ensure_credit_period(uuid) (196/198) — apply 198 first';
    END IF;
    IF COALESCE(pg_get_function_result(
           to_regprocedure('public.refund_credits(uuid,integer,text,text)')), '') <> 'jsonb' THEN
        RAISE EXCEPTION '200 needs migration 142 (refund_credits returning jsonb) — apply 142 first';
    END IF;
END $$;

-- ── 1. subscription_credit_periods: what a clawback covered, and where the ledger stood ─────

ALTER TABLE public.subscription_credit_periods ADD COLUMN IF NOT EXISTS revoked_cover INTEGER;
ALTER TABLE public.subscription_credit_periods ADD COLUMN IF NOT EXISTS revoked_ledger_id BIGINT;
ALTER TABLE public.subscription_credit_periods ADD COLUMN IF NOT EXISTS applied_ledger_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'subscription_credit_periods_revoked_cover_nonneg') THEN
        ALTER TABLE public.subscription_credit_periods
            ADD CONSTRAINT subscription_credit_periods_revoked_cover_nonneg
            CHECK (revoked_cover IS NULL OR revoked_cover >= 0);
    END IF;
END $$;

COMMENT ON COLUMN public.subscription_credit_periods.revoked_cover IS
    'Set by ensure_credit_period''s REFUND branch: the spend above the calendar floor that the '
    'refunded allocation paid for (the clawback''s write-off), still left to absorb later refunds '
    'of charges made in this period — refund_credits moves what it absorbs to revoked_remaining. '
    'Cleared when the period is reinstated. NULL on periods clawed back before 200 and on '
    'periods revoke_tier_credits claws back (Sandbox / legacy): those keep the old behaviour. 200.';
COMMENT ON COLUMN public.subscription_credit_periods.revoked_ledger_id IS
    'Set by ensure_credit_period''s REFUND branch: the user''s last credit_transactions id when '
    'the period was clawed back (0 = none). A debit at or before it, charged inside this period, '
    'can be covered by revoked_cover; at reinstatement the granted-pool spend after it is deducted. '
    'Cleared when the period is reinstated. No FK on purpose. 200.';
COMMENT ON COLUMN public.subscription_credit_periods.applied_ledger_id IS
    'Set by ensure_credit_period when the period starts: the id of its own plan_period ledger row, '
    'read under the user_credits lock. refund_credits treats a debit with a later id as charged in '
    'this period (ledger order, not transaction start times). NULL on periods started before 200, '
    'which keep the applied_at rule. No FK on purpose. 200.';

ALTER TABLE public.credit_transactions ADD COLUMN IF NOT EXISTS covered INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'credit_transactions_covered_nonneg') THEN
        -- NOT VALID: every existing row holds the default 0, so nothing to scan under the lock.
        ALTER TABLE public.credit_transactions
            ADD CONSTRAINT credit_transactions_covered_nonneg CHECK (covered >= 0) NOT VALID;
    END IF;
END $$;

COMMENT ON COLUMN public.credit_transactions.covered IS
    'On a refund row (reverses_id set): how much of the charge an Apple-refund clawback had '
    'already paid for, so it was not paid back (refund_credits, migration 200). delta + covered = '
    'what the refund settled; a replay compares it with the charge and reports `short`. 0 on every '
    'other row and on rows written before 200.';

-- ── 2. ensure_credit_period — 198 + F11 (late renewal) + F15 (cover, mark, window) ──────────

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
    v_user_tier  public.user_tier;
    v_comp_tier  public.user_tier;
    v_alloc      INTEGER;
    v_now_et     TIMESTAMP;
    v_next_reset TIMESTAMPTZ;
    v_row        public.user_credits%ROWTYPE;
    v_remaining  INTEGER;
    v_expired    INTEGER;
    v_sub        public.subscriptions%ROWTYPE;
    v_has_sub    BOOLEAN := FALSE;
    v_prod_row   BOOLEAN := FALSE;
    v_sub_prod   BOOLEAN := FALSE;
    v_paid_row   BOOLEAN := FALSE;
    v_cur        public.subscription_credit_periods%ROWTYPE;
    v_has_cur    BOOLEAN := FALSE;
    v_cur_sub    public.subscriptions%ROWTYPE;
    v_has_cur_sub BOOLEAN := FALSE;
    v_cur_holds  BOOLEAN := FALSE;
    v_running    BOOLEAN := FALSE;
    v_new_key    BOOLEAN := FALSE;
    v_seen       BOOLEAN := FALSE;
    v_seen_row   public.subscription_credit_periods%ROWTYPE;
    v_reinstate  BOOLEAN := FALSE;
    v_start_paid BOOLEAN := FALSE;
    v_retarget   BOOLEAN := FALSE;
    v_plan_alloc INTEGER;
    v_carry      INTEGER := 0;
    v_cap        NUMERIC;
    v_dropped    INTEGER;
    v_restore    INTEGER;
    v_period_id  BIGINT;
    v_floor      INTEGER;
    v_writeoff   INTEGER;
    v_delta      INTEGER;
    v_mark       BIGINT;
    v_stop       BIGINT;
    v_window     INTEGER := 0;
    v_total      INTEGER;
    v_used       INTEGER;
    v_pre_back   INTEGER := 0;
    v_applied    BIGINT;
BEGIN
    IF p_user_id = _GUEST THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, 0);
    END IF;

    -- 198: one row per Apple LINEAGE. v_sub is the WINNING row: one that can carry a paid
    -- period now, then one that still entitles, Production, the higher tier, the later end. A
    -- one-row account reads exactly 196's row. NULL environment = written before 196: Production.
    SELECT * INTO v_sub
      FROM public.subscriptions s
     WHERE s.user_id = p_user_id
     ORDER BY (COALESCE(s.original_transaction_id, '') <> '') DESC,
              COALESCE(s.status = 'active' AND s.current_period_end > now(), FALSE) DESC,
              (s.status IN ('active', 'grace_period', 'billing_retry')) DESC,
              (COALESCE(s.environment, 'Production') = 'Production') DESC,
              (CASE s.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END) DESC,
              s.current_period_end DESC NULLS LAST,
              s.updated_at DESC,
              s.id
     LIMIT 1;
    v_has_sub  := FOUND;
    v_sub_prod := v_has_sub AND COALESCE(v_sub.environment, 'Production') = 'Production';
    -- Calendar sizing: ANY Production row (any status) means users.tier mirrors a paid tier, so
    -- only the complimentary floor may size the calendar allowance (196's rule, kept on purpose:
    -- restricting it to entitling rows would let a stale paid mirror size it after an expiry).
    v_prod_row := EXISTS (SELECT 1 FROM public.subscriptions s2
                           WHERE s2.user_id = p_user_id
                             AND COALESCE(s2.environment, 'Production') = 'Production');

    -- The CALENDAR allowance. users.tier sizes it unless a PRODUCTION subscriptions row exists:
    -- then only the complimentary floor does, and a paid allocation comes only from a paid
    -- period below. (No row: hand-set testers. A Sandbox row: TestFlight / App Review, which
    -- renew every few minutes and keep the pre-196 sizing.)
    SELECT u.tier, u.comp_tier INTO v_user_tier, v_comp_tier FROM public.users u WHERE u.id = p_user_id;
    v_tier := CASE WHEN v_prod_row THEN COALESCE(v_comp_tier, 'free')
                   ELSE COALESCE(v_user_tier, v_comp_tier, 'free') END;
    SELECT monthly_credits INTO v_alloc FROM public.plan_credits WHERE tier = v_tier;
    IF v_alloc IS NULL THEN
        v_alloc := 0;
    END IF;

    -- A row that can carry a paid period for this account: Production, and a tier above the
    -- complimentary floor (a comp tier at or above the plan keeps the calendar allowance).
    v_paid_row := v_sub_prod
        AND (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
          > (CASE v_tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END);

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
        END IF;
        -- Fall through: a paying account's first touch still gets its paid period below.
        SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 0;
        END IF;
    END IF;

    -- Which paid period the pool is in, its lineage's own row, and whether the winning row
    -- brings a new one. Reads only; nothing is written before the branches below.
    IF v_row.paid_period_id IS NOT NULL THEN
        SELECT * INTO v_cur FROM public.subscription_credit_periods WHERE id = v_row.paid_period_id;
        v_has_cur := FOUND;
        v_running := v_has_cur AND v_cur.period_end > now();
        IF v_has_cur THEN
            SELECT * INTO v_cur_sub
              FROM public.subscriptions
             WHERE user_id = p_user_id
               AND original_transaction_id = v_cur.original_transaction_id;
            v_has_cur_sub := FOUND;
        END IF;
    END IF;

    -- 198: the in-force period has ended but its OWN lineage still entitles (Apple's billing
    -- grace or retry, or a renewal not recorded yet): it holds, as 196's HOLD did for its row.
    v_cur_holds := COALESCE(
        v_has_cur AND NOT v_running AND v_has_cur_sub
        AND COALESCE(v_cur_sub.environment, 'Production') = 'Production'
        AND v_cur_sub.status IN ('active', 'grace_period', 'billing_retry')
        AND (CASE v_cur_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
          > (CASE v_tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END),
        FALSE);

    -- The candidate key is the winner's — at most once more, the in-force lineage's own (below).
    -- 200 (IAP audit F11): a key is new when the winning row is active with a future end — OR,
    -- while the in-force period's own lineage holds, when that lineage's row records a LATER
    -- period than the one in force (a renewal no credit call saw while it was active, now in
    -- grace, in retry, or past its end), unless the register holds an unreversed refund of it.
    -- Before 200 such a renewal was never granted, and the next billing grace HELD the period
    -- before it (often 0 left) through the whole grace period.
    <<pick>>
    LOOP
        v_new_key := COALESCE(
            v_paid_row
            AND ((v_sub.status = 'active' AND v_sub.current_period_end > now())
                 OR (v_cur_holds
                     AND v_sub.original_transaction_id = v_cur.original_transaction_id
                     AND v_sub.current_period_end > v_cur.period_end
                     AND NOT EXISTS (SELECT 1 FROM public.subscription_transactions t
                                      WHERE t.original_transaction_id = v_sub.original_transaction_id
                                        AND t.period_end = v_sub.current_period_end
                                        AND t.revoked_signed_at IS NOT NULL
                                        AND (t.reversed_signed_at IS NULL
                                             OR t.reversed_signed_at < t.revoked_signed_at))))
            AND COALESCE(v_sub.original_transaction_id, '') <> ''
            AND NOT (v_has_cur
                     AND v_cur.original_transaction_id = v_sub.original_transaction_id
                     AND v_cur.period_end = v_sub.current_period_end),
            FALSE);
        IF v_new_key THEN
            SELECT * INTO v_seen_row
              FROM public.subscription_credit_periods p
             WHERE p.original_transaction_id = v_sub.original_transaction_id
               AND p.period_end = v_sub.current_period_end;
            v_seen := FOUND;
            IF v_seen AND v_seen_row.user_id <> p_user_id THEN
                RAISE WARNING 'ensure_credit_period: paid period %/% is already granted to user %; user % not granted',
                    v_sub.original_transaction_id, v_sub.current_period_end, v_seen_row.user_id, p_user_id;
            END IF;
        END IF;

        -- Granted before and not in force (an older JWS re-submitted, or a period an upgrade
        -- superseded): never again — UNLESS this account's period was refunded and Apple reversed
        -- the refund (the row entitles again under the same key), which reinstates it once. 198: never
        -- while the transaction register still holds a refund of that period.
        v_reinstate := COALESCE(
            v_new_key AND v_seen AND v_seen_row.user_id = p_user_id
            AND v_seen_row.revoked_at IS NOT NULL AND NOT v_running
            AND NOT EXISTS (SELECT 1 FROM public.subscription_transactions t
                             WHERE t.original_transaction_id = v_seen_row.original_transaction_id
                               AND t.period_end = v_seen_row.period_end
                               AND t.revoked_signed_at IS NOT NULL
                               AND (t.reversed_signed_at IS NULL
                                    OR t.reversed_signed_at < t.revoked_signed_at)),
            FALSE);

        -- A new period starts when nothing paid is running, or at once when it is a higher tier
        -- than the running period (an upgrade). 198: while the in-force period's own lineage holds,
        -- ANOTHER lineage's same-or-lower tier waits too; the lineage's own next key does not (the
        -- retarget below evaluates it when another lineage wins the ORDER BY).
        v_start_paid := COALESCE(
            v_new_key AND NOT v_seen
            AND ((NOT v_running
                  AND NOT (v_cur_holds
                           AND v_sub.original_transaction_id <> v_cur.original_transaction_id
                           AND (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
                            <= (CASE v_cur.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)))
                 OR (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
                  > (CASE v_cur.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)),
            FALSE);

        -- 198: the in-force lineage's OWN next key. When the winner neither starts nor reinstates
        -- while the in-force period's lineage holds (another lineage held back, a winner granted
        -- elsewhere, one at the floor), that lineage's own row is evaluated once instead — so its
        -- renewal starts once its period has ended, as in 196, even when another lineage outranks it
        -- (a later end: early renewals, a recovery in billing grace; a higher tier after its own
        -- downgrade, which then starts as an upgrade on the next call). Otherwise only a HOLD was left.
        -- 200 (IAP audit F11): retargeted when it records a LATER period than the one in force,
        -- whatever its status (v_cur_holds already requires an entitling Production row).
        v_retarget := COALESCE(
            NOT v_start_paid AND NOT v_reinstate AND v_cur_holds
            AND v_cur_sub.current_period_end > v_cur.period_end
            AND COALESCE(v_cur_sub.original_transaction_id, '') <> ''
            AND v_sub.original_transaction_id IS DISTINCT FROM v_cur_sub.original_transaction_id,
            FALSE);
        EXIT pick WHEN NOT v_retarget;
        v_sub      := v_cur_sub;
        v_sub_prod := COALESCE(v_sub.environment, 'Production') = 'Production';
        v_paid_row := v_sub_prod
            AND (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
              > (CASE v_tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END);
        v_seen     := FALSE;
    END LOOP pick;

    -- CALENDAR MODE: no paid period in force and none starting.
    IF NOT v_has_cur AND NOT v_start_paid AND NOT v_reinstate THEN
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

        -- A tier raised mid-month (scripts/set_comp_tier.py, or a Sandbox upgrade) is topped
        -- up at once, as grant_tier_upgrade did (139); a lowered one keeps what it has until
        -- the 1st.
        IF v_alloc > v_row.tier_alloc THEN
            v_delta := GREATEST(v_alloc - v_row.total, 0);
            UPDATE public.user_credits
               SET total      = GREATEST(v_alloc, v_row.total),
                   tier_alloc = v_alloc,
                   updated_at = NOW()
             WHERE user_id = p_user_id;
            IF v_delta > 0 THEN
                INSERT INTO public.credit_transactions
                    (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
                VALUES (p_user_id, v_delta, v_delta, 0, 'tier_upgrade',
                        to_char(v_now_et, 'YYYY-MM'), GREATEST(v_alloc, v_row.total) - v_row.used);
            END IF;
            RETURN (GREATEST(v_alloc, v_row.total) - v_row.used);
        END IF;

        RETURN (v_row.total - v_row.used);
    END IF;

    v_remaining := GREATEST(v_row.total - v_row.used, 0);

    -- REFUND of the period in force: the revoked transaction IS that period (its lineage's row
    -- is revoked with the same original transaction AND the same period end — a refund of an
    -- older renewal, or of a later one not yet started, leaves it alone), or 198's
    -- revoke_paid_period asked for exactly this period. Claw back exactly as revoke_tier_credits
    -- (139) does, floored at the calendar allowance; mark the period revoked so a reversal can
    -- reinstate it.
    IF v_has_cur AND (
           v_cur.revoke_requested_at IS NOT NULL
        OR (v_has_cur_sub AND v_cur_sub.status = 'revoked'
            AND v_cur_sub.original_transaction_id = v_cur.original_transaction_id
            AND v_cur_sub.current_period_end = v_cur.period_end)) THEN
        v_floor    := GREATEST(v_alloc, v_row.used);
        v_writeoff := GREATEST(v_row.used - v_alloc, 0);
        v_delta    := v_floor - v_row.total;
        -- 200 (IAP audit F15): what the refunded allocation paid for above the floor
        -- (revoked_cover) and the user's last ledger id at the clawback (revoked_ledger_id), read
        -- under the user_credits lock held above. refund_credits absorbs a later refund of a
        -- charge that cover paid for instead of paying it back out of the calendar floor, and a
        -- reversal deducts what was spent from the floor after the mark.
        SELECT COALESCE(max(t.id), 0) INTO v_mark
          FROM public.credit_transactions t WHERE t.user_id = p_user_id;
        UPDATE public.subscription_credit_periods
           SET revoked_at        = now(),
               revoked_remaining = v_remaining,
               revoked_cover     = v_writeoff,
               revoked_ledger_id = v_mark
         WHERE id = v_cur.id;
        UPDATE public.user_credits
           SET total          = v_floor - v_writeoff,
               used           = used - v_writeoff,
               tier_alloc     = LEAST(v_row.tier_alloc, v_alloc),
               resets_at      = v_next_reset,
               paid_period_id = NULL,
               updated_at     = NOW()
         WHERE user_id = p_user_id;
        IF v_delta <> 0 THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, v_delta, v_delta, 0, 'tier_revoked',
                    to_char(v_now_et, 'YYYY-MM'), v_floor - v_row.used);
        END IF;
        RETURN (v_floor - v_row.used);
    END IF;

    -- REFUND_REVERSED: reinstate the revoked period, once per clawback, with what the refund took
    -- (200: less the window's net spend, plus refunds of charges made before the clawback).
    -- The calendar leftovers since the refund expire, as on any start of a paid period.
    IF v_reinstate THEN
        v_restore := COALESCE(v_seen_row.revoked_remaining, 0);
        v_total   := v_restore;
        v_used    := 0;
        -- 200 (IAP audit F15): what the user spent from the calendar floor while the period stood
        -- refunded — the granted-pool debits and refunds after the clawback's mark, up to the next
        -- paid period's start — is deducted, or the floor's spends were never paid for. A period
        -- clawed back before 200 has no mark and is reinstated exactly as before.
        IF v_seen_row.revoked_ledger_id IS NOT NULL THEN
            SELECT min(t.id) INTO v_stop
              FROM public.credit_transactions t
             WHERE t.user_id = p_user_id
               AND t.id > v_seen_row.revoked_ledger_id
               AND t.reason IN ('plan_period', 'plan_reinstated');
            -- Refunds of charges made before the clawback (reverses_id at or below the mark) go
            -- back into the period's total, as they would have into a never-refunded pool; the
            -- window's own charges and their refunds stay netted into `used`, so a window charge
            -- refunded after the reversal still pairs against it (final review, 2026-10-21).
            SELECT COALESCE(SUM(t.granted_delta) FILTER (
                       WHERE t.reverses_id IS NOT NULL
                         AND t.reverses_id <= v_seen_row.revoked_ledger_id), 0),
                   COALESCE(-SUM(t.granted_delta) FILTER (
                       WHERE t.reverses_id IS NULL
                          OR t.reverses_id > v_seen_row.revoked_ledger_id), 0)
              INTO v_pre_back, v_window
              FROM public.credit_transactions t
             WHERE t.user_id = p_user_id
               AND t.id > v_seen_row.revoked_ledger_id
               AND (v_stop IS NULL OR t.id < v_stop)
               AND t.reason NOT IN ('grant', 'monthly_reset', 'monthly_expired', 'tier_upgrade',
                                    'tier_revoked', 'plan_period', 'plan_reinstated',
                                    'tester_grant');
            v_total := v_restore + v_pre_back + GREATEST(-v_window, 0);
            v_used  := LEAST(GREATEST(v_window, 0), v_total);
        END IF;
        IF v_remaining > 0 THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, -v_remaining, -v_remaining, 0, 'monthly_expired', NULL, 0);
        END IF;
        UPDATE public.subscription_credit_periods
           SET revoked_at          = NULL,
               revoked_remaining   = NULL,
               revoke_requested_at = NULL,
               revoked_cover       = NULL,
               revoked_ledger_id   = NULL,
               reinstated_at       = now()
         WHERE id = v_seen_row.id;
        v_restore := v_total - v_used;
        UPDATE public.user_credits
           SET total          = v_total,
               used           = v_used,
               tier_alloc     = v_seen_row.allocation,
               resets_at      = v_seen_row.period_end,
               paid_period_id = v_seen_row.id,
               updated_at     = NOW()
         WHERE user_id = p_user_id;
        IF v_restore > 0 THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, v_restore, v_restore, 0, 'plan_reinstated',
                    v_seen_row.original_transaction_id, v_restore);
        END IF;
        RETURN v_restore;
    END IF;

    -- A NEW PAID PERIOD: the tier's full allocation; on an upgrade the leftovers carry on top,
    -- capped at the old allocation times the old period's elapsed share (Apple refunds the rest
    -- of that period). 198: the upgrade records which period it carried from.
    IF v_start_paid THEN
        SELECT monthly_credits INTO v_plan_alloc FROM public.plan_credits WHERE tier = v_sub.tier;
        IF v_plan_alloc IS NULL THEN
            v_plan_alloc := 0;
        END IF;

        IF v_running THEN
            -- floor(allocation x elapsed / span), MULTIPLIED BEFORE DIVIDING: elapsed / span
            -- first is a repeating NUMERIC (11/24 = 0.4583…) that truncates, and FLOOR then
            -- turns an exact 550 into 549. Clamped to [0, allocation].
            v_cap := EXTRACT(EPOCH FROM (now() - v_cur.period_start)) * v_cur.allocation
                     / NULLIF(EXTRACT(EPOCH FROM (v_cur.period_end - v_cur.period_start)), 0);
            v_carry := LEAST(v_remaining,
                             LEAST(GREATEST(FLOOR(COALESCE(v_cap, 0)), 0), v_cur.allocation)::INTEGER);
        END IF;

        INSERT INTO public.subscription_credit_periods
            (user_id, original_transaction_id, period_start, period_end, tier, allocation,
             carried, source, carried_from_period_id)
        VALUES (p_user_id, v_sub.original_transaction_id,
                LEAST(v_sub.current_period_end - INTERVAL '1 month', now()),
                v_sub.current_period_end, v_sub.tier, v_plan_alloc, v_carry,
                CASE WHEN v_running THEN 'upgrade' ELSE 'new_period' END,
                CASE WHEN v_running THEN v_cur.id END)
        ON CONFLICT (original_transaction_id, period_end) DO NOTHING
        RETURNING id INTO v_period_id;

        IF v_period_id IS NULL THEN
            -- Another account's call granted this key between the read above and here. Never
            -- twice: leave this balance alone and say so.
            RAISE WARNING 'ensure_credit_period: paid period %/% was granted concurrently elsewhere; user % not granted',
                v_sub.original_transaction_id, v_sub.current_period_end, p_user_id;
            RETURN v_remaining;
        END IF;

        v_dropped := v_remaining - v_carry;
        IF v_dropped > 0 THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, -v_dropped, -v_dropped, 0, 'monthly_expired', NULL, v_carry);
        END IF;

        UPDATE public.user_credits
           SET total          = v_plan_alloc + v_carry,
               used           = 0,
               tier_alloc     = v_plan_alloc,
               resets_at      = v_sub.current_period_end,
               paid_period_id = v_period_id,
               updated_at     = NOW()
         WHERE user_id = p_user_id;

        INSERT INTO public.credit_transactions
            (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
        VALUES (p_user_id, v_plan_alloc, v_plan_alloc, 0, 'plan_period',
                v_sub.original_transaction_id, v_plan_alloc + v_carry);
        -- 200 (final review): the plan_period row just written, under the lock. A debit after it
        -- was charged in this period whatever its transaction's start time.
        SELECT max(t.id) INTO v_applied
          FROM public.credit_transactions t WHERE t.user_id = p_user_id;
        UPDATE public.subscription_credit_periods
           SET applied_ledger_id = v_applied
         WHERE id = v_period_id;

        RETURN (v_plan_alloc + v_carry);
    END IF;

    -- The paid period in force is still running: nothing to do.
    IF v_running THEN
        RETURN (v_row.total - v_row.used);
    END IF;

    -- It is over. HOLD while it can still be paid for — 198: decided on the in-force period's OWN
    -- lineage row when it exists (another lineage in grace must not hold an expired period's
    -- leftovers, nor a Sandbox / comp-floor row lapse a Production one in grace); otherwise, as in
    -- 196, on the winning row. Else LAPSE: the leftovers expire and the calendar allowance starts.
    IF v_has_cur_sub THEN
        IF v_cur_holds THEN
            RETURN (v_row.total - v_row.used);
        END IF;
    ELSIF v_paid_row AND v_sub.status IN ('active', 'grace_period', 'billing_retry') THEN
        RETURN (v_row.total - v_row.used);
    END IF;

    IF v_remaining > 0 THEN
        INSERT INTO public.credit_transactions
            (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
        VALUES (p_user_id, -v_remaining, -v_remaining, 0, 'monthly_expired', NULL, 0);
    END IF;
    UPDATE public.user_credits
       SET total          = v_alloc,
           used           = 0,
           tier_alloc     = v_alloc,
           resets_at      = v_next_reset,
           paid_period_id = NULL,
           updated_at     = NOW()
     WHERE user_id = p_user_id;
    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
    VALUES (p_user_id, v_alloc, v_alloc, 0, 'monthly_reset',
            to_char(v_now_et, 'YYYY-MM'), v_alloc);
    RETURN v_alloc;
END;
$$;

COMMENT ON FUNCTION public.ensure_credit_period(UUID) IS
    'Brings the GRANTED pool up to date (196, 198, 200). Paid: one allocation per Apple paid '
    'period, from the account''s WINNING subscriptions row (198: one row per lineage) when it is '
    'PRODUCTION, active with a future end and above the complimentary floor — or (200) a later '
    'period that the in-force period''s own lineage row records while it holds, whatever its '
    'status, unless the register holds an unreversed refund of it; each '
    '(original_transaction_id, period_end) is granted at most once via subscription_credit_periods. '
    'A new period waits for the running one to end — and, while the in-force period''s own lineage '
    'still entitles, so does another lineage''s same-or-lower tier — unless it is a higher tier (an '
    'upgrade carries leftovers capped by the old period''s elapsed share, and records where it '
    'carried from); when the winner cannot start, the in-force lineage''s own next key is evaluated '
    'instead, so its renewal is never skipped. A refunded period (its lineage row revoked under its '
    'key, or revoke_requested_at) is clawed back and marked, with what the clawback covered '
    '(revoked_cover) and the ledger mark (revoked_ledger_id, 200); it is reinstated once if Apple '
    'reverses the refund and subscription_transactions holds no refund of it, minus the granted-pool '
    'spend after the mark net of its refunds, plus the refunds after it of charges made before it '
    '(200). A period start records its own plan_period row id (applied_ledger_id, 200), the ledger '
    'order refund_credits uses. HOLD/LAPSE follow the in-force lineage''s own row. Calendar (Free / '
    'complimentary / Sandbox): resets on the ET 1st with 194''s monthly_expired row. Never touches '
    'the purchased pool (App Store 3.1.1). Returns the granted remaining. service_role only.';

-- ── 3. refund_credits — 142 + F10 (roll first) + F15 (clawback cover) ───────────────────────

CREATE OR REPLACE FUNCTION public.refund_credits(
    p_user_id UUID,
    p_amount  INTEGER,
    p_reason  TEXT,
    p_ref_id  TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_spendable      INTEGER;
    v_old_used       INTEGER;
    v_old_pused      INTEGER;
    v_debit_id       BIGINT;
    v_spent_granted  INTEGER;
    v_spent_purch    INTEGER;
    v_have_split     BOOLEAN := FALSE;
    v_searched       BOOLEAN := FALSE;
    v_back_granted   INTEGER := 0;
    v_back_purch     INTEGER := 0;
    v_reversed_id    BIGINT;
    v_outcome        TEXT := 'refunded';
    v_debit_at       TIMESTAMPTZ;
    v_cover_id       BIGINT;
    v_cover          INTEGER;
    v_absorb         INTEGER := 0;
    v_settled        BIGINT;
    v_short          INTEGER := 0;
BEGIN
    IF p_user_id = _GUEST THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN jsonb_build_object(
            'outcome', 'guest', 'refunded', 0, 'spendable', COALESCE(v_spendable, 0));
    END IF;

    IF p_amount IS NULL OR p_amount <= 0 THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN jsonb_build_object(
            'outcome', 'invalid', 'reason', 'non_positive_amount',
            'refunded', 0, 'spendable', v_spendable);
    END IF;

    SELECT used, purchased_used INTO v_old_used, v_old_pused
      FROM public.user_credits
     WHERE user_id = p_user_id
       FOR UPDATE;

    IF NOT FOUND THEN
        -- 139 returned a bare NULL here, which `refund_ledgered` could not tell apart from a
        -- transport fault. It is now its own outcome; NULL is reserved for "the round trip
        -- failed", matching revoke_purchased_credits.
        RETURN jsonb_build_object(
            'outcome', 'no_credits_row', 'refunded', 0, 'spendable', NULL);
    END IF;

    -- 200 (IAP audit F10): roll the period first, as spend_credits does. A refund that lands after
    -- a paid-period or calendar boundary that nothing had rolled yet was paired against the ENDED
    -- period's pool, answered 'refunded', and the next roll expired those credits with it: no
    -- REFUND LEAK alert, and the chip said 'Refunded'. Rolled first, it sees the new pool and takes
    -- the documented capped_to_zero path (owed, alerted). Here, not before the lock: ensure would
    -- INSERT a row for a deleted user (23503) instead of answering no_credits_row. The same lock
    -- is held (ensure's FOR UPDATE on this row is a no-op), so the order stays user_credits first.
    PERFORM public.ensure_credit_period(p_user_id);
    SELECT used, purchased_used INTO v_old_used, v_old_pused
      FROM public.user_credits WHERE user_id = p_user_id;

    IF p_ref_id IS NOT NULL AND p_ref_id <> '' THEN
        v_searched := TRUE;
        SELECT t.id, -t.granted_delta, -t.purchased_delta, t.created_at
          INTO v_debit_id, v_spent_granted, v_spent_purch, v_debit_at
          FROM public.credit_transactions t
         WHERE t.user_id = p_user_id
           AND t.ref_id  = p_ref_id
           AND t.delta   = -p_amount
           -- A clawback is not a spend. Both carry a negative delta AND a ref_id, so without
           -- this they are pairable debits: a refund could "reverse" a subscription or pack
           -- revocation and hand back credits that were deliberately taken away.
           AND t.reason NOT IN ('pack_revoked', 'tier_revoked')
           AND NOT EXISTS (
                 SELECT 1 FROM public.credit_transactions r
                  WHERE r.reverses_id = t.id
               )
         ORDER BY t.id DESC
         LIMIT 1;

        IF FOUND AND (COALESCE(v_spent_granted, 0) <> 0 OR COALESCE(v_spent_purch, 0) <> 0) THEN
            v_have_split := TRUE;
        END IF;
    END IF;

    -- 200 (IAP audit F15): a charge that an Apple-refunded allocation already paid for. Its period
    -- was clawed back AFTER this debit (revoked_ledger_id >= the debit's id) while the debit was
    -- charged in it (after the period's own plan_period row, applied_ledger_id — ledger order, so
    -- no race between transaction start times; periods started before 200 fall back to
    -- applied_at), and the clawback wrote that spend off (revoked_cover).
    -- Paid back now, it came out of the calendar floor — a second payback, or, once the keyed
    -- clawback had started the next period, a false 'owed' alert. The covered part moves to the
    -- period's revoked_remaining instead (a reversal gives it back); the rest is refunded as before.
    -- Lock order: user_credits (above), then the period row, as in ensure_credit_period.
    IF v_have_split AND COALESCE(v_spent_granted, 0) > 0 THEN
        SELECT p.id, p.revoked_cover INTO v_cover_id, v_cover
          FROM public.subscription_credit_periods p
         WHERE p.user_id = p_user_id
           AND p.revoked_at IS NOT NULL
           AND p.revoked_cover > 0
           AND p.revoked_ledger_id >= v_debit_id
           AND (CASE WHEN p.applied_ledger_id IS NOT NULL THEN p.applied_ledger_id < v_debit_id
                     ELSE p.applied_at <= v_debit_at END)
         ORDER BY p.revoked_ledger_id, p.id
         LIMIT 1
           FOR UPDATE;
        IF FOUND THEN
            v_absorb := LEAST(v_spent_granted, v_cover);
            UPDATE public.subscription_credit_periods
               SET revoked_cover     = revoked_cover - v_absorb,
                   revoked_remaining = COALESCE(revoked_remaining, 0) + v_absorb
             WHERE id = v_cover_id;
            v_spent_granted := v_spent_granted - v_absorb;
        END IF;
    END IF;

    IF v_have_split THEN
        -- Exact reversal, each side capped BOTH by what the paired spend took from that pool
        -- and by what the pool currently shows as spent.
        v_back_purch   := LEAST(GREATEST(v_spent_purch, 0), p_amount, v_old_pused);
        v_back_granted := LEAST(GREATEST(v_spent_granted, 0), p_amount - v_back_purch,
                                v_old_used);
    ELSIF v_debit_id IS NOT NULL OR NOT v_searched THEN
        -- Either a debit was found whose split is unknown (a pre-117 row), or there was no
        -- ref_id to search by at all. Both are genuinely "unknown split": granted-first,
        -- overflowing to purchased only if granted cannot absorb it. The non-farmable
        -- direction, and the one that cannot silently swallow a legitimate refund.
        v_back_granted := LEAST(p_amount, v_old_used);
        v_back_purch   := LEAST(p_amount - v_back_granted, v_old_pused);
    ELSE
        -- 139's fix: we DID search on a ref_id and found no UN-REVERSED debit, so paying from
        -- the user's current `used` would MINT credits. Refund nothing.
        --
        -- 142 adds the half 139 could not express: WHY there was no match. Re-run the same
        -- lookup without the reverses_id anti-join. If a debit turns up, this is a replayed
        -- refund of a charge already given back — benign, and the caller must not page anyone.
        -- If nothing turns up, the ref_id or the amount does not match any charge and the user
        -- is owed credits nobody will notice they are owed.
        SELECT t.id INTO v_reversed_id
          FROM public.credit_transactions t
         WHERE t.user_id = p_user_id
           AND t.ref_id  = p_ref_id
           AND t.delta   = -p_amount
           AND t.reason NOT IN ('pack_revoked', 'tier_revoked')
         ORDER BY t.id DESC
         LIMIT 1;

        IF v_reversed_id IS NOT NULL THEN
            v_outcome := 'already_refunded';
            -- 200: what the earlier refund settled — the credits it moved plus what a clawback
            -- covered (credit_transactions.covered). Less than the charge (a partial refund, or a
            -- partly covered one whose rest was capped) and the replay says so (`short`), so a
            -- retried lost reply never reads as settled while the rest is still owed.
            SELECT COALESCE(SUM(r.delta), 0) + COALESCE(SUM(r.covered), 0) INTO v_settled
              FROM public.credit_transactions r
             WHERE r.reverses_id = v_reversed_id;
            v_short := GREATEST(p_amount - v_settled, 0);
        ELSE
            v_outcome := 'no_matching_debit';
            -- Kept from 139. Redundant now that the outcome reaches the application, but free,
            -- and the only signal visible when reading Postgres logs directly.
            RAISE WARNING 'refund_credits: no un-reversed debit for user=% ref_id=% amount=% '
                          '(the refund ref_id or amount does not match its charge) '
                          '— refunding nothing',
                          p_user_id, p_ref_id, p_amount;
        END IF;
    END IF;

    IF v_back_granted + v_back_purch = 0 THEN
        -- 200 (IAP audit F15): the clawback covered the charge, so nothing moves. Wholly covered,
        -- nothing is owed (covered_by_clawback). Partly covered with the rest capped to zero, the
        -- rest is owed (capped_to_zero; `covered` is the absorbed part, so p_amount - covered is
        -- owed). Either way a 0/0/0 row
        -- pairs the debit (reverses_id), so a replay answers already_refunded and can never absorb
        -- the cover twice.
        IF v_absorb > 0 THEN
            IF v_spent_granted + GREATEST(COALESCE(v_spent_purch, 0), 0) > 0 THEN
                v_outcome := 'capped_to_zero';
                RAISE WARNING 'refund_credits: matched debit % for user=% ref_id=%: a clawback '
                              'covered % of the % credits and the pools could absorb none of '
                              'the rest (used=%, purchased_used=%) — refunding nothing more',
                              v_debit_id, p_user_id, p_ref_id, v_absorb, p_amount, v_old_used,
                              v_old_pused;
            ELSE
                v_outcome := 'covered_by_clawback';
            END IF;
            SELECT spendable INTO v_spendable
              FROM public.user_credits WHERE user_id = p_user_id;
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after,
                 reverses_id, covered)
            VALUES
                (p_user_id, 0, 0, 0, p_reason, p_ref_id, v_spendable, v_debit_id, v_absorb);
            RETURN jsonb_build_object(
                'outcome', v_outcome, 'refunded', 0, 'covered', v_absorb,
                'spendable', v_spendable);
        END IF;

        -- A MATCHED debit whose caps collapsed to zero is NOT a success. The reachable path is
        -- the month boundary: `ensure_credit_period` resets `used` to 0, so a report charged in
        -- month M and refunded in M+1 (the reconciliation sweep crosses that boundary on its
        -- own schedule) reads v_old_used = 0 and both LEAST() caps go to zero. The user was
        -- charged, is owed, and `research_reports.is_refunded` is already burned.
        --
        -- Labelling that 'refunded' would hand the caller a success for the exact silent-money
        -- case this migration exists to surface. `v_debit_id IS NOT NULL` is already known here,
        -- so it costs nothing to say so.
        --
        -- Deliberately NOT applied when `NOT v_searched` (no ref_id to search by): there is no
        -- debit to point at, so "owed" is unprovable and claiming it would be a false alarm.
        IF v_outcome = 'refunded' AND v_debit_id IS NOT NULL THEN
            v_outcome := 'capped_to_zero';
            RAISE WARNING 'refund_credits: matched debit % for user=% ref_id=% but the pools '
                          'could absorb none of the % credits (used=%, purchased_used=%) '
                          '— refunding nothing',
                          v_debit_id, p_user_id, p_ref_id, p_amount, v_old_used, v_old_pused;
        END IF;

        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        -- `refunded` with 0 survives only for the genuinely-nothing-to-give-back case, exactly
        -- as revoke_purchased_credits reports `reclaimed: 0`.
        RETURN jsonb_build_object(
            'outcome', v_outcome, 'refunded', 0, 'spendable', v_spendable)
            || CASE WHEN v_short > 0 THEN jsonb_build_object('short', v_short)
                    ELSE '{}'::jsonb END;
    END IF;

    UPDATE public.user_credits
       SET used           = used - v_back_granted,
           purchased_used = purchased_used - v_back_purch,
           updated_at     = NOW()
     WHERE user_id = p_user_id
    RETURNING spendable INTO v_spendable;

    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after,
         reverses_id, covered)
    VALUES
        (p_user_id, v_back_granted + v_back_purch, v_back_granted, v_back_purch,
         p_reason, p_ref_id, v_spendable, v_debit_id, v_absorb);

    -- 'covered' only when a clawback absorbed part of it (200), so every other reply keeps 142's
    -- shape.
    RETURN jsonb_build_object(
        'outcome', 'refunded',
        'refunded', v_back_granted + v_back_purch,
        'spendable', v_spendable)
        || CASE WHEN v_absorb > 0 THEN jsonb_build_object('covered', v_absorb)
                ELSE '{}'::jsonb END;
END;
$$;

COMMENT ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) IS
    'Reverses the RECORDED split of a spend. Returns JSONB {outcome, refunded, spendable} as '
    'of migration 142 (was INTEGER spendable), plus `covered` when an Apple-refund clawback '
    'already paid for part of the charge (200). outcome: refunded | already_refunded | '
    'no_matching_debit | capped_to_zero | covered_by_clawback | guest | invalid | no_credits_row. '
    'TWO of those mean the user is OWED credits and credit_service.refund_ledgered logs them as a '
    'REFUND LEAK at ERROR: `no_matching_debit` (no debit matched this ref_id/amount) and '
    '`capped_to_zero` (the debit matched but the pools absorbed none of it — a charge and refund '
    'that straddle a period boundary). Since 200 it runs ensure_credit_period first, as '
    'spend_credits does, so that boundary is seen whether or not anything rolled the period in '
    'between. `covered_by_clawback` (200): the charge was made in a subscription period Apple '
    'refunded, and the clawback already wrote that spend off, so nothing is owed; the covered '
    'credits go to the period''s revoked_remaining and a 0/0/0 row pairs the debit. '
    '`already_refunded` is an idempotent replay and must NOT page. Excludes pack_revoked / '
    'tier_revoked rows from the debit match (139) so a report refund can never reverse an Apple '
    'clawback.';

COMMENT ON TABLE public.subscription_credit_periods IS
    'Every Apple paid subscription period whose credit allocation was granted, keyed '
    '(original_transaction_id, period_end) UNIQUE so a period is granted at most once per account '
    '(the rows go with the account). Written only by ensure_credit_period, revoke_tier_credits '
    '(revoked_at, revoked_remaining), revoke_paid_period (revoke_requested_at, carry_revoked, '
    'carry_revoked_at, revoked_remaining), reinstate_paid_carry (carry_reinstated_at, '
    'carry_revoked_at, revoked_remaining), refund_credits (revoked_cover, revoked_remaining: 200) '
    'and 196''s '
    'backfill; user_credits.paid_period_id points at the one in force. A key granted before and not '
    'in force is never re-applied, so an older JWS re-submitted later cannot re-grant; a revoked one '
    'is reinstated once per clawback when Apple reverses the refund — never while '
    'subscription_transactions holds an unreversed refund of it — with revoked_remaining adjusted '
    'as ensure_credit_period describes (200); a later refund claws it back again and re-stamps '
    'revoked_at, keeping reinstated_at. revoked_cover / revoked_ledger_id: what '
    'the clawback covered and where the ledger stood (200); applied_ledger_id: where it stood at '
    'the period''s start (200). carried_from_period_id: the period an '
    'upgrade carried leftovers from. period_start is current_period_end - 1 month (the '
    'subscriptions row stores no start). carried = unused credits kept on an upgrade, capped by the '
    'old period''s elapsed share. SERVICE-ROLE ONLY. Migrations 196, 198, 200.';

COMMENT ON COLUMN public.research_reports.refund_outcome IS
    'What the refund behind is_refunded actually did. is_refunded is the one-shot claim, set '
    'BEFORE refund_credits runs; this is written AFTER, best-effort, by '
    'research_reconciliation_service.record_refund_outcome — never inside the is_refunded '
    'compare-and-set, and an unconfirmed label never overwrites a confirmed one. '
    'refunded / already_refunded / covered_by_clawback (200: an Apple-refund clawback already paid '
    'for the charge) / guest / invalid / legacy (charged under the bare-ticker ref '
    'before per-report refs, so it cannot be re-checked) / '
    'manual (corrected by hand) = the credits are back. rpc_failed = the round trip failed on '
    'the unique per-report ref; the reconciliation loop retries it ONCE on that ref -> a '
    'settled outcome or retry_rpc_failed. legacy_rpc_failed = failed on the legacy bare-ticker '
    'ref, never retried. Anything else (no_matching_debit, capped_to_zero, partial, '
    'no_credits_row, unknown, retry_rpc_failed) = still owed: a REFUND LEAK was paged; fix by '
    'hand, then set manual. NULL on a claimed row = the stamp never landed; served as not '
    'confirmed and re-checked on its per-report ref by the retry pass after a 30-minute grace. '
    'The Reports list serves this as '
    'refund_confirmed. See migrations 195, 200.';

-- ── ACL ───────────────────────────────────────────────────────────────────────────────────
-- CREATE OR REPLACE keeps the owner and the grants; restated anyway (belt and braces), naming
-- anon / authenticated too (Supabase's default privileges grant them explicitly).

REVOKE ALL ON FUNCTION public.ensure_credit_period(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ensure_credit_period(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;

-- VERIFY (run after applying):
--   1. One row: t, t, t, t, t, 3, t, f, postgres.
--   SELECT pg_get_functiondef('public.refund_credits(uuid,integer,text,text)'::regprocedure) ~ 'PERFORM\s+public\.ensure_credit_period\(p_user_id\)' AS f10,
--          pg_get_functiondef('public.refund_credits(uuid,integer,text,text)'::regprocedure) LIKE '%covered_by_clawback%' AS f15_refund,
--          pg_get_function_result('public.refund_credits(uuid,integer,text,text)'::regprocedure) = 'jsonb' AS returns_jsonb,
--          pg_get_functiondef('public.ensure_credit_period(uuid)'::regprocedure) ~ 'v_cur_sub\.current_period_end\s*>\s*v_cur\.period_end' AS f11,
--          pg_get_functiondef('public.ensure_credit_period(uuid)'::regprocedure) ~ 'revoked_ledger_id\s*=\s*v_mark' AS f15_stamp,
--          (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'
--             AND table_name = 'subscription_credit_periods'
--             AND column_name IN ('revoked_cover', 'revoked_ledger_id', 'applied_ledger_id')) AS new_cols,
--          EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
--                    AND table_name = 'credit_transactions' AND column_name = 'covered') AS ledger_covered,
--          (has_function_privilege('anon', 'public.refund_credits(uuid,integer,text,text)', 'EXECUTE')
--           OR has_function_privilege('authenticated', 'public.refund_credits(uuid,integer,text,text)', 'EXECUTE')
--           OR has_function_privilege('anon', 'public.ensure_credit_period(uuid)', 'EXECUTE')
--           OR has_function_privilege('authenticated', 'public.ensure_credit_period(uuid)', 'EXECUTE')) AS client_exec,
--          (SELECT proowner::regrole::text FROM pg_proc
--            WHERE oid = 'public.refund_credits(uuid,integer,text,text)'::regprocedure) AS owner;
--
--   2. A cover or a mark only on a clawed-back period. Expect 0, now and on any later day.
--   SELECT count(*) FROM public.subscription_credit_periods
--    WHERE revoked_at IS NULL AND (revoked_cover IS NOT NULL OR revoked_ledger_id IS NOT NULL);
--
--   3. Accounts F11 affects (read-only; can also run BEFORE applying). Expect 0 rows, or a few
--      while billing grace is OFF; each heals on that account's next credit read.
--   SELECT uc.user_id, p.original_transaction_id, p.period_end AS in_force_end, s.status, s.tier,
--          s.current_period_end AS row_end
--     FROM public.user_credits uc
--     JOIN public.subscription_credit_periods p ON p.id = uc.paid_period_id
--     JOIN public.subscriptions s ON s.user_id = uc.user_id
--                                AND s.original_transaction_id = p.original_transaction_id
--    WHERE p.period_end <= now()
--      AND s.status IN ('active', 'grace_period', 'billing_retry')
--      AND COALESCE(s.environment, 'Production') = 'Production'
--      AND s.current_period_end > p.period_end
--      AND NOT (s.status = 'active' AND s.current_period_end > now())  -- 198 already starts those
--      AND NOT EXISTS (SELECT 1 FROM public.subscription_credit_periods q
--                       WHERE q.original_transaction_id = s.original_transaction_id
--                         AND q.period_end = s.current_period_end);
--
--   4. The next day: refund outcomes of reports created since the apply (created_at is the
--      report's, not the refund's — research_reports keeps no refund time). Expect mostly
--      refunded / already_refunded; capped_to_zero only for a charge that straddled a period
--      boundary; covered_by_clawback only for a charge made before an Apple refund of the
--      subscription period it was charged in (a clawback wrote it off; no tier_revoked row is
--      written when nothing was left to take back).
--   SELECT refund_outcome, count(*) FROM public.research_reports
--    WHERE is_refunded AND created_at > '<apply time>' GROUP BY 1;
--
--   5. Refunds a clawback covered (wholly: delta 0; partly: delta > 0), newest first. covered +
--      delta < the charge's own -delta means part of it is still owed (a PARTIAL REFUND page):
--   SELECT r.id, r.user_id, r.reverses_id, r.reason, r.ref_id, r.delta, r.covered,
--          -d.delta AS charge, r.created_at
--     FROM public.credit_transactions r JOIN public.credit_transactions d ON d.id = r.reverses_id
--    WHERE r.covered > 0
--    ORDER BY r.id DESC LIMIT 20;
