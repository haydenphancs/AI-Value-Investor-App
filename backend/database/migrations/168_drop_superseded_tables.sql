-- 168_drop_superseded_tables.sql
--
-- Why: eight tables are dead in the application and several were promised a drop that
-- never came. Each still carries policies, grants, indexes and (in two cases) a trigger and
-- a function — surface that nothing watches, which is exactly the surface that drifts.
--
--   portfolio_holdings    superseded by watchlist_items in 036 ("drop in one release") —
--                         126 migrations ago. Only Python FUNCTION names still contain the
--                         string (`set_portfolio_holdings`, `get_portfolio_holdings`);
--                         no `.table("portfolio_holdings")` call exists.
--   user_lesson_progress  superseded by user_learn_progress (066/067); still GRANTed
--   user_study_schedules  write access to authenticated (062) — a live PostgREST write
--   user_bookmarks        surface on tables the app never reads.
--   asset_snapshots       dead; never named anywhere under backend/app.
--   etf_detail_cache      RETIRED by the per-section decomposition (150/151), kept "for
--   index_detail_cache    one release so a rollback is a code revert". That release shipped.
--   news_articles         dead — no read, no write anywhere in app/ or scripts/; its only
--                         references are the retention function below and a comment in
--                         api.py. Carries six indexes that serve no query.
--
-- Verified dead by: a scan of every string literal under backend/app and backend/scripts
-- against the CREATE TABLE names in the snapshot (the review of 2026-09-11), plus a grep of
-- every SQL function body in the snapshot — only `cleanup_expired_news_articles()` (dropped
-- here with its table) and no trigger, FK or view depends on any of them. The Learn
-- content tables `books` / `book_chapters` are NOT dropped: the study guides are bundled
-- in the app today, but the narration/seed scripts may still reference them — decide
-- separately.
--
-- DESTRUCTIVE: drops the tables and every row in them. Before applying, record what is
-- lost and confirm Supabase's daily backup / PITR covers the project:
--
--     SELECT 'portfolio_holdings'   AS t, count(*) FROM public.portfolio_holdings   UNION ALL
--     SELECT 'user_lesson_progress',      count(*) FROM public.user_lesson_progress UNION ALL
--     SELECT 'user_study_schedules',      count(*) FROM public.user_study_schedules UNION ALL
--     SELECT 'user_bookmarks',            count(*) FROM public.user_bookmarks       UNION ALL
--     SELECT 'asset_snapshots',           count(*) FROM public.asset_snapshots      UNION ALL
--     SELECT 'etf_detail_cache',          count(*) FROM public.etf_detail_cache     UNION ALL
--     SELECT 'index_detail_cache',        count(*) FROM public.index_detail_cache   UNION ALL
--     SELECT 'news_articles',             count(*) FROM public.news_articles;
--
-- `portfolio_holdings`, `user_lesson_progress`, `user_study_schedules` and `user_bookmarks`
-- hold USER rows written before their successors shipped. They were never migrated forward
-- (036's merge copied holdings into watchlist_items; the Learn tables were replaced, not
-- migrated), so any non-zero count here is data the product already stopped showing. If a
-- count surprises you, export the table first.
--
-- BEFORE APPLYING, also check for a hand-scheduled job on the function dropped below — a
-- pg_cron entry does not appear in a schema-only dump and would error on every run after
-- the DROP:
--
--     SELECT jobid, jobname, command FROM cron.job
--      WHERE command ILIKE '%cleanup_expired_news_articles%';
--     -- if any: SELECT cron.unschedule(jobid);
--
-- AFTER APPLYING, in ONE change: re-run scripts/dump_schema.sh; remove the eight entries
-- from backend/scripts/schema_curation.py (flagged `DROPPED BY MIGRATION 168` there);
-- hand-edit EXPECTED in backend/scripts/generate_schema_doc.py (tables 136 -> 128, public
-- and rls 101 -> 93, functions 43 -> 42, policies down by the eight tables' remaining
-- service policies); re-run scripts/generate_schema_doc.py. `test_schema_doc_generator.py`
-- fails until all of that agrees, which is the intended guard. `news_articles` was already
-- removed from the design doc's cache tree and from `_CURATED_TABLES` in
-- tests/test_system_design_doc_parity.py in the same change as this file, so that test
-- stays green across the re-dump.
--
-- Idempotent: every statement is IF EXISTS. Apply AFTER 164 and 166 — both reference these
-- tables only behind `to_regclass` guards, so they stay re-runnable once the tables are gone.

DROP TABLE IF EXISTS public.portfolio_holdings;
DROP TABLE IF EXISTS public.user_lesson_progress;
DROP TABLE IF EXISTS public.user_study_schedules;   -- takes trg_study_schedules_updated_at with it
DROP TABLE IF EXISTS public.user_bookmarks;
DROP TABLE IF EXISTS public.asset_snapshots;
DROP TABLE IF EXISTS public.etf_detail_cache;
DROP TABLE IF EXISTS public.index_detail_cache;

-- The retention function's only job was this table.
DROP FUNCTION IF EXISTS public.cleanup_expired_news_articles();
DROP TABLE IF EXISTS public.news_articles;
