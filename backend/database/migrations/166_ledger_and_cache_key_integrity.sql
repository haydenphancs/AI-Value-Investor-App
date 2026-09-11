-- 166_ledger_and_cache_key_integrity.sql
--
-- Why: three invariants the code relies on are enforced nowhere in the schema, and two
-- schema COMMENTs say the opposite of what the schema does.
--
-- 1. credit_transactions has NO CHECK constraint at all. `refund_credits` decides which
--    pool gets money back by reading `granted_delta` / `purchased_delta` off the matched
--    debit (§9b.2), so `delta = granted_delta + purchased_delta` is the most load-bearing
--    money invariant in the ledger — and it lives only in the bodies of the SECURITY DEFINER
--    RPCs that write the table. Migration 139's own header notes the pre-117 rows carry
--    `granted_delta = purchased_delta = 0` beside a non-zero `delta` ("unknown split"), so
--    the constraint below exempts that shape and is added NOT VALID. ⚠️ The exemption is NOT
--    only historical: `add_credit_transaction` (called from credit_service.log_transaction)
--    still inserts without the split columns today, so it keeps minting (0, 0) rows that the
--    CHECK cannot judge and that `refund_credits` handles through its unknown-split fallback.
--    What the CHECK does bind is every row that RECORDS a split — the ones the refund
--    matcher actually reads. Closing the exemption means teaching add_credit_transaction to
--    record the split (a follow-up RPC change), then tightening this constraint.
--
--    ⚠️ VALIDATE is a separate, deliberate step. Run the count first; if it returns 0, run
--    the VALIDATE statement at the end of this file. If it does not, a post-139 row broke the
--    split and that is a bug to chase, not a row to grandfather.
--
--        SELECT count(*) FROM public.credit_transactions
--         WHERE delta <> granted_delta + purchased_delta
--           AND NOT (granted_delta = 0 AND purchased_delta = 0);
--
-- 2. Three columns sit inside UNIQUE keys that the backend uses as ON CONFLICT targets, and
--    all three are NULLABLE:
--        ticker_news_cache.external_id      UNIQUE (ticker, external_id)
--        news_articles.external_id          UNIQUE (…, external_id)
--        social_mentions_history.source     UNIQUE (ticker, snapshot_date, source)
--    Postgres treats NULLs as distinct in a plain UNIQUE constraint, so one NULL in a keyed
--    column both defeats the dedup AND makes `ON CONFLICT` never match — unbounded duplicate
--    cache rows. Latent today only because every writer forces a value
--    (`news_cache_service.py` falls back to `unknown_{i}`; `social_mentions_service.py`
--    hardcodes `'apewisdom'`). NOT NULL turns that contract into a constraint. Each column
--    is backfilled first so the ALTER cannot fail on a stray row. The social_mentions
--    backfill writes a CONSTANT into a UNIQUE key, so a NULL-source row that already has an
--    'apewisdom' twin for the same (ticker, snapshot_date) is DELETED (it is the duplicate
--    the constraint failed to stop) rather than colliding with it.
--
-- 3. COMMENTs. `refund_credits`'s COMMENT lists six outcomes; the body returns a seventh,
--    `capped_to_zero` — the one migration 142 added specifically because it means the user
--    is OWED and must page (design doc §9b.2). `user_credits`'s COMMENT says the invariants
--    "live in those functions, not in constraints"; six CHECK constraints on that very table
--    (115/117/140) say otherwise. A reader trusting either comment reasons wrongly about
--    what the database will reject.
--
-- Idempotent: the CHECK is added inside a DO block guarded on pg_constraint; UPDATE …
-- WHERE … IS NULL and SET NOT NULL are no-ops on a clean table; COMMENT ON is declarative.
-- The news_articles block is wrapped in a to_regclass guard so this file stays valid after
-- 168 drops that table.

-- ---- 1. Ledger split invariant (NOT VALID — see header) ----
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.credit_transactions'::regclass
           AND conname  = 'credit_transactions_split_sums'
    ) THEN
        ALTER TABLE public.credit_transactions
            ADD CONSTRAINT credit_transactions_split_sums
            CHECK (
                delta = granted_delta + purchased_delta
                OR (granted_delta = 0 AND purchased_delta = 0)   -- pre-117 "unknown split" rows
            ) NOT VALID;
    END IF;
