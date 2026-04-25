-- Add `shares` column to portfolio_holdings so the backend can refresh
-- `market_value` against the current FMP price on every read, instead of
-- storing a static dollar amount that goes stale as the market moves.
--
-- Contract:
--   - shares NULL  → manual mode. `market_value` is whatever the user
--                    entered and is never refreshed.
--   - shares set   → refreshed mode. The row's `market_value` in the DB is
--                    the last cached value; the API recomputes
--                    market_value = shares * current_price on every GET.
--
-- Existing rows keep `shares = NULL` (manual mode) so the migration is
-- backward-compatible. New rows added via POST /tracking/holdings can pass
-- `shares` or `market_value` (or both).

ALTER TABLE portfolio_holdings
    ADD COLUMN IF NOT EXISTS shares DOUBLE PRECISION;

COMMENT ON COLUMN portfolio_holdings.shares IS
    'Share count. When set, market_value is recomputed from FMP live price on read.';
