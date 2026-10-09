"""`StockOverviewService._get_fundamentals` — one build per ticker at a time (CLAUDE.md invariant 4).

Why this exists: a cold fundamentals build is ~15 FMP calls (a daily history from 1900 for the
stock AND SPY among them), and it had no in-flight dedup — the overview, Ask Cay AI's financials
tool (several sections of one round read the key facts at once) and a second viewer of the same
cold ticker each started their own. Pins: concurrent callers share ONE Tier-2 read and ONE FMP
fan-out; a caller that goes away never cancels the build (it still fills the caches); a failure
reaches every waiter, is logged even when nobody waits, and frees the slot for a retry; a slot
left by a closed event loop is never joined. Hermetic: every DB and FMP leg is a stub.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.services import stock_overview_service as sos
from app.services.stock_overview_service import StockOverviewService, _cache
from app.services.ticker_report_cache import current_close_cycle_start
from app.utils.market_hours import ET


def _bundle():
    return {
        "profile": {"companyName": "Apple Inc.", "currency": "USD"},
        "key_metrics": [{"date": "2025-09-27"}],
        "stock_historical": [{"date": "2026-10-07", "close": 250.0}],
        sos._SETTLED_THROUGH_KEY: current_close_cycle_start().astimezone(ET).date().isoformat(),
    }


class _Stubs:
    def __init__(self, svc, fetch=None):
        self.db_reads = 0
        self.fetches = 0
        self.writes = 0
        self.gate = asyncio.Event()
        self.fetch = fetch

        def _check(ticker):
            self.db_reads += 1
            return None

        async def _fetch(ticker):
            self.fetches += 1
            await self.gate.wait()
            if self.fetch is not None:
                return await self.fetch(ticker)
            return _bundle()

        def _upsert(ticker, data):
            self.writes += 1

        svc._check_fundamentals_db = _check
        svc._fetch_fundamentals = _fetch
        svc._upsert_fundamentals_db = _upsert


@pytest.fixture
def svc():
    _cache.clear()
    sos._fundamentals_inflight.clear()
    s = StockOverviewService.__new__(StockOverviewService)
    yield s
    _cache.clear()
    sos._fundamentals_inflight.clear()


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_build(svc):
    stubs = _Stubs(svc)
    calls = [asyncio.ensure_future(svc._get_fundamentals("AAPL")) for _ in range(5)]
    await asyncio.sleep(0.01)
    stubs.gate.set()
    results = await asyncio.gather(*calls)
    assert stubs.db_reads == 1 and stubs.fetches == 1 and stubs.writes == 1
    assert all(r["profile"]["companyName"] == "Apple Inc." for r in results)
    assert sos._fundamentals_inflight == {}
    # Afterwards Tier 1 answers without a new build.
    await svc._get_fundamentals("AAPL")
    assert stubs.fetches == 1


@pytest.mark.asyncio
async def test_different_tickers_build_separately(svc):
    stubs = _Stubs(svc)
    calls = [asyncio.ensure_future(svc._get_fundamentals(t)) for t in ("AAPL", "MSFT")]
    await asyncio.sleep(0.01)
    stubs.gate.set()
    await asyncio.gather(*calls)
    assert stubs.fetches == 2


@pytest.mark.asyncio
async def test_a_caller_that_goes_away_never_cancels_the_build(svc):
    stubs = _Stubs(svc)
    leader = asyncio.ensure_future(svc._get_fundamentals("AAPL"))
    await asyncio.sleep(0.01)
    joiner = asyncio.ensure_future(svc._get_fundamentals("AAPL"))
    await asyncio.sleep(0.01)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    stubs.gate.set()
    data = await asyncio.wait_for(joiner, 2.0)
    assert data["profile"]["companyName"] == "Apple Inc." and stubs.fetches == 1
    assert stubs.writes == 1, "the build still filled the cache tiers"


@pytest.mark.asyncio
async def test_a_failure_reaches_every_waiter_is_logged_and_frees_the_slot(svc, caplog):
    async def boom(ticker):
        raise RuntimeError("fmp down")

    stubs = _Stubs(svc, fetch=boom)
    calls = [asyncio.ensure_future(svc._get_fundamentals("AAPL")) for _ in range(3)]
    await asyncio.sleep(0.01)
    with caplog.at_level(logging.WARNING, logger=sos.logger.name):
        stubs.gate.set()
        results = await asyncio.gather(*calls, return_exceptions=True)
    assert all(isinstance(r, RuntimeError) and "fmp down" in str(r) for r in results)
    assert stubs.fetches == 1 and sos._fundamentals_inflight == {}
    assert "Fundamentals build failed for AAPL: RuntimeError: fmp down" in caplog.text
    # The slot is free: the next call builds again.
    stubs.fetch = None
    await svc._get_fundamentals("AAPL")
    assert stubs.fetches == 2


@pytest.mark.asyncio
async def test_a_failure_nobody_awaits_is_still_logged(svc, caplog):
    async def boom(ticker):
        raise RuntimeError("history 429")

    stubs = _Stubs(svc, fetch=boom)
    leader = asyncio.ensure_future(svc._get_fundamentals("AAPL"))
    await asyncio.sleep(0.01)
    leader.cancel()
    with caplog.at_level(logging.WARNING, logger=sos.logger.name):
        stubs.gate.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if not sos._fundamentals_inflight:
                break
    assert sos._fundamentals_inflight == {}
    assert "Fundamentals build failed for AAPL: RuntimeError: history 429" in caplog.text


@pytest.mark.asyncio
async def test_a_slot_left_by_a_closed_loop_is_never_joined(svc):
    stubs = _Stubs(svc)
    other = asyncio.new_event_loop()
    try:
        sos._fundamentals_inflight["AAPL"] = other.create_future()
        stubs.gate.set()
        data = await asyncio.wait_for(svc._get_fundamentals("AAPL"), 2.0)
        assert data["profile"]["companyName"] == "Apple Inc." and stubs.fetches == 1
    finally:
        other.close()


@pytest.mark.asyncio
async def test_a_tier2_hit_is_shared_too(svc):
    reads = []
    gate = asyncio.Event()

    def _check(ticker):
        reads.append(ticker)
        return _bundle()

    async def _never(ticker):
        raise AssertionError("a Tier-2 hit must not fetch")

    svc._check_fundamentals_db = _check
    svc._fetch_fundamentals = _never
    real_to_thread = asyncio.to_thread

    async def slow_to_thread(fn, *a, **kw):
        await gate.wait()
        return await real_to_thread(fn, *a, **kw)

    import unittest.mock as um
    with um.patch.object(sos.asyncio, "to_thread", slow_to_thread):
        calls = [asyncio.ensure_future(svc._get_fundamentals("AAPL")) for _ in range(3)]
        await asyncio.sleep(0.01)
        gate.set()
        out = await asyncio.gather(*calls)
    assert reads == ["AAPL"] and all(o["profile"]["companyName"] == "Apple Inc." for o in out)
