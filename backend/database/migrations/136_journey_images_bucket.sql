-- 136_journey_images_bucket.sql
--
-- Why: every Investor Journey lesson opens on a title card that reserves a hero image
-- slot, and all 27 of them render the "Image coming soon" placeholder because nothing
-- has ever populated it. There are ZERO journey_* images in Assets.xcassets, so the
-- bundled-asset fallback in JourneyContentStore has never resolved to a picture. This
-- bucket holds the generated lesson artwork so the app can show it.
--
-- Layout: journey-images/lessons/<authoredSlug>.hero.jpg
--   .hero.jpg  1536x864 (16:9, q82)  =  345x180pt @3x, the LessonTitleCard slot
-- Roughly 1.2 MB across all 27 objects.
--
-- <authoredSlug> is the `slug` field from journey_lessons.json — the SAME key
-- scripts/seed_journey.py feeds to uuid5 for the lessons row id, so the storage path
-- and the database identity can never drift apart. It is deliberately NOT the
-- title-derived slug iOS uses for bundled asset names: those two disagree for 14 of the
-- 27 lessons (buffett_way vs the_buffett_way, and so on).
--
-- No <order> prefix, unlike 133's <curriculumOrder>_<slug>: Journey's sortOrder is
-- per-level and repeats 1-7 four times over, so it is not unique. The slug is, and
-- seed_journey.py already refuses to run when two lessons collide on it.
--
-- ⚠️ THIS BUCKET IS PUBLIC ON PURPOSE AND MUST STAY PUBLIC.
-- 128_learn_media_buckets_private.sql (APPLIED) flipped journey-media /
-- money-moves-media / book-media to private because narration is a paid (Pro/Max)
-- feature. LESSON ARTWORK IS FREE and must render for a locked, signed-out reader.
-- Three independent reasons it lives here rather than in journey-media:
--
--   1. journey-media is ALREADY private, so a hero stored there would 404 outright. The
--      iOS AsyncImage error phase renders the placeholder — no crash, no log, no toast —
--      so the art would silently vanish for content that had been working.
--   2. Signed URLs are re-minted with a fresh ?token= every few hours, and iOS
--      URLCache keys on the whole URL, so all 27 heroes would re-download twice a day
--      forever. A stable public URL caches indefinitely. Narration accepts that cost
--      because its bucket is private and there is no alternative; artwork does not have
--      to, and should not.
--
-- (A third reason used to be recorded here: that signing would INVERT the entitlement,
-- because only entitled callers reached sign_journey while locked callers went through
-- redact_journey. That argument is now void — Journey narration is free on every tier,
-- redact_journey was deleted, and EVERY caller reaches sign_journey. Reasons 1 and 2
-- stand on their own and are why this bucket is still public.)
--
-- Therefore: never add journey-images to 128's flip list, and never add it to
-- app/services/learn_audio_urls.py::_SIGNABLE_BUCKETS. Pinned by
-- tests/test_journey_art_parity.py and tests/test_journey_schema_parity.py.
--
-- Mirrors 133_book_covers_bucket.sql's `book-covers` and 081_trending_themes.sql's
-- `home-theme-media`, both public for the same reason: free imagery the client fetches
-- directly via AsyncImage.
--
-- Bucket-only migration: no table, so no RLS on public.* and no GRANTs to add. Storage
-- access is governed entirely by the two policies below.
--
-- Idempotent: safe to re-run.

-- A public bucket is world-readable AND world-listable by the shipped anon key, so
-- nothing non-public may ever be put here. The mime/size limits are cheap hardening the
-- sibling buckets do not have: they cap what a holder of the service_role key could
-- serve from our own Supabase domain. 27 heroes run ~30-75 KB each; 2 MB is generous.
-- Note DO UPDATE re-publicizes a bucket someone had made private — that is the intended
-- enforcement of "must stay public", matching 133.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('journey-images', 'journey-images', true, 2097152, ARRAY['image/jpeg'])
ON CONFLICT (id) DO UPDATE
    SET public = EXCLUDED.public,
        file_size_limit = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

-- Anyone may read lesson artwork, signed in or not, free tier or paid. This is the
-- whole point of the bucket.
DROP POLICY IF EXISTS "journey_images_public_read" ON storage.objects;
CREATE POLICY "journey_images_public_read" ON storage.objects
    FOR SELECT TO anon, authenticated
    USING (bucket_id = 'journey-images');

-- Only the seed script (service_role) writes. Mirrors book_covers_service_write.
DROP POLICY IF EXISTS "journey_images_service_write" ON storage.objects;
CREATE POLICY "journey_images_service_write" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'journey-images')
    WITH CHECK (bucket_id = 'journey-images');

-- Verify immediately after applying (expect 400/404 — NOT 403, which would mean the
-- public read policy did not take):
--   curl -sI "$SUPABASE_URL/storage/v1/object/public/journey-images/lessons/compound_interest.hero.jpg"
-- Then re-run after scripts/seed_journey.py and expect HTTP 200.
