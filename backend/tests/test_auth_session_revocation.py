"""Server-side sessions (migration 214): single-use refresh tokens, reuse detection, logout,
sign-out-all, the 60-minute access token, and re-authentication before account deletion.

Audit 2026-11-26: SEC-3 / AUTH-BE-10 (refresh tokens chained forever, logout only logged, no lever
for an Apple/Google account), CC-10 (the token-only market-data gate served a dead session for up
to 24 h) and SEC-11 (account deletion on a bearer alone).

Every session state change runs through `_refresh_session_fake.SessionStoreFake`, a statement-by-
statement model of the 214 SQL. The SQL itself is exercised against a real Postgres by the local
Supabase end-to-end check (SYSTEM_DESIGN_GUIDELINES §9).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt as jose_jwt

from _refresh_session_fake import PgError, SessionStoreFake
from app.api.v1.endpoints import auth as auth_ep
from app.api.v1.endpoints import users as users_ep
from app.config import Settings, settings
from app.core.security import (
    create_access_token, create_reauth_token, create_refresh_token, decode_reauth_token,
    decode_token, rate_limiter,
)
from app.dependencies import (
    _decode_access_token, _reject_if_password_changed_since_issue, get_access_token_claims,
)
from app.schemas.auth import LogoutRequest, ReauthenticateRequest, RefreshTokenRequest
from app.services import auth_session_service as sessions

_UID = "11111111-2222-4333-8444-555555555555"
_OTHER = "99999999-8888-4777-8666-555555555555"
_EMAIL = "owner@example.com"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    rate_limiter.clear()
    # The schema-missing ERROR is throttled per process; reset so each test sees its own line.
    monkeypatch.setattr(sessions, "_last_schema_missing_error", 0.0)
    yield


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _Query:
    """`supabase.table("users")` — enough for the existence and eviction-stamp reads."""

    def __init__(self, db, table):
        self._db, self._table = db, table
        self._id = None
        self._cols = ""
        self._delete = False

    def select(self, *cols, **_):
        self._cols = " ".join(cols)
        return self

    def delete(self):
        self._delete = True
        return self

    def eq(self, col, value):
        if col in ("id", "user_id"):
            self._id = value
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._table == "device_tokens" and self._delete:
            self._db.log.append(("device_tokens.delete", self._id))
            return SimpleNamespace(data=[])
        if "users_read" in self._db.fail:
            raise RuntimeError("users read: connection reset")
        row = self._db.sessions.users.get(self._id)
        return SimpleNamespace(data=[{**row, "id": self._id}] if row is not None else [])


class _Auth:
    def __init__(self, db):
        self._db = db

    def sign_in_with_password(self, creds):
        self._db.log.append(("sign_in_with_password", creds["email"]))
        if "outage" in self._db.fail:
            raise RuntimeError("upstream gateway timeout contacting the identity provider")
        if creds["password"] != "Correct horse 1!":
            raise RuntimeError("Invalid login credentials")
        return SimpleNamespace(user=SimpleNamespace(id=self._db.password_owner))

    def sign_in_with_id_token(self, creds):
        self._db.log.append(("sign_in_with_id_token", creds["provider"]))
        if "provider_bad" in self._db.fail:
            raise RuntimeError("Bad ID token")
        return SimpleNamespace(user=SimpleNamespace(id=self._db.id_token_owner))


class FakeDB:
    def __init__(self, users=None):
        self.sessions = SessionStoreFake(users=users if users is not None else {
            _UID: {"password_changed_at": None, "sessions_revoked_at": None, "email": _EMAIL},
            _OTHER: {"password_changed_at": None, "sessions_revoked_at": None},
        })
        self.fail: set[str] = set()
        self.log: list = []
        self.auth = _Auth(self)
        self.password_owner = _UID
        self.id_token_owner = _UID

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        return self.sessions.rpc(name, params)


class _Req:
    def __init__(self, ip="203.0.113.77"):
        self.client = SimpleNamespace(host=ip)
        self.headers = {}


def _start(db, uid=_UID, **kw):
    return sessions.start_session(db, uid, _EMAIL, **kw)


async def _refresh(db, token):
    return await auth_ep.refresh_token(RefreshTokenRequest(refresh_token=token), _Req(), db)


def _bearer(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _claims(token):
    return jose_jwt.get_unverified_claims(token)


def _code(exc_info):
    return exc_info.value.detail["error_code"]


def _advance(db, seconds):
    """Move the fake store's clock forward."""
    base = db.sessions.now()
    db.sessions._clock = lambda: base + timedelta(seconds=seconds)


# ── 1. Pure helpers ───────────────────────────────────────────────────────────

def test_legacy_jti_names_the_signing_input_not_the_malleable_signature():
    a = sessions.legacy_jti("header.payload-A.sig")
    assert a == sessions.legacy_jti("header.payload-A.sig")
    assert a != sessions.legacy_jti("header.payload-B.sig")
    # The signature segment's encoding is malleable (padding, unused low bits): same id.
    assert a == sessions.legacy_jti("header.payload-A.sig==") == sessions.legacy_jti(" header.payload-A.sih ")
    assert uuid.UUID(a).version == 5
    assert "payload-A" not in a


def test_a_padded_signature_really_verifies_so_the_canonical_id_is_load_bearing():
    """The review's proof, kept as a guard: python-jose accepts these variants."""
    token = create_refresh_token({"sub": _UID})
    for variant in (token + "=", token + "=="):
        assert decode_token(variant)["sub"] == _UID
        assert sessions.legacy_jti(variant) == sessions.legacy_jti(token)


@pytest.mark.parametrize("value", [None, 7, "", "not-a-uuid", "x" * 65, ["a"], True])
def test_parse_uuid_refuses_anything_but_a_uuid(value):
    assert sessions.parse_uuid(value) is None


def test_parse_uuid_canonicalises():
    assert sessions.parse_uuid(_UID.upper()) == _UID


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -5, 0, "1700000000", None])
def test_auth_time_of_refuses_unusable_values(value):
    assert sessions.auth_time_of(value) is None


def test_auth_time_of_floors_a_float():
    assert sessions.auth_time_of(1700000000.9) == 1700000000


