-- 047_data_integrity_hardening.sql
--
-- Why: Staff DBA review of schema_snapshot.sql surfaced five risk classes:
--   1. `double precision` used for money / shares / ratios — fix with NUMERIC
--      to prevent floating-point drift on currency math.
--   2. Four user-owned tables (chat_sessions, portfolios, portfolio_holdings,
--      watchlist_items) declared user_id NOT NULL but had NO FK to users(id) —
--      orphaned rows on account deletion. Add FKs with ON DELETE CASCADE.
--   3. Bare `numeric` (no precision) on 12 whale/sector columns — pin scale
--      to lock invariants and document intent.
--   4. No CHECK constraints enforcing the obvious business rules
--      (non-negative dollar amounts, 0–100% allocations, non-negative
--      counters). Add them DB-side so bugs surface at write-time.
--   5. Index hygiene — drop two redundant portfolios indexes (idx_portfolios_user
--      and idx_portfolios_user_id, both superseded by idx_portfolios_user_sort),
--      add hot-path indexes for the whale hydration job and alert cleanup.
--
-- This migration is idempotent (IF [NOT] EXISTS everywhere) and safe to
-- replay. CHECK constraints and FK additions use NOT VALID + VALIDATE to
-- minimize lock duration on existing rows.
--
-- ===== APPLY-TIME RISKS — read before running ==============================
--
-- A. AccessExclusive locks on table rewrites. Sections 1 and 2 retype columns
--    (double precision → numeric, bare numeric → numeric(p,s)). Postgres
--    cannot skip the rewrite for these conversions, so each ALTER scans and
--    rewrites the whole table under an AccessExclusive lock. Apply during a
--    low-traffic window. Affected tables: hedge_fund_quarters,
--    portfolio_holdings, portfolio_items, watchlist_items, sector_benchmarks,
--    sector_aggregates, whales, whale_filing_snapshots, whale_holdings,
--    whale_sector_allocations, whale_trade_groups, whale_trades.
--
-- B. Cast-overflow risk on tight numerics. If any current row exceeds the new
--    precision the cast errors out before any CHECK runs:
--      - cagr_5yr_pct numeric(9,4)  ⇒  |val| < 100000
--      - top1/top2_share_pct, whale_*.allocation, whale_trades.*_allocation
--        numeric(7,4)               ⇒  |val| < 1000
--      - whale_holdings.change_percent, whales.ytd_return numeric(9,4)
--        ⇒  |val| < 100000
--    Run a quick MAX(ABS(col)) pre-flight on each before applying.
--
-- C. CHECK / FK VALIDATE failures. The migration assumes existing data already
--    satisfies the new constraints. If VALIDATE errors, common culprits:
--      - Orphaned user_id rows in chat_sessions / portfolios / portfolio_holdings
--        / watchlist_items pointing to deleted auth users.
--      - Negative `used` in user_credits from a refund bug.
--      - whale_trades.amount stored as a signed value (sell = negative).
--    Pre-flight queries are documented in plans/047... and must be run first.
-- ============================================================================

BEGIN;

-- =============================================================================
-- 1. FLOAT → NUMERIC: monetary values, share counts, financial ratios
-- =============================================================================

-- hedge_fund_quarters: dollar volumes and net flows must be exact
ALTER TABLE public.hedge_fund_quarters
    ALTER COLUMN buy_volume  TYPE numeric(20,2) USING buy_volume::numeric(20,2),
    ALTER COLUMN sell_volume TYPE numeric(20,2) USING sell_volume::numeric(20,2),
    ALTER COLUMN net_flow    TYPE numeric(20,2) USING net_flow::numeric(20,2);

-- portfolio_holdings: fractional shares allowed (broker APIs support 4dp)
ALTER TABLE public.portfolio_holdings
    ALTER COLUMN shares TYPE numeric(20,4) USING shares::numeric(20,4);

-- portfolio_items: shares + market_value
ALTER TABLE public.portfolio_items
    ALTER COLUMN shares       TYPE numeric(20,4) USING shares::numeric(20,4),
    ALTER COLUMN market_value TYPE numeric(20,2) USING market_value::numeric(20,2);

