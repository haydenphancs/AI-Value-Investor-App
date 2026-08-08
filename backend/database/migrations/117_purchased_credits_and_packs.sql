-- 117_purchased_credits_and_packs.sql
--
-- Why: we are adding "Buy Credits" — one-off credit packs sold as Apple CONSUMABLE in-app
-- purchases. Today there is no way to add credits to an account that survives, and the
-- reason is structural rather than a missing endpoint.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- PART 0 — WHY PURCHASED CREDITS CANNOT LIVE IN `user_credits.total`
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- App Store Review Guideline 3.1.1: "Any credits or in-game currencies purchased via in-app
-- purchase may not expire, and you should make sure you have a restore mechanism for any
-- restorable in-app purchases."
--
-- THREE shipped RPCs write `user_credits.total`, and every one of them mishandles a balance
-- someone paid cash for:
--
--   1. ensure_credit_period (migration 100, lines 225-230)
--        UPDATE user_credits SET total = v_alloc, used = 0 ...
--      A hard reset at each ET month boundary. Purchased credits would be DELETED on the 1st.
--      That is the guideline violation, and it is real money.
--
--   2. grant_tier_upgrade (migration 112, lines 121-123)
--        IF v_alloc <= v_row.total THEN RETURN (v_row.total - v_row.used);   -- no-op
--      The replay guard is "the allocation is already at or below the current total". A user
--      holding the 1,200-credit Mega pack would sit at total = 1,250, so buying Pro (alloc
--      1,200) takes the no-op branch and grants NOTHING. They pay $14.99 and receive zero
--      credits — and migration 112 exists precisely because that bug already shipped once.
--
--   3. revoke_tier_credits (migration 114, lines 128-141)
--        v_floor := GREATEST(v_free, v_row.used);  total := v_floor
--      Correct claw-back for a refunded SUBSCRIPTION. But it floors the single shared `total`,
--      so refunding a $14.99 subscription would also erase a separately-purchased $19.99 pack
--      the user still owns.
--
-- Fix: a SECOND POOL in the same row. `purchased_total` / `purchased_used` are touched by
-- NOTHING above — all three functions read and write only `total`/`used`, and their
-- `SELECT * INTO v_row ... %ROWTYPE` absorbs new columns without change — so purchased
-- credits are immune to the monthly wipe, the upgrade no-op and the subscription claw-back
-- BY CONSTRUCTION, with zero edits to three functions that are load-bearing and already carry
-- two rounds of bug fixes. That is the reason for two columns rather than one.
--
-- `remaining` (GENERATED, total - used) keeps its EXACT current meaning — granted only — so
-- every existing SQL reader stays correct. `spendable` is the real balance.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- PART 1 — WHY THE LEDGER NOW RECORDS A SPLIT (granted_delta / purchased_delta)
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- Spending drains GRANTED first, so the never-expiring pool is preserved as long as possible.
-- Refunding is NOT the mirror of that, and the two must never be "made consistent":
--
--   * Refund PURCHASED-first is a laundering loop. Granted 50 unspent, purchased 100 fully
--     spent. A 20-credit report drains granted (used 0->20) and fails. A purchased-first
--     refund returns the 20 to the purchased pool — the user just converted 20 EXPIRING
--     credits into 20 PERMANENT ones for free, repeatable on any user-inducible failure,
--     draining the whole monthly allocation into the permanent pool every month.
--
--   * Refund GRANTED-first is worse. It converts purchased -> granted, and
--     ensure_credit_period then wipes them on the 1st. That literally expires purchased
--     credits: the 3.1.1 violation this whole migration exists to prevent.
--
-- The only correct rule is to reverse the EXACT split of the original spend, so the ledger has
-- to carry it. `delta` keeps its existing meaning (the signed total) and always equals
-- granted_delta + purchased_delta, so existing ledger readers are unaffected.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- PART 2 — WHY THE EXISTING /billing/verify IDEMPOTENCY DOES NOT TRANSFER
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- billing.py:66-69 documents why replays are harmless today:
--
--     "Entitlement is keyed on `originalTransactionId`, and credits come from the monthly
--      allocation rather than per-delivery, so a replay cannot mint credits."
--
-- Both halves fail for a consumable. A pack MUST mint credits per delivery, and StoreKit
-- replays `Transaction.updates` on EVERY app launch, so the same purchase arrives many times.
-- `originalTransactionId` is also the wrong key: for consumables each purchase mints a fresh
-- `transactionId`, and a user buying the same pack twice is two grants, not a replay. Reusing
-- apply_transaction's originalTransactionId coalescing would collapse ten purchases into one.
--
-- So the grant is gated on `INSERT ... ON CONFLICT DO NOTHING` against
-- `credit_purchases (environment, transaction_id)` INSIDE the same transaction as the balance
-- update. The conflict IS the idempotency — there is no read-then-write window a concurrent
-- redelivery could slip through, which a "SELECT then INSERT" would have.
--
-- `environment` is in the key because Sandbox and Production transaction-id spaces are not
-- guaranteed disjoint: without it, a tester's sandbox id can permanently block a real
-- production purchase from ever being granted. It is NOT NULL with a default because
-- app_store.py `_to_dict` (:355-360) DROPS None values, so the caller may not receive one —
-- a NULL here would silently stop the unique index from deduping at all.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────
-- APPLY ORDER — this migration is INERT
-- ─────────────────────────────────────────────────────────────────────────────────────────
--
-- Nothing calls add_purchased_credits / revoke_purchased_credits yet, nothing reads
-- credit_packs yet, the new ledger columns default to 0, and the new balance columns default
-- to 0 so `spendable` equals `remaining` for every existing row. Applying this alone changes
-- no behaviour. Migration 118 (two-pool spend/refund) is what goes live with the code, and it
-- REFUSES to apply unless this one already has.
--
-- Applying the CODE before this migration is the bad direction: every pack grant fails with
-- PGRST202 AFTER Apple has already charged the user. It self-heals (the client never calls
-- Transaction.finish(), so StoreKit redelivers) but do not rely on that. Apply first.
--
-- After applying, PostgREST may need `NOTIFY pgrst, 'reload schema';` before the new columns
-- and functions are visible over the REST API.
--
-- Safe to re-run: every statement is idempotent.

