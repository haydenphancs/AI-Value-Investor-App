-- 114_revoke_tier_credits_and_event_ordering.sql
--
-- Two independent App Store entitlement fixes. Bundled because both are small, both are in the
-- purchase path, and applying them separately buys nothing.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- PART 1 — A REFUND DROPS THE TIER BUT LEAVES THE CREDITS SPENDABLE
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- `grant_tier_upgrade` (migration 112) deliberately NEVER lowers `user_credits.total`:
--
--     IF v_alloc <= v_row.total THEN
--         RETURN (v_row.total - v_row.used);      -- a downgrade is a no-op
--
-- That is correct for a VOLUNTARY downgrade — migration 112 §2 argues it at length: a user who
-- drops Max -> Pro keeps what they already paid for until the next monthly reset lowers the
-- allocation naturally. Clawing back mid-period would delete credits someone bought.
--
-- Revocation is not a voluntary downgrade, and it routes through the same code:
--
--     apply_notification(REFUND/REVOKE)
--       -> apply_transaction(status_override='revoked')
--         -> reconcile_user_tier()
--           -> grant_tier_upgrade()      <- no-op, because 0 < total
--
-- So a Max subscriber who refunds keeps 4000 credits — roughly 200 AI reports at ~17 Gemini +
-- ~20 FMP calls each — for the rest of the month, having paid nothing. The tier flips to free
-- immediately, which makes it look handled; only the balance betrays it. This is the one
-- direction where a claw-back is right: the money went back.
--
-- `used` IS RESPECTED. `total` floors at GREATEST(free_allocation, used) so the row can never
-- express "you have spent more than you were ever given" — `remaining` is a GENERATED column
-- (total - used) and would otherwise go negative, which no caller expects and which would make
-- every subsequent charge fail a CHECK rather than a balance test. Credits already spent before
-- the refund are gone; that is a business loss, not a data-integrity problem to model.
--
-- Idempotent by construction, like 112: it lowers toward a floor, so a replayed REFUND
-- notification (Apple retries up to 5 times over ~8 hours) finds `total` already at the floor
-- and does nothing.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- PART 2 — OUT-OF-ORDER NOTIFICATIONS SILENTLY DEMOTE A PAYING SUBSCRIBER
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- `IAPService.apply_transaction` blind-overwrites tier/status/current_period_end with whatever
-- transaction arrived last. Apple guarantees NO ordering and retries aggressively, so a
-- redelivered EXPIRED can land after the DID_RENEW that superseded it — and the renewing
-- customer drops to free. Symmetrically, a late DID_RENEW after a REFUND can re-entitle
-- someone who was refunded.
--
-- `last_event_at` stores Apple's `signedDate` — WHEN APPLE SAID IT — so a delivery older than
-- the one already applied can be ignored.
--
-- ⚠️ It must be `signedDate`, NOT `expiresDate`. They disagree in exactly the case that
-- matters: an EXPIRED notification is signed LATE and carries an EARLY expiresDate, because it
-- describes the period that just ended. Ordering on expiresDate classifies every ordinary
-- expiry as stale and keeps lapsed subscribers on a paid tier — the opposite bug, and the more
-- expensive one. (`tests/test_iap_entitlement.py::test_expiry_notification_drops_the_tier`
-- catches precisely that, and did.)
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- APPLY ORDER — safe in either direction, unlike migrations 108/110/111
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- Migration BEFORE code: nothing calls `revoke_tier_credits` and nothing writes
-- `last_event_at`, so applying this alone changes no behaviour.
--
-- Code BEFORE migration: both degrade to today's behaviour rather than failing.
--   * `revoke_tier_credits` missing -> the RPC errors, is logged (PGRST202), and the tier still
--     drops. Credits self-correct at the next monthly reset.
--   * `last_event_at` missing -> `apply_transaction` retries its write without the column (see
--     `_EVENT_AT_COLUMN` in iap_service.py) and the ordering guard stays inert. A purchase
--     Apple has already charged for must never be rejected to protect an optimisation.
--
-- Neither is the 108/110/111 shape where deploying code first breaks every INSERT.

-- ── PART 2: ordering key ─────────────────────────────────────────────────────────────────

ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMPTZ;

COMMENT ON COLUMN public.subscriptions.last_event_at IS
    'Apple''s signedDate from the most recently APPLIED App Store Server Notification. The '
    'ordering key for out-of-order redeliveries — NOT expiresDate, which describes which '
    'period a transaction is about rather than when Apple asserted it. NULL for rows last '
    'written by the client verify path, which carries no signedDate. See migration 114.';

-- No index: every read of this column is already keyed by `original_transaction_id` or
-- `user_id` (both unique), so the row is located before this value is ever compared.

-- ── PART 1: revoke credits on refund ─────────────────────────────────────────────────────

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
    v_delta     INTEGER;
    v_now_et    TIMESTAMP;
BEGIN
    -- Same guard as grant_tier_upgrade / ensure_credit_period: the shared guest bucket is a
    -- fixed sentinel seeded 100000 by 046_seed_guest_user.sql, not a balance. Never meter it.
    IF p_user_id = _GUEST THEN
        RETURN 0;
    END IF;

    SELECT monthly_credits INTO v_free FROM public.plan_credits WHERE tier = 'free';
    IF v_free IS NULL THEN
        v_free := 0;
    END IF;

    -- Lock so a concurrent REFUND retry and a verify cannot both read the pre-revoke total.
    SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        -- No balance row: nothing was ever granted, so nothing to take back.
        RETURN 0;
    END IF;

    -- Floor at `used` so `remaining` (a GENERATED column, total - used) can never go negative.
    -- Spent credits are not recoverable; this only reclaims the UNSPENT balance.
    v_floor := GREATEST(v_free, v_row.used);

    -- Idempotent: a replayed REFUND finds total already at the floor.
    IF v_row.total <= v_floor THEN
        RETURN (v_row.total - v_row.used);
    END IF;

    v_delta  := v_floor - v_row.total;          -- negative
    v_now_et := (now() AT TIME ZONE 'America/New_York');

    UPDATE public.user_credits
       SET total      = v_floor,
           updated_at = NOW()
     WHERE user_id = p_user_id;

    INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
    VALUES (p_user_id, v_delta, 'tier_revoked',
            to_char(v_now_et, 'YYYY-MM'), v_floor - v_row.used);

    RETURN (v_floor - v_row.used);
END;
$$;

-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by default and Supabase exposes every public
-- function at POST /rest/v1/rpc/<name>. This one only ever LOWERS a balance, so an anon caller
-- could not enrich themselves — but they could grief any account whose uuid they hold. Same
-- REVOKE-then-GRANT as migration 112; both idempotent.
REVOKE ALL ON FUNCTION public.revoke_tier_credits(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_tier_credits(UUID) TO service_role;
