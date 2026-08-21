-- 148_etf_detail_cache_range_key.sql
--
-- Why: `etf_detail_cache` was keyed on `symbol` ALONE, but the payload it stores is
-- range-dependent — `ETFDetailResponse.chart_data` is built from the request's
-- `range` + `interval` (etf_service.get_etf_detail → resolve_interval → fetch_chart_data
-- / _extract_chart_data). So the first range fetched for a symbol won, and every later
-- request for that symbol got it back regardless of the range asked for:
--
--   GET /etfs/SPY?range=3M   -> builds a 3-month chart, caches it under symbol='SPY'
--   GET /etfs/SPY?range=1Y   -> CACHE HIT -> returns the 3-month chart under the 1Y pill
--
-- The iOS client's own cache key already includes the range, so the request really did
-- reach the server; it simply got the wrong answer. Effect: every range pill after the
-- first was a silent no-op for 5 minutes in-process and up to 24 hours from this table
-- — for every user, not just the one who warmed it. Worse in reverse: if the first
-- request after expiry was `range=1D`, every viewer got 5-minute intraday bars on the
-- 1Y/5Y/ALL tabs for the rest of the day.
--
-- `index_detail_cache` (migration 032) already had exactly this shape — a `cache_key`
-- text primary key plus the components as their own columns. This brings the ETF table
-- in line, adding `interval` (which the index table still lacks; index_service now folds
-- it into its own cache_key string).
--
-- Schema: add `cache_key` (symbol_range_interval), `chart_range`, `interval`; backfill
-- existing rows as the historical default (range=3M, interval=default) so nothing is
-- orphaned; make `cache_key` the uniqueness key and drop the old symbol-only unique.
--
-- Deploy order does NOT matter. The service composes `cache_key` already and both cache
-- helpers wrap their query in try/except: until this migration is applied they log a
-- warning about the unknown column and return a MISS, so the Supabase tier is simply
-- inactive and every request rebuilds. Slower, but correct — which the symbol-only key
-- was not.
--
-- Idempotent: all statements are IF (NOT) EXISTS.

ALTER TABLE public.etf_detail_cache
    ADD COLUMN IF NOT EXISTS cache_key   TEXT,
    ADD COLUMN IF NOT EXISTS chart_range TEXT,
    ADD COLUMN IF NOT EXISTS interval    TEXT;

-- Backfill: existing rows were written by the symbol-only code path, which always
-- served the caller's range but only ever stored one. 3M is the endpoint default
-- (etfs.py `Query("3M", alias="range")`), so that is the honest label for them.
UPDATE public.etf_detail_cache
   SET cache_key   = symbol || '_3M_default',
       chart_range = COALESCE(chart_range, '3M')
 WHERE cache_key IS NULL;

-- Collapse any duplicates the backfill could produce before the unique index goes on
-- (symbol was UNIQUE, so there can be none — this is belt-and-braces for a re-run
-- against a partially-migrated table). Keep the most recently cached row per key.
-- DESTRUCTIVE: deletes redundant cache rows only. This table is a pure cache — every
-- row is re-derivable from FMP on the next request, and nothing else references it.
DELETE FROM public.etf_detail_cache a
      USING public.etf_detail_cache b
      WHERE a.cache_key = b.cache_key
        AND a.cached_at < b.cached_at;

ALTER TABLE public.etf_detail_cache
    ALTER COLUMN cache_key SET NOT NULL;

-- The upsert target. `ON CONFLICT (cache_key)` can only infer a NON-PARTIAL unique
-- index — migration 146 is the cautionary tale (a partial index made every whale-trade
-- upsert fail 42P10 in production), so this one carries no predicate.
CREATE UNIQUE INDEX IF NOT EXISTS uq_etf_detail_cache_key
    ON public.etf_detail_cache (cache_key);

-- The old symbol-only uniqueness now actively blocks the point of this migration:
-- it would permit exactly one cached range per ETF. Drop whichever form it took.
ALTER TABLE public.etf_detail_cache
    DROP CONSTRAINT IF EXISTS etf_detail_cache_symbol_key;
DROP INDEX IF EXISTS public.etf_detail_cache_symbol_key;

-- NOTE: no symbol index is created here — migration 031 already declares
-- `idx_etf_detail_cache_symbol ON etf_detail_cache(symbol)`, and re-declaring it with
-- even a textually different body trips `test_no_index_name_is_bound_to_two_different_definitions`
-- (one name, one meaning). Lookups by symbol keep using 031's index.

COMMENT ON TABLE public.etf_detail_cache IS
    'Cached ETFDetailResponse payloads (24h TTL), keyed by cache_key = symbol_range_interval. '
    'The key must include the chart shape because chart_data is built from it; a symbol-only '
    'key made every chart range pill after the first a silent no-op. Written by '
    'app/services/etf_service.py.';

ALTER TABLE public.etf_detail_cache ENABLE ROW LEVEL SECURITY;
