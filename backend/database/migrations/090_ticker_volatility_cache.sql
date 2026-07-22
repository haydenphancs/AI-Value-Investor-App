-- 090_ticker_volatility_cache.sql
--
-- Why: the Updates-screen insight trigger is moving from a FIXED price band
-- (±2/5/10%) to the report's VOLATILITY-RELATIVE z-score — a move is "abnormal"
-- relative to THAT stock's own daily-return σ (σ_daily over a 180-day baseline),
-- so a 3% day is a non-event for a meme stock but a shock for a utility.
--
-- The materiality sweeper evaluates ~200 tickers every 5 minutes from ONE
-- batch-quote; it cannot fetch 180 daily closes per ticker per sweep. But σ_daily
-- moves slowly (it is a 180-day statistic), so it is PRECOMPUTED once per day by a
-- lifespan job (services/volatility_cache_service.recompute_universe) and read
-- from this table by the sweeper (services/volatility_cache_service.get_sigmas_bulk).
--
-- `sigma_daily` is NULLABLE: a newly-listed / low-history ticker has < ~30 closes,
-- so σ is undefined — the gate then falls back to the fixed price band for that
-- scope (never loses a signal). `sample_size` records how many daily returns fed σ.
-- Read posture matches the other market-data caches (public read, service write).
--
-- Idempotent; safe to re-apply. Apply manually (Supabase Studio / CLI).

BEGIN;

CREATE TABLE IF NOT EXISTS ticker_volatility_cache (
    ticker       TEXT        PRIMARY KEY,
    -- population std of daily returns over the ~180-day baseline; NULL = too little
    -- history to judge (gate falls back to the fixed band for this ticker).
    sigma_daily  DOUBLE PRECISION,
    sample_size  INTEGER     NOT NULL DEFAULT 0,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL
);

-- Drift back-fill (safe on a table created by an earlier draft).
ALTER TABLE ticker_volatility_cache ADD COLUMN IF NOT EXISTS sigma_daily DOUBLE PRECISION;
ALTER TABLE ticker_volatility_cache ADD COLUMN IF NOT EXISTS sample_size INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ticker_volatility_cache ADD COLUMN IF NOT EXISTS computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ticker_volatility_cache ADD COLUMN IF NOT EXISTS expires_at  TIMESTAMPTZ;

-- The freshness sweep (WHERE expires_at < now) can't use the scope-leading PK.
CREATE INDEX IF NOT EXISTS idx_ticker_volatility_cache_expiry
    ON ticker_volatility_cache(expires_at);

ALTER TABLE ticker_volatility_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ticker_volatility_cache_public_read" ON ticker_volatility_cache;
CREATE POLICY "ticker_volatility_cache_public_read" ON ticker_volatility_cache
    FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "ticker_volatility_cache_service_write" ON ticker_volatility_cache;
CREATE POLICY "ticker_volatility_cache_service_write" ON ticker_volatility_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON ticker_volatility_cache TO anon, authenticated;
GRANT ALL ON ticker_volatility_cache TO service_role;

COMMIT;
