"""Email-confirmation gate (policy option A) and social sign-in.

Two things pinned here:

1. **Confirmation is mandatory for password signups.** `/register` used to mint app JWTs
   without ever reading `email_confirmed_at`, so anyone could register an address they did
   not own and get a working account on it. It now returns `confirmation_required` and no
   tokens. `/login` reports the unconfirmed state as its OWN error code rather than a
   generic 401 — telling someone "invalid credentials" when their password was correct
   sends them off to reset a password that is fine.

2. **Social sign-in is exempt from that gate, deliberately.** Apple and Google supply an
   already-verified address; requiring a second confirmation of an address the provider has
   already proven would be pointless friction. The exemption is asserted here so it can't be
   "tidied up" into consistency later.

3. **A signup can actually be confirmed** (`/auth/confirm-email`, 2026-11-26). The live
   Confirm-signup email carries an 8-digit code and, until this route, nothing could redeem it,
   so every new email/password account was stuck at EMAIL_NOT_CONFIRMED while this suite was
   green (TG-1). Pinned below: the OTP type is "signup" and never "email"/"recovery"; a refused
   code is a typed 400 with no tokens; both limiters run before GoTrue; an outage is a 503; a
   DIFFERENT stored password is replaced by the mailbox owner's (CC-03, SEC-6) and stamped; no
   device proof is minted.

No network / Supabase — fakes throughout, and the shared rate limiter is reset per test. GoTrue
failures are built by gotrue's own `handle_exception`, the path a real answer takes.
"""

from __future__ import annotations

import json
import logging
import warnings

import httpx
import pytest
from fastapi import HTTPException

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # gotrue announces its own rename
    from gotrue.helpers import handle_exception

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import auth as auth_ep
from app.core.security import rate_limiter
from app.schemas.auth import (
    ConfirmEmailRequest,
    OAuthSignInRequest,
    ResendConfirmationRequest,
    SessionExchangeRequest,
    SignInRequest,
    SignUpRequest,
)

_USER_ID = "11112222-3333-4444-8555-666677778888"
_EMAIL = "new.user@example.com"
_PASSWORD = "A-long-enough-password1!"
# Compliant with `_validate_password_strength` (8+, upper, lower, digit, symbol),
# which mirrors the Supabase Email-provider setting. A weak literal here fails at
# SCHEMA construction, so every test in the file dies before reaching what it asserts.



@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    # `clear()` covers the credential pool too; `_requests` alone does not.
    rate_limiter.clear()
    yield


class _FakeRequest:
    def __init__(self, ip="203.0.113.44", headers=None):
        self.client = type("C", (), {"host": ip})()
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


def _gotrue_error(status: int, body: dict | None = None, *, text: str | None = None) -> Exception:
    """The exception gotrue itself raises for this GoTrue answer (its own `handle_exception`)."""
    req = httpx.Request("POST", "https://example.supabase.co/auth/v1/x")
    if text is not None:
        resp = httpx.Response(status, text=text, request=req)
    else:
        resp = httpx.Response(
            status, json=body or {}, request=req,
            headers={"X-Supabase-Api-Version": "2024-01-01"},
        )
    return handle_exception(httpx.HTTPStatusError("http error", request=req, response=resp))


def _gotrue_transport_error() -> Exception:
    return handle_exception(httpx.ConnectError("connection refused"))


# Failure modes by flag, built through the real SDK mapper.
_SIGNUP_ERRORS = {
    "signup_email_rate": lambda: _gotrue_error(
        429, {"code": "over_email_send_rate_limit", "msg": "email rate limit exceeded"}),
    "signup_ip_rate": lambda: _gotrue_error(
        429, {"code": "over_request_rate_limit", "msg": "Request rate limit reached"}),
    "signup_smtp": lambda: _gotrue_error(
        500, {"code": "unexpected_failure", "msg": "Error sending confirmation email"}),
    "signup_down": _gotrue_transport_error,
    "signup_bad_gateway": lambda: _gotrue_error(502, {"msg": "bad gateway"}),
    "signup_edge_html": lambda: _gotrue_error(520, text="<html>origin error</html>"),
    "signup_invalid_email": lambda: _gotrue_error(
        400, {"code": "email_address_invalid", "msg": "Email address is invalid"}),
    "signup_disabled": lambda: _gotrue_error(
        422, {"code": "signup_disabled", "msg": "Signups not allowed for this instance"}),
    "signup_weak": lambda: _gotrue_error(422, {
        "code": "weak_password", "msg": "Password is known to be weak",
        "weak_password": {"reasons": ["pwned"]}}),
    # GoTrue's 72-byte cap: `validation_failed`, NOT weak_password (verified on the live stack).
    "signup_too_long": lambda: _gotrue_error(
        400, {"code": "validation_failed", "msg": "Password cannot be longer than 72 characters"}),
    # The same code about ANOTHER field must not be read as a password refusal.
    "signup_validation_email": lambda: _gotrue_error(
        400, {"code": "validation_failed", "msg": "Unable to validate email address: invalid format"}),
}


class _FakeUser:
    def __init__(self, confirmed: bool, uid=_USER_ID, email=_EMAIL):
        self.id = uid
        self.email = email
        self.email_confirmed_at = "2026-07-30T00:00:00Z" if confirmed else None


class _FakeAdmin:
    def __init__(self, log, fail):
        self._log, self._fail = log, fail

    def update_user_by_id(self, uid, attrs):
        self._log.append(("admin_attempt", uid))
        if "admin_weak" in self._fail:
            raise _SIGNUP_ERRORS["signup_weak"]()
        if "admin_too_long" in self._fail:
            raise _SIGNUP_ERRORS["signup_too_long"]()
        if "admin_down" in self._fail:
            raise _gotrue_error(403, {"code": "not_admin", "msg": "User not allowed"})
        if "admin_outage" in self._fail:
            raise _gotrue_transport_error()
        self._log.append(("admin.update_user_by_id", uid, dict(attrs)))


