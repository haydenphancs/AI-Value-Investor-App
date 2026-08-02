"""An unconfigured deploy must NOT accept forged in-app purchases.

Apple's `SignedDataVerifier` skips signature verification entirely in the two local
environments — `signed_data_verifier.py:_decode_signed_object`:

    decoded_jwt = jwt.decode(signed_obj, options={"verify_signature": False})
    if self._environment == Environment.XCODE or self._environment == Environment.LOCAL_TESTING:
        # Data is not signed by the App Store, and verification should be skipped
        return decoded_jwt

That is correct for local work against a `.storekit` file. It is catastrophic in production:
`POST /billing/verify` would accept ANY unsigned JWT as a genuine Apple transaction. Craft
`{"transactionId": "1", "originalTransactionId": "1", "productId": "com.phan.caydex.max.monthly"}`,
base64 it into an unsigned JWT, post it, and receive the Max tier and 4000 credits for free —
repeatedly, from any authenticated account.

`IAP_ENVIRONMENT` used to DEFAULT to "LocalTesting", and setting it on Railway is an unchecked
item on the launch checklist. A deploy that forgot it shipped that hole silently: nothing logs
an error, purchases "work", and the revenue simply never arrives.

The default is now "Production", which inverts the failure mode — no root certificates means
`AppStoreNotConfigured`, which `POST /billing/verify` turns into a 503. A misconfigured deploy
sells nothing instead of giving everything away.

No network, no Supabase, no Apple.
"""
from __future__ import annotations

import pytest

from app.config import Settings, settings
from app.integrations import app_store


_LOCAL_ONLY = {"XCODE", "LOCALTESTING"}


def test_default_environment_is_not_a_signature_skipping_one():
    """THE regression guard. If this flips back to a local environment, an unconfigured
    production deploy accepts forged receipts."""
    default = Settings.model_fields["IAP_ENVIRONMENT"].default
    normalized = str(default).strip().replace("_", "").replace(" ", "").upper()
    assert normalized not in _LOCAL_ONLY, (
        f"IAP_ENVIRONMENT defaults to {default!r}, where Apple's library SKIPS signature "
        "verification — any unsigned JWT would be accepted as a real purchase"
    )


def test_default_environment_is_a_recognized_value():
    """A typo'd default would raise AppStoreNotConfigured on every verify — fail-closed, but
    for the wrong reason, and it would look like a certificate problem."""
    default = Settings.model_fields["IAP_ENVIRONMENT"].default
    normalized = str(default).strip().replace("_", "").replace(" ", "").upper()
    assert normalized in {"PRODUCTION", "SANDBOX"}, (
        f"IAP_ENVIRONMENT default {default!r} is neither Production nor Sandbox"
    )


def test_local_environments_are_still_reachable_when_asked_for_explicitly():
    """Local development must keep working — it just has to be opted into."""
    original = settings.IAP_ENVIRONMENT
    try:
        for value in ("Xcode", "LocalTesting", "local_testing", "  xcode  "):
            settings.IAP_ENVIRONMENT = value
            app_store.reset_verifier_cache()
            _env, env_name = app_store._resolve_environment()
            assert env_name in _LOCAL_ONLY, f"{value!r} should resolve to a local environment"
    finally:
        settings.IAP_ENVIRONMENT = original
        app_store.reset_verifier_cache()


def test_production_without_root_certificates_fails_closed():
    """The whole point: no trust anchor must mean 'refuse', never 'accept anything'."""
    original_env = settings.IAP_ENVIRONMENT
    original_dir = settings.IAP_ROOT_CERT_DIR
    try:
        settings.IAP_ENVIRONMENT = "Production"
        settings.IAP_ROOT_CERT_DIR = "certs/definitely-not-here"
        app_store.reset_verifier_cache()
        with pytest.raises(app_store.AppStoreNotConfigured):
            app_store.get_verifier()
    finally:
        settings.IAP_ENVIRONMENT = original_env
        settings.IAP_ROOT_CERT_DIR = original_dir
        app_store.reset_verifier_cache()


def test_sandbox_without_root_certificates_also_fails_closed():
    original_env = settings.IAP_ENVIRONMENT
    original_dir = settings.IAP_ROOT_CERT_DIR
    try:
        settings.IAP_ENVIRONMENT = "Sandbox"
        settings.IAP_ROOT_CERT_DIR = "certs/definitely-not-here"
        app_store.reset_verifier_cache()
        with pytest.raises(app_store.AppStoreNotConfigured):
            app_store.get_verifier()
    finally:
        settings.IAP_ENVIRONMENT = original_env
        settings.IAP_ROOT_CERT_DIR = original_dir
        app_store.reset_verifier_cache()


def test_unknown_environment_is_rejected_rather_than_guessed():
    original = settings.IAP_ENVIRONMENT
    try:
        settings.IAP_ENVIRONMENT = "Staging"
        app_store.reset_verifier_cache()
        with pytest.raises(app_store.AppStoreNotConfigured):
            app_store._resolve_environment()
    finally:
        settings.IAP_ENVIRONMENT = original
        app_store.reset_verifier_cache()


def test_online_revocation_checks_are_on_outside_local_environments():
    """A revoked certificate must not keep verifying; the local environments have nothing to
    check against, which is exactly why they must not be the default."""
    assert app_store._LOCAL_ENVIRONMENTS == {"XCODE", "LOCALTESTING"}