def test_schema_missing_reads_only_structured_codes_through_the_cause_chain():
    inner = PgError("PGRST202", "Could not find the function")
    outer = RuntimeError("wrapped")
    outer.__cause__ = inner
    assert sessions.is_schema_missing(outer)
    assert sessions.is_schema_missing(PgError("PGRST205"))
    # Codes that can come from INSIDE a function body, or a column-cache miss, are not "the
    # migration is missing": reading them so would turn single use off for everyone.
    for code in ("42P01", "42883", "42703", "PGRST204", "23505", "P0001"):
        assert not sessions.is_schema_missing(PgError(code)), code
    assert sessions.is_column_missing(PgError("42703")) and sessions.is_column_missing(PgError("PGRST204"))
    assert not sessions.is_column_missing(PgError("PGRST202"))
    # A transient edge status (int code) and prose that merely says "not found" are NOT a
    # missing migration — reading them as one would degrade to stateless tokens on a blip.
    edge = RuntimeError("520 not found")
    edge.code = 520
    assert not sessions.is_schema_missing(edge)
    assert not sessions.is_schema_missing(RuntimeError("relation does not exist / not found"))


# ── 2. Minting a session ──────────────────────────────────────────────────────

def test_start_session_registers_a_session_and_stamps_its_claims():
    db = FakeDB()
    issued = _start(db)
    assert issued.sid and issued.jti
    refresh, access = _claims(issued.refresh_token), _claims(issued.access_token)
    assert refresh["jti"] == issued.jti and refresh["sid"] == issued.sid
    assert access["sid"] == issued.sid and "jti" not in access
    assert refresh["auth_time"] == access["auth_time"] == issued.auth_time
    assert abs(issued.auth_time - time.time()) < 5
    row = db.sessions.tokens[issued.jti]
    # The row's expiry IS the token's `exp` — the prune and the 'expired' status rely on it.
    assert int(row["expires_at"].timestamp()) == refresh["exp"]
    assert db.sessions.families[issued.sid]["user_id"] == _UID


def test_start_session_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setattr(sessions, "_START_RETRY_DELAY_SECONDS", 0)
    db = FakeDB()
    real = db.sessions.rpc
    calls = {"n": 0}

    def flaky(name, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset")
        return real(name, params)

    db.rpc = flaky
    issued = _start(db)
    assert issued.sid is not None and calls["n"] == 2


def test_start_session_fails_open_to_an_untracked_token_after_two_failures(monkeypatch, caplog):
    monkeypatch.setattr(sessions, "_START_RETRY_DELAY_SECONDS", 0)
    db = FakeDB()
    db.sessions.fail.add("start_refresh_family")
    with caplog.at_level(logging.ERROR, logger="app.services.auth_session_service"):
        issued = _start(db)
    assert issued.sid is None and issued.jti is None
    claims = _claims(issued.refresh_token)
    assert "jti" not in claims and "sid" not in claims and claims["type"] == "refresh"
    assert len([c for c in db.sessions.calls if c[0] == "start_refresh_family"]) == 2
    assert any("UNTRACKED" in r.getMessage() for r in caplog.records)


def test_start_session_with_the_migration_missing_does_not_retry_and_logs_error(caplog):
    db = FakeDB()
    db.sessions.missing = True
    with caplog.at_level(logging.ERROR, logger="app.services.auth_session_service"):
        issued = _start(db)
    assert issued.sid is None
    assert len(db.sessions.calls) == 1
    assert any("214 is NOT applied" in r.getMessage() for r in caplog.records)


def test_start_session_for_an_account_without_a_users_row_issues_untracked():
    db = FakeDB(users={})
    issued = _start(db)
    assert issued.sid is None and len(db.sessions.calls) == 1


def test_session_exchange_auth_time_is_the_supabase_tokens_own_iat():
    db = FakeDB()
    old = int(time.time()) - 3000
    issued = _start(db, auth_time=old)
    assert _claims(issued.access_token)["auth_time"] == old
    assert db.sessions.families[issued.sid]["auth_time"].timestamp() == old


# ── 3. Rotation: single use, grace, reuse ─────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_rotates_and_the_chain_keeps_the_session():
    db = FakeDB()
    first = _start(db)
    r1 = await _refresh(db, first.refresh_token)
    c1 = _claims(r1.refresh_token)
    assert c1["sid"] == first.sid and c1["jti"] != first.jti
    assert _claims(r1.access_token)["sid"] == first.sid
    assert c1["auth_time"] == first.auth_time, "auth_time is the SIGN-IN time, never re-stamped"
    old_row = db.sessions.tokens[first.jti]
    assert old_row["revoked_at"] is not None and old_row["replaced_by"] == c1["jti"]
    r2 = await _refresh(db, r1.refresh_token)
    assert _claims(r2.refresh_token)["sid"] == first.sid
    assert len(db.sessions.live_tokens(first.sid)) == 1


@pytest.mark.asyncio
async def test_reuse_outside_the_grace_window_revokes_the_whole_session(caplog):
    db = FakeDB()
    first = _start(db)
    legit = await _refresh(db, first.refresh_token)          # the real device rotates
    _advance(db, settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS + 1)
    with caplog.at_level(logging.WARNING, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:              # a copy of the OLD token appears
            await _refresh(db, first.refresh_token)
    assert ei.value.status_code == 401 and _code(ei) == "AUTH_SESSION_EXPIRED"
    assert any("REUSE" in r.getMessage() for r in caplog.records)
    assert db.sessions.families[first.sid]["revoke_reason"] == "reuse"
    # The real device's newest token dies with it: both parties must sign in again.
    with pytest.raises(HTTPException) as ei2:
        await _refresh(db, legit.refresh_token)
    assert _code(ei2) == "AUTH_SESSION_EXPIRED"
    assert "session has ended" in ei2.value.detail["user_message"]


@pytest.mark.asyncio
async def test_a_lost_response_retried_inside_the_grace_window_gets_a_sibling():
    db = FakeDB()
    first = _start(db)
    lost = await _refresh(db, first.refresh_token)            # response never reached the app
    _advance(db, 30)
    retry = await _refresh(db, first.refresh_token)           # the app retries the same token
    assert _claims(retry.refresh_token)["sid"] == first.sid
    assert _claims(retry.refresh_token)["jti"] != _claims(lost.refresh_token)["jti"]
    # Whichever one the app kept works, once.
    nxt = await _refresh(db, retry.refresh_token)
    assert _claims(nxt.refresh_token)["sid"] == first.sid


@pytest.mark.asyncio
async def test_concurrent_refreshes_of_one_token_both_succeed():
    """The app's restore and its pre-flight refresh are not single-flighted together."""
    db = FakeDB()
    first = _start(db)
    a, b = await asyncio.gather(_refresh(db, first.refresh_token), _refresh(db, first.refresh_token))
    assert _claims(a.refresh_token)["sid"] == _claims(b.refresh_token)["sid"] == first.sid
    assert db.sessions.families[first.sid]["revoked_at"] is None


@pytest.mark.asyncio
async def test_the_sibling_cap_turns_a_replay_loop_into_reuse_even_inside_the_window():
    db = FakeDB()
    first = _start(db)
    for _ in range(settings.REFRESH_TOKEN_MAX_CHILDREN):       # rotation + grace siblings
        await _refresh(db, first.refresh_token)
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, first.refresh_token)
    assert _code(ei) == "AUTH_SESSION_EXPIRED"
    assert db.sessions.families[first.sid]["revoke_reason"] == "reuse"


@pytest.mark.asyncio
async def test_grace_zero_detects_reuse_immediately(monkeypatch):
    monkeypatch.setattr(settings, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 0)
    db = FakeDB()
    first = _start(db)
    await _refresh(db, first.refresh_token)
    _advance(db, 0.5)
    with pytest.raises(HTTPException):
        await _refresh(db, first.refresh_token)


@pytest.mark.asyncio
async def test_a_grace_replay_cannot_resurrect_a_logged_out_session():
    db = FakeDB()
    first = _start(db)
    await _refresh(db, first.refresh_token)
    sessions.revoke_session(db, _UID, first.sid)
    with pytest.raises(HTTPException) as ei:                  # still inside the window
        await _refresh(db, first.refresh_token)
    assert _code(ei) == "AUTH_SESSION_EXPIRED"
    assert db.sessions.families[first.sid]["revoke_reason"] == "logout"


# ── 4. Legacy (pre-214) tokens ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_legacy_token_keeps_working_and_moves_onto_a_tracked_session():
    db = FakeDB()
    legacy = create_refresh_token({"sub": _UID, "email": _EMAIL})
    out = await _refresh(db, legacy)
    claims = _claims(out.refresh_token)
    assert claims["sid"] and claims["jti"]
    row = db.sessions.tokens[sessions.legacy_jti(legacy)]
    assert row["legacy"] is True and row["revoked_at"] is not None
    assert legacy not in str(db.sessions.tokens), "the raw token must never be stored"


@pytest.mark.asyncio
async def test_a_legacy_token_is_single_use_too():
    db = FakeDB()
    legacy = create_refresh_token({"sub": _UID, "email": _EMAIL})
    upgraded = await _refresh(db, legacy)
    _advance(db, settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS + 5)
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, legacy)
    assert _code(ei) == "AUTH_SESSION_EXPIRED"
    with pytest.raises(HTTPException):
        await _refresh(db, upgraded.refresh_token)


