"""Which tickers are inside their EARNINGS WINDOW today — for the Insights sweeper.

WHY THIS EXISTS — TestFlight, ORCL, 2026-09-11 (two days after its report): the
Insights card read "Updated 2 hours ago · checking for updates" over eight fresh
sources. The sweeper had checked ORCL every five minutes; what it lacked was
allowance. The per-ticker daily regeneration cap had been spent by 10:46 ET, and
the earnings week is exactly when a ticker's wire looks like the market's. So a
ticker whose earnings date falls within the window below gets the MARKET-sized
cap (`updates_materiality.daily_cap_for(..., earnings_window=True)`), instead of
the whole universe getting a bigger cap it does not need.

Cost: ONE licensed, market-wide `/stable/earnings-calendar` call per ET day (the
same call `notification_senders/earnings_sender.py` makes once a day), cached in
process. No per-ticker FMP calls. Membership is a pure function of that response.

Failure is fail-SAFE, never fail-open: any problem (client missing the method,
upstream error, non-list body) yields an EMPTY set — nobody is boosted, the
ordinary cap applies — plus a warning and a short negative TTL so a transient
failure is retried within the same day instead of silently costing the whole
day's boost.

⚠️ `fmp` is keyword-only and has NO default on purpose. The sweeper passes its
own client (`self.fmp`), which in the test-suite stubs is `None` or a stub
without `get_earnings_calendar`. Reaching for `get_fmp_client()` here instead
would make every existing `run_sweep` test attempt a real HTTP call, and the
hermetic guard in `conftest.py` fails the WHOLE session on the first one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Optional, Tuple

from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# A report on day D boosts the ticker from D-1 (previews, positioning) through D+2
# (the reaction session and the follow-up analysis). ORCL's screenshot was D+2.
# Expressed as how far the fetched window reaches BACK and AHEAD of today, so the
# calendar call and the membership test share one definition.
EARNINGS_WINDOW_DAYS_BACK = 2
EARNINGS_WINDOW_DAYS_AHEAD = 1

# After a failed fetch, how long to serve "nobody is boosted" before trying again.
# Short, because a failure here quietly costs the boost; keyed on the injected
# `now` (not a monotonic clock) so it is testable without sleeping.
_FAILURE_RETRY_SECONDS = 15 * 60


def et_date(now: datetime) -> date:
    """The ET calendar date of ``now``. A naive ``now`` is read as UTC."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def earnings_window_bounds(today: date) -> Tuple[date, date]:
    """``(from, to)`` — the inclusive calendar span fetched and tested for ``today``."""
    return (
        today - timedelta(days=EARNINGS_WINDOW_DAYS_BACK),
        today + timedelta(days=EARNINGS_WINDOW_DAYS_AHEAD),
    )


def _row_date(row: Any) -> Optional[date]:
    """The row's ``date`` as a `date`, or None when it cannot be trusted.

    FMP occasionally returns a full timestamp (``"2026-09-21 16:00:00"``) or an
    empty string. Slicing to 10 characters normalises the first; anything that
    is not then a strict ``YYYY-MM-DD`` is rejected rather than guessed.
    """
    raw = str(row.get("date") or "")[:10]
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_earnings_window(rows: Any, today: date) -> FrozenSet[str]:
    """PURE. The upper-cased symbols whose earnings date lies inside today's window.

    Skips anything that is not a dict, has no symbol, or has a date that does not
    parse. A row OUTSIDE the window is dropped even though the fetch was bounded
    by the same dates — the bounds are re-applied here so a lenient upstream
    (or a cached response from an earlier ``today``) can never widen the boost.
    """
    if not isinstance(rows, list):
        return frozenset()
    from_d, to_d = earnings_window_bounds(today)
    out = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        d = _row_date(row)
        if d is None or d < from_d or d > to_d:
            continue
        out.add(symbol)
    return frozenset(out)


