-- 053_industry_override_audit_below_phase_a.sql
--
-- Why: Phase B (AI override) only exists to FIX Census/FRED
-- US-domestic undercounting of global TAM — it should never LOWER a
-- TAM. If Gemini comes back lower than Phase A, the search either
-- narrowed the industry definition (e.g. ad market alone vs all
-- internet content) or hallucinated low. The service now rejects
-- those with status='rejected_below_phase_a' and leaves Phase A
-- untouched in `industry_dossier`.
--
-- This migration widens the CHECK constraint on industry_override_audit
-- to accept the new status. Deprecated statuses
-- ('applied_with_warning', 'rejected_sanity', 'rejected_low_confidence')
-- stay in the allow-list so any historical audit rows still validate.
--
-- Idempotent — DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT.

BEGIN;

-- Inline CHECKs created via `column TEXT NOT NULL CHECK (...)` in
-- migration 051 are named `<table>_<column>_check` by default.
ALTER TABLE public.industry_override_audit
    DROP CONSTRAINT IF EXISTS industry_override_audit_status_check;

ALTER TABLE public.industry_override_audit
    ADD CONSTRAINT industry_override_audit_status_check
    CHECK (status IN (
        'applied',                  -- current: written to industry_dossier
        'applied_with_warning',     -- deprecated (pre-052) — kept for history
        'rejected_validation',      -- current: numeric/structural failure
        'rejected_sanity',          -- deprecated (pre-052) — kept for history
        'rejected_low_confidence',  -- deprecated (pre-052) — kept for history
        'rejected_below_phase_a',   -- NEW: Gemini TAM < Phase A; keep Phase A
        'gemini_error',             -- current: Gemini API / parse failure
        'skipped_kill_switch'       -- current: INDUSTRY_OVERRIDE_AI_ENABLED=false
    ));

COMMIT;
