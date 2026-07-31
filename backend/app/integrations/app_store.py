"""
App Store Server integration — verification of Apple-signed purchase data.

This is the trust boundary for paid entitlement. A client saying "I bought Max" means
nothing; what counts is a transaction Apple SIGNED, verified against Apple's certificate
chain. Everything here exists to answer one question: did Apple really say this?

Uses Apple's own `app-store-server-library` rather than hand-rolling JWS + x5c chain
validation. That is deliberate — a bespoke chain validator on a payment path is exactly the
kind of code where "written carefully" is weaker than "written by the vendor", and getting
it subtly wrong means accepting forged receipts.

Thin by design (see .claude/rules/integrations.md): verify in, plain dict out. No entitlement
decisions, no Supabase, no caching — those live in services/iap_service.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


class AppStoreException(Exception):
    """Base for App Store verification failures."""


class AppStoreNotConfigured(AppStoreException):
    """Verification cannot run — misconfiguration, not a bad receipt.

    Distinct from `AppStoreVerificationFailed` because the two mean opposite things
    operationally: this is our fault and pages an operator, that one is a rejected purchase.
    """


class AppStoreVerificationFailed(AppStoreException):
    """Apple's signature, certificate chain, bundle id, or environment did not check out.

    Treat as hostile input: never grant entitlement, and never echo the reason to the client.
    """


# Environments where Apple's library does not validate against the real root CAs, because
# the data is signed by a local Xcode/StoreKit test certificate.
_LOCAL_ENVIRONMENTS = {"XCODE", "LOCALTESTING"}

_verifier = None          # lazily built SignedDataVerifier
_verifier_key: tuple | None = None   # config snapshot the cached verifier was built from


def _resolve_environment():
    """Map the configured string to the library's Environment enum."""
    from appstoreserverlibrary.models.Environment import Environment  # noqa: PLC0415

    raw = (settings.IAP_ENVIRONMENT or "").strip().replace("_", "").replace(" ", "").upper()
    mapping = {
        "PRODUCTION": Environment.PRODUCTION,
        "SANDBOX": Environment.SANDBOX,
        "XCODE": Environment.XCODE,
        "LOCALTESTING": Environment.LOCAL_TESTING,
    }
    env = mapping.get(raw)
    if env is None:
        raise AppStoreNotConfigured(
            f"IAP_ENVIRONMENT={settings.IAP_ENVIRONMENT!r} is not one of "
            "Production | Sandbox | Xcode | LocalTesting"
        )
    return env, raw


def _load_root_certificates(env_name: str) -> List[bytes]:
    """Read Apple's public root CAs from disk.

    Empty is legitimate for Xcode/LocalTesting. For Sandbox/Production an empty list is a
    hard error: Apple's library would otherwise have nothing to anchor the chain to, and
    "no trust anchor" must never silently degrade into "accept anything" on a payment path.
    """
    cert_dir = Path(settings.IAP_ROOT_CERT_DIR)
    certs: List[bytes] = []
    if cert_dir.is_dir():
        for path in sorted(cert_dir.iterdir()):
            if path.suffix.lower() in {".cer", ".der", ".pem", ".crt"} and path.is_file():
                try:
                    certs.append(path.read_bytes())
                except OSError as e:
                    logger.error("Could not read Apple root cert %s: %s", path, e)

    if not certs and env_name not in _LOCAL_ENVIRONMENTS:
        raise AppStoreNotConfigured(
            f"No Apple root certificates found in {cert_dir!r} and "
            f"IAP_ENVIRONMENT={env_name}. Download them from "
            "https://www.apple.com/certificateauthority/ (AppleRootCA-G3.cer) and place "
            "them there, or set IAP_ENVIRONMENT=LocalTesting for local work."
        )
    return certs