END $$;

COMMENT ON CONSTRAINT credit_transactions_split_sums ON public.credit_transactions IS
    'delta must equal granted_delta + purchased_delta, except the pre-117 rows that recorded '
    'no split (both zero). refund_credits reverses the RECORDED split, so a row that '
    'violates this would refund the wrong pool. Added NOT VALID in 166; VALIDATE once the '
    'count in that migration''s header returns 0. The (0,0) exemption is still being written '
    'by add_credit_transaction (credit_service.log_transaction) — tighten it only after that '
    'RPC records the split.';

-- ---- 2. Nullable columns inside ON CONFLICT keys ----
UPDATE public.ticker_news_cache
   SET external_id = 'unknown_' || id::text
 WHERE external_id IS NULL;
ALTER TABLE public.ticker_news_cache
    ALTER COLUMN external_id SET NOT NULL;

-- Guarded: 168 drops this table, and the UPDATE alone would 42P01 on a re-run after that.
DO $$
BEGIN
    IF to_regclass('public.news_articles') IS NOT NULL THEN
        EXECUTE $q$UPDATE public.news_articles
                       SET external_id = 'unknown_' || id::text
                     WHERE external_id IS NULL$q$;
        EXECUTE 'ALTER TABLE public.news_articles ALTER COLUMN external_id SET NOT NULL';
    END IF;
END $$;

-- A NULL-source row whose (ticker, snapshot_date) already has an 'apewisdom' row IS the
-- duplicate the UNIQUE failed to stop; setting the constant on it would 23505. Drop it.
DELETE FROM public.social_mentions_history n
 WHERE n.source IS NULL
   AND EXISTS (SELECT 1 FROM public.social_mentions_history e
                WHERE e.ticker = n.ticker AND e.snapshot_date = n.snapshot_date
                  AND e.source = 'apewisdom');
UPDATE public.social_mentions_history
   SET source = 'apewisdom'
 WHERE source IS NULL;
ALTER TABLE public.social_mentions_history
    ALTER COLUMN source SET NOT NULL;

-- ---- 3. COMMENTs that contradicted the schema ----
COMMENT ON FUNCTION public.refund_credits(p_user_id uuid, p_amount integer, p_reason text, p_ref_id text) IS
    'Reverses the RECORDED split of a spend. Returns JSONB {outcome, refunded, spendable} as '
    'of migration 142 (was INTEGER spendable). outcome: refunded | already_refunded | '
    'no_matching_debit | capped_to_zero | guest | invalid | no_credits_row. TWO of those mean '
    'the user is OWED credits and credit_service.refund_ledgered logs them as a REFUND LEAK at '
    'ERROR: `no_matching_debit` (no debit matched this ref_id/amount) and `capped_to_zero` '
    '(the debit matched but the pools absorbed none of it — the month-boundary case, because '
    'ensure_credit_period resets `used`). `already_refunded` is an idempotent replay and '
    'must NOT page. Excludes pack_revoked / tier_revoked rows from the debit match (139) so a '
    'report refund can never reverse an Apple clawback.';

COMMENT ON TABLE public.user_credits IS
    'Credit balances, TWO POOLS: granted (total/used, monthly, use-it-or-lose-it) and '
    'purchased (purchased_total/purchased_used, from consumable IAP, NEVER expires per App '
    'Store Guideline 3.1.1). `spendable` is the sum and the number the API serves. '
    'SERVICE-ROLE ONLY: every write goes through a SECURITY DEFINER RPC (spend_credits / '
    'refund_credits / ensure_credit_period / grant_tier_upgrade / revoke_tier_credits / '
    'add_purchased_credits / revoke_purchased_credits). Do not GRANT to anon or '
    'authenticated. `remaining` and `spendable` are generated columns. The ORDERING and '
    'pool-selection invariants (spend granted first, refund the recorded split) live in '
    'those functions; the non-negativity and used <= total invariants are CHECK constraints '
    'on this table (115/117/140), so a direct UPDATE that breaks them fails with 23514 and '
    'an RPC that would must handle it. See migrations 115, 117, 139, 140, 166.';

-- ---- Run AFTER the header count returns 0 ----
-- ALTER TABLE public.credit_transactions VALIDATE CONSTRAINT credit_transactions_split_sums;
