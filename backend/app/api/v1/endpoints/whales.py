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

from app.dependencies import get_current_user, get_optional_user_id
from app.schemas.whale import (
    TrendingWhaleResponse,
    WhaleProfileResponse,
    WhaleTradeGroupResponse,
    WhaleTradeGroupActivityResponse,
    FollowResponse,
)
from app.services.whale_service import WhaleService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Whale Listing ────────────────────────────────────────────────────


@router.get("", response_model=List[TrendingWhaleResponse])
async def list_whales(
    category: Optional[str] = Query(
        None,
        description="Filter by category: investors, institutions, politicians, crypto",
    ),
    user_id: Optional[str] = Depends(get_optional_user_id),
):
    """List all whales, optionally filtered by category."""
    service = WhaleService()
    return await service.get_whale_list(category=category, user_id=user_id)


# ── Activity Feed ────────────────────────────────────────────────────


@router.get("/activity", response_model=List[WhaleTradeGroupActivityResponse])
async def get_whale_activity(
    user: dict = Depends(get_current_user),
):
    """Get recent trade activity from user's followed whales."""
    service = WhaleService()
    return await service.get_whale_activity_feed(user["id"])


# ── Whale Profile ────────────────────────────────────────────────────


@router.get("/{whale_id}/profile", response_model=WhaleProfileResponse)
async def get_whale_profile(
    whale_id: str,
    user_id: Optional[str] = Depends(get_optional_user_id),
):
    """Get full whale profile with holdings, trades, and summaries."""
    service = WhaleService()
    profile = await service.get_whale_profile(
        whale_id=whale_id, user_id=user_id
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Whale not found")
    return profile


# ── Trade Groups ─────────────────────────────────────────────────────


@router.get(
    "/{whale_id}/trade-groups", response_model=List[WhaleTradeGroupResponse]
)
async def get_trade_groups(whale_id: str):
    """Get all trade groups for a whale."""
    service = WhaleService()
    return await service.get_trade_groups(whale_id=whale_id)


@router.get(
    "/{whale_id}/trade-groups/{group_id}",
    response_model=WhaleTradeGroupResponse,
)
async def get_trade_group_detail(whale_id: str, group_id: str):
    """Get a single trade group with all individual trades."""
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
    """Follow a whale."""
    service = WhaleService()
    return await service.toggle_follow(user["id"], whale_id, follow=True)


@router.delete("/{whale_id}/follow", response_model=FollowResponse)
async def unfollow_whale(
    whale_id: str,
    user: dict = Depends(get_current_user),
):
    """Unfollow a whale."""
    service = WhaleService()
    return await service.toggle_follow(user["id"], whale_id, follow=False)
