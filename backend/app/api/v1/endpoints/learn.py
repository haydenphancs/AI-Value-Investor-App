"""
Learn Endpoints
Frontend:
  - GET  /api/v1/learn/journey                                  (public content)
  - GET  /api/v1/learn/money-moves                              (public content)
  - GET  /api/v1/learn/books/progress                           (user book progress)
  - POST /api/v1/learn/books/{curriculum_order}/cores/{n}/complete

Serves authored learning content from Supabase:
  - Investor Journey lessons (skeleton + story content with media URLs) from `lessons`.
  - Money Moves case-study articles (full article + narration URL) from
    `money_move_articles`.
Content endpoints are public. The Book Library progress endpoints are user-scoped
(optional auth: a guest still works, backed by the shared guest user id).
"""

import logging

from fastapi import APIRouter, Depends
from supabase import Client

from app.database import get_supabase
from app.dependencies import get_current_user_or_guest
from app.schemas.book_progress import BookCoreProgressItem, BookProgressResponse
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


@router.get("/books/progress", response_model=BookProgressResponse)
async def get_book_progress(
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """
    Every (book, core) the current user has completed.

    Drives the Book Library's "Continue Core N", per-book mastery, and the library %.
    Degrades to an empty list on a backend hiccup — the iOS local cache is the source of
    truth, so a failed read here just means "no server augmentation this time".
    """
    user_id = user["id"]
    try:
        result = (
            supabase.table("user_book_progress")
            .select("curriculum_order, core_number, completed_at")
            .eq("user_id", user_id)
            .execute()
        )
        items = [BookCoreProgressItem(**row) for row in (result.data or [])]
        return BookProgressResponse(items=items)
    except Exception as exc:
        logger.error("[Learn] book progress fetch failed for user=%s: %s", user_id, exc)
        return BookProgressResponse(items=[])


@router.post(
    "/books/{curriculum_order}/cores/{core_number}/complete",
    response_model=BookProgressResponse,
)
async def complete_book_core(
    curriculum_order: int,
    core_number: int,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """
    Mark one core of one book as completed for the current user (idempotent insert).

    Returns the user's full, current progress so the client can self-heal / sync.
    """
    user_id = user["id"]
    try:
        supabase.table("user_book_progress").upsert(
            {
                "user_id": user_id,
                "curriculum_order": curriculum_order,
                "core_number": core_number,
            },
            on_conflict="user_id,curriculum_order,core_number",
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        logger.error(
            "[Learn] mark core complete failed (user=%s order=%s core=%s): %s",
            user_id,
            curriculum_order,
            core_number,
            exc,
        )
    return await get_book_progress(user=user, supabase=supabase)
