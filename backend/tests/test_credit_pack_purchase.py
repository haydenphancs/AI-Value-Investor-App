"""Consumable credit-pack purchases: routing, exactly-once granting, and refunds.

The service-layer counterpart to `test_credit_pool_isolation.py` (which models the SQL). Here
the real `IAPService` / `CreditService` code runs against a fake Supabase whose `rpc` handler
MODELS `add_purchased_credits` / `revoke_purchased_credits` rather than rubber-stamping them —
same discipline as `test_iap_entitlement.FakeSupabase`, and for the same reason: a fake that
returns bland success for every rpc lets a money bug pass a green suite.

Weighted toward what goes wrong:

* **Routing.** A pack must never reach `tier_for_product` (it would 400 a real purchase) and a
  subscription must never reach the credit path (it would grant credits instead of a tier).
* **Replay.** `Transaction.updates` redelivers on every launch. A replay must grant nothing
  and still report SUCCESS, because success is what tells the client it may finish the
  transaction — an error there strands it forever.
* **Wrong account.** Two distinct cases: a second delivery of a transaction we already own
  (caught by the dedup row) and a FIRST delivery into someone else's session after an account
  switch (caught only by `appAccountToken`).
* **Our own failures.** The user has already been charged by Apple, so a DB failure must
  surface as retryable and leave the transaction unfinished — never as a success.
* **Refunds.** Apple's REFUND must actually reach the purchased pool; before this path existed
  it resolved to no user and was silently dropped.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services import credit_service as cs
from app.services import iap_service as svc

_USER = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
_OTHER = "11110000-ffff-4eee-8ddd-ccccbbbbaaaa"
_PRO = "com.phan.caydex.pro.monthly"
_PACK_PLUS = "com.phan.caydex.credits.plus"
_PACK_MEGA = "com.phan.caydex.credits.mega"

_PACK_ROWS = {
    _PACK_PLUS: {"product_id": _PACK_PLUS, "credits": 250, "price_cents": 499,
                 "display_name": "Plus"},
    _PACK_MEGA: {"product_id": _PACK_MEGA, "credits": 1200, "price_cents": 1999,
                 "display_name": "Mega"},
}


def _pack_txn(product=_PACK_PLUS, txn_id="2000000000000001", **extra):
    payload = {
        "transactionId": txn_id,
        "originalTransactionId": txn_id,
        "productId": product,
        "bundleId": settings.IAP_BUNDLE_ID,
        "environment": "Production",
        "type": "Consumable",
    }
    payload.update(extra)
    return payload


class _Q:
    def __init__(self, db, table, fail):
        self._db, self._table, self._fail = db, table, fail
        self._op = None
        self._values = None
        self._filters: dict = {}

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, values):
        self._op, self._values = "insert", values
        return self

    def update(self, values):
        self._op, self._values = "update", values
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._table in self._fail:
            raise RuntimeError(f"{self._table} unavailable")
        rows = self._db.setdefault(self._table, [])
        if self._op == "select":
            out = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
            return type("R", (), {"data": out})()
        if self._op == "insert":
            rows.append(dict(self._values))
            return type("R", (), {"data": [rows[-1]]})()
        if self._op == "update":
            touched = []
            for r in rows:
                if all(r.get(k) == v for k, v in self._filters.items()):
                    r.update(self._values)
                    touched.append(r)
            return type("R", (), {"data": touched})()
        return type("R", (), {"data": []})()


class FakeSupabase:
    """Models `add_purchased_credits` / `revoke_purchased_credits` (migration 117).

    Keep in step with the SQL. The full arithmetic — spend ordering, refund splits, the
    monthly-reset isolation — is exercised in `test_credit_pool_isolation.py`; here the model
    only needs to be faithful about the OUTCOMES the service branches on.
    """

    def __init__(self, fail=(), packs=None, tier="free"):
        # dict(row) per row, not list(...values()): several tests mutate a catalog row to
        # simulate a corrupt one, and sharing the module-level dicts leaked that mutation
        # into every later test in the file.
        source = packs if packs is not None else _PACK_ROWS
        self.db = {
            "users": [{"id": _USER, "tier": tier}, {"id": _OTHER, "tier": "free"}],
            "credit_packs": [dict(r) for r in source.values()],
            "subscriptions": [],
            "credit_purchases": [],
        }
        self._fail = set(fail)
        self.rpcs: list = []
        self.purchased_total = 0
        self.granted_remaining = 50
        self._purchases: dict[tuple[str, str], dict] = {}

    def table(self, name):
        return _Q(self.db, name, self._fail)

    @property
    def spendable(self) -> int:
        return self.granted_remaining + self.purchased_total

    def rpc(self, name, params):
        if "rpc" in self._fail or name in self._fail:
            raise RuntimeError(f"rpc {name} unavailable")
        self.rpcs.append((name, params))
        result = None

        if name == "add_purchased_credits":
            key = (params["p_environment"], params["p_transaction_id"])
            credits = params["p_credits"]
            if credits is None or credits <= 0:
                result = {"outcome": "invalid", "reason": "non_positive_credits"}
            elif key in self._purchases:
                row = self._purchases[key]
                if row["user_id"] != params["p_user_id"]:
                    result = {"outcome": "conflict", "owner_user_id": row["user_id"]}
                else:
                    result = {"outcome": "replay", "credits": row["credits"],
                              "spendable": self.spendable}
            else:
                self._purchases[key] = {"user_id": params["p_user_id"], "credits": credits,
                                        "revoked_at": None}
                self.db["credit_purchases"].append({
                    "user_id": params["p_user_id"],
                    "transaction_id": params["p_transaction_id"],
                    "original_transaction_id": params.get("p_original_transaction_id"),
                    "environment": params["p_environment"],
                })
                self.purchased_total += credits
                result = {"outcome": "granted", "credits": credits,
                          "spendable": self.spendable}

        elif name == "revoke_purchased_credits":
            key = (params["p_environment"], params["p_transaction_id"])
            row = self._purchases.get(key)
            if row is None:
                result = {"outcome": "unknown"}
            elif row["revoked_at"] is not None:
                result = {"outcome": "already_revoked", "spendable": self.spendable}
            else:
                row["revoked_at"] = "now"
                self.purchased_total -= row["credits"]
                result = {"outcome": "revoked", "spendable": self.spendable,
                          "reclaimed": row["credits"]}

        return type("R", (), {"execute": lambda *_a, **_k: type("D", (), {"data": result})()})()

    def rpc_names(self) -> list[str]:
        return [n for n, _ in self.rpcs]


@pytest.fixture
def sb(monkeypatch):
    fake = FakeSupabase()
    # `apply_consumable_transaction` constructs `CreditService()`, whose __init__ calls
    # get_supabase(). Patch the module-level name so the real service code runs against
    # the fake instead of reaching for a real client.
    monkeypatch.setattr(cs, "get_supabase", lambda: fake)
    return fake


def _service(sb) -> svc.IAPService:
    s = svc.IAPService.__new__(svc.IAPService)
    s.supabase = sb
    return s


# ── Routing ───────────────────────────────────────────────────────────────────────────────

def test_product_kind_routes_subscriptions_and_packs():
    assert svc.product_kind(_PRO) == "subscription"
    assert svc.product_kind(settings.IAP_PRODUCT_MAX_MONTHLY) == "subscription"
    assert svc.product_kind(_PACK_PLUS) == "credit_pack"
    assert svc.product_kind(_PACK_MEGA) == "credit_pack"


def test_product_kind_raises_for_anything_unmapped():
    """A verified purchase we cannot classify is a real anomaly — someone bought something
    real that we can't price. It must never resolve to a default."""
    for bad in ("com.phan.caydex.enterprise.yearly", "", None):
        with pytest.raises(svc.UnknownProduct):
            svc.product_kind(bad)


