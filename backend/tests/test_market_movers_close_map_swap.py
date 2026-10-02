"""The movers close map is rebuilt, then swapped in — never dropped in front of a reader.

Before 2026-10-01 the map behind Home's scanners and every sector strip had two holes:

* The hourly close ingest POPPED `movers:closes` (`price_service.refresh_close_snapshot`),
  and its TTL equalled the loop's period, so for 20-40 s an hour a request paid the full
  ~74-page sweep (11-14 s) and Home's 8 s guard gave up on the scanners.
* After every deploy there was no map at all, so the first dashboard always waited 8 s.

Now `refresh_closes()` is the ONLY writer: it builds the new map completely and only then
swaps it in, refusing an empty or shrunken one. `_all_closes()` serves the map it has,
starts ONE background rebuild past `_CLOSES_SOFT_TTL`, and awaits a rebuild only with no
map or one past `_CLOSES_MAX_AGE` — and even then a failed rebuild still gets the old map.
That is honest because readers judge each row's `trade_date`
(`PriceService._snapshot_is_current` / `_pick_denominator`).

Also here: `PriceService.refresh_universe()`, the forced screener-universe build the Home
warmers use, which must share `_get_universe`'s in-flight build and writer guards.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict

import pytest
import pytest_asyncio

import app.services.market_movers_service as mm
import app.services.price_service as ps
from app.integrations.fmp import FMPUnavailableException
from app.services.market_movers_service import CloseMapRefused, MarketMoversService
from app.services.price_service import PriceService

_KEY = "movers:closes"


# Async, so its teardown can AWAIT stragglers on the test's own loop. `pytest_asyncio`'s
# decorator, not `pytest.fixture`: in strict mode the latter never runs an async body.
@pytest_asyncio.fixture(autouse=True)
async def _isolated(monkeypatch):
    mm._cache.clear()
    mm._inflight.clear()
    monkeypatch.setattr(mm, "_closes_refresh_task", None)
    yield
    # A background rebuild must never outlive its test: once monkeypatch restores the real
    # `_select_all_closes`, a straggler would reach the (blocked) network.
    for task in list(mm._background_tasks):
        task.cancel()
    for task in list(mm._background_tasks):
        try:
            await task
        except BaseException:          # noqa: BLE001 — cancelled or failed; both fine here
            pass
    mm._background_tasks.clear()
    mm._cache.clear()
    mm._inflight.clear()


def _closes(n: int, tag: str = "old") -> Dict[str, Dict[str, Any]]:
    return {
        f"S{i:04d}": {"symbol": f"S{i:04d}", "close": 10.0 + i, "previous_close": 9.0 + i,
                      "trade_date": "2026-09-30", "tag": tag}
        for i in range(n)
    }


def _seed(closes, age: float) -> tuple:
    entry = (time.time() - age, closes)
    mm._cache[_KEY] = entry
    return entry


class _Select:
    """`_select_all_closes` stand-in. Runs in a worker thread (as the real one does), can
    be held on a threading.Event, and returns `results` in order (an Exception raises)."""

    def __init__(self, *results, hold: bool = False):
        self.results = list(results)
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        if not hold:
            self.release.set()

    def __call__(self):
        self.calls += 1
        result = self.results[min(self.calls, len(self.results)) - 1]
        self.entered.set()
        assert self.release.wait(5), "test never released the held close-map build"
        if isinstance(result, BaseException):
            raise result
        return result

    async def wait_entered(self):
        assert await asyncio.to_thread(self.entered.wait, 5), "the build never started"


def _install(monkeypatch, select: _Select) -> _Select:
    monkeypatch.setattr(MarketMoversService, "_select_all_closes", staticmethod(select))
    return select


# ── the swap: old map served while the new one is built ────────────────────────────


@pytest.mark.asyncio
async def test_the_old_map_is_served_while_a_held_refresh_runs(monkeypatch):
    old = _closes(100, "old")
    new = _closes(100, "new")
    _seed(old, age=mm._CLOSES_SOFT_TTL + 60)
    sel = _install(monkeypatch, _Select(new, hold=True))
    svc = MarketMoversService()
    try:
        assert await svc._all_closes() is old           # past soft TTL: served, refresh kicked
        await sel.wait_entered()
        # The build is in flight. Readers still get the OLD map, at once, and no reader
        # starts a second build.
        for _ in range(3):
            assert await asyncio.wait_for(svc._all_closes(), 0.5) is old
        assert sel.calls == 1
        assert mm._cache[_KEY][1] is old, "the live map was dropped before its replacement"
    finally:
        sel.release.set()
    await asyncio.wait_for(mm._closes_refresh_task, 5)
    assert mm._cache[_KEY][1] is new
    assert await svc._all_closes() is new
    assert mm._inflight == {}


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_old_map_and_warns(monkeypatch, caplog):
    old = _closes(100)
    entry = _seed(old, age=mm._CLOSES_MAX_AGE + 60)       # too old to serve untried
    sel = _install(monkeypatch, _Select(RuntimeError("PostgREST 520")))
    with caplog.at_level(logging.WARNING, logger=mm.logger.name):
        got = await MarketMoversService()._all_closes()
    assert got is old, "a failed rebuild must still serve the map it has"
    assert mm._cache[_KEY] is entry, "the failed rebuild replaced (or re-stamped) the map"
    assert sel.calls == 1
    assert any("serving the previous map" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records), "a degraded serve must leave a WARNING"


@pytest.mark.asyncio
async def test_a_failed_background_refresh_is_logged_and_changes_nothing(monkeypatch, caplog):
    old = _closes(100)
    entry = _seed(old, age=mm._CLOSES_SOFT_TTL + 60)
    _install(monkeypatch, _Select(RuntimeError("PostgREST 520")))
    with caplog.at_level(logging.WARNING, logger=mm.logger.name):
        assert await MarketMoversService()._all_closes() is old
        task = mm._closes_refresh_task
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)                 # let the done callback run
    assert mm._cache[_KEY] is entry
    assert any("background close-map refresh failed" in r.getMessage()
               for r in caplog.records), "a background failure must not be silent"
    assert task not in mm._background_tasks, "the done callback did not release the task"


@pytest.mark.asyncio
async def test_no_map_and_a_failed_rebuild_raises(monkeypatch):
    _install(monkeypatch, _Select(RuntimeError("PostgREST 520")))
    with pytest.raises(RuntimeError, match="520"):
        await MarketMoversService()._all_closes()
    assert _KEY not in mm._cache


# ── the writer's sanity checks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_empty_rebuild_is_refused_and_the_old_map_kept(monkeypatch, caplog):
    entry = _seed(_closes(100), age=10)
    _install(monkeypatch, _Select({}))
    with caplog.at_level(logging.ERROR, logger=mm.logger.name):
        with pytest.raises(CloseMapRefused):
            await MarketMoversService().refresh_closes()
    assert mm._cache[_KEY] is entry
    assert any(r.levelno == logging.ERROR and "EMPTY" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_an_empty_rebuild_with_no_live_map_is_refused_too(monkeypatch):
    _install(monkeypatch, _Select({}))
    with pytest.raises(CloseMapRefused):
        await MarketMoversService().refresh_closes()
    assert _KEY not in mm._cache, "an empty map must never be cached as the answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("rows, accepted", [(89, False), (90, True), (100, True), (150, True)])
async def test_a_rebuild_below_ninety_percent_of_the_live_map_is_refused(
    monkeypatch, caplog, rows, accepted
):
    entry = _seed(_closes(100), age=10)
    new = _closes(rows, "new")
    _install(monkeypatch, _Select(new))
    svc = MarketMoversService()
    if accepted:
        assert await svc.refresh_closes() is new
        assert mm._cache[_KEY][1] is new
    else:
        with caplog.at_level(logging.ERROR, logger=mm.logger.name):
            with pytest.raises(CloseMapRefused, match="100 to 89"):
                await svc.refresh_closes()
        assert mm._cache[_KEY] is entry
        assert any(r.levelno == logging.ERROR and "refused" in r.getMessage()
                   for r in caplog.records)


@pytest.mark.asyncio
async def test_a_shrunken_rebuild_is_accepted_once_the_live_map_is_past_max_age(
    monkeypatch, caplog
):
    """A deliberate bulk delete must not freeze the map until the next restart."""
    _seed(_closes(100), age=mm._CLOSES_MAX_AGE + 60)
    new = _closes(50, "new")
    _install(monkeypatch, _Select(new))
    with caplog.at_level(logging.ERROR, logger=mm.logger.name):
        assert await MarketMoversService()._all_closes() is new
    assert mm._cache[_KEY][1] is new
    assert any(r.levelno == logging.ERROR and "accepted anyway" in r.getMessage()
               for r in caplog.records), "accepting a shrink must still be loud"


# ── the soft TTL outlasts the hourly ingest cycle ───────────────────────────────────
# Review finding (2026-10-01): at a soft TTL EQUAL to the loop's 3600 s sleep, the next map
# lands one period PLUS one ingest after the last, so a read inside every ingest window
# kicked a second sweep (often over a half-written upsert) and `after_write` ran a third.


def _close_loop_sleeps() -> list:
    """The literal `asyncio.sleep(N)` seconds awaited inside `_run_close_snapshot_loop`'s
    `while` loop (the 90 s start-up stagger before the loop is not the period)."""
    import ast
    from pathlib import Path

    main_py = Path(mm.__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(main_py.read_text())
    loops = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_close_snapshot_loop"
    ]
    assert len(loops) == 1, "the hourly close-snapshot loop moved or was renamed"
    sleeps = []
    for loop in (n for n in ast.walk(loops[0]) if isinstance(n, ast.While)):
        for node in ast.walk(loop):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "sleep"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "asyncio"
            ):
                assert node.args and isinstance(node.args[0], ast.Constant), (
                    "the loop's sleep is no longer a literal — pin it to the period constant"
                )
                sleeps.append(float(node.args[0].value))
    return sleeps


def test_the_soft_ttl_outlasts_the_hourly_ingest_cycle():
    assert _close_loop_sleeps() == [mm._CLOSE_INGEST_PERIOD_SECONDS], (
        "the hourly loop's period changed: re-derive _CLOSES_SOFT_TTL"
    )
    assert mm._CLOSES_SOFT_TTL == (
        mm._CLOSE_INGEST_PERIOD_SECONDS + mm._CLOSE_INGEST_BUDGET_SECONDS
    ) == 4200
    # A normal ingest is 10-40 s (two ~10 s batch-eod fetches and the upsert); the budget
    # keeps a wide margin over it, and stays far below the hard 12 h limit.
    assert mm._CLOSE_INGEST_BUDGET_SECONDS >= 10 * 40
    assert mm._CLOSES_SOFT_TTL < mm._CLOSES_MAX_AGE == 43200


@pytest.mark.asyncio
@pytest.mark.parametrize("ingest_seconds", [10, 40, 300, 599])
async def test_a_read_during_the_next_ingest_starts_no_second_sweep(monkeypatch, ingest_seconds):
    """The loop stamped this map, slept one period and is now ingesting: a read in that
    window leaves the rebuild to the loop's own `refresh_closes(after_write=True)`."""
    old = _closes(10)
    _seed(old, age=mm._CLOSE_INGEST_PERIOD_SECONDS + ingest_seconds)
    sel = _install(monkeypatch, _Select(_closes(10, "new")))
    assert await MarketMoversService()._all_closes() is old
    await asyncio.sleep(0.05)
    assert sel.calls == 0 and mm._closes_refresh_task is None, (
        f"a read {ingest_seconds}s into the ingest started a second close-map sweep"
    )


