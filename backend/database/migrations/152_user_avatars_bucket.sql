-- 152_user_avatars_bucket.sql
--
-- Why: users can now set a profile picture from their photo library
-- (POST /api/v1/users/me/avatar). The bytes need somewhere to live that is
-- neither the database nor a bucket anyone can list.
--
-- PRIVATE, for the same reason 063 made research-pdfs private: this is per-user
-- content, so a leaked or guessed object URL must not be enough to read it. A
-- PUBLIC bucket is also world-LISTABLE with the shipped anon key (see 136), which
-- would turn this into an enumerable directory of every user's face.
--
-- ⚠️ Where this DIVERGES from 063, deliberately: 063 serves its objects through an
-- authed backend proxy and mints no signed URLs at all ("a leaked object URL must
-- not be enough"). We cannot do that here. The render path is SwiftUI's AsyncImage
-- (ProfileAvatarView), which takes a URL and cannot attach an Authorization header
-- — the same constraint migration 128 hit with AVPlayerItem for the Learn buckets.
-- So we follow 128 instead and sign on read: 24h signature, re-minted every 6h.
-- The accepted cost is that a leaked signed URL is readable for up to 24 hours.
--
-- Bucket layout (by convention, enforced in app/services/avatar_service.py):
--   user-avatars/avatars/<user_id>/<sha256(jpeg)[:32]>.jpg
--
-- The key is CONTENT-ADDRESSED and derived entirely server-side. Nothing in it
-- comes from the request, so path traversal and cross-user overwrite are
-- impossible; and replacing a photo produces a NEW path, so no CDN or on-device
-- URL cache can serve the old one at the moment the user changes it.
--
-- Nothing here is destructive; re-running is safe.

-- 1. Private bucket (idempotent). The MIME allowlist and size limit are a second
--    line of defence behind the app's own validation, not the primary one.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('user-avatars', 'user-avatars', false, 393216, ARRAY['image/jpeg'])
ON CONFLICT (id) DO UPDATE
    SET public             = EXCLUDED.public,
        file_size_limit    = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

-- 2. Service role only. No anon/authenticated policy: the backend is the only
--    writer, and signed URLs are the only read path. Adding a read policy here
--    would hand the shipped anon key the ability to list every avatar.
DROP POLICY IF EXISTS "user_avatars_service_all" ON storage.objects;
CREATE POLICY "user_avatars_service_all" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'user-avatars')
    WITH CHECK (bucket_id = 'user-avatars');

COMMENT ON POLICY "user_avatars_service_all" ON storage.objects IS
    'Profile pictures. Service-role only; reads go out as short-lived signed URLs minted by avatar_service.';
