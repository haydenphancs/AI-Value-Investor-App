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
from app.database import check_supabase_health, get_supabase
from app.api.v1.api import api_router
from app.integrations.coingecko import close_coingecko_client
from app.integrations.fmp import close_fmp_client
from app.services.live_price_manager import get_live_price_manager

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
        # Clear whale profile cache on deploy so code changes take effect
        # immediately without needing manual force_refresh per whale.
        try:
            sb = get_supabase()
            sb.table("whale_profile_cache").delete().neq("whale_id", "").execute()
            logger.info("Cleared whale_profile_cache on startup")
        except Exception as e:
            logger.warning("Failed to clear whale_profile_cache: %s", e)
    else:
        logger.warning("Supabase connection FAILED — check configuration")

    # Skip heavy background tasks in local dev — Railway handles them.
    # Local server is a lightweight dev mirror that reads from the same
    # Supabase caches that Railway populates.
    is_local_dev = settings.ENVIRONMENT == "development"
    if is_local_dev:
        logger.info("Local dev mode — skipping background tasks (Railway handles them)")
    else:
        # Pre-warm ApeWisdom social mentions cache at startup
        asyncio.create_task(_warm_social_cache())

        # Start background news pre-warmer for popular watchlist tickers
        asyncio.create_task(_run_news_pre_warmer())

        # Start background sector benchmark computation (daily)
        asyncio.create_task(_run_sector_benchmark_job())

        # Start background industry dossier recompute (weekly).
        # Replaces live FRED+Census calls per ticker report with a
        # pre-computed Supabase cache keyed on industry.
        asyncio.create_task(_run_industry_dossier_job())

        # Start background whale hydration jobs
        asyncio.create_task(_run_whale_hydration_job())

    yield

    # Graceful shutdown: close live price WebSocket connections
    await get_live_price_manager().shutdown()

    # Close persistent HTTP clients
    await close_fmp_client()
    await close_coingecko_client()
    logger.info("Shutting down")


async def _warm_social_cache():
    """Pre-warm ApeWisdom cache at startup so first sentiment requests have social data."""
    await asyncio.sleep(5)  # let app start
    try:
        from app.integrations.apewisdom import refresh_cache
        cache = await refresh_cache()
        logger.info(f"ApeWisdom cache pre-warmed: {len(cache)} tickers")
    except Exception as e:
        logger.warning(f"ApeWisdom pre-warm failed: {e}")


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


async def _run_sector_benchmark_job():
    """Background task: recompute sector benchmarks weekly on Sunday at 1 AM."""
    from datetime import datetime, timedelta

    await asyncio.sleep(60)  # let app fully start

    while True:
        # Calculate seconds until next Sunday 1:00 AM local time
        now = datetime.now()
        days_until_sunday = (6 - now.weekday()) % 7  # 6 = Sunday
        if days_until_sunday == 0 and now.hour >= 1:
            days_until_sunday = 7  # already past 1 AM Sunday, wait for next week
        next_run = now.replace(hour=1, minute=0, second=0, microsecond=0) + timedelta(days=days_until_sunday)
        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            f"Sector benchmark job: next run at {next_run.isoformat()} "
            f"(sleeping {sleep_seconds / 3600:.1f}h)"
        )
        await asyncio.sleep(sleep_seconds)

        try:
            from app.services.sector_benchmark_service import get_sector_benchmark_service

            service = get_sector_benchmark_service()
            result = await service.compute_all_benchmarks(force=True)
            logger.info(f"Sector benchmark job completed: {result}")
        except Exception as e:
            logger.error(f"Sector benchmark job failed: {e}", exc_info=True)


