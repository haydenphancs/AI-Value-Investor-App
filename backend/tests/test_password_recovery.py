"""Password recovery: enumeration resistance, brute-force limits, session eviction.

Before this, a user who forgot their password was permanently locked out — there was no
recovery path at all (`auth.py` had only login/register/refresh/logout).

The three properties worth pinning, because each is easy to get subtly wrong:

1. **No account enumeration.** `/forgot-password` must answer identically for a registered
   and an unregistered address. A 404 for unknown emails turns it into an oracle for which
   addresses have accounts on a finance app — exactly the list a credential-stuffer wants.
2. **Brute-force resistance.** The code is short (8 digits as the project is configured; the
   schema accepts 6-12). Rate limits are applied per-IP AND per-email independently: per-IP
   alone lets a distributed caller hammer one victim; per-email alone lets one host enumerate
   many addresses.
3. **Session eviction.** The app mints its OWN JWTs and validates them on signature +
   expiry alone, so a reset must stamp `password_changed_at` or a thief keeps access for
   the full 7-day refresh lifetime — defeating the point of resetting (migration 105).

No network / Supabase: the client is a fake, and the shared in-process rate limiter is
reset between tests so limits from one test can't leak into another.
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # gotrue announces its own rename
    from gotrue.helpers import handle_exception

from app.api.v1.endpoints import auth as auth_ep
from app.core.security import create_access_token, create_refresh_token, rate_limiter
from _refresh_session_fake import SessionStoreFake
from app.schemas.auth import (
    ChangePasswordRequest,
    ConfirmEmailRequest,
    ForgotPasswordRequest,
    PASSWORD_MAX_BYTES,
    PASSWORD_MIN_LENGTH,
    PASSWORD_SYMBOLS,
    PASSWORD_TOO_LONG_MESSAGE,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SetPasswordRequest,
    SignInRequest,
    SignUpRequest,
    _validate_password_strength,
)

_USER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
_EMAIL = "someone@example.com"
_GOOD_PASSWORD = "Correct horse battery 1!"
# Compliant with `_validate_password_strength` (8+, upper, lower, digit, symbol),
# which mirrors the Supabase Email-provider setting. A weak literal here fails at
# SCHEMA construction, so every test in the file dies before reaching what it asserts.



@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """The limiter is process-wide; without this, tests poison each other.

    Uses `clear()` rather than reaching for `_requests`: credential limiters live in a
    separate pool, and a name-guessing loop silently stopped covering them.
    """
    rate_limiter.clear()
    yield


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeRequest:
    def __init__(self, ip="203.0.113.10"):
        self.client = type("C", (), {"host": ip})()


def _gotrue_error(status: int, body: dict | None = None, *, text: str | None = None) -> Exception:
    """The exception gotrue itself raises for this GoTrue answer (its own `handle_exception`)."""
    req = httpx.Request("POST", "https://example.supabase.co/auth/v1/verify")
    if text is not None:
        resp = httpx.Response(status, text=text, request=req)
    else:
        resp = httpx.Response(
            status, json=body or {}, request=req,
            headers={"X-Supabase-Api-Version": "2024-01-01"},
        )
    return handle_exception(httpx.HTTPStatusError("http error", request=req, response=resp))


_OTP_EXPIRED = lambda: _gotrue_error(403, {"code": "otp_expired", "msg": "Token has expired or is invalid"})  # noqa: E731
_WEAK = lambda: _gotrue_error(422, {  # noqa: E731
    "code": "weak_password", "msg": "Password is known to be weak",
    "weak_password": {"reasons": ["pwned"]},
})
# GoTrue's 72-byte cap — `validation_failed`, not weak_password (verified on the live stack).
_TOO_LONG = lambda: _gotrue_error(400, {  # noqa: E731
    "code": "validation_failed", "msg": "Password cannot be longer than 72 characters",
})


class _FakeAdmin:
    def __init__(self, log, fail):
        self._log, self._fail = log, fail

    def update_user_by_id(self, uid, attrs):
        if "update" in self._fail:
            raise RuntimeError("update failed")
        if "weak" in self._fail:
            raise _WEAK()
        if "too_long" in self._fail:
            raise _TOO_LONG()
        if "update_outage" in self._fail:
            raise handle_exception(httpx.ConnectError("connection refused"))
        if "update_429" in self._fail:
            raise _gotrue_error(429, {"code": "over_request_rate_limit", "msg": "rate limited"})
        self._log.append(("admin.update_user_by_id", uid, sorted(attrs)))


class _FakeAuth:
    def __init__(self, log, fail, otp_user_id):
        self._log, self._fail = log, fail
        self._otp_user_id = otp_user_id
        self.admin = _FakeAdmin(log, fail)

    def reset_password_for_email(self, email):
        if "send" in self._fail:
            raise RuntimeError("smtp down")
        self._log.append(("reset_password_for_email", email))

    def verify_otp(self, params):
        if "otp" in self._fail:
            raise RuntimeError("invalid otp")
        if "otp_expired" in self._fail:
            raise _OTP_EXPIRED()
        if "otp_outage" in self._fail:
            raise handle_exception(httpx.ConnectError("connection refused"))
        if "otp_429" in self._fail:
            raise _gotrue_error(429, {"code": "over_request_rate_limit", "msg": "rate limited"})
        self._log.append(("verify_otp", params.get("email"), params.get("type"), params.get("token")))
        if self._otp_user_id is None:
            return type("R", (), {"user": None})()
        return type("R", (), {"user": type("U", (), {"id": self._otp_user_id})()})()

    def sign_in_with_password(self, creds):
        if "signin" in self._fail:
            raise RuntimeError("bad credentials")
        # A failure that is OURS. Worded so it does NOT match `_is_rejected_credential` —
        # that classifier is the only thing separating a wrong password from an outage.
        if "outage" in self._fail:
            raise RuntimeError("upstream gateway timeout contacting the identity provider")
        self._log.append(("sign_in_with_password", creds["email"]))


class _FakeQuery:
    def __init__(self, log, table, row, fail):
        self._log, self._table, self._row, self._fail = log, table, row, fail
        self._update = None
        # PostgREST returns a bare OBJECT for single() and a LIST for limit()/plain. Modelling
        # that matters: a fake that always returned the object let `rows[0]` pass here and
        # KeyError in production. Mirrors the same handling in
        # tests/test_auth_confirmation_and_oauth.py.
        self._shape = "single"

    def select(self, *cols, **_k):
        self._cols = " ".join(str(c) for c in cols)
        return self

    def update(self, values):
        self._update = values
        return self

    def eq(self, *_a, **_k):
        return self

    def single(self):
        self._shape = "single"
        return self

    def limit(self, *_a, **_k):
        # Needed by `_app_user_row_exists` and the change-password email lookup. Without it the
        # call raised AttributeError, which the helper deliberately treats as a transport blip
        # and FAILS OPEN — so the account-existence check looked covered while never running.
        self._shape = "list"
        return self

    def execute(self):
        if self._update is not None:
            if "stamp" in self._fail:
                raise RuntimeError("stamp failed")
            if "stamp_once" in self._fail:
                self._fail.discard("stamp_once")
                raise RuntimeError("stamp failed once")
            self._log.append(("update", self._table, sorted(self._update)))
            return type("R", (), {"data": [self._update]})()
        if "lookup" in self._fail:
            raise RuntimeError("lookup failed")
        # Fails ONLY the password_changed_at read, so the existence check passes first.
        if "stale_lookup" in self._fail and "password_changed_at" in getattr(self, "_cols", ""):
            raise RuntimeError("stale lookup failed")
        # Fails ONLY the existence read (`select("id")`), so the staleness read cannot mask it.
        if "exists_lookup" in self._fail and getattr(self, "_cols", "") == "id":
            raise RuntimeError("existence lookup failed")
        data = self._row if self._shape == "single" else ([self._row] if self._row else [])
        return type("R", (), {"data": data})()


class FakeSupabase:
    def __init__(self, row=None, fail=(), otp_user_id=_USER_ID):
        self.log: list[tuple] = []
        self._row = row if row is not None else {"id": _USER_ID, "email": _EMAIL}
        self._fail = set(fail)
        self.auth = _FakeAuth(self.log, self._fail, otp_user_id)
        # Migration 214's session RPCs. They read the SAME row the table reads do, so a stamp a
        # test puts on the row is what the in-transaction eviction check sees too.
        self.sessions = SessionStoreFake(users={self._row.get("id", _USER_ID): self._row} if self._row else {})

    def table(self, name):
        return _FakeQuery(self.log, name, self._row, self._fail)

    def rpc(self, name, params):
        return self.sessions.rpc(name, params)


# ── 1. No account enumeration ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forgot_password_response_is_identical_for_unknown_email():
    known = await auth_ep.forgot_password(
        ForgotPasswordRequest(email=_EMAIL), _FakeRequest("198.51.100.1"), FakeSupabase()
    )
    # An unknown address makes Supabase raise; the response must not differ.
    unknown = await auth_ep.forgot_password(
        ForgotPasswordRequest(email="nobody@example.com"),
        _FakeRequest("198.51.100.2"),
        FakeSupabase(fail=("send",)),
    )
    assert known.message == unknown.message


@pytest.mark.asyncio
async def test_forgot_password_never_raises_on_provider_failure():
    """A provider outage must look exactly like success, or it becomes an oracle."""
    resp = await auth_ep.forgot_password(
        ForgotPasswordRequest(email=_EMAIL), _FakeRequest(), FakeSupabase(fail=("send",))
    )
    assert "if an account exists" in resp.message.lower()


@pytest.mark.asyncio
async def test_forgot_password_lowercases_the_email():
    sb = FakeSupabase()
    await auth_ep.forgot_password(
        ForgotPasswordRequest(email="MiXeD@Example.COM"), _FakeRequest(), sb
    )
    sent = [e for e in sb.log if e[0] == "reset_password_for_email"]
    assert sent and sent[0][1] == "mixed@example.com"


# ── 2. Brute-force / abuse limits ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forgot_password_is_rate_limited_per_email_across_ips():
    """Per-IP alone is not enough: a distributed caller could email-bomb one victim."""
    for i in range(3):
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=_EMAIL), _FakeRequest(f"198.51.100.{i}"), FakeSupabase()
        )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=_EMAIL), _FakeRequest("198.51.100.99"), FakeSupabase()
        )
    assert ei.value.status_code == 429


@pytest.mark.asyncio
async def test_forgot_password_is_rate_limited_per_ip_across_emails():
    """Per-email alone is not enough: one host could enumerate many addresses. Ten per IP per
    15 minutes since 2026-11-26 (it was five an hour): looser than the address bucket (3), so a
    person's own retries are refused by the address window, never by a longer IP one."""
    for i in range(10):
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=f"u{i}@example.com"), _FakeRequest("203.0.113.7"),
            FakeSupabase(),
        )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email="another@example.com"), _FakeRequest("203.0.113.7"),
            FakeSupabase(),
        )
    assert ei.value.status_code == 429
    assert ei.value.headers["Retry-After"] == "900"


