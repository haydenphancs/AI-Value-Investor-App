"""The shipped Apple root CAs must be loadable by Apple's library, not merely present.

WHY THIS FILE EXISTS — a launch-blocking bug that every existing check passed.

`AppleRootCA-G3.pem` was committed to `backend/certs/apple/` (converted from Apple's `.cer`
purely because `.gitignore` carries a blanket `*.cer` rule for signing material). Apple's
library loads trust anchors with `crypto.load_certificate(crypto.FILETYPE_ASN1, ...)` —
ASN.1/DER — so the PEM raised, and `_verify_chain_without_caching` catches that and reports
`INVALID_CERTIFICATE`. Every real purchase would have failed.

Nothing caught it:
  * the file existed, so the "no root certificates" 503 never fired;
  * the verifier still CONSTRUCTED, because roots are parsed per-verification, not at build;
  * the readiness probe (POST a garbage payload, expect 400 not 503) returned **400** —
    which is exactly what a garbage payload returns against a HEALTHY verifier. The probe
    could not distinguish "trust anchor unusable" from "payload correctly rejected".

So the assertion has to be on the certificate bytes themselves, in the form the library
consumes them. `_load_root_certificates` now normalises PEM->DER; these tests pin that the
output is always DER-parseable and that a bad file is dropped rather than poisoning the store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from OpenSSL import crypto

from app.config import settings
from app.integrations import app_store

_CERT_DIR = Path(__file__).resolve().parents[1] / "certs" / "apple"


def _der_parses(data: bytes) -> bool:
    try:
        crypto.load_certificate(crypto.FILETYPE_ASN1, data)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _CERT_DIR.is_dir(), reason="no certs/apple directory in this checkout")
def test_every_loaded_root_is_der_parseable():
    """THE regression guard. Whatever encoding is on disk, what reaches Apple's library must
    parse as DER — otherwise the trust store is empty in effect and every purchase 400s."""
    original = settings.IAP_ROOT_CERT_DIR
    try:
        settings.IAP_ROOT_CERT_DIR = str(_CERT_DIR)
        app_store.reset_verifier_cache()
        certs = app_store._load_root_certificates("PRODUCTION")
    finally:
        settings.IAP_ROOT_CERT_DIR = original
        app_store.reset_verifier_cache()

    assert certs, "no root certificates loaded — Sandbox/Production verification cannot anchor"
    for i, der in enumerate(certs):
        assert _der_parses(der), (
            f"root certificate #{i} is not DER-parseable. Apple's library calls "
            f"load_certificate(FILETYPE_ASN1, ...) on it, so this silently breaks EVERY "
            f"purchase with a 400 that looks identical to a rejected payload."
        )


@pytest.mark.skipif(not _CERT_DIR.is_dir(), reason="no certs/apple directory in this checkout")
def test_the_shipped_root_is_apples_g3_root():
    """Anchoring to the wrong CA is worse than anchoring to none: it fails closed either way
    here, but a future 'fix' that adds some other root would widen the trust boundary."""
    original = settings.IAP_ROOT_CERT_DIR
    try:
        settings.IAP_ROOT_CERT_DIR = str(_CERT_DIR)
        app_store.reset_verifier_cache()
        certs = app_store._load_root_certificates("PRODUCTION")
    finally:
        settings.IAP_ROOT_CERT_DIR = original
        app_store.reset_verifier_cache()

    subjects = {
        crypto.load_certificate(crypto.FILETYPE_ASN1, d).get_subject().CN for d in certs
    }
    assert "Apple Root CA - G3" in subjects, (
        f"Apple Root CA - G3 is not among the shipped roots (found {subjects}). The App Store "
        f"transaction JWS chain anchors to G3; the older 'Apple Inc. Root Certificate' (RSA) "
        f"will not verify it."
    )


def test_a_pem_root_is_accepted_and_converted(tmp_path):
    """The encoding on disk must stop mattering. `.gitignore` blocks `*.cer`, so a PEM is the
    natural thing for someone to commit — it must work rather than fail invisibly."""
    key = crypto.PKey()
    key.generate_key(crypto.TYPE_RSA, 2048)
    cert = crypto.X509()
    cert.get_subject().CN = "Test Root"
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(key)
    cert.set_serial_number(1)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(86400)
    cert.sign(key, "sha256")
    (tmp_path / "root.pem").write_bytes(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))

    original = settings.IAP_ROOT_CERT_DIR
    try:
        settings.IAP_ROOT_CERT_DIR = str(tmp_path)
        app_store.reset_verifier_cache()
        certs = app_store._load_root_certificates("PRODUCTION")
    finally:
        settings.IAP_ROOT_CERT_DIR = original
        app_store.reset_verifier_cache()

    assert len(certs) == 1 and _der_parses(certs[0]), "a PEM root was not converted to DER"


def test_an_unparseable_file_is_dropped_not_appended(tmp_path):
    """One bad file must not disable the good ones.

    `_verify_chain_without_caching` builds the whole trust store inside ONE try block, so an
    entry that fails to load raises INVALID_CERTIFICATE for every root beside it. Skipping the
    bad file is what keeps a stray download from taking payments down.
    """
    good = crypto.PKey()
    good.generate_key(crypto.TYPE_RSA, 2048)
    cert = crypto.X509()
    cert.get_subject().CN = "Good Root"
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(good)
    cert.set_serial_number(2)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(86400)
    cert.sign(good, "sha256")
    (tmp_path / "a_good.der").write_bytes(crypto.dump_certificate(crypto.FILETYPE_ASN1, cert))
    (tmp_path / "b_junk.der").write_bytes(b"this is not a certificate at all")

    original = settings.IAP_ROOT_CERT_DIR
    try:
        settings.IAP_ROOT_CERT_DIR = str(tmp_path)
        app_store.reset_verifier_cache()
        certs = app_store._load_root_certificates("PRODUCTION")
    finally:
        settings.IAP_ROOT_CERT_DIR = original
        app_store.reset_verifier_cache()

    assert len(certs) == 1, "the junk file was appended; it will poison the whole trust store"
    assert crypto.load_certificate(crypto.FILETYPE_ASN1, certs[0]).get_subject().CN == "Good Root"
