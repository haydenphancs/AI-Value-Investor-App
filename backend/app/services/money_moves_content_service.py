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


class MoneyMovesContentService:
    # In-memory cache (content is near-static; refresh hourly).
    _cache: Optional[tuple[float, MoneyMovesResponse]] = None
    _TTL_SECONDS = 3600
    _inflight: Optional[asyncio.Future] = None

    async def get_money_moves(self) -> MoneyMovesResponse:
        cached = MoneyMovesContentService._cache
        if cached and time.time() - cached[0] < self._TTL_SECONDS:
            return cached[1]

        # Dedup concurrent refreshes (thundering-herd guard). SHIELDED: awaiting the shared future
        # directly propagates a WAITER's cancellation into it, so one caller giving up — chat
        # context resolution wraps this in asyncio.wait_for(timeout=4.0) — would cancel the future
        # the leader is about to resolve. The leader's set_result then raises InvalidStateError and
        # 500s a public content request whose data actually loaded fine.
        inflight = MoneyMovesContentService._inflight
        if inflight is not None:
            return await asyncio.shield(inflight)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        MoneyMovesContentService._inflight = future
        try:
            response = await self._load()
            MoneyMovesContentService._cache = (time.time(), response)
            if not future.done():
                future.set_result(response)
            return response
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, never 500 the screen
            logger.exception(
                "money_moves: failed to load content: %s: %s", type(exc).__name__, exc
            )
            # Serve stale cache if we have one; otherwise an empty list.
            fallback = cached[1] if cached else MoneyMovesResponse(articles=[])
            if not future.done():
                future.set_result(fallback)
            return fallback
        except BaseException as exc:
            # CancelledError is a BaseException, so `except Exception` misses it — and then the
            # `finally` below would clear _inflight while the future stays pending FOREVER, hanging
            # every joined waiter (nothing in this stack has a timeout). Resolve it before leaving.
            logger.warning(
                "money_moves: content load aborted (%s) — releasing %d joined waiter(s)",
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
            MoneyMovesContentService._inflight = None

    async def _load(self) -> MoneyMovesResponse:
        rows = await asyncio.to_thread(self._fetch_rows)
        rows.sort(key=lambda r: r.get("sort_order") or 0)
        articles: List[Dict[str, Any]] = []
        for row in rows:
            content = row.get("content")
            # Guard the SHAPE, not just truthiness: a truthy but non-dict content (a JSON list
            # or string from a bad/out-of-band row) would make the item-assignment below raise
            # TypeError, propagate out of _load, and on a cold cache collapse the ENTIRE catalog
            # to []. Skip+log the bad row instead so the rest of the catalog still serves.
            if not isinstance(content, dict):
                if content:
                    logger.warning(
                        "money_moves: skipping row slug=%s with non-dict content (%s)",
                        row.get("slug"), type(content).__name__,
                    )
                continue  # also skips rows not yet seeded with a content blob
            try:
                # Overlay the row's `slug` COLUMN when the content blob doesn't carry a usable one.
                # slug is the article's identity on iOS (a required DTO field, and the key card taps
                # resolve through), so a Studio-edited row that dropped it decodes to nothing: the
                # article is silently discarded client-side and the reader keeps seeing the stale
                # BUNDLED copy — the exact failure the "no app update needed" design exists to
                # avoid. The column is the authoritative id, so prefer it over an absent blob value.
                row_slug = row.get("slug")
                blob_slug = content.get("slug")
                if not isinstance(blob_slug, str) or not blob_slug.strip():
                    if isinstance(row_slug, str) and row_slug.strip():
                        logger.warning(
                            "money_moves: content blob missing slug — overlaying row column "
                            "(id=%s slug=%s title=%r)",
                            row.get("id"), row_slug, content.get("title"),
                        )
                        content["slug"] = row_slug
                    else:
                        # Nothing to overlay: iOS will drop this article on decode. Say so here so
                        # the cause is in the backend logs, not just an unexplained missing card.
                        logger.error(
                            "money_moves: row has no usable slug in content OR column — iOS will "
                            "drop this article (id=%s title=%r)",
                            row.get("id"), content.get("title"),
                        )

                # The narration voice lives in the audio_url column once generated; overlay it
                # so the served article reflects it even if content.audioUrl is stale/null.
                audio_url = row.get("audio_url")
                if audio_url:
                    content["audioUrl"] = audio_url
                    content["hasAudioVersion"] = True
                if row.get("audio_duration_seconds"):
                    content["audioDurationSeconds"] = row["audio_duration_seconds"]
                articles.append(content)
            except Exception as exc:  # noqa: BLE001 — one bad row must not collapse the whole catalog
                logger.warning(
                    "money_moves: skipping malformed row slug=%s: %s: %s",
                    row.get("slug"), type(exc).__name__, exc,
                )
                continue
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