-- watchlist_items: shares + market_value (same intent as portfolio_items)
ALTER TABLE public.watchlist_items
    ALTER COLUMN shares       TYPE numeric(20,4) USING shares::numeric(20,4),
    ALTER COLUMN market_value TYPE numeric(20,2) USING market_value::numeric(20,2);

-- sector_benchmarks: median_value holds many metric types (ratios, %, $);
-- 20,6 is wide enough for all and still exact.
ALTER TABLE public.sector_benchmarks
    ALTER COLUMN median_value TYPE numeric(20,6) USING median_value::numeric(20,6);


-- =============================================================================
-- 2. Bare NUMERIC → typed NUMERIC(p,s): pin scale on whale/sector financial cols
-- =============================================================================

-- sector_aggregates: revenue is USD, *_pct are percentages, hhi is 0–10000
ALTER TABLE public.sector_aggregates
    ALTER COLUMN total_revenue_usd TYPE numeric(24,2) USING total_revenue_usd::numeric(24,2),
    ALTER COLUMN cagr_5yr_pct      TYPE numeric(9,4)  USING cagr_5yr_pct::numeric(9,4),
    ALTER COLUMN hhi               TYPE numeric(10,4) USING hhi::numeric(10,4),
    ALTER COLUMN top1_share_pct    TYPE numeric(7,4)  USING top1_share_pct::numeric(7,4),
    ALTER COLUMN top2_share_pct    TYPE numeric(7,4)  USING top2_share_pct::numeric(7,4);

-- whales: portfolio_value is USD, ytd_return is %
ALTER TABLE public.whales
    ALTER COLUMN portfolio_value TYPE numeric(24,2) USING portfolio_value::numeric(24,2),
    ALTER COLUMN ytd_return      TYPE numeric(9,4)  USING ytd_return::numeric(9,4);

-- whale_filing_snapshots: total_value is USD
ALTER TABLE public.whale_filing_snapshots
    ALTER COLUMN total_value TYPE numeric(24,2) USING total_value::numeric(24,2);

-- whale_holdings: allocation 0–100 (%), change_percent signed %
ALTER TABLE public.whale_holdings
    ALTER COLUMN allocation     TYPE numeric(7,4) USING allocation::numeric(7,4),
    ALTER COLUMN change_percent TYPE numeric(9,4) USING change_percent::numeric(9,4);

-- whale_sector_allocations: allocation 0–100 (%)
ALTER TABLE public.whale_sector_allocations
    ALTER COLUMN allocation TYPE numeric(7,4) USING allocation::numeric(7,4);

-- whale_trade_groups: net_amount is USD
ALTER TABLE public.whale_trade_groups
    ALTER COLUMN net_amount TYPE numeric(24,2) USING net_amount::numeric(24,2);

-- whale_trades: amount is USD, allocations are %
ALTER TABLE public.whale_trades
    ALTER COLUMN amount              TYPE numeric(24,2) USING amount::numeric(24,2),
    ALTER COLUMN previous_allocation TYPE numeric(7,4)  USING previous_allocation::numeric(7,4),
    ALTER COLUMN new_allocation      TYPE numeric(7,4)  USING new_allocation::numeric(7,4);


