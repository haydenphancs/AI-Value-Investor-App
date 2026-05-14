-- Watchlist items table: stores user's tracked stock tickers
-- Referenced by: watchlist.py endpoints, tracking_service.py

CREATE TABLE IF NOT EXISTS watchlist_items (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    logo_url TEXT,
    added_at TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_watchlist_user_ticker UNIQUE (user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_items_user_id
    ON watchlist_items (user_id);

CREATE INDEX IF NOT EXISTS idx_watchlist_items_ticker
    ON watchlist_items (ticker);

-- Enable RLS but allow service_role full access (Supabase service_role bypasses RLS by default)
ALTER TABLE watchlist_items ENABLE ROW LEVEL SECURITY;

-- Policy: users can only see/modify their own watchlist items
CREATE POLICY IF NOT EXISTS "Users manage own watchlist"
    ON watchlist_items
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Allow service_role full access (for backend server operations)
CREATE POLICY IF NOT EXISTS "Service role full access on watchlist"
    ON watchlist_items
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

GRANT ALL ON watchlist_items TO service_role;
GRANT ALL ON watchlist_items TO authenticated;


-- Portfolio holdings table: stores user's portfolio for diversification scoring
-- Referenced by: tracking.py holdings CRUD endpoints

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    market_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    sector TEXT,
    asset_type TEXT DEFAULT 'Stock',
    country TEXT DEFAULT 'US',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_holdings_user_ticker UNIQUE (user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_user_id
    ON portfolio_holdings (user_id);

ALTER TABLE portfolio_holdings ENABLE ROW LEVEL SECURITY;

CREATE POLICY IF NOT EXISTS "Users manage own holdings"
    ON portfolio_holdings
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY IF NOT EXISTS "Service role full access on holdings"
    ON portfolio_holdings
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

GRANT ALL ON portfolio_holdings TO service_role;
GRANT ALL ON portfolio_holdings TO authenticated;
