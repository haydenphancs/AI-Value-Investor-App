"""The launch fan-out's routes must not run their service's Supabase call ON the loop.

`get_current_user` moved its `users` read to a worker thread (test_auth_dependency_event_loop.py)
— and then several of the routes it guards ran their OWN synchronous helper right after it:
`ensure_period` on `/users/me/credits`, `get_settings` on `/users/me/settings`,
`get_user_subscription` on `/users/me/subscription`, the investor-profile read, the device
(un)registration, and the two IAP writes on billing. Every app launch fires these together,
so each launch still cost ~4 dead-loop round trips, and a Supabase edge stall (the 520/525
pages) parked the single worker behind them for the full postgrest timeout — the
instance-wide freeze the dependency fix was filed for.

Thread IDENTITY, not a grep for `to_thread`: a source scan passes on a comment.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

_USER = {"id": "11111111-1111-1111-1111-111111111111", "email": "a@b.c", "tier": "free",
         "is_guest": False}


class _Recorder:
    """Sync Supabase stub: records the thread of every `.execute()` / `.rpc().execute()`."""

    def __init__(self, rows=None, rpc_value=None):
        self.rows, self.rpc_value, self.threads = rows if rows is not None else [], rpc_value, []
        self._is_rpc = False

    def table(self, _n):
        return self

    def rpc(self, *_a, **_k):
        self._is_rpc = True
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        self.threads.append(threading.get_ident())
        if self._is_rpc:
            self._is_rpc = False
            return SimpleNamespace(data=self.rpc_value)
        return SimpleNamespace(data=list(self.rows))


def _assert_off_loop(rec: _Recorder, what: str):
    assert rec.threads, f"{what}: the Supabase call never happened — vacuous"
    loop = threading.get_ident()
    assert all(t != loop for t in rec.threads), f"{what} ran on the event-loop thread"


@pytest.mark.asyncio
async def test_credits_rolls_the_period_off_the_loop(monkeypatch):
    from app.api.v1.endpoints import users as ep
    from app.services import credit_service as cs

    rec = _Recorder(rows=[{"user_id": _USER["id"], "credits_remaining": 5, "credits_total": 50}],
                    rpc_value=5)
    monkeypatch.setattr(cs, "get_supabase", lambda: rec)
    await ep.get_user_credits(user=_USER, supabase=rec)
    _assert_off_loop(rec, "ensure_period")


@pytest.mark.asyncio
async def test_settings_read_and_write_run_off_the_loop(monkeypatch):
    from app.api.v1.endpoints import users as ep
    from app.services import user_settings_service as uss

    rec = _Recorder(rows=[{"preferences": {"theme": "dark"}}])
    monkeypatch.setattr(uss, "get_supabase", lambda: rec)
    await ep.get_my_settings(user=_USER)
    _assert_off_loop(rec, "get_settings")

    rec2 = _Recorder(rows=[{"preferences": {"theme": "dark"}}])
    monkeypatch.setattr(uss, "get_supabase", lambda: rec2)
    from app.schemas.settings import UpdateUserSettingsRequest
    await ep.update_my_settings(UpdateUserSettingsRequest(preferences={"appearance_mode": "dark"}), user=_USER)
    _assert_off_loop(rec2, "upsert_settings")


@pytest.mark.asyncio
async def test_subscription_read_runs_off_the_loop(monkeypatch):
    from app.api.v1.endpoints import users as ep
    from app.services import subscription_service as ss

    rec = _Recorder(rows=[])
    monkeypatch.setattr(ss, "get_supabase", lambda: rec)
    await ep.get_my_subscription(user=_USER)
    _assert_off_loop(rec, "get_user_subscription")


@pytest.mark.asyncio
async def test_device_registration_runs_off_the_loop(monkeypatch):
    from app.api.v1.endpoints import users as ep
    from app.services import user_settings_service as uss
    from app.schemas.settings import DeviceRegisterRequest

    rec = _Recorder(rows=[{"token": "t"}])
    monkeypatch.setattr(uss, "get_supabase", lambda: rec)
    req = DeviceRegisterRequest(token="a" * 64, platform="ios", environment="production")
    await ep.register_device(req, user=_USER)
    _assert_off_loop(rec, "register_device")
    rec2 = _Recorder(rows=[{"token": "t"}])
    monkeypatch.setattr(uss, "get_supabase", lambda: rec2)
    await ep.unregister_device(req, user=_USER)
    _assert_off_loop(rec2, "unregister_device")


@pytest.mark.asyncio
async def test_investor_profile_read_runs_off_the_loop(monkeypatch):
    from app.api.v1.endpoints import users as ep
    from app.services import user_investor_profile_service as ups

    rec = _Recorder(rows=[])
    monkeypatch.setattr(ups, "get_supabase", lambda: rec)
    # The module-level accessor caches an instance; bypass it so the recorder is used.
    monkeypatch.setattr(ep, "get_user_investor_profile_service",
                        lambda: ups.UserInvestorProfileService(supabase=rec))
    await ep.get_my_investor_profile(user=_USER)
    _assert_off_loop(rec, "get_profile")