# ── 5. Failures: closed on refresh, degraded only for a missing migration ────

@pytest.mark.asyncio
async def test_refresh_fails_closed_when_the_session_store_errors():
    db = FakeDB()
    first = _start(db)
    db.sessions.fail.add("rotate_refresh_token")
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, first.refresh_token)
    assert ei.value.status_code == 503 and _code(ei) == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, [], [1, 2], "rotated", {"no": "status"}, {"status": 3}])
async def test_refresh_fails_closed_on_a_malformed_rpc_answer(answer):
    db = FakeDB()
    first = _start(db)
    db.rpc = lambda name, params: SimpleNamespace(execute=lambda: SimpleNamespace(data=answer))
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, first.refresh_token)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_refresh_fails_closed_on_an_unknown_status_or_a_rotation_without_a_session():
    for answer in ({"status": "surprise"}, {"status": "rotated", "family_id": None}):
        db = FakeDB()
        first = _start(db)
        db.rpc = lambda name, params, a=answer: SimpleNamespace(
            execute=lambda: SimpleNamespace(data=a))
        with pytest.raises(HTTPException) as ei:
            await _refresh(db, first.refresh_token)
        assert ei.value.status_code == 503, answer


@pytest.mark.asyncio
async def test_refresh_degrades_to_stateless_while_the_migration_is_missing(caplog):
    db = FakeDB()
    legacy = create_refresh_token({"sub": _UID, "email": _EMAIL})
    db.sessions.missing = True
    with caplog.at_level(logging.ERROR, logger="app.services.auth_session_service"):
        out = await _refresh(db, legacy)
    assert "jti" not in _claims(out.refresh_token)
    assert any("214 is NOT applied" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_tracked_token_whose_row_is_gone_is_refused():
    db = FakeDB()
    first = _start(db)
    db.sessions.tokens.clear()
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, first.refresh_token)
    assert _code(ei) == "AUTH_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_a_malformed_jti_is_token_invalid_not_a_500():
    db = FakeDB()
    token = create_refresh_token({"sub": _UID, "jti": "not-a-uuid", "sid": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, token)
    assert ei.value.status_code == 401 and _code(ei) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_an_account_deleted_between_checks_is_account_not_found():
    db = FakeDB()
    first = _start(db)
    real = db.rpc

    def vanish(name, params):
        db.sessions.users.pop(_UID, None)
        return real(name, params)

    db.rpc = vanish
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, first.refresh_token)
    assert _code(ei) == "AUTH_ACCOUNT_NOT_FOUND"


# ── 6. Logout ─────────────────────────────────────────────────────────────────

async def _logout(db, access, body=None, uid=_UID):
    return await auth_ep.logout(
        body=LogoutRequest(refresh_token=body) if body is not None else None,
        user_id=uid, credentials=_bearer(access), supabase=db,
    )


