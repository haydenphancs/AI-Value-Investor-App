-- 133_book_covers_bucket.sql
--
-- Why: the ten AI-Books in the Wiser tab have no cover art. Every place a cover is
-- drawn renders a flat SwiftUI LinearGradient with the title typed over it, and
-- SearchBookCard renders no cover and no title at all (it calls Image(coverImageName)
-- for assets that do not exist in Assets.xcassets). This bucket holds the generated
-- cover images so the app can show real artwork.
--
-- Layout: book-covers/covers/<curriculumOrder>_<slug>.<thumb|hero>.jpg
--   .thumb.jpg  240x330  =  80x110pt @3x   (LibraryBookCard / EducationBookCard / SearchBookCard)
--   .hero.jpg   480x660  = 160x220pt @3x   (BookDetailView)
-- Roughly 1.1 MB across all 20 objects.
--
-- ⚠️ THIS BUCKET IS PUBLIC ON PURPOSE AND MUST STAY PUBLIC.
-- 128_learn_media_buckets_private.sql flips book-media / journey-media /
-- money-moves-media to private because narration is a paid (Pro/Max) feature. A COVER
-- IS FREE — a locked, signed-out user must still see it — and its URL is compiled into
-- frontend/ios/ios/Models/BookCoverContent.swift with NO signing path on either side
-- (app/services/learn_audio_urls.py:_SIGNABLE_BUCKETS signs only those three buckets,
-- and only the `audioUrl` field). Putting covers in book-media, or adding book-covers
-- to 128's flip list, blanks every cover in the app the day 128 is applied.
--
-- Mirrors 081_trending_themes.sql's `home-theme-media`, which is public for exactly the
-- same reason: free imagery the client fetches directly via AsyncImage.
--
-- Bucket-only migration: no table, so no RLS on public.* and no GRANTs to add. Storage
-- access is governed entirely by the two policies below.
--
-- Idempotent: safe to re-run.

INSERT INTO storage.buckets (id, name, public)
VALUES ('book-covers', 'book-covers', true)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

-- Anyone may read a cover, signed in or not. This is the whole point of the bucket.
DROP POLICY IF EXISTS "book_covers_public_read" ON storage.objects;
CREATE POLICY "book_covers_public_read" ON storage.objects
    FOR SELECT TO anon, authenticated
    USING (bucket_id = 'book-covers');

-- Only the seed script (service_role) writes. Mirrors book_media_service_write.
DROP POLICY IF EXISTS "book_covers_service_write" ON storage.objects;
CREATE POLICY "book_covers_service_write" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'book-covers')
    WITH CHECK (bucket_id = 'book-covers');

-- Verify after applying (expect HTTP 200 once seed_book_covers.py has run):
--   curl -sI "$SUPABASE_URL/storage/v1/object/public/book-covers/covers/2_the-intelligent-investor.thumb.jpg"
