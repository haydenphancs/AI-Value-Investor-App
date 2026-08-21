-- 151_etf_cache_grant_hardening.sql
--
-- Why: verifying migration 150 against the LIVE database (`information_schema.
-- role_table_grants`, not the snapshot — `dump_schema.sh` runs `--no-privileges`, so grants
-- are invisible there) showed 150 had closed the over-grant on `index_detail_cache` and
-- left the two ETF tables untouched:
--
--   etf_detail_cache    anon           SELECT
--   etf_detail_cache    authenticated  DELETE, INSERT, REFERENCES, SELECT, TRIGGER,
--                                      TRUNCATE, UPDATE          <-- full write access
--   etf_snapshot_cache  anon           SELECT
--   etf_snapshot_cache  authenticated  SELECT
--
-- Two separate problems.
--
-- 1. `etf_detail_cache` is WRITABLE by any holder of a valid user JWT — insert, update,
--    delete, TRUNCATE — straight through PostgREST. It is a pure cache and is now RETIRED
--    (etf_service stopped reading and writing it in the per-section decomposition), so the
--    blast radius today is small; but a table nothing reads is exactly the one that stops
--    being watched, and the grant outlives the code that made it look harmless. Migration
--    049 granted only SELECT here, so the write privileges predate it and were never
--    deliberate.
--
-- 2. `etf_snapshot_cache` is anon/authenticated-READABLE, and the decomposition just moved
--    ALL of the ETF per-section cache into it — fundamentals, derived performance and the
--    assembled chart bars now live under its new `fundamentals` / `derived` /
--    `chart:{range}:{interval}` categories. Nothing user-scoped is in there and every byte
--    is public market data the API serves anyway, so this is a posture inconsistency rather
--    than a leak. But it is the wrong posture: migrations 149 and 150 established
--    service_role-only for exactly these caches, on the reasoning that they are served
--    THROUGH the backend (iOS has no Supabase client), so anon/authenticated read widens
--    access for no reason. 049's public-read predates that decision.
--
-- Verified safe: `grep -rn "etf_snapshot_cache\|etf_detail_cache" app/ scripts/` finds only
-- `app/services/etf_service.py`, which reaches Supabase via `get_supabase()` — the
-- service_role client. No anon-key reader exists anywhere in the product.
--
-- The two tables are still deliberately NOT dropped. `etf_detail_cache` and
-- `index_detail_cache` stay in place, unread, for one release so a rollback of the
-- decomposition is a code revert rather than a migration. Drop them in a follow-up.
--
-- Idempotent: REVOKE/GRANT are declarative, and every policy statement is DROP-then-CREATE.

-- ---- etf_snapshot_cache: the live per-section cache ----
DROP POLICY IF EXISTS "etf_snapshot_cache_public_read" ON public.etf_snapshot_cache;

REVOKE ALL ON public.etf_snapshot_cache FROM anon, authenticated;
GRANT ALL ON public.etf_snapshot_cache TO service_role;

COMMENT ON TABLE public.etf_snapshot_cache IS
    'Per-section ETF detail cache. Categories: fundamentals (profile projection + etf-info '
    '+ holders + sector weights + dividends), derived (performance periods + benchmark '
    'CAGR), chart:{range}:{interval} (non-intraday bars only), plus the three older '
    'finished-response categories profile / holdings_risk / dividend_history. 12h TTL for '
    'the section categories and 24h for the response ones, both enforced in application '
    'code via cached_at. Holds only sections that CANNOT contain a live price: the quote '
    'and the related-ETF quotes are in-process only, and the raw daily history is excluded '
    'because reading ~1 MB back is slower than re-fetching it from FMP. That exclusion is '
    'what let etf_service._refresh_volatile be deleted rather than fixed. Written by '
    'app/services/etf_service.py.';

-- ---- etf_detail_cache: retired, and it should not have been writable ----
DROP POLICY IF EXISTS "etf_detail_cache_public_read" ON public.etf_detail_cache;

REVOKE ALL ON public.etf_detail_cache FROM anon, authenticated;
GRANT ALL ON public.etf_detail_cache TO service_role;

COMMENT ON TABLE public.etf_detail_cache IS
    'RETIRED. Held the whole ETF detail response under a symbol_range_interval key. No '
    'longer read or written: etf_service now caches per section in etf_snapshot_cache, '
    'because a 24-hour row of the FULL payload froze current_price and the quote-derived '
    'key statistics along with it — the only reason _refresh_volatile ever existed. Kept '
    'unread for one release so a rollback is a code revert; drop it after that.';
