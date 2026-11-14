"""Is this ticker DELISTED? The evidence behind the app's grey "Delisted" label.

Why it exists (2026-11-13): Fox Corp acquired Roku. FMP's delisted-companies list has ROKU
(NASDAQ, delisted 2026-11-09; its last trade was 2026-11-06 at $160.52), and FMP's profile
says `isActivelyTrading: false` — but FMP keeps a delisted company's last price in its
profile for MONTHS (measured: SEE, AL, APGE, delisted April and September). Nothing reached
iOS, so the detail screen showed $160.52 under a clock-only "Market Open" badge and polled it
every 15 s, Tracking and Home showed it like any other price, and Cay AI called it "(live)".

The rule (owner decisions D2 and D4, 2026-11-13). Say "delisted" only when ALL hold:
  (b) `session_pricing.priced_in_latest_session(sym)` is exactly False — it did not trade in
      the latest US session (checked FIRST: sync, free, and it rules out every live ticker);
  (a) FMP's profile flag reads inactive — the quote's emitted flag
      (`price_service._from_profile`), else the raw profile when no quote is in hand;
  (c) FMP's delisted-companies list has the symbol with `delistedDate <= today` on the ET
      calendar (the newest such row; a FUTURE-dated, scheduled row never matches);
  (d) it has had no traded day (EOD volume > 0) AFTER that date — one EOD read from
      `delistedDate - 45 d` to today, which also supplies the "last close" session date.
Anything unknown — a cold or stale registry, no quote, a failed or slow read, not on the
list — answers None: no label, exactly the behaviour before this existed (fail open).

The list also carries exchange MOVES (ET, DDS, USAC, TCBI: NYSE → Texas Stock Exchange,
2026-10-05). D4: a move FMP no longer prices IS labelled ("Delisted from NYSE · Oct 5, 2026");
the copy never claims the company stopped trading and never gives a reason. ET, which FMP
prices again, fails (b) — and if FMP ever stopped pricing it again, (d) still rejects it,
because it traded after its 10-05 row.

IN MEMORY ONLY — a deliberate Tier 1 with no Tier 2, like the active-listing directory
(`stock_search_service`): persisting a bulk FMP symbol list is the ToS 2.6.1 redistribution
surface migration 157 avoids; the useful part is the HEAD of the list, which changes daily, so
a stored copy is stale by design; a restart costs one lazy load; and the answer is only used
in-process, for rare candidates.

FMP calls: ZERO while no ticker on any screen is a candidate (the common case). With
candidates: the window — `_WINDOW_PAGES` pages (~8 months), fetched concurrently under a
semaphore, loaded only when the first candidate appears, head pages refreshed every 6 h,
fully reloaded every 24 h, served stale up to 3 days — plus one EOD read per delisted symbol
per 6 h. About 40-50 calls a day in practice.

Never raises to a caller, never mutates the quote/profile dicts it is given (price_service
shares them by reference), and never makes a caller wait longer than its `wait`: the window
load and the EOD read run as shared background tasks that a timed-out caller does not cancel,
so the next request (or the iOS poll) finds the answer in memory.

Phase 2 (owner, 2026-11-13: "approve phase 2 too. finish everything") puts the same answer in
front of money and polling: the two paid report doors and new price alerts REFUSE a confirmed
delisting (`ErrorCode.TICKER_DELISTED`, before any charge), the report pre-warms skip it, and
the price-alert loop pauses its rules (`peek_delisted`, no quote, no state write). Doors that
hold no quote use `get_delisted_for_symbols` with `PAID_DOOR_WAIT`; every unknown still
proceeds exactly as before (fail open).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from app.integrations.fmp import get_fmp_client
from app.integrations.fmp_entitlements import is_blocked_symbol
from app.schemas.listing_status import LISTING_STATUS_DELISTED
from app.services import session_pricing
from app.services.asset_class import uses_coingecko_price
from app.services.price_service import price_source, profile_says_inactive
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# What a screen waits for an answer it does not already hold. Seven surfaces call this on a
# request path (core, full, /stocks/{t}, /quote, tracking, home, chat); a cold load usually
# finishes inside it, and when it does not the load continues for the next request.
REQUEST_WAIT = 1.5

# Phase 2 (owner, 2026-11-13: "approve phase 2 too"): what a door that REFUSES on a
# confirmed delisting waits — the two paid report doors (a 20-credit generation runs 60-90 s,
# so 8 s of checking is small beside it), new price alerts and the report pre-warm. A cold
# window (24 pages, concurrency 4) plus one EOD read normally lands well inside it; when it
# does not, the door proceeds exactly as before this existed (fail open), and the lookup
# finishes in the background for the next request.
PAID_DOOR_WAIT = 8.0

# ── the window (FMP's delisted-companies list, newest first) ──────────────────
_PAGE_LIMIT = 100              # FMP's page cap
_WINDOW_PAGES = 24             # ~8 months of delistings at ~3 pages a month
_HEAD_PAGES = 3                # the head refresh: what changed since the last load
_LOAD_CONCURRENCY = 4
_PAGE_TIMEOUT = 10.0
_HEAD_REFRESH_TTL = 6 * 3600.0
_FULL_RELOAD_TTL = 24 * 3600.0
_WINDOW_MAX_STALE = 3 * 86400.0
_DEGRADED_TTL = 600.0          # after a failed page 0: no new load for 10 min
_MAX_EXCHANGE_CHARS = 40       # a longer "exchange" is not an exchange name

# ── the EOD guard, condition (d) ──────────────────────────────────────────────
_EOD_LOOKBACK_DAYS = 45
_EOD_TTL = 6 * 3600.0
_EOD_FAILURE_TTL = 600.0       # a failed read is retried after 10 min, not per request
_EOD_TIMEOUT = 10.0
_EOD_MEMO_MAX = 512            # candidates are rare; a sweep keeps a pathological day bounded

_WINDOW_KEY = "window"

# Monotonic seconds. A module attribute so a test can move time without sleeping.
_clock = time.monotonic


@dataclass(frozen=True)
class DelistedListing:
    """A CONFIRMED delisting: every D2 condition held."""

    symbol: str
    delisted_date: date               # FMP's delistedDate (a US Eastern calendar date)
    exchange: Optional[str]           # the exchange FMP's row names; None when it has none
    last_trade_date: Optional[date]   # newest EOD session with volume > 0; None when unknown

    def wire(self) -> Dict[str, Optional[str]]:
        """The four response fields (`schemas.listing_status.LISTING_FIELDS`), ISO dates."""
        return {
            "listing_status": LISTING_STATUS_DELISTED,
            "delisted_date": self.delisted_date.isoformat(),
            "delisted_exchange": self.exchange,
            "last_trade_date": self.last_trade_date.isoformat() if self.last_trade_date else None,
        }


@dataclass(frozen=True)
class _Row:
    symbol: str
    delisted_date: date
    exchange: Optional[str]


@dataclass
class _Window:
    rows: Dict[str, Tuple[_Row, ...]]   # per symbol, newest delistedDate first
    loaded_at: float                    # _clock() of the last FULL load
    refreshed_at: float                 # _clock() of the last load of either kind
    partial: bool                       # a full load lost a page after page 0

    def newest_on_or_before(self, sym: str, today: date) -> Optional[_Row]:
        for row in self.rows.get(sym, ()):
            if row.delisted_date <= today:
                return row
        return None


class _Pending:
    """Sentinel: the answer needs I/O (the window or the EOD read is not in memory)."""


_PENDING = _Pending()

_window: Optional[_Window] = None
_degraded_at: Optional[float] = None
# "window" → the one load task; ("eod", SYM, delisted) → an EOD read; ("resolve", SYM, today)
# → one symbol's resolution. Held STRONGLY until done (a bare create_task can be collected).
_inflight: Dict[Any, "asyncio.Task[Any]"] = {}
# (SYM, delisted_date) → (_clock() at read, read ok?, last trade date)
_eod: Dict[Tuple[str, date], Tuple[float, bool, Optional[date]]] = {}
# SYM → (_clock() at confirmation, the listing): `peek_delisted`'s memo.
_confirmed: Dict[str, Tuple[float, DelistedListing]] = {}
# Resolution tasks whose timeout was already logged (one WARNING per resolution, not per wait).
_timeout_logged: Set["asyncio.Task[Any]"] = set()


# ── small helpers ─────────────────────────────────────────────────────────────

def _norm(symbol: Any) -> str:
    return symbol.strip().upper() if isinstance(symbol, str) else ""


def _today_et(now: Optional[datetime]) -> date:
    """Today on the ET calendar. A naive `now` is read as UTC (`market_hours`' convention)."""
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def _as_date(raw: Any) -> Optional[date]:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0


def _exchange(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    return text if 0 < len(text) <= _MAX_EXCHANGE_CHARS else None


def _reads_inactive(quote: Any, profile: Any) -> bool:
    """Condition (a): the quote's flag when a quote is in hand, else the raw profile's."""
    if isinstance(quote, Mapping) and quote:
        return profile_says_inactive(quote if isinstance(quote, dict) else dict(quote))
    return profile_says_inactive(profile)


def _reads_active(quote: Any, profile: Any) -> bool:
    """POSITIVE evidence against condition (a), from the same row `_reads_inactive` reads.

    A quote in hand reads active when it does not carry the inactive flag: `_from_profile`
    emits `isActivelyTrading` ONLY when FMP says False, and the screener universe is
    actively-trading rows only. A raw profile (read only with no quote) must say True — its
    flag is always present, so a missing one is unknown. No row at all is NOT evidence."""
    if isinstance(quote, Mapping) and quote:
        return not profile_says_inactive(quote if isinstance(quote, dict) else dict(quote))
    if not isinstance(profile, Mapping):
        return False
    flag = profile.get("isActivelyTrading")
    return flag is True or (isinstance(flag, str) and flag.strip().lower() == "true")


def _retire_if_active(symbol: Any, *, quote: Any, profile: Any) -> None:
    """Drop `peek_delisted`'s memo for a symbol whose FRESH quote reads active (a relisted or
    reused symbol, or FMP pricing a moved listing again). Without this the memo outlived the
    evidence for up to 6 h while the registry still held day D-1 — the alert create door
    accepted a rule that the list showed "Paused: delisted" and the loop skipped, and both
    report doors refused a ticker that was trading. Sync, no I/O, never raises."""
    sym = _norm(symbol)
    if sym and sym in _confirmed and _reads_active(quote, profile):
        _confirmed.pop(sym, None)
        logger.info(
            "listing_status: %s reads actively trading on a fresh quote — its confirmed "
            "delisting is forgotten (alerts resume, reports are allowed)", sym,
        )


def _candidate(symbol: Any, *, quote: Any, profile: Any, now: Optional[datetime]) -> Optional[str]:
    """The normalised symbol when (b) and (a) hold — sync, no I/O — else None."""
    sym = _norm(symbol)
    if not sym or is_blocked_symbol(sym) or uses_coingecko_price(sym):
        return None
    # (b) FIRST: True (it traded) and None (cold / stale registry) both end it here.
    if session_pricing.priced_in_latest_session(sym, now) is not False:
        return None
    return sym if _reads_inactive(quote, profile) else None


def _track(key: Any, task: "asyncio.Task[Any]") -> "asyncio.Task[Any]":
    _inflight[key] = task

    def _done(t: "asyncio.Task[Any]") -> None:
        # Cleared from the task's OWN completion, so an abandoned waiter never removes a
        # still-running load and lets the next request start a duplicate.
        if _inflight.get(key) is t:
            _inflight.pop(key, None)
        _timeout_logged.discard(t)
        if not t.cancelled():
            t.exception()  # retrieved: every coroutine here logs its own failures

    task.add_done_callback(_done)
    return task


# ── the window ────────────────────────────────────────────────────────────────

def _parse_page(rows: Any) -> Tuple[List[_Row], int]:
    """(rows, malformed count). A malformed row is skipped, never fatal."""
    out: List[_Row] = []
    bad = 0
    for raw in rows if isinstance(rows, list) else ():
        if not isinstance(raw, dict):
            bad += 1
            continue
        sym = _norm(raw.get("symbol"))
        day = _as_date(raw.get("delistedDate"))
        if not sym or day is None:
            bad += 1
            continue
        out.append(_Row(sym, day, _exchange(raw.get("exchange"))))
    return out, bad


def _index(rows: Iterable[_Row]) -> Dict[str, Tuple[_Row, ...]]:
    by_sym: Dict[str, Set[_Row]] = {}
    for row in rows:
        by_sym.setdefault(row.symbol, set()).add(row)
    return {
        sym: tuple(sorted(group, key=lambda r: (r.delisted_date, r.exchange or ""), reverse=True))
        for sym, group in by_sym.items()
    }


def _all_rows(window: Optional[_Window]) -> List[_Row]:
    if window is None:
        return []
    return [row for group in window.rows.values() for row in group]


def _usable_window() -> Optional[_Window]:
    """The held window, or None when there is none or it is past `_WINDOW_MAX_STALE`."""
    global _window
    w = _window
    if w is None:
        return None
    age = _clock() - w.loaded_at
    if age > _WINDOW_MAX_STALE:
        logger.warning(
            "listing_status: the delisted-companies window is %.1f days old — dropped; "
            "no Delisted label until a reload succeeds", age / 86400,
        )
        _window = None
        return None
    return w


def _degraded() -> bool:
    return _degraded_at is not None and _clock() - _degraded_at < _DEGRADED_TTL


def _schedule_load(kind: str, trigger: str, client: Any) -> Optional["asyncio.Task[Any]"]:
    """The ONE window load task (joining a running one), or None while degraded / no loop."""
    running = _inflight.get(_WINDOW_KEY)
    if running is not None:
        return running
    if _degraded():
        return None  # an outage must not become one list walk per request
    try:
        task = asyncio.get_running_loop().create_task(
            _load_window(kind, trigger, client), name=f"listing-status-{kind}-load",
        )
    except RuntimeError:
        return None  # no running loop (a sync caller): the next async caller schedules it
    return _track(_WINDOW_KEY, task)


def _maybe_refresh(window: _Window, trigger: str, client: Any) -> None:
    """Schedule a background reload of a held window that is due one (never awaited)."""
    now = _clock()
    full_age = now - window.loaded_at
    any_age = now - window.refreshed_at
    if full_age > _FULL_RELOAD_TTL or (window.partial and any_age > _DEGRADED_TTL):
        _schedule_load("full", trigger, client)
    elif any_age > _HEAD_REFRESH_TTL:
        _schedule_load("head", trigger, client)


async def _load_window(kind: str, trigger: str, client: Any) -> None:
    """Fetch the window ("full": `_WINDOW_PAGES` pages; "head": the first `_HEAD_PAGES`) and
    install / merge it. Never raises `Exception`: a failed page 0 writes the degraded memo and
    logs a WARNING naming the ticker that asked; a later failed page installs what arrived; an
    unexpected error (a bug) is logged with its stack and degrades the same way."""
    global _degraded_at
    try:
        await _load_window_pages(kind, trigger, client)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the done-callback would otherwise swallow it
        _degraded_at = _clock()
        logger.warning(
            "listing_status: delisted-companies %s load crashed (%s: %s), asked for %s — "
            "no Delisted label from new data for %d min", kind, type(e).__name__, e, trigger,
            int(_DEGRADED_TTL // 60), exc_info=True,
        )


async def _load_window_pages(kind: str, trigger: str, client: Any) -> None:
    global _window, _degraded_at
    pages = list(range(_WINDOW_PAGES if kind == "full" else _HEAD_PAGES))
    started = _clock()
    gate = asyncio.Semaphore(_LOAD_CONCURRENCY)
    page0_failed = asyncio.Event()

    async def fetch(page: int) -> Any:
        async with gate:
            if page0_failed.is_set():
                return None  # page 0 failed: the rest would only spend calls into an outage
            try:
                data = await asyncio.wait_for(
                    client.get_delisted_companies(page, _PAGE_LIMIT), _PAGE_TIMEOUT,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — reported below with the page number
                if page == 0:
                    page0_failed.set()
                return e
            if page == 0 and isinstance(data, list) and not data:
                page0_failed.set()
                return ValueError("page 0 was empty (FMP's list is never empty)")
            return data

    results = await asyncio.gather(*(fetch(p) for p in pages), return_exceptions=True)
    first = results[0]
    if isinstance(first, BaseException) or not isinstance(first, list):
        _degraded_at = _clock()
        held = _window is not None
        logger.warning(
            "listing_status: delisted-companies %s load failed at page 0 (%s: %s), asked "
            "for %s — no Delisted label from new data for %d min (%s)",
            kind, type(first).__name__, first, trigger, int(_DEGRADED_TTL // 60),
            "keeping the held window" if held else "no window held",
        )
        return

    rows: List[_Row] = []
    malformed = 0
    failed: List[str] = []
    for page, result in zip(pages, results):
        if isinstance(result, list):
            parsed, bad = _parse_page(result)
            rows.extend(parsed)
            malformed += bad
        elif result is not None:
            failed.append(f"{page} ({type(result).__name__})")

    now = _clock()
    old = _usable_window()
    if kind == "full" and not failed:
        _window = _Window(rows=_index(rows), loaded_at=started, refreshed_at=now, partial=False)
    elif kind == "full":
        # Partial: merged with what was held, so a lost page never REMOVES coverage, and
        # marked partial so the next request after `_DEGRADED_TTL` reloads it.
        _window = _Window(rows=_index(rows + _all_rows(old)), loaded_at=started,
                          refreshed_at=now, partial=True)
    elif old is not None:
        _window = _Window(rows=_index(rows + _all_rows(old)), loaded_at=old.loaded_at,
                          refreshed_at=now, partial=old.partial or bool(failed))
    else:
        # A head refresh whose window was dropped meanwhile: hold it, due a full load at once.
        _window = _Window(rows=_index(rows), loaded_at=started - _FULL_RELOAD_TTL - 1,
                          refreshed_at=now, partial=True)
    _degraded_at = None
    if failed:
        logger.warning(
            "listing_status: delisted-companies %s load lost page(s) %s, asked for %s — "
            "installed the rest; a delisting on a lost page gets no label until the next load",
            kind, ", ".join(failed), trigger,
        )
    logger.info(
        "listing_status: delisted-companies %s load — %d rows over %d page(s), %d malformed "
        "skipped, %d symbols held, in %.2fs (asked for %s)",
        kind, len(rows), len(pages) - len(failed), malformed,
        len(_window.rows), now - started, trigger,
    )


# ── the EOD guard (d) ─────────────────────────────────────────────────────────

def _last_traded(raw: Any, start: date, today: date) -> Optional[date]:
    """The newest date in [start, today] with volume > 0. Raises ValueError on a body that is
    not rows (FMP's `{"Error Message": …}` on a 200 must never read as "no trade")."""
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict) and isinstance(raw.get("historical"), list):
        rows = raw["historical"]
    else:
        raise ValueError(f"historical-price-eod/full returned a {type(raw).__name__}, not rows")
    best: Optional[date] = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _as_date(row.get("date"))
        if day is None or day < start or day > today or not _positive(row.get("volume")):
            continue
        if best is None or day > best:
            best = day
    return best


def _remember_eod(sym: str, delisted: date, ok: bool, last: Optional[date]) -> None:
    now = _clock()
    if len(_eod) >= _EOD_MEMO_MAX:
        for key in [k for k, (at, good, _) in _eod.items()
                    if now - at > (_EOD_TTL if good else _EOD_FAILURE_TTL)]:
            _eod.pop(key, None)
        while len(_eod) >= _EOD_MEMO_MAX:
            _eod.pop(next(iter(_eod)))          # oldest insertion first
    _eod[(sym, delisted)] = (now, ok, last)


def _eod_memo(sym: str, delisted: date) -> Optional[Tuple[bool, Optional[date]]]:
    hit = _eod.get((sym, delisted))
    if hit is None:
        return None
    at, ok, last = hit
    if _clock() - at > (_EOD_TTL if ok else _EOD_FAILURE_TTL):
        return None
    return ok, last


async def _read_eod(sym: str, delisted: date, today: date, client: Any) -> Tuple[bool, Optional[date]]:
    start = delisted - timedelta(days=_EOD_LOOKBACK_DAYS)
    try:
        raw = await asyncio.wait_for(
            client.get_historical_prices(sym, start.isoformat(), today.isoformat()), _EOD_TIMEOUT,
        )
        last = _last_traded(raw, start, today)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — fail open, loudly
        _remember_eod(sym, delisted, False, None)
        logger.warning(
            "listing_status: %s EOD read failed (%s: %s) — no Delisted label (FMP lists it "
            "delisted %s); retried in %d min", sym, type(e).__name__, e, delisted,
            int(_EOD_FAILURE_TTL // 60),
        )
        return False, None
    _remember_eod(sym, delisted, True, last)
    if last is not None and last > delisted:
        logger.info(
            "listing_status: %s traded on %s, after FMP's delisted row dated %s — a moved or "
            "reused symbol that still trades; no Delisted label", sym, last, delisted,
        )
    return True, last


def _schedule_eod(sym: str, delisted: date, today: date, client: Any) -> "asyncio.Task[Any]":
    key = ("eod", sym, delisted)
    running = _inflight.get(key)
    if running is not None:
        return running
    task = asyncio.get_running_loop().create_task(
        _read_eod(sym, delisted, today, client), name=f"listing-status-eod-{sym}",
    )
    return _track(key, task)


# ── resolution ────────────────────────────────────────────────────────────────

def _decide(sym: str, row: _Row, ok: bool, last: Optional[date]) -> Optional[DelistedListing]:
    if not ok:
        return None                       # the EOD read failed: unknown
    if last is not None and last > row.delisted_date:
        _confirmed.pop(sym, None)         # a re-read contradicts an earlier confirmation
        return None                       # (d): it traded after the row — moved or reused
    listing = DelistedListing(sym, row.delisted_date, row.exchange, last)
    _confirmed[sym] = (_clock(), listing)
    return listing


def _from_memory(sym: str, today: date, client: Any) -> Any:
    """The answer when everything it needs is in memory, else `_PENDING`. Schedules a due
    background refresh of a held window as a side effect; never awaits."""
    window = _usable_window()
    if window is None:
        return None if _degraded() else _PENDING
    _maybe_refresh(window, sym, client)
    row = window.newest_on_or_before(sym, today)
    if row is None:
        return None                       # (c) fails: not on the list, or only a future date
    memo = _eod_memo(sym, row.delisted_date)
    if memo is None:
        return _PENDING
    return _decide(sym, row, *memo)


async def _resolve(sym: str, today: date, client: Any) -> Optional[DelistedListing]:
    """One symbol's answer, awaiting whatever I/O it needs. Never raises `Exception` (the
    task's done-callback retrieves exceptions, so one raised here would vanish unlogged)."""
    try:
        return await _resolve_inner(sym, today, client)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "listing_status: resolving %s crashed (%s: %s) — no Delisted label",
            sym, type(e).__name__, e, exc_info=True,
        )
        return None


async def _resolve_inner(sym: str, today: date, client: Any) -> Optional[DelistedListing]:
    window = _usable_window()
    if window is None:
        load = _schedule_load("full", sym, client)
        if load is None:
            return None
        await asyncio.shield(load)
        window = _usable_window()
        if window is None:
            return None
    else:
        _maybe_refresh(window, sym, client)
    row = window.newest_on_or_before(sym, today)
    if row is None:
        return None
    memo = _eod_memo(sym, row.delisted_date)
    if memo is None:
        memo = await asyncio.shield(_schedule_eod(sym, row.delisted_date, today, client))
    return _decide(sym, row, *memo)


def _resolve_task(sym: str, today: date, client: Any) -> "asyncio.Task[Any]":
    key = ("resolve", sym, today)
    running = _inflight.get(key)
    if running is not None:
        return running
    task = asyncio.get_running_loop().create_task(
        _resolve(sym, today, client), name=f"listing-status-resolve-{sym}",
    )
    return _track(key, task)


def _budget(wait: Any) -> float:
    """`wait` as a usable number of seconds: unreadable → `REQUEST_WAIT`, negative/NaN → 0."""
    try:
        budget = float(wait)
    except (TypeError, ValueError):
        budget = REQUEST_WAIT
    if not math.isfinite(budget) or budget < 0:
        budget = 0.0
    return budget


async def _await_bounded(
    tasks: Mapping[str, "asyncio.Task[Any]"], wait: Any,
    *, outcome: str = "answered without a Delisted label this time",
) -> Dict[str, Optional[DelistedListing]]:
    """Each task's answer, or None for one still running after `wait` seconds — which keeps
    running (`asyncio.wait` never cancels), so the next request finds it in memory.
    `outcome` says, in the timeout WARNING, what the caller does without the answer."""
    if not tasks:
        return {}
    budget = _budget(wait)
    done, _ = await asyncio.wait(set(tasks.values()), timeout=budget)
    out: Dict[str, Optional[DelistedListing]] = {}
    for sym, task in tasks.items():
        if task in done:
            if task.cancelled() or task.exception() is not None:
                out[sym] = None           # its own coroutine already logged why
            else:
                out[sym] = task.result()
            continue
        out[sym] = None
        if task not in _timeout_logged:
            _timeout_logged.add(task)
            # A caller that asked not to wait (`wait=0`) learns in the background by design.
            logger.log(
                logging.WARNING if budget > 0 else logging.INFO,
                "listing_status: %s still resolving after %.1fs — %s; the lookup continues "
                "in the background", sym, budget, outcome,
            )
    return out


# ── public API ────────────────────────────────────────────────────────────────

async def get_delisted(
    symbol: Any,
    *,
    quote: Any = None,
    profile: Any = None,
    now: Optional[datetime] = None,
    wait: float = REQUEST_WAIT,
    fmp: Any = None,
) -> Optional[DelistedListing]:
    """The confirmed delisting of `symbol`, or None (trading OR unknown — fail open).

    `quote` is the quote the caller already holds (its `isActivelyTrading` flag is condition
    (a)); `profile` is read only when no quote is in hand. `fmp` overrides the client (a caller
    with its own binding, e.g. `unpriced_holdings_service`). Never raises; never waits longer
    than `wait` seconds.
    """
    try:
        sym = _candidate(symbol, quote=quote, profile=profile, now=now)
        if sym is None:
            _retire_if_active(symbol, quote=quote, profile=profile)
            return None
        today = _today_et(now)
        client = fmp if fmp is not None else get_fmp_client()
        answer = _from_memory(sym, today, client)
        if answer is not _PENDING:
            return answer
        return (await _await_bounded({sym: _resolve_task(sym, today, client)}, wait)).get(sym)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — a label is never worth a failed screen
        logger.warning(
            "listing_status: lookup for %r failed (%s: %s) — no Delisted label",
            symbol, type(e).__name__, e, exc_info=True,
        )
        return None


async def get_delisted_many(
    quotes: Any,
    *,
    now: Optional[datetime] = None,
    wait: float = REQUEST_WAIT,
    fmp: Any = None,
) -> Dict[str, DelistedListing]:
    """`{SYMBOL: listing}` for every confirmed delisting among `quotes` (`{symbol: quote}`,
    the map a feed builder already holds). One shared `wait` for the whole batch; symbols
    absent from the result are trading or unknown. Never raises."""
    out: Dict[str, DelistedListing] = {}
    try:
        if not isinstance(quotes, Mapping):
            return out
        today = _today_et(now)
        client: Any = None
        pending: Dict[str, "asyncio.Task[Any]"] = {}
        for key, quote in quotes.items():
            if not isinstance(quote, Mapping):
                continue
            raw_sym = key if _norm(key) else quote.get("symbol")
            sym = _candidate(raw_sym, quote=quote, profile=None, now=now)
            if sym is None:
                _retire_if_active(raw_sym, quote=quote, profile=None)
                continue
            if sym in out or sym in pending:
                continue
            if client is None:
                client = fmp if fmp is not None else get_fmp_client()
            answer = _from_memory(sym, today, client)
            if answer is _PENDING:
                pending[sym] = _resolve_task(sym, today, client)
            elif answer is not None:
                out[sym] = answer
        for sym, listing in (await _await_bounded(pending, wait)).items():
            if listing is not None:
                out[sym] = listing
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "listing_status: batch lookup failed (%s: %s) — %d confirmed kept, the rest "
            "unlabelled", type(e).__name__, e, len(out), exc_info=True,
        )
    return out


def peek_delisted(symbol: Any, now: Optional[datetime] = None) -> Optional[DelistedListing]:
    """Sync: a delisting confirmed within the last 6 h WHILE the registry still says the
    symbol did not trade (a relisted or reused symbol un-pauses at the next publish, or as
    soon as any caller passes a fresh quote that reads active — `_retire_if_active`). No I/O,
    no scheduling. For callers that must not wait (Phase 2: price-alert evaluation)."""
    sym = _norm(symbol)
    hit = _confirmed.get(sym)
    if not sym or hit is None:
        return None
    at, listing = hit
    if _clock() - at > _EOD_TTL:
        return None
    if session_pricing.priced_in_latest_session(sym, now) is not False:
        return None
    return listing


def _symbols(symbols: Any) -> List[str]:
    """Normalised, de-duplicated, in order. A bare string is ONE symbol, never its letters."""
    if isinstance(symbols, str):
        symbols = [symbols]
    out: List[str] = []
    try:
        for raw in symbols or ():
            sym = _norm(raw)
            if sym and sym not in out:
                out.append(sym)
    except TypeError:
        return []
    return out


async def get_delisted_for_symbols(
    symbols: Any,
    *,
    now: Optional[datetime] = None,
    wait: float = PAID_DOOR_WAIT,
    purpose: str = "a caller with no quote",
    unknown_level: int = logging.WARNING,
    fmp: Any = None,
) -> Dict[str, DelistedListing]:
    """`{SYMBOL: listing}` for every CONFIRMED delisting among `symbols`, for a caller that
    holds NO quote (Phase 2: the paid report doors, the report pre-warm, the price-alert list).

    Cost, cheapest first, per symbol:
      * registry True (it traded in the latest session — every live ticker) → nothing at all;
      * registry None (cold ~2 min after a restart, or stale) → unknown: logged at
        `unknown_level` naming the symbol and `purpose`, no I/O, absent from the result;
      * a delisting `peek_delisted` already holds → answered from memory, no I/O;
      * registry exactly False → ONE `price_source().get_quote` (its 30 s memo, usually warm
        from the screen the user just left), then the D2 rule as `get_delisted_many` runs it.
    The whole call is bounded by `wait`: a quote or a resolution still running then is
    unknown (WARNING with the symbol — the resolution itself continues in the background).
    Never raises; a symbol absent from the result means trading OR unknown — proceed.
    """
    out: Dict[str, DelistedListing] = {}
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _budget(wait)
        need_quote: List[str] = []
        for sym in _symbols(symbols):
            if is_blocked_symbol(sym) or uses_coingecko_price(sym):
                continue
            traded = session_pricing.priced_in_latest_session(sym, now)
            if traded is True:
                continue
            if traded is None:
                logger.log(
                    unknown_level,
                    "listing_status: %s listing status unknown for %s (the latest-session "
                    "price registry is cold or stale) — proceeding as listed", sym, purpose,
                )
                continue
            held = peek_delisted(sym, now)
            if held is not None:
                out[sym] = held
                continue
            need_quote.append(sym)
        if not need_quote:
            return out

        source = price_source()
        fetches = {sym: loop.create_task(source.get_quote(sym), name=f"listing-status-quote-{sym}")
                   for sym in need_quote}
        try:
            done, _ = await asyncio.wait(set(fetches.values()),
                                         timeout=max(0.0, deadline - loop.time()))
        finally:
            for task in fetches.values():
                if not task.done():
                    task.cancel()         # bounded: a slow profile read is not waited for
        quotes: Dict[str, Any] = {}
        for sym, task in fetches.items():
            if task not in done:
                logger.warning(
                    "listing_status: %s quote for %s did not arrive within %.1fs — listing "
                    "status unknown, proceeding as listed", sym, purpose, _budget(wait),
                )
                continue
            exc = None if task.cancelled() else task.exception()
            quote = None if task.cancelled() or exc is not None else task.result()
            if not isinstance(quote, Mapping) or not quote:
                logger.log(
                    unknown_level,
                    "listing_status: %s has no quote for %s (%s) — listing status unknown, "
                    "proceeding as listed", sym, purpose,
                    f"{type(exc).__name__}: {exc}" if exc is not None else "empty answer",
                )
                continue
            quotes[sym] = quote
        if not quotes:
            return out

        today = _today_et(now)
        client = fmp if fmp is not None else get_fmp_client()
        pending: Dict[str, "asyncio.Task[Any]"] = {}
        for sym, quote in quotes.items():
            cand = _candidate(sym, quote=quote, profile=None, now=now)
            if cand is None:
                _retire_if_active(sym, quote=quote, profile=None)
                continue                  # its fresh quote reads active: decided, not delisted
            answer = _from_memory(cand, today, client)
            if answer is _PENDING:
                pending[cand] = _resolve_task(cand, today, client)
            elif answer is not None:
                out[cand] = answer
        answers = await _await_bounded(
            pending, max(0.0, deadline - loop.time()),
            outcome=f"{purpose} proceeds as listed (fail open)",
        )
        for sym, listing in answers.items():
            if listing is not None:
                out[sym] = listing
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — a refusal is never worth a failed door
        logger.warning(
            "listing_status: lookup for %s failed (%s: %s) — %d confirmed kept, the rest "
            "proceed as listed", purpose, type(e).__name__, e, len(out), exc_info=True,
        )
    return out


def kick_window_load(trigger: str, *, fmp: Any = None) -> None:
    """Pre-warm: schedule the window load in the background when none is usable (or a held one
    is due a refresh). Never awaits, never raises. `unpriced_holdings_service` calls it for
    held symbols FMP stopped pricing, so the first screen that shows one finds the window."""
    try:
        client = fmp if fmp is not None else get_fmp_client()
        window = _usable_window()
        if window is None:
            _schedule_load("full", _norm(trigger) or "pre-warm", client)
        else:
            _maybe_refresh(window, _norm(trigger) or "pre-warm", client)
    except Exception as e:  # noqa: BLE001
        logger.warning("listing_status: pre-warm for %r failed (%s: %s)",
                       trigger, type(e).__name__, e)


async def delisted_row(
    symbol: Any, *, now: Optional[datetime] = None, wait: float = REQUEST_WAIT, fmp: Any = None,
) -> Optional[Tuple[date, Optional[str]]]:
    """`(delistedDate, exchange)` of FMP's newest row for `symbol` dated on or before today
    ET, or None (not listed, or the window is not available within `wait`). Condition (c)
    ALONE — no registry, flag or EOD check — so it is a hint for a log line (the unpriced-
    holdings ERROR), never a label. Never raises."""
    try:
        sym = _norm(symbol)
        if not sym:
            return None
        today = _today_et(now)
        client = fmp if fmp is not None else get_fmp_client()
        window = _usable_window()
        if window is None:
            load = _schedule_load("full", sym, client)
            if load is None:
                return None
            done, _ = await asyncio.wait({load}, timeout=max(0.0, float(wait)))
            if load not in done:
                return None
            window = _usable_window()
            if window is None:
                return None
        row = window.newest_on_or_before(sym, today)
        return (row.delisted_date, row.exchange) if row is not None else None
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("listing_status: row lookup for %r failed (%s: %s)",
                       symbol, type(e).__name__, e)
        return None


def _reset_for_tests() -> None:
    global _window, _degraded_at
    for task in list(_inflight.values()):
        try:
            task.cancel()
        except RuntimeError:
            pass  # its event loop is already closed; nothing left to cancel
    _inflight.clear()
    _eod.clear()
    _confirmed.clear()
    _timeout_logged.clear()
    _window = None
    _degraded_at = None
