"""Analytics ingest endpoint.

`POST /api/v1/events` — the app's only write path for product telemetry.

Two properties matter more than anything else this endpoint does:

  1. **It can never break the app.** The client is fire-and-forget, and this returns
     200 even when the insert fails. A non-200 would make a retrying client amplify a
     Supabase outage into a write storm, and a 500 here would surface to users as an
     error caused purely by instrumentation.
  2. **It is bounded.** This is the one route accepting arbitrary client structure, so
     it carries a rate limit, a batch cap, an event-name allowlist, and per-prop size
     caps (see `app/schemas/analytics.py`). Without those, an events table is both a
     storage bomb and somewhere user-typed text can leak.

Identity is the same per-INSTALL bucket used by chat and the guest report budget
(`identity_key`), so guest activity is attributable to an install and a guest → signup
funnel can be stitched later without ever needing a `public.users` row.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header

from app.dependencies import (
    AnalyticsRateLimit,
    get_identity_only_user,
    identity_key,
)
from app.schemas.analytics import AnalyticsBatchRequest, AnalyticsBatchResponse
from app.services.analytics_service import get_analytics_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/events", response_model=AnalyticsBatchResponse)
async def ingest_events(
    body: AnalyticsBatchRequest,
    # Token-only identity: `get_current_user_or_guest` reads public.users and raises 503
    # when that fails, which would destroy the batch (the iOS Analytics actor drops
    # events from its buffer BEFORE the request and never re-queues). Only identity_key
    # is needed here, and for an authed caller that is just the token's `sub`.
    user: dict = Depends(get_identity_only_user),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    x_app_version: Optional[str] = Header(None, alias="X-App-Version"),
    x_platform: Optional[str] = Header(None, alias="X-Platform"),
    _rate: None = AnalyticsRateLimit,
):
    """Record a batch of client events. Always 200 — see the module docstring."""
    if not body.events:
        return AnalyticsBatchResponse(accepted=0, dropped=0)

    import asyncio

    bucket = identity_key(user, x_guest_id)

    def _record() -> tuple[int, int]:
        # Service CONSTRUCTION happens in here too, not in the handler body: it calls
        # get_supabase(), and if that ever throws, an eagerly-built service would 500
        # this endpoint — the exact instrumentation-caused error this module forbids.
        try:
            return get_analytics_service().record_batch(
                bucket,
                body.events,
                session_id=body.session_id,
                app_version=(x_app_version or "")[:40] or None,
                platform=(x_platform or "")[:20] or None,
            )
        except Exception as e:
            logger.warning(
                "analytics: ingest failed outside record_batch: %s: %s",
                type(e).__name__, e,
            )
            return 0, len(body.events)

    # The Supabase SDK is synchronous; calling it directly would block the event loop
    # for every request the app makes on backgrounding.
    accepted, dropped = await asyncio.to_thread(_record)
    return AnalyticsBatchResponse(accepted=accepted, dropped=dropped)
