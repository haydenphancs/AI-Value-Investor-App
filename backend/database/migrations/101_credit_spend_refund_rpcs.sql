-- 101_credit_spend_refund_rpcs.sql
--
-- Why: unified credit ENFORCEMENT across every AI action (chat = 1 credit, report =
-- 20 credits). Migration 100 added the data layer (plan_credits, subscriptions,
-- credit_transactions, ensure_credit_period, add_credit_transaction) but the charge
-- path still used the migration-041 `charge_user_credits`/`refund_user_credits` RPCs,
-- which (a) don't roll the monthly allocation and (b) don't write the audit ledger.
--
-- This adds two atomic, ledger-writing RPCs so a charge (or refund) is ONE round-trip
-- and the ledger row lands in the SAME transaction as the balance mutation (no
-- best-effort gap):
--
--   * spend_credits(user, amount, reason, ref_id)  — reset-if-due (ensure_credit_period)
--       then atomic check-and-debit then ledger insert. Returns the new remaining, or
--       NULL when the balance is insufficient (caller maps NULL → HTTP 402). Nothing is
--       debited on the insufficient path (single guarded UPDATE, no race window).
--   * refund_credits(user, amount, reason, ref_id) — refund (floored at 0) + ledger.
--       NOT idempotent; callers guarantee at-most-once (chat: a once-only settlement
--       flag; reports: the research_reports.is_refunded compare-and-set).
--
-- GUEST GUARD: both RPCs early-return for the shared guest sentinel WITHOUT mutating.
-- Guests are never credit-metered — the single shared GUEST_USER_ID row would deplete
-- for ALL guests and ensure_credit_period never refills it. The service layer also
-- guest-skips (primary); this SQL guard is defense-in-depth against a stray call.
--
-- Mirrors the SECURITY DEFINER + REVOKE-before-GRANT lockdown of migrations 096/097/100
-- (Supabase exposes every public function at POST /rest/v1/rpc/<name>; without the
-- REVOKE an anon-key holder could self-refund). Safe to re-run: CREATE OR REPLACE.

BEGIN;

-- ── spend_credits: reset-if-due + atomic charge + ledger, one transaction ───────────
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
    v_remaining INTEGER;
BEGIN
    -- Guests are never metered (shared pool; never refilled). Return current remaining
    -- (non-NULL = "ok, not charged") without debiting.
    IF p_user_id = _GUEST THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, 0);
    END IF;

    -- Non-positive charge is a caller/sign bug — no-op (a negative amount would MINT
    -- credits via the predicate below). Return current remaining, no debit, no ledger row.
    IF p_amount IS NULL OR p_amount <= 0 THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, 0);
    END IF;

    -- Roll into the current month's tier allocation first (no-op mid-month). This takes
    -- SELECT ... FOR UPDATE on the single user_credits row and holds it for this whole
    -- transaction, so the charge UPDATE below serializes with concurrent spends.
    PERFORM public.ensure_credit_period(p_user_id);

    -- Atomic check-and-debit (mirrors charge_user_credits, migration 041): the
    -- (total - used) >= amount predicate means no row mutates when the balance is
    -- insufficient — no race window, never negative. `remaining` is a GENERATED column,
    -- so we only ever write `used`.
    UPDATE public.user_credits
       SET used       = used + p_amount,
           updated_at = NOW()
     WHERE user_id   = p_user_id
       AND (total - used) >= p_amount
    RETURNING (total - used) INTO v_remaining;

    IF v_remaining IS NULL THEN
        RETURN NULL;  -- insufficient (caller → 402); nothing debited, no ledger row
    END IF;

    -- Ledger the spend in the SAME transaction (guaranteed, not best-effort).
    INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
    VALUES (p_user_id, -p_amount, p_reason, p_ref_id, v_remaining);

    RETURN v_remaining;
END;
$$;

REVOKE ALL ON FUNCTION public.spend_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.spend_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;


-- ── refund_credits: refund (floored) + ledger, one transaction ──────────────────────
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
    v_remaining INTEGER;
    v_delta     INTEGER;
BEGIN
    IF p_user_id = _GUEST THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, 0);
    END IF;

    -- Non-positive refund is a caller/sign bug — no-op (a negative amount would DEBIT).
    IF p_amount IS NULL OR p_amount <= 0 THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN v_remaining;
    END IF;

    -- Refund, flooring `used` at 0 (a stray double-refund can't drive it negative and
    -- silently grant credits). NOT idempotent — callers guarantee at-most-once. The ledger
    -- records the ACTUAL change (old_used - new_used = LEAST(old_used, p_amount)), NOT the
    -- requested amount, so a (contract-forbidden) over-refund can't desync sum(delta) from
    -- the balance. The CTE's SELECT ... FOR UPDATE captures old_used and serializes
    -- concurrent refunds on the single (user_id-unique) row.
    WITH prev AS (
        SELECT used AS old_used FROM public.user_credits
         WHERE user_id = p_user_id FOR UPDATE
    )
    UPDATE public.user_credits u
       SET used       = GREATEST(0, u.used - p_amount),
           updated_at = NOW()
      FROM prev
     WHERE u.user_id = p_user_id
    RETURNING (u.total - u.used), (prev.old_used - u.used)
      INTO v_remaining, v_delta;

    IF v_remaining IS NULL THEN
        RETURN NULL;  -- no credit row for this user (nothing to refund)
    END IF;

    INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
    VALUES (p_user_id, v_delta, p_reason, p_ref_id, v_remaining);

    RETURN v_remaining;
END;
$$;

REVOKE ALL ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refund_credits(UUID, INTEGER, TEXT, TEXT) TO service_role;

COMMIT;
