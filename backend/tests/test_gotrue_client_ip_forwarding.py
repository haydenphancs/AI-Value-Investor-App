"""SEC-7: the end user's address reaches Supabase Auth as `Sb-Forwarded-For`.

Every GoTrue call leaves from the Railway container, so GoTrue's per-IP limits (sign-ins,
`verify`, `token`) were ONE bucket shared by every user: a few junk logins from one attacker
address answered every real user's sign-in with 429 → 503. Supabase documents the fix ("Rate
limits" guide, § Sb-Forwarded-For): a server sends the end user's address in that header, with a
SECRET API key, on a project with IP Address Forwarding enabled, and Auth keys its limits on it.

What these pin:
  * only a VALID single address is ever sent (no header injection, no second value);
  * the header lives in the shared headers dict ONLY for the duration of one locked call, and is
    removed afterwards — on success, on error, and when the awaiting request is cancelled (the
    lock is held until the worker thread actually returns, so the next caller cannot rewrite the
    dict under a request that has not been sent yet);
  * `auth.admin.*` verbs never get it;
  * the FastAPI dependency binds `trusted_client_ip` (the rightmost X-Forwarded-For entry) in the
    request's own task, so the handler — and only that request — sees it.

Hermetic: fake owners stand in for the GoTrue client; the FastAPI app runs in-process over
`httpx.ASGITransport` (no socket).
"""

from __future__ import annotations

import asyncio
import inspect
import threading

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI

from app import database
from app.config import settings
from app.database import (
    GOTRUE_FORWARDED_FOR_HEADER as HDR,
    bind_gotrue_client_ip,
    normalise_client_ip,
    run_gotrue,
)
from app.dependencies import forward_client_ip_to_gotrue


class _GoTrueClient:
    """Stands in for `SyncGoTrueClient`: the SDK builds each request from `self._headers`."""

    def __init__(self, headers=None):
        self._headers = headers if headers is not None else {"Authorization": "Bearer x"}
        self._in_memory_session = None
        self.seen = []

    def sign_in_with_password(self, credentials):
        self.seen.append(dict(self._headers))
        return "session"

    def failing_verb(self):
        self.seen.append(dict(self._headers))
        raise RuntimeError("AuthApiError: 429")


class SyncGoTrueAdminAPI(_GoTrueClient):
    """Name mirrors the SDK's admin class: `_is_admin_api` reads the type name."""

    def update_user_by_id(self, uid, attrs):
        self.seen.append(dict(self._headers))
        return "updated"


def _in_fresh_context(coro_fn, *args):
    """Run in a task with its own copy of the context, like one request under uvicorn."""
    return asyncio.ensure_future(coro_fn(*args))


async def _bound_call(ip, verb, *args):
    bind_gotrue_client_ip(ip)
    return await run_gotrue(verb, *args)


# ── normalise_client_ip ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("203.0.113.9", "203.0.113.9"),
        ("  203.0.113.9 ", "203.0.113.9"),
        ("2001:db8::1", "2001:db8::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:DB8:0:0:0:0:0:1", "2001:db8::1"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_a_valid_address_is_normalised(raw, expected):
    assert normalise_client_ip(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None, "", "   ", "unknown", "testclient", "203.0.113.9, 198.51.100.7",
        "203.0.113.9\r\nX-Injected: 1", "203.0.113.9:443", "999.1.1.1", "0.0.0.0", "::",
        "1" * 100, 12345, b"203.0.113.9", ["203.0.113.9"],
    ],
    ids=[
        "none", "empty", "blank", "unknown", "hostname", "two-values", "crlf-injection",
        "with-port", "out-of-range", "unspecified-v4", "unspecified-v6", "too-long", "int",
        "bytes", "list",
    ],
)
def test_anything_but_one_valid_address_forwards_nothing(raw):
    """GoTrue uses the FIRST value and falls back on an invalid one; sending exactly one
    validated address keeps the header injection-proof and the fallback explicit."""
    assert normalise_client_ip(raw) is None


# ── run_gotrue: the header's lifetime ────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_bound_address_is_sent_and_removed_after_the_call():
    client = _GoTrueClient()
    out = await _in_fresh_context(_bound_call, "203.0.113.9", client.sign_in_with_password, {})
    assert out == "session"
    assert client.seen[0][HDR] == "203.0.113.9"
    assert client.seen[0]["Authorization"] == f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"
    assert HDR not in client._headers, "the address outlived its call on the shared dict"


