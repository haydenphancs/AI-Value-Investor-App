"""Changing your password does not sign YOU out.

Migration 105 stamps `users.password_changed_at`, and
`_reject_if_password_changed_since_issue` then 401s any token whose `iat` predates the stamp.
That is what evicts a thief — and it also evicted the caller, because the token authenticating
the change-password request was necessarily minted before the change. The next request 401'd,
`/auth/refresh` rejected the refresh token for the same reason, and the user was signed out
seconds after being told the change succeeded.

`change_password` now returns replacement credentials minted AFTER the stamp.

This is the BEHAVIOURAL proof: it runs the real handler, takes the tokens out of the real
response, and feeds them to the real eviction check against the real stamp. A source-order
assertion (tokens issued after `_mark_password_changed`) is pinned separately in
test_entitlement_and_seed_identity.py; this one would catch a regression that keeps the order
but breaks the property — e.g. reusing the caller's original token, or minting from a cached
`iat`.

No network, no Supabase: the auth client and users table are fakes.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import app.api.v1.endpoints.auth as auth_ep
from app.core.security import decode_token, rate_limiter
from app.dependencies import _reject_if_password_changed_since_issue
from app.schemas.auth import ChangePasswordRequest

_USER_ID = "11111111-2222-4333-8444-555555555555"
_EMAIL = "victim@example.com"


class _AuthAdmin:
    def update_user_by_id(self, *_a, **_k):
        return type("R", (), {"user": type("U", (), {"id": _USER_ID})()})()


class _Auth:
    admin = _AuthAdmin()

    def sign_in_with_password(self, creds):
        # The current password is re-verified for real; any value is "correct" here.
        return type("R", (), {"user": type("U", (), {"id": _USER_ID, "email": _EMAIL})()})()


class _Q:
    def __init__(self, store):
        self.store = store
        self._op = None
        self._payload = None

    def select(self, *_a): self._op = "select"; return self
    def update(self, payload): self._op, self._payload = "update", payload; return self
    def eq(self, *_a): return self
    def limit(self, *_a): return self
    def single(self): return self

    def execute(self):
        if self._op == "update":
            # This is `_mark_password_changed` stamping the row.
            self.store.update(self._payload)
            return type("R", (), {"data": [dict(self.store)]})()
        return type("R", (), {"data": dict(self.store)})()


class _SB:
    def __init__(self, store):
        self.store = store
        self.auth = _Auth()

    def table(self, _n):
        return _Q(self.store)


@pytest.fixture
def store():
    return {"id": _USER_ID, "email": _EMAIL}


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """`change_password` is limited to 5 per user per 15 min, and the limiter is process-wide.
    Every test here changes the same account's password, so without a reset the file exhausts
    its own budget partway through and later tests fail on a 429 they never meant to exercise.
    """
    rate_limiter.clear()
    yield


class _FakeRequest:
    """`change_password` is rate limited now, so it takes the Request to read the client IP."""

    def __init__(self, ip="203.0.113.55"):
        self.client = type("C", (), {"host": ip})()


async def _change(store) -> object:
    return await auth_ep.change_password(
        ChangePasswordRequest(current_password="old-password-value",
                              new_password="a-different-password"),
        _FakeRequest(),
        user_id=_USER_ID,
        supabase=_SB(store),
    )


@pytest.mark.asyncio
async def test_the_returned_access_token_survives_the_eviction_check(store):
    """THE property. The old token would 401 here; the returned one must not."""
    resp = await _change(store)
    changed_at = store.get("password_changed_at")
    assert changed_at, "_mark_password_changed did not stamp the row"

    # Exactly what every authenticated request does next.
    _reject_if_password_changed_since_issue(resp.access_token, {"id": _USER_ID,
                                                               "password_changed_at": changed_at})


@pytest.mark.asyncio
async def test_a_token_minted_before_the_change_is_still_evicted(store):
    """The control. If this passed too, the eviction feature itself would be broken and the
    test above would be meaningless."""
    from datetime import timedelta

    from fastapi import HTTPException
    from jose import jwt

    from app.config import settings

    # Minted in a genuinely EARLIER second. `iat` has whole-second resolution, so the fix
    # deliberately allows a token issued during the same wall-clock second as the change;
    # using `create_access_token()` here would exercise that 1s window, not the eviction.
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    stale = jwt.encode(
        {"sub": _USER_ID, "email": _EMAIL, "type": "access",
         "iat": past, "exp": past + timedelta(hours=1)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    await _change(store)

    with pytest.raises(HTTPException) as exc:
        _reject_if_password_changed_since_issue(
            stale, {"id": _USER_ID, "password_changed_at": store["password_changed_at"]}
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_the_returned_refresh_token_also_postdates_the_stamp(store):
    """`/auth/refresh` applies the same check; a stale refresh token would end the session on
    the next rotation even if the access token survived."""
    resp = await _change(store)
    changed_dt = datetime.fromisoformat(str(store["password_changed_at"]).replace("Z", "+00:00"))
    if changed_dt.tzinfo is None:
        changed_dt = changed_dt.replace(tzinfo=timezone.utc)

    issued = datetime.fromtimestamp(float(decode_token(resp.refresh_token)["iat"]), tz=timezone.utc)
    # Floored, for the same reason the production check floors: `iat` carries whole seconds
    # while the stamp carries microseconds.
    assert issued >= changed_dt.replace(microsecond=0), (
        "refresh token predates the stamp — the session dies on the next rotation"
    )


@pytest.mark.asyncio
async def test_the_tokens_belong_to_the_caller(store):
    """A correct `iat` on somebody else's subject would be worse than no fix."""
    resp = await _change(store)
    assert decode_token(resp.access_token)["sub"] == _USER_ID
    assert decode_token(resp.refresh_token)["sub"] == _USER_ID
    assert resp.user_id == _USER_ID


@pytest.mark.asyncio
async def test_the_access_token_is_an_access_token(store):
    """It must not be a refresh token: `_decode_access_token` rejects those outright, so the
    session would die on the very next request."""
    resp = await _change(store)
    assert decode_token(resp.access_token)["type"] == "access"
    assert decode_token(resp.refresh_token)["type"] == "refresh"


@pytest.mark.asyncio
async def test_the_response_still_carries_the_human_message(store):
    resp = await _change(store)
    assert "password" in resp.message.lower()