@pytest.mark.asyncio
async def test_forgot_password_per_email_denial_says_900():
    for i in range(3):
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=_EMAIL), _FakeRequest(f"203.0.113.{20 + i}"), FakeSupabase(),
        )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=_EMAIL), _FakeRequest("203.0.113.30"), FakeSupabase(),
        )
    assert ei.value.status_code == 429 and ei.value.headers["Retry-After"] == "900"


@pytest.mark.asyncio
async def test_reset_password_is_rate_limited():
    req = ResetPasswordRequest(email=_EMAIL, code="000000", new_password=_GOOD_PASSWORD)
    for _ in range(10):
        with pytest.raises(HTTPException):
            await auth_ep.reset_password(req, _FakeRequest(), FakeSupabase(fail=("otp",)))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.reset_password(req, _FakeRequest(), FakeSupabase(fail=("otp",)))
    assert ei.value.status_code == 429, "a 6-digit code must not be brute-forceable"


# ── 3. Reset correctness ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_password_verifies_otp_then_sets_password_then_stamps():
    sb = FakeSupabase()
    await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
        _FakeRequest(), sb,
    )
    kinds = [e[0] for e in sb.log]
    assert kinds.index("verify_otp") < kinds.index("admin.update_user_by_id"), (
        "the OTP must be verified BEFORE the password is changed"
    )
    assert ("update", "users", ["password_changed_at"]) in sb.log, (
        "password_changed_at must be stamped so pre-reset tokens stop working"
    )


