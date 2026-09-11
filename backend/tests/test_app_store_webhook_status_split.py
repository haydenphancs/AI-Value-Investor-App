"""Apple's refund/revocation webhook — the ONLY channel through which a refund reaches the
credit clawback, and nothing was exercising it.

WHY THIS FILE EXISTS
--------------------
`POST /billing/app-store-notifications` (`billing.app_store_notifications`) had **zero test
coverage**: the sibling billing tests source-scan `verify_purchase`'s except chain, and the
licence-gate test lists this route in its allow-list, but no test ever CALLED it. Found by
mutation — none of these moved a single test:

  * swapping the 503 and 400 arms on the envelope check (`AppStoreNotConfigured` vs
    `AppStoreVerificationFailed`);
  * deleting the `signedPayload` presence check;
  * answering the `IAPError` arm with 400 instead of 503.

Those status codes are not cosmetic. Apple's server retries a non-2xx for days and gives up
on a 4xx, so:

  * **400 says "your payload is bad, stop."** Right for a JWS that fails Apple's own
    signature check — it will never verify, and inviting retries is pointless.
  * **503 says "our fault, try again later."** Right for a missing root-cert bundle or a
    database blip. Answer 400 here and Apple STOPS: the REFUND notification is dropped
    permanently and the refunded user keeps the credits.
  * Answer 503 where 400 was right and Apple hammers the endpoint for days on a payload that
    can never verify.

The client only ever tells us about *purchases*; a refund, cancellation or failed renewal
arrives here or not at all (the handler's own docstring says so). A wrong status here is a
money bug with no user-visible symptom — exactly the kind nothing else notices.

Pure module: the three collaborators (`verify_notification`,
`extract_transaction_from_notification`, `get_iap_service`) are stubbed on the ENDPOINT
module, where `billing.py`'s module-level imports bind them. No network, no Supabase.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints import billing
from app.integrations import app_store
from app.integrations.app_store import AppStoreNotConfigured, AppStoreVerificationFailed
from app.main import app
from app.services.iap_service import IAPError

# A verified REFUND envelope and the verified transaction nested inside it — what the two
# stubbed verifiers hand back. The values are arbitrary; the tests assert IDENTITY (the
# handler must forward these very objects), not contents.
_NOTIFICATION = {
    "notificationType": "REFUND",
    "subtype": "",
    "signedDate": 1725000000000,
    "data": {"signedTransactionInfo": "<inner-jws>"},
}
_TRANSACTION = {
    "transactionId": "2000000123456789",
    "originalTransactionId": "2000000100000000",
    "productId": "com.phan.caydex.pro.monthly",
}
_BODY = {"signedPayload": "<outer-jws>"}


class _FakeRequest:
    """Only `request.json()` is touched. `malformed=True` models a body that is not JSON —
    Starlette raises `json.JSONDecodeError` (a `ValueError`) from `.json()` in that case,
    and this raises the SAME type so the handler could tighten its `except Exception` to
    either without this test lying about it. The wire test in §5 proves the real parser."""

    def __init__(self, body=None, *, malformed=False):
        self._body, self._malformed = body, malformed

    async def json(self):
        if self._malformed:
            raise json.JSONDecodeError("Expecting value", "not json", 0)
        return self._body


class _Spy:
    """Records every call, then returns `result` — or raises it if it is an exception."""

    def __init__(self, result=None):
        self.calls: list[tuple] = []
        self._result = result

    def __call__(self, *args):
        self.calls.append(args)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _FakeIAPService:
    def __init__(self, outcome="applied:REFUND", raises=None):
        self.calls: list[tuple] = []
        self._outcome, self._raises = outcome, raises

    def apply_notification(self, notification, transaction):
        self.calls.append((notification, transaction))
        if self._raises is not None:
            raise self._raises
        return self._outcome, "user-1"


@pytest.fixture(autouse=True)
def _nothing_real_is_reachable(monkeypatch):
    """Every collaborator starts as a tripwire; each test installs only what it needs.

    Without this, a test that forgets to stub reaches the REAL `verify_notification`, and in
    this process that is `IAP_ENVIRONMENT='Production'`: with no root-cert bundle it raises
    `AppStoreNotConfigured` → 503, and WITH one it attempts an OCSP fetch → the conftest
    network guard. Either way a "503 arm" assertion could pass without the stub being the
    reason — the vacuity rule from .claude/rules/testing.md. It also keeps the memoized
    verifier (`app_store._verifier`) and the `iap_service` singleton out of the picture, so
    there is no warm cache for a later test to inherit.

    Patched on `billing`, not on the source modules: `billing.py` imports all three at
    MODULE level, so the endpoint's own binding is the one resolved at call time."""

    def _tripwire(name):
        def _raise(*_a, **_k):
            raise AssertionError(f"test reached the REAL `{name}` — stub it on `billing`")

        return _raise

    for name in (
        "verify_notification",
        "extract_transaction_from_notification",
        "get_iap_service",
    ):
        monkeypatch.setattr(billing, name, _tripwire(name))


