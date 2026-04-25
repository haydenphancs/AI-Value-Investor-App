-- Merge portfolio_holdings into watchlist_items so the user's tracked tickers
-- (Assets tab) are the single source of truth for Portfolio Insights too.
--
-- After this migration, a watchlist row is "part of the user's portfolio for
-- insights" iff `shares` is set OR `market_value > 0`. Rows with both unset
-- are still tracked (price feed, alerts) but excluded from the diversification
-- score.
--
-- The old `portfolio_holdings` table is left intact for one release in case we
-- need to roll back. A follow-up migration will drop it.

ALTER TABLE watchlist_items
    ADD COLUMN IF NOT EXISTS shares       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS market_value DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sector       TEXT,
    ADD COLUMN IF NOT EXISTS asset_type   TEXT DEFAULT 'Stock',
    ADD COLUMN IF NOT EXISTS country      TEXT DEFAULT 'US';

COMMENT ON COLUMN watchlist_items.shares IS
    'Optional share count. When set, market_value is recomputed from FMP live price on read.';
COMMENT ON COLUMN watchlist_items.market_value IS
    'Optional dollar amount. Used as the holding value when shares is null, or as the cached value otherwise.';

-- Backfill from portfolio_holdings, if it exists.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'portfolio_holdings'
    ) THEN
        -- 1) Update overlapping rows (user already has the ticker on their watchlist)
        UPDATE watchlist_items w
        SET
            shares       = h.shares,
            market_value = h.market_value,
            sector       = COALESCE(w.sector,     h.sector),
            asset_type   = COALESCE(NULLIF(w.asset_type, ''), h.asset_type, 'Stock'),
            country      = COALESCE(NULLIF(w.country,    ''), h.country,    'US'),
            company_name = COALESCE(NULLIF(w.company_name, ''), h.company_name)
        FROM portfolio_holdings h
        WHERE w.user_id = h.user_id AND w.ticker = h.ticker;

        -- 2) Insert holdings that aren't on the user's watchlist yet so their
        --    existing portfolio data isn't lost.
        INSERT INTO watchlist_items
            (user_id, ticker, company_name, shares, market_value, sector, asset_type, country)
        SELECT
            h.user_id, h.ticker, h.company_name, h.shares, h.market_value,
            h.sector, COALESCE(h.asset_type, 'Stock'), COALESCE(h.country, 'US')
        FROM portfolio_holdings h
        WHERE NOT EXISTS (
            SELECT 1 FROM watchlist_items w
            WHERE w.user_id = h.user_id AND w.ticker = h.ticker
        );
    END IF;
END $$;
