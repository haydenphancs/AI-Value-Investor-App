-- 180_news_sentiment_log.sql
--
-- Why: the Updates tab gets a per-scope news-sentiment timeline — how many headlines Cay AI
-- scored bullish / bearish / neutral on each ET day, over 7 / 30 / 90 days (product decision
-- 2026-09-27). Every label it needs is ALREADY produced: `ticker_news_cache.sentiment` is
-- written by the per-article enrichment (news_cache_service._enrich_articles_uncached) that
-- the Updates sweeper runs every 15 minutes. What does not exist is any HISTORY of it:
--   * ticker_news_cache rows expire 6 h after their last refresh and are DELETED every 2 h
--     (cleanup_expired_cache), and a refresh keeps only the newest 50 per scope — under a day
--     for a busy ticker;
--   * ai_insight_cache holds one overwritten row per scope;
--   * FMP's own sentiment endpoints are retired and not on the Order Form.
-- So the timeline can only exist if we start keeping the LABELS from now on.
--
--   A. public.news_sentiment_log — one row per (scope, article): the label, its ET day and
--      when it was made. It stores NO headline, URL, summary or publisher, on purpose:
--      migration 104 removed the last long-term copy of news text for licence reasons, and
--      this table must not become a second one. `article_key` is md5(external_id) cast to
--      uuid — enough to de-duplicate, useless for recovering the article. The label is
--      Cay AI's own classification of licensed news, stored as a derived count input.
--      ⚠️ Keeping it beyond 24 h still touches the open FMP checklist items "Data handling
--      (a)(b)(c)" / "ToS §6.3" (documents/legal/fmp-order-form-checklist.md) — owner item.
--
--      FIRST LABEL WINS. The app writes with ON CONFLICT DO NOTHING: a first fetch after a
--      row expired resets its AI columns and the article can be re-labelled differently,
--      and a past bar must not move when that happens.
--
--   B. Seed: the labels already in ticker_news_cache are copied in once, so the chart opens
--      with a few days instead of none. Free — no model call. Held to the SAME bounds as the
--      live writer (news_sentiment_trend_service.build_log_rows): only articles published in
--      the last 96 h (the cache can hold weeks-old rows for a quiet ticker, which would
--      back-date "tracking since" and draw lone bars), and a missing or future-dated
--      published_at is charted on the day it was cached. Legacy 'Positive'/'Negative'
--      spellings (the cache's CHECK still admits them) map to bullish/bearish. `unknown_N`
--      external ids are positional placeholders, not identities, and are skipped.
--
--   C. public.news_sentiment_daily(text, date) — per-ET-day counts for one scope. An RPC
--      because a 90-day Market query would exceed PostgREST's 1,000-row response cap.
--      SECURITY INVOKER: its only caller is service_role, which holds the table grant.
--
--   D. RLS + grants: service_role only (the 162/173/179 template). iOS never reads this
--      directly; the backend serves it through GET /api/v1/updates/sentiment-trend.
--
-- Retention: 120 days, swept by news_sentiment_trend_service.sweep_expired() from the
-- 2-hourly news pre-warmer loop in main.py (no loop of its own).
--
-- Deploy order: deploy the backend FIRST, then apply this. The code fails open until the
-- table exists (the label hook logs a WARNING and drops the batch — enrichment itself is
-- untouched — and the endpoint answers 503 SENTIMENT_TREND_UNAVAILABLE, which the app treats
-- as "hide the chart"), and the seed then picks up every label written meanwhile. The other
-- order LOSES labels: between apply and deploy the old backend marks rows ai_processed
-- without logging them, and the new hook only logs rows it enriches itself. If it was
-- applied first anyway, re-run section B after the deploy — it is idempotent.
--
-- Idempotent: IF NOT EXISTS on the table and indexes, ON CONFLICT DO NOTHING on the seed,
-- CREATE OR REPLACE on the function, DROP POLICY IF EXISTS before CREATE, and REVOKE/GRANT
-- are declarative. Safe to re-run.
--
-- VERIFY (run after applying):
--   SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'news_sentiment_log';  -- t
--   SELECT has_table_privilege('anon', 'public.news_sentiment_log', 'SELECT');          -- f
--   SELECT has_function_privilege('authenticated',
--          'public.news_sentiment_daily(text, date)', 'EXECUTE');                       -- f
--   SELECT source, count(*) FROM public.news_sentiment_log GROUP BY source;   -- seed rows > 0
--   SELECT * FROM public.news_sentiment_daily('__MARKET__', CURRENT_DATE - 7);

BEGIN;

-- ── A. The label log ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.news_sentiment_log (
    -- The ticker_news_cache key: a ticker, a coin pair (ETHUSD) or the reserved
    -- '__MARKET__'. Same 32-character ceiling the Updates endpoints enforce.
    scope        TEXT        NOT NULL CHECK (char_length(scope) BETWEEN 1 AND 32),
    -- md5(external_id)::uuid. Must match news_sentiment_trend_service.article_key().
    article_key  UUID        NOT NULL,
    -- ET calendar day of the article's published_at (the day it is charted on).
    et_day       DATE        NOT NULL,
    sentiment    TEXT        NOT NULL CHECK (sentiment IN ('bullish', 'bearish', 'neutral')),
    confidence   SMALLINT    CHECK (confidence BETWEEN 0 AND 100),
    -- 'live' = written when the article was labelled; 'seed' = copied by this migration.
    source       TEXT        NOT NULL DEFAULT 'live' CHECK (source IN ('live', 'seed')),
    labelled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, article_key)
);

