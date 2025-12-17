-- =====================================================
-- AI Value Investor App - Supabase Database Schema
-- Version: 2.0 (Supabase Optimized)
-- Date: December 16, 2025
-- =====================================================

-- IMPORTANT: Before running this script
-- 1. Go to Supabase Dashboard > Database > Extensions
-- 2. Enable these extensions:
--    - uuid-ossp
--    - pgvector
-- 3. Then run this script

-- =====================================================
-- ENUM TYPES
-- =====================================================

DO $$ BEGIN
    CREATE TYPE user_tier AS ENUM ('free', 'pro', 'premium');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE sentiment_type AS ENUM ('bullish', 'bearish', 'neutral');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE report_status AS ENUM ('pending', 'processing', 'completed', 'failed');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE investor_persona AS ENUM ('buffett', 'ackman', 'munger', 'lynch', 'graham');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE content_type AS ENUM ('book', 'article');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- =====================================================
-- USER MANAGEMENT
-- =====================================================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Link to Supabase Auth
    auth_user_id UUID UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255),
    tier user_tier DEFAULT 'free' NOT NULL,
    tier_start_date TIMESTAMP WITH TIME ZONE,
    tier_expiry_date TIMESTAMP WITH TIME ZONE,
    
    -- Usage limits
    monthly_deep_research_used INTEGER DEFAULT 0,
    monthly_deep_research_limit INTEGER DEFAULT 1,
    monthly_research_reset_at TIMESTAMP WITH TIME ZONE DEFAULT date_trunc('month', NOW() + INTERVAL '1 month'),
    
    -- Preferences
    preferred_timezone VARCHAR(50) DEFAULT 'America/New_York',
    notification_preferences JSONB DEFAULT '{"email": true, "push": true}'::jsonb,
    onboarding_completed BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login_at TIMESTAMP WITH TIME ZONE,
    
    -- Soft delete
    deleted_at TIMESTAMP WITH TIME ZONE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_tier ON users(tier);
CREATE INDEX IF NOT EXISTS idx_users_auth_user_id ON users(auth_user_id);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(deleted_at) WHERE deleted_at IS NULL;

-- Comments
COMMENT ON TABLE users IS 'Core user accounts with tier management and usage tracking';
COMMENT ON COLUMN users.auth_user_id IS 'Links to Supabase auth.users table';
COMMENT ON COLUMN users.monthly_deep_research_limit IS 'Free: 1, Pro: 10, Premium: -1 (unlimited)';

-- =====================================================
-- STOCKS & COMPANIES
-- =====================================================

CREATE TABLE IF NOT EXISTS stocks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker VARCHAR(10) UNIQUE NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    exchange VARCHAR(50),
    sector VARCHAR(100),
    industry VARCHAR(100),
    
    -- Metadata
    market_cap NUMERIC,
    description TEXT,
    website VARCHAR(255),
    logo_url VARCHAR(500),
    
    -- Search optimization
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(ticker, '') || ' ' || coalesce(company_name, '') || ' ' || coalesce(sector, ''))
    ) STORED,
    
    -- Data freshness
    last_data_update TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT TRUE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_stocks_ticker ON stocks(ticker);
CREATE INDEX IF NOT EXISTS idx_stocks_sector ON stocks(sector);
CREATE INDEX IF NOT EXISTS idx_stocks_industry ON stocks(industry);
CREATE INDEX IF NOT EXISTS idx_stocks_search_vector ON stocks USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_stocks_is_active ON stocks(is_active) WHERE is_active = TRUE;

COMMENT ON TABLE stocks IS 'Master list of stocks/companies that can be tracked';

-- =====================================================
-- USER WATCHLISTS
-- =====================================================