BEGIN;

-- ── 1. user_credits: the second pool ─────────────────────────────────────────────────────

ALTER TABLE public.user_credits
    ADD COLUMN IF NOT EXISTS purchased_total INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.user_credits
    ADD COLUMN IF NOT EXISTS purchased_used  INTEGER NOT NULL DEFAULT 0;

-- Postgres has no ADD CONSTRAINT IF NOT EXISTS; DROP-then-ADD is the idempotent form.
ALTER TABLE public.user_credits
    DROP CONSTRAINT IF EXISTS user_credits_purchased_total_nonneg;
ALTER TABLE public.user_credits
    ADD  CONSTRAINT user_credits_purchased_total_nonneg CHECK (purchased_total >= 0);

ALTER TABLE public.user_credits
    DROP CONSTRAINT IF EXISTS user_credits_purchased_used_nonneg;
ALTER TABLE public.user_credits
    ADD  CONSTRAINT user_credits_purchased_used_nonneg CHECK (purchased_used >= 0);

-- The tripwire that protects `spendable`. Every writer already maintains this — spend_credits
-- only overflows into purchased_used up to what is available, and revoke_purchased_credits
-- floors at purchased_used — so a violation means a new writer got the arithmetic wrong.
-- Failing the write is enormously better than a silently negative balance, because
-- `spendable` is generated and a negative would make every later charge fail a test it should
-- have passed.
ALTER TABLE public.user_credits
    DROP CONSTRAINT IF EXISTS user_credits_purchased_used_le_total;
ALTER TABLE public.user_credits
    ADD  CONSTRAINT user_credits_purchased_used_le_total CHECK (purchased_used <= purchased_total);

-- The number every spend decision should actually use. A generated column may reference only
-- BASE columns of the same row — it CANNOT read `remaining`, which is itself generated — so
-- this recomputes the granted half rather than reusing it. `remaining + (...)` is rejected at
-- apply time; do not "simplify" it back.
ALTER TABLE public.user_credits
    ADD COLUMN IF NOT EXISTS spendable INTEGER
        GENERATED ALWAYS AS ((total - used) + (purchased_total - purchased_used)) STORED;

