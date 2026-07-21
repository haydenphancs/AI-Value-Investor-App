"""
Tests for the shared `_inflight` dedup future in the two Learn content services
(`money_moves_content_service.py` / `journey_content_service.py` — deliberately byte-identical
plumbing, so every test here runs against BOTH).

The endpoints are public, uncached-on-cold-start (3600 s TTL, no lifespan pre-warm) and are also
awaited UNSHIELDED by `chat_context_resolver`, which wraps the whole resolve in
`asyncio.wait_for(..., timeout=4.0)`. That makes cancellation a routine event on the leader AND the
waiter side, and the naive dedup shape broke in two ways:

  1. A cancelled WAITER cancelled the SHARED future (awaiting a future propagates the awaiter's
     cancellation into it). The leader's later `set_result` then raised `InvalidStateError` — a 500
     on a request whose data had loaded fine.
  2. A cancelled LEADER never resolved the future (`CancelledError` is a `BaseException`, so
     `except Exception` missed it) while `finally` cleared the slot — every joined waiter then
     awaited a future nobody would ever complete, with no timeout anywhere in the stack.

No network / Supabase: `_load` is replaced with a gated fake. `asyncio.wait_for(..., timeout=2)`
(or `asyncio.wait`) guards every spot where a regression would HANG, so a hang fails the test
instead of blocking the suite.
"""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.journey import JourneyLessonResponse, JourneyResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services.journey_content_service import JourneyContentService
from app.services.money_moves_content_service import MoneyMovesContentService

# (id, service class, public method name, payload factory)
_SERVICES = [
    (
        "money_moves",
        MoneyMovesContentService,
        "get_money_moves",
        lambda: MoneyMovesResponse(articles=[{"slug": "compounding-101", "title": "Compounding"}]),
    ),
    (
        "journey",
        JourneyContentService,
        "get_journey",
        lambda: JourneyResponse(
            lessons=[JourneyLessonResponse(id="1", title="What is a stock?", level="foundation")]
        ),
    ),
]
_IDS = [s[0] for s in _SERVICES]


@pytest.fixture(autouse=True)
def _reset_service_state():
    """Class-level cache/inflight are process-global — isolate every test, before AND after."""
    def clear():
        for _, cls, _, _ in _SERVICES:
            cls._cache = None
            cls._inflight = None

    clear()
    yield
    clear()


def _install_gated_load(monkeypatch, cls, payload, gate: asyncio.Event, calls: list):
    """Replace `_load` with a fake that parks on `gate` — lets the test hold the leader mid-flight."""

    async def fake_load(self):
        calls.append(1)
        await gate.wait()
        return payload

    monkeypatch.setattr(cls, "_load", fake_load, raising=True)


async def _park(times: int = 3) -> None:
    """Yield to the loop enough times for freshly created tasks to reach their first await."""
    for _ in range(times):
        await asyncio.sleep(0)


# ── 1. Leader + waiters on a cold cache all get the SAME result, from ONE load ──────────────
@pytest.mark.parametrize("name,cls,method,make_payload", _SERVICES, ids=_IDS)
@pytest.mark.asyncio
async def test_waiters_join_single_load(monkeypatch, name, cls, method, make_payload):
    payload = make_payload()
    gate = asyncio.Event()
    calls: list = []
    _install_gated_load(monkeypatch, cls, payload, gate, calls)

    svc = cls()
    leader = asyncio.create_task(getattr(svc, method)())
    await _park()
    waiters = [asyncio.create_task(getattr(svc, method)()) for _ in range(2)]
    await _park()

    gate.set()
    results = await asyncio.wait_for(asyncio.gather(leader, *waiters), timeout=2)

    assert len(calls) == 1, "waiters must JOIN the leader's load, not fire their own"
    assert all(r is payload for r in results), "every caller gets the leader's result"