class _FakeAuth:
    def __init__(self, log, user, fail):
        self._log, self._user, self._fail = log, user, fail
        self.admin = _FakeAdmin(log, fail)

    def sign_up(self, payload):
        if "signup" in self._fail:
            raise RuntimeError("signup failed")
        for flag, build in _SIGNUP_ERRORS.items():
            if flag in self._fail:
                raise build()
        self._log.append(("sign_up", payload["email"]))
        return type("R", (), {"user": self._user})()

    def verify_otp(self, params):
        self._log.append(("verify_otp", params.get("email"), params.get("type"), params.get("token")))
        if "otp_expired" in self._fail:
            raise _gotrue_error(403, {"code": "otp_expired", "msg": "Token has expired or is invalid"})
        if "otp_outage" in self._fail:
            raise _gotrue_transport_error()
        if "otp_5xx" in self._fail:
            raise _gotrue_error(500, {"code": "unexpected_failure", "msg": "Database error"})
        if "otp_429" in self._fail:
            raise _gotrue_error(429, {"code": "over_request_rate_limit", "msg": "rate limited"})
        if "otp_nouser" in self._fail:
            return type("R", (), {"user": None})()
        if "otp_wrong_email" in self._fail:
            return type("R", (), {"user": _FakeUser(True, email="someone.else@example.com")})()
        if "otp_unconfirmed" in self._fail:
            return type("R", (), {"user": _FakeUser(False)})()
        return type("R", (), {"user": _FakeUser(True)})()

    def sign_in_with_password(self, creds):
        self._log.append(("sign_in_attempt", creds["email"]))
        if "not_confirmed" in self._fail:
            raise RuntimeError("Email not confirmed")
        if "signin" in self._fail:
            raise RuntimeError("bad credentials")
        if "signin_typed" in self._fail:
            raise _gotrue_error(400, {"code": "invalid_credentials", "msg": "Invalid login credentials"})
        if "signin_429" in self._fail:
            raise _gotrue_error(429, {"code": "over_request_rate_limit", "msg": "rate limited"})
        if "signin_transport" in self._fail:
            raise _gotrue_transport_error()
        if "signin_5xx" in self._fail:
            raise _gotrue_error(500, {"code": "unexpected_failure", "msg": "Database error"})
        if "signin_edge_html" in self._fail:
            raise _gotrue_error(520, text="<html>origin error</html>")
        # POLICY answers about this account — never a reason to set a password and mint.
        if "signin_typed_unconfirmed" in self._fail:
            raise _gotrue_error(400, {"code": "email_not_confirmed", "msg": "Email not confirmed"})
        if "signin_provider_disabled" in self._fail:
            raise _gotrue_error(400, {"code": "email_provider_disabled", "msg": "Email logins are disabled"})
        if "signin_banned" in self._fail:
            raise _gotrue_error(400, {"code": "user_banned", "msg": "User is banned"})
        if "signin_other_user" in self._fail:
            return type("R", (), {"user": _FakeUser(True, uid="99999999-0000-4000-8000-000000000000")})()
        # A failure that is OURS, not the caller's — deliberately worded so it does NOT match
        # `_is_rejected_credential`, which is the only thing separating a wrong password from
        # an identity-provider outage.
        if "outage" in self._fail:
            raise RuntimeError("upstream gateway timeout contacting the identity provider")
        self._log.append(("sign_in_with_password", creds["email"]))
        return type("R", (), {"user": self._user})()

    def resend(self, payload):
        if "resend" in self._fail:
            raise RuntimeError("resend failed")
        self._log.append(("resend", payload.get("type"), payload.get("email")))

    def sign_in_with_id_token(self, creds):
        if "oauth" in self._fail:
            raise RuntimeError("bad id token")
        self._log.append(("sign_in_with_id_token", creds.get("provider"), creds.get("nonce")))
        return type("R", (), {"user": self._user})()


class _FakeQuery:
    def __init__(self, log, table, row, fail):
        self._log, self._table, self._row, self._fail = log, table, row, fail
        self._update = None
        self._shape = "single"   # PostgREST: single() -> object, limit()/plain -> list

    def select(self, *_a, **_k):
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
        # `sign_in` reads public.users with limit(1) rather than single(): single() RAISES
        # (PostgREST 406 / PGRST116) on zero rows instead of returning empty, which made the
        # missing-row fallback dead code and 500'd a correct password.
        #
        # The SHAPE differs too, and modelling that matters: PostgREST returns a LIST for a
        # plain/limited select and a bare OBJECT for single(). A fake that always returned the
        # object would let `data[0]` pass here and KeyError in production.
        self._shape = "list"
        return self

    def execute(self):
        if self._update is not None:
            self._log.append(("update", self._table, sorted(self._update)))
            return type("R", (), {"data": [self._update]})()
        if "lookup" in self._fail:
            raise RuntimeError("lookup failed")
        data = self._row if self._shape == "single" else ([self._row] if self._row else [])
        return type("R", (), {"data": data})()


class FakeSupabase:
    def __init__(self, confirmed=True, fail=(), row=None):
        self.log: list[tuple] = []
        self._fail = set(fail)
        self._row = row if row is not None else {"id": _USER_ID, "email": _EMAIL}
        self.auth = _FakeAuth(self.log, _FakeUser(confirmed), self._fail)

    def table(self, name):
        return _FakeQuery(self.log, name, self._row, self._fail)


def _async_returning(value, *, on_call=None):
    """Async stub for `verify_supabase_token`, which is now a coroutine.

    It became `async` when Supabase JWT verification gained the JWKS path: an ES256 token needs
    the project's public key fetched over HTTP, and doing that synchronously would block the
    event loop inside an auth dependency. A plain `lambda` stub here silently produces a
    coroutine-less value that `await` then rejects.
    """
    async def _stub(_token):
        if on_call is not None:
            on_call()
        return value
    return _stub


def _body(response) -> dict:
    """Decode a JSONResponse body."""
    return json.loads(response.body)


# ── 1. Register requires confirmation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_issues_no_tokens_when_unconfirmed():
    out = await auth_ep.sign_up(
        SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="New User"),
        _FakeRequest(), FakeSupabase(confirmed=False),
    )
    assert out.confirmation_required is True
    assert out.access_token is None, "an unconfirmed account must not receive a session"
    assert out.refresh_token is None
    assert out.user_id is None, "don't hand out the user id before confirmation either"


@pytest.mark.asyncio
async def test_register_still_creates_the_account_when_unconfirmed():
    """The account must exist so the confirmation link has something to confirm."""
    sb = FakeSupabase(confirmed=False)
    await auth_ep.sign_up(
        SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="New User"),
        _FakeRequest(), sb,
    )
    assert [e for e in sb.log if e[0] == "sign_up"]


@pytest.mark.asyncio
async def test_register_returns_tokens_when_confirmation_is_disabled_project_side():
    """A project with confirmation OFF really does have a usable session — say so honestly
    via `confirmation_required = False` rather than pretending otherwise."""
    out = await auth_ep.sign_up(
        SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="New User"),
        _FakeRequest(), FakeSupabase(confirmed=True),
    )
    assert out.confirmation_required is False
    assert out.access_token and out.refresh_token and out.user_id == _USER_ID


# ── 2. Login distinguishes unconfirmed from wrong-password ────────────────────

@pytest.mark.asyncio
async def test_login_unconfirmed_returns_its_own_error_code():
    resp = await auth_ep.sign_in(
        SignInRequest(email=_EMAIL, password=_PASSWORD),
        _FakeRequest(), FakeSupabase(confirmed=False),
    )
    body = _body(resp)
    assert resp.status_code == 403
    assert body["error_code"] == ErrorCode.EMAIL_NOT_CONFIRMED.value
    assert body["action"] == "confirm_email"
    assert "confirm" in body["user_message"].lower()


@pytest.mark.asyncio
async def test_login_maps_supabases_own_not_confirmed_error():
    """Supabase normally rejects the sign-in itself; that path must reach the same code
    rather than falling through to a generic 401."""
    resp = await auth_ep.sign_in(
        SignInRequest(email=_EMAIL, password=_PASSWORD),
        _FakeRequest(), FakeSupabase(fail=("not_confirmed",)),
    )
    assert _body(resp)["error_code"] == ErrorCode.EMAIL_NOT_CONFIRMED.value


@pytest.mark.asyncio
async def test_login_still_401s_on_genuinely_bad_credentials():
    with pytest.raises(HTTPException) as ei:
        await auth_ep.sign_in(
            SignInRequest(email=_EMAIL, password=_PASSWORD),
            _FakeRequest(), FakeSupabase(fail=("signin",)),
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == ErrorCode.AUTH_CREDENTIALS_INVALID.value


@pytest.mark.asyncio
async def test_a_wrong_password_is_not_logged_as_an_application_error(caplog):
    """A typo must not become a Sentry issue.

    Sentry's `LoggingIntegration` promotes an ERROR record to an EVENT, and this path used to
    call `logger.error(..., exc_info=True)` for every rejected password — so ordinary human
    behaviour raised `AuthApiError: Invalid login credentials` in production, burying real
    faults and consuming quota. Observed live in Sentry, one minute after a single test
    sign-in.
    """
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException):
            await auth_ep.sign_in(
                SignInRequest(email=_EMAIL, password=_PASSWORD),
                _FakeRequest(), FakeSupabase(fail=("signin",)),
            )
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "a rejected password logged at ERROR — Sentry will raise an issue for every typo"
    )
    assert any(r.levelno == logging.INFO for r in caplog.records)


