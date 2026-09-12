"""The auth dependency must not touch Supabase on the event loop.

`app/database.py` builds the SYNCHRONOUS supabase-py client and Railway runs ONE uvicorn
worker (`railway.toml`), so every `.execute()` blocks the whole loop for its round trip.
`main.py` records a measured 18.2 s contiguous stall from exactly this class.

`get_current_user` is the worst possible place for it: auth.md §1a puts 136 of 147 routes
behind `.signInRequired`, so this read ran before EVERY authenticated handler. A launch
fan-out of ~14 requests serialised ~14 Railway→Supabase round trips of dead loop before any
handler started, and a Supabase edge stall froze the instance behind whichever request hit
it first. The module had already moved its JWKS fetch off the loop for this exact reason —
the DB read was left behind (found 2026-09-12).

Thread IDENTITY, not a grep for `to_thread`: a source scan passes on a comment
(`.claude/rules/testing.md` §3).
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

import app.dependencies as deps


class _Recorder:
    """Sync Supabase stub that records which thread its .execute() ran on."""

    def __init__(self, rows):
        self.rows, self.threads = rows, []

    def table(self, _n):
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        self.threads.append(threading.get_ident())
        return SimpleNamespace(data=list(self.rows))


def _request(path="/api/v1/home/dashboard"):
    return SimpleNamespace(url=SimpleNamespace(path=path), headers={},
                           client=SimpleNamespace(host="1.2.3.4"), state=SimpleNamespace())


_USER = {"id": "11111111-1111-1111-1111-111111111111", "email": "a@b.c", "tier": "free"}


@pytest.mark.asyncio
async def test_get_current_user_reads_the_users_table_off_the_loop(monkeypatch):
    rec = _Recorder([_USER])
    monkeypatch.setattr(deps, "get_supabase", lambda: rec)
    monkeypatch.setattr(deps, "_user_id_from_token", _fake_token_resolver(_USER["id"]))
    loop_ident = threading.get_ident()

    user = await deps.get_current_user(
        request=_request(),
        credentials=SimpleNamespace(credentials="tok"),
        supabase=rec,
    )
    assert user["id"] == _USER["id"]
    assert rec.threads, "the users read never happened — the test would be vacuous"
    assert all(t != loop_ident for t in rec.threads), (
        "the auth dependency blocked the event loop on a Supabase round trip"
    )


@pytest.mark.asyncio
async def test_get_current_user_or_guest_reads_off_the_loop(monkeypatch):
    rec = _Recorder([_USER])
    monkeypatch.setattr(deps, "get_supabase", lambda: rec)
    monkeypatch.setattr(deps, "_user_id_from_token", _fake_token_resolver(_USER["id"]))
    loop_ident = threading.get_ident()

    await deps.get_current_user_or_guest(
        authorization="Bearer tok",
        supabase=rec,
        request=_request("/api/v1/analytics/events"),
    )
    assert rec.threads and all(t != loop_ident for t in rec.threads)


@pytest.mark.asyncio
async def test_the_loop_keeps_running_while_the_auth_read_is_in_flight(monkeypatch):
    """The property that actually matters: another coroutine progresses during the read."""
    ticks = 0

    class _Slow(_Recorder):
        def execute(self):
            self.threads.append(threading.get_ident())
            threading.Event().wait(0.25)      # a blocking round trip, as the sync SDK does
            return SimpleNamespace(data=[_USER])

    rec = _Slow([_USER])
    monkeypatch.setattr(deps, "get_supabase", lambda: rec)
    monkeypatch.setattr(deps, "_user_id_from_token", _fake_token_resolver(_USER["id"]))

    async def _other():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.01)
            ticks += 1

    await asyncio.gather(
        deps.get_current_user(request=_request(),
                              credentials=SimpleNamespace(credentials="tok"), supabase=rec),
        _other(),
    )
    assert ticks >= 15, (
        f"only {ticks} ticks ran during a 250 ms auth read — the loop was blocked"
    )


def _fake_token_resolver(user_id):
    async def _resolve(token, **kwargs):
        return user_id
    return _resolve