@pytest.mark.asyncio
async def test_a_map_the_loop_failed_to_refresh_is_still_refreshed_by_a_reader(monkeypatch):
    """Degraded path: past the soft TTL (the loop's rebuild failed or the ingest took longer
    than its budget) a read still serves the old map and kicks exactly one rebuild."""
    old = _closes(10)
    new = _closes(10, "new")
    _seed(old, age=mm._CLOSES_SOFT_TTL + 1)
    sel = _install(monkeypatch, _Select(new))
    assert await MarketMoversService()._all_closes() is old
    for _ in range(50):
        if mm._cache[_KEY][1] is new:
            break
        await asyncio.sleep(0.01)
    assert sel.calls == 1 and mm._cache[_KEY][1] is new


# ── the read path's ages ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fresh_map_is_served_with_no_refresh(monkeypatch):
    old = _closes(10)
    _seed(old, age=mm._CLOSES_SOFT_TTL - 60)
    sel = _install(monkeypatch, _Select(_closes(10, "new")))
    assert await MarketMoversService()._all_closes() is old
    await asyncio.sleep(0.05)
    assert sel.calls == 0 and mm._closes_refresh_task is None


@pytest.mark.asyncio
async def test_a_soft_ttl_read_starts_exactly_one_background_refresh(monkeypatch):
    old = _closes(100)
    new = _closes(100, "new")
    _seed(old, age=mm._CLOSES_SOFT_TTL + 60)
    sel = _install(monkeypatch, _Select(new, hold=True))
    svc = MarketMoversService()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(svc._all_closes() for _ in range(8))), 1.0,
        )
        assert all(r is old for r in results), "a soft-stale read must not wait"
        await sel.wait_entered()
        await asyncio.sleep(0.05)
        assert sel.calls == 1, f"{sel.calls} rebuilds for one stale map"
        assert len(mm._background_tasks) == 1
    finally:
        sel.release.set()
    await asyncio.wait_for(mm._closes_refresh_task, 5)
    assert mm._cache[_KEY][1] is new


