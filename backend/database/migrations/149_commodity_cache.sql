-- 149_commodity_cache.sql
--
-- Why: `commodity_service` was the ONLY one of the five asset-detail services with no
-- Supabase tier at all — no `self.supabase`, zero `supabase.table(...)` calls. Its cache
-- therefore lived only in the uvicorn process: it died on every Railway deploy and was
-- never shared across instances, so the first viewer after each restart paid the full
-- FMP fan-out again.
--
-- Measured before this change: browsing one commodity's 7 range pills cost 55 FMP calls
-- and pulled 6.6 MB of byte-identical daily history (972 KB / 4,333 rows, re-fetched once
-- per range because the cache key included range+interval).
--
-- The service now caches per SECTION with TTLs matched to how fast each section actually
-- changes. This table persists the two sections that are expensive to rebuild and slow to
-- change:
--
--   category='derived'  -> performance_periods + benchmark_summary + the daily key-stat
--                          rows (52-wk high/low, 200-day MA, 30-day avg volume). All are
--                          computed from the full daily history and only move at a close.
--   category='chart'    -> the assembled bars for one (range, interval).
--
-- DELIBERATELY NOT PERSISTED:
--   * the raw daily history — 972 KB x 14 symbols is ~13 MB, and reading ~1 MB back from
--     Supabase is SLOWER than the 0.3 s FMP call it would replace. It stays in the
--     in-process tier, where holding it is free.
--   * the quote and the related-commodity quotes — a persisted price is a stale price.
--     The service re-quotes the volatile fields on every tier-2 hit (`_refresh_volatile`,
--     the same fix etf_service/index_service already carry).
--
-- Shape: ONE table with a `category` discriminator, following `etf_snapshot_cache`
-- (migration 034). That migration deliberately DROPped the per-section tables it replaced
-- ("Unified ETF snapshot cache — replaces separate per-section tables"), so a new
-- one-table-per-section design here would be a regression.
--
-- Deploy order does NOT matter. Both cache helpers wrap their query in try/except: until
-- this migration is applied they log a warning about the missing relation and return a
-- MISS, so the Supabase tier is simply inactive and every request rebuilds from the
-- in-process tier. Slower on a cold process, never wrong. (Same posture as migration 148.)
--
-- Idempotent: every statement is IF (NOT) EXISTS.

CREATE TABLE IF NOT EXISTS public.commodity_cache (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cache_key     TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    category      TEXT NOT NULL,
    response_json JSONB NOT NULL,
    cached_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The upsert target. `ON CONFLICT (cache_key)` can only infer a NON-PARTIAL unique index
-- — migration 146 is the cautionary tale (a partial index made every whale-trade upsert
-- fail 42P10 in production, silently, into a best-effort handler), so this one carries no
-- predicate. Declared separately rather than inline in CREATE TABLE so it is also created
-- when an earlier draft of the table already exists, where CREATE TABLE IF NOT EXISTS is
-- a silent no-op (migration 094's rationale).
CREATE UNIQUE INDEX IF NOT EXISTS uq_commodity_cache_key
    ON public.commodity_cache (cache_key);

-- Lookup/sweep by symbol (invalidating one commodity, or pruning it).
CREATE INDEX IF NOT EXISTS idx_commodity_cache_symbol
    ON public.commodity_cache (symbol);

COMMENT ON TABLE public.commodity_cache IS
    'Per-section commodity detail cache (12h TTL, enforced in application code via '
    'cached_at). cache_key = "{SYMBOL}:{category}" for category=derived, or '
    '"{SYMBOL}:chart:{range}:{interval}" for category=chart. Holds only sections that '
    'are expensive to rebuild AND slow to change; quotes and the raw daily history are '
    'deliberately excluded (a persisted price is a stale price, and the history is large '
    'enough that reading it back is slower than re-fetching it). Written by '
    'app/services/commodity_service.py.';

-- Read posture: service_role ONLY, matching migrations 078 and 094. These caches are
-- served THROUGH the backend API (iOS has no Supabase client), so granting anon or
-- authenticated read would widen access for no reason. NOTE this supersedes the
-- `add-cache-table` skill template, which still shows a public-read policy.
ALTER TABLE public.commodity_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "commodity_cache_service_role_all" ON public.commodity_cache;
CREATE POLICY "commodity_cache_service_role_all" ON public.commodity_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.commodity_cache FROM anon, authenticated;
GRANT ALL ON public.commodity_cache TO service_role;
