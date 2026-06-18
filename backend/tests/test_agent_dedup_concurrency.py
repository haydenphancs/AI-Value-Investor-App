"""
Tests for the global bounded-concurrency + same-(ticker, persona) dedup wrapper
`_run_agent_deduped` in app/services/research_service.py.

Structure under test (research_service.py lines ~66-124):
  * `_AGENT_SEMAPHORE` — process-wide cap; at most N agent runs execute
    concurrently. Lazily built by `_get_agent_semaphore()` from
    `settings.MAX_CONCURRENT_AGENT_RUNS` if None.
  * `_AGENT_INFLIGHT` — {key -> Future}; concurrent same-key callers share ONE
    run (a "leader"); "followers" await the leader's Future and return an
    independent deep copy. Followers do NOT acquire a semaphore slot.
  * `except asyncio.CancelledError` branch — when a fire-and-forget leader Task
    is cancelled, the leader hands followers a NORMAL `RuntimeError` (so they
    fail through the standard refund path) instead of leaving the Future
    unresolved and hanging every follower forever, then re-raises to honor its
    own cancellation.

These tests inject fake `run_callable`s inline — no FMP / Gemini / Supabase /
network. Each test resets `_AGENT_INFLIGHT` and pins `_AGENT_SEMAPHORE` to a
known size for isolation. `asyncio.wait_for(..., timeout=2)` guards every spot
where a regression would hang, so a hang fails the test instead of blocking.
"""

from __future__ import annotations

import asyncio
import copy

import pytest

import app.services.research_service as svc

_run_agent_deduped = svc._run_agent_deduped


def _reset(n: int) -> None:
    """Per-test isolation: empty the inflight map and pin the semaphore to N."""
    svc._AGENT_INFLIGHT.clear()
    svc._AGENT_SEMAPHORE = asyncio.Semaphore(n)


# ── 1. Semaphore bounds concurrency across DISTINCT keys ────────────────────
@pytest.mark.asyncio
async def test_semaphore_bounds_concurrency():
    """With N=2 and many DISTINCT (ticker, persona) keys (no dedup sharing),
    the number of run_callables executing at once must never exceed 2."""
    _reset(2)

    live = 0
    peak = 0
    gate = asyncio.Event()

    async def run_callable():
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            # Park inside the critical section so concurrent leaders pile up and
            # the semaphore is forced to gate them. Released by the test below.
            await gate.wait()
            return {"ok": True}
        finally:
            live -= 1

    # 6 distinct keys → 6 leaders, all wanting a slot at once.
    tasks = [
        asyncio.create_task(
            _run_agent_deduped(f"TCK{i}", "warren_buffett", run_callable)
        )
        for i in range(6)
    ]

    # Let the event loop schedule everything; only 2 should get past the
    # semaphore. Yield a few times so all admitted leaders reach `live += 1`.
    for _ in range(20):
        await asyncio.sleep(0)
    assert live == 2, f"semaphore let {live} run; expected 2"
    assert peak == 2

    # Release the gate and let all 6 drain.
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    assert peak == 2, f"observed peak concurrency {peak}, must not exceed 2"
    assert all(r == {"ok": True} for r in results)
    assert svc._AGENT_INFLIGHT == {}


# ── 2. Followers don't consume a semaphore slot ─────────────────────────────
@pytest.mark.asyncio
async def test_followers_do_not_consume_a_slot():
    """With N=1 and 1 leader + many followers on the SAME key, all followers
    resolve from the leader's single run (run_callable called exactly once) and
    none deadlock waiting for the (already-held) single semaphore slot."""
    _reset(1)

    calls = 0
    gate = asyncio.Event()

    async def run_callable():
        nonlocal calls
        calls += 1
        await gate.wait()
        return {"value": 1}

    # Leader first, so it registers the inflight Future before followers attach.
    leader = asyncio.create_task(
        _run_agent_deduped("AAPL", "warren_buffett", run_callable)
    )
    # Let the leader install its Future in _AGENT_INFLIGHT and start running.
    for _ in range(5):
        await asyncio.sleep(0)
    assert "AAPL::warren_buffett" in svc._AGENT_INFLIGHT

    followers = [
        asyncio.create_task(
            _run_agent_deduped("AAPL", "warren_buffett", run_callable)
        )
        for _ in range(10)
    ]
    # Followers must be able to reach `await inflight` without needing a slot.
    for _ in range(5):
        await asyncio.sleep(0)

    # Nothing has resolved yet (leader still parked) but no deadlock either.
    gate.set()
    leader_result = await asyncio.wait_for(leader, timeout=2)
    follower_results = await asyncio.wait_for(
        asyncio.gather(*followers), timeout=2
    )

    assert calls == 1, f"run_callable ran {calls} times; followers should share"
    assert leader_result == {"value": 1}
    assert all(r == {"value": 1} for r in follower_results)
    assert svc._AGENT_INFLIGHT == {}


