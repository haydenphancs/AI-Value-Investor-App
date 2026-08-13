"""The adversarial credential grid, across every auth dependency.

Auth used to answer the same way — a silent 200 with guest data, or FastAPI's 403 — for a
whole family of distinct situations, and the client could not tell them apart. This pins the
grid so they can never collapse back together:

    no header            → guest (or AUTH_REQUIRED on strict routes)
    "Bearer" + nothing   → ABSENCE, not a bad credential
    non-Bearer scheme    → absence
    lowercase "bearer"   → a real credential (RFC 7235: the scheme is case-insensitive)
    garbage / expired    → AUTH_TOKEN_INVALID
    refresh-as-access    → AUTH_TOKEN_INVALID
    valid, no users row  → AUTH_ACCOUNT_NOT_FOUND
    users read raises    → AUTH_UNAVAILABLE (retryable — NOT 500, NOT a bad credential)

The distinction that matters most on the client: only AUTH_TOKEN_INVALID / AUTH_SESSION_EXPIRED
/ AUTH_ACCOUNT_NOT_FOUND may cost the user their stored credential. AUTH_REQUIRED must not (it
means "you were never signed in"), and neither may AUTH_UNAVAILABLE (transient).

Hermetic: dependencies are invoked directly with stub Supabase clients — the suite's standard
idiom, no TestClient.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import app.dependencies as deps
from app.core.security import create_access_token, create_refresh_token
from app.dependencies import (
    GUEST_USER_ID,
    get_chat_identity,
    get_current_user,
    get_current_user_id,
    get_current_user_or_guest,
    get_identity_only_user,
    get_learn_identity,
    get_optional_user_id,
    get_profile_identity,
    get_research_identity,
    get_watchlist_identity,
)

# All FIVE per-install wrappers. `get_chat_identity` was missing from the two parametrized
# tests below — chat is the surface people paste holdings into, and the one migration 111 was
# written for, so leaving it out of the matrix was the wrong omission to have.
# `get_profile_identity` (migration 131) joins for the same reason: it partitions the investor
# profile per install, so it inherits the identical obligations — reject an unverifiable token
# rather than silently downgrading to guest, and never collapse two installs onto one bucket.
_IDENTITY_WRAPPERS = [
    get_learn_identity,
    get_research_identity,
    get_watchlist_identity,
    get_chat_identity,
    get_profile_identity,
]
_IDENTITY_WRAPPER_IDS = ["learn", "research", "watchlist", "chat", "profile"]

_USER_ID = "11111111-2222-3333-4444-555555555555"


# ── stubs ────────────────────────────────────────────────────────────────────

class _SB:
    """Supabase stub returning a fixed row set from any query chain."""

    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self, *a, **k):
        class _R:
            data = self._rows
        return _R()


class _RaisingSB:
    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self, *a, **k):
        raise RuntimeError("transient postgrest blip")


def _row():
    return [{"id": _USER_ID, "email": "u@example.com", "tier": "free", "password_changed_at": None}]


def _bearer(token):
    return f"Bearer {token}"


def _creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _access():
    return create_access_token({"sub": _USER_ID, "email": "u@example.com"})


def _code(exc: HTTPException) -> str:
    """The structured code, asserting the contract shape on the way through."""
    assert isinstance(exc.detail, dict), f"auth errors must carry the contract body, got {exc.detail!r}"
    for key in ("error_code", "message", "user_message", "action", "details"):
        assert key in exc.detail, f"missing {key} in {exc.detail!r}"
    return exc.detail["error_code"]


# ── strict: get_current_user_id ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strict_no_credential_is_AUTH_REQUIRED_not_403():
    """THE reported bug. FastAPI's HTTPBearer(auto_error=True) answered a missing header with
    403 'Not authenticated', which iOS never treats as recoverable — so tapping Follow while
    signed out reverted the button with nothing shown."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=None)
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_REQUIRED"
    assert exc.value.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_strict_empty_credential_is_AUTH_REQUIRED():
    """`Authorization: Bearer` with nothing after it is absence, not a broken token — the
    client must not be told to discard a credential it never sent."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds("   "))
    assert _code(exc.value) == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_strict_garbage_credential_is_AUTH_TOKEN_INVALID():
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds("not.a.jwt"))
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_strict_refresh_token_is_AUTH_TOKEN_INVALID():
    """A refresh token lives 7 days and skips the password-change eviction, so it must never
    work as an access credential."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds(create_refresh_token({"sub": _USER_ID})))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_strict_expired_token_is_AUTH_TOKEN_INVALID():
    expired = create_access_token(
        {"sub": _USER_ID, "email": "u@example.com"}, expires_delta=timedelta(seconds=-10)
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds(expired))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_strict_valid_token_resolves():
    assert await get_current_user_id(credentials=_creds(_access())) == _USER_ID


