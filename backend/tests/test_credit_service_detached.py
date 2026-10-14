"""Detached / shielded credit settlements (`credit_service.settle_detached`,
`settle_off_loop`, `drain_detached_settlements`), 2026-10-12.

Why these exist: a frame being CANCELLED cannot await — uvicorn cancels in-flight requests at
`--timeout-graceful-shutdown` (30 s, Dockerfile CMD) and the next await is re-cancelled at
once — and a plain `await asyncio.to_thread(refund)` cancels an executor job that had not
started yet. Either way a refund the request owed was silently dropped. The settlement now
runs in a strongly-held task the lifespan shutdown drains.

No network: every settlement here is a local callable.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from app.services import credit_service as cs


@pytest.fixture(autouse=True)
def _empty_registry():
    cs._DETACHED_SETTLEMENTS.clear()
    yield
    cs._DETACHED_SETTLEMENTS.clear()


@pytest.mark.asyncio
async def test_settle_detached_is_strongly_held_runs_off_the_loop_and_is_drained():
    loop_thread = threading.get_ident()
    gate, ran_on = threading.Event(), []

    def _refund():
        gate.wait(timeout=5)
        ran_on.append(threading.get_ident())
        return {"outcome": "refunded"}

    cs.settle_detached(_refund, what="report_refund AAPL:r1")
    assert len(cs._DETACHED_SETTLEMENTS) == 1, "the detached task is not strongly held"
    (task,) = tuple(cs._DETACHED_SETTLEMENTS)
    assert task.get_name() == "settle:report_refund AAPL:r1"
    await asyncio.sleep(0.02)
    assert not task.done()
    gate.set()
    assert await cs.drain_detached_settlements(2.0) == 0
    assert ran_on and ran_on[0] != loop_thread, "the settlement ran on the event loop thread"
    assert cs._DETACHED_SETTLEMENTS == set(), "a finished settlement stayed registered"


@pytest.mark.asyncio
async def test_a_cancelled_detached_settlement_logs_a_leak_by_name(caplog):
    ran = []
    with caplog.at_level(logging.ERROR, logger=cs.logger.name):
        cs.settle_detached(lambda: ran.append(1), what="report_refund AAPL:r9")
        (task,) = tuple(cs._DETACHED_SETTLEMENTS)
        task.cancel()                    # before its first step: the thread never starts
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)           # let the done-callbacks run
    assert ran == []
    hits = [r for r in caplog.records if "AAPL:r9" in r.getMessage()]
    assert hits and "REFUND LEAK?" in hits[0].getMessage()


@pytest.mark.asyncio
async def test_a_detached_settlement_that_raises_is_logged(caplog):
    def _boom():
        raise RuntimeError("credit service construction failed")

    with caplog.at_level(logging.ERROR, logger=cs.logger.name):
        cs.settle_detached(_boom, what="chat_undelivered sess-1")
        assert await cs.drain_detached_settlements(2.0) == 0
        await asyncio.sleep(0)
    assert any("chat_undelivered sess-1" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records)


def test_settle_detached_runs_inline_without_a_loop(caplog):
    ran = []
    cs.settle_detached(lambda: ran.append(threading.get_ident()), what="x")
    assert ran == [threading.get_ident()]
    with caplog.at_level(logging.ERROR, logger=cs.logger.name):
        cs.settle_detached(lambda: 1 / 0, what="inline-boom")   # never raises
    assert any("inline-boom" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_settle_off_loop_returns_the_result_and_propagates_errors():
    loop_thread = threading.get_ident()
    assert await cs.settle_off_loop(lambda: threading.get_ident(), what="t") != loop_thread
    with pytest.raises(ZeroDivisionError):
        await cs.settle_off_loop(lambda: 1 / 0, what="t")
    assert cs._DETACHED_SETTLEMENTS == set()


@pytest.mark.asyncio
async def test_settle_off_loop_finishes_the_refund_when_its_waiter_is_cancelled(caplog):
    """THE property: the request frame is cancelled mid-wait (a deploy), the refund still lands
    — and the drain waits for it."""
    gate, done = threading.Event(), []

    def _refund():
        gate.wait(timeout=5)
        done.append(True)
        return {"outcome": "refunded"}

    waiter = asyncio.create_task(cs.settle_off_loop(_refund, what="report_refund AAPL:r2"))
    await asyncio.sleep(0.02)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert len(cs._DETACHED_SETTLEMENTS) == 1, "the cancelled wait dropped the settlement"
    gate.set()
    assert await cs.drain_detached_settlements(2.0) == 0
    assert done == [True]


@pytest.mark.asyncio
async def test_the_drain_is_bounded_and_names_what_it_left(caplog):
    gate = threading.Event()
    cs.settle_detached(lambda: gate.wait(timeout=5), what="report_refund AAPL:stuck")
    with caplog.at_level(logging.ERROR, logger=cs.logger.name):
        left = await cs.drain_detached_settlements(0.05)
    assert left == 1
    assert any("AAPL:stuck" in r.getMessage() for r in caplog.records)
    gate.set()
    assert await cs.drain_detached_settlements(2.0) == 0


@pytest.mark.asyncio
async def test_draining_nothing_is_instant():
    assert await cs.drain_detached_settlements(0) == 0


def test_the_lifespan_drains_settlements_before_stopping_anything_else():
    """Source pin, comment-stripped and bound to `lifespan`: the drain must run right after
    `yield`, before the loops and HTTP clients the settlements may still need are torn down —
    and AGAIN after every teardown step that can still hand off a refund (the background
    loops, the Home warm builds, the marketing generations), last before the clients close
    (review F1, 2026-10-12). The drain's own late-spawn wait is pinned by behaviour below."""
    import ast
    import inspect

    from app import main

    tree = ast.parse(inspect.getsource(main.lifespan))
    code = ast.unparse(tree)
    after_yield = code[code.index("yield"):]
    call = "drain_detached_settlements(timeout="
    drain = after_yield.index(call)
    assert drain < after_yield.index("insight_sweeper_task.cancel()")
    assert drain < after_yield.index("close_fmp_client()")
    second = after_yield.find(call, drain + 1)
    assert second != -1, "the second drain (after the teardown steps) is gone"
    for step in ("asyncio.gather(*pending", "shutdown_warm_tasks(",
                 "get_marketing_script_service().shutdown("):
        assert after_yield.index(step) < second, f"{step} runs after the last drain"
    assert second < after_yield.index("close_fmp_client()")


