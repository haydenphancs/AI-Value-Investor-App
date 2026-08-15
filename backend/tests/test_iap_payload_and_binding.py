"""Three payment-path defects found by the adversarial review.

--- 1. `_to_dict` returned {} for EVERY real Apple transaction (launch-blocking) ---

Apple's models are `attrs @define` (slots=True). All 42 fields of
`JWSTransactionDecodedPayload` live in `__slots__`, and the `__dict__` inherited from the
non-slotted base `AttrsRawValueAware` is present but **permanently empty** — assignment never
populates it. `_to_dict` flattened `__dict__`, so it returned `{}` after a SUCCESSFUL
signature verification, and `verify_signed_transaction` then raised "missing transactionId".
`POST /billing/verify` answered 400 for every genuine purchase: Apple charges the customer,
the app grants nothing.

35 IAP tests passed throughout, because every one of them feeds a plain dict and none ever
constructs a real library model. That is precisely the gap this file closes — the first
assertion below goes through the actual Apple class.

--- 2. A re-subscribe crashed on the UNIQUE constraint ---

`subscriptions_user_id_key UNIQUE (user_id)` means a user holds at most one row. The
idempotency lookup keyed on `original_transaction_id` alone, so a re-subscribe or a move to a
different subscription group — both of which mint a FRESH originalTransactionId — found
nothing and took the INSERT branch, violating the constraint. `IAPError` → 503, after the
customer had already been charged.

--- 3. A transaction could be REBOUND to a different account ---

The same lookup matched on the transaction alone while the update payload carried `user_id`,
so submitting a transaction already owned by user A moved A's row to user B. A silently drops
to free at the next reconcile; B is granted the tier and its credit allocation. The UNIQUE
constraint does not catch it, because B had no row. An Apple-signed transaction proves a
purchase occurred — never who is entitled to it.

No network, no Supabase, no Apple: the table is a fake, the transaction is a real library model.
"""
from __future__ import annotations

import pytest

from app.integrations.app_store import _to_dict
from app.services.iap_service import (
    IAPError,
    IAPService,
    PurchaseAccountMismatch,
    PurchaseBoundToAnotherAccount,
)


# ---------------------------------------------------------------------------
# 1. Flattening a REAL Apple model
# ---------------------------------------------------------------------------


def _real_transaction(**overrides):
    from appstoreserverlibrary.models.JWSTransactionDecodedPayload import (
        JWSTransactionDecodedPayload,
    )

    txn = JWSTransactionDecodedPayload()
    txn.transactionId = overrides.get("transactionId", "2000000099887766")
    txn.originalTransactionId = overrides.get("originalTransactionId", "2000000099887766")
    txn.productId = overrides.get("productId", "com.phan.caydex.pro.monthly")
    txn.expiresDate = overrides.get("expiresDate", 1790000000000)
    return txn


def test_real_apple_model_flattens_to_a_populated_dict():
    """THE launch blocker. Against the real attrs/slots class, not a hand-made dict."""
    out = _to_dict(_real_transaction())
    assert out, "_to_dict returned {} for a real Apple model — every purchase would 400"
    assert out["transactionId"] == "2000000099887766"
    assert out["productId"] == "com.phan.caydex.pro.monthly"
    assert out["expiresDate"] == 1790000000000


def test_flattened_payload_satisfies_the_verifier_contract():
    """`verify_signed_transaction` rejects a payload without transactionId. Prove the
    flattened shape clears that bar, which is what actually broke."""
    assert _to_dict(_real_transaction()).get("transactionId")


def test_none_values_are_dropped_not_rendered_as_null():
    out = _to_dict(_real_transaction())
    assert all(v is not None for v in out.values())
    assert "revocationDate" not in out  # unset → absent, so status_for_transaction sees no revoke


def test_enums_are_flattened_to_raw_values():
    """Callers must never receive a library enum — the integration layer returns plain types."""
    from appstoreserverlibrary.models.JWSTransactionDecodedPayload import (
        JWSTransactionDecodedPayload,
    )
    from appstoreserverlibrary.models.Type import Type

    txn = _real_transaction()
    txn.type = Type.AUTO_RENEWABLE_SUBSCRIPTION
    out = _to_dict(txn)
    assert out["type"] == Type.AUTO_RENEWABLE_SUBSCRIPTION.value
    assert not isinstance(out["type"], Type)
    assert isinstance(txn, JWSTransactionDecodedPayload)


def test_none_and_non_model_inputs_still_degrade_quietly():
    assert _to_dict(None) == {}
    assert _to_dict("not a model") == {}


