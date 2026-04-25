-- Named portfolios (groupings of tickers from the user's watchlist).
-- Referenced by: app/api/v1/endpoints/portfolios.py

CREATE TABLE IF NOT EXISTS portfolios (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     UUID NOT NULL,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portfolios_user_id
    ON portfolios (user_id);
CREATE INDEX IF NOT EXISTS idx_portfolios_user_sort
    ON portfolios (user_id, sort_order);

ALTER TABLE portfolios ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users manage own portfolios" ON portfolios;
CREATE POLICY "Users manage own portfolios"
    ON portfolios FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Service role full access on portfolios" ON portfolios;
CREATE POLICY "Service role full access on portfolios"
    ON portfolios FOR ALL TO service_role
    USING (true) WITH CHECK (true);

GRANT ALL ON portfolios TO service_role;
GRANT ALL ON portfolios TO authenticated;


-- Ticker membership inside each portfolio. Position drives display order.
-- ON DELETE CASCADE is required: delete_portfolio in the endpoint deletes
-- the parent row directly without a separate items-cleanup call.

CREATE TABLE IF NOT EXISTS portfolio_items (
    portfolio_id  UUID NOT NULL
        REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker        TEXT NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (portfolio_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_items_portfolio
    ON portfolio_items (portfolio_id, position);

ALTER TABLE portfolio_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users manage own portfolio items" ON portfolio_items;
CREATE POLICY "Users manage own portfolio items"
    ON portfolio_items FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM portfolios p
            WHERE p.id = portfolio_items.portfolio_id
              AND p.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM portfolios p
            WHERE p.id = portfolio_items.portfolio_id
              AND p.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS "Service role full access on portfolio_items" ON portfolio_items;
CREATE POLICY "Service role full access on portfolio_items"
    ON portfolio_items FOR ALL TO service_role
    USING (true) WITH CHECK (true);

GRANT ALL ON portfolio_items TO service_role;
GRANT ALL ON portfolio_items TO authenticated;
