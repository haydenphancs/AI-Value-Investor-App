"""`database.run_gotrue`: GoTrue verbs run OFF the loop, serialised, service_role re-asserted.

The sign-in verbs used to run synchronously on the single uvicorn worker's loop BY DESIGN —
the loop's serialisation was what kept supabase-py's auth-state listener (which rewrites the
process-wide client's shared `Authorization` header on every sign-in) from interleaving two
requests on one client. The cost: an unauthenticated caller with a handful of addresses and
wrong passwords (a bcrypt round trip each, ~0.4-0.9 s) stalled EVERY in-flight request in
the process — chat streams, report polls, credit reads — with nothing in the logs but INFO.

`run_gotrue` keeps the serialisation (one asyncio.Lock per loop) and moves the round trip to
a worker thread, re-asserting service_role INSIDE the lock right before the verb.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.config import settings
from app.database import run_gotrue


class _Owner:
    def __init__(self, headers):
        self._headers = headers
        self._in_memory_session = object()
        self.calls = []

    def verb(self, *a, **k):
        self.calls.append((threading.current_thread().name, dict(self._headers), a, k))
        # What the SDK's listener does on SIGNED_IN.
        self._headers["Authorization"] = "Bearer USER_JWT"
        return "session"


@pytest.mark.asyncio
async def test_the_verb_runs_in_a_worker_thread_not_on_the_loop():
    owner = _Owner({"Authorization": "Bearer stale"})
    main = threading.current_thread().name
    out = await run_gotrue(owner.verb, {"email": "a@b.co"}, k=1)
    assert out == "session"
    thread, headers, args, kwargs = owner.calls[0]
    assert thread != main, "the GoTrue verb still ran on the event-loop thread"
    assert args == ({"email": "a@b.co"},) and kwargs == {"k": 1}


@pytest.mark.asyncio
async def test_service_role_is_reasserted_inside_the_lock_before_the_verb():
    """A sign-in that completed between dependency resolution and this verb left a user JWT
    on the shared dict; the verb must not send it."""
    owner = _Owner({"Authorization": "Bearer USER_JWT"})
    await run_gotrue(owner.verb)
    _, headers_seen, _, _ = owner.calls[0]
    assert headers_seen["Authorization"] == f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"
    assert owner._in_memory_session is None, "the previous signer's session survived"


@pytest.mark.asyncio
async def test_concurrent_verbs_are_serialised_and_each_starts_from_service_role():
    """Two sign-ins in flight at once must not interleave: each sees service_role, never the
    other's JWT, and their bodies never overlap in time."""
    shared = {"Authorization": "Bearer service"}
    spans = []

    class _Slow(_Owner):
        def verb(self, who):
            start = time.monotonic()
            seen = self._headers["Authorization"]
            time.sleep(0.05)
            self._headers["Authorization"] = f"Bearer {who}_JWT"
            spans.append((who, seen, start, time.monotonic()))
            return who

    a, b = _Slow(shared), _Slow(shared)
    await asyncio.gather(run_gotrue(a.verb, "A"), run_gotrue(b.verb, "B"))
    assert len(spans) == 2
    for who, seen, *_ in spans:
        assert seen == f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}", (who, seen)
    (w1, _, s1, e1), (w2, _, s2, e2) = sorted(spans, key=lambda t: t[2])
    assert e1 <= s2, f"{w1} and {w2} overlapped — the lock is not serialising the verbs"


@pytest.mark.asyncio
async def test_the_loop_stays_free_while_a_verb_blocks():
    """The whole point: a slow GoTrue round trip must not stall unrelated coroutines."""
    class _Blocking(_Owner):
        def verb(self):
            time.sleep(0.2)
            return "ok"

    ticks = []

    async def _heartbeat():
        for _ in range(4):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.03)

    t0 = time.monotonic()
    await asyncio.gather(run_gotrue(_Blocking({}).verb), _heartbeat())
    # Four heartbeats ~30 ms apart landed INSIDE the 200 ms verb — the loop was not held.
    inside = [t for t in ticks if t - t0 < 0.19]
    assert len(inside) >= 3, f"heartbeats were starved while the verb ran: {ticks}"


@pytest.mark.asyncio
async def test_a_fake_without_sdk_internals_is_simply_run():
    """Test doubles (MagicMock, a bare function) have no `_headers`; they must not crash."""
    from unittest.mock import MagicMock

    m = MagicMock(return_value=42)
    assert await run_gotrue(m, 1, x=2) == 42
    m.assert_called_once_with(1, x=2)

    def bare(v):
        return v * 2
    assert await run_gotrue(bare, 21) == 42


@pytest.mark.asyncio
async def test_a_raising_verb_propagates_its_own_exception():
    class _Boom(Exception):
        pass

    def verb():
        raise _Boom("User not allowed")

    with pytest.raises(_Boom):
        await run_gotrue(verb)


def test_every_gotrue_verb_in_auth_py_goes_through_run_gotrue():
    """Source pin: a new `.auth.<verb>(...)` call in an async handler re-opens the stall.
    `_NonExecuteFinder` in test_crud_paths_off_the_event_loop.py enforces the same thing
    structurally; this names the contract from the other side."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/auth.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    direct = re.findall(r"\.auth\.(?:admin\.)?[a-z_]+\(", code)
    assert not direct, f"direct GoTrue calls in auth.py: {direct}"
    assert code.count("await run_gotrue(") >= 11
