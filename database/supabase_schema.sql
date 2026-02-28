-- =====================================================
-- Caydex - AI Value Investor App
-- Supabase Database Schema v3.0
-- Complete rebuild reverse-engineered from iOS frontend
-- Date: February 28, 2026
-- =====================================================
--
-- EXECUTION ORDER:
-- 1. Enable extensions in Supabase Dashboard (pgvector)
-- 2. Run this file (supabase_schema.sql)
-- 3. Run supabase_rls.sql
-- 4. Run supabase_vector_indexes.sql (after loading data)
--
-- =====================================================

-- =====================================================
-- PHASE 1: EXTENSIONS
-- =====================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =====================================================
-- PHASE 2: ENUM TYPES
-- =====================================================

CREATE TYPE user_tier AS ENUM ('free', 'pro', 'premium');
CREATE TYPE report_status AS ENUM ('pending', 'processing', 'completed', 'failed');
CREATE TYPE chat_message_role AS ENUM ('user', 'assistant', 'system');
CREATE TYPE whale_risk_profile AS ENUM ('conservative', 'moderate', 'aggressive', 'very_aggressive');
CREATE TYPE whale_category AS ENUM ('investors', 'institutions', 'politicians', 'crypto');
CREATE TYPE trade_action AS ENUM ('BOUGHT', 'SOLD');
CREATE TYPE trade_type AS ENUM ('New', 'Increased', 'Decreased', 'Closed');
CREATE TYPE lesson_status AS ENUM ('completed', 'upNext', 'notStarted');
CREATE TYPE lesson_level AS ENUM ('foundation', 'analysis', 'strategies', 'mastery');
CREATE TYPE bookmark_type AS ENUM ('book', 'lesson', 'article', 'report');
CREATE TYPE money_move_category AS ENUM ('blueprints', 'valueTraps', 'battles');
CREATE TYPE book_level AS ENUM ('Starter', 'Intermediate', 'Advanced');
CREATE TYPE news_sentiment AS ENUM ('bullish', 'bearish', 'neutral');
CREATE TYPE asset_type AS ENUM ('etf', 'index', 'crypto', 'commodity');

-- =====================================================
-- GROUP 1: USER MANAGEMENT
-- =====================================================
-- Source: AppState.swift -> UserProfile, CreditInfo, UserTier
-- Source: AuthService.swift -> AuthResponse
-- Source: ProfileViewModel.swift -> credit loading

-- users: Direct link to Supabase auth.users
-- The id IS the auth.users UUID (no separate auth_user_id column)
CREATE TABLE users (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    display_name TEXT,
    avatar_url  TEXT,
    tier        user_tier NOT NULL DEFAULT 'free',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_tier ON users(tier);

COMMENT ON TABLE users IS 'Core user profiles. id = auth.users.id (direct link).';
COMMENT ON COLUMN users.tier IS 'Subscription tier: free (default), pro, premium';

-- user_credits: Separated from users for clean credit management
-- Source: CreditInfo { total, used, remaining, resetsAt }
-- remaining is a GENERATED column = total - used (always consistent)
CREATE TABLE user_credits (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    total       INT NOT NULL DEFAULT 0,
    used        INT NOT NULL DEFAULT 0,
    remaining   INT GENERATED ALWAYS AS (total - used) STORED,
    resets_at   TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_user_credits_user ON user_credits(user_id);

COMMENT ON TABLE user_credits IS 'Per-user credit balance for AI research generation';
COMMENT ON COLUMN user_credits.remaining IS 'Auto-computed: total - used. Never set directly.';

-- =====================================================
-- GROUP 2: WATCHLIST
-- =====================================================
-- Source: AppState.swift -> WatchlistStock
-- Note: price/changePercent are real-time from API, NOT stored

CREATE TABLE watchlist_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker       TEXT NOT NULL,
    company_name TEXT NOT NULL,
    logo_url     TEXT,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, ticker)
);

CREATE INDEX idx_watchlist_user ON watchlist_items(user_id);
CREATE INDEX idx_watchlist_user_added ON watchlist_items(user_id, added_at DESC);

