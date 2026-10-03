-- 188_purge_grounded_search_content.sql
--
-- Why: Gemini "Grounding with Google Search" is RETIRED (owner decision 2026-10-02). The Gemini
-- API Additional Terms (last updated 2026-04-28) let a Grounded Result be shown only to the end
-- user who sent the prompt, together with its Search Suggestions, unmodified, and forbid caching,
-- storing, analysing or collecting its Links — storage is allowed only up to 2 years for display
-- tuning, in that user's own chat history, or temporarily for a function-call refinement. Five
-- features used it as shared background research instead: app-written prompts, parsed into report
-- fields, cached 24 h to 100 days for every user, with append-only audit copies kept forever and
-- the Search Suggestions never captured. The code that called it is gone (generate_grounded_research
-- deleted; tests/test_no_google_search_grounding.py bans the tool). This purges what it stored.
--
-- DESTRUCTIVE. Rows lost, all of them Gemini grounded-research output or its audit trail — no
-- backup is needed and none should be kept (a kept copy is the storage the terms forbid):
--   * price_catalyst_cache / price_catalyst_audit      — "why it moved" answers, raw text, links, queries
--   * competitor_intel_cache / competitor_intel_audit  — research-ranked peer lists + segments
--   * moat_intel_cache / moat_intel_audit              — grounded moat-pillar scores
--   * geopolitical_macro_cache / geopolitical_macro_audit — the market-wide geopolitical factors
--   * industry_override_audit                          — the grounded TAM/CAGR runs
--   * industry_dossier rows holding a grounded GLOBAL TAM: the TAM side is reset to the same zero
--     placeholder Phase A writes (the read path then serves a live Census/FRED figure until the
--     next recompute persists one); the CONCENTRATION columns (from universe market caps, never
--     grounded) are kept
--   * ai_insight_cache.price_move (the grounded "why it moved" block) and the grounding-redirect
--     links the catalyst merged into ai_insight_cache.sources (the FMP article links stay)
--   * ticker_data_cache / ticker_report_cache rows cached before the CACHE_SCHEMA_FLOOR bump that
--     shipped with the retirement — already never served (the floor makes them misses); deleting
--     them means a never-re-requested ticker does not keep grounded content forever. The literal
--     below MUST equal CACHE_SCHEMA_FLOOR in app/services/ticker_report_cache.py.
-- The tables themselves are KEPT, empty, with a RETIRED comment: no code reads or writes them, and
-- dropping them is a separate cleanup (schema_curation.py, the grants test and the doc-parity test
-- name them).
--
-- NOT touched, by owner decision (2026-10-02, pending counsel): research_reports (users' saved report
-- history), the report PDFs already in the research-pdfs bucket, and notification_events bodies
-- (90-day prune). They still hold grounded-derived text.
--
-- APPLY ORDER — after the deploy that removes the grounded code, never before: the old code would
-- refill these tables (and re-generate reports into the deleted cache rows) on the next request.
--   1. Deploy (after the 18:00 ET close; CACHE_SCHEMA_FLOOR moved to the commit time if later).
--   2. Apply this migration (keep the floor literal below equal to the deployed one).
--   3. POST /api/v1/admin/refresh-industry-dossier — Phase A rewrites the reset industries.
--   4. POST /api/v1/admin/refresh-industry-moat-benchmarks?skip_recent_hours=0 — the Network
--      Effects pillar read the grounded lifecycle phase of those industries.
--
-- Idempotent: every DELETE / UPDATE matches nothing on a second run. No schema change.
--
-- VERIFY (run after applying — one row, every column 0):
--   SELECT
--     (SELECT count(*) FROM public.price_catalyst_cache)      AS price_catalyst_cache,
--     (SELECT count(*) FROM public.price_catalyst_audit)      AS price_catalyst_audit,
--     (SELECT count(*) FROM public.competitor_intel_cache)    AS competitor_intel_cache,
--     (SELECT count(*) FROM public.competitor_intel_audit)    AS competitor_intel_audit,
--     (SELECT count(*) FROM public.moat_intel_cache)          AS moat_intel_cache,
--     (SELECT count(*) FROM public.moat_intel_audit)          AS moat_intel_audit,
--     (SELECT count(*) FROM public.geopolitical_macro_cache)  AS geopolitical_macro_cache,
--     (SELECT count(*) FROM public.geopolitical_macro_audit)  AS geopolitical_macro_audit,
--     (SELECT count(*) FROM public.industry_override_audit)   AS industry_override_audit,
--     (SELECT count(*) FROM public.industry_dossier WHERE tam_scope = 'global')       AS global_tam_rows,
--     (SELECT count(*) FROM public.ai_insight_cache WHERE price_move IS NOT NULL)     AS price_move_blocks,
--     (SELECT count(*) FROM public.ai_insight_cache
--       WHERE sources::text LIKE '%vertexaisearch.cloud.google.com%')               AS grounding_links;

