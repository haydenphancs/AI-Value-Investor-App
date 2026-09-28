-- 182_news_sentiment_backfill_fixes.sql
--
-- Why: an adversarial review of migration 181 (applied 2026-09-27, backfill switch still
-- OFF) confirmed four queue defects. None has run in production yet — the flag is off — so
-- this lands before the first backfill does.
--
--   1. 'unsupported' scopes were never re-checked. The worker finishes an unsupported scope
--      (a `^` index, a withdrawn commodity) with next_run_at = now + 30 days so a later deploy
--      that adds a news route picks it up — but the claim only selected queued / done /
--      failed / running, and enqueue / discover never touch that status, so the 30-day
--      recheck was dead. The claim now includes 'unsupported'; next_run_at still spaces the
--      re-checks, and a still-unsupported scope costs one claim/finish pair, no network call.
--
--   2. The attempt cap's once-a-day clock was updated_at — which claim, renew, finish (every
--      status, deferrals included) AND enqueue all write. A capped scope whose daily retry was
--      merely DEFERRED (daily budget spent, a 429, a busy model, a deploy hand-back) was locked
--      out for another full 24 h, showing "building" the whole time; a popular failing ticker,
--      added by anyone once a day, was never retried at all. The clock is now its own column,
--      `last_failed_at`: set by a 'failed' finish, cleared by 'done' / 'unsupported', left alone
--      by a deferral — and set when a claim takes over a LAPSED lease (the worker died without
--      finishing), so a scope that kills the worker cannot loop every 10 minutes. enqueue no
--      longer writes updated_at either.
--
--   3. A stale 'done' row read as "ready". A ticker that left every watchlist keeps its 'done'
--      row while its coverage goes stale; re-added, enqueue pulled it forward but left it
--      'done', so the app showed "ready" over a history with a gap. enqueue now turns such a
--      row back to 'queued' (the WHERE already limits it to coverage ending before yesterday,
--      so a ticker kept up to date is untouched) and the app says "Building…" until it is.
--
--   4. The claim matched the RAW ticker (`w.ticker = s.scope`) while discover and enqueue store
--      upper(btrim(ticker)). A legacy row such as 'nvda ' (POST /tracking/holdings stored it
--      unstripped until 2026-09-09) was queued as 'NVDA' but could never be claimed: the scope
--      stayed 'queued' — and the app on "Building 90-day history…" — forever. The claim now
--      compares the same normalized form, on a new expression index.
--
--   Deliberately NOT changed: "watched" still counts every watchlist row, per-install guest
--   rows included. That is the universe every background job uses (get_top_watchlist_tickers,
--   the news pre-warm, price alerts — migration 108 accepted it), a guest install's rows are
--   still claimable when it signs in, and at today's scale the cost is cents. Narrowing only
--   the backfill would make it disagree with the live labelling of the same tickers.
--
-- Idempotent: ADD COLUMN / CREATE INDEX IF NOT EXISTS, a guarded one-off seed, CREATE OR
-- REPLACE FUNCTION with the same signatures, declarative REVOKE/GRANT. Safe to re-run.
-- The worker needs no deploy to use it (every RPC keeps its parameters).
--
-- VERIFY (run after applying):
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name = 'news_sentiment_backfill' AND column_name = 'last_failed_at';     -- 1 row
--   SELECT pg_get_functiondef('public.claim_sentiment_backfill(uuid, integer, integer, integer)'::regprocedure)
--          ILIKE '%last_failed_at%';                                                     -- t
--   SELECT pg_get_functiondef('public.enqueue_sentiment_backfill(text[])'::regprocedure)
--          ILIKE '%updated_at%';                                                         -- f
--   SELECT indexname FROM pg_indexes WHERE indexname = 'idx_watchlist_items_ticker_norm'; -- 1 row
--   SELECT has_function_privilege('authenticated',
--          'public.claim_sentiment_backfill(uuid, integer, integer, integer)', 'EXECUTE'); -- f

BEGIN;

-- ── The attempt cap's own clock ───────────────────────────────────────────────────
ALTER TABLE public.news_sentiment_backfill
    ADD COLUMN IF NOT EXISTS last_failed_at TIMESTAMPTZ;

COMMENT ON COLUMN public.news_sentiment_backfill.last_failed_at IS
    'When the scope last failed (a failed finish, or a lapsed lease taken over). A scope at '
    'the attempt cap is retried once this is 24 hours old. Cleared by done / unsupported; a '
    'deferral leaves it alone.';

-- One-off seed for rows that failed before this column existed (none while the flag is off).
UPDATE public.news_sentiment_backfill
   SET last_failed_at = updated_at
 WHERE status = 'failed' AND last_failed_at IS NULL;

-- The claim's "still watched" test compares the normalized ticker (see 4 above).
CREATE INDEX IF NOT EXISTS idx_watchlist_items_ticker_norm
    ON public.watchlist_items (upper(btrim(ticker)));