def get_verifier():
    """Process-wide `SignedDataVerifier`, rebuilt if the relevant config changes.

    Cached because constructing it parses certificates. Keyed on the config it was built
    from so a settings change in a test or a reload can't be served a stale verifier.
    """
    global _verifier, _verifier_key

    env, env_name = _resolve_environment()
    key = (
        env_name,
        settings.IAP_BUNDLE_ID,
        settings.IAP_APP_APPLE_ID,
        settings.IAP_ROOT_CERT_DIR,
    )
    if _verifier is not None and _verifier_key == key:
        return _verifier

    from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier  # noqa: PLC0415

    root_certs = _load_root_certificates(env_name)

    # Online checks fetch OCSP/CRL to catch a revoked certificate. Enabled outside local
    # environments; a local test cert has nothing to check against.
    enable_online_checks = env_name not in _LOCAL_ENVIRONMENTS

    try:
        _verifier = SignedDataVerifier(
            root_certificates=root_certs,
            enable_online_checks=enable_online_checks,
            environment=env,
            bundle_id=settings.IAP_BUNDLE_ID,
            app_apple_id=settings.IAP_APP_APPLE_ID,
        )
    except Exception as e:
        raise AppStoreNotConfigured(
            f"Could not build the App Store verifier: {type(e).__name__}: {e}"
        ) from e

    _verifier_key = key
    logger.info(
        "App Store verifier ready (environment=%s, bundle=%s, roots=%d, online_checks=%s)",
        env_name, settings.IAP_BUNDLE_ID, len(root_certs), enable_online_checks,
    )
    return _verifier


def reset_verifier_cache() -> None:
    """Drop the cached verifier. For tests that change settings between cases."""
    global _verifier, _verifier_key
    _verifier, _verifier_key = None, None


def _to_dict(decoded: Any) -> Dict[str, Any]:
    """Flatten a library model into a plain dict (integration layer returns plain types)."""
    if decoded is None:
        return {}
    raw = getattr(decoded, "__dict__", None)
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if v is None:
            continue
        # Enums → their raw value, so callers never depend on library types.
        out[k] = getattr(v, "value", v)
    return out


def verify_signed_transaction(signed_transaction: str) -> Dict[str, Any]:
    """Verify a StoreKit 2 `Transaction` JWS and return its decoded payload.

    Raises `AppStoreVerificationFailed` for anything Apple's verifier rejects — bad
    signature, untrusted chain, wrong bundle id, wrong environment — and
    `AppStoreNotConfigured` when we cannot verify at all.
    """
    if not signed_transaction or not signed_transaction.strip():
        raise AppStoreVerificationFailed("empty signed transaction")

    verifier = get_verifier()
    try:
        decoded = verifier.verify_and_decode_signed_transaction(signed_transaction)
    except AppStoreException:
        raise
    except Exception as e:
        # Includes the library's VerificationException. Logged with the type but NOT echoed
        # to the client — a verifier that explains why it rejected you is an oracle.
        logger.warning(
            "Signed transaction verification failed: %s: %s", type(e).__name__, e
        )
        raise AppStoreVerificationFailed(f"{type(e).__name__}: {e}") from e

    payload = _to_dict(decoded)
    if not payload.get("transactionId"):
        raise AppStoreVerificationFailed("verified payload has no transactionId")
    return payload


def verify_notification(signed_payload: str) -> Dict[str, Any]:
    """Verify an App Store Server Notification V2 JWS and return its decoded payload."""
    if not signed_payload or not signed_payload.strip():
        raise AppStoreVerificationFailed("empty signed notification")

    verifier = get_verifier()
    try:
        decoded = verifier.verify_and_decode_notification(signed_payload)
    except AppStoreException:
        raise
    except Exception as e:
        logger.warning("Notification verification failed: %s: %s", type(e).__name__, e)
        raise AppStoreVerificationFailed(f"{type(e).__name__}: {e}") from e

    payload = _to_dict(decoded)
    if not payload.get("notificationType"):
        raise AppStoreVerificationFailed("verified notification has no notificationType")
    return payload


def extract_transaction_from_notification(
    notification: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Verify and decode the transaction nested inside a notification's `data`.

    Notifications carry the transaction as its own JWS, so it needs verifying in turn —
    trusting the outer envelope's contents without checking the inner signature would
    accept a notification with a swapped-in transaction.
    """
    data = notification.get("data")
    signed = None
    if isinstance(data, dict):
        signed = data.get("signedTransactionInfo")
    else:
        signed = getattr(data, "signedTransactionInfo", None)
    if not signed:
        return None
    return verify_signed_transaction(signed)
