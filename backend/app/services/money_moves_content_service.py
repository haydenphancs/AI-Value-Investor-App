"""
Money Moves content service.

Reads authored case-study articles from the `money_move_articles` table and returns
them ordered by sort_order. Each row stores the full iOS-shaped article in its
`content` JSONB column; we return that blob as-is, overlaying the row's `slug`,
`audio_url` and `sort_order` columns onto the blob so the columns the table is actually
keyed and ordered by survive into the response (the client re-sorts by
`content.sortOrder`, so an un-overlaid column is an order the user never sees). Article content changes
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


def _sort_key(row: Dict[str, Any]) -> int:
    """The row's catalog position, as an int, always.

    Single source of truth for BOTH the server-side sort and the `sortOrder` overlay in
    `_load` — they used to be derived separately, which is how the column's ordering came to
    be silently discarded client-side (iOS re-sorts by `content.sortOrder ?? .max`, so a blob
    without the key sank to the bottom no matter what the column said).

    Coerces defensively because this runs OUTSIDE the per-row try/except: a single row whose
    `sort_order` arrived as a string would make `rows.sort()` raise TypeError comparing str to
    int, which propagates out of `_load` and collapses the ENTIRE catalog to the stale/empty
    fallback. One bad row must cost one bad row.
    """
    raw = row.get("sort_order")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "money_moves: non-numeric sort_order %r on row id=%s slug=%s — treating as 0",
            raw, row.get("id"), row.get("slug"),
        )
        return 0


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
    # An EMPTY load is cached for seconds, not the full hour. A successful-but-empty load is not
    # the same fact as a successful load: it happens during a reseed window (rows briefly deleted
    # then reinserted) or when every row is skipped as malformed. Caching that for 3600s served an
    # empty catalog to every client for up to an hour AFTER the table was healthy again — each one
    # silently falling back to the bundled copy and losing DB-only articles and their audio. Note
    # the exception path never caches at all and self-heals on the next request; this closes the
    # same gap for the empty-success path.
    _EMPTY_TTL_SECONDS = 30
    _inflight: Optional[asyncio.Future] = None

    async def get_money_moves(self) -> MoneyMovesResponse:
        cached = MoneyMovesContentService._cache
        if cached and time.time() - cached[0] < self._ttl_for(cached[1]):
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
            # An empty load must never REPLACE a good catalog: prefer the stale-but-real one and
            # retry on the next request. Without this a single reseed-window read pinned every
            # client to the bundled fallback for an hour.
            if not response.articles and cached and cached[1].articles:
                logger.warning(
                    "money_moves: load returned 0 articles — keeping the previous cache of %d "
                    "and retrying on the next request",
                    len(cached[1].articles),
                )
                if not future.done():
                    future.set_result(cached[1])
                return cached[1]
            if not response.articles:
                # Nothing to fall back to. Serve it, but cache it only briefly (_EMPTY_TTL_SECONDS)
                # so the catalog self-heals as soon as the rows are back.
                logger.warning("money_moves: load returned 0 articles and no prior cache to keep")
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

    @classmethod
    def _ttl_for(cls, response: MoneyMovesResponse) -> int:
        """Full TTL for a real catalog; a short one for an empty result so it self-heals."""
        return cls._TTL_SECONDS if response.articles else cls._EMPTY_TTL_SECONDS

    async def _load(self) -> MoneyMovesResponse:
        rows = await asyncio.to_thread(self._fetch_rows)
        # Sort on (position, slug) — a TOTAL order. Position alone is not: Python's sort is stable
        # so ties keep Postgres' fetch order, which is unspecified without an ORDER BY, while iOS
        # re-sorts client-side with a non-stable `sorted(by:)`. Two rows sharing a sort_order could
        # therefore render in a different order on each side, and in a different order between two
        # launches of the same build. The slug tiebreak is mirrored in MoneyMovesContentStore.swift.
        rows.sort(key=lambda r: (_sort_key(r), str(r.get("slug") or "")))
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

                # Same treatment for `title` — also hard-required on iOS, and until now neither
                # fetched nor overlaid, so a Studio edit that dropped it made the article vanish
                # client-side with NOTHING in the backend logs to explain it.
                row_title = row.get("title")
                blob_title = content.get("title")
                if not isinstance(blob_title, str) or not blob_title.strip():
                    if isinstance(row_title, str) and row_title.strip():
                        logger.warning(
                            "money_moves: content blob missing title — overlaying row column "
                            "(id=%s slug=%s title=%r)",
                            row.get("id"), row.get("slug"), row_title,
                        )
                        content["title"] = row_title
                    else:
                        logger.error(
                            "money_moves: row has no usable title in content OR column — iOS will "
                            "drop this article (id=%s slug=%s)",
                            row.get("id"), row.get("slug"),
                        )

                # The narration voice lives in the audio_url column once generated; overlay it
                # so the served article reflects it even if content.audioUrl is stale/null.
                # BOTH directions matter. Setting the flag only when the column is populated left
                # a row whose clip was removed (column NULLed) serving the blob's authored
                # `hasAudioVersion: true` AND its baked `audioUrl`.
                #
                # The URL is the part that actually bites: iOS ignores the served flag and gates
                # Listen on `audioUrl != nil` (`MoneyMovesContentModels.swift` — "honest gate"),
                # so a stale baked URL renders a Listen button that streams a dead object while
                # the flag alone would have been harmless. Clear both; the column is authoritative.
                audio_url = row.get("audio_url")
                if audio_url:
                    content["audioUrl"] = audio_url
                    content["hasAudioVersion"] = True
                    if row.get("audio_duration_seconds"):
                        content["audioDurationSeconds"] = row["audio_duration_seconds"]
                else:
                    if content.get("hasAudioVersion") or content.get("audioUrl"):
                        logger.warning(
                            "money_moves: audio_url column is empty but the blob claims audio — "
                            "clearing the stale Listen affordance (id=%s slug=%s)",
                            row.get("id"), row.get("slug"),
                        )
                    content["hasAudioVersion"] = False
                    content.pop("audioUrl", None)
                    content.pop("audioDurationSeconds", None)

                # Overlay the row's `sort_order` COLUMN unconditionally. The response carries only
                # these `content` blobs, so without this the column — the thing this service sorts
                # by — never reaches the client at all, and iOS re-sorts by `content.sortOrder ?? .max`
                # (MoneyMovesContentStore.swift): a blob missing the key sinks to the BOTTOM, exactly
                # inverting a curated order. Unconditional matters — overlaying only when the column
                # is non-NULL leaves the identical bug for NULL rows, which sort FIRST here (via
                # _sort_key -> 0) and LAST on iOS (via ?? .max).
                blob_sort = content.get("sortOrder")
                row_sort = _sort_key(row)
                if isinstance(blob_sort, int) and blob_sort != row_sort:
                    logger.warning(
                        "money_moves: content blob sortOrder=%s disagrees with column sort_order=%s "
                        "— column wins (id=%s slug=%s)",
                        blob_sort, row_sort, row.get("id"), row.get("slug"),
                    )
                content["sortOrder"] = row_sort

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
            # `title` is fetched for the same reason as `slug`: it is a HARD-REQUIRED field on the
            # iOS DTO, so a blob that lost it decodes to nothing and the article silently vanishes
            # client-side. The column is NOT NULL, so it is always a usable fallback.
            .select("id, slug, title, sort_order, audio_url, audio_duration_seconds, content")
            .execute()
        )
        return result.data or []


_service: Optional[MoneyMovesContentService] = None


def get_money_moves_content_service() -> MoneyMovesContentService:
    global _service
    if _service is None:
        _service = MoneyMovesContentService()
    return _service
