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
    DEEP_RESEARCH_TIMEOUT_SECONDS: int = 120
    # Per-call timeout for individual Gemini SDK requests. The SDK's
    # default is unbounded — without this guard a hung network read
    # parks the whole report-generation task forever (seen as a card
    # stuck at "Deep research complete, synthesizing..." 55%). On
    # timeout, generate_text / generate_json raise asyncio.TimeoutError,
    # the @async_retry decorator skips it (not a quota error), and the
    # caller's existing sentinel fallback returns instead of hanging.
    GEMINI_REQUEST_TIMEOUT_SECONDS: int = 90

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 60

    # Timeouts
    HTTP_TIMEOUT_SECONDS: int = 30

    # Logging
    LOG_LEVEL: str = "INFO"

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
