"""In-app email change: POST /auth/change-email and /auth/change-email/confirm (audit CC-09).

There was no way to change an account's email, and the `public.users` copy of it (read by
change-password, set-password and Profile) never followed a change made in Studio. These tests
pin the two routes; migration 212's sync trigger is pinned at the source level at the bottom.

The properties that matter, each a way the feature could hurt someone:
  * a stolen access token alone cannot move the account: one proof is required (the current
    password, or the recovery code mailed to the CURRENT address), checked against the address
    GoTrue holds — never the body, never the stale `public.users` copy;
  * the change is made with the CALLER'S OWN Supabase token, passed explicitly. The GoTrue client
    is a process-wide singleton whose stored session another request's sign-in can replace, so
    `update_user()` (which reads the stored session) could change SOMEONE ELSE'S email;
  * an address another account already uses is answered exactly like a real send (enumeration);
  * every failure is typed (invariant #3), and a spent recovery code says so.

No network: the auth client, its admin API and `public.users` are fakes.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gotrue.errors import AuthApiError, AuthRetryableError
from pydantic import ValidationError

import app.api.v1.endpoints.auth as auth_ep
from app.core.security import rate_limiter
from app.schemas.auth import (
    ChangeEmailRequest, ConfirmEmailChangeRequest, EmailChangeResponse,
)
from app.services.auth_methods_service import auth_methods_service

_USER_ID = "11111111-2222-4333-8444-555555555555"
_OTHER_ID = "99999999-8888-4777-8666-555555555555"
_CURRENT = "owner@example.com"
_STALE_COPY = "old-address@example.com"
_NEW = "new-home@example.com"
_SUPABASE_TOKEN = "USER-SUPABASE-JWT"
_BACKEND = Path(__file__).resolve().parents[1]


# ── Fakes ────────────────────────────────────────────────────────────────────

class _U:
    def __init__(self, uid, email, new_email=None):
        self.id, self.email, self.new_email = uid, email, new_email


class _R:
    def __init__(self, user=None, session=None):
        self.user, self.session = user, session


class _Session:
    def __init__(self, token):
        self.access_token = token


class _Admin:
    def __init__(self, outer):
        self._o = outer

    def get_user_by_id(self, uid):
        self._o.calls.append(("admin.get_user_by_id", uid))
        if self._o.admin_raises is not None:
            raise self._o.admin_raises
        return _R(user=self._o.admin_user)


class _Auth:
    def __init__(self, outer):
        self._o = outer
        self.admin = _Admin(outer)

    def sign_in_with_password(self, creds):
        self._o.calls.append(("sign_in_with_password", creds["email"]))
        if self._o.sign_in_raises is not None:
            raise self._o.sign_in_raises
        return self._o.proof

    def verify_otp(self, params):
        self._o.calls.append(("verify_otp", params["email"], params["type"]))
        if self._o.otp_raises is not None:
            raise self._o.otp_raises
        return self._o.proof

    def update_user(self, *_a, **_k):  # must never be called: it reads the shared session
        self._o.calls.append(("update_user",))
        raise AssertionError("update_user reads the singleton's stored session")

    def _request(self, method, path, *, jwt=None, body=None, xform=None, **_k):
        self._o.calls.append(("_request", method, path, jwt, json.dumps(body, sort_keys=True)))
        if path == "user":
            if self._o.put_raises is not None:
                raise self._o.put_raises
            return xform(self._o.put_answer)
        if path == "verify":
            outcome = self._o.verify_script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return xform(outcome)
        raise AssertionError(f"unexpected GoTrue call {method} {path}")


class _Q:
    def __init__(self, outer):
        self._o = outer
        self._payload = None

    def select(self, *_a): return self
    def update(self, payload): self._payload = payload; return self
    def eq(self, *_a): return self
    def limit(self, *_a): return self

    def execute(self):
        if self._payload is not None:
            if self._o.mirror_raises:
                raise RuntimeError("PostgREST is down")
            self._o.mirrored.append(self._payload)
            return type("R", (), {"data": [self._payload]})()
        return type("R", (), {"data": [{"id": _USER_ID, "email": _STALE_COPY}]})()


class _RPC:
    def __init__(self, outer):
        self._o = outer

    def execute(self):
        return type("R", (), {"data": self._o.methods})()


class SB:
    def __init__(self, *, pending=None, has_password=True):
        self.calls: list = []
        self.mirrored: list = []
        self.auth = _Auth(self)
        self.admin_user = _U(_USER_ID, _CURRENT, pending)
        self.admin_raises = None
        self.sign_in_raises = None
        self.otp_raises = None
        self.proof = _R(user=_U(_USER_ID, _CURRENT), session=_Session(_SUPABASE_TOKEN))
        self.put_raises = None
        self.put_answer = {"id": _USER_ID, "email": _CURRENT, "new_email": _NEW}
        self.verify_script: list = []
        self.mirror_raises = False
        self.methods = {"has_password": has_password, "providers": ["email"] if has_password else ["apple"]}

    def table(self, _name):
        return _Q(self)

    def rpc(self, _name, _params):
        return _RPC(self)

    def names(self):
        return [c[0] for c in self.calls]


class _Req:
    def __init__(self, ip="203.0.113.77"):
        self.client = type("C", (), {"host": ip})()
        self.headers = {}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rate_limiter.clear()
    auth_methods_service._cache.clear()
    monkeypatch.setattr(auth_ep, "_EMAIL_TAKEN_PAUSE_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(auth_ep, "_CONFIRM_NOTHING_PENDING_PAUSE_SECONDS", (0.0, 0.0))
    # Most tests start several changes in a row; the spacing has its own tests below.
    monkeypatch.setattr(auth_ep, "CHANGE_EMAIL_SEND_SPACING_SECONDS", 0)
    auth_ep._email_change_last_send.clear()
    yield
    auth_ep._email_change_last_send.clear()
    rate_limiter.clear()
    auth_methods_service._cache.clear()


async def _start(sb, *, new_email=_NEW, password="Current-pass1!", code=None, ip="203.0.113.77"):
    body = {"new_email": new_email}
    if code is not None:
        body["code"] = code
    else:
        body["current_password"] = password
    return await auth_ep.change_email(
        ChangeEmailRequest(**body), _Req(ip), user_id=_USER_ID, supabase=sb,
    )


async def _confirm(sb, current_code="11112222", new_code="33334444"):
    return await auth_ep.confirm_email_change(
        ConfirmEmailChangeRequest(current_code=current_code, new_code=new_code),
        _Req(), user_id=_USER_ID, supabase=sb,
    )


def _err(exc_info) -> dict:
    return exc_info.value.detail


# ── The request schema ───────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"new_email": _NEW},                                                     # no proof
    {"new_email": _NEW, "current_password": "x", "code": "12345678"},        # two proofs
    {"new_email": "not-an-address", "current_password": "x"},
    {"new_email": "", "current_password": "x"},
    {"new_email": _NEW, "current_password": ""},                             # empty password
    {"new_email": _NEW, "code": "12345"},                                    # 5 digits
    {"new_email": _NEW, "code": "1234567890123"},                            # 13 digits
    {"new_email": _NEW, "code": "abcdefgh"},
    {"new_email": _NEW, "current_password": "x" * 1025},                     # over the cap
])
def test_change_email_request_refuses_bad_bodies_before_any_work(body):
    with pytest.raises(ValidationError):
        ChangeEmailRequest(**body)


def test_change_email_request_normalises_a_pasted_code():
    assert ChangeEmailRequest(new_email=_NEW, code="1234 5678").code == "12345678"
    assert ChangeEmailRequest(new_email=_NEW, code="1234-5678").code == "12345678"


@pytest.mark.parametrize("field", ["current_code", "new_code"])
@pytest.mark.parametrize("bad", ["", "12345", "x" * 40, "abcdefgh"])
def test_confirm_request_refuses_bad_codes(field, bad):
    body = {"current_code": "12345678", "new_code": "87654321"}
    body[field] = bad
    with pytest.raises(ValidationError):
        ConfirmEmailChangeRequest(**body)


def test_secrets_never_reach_a_repr():
    r = ChangeEmailRequest(new_email=_NEW, current_password="Sup3r-secret!")
    assert "Sup3r-secret!" not in repr(r)
    c = ConfirmEmailChangeRequest(current_code="11112222", new_code="33334444")
    assert "11112222" not in repr(c) and "33334444" not in repr(c)


# ── POST /auth/change-email ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_password_proof_uses_gotrues_address_and_the_callers_own_token():
    sb = SB()
    resp = await _start(sb)

    assert resp.status == "codes_sent" and resp.email == _NEW
    # The proof is checked against GoTrue's address, NOT the stale public.users copy (CC-09).
    assert ("sign_in_with_password", _CURRENT) in sb.calls
    assert all(c[1] != _STALE_COPY for c in sb.calls if c[0] == "sign_in_with_password")
    put = [c for c in sb.calls if c[0] == "_request" and c[2] == "user"]
    assert put == [("_request", "PUT", "user", _SUPABASE_TOKEN, json.dumps({"email": _NEW}))]
    assert "update_user" not in sb.names()


@pytest.mark.asyncio
async def test_code_proof_verifies_a_recovery_code_for_the_current_address():
    sb = SB(has_password=False)
    resp = await _start(sb, code="12345678")

    assert resp.status == "codes_sent"
    assert ("verify_otp", _CURRENT, "recovery") in sb.calls
    assert "sign_in_with_password" not in sb.names()


@pytest.mark.asyncio
@pytest.mark.parametrize("typed", [_CURRENT, _CURRENT.upper(), f"Owner@Example.COM"])
async def test_the_current_address_is_refused_without_any_gotrue_call(typed):
    sb = SB()
    with pytest.raises(HTTPException) as exc:
        await _start(sb, new_email=typed)
    assert exc.value.status_code == 400
    assert _err(exc)["error_code"] == "AUTH_EMAIL_UNCHANGED"
    assert _err(exc)["action"] == "fix_input"
    assert sb.names() == ["admin.get_user_by_id"]


@pytest.mark.asyncio
async def test_a_wrong_password_is_the_field_not_the_session():
    sb = SB()
    sb.sign_in_raises = AuthApiError("Invalid login credentials", 400, "invalid_credentials")
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert _err(exc)["error_code"] == "AUTH_CREDENTIALS_INVALID"
    assert _err(exc)["user_message"] == "Your current password is incorrect."
    assert not [c for c in sb.calls if c[0] == "_request"], "changed the email without a proof"


@pytest.mark.asyncio
async def test_a_password_on_a_passwordless_account_never_tries_to_sign_in():
    sb = SB(has_password=False)
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert _err(exc)["error_code"] == "AUTH_PASSWORD_NOT_SET"
    assert "code" in _err(exc)["user_message"].lower()
    assert "sign_in_with_password" not in sb.names()


@pytest.mark.asyncio
async def test_a_sign_in_outage_is_ours_and_retryable():
    sb = SB()
    sb.sign_in_raises = AuthRetryableError("connection reset", 0)
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert exc.value.status_code == 503
    assert _err(exc)["error_code"] == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_wrong_recovery_code_is_auth_code_invalid():
    sb = SB(has_password=False)
    sb.otp_raises = AuthApiError("Token has expired or is invalid", 403, "otp_expired")
    with pytest.raises(HTTPException) as exc:
        await _start(sb, code="12345678")
    assert exc.value.status_code == 400
    assert _err(exc)["error_code"] == "AUTH_CODE_INVALID"
    assert not [c for c in sb.calls if c[0] == "_request"]


@pytest.mark.asyncio
async def test_a_proof_for_another_account_is_refused_before_the_change():
    sb = SB()
    sb.proof = _R(user=_U(_OTHER_ID, "someone@example.com"), session=_Session("OTHER-JWT"))
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert _err(exc)["error_code"] == "AUTH_FORBIDDEN"
    assert not [c for c in sb.calls if c[0] == "_request"]


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", [
    _R(user=_U(_USER_ID, _CURRENT), session=None),
    _R(user=_U(_USER_ID, _CURRENT), session=_Session("")),
    _R(user=None, session=_Session(_SUPABASE_TOKEN)),
])
@pytest.mark.parametrize("by_code", [False, True])
async def test_a_proof_without_a_session_never_changes_anything(proof, by_code):
    sb = SB(has_password=not by_code)
    sb.proof = proof
    with pytest.raises(HTTPException) as exc:
        await _start(sb, code="12345678" if by_code else None)
    # A verified recovery code is SPENT: the user must be told to get a new one.
    assert _err(exc)["error_code"] == ("AUTH_CODE_USED" if by_code else "AUTH_UNAVAILABLE")
    assert not [c for c in sb.calls if c[0] == "_request"]


@pytest.mark.asyncio
@pytest.mark.parametrize("taken", [
    AuthApiError("A user with this email address has already been registered", 422, "email_exists"),
    AuthApiError("User already registered", 422, "user_already_exists"),
    RuntimeError("A user with this email address has already been registered"),  # untyped
])
async def test_a_taken_address_reads_exactly_like_a_sent_pair_of_codes(taken, caplog):
    """Enumeration: the answer for an address another account holds must be byte-identical to
    a real send, and the log must not record which address it was."""
    real = await _start(SB())
    rate_limiter.clear()

    sb = SB()
    sb.put_raises = taken
    with caplog.at_level("INFO"):
        answered = await _start(sb)

    assert answered.model_dump() == real.model_dump()
    assert _NEW not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["email_address_invalid", "validation_failed"])
async def test_an_address_the_provider_refuses_is_the_users_to_fix(code):
    sb = SB()
    sb.put_raises = AuthApiError("Unable to validate email address: invalid format", 400, code)
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert exc.value.status_code == 400
    assert _err(exc)["error_code"] == "AUTH_EMAIL_REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, by_code, expected", [
    (AuthApiError("Rate limit", 429, "over_email_send_rate_limit"), False, "AUTH_UNAVAILABLE"),
    (AuthApiError("Rate limit", 429, "over_email_send_rate_limit"), True, "AUTH_CODE_USED"),
    (AuthApiError("Error sending email", 500, "unexpected_failure"), False, "AUTH_UNAVAILABLE"),
    (AuthApiError("Error sending email", 500, "unexpected_failure"), True, "AUTH_CODE_USED"),
    (AuthRetryableError("timeout", 0), False, "AUTH_UNAVAILABLE"),
    (AuthApiError("Email address not authorized", 400, "email_address_not_authorized"), False, "AUTH_UNAVAILABLE"),
])
async def test_a_failed_change_is_typed_and_says_whether_the_code_is_spent(failure, by_code, expected, caplog):
    sb = SB(has_password=not by_code)
    sb.put_raises = failure
    with caplog.at_level("ERROR"), pytest.raises(HTTPException) as exc:
        await _start(sb, code="12345678" if by_code else None)
    assert _err(exc)["error_code"] == expected
    if expected == "AUTH_CODE_USED":
        assert "code has now been used" in _err(exc)["user_message"]
    if getattr(failure, "status", None) == 429:
        assert "GOTRUE_RATE_LIMITED route=change-email" in caplog.text


@pytest.mark.asyncio
async def test_a_project_with_confirmation_off_reports_the_change_honestly(caplog):
    sb = SB()
    sb.put_answer = {"id": _USER_ID, "email": _NEW, "new_email": ""}
    with caplog.at_level("ERROR"):
        resp = await _start(sb)
    assert resp.status == "changed" and resp.email == _NEW
    assert sb.mirrored == [{"email": _NEW}]
    assert "WITHOUT confirmation" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, [], "ok", {"id": _USER_ID}])
async def test_an_odd_success_answer_is_read_as_codes_sent(answer):
    sb = SB()
    sb.put_answer = answer
    resp = await _start(sb)
    assert resp.status == "codes_sent"
    assert sb.mirrored == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, expected, status", [
    (AuthApiError("User not found", 404, "user_not_found"), "AUTH_ACCOUNT_NOT_FOUND", 401),
    (AuthRetryableError("timeout", 0), "AUTH_UNAVAILABLE", 503),
])
async def test_the_account_read_failures_are_typed(failure, expected, status):
    sb = SB()
    sb.admin_raises = failure
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert (exc.value.status_code, _err(exc)["error_code"]) == (status, expected)
    assert sb.names() == ["admin.get_user_by_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [_U(_USER_ID, None), _U(_USER_ID, "   ")])
async def test_an_account_without_an_email_is_forbidden_not_signed_out(user):
    sb = SB()
    sb.admin_user = user
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert exc.value.status_code == 403
    assert _err(exc)["error_code"] == "AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_change_email_is_limited_per_user_and_per_ip():
    sb = SB()
    for _ in range(auth_ep.CHANGE_EMAIL_LIMIT_PER_USER[0]):
        await _start(sb)
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == str(auth_ep.CHANGE_EMAIL_LIMIT_PER_USER[1])

    rate_limiter.clear()
    ip_max = auth_ep.CHANGE_EMAIL_LIMIT_PER_IP[0]
    for i in range(ip_max):
        with pytest.raises(HTTPException) as exc:  # AUTH_EMAIL_UNCHANGED: cheap, still counted
            await _start(SB(), new_email=_CURRENT)
        assert exc.value.status_code == 400
        rate_limiter._protected.pop(f"changeemail:user:{_USER_ID}", None)
        rate_limiter._protected.pop(f"change_mail:email:{_CURRENT}", None)  # isolate the IP limit
    with pytest.raises(HTTPException) as exc:
        await _start(SB())
    assert exc.value.status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize("first_put_raises", [
    None,                                                                           # a free address
    AuthApiError("A user with this email address has already been registered", 422, "email_exists"),
])
async def test_a_second_start_within_the_spacing_reads_the_same_for_taken_and_free(monkeypatch, first_put_raises):
    """Independent audit, 2026-11-26: GoTrue checks "taken" BEFORE its per-user send interval, so
    a second start within a minute answered a free address 503 and a taken one 200. The backend's
    own spacing now answers BOTH with one 429, before any GoTrue call."""
    monkeypatch.setattr(auth_ep, "CHANGE_EMAIL_SEND_SPACING_SECONDS", 65)
    sb = SB()
    sb.put_raises = first_put_raises
    first = await _start(sb)
    assert first.status == "codes_sent"
    n_calls = len(sb.calls)
    with pytest.raises(HTTPException) as exc:
        await _start(sb, new_email="someone-else@example.com")
    assert exc.value.status_code == 429
    assert exc.value.detail == "Please wait a minute before asking for new codes."
    assert 0 < int(exc.value.headers["Retry-After"]) <= 65
    assert len(sb.calls) == n_calls, "the refused start reached GoTrue"


@pytest.mark.asyncio
async def test_a_refused_proof_does_not_start_the_spacing(monkeypatch):
    """Only a send attempt is charged: a mistyped password must not lock the user out for a
    minute, and a recovery code must never be spent by a start the spacing then refuses."""
    monkeypatch.setattr(auth_ep, "CHANGE_EMAIL_SEND_SPACING_SECONDS", 65)
    sb = SB()
    sb.sign_in_raises = AuthApiError("Invalid login credentials", 400, "invalid_credentials")
    with pytest.raises(HTTPException):
        await _start(sb)
    sb.sign_in_raises = None
    assert (await _start(sb)).status == "codes_sent"


@pytest.mark.asyncio
async def test_the_spacing_lifts_once_it_has_passed(monkeypatch):
    monkeypatch.setattr(auth_ep, "CHANGE_EMAIL_SEND_SPACING_SECONDS", 65)
    clock = [1000.0]
    monkeypatch.setattr(auth_ep.time, "monotonic", lambda: clock[0])
    sb = SB()
    await _start(sb)
    clock[0] += 64.5
    with pytest.raises(HTTPException) as exc:
        await _start(sb)
    assert exc.value.headers["Retry-After"] == "1"
    clock[0] += 0.6
    assert (await _start(sb)).status == "codes_sent"


def test_the_spacing_constant_in_source_is_at_least_sixty_seconds():
    src = (_BACKEND / "app" / "api" / "v1" / "endpoints" / "auth.py").read_text(encoding="utf-8")
    m = re.search(r"^CHANGE_EMAIL_SEND_SPACING_SECONDS = (\d+)$", src, re.M)
    assert m and int(m.group(1)) >= 60, "the spacing must cover Supabase's per-user send interval"
    start = _code_of("change_email")
    assert start.index("_email_change_send_wait(") < start.index("_read_auth_account("), (
        "the spacing must refuse before any GoTrue call"
    )
    assert start.index("_mark_email_change_send(") < start.index('"PUT", "user"'), (
        "the spacing must be charged before the send, taken or not"
    )


# ── POST /auth/change-email/confirm ──────────────────────────────────────────

_SESSION = {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "user": {"id": _USER_ID}}
_PARTIAL = {"msg": "Confirmation link accepted. Please proceed to confirm link sent to the other email", "code": 200}
_REFUSED = AuthApiError("Token has expired or is invalid", 403, "otp_expired")


def _verifies(sb):
    return [json.loads(c[4]) for c in sb.calls if c[0] == "_request" and c[2] == "verify"]


@pytest.mark.asyncio
async def test_confirm_checks_each_code_against_gotrues_addresses():
    sb = SB(pending=_NEW)
    sb.verify_script = [_PARTIAL, _SESSION]
    resp = await _confirm(sb)

    assert resp.status == "changed" and resp.email == _NEW
    assert _verifies(sb) == [
        {"type": "email_change", "email": _CURRENT, "token": "11112222"},
        {"type": "email_change", "email": _NEW, "token": "33334444"},
    ]
    assert sb.mirrored == [{"email": _NEW}]


@pytest.mark.asyncio
async def test_confirm_with_nothing_pending_reads_exactly_like_two_wrong_codes():
    """Enumeration. A start aimed at ANOTHER account's address leaves nothing pending (GoTrue
    refused it, though the caller was told "codes sent"). A distinct "nothing to confirm" answer
    would therefore reveal that the address is registered, for the price of two junk codes."""
    nothing = SB(pending=None)
    with pytest.raises(HTTPException) as none_exc:
        await _confirm(nothing)
    assert _verifies(nothing) == [], "spent nothing, so there is nothing to verify"

    rate_limiter.clear()
    wrong = SB(pending=_NEW)
    wrong.verify_script = [_REFUSED, _REFUSED]
    with pytest.raises(HTTPException) as wrong_exc:
        await _confirm(wrong)

    for key in ("error_code", "user_message", "action"):
        assert _err(none_exc)[key] == _err(wrong_exc)[key], key
    assert none_exc.value.status_code == wrong_exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("script, n_verifies", [
    ([_SESSION], 1),                 # the new-address code was accepted on an earlier attempt
    ([_REFUSED, _SESSION], 2),       # the current-address code was accepted earlier (retry)
])
async def test_confirm_finishes_a_change_half_done_on_an_earlier_attempt(script, n_verifies):
    sb = SB(pending=_NEW)
    sb.verify_script = script
    resp = await _confirm(sb)
    assert resp.status == "changed"
    assert len(_verifies(sb)) == n_verifies


@pytest.mark.asyncio
@pytest.mark.parametrize("script, names", [
    ([_PARTIAL, _REFUSED], _NEW),
    ([_REFUSED, _PARTIAL], _CURRENT),
])
async def test_confirm_names_the_address_whose_code_failed(script, names):
    sb = SB(pending=_NEW)
    sb.verify_script = script
    with pytest.raises(HTTPException) as exc:
        await _confirm(sb)
    assert exc.value.status_code == 400
    assert _err(exc)["error_code"] == "AUTH_CODE_INVALID"
    assert names in _err(exc)["user_message"]
    other = _CURRENT if names == _NEW else _NEW
    assert other not in _err(exc)["user_message"]
    assert sb.mirrored == []


@pytest.mark.asyncio
async def test_confirm_with_both_codes_refused_is_one_generic_message():
    sb = SB(pending=_NEW)
    sb.verify_script = [_REFUSED, _REFUSED]
    with pytest.raises(HTTPException) as exc:
        await _confirm(sb)
    assert _err(exc)["error_code"] == "AUTH_CODE_INVALID"
    assert "Those codes are incorrect" in _err(exc)["user_message"]


@pytest.mark.asyncio
async def test_confirm_with_two_partial_answers_is_ours_not_the_users():
    sb = SB(pending=_NEW)
    sb.verify_script = [_PARTIAL, _PARTIAL]
    with pytest.raises(HTTPException) as exc:
        await _confirm(sb)
    assert _err(exc)["error_code"] == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("outage", [
    AuthRetryableError("connection reset", 0),
    AuthApiError("Rate limit", 429, "over_request_rate_limit"),
    AuthApiError("boom", 500, "unexpected_failure"),
])
async def test_a_verify_outage_stops_before_the_second_code(outage):
    sb = SB(pending=_NEW)
    sb.verify_script = [outage, _SESSION]
    with pytest.raises(HTTPException) as exc:
        await _confirm(sb)
    assert exc.value.status_code == 503
    assert _err(exc)["error_code"] == "AUTH_UNAVAILABLE"
    assert len(_verifies(sb)) == 1, "spent the second code while GoTrue was failing"


@pytest.mark.asyncio
@pytest.mark.parametrize("odd", [None, [], "", {"access_token": ""}, {"user": {"id": _USER_ID}}])
async def test_an_answer_without_a_session_is_never_read_as_complete(odd):
    sb = SB(pending=_NEW)
    sb.verify_script = [odd, _REFUSED]
    with pytest.raises(HTTPException) as exc:
        await _confirm(sb)
    assert _err(exc)["error_code"] == "AUTH_CODE_INVALID"


@pytest.mark.asyncio
async def test_a_failed_mirror_write_does_not_undo_a_completed_change(caplog):
    sb = SB(pending=_NEW)
    sb.verify_script = [_PARTIAL, _SESSION]
    sb.mirror_raises = True
    with caplog.at_level("WARNING"):
        resp = await _confirm(sb)
    assert resp.status == "changed"
    assert "could not mirror the new address" in caplog.text


@pytest.mark.asyncio
async def test_confirm_is_limited_per_user():
    sb = SB(pending=_NEW)
    for _ in range(auth_ep.CONFIRM_EMAIL_CHANGE_LIMIT_PER_USER[0]):
        sb.verify_script = [_REFUSED, _REFUSED]
        with pytest.raises(HTTPException):
            await _confirm(sb)
    with pytest.raises(HTTPException) as exc:
        await _confirm(sb)
    assert exc.value.status_code == 429


# ── The SDK seam the cross-user safety rests on ──────────────────────────────

def test_the_sdk_puts_the_explicit_token_over_the_stored_session():
    """`_request(jwt=...)` must win over the Authorization header stored on the shared client —
    that is what keeps another request's sign-in from redirecting this change. Pinned on the
    installed SDK so an upgrade that changes it fails here, not in production."""
    import gotrue

    sent = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": _USER_ID, "email": _CURRENT, "new_email": _NEW}

    class _Http:
        def request(self, method, url, headers=None, params=None, json=None):
            sent.update(method=method, url=url, headers=dict(headers or {}), json=json)
            return _Resp()

    client = gotrue.SyncGoTrueClient(
        url="https://example.supabase.co/auth/v1",
        headers={"Authorization": "Bearer SOMEONE-ELSES-JWT", "apikey": "k"},
        http_client=_Http(),
    )
    out = client._request(
        "PUT", "user", jwt=_SUPABASE_TOKEN, body={"email": _NEW}, xform=auth_ep._raw_json,
    )
    assert sent["headers"]["Authorization"] == f"Bearer {_SUPABASE_TOKEN}"
    assert sent["json"] == {"email": _NEW} and sent["method"] == "PUT"
    assert out == {"id": _USER_ID, "email": _CURRENT, "new_email": _NEW}


def _code_of(fn_name: str) -> str:
    src = (_BACKEND / "app" / "api" / "v1" / "endpoints" / "auth.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == fn_name:
            seg = ast.get_source_segment(src, node)
            return "\n".join(l for l in seg.splitlines() if not l.lstrip().startswith("#"))
    raise AssertionError(f"{fn_name} not found")


def test_neither_route_uses_the_stored_session_verbs():
    for name in ("change_email", "confirm_email_change", "_verify_email_change_code"):
        code = _code_of(name)
        assert ".update_user(" not in code and ".update_user," not in code, name
        assert ".get_session(" not in code, name
    assert "jwt=supabase_token" in _code_of("change_email")


# ── Migration 212: the public.users copy follows auth.users ──────────────────

def _strip_sql(text: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


def test_migration_212_syncs_email_on_update_and_fails_open():
    sql = _strip_sql((_BACKEND / "database" / "migrations" / "212_sync_public_user_email.sql").read_text())
    flat = " ".join(sql.split())
    assert re.search(
        r"CREATE TRIGGER on_auth_user_email_changed AFTER UPDATE OF email ON auth\.users "
        r"FOR EACH ROW WHEN \(OLD\.email IS DISTINCT FROM NEW\.email\) "
        r"EXECUTE FUNCTION public\.sync_public_user_email\(\)", flat,
    ), "the sync trigger is missing or no longer fires on an email UPDATE"
    body = flat[flat.index("CREATE OR REPLACE FUNCTION public.sync_public_user_email"):flat.index("COMMENT ON FUNCTION")]
    assert "EXCEPTION WHEN OTHERS THEN RAISE WARNING" in body, "a mirror failure must never block GoTrue"
    assert "IF NEW.email IS NULL" in body, "public.users.email is NOT NULL: a NULL must be skipped"
    assert "email_change" not in body, "the PENDING address must never be synced"
    assert "DROP TRIGGER IF EXISTS on_auth_user_email_changed ON auth.users" in flat
    # The backfill touches only drifted rows (a re-run updates nothing).
    assert "AND p.email IS DISTINCT FROM a.email" in flat


# ── Review 2026-11-27: change-email shares the hourly mail ceilings ─────────────────────

@pytest.mark.asyncio
async def test_a_change_email_start_counts_twice_against_the_hourly_ip_mail_ceiling():
    """It mails two addresses, and the sign-up / resend / reset routes share the same per-IP
    hourly ceiling (MAIL_PER_IP_PER_HOUR). With room for one email only, a start is refused."""
    ip = "203.0.113.150"
    for _ in range(auth_ep.MAIL_PER_IP_PER_HOUR - 1):
        assert rate_limiter.is_allowed(
            f"mail:ip:h:{ip}", max_requests=auth_ep.MAIL_PER_IP_PER_HOUR,
            window_seconds=auth_ep.MAIL_HOUR_SECONDS, protected=True)
    with pytest.raises(HTTPException) as exc:
        await _start(SB(), ip=ip)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == str(auth_ep.MAIL_HOUR_SECONDS)


@pytest.mark.asyncio
async def test_one_new_address_gets_at_most_five_change_codes_an_hour_whatever_the_ip():
    """The caller chooses the new address, so it is a mail target: charged at entry on every
    attempt (a taken and a free address answer alike)."""
    for i in range(auth_ep.CHANGE_MAIL_PER_RECIPIENT_PER_HOUR):
        await _start(SB(), ip=f"203.0.113.{160 + i}")
        rate_limiter._protected.pop(f"changeemail:user:{_USER_ID}", None)
    with pytest.raises(HTTPException) as exc:
        await _start(SB(), ip="203.0.113.199")
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == str(auth_ep.MAIL_HOUR_SECONDS)

