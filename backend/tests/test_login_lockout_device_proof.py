"""The per-email credential limits cannot be turned into a lockout of a known address.

Sign-in, forgot-password and reset-password are limited PER EMAIL as well as per IP — the
per-email bucket is the brute-force control an address pool cannot get around. Its cost was
a trivial denial of service: anyone who knew victim@example.com could post ten wrong
passwords every fifteen minutes (inside their own per-IP budget) and the victim's correct
password answered 429 from EVERY device for as long as the loop ran; three forgot-password
posts an hour blocked the reset email too.

The exemption is a PROOF the victim's own devices carry and the attacker cannot mint: an
HMAC over (email, issue time) signed with `SECRET_KEY`, handed out only by a flow that
verified a credential and presented back as `X-Device-Token`. A request bearing a valid
proof for the address it signs in as is judged on a per-proof bucket instead of the
per-email one. Everyone still passes the per-IP bucket.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import auth as auth_ep
from app.core.security import (
    DEVICE_PROOF_TTL_DAYS, create_device_proof, rate_limiter, verify_device_proof,
)
from app.schemas.auth import ForgotPasswordRequest, SignInRequest

from tests.test_auth_confirmation_and_oauth import (
    _EMAIL, _PASSWORD, FakeSupabase, _FakeRequest,
)


@pytest.fixture(autouse=True)
def _clear():
    rate_limiter.clear()
    yield
    rate_limiter.clear()


class _Req(_FakeRequest):
    """A request carrying an optional `X-Device-Token`, from a chosen edge-appended IP."""

    def __init__(self, ip="203.0.113.44", proof=None):
        super().__init__(ip)
        self.headers = {"x-forwarded-for": f"198.51.100.1, {ip}"}
        if proof is not None:
            self.headers["x-device-token"] = proof


# ── the primitive ─────────────────────────────────────────────────────────────


def test_a_proof_verifies_only_for_its_own_address_and_only_while_fresh():
    proof = create_device_proof("Victim@Example.com")
    assert verify_device_proof(proof, "victim@example.com")
    assert verify_device_proof(proof, "  VICTIM@example.com ")
    assert not verify_device_proof(proof, "attacker@example.com")
    assert not verify_device_proof(proof[:-1] + ("0" if proof[-1] != "0" else "1"), "victim@example.com")
    # Expiry and a proof "from the future" (clock skew past the issue time) both fail.
    old = create_device_proof("victim@example.com",
                              now=datetime.now(timezone.utc) - timedelta(days=DEVICE_PROOF_TTL_DAYS + 1))
    assert not verify_device_proof(old, "victim@example.com")
    future = create_device_proof("victim@example.com", now=datetime.now(timezone.utc) + timedelta(days=2))
    assert not verify_device_proof(future, "victim@example.com")
    for junk in (None, "", "v1", "v1.x.y.z", "v2." + proof[3:], "a" * 400, proof + ".extra"):
        assert not verify_device_proof(junk, "victim@example.com"), junk


def test_the_proof_carries_no_identity_and_is_not_a_bearer():
    """It is an exemption, not a session: no user id, no email in the clear."""
    proof = create_device_proof(_EMAIL)
    assert _EMAIL not in proof and _EMAIL.split("@")[0] not in proof
    assert proof.count(".") == 3 and len(proof) < 160


# ── sign-in ───────────────────────────────────────────────────────────────────


async def _fail_login(ip: str, proof=None):
    with pytest.raises(HTTPException) as exc:
        await auth_ep.sign_in(SignInRequest(email=_EMAIL, password=_PASSWORD),
                              _Req(ip, proof), FakeSupabase(fail=("signin",)))
    return exc.value.status_code


@pytest.mark.asyncio
async def test_a_verified_sign_in_hands_out_a_proof_for_that_address():
    resp = await auth_ep.sign_in(SignInRequest(email=_EMAIL, password=_PASSWORD),
                                 _Req(), FakeSupabase())
    assert resp.device_token and verify_device_proof(resp.device_token, _EMAIL)
    assert not verify_device_proof(resp.device_token, "someone.else@example.com")


@pytest.mark.asyncio
async def test_an_attacker_cannot_lock_the_owner_out_of_a_known_address():
    # Ten wrong passwords from the attacker's address fill victim's per-email bucket.
    for _ in range(10):
        assert await _fail_login("198.51.100.200") == 401
    # A stranger (no proof) from a fresh address: the per-email lock holds — this IS the
    # brute-force control, and it must keep working.
    assert await _fail_login("198.51.100.201") == 429
    # The OWNER's own phone, carrying the proof a previous sign-in minted, gets in.
    proof = create_device_proof(_EMAIL)
    resp = await auth_ep.sign_in(SignInRequest(email=_EMAIL, password=_PASSWORD),
                                 _Req("203.0.113.9", proof), FakeSupabase())
    assert resp.user_id, "the proof-bearing owner was locked out with the attacker"


@pytest.mark.asyncio
async def test_a_proof_for_another_address_or_a_forged_one_earns_nothing():
    for _ in range(10):
        await _fail_login("198.51.100.200")
    foreign = create_device_proof("someone.else@example.com")
    assert await _fail_login("203.0.113.9", foreign) == 429
    forged = create_device_proof(_EMAIL)
    forged = forged[:-4] + ("0000" if not forged.endswith("0000") else "1111")
    assert await _fail_login("203.0.113.9", forged) == 429


@pytest.mark.asyncio
async def test_a_proof_holder_is_still_bounded_per_proof_and_per_ip():
    """The exemption relaxes ONE bucket; it is not unlimited guessing with a stolen proof.

    Each half isolates ITS bucket (W2 vacuity-2-1: both halves used to send 11 failures from
    one IP, so the per-IP bucket — also 10/min — fired regardless of whether the per-proof
    bucket existed, and a refactor that dropped the proof axis stayed green).
    """
    # Per-PROOF: one stolen proof rotated across 11 distinct IPs (so neither the per-IP
    # nor the per-email bucket can be what fires) is still capped at 10.
    proof = create_device_proof(_EMAIL)
    codes = [await _fail_login(f"203.0.113.{i}", proof) for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429, codes
    assert any(k.startswith("login:device:") for k in rate_limiter._protected), (
        "the 429 must come from the per-proof bucket"
    )
    # ...and it WAS the proof bucket: a different valid proof for the same address, from
    # a fresh IP, is judged on its own bucket and gets through.
    other = create_device_proof(_EMAIL, now=datetime.now(timezone.utc) - timedelta(seconds=5))
    assert other != proof
    assert await _fail_login("203.0.113.99", other) == 401
    # Per-IP: one IP rotating proofs (each minted at a distinct instant, so each lands in
    # its own per-proof bucket) is still capped by the IP bucket.
    rate_limiter.clear()
    codes = []
    for i in range(11):
        rotating = create_device_proof(_EMAIL, now=datetime.now(timezone.utc) - timedelta(seconds=i + 1))
        codes.append(await _fail_login("203.0.113.10", rotating))
    assert codes[:10] == [401] * 10 and codes[10] == 429, codes


# ── forgot-password ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgot_password_honours_the_same_exemption():
    for _ in range(3):
        await auth_ep.forgot_password(ForgotPasswordRequest(email=_EMAIL), _Req("198.51.100.200"),
                                      FakeSupabase())
    with pytest.raises(HTTPException) as exc:
        await auth_ep.forgot_password(ForgotPasswordRequest(email=_EMAIL), _Req("198.51.100.201"),
                                      FakeSupabase())
    assert exc.value.status_code == 429
    # The owner's device still gets its reset email.
    out = await auth_ep.forgot_password(ForgotPasswordRequest(email=_EMAIL),
                                        _Req("203.0.113.9", create_device_proof(_EMAIL)),
                                        FakeSupabase())
    assert out is not None


# ── the iOS half ──────────────────────────────────────────────────────────────


def test_ios_stores_the_proof_after_every_verified_sign_in_and_sends_it_on_the_three_routes():
    """Source pin (comment-stripped, brace-bound): the proof is remembered by the three
    credential-verifying flows, keyed per address, never cleared on sign-out, and attached
    exactly on the three per-email-limited routes."""
    import re
    from pathlib import Path

    ios = Path(__file__).resolve().parents[2] / "frontend/ios/ios"

    def code(rel):
        src = (ios / rel).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())

    def body(src, opener):
        i = src.index(opener); j = src.index("{", i); depth = 0
        for k in range(j, len(src)):
            if src[k] == "{": depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0: return src[j + 1:k]
        raise AssertionError(opener)

    auth = code("Core/Services/AuthService.swift")
    assert 'case deviceToken = "device_token"' in body(auth, "struct AuthResponse")
    assert "let deviceToken: String?" in body(auth, "struct AuthResponse")
    assert "DeviceProofStore.remember(response.deviceToken, for: email)" in \
        body(auth, "func signIn(email: String, password: String)")
    assert "DeviceProofStore.remember(response.deviceToken, for: profile.email)" in \
        body(auth, "func signInWithProvider(")
    assert "DeviceProofStore.remember(response.deviceToken, for: profile.email)" in \
        body(auth, "func exchangeSupabaseSession(")
    # Sign-out clears the SESSION tokens only — the proof must survive it.
    assert "DeviceProofStore" not in body(auth, "func clearToken()")

    store = code("Core/Services/DeviceProofStore.swift")
    assert "KeychainService.shared" in store and "kSecAttrAccessibleAfterFirstUnlock" not in store
    assert "static func proof(for email: String) -> String?" in store

    ep = code("Core/Services/APIEndpoint.swift")
    arm = body(ep, "nonisolated var deviceProofEmail: String?")
    assert re.search(r"case \.signIn\(let email, _\), \.forgotPassword\(let email\), \.resetPassword\(let email, _, _\):", arm)
    assert "default:" in arm and "return nil" in arm

    client = code("Core/Services/APIClient.swift")
    assert 'forHTTPHeaderField: "X-Device-Token"' in client
    assert "endpoint.deviceProofEmail" in client and "DeviceProofStore.proof(for: email)" in client
