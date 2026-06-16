-- 067_user_learn_progress.sql
--
-- Why: the three Learn features (Book Library, Investor Journey, Money Moves) each track a
-- "completion log" of the IDENTICAL shape (user + item key + completed_at). They don't need
-- three tables. This consolidates them into ONE polymorphic table, so adding a future Learn
-- feature needs no schema change at all.
--
--   content_type  discriminates the feature
--   item_key      that feature's stable key:
--       book_core      -> "<curriculum_order>-<core_number>"   (e.g. "1-3")
--       journey_lesson -> lesson title
--       money_move     -> article slug
--
-- 066_user_book_progress was already applied, so this COPIES its rows into the unified table
-- (idempotent). The old user_book_progress table is left in place (no data loss) — it's now
-- unused and can be dropped manually after you've verified this migration. The journey /
-- money-move tables were never created (their per-feature migrations were dropped in favor of
-- this one). Mirrors the user-scoped RLS pattern (no FK so the shared guest id works; backend
-- writes via the service-role client which bypasses RLS).

CREATE TABLE IF NOT EXISTS user_learn_progress (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    content_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, content_type, item_key)
);

-- Hot path: "everything of this type this user has completed".
CREATE INDEX IF NOT EXISTS idx_user_learn_progress_lookup
    ON user_learn_progress (user_id, content_type);

ALTER TABLE user_learn_progress ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_learn_progress_select_own" ON user_learn_progress;
CREATE POLICY "user_learn_progress_select_own" ON user_learn_progress
    FOR SELECT TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_learn_progress_insert_own" ON user_learn_progress;
CREATE POLICY "user_learn_progress_insert_own" ON user_learn_progress
    FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_learn_progress_update_own" ON user_learn_progress;
CREATE POLICY "user_learn_progress_update_own" ON user_learn_progress
    FOR UPDATE TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_learn_progress_delete_own" ON user_learn_progress;
CREATE POLICY "user_learn_progress_delete_own" ON user_learn_progress
    FOR DELETE TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_learn_progress_service_all" ON user_learn_progress;
CREATE POLICY "user_learn_progress_service_all" ON user_learn_progress
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON user_learn_progress TO authenticated;
GRANT ALL ON user_learn_progress TO service_role;
GRANT USAGE, SELECT ON SEQUENCE user_learn_progress_id_seq TO service_role;

-- Backfill the already-applied book progress (guarded + idempotent).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'user_book_progress'
    ) THEN
        INSERT INTO user_learn_progress (user_id, content_type, item_key, completed_at)
        SELECT user_id, 'book_core', curriculum_order || '-' || core_number, completed_at
        FROM user_book_progress
        ON CONFLICT (user_id, content_type, item_key) DO NOTHING;
    END IF;
END $$;
