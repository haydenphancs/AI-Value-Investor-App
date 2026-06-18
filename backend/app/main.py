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

        # Start background report pre-warmer: warms the persona-neutral
        # ticker_data_cache for top tickers so the first report after each close
        # (and any same-session burst) skips re-collecting it. Runs the full
        # persona-neutral collection (FMP fan-out + grounded precompute, which
        # makes some Gemini-grounded calls for cold tickers).
        asyncio.create_task(_run_report_pre_warmer())

        # Start background sector benchmark computation (daily)
        asyncio.create_task(_run_sector_benchmark_job())

        # Start background industry dossier recompute (weekly).
        # Replaces live FRED+Census calls per ticker report with a
        # pre-computed Supabase cache keyed on industry.
        asyncio.create_task(_run_industry_dossier_job())

        # Start background whale hydration jobs
        asyncio.create_task(_run_whale_hydration_job())

        # Refund safety net: reconcile research reports stranded in
        # pending/processing (killed worker) so charged-but-undelivered
        # reports get their credits back.
        asyncio.create_task(_run_research_reconciliation_job())

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


async def _run_report_pre_warmer():
    """Background task: pre-warm the persona-NEUTRAL ticker_data_cache for the
    most popular watchlist tickers.

    After each market close the close-aligned collection cache goes stale;
    warming the top tickers here means the first report request (and any
    same-session multi-user burst on a trending name) hits a warm collection
    and skips re-collecting it. This runs the full persona-NEUTRAL collection —
    the ~20-call FMP fan-out PLUS the persona-neutral grounded precompute (which
    for a cold ticker makes some Gemini-grounded calls) — but skips the
    per-persona Stage-A/Stage-B work, credits, and research_reports rows.

    Idempotent: `collect()` checks freshness first, so a still-fresh ticker is a
    one-DB-read no-op — real FMP work only happens right after a new close.
    Batched small so the pre-warm itself never becomes an FMP thundering herd.
    """
    if not settings.REPORT_PREWARM_ENABLED:
        return

    await asyncio.sleep(45)  # after the news pre-warmer has kicked off

    while True:
        try:
            from app.services.ticker_data_cache import warm_ticker_collection

            top_n = settings.REPORT_PREWARM_TOP_N
            sb = get_supabase()
            rows = sb.rpc("get_top_watchlist_tickers", {"n": top_n}).execute()
            tickers = [r["ticker"] for r in (rows.data or []) if r.get("ticker")]

            if not tickers:
                logger.info("Report pre-warm: no watchlist tickers to warm")
            else:
                # warm_ticker_collection bounds DISTINCT-ticker concurrency via
                # _WARM_SEMAPHORE, collapses same-ticker via _INFLIGHT, and is a
                # cheap no-op for already-fresh tickers — so fire them all and
                # let the helper self-throttle.
                await asyncio.gather(
                    *(warm_ticker_collection(t) for t in tickers),
                    return_exceptions=True,
                )
                logger.info(
                    "Report pre-warm: pass complete for %d tickers", len(tickers)
                )
        except Exception as e:
            logger.error(f"Report pre-warmer failed: {e}", exc_info=True)

        await asyncio.sleep(settings.REPORT_PREWARM_INTERVAL_SECONDS)


