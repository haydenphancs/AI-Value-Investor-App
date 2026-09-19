-- 171_drop_dead_ledger_door.sql
--
-- Why: migration 166 added the `credit_transactions_split_sums` CHECK with a (0,0) escape
-- for rows that record no split, and justified keeping that escape open on the grounds
-- that `add_credit_transaction` (migration 100, called from
-- `CreditService.log_transaction`) "still inserts without the split columns today, so it
-- keeps minting (0, 0) rows". That was wrong on the code side: `log_transaction` has had
-- ZERO callers since the migration-101 combined RPCs (`spend_credits` / `refund_credits`)
-- took over — every live ledger write goes through those, and both record the split. So
-- the RPC is a second, unused door into the money ledger that writes rows the CHECK
-- cannot judge and that `refund_credits` can only handle through its unknown-split
-- fallback. A door nobody uses and nothing tests is exactly the kind that gets used
-- wrongly later (a "quick" admin script, a future helper).
--
-- This migration closes the door and corrects the constraint's COMMENT, which is what a
-- reviewer reads in the atlas. It does NOT tighten the CHECK itself: the (0,0) escape is
-- still needed for the genuine pre-117 rows, and replacing it with an id/timestamp cutover
-- needs a live `max(id)` lookup at apply time — a follow-up once the header count in 166
-- has been run and VALIDATE applied. `refund_credits`' unknown-split ELSIF branch stays
-- for the same rows.
--
-- The Python side ships in the same change: `CreditService.log_transaction` is deleted
-- and `tests/test_ledger_writers_record_the_split.py` fails the build if any code calls
-- the RPC again or a migration inserts into the ledger without both split columns.
--
-- DESTRUCTIVE: drops function public.add_credit_transaction(uuid, integer, text, text,
-- integer). No data is lost (a function, not a table); the only caller was
-- CreditService.log_transaction, which itself had no callers (verified by grep over
-- app/, scripts/ and tests/ on 2026-09-18). Safe to re-run: IF EXISTS.
DROP FUNCTION IF EXISTS public.add_credit_transaction(UUID, INTEGER, TEXT, TEXT, INTEGER);

COMMENT ON CONSTRAINT credit_transactions_split_sums ON public.credit_transactions IS
    'delta must equal granted_delta + purchased_delta, except the pre-117 rows that recorded '
    'no split (both zero). refund_credits reverses the RECORDED split, so a row that '
    'violates this would refund the wrong pool. Added NOT VALID in 166; VALIDATE once the '
    'count in that migration''s header returns 0. The (0,0) escape is HISTORICAL ONLY: no '
    'live writer produces it — add_credit_transaction, the last one that could, was dropped '
    'in 171 (it had no callers). Tighten to an id cutover once VALIDATE has run.';

-- VERIFY (run after applying):
--   SELECT proname FROM pg_proc WHERE proname = 'add_credit_transaction';   -- expect 0 rows
--   SELECT obj_description(oid, 'pg_constraint') FROM pg_constraint
--    WHERE conname = 'credit_transactions_split_sums';                      -- mentions 171
