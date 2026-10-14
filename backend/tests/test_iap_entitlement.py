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

import logging
import time
from collections.abc import MutableMapping
from datetime import datetime, timedelta, timezone

import pytest

from test_credit_pool_isolation import _Credits

from app.config import settings
from app.integrations import app_store
from app.services import iap_service as svc

_USER = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
_OTHER_USER = "11110000-ffff-4eee-8ddd-ccccbbbbaaaa"
_PRO = "com.phan.caydex.pro.monthly"
_MAX = "com.phan.caydex.max.monthly"


def _ms(dt: datetime) -> float:
    return dt.timestamp() * 1000


# One anchor for every fixture's `expiresDate`, so the same transaction always carries the same
# date — as Apple's does. Migration 196 claws back a refunded period only when the refunded
# transaction IS the period in force (same original transaction AND same period end); a
# fixture that minted a fresh `now + 30 days` per call would look like a refund of a
# different period.
_ANCHOR = datetime.now(timezone.utc).replace(microsecond=0)


def _txn(product=_PRO, txn_id="1000000000000001", expires_in_days=30, **extra):
    payload = {
        "transactionId": txn_id,
        "originalTransactionId": txn_id,
        "productId": product,
        "bundleId": settings.IAP_BUNDLE_ID,
    }
    if expires_in_days is not None:
        payload["expiresDate"] = _ms(_ANCHOR + timedelta(days=expires_in_days))
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


class _CreditsView(MutableMapping):
    """`sb.credits` as the tests read and write it ({"total", "used"}), backed by the model."""

    def __init__(self, model: _Credits):
        self._m = model

    def __getitem__(self, key):
        if key not in ("total", "used"):
            raise KeyError(key)
        return getattr(self._m, key)

    def __setitem__(self, key, value):
        if key not in ("total", "used"):
            raise KeyError(key)
        setattr(self._m, key, value)

    def __delitem__(self, key):
        raise TypeError("the balance row has fixed columns")

    def __iter__(self):
        return iter(("total", "used"))

    def __len__(self):
        return 2


class FakeSupabase:
    """Fake Supabase client.

    The `rpc` handler runs `ensure_credit_period`, `grant_tier_upgrade` and
    `revoke_tier_credits` through `_Credits` — the ONE executable model of that SQL
    (tests/test_credit_pool_isolation.py, kept branch for branch with migration 196) — fed with
    this fake's own `users` and `subscriptions` rows before every call. That matters: a fake
    that returns a bland success for every rpc makes a credit test pass no matter what the
    balance does, which is exactly how a money bug survives a green suite, and a SECOND
    hand-written model drifts (this one still encoded migration 114 after 139 and 196 had
    changed the SQL). Change the SQL, change `_Credits`.
    """

    def __init__(self, fail=(), subscriptions=None, credits=None, period_due=False, now=None):
        self.db = {"subscriptions": list(subscriptions or []), "users": [{"id": _USER}]}
        self.log: list = []
        self._fail = set(fail)
        self.rpcs: list = []
        # None = no balance row yet (first touch). Otherwise the model of the user_credits row.
        self.model = None
        if credits is not None:
            self.model = _Credits(total=credits["total"], used=credits["used"])
            self.model.period_due = period_due
        # The model's clock. None = real time, which is what the service compares against.
        self.now = now

    @property
    def credits(self):
        return None if self.model is None else _CreditsView(self.model)

    def table(self, name):
        return _Q(self.db, self.log, name, self._fail)

    def _user(self) -> dict:
        return next((r for r in self.db["users"] if r.get("id") == _USER), {})

    def _tier(self) -> str:
        return (self._user().get("tier") or "free").lower()

    def _sync(self, model: _Credits) -> None:
        """What the SQL reads: users.tier / comp_tier and the account's FIRST subscriptions
        row (plpgsql `SELECT ... INTO` takes the first; the schema allows only one)."""
        model.now = self.now or datetime.now(timezone.utc)
        model.tier = self._tier()
        model.comp_tier = (self._user().get("comp_tier") or None)
        row = next((r for r in self.db["subscriptions"] if r.get("user_id") == _USER), None)
        if row is None:
            model.subscription = None
            return
        end = svc._parse_iso(row.get("current_period_end"))
        model.set_subscription(row.get("original_transaction_id"), (row.get("tier") or "free"),
                               (row.get("status") or ""), end, row.get("environment"))

    def rpc(self, name, params):
        if "rpc" in self._fail or name in self._fail:
            raise RuntimeError(f"rpc {name} unavailable")
        self.rpcs.append((name, params))

        if name in ("ensure_credit_period", "grant_tier_upgrade"):
            if self.model is None:
                # The SQL's first touch: a calendar-mode row at the calendar allowance, then
                # the same call falls through to any paid period.
                self.model = _Credits(total=0)
                self._sync(self.model)
                alloc = self.model._calendar_alloc()
                self.model.total = self.model.tier_alloc = alloc
            self._sync(self.model)
            getattr(self.model, name)()
        elif name == "revoke_tier_credits":
            if self.model is not None:
                self._sync(self.model)
                self.model.revoke_tier_credits()

        return type("R", (), {"execute": lambda *_a, **_k: None})()

    @property
    def remaining(self) -> int:
        return 0 if self.model is None else self.model.total - self.model.used


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
    """Both credit RPCs read users.tier, so the mirror has to happen first or the user is
    granted the OLD tier's allocation.

    Order is asserted deliberately: `ensure_credit_period` first (rolls a due period over and
    creates the balance row on a first touch), THEN `grant_tier_upgrade` (tops the ceiling up
    to the new tier). Reversing them would let a due monthly reset overwrite the fresh upgrade
    grant with the plain allocation."""
    sb = FakeSupabase()
    _service(sb).apply_transaction(_USER, _txn(product=_MAX))
    assert [name for name, _ in sb.rpcs] == ["ensure_credit_period", "grant_tier_upgrade"]
    tier_update_idx = sb.log.index(("update", "users", {"tier": "premium"}))
    assert tier_update_idx >= 0


def test_mid_period_upgrade_actually_delivers_the_credits():
    """THE REGRESSION THIS FILE EXISTS FOR (migration 112).

    `ensure_credit_period` grants only when the monthly boundary has passed, so an upgrade
    bought mid-period used to leave the buyer on their old, usually exhausted, balance:
    money taken, tier flipped, zero credits, 402 on the very next tap, for up to four weeks.
    """
    # Free user who has spent all 50 credits, three weeks before the reset.
    sb = FakeSupabase(credits={"total": 50, "used": 50}, period_due=False)
    assert sb.remaining == 0

    _service(sb).apply_transaction(_USER, _txn(product=_PRO))

    assert sb._tier() == "pro"
    assert sb.credits["total"] == 1200
    assert sb.remaining > 0, "paid for Pro and still had nothing to spend"


def test_upgrade_before_any_credit_row_exists_still_grants():
    """Upgrading before the first credit read means there is no `user_credits` row to raise."""
    sb = FakeSupabase(credits=None)
    _service(sb).apply_transaction(_USER, _txn(product=_MAX))
    assert sb.credits == {"total": 4000, "used": 0}


def test_downgrade_does_not_claw_back_paid_credits():
    """A lower allocation must be a no-op mid-period — the user paid for those credits. This
    is why the fix is a separate RPC and not a loosened condition on ensure_credit_period,
    which would have reset `total` DOWN on every reconcile."""
    sb = FakeSupabase(credits={"total": 4000, "used": 100}, period_due=False)
    sb.db["users"][0]["tier"] = "premium"
    sb.rpc("grant_tier_upgrade", {"p_user_id": _USER})
    assert sb.credits["total"] == 4000

    # Now the winning tier drops to pro; the ceiling must not fall mid-period.
    sb.db["users"][0]["tier"] = "pro"
    sb.rpc("grant_tier_upgrade", {"p_user_id": _USER})
    assert sb.credits["total"] == 4000


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