@pytest.mark.asyncio
async def test_the_header_is_removed_when_the_verb_raises():
    client = _GoTrueClient()
    with pytest.raises(RuntimeError, match="429"):
        await _in_fresh_context(_bound_call, "203.0.113.9", client.failing_verb)
    assert client.seen[0][HDR] == "203.0.113.9"
    assert HDR not in client._headers


@pytest.mark.asyncio
async def test_no_bound_address_sends_no_header_and_clears_a_stale_one():
    """Outside a request (a job, a script) nothing is bound; a header a crashed call left in the
    dict must not ride on this one."""
    client = _GoTrueClient({"Authorization": "Bearer x", HDR: "198.51.100.66"})

    async def _unbound():
        return await run_gotrue(client.sign_in_with_password, {})

    await _in_fresh_context(_unbound)
    assert HDR not in client.seen[0]
    assert HDR not in client._headers


@pytest.mark.asyncio
async def test_an_invalid_bound_address_sends_no_header():
    client = _GoTrueClient()
    await _in_fresh_context(_bound_call, "203.0.113.9, 6.6.6.6", client.sign_in_with_password, {})
    assert HDR not in client.seen[0]


@pytest.mark.asyncio
async def test_admin_verbs_never_carry_the_address():
    """`/admin/*` is not rate-limited by IP, and the admin dict is also read OUTSIDE run_gotrue
    (account deletion), where a leftover header could ride on another user's call."""
    admin = SyncGoTrueAdminAPI()
    await _in_fresh_context(_bound_call, "203.0.113.9", admin.update_user_by_id, "u", {})
    assert HDR not in admin.seen[0]
    assert HDR not in admin._headers


@pytest.mark.asyncio
async def test_a_verb_without_a_headers_dict_still_runs():
    """A test fake / SDK change without `_headers` degrades to a plain call, never a crash."""
    def bare(x):
        return x * 2

    assert await _in_fresh_context(_bound_call, "203.0.113.9", bare, 21) == 42


@pytest.mark.asyncio
async def test_concurrent_requests_each_send_their_own_address():
    """Two requests in flight on ONE shared dict: each call carries its own caller's address
    and never the other's (the lock spans the call; the dict is read inside it)."""
    shared = {"Authorization": "Bearer x"}
    seen = []

    class _Slow(_GoTrueClient):
        def sign_in_with_password(self, who):
            first = self._headers.get(HDR)
            import time
            time.sleep(0.05)
            seen.append((who, first, self._headers.get(HDR)))
            return who

    a, b = _Slow(shared), _Slow(shared)
    await asyncio.gather(
        _in_fresh_context(_bound_call, "203.0.113.1", a.sign_in_with_password, "A"),
        _in_fresh_context(_bound_call, "203.0.113.2", b.sign_in_with_password, "B"),
    )
    assert sorted(seen) == [
        ("A", "203.0.113.1", "203.0.113.1"),
        ("B", "203.0.113.2", "203.0.113.2"),
    ]
    assert HDR not in shared


@pytest.mark.asyncio
async def test_a_cancelled_request_holds_the_lock_until_its_thread_returns():
    """`asyncio.to_thread` cannot stop a running thread. Before this, cancelling the awaiting
    request released the lock mid-call, and the next caller rewrote the shared dict (its
    address, its Authorization reset) under a request that had not been sent yet."""
    shared = {"Authorization": "Bearer x"}
    release = threading.Event()
    entered = threading.Event()
    log = []

    class _Blocking(_GoTrueClient):
        def sign_in_with_password(self, who):
            if who == "A":
                entered.set()
                assert release.wait(5), "test deadlock"
            # Read at the END, i.e. as late as the SDK could build the request.
            log.append((who, self._headers.get(HDR)))
            return who

    a, b = _Blocking(shared), _Blocking(shared)
    task_a = _in_fresh_context(_bound_call, "203.0.113.1", a.sign_in_with_password, "A")
    while not entered.is_set():
        await asyncio.sleep(0.005)
    task_a.cancel()
    task_b = _in_fresh_context(_bound_call, "203.0.113.2", b.sign_in_with_password, "B")
    await asyncio.sleep(0.05)
    assert log == [], "B ran while A's verb was still in flight — the lock was released early"
    assert not task_a.done(), "A returned while its worker thread was still running"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task_a
    assert await task_b == "B"
    assert log == [("A", "203.0.113.1"), ("B", "203.0.113.2")]
    assert HDR not in shared


