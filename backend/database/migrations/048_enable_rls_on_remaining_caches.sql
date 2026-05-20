-- 048_enable_rls_on_remaining_caches.sql
--
-- Why: Supabase Security Advisor flagged two public.*_cache tables without
-- RLS enabled:
--   - public.company_profile_cache (PK: ticker)
--   - public.snapshot_cache       (PK: id uuid)
-- Every other *_cache table in the schema follows the canonical cache pattern
-- from .claude/rules/database.md: RLS on, anon/authenticated can SELECT (it's
-- a read-through cache), only service_role can write. These two slipped
-- through earlier migrations.
--
-- Neither table has an associated sequence (snapshot_cache uses
-- gen_random_uuid(), company_profile_cache uses ticker text as PK), so no
-- sequence GRANT is needed.
--
-- Idempotent: ALTER TABLE ... ENABLE ROW LEVEL SECURITY is a no-op if RLS is
-- already on. CREATE POLICY is wrapped in DROP POLICY IF EXISTS so re-runs
-- don't error.

BEGIN;

-- =============================================================================
-- company_profile_cache
-- =============================================================================
ALTER TABLE public.company_profile_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "company_profile_cache_public_read"   ON public.company_profile_cache;
DROP POLICY IF EXISTS "company_profile_cache_service_write" ON public.company_profile_cache;

CREATE POLICY "company_profile_cache_public_read"
    ON public.company_profile_cache
    FOR SELECT
    TO anon, authenticated
    USING (true);

CREATE POLICY "company_profile_cache_service_write"
    ON public.company_profile_cache
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

GRANT SELECT ON public.company_profile_cache TO anon, authenticated;
GRANT ALL    ON public.company_profile_cache TO service_role;


-- =============================================================================
-- snapshot_cache
-- =============================================================================
ALTER TABLE public.snapshot_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "snapshot_cache_public_read"   ON public.snapshot_cache;
DROP POLICY IF EXISTS "snapshot_cache_service_write" ON public.snapshot_cache;

CREATE POLICY "snapshot_cache_public_read"
    ON public.snapshot_cache
    FOR SELECT
    TO anon, authenticated
    USING (true);

CREATE POLICY "snapshot_cache_service_write"
    ON public.snapshot_cache
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

GRANT SELECT ON public.snapshot_cache TO anon, authenticated;
GRANT ALL    ON public.snapshot_cache TO service_role;

COMMIT;
