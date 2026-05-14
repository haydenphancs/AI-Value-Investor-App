-- Per-portfolio holding values. Lets each named portfolio carry its own
-- shares / market_value per ticker, instead of sharing the master
-- watchlist_items.shares for every portfolio that contains the ticker
-- (e.g. GOOGL with 10 shares set in "Holdings" no longer leaks into
-- "Tech" when the user adds GOOGL to that portfolio too).
--
-- Referenced by: app/api/v1/endpoints/portfolios.py — the diversification
-- score reads from these columns; watchlist_items.shares is left intact for
-- backwards compatibility but is no longer the source of truth for Insights.

ALTER TABLE portfolio_items
    ADD COLUMN IF NOT EXISTS shares       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS market_value DOUBLE PRECISION;

-- Backfill from watchlist_items so existing users don't lose the holding
-- values they already entered. For each existing portfolio_item, copy
-- shares / market_value from the matching watchlist row (same user, same
-- ticker). After this point the per-portfolio columns win.
UPDATE portfolio_items pi
SET
    shares       = wi.shares,
    market_value = wi.market_value
FROM portfolios p, watchlist_items wi
WHERE pi.portfolio_id = p.id
  AND wi.user_id = p.user_id
  AND wi.ticker = pi.ticker
  AND (wi.shares IS NOT NULL OR wi.market_value IS NOT NULL);