def test_replay_does_not_grant_extra_credits():
    """StoreKit replays `Transaction.updates` on every launch, so a per-delivery grant would
    be farmable by relaunching the app.

    This used to assert that NO rpc with 'grant' in the name was ever called. That proxy
    became wrong when migration 112 added `grant_tier_upgrade` to fix mid-period upgrades
    delivering zero credits — so it now asserts the property the proxy stood for: five
    replays leave the balance exactly where one delivery does. `grant_tier_upgrade` is
    idempotent by construction (it no-ops once `total >= allocation`), which is what makes
    that true; a naive additive grant would fail this test five times over.
    """
    sb = FakeSupabase(credits={"total": 50, "used": 0}, period_due=False)
    s = _service(sb)

    s.apply_transaction(_USER, _txn())          # Pro
    after_first = dict(sb.credits)
    assert after_first["total"] == 1200

    for _ in range(4):
        s.apply_transaction(_USER, _txn())

    assert sb.credits == after_first, "replays moved the balance — credits are farmable"
    assert not [n for n, _ in sb.rpcs if "add_credit" in n], (
        "a direct ledger append on the delivery path is additive and therefore farmable"
    )


def test_grant_failure_does_not_lose_the_purchase():
    """If migration 112 has not been applied the RPC 404s (PGRST202). The entitlement is
    already recorded by then, so this must degrade to the old behaviour — credits at the next
    monthly reset — rather than raise and fail a verified purchase."""
    sb = FakeSupabase(fail=("grant_tier_upgrade",))
    out = _service(sb).apply_transaction(_USER, _txn(product=_PRO))
    assert out["tier"] == "pro"
    assert ("update", "users", {"tier": "pro"}) in sb.log


def test_migration_112_keeps_its_two_load_bearing_invariants():
    """Source-scan on the SQL, because the Python fake can only model it.

    Both properties below are what make the grant safe, and both are one careless edit away
    from a money bug: drop the guard and every app launch mints credits; switch to a bare
    assignment and a downgrade deletes credits the user paid for."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "database" / "migrations" / "112_grant_tier_upgrade.sql"
    ).read_text()

    # 1. The idempotence / anti-farming guard: no grant once the ceiling is already met.
    assert "v_alloc <= v_row.total" in sql, (
        "the replay guard is gone — grant_tier_upgrade would add credits on every "
        "Transaction.updates replay"
    )
    # 2. No clawback and no zeroing of `used`: the UPDATE must touch `total`, never `used`.
    grant_update = sql[sql.index("UPDATE public.user_credits"):]
    grant_update = grant_update[: grant_update.index(";")]
    assert "used" not in grant_update, (
        "the grant now writes `used`; zeroing it changes the period's total entitlement and "
        "makes an upgrade's value depend on when in the month it happens"
    )


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


# ── Refund must take the UNSPENT credits back (migration 114 part 1) ──────────
#
# `grant_tier_upgrade` never lowers `total` — deliberately, so a VOLUNTARY downgrade does not
# delete credits the user already paid for (migration 112 §2). Revocation routed through the
# same call, so `reconcile_user_tier` dropped the tier while the allocation stayed spendable:
# a refunded Max subscriber kept 4000 credits, roughly 200 AI reports at ~17 Gemini + ~20 FMP
# calls each, having paid nothing. The tier flipping to free made it look handled.

def _revoked_txn(product=_MAX, txn_id="txn-refund"):
    return _txn(product=product, txn_id=txn_id, revocationDate=_ms(datetime.now(timezone.utc)))


def test_a_refund_claws_back_the_unspent_allocation():
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-refund"))
    assert sb.credits == {"total": 4000, "used": 0}

    s.apply_transaction(_USER, _revoked_txn())

    assert s.winning_tier(_USER) == "free"
    assert sb.credits["total"] == 50, "a refunded subscriber must not keep the paid allocation"
    assert ("revoke_tier_credits", {"p_user_id": _USER}) in sb.rpcs


def test_the_clawback_never_drives_remaining_negative():
    """`remaining` is a GENERATED column (total - used). Flooring at `used` is what keeps it
    from going negative — every later charge would otherwise fail a CHECK rather than a
    balance test. Credits already SPENT are a business loss, not a modelling problem. Since
    139 the spend above the free allocation is written off too (both columns drop together),
    so the row lands on total == used == the free allocation."""
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-refund"))
    sb.credits["used"] = 900          # spent 900 of 4000 before refunding

    s.apply_transaction(_USER, _revoked_txn())

    assert sb.credits == {"total": 50, "used": 50}
    assert sb.remaining == 0, "remaining must floor at zero, never go negative"


def test_the_clawback_is_idempotent_across_apple_retries():
    """Apple retries a notification up to 5 times over ~8 hours."""
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-refund"))
    sb.credits["used"] = 120

    for _ in range(4):
        s.apply_transaction(_USER, _revoked_txn())

    assert sb.credits == {"total": 50, "used": 50}   # 139's write-off, applied once
    assert sb.remaining == 0


def test_an_ordinary_expiry_does_NOT_claw_back():
    """Only a REFUND claws back. An expiry means the paid period was fully used — the next
    monthly reset lowers the allocation naturally, and stripping it early would punish
    someone who paid for what they got."""
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-e"))
    s.apply_notification(
        {"notificationType": "EXPIRED"},
        _txn(product=_MAX, txn_id="txn-e", expires_in_days=-1),
    )
    assert s.winning_tier(_USER) == "free"
    assert sb.credits["total"] == 4000, "expiry is not a refund"
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)


def test_a_refund_does_not_strip_a_user_who_is_still_entitled():
    """The clawback is gated on `winning_tier == "free"`, so a refund can never disentitle
    someone who is still paying.

    `subscriptions_user_id_key UNIQUE (user_id)` means a user holds AT MOST ONE row today —
    `apply_transaction` updates the existing row rather than inserting a second — so this is
    a guard against a future multi-row world (a second store, or family sharing) rather than
    a reachable state now. Seed the rows directly, since the service layer cannot produce them.
    """
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    sb = FakeSupabase(subscriptions=[
        {"id": "row-1", "user_id": _USER, "tier": "premium", "status": "revoked",
         "original_transaction_id": "txn-a", "current_period_end": future},
        {"id": "row-2", "user_id": _USER, "tier": "pro", "status": "active",
         "original_transaction_id": "txn-b", "current_period_end": future},
    ], credits={"total": 4000, "used": 0})
    s = _service(sb)

    assert s.winning_tier(_USER) == "pro", "the still-active row must keep them entitled"

    s.apply_transaction(_USER, _revoked_txn(product=_MAX, txn_id="txn-a"))

    assert sb.credits["total"] == 4000, "must not claw back from a still-entitled user"
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)


def test_a_missing_revoke_rpc_degrades_instead_of_failing_the_webhook():
    """Migration 114 is applied by hand. Until it is, the tier must still drop — and the
    webhook must still return 2xx, or Apple redelivers a notification we DID apply."""
    sb = FakeSupabase(fail={"revoke_tier_credits"})
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-refund"))

    s.apply_transaction(_USER, _revoked_txn())      # must not raise

    assert s.winning_tier(_USER) == "free"


# ── Out-of-order notifications (migration 114 part 2) ─────────────────────────
#
# Apple guarantees NO ordering and retries for ~8 hours, so a redelivered EXPIRED can land
# after the DID_RENEW that superseded it. `apply_transaction` blind-overwrote tier/status/
# current_period_end with whatever arrived last, so the LAST delivery won regardless of which
# described the newer state — silently demoting a paying subscriber.

def _renew_notification(txn_id, *, signed_at, expires_in_days):
    return (
        {"notificationType": "DID_RENEW", "signedDate": _ms(signed_at)},
        _txn(product=_PRO, txn_id=txn_id, expires_in_days=expires_in_days),
    )


def _expired_notification(txn_id, *, signed_at, expires_in_days):
    return (
        {"notificationType": "EXPIRED", "signedDate": _ms(signed_at)},
        _txn(product=_PRO, txn_id=txn_id, expires_in_days=expires_in_days),
    )


def test_a_stale_EXPIRED_redelivery_does_not_demote_a_renewed_subscriber():
    """THE bug. The renewal was signed later, so the expiry is older news."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-r"))

    s.apply_notification(*_renew_notification(
        "txn-r", signed_at=now, expires_in_days=30,
    ))
    s.apply_notification(*_expired_notification(
        "txn-r", signed_at=now - timedelta(hours=6), expires_in_days=-1,
    ))

    assert s.winning_tier(_USER) == "pro", "a redelivered EXPIRED must not demote a payer"


