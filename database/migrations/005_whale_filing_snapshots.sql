-- =====================================================
-- Migration 005: Whale Filing Snapshots + Routing Columns
-- Adds dual-source routing (13F vs Congressional) and
-- persistent cache for aggregated filing data.
-- =====================================================

-- 1. Add routing columns to whales table
ALTER TABLE whales ADD COLUMN IF NOT EXISTS cik TEXT;
ALTER TABLE whales ADD COLUMN IF NOT EXISTS data_source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE whales ADD COLUMN IF NOT EXISTS fmp_name TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_whales_cik ON whales(cik) WHERE cik IS NOT NULL;

COMMENT ON COLUMN whales.cik IS 'SEC Central Index Key for 13F institutional filers';
COMMENT ON COLUMN whales.data_source IS 'Routing: 13f | congressional_house | congressional_senate | manual';
COMMENT ON COLUMN whales.fmp_name IS 'Exact name for FMP congressional trade lookups';

-- 2. Persistent cache for aggregated filing data
CREATE TABLE IF NOT EXISTS whale_filing_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whale_id        UUID NOT NULL REFERENCES whales(id) ON DELETE CASCADE,
    filing_period   TEXT NOT NULL,
    filing_date     TEXT NOT NULL,
    total_value     NUMERIC,
    holdings_data   JSONB NOT NULL DEFAULT '[]',
    sector_data     JSONB NOT NULL DEFAULT '[]',
    trade_group     JSONB,
    behavior_summary JSONB,
    sentiment_text  TEXT,
    raw_hash        TEXT,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(whale_id, filing_period)
);

CREATE INDEX IF NOT EXISTS idx_filing_snapshots_whale
    ON whale_filing_snapshots(whale_id, processed_at DESC);

COMMENT ON TABLE whale_filing_snapshots IS 'Cached aggregated 13F/congressional data per whale per period';
COMMENT ON COLUMN whale_filing_snapshots.filing_period IS 'e.g. "2025-Q4" (13F) or "2026-02" (congressional monthly)';
COMMENT ON COLUMN whale_filing_snapshots.holdings_data IS 'JSONB array: [{ticker, companyName, shares, value, allocation}]';
COMMENT ON COLUMN whale_filing_snapshots.trade_group IS 'JSONB: {tradeCount, netAction, netAmount, summary, insights[], trades[]}';
COMMENT ON COLUMN whale_filing_snapshots.raw_hash IS 'SHA256 of raw FMP response for change detection';

-- 3. Seed whales with CIK / congressional routing
INSERT INTO whales (name, title, description, category, risk_profile, cik, data_source, fmp_name) VALUES
    -- Institutional investors (13F)
    ('Warren Buffett', 'Berkshire Hathaway CEO', 'The Oracle of Omaha. Value investing legend with a focus on long-term compounding.', 'investors', 'conservative', '0001067983', '13f', NULL),
    ('Bill Ackman', 'Pershing Square Capital', 'Activist investor focused on large-cap value opportunities.', 'institutions', 'aggressive', '0001336528', '13f', NULL),
    ('Michael Burry', 'Scion Asset Management', 'The Big Short. Contrarian deep value investor.', 'investors', 'aggressive', '0001649339', '13f', NULL),
    ('Ray Dalio', 'Bridgewater Associates', 'Macro investing pioneer and founder of the world''s largest hedge fund.', 'institutions', 'moderate', '0001350694', '13f', NULL),
    ('Cathie Wood', 'ARK Invest CEO', 'Disruptive innovation focused. High-conviction growth investor.', 'institutions', 'very_aggressive', '0001603466', '13f', NULL),
    ('David Tepper', 'Appaloosa Management', 'Distressed debt and equity investor known for contrarian macro bets.', 'institutions', 'aggressive', '0001656456', '13f', NULL),
    ('George Soros', 'Soros Fund Management', 'Legendary macro investor. Broke the Bank of England.', 'institutions', 'aggressive', '0001029160', '13f', NULL),
    -- Politicians (Congressional disclosures)
    ('Nancy Pelosi', 'U.S. Representative (CA-11)', 'Former Speaker of the House. Active tech-focused portfolio.', 'politicians', NULL, NULL, 'congressional_house', 'Nancy Pelosi'),
    ('Dan Crenshaw', 'U.S. Representative (TX-2)', 'House member with an active and diversified portfolio.', 'politicians', NULL, NULL, 'congressional_house', 'Dan Crenshaw'),
    ('Tommy Tuberville', 'U.S. Senator (AL)', 'Senator with frequent stock trades across multiple sectors.', 'politicians', NULL, NULL, 'congressional_senate', 'Tommy Tuberville')
ON CONFLICT DO NOTHING;
