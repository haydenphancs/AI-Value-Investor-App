-- 124_refund_pairs_each_debit_once.sql
--
-- Why: `refund_credits` (migration 118) finds the spend it is reversing with
--
--     WHERE user_id = p_user_id AND ref_id = p_ref_id AND delta = -p_amount
--     ORDER BY id DESC LIMIT 1
--
-- and NOTHING stops two refunds from selecting the SAME debit row. `ref_id` is deliberately
-- shared across many charges of identical cost — `research.py` charges `ref_id=<TICKER>` at 20
-- credits and the per-user cap explicitly allows 4 personas on one ticker concurrently;
-- `ticker_report.py` uses `report_chat:{ticker}` and `chat.py` uses the session id, both at 1
-- credit per turn. The `delta = -p_amount` clause added in 118 removed only the CROSS-cost
-- mismatch. The same-cost case is the common one, and it produces two distinct failures:
--
--   A. WRONG POOL. Two 20-credit reports on one ticker, the first paid from GRANTED and the
--      second from PURCHASED. The first one fails; its refund selects the newest matching debit
--      — the second one's — and returns 20 credits to the PURCHASED pool instead of granted.
--
--   B. SILENT TOTAL LOSS, which is the serious one. Three same-cost charges; refund the third
--      (correctly paired), then refund the first. The newest matching debit is STILL the third,
--      whose purchased split has already been given back, so both caps
--      (`LEAST(..., v_old_pused)` = 0) collapse to zero, the `v_back_granted + v_back_purch = 0`
--      branch returns the live balance, and NO ledger row is written. The user is charged for an
--      undelivered report and refunded nothing — while `refund_credits` returns non-NULL and
--      `CreditService.refund_ledgered` logs "Refunded 20 credits" as a success. A Gemini outage
--      failing several in-flight same-ticker reports makes this the ordinary case.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- THE FIX: a refund PAIRS with a debit, and a debit can be paired at most ONCE
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- `credit_transactions.reverses_id` records which debit a refund reverses, and the lookup skips
-- any debit that already has one pointing at it. That single constraint is what makes the whole
-- thing sound, and it is worth being precise about why:
--
--   * A reversal returns AT MOST what its paired debit took from each pool (the existing
--     `LEAST(GREATEST(v_spent_*, 0), ...)` caps). With each debit paired at most once, the sum
--     of everything ever returned to the purchased pool can never exceed the sum of everything
--     ever taken from it. **Minting permanent credits is therefore structurally impossible**,
--     for any pairing order — that is a property of the pairing constraint, not of the ORDER BY.
--   * Symmetrically nothing can return more to granted than granted supplied, so credits cannot
--     be destroyed either. Totals are conserved.
--   * And scenario B cannot recur: the already-reversed debit is no longer selectable, so the
--     second refund pairs with a real un-reversed debit and actually pays out.
--
-- ORDER BY stays `id DESC` (newest un-reversed first). Refunds normally fire immediately after
-- their own charge, so newest-first is the EXACT pairing in the common case.
--
-- ⚠️ Residual, stated honestly: when several same-cost charges on one ref_id are outstanding and
-- they are refunded out of order, a refund may adopt an equivalent sibling's split rather than
-- its own. Totals stay correct and nothing can be farmed (see above) — only the granted/purchased
-- attribution of that one refund differs, and it nets out once the siblings settle. The only way
-- to make attribution exact is a per-charge-unique `ref_id`, which the charge sites cannot cheaply
-- produce today (`research.py` precharges BEFORE the `research_reports` insert, so no report id
-- exists yet). If a ref_id ever does become unique per charge, this pairing keeps working
-- unchanged and simply becomes exact.
--
-- Apply order: independent. Adding the column alone changes nothing (the old function never
-- writes it); replacing the function alone would also work, since `NOT EXISTS` over a column
-- that is always NULL is simply always true — but then nothing would ever be marked, so apply
-- BOTH, which this single migration does atomically.
--
-- Safe to re-run.

BEGIN;

-- ── 1. The pairing link ──────────────────────────────────────────────────────────────────

ALTER TABLE public.credit_transactions
    ADD COLUMN IF NOT EXISTS reverses_id BIGINT;

COMMENT ON COLUMN public.credit_transactions.reverses_id IS
    'For a refund row: the id of the DEBIT row it reverses. NULL on debits, on grants, on '
    'monthly resets, and on any refund that found no matching debit (the granted-first '
    'fallback). A debit with a row pointing at it is already reversed and can never be paired '
    'again — that is what makes over-returning to either pool impossible. See migration 124.';

-- Serves the `NOT EXISTS (SELECT 1 ... WHERE reverses_id = t.id)` anti-join below. Partial,
-- because only refund rows ever populate it and they are a small minority of the ledger.
CREATE INDEX IF NOT EXISTS idx_credit_transactions_reverses
    ON public.credit_transactions (reverses_id)
    WHERE reverses_id IS NOT NULL;


