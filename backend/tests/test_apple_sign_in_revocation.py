"""Sign in with Apple token revocation on account deletion (sign-in audit SOC-04, 2026-11-26).

Apple's account-deletion guidance asks an app that offers Sign in with Apple to revoke the user's
tokens through its REST API when they delete their account. GoTrue's id_token grant never holds
an Apple token, so the app (≥ 1.04) sends the sign-in's single-use authorization code with
`/auth/oauth`, the backend exchanges it for a refresh token (`apple_sign_in_grants`, migration 213)
and `DELETE /users/me` revokes it BEFORE the auth row goes.

What is pinned here, all hermetic (httpx.MockTransport, in-memory fakes — no Apple, no Supabase):

1. `integrations/apple_id.py` — the ES256 client secret's exact shape, the form each endpoint
   gets, the typed error split (rejected vs unavailable), refusal BEFORE any I/O when unconfigured,
   and that no exception message carries the code, a token or the secret.
2. `services/apple_sign_in_service.py` — every outcome of the exchange and the revoke, the
   subject check (a code that is not this sign-in's is never stored), and that nothing it logs
   carries a credential. Both halves NEVER raise.
3. The wiring — `/auth/oauth` exchanges only for Apple, only with a code, only after the
   app-row check, and still signs in when the exchange fails; `DELETE /users/me` revokes after the
   purge check and before `admin.delete_user`, and still deletes when the revoke fails.
4. The schema (an old build that sends no code still decodes) and the Sentry redaction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt as jose_jwt

from app.config import settings
from app.integrations import apple_id
from app.services import apple_sign_in_service as svc

_USER_ID = "11111111-2222-4333-8444-555555555555"
_APPLE_SUB = "001234.abcdef0123456789abcdef0123456789.0123"
_CODE = "c" + "0123456789abcdef" * 4 + ".0.mrqz.SECRETCODE"
_REFRESH = "r" + "fedcba9876543210" * 4 + ".0.mrqz.SECRETREFRESH"
_KEY_ID = "ABC123DEFG"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _ec_key_pair():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pem, public


_PEM, _PUBLIC = _ec_key_pair()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_KEY_ID", _KEY_ID)
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_PRIVATE_KEY", _PEM)
    monkeypatch.setattr(settings, "APPLE_TEAM_ID", "WG697LVCS9")
    monkeypatch.setattr(settings, "APPLE_BUNDLE_ID", "com.phan.caydex")
    yield


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_KEY_ID", None)
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_PRIVATE_KEY", None)
    yield


@pytest.fixture(autouse=True)
def _fresh_client_and_warning(monkeypatch):
    """Every test gets its own transport; the once-per-process warning is reset."""
    monkeypatch.setattr(apple_id, "_client", None)
    monkeypatch.setattr(svc, "_warned_not_configured", False)
    yield
    apple_id._client = None


def _install_transport(monkeypatch, handler):
    calls: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = httpx.AsyncClient(
        base_url=apple_id.APPLE_ID_BASE_URL, transport=httpx.MockTransport(_record),
    )
    monkeypatch.setattr(apple_id, "_client", client)
    return calls


def _form(request: httpx.Request) -> dict:
    return dict(urllib.parse.parse_qsl(request.content.decode()))


def _id_token(sub=_APPLE_SUB) -> str:
    # An unsigned-looking but well-formed JWT: only its claims are read (Apple's own token
    # endpoint, over TLS — OIDC Core §3.1.3.7; or a token Supabase already verified).
    return jose_jwt.encode({"sub": sub, "aud": "com.phan.caydex"}, "k" * 32, algorithm="HS256")


def _exchange_ok(request):
    return httpx.Response(200, json={
        "access_token": "a" * 40, "token_type": "Bearer", "expires_in": 3600,
        "refresh_token": _REFRESH, "id_token": _id_token(),
    })


# ── 1. The thin client ────────────────────────────────────────────────────────

def test_is_configured_needs_both_the_key_id_and_the_key(monkeypatch, configured):
    assert apple_id.is_configured()
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_KEY_ID", "  ")
    assert not apple_id.is_configured()
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_KEY_ID", _KEY_ID)
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_PRIVATE_KEY", "")
    assert not apple_id.is_configured()


def test_the_client_secret_is_the_es256_jwt_apple_documents(configured):
    secret = apple_id._client_secret("com.phan.caydex", now=1_800_000_000)
    header = jose_jwt.get_unverified_header(secret)
    assert header["alg"] == "ES256" and header["kid"] == _KEY_ID
    claims = jose_jwt.decode(
        secret, _PUBLIC, algorithms=["ES256"], audience="https://appleid.apple.com",
        options={"verify_exp": False},
    )
    assert claims["iss"] == "WG697LVCS9"
    assert claims["sub"] == "com.phan.caydex"
    assert claims["iat"] == 1_800_000_000
    # Apple caps a client secret at six months; ours is minted per call and lives 5 minutes.
    assert claims["exp"] - claims["iat"] == 300


def test_a_one_line_key_with_escaped_newlines_still_signs(monkeypatch, configured):
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_PRIVATE_KEY", _PEM.strip().replace("\n", "\\n"))
    secret = apple_id._client_secret("com.phan.caydex")
    jose_jwt.decode(secret, _PUBLIC, algorithms=["ES256"], audience="https://appleid.apple.com")


def test_a_malformed_key_is_not_configured_and_never_quoted(monkeypatch, configured):
    bad = "-----BEGIN PRIVATE KEY-----\nNOTAKEYSECRETMATERIAL\n-----END PRIVATE KEY-----"
    monkeypatch.setattr(settings, "APPLE_SIGN_IN_PRIVATE_KEY", bad)
    with pytest.raises(apple_id.AppleIDNotConfiguredException) as ei:
        apple_id._client_secret("com.phan.caydex")
    assert "NOTAKEYSECRETMATERIAL" not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


@pytest.mark.asyncio
async def test_unconfigured_refuses_before_any_request(monkeypatch, unconfigured):
    calls = _install_transport(monkeypatch, _exchange_ok)
    with pytest.raises(apple_id.AppleIDNotConfiguredException):
        await apple_id.exchange_authorization_code(_CODE)
    with pytest.raises(apple_id.AppleIDNotConfiguredException):
        await apple_id.revoke_token(_REFRESH)
    assert calls == []


@pytest.mark.asyncio
async def test_the_exchange_posts_the_documented_form(monkeypatch, configured):
    calls = _install_transport(monkeypatch, _exchange_ok)
    body = await apple_id.exchange_authorization_code(_CODE)
    assert body["refresh_token"] == _REFRESH
    (request,) = calls
    assert request.method == "POST" and request.url.path == "/auth/token"
    form = _form(request)
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == _CODE
    assert form["client_id"] == "com.phan.caydex"
    assert "redirect_uri" not in form, "a native app's code needs no redirect_uri"
    jose_jwt.decode(form["client_secret"], _PUBLIC, algorithms=["ES256"],
                    audience="https://appleid.apple.com")


@pytest.mark.asyncio
async def test_the_revoke_posts_the_documented_form(monkeypatch, configured):
    calls = _install_transport(monkeypatch, lambda r: httpx.Response(200, content=b""))
    await apple_id.revoke_token(_REFRESH, client_id="com.phan.caydex")
    (request,) = calls
    assert request.url.path == "/auth/revoke"
    form = _form(request)
    assert form["token"] == _REFRESH and form["token_type_hint"] == "refresh_token"
    assert form["client_id"] == "com.phan.caydex"


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["exchange", "revoke"])
@pytest.mark.parametrize("status,body,error", [
    (400, {"error": "invalid_grant"}, "invalid_grant"),
    (401, {"error": "invalid_client"}, "invalid_client"),
    # Anything that is not a short OAuth word never reaches a log line or a message.
    (400, {"error": "<script>" + _CODE}, "http_400"),
    (400, None, "http_400"),
])
async def test_a_4xx_is_a_typed_rejection_that_never_echoes_a_secret(
        monkeypatch, configured, call, status, body, error):
    resp = (lambda r: httpx.Response(status, json=body)) if body is not None \
        else (lambda r: httpx.Response(status, text="not json " + _CODE))
    _install_transport(monkeypatch, resp)
    with pytest.raises(apple_id.AppleIDRejectedException) as ei:
        if call == "exchange":
            await apple_id.exchange_authorization_code(_CODE)
        else:
            await apple_id.revoke_token(_REFRESH)
    assert ei.value.error == error and ei.value.status == status
    for secret in (_CODE, _REFRESH, "SECRETCODE", "SECRETREFRESH"):
        assert secret not in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(429, json={"error": "rate_limited"}),
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(503, text="down"),
    lambda r: httpx.Response(204),
])
async def test_a_429_5xx_or_odd_status_is_unavailable(monkeypatch, configured, handler):
    _install_transport(monkeypatch, handler)
    with pytest.raises(apple_id.AppleIDUnavailableException):
        await apple_id.revoke_token(_REFRESH)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_a_transport_failure_is_unavailable(monkeypatch, configured, exc):
    def _raise(request):
        raise exc
    _install_transport(monkeypatch, _raise)
    with pytest.raises(apple_id.AppleIDUnavailableException) as ei:
        await apple_id.exchange_authorization_code(_CODE)
    assert _CODE not in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(200, text="<html>"),
    lambda r: httpx.Response(200, json=["not", "an", "object"]),
])
async def test_an_exchange_200_without_a_json_object_is_unavailable(monkeypatch, configured, handler):
    _install_transport(monkeypatch, handler)
    with pytest.raises(apple_id.AppleIDUnavailableException):
        await apple_id.exchange_authorization_code(_CODE)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", None])
async def test_blank_inputs_are_refused_before_io(monkeypatch, configured, bad):
    calls = _install_transport(monkeypatch, _exchange_ok)
    with pytest.raises(ValueError):
        await apple_id.exchange_authorization_code(bad)
    with pytest.raises(ValueError):
        await apple_id.revoke_token(bad)
    with pytest.raises(ValueError):
        await apple_id.revoke_token(_REFRESH, token_type_hint="id_token")
    assert calls == []


# ── 2. The service ────────────────────────────────────────────────────────────

class _Query:
    def __init__(self, sb, table):
        self._sb, self._table = sb, table
        self._op = None

    def upsert(self, row, on_conflict=None):
        self._op = ("upsert", row, on_conflict)
        return self

    def select(self, cols):
        self._op = ("select", cols)
        return self

    def eq(self, col, val):
        self._sb.log.append(("eq", self._table, col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self._sb.fail:
            raise RuntimeError(self._sb.fail)
        self._sb.log.append((self._op[0], self._table) + tuple(self._op[1:]))
        if self._op[0] == "select":
            return type("R", (), {"data": self._sb.rows})()
        return type("R", (), {"data": [self._op[1]]})()


class FakeSupabase:
    def __init__(self, rows=None, fail=None):
        self.log: list[tuple] = []
        self.rows = rows if rows is not None else []
        self.fail = fail

    def table(self, name):
        return _Query(self, name)


async def _store(sb, code=_CODE, identity=None):
    return await svc.store_grant_from_authorization_code(
        sb, user_id=_USER_ID, authorization_code=code,
        identity_token=identity if identity is not None else _id_token(),
    )


def _no_secret_logged(caplog):
    text = caplog.text + " ".join(str(r.args) for r in caplog.records)
    for secret in (_CODE, _REFRESH, "SECRETCODE", "SECRETREFRESH", _PEM.splitlines()[1]):
        assert secret not in text


@pytest.mark.asyncio
async def test_store_happy_path_upserts_one_row_per_user(monkeypatch, configured, caplog):
    caplog.set_level(logging.DEBUG)
    _install_transport(monkeypatch, _exchange_ok)
    sb = FakeSupabase()
    assert await _store(sb) == svc.STORED
    (_, table, row, conflict), = [e for e in sb.log if e[0] == "upsert"]
    assert table == "apple_sign_in_grants" and conflict == "user_id"
    assert row["user_id"] == _USER_ID and row["apple_sub"] == _APPLE_SUB
    assert row["client_id"] == "com.phan.caydex" and row["refresh_token"] == _REFRESH
    assert "updated_at" in row and "created_at" not in row, "an upsert must keep created_at"
    _no_secret_logged(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [None, "", "   "])
async def test_no_code_is_a_no_op(monkeypatch, configured, code):
    calls = _install_transport(monkeypatch, _exchange_ok)
    sb = FakeSupabase()
    assert await _store(sb, code=code) == svc.NO_CODE
    assert calls == [] and sb.log == []


@pytest.mark.asyncio
async def test_unconfigured_stores_nothing_and_warns_once_per_process(monkeypatch, unconfigured, caplog):
    caplog.set_level(logging.DEBUG)
    calls = _install_transport(monkeypatch, _exchange_ok)
    sb = FakeSupabase()
    assert await _store(sb) == svc.NOT_CONFIGURED
    assert await _store(sb) == svc.NOT_CONFIGURED
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "NOT configured" in warnings[0].getMessage()
    assert calls == [] and sb.log == []


@pytest.mark.asyncio
async def test_a_code_for_another_apple_user_is_never_stored(monkeypatch, configured, caplog):
    """Someone pairs their own verified identity token with another person's intercepted code:
    storing it would let THEIR deletion revoke THAT person's Apple grant."""
    caplog.set_level(logging.INFO)

    def _other(request):
        return httpx.Response(200, json={"refresh_token": _REFRESH,
                                         "id_token": _id_token(sub="000999.someone.else.0001")})
    _install_transport(monkeypatch, _other)
    sb = FakeSupabase()
    assert await _store(sb) == svc.SUBJECT_MISMATCH
    assert not [e for e in sb.log if e[0] == "upsert"]
    _no_secret_logged(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"refresh_token": _REFRESH},                                  # no id_token
    {"refresh_token": _REFRESH, "id_token": "not-a-jwt"},
    {"refresh_token": _REFRESH, "id_token": jose_jwt.encode({"aud": "x"}, "k" * 32)},  # no sub
])
async def test_an_answer_whose_subject_cannot_be_read_is_never_stored(monkeypatch, configured, answer):
    _install_transport(monkeypatch, lambda r: httpx.Response(200, json=answer))
    sb = FakeSupabase()
    assert await _store(sb) == svc.SUBJECT_UNVERIFIED
    assert not [e for e in sb.log if e[0] == "upsert"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["garbage", "a.b", jose_jwt.encode({"aud": "x"}, "k" * 32)])
