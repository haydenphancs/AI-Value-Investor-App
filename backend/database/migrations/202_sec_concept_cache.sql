-- 202_sec_concept_cache.sql
--
-- Why: the Signal of Confidence chart draws FMP's quarterly `commonStockRepurchased` as
-- "Buybacks", and that figure is not always a buyback. A survey of 15 issuers x 8 quarters
-- against SEC XBRL (2026-10-22) found 11 of 120 rows that were something else: INTC's
-- $181M and $617M are restricted-stock-unit tax withholding (Intel's 10-Qs say "No shares
-- were repurchased"), META / AMD / NVDA rows add withholding to real buybacks, and JPM's
-- add preferred-stock redemptions. `app/services/_buyback_verification.py` decides each
-- quarter from the company's OWN filing, which needs up to five us-gaap concepts per
-- company from SEC's XBRL `companyconcept` API (`app/integrations/sec_edgar.py`).
--
-- This table is the Tier-2 (24 h) half of the cache-aside in
-- `app/services/sec_concept_service.py` (Tier 1 is a 5-minute in-memory dict). It is the
-- only writer and reader. One row per (company, concept):
--   * found = false  → SEC answered 404: the company never used the concept. A real
--                      answer, cached like a fact (facts is NULL), never read as zero.
--   * found = true   → `facts` holds the payload TRIMMED to {"units": {"USD": [...]}} with
--                      only the 10-Q / 10-K rows (and their amendments / transition
--                      forms) and only their start, end, val, filed, form keys — the shape
--                      `_buyback_verification.facts_from_concept` parses.
--   * newest_end     → the latest period end among the stored facts. The service refetches
--                      a row whose newest_end trails the quarter FMP is charting by more
--                      than a week (a 10-Q filed since the row was written), at most hourly.
-- When SEC fails, a row up to 30 days old is served as stale rather than leaving the chart
-- unverified.
--
-- ⚠️ RLS is service-role ONLY. SEC data is public domain, but every cache table has been
-- service-role-only since 164: the iOS app has no Supabase client, every reader is the
-- backend's service_role, and an anon/authenticated grant is a second door nobody needs
-- (database.md). Same posture as 162.
--
-- Primary key (cik, concept) and no serial column, so there is no sequence to grant.
--
-- Deploy order does not matter: the service try/excepts both the read and the write, so
-- before this migration is applied every request simply fetches from SEC (and logs
-- [sec-concept-cache-write-failed]).

CREATE TABLE IF NOT EXISTS public.sec_concept_cache (
    cik         TEXT        NOT NULL,
    concept     TEXT        NOT NULL,
    found       BOOLEAN     NOT NULL,
    facts       JSONB,
    newest_end  DATE,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cik, concept)
);

COMMENT ON TABLE public.sec_concept_cache IS
    'Tier-2 (24 h) cache of SEC EDGAR XBRL companyconcept facts (us-gaap cash-flow concepts) '
    'used to verify the Signal of Confidence buyback figure: FMP''s commonStockRepurchased is '
    'sometimes employee tax withholding or a preferred redemption, and the company''s own '
    'filing says which. One row per (CIK, concept); found=false caches SEC''s 404 (the company '
    'never used the concept). Public-domain SEC data, service_role only like every cache since '
    '164. Written and read only by app/services/sec_concept_service.py.';

COMMENT ON COLUMN public.sec_concept_cache.cik IS
    'SEC Central Index Key, 10 digits zero-padded (e.g. 0000050863 for Intel).';
COMMENT ON COLUMN public.sec_concept_cache.concept IS
    'us-gaap element name, e.g. PaymentsForRepurchaseOfCommonStock or '
    'PaymentsRelatedToTaxWithholdingForShareBasedCompensation.';
COMMENT ON COLUMN public.sec_concept_cache.found IS
    'false = SEC answered 404: the company never reported this concept. An answer, not a '
    'zero; facts is NULL.';
COMMENT ON COLUMN public.sec_concept_cache.facts IS
    'The companyconcept payload trimmed to {"units": {"USD": [rows]}}: 10-Q / 10-K rows and '
    'their amendments only, keys start, end, val, filed, form. NULL when found is false.';
COMMENT ON COLUMN public.sec_concept_cache.newest_end IS
    'Latest period end among the stored facts. A row trailing the charted quarter by more '
    'than 7 days is refetched (at most hourly), so a newly filed 10-Q is picked up.';
COMMENT ON COLUMN public.sec_concept_cache.fetched_at IS
    'When SEC was last asked. Fresh for 24 h; served as stale up to 30 days while SEC fails.';

ALTER TABLE public.sec_concept_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sec_concept_cache_service_all" ON public.sec_concept_cache;
CREATE POLICY "sec_concept_cache_service_all" ON public.sec_concept_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.sec_concept_cache FROM anon, authenticated;
GRANT ALL ON public.sec_concept_cache TO service_role;
