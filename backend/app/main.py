"""
Caydex API — FastAPI Backend
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
# Registered on the STARLETTE base class, not fastapi.HTTPException: fastapi's subclasses it,
# so this one handler covers both, including the 404/405 Starlette itself raises for an
# unmatched route.
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging
import time
import asyncio
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.database import check_supabase_health, get_supabase
from app.api.v1.api import api_router
from app.integrations.coingecko import close_coingecko_client
from app.integrations.finra_short_interest import close_finra_client
from app.integrations.fmp import close_fmp_client
from app.integrations.openfda import close_openfda_client
from app.integrations.uspto import close_uspto_client
from app.services.live_price_manager import get_live_price_manager
from app.log_redaction import scrub_sentry_event, SecretRedactingFilter

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Redact API keys / tokens from EVERY log line before it reaches stdout (Railway
# logs) — e.g. FMP per-symbol warnings echo the request URL with apikey=. The Sentry
# side is scrubbed separately in _sentry_before_send below.
for _handler in logging.getLogger().handlers:
    _handler.addFilter(SecretRedactingFilter())

# ── Error monitoring (Sentry) ──────────────────────────────────────────────
# Init ONLY when a DSN is present AND we're running in production. The DSN belongs
# in the Railway (prod) env, but the local backend/.env also carries it so the
# on-demand triage digest (scripts/error_digest.py) can authenticate. WITHOUT the
# ENVIRONMENT gate, the local dev server (env=development) — and pytest — would
# both ship their logger.error(...) to the PROD project, which is the source of the
# server=Hai-World / FMP-400 / permission-denied noise polluting the digest. Tests
# are ALSO covered by conftest.py forcing an empty DSN; this gate additionally
# silences the local dev server (ENVIRONMENT defaults to "development" in config.py;
# Railway sets it to "production"). When enabled: captures unhandled exceptions AND
# logger.error/exception (via LoggingIntegration) with full stacks; INFO logs become
# breadcrumbs. before_send tags the exception type / error_code so Sentry groups our
# known failure modes cleanly. Never ships user PII (send_default_pii=False) — this
# is a fintech backend.
if settings.SENTRY_DSN and settings.ENVIRONMENT == "production":
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    def _sentry_before_send(event: dict, hint: dict) -> dict:
        exc_info = (hint or {}).get("exc_info")
        if exc_info and exc_info[1] is not None:
            exc = exc_info[1]
            tags = event.setdefault("tags", {})
            tags["exception_type"] = type(exc).__name__
            code = getattr(exc, "error_code", None) or getattr(exc, "code", None)
            if code:
                tags["error_code"] = str(code)
        # Scrub any API key / token that leaked into the message or exception value
        # (e.g. FMP's apikey= in a request URL) so it is NEVER stored in Sentry.
        return scrub_sentry_event(event)

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=settings.APP_VERSION,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_send=_sentry_before_send,
        send_default_pii=False,
        # `send_default_pii=False` does NOT cover request bodies — it gates only cookies.
        # `StarletteRequestExtractor.extract_request_info` attaches the parsed JSON body to
        # every event unconditionally (integrations/starlette.py: `request_info["data"] =
        # json`), and the only thing that suppresses it is this option. Without it, a failed
        # sign-up — `auth.py` logs `logger.error(..., exc_info=True)`, which
        # LoggingIntegration turns into an event — shipped
        # `{"email": ..., "password": "<plaintext>"}` to Sentry. Same for the 6-digit
        # password-reset code. These events are logger-driven and the stack is what
        # diagnoses them, so the body was never adding diagnostic value.
        max_request_body_size="never",
    )
    logger.info("Sentry error monitoring enabled (environment=%s)", settings.ENVIRONMENT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    healthy = await check_supabase_health()
    if healthy:
        logger.info("Supabase connection OK")
        # NOTE: this used to DELETE every `whale_profile_cache` row on startup, so that
        # a code change to the assembled profile shape took effect without a manual
        # force_refresh per whale. That goal is right; the blunt instrument was not — it
        # also fired on an OOM, a health-check flap or an instance rotation, leaving all
        # 56 whales Tier-2 cold for reasons unrelated to any deploy, and the first
        # visitor to each then paid a full rebuild.
        #
        # Invalidation now runs through `WHALE_PROFILE_SCHEMA_FLOOR` in whale_service:
        # a cached row older than the floor is treated as a miss. Bump the floor when
        # the profile shape changes; every other restart keeps the cache warm.
    else:
        logger.warning("Supabase connection FAILED — check configuration")

    # Skip heavy background tasks in local dev — Railway handles them.
    # Local server is a lightweight dev mirror that reads from the same
    # Supabase caches that Railway populates.
    is_local_dev = settings.ENVIRONMENT == "development"
    # Declared before the branch so the shutdown block below can reference them
    # even when background tasks were skipped (local dev).
    insight_sweeper_task: Optional[asyncio.Task] = None
    # EVERY background task's handle lands here.
    #
    # Two reasons, both of which bit us. (1) `asyncio.create_task` keeps only a WEAK
    # reference, so a fire-and-forget task can be garbage-collected mid-execution — the
    # documented CPython caveat. (2) More importantly, the teardown below closes the shared
    # httpx clients (`close_fmp_client` / `close_coingecko_client`); nine of these loops used
    # to still be running at that point, so every Railway redeploy tore their HTTP client out
    # from under them mid-request. The research reconciliation job is the one that hurts —
    # it is the refund safety net for charged-but-undelivered reports.
    background_tasks: list[asyncio.Task] = []

    def _on_background_task_done(task: asyncio.Task) -> None:
        """Make a background loop's death LOUD.

        Every loop below is `while True` with an internal try/except, so the only way one
        exits is a raise OUTSIDE that guard — exactly the failure that killed price alerts:
        `run_price_alert_loop` read an undeclared setting before its `while True`, so it
        died with AttributeError ~30s after every boot. Nothing logged, nothing retried,
        and the feature was simply absent in production while the app kept serving.

        Without a done-callback the exception is retrieved by nobody and asyncio's
        "Task exception was never retrieved" warning only fires at GC, if at all.
        """
        if task.cancelled():  # shutdown path — expected, already logged below
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Background task %r DIED and will not restart (%s: %s)",
                task.get_name(), type(exc).__name__, exc, exc_info=exc,
            )
        else:
            # A `while True` loop returning normally is also a bug, just a quiet one.
            logger.warning("Background task %r exited without an error", task.get_name())

    def _spawn(coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(_on_background_task_done)
        background_tasks.append(task)
        return task

    # The notification loops are the one family that CAN be opted back in locally.
    # Everything else here is FMP/Gemini-heavy and belongs to Railway, but with the
    # blanket skip there was no way to exercise a notification sender on a laptop at
    # all — which made "does this notification actually fire?" unanswerable before
    # deploying it. Pair with PUSH_DRY_RUN=true to run the full pipeline (audience,
    # preferences, caps, quiet hours, claim, ledger row) with no APNs and no device.
    run_notification_jobs = (not is_local_dev) or settings.RUN_NOTIFICATION_JOBS_LOCALLY

    if is_local_dev:
        logger.info("Local dev mode — skipping background tasks (Railway handles them)")
        if run_notification_jobs:
            logger.info(
                "RUN_NOTIFICATION_JOBS_LOCALLY is set — starting the notification "
                "loops locally (PUSH_DRY_RUN=%s)", settings.PUSH_DRY_RUN,
            )
    else:
        # Pre-warm ApeWisdom social mentions cache at startup
        _spawn(_warm_social_cache(), "warm_social_cache")

        # Start background news pre-warmer for popular watchlist tickers
        _spawn(_run_news_pre_warmer(), "run_news_pre_warmer")

        # Start background report pre-warmer: warms the persona-neutral
        # ticker_data_cache for top tickers so the first report after each close
        # (and any same-session burst) skips re-collecting it. Runs the full
        # persona-neutral collection (FMP fan-out + grounded precompute, which
        # makes some Gemini-grounded calls for cold tickers).
        _spawn(_run_report_pre_warmer(), "run_report_pre_warmer")

        # Start background scanner pre-warmer: keeps the Home Daily Scanners
        # (Movers/Volume + Skeptical Money) hot during the regular session so the
        # first Home load after each 20-min cache expiry isn't a cold build.
        _spawn(_run_scanner_pre_warmer(), "run_scanner_pre_warmer")

        # NOTE: the old weekly sector-only benchmark job was RETIRED here.
        # Sector + industry medians are now computed together by the
        # industry-benchmark recompute chained into the quarterly batch
        # (`_run_industry_dossier_job`, base+120 min). Running both would let
        # two writers race on the industry='' sector-aggregate rows. Manual
        # refresh remains available via POST /api/v1/admin/refresh-industry-benchmarks.

        # Start background industry dossier recompute (weekly).
        # Replaces live FRED+Census calls per ticker report with a
        # pre-computed Supabase cache keyed on industry.
        _spawn(_run_industry_dossier_job(), "run_industry_dossier_job")

        # Start background TTM benchmark refresh (weekly). TTM is a CURRENT
        # snapshot (price ÷ trailing-12mo earnings → drifts daily for every
        # company), so it must refresh far more often than the quarterly fiscal
        # recompute. Upserts the period_type='ttm' rows in place (~3.5 min).
        _spawn(_run_ttm_benchmark_job(), "run_ttm_benchmark_job")

        # Daily σ (daily-return volatility) precompute. Feeds the Updates insight
        # gate's volatility-relative move trigger: the 5-min sweeper reads each
        # ticker's σ from ticker_volatility_cache instead of fetching 180 daily
        # closes per ticker per sweep. ~201 light FMP calls/day (~08:00 UTC).
        _spawn(_run_volatility_precompute_job(), "run_volatility_precompute_job")

        # Start background whale hydration jobs
        _spawn(_run_whale_hydration_job(), "run_whale_hydration_job")

        # Warm whale_profile_cache after boot. The startup wipe is gone (invalidation
        # now runs through WHALE_PROFILE_SCHEMA_FLOOR), but a restart still empties the
        # in-process Tier-1 cache, and a schema-floor bump legitimately invalidates
        # Tier-2 for everyone at once. This makes either case invisible to users.
        _spawn(_run_whale_profile_pre_warmer(), "run_whale_profile_pre_warmer")

        # Refund safety net: reconcile research reports stranded in
        # pending/processing (killed worker) so charged-but-undelivered
        # reports get their credits back.
        _spawn(_run_research_reconciliation_job(), "run_research_reconciliation_job")

        # Entitlement safety net: expire subscriptions whose paid period ended so a
        # cancelled subscriber stops drawing the paid monthly credit allocation. Without
        # it, ONE lost EXPIRED/REFUND notification entitles an account forever, because
        # nothing else ever re-evaluates `users.tier`.
        _spawn(_run_subscription_expiry_sweep(), "run_subscription_expiry_sweep")

        # Updates-screen AI Insights sweeper. Re-evaluates every watchlisted
        # scope (plus the general market feed) on a 5-min price / 15-min news
        # cadence during market hours and regenerates a card only when a
        # materiality predicate trips. This is what keeps the read path free of
        # any Gemini call — see services/updates_insight_sweeper.py.
        #
        # Cancelled FIRST below, ahead of the others: it holds a cross-process claim row
        # per scope, and a clean cancel lets the current sweep unwind instead of leaving
        # claims to time out.
        from app.services.updates_insight_sweeper import run_insight_sweeper_loop
        insight_sweeper_task = _spawn(run_insight_sweeper_loop(), "insight_sweeper")

    # Outside the else: this family is opt-in-able locally (see `run_notification_jobs`).
    if run_notification_jobs:
        # Quiet-hours flush. Runs 24/7 — NOT gated on market hours, because a quiet
        # window ends on the USER's clock, not the market's.
        _spawn(_run_notification_dispatch_loop(), "notification_dispatch")
        # Daily senders (earnings after the close, smart money in the evening). Wakes
        # hourly; the once-per-ET-day schedule is enforced by the cross-instance claim.
        _spawn(_run_scheduled_notification_senders(), "notification_senders")
        # User-set price alerts. 60s cadence across the extended session (04:00-20:00 ET)
        # — a threshold crossed in pre-market is exactly what someone sets an alert for.
        # Separate from the Updates sweeper on purpose: that loop's universe is capped at
        # the top-200 watchlisted tickers, and an alerted ticker is frequently outside it.
        from app.services.price_alert_service import run_price_alert_loop
        _spawn(run_price_alert_loop(), "price_alerts")

    yield

    # Stop the insight sweeper first — it releases claim rows on the way out.
    if insight_sweeper_task is not None:
        insight_sweeper_task.cancel()
        try:
            await insight_sweeper_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(
                "Insight sweeper shutdown raised: %s: %s", type(e).__name__, e
            )

    # Then stop EVERY remaining background loop BEFORE closing the HTTP clients they use.
    # Skipping this is not cosmetic: `close_fmp_client()` below would otherwise pull the
    # shared httpx client out from under nine still-running loops on every redeploy.
    pending = [t for t in background_tasks if not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        # `return_exceptions=True` so one loop that raises on cancel cannot prevent the
        # rest from being awaited — a hung shutdown is how Railway ends up SIGKILLing us
        # mid-write instead of letting the sweeps unwind.
        results = await asyncio.gather(*pending, return_exceptions=True)
        for task, result in zip(pending, results):
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.warning(
                    "Background task %s raised on shutdown: %s: %s",
                    task.get_name(), type(result).__name__, result,
                )
        logger.info("Stopped %d background tasks", len(pending))

    # Graceful shutdown: close live price WebSocket connections
    await get_live_price_manager().shutdown()

    # Close persistent HTTP clients.
    #
    # Every integration holding a module-level `httpx.AsyncClient` must appear here. openfda
    # and uspto shipped finished, docstring'd `close_*_client()` hooks that were never imported
    # by anything — the tear-down existed and simply was not wired — and FINRA's two clients
    # had no hook at all. `tests/test_integration_client_teardown.py` fails the build if a new
    # integration adds a persistent client without joining this list.
    await close_fmp_client()
    await close_coingecko_client()
    await close_openfda_client()
    await close_uspto_client()
    await close_finra_client()
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

            # Retention sweep for chat_usage_budget. Migration 096 documented this
            # sweep and indexed for it, but it was never implemented, so the table
            # accumulated one row per user per active day indefinitely. Piggy-backed
            # on this 2-hourly loop rather than adding another task: it is a cheap
            # single DELETE and does not need its own cadence.
            from app.services.chat_budget_service import get_chat_budget_service

            await asyncio.to_thread(
                get_chat_budget_service().cleanup_old_budget_rows
            )

            # Same for guest_report_budget (migration 106) — one row per INSTALL per
            # month, and installs are never cleaned up otherwise, so without this the
            # table grows without bound as installs churn.
            from app.services.guest_report_budget_service import (
                get_guest_report_budget_service,
            )

            await asyncio.to_thread(
                get_guest_report_budget_service().sweep_expired
            )

            # And analytics_events (migration 107) — the highest-volume of the three.
            # These are aggregate inputs, not a system of record, so they age out.
            from app.services.analytics_service import get_analytics_service

            await asyncio.to_thread(get_analytics_service().sweep_expired)

            # And push_send_log (migration 109) — one row per delivered push; the
            # dedup horizon is a single trading day, so anything old is pure history.
            from app.services.push_dispatch_service import get_push_dispatch_service

            await asyncio.to_thread(get_push_dispatch_service().sweep_expired)
        except Exception as e:
            logger.error(f"News pre-warmer failed: {e}", exc_info=True)

        # Re-run every 2 hours
        await asyncio.sleep(7200)


async def _run_notification_dispatch_loop():
    """Background task: deliver notifications parked by quiet hours.

    Quiet hours DEFER a notification rather than dropping it — the ledger row is
    written immediately (so the in-app inbox has it) and only the buzz waits. Something
    has to wake those rows up, and it cannot be the Updates insight sweeper: that loop
    is gated on `is_market_active()`, and a European user's 07:00 quiet-end is 01:00 ET,
    when the sweeper is asleep. A user in Asia would never receive a deferred alert at
    all. So this runs 24/7, deliberately, and is the only loop here that does.

    Cheap when idle: one RPC per cycle that returns zero rows on the overwhelming
    majority of ticks. Cross-instance safe — `claim_due_notifications` (migration 119)
    uses FOR UPDATE SKIP LOCKED, so two instances never hand out the same row.
    """
    from app.services.push_dispatch_service import get_push_dispatch_service

    # Stagger past the startup burst so this is not competing with the pre-warmers for
    # the event loop on the first seconds of a deploy.
    await asyncio.sleep(45)

    interval = max(settings.NOTIFICATION_DISPATCH_INTERVAL_SECONDS, 10)
    while True:
        try:
            await get_push_dispatch_service().flush_deferred()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Never let one bad cycle kill the loop — a dead dispatcher means every
            # deferred notification silently expires unsent, which looks exactly like
            # "push is broken" and has no other symptom.
            logger.error(
                "Notification dispatch cycle failed (%s: %s)",
                type(e).__name__, e, exc_info=True,
            )
        await asyncio.sleep(interval)


async def _run_scheduled_notification_senders():
    """Background task: the daily notification senders (earnings, smart money).

    ONE loop for both, waking hourly. The hourly cadence is not the schedule — the
    schedule is enforced by `claim_notification_job`, which grants a job at most once per
    ET trading day. Waking often just means a job that was missed (deploy, crash, an
    instance rotating out) is picked up within the hour instead of being lost until
    tomorrow, and a claim that is refused costs one cheap RPC.

    Each sender is invoked past its own ET hour: earnings after the close (16:00), smart
    money in the evening (18:00) once Form 4s have landed. Guarding on the hour here as
    well as in the claim keeps a restart at 06:00 from spending 200 FMP calls on a day's
    Form 4s that do not exist yet.
    """
    from app.services.notification_senders.earnings_sender import (
        run_earnings_notifications,
    )
    from app.services.notification_senders.smart_money_sender import (
        run_smart_money_notifications,
    )
    from app.services.notification_senders.profile_match_sender import (
        run_profile_match_notifications,
    )
    # `datetime` is not a module-level import in this file (every other loop imports it
    # locally), so it must be imported here or the first wake raises NameError — an
    # error a plain `from app.main import app` import check would never surface.
    from datetime import datetime as _dt

    from app.utils.market_hours import ET as _ET

    # Stagger past both the startup burst and the dispatch loop.
    await asyncio.sleep(90)

    senders = (
        ("earnings", settings.EARNINGS_NOTIFY_HOUR_ET, run_earnings_notifications),
        ("smart_money", settings.SMART_MONEY_NOTIFY_HOUR_ET, run_smart_money_notifications),
        # LAST, and deliberately an hour later: it reads the same signals the smart-money
        # pass does, and a reader who follows a ticker AND its topic should get the
        # specific alert first — the per-category caps then keep the derived one from
        # piling on top.
        ("profile_match", settings.PROFILE_MATCH_NOTIFY_HOUR_ET, run_profile_match_notifications),
    )

    while True:
        hour_et = _dt.now(_ET).hour
        for name, after_hour, run_sender in senders:
            if hour_et < after_hour:
                continue
            try:
                await run_sender()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # The claim is released by `claimed_job`'s shielded finally with
                # success=False, so the day is RETRIED on the next wake rather than
                # silently skipped. Log loudly: a sender that quietly stops is
                # indistinguishable from "nothing happened today".
                logger.error(
                    "Notification sender %s failed (%s: %s) — will retry next hour",
                    name, type(e).__name__, e, exc_info=True,
                )
        await asyncio.sleep(3600)


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


async def _run_scanner_pre_warmer():
    """Background task: keep the Home Daily Scanners hot during the regular session.

    Movers + Volume are intraday metrics behind a 20-min cache; Skeptical Money is
    built in the SAME ``get_scanners()`` pass. Without warming, the first Home load
    after each cache expiry pays a cold build. This refreshes the shared scanner
    cache every ``SCANNER_PREWARM_INTERVAL_SECONDS`` (set BELOW the 20-min TTL so it
    never goes cold mid-session) ONLY while the regular US session is open, and
    idles otherwise (0 FMP calls overnight/weekends).

    ``get_scanners()`` serves its cache first (a no-op when a user already built it
    recently, via the in-flight dedup) and degrades internally on an FMP 429, so
    this loop needs no extra rate logic — the inter-build gap IS the backoff. Short
    interest is 3-day cached over a bi-monthly source, so warming it here adds ~0
    FINRA calls.
    """
    if not settings.SCANNER_PREWARM_ENABLED:
        return

    # Stagger after the news (30s) and report (45s) pre-warmers so the startup
    # bursts don't pile onto the shared 20-connection FMP pool at once.
    await asyncio.sleep(120)

    from app.services.home_dashboard_service import (
        _market_status,
        get_home_dashboard_service,
    )
    from app.services.signals_service import get_signals_service

    while True:
        try:
            _, is_open = _market_status()
            if is_open:  # regular US session only (9:30–4 ET, DST-aware)
                await get_home_dashboard_service().get_scanners()
                # App-Exclusive Signals ride along (congress/whale/earnings). Cheap:
                # whale is Supabase-only, congress is 2 FMP calls, earnings is 1 —
                # and get_signals() serves its own cache first (a no-op when warm).
                await get_signals_service().get_signals()
                # Emerging Frontiers themes ride along too — one batch-quote fan-out
                # over the small ticker union; get_themes() serves its cache first.
                await get_home_dashboard_service().get_themes()
                logger.info("Scanner + signals + themes pre-warm: refreshed (regular session open)")
        except Exception as e:
            logger.error(f"Scanner pre-warmer failed: {e}", exc_info=True)

        await asyncio.sleep(settings.SCANNER_PREWARM_INTERVAL_SECONDS)


async def _run_subscription_expiry_sweep():
    """Background task: expire lapsed subscriptions so entitlement self-corrects.

    `reconcile_user_tier` is the only writer of `users.tier` and it runs ONLY on a client
    verify or an App Store Server Notification. A single lost EXPIRED/REFUND notification
    therefore left a cancelled subscriber on their paid tier permanently — and because
    `ensure_credit_period` reads `users.tier` at every monthly boundary, they kept drawing
    the paid allocation (up to 4000 credits/month) forever, at real Gemini + FMP cost. The
    client only ever reports purchases, so nothing else could notice.

    Hourly is ample: the shortest grace window is 24h, so this is about eventual correctness,
    not latency. Idempotent — an already-expired row is not selected.
    """
    from app.services.iap_service import get_iap_service

    await asyncio.sleep(150)  # let app fully start, and stagger off the other sweeps

    while True:
        try:
            # Sync Supabase SDK — must not block the event loop.
            await asyncio.to_thread(get_iap_service().sweep_expired_subscriptions)
        except Exception as e:
            logger.error(f"Subscription expiry sweep failed: {e}", exc_info=True)

        await asyncio.sleep(3600)


async def _run_research_reconciliation_job():
    """Background task: refund research reports orphaned charged-but-undelivered.

    Generate Analysis charges credits upfront then runs in a fire-and-forget
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


