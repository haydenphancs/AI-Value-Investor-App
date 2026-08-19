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

try:
    # `attrs` ships as a transitive dependency of app-store-server-library, whose models are
    # all `@define(slots=True)`. Imported defensively so a packaging change degrades to the
    # old __dict__ path rather than breaking import of the whole integration.
    import attr
except ImportError:  # pragma: no cover - attrs is present wherever the Apple library is
    attr = None  # type: ignore[assignment]

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

# Verifiers for environments OTHER than the configured one, built on demand. See
# `_verify_with_environment_fallback` for why an environment we did not configure is still
# a legitimate thing to accept.
_alt_verifiers: dict = {}
_alt_verifier_key: tuple | None = None

# The one and only verification failure that justifies a retry. Apple's library checks the
# JWS chain FIRST (`_decode_signed_object`), then the bundle id, and only then compares the
# environment — so by the time this status is raised the signature and the trust chain have
# ALREADY passed and the bundle id has ALREADY matched. Retrying such a payload against the
# sibling environment therefore re-runs the same cryptographic validation and differs only in
# the environment equality check. Any other status (bad signature, untrusted chain, wrong
# app) must stay fatal — retrying those would be a real weakening.
_RETRYABLE_STATUS_NAME = "INVALID_ENVIRONMENT"

# NOTIFICATIONS ONLY. A Sandbox notification configured against Production never reaches the
# environment comparison at all, so the fallback above was dead on this path and EVERY sandbox
# notification was rejected 400 — which lands exactly when you first wire up the Server
# Notifications URL and reads as a bad URL rather than a code bug.
#
# The library (`SignedDataVerifier._verify_notification`) checks in this order:
#
#     if bundle_id != self._bundle_id or (
#         self._environment == Environment.PRODUCTION and app_apple_id != self._app_apple_id
#     ):
#         raise VerificationException(VerificationStatus.INVALID_APP_IDENTIFIER)
#     if environment != self._environment:
#         raise VerificationException(VerificationStatus.INVALID_ENVIRONMENT)
#
# Apple does not send `data.appAppleId` in Sandbox, so under a PRODUCTION verifier the
# `app_apple_id` half of that first condition trips and INVALID_APP_IDENTIFIER is raised
# before the environment is ever compared.
#
# Why retrying it is safe — the bundle id is STILL enforced:
#   * `_decode_signed_object` has already run, so the JWS signature and Apple's trust chain
#     passed. Only Apple can produce such a payload; a forged one dies here on every verifier.
#   * The sibling SANDBOX verifier re-runs the identical `bundle_id != self._bundle_id` test.
#     What it skips is only the `appAppleId` comparison — which the library itself skips for
#     non-Production, because Apple does not send the field there.
#   * A Production-signed payload retried against SANDBOX still fails on INVALID_ENVIRONMENT,
#     so a misconfigured `IAP_APP_APPLE_ID` cannot be laundered into an acceptance.
#
# Deliberately NOT applied to signed transactions: those carry no appAppleId comparison, so
# INVALID_APP_IDENTIFIER there means a genuine bundle-id mismatch — a different app — and must
# stay fatal.
_RETRYABLE_NOTIFICATION_STATUS_NAMES = frozenset(
    {_RETRYABLE_STATUS_NAME, "INVALID_APP_IDENTIFIER"}
)

_NOTIFICATION_METHOD = "verify_and_decode_notification"


def _is_environment_mismatch(exc: Exception, method_name: str = "") -> bool:
    status = getattr(exc, "status", None)
    name = getattr(status, "name", None)
    if method_name == _NOTIFICATION_METHOD:
        return name in _RETRYABLE_NOTIFICATION_STATUS_NAMES
    return name == _RETRYABLE_STATUS_NAME


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

    # A LOCAL environment outside local development is a forged-purchase hole, not a
    # convenience setting. In Xcode/LocalTesting Apple's `SignedDataVerifier` returns the
    # decoded JWT WITHOUT checking the signature at all
    # (`signed_data_verifier._decode_signed_object`), `_load_root_certificates` below is
    # allowed to find zero trust anchors, and online revocation checks are disabled. A deploy
    # set that way accepts any unsigned JWT anyone posts to `POST /billing/verify` as a
    # genuine Apple purchase — free Max tier and 4,000 credits for the asking.
    #
    # The default already fails closed (`Production`, pinned by
    # `tests/test_iap_environment_fails_closed.py`). This closes the other half: an EXPLICIT
    # setting. Nothing else stopped a Railway instance from carrying `IAP_ENVIRONMENT=Xcode`.
    #
    # Raised here rather than validated on `Settings` deliberately. This fails the payment
    # path closed — 503, exactly like the missing-root-certificates case below — instead of
    # refusing to boot the whole app on a misconfigured deploy, which would take down market
    # data, Learn and chat over an IAP setting.
    if raw in _LOCAL_ENVIRONMENTS and settings.ENVIRONMENT != "development":
        raise AppStoreNotConfigured(
            f"IAP_ENVIRONMENT={settings.IAP_ENVIRONMENT!r} skips Apple signature verification "
            f"and is only permitted when ENVIRONMENT=development (got "
            f"{settings.ENVIRONMENT!r}). Set IAP_ENVIRONMENT=Sandbox or Production."
        )

    return env, raw