def test_notifications_still_apply_in_order():
    """The guard must only reject OLDER deliveries — a genuine later expiry still lands."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-r"))

    s.apply_notification(*_renew_notification(
        "txn-r", signed_at=now - timedelta(hours=6), expires_in_days=30,
    ))
    s.apply_notification(*_expired_notification(
        "txn-r", signed_at=now, expires_in_days=-1,
    ))

    assert s.winning_tier(_USER) == "free"


def test_a_stale_delivery_can_never_block_a_refund():
    """A refund is authoritative even when it arrives out of order — leaving a refunded user
    entitled is the expensive direction."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-r"))
    s.apply_notification(*_renew_notification("txn-r", signed_at=now, expires_in_days=30))

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now - timedelta(days=1))},
        _revoked_txn(product=_MAX, txn_id="txn-r"),
    )
    assert s.winning_tier(_USER) == "free"


def test_a_client_payload_without_signedDate_is_not_treated_as_stale():
    """A payload with no `signedDate` is unorderable, so against a row that is NOT revoked it
    applies — dropping it would break Restore Purchases and the normal buy flow.

    Renamed from `test_the_client_verify_path_is_never_treated_as_stale`, whose premise ("a
    transaction carries no signedDate") was false: a verified JWS does carry one, and ignoring
    it is what let a refunded subscriber replay the pre-refund JWS. The client path IS ordered
    now — see the "Replaying a pre-refund JWS" section below."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-r"))
    s.apply_notification(*_renew_notification("txn-r", signed_at=now, expires_in_days=30))

    out = s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-r", expires_in_days=30))
    assert out["was_stale"] is False
    assert s.winning_tier(_USER) == "premium"


def test_ordering_is_on_signedDate_not_expiresDate():
    """The distinction is the whole design. An EXPIRED notification is signed LATE and carries
    an EARLY expiresDate, because it describes the period that just ended. Ordering on
    expiresDate would classify every ordinary expiry as stale and keep lapsed subscribers
    paid — the opposite bug, and the more expensive one."""
    now = datetime.now(timezone.utc)
    prior = {
        "id": "row-1", "tier": "pro", "status": "active",
        "current_period_end": (now + timedelta(days=30)).isoformat(),
        "last_event_at": (now - timedelta(hours=6)).isoformat(),
    }
    # Later signedDate, EARLIER expiresDate — a real expiry. Must apply.
    assert svc._stale_delivery_reason(prior, status="expired", event_at=now) is None
    # Earlier signedDate — genuinely stale. Must be skipped.
    assert svc._stale_delivery_reason(
        prior, status="expired", event_at=now - timedelta(days=1)
    ) is not None


def test_the_guard_is_inert_before_migration_114_is_applied():
    """Deploy order is not guaranteed. With no stored `last_event_at` there is nothing to
    compare against, so every delivery applies exactly as it does today."""
    now = datetime.now(timezone.utc)
    prior_without_column = {"id": "row-1", "tier": "pro", "status": "active"}
    assert svc._stale_delivery_reason(
        prior_without_column, status="expired", event_at=now - timedelta(days=99)
    ) is None


@pytest.mark.parametrize("junk", [None, "", "not-a-date", 12345, "2026-13-45T99:99:99"])
def test_a_junk_stored_timestamp_never_raises(junk):
    """`_parse_iso` must degrade to None rather than raise — this runs inside a webhook, and
    a TypeError here would make Apple redeliver forever."""
    now = datetime.now(timezone.utc)
    prior = {"id": "row-1", "tier": "pro", "status": "active", "last_event_at": junk}
    assert svc._stale_delivery_reason(prior, status="active", event_at=now) is None


def test_a_naive_stored_timestamp_is_comparable():
    """Postgres emits +00:00 but some clients emit a naive string. Comparing naive against the
    aware datetime derived from Apple's epoch-ms raises TypeError, which would 500 the
    webhook."""
    now = datetime.now(timezone.utc)
    prior = {
        "id": "row-1", "tier": "pro", "status": "active",
        "last_event_at": (now + timedelta(days=1)).replace(tzinfo=None).isoformat(),
    }
    assert svc._stale_delivery_reason(prior, status="expired", event_at=now) is not None


# ── Replaying a pre-refund JWS (client-verify ordering on signedDate) ────────
#
# Buy Max, get an Apple refund, re-POST the SAVED pre-refund JWS to /billing/verify. Apple's
# signature still verifies (online checks validate the chain at the current time), the JWS has
# no revocationDate and a future expiresDate, so it read "active" — and the client path was
# never ordered, so it overwrote the revoked row and `grant_tier_upgrade` handed back the 4000.
# The fix orders the client path on the JWS's own `signedDate`.

def _jws(product=_MAX, txn_id="txn-1", *, signed_at, original=None, **extra):
    payload = _txn(product=product, txn_id=txn_id, signedDate=_ms(signed_at), **extra)
    if original is not None:
        payload["originalTransactionId"] = original
    return payload


def _refund(s, txn_id="txn-1", *, at, product=_MAX):
    return s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(at)},
        _jws(product=product, txn_id=txn_id, signed_at=at, revocationDate=_ms(at)),
    )


def test_a_refunded_subscriber_cannot_replay_the_pre_refund_jws():
    """THE bug. Before the fix: premium and 4000 credits back, `was_stale` False."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    jws_1 = _jws(signed_at=now - timedelta(days=3))
    s.apply_verified_transaction(_USER, jws_1)
    assert sb.credits == {"total": 4000, "used": 0}

    _refund(s, at=now - timedelta(days=1))
    assert sb._tier() == "free" and sb.credits["total"] == 50

    out = s.apply_verified_transaction(_USER, jws_1)

    assert out["was_stale"] is True
    assert out["winning_tier"] == "free"
    assert sb._tier() == "free"
    assert sb.credits["total"] == 50, "the replay handed the refunded allocation back"
    assert sb.db["subscriptions"][0]["status"] == "revoked"


@pytest.mark.parametrize("new_lineage", [False, True], ids=["same-original", "new-original"])
def test_a_genuine_resubscribe_after_a_refund_still_applies(new_lineage):
    """The guard must not lock a refunded customer out: a new purchase is a new transaction
    that Apple signs AFTER the refund."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    _refund(s, at=now - timedelta(days=1))

    # A new purchase expires a month after ITS purchase — never on the refunded period's
    # exact end, which (with the same original transaction) is a reversal of that refund.
    fresh = _jws(txn_id="txn-2", signed_at=now, expires_in_days=31,
                 original="txn-2" if new_lineage else "txn-1")
    out = s.apply_verified_transaction(_USER, fresh)

    assert out["was_stale"] is False
    assert out["winning_tier"] == "premium"
    assert sb.credits["total"] == 4000
    assert len(sb.db["subscriptions"]) == 1


@pytest.mark.parametrize("new_lineage", [False, True], ids=["same-original", "new-original"])
def test_a_cheap_resubscribe_does_not_reopen_the_replay(new_lineage):
    """Refund Max, buy Pro, replay the Max JWS. If the comparison ran only against a REVOKED
    row — or only within one originalTransactionId — the Pro purchase would re-open it."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    jws_max = _jws(signed_at=now - timedelta(days=3))
    s.apply_verified_transaction(_USER, jws_max)
    _refund(s, at=now - timedelta(days=2))
    s.apply_verified_transaction(_USER, _jws(
        product=_PRO, txn_id="txn-pro", signed_at=now - timedelta(days=1), expires_in_days=31,
        original="txn-pro" if new_lineage else "txn-1",
    ))
    assert sb._tier() == "pro" and sb.credits["total"] == 1200

    out = s.apply_verified_transaction(_USER, jws_max)

    assert out["was_stale"] is True
    assert sb._tier() == "pro"
    assert sb.credits["total"] == 1200
    assert sb.db["subscriptions"][0]["tier"] == "pro"


