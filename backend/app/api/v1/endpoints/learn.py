"""
Learn Endpoints
Frontend:
  - GET  /api/v1/learn/journey                       (public content)
  - GET  /api/v1/learn/money-moves                   (public content)
  - GET  /api/v1/learn/progress/{content_type}       (user completion log)
  - POST /api/v1/learn/progress/{content_type}       (mark an item completed)
  - GET/POST/DELETE /api/v1/learn/bookmarks          (toggleable book bookmarks)
  - GET/PUT/DELETE  /api/v1/learn/money-move-bookmark (the ONE saved Money Move topic)

Serves authored learning content from Supabase:
  - Investor Journey lessons (skeleton + story content with media URLs) from `lessons`.
  - Money Moves case-study articles (full article + narration URL) from
    `money_move_articles`.
Content endpoints are public. Progress is one unified completion log (user_learn_progress):
content_type ∈ {book_core, journey_lesson, money_move}, item_key is that feature's stable key
(book "<order>-<core>", journey lesson title, money-move slug). User-scoped, optional auth
(a guest still works, backed by the shared guest user id). Book bookmarks share that same
unified table under content_type 'book_bookmark' (item_key = book title) but are toggleable, so
they get their own GET/POST/DELETE /bookmarks routes (the /progress endpoint never deletes).
The Money Moves bookmark is the same idea one more time (content_type
'money_move_bookmark', item_key = article slug) but SINGLE-VALUED: at most one topic is
saved, so PUT replaces instead of appending and the response carries one nullable slug.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends
from supabase import Client

from app.api.error_response import error_response_from_exception
from app.database import get_supabase
from app.dependencies import get_learn_identity
from app.schemas.bookmarks import (
    BookmarkListResponse,
    BookmarkRequest,
    MoneyMoveBookmarkRequest,
    MoneyMoveBookmarkResponse,
)
from app.schemas.journey import JourneyResponse
from app.schemas.learn_progress import CompleteLearnItemRequest, LearnProgressResponse
from app.schemas.learn_books_audio import BooksAudioResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services.book_audio_service import get_books_audio
from app.services.journey_content_service import get_journey_content_service
from app.services.money_moves_content_service import get_money_moves_content_service
from app.services.entitlements import (
    TIER_PRO,
    learn_audio_unlocked,
    required_tier_for_learn_audio,
)
from app.services.learn_audio_gate import (
    redact_money_moves,
    sign_journey,
    sign_money_moves,
)

# Stable discriminators for the unified completion log.
LEARN_CONTENT_TYPES = {"book_core", "journey_lesson", "money_move"}

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/journey", response_model=JourneyResponse)
async def get_journey(user: dict = Depends(get_learn_identity)):
    """
    All Investor Journey lessons, ordered by level then sort_order.

    Each lesson includes its `story_content` (cards with audio/image/video URLs).
    Degrades gracefully to stale cache or an empty list on a backend hiccup.

    Every word AND the narration are free on every tier, including signed-out guests —
    the Journey is the deliberate exception to the Learn audio gate
    (entitlements.JOURNEY_AUDIO_UNLOCKED_TIERS). Money Moves and Books are still Pro/Max.

    There is no locked branch: `audio_locked` and `tier_required` on the envelope are
    therefore permanently False/None. They are retained because already-shipped builds
    decode this response and because MoneyMovesResponse still uses the identical pair.

    `journey-media` is a PRIVATE bucket (migration 128), so the stored `audioUrl` is a
    public-form URL that no longer resolves. Signing is not an optimisation here — it is
    the only thing that makes narration play, and it now runs for 100% of callers.

    The identity dependency no longer reads `tier`; it stays because a signed-out caller
    must still resolve to a per-install guest for the rest of the Learn surface, and
    because two source-scan tests pin this route as identity-bearing.
    """
    service = get_journey_content_service()
    response = await service.get_journey()
    # Signed HERE, per request, never inside the service: `get_journey()` returns the object
    # held in a process-wide 1-hour cache, shared by every caller. `sign_journey` must not
    # mutate it — rewriting URLs in place would pin one caller's short-lived signed URLs
    # into the shared payload for an hour and hand them to everyone. That invariant now
    # carries every Journey request rather than the Pro slice, so it is MORE load-bearing,
    # not less; it is pinned by test_sign_journey_does_not_mutate_the_shared_cache_entry.
    return await sign_journey(response)


@router.get("/money-moves", response_model=MoneyMovesResponse)
async def get_money_moves(user: dict = Depends(get_learn_identity)):
    """
    All Money Moves articles, ordered by sort_order.

    Each item is the full iOS-shaped article `content` (with the narration audioUrl
    overlaid when the voice exists). Degrades gracefully to stale cache or an empty
    list on a backend hiccup.

    Every article is fully readable on every tier. The NARRATION is Pro/Max, so a locked
    caller gets the same articles with `audioUrl`, `audioDurationSeconds` and the
    read-along spans stripped. `hasAudioVersion` is deliberately left alone — see
    MoneyMovesResponse.
    """
    service = get_money_moves_content_service()
    response = await service.get_money_moves()
    # Same copy-on-read rule as /journey above, on BOTH branches: the service's return value
    # is the shared cache entry.
    tier = user.get("tier")
    if not learn_audio_unlocked(tier):
        return redact_money_moves(response, required_tier_for_learn_audio(tier) or TIER_PRO)
    return await sign_money_moves(response)


@router.get("/books/audio", response_model=BooksAudioResponse)
async def get_books_audio_urls(user: dict = Depends(get_learn_identity)):
    """
    Signed narration URLs for the book library, one per book.

    Every book's TEXT ships in the app and stays free; this route serves only the produced
    narration, which is Pro/Max. It exists because book audio used to be ten PUBLIC Storage
    URLs compiled into the binary — readable by anyone with the app, on any plan.

    A locked caller gets 200 with an empty list and `audio_locked: true`, matching
    /journey and /money-moves rather than raising a 403 on a screen whose job is to show
    the upgrade offer.
    """
    tier = user.get("tier")
    unlocked = learn_audio_unlocked(tier)
    return await get_books_audio(
        unlocked=unlocked,
        tier_required=None if unlocked else (required_tier_for_learn_audio(tier) or TIER_PRO),
    )


@router.get("/progress/{content_type}", response_model=LearnProgressResponse)
async def get_learn_progress(
    content_type: str,
    user: dict = Depends(get_learn_identity),
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

    def _query():
        return (
            supabase.table("user_learn_progress")
            .select("item_key")
            .eq("user_id", user_id)
            .eq("content_type", content_type)
            .execute()
        )

    try:
        # The Supabase SDK is SYNCHRONOUS; called directly from an `async def` it blocks the
        # event loop for the whole round-trip, stalling every other in-flight request on this
        # instance. Every Learn screen open hits this route.
        result = await asyncio.to_thread(_query)
        return LearnProgressResponse(keys=[row["item_key"] for row in (result.data or [])])
    except Exception as exc:
        logger.error(
            "[Learn] progress fetch failed (user=%s type=%s): %s", user_id, content_type, exc
        )
        # NOT 200-with-empty. An empty list is indistinguishable from "this user
        # has completed nothing", so iOS `hydrate()` would not throw — and the
        # reconcile pass would then re-POST every locally-known key (up to 25 per
        # store, 3 stores per Learn open) against a backend that is already
        # failing. A typed error lets the client keep its local cache and back off.
        return error_response_from_exception(
            exc, step="learn_progress_fetch", extra_details={"content_type": content_type}
        )


@router.post("/progress/{content_type}", response_model=LearnProgressResponse)
async def complete_learn_item(
    content_type: str,
    request: CompleteLearnItemRequest,
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """
    Mark one Learn item completed (idempotent). Returns the full key set for that content_type.
    """
    user_id = user["id"]
    key = (request.key or "").strip()
    if content_type in LEARN_CONTENT_TYPES and key:
        def _upsert():
            return supabase.table("user_learn_progress").upsert(
                {"user_id": user_id, "content_type": content_type, "item_key": key},
                on_conflict="user_id,content_type,item_key",
                ignore_duplicates=True,
            ).execute()

        try:
            await asyncio.to_thread(_upsert)
        except Exception as exc:
            logger.error(
                "[Learn] mark complete failed (user=%s type=%s key=%r): %s",
                user_id,
                content_type,
                key,
                exc,
            )
    return await get_learn_progress(content_type=content_type, user=user, supabase=supabase)


@router.delete("/progress/{content_type}", response_model=LearnProgressResponse)
async def uncomplete_learn_item(
    content_type: str,
    request: CompleteLearnItemRequest,
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """
    Un-mark one Learn item (idempotent). Returns the remaining key set for that content_type.

    Lets a learner toggle completion back off — e.g. the Money Moves article-end Complete button.
    """
    user_id = user["id"]
    key = (request.key or "").strip()
    if content_type in LEARN_CONTENT_TYPES and key:
        def _delete():
            return (
                supabase.table("user_learn_progress")
                .delete()
                .eq("user_id", user_id)
                .eq("content_type", content_type)
                .eq("item_key", key)
                .execute()
            )

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:
            logger.error(
                "[Learn] uncomplete failed (user=%s type=%s key=%r): %s",
                user_id,
                content_type,
                key,
                exc,
            )
            # Do NOT fall through to a 200. The follow-up select would return the
            # key STILL PRESENT, which iOS reads as "the delete was confirmed" —
            # it drops its tombstone and the next union-merge resurrects the item,
            # flipping it back to Completed against the user's explicit tap.
            return error_response_from_exception(
                exc, step="learn_uncomplete", extra_details={"content_type": content_type}
            )
    return await get_learn_progress(content_type=content_type, user=user, supabase=supabase)


# --- Book bookmarks ---------------------------------------------------------------------------
# Toggleable per-user book bookmarks live in the SAME unified table as Learn completion progress
# (user_learn_progress, migration 067), under content_type 'book_bookmark'. item_key is the book
# TITLE — the stable id shared across LibraryBook / EducationBook / SearchBookItem on iOS.
#
# Unlike the completion content_types (book_core/journey_lesson/money_move, which are append-only
# and live behind /progress), bookmarks can be removed — so they get their own GET/POST/DELETE
# routes here rather than going through the generic /progress endpoint (which has no removal and
# would let a bookmark be "marked complete"). `completed_at` doubles as "saved_at" — it orders
# the list most-recent-first. Optional auth (guests work too).

BOOKMARK_CONTENT_TYPE = "book_bookmark"


@router.get("/bookmarks", response_model=BookmarkListResponse)
async def get_book_bookmarks(
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """
    The current user's bookmarked book titles, most-recent-first.

    Degrades to an empty list on a backend hiccup — the iOS local cache is the source of truth.
    """
    user_id = user["id"]

    def _query():
        return (
            supabase.table("user_learn_progress")
            .select("item_key")
            .eq("user_id", user_id)
            .eq("content_type", BOOKMARK_CONTENT_TYPE)
            # `item_key` breaks ties DETERMINISTICALLY. `completed_at` defaults to now() and two
            # bookmarks saved in quick succession can share a timestamp, leaving their relative
            # order up to Postgres — so the list (and with it the Book Library hero shortcut, which
            # opens `bookmarkedTitles.first`) could point at a different book on each request.
            .order("completed_at", desc=True)
            .order("item_key", desc=False)
            .execute()
        )

    try:
        result = await asyncio.to_thread(_query)   # sync SDK — keep it off the event loop
        return BookmarkListResponse(bookmarks=[row["item_key"] for row in (result.data or [])])
    except Exception as exc:
        logger.error("[Learn] bookmarks fetch failed (user=%s): %s", user_id, exc)
        # See get_learn_progress — a 200-with-empty triggers a pointless
        # re-push storm against an already-failing backend.
        return error_response_from_exception(exc, step="learn_bookmarks_fetch")


@router.post("/bookmarks", response_model=BookmarkListResponse)
async def add_book_bookmark(
    request: BookmarkRequest,
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """Bookmark a book (idempotent). Returns the user's full bookmark list, most-recent-first."""
    user_id = user["id"]
    key = (request.book_key or "").strip()
    if key:
        def _upsert():
            return supabase.table("user_learn_progress").upsert(
                {"user_id": user_id, "content_type": BOOKMARK_CONTENT_TYPE, "item_key": key},
                on_conflict="user_id,content_type,item_key",
                ignore_duplicates=True,
            ).execute()

        try:
            await asyncio.to_thread(_upsert)
        except Exception as exc:
            logger.error("[Learn] add bookmark failed (user=%s key=%r): %s", user_id, key, exc)
    return await get_book_bookmarks(user=user, supabase=supabase)