def _as_der(data: bytes, path: Path) -> bytes:
    """Normalise a root certificate to DER, which is the only form the library accepts.

    Apple's library loads trust anchors with
    `crypto.load_certificate(crypto.FILETYPE_ASN1, ...)` — ASN.1/**DER**. Handing it a
    PEM file raises, and the raise happens inside the loop that builds the trust store, so
    ONE PEM disables every root in the directory.

    This shipped: `AppleRootCA-G3.pem` was committed (converted from Apple's `.cer` only
    because `.gitignore` has a blanket `*.cer` rule for signing material). The verifier still
    CONSTRUCTED fine, so the readiness probe returned 400 "Invalid signature" — exactly what a
    garbage payload returns against a *working* verifier. Nothing distinguished "trust anchor
    unusable" from "payload rejected", and every real purchase would have 400'd.

    Accepting both formats here is the fix: the on-disk encoding is now irrelevant, and a
    file that is neither raises `ValueError` for the caller to skip and log loudly.
    """
    if data.lstrip().startswith(b"-----BEGIN"):
        from OpenSSL import crypto  # noqa: PLC0415

        try:
            cert = crypto.load_certificate(crypto.FILETYPE_PEM, data)
        except Exception as e:  # noqa: BLE001 — surface as a typed error to the caller
            raise ValueError(f"{path.name} looks like PEM but does not parse: {e}") from e
        logger.info("Converted PEM root certificate %s to DER", path.name)
        return crypto.dump_certificate(crypto.FILETYPE_ASN1, cert)

    from OpenSSL import crypto  # noqa: PLC0415

    try:
        crypto.load_certificate(crypto.FILETYPE_ASN1, data)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{path.name} is neither valid DER nor PEM: {e}") from e
    return data


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
                    certs.append(_as_der(path.read_bytes(), path))
                except OSError as e:
                    logger.error("Could not read Apple root cert %s: %s", path, e)
                except ValueError as e:
                    # Do NOT append an unparseable root. `_verify_chain_without_caching`
                    # loads every trusted cert inside ONE try block, so a single bad entry
                    # raises INVALID_CERTIFICATE for the whole store — one broken file
                    # disables every good root beside it.
                    logger.error("Ignoring unusable Apple root cert %s: %s", path, e)

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
    """Drop the cached verifiers. For tests that change settings between cases."""
    global _verifier, _verifier_key, _alt_verifiers, _alt_verifier_key
    _verifier, _verifier_key = None, None
    _alt_verifiers, _alt_verifier_key = {}, None


def _sibling_environments(env_name: str):
    """Environments a payload may legitimately arrive from besides the configured one.

    Production <-> Sandbox, and nothing else.

    Why this exists: **App Review tests in-app purchases in SANDBOX against the build you
    submitted for PRODUCTION.** A server configured `IAP_ENVIRONMENT=Production` that accepts
    only its own environment therefore rejects every reviewer purchase with a 400, and the
    submission is rejected for "in-app purchase doesn't work" — a failure that never
    reproduces for the developer, because their own sandbox testing runs against a server set
    to Sandbox. Apple's own guidance is to accept both.

    Local environments are deliberately NOT cross-accepted. `Xcode` and `LocalTesting` skip
    real certificate validation entirely (that is what `_LOCAL_ENVIRONMENTS` gates), so
    honouring an Xcode-signed receipt on a Production server would mean accepting forged,
    unsigned purchases — the exact fail-open that `IAP_ENVIRONMENT` defaulting to Production
    was changed to prevent.
    """
    from appstoreserverlibrary.models.Environment import Environment  # noqa: PLC0415

    if env_name == "PRODUCTION":
        return [(Environment.SANDBOX, "SANDBOX")]
    if env_name == "SANDBOX":
        return [(Environment.PRODUCTION, "PRODUCTION")]
    return []


