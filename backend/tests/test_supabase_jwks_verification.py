"""Supabase JWT verification: asymmetric (JWKS) only, every claim required.

History, because each rule below closed a real hole:

* 2026-08-15 — the verifier was hardcoded to `algorithms=["HS256"]` against
  `SUPABASE_JWT_SECRET`. Revoking the legacy key would have broken the Supabase session-exchange
  route (`auth.py`), so it learned ES256/RS256 via the project JWKS and the legacy key was then
  REVOKED at Supabase.
* 2026-11-26 (SOC-01) — the HS256 branch stayed, and python-jose neither requires `exp` nor checks
  `aud` when the claim is absent. So `{"sub": "<victim uuid>"}` signed with the revoked-but-not-
  rotatable legacy secret (Supabase offers only "Reveal" for it; it sits in Railway and every
  developer .env) was accepted forever: on session-exchange, and as a bearer on every route.
  HS256 is now refused outright, and `exp`, `aud`, `iss` and `sub` are REQUIRED.
* 2026-11-26 (SOC-02) — a header claiming RS256 against the project's EC `kid` made python-jose
  raise `JWKError`, a `JOSEError` that is NOT a `JWTError`, so it escaped as a 500 with an ERROR +
  Sentry event per unauthenticated POST. The key type now selects the algorithm before decoding,
  and every JOSE / unexpected error is a clean None.

No network: the JWKS cache is seeded directly.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path

import pytest
from jose import jwk as jose_jwk
from jose import jwt
from jose.exceptions import JWKError

from app.config import settings
from app.core import security


# An ES256 keypair generated once for these tests. NOT a real key of any project.
_PRIVATE_PEM = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgevZzL1gdAFr88hb2
OF/2NxApJCzGCEDdfSp6VQO30hyhRANCAAQRWz+jn65BtOMvdyHKcvjBeBSDZH2r
1RTwjmYSi9R/zpBnuQ4EiMnCqfMPWiZqB4QdbAd0E7oH50VpuZ1P087G
-----END PRIVATE KEY-----"""

# The matching public JWK, as Supabase would publish it.
_PUBLIC_JWK = {
    "kty": "EC",
    "crv": "P-256",
    "x": "EVs_o5-uQbTjL3chynL4wXgUg2R9q9UU8I5mEovUf84",
    "y": "kGe5DgSIycKp8w9aJmoHhB1sB3QTugfnRWm5nU_TzsY",
    "alg": "ES256",
    "use": "sig",
    "kid": "test-standby-kid",
}

_LEGACY_SECRET = "legacy-shared-secret-for-tests-only-not-real"
_SUPABASE_URL = "https://testproject.supabase.co"
_ISSUER = f"{_SUPABASE_URL}/auth/v1"
_SUB = "11112222-3333-4444-8555-666677778888"


def _claims(**over):
    now = int(time.time())
    base = {
        "sub": _SUB,
        "aud": "authenticated",
        "iss": _ISSUER,
        "email": "user@example.com",
        "role": "authenticated",
        "is_anonymous": False,
        "iat": now,
        "exp": now + 3600,
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not _DROP}


_DROP = object()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    security._reset_jwks_cache_for_tests()
    # The legacy secret stays SET in most tests on purpose: the point is that it no longer
    # matters. A test that it is refused with the secret configured proves more than one that
    # it is refused with nothing to verify against.
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", _LEGACY_SECRET)
    monkeypatch.setattr(settings, "SUPABASE_URL", _SUPABASE_URL)

    async def _no_network():
        return {}  # hermetic: an unseeded kid behaves like a JWKS outage

    monkeypatch.setattr(security, "_fetch_jwks", _no_network)
    yield
    security._reset_jwks_cache_for_tests()


def _seed_jwks(*jwks):
    security._JWKS_CACHE["keys"] = {k["kid"]: k for k in jwks}
    security._JWKS_CACHE["fetched_at"] = time.monotonic()


def _es256(headers=None, **over):
    return jwt.encode(_claims(**over), _PRIVATE_PEM, algorithm="ES256",
                      headers={"kid": _PUBLIC_JWK["kid"], **(headers or {})})


def _hs256(claims=None, headers=None, **over):
    return jwt.encode(claims if claims is not None else _claims(**over), _LEGACY_SECRET,
                      algorithm="HS256", headers=headers)


def _b64(obj) -> str:
    raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _with_header(token: str, header) -> str:
    """The same payload and signature under a different (unsigned-over) header."""
    _, payload, sig = token.split(".")
    return f"{_b64(header)}.{payload}.{sig}"


def _function_source(name: str) -> str:
    import ast

    src = Path(security.__file__).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found in core/security.py")