def _wire(monkeypatch, *, verify=_NOTIFICATION, extract=_TRANSACTION, service=None):
    """Install the happy-path collaborators, each overridable with a return value or an
    exception instance to raise. Returns `(verify_spy, extract_spy, service)`."""
    verify_spy, extract_spy = _Spy(verify), _Spy(extract)
    if service is None:
        service = _FakeIAPService()
    monkeypatch.setattr(billing, "verify_notification", verify_spy)
    monkeypatch.setattr(billing, "extract_transaction_from_notification", extract_spy)
    monkeypatch.setattr(billing, "get_iap_service", lambda: service)
    return verify_spy, extract_spy, service


def _post(body=_BODY, *, malformed=False):
    """Call the handler the way FastAPI would. `asyncio.run`, not the process-wide loop."""
    return asyncio.run(
        billing.app_store_notifications(_FakeRequest(body, malformed=malformed))
    )


def _refused(body=_BODY, *, malformed=False) -> tuple[int, str]:
    """`(status_code, detail)` of the HTTPException the handler raised."""
    with pytest.raises(HTTPException) as excinfo:
        _post(body, malformed=malformed)
    return excinfo.value.status_code, excinfo.value.detail


# ── 1. The body gate — garbage falls CLOSED, before any verifier is built ────────

def test_a_malformed_body_is_400_and_never_verified(monkeypatch):
    verify, _, service = _wire(monkeypatch)
    assert _refused(malformed=True) == (400, "Malformed body")
    assert verify.calls == [] and service.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},                            # no key at all
        None,                          # JSON `null` — `(body or {})` must not blow up
        {"signedPayload": ""},
        {"signedPayload": None},
        {"signed_payload": "<jws>"},   # Apple sends camelCase; the wrong key IS a missing key
    ],
)
def test_a_missing_or_blank_signed_payload_is_400(monkeypatch, body):
    """Deleting the presence check hands `None` to `verify_notification`. The integration
    would still refuse it — but as `AppStoreVerificationFailed`, after this handler had
    already decided the body was fine, with a log line blaming Apple's signature for what
    was an empty POST."""
    verify, _, service = _wire(monkeypatch)
    assert _refused(body) == (400, "Missing signedPayload")
    assert verify.calls == [], "an absent payload reached the verifier"
    assert service.calls == []


def test_a_whitespace_payload_is_refused_by_the_integration_guard(monkeypatch):
    """`if not signed_payload` lets `"   "` through — it is the integration's `.strip()`
    guard that refuses it, BEFORE any verifier is built, which is what keeps this hermetic.
    Uses the REAL `verify_notification` for exactly that reason: it pins that the endpoint
    and the integration fall closed together on an input each alone would let past. If the
    integration ever moves that guard behind verifier construction, this test stops being
    a 400 (503 or a NETWORK block) and says so.

    That last claim holds only while `app_store._verifier` is COLD: a verifier an earlier
    test left cached would parse-fail the whitespace and answer the same 400 with the guard
    gone. Dropped here so the claim does not depend on which file ran first. (In this
    process the verifier is unbuildable anyway — `Production` with no `IAP_APP_APPLE_ID`
    raises `AppStoreNotConfigured` — but that is an accident of config, not a guarantee.)"""
    app_store.reset_verifier_cache()
    _, extract, service = _wire(monkeypatch)
    monkeypatch.setattr(billing, "verify_notification", app_store.verify_notification)
    assert _refused({"signedPayload": "  \n\t "}) == (400, "Invalid signature")
    assert extract.calls == [] and service.calls == []


