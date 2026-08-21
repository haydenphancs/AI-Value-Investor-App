-- 150_index_cache.sql
--
-- Why: `index_service` cached ONE monolithic payload under a key carrying range+interval
-- (`index_detail_cache`, migration 032) — the same shape `commodity_service` had before
-- migration 149 and `etf_service` before this pass. Only `chart_data` depends on the
-- range; the other EIGHT sections of the response do not. So every range pill was a cold
-- rebuild that re-fetched the same quote, the same multi-year daily history, the same
-- global sector-performance list and the same 503-row constituent list.
--
-- Measured before this change: ~32-36 FMP calls and ~10.5 MB of byte-identical history to
-- browse one index's 7 range pills, ~83% of it duplicated, with a worst cold range of
-- 14.8 s. The index screen also had NO in-memory tier for the assembled response at all,
-- so every request went to Supabase and then re-quoted FMP before it could answer.
--
-- The service now caches per SECTION with TTLs matched to how fast each section actually
-- changes. This table persists the sections that are expensive to rebuild and slow to
-- change:
--
--   category='derived'      -> performance_periods + the history-derived key statistics
--                              (50-day and 200-day averages). All are computed from the
--                              full daily history and only move at a close.
--   category='constituents' -> the constituent COUNT. `get_index_constituents` returns a
--                              503/3000-row list that the service uses only for `len()`,
--                              so the list is reduced to its one useful number before it
--                              is stored.
--   category='chart:{range}:{interval}'
--                           -> the assembled bars for one non-intraday chart shape.
--
-- DELIBERATELY NOT PERSISTED:
--   * the raw daily history — reading ~1 MB back from Supabase is SLOWER than the FMP
--     call it would replace. It stays in the in-process tier, where holding it is free.
--   * the quote, and every key statistic derived from it (Open, Previous Close, Day High,
--     Day Low, 52-Week High/Low, Volume) — a persisted price is a stale price. Those rows
--     are exactly what `_refresh_volatile` had to re-quote on every cache hit, and
--     excluding them here is what makes that helper unnecessary rather than merely fixed.
--   * an intraday chart. A 12h-old 5-minute series would paint yesterday's session under
--     a live header.
--
-- Shape: ONE table with a `category` discriminator, following `etf_snapshot_cache`
-- (migration 034) and `commodity_cache` (149). Migration 034 deliberately DROPped the
-- per-section tables it replaced, so a new one-table-per-section design would be a
-- regression.
--
-- Deploy order does NOT matter. Both cache helpers wrap their query in try/except: until
-- this migration is applied they log a warning about the missing relation and return a
-- MISS, so the Supabase tier is simply inactive and every request rebuilds from the
-- in-process tier. Slower on a cold process, never wrong. (Same posture as 148 and 149.)
--
-- Idempotent: every statement is IF (NOT) EXISTS.

CREATE TABLE IF NOT EXISTS public.index_cache (
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
CREATE UNIQUE INDEX IF NOT EXISTS uq_index_cache_key
    ON public.index_cache (cache_key);

-- Lookup/sweep by symbol (invalidating one index, or pruning it). Distinct name from
-- 032's `idx_index_detail_cache_symbol`, which is bound to a different table.
CREATE INDEX IF NOT EXISTS idx_index_cache_symbol
    ON public.index_cache (symbol);

COMMENT ON TABLE public.index_cache IS
    'Per-section index detail cache (12h TTL, enforced in application code via '
    'cached_at). cache_key = "{SYMBOL}:{category}" for category=derived or constituents, '
    'or "{SYMBOL}:chart:{range}:{interval}" for category=chart. Holds only sections that '
    'are expensive to rebuild AND slow to change; the quote, every quote-derived key '
    'statistic and the raw daily history are deliberately excluded (a persisted price is '
    'a stale price, and the history is large enough that reading it back is slower than '
    're-fetching it). Supersedes the whole-payload index_detail_cache from migration 032. '
    'Written by app/services/index_service.py.';

-- Read posture: service_role ONLY, matching migrations 078, 094 and 149. These caches are
-- served THROUGH the backend API (iOS has no Supabase client), so granting anon or
-- authenticated read would widen access for no reason. NOTE this supersedes the
-- `add-cache-table` skill template, which still shows a public-read policy.
ALTER TABLE public.index_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "index_cache_service_role_all" ON public.index_cache;
CREATE POLICY "index_cache_service_role_all" ON public.index_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.index_cache FROM anon, authenticated;
GRANT ALL ON public.index_cache TO service_role;


-- ── Hardening: close an over-grant on the table this one supersedes ──────────────────
--
-- Migration 032 shipped `GRANT ALL ON index_detail_cache TO authenticated`, so any holder
-- of a valid user JWT can read AND WRITE that cache directly through PostgREST — i.e.
-- poison what every viewer of an index is served. Nothing needs that grant: the backend
-- uses service_role and iOS has no Supabase client. 049 tightened the POLICY to
-- public-read but left the table-level GRANT untouched, and a GRANT is what PostgREST
-- checks first.
--
-- The table itself is deliberately NOT dropped here. `index_service` stops reading and
-- writing it in the same change that adds this file, but leaving the relation in place
-- for one release means a rollback is a code revert rather than a migration. Drop it in a
-- follow-up once the per-section path has run in production.
REVOKE ALL ON public.index_detail_cache FROM anon, authenticated;
GRANT ALL ON public.index_detail_cache TO service_role;