# ── the FastAPI dependency ───────────────────────────────────────────────────

def test_the_dependency_is_async():
    """A SYNC dependency runs in a threadpool with a COPY of the context: its contextvar set
    would never reach the handler, and forwarding would silently do nothing."""
    assert inspect.iscoroutinefunction(forward_client_ip_to_gotrue)


def _app(record):
    router = APIRouter(dependencies=[Depends(forward_client_ip_to_gotrue)])

    @router.post("/auth/login")
    async def login():
        client = _GoTrueClient()
        await run_gotrue(client.sign_in_with_password, {})
        record.append(("login", database._gotrue_client_ip.get(), client.seen[0].get(HDR)))
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)

    @app.get("/other")
    async def other():
        record.append(("other", database._gotrue_client_ip.get(), None))
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_the_handler_forwards_the_rightmost_x_forwarded_for_entry():
    """The leftmost entry is whatever the caller sent; the RIGHTMOST is the one Railway's edge
    appended (`trusted_client_ip`). Only that one may reach GoTrue's rate limiter."""
    record = []
    transport = httpx.ASGITransport(app=_app(record), client=("10.0.0.5", 4321))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        # One TASK per request, as uvicorn runs them: ASGITransport calls the app inside the
        # caller's task, so without this every request would share one context — a harness
        # artifact, not production behaviour (uvicorn's `create_task(cycle.run_asgi(app))`).
        async def _one(method, path, **kw):
            return await asyncio.ensure_future(http.request(method, path, **kw))

        r = await _one("POST", "/auth/login", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
        assert r.status_code == 200
        r = await _one("POST", "/auth/login")  # no XFF: the connection's own address
        assert r.status_code == 200
        r = await _one("GET", "/other")  # a route without the dependency: nothing bound, no leak
        assert r.status_code == 200
    assert database._gotrue_client_ip.get() is None, "a request's address leaked to its caller"
    assert record == [
        ("login", "203.0.113.9", "203.0.113.9"),
        ("login", "10.0.0.5", "10.0.0.5"),
        ("other", None, None),
    ]


@pytest.mark.asyncio
async def test_a_bad_address_never_fails_the_request():
    record = []
    transport = httpx.ASGITransport(app=_app(record), client=("10.0.0.5", 4321))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        r = await asyncio.ensure_future(
            http.post("/auth/login", headers={"X-Forwarded-For": "not-an-ip"})
        )
    assert r.status_code == 200
    assert record == [("login", None, None)]


@pytest.mark.asyncio
async def test_a_job_started_outside_any_request_forwards_nothing():
    """Lifespan loops are created at startup, before any request bound an address."""
    client = _GoTrueClient()

    async def _job():
        return await run_gotrue(client.sign_in_with_password, {})

    await asyncio.ensure_future(_job())
    assert HDR not in client.seen[0]


def test_a_sub_client_that_snapshotted_the_address_is_cleaned_on_reset():
    """A postgrest/storage session rebuilt from the shared dict while a locked GoTrue call held
    an address keeps a COPY; the per-resolution reset drops it there (and only there)."""
    from types import SimpleNamespace

    bearer = f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"
    shared = {"Authorization": bearer, HDR: "203.0.113.9"}  # a call in flight owns this one
    postgrest = SimpleNamespace(session=SimpleNamespace(
        headers=httpx.Headers({"Authorization": bearer, HDR: "203.0.113.9"})))
    storage = SimpleNamespace(session=SimpleNamespace(
        headers=httpx.Headers({"Authorization": "Bearer USER_JWT", HDR: "203.0.113.9"})))
    client = SimpleNamespace(
        options=SimpleNamespace(headers=shared), auth=None, _postgrest=postgrest, _storage=storage,
    )
    database._reset_to_service_role(client)
    assert HDR not in postgrest.session.headers
    assert HDR not in storage.session.headers
    assert storage.session.headers["Authorization"] == bearer
    assert shared[HDR] == "203.0.113.9", "the shared dict belongs to the locked call in flight"
