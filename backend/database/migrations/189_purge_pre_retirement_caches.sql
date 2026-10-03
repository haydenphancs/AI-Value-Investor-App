-- 189_purge_pre_retirement_caches.sql
--
-- Why: migration 188 purged everything Gemini "Grounding with Google Search" had stored, but it
-- was applied BEFORE the deploy that removed the grounded code (owner, 2026-10-02). Production
-- kept running that code (git HEAD) afterwards, so it refilled the grounded caches and kept
-- writing ticker_data_cache / ticker_report_cache rows built from grounded research — AFTER the
-- CACHE_SCHEMA_FLOOR instant (2026-10-03 01:30 UTC) that 188's report-cache DELETEs and every
-- reader's clock rely on. Railway also overlaps the old and new deployments (up to ~300 s of
-- healthcheck plus a 30 s drain), so there is no clean deploy instant: any time-based rule leaks.
--
-- The fix in code is a POSITIVE provenance stamp written only by the post-retirement code and
-- required by every reader that serves a cached collection or another user's report:
--   * ticker_data_cache.collected_data -> 'grounding_free' = JSON true
--     (CollectedTickerData.grounding_free, set only by TickerReportDataCollector._collect_fresh;
--      checked by ticker_data_cache.collection_is_grounding_free)
--   * ticker_report_cache.ticker_report_data -> '_grounding_free' = JSON true
--     (report_degradation.GROUNDING_FREE_KEY, set by assemble_report on every report;
--      checked by report_degradation.report_is_grounding_free)
--   * ai_insight_cache.prompt_version >= 7 (news_insight_service._MIN_SERVABLE_PROMPT_VERSION;
--     only the current code writes 7, and every card below it is a read miss)
--   * the two chat answer caches are keyed on a versioned hash (`deep-dive-v2`, starter `v2`),
--     so no row the old code wrote can be looked up at all
-- An unstamped row is already never served once the new code is live. This migration deletes
-- them so they stop occupying the cache (and so `is_cached_collection_fresh`, which reads only
-- `cached_at`, stops calling an unstamped collection fresh and skipping its pre-warm), and
-- re-runs 188's purge of everything the old code refilled after 188 was applied.
-- The two JSON key literals below MUST equal those Python constants
-- (tests/test_grounding_free_stamp.py reads this file and checks).
--
-- Type-safe on purpose: `IS DISTINCT FROM 'true'::jsonb` keeps ONLY a JSON boolean true, exactly
-- like the Python readers (`is True`). A missing key (NULL), false, the string "true", 1, or a
-- payload that is not an object (`->` yields NULL there) is deleted.
--
-- DESTRUCTIVE. Rows lost — all of them either grounded-research output or a shared cache that
-- may contain it; no backup is needed and none should be kept (a kept copy is the storage the
-- Grounding terms forbid). Every one of these tables refills itself from licensed data:
--   * ticker_data_cache rows without the stamp           — re-collected on the next report /
--                                                           pre-warm (FMP + FRED, no Gemini)
--   * ticker_report_cache rows without the stamp         — regenerated on the next request
--   * everything 188 purged, again (HEAD refilled it after 188): the five grounded caches and
--     their audit logs, industry_override_audit, the grounded GLOBAL TAM side of
--     industry_dossier, ai_insight_cache.price_move and its grounding-redirect source links
--   * ai_insight_cache rows below prompt_version 7 — Updates cards whose text was generated
--     with the grounded catalyst in the prompt; never served by the current code. The feed shows
--     the plain headline list for that ticker until the sweeper writes a v7 card (its next
--     active pass: 04:00-20:00 ET on trading days, every 30 min off-hours for coins)
--   * market_deep_dive_cache (ALL rows)                  — the chat "AI Analyst" briefs, shared
--     across users for 24 h; the old chat could call the grounded catalyst search mid-turn and
--     the rows carry no provenance. A miss generates the brief live (`_check_deep_dive_cache`
--     returns None → a normal turn; the next tap is cached again by the current code).
--   * chat_starter_answers (ALL rows)                    — the pre-computed Ask Cay AI chip
--     answers, shared across users for the ET day, same reason. A miss answers the chip live
--     (`chat_starter_warm_service.lookup` returns None), and the lifespan warm loop
--     (`warm_todays_starters`) re-answers today's chips on its next pass.
--
-- NOT touched, by owner decision (2026-10-02, pending counsel): research_reports (users' saved
-- report history — their own report is not gated on the stamp either) and notification_events
-- bodies (90-day prune). The report PDFs in the research-pdfs bucket are not touched either.
--
-- APPLY ORDER — only after Railway shows the OLD deployment REMOVED (the new deployment Active
-- and no earlier deployment still Active or Removing). While any old instance runs it refills
-- these tables and writes unstamped rows; rows the NEW code writes are stamped and survive.
--   1. Deploy; wait until Railway lists the old deployment as removed.
--   2. Apply this migration.
--   3. POST /api/v1/admin/refresh-industry-dossier — Phase A rewrites every industry, including
--      any the reset below matched. It takes the quarterly job's day-keyed claim, so it answers
--      409 SYSTEM_BUSY with details.reason = already_ran_today if a dossier recompute already
--      succeeded that UTC day (188's step 3, or Sunday's chain). Then retry steps 3 and 4 after
--      00:00 UTC, or let the next quarterly run do them; until then the read path serves a live
--      Census/FRED figure for a reset industry, so nothing grounded is shown meanwhile.
--   4. POST /api/v1/admin/refresh-industry-moat-benchmarks?skip_recent_hours=0 — the Network
--      Effects pillar reads the lifecycle phase of those industries.
--
-- Idempotent: every DELETE / UPDATE matches nothing on a second run (barring new unstamped
-- writes, which only an old deployment produces). No schema change.
--
-- VERIFY (run right after applying — one row, every count 0; the two chat caches may hold new
-- rows minutes later because the current code refills them, so for those compare the oldest
-- row with the time you applied this):
--   SELECT
--     (SELECT count(*) FROM public.ticker_data_cache
--       WHERE (collected_data -> 'grounding_free') IS DISTINCT FROM 'true'::jsonb)      AS unstamped_collections,
--     (SELECT count(*) FROM public.ticker_report_cache
--       WHERE (ticker_report_data -> '_grounding_free') IS DISTINCT FROM 'true'::jsonb) AS unstamped_reports,
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
--     (SELECT count(*) FROM public.ai_insight_cache WHERE prompt_version < 7)         AS pre_v7_cards,
--     (SELECT count(*) FROM public.updates_insight_state s WHERE s.last_inputset_id IS NOT NULL
--       AND NOT EXISTS (SELECT 1 FROM public.ai_insight_cache c
--                        WHERE c.scope = s.scope AND c.prompt_version >= 7))     AS stale_fingerprints,
--     (SELECT count(*) FROM public.ai_insight_cache
--       WHERE sources::text LIKE '%vertexaisearch.cloud.google.com%')               AS grounding_links,
--     (SELECT min(cached_at)  FROM public.market_deep_dive_cache)                     AS oldest_deep_dive,
--     (SELECT min(created_at) FROM public.chat_starter_answers)                       AS oldest_starter_answer;