# NOTE: `_run_sector_benchmark_job` (weekly Sunday 1 AM, sector-only over the
# S&P 500) was RETIRED in the industry-benchmark migration. Sector + industry
# medians are now produced in one pass by the industry-benchmark recompute
# chained into `_run_industry_dossier_job` (base+120 min). The old
# `sector_benchmark_service.compute_all_benchmarks` still backs the manual
# admin endpoint but is no longer scheduled — two schedulers writing the
# industry='' rows would race and re-introduce stale data.


# TTM weekly refresh fires at 06:00 UTC Sunday — deliberately AFTER the quarterly
# fiscal recompute window so the two FMP-heavy jobs never overlap. The dossier chain
# runs the fiscal recompute at base 02:00 + 120 min = 04:00 UTC (first Sunday of
# Jan/Apr/Jul/Oct), with the moat job at base+90 running up to ~05:00. 06:00 clears
# both, so on those 4 quarter-start Sundays the jobs no longer race on FMP rate budget.
_TTM_WEEKLY_HOUR_UTC = 6


def _next_weekly_ttm_run(now: "datetime") -> "datetime":
    """Next Sunday at _TTM_WEEKLY_HOUR_UTC:00 UTC strictly after `now`.

    Module-level + pure so the schedule (and its non-overlap with
    `_next_quarterly_dossier_run` + 120 min) is unit-testable independently of the
    long-running loop. `now` must be a timezone-aware UTC datetime.
    """
    from datetime import timedelta

    days_until_sunday = (6 - now.weekday()) % 7  # 6 = Sunday
    candidate = now.replace(
        hour=_TTM_WEEKLY_HOUR_UTC, minute=0, second=0, microsecond=0
    ) + timedelta(days=days_until_sunday)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


