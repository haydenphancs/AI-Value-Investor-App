-- 068_book_media_bucket.sql
--
-- Why: The Book Library is gaining narrated audio. Unlike Money Moves (one clip per
-- article) and the Journey (one clip per card), each BOOK is a single .m4a covering all of
-- its cores in order, with per-core start offsets so the player can seek to a core. The
-- book CONTENT stays in the app (BooksContent.swift, generated from documents/Books/), so
-- this feature needs no content table — only a public Storage home for the audio files.
--
-- This mirrors 061_journey_media_bucket.sql / 065_money_moves_content.sql exactly: a public
-- bucket with anon+authenticated read and service_role write. Public so the iOS AVPlayer can
-- stream the URL directly (same as journey/money-moves narration). The per-book audio URL is
-- deterministic — {SUPABASE_URL}/storage/v1/object/public/book-media/audio/<order>_<slug>.m4a —
-- and baked into the app via the generated BookAudioContent.swift; no serving endpoint needed.
--
-- Bucket layout (by convention):
--   book-media/audio/<curriculumOrder>_<slug>.m4a   e.g. 1_rich-dad-poor-dad.m4a

-- 1. Public bucket (idempotent)
INSERT INTO storage.buckets (id, name, public)
VALUES ('book-media', 'book-media', true)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

-- 2. Public read of objects in this bucket (anon + authenticated)
DROP POLICY IF EXISTS "book_media_public_read" ON storage.objects;
CREATE POLICY "book_media_public_read" ON storage.objects
    FOR SELECT TO anon, authenticated
    USING (bucket_id = 'book-media');

-- 3. Service role manages uploads/updates/deletes for this bucket
DROP POLICY IF EXISTS "book_media_service_write" ON storage.objects;
CREATE POLICY "book_media_service_write" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'book-media')
    WITH CHECK (bucket_id = 'book-media');
