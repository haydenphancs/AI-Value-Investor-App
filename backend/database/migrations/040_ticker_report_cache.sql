-- Ticker Report cache (24-hour TTL)
-- Stores the full TickerReportResponse JSONB keyed by (ticker, persona).
-- Direct path GET /stocks/{ticker}/report checks this table before
-- regenerating; ResearchService also writes here after a successful
-- Generate Analysis run so both paths share heat.

CREATE TABLE IF NOT EXISTS ticker_report_cache (
    ticker TEXT NOT NULL,
    persona TEXT NOT NULL,
    ticker_report_data JSONB NOT NULL,
    cached_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, persona)
);

CREATE INDEX IF NOT EXISTS idx_ticker_report_cache_cached_at
    ON ticker_report_cache(cached_at DESC);

ALTER TABLE ticker_report_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON ticker_report_cache
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON ticker_report_cache TO service_role;
GRANT ALL ON ticker_report_cache TO authenticated;