@pytest.mark.asyncio
async def test_a_map_stamped_in_the_future_is_refreshed(monkeypatch):
    """The wall clock stepped back: the stamp is re-taken instead of trusted for a day."""
    old = _closes(100)
    _seed(old, age=-600)
    sel = _install(monkeypatch, _Select(_closes(100, "new")))
    assert await MarketMoversService()._all_closes() is old
    await asyncio.wait_for(mm._closes_refresh_task, 5)
    assert sel.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [None, mm._CLOSES_MAX_AGE + 60])
async def test_no_map_or_a_map_past_max_age_awaits_the_rebuild(monkeypatch, age):
    if age is not None:
        _seed(_closes(100), age=age)
    new = _closes(100, "new")
    sel = _install(monkeypatch, _Select(new))
    assert await MarketMoversService()._all_closes() is new
    assert sel.calls == 1
    assert mm._cache[_KEY][1] is new


@pytest.mark.asyncio
async def test_concurrent_cold_reads_share_one_rebuild(monkeypatch):
    new = _closes(100, "new")
    sel = _install(monkeypatch, _Select(new, hold=True))
    svc = MarketMoversService()
    readers = [asyncio.create_task(svc._all_closes()) for _ in range(5)]
    await sel.wait_entered()
    sel.release.set()
    results = await asyncio.wait_for(asyncio.gather(*readers), 5)
    assert all(r is new for r in results)
    assert sel.calls == 1