async def test_an_unreadable_identity_token_skips_the_exchange(monkeypatch, configured, identity):
    calls = _install_transport(monkeypatch, _exchange_ok)
    assert await _store(FakeSupabase(), identity=identity) == svc.SUBJECT_UNVERIFIED
    assert calls == [], "the single-use code must not be spent when nothing could be stored"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"id_token": _id_token()},                                       # no refresh token
    {"refresh_token": "", "id_token": _id_token()},
    {"refresh_token": 12345, "id_token": _id_token()},
    {"refresh_token": "x" * 5000, "id_token": _id_token()},           # over the column CHECK
])
async def test_no_usable_refresh_token_stores_nothing(monkeypatch, configured, answer, caplog):
    caplog.set_level(logging.INFO)
    _install_transport(monkeypatch, lambda r: httpx.Response(200, json=answer))
    sb = FakeSupabase()
    assert await _store(sb) == svc.NO_REFRESH_TOKEN
    assert not [e for e in sb.log if e[0] == "upsert"]
    assert "x" * 100 not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error,level", [
    (400, "invalid_grant", logging.WARNING),     # a retried /auth/oauth reuses a spent code
    (401, "invalid_client", logging.ERROR),      # wrong key / key id / team id: an owner fix
])
async def test_a_refused_exchange_is_logged_at_the_right_level(
        monkeypatch, configured, caplog, status, error, level):
    caplog.set_level(logging.INFO)
    _install_transport(monkeypatch, lambda r: httpx.Response(status, json={"error": error}))
    assert await _store(FakeSupabase()) == svc.EXCHANGE_REJECTED
    hits = [r for r in caplog.records if error in r.getMessage()]
    assert hits and hits[0].levelno == level
    _no_secret_logged(caplog)