@pytest.mark.asyncio
async def test_logout_revokes_only_this_devices_session():
    db = FakeDB()
    phone, tablet = _start(db), _start(db)
    out = await _logout(db, phone.access_token, phone.refresh_token)
    assert out.sessions_revoked == 1
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, phone.refresh_token)
    assert _code(ei) == "AUTH_SESSION_EXPIRED"
    assert (await _refresh(db, tablet.refresh_token)).user_id == _UID


@pytest.mark.asyncio
async def test_a_shipped_build_with_no_body_still_ends_its_session_through_the_bearer_sid():
    db = FakeDB()
    s = _start(db)
    out = await _logout(db, s.access_token, body=None)
    assert out.sessions_revoked == 1
    assert db.sessions.families[s.sid]["revoke_reason"] == "logout"


@pytest.mark.asyncio
async def test_logout_with_pre_211_tokens_revokes_nothing_and_still_succeeds():
    db = FakeDB()
    access = create_access_token({"sub": _UID})
    out = await _logout(db, access, create_refresh_token({"sub": _UID}))
    assert out.sessions_revoked == 0 and db.sessions.calls == []


@pytest.mark.asyncio
async def test_logout_ignores_another_accounts_refresh_token_in_the_body():
    db = FakeDB()
    mine, theirs = _start(db), _start(db, uid=_OTHER)
    out = await _logout(db, mine.access_token, theirs.refresh_token)
    assert out.sessions_revoked == 1
    assert db.sessions.families[theirs.sid]["revoked_at"] is None
    assert db.sessions.families[mine.sid]["revoked_at"] is not None
    # Refused in Python before the RPC (the RPC's own user scope is the second wall).
    assert not any(
        name == "revoke_refresh_family" and params["p_family_id"] == theirs.sid
        for name, params in db.sessions.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["garbage", "a.b.c", "", " "])
async def test_logout_ignores_an_unverifiable_body_token(body):
    db = FakeDB()
    s = _start(db)
    out = await _logout(db, s.access_token, body)
    assert out.sessions_revoked == 1  # the bearer's own session


@pytest.mark.asyncio
async def test_logout_ignores_an_expired_body_token_and_a_non_refresh_one():
    db = FakeDB()
    s = _start(db)
    expired = create_refresh_token(
        {"sub": _UID, "sid": str(uuid.uuid4()), "jti": str(uuid.uuid4())},
        expires_delta=timedelta(seconds=-5),
    )
    assert (await _logout(db, s.access_token, expired)).sessions_revoked == 1
    db2 = FakeDB()
    s2 = _start(db2)
    out = await _logout(db2, s2.access_token, s2.access_token)  # an ACCESS token in the body
    assert out.sessions_revoked == 1


@pytest.mark.asyncio
async def test_logout_answers_503_when_the_store_fails():
    db = FakeDB()
    s = _start(db)
    db.sessions.fail.add("revoke_refresh_family")
    with pytest.raises(HTTPException) as ei:
        await _logout(db, s.access_token, s.refresh_token)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_logout_of_a_tracked_session_whose_function_vanished_is_a_503_not_a_quiet_200():
    """A `sid` was minted by a live 214, so a missing function now is a rollback or a drift: a
    logout must not answer 200 for a session it could not end (review 2026-11-26)."""
    db = FakeDB()
    s = _start(db)
    db.sessions.missing = True
    with pytest.raises(HTTPException) as ei:
        await _logout(db, s.access_token, s.refresh_token)
    assert ei.value.status_code == 503


# ── 7. Sign out of all devices ────────────────────────────────────────────────

async def _sign_out_all(db, uid=_UID):
    return await auth_ep.sign_out_all(req=_Req(), user={"id": uid}, supabase=db)


@pytest.mark.asyncio
async def test_sign_out_all_ends_every_session_of_the_account_and_only_that_account(monkeypatch):
    detached = []
    monkeypatch.setattr(auth_ep, "_detach_all_devices", lambda uid: detached.append(uid) or True)
    db = FakeDB()
    a, b = _start(db), _start(db)
    other = _start(db, uid=_OTHER)
    legacy = create_refresh_token({"sub": _UID, "iat": int(time.time()) - 60})
    time.sleep(1.05)  # the stamp is compared at whole seconds; make the old tokens strictly older
    await _sign_out_all(db)
    assert detached == [_UID]
    for tok in (a.refresh_token, b.refresh_token, legacy):
        with pytest.raises(HTTPException) as ei:
            await _refresh(db, tok)
        assert _code(ei) == "AUTH_SESSION_EXPIRED"
        assert "all devices" in ei.value.detail["user_message"]
    assert (await _refresh(db, other.refresh_token)).user_id == _OTHER
    # The access tokens die on every route that reads the users row.
    with pytest.raises(HTTPException) as ei:
        _reject_if_password_changed_since_issue(a.access_token, {"id": _UID, **db.sessions.users[_UID]})
    assert _code(ei) == "AUTH_SESSION_EXPIRED" and "all devices" in ei.value.detail["user_message"]
    # Signing in again afterwards works — from the next whole second on (the stamp is rounded UP
    # to it so nothing minted in its own second survives).
    time.sleep(1.05)
    fresh = _start(db)
    assert (await _refresh(db, fresh.refresh_token)).user_id == _UID


@pytest.mark.asyncio
async def test_sign_out_all_is_rate_limited(monkeypatch):
    monkeypatch.setattr(auth_ep, "_detach_all_devices", lambda uid: True)
    db = FakeDB()
    for _ in range(5):
        await _sign_out_all(db)
    with pytest.raises(HTTPException) as ei:
        await _sign_out_all(db)
    assert ei.value.status_code == 429


@pytest.mark.asyncio
async def test_sign_out_all_failures_are_503_never_a_false_success(monkeypatch):
    monkeypatch.setattr(auth_ep, "_detach_all_devices", lambda uid: True)
    db = FakeDB()
    db.sessions.missing = True
    with pytest.raises(HTTPException) as ei:
        await _sign_out_all(db)
    assert ei.value.status_code == 503 and "isn't available" in ei.value.detail["user_message"]
    rate_limiter.clear()
    db2 = FakeDB()
    db2.sessions.fail.add("revoke_all_refresh_sessions")
    with pytest.raises(HTTPException) as ei:
        await _sign_out_all(db2)
    assert ei.value.status_code == 503
    rate_limiter.clear()
    with pytest.raises(HTTPException) as ei:
        await _sign_out_all(FakeDB(users={}))
    assert _code(ei) == "AUTH_ACCOUNT_NOT_FOUND"