@pytest.mark.asyncio
async def test_a_reader_that_gives_up_does_not_cancel_the_rebuild(monkeypatch):
    """Home's 8 s guard cancels a slow read. The cold sweep used to be cancelled with it,
    so after a deploy every request restarted it and none ever finished."""
    new = _closes(100, "new")
    sel = _install(monkeypatch, _Select(new, hold=True))
    svc = MarketMoversService()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(svc._all_closes(), 0.05)
    await sel.wait_entered()
    sel.release.set()
    await asyncio.wait_for(mm._closes_refresh_task, 5)
    assert mm._cache[_KEY][1] is new, "the abandoned read cancelled the shared rebuild"
    assert await svc._all_closes() is new
    assert sel.calls == 1


# ── after_write: the hourly loop's refresh must contain what the ingest just wrote ──


@pytest.mark.asyncio
async def test_after_write_waits_out_a_build_that_predates_the_write(monkeypatch):
    stale_build = _closes(100, "pre-ingest")
    post_build = _closes(100, "post-ingest")
    sel = _install(monkeypatch, _Select(stale_build, post_build, hold=True))
    svc = MarketMoversService()
    early = asyncio.create_task(svc.refresh_closes())    # e.g. a soft-TTL kick
    await sel.wait_entered()
    loop_refresh = asyncio.create_task(svc.refresh_closes(after_write=True))
    await asyncio.sleep(0.05)
    assert not loop_refresh.done(), "it returned the pre-ingest build's map"
    sel.release.set()
    assert await asyncio.wait_for(early, 5) is stale_build
    assert await asyncio.wait_for(loop_refresh, 5) is post_build
    assert sel.calls == 2
    assert mm._cache[_KEY][1] is post_build


