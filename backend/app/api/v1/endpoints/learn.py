"""
Learn Endpoints
Frontend: GET /api/v1/learn/journey, GET /api/v1/learn/money-moves

Serves authored learning content from Supabase:
  - Investor Journey lessons (skeleton + story content with media URLs) from `lessons`.
  - Money Moves case-study articles (full article + narration URL) from
    `money_move_articles`.
Public — no auth required to read content.
"""

import logging

from fastapi import APIRouter

from app.schemas.journey import JourneyResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services.journey_content_service import get_journey_content_service
from app.services.money_moves_content_service import get_money_moves_content_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/journey", response_model=JourneyResponse)
async def get_journey():
    """
    All Investor Journey lessons, ordered by level then sort_order.

    Each lesson includes its `story_content` (cards with audio/image/video URLs).
    Degrades gracefully to stale cache or an empty list on a backend hiccup.
    """
    service = get_journey_content_service()
    return await service.get_journey()


@router.get("/money-moves", response_model=MoneyMovesResponse)
async def get_money_moves():
    """
    All Money Moves articles, ordered by sort_order.

    Each item is the full iOS-shaped article `content` (with the narration audioUrl
    overlaid when the voice exists). Degrades gracefully to stale cache or an empty
    list on a backend hiccup.
    """
    service = get_money_moves_content_service()
    return await service.get_money_moves()