async def _run_ttm_benchmark_job():
    """Weekly TTM (trailing-twelve-month) benchmark refresh — Sunday 06:00 UTC.

    TTM is a CURRENT snapshot (price ÷ TTM earnings drifts daily for EVERY
    company, so the industry/sector median goes stale as a whole), which is why
    it refreshes weekly rather than with the quarterly fiscal recompute.
    `recompute_all_ttm` UPSERTS the period_type='ttm' rows in place — additive,
    the fiscal annual/quarterly rows are untouched. ~3.5 min / ~11k light FMP
    calls. `skip_if_fresh_hours=24` makes a re-trigger RESUME (skip sectors done
    in the last day) rather than redo everything after a dyno restart.

    Fires at 06:00 UTC (not 04:00) to avoid colliding with the quarterly fiscal
    recompute that runs at ~04:00 UTC on quarter-start Sundays — see
    `_next_weekly_ttm_run`.
    """
    from datetime import datetime, timezone

    await asyncio.sleep(180)  # let app fully start

    while True:
        now = datetime.now(timezone.utc)
        next_run = _next_weekly_ttm_run(now)
        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            f"TTM benchmark job: next run at {next_run.isoformat()} "
            f"(sleeping {sleep_seconds / 3600:.1f}h)"
        )
        await asyncio.sleep(sleep_seconds)

        try:
            from app.services.industry_benchmark_service import (
                get_industry_benchmark_service,
            )

            result = await get_industry_benchmark_service().recompute_all_ttm(
                skip_if_fresh_hours=24,
            )
            logger.info(f"TTM benchmark weekly job completed: {result}")
        except Exception as e:
            logger.error(f"TTM benchmark weekly job failed: {e}", exc_info=True)