@pytest.mark.asyncio
async def test_reset_password_uses_the_recovery_otp_type():
    sb = FakeSupabase()
    await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
        _FakeRequest(), sb,
    )
    otp = [e for e in sb.log if e[0] == "verify_otp"][0]
    assert otp[2] == "recovery"


@pytest.mark.asyncio
async def test_reset_password_rejects_a_bad_code_without_touching_the_password():
    sb = FakeSupabase(fail=("otp",))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.reset_password(
            ResetPasswordRequest(email=_EMAIL, code="999999", new_password=_GOOD_PASSWORD),
            _FakeRequest(), sb,
        )
    assert ei.value.status_code == 400
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


@pytest.mark.asyncio
async def test_reset_password_rejects_a_verified_response_with_no_user():
    """verify_otp did not raise, so GoTrue consumed the code: AUTH_CODE_USED, nothing written."""
    sb = FakeSupabase(otp_user_id=None)
    with pytest.raises(HTTPException) as ei:
        await auth_ep.reset_password(
            ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
            _FakeRequest(), sb,
        )
    _assert_code_used(ei.value)
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


_CODE_USED_COPY = (
    "We couldn't set your new password, and that code has now been used. "
    "Send a new code and try again."
)


def _assert_code_used(exc: HTTPException) -> None:
    """C2 / PW-4: after a VERIFIED code the write failed. The code is spent, so NOT the
    retryable AUTH_UNAVAILABLE ("Please try again" could only be answered "invalid code"): a code
    of its own, 503, fix_input, copy that says the code is used and to send a new one. Typed,
    never a bare-string 5xx (iOS's 5xx arm decodes `APIErrorResponse` first)."""
    assert exc.status_code == 503, exc.detail
    assert exc.detail["error_code"] == "AUTH_CODE_USED"
    assert exc.detail["action"] == "fix_input"
    assert exc.detail["user_message"] == _CODE_USED_COPY
    assert not (exc.headers or {}).get("WWW-Authenticate")


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [
    "update",           # untyped
    "update_outage",    # transport (AuthRetryableError)
    "update_429",       # GoTrue 429 on the admin call
    "too_long",         # GoTrue `validation_failed` — C2 files it here, not as a refusal
])
async def test_reset_password_surfaces_a_failed_update_as_a_typed_error(fail, caplog):
    sb = FakeSupabase(fail=(fail,))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.reset_password(
                ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
                _FakeRequest(), sb,
            )
    _assert_code_used(ei.value)
    assert ("update", "users", ["password_changed_at"]) not in sb.log, "nothing changed — no stamp"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a failure after a good code is ours and reaches Sentry"
    assert not any(_EMAIL in m or _GOOD_PASSWORD in m for m in errors)


@pytest.mark.asyncio
async def test_a_verify_outage_and_a_spent_code_are_told_apart_by_code():
    """Before the verify the code is still good (AUTH_UNAVAILABLE, retry); after it, it is gone
    (AUTH_CODE_USED, send a new one). iOS branches on the code, never on the copy."""
    with pytest.raises(HTTPException) as before:
        await auth_ep.reset_password(
            ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
            _FakeRequest(), FakeSupabase(fail=("otp_outage",)),
        )
    rate_limiter.clear()
    with pytest.raises(HTTPException) as after:
        await auth_ep.reset_password(
            ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
            _FakeRequest(), FakeSupabase(fail=("update_outage",)),
        )
    assert (before.value.detail["error_code"], before.value.detail["action"]) == (
        "AUTH_UNAVAILABLE", "retry_later")
    assert (after.value.detail["error_code"], after.value.detail["action"]) == (
        "AUTH_CODE_USED", "fix_input")


def test_reset_and_set_password_raise_no_retryable_code_after_the_verify():
    """Source guard, comment-stripped and bounded to the code AFTER each route's
    `_otp_failure(...)`: once the code is spent nothing may raise AUTH_UNAVAILABLE."""
    import inspect

    for fn, marker in ((auth_ep.reset_password, '_otp_failure(e, route="reset-password")'),
                       (auth_ep.set_password, '_otp_failure(e, route="set-password"')):
        code = "\n".join(l.split("#", 1)[0] for l in inspect.getsource(fn).splitlines())
        after = code[code.index(marker):]
        assert "ErrorCode.AUTH_UNAVAILABLE" not in after, fn.__name__
        assert "_code_used(" in after, fn.__name__


@pytest.mark.asyncio
async def test_reset_succeeds_even_if_the_stamp_write_fails():
    """The password IS already changed by then. Failing the request would tell the user
    their reset didn't work when it did — the stamp failure is logged instead."""
    resp = await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
        _FakeRequest(), FakeSupabase(fail=("stamp",)),
    )
    assert "reset" in resp.message.lower()


