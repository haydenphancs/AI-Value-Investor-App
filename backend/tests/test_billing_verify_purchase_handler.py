"""`POST /billing/verify` — the trust boundary for every in-app purchase, finally INVOKED.

WHY THIS FILE EXISTS
--------------------
`verify_purchase` in `app/api/v1/endpoints/billing.py` is the only door through which an
Apple-signed StoreKit transaction becomes a tier or a credit balance. Until this file it was
covered exclusively by `inspect.getsource()` STRING assertions — `test_iap_entitlement.py`
checks that the text "except PurchaseBoundToAnotherAccount" sits above "except IAPError",
`test_credit_pack_purchase.py` that "except PurchaseAccountMismatch" sits above its parent.
No test ever CALLED the handler.

Found by mutation. With those substrings left intact, every one of these survived the suite:

  * `status_code=503` → `status_code=200` on the `AppStoreNotConfigured` arm, so a
    misconfigured verifier answers a real purchase with a 200 carrying no entitlement;
  * `result.get("credits_granted", 0)` → `result.get("credits_granted", 1200)`, so every
    subscription replay tells the client it just added 1,200 credits;
  * `tier=result["winning_tier"]` → `tier=result["tier"]`, so a Pro receipt replayed by a Max
    subscriber renders as a demotion — the exact bug the `winning_tier` key exists to prevent;
  * `message=str(e)` on the verification-failed arm, handing Apple's rejection reason back to
    whoever is forging receipts;
  * swapping the two 409 codes, which tells iOS to `finish()` a transaction nobody was credited
    for — deleting a purchase the user paid for, with no redelivery left to repair it.

A source scan proves the words are there; it cannot prove what they DO. Everything below calls
the handler with a stubbed verifier and a stubbed `IAPService` and asserts on the RESPONSE —
status code, error code, action, the field mapping, and which arm a subclass actually lands in.

No network: `billing.py` binds `verify_signed_transaction` and `get_iap_service` with
MODULE-LEVEL imports, so the binding the handler resolves at call time is `billing.<name>` and
that is what gets patched. An autouse fixture makes any path that reaches the real ones fail
loudly instead of reaching Apple or Supabase.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import billing
from app.integrations.app_store import AppStoreNotConfigured, AppStoreVerificationFailed
from app.schemas.subscription import VerifyPurchaseRequest, VerifyPurchaseResponse
from app.services.iap_service import (
    IAPError,
    PurchaseAccountMismatch,
    PurchaseBoundToAnotherAccount,
    PurchaseRevoked,
    UnknownProduct,
)

_USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_USER = {"id": _USER_ID, "tier": "free", "email": "buyer@example.com"}
# `VerifyPurchaseRequest.signed_transaction` has `min_length=32`. The blob is opaque here —
# the stubbed verifier decides what it "contains", which is the whole point of the design
# (nothing the client asserts about the purchase is ever read).
_SIGNED = "eyJhbGciOiJFUzI1NiJ9." + "x" * 48

# What the stubbed verifier hands back: the dict shape `verify_signed_transaction` documents.
_VERIFIED = {
    "productId": "com.phan.caydex.pro.monthly",
    "transactionId": "2000000123456789",
    "originalTransactionId": "2000000123456789",
    "environment": "Sandbox",
}

# The two summary shapes `apply_verified_transaction` returns, copied from `iap_service.py`.
_SUBSCRIPTION_RESULT = {
    "tier": "pro",              # THIS transaction's tier …
    "winning_tier": "premium",  # … which is NOT the user's winning tier (they also hold Max)
    "status": "active",
    "original_transaction_id": "2000000123456789",
    "current_period_end": "2026-10-10T00:00:00+00:00",
    "was_replay": True,
    "was_stale": False,
}
_CREDIT_PACK_RESULT = {
    "kind": "credit_pack",
    "tier": "pro",
    "winning_tier": "pro",
    "status": "granted",
    "current_period_end": None,
    "was_replay": False,
    "credits_granted": 250,
    "credits_spendable": 1450,   # deliberately ≠ credits_granted, so a swapped key is visible
    "original_transaction_id": "2000000987654321",
}


class _FakeIAPService:
    """Stands in for `IAPService`. Records what the handler passed it and either returns
    `result` or raises `raises`, so each exception arm can be driven directly."""

    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def apply_verified_transaction(self, user_id, payload):
        self.calls.append((user_id, payload))
        if self._raises is not None:
            raise self._raises
        return dict(self._result)


class _FakeVerifier:
    """Stands in for `verify_signed_transaction`. Records the blob it was handed."""

    def __init__(self, payload=None, raises=None):
        self._payload = payload if payload is not None else dict(_VERIFIED)
        self._raises = raises
        self.calls: list[str] = []

    def __call__(self, signed_transaction):
        self.calls.append(signed_transaction)
        if self._raises is not None:
            raise self._raises
        return dict(self._payload)


def _unreachable(*_a, **_k):
    raise AssertionError(
        "verify_purchase reached a collaborator this test did not stub — the real App Store "
        "verifier or the real IAPService, both of which need Apple / Supabase"
    )


@pytest.fixture(autouse=True)
def _nothing_real_is_reachable(monkeypatch):
    """Both collaborators are pointed at a loud failure before every test, so an incomplete
    stub cannot fall through to `IAPService()` (which opens a Supabase client) or to the
    App Store verifier. Patched on `billing`, not on the source modules — see the module
    docstring for why that is the only binding that matters."""
    monkeypatch.setattr(billing, "verify_signed_transaction", _unreachable)
    monkeypatch.setattr(billing, "get_iap_service", _unreachable)


def _call(monkeypatch, *, verifier=None, service=None, user=None):
    """Invoke the handler the way FastAPI would, with its dependencies already resolved.

    `asyncio.run`, never `get_event_loop().run_until_complete` — the latter reads the
    process-wide loop policy and breaks under test reordering."""
    verifier = verifier if verifier is not None else _FakeVerifier()
    service = service if service is not None else _FakeIAPService(result=_SUBSCRIPTION_RESULT)
    monkeypatch.setattr(billing, "verify_signed_transaction", verifier)
    monkeypatch.setattr(billing, "get_iap_service", lambda: service)
    return asyncio.run(
        billing.verify_purchase(
            request=VerifyPurchaseRequest(signed_transaction=_SIGNED),
            user=dict(user if user is not None else _USER),
        )
    )


def _error(resp):
    """An error arm returns a `JSONResponse`; success returns the Pydantic model. Decode the
    former, and make a grant on an error test fail with a readable message."""
    assert not isinstance(resp, VerifyPurchaseResponse), (
        f"expected an error response, got a granted entitlement: {resp!r}"
    )
    return resp.status_code, json.loads(resp.body)


# ── 1. The happy path, and what the response is built FROM ──────────────────

def test_a_verified_subscription_is_granted_from_the_verified_payload(monkeypatch):
    """The docstring's promise: "Nothing the client asserts about what it bought is used."
    The service must receive the VERIFIED dict, not the blob the client sent, and it must be
    keyed on the dependency-resolved user id — the two inputs a forged receipt would love to
    control."""
    verifier, service = _FakeVerifier(), _FakeIAPService(result=_SUBSCRIPTION_RESULT)
    resp = _call(monkeypatch, verifier=verifier, service=service)

    assert isinstance(resp, VerifyPurchaseResponse)
    assert verifier.calls == [_SIGNED]
    assert service.calls == [(_USER_ID, _VERIFIED)], (
        "apply_verified_transaction must be handed the payload Apple's chain verified, "
        "under the caller's own id"
    )


def test_tier_is_the_winning_tier_not_this_transactions_tier(monkeypatch):
    """`VerifyPurchaseResponse.tier` is documented as the WINNING tier across all the user's
    subscriptions "so a Pro receipt replayed by a Max subscriber doesn't appear to demote
    them". `result["tier"]` is right there in the same dict and would compile — this is the
    mutation the string scans cannot see."""
    resp = _call(monkeypatch, service=_FakeIAPService(result=_SUBSCRIPTION_RESULT))
    assert resp.tier == "premium", "a Pro receipt replayed by a Max subscriber rendered as Pro"


def test_the_subscription_fields_map_one_to_one(monkeypatch):
    resp = _call(monkeypatch, service=_FakeIAPService(result=_SUBSCRIPTION_RESULT))
    assert resp.status == "active"
    assert resp.current_period_end == "2026-10-10T00:00:00+00:00"
    assert resp.was_replay is True


def test_the_subscription_path_defaults_the_pack_fields(monkeypatch):
    """The subscription summary carries no `kind` / `credits_*` keys. The handler's defaults
    are what an already-shipped iOS build decodes, so they are contract: "subscription", 0,
    None. A replay that reports credits it did not add is the specific lie the 0 prevents."""
    resp = _call(monkeypatch, service=_FakeIAPService(result=_SUBSCRIPTION_RESULT))
    assert resp.kind == "subscription"
    assert resp.credits_granted == 0
    assert resp.credits_spendable is None


def test_a_credit_pack_maps_its_own_fields(monkeypatch):
    """Each pack field from its OWN key. `credits_granted` and `credits_spendable` are given
    distinct values so a swap or a copy is visible."""
    resp = _call(monkeypatch, service=_FakeIAPService(result=_CREDIT_PACK_RESULT))
    assert resp.kind == "credit_pack"
    assert resp.credits_granted == 250
    assert resp.credits_spendable == 1450
    assert resp.tier == "pro"
    assert resp.status == "granted"
    assert resp.current_period_end is None
    assert resp.was_replay is False


def test_the_response_tracks_the_service_not_a_constant(monkeypatch):
    """Anti-vacuity. A handler gutted to `return VerifyPurchaseResponse(tier="premium",
    status="active")` would satisfy several assertions above one at a time. Two different
    service answers must produce two different responses."""
    a = _call(monkeypatch, service=_FakeIAPService(
        result={**_SUBSCRIPTION_RESULT, "winning_tier": "pro", "status": "expired"}
    ))
    b = _call(monkeypatch, service=_FakeIAPService(
        result={**_SUBSCRIPTION_RESULT, "winning_tier": "premium", "status": "active"}
    ))
    assert (a.tier, a.status) == ("pro", "expired")
    assert (b.tier, b.status) == ("premium", "active")


# ── 2. Verification arms — nothing may be applied past a failed check ────────

def test_a_misconfigured_verifier_is_a_retryable_503_and_applies_nothing(monkeypatch):
    """OUR fault, not a bad receipt: 503 so StoreKit keeps the transaction unfinished and
    redelivers once the operator fixes it. `SYSTEM_BUSY` defaults to 409, so the explicit
    `status_code=503` is load-bearing — dropping it turns a transient outage into a code the
    client treats as terminal."""
    service = _FakeIAPService(result=_SUBSCRIPTION_RESULT)
    status, body = _error(_call(
        monkeypatch,
        verifier=_FakeVerifier(raises=AppStoreNotConfigured("APP_STORE_BUNDLE_ID unset")),
        service=service,
    ))
    assert status == 503
    assert body["error_code"] == ErrorCode.SYSTEM_BUSY.value
    assert body["action"] == "retry_later"
    assert service.calls == [], "an unverified transaction reached apply_verified_transaction"


def test_the_503_arms_do_not_reuse_the_analysis_engine_copy(monkeypatch):
    """`SYSTEM_BUSY`'s registered user_message is about the report engine being at capacity.
    A purchase must not be answered with that — both 503 arms pass their own copy, and a
    dropped `user_message=` kwarg would silently fall back to the wrong sentence."""
    _, not_configured = _error(_call(
        monkeypatch, verifier=_FakeVerifier(raises=AppStoreNotConfigured("x")),
    ))
    _, apply_failed = _error(_call(
        monkeypatch, service=_FakeIAPService(raises=IAPError("supabase insert failed")),
    ))
    for body in (not_configured, apply_failed):
        assert "analysis engine" not in body["user_message"].lower()
        assert "purchase" in body["user_message"].lower()


def test_a_rejected_signature_is_a_terminal_400_that_does_not_echo_the_reason(monkeypatch):
    """Hostile or corrupt input. The reason is an oracle: "an error that explains why it
    rejected you is an oracle for forging one that passes". It goes to the log, never the
    body — in ANY field."""
    reason = "leaf certificate not chained to Apple Root CA G3; expected env Sandbox"
    service = _FakeIAPService(result=_SUBSCRIPTION_RESULT)
    status, body = _error(_call(
        monkeypatch,
        verifier=_FakeVerifier(raises=AppStoreVerificationFailed(reason)),
        service=service,
    ))
    assert status == 400
    assert body["error_code"] == ErrorCode.INVALID_INPUT.value
    assert reason not in json.dumps(body), "Apple's rejection reason leaked to the client"
    assert "Root CA" not in json.dumps(body)
    assert service.calls == [], "a transaction that failed Apple's check was still applied"


def test_an_unrelated_verifier_fault_is_not_turned_into_a_grant(monkeypatch):
    """Only the two typed App Store exceptions are handled. Anything else is a programming
    error and must surface as one (the app-level handler makes it a 500) — never be absorbed
    into a success, and never reach the entitlement step."""
    service = _FakeIAPService(result=_SUBSCRIPTION_RESULT)
    with pytest.raises(RuntimeError):
        _call(
            monkeypatch,
            verifier=_FakeVerifier(raises=RuntimeError("ocsp socket closed")),
            service=service,
        )
    assert service.calls == []


# ── 3. Entitlement arms — each lands on its own code, status and action ──────

@pytest.mark.parametrize(
    "exc, status, code, action",
    [
        (PurchaseRevoked("refunded 2026-09-01"), 409, ErrorCode.PURCHASE_REVOKED, "contact_support"),
        (UnknownProduct("no credit_packs row"), 400, ErrorCode.INVALID_INPUT, None),
        (PurchaseAccountMismatch("appAccountToken names bbbb"), 409, ErrorCode.PURCHASE_ACCOUNT_MISMATCH, "sign_in"),
        (PurchaseBoundToAnotherAccount("owned by bbbb"), 409, ErrorCode.PURCHASE_ALREADY_LINKED, "contact_support"),
        (IAPError("supabase insert failed"), 503, ErrorCode.SYSTEM_BUSY, "retry_later"),
    ],
    ids=["revoked", "unknown_product", "account_mismatch", "bound_to_another", "generic"],
)
def test_each_entitlement_failure_lands_on_its_own_arm(monkeypatch, exc, status, code, action):
    """The status is what StoreKit reads (5xx = retry, 4xx = stop) and the code + action is
    what the iOS client reads (finish the transaction, or keep it, or sign in). Every row here
    is a distinct client behaviour, and a wrong number on any of them either loops a purchase
    forever or deletes one."""
    got_status, body = _error(_call(monkeypatch, service=_FakeIAPService(raises=exc)))
    assert got_status == status
    assert body["error_code"] == code.value
    assert body["action"] == action


def test_revoked_carries_the_finishable_marker(monkeypatch):
    """`details.transaction == "revoked"` is the flag the client keys `finish()` on. Values
    must stay flat scalars — iOS `AnyCodable` decodes String/Int/Double/Bool only."""
    _, body = _error(_call(monkeypatch, service=_FakeIAPService(raises=PurchaseRevoked("refunded"))))
    assert body["details"] == {"transaction": "revoked"}


def test_unknown_product_names_the_verified_product_id(monkeypatch):
    """"A REAL purchase we can't price" — the message must say WHICH product so support can
    map it, and it must come from the VERIFIED payload."""
    _, body = _error(_call(
        monkeypatch,
        verifier=_FakeVerifier(payload={**_VERIFIED, "productId": "com.phan.caydex.pack.mystery"}),
        service=_FakeIAPService(raises=UnknownProduct("no credit_packs row")),
    ))
    assert "com.phan.caydex.pack.mystery" in body["message"]


def test_unknown_product_survives_an_empty_verified_payload(monkeypatch):
    """The degrade path's EMPTY branch: a verifier that returns `{}` (no productId at all)
    still has to produce the 400, not an AttributeError/KeyError from formatting the message.
    `payload.get('productId')` is the difference."""
    status, body = _error(_call(
        monkeypatch,
        verifier=_FakeVerifier(payload={}),
        service=_FakeIAPService(raises=UnknownProduct("transaction has no productId")),
    ))
    assert status == 400
    assert body["error_code"] == ErrorCode.INVALID_INPUT.value


# ── 4. The subclass ordering, EXERCISED rather than grepped ──────────────────
#
# `PurchaseAccountMismatch` subclasses `PurchaseBoundToAnotherAccount`, and both subclass
# `IAPError`. Python takes the first matching `except`, so the order of the arms decides which
# code a real exception gets. The existing tests check the ORDER OF THE TEXT; these check
# where an instance actually lands, which is the only thing the client ever sees.

def test_the_hierarchy_this_file_relies_on_still_holds():
    """If someone flattens `PurchaseAccountMismatch` out from under its parent, the ordering
    tests below stop testing an ordering at all (any order would pass). Pin the premise."""
    assert issubclass(PurchaseAccountMismatch, PurchaseBoundToAnotherAccount)
    assert issubclass(PurchaseBoundToAnotherAccount, IAPError)
    assert issubclass(PurchaseRevoked, IAPError)
    assert issubclass(UnknownProduct, IAPError)


def test_a_mismatch_lands_on_its_own_arm_not_the_parents(monkeypatch):
    """🔴 The money assertion. PURCHASE_ALREADY_LINKED tells iOS to `finish()` the transaction.
    On a mismatch NOBODY was credited, so finishing deletes a purchase the user paid for with
    no redelivery left to repair it. A parent arm placed first — or the two codes swapped —
    produces exactly that, with every source substring still present."""
    _, body = _error(_call(
        monkeypatch, service=_FakeIAPService(raises=PurchaseAccountMismatch("token names bbbb")),
    ))
    assert body["error_code"] == ErrorCode.PURCHASE_ACCOUNT_MISMATCH.value
    assert body["error_code"] != ErrorCode.PURCHASE_ALREADY_LINKED.value
    assert body["action"] == "sign_in", "the purchase is intact; signing in as the buyer claims it"


def test_the_parent_still_lands_on_already_linked(monkeypatch):
    """The other half of the swap: the parent must NOT be pulled onto the mismatch arm (which
    would tell a user whose purchase was already credited elsewhere to keep redelivering it),
    and must not fall through to the retryable 503 either."""
    status, body = _error(_call(
        monkeypatch, service=_FakeIAPService(raises=PurchaseBoundToAnotherAccount("owned by bbbb")),
    ))
    assert body["error_code"] == ErrorCode.PURCHASE_ALREADY_LINKED.value
    assert body["error_code"] != ErrorCode.PURCHASE_ACCOUNT_MISMATCH.value
    assert status == 409 and status < 500, "a 5xx here re-opens the redelivery-forever loop"


def test_revoked_does_not_fall_to_the_retryable_arm(monkeypatch):
    """`PurchaseRevoked` is an `IAPError`. If its arm is removed or moved below the generic
    one, a refunded purchase becomes a 503 "reopen the app shortly" — redelivered on every
    launch, forever, against a condition that can never clear."""
    status, body = _error(_call(monkeypatch, service=_FakeIAPService(raises=PurchaseRevoked("refunded"))))
    assert body["error_code"] != ErrorCode.SYSTEM_BUSY.value
    assert status != 503


# ── 5. Garbage falls CLOSED ───────────────────────────────────────────────────

@pytest.mark.parametrize("missing", ["winning_tier", "status"])
def test_a_service_result_without_a_tier_or_status_is_not_defaulted_into_a_grant(monkeypatch, missing):
    """The three pack fields are `.get`-defaulted on purpose; these two are hard-indexed on
    purpose. A summary that cannot say what tier was granted is a service bug and must
    surface as one — `result.get("winning_tier", "premium")` would grant Max to everyone the
    service half-answered, and `.get(..., "free")` would render a paid purchase as a
    demotion. Either way the caller must see an error, not a 200."""
    result = {k: v for k, v in _SUBSCRIPTION_RESULT.items() if k != missing}
    try:
        resp = _call(monkeypatch, service=_FakeIAPService(result=result))
    except Exception as e:  # noqa: BLE001 — a propagated error IS the app-level 500
        # Today this is the KeyError from the hard index. Any other propagated exception
        # is still "the caller saw an error" — except an AssertionError, which is this
        # file's own `_unreachable` and means the harness, not the subject, misfired.
        assert not isinstance(e, AssertionError), e
        return
    # A handler that catches the gap itself must still answer with an ERROR, never a grant.
    assert not isinstance(resp, VerifyPurchaseResponse), (
        f"a summary missing {missing!r} was defaulted into a granted entitlement: {resp!r}"
    )
    assert resp.status_code >= 400


def test_an_unrelated_service_fault_propagates_instead_of_granting(monkeypatch):
    """Only `IAPError` and its subclasses are handled. A foreign exception from the
    entitlement step is a programming error; it must neither become a 200 nor be quietly
    relabelled as a retryable outage."""
    with pytest.raises(RuntimeError):
        _call(monkeypatch, service=_FakeIAPService(raises=RuntimeError("attribute typo")))


# ── 6. Every error arm honours the iOS `APIErrorResponse` contract ────────────

_EVERY_ERROR_ARM = [
    # (raised by the verifier, raised by the service) — exactly one of the two per row
    pytest.param(AppStoreNotConfigured("x"), None, id="not_configured"),
    pytest.param(AppStoreVerificationFailed("x"), None, id="verification_failed"),
    pytest.param(None, PurchaseRevoked("x"), id="revoked"),
    pytest.param(None, UnknownProduct("x"), id="unknown_product"),
    pytest.param(None, PurchaseAccountMismatch("x"), id="account_mismatch"),
    pytest.param(None, PurchaseBoundToAnotherAccount("x"), id="bound_to_another"),
    pytest.param(None, IAPError("x"), id="generic"),
]


@pytest.mark.parametrize("verify_exc, apply_exc", _EVERY_ERROR_ARM)
def test_every_error_arm_matches_the_api_error_response_shape(monkeypatch, verify_exc, apply_exc):
    """Invariant #3: the iOS decoder needs every one of these keys or it cannot render an
    actionable error, and `details` values must be flat scalars (`AnyCodable` yields "" for
    anything nested). Seven arms, one shape."""
    status, body = _error(_call(
        monkeypatch,
        verifier=_FakeVerifier(raises=verify_exc),
        service=_FakeIAPService(result=_SUBSCRIPTION_RESULT, raises=apply_exc),
    ))

    assert 400 <= status < 600
    assert {"error_code", "message", "user_message", "action", "details"} <= body.keys()
    assert body["error_code"] in {c.value for c in ErrorCode}
    assert body["user_message"].strip(), "an empty user_message renders as a blank alert"
    assert all(
        isinstance(v, (str, int, float, bool, type(None))) for v in body["details"].values()
    ), f"nested value in details: {body['details']!r}"
