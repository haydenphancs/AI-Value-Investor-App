-- 139_credit_refund_and_grant_correctness.sql
--
-- Why: an adversarial review of the money path (6 per-layer finders, each verified by a
-- skeptic that defaulted to REFUTED) confirmed three defects in the credit RPCs. Each is
-- reachable without attacker control, and two of them move credits in the wrong direction.
--
--   A. REFUND MINTS CREDITS. `refund_credits`' fallback fires whenever no un-reversed debit
--      is found, and pays `LEAST(p_amount, used)` — bounded by the user's CURRENT `used`,
--      not by the debit being refunded. Refund a charge twice and the second one pays out
--      again from unrelated spend. Reachable via a committed-but-unacked insert: a
--      Cloudflare 520 or read timeout after Postgres commits makes research.py refund a row
--      that IS in the table, and the reconciliation sweep later refunds it a second time.
--
--   B. CHARGED-BACK CREDITS COME BACK. `revoke_purchased_credits` floors `purchased_total`
--      at `purchased_used` and never touches `purchased_used`. `spendable` is
--      (total-used)+(purchased_total-purchased_used), so a later report refund lowers
--      `purchased_used` and RAISES `spendable` on a pack Apple has already refunded.
--
--   C. A PAID RE-SUBSCRIBE GRANTS NOTHING. `revoke_tier_credits` sets
--      `total = GREATEST(free_alloc, used)` — a high-water mark, not an allocation — and
--      `grant_tier_upgrade`'s replay guard `IF v_alloc <= v_row.total` reads that as
--      "already granted". A Max subscriber who spent >1200, got an Apple refund, then
--      subscribes to Pro pays $14.99 and receives ZERO credits until the ET month rolls.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- A FOURTH THING, found while writing this, that makes (A) far more likely than it looks
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- `credit_transactions.granted_delta` carries the comment "delta = granted_delta +
-- purchased_delta for every row written from migration 118 onward". THAT IS CURRENTLY FALSE.
-- `ensure_credit_period`, `grant_tier_upgrade` and `revoke_tier_credits` all INSERT with the
-- short column list `(user_id, delta, reason, ref_id, balance_after)`, leaving both split
-- columns at their `0` DEFAULT while `delta` is non-zero. Those rows are indistinguishable
-- from genuine pre-117 "unknown split" rows — which is exactly the condition that sends
-- `refund_credits` down the fallback in (A). Section 6 fixes the reads; sections 2-4 fix the
-- writes, so the ambiguity stops being produced.
--
-- Two negative-delta rows also carry a `ref_id` and are therefore *pairable debits* that a
-- refund can currently reverse: `pack_revoked` (ref_id = the Apple transaction id) and
-- `tier_revoked` (ref_id = 'YYYY-MM'). Section 6 excludes them by reason.
--
-- Apply order: safe in both directions. The new column is additive with a DEFAULT and is
-- backfilled from `total`, so the OLD code keeps working against the new schema (it simply
-- never reads `tier_alloc`), and the NEW functions work against a freshly-backfilled column.
--
-- Safe to re-run.

BEGIN;

-- ── 1. user_credits.tier_alloc — the allocation actually granted this period ──────────────
--
-- `total` cannot answer "have we already granted this tier's allocation?", because
-- `revoke_tier_credits` overwrites it with GREATEST(free_alloc, used) — a number that has
-- nothing to do with any allocation. This column records the answer explicitly.
--
-- Backfilled from `total` rather than 0 so existing users' behaviour is unchanged on apply:
-- a mid-month replay still short-circuits exactly as it does today.

ALTER TABLE public.user_credits
    ADD COLUMN IF NOT EXISTS tier_alloc INTEGER NOT NULL DEFAULT 0;

UPDATE public.user_credits
   SET tier_alloc = total
 WHERE tier_alloc = 0
   AND total > 0;

COMMENT ON COLUMN public.user_credits.tier_alloc IS
    'Monthly allocation actually granted for the CURRENT period. Written by '
    'ensure_credit_period (on reset), grant_tier_upgrade (on grant) and revoke_tier_credits '
    '(down to the free allocation). Read only by grant_tier_upgrade''s replay guard. Exists '
    'because `total` is a high-water mark after a revoke, not an allocation, and using it as '
    'the guard made a paid re-subscribe grant nothing. See migration 139.';