@pytest.mark.asyncio
async def test_an_unreachable_apple_is_an_outcome_not_an_exception(monkeypatch, configured):
    _install_transport(monkeypatch, lambda r: httpx.Response(503))
    assert await _store(FakeSupabase()) == svc.EXCHANGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_a_slow_apple_cannot_hold_the_sign_in(monkeypatch, configured):
    async def _slow(*_a, **_k):
        await asyncio.sleep(5)
    monkeypatch.setattr(apple_id, "exchange_authorization_code", _slow)
    monkeypatch.setattr(svc, "EXCHANGE_TIMEOUT_SECONDS", 0.05)
    assert await _store(FakeSupabase()) == svc.EXCHANGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_an_unexpected_exchange_error_never_escapes(monkeypatch, configured):
    async def _boom(*_a, **_k):
        raise KeyError("surprise")
    monkeypatch.setattr(apple_id, "exchange_authorization_code", _boom)
    assert await _store(FakeSupabase()) == svc.EXCHANGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_a_failed_write_is_reported_not_raised(monkeypatch, configured, caplog):
    caplog.set_level(logging.INFO)
    _install_transport(monkeypatch, _exchange_ok)
    assert await _store(FakeSupabase(fail="relation does not exist")) == svc.STORE_FAILED
    assert "migration 213" in caplog.text
    _no_secret_logged(caplog)