def _next_daily_run(now: "datetime", hour_utc: int = 8) -> "datetime":
    """Next occurrence of ``hour_utc``:00 UTC strictly after ``now``.
    Module-level so it can be unit-tested independently of the loop.
    """
    from datetime import timedelta

    candidate = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


async def _run_volatility_precompute_job():
    """Daily σ precompute for the Updates volatility-relative move trigger.

    Populates ``ticker_volatility_cache`` once a day (~08:00 UTC, pre-open) for the
    swept universe (top-200 watchlist + ^GSPC) so the 5-min sweeper can read σ
    cheaply. ~201 light FMP historical calls; ``skip_if_fresh_hours`` makes a dyno
    restart RESUME rather than refetch. A ticker with no σ (new/low history) simply
    falls back to the fixed price band in the gate — never loses a signal.
    """
    from datetime import datetime, timezone

    from app.database import get_supabase
    from app.services.updates_insight_sweeper import MARKET_INDEX_SYMBOL
    from app.services.volatility_cache_service import get_volatility_cache_service

    def _universe() -> list:
        try:
            res = get_supabase().rpc(
                "get_top_watchlist_tickers", {"n": 200}
            ).execute()
            return [
                str(r["ticker"]).upper()
                for r in (res.data or []) if r.get("ticker")
            ]
        except Exception as e:
            logger.warning(
                "Volatility precompute: watchlist read failed: %s: %s",
                type(e).__name__, e,
            )
            return []

    await asyncio.sleep(200)  # after the sweeper's 150s startup stagger

    while True:
        # Compute FIRST, then sleep to the next 08:00 UTC. On a cold start (empty
        # or >36h-stale ticker_volatility_cache) the volatility trigger would
        # otherwise sit on the fixed-band fallback for the whole universe until the
        # next 08:00 — the just-shipped feature dark for up to ~24h. skip_if_fresh_
        # hours=20 makes this loop-head run a cheap no-op when rows are already
        # fresh (a steady-state redeploy skips everything), so it is safe to run
        # every iteration and it is what actually makes the "resume" path reachable.
        try:
            tickers = await asyncio.to_thread(_universe)
            symbols = list(dict.fromkeys(tickers + [MARKET_INDEX_SYMBOL]))
            written = await get_volatility_cache_service().recompute_universe(
                symbols, skip_if_fresh_hours=20,
            )
            logger.info("Volatility precompute job completed: %d rows", written)
        except Exception as e:
            logger.error(
                "Volatility precompute job failed: %s", e, exc_info=True,
            )

        now = datetime.now(timezone.utc)
        next_run = _next_daily_run(now, hour_utc=8)
        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            "Volatility precompute job: next run at %s (sleeping %.1fh)",
            next_run.isoformat(), sleep_seconds / 3600,
        )
        await asyncio.sleep(sleep_seconds)


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
    #   base + 120 min → industry_benchmark.recompute_all (sector + industry medians)
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

        # ── Sector + industry benchmarks (vs-industry overlay) @ base + 120 min ──
        # Replaces the retired weekly sector-only job: ONE pass computes every
        # industry median AND the industry='' sector aggregate over the broad
        # ~$500M-floor universe (`benchmark_universe.json`). Started last (after
        # moat) so its FMP burst can't overlap the upstream refreshes.
        # `skip_if_fresh_hours=24` keeps the quarterly run from redoing rows an
        # operator already triggered manually within the last day. The universe
        # file is regenerated out-of-band (manual `python -m
        # scripts.build_benchmark_universe`) — industries shift slowly, so the
        # committed universe is stable between quarterly recomputes.
        await _wait_until(next_run + _td(minutes=120))
        try:
            from app.services.industry_benchmark_service import (
                get_industry_benchmark_service,
            )

            industry_bench_summary = (
                await get_industry_benchmark_service().recompute_all(
                    skip_if_fresh_hours=24,
                )
            )
            logger.info(
                f"Industry benchmark quarterly batch completed: {industry_bench_summary}"
            )
        except Exception as e:
            logger.error(
                f"Industry benchmark quarterly batch failed: {e}", exc_info=True,
            )


