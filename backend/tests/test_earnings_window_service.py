"""`earnings_window_service` — the one-call-per-ET-day lookup behind the cap boost.

Everything here is hermetic: the service takes its FMP client as a keyword argument
and the tests hand it a fake. The autouse fixture resets the process singleton before
AND after each test, because the run_sweep-driving stubs elsewhere in the suite reach
the real service with a client that lacks the method (populating its negative TTL
with the real clock).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import earnings_window_service as ews
from app.services.earnings_window_service import (
    EARNINGS_WINDOW_DAYS_AHEAD,
    EARNINGS_WINDOW_DAYS_BACK,
    EarningsWindowService,
    earnings_window_bounds,
    et_date,
    get_earnings_window_service,
    parse_earnings_window,
    symbols_in_earnings_window,
)

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 11)                         # ORCL's D+2 (report 2026-09-09)
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)   # 12:00 ET


@pytest.fixture(autouse=True)
def _fresh_singleton():
    get_earnings_window_service().reset()
    yield
    get_earnings_window_service().reset()


class _Fake:
    def __init__(self, rows=None, raises=None, delay=0.0):
        self.rows = [] if rows is None else rows
        self.raises = raises
        self.delay = delay
        self.calls = []

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls.append((from_date, to_date))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.rows


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_window_bounds_are_today_minus_2_to_plus_1():
    assert (EARNINGS_WINDOW_DAYS_BACK, EARNINGS_WINDOW_DAYS_AHEAD) == (2, 1)
    assert earnings_window_bounds(TODAY) == (date(2026, 9, 9), date(2026, 9, 12))


@pytest.mark.parametrize(
    "today,boosted",
    [
        (date(2026, 9, 8), True),     # D-1: previews
        (date(2026, 9, 9), True),     # D
        (date(2026, 9, 10), True),    # D+1: the reaction session
        (date(2026, 9, 11), True),    # D+2: the TestFlight screenshot
        (date(2026, 9, 12), False),   # D+3
        (date(2026, 9, 7), False),    # D-2
    ],
)
def test_membership_covers_d_minus_1_through_d_plus_2(today, boosted):
    rows = [{"symbol": "ORCL", "date": "2026-09-09", "time": "amc"}]
    assert ("ORCL" in parse_earnings_window(rows, today)) is boosted


def test_symbols_are_upper_cased_and_stripped():
    rows = [{"symbol": " orcl ", "date": "2026-09-10"}]
    assert parse_earnings_window(rows, TODAY) == frozenset({"ORCL"})


@pytest.mark.parametrize(
    "row",
    [
        None, "ORCL", 42, [],
        {"symbol": "", "date": "2026-09-10"},
        {"symbol": None, "date": "2026-09-10"},
        {"date": "2026-09-10"},
        {"symbol": "ORCL"},
        {"symbol": "ORCL", "date": None},
        {"symbol": "ORCL", "date": ""},
        {"symbol": "ORCL", "date": "garbage"},
        {"symbol": "ORCL", "date": "2026/09/10"},
        {"symbol": "ORCL", "date": "2026-13-45"},
        {"symbol": "ORCL", "date": 20260910},
    ],
)
def test_malformed_rows_are_skipped_not_raised(row):
    assert parse_earnings_window([row], TODAY) == frozenset()


def test_a_full_timestamp_date_is_read_by_its_day():
    rows = [{"symbol": "ORCL", "date": "2026-09-10 16:00:00"}]
    assert parse_earnings_window(rows, TODAY) == frozenset({"ORCL"})


def test_a_non_list_body_yields_nothing():
    assert parse_earnings_window({"symbol": "ORCL", "date": "2026-09-10"}, TODAY) == frozenset()
    assert parse_earnings_window(None, TODAY) == frozenset()


def test_rows_outside_the_window_are_dropped_even_if_upstream_sent_them():
    rows = [
        {"symbol": "EARLY", "date": "2026-09-08"},
        {"symbol": "IN", "date": "2026-09-09"},
        {"symbol": "LATE", "date": "2026-09-13"},
    ]
    assert parse_earnings_window(rows, TODAY) == frozenset({"IN"})


def test_et_date_reads_a_naive_now_as_utc():
    # 03:00Z on the 12th is still the 11th in New York.
    assert et_date(datetime(2026, 9, 12, 3, 0)) == date(2026, 9, 11)
    assert et_date(datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)) == date(2026, 9, 11)


# ── the service ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_market_wide_call_per_et_day():
    fake = _Fake([{"symbol": "ORCL", "date": "2026-09-09"}])
    svc = EarningsWindowService()
    a = await svc.symbols_in_window(NOW, fmp=fake)
    b = await svc.symbols_in_window(NOW + timedelta(hours=3), fmp=fake)
    assert a == b == frozenset({"ORCL"})
    assert fake.calls == [("2026-09-09", "2026-09-12")]


@pytest.mark.asyncio
async def test_an_empty_calendar_is_a_successful_empty_day():
    fake = _Fake([])
    svc = EarningsWindowService()
    assert await svc.symbols_in_window(NOW, fmp=fake) == frozenset()
    assert await svc.symbols_in_window(NOW + timedelta(hours=5), fmp=fake) == frozenset()
    assert len(fake.calls) == 1, "an empty day must be cached, not refetched every sweep"


@pytest.mark.asyncio
async def test_a_non_list_response_is_a_failure_with_a_negative_ttl(caplog):
    fake = _Fake({"error": "quota"})
    svc = EarningsWindowService()
    with caplog.at_level(logging.WARNING):
        assert await svc.symbols_in_window(NOW, fmp=fake) == frozenset()
    assert "no ticker is boosted" in caplog.text
    # Inside the retry window: served from the negative cache, no call.
    assert await svc.symbols_in_window(NOW + timedelta(minutes=5), fmp=fake) == frozenset()
    assert len(fake.calls) == 1
    # Past it: retried.
    fake.rows = [{"symbol": "ORCL", "date": "2026-09-09"}]
    assert await svc.symbols_in_window(NOW + timedelta(minutes=16), fmp=fake) == frozenset({"ORCL"})
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_a_raising_client_degrades_to_empty_and_logs(caplog):
    fake = _Fake(raises=RuntimeError("FMP 503"))
    svc = EarningsWindowService()
    with caplog.at_level(logging.WARNING):
        assert await svc.symbols_in_window(NOW, fmp=fake) == frozenset()
    assert "RuntimeError: FMP 503" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [None, object()])
async def test_a_client_without_the_method_degrades_to_empty(client, caplog):
    """The run_sweep stubs elsewhere in the suite carry `self.fmp = None`; this is
    the path that keeps them hermetic."""
    svc = EarningsWindowService()
    with caplog.at_level(logging.WARNING):
        assert await svc.symbols_in_window(NOW, fmp=client) == frozenset()
    assert "get_earnings_calendar" in caplog.text


@pytest.mark.asyncio
async def test_et_day_rollover_refetches_with_the_new_window():
    """23:59 ET and 00:01 ET are the SAME UTC date — proves the cache is keyed on ET."""
    fake = _Fake([])
    svc = EarningsWindowService()
    late = datetime(2026, 9, 11, 23, 59, tzinfo=ET)
    early = datetime(2026, 9, 12, 0, 1, tzinfo=ET)
    assert late.astimezone(timezone.utc).date() == early.astimezone(timezone.utc).date()
    await svc.symbols_in_window(late, fmp=fake)
    await svc.symbols_in_window(early, fmp=fake)
    assert fake.calls == [("2026-09-09", "2026-09-12"), ("2026-09-10", "2026-09-13")]


@pytest.mark.asyncio
async def test_a_naive_now_is_read_as_utc():
    fake = _Fake([])
    svc = EarningsWindowService()
    await svc.symbols_in_window(datetime(2026, 9, 12, 3, 0), fmp=fake)   # 11th in ET
    assert fake.calls == [("2026-09-09", "2026-09-12")]


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_fetch():
    fake = _Fake([{"symbol": "ORCL", "date": "2026-09-09"}], delay=0.02)
    svc = EarningsWindowService()
    results = await asyncio.gather(*[svc.symbols_in_window(NOW, fmp=fake) for _ in range(5)])
    assert all(r == frozenset({"ORCL"}) for r in results)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_a_failure_is_shared_with_joiners_as_empty_not_raised():
    fake = _Fake(raises=RuntimeError("boom"), delay=0.02)
    svc = EarningsWindowService()
    results = await asyncio.gather(*[svc.symbols_in_window(NOW, fmp=fake) for _ in range(3)])
    assert results == [frozenset()] * 3


@pytest.mark.asyncio
async def test_a_cancelled_leader_leaves_no_stuck_inflight():
    fake = _Fake([], delay=1.0)
    svc = EarningsWindowService()
    task = asyncio.create_task(svc.symbols_in_window(NOW, fmp=fake))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert svc._inflight == {}
    fake.delay = 0
    assert await svc.symbols_in_window(NOW, fmp=fake) == frozenset()


@pytest.mark.asyncio
async def test_the_module_entry_point_uses_the_singleton():
    fake = _Fake([{"symbol": "PLUG", "date": "2026-09-10"}])
    assert await symbols_in_earnings_window(NOW, fmp=fake) == frozenset({"PLUG"})
    assert get_earnings_window_service()._day == TODAY
    assert ews._service is get_earnings_window_service()


@pytest.mark.asyncio
async def test_a_cancelled_joiner_does_not_cancel_the_leaders_fetch():
    """The runtime proof behind the shield: a joiner that gives up must not cancel
    the shared future out from under the leader and the other joiners."""
    fake = _Fake([{"symbol": "ORCL", "date": "2026-09-09"}], delay=0.05)
    svc = EarningsWindowService()
    leader = asyncio.create_task(svc.symbols_in_window(NOW, fmp=fake))
    await asyncio.sleep(0.005)
    joiner_a = asyncio.create_task(svc.symbols_in_window(NOW, fmp=fake))
    joiner_b = asyncio.create_task(svc.symbols_in_window(NOW, fmp=fake))
    await asyncio.sleep(0.005)
    joiner_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await joiner_a
    assert await leader == frozenset({"ORCL"})
    assert await joiner_b == frozenset({"ORCL"})
    assert len(fake.calls) == 1
    assert svc._inflight == {}
