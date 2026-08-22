-- 153_security_advisor_hardening_ii.sql
--
-- Why: Supabase Security Advisor reports 12 warnings (0 errors). This closes the
-- SQL-fixable subset, and two issues the advisor does NOT report.
--
-- This is 049_security_advisor_hardening.sql's second pass. 049 locked four
-- SECURITY DEFINER functions and explicitly deferred `vector` and the leaked-password
-- toggle. Everything below is either (a) drift that accumulated after 049 because
-- nothing enforced its convention, or (b) something 049 did not look at.
--
--   A. Three SECURITY DEFINER functions still carry the DEFAULT ACL, i.e. an implicit
--      EXECUTE to PUBLIC (6 advisor warnings — 3 functions x anon/authenticated).
--      They were created or replaced AFTER 049 and nobody re-ran the convention.
--      29 of the other 32 are correctly locked, which is why this went unnoticed.
--
--   B. Four PUBLIC buckets are LISTABLE (4 advisor warnings).
--
--   C. anon/authenticated hold CREATE on schema public.  ** NOT IN THE ADVISOR **
--      This is the primitive that makes `extension_in_public` (the vector warning)
--      dangerous in the first place. See the note below.
--
--   D. The four RAG search RPCs are exposed to PUBLIC, plus three trigger helpers.
--      ** NOT IN THE ADVISOR **
--
--   E. anon/authenticated hold TRUNCATE on storage.objects, and RLS does NOT gate
--      TRUNCATE.  ** NOT IN THE ADVISOR **
--
-- DELIBERATELY NOT FIXED: extension_in_public (vector). Moving it means rewriting the
-- four search_*_chunks signatures that name `public.vector`, re-pinning search_path on
-- 14 functions to include `extensions`, and trusting three HNSW opclasses to re-resolve.
-- 049 deferred it for the same reason. Section C removes the actual attack instead: with
-- no CREATE on public, a hostile role cannot plant a shadowing function, and every app
-- SECURITY DEFINER function already pins `search_path = public, pg_temp` (049 §A).
-- Expect that ONE warning to remain in the dashboard. That is intended.
--
-- Idempotent throughout: REVOKE/GRANT are declarative, DROP POLICY is IF EXISTS.

BEGIN;

-- =============================================================================
-- A. Lock the three SECURITY DEFINER functions
--
-- Convention is the repo's existing one (37 uses): REVOKE ALL FROM PUBLIC, then
-- GRANT EXECUTE to service_role for anything the backend actually calls.
-- =============================================================================

-- Returns void, so PostgREST DOES expose it as an RPC — this is the one of the three
-- that is genuinely callable today. It DELETEs from news_articles, so any holder of the
-- shipped anon key could purge the news cache on demand and force FMP re-fetches.
-- Nothing in the backend calls it yet and pg_cron is not installed, but it is granted to
-- service_role so a future sweeper needs no further migration.
REVOKE ALL ON FUNCTION public.cleanup_expired_news_articles() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.cleanup_expired_news_articles() TO service_role;

