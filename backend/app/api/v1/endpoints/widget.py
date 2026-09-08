"""
Home-screen widget endpoints.

  GET  /api/v1/widget/market-mover        the day's most unusual tracked move
  GET  /api/v1/widget/portfolio-mover     the same, over the caller's holdings
  GET  /api/v1/widget/token               the extension's market-scoped credential

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

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import create_widget_token, widget_token_expires_at
from app.dependencies import (
    StandardRateLimit,
    WidgetRateLimit,
    get_current_user_id,
    get_watchlist_identity,
    get_widget_caller,
)
from app.schemas.widget import WidgetMoverPayload, WidgetTokenResponse
from app.services.active_group_service import (
    ActiveGroupUnavailable,
    get_active_group,
)
from app.services.widget_movers_service import get_widget_movers_service

logger = logging.getLogger(__name__)

# 🔒 ACCOUNT-ONLY. Every route on this router requires a real account.
#
# FMP's signed Order Form grants End-User Display Rights — Exhibit A's *Access-Restricted
# External Display* — so their market data may be shown only "through the Licensee's
# authenticated platform". Public External Display was declined (2026-09-04), which makes a
# signed-out response on any of these routes a licence breach, not a product choice.
#
# Declared on the ROUTER rather than per-route on purpose: a route added to this file
# tomorrow is authenticated by default. The per-route form relies on the author remembering,
# and this file alone has 2 routes — the failure mode is silent (a 200 with real prices)
# and nothing downstream would notice.
router = APIRouter(dependencies=[Depends(get_current_user_id)])

# The ONE route the Home Screen extension calls for itself, on its own router.
#
# `CaydexWidgets` is a separate process holding no session: the App Group is its only channel to
# the app, and it cannot refresh a token because refresh is main-actor and lives in the app
# (auth.md §8). It authenticates with a long-lived, market-scoped widget token instead — see the
# WIDGET TOKEN block in `core/security.py`.
#
# ⚠️ A SECOND ROUTER, not a relaxed dependency on the first, and that is the whole point. If
# `get_widget_caller` were moved onto `router`, every route added to this file afterwards would
# silently accept a widget token — including one returning the caller's holdings, which the token
# is only defensible because it cannot reach. Landing on the STRICT router by default is the
# fail-closed half of auth.md §1; a route joins this one only by someone typing its name here.
#
# `tests/test_widget_token_auth.py` pins that this router holds exactly one route.
widget_client_router = APIRouter(dependencies=[Depends(get_widget_caller)])

# How many of the caller's holdings are RANKED.
#
# This is a cap on the ranking input, not on what is rendered — and that distinction is
# why 30 was wrong. Holdings arrive ordered by `position` (active group) or `added_at
# desc` (watchlist), so truncating at 30 dropped tickers by RECENCY before anything was
# ranked: a user with 40 holdings was told the biggest mover among the 30 they happened
# to add most recently, and `basket.total_count` then stated a denominator that was not
# their portfolio. Neither is detectable from the tile.
#
# 200 costs nothing extra. `get_batch_quotes_bulk` chunks at 300, so the whole set plus
# the index symbols is still ONE request — market mode already ranks 200 this way. It
# also matches `_MAX_UNIVERSE`, so neither mode can be asked about a wider scope than
# the other.
_MAX_HOLDINGS = 200

_MAX_SCOPE_LEN = 32


def _valid_ticker(t: str) -> bool:
    if not t or len(t) > _MAX_SCOPE_LEN:
        return False
    return all(c.isalnum() or c in ".-^=" for c in t)


def _empty(mode: str) -> WidgetMoverPayload:
    """Last-resort payload. Renders as the widget's empty state, not an error."""
    from datetime import datetime, timezone

    from app.utils.market_hours import (
        session_label,
        session_phase,
        session_trading_date,
    )

    # Even the empty payload carries an honest time anchor: a blank tile that also
    # cannot say WHEN it went blank is indistinguishable from a broken one.
    return WidgetMoverPayload(
        mode=mode,
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_session=session_phase(),
        session_date=session_trading_date().isoformat(),
        session_label=session_label(),
    )


@widget_client_router.get("/market-mover", response_model=WidgetMoverPayload)
async def get_market_mover(_rate_limit=WidgetRateLimit) -> WidgetMoverPayload:
    """The most unusual move among the tickers Caydex tracks, and why.

    ⚠️ This docstring used to open "Public on purpose". It was, and that is exactly why the
    extension could refresh itself. It stopped being true on 2026-09-07: End-User Display Rights
    permit FMP data only "through the Licensee's authenticated platform", so it now takes either
    a session bearer (the app's own call, via `APIClient`) or a widget token (the extension's).
    What has NOT changed is that the payload is market-wide — a shared universe, nothing about
    the caller — which is the entire reason a long-lived token is defensible here and nowhere
    else.

    `WidgetRateLimit`, not `StandardRateLimit`: the latter would bucket every widget on Earth
    under one shared guest key. See `WidgetRateLimitChecker`.

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


@router.get("/token", response_model=WidgetTokenResponse)
async def issue_widget_token(user_id: str = Depends(get_current_user_id)) -> WidgetTokenResponse:
    """Mint the Home Screen extension's market-data credential for the signed-in caller.

    On the STRICT router: you must already hold a real session to be issued one. The token that
    comes back is not a session and can never become one — `_decode_access_token` allow-lists
    `type == "access"`, so presenting this as a bearer is a 401 on every authenticated route
    (`tests/test_widget_token_auth.py` proves it rather than asserting it).

    Not rate-limited beyond the router's own gate: the client asks at most once per sign-in and
    once per renewal window (30 days), and a caller able to reach this route already holds the
    strictly more powerful credential.
    """
    token = create_widget_token(user_id)
    expires = widget_token_expires_at(token)
    # `widget_token_expires_at` re-decodes what we just minted, so None here means the token we
    # are about to hand out is unreadable by our own verifier — worth an error, not a shrug.
    if expires is None:
        logger.error("widget token minted but its exp is unreadable (user=%s)", user_id)
        raise HTTPException(
            status_code=500, detail="Could not issue a widget token. Please try again."
        )
    return WidgetTokenResponse(
        token=token, expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


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