-- ── 2. ensure_credit_period: stamp tier_alloc, and ledger an honest split ─────────────────
--
-- Body is migration 100's verbatim except: the reset UPDATE also sets `tier_alloc`, the
-- first-touch INSERT seeds it, and both ledger INSERTs now carry granted_delta/purchased_delta.

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


-- ── 3. grant_tier_upgrade: guard on tier_alloc, not on the corrupted total ────────────────
--
-- Body is migration 112's verbatim except the replay guard, the two `tier_alloc` writes, and
-- the honest ledger split.

CREATE OR REPLACE FUNCTION public.grant_tier_upgrade(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST      CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_tier      public.user_tier;
    v_alloc     INTEGER;
    v_row       public.user_credits%ROWTYPE;
    v_now_et    TIMESTAMP;
    v_next_reset TIMESTAMPTZ;
    v_delta     INTEGER;
BEGIN
    IF p_user_id = _GUEST THEN
        RETURN 0;
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
            VALUES (p_user_id, v_alloc, v_alloc, 0, 'tier_upgrade',
                    to_char(v_now_et, 'YYYY-MM'), v_alloc);
            RETURN v_alloc;
        END IF;
        SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 0;
        END IF;
    END IF;

    -- THE FIX. This compared against `v_row.total`, which `revoke_tier_credits` overwrites
    -- with GREATEST(free_alloc, used) — so after an Apple refund of a subscription, a user
    -- who had spent more than the next plan's allocation looked like they had "already been
    -- granted" it, and a fresh PAID subscription granted nothing. `tier_alloc` records what
    -- was actually granted for this period and is reset to the free allocation on a revoke,
    -- so a genuine re-subscribe is never mistaken for a replay.
    IF v_alloc <= v_row.tier_alloc THEN
        RETURN (v_row.total - v_row.used);
    END IF;

    -- `total` can legitimately already be at or above the allocation (a revoke high-water
    -- mark, or a mid-period downgrade). Never LOWER it here — this function only ever raises
    -- the ceiling — but always record the allocation we just granted.
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
END;
$$;


-- ── 4. revoke_tier_credits: drop tier_alloc to the free allocation ────────────────────────
--
-- Body is migration 114's verbatim except the `tier_alloc` write and the honest ledger split.
-- Without the `tier_alloc` write the fix in section 3 would be inert: the guard would still
-- see the pre-revoke allocation and refuse to grant a genuine re-subscribe.

CREATE OR REPLACE FUNCTION public.revoke_tier_credits(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST      CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_free      INTEGER;
    v_row       public.user_credits%ROWTYPE;
    v_floor     INTEGER;
    v_writeoff  INTEGER;
    v_delta     INTEGER;
    v_now_et    TIMESTAMP;
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

    v_floor := GREATEST(v_free, v_row.used);

    -- Write off the spend that the refunded subscription paid for, exactly as section 5 does
    -- for a charged-back pack. Flooring `total` at `used` and leaving `used` alone left a
    -- high-water mark that survives into the NEXT subscription: `grant_tier_upgrade` would
    -- raise `total` to the new allocation but `used` still carried the old tier's spending,
    -- so `remaining` stayed 0 and the paid re-subscribe delivered nothing usable.
    -- `remaining` is unchanged by the write-off (both columns move together).
    v_writeoff := GREATEST(v_row.used - v_free, 0);

    -- Idempotent: a replayed REFUND finds total already at the floor. `tier_alloc` is still
    -- written, because the first delivery may have landed before this migration.
    IF v_row.total <= v_floor AND v_writeoff = 0 THEN
        UPDATE public.user_credits
           SET tier_alloc = LEAST(v_row.tier_alloc, v_free),
               updated_at = NOW()
         WHERE user_id = p_user_id;
        RETURN (v_row.total - v_row.used);
    END IF;

    v_delta  := v_floor - v_row.total;          -- <= 0, the change in `remaining`
    v_now_et := (now() AT TIME ZONE 'America/New_York');

    UPDATE public.user_credits
       SET total      = v_floor - v_writeoff,
           used       = used - v_writeoff,
           tier_alloc = LEAST(v_row.tier_alloc, v_free),
           updated_at = NOW()
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


-- ── 5. revoke_purchased_credits: write off the SPENT portion of a charged-back pack ───────
--
-- Body is migration 117's verbatim except the balance UPDATE.
--
-- The floor at `purchased_used` was right about not letting `purchased_remaining` go
-- negative and wrong about leaving `purchased_used` alone. Because `spendable` subtracts
-- `purchased_used`, a later report refund lowers it and RAISES the balance on a pack Apple
-- has refunded — money back AND credits kept.
--
-- Reduce BOTH columns by the amount the floor lifted the total, i.e. the already-spent
-- portion of this pack. `purchased_remaining` is unchanged (0), but the spent baseline is
-- written off, so a later refund caps `back_purch` at `purchased_used = 0` and correctly
-- returns nothing for a spend the customer was refunded for.
--
-- Worked: total=130 used=20, revoking 130 -> floor 20, writeoff 20 -> total 0, used 0.
--         total=200 used=150, revoking 130 -> floor 150, writeoff 80 -> total 70, used 70.
--         total=500 used=20,  revoking 130 -> floor 370, writeoff 0  -> unchanged behaviour.

CREATE OR REPLACE FUNCTION public.revoke_purchased_credits(
    p_transaction_id TEXT,
    p_environment    TEXT DEFAULT 'Production'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_env         TEXT;
    v_row         public.credit_purchases%ROWTYPE;
    v_credits_row public.user_credits%ROWTYPE;
    v_new_total   INTEGER;
    v_delta       INTEGER;
    v_writeoff    INTEGER;
    v_spendable   INTEGER;
BEGIN
    IF p_transaction_id IS NULL OR p_transaction_id = '' THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'missing_transaction_id');
    END IF;

    v_env := COALESCE(NULLIF(p_environment, ''), 'Production');

    SELECT * INTO v_row FROM public.credit_purchases
     WHERE environment = v_env AND transaction_id = p_transaction_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'unknown');
    END IF;

    IF v_row.revoked_at IS NOT NULL THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = v_row.user_id;
        RETURN jsonb_build_object(
            'outcome',   'already_revoked',
            'spendable', COALESCE(v_spendable, 0)
        );
    END IF;

    UPDATE public.credit_purchases
       SET revoked_at = NOW()
     WHERE id = v_row.id;

    SELECT * INTO v_credits_row FROM public.user_credits
     WHERE user_id = v_row.user_id FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'revoked', 'spendable', 0, 'reclaimed', 0);
    END IF;

    v_new_total := GREATEST(v_credits_row.purchased_used,
                            v_credits_row.purchased_total - v_row.credits);
    v_delta     := v_new_total - v_credits_row.purchased_total;   -- <= 0

    -- How far the floor lifted the total: the portion of this pack already spent. Clamped to
    -- [0, purchased_used] so a corrupt row (credits > purchased_total) cannot drive either
    -- column negative and trip a CHECK.
    v_writeoff := LEAST(
        GREATEST(v_new_total - (v_credits_row.purchased_total - v_row.credits), 0),
        v_credits_row.purchased_used
    );

    IF v_delta = 0 AND v_writeoff = 0 THEN
        RETURN jsonb_build_object(
            'outcome',   'revoked',
            'spendable', v_credits_row.spendable,
            'reclaimed', 0
        );
    END IF;

    UPDATE public.user_credits
       SET purchased_total = v_new_total - v_writeoff,
           purchased_used  = purchased_used - v_writeoff,
           updated_at      = NOW()
     WHERE user_id = v_row.user_id
    RETURNING spendable INTO v_spendable;

    -- Ledger the change in SPENDABLE (v_delta). The write-off moves total and used by the
    -- same amount, so it contributes nothing to the balance and must not be double-counted.
    IF v_delta <> 0 THEN
        INSERT INTO public.credit_transactions
            (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
        VALUES
            (v_row.user_id, v_delta, 0, v_delta, 'pack_revoked', p_transaction_id, v_spendable);
    END IF;

    RETURN jsonb_build_object(
        'outcome',   'revoked',
        'spendable', v_spendable,
        'reclaimed', -v_delta
    );
END;
$$;


-- ── 6. refund_credits: the fallback may only fire for a debit we actually FOUND ───────────
--
-- Body is migration 124's verbatim except the debit lookup's `reason` exclusion and the
-- IF/ELSIF/ELSE.
--
-- Before, "no un-reversed debit found" fell through to a granted-first payout bounded by the
-- user's CURRENT `used`. That conflates two very different states:
--
--   * a debit EXISTS with a 0/0 split (pre-117 rows, and — until sections 2-4 above — every
--     grant/reset/revoke row). Unknown split; the fallback is correct and non-farmable.
--   * NO un-reversed debit matched at all: already reversed, or never charged. Paying here
--     hands back credits that were never debited for this ref_id.
--
-- `spend_credits` always writes a ledger row, so the second case can only mean already-
-- reversed or a caller bug — and paying nothing is the only direction that cannot mint.

CREATE OR REPLACE FUNCTION public.refund_credits(
    p_user_id UUID,
    p_amount  INTEGER,
    p_reason  TEXT,
    p_ref_id  TEXT
)
RETURNS INTEGER
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
BEGIN
    IF p_user_id = _GUEST THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_spendable, 0);
    END IF;

    IF p_amount IS NULL OR p_amount <= 0 THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN v_spendable;
    END IF;

    SELECT used, purchased_used INTO v_old_used, v_old_pused
      FROM public.user_credits
     WHERE user_id = p_user_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF p_ref_id IS NOT NULL AND p_ref_id <> '' THEN
        v_searched := TRUE;
        SELECT t.id, -t.granted_delta, -t.purchased_delta
          INTO v_debit_id, v_spent_granted, v_spent_purch
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
        -- THE FIX. We DID search on a ref_id and found no un-reversed debit: this charge was
        -- already refunded, or never happened. Paying from the user's current `used` would
        -- MINT credits — returning money for a debit that no longer exists. Fall through to
        -- the zero branch below.
        --
        -- Note the `NOT v_searched` arm above: without it a refund called with no ref_id
        -- would pay nothing and silently lose a legitimate refund, which is the same bug
        -- pointing the other way.
        --
        -- ⚠️ This branch also catches a MISMATCHED ref_id — a refund keyed differently from
        -- its charge (the shape of a real bug: the reconciliation sweep once refunded with
        -- report_id while research.py charged with the ticker). Before 139 that silently
        -- expired paid credits by refunding them into the granted pool; now it is a no-op,
        -- which does not destroy or mint anything but DOES leave the user owed. Neither is
        -- correct, so make it findable instead of silent — this is the only signal a
        -- mismatched key produces.
        RAISE WARNING 'refund_credits: no un-reversed debit for user=% ref_id=% amount=% '
                      '(already refunded, or the refund ref_id does not match its charge) '
                      '— refunding nothing',
                      p_user_id, p_ref_id, p_amount;
    END IF;

    IF v_back_granted + v_back_purch = 0 THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN v_spendable;
    END IF;

    UPDATE public.user_credits
       SET used           = used - v_back_granted,
           purchased_used = purchased_used - v_back_purch,
           updated_at     = NOW()
     WHERE user_id = p_user_id
    RETURNING spendable INTO v_spendable;

    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after,
         reverses_id)
    VALUES
        (p_user_id, v_back_granted + v_back_purch, v_back_granted, v_back_purch,
         p_reason, p_ref_id, v_spendable, v_debit_id);

    RETURN v_spendable;
END;
$$;


-- ── 7. Lock-down ─────────────────────────────────────────────────────────────────────────
--
-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by default, and Supabase exposes every
-- public function at POST /rest/v1/rpc/<name>. Re-applied for every function this migration
-- replaced: CREATE OR REPLACE preserves existing grants, but a function REPLACED after a
-- future DROP would come back wide open.

REVOKE ALL ON FUNCTION public.ensure_credit_period(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ensure_credit_period(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.grant_tier_upgrade(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.grant_tier_upgrade(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_tier_credits(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_tier_credits(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_purchased_credits(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_purchased_credits(TEXT, TEXT) TO service_role;

REVOKE ALL ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;

COMMIT;