@pytest.mark.asyncio
async def test_an_upstream_outage_is_a_retryable_503_not_a_wrong_password(caplog):
    """The inverse: OUR failure must not be laundered into "your password is wrong".

    That told the user to reset a password that was never the problem, and hid the incident
    behind a 401 nobody investigates. AUTH_UNAVAILABLE is retryable and iOS is forbidden from
    clearing a credential on it — and this one SHOULD reach Sentry.
    """
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.sign_in(
                SignInRequest(email=_EMAIL, password=_PASSWORD),
                _FakeRequest(), FakeSupabase(fail=("outage",)),
            )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == ErrorCode.AUTH_UNAVAILABLE.value
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "a genuine upstream failure must still reach Sentry"
    )


@pytest.mark.asyncio
async def test_login_succeeds_normally_when_confirmed():
    out = await auth_ep.sign_in(
        SignInRequest(email=_EMAIL, password=_PASSWORD), _FakeRequest(), FakeSupabase()
    )
    assert out.access_token and out.user_id == _USER_ID


# ── 3. Resend confirmation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resend_uses_the_signup_type():
    sb = FakeSupabase()
    await auth_ep.resend_confirmation(
        ResendConfirmationRequest(email=_EMAIL), _FakeRequest(), sb
    )
    sent = [e for e in sb.log if e[0] == "resend"]
    assert sent and sent[0][1] == "signup"
    assert sent[0][2] == _EMAIL.lower()


@pytest.mark.asyncio
async def test_resend_response_does_not_reveal_account_state():
    ok = await auth_ep.resend_confirmation(
        ResendConfirmationRequest(email=_EMAIL), _FakeRequest("198.51.100.5"), FakeSupabase()
    )
    failed = await auth_ep.resend_confirmation(
        ResendConfirmationRequest(email="nobody@example.com"),
        _FakeRequest("198.51.100.6"), FakeSupabase(fail=("resend",)),
    )
    assert ok.message == failed.message


@pytest.mark.asyncio
async def test_resend_is_rate_limited_per_email():
    for i in range(3):
        await auth_ep.resend_confirmation(
            ResendConfirmationRequest(email=_EMAIL), _FakeRequest(f"198.51.100.{i}"),
            FakeSupabase(),
        )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.resend_confirmation(
            ResendConfirmationRequest(email=_EMAIL), _FakeRequest("198.51.100.90"),
            FakeSupabase(),
        )
    assert ei.value.status_code == 429


# ── 4. Social sign-in ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["apple", "google"])
async def test_oauth_issues_tokens_for_a_verified_provider_token(provider):
    out = await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider=provider, id_token="x" * 40, nonce="n0nce"),
        _FakeRequest(), FakeSupabase(),
    )
    assert out.access_token and out.refresh_token and out.user_id == _USER_ID


@pytest.mark.asyncio
async def test_oauth_is_exempt_from_the_confirmation_gate():
    """THE POINT OF THIS TEST: Apple/Google addresses are already verified, so an
    unconfirmed `email_confirmed_at` from Supabase must NOT block social sign-in the way it
    blocks a password sign-in. If someone later "unifies" the two paths, this fails."""
    out = await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider="apple", id_token="x" * 40),
        _FakeRequest(), FakeSupabase(confirmed=False),
    )
    assert out.access_token, "social sign-in must not be gated on email_confirmed_at"


@pytest.mark.asyncio
async def test_oauth_forwards_the_nonce_when_present():
    """Apple binds a nonce to the token to prevent replay; dropping it silently would
    weaken the flow without any visible symptom."""
    sb = FakeSupabase()
    await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider="apple", id_token="x" * 40, nonce="the-nonce"),
        _FakeRequest(), sb,
    )
    call = [e for e in sb.log if e[0] == "sign_in_with_id_token"][0]
    assert call[2] == "the-nonce"


@pytest.mark.asyncio
async def test_oauth_rejects_an_unverifiable_token():
    with pytest.raises(HTTPException) as ei:
        await auth_ep.oauth_sign_in(
            OAuthSignInRequest(provider="apple", id_token="x" * 40),
            _FakeRequest(), FakeSupabase(fail=("oauth",)),
        )
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_oauth_rejects_an_unknown_provider_at_the_schema():
    with pytest.raises(ValueError):
        OAuthSignInRequest(provider="facebook", id_token="x" * 40)


@pytest.mark.asyncio
async def test_oauth_persists_a_first_time_display_name():
    """Apple returns the name only on the FIRST authorization — miss it and it's gone.

    ⚠️ The row is NEVER null here in production: the signup trigger pre-fills the email's
    local part. This fixture used to model a NULL row, which made the test pass while the
    real path (a relay local part already sitting in the column) returned without writing.
    Hide My Email is the realistic shape."""
    sb = FakeSupabase(row={"id": _USER_ID, "email": "xk3n8d2q@privaterelay.appleid.com",
                           "display_name": "xk3n8d2q"})
    await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider="apple", id_token="x" * 40, display_name="Ada L"),
        _FakeRequest(), sb,
    )
    assert ("update", "users", ["display_name"]) in sb.log


@pytest.mark.asyncio
async def test_oauth_still_persists_when_the_row_is_genuinely_null():
    sb = FakeSupabase(row={"id": _USER_ID, "email": _EMAIL, "display_name": None})
    await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider="apple", id_token="x" * 40, display_name="Ada L"),
        _FakeRequest(), sb,
    )
    assert ("update", "users", ["display_name"]) in sb.log


@pytest.mark.asyncio
async def test_oauth_never_overwrites_an_existing_display_name():
    """The user may have renamed themselves deliberately since signing up."""
    sb = FakeSupabase(row={"id": _USER_ID, "email": _EMAIL, "display_name": "Chosen Name"})
    await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider="apple", id_token="x" * 40, display_name="Apple Name"),
        _FakeRequest(), sb,
    )
    assert not [e for e in sb.log if e[0] == "update"]


@pytest.mark.asyncio
async def test_oauth_succeeds_even_if_the_name_write_fails():
    sb = FakeSupabase(fail=("lookup",))
    out = await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider="apple", id_token="x" * 40, display_name="Ada L"),
        _FakeRequest(), sb,
    )
    assert out.access_token, "a cosmetic name write must never fail a sign-in"


# ── 5. Session exchange (web OAuth, e.g. Google) ──────────────────────────────

@pytest.mark.asyncio
async def test_session_exchange_rejects_an_unverifiable_supabase_token(monkeypatch):
    monkeypatch.setattr(auth_ep, "verify_supabase_token", _async_returning(None))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(),
            FakeSupabase(),
        )
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_session_exchange_issues_tokens_for_a_verified_token(monkeypatch):
    monkeypatch.setattr(
        auth_ep, "verify_supabase_token", _async_returning({"sub": _USER_ID, "email": _EMAIL})
    )
    out = await auth_ep.session_exchange(
        SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(), FakeSupabase()
    )
    assert out.user_id == _USER_ID and out.access_token