-- The chart read: one scope, a trailing day range.
CREATE INDEX IF NOT EXISTS idx_news_sentiment_log_scope_day
    ON public.news_sentiment_log (scope, et_day DESC);
-- The retention sweep: a global day range.
CREATE INDEX IF NOT EXISTS idx_news_sentiment_log_day
    ON public.news_sentiment_log (et_day);

COMMENT ON TABLE public.news_sentiment_log IS
    'One row per (scope, article) holding Cay AI''s bullish/bearish/neutral label for a news '
    'article and the ET day it was published — the history behind the Updates news-sentiment '
    'timeline. Stores NO headline, URL, summary or publisher (migration 104: no long-term copy '
    'of news text); article_key is md5(external_id)::uuid. First label wins (ON CONFLICT DO '
    'NOTHING). Written by app/services/news_sentiment_trend_service.py (record_labels, called '
    'from news_cache_service._enrich_articles_uncached), read through news_sentiment_daily(); '
    'rows older than 120 days are swept.';

COMMENT ON COLUMN public.news_sentiment_log.article_key IS
    'md5(external_id)::uuid — de-duplicates an article within a scope; cannot recover it.';

-- ── B. Seed from the labels already in the cache ─────────────────────────────────
INSERT INTO public.news_sentiment_log
    (scope, article_key, et_day, sentiment, confidence, source, labelled_at)
SELECT c.ticker,
       md5(c.external_id)::uuid,
       ((CASE WHEN c.published_at IS NULL OR c.published_at > now() + interval '2 hours'
              THEN COALESCE(c.cached_at, now())
              ELSE c.published_at
         END) AT TIME ZONE 'America/New_York')::date,
       CASE lower(c.sentiment)
            WHEN 'positive' THEN 'bullish'
            WHEN 'negative' THEN 'bearish'
            ELSE lower(c.sentiment)
       END,
       LEAST(GREATEST(COALESCE(c.sentiment_confidence, 0), 0), 100)::smallint,
       'seed',
       COALESCE(c.cached_at, now())
  FROM public.ticker_news_cache c
 WHERE c.ai_processed IS TRUE
   AND lower(c.sentiment) IN ('bullish', 'bearish', 'neutral', 'positive', 'negative')
   AND c.external_id <> ''
   AND c.external_id NOT LIKE 'unknown\_%'
   AND char_length(c.ticker) BETWEEN 1 AND 32
   AND (c.published_at IS NULL OR c.published_at >= now() - interval '96 hours')
ON CONFLICT (scope, article_key) DO NOTHING;

-- ── C. Per-day counts for one scope ──────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.news_sentiment_daily(
    p_scope TEXT,
    p_since DATE
)
RETURNS TABLE (day DATE, bullish BIGINT, bearish BIGINT, neutral BIGINT)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    -- ⚠️ Every column is alias-qualified: the RETURNS TABLE names are OUT parameters.
    -- The floor keeps a bad p_since from walking more than the retention window.
    SELECT l.et_day                                        AS day,
           count(*) FILTER (WHERE l.sentiment = 'bullish') AS bullish,
           count(*) FILTER (WHERE l.sentiment = 'bearish') AS bearish,
           count(*) FILTER (WHERE l.sentiment = 'neutral') AS neutral
      FROM public.news_sentiment_log l
     WHERE l.scope = p_scope
       AND l.et_day >= GREATEST(
               COALESCE(p_since, (now() AT TIME ZONE 'America/New_York')::date - 90),
               (now() AT TIME ZONE 'America/New_York')::date - 400
           )
     GROUP BY l.et_day
     ORDER BY l.et_day ASC;
$$;

COMMENT ON FUNCTION public.news_sentiment_daily(TEXT, DATE) IS
    'Per-ET-day counts of bullish / bearish / neutral labels for one scope since p_since '
    '(never more than 400 days back). Days with no labelled article are absent, not zero. '
    'INVOKER: its only caller is service_role.';

REVOKE ALL ON FUNCTION public.news_sentiment_daily(TEXT, DATE)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.news_sentiment_daily(TEXT, DATE) TO service_role;

-- ── D. RLS + grants: service_role only ───────────────────────────────────────────
ALTER TABLE public.news_sentiment_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "news_sentiment_log_service_all" ON public.news_sentiment_log;
CREATE POLICY "news_sentiment_log_service_all" ON public.news_sentiment_log
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.news_sentiment_log FROM anon, authenticated;
-- A table with no GRANT is owner-only, not open (169's lesson). No sequence: composite key.
GRANT ALL ON public.news_sentiment_log TO service_role;

COMMIT;
