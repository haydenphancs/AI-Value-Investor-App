-- 184_calendar_quarter_benchmarks.sql
--
-- Why: the QUARTERLY sector/industry benchmark rows were keyed
-- "<FISCAL quarter number>'<calendar year of the period end>" (period_type 'quarterly'),
-- and Growth, Profit Power and the report's Fundamentals drill-down joined a company's
-- quarter to that key. For any company whose fiscal quarters are not calendar quarters
-- the join picked the WRONG peer quarter, and the medians themselves pooled mismatched
-- quarters:
--   * Microsoft fiscal Q1 '26 (Jul-Sep 2025) → "Q1'25" = peers' Jan-Mar 2025 (6 months stale)
--   * Nvidia Q4 FY25 (Nov 2024-Jan 2025)     → "Q4'25" = peers' Oct-Dec 2025 (~10 months AHEAD)
--   * Apple's Dec quarter (fiscal Q1)        → peers' Jan-Mar
-- (Financials-tab deep check 2026-09-30, finding #34.) The interim fix hid the quarterly
-- peer line for such companies.
--
-- Fix: quarterly rows are now keyed by the CALENDAR quarter the period ENDS in
-- (`app/utils/period_labels.calendar_quarter_label`; an end on day 1-7 counts as the
-- previous month, for 52/53-week filers), and stored under a NEW period_type
-- 'calendar_quarter' so they can never be mixed with — or mis-joined to — the old
-- fiscal-keyed rows during the transition. Every reader asks for 'calendar_quarter'
-- only. This migration widens the period_type CHECK to allow it; 'quarterly' stays
-- allowed so the legacy rows remain valid until 185 deletes them, and so a code
-- rollback can still write.
--
-- ORDER: apply this BEFORE deploying the code that writes 'calendar_quarter'. If the
-- code runs first, the recompute logs one ERROR naming this file, writes every annual
-- row, and skips the calendar-quarter rows (its summary says
-- calendar_quarter_blocked=true); apply this and re-run it. Then recompute
-- (documents/OWNER_TASKS.md), then apply 185.
--
-- Idempotent: DROP IF EXISTS then re-ADD, inside one transaction so the table is never
-- left without the CHECK. Every existing row ('annual' / 'quarterly' / 'ttm') satisfies
-- the widened constraint, so the re-ADD validates without touching data.
--
-- Locking: DROP + ADD CONSTRAINT take ACCESS EXCLUSIVE, held to COMMIT. The scan itself is
-- sub-second, but a PENDING exclusive lock queues every reader behind it (Growth, Profit
-- Power, reports, moat, index P/E), so give up after 3 s rather than stall them behind a
-- long-running statement — re-run it when it fails (same precedent as 129).
--
-- VERIFY (one row, after applying):
--   SELECT pg_get_constraintdef(oid) AS def, convalidated
--     FROM pg_constraint
--    WHERE conrelid = 'public.sector_benchmarks'::regclass
--      AND conname  = 'sector_benchmarks_period_type_check';
--   → def lists 'annual', 'quarterly', 'ttm' AND 'calendar_quarter'; convalidated = true.

BEGIN;

SET LOCAL lock_timeout = '3s';

ALTER TABLE public.sector_benchmarks
    DROP CONSTRAINT IF EXISTS sector_benchmarks_period_type_check;

ALTER TABLE public.sector_benchmarks
    ADD CONSTRAINT sector_benchmarks_period_type_check
    CHECK (period_type = ANY (ARRAY[
        'annual'::text, 'quarterly'::text, 'ttm'::text, 'calendar_quarter'::text
    ]));

COMMIT;
