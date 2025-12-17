-- =====================================================
-- Supabase Row Level Security (RLS) Policies
-- Run this AFTER creating the main schema
-- =====================================================

-- =====================================================
-- ENABLE RLS ON TABLES
-- =====================================================

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlists ENABLE ROW LEVEL SECURITY;
ALTER TABLE deep_research_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_updates ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_activity_log ENABLE ROW LEVEL SECURITY;

-- Public tables (no RLS needed):
-- stocks, news_articles, news_stocks, breaking_news, company_fundamentals,
-- earnings, stock_prices, analyst_forecasts, company_insights,
-- educational_content, content_chunks, article_chunks

-- =====================================================
-- USERS TABLE POLICIES
-- =====================================================

-- Users can read their own data
CREATE POLICY "Users can view own profile"
    ON users FOR SELECT
    USING (auth.uid() = auth_user_id);

-- Users can update their own data
CREATE POLICY "Users can update own profile"
    ON users FOR UPDATE
    USING (auth.uid() = auth_user_id);

-- Allow user creation on signup
CREATE POLICY "Users can insert own profile"
    ON users FOR INSERT
    WITH CHECK (auth.uid() = auth_user_id);

-- Service role can do everything
CREATE POLICY "Service role full access to users"
    ON users
    USING (auth.role() = 'service_role');

-- =====================================================
-- WATCHLISTS TABLE POLICIES
-- =====================================================

-- Users can view their own watchlists
CREATE POLICY "Users can view own watchlist"
    ON watchlists FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = watchlists.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can add to their watchlist
CREATE POLICY "Users can add to own watchlist"
    ON watchlists FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = watchlists.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can update their watchlist
CREATE POLICY "Users can update own watchlist"
    ON watchlists FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = watchlists.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can delete from their watchlist
CREATE POLICY "Users can delete from own watchlist"
    ON watchlists FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = watchlists.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- =====================================================
-- DEEP RESEARCH REPORTS POLICIES
-- =====================================================

-- Users can view their own reports
CREATE POLICY "Users can view own reports"
    ON deep_research_reports FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = deep_research_reports.user_id
            AND users.auth_user_id = auth.uid()
        )
        AND deleted_at IS NULL
    );

-- Users can create reports
CREATE POLICY "Users can create own reports"
    ON deep_research_reports FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = deep_research_reports.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can update their own reports (ratings, feedback)
CREATE POLICY "Users can update own reports"
    ON deep_research_reports FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = deep_research_reports.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can soft delete their reports
CREATE POLICY "Users can delete own reports"
    ON deep_research_reports FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = deep_research_reports.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- =====================================================
-- WIDGET UPDATES POLICIES
-- =====================================================

-- Users can view their widget updates
CREATE POLICY "Users can view own widgets"
    ON widget_updates FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = widget_updates.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Only service/backend can create widgets
CREATE POLICY "Service can manage widgets"
    ON widget_updates FOR ALL
    USING (auth.role() = 'service_role');

-- =====================================================
-- CHAT SESSIONS POLICIES
-- =====================================================

-- Users can view their own chat sessions
CREATE POLICY "Users can view own chat sessions"
    ON chat_sessions FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = chat_sessions.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can create chat sessions
CREATE POLICY "Users can create own chat sessions"
    ON chat_sessions FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = chat_sessions.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can update their chat sessions
CREATE POLICY "Users can update own chat sessions"
    ON chat_sessions FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = chat_sessions.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can delete their chat sessions
CREATE POLICY "Users can delete own chat sessions"
    ON chat_sessions FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = chat_sessions.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- =====================================================
-- CHAT MESSAGES POLICIES
-- =====================================================

-- Users can view messages in their sessions
CREATE POLICY "Users can view own chat messages"
    ON chat_messages FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM chat_sessions cs
            JOIN users u ON cs.user_id = u.id
            WHERE cs.id = chat_messages.session_id
            AND u.auth_user_id = auth.uid()
        )
    );

-- Users can add messages to their sessions
CREATE POLICY "Users can create own chat messages"
    ON chat_messages FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM chat_sessions cs
            JOIN users u ON cs.user_id = u.id
            WHERE cs.id = chat_messages.session_id
            AND u.auth_user_id = auth.uid()
        )
    );

-- Service role can insert AI responses
CREATE POLICY "Service can create AI messages"
    ON chat_messages FOR INSERT
    WITH CHECK (auth.role() = 'service_role');

-- =====================================================
-- NOTIFICATIONS POLICIES
-- =====================================================

-- Users can view their own notifications
CREATE POLICY "Users can view own notifications"
    ON notifications FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = notifications.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can mark notifications as read