def _rsa_keypair(kid="test-rsa-kid"):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public = jose_jwk.construct(pub_pem, "RS256").to_dict()
    public = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in public.items()}
    public.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return pem, public


# ── the good path: ES256 (current) and RS256 (a promotable standby) ───────────

@pytest.mark.asyncio
async def test_an_es256_token_verifies_against_the_jwks_key():
    """The live case. Without this, Google/Apple web sign-in (session-exchange) is broken."""
    _seed_jwks(_PUBLIC_JWK)
    payload = await security.verify_supabase_token(_es256())
    assert payload is not None, "ES256 token rejected — session-exchange would be broken"
    assert payload["sub"] == _SUB
    # The whole payload comes back: session-exchange reads `iat` (password-change eviction) and
    # `is_anonymous` (anonymous sessions are refused) from it.
    assert isinstance(payload["iat"], int) and abs(payload["iat"] - int(time.time())) < 60
    assert payload["is_anonymous"] is False


@pytest.mark.asyncio
async def test_an_rs256_token_verifies_against_an_rsa_jwk():
    """Supabase can promote an RSA standby key; RS256 must not be hard-coded away."""
    pem, public = _rsa_keypair()
    _seed_jwks(_PUBLIC_JWK, public)
    token = jwt.encode(_claims(), pem, algorithm="RS256", headers={"kid": public["kid"]})
    payload = await security.verify_supabase_token(token)
    assert payload is not None and payload["sub"] == _SUB


@pytest.mark.asyncio
async def test_a_trailing_slash_on_supabase_url_does_not_break_the_issuer_check(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", _SUPABASE_URL + "/")
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256()) is not None


def test_the_issuer_and_the_jwks_url_come_from_one_base(monkeypatch):
    """GoTrue stamps `iss` with the URL it serves its JWKS from; derive both from one place so
    a custom-domain move changes them together."""
    monkeypatch.setattr(settings, "SUPABASE_URL", _SUPABASE_URL + "/")
    assert security._supabase_auth_base() == _ISSUER
    # Read the FILE: the autouse fixture has replaced `security._fetch_jwks` with a stub.
    assert "_supabase_auth_base()" in _function_source("_fetch_jwks")
    assert "_supabase_auth_base()" in _function_source("verify_supabase_token")


# ── SOC-01: the legacy HS256 master key ──────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"sub": _SUB},  # THE forgery: no exp, no aud — accepted before 2026-11-26
        None,           # a fully-formed HS256 token with every claim right
    ],
    ids=["bare-sub-no-exp-no-aud", "well-formed"],
)
async def test_an_hs256_token_signed_with_the_legacy_secret_is_refused(claims):
    """The revoked legacy key can still be revealed at Supabase and sits in Railway and every
    developer .env. Anything it signs must be worthless here — with the secret CONFIGURED."""
    _seed_jwks(_PUBLIC_JWK)
    assert settings.SUPABASE_JWT_SECRET == _LEGACY_SECRET
    token = _hs256(claims=claims) if claims is not None else _hs256()
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
async def test_an_hs256_token_naming_a_real_kid_is_refused():
    """A `kid` must not route an HS256 token to any key at all."""
    _seed_jwks(_PUBLIC_JWK)
    token = _hs256(headers={"kid": _PUBLIC_JWK["kid"]})
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
async def test_hs256_is_refused_when_no_legacy_secret_is_configured(monkeypatch):
    """After the owner deletes the variable, nothing changes either."""
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_hs256()) is None


@pytest.mark.asyncio
async def test_a_jwks_outage_refuses_every_token_without_raising(monkeypatch):
    """No legacy fallback: with no key available nothing verifies — HS256 included — and the
    verifier still answers None rather than raising."""
    async def _down():
        return {}

    monkeypatch.setattr(security, "_fetch_jwks", _down)
    assert await security.verify_supabase_token(_es256()) is None
    assert await security.verify_supabase_token(_hs256()) is None


def _verifier_code() -> str:
    """`verify_supabase_token`'s body, docstring and comments stripped (they name the very
    patterns these guards forbid, in order to explain them)."""
    src = _function_source("verify_supabase_token")
    body = src.split('"""', 2)[-1]
    return "\n".join(
        line.split("#", 1)[0] for line in body.splitlines()
    )


