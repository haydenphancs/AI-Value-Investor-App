-- 129_portfolio_unique_constraints.sql
--
-- Why: two UNIQUE constraints exist in PRODUCTION and in
-- database/schema_snapshot.sql, but in NO migration file — they were added
-- out-of-band via the dashboard/psql:
--
--   * portfolios.portfolios_user_id_name_key            UNIQUE (user_id, name)
--   * portfolio_items.portfolio_items_portfolio_id_ticker_key
--                                                       UNIQUE (portfolio_id, ticker)
--
-- Three code paths now depend on them for CORRECTNESS, not just hygiene, because
-- each one keys off the 23505 those constraints raise:
--
--   * users.py `_claim_portfolios` merges the guest's groups BY NAME and retries a
--     bounded re-read when the move collides with a concurrently-seeded "Holdings".
--   * users.py `_merge_portfolio_items` dedupes on (portfolio_id, ticker).
--   * portfolios.py `_seed_default_portfolio` adopts the race winner on 23505.
--
-- Without the constraints those paths do not fail — they silently succeed and leave
-- DUPLICATE rows (two "Holdings" groups, a ticker listed twice), which is strictly
-- worse than the error they replaced. A fresh environment built from migrations/
-- alone would be in exactly that state today.
--
-- Idempotent: a NO-OP against production, where both constraints already exist.
-- Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, hence the pg_constraint guards.
--
-- ⚠️ PRE-CHECK ON ANY DATABASE THAT MIGHT ALREADY HOLD DUPLICATES. `ADD CONSTRAINT`
-- fails loudly if it does, and this migration deliberately does NOT delete data —
-- choosing which duplicate survives is a product decision, not a migration's.
-- Find them first:
--
--   SELECT user_id, name, count(*) FROM public.portfolios
--    GROUP BY 1, 2 HAVING count(*) > 1;
--
--   SELECT portfolio_id, ticker, count(*) FROM public.portfolio_items
--    GROUP BY 1, 2 HAVING count(*) > 1;
--
-- Apply manually via Supabase Studio or the supabase CLI (never auto-applied).

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.portfolios'::regclass
           AND conname  = 'portfolios_user_id_name_key'
    ) THEN
        ALTER TABLE public.portfolios
            ADD CONSTRAINT portfolios_user_id_name_key UNIQUE (user_id, name);
        RAISE NOTICE 'Added portfolios_user_id_name_key';
    ELSE
        RAISE NOTICE 'portfolios_user_id_name_key already present — no-op';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.portfolio_items'::regclass
           AND conname  = 'portfolio_items_portfolio_id_ticker_key'
    ) THEN
        ALTER TABLE public.portfolio_items
            ADD CONSTRAINT portfolio_items_portfolio_id_ticker_key
            UNIQUE (portfolio_id, ticker);
        RAISE NOTICE 'Added portfolio_items_portfolio_id_ticker_key';
    ELSE
        RAISE NOTICE 'portfolio_items_portfolio_id_ticker_key already present — no-op';
    END IF;
END $$;

COMMIT;