COMMENT ON COLUMN public.user_credits.purchased_total IS
    'Lifetime credits granted from CONSUMABLE in-app purchases (credit packs). NEVER reset by '
    'ensure_credit_period / grant_tier_upgrade / revoke_tier_credits — App Store Guideline '
    '3.1.1 forbids purchased credits expiring. Only add_purchased_credits raises it and only '
    'revoke_purchased_credits (Apple REFUND) lowers it. See migration 117.';

COMMENT ON COLUMN public.user_credits.purchased_used IS
    'Credits spent out of the purchased pool. spend_credits drains the GRANTED pool first and '
    'only overflows here, so this stays 0 until the monthly allocation is exhausted.';

COMMENT ON COLUMN public.user_credits.spendable IS
    'Auto-computed: (total - used) + (purchased_total - purchased_used). The real balance and '
    'the one the API returns. `remaining` deliberately still means the GRANTED half only, so '
    'pre-existing SQL readers keep their original semantics. Never set directly.';

-- Migration 115 put the authoritative "who may write this table" note here and listed the
-- five RPCs by name. It is the first thing a reader hits, so leaving it stale is how the next
-- person concludes the purchased pool is unmanaged.
COMMENT ON TABLE public.user_credits IS
    'Credit balances, TWO POOLS: granted (total/used, monthly, use-it-or-lose-it) and '
    'purchased (purchased_total/purchased_used, from consumable IAP, NEVER expires per App '
    'Store Guideline 3.1.1). `spendable` is the sum and the number the API serves. '
    'SERVICE-ROLE ONLY: every write goes through a SECURITY DEFINER RPC (spend_credits / '
    'refund_credits / ensure_credit_period / grant_tier_upgrade / revoke_tier_credits / '
    'add_purchased_credits / revoke_purchased_credits). Do not GRANT to anon or authenticated '
    '— `remaining` and `spendable` are generated columns and the invariants live in those '
    'functions, not in constraints. See migrations 115 and 117.';


-- ── 2. credit_transactions: record which pool each movement came from ────────────────────
-- `delta` is unchanged and still the signed total, so every existing reader keeps working.
-- The split exists so refund_credits can reverse a spend EXACTLY (PART 1 above).

ALTER TABLE public.credit_transactions
    ADD COLUMN IF NOT EXISTS granted_delta   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.credit_transactions
    ADD COLUMN IF NOT EXISTS purchased_delta INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.credit_transactions.granted_delta IS
    'Signed portion of `delta` that moved the GRANTED pool. delta = granted_delta + '
    'purchased_delta for every row written from migration 118 onward. Rows written BEFORE '
    '118 have 0/0 with a non-zero delta — refund_credits treats a 0/0 spend row as "unknown '
    'split" and falls back to granted-first, which is the direction that cannot mint '
    'permanent credits.';

COMMENT ON COLUMN public.credit_transactions.purchased_delta IS
    'Signed portion of `delta` that moved the PURCHASED pool. See granted_delta.';


-- ── 3. credit_packs: the consumable catalog (config, tunable without a deploy) ────────────
-- Mirrors plan_credits (migration 100 §1): non-sensitive storefront config, client-readable,
-- service-role-writable. `price_cents` is DISPLAY ONLY and a fallback — the price the user
-- actually pays comes from StoreKit's localized `displayPrice`, and Apple is the authority.
-- `credits` is NOT display-only: it is what we grant, so it is read server-side and must
-- never be sourced from the client.

CREATE TABLE IF NOT EXISTS public.credit_packs (
    product_id   TEXT PRIMARY KEY,            -- must match App Store Connect exactly
    credits      INTEGER NOT NULL,
    price_cents  INTEGER NOT NULL DEFAULT 0,  -- USD cents, storefront display only
    display_name TEXT    NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT credit_packs_credits_positive CHECK (credits > 0),
    CONSTRAINT credit_packs_price_nonneg     CHECK (price_cents >= 0)
);