@pytest.mark.asyncio
async def test_a_plain_refresh_joins_the_build_in_flight(monkeypatch):
    new = _closes(100, "new")
    sel = _install(monkeypatch, _Select(new, hold=True))
    svc = MarketMoversService()
    first = asyncio.create_task(svc.refresh_closes())
    await sel.wait_entered()
    second = asyncio.create_task(svc.refresh_closes())
    sel.release.set()
    assert await asyncio.wait_for(first, 5) is new
    assert await asyncio.wait_for(second, 5) is new
    assert sel.calls == 1


@pytest.mark.asyncio
async def test_a_cancelled_leader_does_not_strand_its_joiners(monkeypatch):
    sel = _install(monkeypatch, _Select(_closes(100), hold=True))
    svc = MarketMoversService()
    leader = asyncio.create_task(svc.refresh_closes())
    await sel.wait_entered()
    joiner = asyncio.create_task(svc.refresh_closes())
    await asyncio.sleep(0.02)
    leader.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await leader
        with pytest.raises(RuntimeError, match="cancelled"):
            await asyncio.wait_for(joiner, 2)
    finally:
        sel.release.set()
    assert mm._inflight == {}


# ── the ingest no longer drops the map ─────────────────────────────────────────────


def _eod(symbol, d, close):
    return {"symbol": symbol, "date": d, "close": close, "volume": 1000}


@pytest.mark.asyncio
async def test_the_ingest_keeps_the_close_map_and_starts_no_task(monkeypatch):
    entry = _seed(_closes(100), age=30)
    ps._cache["price:close:AAPL"] = (time.time(), {"close": 1.0})

    sessions = {
        "2026-09-04": [_eod("AAPL", "2026-09-04", 101.0), _eod("MSFT", "2026-09-04", 201.0)],
        "2026-09-03": [_eod("AAPL", "2026-09-03", 100.0), _eod("MSFT", "2026-09-03", 200.0)],
    }

    async def _fetch(self, start_date=None):
        return start_date, sessions.get(start_date, [])

    async def _universe(self):
        return None

    written = []
    monkeypatch.setattr(PriceService, "_fetch_latest_session", _fetch)
    monkeypatch.setattr(PriceService, "_universe_symbols_for_coverage", _universe)
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: written.extend(p) or len(p)))
    sel = _install(monkeypatch, _Select(_closes(100, "new")))

    before = asyncio.all_tasks()
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 2
    await asyncio.sleep(0.05)

    assert [r["symbol"] for r in written] == ["AAPL", "MSFT"], "anti-vacuity: no write"
    assert mm._cache.get(_KEY) is entry, (
        "the ingest dropped the movers close map — the next request pays the full sweep"
    )
    assert asyncio.all_tasks() - before == set(), "the ingest started a background task"
    assert sel.calls == 0, "the ingest kicked a close-map rebuild (not hermetic in tests)"
    assert "price:close:AAPL" not in ps._cache, "the per-symbol close keys must still drop"