COMMENT ON TABLE watchlist_items IS 'User watchlist. Price data fetched live from FMP API.';

-- =====================================================
-- GROUP 3: AI RESEARCH INFRASTRUCTURE
-- =====================================================
-- Source: AnalysisPersona, InvestorPersona (HomeModels), ReportAgentPersona
-- Source: TaskPollingManager.swift -> ResearchReportDetail, ResearchStatusResponse
-- Source: ResearchModels.swift -> AnalysisReport

-- agent_personas: Configurable AI investor personalities
-- Populated by backend, fetched via getPersonas endpoint
CREATE TABLE agent_personas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    title           TEXT,
    tagline         TEXT,
    style           TEXT,
    description     TEXT,
    key_principles  JSONB,
    accent_color    TEXT,
    icon_name       TEXT,
    focus           TEXT,
    famous_quotes   JSONB,
    persona_prompt  TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_personas_key ON agent_personas(key);
CREATE INDEX idx_agent_personas_active ON agent_personas(is_active) WHERE is_active = true;

COMMENT ON TABLE agent_personas IS 'AI investor personas (Buffett, Wood, Lynch, Ackman, etc.) with system prompts';
COMMENT ON COLUMN agent_personas.key IS 'Snake_case identifier: warren_buffett, cathie_wood, etc.';
COMMENT ON COLUMN agent_personas.persona_prompt IS 'System prompt sent to LLM for report generation';