# ── 4. Change password (authenticated) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_password_requires_the_current_password():
    """A stolen access token alone must not be enough to seize the account."""
    sb = FakeSupabase(fail=("signin",))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="wrong", new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=sb,
        )
    assert ei.value.status_code == 401
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


@pytest.mark.asyncio
async def test_change_password_verifies_before_updating_and_stamps():
    sb = FakeSupabase()
    await auth_ep.change_password(
        ChangePasswordRequest(current_password="old-password", new_password=_GOOD_PASSWORD),
        _FakeRequest(), user_id=_USER_ID, supabase=sb,
    )
    kinds = [e[0] for e in sb.log]
    assert kinds.index("sign_in_with_password") < kinds.index("admin.update_user_by_id")
    assert ("update", "users", ["password_changed_at"]) in sb.log


@pytest.mark.asyncio
async def test_change_password_rejects_reusing_the_same_password():
    sb = FakeSupabase()
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password=_GOOD_PASSWORD, new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=sb,
        )
    assert ei.value.status_code == 400
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


@pytest.mark.asyncio
async def test_refresh_is_rejected_when_the_account_row_is_gone():
    """Otherwise the client loops forever: profile 401s, refresh SUCCEEDS, repeat.

    `is_token_stale_after_password_change` fails open when its read finds no row, so nothing
    else on this path notices a missing `public.users` row. iOS ends up holding a credential it
    can never validate and never clears — permanently `.restoring`, re-hitting the backend.
    AUTH_ACCOUNT_NOT_FOUND is one of the three codes allowed to clear a credential, so it ends
    the loop rather than feeding it.
    """
    fresh = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    with pytest.raises(HTTPException) as ei:
        await auth_ep.refresh_token(
            RefreshTokenRequest(refresh_token=fresh), _FakeRequest(), FakeSupabase(row={}),
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == "AUTH_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["lookup", "exists_lookup", "stale_lookup"])
async def test_refresh_fails_CLOSED_when_the_users_read_is_unavailable(fail, caplog):
    """REWRITTEN DELIBERATELY (SEC-4). This test used to be
    `test_refresh_still_works_when_the_existence_check_is_unavailable` and pinned the opposite:
    "fail OPEN — a Supabase blip must not lock every user out".

    On `/auth/refresh` fail-open was not harmless: the refresh MINTED a new pair whose `iat`
    post-dates `password_changed_at`, so one transient read failure laundered a PRE-RESET refresh
    token into a permanent post-reset session — and the route allows 60 tries a minute, so an
    attacker simply retries until a blip lands. Now: 503 AUTH_UNAVAILABLE, no tokens. A 503 is
    retryable and iOS keeps its credential on a 5xx, so a blip still logs nobody out.

    Each read is covered ALONE: the existence check (`exists_lookup` — the staleness read would
    succeed), the staleness check (`stale_lookup` — the existence read succeeds first), and both.
    """
    fresh = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    sb = FakeSupabase(row={"id": _USER_ID, "password_changed_at": past}, fail=(fail,))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.refresh_token(
                RefreshTokenRequest(refresh_token=fresh), _FakeRequest(), sb,
            )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert ei.value.detail["action"] == "retry_later"
    assert "access_token" not in str(ei.value.detail)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "a fail-closed read must reach Sentry"


@pytest.mark.asyncio
async def test_change_password_is_rate_limited_per_user_across_ips():
    """This route runs a real `sign_in_with_password` per call, so it is a password oracle
    for anyone holding a stolen access token — the exact threat the current-password
    requirement exists to stop. It was the only credential route with no limiter at all, and
    Supabase's own limiting is not a backstop because every call leaves from one egress IP.

    Keyed per USER, so rotating source addresses does not buy more guesses.
    """
    for i in range(5):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.change_password(
                ChangePasswordRequest(current_password=f"guess-{i}", new_password=_GOOD_PASSWORD),
                _FakeRequest(f"198.51.100.{i}"),
                user_id=_USER_ID, supabase=FakeSupabase(fail=("signin",)),
            )
        assert ei.value.status_code == 401  # wrong password — and the budget is consumed

    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="guess-6", new_password=_GOOD_PASSWORD),
            _FakeRequest("198.51.100.200"),
            user_id=_USER_ID, supabase=FakeSupabase(fail=("signin",)),
        )
    assert ei.value.status_code == 429


@pytest.mark.asyncio
async def test_change_password_upstream_failure_is_503_not_a_wrong_password(caplog):
    """An identity-provider failure must not be reported as "your password is incorrect".

    This block used to map EVERY exception to that message. A GoTrue 429 — which this endpoint
    invites, since every call leaves from the one Railway egress IP — a Supabase 5xx, or a
    connect timeout all told the user something false about their password and sent them to a
    reset they did not need. Worse, the per-user limiter consumes its slot at check time and
    nothing refunds it, so five outage-induced failures locked the real owner out for 15
    minutes. Same classification /login already does.
    """
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.change_password(
                ChangePasswordRequest(
                    current_password="the-correct-one", new_password=_GOOD_PASSWORD
                ),
                _FakeRequest(),
                user_id=_USER_ID,
                supabase=FakeSupabase(fail=("outage",)),
            )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "an upstream failure must reach Sentry — the old handler bound no exception and "
        "logged at INFO with no type, message or stack"
    )


