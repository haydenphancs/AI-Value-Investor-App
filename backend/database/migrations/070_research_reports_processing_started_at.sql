-- 070_research_reports_processing_started_at.sql
--
-- Why: decouple the reconciliation sweep's "stuck" clock from queue-wait time.
--
-- Report generation now runs behind a global agent-run semaphore
-- (MAX_CONCURRENT_AGENT_RUNS). Under a deep backlog a report can sit in
-- status='processing' (is_refunded=false) waiting for a slot while its
-- created_at keeps aging — the in-process 600s pipeline timeout has not even
-- started counting yet (it begins only AFTER the slot is acquired). The
-- reconciliation sweep keyed off created_at, so it could refund a report that
-- was still legitimately queued. (The completion write is now conditional, so
-- this never causes a double-resolve/credit-leak — but a live queued report
-- could be refunded-then-not-delivered.)
--
-- Fix: stamp processing_started_at when the report actually acquires a slot and
-- begins agent work. The sweep then ages a STARTED report off processing_started_at
-- (a genuine hang), and a NEVER-STARTED row off created_at only after a much
-- longer "abandoned in queue" threshold — so a legitimately queued report is no
-- longer prematurely refunded.
--
-- Backfill: existing rows get NULL (never-started semantics); harmless — old
-- completed/failed rows are excluded by status, and any stale pending/processing
-- rows are reconciled via the long created_at fallback as before.
-- research_reports already has RLS; no policy changes needed. Nullable, no default.

ALTER TABLE research_reports
    ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ;

-- Sweep candidate lookup filters on (status, is_refunded, created_at) and now
-- also reads processing_started_at; the existing created_at index already covers
-- the hot filter, so no new index is required for the current 200-row sweep.
