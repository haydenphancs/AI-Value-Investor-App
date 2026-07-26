-- 100_credit_tiers_and_monthly_allocation.sql
--
-- Why: we are introducing Free / Pro / Max subscription tiers with MONTHLY credit
-- allocations (unified pool: 1 chat = 1 credit, 1 report = 20 credits). Today credits
-- are a fixed, one-time-seeded balance on `user_credits` that only ever decreases —
-- there is no periodic replenishment (`resets_at` was scaffolded but never written).
-- This migration adds the DATA LAYER that a later ENFORCEMENT phase wires in:
--
--   1. plan_credits          — tier -> monthly credit allocation + price (config,
--                              tunable without a deploy). Source of truth for how many
--                              credits each tier gets per month.
--   2. subscriptions         — entitlement source of truth (App Store / Stripe). A
--                              server-side receipt validator / webhook updates it and
--                              mirrors the winning tier onto users.tier.
--   3. credit_transactions   — append-only audit ledger (grants, monthly resets,
--                              charges, refunds). Appropriate for a fintech product.
--   4. ensure_credit_period  — LAZY monthly reset RPC on the EXISTING user_credits row.
--                              Chosen over a new bucket table so the existing
--                              charge_user_credits / refund_user_credits RPCs (mig 041),
--                              the is_refunded reconciliation, and the iOS credits UI all
--                              keep working verbatim. First call each month HARD-RESETS
--                              the balance to the tier allocation (use-it-or-lose-it —
--                              unused prior-month credits do NOT roll over).
--   5. add_credit_transaction — locked-down helper so the service layer can append
--                              charge/refund rows to the ledger.
--
-- DELIBERATELY NOT TOUCHED HERE (these are AUTH-phase cleanups; changing them now would
-- alter the LIVE guest-only app):
--   * the 100k guest credit grant (guests still rely on it while login is off),
--   * the conflicting signup seeds (handle_new_auth_user = 50 vs create_user_credits
--     by-tier 3/25/100).
-- ensure_credit_period is NOT called by any endpoint yet, AND it SKIPS the guest
-- sentinel, so applying this migration changes no runtime behaviour on its own.
--
-- "Max" reuses the existing user_tier value 'premium' (display_name = 'Max') to avoid an
-- enum change. Safe to re-run: every statement is idempotent.

BEGIN;

-- ── 1. plan_credits: tier -> monthly allocation + storefront price ───────────────────
CREATE TABLE IF NOT EXISTS public.plan_credits (
    tier            public.user_tier PRIMARY KEY,
    monthly_credits INTEGER NOT NULL,
    price_cents     INTEGER NOT NULL DEFAULT 0,   -- USD cents, storefront display only
    display_name    TEXT    NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT plan_credits_monthly_nonneg CHECK (monthly_credits >= 0),
    CONSTRAINT plan_credits_price_nonneg   CHECK (price_cents >= 0)
);

-- Seed / re-sync the approved allocations. ON CONFLICT DO UPDATE so re-running the
-- migration re-syncs the numbers (this is config, not user data).
INSERT INTO public.plan_credits (tier, monthly_credits, price_cents, display_name) VALUES
    ('free',      50, 0,    'Free'),
    ('pro',     1200, 1499, 'Pro'),
    ('premium', 4000, 3999, 'Max')
ON CONFLICT (tier) DO UPDATE
    SET monthly_credits = EXCLUDED.monthly_credits,
        price_cents     = EXCLUDED.price_cents,
        display_name    = EXCLUDED.display_name,
        updated_at      = NOW();

ALTER TABLE public.plan_credits ENABLE ROW LEVEL SECURITY;

-- Pricing/allocation config is non-sensitive (shown on the paywall / App Store), so
-- clients may read it; only the service role writes it.
DROP POLICY IF EXISTS "plan_credits_public_read" ON public.plan_credits;
CREATE POLICY "plan_credits_public_read" ON public.plan_credits
    FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "plan_credits_service_all" ON public.plan_credits;
