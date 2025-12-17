"""
AI Value Investor API - Main Application
FastAPI backend for the AI Value Investor iOS application.

Requirements: Section 2.4, 2.5 - FastAPI, Supabase, Python
Security: Section 5.3 - API keys server-side only
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import logging
import time
from typing import Any

from app.config import settings
from app.database import db_manager
from app.api.v1.api import api_router
from app.jobs.scheduler import init_scheduler, shutdown_scheduler
from app.cache import cache_manager

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan events.
    Handles startup and shutdown tasks.
    """
    # Startup
    logger.info("Starting AI Value Investor API")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Debug mode: {settings.DEBUG}")

    # Check database connection
    is_connected = await db_manager.check_connection()
    if is_connected:
        logger.info("✓ Database connection established")
    else:
        logger.warning("✗ Database connection failed - check configuration")

    # Initialize Redis cache
    try:
        await cache_manager.connect()
        if cache_manager.is_connected:
            logger.info("✓ Redis cache connected")
        else:
            logger.warning("⚠ Running without Redis cache")
    except Exception as e:
        logger.error(f"✗ Failed to connect to Redis: {e}")

    # Initialize background tasks scheduler
    if settings.ENABLE_BACKGROUND_JOBS:
        try:
            init_scheduler()
            logger.info("✓ Background job scheduler started")
        except Exception as e:
            logger.error(f"✗ Failed to start scheduler: {e}")
    else:
        logger.info("Background jobs disabled (ENABLE_BACKGROUND_JOBS=False)")

    yield

    # Shutdown
    logger.info("Shutting down AI Value Investor API")

    # Shutdown scheduler
    if settings.ENABLE_BACKGROUND_JOBS:
        try:
            shutdown_scheduler()
            logger.info("✓ Background job scheduler stopped")
        except Exception as e:
            logger.error(f"Error shutting down scheduler: {e}")

    # Disconnect cache
    try:
        await cache_manager.disconnect()
        logger.info("✓ Redis cache disconnected")
    except Exception as e:
        logger.error(f"Error disconnecting cache: {e}")

    await db_manager.close()


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Backend API for AI Value Investor - Democratizing Financial Literacy",
    # docs_url="/api/docs" if settings.DEBUG else None,  # Disable in production
    # redoc_url="/api/redoc" if settings.DEBUG else None,
    # openapi_url="/api/openapi.json" if settings.DEBUG else None,

    docs_url="/docs" if settings.DEBUG else None,  # Disable in production
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,

    lifespan=lifespan
)


# Middleware Configuration
# ======================

# CORS Middleware - Section 3.4 (Communications Interfaces)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,  # Update for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"]
)

# GZip Compression Middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """
    Add processing time to response headers and log requests.
    """
    start_time = time.time()

    # Generate request ID for tracing
    request_id = f"{int(start_time * 1000)}"
    request.state.request_id = request_id

    response = await call_next(request)

    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    response.headers["X-Request-ID"] = request_id

    # Log request
    logger.info(
        f"Request: {request.method} {request.url.path} | "
        f"Status: {response.status_code} | "
        f"Duration: {process_time:.3f}s | "
        f"Request-ID: {request_id}"
    )

    return response


# Exception Handlers
# =================

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle validation errors with user-friendly messages.
    """
    logger.warning(f"Validation error for {request.url.path}: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Invalid request data",
            "errors": exc.errors(),
            "request_id": getattr(request.state, "request_id", None)
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    Handle unexpected errors gracefully.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal server error occurred",
            "request_id": getattr(request.state, "request_id", None)
        }
    )


# API Routes
# ==========

# Include API v1 router
app.include_router(api_router, prefix="/api/v1")


# Root endpoints
@app.get("/", tags=["Root"])
async def root() -> dict[str, str]:
    """
    Root endpoint - API health check.
    """
    return {
        "message": "AI Value Investor API",
        "version": settings.APP_VERSION,
        "status": "online",
        "docs": "/api/docs" if settings.DEBUG else "disabled",
        "disclaimer": settings.LEGAL_DISCLAIMER
    }


@app.get("/health", tags=["Root"])
async def health_check() -> dict[str, Any]:
    """
    Health check endpoint for monitoring.
    Returns database connection status and service health.
    """
    db_healthy = await db_manager.check_connection()

    return {
        "status": "healthy" if db_healthy else "degraded",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "database": "connected" if db_healthy else "disconnected",
        "timestamp": time.time()
    }


@app.get("/disclaimer", tags=["Root"])
async def get_disclaimer() -> dict[str, str]:
    """
    Legal disclaimer endpoint (Section 5.2).
    Required to be displayed on all screens providing analysis.
    """
    return {
        "disclaimer": settings.LEGAL_DISCLAIMER,
        "full_text": (
            "The information provided by this application is for educational "
            "purposes only and should not be considered as financial advice. "
            "Always conduct your own research and consult with a qualified "
            "financial advisor before making investment decisions. Past "
            "performance does not guarantee future results."
        )
    }


# Development endpoints (only in debug mode)
if settings.DEBUG:
    @app.get("/debug/config", tags=["Debug"])
    async def debug_config() -> dict[str, Any]:
        """
        Debug endpoint to check configuration (excluding secrets).
        Only available in debug mode.
        """
        return {
            "app_name": settings.APP_NAME,
            "environment": settings.ENVIRONMENT,
            "debug": settings.DEBUG,
            "supabase_url": settings.SUPABASE_URL,
            "gemini_model": settings.GEMINI_MODEL,
            "free_tier_limit": settings.FREE_TIER_DEEP_RESEARCH_LIMIT,
            "pro_tier_limit": settings.PRO_TIER_DEEP_RESEARCH_LIMIT,
            "premium_tier_limit": settings.PREMIUM_TIER_DEEP_RESEARCH_LIMIT,
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