# Set once the hydration job's first politician sweep completes. The pre-warmer waits on
# it so the two lifespan tasks are ordered explicitly rather than by hopeful sleeps.
_politician_sweep_done = asyncio.Event()


async def _run_whale_profile_pre_warmer():
    """Rebuild `whale_profile_cache` for every whale, once, shortly after boot.

    Why this exists: `whale_profile_cache` is written in exactly ONE place — the request
    path — so after a restart the first visitor to each whale pays a full rebuild.
    Measured cost of warming the whole roster: 55 whales served from a stored
    `whale_filing_snapshots` row with ZERO FMP calls, and one whale (no snapshot at all)
    that reaches FMP. Bounded by `WHALE_PREWARM_CONCURRENCY`.

    One-shot, not a loop: `whale_profile_cache` has a 24h TTL and a 13F snapshot changes
    QUARTERLY, so there is nothing to re-warm on an interval. The hydration job already
    owns refreshing the underlying data.
    """
    from app.config import settings

    if not getattr(settings, "WHALE_PREWARM_ENABLED", True):
        logger.info("Whale profile pre-warm disabled by config")
        return

    # Wait for the hydration job's first politician sweep to FINISH, so a whale being
    # rewritten underneath us is warmed from its NEW snapshot rather than immediately
    # invalidated (the sweep deletes each whale's whale_profile_cache row as it goes).
    #
    # ⚠️ An event, not a magic sleep. The previous `sleep(180)` claimed that guarantee and
    # did not provide it: the sweep STARTS at t=120s and has no bounded duration, so the
    # pre-warmer woke 60s INTO it and any whale swept after t=180 had its just-written
    # warm thrown away.
    #
    # The timeout is load-bearing: a hung or failed sweep must never disable warming for
    # the process lifetime, so we fall through and warm anyway.
    try:
        await asyncio.wait_for(_politician_sweep_done.wait(), timeout=600)
        logger.info("Whale profile pre-warm: politician sweep finished, warming now")
    except asyncio.TimeoutError:
        logger.warning(
            "Whale profile pre-warm: politician sweep did not finish within 600s — "
            "warming anyway; whales it rewrites afterwards will simply be rebuilt on view"
        )

    try:
        from app.database import get_supabase
        from app.services.whale_service import warm_whale_profile

        sb = get_supabase()
        rows = (
            sb.table("whales").select("id,name").limit(500).execute()
        ).data or []
        if not rows:
            logger.warning("Whale profile pre-warm: no whales to warm")
            return

        started = time.monotonic()
        # ⚠️ SEQUENTIAL, with a real yield between whales. NOT asyncio.gather.
        #
        # The snapshot-served build path contains no genuine suspension point: its only
        # `await` is on a coroutine that never yields, and every Supabase call underneath
        # is SYNCHRONOUS. So a semaphore around it is inert — it is acquired and released
        # inside one task step and never awaits. Measured with a heartbeat task, a
        # gather over 56 whales produced an 18.2s CONTIGUOUS event-loop stall, and the
        # figure was byte-for-byte identical at concurrency 1, 3 and 56.
        #
        # An 18s stall 180 seconds after every deploy is worse than the cold builds this
        # pre-warm exists to remove. Running them one at a time with an explicit
        # `sleep(0)` between bounds the stall to a SINGLE build (~0.3s) and lets any
        # queued user request through in between. Total wall time is longer; nobody is
        # waiting on it.
        warmed = 0
        for r in rows:
            await warm_whale_profile(str(r["id"]))
            warmed += 1
            # A real yield: hands control back so pending I/O is polled between builds.
            await asyncio.sleep(0)
        logger.info(
            "Whale profile pre-warm complete: %d/%d whale(s) in %.1fs",
            warmed, len(rows), time.monotonic() - started,
        )
    except Exception as e:
        logger.error(
            "Whale profile pre-warm failed: %s: %s",
            type(e).__name__, e, exc_info=True,
        )


