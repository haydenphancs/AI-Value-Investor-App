-- 158_market_close_snapshot_previous.sql
--
-- Why: 157 stored ONE close per symbol and `price_service` used it as the day-change
-- denominator. That is wrong roughly half the time, and the failure is silent.
--
-- Caught by verifying the two price paths against each other: for AAPL the batch path
-- reported +0.00% while the single-symbol path (FMP `profile`) reported -2.51% for the
-- same instant. Both were reading correct data.
--
--   * The live price from `company-screener` / `profile` is the price of the most recent
--     session that HAS one. While the market is open that is today, in progress. While it
--     is closed — nights, weekends, and holidays like Labor Day, when this was found —
--     it is simply the last session's official close.
--   * 157 stored that same last session's close.
--   * So whenever the market is closed, price == close, and every change % collapses to
--     exactly 0.00 — a fabricated "flat market" on every tile, which is precisely the
--     class of bug `price_service` was written to avoid.
--
-- The denominator is ALWAYS the close of the session BEFORE the one the live price
-- belongs to. Knowing that requires two sessions, so this adds the second.
--
-- How `price_service` picks between them, without needing to know whether the market is
-- open: if the live price differs from `close`, the price belongs to a later session and
-- `close` is the right denominator; if it equals `close`, the live price IS that close and
-- `previous_close` is. Self-correcting at the open and at the close, with no dependency on
-- session state, holiday calendars, or the job's timing.
--
-- Nullable on purpose. The ingest fetches two sessions and the second call can fail on its
-- own; a row with `close` but no `previous_close` yields an UNKNOWN change (None), which
-- the caller hides. Degraded, never wrong — same posture as 157.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS.

ALTER TABLE public.market_close_snapshot
    ADD COLUMN IF NOT EXISTS previous_close NUMERIC;

ALTER TABLE public.market_close_snapshot
    ADD COLUMN IF NOT EXISTS previous_trade_date DATE;

COMMENT ON COLUMN public.market_close_snapshot.previous_close IS
    'Official close of the session BEFORE `trade_date`. The day-change denominator when '
    'the market is shut and the live price already equals `close` — without it every '
    'change % reads 0.00%% overnight and at weekends.';

COMMENT ON COLUMN public.market_close_snapshot.previous_trade_date IS
    'Session date for `previous_close`. Stored so a stale or skipped ingest is visible in '
    'the data rather than only in the logs.';