-- `credits > 0` (not >= 0) on purpose: a zero or negative row would let a verified purchase
-- grant nothing, or DEBIT the buyer. add_purchased_credits guards it again at call time — a
-- corrupt catalog row is a money bug in both directions, and the value passes through Python
-- between the two checks.

-- Approved ladder. Each pack is priced ABOVE the subscription per-credit rate (Pro = $0.0125,
-- Max = $0.0100) so a top-up never undercuts a plan; the ladder discounts from 1.77x down to
-- 1.33x. Mega deliberately mirrors Pro's monthly allowance (1,200) so the upsell is legible:
-- "the same credits, every month, for $5 less".
INSERT INTO public.credit_packs (product_id, credits, price_cents, display_name, sort_order) VALUES
    ('com.phan.caydex.credits.starter',   90,  199, 'Starter', 1),
    ('com.phan.caydex.credits.plus',     250,  499, 'Plus',    2),
    ('com.phan.caydex.credits.power',    550,  999, 'Power',   3),
    ('com.phan.caydex.credits.mega',    1200, 1999, 'Mega',    4)
ON CONFLICT (product_id) DO UPDATE
    SET credits      = EXCLUDED.credits,
        price_cents  = EXCLUDED.price_cents,
        display_name = EXCLUDED.display_name,
        sort_order   = EXCLUDED.sort_order,
        updated_at   = NOW();
-- Deliberately does NOT re-set is_active: retiring a pack is done in the DB and must survive
-- a re-run of this migration.

ALTER TABLE public.credit_packs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "credit_packs_public_read" ON public.credit_packs;
CREATE POLICY "credit_packs_public_read" ON public.credit_packs
    FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "credit_packs_service_all" ON public.credit_packs;
CREATE POLICY "credit_packs_service_all" ON public.credit_packs
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON public.credit_packs TO anon, authenticated;
GRANT ALL    ON public.credit_packs TO service_role;


-- ── 4. credit_purchases: the exactly-once key + purchase audit trail ─────────────────────
-- One row per Apple consumable transaction. UNIQUE (environment, transaction_id) is what
-- makes the grant exactly-once (PART 2 above).
--
-- Account-only by design: guests cannot buy, because consumables are NOT restorable by Apple
-- and guest identity in this app is per-install and rotatable — a guest who reinstalls would
-- lose credits they paid for. So this takes the ORDINARY user-scoped FK from
-- .claude/rules/database.md, NOT the FK-less guest-writable shape of migrations 108/110/111.
-- The ON DELETE CASCADE also means account deletion cleans this up automatically, so unlike
-- credit_transactions this needs NO entry in `_UNLINKED_USER_TABLES` (users.py:448) — and
-- test_account_deletion_completeness.py only demands an entry for tables WITHOUT an FK.

CREATE TABLE IF NOT EXISTS public.credit_purchases (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 UUID NOT NULL
                                REFERENCES public.users(id) ON DELETE CASCADE,
    transaction_id          TEXT NOT NULL,
    environment             TEXT NOT NULL DEFAULT 'Production',
    original_transaction_id TEXT,
    product_id              TEXT    NOT NULL,
    credits                 INTEGER NOT NULL,
    price_cents             INTEGER,
    -- StoreKit `appAccountToken`: the uuid the CLIENT stamped on the purchase, which for us is
    -- the buying account's id. Recorded so a transaction redelivered into a DIFFERENT signed-in
    -- session can be refused instead of crediting the wrong account — there is no prior row to
    -- catch that case, so the token is the only evidence of who actually paid.
    app_account_token       UUID,
    purchased_at            TIMESTAMPTZ,        -- Apple's purchaseDate
    revoked_at              TIMESTAMPTZ,        -- set by revoke_purchased_credits on REFUND
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT credit_purchases_credits_positive CHECK (credits > 0)
);

-- THE idempotency key. Everything else in this file is bookkeeping around it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_purchases_env_txn
    ON public.credit_purchases (environment, transaction_id);
-- "this user's purchase history, newest first" — the only user-facing read pattern.
CREATE INDEX IF NOT EXISTS idx_credit_purchases_user
    ON public.credit_purchases (user_id, created_at DESC);