@pytest.mark.asyncio
async def test_revoke_unconfigured_warns_and_reads_nothing(unconfigured, caplog):
    caplog.set_level(logging.INFO)
    sb = FakeSupabase(rows=[{"refresh_token": _REFRESH, "client_id": "com.phan.caydex"}])
    assert await svc.revoke_for_account(sb, _USER_ID) == svc.NOT_CONFIGURED
    assert sb.log == []
    assert any(r.levelno == logging.WARNING and "revocation is OFF" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_revoke_happy_path_sends_the_stored_token_and_client(monkeypatch, configured, caplog):
    caplog.set_level(logging.DEBUG)
    calls = _install_transport(monkeypatch, lambda r: httpx.Response(200))
    sb = FakeSupabase(rows=[{"refresh_token": _REFRESH, "client_id": "com.phan.caydex"}])
    assert await svc.revoke_for_account(sb, _USER_ID) == svc.REVOKED
    assert ("eq", "apple_sign_in_grants", "user_id", _USER_ID) in sb.log
    form = _form(calls[0])
    assert form["token"] == _REFRESH and form["client_id"] == "com.phan.caydex"
    _no_secret_logged(caplog)


@pytest.mark.asyncio
async def test_revoke_reads_a_single_object_shape_too(monkeypatch, configured):
    _install_transport(monkeypatch, lambda r: httpx.Response(200))
    sb = FakeSupabase(rows={"refresh_token": _REFRESH, "client_id": "com.phan.caydex"})
    assert await svc.revoke_for_account(sb, _USER_ID) == svc.REVOKED


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"refresh_token": "", "client_id": "x"}], [{"client_id": "x"}],
                                  [None]])
