-- 066_user_book_progress.sql
--
-- Why: The Book Library (iOS) showed FAKE reading progress — `currentChapter` and
-- `isMastered` were hardcoded in Swift sample data (every user saw "Continue Core 2"
-- and "2 of 10 Books Mastered" regardless of what they'd read), and the only completion
-- signal ("Complete & Continue") wrote to in-view @State that was thrown away on dismiss.
-- Nothing persisted.
--
-- This adds real, per-core completion tracking so "Continue Core N", book mastery, and the
-- library % reflect what the user has actually read — and survive app restart / reinstall
-- and sync across devices. iOS keeps a local UserDefaults mirror for instant + offline
-- reads and writes through to this table; on launch it union-merges the server set in.
--
-- Schema: one row per (user, book, core) completed. The Book Library CONTENT lives in the
-- app (BooksContent.swift, regenerated from documents/books/), not the DB — so a book is
-- keyed by curriculum_order (1..10, the stable LibraryBook.curriculumOrder), not a books FK.
-- Mirrors the watchlist_items user-scoped pattern (no FK to auth.users so the shared guest
-- user id works too; backend writes via the service-role client which bypasses RLS).

CREATE TABLE IF NOT EXISTS user_book_progress (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    curriculum_order INT NOT NULL,
    core_number INT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, curriculum_order, core_number)
);

-- Hot path: "all cores this user has completed" (read on every Book Library / detail open).
CREATE INDEX IF NOT EXISTS idx_user_book_progress_user
    ON user_book_progress (user_id);

ALTER TABLE user_book_progress ENABLE ROW LEVEL SECURITY;

-- Users may only read/write their own rows. Service role (backend) bypasses via its policy.
DROP POLICY IF EXISTS "user_book_progress_select_own" ON user_book_progress;
CREATE POLICY "user_book_progress_select_own" ON user_book_progress
    FOR SELECT TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_book_progress_insert_own" ON user_book_progress;
CREATE POLICY "user_book_progress_insert_own" ON user_book_progress
    FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_book_progress_update_own" ON user_book_progress;
CREATE POLICY "user_book_progress_update_own" ON user_book_progress
    FOR UPDATE TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_book_progress_delete_own" ON user_book_progress;
CREATE POLICY "user_book_progress_delete_own" ON user_book_progress
    FOR DELETE TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_book_progress_service_all" ON user_book_progress;
CREATE POLICY "user_book_progress_service_all" ON user_book_progress
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON user_book_progress TO authenticated;
GRANT ALL ON user_book_progress TO service_role;
GRANT USAGE, SELECT ON SEQUENCE user_book_progress_id_seq TO service_role;
