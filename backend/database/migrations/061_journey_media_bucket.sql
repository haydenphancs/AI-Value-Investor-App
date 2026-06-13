-- 061_journey_media_bucket.sql
--
-- Why: The Investor Journey lessons are moving from bundled app assets to the
-- database. The lesson text/content already has a home — public.lessons.story_content
-- (JSONB, documented as {lessonLabel, lessonNumber, totalLessonsInLevel, estimatedMinutes, cards[]}).
-- Each card now also carries media URLs (audioUrl / imageUrl / videoUrl). Those media
-- files (AI narration .m4a, lesson images, optional videos) need a public Storage home.
--
-- This migration creates a public 'journey-media' Storage bucket and the read/write
-- policies for it. The lessons table itself already exists with RLS (lessons_select_all
-- public read + lessons_service_all service write), so no table DDL is needed here.
--
-- Bucket layout (by convention):
--   journey-media/audio/<lesson_key>_<NN>.m4a
--   journey-media/image/<lesson_key>_<slot>.<ext>
--   journey-media/video/<lesson_key>_<slot>.<ext>

-- 1. Public bucket (idempotent)
INSERT INTO storage.buckets (id, name, public)
VALUES ('journey-media', 'journey-media', true)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

-- 2. Public read of objects in this bucket (anon + authenticated)
DROP POLICY IF EXISTS "journey_media_public_read" ON storage.objects;
CREATE POLICY "journey_media_public_read" ON storage.objects
    FOR SELECT TO anon, authenticated
    USING (bucket_id = 'journey-media');

-- 3. Service role manages uploads/updates/deletes for this bucket
DROP POLICY IF EXISTS "journey_media_service_write" ON storage.objects;
CREATE POLICY "journey_media_service_write" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'journey-media')
    WITH CHECK (bucket_id = 'journey-media');