def test_unregister_all_devices_deletes_by_account_and_reports_failure():
    from app.services.user_settings_service import UserSettingsService

    svc = UserSettingsService.__new__(UserSettingsService)
    db = FakeDB()
    svc.supabase = db
    assert svc.unregister_all_devices(_UID) is True
    assert ("device_tokens.delete", _UID) in db.log
    assert svc.unregister_all_devices("") is False

    class Broken:
        def table(self, _):
            raise RuntimeError("down")

    svc.supabase = Broken()
    assert svc.unregister_all_devices(_UID) is False


# ── 8. Per-request eviction (dependencies) ────────────────────────────────────

def test_the_later_of_the_two_stamps_is_the_boundary():
    now = datetime.now(timezone.utc)
    token = create_access_token({"sub": _UID})
    row = {"id": _UID, "password_changed_at": (now - timedelta(days=3)).isoformat(),
           "sessions_revoked_at": (now + timedelta(seconds=5)).isoformat()}
    with pytest.raises(HTTPException) as ei:
        _reject_if_password_changed_since_issue(token, row)
    assert "all devices" in ei.value.detail["user_message"]
    row = {"id": _UID, "password_changed_at": (now + timedelta(seconds=5)).isoformat(),
           "sessions_revoked_at": (now - timedelta(days=3)).isoformat()}
    with pytest.raises(HTTPException) as ei:
        _reject_if_password_changed_since_issue(token, row)
    assert "password" in ei.value.detail["user_message"].lower()


def test_an_unreadable_stamp_does_not_hide_a_readable_one():
    later = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    with pytest.raises(HTTPException):
        _reject_if_password_changed_since_issue(
            create_access_token({"sub": _UID}),
            {"id": _UID, "sessions_revoked_at": "garbage", "password_changed_at": later},
        )


def test_a_token_minted_after_the_stamp_survives():
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _reject_if_password_changed_since_issue(
        create_access_token({"sub": _UID}), {"id": _UID, "sessions_revoked_at": stamp},
    )


@pytest.mark.asyncio
async def test_access_claims_dependency_never_raises():
    assert await get_access_token_claims(None) == {}
    assert await get_access_token_claims(_bearer("garbage")) == {}
    assert await get_access_token_claims(_bearer(create_refresh_token({"sub": _UID}))) == {}
    claims = await get_access_token_claims(_bearer(create_access_token({"sub": _UID, "auth_time": 5})))
    assert claims["auth_time"] == 5


# ── 9. CC-10: a 60-minute access token ────────────────────────────────────────

def test_the_access_token_defaults_to_60_minutes_and_is_capped():
    assert Settings.model_fields["ACCESS_TOKEN_EXPIRE_MINUTES"].default == 60
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 60
    assert Settings._cap_access_token_life(1440) == 60
    assert Settings._cap_access_token_life(61) == 60
    assert Settings._cap_access_token_life(1) == 5
    assert Settings._cap_access_token_life(30) == 30
    token = create_access_token({"sub": _UID})
    c = _claims(token)
    assert c["exp"] - c["iat"] <= 60 * 60


# ── 10. SEC-11: re-authentication before deletion ─────────────────────────────

def test_a_reauth_proof_is_bound_to_account_and_purpose_and_expires(monkeypatch):
    proof = create_reauth_token(_UID)
    assert decode_reauth_token(proof, _UID)
    assert not decode_reauth_token(proof, _OTHER)
    assert not decode_reauth_token(proof, _UID, purpose="something_else")
    for bad in (None, "", "x", "a.b.c", "y" * 5000, 42):
        assert not decode_reauth_token(bad, _UID)
    # Not a session: the access-token decoder refuses it, and an access token is not a proof.
    from jose import JWTError
    with pytest.raises(JWTError):
        _decode_access_token(proof)
    assert not decode_reauth_token(create_access_token({"sub": _UID}), _UID)
    expired = jose_jwt.encode(
        {"sub": _UID, "type": "reauth", "purpose": "account_delete",
         "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    assert not decode_reauth_token(expired, _UID)


class _DeleteDB:
    def __init__(self):
        self.log = []
        self.auth = SimpleNamespace(admin=SimpleNamespace(
            delete_user=lambda uid: self.log.append(("delete_user", uid))))

    def rpc(self, name, params):
        # The 211 fingerprint (`record_deleted_account`) — recorded so the order is visible.
        self.log.append(("rpc", name))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=1))


@pytest.fixture
def _no_purges(monkeypatch):
    calls = []
    monkeypatch.setattr(users_ep, "_purge_research_pdfs", lambda sb, uid: calls.append("pdfs"))
    monkeypatch.setattr(users_ep, "_purge_avatars", lambda sb, uid: calls.append("avatars"))
    monkeypatch.setattr(users_ep, "_purge_unlinked_rows", lambda sb, uid: calls.append("rows") or {})
    from app.services import apple_sign_in_service as _apple

    async def _revoke(sb, uid):
        calls.append("apple_revoke")
        if isinstance(getattr(sb, "log", None), list):
            sb.log.append(("apple_revoke", uid))
        return _apple.NO_GRANT

    monkeypatch.setattr(_apple, "revoke_for_account", _revoke)
    return calls


async def _delete(db, *, reauth=None, claims=None):
    return await users_ep.delete_account(
        user={"id": _UID}, supabase=db, claims=claims or {}, reauth=reauth,
    )


@pytest.mark.asyncio
async def test_refused_proofs_never_use_up_the_accounts_deletion_attempts(_no_purges):
    """Review 2026-11-27: the per-account deletion bucket used to be charged by the dependency,
    BEFORE the re-auth check, so someone holding the token could spend the owner's five
    attempts with bad proofs and block the real deletion for an hour."""
    per_user, _ = users_ep.ACCOUNT_DELETE_LIMIT_PER_USER
    for _ in range(per_user + 2):
        with pytest.raises(HTTPException) as ei:
            await _delete(_DeleteDB(), reauth="not-a-proof")
        assert _code(ei) == "AUTH_REAUTH_REQUIRED"
    assert await _delete(_DeleteDB()) == {"deleted": True}