def test_tier_for_product_still_refuses_a_credit_pack():
    """`tier_for_product` answers exactly one question — which SUBSCRIPTION tier is this. If it
    ever learned about packs, an unmapped product would become resolvable and the honest 400
    would be lost."""
    with pytest.raises(svc.UnknownProduct):
        svc.tier_for_product(_PACK_PLUS)


def test_a_retired_pack_is_still_recognised_as_a_pack():
    """Routing is by NAMING PREFIX, not by a `credit_packs` lookup, so a pack removed from the
    catalog is still diagnosed as a pack rather than reported as an unmapped subscription."""
    assert svc.is_credit_pack_product("com.phan.caydex.credits.retired2024")
    assert svc.product_kind("com.phan.caydex.credits.retired2024") == "credit_pack"


def test_pack_purchase_writes_no_subscription_and_reconciles_no_tier(sb):
    """A pack is a balance, not an entitlement. Writing a `subscriptions` row for one would
    hand the buyer a tier they did not purchase."""
    out = _service(sb).apply_verified_transaction(_USER, _pack_txn())

    assert out["kind"] == "credit_pack"
    assert sb.db["subscriptions"] == []
    assert "grant_tier_upgrade" not in sb.rpc_names()
    assert "ensure_credit_period" not in sb.rpc_names()
    assert "revoke_tier_credits" not in sb.rpc_names()