# ── 2. A cancelled WAITER must not poison the leader ────────────────────────────────────────
@pytest.mark.parametrize("name,cls,method,make_payload", _SERVICES, ids=_IDS)
@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_break_leader(monkeypatch, name, cls, method, make_payload):
    """The real trigger: chat_context_resolver's 4 s `wait_for` cancels a joined caller.

    Without `asyncio.shield`, that cancellation lands on the SHARED future and the leader's
    `set_result` raises `InvalidStateError` — a 500 on content that loaded perfectly.
    """
    payload = make_payload()
    gate = asyncio.Event()
    calls: list = []
    _install_gated_load(monkeypatch, cls, payload, gate, calls)

    svc = cls()
    leader = asyncio.create_task(getattr(svc, method)())
    await _park()
    waiter = asyncio.create_task(getattr(svc, method)())
    await _park()

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    gate.set()
    result = await asyncio.wait_for(leader, timeout=2)   # must NOT raise InvalidStateError
    assert result is payload
    assert cls._cache is not None and cls._cache[1] is payload, "leader still populated the cache"


# ── 3. A cancelled LEADER must release its waiters instead of hanging them forever ──────────
@pytest.mark.parametrize("name,cls,method,make_payload", _SERVICES, ids=_IDS)
@pytest.mark.asyncio
async def test_cancelled_leader_releases_waiters(monkeypatch, name, cls, method, make_payload):
    payload = make_payload()
    gate = asyncio.Event()
    calls: list = []
    _install_gated_load(monkeypatch, cls, payload, gate, calls)

    svc = cls()
    leader = asyncio.create_task(getattr(svc, method)())
    await _park()
    waiter = asyncio.create_task(getattr(svc, method)())
    await _park()

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader

    # The assertion that matters: the waiter FINISHES (however it finishes). A regression here is a
    # request pinned forever on a future nobody will resolve, so a timeout must fail the test.
    done, pending = await asyncio.wait({waiter}, timeout=2)
    assert not pending, "waiter hung on the cancelled leader's unresolved future"
    assert waiter.cancelled() or waiter.exception() is not None or waiter.result() is not None

    # And the slot is clean, so the NEXT caller can lead a fresh load rather than joining a corpse.
    assert cls._inflight is None
    gate.set()


# ── 4. After a cancelled leader, a later caller can still load normally ─────────────────────
@pytest.mark.parametrize("name,cls,method,make_payload", _SERVICES, ids=_IDS)
@pytest.mark.asyncio
async def test_recovers_after_cancelled_leader(monkeypatch, name, cls, method, make_payload):
    payload = make_payload()
    gate = asyncio.Event()
    calls: list = []
    _install_gated_load(monkeypatch, cls, payload, gate, calls)

    svc = cls()
    leader = asyncio.create_task(getattr(svc, method)())
    await _park()
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader

    gate.set()
    result = await asyncio.wait_for(getattr(svc, method)(), timeout=2)
    assert result is payload


# ── 5. A failing load degrades (never raises) and still resolves every waiter ───────────────
@pytest.mark.parametrize("name,cls,method,make_payload", _SERVICES, ids=_IDS)
@pytest.mark.asyncio
async def test_load_failure_degrades_for_leader_and_waiters(monkeypatch, name, cls, method, make_payload):
    """A cold-cache Supabase failure must serve an EMPTY body, not a 500 — to leader and waiters
    alike. iOS then falls back to bundled content (and, since the empty response no longer latches
    its prefetch flag, retries later in the session)."""
    gate = asyncio.Event()

    async def boom(self):
        await gate.wait()
        raise RuntimeError("supabase down")

    monkeypatch.setattr(cls, "_load", boom, raising=True)

    svc = cls()
    leader = asyncio.create_task(getattr(svc, method)())
    await _park()
    waiter = asyncio.create_task(getattr(svc, method)())
    await _park()

    gate.set()
    lead_result, wait_result = await asyncio.wait_for(asyncio.gather(leader, waiter), timeout=2)

    items = lead_result.articles if name == "money_moves" else lead_result.lessons
    assert items == []
    assert wait_result is lead_result
    assert cls._cache is None, "a failed load must never be cached"