CREATE POLICY "plan_credits_service_all" ON public.plan_credits
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON public.plan_credits TO anon, authenticated;
GRANT ALL    ON public.plan_credits TO service_role;


-- ── 2. subscriptions: entitlement source of truth ───────────────────────────────────
CREATE TABLE IF NOT EXISTS public.subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL UNIQUE
                                REFERENCES public.users(id) ON DELETE CASCADE,
    tier                    public.user_tier NOT NULL DEFAULT 'free',
    status                  TEXT NOT NULL DEFAULT 'active',  -- active|grace|expired|canceled
    store                   TEXT,                            -- apple|stripe|promo
    original_transaction_id TEXT,                            -- store dedup / restore key
    current_period_end      TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Expiry sweeps scan (status, current_period_end); store restores look up by txn id.
CREATE INDEX IF NOT EXISTS idx_subscriptions_expiry
    ON public.subscriptions (status, current_period_end);
CREATE INDEX IF NOT EXISTS idx_subscriptions_txn
    ON public.subscriptions (original_transaction_id);

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

-- A user may READ their own subscription; only the service role WRITES it (receipt
-- validation / webhooks run server-side — clients must never self-assign a tier).
DROP POLICY IF EXISTS "subscriptions_select_own" ON public.subscriptions;
CREATE POLICY "subscriptions_select_own" ON public.subscriptions
    FOR SELECT TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "subscriptions_service_all" ON public.subscriptions;
CREATE POLICY "subscriptions_service_all" ON public.subscriptions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON public.subscriptions TO authenticated;
GRANT ALL    ON public.subscriptions TO service_role;


-- ── 3. credit_transactions: append-only audit ledger ────────────────────────────────
CREATE TABLE IF NOT EXISTS public.credit_transactions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       UUID NOT NULL,
    delta         INTEGER NOT NULL,   -- signed: -20 report charge, +N grant/refund/reset
    reason        TEXT NOT NULL,      -- monthly_reset|report_charge|chat_charge|refund|grant
    ref_id        TEXT,               -- report_id / chat session / period key (YYYY-MM)
    balance_after INTEGER,            -- remaining after this txn (best-effort snapshot)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credit_transactions_user
    ON public.credit_transactions (user_id, created_at DESC);

ALTER TABLE public.credit_transactions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "credit_transactions_select_own" ON public.credit_transactions;
CREATE POLICY "credit_transactions_select_own" ON public.credit_transactions
    FOR SELECT TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "credit_transactions_service_all" ON public.credit_transactions;
CREATE POLICY "credit_transactions_service_all" ON public.credit_transactions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON public.credit_transactions TO authenticated;
GRANT ALL    ON public.credit_transactions TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.credit_transactions_id_seq TO service_role;


-- ── 3b. Align research_reports.credits_charged DEFAULT to the unified report cost ────
-- Migration 041 set this DEFAULT to 5 (the old example cost). The app always stamps
-- credits_charged explicitly on insert (research.py), so the column default is inert
-- today; aligning it to 20 keeps a future insert that omits the column from recording a
-- stale 5. Metadata-only + idempotent (SET DEFAULT).
ALTER TABLE public.research_reports
    ALTER COLUMN credits_charged SET DEFAULT 20;


-- ── 4. ensure_credit_period: lazy monthly allocation reset ──────────────────────────
-- HARD-RESETS user_credits to the tier's monthly allocation (total = alloc, used = 0 —
-- use-it-or-lose-it; unused prior-month credits do NOT roll over) the first time it is
-- called in a new calendar month (ET, matching the app's trading-day convention — mig
-- 089/096). Returns the current `remaining`. Idempotent within a month (only resets when due).
-- SKIPS the shared guest sentinel so the live guest-only app keeps its fixed grant.
-- SECURITY DEFINER + row_security = off so the service role can top up regardless of RLS;
-- the balance row is locked FOR UPDATE so concurrent turns can't double-reset.
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
    v_now_et     TIMESTAMP;      -- ET wall-clock (no tz)
    v_next_reset TIMESTAMPTZ;
    v_row        public.user_credits%ROWTYPE;
    v_remaining  INTEGER;