BEGIN;

-- DESTRUCTIVE: report-data collections without the post-retirement stamp (see header). They
-- are never served by the current code; deleting them also lets the pre-warmer rebuild them.
DELETE FROM public.ticker_data_cache
WHERE (collected_data -> 'grounding_free') IS DISTINCT FROM 'true'::jsonb;

-- DESTRUCTIVE: cached reports (served free to every user) without the stamp (see header).
DELETE FROM public.ticker_report_cache
WHERE (ticker_report_data -> '_grounding_free') IS DISTINCT FROM 'true'::jsonb;

-- DESTRUCTIVE: 188's purge again — the pre-retirement code refilled these after 188 ran.
-- Grounded-research caches and their audit logs. No backup by design.
DELETE FROM public.price_catalyst_cache;
DELETE FROM public.price_catalyst_audit;
DELETE FROM public.competitor_intel_cache;
DELETE FROM public.competitor_intel_audit;
DELETE FROM public.moat_intel_cache;
DELETE FROM public.moat_intel_audit;
DELETE FROM public.geopolitical_macro_cache;
DELETE FROM public.geopolitical_macro_audit;
DELETE FROM public.industry_override_audit;

-- DESTRUCTIVE (TAM side only): 188's industry_dossier reset, verbatim. The current code never
-- writes tam_scope = 'global' (industry_dossier_service only compares against GROUNDED_TAM_SCOPE)
-- and its Phase A labels carry the Census / "via FRED" / "BEA " markers the second arm keys
-- off, so only rows the old code's Phase B wrote match. Concentration columns are kept.
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