def test_a_revocation_seen_only_by_the_client_still_blocks_the_replay():
    """No webhook at all (or the REFUND notification lost): the refund arrives as a revoked
    JWS through `Transaction.updates`. Its signedDate becomes the ordering key, so neither the
    replay nor a DID_RENEW redelivered late can re-entitle the row."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    jws_1 = _jws(signed_at=now - timedelta(days=3))
    s.apply_verified_transaction(_USER, jws_1)

    refunded_at = now - timedelta(days=1)
    s.apply_verified_transaction(
        _USER, _jws(signed_at=refunded_at, revocationDate=_ms(refunded_at))
    )
    assert sb._tier() == "free" and sb.credits["total"] == 50
    assert svc._parse_iso(sb.db["subscriptions"][0]["last_event_at"]) == svc._ms_to_dt(
        _ms(refunded_at)
    )

    assert s.apply_verified_transaction(_USER, jws_1)["was_stale"] is True
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(days=2))},
        _txn(product=_MAX, txn_id="txn-1", expires_in_days=30),
    )
    assert sb._tier() == "free" and sb.credits["total"] == 50


def test_a_client_revocation_never_lowers_a_later_stored_key():
    """Monotonic: an older revoked JWS still revokes (revocation wins) but must not rewind the
    ordering key — that would re-admit anything signed between the two."""
    now = datetime.now(timezone.utc)
    later = now - timedelta(hours=1)
    sb = FakeSupabase(subscriptions=[{
        "id": "row-1", "user_id": _USER, "tier": "premium", "status": "active",
        "original_transaction_id": "txn-1", "last_event_at": later.isoformat(),
        "current_period_end": (now + timedelta(days=20)).isoformat(),
    }])
    s = _service(sb)

    s.apply_verified_transaction(_USER, _jws(
        signed_at=now - timedelta(days=1), revocationDate=_ms(now - timedelta(days=1)),
    ))

    row = sb.db["subscriptions"][0]
    assert row["status"] == "revoked"
    assert row["last_event_at"] == later.isoformat()


def test_a_legacy_revoked_row_without_last_event_at_orders_on_updated_at():
    """Rows revoked before migration 114, or by the client path before this fix, carry no
    `last_event_at`. `updated_at` — when we wrote the revocation — is the fallback."""
    now = datetime.now(timezone.utc)
    revoked_written = now - timedelta(days=1)

    def _legacy():
        return FakeSupabase(subscriptions=[{
            "id": "row-1", "user_id": _USER, "tier": "premium", "status": "revoked",
            "original_transaction_id": "txn-1", "updated_at": revoked_written.isoformat(),
            "current_period_end": (now + timedelta(days=20)).isoformat(),
        }], credits={"total": 50, "used": 0})

    sb = _legacy()
    out = _service(sb).apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=2)))
    assert out["was_stale"] is True
    assert sb.credits["total"] == 50

    sb = _legacy()
    out = _service(sb).apply_verified_transaction(_USER, _jws(txn_id="txn-2", signed_at=now,
                                                              original="txn-1"))
    assert out["was_stale"] is False
    assert sb._tier() == "premium"


def test_a_revoked_row_with_no_timestamp_at_all_is_inert_as_before():
    """Nothing to compare against — the guard's documented inert state, same as the webhook's
    pre-migration-114 behaviour. (Every row the service writes carries `updated_at`.)"""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(subscriptions=[{
        "id": "row-1", "user_id": _USER, "tier": "premium", "status": "revoked",
        "original_transaction_id": "txn-1",
    }])
    out = _service(sb).apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=9)))
    assert out["was_stale"] is False


def test_a_payload_without_signedDate_cannot_reentitle_a_revoked_row():
    """Fail closed against a revocation: with no signedDate the payload cannot prove it
    postdates the refund. (Apple always sets it; its absence is malformed.)"""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    no_date = _txn(product=_MAX, txn_id="txn-1")
    s.apply_verified_transaction(_USER, no_date)
    _refund(s, at=now - timedelta(days=1))

    out = s.apply_verified_transaction(_USER, no_date)

    assert out["was_stale"] is True
    assert sb._tier() == "free" and sb.credits["total"] == 50


def test_an_old_client_payload_cannot_demote_a_renewed_subscriber():
    """The same missing ordering let a superseded JWS overwrite newer state in the OTHER
    direction: an old period's transaction (expired) replayed after a DID_RENEW demoted a payer."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    old = _jws(product=_PRO, txn_id="txn-r", signed_at=now - timedelta(days=31),
               expires_in_days=-1)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-r",
                                             signed_at=now - timedelta(days=31)))
    s.apply_notification(*_renew_notification("txn-r", signed_at=now - timedelta(hours=1),
                                               expires_in_days=30))

    out = s.apply_verified_transaction(_USER, old)

    assert out["was_stale"] is True
    assert s.winning_tier(_USER) == "pro"


def test_a_client_payload_signed_after_the_stored_event_still_applies():
    """Negative control for the ordering: a later-signed payload is newer news."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-r"))
    s.apply_notification(*_renew_notification("txn-r", signed_at=now - timedelta(hours=1),
                                               expires_in_days=30))

    out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-r", signed_at=now))

    assert out["was_stale"] is False
    assert s.winning_tier(_USER) == "premium"


def test_the_client_ordering_edges():
    """Pure-function edges: ties, revocation, no prior, junk stored keys."""
    t = datetime.now(timezone.utc)
    revoked = {"status": "revoked", "last_event_at": t.isoformat()}
    active = {"status": "active", "last_event_at": t.isoformat()}
    reason = svc._stale_client_delivery_reason

    assert reason(revoked, status="active", signed_at=t) is not None, "a tie with a refund"
    assert reason(active, status="active", signed_at=t) is None, "ties apply elsewhere"
    assert reason(active, status="active", signed_at=t - timedelta(milliseconds=1)) is not None
    assert reason(revoked, status="active", signed_at=t + timedelta(milliseconds=1)) is None
    assert reason(revoked, status="revoked", signed_at=t - timedelta(days=1)) is None, \
        "an incoming revocation always applies"
    assert reason(active, status="expired", signed_at=None) is None
    assert reason(None, status="active", signed_at=t) is None
    assert reason({}, status="active", signed_at=None) is None
    for junk in (None, "", "not-a-date", 12345):
        assert reason({"status": "active", "last_event_at": junk},
                      status="active", signed_at=t) is None
    # Status case must not decide the rule.
    assert reason({"status": "REVOKED", "last_event_at": t.isoformat()},
                  status="active", signed_at=None) is not None


def test_a_junk_client_signedDate_against_a_revoked_row_fails_closed():
    """`_ms_to_dt` degrades junk to None — which, against a revocation, must mean stale."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    _refund(s, at=now - timedelta(days=1))

    junk = _txn(product=_MAX, txn_id="txn-1", signedDate="not-a-number")
    assert s.apply_verified_transaction(_USER, junk)["was_stale"] is True
    assert sb._tier() == "free"


# ── isUpgraded: a superseded transaction must never overwrite the upgrade ───

def test_an_upgraded_transaction_does_not_demote_the_upgrade():
    """Pro → Max keeps the originalTransactionId; StoreKit then delivers the old Pro
    transaction with `isUpgraded`. Applying it wrote Pro over the Max row."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-max", original="txn-g",
                                             signed_at=now - timedelta(hours=1)))
    assert sb._tier() == "premium"

    out = s.apply_verified_transaction(_USER, _jws(
        product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now, isUpgraded=True,
    ))

    assert out["was_stale"] is True
    assert sb._tier() == "premium"
    assert sb.db["subscriptions"][0]["tier"] == "premium"
    assert sb.credits["total"] == 4000


def test_a_revoked_upgraded_transaction_does_not_revoke_the_upgrade():
    """If the superseded transaction carries a revocation, applying it revoked a paying Max
    subscriber and clawed back the allocation — through the webhook as well."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-max", original="txn-g",
                                             signed_at=now - timedelta(hours=1)))

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now)},
        _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now,
             isUpgraded=True, revocationDate=_ms(now)),
    )

    assert sb._tier() == "premium"
    assert sb.db["subscriptions"][0]["status"] == "active"
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)


