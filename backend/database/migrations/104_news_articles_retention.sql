-- 104_news_articles_retention.sql
--
-- Why: `public.news_articles` stores THIRD-PARTY editorial content — publisher
-- headlines, article `summary` text, `thumbnail_url`, and `source_logo_url` — with no
-- `expires_at` column and no cleanup function. Every other place the app caches upstream
-- content has a TTL (`ticker_news_cache` expires in 6h and has
-- `cleanup_expired_news_cache()`); this table did not, so rows sat indefinitely.
--
-- Retaining a licensed news feed forever, and serving it to end users, is a data-license
-- problem rather than a code bug. It is also a staleness problem: the rows were served
-- ordered by `published_at DESC` with no age bound.
--
-- What changed in code alongside this migration:
--   * `GET /api/v1/news` and `GET /api/v1/news/{id}` were REMOVED
--     (backend/app/api/v1/endpoints/news.py deleted, router unregistered in api.py).
--     They were the table's only readers. Nothing in the codebase writes the table, and
--     no client ever called those two endpoints — the iOS enum cases existed but were
--     never invoked, and have been removed too.
--   * Live news is unaffected: it is per-asset (`/stocks/{ticker}/news`,
--     `/updates/feed`) and backed by `ticker_news_cache`.
--
-- So after this migration the table is unreferenced by application code.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- DESTRUCTIVE: deletes all rows from public.news_articles.
--
-- What is lost: cached third-party news articles (headline, summary text, thumbnail and
-- publisher-logo URLs, sentiment, and any AI-derived `insight_summary` /
-- `key_takeaways` computed from them). This is upstream-derived content, not user data —
-- nothing here was authored by Caydex or by a user, and none of it is reachable from the
-- app any more. Verified: zero writers and zero readers in backend/app.
--
-- The TABLE is intentionally kept rather than dropped, because it predates the earliest
-- migration in this folder (the visible set starts at 015) and may be referenced by
-- something outside this repo. Emptying it removes the retention exposure without
-- risking an unknown dependency.
--
-- REVIEW BEFORE APPLYING. If you want the rows preserved, snapshot them first:
--   CREATE TABLE news_articles_archive_20260729 AS SELECT * FROM public.news_articles;
-- ─────────────────────────────────────────────────────────────────────────────

-- Row count before, for the apply log.
DO $$
DECLARE n bigint;
BEGIN
    SELECT count(*) INTO n FROM public.news_articles;
    RAISE NOTICE 'news_articles rows before cleanup: %', n;
END $$;

DELETE FROM public.news_articles;

-- Add an expiry column + cleanup function so that IF the table is ever repopulated it
-- cannot silently accumulate again. Idempotent.
ALTER TABLE public.news_articles
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

COMMENT ON COLUMN public.news_articles.expires_at IS
    'Retention bound for cached third-party article content. Any writer MUST set this. '
    'NULL rows are treated as expired by cleanup_expired_news_articles().';

CREATE INDEX IF NOT EXISTS idx_news_articles_expires_at
    ON public.news_articles(expires_at);

CREATE OR REPLACE FUNCTION public.cleanup_expired_news_articles() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    -- NULL expires_at counts as expired: a writer that forgets to set a retention
    -- bound must not get indefinite storage by default.
    DELETE FROM news_articles WHERE expires_at IS NULL OR expires_at < now();
END;
$$;
