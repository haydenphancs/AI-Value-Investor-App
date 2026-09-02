"""An Apple/Google account has NO password. Both password routes must know that.

Reported from TestFlight: *"If users are sign in using gmail or apple account, then they don't
know their password right? Then how they can change password if the don't know the current
password?"*

They could not. Supabase provisions an OAuth account through `sign_in_with_id_token` and never
writes a password, so `auth.users.encrypted_password` is NULL — but `/auth/change-password`
proves the current password by attempting a real `sign_in_with_password`, which GoTrue rejects
as `invalid_credentials`. So the route answered **AUTH_CREDENTIALS_INVALID, "Your current
password is incorrect."** about a password that has never existed, and burned one of five
per-user attempts per 15 minutes doing it.

Two properties are pinned here, and they are opposites on purpose:

- `/auth/change-password` fails **OPEN** on an unknown probe. The current password is still
  demanded, so falling through is no worse than the pre-fix behaviour.
- `/auth/set-password` fails **CLOSED**. Nothing else stands between the caller and the write,
  so proceeding on an unknown answer would let it overwrite an EXISTING password without proving
  the current one — the exact bypass change-password exists to prevent.

No network, no Supabase: the auth client, the admin client and the users table are fakes, and
the `account_auth_methods` RPC is scripted per test.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.auth as auth_ep
from app.core.security import rate_limiter
from app.schemas.auth import ChangePasswordRequest, SetPasswordRequest
from app.services.auth_methods_service import auth_methods_service

_USER_ID = "11111111-2222-4333-8444-555555555555"
_OTHER_ID = "99999999-8888-4777-8666-555555555555"
_EMAIL = "oauth-user@example.com"
_NEW_PASSWORD = "A-different-password1!"


# ── Fakes ────────────────────────────────────────────────────────────────────

class _AuthAdmin:
    def __init__(self, outer):
        self._outer = outer

    def update_user_by_id(self, user_id, payload):
        self._outer.admin_updates.append((user_id, payload))
        return type("R", (), {"user": type("U", (), {"id": user_id})()})()


class _Auth:
    def __init__(self, outer):
        self._outer = outer
        self.admin = _AuthAdmin(outer)

    def sign_in_with_password(self, creds):
        self._outer.sign_ins.append(creds)
        return type("R", (), {"user": type("U", (), {"id": _USER_ID, "email": _EMAIL})()})()

    def verify_otp(self, params):
        self._outer.otp_calls.append(params)
        if self._outer.otp_raises:
            raise RuntimeError("Token has expired or is invalid")
        uid = self._outer.otp_user_id
        if uid is None:
            return type("R", (), {"user": None})()
        return type("R", (), {"user": type("U", (), {"id": uid, "email": _EMAIL})()})()


class _Q:
    def __init__(self, store):
        self.store = store
        self._op = None
        self._payload = None
        # PostgREST returns a bare OBJECT for single() and a LIST for limit(). Both routes read
        # the email with limit(1), so the fake must model the list shape or `rows[0]` would pass
        # here and KeyError in production.
        self._shape = "single"

    def select(self, *_a): self._op = "select"; return self
    def update(self, payload): self._op, self._payload = "update", payload; return self
    def eq(self, *_a): return self
    def limit(self, *_a): self._shape = "list"; return self
    def single(self): self._shape = "single"; return self

    def execute(self):
        if self._op == "update":
            self.store.update(self._payload)
            return type("R", (), {"data": [dict(self.store)]})()
        row = dict(self.store)
        return type("R", (), {"data": row if self._shape == "single" else [row]})()


class _RPC:
    def __init__(self, outer, name, params):
        self._outer, self._name, self._params = outer, name, params

    def execute(self):
        self._outer.rpc_calls.append((self._name, self._params))
        if self._outer.rpc_raises:
            raise RuntimeError("PostgREST is down")
        return type("R", (), {"data": self._outer.rpc_data})()


class _SB:
    """Supabase double. `methods` scripts what `account_auth_methods` returns."""

    def __init__(self, store, methods="absent", rpc_raises=False):
        self.store = store
        self.auth = _Auth(self)
        self.sign_ins: list = []
        self.otp_calls: list = []
        self.admin_updates: list = []
        self.rpc_calls: list = []
        self.rpc_raises = rpc_raises
        self.otp_raises = False
        self.otp_user_id = _USER_ID
        if methods == "absent":
            self.rpc_data = {"has_password": False, "providers": ["apple"]}
        elif methods == "present":
            self.rpc_data = {"has_password": True, "providers": ["email"]}
        elif methods == "unknown":
            self.rpc_data = None  # SQL NULL — no auth.users row
        else:
            self.rpc_data = methods

    def table(self, _n):
        return _Q(self.store)

    def rpc(self, name, params):
        return _RPC(self, name, params)


class _FakeRequest:
    def __init__(self, ip="203.0.113.55"):
        self.client = type("C", (), {"host": ip})()


@pytest.fixture
def store():
    return {"id": _USER_ID, "email": _EMAIL}


@pytest.fixture(autouse=True)
def _clean_state():
    """Both routes are limited per user per 15 min and the limiter is process-wide; the probe
    cache is a module singleton. Either leaking across tests makes a later one fail on state it
    never set up."""
    rate_limiter.clear()
    auth_methods_service._cache.clear()
    yield
    rate_limiter.clear()
    auth_methods_service._cache.clear()


async def _change(sb):
    return await auth_ep.change_password(
        ChangePasswordRequest(current_password="old-password-value",
                              new_password=_NEW_PASSWORD),
        _FakeRequest(), user_id=_USER_ID, supabase=sb,
    )


async def _set(sb, code="123456"):
    return await auth_ep.set_password(
        SetPasswordRequest(code=code, new_password=_NEW_PASSWORD),
        _FakeRequest(), user_id=_USER_ID, supabase=sb,
    )


# ── /auth/change-password ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_password_on_a_passwordless_account_says_so(store):
    """THE regression. Not AUTH_CREDENTIALS_INVALID, which claimed a nonexistent password was
    mistyped."""
    sb = _SB(store, methods="absent")
    with pytest.raises(HTTPException) as exc:
        await _change(sb)

    assert exc.value.status_code == 400
    assert exc.value.detail["error_code"] == "AUTH_PASSWORD_NOT_SET"
    assert exc.value.detail["action"] == "fix_input"
    assert "password" in exc.value.detail["user_message"].lower()


@pytest.mark.asyncio
async def test_change_password_never_attempts_a_sign_in_for_a_passwordless_account(store):
    """The sign-in IS the defect: it is what produced `invalid_credentials`, and it is what the
    per-user attempt limiter is protecting. Neither may run for an account that cannot pass it."""
    sb = _SB(store, methods="absent")
    with pytest.raises(HTTPException):
        await _change(sb)

    assert sb.sign_ins == [], "attempted a password sign-in against an account with no password"


@pytest.mark.asyncio
async def test_change_password_does_not_burn_an_attempt_for_a_passwordless_account(store):
    """The limiter is charged before any work and never refunded, so five taps used to lock the
    user out of a flow that can never succeed. The probe now runs first."""
    sb = _SB(store, methods="absent")
    for _ in range(8):  # comfortably past the 5-per-user / 15-min budget
        with pytest.raises(HTTPException) as exc:
            await _change(sb)
        assert exc.value.detail["error_code"] == "AUTH_PASSWORD_NOT_SET", (
            "fell through to the rate limiter instead of answering honestly"
        )


@pytest.mark.asyncio
async def test_change_password_fails_open_when_the_probe_cannot_answer(store):
    """Unknown must NOT be read as "no password" — an unapplied migration or a PostgREST blip
    would otherwise take the feature away from everyone who does have one."""
    sb = _SB(store, methods="unknown")
    resp = await _change(sb)

    assert resp.access_token
    assert len(sb.sign_ins) == 1, "the legacy current-password check was skipped"


@pytest.mark.asyncio
async def test_change_password_fails_open_when_the_rpc_raises(store):
    sb = _SB(store, methods="absent", rpc_raises=True)
    resp = await _change(sb)

    assert resp.access_token
    assert len(sb.sign_ins) == 1


@pytest.mark.asyncio
async def test_change_password_is_untouched_for_an_account_with_a_password(store):
    sb = _SB(store, methods="present")
    resp = await _change(sb)

    assert resp.access_token and resp.refresh_token
    assert len(sb.sign_ins) == 1
    assert store.get("password_changed_at")


# ── /auth/set-password ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_password_succeeds_for_a_passwordless_account(store):
    sb = _SB(store, methods="absent")
    resp = await _set(sb)

    assert sb.admin_updates == [(_USER_ID, {"password": _NEW_PASSWORD})]
    assert store.get("password_changed_at"), "_mark_password_changed did not stamp the row"
    # Minted after the stamp, so THIS device stays signed in while others are evicted. Dropping
    # them would sign the user out seconds after telling them it worked.
    assert resp.access_token and resp.refresh_token
    assert resp.user_id == _USER_ID


@pytest.mark.asyncio
async def test_set_password_verifies_a_recovery_otp_for_the_accounts_own_email(store):
    """The code is the proof of mailbox control. The email must come from the token's subject,
    never the body, or the OTP check could be aimed at somebody else's mailbox."""
    sb = _SB(store, methods="absent")
    await _set(sb, code="654321")

    assert sb.otp_calls == [{"email": _EMAIL, "token": "654321", "type": "recovery"}]