-- DESTRUCTIVE: Updates cards written before prompt_version 7 (see header). Never served by the
-- current code (`_MIN_SERVABLE_PROMPT_VERSION`); the sweeper regenerates each one at v7.
DELETE FROM public.ai_insight_cache
WHERE prompt_version < 7;

-- A scope left with no servable card must not keep a fingerprint that reads "unchanged": in the
-- deployment overlap an old instance can write a v6 card AFTER the new code stored its v7
-- fingerprint, and the DELETE above would then leave that scope card-less until its news changes.
-- A NULL fingerprint is the sweeper's cold start (`updates_materiality`), so it regenerates.
UPDATE public.updates_insight_state s
SET last_inputset_id = NULL
WHERE s.last_inputset_id IS NOT NULL
  AND NOT EXISTS (
        SELECT 1 FROM public.ai_insight_cache c
        WHERE c.scope = s.scope AND c.prompt_version >= 7
  );

-- DESTRUCTIVE: the grounded "why it moved" block on Updates cards (188, again). The current code
-- writes price_move = NULL. (Only v7+ rows remain after the DELETE above, so this is a guard.)
UPDATE public.ai_insight_cache
SET price_move = NULL
WHERE price_move IS NOT NULL;

-- DESTRUCTIVE: grounding-redirect links merged into a card's sources (188, again); the card's
-- FMP article links stay, in order, and an array left empty becomes NULL.
UPDATE public.ai_insight_cache c
SET sources = (
    SELECT jsonb_agg(e.value ORDER BY e.ord)
    FROM jsonb_array_elements(c.sources) WITH ORDINALITY AS e(value, ord)
    WHERE coalesce(e.value ->> 'url', '') NOT LIKE '%vertexaisearch.cloud.google.com%'
)
WHERE jsonb_typeof(c.sources) = 'array'
  AND c.sources::text LIKE '%vertexaisearch.cloud.google.com%';

-- DESTRUCTIVE: shared chat answer caches with no provenance (see header). Both refill live, and
-- the current code can no longer read an old row (versioned keys) — this frees the space and the
-- starter warm loop's daily cap, which counts every row warmed today.
DELETE FROM public.market_deep_dive_cache;
DELETE FROM public.chat_starter_answers;

COMMENT ON TABLE public.chat_starter_answers IS
    'Pre-computed answers to the day''s Ask Cay AI suggestion chips, so tapping one replays '
    'a stored answer instead of paying a Gemini turn. Keyed on a versioned hash of the QUESTION, '
    'not the chip slot, because the chip set drifts intraday as the hot-ticker and hot-sector '
    'slots track the tape. One ET day of retention: yesterday''s answer to a "today" question '
    'is wrong, not merely stale. Written by app/services/chat_starter_warm_service.py; read by '
    'the chat streaming endpoint. (Before 2026-10-02 an answer could rest on a Google-grounded '
    'web search; that tier is retired and migration 189 purged its rows.)';

COMMENT ON COLUMN public.chat_starter_answers.question_hash IS
    'SHA-256 of a version prefix (v2) plus the NFKC-normalised, case-folded question text '
    '(chat_starter_warm_service.question_hash). Fixed-width key; the raw question is kept '
    'alongside so a collision would be visible rather than silent.';

COMMIT;