CREATE POLICY "Users can update own notifications"
    ON notifications FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = notifications.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Users can delete their notifications
CREATE POLICY "Users can delete own notifications"
    ON notifications FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = notifications.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Service role can create notifications
CREATE POLICY "Service can create notifications"
    ON notifications FOR INSERT
    WITH CHECK (auth.role() = 'service_role');

-- =====================================================
-- USER ACTIVITY LOG POLICIES
-- =====================================================

-- Users can view their own activity
CREATE POLICY "Users can view own activity"
    ON user_activity_log FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = user_activity_log.user_id
            AND users.auth_user_id = auth.uid()
        )
    );

-- Service role can log activity
CREATE POLICY "Service can log activity"
    ON user_activity_log FOR INSERT
    WITH CHECK (auth.role() = 'service_role');

-- =====================================================
-- PUBLIC TABLE POLICIES (Read-Only)
-- =====================================================

-- Allow everyone to read stocks
CREATE POLICY "Anyone can view stocks"
    ON stocks FOR SELECT
    USING (true);

-- Allow everyone to read news
CREATE POLICY "Anyone can view news"
    ON news_articles FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view news-stock relationships"
    ON news_stocks FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view breaking news"
    ON breaking_news FOR SELECT
    USING (true);

-- Allow everyone to read financial data
CREATE POLICY "Anyone can view fundamentals"
    ON company_fundamentals FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view earnings"
    ON earnings FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view stock prices"
    ON stock_prices FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view analyst forecasts"
    ON analyst_forecasts FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view company insights"
    ON company_insights FOR SELECT
    USING (true);

-- Allow everyone to read educational content
CREATE POLICY "Anyone can view educational content"
    ON educational_content FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view content chunks"
    ON content_chunks FOR SELECT
    USING (true);

CREATE POLICY "Anyone can view article chunks"
    ON article_chunks FOR SELECT
    USING (true);

-- =====================================================
-- SERVICE ROLE POLICIES (Backend Operations)
-- =====================================================

-- Service role can manage public data
CREATE POLICY "Service can manage stocks"
    ON stocks FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage news"
    ON news_articles FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage news-stocks"
    ON news_stocks FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage breaking news"
    ON breaking_news FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage fundamentals"
    ON company_fundamentals FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage earnings"
    ON earnings FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage stock prices"
    ON stock_prices FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage forecasts"
    ON analyst_forecasts FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage insights"
    ON company_insights FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage educational content"
    ON educational_content FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage content chunks"
    ON content_chunks FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage article chunks"
    ON article_chunks FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can manage background jobs"
    ON background_jobs FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can view API logs"
    ON api_usage_logs FOR SELECT
    USING (auth.role() = 'service_role');

CREATE POLICY "Service can create API logs"
    ON api_usage_logs FOR INSERT
    WITH CHECK (auth.role() = 'service_role');

-- =====================================================
-- HELPER FUNCTIONS FOR RLS
-- =====================================================

-- Function to check if user has permission for stock operations
CREATE OR REPLACE FUNCTION user_owns_stock_data(p_user_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM users
        WHERE id = p_user_id
        AND auth_user_id = auth.uid()
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to check user tier
CREATE OR REPLACE FUNCTION get_user_tier()
RETURNS user_tier AS $$
BEGIN
    RETURN (
        SELECT tier FROM users
        WHERE auth_user_id = auth.uid()
        LIMIT 1
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =====================================================
-- VERIFICATION
-- =====================================================

-- Verify RLS is enabled
DO $$
DECLARE
    r RECORD;
BEGIN
    RAISE NOTICE '=================================================';
    RAISE NOTICE 'RLS Status:';
    FOR r IN (
        SELECT tablename, rowsecurity
        FROM pg_tables
        WHERE schemaname = 'public'
        AND tablename IN (
            'users', 'watchlists', 'deep_research_reports',
            'widget_updates', 'chat_sessions', 'chat_messages',
            'notifications', 'user_activity_log'
        )
    )
    LOOP
        RAISE NOTICE '% : RLS %', r.tablename, 
            CASE WHEN r.rowsecurity THEN 'ENABLED ✓' ELSE 'DISABLED ✗' END;
    END LOOP;
    RAISE NOTICE '=================================================';
END $$;

-- Success message
DO $$
BEGIN
    RAISE NOTICE '=================================================';
    RAISE NOTICE 'RLS Policies created successfully!';
    RAISE NOTICE 'All user data is now protected by Row Level Security';
    RAISE NOTICE '=================================================';
END $$;