# ── review F1 (2026-10-12): the drain must not stop at an empty first look ─────────────
#
# uvicorn 0.34 cancels the request tasks at the graceful-shutdown deadline and IMMEDIATELY
# sends `lifespan.shutdown`; the cancelled handler's `finally` spawns its refund several loop
# iterations after the drain first scans. A drain that returned on that empty scan left the
# refund to `asyncio.run`'s teardown (cancelled: lost, or a false "REFUND LEAK?" page).


@pytest.mark.asyncio
async def test_a_settlement_spawned_after_the_drain_began_is_still_awaited():
    ran = []

    def _refund():
        import time
        time.sleep(0.1)
        ran.append(1)

    async def _late_finally():
        await asyncio.sleep(0.15)        # the handler's finally, reached after the drain began
        cs.settle_detached(_refund, what="report_refund AAPL:late")

    spawner = asyncio.create_task(_late_finally())
    assert await cs.drain_detached_settlements(2.0) == 0
    assert ran == [1], "the drain returned before a settlement spawned 150 ms in"
    await spawner


@pytest.mark.asyncio
async def test_the_drain_waits_for_a_cancelled_task_that_unwinds_slower_than_the_quiet_window():
    """A starved loop can stretch an unwind past the quiet window: the drain also waits for
    tasks still unwinding a cancel (`Task.cancelling()`), bounded by `_DRAIN_UNWIND_SECONDS`."""
    ran, inside = [], asyncio.Event()

    async def _request():
        try:
            inside.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(cs._DRAIN_QUIET_SECONDS + 0.4)
            cs.settle_detached(lambda: ran.append(1), what="report_refund AAPL:slow")
            raise

    req = asyncio.create_task(_request())
    await inside.wait()
    req.cancel()
    assert await cs.drain_detached_settlements(5.0) == 0
    assert ran == [1], "the drain returned while a cancelled request was still unwinding"
    assert req.cancelled()