BEGIN;

-- DESTRUCTIVE: grounded-research caches and their audit logs (see header). No backup by design.
DELETE FROM public.price_catalyst_cache;
DELETE FROM public.price_catalyst_audit;
DELETE FROM public.competitor_intel_cache;
DELETE FROM public.competitor_intel_audit;
DELETE FROM public.moat_intel_cache;
DELETE FROM public.moat_intel_audit;
DELETE FROM public.geopolitical_macro_cache;
DELETE FROM public.geopolitical_macro_audit;
DELETE FROM public.industry_override_audit;

-- DESTRUCTIVE (TAM side only): the grounded global TAM rows. `tam_scope = 'global'` is what the
-- retired Phase B wrote; the second arm catches a curated row written before migration 060 added
-- tam_scope and never back-filled — the retired `_backfill_global_scope` rule: a non-Census,
-- non-FRED, non-BEA label on a curated industry. A false positive there only swaps a Phase A
-- figure for the live Census/FRED compute until the recompute in step 3 restores it.
UPDATE public.industry_dossier
SET current_tam_b   = 0,
    future_tam_b    = 0,
    current_year    = NULL,
    future_year     = NULL,
    cagr_5y_pct     = NULL,
    lifecycle_phase = 'mature',
    source_grain    = 'all_industry',
    source_label    = 'Research figure withdrawn — pending recompute',
    tam_scope       = 'us'
WHERE tam_scope = 'global'
   OR (
        industry IN (
            'Semiconductors', 'Biotechnology', 'Drug Manufacturers - General',
            'Drug Manufacturers - Specialty & Generic', 'Medical - Devices',
            'Medical - Instruments & Supplies', 'Auto - Manufacturers', 'Aerospace & Defense',
            'Internet Content & Information', 'Software - Infrastructure', 'Software - Application',
            'Software - Services', 'Information Technology Services', 'Consumer Electronics',
            'Semiconductor Equipment & Materials', 'Communication Equipment', 'Internet Retail',
            'Beverages - Non-Alcoholic', 'Beverages - Alcoholic', 'Apparel - Footwear & Accessories'
        )
        AND coalesce(current_tam_b, 0) > 0
        AND lower(source_label) NOT LIKE '%census%'
        AND lower(source_label) NOT LIKE '%via fred%'
        AND lower(source_label) NOT LIKE 'bea %'
   );

-- DESTRUCTIVE: the grounded "why it moved" block on Updates cards (no longer produced or served).
UPDATE public.ai_insight_cache
SET price_move = NULL
WHERE price_move IS NOT NULL;

-- DESTRUCTIVE: grounding-redirect links the catalyst merged into a card's sources; the card's own
-- FMP article links stay, in order. An array left empty becomes NULL (what the writer stores).
UPDATE public.ai_insight_cache c
SET sources = (
    SELECT jsonb_agg(e.value ORDER BY e.ord)
    FROM jsonb_array_elements(c.sources) WITH ORDINALITY AS e(value, ord)
    WHERE coalesce(e.value ->> 'url', '') NOT LIKE '%vertexaisearch.cloud.google.com%'
)
WHERE jsonb_typeof(c.sources) = 'array'
  AND c.sources::text LIKE '%vertexaisearch.cloud.google.com%';

-- DESTRUCTIVE: report caches built before the retirement's CACHE_SCHEMA_FLOOR (already never
-- served). The literal MUST equal CACHE_SCHEMA_FLOOR in app/services/ticker_report_cache.py.
DELETE FROM public.ticker_data_cache   WHERE cached_at < '2026-10-03 01:30:00+00';
DELETE FROM public.ticker_report_cache WHERE cached_at < '2026-10-03 01:30:00+00';

COMMENT ON TABLE public.price_catalyst_cache IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.price_catalyst_audit IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.competitor_intel_cache IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.competitor_intel_audit IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.moat_intel_cache IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.moat_intel_audit IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.geopolitical_macro_cache IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.geopolitical_macro_audit IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';
COMMENT ON TABLE public.industry_override_audit IS
    'RETIRED 2026-10-02 (Google Search grounding terms). Purged by migration 188; no code reads or writes it.';

COMMIT;