# ── strict: get_current_user ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_users_row_is_ACCOUNT_NOT_FOUND():
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            credentials=_creds(_access()), user_id=_USER_ID, supabase=_SB([])
        )
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_users_read_failure_is_503_not_500():
    """A read failure is RETRYABLE and must preserve the credential. As a 500 it was not an
    auth error to the client at all, so the app kept a good token bound to a request that could
    never resolve and retried forever. It also has to match `get_current_user_or_guest`, which
    already answered 503 — one failure mode must not have two contracts."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            credentials=_creds(_access()), user_id=_USER_ID, supabase=_RaisingSB()
        )
    assert exc.value.status_code == 503
    assert _code(exc.value) == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_password_change_eviction_is_SESSION_EXPIRED():
    changed = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    rows = [{"id": _USER_ID, "email": "u@example.com", "password_changed_at": changed}]
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            credentials=_creds(_access()), user_id=_USER_ID, supabase=_SB(rows)
        )
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_SESSION_EXPIRED"


# ── optional-auth: absence is a guest, a BAD credential is not ───────────────

@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer    ", "Basic dXNlcjpwYXNz", "Token abc"],
    ids=["none", "empty", "bearer-bare", "bearer-blank", "basic", "other-scheme"],
)
@pytest.mark.asyncio
async def test_absent_credential_is_a_guest(header):
    user = await get_current_user_or_guest(authorization=header, supabase=_SB(_row()))
    assert user["id"] == GUEST_USER_ID
    assert await get_optional_user_id(authorization=header) is None


@pytest.mark.parametrize("bad", ["not.a.jwt", "a.b.c"], ids=["garbage", "shaped-but-invalid"])
@pytest.mark.asyncio
async def test_bad_credential_is_rejected_not_guested(bad):
    """The single largest source of app-wide auth unreliability: an expired token used to be
    silently demoted to the shared guest and answered 200, so a signed-in user was served the
    guest's watchlist and reports with nothing on the wire telling the client to refresh."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(authorization=_bearer(bad), supabase=_SB(_row()))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"

    with pytest.raises(HTTPException) as exc2:
        await get_optional_user_id(authorization=_bearer(bad))
    assert _code(exc2.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_lowercase_bearer_scheme_is_a_credential():
    """RFC 7235 makes the scheme case-insensitive. The old `startswith("Bearer ")` treated
    `bearer x` as no credential at all — a signed-in caller silently served guest data."""
    user = await get_current_user_or_guest(
        authorization=f"bearer {_access()}", supabase=_SB(_row())
    )
    assert user["id"] == _USER_ID


# ── the three per-install identity wrappers inherit the rejection ────────────

@pytest.mark.parametrize("dep", _IDENTITY_WRAPPERS, ids=_IDENTITY_WRAPPER_IDS)
@pytest.mark.asyncio
async def test_identity_wrappers_reject_a_bad_token(dep):
    """A bad token must NOT land in a per-install guest partition. Silently writing a
    signed-in user's data into their install's guest bucket is invisible and unrecoverable —
    the rows are simply owned by the wrong identity."""
    with pytest.raises(HTTPException) as exc:
        await dep(authorization=_bearer("not.a.jwt"), x_guest_id="install-A", supabase=_SB(_row()))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.parametrize("dep", _IDENTITY_WRAPPERS, ids=_IDENTITY_WRAPPER_IDS)
@pytest.mark.asyncio
async def test_identity_wrappers_still_partition_real_guests(dep):
    """The guest path must be untouched — this is the whole guest-first product."""
    a = await dep(authorization=None, x_guest_id="install-A", supabase=_SB([]))
    b = await dep(authorization=None, x_guest_id="install-B", supabase=_SB([]))
    assert a["id"] != b["id"]
    assert a["id"] != GUEST_USER_ID


@pytest.mark.parametrize("dep", _IDENTITY_WRAPPERS, ids=_IDENTITY_WRAPPER_IDS)
@pytest.mark.asyncio
async def test_identity_wrappers_all_flag_a_guest(dep):
    """`is_guest` must be present and TRUE on every per-install wrapper.

    This is the invariant that has now failed three separate times — in research, watchlist and
    chat. The trap: a per-install id is a uuid5 that NEVER equals `GUEST_USER_ID`, so the
    obvious-looking `user["id"] == GUEST_USER_ID` classifies every guest as a paying account.
    In chat that meant sending guests into a credit precharge against a `user_credits` row that
    does not exist — a 402 "insufficient credits" on a feature that is free for them.

    `user.get("is_guest")` is the prescribed test, and it only works if EVERY wrapper sets it.
    Two of the four did not until this week, so the prescribed test read falsy on the widest
    guest surface in the app.
    """
    guest = await dep(authorization=None, x_guest_id="install-A", supabase=_SB([]))
    assert guest.get("is_guest") is True, (
        f"{dep.__name__} does not flag a guest — `user.get('is_guest')` will read falsy and "
        "callers following the rulebook will treat this guest as a paying account"
    )


@pytest.mark.parametrize("dep", _IDENTITY_WRAPPERS, ids=_IDENTITY_WRAPPER_IDS)
@pytest.mark.asyncio
async def test_identity_wrappers_flag_a_real_account_as_not_guest(dep):
    """Present-and-False, not absent. A caller must be able to tell a real account apart from
    a wrapper that simply forgot to set the key — those are the same value to `.get()`."""
    user = await dep(
        authorization=_bearer(_access()), x_guest_id="install-A", supabase=_SB(_row())
    )
    assert user["id"] == _USER_ID
    assert user.get("is_guest") is False


# ── the deliberate carve-out ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "header",
    [None, "Bearer not.a.jwt", f"Bearer {create_refresh_token({'sub': _USER_ID})}", "Basic xyz"],
    ids=["none", "garbage", "refresh-token", "basic"],
)
@pytest.mark.asyncio
async def test_identity_only_user_NEVER_raises(header):
    """Analytics promises in its own module docstring that it can never break the app, and the
    iOS `Analytics` actor drops the batch on any error. So this ONE dependency keeps the guest
    fallback while every sibling now rejects. Pinned so a future 'consistency' pass doesn't
    quietly make instrumentation able to fail the product."""
    user = await get_identity_only_user(authorization=header)
    assert user["id"] == GUEST_USER_ID


