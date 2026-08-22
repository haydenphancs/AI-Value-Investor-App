"""
schema_curation.py — the hand-maintained half of the Database Atlas.

The parser gets structure out of `schema_snapshot.sql` (columns, keys, indexes,
policies, functions). It cannot get *meaning*. This file supplies that: which
domain a table belongs to, what it is for, and which of its columns actually
matter when you are trying to understand it.

WHERE PURPOSE TEXT COMES FROM — precedence, highest first:
  1. `COMMENT ON TABLE` in the dump. Authored in a migration, versioned with the
     schema, and therefore the closest thing to ground truth. 46 tables have one.
  2. `purpose=` below, for the tables that do not.
  3. Nothing — the generator refuses to run (see `--allow-uncurated`).

`note=` is always shown, in addition to whichever purpose won. Use it for the
non-obvious thing: a dropped FK, a TTL, a trap.

ADDING A TABLE: add an entry here in the same change as the migration. The
generator exits non-zero listing anything in the dump it cannot find here, so a
new table cannot quietly land in the atlas as a blank card.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Domain:
    key: str
    label: str
    color: str
    blurb: str


@dataclass(frozen=True)
class TableDoc:
    domain: str
    purpose: str = ""
    key: tuple[str, ...] = ()
    note: str = ""


# ---------------------------------------------------------------------------
# Domains. Colours extend the palette the sibling System Design docs already
# use (#0284c7 client / #059669 backend / #7c3aed AI / #d97706 data) with the
# structure doc's extras, spread far enough apart to stay tellable at 11px.
# ---------------------------------------------------------------------------

DOMAINS: tuple[Domain, ...] = (
    Domain("identity", "Identity & Accounts", "#0284c7",
           "Who the caller is. `public.users` mirrors `auth.users` 1:1 via a trigger; everything "
           "else here hangs off it."),
    Domain("billing", "Billing, Credits & IAP", "#059669",
           "The money. Two credit pools (granted vs purchased), an append-only ledger, and the "
           "App Store purchase record that must stay exactly-once."),
    Domain("research", "Research & Reports", "#7c3aed",
           "The credit-charged AI report. `research_reports` is both the task queue and the "
           "content store; two cache layers keep a re-run off the agent pipeline."),
    Domain("chat", "Ask Cay AI", "#4f46e5",
           "Conversational analysis. Sessions and messages, plus a per-day turn/token budget "
           "that is claimed before the model is called."),
    Domain("learn-content", "Learn · Content", "#d97706",
           "Server-driven educational content. A new row appears in the already-shipped app — "
           "the bundled JSON is only the offline fallback."),
    Domain("learn-progress", "Learn · Progress", "#ca8a04",
           "What each person has read, finished, bookmarked and scheduled."),
    Domain("whales", "Whales, Institutions & Congress", "#e11d48",
           "13F filers, hedge funds and politician disclosures. `whales` is the registry hub; "
           "everything else cascades from it."),
    Domain("portfolio", "Portfolio & Watchlist", "#0d9488",
           "The caller's own holdings and follow list. Guest-writable, so partitioned per "
           "install rather than FK-bound."),
    Domain("notifications", "Notifications & Alerts", "#db2777",
           "Decide → claim → send. Dedup keys are claimed BEFORE delivery so a retry or a second "
           "Railway instance cannot double-send."),
    Domain("market-cache", "Market Data Caches", "#475569",
           "Tier-2 of the cache-aside pattern: an in-memory dict in front, a Supabase row here, "
           "FMP/CoinGecko/FRED behind. Keyed on a natural ticker/symbol string — no FKs."),
    Domain("llm-intel", "LLM Intel & Audit Trails", "#a21caf",
           "Model-generated analysis, each `*_cache` paired with an `*_audit` table that keeps "
           "the raw response and what was rejected, so a bad answer can be traced."),
    Domain("benchmarks", "Benchmarks & Reference", "#16a34a",
           "Pre-computed peer medians and industry structure, so a report never fans out to "
           "compute a sector median per request."),
    Domain("news", "News & Editorial Feed", "#2563eb",
           "Aggregated headlines with AI enrichment, plus the server-driven Home cards that can "
           "be changed without an app release."),
    Domain("rag", "RAG · pgvector", "#0891b2",
           "Embedded text for grounded, cited answers. Three chunk tables, all `vector(1536)` "
           "with an HNSW index, searched through STABLE SQL functions."),
    Domain("ops", "Analytics, Budgets & Job State", "#78716c",
           "Cross-instance coordination and cost control: who claims a job, how many "
           "generations are left today, and first-party product analytics."),
    Domain("supabase", "Supabase-managed", "#94a3b8",
           "Vendor-owned schemas — GoTrue auth, Storage, Realtime, and the migration ledger. "
           "You depend on them but do not define them; never write a migration against these."),
)

DOMAIN_ORDER: tuple[str, ...] = tuple(d.key for d in DOMAINS)


# ---------------------------------------------------------------------------
# Logical joins that carry no FK constraint and that name-matching cannot infer.
# Rendered as dashed edges alongside the inferred ones.
#   (schema.table, column) -> (schema.table, column, why)
# ---------------------------------------------------------------------------

IMPLICIT_REFS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("public.research_reports", "investor_persona"): (
        "public.agent_personas", "key", "text join on the persona key"),
    ("public.ticker_report_cache", "persona"): (
        "public.agent_personas", "key", "text join on the persona key"),
    ("public.article_chunks", "article_id"): (
        "public.money_move_articles", "id", "RAG chunks of a Money Moves article; no FK"),
    ("public.user_bookmarks", "bookmarkable_id"): (
        "public.books", "id",
        "polymorphic — the real target is chosen by bookmarkable_type "
        "(book | lesson | article | report)"),
    ("public.sector_benchmarks", "industry"): (
        "public.industry_dossier", "industry", "shared industry-name string domain"),
    ("public.industry_moat_benchmarks", "industry"): (
        "public.industry_dossier", "industry", "shared industry-name string domain"),
    ("public.sector_aggregates", "sector"): (
        "public.sector_benchmarks", "sector", "shared sector-name string domain"),
    ("public.analytics_events", "identity_key"): (
        "public.users", "id",
        "holds a real user id OR a per-install guest uuid — deliberately no FK, so account "
        "deletion purges it by hand via _UNLINKED_IDENTITY_TABLES"),
    ("public.guest_report_budget", "bucket_key"): (
        "public.users", "id",
        "synthetic per-install guest uuid from guest_user_id_for(); never a real user row"),
    ("public.book_chunks", "chapter_number"): (
        "public.book_chapters", "chapter_number", "joined with book_id, not a key on its own"),
}

# Columns that look like a foreign key but are not one.
#
# NOTE: as of the current schema this filters NOTHING — every entry below names a
# `<base>_id` column for which no table `<base>`/`<base>s` exists, so the
# inference pass already declines to draw an edge. It is kept as a forward
# guard: the day someone adds a `stocks` or `transactions` table,
# `chat_sessions.stock_id` and `credit_purchases.transaction_id` would silently
# sprout false relationships in the map. A fabricated edge in a schema diagram
# is worse than a missing one, so the guard is cheap insurance rather than dead
# config. `test_not_a_ref_suppresses_an_edge_that_would_otherwise_be_inferred`
# proves it still works.
NOT_A_REF: frozenset[tuple[str, str]] = frozenset({
    ("public.ai_insight_cache", "inputset_id"),
    ("public.updates_insight_state", "last_inputset_id"),
    ("public.chat_sessions", "reference_id"),
    ("public.chat_sessions", "stock_id"),
    ("public.credit_transactions", "ref_id"),
    ("public.credit_transactions", "reverses_id"),
    ("public.credit_purchases", "transaction_id"),
    ("public.credit_purchases", "original_transaction_id"),
    ("public.subscriptions", "original_transaction_id"),
    ("public.news_articles", "external_id"),
    ("public.ticker_news_cache", "external_id"),
    ("public.competitor_intel_audit", "run_id"),
    ("public.moat_intel_audit", "run_id"),
    ("public.ip_intel_audit", "run_id"),
    ("public.price_catalyst_audit", "run_id"),
    ("public.geopolitical_macro_audit", "run_id"),
    ("public.industry_override_audit", "run_id"),
})


# ---------------------------------------------------------------------------
# Per-table curation
# ---------------------------------------------------------------------------

T = TableDoc

CURATION: dict[str, TableDoc] = {

    # ---------------------------------------------------------------- identity
    "public.users": T("identity", key=("id", "email", "tier", "is_admin", "password_changed_at"),
        note="1:1 mirror of auth.users, created by the on_auth_user_created trigger. "
             "The `id` IS the auth uuid — that shared key is what lets RLS compare auth.uid()."),
    "public.user_settings": T("identity",
        purpose="Per-user app preferences as one JSONB blob, so a new toggle needs no migration.",
        key=("user_id", "preferences"),
        note="`preferences` is key-NAME-policied, not schema'd: a type mismatch once rewrote "
             "every server row to false. Version and single-flight writes."),
    "public.user_investor_profile": T("identity",
        key=("user_id", "experience_level", "explanation_style", "topics", "consented_at"),
        note="Guest-writable, so no FK to users. Drives pedagogy only — never analysis."),
    "public.user_memory_facts": T("identity", key=("user_id", "fact_key", "fact_value", "hit_count"),
        note="Derived from conversation, never extracted verbatim."),
    "public.device_tokens": T("identity",
        purpose="APNs device tokens for push. One row per install per user; `environment` "
                "separates sandbox from production so a TestFlight token never gets a prod push.",
        key=("user_id", "token", "platform", "environment")),

    # ----------------------------------------------------------------- billing
    "public.user_credits": T("billing",
        key=("user_id", "total", "used", "remaining", "purchased_total", "purchased_used",
             "spendable", "resets_at"),
        note="TWO pools. Granted credits reset monthly; purchased ones never expire and the "
             "three tier RPCs must never touch them (App Store 3.1.1). `spendable` is the sum."),
    "public.credit_transactions": T("billing",
        purpose="Append-only credit ledger — one row per debit, grant or refund, with the "
                "balance after it.",
        key=("user_id", "delta", "reason", "granted_delta", "purchased_delta", "reverses_id"),
        note="`granted_delta`/`purchased_delta` record HOW a spend split across the two pools; "
             "a refund reverses the recorded split, not the current balance."),
    "public.credit_purchases": T("billing",
        purpose="One row per consumable App Store purchase, and the exactly-once guard for it.",
        key=("user_id", "transaction_id", "environment", "product_id", "credits", "revoked_at"),
        note="UNIQUE(environment, transaction_id) is what makes granting idempotent — the same "
             "transaction replayed cannot mint credits twice."),
    "public.credit_packs": T("billing",
        purpose="Catalogue of purchasable credit packs, keyed by App Store product id.",
        key=("product_id", "credits", "price_cents", "is_active")),
    "public.plan_credits": T("billing",
        purpose="Monthly credit allowance per subscription tier. Read by the tier-grant RPCs.",
        key=("tier", "monthly_credits", "price_cents")),
    "public.subscriptions": T("billing",
        purpose="Current auto-renewing subscription state per user, reconciled from App Store "
                "server notifications.",
        key=("user_id", "tier", "status", "store", "current_period_end", "last_event_at"),
        note="UNIQUE(user_id) — one subscription per account."),
    "public.guest_report_budget": T("billing",
        key=("bucket_key", "period_month", "report_count"),
        note="LEGACY. AI generation went account-only; a client-chosen header meant rotating it "
             "minted a fresh allowance. Credits replaced this. Kept for the claim/release RPCs."),

    # ---------------------------------------------------------------- research
    "public.research_reports": T("research",
        key=("id", "user_id", "ticker", "investor_persona", "status", "progress",
             "processing_started_at", "credits_charged", "is_refunded", "ticker_report_data"),
        note="user_id FK dropped in migration 110 so guests partition per install — deletion is "
             "manual via _UNLINKED_USER_TABLES. `processing_started_at` is the clock the "
             "refund reconciler uses; the completion write is conditional on it."),
    "public.ticker_report_cache": T("research",
        purpose="The assembled report payload, keyed by (ticker, persona), so a second request "
                "for the same pair skips both agent stages.",
        key=("ticker", "persona", "ticker_report_data", "cached_at"),
        note="Close-aligned, NOT rolling-TTL: it pins to the last completed market close."),
    "public.ticker_data_cache": T("research",
        purpose="Stage-A output — the persona-NEUTRAL collected FMP data for a ticker.",
        key=("ticker", "collected_data", "cached_at"),
        note="Persona-neutral on purpose: a second persona on the same ticker reuses this and "
             "skips the ~25-40 call FMP fan-out entirely."),
    "public.agent_personas": T("research",
        key=("key", "name", "persona_prompt", "focus", "is_active"),
        note="`key` is the snake_case join value that research_reports.investor_persona and "
             "ticker_report_cache.persona both carry as plain text."),
    "public.market_deep_dive_cache": T("research",
        purpose="Cached long-form market analysis, keyed by symbol plus a hash of the prompt "
                "context so a different question is a different row.",
        key=("symbol", "context_hash", "report_markdown")),

    # -------------------------------------------------------------------- chat
    "public.chat_sessions": T("chat",
        key=("id", "user_id", "session_type", "context_type", "reference_id", "message_count",
             "memory_summary"),
        note="Guest-writable — user_id FK dropped in migration 111. Chat transcripts are the "
             "most sensitive rows stored, so account deletion lists this explicitly."),
    "public.chat_messages": T("chat",
        key=("session_id", "role", "content", "rich_content", "citations", "tokens_used"),
        note="Cascades from chat_sessions, which is why it needs no entry of its own in the "
             "manual account-deletion list."),
    "public.chat_usage_budget": T("chat",
        purpose="Per-user, per-day chat turn and token budget.",
        key=("user_id", "budget_day", "turn_count", "token_count"),
        note="A turn is CLAIMED before the model call and released on failure, so a crash "
             "cannot leak budget."),

    # ----------------------------------------------------------- learn-content
    "public.books": T("learn-content", key=("id", "title", "author", "level", "is_most_read")),
    "public.book_chapters": T("learn-content", key=("book_id", "chapter_number", "chapter_title",
        "sections", "audio_duration_seconds"),
        note="`sections` is a JSONB array [{title, content, iconName?}]."),
    "public.lessons": T("learn-content", key=("id", "title", "level", "sort_order", "story_content"),
        note="`story_content` carries the lesson body AND its word-level read-along timings, so "
             "new narration ships without an app update."),
    "public.money_move_articles": T("learn-content",
        key=("slug", "title", "category", "content", "sections", "audio_url", "is_featured"),
        note="Sentence-level read-along timings ride inside the served JSONB. A new row is a new "
             "card in the already-shipped app."),

    # ---------------------------------------------------------- learn-progress
    "public.user_learn_progress": T("learn-progress",
        purpose="The unified Learn progress table — one row per (user, content_type, item_key) "
                "the person has completed.",
        key=("user_id", "content_type", "item_key", "completed_at"),
        note="Guest-writable, so no FK to users. Supersedes user_lesson_progress."),
    "public.user_lesson_progress": T("learn-progress",
        purpose="Legacy per-lesson progress, superseded by user_learn_progress.",
        key=("user_id", "lesson_id", "status", "completed_at"),
        note="No reference remains in backend/app — it is FK-bound and still carries RLS, but "
             "the live read path is user_learn_progress."),
    "public.user_study_schedules": T("learn-progress",
        key=("user_id", "daily_reminder_enabled", "morning_session_time", "review_time"),
        note="UNIQUE(user_id) — one schedule per account."),
    "public.user_bookmarks": T("learn-progress",
        key=("user_id", "bookmarkable_type", "bookmarkable_id"),
        note="Polymorphic: bookmarkable_type picks which table bookmarkable_id points into, so "
             "no FK can enforce it. No reference remains in backend/app."),

    # ------------------------------------------------------------------ whales
    "public.whales": T("whales",
        key=("id", "name", "cik", "category", "firm_name", "lifecycle_status",
             "last_filing_period", "followers_count"),
        note="The hub — 7 tables cascade from it. Seeded from data/whale_registry.json at "
             "startup, then hydrated from EDGAR. UNIQUE(cik) WHERE cik IS NOT NULL."),
    "public.whale_holdings": T("whales",
        purpose="Current position per whale per ticker, as a share of the portfolio.",
        key=("whale_id", "ticker", "allocation", "change_percent")),
    "public.whale_trades": T("whales",
        purpose="Individual buy/sell events derived by diffing consecutive 13F filings, or "
                "parsed from a congressional disclosure.",
        key=("whale_id", "trade_group_id", "ticker", "action", "trade_type", "amount_range",
             "date", "disclosure_date"),
        note="13F diffs must compare SHARES, not value — a price move is not a trade, and a "
             "split fabricates a huge one. `date` is when it traded; `disclosure_date` is when "
             "it was filed, and a 13F carries a 45-day lag between them."),
    "public.whale_trade_groups": T("whales",
        purpose="One filing period's trades for one whale, rolled up with a generated summary.",
        key=("whale_id", "date", "trade_count", "net_action", "net_amount", "insights"),
        note="whale_trades.trade_group_id is the one FK in the schema that is ON DELETE SET "
             "NULL — a trade outlives its rollup."),
    "public.whale_sector_allocations": T("whales",
        purpose="Portfolio weight per sector for one whale, for the allocation donut.",
        key=("whale_id", "sector", "allocation")),
    "public.whale_alerts": T("whales",
        purpose="Editorial 'notable move' cards surfaced on the Whale tab.",
        key=("whale_id", "title", "ticker", "is_active", "expires_at")),
    "public.whale_follows": T("whales",
        purpose="Which whales a user follows. The one user↔whale join in the schema.",
        key=("user_id", "whale_id"),
        note="Account-only (FK-bound both ways). Triggers keep whales.followers_count in sync "
             "on insert and delete."),
    "public.whale_filing_snapshots": T("whales",
        key=("whale_id", "filing_period", "filing_date", "total_value", "holdings_data",
             "raw_hash", "processed_at"),
        note="`raw_hash` makes ingestion idempotent — an unchanged filing is skipped rather "
             "than re-diffed into phantom trades."),
    "public.whale_profile_cache": T("whales",
        purpose="Rendered whale profile payload, cached by whale_id.",
        key=("whale_id", "profile_json", "cached_at")),
    "public.hedge_fund_quarters": T("whales",
        purpose="Per-ticker quarterly institutional flow — buy vs sell volume and holder counts "
                "— behind the 'Institutions' flow chart.",
        key=("ticker", "year", "quarter", "buy_volume", "sell_volume", "net_flow"),
        note="Measured in SHARES. The UI label is 'Institutions'; the code says hedge_fund_*."),

    # --------------------------------------------------------------- portfolio
    "public.portfolios": T("portfolio",
        purpose="A named portfolio belonging to one caller. Several per user, exactly one active.",
        key=("user_id", "name", "is_active", "sort_order"),
        note="Guest-writable — user_id FK dropped in migration 108. A partial unique index on "
             "(user_id) WHERE is_active enforces the single active portfolio."),
    "public.portfolio_items": T("portfolio",
        purpose="A ticker position inside a portfolio.",
        key=("portfolio_id", "ticker", "shares", "market_value", "position"),
        note="Cascades from portfolios — which is deleted by hand, so the cascade still runs."),
    "public.portfolio_holdings": T("portfolio",
        purpose="Legacy flat holdings table, keyed directly on the user rather than a portfolio.",
        key=("user_id", "ticker", "shares", "market_value", "sector"),
        note="Still FK-bound to users and still carries six RLS policies, some of them "
             "overlapping legacy duplicates. The live path is portfolios + portfolio_items."),
    "public.watchlist_items": T("portfolio",
        key=("user_id", "ticker", "shares", "market_value", "sector", "asset_type", "market_cap"),
        note="Guest-writable — user_id FK dropped in migration 108. UNIQUE(user_id, ticker)."),

    # ----------------------------------------------------------- notifications
    "public.notification_events": T("notifications",
        key=("user_id", "dedup_key", "kind", "category", "push_state", "deliver_after",
             "sent_at", "read_at"),
        note="UNIQUE(user_id, dedup_key) is the dedup CLAIM — insert before sending and treat a "
             "conflict as already-handled. Doubles as the in-app inbox, the per-category cap "
             "ledger and the quiet-hours deferral queue."),
    "public.notification_job_state": T("notifications",
        key=("job", "enabled", "claim_at", "runs_today", "notified_today", "last_cursor")),
    "public.push_send_log": T("notifications", key=("user_id", "dedup_key", "sent_at")),
    "public.price_alerts": T("notifications",
        key=("user_id", "ticker", "kind", "threshold", "armed", "last_price", "repeat_mode")),

    # ------------------------------------------------------------ market-cache
    "public.stock_fundamentals_cache": T("market-cache",
        purpose="Tier-2 cache of the bulk FMP fundamentals response for a ticker.",
        key=("ticker", "response_json", "cached_at")),
    "public.company_profile_cache": T("market-cache",
        purpose="Tier-2 cache of the FMP company profile — name, sector, industry, logo.",
        key=("ticker", "profile_json", "cached_at")),
    "public.growth_cache": T("market-cache",
        purpose="Assembled growth section (revenue/EPS/FCF YoY and QoQ) for a ticker.",
        key=("ticker", "response_json", "next_earnings_date"),
        note="`next_earnings_date` is the invalidation trigger — growth is stale the moment the "
             "company reports, regardless of elapsed time."),
    "public.earnings_cache": T("market-cache",
        purpose="Earnings history plus forward estimates for the earnings timeline.",
        key=("ticker", "response_json", "next_earnings_date")),
    "public.profit_power_cache": T("market-cache",
        purpose="Margin and return metrics behind the Profitability section.",
        key=("ticker", "response_json", "next_earnings_date"),
        note="The reference implementation of the two-tier + _inflight cache pattern."),
    "public.revenue_breakdown_cache": T("market-cache",
        purpose="Revenue split by product and geography, from FMP segmentation.",
        key=("ticker", "response_json", "next_earnings_date")),
    "public.holders_cache": T("market-cache",
        purpose="Institutional and insider ownership for the Holders tab.",
        key=("ticker", "response_json", "cached_at"),
        note="13F data behind this carries a 45-day filing lag; the tab must date-label it or "
             "it reads as live trading."),
    "public.short_interest_cache": T("market-cache",
        purpose="FINRA short-interest figures per ticker.",
        key=("ticker", "response_json", "cached_at")),
    "public.health_check_cache": T("market-cache",
        purpose="Balance-sheet health scores (liquidity, leverage, coverage).",
        key=("ticker", "response_json", "next_earnings_date")),
    "public.signal_of_confidence_cache": T("market-cache",
        purpose="The composite confidence signal shown on the detail screen.",
        key=("ticker", "response_json", "next_earnings_date")),
    "public.ticker_volatility_cache": T("market-cache",
        purpose="Daily sigma per ticker, used to decide whether a price move is notable enough "
                "to explain.",
        key=("ticker", "sigma_daily", "sample_size", "expires_at"),
        note="Replaced a fixed percentage band — a 3% day means something very different for a "
             "utility than for a small-cap biotech."),
    "public.snapshot_cache": T("market-cache",
        purpose="Per-category detail-screen snapshot payloads for a ticker.",
        key=("ticker", "category", "response_json")),
    "public.signals_cache": T("market-cache",
        purpose="Generic keyed cache for computed signal payloads.",
        key=("cache_key", "data", "expires_at")),
    "public.etf_detail_cache": T("market-cache",
        purpose="Full ETF detail response — holdings, sectors, expense ratio.",
        key=("cache_key", "symbol", "chart_range", "interval", "response_json"),
        note="RETIRED and no longer read or written. etf_service was decomposed into "
             "per-section caches (etf_snapshot_cache categories fundamentals / derived / "
             "chart:*), because a 24h row of the WHOLE payload froze current_price — which "
             "is the only reason `_refresh_volatile` ever existed. Left in place for one "
             "release so a rollback is a code revert; drop it after that."),
    "public.etf_snapshot_cache": T("market-cache",
        purpose="Per-category ETF snapshot sections.",
        key=("symbol", "category", "response_json")),
    "public.index_detail_cache": T("market-cache",
        purpose="Index detail response, keyed by symbol AND chart range.",
        key=("cache_key", "symbol", "chart_range", "response_json"),
        note="RETIRED and no longer read or written — superseded by index_cache. Migration "
             "150 also REVOKEd the GRANT ALL to `authenticated` that 032 shipped, which had "
             "let any signed-in user read and write this cache through PostgREST. Left in "
             "place for one release so a rollback is a code revert."),
    "public.index_cache": T("market-cache",
        key=("cache_key", "symbol", "category", "response_json", "cached_at"),
        note="Migration 150. Per-section, 12h, enforced in app code via cached_at. Holds "
             "only sections that CANNOT contain a live price (derived, constituents count, "
             "non-intraday chart) — that is what let `_refresh_volatile` be deleted rather "
             "than fixed. The raw daily history is deliberately excluded: reading ~1 MB "
             "back is slower than re-fetching it from FMP."),
    "public.commodity_cache": T("market-cache",
        key=("cache_key", "symbol", "category", "response_json", "cached_at"),
        note="Migration 149. Same per-section shape as index_cache, and the first of the "
             "three. Quotes and the raw history are excluded by design."),
    "public.index_macro_forecast_cache": T("market-cache",
        purpose="FRED-derived macro indicators and the narrative template for an index.",
        key=("symbol", "story_template", "indicators_json")),
    "public.crypto_coin_id_cache": T("market-cache",
        purpose="Symbol → CoinGecko id resolution, tier 2 of the three-tier resolver.",
        key=("symbol", "coingecko_id", "name"),
        note="Resolution order is hardcoded top-100 map → this table → CoinGecko /search. "
             "Bypassing the order burns the 30 calls/min free-tier budget."),
    "public.crypto_fundamentals_cache": T("market-cache",
        purpose="Supply, FDV and volume for a coin.",
        key=("symbol", "response_json")),
    "public.crypto_snapshots": T("market-cache",
        purpose="Generated per-category prose for a coin's detail screen.",
        key=("symbol", "category", "paragraphs")),
    "public.asset_snapshots": T("market-cache",
        key=("symbol", "asset_type", "snapshot_type", "content", "expires_at"),
        note="No reference remains in backend/app; crypto_snapshots and etf_snapshot_cache "
             "carry the live paths."),
    "public.social_mentions_history": T("market-cache",
        purpose="Daily ApeWisdom mention and upvote counts per ticker, kept as history so the "
                "sentiment trend has a denominator.",
        key=("ticker", "mentions", "upvotes", "rank", "source", "snapshot_date")),

    # --------------------------------------------------------------- llm-intel
    "public.ai_insight_cache": T("llm-intel",
        purpose="Generated headline + bullets for an Updates scope, with the inputs that "
                "produced them.",
        key=("scope", "headline", "bullets", "inputset_id", "trigger_reason", "close_cycle",
             "soft_expires_at", "hard_expires_at"),
        note="Two expiries: soft allows a refresh, hard forces one. `inputset_id` is what makes "
             "regeneration event-driven rather than timer-driven."),
    "public.competitor_intel_cache": T("llm-intel",
        purpose="Validated competitor ticker set for a company.",
        key=("ticker", "competitor_tickers", "source_labels", "expires_at")),
    "public.competitor_intel_audit": T("llm-intel",
        purpose="Every competitor-intel run: raw response, what validated, what was rejected.",
        key=("run_id", "ticker", "status", "suggested_tickers", "validated_tickers", "rejected")),
    "public.moat_intel_cache": T("llm-intel",
        purpose="Per-pillar moat scores for a company.",
        key=("ticker", "pillar_scores", "source_labels", "expires_at")),
    "public.moat_intel_audit": T("llm-intel",
        purpose="Every moat run, including which pillars were asked for versus resolved.",
        key=("run_id", "ticker", "status", "pillars_requested", "pillars_resolved", "rejected")),
    "public.ip_intel_cache": T("llm-intel",
        purpose="Patent and FDA-pipeline intelligence for a company.",
        key=("ticker", "payload", "source_labels", "expires_at")),
    "public.ip_intel_audit": T("llm-intel",
        purpose="Every IP-intel run with the USPTO/FDA counts it resolved.",
        key=("run_id", "ticker", "status", "uspto_total", "fda_active", "error_detail")),
    "public.price_catalyst_cache": T("llm-intel",
        purpose="The grounded explanation for a notable price move — tag, reason, sources.",
        key=("ticker", "tag", "reason", "sources", "window_label", "change_pct", "expires_at")),
    "public.price_catalyst_audit": T("llm-intel",
        purpose="Every catalyst run with the search queries it issued.",
        key=("run_id", "ticker", "status", "change_pct", "search_queries", "tokens_used")),
    "public.geopolitical_macro_cache": T("llm-intel",
        purpose="Macro threat factors per scope, feeding the report's critical-factors section.",
        key=("scope", "factors", "expires_at")),
    "public.geopolitical_macro_audit": T("llm-intel",
        purpose="Every macro run with its factor count and search queries.",
        key=("run_id", "status", "factor_count", "search_queries", "tokens_used")),
    "public.industry_override_audit": T("llm-intel",
        purpose="Audit of model-proposed TAM/CAGR overrides for an industry — what was proposed, "
                "what was applied, and why anything was rejected.",
        key=("run_id", "industry", "status", "phase_a_tam_b", "applied_tam_b",
             "rejection_reason")),

    # -------------------------------------------------------------- benchmarks
    "public.sector_benchmarks": T("benchmarks",
        purpose="Pre-computed median financial metrics so a report never fans out to compute "
                "peer medians per request.",
        key=("sector", "industry", "metric_name", "period_type", "period_label", "median_value",
             "sample_size"),
        note="`industry = ''` is the SECTOR aggregate and the fallback; a non-empty industry is "
             "an INDUSTRY aggregate whose `sector` names its parent. Winsorized, and dropped "
             "below MIN_SAMPLE_SIZE rather than published thin."),
    "public.sector_aggregates": T("benchmarks",
        purpose="Sector-level size and concentration — revenue, CAGR, HHI, top-holder shares.",
        key=("sector", "total_revenue_usd", "cagr_5yr_pct", "hhi", "num_constituents")),
    "public.industry_dossier": T("benchmarks",
        purpose="Per-industry structure: TAM now and future, growth, lifecycle phase and "
                "concentration. The Moat section's TAM comes from here.",
        key=("industry", "sector", "current_tam_b", "future_tam_b", "cagr_5y_pct",
             "lifecycle_phase", "hhi", "tam_scope"),
        note="TAM is scoped per INDUSTRY, not per company, and the unit is BILLIONS."),
    "public.industry_moat_benchmarks": T("benchmarks",
        purpose="Peer moat-score distribution per (industry, pillar), for the 'vs peers' band.",
        key=("industry", "pillar_name", "peer_average_score", "score_p25", "score_p75",
             "sample_size")),

    # -------------------------------------------------------------------- news
    "public.news_articles": T("news",
        key=("external_id", "source_name", "headline", "sentiment", "related_tickers",
             "insight_key_points", "expires_at"),
        note="UNIQUE(external_id, source_name) — the same wire story from two sources is two "
             "rows. Swept by cleanup_expired_news_articles()."),
    "public.ticker_news_cache": T("news",
        purpose="Per-ticker news with AI summary bullets and sentiment, cached separately from "
                "the global feed.",
        key=("ticker", "external_id", "headline", "summary_bullets", "sentiment", "ai_processed",
             "expires_at"),
        note="Enrichment is per (ticker, article): sharing one cache across tickers "
             "misattributes a summary written for a different company."),
    "public.market_insights": T("news", key=("headline", "bullet_points", "sentiment")),
    "public.daily_briefings": T("news", key=("type", "title", "date", "is_active", "priority")),
    "public.trending_themes": T("news",
        key=("slug", "category", "title", "tickers", "accent_hex", "is_active", "sort_order"),
        note="`accent_hex` is server-supplied colour — clamp it through "
             "Color(themedHex:role:fallback:) on iOS, never trust it raw."),

    # --------------------------------------------------------------------- rag
    "public.book_chunks": T("rag",
        purpose="Embedded book text for retrieval-grounded answers about a book.",
        key=("book_id", "chapter_number", "chunk_index", "chunk_text", "embedding"),
        note="vector(1536) with an HNSW index. Searched via search_book_chunks()."),
    "public.article_chunks": T("rag",
        purpose="Embedded Money Moves article text.",
        key=("article_id", "chunk_index", "chunk_text", "embedding"),
        note="vector(1536), HNSW. article_id points at money_move_articles with no FK."),
    "public.company_filing_chunks": T("rag",
        key=("ticker", "filing_type", "fiscal_year", "fiscal_quarter", "chunk_index",
             "chunk_text", "embedding"),
        note="vector(1536), HNSW. `ticker` is a natural key — there is no ticker table."),

    # --------------------------------------------------------------------- ops
    "public.analytics_events": T("ops",
        key=("identity_key", "session_id", "event", "props", "app_version", "client_ts",
             "server_ts")),
    "public.ai_insight_budget": T("ops",
        purpose="Global per-day generation cap for Updates insights — the cost ceiling.",
        key=("budget_day", "gen_count"),
        note="Incremented through increment_ai_insight_budget(), which returns the new count so "
             "the caller can stop rather than check-then-act."),
    "public.updates_insight_state": T("ops",
        purpose="Per-scope scheduler state for the Updates insight job: what it last saw, why it "
                "regenerated or skipped, and how many attempts are left today.",
        key=("scope", "last_inputset_id", "last_trigger_reason", "last_skip_reason",
             "close_cycle", "regen_count_today", "attempts_today", "claim_at"),
        note="`claim_at` is the cross-instance lease. `last_skip_reason` is what makes a quiet "
             "day diagnosable instead of looking like a broken job."),
}


# ---------------------------------------------------------------------------
# Supabase-managed schemas. Vendor-owned; documented at the schema level rather
# than table by table, because you neither define nor migrate them.
# ---------------------------------------------------------------------------

MANAGED_SCHEMAS: dict[str, str] = {
    "auth": "Supabase GoTrue. `auth.users` is the identity root — public.users mirrors it 1:1 "
            "through the on_auth_user_created trigger, and every RLS policy comparing "
            "auth.uid() is comparing against a row in here.",
    "storage": "Supabase Storage metadata. Nine buckets. PUBLIC (served from "
               "/object/public/, which bypasses RLS): book-covers, journey-images, "
               "money-moves-images, home-theme-media — none is LISTABLE any more, migration "
               "153 dropped their anon SELECT policies so the object URL is the only way in. "
               "PRIVATE (service-role write, short-lived signed URLs on read): book-media, "
               "journey-media, money-moves-media, research-pdfs and user-avatars.",
    "realtime": "Supabase Realtime. Not used by this app's request paths.",
    "supabase_migrations": "Supabase CLI migration ledger.",
}

MANAGED_TABLE_NOTES: dict[str, str] = {
    "auth.users": "The identity root. public.users.id IS this table's id — the shared key is "
                  "what makes `auth.uid() = user_id` work in every RLS policy.",
    "auth.sessions": "Live sessions behind the access/refresh token pair.",
    "auth.identities": "One row per linked provider (Apple, Google, email) per user.",
    "auth.refresh_tokens": "Refresh-token chain; a reuse is what invalidates a family.",
    "storage.objects": "Every stored file. Service-role policies scope the app's nine "
                       "buckets; the four public ones have no anon/authenticated policy at "
                       "all, because a public bucket is served without touching RLS and the "
                       "policy only ever enabled enumeration (migration 153).",
    "storage.buckets": "Bucket definitions, incl. which are public vs signed-URL only.",
}
