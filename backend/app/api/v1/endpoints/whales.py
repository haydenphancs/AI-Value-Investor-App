"""
Whale Endpoints — Dual-source whale tracking (13F + Congressional).

Routes:
  GET    /whales                              → List trending/popular whales
  GET    /whales/activity                     → Recent trades from followed whales
  GET    /whales/{whale_id}/profile           → Full whale profile
  GET    /whales/{whale_id}/trade-groups      → All trade groups for a whale
  GET    /whales/{whale_id}/trade-groups/{id} → Single trade group detail
  POST   /whales/{whale_id}/follow            → Follow a whale
  DELETE /whales/{whale_id}/follow            → Unfollow a whale
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
import logging

from app.dependencies import get_current_user, get_watchlist_identity
from app.api.error_response import ErrorCode, make_error_body, make_error_response
from app.schemas.whale import (
    TrendingWhaleResponse,
    WhaleProfileResponse,
    WhaleTradeGroupResponse,
    WhaleTradeGroupActivityResponse,
    WhaleAlertBannerResponse,
    FollowResponse,
)
from app.services.whale_service import (
    WhaleService,
    WhaleFollowLockedException,
    whale_detail_allowed,
)
from app.services.entitlements import required_tier_for_whales
from app.database import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Whale Listing ────────────────────────────────────────────────────


@router.get("", response_model=List[TrendingWhaleResponse])
async def list_whales(
    category: Optional[str] = Query(
        None,
        description="Filter by category: investors, institutions, politicians, crypto",
    ),
    # `get_watchlist_identity`, not `get_optional_user_id`: the latter returns a bare id
    # with NO tier, and the roster needs one to decide which Follow buttons lock. Same
    # dependency home.py and updates.py already read `tier` from; a signed-out caller
    # resolves to a per-install guest hardcoded to "free".
    user: dict = Depends(get_watchlist_identity),
):
    """List all whales, optionally filtered by category.

    Every whale is listed on every tier — the roster is the feature's own demo. Only the
    per-row `is_locked` flag (whose Follow button is a paywall) varies by plan.
    """
    service = WhaleService()
    return await service.get_whale_list(
        category=category, user_id=user.get("id"), tier=user.get("tier")
    )


# ── Activity Feed ────────────────────────────────────────────────────


@router.get("/activity", response_model=List[WhaleTradeGroupActivityResponse])
async def get_whale_activity(
    user: dict = Depends(get_current_user),
):
    """Get recent trade activity from user's followed whales.

    Honours the plan's follow allowance: a Free caller sees only the designated free
    whale's trades, however many rows `whale_follows` holds for them.
    """
    service = WhaleService()
    return await service.get_whale_activity_feed(user["id"], tier=user.get("tier"))


# ── Alerts ──────────────────────────────────────────────────────────


@router.get("/alerts", response_model=Optional[WhaleAlertBannerResponse])
async def get_whale_alerts(
    user: dict = Depends(get_watchlist_identity),
):
    """Get the most recent active whale alert banner.

    Ungated: a banner names a whale and an amount, which the free roster already shows.
    """
    service = WhaleService()
    return await service.get_whale_alerts(user_id=user.get("id"))


# ── Whale Profile ────────────────────────────────────────────────────


@router.get("/{whale_id}/profile", response_model=WhaleProfileResponse)
async def get_whale_profile(
    whale_id: str,
    user: dict = Depends(get_watchlist_identity),
    force_refresh: bool = False,
):
    """Get full whale profile with holdings, trades, and summaries.

    Uses 3-tier cache-aside: in-memory (1h) → Supabase (24h) → FMP live.
    Follow state is always fresh (not cached).

    Every tier gets the header, bio, risk profile, both stat tiles and sector exposure.
    Free has the POSITION-LEVEL detail withheld server-side (holdings / trade groups /
    sentiment) — see `redact_whale_profile`.

    Pass ?force_refresh=true to bypass all caches and rebuild from FMP.
    """
    user_id = user.get("id")
    service = WhaleService()
    try:
        profile = await service.get_whale_profile(
            whale_id=whale_id, user_id=user_id,
            force_refresh=force_refresh,
            tier=user.get("tier"),
            # `is_guest`, not `user_id is None`: this dependency ALWAYS yields an id
            # (the shared guest sentinel, or a per-install uuid5 when the client sends
            # X-Guest-Id), so the service's old `user_id is None` check for the
            # destructive force_refresh lever could never fire. Defaults to True in the
            # service, so a forgotten argument denies rather than allows.
            is_guest=bool(user.get("is_guest", True)),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "[whale_profile] Unhandled error for whale_id=%s, user=%s: %s: %s",
            whale_id, user_id, type(e).__name__, e, exc_info=True,
        )
        # A STRUCTURED body, not a bare string. This used to raise
        # `HTTPException(503, detail="...")`, which the StarletteHTTPException handler
        # renders as `{"detail": "..."}` — a shape iOS's `APIErrorResponse` cannot
        # decode, so `AppError` fell through to its generic "something went wrong"
        # and the user got no actionable message and no retry affordance (invariant #3).
        #
        # The internal exception text is deliberately NOT echoed: the full type,
        # message and stack are in the log above. Only the code and the user copy travel.
        raise HTTPException(
            status_code=503,
            detail=make_error_body(
                ErrorCode.WHALE_PROFILE_UNAVAILABLE,
                message=f"whale profile build failed: {type(e).__name__}",
                user_message=(
                    "We couldn't load this investor right now. Please try again shortly."
                ),
                action="retry",
            ),
        )
    if not profile:
        # Also structured — "no such whale" is a distinct, actionable state, and the
        # previous bare-string 404 was indistinguishable from the 503 above on iOS.
        raise HTTPException(
            status_code=404,
            detail=make_error_body(
                ErrorCode.WHALE_NOT_FOUND,
                message=f"no whale row for id={whale_id}",
            ),
        )
    return profile


# ── Trade Groups ─────────────────────────────────────────────────────


# Both routes below took NO auth dependency at all until the tier gate landed. That made
# the profile's Recent Trades redaction bypassable by anyone who knew a whale id — the
# withheld trade groups were served in full from here. They are the same paid data, so they
# take the same gate.


def _trade_groups_locked(user: dict, whale_id: str):
    """The shared Pro/Max check for the trade-group routes, or None when allowed.

    Per-whale, not per-tier alone: the designated free whale is open on Free, so its trade
    cards in the Recent Trades feed tap through instead of dead-ending.
    """
    tier = user.get("tier")
    if whale_detail_allowed(tier, whale_id, get_supabase()):
        return None
    return make_error_response(
        ErrorCode.WHALE_FOLLOW_LOCKED,
        message="Whale trade detail requires a paid plan",
        details={"tier_required": required_tier_for_whales(tier) or ""},
    )


@router.get(
    "/{whale_id}/trade-groups", response_model=List[WhaleTradeGroupResponse]
)
async def get_trade_groups(
    whale_id: str,
    user: dict = Depends(get_watchlist_identity),
):
    """Get all trade groups for a whale. Paid — same data the profile withholds."""
    if (locked := _trade_groups_locked(user, whale_id)) is not None:
        return locked
    service = WhaleService()
    return await service.get_trade_groups(whale_id=whale_id)


@router.get(
    "/{whale_id}/trade-groups/{group_id}",
    response_model=WhaleTradeGroupResponse,
)
async def get_trade_group_detail(
    whale_id: str,
    group_id: str,
    user: dict = Depends(get_watchlist_identity),
):
    """Get a single trade group with all individual trades. Paid."""
    if (locked := _trade_groups_locked(user, whale_id)) is not None:
        return locked
    service = WhaleService()
    group = await service.get_trade_group_detail(
        whale_id=whale_id, group_id=group_id
    )
    if not group:
        raise HTTPException(status_code=404, detail="Trade group not found")
    return group


# ── Follow / Unfollow ────────────────────────────────────────────────


@router.post("/{whale_id}/follow", response_model=FollowResponse)
async def follow_whale(
    whale_id: str,
    user: dict = Depends(get_current_user),
):
    """Follow a whale. Refused with WHALE_FOLLOW_LOCKED when the plan doesn't allow it."""
    service = WhaleService()
    try:
        return await service.toggle_follow(
            user["id"], whale_id, follow=True, tier=user.get("tier")
        )
    except WhaleFollowLockedException as e:
        logger.info(
            "Follow refused for user=%s whale=%s: %s", user["id"], whale_id, e.reason
        )
        # Flat scalars only — iOS `AnyCodable` decodes String/Int/Double/Bool and silently
        # yields "" for anything else, so a nested dict here would arrive as garbage.
        return make_error_response(
            ErrorCode.WHALE_FOLLOW_LOCKED,
            message=e.reason,
            details={
                "tier_required": e.tier_required,
                "limit": e.limit if e.limit is not None else -1,
            },
        )


@router.delete("/{whale_id}/follow", response_model=FollowResponse)
async def unfollow_whale(
    whale_id: str,
    user: dict = Depends(get_current_user),
):
    """Unfollow a whale. NEVER gated — see toggle_follow."""
    service = WhaleService()
    return await service.toggle_follow(user["id"], whale_id, follow=False)
