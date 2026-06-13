"""
Money Moves content service.

Reads authored case-study articles from the `money_move_articles` table and returns
them ordered by sort_order. Each row stores the full iOS-shaped article in its
`content` JSONB column; we return that blob as-is, overlaying the row's `audio_url`
column onto content["audioUrl"] when a narration voice exists. Article content changes
rarely, so a single in-memory tier with a long TTL is enough — the table itself is the
durable store, so there is no separate *_cache table. Mirrors journey_content_service.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.database import get_supabase
from app.schemas.money_moves import MoneyMovesResponse

logger = logging.getLogger(__name__)


class MoneyMovesContentService:
    # In-memory cache (content is near-static; refresh hourly).
    _cache: Optional[tuple[float, MoneyMovesResponse]] = None
    _TTL_SECONDS = 3600
    _inflight: Optional[asyncio.Future] = None

    async def get_money_moves(self) -> MoneyMovesResponse:
        cached = MoneyMovesContentService._cache
        if cached and time.time() - cached[0] < self._TTL_SECONDS:
            return cached[1]

        # Dedup concurrent refreshes (thundering-herd guard).
        if MoneyMovesContentService._inflight is not None:
            return await MoneyMovesContentService._inflight

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        MoneyMovesContentService._inflight = future
        try:
            response = await self._load()
            MoneyMovesContentService._cache = (time.time(), response)
            future.set_result(response)
            return response
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, never 500 the screen
            logger.exception("Failed to load money moves content: %s", exc)
            # Serve stale cache if we have one; otherwise an empty list.
            fallback = cached[1] if cached else MoneyMovesResponse(articles=[])
            future.set_result(fallback)
            return fallback
        finally:
            MoneyMovesContentService._inflight = None

    async def _load(self) -> MoneyMovesResponse:
        rows = await asyncio.to_thread(self._fetch_rows)
        rows.sort(key=lambda r: r.get("sort_order") or 0)
        articles: List[Dict[str, Any]] = []
        for row in rows:
            content = row.get("content")
            if not content:
                continue  # skip rows that haven't been seeded with a content blob yet
            # The narration voice lives in the audio_url column once generated; overlay it
            # so the served article reflects it even if content.audioUrl is stale/null.
            audio_url = row.get("audio_url")
            if audio_url:
                content["audioUrl"] = audio_url
                content["hasAudioVersion"] = True
            if row.get("audio_duration_seconds"):
                content["audioDurationSeconds"] = row["audio_duration_seconds"]
            articles.append(content)
        return MoneyMovesResponse(articles=articles)

    def _fetch_rows(self) -> List[dict]:
        sb = get_supabase()
        result = (
            sb.table("money_move_articles")
            .select("id, slug, sort_order, audio_url, audio_duration_seconds, content")
            .execute()
        )
        return result.data or []


_service: Optional[MoneyMovesContentService] = None


def get_money_moves_content_service() -> MoneyMovesContentService:
    global _service
    if _service is None:
        _service = MoneyMovesContentService()
    return _service