@pytest.mark.parametrize("body", [["<jws>"], "<jws>", 123])
def test_a_non_object_body_never_reaches_verification(monkeypatch, body):
    """Apple never sends a JSON array / string / number, but anyone can. `(body or {}).get`
    surfaces these as an `AttributeError` today — the generic handler's 500, not the 400
    they deserve — so this pins only the part that matters: no verifier runs and nothing
    is applied. (A 400 would be an improvement; treating the body AS the payload — the
    tempting `body if isinstance(body, str) else ...` — is the mutation this catches.)

    Exactly those two outcomes are accepted. A bare `pytest.raises(Exception)` would also
    bless a 503 (Apple retries a body that can never parse) or any unrelated crash."""
    verify, _, service = _wire(monkeypatch)
    with pytest.raises((AttributeError, HTTPException)) as excinfo:
        _post(body)
    if isinstance(excinfo.value, HTTPException):
        assert excinfo.value.status_code == 400, "a non-object body must not invite retries"
    assert verify.calls == [] and service.calls == []


# ── 2. The envelope check — 503 says "retry", 400 says "stop" ────────────────────
#
# `AppStoreNotConfigured` and `AppStoreVerificationFailed` share a base class, so the two
# tests below are one pair: collapse the arms into `except AppStoreException` and whichever
# status you pick, the other test fails. That is the mutation this file was written for.

def test_an_unconfigured_verifier_answers_503_so_apple_retries(monkeypatch):
    """🔴 Our fault (no root-cert bundle, bad IAP_ENVIRONMENT). A 400 here tells Apple the
    REFUND was malformed; it stops retrying and the clawback never happens."""
    _, extract, service = _wire(monkeypatch, verify=AppStoreNotConfigured("no root certs"))
    assert _refused() == (503, "Verification unavailable")
    assert extract.calls == [] and service.calls == []


def test_an_unverifiable_envelope_answers_400_so_apple_stops(monkeypatch):
    """Hostile or corrupt input. A 503 here invites retries on a payload that can never
    verify — Apple hammers the endpoint for days, and the log fills with 'unavailable' for
    something that is nobody's outage."""
    _, extract, service = _wire(monkeypatch, verify=AppStoreVerificationFailed("bad chain"))
    assert _refused() == (400, "Invalid signature")
    assert extract.calls == [] and service.calls == []


def test_an_unexpected_verifier_exception_is_neither_swallowed_nor_a_400(monkeypatch):
    """A library bug is not a bad payload. `except Exception → 400` would drop the refund
    permanently; `except Exception → 200` would tell Apple it was applied. Both are worse
    than the unhandled 500, which Apple retries."""
    _, extract, service = _wire(monkeypatch, verify=RuntimeError("verifier exploded"))
    with pytest.raises(RuntimeError):
        _post()
    assert extract.calls == [] and service.calls == []


# ── 3. The inner transaction — verified in its own right, never trusted via the envelope ──

def test_a_forged_inner_transaction_answers_400(monkeypatch):
    """A valid envelope wrapping a transaction that fails ITS signature check is the
    swapped-in-transaction attack the integration's docstring describes. It must not reach
    the service, and it is a 400 — the envelope may be Apple's, but the payload as a whole
    will never verify."""
    _, _, service = _wire(monkeypatch, extract=AppStoreVerificationFailed("inner chain"))
    assert _refused() == (400, "Invalid signature")
    assert service.calls == []


