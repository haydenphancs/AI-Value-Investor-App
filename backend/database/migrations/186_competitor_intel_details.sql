-- 186_competitor_intel_details.sql
--
-- Why: TestFlight feedback #57 (AVGO, 2026-09-26). The report's Competitors section put
-- NVIDIA first, Cay AI said NVIDIA was not the main competitor, and nothing on screen said
-- how the list was chosen or ordered. Two things were missing from the cached research:
--
--   1. WHERE each competitor competes. The grounded research already returned a per-row
--      "why" and the service threw it away. It now asks for a short "competes in" label
--      (`segment`, at most 48 characters, cleaned server-side: citation markers, links,
--      markdown and control characters stripped) and stores it here, keyed by ticker:
--        {"MRVL": {"segment": "Custom AI accelerators & networking"}, ...}
--      A ticker with no usable label is simply absent from the object.
--   2. WHAT THE ORDER MEANS. `competitor_tickers` is now ordered by the research's own
--      directness ranking (the share of the focal company's revenue each rival contests),
--      most direct first, with mainly-customer / supplier / design-partner rows dropped.
--      The COMMENT below records that. Migration 054's inline note ("ordered by FMP
--      mktCap desc") and its Phase-1 "$27.3B floor and 7-row cap" prose are superseded;
--      054 itself is not edited.
--
-- Deploy order — the code tolerates this column being missing, so either order works:
--   * Before 186: `competitor_intel_service._write_cache` tries the full row, gets
--     PostgREST's unknown-column error (PGRST204 / 42703, matched by
--     `app/utils/supabase_errors.is_unknown_column_error`), logs "migration 186 not
--     applied" at WARNING and writes the tickers only, stamped
--     model_version '<model>|cip-v2-nodetails'.
--   * After 186: a '-nodetails' row reads as STALE once `competitor_details` is present
--     in the row, so the next collection re-extracts it and writes the labels. It heals
--     itself; nothing to backfill.
--   Apply BEFORE the backend deploy and before Sun 2026-10-04 02:30 UTC, when the
--   quarterly batch re-extracts the top 500 tickers, so that run writes the labels the
--   first time instead of re-billing a second grounded call per ticker afterwards.
--
-- Independent of 185 (sector_benchmarks): neither reads nor writes the other's table, so
-- they can be applied in either order.
--
-- Grants: unchanged. The table already has RLS with a service_role-only policy, REVOKE
-- from anon/authenticated (164) and GRANT ALL TO service_role; a new column inherits the
-- table-level grants.
--
-- Cost of the DDL: ADD COLUMN with a constant DEFAULT is a catalog-only change on
-- Postgres 11+ (no table rewrite), and the table holds one row per ticker (~500).
--
-- Idempotent: ADD COLUMN IF NOT EXISTS; COMMENT ON replaces any earlier comment.
--
-- VERIFY (after applying) — one row: competitor_details | jsonb | NO | '{}'::jsonb
--   SELECT column_name, data_type, is_nullable, column_default
--     FROM information_schema.columns
--    WHERE table_schema = 'public'
--      AND table_name = 'competitor_intel_cache'
--      AND column_name = 'competitor_details';

BEGIN;

ALTER TABLE public.competitor_intel_cache
    ADD COLUMN IF NOT EXISTS competitor_details JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.competitor_intel_cache.competitor_details IS
    'Per-competitor detail from the grounded research, keyed by ticker: '
    '{"TICKER": {"segment": "<where it competes, <= 48 chars, cleaned>"}}. A ticker with no '
    'usable label is absent. Written with model_version ''<model>|cip-v2''; a row stamped '
    '''|cip-v2-nodetails'' was written before this column existed and is re-extracted.';

COMMENT ON COLUMN public.competitor_intel_cache.competitor_tickers IS
    'Validated competitor tickers (FMP profile with positive market cap), ordered by '
    'grounded-research directness, most direct first (share of the focal company''s revenue '
    'each one contests). Mainly-customer / supplier / design-partner rows are excluded. Not '
    'ordered by market cap.';

COMMIT;
