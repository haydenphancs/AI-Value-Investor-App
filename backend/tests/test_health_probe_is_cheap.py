"""`/health`'s Supabase probe must be cheap and pooled — it is unauthenticated (F24-4).

It used to open a fresh `httpx.AsyncClient` (a TLS handshake to Supabase) per hit and GET
the PostgREST ROOT, which makes PostgREST build the whole ~556 KB OpenAPI schema document
server-side on every call — a curl loop against `/health` amplified into the database's
API layer. Now: a HEAD on one tiny table (`limit=1`, `Prefer: count=none`) over one shared
pool, closed in the lifespan teardown next to the integration clients.
"""

from __future__ import annotations

import pytest

import app.database as db


class _Resp:
    def __init__(self, status):
        self.status_code = status


class _Client:
    def __init__(self, status=200, raise_exc=None):
        self.calls = []
        self.status = status
        self.raise_exc = raise_exc
        self.closed = False

    async def head(self, url, headers=None):
        self.calls.append(("HEAD", url, dict(headers or {})))
        if self.raise_exc:
            raise self.raise_exc
        return _Resp(self.status)

    async def get(self, *a, **k):
        raise AssertionError("the probe must not GET (the root GET builds the schema doc)")

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(db, "_health_http", None)
    monkeypatch.setattr(db, "get_supabase", lambda: object())
    yield
    db._health_http = None


@pytest.mark.asyncio
async def test_the_probe_is_a_bounded_head_on_a_small_table_not_the_schema_root(monkeypatch):
    client = _Client(200)
    monkeypatch.setattr(db, "_get_health_client", lambda: client)
    assert await db.check_supabase_health() is True
    method, url, headers = client.calls[0]
    assert method == "HEAD"
    assert url.endswith(db._HEALTH_PROBE_PATH)
    assert "/rest/v1/agent_personas" in url and "limit=1" in url
    assert not url.rstrip("/").endswith("/rest/v1"), "the PostgREST root builds the OpenAPI doc"
    assert headers.get("Prefer") == "count=none"


@pytest.mark.asyncio
async def test_a_206_counts_as_healthy_and_a_500_or_exception_does_not(monkeypatch):
    monkeypatch.setattr(db, "_get_health_client", lambda: _Client(206))
    assert await db.check_supabase_health() is True
    monkeypatch.setattr(db, "_get_health_client", lambda: _Client(500))
    assert await db.check_supabase_health() is False
    monkeypatch.setattr(db, "_get_health_client", lambda: _Client(raise_exc=RuntimeError("dns")))
    assert await db.check_supabase_health() is False


def test_the_client_is_pooled_and_closable():
    a = db._get_health_client()
    b = db._get_health_client()
    assert a is b, "a fresh client per hit is a TLS handshake per hit"
    import asyncio
    asyncio.run(db.close_health_client())
    assert db._health_http is None


def test_the_lifespan_teardown_closes_it():
    import inspect
    import app.main as main_mod

    src = inspect.getsource(main_mod.lifespan)
    assert "await close_health_client()" in src