@pytest.mark.asyncio
async def test_set_password_refuses_when_the_account_already_has_one(store):
    """Otherwise this route is a way to replace a password without proving the current one."""
    sb = _SB(store, methods="present")
    with pytest.raises(HTTPException) as exc:
        await _set(sb)

    assert exc.value.detail["error_code"] == "AUTH_PASSWORD_ALREADY_SET"
    assert sb.admin_updates == []


@pytest.mark.asyncio
async def test_set_password_fails_CLOSED_when_the_probe_cannot_answer(store):
    """The asymmetry with change-password, and the whole reason the RPC returns NULL rather than
    False for an account it cannot see. Proceeding here could overwrite an existing password."""
    sb = _SB(store, methods="unknown")
    with pytest.raises(HTTPException) as exc:
        await _set(sb)

    assert exc.value.status_code == 503
    assert exc.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert sb.admin_updates == []
    assert sb.otp_calls == [], "verified an OTP before deciding it was allowed to write"


@pytest.mark.asyncio
async def test_set_password_fails_closed_when_the_rpc_raises(store):
    sb = _SB(store, methods="absent", rpc_raises=True)
    with pytest.raises(HTTPException) as exc:
        await _set(sb)

    assert exc.value.status_code == 503
    assert sb.admin_updates == []


