"""
Money Moves content service.

Reads authored case-study articles from the `money_move_articles` table and returns
them ordered by sort_order. Each row stores the full iOS-shaped article in its
`content` JSONB column; we return that blob as-is, overlaying the row's `slug`,
`audio_url`, `image_url`, `published_at` and `sort_order` columns onto the blob so the
columns the table is actually keyed and ordered by survive into the response. Article
content changes rarely, so a single in-memory tier with a long TTL is enough — the table
itself is the durable store, so there is no separate *_cache table. Mirrors
journey_content_service.

⚠️ On `sortOrder`: the overlay is still required (a blob missing the key sinks to the BOTTOM
client-side via `?? .max`), but do NOT describe it as the order the user sees. iOS re-sorts
the catalog by DATE — `LearnViewModel.newestFirst` — so `sortOrder`'s only surviving display
effect is the featured tie-break in `MoneyMovesContentStore`. The Wiser section is labelled
"Most Recent" for that reason.

Wiser weekly pieces (plan "Wiser weekly content engine" §1.10, migration 204) add three rules,
all decided HERE so the publisher needs no swap writes and no SQL function:

* **Featured rule** — of the served rows whose `is_featured` column is true, the LOWEST
  sort_order wins (tie: slug); every other row is served `isFeatured: false`. Every published
  Deep Dive keeps its flag and gets a more negative sort_order than the last, so the newest one
  wins on its own and unpublishing it falls back to the previous one, then to the bundle hero.
  iOS shows the first `isFeatured` row by ascending sortOrder, so serving exactly one keeps the
  two sides in agreement. A slug in `NEVER_FEATURE` never wins.
* **Archive rule** — only the newest `LEARN_SERVED_WEEKLY_MAX` rows with `source = 'weekly'`
  are served; bundle rows always are.
* **Cache generation** — `invalidate()` (publish / unpublish) and `sync_with_stamp()` (each
  instance's tick, which covers the Railway deploy overlap) keep a load that started BEFORE the
  change from re-caching the old rows for an hour.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Union

from app.config import settings
from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.schemas.money_moves import MoneyMovesResponse

logger = logging.getLogger(__name__)

#: Slugs that are NEVER served as the featured hero, whatever their `is_featured` column says.
#:
#: A superset of every `money_moves:*` key in `marketing/content_pool.EXCLUDED`
#: (`tests/test_learn_bundle_isolation.py` pins it): those articles were judged unfit to front
#: the brand (unsourced time-relative figures, misconduct stories centred on identifiable people,
#: a named real investor's biography, a valuation verdict on a named company), and the hero is
#: the most prominent slot in Learn. Under the featured rule an unpublish falls back to OLDER
#: flags, so without this list a forgotten flag on one of them would quietly come back as the
#: hero (plan §1.10: "an unpublish never re-features an article on content_pool.EXCLUDED").
#:
#: Copied, not imported: `content_pool` loads the marketing compliance and grounding regex tables
#: at import time, and the Learn content path must not pull the marketing package in for one set.
NEVER_FEATURE: FrozenSet[str] = frozenset({
    "warren-buffetts-early-days",
    "google-vs-microsoft-ai-wars",
    "the-future-of-digital-finance",
    "the-fall-of-enron",
    "the-ftx-collapse",
    "theranos-blood-and-lies",
    "weworks-unraveling",
    "the-fall-of-sears",
})

#: `money_move_articles.source` (migration 204, CHECK bundle | weekly). A row read before 204 is
#: applied has no column at all and is a bundle row: no weekly row can exist without 204.
SOURCE_BUNDLE = "bundle"
SOURCE_WEEKLY = "weekly"

#: Fallback for the archive cap when `settings.LEARN_SERVED_WEEKLY_MAX` is unusable (the field is
#: validated 4..500 at boot, so this only guards a hand-patched or future-renamed setting).
_DEFAULT_SERVED_WEEKLY_MAX = 40

#: How far apart the publishing instance's clock (the ledger stamp) and this instance's clock (the
#: cache's "built at") may be before `sync_with_stamp` misses a change. The cost of the margin is
#: at most one extra reload per publish, so it is generous. Railway hosts are NTP-synced to well
#: under a second; the margin is for the Postgres `now()` vs app-clock mix.
_STAMP_SKEW_SECONDS = 120

# PostgREST's answer to a SELECT naming a column the table does not have: Postgres 42703
# ("column money_move_articles.source does not exist"); PGRST204 is the schema-cache variant.
_MISSING_COLUMN_MARKERS = ("42703", "pgrst204", "does not exist", "could not find")
_SOURCE_WORD_RE = re.compile(r"\bsource\b")


def _is_missing_source_column(exc: BaseException) -> bool:
    """True only for "the `source` column does not exist" — the deploy-order gap before migration
    204 is applied. Any other failure (network, permissions, a different missing column) must
    keep raising so it reaches the stale-cache fallback and its own log line."""
    parts = [getattr(exc, attr, None) for attr in ("code", "message", "details", "hint")]
    text = " ".join(str(p) for p in parts + [exc] if p).lower()
    if not _SOURCE_WORD_RE.search(text):
        return False
    return any(marker in text for marker in _MISSING_COLUMN_MARKERS)


def _parse_instant(value: Any) -> Optional[datetime]:
    """An ISO-8601 string (or datetime) as an aware UTC datetime; None when unusable."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:
        # "0001-01-01T00:00:00+05:00" is a valid ISO string whose UTC instant is year 0. It runs
        # outside any per-row try (the archive sort, the stamp check), so it must not raise.
        return None


