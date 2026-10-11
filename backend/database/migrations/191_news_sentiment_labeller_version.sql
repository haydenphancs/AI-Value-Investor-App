-- 191_news_sentiment_labeller_version.sql
--
-- Why: the News Tone labels were measured against three blind raters on 2026-10-10 (200 live
-- headlines, the raters agreeing 93% among themselves). The labeller agreed with them only 70.5%
-- of the time and was over-directional: 69 bullish where the raters saw 50, 62 bearish vs 52,
-- and 69 neutral vs 98. The causes were in the question, not the model: it asked for the lean
-- "for the stock" without naming the ticker (so a Samsung/TSMC story counted bullish for AAPL),
-- it forbade "neutral as a safe default", and it counted routine fund-holdings filings and
-- law-firm solicitations as news. The code now names the subject, lets neutral be a real
-- answer, asks a `relevance`, and keeps off-topic stories, filings and solicitations off the
-- chart (app/services/news_sentiment_trend_service.tone_skip_reason).
--
-- A label's meaning therefore depends on which rules wrote it, and the log is first-label-wins:
-- an old label never heals by itself. Two columns:
--   * `labeller` records the rules (TONE_LABELLER_VERSION; 2 = the 2026-10-10 rules). Old rows stay
--     NULL; migration 192 deletes the re-labellable ones and re-queues their backfill.
--   * `skip_reason` records the chart gate's verdict for an article it keeps OFF the chart
--     (off_topic / holdings_filing / solicitation / duplicate_title), so the verdict is
--     first-label-wins like the label: without a row, the nightly backfill re-scan re-asked the
--     model about every skipped article (paid) and logged the first answer that came back
--     "primary" — undoing the gate — and logged the second wire copy the gate had dropped.
--     news_sentiment_daily() counts only rows with skip_reason IS NULL.
--
-- DEPLOY ORDER: apply THIS migration BEFORE deploying the code that writes `labeller`. (The code
-- heals a missing column by writing without it — app.services.news_sentiment_trend_service.
-- upsert_log_rows — so the other order loses no label; those rows just read as old rules and are
-- re-labelled by 192. The heal re-checks for the column every 10 minutes, so no restart is
-- needed — but confirm new rows carry labeller = 2 before applying 192.) Then deploy, then 192.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS; the CHECK is dropped and re-added under one name; COMMENT
-- re-runs. Adding a nullable column with no default is catalog-only (no rewrite); the CHECK's
-- validation scans the ~43k rows under a brief ACCESS EXCLUSIVE lock (milliseconds), bounded by
-- lock_timeout so a long-running reader fails this fast instead of queueing live writes behind it.
-- news_sentiment_daily() is replaced with the same signature, body and grants plus the one
-- `skip_reason IS NULL` filter (CREATE OR REPLACE keeps its ACL; the REVOKE/GRANT re-state it).
-- No table grant or policy change: the table stays service-role only (migration 180).

BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.news_sentiment_log ADD COLUMN IF NOT EXISTS labeller smallint;

ALTER TABLE public.news_sentiment_log DROP CONSTRAINT IF EXISTS news_sentiment_log_labeller_check;
ALTER TABLE public.news_sentiment_log
    ADD CONSTRAINT news_sentiment_log_labeller_check CHECK (labeller IS NULL OR labeller BETWEEN 1 AND 100);

ALTER TABLE public.news_sentiment_log ADD COLUMN IF NOT EXISTS skip_reason text;

ALTER TABLE public.news_sentiment_log DROP CONSTRAINT IF EXISTS news_sentiment_log_skip_reason_check;
ALTER TABLE public.news_sentiment_log
    ADD CONSTRAINT news_sentiment_log_skip_reason_check CHECK (
        skip_reason IS NULL
        OR skip_reason IN ('off_topic', 'holdings_filing', 'solicitation', 'duplicate_title')
    );

COMMENT ON COLUMN public.news_sentiment_log.skip_reason IS
    'Why the chart gate keeps this article OFF the News Tone chart (news_sentiment_trend_service.'
    'tone_skip_reason): off_topic / holdings_filing / solicitation / duplicate_title; NULL = counted. '
    'Stored so the verdict is first-label-wins like the label (the backfill never re-asks).';

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
    -- Rows the chart gate keeps off the chart (skip_reason, migration 191) are not counted.
    SELECT l.et_day                                        AS day,
           count(*) FILTER (WHERE l.sentiment = 'bullish') AS bullish,
           count(*) FILTER (WHERE l.sentiment = 'bearish') AS bearish,
           count(*) FILTER (WHERE l.sentiment = 'neutral') AS neutral
      FROM public.news_sentiment_log l
     WHERE l.scope = p_scope
       AND l.skip_reason IS NULL
       AND l.et_day >= GREATEST(
               COALESCE(p_since, (now() AT TIME ZONE 'America/New_York')::date - 90),
               (now() AT TIME ZONE 'America/New_York')::date - 400
           )
     GROUP BY l.et_day
     ORDER BY l.et_day ASC;
$$;

COMMENT ON FUNCTION public.news_sentiment_daily(TEXT, DATE) IS
    'Per-ET-day counts of bullish / bearish / neutral labels for one scope since p_since '
    '(never more than 400 days back), counting only rows the chart gate keeps (skip_reason IS NULL). '
    'Days with no counted article are absent, not zero. INVOKER: its only caller is service_role.';

REVOKE ALL ON FUNCTION public.news_sentiment_daily(TEXT, DATE)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.news_sentiment_daily(TEXT, DATE) TO service_role;

COMMENT ON COLUMN public.news_sentiment_log.labeller IS
    'Which labelling rules wrote the row (news_sentiment_trend_service.TONE_LABELLER_VERSION): 2 = the '
    'scope-named question with a relevance answer and the chart gate (2026-10-10). NULL = earlier rules; '
    'migration 192 deletes those for re-labellable scopes and re-queues their backfill.';

COMMIT;

-- VERIFY (run after applying — one row: new_columns = 2, rpc_filters = true):
--   SELECT
--     (SELECT count(*) FROM information_schema.columns
--       WHERE table_schema = 'public' AND table_name = 'news_sentiment_log'
--         AND column_name IN ('labeller', 'skip_reason'))                         AS new_columns,
--     (SELECT pg_get_functiondef('public.news_sentiment_daily(text, date)'::regprocedure)
--             ILIKE '%skip_reason IS NULL%')                                       AS rpc_filters;