@pytest.mark.asyncio
async def test_a_shipped_build_still_deletes_with_the_bearer_alone(_no_purges):
    db = _DeleteDB()
    assert await _delete(db) == {"deleted": True}
    # The combined order (review 2026-11-27): the 211 fingerprint, then the 213 Apple revoke,
    # then the login itself — after the purge, which ran first.
    assert db.log == [("rpc", "record_deleted_account"), ("apple_revoke", _UID), ("delete_user", _UID)]
    assert _no_purges == ["pdfs", "avatars", "rows", "apple_revoke"]


@pytest.mark.asyncio
async def test_a_valid_proof_deletes(_no_purges, monkeypatch):
    monkeypatch.setattr(settings, "ACCOUNT_DELETE_REQUIRE_REAUTH", True)
    db = _DeleteDB()
    assert await _delete(db, reauth=create_reauth_token(_UID)) == {"deleted": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["garbage", "proof-for-other"])
async def test_a_present_but_wrong_proof_is_refused_before_anything_is_deleted(_no_purges, bad):
    proof = create_reauth_token(_OTHER) if bad == "proof-for-other" else bad
    db = _DeleteDB()
    with pytest.raises(HTTPException) as ei:
        await _delete(db, reauth=proof)
    assert ei.value.status_code == 403 and _code(ei) == "AUTH_REAUTH_REQUIRED"
    assert _no_purges == [] and db.log == [], "nothing may be deleted before the check"


@pytest.mark.asyncio
async def test_with_the_switch_on_a_bearer_alone_needs_a_recent_sign_in(_no_purges, monkeypatch):
    monkeypatch.setattr(settings, "ACCOUNT_DELETE_REQUIRE_REAUTH", True)
    now = int(time.time())
    with pytest.raises(HTTPException) as ei:
        await _delete(_DeleteDB(), claims={"auth_time": now - 3600})
    assert _code(ei) == "AUTH_REAUTH_REQUIRED"
    with pytest.raises(HTTPException):
        await _delete(_DeleteDB(), claims={})                      # legacy token: unknown
    with pytest.raises(HTTPException):
        await _delete(_DeleteDB(), claims={"auth_time": now + 3600})  # from the future
    with pytest.raises(HTTPException):
        await _delete(_DeleteDB(), claims={"auth_time": True})
    assert await _delete(_DeleteDB(), claims={"auth_time": now - 60}) == {"deleted": True}


async def _reauth(db, body, uid=_UID):
    return await auth_ep.reauthenticate(
        request=body, req=_Req(), user={"id": uid, "email": _EMAIL}, supabase=db, auth_client=db,
    )


@pytest.mark.asyncio
async def test_reauthenticate_with_the_right_password_returns_a_proof_and_no_session():
    db = FakeDB()
    out = await _reauth(db, ReauthenticateRequest(password="Correct horse 1!"))
    assert decode_reauth_token(out.reauth_token, _UID)
    assert out.expires_in == settings.REAUTH_TOKEN_EXPIRE_SECONDS
    assert db.sessions.calls == [], "re-authentication must never start or touch a session"


@pytest.mark.asyncio
async def test_reauthenticate_wrong_password_and_outage_are_distinct():
    db = FakeDB()
    with pytest.raises(HTTPException) as ei:
        await _reauth(db, ReauthenticateRequest(password="wrong"))
    assert _code(ei) == "AUTH_CREDENTIALS_INVALID"
    db.fail.add("outage")
    with pytest.raises(HTTPException) as ei:
        await _reauth(db, ReauthenticateRequest(password="Correct horse 1!"))
    assert _code(ei) == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_reauthenticate_refuses_a_password_that_proves_another_account():
    db = FakeDB()
    db.password_owner = _OTHER
    with pytest.raises(HTTPException) as ei:
        await _reauth(db, ReauthenticateRequest(password="Correct horse 1!"))
    assert _code(ei) == "AUTH_CREDENTIALS_INVALID"


def _id_token(sub):
    return jose_jwt.encode({"sub": sub, "iss": "https://appleid.apple.com"}, "k" * 32, algorithm="HS256")


@pytest.mark.asyncio
async def test_an_unlinked_provider_subject_never_reaches_gotrue():
    """GoTrue's id-token sign-in CREATES an account for an unknown subject."""
    db = FakeDB()
    with pytest.raises(HTTPException) as ei:
        await _reauth(db, ReauthenticateRequest(provider="apple", id_token=_id_token("apple-sub-X")))
    assert _code(ei) == "AUTH_CREDENTIALS_INVALID"
    assert not any(e[0] == "sign_in_with_id_token" for e in db.log)


@pytest.mark.asyncio
async def test_a_linked_provider_subject_is_verified_by_gotrue():
    db = FakeDB()
    db.sessions.users[_UID]["_identities"] = {("apple", "apple-sub-1")}
    out = await _reauth(db, ReauthenticateRequest(
        provider="apple", id_token=_id_token("apple-sub-1"), nonce="n"))
    assert decode_reauth_token(out.reauth_token, _UID)
    db.fail.add("provider_bad")
    with pytest.raises(HTTPException) as ei:
        await _reauth(db, ReauthenticateRequest(provider="apple", id_token=_id_token("apple-sub-1")))
    assert _code(ei) == "AUTH_PROVIDER_FAILED"


# ── Review 2026-11-27: the Apple code at "Confirm it's you" stores the grant ─────────

def _apple_store_recorder(monkeypatch, *, raises=False):
    from app.services import apple_sign_in_service as svc
    calls = []

    async def fake_store(supabase, *, user_id, authorization_code, identity_token):
        calls.append((user_id, authorization_code, identity_token))
        if raises:
            raise RuntimeError("apple exchange blew up")
        return "stored"

    monkeypatch.setattr(svc, "store_grant_from_authorization_code", fake_store)
    return calls


