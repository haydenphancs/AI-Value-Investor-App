-- 208_drop_home_feed_tables.sql
--
-- Why: `daily_briefings` and `market_insights` existed only for the legacy
-- `GET /api/v1/home/feed`, removed on 2026-11-05 with `home_service.py` (owner, 2026-11-05:
-- "Remove the route", then "Write the migration"). The app had stopped calling that route on
-- 2026-08-19, and its earnings fallback was the last multi-day `earnings-calendar` read. Both
-- tables were created in Supabase Studio and never had a writer: no migration seeds them and
-- nothing under backend/ inserts into them. Each still carries a policy, a grant and indexes,
-- which is surface nothing watches.
--
-- Verified dead on 2026-11-05 by:
--   * production: 0 rows in each table, read twice that day.
--   * code: no reference under backend/app, backend/marketing or frontend/ios/ios. Under
--     backend/scripts only the Atlas's own curation still names them (see AFTER APPLYING).
--   * schema_snapshot.sql: no foreign key, view, function or trigger depends on either table.
--     That is why this uses no CASCADE, and it must not gain one: a dependency added since the
--     last dump should stop this drop, not be dropped along with it.
--
-- DESTRUCTIVE: drops both tables and any rows in them. They held no rows on 2026-11-05, so
-- nothing is lost, and the DO block below refuses if either has gained a row since. Still,
-- confirm Supabase's daily backup / PITR covers the project before applying, and record the
-- state first:
--
--     SELECT 'daily_briefings' AS t, count(*) FROM public.daily_briefings UNION ALL
--     SELECT 'market_insights',      count(*) FROM public.market_insights;
--
-- The check and the drops share ONE DO block on purpose. A DO block is atomic, so a failed
-- check drops nothing on any client. As separate statements, a client that carries on after
-- an error (psql without --single-transaction) would still have run the drops.
--
-- ORDER: apply AFTER the deploy that removes `home_service.py`. Until then a call to the old
-- route would only log a WARNING and fall back, but nothing is gained by applying first.
--
-- Idempotent: a table that is already gone is skipped, so a re-run does nothing.
--
-- AFTER APPLYING, in ONE change: re-run scripts/dump_schema.sh; remove the two entries from
-- backend/scripts/schema_curation.py (flagged `DROPPED BY MIGRATION 208` there) and
-- `daily_briefings` from `_REGISTRY` in backend/scripts/schema_doc_data.py; hand-edit EXPECTED
-- in backend/scripts/generate_schema_doc.py (tables 158 -> 156, public and rls 117 -> 115,
-- policies 124 -> 122, plus anything else the re-dump picked up); re-run
-- scripts/generate_schema_doc.py. `test_schema_doc_generator.py` fails until all of that
-- agrees, which is the intended guard.

DO $$
DECLARE
    t TEXT;
    has_rows BOOLEAN;
BEGIN
    FOREACH t IN ARRAY ARRAY['daily_briefings', 'market_insights'] LOOP
        IF to_regclass('public.' || quote_ident(t)) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I)', t) INTO has_rows;
            IF has_rows THEN
                RAISE EXCEPTION 'public.% has rows; it was empty when 208 was written, so review them before dropping', t;
            END IF;
        END IF;
    END LOOP;

    -- DESTRUCTIVE: both tables are proven empty just above. Backup: see the header.
    DROP TABLE IF EXISTS public.daily_briefings;
    DROP TABLE IF EXISTS public.market_insights;
END
$$;

-- VERIFY (read-only) right after applying → one row: t | t
-- SELECT
--   to_regclass('public.daily_briefings') IS NULL,
--   to_regclass('public.market_insights') IS NULL;
