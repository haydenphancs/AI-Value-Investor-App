-- 103_persona_style_names.sql
--
-- Why: the five research personas were named after real living investors — Warren
-- Buffett, Cathie Wood, Peter Lynch, Bill Ackman, Michael Burry — and those names were
-- user-facing product features (persona cards, report badges, the Home feed, the
-- "Default Analyst" setting). Naming a commercial product feature after a living person
-- creates state right-of-publicity and Lanham Act §43(a) false-endorsement exposure, and
-- App Review 5.2.1 forbids "misleading, false, or copycat representations, names, or
-- metadata". Describing the documented METHOD is fine; naming the feature after the
-- person is the part that creates the claim.
--
-- This renames the display labels to style names. The methods are still described in
-- prose (and each persona's prompt still references the school of thought by name), so
-- the product proposition survives.
--
-- IMPORTANT — what is NOT changing:
--   * `key` values (warren_buffett, cathie_wood, …) are UNCHANGED. They are the stable
--     identifiers persisted in research_reports.investor_persona and sent over the wire,
--     so every existing report keeps resolving. No data migration is needed.
--   * `agent_tag` values (buffett, wood, …) are UNCHANGED — the iOS ReportAgentPersona
--     badge dispatches on those.
-- Only the human-readable `name` (and matching taglines) move.
--
-- Idempotent: plain UPDATEs keyed on `key`, safe to re-run. Matches the
-- 043_align_personas_with_ios_fallback.sql pattern.
--
-- Keep in sync with backend/app/services/agents/persona_config.py (display_name),
-- research.py _FALLBACK_PERSONAS, and pdf_report_service._PERSONA_DISPLAY.
-- backend/tests/test_persona_display_parity.py asserts all four agree — it exists
-- because the tagline drifted between two of them the first time round.
--
-- REPLAY HAZARD: do NOT re-apply 043 or 074 after this migration.
--   * 074_seed_michael_burry_persona.sql is INSERT … ON CONFLICT (key) DO UPDATE
--     SET name = EXCLUDED.name with name = 'Michael Burry' → silently reverts that row.
--   * 043_align_personas_with_ios_fallback.sql restores tagline = 'Growth at Value'
--     for peter_lynch.
-- If either is ever replayed, re-run this migration afterwards.
--
-- Verify on apply: each statement should report `UPDATE 1`. `UPDATE 0` means the key is
-- missing from agent_personas and the rename silently did nothing for that persona.

UPDATE public.agent_personas
   SET name    = 'The Quality Compounder',
       tagline = 'Safe, Long-term Value'
 WHERE key = 'warren_buffett';

UPDATE public.agent_personas
   SET name    = 'The Disruption Seeker',
       tagline = 'Disruptive Innovation'
 WHERE key = 'cathie_wood';

UPDATE public.agent_personas
   SET name    = 'The Everyday Growth Hunter',
       tagline = 'Growth at a Reasonable Price'
 WHERE key = 'peter_lynch';

UPDATE public.agent_personas
   SET name    = 'The Activist Concentrator',
       tagline = 'Activist Value'
 WHERE key = 'bill_ackman';

UPDATE public.agent_personas
   SET name    = 'The Deep Value Skeptic',
       tagline = 'Contrarian Deep Value'
 WHERE key = 'michael_burry';

-- Any other rows (e.g. the Munger / Graham rows 043 deactivated) are left untouched:
-- they are is_active = FALSE and never served.
