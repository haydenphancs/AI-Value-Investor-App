"""App Review buys in SANDBOX against the build submitted for PRODUCTION.

Apple's `SignedDataVerifier` is constructed for exactly one environment and hard-rejects a
payload whose `environment` field differs:

    if decoded_transaction_info.environment != self._environment:
        raise VerificationException(VerificationStatus.INVALID_ENVIRONMENT)

So a server running the launch setting `IAP_ENVIRONMENT=Production` used to answer HTTP 400 to
every purchase a reviewer made, and the submission would be rejected for "in-app purchase
doesn't work". It never reproduces for the developer, because their own sandbox testing runs
against a backend set to Sandbox — the mismatch only exists in the one configuration nobody
tests: production server, sandbox purchase.

The fix accepts the sibling environment, and these tests pin the two halves of it:

* it ACCEPTS a correctly-signed payload from the sibling environment, and
* it still REJECTS everything else — bad signature, untrusted chain, wrong bundle id — and
  never cross-accepts a LOCAL environment, where signature verification is skipped entirely.

The second half is the one that matters. A fallback that retried on any failure would turn
"try the other environment" into "try until something says yes".

No network, no Apple, no Supabase — the verifiers are stubbed at the library boundary.
"""
from __future__ import annotations

import pytest

from appstoreserverlibrary.signed_data_verifier import (
    VerificationException,
    VerificationStatus,
)

from app.integrations import app_store


_TXN = {"transactionId": "2000000000000001", "productId": "com.phan.caydex.pro.monthly"}


class _StubVerifier:
    """Models the real library: chain/signature first, then bundle id, then environment."""

    def __init__(self, environment: str, calls: list):
        self._environment = environment
        self._calls = calls

    def _check(self, blob):
        self._calls.append(self._environment)
        if blob.get("forged"):
            # `_decode_signed_object` rejects before any environment comparison.
            raise VerificationException(VerificationStatus.VERIFICATION_FAILURE)
        if blob.get("bundle_mismatch"):
            raise VerificationException(VerificationStatus.INVALID_APP_IDENTIFIER)
        if blob.get("environment") != self._environment:
            raise VerificationException(VerificationStatus.INVALID_ENVIRONMENT)
        return dict(_TXN, environment=blob["environment"])

    def verify_and_decode_signed_transaction(self, blob):
        return self._check(blob)

    def verify_and_decode_notification(self, blob):
        return self._check(blob)


@pytest.fixture
def wired(monkeypatch):
    """Configured for PRODUCTION with a SANDBOX sibling available."""
    calls: list = []
    primary = _StubVerifier("Production", calls)
    siblings = {"SANDBOX": _StubVerifier("Sandbox", calls)}

    monkeypatch.setattr(app_store, "get_verifier", lambda: primary)
    monkeypatch.setattr(app_store, "_get_sibling_verifiers", lambda: siblings)
    monkeypatch.setattr(app_store, "_resolve_environment", lambda: (None, "PRODUCTION"))
    return calls


# ── The bug this fixes ────────────────────────────────────────────────────────

def test_sandbox_payload_is_accepted_while_configured_for_production(wired):
    """THE App Review case. Without the fallback this raised and the reviewer saw a 400."""
    out = app_store._verify_with_environment_fallback(
        "verify_and_decode_signed_transaction", {"environment": "Sandbox"}
    )
    assert out["transactionId"] == _TXN["transactionId"]
    assert wired == ["Production", "Sandbox"], "should try the configured env first"


def test_production_payload_still_takes_the_primary_verifier(wired):
    """The normal path must not pay for the fallback: one call, no sibling consulted."""
    app_store._verify_with_environment_fallback(
        "verify_and_decode_signed_transaction", {"environment": "Production"}
    )
    assert wired == ["Production"]


def test_notifications_get_the_same_treatment(wired):
    """Server notifications carry the same environment field, so a Sandbox notification
    against a Production server would otherwise be dropped — and dropped notifications are
    how a refunded user keeps their tier."""
    out = app_store._verify_with_environment_fallback(
        "verify_and_decode_notification", {"environment": "Sandbox"}
    )
    assert out["transactionId"] == _TXN["transactionId"]


# ── The half that must NOT weaken ─────────────────────────────────────────────

def test_a_forged_payload_is_rejected_by_every_verifier(wired):
    """The fallback must not become 'ask around until someone says yes'. A signature failure
    fails identically in both environments."""
    with pytest.raises(VerificationException) as ei:
        app_store._verify_with_environment_fallback(
            "verify_and_decode_signed_transaction",
            {"environment": "Sandbox", "forged": True},
        )
    assert ei.value.status is VerificationStatus.VERIFICATION_FAILURE
    assert wired == ["Production"], "a signature failure must not be retried at all"


def test_wrong_bundle_id_is_not_retried(wired):
    """INVALID_APP_IDENTIFIER means the receipt is for a different app. Retrying it in
    another environment would be meaningless and would mask the real reason."""
    with pytest.raises(VerificationException) as ei:
        app_store._verify_with_environment_fallback(
            "verify_and_decode_signed_transaction",
            {"environment": "Production", "bundle_mismatch": True},
        )
    assert ei.value.status is VerificationStatus.INVALID_APP_IDENTIFIER
    assert wired == ["Production"]


def test_only_the_environment_status_is_treated_as_retryable():
    """Pins the predicate itself, so a future edit can't widen it to `any VerificationException`."""
    assert app_store._is_environment_mismatch(
        VerificationException(VerificationStatus.INVALID_ENVIRONMENT)
    )
    for status in VerificationStatus:
        if status is VerificationStatus.INVALID_ENVIRONMENT:
            continue
        assert not app_store._is_environment_mismatch(VerificationException(status)), (
            f"{status.name} must stay fatal"
        )
    assert not app_store._is_environment_mismatch(ValueError("unrelated"))


# ── Local environments are never cross-accepted ───────────────────────────────

@pytest.mark.parametrize("env_name", ["XCODE", "LOCALTESTING"])
def test_local_environments_have_no_siblings(env_name):
    """Xcode / LocalTesting SKIP signature verification. Cross-accepting one from a real
    environment would accept forged, unsigned purchases — the exact hole that making
    IAP_ENVIRONMENT default to Production was meant to close."""
    assert app_store._sibling_environments(env_name) == []


def test_real_environments_are_siblings_of_each_other():
    assert [n for _, n in app_store._sibling_environments("PRODUCTION")] == ["SANDBOX"]
    assert [n for _, n in app_store._sibling_environments("SANDBOX")] == ["PRODUCTION"]


def test_no_local_environment_is_ever_offered_as_a_sibling():
    """Belt and braces: whatever the configured environment, a signature-skipping one must
    never appear in the fallback set."""
    for env_name in ["PRODUCTION", "SANDBOX", "XCODE", "LOCALTESTING"]:
        names = {n for _, n in app_store._sibling_environments(env_name)}
        assert not (names & app_store._LOCAL_ENVIRONMENTS)
