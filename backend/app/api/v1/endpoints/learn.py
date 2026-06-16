"""
Learn Endpoints
Frontend:
  - GET  /api/v1/learn/journey                       (public content)
  - GET  /api/v1/learn/money-moves                   (public content)
  - GET  /api/v1/learn/progress/{content_type}       (user completion log)
  - POST /api/v1/learn/progress/{content_type}       (mark an item completed)

Serves authored learning content from Supabase:
  - Investor Journey lessons (skeleton + story content with media URLs) from `lessons`.
  - Money Moves case-study articles (full article + narration URL) from
    `money_move_articles`.
Content endpoints are public. Progress is one unified completion log (user_learn_progress):
content_type ∈ {book_core, journey_lesson, money_move}, item_key is that feature's stable key
(book "<order>-<core>", journey lesson title, money-move slug). User-scoped, optional auth
(a guest still works, backed by the shared guest user id).
"""

import logging

from fastapi import APIRouter, Depends
from supabase import Client

from app.database import get_supabase
from app.dependencies import get_current_user_or_guest
from app.schemas.journey import JourneyResponse
from app.schemas.learn_progress import CompleteLearnItemRequest, LearnProgressResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services.journey_content_service import get_journey_content_service
from app.services.money_moves_content_service import get_money_moves_content_service

# Stable discriminators for the unified completion log.
LEARN_CONTENT_TYPES = {"book_core", "journey_lesson", "money_move"}

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


@router.get("/progress/{content_type}", response_model=LearnProgressResponse)
async def get_learn_progress(
    content_type: str,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """
    Keys of every item the current user has completed for one Learn feature.

    content_type in {book_core, journey_lesson, money_move}; the returned `keys` are that
    feature's item_keys (book "<order>-<core>", journey lesson title, money-move slug).
    Degrades to an empty list on a backend hiccup — the iOS local cache is the source of truth.
    """
    if content_type not in LEARN_CONTENT_TYPES:
        return LearnProgressResponse(keys=[])
    user_id = user["id"]
    try:
        result = (
            supabase.table("user_learn_progress")
            .select("item_key")
            .eq("user_id", user_id)
            .eq("content_type", content_type)
            .execute()
        )
        return LearnProgressResponse(keys=[row["item_key"] for row in (result.data or [])])
    except Exception as exc:
        logger.error(
            "[Learn] progress fetch failed (user=%s type=%s): %s", user_id, content_type, exc
        )
        return LearnProgressResponse(keys=[])


@router.post("/progress/{content_type}", response_model=LearnProgressResponse)
async def complete_learn_item(
    content_type: str,
    request: CompleteLearnItemRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """
    Mark one Learn item completed (idempotent). Returns the full key set for that content_type.
    """
    user_id = user["id"]
    key = (request.key or "").strip()
    if content_type in LEARN_CONTENT_TYPES and key:
        try:
            supabase.table("user_learn_progress").upsert(
                {"user_id": user_id, "content_type": content_type, "item_key": key},
                on_conflict="user_id,content_type,item_key",
                ignore_duplicates=True,
            ).execute()
        except Exception as exc:
            logger.error(
                "[Learn] mark complete failed (user=%s type=%s key=%r): %s",
                user_id,
                content_type,
                key,
                exc,
            )
    return await get_learn_progress(content_type=content_type, user=user, supabase=supabase)