@pytest.mark.asyncio
async def test_an_apple_reauth_with_a_code_stores_the_grant_before_answering(monkeypatch):
    """An account last signed in on 1.0–1.03 has no stored grant; the re-auth right before its
    deletion is the last chance to get one, or the deletion revokes nothing (SOC-04)."""
    calls = _apple_store_recorder(monkeypatch)
    db = FakeDB()
    db.sessions.users[_UID]["_identities"] = {("apple", "apple-sub-1")}
    token = _id_token("apple-sub-1")
    out = await _reauth(db, ReauthenticateRequest(
        provider="apple", id_token=token, nonce="n", authorization_code="c0de"))
    assert decode_reauth_token(out.reauth_token, _UID)
    assert calls == [(_UID, "c0de", token)]


@pytest.mark.asyncio
async def test_a_failing_apple_grant_store_never_refuses_the_reauth(monkeypatch):
    _apple_store_recorder(monkeypatch, raises=True)
    db = FakeDB()
    db.sessions.users[_UID]["_identities"] = {("apple", "apple-sub-1")}
    out = await _reauth(db, ReauthenticateRequest(
        provider="apple", id_token=_id_token("apple-sub-1"), nonce="n", authorization_code="c0de"))
    assert decode_reauth_token(out.reauth_token, _UID)


@pytest.mark.asyncio
async def test_no_grant_is_stored_for_google_for_no_code_or_for_another_account(monkeypatch):
    calls = _apple_store_recorder(monkeypatch)
    db = FakeDB()
    db.sessions.users[_UID]["_identities"] = {("apple", "apple-sub-1"), ("google", "g-sub-1")}
    await _reauth(db, ReauthenticateRequest(
        provider="google", id_token=_id_token("g-sub-1"), authorization_code="c0de"))
    await _reauth(db, ReauthenticateRequest(provider="apple", id_token=_id_token("apple-sub-1"), nonce="n"))
    db.id_token_owner = _OTHER        # GoTrue verified the token as ANOTHER account
    with pytest.raises(HTTPException):
        await _reauth(db, ReauthenticateRequest(
            provider="apple", id_token=_id_token("apple-sub-1"), nonce="n", authorization_code="c0de"))
    assert calls == [], "a grant was stored for Google, without a code, or for another account"


def test_the_reauth_code_is_a_redacted_optional_field_not_a_credential_kind():
    field = ReauthenticateRequest.model_fields["authorization_code"]
    assert field.repr is False and field.default is None
    assert ReauthenticateRequest(authorization_code="c0de").credential_kind() is None
    assert "c0de" not in repr(ReauthenticateRequest(password="x", authorization_code="c0de"))


@pytest.mark.asyncio
async def test_reauthenticate_identity_check_fails_closed():
    db = FakeDB()
    db.sessions.fail.add("account_has_identity")
    with pytest.raises(HTTPException) as ei:
        await _reauth(db, ReauthenticateRequest(provider="google", id_token=_id_token("g-1")))
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_reauthenticate_refuses_an_unparsable_id_token():
    with pytest.raises(HTTPException) as ei:
        await _reauth(FakeDB(), ReauthenticateRequest(provider="apple", id_token="x" * 40))
    assert _code(ei) == "AUTH_PROVIDER_FAILED"


@pytest.mark.asyncio
async def test_reauthenticate_supabase_token_must_be_fresh_ours_and_not_anonymous(monkeypatch):
    now = int(time.time())

    def verifier(payload):
        async def _v(_token):
            return payload
        return _v

    fresh = [{"method": "oauth", "timestamp": now - 30}]
    for payload, ok in (
        ({"sub": _UID, "iat": now - 30, "amr": fresh}, True),
        # A GoTrue REFRESH: fresh iat, sign-in an hour ago — not a re-authentication.
        ({"sub": _UID, "iat": now - 5, "amr": [{"method": "oauth", "timestamp": now - 3600}]}, False),
        ({"sub": _UID, "iat": now - 30}, False),                       # no amr: unknown
        ({"sub": _OTHER, "iat": now - 30, "amr": fresh}, False),
        ({"sub": _UID, "iat": now - 30, "amr": fresh, "is_anonymous": True}, False),
        ({"sub": _UID, "amr": "password"}, False),
        (None, False),
    ):
        rate_limiter.clear()
        monkeypatch.setattr(auth_ep, "verify_supabase_token", verifier(payload))
        body = ReauthenticateRequest(supabase_access_token="s" * 40)
        if ok:
            assert decode_reauth_token((await _reauth(FakeDB(), body)).reauth_token, _UID)
        else:
            with pytest.raises(HTTPException):
                await _reauth(FakeDB(), body)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {},
    {"password": "p", "supabase_access_token": "s" * 40},
    {"provider": "apple"},
    {"id_token": "t" * 40},
])
async def test_reauthenticate_needs_exactly_one_credential(body):
    with pytest.raises(HTTPException) as ei:
        await _reauth(FakeDB(), ReauthenticateRequest(**body))
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_reauthenticate_is_rate_limited_per_account():
    db = FakeDB()
    for _ in range(5):
        with pytest.raises(HTTPException):
            await _reauth(db, ReauthenticateRequest(password="wrong"))
    with pytest.raises(HTTPException) as ei:
        await _reauth(db, ReauthenticateRequest(password="Correct horse 1!"))
    assert ei.value.status_code == 429


# ── 11. Every token-minting route starts a session ────────────────────────────

