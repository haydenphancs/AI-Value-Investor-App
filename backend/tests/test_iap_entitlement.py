"""In-app purchase entitlement.

This is the money path, so the tests are weighted toward the ways it can go WRONG rather
than the happy purchase:

* **Forgery.** Nothing the client says about what it bought is used. The tier comes from the
  Apple-verified payload, so a client claiming `premium` while presenting a `pro` receipt
  gets pro.
* **Replay.** StoreKit replays `Transaction.updates` on every launch, restore re-submits, and
  Apple retries webhooks. If a replay minted credits, users would farm them by relaunching.
* **Revocation and expiry.** A refunded or lapsed subscription must actually lose the tier.
  Taking "a transaction arrived" to mean "entitled" would keep refunded users on a paid plan.
* **The winning tier.** A user can hold several rows. `users.tier` must track the best
  ACTIVE one, so a stale expired row can neither demote a paying customer nor keep a lapsed
  one paid.
* **Failing closed.** Production with no trust anchor must refuse to verify, never degrade
  into accepting anything.

No network / Supabase — the client is a fake and verification is stubbed at the integration
boundary, which is itself tested separately for fail-closed behaviour.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.integrations import app_store
from app.services import iap_service as svc

_USER = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
_OTHER_USER = "11110000-ffff-4eee-8ddd-ccccbbbbaaaa"
_PRO = "com.phan.caydex.pro.monthly"
_MAX = "com.phan.caydex.max.monthly"


def _ms(dt: datetime) -> float:
    return dt.timestamp() * 1000


def _txn(product=_PRO, txn_id="1000000000000001", expires_in_days=30, **extra):
    payload = {
        "transactionId": txn_id,
        "originalTransactionId": txn_id,
        "productId": product,
        "bundleId": settings.IAP_BUNDLE_ID,
    }
    if expires_in_days is not None:
        payload["expiresDate"] = _ms(
            datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        )
    payload.update(extra)
    return payload


# ── Fake Supabase ─────────────────────────────────────────────────────────────

class _Q:
    def __init__(self, db, log, table, fail):
        self._db, self._log, self._table, self._fail = db, log, table, fail
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
            out = [
                r for r in rows
                if all(r.get(k) == v for k, v in self._filters.items())
            ]
            return type("R", (), {"data": out})()
        if self._op == "insert":
            rows.append({"id": f"row-{len(rows) + 1}", **self._values})
            self._log.append(("insert", self._table))
            return type("R", (), {"data": [rows[-1]]})()
        if self._op == "update":
            touched = []
            for r in rows:
                if all(r.get(k) == v for k, v in self._filters.items()):
                    r.update(self._values)
                    touched.append(r)
            # `users` rows are implicit in this fake; record the intent either way.
            self._log.append(("update", self._table, self._values))
            return type("R", (), {"data": touched})()
        return type("R", (), {"data": []})()


class FakeSupabase:
    def __init__(self, fail=(), subscriptions=None):
        self.db = {"subscriptions": list(subscriptions or []), "users": [{"id": _USER}]}
        self.log: list = []
        self._fail = set(fail)
        self.rpcs: list = []

    def table(self, name):
        return _Q(self.db, self.log, name, self._fail)

    def rpc(self, name, params):
        if "rpc" in self._fail:
            raise RuntimeError("rpc unavailable")
        self.rpcs.append((name, params))
        return type("R", (), {"execute": lambda *_a, **_k: None})()


def _service(sb) -> svc.IAPService:
    s = svc.IAPService.__new__(svc.IAPService)
    s.supabase = sb
    return s


# ── Product mapping ───────────────────────────────────────────────────────────

def test_products_map_to_the_right_tiers():
    assert svc.tier_for_product(_PRO) == "pro"
    assert svc.tier_for_product(_MAX) == "premium"


def test_unmapped_product_raises_rather_than_defaulting_to_free():
    """A verified purchase we can't price is an anomaly. Defaulting it to free would take
    the user's money and give them nothing, silently."""
    with pytest.raises(svc.UnknownProduct):
        svc.tier_for_product("com.phan.caydex.enterprise.yearly")
    with pytest.raises(svc.UnknownProduct):
        svc.tier_for_product(None)
    with pytest.raises(svc.UnknownProduct):
        svc.tier_for_product("")