@pytest.mark.asyncio
async def test_a_task_that_swallows_its_cancel_cannot_hold_the_drain(monkeypatch):
    monkeypatch.setattr(cs, "_DRAIN_UNWIND_SECONDS", 0.2)
    stop = []

    async def _stubborn():
        while True:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                if stop:
                    raise
                # swallowed: `cancelling()` stays 1 forever

    t = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)
    t.cancel()
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await cs.drain_detached_settlements(5.0) == 0
    assert loop.time() - t0 < 0.2 + cs._DRAIN_QUIET_SECONDS + 0.5
    stop.append(1)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


@pytest.mark.asyncio
async def test_the_quiet_window_never_outlasts_the_timeout():
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await cs.drain_detached_settlements(0.1) == 0
    assert loop.time() - t0 < 0.3


@pytest.mark.asyncio
async def test_uvicorns_cancel_then_lifespan_shutdown_order_still_drains_the_refund(caplog):
    """The F1 replay, through uvicorn's OWN `Server.shutdown` (cancel the request tasks at the
    deadline, then `lifespan.shutdown()` at once) and a FastAPI app with three
    `@app.middleware("http")` layers, as main.py has. Before the fix the drain logged done
    before the handler's `finally` had even run."""
    from contextlib import asynccontextmanager

    import uvicorn
    from fastapi import FastAPI
    from uvicorn.lifespan.on import LifespanOn
    from uvicorn.server import Server

    order: list = []
    in_handler = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        order.append("drain_start")
        left = await cs.drain_detached_settlements(timeout=5.0)   # main.py's first drain
        order.append(f"drain_done:{left}")

    mini = FastAPI(lifespan=lifespan)

    @mini.get("/charged")
    async def charged():
        try:
            in_handler.set()
            await asyncio.Event().wait()         # a charged generation still running
        finally:
            order.append("handler_finally")
            # What every door's `finally` does with the refund a cancelled frame owes.
            cs.settle_detached(lambda: order.append("refund_ran"), what="report_refund AAPL:f1")

    for _ in range(3):                           # main.py stacks three http middlewares
        @mini.middleware("http")
        async def _hop(request, call_next):
            return await call_next(request)

    config = uvicorn.Config(mini, lifespan="on", log_config=None, timeout_graceful_shutdown=0.05)
    config.load()
    server = Server(config)
    server.servers = []
    server.lifespan = LifespanOn(config)
    await server.lifespan.startup()
    assert not server.lifespan.startup_failed

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/charged",
        "raw_path": b"/charged", "query_string": b"", "root_path": "",
        "server": ("testserver", 80), "client": ("127.0.0.1", 1),
        "headers": [(b"host", b"testserver")],
    }
    state = {"sent_body": False}

    async def receive():
        if not state["sent_body"]:
            state["sent_body"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        pass

    request_task = asyncio.create_task(config.loaded_app(scope, receive, send))
    server.server_state.tasks.add(request_task)
    request_task.add_done_callback(server.server_state.tasks.discard)
    await asyncio.wait_for(in_handler.wait(), 2.0)

    with caplog.at_level(logging.INFO):
        await server.shutdown()      # uvicorn's order: cancel at the deadline, then lifespan
    assert "refund_ran" in order, order
    assert order.index("handler_finally") < order.index("refund_ran") < order.index("drain_done:0"), order
    assert not any("REFUND LEAK" in r.getMessage() for r in caplog.records)
    assert request_task.done()


# ── a task cancelled AFTER its worker thread started is not a lost refund ──────────────


async def _cancel_after_thread_start(fn_after_gate, what):
    gate, in_thread = threading.Event(), threading.Event()

    def _settle():
        in_thread.set()
        gate.wait(timeout=5)
        return fn_after_gate()

    cs.settle_detached(_settle, what=what)
    (task,) = tuple(cs._DETACHED_SETTLEMENTS)
    for _ in range(200):
        if in_thread.is_set():
            break
        await asyncio.sleep(0.01)
    assert in_thread.is_set()
    task.cancel()                     # e.g. `asyncio.run`'s teardown
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)            # done-callbacks
    gate.set()


