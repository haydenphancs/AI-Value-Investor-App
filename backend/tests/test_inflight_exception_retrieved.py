"""A failed in-flight leader must not leave an unread exception on its shared future.

Every cache-aside service parks an ``asyncio.Future`` in an ``_inflight`` dict so concurrent
callers join one upstream fetch. On failure the leader stores the exception on the future
for the joiners AND re-raises it itself. With no joiner (the common single-request case)
nobody reads the stored exception, and when the future is collected asyncio logs "Future
exception was never retrieved" at ERROR, which Sentry turns into a second event for a
failure the leader already reported.

``app.utils.inflight.fail_shared_future`` stores the exception and marks it retrieved. These
tests drive real leaders (coingecko, a service, the module-level ``_deduped`` shape), prove
joiners still receive the exception, and scan ``app/`` so a new leader cannot bring the bare
``set_exception`` back.
"""

from __future__ import annotations

import ast
import asyncio
import gc
from pathlib import Path

import pytest

from app.utils.inflight import fail_shared_future

_APP = Path(__file__).resolve().parents[1] / "app"


def _capture_loop_errors(loop):
    seen: list = []
    loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
    return seen


def _never_retrieved(seen) -> list:
    return [c for c in seen if "never retrieved" in str(c.get("message", ""))]


async def _run_unjoined_failure(coro_factory, expected_exc):
    """Run one leader with no joiner, swallow its exception, then force GC of the future."""
    loop = asyncio.get_running_loop()
    seen = _capture_loop_errors(loop)
    try:
        caught = None
        try:
            await coro_factory()
        except expected_exc as e:  # noqa: PERF203 - the failure is the point
            caught = type(e)
        assert caught is not None, "the leader did not fail; the test proves nothing"
        # The except-block name is deleted on exit, so the traceback (and the leader frame
        # holding the future) is only reachable through reference cycles now.
        for _ in range(3):
            gc.collect()
            await asyncio.sleep(0)
        return _never_retrieved(seen)
    finally:
        loop.set_exception_handler(None)


# ── the harness and the helper ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_harness_detects_an_unretrieved_future():
    """Positive control: the old idiom IS reported, so the tests below are not vacuous."""
    loop = asyncio.get_running_loop()
    seen = _capture_loop_errors(loop)
    try:
        fut = loop.create_future()
        fut.set_exception(RuntimeError("bare"))
        del fut
        gc.collect()
        assert _never_retrieved(seen), "the harness did not observe asyncio's GC report"
    finally:
        loop.set_exception_handler(None)


@pytest.mark.asyncio
async def test_helper_marks_retrieved_and_a_shielded_joiner_still_raises():
    loop = asyncio.get_running_loop()
    seen = _capture_loop_errors(loop)
    try:
        fut = loop.create_future()
        joiner = asyncio.ensure_future(asyncio.shield(fut))
        await asyncio.sleep(0)
        assert fail_shared_future(fut, ValueError("upstream 503")) is True
        with pytest.raises(ValueError, match="upstream 503"):
            await joiner

        lone = loop.create_future()
        assert fail_shared_future(lone, ValueError("nobody joined")) is True
        del fut, lone, joiner
        gc.collect()
        assert _never_retrieved(seen) == []
    finally:
        loop.set_exception_handler(None)


@pytest.mark.asyncio
async def test_helper_is_a_no_op_on_a_settled_future():
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    done.set_result("value")
    assert fail_shared_future(done, RuntimeError("late")) is False
    assert done.result() == "value"

    cancelled = loop.create_future()
    cancelled.cancel()
    assert fail_shared_future(cancelled, RuntimeError("late")) is False
    assert cancelled.cancelled()


