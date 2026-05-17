-- 045_report_status_add_deleted.sql
--
-- The research.py endpoint uses 'deleted' as a soft-delete sentinel:
--   * GET /reports filters .neq("status", "deleted")
--   * DELETE /reports/{id} sets status = "deleted"
-- But the report_status enum was defined as
--   ('pending', 'processing', 'completed', 'failed')
-- so any reference to 'deleted' throws 22P02 invalid_text_representation
-- → /reports returns 500 → iOS falls back to mock data.
--
-- Add 'deleted' to the enum. ALTER TYPE ADD VALUE is non-transactional
-- and idempotent via IF NOT EXISTS.

ALTER TYPE public.report_status ADD VALUE IF NOT EXISTS 'deleted';
