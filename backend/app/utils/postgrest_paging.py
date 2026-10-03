"""Read every row PostgREST will give you, not the first page it happens to return.

WHY THIS EXISTS
---------------
PostgREST clamps a response to its server-side `max-rows` (~1,000 on this project)
**regardless of the `.limit()` or `.range()` the client asks for** — measured in this repo
at `market_movers_service` ("verified: `.range(0, 49999)` still returns 1,000") and again
at `news_cache_service`. So `.limit(50_000)` is not a bigger read; it is a 1,000-row read
with a comment claiming otherwise, and because the call SUCCEEDS the truncation is silent.

Five sites relied on exactly that, each with a comment explaining the cap it was not
lifting (found 2026-09-12):

  * `price_alert_service`   `.limit(MAX_RULES=5000)`  — alert rules past row 1,000 never fire
  * `signals_service`       `.limit(10000)`           — "N funds adding" under-counts
  * `ip_intel_service` / `hydrate_hedge_fund_flow` (and the retired `competitor_intel_service`)
                            `.limit(50_000)`          — the "top watchlisted tickers"
                                                        universe computed from an
                                                        arbitrary unordered sample

`sector_benchmark_lookup._page_all` already did this correctly; this is that loop, lifted
so there is one implementation to reason about.

ORDER MATTERS. Without an `ORDER BY`, Postgres may return rows in any order and the pages
can overlap or skip — so every caller passes the column to order on, and it must be
unique-ish (a primary key, or a key plus a tiebreaker).
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class PagedReadIncomplete(RuntimeError):
    """A paged read could not PROVE it returned every row, so it returned none.

    Raised by `fetch_all_rows_concurrent` instead of a partial list: a short page in the
    middle, fewer rows than the exact count, a missing count, or more pages than the
    backstop allows. A partial close map / universe looks exactly like a complete one
    downstream, so the only safe answer to "I am not sure I have everything" is to fail.
    """

#: One request's worth. Matching the server's own cap means the "short page → done" test
#: fires exactly once, at the end.
PAGE_SIZE = 1000

#: Backstop so a pathological table cannot spin forever. 200 pages = 200,000 rows; every
#: caller here is far below that, and crossing it is a signal worth logging, not silence.
MAX_PAGES = 200


def fetch_all_rows(
    build_query: Callable[[], Any],
    *,
    order_by: str,
    what: str,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    desc: bool = False,
) -> List[Dict[str, Any]]:
    """Page a PostgREST SELECT to completion.

    `build_query` returns a FRESH, un-executed query each call (filters applied, no
    `.range()`/`.order()`) — fresh because postgrest-py builders are stateful and reusing
    one accumulates the previous page's range.
    """
    rows: List[Dict[str, Any]] = []
    if page_size > PAGE_SIZE:
        # The server clamps every page to PAGE_SIZE whatever is asked, so a larger
        # request would make the first (short) page look like the last one and return
        # PAGE_SIZE rows as "complete" — the exact silent truncation this helper exists
        # to remove. Clamp, and page more times instead.
        logger.warning(
            "%s: page_size %d exceeds the PostgREST cap %d — clamping",
            what, page_size, PAGE_SIZE,
        )
        page_size = PAGE_SIZE
    if page_size <= 0:
        raise ValueError(f"{what}: page_size must be positive, got {page_size}")
    for page in range(max_pages):
        start = page * page_size
        batch = (
            build_query()
            .order(order_by, desc=desc)
            .range(start, start + page_size - 1)
            .execute()
            .data
        ) or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
    logger.warning(
        "%s: stopped paging at %d rows (%d pages) — the read is capped, not complete",
        what, len(rows), max_pages,
    )
    return rows


def fetch_all_rows_concurrent(
    build_query: Callable[[], Any],
    *,
    count_query: Callable[[], Any],
    order_by: str,
    what: str,
    page_size: int = PAGE_SIZE,
    workers: int = 4,
    max_pages: int = MAX_PAGES,
    desc: bool = False,
) -> List[Dict[str, Any]]:
    """Page a PostgREST SELECT to completion with `workers` pages in flight — or raise.

    SYNC, like `fetch_all_rows` (call it through `asyncio.to_thread`). The serial helper
    pays one round trip per 1,000 rows; for the ~74-page `market_close_snapshot` sweep that
    was 11-14 s across regions, past the Home scanner's 8 s guard. Four pages at a time
    makes it ~3 s. Four, not more: the PostgREST httpx pool is 20 connections
    (`database.py`), and request-path reads need the rest.

    Parallel OFFSET paging cannot use "a short page means done" — page 40 may finish
    before page 3 — so the read is bounded by an exact COUNT taken first, and every way it
    can come back short raises `PagedReadIncomplete` instead of returning a partial list:

      * `count_query()` returns a FRESH query whose `.execute().count` is the exact row
        count (`.select(col, count=CountMethod.exact, head=True)`). A missing count
        raises: without it nothing proves the read complete.
      * Pages `0 .. ceil(count / page_size)` are fetched — one more than the count needs,
        a SENTINEL page that catches rows added after the count. A non-empty sentinel
        means the table grew; paging continues serially until a short page.
      * Assembled in PAGE order, never completion order (offset order is the only order
        that means anything).
      * Every page before the last non-empty one must be exactly `page_size` rows. A
        short page in the middle means rows moved out from under the sweep (a delete, or
        a server `max-rows` below `page_size`) and something in between was skipped.
      * Fewer rows than the count raises.
      * Any page raising cancels the pages not yet started and re-raises that error.

    `build_query` must return a FRESH, un-executed query each call, exactly as for
    `fetch_all_rows`, and `order_by` must be unique (`test_postgrest_paging_order_key`).
    """
    if page_size > PAGE_SIZE:
        logger.warning(
            "%s: page_size %d exceeds the PostgREST cap %d — clamping",
            what, page_size, PAGE_SIZE,
        )
        page_size = PAGE_SIZE
    if page_size <= 0:
        raise ValueError(f"{what}: page_size must be positive, got {page_size}")
    if workers <= 0:
        raise ValueError(f"{what}: workers must be positive, got {workers}")

    total = getattr(count_query().execute(), "count", None)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise PagedReadIncomplete(
            f"{what}: the exact row count came back {total!r} — a parallel read cannot "
            f"prove itself complete without it"
        )
    n_pages = math.ceil(total / page_size) + 1          # + the sentinel page
    if n_pages > max_pages:
        raise PagedReadIncomplete(
            f"{what}: {total} rows need {n_pages} pages of {page_size}, over the "
            f"{max_pages}-page backstop — refusing a capped read"
        )

    def _page(i: int) -> List[Dict[str, Any]]:
        start = i * page_size
        batch = (
            build_query()
            .order(order_by, desc=desc)
            .range(start, start + page_size - 1)
            .execute()
            .data
        )
        return list(batch or [])

    pages: List[Optional[List[Dict[str, Any]]]] = [None] * n_pages
    pool = ThreadPoolExecutor(
        max_workers=min(workers, n_pages), thread_name_prefix=f"page:{what}",
    )
    try:
        futures = [pool.submit(_page, i) for i in range(n_pages)]
        wait(futures, return_when=FIRST_EXCEPTION)
        failed = next(
            (i for i, f in enumerate(futures) if f.done() and not f.cancelled()
             and f.exception() is not None),
            None,
        )
        if failed is not None:
            exc = futures[failed].exception()
            logger.warning(
                "%s: page %d of %d failed (%s: %s) — abandoning the whole read",
                what, failed, n_pages, type(exc).__name__, exc,
            )
            raise exc
        for i, f in enumerate(futures):
            pages[i] = f.result()
    finally:
        # On failure: pages not yet started are dropped, and the (at most `workers`)
        # running ones finish here with their rows discarded — no thread outlives the read.
        pool.shutdown(wait=True, cancel_futures=True)

    # The table grew past the count: the sentinel is full, so keep going, serially.
    while len(pages[-1] or []) >= page_size:
        if len(pages) >= max_pages:
            raise PagedReadIncomplete(
                f"{what}: still growing after {len(pages)} pages — refusing a capped read"
            )
        pages.append(_page(len(pages)))

    last_nonempty = max((i for i, p in enumerate(pages) if p), default=-1)
    for i, p in enumerate(pages):
        n = len(p or [])
        if n > page_size or (i < last_nonempty and n != page_size):
            raise PagedReadIncomplete(
                f"{what}: page {i} returned {n} rows where {page_size} were due (last "
                f"non-empty page {last_nonempty}) — rows moved during the sweep, or the "
                f"server caps a response below {page_size}; refusing a read with a hole"
            )

    rows: List[Dict[str, Any]] = [r for p in pages for r in (p or [])]
    if len(rows) < total:
        raise PagedReadIncomplete(
            f"{what}: read {len(rows)} rows but the exact count was {total} — refusing "
            f"a partial read"
        )
    return rows
