-- Incremental quarterly store for hedge fund 13F data.
-- NAMING: "hedge fund" here = FMP 13F institutional-ownership data; the app UI
-- labels it "Institutions" (iOS SmartMoneyTab.hedgeFunds = "Institutions").
-- Each row = one ticker + one quarter.  Immutable once filed.
CREATE TABLE IF NOT EXISTS hedge_fund_quarters (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker TEXT NOT NULL,
    year INT NOT NULL,
    quarter INT NOT NULL,
    quarter_date TEXT NOT NULL,
    buy_volume DOUBLE PRECISION DEFAULT 0,
    sell_volume DOUBLE PRECISION DEFAULT 0,
    net_flow DOUBLE PRECISION DEFAULT 0,
    buyers_count INT DEFAULT 0,
    sellers_count INT DEFAULT 0,
    computed_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (ticker, year, quarter)
);

CREATE INDEX IF NOT EXISTS idx_hfq_ticker ON hedge_fund_quarters (ticker);

GRANT SELECT, INSERT, UPDATE, DELETE ON hedge_fund_quarters TO service_role;
