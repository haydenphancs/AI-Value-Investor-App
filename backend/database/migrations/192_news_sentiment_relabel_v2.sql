-- 192_news_sentiment_relabel_v2.sql
--
-- Why: owner decision 2026-10-10 ("Re-label tickers/coins"). The News Tone labels on file were
-- written by the old rules, which a blind three-rater audit found over-directional (model vs
-- raters 70.5% agreement; 69 bullish vs 50, 62 bearish vs 52, 69 neutral vs 98 — see migration
-- 191). The log keeps the FIRST label per article, so the new rules can never replace an old
-- label by themselves, and a chart that mixed the two would show a step on the deploy day. This
-- migration, like 183 before it:
--   1. puts every re-labellable scope back in the backfill queue with NO coverage, so the worker
--      re-runs its 90 days under the new rules (its next tick, within minutes);
--   2. deletes exactly those scopes' old-rule labels (`labeller IS NULL`), and the dead rows
--      logged under the commodity detail screen's COMMODITY_<root> keys, which no reader can
--      chart (the code no longer logs them).
--
-- PRECONDITION: SENTIMENT_BACKFILL_ENABLED=true on Railway (check before applying): nothing else
-- rebuilds what this deletes. Backups: Supabase's daily backup / PITR covers the table, and the
-- labels are reproducible by the backfill.
--
-- Kept on purpose:
--   * __MARKET__ — the Market line has no backfill (its mix cannot be rebuilt), so its history
--     stays; its new labels follow the new rules from the deploy on.
--   * scopes with no backfill row, or an 'unsupported' one (^GSPC): nothing would rebuild them.
--   A scope no watchlist holds any more IS re-labelled: its old-rule rows are deleted and its row
--   re-queued, and the claim (which takes only watched tickers) rebuilds it the day someone
--   watches it again — no reader shows an unwatched scope meanwhile (the Updates tabs and their
--   chat are watchlist-scoped). Keeping its old rows would bring the old rules back on re-watch.
--   * every row the new code wrote (labeller = 2).
--
-- DEPLOY ORDER: 191 → deploy the code → wait until Railway lists the PREVIOUS deployment as
-- Removed (it overlaps the new one for up to ~5 minutes, and its old backfill worker would claim
-- these re-queued scopes and rebuild them under the OLD rules — the new worker then skips those
-- articles as already logged) → THIS migration. Step 0 below refuses to run while labels without
-- a labeller are still being written. Re-run VERIFY an hour later; if old_labels_left > 0, re-run
-- this migration (idempotent).
--
-- Cost of the re-run: about 40k articles through the live request at 25 per call, ≈ 1,700 model
-- calls — a few dollars on flash-lite (about half on Flex) — within SENTIMENT_BACKFILL_DAILY_CALLS
-- (3000). Holdings filings and law-firm solicitations are dropped BEFORE the model call now, so
-- the real count is lower. While it runs, the app shows "Building 90-day history…" /
-- "filling in 90 days…" for those tickers, newest weeks first.
--
-- Idempotent: step 1 matches only scopes that still HAVE old-rule rows and step 2 deletes only
-- those rows, so a second run finds nothing and changes nothing. No schema change.
--
-- VERIFY (run after applying — one row):
--   SELECT
--     (SELECT count(*) FROM public.news_sentiment_log l
--       WHERE l.labeller IS NULL AND l.scope <> '__MARKET__'
--         AND EXISTS (SELECT 1 FROM public.news_sentiment_backfill b
--                      WHERE b.scope = l.scope AND b.status <> 'unsupported'))   AS old_labels_left,   -- 0
--     (SELECT count(*) FROM public.news_sentiment_log WHERE scope LIKE 'COMMODITY\_%') AS dead_rows_left, -- 0
--     (SELECT count(*) FROM public.news_sentiment_log WHERE scope = '__MARKET__')       AS market_labels,   -- unchanged
--     (SELECT count(*) FROM public.news_sentiment_backfill
--       WHERE status IN ('queued', 'running'))                                          AS tickers_requeued; -- ~30
--   Before applying, this must show only labeller 2 (no NULL):
--     SELECT labeller, count(*) FROM public.news_sentiment_log
--      WHERE labelled_at > now() - interval '1 hour' GROUP BY 1;
--   An hour or two later every WATCHED row is 'done' again (or 'unsupported' for ^GSPC; an
--   unwatched one stays 'queued' until it is watched again), and
--   `SELECT labeller, count(*) FROM news_sentiment_log GROUP BY 1` shows the new rows under 2.

BEGIN;

-- 0. Refuse to run while an old-rule writer may still be live (pre-deploy code, the previous
--    deployment's overlap, or a process still healing a missing 191 column):
--    * a NULL-labeller label in the last 15 minutes (live labels carry their write time), or
--    * a backfill run holding a live lease — its rows carry the run's START time, so the first
--      check cannot see an old-code run that began earlier and is still writing. A new-code run
--      also refuses it: re-apply a few minutes later (runs take minutes; the step is idempotent).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.news_sentiment_log
                WHERE labeller IS NULL AND labelled_at > now() - interval '15 minutes') THEN
        RAISE EXCEPTION '192: labels without a labeller were written in the last 15 minutes; finish the deploy (previous deployment Removed, 191 applied) first';
    END IF;
    IF EXISTS (SELECT 1 FROM public.news_sentiment_backfill
                WHERE status = 'running' AND lease_until > now()) THEN
        RAISE EXCEPTION '192: a backfill run is in progress (status running, lease live); re-apply in a few minutes';
    END IF;
END $$;

-- 1. Re-queue every re-labellable scope that still holds old-rule rows, with no coverage. The
--    claim token / lease are cleared, so a worker holding one of these scopes right now finds its
--    lease gone at its next renew and stops without writing its finish. Runs BEFORE the delete,
--    because it finds its scopes by their old-rule rows.
UPDATE public.news_sentiment_backfill b
   SET status         = 'queued',
       covered_from   = NULL,
       covered_to     = NULL,
       attempts       = 0,
       last_failed_at = NULL,
       last_error     = NULL,
       claim_token    = NULL,
       lease_until    = NULL,
       next_run_at    = now(),
       requested_at   = now(),
       updated_at     = now()
 WHERE b.status <> 'unsupported'
   AND b.scope <> '__MARKET__'
   AND EXISTS (
       SELECT 1
         FROM public.news_sentiment_log l
        WHERE l.scope = b.scope
          AND l.labeller IS NULL
   );

-- 2. DESTRUCTIVE (bounded): deletes the old-rule labels of exactly the scopes step 1 re-queued,
--    which the backfill rebuilds under the new rules (reproducible; nothing is lost that is not
--    being rebuilt), plus the COMMODITY_<root> rows that no endpoint can read.
DELETE FROM public.news_sentiment_log l
 WHERE l.labeller IS NULL
   AND l.scope <> '__MARKET__'
   AND EXISTS (
       SELECT 1
         FROM public.news_sentiment_backfill b
        WHERE b.scope = l.scope
          AND b.status <> 'unsupported'
   );

-- 3. DESTRUCTIVE: deletes every COMMODITY_<root> label (the commodity detail screen's cache key).
--    No endpoint can read it (is_trend_scope refuses '_') and the code no longer writes it; it is
--    not rebuilt because nothing was ever charted from it.
DELETE FROM public.news_sentiment_log
 WHERE scope LIKE 'COMMODITY\_%';

COMMIT;