async def _run_whale_hydration_job():
    """Background task: hydrate whale profiles.

    - Full hydration daily at 02:00 **UTC**.
    - Politician-only hydration every 6 hours.

    Four scheduling defects this shape had to fix:

    1. ``datetime.now()`` is LOCAL. The docstring, the design doc and the ops runbook
       all say 02:00 UTC, but on any container whose TZ was not UTC the job ran at a
       different wall-clock hour than everything else was reasoned about.
    2. The clock was read ONCE at the top of the loop, before the politician sweep.
       That sweep hits FMP + Gemini for 8 filers and can run for many minutes, so a
       cycle that woke at 01:5x was still holding ``hour == 1`` when it reached the
       daily check — and the full hydration was skipped for that whole day.
    3. ``await asyncio.sleep(3600)`` ran AFTER the work, so every cycle was
       3600s + runtime. The wake-up hour drifted forward and could step straight over
       hour 02 (…01:58 → 03:04), silently skipping the daily run for days at a time.
       Sleeping to the next wall-clock boundary keeps the schedule anchored.
    4. ``await fmp.close()`` sat on the happy path only, so any exception before it
       leaked an ``FMPClient`` — and a fresh one was constructed every hour.
    """
    from datetime import datetime, timedelta, timezone

    await asyncio.sleep(120)  # let app fully start

    politician_interval = 6 * 3600  # 6 hours
    # `None`, not 0.0: `time.monotonic()` is time since BOOT on Linux, so a 0.0 seed
    # made the first sweep wait until monotonic passed 21600 — up to 6 hours after a
    # deploy on a freshly booted host. `None` means "never run", so it runs immediately.
    last_politician_run: Optional[float] = None
    # Seed with TODAY when today's 02:00 window has already passed, so a deploy at
    # 14:00 waits for tomorrow instead of kicking off a full hydration of all 53 whales
    # on every restart. A deploy before 02:00 leaves this None and still runs today.
    _boot = datetime.now(timezone.utc)
    last_full_run_date = _boot.date() if _boot.hour >= 2 else None
    if last_full_run_date is not None:
        # SAY SO. This seed INFERS from the clock that today's run already happened, and
        # it is sometimes wrong: a redeploy or OOM at 02:07 mid-run boots a process that
        # skips the rest of the day, leaving the un-swept whales on yesterday's data with
        # no way for a consumer to compensate (`_get_or_process_latest` prefers the
        # stored snapshot whenever `last_hydrated_at` is set and never rebuilds on age).
        # It self-heals the next day, so the fix is visibility, not a durable marker —
        # an undiagnosable silent skip is the part that is unacceptable.
        logger.info(
            "Whale full hydration: boot at %s UTC is past 02:00 — assuming today's (%s) "
            "run already happened. Next full run %s. If this boot was a mid-run restart, "
            "today's remaining whales keep yesterday's data.",
            _boot.isoformat(timespec="seconds"), _boot.date(),
            _boot.date() + timedelta(days=1),
        )

    async def _with_hydrator(run):
        """Build the clients, hand them to `run`, and ALWAYS close the FMP client."""
        from scripts.hydrate_whales import WhaleHydrator
        from app.integrations.fmp import FMPClient
        from app.integrations.gemini import GeminiClient

        fmp = FMPClient()
        try:
            hydrator = WhaleHydrator(fmp, GeminiClient())
            await run(hydrator)
        finally:
            try:
                await fmp.close()
            except Exception as close_err:      # never mask the original failure
                logger.warning(
                    "FMP client close failed after whale hydration: %s: %s",
                    type(close_err).__name__, close_err,
                )

    while True:
        current_time = time.monotonic()

        # ── Politicians: every 6 hours ──────────────────────────────────
        if (
            last_politician_run is None
            or current_time - last_politician_run >= politician_interval
        ):
            try:
                async def _politicians(hydrator):
                    from app.database import get_supabase
                    sb = get_supabase()
                    politicians = (
                        sb.table("whales")
                        .select("*")
                        .in_(
                            "data_source",
                            ["congressional_house", "congressional_senate"],
                        )
                        .limit(500)
                        .execute()
                    )
                    for whale in (politicians.data or []):
                        try:
                            await hydrator._hydrate_one(whale)
                        except Exception as e:
                            logger.error(
                                "Politician hydration failed for %s: %s: %s",
                                whale.get("name"), type(e).__name__, e,
                                exc_info=True,
                            )

                await _with_hydrator(_politicians)
                last_politician_run = current_time
                logger.info("Politician whale hydration completed")
            except Exception as e:
                logger.error(
                    "Politician whale hydration job failed: %s: %s",
                    type(e).__name__, e, exc_info=True,
                )
                # Still stamp it: a hard failure must not turn into a retry every
                # hour against an upstream that is already unhappy.
                last_politician_run = current_time
            finally:
                # Release the pre-warmer whether the sweep succeeded or not — it is an
                # ORDERING signal, not a success signal. Gating it on success would let
                # one bad sweep suppress warming for the whole process lifetime.
                _politician_sweep_done.set()

        # ── Full hydration: daily at 02:00 UTC ──────────────────────────
        # Clock re-read HERE, after the sweep above, and guarded by the DATE it last
        # ran rather than by an exact hour equality — so a cycle that overshoots 02:00
        # still runs, once, instead of skipping the day.
        now = datetime.now(timezone.utc)
        if now.hour >= 2 and last_full_run_date != now.date():
            last_full_run_date = now.date()
            try:
                await _with_hydrator(lambda h: h.run())
                logger.info("Full whale hydration completed (UTC %s)", now.date())
            except Exception as e:
                logger.error(
                    "Full whale hydration job failed: %s: %s",
                    type(e).__name__, e, exc_info=True,
                )

        # Sleep to the top of the next hour rather than a flat 3600s, so the wake-up
        # time cannot drift forward past the 02:00 window.
        now = datetime.now(timezone.utc)
        seconds_past_hour = now.minute * 60 + now.second
        await asyncio.sleep(max(60, 3600 - seconds_past_hour))


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


