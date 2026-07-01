-- 076_whale_trades_congress_range.sql
--
-- Why: congressional (STOCK Act) trades are legally disclosed ONLY as dollar
-- RANGES (e.g. "$1,001 - $15,000") on a 30-45 day lag — never an exact figure.
-- The pipeline collapses that range to a midpoint for internal sort/aggregation
-- math, but the whale_trades table stored ONLY that midpoint, so the profile's
-- "Recent Trades", the "Whales Sold" alert, and the AI sentiment all rendered a
-- fabricated-precision dollar amount (e.g. "$455K") for a Senator, and dated the
-- trades by a "now" stamp (making disclosures look like they happened Today /
-- Yesterday). This migration lets us persist the honest range + real disclosure
-- date per trade so the UI can show "$50K - $250K" and "Traded Jun 1 · Disclosed
-- Jun 30". 13F institutional trades leave both columns NULL (their dollar amount
-- is real: shares x implied price), so their display is unchanged.
--
-- Schema:
--   whale_trades.amount_range     TEXT  -- raw STOCK Act bucket (congress only)
--   whale_trades.disclosure_date  TEXT  -- ISO date the trade became public
--
-- Cleanup: the pre-fix daily/6-hourly hydration wrote a NEW trade group dated
-- "now" on every run, so whale_trade_groups accumulated near-identical rows dated
-- today / yesterday / 2-days-ago for the SAME underlying congressional trades.
-- We delete the existing congressional groups + trades (and their snapshots) so
-- the next hydration run rebuilds them keyed by real disclosure dates. This data
-- is fully regenerable from FMP — no user data is lost.
--
-- Idempotent: safe to re-apply. Apply manually (Supabase Studio / CLI).
-- After applying, re-run:  cd backend && python -m scripts.hydrate_whales --force

-- ── 1. New columns (nullable; 13F leaves them NULL) ────────────────────
ALTER TABLE whale_trades
    ADD COLUMN IF NOT EXISTS amount_range TEXT;

ALTER TABLE whale_trades
    ADD COLUMN IF NOT EXISTS disclosure_date TEXT;

-- ── 2. Cleanup of the fabricated daily-duplicate congressional groups ──
-- DESTRUCTIVE: removes congressional whale_trades / whale_trade_groups /
-- whale_filing_snapshots. All are regenerable from FMP via the hydration job;
-- no user-authored data lives in these tables. 13F (institutional) rows are
-- left untouched (data_source = '13f').
DELETE FROM whale_trades
WHERE whale_id IN (
    SELECT id FROM whales
    WHERE data_source IN ('congressional_house', 'congressional_senate')
);

DELETE FROM whale_trade_groups
WHERE whale_id IN (
    SELECT id FROM whales
    WHERE data_source IN ('congressional_house', 'congressional_senate')
);

DELETE FROM whale_filing_snapshots
WHERE whale_id IN (
    SELECT id FROM whales
    WHERE data_source IN ('congressional_house', 'congressional_senate')
);

-- Drop stale assembled-profile JSON so the rebuilt profile is served fresh.
-- Guarded: whale_profile_cache may not exist in every environment.
DO $$
BEGIN
    IF to_regclass('public.whale_profile_cache') IS NOT NULL THEN
        DELETE FROM whale_profile_cache
        WHERE whale_id IN (
            SELECT id FROM whales
            WHERE data_source IN ('congressional_house', 'congressional_senate')
        );
    END IF;
END $$;
