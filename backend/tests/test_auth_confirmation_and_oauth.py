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

No network / Supabase — fakes throughout, and the shared rate limiter is reset per test.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import auth as auth_ep
from app.core.security import rate_limiter
from app.schemas.auth import (
    OAuthSignInRequest,
    ResendConfirmationRequest,
    SessionExchangeRequest,
    SignInRequest,
    SignUpRequest,
)

_USER_ID = "11112222-3333-4444-8555-666677778888"
_EMAIL = "new.user@example.com"
_PASSWORD = "a-long-enough-password"


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    store = getattr(rate_limiter, "_requests", None)
    if isinstance(store, dict):
        store.clear()
    yield


class _FakeRequest:
    def __init__(self, ip="203.0.113.44"):
        self.client = type("C", (), {"host": ip})()


class _FakeUser:
    def __init__(self, confirmed: bool, uid=_USER_ID, email=_EMAIL):
        self.id = uid
        self.email = email
        self.email_confirmed_at = "2026-07-30T00:00:00Z" if confirmed else None


class _FakeAuth:
    def __init__(self, log, user, fail):
        self._log, self._user, self._fail = log, user, fail

    def sign_up(self, payload):
        if "signup" in self._fail:
            raise RuntimeError("signup failed")
        self._log.append(("sign_up", payload["email"]))
        return type("R", (), {"user": self._user})()

    def sign_in_with_password(self, creds):
        if "not_confirmed" in self._fail:
            raise RuntimeError("Email not confirmed")
        if "signin" in self._fail:
            raise RuntimeError("bad credentials")
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
    """Apple returns the name only on the FIRST authorization — miss it and it's gone."""
    sb = FakeSupabase(row={"id": _USER_ID, "display_name": None})
    await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider="apple", id_token="x" * 40, display_name="Ada L"),
        _FakeRequest(), sb,
    )
    assert ("update", "users", ["display_name"]) in sb.log


@pytest.mark.asyncio
async def test_oauth_never_overwrites_an_existing_display_name():
    """The user may have renamed themselves deliberately since signing up."""
    sb = FakeSupabase(row={"id": _USER_ID, "display_name": "Chosen Name"})
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
    monkeypatch.setattr(auth_ep, "verify_supabase_token", lambda _t: None)
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(),
            FakeSupabase(),
        )
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_session_exchange_issues_tokens_for_a_verified_token(monkeypatch):
    monkeypatch.setattr(
        auth_ep, "verify_supabase_token", lambda _t: {"sub": _USER_ID, "email": _EMAIL}
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
        auth_ep, "verify_supabase_token", lambda _t: {"sub": _USER_ID, "email": _EMAIL}
    )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="t" * 40), _FakeRequest(),
            FakeSupabase(fail=("lookup",)),
        )
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_session_exchange_never_trusts_the_sub_before_verification(monkeypatch):
    """A forged token must not reach the DB lookup at all."""
    seen = []
    monkeypatch.setattr(
        auth_ep, "verify_supabase_token", lambda _t: seen.append("verified") or None
    )
    sb = FakeSupabase()
    with pytest.raises(HTTPException):
        await auth_ep.session_exchange(
            SessionExchangeRequest(supabase_access_token="forged" * 10), _FakeRequest(), sb
        )
    assert seen == ["verified"]
    assert sb.log == [], "no DB access before the signature check passes"
