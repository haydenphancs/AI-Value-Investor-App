-- 042_restore_service_role_grants.sql
--
-- The PostgREST service_role JWT is currently 403'd on
-- `agent_personas` and `research_reports` (verified: GET /rest/v1/users
-- returns 200 with the same key, GET /rest/v1/research_reports returns
-- 42501 permission denied). That breaks /research/generate end-to-end
-- because the endpoint reads agent_personas and writes research_reports.
--
-- Root cause is a missing GRANT on those two tables — likely lost in a
-- prior migration that recreated them. Other Supabase tables in this
-- project still have the standard service_role grant intact, which is
-- why this only affects research.
--
-- Restore the grants. Service_role is meant to bypass RLS and have full
-- table access for server-side workloads (FastAPI background tasks,
-- migrations, admin scripts).

GRANT ALL ON public.agent_personas TO service_role;
GRANT ALL ON public.research_reports TO service_role;

-- Also grant on ticker_report_cache since it's read/written from the
-- same code path (research_service.upsert_cached_report) and may have
-- the same problem.
GRANT ALL ON public.ticker_report_cache TO service_role;

-- Future-proof: any sequence used by these tables (e.g. autoincrement
-- IDs) needs USAGE for INSERTs to work.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