CREATE TABLE IF NOT EXISTS watchlists (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    -- User preferences for this stock
    alert_on_news BOOLEAN DEFAULT TRUE,
    alert_threshold_percentage NUMERIC(5,2),
    custom_notes TEXT,
    
    -- Position info (optional)
    position_size NUMERIC,
    average_cost NUMERIC,
    
    added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_viewed_at TIMESTAMP WITH TIME ZONE,
    
    UNIQUE(user_id, stock_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_watchlists_user ON watchlists(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlists_stock ON watchlists(stock_id);
CREATE INDEX IF NOT EXISTS idx_watchlists_added_at ON watchlists(added_at DESC);

COMMENT ON TABLE watchlists IS 'User-specific stock watchlists with alert preferences';

-- =====================================================
-- NEWS MANAGEMENT
-- =====================================================

CREATE TABLE IF NOT EXISTS news_articles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Source information
    source_name VARCHAR(100),
    source_url TEXT,
    external_id VARCHAR(255),
    
    -- Content
    title TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    image_url TEXT,
    
    -- Classification
    sentiment sentiment_type,
    relevance_score NUMERIC(3,2),
    
    -- Metadata
    author VARCHAR(255),
    published_at TIMESTAMP WITH TIME ZONE NOT NULL,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- AI Processing
    ai_summary TEXT,
    ai_summary_bullets JSONB,
    ai_processed BOOLEAN DEFAULT FALSE,
    ai_processed_at TIMESTAMP WITH TIME ZONE,
    ai_model_version VARCHAR(50),
    
    -- Search optimization
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(ai_summary, '') || ' ' || coalesce(content, ''))
    ) STORED,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(external_id, source_name)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news_articles(sentiment) WHERE sentiment IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_news_ai_processed ON news_articles(ai_processed, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_search_vector ON news_articles USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_news_source_name ON news_articles(source_name);

COMMENT ON TABLE news_articles IS 'Aggregated and processed news articles';

-- =====================================================
-- NEWS-STOCK RELATIONSHIPS
-- =====================================================

CREATE TABLE IF NOT EXISTS news_stocks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    news_id UUID NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    relevance_score NUMERIC(3,2),
    mentioned_count INTEGER DEFAULT 1,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(news_id, stock_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_news_stocks_news ON news_stocks(news_id);
CREATE INDEX IF NOT EXISTS idx_news_stocks_stock ON news_stocks(stock_id);
CREATE INDEX IF NOT EXISTS idx_news_stocks_relevance ON news_stocks(relevance_score DESC);

-- =====================================================
-- BREAKING NEWS
-- =====================================================

CREATE TABLE IF NOT EXISTS breaking_news (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    news_id UUID NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    impact_score NUMERIC(3,2),
    is_price_moving BOOLEAN DEFAULT FALSE,
    price_change_percent NUMERIC(5,2),
    
    shown_in_feed BOOLEAN DEFAULT FALSE,
    expires_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_breaking_news_stock ON breaking_news(stock_id);
CREATE INDEX IF NOT EXISTS idx_breaking_news_expires ON breaking_news(expires_at);
CREATE INDEX IF NOT EXISTS idx_breaking_news_active ON breaking_news(stock_id, expires_at);

-- =====================================================
-- DEEP RESEARCH REPORTS
-- =====================================================

CREATE TABLE IF NOT EXISTS deep_research_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    investor_persona investor_persona NOT NULL,
    analysis_period VARCHAR(50),
    
    -- Report content
    title VARCHAR(500),
    executive_summary TEXT,
    
    pros JSONB,
    cons JSONB,
    moat_analysis TEXT,
    valuation_notes TEXT,
    risk_factors JSONB,
    investment_thesis TEXT,
    
    full_report TEXT,
    report_metadata JSONB,
    
    -- Status
    status report_status DEFAULT 'pending',
    error_message TEXT,
    
    -- Performance tracking
    generation_time_seconds INTEGER,
    tokens_used INTEGER,
    cost_usd NUMERIC(10,6),
    
    -- User interaction
    user_rating INTEGER CHECK (user_rating BETWEEN 1 AND 5),
    user_feedback TEXT,
    views_count INTEGER DEFAULT 0,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    deleted_at TIMESTAMP WITH TIME ZONE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_reports_user ON deep_research_reports(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_stock ON deep_research_reports(stock_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_status ON deep_research_reports(status) WHERE status != 'completed';
CREATE INDEX IF NOT EXISTS idx_reports_persona ON deep_research_reports(investor_persona);
CREATE INDEX IF NOT EXISTS idx_reports_active ON deep_research_reports(user_id, status) WHERE deleted_at IS NULL;

COMMENT ON TABLE deep_research_reports IS 'AI-generated company analysis reports';

-- =====================================================
-- WIDGET DATA
-- =====================================================

CREATE TABLE IF NOT EXISTS widget_updates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    headline TEXT NOT NULL,
    sentiment sentiment_type,
    emoji VARCHAR(10),
    daily_trend VARCHAR(100),
    
    market_summary TEXT,
    top_movers JSONB,
    
    update_type VARCHAR(50),
    scheduled_for TIMESTAMP WITH TIME ZONE,
    published_at TIMESTAMP WITH TIME ZONE,
    
    deep_link_url TEXT,
    linked_report_id UUID REFERENCES deep_research_reports(id),
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_widget_user ON widget_updates(user_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_widget_published ON widget_updates(published_at DESC) WHERE published_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_widget_scheduled ON widget_updates(scheduled_for) WHERE scheduled_for IS NOT NULL;

COMMENT ON TABLE widget_updates IS 'iOS home screen widget content updates';

-- =====================================================
-- EDUCATIONAL CONTENT (RAG System)
-- =====================================================

CREATE TABLE IF NOT EXISTS educational_content (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    type content_type NOT NULL,
    title VARCHAR(500) NOT NULL,
    author VARCHAR(255),
    publication_year INTEGER,
    
    source_url TEXT,
    isbn VARCHAR(20),
    
    full_text TEXT,
    summary TEXT,
    
    topics JSONB,
    difficulty_level VARCHAR(50),
    
    is_processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE,
    chunk_count INTEGER DEFAULT 0,
    
    -- Search optimization
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(author, '') || ' ' || coalesce(summary, ''))
    ) STORED,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_content_type ON educational_content(type);
CREATE INDEX IF NOT EXISTS idx_content_processed ON educational_content(is_processed);
CREATE INDEX IF NOT EXISTS idx_content_search_vector ON educational_content USING GIN(search_vector);

COMMENT ON TABLE educational_content IS 'Books and articles for RAG-based learning';

-- =====================================================
-- CONTENT CHUNKS (for RAG)
-- =====================================================

CREATE TABLE IF NOT EXISTS content_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    content_id UUID NOT NULL REFERENCES educational_content(id) ON DELETE CASCADE,
    
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    
    embedding vector(1536),
    
    page_number INTEGER,
    section_title VARCHAR(500),
    token_count INTEGER,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(content_id, chunk_index)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_chunks_content ON content_chunks(content_id);

COMMENT ON TABLE content_chunks IS 'Vectorized chunks of educational content for semantic search';
COMMENT ON COLUMN content_chunks.embedding IS 'Vector index will be created after data load';

-- =====================================================
-- ARTICLE CHUNKS (for RAG)
-- =====================================================

CREATE TABLE IF NOT EXISTS article_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    article_id UUID NOT NULL,
    
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    
    embedding vector(1536),
    
    section_title VARCHAR(500),
    token_count INTEGER,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(article_id, chunk_index)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_article_chunks_article ON article_chunks(article_id);

-- =====================================================
-- CHAT HISTORY
-- =====================================================

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    session_type VARCHAR(50) NOT NULL,
    
    content_id UUID REFERENCES educational_content(id),
    stock_id UUID REFERENCES stocks(id),
    
    title VARCHAR(500),
    is_active BOOLEAN DEFAULT TRUE,
    message_count INTEGER DEFAULT 0,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_message_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_sessions(user_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_active ON chat_sessions(user_id, is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_chat_session_type ON chat_sessions(session_type);

COMMENT ON TABLE chat_sessions IS 'User chat sessions with AI agents';

-- =====================================================
-- CHAT MESSAGES
-- =====================================================

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    
    citations JSONB,
    retrieved_chunks UUID[],
    
    tokens_used INTEGER,
    model_version VARCHAR(50),
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_messages_created ON chat_messages(created_at DESC);

-- =====================================================
-- COMPANY FUNDAMENTAL DATA
-- =====================================================

CREATE TABLE IF NOT EXISTS company_fundamentals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,
    period_end_date DATE,
    
    -- Income Statement
    revenue NUMERIC,
    gross_profit NUMERIC,
    operating_income NUMERIC,
    net_income NUMERIC,
    eps NUMERIC,
    ebitda NUMERIC,
    
    -- Balance Sheet
    total_assets NUMERIC,
    total_liabilities NUMERIC,
    shareholders_equity NUMERIC,
    total_debt NUMERIC,
    cash_and_equivalents NUMERIC,
    
    -- Cash Flow
    operating_cash_flow NUMERIC,
    free_cash_flow NUMERIC,
    capex NUMERIC,
    
    -- Ratios
    pe_ratio NUMERIC,
    pb_ratio NUMERIC,
    ps_ratio NUMERIC,
    debt_to_equity NUMERIC,
    current_ratio NUMERIC,
    roe NUMERIC,
    roa NUMERIC,
    gross_margin NUMERIC,
    operating_margin NUMERIC,
    profit_margin NUMERIC,
    
    raw_data JSONB,
    document_url TEXT,
    
    filing_date DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(stock_id, fiscal_year, fiscal_quarter)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_fundamentals_stock ON company_fundamentals(stock_id, fiscal_year DESC, fiscal_quarter DESC);
CREATE INDEX IF NOT EXISTS idx_fundamentals_period ON company_fundamentals(period_end_date DESC);

COMMENT ON TABLE company_fundamentals IS 'Financial fundamentals from 10-K/10-Q filings';

-- =====================================================
-- EARNINGS DATA
-- =====================================================

CREATE TABLE IF NOT EXISTS earnings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    earnings_date TIMESTAMP WITH TIME ZONE NOT NULL,
    fiscal_quarter INTEGER,
    fiscal_year INTEGER,
    
    eps_actual NUMERIC,
    eps_estimate NUMERIC,
    eps_surprise NUMERIC,
    eps_surprise_percent NUMERIC,
    
    revenue_actual NUMERIC,
    revenue_estimate NUMERIC,
    revenue_surprise NUMERIC,
    revenue_surprise_percent NUMERIC,
    
    has_occurred BOOLEAN DEFAULT FALSE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(stock_id, earnings_date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_earnings_stock ON earnings(stock_id, earnings_date DESC);
CREATE INDEX IF NOT EXISTS idx_earnings_upcoming ON earnings(earnings_date) WHERE has_occurred = FALSE;
CREATE INDEX IF NOT EXISTS idx_earnings_recent ON earnings(earnings_date DESC) WHERE has_occurred = TRUE;

COMMENT ON TABLE earnings IS 'Earnings reports and analyst estimates';

-- =====================================================
-- STOCK PRICE DATA
-- =====================================================

CREATE TABLE IF NOT EXISTS stock_prices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    price_date DATE NOT NULL,
    open_price NUMERIC NOT NULL,
    high_price NUMERIC NOT NULL,
    low_price NUMERIC NOT NULL,
    close_price NUMERIC NOT NULL,
    adjusted_close NUMERIC,
    volume BIGINT,
    
    daily_return NUMERIC,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(stock_id, price_date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_prices_stock_date ON stock_prices(stock_id, price_date DESC);
CREATE INDEX IF NOT EXISTS idx_prices_date ON stock_prices(price_date DESC);

COMMENT ON TABLE stock_prices IS 'Daily OHLCV price data for charts and analysis';

-- =====================================================
-- ANALYST FORECASTS
-- =====================================================

CREATE TABLE IF NOT EXISTS analyst_forecasts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    forecast_type VARCHAR(50),
    forecast_period VARCHAR(50),
    
    mean_estimate NUMERIC,
    median_estimate NUMERIC,
    high_estimate NUMERIC,
    low_estimate NUMERIC,
    number_of_analysts INTEGER,
    standard_deviation NUMERIC,
    
    as_of_date DATE NOT NULL,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(stock_id, forecast_type, forecast_period, as_of_date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_forecasts_stock ON analyst_forecasts(stock_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_forecasts_type ON analyst_forecasts(forecast_type, as_of_date DESC);

-- =====================================================
-- COMPANY INSIGHTS CACHE
-- =====================================================

CREATE TABLE IF NOT EXISTS company_insights (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stock_id UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    
    insight_type VARCHAR(100) NOT NULL,
    
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    
    sources JSONB,
    charts_data JSONB,
    
    cache_hit_count INTEGER DEFAULT 0,
    
    generated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(stock_id, insight_type)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_insights_stock ON company_insights(stock_id);
CREATE INDEX IF NOT EXISTS idx_insights_type ON company_insights(insight_type);
CREATE INDEX IF NOT EXISTS idx_insights_expires ON company_insights(expires_at);

COMMENT ON TABLE company_insights IS 'Cached AI-generated company insights';

-- =====================================================
-- API USAGE TRACKING
-- =====================================================

CREATE TABLE IF NOT EXISTS api_usage_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    
    api_provider VARCHAR(50) NOT NULL,
    endpoint VARCHAR(255),
    model_name VARCHAR(100),
    
    tokens_used INTEGER,
    cost_usd NUMERIC(10,6),
    
    request_type VARCHAR(100),
    status VARCHAR(20),
    response_time_ms INTEGER,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_api_logs_user ON api_usage_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_logs_provider ON api_usage_logs(api_provider, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_logs_date ON api_usage_logs(created_at DESC);

COMMENT ON TABLE api_usage_logs IS 'Track API usage and costs';

-- =====================================================
-- NOTIFICATION QUEUE
-- =====================================================

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    
    priority INTEGER DEFAULT 0,
    related_stock_id UUID REFERENCES stocks(id),
    deep_link TEXT,
    
    is_read BOOLEAN DEFAULT FALSE,
    is_sent BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMP WITH TIME ZONE,
    read_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(user_id, is_read) WHERE is_read = FALSE;
CREATE INDEX IF NOT EXISTS idx_notifications_unsent ON notifications(is_sent, expires_at) WHERE is_sent = FALSE;

-- =====================================================
-- BACKGROUND JOBS
-- =====================================================

CREATE TABLE IF NOT EXISTS background_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    job_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    
    parameters JSONB,
    result JSONB,
    
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error_message TEXT,
    
    scheduled_for TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    next_retry_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_jobs_status ON background_jobs(status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_jobs_pending ON background_jobs(scheduled_for) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_jobs_type ON background_jobs(job_type, created_at DESC);

-- =====================================================
-- USER ACTIVITY LOG
-- =====================================================

CREATE TABLE IF NOT EXISTS user_activity_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    activity_type VARCHAR(100) NOT NULL,
    activity_details JSONB,
    
    stock_id UUID REFERENCES stocks(id),
    session_id VARCHAR(255),
    ip_address INET,
    user_agent TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_activity_user ON user_activity_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_type ON user_activity_log(activity_type, created_at DESC);

COMMENT ON TABLE user_activity_log IS 'Track user activity for analytics and debugging';

-- =====================================================
-- FUNCTIONS AND TRIGGERS
-- =====================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply update trigger to relevant tables
DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_stocks_updated_at ON stocks;
CREATE TRIGGER update_stocks_updated_at BEFORE UPDATE ON stocks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_educational_content_updated_at ON educational_content;
CREATE TRIGGER update_educational_content_updated_at BEFORE UPDATE ON educational_content
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_earnings_updated_at ON earnings;
CREATE TRIGGER update_earnings_updated_at BEFORE UPDATE ON earnings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_analyst_forecasts_updated_at ON analyst_forecasts;
CREATE TRIGGER update_analyst_forecasts_updated_at BEFORE UPDATE ON analyst_forecasts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Function to increment message count
CREATE OR REPLACE FUNCTION increment_message_count()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE chat_sessions
    SET message_count = message_count + 1,
        last_message_at = NOW()
    WHERE id = NEW.session_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS increment_chat_message_count ON chat_messages;
CREATE TRIGGER increment_chat_message_count
    AFTER INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION increment_message_count();

-- Function to auto-reset monthly usage
CREATE OR REPLACE FUNCTION reset_monthly_usage()
RETURNS void AS $$
BEGIN
    UPDATE users
    SET 
        monthly_deep_research_used = 0,
        monthly_research_reset_at = date_trunc('month', NOW() + INTERVAL '1 month')
    WHERE monthly_research_reset_at < NOW();
END;
$$ LANGUAGE plpgsql;

-- Function to update chunk count
CREATE OR REPLACE FUNCTION update_chunk_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE educational_content
        SET chunk_count = chunk_count + 1
        WHERE id = NEW.content_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE educational_content
        SET chunk_count = chunk_count - 1
        WHERE id = OLD.content_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_content_chunk_count ON content_chunks;
CREATE TRIGGER update_content_chunk_count
    AFTER INSERT OR DELETE ON content_chunks
    FOR EACH ROW EXECUTE FUNCTION update_chunk_count();

-- =====================================================
-- MAINTENANCE PROCEDURES
-- =====================================================

-- Cleanup old news articles (keep 90 days)
CREATE OR REPLACE FUNCTION cleanup_old_news()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM news_articles
    WHERE published_at < NOW() - INTERVAL '90 days';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Cleanup old widget updates (keep 30 days)
CREATE OR REPLACE FUNCTION cleanup_old_widgets()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM widget_updates
    WHERE published_at < NOW() - INTERVAL '30 days';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Cleanup expired insights cache
CREATE OR REPLACE FUNCTION cleanup_expired_insights()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM company_insights
    WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Cleanup old API logs (keep 30 days)
CREATE OR REPLACE FUNCTION cleanup_old_api_logs()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM api_usage_logs
    WHERE created_at < NOW() - INTERVAL '30 days';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- INITIAL DATA & SETUP VERIFICATION
-- =====================================================

-- Verify extensions are enabled
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'uuid-ossp') THEN
        RAISE NOTICE 'WARNING: uuid-ossp extension is not enabled. Enable it in Supabase Dashboard.';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE NOTICE 'WARNING: pgvector extension is not enabled. Enable it in Supabase Dashboard.';
    END IF;
END $$;

-- Success message
DO $$
BEGIN
    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Schema created successfully!';
    RAISE NOTICE 'Next steps:';
    RAISE NOTICE '1. Run supabase_rls.sql to set up Row Level Security';
    RAISE NOTICE '2. Run supabase_vector_indexes.sql after loading data';
    RAISE NOTICE '3. Check Supabase Dashboard for any warnings';
    RAISE NOTICE '=================================================';
END $$;
