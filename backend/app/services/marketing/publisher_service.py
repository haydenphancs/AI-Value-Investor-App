"""
Marketing publisher loop — the web-lifespan half of the marketing engine (design doc §12).

The MEDIA WORKER (a separate Railway cron service, `marketing/main.py`) makes the
day's artefacts and records one `marketing_posts` row per outlet. THIS loop, running in the
web process where the social-posting secrets live, is the only thing that ever calls a
platform: it takes `approved` rows one at a time — `claim_post` is an atomic
`approved → queued` UPDATE, so a restart mid-fan-out or two ticks close together cannot
double-post — and records `published | failed` with the platform's id/URL and cost.

Shape: an INTERVAL loop like `_run_notification_dispatch_loop`, not a daily claim. Posts
become `approved` at arbitrary moments (a reviewer's Approve — `run_service.review_post`, reached
from the Telegram review bot) and Upload-Post jobs complete asynchronously, so it has to wake often
enough to publish AND to reconcile.

Phase 1 ships the loop and the claim discipline with NO adapters: it counts what is waiting
and logs it, touching no row. Phase 5 adds `integrations/upload_post.py`, `integrations/x_api.py`
and the per-platform dispatch, and the reconcile step. Everything is gated on
`MARKETING_ENABLED` (default False) and honours `MARKETING_DRY_RUN` (default True).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.config import settings
from app.services.marketing import review_service
from app.services.marketing.run_service import get_marketing_run_service

logger = logging.getLogger(__name__)

# Adapters register here in Phase 5: platform → async callable(post) -> result dict.
# Empty on purpose in Phase 1 — an empty registry means "count, log, touch nothing".
PUBLISHERS: Dict[str, Any] = {}


async def publish_cycle() -> Dict[str, int]:
    """One tick. Returns counters so a test (and the log line) can see what happened.

    With no adapters registered the cycle only OBSERVES. It must never move a row it cannot
    publish — a `queued` row with nobody to publish it would sit there looking claimed.
    """
    svc = get_marketing_run_service()
    counters = {"approved_waiting": 0, "published": 0, "failed": 0, "skipped": 0}
    platforms = sorted(PUBLISHERS)
    if not platforms:
        # Phase 1-4 state: rows accumulate (and get reviewed) while no adapter exists yet.
        approved = await svc.list_posts("approved", limit=100)
        counters["approved_waiting"] = len(approved)
        if approved:
            logger.info(
                "marketing publisher: %d approved post(s) waiting, no adapters registered yet "
                "(platforms=%s) — nothing sent",
                len(approved), sorted({p.get("platform") for p in approved}),
            )
        return counters

    # Only rows an adapter can send — and, live, only non-rehearsal rows — are fetched: the filter
    # runs IN the query, before its LIMIT. Filtering afterwards let 100 older rows no adapter could
    # send (an approved Instagram post with only X wired, an approved rehearsal) fill the window,
    # and every sendable post behind them waited forever with no symptom but silence.
    live = not settings.MARKETING_DRY_RUN
    approved = await svc.list_posts("approved", limit=100, platforms=platforms, live_only=live)
    counters["approved_waiting"] = len(approved)
    for post in approved:
        # Dry-run is decided BEFORE the claim, so a rehearsal touches no row at all: a
        # claim-then-put-back would leave a crash window in which a dry-run post sits `queued`
        # forever. Either the web process's switch or the run's own flag (carried on every
        # row by `create_posts`) makes it a rehearsal — the row check is defence in depth
        # behind the `live_only` query filter.
        if settings.MARKETING_DRY_RUN or bool((post.get("metadata") or {}).get("dry_run")):
            logger.info(
                "marketing publisher DRY_RUN: would publish post_id=%s platform=%s key=%s",
                post["id"], post["platform"], post["idempotency_key"],
            )
            counters["skipped"] += 1
            continue
        claimed = await svc.claim_post(post["id"])
        if claimed is None:
            counters["skipped"] += 1
            continue
        adapter = PUBLISHERS[claimed["platform"]]
        try:
            result = await adapter(claimed)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "marketing publish FAILED post_id=%s platform=%s key=%s: %s: %s",
                claimed["id"], claimed["platform"], claimed["idempotency_key"],
                type(e).__name__, e, exc_info=True,
            )
            await svc.mark_post(
                claimed["id"], "failed",
                last_error=f"{type(e).__name__}: {e}"[:2000],
                attempts=int(claimed.get("attempts") or 0) + 1,
            )
            counters["failed"] += 1
            continue
        # The outlet accepted it. From here a ledger failure must NEVER flip the row to
        # `failed` — a retry would double-post. Record loudly and leave it `queued` for the
        # reconcile step (Phase 5) / a human, with the external id in the log line.
        try:
            await svc.mark_post(
                claimed["id"], "published",
                external_id=result.get("external_id"),
                external_url=result.get("external_url"),
                cost_micros=int(result.get("cost_micros") or 0),
                attempts=int(claimed.get("attempts") or 0) + 1,
                published_at=result.get("published_at"),
            )
            counters["published"] += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "marketing post PUBLISHED BUT LEDGER WRITE FAILED post_id=%s platform=%s key=%s "
                "external_id=%s external_url=%s: %s: %s — row left `queued`; reconcile by hand",
                claimed["id"], claimed["platform"], claimed["idempotency_key"],
                result.get("external_id"), result.get("external_url"),
                type(e).__name__, e, exc_info=True,
            )
            counters["published"] += 1
    return counters


async def publisher_tick() -> None:
    """One wake of the loop: the Telegram review sweep, then the publish cycle. Each half has its
    own guard, so a failing sweep can never stop publishing (or the reverse)."""
    # The review sweep runs whatever MARKETING_ENABLED says: posts are reviewed while the
    # publisher is still off (Phases 1-4) — its own switch is the MARKETING_TELEGRAM_* settings.
    if review_service.is_configured():
        try:
            review = await review_service.review_cycle()
            if review["pending"] or review["failed"] or review["rate_limited"]:
                logger.info("marketing review sweep: %s", review)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "marketing review sweep failed (%s: %s)", type(e).__name__, e, exc_info=True,
            )
    if settings.MARKETING_ENABLED:
        try:
            counters = await publish_cycle()
            if counters["approved_waiting"] or counters["published"] or counters["failed"]:
                logger.info("marketing publisher cycle: %s", counters)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # One bad cycle must not kill the loop; a dead publisher looks exactly like
            # "nothing is ever posted" and has no other symptom.
            logger.error(
                "marketing publisher cycle failed (%s: %s)",
                type(e).__name__, e, exc_info=True,
            )


async def run_marketing_publisher_loop() -> None:
    """Background task registered by `app/main.py` via `_spawn`. Publishing sleeps quietly while
    MARKETING_ENABLED is False (checked every cycle, so a Railway variable flip needs only a
    restart, not a deploy); the review sweep runs whenever the review bot is configured."""
    await asyncio.sleep(60)  # stagger past the startup pre-warm burst
    interval = max(int(settings.MARKETING_PUBLISHER_INTERVAL_SECONDS), 30)
    while True:
        await publisher_tick()
        await asyncio.sleep(interval)
