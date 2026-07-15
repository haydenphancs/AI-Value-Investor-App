"""Guards for live-price previous_close refresh (LivePriceManager).

previous_close is fetched once at room creation. A transient failure / missing
`previousClose` used to pin it to 0.0 for the room's lifetime, freezing
change/change_percent at +0.00%. The reader loop now lazily re-fetches it
(throttled) when it's missing or stale across a trading-day boundary. These
tests pin the decision logic without hitting the network (the fetch is stubbed).
"""
from __future__ import annotations

import time

import pytest

from app.services.live_price_manager import LivePriceManager, TickerRoom


def _stub_fetch(calls, *, sets=None):
    async def fake_fetch(room):
        calls.append(room.ticker)
        room.last_prev_close_attempt = time.monotonic()
        if sets is not None:
            room.previous_close = sets
            room.previous_close_epoch_day = int(time.time()) // 86400
    return fake_fetch


@pytest.mark.asyncio
async def test_refetches_when_previous_close_missing():
    mgr = LivePriceManager()
    room = TickerRoom(ticker="AAPL")  # previous_close defaults to 0.0
    calls = []
    mgr._fetch_previous_close = _stub_fetch(calls, sets=150.0)  # type: ignore[method-assign]

    await mgr._ensure_previous_close(room, tick_epoch=int(time.time()))

    assert calls == ["AAPL"]           # missing → re-fetched
    assert room.previous_close == 150.0


@pytest.mark.asyncio
async def test_throttles_repeat_attempts_within_window():
    mgr = LivePriceManager()
    room = TickerRoom(ticker="AAPL")   # still 0.0 (needs), but just attempted
    room.last_prev_close_attempt = time.monotonic()
    calls = []
    mgr._fetch_previous_close = _stub_fetch(calls)  # type: ignore[method-assign]

    await mgr._ensure_previous_close(room, tick_epoch=int(time.time()))

    assert calls == []                 # throttled — no FMP hammering per tick


@pytest.mark.asyncio
async def test_no_refetch_when_valid_and_same_day():
    mgr = LivePriceManager()
    room = TickerRoom(ticker="AAPL")
    room.previous_close = 150.0
    room.previous_close_epoch_day = int(time.time()) // 86400
    room.last_prev_close_attempt = 0.0  # long ago → not throttled
    calls = []
    mgr._fetch_previous_close = _stub_fetch(calls)  # type: ignore[method-assign]

    await mgr._ensure_previous_close(room, tick_epoch=int(time.time()))

    assert calls == []                 # valid + same day → nothing to do


@pytest.mark.asyncio
async def test_refetches_on_new_trading_day():
    mgr = LivePriceManager()
    room = TickerRoom(ticker="AAPL")
    room.previous_close = 150.0
    room.previous_close_epoch_day = (int(time.time()) // 86400) - 1  # fetched "yesterday"
    room.last_prev_close_attempt = 0.0
    calls = []
    mgr._fetch_previous_close = _stub_fetch(calls, sets=155.0)  # type: ignore[method-assign]

    await mgr._ensure_previous_close(room, tick_epoch=int(time.time()))  # today's tick

    assert calls == ["AAPL"]           # crossed day boundary → refresh reference close


@pytest.mark.asyncio
async def test_millisecond_timestamp_not_seen_as_new_day():
    mgr = LivePriceManager()
    room = TickerRoom(ticker="AAPL")
    room.previous_close = 150.0
    room.previous_close_epoch_day = int(time.time()) // 86400  # today
    room.last_prev_close_attempt = 0.0
    calls = []
    mgr._fetch_previous_close = _stub_fetch(calls)  # type: ignore[method-assign]

    # Same calendar day, but the tick timestamp is in MILLISECONDS.
    await mgr._ensure_previous_close(room, tick_epoch=int(time.time()) * 1000)

    assert calls == []                 # normalized ms → same day → no wasteful refetch
