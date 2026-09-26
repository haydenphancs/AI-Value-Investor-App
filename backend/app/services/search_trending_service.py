"""
The search-screen chips: "Trending searches", "Most added" and the curated "Popular" fallback.

READ PATH, IMPERSONAL. Every caller gets the same lists; nothing here reads, keys on or
logs the caller (an AST test in tests/test_search_trending_service.py fails the build if a
user identifier ever appears in this module). The write path is `search_pick_service`.

Where the numbers come from (migration 179):
  • Trending searches — `get_search_trending`: anonymous daily counters of search-RESULT
    taps, de-duplicated per account per ticker per 7 ET days before they are written.
  • Most added — `get_most_added_tickers`: distinct real accounts that added the ticker to a
    watchlist in the window, excluding each account's first 24 h (onboarding) and admins.
Both enforce the privacy floor of 3 INSIDE SQL, and this module drops anything below it
again. The lists never carry a count.

When too little real activity qualifies, a section falls back to ONE curated "Popular"
section (backend/data/search_trending_popular.json), honestly labelled — never "Trending".

Cache: in memory only, 1 h (a degraded answer for 60 s), with `_inflight` so concurrent
first callers share one computation. There is no Supabase tier: the upstream IS Supabase —
two indexed aggregate calls an hour — so caching it in itself would save nothing (the same
reasoning as the active-listing directory, SYSTEM_DESIGN_GUIDELINES §7.1). A restart costs
two small calls. `get_trending()` never raises: the search screens must never break.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from app.database import get_supabase
from app.schemas.search_trending import (
    PICK_TYPES,
    SYMBOL_PATTERN,
    SearchTrendingItemResponse,
    SearchTrendingResponse,
    SearchTrendingSectionResponse,
)
from app.schemas.stock import StockSearchResult
from app.services import stock_search_service
from app.services.crypto_names import CRYPTO_NAMES
from app.utils.inflight import fail_shared_future
from app.utils.market_hours import ET
from app.utils.supabase_async import sb_exec

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
#: The privacy floor. Also enforced inside both SQL functions (GREATEST(p_min, 3)).
MIN_PEOPLE = 3
SECTION_MAX_ITEMS = 8
#: A list with fewer live items than this is replaced by the curated section: two or three
#: chips under "Trending searches" read as broken, not as trending.
MIN_LIVE_ITEMS = 5
#: Fetched per list so the stocks-only variant still fills after filtering.
_RPC_FETCH_LIMIT = 40
NEW_ACCOUNT_GRACE_HOURS = 24
_TTL_SECONDS = 3600.0
_DEGRADED_TTL_SECONDS = 60.0
#: A list whose RPC fails is served from its last good copy for up to this long.
_STALE_MAX_SECONDS = 86400.0
RETENTION_DAYS = 14

_TABLE = "search_pick_daily"
_CURATED_PATH = Path(__file__).resolve().parents[2] / "data" / "search_trending_popular.json"

Item = SearchTrendingItemResponse
Section = SearchTrendingSectionResponse

_SYMBOL_RE = re.compile(SYMBOL_PATTERN)


# ── Curated fallback ──────────────────────────────────────────────────────────

class Curated:
    """The validated contents of search_trending_popular.json."""

    def __init__(self, all_items: List[Item], stock_items: List[Item], blocked: FrozenSet[str]):
        self.all_items = all_items
        self.stock_items = stock_items
        self.blocked = blocked

    @property
    def names(self) -> Dict[str, str]:
        return {i.symbol: i.name for i in [*self.all_items, *self.stock_items]}


def _curated_items(raw: Any, *, stocks_only: bool) -> List[Item]:
    items: List[Item] = []
    seen = set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        kind = str(row.get("type") or "stock").strip().lower()
        if not sym or kind not in PICK_TYPES or (stocks_only and kind != "stock"):
            continue
        if (sym, kind) in seen:
            continue
        seen.add((sym, kind))
        items.append(Item(symbol=sym, name=str(row.get("name") or "").strip(), type=kind))
    return items


def load_curated(path: Path = _CURATED_PATH) -> Curated:
    """Never raises. A broken file logs ERROR and yields empty lists — the app then shows
    its own bundled copy (the offline floor), so the chips still appear."""
    try:
        data = json.loads(path.read_text())
        blocked = frozenset(
            str(s).strip().upper() for s in data.get("blocked") or [] if str(s).strip()
        )
        return Curated(
            _curated_items(data.get("all"), stocks_only=False),
            _curated_items(data.get("stocks"), stocks_only=True),
            blocked,
        )
    except Exception as e:  # noqa: BLE001 — a bad file must not take the endpoint down
        logger.error(
            "search trending: curated list %s unreadable (%s: %s) — serving no Popular section",
            path, type(e).__name__, e, exc_info=True,
        )
        return Curated([], [], frozenset())


_CURATED = load_curated()


# ── Pure transforms (tested directly) ─────────────────────────────────────────

def _count_at_least_floor(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value >= MIN_PEOPLE


def normalize_rows(
    rows: Any,
    *,
    count_key: str,
    directory: Optional[Dict[str, str]],
    curated_names: Dict[str, str],
    blocked: Iterable[str] = (),
) -> List[Item]:
    """RPC rows → display items. Drops, silently and per row:
    malformed rows; counts below the floor (again — defence in depth) or non-finite;
    unknown types; symbols of the wrong shape (a watchlist row is client-writable, so
    "SEND ETH TO 0X…" or a dotted "BRK.B" can arrive); coins the app cannot price;
    NASDAQ mutual funds; listings the search rules would hide (preferreds, notes, dead or
    renamed tickers — `stock_search_service.would_keep`); and `blocked` symbols.

    NAMES NEVER COME FROM THE ROW. `watchlist_items.company_name` is client-writable, so a
    row's name would let the latest adder rename a chip for every user; the directory (FMP's
    active list) or the curated file names it, else the chip shows the symbol alone.

    De-duplicated per SECURITY — (symbol, is-crypto) — so a symbol whose stock and etf rows
    both qualified is one chip, while BTC the coin and BTC the ETF stay two. Order kept."""
    blocked_set = {str(b).upper() for b in blocked}
    out: List[Item] = []
    seen = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        ticker, kind = row.get("ticker"), row.get("asset_type")
        if not isinstance(ticker, str) or not isinstance(kind, str):
            continue
        if not _count_at_least_floor(row.get(count_key)):
            continue
        sym, kind = ticker.strip().upper(), kind.strip().lower()
        if not _SYMBOL_RE.fullmatch(sym) or kind not in PICK_TYPES or sym in blocked_set:
            continue
        if kind == "crypto":
            if sym not in CRYPTO_NAMES and sym.endswith("USD") and sym[:-3] in CRYPTO_NAMES:
                sym = sym[:-3]  # a stored pair that slipped through (BTCUSD)
            if sym not in CRYPTO_NAMES or sym in blocked_set:
                continue
            name = CRYPTO_NAMES[sym]
        else:
            # Search never shows a dotted symbol (FMP spells BRK-B; dotted is foreign), and
            # the mutual-fund rule needs an exchange `would_keep` does not have here.
            if "." in sym or stock_search_service.is_mutual_fund_symbol(sym):
                continue
            directory_name = (directory or {}).get(sym, "")
            # The directory's name also feeds the same-issuer rule (AGNCL beside AGNC).
            candidate = StockSearchResult(symbol=sym, name=directory_name, type=kind)
            if not stock_search_service.would_keep(candidate, "", directory):
                continue
            name = directory_name or curated_names.get(sym, "")
        key = (sym, kind == "crypto")
        if key in seen:
            continue
        seen.add(key)
        out.append(Item(symbol=sym, name=(name or "").strip(), type=kind))
    return out


def assemble_sections(
    searched: Sequence[Item],
    added: Sequence[Item],
    curated: Sequence[Item],
    *,
    stocks_only: bool,
    blocked: Iterable[str] = (),
) -> List[Section]:
    """The ordered sections one surface family shows.

    A list with at least MIN_LIVE_ITEMS items is live (capped at SECTION_MAX_ITEMS).
    Otherwise it is replaced — ONCE, however many lists fell back, at the position of the
    first — by the curated "popular" section, minus any SYMBOL a live section already
    shows (a live BTC ETF beside Popular's BTC coin would be two identical-looking chips).
    `stocks_only` filters BEFORE the threshold, so the company picker falls back on its own
    numbers, not on the all-assets ones."""
    def keep(items: Sequence[Item]) -> List[Item]:
        return [i for i in items if not stocks_only or i.type == "stock"]

    blocked_set = {str(b).upper() for b in blocked}
    slots: List[Optional[Section]] = []   # None = where "popular" goes
    shown = set()
    for kind, items in (("trending_searches", keep(searched)), ("most_added", keep(added))):
        if len(items) >= MIN_LIVE_ITEMS:
            chosen = items[:SECTION_MAX_ITEMS]
            slots.append(Section(kind=kind, items=chosen))
            shown |= {i.symbol for i in chosen}
        elif None not in slots:
            slots.append(None)

    sections: List[Section] = []
    for slot in slots:
        if slot is not None:
            sections.append(slot)
            continue
        popular = [
            i for i in keep(curated)
            if i.symbol not in shown and i.symbol not in blocked_set
        ][:SECTION_MAX_ITEMS]
        if popular:
            sections.append(Section(kind="popular", items=popular))
    return sections


def build_response(
    searched: Sequence[Item],
    added: Sequence[Item],
    curated: Curated,
    now: datetime,
) -> SearchTrendingResponse:
    return SearchTrendingResponse(
        window_days=WINDOW_DAYS,
        computed_at=now.astimezone(timezone.utc).isoformat(),
        sections=assemble_sections(
            searched, added, curated.all_items, stocks_only=False, blocked=curated.blocked,
        ),
        stock_sections=assemble_sections(
            searched, added, curated.stock_items, stocks_only=True, blocked=curated.blocked,
        ),
    )


def _today_et() -> date:
    return datetime.now(ET).date()


def _is_missing_function(e: BaseException) -> bool:
    text = str(e)
    return "PGRST202" in text or "could not find the function" in text.lower()


# ── The service ───────────────────────────────────────────────────────────────

class SearchTrendingService:
    def __init__(self, curated: Optional[Curated] = None):
        self._curated = curated or _CURATED
        # (stored_at, ttl, response)
        self._cache: Optional[Tuple[float, float, SearchTrendingResponse]] = None
        self._inflight: Optional[asyncio.Future] = None
        # Per-list last good items: a failed RPC serves these for up to a day.
        self._last_good: Dict[str, Tuple[float, List[Item]]] = {}
        self._missing_logged: set = set()

    async def get_trending(self) -> SearchTrendingResponse:
        """The current lists. Never raises."""
        now = time.time()
        cached = self._cache
        if cached is not None and now - cached[0] < cached[1]:
            return cached[2]

        fut = self._inflight
        if fut is not None:
            return await asyncio.shield(fut)

        fut = asyncio.get_running_loop().create_future()
        self._inflight = fut
        try:
            response, complete = await self._compute(_today_et())
            self._cache = (time.time(), _TTL_SECONDS if complete else _DEGRADED_TTL_SECONDS, response)
            if not fut.done(): fut.set_result(response)
            return response
        except asyncio.CancelledError:
            # A BaseException: without this arm the joiners parked on the shield would wait
            # for the life of the process.
            fail_shared_future(fut, RuntimeError("search trending computation was cancelled"))
            raise
        except Exception as e:  # noqa: BLE001 — degrade, never fail the search screen
            logger.warning(
                "search trending: computation failed (%s: %s) — serving the curated lists",
                type(e).__name__, e, exc_info=True,
            )
            response = build_response([], [], self._curated, datetime.now(timezone.utc))
            self._cache = (time.time(), _DEGRADED_TTL_SECONDS, response)
            if not fut.done(): fut.set_result(response)
            return response
        finally:
            if self._inflight is fut:
                self._inflight = None

    async def _compute(self, today: date) -> Tuple[SearchTrendingResponse, bool]:
        """Both lists, each degrading on its own. Returns (response, complete)."""
        since_day = today - timedelta(days=WINDOW_DAYS - 1)
        since_ts = datetime.combine(since_day, dtime.min, tzinfo=ET)
        # Kick the active-listing directory first: cold after every deploy, and this is
        # usually the first caller. Its refresh then overlaps the two RPCs below.
        stock_search_service.current_directory()
        client = get_supabase()
        searched_res, added_res = await asyncio.gather(
            sb_exec(client.rpc("get_search_trending", {
                "p_since": since_day.isoformat(),
                "p_min_picks": MIN_PEOPLE,
                "p_limit": _RPC_FETCH_LIMIT,
            })),
            sb_exec(client.rpc("get_most_added_tickers", {
                "p_since": since_ts.isoformat(),
                "p_min_users": MIN_PEOPLE,
                "p_limit": _RPC_FETCH_LIMIT,
                "p_min_account_age_hours": NEW_ACCOUNT_GRACE_HOURS,
            })),
            return_exceptions=True,
        )
        directory = stock_search_service.current_directory()
        # Without the directory the dead-listing and same-issuer rules cannot run, so the
        # answer is DEGRADED: cached for a minute, never recorded as a list's last good copy.
        filtered = directory is not None
        names = self._curated.names
        blocked = self._curated.blocked

        searched, ok_s = self._list_or_stale(
            "get_search_trending", searched_res,
            lambda rows: normalize_rows(rows, count_key="picks", directory=directory,
                                        curated_names=names, blocked=blocked),
            filtered=filtered,
        )
        added, ok_a = self._list_or_stale(
            "get_most_added_tickers", added_res,
            lambda rows: normalize_rows(rows, count_key="adders", directory=directory,
                                        curated_names=names, blocked=blocked),
            filtered=filtered,
        )
        response = build_response(searched, added, self._curated, datetime.now(timezone.utc))
        return response, ok_s and ok_a

    def _list_or_stale(
        self, rpc: str, result: Any, normalize, *, filtered: bool = True,
    ) -> Tuple[List[Item], bool]:
        if isinstance(result, BaseException):
            if _is_missing_function(result):
                if rpc not in self._missing_logged:
                    self._missing_logged.add(rpc)
                    logger.error(
                        "search trending: %s is missing — migration 179 not applied? "
                        "Serving the curated list until it is.", rpc,
                    )
            else:
                logger.warning(
                    "search trending: %s failed (%s: %s) — serving the last good list if "
                    "under a day old", rpc, type(result).__name__, result,
                )
            stale = self._last_good.get(rpc)
            if stale is not None and time.time() - stale[0] <= _STALE_MAX_SECONDS:
                return stale[1], False
            return [], False
        items = normalize(getattr(result, "data", None))
        if filtered:
            self._last_good[rpc] = (time.time(), items)
            return items, True
        # Cold directory: a filtered copy under a day old beats this unfiltered pass.
        stale = self._last_good.get(rpc)
        if stale is not None and time.time() - stale[0] <= _STALE_MAX_SECONDS:
            return stale[1], False
        return items, False

    def sweep_expired(self, today: Optional[date] = None) -> int:
        """Delete counters older than RETENTION_DAYS (ET). Best-effort; runs in main.py
        beside the analytics_events sweep. Returns the row count."""
        cutoff = (today or _today_et()) - timedelta(days=RETENTION_DAYS)
        try:
            result = get_supabase().table(_TABLE).delete().lt("day", cutoff.isoformat()).execute()
            count = len(result.data or [])
            if count:
                logger.info("search_pick_daily sweep: deleted %d row(s) before %s", count, cutoff)
            return count
        except Exception as e:  # noqa: BLE001
            logger.warning("search_pick_daily sweep failed (%s: %s)", type(e).__name__, e)
            return 0


_service: Optional[SearchTrendingService] = None


def get_search_trending_service() -> SearchTrendingService:
    global _service
    if _service is None:
        _service = SearchTrendingService()
    return _service
