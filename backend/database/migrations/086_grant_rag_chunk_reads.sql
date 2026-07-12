-- 086_grant_rag_chunk_reads.sql
--
-- Why: chat RAG grounding is silently disabled. The retrieval RPCs
--   search_filing_chunks() and search_all_chunks() (called from
--   backend/app/services/chat_service.py) fail at runtime with
--   Postgres 42501 "permission denied for table company_filing_chunks"
--   (and book_chunks) whenever invoked through the service-role Supabase
--   client. chat_service catches the exception and returns [], so chat
--   answers WITHOUT filing/book RAG context — degraded, not crashing.
--
-- Root cause (verified live against the DB):
--   - All four search_*_chunks functions are SECURITY *INVOKER* (the
--     default — none declares SECURITY DEFINER), owned by `postgres`.
--     Being INVOKER, they execute with the *caller's* privileges. The
--     backend calls them through the Supabase client as `service_role`.
--   - company_filing_chunks / book_chunks / article_chunks / books grant
--     table privileges to `postgres` ONLY. service_role (and anon /
--     authenticated) have NO SELECT:
--       has_table_privilege('service_role','public.company_filing_chunks','SELECT') = false
--   - The tables DO have RLS enabled with permissive `*_select_all`
--     (USING true) + `*_service_all` policies, but Postgres runs the
--     table-level privilege check BEFORE RLS. With no GRANT, the query
--     is rejected with 42501 before any policy is evaluated — so those
--     RLS policies were effectively dead. (service_role additionally has
--     rolbypassrls = true, so once SELECT is granted RLS is a non-issue
--     for it.)
--   Plain `.table("chat_sessions").select(...)` works with the same
--   client because chat_sessions was granted to service_role normally;
--   these RAG tables were created outside that flow and never granted.
--
-- Fix: grant SELECT on the four RAG source tables to service_role. This
--   is the minimal privilege the SECURITY INVOKER search functions need
--   (they only read). search_all_chunks joins `books`, and
--   search_book_chunks joins `books`, so `books` is included too.
--
-- Scope decision — service_role ONLY (deliberately NOT anon /
--   authenticated): the app reaches these tables solely through the
--   backend's service-role client (iOS -> backend -> chat_service). The
--   RAG chunk tables hold raw source text; granting the public API roles
--   direct SELECT would newly expose that over PostgREST, contrary to the
--   hardening direction of migration 049. If a future ingestion job writes
--   chunks via the service-role client (today they are written by a
--   postgres-owned process; the tables are currently empty), extend this
--   with the needed INSERT/UPDATE grant then.
--
-- Idempotent: GRANT is a replayable no-op if the privilege already exists.

BEGIN;

GRANT SELECT ON public.company_filing_chunks TO service_role;
GRANT SELECT ON public.book_chunks           TO service_role;
GRANT SELECT ON public.article_chunks        TO service_role;
GRANT SELECT ON public.books                 TO service_role;

COMMIT;