@pytest.mark.asyncio
async def test_a_denied_ip_never_reaches_the_per_email_limiter():
    """The short-circuit. Without it the protected pool is still evictable.

    `is_allowed` inserts its key before deciding, so evaluating the email limiter after the IP
    limiter already refused created a `login:email:<attacker-chosen>` entry per rejected
    request. ~20k cheap POSTs from ONE address — all 429'd, none reaching Supabase — filled the
    pool and evicted the victim's bucket, handing back a fresh 10-guess window. That defeated
    the whole point of the separate credential pool.

    Here: burn the per-IP budget (10/60s), then send 50 more with distinct emails. None of
    those emails may appear in the pool.
    """
    from app.core.security import rate_limiter

    for i in range(10):
        # Consume the IP budget directly; the handler path is exercised in the loop below.
        rate_limiter.is_allowed(
            "login:ip:203.0.113.77", max_requests=10, window_seconds=60, protected=True
        )

    before = len(rate_limiter._protected)
    for i in range(50):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.sign_in(
                SignInRequest(email=f"flood{i}@example.com", password=_GOOD_PASSWORD),
                _FakeRequest("203.0.113.77"), FakeSupabase(),
            )
        assert ei.value.status_code == 429

    leaked = [k for k in rate_limiter._protected if k.startswith("login:email:flood")]
    assert not leaked, f"{len(leaked)} email keys inserted by requests the IP limiter refused"
    assert len(rate_limiter._protected) == before


@pytest.mark.asyncio
async def test_change_password_reports_a_missing_account_on_the_error_contract():
    """Was a bare-string 404. Now AUTH_ACCOUNT_NOT_FOUND, matching what /auth/refresh already
    returns for the identical condition.

    The string body mattered: iOS cannot decode it as `APIErrorResponse`, so it surfaced as a
    generic `.serverError`/`.unknown` — and `.serverError` used to be auto-retried twice, so a
    single tap burned three of the five per-user attempts without a password ever being checked.
    """
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="x", new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=FakeSupabase(row={"id": _USER_ID}),
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == "AUTH_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_change_password_lookup_blip_is_retryable_not_a_500():
    """A transport fault on the email lookup must be distinguishable from "your account is
    gone", and must not present as an undecodable 500 that the client then retries."""
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="x", new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=FakeSupabase(fail=("lookup",)),
        )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"


# ── 5. Session eviction on refresh ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_is_rejected_for_a_token_issued_before_the_password_change():
    """The check that actually caps a thief's window: without it a stolen refresh token
    keeps minting access tokens for 7 days after the victim resets."""
    stale = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    sb = FakeSupabase(row={"id": _USER_ID, "password_changed_at": future})
    from app.schemas.auth import RefreshTokenRequest

    with pytest.raises(HTTPException) as ei:
        await auth_ep.refresh_token(RefreshTokenRequest(refresh_token=stale), _FakeRequest(), sb)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_still_works_when_the_password_predates_the_token():
    fresh = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    sb = FakeSupabase(row={"id": _USER_ID, "password_changed_at": past})
    from app.schemas.auth import RefreshTokenRequest

    out = await auth_ep.refresh_token(RefreshTokenRequest(refresh_token=fresh), _FakeRequest(), sb)
    assert out.access_token and out.refresh_token


def test_staleness_check_fails_CLOSED_on_a_db_error():
    """REWRITTEN DELIBERATELY (SEC-4): was `..._fails_open_on_a_db_error`, asserting False
    ("allowing the token"). Its only callers MINT tokens, so "could not check" must not mean
    "fresh". It now raises; the caller answers 503."""
    payload = {"iat": datetime.now(timezone.utc).timestamp()}
    with pytest.raises(auth_ep._IdentityStoreUnavailable):
        auth_ep.is_token_stale_after_password_change(
            payload, _USER_ID, FakeSupabase(fail=("lookup",))
        )


def test_staleness_check_reads_a_missing_row_as_not_stale():
    """No row is EXISTENCE's question (`_app_user_row_exists`, asked first), not an outage —
    `limit(1)` returns an empty list where `single()` used to raise."""
    payload = {"iat": datetime.now(timezone.utc).timestamp()}
    assert auth_ep.is_token_stale_after_password_change(
        payload, _USER_ID, FakeSupabase(row={})
    ) is False


@pytest.mark.parametrize("bad_stamp", ["not-a-date", "2026-13-45T99:99:99"])
def test_staleness_check_fails_closed_on_an_unreadable_stamp(bad_stamp):
    payload = {"iat": datetime.now(timezone.utc).timestamp()}
    with pytest.raises(auth_ep._IdentityStoreUnavailable):
        auth_ep.is_token_stale_after_password_change(
            payload, _USER_ID, FakeSupabase(row={"id": _USER_ID, "password_changed_at": bad_stamp})
        )


def test_staleness_check_ignores_tokens_without_iat():
    assert auth_ep.is_token_stale_after_password_change(
        {}, _USER_ID, FakeSupabase(row={"id": _USER_ID, "password_changed_at": "2030-01-01T00:00:00Z"})
    ) is False


# ── 6. Password policy ────────────────────────────────────────────────────────

def test_short_passwords_are_rejected():
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password="a" * (PASSWORD_MIN_LENGTH - 1))


def test_absurdly_long_passwords_are_rejected():
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password="a" * 500)


def test_padded_passwords_are_rejected():
    """A leading/trailing space is almost always a paste accident, and the user will not
    be able to reproduce it at sign-in."""
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password="  spaced out  ")


# ── 6b. The NEW-password rule is GoTrue's own, exactly (C1, PW-1) ─────────────
#
# reset- and set-password spend the single-use code in `verify_otp` BEFORE the password write,
# so a password we pass and GoTrue refuses burns the code (three recovery emails per 15 minutes). The
# rule used to be a deliberate SUPERSET ("symbol" = any non-alphanumeric, "uppercase" = any
# `isupper()`); now it is GoTrue's ASCII sets and 72-byte cap, refused here before any call.

#: Every schema that SETS a password, built around a candidate new password.
_SETTERS = {
    "sign-up": lambda pw: SignUpRequest(email=_EMAIL, password=pw, display_name="N"),
    "reset": lambda pw: ResetPasswordRequest(email=_EMAIL, code="12345678", new_password=pw),
    "change": lambda pw: ChangePasswordRequest(current_password="old", new_password=pw),
    "set": lambda pw: SetPasswordRequest(code="12345678", new_password=pw),
}