def test_the_service_receives_the_verified_objects_not_the_raw_body(monkeypatch):
    """Identity, not equality: the handler must forward the very dicts the verifiers
    returned. The raw body, the signedPayload string, or a re-decoded copy would all be
    "equal enough" for a looser assertion, and wrong."""
    verify, extract, service = _wire(monkeypatch)
    result = _post()

    assert verify.calls == [("<outer-jws>",)]
    assert len(extract.calls) == 1 and extract.calls[0][0] is _NOTIFICATION
    assert len(service.calls) == 1
    notification, transaction = service.calls[0]
    assert notification is _NOTIFICATION and transaction is _TRANSACTION
    assert result == {"received": True, "outcome": "applied:REFUND"}


def test_a_transaction_less_notification_still_reaches_the_service(monkeypatch):
    """`extract_transaction_from_notification` returns None when the envelope carries no
    `signedTransactionInfo` (Apple's TEST notification, for one). That is the SERVICE's
    decision to make — it answers `ignored_no_transaction` — not a reason for the endpoint
    to refuse, which would make Apple retry a notification we have chosen to ignore."""
    _, _, service = _wire(
        monkeypatch, extract=None, service=_FakeIAPService("ignored_no_transaction")
    )
    result = _post()

    assert service.calls == [(_NOTIFICATION, None)]
    assert result == {"received": True, "outcome": "ignored_no_transaction"}


# ── 4. The apply arm — transient means 503, and "applied" is only ever the service's word ──

def test_a_transient_service_failure_answers_503_so_apple_retries(monkeypatch):
    """🔴 The clawback lives on the other side of this call. `IAPError` is what
    `apply_notification` re-raises when the entitlement write fails — a database blip. 503
    keeps Apple retrying until the write lands; 400 would drop the REFUND for good and the
    refunded user keeps every credit."""
    _, _, service = _wire(monkeypatch, service=_FakeIAPService(raises=IAPError("db down")))
    assert _refused() == (503, "Could not apply notification")
    assert len(service.calls) == 1


def test_an_unexpected_service_exception_is_not_answered_200(monkeypatch):
    """`{"received": True}` is a promise to Apple that it need not retry. A bare
    `except Exception` around the apply call that still returns it would turn every
    unknown failure into a silently dropped notification."""
    _, _, service = _wire(monkeypatch, service=_FakeIAPService(raises=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _post()
    assert len(service.calls) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        "applied:REFUND",
        "applied:DID_RENEW",
        "ignored_unknown_transaction",
        "credit_pack_ignored:REFUND",
    ],
)
def test_the_outcome_is_echoed_verbatim(monkeypatch, outcome):
    """Anti-vacuity: the handler gutted to a constant `{"received": True, "outcome": "ok"}`
    passes every status-code test above and fails here. The outcome is the only record in
    the response of what actually happened, and the logs cross-reference it."""
    _wire(monkeypatch, service=_FakeIAPService(outcome))
    assert _post() == {"received": True, "outcome": outcome}


# ── 5. On the wire — what Apple's server actually sees ───────────────────────────
#
# The tests above call the handler directly. Two things only the full ASGI stack can prove:
# that the route is reachable with NO bearer (Apple has no account), and that `main.py`'s
# HTTPException handler renders these statuses unchanged. `TestClient(app)` WITHOUT the
# context manager — the lifespan's startup jobs reach Supabase, and conftest blocks that.

_WEBHOOK = "/api/v1/billing/app-store-notifications"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_on_the_wire_apple_needs_no_bearer(client, monkeypatch):
    """`test_account_only_licence_gate.py` EXEMPTS this route from the account wall; it does
    not assert the route is open. So `Depends(get_current_user)` added here would 401 every
    call Apple makes — refunds never claw back — and that file stays green. This one does
    not."""
    _, _, service = _wire(monkeypatch)
    r = client.post(_WEBHOOK, json=_BODY)  # deliberately no Authorization header
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True, "outcome": "applied:REFUND"}
    assert len(service.calls) == 1


