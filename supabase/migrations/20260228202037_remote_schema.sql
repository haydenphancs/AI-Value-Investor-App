drop extension if exists "pg_net";

create extension if not exists "vector" with schema "public";

create type "public"."asset_type" as enum ('etf', 'index', 'crypto', 'commodity');

create type "public"."book_level" as enum ('Starter', 'Intermediate', 'Advanced');

create type "public"."bookmark_type" as enum ('book', 'lesson', 'article', 'report');

create type "public"."chat_message_role" as enum ('user', 'assistant', 'system');

create type "public"."lesson_level" as enum ('foundation', 'analysis', 'strategies', 'mastery');

create type "public"."lesson_status" as enum ('completed', 'upNext', 'notStarted');

create type "public"."money_move_category" as enum ('blueprints', 'valueTraps', 'battles');

create type "public"."news_sentiment" as enum ('bullish', 'bearish', 'neutral');

create type "public"."report_status" as enum ('pending', 'processing', 'completed', 'failed');

create type "public"."trade_action" as enum ('BOUGHT', 'SOLD');

create type "public"."trade_type" as enum ('New', 'Increased', 'Decreased', 'Closed');

create type "public"."user_tier" as enum ('free', 'pro', 'premium');

create type "public"."whale_category" as enum ('investors', 'institutions', 'politicians', 'crypto');

create type "public"."whale_risk_profile" as enum ('conservative', 'moderate', 'aggressive', 'very_aggressive');


  create table "public"."agent_personas" (
    "id" uuid not null default gen_random_uuid(),
    "key" text not null,
    "name" text not null,
    "title" text,
    "tagline" text,
    "style" text,
    "description" text,
    "key_principles" jsonb,
    "accent_color" text,
    "icon_name" text,
    "focus" text,
    "famous_quotes" jsonb,
    "persona_prompt" text,
    "is_active" boolean not null default true,
    "created_at" timestamp with time zone not null default now(),
    "updated_at" timestamp with time zone not null default now()
      );


