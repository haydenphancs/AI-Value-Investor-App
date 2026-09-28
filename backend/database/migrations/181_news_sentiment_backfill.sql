-- 181_news_sentiment_backfill.sql
--
-- Why: the Updates news-tone chart (migration 180) only had history from the day it went
-- live, so every ticker opened on ~4 bars and a newly watched ticker had none. FMP serves
-- 90 days of news through the same licensed endpoints the feed already uses (measured
-- 2026-09-27: ORCL 682 articles over 90 days, BTCUSD ~136/day), so a background job can fetch
-- that history once per ticker, have Cay AI label each article with the SAME rubric as the
-- live path, and write the labels — never the text — into news_sentiment_log. A nightly
-- top-up then keeps every watched ticker complete, including those outside the sweeper's
-- top-200 universe that are only labelled when someone scrolls their feed.
--
-- Cost is per TICKER, never per user: news_sentiment_log is keyed by scope, so a ticker is
-- backfilled once and every user who watches it reads the same rows.
--
--   A. news_sentiment_log.source gains 'backfill'. The 180 CHECK was inline and unnamed, so a
--      DO block drops whichever CHECK on the table mentions `source` and the constraint is
--      re-added under an explicit name. Plus a nullable `model` column: which model labelled
--      the row (the news model is switchable — app/services/news_llm.py — and labels from
--      different models must stay auditable). Rows written before this migration stay NULL.
--
--   B. public.news_sentiment_backfill — one row per watched scope: its status, a lease with a
--      FENCING TOKEN (two Railway instances overlap during a deploy; a worker whose lease
--      expired must not overwrite the new holder's progress), the covered day range, and the
--      next time it is due (the nightly top-up).
--
--   C. Functions, all SECURITY INVOKER (their only caller is service_role, which holds the
--      grants — the 179/180 template):
--        claim_sentiment_backfill  — hand out due scopes (FOR UPDATE SKIP LOCKED + lease on the
--                                    DB clock); only scopes still on some watchlist; attempts
--                                    +1 per claim, and a scope at the attempt cap is retried
--                                    at most once a day.
--        renew_sentiment_backfill  — extend the lease and record progress; FENCED on the token.
--        finish_sentiment_backfill — close a run; FENCED on the token. 'queued' = deferred
--                                    (429, budget): the attempt it cost is given back.
--        enqueue_sentiment_backfill — the add-ticker nudge; a scope already covered is a no-op.
--        discover_sentiment_backfill — queue every watched ticker not seen yet. SQL-side on
--                                    purpose: a PostgREST read would silently stop at 1,000.
--
--   D. RLS + grants: service_role only. iOS never reads any of this directly.
--
-- Deploy order: apply BEFORE turning on SENTIMENT_BACKFILL_ENABLED (it defaults to false).
-- The live label writer already sends the new `model` column; before this migration it
-- retries without it (logged once), so no live label is lost either way.
--
-- Idempotent: the CHECK is dropped and re-added by name, ADD COLUMN IF NOT EXISTS, CREATE
-- TABLE / INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP POLICY IF EXISTS before
-- CREATE, declarative REVOKE/GRANT. Safe to re-run.
--
-- VERIFY (run after applying):
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conname = 'news_sentiment_log_source_check';              -- ... 'backfill' ...
--   SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'news_sentiment_backfill'; -- t
--   SELECT has_function_privilege('authenticated',
--          'public.claim_sentiment_backfill(uuid, integer, integer, integer)', 'EXECUTE');  -- f
--   SELECT public.discover_sentiment_backfill();       -- number of watched tickers queued
--   SELECT scope, status, next_run_at FROM public.news_sentiment_backfill ORDER BY scope;

BEGIN;

-- ── A. news_sentiment_log: 'backfill' source + the labelling model ───────────────
DO $$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT con.conname
          FROM pg_constraint con
         WHERE con.conrelid = 'public.news_sentiment_log'::regclass
           AND con.contype = 'c'
           AND pg_get_constraintdef(con.oid) ILIKE '%source%'
    LOOP
        EXECUTE format('ALTER TABLE public.news_sentiment_log DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE public.news_sentiment_log
    ADD CONSTRAINT news_sentiment_log_source_check
    CHECK (source IN ('live', 'seed', 'backfill'));

ALTER TABLE public.news_sentiment_log
    ADD COLUMN IF NOT EXISTS model TEXT CHECK (model IS NULL OR char_length(model) <= 80);

COMMENT ON COLUMN public.news_sentiment_log.model IS
    'The model that produced the label (news_llm.news_model_name()); NULL for rows written '
    'before migration 181.';

-- ── B. The per-scope backfill queue ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.news_sentiment_backfill (
    -- A watched ticker as the watchlist stores it (coins as pairs). Never '__MARKET__': the
    -- Market feed's mix cannot be rebuilt for past dates, so it fills day by day instead.
    scope         TEXT        PRIMARY KEY CHECK (char_length(scope) BETWEEN 1 AND 32),
    status        TEXT        NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'running', 'done', 'failed', 'unsupported')),
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_run_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The fencing token of the current claim; renew/finish must present it.
    claim_token   UUID,
    lease_until   TIMESTAMPTZ,
    attempts      INTEGER     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    -- The contiguous ET-day range whose news has been fetched and labelled.
    covered_from  DATE,
    covered_to    DATE,
    -- Last run's counts (articles fetched, labels written).
    articles      INTEGER     NOT NULL DEFAULT 0 CHECK (articles >= 0),
    labels        INTEGER     NOT NULL DEFAULT 0 CHECK (labels >= 0),
    last_run_at   TIMESTAMPTZ,
    last_error    TEXT        CHECK (last_error IS NULL OR char_length(last_error) <= 500),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_news_sentiment_backfill_due
    ON public.news_sentiment_backfill (next_run_at);

COMMENT ON TABLE public.news_sentiment_backfill IS
    'One row per watched ticker for the news-sentiment backfill: status, a fenced lease, the '
    'ET-day range already labelled into news_sentiment_log, and when it is next due (a nightly '
    'top-up). Worked by app/services/news_sentiment_backfill_service.py through the '
    'claim/renew/finish/enqueue/discover_sentiment_backfill functions. Per ticker, never per '
    'user.';

-- ── C. Functions ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.claim_sentiment_backfill(
    p_token          UUID,
    p_limit          INTEGER,
    p_lease_seconds  INTEGER,
    p_max_attempts   INTEGER
)
RETURNS SETOF public.news_sentiment_backfill
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    UPDATE public.news_sentiment_backfill b
       SET status      = 'running',
           claim_token = p_token,
           lease_until = now() + make_interval(secs => GREATEST(COALESCE(p_lease_seconds, 300), 60)),
           attempts    = b.attempts + 1,
           updated_at  = now()
     WHERE b.scope IN (
           SELECT s.scope
             FROM public.news_sentiment_backfill s
            WHERE s.next_run_at <= now()
              AND s.status IN ('queued', 'done', 'failed', 'running')
              -- A running row is claimable only once its lease has lapsed (a dead worker).
              AND (s.status <> 'running' OR s.lease_until IS NULL OR s.lease_until < now())
              -- A scope that keeps failing is tried at most once a day, never dropped.
              AND (s.attempts < GREATEST(COALESCE(p_max_attempts, 5), 1)
                   OR s.updated_at < now() - interval '24 hours')
              -- Only tickers someone still watches.
              AND EXISTS (SELECT 1 FROM public.watchlist_items w WHERE w.ticker = s.scope)
            ORDER BY s.next_run_at ASC
            LIMIT LEAST(GREATEST(COALESCE(p_limit, 1), 1), 10)
            FOR UPDATE SKIP LOCKED
           )
    RETURNING b.*;
$$;

COMMENT ON FUNCTION public.claim_sentiment_backfill(UUID, INTEGER, INTEGER, INTEGER) IS
    'Claims up to p_limit (max 10) due, still-watched scopes under token p_token with a lease '
    'of p_lease_seconds (min 60) on the database clock. INVOKER: its only caller is service_role.';

CREATE OR REPLACE FUNCTION public.renew_sentiment_backfill(
    p_scope          TEXT,
    p_token          UUID,
    p_lease_seconds  INTEGER,
    p_covered_from   DATE,
    p_covered_to     DATE
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
BEGIN
    UPDATE public.news_sentiment_backfill
       SET lease_until  = now() + make_interval(secs => GREATEST(COALESCE(p_lease_seconds, 300), 60)),
           covered_from = COALESCE(p_covered_from, covered_from),
           covered_to   = COALESCE(p_covered_to, covered_to),
           updated_at   = now()
     WHERE scope = p_scope
       AND claim_token = p_token
       AND status = 'running';
    RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION public.renew_sentiment_backfill(TEXT, UUID, INTEGER, DATE, DATE) IS
    'Extends the lease and records the covered range — only for the holder of p_token. '
    'false = the claim was lost; the worker must stop.';

CREATE OR REPLACE FUNCTION public.finish_sentiment_backfill(
    p_scope         TEXT,
    p_token         UUID,
    p_status        TEXT,
    p_next_run_at   TIMESTAMPTZ,
    p_covered_from  DATE,
    p_covered_to    DATE,
    p_articles      INTEGER,
    p_labels        INTEGER,
    p_error         TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF p_status IS NULL OR p_status NOT IN ('queued', 'done', 'failed', 'unsupported') THEN
        RAISE EXCEPTION 'finish_sentiment_backfill: invalid status %', p_status
            USING ERRCODE = '22023';  -- invalid_parameter_value
    END IF;

    UPDATE public.news_sentiment_backfill
       SET status       = p_status,
           next_run_at  = COALESCE(p_next_run_at, now() + interval '1 day'),
           covered_from = COALESCE(p_covered_from, covered_from),
           covered_to   = COALESCE(p_covered_to, covered_to),
           articles     = GREATEST(COALESCE(p_articles, 0), 0),
           labels       = GREATEST(COALESCE(p_labels, 0), 0),
           -- Success clears the failure count; a deferral gives back the attempt it cost.
           attempts     = CASE
                              WHEN p_status IN ('done', 'unsupported') THEN 0
                              WHEN p_status = 'queued' THEN GREATEST(attempts - 1, 0)
                              ELSE attempts
                          END,
           last_error   = CASE WHEN p_error IS NULL THEN NULL ELSE left(p_error, 500) END,
           last_run_at  = now(),
           claim_token  = NULL,
           lease_until  = NULL,
           updated_at   = now()
     WHERE scope = p_scope
       AND claim_token = p_token;
    RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION public.finish_sentiment_backfill(TEXT, UUID, TEXT, TIMESTAMPTZ, DATE, DATE, INTEGER, INTEGER, TEXT) IS
    'Closes a run for the holder of p_token: done / failed / unsupported / queued (deferred). '
    'false = the claim was lost and nothing was written.';

CREATE OR REPLACE FUNCTION public.enqueue_sentiment_backfill(p_scopes TEXT[])
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_count INTEGER;
    v_today DATE := (now() AT TIME ZONE 'America/New_York')::date;
BEGIN
    WITH wanted AS (
        SELECT DISTINCT upper(btrim(s)) AS scope
          FROM unnest(COALESCE(p_scopes, ARRAY[]::text[])) AS s
         WHERE s IS NOT NULL
           AND char_length(btrim(s)) BETWEEN 1 AND 32
           AND upper(btrim(s)) <> '__MARKET__'
         LIMIT 100
    ),
    upserted AS (
        INSERT INTO public.news_sentiment_backfill AS b (scope, status, requested_at, next_run_at)
        SELECT w.scope, 'queued', now(), now()
          FROM wanted w
        ON CONFLICT (scope) DO UPDATE
           SET next_run_at  = LEAST(b.next_run_at, now()),
               requested_at = now(),
               updated_at   = now()
         -- Re-adding a covered ticker is a no-op; only a stale or never-finished one moves up.
         WHERE b.status IN ('queued', 'done', 'failed')
           AND (b.covered_to IS NULL OR b.covered_to < v_today - 1)
        RETURNING 1
    )
    SELECT count(*) INTO v_count FROM upserted;
    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION public.enqueue_sentiment_backfill(TEXT[]) IS
    'Queues (or pulls forward) up to 100 scopes; a scope covered through yesterday is left '
    'alone. Returns rows inserted or moved.';

CREATE OR REPLACE FUNCTION public.discover_sentiment_backfill()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    INSERT INTO public.news_sentiment_backfill (scope, status, requested_at, next_run_at)
    SELECT DISTINCT upper(btrim(w.ticker)), 'queued', now(), now()
      FROM public.watchlist_items w
     WHERE w.ticker IS NOT NULL
       AND char_length(btrim(w.ticker)) BETWEEN 1 AND 32
    ON CONFLICT (scope) DO NOTHING;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION public.discover_sentiment_backfill() IS
    'Queues every watched ticker that has no backfill row yet. Returns the number queued.';

REVOKE ALL ON FUNCTION public.claim_sentiment_backfill(UUID, INTEGER, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.renew_sentiment_backfill(TEXT, UUID, INTEGER, DATE, DATE)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_sentiment_backfill(TEXT, UUID, TEXT, TIMESTAMPTZ, DATE, DATE, INTEGER, INTEGER, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.enqueue_sentiment_backfill(TEXT[])
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.discover_sentiment_backfill()
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.claim_sentiment_backfill(UUID, INTEGER, INTEGER, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.renew_sentiment_backfill(TEXT, UUID, INTEGER, DATE, DATE) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_sentiment_backfill(TEXT, UUID, TEXT, TIMESTAMPTZ, DATE, DATE, INTEGER, INTEGER, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.enqueue_sentiment_backfill(TEXT[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.discover_sentiment_backfill() TO service_role;

-- ── D. RLS + grants: service_role only ───────────────────────────────────────────
ALTER TABLE public.news_sentiment_backfill ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "news_sentiment_backfill_service_all" ON public.news_sentiment_backfill;
CREATE POLICY "news_sentiment_backfill_service_all" ON public.news_sentiment_backfill
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.news_sentiment_backfill FROM anon, authenticated;
-- A table with no GRANT is owner-only, not open (169's lesson). No sequence: text key.
GRANT ALL ON public.news_sentiment_backfill TO service_role;

COMMIT;