# ── the screener universe: forced rebuild for the warmers ─────────────────────────


class _Pages:
    def __init__(self, *results, hold=False):
        self.results = list(results)
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def __call__(self):
        self.calls += 1
        result = self.results[min(self.calls, len(self.results)) - 1]
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 5)
        if isinstance(result, BaseException):
            raise result
        return result


def _screener(*symbols):
    return [{"symbol": s, "price": 10.0} for s in symbols]


@pytest.fixture
def _clean_universe():
    keys = ("price:universe", ps._UNIVERSE_DEGRADED_KEY)
    for k in keys:
        ps._cache.pop(k, None)
    ps._inflight.pop("price:universe", None)
    yield
    for k in keys:
        ps._cache.pop(k, None)
    ps._inflight.pop("price:universe", None)


@pytest.mark.asyncio
async def test_refresh_universe_rebuilds_a_fresh_universe(_clean_universe):
    svc = PriceService()
    pages = _Pages(_screener("AAPL"), _screener("AAPL", "MSFT"))
    svc._fetch_universe_pages = pages
    first = await svc._get_universe()
    assert await svc._get_universe() is first and pages.calls == 1   # fresh: no rebuild
    forced = await svc.refresh_universe()
    assert pages.calls == 2, "refresh_universe skipped the build because the cache was fresh"
    assert set(forced) == {"AAPL", "MSFT"}
    assert await svc._get_universe() is forced


@pytest.mark.asyncio
@pytest.mark.parametrize("forced_first", [False, True])
async def test_refresh_universe_and_a_cold_read_share_one_build(_clean_universe, forced_first):
    svc = PriceService()
    pages = _Pages(_screener("AAPL"), hold=True)
    svc._fetch_universe_pages = pages
    calls = [svc.refresh_universe, svc._get_universe]
    if not forced_first:
        calls.reverse()
    lead = asyncio.create_task(calls[0]())
    await asyncio.wait_for(pages.entered.wait(), 2)
    join = asyncio.create_task(calls[1]())
    await asyncio.sleep(0.02)
    pages.release.set()
    a, b = await asyncio.wait_for(asyncio.gather(lead, join), 5)
    assert a is b
    assert pages.calls == 1, "a forced rebuild and a cold read ran two screener sweeps"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [[], RuntimeError("fmp 503")])
async def test_a_failed_forced_rebuild_never_replaces_a_good_universe(_clean_universe, bad):
    svc = PriceService()
    pages = _Pages(_screener("AAPL"), bad)
    svc._fetch_universe_pages = pages
    good = await svc._get_universe()
    with pytest.raises((FMPUnavailableException, RuntimeError)):
        await svc.refresh_universe()
    assert ps._cache["price:universe"][1] is good, "an empty/failed sweep was cached as good"
    assert await svc._get_universe() is good


@pytest.mark.asyncio
async def test_refresh_universe_respects_the_degraded_memo(_clean_universe):
    """Forced skips the FRESHNESS check only — a failure seconds ago still holds it off."""
    svc = PriceService()
    pages = _Pages(_screener("AAPL"))
    svc._fetch_universe_pages = pages
    ps._cache[ps._UNIVERSE_DEGRADED_KEY] = (time.time(), True)
    with pytest.raises(FMPUnavailableException):
        await svc.refresh_universe()
    assert pages.calls == 0