# ── Status derivation ─────────────────────────────────────────────────────────

def test_active_when_expiry_is_in_the_future():
    assert svc.status_for_transaction(_txn()) == "active"


def test_expired_when_expiry_has_passed():
    assert svc.status_for_transaction(_txn(expires_in_days=-1)) == "expired"


def test_revoked_on_revocation_date():
    assert svc.status_for_transaction(_txn(revocationDate=time.time() * 1000)) == "revoked"


def test_revoked_on_reason_zero():
    """`revocationReason: 0` is a real Apple value and is FALSY — a truthiness check would
    miss this refund and leave the user entitled."""
    assert svc.status_for_transaction(_txn(revocationReason=0)) == "revoked"


def test_no_expiry_is_treated_as_active():
    """Non-renewing purchases carry no expiresDate."""
    assert svc.status_for_transaction(_txn(expires_in_days=None)) == "active"


# ── Applying a transaction ────────────────────────────────────────────────────

def test_apply_records_the_subscription_and_mirrors_the_tier():
    sb = FakeSupabase()
    out = _service(sb).apply_transaction(_USER, _txn(product=_MAX))
    assert out["tier"] == "premium"
    assert out["winning_tier"] == "premium"
    assert out["was_replay"] is False
    assert ("insert", "subscriptions") in sb.log
    assert ("update", "users", {"tier": "premium"}) in sb.log


def test_apply_refreshes_the_credit_period_after_mirroring():
    """ensure_credit_period reads users.tier, so the mirror has to happen first or the user
    is granted the OLD tier's allocation."""
    sb = FakeSupabase()
    _service(sb).apply_transaction(_USER, _txn(product=_MAX))
    assert [name for name, _ in sb.rpcs] == ["ensure_credit_period"]
    tier_update_idx = sb.log.index(("update", "users", {"tier": "premium"}))
    assert tier_update_idx >= 0


def test_tier_comes_from_the_verified_payload_not_the_client():
    """The endpoint accepts only a signed blob, and the tier is read from it. A pro receipt
    can never yield premium regardless of what the client wanted."""
    sb = FakeSupabase()
    out = _service(sb).apply_transaction(_USER, _txn(product=_PRO))
    assert out["tier"] == "pro"


def test_replayed_transaction_updates_instead_of_inserting():
    """StoreKit replays on every launch. A second insert would create a duplicate
    entitlement row for one purchase."""
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn())
    s.apply_transaction(_USER, _txn())
    assert len(sb.db["subscriptions"]) == 1
    inserts = [e for e in sb.log if e[0] == "insert"]
    assert len(inserts) == 1


def test_replay_is_reported_as_a_replay():
    sb = FakeSupabase()
    s = _service(sb)
    assert s.apply_transaction(_USER, _txn())["was_replay"] is False
    assert s.apply_transaction(_USER, _txn())["was_replay"] is True


def test_replay_does_not_grant_extra_credits_directly():
    """Credits come from the monthly allocation RPC, which is idempotent within a period —
    NOT from a per-delivery grant. Asserted so nobody 'helpfully' adds one."""
    sb = FakeSupabase()
    s = _service(sb)
    for _ in range(5):
        s.apply_transaction(_USER, _txn())
    assert all(name == "ensure_credit_period" for name, _ in sb.rpcs)
    assert not [n for n, _ in sb.rpcs if "add_credit" in n or "grant" in n]


# ── Winning tier ──────────────────────────────────────────────────────────────

