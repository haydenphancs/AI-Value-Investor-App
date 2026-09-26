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
    Domain("marketing", "Marketing Engine", "#ea580c",
           "The zero-touch content pipeline (design doc §12): a run per ET day, its artefacts in "
           "the PUBLIC `marketing-media` bucket, a publish ledger per platform, and the podcast "
           "feed's episodes. Nothing FMP-licensed may enter these tables — class A/C content only."),
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
    "public.chat_starters": T("chat",
        purpose="Editorial pool of starter questions for the empty chat state and the "
                "five asset detail AI bars.",
        key=("slug", "text", "scope", "is_active", "sort_order"),
        note="Migration 161. The DAY'S selection is NOT stored: app/services/daily_rotation.py "
             "derives it as a pure function of the pool and the ET date, so every instance "
             "agrees with no schedule table to drift and no cron to miss. `scope` is a closed "
             "vocabulary (global + one per detail screen); non-global rows carry a literal "
             "{symbol} the client fills in, and iOS DROPS a template it cannot fill rather "
             "than rendering a raw brace. SERVICE-ROLE ONLY like 157/158/159 — nothing "
             "client-side reads it (iOS goes through GET /chat/starters), so a public grant "
             "would widen the anon key's reach for no benefit."),
    "public.chat_starter_answers": T("chat",
        purpose="Pre-computed answers to the day's suggestion chips, so tapping one replays "
                "a stored answer instead of paying a Gemini turn.",
        key=("question_hash", "answer_date", "question", "answer", "widget"),
        note="Migration 162. Keyed on the QUESTION, never the chip slot: the chip set is "
             "rebuilt every 15 minutes and its hot-ticker/hot-sector slots follow the tape, "
             "so slot-keying would eventually serve one question's answer under another's "
             "text. Retention is ONE ET day - yesterday's answer to a 'today' question is "
             "wrong, not merely stale. Rows are GLOBALLY shared, so the warm job runs with "
             "no user id, no personalisation and no memory facts. SERVICE-ROLE ONLY: the "
             "rows hold FMP-derived market data and the anon key ships in the iOS binary."),

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

    # ------------------------------------------------------------------ whales
    "public.whales": T("whales",
        key=("id", "name", "cik", "category", "firm_name", "lifecycle_status",
             "last_filing_period", "followers_count"),
        note="The hub — 7 tables cascade from it. Seeded from data/whale_registry.json by "
             "hand (scripts/sync_whale_registry.py), then hydrated nightly via FMP. "
             "UNIQUE(cik) WHERE cik IS NOT NULL."),
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
    # ------------------------------------------ trillion club (175) — Home section
    "public.trillion_club_companies": T("whales",
        purpose="Registry of the Trillion-Dollar Club Bets Home section: which companies worth "
                "$1T or more get a card, how each is sized, and whether its 13F is ingested.",
        key=("slug", "display_name", "ciks", "card_kind", "use_13f", "cap_source",
             "membership_mode", "is_member", "last_market_cap", "published"),
        note="Migration 175. Seeded from data/trillion_club_seed.json by "
             "scripts/seed_trillion_club.py (writes to PRODUCTION; owner-run). Identity and "
             "editorial columns are hand-kept; is_member / streaks / last close are written by "
             "the daily job. A manual cap (Aramco, Samsung) must be force_in/force_out (CHECK). "
             "use_13f is an owner opt-in, never implied by membership. service_role only."),
    "public.trillion_club_stakes": T("whales",
        purpose="Hand-kept stakes outside the 13F (private, non-US listed, off-13F US, "
                "commitments, notes on 13F rows), each with a primary source and dates.",
        key=("company_slug", "kind", "investee_name", "ownership_pct", "disclosed_value_usd",
             "value_basis", "as_of", "source_url", "source_confidence", "material", "published"),
        note="Migration 175. FK to trillion_club_companies.slug (ON DELETE/UPDATE CASCADE). "
             "UNIQUE (company_slug, investee_name, kind) is the seed's upsert key. A secondary "
             "(news-only) row can never be published (CHECK); only material rows reach the "
             "Home card."),
    "public.trillion_club_filings": T("whales",
        purpose="One built 13F snapshot per (CIK, quarter) for the club's 13F filers: holdings "
                "and quarter-over-quarter share changes.",
        key=("cik", "period", "period_end", "filed_on", "amended_on", "accessions",
             "total_value", "holdings", "changes", "raw_hash", "build_status"),
        note="Migration 175. period_end is the 'holdings as of' date, never the filing date. "
             "accessions[] because FMP folds a 13F-HR/A into the original quarter. FMP-licensed "
             "data: served to signed-in users only, never written from a laptop."),

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

    # ------------------------------------------------------------ marketing (170)
    "public.marketing_runs": T("marketing",
        key=("run_date", "status", "stage", "content_class", "source_ref", "attempts", "timings"),
        note="Migration 170. The media worker (a Railway cron service holding NO Supabase key) "
             "claims the day by INSERTing this row through the internal API and checkpoints "
             "`stage` so a skipped or killed cron slot resumes on the next hourly tick."),
    "public.marketing_assets": T("marketing",
        key=("run_id", "kind", "storage_path", "sha256", "status", "duration_seconds"),
        note="Two-phase upload: pending_upload while the worker holds a signed upload URL, ready "
             "once the API has HEAD-verified the object. Paths are content-addressed and immutable."),
    "public.marketing_posts": T("marketing",
        key=("run_id", "platform", "format", "status", "idempotency_key", "external_url",
             "cost_micros"),
        note="pending_review → approved (admin / MARKETING_AUTO_PUBLISH) → queued (claimed by the "
             "publisher loop before the first external call) → published | failed. "
             "idempotency_key is the key presented to the outlet, so a restart cannot double-post; "
             "cost_micros is micro-dollars (X bills $0.015 per post)."),
    "public.podcast_episodes": T("marketing",
        key=("guid", "title", "mp3_path", "duration_seconds", "published_at"),
        note="Apple/Spotify have no upload API: they poll GET /podcast/feed.xml, which is rendered "
             "from this table. guid never changes; mp3_path is immutable (Spotify re-fetches only "
             "on a path change)."),
    # ------------------------------------------------------------ marketing (173 + 176)
    # Purpose text comes from each table's COMMENT ON TABLE — migration 173, and 176 for
    # marketing_scripts, whose comment it rewrites (precedence 1);
    # `purpose=` is still given so the card is not blank if the comment is ever lost.
    "public.marketing_scripts": T("marketing",
        purpose="One row per marketing run: the day's frozen selection and the class-A "
                "writer's validated package, plus the violations of rounds that failed.",
        key=("run_id", "run_date", "status", "source_ref", "template_id", "generation_id",
             "lease_until", "generations", "content_rejections", "reject_reason",
             "retry_not_before", "output"),
        note="Migrations 173 + 176 (run_date, content_rejections, reject_reason). Written ONLY by the web side: app/services/marketing/script_service.py "
             "drives select → generate → accept behind the kick-and-poll internal route (the "
             "writer itself is app/services/marketing/writer_service.py), through the "
             "get/insert/update_script helpers in app/services/marketing/run_service.py. The "
             "worker never writes it. run_id PK = first-write-wins selection. A generation holds the row "
             "through lease_until + a fresh generation_id (one conditional UPDATE) and every "
             "terminal write is fenced on that generation_id. `accepted` is terminal and "
             "`output` immutable. Two caps: 4 content rejections (reject_reason content) and 4 "
             "generations ending without a verdict (writer_unavailable). `run_date` is written "
             "in the selecting INSERT and is what selection's `recent` window reads. Writer "
             "output never goes to the public bucket or to marketing_runs.metadata."),
    "public.marketing_link_hits": T("marketing",
        purpose="Per-campaign daily tap counts for the smart link GET /go/{campaign}.",
        key=("campaign", "day", "hits"),
        note="Migration 173. Batched in-process by app/services/marketing/smart_link.py and "
             "flushed every 60 s through the increment_marketing_link_hits RPC (atomic "
             "upsert-increment, SECURITY INVOKER, service_role only). `day` is the ET calendar "
             "day; `campaign` is CHECKed against the same pattern the route enforces."),

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
    # ------------------------------------------ Caydex Fair Value Estimate (178)
    "public.dcf_fair_value_cache": T("research",
        purpose="Tier-2 cache (24 h) of the Caydex Fair Value Estimate, a 2-stage FCFE DCF.",
        key=("ticker", "model_version", "response_json", "computed_at"),
        note="Migration 178. A row whose model_version differs from the running model is "
             "ignored, so a model change is a miss. GLOBAL and impersonal: no per-user column "
             "may exist (spec: documents/research/dcf-methodology-v1.md). service_role only."),
    "public.dcf_fair_value_history": T("research",
        purpose="Append-only record of every fair-value estimate computed, with its inputs.",
        key=("ticker", "as_of_date", "model_version", "status", "refusal_code", "fair_value",
             "inputs"),
        note="Migration 178. One row per (ticker, ET date, model version); the service upserts "
             "with ignore-duplicates and never updates or deletes. FMP keeps no consensus "
             "history, so this is the only record of what the forecast was on a given day. "
             "service_role only."),
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
    "public.etf_snapshot_cache": T("market-cache",
        purpose="Per-category ETF snapshot sections.",
        key=("symbol", "category", "response_json")),
    "public.index_cache": T("market-cache",
        key=("cache_key", "symbol", "category", "response_json", "cached_at"),
        note="Migration 150. Per-section, 12h, enforced in app code via cached_at. Holds "
             "only sections that CANNOT contain a live price (derived, constituents count, "
             "non-intraday chart) — that is what let `_refresh_volatile` be deleted rather "
             "than fixed. The raw daily history is deliberately excluded: reading ~1 MB "
             "back is slower than re-fetching it from FMP."),
    "public.corporate_action_cache": T("market-cache",
        purpose="Stock splits and ex-dividend dates DERIVED from entitled price series, "
                "because /splits and /dividends are outside the licence.",
        key=("symbol", "kind", "from_date", "to_date", "events"),
        note="Migration 159. FMP's package enforcement (2026-09-03) took away /splits, and "
             "that is not cosmetic: with no split detected, a 13F position merely HELD "
             "through a 10:1 reads as a purchase — KLAC's 10:1 made BlackRock's row show "
             "+$34,275.0M / +901.88% against a true +$71M. Splits are now derived from "
             "historical-price-eod/full vs /non-split-adjusted, whose ratio moves on any "
             "corporate action; the classifier snaps it to a small rational so a SPIN-OFF "
             "(which changes no share count) is not mistaken for a split. Only CLOSED "
             "windows are stored and they never expire: FMP restates its adjusted series "
             "after every action, but the ratio between two days INSIDE a finished window "
             "is invariant under that rescaling. SERVICE-ROLE ONLY like 157/158 — derived "
             "vendor data must not be readable with the anon key in the iOS binary."),
    "public.market_close_snapshot": T("market-cache",
        purpose="Most recent official close per symbol — the denominator for batch "
                "day-change %.",
        key=("symbol", "trade_date", "close", "volume"),
        note="Migration 157. Exists because FMP's package enforcement (2026-09-03) took "
             "away quote/batch-quote: the entitled company-screener has live price but NO "
             "change field, and profile has change but only one symbol per call. Fed "
             "daily from /stable/batch-eod (whole market, 65,690 rows, ~10 s) — far too "
             "heavy for a request path. SERVICE-ROLE ONLY, unlike the other *_cache "
             "tables: a bulk close dump must not be readable with the anon key shipped in "
             "the iOS binary (FMP ToS 2.6.1 redistribution). Index/commodity/crypto/FX "
             "symbols are dropped on ingest — batch-eod includes them, but they 402 on "
             "the per-symbol endpoint and are in no purchased package."),
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
        # 174 adds tickers_as_of / rotation_enabled / pinned_tickers / blocked_tickers; list
        # them here once 174 is applied and the snapshot is re-dumped (the column-drift
        # guard only knows the snapshot).
        key=("slug", "category", "title", "tickers", "accent_hex", "is_active", "sort_order"),
        note="`accent_hex` is server-supplied colour — clamp it through "
             "Color(themedHex:role:fallback:) on iOS, never trust it raw. Since migration "
             "174 `tickers` is rewritten monthly by services/theme_rotation (ONLY through "
             "publish_theme_rotation, which refuses if a list was edited after the run read "
             "it); pinned/blocked are the editor overrides; service_role only."),
    # ------------------------------------------------------ theme rotation (174)
    "public.theme_rotation_runs": T("news",
        purpose="One row per (month, mode) of the monthly Emerging Frontiers rotation — the "
                "month-level done record.",
        key=("run_month", "mode", "status", "attempts", "summary", "error"),
        note="Migration 174. services/theme_rotation/service.py. The concurrency claim is "
             "notification_job_state (theme_rotation_monthly); this row stops a second run "
             "on day 2-7 of the catch-up window. Preview rows are never unique or published."),
    "public.theme_rotation_decisions": T("news",
        purpose="Every (run, theme, ticker) decision of the rotation with reason and score.",
        key=("run_id", "slug", "ticker", "action", "reason_code", "reason_text", "score",
             "strike"),
        note="Migration 174. History (tenure, strikes, returning stocks) is read ONLY from "
             "published live runs, so a dry run can never cause a live removal. reason_text "
             "is a fixed template shown in the app's 'What changed'."),
    "public.theme_relevance_cache": T("news",
        purpose="Tier-2 cache of the AI verdict on whether a company's own description is "
                "on-theme.",
        key=("ticker", "slug", "prompt_version", "definitions_version", "description_hash",
             "verdict"),
        note="Migration 174. services/theme_rotation/llm_gate.py. A verdict can only BLOCK; "
             "keyed by description hash (not month) so it cannot flip at random; failures "
             "are never stored; rationale is audit-only."),
    "public.theme_daily_insights": T("news",
        purpose="Per theme per US trading day: performance vs an S&P 500 ETF and the dated "
                "'why it's moving' summary.",
        key=("slug", "as_of", "performance", "series", "summary_text", "summary_as_of"),
        note="Migration 174. services/theme_insights_service.py, after each close; read by "
             "the Home theme endpoints. A carried-forward summary keeps its ORIGINAL "
             "summary_as_of."),

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