-- =============================================================================
-- 3. MISSING FOREIGN KEYS: prevent orphaned user-owned data on account delete
--    Pattern: ADD ... NOT VALID, then VALIDATE — avoids long lock at add time.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chat_sessions_user_id_fkey'
          AND conrelid = 'public.chat_sessions'::regclass
    ) THEN
        ALTER TABLE public.chat_sessions
            ADD CONSTRAINT chat_sessions_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id)
            ON DELETE CASCADE
            NOT VALID;
        ALTER TABLE public.chat_sessions VALIDATE CONSTRAINT chat_sessions_user_id_fkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'portfolios_user_id_fkey'
          AND conrelid = 'public.portfolios'::regclass
    ) THEN
        ALTER TABLE public.portfolios
            ADD CONSTRAINT portfolios_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id)
            ON DELETE CASCADE
            NOT VALID;
        ALTER TABLE public.portfolios VALIDATE CONSTRAINT portfolios_user_id_fkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'portfolio_holdings_user_id_fkey'
          AND conrelid = 'public.portfolio_holdings'::regclass
    ) THEN
        ALTER TABLE public.portfolio_holdings
            ADD CONSTRAINT portfolio_holdings_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id)
            ON DELETE CASCADE
            NOT VALID;
        ALTER TABLE public.portfolio_holdings VALIDATE CONSTRAINT portfolio_holdings_user_id_fkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'watchlist_items_user_id_fkey'
          AND conrelid = 'public.watchlist_items'::regclass
    ) THEN
        ALTER TABLE public.watchlist_items
            ADD CONSTRAINT watchlist_items_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id)
            ON DELETE CASCADE
            NOT VALID;
        ALTER TABLE public.watchlist_items VALIDATE CONSTRAINT watchlist_items_user_id_fkey;
    END IF;
END $$;


-- =============================================================================
-- 4. CHECK CONSTRAINTS: enforce business invariants DB-side
-- =============================================================================

-- user_credits: balances cannot go negative
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'user_credits_total_nonneg') THEN
        ALTER TABLE public.user_credits
            ADD CONSTRAINT user_credits_total_nonneg CHECK (total >= 0) NOT VALID;
        ALTER TABLE public.user_credits VALIDATE CONSTRAINT user_credits_total_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'user_credits_used_nonneg') THEN
        ALTER TABLE public.user_credits
            ADD CONSTRAINT user_credits_used_nonneg CHECK (used >= 0) NOT VALID;
        ALTER TABLE public.user_credits VALIDATE CONSTRAINT user_credits_used_nonneg;
    END IF;
END $$;

-- portfolio_holdings: market_value is NOT NULL DEFAULT 0; shares is nullable
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'portfolio_holdings_market_value_nonneg') THEN
        ALTER TABLE public.portfolio_holdings
            ADD CONSTRAINT portfolio_holdings_market_value_nonneg CHECK (market_value >= 0) NOT VALID;
        ALTER TABLE public.portfolio_holdings VALIDATE CONSTRAINT portfolio_holdings_market_value_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'portfolio_holdings_shares_nonneg') THEN
        ALTER TABLE public.portfolio_holdings
            ADD CONSTRAINT portfolio_holdings_shares_nonneg CHECK (shares IS NULL OR shares >= 0) NOT VALID;
        ALTER TABLE public.portfolio_holdings VALIDATE CONSTRAINT portfolio_holdings_shares_nonneg;
    END IF;
END $$;

-- portfolio_items: both nullable
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'portfolio_items_shares_nonneg') THEN
        ALTER TABLE public.portfolio_items
            ADD CONSTRAINT portfolio_items_shares_nonneg CHECK (shares IS NULL OR shares >= 0) NOT VALID;
        ALTER TABLE public.portfolio_items VALIDATE CONSTRAINT portfolio_items_shares_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'portfolio_items_market_value_nonneg') THEN
        ALTER TABLE public.portfolio_items
            ADD CONSTRAINT portfolio_items_market_value_nonneg CHECK (market_value IS NULL OR market_value >= 0) NOT VALID;
        ALTER TABLE public.portfolio_items VALIDATE CONSTRAINT portfolio_items_market_value_nonneg;
    END IF;
END $$;

-- watchlist_items: both nullable
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_items_shares_nonneg') THEN
        ALTER TABLE public.watchlist_items
            ADD CONSTRAINT watchlist_items_shares_nonneg CHECK (shares IS NULL OR shares >= 0) NOT VALID;
        ALTER TABLE public.watchlist_items VALIDATE CONSTRAINT watchlist_items_shares_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_items_market_value_nonneg') THEN
        ALTER TABLE public.watchlist_items
            ADD CONSTRAINT watchlist_items_market_value_nonneg CHECK (market_value IS NULL OR market_value >= 0) NOT VALID;
        ALTER TABLE public.watchlist_items VALIDATE CONSTRAINT watchlist_items_market_value_nonneg;
    END IF;
