"""Supabase JWT verification across the legacy-secret → JWKS migration.

Supabase is moving projects off a single shared HS256 secret onto asymmetric signing keys
(ES256) published at `/auth/v1/.well-known/jwks.json`. The dashboard shows a CURRENT key and a
STANDBY key; "Rotate keys" promotes the standby, and the old key stays valid for verification
until it is explicitly REVOKED.

Why this file exists: the verifier was hardcoded to `algorithms=["HS256"]` against
`SUPABASE_JWT_SECRET`. Revoking the legacy key would therefore have broken the Supabase
session-exchange route (`auth.py`), i.e. Google/Apple web sign-in — and the failure would only
have shown up *after* the irreversible dashboard step.

No network: the JWKS cache is seeded directly.
"""

from __future__ import annotations

import time

import pytest
from jose import jwt

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


def _claims(**over):
    now = int(time.time())
    base = {
        "sub": "11112222-3333-4444-8555-666677778888",
        "aud": "authenticated",
        "email": "user@example.com",
        "iat": now,
        "exp": now + 3600,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    security._reset_jwks_cache_for_tests()
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", _LEGACY_SECRET)
    yield
    security._reset_jwks_cache_for_tests()


def _seed_jwks(*jwks):
    security._JWKS_CACHE["keys"] = {k["kid"]: k for k in jwks}
    security._JWKS_CACHE["fetched_at"] = time.monotonic()


def _es256(**over):
    return jwt.encode(_claims(**over), _PRIVATE_PEM, algorithm="ES256",
                      headers={"kid": _PUBLIC_JWK["kid"]})


def _hs256(**over):
    return jwt.encode(_claims(**over), _LEGACY_SECRET, algorithm="HS256")


# ── both signing schemes must verify during the migration ─────────────────────

@pytest.mark.asyncio
async def test_an_es256_token_verifies_against_the_jwks_key():
    """The post-rotation case. Without this, promoting the standby key breaks sign-in."""
    _seed_jwks(_PUBLIC_JWK)
    payload = await security.verify_supabase_token(_es256())
    assert payload is not None, "ES256 token rejected — Google/Apple sign-in would be broken"
    assert payload["sub"] == _claims()["sub"]


@pytest.mark.asyncio
async def test_a_legacy_hs256_token_still_verifies():
    """The pre-rotation case must keep working, or deploying this change is itself an outage."""
    _seed_jwks(_PUBLIC_JWK)
    payload = await security.verify_supabase_token(_hs256())
    assert payload is not None and payload["sub"] == _claims()["sub"]


@pytest.mark.asyncio
async def test_hs256_still_verifies_when_no_jwks_key_is_available():
    """A JWKS fetch failure must degrade to the legacy path, not lock everyone out."""
    security._reset_jwks_cache_for_tests()
    assert await security.verify_supabase_token(_hs256()) is not None


# ── the attack this split exists to prevent ───────────────────────────────────

def test_each_branch_pins_exactly_one_algorithm():
    """Algorithm confusion, pinned at the SOURCE — deliberately, and here is why.

    The attack: the ES256 public key is public by definition, so if the verifier passed
    `algorithms=["ES256","HS256"]` to one `jwt.decode` with the JWK as the key, an attacker
    could re-sign chosen claims as HS256 using that public key material as the HMAC secret and
    be admitted as any user.

    A behavioural test cannot demonstrate it here. Measured against this pinned python-jose:
    forging is refused at `jwt.encode` ("The specified key is an asymmetric key ... should not
    be used as an HMAC secret"), and verifying is refused at `jwt.decode` ("Incorrect key type.
    Expected: 'oct', Received: EC"). So a permissive verifier and a correct one behave
    IDENTICALLY under test, and any behavioural assertion would pass whether or not the split
    existed — i.e. be vacuous. (It was, on the first attempt; mutation testing caught it.)

    What we actually control is the shape, so that is what is asserted. This stays honest if
    the library is ever swapped for one without those internal guards.
    """
    import inspect
    import re

    src = inspect.getsource(security.verify_supabase_token)
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )
    # Split the docstring off — it names the forbidden pattern in order to explain it.
    body = code.split('"""', 2)[-1]

    assert "algorithms=[alg]" in body, "the asymmetric branch no longer pins a single algorithm"
    assert 'algorithms=["HS256"]' in body, "the legacy branch no longer pins HS256 alone"
    union = re.search(r"algorithms=\[[^\]]*,[^\]]*\]", body)
    assert union is None, (
        f"verify_supabase_token passes a UNION of algorithms ({union.group(0) if union else ''}) "
        "to one decode call — the algorithm must select the key, never be negotiable against it"
    )


@pytest.mark.asyncio
async def test_an_hs256_token_is_never_checked_against_a_jwks_key():
    """The behavioural half that IS observable: with JWKS keys loaded but no legacy secret,
    an HS256 token must fail rather than fall through to the asymmetric key."""
    _seed_jwks(_PUBLIC_JWK)
    security_secret = settings.SUPABASE_JWT_SECRET
    try:
        settings.SUPABASE_JWT_SECRET = None
        assert await security.verify_supabase_token(_hs256()) is None
    finally:
        settings.SUPABASE_JWT_SECRET = security_secret


@pytest.mark.asyncio
async def test_an_es256_token_signed_by_an_unknown_key_is_refused():
    """A revoked/foreign key must not verify just because the token names a kid."""
    _seed_jwks(dict(_PUBLIC_JWK, kid="some-other-kid"))
    # Only one refetch is attempted, and the network call is stubbed to return nothing.
    async def _no_keys():
        return {}
    security._fetch_jwks = _no_keys  # type: ignore[assignment]
    assert await security.verify_supabase_token(_es256()) is None


# ── malformed / hostile input ─────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b", "a.b.c", "..", "null"])
async def test_garbage_is_refused_without_raising(bad):
    """These reach the verifier straight off the wire; a raise here would be a 500 on a public
    auth route rather than a clean rejection."""
    assert await security.verify_supabase_token(bad) is None


@pytest.mark.asyncio
async def test_the_none_algorithm_is_refused():
    """`alg: none` is the oldest JWT bypass there is."""
    import base64, json
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(_claims())}."
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
async def test_an_expired_token_is_refused():
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256(exp=int(time.time()) - 10)) is None


@pytest.mark.asyncio
async def test_the_wrong_audience_is_refused():
    """A Supabase token minted for another audience is not a session for this app."""
    _seed_jwks(_PUBLIC_JWK)
    assert await security.verify_supabase_token(_es256(aud="some-other-service")) is None


@pytest.mark.asyncio
async def test_an_es256_token_with_no_kid_is_refused():
    """Without a kid there is no non-guessing way to pick a key; guessing is how you end up
    accepting a key the project has revoked."""
    _seed_jwks(_PUBLIC_JWK)
    token = jwt.encode(_claims(), _PRIVATE_PEM, algorithm="ES256")
    assert await security.verify_supabase_token(token) is None


@pytest.mark.asyncio
async def test_hs256_is_refused_when_no_legacy_secret_is_configured(monkeypatch):
    """After the legacy key is revoked and the env var removed, an HS256 token must fail
    closed rather than fall through to some other key."""
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)
    assert await security.verify_supabase_token(_hs256()) is None