# Largest JSON body any non-upload route legitimately sends. The settings blob is
# capped at 16 KiB by `MAX_PREFERENCES_BYTES`, but that check runs AFTER FastAPI has
# materialised the body, json.loads has built the object graph, and sanitize has built
# a second dict — so a 50 MB blob costs ~150 MB of RSS per in-flight request before
# anything rejects it. This rejects on Content-Length, before the body is read.
_MAX_JSON_BODY_BYTES = 1 * 1024 * 1024

# Deliberately NOT applied globally: chat streams SSE and the report path proxies a
# PDF through this same stack, so a blanket body cap would be a new failure mode on
# routes that legitimately stream. Scoped to the JSON write routes that take a
# client-authored document.
# `/users/me` joins the list because `PATCH /users/me` writes `display_name` into a bare `text`
# column that `get_current_user` re-reads (via `select("*")`) on EVERY authenticated request.
# The Pydantic bound is the real guard; this is the cheap outer one that rejects the body before
# it is parsed at all.
# `/me/investor-profile` joined these because it is the one guest-writable, unauthenticated
# JSON write in the app: the body is materialised and json.loads'd BEFORE Pydantic's
# per-field `max_length` can fire, so without a cap a caller could post 50 MB and burn
# ~150 MB of RSS per in-flight request with no credential at all.
_BODY_CAPPED_PATH_SUFFIXES = ("/me/settings", "/users/me", "/me/investor-profile")


