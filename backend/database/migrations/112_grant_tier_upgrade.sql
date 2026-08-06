-- 112_grant_tier_upgrade.sql
--
-- Why: A MID-MONTH UPGRADE CURRENTLY DELIVERS ZERO CREDITS. The buyer pays and gets nothing.
--
-- `IAPService.reconcile_user_tier` mirrors the purchased tier onto `users.tier` and then calls
-- `ensure_credit_period` under the comment "Grant the tier's allocation". But that function only
-- grants when the *period boundary* has passed (`112`'s predecessor, migration 100, line 224):
--
--     IF v_row.resets_at IS NULL OR now() >= v_row.resets_at THEN   -- grant
--     ...
--     -- Not due: return the live remaining unchanged.
--     RETURN (v_row.total - v_row.used);
--
-- So the sequence that matters is: user signs up on the 3rd (first credit touch stamps
-- `resets_at` = 1st of next month, total = 50), spends all 50 by the 10th, hits 402, buys Pro
-- for $14.99. `users.tier` becomes 'pro', `ensure_credit_period` finds `now() < resets_at`,
-- falls to the "not due" branch, and returns the balance UNCHANGED. Their very next tap is
-- another 402 — for up to four weeks. Pro -> Max is identical.
--
-- The person most likely to upgrade is someone who just ran out, which is exactly the person
-- this hits. Verified against the live deployed function (schema_snapshot.sql:1194), not just
-- the migration: no later CREATE OR REPLACE added a tier-change branch, and no other code path
-- writes `user_credits.total`.
--
-- Fix: a separate RPC that grants on the tier TRANSITION rather than on the calendar.
--
-- Design notes (the three things that make this safe):
--
--   1. IDEMPOTENT BY CONSTRUCTION. The grant is gated on `v_alloc > v_row.total`, so a replay
--      — and `Transaction.updates` replays on every app launch — finds `total` already equal to
--      the allocation and does nothing. No transaction-id bookkeeping needed, and no way to farm
--      credits by relaunching the app. This is the single most important property here: the
--      previous credit bug class in this codebase was double-granting, and a naive
--      "grant on every reconcile" fix would reintroduce it.
--
--   2. NEVER CLAWS BACK. `GREATEST(total, v_alloc)` means a DOWNGRADE is a no-op: a user who
--      drops from Max to Pro keeps the credits they already paid for until the next monthly
--      reset lowers the allocation naturally. Making `ensure_credit_period` itself fire on
--      `total <> v_alloc` would have deleted credits mid-period, which is why this is a
--      separate function rather than a loosened condition on the existing one.
--
--   3. `used` IS PRESERVED, NOT ZEROED. Semantics are "this tier grants N credits per period",
--      so a Free user who spent 50 and upgrades to Pro ends the month having had 1200 available,
--      not 1250. Zeroing `used` instead would be more generous and is a one-word change
--      (`used = 0`) if that is the product call — but it makes the period total depend on when
--      in the month you upgrade, which is harder to explain on a receipt.
--
-- Safe to apply any time, and safe to apply BEFORE the code that calls it: nothing invokes
-- `grant_tier_upgrade` until the matching `iap_service.py` change is deployed, and applying it
-- alone changes no behaviour. Applying the CODE without this migration is the ordering that
-- degrades — the RPC call fails, is logged, and credits simply land on the next monthly reset
-- exactly as they do today.

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
    -- Never touch the shared guest bucket. It is a fixed sentinel (seeded 100000 by
    -- 046_seed_guest_user.sql) that keeps the signed-out app from being walled out; it is
    -- not a balance, is never metered, and must not be reset. Same guard as
    -- ensure_credit_period.
    IF p_user_id = _GUEST THEN
        RETURN 0;
    END IF;

    -- Resolve tier -> monthly allocation. `reconcile_user_tier` has already mirrored the
    -- winning tier onto users.tier before calling this, so this reads the NEW tier.
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

    -- Lock the balance row so a concurrent verify + webhook for the same purchase cannot
    -- both observe the pre-grant total and both raise it.
    SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;

    IF NOT FOUND THEN
        -- No balance row yet (upgrade before the first credit read). Create it at the new
        -- tier's allocation. ON CONFLICT DO NOTHING mirrors ensure_credit_period: FOR UPDATE
        -- cannot lock a row that does not exist, so two first-touch calls can both land here.
        INSERT INTO public.user_credits (user_id, total, used, resets_at, updated_at)
        VALUES (p_user_id, v_alloc, 0, v_next_reset, NOW())
        ON CONFLICT (user_id) DO NOTHING;

        IF FOUND THEN
            INSERT INTO public.credit_transactions
                (user_id, delta, reason, ref_id, balance_after)
            VALUES (p_user_id, v_alloc, 'tier_upgrade',
                    to_char(v_now_et, 'YYYY-MM'), v_alloc);
            RETURN v_alloc;
        END IF;

        SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 0;
        END IF;
    END IF;

    -- The whole fix, and the whole replay guard, is this comparison. An upgrade raises the
    -- ceiling; a replay or a downgrade finds nothing to do.
    IF v_alloc <= v_row.total THEN
        RETURN (v_row.total - v_row.used);
    END IF;

    v_delta := v_alloc - v_row.total;

    UPDATE public.user_credits
       SET total      = v_alloc,
           updated_at = NOW()
     WHERE user_id = p_user_id;

    INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
    VALUES (p_user_id, v_delta, 'tier_upgrade',
            to_char(v_now_et, 'YYYY-MM'), v_alloc - v_row.used);

    RETURN (v_alloc - v_row.used);
END;
$$;

-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by default and Supabase exposes every public
-- function at POST /rest/v1/rpc/<name>. Without the REVOKE, a holder of the shipped anon key
-- could call this directly — and while the tier lookup means it can only ever grant what the
-- caller's OWN tier already entitles them to, an unauthenticated RPC that writes
-- `user_credits` has no business being reachable. REVOKE precedes GRANT; both idempotent.
REVOKE ALL ON FUNCTION public.grant_tier_upgrade(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.grant_tier_upgrade(UUID) TO service_role;
