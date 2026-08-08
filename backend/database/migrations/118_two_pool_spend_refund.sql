-- 118_two_pool_spend_refund.sql
--
-- Why: migration 117 gave `user_credits` a second, never-expiring pool for credits bought as
-- consumable in-app purchases. `spend_credits` and `refund_credits` (migration 101) are still
-- blind to it — they test and mutate `total`/`used` only — so a user holding 500 purchased
-- credits and 0 granted credits gets a 402 on every action while their paid balance sits
-- untouched. This teaches the two gate functions about both pools.
--
-- Nothing else changes. `ensure_credit_period` (100), `grant_tier_upgrade` (112) and
-- `revoke_tier_credits` (114) are DELIBERATELY NOT TOUCHED: they are column-explicit, they
-- write only `total`/`used`, and their `SELECT * INTO ... %ROWTYPE` absorbs the new columns.
-- Their return values now describe the GRANTED pool only, which is a lie to a caller — but
-- every caller in the app discards them (users.py:87 calls ensure_period for its side effect
-- and re-reads the table; iap_service._revoke_tier_credits and reconcile_user_tier ignore the
-- result entirely), so rewriting three load-bearing functions that already carry two rounds of
-- money-bug fixes, purely to correct a value nobody reads, is a worse trade than a comment.
-- If a caller ever starts using one of those returns, change it THEN.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- SPEND ORDER AND REFUND ORDER ARE NOT INVERSES. DO NOT "FIX" THAT.
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- SPEND drains GRANTED first, then purchased. That protects the pool that never expires: a
-- user's paid credits survive as long as possible, which is what makes "your purchased credits
-- never expire" literally true rather than merely technically true.
--
-- REFUND reverses THE RECORDED SPLIT OF THE ORIGINAL SPEND, read from
-- credit_transactions.granted_delta / purchased_delta (migration 117). Neither simple ordering
-- is safe, and both are tempting:
--
--   * Refund PURCHASED-first is a laundering loop. Granted 50 unspent, purchased 100 fully
--     spent. A 20-credit report drains granted (used 0->20) and then fails. A purchased-first
--     refund hands the 20 back to the purchased pool: the user just converted 20 EXPIRING
--     credits into 20 PERMANENT ones for free. It repeats on any user-inducible failure — a
--     killed connection mid-generation will do — and drains the entire monthly allocation into
--     the permanent pool every month. Capping the refund at `purchased_used` does NOT fix
--     this; it bounds each conversion, not the number of conversions.
--
--   * Refund GRANTED-first is worse. It converts purchased -> granted, and
--     ensure_credit_period wipes granted on the 1st. That literally expires credits the user
--     paid cash for: the App Store Guideline 3.1.1 violation migration 117 exists to prevent.
--
-- Only reversing the actual split is both non-farmable and non-expiring.
--
-- FALLBACK when no split is on file (p_ref_id IS NULL, or the matched row predates 117 and
-- carries 0/0): granted-first, overflowing to purchased only if the granted pool cannot absorb
-- it. That is exactly right for pre-117 rows — the purchased pool did not exist then, so every
-- historical spend was 100% granted — and it is the direction that cannot mint permanent
-- credits.
--
-- ⚠️ The fallback is SAFE, not free: it is the branch that can move a purchased credit into
-- the granted pool, where the next monthly reset destroys it. So every refund call site must
-- pass THE SAME ref_id ITS CHARGE USED. All five do today (research.py -> ticker;
-- ticker_report.py -> "{ticker}:{persona}" for reports and "report_chat:{ticker}" for chat;
-- chat.py -> session id; research_reconciliation_service.py -> ticker) — but the
-- reconciliation one passed `report_id` until this migration's review caught it, which routed
-- EVERY reconciled report failure through the fallback and expired paid credits on the primary
-- failure path of the 20-credit action. If you add a refund site, match its charge's ref_id.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- LEDGER SEMANTICS CHANGE AT THIS MIGRATION
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- `credit_transactions.balance_after` meant "granted remaining" before this migration and
-- means "spendable" from here on. They are identical for every user whose purchased_total is
-- 0, which is everyone at apply time — but a support query that reconciles sum(delta) against
-- balance_after across the boundary will not tie out for a user who later buys a pack. Rows
-- are distinguishable: pre-118 spend/refund rows have granted_delta = purchased_delta = 0
-- with a non-zero delta.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- APPLY ORDER
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- 117 MUST be applied first, and the guard below enforces it rather than trusting the
-- operator. Without 117's columns every `spend_credits` call would error, `CreditService.
-- precharge` would raise `CreditServiceUnavailable`, and EVERY chat turn and EVERY report
-- would answer 409 SYSTEM_BUSY. That is the only genuinely dangerous ordering in this feature,
-- and a refused migration is infinitely better than a silent outage.
--
-- If you DO apply it out of order, the console shows the RAISE followed by a cascade of
-- "current transaction is aborted" lines. Only the first is meaningful — the transaction
-- rolls back whole and nothing is left half-applied. Apply 117, then re-run this.
--
-- This migration alone, with the application code undeployed, is safe: both purchased columns
-- are 0 for every row, so the arithmetic below reduces exactly to migration 101's behaviour.
--
-- Safe to re-run: CREATE OR REPLACE.

BEGIN;

-- ── 0. Refuse to apply before 117 ────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'user_credits'
           AND column_name  = 'purchased_total'
    ) THEN
        RAISE EXCEPTION
            'Migration 118 requires 117: public.user_credits.purchased_total does not exist. '
            'Apply 117_purchased_credits_and_packs.sql first — applying 118 alone would make '
            'every spend_credits call fail and take chat and reports down.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'credit_transactions'
           AND column_name  = 'purchased_delta'
    ) THEN
        RAISE EXCEPTION
            'Migration 118 requires 117: public.credit_transactions.purchased_delta does not '
            'exist. Apply 117_purchased_credits_and_packs.sql first.';
    END IF;
END $$;


-- ── 1. spend_credits: two-pool, granted-first, still ONE guarded UPDATE ──────────────────
CREATE OR REPLACE FUNCTION public.spend_credits(
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
    v_spendable       INTEGER;
    v_granted_taken   INTEGER;
    v_purchased_taken INTEGER;
BEGIN
    -- Guests are never metered (shared pool; never refilled). Return current spendable
    -- (non-NULL = "ok, not charged") without debiting.
    IF p_user_id = _GUEST THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_spendable, 0);
    END IF;

    -- Non-positive charge is a caller/sign bug — no-op (a negative amount would MINT credits
    -- via the predicate below). Return current spendable, no debit, no ledger row.
    IF p_amount IS NULL OR p_amount <= 0 THEN
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_spendable, 0);
    END IF;

    -- Roll into the current month's tier allocation first (no-op mid-month). This takes
    -- SELECT ... FOR UPDATE on the single user_credits row and holds it for this whole
    -- transaction, so the charge below serializes with concurrent spends and grants.
    PERFORM public.ensure_credit_period(p_user_id);

    -- ⚠️ ONE STATEMENT, ON PURPOSE. Every right-hand side is evaluated against the PRE-UPDATE
    -- tuple, so the granted/purchased split is computed atomically with the guard that
    -- authorises it. `prev` captures the old values so RETURNING (which sees the NEW row) can
    -- report how much came from each pool.
    --
    -- Do NOT refactor this into "read the balance, decide the split, then update". That
    -- reintroduces exactly the race the WHERE clause exists to close: two concurrent spends
    -- would both read a sufficient balance and both debit, overdrawing the account. The guard
    -- is the entire atomicity story of the credit system (migration 101), and a read-then-write
    -- makes it decorative.
    --
    -- Do NOT simplify to "wholly granted OR wholly purchased" either — a user with 15 granted
    -- and 1000 purchased would be refused a 20-credit report they can clearly afford.
    --
    -- GREATEST(0, total - used) rather than (total - used): `used > total` is not forbidden by
    -- any CHECK, and a negative granted remainder would otherwise make LEAST() subtract from
    -- `used` — a silent credit mint.
    WITH prev AS (
        SELECT used AS old_used, purchased_used AS old_purchased_used
          FROM public.user_credits
         WHERE user_id = p_user_id
           FOR UPDATE
    )
    UPDATE public.user_credits u
       SET used           = u.used
                              + LEAST(p_amount, GREATEST(0, u.total - u.used)),
           purchased_used = u.purchased_used
                              + GREATEST(0, p_amount - GREATEST(0, u.total - u.used)),
           updated_at     = NOW()
      FROM prev
     WHERE u.user_id = p_user_id
       AND (u.total - u.used) + (u.purchased_total - u.purchased_used) >= p_amount
    RETURNING u.spendable,
              (u.used - prev.old_used),
              (u.purchased_used - prev.old_purchased_used)
      INTO v_spendable, v_granted_taken, v_purchased_taken;

    IF v_spendable IS NULL THEN
        RETURN NULL;  -- insufficient (caller → 402); nothing debited, no ledger row
    END IF;

    -- Ledger the spend in the SAME transaction (guaranteed, not best-effort). The split is
    -- what makes an exact refund possible later — see the header.
    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
    VALUES
        (p_user_id, -p_amount, -v_granted_taken, -v_purchased_taken,
         p_reason, p_ref_id, v_spendable);

    RETURN v_spendable;
END;
$$;

REVOKE ALL ON FUNCTION public.spend_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.spend_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;


-- ── 2. refund_credits: reverse the RECORDED split ───────────────────────────────────────
-- Signature deliberately unchanged, so `CreditService.refund_ledgered` (credit_service.py:249)
-- and all of its call sites — chat.py's once-only settlement flag, ticker_report.py's
-- delivered flag, research_reconciliation_service.py's is_refunded CAS — are untouched.
-- Still NOT idempotent; those callers still guarantee at-most-once.
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
    v_spent_granted  INTEGER;   -- positive magnitude taken from granted by the original spend
    v_spent_purch    INTEGER;   -- positive magnitude taken from purchased
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
    -- plpgsql against these captured values, which is safe precisely because of this lock —
    -- no other transaction can move the row underneath us.
    SELECT used, purchased_used INTO v_old_used, v_old_pused
      FROM public.user_credits
     WHERE user_id = p_user_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NULL;  -- no credit row for this user (nothing to refund)
    END IF;

    -- Find the spend this refund reverses: the most recent debit of the SAME AMOUNT against
    -- the same ref_id. Refunds fire immediately after their charge (or from the reconciliation
    -- sweep for a specific report), so this identifies it even where a ref_id is per-session
    -- rather than per-request.
    --
    -- ⚠️ `delta = -p_amount` rather than `delta < 0` is a deliberate tightening, because
    -- ref_id is NOT unique per spend: ticker_report.py uses "{ticker}:{persona}" for every
    -- generation of that pair and chat.py uses the session id for every turn. Without the
    -- amount match, two spends sharing a ref_id that straddle the granted→purchased boundary
    -- let the FIRST one's refund adopt the SECOND one's split — returning granted credits to
    -- the purchased pool, which is precisely the laundering direction the header rules out.
    -- It does not make the match unique, but it removes the cross-cost case, and a miss is
    -- safe (it takes the granted-first fallback, which cannot mint permanent credits).
    IF p_ref_id IS NOT NULL AND p_ref_id <> '' THEN
        SELECT -granted_delta, -purchased_delta
          INTO v_spent_granted, v_spent_purch
          FROM public.credit_transactions
         WHERE user_id = p_user_id
           AND ref_id  = p_ref_id
           AND delta   = -p_amount
         ORDER BY id DESC
         LIMIT 1;

        -- A row written before migration 117 carries 0/0 with a non-zero delta: the split is
        -- unknown, not zero. Treat it as unknown and take the fallback.
        IF FOUND AND (COALESCE(v_spent_granted, 0) <> 0 OR COALESCE(v_spent_purch, 0) <> 0) THEN
            v_have_split := TRUE;
        END IF;
    END IF;

    IF v_have_split THEN
        -- Exact reversal, each side capped BOTH by what the original spend took from that pool
        -- and by what the pool currently shows as spent. The purchased cap is what makes
        -- laundering impossible — we can never return more to the permanent pool than the
        -- spend took from it. The granted cap is belt-and-braces: callers guarantee
        -- at-most-once, so a contract-violating double refund is not supposed to reach here,
        -- but capping it means such a bug under-refunds instead of minting.
        v_back_purch   := LEAST(GREATEST(v_spent_purch, 0), p_amount, v_old_pused);
        v_back_granted := LEAST(GREATEST(v_spent_granted, 0), p_amount - v_back_purch,
                                v_old_used);
    ELSE
        -- Unknown split: granted-first, overflowing to purchased only if granted cannot absorb
        -- it (otherwise the credits would simply vanish). Correct for pre-117 rows, whose
        -- spends were necessarily 100% granted, and the non-farmable direction in general.
        v_back_granted := LEAST(p_amount, v_old_used);
        v_back_purch   := LEAST(p_amount - v_back_granted, v_old_pused);
    END IF;

    IF v_back_granted + v_back_purch = 0 THEN
        -- Nothing to give back (already refunded, or the pools show nothing spent). Return the
        -- live balance without writing a zero-delta ledger row.
        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN v_spendable;
    END IF;

    -- Both terms are capped at the corresponding `*_used` above, so neither can go negative
    -- and the purchased_used <= purchased_total CHECK cannot be tripped.
    UPDATE public.user_credits
       SET used           = used - v_back_granted,
           purchased_used = purchased_used - v_back_purch,
           updated_at     = NOW()
     WHERE user_id = p_user_id
    RETURNING spendable INTO v_spendable;

    -- Ledger the ACTUAL movement, not the requested amount, so a (contract-forbidden)
    -- over-refund cannot desync sum(delta) from the balance. Same property migration 101 had.
    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
    VALUES
        (p_user_id, v_back_granted + v_back_purch, v_back_granted, v_back_purch,
         p_reason, p_ref_id, v_spendable);

    RETURN v_spendable;
END;
$$;

REVOKE ALL ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;


-- ── 3. Deprecate the pool-blind RPCs from migration 041 ──────────────────────────────────
-- `charge_user_credits` / `refund_user_credits` write `used` directly and know nothing about
-- the purchased pool, so calling either would let a user spend past their real balance or be
-- refunded into the wrong one. Nothing in the app calls them — `CreditService.try_charge` /
-- `.refund` are their only wrappers and no endpoint uses those (grep: comments and
-- tests/test_research_credits.py only). NOT dropped here, because dropping a function is
-- destructive and the tests still exercise the wrappers; the comment is the tripwire so the
-- next person does not wire them back in and reintroduce the bypass.
COMMENT ON FUNCTION public.charge_user_credits(UUID, INTEGER) IS
    'DEPRECATED (migration 118). Pool-blind: tests and mutates user_credits.used against '
    '`total` only and cannot see the purchased pool, so it will refuse a spend the user can '
    'afford and bypass the granted-first ordering. Use spend_credits. Unused by application '
    'code as of migration 118.';
COMMENT ON FUNCTION public.refund_user_credits(UUID, INTEGER) IS
    'DEPRECATED (migration 118). Pool-blind, and writes no ledger row and no granted/purchased '
    'split, so a later refund cannot be reversed exactly. Use refund_credits. Unused by '
    'application code as of migration 118.';

-- Both are SECURITY INVOKER, so migration 115's `REVOKE ALL ON public.user_credits FROM anon,
-- authenticated` already stops an anon-key holder from calling them (verified: `SET ROLE anon;
-- SELECT charge_user_credits(..., -100000)` -> "permission denied for table user_credits").
-- Revoking EXECUTE too makes that defence local to the function rather than two migrations
-- away, so it survives anyone re-granting the table.
REVOKE ALL ON FUNCTION public.charge_user_credits(UUID, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.refund_user_credits(UUID, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.charge_user_credits(UUID, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.refund_user_credits(UUID, INTEGER) TO service_role;

COMMIT;