END $$;

-- whale_holdings: allocation is a percentage 0–100
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'whale_holdings_allocation_range') THEN
        ALTER TABLE public.whale_holdings
            ADD CONSTRAINT whale_holdings_allocation_range CHECK (allocation >= 0 AND allocation <= 100) NOT VALID;
        ALTER TABLE public.whale_holdings VALIDATE CONSTRAINT whale_holdings_allocation_range;
    END IF;
END $$;

-- whale_sector_allocations: allocation is a percentage 0–100
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'whale_sector_allocations_allocation_range') THEN
        ALTER TABLE public.whale_sector_allocations
            ADD CONSTRAINT whale_sector_allocations_allocation_range CHECK (allocation >= 0 AND allocation <= 100) NOT VALID;
        ALTER TABLE public.whale_sector_allocations VALIDATE CONSTRAINT whale_sector_allocations_allocation_range;
    END IF;
END $$;

-- whale_trades / whale_trade_groups: dollar amounts cannot be negative
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'whale_trades_amount_nonneg') THEN
        ALTER TABLE public.whale_trades
            ADD CONSTRAINT whale_trades_amount_nonneg CHECK (amount >= 0) NOT VALID;
        ALTER TABLE public.whale_trades VALIDATE CONSTRAINT whale_trades_amount_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'whale_trade_groups_net_amount_nonneg') THEN
        ALTER TABLE public.whale_trade_groups
            ADD CONSTRAINT whale_trade_groups_net_amount_nonneg CHECK (net_amount >= 0) NOT VALID;
        ALTER TABLE public.whale_trade_groups VALIDATE CONSTRAINT whale_trade_groups_net_amount_nonneg;
    END IF;
END $$;

-- hedge_fund_quarters: volumes / counts non-negative, quarter in [1,4]
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hedge_fund_quarters_buy_volume_nonneg') THEN
        ALTER TABLE public.hedge_fund_quarters
            ADD CONSTRAINT hedge_fund_quarters_buy_volume_nonneg CHECK (buy_volume >= 0) NOT VALID;
        ALTER TABLE public.hedge_fund_quarters VALIDATE CONSTRAINT hedge_fund_quarters_buy_volume_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hedge_fund_quarters_sell_volume_nonneg') THEN
        ALTER TABLE public.hedge_fund_quarters
            ADD CONSTRAINT hedge_fund_quarters_sell_volume_nonneg CHECK (sell_volume >= 0) NOT VALID;
        ALTER TABLE public.hedge_fund_quarters VALIDATE CONSTRAINT hedge_fund_quarters_sell_volume_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hedge_fund_quarters_buyers_count_nonneg') THEN
        ALTER TABLE public.hedge_fund_quarters
            ADD CONSTRAINT hedge_fund_quarters_buyers_count_nonneg CHECK (buyers_count >= 0) NOT VALID;
        ALTER TABLE public.hedge_fund_quarters VALIDATE CONSTRAINT hedge_fund_quarters_buyers_count_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hedge_fund_quarters_sellers_count_nonneg') THEN
        ALTER TABLE public.hedge_fund_quarters
            ADD CONSTRAINT hedge_fund_quarters_sellers_count_nonneg CHECK (sellers_count >= 0) NOT VALID;
        ALTER TABLE public.hedge_fund_quarters VALIDATE CONSTRAINT hedge_fund_quarters_sellers_count_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hedge_fund_quarters_quarter_range') THEN
        ALTER TABLE public.hedge_fund_quarters
            ADD CONSTRAINT hedge_fund_quarters_quarter_range CHECK (quarter BETWEEN 1 AND 4) NOT VALID;
        ALTER TABLE public.hedge_fund_quarters VALIDATE CONSTRAINT hedge_fund_quarters_quarter_range;
    END IF;
