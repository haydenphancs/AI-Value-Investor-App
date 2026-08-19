-- 142_refund_credits_reports_its_outcome.sql
--
-- Why: migration 139 made a mismatched/unmatched `ref_id` refund NOTHING instead of minting
-- credits. That was right, but it left the failure SILENT — and the silence is expensive.
--
-- `refund_credits` returns `spendable`, so the no-op branch is byte-identical to a successful
-- refund. `credit_service.refund_ledgered` therefore logs "Refunded 20 credits ... remaining=140"
-- on a refund that moved zero. The only signal 139 could give was `RAISE WARNING`, which
-- PostgREST does not surface — it dies in the Postgres log where nothing reads it.
--
-- That matters because all three report sites burn the one-shot `research_reports.is_refunded`
-- CAS BEFORE calling this function (research.py:300, :774, research_reconciliation_service.py:141).
-- There is no retry. The user loses 20 credits — 40% of a free account's monthly allocation —
-- and iOS then renders a "[Refunded]" chip off that same flag, so they are told they were
-- refunded. No report, no credits, and a UI that says otherwise.
--
-- ── What this migration changes ───────────────────────────────────────────────────────────
--
-- Body is 139's VERBATIM except:
--   1. RETURNS JSONB instead of INTEGER, and every RETURN becomes a jsonb_build_object.
--   2. A new discriminator on the path that currently only warns (§ below).
--   3. `v_refunded` carries the amount actually moved, so the caller can stop guessing.
--
-- ── The discriminator, and why it is the whole point ──────────────────────────────────────
--
-- 139's ELSE branch conflates two situations that deserve OPPOSITE treatment:
--
--   (a) the debit exists but is already reversed  -> an idempotent replay. Benign. Must NOT page.
--   (b) no debit matches this ref_id/amount at all -> wrong key, wrong amount, or never charged.
--       The user is OWED credits. Must be loud.
--
-- Making both loud trades a silent-money bug for an alert-fatigue bug, and alert fatigue is how
-- the loud one gets ignored. They are separable: 139's lookup excludes already-reversed debits
-- via `NOT EXISTS (reverses_id)` (migration 124). Re-running that same lookup WITHOUT the
-- anti-join answers "does a matching debit exist at all?" — found => (a), not found => (b).
--
-- The extra lookup runs ONLY on the no-op path (rare) and hits the same index as the first.
--
-- ── ⚠️ DROP FUNCTION: the first in this repo ──────────────────────────────────────────────
--
-- Postgres cannot change a function's return type with CREATE OR REPLACE, and no migration here
-- has ever dropped a function (checked: `DROP FUNCTION` appears nowhere in this directory).
-- Two consequences, both handled below:
--
--   * A DROP DISCARDS GRANTS. `CREATE OR REPLACE` preserves them, which is why the existing
--     REVOKE/GRANT tails were belt-and-braces; here the tail is LOAD-BEARING. Without it the
--     recreated function comes back with PUBLIC EXECUTE — a credit-MOVING function reachable
--     by anyone holding the shipped anon key.
--   * DROP + CREATE are in the same BEGIN;/COMMIT; so no window exists where the function is
--     absent. A concurrent call blocks on the lock and then sees the new definition.
--
-- ⚠️ DEPLOY ORDER IS NOT OPTIONAL. `refund_ledgered` did `int(result.data)`, and `int(dict)`
-- raises TypeError OUTSIDE its try — so applying this before the matching backend deploy 500s
-- every refund. Ship the int-tolerant Python FIRST, then apply this. The tolerance is marked
-- for removal in credit_service.py.

BEGIN;

DROP FUNCTION IF EXISTS public.refund_credits(UUID, INTEGER, TEXT, TEXT);

CREATE FUNCTION public.refund_credits(
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
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        -- `v_outcome` is still 'refunded' when the caps genuinely resolved to zero (nothing
        -- left to give back) — that is a success that moved 0, exactly as
        -- revoke_purchased_credits reports `reclaimed: 0`. The two diagnostic outcomes above
        -- are what distinguish it from a refund that should have moved something.
        RETURN jsonb_build_object(
            'outcome', v_outcome, 'refunded', 0, 'spendable', v_spendable);
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

    RETURN jsonb_build_object(
        'outcome', 'refunded',
        'refunded', v_back_granted + v_back_purch,
        'spendable', v_spendable);
END;
$$;

COMMENT ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) IS
    'Reverses the RECORDED split of a spend. Returns JSONB {outcome, refunded, spendable} as of '
    'migration 142 (was INTEGER spendable). outcome: refunded | already_refunded | '
    'no_matching_debit | guest | invalid | no_credits_row. `no_matching_debit` means the user is '
    'OWED credits — credit_service.refund_ledgered logs it as a REFUND LEAK at ERROR.';

-- ── Lock-down ────────────────────────────────────────────────────────────────────────────
--
-- LOAD-BEARING here, unlike the equivalent tails in 117/139. Those functions were replaced with
-- CREATE OR REPLACE, which preserves privileges; this one was DROPPED, which discards them. Omit
-- this and a credit-MOVING function comes back with PUBLIC EXECUTE, reachable by anyone holding
-- the shipped anon key.
REVOKE ALL ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;

COMMIT;
