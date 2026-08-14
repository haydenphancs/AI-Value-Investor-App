"""In-flight dedup for the widget cache, verified by BEHAVIOUR not by source scan.

`test_inflight_cancellation_safety.py` guards this pattern across the codebase, but
its join check is a regex for ``await …_inflight[…]``. `WidgetMoversService._cached`
reads the future with ``.get()`` and then awaits ``asyncio.shield(existing)``, which
that regex cannot see — so that file passes **vacuously** for this module, and its
own docstring warns a source-only guard "would not survive someone simplifying the
shield away".

These are the two defects it describes, exercised against the real service:

1. a cancelled JOINER must not poison the shared future for everyone else;
2. a cancelled LEADER must not strand joiners awaiting forever.

Both are provoked with a controllable builder — no network, no Supabase.
"""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.widget import WidgetMoverPayload
from app.services.widget_movers_service import WidgetMoversService


def _payload(mode: str = "market") -> WidgetMoverPayload:
    return WidgetMoverPayload(
        mode=mode, as_of="2026-08-14T21:50:28Z", market_session="regular"
    )


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_build():
    """Ten widget refreshes landing together must cost one upstream fetch."""
    svc = WidgetMoversService()
    calls = 0
    gate = asyncio.Event()

    async def build():
        nonlocal calls
        calls += 1
        await gate.wait()
        return _payload()

    tasks = [asyncio.create_task(svc._cached("k", build)) for _ in range(10)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1
    assert all(r.mode == "market" for r in results)


@pytest.mark.asyncio
async def test_a_cancelled_joiner_does_not_break_the_other_joiners():
    """Defect 2. A widget timeline that gives up must not fail the other callers.

    TWO surviving joiners, deliberately. With only one, this test passes even
    without the shield: an unshielded joiner's cancellation propagates into the
    shared future and cancels it, but the leader's `if not fut.done()` guard then
    simply skips `set_result`, so the LEADER still returns its own value and a
    single-joiner assertion sees nothing wrong. The damage is only visible to
    somebody parked on the future — which is what `survivor` is.
    """
    svc = WidgetMoversService()
    gate = asyncio.Event()

    async def build():
        await gate.wait()
        return _payload()

    leader = asyncio.create_task(svc._cached("k", build))
    await asyncio.sleep(0)
    quitter = asyncio.create_task(svc._cached("k", build))
    survivor = asyncio.create_task(svc._cached("k", build))
    await asyncio.sleep(0)

    quitter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await quitter

    gate.set()
    assert (await leader).mode == "market"

    # The real assertion: one caller abandoning the request must not cancel a
    # different caller's perfectly healthy one.
    assert not survivor.cancelled()
    assert (await survivor).mode == "market"


@pytest.mark.asyncio
async def test_a_cancelled_leader_does_not_strand_joiners():
    """Defect 1. `CancelledError` is a BaseException, so an `except Exception` arm
    misses it — joiners parked on the future would hang for the process lifetime.

    Asserting `pytest.raises(BaseException)` around `wait_for` would be VACUOUS:
    `asyncio.TimeoutError` is itself a BaseException, so a joiner that hangs
    forever — the exact defect — would satisfy it. The timeout has to be the
    failure condition, not an accepted outcome.
    """
    svc = WidgetMoversService()
    started = asyncio.Event()

    async def build():
        started.set()
        await asyncio.sleep(3600)

    leader = asyncio.create_task(svc._cached("k", build))
    await started.wait()
    joiner = asyncio.create_task(svc._cached("k", build))
    await asyncio.sleep(0)

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader

    try:
        await asyncio.wait_for(asyncio.shield(joiner), timeout=1.0)
    except asyncio.TimeoutError:
        joiner.cancel()
        pytest.fail(
            "the joiner never settled — a cancelled leader stranded it. The leader's "
            "handler must catch BaseException (or CancelledError) and resolve the "
            "shared future; popping the dict only stops NEW joiners."
        )
    except asyncio.CancelledError:
        pass          # settled, which is all this test requires
    assert joiner.done()


@pytest.mark.asyncio
async def test_a_failed_build_is_not_cached():
    """A transient upstream error must not be pinned for the whole TTL — the next
    refresh has to be allowed to try again."""
    svc = WidgetMoversService()
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("upstream blip")
        return _payload()

    with pytest.raises(RuntimeError):
        await svc._cached("k", flaky)

    assert (await svc._cached("k", flaky)).mode == "market"
    assert attempts == 2


@pytest.mark.asyncio
async def test_a_successful_build_is_served_from_memory():
    svc = WidgetMoversService()
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return _payload()

    await svc._cached("k", build)
    await svc._cached("k", build)
    assert calls == 1


@pytest.mark.asyncio
async def test_different_keys_do_not_share_a_build():
    """Market and each distinct portfolio are separate cache entries."""
    svc = WidgetMoversService()
    seen = []

    async def build_for(mode):
        async def _b():
            seen.append(mode)
            return _payload(mode)
        return _b

    await svc._cached("market", await build_for("market"))
    await svc._cached("portfolio:AAPL,MSFT", await build_for("portfolio"))
    assert seen == ["market", "portfolio"]


@pytest.mark.asyncio
async def test_the_inflight_map_is_always_drained():
    """A leaked key would deduplicate against a future nobody will ever resolve."""
    svc = WidgetMoversService()

    async def ok():
        return _payload()

    async def boom():
        raise RuntimeError("x")

    await svc._cached("a", ok)
    with pytest.raises(RuntimeError):
        await svc._cached("b", boom)

    assert svc._inflight == {}
