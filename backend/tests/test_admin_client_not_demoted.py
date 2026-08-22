"""`auth.admin.*` must never run on a client that a sign-in has demoted.

THE BUG (Sentry python-fastapi 7687048937, 2026-08-22): `DELETE /api/v1/users/me` answered
`AuthApiError('User not allowed')` and the account survived in BOTH `auth.users` and
`public.users` — the user was told their account was deleted and it was not. App Store
Guideline 5.1.1(v) requires in-app deletion to work.

IT IS ALL ONE DICT. `SyncClient.__init__` hands `self.options.headers` by REFERENCE to the
GoTrue client, which hands the same reference on to `SyncGoTrueAdminAPI`. Measured on
supabase 2.16.0 / gotrue 2.12.4:

    options.headers is auth._headers  -> True
    auth._headers  is admin._headers  -> True

So `_listen_to_auth_events` rewriting `Authorization` on SIGNED_IN rewrites it for
`auth.admin.*` too, and `SyncGoTrueBaseAPI._request` sends `{**self._headers, ...}` with no
per-call `jwt` on `delete_user` / `update_user_by_id`. GoTrue refuses `/admin/*` under a user
JWT with `User not allowed`.

`get_auth_client()` already kept sign-ins off the SERVICE-ROLE client. What was missing is
keeping them off the ADMIN path, and the blast radius was wider than the Sentry issue showed —
two of the three sites demote the client INSIDE THEIR OWN REQUEST, so they failed on every
call, not occasionally:

    change-password : sign_in_with_password (SIGNED_IN)          -> admin.update_user_by_id
    reset-password  : verify_otp (recovery returns a session)    -> admin.update_user_by_id
    delete-account  : any earlier sign-in in the process         -> admin.delete_user

Neither password route classified `User not allowed`, so both surfaced as a bare 500.

No existing test covered the SEQUENCE — sign in, and THEN make an admin call. That is what
this file pins, at two levels: against the real SDK objects, and through the three handlers
with a fake that reproduces the aliasing.

No network: the SDK clients are built against a throwaway URL and never issue a request.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.database as db
from app.api.v1.endpoints import auth as auth_ep
from app.api.v1.endpoints import users as users_ep
from app.schemas.auth import ChangePasswordRequest, ResetPasswordRequest

_URL = "https://example.supabase.co"
_KEY = "SERVICE_ROLE_FAKE"
_SERVICE = f"Bearer {_KEY}"
_USER_JWT = "Bearer USER_A_JWT"
_USER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
_EMAIL = "someone@example.com"
# Compliant with `_validate_password_strength`; a weak literal fails at SCHEMA construction
# and every test in the file dies before reaching what it asserts.
_OLD_PASSWORD = "Correct horse battery 1!"
_NEW_PASSWORD = "A-different-password1!"


@pytest.fixture(autouse=True)
def _fake_settings(monkeypatch):
    """Real SDK clients, fake credentials, no network.

    The three singletons are reset around every test — this file BUILDS them, so a leftover
    from a neighbouring module (or from this one) would make the assertions read stale state.
    """
    monkeypatch.setattr(
        db, "settings",
        SimpleNamespace(SUPABASE_URL=_URL, SUPABASE_SERVICE_ROLE_KEY=_KEY),
    )
    monkeypatch.setattr(db, "_supabase_client", None)
    monkeypatch.setattr(db, "_auth_client", None)
    monkeypatch.setattr(db, "_admin_client", None)
    yield


def _sign_in_on(client, token="USER_A_JWT"):
    """Demote `client` the way a real sign-in does — through gotrue's OWN emitter.

    Not by calling supabase's listener directly: the point is that the path from
    `sign_in_with_password` to the rewritten header is intact end to end.
    """
    client.auth._notify_all_subscribers("SIGNED_IN", SimpleNamespace(access_token=token))


def _admin_header(client) -> str:
    """What `auth.admin.*` would actually put on the wire."""
    return client.auth.admin._headers["Authorization"]


# ---------------------------------------------------------------------------
# 1. The SDK-level property, in the exact order that broke production
# ---------------------------------------------------------------------------


def test_a_sign_in_does_not_demote_the_admin_client():
    """THE regression. Sign in first, THEN look at the admin client — the sequence no test
    covered, and the one the Railway process lives in permanently."""
    auth_client = db.get_auth_client()
    admin_client = db.get_admin_client()

    _sign_in_on(auth_client)

    assert _admin_header(auth_client) == _USER_JWT, (
        "the SDK stopped rewriting the header on sign-in — re-check whether the separate "
        "admin client is still load-bearing"
    )
    assert _admin_header(admin_client) == _SERVICE, (
        "admin.* would go out under a USER's JWT — GoTrue answers /admin/* with "
        "'User not allowed' and account deletion silently fails"
    )


def test_the_admin_client_is_a_third_distinct_object():
    """Isolation is the SEPARATE INSTANCE; the header re-assert is only the healing layer."""
    service, auth_client, admin_client = (
        db.get_supabase(), db.get_auth_client(), db.get_admin_client()
    )
    assert len({id(service), id(auth_client), id(admin_client)}) == 3


def test_the_admin_client_is_memoized():
    """A new client per request would leak a socket pool on every call."""
    assert db.get_admin_client() is db.get_admin_client()


def test_the_admin_client_self_heals_if_something_does_demote_it():
    """Belt and braces: even if a future call signs in here, the NEXT resolution is clean
    again. The source-scan guard is what stops it happening in the first place."""
    admin_client = db.get_admin_client()
    _sign_in_on(admin_client)
    assert _admin_header(admin_client) == _USER_JWT  # demoted...

    assert _admin_header(db.get_admin_client()) == _SERVICE  # ...and repaired on re-resolution


def test_the_auth_client_starts_every_request_at_service_role():
    """One caller's (possibly expired) JWT must not ride along on the next caller's sign-in,
    and the last signer's Session must not be readable on the next caller's request."""
    auth_client = db.get_auth_client()
    _sign_in_on(auth_client)
    auth_client.auth._in_memory_session = SimpleNamespace(access_token="USER_A_JWT")

    refreshed = db.get_auth_client()
    assert refreshed.options.headers["Authorization"] == _SERVICE
    assert refreshed.auth._in_memory_session is None


def test_the_service_role_client_is_untouched_by_a_sign_in_elsewhere():
    """The property `get_auth_client` was introduced for, re-pinned now that a third client
    shares the pattern."""
    service = db.get_supabase()
    _sign_in_on(db.get_auth_client())
    assert service.options.headers["Authorization"] == _SERVICE


def test_sdk_canary_the_admin_api_reads_the_dict_we_write():
    """`_reset_to_service_role` writes `options.headers`, which is only the right place while
    the SDK keeps aliasing it into the admin API. If a supabase-py bump de-aliases them the
    re-assert lands in the wrong dict and does nothing — say so here rather than in prod."""
    client = db.get_admin_client()
    assert client.options.headers is client.auth._headers
    assert client.auth._headers is client.auth.admin._headers


# ---------------------------------------------------------------------------
# 2. The three handlers, through a fake that reproduces the aliasing
# ---------------------------------------------------------------------------


class _NotAllowed(RuntimeError):
    """What GoTrue answers to /admin/* under a user JWT."""


class _FakeAdmin:
    def __init__(self, headers, log):
        self._headers, self._log = headers, log

    def _guard(self, entry):
        if self._headers["Authorization"] != _SERVICE:
            raise _NotAllowed("User not allowed")
        self._log.append(entry)

    def update_user_by_id(self, uid, attrs):
        self._guard(("admin.update_user_by_id", uid, sorted(attrs)))

    def delete_user(self, uid, should_soft_delete=False):
        self._guard(("admin.delete_user", uid))


class _FakeAuth:
    def __init__(self, headers, log):
        self._headers, self._log = headers, log
        # THE ALIASING. One dict, shared with admin — exactly what supabase-py does.
        self.admin = _FakeAdmin(headers, log)

    def _demote(self):
        """What the SDK's auth-state listener does on SIGNED_IN."""
        self._headers["Authorization"] = _USER_JWT

    def sign_in_with_password(self, creds):
        self._log.append(("sign_in_with_password", creds["email"]))
        self._demote()

    def verify_otp(self, params):
        self._log.append(("verify_otp", params.get("type")))
        self._demote()
        return SimpleNamespace(user=SimpleNamespace(id=_USER_ID))


class _FakeQuery:
    def __init__(self, log, table, rows):
        self._log, self._table, self._rows, self._update = log, table, rows, None

    def select(self, *_a, **_k):
        return self

    def update(self, values):
        self._update = values
        return self

    def eq(self, *_a):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._update is not None:
            self._log.append(("table.update", self._table, sorted(self._update)))
            return SimpleNamespace(data=[{}])
        return SimpleNamespace(data=self._rows)


class _AliasedSupabase:
    """A supabase-py client, modelled at the one detail that matters: auth and auth.admin
    share ONE headers dict, so a sign-in on `auth` re-authenticates `admin`."""

    def __init__(self):
        self.log: list[tuple] = []
        self.headers = {"Authorization": _SERVICE}
        self.auth = _FakeAuth(self.headers, self.log)

    def table(self, name):
        return _FakeQuery(self.log, name, [{"email": _EMAIL}])


class _FakeRequest:
    def __init__(self, ip="203.0.113.77"):
        self.client = type("C", (), {"host": ip})()


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """These routes are rate limited per IP AND per user; the limiter is process-wide."""
    from app.core.security import rate_limiter
    rate_limiter.clear()
    yield


@pytest.fixture
def _no_purges(monkeypatch):
    """`delete_account`'s storage/table purges are covered by
    test_account_deletion_completeness.py. Here only the auth step is under test."""
    monkeypatch.setattr(users_ep, "_purge_research_pdfs", lambda *a, **k: None)
    monkeypatch.setattr(users_ep, "_purge_avatars", lambda *a, **k: None)
    monkeypatch.setattr(users_ep, "_purge_unlinked_rows", lambda *a, **k: {})
    yield


async def _change_password(demoted, admin):
    return await auth_ep.change_password(
        ChangePasswordRequest(current_password=_OLD_PASSWORD, new_password=_NEW_PASSWORD),
        _FakeRequest(),
        user_id=_USER_ID,
        supabase=demoted,
        auth_client=demoted,
        admin_client=admin,
    )


async def _reset_password(demoted, admin):
    return await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_NEW_PASSWORD),
        _FakeRequest(),
        supabase=demoted,
        auth_client=demoted,
        admin_client=admin,
    )


async def _delete_account(demoted, admin):
    return await users_ep.delete_account(
        user={"id": _USER_ID}, supabase=demoted, auth_client=demoted, admin_client=admin,
    )


@pytest.mark.asyncio
async def test_change_password_runs_admin_on_the_undemoted_client():
    """`sign_in_with_password` three statements earlier demotes the auth client IN THIS
    REQUEST — so this route failed on every single call before the fix."""
    demoted, admin = _AliasedSupabase(), _AliasedSupabase()
    result = await _change_password(demoted, admin)

    assert result.user_id == _USER_ID
    assert demoted.headers["Authorization"] == _USER_JWT, "the fake never demoted — vacuous test"
    assert ("admin.update_user_by_id", _USER_ID, ["password"]) in admin.log


@pytest.mark.asyncio
async def test_change_password_would_fail_on_the_demoted_client():
    """The mutation half. Without it the test above passes on a handler that merely ACCEPTS an
    `admin_client` parameter and still uses the wrong one."""
    demoted = _AliasedSupabase()
    with pytest.raises(HTTPException) as exc:
        await _change_password(demoted, demoted)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_reset_password_runs_admin_on_the_undemoted_client():
    """`verify_otp` with type=recovery returns a session, so gotrue emits SIGNED_IN — the
    reason a verified reset code still 500'd."""
    demoted, admin = _AliasedSupabase(), _AliasedSupabase()
    await _reset_password(demoted, admin)

    assert demoted.headers["Authorization"] == _USER_JWT, "the fake never demoted — vacuous test"
    assert ("admin.update_user_by_id", _USER_ID, ["password"]) in admin.log


@pytest.mark.asyncio
async def test_reset_password_would_fail_on_the_demoted_client():
    demoted = _AliasedSupabase()
    with pytest.raises(HTTPException) as exc:
        await _reset_password(demoted, demoted)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_delete_account_runs_admin_on_the_undemoted_client(_no_purges):
    """The Sentry case: a sign-in ANYWHERE earlier in the process, then the delete."""
    demoted, admin = _AliasedSupabase(), _AliasedSupabase()
    demoted.auth.sign_in_with_password({"email": _EMAIL, "password": _OLD_PASSWORD})

    assert await _delete_account(demoted, admin) == {"deleted": True}
    assert ("admin.delete_user", _USER_ID) in admin.log


@pytest.mark.asyncio
async def test_delete_account_would_fail_on_the_demoted_client(_no_purges):
    """Reproduces the production failure exactly: a 500 whose body says the account is still
    there, which is what the user actually received."""
    import json

    demoted = _AliasedSupabase()
    demoted.auth.sign_in_with_password({"email": _EMAIL, "password": _OLD_PASSWORD})

    res = await _delete_account(demoted, demoted)
    assert res.status_code == 500
    assert json.loads(res.body)["error_code"] == "ACCOUNT_DELETE_INCOMPLETE"
