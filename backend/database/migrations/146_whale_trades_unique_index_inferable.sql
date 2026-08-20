-- 146_whale_trades_unique_index_inferable.sql
--
-- Why: migration 143's unique index is PARTIAL, and a partial index cannot be inferred by
-- `ON CONFLICT (columns)`. That silently defeated 143's entire purpose in production.
--
-- 143 created:
--     CREATE UNIQUE INDEX uq_whale_trades_group_ticker_action_date
--         ON whale_trades (trade_group_id, ticker, action, date)
--         WHERE (trade_group_id IS NOT NULL);        -- ← this predicate is the problem
--
-- PostgreSQL will only infer a partial index when the statement ALSO supplies the index
-- predicate (`ON CONFLICT (cols) WHERE trade_group_id IS NOT NULL`). PostgREST's
-- `on_conflict=` parameter takes a bare column list and cannot express a predicate, so
-- `hydrate_whales._upsert_trade`'s upsert failed with 42P10 on every call. Its fallback then
-- misread 42P10 as "migration 143 is not applied yet" and issued a plain INSERT, which hit
-- the very index that could not be inferred — 23505.
--
-- Measured in the Railway log on 2026-08-19, one hydration cycle: 12 × 42P10 fallback
-- warnings followed by 24 × 23505 "Failed to sync trade group". Net effect: whale trade
-- hydration could not top up or repair ANY existing trade group — exactly the "REPAIR run
-- tops up a partially written group" behaviour 143 was written to deliver.
--
-- Fix: drop the predicate. It was never load-bearing. `trade_group_id` is nullable, but a
-- unique btree treats every NULL as DISTINCT, so rows with a NULL group never conflict with
-- each other whether they are indexed or not. The predicate therefore bought nothing and cost
-- ON CONFLICT inference. Verified against production before writing this: 0 rows have a NULL
-- `trade_group_id`, and 0 duplicate (trade_group_id, ticker, action, date) groups exist, so
-- the non-partial index builds cleanly and no row changes meaning.
--
-- Note the index KEEPS ITS NAME, so 143's dedupe step and every reference to it stay valid.
--
-- Idempotent: safe to run more than once.

-- DESTRUCTIVE (briefly): drops a unique index and immediately recreates it over the same
-- columns. No data is deleted. There is a sub-second window with no uniqueness enforcement;
-- acceptable here because the only writer is the hydration job, which is idempotent by
-- construction once this lands. Not CONCURRENTLY: that cannot run inside a transaction, and
-- this table is small (~6k rows).
DROP INDEX IF EXISTS public.uq_whale_trades_group_ticker_action_date;

CREATE UNIQUE INDEX IF NOT EXISTS uq_whale_trades_group_ticker_action_date
    ON public.whale_trades (trade_group_id, ticker, action, date);

COMMENT ON INDEX public.uq_whale_trades_group_ticker_action_date IS
    'Natural identity of a whale trade. Deliberately NOT partial: a partial unique index '
    'cannot be inferred by ON CONFLICT (cols), which is the only form PostgREST can emit.';
