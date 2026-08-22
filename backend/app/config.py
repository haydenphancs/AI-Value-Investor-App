"""
Application Configuration
Environment variables with Pydantic validation.
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

# `backend/` — this file is `backend/app/config.py`.
_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Caydex API"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ALLOWED_ORIGINS: list[str] = ["*"]

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24       # 24 hours
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Admin trigger token — when set, accepted via `X-Admin-Token` header
    # as an alternative to the email-based admin allowlist on admin
    # endpoints. Useful in dev for running scripted maintenance jobs
    # (sector_benchmarks recompute) without going through the auth flow.
    # Leave unset in production to force email-based auth.
    ADMIN_TOKEN: Optional[str] = None

    # Supabase
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: Optional[str] = None
    SUPABASE_ANON_KEY: Optional[str] = None

    # Gemini AI
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_MAX_TOKENS: int = 8192
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_CACHE_TTL: int = 3600  # seconds to cache API responses (saves quota)

    # Price-catalyst grounding (Gemini web-search "why did it move" for big moves)
    PRICE_CATALYST_AI_ENABLED: bool = True       # kill switch; false → FMP fallback
    PRICE_CATALYST_CACHE_TTL_HOURS: int = 24     # a move's reason is fresh daily

    # Geopolitical macro grounding (Gemini web-search scan of REAL current
    # macro-shock events — wars, trade wars, oil shocks, pandemics). One
    # market-wide scan shared across every ticker, refreshed on-demand. When
    # False, the Macro module shows deterministic FRED/FMP factors only.
    GEOPOLITICAL_INTEL_AI_ENABLED: bool = True   # kill switch; false → no geo factors
    GEOPOLITICAL_CACHE_TTL_DAYS: int = 7         # geopolitical regimes shift on weeks

    # Financial Modeling Prep
    FMP_API_KEY: str
    FMP_BASE_URL: str = "https://financialmodelingprep.com/stable"

    # CoinGecko (Demo API — free tier, 30 calls/min, 10K/month)
    COINGECKO_API_KEY: str = ""
    # Paid plans live on pro-api.coingecko.com; the Demo plan on api.coingecko.com. The
    # integration derives the auth header from this value (`CoinGeckoClient._auth_header`),
    # so changing plan is this one variable — never set a header separately.
    COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"
    # Default is the safety margin under the free Demo plan's 30/min. A paid plan allows far
    # more; leaving this at 25 throttles a paid key to demo speed.
    COINGECKO_MAX_CALLS_PER_MINUTE: int = 25

    # FRED (Federal Reserve Economic Data) — free tier ~120 req/min.
    # Used to ground the Macro module in real CPI / Fed Funds / yield-curve
    # data. When unset, the FRED-backed risk factors silently degrade
    # rather than crash the report (see app/integrations/fred.py).
    FRED_API_KEY: str = ""
    FRED_BASE_URL: str = "https://api.stlouisfed.org/fred"

    # US Census Bureau Data API — used by industry_tam_service to fetch
    # NAICS-precise industry revenue (e.g., NAICS 5112 Software Publishers).
    # Required for every Census API request; sign up free at
    # https://api.census.gov/data/key_signup.html. When unset, the Census
    # tier of the TAM chain silently degrades and FRED becomes the primary
    # source (see app/services/industry_tam_service.py).
    CENSUS_API_KEY: str = ""
    CENSUS_BASE_URL: str = "https://api.census.gov/data"

    # Industry dossier — AI-driven Phase B override toggle. When False,
    # the quarterly recompute job runs Phase A (Census/FRED) only and
    # skips the AI research overrides. Useful as a kill switch when
    # Gemini quota is exhausted or a bad research run shipped and we
    # need to revert to Census-only while debugging.
    INDUSTRY_OVERRIDE_AI_ENABLED: bool = True

    # Competitor intel — Phase 2 revenue-mix-aware peer selection via
    # Gemini grounded research. When False, ticker_report_data_collector
    # falls back to the Phase 1 deterministic peer-augmentation path
    # (FMP /stock-peers + industry-universe). Kill switch for quota
    # outages or bad-research-run incidents.
    COMPETITOR_INTEL_AI_ENABLED: bool = True

    # Moat intel — Phase 3D Gemini grounded fallback for moat pillars
    # the deterministic scorer left at confidence='low'. When False,
    # low-confidence pillars fall back to the legacy AI Stage A
    # dimension (ungrounded). Kill switch for quota outages.
    MOAT_INTEL_AI_ENABLED: bool = True

    # USPTO PatentsView API key (Phase 3C) — used by ip_intel_service to
    # fetch patent counts that boost the Intangible Assets pillar for
    # tech / biotech / pharma tickers. Optional: when missing, the
    # integration silently no-ops and the patents driver simply doesn't
    # contribute. Register at https://search.patentsview.org/docs/
    USPTO_API_KEY: str = ""

    # Vector/RAG
    EMBEDDING_DIMENSION: int = 1536
    VECTOR_SIMILARITY_THRESHOLD: float = 0.7
    RAG_TOP_K_RESULTS: int = 5

    # Research
    # DEEP_RESEARCH_TIMEOUT_SECONDS removed (2026-08-07): declared, settable, and read by
    # NOTHING. It was set to 30 on Railway while the live knob —
    # RESEARCH_PIPELINE_TIMEOUT_SECONDS below — ran at its 600s default, so the deploy
    # looked configured and was not. `extra="ignore"` means an existing .env keeps loading.
    # Hard ceiling on a single deep-research agent run (Stage A collect →
    # agentic rounds → Stage B narratives). research_service wraps
    # `agent.run` in asyncio.wait_for(this) so a hung pipeline raises
    # TimeoutError → the failure path refunds the user's credits instead of
    # leaving the report stranded in "processing" forever. A DEDICATED new
    # setting (NOT DEEP_RESEARCH_TIMEOUT_SECONDS above, which an existing .env
    # pins to 30s). Kept STRICTLY below the reconciliation sweep's 900s
    # stuck-threshold so a live worker always wins the refund race with a
    # clean, specific error.
    RESEARCH_PIPELINE_TIMEOUT_SECONDS: int = 600
    # Per-call timeout for individual Gemini SDK requests. The SDK's
    # default is unbounded — without this guard a hung network read
    # parks the whole report-generation task forever (seen as a card
    # stuck at "Deep research complete, synthesizing..." 55%). On
    # timeout, _call_with_timeout raises GeminiTimeoutError (a TimeoutError
    # SUBCLASS, so existing `except asyncio.TimeoutError` handlers are
    # unaffected), @async_retry gives it the OWN budget below, and the
    # caller's existing sentinel fallback returns instead of hanging.
    GEMINI_REQUEST_TIMEOUT_SECONDS: int = 90
    # Retries for a per-call Gemini TIMEOUT. Default 0 = do not retry.
    #
    # It previously fell through to @async_retry's GENERIC branch and WAS retried,
    # contradicting the docstrings: one hung call cost 90s + backoff + 90s ≈ 182s
    # against the 600s RESEARCH_PIPELINE_TIMEOUT_SECONDS ceiling, with ~15 parallel
    # Stage-B narratives per report. A read that stalled for a full 90s is a stuck
    # connection, not a blip, so the expected payoff of a retry is low and the
    # latency cost is high. Kept as a setting (not deleted) so the judgement is one
    # env var away — note pydantic-settings reads it at startup, so changing it on
    # Railway needs a RESTART, not just a redeploy.
    GEMINI_TIMEOUT_MAX_RETRIES: int = 0
    # Consecutive per-call timeouts (no success in between) before the integration
    # escalates from WARNING to a single ERROR. Individual timeouts are WARNING so
    # they stop paging Sentry; this is what keeps a SUSTAINED outage visible without
    # emitting one issue per call.
    GEMINI_TIMEOUT_ALERT_STREAK: int = 5

    # Bounded concurrency for research-report generation. A single user may
    # have at most MAX_CONCURRENT_REPORTS_PER_USER reports in flight
    # (pending/processing) at once — e.g. 4 personas on one ticker, or 1
    # persona on 4 tickers. Enforced pre-charge in the /research/generate
    # endpoint (no credits burned on rejection). MAX_CONCURRENT_AGENT_RUNS is
    # a global ceiling for the optional defense-in-depth semaphore around the
    # agent run (added now; wired only if Gemini/Railway load demands it).
    MAX_CONCURRENT_REPORTS_PER_USER: int = 4
    MAX_CONCURRENT_AGENT_RUNS: int = 8

    # Global admission backstop (fast-fail under overload). Beyond the per-user
    # cap, bound the TOTAL reports in flight (pending/processing) across ALL
    # users in the current close cycle. Over this, /research/generate fast-fails
    # pre-charge with 409 SYSTEM_BUSY ("try again shortly") instead of piling
    # unbounded agent runs onto the single event loop. The real pacing is
    # MAX_CONCURRENT_AGENT_RUNS (the semaphore); this just sheds load past a
    # safe backlog. Set 0 to disable the gate.
    MAX_GLOBAL_INFLIGHT_REPORTS: int = 150

    # ── GET /stocks/{ticker}/report admission ─────────────────────────────────
    # The synchronous direct path, which until now had NO per-caller limit and no
    # admission gate at all. A cache MISS there costs ~17 Gemini + ~20 FMP calls,
    # and the cache is keyed (ticker, persona) so looping tickers never hits it —
    # an unauthenticated loop could burn the whole upstream budget.
    #
    # Per-caller window (per-install for guests). A report is ~20x a chat turn, so
    # this sits far below CHAT_RATE_LIMIT_PER_MINUTE.
    REPORT_RATE_LIMIT_PER_MINUTE: int = 3
    # Global in-flight backstop for that endpoint. Unlike MAX_GLOBAL_INFLIGHT_REPORTS
    # (which counts DB rows for the fire-and-forget /research/generate path), the
    # direct path is synchronous, so a simple in-process counter IS the true
    # concurrency gauge. Over this → 409 SYSTEM_BUSY. Set 0 to disable.
    REPORT_GET_MAX_INFLIGHT: int = 24

    # Free AI reports per INSTALL per month for signed-out users (migration 106).
    # Guests can't be metered by user_credits — that table is FK-bound to
    # public.users and a per-install id has no users row — so they get their own
    # durable budget keyed off the X-Guest-Id-derived uuid.
    #
    # Deliberately BELOW the signed-in Free tier (50 credits = 2 reports/month) so
    # creating an account is an upgrade. It was previously unlimited, which made
    # signing in a strict downgrade and gave users no reason to register.
    # Set 0 to require sign-in for any report generation.
    GUEST_REPORT_MONTHLY_LIMIT: int = 1

    # Unified credit pricing (approved 2026-07-26; see migration 100 + the pricing
    # plan). One credit == one "Ask Cay AI" chat turn. A full report is ~20x a chat
    # turn by token/$ weight, so it costs 20 credits. Tier monthly allocations live
    # in the plan_credits DB table (free 50 / pro 1200 / premium="Max" 4000). These
    # constants are the per-ACTION debit amounts; the enforcement phase charges them
    # against the monthly pool (chat is not yet wired to the pool).
    CHAT_CREDIT_COST: int = 1
    REPORT_CREDIT_COST: int = 20

    # ── In-app purchase (StoreKit 2 / App Store Server) ────────────────────────
    # Entitlement comes from Apple-signed transactions, verified locally against
    # Apple's certificate chain — the client is never trusted to say what it bought.
    IAP_BUNDLE_ID: str = "com.phan.caydex"
    # One of: "Production" | "Sandbox" | "Xcode" | "LocalTesting".
    #   Xcode / LocalTesting     → a StoreKit configuration file. Root-cert validation is
    #                              skipped by Apple's library, so local testing needs no certs.
    #   Sandbox / Production     → REQUIRES Apple's root certificates (see
    #                              IAP_ROOT_CERT_DIR) or verification fails closed.
    #
    # DEFAULTS TO PRODUCTION, and that default is a security control, not a convenience.
    # In XCODE / LOCAL_TESTING, Apple's library does NOT verify the signature at all —
    # `SignedDataVerifier._decode_signed_object` calls
    # `jwt.decode(signed_obj, options={"verify_signature": False})` and returns immediately.
    # So with a local environment configured, `POST /billing/verify` accepts ANY unsigned JWT
    # as a genuine Apple transaction: forge {"productId": "com.phan.caydex.max.monthly", ...},
    # post it, receive the Max tier and 4000 credits, for free, forever.
    #
    # This previously defaulted to "LocalTesting", so a deploy that simply forgot to set the
    # variable — it is an unchecked item on the launch checklist — would have shipped exactly
    # that. Defaulting to Production inverts the failure: an unconfigured deploy has no root
    # certificates, `_load_root_certificates` raises AppStoreNotConfigured, and the endpoint
    # returns 503 instead of granting free subscriptions. Local work sets IAP_ENVIRONMENT=Xcode
    # explicitly (see the launch checklist §6b), which is the correct place for that knowledge.
    IAP_ENVIRONMENT: str = "Production"
    # Numeric App Store app id (App Store Connect → App Information → Apple ID).
    # Required for Production verification; unknown until the ASC record exists.
    IAP_APP_APPLE_ID: Optional[int] = None
    # Directory holding Apple's PUBLIC root CA certificates (AppleRootCA-G3.cer etc.),
    # downloadable from https://www.apple.com/certificateauthority/. Not committed —
    # they are Apple infrastructure, not our secret, but also not ours to redistribute.
    IAP_ROOT_CERT_DIR: str = "certs/apple"

    # ── Associated domains (passkeys + Password AutoFill) ──────────────────────
    # Served by GET /.well-known/apple-app-site-association. NEITHER IS A SECRET: both ship
    # inside every copy of the app, and Apple has to be able to fetch them unauthenticated.
    # Settings rather than literals so a rename or a second bundle (staging) is a config change.
    APPLE_TEAM_ID: str = "WG697LVCS9"
    APPLE_BUNDLE_ID: str = "com.phan.caydex"
    # StoreKit product id → tier. The product ids must match App Store Connect exactly.
    # Kept in config rather than the DB so a typo can't silently grant the wrong tier.
    IAP_PRODUCT_PRO_MONTHLY: str = "com.phan.caydex.pro.monthly"
    IAP_PRODUCT_MAX_MONTHLY: str = "com.phan.caydex.max.monthly"
    # CONSUMABLE credit packs ("Buy Credits"). How many credits each pack grants lives in the
    # `credit_packs` DB table, NOT here — deliberately mirroring how tier allocations live in
    # `plan_credits` rather than in config. One source of truth per concern: config owns the
    # product IDENTITY (a typo must not silently map a purchase to the wrong product), the DB
    # owns the AMOUNT (tunable without a deploy).
    #
    # The prefix is what makes a product id *recognisable* as a pack before any DB lookup, so
    # an unmapped id still raises UnknownProduct instead of being silently treated as a tier.
    IAP_CREDIT_PACK_PREFIX: str = "com.phan.caydex.credits."
    # Hard ceiling on a single grant. `credit_packs.credits` is a DB value and this is the one
    # thing standing between a bad row (or a bad hand-edit in Studio) and an unbounded credit
    # mint on a verified purchase. Sized well above the largest real pack (1,200).
    IAP_MAX_PACK_CREDITS: int = 10_000
    # Shared secret Apple echoes in App Store Server Notifications, if configured. When set,
    # the webhook additionally requires it — defence in depth on top of JWS verification.
    IAP_WEBHOOK_SHARED_SECRET: Optional[str] = None

    # ── APNs push notifications (token-based auth via a .p8 key) ───────────────
    # All optional: with any unset, push is simply disabled (PushService.enabled
    # is False) and registration still records device tokens for later. NEVER
    # commit the .p8 — set APNS_AUTH_KEY to the key file's CONTENTS via env/secret.
    APNS_KEY_ID: Optional[str] = None          # 10-char Key ID from the .p8
    APNS_TEAM_ID: Optional[str] = None         # Apple Developer Team ID (WG697LVCS9)
    APNS_AUTH_KEY: Optional[str] = None        # PEM contents of the AuthKey_XXXX.p8
    APNS_BUNDLE_ID: str = "com.phan.caydex"    # apns-topic
    # "production" → api.push.apple.com ; "sandbox" → api.sandbox.push.apple.com
    APNS_ENV: str = "sandbox"
    # Per-user daily ceiling on ALERTS, across every ticker.
    #
    # The dedup key is per (user, ticker, day), which stops the SAME alert repeating
    # but does nothing about VOLUME: in a market-wide selloff, ten watchlist tickers
    # each crossing the Unusual threshold means ten separate notifications in one
    # afternoon. That is the classic way an app teaches people to disable its
    # notifications — and once disabled, iOS never re-prompts.
    #
    # Deliberately low. A user who genuinely wants every move has the Updates tab;
    # push is for the handful worth an interruption.
    #
    # NOTE: this is now the WATCHLIST category's cap specifically, not a global one.
    # Per-category ceilings live in `notification_kinds.CATEGORY_DAILY_CAPS`; this knob
    # is threaded through `category_cap()` so an operator who had already tuned it keeps
    # the behaviour they configured instead of finding it silently inert. <= 0 disables.
    PUSH_MAX_ALERTS_PER_USER_PER_DAY: int = 3

    # ── Notification pipeline ─────────────────────────────────────────────────
    # Global dry run. Runs the ENTIRE dispatch pipeline — audience, child+master
    # preference, per-category cap, quiet hours, the dedup claim, the ledger/inbox row,
    # the badge count — and replaces ONLY the APNs POST with a log line and
    # push_state='dry_run'.
    #
    # Two jobs, both load-bearing:
    #   1. It makes every sender exercisable locally with no Apple configuration and no
    #      physical device, which is the only way "does this notification actually fire?"
    #      is answerable before shipping. The Simulator cannot reach APNs.
    #   2. It is the GLOBAL KILL SWITCH. One env var stops all pushes while leaving the
    #      in-app inbox fully working, so a misbehaving sender degrades instead of
    #      needing a rollback.
    PUSH_DRY_RUN: bool = False

    # `main.py` skips EVERY background task when ENVIRONMENT == "development" (Railway
    # owns them), which means no notification sender can be exercised locally at all.
    # This spawns ONLY the notification loops in local dev, leaving the FMP-heavy
    # pre-warmers off so a laptop does not burn the quota.
    RUN_NOTIFICATION_JOBS_LOCALLY: bool = False

    # ET hour after which each daily sender may run. `_run_scheduled_notification_senders`
    # wakes hourly and skips a sender until the local ET hour reaches its value; the real
    # once-per-day guarantee comes from `claim_notification_job`.
    #
    # ⚠️ These three were REFERENCED by main.py but never DEFINED here, and `Settings` uses
    # `extra="ignore"`, so `settings.EARNINGS_NOTIFY_HOUR_ET` raised AttributeError. The
    # tuple that reads them is built BEFORE the `while True` and outside any try, so the
    # whole task died ~90s after every startup — meaning the earnings and smart-money
    # senders never ran at all, silently, with only a task-crash log to show for it.
    #
    # Values match what main.py's docstring already documented: earnings after the close,
    # smart money in the evening once Form 4s have landed.
    EARNINGS_NOTIFY_HOUR_ET: int = 16
    SMART_MONEY_NOTIFY_HOUR_ET: int = 18
    # An hour after smart money, deliberately: it derives from the same signals, so the
    # specific alert should land first and the per-category caps keep this from stacking.
    PROFILE_MATCH_NOTIFY_HOUR_ET: int = 19

    # How long a `notification_job_state.claim_at` may sit before another instance may
    # steal it. Sized well above the slowest sender (insider, ~200 FMP calls) so a slow
    # run is never stolen mid-flight, and low enough that a hard-killed instance frees
    # the job within a quarter hour.
    NOTIFICATION_JOB_STALE_SECONDS: int = 900

    # Quiet-hours flush cadence. Runs 24/7 (NOT gated on market hours — a European
    # user's 07:00 quiet-end is 01:00 ET, when the market sweeper is asleep).
    NOTIFICATION_DISPATCH_INTERVAL_SECONDS: int = 60
    # Rows handed out per flush cycle. Bounded so one instance cannot claim the whole
    # backlog while another idles.
    NOTIFICATION_DISPATCH_BATCH: int = 100
    # A notification deferred longer than this is marked `failed` and never sent.
    # A 14-hour-late "AAPL moved 8%" is misinformation, not a notification — the inbox
    # row survives, so the information is not lost, only the buzz.
    NOTIFICATION_MAX_DEFER_HOURS: int = 12

    # Price alerts (migration 125). These four MUST be declared here, not left to a
    # Railway variable: `model_config` sets `extra="ignore"`, so an undeclared env var is
    # silently dropped and every read raises AttributeError. They were missing until
    # 2026-08-14, which killed `run_price_alert_loop` ~30s after every boot (the interval
    # read sits outside its `while True`) and 503'd all four /alerts/price routes.
    #
    # Evaluation cadence, gated on `session_phase() != "closed"` (04:00-20:00 ET). One
    # cycle is a single bulk quote call, so this is cheap; the loop floors it at 15s.
    PRICE_ALERT_INTERVAL_SECONDS: int = 60
    # Caps, surfaced to iOS on every list response (`PriceAlertListResponse`) so the UI
    # can disable the bell at the cap rather than fail the POST. Keep in step with the
    # schema defaults in `schemas/price_alerts.py` — iOS falls back to those before its
    # first successful fetch.
    PRICE_ALERT_MAX_PER_USER: int = 20
    PRICE_ALERT_MAX_PER_TICKER_PER_USER: int = 3
    # How far price must retreat past the threshold before a `repeat` rule re-arms,
    # as a fraction. Without a band, a price oscillating either side of the limit
    # notifies on every cycle. Matches `price_alert_engine.evaluate_alert`'s own default.
    PRICE_ALERT_REARM_PCT: float = 0.005

    # Gemini quota (429) handling. Instead of skipping retries on a rate-limit
    # error, back off and retry a bounded number of times — paired with the
    # agent-run semaphore this recovers transient 429s rather than degrading a
    # report to sentinel narratives. A module-level circuit breaker opens after
    # GEMINI_QUOTA_CIRCUIT_THRESHOLD consecutive quota errors and fails fast for
    # GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS so a sustained outage stops hammering.
    GEMINI_QUOTA_MAX_RETRIES: int = 2
    GEMINI_QUOTA_RETRY_DELAY_SECONDS: float = 5.0
    GEMINI_QUOTA_CIRCUIT_THRESHOLD: int = 20
    GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS: float = 30.0

    # Stage-B context caching. The ~15 parallel narrative calls per report all
    # share the same big financial-evidence blob + persona system prompt. When
    # enabled, that shared prefix is uploaded ONCE to a Gemini CachedContent and
    # each call bills only its tiny per-field instruction plus the cached prefix
    # at the discounted read rate (~25% of input) — cutting per-report input
    # tokens materially (TPM is the binding constraint on Flash). Fail-safe: any
    # cache problem (below min-token size, SDK/quota error) silently falls back
    # to inline prompts, so report quality never degrades. Kill switch.
    GEMINI_CONTEXT_CACHE_ENABLED: bool = True
    GEMINI_CONTEXT_CACHE_TTL_MINUTES: int = 10

    # Multi-agent chat (Phase 3): a cheap router classifies each question and routes to a topic
    # specialist (valuation/technicals/macro/…); genuinely cross-domain questions run several
    # specialists in parallel + a synthesizer. Kill switch → the plain single-agent streaming path.
    CHAT_MULTI_AGENT_ENABLED: bool = True

    # How many specialist lenses a cross-domain ("synthesize") turn may run in parallel.
    # This is the single most expensive path in chat and the only one that is not
    # comfortably profitable: each specialist is its own agentic stream (`max_rounds=2`,
    # its own tool fan-out), and the merge is a further call on top. Measured, a 3-lens
    # turn costs roughly 4x a single-lens one — which puts it AT OR BELOW the net revenue
    # of the 1 credit it charges on the Max tier ($0.0085/credit after Apple's 15%).
    #
    # Chat is priced at a flat 1 credit deliberately and permanently (the price basis
    # would otherwise be an LLM classification, i.e. nondeterministic, and raising the
    # price after credit packs are sold devalues a consumable — App Store 3.1.1). So the
    # variance is bounded HERE, on the cost side, rather than passed to the user.
    #
    # 2 keeps the cross-domain answer genuinely multi-lens while cutting the worst case by
    # a third. Raising it back to 3 is a real money decision, not a tuning knob.
    CHAT_MAX_SPECIALISTS: int = 2

    # Chat RAG quality (Phase 4). Query rewrite resolves follow-ups ("why is it down?") into a
    # standalone search query (only when the message looks context-dependent, so standalone questions
    # skip the call). Rerank pulls a wider candidate set then LLM-scores it down to top-K. Both are
    # cheap flash-lite calls with graceful fallback; kill switches here.
    CHAT_QUERY_REWRITE_ENABLED: bool = True
    CHAT_RERANK_ENABLED: bool = True
    RAG_RERANK_CANDIDATES: int = 20  # wider vector search before reranking down to RAG_TOP_K_RESULTS

    # Master switch for chat RAG. OFF because the corpus is EMPTY and nothing in this
    # repo ingests it: `book_chunks` / `article_chunks` / `company_filing_chunks` are
    # written by no code here (migration 086's header says as much), and
    # `generate_embedding` has exactly one caller — the query side. Left on, every
    # single chat turn paid for an embedding call plus a Supabase RPC to retrieve
    # nothing, and ~40% of turns also paid for a flash-lite query rewrite first — two
    # network round trips on the time-to-first-token path for a guaranteed empty result.
    # Deliberately a flag and NOT an emptiness probe: a per-turn COUNT is another round
    # trip, and it would fail OPEN on a transient DB error (silently resuming the waste).
    # Flip this to True on the day an ingestion pipeline lands.
    CHAT_RAG_ENABLED: bool = False

    # Per-turn model routing. `chat_router.route_question()` already classifies every
    # turn on the critical path and the result was used only to pick a prompt lens —
    # the classification is already paid for, so choosing a model from it costs nothing.
    # A conceptual question with no ticker and no on-screen data ("what is a P/E
    # ratio?") does not need the flagship model; measured, that turn is ~900 prompt
    # tokens and the cheap model answers it for roughly a quarter of the price.
    # Ships OFF: this is the one cost lever that can change what a user reads, so it
    # wants an offline eval plus real answers reviewed before it is switched on
    # (scripts/eval_model_routing.py). See `select_model` for the fail-closed rules.
    CHAT_MODEL_ROUTING_ENABLED: bool = False
    CHAT_CHEAP_MODEL: str = "gemini-2.5-flash-lite"

    # Output ceiling for CHAT only (report generation keeps GEMINI_MAX_TOKENS=8192).
    # Output is the majority of a chat turn's cost, and the chat system prompt already
    # demands "SHORT, direct… AT MOST 2-3 brief supporting bullet points" — measured,
    # real answers land around 150 tokens. An 8192 ceiling on a 150-token brief only
    # ever binds when something has gone wrong (a loop, a runaway list), so this is a
    # blast-radius cap, not a style control: it should never fire on a healthy turn.
    CHAT_MAX_OUTPUT_TOKENS: int = 1200

    # Reuse the stored rolling summary instead of regenerating it every turn.
    # `_condense_history` fires a flash-lite call on EVERY turn once a chat passes
    # `_RECENT_TURNS`, re-summarising almost the same older messages each time. The
    # summary is persisted on the session and refreshed only after this many new
    # MESSAGES have entered the older slice. One chat TURN writes two messages (the user's
    # and the assistant's), so this value is a message count and the cadence it buys is
    # N/2 turns — 4 here means one summariser call every 2 turns, not every 4. The comment
    # here and in migration 130 both said "one call per N turns", overstating the saving 2x.
    # Left at 4 deliberately: halving the LLM calls is still the win, and a longer watermark
    # trades bounded staleness in the summary for a smaller one.
    # The trade is bounded staleness in the SUMMARY only; the recent turns are always
    # verbatim, so nothing the user just said can be stale.
    CHAT_SUMMARY_REFRESH_AFTER_MESSAGES: int = 4

    # Apply the reader's learning preferences to chat answers (Pro/Max only).
    #
    # ⚠️ SHIPS OFF, AND THE GATE IS LEGAL, NOT TECHNICAL. Turning this on changes the app
    # from producing output that is identical for everyone to output composed for one
    # reader. The design keeps that on the safe side of the line — it personalizes
    # PEDAGOGY (what to cover first, at what reading level), never a view about a
    # security, and the profile deliberately excludes the five suitability inputs — but
    # the live Terms §2 still promise output is "general and impersonal", and that
    # paragraph needs its carve-out before this is enabled. See the plan's §1.
    #
    # Independent of the tier gate on purpose: `signals_unlocked` answers "may this user
    # have it", this answers "does the feature exist yet".
    CHAT_PERSONALIZATION_ENABLED: bool = False

    # Cross-session memory (rung 2): what the reader has been asking about, carried
    # between conversations. Costs no LLM — both facts are already computed each turn
    # (the router's specialist, the session's ticker) and were simply being discarded.
    #
    # Separate from CHAT_PERSONALIZATION_ENABLED so the two can be judged apart: the
    # preference block is something the reader TOLD us, this is something we OBSERVED,
    # and a reader may reasonably feel differently about the second. It is gated behind
    # the same tier + consent checks regardless, so this flag can only ever narrow.
    CHAT_MEMORY_FACTS_ENABLED: bool = False

    # ── Chat security / abuse & cost controls (denial-of-wallet, OWASP LLM10) ──
    # Input hygiene: a normalized+stripped user message over CHAT_MESSAGE_MAX_CHARS
    # is rejected with a friendly CHAT_MESSAGE_TOO_LONG (the Pydantic HARD_MAX is a
    # cheaper structural 422 for absurd payloads before any work). Client `context`
    # (grounding text injected into the SYSTEM instruction) is normalized + truncated
    # to CHAT_CONTEXT_MAX_CHARS. CHAT_PROMPT_MAX_CHARS is the final assembled-input
    # ceiling (defense-in-depth so a fat context can't blow up token spend).
    CHAT_MESSAGE_MAX_CHARS: int = 4000       # friendly limit (~1k tokens) enforced in-endpoint
    CHAT_MESSAGE_HARD_MAX: int = 8000        # structural Pydantic 422 ceiling
    CHAT_CONTEXT_MAX_CHARS: int = 8000       # client-supplied grounding, truncated not rejected
    CHAT_PROMPT_MAX_CHARS: int = 24000       # assembled-prompt input cap (~6k tokens)
    # Per-user (per-install for guests) request rate limit on the send + stream
    # endpoints, sharing one bucket so switching endpoints can't dodge it.
    CHAT_RATE_LIMIT_PER_MINUTE: int = 15
    # Durable per-user daily budget (Supabase chat_usage_budget, migration 096).
    # A turn is claimed atomically BEFORE the Gemini call; over the cap → 409
    # CHAT_DAILY_LIMIT_REACHED. Token count is best-effort observability + a soft
    # secondary ceiling. Set the turn limit to 0 to disable chat generation entirely.
    CHAT_DAILY_TURN_LIMIT: int = 60
    # Anti-rotation ceiling for GUESTS only, keyed on `trusted_client_ip` rather than the
    # client-chosen `X-Guest-Id`. The per-install budget above is the fair-use limit; this is
    # the one a caller cannot reset by minting a new header. Set well above the per-install
    # figure so a shared network (office, campus, CGNAT) is not throttled in normal use — it
    # bounds abuse, it is not a second fair-use limit. Signed-in users are exempt: their bucket
    # is a real account id, which is not rotatable.
    CHAT_DAILY_TURN_LIMIT_PER_IP: int = 300
    CHAT_DAILY_TOKEN_LIMIT: int = 200000

    # A charged chat turn earns the session ONE free follow-up, valid for this many seconds.
    #
    # Chat is a flat 1 credit per turn and stays that way, so the cost of the decision is
    # "meter anxiety": a user who must spend a credit to ask "what does that mean?" learns
    # to stop asking, and asking is the retention loop. This buys that back without making
    # the price unpredictable — the allowance is granted by a turn the user ALREADY paid
    # for, so nothing about the cost of sending a message becomes uncertain.
    #
    # 300s (5 min) is long enough to read an answer and type a real follow-up, short enough
    # that it is not a free-chat mode. The bound that matters is structural, not temporal:
    # only a CHARGED turn grants one, so the worst case is 2 turns per credit no matter how
    # this is tuned. Set to 0 to disable (grant_free_followup then also CLEARS live windows,
    # so it is a true kill switch rather than a slow drain).
    CHAT_FREE_FOLLOWUP_SECONDS: int = 300

    # Report pre-warming. After each market close the persona-neutral
    # ticker_data_cache goes stale; warming the top watchlist tickers means the
    # first report (and any same-session burst) skips re-collecting it. This runs
    # the FULL persona-neutral collection — the ~20-call FMP fan-out PLUS the
    # persona-neutral grounded precompute (which for a cold ticker makes some
    # Gemini-grounded calls). Idempotent: a still-fresh collection is a one-DB-read
    # no-op, so real work only happens right after a new close cycle.
    REPORT_PREWARM_ENABLED: bool = True
    REPORT_PREWARM_TOP_N: int = 20
    REPORT_PREWARM_INTERVAL_SECONDS: int = 3600

    # Home Daily Scanners pre-warm: keeps Movers + Volume (and, free of charge,
    # Skeptical Money — built in the same get_scanners() pass) hot during the
    # regular session. Interval is BELOW the 20-min scanner cache TTL so the cache
    # is refreshed before it expires (no cold gap mid-session). ~135 FMP calls per
    # build, builds ≥15 min apart, gated to market hours → ~18% of a Premium
    # minute's budget. Raise the interval to 1800 on FMP Starter (300/min).
    SCANNER_PREWARM_ENABLED: bool = True
    SCANNER_PREWARM_INTERVAL_SECONDS: int = 900

    # On-view report pre-warm: when a user opens a ticker's detail view, iOS
    # fires POST /stocks/{ticker}/prewarm-report, which warms the persona-neutral
    # ticker_data_cache so a later Generate Analysis skips the ~20-call FMP
    # fan-out. Bounded across DISTINCT tickers by REPORT_PREWARM_DETAIL_CONCURRENCY
    # (same-ticker collapses via _INFLIGHT; fresh tickers are a cheap no-op).
    # NOTE: this runs the FULL persona-NEUTRAL collection (_collect_fresh) — the
    # ~20-call FMP fan-out PLUS the persona-neutral GROUNDED precompute (which,
    # for a cold ticker, makes some Gemini-grounded calls: moat grounding for
    # most names, price-catalyst for big movers, a market-wide geopolitical
    # scan). It skips only the per-persona Stage-A scoring + Stage-B narratives.
    # So it is a latency/cost-SHIFT (and bounded Gemini spend), NOT free and NOT
    # a throughput increase (reports stay MAX_CONCURRENT_AGENT_RUNS-bound).
    # The endpoint is rate-limited + capped at REPORT_PREWARM_MAX_INFLIGHT
    # in-flight warms so a distinct-cold-ticker burst can't drain quota. Kill switch.
    REPORT_PREWARM_ON_VIEW_ENABLED: bool = True
    REPORT_PREWARM_DETAIL_CONCURRENCY: int = 3

    # ── Whale profile pre-warm ───────────────────────────────────────────
    # `whale_profile_cache` holds an ASSEMBLED profile and is written only by the request
    # path, so after a restart all 56 whales are Tier-2 cold and the first visitor to
    # each pays the rebuild. This warms them in the background instead.
    #
    # Cheap by construction: 55 of 56 whales are served from a stored
    # `whale_filing_snapshots` row and make ZERO FMP calls; only a whale with no snapshot
    # at all reaches the FMP path.
    WHALE_PREWARM_ENABLED: bool = True
    # Distinct-whale concurrency. The postgrest client is SYNCHRONOUS, so parallel builds
    # serialize on the event loop anyway — this exists to stop them from also starving
    # real user requests. Keep it low.
    WHALE_PREWARM_CONCURRENCY: int = 3
    REPORT_PREWARM_MAX_INFLIGHT: int = 50

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 60

    # Timeouts
    HTTP_TIMEOUT_SECONDS: int = 30

    # Logging
    LOG_LEVEL: str = "INFO"

    # Error monitoring (Sentry). Optional — an unset DSN keeps the SDK fully inert,
    # so local dev is a no-op. Set SENTRY_DSN on Railway (prod) only. The digest
    # job's READ creds (SENTRY_API_TOKEN / SENTRY_ORG / SENTRY_PROJECT) are NOT
    # here — scripts/error_digest.py reads them straight from the environment, so it
    # runs standalone and the API process never needs them.
    SENTRY_DSN: Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0   # 0 = errors only (no perf tracing); cheap

    # Disclaimer
    LEGAL_DISCLAIMER: str = (
        "For educational purposes only. Not financial advice. "
        "AI generated content may be inaccurate. Always do your own research "
        "and consult a qualified financial advisor."
    )

    model_config = SettingsConfigDict(
        # ABSOLUTE, anchored to this file — not the bare ".env", which pydantic resolves
        # against the CURRENT WORKING DIRECTORY. Start uvicorn from the repo root (which is
        # what `--app-dir backend` invites, since it only adds to sys.path and does NOT chdir)
        # and pydantic looked for `<repo-root>/.env`, found nothing, and failed with five
        # "Field required" errors that read like missing secrets rather than a missing file.
        #
        # `source backend/.env` is not a workaround: the file has no `export` prefixes, so
        # those assignments stay in the shell and are never inherited by the child process.
        #
        # Real environment variables still win over the file, so Railway is unaffected.
        env_file=_BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
