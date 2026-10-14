-- 195_research_reports_refund_outcome.sql
--
-- Why: `research_reports.is_refunded` is the one-shot compare-and-set CLAIM on a failed or
-- deleted report's refund, and it is flipped BEFORE `refund_credits` runs. So it cannot say
-- whether the refund LANDED — yet iOS shows "Refunded 20 credits" off it. When the refund RPC
-- fails (a transport fault, `no_matching_debit`, a month-boundary `capped_to_zero` /
-- `partial`), the card still promises credits that never came back (owner decision
-- 2026-10-12: "Refund-confirmed report chip").
--
-- This column records what the refund behind `is_refunded` actually did. TEXT, not a boolean:
-- the retry pass must tell a transport fault (safe to replay on the unique per-report charge
-- ref) from a business no-op (a replay cannot help). No CHECK constraint, on purpose: a new
-- outcome name from the RPC must never make the stamp fail.
--
-- Values (written by research_reconciliation_service.record_refund_outcome):
--   refunded / already_refunded / guest / invalid  the credits are back (the RPC said so)
--   legacy     charged under the old bare-ticker ref, so it cannot be re-checked: backfilled
--              below, or stamped by the retry pass when the per-report ref finds no debit
--              (today's chip is kept)
--   manual     the owner corrected the balance by hand (OWNER_TASKS runbook) — set by SQL
--   rpc_failed          the round trip failed on the unique per-report ref; the reconciliation
--                       loop retries it ONCE on that ref -> a settled outcome or retry_rpc_failed
--   legacy_rpc_failed   the round trip failed on the legacy bare-ticker ref (or with no ref);
--                       never retried — that refund may have committed with its reply lost
--   anything else (no_matching_debit, capped_to_zero, partial, no_credits_row, unknown,
--   retry_rpc_failed) = still owed: a REFUND LEAK was paged; fix by hand, then set 'manual'.
--   NULL on a claimed row (is_refunded) = the stamp never landed (a lost reply, a SIGKILL
--   between the claim and the RPC); the API serves it as "not confirmed" and the retry pass
--   re-checks it on its per-report ref once it is 30 minutes old (measured from created_at).
--
-- Written ONLY by a separate best-effort UPDATE after the refund, in the refund's own worker
-- thread — NEVER inside the is_refunded compare-and-set, so a missing column can never break
-- the money guard. An unconfirmed label never overwrites a confirmed one.
--
-- The backfill labels 'legacy' (confirmed) ONLY the refunded rows that were CHARGED under the
-- old bare-ticker ref, so the "Refunded N credits" chip on those history rows stays exactly as
-- it is today. Those rows cannot be re-checked: a replay on the per-report ref answers
-- `no_matching_debit` whether or not their bare-ticker refund landed. The bound comes from the
-- ledger itself, not from a hard-coded deploy instant: the first deep-door `report_charge`
-- whose ref_id is `TICKER:{report uuid}` (`report_charge_ref`, the unique per-report ref) marks
-- when that scheme went live, and a research_reports row is inserted only AFTER its charge
-- committed, so every row created before that first charge was charged under the bare ticker.
-- The pattern cannot match any other ref: the legacy deep door's bare ticker has no ':', the
-- legacy direct door's `TICKER:persona` and the current direct door's
-- `TICKER:persona:{32 hex}` (same `report_charge` reason) do not end in a dashed UUID, and
-- `stock_id` cannot contain ':' (its schema pattern is ^[A-Za-z0-9.-]+$). With no per-report
-- charge yet (applied before the deploy), the bound is now(): every row refunded so far was
-- charged under the bare ticker.
--
-- Rows created AT or AFTER the bound are left unlabelled ON PURPOSE: a refund that never
-- landed (a SIGKILL between the claim and the RPC, a lost reply, a no-op paged at the time)
-- must not be dressed up as confirmed. The reconciliation loop's retry pass re-checks each one
-- on its per-report ref after a 30-minute grace: `already_refunded` / `refunded` confirm it,
-- a no-op is stamped and paged, a transport fault leaves it for the next pass, and a row that
-- answers `no_matching_debit` was charged under the bare ticker by OLD code (a container still
-- serving during the deploy overlap, or before the deploy when this file ran first), so it
-- gets 'legacy' too.
-- Idempotent by its WHERE (refund_outcome IS NULL).
--
-- The partial index serves the retry pass (claimed rows whose refund is not proven); after
-- the backfill that set is tiny.
--
-- Code deployed before this degrades: one WARNING ("apply migration 195"), the Reports list
-- serves refund_confirmed = null (iOS keeps its legacy chip), no stamps; it re-checks every 10
-- minutes, so no restart is needed once this lands. Rows the new code refunded in between are
-- left unlabelled by the bound above, and the retry pass settles their label.
--
-- No grant or policy change: research_reports is service-role only (migration 165), and the
-- table-level grants cover a new column.
--
-- Idempotent: ADD COLUMN / CREATE INDEX IF NOT EXISTS; the backfill's WHERE; COMMENT re-runs.
-- Adding a nullable column with no default is catalog-only. If the lock times out (a busy
-- moment on research_reports), run it again. The bound's subquery scans credit_transactions
-- once (an uncorrelated InitPlan), which is fine for a one-off migration.
--
-- Deploy order: either way. Applied BEFORE the backend deploy, the bound is now() and every
-- refunded row so far is labelled; refunds the old code claims afterwards stay unlabelled
-- (the old code serves no refund_confirmed, so no card changes) until, after the deploy, the
-- retry pass labels them — 50 rows per 5-minute pass, each once it is 30 minutes old. Applied
-- AFTER, the bound is the first per-report charge. A re-run is harmless: it labels only
-- bare-ticker-era rows that are still unlabelled.

BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.research_reports ADD COLUMN IF NOT EXISTS refund_outcome text;

UPDATE public.research_reports
   SET refund_outcome = 'legacy'
 WHERE is_refunded
   AND refund_outcome IS NULL
   AND created_at < COALESCE(
         (SELECT min(ct.created_at)
            FROM public.credit_transactions ct
           WHERE ct.reason = 'report_charge'
             AND ct.ref_id ~ '^[^:]+:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
         now());

CREATE INDEX IF NOT EXISTS idx_research_reports_refund_retry
    ON public.research_reports (created_at)
    WHERE is_refunded AND (refund_outcome IS NULL OR refund_outcome = 'rpc_failed');

COMMENT ON COLUMN public.research_reports.refund_outcome IS
    'What the refund behind is_refunded actually did. is_refunded is the one-shot claim, set '
    'BEFORE refund_credits runs; this is written AFTER, best-effort, by '
    'research_reconciliation_service.record_refund_outcome — never inside the is_refunded '
    'compare-and-set, and an unconfirmed label never overwrites a confirmed one. '
    'refunded / already_refunded / guest / invalid / legacy (charged under the bare-ticker ref '
    'before per-report refs, so it cannot be re-checked) / '
    'manual (corrected by hand) = the credits are back. rpc_failed = the round trip failed on '
    'the unique per-report ref; the reconciliation loop retries it ONCE on that ref -> a '
    'settled outcome or retry_rpc_failed. legacy_rpc_failed = failed on the legacy bare-ticker '
    'ref, never retried. Anything else (no_matching_debit, capped_to_zero, partial, '
    'no_credits_row, unknown, retry_rpc_failed) = still owed: a REFUND LEAK was paged; fix by '
    'hand, then set manual. NULL on a claimed row = the stamp never landed; served as not '
    'confirmed and re-checked on its per-report ref by the retry pass after a 30-minute grace. '
    'The Reports list serves this as '
    'refund_confirmed. See migration 195.';

COMMIT;

-- VERIFY (run right after applying — one row: refund_outcome_column = 1, retry_index = 1,
-- unlabelled_bare_ticker_era = 0; applied before the deploy, rows the old code claims later
-- count there until the retry pass labels them). unlabelled_refunds MAY be > 0: refunds
-- claimed at or after the first per-report charge whose stamp has not landed. The retry pass
-- labels each one once it is 30 minutes old (only a transport fault on its re-check leaves it
-- for the next pass), so unlabelled rows older than an hour after the deploy mean the pass is
-- not reaching them — check the logs for "research refund retry"):
--   WITH bound AS (
--     SELECT COALESCE(
--       (SELECT min(ct.created_at) FROM public.credit_transactions ct
--         WHERE ct.reason = 'report_charge'
--           AND ct.ref_id ~ '^[^:]+:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
--       now()) AS first_per_report_charge)
--   SELECT
--     (SELECT count(*) FROM information_schema.columns
--       WHERE table_schema = 'public' AND table_name = 'research_reports'
--         AND column_name = 'refund_outcome')                                       AS refund_outcome_column,
--     (SELECT count(*) FROM pg_indexes
--       WHERE schemaname = 'public' AND indexname = 'idx_research_reports_refund_retry') AS retry_index,
--     (SELECT count(*) FROM public.research_reports, bound
--       WHERE is_refunded AND refund_outcome IS NULL
--         AND created_at < bound.first_per_report_charge)                          AS unlabelled_bare_ticker_era,
--     (SELECT count(*) FROM public.research_reports
--       WHERE is_refunded AND refund_outcome IS NULL)                                AS unlabelled_refunds,
--     (SELECT first_per_report_charge FROM bound)                                    AS first_per_report_charge;
--
-- After a refund failure has happened (what is owed, by label):
--   SELECT refund_outcome, count(*) FROM public.research_reports WHERE is_refunded GROUP BY 1;
