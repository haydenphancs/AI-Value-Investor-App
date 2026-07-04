-- 083_retitle_themes.sql
--
-- Why: editorial call to drop the leading "The" from four Emerging Frontiers
-- theme cards (Home -> "Emerging Frontiers") so the grid reads punchier and is
-- less repetitive: "The Silicon Rush" -> "Silicon Rush", "The Modern Battlefield"
-- -> "Modern Battlefield", "The Robot Workforce" -> "Robot Workforce", "The Cyber
-- Wars" -> "Cyber Wars". The other four keep their wording on purpose:
--   * "The New Oil" / "The Final Frontier" are idioms that lean on the article.
--   * "Hacking Human Health" / "Powering the Machine" already have no "The".
--
-- Needed because 081 seeded these rows with `ON CONFLICT (slug) DO NOTHING`, so
-- re-applying 081 will NOT update an already-seeded database -- this explicit
-- UPDATE carries the change to installs where 081 has already run.
--
-- Idempotent + non-clobbering: each UPDATE is guarded on BOTH the slug AND the
-- exact old title, so it fires at most once and never overwrites a title an
-- editor has since changed in Supabase (mirrors 082's guarded backfill). The
-- slug is the stable key (drives the theme-detail deep-link + upsert identity)
-- and is intentionally NOT touched. Safe to re-run; a no-op once applied.

UPDATE public.trending_themes SET title = 'Silicon Rush', updated_at = now()
    WHERE slug = 'silicon-rush'       AND title = 'The Silicon Rush';
UPDATE public.trending_themes SET title = 'Modern Battlefield', updated_at = now()
    WHERE slug = 'modern-battlefield' AND title = 'The Modern Battlefield';
UPDATE public.trending_themes SET title = 'Robot Workforce', updated_at = now()
    WHERE slug = 'robot-workforce'    AND title = 'The Robot Workforce';
UPDATE public.trending_themes SET title = 'Cyber Wars', updated_at = now()
    WHERE slug = 'cyber-wars'         AND title = 'The Cyber Wars';