async def _until_logged(caplog, needle):
    for _ in range(300):
        if any(needle in r.getMessage() for r in caplog.records):
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_task_cancelled_after_its_thread_started_is_not_paged_as_a_leak(caplog):
    ran = []
    with caplog.at_level(logging.INFO, logger=cs.logger.name):
        await _cancel_after_thread_start(lambda: ran.append(1), "report_refund AAPL:r10")
        await _until_logged(caplog, "finished after its task was cancelled")
    mine = [r for r in caplog.records if "AAPL:r10" in r.getMessage()]
    assert ran == [1]
    assert not any("REFUND LEAK" in r.getMessage() for r in mine), [r.getMessage() for r in mine]
    assert any(r.levelno == logging.WARNING and "after the worker thread started" in r.getMessage()
               for r in mine)
    assert any("finished after its task was cancelled" in r.getMessage() for r in mine)


@pytest.mark.asyncio
async def test_a_raise_in_a_thread_whose_task_was_cancelled_is_still_logged(caplog):
    def _boom():
        raise RuntimeError("refund_credits RPC died")

    with caplog.at_level(logging.INFO, logger=cs.logger.name):
        await _cancel_after_thread_start(_boom, "report_refund AAPL:r11")
        await _until_logged(caplog, "raised after its task was cancelled")
    hits = [r for r in caplog.records if "AAPL:r11" in r.getMessage()
            and "raised after its task was cancelled" in r.getMessage()]
    assert hits and hits[0].levelno == logging.ERROR and "RuntimeError" in hits[0].getMessage()


# ── charge_off_loop: a charge whose waiter was cancelled is handed back ────────────────


async def _cancel_while_charging(result=None, raises=None):
    gate, abandoned = threading.Event(), []

    def _charge():
        gate.wait(timeout=5)
        if raises is not None:
            raise raises
        return result

    waiter = asyncio.create_task(cs.charge_off_loop(
        _charge, on_abandoned=lambda r, e: abandoned.append((r, e, threading.get_ident())),
        what="report_charge AAPL:r1",
    ))
    await asyncio.sleep(0.02)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.set()
    assert await cs.drain_detached_settlements(2.0) == 0, "the hand-back was not drained"
    return abandoned


@pytest.mark.asyncio
async def test_a_charge_that_lands_after_its_waiter_was_cancelled_is_handed_back():
    loop_thread = threading.get_ident()
    abandoned = await _cancel_while_charging(result=80)
    assert [(r, e) for r, e, _t in abandoned] == [(80, None)]
    assert abandoned[0][2] != loop_thread, "the hand-back ran on the event loop"


@pytest.mark.asyncio
async def test_an_unconfirmed_charge_after_a_cancel_reaches_the_hand_back_with_its_error():
    abandoned = await _cancel_while_charging(raises=cs.CreditServiceUnavailable("edge 520"))
    assert len(abandoned) == 1 and isinstance(abandoned[0][1], cs.CreditServiceUnavailable)


@pytest.mark.asyncio
async def test_a_charge_whose_waiter_was_not_cancelled_never_calls_the_hand_back():
    abandoned = []
    out = await cs.charge_off_loop(lambda: 80, on_abandoned=lambda *a: abandoned.append(a),
                                   what="x")
    assert out == 80 and abandoned == []
    with pytest.raises(cs.CreditServiceUnavailable):
        await cs.charge_off_loop(
            lambda: (_ for _ in ()).throw(cs.CreditServiceUnavailable("x")),
            on_abandoned=lambda *a: abandoned.append(a), what="x",
        )
    assert abandoned == [], "an observed outcome must be settled by its caller, not twice"
