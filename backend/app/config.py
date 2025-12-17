"""
Application Configuration
Handles all environment variables and settings with proper validation.
Security: All API keys are server-side only as per Section 5.3
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Uses pydantic-settings for validation and type safety.
    """

    # Application Settings
    APP_NAME: str = "AI Value Investor API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production

    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ALLOWED_ORIGINS: list[str] = ["*"]  # Update for production

    # Security Settings (Section 5.3)
    SECRET_KEY: str  # Required - for JWT token generation
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Supabase Configuration (Section 2.4, 2.5)
    SUPABASE_URL: str  # Required
    SUPABASE_KEY: str  # Required - Service role key (server-side only)
    SUPABASE_JWT_SECRET: Optional[str] = None  # For validating Supabase Auth tokens

    # Database Configuration
    DATABASE_URL: Optional[str] = None  # Direct PostgreSQL connection if needed
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Google Gemini API (Section 3.3, 4.3.1)
    GEMINI_API_KEY: str  # Required - must be server-side only
    GEMINI_MODEL: str = "gemini-1.5-pro"  # Requirement: above 1.5 version
    GEMINI_MAX_TOKENS: int = 8192
    GEMINI_TEMPERATURE: float = 0.7

    # Financial Modeling Prep API (Section 3.3)
    FMP_API_KEY: str  # Required
    FMP_BASE_URL: str = "https://financialmodelingprep.com/api/v3"

    # News API Configuration (Section 3.3)
    NEWS_API_KEY: Optional[str] = None  # NewsAPI.org
    SERP_API_KEY: Optional[str] = None  # SerpApi alternative
    FINANCIAL_NEWS_API_KEY: Optional[str] = None  # Future: FinancialNewsAPI.org

    # Redis Configuration (for background jobs and caching)
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_SECONDS: int = 3600  # 1 hour default cache

    # Background Job Settings (Section 4.1)
    ENABLE_BACKGROUND_JOBS: bool = True  # Disable for local development if needed
    NEWS_SCRAPING_SCHEDULE: str = "0 7,16 * * *"  # 7:30 AM & 4:00 PM MT
    WIDGET_UPDATE_SCHEDULE: str = "0 7,16 * * *"  # Twice daily (Section 4.2)

    # Business Rules (Section 5.5)
    FREE_TIER_DEEP_RESEARCH_LIMIT: int = 1
    PRO_TIER_DEEP_RESEARCH_LIMIT: int = 10
    PREMIUM_TIER_DEEP_RESEARCH_LIMIT: int = -1  # Unlimited

    # Performance Settings (Section 5.1)
    WIDGET_RENDER_TIMEOUT_SECONDS: int = 2
    DEEP_RESEARCH_TIMEOUT_SECONDS: int = 30

    # Vector Database Settings (for RAG - Section 4.4)
    EMBEDDING_DIMENSION: int = 1536  # Matches database schema
    VECTOR_SIMILARITY_THRESHOLD: float = 0.7
    RAG_TOP_K_RESULTS: int = 5

    # AI Model Versioning
    AI_MODEL_VERSION: str = "gemini-1.5-pro-20241215"

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # json or text

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60

    # External Services Timeouts
    HTTP_TIMEOUT_SECONDS: int = 30

    # Disclaimer (Section 5.2)
    LEGAL_DISCLAIMER: str = "For educational purposes only. Not financial advice."

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Create and cache settings instance.
    This ensures settings are loaded once and reused.

    Returns:
        Settings: Application settings instance
    """
    return Settings()


# Convenience accessor
settings = get_settings()
