-- 164_market_caches_service_role_only.sql
--
-- Why: FMP-DERIVED MARKET DATA AND PAID AI OUTPUT ARE READABLE WITH THE SHIPPED ANON KEY.
--
-- Migration 049 (and the cache-table template it spread) gave every `*_cache` table a
-- `FOR SELECT TO anon, authenticated USING (true)` policy plus `GRANT SELECT … TO anon`.
-- That template predates two decisions that make it wrong today:
--
--   1. The licence. FMP's signed Order Form grants End-User Display Rights — data may be
--      shown only "through the Licensee's authenticated platform" — and the app went
--      account-only on 2026-09-07 for exactly that reason (`.claude/rules/auth.md` §1a).
--      An anon-readable PostgREST table of FMP-derived rows is a second, unauthenticated
--      door to the same data, reachable by anyone who extracts the anon key from the iOS
--      binary. FMP ToS 2.6.1 calls that redistribution. Migrations 149/150/151/157/158/159/
--      161/162 each say so in their headers and ship service-role-only; this migration
--      brings the 049-era tables to the same posture.
--
--   2. What is in the rows. `ticker_report_cache.ticker_report_data` is the full generated
--      multi-persona AI report — the product's 20-credit deliverable — served free to a
--      cache hit and billable on a miss (`ticker_report.py`). `market_deep_dive_cache`,
--      `ai_insight_cache`, `price_catalyst_cache`, the `*_intel_cache` tables and the
--      `*_audit` tables hold Gemini output and the grounded-search evidence behind it.
--      None of it should be a free download.
--
-- Verified safe: the iOS app has NO Supabase REST client (zero `/rest/v1/` or
-- `SupabaseClient` references under frontend/ios, and no anon key in the app at all), and
-- every backend reader goes through `get_supabase()`, the service_role client. No anon-key
-- client exists anywhere in the repo. So no reader loses access; only the door that a user
-- JWT plus the project's (public, publishable) anon key could open closes.
--
-- Every public-read policy is DROPPED, not merely made moot by the REVOKE. A permissive
-- policy left behind a REVOKE is inert only until someone GRANTs again — 150 REVOKEd
-- `index_detail_cache` and left `index_detail_cache_public_read` live; this file is the
-- first to drop it (151 dropped only the ETF twin). Each table keeps its existing
-- service_role policy (`*_service_write` / `*_service_all` / "Allow service_role full
-- access"); `sector_aggregates` had ONLY the authenticated policy, so it gets a service_role
-- one here. service_role also has BYPASSRLS, so the policy is belt and braces either way.
--
-- Two corrections to what this header first claimed, from the 2026-09-11 review:
--   * The five RAG/Learn tables (article_chunks, book_chunks, company_filing_chunks, books,
--     book_chapters) were ALREADY service-role-only at the grant level — 086 verified live
--     that anon/authenticated held no SELECT there. Their `*_select_all` policies were dead;
--     dropping them is hygiene, not a closed door.
--   * `whale_follows` is in the sweep because of a TRIGGER, not a grant: its AFTER INSERT /
--     DELETE triggers run `update_whale_followers_count()`, which is SECURITY INVOKER and
--     UPDATEs `whales`. Once `whales` is service-role-only, a client-side follow through
--     PostgREST (allowed by `whale_follows_insert_own`) would fail inside the trigger with
--     42501. The app writes whale_follows only via service_role (whale_service.py), so
--     closing the client path removes the coupling for free.
--
-- Deliberately NOT touched — catalogue/editorial tables with no FMP data and no AI output:
-- `credit_packs`, `plan_credits` (the storefront catalogues, `.public` by design — §9.1),
-- `trending_themes`, `lessons`, `money_move_articles`, `agent_personas`. Those can move to
-- the same posture later; nothing in this migration depends on them.
--
-- `vector_search_stats` (a hand-created view with no migration) is recreated as
-- `security_invoker` and locked to service_role: as a plain view it read the three
-- service-role-only chunk tables with its OWNER's privileges, bypassing both their grants
-- and their RLS.
--
-- Apply BEFORE 168 (which drops four of the retired tables named in the guarded block at
-- the end). Idempotent: REVOKE/GRANT are declarative, every DROP POLICY is IF EXISTS, and
-- the retired tables are guarded with to_regclass so re-running after 168 is a no-op.
--
-- VERIFY AFTER APPLYING:
--
--     SELECT table_name, grantee, string_agg(privilege_type, ',')
--       FROM information_schema.role_table_grants
--      WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
--        AND (table_name LIKE '%_cache' OR table_name LIKE 'whale%'
--             OR table_name IN ('sector_benchmarks','sector_aggregates','industry_dossier'))
--     -- and, for the trigger coupling:
--     -- SELECT has_table_privilege('authenticated','public.whale_follows','INSERT') AS can_follow,
--     --        has_table_privilege('authenticated','public.whales','UPDATE')       AS can_bump;
--      GROUP BY 1, 2;
--     -- expect: no rows.

-- ---- Live tables: FMP-derived caches, AI output, audit trails, RAG corpus, smart money ----

DROP POLICY IF EXISTS "ai_insight_cache_public_read" ON public.ai_insight_cache;
REVOKE ALL ON public.ai_insight_cache FROM anon, authenticated;
GRANT  ALL ON public.ai_insight_cache TO service_role;

DROP POLICY IF EXISTS "company_profile_cache_public_read" ON public.company_profile_cache;
REVOKE ALL ON public.company_profile_cache FROM anon, authenticated;
GRANT  ALL ON public.company_profile_cache TO service_role;

DROP POLICY IF EXISTS "competitor_intel_audit_public_read" ON public.competitor_intel_audit;
REVOKE ALL ON public.competitor_intel_audit FROM anon, authenticated;
GRANT  ALL ON public.competitor_intel_audit TO service_role;

DROP POLICY IF EXISTS "competitor_intel_cache_public_read" ON public.competitor_intel_cache;
REVOKE ALL ON public.competitor_intel_cache FROM anon, authenticated;
GRANT  ALL ON public.competitor_intel_cache TO service_role;

DROP POLICY IF EXISTS "crypto_coin_id_cache_public_read" ON public.crypto_coin_id_cache;
REVOKE ALL ON public.crypto_coin_id_cache FROM anon, authenticated;
GRANT  ALL ON public.crypto_coin_id_cache TO service_role;

DROP POLICY IF EXISTS "crypto_fundamentals_cache_public_read" ON public.crypto_fundamentals_cache;
REVOKE ALL ON public.crypto_fundamentals_cache FROM anon, authenticated;
GRANT  ALL ON public.crypto_fundamentals_cache TO service_role;

DROP POLICY IF EXISTS "crypto_snapshots_public_read" ON public.crypto_snapshots;
REVOKE ALL ON public.crypto_snapshots FROM anon, authenticated;
GRANT  ALL ON public.crypto_snapshots TO service_role;

DROP POLICY IF EXISTS "geopolitical_macro_cache_public_read" ON public.geopolitical_macro_cache;
REVOKE ALL ON public.geopolitical_macro_cache FROM anon, authenticated;
GRANT  ALL ON public.geopolitical_macro_cache TO service_role;

DROP POLICY IF EXISTS "index_macro_forecast_cache_public_read" ON public.index_macro_forecast_cache;
REVOKE ALL ON public.index_macro_forecast_cache FROM anon, authenticated;
GRANT  ALL ON public.index_macro_forecast_cache TO service_role;

DROP POLICY IF EXISTS "industry_dossier_public_read" ON public.industry_dossier;
REVOKE ALL ON public.industry_dossier FROM anon, authenticated;
GRANT  ALL ON public.industry_dossier TO service_role;

DROP POLICY IF EXISTS "industry_moat_benchmarks_public_read" ON public.industry_moat_benchmarks;
REVOKE ALL ON public.industry_moat_benchmarks FROM anon, authenticated;
GRANT  ALL ON public.industry_moat_benchmarks TO service_role;

DROP POLICY IF EXISTS "industry_override_audit_public_read" ON public.industry_override_audit;
REVOKE ALL ON public.industry_override_audit FROM anon, authenticated;
GRANT  ALL ON public.industry_override_audit TO service_role;

DROP POLICY IF EXISTS "ip_intel_audit_public_read" ON public.ip_intel_audit;
REVOKE ALL ON public.ip_intel_audit FROM anon, authenticated;
GRANT  ALL ON public.ip_intel_audit TO service_role;

DROP POLICY IF EXISTS "ip_intel_cache_public_read" ON public.ip_intel_cache;
REVOKE ALL ON public.ip_intel_cache FROM anon, authenticated;
GRANT  ALL ON public.ip_intel_cache TO service_role;

DROP POLICY IF EXISTS "market_deep_dive_cache_public_read" ON public.market_deep_dive_cache;
REVOKE ALL ON public.market_deep_dive_cache FROM anon, authenticated;
GRANT  ALL ON public.market_deep_dive_cache TO service_role;

DROP POLICY IF EXISTS "moat_intel_audit_public_read" ON public.moat_intel_audit;
REVOKE ALL ON public.moat_intel_audit FROM anon, authenticated;
GRANT  ALL ON public.moat_intel_audit TO service_role;

DROP POLICY IF EXISTS "moat_intel_cache_public_read" ON public.moat_intel_cache;
REVOKE ALL ON public.moat_intel_cache FROM anon, authenticated;
GRANT  ALL ON public.moat_intel_cache TO service_role;

DROP POLICY IF EXISTS "price_catalyst_cache_public_read" ON public.price_catalyst_cache;
REVOKE ALL ON public.price_catalyst_cache FROM anon, authenticated;
GRANT  ALL ON public.price_catalyst_cache TO service_role;

DROP POLICY IF EXISTS "short_interest_cache_public_read" ON public.short_interest_cache;
REVOKE ALL ON public.short_interest_cache FROM anon, authenticated;
GRANT  ALL ON public.short_interest_cache TO service_role;

DROP POLICY IF EXISTS "signals_cache_public_read" ON public.signals_cache;
REVOKE ALL ON public.signals_cache FROM anon, authenticated;
GRANT  ALL ON public.signals_cache TO service_role;

DROP POLICY IF EXISTS "snapshot_cache_public_read" ON public.snapshot_cache;
REVOKE ALL ON public.snapshot_cache FROM anon, authenticated;
GRANT  ALL ON public.snapshot_cache TO service_role;

DROP POLICY IF EXISTS "stock_fundamentals_cache_public_read" ON public.stock_fundamentals_cache;
REVOKE ALL ON public.stock_fundamentals_cache FROM anon, authenticated;
GRANT  ALL ON public.stock_fundamentals_cache TO service_role;

DROP POLICY IF EXISTS "ticker_report_cache_public_read" ON public.ticker_report_cache;
REVOKE ALL ON public.ticker_report_cache FROM anon, authenticated;
GRANT  ALL ON public.ticker_report_cache TO service_role;

DROP POLICY IF EXISTS "ticker_volatility_cache_public_read" ON public.ticker_volatility_cache;
REVOKE ALL ON public.ticker_volatility_cache FROM anon, authenticated;
GRANT  ALL ON public.ticker_volatility_cache TO service_role;

DROP POLICY IF EXISTS "Allow public read access" ON public.sector_benchmarks;
REVOKE ALL ON public.sector_benchmarks FROM anon, authenticated;
GRANT  ALL ON public.sector_benchmarks TO service_role;

DROP POLICY IF EXISTS "sector_aggregates_read_authenticated" ON public.sector_aggregates;
REVOKE ALL ON public.sector_aggregates FROM anon, authenticated;
GRANT  ALL ON public.sector_aggregates TO service_role;
-- That was its ONLY policy; give it the service_role one every other table here has.
DROP POLICY IF EXISTS "sector_aggregates_service_all" ON public.sector_aggregates;
CREATE POLICY "sector_aggregates_service_all" ON public.sector_aggregates
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "article_chunks_select_all" ON public.article_chunks;
REVOKE ALL ON public.article_chunks FROM anon, authenticated;
GRANT  ALL ON public.article_chunks TO service_role;

DROP POLICY IF EXISTS "book_chunks_select_all" ON public.book_chunks;
REVOKE ALL ON public.book_chunks FROM anon, authenticated;
GRANT  ALL ON public.book_chunks TO service_role;

DROP POLICY IF EXISTS "filing_chunks_select_all" ON public.company_filing_chunks;
REVOKE ALL ON public.company_filing_chunks FROM anon, authenticated;
GRANT  ALL ON public.company_filing_chunks TO service_role;

DROP POLICY IF EXISTS "books_select_all" ON public.books;
REVOKE ALL ON public.books FROM anon, authenticated;
GRANT  ALL ON public.books TO service_role;

DROP POLICY IF EXISTS "book_chapters_select_all" ON public.book_chapters;
REVOKE ALL ON public.book_chapters FROM anon, authenticated;
GRANT  ALL ON public.book_chapters TO service_role;

DROP POLICY IF EXISTS "whales_select_all" ON public.whales;
REVOKE ALL ON public.whales FROM anon, authenticated;
GRANT  ALL ON public.whales TO service_role;

DROP POLICY IF EXISTS "whale_trades_select_all" ON public.whale_trades;
REVOKE ALL ON public.whale_trades FROM anon, authenticated;
GRANT  ALL ON public.whale_trades TO service_role;

DROP POLICY IF EXISTS "whale_trade_groups_select_all" ON public.whale_trade_groups;
REVOKE ALL ON public.whale_trade_groups FROM anon, authenticated;
GRANT  ALL ON public.whale_trade_groups TO service_role;

DROP POLICY IF EXISTS "whale_holdings_select_all" ON public.whale_holdings;
REVOKE ALL ON public.whale_holdings FROM anon, authenticated;
GRANT  ALL ON public.whale_holdings TO service_role;

DROP POLICY IF EXISTS "whale_sectors_select_all" ON public.whale_sector_allocations;
REVOKE ALL ON public.whale_sector_allocations FROM anon, authenticated;
GRANT  ALL ON public.whale_sector_allocations TO service_role;

DROP POLICY IF EXISTS "whale_profile_cache_select_all" ON public.whale_profile_cache;
REVOKE ALL ON public.whale_profile_cache FROM anon, authenticated;
GRANT  ALL ON public.whale_profile_cache TO service_role;

DROP POLICY IF EXISTS "whale_filing_snapshots_select_all" ON public.whale_filing_snapshots;
REVOKE ALL ON public.whale_filing_snapshots FROM anon, authenticated;
GRANT  ALL ON public.whale_filing_snapshots TO service_role;

DROP POLICY IF EXISTS "whale_alerts_select_all" ON public.whale_alerts;
REVOKE ALL ON public.whale_alerts FROM anon, authenticated;
GRANT  ALL ON public.whale_alerts TO service_role;

-- whale_follows: the trigger coupling described in the header. FK-bound to public.users;
-- written only by whale_service.py under service_role.
DROP POLICY IF EXISTS "whale_follows_select_own" ON public.whale_follows;
DROP POLICY IF EXISTS "whale_follows_insert_own" ON public.whale_follows;
DROP POLICY IF EXISTS "whale_follows_delete_own" ON public.whale_follows;
REVOKE ALL ON public.whale_follows FROM anon, authenticated;
GRANT  ALL ON public.whale_follows TO service_role;
DROP POLICY IF EXISTS "whale_follows_service_all" ON public.whale_follows;
CREATE POLICY "whale_follows_service_all" ON public.whale_follows
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ---- vector_search_stats: owner-privileged view over the chunk tables ----
CREATE OR REPLACE VIEW public.vector_search_stats WITH (security_invoker = true) AS
 SELECT 'book_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (book_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(book_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT book_chunks.book_id) AS unique_sources
   FROM public.book_chunks
UNION ALL
 SELECT 'article_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (article_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(article_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT article_chunks.article_id) AS unique_sources
   FROM public.article_chunks
UNION ALL
 SELECT 'company_filing_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (company_filing_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(company_filing_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT company_filing_chunks.ticker) AS unique_sources
   FROM public.company_filing_chunks;

REVOKE ALL ON public.vector_search_stats FROM PUBLIC, anon, authenticated;
GRANT  SELECT ON public.vector_search_stats TO service_role;

COMMENT ON VIEW public.vector_search_stats IS
    'Operator dashboard over the three RAG chunk tables. security_invoker since migration '
    '164 so it cannot read them with owner privileges; service_role only. Created by hand '
    'in the SQL editor originally — migration 164 is its first provenance.';

-- ---- Retired tables (dropped by 168): guarded so this file stays re-runnable ----
DO $$
BEGIN
    IF to_regclass('public.index_detail_cache') IS NOT NULL THEN
        EXECUTE 'DROP POLICY IF EXISTS "index_detail_cache_public_read" ON public.index_detail_cache';
        EXECUTE 'REVOKE ALL ON public.index_detail_cache FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON public.index_detail_cache TO service_role';
    END IF;
    IF to_regclass('public.etf_detail_cache') IS NOT NULL THEN
        EXECUTE 'DROP POLICY IF EXISTS "etf_detail_cache_public_read" ON public.etf_detail_cache';
        EXECUTE 'REVOKE ALL ON public.etf_detail_cache FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON public.etf_detail_cache TO service_role';
    END IF;
    IF to_regclass('public.asset_snapshots') IS NOT NULL THEN
        EXECUTE 'DROP POLICY IF EXISTS "snapshots_select_all" ON public.asset_snapshots';
        EXECUTE 'REVOKE ALL ON public.asset_snapshots FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON public.asset_snapshots TO service_role';
    END IF;
    IF to_regclass('public.news_articles') IS NOT NULL THEN
        EXECUTE 'DROP POLICY IF EXISTS "news_select_all" ON public.news_articles';
        EXECUTE 'REVOKE ALL ON public.news_articles FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON public.news_articles TO service_role';
    END IF;
END $$;
