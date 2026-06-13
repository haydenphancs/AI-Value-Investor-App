-- 062_lessons_grants.sql
--
-- Why: public.lessons (the Investor Journey content table) was created outside the
-- standard migration flow and never received table-level GRANTs. It has RLS policies
-- (lessons_select_all public read, lessons_service_all service write) but RLS only
-- governs WHICH ROWS a role may touch — the role still needs a table-level GRANT to
-- touch the table at all. Without it, even the service_role key gets
-- "permission denied for table lessons" (SQLSTATE 42501), which blocks both the
-- /api/v1/learn/journey read endpoint and the seed_journey.py writer.
--
-- This migration grants the same privileges the cache-table template uses:
--   - anon + authenticated: SELECT (public lesson content)
--   - service_role: ALL (the seeder / backend writer)
-- id is a uuid with a gen_random_uuid() default, so there is no sequence to grant.
--
-- Idempotent: GRANT is safe to run repeatedly.

GRANT SELECT ON public.lessons TO anon, authenticated;
GRANT ALL    ON public.lessons TO service_role;

-- The two user-scoped journey tables have the same latent gap. Granting them now so
-- progress + schedule sync work when they're wired up (RLS still restricts rows to the owner).
GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_lesson_progress TO authenticated;
GRANT ALL ON public.user_lesson_progress TO service_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_study_schedules TO authenticated;
GRANT ALL ON public.user_study_schedules TO service_role;