-- The next two return `trigger`. PostgREST refuses to expose trigger-returning functions
-- and Postgres itself rejects a direct call ("trigger functions can only be called as
-- triggers"), so these are defence-in-depth rather than a live hole. They get the REVOKE
-- and NO grant, exactly like handle_new_auth_user (049 §B): a trigger executes as part of
-- the table operation and never needs an EXECUTE grant to anyone.
REVOKE ALL ON FUNCTION public.create_user_credits() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.touch_whale_snapshot_processed_at() FROM PUBLIC;


-- =============================================================================
-- B. Stop the four PUBLIC buckets being enumerable
--
-- ⚠️ READ THIS BEFORE "RESTORING" ANY POLICY BELOW.
--
-- These buckets stay public = true. Dropping the SELECT policy does NOT stop the
-- images loading, because a public bucket is served from
--   /storage/v1/object/public/<bucket>/<path>
-- which bypasses RLS entirely. Verified against production: that URL returns HTTP 200
-- with NO api key at all, which is also exactly how the iOS app loads them (see
-- frontend/ios/ios/Models/BookCoverContent.swift).
--
-- What the SELECT policy actually enables is the LIST api,
--   POST /storage/v1/object/list/<bucket>
-- i.e. anyone holding the shipped anon key can enumerate every object in these buckets.
-- That is the "Public Bucket Allows Listing" warning.
--
-- The five backend callers of .list() (seed_journey, seed_book_audio, seed_money_moves)
-- all run on the SERVICE ROLE, which keeps its *_service_write FOR ALL policy below and
-- is unaffected. No client lists these buckets; every storage read in the repo goes
-- through /object/public/ or /object/sign/.
-- =============================================================================

DROP POLICY IF EXISTS "book_covers_public_read"        ON storage.objects;
DROP POLICY IF EXISTS "home_theme_media_public_read"   ON storage.objects;
DROP POLICY IF EXISTS "journey_images_public_read"     ON storage.objects;
DROP POLICY IF EXISTS "money_moves_images_public_read" ON storage.objects;


-- =============================================================================
-- C. Revoke CREATE on schema public from the client-reachable roles
--
-- NOT an advisor warning, and the most valuable line in this file.
--
-- `anon` and `authenticated` held CREATE on schema public (anon=UC/postgres,
-- authenticated=UC/postgres — granted explicitly, not inherited from PUBLIC). That is
-- the Postgres <=14 default that Supabase's newer projects revoke.
--
-- It is also precisely what makes an extension in `public` a real risk rather than a
-- lint: the danger of extension_in_public is that a role able to CREATE in public can
-- plant a function or operator that SHADOWS the extension's, and have it resolve first
-- inside somebody else's session. Remove CREATE and that attack has no first step.
--
-- service_role KEEPS its CREATE deliberately. It is already a full-trust key — if it
-- leaks, schema CREATE is the least of the consequences — and narrowing it risks
-- breaking a Supabase internal for no measurable gain.
-- =============================================================================

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM anon;
REVOKE CREATE ON SCHEMA public FROM authenticated;


-- =============================================================================
-- D. Take the remaining app functions off the public RPC surface
--
-- NOT advisor warnings. All SECURITY INVOKER, so none can escalate — they run with the
-- caller's own privileges. Revoking is free and removes surface.
--
-- The search_*_chunks RPCs matter most: they return chunks of LICENSED book and article
-- text. They are safe today only because the chunk tables grant SELECT to service_role
-- alone (086_grant_rag_chunk_reads), so an anon caller hits a permission error on the
-- table rather than getting content. That is one grant away from being a content leak,
-- and the function should never have been callable by anon regardless.
--
-- Signatures copied verbatim from 049 §A, which is the only other place they appear.
-- =============================================================================

REVOKE ALL ON FUNCTION public.search_all_chunks(public.vector, double precision, integer)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_all_chunks(public.vector, double precision, integer)
    TO service_role;

REVOKE ALL ON FUNCTION public.search_article_chunks(public.vector, double precision, integer)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_article_chunks(public.vector, double precision, integer)
    TO service_role;

REVOKE ALL ON FUNCTION public.search_book_chunks(public.vector, double precision, integer, uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_book_chunks(public.vector, double precision, integer, uuid)
    TO service_role;

REVOKE ALL ON FUNCTION public.search_filing_chunks(public.vector, double precision, integer, text, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_filing_chunks(public.vector, double precision, integer, text, text)
    TO service_role;

-- Three trigger helpers still on the default ACL. Same treatment as §A's trigger pair:
-- revoke, no grant. Revoking EXECUTE does not affect trigger firing — a trigger runs as
-- part of the table operation and does not check the caller's EXECUTE privilege.
REVOKE ALL ON FUNCTION public.increment_chat_message_count()  FROM PUBLIC;
REVOKE ALL ON FUNCTION public.update_updated_at_column()      FROM PUBLIC;
REVOKE ALL ON FUNCTION public.update_whale_followers_count()  FROM PUBLIC;


-- =============================================================================
-- E. TRUNCATE on storage.objects — NOT FIXABLE BY US. Documented, not attempted.
--
-- The finding is real: `anon` and `authenticated` hold TRUNCATE on storage.objects, and
-- RLS does NOT apply to TRUNCATE (it is all-or-nothing at the table level), so none of
-- the careful per-bucket policies above would stop it.
--
-- ⚠️ THIS FILE ORIGINALLY TRIED TO REVOKE IT, AND THAT WAS A SILENT NO-OP.
-- Measured after applying:  anon still had  DELETE,INSERT,...,TRUNCATE,UPDATE.
--
-- Why. The grants were issued BY `supabase_storage_admin`, which owns storage.objects:
--     anon=arwdDxtm/supabase_storage_admin
-- A REVOKE only removes grants made by the role executing it. Migrations run as
-- `postgres`, which is NOT a superuser here (rolsuper = false) and is NOT a member of
-- `supabase_storage_admin` — so the statement matched nothing. Postgres does not raise
-- on a REVOKE that has nothing to revoke, so it "succeeded" and changed nothing.
-- The only role that could do it is `supabase_admin` (the sole superuser), which Supabase
-- reserves and does not expose. The Studio SQL editor also runs as `postgres`.
--
-- Accepted, with the reasoning written down rather than left implicit:
--   * NOT REACHABLE. anon/authenticated reach Postgres only via PostgREST and the Storage
--     API. Neither exposes a DDL surface, and the Storage service never issues TRUNCATE.
--     Exploiting it needs direct SQL, which needs the database password — and anyone
--     holding that does not need this privilege.
--   * NOT DURABLE EVEN IF DONE. Supabase manages this schema and re-applies its grants on
--     platform upgrades, so a revoke would silently come back.
--
-- If Supabase ever exposes a supported way to manage storage-schema grants, revisit.
-- Do NOT re-add a bare REVOKE here: it will pass review, apply cleanly, do nothing, and
-- leave the next reader believing this is closed.
-- =============================================================================

COMMIT;