def test_the_verifier_pins_one_algorithm_per_decode_and_never_hs256():
    """Algorithm confusion and the legacy key, pinned at the SOURCE — deliberately.

    python-jose refuses an asymmetric key as an HMAC secret on its own ("Incorrect key type.
    Expected: 'oct', Received: EC"), so a permissive verifier and a correct one behave
    IDENTICALLY under a behavioural forgery test — any such assertion is vacuous (the first
    attempt was; mutation testing caught it). The shape is what we control:
      * exactly ONE decode call, with `algorithms=[alg]` (never a union);
      * no HS256 anywhere in the code path, and no read of `SUPABASE_JWT_SECRET`;
      * the key type picks the algorithm (`_ALG_FOR_KTY`).
    """
    code = _verifier_code()
    assert code.count("jwt.decode(") == 1, "one decode per token, against one key"
    assert "algorithms=[alg]" in code, "the decode no longer pins the single checked algorithm"
    union = re.search(r"algorithms=\[[^\]]*,[^\]]*\]", code)
    assert union is None, (
        f"verify_supabase_token passes a UNION of algorithms ({union.group(0) if union else ''})"
    )
    assert not re.search(r"""['"]HS256['"]""", code), "an HS256 path is back in the Supabase verifier"
    assert "SUPABASE_JWT_SECRET" not in code, "the verifier reads the revoked legacy secret again"
    assert "_ALG_FOR_KTY" in code
    assert security._ALG_FOR_KTY == {"EC": "ES256", "RSA": "RS256"}


def test_every_claim_is_required_not_merely_checked_when_present():
    """python-jose skips `aud` / `iss` / `exp` checks when the claim is ABSENT unless asked to
    require them — exactly how a bare `{"sub": ...}` token used to pass."""
    code = _verifier_code()
    for option in ("require_exp", "require_aud", "require_iss", "require_sub"):
        assert re.search(rf'"{option}":\s*True', code), f"{option} is not set"
    assert "audience=" in code and "issuer=" in code


def test_nothing_in_the_backend_reads_the_legacy_jwt_secret():
    """`SUPABASE_JWT_SECRET` stays DECLARED on Settings (settings parity, and the owner deletes it
    from Railway after deploy) but no code may read it — a reader is how the master key, or the
    503-on-every-expired-token branch it used to gate, would come back."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    readers = []
    for path in app_dir.rglob("*.py"):
        if path.name == "config.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\.SUPABASE_JWT_SECRET\b|['\"]SUPABASE_JWT_SECRET['\"]\s*\)", code):
                readers.append(f"{path.relative_to(app_dir)}:{lineno}")
    assert readers == [], f"code reads SUPABASE_JWT_SECRET again: {readers}"


# ── required claims (behaviour) ──────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["exp", "aud", "iss", "sub"])
async def test_a_token_missing_a_required_claim_is_refused(claim):
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256(**{claim: _DROP})) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "iss",
    [
        "https://attacker.example/auth/v1",
        _SUPABASE_URL,                       # right host, wrong path
        "https://otherproject.supabase.co/auth/v1",  # a DIFFERENT Supabase project's token
        _ISSUER + "/",
    ],
    ids=["foreign-host", "no-auth-path", "other-project", "trailing-slash"],
)
async def test_a_token_from_another_issuer_is_refused(iss):
    """Signed by a key we trust is not enough: it must have been minted by THIS project's Auth."""
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256(iss=iss)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("sub", ["", "   ", 12345, None], ids=["empty", "blank", "int", "null"])
async def test_a_token_without_a_usable_sub_is_refused(sub):
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256(sub=sub)) is None


@pytest.mark.asyncio
async def test_an_expired_token_is_refused():
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256(exp=int(time.time()) - 10)) is None


@pytest.mark.asyncio
async def test_the_wrong_audience_is_refused():
    """A Supabase token minted for another audience is not a session for this app."""
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256(aud="some-other-service")) is None


# ── SOC-02: a mismatched header is a clean None, never a 500 ─────────────────

@pytest.mark.asyncio
async def test_rs256_claimed_against_the_projects_ec_key_is_refused_without_raising(caplog):
    """THE reported shape: the public `kid` from the JWKS, `alg` rewritten to RS256. python-jose
    raises `JWKError` (a JOSEError, not a JWTError) on it, which used to escape session-exchange
    as an off-contract 500 with an ERROR + Sentry event per call."""
    _seed_jwks(_PUBLIC_JWK)
    token = _with_header(_es256(), {"alg": "RS256", "typ": "JWT", "kid": _PUBLIC_JWK["kid"]})
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        assert await security.verify_supabase_token(token) is None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "a hostile header must not produce an ERROR (each one is a Sentry event)"
    )