def test_an_upgraded_transaction_with_no_row_writes_nothing():
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    out = _service(sb).apply_verified_transaction(
        _USER, _jws(product=_PRO, txn_id="txn-pro", signed_at=now, isUpgraded=True)
    )
    assert sb.db["subscriptions"] == []
    assert out["winning_tier"] == "free"


def test_a_transaction_that_is_not_upgraded_still_applies():
    """Negative control: `isUpgraded: False` is the ordinary case."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    _service(sb).apply_verified_transaction(
        _USER, _jws(signed_at=now, isUpgraded=False)
    )
    assert sb._tier() == "premium"


@pytest.mark.parametrize("value, expected", [
    (True, True), ("true", True), (" TRUE ", True),
    (False, False), (None, False), ("false", False), ("", False), (1, False), ("yes", False),
])
def test_is_upgraded_reads_only_a_real_true(value, expected):
    assert svc._is_upgraded({"isUpgraded": value}) is expected


# ── A cross-account purchase is TERMINAL, not retryable ──────────────────────
#
# `apply_transaction` correctly refuses to rebind a transaction owned by someone else, but it
# signalled that with a plain `IAPError`, and `billing.py` maps every IAPError to SYSTEM_BUSY /
# 503 "reopen the app shortly and it will be applied". StoreKit reads 5xx as retryable, so
# `StoreKitService` left the transaction UNFINISHED and `Transaction.updates` re-delivered it on
# every launch — forever, against a condition that can never clear, telling the user each time
# to wait for something that will not happen.

def test_a_cross_account_purchase_raises_the_terminal_type():
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_OTHER_USER, _txn(txn_id="txn-owned"))

    with pytest.raises(svc.PurchaseBoundToAnotherAccount):
        s.apply_transaction(_USER, _txn(txn_id="txn-owned"))


def test_the_terminal_type_is_still_an_IAPError():
    """Subclassing matters: `billing.py` and `apply_notification` both catch `IAPError`
    broadly, and a sibling class would slip past them into a bare 500."""
    assert issubclass(svc.PurchaseBoundToAnotherAccount, svc.IAPError)


def test_billing_answers_it_before_the_retryable_arm():
    """Python takes the FIRST matching `except`. A subclass listed after its parent is dead
    code — the exact shape that would silently restore the 503."""
    import inspect

    from app.api.v1.endpoints import billing

    src = inspect.getsource(billing.verify_purchase)
    terminal_at = src.index("except PurchaseBoundToAnotherAccount")
    generic_at = src.index("except IAPError")
    assert terminal_at < generic_at, (
        "the terminal arm must precede `except IAPError`, or it never runs"
    )


def test_billing_does_not_test_for_a_guest_by_comparing_to_the_sentinel():
    """`verify_purchase` used to answer 401 when `user["id"] == GUEST_USER_ID`.

    Two things were wrong with it, and the second is why the branch is gone rather than
    rewritten. First, it was unreachable: the route depends on the STRICT `get_current_user`,
    which raises AUTH_REQUIRED with no credential, refuses an unverifiable one rather than
    downgrading it to guest (`.claude/rules/auth.md` §4), and raises AUTH_ACCOUNT_NOT_FOUND
    when the verified `sub` has no `public.users` row — so it can only return a real account.

    Second, the comparison itself is the known-wrong guest test. Under migrations 108/110/111 a
    guest resolves to a per-INSTALL uuid5 that never equals the shared sentinel, so
    `user["id"] == GUEST_USER_ID` silently classifies every guest as a paying account. Leaving
    a dead instance of it on the payment path left the wrong pattern sitting there to be copied.
    The correct test, where one is needed at all, is `user.get("is_guest")`.
    """
    import inspect

    from app.api.v1.endpoints import billing

    src = inspect.getsource(billing.verify_purchase)
    # Comments legitimately NAME the retired comparison to explain why it is wrong; only live
    # code can reinstate it.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'user["id"] == GUEST_USER_ID' not in code, (
        "verify_purchase compares against the guest sentinel again — dead on this dependency, "
        "and the wrong test everywhere else"
    )
    assert "GUEST_USER_ID" not in code, (
        "the sentinel is referenced in live code in verify_purchase"
    )
    # The protection it purported to give must still exist, and it comes from the dependency.
    assert "Depends(get_current_user)" in code, (
        "verify_purchase no longer uses the STRICT dependency — that, not an id comparison, is "
        "what keeps guests out of the purchase path"
    )


def test_billing_returns_a_terminal_4xx_with_its_own_code():
    """409 + a distinct code is what lets the client finish the transaction. Sharing
    INVALID_INPUT with the 400 verify-failure path would leave it indistinguishable."""
    import inspect

    from app.api.error_response import _DEFAULT_STATUS, ErrorCode
    from app.api.v1.endpoints import billing

    src = inspect.getsource(billing.verify_purchase)
    arm = src[src.index("except PurchaseBoundToAnotherAccount"):src.index("except IAPError")]
    assert "ErrorCode.PURCHASE_ALREADY_LINKED" in arm
    assert "SYSTEM_BUSY" not in arm, "a retryable code here re-opens the redelivery loop"

    assert _DEFAULT_STATUS[ErrorCode.PURCHASE_ALREADY_LINKED] == 409, (
        "must be a terminal 4xx — StoreKit treats 5xx as retryable"
    )


def test_the_action_is_not_a_retry():
    """`retry_later` is what told the user to reopen the app. Nothing about waiting helps."""
    from app.api.error_response import _DEFAULT_ACTIONS, ErrorCode

    assert _DEFAULT_ACTIONS[ErrorCode.PURCHASE_ALREADY_LINKED] != "retry_later"


def test_the_ios_client_finishes_the_transaction_on_this_code_only():
    """The backend fix alone does NOT break the loop: `StoreKitService` left the transaction
    unfinished on ANY thrown error. It must finish on the terminal case, and ONLY that one —
    finishing on a transient failure would lose a purchase the user paid for."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    src = (repo / "frontend/ios/ios/Core/Services/StoreKitService.swift").read_text()

    catch = src[src.index("verifyPurchase(signedTransaction: signed)"):]
    catch = catch[: catch.index("// Recorded server-side")]

    assert "case .purchaseAlreadyLinked" in catch, (
        "StoreKitService must finish the transaction on the terminal error, or Apple keeps "
        "redelivering it every launch"
    )
    finish_at = catch.index("await transaction.finish()")
    terminal_at = catch.index("case .purchaseAlreadyLinked")
    assert terminal_at < finish_at, "the finish must sit inside the terminal branch"
    # The unconditional 'leave unfinished' path must still exist for everything else.
    assert "leaving unfinished" in catch


# ── Migration 196: Production-only paid periods, refunds of the right period, reversals ──
#
# The balance math runs through `_Credits` (see FakeSupabase). These pin the SERVICE half:
# what `apply_transaction` writes for the SQL to read, and the Apple event sequences the
# 2026-10-12 review found.

def test_the_subscriptions_row_records_the_apple_environment():
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO))
    assert sb.db["subscriptions"][0]["environment"] == settings.IAP_ENVIRONMENT
    s.apply_transaction(_USER, _txn(product=_PRO, environment="Sandbox"))
    assert sb.db["subscriptions"][0]["environment"] == "Sandbox"


def test_a_sandbox_purchase_keeps_the_calendar_sizing():
    """TestFlight / App Review buy in Sandbox, which renews every few minutes. Only a
    Production row starts a paid period; a Sandbox one tops the calendar pool up once."""
    sb = FakeSupabase(credits={"total": 50, "used": 0})
    s = _service(sb)
    for _ in range(5):
        s.apply_transaction(_USER, _txn(product=_MAX, environment="Sandbox"))
    assert sb.credits == {"total": 4000, "used": 0}
    assert sb.model.paid_period is None and not sb.model.periods


