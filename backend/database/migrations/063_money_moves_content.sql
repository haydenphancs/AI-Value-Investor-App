-- 063_money_moves_content.sql
--
-- Why: "Money Moves" (the iOS case-study / deep-dive reading + listening feature)
-- has had its article content 100% hardcoded in Swift, with no backend behind it.
-- A public.money_move_articles table already exists (created in Supabase Studio,
-- outside the migration flow — same story as public.lessons) but it is an ORPHAN:
-- nothing reads or writes it, it has no table-level GRANTs, and it lacks the fields
-- the rich iOS article needs (key highlights, comments, hero gradient, etc.) plus a
-- home for the narration voice.
--
-- We mirror the Investor Journey content pattern exactly: store the full iOS-shaped
-- article as a single JSONB passthrough (`content`) that the iOS Codable decoder reads
-- directly, keep a few first-class columns for querying/ordering, and serve it via
-- GET /api/v1/learn/money-moves (cached service) with a bundled-JSON offline fallback.
--
-- This migration:
--   1. ALTERs money_move_articles up to spec (idempotent ADD COLUMN IF NOT EXISTS).
--   2. Adds a unique slug index (deterministic upsert/lookup key) + a sort index.
--   3. Grants table-level privileges (RLS already exists; GRANT is the missing piece —
--      same latent gap 062 fixed for public.lessons; without it service_role gets
--      "permission denied for table money_move_articles", SQLSTATE 42501).
--   4. Creates the public 'money-moves-media' Storage bucket for the narration .m4a
--      (the voice is generated/uploaded later; audio_url stays NULL until then).
--
-- The legacy typed columns (sections / statistics / related_articles / author_*) are
-- left untouched and unused — the served payload is `content`. A future cleanup
-- migration can DROP them once confirmed dead. id is a uuid with a gen_random_uuid()
-- default, so there is no sequence to grant.
--
-- Idempotent: every statement is guarded (IF NOT EXISTS / DROP POLICY IF EXISTS /
-- ON CONFLICT / GRANT) and safe to re-run.

-- 1. Bring the table up to spec ------------------------------------------------
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS slug TEXT;
-- Full iOS-shaped article (MoneyMoveArticleDTO): {slug, title, subtitle, category,
-- author{}, readTimeMinutes, viewCount, tagLabel, isFeatured, hasAudioVersion,
-- audioUrl, heroGradientColors[], keyHighlights[], sections[], statistics[],
-- comments[], relatedArticles[]}. camelCase keys — decoded directly by iOS.
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS content JSONB;
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS view_count TEXT;
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS is_featured BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS has_audio_version BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS audio_url TEXT;             -- the narration voice (filled later)
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS audio_duration_seconds INTEGER;
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.money_move_articles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN public.money_move_articles.content IS
    'JSONB passthrough of the full iOS MoneyMoveArticleDTO (camelCase keys). Source of truth for the served article.';
COMMENT ON COLUMN public.money_move_articles.audio_url IS
    'Public money-moves-media URL of the narration .m4a (Achird TTS). NULL until voice is generated.';

-- 2. Indexes ------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS idx_money_move_articles_slug
    ON public.money_move_articles(slug);
CREATE INDEX IF NOT EXISTS idx_money_move_articles_sort
    ON public.money_move_articles(sort_order);

-- 3. Table-level GRANTs (RLS policies money_moves_select_all / money_moves_service_all
--    already exist; RLS restricts rows but does not grant table access). --------
GRANT SELECT ON public.money_move_articles TO anon, authenticated;
GRANT ALL    ON public.money_move_articles TO service_role;

-- 4. Public Storage bucket for narration audio (mirror of 061_journey_media_bucket) --
--    Layout: money-moves-media/audio/<slug>.m4a
INSERT INTO storage.buckets (id, name, public)
VALUES ('money-moves-media', 'money-moves-media', true)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

DROP POLICY IF EXISTS "money_moves_media_public_read" ON storage.objects;
CREATE POLICY "money_moves_media_public_read" ON storage.objects
    FOR SELECT TO anon, authenticated
    USING (bucket_id = 'money-moves-media');

DROP POLICY IF EXISTS "money_moves_media_service_write" ON storage.objects;
CREATE POLICY "money_moves_media_service_write" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'money-moves-media')
    WITH CHECK (bucket_id = 'money-moves-media');