def test_the_symbol_set_is_gotrues_32_characters_which_is_ascii_punctuation():
    import string

    assert len(PASSWORD_SYMBOLS) == 32 and len(set(PASSWORD_SYMBOLS)) == 32
    assert set(PASSWORD_SYMBOLS) == set(string.punctuation)
    assert "\\" in PASSWORD_SYMBOLS and " " not in PASSWORD_SYMBOLS


@pytest.mark.parametrize("symbol", list(PASSWORD_SYMBOLS))
def test_every_gotrue_symbol_counts(symbol):
    pw = f"Abcdefg1{symbol}"
    assert _validate_password_strength(pw) == pw


@pytest.mark.parametrize("setter", sorted(_SETTERS))
@pytest.mark.parametrize("password, named", [
    ("Aa1 bbbbbbbb", "a symbol"),          # GoTrue refuses this as weak (verified live)
    ("Blue Sky 2025", "a symbol"),
    ("Abcdefg1€", "a symbol"),
    ("Abcdefg1§", "a symbol"),
    ("Abcdefg1 x", "a symbol"),       # a no-break space inside
    ("Élan2025!", "an uppercase letter"),  # É is not A–Z
    ("Ａbcdefg1!", "an uppercase letter"), # full-width Ａ is not A–Z
    ("ABCDEFé1!", "a lowercase letter"),   # é is lowercase to Python, not a–z to GoTrue
    ("ABCDEFGß1!", "a lowercase letter"),  # ß likewise
    ("Abcdefgh!٣", "a number"),            # Arabic-Indic three is not 0–9
    ("Abcdefgh!²", "a number"),            # a superscript two is not 0–9
])
def test_a_character_outside_gotrues_sets_counts_toward_no_class(setter, password, named):
    with pytest.raises(ValueError) as ei:
        _SETTERS[setter](password)
    msg = str(ei.value)
    assert named in msg, msg
    # The note says WHY a character that looks like it counts does not.
    assert "don't count" in msg, msg


@pytest.mark.parametrize("password", [
    "Correct horse battery 1!",     # spaces INSIDE are allowed; they just count for nothing
    "Élan Vital 2025!",             # non-ASCII is allowed when the four ASCII classes are present
    "Aa1!" + "😀" * 17,             # exactly 72 bytes
    "Aa1!" + "x" * 68,              # 72 ASCII characters
])
def test_a_compliant_password_with_extra_characters_is_accepted(password):
    for build in _SETTERS.values():
        build(password)


@pytest.mark.parametrize("password, n_bytes", [
    ("Aa1!" + "x" * 69, 73),                 # 73 ASCII characters
    ("Aa1!" + "😀" * 17 + "x", 73),          # 22 characters, 73 bytes
    ("Aa1!" + "é" * 35, 74),                 # 39 characters, 74 bytes
    ("Aa1!" + "x" * 5000, 5004),             # never encoded: the character count refuses first
])
@pytest.mark.parametrize("setter", sorted(_SETTERS))
def test_over_72_bytes_is_refused_with_the_bytes_message(setter, password, n_bytes):
    """GoTrue refuses > 72 BYTES with `validation_failed` (verified live). Counted in bytes,
    because an accented letter is two and an emoji four — a 22-character password can be too
    long, and the message says so."""
    assert len(password.encode("utf-8")) == n_bytes
    with pytest.raises(ValueError) as ei:
        _SETTERS[setter](password)
    assert PASSWORD_TOO_LONG_MESSAGE in str(ei.value)
    assert PASSWORD_MAX_BYTES == 72


def test_every_missing_class_is_named_in_one_message_with_its_ascii_range():
    with pytest.raises(ValueError) as ei:
        _validate_password_strength("        x")   # 9 characters, but padded — checked first
    assert "space" in str(ei.value)
    with pytest.raises(ValueError) as ei:
        _validate_password_strength("abcdefgh")
    msg = str(ei.value)
    for part in ("an uppercase letter (A–Z)", "a number (0–9)", "a symbol such as ! @ # $"):
        assert part in msg, msg
    assert "a lowercase letter" not in msg
    assert "don't count" not in msg, "no uncounted character, so no note"


def test_the_order_is_short_then_long_then_padded_then_classes():
    with pytest.raises(ValueError, match="at least 8"):
        _validate_password_strength("é")
    with pytest.raises(ValueError, match="too long"):
        _validate_password_strength(" " + "x" * 80)   # too long wins over padded
    with pytest.raises(ValueError, match="start or end with a space"):
        _validate_password_strength(" Abcdefg1!")


@pytest.mark.parametrize("bad", [None, 12345678, b"Abcdefg1!"])
def test_a_non_string_never_crashes_the_validator(bad):
    with pytest.raises(ValueError):
        _validate_password_strength(bad)


def test_a_lone_surrogate_is_measured_not_crashed_on():
    """JSON can carry "\\ud800"; `str.encode("utf-8")` would raise UnicodeEncodeError mid-rule."""
    with pytest.raises(ValueError, match="symbol"):
        _validate_password_strength("Abcdefg1\ud800")


def test_sign_in_and_confirm_email_are_not_subject_to_the_rule():
    """Existing passwords predate the rule; confirm-email's password may BE the existing one (it
    is checked only when about to be written)."""
    assert SignInRequest(email=_EMAIL, password="a b").password == "a b"
    assert ConfirmEmailRequest(email=_EMAIL, code="12345678", password="é" * 50).password == "é" * 50