@pytest.mark.asyncio
async def test_helper_passes_a_cancelled_error_through_unchanged():
    """The `except BaseException` leaders hand joiners a CancelledError as-is; the helper
    must neither convert it nor raise while marking it retrieved."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    assert fail_shared_future(fut, asyncio.CancelledError()) is True
    assert isinstance(fut.exception(), asyncio.CancelledError)
    assert not fut.cancelled()


# ── real leaders ──────────────────────────────────────────────────────────────


def _coingecko_client(monkeypatch, request_once):
    from app.integrations import coingecko as cg

    client = cg.CoinGeckoClient()
    monkeypatch.setattr(client, "_request_once", request_once)
    monkeypatch.setattr(client, "_BACKOFF_BASE_SECONDS", 0.0)
    # The retry arm logs the exception object as a %-arg. pytest's log capture keeps that
    # LogRecord, which pins exception -> traceback -> leader frame -> future past the test,
    # so the GC report could never fire and the retry-arm test would pass vacuously.
    monkeypatch.setattr(cg.logger, "disabled", True)
    return cg, client


@pytest.mark.asyncio
async def test_coingecko_retry_exhaustion_is_not_reported_twice(monkeypatch):
    """The `set_exception(last)` arm: every attempt fails transiently, no joiner."""
    from app.integrations.coingecko import CoinGeckoUnavailableException

    async def _always_503(endpoint, params):
        raise CoinGeckoUnavailableException(f"{endpoint}: upstream 503")

    _, client = _coingecko_client(monkeypatch, _always_503)
    leaks = await _run_unjoined_failure(
        lambda: client._make_request("coins/markets", {"ids": "bitcoin"}),
        CoinGeckoUnavailableException,
    )
    assert leaks == [], "coingecko left an unread exception on its in-flight future"
    assert client._inflight == {}


@pytest.mark.asyncio
async def test_coingecko_permanent_error_is_not_reported_twice(monkeypatch):
    """The `except BaseException` arm: a permanent 4xx raises on the first attempt."""
    from app.integrations.coingecko import CoinGeckoException

    async def _http_404(endpoint, params):
        raise CoinGeckoException(f"{endpoint}: HTTP 404")

    _, client = _coingecko_client(monkeypatch, _http_404)
    leaks = await _run_unjoined_failure(
        lambda: client._make_request("coins/nope"), CoinGeckoException,
    )
    assert leaks == []


@pytest.mark.asyncio
async def test_coingecko_joiner_still_receives_the_leaders_exception(monkeypatch):
    from app.integrations.coingecko import CoinGeckoException

    release = asyncio.Event()

    async def _slow_404(endpoint, params):
        await release.wait()
        raise CoinGeckoException(f"{endpoint}: HTTP 404")

    _, client = _coingecko_client(monkeypatch, _slow_404)
    leader = asyncio.ensure_future(client._make_request("coins/x"))
    await asyncio.sleep(0)
    joiner = asyncio.ensure_future(client._make_request("coins/x"))
    await asyncio.sleep(0)
    release.set()
    for task in (leader, joiner):
        with pytest.raises(CoinGeckoException, match="HTTP 404"):
            await task


def _profit_power_service(monkeypatch, build):
    from app.services import profit_power_service as pp

    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.fmp = None
    svc.supabase = None
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    monkeypatch.setattr(svc, "_build_profit_power", build)
    pp._inflight.clear()
    pp._cache.clear()
    return pp, svc


@pytest.mark.asyncio
async def test_service_leader_failure_is_not_reported_twice(monkeypatch):
    """profit_power is the reference template every snapshot service copies."""

    async def _boom(ticker):
        raise RuntimeError("FMP fan-out exploded")

    pp, svc = _profit_power_service(monkeypatch, _boom)
    leaks = await _run_unjoined_failure(lambda: svc.get_profit_power("AAPL"), RuntimeError)
    assert leaks == []
    assert pp._inflight == {}


@pytest.mark.asyncio
async def test_service_joiner_still_receives_the_leaders_exception(monkeypatch):
    release = asyncio.Event()

    async def _slow_boom(ticker):
        await release.wait()
        raise RuntimeError("FMP fan-out exploded")

    _, svc = _profit_power_service(monkeypatch, _slow_boom)
    leader = asyncio.ensure_future(svc.get_profit_power("AAPL"))
    for _ in range(3):
        await asyncio.sleep(0)
    joiner = asyncio.ensure_future(svc.get_profit_power("AAPL"))
    for _ in range(3):
        await asyncio.sleep(0)
    release.set()
    for task in (leader, joiner):
        with pytest.raises(RuntimeError, match="fan-out exploded"):
            await task


@pytest.mark.asyncio
async def test_module_level_deduped_leader_is_not_reported_twice():
    """The `except BaseException` shape (technical_analysis._deduped)."""
    from app.services import technical_analysis_service as ta

    async def _boom():
        raise RuntimeError("indicator build failed")

    ta._inflight.clear()
    leaks = await _run_unjoined_failure(
        lambda: ta._deduped("AAPL:1d", _boom), RuntimeError,
    )
    assert leaks == []


# ── source guard: no new bare `set_exception` ────────────────────────────────


def _unread_set_exception_sites(tree: ast.AST) -> list:
    """`X.set_exception(...)` statements with no later `X.exception()` in the same block."""
    offenders = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block):
                if not (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and stmt.value.func.attr == "set_exception"
                ):
                    continue
                target = ast.unparse(stmt.value.func.value)
                marked = any(
                    isinstance(later, ast.Expr)
                    and isinstance(later.value, ast.Call)
                    and isinstance(later.value.func, ast.Attribute)
                    and later.value.func.attr == "exception"
                    and ast.unparse(later.value.func.value) == target
                    for later in block[i + 1:]
                )
                if not marked:
                    offenders.append(stmt.lineno)
    return offenders


def test_guard_flags_the_bare_idiom():
    """Positive control for the scan below."""
    bare = "if not fut.done():\n    fut.set_exception(e)\n"
    marked = bare + "    fut.exception()\n"
    other = bare + "    other.exception()\n"
    assert _unread_set_exception_sites(ast.parse(bare)) == [2]
    assert _unread_set_exception_sites(ast.parse(marked)) == []
    assert _unread_set_exception_sites(ast.parse(other)) == [2]


def test_no_leader_leaves_a_set_exception_unread():
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line in _unread_set_exception_sites(tree):
            offenders.append(f"{path.relative_to(_APP.parent)}:{line}")
    assert offenders == [], (
        "set_exception on a shared future without marking it retrieved; with no joiner "
        "asyncio logs 'Future exception was never retrieved' at ERROR (a second Sentry "
        "event). Use app.utils.inflight.fail_shared_future: " + ", ".join(offenders)
    )
