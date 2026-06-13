"""
Learn / Investor Journey Endpoints
Frontend: GET /api/v1/learn/journey

Serves the authored Investor Journey lessons (skeleton + story content with media
URLs) from the `lessons` table. Public — no auth required to read lesson content.
"""

import logging

from fastapi import APIRouter

from app.schemas.journey import JourneyResponse
from app.services.journey_content_service import get_journey_content_service

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
