"""
force_refresh auth guard on GET /whales/{id}/profile.

force_refresh DESTRUCTIVELY deletes the durable whale_filing_snapshots store and
drives an unbounded FMP rebuild. The endpoint authenticates via
get_optional_user_id (anonymous allowed), so without a guard an unauthenticated
caller could loop ?force_refresh=true over every whale to wipe snapshots and
drain the shared FMP quota. These tests pin that:
  - anonymous (user_id=None)  -> force_refresh is neutralized, NO deletes fire
  - authenticated (user_id set) -> force_refresh still busts the caches

Pure logic with a chainable fake Supabase shim — no network, no real DB. Run via
`python -m pytest` from backend/.
"""

import asyncio

import pytest

from app.services import whale_service as wsvc
from app.services.whale_service import WhaleService, _whale_profile_cache


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    """Records delete() calls; every chained op returns self; execute() -> []."""

    def __init__(self, name, recorder):
        self.name = name
        self._rec = recorder

    def delete(self):
        self._rec.append((self.name, "delete"))
        return self

    def __getattr__(self, _name):
        # select / eq / order / limit / upsert / update / in_ / etc. all chain.
        def _chain(*args, **kwargs):
            return self
        return _chain

    def execute(self):
        return _Result([])  # whale not found -> build short-circuits to None


class _FakeSB:
    def __init__(self, recorder):
        self._rec = recorder

    def table(self, name):
        return _Table(name, self._rec)


@pytest.fixture(autouse=True)
def _clear_cache():
    _whale_profile_cache.clear()
    yield
    _whale_profile_cache.clear()


def _run_with_fake(monkeypatch, *, user_id, force_refresh):
    recorder: list = []
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _FakeSB(recorder))
    svc = WhaleService.__new__(WhaleService)  # skip __init__ (no FMP client needed)
    result = asyncio.run(
        svc.get_whale_profile("w1", user_id=user_id, force_refresh=force_refresh)
    )
    return result, recorder


def test_anonymous_force_refresh_does_not_delete(monkeypatch):
    result, recorder = _run_with_fake(
        monkeypatch, user_id=None, force_refresh=True
    )
    assert result is None  # whale not found in the fake
    destructive = [
        entry for entry in recorder
        if entry == ("whale_profile_cache", "delete")
        or entry == ("whale_filing_snapshots", "delete")
    ]
    assert destructive == [], f"anonymous force_refresh must not delete, got {recorder}"


def test_authenticated_force_refresh_busts_caches(monkeypatch):
    result, recorder = _run_with_fake(
        monkeypatch, user_id="u1", force_refresh=True
    )
    assert result is None
    # An authenticated operator IS allowed to force a rebuild.
    assert ("whale_profile_cache", "delete") in recorder
    assert ("whale_filing_snapshots", "delete") in recorder
