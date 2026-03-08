"""
Caydex API — FastAPI Backend
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import logging
import time
import asyncio
from typing import Any

from app.config import settings
from app.database import check_supabase_health
from app.api.v1.api import api_router
from app.integrations.fmp import close_fmp_client

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    healthy = await check_supabase_health()
    if healthy:
        logger.info("Supabase connection OK")
    else:
        logger.warning("Supabase connection FAILED — check configuration")

    # Start background news pre-warmer for popular watchlist tickers
    asyncio.create_task(_run_news_pre_warmer())

    yield

    # Graceful shutdown: close persistent HTTP clients
    await close_fmp_client()
    logger.info("Shutting down")


async def _run_news_pre_warmer():
    """Background task: pre-warm news cache for popular watchlist tickers."""
    # Delay initial run to let the app fully start
    await asyncio.sleep(30)

    while True:
        try:
            from app.services.news_cache_service import get_news_cache_service

            service = get_news_cache_service()
            await service.pre_warm_popular_tickers(top_n=20)
            await service.cleanup_expired_cache()
        except Exception as e:
            logger.error(f"News pre-warmer failed: {e}", exc_info=True)

        # Re-run every 2 hours
        await asyncio.sleep(7200)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Caydex — AI Value Investing Education Platform",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# GZip
app.add_middleware(GZipMiddleware, minimum_size=1000)


# Request timing
@app.middleware("http")
async def add_process_time(request: Request, call_next):
    start = time.time()
    request_id = f"{int(start * 1000)}"
    request.state.request_id = request_id

    response = await call_next(request)

    elapsed = time.time() - start
    response.headers["X-Process-Time"] = str(elapsed)
    response.headers["X-Request-ID"] = request_id

    logger.info(
        f"{request.method} {request.url.path} | {response.status_code} | {elapsed:.3f}s"
    )
    return response


# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Invalid request data", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def general_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred"},
    )


# Routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "online",
    }


@app.get("/health", tags=["Root"])
async def health():
    db_ok = await check_supabase_health()
    return {
        "status": "healthy" if db_ok else "degraded",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "database": "connected" if db_ok else "disconnected",
    }


@app.get("/disclaimer", tags=["Root"])
async def disclaimer():
    return {"disclaimer": settings.LEGAL_DISCLAIMER}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
