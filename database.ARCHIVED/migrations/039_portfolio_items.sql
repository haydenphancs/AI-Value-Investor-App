-- Migration 039: portfolio_items table
--
-- Per-portfolio ticker list. Tickers must already exist in the user's master
-- watchlist_items table — the service layer enforces this rather than a FK
-- because watchlist_items has its own (user_id, ticker) uniqueness and
-- portfolio_items.ticker is just a denormalized symbol string.
--
-- `position` controls intra-portfolio ordering (used when sort = "Date Added"
-- in the iOS Tracking screen). Updated whenever PUT /portfolios/{id}/tickers
-- replaces the membership list.

CREATE TABLE IF NOT EXISTS portfolio_items (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portfolio_id  UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker        TEXT NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(portfolio_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_items_portfolio
    ON portfolio_items(portfolio_id, position);

ALTER TABLE portfolio_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY portfolio_items_owner ON portfolio_items
    USING (
        portfolio_id IN (
            SELECT id FROM portfolios WHERE user_id = auth.uid()
        )
    );