@pytest.mark.asyncio
async def test_session_exchange_refuses_when_no_app_user_row_exists(monkeypatch):
    """The DB trigger creates public.users on auth.users insert. Minting tokens for an id
    with no app row would produce a signed-in user that every endpoint 404s on."""
    monkeypatch.setattr(
        auth_ep, "verify_supabase_token", _async_returning({"sub": _USER_ID, "email": _EMAIL})
    )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(),
            FakeSupabase(fail=("lookup",)),
        )
    # 503 AUTH_UNAVAILABLE, not a bare-string 500: iOS's 5xx arm falls back to
    # `.serverError`, whose copy is hardcoded, so a string detail threw away the specific
    # sentence. Asserted on the CODE rather than the status — the identifier is the
    # contract, the number is incidental.
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_session_exchange_never_trusts_the_sub_before_verification(monkeypatch):
    """A forged token must not reach the DB lookup at all."""
    seen = []
    monkeypatch.setattr(
        auth_ep, "verify_supabase_token", _async_returning(None, on_call=lambda: seen.append("verified"))
    )
    sb = FakeSupabase()
    with pytest.raises(HTTPException):
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="forged" * 10), _FakeRequest(), sb
        )
    assert seen == ["verified"]
    assert sb.log == [], "no DB access before the signature check passes"


# ── 6. Confirm a signup with the emailed code (/auth/confirm-email) ───────────

_CODE = "9818-0778"          # the shape of the code in the reported email, as a user pastes it
_CODE_DIGITS = "98180778"


def _confirm(email=_EMAIL, code=_CODE, password=_PASSWORD) -> ConfirmEmailRequest:
    return ConfirmEmailRequest(email=email, code=code, password=password)


def _kinds(sb) -> list[str]:
    return [e[0] for e in sb.log]


@pytest.mark.asyncio
async def test_confirm_email_verifies_a_SIGNUP_code_then_signs_in():
    """(a) The type is "signup" — never "email" (GoTrue's "email" also matches a RECOVERY token,
    turning a reset code into a passwordless sign-in) and never "recovery". The email reaches
    GoTrue lowercased and the code as digits only."""
    sb = FakeSupabase()
    out = await auth_ep.confirm_email(_confirm(email="New.User@Example.com"), _FakeRequest(), sb)

    verify = [e for e in sb.log if e[0] == "verify_otp"]
    assert verify == [("verify_otp", _EMAIL, "signup", _CODE_DIGITS)]
    assert _kinds(sb).index("verify_otp") < _kinds(sb).index("sign_in_with_password")
    assert out.access_token and out.refresh_token and out.user_id == _USER_ID


@pytest.mark.asyncio
async def test_confirm_email_never_mints_a_device_proof():
    """An emailed code proves the mailbox, not a password: auth.md §3a mints the lockout
    exemption only after a VERIFIED CREDENTIAL — on both the match and the replace paths."""
    for fail in ((), ("signin",)):
        rate_limiter.clear()
        out = await auth_ep.confirm_email(_confirm(), _FakeRequest(), FakeSupabase(fail=fail))
        assert out.device_token is None


@pytest.mark.asyncio
async def test_a_matching_password_changes_nothing():
    sb = FakeSupabase()
    await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    assert "admin.update_user_by_id" not in _kinds(sb)
    assert not [e for e in sb.log if e[0] == "update"], "no stamp when no password changed"


def test_confirm_email_source_pins_the_signup_type():
    """Comment-stripped and brace-bound to the handler: the only verify_otp type it may send."""
    import inspect

    src = inspect.getsource(auth_ep.confirm_email)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert '"type": "signup"' in code
    assert '"type": "email"' not in code and '"type": "recovery"' not in code
    assert "device_proof=False" in code


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["otp_expired", "otp_nouser", "otp_wrong_email", "otp_unconfirmed"])
async def test_a_refused_code_is_a_typed_400_with_no_tokens(fail, caplog):
    """(b)(c) A wrong/expired code — and a verify answer that is anomalous (no user, another
    address, still unconfirmed) — never reaches the sign-in, never mints."""
    sb = FakeSupabase(fail=(fail,))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    assert ei.value.status_code == 400
    assert ei.value.detail["error_code"] == "AUTH_CODE_INVALID"
    assert ei.value.detail["action"] == "fix_input"
    assert "sign_in_attempt" not in _kinds(sb) and "admin.update_user_by_id" not in _kinds(sb)
    if fail == "otp_expired":
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "a typo is not an incident"


@pytest.mark.asyncio
async def test_an_unknown_email_and_a_wrong_code_answer_identically():
    """(e) GoTrue answers both with otp_expired; the body must not become an account oracle."""
    bodies = []
    for email, ip in ((_EMAIL, "198.51.100.71"), ("nobody@example.com", "198.51.100.72")):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.confirm_email(
                _confirm(email=email), _FakeRequest(ip), FakeSupabase(fail=("otp_expired",)),
            )
        bodies.append((ei.value.status_code, ei.value.detail))
    assert bodies[0] == bodies[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail, marker", [
    ("otp_outage", None), ("otp_5xx", None), ("otp_429", "GOTRUE_RATE_LIMITED"),
])
async def test_an_upstream_failure_is_a_retryable_503(fail, marker, caplog):
    """(f) Ours, not theirs: the code is probably still good, so the user is told to retry —
    not to burn the resend budget on a new one. ERROR (Sentry), never the email or the code."""
    sb = FakeSupabase(fail=(fail,))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert "sign_in_attempt" not in _kinds(sb)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors
    if marker:
        assert any(marker in m for m in errors)
    for m in (r.getMessage() for r in caplog.records):
        assert _EMAIL not in m and _CODE_DIGITS not in m and _PASSWORD not in m


@pytest.mark.asyncio
async def test_confirm_email_limiter_order_and_per_email_budget():
    """(d) Per-IP FIRST, then per-email, both before GoTrue. Ten attempts per address an hour
    from ANY number of addresses; the eleventh never reaches verify_otp."""
    sb = FakeSupabase(fail=("otp_expired",))
    for i in range(10):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.confirm_email(_confirm(), _FakeRequest(f"198.51.100.{i}"), sb)
        assert ei.value.status_code == 400
    calls_before = len([e for e in sb.log if e[0] == "verify_otp"])
    with pytest.raises(HTTPException) as ei:
        await auth_ep.confirm_email(_confirm(), _FakeRequest("198.51.100.200"), sb)
    assert ei.value.status_code == 429
    assert ei.value.headers["Retry-After"] == "3600"
    assert len([e for e in sb.log if e[0] == "verify_otp"]) == calls_before


@pytest.mark.asyncio
async def test_a_denied_ip_never_inserts_the_per_email_key():
    for i in range(20):
        with pytest.raises(HTTPException):
            await auth_ep.confirm_email(
                _confirm(email=f"u{i}@example.com"), _FakeRequest("203.0.113.99"),
                FakeSupabase(fail=("otp_expired",)),
            )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.confirm_email(
            _confirm(email="victim@example.com"), _FakeRequest("203.0.113.99"), FakeSupabase(),
        )
    assert ei.value.status_code == 429
    assert not [k for k in rate_limiter._protected if k == "confirm:email:victim@example.com"]