BEGIN
    -- Never reset the shared guest bucket (protects the live guest app's fixed balance).
    IF p_user_id = _GUEST THEN
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, 0);
    END IF;

    -- Resolve tier -> monthly allocation (fall back to 'free' / 0 if unknown).
    SELECT u.tier INTO v_tier FROM public.users u WHERE u.id = p_user_id;
    IF v_tier IS NULL THEN
        v_tier := 'free';
    END IF;
    SELECT monthly_credits INTO v_alloc FROM public.plan_credits WHERE tier = v_tier;
    IF v_alloc IS NULL THEN
        v_alloc := 0;
    END IF;

    -- Next month boundary in ET, as a timestamptz.
    v_now_et     := (now() AT TIME ZONE 'America/New_York');
    v_next_reset := (date_trunc('month', v_now_et) + INTERVAL '1 month')
                        AT TIME ZONE 'America/New_York';

    -- Lock (or create) the balance row.
    SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;

    IF NOT FOUND THEN
        -- FOR UPDATE cannot lock a row that does not exist yet, so two concurrent
        -- first-touch calls could both reach here. ON CONFLICT DO NOTHING makes the
        -- loser a no-op instead of a unique_violation on user_credits_user_id_key.
        INSERT INTO public.user_credits (user_id, total, used, resets_at, updated_at)
        VALUES (p_user_id, v_alloc, 0, v_next_reset, NOW())
        ON CONFLICT (user_id) DO NOTHING;

        IF FOUND THEN
            -- We created the row: log the initial grant exactly once.
            INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
            VALUES (p_user_id, v_alloc, 'grant', to_char(v_now_et, 'YYYY-MM'), v_alloc);
            RETURN v_alloc;
        END IF;

        -- A concurrent call created it first; return the live remaining (no grant log).
        SELECT (total - used) INTO v_remaining
          FROM public.user_credits WHERE user_id = p_user_id;
        RETURN COALESCE(v_remaining, v_alloc);
    END IF;

    -- Due for reset? (never set, or the stored boundary has passed.)
    IF v_row.resets_at IS NULL OR now() >= v_row.resets_at THEN
        UPDATE public.user_credits
           SET total      = v_alloc,
               used       = 0,
               resets_at  = v_next_reset,
               updated_at = NOW()
         WHERE user_id = p_user_id;
        INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
        VALUES (p_user_id, v_alloc, 'monthly_reset', to_char(v_now_et, 'YYYY-MM'), v_alloc);
        RETURN v_alloc;
    END IF;

    -- Not due: return the live remaining unchanged.
    RETURN (v_row.total - v_row.used);
END;
$$;

-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by default and Supabase exposes every
-- public function at POST /rest/v1/rpc/<name>; without the REVOKE a holder of the anon key
-- could loop ensure_credit_period to force a victim's balance back to full. REVOKE precedes
-- GRANT; both idempotent.
REVOKE ALL ON FUNCTION public.ensure_credit_period(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ensure_credit_period(UUID) TO service_role;


-- ── 5. add_credit_transaction: locked-down ledger append helper ─────────────────────
CREATE OR REPLACE FUNCTION public.add_credit_transaction(
    p_user_id       UUID,
    p_delta         INTEGER,
    p_reason        TEXT,
    p_ref_id        TEXT,
    p_balance_after INTEGER
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
    VALUES (p_user_id, p_delta, p_reason, p_ref_id, p_balance_after)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

REVOKE ALL ON FUNCTION public.add_credit_transaction(UUID, INTEGER, TEXT, TEXT, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.add_credit_transaction(UUID, INTEGER, TEXT, TEXT, INTEGER) TO service_role;

COMMIT;
