"""
Application Configuration
Environment variables with Pydantic validation.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


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
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