@pytest.mark.asyncio
async def test_a_device_proof_does_not_relax_the_confirm_limiter():
    """A proof proves a past PASSWORD for the address, not this mailbox: the per-email bucket
    that bounds code guessing must apply to it like to anyone."""
    from app.core.security import create_device_proof

    proof = create_device_proof(_EMAIL)
    sb = FakeSupabase(fail=("otp_expired",))
    for i in range(10):
        with pytest.raises(HTTPException):
            await auth_ep.confirm_email(
                _confirm(), _FakeRequest(f"198.51.100.{i}", {"X-Device-Token": proof}), sb,
            )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.confirm_email(
            _confirm(), _FakeRequest("198.51.100.250", {"X-Device-Token": proof}), sb,
        )
    assert ei.value.status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["signin", "signin_typed"])
async def test_a_different_stored_password_is_replaced_by_the_mailbox_owner(fail):
    """CC-03 (re-registered with a new password) and SEC-6 (a squatter registered first): GoTrue
    kept the FIRST password. Whoever proved the mailbox sets theirs — on the ADMIN client — and
    the stamp evicts anything minted under the old one, BEFORE our tokens are minted."""
    sb = FakeSupabase(fail=(fail,))
    out = await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    kinds = _kinds(sb)
    assert ("admin.update_user_by_id", _USER_ID, {"password": _PASSWORD}) in sb.log
    assert ("update", "users", ["password_changed_at"]) in sb.log
    assert kinds.index("verify_otp") < kinds.index("sign_in_attempt") \
        < kinds.index("admin.update_user_by_id") < kinds.index("update")
    assert out.access_token and out.user_id == _USER_ID and out.device_token is None


_CONFIRMED_PREFIX = "Your email is confirmed"


def _assert_confirmed_but_failed(exc: HTTPException, status: int) -> dict:
    """The ONE shape of every confirm-email failure after a good code (C2, BE-AUTH-04): a code of
    its own (never the retryable AUTH_UNAVAILABLE a still-good code gets), action `sign_in`,
    copy opening "Your email is confirmed", and no WWW-Authenticate (no stored credential is
    involved, so iOS must never clear one)."""
    detail = exc.detail
    assert exc.status_code == status, (exc.status_code, detail)
    assert detail["error_code"] == "AUTH_CONFIRMED_SIGN_IN_FAILED", detail
    assert detail["action"] == "sign_in"
    assert detail["user_message"].startswith(_CONFIRMED_PREFIX), detail["user_message"]
    assert not (exc.headers or {}).get("WWW-Authenticate")
    return detail


@pytest.mark.asyncio
@pytest.mark.parametrize("password, why", [
    ("abc", "too short"),
    ("Blue Sky 2025", "its only 'symbol' is a space (GoTrue refuses it)"),
    ("Élan2025€", "no ASCII capital, a non-ASCII symbol"),
    ("Aa1!" + "😀" * 18, "76 bytes: over GoTrue's 72-byte cap"),
])
async def test_a_replacement_password_must_meet_the_rule(password, why, caplog):
    """The existing password is never strength-checked (it may predate the rule), but one we
    are about to WRITE is — against GoTrue's exact rule (C1), BEFORE the admin call. The stored
    password DIFFERED, so the refusal is the password's: 400, "that password can't be used",
    Forgot password named."""
    sb = FakeSupabase(fail=("signin",))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.confirm_email(_confirm(password=password), _FakeRequest(), sb)
    detail = _assert_confirmed_but_failed(ei.value, 400)
    assert detail["user_message"] == auth_ep._CONFIRMED_PASSWORD_REFUSED, why
    assert "Forgot password" in detail["user_message"]
    assert "admin_attempt" not in _kinds(sb), f"the write was attempted: {why}"
    assert not [e for e in sb.log if e[0] == "update"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    if len(password) > 5:  # "abc" is too short to search for without false hits
        for r in caplog.records:
            assert password not in r.getMessage(), "the typed password reached a log line"


@pytest.mark.asyncio
async def test_a_legacy_weak_password_that_MATCHES_still_signs_in():
    """The schema must not strength-validate: an account created under an older rule would be
    confirmable but never sign-in-able through this route."""
    out = await auth_ep.confirm_email(_confirm(password="abc"), _FakeRequest(), FakeSupabase())
    assert out.access_token


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_fail, reasons", [
    ("admin_weak", {"reasons": "pwned"}),
    # GoTrue's 72-byte cap is `validation_failed`, not weak_password: still the password's fault.
    ("admin_too_long", {}),
])
async def test_a_provider_refused_replacement_is_typed(admin_fail, reasons):
    sb = FakeSupabase(fail=("signin", admin_fail))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    detail = _assert_confirmed_but_failed(ei.value, 400)
    assert detail["user_message"] == auth_ep._CONFIRMED_PASSWORD_REFUSED
    assert detail["details"] == reasons
    assert not [e for e in sb.log if e[0] == "update"]


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_fail", ["admin_down", "admin_outage"])
async def test_a_failed_password_write_is_a_503_that_says_the_email_is_confirmed(admin_fail, caplog):
    sb = FakeSupabase(fail=("signin", admin_fail))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    detail = _assert_confirmed_but_failed(ei.value, 503)
    assert detail["user_message"] == auth_ep._CONFIRMED_FINISH_FAILED
    assert "Forgot password" in detail["user_message"] and "sign in" in detail["user_message"]
    assert not [e for e in sb.log if e[0] == "update"], "no stamp: nothing changed"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "an outage reaches Sentry"


# ── After a good code, a sign-in probe that fails UPSTREAM falls through to SET (C3, BE-AUTH-03)