async def test_revoke_without_a_stored_grant_calls_nobody(monkeypatch, configured, rows):
    calls = _install_transport(monkeypatch, lambda r: httpx.Response(200))
    assert await svc.revoke_for_account(FakeSupabase(rows=rows), _USER_ID) == svc.NO_GRANT
    assert calls == []


@pytest.mark.asyncio
async def test_revoke_read_failure_is_an_outcome(configured, caplog):
    caplog.set_level(logging.INFO)
    assert await svc.revoke_for_account(FakeSupabase(fail="down"), _USER_ID) == svc.READ_FAILED
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,outcome", [
    (lambda r: httpx.Response(400, json={"error": "invalid_grant"}), svc.REVOKE_REJECTED),
    (lambda r: httpx.Response(500), svc.REVOKE_UNAVAILABLE),
])
async def test_revoke_failures_are_outcomes_and_never_quote_the_token(
        monkeypatch, configured, caplog, handler, outcome):
    caplog.set_level(logging.DEBUG)
    _install_transport(monkeypatch, handler)
    sb = FakeSupabase(rows=[{"refresh_token": _REFRESH, "client_id": "com.phan.caydex"}])
    assert await svc.revoke_for_account(sb, _USER_ID) == outcome
    _no_secret_logged(caplog)


@pytest.mark.asyncio
async def test_a_slow_or_broken_revoke_cannot_hold_the_deletion(monkeypatch, configured):
    sb = FakeSupabase(rows=[{"refresh_token": _REFRESH, "client_id": "com.phan.caydex"}])

    async def _slow(*_a, **_k):
        await asyncio.sleep(5)
    monkeypatch.setattr(apple_id, "revoke_token", _slow)
    monkeypatch.setattr(svc, "REVOKE_TIMEOUT_SECONDS", 0.05)
    assert await svc.revoke_for_account(sb, _USER_ID) == svc.REVOKE_UNAVAILABLE

    async def _boom(*_a, **_k):
        raise KeyError("surprise")
    monkeypatch.setattr(apple_id, "revoke_token", _boom)
    assert await svc.revoke_for_account(sb, _USER_ID) == svc.REVOKE_UNAVAILABLE


