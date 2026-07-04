-- 082_trending_themes_subtitle.sql
--
-- Why: the Emerging Frontiers theme DETAIL screen (GET /home/themes/{slug}) shows
-- a hero with the title + an editorial SUBTITLE (e.g. "The chips and models
-- powering the AI era"). Migration 081 created public.trending_themes without a
-- subtitle column; add it and backfill the seeded rows. The card on Home does not
-- use the subtitle — only the detail hero does.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + UPDATEs guarded on the slug AND on the
-- subtitle still being unset (never clobber an editor's later wording). Safe to
-- re-run and safe whether or not 081 has been applied yet (081 must come first).

ALTER TABLE public.trending_themes ADD COLUMN IF NOT EXISTS subtitle text;

COMMENT ON COLUMN public.trending_themes.subtitle IS
    'Editorial tagline shown under the title on the theme detail hero. Editable in Supabase; no app release.';

-- Backfill subtitles for the 081 seed rows, only when still unset.
UPDATE public.trending_themes SET subtitle = 'The chips and models powering the AI era'
    WHERE slug = 'silicon-rush'       AND (subtitle IS NULL OR subtitle = '');
UPDATE public.trending_themes SET subtitle = 'Defense primes and the new face of warfare'
    WHERE slug = 'modern-battlefield' AND (subtitle IS NULL OR subtitle = '');
UPDATE public.trending_themes SET subtitle = 'The critical minerals the world is racing to secure'
    WHERE slug = 'the-new-oil'        AND (subtitle IS NULL OR subtitle = '');
UPDATE public.trending_themes SET subtitle = 'Automation and the machines reshaping labor'
    WHERE slug = 'robot-workforce'    AND (subtitle IS NULL OR subtitle = '');
UPDATE public.trending_themes SET subtitle = 'Genomics and the biotech rewriting medicine'
    WHERE slug = 'hacking-health'     AND (subtitle IS NULL OR subtitle = '');
UPDATE public.trending_themes SET subtitle = 'The front line of digital defense'
    WHERE slug = 'cyber-wars'         AND (subtitle IS NULL OR subtitle = '');
UPDATE public.trending_themes SET subtitle = 'Nuclear, grids and the power behind the boom'
    WHERE slug = 'powering-machine'   AND (subtitle IS NULL OR subtitle = '');
UPDATE public.trending_themes SET subtitle = 'Rockets, satellites and the orbital economy'
    WHERE slug = 'final-frontier'     AND (subtitle IS NULL OR subtitle = '');