async def _run_research_reconciliation_job():
    """Background task: refund research reports orphaned charged-but-undelivered.

    Generate Analysis charges 5 credits upfront then runs in a fire-and-forget
    task. If the worker is killed mid-run (deploy / OOM / crash) the row is
    stranded in pending/processing and never refunded. This sweep reconciles
    such rows on a fixed interval. Idempotent (claim-then-refund on
    `is_refunded`), so it's safe even if multiple workers run it.
    """
    from app.services.research_reconciliation_service import (
        sweep_once,
        RECON_SWEEP_INTERVAL_SECONDS,
    )

    await asyncio.sleep(90)  # let app fully start

    while True:
        try:
            await sweep_once()
        except Exception as e:
            logger.error(f"Research reconciliation sweep failed: {e}", exc_info=True)

        await asyncio.sleep(RECON_SWEEP_INTERVAL_SECONDS)


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

    # Each sub-job is anchored to a wall-clock offset from the quarterly
    # base run time (02:00 UTC). Spacing the starts by 30 min means even
    # if one job's burst tail is still draining FMP quota, the next job
    # waits until it's clear before hitting FMP again — never overlapping
    # in the rate-limit window.
    #
    #   base + 0   min → industry_dossier  (Phase A + Phase B)
    #   base + 30  min → competitor_intel.refresh_top_tickers
    #   base + 60  min → ip_intel.refresh_top_tickers
    #   base + 90  min → industry_moat_benchmark.recompute_all  (longest)
    #
    # If a sub-job overruns its 30-min window, the next one starts as
    # soon as the previous awaits return — _wait_until clamps to "at
    # least the target time, never earlier".
    async def _wait_until(target: datetime) -> None:
        delta = (target - datetime.now(timezone.utc)).total_seconds()
        if delta > 0:
            await asyncio.sleep(delta)

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

        # ── Phase 2 chained: competitor intel @ base + 30 min ──
        # Waits until the staggered start time so its Gemini-grounded
        # research batch doesn't overlap any FMP burst tail from the
        # dossier job. Own try/except so a batch failure can't break
        # the loop.
        from datetime import timedelta as _td
        await _wait_until(next_run + _td(minutes=30))
        try:
            from app.services.competitor_intel_service import (
                get_competitor_intel_service,
            )

            competitor_summary = (
                await get_competitor_intel_service().refresh_top_tickers()
            )
            logger.info(
                f"Competitor intel quarterly batch completed: {competitor_summary}"
            )
        except Exception as e:
            logger.error(f"Competitor intel quarterly batch failed: {e}", exc_info=True)

        # ── Phase 3C chained: ip_intel (USPTO + FDA) @ base + 60 min ──
        # USPTO patents and FDA approvals change very slowly. Run an
        # hour after base so the FMP rate-limit window has fully reset.
        await _wait_until(next_run + _td(minutes=60))
        try:
            from app.services.ip_intel_service import get_ip_intel_service

            ip_summary = (
                await get_ip_intel_service().refresh_top_tickers()
            )
            logger.info(
                f"IP intel quarterly batch completed: {ip_summary}"
            )
        except Exception as e:
            logger.error(f"IP intel quarterly batch failed: {e}", exc_info=True)

        # ── Industry moat benchmarks (Peer Avg overlay) @ base + 90 min ──
        # Heaviest job in the chain (~140k FMP calls, ~60-90 min wall-clock
        # at 3000/min). Started last so any failures don't block the
        # upstream refreshes. `skip_if_fresh_hours=24` prevents the
        # quarterly run from blowing through FMP quota redoing rows
        # the operator already triggered manually within the last day.
        await _wait_until(next_run + _td(minutes=90))
        try:
            from app.services.industry_moat_benchmark_service import (
                get_industry_moat_benchmark_service,
            )

            moat_bench_summary = (
                await get_industry_moat_benchmark_service().recompute_all(
                    skip_if_fresh_hours=24,
                )
            )
            logger.info(
                f"Industry moat benchmark quarterly batch completed: {moat_bench_summary}"
            )
        except Exception as e:
            logger.error(
                f"Industry moat benchmark quarterly batch failed: {e}", exc_info=True,
            )


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


@app.get("/health/pdf", tags=["Root"])
async def health_pdf():
    """Verify the WeasyPrint native stack (cairo/pango) loaded. Returns 503 when it can't,
    so a misconfigured image fails the Railway deploy gate instead of the first user PDF."""
    try:
        import weasyprint  # noqa: F401 — lazy import; just probing the native libs

        return {"status": "healthy", "weasyprint": weasyprint.__version__}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "error": f"{type(e).__name__}: {e}"},
        )


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