# ── 3. Wiring: /auth/oauth and DELETE /users/me ───────────────────────────────

from app.api.v1.endpoints import auth as auth_ep  # noqa: E402
from app.api.v1.endpoints import users as users_ep  # noqa: E402
from app.core.security import rate_limiter  # noqa: E402
from app.schemas.auth import OAuthSignInRequest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    rate_limiter.clear()
    yield


class _Req:
    def __init__(self):
        self.client = type("C", (), {"host": "203.0.113.9"})()
        self.headers = {}


class _OAuthSupabase:
    """Just enough of the client for `oauth_sign_in`: the id-token grant and the users row."""

    def __init__(self, row_exists=True):
        self.row_exists = row_exists
        outer = self

        class _Auth:
            def sign_in_with_id_token(self, creds):
                user = type("U", (), {"id": _USER_ID, "email": "a@privaterelay.appleid.com"})()
                return type("R", (), {"user": user})()
        self.auth = _Auth()

        class _Q:
            def select(self, *_a, **_k): return self
            def eq(self, *_a, **_k): return self
            def limit(self, *_a, **_k): return self
            def single(self): return self
            def update(self, *_a, **_k): return self
            def execute(self):
                return type("R", (), {"data": [{"id": _USER_ID}] if outer.row_exists else []})()
        self._q = _Q

    def table(self, _name):
        return self._q()


@pytest.fixture
def grant_calls(monkeypatch):
    calls: list[dict] = []

    async def _store(supabase, **kwargs):
        calls.append(kwargs)
        return svc.EXCHANGE_UNAVAILABLE   # a failed exchange must not fail the sign-in
    monkeypatch.setattr(svc, "store_grant_from_authorization_code", _store)
    return calls


async def _oauth(provider="apple", code=_CODE, row_exists=True):
    return await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider=provider, id_token=_id_token(), nonce="n", authorization_code=code),
        _Req(), _OAuthSupabase(row_exists=row_exists),
    )


