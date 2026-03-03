-- Migration 004: Create portfolio_holdings table for diversification scoring
-- Run this in the Supabase SQL Editor

-- ── Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    market_value NUMERIC(18,2) NOT NULL DEFAULT 0,
    sector TEXT,
    asset_type TEXT NOT NULL DEFAULT 'Stock',
    country TEXT NOT NULL DEFAULT 'US',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE(user_id, ticker)
);

-- ── RLS ──────────────────────────────────────────────────────────────
ALTER TABLE portfolio_holdings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own holdings"
    ON portfolio_holdings FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert own holdings"
    ON portfolio_holdings FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update own holdings"
    ON portfolio_holdings FOR UPDATE
    USING (user_id = auth.uid());

CREATE POLICY "Users can delete own holdings"
    ON portfolio_holdings FOR DELETE
    USING (user_id = auth.uid());

CREATE POLICY "Service role full access on portfolio_holdings"
    ON portfolio_holdings FOR ALL
    USING (auth.role() = 'service_role');

-- ── Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_user
    ON portfolio_holdings(user_id);

CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_user_ticker
    ON portfolio_holdings(user_id, ticker);
