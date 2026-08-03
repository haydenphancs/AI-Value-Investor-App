"""Two auth holes found by the adversarial review, both in `app/dependencies.py`.

--- 1. Password-change eviction was missing on the DOMINANT auth path ---

Migration 105 added `users.password_changed_at` so a reset evicts a thief: the app mints its
own JWTs, so signature+expiry alone would keep a stolen token alive for the full
ACCESS_TOKEN_EXPIRE_MINUTES (24h) after the victim changed their password.

The check was wired into `get_current_user` and into `POST /auth/refresh` — but NOT into
`get_current_user_or_guest`, which is what most authenticated routes actually depend on
(chat, ticker reports, research, analytics, users/me/credits) plus everything reached through
`get_watchlist_identity` and `get_learn_identity` (watchlist, portfolios, home, learn, updates).
Migration 105's own header claimed it "covers every endpoint that resolves a full user row";
it did not. A stolen token kept reading and writing the victim's data — and spending their
credits — for up to 24h after the reset.

--- 2. A REFRESH token was accepted as an access credential ---

`create_access_token` stamps `"type": "access"`; `create_refresh_token` stamps
`"type": "refresh"` (app/core/security.py). Both are signed with the same `SECRET_KEY`, and
nothing checked the claim. So the refresh token — the one credential deliberately scoped to
exchange-only — authenticated every route. Worse on two counts: it lives 7 days rather than 24
hours, and `POST /auth/refresh` is the ONE place enforcing `password_changed_at`, so using the
refresh token directly skipped that check entirely.

No network / Supabase — the users read is stubbed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.core.security import create_access_token, create_refresh_token
from app.dependencies import (
    GUEST_USER_ID,
    _decode_access_token,
    get_current_user_or_guest,
)

_USER_ID = "11111111-2222-4333-8444-555555555555"


class _UsersTable:
    """Minimal stand-in for `supabase.table("users").select("*").eq(...).limit(1).execute()`."""

    def __init__(self, row):
        self._row = row

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": [dict(self._row)] if self._row else []})()


class _SB:
    def __init__(self, row):
        self._row = row

    def table(self, _name):
        return _UsersTable(self._row)


def _row(password_changed_at=None):
    row = {"id": _USER_ID, "email": "victim@example.com", "tier": "free"}
    if password_changed_at is not None:
        row["password_changed_at"] = password_changed_at
    return row


def _bearer(token: str) -> str:
    return f"Bearer {token}"


# ---------------------------------------------------------------------------
# 1. Password-change eviction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stolen_token_is_rejected_after_a_password_change():
    """THE bug: this path used to hand the row straight back."""
    token = create_access_token({"sub": _USER_ID, "email": "victim@example.com"})
    changed_after = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(
            authorization=_bearer(token), supabase=_SB(_row(changed_after))
        )
    assert exc.value.status_code == 401
    assert "password was changed" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_token_issued_after_the_change_still_works():
    """The eviction must not lock out the legitimate user who just reset."""
    changed_before = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token = create_access_token({"sub": _USER_ID, "email": "victim@example.com"})

    user = await get_current_user_or_guest(
        authorization=_bearer(token), supabase=_SB(_row(changed_before))
    )
    assert user["id"] == _USER_ID


@pytest.mark.asyncio
async def test_account_that_never_changed_its_password_is_unaffected():
    token = create_access_token({"sub": _USER_ID, "email": "victim@example.com"})
    user = await get_current_user_or_guest(
        authorization=_bearer(token), supabase=_SB(_row(None))
    )
    assert user["id"] == _USER_ID


@pytest.mark.asyncio
async def test_a_malformed_password_changed_at_fails_OPEN():
    """Deliberate posture: a parse quirk must not lock a legitimate user out of their account."""
    token = create_access_token({"sub": _USER_ID, "email": "victim@example.com"})
    user = await get_current_user_or_guest(
        authorization=_bearer(token), supabase=_SB(_row("not-a-timestamp"))
    )
    assert user["id"] == _USER_ID


@pytest.mark.asyncio
async def test_naive_password_changed_at_is_treated_as_utc():
    """Postgres can hand back a naive string; comparing it to an aware dt would raise and
    fail open, silently disabling the eviction."""
    token = create_access_token({"sub": _USER_ID, "email": "victim@example.com"})
    naive_future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()

    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(
            authorization=_bearer(token), supabase=_SB(_row(naive_future))
        )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# 2. Refresh token must not authenticate
# ---------------------------------------------------------------------------


def test_refresh_token_is_refused_as_an_access_credential():
    from jose import JWTError

    refresh = create_refresh_token({"sub": _USER_ID})
    with pytest.raises(JWTError):
        _decode_access_token(refresh)


def test_access_token_still_decodes():
    access = create_access_token({"sub": _USER_ID, "email": "victim@example.com"})
    payload = _decode_access_token(access)
    assert payload["sub"] == _USER_ID
    assert payload["type"] == "access"


@pytest.mark.asyncio
async def test_refresh_token_does_not_authenticate_a_request():
    """End to end: presenting the refresh token as a Bearer credential must not resolve the
    account. It used to return the real user row.

    The rejection is now LOUD. These two tests previously asserted `user["id"] ==
    GUEST_USER_ID` — a silent demotion to the shared guest with a 200 — and that quiet
    fallback turned out to be the largest single source of app-wide auth unreliability: a
    signed-in user whose token died was served the guest's watchlist, reports and credits with
    no signal, so the client never learned to refresh. The security property under test is
    unchanged and strictly stronger: the refresh token still cannot authenticate as the real
    user, and now the caller is told so.
    """
    refresh = create_refresh_token({"sub": _USER_ID})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(
            authorization=_bearer(refresh), supabase=_SB(_row(None))
        )
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "AUTH_TOKEN_INVALID"
    # The one thing that must never happen: resolving to the account the token names.
    assert _USER_ID not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_garbage_token_is_rejected_not_silently_guested():
    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(
            authorization=_bearer("not.a.jwt"), supabase=_SB(_row(None))
        )
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_a_bearer_header_with_no_credential_is_a_guest_not_a_bad_token():
    """`Authorization: Bearer` with nothing after it is ABSENCE, not a failed credential.

    The distinction decides whether the client keeps its Keychain entry: AUTH_TOKEN_INVALID
    tells it to refresh and possibly discard, AUTH_REQUIRED tells it it was never signed in.
    Treating an empty credential as invalid would hand a token-less caller a reason to wipe
    a credential it does not have.
    """
    user = await get_current_user_or_guest(
        authorization="Bearer   ", supabase=_SB(_row(None))
    )
    assert user["id"] == GUEST_USER_ID


@pytest.mark.asyncio
async def test_a_non_bearer_scheme_is_a_guest():
    """Basic auth is not a credential this app issues; it is not a BAD bearer token."""
    user = await get_current_user_or_guest(
        authorization="Basic dXNlcjpwYXNz", supabase=_SB(_row(None))
    )
    assert user["id"] == GUEST_USER_ID


@pytest.mark.asyncio
async def test_lowercase_bearer_scheme_is_honoured():
    """RFC 7235 makes the scheme case-insensitive. The old `startswith("Bearer ")` check
    silently treated `bearer x` as no credential at all — a signed-in caller served guest
    data."""
    token = create_access_token({"sub": _USER_ID, "email": "victim@example.com"})
    user = await get_current_user_or_guest(
        authorization=f"bearer {token}", supabase=_SB(_row(None))
    )
    assert user["id"] == _USER_ID


@pytest.mark.asyncio
async def test_no_authorization_header_is_a_guest():
    user = await get_current_user_or_guest(authorization=None, supabase=_SB(_row(None)))
    assert user["id"] == GUEST_USER_ID


def test_helper_is_not_recursive():
    """Guards a mistake made while writing this fix: a search-and-replace turned the helper's
    own `decode_token` call into a call to itself, which is an unbounded recursion on the very
    first authenticated request. It parses and imports fine — only execution reveals it."""
    access = create_access_token({"sub": _USER_ID})
    assert _decode_access_token(access)["sub"] == _USER_ID