def test_subscription_purchase_never_grants_pack_credits(sb):
    """The mirror check: the subscription path must not touch the purchased pool."""
    from datetime import datetime, timedelta, timezone
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000
    _service(sb).apply_verified_transaction(
        _USER,
        {"transactionId": "1", "originalTransactionId": "1", "productId": _PRO,
         "expiresDate": expires},
    )
    assert "add_purchased_credits" not in sb.rpc_names()
    assert sb.purchased_total == 0


# ── Granting ──────────────────────────────────────────────────────────────────────────────

def test_pack_grant_returns_the_credits_and_the_new_balance(sb):
    out = _service(sb).apply_verified_transaction(_USER, _pack_txn())

    assert out["credits_granted"] == 250
    assert out["was_replay"] is False
    assert out["status"] == "granted"
    assert out["credits_spendable"] == 300      # 50 granted + 250 purchased
    assert sb.purchased_total == 250


def test_replayed_delivery_grants_nothing_but_still_succeeds(sb):
    """StoreKit replays on EVERY launch. Success is what lets the client call
    `Transaction.finish()` — answering with an error would strand the transaction forever."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn())

    for _ in range(3):
        out = service.apply_verified_transaction(_USER, _pack_txn())
        assert out["was_replay"] is True
        assert out["status"] == "duplicate"
        assert out["credits_granted"] == 0, \
            "must not claim credits were added that the user can check against their balance"

    assert sb.purchased_total == 250, "three replays must not grant three packs"


def test_two_distinct_purchases_of_the_same_pack_both_grant(sb):
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-A"))
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-B"))
    assert sb.purchased_total == 500


def test_pack_response_reports_the_users_real_tier_not_free(sb, monkeypatch):
    """The client surfaces `tier` as the purchase result. Returning "free" for a pack bought by
    a Pro subscriber would render as a demotion."""
    for row in sb.db["users"]:
        if row["id"] == _USER:
            row["tier"] = "pro"

    out = _service(sb).apply_verified_transaction(_USER, _pack_txn())
    assert out["tier"] == "pro"
    assert out["winning_tier"] == "pro"


# ── Wrong account ─────────────────────────────────────────────────────────────────────────

def test_second_delivery_by_another_account_is_terminal_not_retryable(sb):
    """Ownership of an Apple transaction never moves, so this must be 409 (client finishes the
    transaction) and not a 5xx (client retries forever against a condition that cannot clear)."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn())
    before = sb.purchased_total

    with pytest.raises(svc.PurchaseBoundToAnotherAccount):
        service.apply_verified_transaction(_OTHER, _pack_txn())

    assert sb.purchased_total == before


