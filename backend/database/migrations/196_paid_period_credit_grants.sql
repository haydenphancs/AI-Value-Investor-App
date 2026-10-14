-- 196_paid_period_credit_grants.sql
--
-- Why: a paid plan's credits followed the ET CALENDAR month, not the period the customer paid
-- for, so every new subscription got one allocation more than it paid for. Found by the
-- 2026-10-12 credits audit; owner decision 2026-10-12: "(b) Per paid period".
--
--   Max ($39.99, 4,000/month) bought on Oct 25 and cancelled at once:
--     Oct 25  grant_tier_upgrade tops the balance up to the full 4,000      (+3,950)
--     Nov 1   ensure_credit_period resets on the calendar month, and
--             users.tier is still premium until the paid period ends Nov 25 (+4,000)
--     Nov 25  the EXPIRED notification drops the tier, and nothing lowers the balance,
--             which lasts until Dec 1
--   = 7,950 credits for one payment (Pro: 2,350 for $14.99). Any start day but the 1st
--   double-grants; a late-month start just pays the second grant out within a week. At that
--   rate a Max credit cost $0.0050 against $0.0192 for the best pack (§9b.7's "no pack
--   undercuts a plan" held only on paper).
--
-- The rule now (SYSTEM_DESIGN_GUIDELINES §9b.12):
--
--   * ONE APPLE PAID PERIOD = ONE ALLOCATION. A period is the account's `subscriptions` row
--     while it is `active` with `current_period_end` in the future, keyed by
--     (original_transaction_id, current_period_end): a renewal or an upgrade is a new key.
--     The row is the only input: iap_service already writes it from Apple-signed data,
--     ordered by Apple's signedDate, with the stale-JWS and cross-account guards in front.
--   * ONLY A PRODUCTION ROW, AND ONLY ABOVE THE COMPLIMENTARY FLOOR. A Sandbox / TestFlight
--     row (new column `subscriptions.environment`) never starts a paid period: sandbox renews
--     every few minutes, so it keeps the calendar sizing it had before 196. A row whose tier
--     is not above users.comp_tier never starts one either (the App Review demo account is
--     comp Max; a reviewer's sandbox Pro must not shrink it).
--   * EXACTLY ONCE PER ACCOUNT. A key is granted at most once (subscription_credit_periods
--     UNIQUE key below); a key granted before and not in force is IGNORED, so re-submitting an
--     older JWS (the 2026-09-25 replay class) can never re-grant a period. The keys go with the
--     account on deletion (ON DELETE CASCADE, like credit_purchases); a re-claim by another
--     account is refused upstream by the appAccountToken check.
--   * WHEN. A new key starts when no paid period is running (first purchase, re-subscribe, a
--     renewal once the old period has ended — an early DID_RENEW waits), or at once when it is
--     a HIGHER tier than the running one (an upgrade). A same-or-lower tier never cuts a
--     running period short.
--   * HOW MUCH. A new period sets the granted pool to the tier's full allocation (used = 0).
--     Leftovers expire at the period boundary, EXCEPT on an upgrade: the unused credits carry
--     on top, capped at the old allocation times the share of the old period already elapsed
--     ("Cap by Pro time used", owner 2026-10-12) — Apple refunds the unused part of the old
--     period, so an instant upgrade carries ~0 and 400 left after 20 of 31 days carries 400.
--     A subscription that starts from the calendar pool (Free / comp) starts fresh.
--   * LAPSE. When the paid period has ended and the subscription row no longer entitles, the
--     leftovers expire ("Expire at plan end", owner 2026-10-12) and the calendar allowance
--     starts at once, resetting on each ET 1st as before.
--   * HOLD. A period that ended while the row still entitles (grace_period / billing_retry,
--     or `active` past its end until the expiry sweep runs) keeps its balance and grants
--     nothing until Apple's renewal or the expiry lands.
--   * REFUND. A `revoked` row whose transaction IS the period in force (same original
--     transaction AND same period end) claws back as revoke_tier_credits does, floored at the
--     calendar allowance, marks the period revoked and returns to calendar mode. A refund of
--     an OLDER renewal (or of a later one not yet started) leaves the period in force alone.
--   * REFUND_REVERSED. When the row entitles again under a key whose period was revoked, the
--     period is reinstated once with exactly what the refund took (`plan_reinstated`).
--   * CALENDAR MODE (Free and complimentary accounts, and Sandbox rows) is unchanged,
--     including 194's `monthly_expired` row. users.tier sizes it unless a PRODUCTION
--     subscriptions row exists; then only users.comp_tier does. users.tier never sizes a
--     PRODUCTION paid allocation.
--   * The PURCHASED pool is never named (App Store 3.1.1), exactly as before.
--
-- Ledger: a new period writes `plan_period` (+allocation, ref_id = the original transaction
-- id), preceded by a `monthly_expired` row (194's shape: ref_id NULL, so no refund can pair it)
-- for whatever did not carry. A lapse writes `monthly_expired` + `monthly_reset`; a refund
-- writes `tier_revoked` (excluded from refund pairing since 139); a reversal writes
-- `monthly_expired` + `plan_reinstated`. Every row carries the split.
--
-- Backfill (first apply only): every account whose subscriptions row entitles today, whose
-- current period the old code demonstrably paid out (tier_alloc at the plan's allocation) and
-- whose plan is above its complimentary floor gets that period recorded as already granted
-- (source 'backfill'), its balance untouched and resets_at moved to the period end. So applying
-- this neither re-grants a period the calendar reset already paid for nor drops a subscriber to
-- the free allowance at the next 1st; a period the old code never paid (a failed grant) is left
-- for ensure_credit_period to grant. The one-time effect is that a backfilled subscriber's
-- balance lasts to their renewal date instead of the 1st ("changes will not affect a billing
-- period already paid for", Terms §5). A re-run skips the backfill once any period has been
-- granted the new way, because by then an account in calendar mode with an active
-- subscription may just not be reconciled yet, and backfilling it would mark it paid.
--
-- grant_tier_upgrade(uuid) keeps its signature for its callers (iap_service.reconcile_user_tier,
-- scripts/set_comp_tier.py, migration 177's instructions) and now just runs
-- ensure_credit_period, whose calendar branch keeps the old mid-month top-up for a raised
-- complimentary (or Sandbox) tier. revoke_tier_credits keeps 139's arithmetic, refuses to claw
-- back a paid period the refund was not for, and leaves paid mode.
--
-- Depends on 194 (this ensure_credit_period carries 194's `monthly_expired` row verbatim;
-- 194 now refuses to run once this table exists, so it cannot replace this function by being
-- applied late) and on 177 (users.comp_tier). Apply 194 FIRST; 195 touches nothing here.
--
-- Deploy order: the backend works with or without this applied (it calls the same three RPC
-- names, and writes subscriptions.environment with a missing-column retry). Apply this, THEN
-- deploy the backend carrying the environment write, the new Terms / Support / plan copy and
-- the `plan_period` / `plan_reinstated` history labels, THEN ship an app build with the new
-- copy. Rows written before that deploy have environment NULL and count as Production; a
-- tester's Sandbox renewal in that window still grants (minutes, once).
--
-- Idempotent: CREATE TABLE / INDEX / ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE, DROP POLICY IF
-- EXISTS, and a backfill guarded to the first apply.

BEGIN;
SET LOCAL lock_timeout = '5s';

-- ── 1. subscription_credit_periods — every paid period ever granted ───────────────────────

CREATE TABLE IF NOT EXISTS public.subscription_credit_periods (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    original_transaction_id TEXT NOT NULL,
    period_start            TIMESTAMPTZ NOT NULL,
    period_end              TIMESTAMPTZ NOT NULL,
    tier                    public.user_tier NOT NULL,
    allocation              INTEGER NOT NULL,
    carried                 INTEGER NOT NULL DEFAULT 0,
    source                  TEXT NOT NULL,
    applied_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at              TIMESTAMPTZ,
    revoked_remaining       INTEGER,
    reinstated_at           TIMESTAMPTZ,
    CONSTRAINT subscription_credit_periods_key UNIQUE (original_transaction_id, period_end),
    CONSTRAINT subscription_credit_periods_allocation_nonneg CHECK (allocation >= 0),
    CONSTRAINT subscription_credit_periods_carried_nonneg CHECK (carried >= 0),
    CONSTRAINT subscription_credit_periods_revoked_remaining_nonneg
        CHECK (revoked_remaining IS NULL OR revoked_remaining >= 0),
    CONSTRAINT subscription_credit_periods_span CHECK (period_end > period_start),
    CONSTRAINT subscription_credit_periods_source CHECK (source IN ('new_period', 'upgrade', 'backfill'))
);

CREATE INDEX IF NOT EXISTS idx_subscription_credit_periods_user
    ON public.subscription_credit_periods (user_id, period_end DESC);

ALTER TABLE public.subscription_credit_periods ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "subscription_credit_periods_service_all" ON public.subscription_credit_periods;
CREATE POLICY "subscription_credit_periods_service_all" ON public.subscription_credit_periods
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.subscription_credit_periods FROM anon, authenticated;
GRANT  ALL ON public.subscription_credit_periods TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.subscription_credit_periods_id_seq TO service_role;

COMMENT ON TABLE public.subscription_credit_periods IS
    'Every Apple paid subscription period whose credit allocation was granted, keyed '
    '(original_transaction_id, period_end) UNIQUE so a period is granted at most once per account '
    '(the rows go with the account). Written only by ensure_credit_period, revoke_tier_credits '
    '(revoked_at) and 196''s backfill; user_credits.paid_period_id points at the one in force. A '
    'key granted before and not in force is never re-applied, so an older JWS re-submitted later '
    'cannot re-grant; a revoked one is reinstated once, with revoked_remaining, when Apple '
    'reverses the refund. period_start is current_period_end - 1 month (the subscriptions row '
    'stores no start). carried = unused credits kept on an upgrade, capped by the old period''s '
    'elapsed share. SERVICE-ROLE ONLY. Migration 196.';

-- ── 2. user_credits.paid_period_id — which paid period the granted pool belongs to ────────

ALTER TABLE public.user_credits
    ADD COLUMN IF NOT EXISTS paid_period_id BIGINT
        REFERENCES public.subscription_credit_periods(id) ON DELETE SET NULL;

-- The FK's ON DELETE SET NULL looks rows up by this column on every account deletion.
CREATE INDEX IF NOT EXISTS idx_user_credits_paid_period
    ON public.user_credits (paid_period_id) WHERE paid_period_id IS NOT NULL;

COMMENT ON COLUMN public.user_credits.paid_period_id IS
    'The subscription_credit_periods row the GRANTED pool belongs to. NULL = calendar mode: Free, '
    'complimentary or Sandbox allowance, reset on the ET 1st. Set only by ensure_credit_period (a '
    'new or reinstated paid period) and 196''s backfill; cleared on a lapse or a refund. resets_at '
    'is the paid period''s end while it is set. Migration 196.';

COMMENT ON COLUMN public.user_credits.tier_alloc IS
    'Allocation granted for the CURRENT period (paid: the plan allocation, excluding any carried '
    'credits; calendar: the Free, complimentary or Sandbox allocation). Written by '
    'ensure_credit_period and revoke_tier_credits. Read by ensure_credit_period''s calendar branch '
    'to top up a raised tier mid-month, and by 196''s backfill. See migrations 139 and 196.';

COMMENT ON COLUMN public.users.tier IS
    'Effective tier the app gates FEATURES on. Written by iap_service.reconcile_user_tier as '
    'max(winning subscriptions tier, users.comp_tier); also set by scripts/seed_testflight_testers.py '
    'and scripts/set_comp_tier.py for complimentary accounts. Since migration 196 it never sizes a '
    'PRODUCTION paid credit allocation (that comes from the subscription''s paid period); '
    'ensure_credit_period reads it only for the calendar allowance of an account with no '
    'Production subscriptions row (hand-set testers, Sandbox / TestFlight subscribers).';

-- ── 3. subscriptions.environment — Production rows only start paid periods ─────────────────

ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS environment TEXT;

COMMENT ON COLUMN public.subscriptions.environment IS
    'Apple environment of the transaction last applied to the row (Production, Sandbox, Xcode, '
    'LocalTesting), written by iap_service.apply_transaction. Only a Production row - or NULL, a '
    'row written before 196 or by older code - starts a paid credit period: Sandbox / TestFlight '
    'renew every few minutes, so their rows keep the calendar allowance sized by users.tier, as '
    'every row was before 196. Migration 196.';

-- ── 4. Backfill: today's paid periods count as already granted (first apply only) ─────────

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.subscription_credit_periods WHERE source <> 'backfill') THEN
        RAISE NOTICE '196: backfill skipped - paid periods are already being granted by ensure_credit_period';
        RETURN;
    END IF;

    INSERT INTO public.subscription_credit_periods
        (user_id, original_transaction_id, period_start, period_end, tier, allocation, carried, source)
    SELECT s.user_id,
           s.original_transaction_id,
           s.current_period_end - INTERVAL '1 month',
           s.current_period_end,
           s.tier,
           COALESCE(pc.monthly_credits, 0),
           0,
           'backfill'
      FROM public.subscriptions s
      JOIN public.user_credits uc ON uc.user_id = s.user_id
      JOIN public.users u ON u.id = s.user_id
      LEFT JOIN public.plan_credits pc ON pc.tier = s.tier
     WHERE s.status IN ('active', 'grace_period', 'billing_retry')
       AND s.tier <> 'free'
       AND s.original_transaction_id IS NOT NULL
       AND s.original_transaction_id <> ''
       AND s.current_period_end IS NOT NULL
       AND uc.paid_period_id IS NULL
       -- Only a period the calendar code demonstrably paid out: a grant that failed left
       -- tier_alloc below the plan, and that period is left for ensure_credit_period to grant.
       AND uc.tier_alloc >= COALESCE(pc.monthly_credits, 0)
       -- A complimentary tier at or above the plan keeps the calendar allowance.
       AND (CASE s.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
         > (CASE COALESCE(u.comp_tier, 'free') WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
    ON CONFLICT (original_transaction_id, period_end) DO NOTHING;

    UPDATE public.user_credits uc
       SET paid_period_id = p.id,
           resets_at      = p.period_end,
           updated_at     = now()
      FROM public.subscriptions s
      JOIN public.subscription_credit_periods p
        ON p.original_transaction_id = s.original_transaction_id
       AND p.period_end             = s.current_period_end
       AND p.user_id                = s.user_id
       AND p.source                 = 'backfill'
     WHERE uc.user_id = s.user_id
       AND uc.paid_period_id IS NULL;
END $$;

-- ── 5. ensure_credit_period — 194's calendar block, then the paid period ──────────────────
--
-- Layout, on purpose: the first-touch branch, then a pure look at the subscription row, then
-- 194's calendar reset VERBATIM (tests/test_monthly_expired_ledger_row.py pins it on the latest
-- definition), then the paid branches. v_alloc is the CALENDAR allowance throughout.

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
    v_paid_row   BOOLEAN := FALSE;
    v_cur        public.subscription_credit_periods%ROWTYPE;
    v_has_cur    BOOLEAN := FALSE;
    v_running    BOOLEAN := FALSE;
    v_new_key    BOOLEAN := FALSE;
    v_seen       BOOLEAN := FALSE;
    v_seen_row   public.subscription_credit_periods%ROWTYPE;
    v_reinstate  BOOLEAN := FALSE;
    v_start_paid BOOLEAN := FALSE;
    v_plan_alloc INTEGER;
    v_carry      INTEGER := 0;
    v_cap        NUMERIC;
    v_dropped    INTEGER;
    v_restore    INTEGER;
    v_period_id  BIGINT;
    v_floor      INTEGER;
    v_writeoff   INTEGER;
    v_delta      INTEGER;
BEGIN
    IF p_user_id = _GUEST THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, 0);
    END IF;

    -- ONE row per account (subscriptions_user_id_key UNIQUE (user_id)). NULL environment = a
    -- row written before 196 or by older code: treated as Production.
    SELECT * INTO v_sub FROM public.subscriptions WHERE user_id = p_user_id;
    v_has_sub  := FOUND;
    v_prod_row := v_has_sub AND COALESCE(v_sub.environment, 'Production') = 'Production';

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
    v_paid_row := v_prod_row
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

    -- Which paid period the pool is in, and whether the subscription row brings a new one.
    -- Reads only; nothing is written before the branches below.
    IF v_row.paid_period_id IS NOT NULL THEN
        SELECT * INTO v_cur FROM public.subscription_credit_periods WHERE id = v_row.paid_period_id;
        v_has_cur := FOUND;
        v_running := v_has_cur AND v_cur.period_end > now();
    END IF;

    v_new_key := COALESCE(
        v_paid_row
        AND v_sub.status = 'active'
        AND v_sub.current_period_end > now()
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
    -- the refund (the row entitles again under the same key), which reinstates it once.
    v_reinstate := COALESCE(
        v_new_key AND v_seen AND v_seen_row.user_id = p_user_id
        AND v_seen_row.revoked_at IS NOT NULL AND NOT v_running,
        FALSE);

    -- A new period starts when nothing paid is running, or at once when it is a higher tier
    -- than the running period (an upgrade).
    v_start_paid := COALESCE(
        v_new_key AND NOT v_seen
        AND (NOT v_running
             OR (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
              > (CASE v_cur.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)),
        FALSE);

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

    -- REFUND of the period in force: the revoked transaction IS that period (same original
    -- transaction AND same period end — a refund of an older renewal, or of a later one not yet
    -- started, leaves it alone). Claw back exactly as revoke_tier_credits (139) does, floored at
    -- the calendar allowance; mark the period revoked so a reversal can reinstate it.
    IF v_has_cur AND v_has_sub AND v_sub.status = 'revoked'
       AND v_sub.original_transaction_id = v_cur.original_transaction_id
       AND v_sub.current_period_end = v_cur.period_end THEN
        v_floor    := GREATEST(v_alloc, v_row.used);
        v_writeoff := GREATEST(v_row.used - v_alloc, 0);
        v_delta    := v_floor - v_row.total;
        UPDATE public.subscription_credit_periods
           SET revoked_at        = now(),
               revoked_remaining = v_remaining
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

    -- REFUND_REVERSED: reinstate the revoked period once, with exactly what the refund took.
    -- The calendar leftovers since the refund expire, as on any start of a paid period.
    IF v_reinstate THEN
        v_restore := COALESCE(v_seen_row.revoked_remaining, 0);
        IF v_remaining > 0 THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, -v_remaining, -v_remaining, 0, 'monthly_expired', NULL, 0);
        END IF;
        UPDATE public.subscription_credit_periods
           SET revoked_at        = NULL,
               revoked_remaining = NULL,
               reinstated_at     = now()
         WHERE id = v_seen_row.id;
        UPDATE public.user_credits
           SET total          = v_restore,
               used           = 0,
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
    -- capped at the old allocation times the old period's elapsed share (Apple refunds the
    -- rest of that period).
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
             carried, source)
        VALUES (p_user_id, v_sub.original_transaction_id,
                LEAST(v_sub.current_period_end - INTERVAL '1 month', now()),
                v_sub.current_period_end, v_sub.tier, v_plan_alloc, v_carry,
                CASE WHEN v_running THEN 'upgrade' ELSE 'new_period' END)
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

        RETURN (v_plan_alloc + v_carry);
    END IF;

    -- The paid period in force is still running: nothing to do.
    IF v_running THEN
        RETURN (v_row.total - v_row.used);
    END IF;

    -- It is over. HOLD while the row can still carry a paid period and entitles (Apple
    -- retrying billing, or an expiry not yet recorded); otherwise LAPSE: the leftovers expire
    -- and the calendar allowance starts now.
    IF v_paid_row AND v_sub.status IN ('active', 'grace_period', 'billing_retry') THEN
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
    'Brings the GRANTED pool up to date (196). Paid: one allocation per Apple paid period, from a '
    'PRODUCTION subscriptions row (active, period end in the future) whose tier is above the '
    'complimentary floor, each (original_transaction_id, period_end) granted at most once via '
    'subscription_credit_periods; a new period waits for the running one to end unless it is a '
    'higher tier (an upgrade carries leftovers capped by the old period''s elapsed share); a '
    'refunded period is clawed back and marked, and reinstated once if Apple reverses the refund; '
    'a lapsed one expires into the calendar allowance. Calendar (Free / complimentary / Sandbox): '
    'resets on the ET 1st with 194''s monthly_expired row. Never touches the purchased pool (App '
    'Store 3.1.1). Returns the granted remaining. service_role only.';

-- ── 6. grant_tier_upgrade — kept for its callers, now the same reconciliation ─────────────

CREATE OR REPLACE FUNCTION public.grant_tier_upgrade(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
BEGIN
    IF p_user_id = '00000000-0000-4000-8000-00000000dead'::UUID THEN
        RETURN 0;
    END IF;
    -- Before 196 this topped the balance up to users.tier's full allocation on any upgrade —
    -- the first half of the double grant. A paid allocation now comes only from a paid period,
    -- and a raised complimentary (or Sandbox) tier is topped up by ensure_credit_period's
    -- calendar branch.
    RETURN public.ensure_credit_period(p_user_id);
END;
$$;

COMMENT ON FUNCTION public.grant_tier_upgrade(UUID) IS
    'Kept for its callers (iap_service.reconcile_user_tier, scripts/set_comp_tier.py). Since 196 it '
    'runs ensure_credit_period: a paid allocation is granted per Apple paid period there, and a '
    'raised complimentary or Sandbox tier is topped up by its calendar branch. service_role only.';

-- ── 7. revoke_tier_credits — 139's arithmetic, only for the period the refund was for ─────

CREATE OR REPLACE FUNCTION public.revoke_tier_credits(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST       CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_free       INTEGER;
    v_row        public.user_credits%ROWTYPE;
    v_cur        public.subscription_credit_periods%ROWTYPE;
    v_sub        public.subscriptions%ROWTYPE;
    v_has_sub    BOOLEAN := FALSE;
    v_floor      INTEGER;
    v_writeoff   INTEGER;
    v_delta      INTEGER;
    v_now_et     TIMESTAMP;
    v_next_reset TIMESTAMPTZ;
BEGIN
    IF p_user_id = _GUEST THEN
        RETURN 0;
    END IF;

    SELECT monthly_credits INTO v_free FROM public.plan_credits WHERE tier = 'free';
    IF v_free IS NULL THEN
        v_free := 0;
    END IF;

    SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 0;
    END IF;

    v_now_et     := (now() AT TIME ZONE 'America/New_York');
    v_next_reset := (date_trunc('month', v_now_et) + INTERVAL '1 month')
                        AT TIME ZONE 'America/New_York';

    -- 196: in paid mode, claw back only when the refunded transaction IS the period in force.
    -- ensure_credit_period (run first by iap_service) has normally done it already and left
    -- paid mode; reaching here still in paid mode means the refund was for another period.
    IF v_row.paid_period_id IS NOT NULL THEN
        SELECT * INTO v_cur FROM public.subscription_credit_periods WHERE id = v_row.paid_period_id;
        SELECT * INTO v_sub FROM public.subscriptions WHERE user_id = p_user_id;
        v_has_sub := FOUND;
        IF NOT COALESCE(v_has_sub
                        AND v_sub.status = 'revoked'
                        AND v_sub.original_transaction_id = v_cur.original_transaction_id
                        AND v_sub.current_period_end = v_cur.period_end, FALSE) THEN
            RAISE WARNING 'revoke_tier_credits: user % - the refunded transaction is not the paid period in force (%/%); nothing clawed back',
                p_user_id, v_cur.original_transaction_id, v_cur.period_end;
            RETURN (v_row.total - v_row.used);
        END IF;
        UPDATE public.subscription_credit_periods
           SET revoked_at        = now(),
               revoked_remaining = GREATEST(v_row.total - v_row.used, 0)
         WHERE id = v_cur.id
           AND revoked_at IS NULL;
    END IF;

    v_floor := GREATEST(v_free, v_row.used);

    -- Write off the spend that the refunded subscription paid for, exactly as section 5 does
    -- for a charged-back pack. Flooring `total` at `used` and leaving `used` alone left a
    -- high-water mark that survives into the NEXT subscription: `grant_tier_upgrade` would
    -- raise `total` to the new allocation but `used` still carried the old tier's spending,
    -- so `remaining` stayed 0 and the paid re-subscribe delivered nothing usable.
    -- `remaining` is unchanged by the write-off (both columns move together).
    v_writeoff := GREATEST(v_row.used - v_free, 0);

    -- Idempotent: a replayed REFUND finds total already at the floor. `tier_alloc` is still
    -- written, because the first delivery may have landed before this migration. 196: a
    -- refund also ends paid mode, so the pool follows the ET calendar from now on.
    IF v_row.total <= v_floor AND v_writeoff = 0 THEN
        UPDATE public.user_credits
           SET tier_alloc     = LEAST(v_row.tier_alloc, v_free),
               resets_at      = CASE WHEN v_row.paid_period_id IS NULL THEN resets_at
                                     ELSE v_next_reset END,
               paid_period_id = NULL,
               updated_at     = NOW()
         WHERE user_id = p_user_id;
        RETURN (v_row.total - v_row.used);
    END IF;

    v_delta := v_floor - v_row.total;          -- <= 0, the change in `remaining`

    UPDATE public.user_credits
       SET total          = v_floor - v_writeoff,
           used           = used - v_writeoff,
           tier_alloc     = LEAST(v_row.tier_alloc, v_free),
           resets_at      = CASE WHEN v_row.paid_period_id IS NULL THEN resets_at
                                 ELSE v_next_reset END,
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
END;
$$;

COMMENT ON FUNCTION public.revoke_tier_credits(UUID) IS
    'Apple REFUND/REVOKE of a subscription: claws back the unspent granted pool to '
    'GREATEST(free allocation, used) and writes off the spend (139), then leaves paid mode (196: '
    'paid_period_id NULL, resets_at the next ET 1st). In paid mode it acts only when the refunded '
    'transaction is the period in force, which it marks revoked; otherwise it logs a WARNING and '
    'changes nothing. Never touches the purchased pool. service_role only.';

REVOKE ALL ON FUNCTION public.ensure_credit_period(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ensure_credit_period(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.grant_tier_upgrade(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.grant_tier_upgrade(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_tier_credits(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_tier_credits(UUID) TO service_role;

COMMIT;

-- VERIFY (run after applying):
--   1. One row: t, t, t, f, f, t.
--   SELECT to_regclass('public.subscription_credit_periods') IS NOT NULL AS has_table,
--          EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
--                    AND table_name = 'user_credits' AND column_name = 'paid_period_id') AS has_period_col,
--          EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
--                    AND table_name = 'subscriptions' AND column_name = 'environment') AS has_env_col,
--          has_function_privilege('anon', 'public.ensure_credit_period(uuid)', 'EXECUTE') AS anon_exec,
--          has_table_privilege('authenticated', 'public.subscription_credit_periods', 'SELECT') AS auth_select,
--          pg_get_functiondef('public.ensure_credit_period(uuid)'::regprocedure) LIKE '%plan_period%' AS new_body;
--
--   2. Production subscribers left in calendar mode. Expect 0 rows, or only rows the backfill
--      deliberately skipped: tier_alloc below the plan (a grant that had failed; the next credit
--      read grants the period) or a comp tier at or above the plan.
--   SELECT s.user_id, s.status, s.tier, s.current_period_end, uc.tier_alloc, u.comp_tier
--     FROM public.subscriptions s
--     JOIN public.user_credits uc ON uc.user_id = s.user_id
--     JOIN public.users u ON u.id = s.user_id
--    WHERE s.status IN ('active', 'grace_period', 'billing_retry') AND s.tier <> 'free'
--      AND uc.paid_period_id IS NULL;
--
--   3. Accounts the calendar branch now sizes from Free although users.tier is paid — a paid
--      tier with a Production subscriptions row that does not entitle, and no complimentary
--      floor. Each is either a lapsed subscriber awaiting the expiry sweep (fine) or a tester who
--      needs `UPDATE public.users SET comp_tier = tier WHERE id = '<uuid>';`:
--   SELECT u.id, u.email, u.tier, u.comp_tier, s.status, s.current_period_end
--     FROM public.users u JOIN public.subscriptions s ON s.user_id = u.id
--    WHERE u.tier <> 'free' AND u.comp_tier IS NULL
--      AND s.status NOT IN ('active', 'grace_period', 'billing_retry');
