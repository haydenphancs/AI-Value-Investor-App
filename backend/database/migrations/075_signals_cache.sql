-- 075_signals_cache.sql
--
-- Why: the Home "App-Exclusive Signals" section (Congressional Buys, Whale
-- Accumulation, Earnings Shockers) aggregates FMP congress/earnings feeds plus
-- the daily-hydrated Supabase whale registry into one payload. Those sources
-- move on daily/quarterly cadences, so a 24h Tier-2 cache — behind the service's
-- 45-min in-memory tier — survives Railway restarts, keeps the FMP quota and the
-- Supabase reads low, and lets the on-view pre-warm ride along cheaply.
--
-- The payload is GLOBAL (not per-user, not per-ticker) — every Home load renders
-- the same three cards — so the table is keyed by a single constant cache_key
-- (currently "signals"). Written/read by the backend service role via
-- app/services/signals_service.py (a serialized SignalsGroupResponse in `data`);
-- public SELECT mirrors the other *_cache tables (non-sensitive aggregate market
-- data). A stale/garbled row degrades gracefully: the service treats a failed
-- model_validate as a miss and rebuilds.
--
-- Idempotent: safe to re-apply. Apply manually (Supabase Studio / CLI).

CREATE TABLE IF NOT EXISTS signals_cache (
    id BIGSERIAL PRIMARY KEY,
    cache_key TEXT NOT NULL,
    data JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE(cache_key)
);

CREATE INDEX IF NOT EXISTS idx_signals_cache_lookup
    ON signals_cache(cache_key, expires_at);

ALTER TABLE signals_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "signals_cache_public_read" ON signals_cache
    FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "signals_cache_service_write" ON signals_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON signals_cache TO anon, authenticated;
GRANT ALL ON signals_cache TO service_role;
GRANT USAGE, SELECT ON SEQUENCE signals_cache_id_seq TO service_role;