@pytest.mark.asyncio
async def test_change_password_maps_gotrues_too_long_refusal_to_password_rejected():
    """Backstop for a provider rule that drifts from ours: GoTrue's `validation_failed` on the
    password is the user's to fix (AUTH_PASSWORD_REJECTED, 400, the bytes copy), never the
    retryable 503 "Please try again" it used to fall to."""
    sb = FakeSupabase(fail=("too_long",))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="old-password", new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=sb,
        )
    assert ei.value.status_code == 400
    assert ei.value.detail["error_code"] == "AUTH_PASSWORD_REJECTED"
    assert ei.value.detail["user_message"] == PASSWORD_TOO_LONG_MESSAGE
    assert ("update", "users", ["password_changed_at"]) not in sb.log


def test_code_accepts_human_formatting_but_not_letters():
    assert ResetPasswordRequest(
        email=_EMAIL, code="123 456", new_password=_GOOD_PASSWORD
    ).code == "123456"
    assert ResetPasswordRequest(
        email=_EMAIL, code="123-456", new_password=_GOOD_PASSWORD
    ).code == "123456"
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="abc123", new_password=_GOOD_PASSWORD)


# ── 7. The code is 8 digits in production, and the schema normalises it ──────

@pytest.mark.asyncio
async def test_reset_accepts_the_live_8_digit_code_and_sends_digits_only():
    """The live project sends 8-digit codes (98180778 in the reported email). Spaces and dashes
    a user pastes are stripped before the code goes upstream."""
    sb = FakeSupabase()
    await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="9818-0778", new_password=_GOOD_PASSWORD),
        _FakeRequest(), sb,
    )
    otp = [e for e in sb.log if e[0] == "verify_otp"][0]
    assert otp[2] == "recovery" and otp[3] == "98180778"


@pytest.mark.parametrize("raw, expected", [
    ("12345678", "12345678"),
    (" 1234 5678 ", "12345678"),
    ("1234–5678", "12345678"),          # an en dash from a mail client's formatting
    ("١٢٣٤٥٦٧٨", "12345678"),  # Arabic-Indic digits
    ("１２３４５６", "123456"),               # full-width digits
])
def test_the_code_is_normalised_to_ascii_digits(raw, expected):
    assert ResetPasswordRequest(email=_EMAIL, code=raw, new_password=_GOOD_PASSWORD).code == expected


@pytest.mark.parametrize("raw", [
    "1-2-3-",            # 6 characters but only 3 digits: the old raw-length rule let it through
    "12345",             # too short
    "1234567890123",     # 13 digits
    "abcdefgh",
    "²²²²²²",  # superscripts are "digits" to isdigit(), not to GoTrue
    "1234.5678",
    "",
    "1" * 40,            # raw length is bounded too
])
def test_malformed_codes_are_refused_before_any_upstream_call(raw):
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code=raw, new_password=_GOOD_PASSWORD)


# ── 8. verify_otp failures are classified (AUTH-BE-05) ────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["otp", "otp_expired"])
async def test_a_refused_code_is_a_typed_400_logged_at_info(fail, caplog):
    """Both the untyped fake and the REAL gotrue `otp_expired` shape."""
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.reset_password(
                ResetPasswordRequest(email=_EMAIL, code="12345678", new_password=_GOOD_PASSWORD),
                _FakeRequest(), FakeSupabase(fail=(fail,)),
            )
    assert ei.value.status_code == 400
    assert ei.value.detail["error_code"] == "AUTH_CODE_INVALID"
    assert ei.value.detail["action"] == "fix_input"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "a typo is not an incident"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail, marker", [("otp_outage", None), ("otp_429", "GOTRUE_RATE_LIMITED")])
async def test_an_upstream_failure_is_never_told_to_the_user_as_a_bad_code(fail, marker, caplog):
    """The old handler said "That code is invalid or has expired" for a GoTrue outage or 429 —
    to a user holding a GOOD code, who then burned the 3/h reset budget on a new one."""
    sb = FakeSupabase(fail=(fail,))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.reset_password(
                ResetPasswordRequest(email=_EMAIL, code="12345678", new_password=_GOOD_PASSWORD),
                _FakeRequest(), sb,
            )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an outage must reach Sentry"
    if marker:
        assert any(marker in m for m in errors)
    assert not any(_EMAIL in m for m in errors), "the address must never be logged"


@pytest.mark.asyncio
async def test_an_unknown_email_and_a_wrong_code_answer_identically():
    """GoTrue answers both with otp_expired; the body must not become an account oracle."""
    bodies = []
    for email, ip in ((_EMAIL, "198.51.100.30"), ("nobody@example.com", "198.51.100.31")):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.reset_password(
                ResetPasswordRequest(email=email, code="12345678", new_password=_GOOD_PASSWORD),
                _FakeRequest(ip), FakeSupabase(fail=("otp_expired",)),
            )
        bodies.append((ei.value.status_code, ei.value.detail))
    assert bodies[0] == bodies[1]


@pytest.mark.asyncio
async def test_a_verified_answer_with_no_user_is_a_typed_code_used(caplog):
    """The verify did not raise, so the code is consumed; the anomaly is ours (ERROR)."""
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.reset_password(
                ResetPasswordRequest(email=_EMAIL, code="12345678", new_password=_GOOD_PASSWORD),
                _FakeRequest(), FakeSupabase(otp_user_id=None),
            )
    _assert_code_used(ei.value)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── 9. A refused NEW password after the code is spent (AUTH-BE-03, SB-5) ──────

@pytest.mark.asyncio
async def test_reset_reports_a_provider_refused_password_and_asks_for_a_new_code(caplog):
    sb = FakeSupabase(fail=("weak",))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.reset_password(
                ResetPasswordRequest(email=_EMAIL, code="12345678", new_password=_GOOD_PASSWORD),
                _FakeRequest(), sb,
            )
    detail = ei.value.detail
    assert ei.value.status_code == 400
    assert detail["error_code"] == "AUTH_PASSWORD_REJECTED"
    assert detail["user_message"] == (
        "That password can't be used. Send a new code, then choose a different password."
    ), "the code in the form is already spent; the words match the screens' button"
    assert detail["details"] == {"reasons": "pwned"}
    assert ("update", "users", ["password_changed_at"]) not in sb.log, "nothing changed — no stamp"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── 10. _mark_password_changed retries once (SEC-4) ───────────────────────────

