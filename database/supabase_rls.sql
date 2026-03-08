-- =====================================================
-- Caydex - Row Level Security (RLS) Policies
-- Run AFTER supabase_schema.sql
-- Version: 3.0
-- Date: February 28, 2026
-- =====================================================
--
-- RLS Strategy:
--   - User-owned data: auth.uid() = user_id (direct, no JOIN needed)
--   - Public/read-only data: open SELECT, service_role for writes
--   - Chat messages: access via session ownership
--   - Whale data: public read, service_role writes
--   - Education content: public read, service_role writes
--
-- Since users.id = auth.users.id directly, RLS checks are simple:
--   auth.uid() = user_id (no JOIN to users table needed!)
-- =====================================================

-- =====================================================
-- ENABLE RLS ON ALL TABLES
-- =====================================================

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_personas ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE whales ENABLE ROW LEVEL SECURITY;
ALTER TABLE whale_sector_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE whale_holdings ENABLE ROW LEVEL SECURITY;
ALTER TABLE whale_trade_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE whale_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE whale_follows ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE books ENABLE ROW LEVEL SECURITY;
ALTER TABLE book_chapters ENABLE ROW LEVEL SECURITY;
ALTER TABLE lessons ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_lesson_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE money_move_articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_bookmarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE book_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE article_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_filing_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_study_schedules ENABLE ROW LEVEL SECURITY;

-- =====================================================
-- USERS
-- =====================================================
-- users.id = auth.uid() directly (no JOIN needed)

CREATE POLICY "users_select_own"
    ON users FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "users_update_own"
    ON users FOR UPDATE
    USING (auth.uid() = id);

CREATE POLICY "users_insert_own"
    ON users FOR INSERT
    WITH CHECK (auth.uid() = id);

CREATE POLICY "users_service_all"
    ON users FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- USER CREDITS
-- =====================================================

CREATE POLICY "credits_select_own"
    ON user_credits FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "credits_update_own"
    ON user_credits FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "credits_service_all"
    ON user_credits FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- WATCHLIST ITEMS
-- =====================================================

CREATE POLICY "watchlist_select_own"
    ON watchlist_items FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "watchlist_insert_own"
    ON watchlist_items FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "watchlist_update_own"
    ON watchlist_items FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "watchlist_delete_own"
    ON watchlist_items FOR DELETE
    USING (auth.uid() = user_id);

CREATE POLICY "watchlist_service_all"
    ON watchlist_items FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- TICKER NEWS CACHE (service role only)
-- =====================================================

ALTER TABLE ticker_news_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "ticker_news_cache_service_all"
    ON ticker_news_cache FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- AGENT PERSONAS (public read, service writes)
-- =====================================================

CREATE POLICY "personas_select_all"
    ON agent_personas FOR SELECT
    USING (true);

CREATE POLICY "personas_service_all"
    ON agent_personas FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- RESEARCH REPORTS
-- =====================================================

CREATE POLICY "reports_select_own"
    ON research_reports FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "reports_insert_own"
    ON research_reports FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "reports_update_own"
    ON research_reports FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "reports_delete_own"
    ON research_reports FOR DELETE
    USING (auth.uid() = user_id);

CREATE POLICY "reports_service_all"
    ON research_reports FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- CHAT SESSIONS
-- =====================================================

CREATE POLICY "chat_sessions_select_own"
    ON chat_sessions FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "chat_sessions_insert_own"
    ON chat_sessions FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "chat_sessions_update_own"
    ON chat_sessions FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "chat_sessions_delete_own"
    ON chat_sessions FOR DELETE
    USING (auth.uid() = user_id);

-- =====================================================
-- CHAT MESSAGES
-- =====================================================
-- Access via session ownership (JOIN to chat_sessions)

CREATE POLICY "chat_messages_select_own"
    ON chat_messages FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM chat_sessions
            WHERE chat_sessions.id = chat_messages.session_id
            AND chat_sessions.user_id = auth.uid()
        )
    );

CREATE POLICY "chat_messages_insert_own"
    ON chat_messages FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM chat_sessions
            WHERE chat_sessions.id = chat_messages.session_id
            AND chat_sessions.user_id = auth.uid()
        )
    );

-- Service role can insert AI responses
CREATE POLICY "chat_messages_service_insert"
    ON chat_messages FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- WHALES (public read, service writes)
-- =====================================================

CREATE POLICY "whales_select_all"
    ON whales FOR SELECT
    USING (true);

CREATE POLICY "whales_service_all"
    ON whales FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "whale_sectors_select_all"
    ON whale_sector_allocations FOR SELECT
    USING (true);

CREATE POLICY "whale_sectors_service_all"
    ON whale_sector_allocations FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "whale_holdings_select_all"
    ON whale_holdings FOR SELECT
    USING (true);