def _row_source(row: Dict[str, Any]) -> str:
    """`weekly` only when the column says so. Absent (pre-204) or NULL is a bundle row — the
    conservative reading, since a bundle row is always served and never capped."""
    raw = row.get("source")
    if isinstance(raw, str) and raw.strip().lower() == SOURCE_WEEKLY:
        return SOURCE_WEEKLY
    return SOURCE_BUNDLE


def _served_weekly_max() -> int:
    """`settings.LEARN_SERVED_WEEKLY_MAX`, read at call time; the default on a junk value."""
    raw = getattr(settings, "LEARN_SERVED_WEEKLY_MAX", _DEFAULT_SERVED_WEEKLY_MAX)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        logger.error(
            "money_moves: LEARN_SERVED_WEEKLY_MAX=%r is not a positive integer — using %d",
            raw, _DEFAULT_SERVED_WEEKLY_MAX,
        )
        return _DEFAULT_SERVED_WEEKLY_MAX
    return raw


def _iso_published_at(value: Any) -> Optional[str]:
    """Normalize a `timestamptz` column into the ONE ISO-8601 shape iOS can parse.

    PostgREST renders `timestamptz` as a string with up to SIX fractional digits and a
    `+00:00` offset (`"2026-06-12T14:03:21.123456+00:00"`). Both ISO-8601 parsers shipped in
    the app — `BackendISO8601` and `UpdatesDateParser` — use `ISO8601DateFormatter` with
    `.withFractionalSeconds`, which accepts EXACTLY three. So the raw column value fails to
    decode on the client and the card silently falls back to the drifting
    `publishedDaysAgo` estimate, which is the whole thing this column exists to replace.

    Second precision is plenty for a label that renders "Yesterday" / "Aug 3", so drop the
    fraction entirely and emit `"2026-06-12T14:03:21Z"`.

    Returns None (caller skips the overlay) rather than raising — one malformed timestamp
    must not cost the article its date, let alone the catalog its row.
    """
    if value is None:
        return None
    try:
        raw = value if isinstance(value, str) else str(value)
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:                      # Postgres always sends an offset for
            parsed = parsed.replace(tzinfo=timezone.utc)  # timestamptz; be safe if it doesn't.
        return (
            parsed.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (ValueError, TypeError, OverflowError) as exc:
        logger.warning(
            "money_moves: unparseable published_at %r (%s: %s) — leaving the article on its "
            "publishedDaysAgo estimate",
            value, type(exc).__name__, exc,
        )
        return None


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


# One served article: the raw row (columns) beside the content blob it overlays.
_Entry = Tuple[Dict[str, Any], Dict[str, Any]]


def _apply_archive_cap(entries: List[_Entry], limit: int) -> List[_Entry]:
    """Serve only the newest `limit` weekly pieces; bundle rows always (plan §1.10, 1c).

    Newest = `published_at` descending, then sort_order ascending (a newer Deep Dive has the
    more negative sort_order), then slug — a total order, so two instances agree on the cut. A
    weekly row with no usable `published_at` ranks as the oldest: it is the one to drop, never a
    reason to drop a dated piece. The input order (the catalog order) is kept for what survives.
    """
    weekly = [e for e in entries if _row_source(e[0]) == SOURCE_WEEKLY]
    if len(weekly) <= limit:
        return entries

    def newest_first(entry: _Entry) -> Tuple[int, float, int, str]:
        row = entry[0]
        when = _parse_instant(row.get("published_at"))
        return (
            0 if when else 1,
            -when.timestamp() if when else 0.0,
            _sort_key(row),
            str(row.get("slug") or ""),
        )

    ranked = sorted(weekly, key=newest_first)
    kept_ids = {id(e[1]) for e in ranked[:limit]}
    held_back = [str(e[0].get("slug")) for e in ranked[limit:]]
    logger.info(
        "money_moves: archive rule — serving the newest %d of %d weekly pieces; holding back "
        "%d (newest first: %s)",
        limit, len(weekly), len(held_back), ", ".join(held_back[:10]),
    )
    return [e for e in entries if _row_source(e[0]) != SOURCE_WEEKLY or id(e[1]) in kept_ids]


def _is_flagged(row: Dict[str, Any], content: Dict[str, Any]) -> bool:
    """Whether the row ASKS to be featured. The `is_featured` column is authoritative; the
    blob's `isFeatured` is the fallback only when the column is absent or NULL. Only a real
    `True` counts — a non-boolean value (an int, a string from an out-of-band edit) is a
    malformed flag, and a malformed flag must never put an article in the hero slot."""
    blob = content.get("isFeatured")
    column = row.get("is_featured")
    if isinstance(column, bool):
        if isinstance(blob, bool) and blob != column:
            logger.warning(
                "money_moves: content blob isFeatured=%s disagrees with column is_featured=%s "
                "— column wins (id=%s slug=%s)",
                blob, column, row.get("id"), row.get("slug"),
            )
        return column
    if column is not None:
        logger.warning(
            "money_moves: non-boolean is_featured %r — treating the row as NOT featured "
            "(id=%s slug=%s)",
            column, row.get("id"), row.get("slug"),
        )
        return False
    return blob is True


def _ios_decodable(content: Dict[str, Any]) -> bool:
    """slug and title are hard-required by the iOS DTO: an article without them is dropped on
    decode, so featuring it would leave NO hero while a decodable flagged row was passed over."""
    slug, title = content.get("slug"), content.get("title")
    return (isinstance(slug, str) and bool(slug.strip())
            and isinstance(title, str) and bool(title.strip()))


def _apply_featured_rule(entries: List[_Entry]) -> Optional[str]:
    """Serve exactly one `isFeatured: true` (or none); return the winner's slug.

    `entries` arrive in catalog order — sort_order ascending, slug tie-break, the same total
    order `_load` sorts by — so the FIRST eligible flagged entry is the lowest sort_order. Every
    other entry is served `isFeatured: false`, whatever its blob or column said.
    """
    winner: Optional[str] = None
    flagged_bundle: List[str] = []
    for row, content in entries:
        flagged = _is_flagged(row, content)
        slug = content.get("slug")
        # Both spellings: a Studio edit can leave the blob's slug and the column apart. Only
        # strings — a malformed (unhashable) blob slug must cost this row, not the catalog.
        names = {s for s in (slug, row.get("slug")) if isinstance(s, str)}
        if flagged and _row_source(row) == SOURCE_BUNDLE:
            flagged_bundle.append(str(slug))
        if flagged and (names & NEVER_FEATURE):
            logger.warning(
                "money_moves: row slug=%s is flagged featured but is on NEVER_FEATURE — served "
                "isFeatured=false (clear its is_featured column)",
                slug,
            )
            flagged = False
        if flagged and not _ios_decodable(content):
            logger.warning(
                "money_moves: flagged featured row id=%s slug=%s has no usable slug/title — iOS "
                "would drop it, so it cannot be the hero",
                row.get("id"), row.get("slug"),
            )
            flagged = False
        if flagged and winner is None:
            winner = str(slug)
            content["isFeatured"] = True
        else:
            content["isFeatured"] = False
    # Many flagged WEEKLY rows is the design (each published Deep Dive keeps its flag, so an
    # unpublish falls back to the previous one). More than one flagged BUNDLE row is an
    # authoring mistake in money_moves.json or a Studio edit: only the lowest sort_order shows.
    if len(flagged_bundle) > 1:
        logger.warning(
            "money_moves: %d bundle rows are flagged is_featured (%s) — only the lowest "
            "sort_order is served as the hero; fix money_moves.json and reseed",
            len(flagged_bundle), ", ".join(flagged_bundle),
        )
    if winner is None and entries:
        logger.warning(
            "money_moves: no served article is featured — iOS falls back to the hero bundled in "
            "the installed app build"
        )
    return winner


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
    # Cache GENERATION (plan §1.10). Without it, a load that read the table just BEFORE a publish
    # or unpublish would finish after the publisher's invalidate and re-cache the old rows for
    # the full hour — an unpublished article served on, or a new Deep Dive missing. `invalidate`
    # bumps it, and a load stores its result only if the generation it started under is current.
    _generation: int = 0
    # When the CURRENT cache's load STARTED (UTC) — what it may have missed is anything stamped
    # after this — and when the in-flight load started. Compared by `sync_with_stamp`.
    _built_at: Optional[datetime] = None
    _inflight_started_at: Optional[datetime] = None
    # `money_move_articles.source` missing (migration 204 not applied yet): ERROR once per process.
    _source_column_missing_logged: bool = False

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
        generation = MoneyMovesContentService._generation
        started_at = datetime.now(timezone.utc)
        MoneyMovesContentService._inflight = future
        MoneyMovesContentService._inflight_started_at = started_at
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
            # Still answered to this load's own caller and joiners below — they asked before the
            # change, so the rows they get are no staler than a cache hit would have been.
            self._store_if_current(generation, started_at, response)
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
                    fail_shared_future(future, exc)
                else:
                    future.cancel()
            raise
        finally:
            # Only if the slot is still OURS: `invalidate` detaches an in-flight load, and the
            # caller after it may already have registered a fresh one. Clearing that one would
            # let a third caller start a duplicate load instead of joining it.
            if MoneyMovesContentService._inflight is future:
                MoneyMovesContentService._inflight = None
                MoneyMovesContentService._inflight_started_at = None

    @classmethod
    def _ttl_for(cls, response: MoneyMovesResponse) -> int:
        """Full TTL for a real catalog; a short one for an empty result so it self-heals."""
        return cls._TTL_SECONDS if response.articles else cls._EMPTY_TTL_SECONDS

    @classmethod
    def _store_if_current(
        cls, generation: int, started_at: datetime, response: MoneyMovesResponse,
    ) -> bool:
        """Cache `response` only if no `invalidate` ran since its load began."""
        if cls._generation != generation:
            logger.info(
                "money_moves: the cache was invalidated while this load ran (generation %d -> "
                "%d) — answering its own callers but NOT caching its %d articles",
                generation, cls._generation, len(response.articles),
            )
            return False
        cls._cache = (time.time(), response)
        cls._built_at = started_at
        return True

    @classmethod
    def invalidate(cls, reason: str) -> None:
        """Drop the cached catalog so the next request reads the table (publish / unpublish).

        Bumps the generation, clears the cache, and DETACHES the in-flight load: it keeps running
        and still answers the callers already waiting on it, but it will not store its result,
        and the next caller starts a fresh load instead of joining one that read the old rows.
        Process-local — every other instance converges through `sync_with_stamp` on its tick.
        """
        cls._generation += 1
        detached = cls._inflight is not None
        cls._cache = None
        cls._built_at = None
        cls._inflight = None
        cls._inflight_started_at = None
        logger.info(
            "money_moves: cache invalidated (%s) — generation %d%s",
            reason, cls._generation, "; detached an in-flight load" if detached else "",
        )

    @classmethod
    def sync_with_stamp(cls, stamp: Union[str, datetime, None]) -> bool:
        """Invalidate when the ledger changed after this instance's cache was built.

        `stamp` is the ledger's max(published_at, unpublished_at): the last moment ANY instance
        changed what is served. Publish and unpublish invalidate only the instance that ran them,
        so during a Railway deploy overlap (or with a second instance) the other one would serve
        the old catalog for up to the hour-long TTL. Each instance's tick calls this instead.

        Compared against the moment the cached load STARTED (and the in-flight load's start, if
        one is running), less `_STAMP_SKEW_SECONDS` for clock skew between instances. Returns
        whether it invalidated. A None stamp (nothing ever published) is a no-op; an
        unparseable one is logged and ignored — the TTL still bounds the staleness.
        """
        if stamp is None:
            return False
        changed_at = _parse_instant(stamp)
        if changed_at is None:
            logger.warning(
                "money_moves: sync_with_stamp got an unparseable ledger stamp %r — not "
                "invalidating (the %ds TTL still bounds staleness)",
                stamp, cls._TTL_SECONDS,
            )
            return False
        skew = timedelta(seconds=_STAMP_SKEW_SECONDS)
        now = datetime.now(timezone.utc)
        if changed_at > now + skew:
            # Still honoured (fail toward freshness), but a stamp from the future means a bad
            # ledger value or a skewed clock, and it will invalidate on every tick until fixed.
            logger.warning(
                "money_moves: ledger stamp %s is in the future (now %s) — check the publisher's "
                "clock / the ledger row", changed_at.isoformat(), now.isoformat(),
            )
        built_at = cls._built_at if cls._cache is not None else None
        inflight_at = cls._inflight_started_at if cls._inflight is not None else None
        stale_cache = built_at is not None and changed_at > built_at - skew
        stale_load = inflight_at is not None and changed_at > inflight_at - skew
        if not (stale_cache or stale_load):
            return False
        cls.invalidate(
            f"ledger stamp {changed_at.isoformat()} is newer than the "
            f"{'cache built at ' + built_at.isoformat() if stale_cache else 'in-flight load'}"
        )
        return True

    async def _load(self) -> MoneyMovesResponse:
        rows = await asyncio.to_thread(self._fetch_rows)
        # Sort on (position, slug) — a TOTAL order. Position alone is not: Python's sort is stable
        # so ties keep Postgres' fetch order, which is unspecified without an ORDER BY, while iOS
        # re-sorts client-side with a non-stable `sorted(by:)`. Two rows sharing a sort_order could
        # therefore render in a different order on each side, and in a different order between two
        # launches of the same build. The slug tiebreak is mirrored in MoneyMovesContentStore.swift.
        rows.sort(key=lambda r: (_sort_key(r), str(r.get("slug") or "")))
        entries: List[_Entry] = []
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

                # Artwork, overlaid the same way audio is — the column wins in ONE direction only,
                # and the asymmetry with audio above is deliberate.
                #
                # A populated column overwrites the blob, so re-pointing a plate is a single UPDATE.
                # But an EMPTY column must NOT clear a baked `imageUrl`, which is exactly the
                # opposite of the audio rule. Artwork lives in a PUBLIC bucket and is never signed
                # or expired, so a baked URL cannot go stale the way a narration URL can; whereas
                # the column is written only by seed_money_moves.py, and a seed run from a box
                # without backend/data/money_moves_art/ legitimately produces no column value. Under
                # the audio rule that run would silently strip the artwork from every article.
                #
                # There is also no `hasImage` flag to keep in step: iOS gates purely on a non-empty
                # url and falls back to the article's heroGradientColors otherwise, so a missing
                # image degrades to exactly today's appearance rather than to a broken slot.
                image_url = row.get("image_url")
                if image_url:
                    content["imageUrl"] = image_url

                # The publication date — served so the client can render a REAL one.
                #
                # Without this the card and the article both compute their date at DECODE time
                # as `now - content.publishedDaysAgo` days. That offset is a constant baked into
                # the blob, so the derived date never ages: an article authored with
                # `publishedDaysAgo: 12` reads "12 days ago" today and "12 days ago" in 2030.
                # Fine as an ORDERING key (relative order is preserved forever, which is why the
                # field was documented "never rendered"), useless as a date. This column is a
                # real instant, stamped once by seed_money_moves.py.
                #
                # ONE DIRECTION, like `image_url` above and deliberately NOT like `audio_url`:
                # a populated column wins, an empty one leaves the client on its estimate. The
                # column is nullable and only the seeder writes it, so a row added through
                # Studio — or any row predating the seeder — is NULL. Under the audio rule that
                # single row would have its date actively stripped. And unlike a narration URL,
                # a date has no affordance to leave dangling and cannot 404: stale-but-plausible
                # beats absent. Warn on NULL so it is possible to tell which articles are
                # showing a real date and which are still estimating.
                published_at = _iso_published_at(row.get("published_at"))
                if published_at:
                    content["publishedAt"] = published_at
                else:
                    logger.warning(
                        "money_moves: published_at is empty — article keeps its drifting "
                        "publishedDaysAgo estimate (id=%s slug=%s)",
                        row.get("id"), row.get("slug"),
                    )

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

                entries.append((row, content))
            except Exception as exc:  # noqa: BLE001 — one bad row must not collapse the whole catalog
                logger.warning(
                    "money_moves: skipping malformed row slug=%s: %s: %s",
                    row.get("slug"), type(exc).__name__, exc,
                )
                continue
        # After the per-row overlays, so a malformed row (already skipped) neither takes an
        # archive slot nor the hero. Cap FIRST: a weekly piece that is not served must not be
        # the hero either.
        entries = _apply_archive_cap(entries, _served_weekly_max())
        _apply_featured_rule(entries)
        return MoneyMovesResponse(articles=[content for _row, content in entries])

    def _fetch_rows(self) -> List[dict]:
        sb = get_supabase()
        # `title` is fetched for the same reason as `slug`: it is a HARD-REQUIRED field on the
        # iOS DTO, so a blob that lost it decodes to nothing and the article silently vanishes
        # client-side. The column is NOT NULL, so it is always a usable fallback.
        # `is_featured` is the authoritative hero flag and `source` (migration 204) tells a
        # weekly piece from a bundle article — both read by the featured and archive rules.
        # Two LITERAL lists, not one string built twice: `test_money_moves_art_parity` reads the
        # SELECT itself, and `test_money_moves_content_service` pins the fallback to this list
        # minus `source`, so the two cannot drift apart.
        try:
            result = (
                sb.table("money_move_articles")
                .select("id, slug, title, sort_order, audio_url, audio_duration_seconds, "
                        "image_url, published_at, is_featured, source, content")
                .execute()
            )
        except Exception as exc:
            if not _is_missing_source_column(exc):
                raise
            # Deploy order: this code can reach production before the owner applies migration
            # 204. The catalog must never go dark for that — re-read without the column, which
            # serves every row as a bundle row (correct: no weekly row exists before 204).
            cls = MoneyMovesContentService
            if not cls._source_column_missing_logged:
                cls._source_column_missing_logged = True
                logger.error(
                    "money_moves: money_move_articles.source does not exist (%s: %s) — apply "
                    "migration 204_learn_weekly_pipeline.sql; serving every row as a bundle row "
                    "until then",
                    type(exc).__name__, exc,
                )
            else:
                logger.warning(
                    "money_moves: money_move_articles.source still missing — re-read without it "
                    "(apply migration 204)"
                )
            result = (
                sb.table("money_move_articles")
                .select("id, slug, title, sort_order, audio_url, audio_duration_seconds, "
                        "image_url, published_at, is_featured, content")
                .execute()
            )
        return result.data or []


_service: Optional[MoneyMovesContentService] = None


def get_money_moves_content_service() -> MoneyMovesContentService:
    global _service
    if _service is None:
        _service = MoneyMovesContentService()
    return _service