-- Apple's REFUND notification may identify the purchase by originalTransactionId.
CREATE INDEX IF NOT EXISTS idx_credit_purchases_original_txn
    ON public.credit_purchases (original_transaction_id);
-- `IAPService.user_id_for_transaction` resolves a webhook to its owner by transaction_id
-- ALONE (it has no environment to hand at that point), and the unique index above leads on
-- `environment`, so it cannot serve that lookup. Without this the REFUND webhook path is a
-- sequential scan over a table that only ever grows. Measured on 20k rows: 255 buffers and
-- 19,004 rows filtered, versus an index probe.
--
-- ⚠️ The name must differ from the unique index above. It originally did NOT — both were
-- `idx_credit_purchases_txn` — and because every statement here is `IF NOT EXISTS`, the second
-- one silently became a NO-OP: the migration applied cleanly, reported success, and simply
-- never created this index. Idempotency guards hide name collisions rather than surfacing
-- them, so distinct names are the only defence. Migration 121 repairs databases that took
-- the original 117.
CREATE INDEX IF NOT EXISTS idx_credit_purchases_txn_lookup
    ON public.credit_purchases (transaction_id);

-- Serves the split lookup in `refund_credits` (migration 118): "the most recent debit for
-- this (user, ref_id)". Partial on `delta < 0` because that is the only half ever searched —
-- it keeps the index to the spend rows and excludes every grant, refund and monthly reset.
-- Measured worst case (a ref_id with no matching debit, which is the fallback path): a full
-- backward PK scan, 20,025 rows filtered, 359 buffers → 3 buffers with this index.
CREATE INDEX IF NOT EXISTS idx_credit_transactions_spend_lookup
    ON public.credit_transactions (user_id, ref_id, id DESC)
    WHERE delta < 0;

ALTER TABLE public.credit_purchases ENABLE ROW LEVEL SECURITY;

-- No client policy. Unlike credit_transactions (which a user may read), this table is the
-- integrity key for granting money-backed credits: every write goes through the SECURITY
-- DEFINER RPCs below and nothing in the app reads it directly. Same posture migration 115
-- settled on for user_credits — anon/authenticated get nothing.
DROP POLICY IF EXISTS "credit_purchases_service_all" ON public.credit_purchases;
CREATE POLICY "credit_purchases_service_all" ON public.credit_purchases
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.credit_purchases FROM anon, authenticated;
GRANT  ALL ON public.credit_purchases TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.credit_purchases_id_seq TO service_role;


-- ── 5. add_purchased_credits: idempotent consumable grant ────────────────────────────────
-- Returns JSONB rather than an integer because the caller has to distinguish four outcomes
-- that map to four different HTTP responses, and a bare balance cannot express them:
--
--   {"outcome":"granted",  "credits":N, "spendable":N}  -> 200, credits added
--   {"outcome":"replay",   "credits":N, "spendable":N}  -> 200, already applied (the COMMON
--                                                          case — Transaction.updates fires
--                                                          on every launch)
--   {"outcome":"conflict", "owner_user_id":"..."}       -> 409 PURCHASE_ALREADY_LINKED
--   {"outcome":"invalid",  "reason":"..."}              -> 400, never charge-and-drop silently
--
-- `replay` MUST be a success for the client: it is what tells iOS to call
-- Transaction.finish(). Returning an error there strands the transaction forever, which is
-- the exact failure billing.py:132-146 documents for the subscription path.
--
-- A status string rather than RAISE EXCEPTION for the conflict case, because a plpgsql raise
-- reaches the service as a generic PostgREST error it would have to string-match.

