-- 069_ticker_data_cache.sql
--
-- Why: the deterministic FMP collection for a ticker (profile, financials,
-- ratios, price history, vitals, peers, transcript, macro/FRED, etc.) is
-- persona-NEUTRAL. collect() reads persona_key only to set the agent label and
-- the persona-weighted score, and both of those happen AFTER collection. Before
-- this table, every persona's report generation re-ran the full FMP fan-out
-- (~20+ upstream calls) + deterministic assembly, so 4 personas analyzing the
-- same company (e.g. ORCL) meant 4x the FMP quota and 4x the latency on the
-- expensive part.
--
-- This caches the collection by TICKER (24h) so personas 2..N — and later users
-- on the same ticker — reuse it and only re-run the genuinely per-persona layer
-- (scoring + Stage B narratives). Sits BELOW the per-(ticker,persona)
-- ticker_report_cache: a different-persona request misses the report cache,
-- then HITS this collection cache, skipping the fan-out.
--
-- Schema: ticker PK, collected_data JSONB (a serialized CollectedTickerData),
-- cached_at. INTERNAL-only — read/written by the backend service role; never
-- served to iOS. Same 24h TTL + schema-floor discipline as ticker_report_cache
-- (enforced in app/services/ticker_data_cache.py).

CREATE TABLE IF NOT EXISTS ticker_data_cache (
    ticker TEXT PRIMARY KEY,
    collected_data JSONB NOT NULL,
    cached_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticker_data_cache_cached_at
    ON ticker_data_cache(cached_at DESC);

ALTER TABLE ticker_data_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON ticker_data_cache
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON ticker_data_cache TO service_role;
GRANT ALL ON ticker_data_cache TO authenticated;
