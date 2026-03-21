-- Fix permissions on watchlist_items and portfolio_holdings tables
-- The tables exist but service_role lacks GRANT access

GRANT ALL ON watchlist_items TO service_role;
GRANT ALL ON watchlist_items TO authenticated;

GRANT ALL ON portfolio_holdings TO service_role;
GRANT ALL ON portfolio_holdings TO authenticated;

-- Ensure RLS policies exist for service_role
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'watchlist_items' AND policyname = 'Service role full access on watchlist'
    ) THEN
        CREATE POLICY "Service role full access on watchlist"
            ON watchlist_items FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'portfolio_holdings' AND policyname = 'Service role full access on holdings'
    ) THEN
        CREATE POLICY "Service role full access on holdings"
            ON portfolio_holdings FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;
END $$;