-- ── Claim ────────────────────────────────────────────────────────────────────────
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
       SET status         = 'running',
           claim_token    = p_token,
           lease_until    = now() + make_interval(secs => GREATEST(COALESCE(p_lease_seconds, 300), 60)),
           attempts       = b.attempts + 1,
           -- Taking over a LAPSED lease means the last worker died without finishing: that
           -- counts as a failure for the cap's clock (a scope that kills the worker must not
           -- be retried every lease).
           last_failed_at = CASE WHEN b.status = 'running' THEN now() ELSE b.last_failed_at END,
           updated_at     = now()
     WHERE b.scope IN (
           SELECT s.scope
             FROM public.news_sentiment_backfill s
            WHERE s.next_run_at <= now()
              -- 'unsupported' too: its next_run_at is the 30-day re-check.
              AND s.status IN ('queued', 'done', 'failed', 'running', 'unsupported')
              -- A running row is claimable only once its lease has lapsed (a dead worker).
              AND (s.status <> 'running' OR s.lease_until IS NULL OR s.lease_until < now())
              -- A scope that keeps failing is tried at most once a day after its last
              -- failure, never dropped; a deferral does not move this clock.
              AND (s.attempts < GREATEST(COALESCE(p_max_attempts, 5), 1)
                   OR s.last_failed_at IS NULL
                   OR s.last_failed_at < now() - interval '24 hours')
              -- Only tickers someone still watches — normalized exactly like discover and
              -- enqueue store the scope, or a legacy 'nvda ' row is never claimable.
              AND EXISTS (SELECT 1 FROM public.watchlist_items w
                           WHERE upper(btrim(w.ticker)) = s.scope)
            ORDER BY s.next_run_at ASC
            LIMIT LEAST(GREATEST(COALESCE(p_limit, 1), 1), 10)
            FOR UPDATE SKIP LOCKED
           )
    RETURNING b.*;
$$;

COMMENT ON FUNCTION public.claim_sentiment_backfill(UUID, INTEGER, INTEGER, INTEGER) IS
    'Claims up to p_limit (max 10) due, still-watched scopes (unsupported ones on their 30-day '
    're-check) under token p_token with a lease of p_lease_seconds (min '
    '60) on the database clock. INVOKER: its only caller is service_role.';

-- ── Finish ───────────────────────────────────────────────────────────────────────
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
       SET status         = p_status,
           next_run_at    = COALESCE(p_next_run_at, now() + interval '1 day'),
           covered_from   = COALESCE(p_covered_from, covered_from),
           covered_to     = COALESCE(p_covered_to, covered_to),
           articles       = GREATEST(COALESCE(p_articles, 0), 0),
           labels         = GREATEST(COALESCE(p_labels, 0), 0),
           -- Success clears the failure count; a deferral gives back the attempt it cost.
           attempts       = CASE
                                WHEN p_status IN ('done', 'unsupported') THEN 0
                                WHEN p_status = 'queued' THEN GREATEST(attempts - 1, 0)
                                ELSE attempts
                            END,
           -- The cap's clock: moved by a failure only, cleared by success, kept by a deferral.
           last_failed_at = CASE
                                WHEN p_status = 'failed' THEN now()
                                WHEN p_status IN ('done', 'unsupported') THEN NULL
                                ELSE last_failed_at
                            END,
           last_error     = CASE WHEN p_error IS NULL THEN NULL ELSE left(p_error, 500) END,
           last_run_at    = now(),
           claim_token    = NULL,
           lease_until    = NULL,
           updated_at     = now()
     WHERE scope = p_scope
       AND claim_token = p_token;
    RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION public.finish_sentiment_backfill(TEXT, UUID, TEXT, TIMESTAMPTZ, DATE, DATE, INTEGER, INTEGER, TEXT) IS
    'Closes a run for the holder of p_token: done / failed / unsupported / queued (deferred). '
    'Only failed moves last_failed_at. false = the claim was lost and nothing was written.';

-- ── Enqueue (the add-ticker nudge) ─────────────────────────────────────────────────
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
           -- Never the run timestamps or the cap's clock: a nudge is not a run.
           SET next_run_at  = LEAST(b.next_run_at, now()),
               requested_at = now(),
               -- A stale 'done' is not a finished history any more.
               status       = CASE WHEN b.status = 'done' THEN 'queued' ELSE b.status END
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
    'alone, and a stale done row goes back to queued. Never touches updated_at or the '
    'attempt cap''s clock. Returns rows inserted or moved.';

REVOKE ALL ON FUNCTION public.claim_sentiment_backfill(UUID, INTEGER, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_sentiment_backfill(TEXT, UUID, TEXT, TIMESTAMPTZ, DATE, DATE, INTEGER, INTEGER, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.enqueue_sentiment_backfill(TEXT[])
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_sentiment_backfill(UUID, INTEGER, INTEGER, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_sentiment_backfill(TEXT, UUID, TEXT, TIMESTAMPTZ, DATE, DATE, INTEGER, INTEGER, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.enqueue_sentiment_backfill(TEXT[]) TO service_role;

COMMIT;