@app.middleware("http")
async def cap_json_body(request: Request, call_next):
    if request.method in ("PUT", "POST", "PATCH") and request.url.path.endswith(
        _BODY_CAPPED_PATH_SUFFIXES
    ):
        raw_length = request.headers.get("content-length")
        try:
            declared = int(raw_length) if raw_length is not None else 0
        except ValueError:
            declared = 0
        if declared > _MAX_JSON_BODY_BYTES:
            # Local import, matching the other handlers in this file (app.api.error_response
            # imports from app.*, so a module-level import here would be circular).
            from app.api.error_response import ErrorCode, make_error_body

            logger.warning(
                "rejected oversized body on %s: %s bytes (cap %s)",
                request.url.path, declared, _MAX_JSON_BODY_BYTES,
            )
            return JSONResponse(
                status_code=413,
                content=make_error_body(
                    ErrorCode.INVALID_INPUT,
                    message=f"request body exceeds {_MAX_JSON_BODY_BYTES} bytes",
                    user_message="Your settings couldn't be saved. Please try again.",
                ),
            )
    return await call_next(request)


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
    """422s carry the FIRST validator message, on the error contract iOS decodes.

    This used to replace every body with `{"detail": "Invalid request data"}`, which iOS
    cannot decode as `APIErrorResponse` — so it surfaced the generic "Validation failed".
    The visible cost was the password rules in `schemas/auth.py`: register with a trailing
    space and the real reason ("Password can't start or end with a space") was discarded,
    the sign-in screen showed "Sign in failed. Please try again.", and `canSubmit` only
    checks length so the form re-enabled and the user could retry the same input forever
    with no way to learn what was wrong.

    Only the message is surfaced, never the raw pydantic error list: `exc.errors()` carries
    the input value, which on these routes is a password. DEBUG keeps the full list.
    """
    first = (exc.errors() or [{}])[0]
    raw = str(first.get("msg") or "").strip()
    # Pydantic prefixes custom ValueErrors with "Value error, " — noise to a user.
    message = raw.removeprefix("Value error, ") or "Invalid request data"
    from app.api.error_response import ErrorCode, make_error_body

    body = make_error_body(
        ErrorCode.INVALID_INPUT,
        message=f"request validation failed: {message}",
        user_message=message,
    )
    if settings.DEBUG:
        # REDACT `input` before echoing the error list. `exc.errors()` embeds the offending
        # value, and on `/auth/login`, `/auth/register` and `/auth/change-password` that value
        # IS the password — so a validation failure would put a plaintext credential in the
        # response body, the terminal, and any proxy log in between. `loc` and `msg` are what
        # make the error diagnosable; the value never was.
        safe = [
            {k: v for k, v in err.items() if k not in ("input", "ctx")}
            for err in exc.errors()
        ]
        body["details"] = {"errors": str(safe)}
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=body,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Let an `HTTPException` carry the structured error body directly.

    FastAPI's built-in handler always renders `{"detail": <whatever>}`. That is fine for the
    ~100 raise sites in this codebase that pass a plain string, and wrong for auth rejections,
    which happen inside a DEPENDENCY — there is no handler yet to `return
    make_error_response(...)`, so raising was the only option and the contract body had nowhere
    to go (CLAUDE.md invariant #3).

    The rule here is deliberately narrow:
      * `detail` is a dict  → it IS the body, emitted verbatim (see `auth_error`).
      * `detail` is anything else → `{"detail": ...}`, byte-for-byte what FastAPI already did.

    Narrow on purpose. A blanket "reshape every 401/403 into the contract" would have changed
    the body of every existing raise, and `APIClient.validateResponse` already has per-status
    behaviour keyed off those shapes — 404 and 422 in particular try the structured decode and
    fall back to a typed error. Opting in per raise site keeps this change additive.

    `headers` is forwarded so `WWW-Authenticate` and `Retry-After` survive.
    """
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
            headers=getattr(exc, "headers", None),
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
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
    """READINESS: is the app able to serve, dependencies included?

    Round-trips Supabase, so it costs 300-500ms. Not used by Railway's deploy gate
    (`railway.toml` points at `/health/pdf`) — keep it that way, because a Supabase blip
    would otherwise make the platform kill a perfectly healthy container.
    """
    db_ok = await check_supabase_health()
    return {
        "status": "healthy" if db_ok else "degraded",
    }


@app.get("/health/live", tags=["Root"])
async def health_live():
    """LIVENESS: is a server listening on this port? Instant, zero dependencies.

    Exists because the iOS `ServerEnvironmentManager` probes localhost on every launch and
    foreground with a sub-second timeout to decide between the local backend and Railway, and
    it was probing `/health` — which waits on Supabase. Measured at 0.34-0.52s against a 0.5s
    timeout, so the probe was a coin flip, and losing it silently routed a DEVELOPMENT build
    at PRODUCTION. That is the worst possible failure mode: you believe you are testing your
    local changes and you are exercising the live service. It cost a mis-verification during
    the session that added this.

    Deliberately returns before touching anything — no DB, no cache, no settings read.
    """
    return {"status": "alive"}


@app.get("/.well-known/apple-app-site-association", include_in_schema=False)
async def apple_app_site_association():
    """Apple's associated-domains manifest. Required for passkeys AND Password AutoFill.

    `webcredentials` is what binds saved passwords and passkeys to this app: without it, an
    `ASAuthorizationPlatformPublicKeyCredentialProvider` request returns an error outright, and
    the `.textContentType(.username)` field in SignInView has no domain to offer credentials
    for. The entitlement alone is not enough — Apple fetches THIS file and checks that the app
    it names matches the one asking.

    Four things Apple is strict about, all of which have to hold in production:

      1. Served over HTTPS from the exact RP ID host, with **no redirects**. An HTTP→HTTPS or
         apex→www redirect fails the check silently. This is why the domain is pointed straight
         at the backend rather than left on a parking page.
      2. `Content-Type: application/json`. FastAPI's JSONResponse sets it; do not "helpfully"
         serve this as a static text file.
      3. No `.json` extension on the path.
      4. It must NOT be behind auth. This route is intentionally public — it contains no
         secrets, only a Team ID and bundle id, both of which ship inside the app anyway.

    ⚠️ The RP ID is effectively permanent: changing it invalidates every passkey already
    enrolled. `caydexinvest.com` is the committed choice — see APPLE_APP_SITE_ASSOCIATION_*
    settings in config.py.
    """
    return JSONResponse(
        content={
            "webcredentials": {
                "apps": [f"{settings.APPLE_TEAM_ID}.{settings.APPLE_BUNDLE_ID}"]
            }
        },
        media_type="application/json",
    )


@app.get("/health/pdf", tags=["Root"])
async def health_pdf():
    """Verify the PDF stack end-to-end. Returns 503 when it can't render, so a
    misconfigured image fails the Railway deploy gate instead of the first user PDF.

    Importing weasyprint already probes the native libs (cairo/pango load at import
    time), but that alone can NOT catch a weasyprint/pydyf version mismatch — that
    fails inside `write_pdf`, e.g. the pre-0.11 `transform()` API break the pin in
    requirements.txt guards against. So this actually renders a one-line document.
    Cheap (a few ms, no I/O) and it exercises the exact call path the report PDF uses.
    """
    try:
        import io

        import weasyprint

        buf = io.BytesIO()
        weasyprint.HTML(string="<p>ok</p>").write_pdf(buf)
        data = buf.getvalue()
        if not data.startswith(b"%PDF-"):
            raise RuntimeError(f"renderer produced {len(data)} bytes, not a PDF")

        import pydyf

        return {
            "status": "healthy",
            "weasyprint": weasyprint.__version__,
            "pydyf": getattr(pydyf, "__version__", "unknown"),
            "rendered_bytes": len(data),
        }
    except Exception as e:
        logger.error("PDF healthcheck FAILED: %s: %s", type(e).__name__, e, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "error": f"{type(e).__name__}: {e}"},
        )


# `GET /disclaimer` was removed 2026-08-07. It served `settings.LEGAL_DISCLAIMER` and was
# confirmed dead: repo-wide, the path string occurred exactly once — its own definition. No
# iOS call site (`APIEndpoint`/`APIClient` never referenced it), no test, no doc. Every
# disclaimer the app shows is bundled client-side, so wiring it would have duplicated copy
# that already ships, and a route nobody calls is a route nobody keeps accurate.
# `settings.LEGAL_DISCLAIMER` itself stays — `chat_security.py` uses it to append the
# disclaimer to AI replies.


# ── Public legal pages ─────────────────────────────────────────────────────────
#
# App Store Connect requires a **Privacy Policy URL** and a **Support URL**, both of which
# must resolve to real pages a reviewer can open. They are served from here rather than from
# a separate static host so that the one custom domain (`caydexinvest.com`, already pointed at
# this service for the Apple associated-domains manifest) covers every URL Apple needs. No
# second host, no second certificate, nothing else to keep alive.
#
# ⚠️ The files live at `backend/app/templates/legal/`, NOT at the repo-root `documents/legal/`.
# `backend/Dockerfile` builds with the `backend/` directory as its context (`COPY . .`), so
# anything outside `backend/` simply does not exist in the deployed container — the route would
# 404 in production while working perfectly on a laptop. `tests/test_legal_pages.py` asserts the
# served copies still match the authored originals, so the duplication cannot drift silently.
_LEGAL_DIR = Path(__file__).resolve().parents[0] / "templates" / "legal"

_LEGAL_PAGES = {
    "privacy": "privacy.html",
    "terms": "terms.html",
    "support": "support.html",
}


def _render_legal(name: str) -> HTMLResponse:
    """Read and return a legal page, or a 404 that says which file is missing.

    Read per request rather than cached at import: these are ~10 KB, served a handful of times
    a day (App Review, and the occasional user), and a stale cache after a content edit is a
    worse failure than the read. If that ever changes, cache it — but not before.
    """
    path = _LEGAL_DIR / _LEGAL_PAGES[name]
    try:
        html = path.read_text(encoding="utf-8")
    except OSError as e:
        # Loud, because a missing legal page is an App Review rejection, and the most likely
        # cause is a deploy that did not include the file.
        logger.error(
            "Legal page %r could not be read from %s: %s: %s",
            name, path, type(e).__name__, e,
        )
        raise StarletteHTTPException(
            status_code=404, detail=f"{name} page is unavailable"
        ) from e
    return HTMLResponse(content=html)


@app.get("/privacy", tags=["Root"], include_in_schema=False)
async def privacy_policy():
    """Privacy Policy. Set as the App Store Connect Privacy Policy URL."""
    return _render_legal("privacy")


@app.get("/terms", tags=["Root"], include_in_schema=False)
async def terms_of_use():
    """Terms of Use, referenced from the in-app Legal screen."""
    return _render_legal("terms")


@app.get("/support", tags=["Root"], include_in_schema=False)
async def support_page():
    """Support page. Set as the App Store Connect Support URL.

    ASC will not accept a bare `mailto:` there, and a reviewer does open it — it is also where
    account deletion is documented, which Apple requires to be discoverable.
    """
    return _render_legal("support")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