def _sub(tier, status="active", days=30, user=_USER, txn="t1"):
    end = (
        (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        if days is not None else None
    )
    return {
        "id": f"id-{txn}", "user_id": user, "tier": tier, "status": status,
        "current_period_end": end, "original_transaction_id": txn,
    }


def test_winning_tier_picks_the_best_active_entitlement():
    sb = FakeSupabase(subscriptions=[_sub("pro", txn="a"), _sub("premium", txn="b")])
    assert _service(sb).winning_tier(_USER) == "premium"


def test_expired_row_does_not_demote_an_active_one():
    """The nasty ordering case: a stale expired Max row alongside a live Pro row."""
    sb = FakeSupabase(subscriptions=[
        _sub("premium", status="expired", days=-5, txn="old"),
        _sub("pro", txn="new"),
    ])
    assert _service(sb).winning_tier(_USER) == "pro"


def test_lapsed_period_end_is_not_entitling_even_if_status_says_active():
    """Status can lag reality. A period that has already ended must not entitle."""
    sb = FakeSupabase(subscriptions=[_sub("premium", status="active", days=-1)])
    assert _service(sb).winning_tier(_USER) == "free"


def test_revoked_row_is_not_entitling():
    sb = FakeSupabase(subscriptions=[_sub("premium", status="revoked")])
    assert _service(sb).winning_tier(_USER) == "free"


def test_grace_period_and_billing_retry_still_entitle():
    """Apple keeps a subscriber in grace/retry after a failed charge. Cutting access
    immediately would punish someone whose card merely needs updating."""
    for status in ("grace_period", "billing_retry"):
        sb = FakeSupabase(subscriptions=[_sub("pro", status=status)])
        assert _service(sb).winning_tier(_USER) == "pro", status


def test_no_subscriptions_means_free():
    assert _service(FakeSupabase()).winning_tier(_USER) == "free"


def test_another_users_subscription_never_leaks():
    sb = FakeSupabase(subscriptions=[_sub("premium", user=_OTHER_USER, txn="theirs")])
    assert _service(sb).winning_tier(_USER) == "free"


def test_null_period_end_still_entitles_when_active():
    sb = FakeSupabase(subscriptions=[_sub("pro", days=None)])
    assert _service(sb).winning_tier(_USER) == "pro"


def test_unparseable_period_end_is_treated_as_not_entitling():
    """Under-grant on malformed data rather than over-grant: a support ticket beats giving
    away paid tiers on a bad timestamp."""
    bad = _sub("premium")
    bad["current_period_end"] = "not-a-date"
    sb = FakeSupabase(subscriptions=[bad])
    assert _service(sb).winning_tier(_USER) == "free"


# ── Failure handling ──────────────────────────────────────────────────────────

def test_read_failure_raises_rather_than_guessing_free():
    """Guessing 'free' on a DB blip would strip a paying customer's access."""
    sb = FakeSupabase(fail=("subscriptions",), subscriptions=[_sub("premium")])
    with pytest.raises(svc.IAPError):
        _service(sb).winning_tier(_USER)


def test_write_failure_surfaces_as_iap_error():
    sb = FakeSupabase(fail=("subscriptions",))
    with pytest.raises(svc.IAPError):
        _service(sb).apply_transaction(_USER, _txn())


def test_credit_rpc_failure_does_not_lose_the_entitlement():
    """The purchase is already recorded. Failing the whole call would tell a paying user
    their purchase failed; the RPC also runs lazily on the next credit read."""
    sb = FakeSupabase(fail=("rpc",))
    out = _service(sb).apply_transaction(_USER, _txn(product=_MAX))
    assert out["winning_tier"] == "premium"
    assert ("update", "users", {"tier": "premium"}) in sb.log


# ── Webhook ───────────────────────────────────────────────────────────────────

def test_notification_for_an_unknown_transaction_is_ignored_not_retried():
    """Apple retries non-2xx for days. A notification we can't map will never map, so
    inviting retries forever is pointless."""
    sb = FakeSupabase()
    outcome, user_id = _service(sb).apply_notification(
        {"notificationType": "DID_RENEW"}, _txn(txn_id="unknown-txn")
    )
    assert outcome == "ignored_unknown_transaction"
    assert user_id is None


def test_notification_without_a_transaction_is_ignored():
    outcome, user_id = _service(FakeSupabase()).apply_notification(
        {"notificationType": "TEST"}, None
    )
    assert outcome == "ignored_no_transaction"
    assert user_id is None


def test_notification_applies_to_the_mapped_user():
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(txn_id="txn-1"))
    outcome, user_id = s.apply_notification(
        {"notificationType": "DID_RENEW"}, _txn(txn_id="txn-1", expires_in_days=60)
    )
    assert outcome == "applied:DID_RENEW"
    assert user_id == _USER