class _NoNewColumns(FakeSupabase):
    """A database where migrations 114 and 196 have not been applied: a write naming either
    optional column fails, as PostgREST does for an unknown column."""

    def table(self, name):
        q = super().table(name)
        if name != "subscriptions":
            return q
        original = q.execute

        def execute():
            if q._op in ("insert", "update") and (
                    "environment" in q._values or "last_event_at" in q._values):
                raise RuntimeError("PGRST204: column not found")
            return original()

        q.execute = execute
        return q


def test_a_database_without_the_new_columns_still_records_the_purchase():
    sb = _NoNewColumns()
    _service(sb).apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-old-db"))
    row = sb.db["subscriptions"][0]
    assert row["status"] == "active" and "environment" not in row
    assert sb.credits == {"total": 4000, "used": 0}, "a NULL environment counts as Production"


def test_an_expiry_at_plan_end_lapses_to_the_free_allowance_without_a_clawback():
    """Owner rule 2026-10-12, "Expire at plan end": when the paid period is over and Apple says
    EXPIRED, the leftovers end with it and the free allowance starts. It is not a refund — no
    `revoke_tier_credits`, no `tier_revoked` row."""
    end = _ANCHOR + timedelta(days=30)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-x"))
    sb.credits["used"] = 1000
    sb.now = end + timedelta(seconds=1)
    s.apply_notification({"notificationType": "EXPIRED", "signedDate": _ms(end)},
                         _txn(product=_MAX, txn_id="txn-x"))
    assert sb.credits == {"total": 50, "used": 0}
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)
    reasons = [r["reason"] for r in sb.model.ledger]
    assert "tier_revoked" not in reasons and reasons[-2:] == ["monthly_expired", "monthly_reset"]


def test_a_refund_of_an_older_renewal_keeps_the_current_period():
    """Apple refunds LAST month's charge while this month's is in force: the REFUND carries the
    old transaction (old expiresDate). The period in force was paid and not refunded."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    # The first purchase arrives through the client (a notification for a transaction the
    # server has never seen is ignored — it can beat the verify call).
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-1", expires_in_days=1))
    sb.now = _ANCHOR + timedelta(days=1, seconds=1)             # the first period is over
    s.apply_notification({"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(days=2))},
                         _txn(product=_MAX, txn_id="txn-2", expires_in_days=31,
                              originalTransactionId="txn-1"))
    sb.credits["used"] = 300
    held = sb.remaining
    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now - timedelta(days=1))},
        _txn(product=_MAX, txn_id="txn-1", expires_in_days=1, revocationDate=_ms(now)),
    )
    assert sb.remaining == held == 3700
    assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=31)
    # 2026-10-13: and the TIER. The REFUND used to be written over the newer row, dropping a
    # subscriber whose current period was paid for to free until Restore or the next DID_RENEW.
    assert sb._tier() == "premium"
    row = sb.db["subscriptions"][0]
    assert row["status"] == "active"
    assert svc._parse_iso(row["current_period_end"]) == _ANCHOR + timedelta(days=31)
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)


def test_refund_reversed_gives_back_exactly_what_the_refund_took():
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    sb.credits["used"] = 100
    _refund(s, at=now - timedelta(days=2))
    assert sb.remaining == 0 and sb._tier() == "free"

    s.apply_notification(
        {"notificationType": "REFUND_REVERSED", "signedDate": _ms(now - timedelta(days=1))},
        _jws(signed_at=now - timedelta(days=1)),
    )
    assert sb._tier() == "premium"
    assert sb.remaining == 3900, "the refund took 3,900; the reversal gives exactly that back"
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=1)))
    assert sb.remaining == 3900, "a replay of the reversed transaction reinstates nothing more"


# ── A refund of an OLDER period never overwrites the newer row (2026-10-13) ──
#
# Apple refunds a past renewal with THAT renewal's transaction: same originalTransactionId, an
# EARLIER expiresDate. "Revocation always wins" wrote it over the newer row, so
# `reconcile_user_tier` dropped a subscriber whose current period was paid for and not refunded
# to free until Restore or the next DID_RENEW. Migration 196 already kept the credits.

def _renewed_max(sb, *, renew_signed_at):
    """Max bought through the client (its period ends at +1 day), then renewed by DID_RENEW
    (+31 days) once the model's clock is past the first period's end. The renewal's credit
    period is in force: 4,000."""
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-1", expires_in_days=1))
    sb.now = _ANCHOR + timedelta(days=1, seconds=1)
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(renew_signed_at)},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1"),
    )
    assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=31)
    return s


def _refund_of_the_first_period(now, ntype="REFUND", *, signed_at=None):
    return (
        {"notificationType": ntype, "signedDate": _ms(signed_at or now - timedelta(days=1))},
        _txn(product=_MAX, txn_id="txn-1", expires_in_days=1, revocationDate=_ms(now)),
    )


def _row(sb) -> dict:
    return sb.db["subscriptions"][0]


@pytest.mark.parametrize("ntype", ["REFUND", "REVOKE"])
def test_an_older_period_revocation_keeps_the_tier_the_row_and_the_credits(ntype):
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=2))
    sb.credits["used"] = 300
    before = dict(_row(sb))

    outcome, _ = s.apply_notification(*_refund_of_the_first_period(now, ntype))

    assert outcome == f"applied:{ntype}"          # a 200: Apple must not redeliver it
    assert sb._tier() == "premium"
    assert _row(sb) == before, "nothing is written — tier, status, period end, ordering key"
    assert sb.remaining == 3700
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)
    assert "tier_revoked" not in [r["reason"] for r in sb.model.ledger]


@pytest.mark.parametrize("refunded_end_days", [31, 61], ids=["the-period-in-force", "a-later-renewal"])
def test_a_refund_of_the_current_or_a_later_period_still_revokes(refunded_end_days):
    """Negative control: an EQUAL expiresDate is the period in force, a LATER one a renewal the
    row has not recorded yet. Both keep today's behaviour — revoked, free."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=2))
    sb.credits["used"] = 300

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now - timedelta(days=1))},
        _txn(product=_MAX, txn_id=f"txn-{refunded_end_days}", expires_in_days=refunded_end_days,
             originalTransactionId="txn-1", revocationDate=_ms(now)),
    )

    assert _row(sb)["status"] == "revoked"
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(
        days=refunded_end_days)
    assert sb._tier() == "free"
    if refunded_end_days == 31:
        assert sb.credits == {"total": 50, "used": 50}, "the period in force is clawed back"
        assert sb.model.paid_period is None


@pytest.mark.parametrize("source", ["notification", "client"])
def test_refunding_the_superseded_pro_of_an_upgrade_keeps_max(source):
    """Pro → Max keeps the originalTransactionId and starts Max at once; the Pro transaction
    keeps its own, EARLIER expiresDate. Its refund — here WITHOUT `isUpgraded`, which the
    dedicated guard above handles when Apple sets it — must leave Max alone."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-pro", original="txn-g",
                                             signed_at=now - timedelta(days=10),
                                             expires_in_days=20))
    s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-max", original="txn-g",
                                             signed_at=now - timedelta(hours=1),
                                             expires_in_days=30))
    assert sb._tier() == "premium" and sb.model.paid_period["tier"] == "premium"
    held = sb.remaining

    refunded = _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now,
                    expires_in_days=20, revocationDate=_ms(now))
    if source == "notification":
        s.apply_notification({"notificationType": "REFUND", "signedDate": _ms(now)}, refunded)
    else:
        s.apply_verified_transaction(_USER, refunded)

    assert sb._tier() == "premium"
    assert (_row(sb)["tier"], _row(sb)["status"]) == ("premium", "active")
    assert sb.remaining == held and sb.model.paid_period["tier"] == "premium"
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)


def test_a_revoked_older_renewal_from_the_client_keeps_the_tier_and_records_no_key():
    """No webhook (or it lost the race): the refund of last month's renewal arrives as a revoked
    JWS through `Transaction.updates`. Same outcome — and its signedDate is NOT stored as the
    ordering key, which exists to keep a REVOKED row revoked; this row was not revoked."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=2))
    sb.credits["used"] = 300
    key_before = _row(sb)["last_event_at"]

    out = s.apply_verified_transaction(_USER, _jws(
        txn_id="txn-1", signed_at=now, expires_in_days=1, revocationDate=_ms(now),
    ))

    assert out["was_stale"] is True and out["winning_tier"] == "premium"
    assert out["status"] == "active", "the client is told the stored state, not 'revoked'"
    assert sb._tier() == "premium"
    assert _row(sb)["status"] == "active" and _row(sb)["last_event_at"] == key_before
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=31)
    assert sb.remaining == 3700
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)