def _next_quarterly_dossier_run(now: "datetime") -> "datetime":
    """First Sunday of January / April / July / October at 02:00 UTC.

    Picks the next such datetime strictly after `now`. Module-level so
    it can be unit-tested independently of the long-running job loop.
    `now` must be a timezone-aware UTC datetime.
    """
    from datetime import datetime, timedelta, timezone

    candidates = []
    for year_offset in (0, 1):
        for month in (1, 4, 7, 10):
            anchor = datetime(now.year + year_offset, month, 1, 2, 0, 0,
                              tzinfo=timezone.utc)
            days_to_sunday = (6 - anchor.weekday()) % 7
            first_sunday = anchor + timedelta(days=days_to_sunday)
            if first_sunday > now:
                candidates.append(first_sunday)
    return min(candidates)


async def _run_industry_dossier_job():
    """Background task: recompute the industry_dossier table quarterly
    on the first Sunday of January / April / July / October at 02:00 UTC.

    The recompute itself is two-phase:
      Phase A — Census/FRED 4-tier chain (industry_dossier_service)
      Phase B — AI-driven research overrides for the curated
                globally-traded industries (industry_override_service)

    Phase B fires automatically right after Phase A from inside
    `recompute_all()` — no separate task. Pure asyncio.sleep loop with
    per-iteration try/except so a single failed quarter doesn't break
    the loop.
    """
    from datetime import datetime, timezone

    await asyncio.sleep(120)  # let app fully start

    while True:
        now = datetime.now(timezone.utc)
        next_run = _next_quarterly_dossier_run(now)
        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            f"Industry dossier job (quarterly): next run at {next_run.isoformat()} "
            f"(sleeping {sleep_seconds / 3600:.1f}h)"
        )
        await asyncio.sleep(sleep_seconds)

        try:
            from app.services.industry_dossier_service import get_industry_dossier_service

            service = get_industry_dossier_service()
            result = await service.recompute_all()
            logger.info(f"Industry dossier job completed: {result}")
        except Exception as e:
            logger.error(f"Industry dossier job failed: {e}", exc_info=True)


async def _run_whale_hydration_job():
    """Background task: hydrate whale profiles.

    - Runs full hydration daily at 2 AM UTC.
    - Runs politician-only hydration every 6 hours.
    """
    from datetime import datetime, timedelta

    await asyncio.sleep(120)  # let app fully start

    politician_interval = 6 * 3600  # 6 hours
    last_politician_run = 0.0

    while True:
        now = datetime.now()
        import time as _time
        current_time = _time.monotonic()

        # Politicians: every 6 hours
        if current_time - last_politician_run >= politician_interval:
            try:
                from scripts.hydrate_whales import WhaleHydrator
                from app.integrations.fmp import FMPClient
                from app.integrations.gemini import GeminiClient

                fmp = FMPClient()
                gemini = GeminiClient()
                hydrator = WhaleHydrator(fmp, gemini)

                # Hydrate politicians only
                from app.database import get_supabase
                sb = get_supabase()
                politicians = (
                    sb.table("whales")
                    .select("*")
                    .in_("data_source", ["congressional_house", "congressional_senate"])
                    .execute()
                )
                for whale in (politicians.data or []):
                    try:
                        await hydrator._hydrate_one(whale)
                    except Exception as e:
                        logger.error(f"Politician hydration failed for {whale['name']}: {e}")

                await fmp.close()
                last_politician_run = current_time
                logger.info("Politician whale hydration completed")
            except Exception as e:
                logger.error(f"Politician whale hydration job failed: {e}", exc_info=True)

        # Full hydration: daily at 2 AM UTC
        hours_until_2am = (26 - now.hour) % 24  # hours until next 2 AM
        if hours_until_2am == 0:
            try:
                from scripts.hydrate_whales import WhaleHydrator
                from app.integrations.fmp import FMPClient
                from app.integrations.gemini import GeminiClient

                fmp = FMPClient()
                gemini = GeminiClient()
                hydrator = WhaleHydrator(fmp, gemini)
                await hydrator.run()
                await fmp.close()
                logger.info("Full whale hydration completed")
            except Exception as e:
                logger.error(f"Full whale hydration job failed: {e}", exc_info=True)

        # Check every hour
        await asyncio.sleep(3600)


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
    content = {"detail": "Invalid request data"}
    if settings.DEBUG:
        content["errors"] = exc.errors()
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=content,
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
