"""
Home-screen widget endpoints.

  GET  /api/v1/widget/market-mover        the day's most unusual tracked move
  GET  /api/v1/widget/portfolio-mover     the same, over the caller's holdings

DESIGN NOTE — the read path never calls Gemini, same as `updates.py`. Everything
served here was produced by the background insight sweeper; these handlers read
caches. That matters more for a widget than for a screen: WidgetKit schedules
refreshes itself, unattended, and the client cannot be rate-limited into behaving.

WHY TWO ROUTES INSTEAD OF ONE WITH `?mode=`
--------------------------------------------
`APIEndpoint.authPolicy` on iOS is an exhaustive switch with one policy per case,
and `tests/test_ios_auth_policy_parity.py` asserts each case matches its backend
dependency. A single route whose auth requirement changed with a query parameter
cannot be expressed in that model — it would have to be special-cased in the test
that exists to stop auth drift.

It also keeps holdings out of the query string, and therefore out of Railway's
access logs.

DEGRADE, NEVER ERROR
--------------------
A widget that renders an error message is worse than one rendering yesterday's
close — the user cannot retry it, cannot see why, and it sits on their Home Screen
looking broken. So every failure path here returns a valid payload with whatever
was resolvable (usually the market story) rather than raising. No new `ErrorCode`.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.dependencies import StandardRateLimit, get_watchlist_identity
from app.schemas.widget import WidgetMoverPayload
from app.services.active_group_service import (
    ActiveGroupUnavailable,
    get_active_group,
)
from app.services.widget_movers_service import get_widget_movers_service

logger = logging.getLogger(__name__)

router = APIRouter()

# A widget only ever renders one mover (plus, on the large family, a few
# runners-up), so ranking the whole of a very large group buys nothing and costs a
# bigger batch quote. Matches `_MAX_TABS` in updates.py.
_MAX_HOLDINGS = 30

_MAX_SCOPE_LEN = 32


def _valid_ticker(t: str) -> bool:
    if not t or len(t) > _MAX_SCOPE_LEN:
        return False
    return all(c.isalnum() or c in ".-^=" for c in t)


def _empty(mode: str) -> WidgetMoverPayload:
    """Last-resort payload. Renders as the widget's empty state, not an error."""
    from datetime import datetime, timezone

    from app.utils.market_hours import session_phase

    phase = session_phase()
    return WidgetMoverPayload(
        mode=mode,
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_session=phase,
        is_stale=phase == "closed",
    )


@router.get("/market-mover", response_model=WidgetMoverPayload)
async def get_market_mover(_rate_limit=StandardRateLimit) -> WidgetMoverPayload:
    """The most unusual move among the tickers Caydex tracks, and why.

    Public on purpose: it is market data about a shared universe, carries nothing
    about the caller, and making it public means the widget needs no identity for
    its default mode.

    Ranked by volatility-relative z, not raw percent — so a 3% day on a normally
    calm name outranks 3% on one that moves that much routinely.
    """
    try:
        return await get_widget_movers_service().get_market_mover()
    except Exception as e:
        logger.error(
            "widget market-mover failed: %s: %s", type(e).__name__, e, exc_info=True
        )
        return _empty("market")


@router.get("/portfolio-mover", response_model=WidgetMoverPayload)
async def get_portfolio_mover(
    # Watchlist identity, not the shared guest sentinel — these are the caller's own
    # holdings and must resolve to the same per-install partition the watchlist routes
    # write (migration 108). Reading the shared bucket would show a signed-out user
    # someone else's positions.
    user: dict = Depends(get_watchlist_identity),
    _rate_limit=StandardRateLimit,
) -> WidgetMoverPayload:
    """The caller's biggest mover, plus a combined reason when holdings moved together.

    Follows the ACTIVE GROUP (migration 126) rather than the master watchlist, so the
    widget, the Updates pills, Home and Tracking all describe the same set of tickers.
    """
    user_id = user["id"]
    try:
        tickers: List[str] = []
        try:
            group = await get_active_group(user_id)
        except ActiveGroupUnavailable as e:
            logger.warning(
                "widget: active group unreadable for user=%s (%s) — falling back to "
                "the master watchlist",
                user_id, e,
            )
            group = None

        if group is not None:
            tickers = [t.upper() for t in group.tickers if _valid_ticker(t.upper())]
        else:
            tickers = await _watchlist_tickers(user_id)

        tickers = list(dict.fromkeys(tickers))[:_MAX_HOLDINGS]
        if not tickers:
            # No holdings is a legitimate state, not a failure: the widget shows the
            # market story and an invitation to add something.
            return await get_widget_movers_service().get_market_mover()

        return await get_widget_movers_service().get_portfolio_mover(user_id, tickers)
    except Exception as e:
        logger.error(
            "widget portfolio-mover failed for user=%s: %s: %s",
            user_id, type(e).__name__, e, exc_info=True,
        )
        return _empty("portfolio")


async def _watchlist_tickers(user_id: str) -> List[str]:
    import asyncio

    from app.database import get_supabase

    def _read() -> List[Dict[str, Any]]:
        res = (
            get_supabase()
            .table("watchlist_items")
            .select("ticker")
            .eq("user_id", user_id)
            .order("added_at", desc=True)
            .limit(_MAX_HOLDINGS)
            .execute()
        )
        return res.data or []

    try:
        rows = await asyncio.to_thread(_read)
    except Exception as e:
        logger.error(
            "widget: watchlist read failed for user=%s: %s: %s",
            user_id, type(e).__name__, e, exc_info=True,
        )
        return []
    return [
        str(r["ticker"]).upper()
        for r in rows
        if r.get("ticker") and _valid_ticker(str(r["ticker"]).upper())
    ]