def test_the_stamp_is_retried_once(monkeypatch, caplog):
    monkeypatch.setattr(auth_ep, "_STAMP_RETRY_DELAY_SECONDS", 0)
    sb = FakeSupabase(fail=("stamp_once",))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        auth_ep._mark_password_changed(sb, _USER_ID)
    assert ("update", "users", ["password_changed_at"]) in sb.log
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_stamp_that_fails_twice_is_an_error_not_a_raise(monkeypatch, caplog):
    monkeypatch.setattr(auth_ep, "_STAMP_RETRY_DELAY_SECONDS", 0)
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        auth_ep._mark_password_changed(FakeSupabase(fail=("stamp",)), _USER_ID)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR and "FAILED twice" in r.getMessage()]


# ── 11. Refresh: expired vs garbage vs wrong type (AUTH-BE-14) ─────────────────

@pytest.mark.asyncio
async def test_an_expired_refresh_token_is_session_expired_at_info(caplog):
    """Every lapsed 7-day refresh used to be a logger.error — a Sentry event per user per week —
    answered "invalid" when the truth is "expired"."""
    expired = create_refresh_token({"sub": _USER_ID, "email": _EMAIL}, expires_delta=timedelta(seconds=-30))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.refresh_token(
                RefreshTokenRequest(refresh_token=expired), _FakeRequest(), FakeSupabase(),
            )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == "AUTH_SESSION_EXPIRED"
    assert "password" not in ei.value.detail["user_message"].lower(), "nothing about a password changed"
    assert not [r for r in caplog.records
                if r.name == "app.api.v1.endpoints.auth" and r.levelno >= logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["not.a.jwt", "", "x" * 200])
async def test_a_garbage_refresh_token_is_token_invalid_without_an_error(token, caplog):
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.refresh_token(
                RefreshTokenRequest(refresh_token=token), _FakeRequest(), FakeSupabase(),
            )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == "AUTH_TOKEN_INVALID"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_an_access_token_is_not_a_refresh_token():
    access = create_access_token({"sub": _USER_ID, "email": _EMAIL})
    with pytest.raises(HTTPException) as ei:
        await auth_ep.refresh_token(
            RefreshTokenRequest(refresh_token=access), _FakeRequest(), FakeSupabase(),
        )
    assert ei.value.detail["error_code"] == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_a_refresh_token_with_no_subject_never_mints():
    """It used to skip both account checks (`if user_id and …`) and mint tokens for sub=None."""
    nosub = create_refresh_token({"email": _EMAIL})
    with pytest.raises(HTTPException) as ei:
        await auth_ep.refresh_token(
            RefreshTokenRequest(refresh_token=nosub), _FakeRequest(), FakeSupabase(),
        )
    assert ei.value.detail["error_code"] == "AUTH_TOKEN_INVALID"


# ── 12. Retry-After is the window of the bucket that denied (IOS-AUTH-04) ─────

async def _exhaust(call, n):
    for _ in range(n):
        try:
            await call()
        except HTTPException as e:
            if e.status_code == 429:
                return e
    with pytest.raises(HTTPException) as ei:
        await call()
    return ei.value


@pytest.mark.asyncio
async def test_login_per_email_denial_says_900_not_60():
    """10 wrong passwords in 15 minutes: the old "60" told the user to wait a minute, after
    which the CORRECT password was refused again."""
    i = {"n": 0}

    async def call():
        i["n"] += 1
        return await auth_ep.sign_in(
            SignInRequest(email=_EMAIL, password="Wrong-pass-1!"),
            _FakeRequest(f"198.51.100.{i['n']}"), FakeSupabase(fail=("signin",)),
        )
    denial = await _exhaust(call, 10)
    assert denial.status_code == 429
    assert denial.headers["Retry-After"] == "900"


@pytest.mark.asyncio
async def test_login_per_ip_denial_still_says_60():
    async def call():
        return await auth_ep.sign_in(
            SignInRequest(email=f"u{datetime.now().timestamp()}@example.com", password="Wrong-pass-1!"),
            _FakeRequest("203.0.113.88"), FakeSupabase(fail=("signin",)),
        )
    denial = await _exhaust(call, 10)
    assert denial.headers["Retry-After"] == "60"


def test_the_limiter_helper_takes_no_route_wide_retry_after():
    """Source pin: a single route-wide Retry-After is exactly the bug; the helper must derive
    it from the denying check."""
    import inspect

    sig = inspect.signature(auth_ep._enforce_credential_limits)
    assert "retry_after" not in sig.parameters


@pytest.mark.asyncio
async def test_one_address_gets_at_most_six_reset_emails_an_hour():
    """2026-11-26 review: the 15-minute window (3) would allow 12 reset emails an hour into one
    inbox; the hourly ceiling holds it to 6. The 15-minute address bucket is cleared between
    rounds here to stand for the next quarter-hour."""
    from app.core.security import rate_limiter as rl
    allowed = 0
    for q in range(3):
        for i in range(3):
            try:
                await auth_ep.forgot_password(
                    ForgotPasswordRequest(email=_EMAIL), _FakeRequest(f"203.0.113.{40 + q * 5 + i}"),
                    FakeSupabase(),
                )
                allowed += 1
            except HTTPException as e:
                assert e.status_code == 429
        rl._protected.pop(f"forgot:email:{_EMAIL}", None)
    assert allowed == 6, allowed
    with pytest.raises(HTTPException) as ei:
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=_EMAIL), _FakeRequest("203.0.113.99"), FakeSupabase(),
        )
    assert ei.value.status_code == 429 and ei.value.headers["Retry-After"] == "3600"