@pytest.mark.asyncio
async def test_oauth_exchanges_an_apple_code_and_still_signs_in_when_it_fails(grant_calls):
    out = await _oauth()
    assert out.access_token and out.user_id == _USER_ID
    assert grant_calls == [{"user_id": _USER_ID, "authorization_code": _CODE,
                            "identity_token": _id_token()}]


@pytest.mark.asyncio
async def test_oauth_never_exchanges_for_google_or_without_a_code(grant_calls):
    await _oauth(provider="google")
    await _oauth(code=None)
    assert grant_calls == []


@pytest.mark.asyncio
async def test_oauth_exchanges_only_after_the_app_row_check(grant_calls):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await _oauth(row_exists=False)
    assert ei.value.status_code == 503
    assert grant_calls == [], "a code must not be spent (or a grant stored) for a refused sign-in"


@pytest.mark.asyncio
async def test_oauth_survives_a_grant_step_that_raises(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("should never happen")
    monkeypatch.setattr(svc, "store_grant_from_authorization_code", _boom)
    out = await _oauth()
    assert out.access_token


class _DelSupabase:
    """`delete_account`'s fake: tables purge, storage is empty, the admin delete is recorded."""

    def __init__(self, log, fail_table=None):
        self.log, self._fail_table = log, fail_table
        log_ = log

        class _Bucket:
            def list(self, *_a, **_k): return []
            def remove(self, *_a, **_k): return None

        class _Storage:
            def from_(self, _b): return _Bucket()
        self.storage = _Storage()

        class _Admin:
            def delete_user(self, uid): log_.append(("auth.delete_user", uid))
        self.auth = type("A", (), {"admin": _Admin()})()

    def table(self, name):
        log, fail = self.log, self._fail_table

        class _Q:
            def delete(self): return self
            def eq(self, *_a): return self
            def execute(self):
                if name == fail:
                    raise RuntimeError("purge failed")
                log.append(("table.delete", name))
                return self
        return _Q()


@pytest.mark.asyncio
async def test_delete_revokes_after_the_purge_and_before_the_auth_row(monkeypatch):
    log: list[tuple] = []

    async def _revoke(supabase, user_id):
        log.append(("apple.revoke", user_id))
        return svc.REVOKED
    monkeypatch.setattr(svc, "revoke_for_account", _revoke)
    out = await users_ep.delete_account(user={"id": _USER_ID}, supabase=_DelSupabase(log))
    assert out == {"deleted": True}
    names = [e[0] for e in log]
    assert names.index("apple.revoke") < names.index("auth.delete_user")
    assert max(i for i, n in enumerate(names) if n == "table.delete") < names.index("apple.revoke")


@pytest.mark.asyncio
async def test_an_aborted_deletion_never_revokes(monkeypatch):
    """A purge failure keeps the account so the user can retry; revoking first would leave a
    live account whose Apple grant is already gone."""
    log: list[tuple] = []

    async def _revoke(supabase, user_id):
        log.append(("apple.revoke", user_id))
        return svc.REVOKED
    monkeypatch.setattr(svc, "revoke_for_account", _revoke)
    table = next(iter(users_ep._UNLINKED_USER_TABLES))
    if isinstance(table, tuple):
        table = table[0]
    res = await users_ep.delete_account(user={"id": _USER_ID}, supabase=_DelSupabase(log, fail_table=table))
    assert res.status_code == 500
    assert ("apple.revoke", _USER_ID) not in log and not [e for e in log if e[0] == "auth.delete_user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("behaviour", ["fails", "raises"])
async def test_a_failed_revoke_never_blocks_the_deletion(monkeypatch, behaviour):
    log: list[tuple] = []

    async def _revoke(supabase, user_id):
        if behaviour == "raises":
            raise RuntimeError("boom")
        return svc.REVOKE_UNAVAILABLE
    monkeypatch.setattr(svc, "revoke_for_account", _revoke)
    out = await users_ep.delete_account(user={"id": _USER_ID}, supabase=_DelSupabase(log))
    assert out == {"deleted": True}
    assert ("auth.delete_user", _USER_ID) in log


# ── 4. Schema + redaction ─────────────────────────────────────────────────────

def test_an_old_build_without_a_code_still_decodes():
    req = OAuthSignInRequest(provider="apple", id_token="x" * 40)
    assert req.authorization_code is None


@pytest.mark.parametrize("sent,stored", [
    ("A" * 200, "A" * 64),                  # truncated, never a 422 that loses the name
    ("  Ada Lovelace  ", "Ada Lovelace"),
    ("   ", None),
    (None, None),
])
def test_a_long_provider_name_is_truncated_not_refused(sent, stored):
    """A name over 64 characters used to 422 the WHOLE /auth/oauth (shipped builds send it as
    Apple / Google gave it), and Apple sends the name only once — so the user lost both the tap
    and the name. The column bound still holds."""
    assert OAuthSignInRequest(provider="google", id_token="x" * 40, display_name=sent).display_name == stored
    with pytest.raises(ValueError):
        OAuthSignInRequest(provider="google", id_token="x" * 40, display_name="x" * 513)


def test_the_code_is_bounded_and_kept_out_of_the_repr():
    req = OAuthSignInRequest(provider="apple", id_token="x" * 40, authorization_code=_CODE)
    assert _CODE not in repr(req)
    with pytest.raises(ValueError):
        OAuthSignInRequest(provider="apple", id_token="x" * 40, authorization_code="c" * 1025)


def test_the_code_never_reaches_sentry():
    from app.log_redaction import _is_secret_field_name, scrub_sentry_event

    assert _is_secret_field_name("authorization_code")
    assert not _is_secret_field_name("error_code") and not _is_secret_field_name("status_code")
    out = scrub_sentry_event({"request": {"data": {"provider": "apple", "authorization_code": _CODE}}})
    assert _CODE not in json.dumps(out) and out["request"]["data"]["provider"] == "apple"
    # A frame in either Apple module keeps no locals at all (code, refresh token, client
    # secret, the .p8 key). A neutral local NAME and a plain VALUE on purpose: a
    # credential-named local or a token-shaped value is redacted in any module, so only this
    # proves the whole frame is dropped — and the control proves an ordinary frame keeps it.
    plain = "plainvalue that only the module rule hides"

    def _frame(module, vars_):
        return {"exception": {"values": [{"value": "boom", "stacktrace": {"frames": [
            {"module": module, "function": "f", "vars": vars_}]}}]}}
    assert plain in json.dumps(scrub_sentry_event(_frame("app.services.other", {"row": plain}))), (
        "control: an ordinary frame keeps this local, or the check below proves nothing")
    for module in ("app.integrations.apple_id", "app.services.apple_sign_in_service"):
        assert plain not in json.dumps(scrub_sentry_event(_frame(module, {"row": plain}))), module
    # And in any other frame, a model repr's `authorization_code='…'` loses its value, while a
    # neighbouring ordinary field keeps its own.
    out = json.dumps(scrub_sentry_event(_frame("app.services.other", {
        "values": "{'x': Thing(provider='keepme', authorization_code='plaincodevalue')}"})))
    assert "plaincodevalue" not in out and "keepme" in out


def test_classify_exception_maps_apple_errors_to_auth_unavailable():
    from app.api.error_response import ErrorCode, classify_exception

    for exc in (apple_id.AppleIDUnavailableException("apple token revoke: Apple answered HTTP 503"),
                apple_id.AppleIDRejectedException("x HTTP 429", status=400, error="invalid_grant"),
                apple_id.AppleIDNotConfiguredException("timed out")):
        code, status = classify_exception(exc)
        assert code == ErrorCode.AUTH_UNAVAILABLE and status == 503, type(exc).__name__