@pytest.mark.asyncio
async def test_set_password_rejects_an_invalid_code(store):
    sb = _SB(store, methods="absent")
    sb.otp_raises = True
    with pytest.raises(HTTPException) as exc:
        await _set(sb)

    assert exc.value.status_code == 400
    assert sb.admin_updates == []


@pytest.mark.asyncio
async def test_set_password_refuses_an_otp_resolving_to_a_different_account(store):
    """Belt and braces: the email came from this user's own row, so this should be impossible —
    but being wrong means writing a password into somebody else's account."""
    sb = _SB(store, methods="absent")
    sb.otp_user_id = _OTHER_ID
    with pytest.raises(HTTPException) as exc:
        await _set(sb)

    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "AUTH_FORBIDDEN"
    assert sb.admin_updates == []


@pytest.mark.asyncio
async def test_set_password_refuses_an_otp_with_no_user(store):
    sb = _SB(store, methods="absent")
    sb.otp_user_id = None
    with pytest.raises(HTTPException) as exc:
        await _set(sb)

    assert exc.value.status_code == 403
    assert sb.admin_updates == []


@pytest.mark.asyncio
async def test_set_password_invalidates_the_cached_probe(store):
    """`has_password` just flipped. Without the invalidation `GET /users/me` keeps reporting the
    old answer for the whole TTL, and the settings row keeps offering "Set a Password" to someone
    who now has one."""
    sb = _SB(store, methods="absent")
    # Warm the cache the way `GET /users/me` would.
    assert (await auth_methods_service.get(sb, _USER_ID))["has_password"] is False
    assert _USER_ID in auth_methods_service._cache

    await _set(sb)
    assert _USER_ID not in auth_methods_service._cache


@pytest.mark.asyncio
async def test_change_password_invalidates_the_cached_probe(store):
    sb = _SB(store, methods="present")
    assert (await auth_methods_service.get(sb, _USER_ID))["has_password"] is True
    assert _USER_ID in auth_methods_service._cache

    await _change(sb)
    assert _USER_ID not in auth_methods_service._cache


@pytest.mark.asyncio
async def test_a_failed_probe_is_cached_so_it_does_not_hammer_postgrest(store):
    """Measured against production before migration 156 was applied: without this, every
    `GET /users/me` re-hit PostgREST for a function that does not exist — one guaranteed-failing
    round trip on the launch critical path, on every session restore, plus a warning per call.

    Safe to cache because unknown fails OPEN at both call sites: for the TTL the app behaves
    exactly as it did before the feature existed.
    """
    sb = _SB(store, methods="absent", rpc_raises=True)

    assert await auth_methods_service.get(sb, _USER_ID) is None
    assert await auth_methods_service.get(sb, _USER_ID) is None
    assert len(sb.rpc_calls) == 1, (
        f"the failure was not cached — {len(sb.rpc_calls)} RPC round trips for two reads"
    )


@pytest.mark.asyncio
async def test_a_successful_probe_is_cached_too(store):
    sb = _SB(store, methods="present")

    assert (await auth_methods_service.get(sb, _USER_ID))["has_password"] is True
    assert (await auth_methods_service.get(sb, _USER_ID))["has_password"] is True
    assert len(sb.rpc_calls) == 1


@pytest.mark.asyncio
async def test_the_probe_cache_is_bounded(store):
    """`GET /users/me` runs on every session restore and the cache is keyed by user id, so
    nothing but this bound stops a long-lived Railway process growing one entry per distinct
    user, forever."""
    from app.services import auth_methods_service as mod

    sb = _SB(store, methods="present")
    for i in range(mod._MAX_ENTRIES + 200):
        await auth_methods_service.get(sb, f"user-{i}")

    assert len(auth_methods_service._cache) <= mod._MAX_ENTRIES, (
        f"cache grew to {len(auth_methods_service._cache)} entries, unbounded"
    )