_UPSTREAM_SIGN_IN_FAILURES = [
    pytest.param("signin_429", "GOTRUE_RATE_LIMITED", id="429"),
    pytest.param("signin_transport", None, id="transport"),
    pytest.param("signin_5xx", None, id="5xx"),
    pytest.param("signin_edge_html", None, id="edge-520-html"),
    pytest.param("outage", None, id="untyped"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail, marker", _UPSTREAM_SIGN_IN_FAILURES)
async def test_an_upstream_sign_in_failure_sets_the_typed_password_and_mints(fail, marker, caplog):
    """The code is spent and the address confirmed; refusing here used to leave a squatter's or
    stale FIRST password live on the account (BE-AUTH-03). The caller proved the mailbox, so
    their typed password is written — the `/reset-password` capability, harmless if it already
    matched — stamped, and they are signed in. No device proof: still only an emailed code."""
    sb = FakeSupabase(fail=(fail,))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        out = await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    kinds = _kinds(sb)
    assert ("admin.update_user_by_id", _USER_ID, {"password": _PASSWORD}) in sb.log
    assert ("update", "users", ["password_changed_at"]) in sb.log
    assert kinds.index("verify_otp") < kinds.index("sign_in_attempt") \
        < kinds.index("admin.update_user_by_id") < kinds.index("update")
    assert out.access_token and out.user_id == _USER_ID and out.device_token is None
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "the upstream failure still reaches Sentry"
    if marker:
        assert any(marker in m for m in errors)
    for r in caplog.records:
        m = r.getMessage()
        assert _EMAIL not in m and _CODE_DIGITS not in m and _PASSWORD not in m


@pytest.mark.asyncio
@pytest.mark.parametrize("fail, marker", _UPSTREAM_SIGN_IN_FAILURES)
@pytest.mark.parametrize("admin_fail", ["admin_down", "admin_outage", "admin_weak", "admin_too_long"])
async def test_an_upstream_sign_in_failure_whose_write_also_fails_names_forgot_password(
    fail, marker, admin_fail,
):
    """Only when the write fails too: 503 with the "sign in, or use Forgot password" copy —
    never "that password can't be used", because after an upstream failure nobody learned
    whether the typed password is the stored one. Nothing stamped, nothing minted."""
    sb = FakeSupabase(fail=(fail, admin_fail))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    detail = _assert_confirmed_but_failed(ei.value, 503)
    assert detail["user_message"] == auth_ep._CONFIRMED_FINISH_FAILED
    assert not [e for e in sb.log if e[0] == "update"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail, _marker", _UPSTREAM_SIGN_IN_FAILURES)
async def test_an_upstream_sign_in_failure_with_a_legacy_password_says_sign_in_not_refused(
    fail, _marker,
):
    """A legacy password the rule cannot WRITE may still be the right one for sign-in: after an
    upstream failure it is never called refused (that copy would send a user with a working
    password to Forgot password). 503 "sign in, or use Forgot password", and no write."""
    sb = FakeSupabase(fail=(fail,))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.confirm_email(_confirm(password="abc"), _FakeRequest(), sb)
    detail = _assert_confirmed_but_failed(ei.value, 503)
    assert detail["user_message"] == auth_ep._CONFIRMED_FINISH_FAILED
    assert "admin_attempt" not in _kinds(sb)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [
    "signin_other_user",          # the sign-in resolved ANOTHER account
    "not_confirmed",              # untyped "Email not confirmed" (the wording /login matches)
    "signin_typed_unconfirmed",   # GoTrue's typed email_not_confirmed
    "signin_provider_disabled",   # a 4xx the classifier files as OURS ("unavailable") — policy
    "signin_banned",              # an unclassified 4xx about this account
])
async def test_a_policy_answer_after_the_code_is_spent_says_sign_in_and_never_mints(fail, caplog):
    """An ANSWER about this account is never a reason to set a password and mint past it (the
    C3 caveat: never on not_confirmed, never on a typed 4xx). The address IS confirmed and the
    code is dead, so it is AUTH_CONFIRMED_SIGN_IN_FAILED — "sign in" — and nothing is written."""
    sb = FakeSupabase(fail=(fail,))
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.confirm_email(_confirm(), _FakeRequest(), sb)
    detail = _assert_confirmed_but_failed(ei.value, 503)
    assert detail["user_message"] == auth_ep._CONFIRMED_SIGN_IN_FAILED
    assert "sign in" in detail["user_message"].lower()
    assert "admin_attempt" not in _kinds(sb)
    assert not [e for e in sb.log if e[0] == "update"]
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_confirm_email_checks_the_app_row_before_minting():
    """(g) Same guard as /oauth: tokens for an id with no public.users row authenticate against
    nothing and loop the client forever."""
    with pytest.raises(HTTPException) as ei:
        await auth_ep.confirm_email(_confirm(), _FakeRequest(), FakeSupabase(row={}))
    detail = _assert_confirmed_but_failed(ei.value, 503)
    assert detail["user_message"] == auth_ep._CONFIRMED_ACCOUNT_NOT_READY


@pytest.mark.asyncio
async def test_a_spent_code_and_a_still_good_code_are_told_apart_by_code_not_copy():
    """BE-AUTH-04: a verify OUTAGE (code still good → retry) and a failure after the code was
    spent (→ sign in) used to share 503 AUTH_UNAVAILABLE / retry_later, differing only in
    free text iOS cannot branch on."""
    with pytest.raises(HTTPException) as before:
        await auth_ep.confirm_email(_confirm(), _FakeRequest(), FakeSupabase(fail=("otp_outage",)))
    rate_limiter.clear()
    with pytest.raises(HTTPException) as after:
        await auth_ep.confirm_email(_confirm(), _FakeRequest(), FakeSupabase(fail=("signin_other_user",)))
    assert before.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert before.value.detail["action"] == "retry_later"
    assert after.value.detail["error_code"] == "AUTH_CONFIRMED_SIGN_IN_FAILED"
    assert after.value.detail["action"] == "sign_in"


def test_confirm_email_raises_no_retryable_code_after_the_verify():
    """Source guard, comment-stripped and bounded to the code AFTER `_otp_failure(...)`: once the
    code is spent, nothing may raise AUTH_UNAVAILABLE (retry_later) or AUTH_PASSWORD_REJECTED
    (iOS would keep the dead code), and every helper used names the confirmed code."""
    import inspect

    src = inspect.getsource(auth_ep.confirm_email)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    after = code[code.index('_otp_failure(e, route="confirm-email")'):]
    assert "ErrorCode.AUTH_UNAVAILABLE" not in after
    assert "ErrorCode.AUTH_PASSWORD_REJECTED" not in after
    assert after.count("_confirmed_but_failed(") >= 6, "a post-verify failure lost its typed shape"
    helper = "\n".join(
        l.split("#", 1)[0] for l in inspect.getsource(auth_ep._confirmed_but_failed).splitlines()
    )
    assert "ErrorCode.AUTH_CONFIRMED_SIGN_IN_FAILED" in helper


def test_every_confirmed_copy_opens_with_the_confirmation():
    for name in ("_CONFIRMED_SIGN_IN_FAILED", "_CONFIRMED_PASSWORD_REFUSED",
                 "_CONFIRMED_FINISH_FAILED", "_CONFIRMED_ACCOUNT_NOT_READY"):
        assert getattr(auth_ep, name).startswith(_CONFIRMED_PREFIX), name
    assert "Forgot password" in auth_ep._CONFIRMED_PASSWORD_REFUSED
    assert "Forgot password" in auth_ep._CONFIRMED_FINISH_FAILED
    assert auth_ep._CONFIRMED_SIGN_IN_FAILED.endswith("Please sign in.")


# ── `_is_upstream_failure`: the line between "GoTrue was down" and "GoTrue answered" ──

@pytest.mark.parametrize("build, expected", [
    (lambda: _gotrue_error(429, {"code": "over_request_rate_limit", "msg": "x"}), True),
    (lambda: _gotrue_error(400, {"code": "over_request_rate_limit", "msg": "x"}), True),
    (_gotrue_transport_error, True),
    (lambda: _gotrue_error(502, {"msg": "bad gateway"}), True),
    (lambda: _gotrue_error(500, {"code": "unexpected_failure", "msg": "db"}), True),
    (lambda: _gotrue_error(520, text="<html>edge</html>"), True),
    (lambda: RuntimeError("upstream gateway timeout"), True),       # untyped, no status/code
    (lambda: _gotrue_error(400, {"code": "invalid_credentials", "msg": "Invalid login credentials"}), False),
    (lambda: _gotrue_error(400, {"code": "email_not_confirmed", "msg": "Email not confirmed"}), False),
    (lambda: _gotrue_error(400, {"code": "email_provider_disabled", "msg": "disabled"}), False),
    (lambda: _gotrue_error(403, {"code": "bad_jwt", "msg": "bad"}), False),
    (lambda: _gotrue_error(422, {"code": "signup_disabled", "msg": "x"}), False),
    (lambda: _gotrue_error(400, {"code": "user_banned", "msg": "banned"}), False),
    (lambda: _gotrue_error(404, {"code": "user_not_found", "msg": "x"}), False),
    (lambda: RuntimeError("Token has expired or is invalid"), False),  # untyped, "rejected" words
])
def test_is_upstream_failure(build, expected):
    assert auth_ep._is_upstream_failure(build()) is expected


def test_an_untyped_not_confirmed_reads_as_upstream_so_it_is_tested_first():
    """Documents WHY confirm-email tests `_is_email_not_confirmed` before `_is_upstream_failure`:
    the untyped wording carries no status, so on its own it would read as an outage."""
    e = RuntimeError("Email not confirmed")
    assert auth_ep._is_email_not_confirmed(e) and auth_ep._is_upstream_failure(e)
    import inspect

    src = "\n".join(l.split("#", 1)[0] for l in inspect.getsource(auth_ep.confirm_email).splitlines())
    assert "not _is_email_not_confirmed(e) and _is_upstream_failure(e)" in src


@pytest.mark.parametrize("bad", [
    {"code": "1-2-3-"}, {"code": "abcdefgh"}, {"code": ""}, {"password": ""},
    {"password": "x" * 1025}, {"email": "not-an-email"},
])
def test_confirm_email_schema_refuses_malformed_input_before_any_upstream_call(bad):
    with pytest.raises(ValueError):
        ConfirmEmailRequest(**{"email": _EMAIL, "code": _CODE, "password": _PASSWORD, **bad})


# ── 7. Register: no profile write for a pending address; failures classified ──

@pytest.mark.asyncio
async def test_register_writes_nothing_to_public_users():
    """AUTH-BE-04: the trigger copies display_name from metadata. The old UPDATE also ran for an
    address already pending under SOMEONE ELSE (GoTrue returns that real user unchanged), so any
    unauthenticated caller could rewrite the pending owner's profile name."""
    sb = FakeSupabase(confirmed=False)
    await auth_ep.sign_up(
        SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="Attacker Text"),
        _FakeRequest(), sb,
    )
    assert not [e for e in sb.log if e[0] == "update"]


@pytest.mark.asyncio
async def test_register_copy_is_neutral_and_names_the_way_out():
    out = await auth_ep.sign_up(
        SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="N"),
        _FakeRequest(), FakeSupabase(confirmed=False),
    )
    assert "link" not in out.message.lower()
    assert "sign in" in out.message.lower() and "reset" in out.message.lower()