-- research_reports: AI-generated analysis reports
-- Serves as BOTH task queue (status/progress/current_step) AND content storage
-- Source: ResearchReportDetail (TaskPollingManager.swift)
CREATE TABLE research_reports (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker                  TEXT NOT NULL,
    company_name            TEXT NOT NULL,
    investor_persona        TEXT NOT NULL,

    -- Task queue fields (polling by iOS client)
    status                  report_status NOT NULL DEFAULT 'pending',
    progress                INT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    current_step            TEXT,
    error_message           TEXT,
    estimated_time_remaining INT,

    -- Report content (populated on completion)
    title                   TEXT,
    executive_summary       TEXT,
    investment_thesis       JSONB,
    pros                    JSONB,
    cons                    JSONB,
    moat_analysis           JSONB,
    valuation_analysis      JSONB,
    risk_assessment         JSONB,
    full_report             TEXT,
    key_takeaways           JSONB,
    action_recommendation   TEXT,

    -- Generation metrics
    generation_time_seconds INT,
    tokens_used             INT,

    -- User feedback
    user_rating             INT CHECK (user_rating BETWEEN 1 AND 5),
    user_feedback           TEXT,

    -- Timestamps
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX idx_reports_user ON research_reports(user_id, created_at DESC);
CREATE INDEX idx_reports_user_status ON research_reports(user_id, status);
CREATE INDEX idx_reports_ticker ON research_reports(ticker);
CREATE INDEX idx_reports_status_pending ON research_reports(status, created_at)
    WHERE status IN ('pending', 'processing');
CREATE INDEX idx_reports_persona ON research_reports(investor_persona);

COMMENT ON TABLE research_reports IS 'AI research reports. Dual-purpose: task queue + content store.';
COMMENT ON COLUMN research_reports.investment_thesis IS 'JSONB: {summary, key_drivers[], risks[], time_horizon, conviction_level}';
COMMENT ON COLUMN research_reports.moat_analysis IS 'JSONB: {moat_rating, moat_sources[], moat_sustainability, competitive_position, barriers_to_entry[]}';
COMMENT ON COLUMN research_reports.valuation_analysis IS 'JSONB: {valuation_rating, key_metrics{}, historical_context, margin_of_safety}';
COMMENT ON COLUMN research_reports.risk_assessment IS 'JSONB: {overall_risk, business_risks[], financial_risks[], market_risks[]}';

-- =====================================================
-- GROUP 4: CHAT SYSTEM
-- =====================================================
-- Source: ChatModels.swift -> ChatSession, ChatMessage, ChatCitation, ChatHistoryItem
-- Source: ChatConversationModels.swift -> RichChatMessage, rich content types

CREATE TABLE chat_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT,
    session_type    TEXT NOT NULL DEFAULT 'NORMAL',
    stock_id        TEXT,
    preview_message TEXT,
    message_count   INT NOT NULL DEFAULT 0,
    is_saved        BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_sessions_user ON chat_sessions(user_id, last_message_at DESC);
CREATE INDEX idx_chat_sessions_type ON chat_sessions(session_type);
CREATE INDEX idx_chat_sessions_saved ON chat_sessions(user_id, is_saved) WHERE is_saved = true;

COMMENT ON TABLE chat_sessions IS 'AI chat sessions. Types: BOOK, CONCEPT, STOCK, NORMAL, JOURNEY, REPORT';
COMMENT ON COLUMN chat_sessions.stock_id IS 'Ticker symbol if chat is about a specific stock';

CREATE TABLE chat_messages (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role         chat_message_role NOT NULL,
    content      TEXT NOT NULL,
    rich_content JSONB,
    citations    JSONB,
    tokens_used  INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at ASC);

COMMENT ON TABLE chat_messages IS 'Individual messages within a chat session';
COMMENT ON COLUMN chat_messages.rich_content IS 'JSONB array of typed blocks: text, sentimentAnalysis, stockPerformance, riskFactors, tip, bulletPoints';
COMMENT ON COLUMN chat_messages.citations IS 'JSONB array: [{source, title, url?}]';

-- =====================================================
-- GROUP 5: WHALE TRACKING
-- =====================================================
-- Source: WhaleProfileModels.swift -> WhaleProfile, WhaleHolding, WhaleTradeGroup, WhaleTrade
-- Source: TrackingModels.swift -> TrendingWhale, WhaleActivity

CREATE TABLE whales (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    title             TEXT,
    description       TEXT,
    avatar_url        TEXT,
    category          whale_category NOT NULL DEFAULT 'investors',
    risk_profile      whale_risk_profile,
    portfolio_value   NUMERIC,
    ytd_return        NUMERIC,
    followers_count   INT NOT NULL DEFAULT 0,
    behavior_summary  JSONB,
    sentiment_summary TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_whales_category ON whales(category);
CREATE INDEX idx_whales_name ON whales(name);

COMMENT ON TABLE whales IS 'Notable investors, institutions, and politicians tracked for trades';
COMMENT ON COLUMN whales.behavior_summary IS 'JSONB: {action, primaryFocus, secondaryAction, secondaryFocus}';

CREATE TABLE whale_sector_allocations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whale_id   UUID NOT NULL REFERENCES whales(id) ON DELETE CASCADE,
    sector     TEXT NOT NULL,
    allocation NUMERIC NOT NULL,

    UNIQUE(whale_id, sector)
);

CREATE INDEX idx_whale_sectors_whale ON whale_sector_allocations(whale_id);

CREATE TABLE whale_holdings (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whale_id       UUID NOT NULL REFERENCES whales(id) ON DELETE CASCADE,
    ticker         TEXT NOT NULL,
    company_name   TEXT NOT NULL,
    logo_url       TEXT,
    allocation     NUMERIC NOT NULL,
    change_percent NUMERIC,

    UNIQUE(whale_id, ticker)
);

CREATE INDEX idx_whale_holdings_whale ON whale_holdings(whale_id);
CREATE INDEX idx_whale_holdings_ticker ON whale_holdings(ticker);

CREATE TABLE whale_trade_groups (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whale_id    UUID NOT NULL REFERENCES whales(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    trade_count INT NOT NULL,
    net_action  TEXT NOT NULL,
    net_amount  NUMERIC NOT NULL,
    summary     TEXT,
    insights    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_whale_trade_groups_whale ON whale_trade_groups(whale_id, created_at DESC);

COMMENT ON COLUMN whale_trade_groups.insights IS 'JSONB array of insight strings';

CREATE TABLE whale_trades (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whale_id            UUID NOT NULL REFERENCES whales(id) ON DELETE CASCADE,
    trade_group_id      UUID REFERENCES whale_trade_groups(id) ON DELETE SET NULL,
    ticker              TEXT NOT NULL,
    company_name        TEXT NOT NULL,
    action              trade_action NOT NULL,
    trade_type          trade_type NOT NULL,
    amount              NUMERIC NOT NULL,
    previous_allocation NUMERIC,
    new_allocation      NUMERIC,
    date                TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_whale_trades_whale ON whale_trades(whale_id, created_at DESC);
CREATE INDEX idx_whale_trades_group ON whale_trades(trade_group_id);
CREATE INDEX idx_whale_trades_ticker ON whale_trades(ticker);

CREATE TABLE whale_follows (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    whale_id    UUID NOT NULL REFERENCES whales(id) ON DELETE CASCADE,
    followed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, whale_id)
);

CREATE INDEX idx_whale_follows_user ON whale_follows(user_id);
CREATE INDEX idx_whale_follows_whale ON whale_follows(whale_id);

-- =====================================================
-- GROUP 6: NEWS
-- =====================================================
-- Source: UpdatesModels.swift -> NewsArticle, NewsSource, NewsSentiment
-- Source: NewsDetailModels.swift -> NewsArticleDetail, KeyTakeaway

CREATE TABLE news_articles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    headline            TEXT NOT NULL,
    summary             TEXT,
    source_name         TEXT NOT NULL,
    source_logo_url     TEXT,
    source_is_verified  BOOLEAN NOT NULL DEFAULT false,
    sentiment           news_sentiment,
    published_at        TIMESTAMPTZ NOT NULL,
    thumbnail_url       TEXT,
    related_tickers     JSONB,
    category            TEXT,
    is_breaking         BOOLEAN NOT NULL DEFAULT false,
    article_url         TEXT,

    -- AI-enriched fields
    insight_summary     TEXT,
    insight_key_points  JSONB,
    key_takeaways       JSONB,
    read_time_minutes   INT,

    -- Deduplication
    external_id         TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(external_id, source_name)
);

CREATE INDEX idx_news_published ON news_articles(published_at DESC);
CREATE INDEX idx_news_sentiment ON news_articles(sentiment) WHERE sentiment IS NOT NULL;
CREATE INDEX idx_news_breaking ON news_articles(is_breaking, published_at DESC) WHERE is_breaking = true;
CREATE INDEX idx_news_source ON news_articles(source_name);
CREATE INDEX idx_news_category ON news_articles(category) WHERE category IS NOT NULL;
CREATE INDEX idx_news_related_tickers ON news_articles USING GIN(related_tickers jsonb_path_ops);

COMMENT ON TABLE news_articles IS 'Aggregated news articles with AI-enriched insights';
COMMENT ON COLUMN news_articles.related_tickers IS 'JSONB array of ticker strings: ["AAPL", "MSFT"]';
COMMENT ON COLUMN news_articles.key_takeaways IS 'JSONB array: [{index, text}]';

-- =====================================================
-- GROUP 6b: AI-GENERATED ASSET SNAPSHOTS
-- =====================================================
-- Source: ETFDetailModels.swift -> ETFSnapshotPrompts (Gemini 2.0 Flash)
-- Source: IndexDetailModels.swift -> IndexSnapshotsData (template + generatedBy)
-- Source: CryptoDetailModels.swift -> CryptoSnapshotItem
-- Source: CommodityDetailModels.swift -> (future)
--
-- Polymorphic table: one row per (symbol, asset_type, snapshot_type) combo
-- Shared across all users, refreshed weekly by Gemini backend job

CREATE TABLE asset_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          TEXT NOT NULL,
    asset_type      asset_type NOT NULL,
    snapshot_type   TEXT NOT NULL,
    title           TEXT,
    content         JSONB NOT NULL,
    generated_by    TEXT,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(symbol, asset_type, snapshot_type)
);

CREATE INDEX idx_snapshots_symbol ON asset_snapshots(symbol, asset_type);
CREATE INDEX idx_snapshots_expires ON asset_snapshots(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX idx_snapshots_type ON asset_snapshots(asset_type, snapshot_type);

COMMENT ON TABLE asset_snapshots IS 'AI-generated analysis snapshots for ETFs, indexes, crypto, commodities. Refreshed weekly by Gemini.';
COMMENT ON COLUMN asset_snapshots.snapshot_type IS 'e.g. identity_rating, strategy, net_yield, holdings_risk (ETF); valuation, sector_performance, macro_forecast (Index)';
COMMENT ON COLUMN asset_snapshots.content IS 'JSONB: full snapshot payload. Schema varies by asset_type + snapshot_type.';
COMMENT ON COLUMN asset_snapshots.generated_by IS 'Model identifier, e.g. "Gemini 2.0 Flash"';

-- =====================================================
-- GROUP 7: EDUCATION & LEARNING
-- =====================================================
-- Source: LearnModels.swift -> EducationBook, BookLevel, JourneyTrack
-- Source: BookCoreDetailModels.swift -> CoreChapterContent, CoreChapterSection
-- Source: InvestorPathModels.swift -> Lesson, LevelProgress, LessonStoryContent
-- Source: MoneyMoveArticleModels.swift -> MoneyMoveArticle, ArticleSection

-- books: Investment education books
CREATE TABLE books (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title            TEXT NOT NULL,
    author           TEXT NOT NULL,
    description      TEXT,
    cover_image_name TEXT,
    page_count       INT,
    published_year   INT,
    rating           NUMERIC,
    level            book_level,
    is_most_read     BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_books_level ON books(level);
CREATE INDEX idx_books_rating ON books(rating DESC);

COMMENT ON TABLE books IS 'Investment education books available in the Learn section';

-- book_chapters: Chapter content for each book
CREATE TABLE book_chapters (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id                UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_number         INT NOT NULL,
    chapter_title          TEXT NOT NULL,
    sections               JSONB NOT NULL,
    audio_duration_seconds INT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(book_id, chapter_number)
);

CREATE INDEX idx_book_chapters_book ON book_chapters(book_id, chapter_number);

COMMENT ON COLUMN book_chapters.sections IS 'JSONB array: [{title, content, iconName?}]';

-- lessons: Investor journey lessons (Foundation -> Analysis -> Strategies -> Mastery)
CREATE TABLE lessons (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title            TEXT NOT NULL,
    description      TEXT,
    duration_minutes INT,
    category         TEXT NOT NULL DEFAULT 'standard',
    level            lesson_level NOT NULL,
    sort_order       INT NOT NULL DEFAULT 0,
    story_content    JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_lessons_level ON lessons(level, sort_order);
CREATE INDEX idx_lessons_category ON lessons(category);

COMMENT ON TABLE lessons IS 'Investor journey lessons organized by level';
COMMENT ON COLUMN lessons.story_content IS 'JSONB: {lessonLabel, lessonNumber, totalLessonsInLevel, estimatedMinutes, cards[]}';

-- user_lesson_progress: Tracks per-user lesson completion
CREATE TABLE user_lesson_progress (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lesson_id    UUID NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    status       lesson_status NOT NULL DEFAULT 'notStarted',
    completed_at TIMESTAMPTZ,

    UNIQUE(user_id, lesson_id)
);

CREATE INDEX idx_lesson_progress_user ON user_lesson_progress(user_id);
CREATE INDEX idx_lesson_progress_status ON user_lesson_progress(user_id, status);

-- money_move_articles: Case study articles (Blueprints, Value Traps, Battles)
CREATE TABLE money_move_articles (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title              TEXT NOT NULL,
    subtitle           TEXT,
    category           money_move_category NOT NULL,
    author_name        TEXT,
    author_credentials TEXT,
    author_avatar_name TEXT,
    published_at       TIMESTAMPTZ,
    read_time_minutes  INT,
    sections           JSONB,
    statistics         JSONB,
    related_articles   JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_money_moves_category ON money_move_articles(category);

COMMENT ON TABLE money_move_articles IS 'Investment case studies: blueprints, value traps, and battles';
COMMENT ON COLUMN money_move_articles.sections IS 'JSONB array: [{type, title?, content?, items?[], imageURL?}]';

-- user_bookmarks: Polymorphic bookmarks across content types
CREATE TABLE user_bookmarks (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bookmarkable_type bookmark_type NOT NULL,
    bookmarkable_id   UUID NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, bookmarkable_type, bookmarkable_id)
);

CREATE INDEX idx_bookmarks_user ON user_bookmarks(user_id);
CREATE INDEX idx_bookmarks_user_type ON user_bookmarks(user_id, bookmarkable_type);
CREATE INDEX idx_bookmarks_target ON user_bookmarks(bookmarkable_type, bookmarkable_id);

COMMENT ON TABLE user_bookmarks IS 'Polymorphic bookmarks: book, lesson, article, or report';

-- =====================================================
-- GROUP 8: RAG VECTOR STORAGE (pgvector)
-- =====================================================
-- Source: RAG chat system for books, articles, and SEC filings
-- Embedding dimension: 1536 (OpenAI text-embedding-ada-002 / text-embedding-3-small)

-- book_chunks: Vectorized book content for semantic search
CREATE TABLE book_chunks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id        UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_number INT,
    chunk_index    INT NOT NULL,
    chunk_text     TEXT NOT NULL,
    embedding      vector(1536),
    section_title  TEXT,
    token_count    INT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(book_id, chunk_index)
);

CREATE INDEX idx_book_chunks_book ON book_chunks(book_id);

-- article_chunks: Vectorized article/news content
CREATE TABLE article_chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id    UUID NOT NULL,
    chunk_index   INT NOT NULL,
    chunk_text    TEXT NOT NULL,
    embedding     vector(1536),
    section_title TEXT,
    token_count   INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(article_id, chunk_index)
);

CREATE INDEX idx_article_chunks_article ON article_chunks(article_id);

-- company_filing_chunks: Vectorized SEC filings (10-K, 10-Q)
CREATE TABLE company_filing_chunks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker         TEXT NOT NULL,
    filing_type    TEXT NOT NULL,
    fiscal_year    INT,
    fiscal_quarter INT,
    chunk_index    INT NOT NULL,
    chunk_text     TEXT NOT NULL,
    embedding      vector(1536),
    section_title  TEXT,
    token_count    INT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_filing_chunks_unique
    ON company_filing_chunks(ticker, filing_type, fiscal_year, COALESCE(fiscal_quarter, 0), chunk_index);
CREATE INDEX idx_filing_chunks_ticker ON company_filing_chunks(ticker);
CREATE INDEX idx_filing_chunks_filing ON company_filing_chunks(ticker, filing_type, fiscal_year);

COMMENT ON TABLE company_filing_chunks IS 'Vectorized SEC filing chunks for RAG-based company research';

-- =====================================================
-- GROUP 9: USER PREFERENCES
-- =====================================================
-- Source: InvestorPathModels.swift -> StudySchedule

CREATE TABLE user_study_schedules (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    daily_reminder_enabled BOOLEAN NOT NULL DEFAULT false,
    morning_session_time   TEXT,
    review_time            TEXT,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_study_schedules_user ON user_study_schedules(user_id);

COMMENT ON TABLE user_study_schedules IS 'User learning schedule preferences';

-- =====================================================
-- FUNCTIONS
-- =====================================================

-- Auto-update updated_at on row modification
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Auto-increment chat_sessions.message_count on new message
CREATE OR REPLACE FUNCTION increment_chat_message_count()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE chat_sessions
    SET message_count = message_count + 1,
        last_message_at = now()
    WHERE id = NEW.session_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Auto-create user_credits row when a new user is inserted
CREATE OR REPLACE FUNCTION create_user_credits()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO user_credits (user_id, total, used)
    VALUES (NEW.id, CASE NEW.tier
        WHEN 'free' THEN 3
        WHEN 'pro' THEN 25
        WHEN 'premium' THEN 100
    END, 0);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Auto-update whale followers_count on follow/unfollow
CREATE OR REPLACE FUNCTION update_whale_followers_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE whales SET followers_count = followers_count + 1
        WHERE id = NEW.whale_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE whales SET followers_count = followers_count - 1
        WHERE id = OLD.whale_id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Auto-create user profile from auth.users on signup
-- This function is called by a Supabase Auth trigger
CREATE OR REPLACE FUNCTION handle_new_auth_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.users (id, email, display_name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'display_name', NEW.raw_user_meta_data->>'full_name', split_part(NEW.email, '@', 1)),
        NEW.raw_user_meta_data->>'avatar_url'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =====================================================
-- TRIGGERS
-- =====================================================

-- updated_at triggers
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_user_credits_updated_at
    BEFORE UPDATE ON user_credits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_agent_personas_updated_at
    BEFORE UPDATE ON agent_personas
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_whales_updated_at
    BEFORE UPDATE ON whales
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_study_schedules_updated_at
    BEFORE UPDATE ON user_study_schedules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Chat message count trigger
CREATE TRIGGER trg_chat_message_count
    AFTER INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION increment_chat_message_count();

-- Auto-create credits on user insert
CREATE TRIGGER trg_create_user_credits
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION create_user_credits();

-- Whale followers count triggers
CREATE TRIGGER trg_whale_follow_increment
    AFTER INSERT ON whale_follows
    FOR EACH ROW EXECUTE FUNCTION update_whale_followers_count();

CREATE TRIGGER trg_whale_follow_decrement
    AFTER DELETE ON whale_follows
    FOR EACH ROW EXECUTE FUNCTION update_whale_followers_count();

-- Auth trigger: auto-create user profile on Supabase Auth signup
-- NOTE: This trigger is on auth.users, requires SECURITY DEFINER
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_auth_user();

-- =====================================================
-- SEED: Default Agent Personas
-- =====================================================

INSERT INTO agent_personas (key, name, title, tagline, style, description, icon_name, accent_color, focus, is_active)
VALUES
    ('warren_buffett', 'Warren Buffett', 'Value Investing Legend', 'Long-term value with a margin of safety', 'Value investing with a focus on long-term compounding', 'Analyzes companies through the lens of intrinsic value, competitive moats, and management quality.', 'person.fill', '1B4332', 'Intrinsic value & moats', true),
    ('cathie_wood', 'Cathie Wood', 'Disruptive Innovation Pioneer', 'Exponential growth through disruption', 'Growth investing focused on disruptive innovation', 'Focuses on companies at the forefront of technological innovation and disruption.', 'sparkles', '6366F1', 'Disruptive innovation', true),
    ('peter_lynch', 'Peter Lynch', 'The People''s Investor', 'Invest in what you know', 'Growth at a reasonable price (GARP)', 'Combines fundamental analysis with practical, everyday observations about companies.', 'chart.line.uptrend.xyaxis', '2563EB', 'GARP investing', true),
    ('bill_ackman', 'Bill Ackman', 'Activist Investor', 'Concentrated bets with conviction', 'Activist investing with deep fundamental analysis', 'Takes concentrated positions and advocates for operational and strategic changes.', 'bolt.fill', 'DC2626', 'Activist catalysts', true),
    ('charlie_munger', 'Charlie Munger', 'The Sage of Reason', 'Invert, always invert', 'Mental models and multidisciplinary thinking', 'Applies mental models from multiple disciplines to identify wonderful companies at fair prices.', 'brain.head.profile', '92400E', 'Mental models', true),
    ('benjamin_graham', 'Benjamin Graham', 'Father of Value Investing', 'Margin of safety above all', 'Deep value investing with strict criteria', 'The original value investor. Focuses on quantitative screens, margin of safety, and balance sheet strength.', 'book.fill', '1E3A5F', 'Deep value', true)
ON CONFLICT (key) DO NOTHING;

-- =====================================================
-- VERIFICATION
-- =====================================================

DO $$
DECLARE
    tbl_count INT;
BEGIN
    SELECT COUNT(*) INTO tbl_count
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';

    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Caydex Schema v3.0 - Created Successfully';
    RAISE NOTICE 'Total tables: %', tbl_count;
    RAISE NOTICE '';
    RAISE NOTICE 'Next steps:';
    RAISE NOTICE '1. Run supabase_rls.sql for Row Level Security';
    RAISE NOTICE '2. Run supabase_vector_indexes.sql after loading data';
    RAISE NOTICE '=================================================';
END $$;