@router.delete("/bookmarks", response_model=BookmarkListResponse)
async def remove_book_bookmark(
    request: BookmarkRequest,
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """Remove a book bookmark (idempotent). Returns the user's full bookmark list."""
    user_id = user["id"]
    key = (request.book_key or "").strip()
    if key:
        def _delete():
            return (
                supabase.table("user_learn_progress")
                .delete()
                .eq("user_id", user_id)
                .eq("content_type", BOOKMARK_CONTENT_TYPE)
                .eq("item_key", key)
                .execute()
            )

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:
            logger.error("[Learn] remove bookmark failed (user=%s key=%r): %s", user_id, key, exc)
            # See uncomplete_learn_item — a 200 here makes iOS drop its tombstone
            # and the bookmark reappears.
            return error_response_from_exception(exc, step="learn_remove_bookmark")
    return await get_book_bookmarks(user=user, supabase=supabase)


# --- Money Moves bookmark ----------------------------------------------------------------------
# The ONE saved Money Move topic, in the same unified table (user_learn_progress, migration 067)
# under content_type 'money_move_bookmark'. item_key is the article SLUG — the canonical id, the
# same key the money_move completion log uses, so a bookmark and a completion always agree about
# which article they mean.
#
# SINGLE-VALUED, and that is the whole design. The Money Moves screen shows one saved topic below
# its hero, so PUT REPLACES rather than appends: the client never has to reconcile an ordered list,
# never needs tombstones for a displaced entry, and cannot end up rendering two saved rows. The
# response carries one nullable slug instead of a list for the same reason.
#
# `completed_at` doubles as "saved_at". A row is only ever displaced or deleted, never updated.
# Optional auth — a guest saves against their own per-install identity (auth.md §1a).

MONEY_MOVE_BOOKMARK_CONTENT_TYPE = "money_move_bookmark"


def _money_move_bookmark_rows(supabase: Client, user_id: str):
    """The user's saved-topic rows, most-recent-first. Sync — call via asyncio.to_thread."""
    return (
        supabase.table("user_learn_progress")
        .select("item_key")
        .eq("user_id", user_id)
        .eq("content_type", MONEY_MOVE_BOOKMARK_CONTENT_TYPE)
        # `item_key` breaks ties DETERMINISTICALLY, exactly as get_book_bookmarks does.
        # `completed_at` defaults to now(), so a PUT racing another device's PUT can leave two
        # rows sharing a timestamp for the moment before the delete lands — and then which one
        # this endpoint returns would be up to Postgres, i.e. the saved row could flip between
        # two requests. Ordering by item_key second makes the answer stable either way.
        .order("completed_at", desc=True)
        .order("item_key", desc=False)
        .limit(1)
        .execute()
    )


@router.get("/money-move-bookmark", response_model=MoneyMoveBookmarkResponse)
async def get_money_move_bookmark(
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """The slug of the user's saved Money Move topic, or null when nothing is saved."""
    user_id = user["id"]
    try:
        result = await asyncio.to_thread(_money_move_bookmark_rows, supabase, user_id)
        rows = result.data or []
        return MoneyMoveBookmarkResponse(bookmark=rows[0]["item_key"] if rows else None)
    except Exception as exc:
        logger.error(
            "[Learn] money-move bookmark fetch failed (user=%s): %s",
            user_id,
            f"{type(exc).__name__}: {exc}",
        )
        # NOT a 200-with-null. The iOS store treats a successful GET as authoritative and clears
        # its unsynced flag, so answering null on a backend hiccup would look like "nothing is
        # saved" and silently drop the user's bookmark. Same reasoning as get_book_bookmarks.
        return error_response_from_exception(exc, step="learn_money_move_bookmark_fetch")


@router.put("/money-move-bookmark", response_model=MoneyMoveBookmarkResponse)
async def set_money_move_bookmark(
    request: MoneyMoveBookmarkRequest,
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """
    Save a topic, replacing whatever was saved before. Idempotent.

    Returns the resulting bookmark so the client can adopt the server's view in one round trip.
    """
    user_id = user["id"]
    slug = (request.slug or "").strip()
    if not slug:
        # A blank slug is not "clear the bookmark" — DELETE is. Writing it would create a row
        # with an empty item_key that no article can ever resolve, i.e. a permanently dangling
        # saved row. Answer with the current state instead.
        return await get_money_move_bookmark(user=user, supabase=supabase)

    def _replace():
        # Insert BEFORE deleting. If the process dies between the two statements the user keeps a
        # bookmark (possibly two for a moment, which the ordered GET resolves deterministically);
        # deleting first would leave them with none.
        supabase.table("user_learn_progress").upsert(
            {
                "user_id": user_id,
                "content_type": MONEY_MOVE_BOOKMARK_CONTENT_TYPE,
                "item_key": slug,
            },
            on_conflict="user_id,content_type,item_key",
            ignore_duplicates=True,
        ).execute()
        return (
            supabase.table("user_learn_progress")
            .delete()
            .eq("user_id", user_id)
            .eq("content_type", MONEY_MOVE_BOOKMARK_CONTENT_TYPE)
            .neq("item_key", slug)
            .execute()
        )

    try:
        await asyncio.to_thread(_replace)
    except Exception as exc:
        logger.error(
            "[Learn] money-move bookmark set failed (user=%s slug=%r): %s",
            user_id,
            slug,
            f"{type(exc).__name__}: {exc}",
        )
        # Surfaced, not swallowed: the client clears its "needs push" flag on a 2xx, so a silent
        # failure here loses the save with no retry and no trace.
        return error_response_from_exception(exc, step="learn_money_move_bookmark_set")
    return await get_money_move_bookmark(user=user, supabase=supabase)


@router.delete("/money-move-bookmark", response_model=MoneyMoveBookmarkResponse)
async def remove_money_move_bookmark(
    request: MoneyMoveBookmarkRequest,
    user: dict = Depends(get_learn_identity),
    supabase: Client = Depends(get_supabase),
):
    """Remove the saved topic, by slug. Idempotent."""
    user_id = user["id"]
    slug = (request.slug or "").strip()
    if not slug:
        return await get_money_move_bookmark(user=user, supabase=supabase)

    def _delete():
        # Scoped to the NAMED slug, never "delete every row of this type". An un-bookmark queued
        # offline on one device can land long after the user saved a different topic on another;
        # an unscoped delete would then wipe a bookmark the user never touched.
        return (
            supabase.table("user_learn_progress")
            .delete()
            .eq("user_id", user_id)
            .eq("content_type", MONEY_MOVE_BOOKMARK_CONTENT_TYPE)
            .eq("item_key", slug)
            .execute()
        )

    try:
        await asyncio.to_thread(_delete)
    except Exception as exc:
        logger.error(
            "[Learn] money-move bookmark remove failed (user=%s slug=%r): %s",
            user_id,
            slug,
            f"{type(exc).__name__}: {exc}",
        )
        # See remove_book_bookmark — a 200 here makes the client retire its pending removal and
        # the next hydrate resurrects the bookmark the user just cleared.
        return error_response_from_exception(exc, step="learn_money_move_bookmark_remove")
    return await get_money_move_bookmark(user=user, supabase=supabase)