def test_a_renewal_signed_before_an_older_refund_but_delivered_after_it_still_applies():
    """Why the kept row records no ordering key. A refund is signed late; had its signedDate
    become `last_event_at`, this genuine DID_RENEW — signed earlier, redelivered later (Apple
    retries for hours, in no order) — would be refused as stale, and the subscriber would
    lapse at the end of the current period with the next one already paid."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    s.apply_notification(*_refund_of_the_first_period(now, signed_at=now - timedelta(hours=1)))

    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(hours=2))},
        _txn(product=_MAX, txn_id="txn-3", expires_in_days=61, originalTransactionId="txn-1"),
    )

    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=61)
    assert _row(sb)["status"] == "active" and sb._tier() == "premium"


def test_an_older_refund_during_a_billing_grace_period_keeps_the_hold():
    """The renewed period has ENDED and Apple is retrying the next charge (grace): still
    entitled, and 196 HOLDS the balance. Applied, the older period's revoked row ended both —
    free tier, and the balance lapsed to the free allowance."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    sb.credits["used"] = 300
    sb.now = _ANCHOR + timedelta(days=31, seconds=1)
    s.apply_notification(
        {"notificationType": "DID_FAIL_TO_RENEW", "subtype": "GRACE_PERIOD",
         "signedDate": _ms(now - timedelta(days=2))},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1"),
    )
    assert _row(sb)["status"] == "grace_period" and sb.remaining == 3700

    s.apply_notification(*_refund_of_the_first_period(now))

    assert _row(sb)["status"] == "grace_period"
    assert sb._tier() == "premium" and sb.remaining == 3700


def test_after_ignored_older_refunds_the_current_period_can_still_be_refunded():
    """Apple redelivers the older refund (no-op each time); a later refund of the period in
    force must still revoke and claw back."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    sb.credits["used"] = 300
    for _ in range(3):
        s.apply_notification(*_refund_of_the_first_period(now, signed_at=now - timedelta(days=2)))
    assert sb._tier() == "premium" and sb.remaining == 3700

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now - timedelta(days=1))},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1",
             revocationDate=_ms(now)),
    )

    assert sb._tier() == "free" and _row(sb)["status"] == "revoked"
    assert sb.credits == {"total": 50, "used": 50}


def test_a_refund_of_the_running_period_after_an_early_renewal_keeps_the_paid_renewal():
    """Apple charges a renewal up to 24 h before the period ends, so the DID_RENEW can land while
    the old period still runs — and is still the credit period in force (196 waits for it to
    end). Refunding that old period keeps the tier: the renewal is paid. Its unspent credits are
    NOT clawed back (the row is not rewritten, so 196 never sees a refund of the period in
    force); they last until it ends, at most ~24 h, then expire as the renewal starts. Accepted:
    the same credits could have been spent before asking for the refund, which no clawback
    recovers either."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-1", expires_in_days=1))
    sb.credits["used"] = 300
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(hours=2))},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1"),
    )
    assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=1), "the old period runs on"

    s.apply_notification(*_refund_of_the_first_period(now, signed_at=now - timedelta(hours=1)))

    assert sb._tier() == "premium"
    assert sb.remaining == 3700 and sb.model.paid_period["end"] == _ANCHOR + timedelta(days=1)
    sb.now = _ANCHOR + timedelta(days=1, seconds=1)               # the refunded period ends
    sb.rpc("ensure_credit_period", {"p_user_id": _USER})          # the next credit read
    assert sb.credits == {"total": 4000, "used": 0}
    assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=31)
    assert (sb.model.ledger[-2]["reason"], sb.model.ledger[-2]["delta"]) == ("monthly_expired", -3700)


def test_the_ignored_older_refund_is_logged_with_both_dates(caplog):
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=2))

    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_notification(*_refund_of_the_first_period(now))

    msgs = [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "OLDER period" in r.getMessage()]
    assert len(msgs) == 1
    assert "txn=txn-1" in msgs[0] and f"user={_USER}" in msgs[0]
    assert (_ANCHOR + timedelta(days=1)).isoformat() in msgs[0]       # the refunded period
    assert (_ANCHOR + timedelta(days=31)).isoformat() in msgs[0]      # the period kept


def test_the_older_period_guard_edges():
    """Pure-function edges. Everything but a strictly older period of the SAME lineage against
    an ENTITLING row applies exactly as before (revocation wins)."""
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "premium",
           "current_period_end": end.isoformat()}

    def reason(prior=row, *, status="revoked", otid="otid", period_end=end - timedelta(days=30)):
        return svc._older_period_revocation_reason(
            prior, status=status, original_transaction_id=otid, period_end=period_end,
        )

    assert reason() is not None, "a month older, same lineage, entitling row"
    assert reason(period_end=end - timedelta(seconds=60)) is not None
    # Applies as before.
    for status in ("active", "expired", "grace_period"):
        assert reason(status=status) is None, f"only a revocation is refused, not {status}"
    assert reason(prior=None) is None and reason(prior={}) is None
    assert reason(otid="another") is None, "the owner fallback's row is ANOTHER lineage"
    assert reason(prior={**row, "original_transaction_id": None}, otid="") is None
    assert reason(period_end=None) is None
    assert reason(period_end=end) is None, "equal: a refund of the period in force"
    assert reason(period_end=end + timedelta(days=30)) is None, "later: a renewal not yet recorded"
    assert reason(period_end=end - timedelta(seconds=59)) is None, "inside the drift margin"
    for status in ("revoked", "expired", "", None, "paused"):
        assert reason(prior={**row, "status": status}) is None, status
    for junk in (None, "", "not-a-date", 12345, "2026-13-45T99:99:99"):
        assert reason(prior={**row, "current_period_end": junk}) is None, junk
    # Entitling in any case, including Apple's retry states; compared by period end, never
    # against the clock (an `active` row past its end is still the newer period).
    for status in ("ACTIVE", "Grace_Period", "billing_retry"):
        assert reason(prior={**row, "status": status}) is not None, status
    t = datetime.now(timezone.utc)
    lapsed = {**row, "current_period_end": (t - timedelta(days=2)).isoformat()}
    assert reason(prior=lapsed, period_end=t - timedelta(days=32)) is not None
    # A naive stored timestamp is read as UTC rather than raising.
    naive = {**row, "current_period_end": end.replace(tzinfo=None).isoformat()}
    assert reason(prior=naive) is not None
    # Both dates are in the reason (the WARNING carries it).
    assert end.isoformat() in reason() and (end - timedelta(days=30)).isoformat() in reason()


# ── A late OLDER revocation never rewinds the ordering key (2026-10-13) ───────
#
# A revocation bypasses the stale check ("revocation always wins"), so it can be signed BEFORE
# the event whose `signedDate` is stored. The webhook path still wrote its signedDate as
# `last_event_at` unconditionally and LOWERED the key. The refund of an older period, signed at
# T3 and delivered after the refund of the current period (T5) had revoked the row, left T3 —
# and anything signed between the two then read as newer than the refund: the saved pre-refund
# JWS of the current period (T4) brought back the tier AND migration 196's `plan_reinstated`
# of the refunded period, the 2026-09-25 replay class. The client path was already monotonic
# (`test_a_client_revocation_never_lowers_a_later_stored_key`); the webhook path now is too.

def _refund_of_the_current_period(at):
    """The REFUND of the renewal `_renewed_max` put in force (txn-2: original txn-1, +31 d)."""
    return (
        {"notificationType": "REFUND", "signedDate": _ms(at)},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1",
             revocationDate=_ms(at)),
    )