CREATE OR REPLACE FUNCTION public.add_purchased_credits(
    p_transaction_id          TEXT,
    p_user_id                 UUID,
    p_product_id              TEXT,
    p_credits                 INTEGER,
    p_environment             TEXT        DEFAULT 'Production',
    p_original_transaction_id TEXT        DEFAULT NULL,
    p_price_cents             INTEGER     DEFAULT NULL,
    p_app_account_token       UUID        DEFAULT NULL,
    p_purchased_at            TIMESTAMPTZ DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST      CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_env       TEXT;
    v_inserted  INTEGER := 0;
    v_owner     UUID;
    v_credits   INTEGER;
    v_spendable INTEGER;
BEGIN
    -- Same guest guard as ensure_credit_period / spend_credits / grant_tier_upgrade. The
    -- endpoint already refuses guests, so reaching here means a bug or a stray call; refuse
    -- rather than crediting the shared sentinel every signed-out install uses.
    IF p_user_id IS NULL OR p_user_id = _GUEST THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'guest_or_null_user');
    END IF;

    IF p_transaction_id IS NULL OR p_transaction_id = '' THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'missing_transaction_id');
    END IF;

    -- NULL environment would defeat the unique index (NULLs are distinct), so the same
    -- purchase could be granted repeatedly. Never let it through.
    v_env := COALESCE(NULLIF(p_environment, ''), 'Production');

    -- A non-positive grant is a corrupt catalog row or a caller bug. Refuse BEFORE the dedup
    -- INSERT: writing a purchase row for a zero grant is unrecoverable, because every
    -- redelivery afterwards finds a duplicate and reports "replay" forever. The user paid, so
    -- a silent zero-grant is the worst outcome available.
    IF p_credits IS NULL OR p_credits <= 0 THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'non_positive_credits');
    END IF;

    -- THE idempotency. Claim the transaction id first: if this INSERT does nothing, another
    -- delivery of the same purchase already granted it and no balance is touched below.
    -- Doing it before the balance work also makes the common (replay) case one statement.
    INSERT INTO public.credit_purchases (
        user_id, transaction_id, environment, original_transaction_id,
        product_id, credits, price_cents, app_account_token, purchased_at
    ) VALUES (
        p_user_id, p_transaction_id, v_env, p_original_transaction_id,
        p_product_id, p_credits, p_price_cents, p_app_account_token, p_purchased_at
    )
    ON CONFLICT (environment, transaction_id) DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    IF v_inserted = 0 THEN
        SELECT user_id, credits INTO v_owner, v_credits
          FROM public.credit_purchases
         WHERE environment = v_env AND transaction_id = p_transaction_id;

        -- Ownership of a transaction never moves. Answering "retry later" here would make the
        -- client leave the transaction unfinished and redeliver it on every launch forever —
        -- the exact failure PURCHASE_ALREADY_LINKED / 409 was added to end for subscriptions.
        IF v_owner IS DISTINCT FROM p_user_id THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'owner_user_id', v_owner);
        END IF;

        SELECT spendable INTO v_spendable
          FROM public.user_credits WHERE user_id = p_user_id;

        RETURN jsonb_build_object(
            'outcome',   'replay',
            'credits',   v_credits,
            'spendable', COALESCE(v_spendable, 0)
        );
    END IF;

    -- Fresh grant. ensure_credit_period creates the balance row if this is the user's first
    -- touch, rolls a due month, and takes SELECT ... FOR UPDATE on the row — held for the rest
    -- of this transaction, so a concurrent grant or spend serialises behind us. Reused rather
    -- than re-implemented so the first-touch race handling stays in one place, and so we never
    -- hand-insert a row with a wrong `resets_at` that would skip the user's first allocation.
    PERFORM public.ensure_credit_period(p_user_id);

    UPDATE public.user_credits
       SET purchased_total = purchased_total + p_credits,
           updated_at      = NOW()
     WHERE user_id = p_user_id
    RETURNING spendable INTO v_spendable;

    IF v_spendable IS NULL THEN
        -- ensure_credit_period should have guaranteed a row. If it somehow did not, RAISE so
        -- the whole transaction — including the credit_purchases claim — rolls back. Otherwise
        -- the id is burned with no credits granted and every retry reports "replay".
        RAISE EXCEPTION
            'add_purchased_credits: no user_credits row for % after ensure_credit_period',
            p_user_id;
    END IF;

    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
    VALUES
        (p_user_id, p_credits, 0, p_credits, 'pack_purchase', p_transaction_id, v_spendable);

    RETURN jsonb_build_object(
        'outcome',   'granted',
        'credits',   p_credits,
        'spendable', v_spendable
    );
