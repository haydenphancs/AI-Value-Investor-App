"""
Investor Journey content service.

Reads authored lessons from the `lessons` table (skeleton + story_content JSONB)
and returns them ordered by level then sort_order. Lesson content changes rarely,
so a single in-memory tier with a long TTL is enough — the table itself is the
durable store, so there is no separate *_cache table.
"""

import asyncio
import logging
import time
from typing import List, Optional

from app.database import get_supabase
from app.schemas.journey import JourneyLessonResponse, JourneyResponse

logger = logging.getLogger(__name__)

# Stable display order for the four levels.
_LEVEL_ORDER = {"foundation": 0, "analysis": 1, "strategies": 2, "mastery": 3}


def _has_waiters(fut: asyncio.Future) -> bool:
    """Whether anything is awaiting `fut`.

    Mirrors `news_cache_service._has_waiters`. ``asyncio.Future._callbacks`` is private but stable
    across CPython 3.8-3.13 and is the only way to ask this. Guarded so a future CPython change
    degrades to "assume waiters" rather than raising.
    """
    try:
        return bool(getattr(fut, "_callbacks", None))
    except Exception:
        return True


class JourneyContentService:
    # In-memory cache (content is near-static; refresh hourly).
    _cache: Optional[tuple[float, JourneyResponse]] = None
    _TTL_SECONDS = 3600
    _inflight: Optional[asyncio.Future] = None

    async def get_journey(self) -> JourneyResponse:
        cached = JourneyContentService._cache
        if cached and time.time() - cached[0] < self._TTL_SECONDS:
            return cached[1]

        # Dedup concurrent refreshes (thundering-herd guard). SHIELDED: awaiting the shared future
        # directly propagates a WAITER's cancellation into it, so one caller giving up — chat
        # context resolution wraps this in asyncio.wait_for(timeout=4.0) — would cancel the future
        # the leader is about to resolve. The leader's set_result then raises InvalidStateError and
        # 500s a public content request whose data actually loaded fine.
        inflight = JourneyContentService._inflight
        if inflight is not None:
            return await asyncio.shield(inflight)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        JourneyContentService._inflight = future
        try:
            response = await self._load()
            JourneyContentService._cache = (time.time(), response)
            if not future.done():
                future.set_result(response)
            return response
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, never 500 the screen
            logger.exception(
                "journey: failed to load content: %s: %s", type(exc).__name__, exc
            )
            # Serve stale cache if we have one; otherwise an empty journey.
            fallback = cached[1] if cached else JourneyResponse(lessons=[])
            if not future.done():
                future.set_result(fallback)
            return fallback
        except BaseException as exc:
            # CancelledError is a BaseException, so `except Exception` misses it — and then the
            # `finally` below would clear _inflight while the future stays pending FOREVER, hanging
            # every joined waiter (nothing in this stack has a timeout). Resolve it before leaving.
            logger.warning(
                "journey: content load aborted (%s) — releasing %d joined waiter(s)",
                type(exc).__name__, 1 if _has_waiters(future) else 0,
            )
            if not future.done():
                # Only hand the exception over when someone is actually waiting: an unretrieved
                # future exception logs a spurious traceback on GC for every cancellation.
                if _has_waiters(future):
                    future.set_exception(exc)
                else:
                    future.cancel()
            raise
        finally:
            JourneyContentService._inflight = None

    async def _load(self) -> JourneyResponse:
        rows = await asyncio.to_thread(self._fetch_rows)
        lessons: List[JourneyLessonResponse] = []
        for row in rows:
            # Per-row resilience: one malformed/out-of-band lesson row must NOT collapse the
            # entire journey. A bare list comprehension building JourneyLessonResponse would let
            # the FIRST bad row (missing NOT-NULL title/level, or a story_content stored as a JSON
            # array/string rather than an object) raise a ValidationError, propagate out of _load,
            # and on a cold cache empty ALL 27 lessons. Skip+log the bad row instead. Mirrors the
            # hardening already in money_moves_content_service._load.
            try:
                # NOTE (identity): unlike money_moves — where the article's `slug` lives INSIDE the
                # content blob and has to be overlaid from the row column — a Journey lesson's
                # identity is the `title` COLUMN, which is already what we serve below. So there is
                # nothing to overlay. The equivalent silent-drop hazard is a BLANK title: iOS keys
                # its remote lessons by title, so "" matches no lesson and the row just never
                # appears, with no client-side clue. Say so here.
                if not str(row.get("title") or "").strip():
                    logger.error(
                        "journey: lesson row has a blank title — iOS keys lessons by title, so this "
                        "row will silently never appear (id=%s level=%s)",
                        row.get("id"), row.get("level"),
                    )

                # Guard the SHAPE of story_content, not just presence: iOS treats a lesson with no
                # cards as "fall back to bundled content", so degrading a malformed blob to None
                # keeps the lesson tile (title/level/duration) visible instead of dropping the row.
                story = row.get("story_content")
                if story is not None and not isinstance(story, dict):
                    logger.warning(
                        "journey: coercing non-dict story_content to None (id=%s title=%r type=%s)",
                        row.get("id"), row.get("title"), type(story).__name__,
                    )
                    story = None
                lessons.append(
                    JourneyLessonResponse(
                        id=str(row["id"]),
                        title=row["title"],
                        description=row.get("description"),
                        level=row["level"],
                        duration_minutes=row.get("duration_minutes"),
                        category=row.get("category") or "standard",
                        sort_order=row.get("sort_order") or 0,
                        story_content=story,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — one bad row must not collapse the whole journey
                logger.warning(
                    "journey: skipping malformed lesson row (id=%s title=%r): %s: %s",
                    row.get("id"), row.get("title"), type(exc).__name__, exc,
                )
                continue
        lessons.sort(key=lambda l: (_LEVEL_ORDER.get(l.level, 99), l.sort_order))
        return JourneyResponse(lessons=lessons)

    def _fetch_rows(self) -> List[dict]:
        sb = get_supabase()
        result = (
            sb.table("lessons")
            .select(
                "id, title, description, level, duration_minutes, category, sort_order, story_content"
            )
            .execute()
        )
        return result.data or []


_service: Optional[JourneyContentService] = None


def get_journey_content_service() -> JourneyContentService:
    global _service
    if _service is None:
        _service = JourneyContentService()
    return _service