def _get_sibling_verifiers():
    """Build (once) the verifiers for the sibling environments of the configured one.

    Same root certificates and same bundle id as the primary verifier — only the environment
    field differs, so this adds no new trust anchor.
    """
    global _alt_verifiers, _alt_verifier_key

    _, env_name = _resolve_environment()
    key = (
        env_name,
        settings.IAP_BUNDLE_ID,
        settings.IAP_APP_APPLE_ID,
        settings.IAP_ROOT_CERT_DIR,
    )
    if _alt_verifier_key == key:
        return _alt_verifiers

    from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier  # noqa: PLC0415

    built = {}
    for env, name in _sibling_environments(env_name):
        try:
            built[name] = SignedDataVerifier(
                root_certificates=_load_root_certificates(env_name),
                enable_online_checks=env_name not in _LOCAL_ENVIRONMENTS,
                environment=env,
                bundle_id=settings.IAP_BUNDLE_ID,
                app_apple_id=settings.IAP_APP_APPLE_ID,
            )
        except Exception as e:
            # Non-fatal: the primary verifier still works, we simply lose the cross-environment
            # acceptance. Logged loudly because losing it silently is how App Review fails.
            logger.warning(
                "Could not build the %s fallback verifier (%s: %s) — purchases signed for "
                "that environment will be rejected",
                name, type(e).__name__, e,
            )

    _alt_verifiers, _alt_verifier_key = built, key
    return _alt_verifiers


def _verify_with_environment_fallback(method_name: str, blob: str):
    """Run `method_name` on the configured verifier, retrying siblings on an env mismatch.

    For TRANSACTIONS, only `INVALID_ENVIRONMENT` is retried, and only after the primary
    verifier has already validated the signature, the trust chain and the bundle id (Apple's
    library checks those first — see `_RETRYABLE_STATUS_NAME`). A forged or tampered payload
    fails identically on every verifier, so this cannot be used to smuggle one through.

    For NOTIFICATIONS, `INVALID_APP_IDENTIFIER` is retried as well, because a Sandbox
    notification never reaches the environment comparison — see
    `_RETRYABLE_NOTIFICATION_STATUS_NAMES` for why the bundle id is still enforced.
    """
    primary = get_verifier()
    try:
        return getattr(primary, method_name)(blob)
    except Exception as first:
        if not _is_environment_mismatch(first, method_name):
            raise

        for name, verifier in _get_sibling_verifiers().items():
            try:
                decoded = getattr(verifier, method_name)(blob)
            except Exception:
                continue
            logger.info(
                "Accepted a %s-signed payload while configured for %s (expected during "
                "App Review, which tests purchases in Sandbox against the production build)",
                name, _resolve_environment()[1],
            )
            return decoded
        raise


def _to_dict(decoded: Any) -> Dict[str, Any]:
    """Flatten a library model into a plain dict (integration layer returns plain types).

    Reads **attrs fields**, not ``__dict__``. Apple's models (`JWSTransactionDecodedPayload`
    and friends) are `attrs @define` classes, i.e. `slots=True`: all 42 fields live in
    `__slots__`, and the instance's `__dict__` — inherited from the non-slotted base
    `AttrsRawValueAware` — is present but **permanently empty**, even after assignment.

    So the previous `__dict__` flattening returned `{}` for every successfully verified
    transaction. It cleared the `isinstance(raw, dict)` guard (an empty dict IS a dict), and
    the caller then raised `AppStoreVerificationFailed("missing transactionId")` — meaning
    Apple's signature check passed and the purchase was rejected anyway. `POST /billing/verify`
    answered 400 for EVERY real purchase: the user is charged by Apple and receives nothing.

    This survived 35 IAP tests because they all feed plain dicts, never a real library model —
    which is exactly why `test_iap_payload_flattening.py` now asserts against the real class.
    """
    if decoded is None:
        return {}

    raw: Any
    if attr is not None and attr.has(type(decoded)):
        raw = {f.name: getattr(decoded, f.name, None) for f in attr.fields(type(decoded))}
    else:
        # Non-attrs model (or attrs unavailable): fall back to the old behaviour.
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

    try:
        decoded = _verify_with_environment_fallback(
            "verify_and_decode_signed_transaction", signed_transaction
        )
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

    try:
        decoded = _verify_with_environment_fallback(
            "verify_and_decode_notification", signed_payload
        )
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