class EarningsWindowService:
    """One calendar fetch per ET day, shared by every caller in the process."""

    def __init__(self) -> None:
        self._day: Optional[date] = None
        self._symbols: FrozenSet[str] = frozenset()
        self._failed_until: Optional[datetime] = None
        # Keyed by the ET date being fetched — the same shape as every other
        # `_inflight` map in this codebase, so the cancellation-safety guards in
        # tests/test_inflight_cancellation_safety.py recognise the join below.
        self._inflight: Dict[date, asyncio.Future] = {}

    def reset(self) -> None:
        """Forget everything (tests)."""
        self._day = None
        self._symbols = frozenset()
        self._failed_until = None
        self._inflight = {}

    async def symbols_in_window(self, now: datetime, *, fmp: Any) -> FrozenSet[str]:
        """The tickers in their earnings window on ``now``'s ET date.

        Never raises. See the module docstring for the failure policy and for why
        ``fmp`` must be supplied by the caller.
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        today = et_date(now)
        if self._day == today:
            return self._symbols
        if self._failed_until is not None and now < self._failed_until:
            return frozenset()

        inflight = self._inflight.get(today)
        if inflight is not None:
            # Shielded, as in news_cache_service._deduped: a cancelled joiner must
            # not cancel the fetch the leader is about to publish.
            try:
                return await asyncio.shield(inflight)
            except asyncio.CancelledError:
                if inflight.cancelled():
                    # The LEADER was cancelled (shutdown mid-fetch). Degrade like
                    # any other failure; the joiner itself was not cancelled.
                    return frozenset()
                raise

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[today] = fut
        try:
            result = await self._fetch(now, today, fmp)
            if not fut.done():
                fut.set_result(result)
            return result
        except Exception as e:
            # `_fetch` already degrades every expected failure; this is the
            # defensive net for the unexpected — same policy, logged. Joiners get
            # the same empty answer rather than an exception.
            logger.warning(
                "Earnings window lookup raised (%s: %s) — no ticker is boosted "
                "until the next attempt", type(e).__name__, e,
            )
            self._failed_until = now + timedelta(seconds=_FAILURE_RETRY_SECONDS)
            if not fut.done():
                fut.set_result(frozenset())
            return frozenset()
        except BaseException:
            # Cancellation must still resolve the future or every joiner hangs.
            if not fut.done():
                fut.cancel()
            raise
        finally:
            self._inflight.pop(today, None)

    async def _fetch(self, now: datetime, today: date, fmp: Any) -> FrozenSet[str]:
        getter = getattr(fmp, "get_earnings_calendar", None)
        if getter is None:
            self._note_failure(now, "no FMP client with get_earnings_calendar")
            return frozenset()
        from_d, to_d = earnings_window_bounds(today)
        try:
            rows = await getter(from_date=from_d.isoformat(), to_date=to_d.isoformat())
        except Exception as e:
            self._note_failure(now, f"{type(e).__name__}: {e}")
            return frozenset()
        if not isinstance(rows, list):
            self._note_failure(now, f"calendar returned {type(rows).__name__}, not a list")
            return frozenset()
        # `[]` is a real answer (a holiday, or a genuinely quiet stretch) and is
        # cached for the day like any other — refetching an empty calendar every
        # sweep would be pure waste.
        symbols = parse_earnings_window(rows, today)
        self._day = today
        self._symbols = symbols
        self._failed_until = None
        logger.info(
            "Earnings window %s..%s: %d ticker(s) boosted for %s",
            from_d.isoformat(), to_d.isoformat(), len(symbols), today.isoformat(),
        )
        return symbols

    def _note_failure(self, now: datetime, why: str) -> None:
        self._failed_until = now + timedelta(seconds=_FAILURE_RETRY_SECONDS)
        logger.warning(
            "Earnings window unavailable (%s) — no ticker is boosted for the next "
            "%d min", why, _FAILURE_RETRY_SECONDS // 60,
        )


_service: Optional[EarningsWindowService] = None


def get_earnings_window_service() -> EarningsWindowService:
    global _service
    if _service is None:
        _service = EarningsWindowService()
    return _service


async def symbols_in_earnings_window(now: datetime, *, fmp: Any) -> FrozenSet[str]:
    """Module-level entry point (the sweeper imports this name so tests can patch it)."""
    return await get_earnings_window_service().symbols_in_window(now, fmp=fmp)