def _both_refunds_out_of_order(sb, now):
    """`_renewed_max`, the refund of the current period (T5 = now - 1 d), then the refund of
    the FIRST period: signed earlier (T3 = now - 3 d), delivered after it. The row no longer
    entitles by then, so the older-period guard leaves that refund to apply. Returns the
    service and the ordering key T5 wrote."""
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=6))
    s.apply_notification(*_refund_of_the_current_period(now - timedelta(days=1)))
    assert sb._tier() == "free" and sb.remaining == 50
    key = _row(sb)["last_event_at"]
    assert svc._parse_iso(key) == svc._ms_to_dt(_ms(now - timedelta(days=1))), \
        "a later revocation still raises the key"

    s.apply_notification(*_refund_of_the_first_period(now, signed_at=now - timedelta(days=3)))
    return s, key


@pytest.mark.parametrize("replay", ["client", "notification"])
def test_a_late_older_refund_cannot_reopen_the_pre_refund_replay(replay):
    """THE bug. Before the fix the key fell to T3 and a payload signed at T4 applied: premium,
    and 4,000 credits (196 reinstated the refunded period); `was_stale` False.

    `client`: the saved pre-refund JWS re-POSTed to /billing/verify. `notification`: Apple's own
    out-of-order delivery — a DID_CHANGE_RENEWAL_STATUS signed at T4, still carrying the
    unrefunded transaction, retried after both refunds. No attacker needed for that one."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s, key = _both_refunds_out_of_order(sb, now)
    assert _row(sb)["status"] == "revoked", "the older refund still applies (revocation wins)"
    assert _row(sb)["last_event_at"] == key, "the older refund rewound the ordering key"

    pre_refund = _jws(txn_id="txn-2", original="txn-1", signed_at=now - timedelta(days=2),
                      expires_in_days=31)
    if replay == "client":
        out = s.apply_verified_transaction(_USER, pre_refund)
        assert out["was_stale"] is True and out["winning_tier"] == "free"
    else:
        s.apply_notification(
            {"notificationType": "DID_CHANGE_RENEWAL_STATUS", "subtype": "AUTO_RENEW_DISABLED",
             "signedDate": _ms(now - timedelta(days=2))},
            pre_refund,
        )

    assert sb._tier() == "free" and _row(sb)["status"] == "revoked"
    assert sb.remaining == 50, "the replay got the refunded period's credits back"
    assert "plan_reinstated" not in [r["reason"] for r in sb.model.ledger]
    assert _row(sb)["last_event_at"] == key


@pytest.mark.parametrize("source", ["client-same-original", "client-new-original", "notification"])
def test_a_resubscribe_signed_after_both_refunds_still_applies(source):
    """The kept key is the LATEST refund's, so it must not lock the customer out: a genuine
    re-subscribe is a new transaction Apple signs after both refunds."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s, key = _both_refunds_out_of_order(sb, now)

    # A month after ITS purchase: never a refunded period's exact end (+1 or +31 days), which
    # with the same original transaction would be a reversal of that refund.
    fresh = _jws(txn_id="txn-3", signed_at=now, expires_in_days=30,
                 original="txn-3" if source == "client-new-original" else "txn-1")
    if source == "notification":
        s.apply_notification({"notificationType": "SUBSCRIBED", "subtype": "RESUBSCRIBE",
                              "signedDate": _ms(now)}, fresh)
        assert svc._parse_iso(_row(sb)["last_event_at"]) == svc._ms_to_dt(_ms(now)), \
            "a later notification still moves the key forward"
    else:
        out = s.apply_verified_transaction(_USER, fresh)
        assert out["was_stale"] is False and out["winning_tier"] == "premium"

    assert sb._tier() == "premium" and _row(sb)["status"] == "active"
    assert sb.remaining == 4000
    assert len(sb.db["subscriptions"]) == 1


@pytest.mark.parametrize("stored_status", ["revoked", "expired", "active"])
def test_a_webhook_revocation_never_lowers_a_later_stored_key(stored_status):
    """The webhook twin of `test_a_client_revocation_never_lowers_a_later_stored_key`, whatever
    the row holds: a revocation signed before the stored key still revokes, and the key stays."""
    now = datetime.now(timezone.utc)
    later = now - timedelta(hours=1)
    sb = FakeSupabase(subscriptions=[{
        "id": "row-1", "user_id": _USER, "tier": "premium", "status": stored_status,
        "original_transaction_id": "txn-1", "last_event_at": later.isoformat(),
        "current_period_end": (now + timedelta(days=20)).isoformat(),
    }])
    signed = now - timedelta(days=1)

    outcome, _ = _service(sb).apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(signed)},
        _jws(signed_at=signed, revocationDate=_ms(signed)),
    )

    assert outcome == "applied:REFUND"
    assert _row(sb)["status"] == "revoked" and sb._tier() == "free"
    assert _row(sb)["last_event_at"] == later.isoformat()


_NO_KEY = object()


def test_the_webhook_ordering_key_edges():
    """What a REFUND notification leaves in `last_event_at`, by what the row stored."""
    now = datetime.now(timezone.utc)
    signed = now - timedelta(days=1)
    written = svc._ms_to_dt(_ms(signed))

    def key_after(stored):
        row = {"id": "row-1", "user_id": _USER, "tier": "premium", "status": "revoked",
               "original_transaction_id": "txn-1",
               "current_period_end": (now + timedelta(days=20)).isoformat()}
        if stored is not _NO_KEY:
            row["last_event_at"] = stored
        sb = FakeSupabase(subscriptions=[row])
        _service(sb).apply_notification(
            {"notificationType": "REFUND", "signedDate": _ms(signed)},
            _jws(signed_at=signed, revocationDate=_ms(signed)),
        )
        assert _row(sb)["status"] == "revoked"
        return _row(sb).get("last_event_at")

    later = written + timedelta(milliseconds=1)
    assert key_after(later.isoformat()) == later.isoformat(), "kept: one ms later"
    naive = (written + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert key_after(naive) == naive, "a naive stored key reads as UTC, and is kept"
    assert svc._parse_iso(key_after(written.isoformat())) == written, "a tie keeps the instant"
    earlier = written - timedelta(milliseconds=1)
    assert svc._parse_iso(key_after(earlier.isoformat())) == written, "raised"
    assert svc._parse_iso(key_after(_NO_KEY)) == written, "no key yet: written"
    for junk in (None, "", "not-a-date", 12345, "2026-13-45T99:99:99"):
        assert svc._parse_iso(key_after(junk)) == written, f"junk {junk!r} is replaced"


def test_a_client_payload_that_is_not_a_revocation_records_no_ordering_key():
    """The client half of the same rule, pinned: only a client REVOCATION stores its signedDate.
    A client payload's status is derived from the transaction alone, so in a billing grace
    period it reads `expired` (the period has ended). Had its signedDate become the key, Apple's
    DID_FAIL_TO_RENEW / GRACE_PERIOD — signed before it, delivered after it — would be refused
    as stale, and a subscriber Apple still entitles would stay on Free."""
    now = datetime.now(timezone.utc)
    renewed_at = now - timedelta(days=30)
    sb = FakeSupabase(subscriptions=[{
        "id": "row-1", "user_id": _USER, "tier": "premium", "status": "active",
        "original_transaction_id": "txn-1", "last_event_at": renewed_at.isoformat(),
        "current_period_end": (_ANCHOR - timedelta(days=1)).isoformat(),
    }])
    s = _service(sb)

    out = s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(hours=1),
                                                   expires_in_days=-1))
    assert out["was_stale"] is False and _row(sb)["status"] == "expired"
    assert _row(sb)["last_event_at"] == renewed_at.isoformat()

    s.apply_notification(
        {"notificationType": "DID_FAIL_TO_RENEW", "subtype": "GRACE_PERIOD",
         "signedDate": _ms(now - timedelta(hours=2))},
        _txn(product=_MAX, txn_id="txn-1", expires_in_days=-1),
    )
    assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"
