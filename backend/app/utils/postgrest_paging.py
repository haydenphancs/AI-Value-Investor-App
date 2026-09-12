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
  * `competitor_intel_service` / `ip_intel_service` / `hydrate_hedge_fund_flow`
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
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

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