def test_first_delivery_into_a_different_session_is_refused_via_app_account_token(sb):
    """The case the dedup row CANNOT catch: A buys, the verify call fails, A signs out, B signs
    in on the same device, and `Transaction.updates` redelivers into B's session. There is no
    prior row, so `appAccountToken` — stamped by the client and returned inside Apple's SIGNED
    payload — is the only evidence of who actually paid."""
    with pytest.raises(svc.PurchaseBoundToAnotherAccount):
        _service(sb).apply_verified_transaction(
            _OTHER, _pack_txn(appAccountToken=_USER)
        )
    assert sb.purchased_total == 0


def test_matching_app_account_token_grants_normally(sb):
    out = _service(sb).apply_verified_transaction(
        _USER, _pack_txn(appAccountToken=_USER.upper())   # Apple may vary the case
    )
    assert out["credits_granted"] == 250


def test_missing_app_account_token_still_grants(sb):
    """An older client, or a purchase made before the token was wired in, must not be refused —
    the per-transaction dedup still prevents double-granting."""
    out = _service(sb).apply_verified_transaction(_USER, _pack_txn())
    assert out["credits_granted"] == 250


# ── Catalog integrity ─────────────────────────────────────────────────────────────────────

def test_pack_with_no_catalog_row_raises_unknown_product(sb):
    """Verified but unpriced. Same honest 400 an unmapped subscription id gets — never a
    silent zero-credit grant."""
    with pytest.raises(svc.UnknownProduct):
        _service(sb).apply_verified_transaction(
            _USER, _pack_txn(product="com.phan.caydex.credits.ghost")
        )
    assert sb.purchased_total == 0


@pytest.mark.parametrize("bad_credits", [0, -100])
def test_non_positive_catalog_credits_refuse_the_grant(sb, bad_credits):
    """A corrupt row would take the user's money and grant nothing (or debit them)."""
    for row in sb.db["credit_packs"]:
        if row["product_id"] == _PACK_PLUS:
            row["credits"] = bad_credits

    with pytest.raises(svc.IAPError):
        _service(sb).apply_verified_transaction(_USER, _pack_txn())
    assert "add_purchased_credits" not in sb.rpc_names()


def test_absurd_catalog_credits_refuse_the_grant(sb):
    """The ceiling is the only thing between a bad hand-edit in Studio and an unbounded credit
    mint on a $4.99 purchase."""
    for row in sb.db["credit_packs"]:
        if row["product_id"] == _PACK_PLUS:
            row["credits"] = settings.IAP_MAX_PACK_CREDITS + 1

    with pytest.raises(svc.IAPError):
        _service(sb).apply_verified_transaction(_USER, _pack_txn())
    assert sb.purchased_total == 0


def test_catalog_read_failure_is_retryable_not_unknown_product(sb):
    """A DB outage is OURS, not a bad receipt. `IAPError` maps to 503, which leaves the
    transaction unfinished so Apple redelivers it — that is the recovery."""
    sb._fail.add("credit_packs")
    with pytest.raises(svc.IAPError) as exc:
        _service(sb).apply_verified_transaction(_USER, _pack_txn())
    assert not isinstance(exc.value, svc.UnknownProduct)


def test_grant_rpc_failure_surfaces_as_retryable_never_as_success(sb):
    """The user HAS been charged. Reporting success here would let the client finish the
    transaction and the credits would be lost with no redelivery to repair them."""
    sb._fail.add("add_purchased_credits")
    with pytest.raises(svc.IAPError):
        _service(sb).apply_verified_transaction(_USER, _pack_txn())


def test_transaction_without_a_transaction_id_is_refused(sb):
    payload = _pack_txn()
    payload.pop("transactionId")
    with pytest.raises(svc.UnknownProduct):
        _service(sb).apply_verified_transaction(_USER, payload)


