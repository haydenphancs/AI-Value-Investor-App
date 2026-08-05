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
    COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"

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
    DEEP_RESEARCH_TIMEOUT_SECONDS: int = 120  # legacy/unused (pinned to 30 in .env)
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
    # timeout, generate_text / generate_json raise asyncio.TimeoutError,
    # the @async_retry decorator skips it (not a quota error), and the
    # caller's existing sentinel fallback returns instead of hanging.
    GEMINI_REQUEST_TIMEOUT_SECONDS: int = 90

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
    # StoreKit product id → tier. The product ids must match App Store Connect exactly.
    # Kept in config rather than the DB so a typo can't silently grant the wrong tier.
    IAP_PRODUCT_PRO_MONTHLY: str = "com.phan.caydex.pro.monthly"
    IAP_PRODUCT_MAX_MONTHLY: str = "com.phan.caydex.max.monthly"
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
    PUSH_MAX_ALERTS_PER_USER_PER_DAY: int = 3

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

    # Chat RAG quality (Phase 4). Query rewrite resolves follow-ups ("why is it down?") into a
    # standalone search query (only when the message looks context-dependent, so standalone questions
    # skip the call). Rerank pulls a wider candidate set then LLM-scores it down to top-K. Both are
    # cheap flash-lite calls with graceful fallback; kill switches here.
    CHAT_QUERY_REWRITE_ENABLED: bool = True
    CHAT_RERANK_ENABLED: bool = True
    RAG_RERANK_CANDIDATES: int = 20  # wider vector search before reranking down to RAG_TOP_K_RESULTS

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