_SEND_FAILED = "couldn't send your confirmation email"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag, status, code, copy, level", [
    ("signup_email_rate", 503, "AUTH_UNAVAILABLE", _SEND_FAILED, logging.WARNING),
    ("signup_smtp", 503, "AUTH_UNAVAILABLE", _SEND_FAILED, logging.WARNING),
    ("signup_down", 503, "AUTH_UNAVAILABLE", _SEND_FAILED, logging.WARNING),
    ("signup_bad_gateway", 503, "AUTH_UNAVAILABLE", _SEND_FAILED, logging.WARNING),
    ("signup_edge_html", 503, "AUTH_UNAVAILABLE", _SEND_FAILED, logging.WARNING),
    ("signup_ip_rate", 503, "AUTH_UNAVAILABLE", "couldn't create your account", logging.ERROR),
    ("signup_invalid_email", 400, "INVALID_INPUT", "email address can't be used", logging.INFO),
    ("signup_weak", 400, "AUTH_PASSWORD_REJECTED", None, logging.INFO),
    # GoTrue's 72-byte cap answers `validation_failed`; it used to fall to "Registration failed".
    ("signup_too_long", 400, "AUTH_PASSWORD_REJECTED", "72 bytes at most", logging.INFO),
])
async def test_register_classifies_provider_failures(flag, status, code, copy, level, caplog):
    """AUTH-BE-08 / SB-6: each of these was a bare 400 "Registration failed" at ERROR."""
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.sign_up(
                SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="N"),
                _FakeRequest(), FakeSupabase(fail=(flag,)),
            )
    assert ei.value.status_code == status
    assert ei.value.detail["error_code"] == code
    if copy:
        assert copy in ei.value.detail["user_message"]
    own = [r for r in caplog.records if r.name == "app.api.v1.endpoints.auth"]
    assert max(r.levelno for r in own) == level, [(r.levelname, r.getMessage()) for r in own]
    assert not any(_EMAIL in r.getMessage() for r in own), "the address must never be logged"
    if flag == "signup_email_rate":
        assert any("GOTRUE_EMAIL_RATE_LIMITED" in r.getMessage() for r in own)
    if flag == "signup_ip_rate":
        assert any("GOTRUE_RATE_LIMITED" in r.getMessage() for r in own)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [
    "signup", "signup_disabled",
    # `validation_failed` about a field that is NOT the password is not a password refusal.
    "signup_validation_email",
])
async def test_register_keeps_the_generic_fallback_for_what_needs_a_person(flag, caplog):
    """Untyped exceptions (our bugs) and 4xx configuration codes keep the literal fallback, with
    a stack trace at ERROR — they need a person, not a retry."""
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.sign_up(
                SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="N"),
                _FakeRequest(), FakeSupabase(fail=(flag,)),
            )
    assert ei.value.status_code == 400 and ei.value.detail == "Registration failed"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]


@pytest.mark.asyncio
async def test_register_per_email_denial_says_3600():
    """IOS-AUTH-04: Retry-After is the window of the bucket that denied (it said "60")."""
    for i in range(5):
        await auth_ep.sign_up(
            SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="N"),
            _FakeRequest(f"198.51.100.{100 + i}"), FakeSupabase(confirmed=False),
        )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.sign_up(
            SignUpRequest(email=_EMAIL, password=_PASSWORD, display_name="N"),
            _FakeRequest("198.51.100.199"), FakeSupabase(confirmed=False),
        )
    assert ei.value.status_code == 429 and ei.value.headers["Retry-After"] == "3600"


@pytest.mark.parametrize("password, named", [
    ("Aa1 bbbbbbbb", "a symbol"),            # a space is NOT a symbol to GoTrue (verified live)
    ("Blue Sky 2025", "a symbol"),
    ("Abcdefg1€", "a symbol"),                # € is not in GoTrue's 32-character list
    ("Élan2025!", "an uppercase letter"),     # É is not A–Z
    ("abcdefgh1!", "an uppercase letter"),
    ("A" * 80, "72 bytes"),                   # GoTrue refuses > 72 bytes
    ("Aa1!" + "é" * 35, "72 bytes"),          # 39 characters, 74 bytes
])
def test_register_refuses_what_gotrue_would_refuse_before_any_upstream_call(password, named):
    """C1: the sign-up schema is GoTrue's own rule, so the 422 (with the reason) comes from us
    BEFORE GoTrue is called — never an after-the-fact AUTH_PASSWORD_REJECTED with no reason."""
    with pytest.raises(ValueError) as ei:
        SignUpRequest(email=_EMAIL, password=password, display_name="N")
    assert named in str(ei.value)


# ── 8. Social sign-in: a missing app row is OURS (SOC-12) ─────────────────────

@pytest.mark.asyncio
async def test_oauth_missing_app_row_is_a_retryable_503_not_sign_in_again():
    """Was 401 AUTH_ACCOUNT_NOT_FOUND "Please sign in again" — said to someone mid-sign-in, and a
    code iOS may clear a credential on. No token was issued; the fault is a trigger's."""
    with pytest.raises(HTTPException) as ei:
        await auth_ep.oauth_sign_in(
            OAuthSignInRequest(provider="apple", id_token="x" * 40),
            _FakeRequest(), FakeSupabase(row={}),
        )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert ei.value.detail["user_message"] == "Your account isn't ready yet. Please try again."