def test_environment_defaults_when_apple_omits_it(sb):
    """`app_store._to_dict` DROPS None values, so `environment` can be absent — and it is part
    of the unique dedup key, where a NULL would stop the index deduping at all."""
    payload = _pack_txn()
    payload.pop("environment")
    _service(sb).apply_verified_transaction(_USER, payload)

    _, params = sb.rpcs[-1]
    assert params["p_environment"] == settings.IAP_ENVIRONMENT
    assert params["p_environment"]


# ── Refunds via the webhook ───────────────────────────────────────────────────────────────

def _notification(ntype, transaction, subtype=""):
    return {"notificationType": ntype, "subtype": subtype, "signedDate": 1_700_000_000_000}, \
        transaction


def test_refund_notification_reaches_the_purchased_pool(sb):
    """Before this path existed, `user_id_for_transaction` only looked in `subscriptions`, so a
    consumable REFUND resolved to no user and was dropped as `ignored_unknown_transaction` —
    the refunded user silently kept every credit they bought."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-R"))
    assert sb.purchased_total == 250

    notif, txn = _notification("REFUND", _pack_txn(txn_id="TXN-R"))
    outcome, user_id = service.apply_notification(notif, txn)

    assert outcome == "credit_pack_revoked"
    assert user_id == _USER
    assert sb.purchased_total == 0


def test_refund_notification_is_idempotent_across_apples_retries(sb):
    """Apple retries a REFUND up to 5 times over ~8 hours."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-R"))
    notif, txn = _notification("REFUND", _pack_txn(txn_id="TXN-R"))

    service.apply_notification(notif, txn)
    for _ in range(4):
        outcome, _u = service.apply_notification(notif, txn)
        assert outcome == "credit_pack_already_revoked"
    assert sb.purchased_total == 0


def test_pack_refund_does_not_touch_the_subscription_clawback(sb):
    """`revoke_tier_credits` floors the GRANTED pool. Routing a pack refund through it would
    strip a subscription the user still pays for."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-R"))
    notif, txn = _notification("REFUND", _pack_txn(txn_id="TXN-R"))
    service.apply_notification(notif, txn)

    assert "revoke_tier_credits" not in sb.rpc_names()
    assert sb.db["subscriptions"] == []


def test_consumption_request_is_acknowledged_without_mutating(sb):
    """Apple wants consumption data within 12h to adjudicate a refund. We have no App Store
    Server API client, so this is deliberately unanswered — but it must still resolve to a 200
    outcome, because a non-2xx makes Apple retry for days."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-C"))
    notif, txn = _notification("CONSUMPTION_REQUEST", _pack_txn(txn_id="TXN-C"))

    outcome, _u = service.apply_notification(notif, txn)
    assert outcome == "credit_pack_consumption_request_unanswered"
    assert sb.purchased_total == 250


def test_unrelated_pack_notification_is_ignored_cleanly(sb):
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-O"))
    notif, txn = _notification("REFUND_DECLINED", _pack_txn(txn_id="TXN-O"))

    outcome, _u = service.apply_notification(notif, txn)
    assert outcome.startswith("credit_pack_ignored")
    assert sb.purchased_total == 250


def test_revoke_rpc_failure_is_reported_not_raised(sb):
    """Raising would return non-2xx and make Apple redeliver a notification we may already have
    applied. Best-effort by design; the loss is logged."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-R"))
    sb._fail.add("revoke_purchased_credits")

    notif, txn = _notification("REFUND", _pack_txn(txn_id="TXN-R"))
    outcome, _u = service.apply_notification(notif, txn)
    assert outcome == "credit_pack_revoke_failed"


def test_user_lookup_falls_back_to_credit_purchases(sb):
    """The concrete fix: `subscriptions` has no row for a consumable."""
    service = _service(sb)
    service.apply_verified_transaction(_USER, _pack_txn(txn_id="TXN-L"))

    assert service.user_id_for_transaction("TXN-L") == _USER
    assert service.user_id_for_transaction("NEVER-SEEN") is None