-- ── 2. refund_credits: pair with an UN-REVERSED debit, and record the pairing ────────────
-- Body is migration 118's verbatim except for the lookup block and the ledger INSERT.
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
    v_debit_id       BIGINT;     -- the debit row this refund pairs with, NULL if none
    v_spent_granted  INTEGER;    -- positive magnitude taken from granted by that spend
    v_spent_purch    INTEGER;    -- positive magnitude taken from purchased
    v_have_split     BOOLEAN := FALSE;
    v_back_granted   INTEGER := 0;
    v_back_purch     INTEGER := 0;
BEGIN
    IF p_user_id = _GUEST THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_spendable, 0);
    END IF;

    -- Non-positive refund is a caller/sign bug — no-op (a negative amount would DEBIT).
    IF p_amount IS NULL OR p_amount <= 0 THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN v_spendable;
    END IF;

    -- Lock the balance row for the rest of the transaction. Everything below is computed in
    -- plpgsql against these captured values, which is safe precisely because of this lock — no
    -- other transaction can move the row underneath us, and that same lock serialises two
    -- concurrent refunds so they cannot both pass the NOT EXISTS check on one debit.
    SELECT used, purchased_used INTO v_old_used, v_old_pused
      FROM public.user_credits
     WHERE user_id = p_user_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NULL;  -- no credit row for this user (nothing to refund)
    END IF;

    -- Pair with the newest debit of this amount on this ref_id that NOTHING has reversed yet.
    -- The `NOT EXISTS` anti-join is the entire fix — see the header for why pairing-at-most-once
    -- is what makes over-returning to either pool impossible.
    IF p_ref_id IS NOT NULL AND p_ref_id <> '' THEN
        SELECT t.id, -t.granted_delta, -t.purchased_delta
          INTO v_debit_id, v_spent_granted, v_spent_purch
          FROM public.credit_transactions t
         WHERE t.user_id = p_user_id
           AND t.ref_id  = p_ref_id
           AND t.delta   = -p_amount
           AND NOT EXISTS (
                 SELECT 1 FROM public.credit_transactions r
                  WHERE r.reverses_id = t.id
               )
         ORDER BY t.id DESC
         LIMIT 1;

        -- A row written before migration 117 carries 0/0 with a non-zero delta: the split is
        -- unknown, not zero. Treat it as unknown and take the fallback — but keep v_debit_id so
        -- the row is still marked reversed and cannot be paired twice.
        IF FOUND AND (COALESCE(v_spent_granted, 0) <> 0 OR COALESCE(v_spent_purch, 0) <> 0) THEN
            v_have_split := TRUE;
        END IF;
    END IF;

    IF v_have_split THEN
        -- Exact reversal, each side capped BOTH by what the paired spend took from that pool and
        -- by what the pool currently shows as spent.
        v_back_purch   := LEAST(GREATEST(v_spent_purch, 0), p_amount, v_old_pused);
        v_back_granted := LEAST(GREATEST(v_spent_granted, 0), p_amount - v_back_purch,
                                v_old_used);
    ELSE
        -- Unknown split (no ref_id, no un-reversed debit, or a pre-117 row): granted-first,
        -- overflowing to purchased only if granted cannot absorb it (otherwise the credits would
        -- simply vanish). The non-farmable direction.
        v_back_granted := LEAST(p_amount, v_old_used);
        v_back_purch   := LEAST(p_amount - v_back_granted, v_old_pused);
    END IF;

    IF v_back_granted + v_back_purch = 0 THEN
        -- Genuinely nothing to give back: both pools already show the spend returned. Distinct
        -- from the pre-124 bug, where this branch fired because the refund had re-selected an
        -- already-reversed debit. Return the live balance without writing a zero-delta row.
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN v_spendable;
    END IF;

    -- Both terms are capped at the corresponding `*_used` above, so neither can go negative and
    -- the purchased_used <= purchased_total CHECK cannot be tripped.
    UPDATE public.user_credits
       SET used           = used - v_back_granted,
           purchased_used = purchased_used - v_back_purch,
           updated_at     = NOW()
     WHERE user_id = p_user_id
    RETURNING spendable INTO v_spendable;

    -- Ledger the ACTUAL movement, not the requested amount, so a (contract-forbidden)
    -- over-refund cannot desync sum(delta) from the balance. `reverses_id` is what retires the
    -- paired debit from any future lookup.
    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after,
         reverses_id)
    VALUES
        (p_user_id, v_back_granted + v_back_purch, v_back_granted, v_back_purch,
         p_reason, p_ref_id, v_spendable, v_debit_id);

    RETURN v_spendable;
END;
$$;

REVOKE ALL ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;

COMMIT;
