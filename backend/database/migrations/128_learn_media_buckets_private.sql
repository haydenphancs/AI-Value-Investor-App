-- 128_learn_media_buckets_private.sql
--
-- ⚠️⚠️ DO NOT APPLY YET. Read all of this first. ⚠️⚠️
--
-- Why: Wiser narration is Pro/Max (see services/entitlements.LEARN_AUDIO_UNLOCKED_TIERS),
-- but all three audio buckets are PUBLIC — 061 (journey-media), 065 (money-moves-media),
-- 068 (book-media) each created a bucket with anon+authenticated read so AVPlayer could
-- stream the URL directly. Every object path is deterministic, and the clip NAMES ship
-- inside the app bundle (207 in Resources/Journey/journey_lessons.json, 13 in
-- Resources/MoneyMoves/money_moves.json; book URLs were compiled straight into
-- BookAudioContent.swift until the signed-URL change). So anyone with a build can
-- reconstruct every URL offline.
--
-- That makes the shipped gate OBSCURITY, not enforcement: stripping `audioUrl` from the
-- JSON withholds nothing, because the object is still world-readable. This migration is
-- what actually closes it.
--
-- ── PRECONDITION — this is a ONE-WAY DOOR for installed builds ───────────────────────────
--
-- Applying this instantly breaks narration in EVERY already-installed build: shipped copies
-- of BookAudioContent.swift carry baked public URLs, and any Learn payload already cached on
-- a device carries public URLs too. There is no client-side recovery.
--
-- Apply ONLY when all of these hold:
--   1. No build in anyone's hands still uses public URLs — no App Store release, and no
--      TestFlight testers on an older build. (As of 2026-08-12 the App Store Connect record
--      does not exist yet — LAUNCH_CHECKLIST §"START HERE" item #4 — so this is satisfiable
--      now, but re-check before applying.)
--   2. /learn/journey, /learn/money-moves and /learn/books/audio have been serving SIGNED
--      urls in production, and the logs show no
--      `learn_audio_urls: signing bucket=... failed` warnings.
--
-- Point 2 matters more than it looks. `learn_audio_urls` degrades a signing failure by
-- falling back to the STORED PUBLIC URL, which still plays today — so a broken signing path
-- is currently invisible. After this migration that same fallback becomes a dead URL and an
-- AVPlayer error. Signing health must be proven WHILE the fallback is still masking it.
--
-- Until this is applied, nothing is enforced. The signed-URL work exists so that the flip is
-- a single migration with no client change and no coordinated release — it does not, by
-- itself, close the hole.
--
-- Not touched: the three *_service_write policies. The seed scripts
-- (seed_journey.py / seed_money_moves.py / seed_book_audio.py) run as service_role and must
-- keep working. service_role also bypasses RLS for the reads that mint signed URLs.
--
-- ⚠️ ONE MORE THING TO CHECK BEFORE APPLYING. Only `audioUrl` is signed. A Journey card also
-- carries `imageUrl` and `videoUrl` (seed_journey.py writes both as NULL today, and lesson
-- artwork ships as bundled iOS assets), so nothing else in these buckets is referenced right
-- now. If either field is ever populated from `journey-media`, it must be added to
-- `learn_audio_gate._journey_audio_urls` FIRST — this migration would otherwise break lesson
-- artwork silently, and only for content that had been working.
--   Check with:  select count(*) from lessons
--                where story_content::text like '%"imageUrl": "http%'
--                   or story_content::text like '%"videoUrl": "http%';

-- 1. Flip the three Learn media buckets to private (idempotent).
UPDATE storage.buckets
   SET public = false
 WHERE id IN ('journey-media', 'money-moves-media', 'book-media');

-- 2. Remove anonymous/authenticated read. After this the ONLY read path is a signed URL
--    minted by app/services/learn_audio_urls.py on the service-role key.
--
-- DESTRUCTIVE (policy-level, not data): revokes public read of every object in these three
-- buckets. No rows are deleted and no audio is removed — re-running 061/065/068 restores the
-- previous state exactly if this needs to be rolled back.
DROP POLICY IF EXISTS "journey_media_public_read"     ON storage.objects;
DROP POLICY IF EXISTS "money_moves_media_public_read" ON storage.objects;
DROP POLICY IF EXISTS "book_media_public_read"        ON storage.objects;

-- Verification after applying (expect 403/400, not 200):
--   curl -sI "$SUPABASE_URL/storage/v1/object/public/book-media/audio/1_rich-dad-poor-dad.m4a"
-- and, with a Pro bearer, that this still returns a playable signed URL:
--   curl -s "$API/api/v1/learn/books/audio" -H "Authorization: Bearer $TOKEN"