def test_every_mint_site_goes_through_start_session():
    """A route that minted with `create_refresh_token` directly would issue an untracked token
    forever — logout could never end it. Scan the auth module's code (docstrings/comments out)."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(auth_ep))
    direct = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in (
            "create_refresh_token", "create_access_token")
    ]
    assert direct == [], f"auth.py mints outside auth_session_service at lines {direct}"
    starts = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "start_session"
    ]
    assert len(starts) >= 3  # sign-in, register, _issue_app_tokens_for


# ── 12. Review round (2026-11-26) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_padded_copy_of_a_legacy_token_is_reuse_not_a_new_first_use():
    """One legacy token plus "="*1..5 used to open six live sessions."""
    db = FakeDB()
    legacy = create_refresh_token({"sub": _UID, "email": _EMAIL})
    await _refresh(db, legacy)
    _advance(db, settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS + 5)
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, legacy + "=")
    assert _code(ei) == "AUTH_SESSION_EXPIRED"
    assert len(db.sessions.families) == 1
    assert all(f["revoke_reason"] == "reuse" for f in db.sessions.families.values())


@pytest.mark.asyncio
async def test_padded_copies_inside_the_grace_window_hit_the_sibling_cap():
    db = FakeDB()
    legacy = create_refresh_token({"sub": _UID, "email": _EMAIL})
    for pad in range(settings.REFRESH_TOKEN_MAX_CHILDREN):
        await _refresh(db, legacy + "=" * pad)
    with pytest.raises(HTTPException):
        await _refresh(db, legacy + "=" * 7)
    assert len(db.sessions.families) == 1


@pytest.mark.asyncio
async def test_sign_out_all_evicts_an_access_token_minted_in_the_same_second(monkeypatch):
    monkeypatch.setattr(auth_ep, "_detach_all_devices", lambda uid: True)
    db = FakeDB()
    s = _start(db)                    # minted now — very likely the same second as the stamp
    await _sign_out_all(db)
    with pytest.raises(HTTPException) as ei:
        _reject_if_password_changed_since_issue(s.access_token, {"id": _UID, **db.sessions.users[_UID]})
    assert _code(ei) == "AUTH_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_a_tracked_token_never_degrades_to_stateless(caplog):
    db = FakeDB()
    s = _start(db)
    db.sessions.missing = True
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, s.refresh_token)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["42P01", "42883", "42703", "PGRST204"])
async def test_codes_from_inside_a_function_fail_closed_on_refresh(code):
    db = FakeDB()
    legacy = create_refresh_token({"sub": _UID, "email": _EMAIL})

    def raising(name, params):
        def boom():
            raise PgError(code, "relation \"x\" does not exist")
        return SimpleNamespace(execute=boom)

    db.rpc = raising
    with pytest.raises(HTTPException) as ei:
        await _refresh(db, legacy)
    assert ei.value.status_code == 503


def test_start_session_retry_after_a_lost_answer_keeps_the_tracked_session(monkeypatch):
    monkeypatch.setattr(sessions, "_START_RETRY_DELAY_SECONDS", 0)
    db = FakeDB()
    real = db.sessions.rpc
    calls = {"n": 0}

    def commit_then_lose(name, params):
        calls["n"] += 1
        call = real(name, params)
        if calls["n"] == 1:
            call.execute()                    # the insert COMMITS...
            def lost():
                raise RuntimeError("connection reset after commit")   # ...the answer is lost
            return SimpleNamespace(execute=lost)
        return call

    db.rpc = commit_then_lose
    issued = _start(db)
    assert issued.sid is not None and issued.sid in db.sessions.families
    assert len(db.sessions.families) == 1, "no orphan session, no untracked fallback"


def test_supabase_sign_in_time_reads_amr_never_iat():
    assert sessions.supabase_sign_in_time({"iat": 999, "amr": [{"timestamp": 5}, {"timestamp": 7}]}) == 7
    for bad in ({}, {"iat": 999}, {"amr": "x"}, {"amr": [1, None]}, {"amr": [{"timestamp": True}]},
                {"amr": [{"timestamp": float("nan")}]}, None):
        assert sessions.supabase_sign_in_time(bad) is None


@pytest.mark.asyncio
async def test_session_exchange_judges_eviction_and_auth_time_on_the_sign_in(monkeypatch):
    """A GoTrue refresh after a sign-out-all mints a Supabase token with a fresh iat: it must not
    come back as a Caydex session."""
    now = int(time.time())
    db = FakeDB()
    db.sessions.users[_UID]["sessions_revoked_at"] = datetime.fromtimestamp(now - 60, tz=timezone.utc).isoformat()

    async def verifier(_t, payload={"sub": _UID, "email": _EMAIL, "iat": now - 5,
                                    "amr": [{"method": "oauth", "timestamp": now - 3600}]}):
        return payload

    monkeypatch.setattr(auth_ep, "verify_supabase_token", verifier)
    from app.schemas.auth import SessionExchangeRequest
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(SessionExchangeRequest(supabase_access_token="s" * 40), _Req(), db)
    assert _code(ei) == "AUTH_PROVIDER_FAILED"

    async def fresh(_t):
        return {"sub": _UID, "email": _EMAIL, "iat": now, "amr": [{"method": "oauth", "timestamp": now}]}

    monkeypatch.setattr(auth_ep, "verify_supabase_token", fresh)
    out = await auth_ep.session_exchange(SessionExchangeRequest(supabase_access_token="s" * 40), _Req(), db)
    assert _claims(out.access_token)["auth_time"] == now

    async def no_amr(_t):
        return {"sub": _UID, "email": _EMAIL, "iat": now}

    monkeypatch.setattr(auth_ep, "verify_supabase_token", no_amr)
    rate_limiter.clear()
    out = await auth_ep.session_exchange(SessionExchangeRequest(supabase_access_token="s" * 40), _Req(), db)
    assert "auth_time" not in _claims(out.access_token), "no amr → unknown, never 'now'"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", " ", "\t"])
async def test_a_blank_proof_header_is_an_invalid_proof(_no_purges, blank):
    db = _DeleteDB()
    with pytest.raises(HTTPException) as ei:
        await _delete(db, reauth=blank)
    assert _code(ei) == "AUTH_REAUTH_REQUIRED" and db.log == []


def test_the_users_stamp_read_falls_back_only_on_a_missing_column():
    calls = []

    class Q:
        def __init__(self, fail_code):
            self.fail_code = fail_code
        def select(self, cols):
            self.cols = cols; return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        def execute(self):
            calls.append(self.cols)
            if "sessions_revoked_at" in self.cols and self.fail_code:
                raise PgError(self.fail_code)
            return SimpleNamespace(data=[{"password_changed_at": None}])

    class DB:
        def __init__(self, code): self.code = code
        def table(self, _): return Q(self.code)

    assert auth_ep._users_stamps(DB("42703"), _UID) == {"password_changed_at": None}
    assert calls[-1] == "password_changed_at"
    with pytest.raises(PgError):
        auth_ep._users_stamps(DB("PGRST202"), _UID)   # not a column miss: no silent fallback
