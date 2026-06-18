"""
Tests for the on-view report-collection pre-warm:
  - POST /stocks/{ticker}/prewarm-report endpoint (202, kill switch, soft-skip,
    schedules a background warm).
  - warm_ticker_collection: no-op when fresh, collects once when cold, deduped
    across concurrent same-ticker callers, and NEVER raises.

No network: the Supabase/FMP/collector boundaries are monkeypatched. Endpoint
handler is called directly; warm helper is exercised against the real
get_or_collect with mocked cache I/O.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import app.api.v1.endpoints.stocks as stocks
import app.services.ticker_data_cache as tdc
from app.config import settings


# ── Endpoint ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prewarm_endpoint_schedules_warm_and_returns_202(monkeypatch):
    """Enabled → 202 'warming' AND the background warm is actually scheduled."""
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", True)
    called = asyncio.Event()

    async def _spy(ticker):
        assert ticker == "AAPL"
        called.set()

    monkeypatch.setattr(stocks, "warm_ticker_collection", _spy)

    resp = await stocks.prewarm_report_collection(ticker="aapl", user={"id": "u1"})

    assert resp.status_code == 202
    assert json.loads(resp.body)["status"] == "warming"
    await asyncio.wait_for(called.wait(), timeout=1)  # task ran (not GC'd)


@pytest.mark.asyncio
async def test_prewarm_endpoint_disabled_does_not_schedule(monkeypatch):
    """Kill switch off → 202 'disabled' and NO warm scheduled."""
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", False)
    scheduled = {"n": 0}

    async def _spy(ticker):
        scheduled["n"] += 1

    monkeypatch.setattr(stocks, "warm_ticker_collection", _spy)

    resp = await stocks.prewarm_report_collection(ticker="AAPL", user={"id": "u1"})

    assert resp.status_code == 202
    assert json.loads(resp.body)["status"] == "disabled"
    await asyncio.sleep(0)  # give any (wrongly) scheduled task a chance to run
    assert scheduled["n"] == 0


@pytest.mark.asyncio
async def test_prewarm_endpoint_soft_skips_bad_ticker(monkeypatch):
    """A malformed ticker on this best-effort path returns 202 'skipped',
    never a 422 (must not disrupt the fire-and-forget caller)."""
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", True)

    async def _spy(ticker):
        raise AssertionError("should not warm a bad ticker")

    monkeypatch.setattr(stocks, "warm_ticker_collection", _spy)

    resp = await stocks.prewarm_report_collection(
        ticker="not a ticker!!", user={"id": "u1"}
    )
    assert resp.status_code == 202
    assert json.loads(resp.body)["status"] == "skipped"


# ── warm_ticker_collection ───────────────────────────────────────────────────


class _FakeCollector:
    """Stand-in for TickerReportDataCollector; counts _collect_fresh and can be
    gated on an event to hold concurrent callers in-flight together."""

    calls = 0
    gate: "asyncio.Event | None" = None

    def __init__(self, fmp=None):
        self.fmp = fmp

    async def _collect_fresh(self, ticker):
        type(self).calls += 1
        if type(self).gate is not None:
            await type(self).gate.wait()
        return {"ticker": ticker, "profile": {"x": 1}, "computed": {"y": 2}}


def _patch_collection_boundaries(monkeypatch, *, fresh):
    """Mock the Supabase/FMP/collector boundaries. `fresh` controls whether the
    cache reports the ticker as already warm."""
    _FakeCollector.calls = 0
    _FakeCollector.gate = None
    tdc._INFLIGHT.clear()
    tdc._WARM_SEMAPHORE = None  # rebuilt from settings on next use

    sentinel = object() if fresh else None

    async def _get_cached(ticker):
        return sentinel

    async def _store(ticker, out):
        return None

    monkeypatch.setattr(tdc, "get_cached_collection", _get_cached)
    monkeypatch.setattr(tdc, "store_collection", _store)
    monkeypatch.setattr(
        "app.services.agents.ticker_report_data_collector.TickerReportDataCollector",
        _FakeCollector,
    )
    monkeypatch.setattr("app.integrations.fmp.get_fmp_client", lambda: object())


@pytest.mark.asyncio
async def test_warm_noop_when_fresh(monkeypatch):
    """Already-fresh ticker → no collection work at all (cheap fast path)."""
    _patch_collection_boundaries(monkeypatch, fresh=True)
    await tdc.warm_ticker_collection("AAPL")
    assert _FakeCollector.calls == 0


@pytest.mark.asyncio
async def test_warm_collects_once_when_cold(monkeypatch):
    """Cold ticker → exactly one _collect_fresh."""
    _patch_collection_boundaries(monkeypatch, fresh=False)
    await tdc.warm_ticker_collection("AAPL")
    assert _FakeCollector.calls == 1


@pytest.mark.asyncio
async def test_warm_dedups_concurrent_same_ticker(monkeypatch):
    """N concurrent warms of the SAME cold ticker collapse to ONE _collect_fresh
    via the get_or_collect _INFLIGHT future (the whole point — a hot ticker
    being opened by many users at once must not fan out N collections)."""
    _patch_collection_boundaries(monkeypatch, fresh=False)
    tdc._WARM_SEMAPHORE = asyncio.Semaphore(10)  # don't let the sema serialize them
    _FakeCollector.gate = asyncio.Event()

    tasks = [asyncio.create_task(tdc.warm_ticker_collection("AAPL")) for _ in range(5)]
    await asyncio.sleep(0.05)        # let all 5 attach to the one in-flight future
    _FakeCollector.gate.set()
    await asyncio.gather(*tasks)

    assert _FakeCollector.calls == 1
    assert not tdc._INFLIGHT          # cleaned up


@pytest.mark.asyncio
async def test_warm_never_raises(monkeypatch):
    """A failure anywhere in the warm path is swallowed (best-effort) — the
    report will just collect on demand later."""
    _patch_collection_boundaries(monkeypatch, fresh=False)

    async def _boom(ticker, fetch_fresh):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(tdc, "get_or_collect", _boom)

    # Must NOT raise.
    await tdc.warm_ticker_collection("AAPL")


@pytest.mark.asyncio
async def test_warm_non_str_ticker_never_raises(monkeypatch):
    """REGRESSION: normalization now lives INSIDE the try, so even a non-str
    input (e.g. a malformed RPC row) is swallowed, honoring 'never raises'."""
    _patch_collection_boundaries(monkeypatch, fresh=False)
    await tdc.warm_ticker_collection(12345)            # .upper() would AttributeError
    assert _FakeCollector.calls == 0                   # bailed before collecting


@pytest.mark.asyncio
async def test_endpoint_sheds_load_at_inflight_cap(monkeypatch):
    """REGRESSION: over REPORT_PREWARM_MAX_INFLIGHT in-flight warms → 202 'busy'
    and NO new warm scheduled (bounds Gemini/Supabase drain under a burst)."""
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_PREWARM_MAX_INFLIGHT", 0)  # always "full"
    scheduled = {"n": 0}

    async def _spy(ticker):
        scheduled["n"] += 1

    monkeypatch.setattr(stocks, "warm_ticker_collection", _spy)

    resp = await stocks.prewarm_report_collection(
        ticker="AAPL", user={"id": "u1"}, _rate_limit=None
    )
    assert resp.status_code == 202
    assert json.loads(resp.body)["status"] == "busy"
    await asyncio.sleep(0)
    assert scheduled["n"] == 0


@pytest.mark.asyncio
async def test_get_or_collect_cancelled_owner_wakes_waiters(monkeypatch):
    """REGRESSION (the HIGH finding): if the inflight OWNER is cancelled mid-fetch
    (e.g. a fire-and-forget prewarm task killed on shutdown/redeploy), every
    attached WAITER — including a real report's collect() — must fail fast with a
    regular exception, NOT hang on an unresolved future."""
    tdc._INFLIGHT.clear()

    async def _none(t):
        return None

    async def _store(t, o):
        return None

    monkeypatch.setattr(tdc, "get_cached_collection", _none)
    monkeypatch.setattr(tdc, "store_collection", _store)

    gate = asyncio.Event()

    async def _owner_fetch():
        await gate.wait()                 # park mid-fetch until cancelled
        return {"x": 1}

    async def _waiter_fetch():
        raise AssertionError("waiter must attach to the owner's future, not run")

    owner = asyncio.create_task(tdc.get_or_collect("X", _owner_fetch))
    await asyncio.sleep(0.05)             # owner creates the inflight future
    waiter = asyncio.create_task(tdc.get_or_collect("X", _waiter_fetch))
    await asyncio.sleep(0.05)             # waiter attaches via `await inflight`

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    # Waiter gets a regular RuntimeError (the handoff), NOT a TimeoutError (hang).
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(waiter, timeout=2)
    assert not tdc._INFLIGHT             # cleaned up


@pytest.mark.asyncio
async def test_prewarm_then_report_collection_is_a_cache_hit(monkeypatch):
    """INTEGRATION (the feature's whole point): an on-view warm populates
    ticker_data_cache, so a LATER report collection for the same ticker HITs the
    cache and skips _collect_fresh entirely. Uses the REAL get_or_collect against
    a stateful in-memory stand-in for the Supabase cache."""
    tdc._INFLIGHT.clear()
    tdc._WARM_SEMAPHORE = None
    _FakeCollector.calls = 0
    _FakeCollector.gate = None

    store: dict = {}

    async def _get(ticker):
        return store.get(ticker)

    async def _store(ticker, out):
        store[ticker] = out

    monkeypatch.setattr(tdc, "get_cached_collection", _get)
    monkeypatch.setattr(tdc, "store_collection", _store)
    monkeypatch.setattr(
        "app.services.agents.ticker_report_data_collector.TickerReportDataCollector",
        _FakeCollector,
    )
    monkeypatch.setattr("app.integrations.fmp.get_fmp_client", lambda: object())

    # 1) On-view warm → exactly one collection, persisted to the shared cache.
    await tdc.warm_ticker_collection("X")
    assert _FakeCollector.calls == 1
    assert "X" in store

    # 2) A later report collection for the same ticker reuses it — HIT, no
    #    second _collect_fresh (the FMP fan-out + grounded precompute are saved).
    result = await tdc.get_or_collect("X", lambda: _FakeCollector()._collect_fresh("X"))
    assert _FakeCollector.calls == 1
    assert result == store["X"]


@pytest.mark.asyncio
async def test_warm_semaphore_slot_released_on_collect_failure(monkeypatch):
    """A failing collect must RELEASE its _WARM_SEMAPHORE slot — else after
    REPORT_PREWARM_DETAIL_CONCURRENCY failures all future distinct-ticker warms
    would deadlock process-wide. (warm failures are expected: FMP/Gemini hiccups.)"""
    _patch_collection_boundaries(monkeypatch, fresh=False)
    tdc._WARM_SEMAPHORE = asyncio.Semaphore(1)   # one slot; must be reusable

    calls = {"n": 0}

    async def _maybe_boom(ticker, fetch_fresh):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("FMP hiccup")     # first warm fails mid-slot
        return await fetch_fresh()

    monkeypatch.setattr(tdc, "get_or_collect", _maybe_boom)

    await tdc.warm_ticker_collection("AAA")      # fails, must release the slot
    await asyncio.wait_for(tdc.warm_ticker_collection("BBB"), timeout=2)  # not deadlocked
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_prewarm_inflight_slot_released_after_completion(monkeypatch):
    """The endpoint's _PREWARM_TASKS add/discard lifecycle: a scheduled warm is
    tracked while in-flight and discarded on completion — else _PREWARM_TASKS
    fills monotonically and the endpoint wedges at permanent 'busy'."""
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_PREWARM_MAX_INFLIGHT", 50)
    stocks._PREWARM_TASKS.clear()

    done = asyncio.Event()

    async def _warm(ticker):
        await done.wait()

    monkeypatch.setattr(stocks, "warm_ticker_collection", _warm)

    resp = await stocks.prewarm_report_collection(
        ticker="AAPL", user={"id": "u1"}, _rate_limit=None
    )
    assert resp.status_code == 202
    await asyncio.sleep(0)
    assert len(stocks._PREWARM_TASKS) == 1        # tracked while in-flight

    done.set()
    await asyncio.sleep(0.05)                      # let the task finish + callback fire
    assert len(stocks._PREWARM_TASKS) == 0        # discarded on completion