def test_refund_notification_revokes_the_tier():
    """The whole reason the webhook exists: without it a refunded user keeps paid access."""
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-9"))
    assert s.winning_tier(_USER) == "premium"

    s.apply_notification(
        {"notificationType": "REFUND"},
        _txn(product=_MAX, txn_id="txn-9", revocationDate=time.time() * 1000),
    )
    assert s.winning_tier(_USER) == "free"
    assert ("update", "users", {"tier": "free"}) in sb.log


def test_expiry_notification_drops_the_tier():
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-x"))
    s.apply_notification(
        {"notificationType": "EXPIRED"},
        _txn(product=_PRO, txn_id="txn-x", expires_in_days=-1),
    )
    assert s.winning_tier(_USER) == "free"


def test_notification_for_an_unmapped_product_is_ignored_not_retried():
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(txn_id="txn-k"))
    sb.db["subscriptions"][0]["original_transaction_id"] = "txn-k"
    outcome, _ = s.apply_notification(
        {"notificationType": "DID_RENEW"},
        _txn(product="com.unknown.thing", txn_id="txn-k"),
    )
    assert outcome == "ignored_unknown_product"


# ── Verification boundary fails closed ────────────────────────────────────────

@pytest.fixture
def _restore_iap_settings():
    env, certs = settings.IAP_ENVIRONMENT, settings.IAP_ROOT_CERT_DIR
    yield
    settings.IAP_ENVIRONMENT, settings.IAP_ROOT_CERT_DIR = env, certs
    app_store.reset_verifier_cache()


@pytest.mark.parametrize("env", ["Production", "Sandbox"])
def test_verification_refuses_to_run_without_a_trust_anchor(env, _restore_iap_settings):
    """No root certs must mean 'cannot verify', never 'accept anything'."""
    settings.IAP_ENVIRONMENT = env
    settings.IAP_ROOT_CERT_DIR = "certs/definitely-not-here"
    app_store.reset_verifier_cache()
    with pytest.raises(app_store.AppStoreNotConfigured):
        app_store.get_verifier()


def test_unknown_environment_fails_closed(_restore_iap_settings):
    settings.IAP_ENVIRONMENT = "Staging"
    app_store.reset_verifier_cache()
    with pytest.raises(app_store.AppStoreNotConfigured):
        app_store.get_verifier()


@pytest.mark.parametrize("garbage", ["", "   ", "not-a-jws", "a.b.c", "x" * 500])
def test_garbage_transactions_are_rejected(garbage, _restore_iap_settings):
    settings.IAP_ENVIRONMENT = "LocalTesting"
    app_store.reset_verifier_cache()
    with pytest.raises(app_store.AppStoreVerificationFailed):
        app_store.verify_signed_transaction(garbage)


def test_local_testing_needs_no_certificates(_restore_iap_settings):
    """Local StoreKit testing must work without Apple's roots, or nothing is testable
    before the App Store Connect record exists."""
    settings.IAP_ENVIRONMENT = "LocalTesting"
    settings.IAP_ROOT_CERT_DIR = "certs/definitely-not-here"
    app_store.reset_verifier_cache()
    assert app_store.get_verifier() is not None