# ---------------------------------------------------------------------------
# 2 + 3. Subscription binding
# ---------------------------------------------------------------------------


class _Table:
    def __init__(self, store, log):
        self.store, self.log = store, log
        self._op = None
        self._payload = None
        self._filters = {}

    def select(self, *_a):
        self._op = "select"
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self._op == "insert":
            # Model `subscriptions_user_id_key UNIQUE (user_id)`.
            if any(r["user_id"] == self._payload["user_id"] for r in self.store):
                raise RuntimeError(
                    'duplicate key value violates unique constraint "subscriptions_user_id_key"'
                )
            row = dict(self._payload)
            row["id"] = f"sub-{len(self.store) + 1}"
            self.store.append(row)
            self.log.append(("insert", row["id"]))
            return type("R", (), {"data": [row]})()

        matched = [
            r for r in self.store
            if all(r.get(k) == v for k, v in self._filters.items())
        ]
        if self._op == "update":
            for r in matched:
                r.update(self._payload)
            self.log.append(("update", [r["id"] for r in matched]))
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _SB:
    def __init__(self, store):
        self.store, self.log = store, []

    def table(self, _name):
        return _Table(self.store, self.log)


def _svc(store):
    svc = IAPService.__new__(IAPService)  # bypass __init__ (wires Supabase)
    svc.supabase = _SB(store)
    svc.reconcile_user_tier = lambda uid: "pro"  # isolate the binding logic
    return svc


_PRO = {
    "productId": "com.phan.caydex.pro.monthly",
    "expiresDate": 4102444800000,  # year 2100 — comfortably active
}


def test_resubscribe_with_a_new_transaction_id_updates_instead_of_violating_unique():
    """Re-subscribe / new subscription group → fresh originalTransactionId, same user."""
    store = [{
        "id": "sub-1", "user_id": "user-A", "tier": "pro", "status": "expired",
        "original_transaction_id": "OLD-111", "current_period_end": None,
    }]
    svc = _svc(store)

    result = svc.apply_transaction(
        "user-A", {**_PRO, "originalTransactionId": "NEW-222", "transactionId": "NEW-222"}
    )

    assert len(store) == 1, "inserted a second row — subscriptions_user_id_key would reject it"
    assert store[0]["original_transaction_id"] == "NEW-222"
    assert store[0]["status"] == "active"
    assert result["was_replay"] is False, "a genuinely new transaction is not a replay"


def test_a_transaction_bound_to_another_account_is_refused():
    """THE entitlement-theft case: A's row must not move to B."""
    store = [{
        "id": "sub-1", "user_id": "user-A", "tier": "premium", "status": "active",
        "original_transaction_id": "TXN-999", "current_period_end": "2100-01-01T00:00:00+00:00",
    }]
    svc = _svc(store)

    with pytest.raises(IAPError):
        svc.apply_transaction(
            "user-B", {**_PRO, "originalTransactionId": "TXN-999", "transactionId": "TXN-999"}
        )

    assert store[0]["user_id"] == "user-A", "entitlement was transferred to another account"
    assert store[0]["tier"] == "premium", "the original owner's tier was overwritten"


def test_replaying_your_own_transaction_is_idempotent():
    """StoreKit replays `Transaction.updates` on every launch — this must not multiply rows."""
    store = [{
        "id": "sub-1", "user_id": "user-A", "tier": "pro", "status": "active",
        "original_transaction_id": "TXN-555", "current_period_end": None,
    }]
    svc = _svc(store)

    result = svc.apply_transaction(
        "user-A", {**_PRO, "originalTransactionId": "TXN-555", "transactionId": "TXN-555"}
    )

    assert len(store) == 1
    assert result["was_replay"] is True


def test_first_ever_purchase_still_inserts():
    store = []
    svc = _svc(store)

    result = svc.apply_transaction(
        "user-A", {**_PRO, "originalTransactionId": "TXN-1", "transactionId": "TXN-1"}
    )

    assert len(store) == 1
    assert store[0]["user_id"] == "user-A"
    assert result["was_replay"] is False


def test_a_row_with_no_owner_recorded_does_not_block_the_apply():
    """Defensive: a legacy row missing user_id must not be treated as someone else's."""
    store = [{
        "id": "sub-1", "user_id": None, "tier": "pro", "status": "active",
        "original_transaction_id": "TXN-777", "current_period_end": None,
    }]
    svc = _svc(store)

    result = svc.apply_transaction(
        "user-A", {**_PRO, "originalTransactionId": "TXN-777", "transactionId": "TXN-777"}
    )
    assert result["was_replay"] is True
    assert store[0]["user_id"] == "user-A"


