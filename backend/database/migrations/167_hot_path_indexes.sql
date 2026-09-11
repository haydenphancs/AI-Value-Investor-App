-- 167_hot_path_indexes.sql
--
-- Why: four hot queries have no index whose leading columns match their filter + order,
-- found by matching every `.eq/.gt/.gte/.order` chain in the backend against the index
-- inventory in the snapshot. All four are additive; one strict left-prefix index is dropped
-- because the new composite subsumes it.
--
-- 1. credit_transactions — `credit_history_service.list_history` keyset-paginates
--        WHERE user_id = ? [AND id < cursor] ORDER BY id DESC LIMIT n+1
--    The only user-scoped index is (user_id, created_at DESC); the `id`-ordered walk sorts
--    the user's whole ledger per page. This is the fastest-growing user table in the schema
--    (one row per chat turn, two per report spend), so the cost is O(rows-per-user) today
--    and grows with usage. Index: (user_id, id DESC).
--
-- 2. research_reports — `trending_service` reads
--        WHERE status = 'completed' AND created_at >= since [AND created_at < until]
--    The status-leading index is partial on pending/processing (idx_reports_status_pending)
--    and the completed-partial indexes lead with ticker or user_id; the best the planner can
--    do today is a FULL scan of idx_reports_user_completed (same partial predicate) plus a
--    created_at filter. Index: (created_at DESC) WHERE status = 'completed' — a predicate
--    that exactly matches the filter, so the window becomes a bounded range scan.
--
-- 3. whale_trades — `smart_money_sender` runs, every notification cycle,
--        WHERE created_at > since ORDER BY created_at DESC LIMIT 1000
--    and `tracking_service` runs
--        WHERE ticker IN (…) AND created_at >= cutoff ORDER BY created_at DESC LIMIT 500
--    Existing indexes: (trade_group_id), (ticker), (whale_id, created_at DESC), and the
--    dedup UNIQUE. Nothing leads with created_at, so the sender is a table-wide seq scan +
--    top-N sort per cycle. Indexes: (created_at DESC) for the sender, and
--    (ticker, created_at DESC) for tracking_service — note that serves its FILTER (the
--    `IN (...)` on ticker is not an equality, so the created_at ORDER BY still sorts, over a
--    much smaller set). The bare (ticker) index is a strict left-prefix of the second and
--    is dropped.
--
-- 4. chat_sessions — the list query orders `last_message_at DESC NULLS LAST`; the index is
--    DESC (i.e. NULLS FIRST) so the planner sorts explicitly. The column is NOT NULL, so the
--    NULLS clause is a no-op semantically and only a pessimisation for the planner. Fixed on
--    the CODE side (chat.py drops `nullsfirst=False`) — no DDL here.
--
-- Not done, deliberately: widening idx_reports_status_pending to include 'failed' for the
-- reconciliation sweep (the sweep's real cost driver is elsewhere and the table is small),
-- and dropping the seven never-filtered indexes the review listed — those need
-- pg_stat_user_indexes.idx_scan from production first.
--
-- Plain CREATE INDEX (not CONCURRENTLY): the Supabase SQL editor runs a script in one
-- transaction, where CONCURRENTLY is not allowed, and every table here is small enough that
-- the brief write lock is unnoticeable. Idempotent via IF NOT EXISTS / IF EXISTS.

CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id_desc
    ON public.credit_transactions (user_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_reports_completed_created
    ON public.research_reports (created_at DESC)
    WHERE status = 'completed';

CREATE INDEX IF NOT EXISTS idx_whale_trades_created_at
    ON public.whale_trades (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_whale_trades_ticker_created
    ON public.whale_trades (ticker, created_at DESC);

-- Strict left-prefix of idx_whale_trades_ticker_created — pure write cost once that exists.
DROP INDEX IF EXISTS public.idx_whale_trades_ticker;
