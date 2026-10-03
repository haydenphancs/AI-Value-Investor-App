-- 187_persona_copy_cleanup.sql
--
-- Why: the copy GET /research/personas serves from agent_personas makes two claims we
-- forbid everywhere else, and the table can still serve two rows named after real people.
--   1. SUITABILITY. warren_buffett's description (seeded by 043) ends with a sentence saying
--      which kind of investor the style is ideal FOR. That is the claim ADVICE_BOUNDARY
--      (persona_config.py) forbids and Caydex cannot make: it collects no risk tolerance,
--      finances or horizon. Its tagline (043/103) called an analysis style "Safe" — the same
--      claim in one word. Both are rewritten below.
--   2. A CATCHPHRASE ECHO. peter_lynch's description ("companies you understand and can spot
--      in everyday life") was the last trace of a famous investor's slogan and of the
--      "Everyday" label 155 removed. The report prompts were scrubbed in the same change.
--   3. REAL NAMES. 043 deactivated two extra rows, charlie_munger and benjamin_graham, whose
--      names are real investors'. get_personas serves EVERY is_active row and iOS now renders
--      any key the server sends, so if 043 is missing or was reverted those names show on the
--      persona row. This re-asserts is_active = FALSE for both (their other columns are left
--      alone: they are never served while inactive).
--
-- Restates name, tagline and description for ALL FIVE served rows, so the served copy is
-- fully determined by this file: production may still hold 'The Everyday Growth Hunter'
-- (155 unapplied, or reverted by a replay of 103), and 043 / 074 / 103 / 155 each revert some
-- of these columns if replayed.
-- REPLAY HAZARD: re-run THIS migration after replaying any of 043, 074, 103 or 155.
--
-- Unchanged: key (the wire id persisted in research_reports.investor_persona), icon_name,
-- accent_color, the five rows' is_active (043/074 set them TRUE), and every other column.
-- agent_tag lives in code. Display order is pinned in code (research._PERSONA_DISPLAY_ORDER),
-- so the heap reshuffle these UPDATEs cause cannot reorder the cards (the 155 bug).
--
-- Keep in sync: persona_config.py display_name; research.py _FALLBACK_PERSONAS; iOS
-- AnalysisPersona fallbacks (ResearchModels.swift). tests/test_persona_display_parity.py
-- resolves migrations >= 103 last-writer-wins and asserts all three agree, and runs the
-- suitability / real-name checks on the RESOLVED values (this header quotes old copy).
--
-- Idempotent: plain UPDATEs keyed on key (UNIQUE) in one transaction; a second run changes
-- nothing but updated_at (trg_agent_personas_updated_at).
--
-- MISSING-ROW GUARD — deliberately STRICT, for production: the DO block fails the whole
-- transaction (nothing applied) if any of the five rows is missing, instead of the silent
-- "UPDATE 0" 103 and 155 warned about. No migration seeds warren_buffett, cathie_wood,
-- peter_lynch or bill_ackman (only 074 seeds michael_burry; the other four pre-date the
-- migrations folder), so on a FRESH database this migration fails where 103/155 quietly did
-- nothing. That is intended: production holds all five rows, and a database without them is
-- not serving these personas anyway (get_personas falls back to _FALLBACK_PERSONAS).
--
-- No DDL: no schema_snapshot / Database Atlas change, no schema_curation entry.
--
-- BEFORE applying (look at what is served today):
--   SELECT key, name, tagline, description, is_active, updated_at
--     FROM public.agent_personas ORDER BY key;
--   -- If peter_lynch shows 'The Everyday Growth Hunter', 155 is not in effect; 187 fixes it.
--
-- VERIFY (after applying):
--   SELECT key, name, tagline, description FROM public.agent_personas
--    WHERE key IN ('warren_buffett','peter_lynch','cathie_wood','bill_ackman','michael_burry')
--    ORDER BY key;
--   -- five rows matching the strings below.
--   SELECT key, name FROM public.agent_personas WHERE is_active ORDER BY key;
--   -- exactly the five keys above; no charlie_munger / benjamin_graham, nothing else.

BEGIN;

DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(t.k, ', ' ORDER BY t.k) INTO missing
      FROM unnest(ARRAY['warren_buffett', 'peter_lynch', 'cathie_wood',
                        'bill_ackman', 'michael_burry']) AS t(k)
     WHERE NOT EXISTS (SELECT 1 FROM public.agent_personas p WHERE p.key = t.k);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '187: agent_personas has no row for: %. Seed it (074 seeds michael_burry), then re-run 187.', missing;
    END IF;
END$$;

UPDATE public.agent_personas
   SET name        = 'The Quality Compounder',
       tagline     = 'Durable, Long-term Value',
       description = 'Focuses on fundamental value, durable moats, consistent earnings, and long-term competitive advantages, and asks for a margin of safety on price.'
 WHERE key = 'warren_buffett';

UPDATE public.agent_personas
   SET name        = 'The Growth Hunter',
       tagline     = 'Growth at a Reasonable Price',
       description = 'Looks for growth at a reasonable price (GARP), weighing earnings growth against valuation, with a focus on understandable businesses.'
 WHERE key = 'peter_lynch';

UPDATE public.agent_personas
   SET name        = 'The Disruption Seeker',
       tagline     = 'Disruptive Innovation',
       description = 'Emphasizes disruptive innovation, emerging technologies, and high-growth potential companies that could reshape industries.'
 WHERE key = 'cathie_wood';

UPDATE public.agent_personas
   SET name        = 'The Activist Concentrator',
       tagline     = 'Activist Value',
       description = 'Takes concentrated positions in high-quality businesses, uses activist strategies to unlock value, and focuses on companies with durable competitive advantages.'
 WHERE key = 'bill_ackman';

UPDATE public.agent_personas
   SET name        = 'The Deep Value Skeptic',
       tagline     = 'Contrarian Deep Value',
       description = 'A contrarian skeptic who hunts deeply undervalued, out-of-favor companies with a large margin of safety, scrutinizes the balance sheet for hidden risk, and is wary of hype, crowded trades, and expensive darlings.'
 WHERE key = 'michael_burry';

-- The two real-name rows 043 deactivated. UPDATE 0 here is fine (the rows may not exist).
UPDATE public.agent_personas
   SET is_active = FALSE
 WHERE key IN ('charlie_munger', 'benjamin_graham');

COMMIT;