# ── 3. Leader exception propagates to all followers (no hang) ────────────────
@pytest.mark.asyncio
async def test_leader_exception_propagates_to_followers():
    """If the leader's run_callable raises, the leader AND every follower must
    receive an exception (so each fails + refunds) rather than hanging."""
    _reset(1)

    gate = asyncio.Event()

    class Boom(Exception):
        pass

    async def run_callable():
        await gate.wait()
        raise Boom("agent blew up")

    leader = asyncio.create_task(
        _run_agent_deduped("MSFT", "cathie_wood", run_callable)
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert "MSFT::cathie_wood" in svc._AGENT_INFLIGHT

    followers = [
        asyncio.create_task(
            _run_agent_deduped("MSFT", "cathie_wood", run_callable)
        )
        for _ in range(5)
    ]
    for _ in range(5):
        await asyncio.sleep(0)

    gate.set()

    with pytest.raises(Boom):
        await asyncio.wait_for(leader, timeout=2)

    for f in followers:
        with pytest.raises(Exception):
            await asyncio.wait_for(f, timeout=2)

    assert svc._AGENT_INFLIGHT == {}


# ── 4. REGRESSION: leader cancellation must not hang followers ───────────────
@pytest.mark.asyncio
async def test_leader_cancellation_does_not_hang_followers():
    """The crux of the `except asyncio.CancelledError` branch: when the
    fire-and-forget leader Task is cancelled (Railway redeploy / GC), the leader
    must raise CancelledError, but every follower must get a NORMAL Exception
    (RuntimeError) and resolve — NOT hang forever on `await inflight`."""
    _reset(1)

    started = asyncio.Event()
    park = asyncio.Event()

    async def run_callable():
        started.set()
        # Park forever; the test cancels the leader Task instead of releasing.
        await park.wait()
        return {"never": "returned"}

    leader = asyncio.create_task(
        _run_agent_deduped("NVDA", "peter_lynch", run_callable)
    )
    # Wait until the leader is genuinely inside run_callable (slot acquired,
    # Future installed) so cancellation hits the awaited critical section.
    await asyncio.wait_for(started.wait(), timeout=2)
    assert "NVDA::peter_lynch" in svc._AGENT_INFLIGHT

    followers = [
        asyncio.create_task(
            _run_agent_deduped("NVDA", "peter_lynch", run_callable)
        )
        for _ in range(5)
    ]
    # Let followers reach `await inflight` on the leader's Future.
    for _ in range(5):
        await asyncio.sleep(0)

    # Cancel the leader Task — simulates the fire-and-forget task being killed.
    leader.cancel()

    # Leader honors its own cancellation.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(leader, timeout=2)

    # Followers must each raise a REGULAR exception (RuntimeError), not hang and
    # not propagate CancelledError. wait_for's timeout converts a hang into a
    # TimeoutError test failure.
    for f in followers:
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(f, timeout=2)

    assert svc._AGENT_INFLIGHT == {}


# ── 5. Followers get independent deep copies ────────────────────────────────
@pytest.mark.asyncio
async def test_follower_deepcopy_independence():
    """Each caller's returned dict is an independent deep copy — mutating one
    caller's result does not bleed into another's (each stamps its own
    persona-weighted quality_score downstream)."""
    _reset(1)

    gate = asyncio.Event()
    shared_payload = {"nested": {"score": 1}, "list": [1, 2, 3]}

    async def run_callable():
        await gate.wait()
        # Return the SAME object every time — the wrapper is responsible for
        # handing followers independent copies.
        return shared_payload

    leader = asyncio.create_task(
        _run_agent_deduped("TSLA", "bill_ackman", run_callable)
    )
    for _ in range(5):
        await asyncio.sleep(0)

    followers = [
        asyncio.create_task(
            _run_agent_deduped("TSLA", "bill_ackman", run_callable)
        )
        for _ in range(3)
    ]
    for _ in range(5):
        await asyncio.sleep(0)

    gate.set()
    leader_result = await asyncio.wait_for(leader, timeout=2)
    f0, f1, f2 = await asyncio.wait_for(asyncio.gather(*followers), timeout=2)

    # Every follower copy must be distinct from the others (deep-copied).
    assert f0 is not f1 and f1 is not f2 and f0 is not f2
    assert f0["nested"] is not f1["nested"]

    # Mutate one follower's nested dict; the others must be untouched.
    f0["nested"]["score"] = 999
    f0["list"].append(4)
    assert f1["nested"]["score"] == 1
    assert f2["nested"]["score"] == 1
    assert f1["list"] == [1, 2, 3]
    assert leader_result["nested"]["score"] == 1  # leader's own copy unaffected

    assert svc._AGENT_INFLIGHT == {}


# ── 6. _AGENT_INFLIGHT is empty after completion ────────────────────────────
@pytest.mark.asyncio
async def test_inflight_cleared_after_completion():
    """After a single leader run resolves, its key is removed from
    _AGENT_INFLIGHT (the `finally: pop` cleanup), so a later request re-leads
    instead of awaiting a stale, already-resolved Future."""
    _reset(1)

    async def run_callable():
        return {"done": True}

    # First run.
    r1 = await asyncio.wait_for(
        _run_agent_deduped("GOOG", "warren_buffett", run_callable), timeout=2
    )
    assert r1 == {"done": True}
    assert svc._AGENT_INFLIGHT == {}, "key not cleaned up after leader finished"

    # A subsequent run with the same key must re-lead (fresh run_callable call),
    # proving the prior Future was popped rather than reused.
    calls = 0

    async def run_callable_2():
        nonlocal calls
        calls += 1
        return {"done": 2}

    r2 = await asyncio.wait_for(
        _run_agent_deduped("GOOG", "warren_buffett", run_callable_2), timeout=2
    )
    assert r2 == {"done": 2}
    assert calls == 1
    assert svc._AGENT_INFLIGHT == {}