# ── First-delivery cross-account guard (appAccountToken) ──────────────────────────────


def test_a_first_delivery_for_another_account_is_refused_before_any_write():
    """The gap the rebind check above CANNOT cover.

    `test_a_transaction_bound_to_another_account_is_refused` needs a `subscriptions` row to
    already exist. On a FIRST delivery there is none — so if A's purchase completes at Apple
    but the verify never lands (offline, app killed, signed out), the transaction stays
    unfinished, and when B signs in on the same device `drainUnfinishedTransactions()`
    re-submits it under B's bearer token. Nothing distinguished that from B's own purchase:
    B was granted the tier AND its credit allocation, and A could never be given the row back.

    `appAccountToken` is the only evidence of who paid, and Apple returns it inside the
    SIGNED payload so the client cannot forge it independently of the transaction.
    """
    store = []
    svc = _svc(store)

    with pytest.raises(PurchaseAccountMismatch):
        svc.apply_transaction(
            "user-B",
            {**_PRO, "originalTransactionId": "TXN-NEW", "transactionId": "TXN-NEW",
             "appAccountToken": "user-A"},
        )

    assert store == [], "a refused first delivery must write nothing at all"


def test_the_mismatch_is_raised_before_a_row_exists_so_the_client_keeps_the_transaction():
    """`PurchaseAccountMismatch` must stay a distinct type from its parent.

    The parent means "we already credited someone else, finish it so Apple stops
    redelivering". This one means "nobody has been credited yet" — finishing it would destroy
    a purchase the buyer paid for, with no redelivery left to repair it.
    """
    assert issubclass(PurchaseAccountMismatch, PurchaseBoundToAnotherAccount)
    assert PurchaseAccountMismatch is not PurchaseBoundToAnotherAccount


def test_a_matching_token_is_accepted_case_insensitively():
    """uuids round-trip through Apple with inconsistent case; a case difference is not theft."""
    store = []
    svc = _svc(store)
    result = svc.apply_transaction(
        "USER-a",
        {**_PRO, "originalTransactionId": "TXN-OK", "transactionId": "TXN-OK",
         "appAccountToken": "user-A"},
    )
    assert result["was_replay"] is False
    assert store and store[0]["user_id"] == "USER-a"


def test_a_transaction_without_a_token_still_applies():
    """Older clients stamped no token. Refusing them would strand real purchases; the
    per-transaction dedup and the rebind check still bound the damage."""
    store = []
    svc = _svc(store)
    result = svc.apply_transaction(
        "user-A", {**_PRO, "originalTransactionId": "TXN-OLD", "transactionId": "TXN-OLD"}
    )
    assert result["was_replay"] is False
    assert store and store[0]["user_id"] == "user-A"


def test_the_webhook_call_site_opts_out_of_the_token_check():
    """Apple's notification is not client-submitted: `user_id` is resolved from our OWN
    subscriptions row, so there is no cross-account question — and raising would return a
    non-200 that Apple retries for days against a legacy mis-bound row.

    ⚠️ Drives the REAL webhook entry point (`apply_notification`), not `apply_transaction`
    directly. Calling the inner method with `client_submitted=False` would pin only the
    parameter's behaviour and pass even if the webhook call site dropped the kwarg — which is
    exactly the regression this exists to catch. Verified by mutation: deleting
    `client_submitted=False` at iap_service.py's `apply_notification` call site turns this red.
    """
    store = [{
        "id": "sub-1", "user_id": "user-B", "tier": "pro", "status": "active",
        "original_transaction_id": "TXN-HOOK", "current_period_end": None,
    }]
    svc = _svc(store)

    # The stored owner is user-B; the signed payload names user-A. A client submitting this
    # would (correctly) be refused — Apple must not be.
    notification = {
        "notificationType": "DID_RENEW",
        "signedDate": 1_700_000_000_000,
        "data": {"signedTransactionInfo": "ignored-by-the-fake"},
    }
    transaction = {**_PRO, "originalTransactionId": "TXN-HOOK", "transactionId": "TXN-HOOK",
                   "appAccountToken": "user-A"}

    outcome, resolved = svc.apply_notification(notification, transaction)

    assert "account" not in str(outcome).lower(), (
        f"the webhook was refused on a cross-account check: {outcome}"
    )
    assert store[0]["user_id"] == "user-B"
