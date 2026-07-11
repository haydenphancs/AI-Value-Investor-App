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


class JourneyContentService:
    # In-memory cache (content is near-static; refresh hourly).
    _cache: Optional[tuple[float, JourneyResponse]] = None
    _TTL_SECONDS = 3600
    _inflight: Optional[asyncio.Future] = None

    async def get_journey(self) -> JourneyResponse:
        cached = JourneyContentService._cache
        if cached and time.time() - cached[0] < self._TTL_SECONDS:
            return cached[1]

        # Dedup concurrent refreshes (thundering-herd guard).
        if JourneyContentService._inflight is not None:
            return await JourneyContentService._inflight

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        JourneyContentService._inflight = future
        try:
            response = await self._load()
            JourneyContentService._cache = (time.time(), response)
            future.set_result(response)
            return response
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, never 500 the screen
            logger.exception("Failed to load journey content: %s", exc)
            # Serve stale cache if we have one; otherwise an empty journey.
            fallback = cached[1] if cached else JourneyResponse(lessons=[])
            future.set_result(fallback)
            return fallback
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