# ── 9. Session exchange: anonymous, pre-reset and unverifiable tokens (SEC-2, SOC-02) ──

@pytest.mark.asyncio
async def test_session_exchange_refuses_an_anonymous_session(monkeypatch):
    monkeypatch.setattr(auth_ep, "verify_supabase_token",
                        _async_returning({"sub": _USER_ID, "is_anonymous": True}))
    sb = FakeSupabase()
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(), sb,
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == "AUTH_PROVIDER_FAILED"
    assert sb.log == []


@pytest.mark.asyncio
async def test_session_exchange_turns_a_raising_verifier_into_a_401_not_a_500(monkeypatch):
    """SOC-02: python-jose's JWKError (a JOSEError, not a JWTError) escaped as an off-contract
    500 with a Sentry event per forged POST."""
    async def _raises(_t):
        raise RuntimeError("JWKError: alg mismatch")
    monkeypatch.setattr(auth_ep, "verify_supabase_token", _raises)
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(), FakeSupabase(),
        )
    assert ei.value.status_code == 401 and ei.value.detail["error_code"] == "AUTH_PROVIDER_FAILED"


def _stamp(offset_seconds: float) -> tuple[float, str]:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(microsecond=500_000)
    return now.timestamp(), (now + timedelta(seconds=offset_seconds)).isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("iat_shift, refused", [
    (-3600, True),    # a Supabase token minted an hour BEFORE the reset
    (0, False),       # minted in the same second as the stamp: whole-second comparison
    (+60, False),     # minted after the reset
])
async def test_session_exchange_refuses_a_token_minted_before_the_password_change(
    monkeypatch, iat_shift, refused,
):
    """SEC-2: a thief's still-unexpired Supabase JWT, exchanged AFTER the victim's reset, used to
    become fresh app tokens (iat=now) that pass every later eviction check."""
    now_ts, stamp = _stamp(0)
    monkeypatch.setattr(auth_ep, "verify_supabase_token", _async_returning(
        {"sub": _USER_ID, "email": _EMAIL, "iat": int(now_ts + iat_shift)}))
    sb = FakeSupabase(row={"id": _USER_ID, "password_changed_at": stamp})
    if refused:
        with pytest.raises(HTTPException) as ei:
            await auth_ep.session_exchange(
                SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(), sb,
            )
        assert ei.value.status_code == 401
        assert ei.value.detail["error_code"] == "AUTH_PROVIDER_FAILED"
    else:
        out = await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(), sb,
        )
        assert out.access_token


@pytest.mark.asyncio
async def test_session_exchange_fails_closed_on_a_missing_iat_once_a_password_changed(monkeypatch):
    _, stamp = _stamp(0)
    monkeypatch.setattr(auth_ep, "verify_supabase_token",
                        _async_returning({"sub": _USER_ID, "email": _EMAIL}))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(),
            FakeSupabase(row={"id": _USER_ID, "password_changed_at": stamp}),
        )
    assert ei.value.detail["error_code"] == "AUTH_PROVIDER_FAILED"


# ── 10. No secret ever reprs (SEC-1) ──────────────────────────────────────────

def _every_auth_model_with_secrets():
    """(model, kwargs, secret values) for every schema in app/schemas/auth.py that holds one."""
    from app.schemas import auth as s

    pw, code = "Hunter2!xyzA", "12345678"
    return [
        (s.SignInRequest, {"email": _EMAIL, "password": pw}, [pw]),
        (s.SignUpRequest, {"email": _EMAIL, "password": pw, "display_name": "V"}, [pw]),
        (s.ResetPasswordRequest, {"email": _EMAIL, "code": code, "new_password": pw}, [code, pw]),
        (s.ChangePasswordRequest, {"current_password": "Old-pass-1!", "new_password": pw},
         ["Old-pass-1!", pw]),
        (s.SetPasswordRequest, {"code": code, "new_password": pw}, [code, pw]),
        (s.ConfirmEmailRequest, {"email": _EMAIL, "code": code, "password": pw}, [code, pw]),
        (s.RefreshTokenRequest, {"refresh_token": "rt-SECRET-123"}, ["rt-SECRET-123"]),
        (s.OAuthSignInRequest, {"provider": "apple", "id_token": "idtok-" + "S" * 30,
                                "nonce": "nonce-SECRET"}, ["idtok-" + "S" * 30, "nonce-SECRET"]),
        (s.SessionExchangeRequest, {"supabase_access_token": "sbat-" + "S" * 30}, ["sbat-" + "S" * 30]),
        (s.TokenResponse, {"access_token": "AT-SECRET", "refresh_token": "RT-SECRET",
                           "user_id": "u", "device_token": "DT-SECRET"},
         ["AT-SECRET", "RT-SECRET", "DT-SECRET"]),
        (s.PasswordChangedResponse, {"message": "m", "access_token": "AT-SECRET",
                                     "refresh_token": "RT-SECRET", "user_id": "u"},
         ["AT-SECRET", "RT-SECRET"]),
        (s.SignUpResponse, {"confirmation_required": False, "message": "m",
                            "access_token": "AT-SECRET", "refresh_token": "RT-SECRET"},
         ["AT-SECRET", "RT-SECRET"]),
    ]


@pytest.mark.parametrize("model, kwargs, secrets", _every_auth_model_with_secrets(),
                         ids=lambda v: getattr(v, "__name__", None) or "")
def test_no_auth_model_reprs_a_secret(model, kwargs, secrets):
    """Sentry ships frame locals, and serialises an unknown object — a pydantic model — as its
    repr. A GoTrue 429 on sign-up put "SignUpRequest(email=…, password='Hunter2!…')" into
    Sentry. `repr=False` drops the field from __repr__/__str__ only; model_dump still has it."""
    from sentry_sdk.utils import safe_repr

    inst = model(**kwargs)
    for rendered in (repr(inst), str(inst), safe_repr(inst)):
        for secret in secrets:
            assert secret not in rendered, f"{model.__name__} reprs a secret: {rendered}"
    dumped = json.dumps(inst.model_dump())
    for secret in secrets:
        assert secret in dumped, "the value must still be forwarded upstream / to the client"


def test_the_secret_walk_covers_every_secret_field_in_the_module():
    """Anti-vacuity: a new secret-looking field added to app/schemas/auth.py without
    repr=False fails here, not in Sentry."""
    import re

    from app.schemas import auth as s

    secret_name = re.compile(r"(password|code|token|nonce)$")
    for name in dir(s):
        model = getattr(s, name)
        if not (isinstance(model, type) and issubclass(model, s.BaseModel) and model is not s.BaseModel):
            continue
        if model.__module__ != s.__name__:
            continue
        for field_name, field in model.model_fields.items():
            if secret_name.search(field_name) and field_name != "token_type":
                assert field.repr is False, f"{model.__name__}.{field_name} would repr its value"


def test_every_auth_route_forwards_the_callers_address_to_gotrue():
    """SEC-7, router-level so a new route is covered by default: each /api/v1/auth route
    resolves `forward_client_ip_to_gotrue` before its handler runs."""
    from app.dependencies import forward_client_ip_to_gotrue
    from app.main import app

    routes = [r for r in app.routes if getattr(r, "path", "").startswith("/api/v1/auth/")]
    assert len(routes) >= 12, [r.path for r in routes]
    missing = [r.path for r in routes
               if forward_client_ip_to_gotrue not in [d.call for d in r.dependant.dependencies]]
    assert not missing, missing
