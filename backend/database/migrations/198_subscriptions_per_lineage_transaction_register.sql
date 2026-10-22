-- 198_subscriptions_per_lineage_transaction_register.sql
--
-- Why: migration 196 derives a subscriber's paid credit period from the account's ONE
-- `subscriptions` row (UNIQUE (user_id)). Two adversarial reviews (2026-10-13) found six gaps that
-- no Python guard can close over one row (SYSTEM_DESIGN_GUIDELINES §9b.12 "Known, accepted gaps"):
--
--   1. A refunded OLDER period that is still the credit period in force keeps its credits: after
--      an early renewal (the renewal is the row), or when the REFUND's delivery failed until a
--      same-lineage resubscribe became the row. 196 claws back only when THE row is revoked under
--      the in-force key, and iap_service rightly refuses to write the old period over the newer
--      row.
--   2. Another lineage (a second Apple ID on the same Caydex account) re-points the one row: the
--      live lineage's webhooks then resolve to no user, a worse lineage demotes the account, the
--      other lineage's refunded credits are never clawed back, and its ordering key is copied
--      over the live one's.
--   3. A late or duplicate revocation of a refunded Max within 7 days of a Pro resubscribe reads
--      as the refund of an unrecorded overlap upgrade and drops the Pro payer to Free.
--   4. Credits an upgrade CARRIED from the Pro period survive a later refund of that Pro.
--   5. A refund of the running period delivered before the early renewal's DID_RENEW revokes the
--      row, and the renewal (signed earlier) is then refused as stale.
--   6. A REFUND_REVERSED is refused as stale when an unrelated later notification set the key.
--
-- Owner decisions, 2026-10-14 (design: .claude/session-state/b4c0d931-…-design.md, v2):
-- "One allocation" for two paying lineages, "Close it now" for the upgrade carry, "Delete with
-- account" for the new register, "Yes" to binding an unknown lineage by its appAccountToken, and
-- D1/D4/D6-D9 as recommended.
--
-- What this migration does:
--
--   * subscriptions: ONE ROW PER APPLE LINEAGE. UNIQUE (user_id) is dropped and a UNIQUE index on
--     original_transaction_id added, so a second lineage gets its own row and no row is ever
--     re-pointed. Two columns: transaction_id (the Apple transaction last applied) and
--     max_period_end, the latest current_period_end the row has ever held. A BEFORE trigger keeps
--     max_period_end for EVERY writer — iap_service in either mode, the expiry sweep, a hand edit —
--     restarts it when a pre-198 writer re-points the row to another lineage, and never touches
--     updated_at (the write fence). The backfill knows the mark only for a row that entitles now;
--     any other gets 'infinity' (unknown: its end may have been rewound before the trigger
--     existed), and Rule D never fires on it. There is deliberately no CHECK: the trigger already
--     keeps the column right for every writer (CHECKs run after BEFORE triggers), so one would only
--     bite with the trigger disabled. (Design v1 had a CHECK and no trigger: that failed every
--     write of code that does not set the column.)
--   * subscription_transactions (new): one row per Apple transaction iap_service has seen, keyed
--     (environment, transaction_id): when it was first applied to a lineage row, and the latest
--     Apple signedDate of a refund of it and of a reversal of that refund. Both only move forward,
--     so arrival order never matters; a transaction is REFUNDED while its latest refund is newer
--     than its latest reversal. Written only through record_subscription_transaction.
--   * subscription_credit_periods: revoke_requested_at (a keyed clawback asked for this period),
--     carried_from_period_id (the period an upgrade carried leftovers from) and the carry stamps.
--   * ensure_credit_period: 196's body with six changes. (1) The winning row: the account's best
--     row (one that can start a period now, then one that entitles, Production, higher tier,
--     later end). (2) Calendar sizing keeps 196's "any Production row" test, while v_paid_row reads
--     the winning row's own environment. (3) The in-force period's own lineage row (v_cur_sub)
--     decides the REFUND backstop and HOLD, and while it holds, another lineage's same-or-lower
--     tier does not start. (4) A period whose refund the register still holds is never reinstated.
--     (5) An upgrade records the period it carried from. (6) When the winner can neither start nor
--     reinstate while the in-force period's lineage holds, that lineage's own row is evaluated once
--     instead (the <<pick>> retarget), so its renewal is never skipped: a period can then start from
--     a row that is not the winner. A one-row account reads exactly 196's row
--     and takes exactly 196's branches. The calendar block is 194's, verbatim.
--   * revoke_tier_credits: reads the in-force lineage's row instead of "the" row.
--   * revoke_paid_period (new): claws back exactly one credit period, keyed (original transaction,
--     period end), never touching subscriptions or the tier. It re-checks the register under the
--     balance lock (a reversal that won the race keeps the credits), marks revoke_requested_at and
--     runs ensure_credit_period twice: its REFUND branch does 196's arithmetic, the second call
--     starts the next period. A refund of the period an upgrade carried from takes the carry back
--     once — from the balance while the upgrade is in force, from its revoked_remaining while it
--     is clawed back.
--   * reinstate_paid_carry (new): gives a clawed-back carry back once per clawback on
--     REFUND_REVERSED, re-checking the register under the balance lock; clearing carry_revoked_at
--     lets a later refund of the same period take it again.
--   * Guards: 196 now refuses to run once this migration's table exists, and 194's refusal names
--     the newest credit migration.
--
-- Ledger: a keyed clawback writes 196's `tier_revoked` (+ nothing else); a carry clawback writes
-- `tier_revoked` (-carry); a carry reinstatement writes `plan_reinstated` (+carry, ref_id = the
-- original transaction). No new reason, so the history screen needs no new label.
--
-- The purchased pool is never named (App Store 3.1.1; tests/test_credit_paid_periods.py).
--
-- Deploy order: deploy the backend first (it probes for subscription_transactions and behaves
-- exactly as before until it exists), then apply this. Applied before the deploy, or with an older
-- backend running, everything still works: the trigger keeps max_period_end right for writers that
-- do not set it (a row the old code re-points to another lineage starts its mark again), the
-- functions read one-row accounts exactly as 196, and the old backend never inserts a second row
-- except in one race (two FIRST purchases on two Apple IDs at once). Until the deploy, though,
-- the old code still re-points a row when a second Apple ID buys: that account keeps the pre-198
-- behaviour (gap 2) until the new backend runs.
-- Apply 197 (comments only) before this one: this file re-comments subscriptions.updated_at.
--
-- Depends on 114 (last_event_at) and 196 (subscription_credit_periods, the 196 functions).
-- Idempotent: IF NOT EXISTS / CREATE OR REPLACE / DROP … IF EXISTS / guarded DO blocks; the
-- backfill only fills NULLs. Re-running it is safe; re-running 196 or 194 after it is refused.
--
-- ⚠️ Never after 200 (200_credit_refund_roll_late_renewal_clawback_cover.sql). 200 redefines
-- ensure_credit_period (a late renewal starts; a clawback records what it covered); this file
-- applied again would silently put back 198's body, and refund_credits would then read clawback
-- stamps nothing writes any more. The guard below refuses that (added 2026-10-21, IAP audit phase
-- 2): once 200's column subscription_credit_periods.revoked_ledger_id exists, this file raises and
-- changes nothing.

BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'subscription_credit_periods'
                  AND column_name = 'revoked_ledger_id') THEN
        RAISE EXCEPTION '198 must not be applied after 200: it would replace 200''s ensure_credit_period. Re-apply 200 instead.';
    END IF;
END $$;

-- ── 0. Preflight: no two rows may share a lineage ──────────────────────────────────────────

DO $$
DECLARE
    v_dupes INTEGER;
BEGIN
    SELECT count(*) INTO v_dupes
      FROM (SELECT original_transaction_id
              FROM public.subscriptions
             WHERE original_transaction_id IS NOT NULL
             GROUP BY original_transaction_id
            HAVING count(*) > 1) d;
    IF v_dupes > 0 THEN
        RAISE EXCEPTION '198: % original_transaction_id value(s) sit on more than one subscriptions row; resolve them by hand (VERIFY query 4), then re-run', v_dupes;
    END IF;
END $$;

-- ── 1. subscriptions: one row per lineage ─────────────────────────────────────────────────

ALTER TABLE public.subscriptions ADD COLUMN IF NOT EXISTS transaction_id TEXT;
ALTER TABLE public.subscriptions ADD COLUMN IF NOT EXISTS max_period_end TIMESTAMPTZ;

-- The high-water mark is KNOWN only for a row that entitles now. A row revoked or expired before
-- 198 may have been REWOUND (the late refund of an older period wrote that period's end over it,
-- which the old code allowed over a non-entitling row), so its true mark is lost: 'infinity'
-- marks it unknown, and Rule D (iap_service `_newer_period_reason`) never fires on it — that
-- lineage keeps the pre-198 ordering instead of letting a refunded renewal's saved JWS through
-- (review #17, 2026-10-15). Re-runs touch only rows still unmarked.
UPDATE public.subscriptions
   SET max_period_end = CASE WHEN status IN ('active', 'grace_period', 'billing_retry')
                             THEN current_period_end
                             ELSE 'infinity'::timestamptz END
 WHERE max_period_end IS NULL
   AND (current_period_end IS NOT NULL
        OR status NOT IN ('active', 'grace_period', 'billing_retry'));

CREATE OR REPLACE FUNCTION public.subscriptions_keep_max_period_end()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    -- GREATEST ignores NULLs, and keeps an 'infinity' (an unknown mark) for good. Never touches
    -- updated_at: iap_service fences its writes on it.
    IF TG_OP = 'UPDATE' AND NEW.original_transaction_id IS DISTINCT FROM OLD.original_transaction_id THEN
        -- Re-pointed to ANOTHER lineage (only code from before 198, or the new backend for up to
        -- 300 s after it lands, does that): the mark describes this lineage's periods, so it
        -- starts again from its own. NEW.max_period_end still holds OLD's when the writer did not
        -- set it, so it is not read (review #0, 2026-10-15).
        NEW.max_period_end := NEW.current_period_end;
    ELSIF TG_OP = 'UPDATE' THEN
        NEW.max_period_end := GREATEST(NEW.max_period_end, OLD.max_period_end,
                                       OLD.current_period_end, NEW.current_period_end);
    ELSE
        NEW.max_period_end := GREATEST(NEW.max_period_end, NEW.current_period_end);
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.subscriptions_keep_max_period_end() FROM PUBLIC;

DROP TRIGGER IF EXISTS subscriptions_max_period_end ON public.subscriptions;
CREATE TRIGGER subscriptions_max_period_end
    BEFORE INSERT OR UPDATE ON public.subscriptions
    FOR EACH ROW EXECUTE FUNCTION public.subscriptions_keep_max_period_end();

-- The dropped constraint's index served every per-user read (winning_tier, the SQL below).
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON public.subscriptions (user_id);

CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_original_transaction_id_key
    ON public.subscriptions (original_transaction_id);

-- DESTRUCTIVE (an index, no data): idx_subscriptions_txn (migration 100) is the same btree on
-- original_transaction_id; the unique index above serves every lookup it served, and keeping both
-- only doubles the write cost (review #4, 2026-10-15).
DROP INDEX IF EXISTS public.idx_subscriptions_txn;

-- DESTRUCTIVE (a constraint, no data): one row per account becomes one row per Apple lineage.
-- The new unique index above takes over the race it used to serialise (two first deliveries of
-- one purchase).
ALTER TABLE public.subscriptions DROP CONSTRAINT IF EXISTS subscriptions_user_id_key;

COMMENT ON TABLE public.subscriptions IS
    'One row per Apple subscription LINEAGE (original_transaction_id, UNIQUE since 198) owned by '
    'an account; an account can hold several (a second Apple ID). Written only by '
    'iap_service.apply_transaction and the expiry sweep. users.tier mirrors the best entitling '
    'row; ensure_credit_period derives the paid credit period from the winning row. SELECT-own '
    'for authenticated (RLS). See migrations 100, 114, 196, 198.';

COMMENT ON COLUMN public.subscriptions.transaction_id IS
    'The Apple transactionId last applied to this lineage row (its period''s own id). NULL on '
    'rows written before 198 or by older code. Written by iap_service.apply_transaction. 198.';

COMMENT ON COLUMN public.subscriptions.max_period_end IS
    'The latest current_period_end this row has ever held for its lineage (an overlap upgrade '
    'moves current_period_end back; this never moves back; a re-point to another lineage restarts '
    'it). Maintained by the trigger subscriptions_max_period_end for every writer. Backfilled from '
    'current_period_end for rows that entitled when 198 was applied; ''infinity'' = UNKNOWN (a row '
    'revoked or expired before 198, whose end may have been rewound). iap_service lets an '
    'out-of-order renewal for a LATER period than a KNOWN mark apply over a revoked or expired '
    'row. 198.';

COMMENT ON COLUMN public.subscriptions.updated_at IS
    'When this row was last written. iap_service.apply_transaction FENCES its update on the '
    'value it read (WHERE id = ... AND updated_at = ...), so every writer of this table MUST set '
    'it: no trigger does (the only trigger, subscriptions_max_period_end, maintains max_period_end '
    'and never sets updated_at), and a write that leaves it unchanged is invisible to a '
    'concurrent delivery, which would then overwrite it. Also the client ordering fallback for a '
    'revoked row with no last_event_at. See migrations 197 and 198.';

-- ── 2. subscription_transactions: what we know about each Apple transaction ─────────────────

CREATE TABLE IF NOT EXISTS public.subscription_transactions (
    id                      BIGSERIAL PRIMARY KEY,
    environment             TEXT NOT NULL,
    transaction_id          TEXT NOT NULL,
    original_transaction_id TEXT NOT NULL,
    user_id                 UUID REFERENCES public.users(id) ON DELETE CASCADE,
    tier                    public.user_tier,
    period_end              TIMESTAMPTZ,
    revocation_date         TIMESTAMPTZ,
    applied_at              TIMESTAMPTZ,
    revoked_signed_at       TIMESTAMPTZ,
    reversed_signed_at      TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT subscription_transactions_key UNIQUE (environment, transaction_id),
    CONSTRAINT subscription_transactions_ids_nonempty
        CHECK (environment <> '' AND transaction_id <> '' AND original_transaction_id <> '')
);

CREATE INDEX IF NOT EXISTS idx_subscription_transactions_lineage
    ON public.subscription_transactions (original_transaction_id, period_end);

-- The FK's ON DELETE CASCADE looks rows up by this column on every account deletion.
CREATE INDEX IF NOT EXISTS idx_subscription_transactions_user
    ON public.subscription_transactions (user_id) WHERE user_id IS NOT NULL;

ALTER TABLE public.subscription_transactions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "subscription_transactions_service_all" ON public.subscription_transactions;
CREATE POLICY "subscription_transactions_service_all" ON public.subscription_transactions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.subscription_transactions FROM anon, authenticated;
GRANT  ALL ON public.subscription_transactions TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.subscription_transactions_id_seq TO service_role;

COMMENT ON TABLE public.subscription_transactions IS
    'One row per Apple subscription transaction iap_service has seen, keyed (environment, '
    'transaction_id): applied_at = first written to a lineage row; revoked_signed_at / '
    'reversed_signed_at = the latest Apple signedDate of a refund of it and of a reversal of that '
    'refund (both only move forward, so arrival order never matters). REFUNDED while '
    'revoked_signed_at is set and later than reversed_signed_at. Refuses replays of refunded '
    'transactions (even of lineages never bound to an account), lets a reversal through in either '
    'arrival order, tells an older period from an unrecorded upgrade, and keeps '
    'ensure_credit_period from reinstating a refunded period. Written only by '
    'record_subscription_transaction. user_id NULL = no known owner. SERVICE-ROLE ONLY. 198.';

-- ── 3. subscription_credit_periods: keyed clawback + upgrade carry provenance ───────────────

ALTER TABLE public.subscription_credit_periods
    ADD COLUMN IF NOT EXISTS revoke_requested_at TIMESTAMPTZ;
ALTER TABLE public.subscription_credit_periods
    ADD COLUMN IF NOT EXISTS carried_from_period_id BIGINT
        REFERENCES public.subscription_credit_periods(id) ON DELETE SET NULL;
ALTER TABLE public.subscription_credit_periods ADD COLUMN IF NOT EXISTS carry_revoked INTEGER;
ALTER TABLE public.subscription_credit_periods ADD COLUMN IF NOT EXISTS carry_revoked_at TIMESTAMPTZ;
ALTER TABLE public.subscription_credit_periods ADD COLUMN IF NOT EXISTS carry_reinstated_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'subscription_credit_periods_carry_revoked_nonneg') THEN
        ALTER TABLE public.subscription_credit_periods
            ADD CONSTRAINT subscription_credit_periods_carry_revoked_nonneg
            CHECK (carry_revoked IS NULL OR carry_revoked >= 0);
    END IF;
END $$;

-- The FK's ON DELETE SET NULL, and revoke_paid_period, look periods up by this column.
CREATE INDEX IF NOT EXISTS idx_subscription_credit_periods_carried_from
    ON public.subscription_credit_periods (carried_from_period_id)
    WHERE carried_from_period_id IS NOT NULL;

COMMENT ON COLUMN public.subscription_credit_periods.revoke_requested_at IS
    'Set by revoke_paid_period when the refunded transaction is this period while it is in force; '
    'ensure_credit_period''s REFUND branch then claws it back (196''s arithmetic) whatever the '
    'subscriptions row says. Cleared when the period is reinstated. 198.';
COMMENT ON COLUMN public.subscription_credit_periods.carried_from_period_id IS
    'For an upgrade period: the period whose leftovers it carried (the carry is capped by that '
    'period''s elapsed share). A refund of that period takes the carry back once. 198.';
COMMENT ON COLUMN public.subscription_credit_periods.carry_revoked IS
    'How much of this upgrade''s carry revoke_paid_period took back (the latest clawback; 0 is a '
    'stamp too). carry_revoked_at is set while it is clawed back and cleared when a reversal gives '
    'it back (carry_reinstated_at). 198.';

COMMENT ON TABLE public.subscription_credit_periods IS
    'Every Apple paid subscription period whose credit allocation was granted, keyed '
    '(original_transaction_id, period_end) UNIQUE so a period is granted at most once per account '
    '(the rows go with the account). Written only by ensure_credit_period, revoke_tier_credits '
    '(revoked_at, revoked_remaining), revoke_paid_period (revoke_requested_at, carry_revoked, '
    'carry_revoked_at, revoked_remaining), reinstate_paid_carry (carry_reinstated_at, '
    'carry_revoked_at, revoked_remaining) and 196''s '
    'backfill; user_credits.paid_period_id points at the one in force. A key granted before and not '
    'in force is never re-applied, so an older JWS re-submitted later cannot re-grant; a revoked one '
    'is reinstated once, with revoked_remaining, when Apple reverses the refund — never while '
    'subscription_transactions still holds a refund of it. carried_from_period_id: the period an '
    'upgrade carried leftovers from. period_start is current_period_end - 1 month (the '
    'subscriptions row stores no start). carried = unused credits kept on an upgrade, capped by the '
    'old period''s elapsed share. SERVICE-ROLE ONLY. Migrations 196, 198.';

COMMENT ON TABLE public.user_credits IS
    'Credit balances, TWO POOLS: granted (total/used, monthly, use-it-or-lose-it) and purchased '
    '(purchased_total/purchased_used, from consumable IAP, NEVER expires per App Store Guideline '
    '3.1.1). `spendable` is the sum and the number the API serves. SERVICE-ROLE ONLY: every write '
    'goes through a SECURITY DEFINER RPC (e.g. spend_credits / refund_credits / '
    'ensure_credit_period / grant_tier_upgrade / revoke_tier_credits / revoke_paid_period / '
    'reinstate_paid_carry / add_purchased_credits / revoke_purchased_credits). Do not GRANT to anon '
    'or authenticated. `remaining` and `spendable` are generated columns. The ORDERING and '
    'pool-selection invariants (spend granted first, refund the recorded split) live in those '
    'functions; the non-negativity and used <= total invariants are CHECK constraints on this table '
    '(115/117/140), so a direct UPDATE that breaks them fails with 23514 and an RPC that would must '
    'handle it. See migrations 115, 117, 139, 140, 166, 198.';

-- ── 4. record_subscription_transaction ────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.record_subscription_transaction(
    p_environment             TEXT,
    p_transaction_id          TEXT,
    p_original_transaction_id TEXT,
    p_user_id                 UUID,
    p_tier                    TEXT,
    p_period_end              TIMESTAMPTZ,
    p_revocation_date         TIMESTAMPTZ,
    p_kind                    TEXT,
    p_signed_at               TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_owner UUID;
    v_at    TIMESTAMPTZ;
    v_t     public.subscription_transactions%ROWTYPE;
BEGIN
    IF p_kind IS NULL OR p_kind NOT IN ('applied', 'revoked', 'reversed') THEN
        RAISE EXCEPTION 'record_subscription_transaction: unknown kind %', p_kind;
    END IF;
    IF COALESCE(p_environment, '') = '' OR COALESCE(p_transaction_id, '') = ''
       OR COALESCE(p_original_transaction_id, '') = '' THEN
        RAISE EXCEPTION 'record_subscription_transaction: environment, transaction_id and original_transaction_id are required';
    END IF;
    -- A reversal is evidence only with Apple's own date: stamped now(), it would outrank every
    -- refund signed before now and read "not refunded" (a second refund included).
    IF p_kind = 'reversed' AND p_signed_at IS NULL THEN
        RAISE EXCEPTION 'record_subscription_transaction: a reversal needs Apple''s signedDate';
    END IF;

    -- A token can name a deleted account: keep an owner only if it exists.
    IF p_user_id IS NOT NULL THEN
        SELECT u.id INTO v_owner FROM public.users u WHERE u.id = p_user_id FOR KEY SHARE;
    END IF;
    -- Serialise with revoke_paid_period / ensure_credit_period on this account (same lock order:
    -- the balance row first), so a reversal recorded here cannot interleave with a clawback that
    -- read the register before it.
    IF v_owner IS NOT NULL THEN
        PERFORM 1 FROM public.user_credits WHERE user_id = v_owner FOR UPDATE;
    END IF;

    -- A refund with no signedDate is stamped now(): later than any pre-refund signature, so it
    -- fails toward "refunded".
    v_at := COALESCE(p_signed_at, now());

    INSERT INTO public.subscription_transactions AS t
        (environment, transaction_id, original_transaction_id, user_id, tier, period_end,
         revocation_date, applied_at, revoked_signed_at, reversed_signed_at)
    VALUES (p_environment, p_transaction_id, p_original_transaction_id, v_owner,
            NULLIF(p_tier, '')::public.user_tier, p_period_end, p_revocation_date,
            CASE WHEN p_kind = 'applied'  THEN now() END,
            CASE WHEN p_kind = 'revoked'  THEN v_at END,
            CASE WHEN p_kind = 'reversed' THEN v_at END)
    ON CONFLICT (environment, transaction_id) DO UPDATE SET
        user_id            = COALESCE(t.user_id, EXCLUDED.user_id),
        tier               = COALESCE(t.tier, EXCLUDED.tier),
        period_end         = COALESCE(t.period_end, EXCLUDED.period_end),
        revocation_date    = COALESCE(EXCLUDED.revocation_date, t.revocation_date),
        applied_at         = COALESCE(t.applied_at, EXCLUDED.applied_at),
        revoked_signed_at  = GREATEST(t.revoked_signed_at, EXCLUDED.revoked_signed_at),
        reversed_signed_at = GREATEST(t.reversed_signed_at, EXCLUDED.reversed_signed_at),
        updated_at         = now()
    RETURNING * INTO v_t;

    IF v_owner IS NOT NULL AND v_t.user_id IS DISTINCT FROM v_owner THEN
        RAISE WARNING 'record_subscription_transaction: %/% is recorded for user %, not %',
            p_environment, p_transaction_id, v_t.user_id, v_owner;
    END IF;

    RETURN jsonb_build_object(
        'applied_at',         v_t.applied_at,
        'revoked_signed_at',  v_t.revoked_signed_at,
        'reversed_signed_at', v_t.reversed_signed_at,
        'user_id',            v_t.user_id,
        'refunded',           (v_t.revoked_signed_at IS NOT NULL
                               AND (v_t.reversed_signed_at IS NULL
                                    OR v_t.reversed_signed_at < v_t.revoked_signed_at)));
END;
$$;

COMMENT ON FUNCTION public.record_subscription_transaction(TEXT, TEXT, TEXT, UUID, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TIMESTAMPTZ) IS
    'Records what iap_service learned about one Apple subscription transaction: applied to a '
    'lineage row (applied_at, first time only), a refund (revoked_signed_at) or a reversal '
    '(reversed_signed_at, needs Apple''s signedDate). Every column only moves forward. Takes the '
    'owner''s user_credits lock first. Returns the merged row and whether it is refunded. '
    'service_role only. 198.';

-- ── 5. ensure_credit_period — 196 + the winning row, the in-force lineage row, the guard ────

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
    v_sub_prod   BOOLEAN := FALSE;
    v_paid_row   BOOLEAN := FALSE;
    v_cur        public.subscription_credit_periods%ROWTYPE;
    v_has_cur    BOOLEAN := FALSE;
    v_cur_sub    public.subscriptions%ROWTYPE;
    v_has_cur_sub BOOLEAN := FALSE;
    v_cur_holds  BOOLEAN := FALSE;
    v_running    BOOLEAN := FALSE;
    v_new_key    BOOLEAN := FALSE;
    v_seen       BOOLEAN := FALSE;
    v_seen_row   public.subscription_credit_periods%ROWTYPE;
    v_reinstate  BOOLEAN := FALSE;
    v_start_paid BOOLEAN := FALSE;
    v_retarget   BOOLEAN := FALSE;
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

    -- 198: one row per Apple LINEAGE. v_sub is the WINNING row: one that can carry a paid
    -- period now, then one that still entitles, Production, the higher tier, the later end. A
    -- one-row account reads exactly 196's row. NULL environment = written before 196: Production.
    SELECT * INTO v_sub
      FROM public.subscriptions s
     WHERE s.user_id = p_user_id
     ORDER BY (COALESCE(s.original_transaction_id, '') <> '') DESC,
              COALESCE(s.status = 'active' AND s.current_period_end > now(), FALSE) DESC,
              (s.status IN ('active', 'grace_period', 'billing_retry')) DESC,
              (COALESCE(s.environment, 'Production') = 'Production') DESC,
              (CASE s.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END) DESC,
              s.current_period_end DESC NULLS LAST,
              s.updated_at DESC,
              s.id
     LIMIT 1;
    v_has_sub  := FOUND;
    v_sub_prod := v_has_sub AND COALESCE(v_sub.environment, 'Production') = 'Production';
    -- Calendar sizing: ANY Production row (any status) means users.tier mirrors a paid tier, so
    -- only the complimentary floor may size the calendar allowance (196's rule, kept on purpose:
    -- restricting it to entitling rows would let a stale paid mirror size it after an expiry).
    v_prod_row := EXISTS (SELECT 1 FROM public.subscriptions s2
                           WHERE s2.user_id = p_user_id
                             AND COALESCE(s2.environment, 'Production') = 'Production');

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
    v_paid_row := v_sub_prod
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

    -- Which paid period the pool is in, its lineage's own row, and whether the winning row
    -- brings a new one. Reads only; nothing is written before the branches below.
    IF v_row.paid_period_id IS NOT NULL THEN
        SELECT * INTO v_cur FROM public.subscription_credit_periods WHERE id = v_row.paid_period_id;
        v_has_cur := FOUND;
        v_running := v_has_cur AND v_cur.period_end > now();
        IF v_has_cur THEN
            SELECT * INTO v_cur_sub
              FROM public.subscriptions
             WHERE user_id = p_user_id
               AND original_transaction_id = v_cur.original_transaction_id;
            v_has_cur_sub := FOUND;
        END IF;
    END IF;

    -- 198: the in-force period has ended but its OWN lineage still entitles (Apple's billing
    -- grace or retry, or a renewal not recorded yet): it holds, as 196's HOLD did for its row.
    v_cur_holds := COALESCE(
        v_has_cur AND NOT v_running AND v_has_cur_sub
        AND COALESCE(v_cur_sub.environment, 'Production') = 'Production'
        AND v_cur_sub.status IN ('active', 'grace_period', 'billing_retry')
        AND (CASE v_cur_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
          > (CASE v_tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END),
        FALSE);

    -- The candidate key is the winner's — at most once more, the in-force lineage's own (below).
    <<pick>>
    LOOP
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
        -- the refund (the row entitles again under the same key), which reinstates it once. 198: never
        -- while the transaction register still holds a refund of that period.
        v_reinstate := COALESCE(
            v_new_key AND v_seen AND v_seen_row.user_id = p_user_id
            AND v_seen_row.revoked_at IS NOT NULL AND NOT v_running
            AND NOT EXISTS (SELECT 1 FROM public.subscription_transactions t
                             WHERE t.original_transaction_id = v_seen_row.original_transaction_id
                               AND t.period_end = v_seen_row.period_end
                               AND t.revoked_signed_at IS NOT NULL
                               AND (t.reversed_signed_at IS NULL
                                    OR t.reversed_signed_at < t.revoked_signed_at)),
            FALSE);

        -- A new period starts when nothing paid is running, or at once when it is a higher tier
        -- than the running period (an upgrade). 198: while the in-force period's own lineage holds,
        -- ANOTHER lineage's same-or-lower tier waits too; the lineage's own next key does not (the
        -- retarget below evaluates it when another lineage wins the ORDER BY).
        v_start_paid := COALESCE(
            v_new_key AND NOT v_seen
            AND ((NOT v_running
                  AND NOT (v_cur_holds
                           AND v_sub.original_transaction_id <> v_cur.original_transaction_id
                           AND (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
                            <= (CASE v_cur.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)))
                 OR (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
                  > (CASE v_cur.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)),
            FALSE);

        -- 198: the in-force lineage's OWN next key. When the winner neither starts nor reinstates
        -- while the in-force period's lineage holds (another lineage held back, a winner granted
        -- elsewhere, one at the floor), that lineage's own row is evaluated once instead — so its
        -- renewal starts once its period has ended, as in 196, even when another lineage outranks it
        -- (a later end: early renewals, a recovery in billing grace; a higher tier after its own
        -- downgrade, which then starts as an upgrade on the next call). Otherwise only a HOLD was left.
        v_retarget := COALESCE(
            NOT v_start_paid AND NOT v_reinstate AND v_cur_holds
            AND v_cur_sub.status = 'active'
            AND v_cur_sub.current_period_end > now()
            AND COALESCE(v_cur_sub.original_transaction_id, '') <> ''
            AND v_sub.original_transaction_id IS DISTINCT FROM v_cur_sub.original_transaction_id,
            FALSE);
        EXIT pick WHEN NOT v_retarget;
        v_sub      := v_cur_sub;
        v_sub_prod := COALESCE(v_sub.environment, 'Production') = 'Production';
        v_paid_row := v_sub_prod
            AND (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END)
              > (CASE v_tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END);
        v_seen     := FALSE;
    END LOOP pick;

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

    -- REFUND of the period in force: the revoked transaction IS that period (its lineage's row
    -- is revoked with the same original transaction AND the same period end — a refund of an
    -- older renewal, or of a later one not yet started, leaves it alone), or 198's
    -- revoke_paid_period asked for exactly this period. Claw back exactly as revoke_tier_credits
    -- (139) does, floored at the calendar allowance; mark the period revoked so a reversal can
    -- reinstate it.
    IF v_has_cur AND (
           v_cur.revoke_requested_at IS NOT NULL
        OR (v_has_cur_sub AND v_cur_sub.status = 'revoked'
            AND v_cur_sub.original_transaction_id = v_cur.original_transaction_id
            AND v_cur_sub.current_period_end = v_cur.period_end)) THEN
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
           SET revoked_at          = NULL,
               revoked_remaining   = NULL,
               revoke_requested_at = NULL,
               reinstated_at       = now()
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
    -- capped at the old allocation times the old period's elapsed share (Apple refunds the rest
    -- of that period). 198: the upgrade records which period it carried from.
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
             carried, source, carried_from_period_id)
        VALUES (p_user_id, v_sub.original_transaction_id,
                LEAST(v_sub.current_period_end - INTERVAL '1 month', now()),
                v_sub.current_period_end, v_sub.tier, v_plan_alloc, v_carry,
                CASE WHEN v_running THEN 'upgrade' ELSE 'new_period' END,
                CASE WHEN v_running THEN v_cur.id END)
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

    -- It is over. HOLD while it can still be paid for — 198: decided on the in-force period's OWN
    -- lineage row when it exists (another lineage in grace must not hold an expired period's
    -- leftovers, nor a Sandbox / comp-floor row lapse a Production one in grace); otherwise, as in
    -- 196, on the winning row. Else LAPSE: the leftovers expire and the calendar allowance starts.
    IF v_has_cur_sub THEN
        IF v_cur_holds THEN
            RETURN (v_row.total - v_row.used);
        END IF;
    ELSIF v_paid_row AND v_sub.status IN ('active', 'grace_period', 'billing_retry') THEN
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
    'Brings the GRANTED pool up to date (196, 198). Paid: one allocation per Apple paid period, '
    'from the account''s WINNING subscriptions row (198: one row per lineage) when it is '
    'PRODUCTION, active with a future end and above the complimentary floor; each '
    '(original_transaction_id, period_end) is granted at most once via subscription_credit_periods. '
    'A new period waits for the running one to end — and, while the in-force period''s own lineage '
    'still entitles, so does another lineage''s same-or-lower tier — unless it is a higher tier (an '
    'upgrade carries leftovers capped by the old period''s elapsed share, and records where it '
    'carried from); when the winner cannot start, the in-force lineage''s own next key is evaluated '
    'instead, so its renewal is never skipped. A refunded period (its lineage row revoked under its key, or '
    'revoke_requested_at) is clawed back and marked; it is reinstated once if Apple reverses the '
    'refund and subscription_transactions holds no refund of it. HOLD/LAPSE follow the in-force '
    'lineage''s own row. Calendar (Free / complimentary / Sandbox): resets on the ET 1st with 194''s '
    'monthly_expired row. Never touches the purchased pool (App Store 3.1.1). Returns the granted '
    'remaining. service_role only.';

-- ── 6. revoke_tier_credits — 196, reading the in-force lineage's row ──────────────────────

CREATE OR REPLACE FUNCTION public.revoke_tier_credits(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST        CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_free        INTEGER;
    v_row         public.user_credits%ROWTYPE;
    v_cur         public.subscription_credit_periods%ROWTYPE;
    v_cur_sub     public.subscriptions%ROWTYPE;
    v_has_cur_sub BOOLEAN := FALSE;
    v_floor       INTEGER;
    v_writeoff    INTEGER;
    v_delta       INTEGER;
    v_now_et      TIMESTAMP;
    v_next_reset  TIMESTAMPTZ;
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
    -- 198: the period's OWN lineage row decides (an account can hold several rows).
    IF v_row.paid_period_id IS NOT NULL THEN
        SELECT * INTO v_cur FROM public.subscription_credit_periods WHERE id = v_row.paid_period_id;
        SELECT * INTO v_cur_sub
          FROM public.subscriptions
         WHERE user_id = p_user_id
           AND original_transaction_id = v_cur.original_transaction_id;
        v_has_cur_sub := FOUND;
        IF NOT COALESCE(v_has_cur_sub
                        AND v_cur_sub.status = 'revoked'
                        AND v_cur_sub.original_transaction_id = v_cur.original_transaction_id
                        AND v_cur_sub.current_period_end = v_cur.period_end, FALSE) THEN
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
    'Apple REFUND/REVOKE of a subscription (legacy backstop; iap_service calls revoke_paid_period '
    'once 198 is applied): claws back the unspent granted pool to GREATEST(free allocation, used) '
    'and writes off the spend (139), then leaves paid mode (196). In paid mode it acts only when '
    'the in-force period''s own lineage row (198) is revoked under that period''s key; otherwise it '
    'logs a WARNING and changes nothing. Never touches the purchased pool. service_role only.';

-- ── 7. revoke_paid_period — the keyed clawback ────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.revoke_paid_period(
    p_user_id                 UUID,
    p_environment             TEXT,
    p_transaction_id          TEXT,
    p_original_transaction_id TEXT,
    p_period_end              TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST     CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_row      public.user_credits%ROWTYPE;
    v_t        public.subscription_transactions%ROWTYPE;
    v_p        public.subscription_credit_periods%ROWTYPE;
    v_u        public.subscription_credit_periods%ROWTYPE;
    v_x        INTEGER;
    v_after    BIGINT;
    v_now_et   TIMESTAMP;
BEGIN
    IF p_user_id = _GUEST THEN
        RETURN jsonb_build_object('outcome', 'guest');
    END IF;

    SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'no_balance');
    END IF;

    -- Re-checked UNDER the balance lock: a REFUND_REVERSED recorded since the caller decided
    -- (record_subscription_transaction takes the same lock first) keeps the credits.
    SELECT * INTO v_t FROM public.subscription_transactions
     WHERE environment = p_environment AND transaction_id = p_transaction_id;
    IF NOT FOUND THEN
        RAISE WARNING 'revoke_paid_period: %/% is not in subscription_transactions; nothing clawed back',
            p_environment, p_transaction_id;
        RETURN jsonb_build_object('outcome', 'not_recorded');
    END IF;
    IF NOT (v_t.revoked_signed_at IS NOT NULL
            AND (v_t.reversed_signed_at IS NULL OR v_t.reversed_signed_at < v_t.revoked_signed_at)) THEN
        RETURN jsonb_build_object('outcome', 'reversed');
    END IF;

    SELECT * INTO v_p FROM public.subscription_credit_periods
     WHERE original_transaction_id = p_original_transaction_id
       AND period_end = p_period_end
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'no_period');
    END IF;
    IF v_p.user_id <> p_user_id THEN
        RAISE WARNING 'revoke_paid_period: period %/% belongs to user %, not %; nothing clawed back',
            p_original_transaction_id, p_period_end, v_p.user_id, p_user_id;
        RETURN jsonb_build_object('outcome', 'other_owner');
    END IF;

    -- The refunded period IS the one in force: ask ensure_credit_period's REFUND branch for it
    -- (196's arithmetic, in one place), then let it start the next period.
    IF v_row.paid_period_id IS NOT DISTINCT FROM v_p.id THEN
        UPDATE public.subscription_credit_periods
           SET revoke_requested_at = COALESCE(revoke_requested_at, now())
         WHERE id = v_p.id;
        PERFORM public.ensure_credit_period(p_user_id);
        PERFORM public.ensure_credit_period(p_user_id);
        SELECT paid_period_id INTO v_after FROM public.user_credits WHERE user_id = p_user_id;
        IF v_after IS NOT DISTINCT FROM v_p.id THEN
            RAISE WARNING 'revoke_paid_period: period %/% of user % is still in force after the clawback',
                p_original_transaction_id, p_period_end, p_user_id;
            RETURN jsonb_build_object('outcome', 'still_in_force');
        END IF;
        RETURN jsonb_build_object('outcome', 'clawed_back');
    END IF;

    IF v_p.revoked_at IS NOT NULL THEN
        RETURN jsonb_build_object('outcome', 'already_revoked');
    END IF;

    -- Not in force. The period an upgrade CARRIED leftovers from: take the carry back — once per
    -- refund. carry_revoked_at is set while the carry is clawed back and cleared when a reversal
    -- gives it back, so Apple refunding the period AGAIN after a reversal takes it again (review of
    -- the fix pass, 2026-10-15; a duplicate of an already-reversed refund never gets here — D4).
    SELECT * INTO v_u FROM public.subscription_credit_periods
     WHERE carried_from_period_id = v_p.id
       AND user_id = p_user_id
     ORDER BY id DESC
     LIMIT 1
       FOR UPDATE;
    IF FOUND AND v_u.carry_revoked_at IS NULL THEN
        v_now_et := (now() AT TIME ZONE 'America/New_York');
        IF v_row.paid_period_id IS NOT DISTINCT FROM v_u.id THEN
            -- The upgrade is in force: from the balance (never below what was spent).
            v_x := LEAST(v_u.carried, GREATEST(v_row.total - v_row.used, 0));
            IF v_x > 0 THEN
                UPDATE public.user_credits
                   SET total      = total - v_x,
                       updated_at = NOW()
                 WHERE user_id = p_user_id;
                INSERT INTO public.credit_transactions
                    (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
                VALUES (p_user_id, -v_x, -v_x, 0, 'tier_revoked',
                        to_char(v_now_et, 'YYYY-MM'), v_row.total - v_x - v_row.used);
            END IF;
            UPDATE public.subscription_credit_periods
               SET carry_revoked = v_x, carry_revoked_at = now()
             WHERE id = v_u.id AND carry_revoked_at IS NULL;
            RETURN jsonb_build_object('outcome', 'carry_clawed_back', 'carry', v_x);
        ELSIF v_u.revoked_at IS NOT NULL THEN
            -- The upgrade was itself clawed back: take the carry out of what a reversal of that
            -- refund would give back.
            v_x := LEAST(v_u.carried, COALESCE(v_u.revoked_remaining, 0));
            UPDATE public.subscription_credit_periods
               SET revoked_remaining = COALESCE(revoked_remaining, 0) - v_x,
                   carry_revoked     = v_x,
                   carry_revoked_at  = now()
             WHERE id = v_u.id AND carry_revoked_at IS NULL;
            RETURN jsonb_build_object('outcome', 'carry_clawed_back_from_revoked', 'carry', v_x);
        END IF;
        -- The upgrade period has ended: the carry expired with it.
    END IF;

    RETURN jsonb_build_object('outcome', 'not_in_force');
END;
$$;

COMMENT ON FUNCTION public.revoke_paid_period(UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ) IS
    'Keyed clawback (198): the refund of Apple transaction (environment, transaction_id), whose '
    'period is (original_transaction_id, period_end). Under the user_credits lock it re-checks '
    'that subscription_transactions still holds the refund (else ''reversed''). If that period is '
    'in force it sets revoke_requested_at and runs ensure_credit_period twice (196''s clawback, '
    'then the next period); if an upgrade carried leftovers from it, the carry is taken back once '
    'per refund (from the balance, or from the clawed-back upgrade''s revoked_remaining; a '
    'reinstatement clears carry_revoked_at). Never touches '
    'subscriptions, the tier or the purchased pool. Returns {outcome}. service_role only.';

-- ── 8. reinstate_paid_carry — REFUND_REVERSED of the period an upgrade carried from ────────

CREATE OR REPLACE FUNCTION public.reinstate_paid_carry(
    p_user_id                 UUID,
    p_original_transaction_id TEXT,
    p_period_end              TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    _GUEST   CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_row    public.user_credits%ROWTYPE;
    v_p      public.subscription_credit_periods%ROWTYPE;
    v_u      public.subscription_credit_periods%ROWTYPE;
    v_x      INTEGER;
BEGIN
    IF p_user_id = _GUEST THEN
        RETURN jsonb_build_object('outcome', 'guest');
    END IF;

    SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'no_balance');
    END IF;

    SELECT * INTO v_p FROM public.subscription_credit_periods
     WHERE original_transaction_id = p_original_transaction_id
       AND period_end = p_period_end
       AND user_id = p_user_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'no_period');
    END IF;

    SELECT * INTO v_u FROM public.subscription_credit_periods
     WHERE carried_from_period_id = v_p.id
       AND user_id = p_user_id
     ORDER BY id DESC
     LIMIT 1
       FOR UPDATE;
    IF NOT FOUND OR v_u.carry_revoked_at IS NULL THEN
        RETURN jsonb_build_object('outcome', 'nothing_to_reinstate');
    END IF;

    -- Under the balance lock: a refund of that period that Apple signed AFTER the reversal (it
    -- refunded it again) keeps the carry clawed back, whatever the caller decided.
    IF EXISTS (SELECT 1 FROM public.subscription_transactions t
                WHERE t.original_transaction_id = p_original_transaction_id
                  AND t.period_end = p_period_end
                  AND t.revoked_signed_at IS NOT NULL
                  AND (t.reversed_signed_at IS NULL OR t.reversed_signed_at < t.revoked_signed_at)) THEN
        RETURN jsonb_build_object('outcome', 'still_refunded');
    END IF;

    v_x := COALESCE(v_u.carry_revoked, 0);
    IF v_row.paid_period_id IS NOT DISTINCT FROM v_u.id THEN
        IF v_x > 0 THEN
            UPDATE public.user_credits
               SET total      = total + v_x,
                   updated_at = NOW()
             WHERE user_id = p_user_id;
            INSERT INTO public.credit_transactions
                (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
            VALUES (p_user_id, v_x, v_x, 0, 'plan_reinstated',
                    p_original_transaction_id, v_row.total + v_x - v_row.used);
        END IF;
        UPDATE public.subscription_credit_periods
           SET carry_reinstated_at = now(),
               carry_revoked_at    = NULL
         WHERE id = v_u.id AND carry_revoked_at IS NOT NULL;
        RETURN jsonb_build_object('outcome', 'carry_reinstated', 'carry', v_x);
    ELSIF v_u.revoked_at IS NOT NULL THEN
        UPDATE public.subscription_credit_periods
           SET revoked_remaining   = COALESCE(revoked_remaining, 0) + v_x,
               carry_reinstated_at = now(),
               carry_revoked_at    = NULL
         WHERE id = v_u.id AND carry_revoked_at IS NOT NULL;
        RETURN jsonb_build_object('outcome', 'carry_reinstated_to_revoked', 'carry', v_x);
    END IF;

    -- The upgrade period has ended: the carry would have expired with it.
    UPDATE public.subscription_credit_periods
       SET carry_reinstated_at = now(),
           carry_revoked_at    = NULL
     WHERE id = v_u.id AND carry_revoked_at IS NOT NULL;
    RETURN jsonb_build_object('outcome', 'carry_expired');
END;
$$;

COMMENT ON FUNCTION public.reinstate_paid_carry(UUID, TEXT, TIMESTAMPTZ) IS
    'REFUND_REVERSED of the period an upgrade carried leftovers from (198): gives back the carry '
    'revoke_paid_period took, once per clawback — to the balance while the upgrade is in force, to '
    'its revoked_remaining while it is clawed back; nothing after it ended — and clears '
    'carry_revoked_at, so a later refund takes it again. Re-checks subscription_transactions under '
    'the balance lock (a refund signed after the reversal keeps it clawed: ''still_refunded''). '
    'Never touches the purchased pool. service_role only.';

-- ── ACL ───────────────────────────────────────────────────────────────────────────────────

REVOKE ALL ON FUNCTION public.ensure_credit_period(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ensure_credit_period(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_tier_credits(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_tier_credits(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.record_subscription_transaction(TEXT, TEXT, TEXT, UUID, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_subscription_transaction(TEXT, TEXT, TEXT, UUID, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TIMESTAMPTZ) TO service_role;

REVOKE ALL ON FUNCTION public.revoke_paid_period(UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_paid_period(UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ) TO service_role;

REVOKE ALL ON FUNCTION public.reinstate_paid_carry(UUID, TEXT, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reinstate_paid_carry(UUID, TEXT, TIMESTAMPTZ) TO service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;

-- VERIFY (run after applying):
--   1. One row: t, t, t, t, f, f, t, t.
--   SELECT to_regclass('public.subscription_transactions') IS NOT NULL AS has_register,
--          EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
--                    AND table_name = 'subscriptions' AND column_name = 'max_period_end') AS has_max,
--          to_regclass('public.subscriptions_original_transaction_id_key') IS NOT NULL AS has_otid_key,
--          EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'subscriptions_max_period_end') AS has_trigger,
--          EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'subscriptions_user_id_key') AS still_one_per_user,
--          has_function_privilege('anon', 'public.revoke_paid_period(uuid, text, text, text, timestamptz)', 'EXECUTE') AS anon_exec,
--          pg_get_functiondef('public.ensure_credit_period(uuid)'::regprocedure) LIKE '%revoke_requested_at%' AS new_body,
--          NOT has_table_privilege('authenticated', 'public.subscription_transactions', 'SELECT') AS register_closed;
--
--   2. Every row's high-water mark is at or after its period end ('infinity' = unknown, for rows
--      that did not entitle when 198 was applied). Expect 0.
--   SELECT count(*) FROM public.subscriptions
--    WHERE current_period_end IS NOT NULL
--      AND (max_period_end IS NULL OR max_period_end < current_period_end);
--
--   3. Accounts holding more than one lineage (expected 0 right after applying; later, each is an
--      account that has held more than one Apple subscription — a second Apple ID, an older lapsed
--      one, or a Sandbox/TestFlight row beside a Production one — not necessarily paying twice; the
--      paying-twice signal is the WARNING IAP TWO PAID SUBSCRIPTIONS, OWNER_TASKS §2.4):
--   SELECT user_id, count(*) FROM public.subscriptions GROUP BY user_id HAVING count(*) > 1;
--
--   4. (If the preflight refused) the rows sharing an original transaction:
--   SELECT original_transaction_id, user_id, status, tier, current_period_end, updated_at
--     FROM public.subscriptions
--    WHERE original_transaction_id IN (SELECT original_transaction_id FROM public.subscriptions
--                                        WHERE original_transaction_id IS NOT NULL
--                                        GROUP BY 1 HAVING count(*) > 1)
--    ORDER BY original_transaction_id, updated_at;