END $$;

-- whales: portfolio_value and followers_count non-negative
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'whales_portfolio_value_nonneg') THEN
        ALTER TABLE public.whales
            ADD CONSTRAINT whales_portfolio_value_nonneg CHECK (portfolio_value IS NULL OR portfolio_value >= 0) NOT VALID;
        ALTER TABLE public.whales VALIDATE CONSTRAINT whales_portfolio_value_nonneg;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'whales_followers_count_nonneg') THEN
        ALTER TABLE public.whales
            ADD CONSTRAINT whales_followers_count_nonneg CHECK (followers_count >= 0) NOT VALID;
        ALTER TABLE public.whales VALIDATE CONSTRAINT whales_followers_count_nonneg;
    END IF;
END $$;

-- sector_aggregates: percentage columns 0–100
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sector_aggregates_top1_share_pct_range') THEN
        ALTER TABLE public.sector_aggregates
            ADD CONSTRAINT sector_aggregates_top1_share_pct_range CHECK (top1_share_pct IS NULL OR (top1_share_pct >= 0 AND top1_share_pct <= 100)) NOT VALID;
        ALTER TABLE public.sector_aggregates VALIDATE CONSTRAINT sector_aggregates_top1_share_pct_range;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sector_aggregates_top2_share_pct_range') THEN
        ALTER TABLE public.sector_aggregates
            ADD CONSTRAINT sector_aggregates_top2_share_pct_range CHECK (top2_share_pct IS NULL OR (top2_share_pct >= 0 AND top2_share_pct <= 100)) NOT VALID;
        ALTER TABLE public.sector_aggregates VALIDATE CONSTRAINT sector_aggregates_top2_share_pct_range;
    END IF;
END $$;

-- sector_benchmarks: sample_size non-negative
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sector_benchmarks_sample_size_nonneg') THEN
        ALTER TABLE public.sector_benchmarks
            ADD CONSTRAINT sector_benchmarks_sample_size_nonneg CHECK (sample_size >= 0) NOT VALID;
        ALTER TABLE public.sector_benchmarks VALIDATE CONSTRAINT sector_benchmarks_sample_size_nonneg;
    END IF;
END $$;

-- research_reports: fair_value_estimate non-negative when present
-- (overall_score and progress already have CHECK constraints).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'research_reports_fair_value_nonneg') THEN
        ALTER TABLE public.research_reports
            ADD CONSTRAINT research_reports_fair_value_nonneg CHECK (fair_value_estimate IS NULL OR fair_value_estimate >= 0) NOT VALID;
        ALTER TABLE public.research_reports VALIDATE CONSTRAINT research_reports_fair_value_nonneg;
    END IF;
END $$;


-- =============================================================================
-- 5. INDEX HYGIENE
-- =============================================================================

-- Drop two of three overlapping portfolios indexes; keep the composite
-- (user_id, sort_order). Plain user_id queries still benefit via leading-column
-- rule.
DROP INDEX IF EXISTS public.idx_portfolios_user;     -- (user_id, sort_order) — superseded duplicate
DROP INDEX IF EXISTS public.idx_portfolios_user_id;  -- (user_id) — covered by composite
-- Keep: idx_portfolios_user_sort (user_id, sort_order)

-- Whale hydration job filters by last_hydrated_at; add a plain BTREE.
CREATE INDEX IF NOT EXISTS idx_whales_last_hydrated_at
    ON public.whales (last_hydrated_at NULLS FIRST);

-- whale_alerts cleanup query: WHERE expires_at < now().
CREATE INDEX IF NOT EXISTS idx_whale_alerts_expires_at
    ON public.whale_alerts (expires_at)
    WHERE expires_at IS NOT NULL;

-- whale_alerts.whale_id is referenced for "alerts for a given whale" lookups
-- but not indexed.
CREATE INDEX IF NOT EXISTS idx_whale_alerts_whale
    ON public.whale_alerts (whale_id)
    WHERE whale_id IS NOT NULL;

COMMIT;