CREATE POLICY "whale_holdings_service_all"
    ON whale_holdings FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "whale_trade_groups_select_all"
    ON whale_trade_groups FOR SELECT
    USING (true);

CREATE POLICY "whale_trade_groups_service_all"
    ON whale_trade_groups FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "whale_trades_select_all"
    ON whale_trades FOR SELECT
    USING (true);

CREATE POLICY "whale_trades_service_all"
    ON whale_trades FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- WHALE FOLLOWS (user-owned)
-- =====================================================

CREATE POLICY "whale_follows_select_own"
    ON whale_follows FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "whale_follows_insert_own"
    ON whale_follows FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "whale_follows_delete_own"
    ON whale_follows FOR DELETE
    USING (auth.uid() = user_id);

-- =====================================================
-- NEWS ARTICLES (public read, service writes)
-- =====================================================

CREATE POLICY "news_select_all"
    ON news_articles FOR SELECT
    USING (true);

CREATE POLICY "news_service_all"
    ON news_articles FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- ASSET SNAPSHOTS (public read, service writes)
-- =====================================================

CREATE POLICY "snapshots_select_all"
    ON asset_snapshots FOR SELECT
    USING (true);

CREATE POLICY "snapshots_service_all"
    ON asset_snapshots FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- BOOKS & CHAPTERS (public read, service writes)
-- =====================================================

CREATE POLICY "books_select_all"
    ON books FOR SELECT
    USING (true);

CREATE POLICY "books_service_all"
    ON books FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "book_chapters_select_all"
    ON book_chapters FOR SELECT
    USING (true);

CREATE POLICY "book_chapters_service_all"
    ON book_chapters FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- LESSONS (public read, service writes)
-- =====================================================

CREATE POLICY "lessons_select_all"
    ON lessons FOR SELECT
    USING (true);

CREATE POLICY "lessons_service_all"
    ON lessons FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- USER LESSON PROGRESS (user-owned)
-- =====================================================

CREATE POLICY "lesson_progress_select_own"
    ON user_lesson_progress FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "lesson_progress_insert_own"
    ON user_lesson_progress FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "lesson_progress_update_own"
    ON user_lesson_progress FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "lesson_progress_service_all"
    ON user_lesson_progress FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- MONEY MOVE ARTICLES (public read, service writes)
-- =====================================================

CREATE POLICY "money_moves_select_all"
    ON money_move_articles FOR SELECT
    USING (true);

CREATE POLICY "money_moves_service_all"
    ON money_move_articles FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- USER BOOKMARKS (user-owned)
-- =====================================================

CREATE POLICY "bookmarks_select_own"
    ON user_bookmarks FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "bookmarks_insert_own"
    ON user_bookmarks FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "bookmarks_delete_own"
    ON user_bookmarks FOR DELETE
    USING (auth.uid() = user_id);

-- =====================================================
-- RAG VECTOR TABLES (public read, service writes)
-- =====================================================

CREATE POLICY "book_chunks_select_all"
    ON book_chunks FOR SELECT
    USING (true);

CREATE POLICY "book_chunks_service_all"
    ON book_chunks FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "article_chunks_select_all"
    ON article_chunks FOR SELECT
    USING (true);

CREATE POLICY "article_chunks_service_all"
    ON article_chunks FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "filing_chunks_select_all"
    ON company_filing_chunks FOR SELECT
    USING (true);

CREATE POLICY "filing_chunks_service_all"
    ON company_filing_chunks FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- USER STUDY SCHEDULES (user-owned)
-- =====================================================

CREATE POLICY "study_schedules_select_own"
    ON user_study_schedules FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "study_schedules_insert_own"
    ON user_study_schedules FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "study_schedules_update_own"
    ON user_study_schedules FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "study_schedules_service_all"
    ON user_study_schedules FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- VERIFICATION
-- =====================================================

DO $$
DECLARE
    r RECORD;
    rls_count INT := 0;
    policy_count INT := 0;
BEGIN
    RAISE NOTICE '=================================================';
    RAISE NOTICE 'RLS Status per Table:';
    RAISE NOTICE '-------------------------------------------------';

    FOR r IN (
        SELECT tablename, rowsecurity
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    )
    LOOP
        IF r.rowsecurity THEN
            rls_count := rls_count + 1;
        END IF;
        RAISE NOTICE '  %-35s %s', r.tablename,
            CASE WHEN r.rowsecurity THEN 'ENABLED' ELSE 'disabled' END;
    END LOOP;

    SELECT COUNT(*) INTO policy_count FROM pg_policies WHERE schemaname = 'public';

    RAISE NOTICE '-------------------------------------------------';
    RAISE NOTICE 'Tables with RLS enabled: %', rls_count;
    RAISE NOTICE 'Total policies created:  %', policy_count;
    RAISE NOTICE '=================================================';
END $$;
