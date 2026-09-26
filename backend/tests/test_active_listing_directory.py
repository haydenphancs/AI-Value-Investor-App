"""
The active-listing directory behind ticker search's liveness rule
(`stock_search_service.get_active_listings` / `refresh_active_listings`).

It sits on the hottest route in the app — a debounced keystroke from six iOS search
surfaces — so the contract is about what it must NEVER do:

  • never make a request wait for FMP (a cold directory returns None, the rules that
    need it are skipped, one background refresh is scheduled);
  • never start more than one 70k-row fetch at a time, and never one per keystroke
    during an outage (60 s degraded memo);
  • never install a truncated or error payload as the whitelist — it would hide every
    live company (absolute floors, plus 80% of a still-usable last good copy);
  • never latch off: the relative floor ignores an expired copy, and a filter that keeps
    failing escalates to ERROR so it is visible.

Hermetic: FMP is a fake client patched onto `stock_search_service.get_fmp_client`.
conftest resets the module's cache and cancels any leftover refresh between tests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List

import pytest

from app.integrations.fmp import FMPClient, FMPUnavailableException
from app.services import stock_search_service as svc

LOGGER = "app.services.stock_search_service"


def _payload(us: int = 30_000, foreign: int = 25_000) -> List[dict]:
    rows = [{"symbol": f"S{i}", "name": f"Company {i} Inc."} for i in range(us)]
    rows += [{"symbol": f"F{i}.TO", "name": f"Foreign {i}"} for i in range(foreign)]
    return rows


class _Fake:
    def __init__(self, result: Any = None, exc: BaseException = None, delay: float = 0.0):
        self.result = _payload() if result is None and exc is None else result
        self.exc = exc
        self.delay = delay
        self.calls = 0

    async def get_actively_trading_list(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result


def _use(monkeypatch, fake: _Fake) -> _Fake:
    monkeypatch.setattr(svc, "get_fmp_client", lambda: fake)
    return fake


async def _drain() -> None:
    task = svc._inflight.get(svc._DIRECTORY_KEY)
    if task is not None:
        await task


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_cold_call_returns_none_immediately_and_schedules_one_refresh(monkeypatch):
    fake = _use(monkeypatch, _Fake())
    assert svc.get_active_listings() is None, "a keystroke never waits for FMP"
    assert svc._DIRECTORY_KEY in svc._inflight
    await _drain()
    directory = svc.get_active_listings()
    assert directory is not None and len(directory) == 30_000
    assert fake.calls == 1
    assert svc._DIRECTORY_KEY not in svc._inflight, "cleared by the task's own completion"


@pytest.mark.asyncio
async def test_twenty_concurrent_cold_keystrokes_start_one_fetch(monkeypatch):
    fake = _use(monkeypatch, _Fake(delay=0.01))
    assert all(svc.get_active_listings() is None for _ in range(20))
    await _drain()
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_a_fresh_directory_is_served_without_any_fetch(monkeypatch):
    fake = _use(monkeypatch, _Fake())
    await svc.refresh_active_listings()
    for _ in range(5):
        assert svc.get_active_listings() is not None
    assert fake.calls == 1 and not svc._inflight


@pytest.mark.asyncio
async def test_dotted_foreign_symbols_are_not_stored_and_symbols_are_normalized(monkeypatch):
    rows = _payload() + [{"symbol": " avgo ", "name": "Broadcom Inc."},
                         {"symbol": None, "name": "x"}, "junk", {"symbol": 7},
                         {"symbol": "NONAME", "name": 12}]
    _use(monkeypatch, _Fake(result=rows))
    directory = await svc.refresh_active_listings()
    assert not any("." in s for s in directory)
    assert directory["AVGO"] == "Broadcom Inc."
    assert directory["NONAME"] == ""


@pytest.mark.asyncio
async def test_stale_serves_the_last_good_copy_and_refreshes_once(monkeypatch):
    fake = _use(monkeypatch, _Fake())
    await svc.refresh_active_listings()
    ts, directory = svc._cache[svc._DIRECTORY_KEY]
    svc._cache[svc._DIRECTORY_KEY] = (ts - svc._FRESH_TTL - 60, directory)

    assert svc.get_active_listings() is directory, "stale-while-revalidate"
    assert svc.get_active_listings() is directory
    await _drain()
    assert fake.calls == 2, "exactly one background refresh"


# ── Refusals and failures ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    _payload(us=30_000, foreign=19_999),            # 49,999 rows total
    _payload(us=19_999, foreign=40_000),            # 19,999 dot-free symbols
    [],                                             # an empty answer is a failure
])
async def test_payload_under_a_floor_is_refused_and_never_cached(monkeypatch, rows, caplog):
    _use(monkeypatch, _Fake(result=rows))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert await svc.refresh_active_listings() is None
    assert svc._DIRECTORY_KEY not in svc._cache
    assert any("ActiveListingsRefused" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_shrunken_payload_keeps_the_last_good_copy(monkeypatch):
    _use(monkeypatch, _Fake())
    good = await svc.refresh_active_listings()
    _use(monkeypatch, _Fake(result=_payload(us=23_000, foreign=30_000)))   # 77% of 30k
    assert await svc.refresh_active_listings() is None
    assert svc._cache[svc._DIRECTORY_KEY][1] is good


@pytest.mark.asyncio
async def test_the_relative_floor_ignores_an_expired_copy(monkeypatch):
    """Otherwise a legitimate >20% shrink would be refused forever once the old copy
    expired, and the liveness rule would stay off for good."""
    _use(monkeypatch, _Fake())
    good = await svc.refresh_active_listings()
    expired = (time.time() - svc._MAX_STALE - 60, good)
    _use(monkeypatch, _Fake(result=_payload(us=23_000, foreign=30_000)))

    # A refresh that runs while the expired copy is still held must not compare with it.
    svc._cache[svc._DIRECTORY_KEY] = expired
    assert await svc.refresh_active_listings() is not None
    assert len(svc._cache[svc._DIRECTORY_KEY][1]) == 23_000

    # The keystroke path: an expired copy is dropped, never served, and replaced.
    svc._cache[svc._DIRECTORY_KEY] = expired
    assert svc.get_active_listings() is None, "an expired copy is not served"
    await _drain()
    assert len(svc._cache[svc._DIRECTORY_KEY][1]) == 23_000


@pytest.mark.asyncio
async def test_an_upstream_failure_keeps_last_good_and_memoizes_the_outage(monkeypatch):
    _use(monkeypatch, _Fake())
    good = await svc.refresh_active_listings()
    ts, _ = svc._cache[svc._DIRECTORY_KEY]
    svc._cache[svc._DIRECTORY_KEY] = (ts - svc._FRESH_TTL - 60, good)

    failing = _use(monkeypatch, _Fake(exc=FMPUnavailableException("boom")))
    assert svc.get_active_listings() is good
    await _drain()
    assert failing.calls == 1
    for _ in range(10):
        assert svc.get_active_listings() is good
    assert not svc._inflight and failing.calls == 1, "no fetch per keystroke in an outage"


@pytest.mark.asyncio
async def test_a_hung_fetch_times_out(monkeypatch):
    monkeypatch.setattr(svc, "_REFRESH_TIMEOUT", 0.05)
    _use(monkeypatch, _Fake(delay=5))
    assert await svc.refresh_active_listings() is None
    assert svc._DEGRADED_KEY in svc._cache


@pytest.mark.asyncio
async def test_repeated_failures_escalate_to_error(monkeypatch, caplog):
    _use(monkeypatch, _Fake(exc=RuntimeError("down")))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(4):
            await svc.refresh_active_listings()
    levels = [r.levelno for r in caplog.records if r.name == LOGGER]
    assert levels == [logging.WARNING, logging.WARNING, logging.ERROR, logging.WARNING]
    _use(monkeypatch, _Fake())
    await svc.refresh_active_listings()
    assert svc._consecutive_failures == 0


@pytest.mark.asyncio
async def test_a_cancelled_refresh_propagates_and_clears_its_entry(monkeypatch):
    _use(monkeypatch, _Fake(delay=5))
    svc.get_active_listings()
    task = svc._inflight[svc._DIRECTORY_KEY]
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert svc._DIRECTORY_KEY not in svc._inflight, "a cancel must not wedge the next refresh"
    assert svc._DEGRADED_KEY not in svc._cache, "a cancel is not an outage"


def test_a_sync_caller_gets_none_and_schedules_nothing():
    assert svc.get_active_listings() is None
    assert not svc._inflight


# ── The integration method ────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"Error Message": "limit"}, None, "text"])
async def test_the_integration_refuses_a_non_list_body(body):
    class _Client:
        async def _make_request(self, endpoint, params=None):
            assert endpoint == "actively-trading-list"
            return body
        get_actively_trading_list = FMPClient.get_actively_trading_list

    with pytest.raises(FMPUnavailableException):
        await _Client().get_actively_trading_list()
