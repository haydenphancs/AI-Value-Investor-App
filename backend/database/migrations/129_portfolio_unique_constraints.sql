-- 129_portfolio_unique_constraints.sql
--
-- Why: `portfolios_user_id_name_key UNIQUE (user_id, name)` exists in PRODUCTION and
-- in database/schema_snapshot.sql:8609-8613, but in NO migration file — it was added
-- out-of-band. Three code paths now depend on the 23505 it raises:
--
--   * users.py `_claim_portfolios` merges the guest's groups BY NAME and retries a
--     bounded re-read when the move collides with a concurrently-seeded "Holdings".
--   * users.py `_merge_portfolio_items` dedupes on (portfolio_id, ticker).
--   * portfolios.py `_seed_default_portfolio` adopts the race winner on 23505 —
--     and deliberately RE-RAISES a 23505 it cannot attribute to that constraint.
--
-- Without it on `portfolios`, those paths do not fail — they silently succeed and
-- leave two "Holdings" groups for one user, which is worse than the error they
-- replaced. An environment built from migrations/ alone is in exactly that state.
--
-- `portfolio_items` is a DIFFERENT case and this migration treats it differently.
-- 037_portfolios.sql:53 already declares `PRIMARY KEY (portfolio_id, ticker)`, so a
-- fresh environment DOES enforce that pair — just under the name
-- `portfolio_items_pkey`. Production instead carries a surrogate `id` PK plus a
-- separate `portfolio_items_portfolio_id_ticker_key` (snapshot:8589 and :8597).
-- Both are correct; only the NAME differs. So the guard below is written on the
-- COLUMN SET, not the name: it adds the constraint only where nothing already
-- enforces that pair, and no-ops on both prod and a 037-built database. Matching on
-- the name alone would add a redundant second unique index to every fresh env.
--
-- NOT closed by this migration (pre-existing drift, filed separately): production
-- `portfolio_items` also has `id uuid` and `added_at`, which no migration creates,
-- and `_merge_portfolio_items` selects/filters on `id`. A fresh env still diverges
-- there. Do not read this migration as achieving full portfolio_items parity.
--
-- Idempotent: a NO-OP against production. Postgres has no
-- `ADD CONSTRAINT IF NOT EXISTS`, hence the pg_constraint guards. The no-op path
-- takes NO table lock (regclass + syscache lookups only).
--
-- ⚠️ RUN THE PRE-CHECK FIRST on any database that might already hold duplicates.
-- `ADD CONSTRAINT` fails loudly if it does, and this migration deliberately does NOT
-- delete data — choosing which duplicate survives is a product decision.
--
--   SELECT user_id, name, count(*) FROM public.portfolios
--    GROUP BY 1, 2 HAVING count(*) > 1;
--
--   SELECT portfolio_id, ticker, count(*) FROM public.portfolio_items
--    GROUP BY 1, 2 HAVING count(*) > 1;
--
-- (Both constraint column sets are NOT NULL, so there is no NULL-skipping hole.
-- Matching is case-sensitive, which is consistent with `_claim_portfolios` comparing
-- names with an exact `==`. A `lower(name)` hunt will surface pairs the constraint
-- accepts by design.)
--
-- Apply manually via Supabase Studio or the supabase CLI (never auto-applied).

BEGIN;

-- ADD CONSTRAINT ... UNIQUE takes an ACCESS EXCLUSIVE lock and builds an index. The
-- hazard is the lock QUEUE, not the hold time: a pending ACCESS EXCLUSIVE blocks
-- every subsequent SELECT behind it, and GET /portfolios is on the app's launch hot
-- path (ContentView mounts every tab, so TrackingViewModel.init and the Home fetch
-- both hit it). Fail fast and roll back rather than stalling the Assets and Home
-- tabs behind one long-running reader.
SET LOCAL lock_timeout = '3s';

-- ── portfolios (user_id, name) ───────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.portfolios'::regclass
           AND c.contype IN ('p', 'u')
           AND (
                SELECT array_agg(a.attname::text ORDER BY a.attname)
                  FROM unnest(c.conkey) AS k(attnum)
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum
               ) = ARRAY['name', 'user_id']
    ) THEN
        RAISE NOTICE 'portfolios(user_id, name) already enforced — no-op';

    -- A bare UNIQUE INDEX of the same name (what a dashboard "add index" flow
    -- produces — and these WERE added out-of-band) leaves pg_constraint empty, so
    -- the check above passes and ADD CONSTRAINT then dies with 42P07. Promote the
    -- existing index instead of colliding with it.
    ELSIF EXISTS (
        SELECT 1 FROM pg_class
         WHERE relname = 'portfolios_user_id_name_key'
           AND relnamespace = 'public'::regnamespace
    ) THEN
        ALTER TABLE public.portfolios
            ADD CONSTRAINT portfolios_user_id_name_key
            UNIQUE USING INDEX portfolios_user_id_name_key;
        RAISE NOTICE 'Promoted the existing portfolios_user_id_name_key index to a constraint';

    ELSE
        ALTER TABLE public.portfolios
            ADD CONSTRAINT portfolios_user_id_name_key UNIQUE (user_id, name);
        RAISE NOTICE 'Added portfolios_user_id_name_key';
    END IF;
END $$;

-- ── portfolio_items (portfolio_id, ticker) ───────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.portfolio_items'::regclass
           AND c.contype IN ('p', 'u')
           AND (
                SELECT array_agg(a.attname::text ORDER BY a.attname)
                  FROM unnest(c.conkey) AS k(attnum)
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum
               ) = ARRAY['portfolio_id', 'ticker']
    ) THEN
        -- Prod: the named UNIQUE. A 037-built env: portfolio_items_pkey. Both fine.
        RAISE NOTICE 'portfolio_items(portfolio_id, ticker) already enforced — no-op';

    ELSIF EXISTS (
        SELECT 1 FROM pg_class
         WHERE relname = 'portfolio_items_portfolio_id_ticker_key'
           AND relnamespace = 'public'::regnamespace
    ) THEN
        ALTER TABLE public.portfolio_items
            ADD CONSTRAINT portfolio_items_portfolio_id_ticker_key
            UNIQUE USING INDEX portfolio_items_portfolio_id_ticker_key;
        RAISE NOTICE 'Promoted the existing portfolio_items unique index to a constraint';

    ELSE
        ALTER TABLE public.portfolio_items
            ADD CONSTRAINT portfolio_items_portfolio_id_ticker_key
            UNIQUE (portfolio_id, ticker);
        RAISE NOTICE 'Added portfolio_items_portfolio_id_ticker_key';
    END IF;
END $$;

COMMIT;