alter table "public"."agent_personas" enable row level security;


  create table "public"."article_chunks" (
    "id" uuid not null default gen_random_uuid(),
    "article_id" uuid not null,
    "chunk_index" integer not null,
    "chunk_text" text not null,
    "embedding" public.vector(1536),
    "section_title" text,
    "token_count" integer,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."article_chunks" enable row level security;


  create table "public"."asset_snapshots" (
    "id" uuid not null default gen_random_uuid(),
    "symbol" text not null,
    "asset_type" public.asset_type not null,
    "snapshot_type" text not null,
    "title" text,
    "content" jsonb not null,
    "generated_by" text,
    "generated_at" timestamp with time zone not null default now(),
    "expires_at" timestamp with time zone,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."asset_snapshots" enable row level security;


  create table "public"."book_chapters" (
    "id" uuid not null default gen_random_uuid(),
    "book_id" uuid not null,
    "chapter_number" integer not null,
    "chapter_title" text not null,
    "sections" jsonb not null,
    "audio_duration_seconds" integer,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."book_chapters" enable row level security;


  create table "public"."book_chunks" (
    "id" uuid not null default gen_random_uuid(),
    "book_id" uuid not null,
    "chapter_number" integer,
    "chunk_index" integer not null,
    "chunk_text" text not null,
    "embedding" public.vector(1536),
    "section_title" text,
    "token_count" integer,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."book_chunks" enable row level security;


  create table "public"."books" (
    "id" uuid not null default gen_random_uuid(),
    "title" text not null,
    "author" text not null,
    "description" text,
    "cover_image_name" text,
    "page_count" integer,
    "published_year" integer,
    "rating" numeric,
    "level" public.book_level,
    "is_most_read" boolean not null default false,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."books" enable row level security;


  create table "public"."chat_messages" (
    "id" uuid not null default gen_random_uuid(),
    "session_id" uuid not null,
    "role" public.chat_message_role not null,
    "content" text not null,
    "rich_content" jsonb,
    "citations" jsonb,
    "tokens_used" integer,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."chat_messages" enable row level security;


  create table "public"."chat_sessions" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "title" text,
    "session_type" text not null default 'NORMAL'::text,
    "stock_id" text,
    "preview_message" text,
    "message_count" integer not null default 0,
    "is_saved" boolean not null default false,
    "created_at" timestamp with time zone not null default now(),
    "last_message_at" timestamp with time zone not null default now()
      );


alter table "public"."chat_sessions" enable row level security;


  create table "public"."company_filing_chunks" (
    "id" uuid not null default gen_random_uuid(),
    "ticker" text not null,
    "filing_type" text not null,
    "fiscal_year" integer,
    "fiscal_quarter" integer,
    "chunk_index" integer not null,
    "chunk_text" text not null,
    "embedding" public.vector(1536),
    "section_title" text,
    "token_count" integer,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."company_filing_chunks" enable row level security;


  create table "public"."lessons" (
    "id" uuid not null default gen_random_uuid(),
    "title" text not null,
    "description" text,
    "duration_minutes" integer,
    "category" text not null default 'standard'::text,
    "level" public.lesson_level not null,
    "sort_order" integer not null default 0,
    "story_content" jsonb,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."lessons" enable row level security;


  create table "public"."money_move_articles" (
    "id" uuid not null default gen_random_uuid(),
    "title" text not null,
    "subtitle" text,
    "category" public.money_move_category not null,
    "author_name" text,
    "author_credentials" text,
    "author_avatar_name" text,
    "published_at" timestamp with time zone,
    "read_time_minutes" integer,
    "sections" jsonb,
    "statistics" jsonb,
    "related_articles" jsonb,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."money_move_articles" enable row level security;


  create table "public"."news_articles" (
    "id" uuid not null default gen_random_uuid(),
    "headline" text not null,
    "summary" text,
    "source_name" text not null,
    "source_logo_url" text,
    "source_is_verified" boolean not null default false,
    "sentiment" public.news_sentiment,
    "published_at" timestamp with time zone not null,
    "thumbnail_url" text,
    "related_tickers" jsonb,
    "category" text,
    "is_breaking" boolean not null default false,
    "article_url" text,
    "insight_summary" text,
    "insight_key_points" jsonb,
    "key_takeaways" jsonb,
    "read_time_minutes" integer,
    "external_id" text,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."news_articles" enable row level security;


  create table "public"."research_reports" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "ticker" text not null,
    "company_name" text not null,
    "investor_persona" text not null,
    "status" public.report_status not null default 'pending'::public.report_status,
    "progress" integer not null default 0,
    "current_step" text,
    "error_message" text,
    "estimated_time_remaining" integer,
    "title" text,
    "executive_summary" text,
    "investment_thesis" jsonb,
    "pros" jsonb,
    "cons" jsonb,
    "moat_analysis" jsonb,
    "valuation_analysis" jsonb,
    "risk_assessment" jsonb,
    "full_report" text,
    "key_takeaways" jsonb,
    "action_recommendation" text,
    "generation_time_seconds" integer,
    "tokens_used" integer,
    "user_rating" integer,
    "user_feedback" text,
    "created_at" timestamp with time zone not null default now(),
    "completed_at" timestamp with time zone
      );


alter table "public"."research_reports" enable row level security;


  create table "public"."user_bookmarks" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "bookmarkable_type" public.bookmark_type not null,
    "bookmarkable_id" uuid not null,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."user_bookmarks" enable row level security;


  create table "public"."user_credits" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "total" integer not null default 0,
    "used" integer not null default 0,
    "remaining" integer generated always as ((total - used)) stored,
    "resets_at" timestamp with time zone,
    "updated_at" timestamp with time zone not null default now()
      );


alter table "public"."user_credits" enable row level security;


  create table "public"."user_lesson_progress" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "lesson_id" uuid not null,
    "status" public.lesson_status not null default 'notStarted'::public.lesson_status,
    "completed_at" timestamp with time zone
      );


alter table "public"."user_lesson_progress" enable row level security;


  create table "public"."user_study_schedules" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "daily_reminder_enabled" boolean not null default false,
    "morning_session_time" text,
    "review_time" text,
    "updated_at" timestamp with time zone not null default now()
      );


alter table "public"."user_study_schedules" enable row level security;


  create table "public"."users" (
    "id" uuid not null,
    "email" text not null,
    "display_name" text,
    "avatar_url" text,
    "tier" public.user_tier not null default 'free'::public.user_tier,
    "created_at" timestamp with time zone not null default now(),
    "updated_at" timestamp with time zone not null default now()
      );


alter table "public"."users" enable row level security;


  create table "public"."watchlist_items" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "ticker" text not null,
    "company_name" text not null,
    "logo_url" text,
    "added_at" timestamp with time zone not null default now()
      );


alter table "public"."watchlist_items" enable row level security;


  create table "public"."whale_follows" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid not null,
    "whale_id" uuid not null,
    "followed_at" timestamp with time zone not null default now()
      );


alter table "public"."whale_follows" enable row level security;


  create table "public"."whale_holdings" (
    "id" uuid not null default gen_random_uuid(),
    "whale_id" uuid not null,
    "ticker" text not null,
    "company_name" text not null,
    "logo_url" text,
    "allocation" numeric not null,
    "change_percent" numeric
      );


alter table "public"."whale_holdings" enable row level security;


  create table "public"."whale_sector_allocations" (
    "id" uuid not null default gen_random_uuid(),
    "whale_id" uuid not null,
    "sector" text not null,
    "allocation" numeric not null
      );


alter table "public"."whale_sector_allocations" enable row level security;


  create table "public"."whale_trade_groups" (
    "id" uuid not null default gen_random_uuid(),
    "whale_id" uuid not null,
    "date" text not null,
    "trade_count" integer not null,
    "net_action" text not null,
    "net_amount" numeric not null,
    "summary" text,
    "insights" jsonb,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."whale_trade_groups" enable row level security;


  create table "public"."whale_trades" (
    "id" uuid not null default gen_random_uuid(),
    "whale_id" uuid not null,
    "trade_group_id" uuid,
    "ticker" text not null,
    "company_name" text not null,
    "action" public.trade_action not null,
    "trade_type" public.trade_type not null,
    "amount" numeric not null,
    "previous_allocation" numeric,
    "new_allocation" numeric,
    "date" text not null,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."whale_trades" enable row level security;


  create table "public"."whales" (
    "id" uuid not null default gen_random_uuid(),
    "name" text not null,
    "title" text,
    "description" text,
    "avatar_url" text,
    "category" public.whale_category not null default 'investors'::public.whale_category,
    "risk_profile" public.whale_risk_profile,
    "portfolio_value" numeric,
    "ytd_return" numeric,
    "followers_count" integer not null default 0,
    "behavior_summary" jsonb,
    "sentiment_summary" text,
    "created_at" timestamp with time zone not null default now(),
    "updated_at" timestamp with time zone not null default now()
      );


alter table "public"."whales" enable row level security;

CREATE UNIQUE INDEX agent_personas_key_key ON public.agent_personas USING btree (key);

CREATE UNIQUE INDEX agent_personas_pkey ON public.agent_personas USING btree (id);

CREATE UNIQUE INDEX article_chunks_article_id_chunk_index_key ON public.article_chunks USING btree (article_id, chunk_index);

CREATE UNIQUE INDEX article_chunks_pkey ON public.article_chunks USING btree (id);

CREATE UNIQUE INDEX asset_snapshots_pkey ON public.asset_snapshots USING btree (id);

CREATE UNIQUE INDEX asset_snapshots_symbol_asset_type_snapshot_type_key ON public.asset_snapshots USING btree (symbol, asset_type, snapshot_type);

CREATE UNIQUE INDEX book_chapters_book_id_chapter_number_key ON public.book_chapters USING btree (book_id, chapter_number);

CREATE UNIQUE INDEX book_chapters_pkey ON public.book_chapters USING btree (id);

CREATE UNIQUE INDEX book_chunks_book_id_chunk_index_key ON public.book_chunks USING btree (book_id, chunk_index);

CREATE UNIQUE INDEX book_chunks_pkey ON public.book_chunks USING btree (id);

CREATE UNIQUE INDEX books_pkey ON public.books USING btree (id);

CREATE UNIQUE INDEX chat_messages_pkey ON public.chat_messages USING btree (id);

CREATE UNIQUE INDEX chat_sessions_pkey ON public.chat_sessions USING btree (id);

CREATE UNIQUE INDEX company_filing_chunks_pkey ON public.company_filing_chunks USING btree (id);

CREATE INDEX idx_agent_personas_active ON public.agent_personas USING btree (is_active) WHERE (is_active = true);

CREATE INDEX idx_agent_personas_key ON public.agent_personas USING btree (key);

CREATE INDEX idx_article_chunks_article ON public.article_chunks USING btree (article_id);

CREATE INDEX idx_article_chunks_embedding_hnsw ON public.article_chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');

CREATE INDEX idx_book_chapters_book ON public.book_chapters USING btree (book_id, chapter_number);

CREATE INDEX idx_book_chunks_book ON public.book_chunks USING btree (book_id);

CREATE INDEX idx_book_chunks_embedding_hnsw ON public.book_chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');

CREATE INDEX idx_bookmarks_target ON public.user_bookmarks USING btree (bookmarkable_type, bookmarkable_id);

CREATE INDEX idx_bookmarks_user ON public.user_bookmarks USING btree (user_id);

CREATE INDEX idx_bookmarks_user_type ON public.user_bookmarks USING btree (user_id, bookmarkable_type);

CREATE INDEX idx_books_level ON public.books USING btree (level);

CREATE INDEX idx_books_rating ON public.books USING btree (rating DESC);

CREATE INDEX idx_chat_messages_session ON public.chat_messages USING btree (session_id, created_at);

CREATE INDEX idx_chat_sessions_saved ON public.chat_sessions USING btree (user_id, is_saved) WHERE (is_saved = true);

CREATE INDEX idx_chat_sessions_type ON public.chat_sessions USING btree (session_type);

CREATE INDEX idx_chat_sessions_user ON public.chat_sessions USING btree (user_id, last_message_at DESC);

CREATE INDEX idx_filing_chunks_embedding_hnsw ON public.company_filing_chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');

CREATE INDEX idx_filing_chunks_filing ON public.company_filing_chunks USING btree (ticker, filing_type, fiscal_year);

CREATE INDEX idx_filing_chunks_ticker ON public.company_filing_chunks USING btree (ticker);

CREATE UNIQUE INDEX idx_filing_chunks_unique ON public.company_filing_chunks USING btree (ticker, filing_type, fiscal_year, COALESCE(fiscal_quarter, 0), chunk_index);

CREATE INDEX idx_lesson_progress_status ON public.user_lesson_progress USING btree (user_id, status);

CREATE INDEX idx_lesson_progress_user ON public.user_lesson_progress USING btree (user_id);

CREATE INDEX idx_lessons_category ON public.lessons USING btree (category);

CREATE INDEX idx_lessons_level ON public.lessons USING btree (level, sort_order);

CREATE INDEX idx_money_moves_category ON public.money_move_articles USING btree (category);

CREATE INDEX idx_news_breaking ON public.news_articles USING btree (is_breaking, published_at DESC) WHERE (is_breaking = true);

CREATE INDEX idx_news_category ON public.news_articles USING btree (category) WHERE (category IS NOT NULL);

CREATE INDEX idx_news_published ON public.news_articles USING btree (published_at DESC);

CREATE INDEX idx_news_related_tickers ON public.news_articles USING gin (related_tickers jsonb_path_ops);

CREATE INDEX idx_news_sentiment ON public.news_articles USING btree (sentiment) WHERE (sentiment IS NOT NULL);

CREATE INDEX idx_news_source ON public.news_articles USING btree (source_name);

CREATE INDEX idx_reports_persona ON public.research_reports USING btree (investor_persona);

CREATE INDEX idx_reports_status_pending ON public.research_reports USING btree (status, created_at) WHERE (status = ANY (ARRAY['pending'::public.report_status, 'processing'::public.report_status]));

CREATE INDEX idx_reports_ticker ON public.research_reports USING btree (ticker);

CREATE INDEX idx_reports_user ON public.research_reports USING btree (user_id, created_at DESC);

CREATE INDEX idx_reports_user_status ON public.research_reports USING btree (user_id, status);

CREATE INDEX idx_snapshots_expires ON public.asset_snapshots USING btree (expires_at) WHERE (expires_at IS NOT NULL);

CREATE INDEX idx_snapshots_symbol ON public.asset_snapshots USING btree (symbol, asset_type);

CREATE INDEX idx_snapshots_type ON public.asset_snapshots USING btree (asset_type, snapshot_type);

CREATE INDEX idx_study_schedules_user ON public.user_study_schedules USING btree (user_id);

CREATE INDEX idx_user_credits_user ON public.user_credits USING btree (user_id);

CREATE INDEX idx_users_email ON public.users USING btree (email);

CREATE INDEX idx_users_tier ON public.users USING btree (tier);

CREATE INDEX idx_watchlist_user ON public.watchlist_items USING btree (user_id);

CREATE INDEX idx_watchlist_user_added ON public.watchlist_items USING btree (user_id, added_at DESC);

CREATE INDEX idx_whale_follows_user ON public.whale_follows USING btree (user_id);

CREATE INDEX idx_whale_follows_whale ON public.whale_follows USING btree (whale_id);

CREATE INDEX idx_whale_holdings_ticker ON public.whale_holdings USING btree (ticker);

CREATE INDEX idx_whale_holdings_whale ON public.whale_holdings USING btree (whale_id);

CREATE INDEX idx_whale_sectors_whale ON public.whale_sector_allocations USING btree (whale_id);

CREATE INDEX idx_whale_trade_groups_whale ON public.whale_trade_groups USING btree (whale_id, created_at DESC);

CREATE INDEX idx_whale_trades_group ON public.whale_trades USING btree (trade_group_id);

CREATE INDEX idx_whale_trades_ticker ON public.whale_trades USING btree (ticker);

CREATE INDEX idx_whale_trades_whale ON public.whale_trades USING btree (whale_id, created_at DESC);

CREATE INDEX idx_whales_category ON public.whales USING btree (category);

CREATE INDEX idx_whales_name ON public.whales USING btree (name);

CREATE UNIQUE INDEX lessons_pkey ON public.lessons USING btree (id);

CREATE UNIQUE INDEX money_move_articles_pkey ON public.money_move_articles USING btree (id);

CREATE UNIQUE INDEX news_articles_external_id_source_name_key ON public.news_articles USING btree (external_id, source_name);

CREATE UNIQUE INDEX news_articles_pkey ON public.news_articles USING btree (id);

CREATE UNIQUE INDEX research_reports_pkey ON public.research_reports USING btree (id);

CREATE UNIQUE INDEX user_bookmarks_pkey ON public.user_bookmarks USING btree (id);

CREATE UNIQUE INDEX user_bookmarks_user_id_bookmarkable_type_bookmarkable_id_key ON public.user_bookmarks USING btree (user_id, bookmarkable_type, bookmarkable_id);

CREATE UNIQUE INDEX user_credits_pkey ON public.user_credits USING btree (id);

CREATE UNIQUE INDEX user_credits_user_id_key ON public.user_credits USING btree (user_id);

CREATE UNIQUE INDEX user_lesson_progress_pkey ON public.user_lesson_progress USING btree (id);

CREATE UNIQUE INDEX user_lesson_progress_user_id_lesson_id_key ON public.user_lesson_progress USING btree (user_id, lesson_id);

CREATE UNIQUE INDEX user_study_schedules_pkey ON public.user_study_schedules USING btree (id);

CREATE UNIQUE INDEX user_study_schedules_user_id_key ON public.user_study_schedules USING btree (user_id);

CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id);

CREATE UNIQUE INDEX watchlist_items_pkey ON public.watchlist_items USING btree (id);

CREATE UNIQUE INDEX watchlist_items_user_id_ticker_key ON public.watchlist_items USING btree (user_id, ticker);

CREATE UNIQUE INDEX whale_follows_pkey ON public.whale_follows USING btree (id);

CREATE UNIQUE INDEX whale_follows_user_id_whale_id_key ON public.whale_follows USING btree (user_id, whale_id);

CREATE UNIQUE INDEX whale_holdings_pkey ON public.whale_holdings USING btree (id);

CREATE UNIQUE INDEX whale_holdings_whale_id_ticker_key ON public.whale_holdings USING btree (whale_id, ticker);

CREATE UNIQUE INDEX whale_sector_allocations_pkey ON public.whale_sector_allocations USING btree (id);

CREATE UNIQUE INDEX whale_sector_allocations_whale_id_sector_key ON public.whale_sector_allocations USING btree (whale_id, sector);

CREATE UNIQUE INDEX whale_trade_groups_pkey ON public.whale_trade_groups USING btree (id);

CREATE UNIQUE INDEX whale_trades_pkey ON public.whale_trades USING btree (id);

CREATE UNIQUE INDEX whales_pkey ON public.whales USING btree (id);

alter table "public"."agent_personas" add constraint "agent_personas_pkey" PRIMARY KEY using index "agent_personas_pkey";

alter table "public"."article_chunks" add constraint "article_chunks_pkey" PRIMARY KEY using index "article_chunks_pkey";

alter table "public"."asset_snapshots" add constraint "asset_snapshots_pkey" PRIMARY KEY using index "asset_snapshots_pkey";

alter table "public"."book_chapters" add constraint "book_chapters_pkey" PRIMARY KEY using index "book_chapters_pkey";

alter table "public"."book_chunks" add constraint "book_chunks_pkey" PRIMARY KEY using index "book_chunks_pkey";

alter table "public"."books" add constraint "books_pkey" PRIMARY KEY using index "books_pkey";

alter table "public"."chat_messages" add constraint "chat_messages_pkey" PRIMARY KEY using index "chat_messages_pkey";

alter table "public"."chat_sessions" add constraint "chat_sessions_pkey" PRIMARY KEY using index "chat_sessions_pkey";

alter table "public"."company_filing_chunks" add constraint "company_filing_chunks_pkey" PRIMARY KEY using index "company_filing_chunks_pkey";

alter table "public"."lessons" add constraint "lessons_pkey" PRIMARY KEY using index "lessons_pkey";

alter table "public"."money_move_articles" add constraint "money_move_articles_pkey" PRIMARY KEY using index "money_move_articles_pkey";

alter table "public"."news_articles" add constraint "news_articles_pkey" PRIMARY KEY using index "news_articles_pkey";

alter table "public"."research_reports" add constraint "research_reports_pkey" PRIMARY KEY using index "research_reports_pkey";

alter table "public"."user_bookmarks" add constraint "user_bookmarks_pkey" PRIMARY KEY using index "user_bookmarks_pkey";

alter table "public"."user_credits" add constraint "user_credits_pkey" PRIMARY KEY using index "user_credits_pkey";

alter table "public"."user_lesson_progress" add constraint "user_lesson_progress_pkey" PRIMARY KEY using index "user_lesson_progress_pkey";

alter table "public"."user_study_schedules" add constraint "user_study_schedules_pkey" PRIMARY KEY using index "user_study_schedules_pkey";

alter table "public"."users" add constraint "users_pkey" PRIMARY KEY using index "users_pkey";

alter table "public"."watchlist_items" add constraint "watchlist_items_pkey" PRIMARY KEY using index "watchlist_items_pkey";

alter table "public"."whale_follows" add constraint "whale_follows_pkey" PRIMARY KEY using index "whale_follows_pkey";

alter table "public"."whale_holdings" add constraint "whale_holdings_pkey" PRIMARY KEY using index "whale_holdings_pkey";

alter table "public"."whale_sector_allocations" add constraint "whale_sector_allocations_pkey" PRIMARY KEY using index "whale_sector_allocations_pkey";

alter table "public"."whale_trade_groups" add constraint "whale_trade_groups_pkey" PRIMARY KEY using index "whale_trade_groups_pkey";

alter table "public"."whale_trades" add constraint "whale_trades_pkey" PRIMARY KEY using index "whale_trades_pkey";

alter table "public"."whales" add constraint "whales_pkey" PRIMARY KEY using index "whales_pkey";

alter table "public"."agent_personas" add constraint "agent_personas_key_key" UNIQUE using index "agent_personas_key_key";

alter table "public"."article_chunks" add constraint "article_chunks_article_id_chunk_index_key" UNIQUE using index "article_chunks_article_id_chunk_index_key";

alter table "public"."asset_snapshots" add constraint "asset_snapshots_symbol_asset_type_snapshot_type_key" UNIQUE using index "asset_snapshots_symbol_asset_type_snapshot_type_key";

alter table "public"."book_chapters" add constraint "book_chapters_book_id_chapter_number_key" UNIQUE using index "book_chapters_book_id_chapter_number_key";

alter table "public"."book_chapters" add constraint "book_chapters_book_id_fkey" FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE not valid;

alter table "public"."book_chapters" validate constraint "book_chapters_book_id_fkey";

alter table "public"."book_chunks" add constraint "book_chunks_book_id_chunk_index_key" UNIQUE using index "book_chunks_book_id_chunk_index_key";

alter table "public"."book_chunks" add constraint "book_chunks_book_id_fkey" FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE not valid;

alter table "public"."book_chunks" validate constraint "book_chunks_book_id_fkey";

alter table "public"."chat_messages" add constraint "chat_messages_session_id_fkey" FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id) ON DELETE CASCADE not valid;

alter table "public"."chat_messages" validate constraint "chat_messages_session_id_fkey";

alter table "public"."chat_sessions" add constraint "chat_sessions_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."chat_sessions" validate constraint "chat_sessions_user_id_fkey";

alter table "public"."news_articles" add constraint "news_articles_external_id_source_name_key" UNIQUE using index "news_articles_external_id_source_name_key";

alter table "public"."research_reports" add constraint "research_reports_progress_check" CHECK (((progress >= 0) AND (progress <= 100))) not valid;

alter table "public"."research_reports" validate constraint "research_reports_progress_check";

alter table "public"."research_reports" add constraint "research_reports_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."research_reports" validate constraint "research_reports_user_id_fkey";

alter table "public"."research_reports" add constraint "research_reports_user_rating_check" CHECK (((user_rating >= 1) AND (user_rating <= 5))) not valid;

alter table "public"."research_reports" validate constraint "research_reports_user_rating_check";

alter table "public"."user_bookmarks" add constraint "user_bookmarks_user_id_bookmarkable_type_bookmarkable_id_key" UNIQUE using index "user_bookmarks_user_id_bookmarkable_type_bookmarkable_id_key";

alter table "public"."user_bookmarks" add constraint "user_bookmarks_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."user_bookmarks" validate constraint "user_bookmarks_user_id_fkey";

alter table "public"."user_credits" add constraint "user_credits_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."user_credits" validate constraint "user_credits_user_id_fkey";

alter table "public"."user_credits" add constraint "user_credits_user_id_key" UNIQUE using index "user_credits_user_id_key";

alter table "public"."user_lesson_progress" add constraint "user_lesson_progress_lesson_id_fkey" FOREIGN KEY (lesson_id) REFERENCES public.lessons(id) ON DELETE CASCADE not valid;

alter table "public"."user_lesson_progress" validate constraint "user_lesson_progress_lesson_id_fkey";

alter table "public"."user_lesson_progress" add constraint "user_lesson_progress_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."user_lesson_progress" validate constraint "user_lesson_progress_user_id_fkey";

alter table "public"."user_lesson_progress" add constraint "user_lesson_progress_user_id_lesson_id_key" UNIQUE using index "user_lesson_progress_user_id_lesson_id_key";

alter table "public"."user_study_schedules" add constraint "user_study_schedules_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."user_study_schedules" validate constraint "user_study_schedules_user_id_fkey";

alter table "public"."user_study_schedules" add constraint "user_study_schedules_user_id_key" UNIQUE using index "user_study_schedules_user_id_key";

alter table "public"."users" add constraint "users_id_fkey" FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE not valid;

alter table "public"."users" validate constraint "users_id_fkey";

alter table "public"."watchlist_items" add constraint "watchlist_items_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."watchlist_items" validate constraint "watchlist_items_user_id_fkey";

alter table "public"."watchlist_items" add constraint "watchlist_items_user_id_ticker_key" UNIQUE using index "watchlist_items_user_id_ticker_key";

alter table "public"."whale_follows" add constraint "whale_follows_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."whale_follows" validate constraint "whale_follows_user_id_fkey";

alter table "public"."whale_follows" add constraint "whale_follows_user_id_whale_id_key" UNIQUE using index "whale_follows_user_id_whale_id_key";

alter table "public"."whale_follows" add constraint "whale_follows_whale_id_fkey" FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE not valid;

alter table "public"."whale_follows" validate constraint "whale_follows_whale_id_fkey";

alter table "public"."whale_holdings" add constraint "whale_holdings_whale_id_fkey" FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE not valid;

alter table "public"."whale_holdings" validate constraint "whale_holdings_whale_id_fkey";

alter table "public"."whale_holdings" add constraint "whale_holdings_whale_id_ticker_key" UNIQUE using index "whale_holdings_whale_id_ticker_key";

alter table "public"."whale_sector_allocations" add constraint "whale_sector_allocations_whale_id_fkey" FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE not valid;

alter table "public"."whale_sector_allocations" validate constraint "whale_sector_allocations_whale_id_fkey";

alter table "public"."whale_sector_allocations" add constraint "whale_sector_allocations_whale_id_sector_key" UNIQUE using index "whale_sector_allocations_whale_id_sector_key";

alter table "public"."whale_trade_groups" add constraint "whale_trade_groups_whale_id_fkey" FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE not valid;

alter table "public"."whale_trade_groups" validate constraint "whale_trade_groups_whale_id_fkey";

alter table "public"."whale_trades" add constraint "whale_trades_trade_group_id_fkey" FOREIGN KEY (trade_group_id) REFERENCES public.whale_trade_groups(id) ON DELETE SET NULL not valid;

alter table "public"."whale_trades" validate constraint "whale_trades_trade_group_id_fkey";

alter table "public"."whale_trades" add constraint "whale_trades_whale_id_fkey" FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE not valid;

alter table "public"."whale_trades" validate constraint "whale_trades_whale_id_fkey";

set check_function_bodies = off;

CREATE OR REPLACE FUNCTION public.create_user_credits()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    INSERT INTO user_credits (user_id, total, used)
    VALUES (NEW.id, CASE NEW.tier
        WHEN 'free' THEN 3
        WHEN 'pro' THEN 25
        WHEN 'premium' THEN 100
    END, 0);
    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
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
$function$
;

CREATE OR REPLACE FUNCTION public.increment_chat_message_count()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE chat_sessions
    SET message_count = message_count + 1,
        last_message_at = now()
    WHERE id = NEW.session_id;
    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.search_all_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 10)
 RETURNS TABLE(source_type text, source_id uuid, source_label text, section_title text, chunk_text text, similarity double precision)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    (
        SELECT
            'book'::TEXT AS source_type,
            bc.book_id AS source_id,
            b.title || ' by ' || b.author AS source_label,
            bc.section_title,
            bc.chunk_text,
            (1 - (bc.embedding <=> query_embedding))::FLOAT AS similarity
        FROM book_chunks bc
        JOIN books b ON bc.book_id = b.id
        WHERE (1 - (bc.embedding <=> query_embedding)) > match_threshold
    )
    UNION ALL
    (
        SELECT
            'article'::TEXT,
            ac.article_id,
            'Article' AS source_label,
            ac.section_title,
            ac.chunk_text,
            (1 - (ac.embedding <=> query_embedding))::FLOAT
        FROM article_chunks ac
        WHERE (1 - (ac.embedding <=> query_embedding)) > match_threshold
    )
    UNION ALL
    (
        SELECT
            'filing'::TEXT,
            cfc.id,
            cfc.ticker || ' ' || cfc.filing_type || ' ' || cfc.fiscal_year::TEXT AS source_label,
            cfc.section_title,
            cfc.chunk_text,
            (1 - (cfc.embedding <=> query_embedding))::FLOAT
        FROM company_filing_chunks cfc
        WHERE (1 - (cfc.embedding <=> query_embedding)) > match_threshold
    )
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.search_article_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 5)
 RETURNS TABLE(id uuid, article_id uuid, section_title text, chunk_text text, similarity double precision)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        ac.id,
        ac.article_id,
        ac.section_title,
        ac.chunk_text,
        (1 - (ac.embedding <=> query_embedding))::FLOAT AS similarity
    FROM article_chunks ac
    WHERE (1 - (ac.embedding <=> query_embedding)) > match_threshold
    ORDER BY ac.embedding <=> query_embedding
    LIMIT match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.search_book_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 5, filter_book_id uuid DEFAULT NULL::uuid)
 RETURNS TABLE(id uuid, book_id uuid, book_title text, book_author text, chapter_number integer, section_title text, chunk_text text, similarity double precision)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        bc.id,
        bc.book_id,
        b.title AS book_title,
        b.author AS book_author,
        bc.chapter_number,
        bc.section_title,
        bc.chunk_text,
        (1 - (bc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM book_chunks bc
    JOIN books b ON bc.book_id = b.id
    WHERE (1 - (bc.embedding <=> query_embedding)) > match_threshold
      AND (filter_book_id IS NULL OR bc.book_id = filter_book_id)
    ORDER BY bc.embedding <=> query_embedding
    LIMIT match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.search_filing_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 5, filter_ticker text DEFAULT NULL::text, filter_filing_type text DEFAULT NULL::text)
 RETURNS TABLE(id uuid, ticker text, filing_type text, fiscal_year integer, fiscal_quarter integer, section_title text, chunk_text text, similarity double precision)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        cfc.id,
        cfc.ticker,
        cfc.filing_type,
        cfc.fiscal_year,
        cfc.fiscal_quarter,
        cfc.section_title,
        cfc.chunk_text,
        (1 - (cfc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM company_filing_chunks cfc
    WHERE (1 - (cfc.embedding <=> query_embedding)) > match_threshold
      AND (filter_ticker IS NULL OR cfc.ticker = filter_ticker)
      AND (filter_filing_type IS NULL OR cfc.filing_type = filter_filing_type)
    ORDER BY cfc.embedding <=> query_embedding
    LIMIT match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.update_updated_at_column()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.update_whale_followers_count()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;

create or replace view "public"."vector_search_stats" as  SELECT 'book_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (book_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(book_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT book_chunks.book_id) AS unique_sources
   FROM public.book_chunks
UNION ALL
 SELECT 'article_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (article_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(article_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT article_chunks.article_id) AS unique_sources
   FROM public.article_chunks
UNION ALL
 SELECT 'company_filing_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (company_filing_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(company_filing_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT company_filing_chunks.ticker) AS unique_sources
   FROM public.company_filing_chunks;



  create policy "personas_select_all"
  on "public"."agent_personas"
  as permissive
  for select
  to public
using (true);



  create policy "personas_service_all"
  on "public"."agent_personas"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "article_chunks_select_all"
  on "public"."article_chunks"
  as permissive
  for select
  to public
using (true);



  create policy "article_chunks_service_all"
  on "public"."article_chunks"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "snapshots_select_all"
  on "public"."asset_snapshots"
  as permissive
  for select
  to public
using (true);



  create policy "snapshots_service_all"
  on "public"."asset_snapshots"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "book_chapters_select_all"
  on "public"."book_chapters"
  as permissive
  for select
  to public
using (true);



  create policy "book_chapters_service_all"
  on "public"."book_chapters"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "book_chunks_select_all"
  on "public"."book_chunks"
  as permissive
  for select
  to public
using (true);



  create policy "book_chunks_service_all"
  on "public"."book_chunks"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "books_select_all"
  on "public"."books"
  as permissive
  for select
  to public
using (true);



  create policy "books_service_all"
  on "public"."books"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "chat_messages_insert_own"
  on "public"."chat_messages"
  as permissive
  for insert
  to public
with check ((EXISTS ( SELECT 1
   FROM public.chat_sessions
  WHERE ((chat_sessions.id = chat_messages.session_id) AND (chat_sessions.user_id = auth.uid())))));



  create policy "chat_messages_select_own"
  on "public"."chat_messages"
  as permissive
  for select
  to public
using ((EXISTS ( SELECT 1
   FROM public.chat_sessions
  WHERE ((chat_sessions.id = chat_messages.session_id) AND (chat_sessions.user_id = auth.uid())))));



  create policy "chat_messages_service_insert"
  on "public"."chat_messages"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "chat_sessions_delete_own"
  on "public"."chat_sessions"
  as permissive
  for delete
  to public
using ((auth.uid() = user_id));



  create policy "chat_sessions_insert_own"
  on "public"."chat_sessions"
  as permissive
  for insert
  to public
with check ((auth.uid() = user_id));



  create policy "chat_sessions_select_own"
  on "public"."chat_sessions"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "chat_sessions_update_own"
  on "public"."chat_sessions"
  as permissive
  for update
  to public
using ((auth.uid() = user_id));



  create policy "filing_chunks_select_all"
  on "public"."company_filing_chunks"
  as permissive
  for select
  to public
using (true);



  create policy "filing_chunks_service_all"
  on "public"."company_filing_chunks"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "lessons_select_all"
  on "public"."lessons"
  as permissive
  for select
  to public
using (true);



  create policy "lessons_service_all"
  on "public"."lessons"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "money_moves_select_all"
  on "public"."money_move_articles"
  as permissive
  for select
  to public
using (true);



  create policy "money_moves_service_all"
  on "public"."money_move_articles"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "news_select_all"
  on "public"."news_articles"
  as permissive
  for select
  to public
using (true);



  create policy "news_service_all"
  on "public"."news_articles"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "reports_delete_own"
  on "public"."research_reports"
  as permissive
  for delete
  to public
using ((auth.uid() = user_id));



  create policy "reports_insert_own"
  on "public"."research_reports"
  as permissive
  for insert
  to public
with check ((auth.uid() = user_id));



  create policy "reports_select_own"
  on "public"."research_reports"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "reports_service_all"
  on "public"."research_reports"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "reports_update_own"
  on "public"."research_reports"
  as permissive
  for update
  to public
using ((auth.uid() = user_id));



  create policy "bookmarks_delete_own"
  on "public"."user_bookmarks"
  as permissive
  for delete
  to public
using ((auth.uid() = user_id));



  create policy "bookmarks_insert_own"
  on "public"."user_bookmarks"
  as permissive
  for insert
  to public
with check ((auth.uid() = user_id));



  create policy "bookmarks_select_own"
  on "public"."user_bookmarks"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "credits_select_own"
  on "public"."user_credits"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "credits_service_all"
  on "public"."user_credits"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "credits_update_own"
  on "public"."user_credits"
  as permissive
  for update
  to public
using ((auth.uid() = user_id));



  create policy "lesson_progress_insert_own"
  on "public"."user_lesson_progress"
  as permissive
  for insert
  to public
with check ((auth.uid() = user_id));



  create policy "lesson_progress_select_own"
  on "public"."user_lesson_progress"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "lesson_progress_service_all"
  on "public"."user_lesson_progress"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "lesson_progress_update_own"
  on "public"."user_lesson_progress"
  as permissive
  for update
  to public
using ((auth.uid() = user_id));



  create policy "study_schedules_insert_own"
  on "public"."user_study_schedules"
  as permissive
  for insert
  to public
with check ((auth.uid() = user_id));



  create policy "study_schedules_select_own"
  on "public"."user_study_schedules"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "study_schedules_service_all"
  on "public"."user_study_schedules"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "study_schedules_update_own"
  on "public"."user_study_schedules"
  as permissive
  for update
  to public
using ((auth.uid() = user_id));



  create policy "users_insert_own"
  on "public"."users"
  as permissive
  for insert
  to public
with check ((auth.uid() = id));



  create policy "users_select_own"
  on "public"."users"
  as permissive
  for select
  to public
using ((auth.uid() = id));



  create policy "users_service_all"
  on "public"."users"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "users_update_own"
  on "public"."users"
  as permissive
  for update
  to public
using ((auth.uid() = id));



  create policy "watchlist_delete_own"
  on "public"."watchlist_items"
  as permissive
  for delete
  to public
using ((auth.uid() = user_id));



  create policy "watchlist_insert_own"
  on "public"."watchlist_items"
  as permissive
  for insert
  to public
with check ((auth.uid() = user_id));



  create policy "watchlist_select_own"
  on "public"."watchlist_items"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "watchlist_update_own"
  on "public"."watchlist_items"
  as permissive
  for update
  to public
using ((auth.uid() = user_id));



  create policy "whale_follows_delete_own"
  on "public"."whale_follows"
  as permissive
  for delete
  to public
using ((auth.uid() = user_id));



  create policy "whale_follows_insert_own"
  on "public"."whale_follows"
  as permissive
  for insert
  to public
with check ((auth.uid() = user_id));



  create policy "whale_follows_select_own"
  on "public"."whale_follows"
  as permissive
  for select
  to public
using ((auth.uid() = user_id));



  create policy "whale_holdings_select_all"
  on "public"."whale_holdings"
  as permissive
  for select
  to public
using (true);



  create policy "whale_holdings_service_all"
  on "public"."whale_holdings"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "whale_sectors_select_all"
  on "public"."whale_sector_allocations"
  as permissive
  for select
  to public
using (true);



  create policy "whale_sectors_service_all"
  on "public"."whale_sector_allocations"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "whale_trade_groups_select_all"
  on "public"."whale_trade_groups"
  as permissive
  for select
  to public
using (true);



  create policy "whale_trade_groups_service_all"
  on "public"."whale_trade_groups"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "whale_trades_select_all"
  on "public"."whale_trades"
  as permissive
  for select
  to public
using (true);



  create policy "whale_trades_service_all"
  on "public"."whale_trades"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));



  create policy "whales_select_all"
  on "public"."whales"
  as permissive
  for select
  to public
using (true);



  create policy "whales_service_all"
  on "public"."whales"
  as permissive
  for all
  to public
using ((auth.role() = 'service_role'::text));


CREATE TRIGGER trg_agent_personas_updated_at BEFORE UPDATE ON public.agent_personas FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER trg_chat_message_count AFTER INSERT ON public.chat_messages FOR EACH ROW EXECUTE FUNCTION public.increment_chat_message_count();

CREATE TRIGGER trg_user_credits_updated_at BEFORE UPDATE ON public.user_credits FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER trg_study_schedules_updated_at BEFORE UPDATE ON public.user_study_schedules FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER trg_create_user_credits AFTER INSERT ON public.users FOR EACH ROW EXECUTE FUNCTION public.create_user_credits();

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER trg_whale_follow_decrement AFTER DELETE ON public.whale_follows FOR EACH ROW EXECUTE FUNCTION public.update_whale_followers_count();

CREATE TRIGGER trg_whale_follow_increment AFTER INSERT ON public.whale_follows FOR EACH ROW EXECUTE FUNCTION public.update_whale_followers_count();

CREATE TRIGGER trg_whales_updated_at BEFORE UPDATE ON public.whales FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();