@pytest.mark.parametrize(
    "body, verify, apply_error, expected",
    [
        ({}, _NOTIFICATION, None, (400, "Missing signedPayload")),
        (_BODY, AppStoreVerificationFailed("bad chain"), None, (400, "Invalid signature")),
        (_BODY, AppStoreNotConfigured("no certs"), None, (503, "Verification unavailable")),
        (_BODY, _NOTIFICATION, IAPError("db down"), (503, "Could not apply notification")),
    ],
    ids=["missing-payload", "bad-signature", "unconfigured", "iap-error"],
)
def test_on_the_wire_the_status_survives_the_exception_handler(
    client, monkeypatch, body, verify, apply_error, expected
):
    """The handler raises `HTTPException(detail=<str>)`; `main.py` renders a string detail
    as `{"detail": ...}` with the status unchanged. Pinned here because that handler is
    "narrow by design", and a well-meant reshape of every 4xx/5xx body could change what
    Apple's retry logic keys off."""
    _wire(
        monkeypatch,
        verify=verify,
        service=_FakeIAPService(raises=apply_error) if apply_error else None,
    )
    r = client.post(_WEBHOOK, json=body)
    assert (r.status_code, r.json()["detail"]) == expected


@pytest.mark.parametrize(
    "raw",
    [b"this is not json", b"", b"{\"signedPayload\": "],
    ids=["not-json", "empty", "truncated"],
)
def test_on_the_wire_a_body_the_real_parser_rejects_is_400(client, monkeypatch, raw):
    """The direct-call tests prove the `Malformed body` arm against `_FakeRequest`, whose
    `.json()` raises what we BELIEVE Starlette raises. This is the only test that lets the
    real `Request.json()` do the raising, so a Starlette upgrade that changed the exception
    type — or a handler tightened to a type the fake happens to match — fails here and
    nowhere else. A 400 is right: Apple never sends these, and a retry would not help."""
    verify, _, service = _wire(monkeypatch)
    r = client.post(_WEBHOOK, content=raw, headers={"content-type": "application/json"})
    assert (r.status_code, r.json()["detail"]) == (400, "Malformed body")
    assert verify.calls == [] and service.calls == []


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────────
#
# Hand-run 2026-09-10 (testing.md §3 rule 3). Each mutant was built from the handler's
# source text and rebound onto `billing` + the mounted route IN-PROCESS (app/ untouched),
# this file run, then the binding restored. Baseline green; every mutant red, and red in
# the test written for it:
#
#   swap the 503/400 envelope arms       -> unconfigured→503, unverifiable→400, whitespace,
#                                           wire-status ×2
#   delete the signedPayload check       -> missing-payload ×5, wire-status
#   IAPError arm answers 400             -> transient→503, wire-status
#   handler gutted to a constant reply   -> every test in the file (30/30)
#   trust the inner txn via the envelope -> forged-inner→400, identity, transaction-less
#   `except Exception → 200` on apply    -> transient→503, unexpected-not-200, wire-status
#   collapse envelope arms to → 503      -> unverifiable→400, whitespace, wire-status
#   collapse envelope arms to → 400      -> unconfigured→503, unverifiable→400 (wrong
#                                           detail), whitespace, wire-status ×2
#   refuse transaction-less envelopes    -> transaction-less
#   Depends(get_current_user) on route   -> the two wire tests ONLY — which is the point of
#                                           having them.
#   malformed body answers 503           -> malformed (direct), real-parser wire ×3
#   parse error escapes as 500           -> malformed (direct), real-parser wire ×3
#
# Zero blocked network calls across every run (the runner imported conftest first).
#
# Adversarial self-check 2026-09-10: every mutant above RE-RUN in-process (handler
# `__code__` swapped in place; the bearer mutant rebuilt the mounted `APIRoute`) — all red
# as logged. Three things tightened in this file only: the `Malformed body` arm had never
# met the REAL Starlette parser (only `_FakeRequest`, which raised a plain `ValueError`) —
# the fake now raises `json.JSONDecodeError` and a wire test posts non-JSON / empty /
# truncated bodies; the non-object-body test's `pytest.raises(Exception)` blessed any
# failure at all, now `AttributeError` or a 400; and the whitespace test resets the
# verifier cache so its "would become 503" claim does not hinge on which file ran first.
