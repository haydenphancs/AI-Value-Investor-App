-- 183_news_sentiment_backfill_relabel.sql
--
-- Why: the 90-day backfill's FIRST pass ran on the wrong labeller. SENTIMENT_BACKFILL_ENABLED was
-- already on when commit bdce0010 deployed (2026-09-27 21:42 MDT), so between 03:00 and 04:59 UTC
-- on 2026-09-28 it wrote 34,119 `source='backfill'` labels with its first design: a shorter,
-- sentiment-only prompt at temperature 0. That prompt then FAILED calibration (79.7% agreement
-- with the live labels vs the live labeller's own 85.7% on the same 300 articles), leaning
-- bullish — and the production labels show the same lean: 21% bearish against 27% for live
-- labels overall, 13% vs 26% on the days both exist. On the chart that is a tone jump exactly
-- where backfilled days meet live ones.
--
-- Commit 610021e2 (deployed 2026-09-28 17:53:17 MDT = 23:53:17 UTC) makes the backfill send the
-- LIVE enrichment request itself, and passed calibration (83.7% vs 83.3%; the owner's re-run
-- 85.7% vs 84.0%). But the log keeps the FIRST label per article (`ignore_duplicates`), so the
-- new labeller can never replace the old labels by itself. This migration:
--   1. puts every scope that holds old-labeller rows back in the queue with NO coverage, so the
--      worker re-runs its full 90 days (its next tick, within ~3 minutes);
--   2. deletes exactly those rows — backfill labels written BEFORE the new build went live.
-- Live and seed labels are untouched, and so is anything the new build has written since.
--
-- Cost of the re-run: about 34k articles through the live request, ≈ 1,400 model calls, about
-- $2 on standard flash-lite (roughly half on Flex), within SENTIMENT_BACKFILL_DAILY_CALLS (3000).
-- While it runs, the app shows "Building 90-day history…" / "filling in 90 days…" for those
-- tickers (their rows are queued with no coverage), and the bars come back newest weeks first.
--
-- Idempotent: step 1 only matches scopes that still HAVE old-labeller rows and step 2 deletes
-- only those rows, so a second run finds nothing and changes nothing. No schema change.
--
-- VERIFY (run after applying — one row):
--   SELECT
--     (SELECT count(*) FROM public.news_sentiment_log
--       WHERE source = 'backfill' AND labelled_at < '2026-09-28 23:53:17+00')  AS old_labels_left,   -- 0
--     (SELECT count(*) FROM public.news_sentiment_log WHERE source = 'live')   AS live_labels,       -- unchanged
--     (SELECT count(*) FROM public.news_sentiment_backfill
--       WHERE status IN ('queued', 'running'))                                 AS tickers_requeued;  -- ~28
--   An hour or two later every row is 'done' again (or 'unsupported' for ^GSPC) and
--   `source = 'backfill'` holds tens of thousands of rows again.

BEGIN;

-- 1. Re-queue every scope the old labeller touched, with no coverage: the worker plans the whole
--    horizon again. claim_token/lease_until are cleared, so a worker holding one of these scopes
--    right now finds its lease gone at its next renew and stops without writing its finish.
--    Runs BEFORE the delete, because it finds its scopes by their old-labeller rows.
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
   AND EXISTS (
       SELECT 1
         FROM public.news_sentiment_log l
        WHERE l.scope = b.scope
          AND l.source = 'backfill'
          AND l.labelled_at < '2026-09-28 23:53:17+00'
   );

-- 2. DESTRUCTIVE (bounded): deletes the backfill labels the old sentiment-only labeller wrote —
--    every `source='backfill'` row labelled before commit 610021e2 went live (23:53:17 UTC on
--    2026-09-28). Nothing else matches: live and seed rows have another source, and the new
--    build's rows are all later. The data is reproducible — step 1 queued its re-labelling with
--    the calibrated live request (≈ $2); nothing is lost that is not being rebuilt.
DELETE FROM public.news_sentiment_log
 WHERE source = 'backfill'
   AND labelled_at < '2026-09-28 23:53:17+00';

COMMIT;
