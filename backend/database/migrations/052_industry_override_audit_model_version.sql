-- 052_industry_override_audit_model_version.sql
--
-- Why: the Phase B override service now captures which Gemini model
-- produced each audit row, so a future model upgrade (e.g. 1.5-pro →
-- 2.0-pro) is traceable without parsing the raw_response JSONB. The
-- column is nullable so existing audit rows (written before this
-- migration) keep validating.
--
-- Also: the application code no longer emits the following audit
-- statuses, but we KEEP them in the CHECK constraint so historical
-- rows still validate:
--
--   - 'applied_with_warning'    — was used for 3x-10x TAM divergence
--                                 vs Phase A; replaced with info-only
--                                 logging (trust mode, operator reviews
--                                 audit log).
--   - 'rejected_low_confidence' — was used when Gemini self-reported
--                                 confidence="low"; gate removed.
--   - 'rejected_sanity'         — was used for >10x TAM divergence vs
--                                 Phase A; gate removed.
--
-- New writes use only: 'applied', 'rejected_validation', 'gemini_error',
-- 'skipped_kill_switch'. The deprecated values stay legal for backwards
-- compatibility with rows already in the table.
--
-- Idempotent — ADD COLUMN IF NOT EXISTS.

BEGIN;

ALTER TABLE public.industry_override_audit
    ADD COLUMN IF NOT EXISTS model_version TEXT;

COMMENT ON COLUMN public.industry_override_audit.model_version IS
    'Gemini model identifier captured from the API response (e.g. gemini-1.5-pro). '
    'Null for rows written before migration 052 and for skipped_kill_switch rows.';

COMMIT;