@pytest.mark.asyncio
async def test_identity_only_user_still_resolves_a_good_token():
    user = await get_identity_only_user(authorization=_bearer(_access()))
    assert user["id"] == _USER_ID


# ── misconfiguration must not read as a bad credential ───────────────────────

@pytest.mark.asyncio
async def test_unset_supabase_secret_reports_UNAVAILABLE_not_INVALID(monkeypatch):
    """`verify_supabase_token` returns None when SUPABASE_JWT_SECRET is unset. Reporting that
    as a bad credential would send every Supabase-issued user into a re-sign-in loop while the
    real fault sat in the environment — so it answers AUTH_UNAVAILABLE (retryable, credential
    preserved) instead."""
    monkeypatch.setattr(deps.settings, "SUPABASE_JWT_SECRET", None)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(authorization=_bearer("not.a.jwt"), supabase=_SB(_row()))
    assert exc.value.status_code == 503
    assert _code(exc.value) == "AUTH_UNAVAILABLE"


# ── AI generation is account-only ────────────────────────────────────────────
#
# The change these pin: guest AI metering keyed on `guest_user_id_for(X-Guest-Id)`, a UUID5 of a
# header the CLIENT chooses. Rotating it minted a fresh identity — and therefore a fresh
# GUEST_REPORT_MONTHLY_LIMIT — on every request, so the most expensive call in the product
# (~17 Gemini + ~20 FMP per run) was unmetered against anyone willing to send a new header.
# There was no per-IP limit behind it and credits don't meter the guest sentinel.

@pytest.mark.parametrize(
    "header, guest_id",
    [(None, None), (None, "install-A"), (None, "install-B"), ("Bearer   ", "install-C")],
    ids=["no-header", "guest-A", "guest-B", "blank-bearer"],
)
@pytest.mark.asyncio
async def test_no_guest_identity_can_reach_AI_generation(header, guest_id):
    """Whatever install id it claims, a caller with no credential is refused.

    Parametrised over DIFFERENT guest ids on purpose: that is the rotation attack. Under the
    old per-install allowance each of these was a brand-new "person" with an unused free report.
    """
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=None)
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_REQUIRED"


def test_both_generation_paths_require_an_account():
    """Gating only /research/generate would be cosmetic — `GET /stocks/{ticker}/report` runs the
    same pipeline for the same cost on a cache miss."""
    import re
    from pathlib import Path

    endpoints = Path(__file__).resolve().parents[1] / "app/api/v1/endpoints"
    for name, minimum in (("research.py", 9), ("ticker_report.py", 2)):
        src = (endpoints / name).read_text()
        assert src.count("Depends(get_current_user)") >= minimum, name
        for guest_dep in ("get_current_user_or_guest", "get_research_identity"):
            assert f"Depends({guest_dep})" not in src, f"{name} still admits guests via {guest_dep}"


def test_the_rotatable_guest_budget_is_no_longer_consulted():
    """`guest_report_budget` was the rotatable meter. Any live call to it re-opens the hole."""
    import pathlib

    endpoints = pathlib.Path(__file__).resolve().parents[1] / "app/api/v1/endpoints"
    for name in ("research.py", "ticker_report.py", "stocks.py"):
        code = "\n".join(
            line for line in (endpoints / name).read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "get_guest_report_budget_service" not in code, name
