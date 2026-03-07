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

    # Supabase
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: Optional[str] = None
    SUPABASE_ANON_KEY: Optional[str] = None

    # Gemini AI
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-1.5-pro"
    GEMINI_MAX_TOKENS: int = 8192
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_CACHE_TTL: int = 3600  # seconds to cache API responses (saves quota)

    # Financial Modeling Prep
    FMP_API_KEY: str
    FMP_BASE_URL: str = "https://financialmodelingprep.com/stable"

    # Vector/RAG
    EMBEDDING_DIMENSION: int = 1536
    VECTOR_SIMILARITY_THRESHOLD: float = 0.7
    RAG_TOP_K_RESULTS: int = 5

    # Research
    DEEP_RESEARCH_TIMEOUT_SECONDS: int = 120

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
