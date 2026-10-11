-- 193_news_sentiment_title_hash.sql
--
-- Why: the News Tone chart counts one headline per story. Wire copies of one story arrive under
-- different URLs ("UPDATE 1-Fed holds rates", "Fed Holds Rates!"), and the label log keys an
-- article by md5(url) — so two copies are two rows. Migration 191's gate dedups copies only
-- WITHIN one write (one enrichment batch, one backfill window): a copy that arrived in a LATER
-- batch was counted again, because the log holds no title to compare against (migration 104
-- forbids keeping news text).
--
-- This column keeps a HASH of the normalised title — md5 of
-- app/services/news_tone_filters.title_key(), like article_key is md5 of the URL — never the
-- text. Before writing, news_sentiment_trend_service.upsert_log_rows looks up counted rows of the
-- same scope and ET day with the same hash and writes the new copy with
-- skip_reason = 'duplicate_title' (191's CHECK already allows it), so it stays off the chart.
-- Best-effort: two copies written at the same instant by two writers can still both count.
--
-- Rows written before this column (and by code deployed ahead of it) carry NULL and take no part
-- in the check; the code heals a missing column by writing without it.
--
-- Idempotent: ADD COLUMN / CREATE INDEX IF NOT EXISTS; COMMENT re-runs. Adding a nullable column
-- is catalog-only; the partial index builds over ~40k rows in well under a second. No grant or
-- policy change: the table stays service-role only (migration 180).
--
-- Deploy order: either way. Applied first, nothing writes the column until the deploy; deployed
-- first, the code writes without it (one WARNING) and re-checks every 10 minutes.

BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.news_sentiment_log ADD COLUMN IF NOT EXISTS title_hash uuid;

CREATE INDEX IF NOT EXISTS idx_news_sentiment_log_scope_title
    ON public.news_sentiment_log (scope, title_hash)
    WHERE title_hash IS NOT NULL;

COMMENT ON COLUMN public.news_sentiment_log.title_hash IS
    'md5 of the normalised headline (news_tone_filters.title_key) as a uuid — never the text. A '
    'counted row with the same scope, ET day and hash makes a later copy a duplicate_title skip.';

COMMIT;

-- VERIFY (run after applying — one row: title_hash_column = 1, title_index = 1):
--   SELECT
--     (SELECT count(*) FROM information_schema.columns
--       WHERE table_schema = 'public' AND table_name = 'news_sentiment_log'
--         AND column_name = 'title_hash')                                          AS title_hash_column,
--     (SELECT count(*) FROM pg_indexes
--       WHERE schemaname = 'public' AND indexname = 'idx_news_sentiment_log_scope_title') AS title_index;