END;
$$;

-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by default and Supabase exposes every public
-- function at POST /rest/v1/rpc/<name>. This one MINTS CREDITS, so leaving it public would let
-- any holder of the shipped anon key grant themselves unlimited paid credits by inventing a
-- transaction id. This REVOKE is the single most important line in the file.
REVOKE ALL ON FUNCTION public.add_purchased_credits(
    TEXT, UUID, TEXT, INTEGER, TEXT, TEXT, INTEGER, UUID, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.add_purchased_credits(
    TEXT, UUID, TEXT, INTEGER, TEXT, TEXT, INTEGER, UUID, TIMESTAMPTZ) TO service_role;


-- ── 6. revoke_purchased_credits: claw back a REFUNDED pack ───────────────────────────────
-- Apple refunded the user's money, so the UNSPENT balance goes back. Mirrors migration 114
-- Part 1 exactly, including its two properties:
--
--   * FLOORS AT `purchased_used`. Credits already spent are a business loss, not a data
--     problem to model — and letting purchased_total drop below purchased_used would violate
--     the CHECK above and make `spendable` negative.
--   * IDEMPOTENT. Apple retries a REFUND notification up to 5 times over ~8 hours; the
--     revoked_at guard makes every delivery after the first a no-op.
--
-- Known, accepted exposure: buy -> spend everything -> request an Apple refund reclaims 0.
-- The mechanism that prevents that is answering CONSUMPTION_REQUEST via the App Store Server
-- API, which this repo has no client for. The service layer logs that case distinctly so the
-- exposure is measurable before deciding to build one.

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
    v_spendable   INTEGER;
BEGIN
    IF p_transaction_id IS NULL OR p_transaction_id = '' THEN
        RETURN jsonb_build_object('outcome', 'invalid', 'reason', 'missing_transaction_id');
    END IF;

    v_env := COALESCE(NULLIF(p_environment, ''), 'Production');

    -- Lock the purchase row so two concurrent REFUND deliveries cannot both pass the
    -- revoked_at check and claw back twice.
    SELECT * INTO v_row FROM public.credit_purchases
     WHERE environment = v_env AND transaction_id = p_transaction_id
     FOR UPDATE;

    IF NOT FOUND THEN
        -- A refund for a transaction we never granted (a subscription id routed here by
        -- mistake, or a purchase predating this feature). Not an error — the webhook must
        -- still answer 200 or Apple retries for days.
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
        -- Nothing was ever granted to claw back from. revoked_at is set, so retries no-op.
        RETURN jsonb_build_object('outcome', 'revoked', 'spendable', 0, 'reclaimed', 0);
    END IF;

    v_new_total := GREATEST(v_credits_row.purchased_used,
                            v_credits_row.purchased_total - v_row.credits);
    v_delta     := v_new_total - v_credits_row.purchased_total;   -- <= 0

    IF v_delta = 0 THEN
        -- Fully spent already: the floor bites and there is nothing to reclaim. Still counts
        -- as revoked (revoked_at is set), so a retry short-circuits above.
        RETURN jsonb_build_object(
            'outcome',   'revoked',
            'spendable', v_credits_row.spendable,
            'reclaimed', 0
        );
    END IF;

    UPDATE public.user_credits
       SET purchased_total = v_new_total,
           updated_at      = NOW()
     WHERE user_id = v_row.user_id
    RETURNING spendable INTO v_spendable;

    INSERT INTO public.credit_transactions
        (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
    VALUES
        (v_row.user_id, v_delta, 0, v_delta, 'pack_revoked', p_transaction_id, v_spendable);

    RETURN jsonb_build_object(
        'outcome',   'revoked',
        'spendable', v_spendable,
        'reclaimed', -v_delta
    );
END;
$$;

-- Only ever LOWERS a balance, so an anon caller could not enrich themselves — but they could
-- grief any account whose transaction id they hold. Same REVOKE-then-GRANT as everything else.
REVOKE ALL ON FUNCTION public.revoke_purchased_credits(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_purchased_credits(TEXT, TEXT) TO service_role;

COMMIT;