@pytest.mark.asyncio
async def test_es256_claimed_against_an_rsa_key_is_refused():
    _, public = _rsa_keypair(kid="rsa-standby")
    _seed_jwks(public)
    token = _with_header(_es256(), {"alg": "ES256", "typ": "JWT", "kid": "rsa-standby"})
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "jwk_over",
    [{"alg": "RS256"}, {"use": "enc"}, {"kty": "oct"}, {"kty": None}],
    ids=["jwk-alg-mismatch", "encryption-key", "symmetric-kty", "no-kty"],
)
async def test_a_jwk_that_is_not_an_es256_signing_key_is_not_used(jwk_over):
    _seed_jwks(dict(_PUBLIC_JWK, **jwk_over))
    assert await security.verify_supabase_token(_es256()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [JWKError("bad key"), RuntimeError("library bug"), ValueError("odd")],
    ids=["jose-jwk-error", "runtime-error", "value-error"],
)
async def test_any_decode_failure_is_none_never_a_raise(monkeypatch, exc):
    """Defence in depth behind the kty check: whatever python-jose raises, the caller (an
    unauthenticated route) gets None."""
    _seed_jwks(_PUBLIC_JWK)

    def _boom(*a, **k):
        raise exc

    monkeypatch.setattr(security.jwt, "decode", _boom)
    assert await security.verify_supabase_token(_es256()) is None


@pytest.mark.asyncio
async def test_an_unexpected_header_parse_error_is_none(monkeypatch):
    def _boom(_token):
        raise TypeError("unexpected")

    monkeypatch.setattr(security.jwt, "get_unverified_header", _boom)
    assert await security.verify_supabase_token(_es256()) is None


# ── malformed / hostile input ─────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b", "a.b.c", "..", "null"])
async def test_garbage_is_refused_without_raising(bad):
    """These reach the verifier straight off the wire; a raise here would be a 500 on a public
    auth route rather than a clean rejection."""
    assert await security.verify_supabase_token(bad) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, 123, b"a.b.c", ["a"], {"t": 1}],
                         ids=["none", "int", "bytes", "list", "dict"])
async def test_a_non_string_token_is_refused_without_raising(bad):
    assert await security.verify_supabase_token(bad) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none", "typ": "JWT"},
        {"alg": "ES256", "kid": 7},
        {"alg": "ES256", "kid": ["test-standby-kid"]},
        {"alg": "ES256", "kid": "k" * 129},
        {"alg": "ES256", "kid": ""},
        {"alg": ["ES256"], "kid": "test-standby-kid"},
        {"alg": None, "kid": "test-standby-kid"},
        ["ES256"],
        "ES256",
    ],
    ids=["alg-none", "kid-int", "kid-list", "kid-too-long", "kid-empty",
         "alg-list", "alg-null", "header-array", "header-string"],
)
async def test_hostile_headers_are_refused_without_raising(header):
    """`kid` is attacker-chosen and used as a cache key: an unhashable one must not raise
    TypeError inside the JWKS lookup, and an absurd one must not be looked up at all."""
    _seed_jwks(_PUBLIC_JWK)
    token = _with_header(_es256(), header)
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
async def test_the_none_algorithm_is_refused():
    """`alg: none` is the oldest JWT bypass there is."""
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims())}."
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
async def test_an_es256_token_with_no_kid_is_refused():
    """Without a kid there is no non-guessing way to pick a key; guessing is how you end up
    accepting a key the project has revoked."""
    _seed_jwks(_PUBLIC_JWK)
    token = jwt.encode(_claims(), _PRIVATE_PEM, algorithm="ES256")
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
async def test_an_es256_token_signed_by_an_unknown_key_is_refused(monkeypatch):
    """A revoked/foreign key must not verify just because the token names a kid."""
    _seed_jwks(dict(_PUBLIC_JWK, kid="some-other-kid"))
    # Only one refetch is attempted, and the network call is stubbed to return nothing.
    async def _no_keys():
        return {}
    # `monkeypatch`, not a bare assignment: this used to be `security._fetch_jwks = _no_keys`
    # with no restore, so the stub outlived the test and every later test in the session saw
    # an empty JWKS. It went unnoticed because tests/test_patch_targets_exist.py could not
    # see `from app.core import security` bindings until 2026-09-10.
    monkeypatch.setattr(security, "_fetch_jwks", _no_keys)
    assert await security.verify_supabase_token(_es256()) is None


@pytest.mark.asyncio
async def test_a_token_signed_by_a_different_ec_key_under_our_kid_is_refused():
    """The signature, not the `kid`, is what proves the issuer."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    _seed_jwks(_PUBLIC_JWK)
    token = jwt.encode(_claims(), other, algorithm="ES256", headers={"kid": _PUBLIC_JWK["kid"]})
    assert await security.verify_supabase_token(token) is None
