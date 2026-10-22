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

import inspect
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

# Tables and RPCs migration 198 adds: before it, PostgREST answers PGRST205 / PGRST202.
_LINEAGE_ONLY_TABLES = frozenset({"subscription_transactions"})
_LINEAGE_ONLY_RPCS = frozenset({"record_subscription_transaction", "revoke_paid_period",
                                "reinstate_paid_carry"})
# The schema a FakeSupabase defaults to — set per test by the autouse `_schema` fixture.
_DEFAULT_SCHEMA = {"schema": "legacy"}


@pytest.fixture(autouse=True, params=["legacy", "lineage"])
def _schema(request):
    """Every test in this file runs against the database before migration 198 AND after it.
    A test whose expectation differs reads `request.node.callspec`, via the `schema` fixture."""
    _DEFAULT_SCHEMA["schema"] = request.param
    yield request.param
    _DEFAULT_SCHEMA["schema"] = "legacy"


@pytest.fixture
def schema(_schema):
    return _schema


_UNKNOWN_MARK = "infinity"            # how PostgREST renders timestamptz 'infinity'
_ENTITLING = ("active", "grace_period", "billing_retry")


def _keep_max_period_end(old, new) -> None:
    """Migration 198's trigger `subscriptions_max_period_end`: the latest period end the row has
    ever held, for every writer; never touches updated_at. A re-point to another lineage starts
    again from its own period end; an unknown mark ('infinity') stays unknown."""
    if old is not None and old.get("original_transaction_id") != new.get("original_transaction_id"):
        new["max_period_end"] = new.get("current_period_end")
        return
    if _UNKNOWN_MARK in ((old or {}).get("max_period_end"), new.get("max_period_end")):
        new["max_period_end"] = _UNKNOWN_MARK
        return
    ends = [svc._parse_iso(v) for v in (
        (old or {}).get("max_period_end"), (old or {}).get("current_period_end"),
        new.get("max_period_end"), new.get("current_period_end"))]
    ends = [e for e in ends if e is not None]
    new["max_period_end"] = max(ends).isoformat() if ends else None


def _backfill_max_period_end(rows) -> None:
    """Migration 198's backfill over the rows that exist when it is applied: the mark is known
    only for a row that entitles then; any other gets 'infinity' (its end may have been rewound)."""
    for r in rows:
        if r.get("max_period_end") is not None:
            continue
        if str(r.get("status") or "") in _ENTITLING:
            if r.get("current_period_end") is not None:
                r["max_period_end"] = r["current_period_end"]
        else:
            r["max_period_end"] = _UNKNOWN_MARK


class _PgError(Exception):
    """What postgrest-py raises: an APIError carrying the SQLSTATE / PostgREST code as `.code`,
    which is what the service classifies on (never the message text)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code


class _Q:
    def __init__(self, db, log, table, fail, owner=None):
        self._db, self._log, self._table, self._fail = db, log, table, fail
        self._owner = owner
        self._op = None
        self._values = None
        self._filters: dict = {}
        self._order = None
        self._limit = None

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
        # postgrest-py sends `col=eq.None`, which Postgres rejects; Python's None == None would
        # quietly match instead and hide the bug.
        if val is None:
            raise _PgError("22P02", f"invalid input syntax: {col}=eq.None")
        self._filters[col] = val
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def execute(self):
        if self._table in self._fail:
            raise RuntimeError(f"{self._table} unavailable")
        lineage = self._owner is not None and self._owner.schema == "lineage"
        if self._table in _LINEAGE_ONLY_TABLES and not lineage:
            # Migration 198 not applied: PostgREST's schema-cache miss, as the real client raises.
            raise _PgError("PGRST205", f"Could not find the table 'public.{self._table}'")
        if self._owner is not None:
            self._owner._materialize(self._table)
        rows = self._db.setdefault(self._table, [])
        if self._op == "select":
            # COPIES, as the real client returns fresh JSON: a live row dict would follow a
            # concurrent write, and a write fenced on what was read would then always pass.
            out = [
                dict(r) for r in rows
                if all(r.get(k) == v for k, v in self._filters.items())
            ]
            if self._order is not None:                 # NULLS LAST, as PostgREST's default
                col, desc = self._order
                present = sorted((r for r in out if r.get(col) is not None),
                                 key=lambda r: r[col], reverse=desc)
                out = present + [r for r in out if r.get(col) is None]
            if self._limit is not None:
                out = out[:self._limit]
            return type("R", (), {"data": out})()
        if self._op == "insert":
            if self._table == "subscriptions":
                # One row per account before 198; one per lineage since.
                key, constraint = (("original_transaction_id", "subscriptions_original_transaction_id_key")
                                   if lineage else ("user_id", "subscriptions_user_id_key"))
                if any(r.get(key) == self._values.get(key) for r in rows):
                    raise _PgError("23505", "duplicate key value violates unique constraint "
                                            f'"{constraint}"')
            rows.append({"id": f"row-{len(rows) + 1}", **self._values})
            if self._table == "subscriptions" and lineage:
                _keep_max_period_end(None, rows[-1])
            self._log.append(("insert", self._table))
            return type("R", (), {"data": [rows[-1]]})()
        if self._op == "update":
            touched = []
            for r in rows:
                if all(r.get(k) == v for k, v in self._filters.items()):
                    old = dict(r)
                    r.update(self._values)
                    if self._table == "subscriptions" and lineage:
                        _keep_max_period_end(old, r)
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

    The `rpc` handler runs six RPCs — `ensure_credit_period`, `grant_tier_upgrade`,
    `revoke_tier_credits`, `record_subscription_transaction`, `revoke_paid_period` and
    `reinstate_paid_carry` — through `_Credits`, the ONE executable model of that SQL
    (tests/test_credit_pool_isolation.py, modelled on migrations 196, 198 and 200; the branches it
    does NOT model are listed in SYSTEM_DESIGN_GUIDELINES §9b.12, "the executable model") — fed
    with this fake's own `users` and `subscriptions` rows before every call. That matters: a fake
    that returns a bland success for every rpc makes a credit test pass no matter what the
    balance does, which is exactly how a money bug survives a green suite, and a SECOND
    hand-written model drifts (this one still encoded migration 114 after 139 and 196 had
    changed the SQL). Change the SQL, change `_Credits`.

    `schema` is which database the service talks to: "legacy" (before migration 198: one
    subscriptions row per account, no `subscription_transactions`, which answers PGRST205) or
    "lineage" (198: one row per lineage, the register, the `max_period_end` trigger and the keyed
    RPCs). The default comes from the autouse `_schema` fixture, so every test here runs on both.
    The register and the credit periods live in the model (`register`, `periods`); the
    `subscription_transactions` / `subscription_credit_periods` tables are rendered from them.
    """

    def __init__(self, fail=(), subscriptions=None, credits=None, period_due=False, now=None,
                 schema=None):
        self.db = {"subscriptions": list(subscriptions or []), "users": [{"id": _USER}]}
        self.log: list = []
        self._fail = set(fail)
        self.rpcs: list = []
        self._schema = None
        self.schema = schema or _DEFAULT_SCHEMA["schema"]
        # migration 198's tables, shared by every model of this account.
        self.periods: dict = {}
        self.register: dict = {}
        # None = no balance row yet (first touch). Otherwise the model of the user_credits row.
        self.model = None
        if credits is not None:
            self.model = self._new_model(total=credits["total"], used=credits["used"])
            self.model.period_due = period_due
        # The model's clock. None = real time, which is what the service compares against.
        self.now = now

    @property
    def schema(self) -> str:
        return self._schema

    @schema.setter
    def schema(self, value: str) -> None:
        """Setting "lineage" over rows that exist is the owner applying 198: its backfill runs."""
        if value == "lineage" and self._schema != "lineage":
            _backfill_max_period_end(self.db["subscriptions"])
        self._schema = value

    @property
    def lineage(self) -> bool:
        return self.schema == "lineage"

    def _new_model(self, **kw) -> _Credits:
        return _Credits(periods=self.periods, register=self.register, **kw)

    @property
    def credits(self):
        return None if self.model is None else _CreditsView(self.model)

    def table(self, name):
        return _Q(self.db, self.log, name, self._fail, owner=self)

    def _materialize(self, table: str) -> None:
        """Render the model-owned tables as PostgREST would return them (ISO strings)."""
        iso = lambda v: v.isoformat() if isinstance(v, datetime) else v   # noqa: E731
        if table == "subscription_transactions":
            self.db[table] = [
                {"id": i + 1, "environment": env, "transaction_id": txn,
                 "original_transaction_id": rec["otid"], "user_id": rec["user"],
                 "tier": rec["tier"], "period_end": iso(rec["period_end"]),
                 "revocation_date": iso(rec.get("revocation_date")),
                 "applied_at": iso(rec["applied_at"]), "revoked_signed_at": iso(rec["rev"]),
                 "reversed_signed_at": iso(rec["rr"]), "created_at": iso(rec.get("created"))}
                # NEWEST first, unlike creation order (a Postgres heap after updates is unordered
                # too): a query that needs the first record must ask for it with order().
                for i, ((env, txn), rec) in reversed(list(enumerate(self.register.items())))]
        elif table == "user_credits" and self.model is not None:
            self.db[table] = [{"user_id": _USER, "total": self.model.total, "used": self.model.used,
                               "paid_period_id": (self.model.paid_period or {}).get("id")}]
        elif table == "subscription_credit_periods":
            self.db[table] = [
                {"id": p.get("id"), "user_id": _USER, "original_transaction_id": otid,
                 "period_start": iso(p.get("start")), "period_end": iso(end),
                 "revoked_at": iso(p.get("revoked_at")), "tier": p.get("tier")}
                for (otid, end), p in self.periods.items()]

    def _user(self) -> dict:
        return next((r for r in self.db["users"] if r.get("id") == _USER), {})

    def _tier(self) -> str:
        return (self._user().get("tier") or "free").lower()

    def _sync(self, model: _Credits) -> None:
        """What the SQL reads: users.tier / comp_tier and the account's subscriptions rows —
        before 198 its ONE row, since 198 one per lineage (the SQL picks the winning row and the
        in-force lineage's own row; the model does the same)."""
        model.now = self.now or datetime.now(timezone.utc)
        model.tier = self._tier()
        model.comp_tier = (self._user().get("comp_tier") or None)
        rows = [r for r in self.db["subscriptions"] if r.get("user_id") == _USER]
        if not rows:
            model.subscription = None
            return
        if not self.lineage:
            rows = rows[:1]
        model.subscriptions = []
        for r in rows:
            updated = svc._parse_iso(r.get("updated_at"))
            model.subscriptions.append(model._sub_row(
                r.get("original_transaction_id"), (r.get("tier") or "free"),
                (r.get("status") or ""), svc._parse_iso(r.get("current_period_end")),
                r.get("environment"), updated=updated.timestamp() if updated else 0.0,
                row_id=str(r.get("id") or "")))

    def _ensure_model(self) -> _Credits:
        if self.model is None:
            # The SQL's first touch: a calendar-mode row at the calendar allowance, then
            # the same call falls through to any paid period.
            self.model = self._new_model(total=0)
            self._sync(self.model)
            alloc = self.model._calendar_alloc()
            self.model.total = self.model.tier_alloc = alloc
        return self.model

    def rpc(self, name, params):
        if "rpc" in self._fail or name in self._fail:
            raise RuntimeError(f"rpc {name} unavailable")
        self.rpcs.append((name, params))
        data = None
        if name in _LINEAGE_ONLY_RPCS and not self.lineage:
            raise _PgError("PGRST202", f"Could not find the function public.{name}")

        if name in ("ensure_credit_period", "grant_tier_upgrade"):
            self._ensure_model()
            self._sync(self.model)
            data = getattr(self.model, name)()
        elif name == "revoke_tier_credits":
            if self.model is not None:
                self._sync(self.model)
                data = self.model.revoke_tier_credits()
        elif name == "record_subscription_transaction":
            owner = params["p_user_id"] if any(
                u.get("id") == params["p_user_id"] for u in self.db["users"]) else None
            recorder = self.model or self._new_model(total=0)
            recorder.now = self.now or datetime.now(timezone.utc)
            data = recorder.record_subscription_transaction(
                params["p_environment"], params["p_transaction_id"],
                params["p_original_transaction_id"], owner, params["p_tier"],
                svc._parse_iso(params["p_period_end"]), svc._parse_iso(params["p_revocation_date"]),
                params["p_kind"], svc._parse_iso(params["p_signed_at"]))
        elif name == "revoke_paid_period":
            if self.model is None:
                data = {"outcome": "no_balance"}
            else:
                self._sync(self.model)
                data = self.model.revoke_paid_period(
                    params["p_environment"], params["p_transaction_id"],
                    params["p_original_transaction_id"], svc._parse_iso(params["p_period_end"]))
        elif name == "reinstate_paid_carry":
            if self.model is None:
                data = {"outcome": "no_balance"}
            else:
                self._sync(self.model)
                data = self.model.reinstate_paid_carry(
                    params["p_original_transaction_id"], svc._parse_iso(params["p_period_end"]))

        result = type("R", (), {"data": data})()
        return type("Call", (), {"execute": lambda *_a, **_k: result})()

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
    credit_rpcs = [name for name, _ in sb.rpcs if name != "record_subscription_transaction"]
    assert credit_rpcs == ["ensure_credit_period", "grant_tier_upgrade"]
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


def test_the_grant_delegates_and_the_mid_month_top_up_keeps_112s_two_invariants():
    """Source-scan on the LIVE SQL, because the Python fake can only model it.

    112's two properties are what made the grant safe, and both are one careless edit away from
    a money bug: drop the replay guard and every app launch mints credits; switch to a bare
    assignment (or write `used`) and a downgrade deletes credits the user paid for.

    This used to open `112_grant_tier_upgrade.sql` by name, comments included, so it pinned a
    body that stopped running at 196 (IAP audit F19): it passed whatever the live functions did.
    Since 196 grant_tier_upgrade is a pure delegate to ensure_credit_period, and the two
    properties live in that function's mid-month top-up (`IF v_alloc > v_row.tier_alloc THEN`;
    `total = GREATEST(v_alloc, v_row.total)`, never `used`). Both are now read from the latest
    definitions by the detectors in test_credit_review_fixes, whose in-memory mutant table
    (`test_the_live_sql_detectors_catch_each_later_redefinition`) proves each one fails on a
    broken later migration. 112's old "the UPDATE touches total, never used" is restated there:
    the grant itself has no UPDATE any more."""
    from test_credit_review_fixes import _grant_problems, _top_up_problems

    problems = _grant_problems() + _top_up_problems()
    assert not problems, problems


# ── Winning tier ──────────────────────────────────────────────────────────────

def _sub(tier, status="active", days=30, user=_USER, txn="t1"):
    end = (
        (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        if days is not None else None
    )
    return {
        "id": f"id-{txn}", "user_id": user, "tier": tier, "status": status,
        "current_period_end": end, "original_transaction_id": txn,
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
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
    if sb.lineage:
        # Migration 198: the KEYED clawback, for exactly the refunded period (a no-op here once
        # ensure_credit_period did it), replaces the legacy backstop.
        keyed = [p for n, p in sb.rpcs if n == "revoke_paid_period"]
        assert keyed and keyed[-1]["p_original_transaction_id"] == "txn-refund"
        assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)
    else:
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

    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)
    if sb.lineage:
        # Migration 198: the still-paying lineage's own paid period starts — its allocation, not
        # the seeded calendar balance — and the tier stays Pro.
        assert s.winning_tier(_USER) == "pro"
        assert sb.model.paid_period["otid"] == "txn-b" and sb.remaining == 1200
    else:
        assert sb.credits["total"] == 4000, "must not claw back from a still-entitled user"


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
    # Migration 198: a new lineage gets its own row; before it, the one row is reused.
    assert len(sb.db["subscriptions"]) == (2 if sb.lineage and new_lineage else 1)


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
    pro_row = _row_of(sb, "txn-pro") if sb.lineage and new_lineage else _row(sb)
    assert pro_row["tier"] == "pro"
    if sb.lineage and new_lineage:
        assert _row_of(sb, "txn-1")["status"] == "revoked", "the refunded lineage stays revoked"


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
            # Revoked before 198 at an EARLIER end than the replayed JWS's (+30 d): after 198
            # its backfilled mark is unknown, so Rule D never reads the JWS as a newer period
            # (review #17 — this fixture once moved to +30 d and hid exactly that).
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
    unfinished on ANY thrown error. A failed verify must finish on PURCHASE_ALREADY_LINKED and
    PURCHASE_REVOKED only — the two codes that can never become grantable — and leave every other
    failure unfinished: finishing a transient or a recorded-nothing failure (503 SYSTEM_BUSY,
    409 PURCHASE_ACCOUNT_MISMATCH, a 400) loses a purchase the user paid for.

    This used to check only that `case .purchaseAlreadyLinked` came before the FIRST finish, on
    raw text, so a mutant finishing EVERY failed verify passed it (IAP audit F18). It now runs the
    one brace-bound, comment-stripped checker in test_ios_purchase_failure_visibility, whose
    in-memory mutant table proves it catches that and the other ways to finish too much."""
    from test_ios_purchase_failure_visibility import _SERVICE, _finish_policy_violations, _read

    violations = _finish_policy_violations(_read(_SERVICE))
    assert not violations, violations


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
                raise _PgError("PGRST204", "Could not find the column in the schema cache")
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


def _row_of(sb, original_transaction_id: str) -> dict:
    """Migration 198: one row per lineage — the row of THIS original transaction."""
    return next(r for r in sb.db["subscriptions"]
                if r.get("original_transaction_id") == original_transaction_id)


@pytest.mark.parametrize("ntype", ["REFUND", "REVOKE"])
def test_an_older_period_revocation_keeps_the_tier_the_row_and_the_credits(ntype):
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=2))
    sb.credits["used"] = 300
    before = dict(_row(sb))

    outcome, _ = s.apply_notification(*_refund_of_the_first_period(now, ntype))

    assert outcome == f"unapplied:older_period:{ntype}"          # a 200: Apple must not redeliver it
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
    dedicated guard above handles when Apple sets it — must leave Max alone. The credits the
    upgrade CARRIED from that Pro period stay in the Max balance: an accepted gap (§9b.12)."""
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
    assert sb.model.paid_period["tier"] == "premium"
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)
    if sb.lineage:
        # Migration 198 (owner, 2026-10-14, "Close it now"): the leftovers the upgrade CARRIED
        # from the refunded Pro period are taken back, once; the Max allocation stays.
        carried = sb.model.paid_period["carried"]
        assert carried > 0 and sb.remaining == held - carried
    else:
        assert sb.remaining == held, "before 198 the carry survives (accepted gap, §9b.12)"


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
    if sb.lineage:
        # Migration 198's keyed clawback: the refunded period's unspent credits go at once, and
        # the paid renewal starts now instead of at the old end (its allocation, once).
        assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=31)
        assert sb.credits == {"total": 4000, "used": 0}
        refunded = sb.periods[("txn-1", _ANCHOR + timedelta(days=1))]
        assert refunded["revoked_at"] is not None and refunded["revoked_remaining"] == 3700
        return
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
    """Pure-function edges. Only a delivery about a strictly older period of the SAME lineage,
    against an ENTITLING row, is refused; never a subscription-state notification, an entitling
    higher tier (an upgrade of any length) or a Production overlap upgrade's refund."""
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "pro",
           "current_period_end": end.isoformat()}

    def reason(prior=row, *, status="revoked", override=None, tier="pro", env="Production",
               otid="otid", period_end=end - timedelta(days=30)):
        return svc._older_period_reason(
            prior, status=status, status_override=override, tier=tier, environment=env,
            original_transaction_id=otid, period_end=period_end,
        )

    # Any status about a TRANSACTION of the same or a lower tier: a refund, a renewal, or a
    # status derived from the payload (client JWS, REFUND_REVERSED, CONSUMPTION_REQUEST, ...).
    for status, override in (("revoked", None), ("revoked", "revoked"), ("expired", None),
                             ("active", None), ("active", "active")):
        assert reason(status=status, override=override) is not None, (status, override)
    for tier in ("pro", "free", "not-a-tier"):
        assert reason(tier=tier, period_end=end - timedelta(days=1)) is not None, tier
    # Apple stating the SUBSCRIPTION's condition is honoured as before.
    assert reason(status="expired", override="expired") is None
    assert reason(status="grace_period", override="grace_period") is None
    # Boundaries, dates, lineage, the stored row.
    assert reason(period_end=end - timedelta(seconds=60)) is not None
    assert reason(period_end=end - timedelta(seconds=59)) is None, "inside the drift margin"
    assert reason(period_end=end) is None, "equal: the period in force"
    assert reason(period_end=end + timedelta(days=30)) is None, "later: a renewal not yet recorded"
    assert reason(period_end=None) is None
    assert reason(prior=None) is None and reason(prior={}) is None
    assert reason(otid="another") is None, "the owner fallback's row is ANOTHER lineage"
    assert reason(prior={**row, "original_transaction_id": None}, otid="") is None
    for status in ("revoked", "expired", "", None, "paused"):
        assert reason(prior={**row, "status": status}) is None, status
    for junk in (None, "", "not-a-date", 12345, "2026-13-45T99:99:99"):
        assert reason(prior={**row, "current_period_end": junk}) is None, junk
    for status in ("ACTIVE", "Grace_Period", "billing_retry"):
        assert reason(prior={**row, "status": status}) is not None, status
    t = datetime.now(timezone.utc)
    lapsed = {**row, "current_period_end": (t - timedelta(days=2)).isoformat()}
    assert reason(prior=lapsed, period_end=t - timedelta(days=32)) is not None, "never the clock"
    naive = {**row, "current_period_end": end.replace(tzinfo=None).isoformat()}
    assert reason(prior=naive) is not None
    # An entitling HIGHER tier is an upgrade, whatever its period length: it applies.
    for status in ("active", "grace_period"):
        for gap in (timedelta(minutes=5), timedelta(days=4), timedelta(days=55)):
            assert reason(status=status, tier="premium", period_end=end - gap) is None, gap
    # A REVOKED higher tier within 7 days is the refund of an overlap upgrade (Production only).
    for gap in (timedelta(minutes=5), timedelta(days=4), timedelta(days=7)):
        assert reason(tier="premium", period_end=end - gap) is None, gap
        assert reason(tier="premium", env=None, period_end=end - gap) is None, gap
        assert reason(tier="premium", env="Sandbox", period_end=end - gap) is not None, gap
    assert reason(tier="premium", period_end=end - timedelta(days=7, seconds=1)) is not None
    assert reason(tier="premium", period_end=end - timedelta(days=30)) is not None, (
        "the last Max month before a downgrade is an older period")
    # A higher tier that merely EXPIRED is an older period at any distance.
    assert reason(status="expired", tier="premium", period_end=end - timedelta(days=1)) is not None
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

    new_row = sb.lineage and source == "client-new-original"
    assert sb._tier() == "premium"
    assert (_row_of(sb, "txn-3") if new_row else _row(sb))["status"] == "active"
    assert sb.remaining == 4000
    assert len(sb.db["subscriptions"]) == (2 if new_row else 1)


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
    as stale, and a subscriber Apple still entitles would stay on Free.

    The period ended two days ago, past the expiry sweep's 24 h `active` window: inside it, the
    same client payload is HELD and writes nothing at all (2026-10-15,
    `test_a_derived_expiry_before_the_grace_notification_arrives_is_held`)."""
    now = datetime.now(timezone.utc)
    renewed_at = now - timedelta(days=30)
    sb = FakeSupabase(subscriptions=[{
        "id": "row-1", "user_id": _USER, "tier": "premium", "status": "active",
        "original_transaction_id": "txn-1", "last_event_at": renewed_at.isoformat(),
        "current_period_end": (_ANCHOR - timedelta(days=2)).isoformat(),
    }])
    s = _service(sb)

    out = s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(hours=1),
                                                   expires_in_days=-2))
    assert out["was_stale"] is False and _row(sb)["status"] == "expired"
    assert _row(sb)["last_event_at"] == renewed_at.isoformat()

    s.apply_notification(
        {"notificationType": "DID_FAIL_TO_RENEW", "subtype": "GRACE_PERIOD",
         "signedDate": _ms(now - timedelta(hours=2))},
        _txn(product=_MAX, txn_id="txn-1", expires_in_days=-2),
    )
    assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"


# ── Two deliveries at once: the subscriptions write is FENCED (2026-10-13) ────
#
# `apply_transaction` reads the row, decides, then writes: separate PostgREST calls, each its own
# transaction. A delivery that read the row BEFORE another wrote it used to write its decision
# over the newer state — a renewal's JWS verified while that renewal's REFUND landed put the
# refunded period back. The write is now fenced on the `updated_at` it read, and a miss reads
# again and decides again. `_Racy` runs the other delivery at the boundary a real interleaving
# crosses: right before (or right after) a write executes.

class _Racy(FakeSupabase):
    """Runs `concurrent(sb)` right before — or, with `after=True`, right after — the next write
    to `table` executes: at most `times` times, never inside itself. Arm it after the setup."""

    def __init__(self, *a, table="subscriptions", after=False, **k):
        super().__init__(*a, **k)
        self.concurrent, self.times = None, 1
        self._race_table, self._after, self._inside = table, after, False

    def _fire(self):
        if self.concurrent is None or self.times <= 0 or self._inside:
            return
        self.times -= 1
        self._inside = True
        try:
            self.concurrent(self)
        finally:
            self._inside = False

    def table(self, name):
        q = super().table(name)
        if name != self._race_table:
            return q
        original = q.execute

        def execute():
            if q._op not in ("insert", "update"):
                return original()
            if not self._after:
                self._fire()
            out = original()
            if self._after:
                self._fire()
            return out

        q.execute = execute
        return q


@pytest.fixture
def fence_backoffs(monkeypatch):
    """No sleeping in tests: records each back-off's attempt number instead."""
    calls: list = []
    monkeypatch.setattr(svc, "_fence_backoff", calls.append)
    return calls


def test_a_renewal_verified_while_its_refund_lands_cannot_undo_the_refund(fence_backoffs):
    """THE race. The client verifies the renewal's JWS (signed T4) and the REFUND of that renewal
    (T5) lands between the verify's read and its write. Unfenced, the verify wrote `active` over
    the refund: premium again, and once the first period ended the REFUNDED renewal started a
    paid period of 4,000. Fenced, it misses, reads the refund, and its T4 is stale against T5."""
    now = datetime.now(timezone.utc)
    sb = _Racy()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=5), expires_in_days=1))
    refund_at = now - timedelta(days=1)
    sb.concurrent = lambda _sb: s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(refund_at)},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1",
             revocationDate=_ms(refund_at)),
    )

    out = s.apply_verified_transaction(_USER, _jws(
        txn_id="txn-2", original="txn-1", signed_at=now - timedelta(days=2), expires_in_days=31,
    ))

    assert out["was_stale"] is True and out["winning_tier"] == "free"
    assert fence_backoffs == [1], "one miss, then the re-read"
    assert _row(sb)["status"] == "revoked" and sb._tier() == "free"
    sb.now = _ANCHOR + timedelta(days=1, seconds=1)               # the first period ends
    sb.rpc("ensure_credit_period", {"p_user_id": _USER})          # the next credit read
    assert sb.model.paid_period is None, "the refunded renewal started a paid period"
    assert sb.remaining == 50


def _early_renewed_max(sb, *, renew_signed_at):
    """Max bought through the client (its period ends at +1 day), then renewed EARLY: the
    DID_RENEW (+31 days) lands while the first period still runs, so the first period stays the
    credit period in force (196 waits for it to end)."""
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-1", expires_in_days=1))
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(renew_signed_at)},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1"),
    )
    assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=1)
    return s


@pytest.mark.parametrize("timing", ["after-it", "inside-its-window"])
@pytest.mark.parametrize("renewal", ["on-time", "early"])
def test_an_older_refund_over_a_revoked_row_still_settles_the_period_in_force(
        renewal, timing, fence_backoffs):
    """Both periods refunded, the current one first; the OLDER refund then applies over a row
    that no longer entitles (the older-period guard is for entitling rows). Whichever period is
    the credit period in force must end up clawed back:

    * on time — the CURRENT period is in force and its own refund claws it back. But an older
      refund landing INSIDE the window between that refund's write and its
      `ensure_credit_period` rewrote the row's period end first, so 196 never matched the period
      in force and 3,900 stayed spendable. Settling before overwriting a revoked row closes it.
    * early — the OLDER period is still in force, and only the older refund, WRITTEN over the
      revoked row, lets 196 claw it back. So that refund must keep applying: refusing it like
      the entitling case leaves 3,900."""
    now = datetime.now(timezone.utc)
    sb = _Racy(after=True)
    if renewal == "on-time":
        s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    else:
        s = _early_renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    sb.credits["used"] = 100
    older = _refund_of_the_first_period(now, signed_at=now - timedelta(hours=1))
    if timing == "inside-its-window":
        sb.concurrent = lambda _sb: s.apply_notification(*older)

    s.apply_notification(*_refund_of_the_current_period(now - timedelta(hours=2)))
    if timing == "after-it":
        s.apply_notification(*older)

    assert sb._tier() == "free" and _row(sb)["status"] == "revoked"
    assert sb.remaining == 0, "a refunded period's credits stayed spendable"
    assert sb.model.paid_period is None
    assert "plan_reinstated" not in [r["reason"] for r in sb.model.ledger]


def test_two_first_deliveries_at_once_both_succeed(fence_backoffs):
    """A cold launch hands one unfinished purchase to the `Transaction.updates` listener AND the
    unfinished-transaction drain. Both read no row; the second INSERT collides on
    `subscriptions_user_id_key` (23505). That fell into the missing-column retry, collided
    again and answered 503; now it re-reads and finds the purchase already recorded."""
    now = datetime.now(timezone.utc)
    sb = _Racy()
    s = _service(sb)
    purchase = _jws(signed_at=now - timedelta(minutes=1))
    sb.concurrent = lambda _sb: s.apply_verified_transaction(_USER, purchase)

    out = s.apply_verified_transaction(_USER, purchase)

    assert out["was_stale"] is False and out["winning_tier"] == "premium"
    assert out["was_replay"] is True, "by the time it landed, the purchase was recorded"
    assert fence_backoffs == [1]
    assert len(sb.db["subscriptions"]) == 1 and sb._tier() == "premium"
    assert sb.credits == {"total": 4000, "used": 0}


@pytest.mark.parametrize("ntype, attempts", [
    ("DID_RENEW", svc._FENCE_ATTEMPTS), ("REFUND", svc._FENCE_ATTEMPTS_REVOCATION),
])
def test_a_row_that_keeps_changing_answers_503_after_its_attempts(
        ntype, attempts, fence_backoffs, caplog):
    """Bounded: a row that changes under every attempt ends in a PLAIN `IAPError` (the webhook's
    503, so Apple redelivers; the client's SYSTEM_BUSY, so StoreKit keeps the transaction
    unfinished), with nothing of this delivery written. A revocation gets more attempts."""
    now = datetime.now(timezone.utc)
    sb = _Racy()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    before = {k: v for k, v in _row(sb).items() if k != "updated_at"}
    bumps = iter(range(1, 1000))

    def foreign_write(_sb):
        _row(_sb)["updated_at"] = (now + timedelta(seconds=next(bumps))).isoformat()

    sb.concurrent, sb.times = foreign_write, 1000
    payload = _txn(product=_MAX, txn_id="txn-3", expires_in_days=61, originalTransactionId="txn-1",
                   **({"revocationDate": _ms(now)} if ntype == "REFUND" else {}))

    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"), \
            pytest.raises(svc.IAPError) as raised:
        s.apply_notification({"notificationType": ntype, "signedDate": _ms(now)}, payload)

    assert type(raised.value) is svc.IAPError
    assert fence_backoffs == list(range(1, attempts))
    if ntype == "REFUND":
        assert attempts > svc._FENCE_ATTEMPTS, "a revocation's 503 waits on Apple's retry"
    assert {k: v for k, v in _row(sb).items() if k != "updated_at"} == before
    messages = [r.getMessage() for r in caplog.records]
    assert sum("IAP fence miss" in m for m in messages) == attempts - 1
    assert sum("IAP fence exhausted" in m for m in messages) == 1


def test_a_re_run_that_finds_the_row_rebound_raises_the_terminal_type(fence_backoffs):
    """Whatever the re-read finds is judged afresh, and its typed outcome is never wrapped in a
    generic retry error: a row that now belongs to ANOTHER account answers the terminal 409."""
    now = datetime.now(timezone.utc)
    sb = _Racy()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))

    def rebind(_sb):
        _row(_sb).update(user_id=_OTHER_USER, updated_at=now.isoformat())

    sb.concurrent = rebind
    with pytest.raises(svc.PurchaseBoundToAnotherAccount):
        s.apply_verified_transaction(_USER, _jws(
            txn_id="txn-2", original="txn-1", signed_at=now - timedelta(hours=1),
            expires_in_days=31,
        ))
    assert fence_backoffs == [1]


class _FailingWrite(FakeSupabase):
    """The first subscriptions write fails with an error that is NOT a missing column."""

    def __init__(self, *a, code="57014", **k):
        super().__init__(*a, **k)
        self.code, self.failures = code, 1

    def table(self, name):
        q = super().table(name)
        if name != "subscriptions":
            return q
        original = q.execute

        def execute():
            if q._op in ("insert", "update") and self.failures:
                self.failures -= 1
                raise _PgError(self.code, "canceling statement due to statement timeout")
            return original()

        q.execute = execute
        return q


@pytest.mark.parametrize("code", ["57014", None])
def test_a_transient_write_error_is_a_503_never_a_write_without_the_new_columns(code):
    """The missing-column retry caught EVERY error: a timeout re-wrote the purchase without
    `environment` (a Sandbox purchase stored as NULL, which counts as Production — a paid
    allocation for a test renewal) and without its ordering key. Only a missing-column code
    retries now; anything else raises and the delivery is retried whole."""
    sb = _FailingWrite(code=code)
    with pytest.raises(svc.IAPError):
        _service(sb).apply_transaction(
            _USER, _txn(product=_MAX, txn_id="txn-t", environment="Sandbox"))
    assert sb.db["subscriptions"] == []


def test_a_replay_that_changes_nothing_is_not_rewritten():
    """StoreKit replays the same transaction on every launch, from several places at once.
    Rewriting identical values moved `updated_at` every time — what the fence compares — so
    those bursts would miss each other's fences until one answered 503 for an applied
    purchase. An identical replay writes nothing, and still reconciles."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    purchase = _jws(signed_at=now - timedelta(days=1))
    s.apply_verified_transaction(_USER, purchase)
    before = dict(_row(sb))
    writes = [e for e in sb.log if e[:2] == ("update", "subscriptions")]

    for _ in range(3):
        out = s.apply_verified_transaction(_USER, purchase)
        assert out["was_stale"] is False and out["winning_tier"] == "premium"

    assert _row(sb) == before
    assert [e for e in sb.log if e[:2] == ("update", "subscriptions")] == writes
    assert sb.credits == {"total": 4000, "used": 0}


def test_a_notification_whose_lineage_was_replaced_meanwhile_is_not_written_over_it(
        fence_backoffs, caplog):
    """The old lineage's EXPIRED reads its row; before it writes, the user buys Max on a NEW
    lineage (the client path replaces the one row). The fence misses, and on the re-read no row
    carries the old lineage. The owner fallback used to put that EXPIRED over the new paying
    row — free, and the new lineage's own notifications then found no user. A notification
    names its own lineage, so it never falls back now."""
    now = datetime.now(timezone.utc)
    sb = _Racy()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-a",
                                             signed_at=now - timedelta(days=30), expires_in_days=1))
    sb.concurrent = lambda _sb: s.apply_verified_transaction(
        _USER, _jws(txn_id="txn-b", signed_at=now - timedelta(minutes=5), expires_in_days=30))

    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        outcome, user = s.apply_notification(
            {"notificationType": "EXPIRED", "signedDate": _ms(now)},
            _txn(product=_PRO, txn_id="txn-a", expires_in_days=1),
        )

    if sb.lineage:
        # Migration 198: the new purchase got ITS OWN row, so the old lineage's EXPIRED lands on
        # the old lineage's row and the paying one is untouched.
        assert outcome == "applied:EXPIRED"
        assert (_row_of(sb, "txn-a")["status"], _row_of(sb, "txn-a")["tier"]) == ("expired", "pro")
        assert (_row_of(sb, "txn-b")["status"], _row_of(sb, "txn-b")["tier"]) == ("active", "premium")
        assert sb._tier() == "premium" and len(sb.db["subscriptions"]) == 2
        return
    assert (outcome, user) == ("ignored_superseded_lineage", _USER)
    row = _row(sb)
    assert (row["original_transaction_id"], row["status"], row["tier"]) == ("txn-b", "active",
                                                                            "premium")
    assert sb._tier() == "premium" and len(sb.db["subscriptions"]) == 1
    assert any("no subscriptions row carries this original transaction" in r.getMessage()
               for r in caplog.records)


def test_a_tier_mirror_decided_before_a_refund_is_corrected():
    """`reconcile_user_tier` reads the rows, then writes users.tier — outside the fence. A
    reconcile that read before a REFUND and wrote after the refund's own reconcile left a
    refunded account on paid features, and nothing re-mirrors a revoked row. It re-checks."""
    now = datetime.now(timezone.utc)
    sb = _Racy(table="users")
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    sb.concurrent = lambda _sb: _refund(s, at=now - timedelta(days=1))

    assert s.reconcile_user_tier(_USER) == "free"
    assert sb._tier() == "free"


def _keyless_revoked_row(now):
    """Revoked before migration 114 (or by the old any-error degraded write): no
    `last_event_at`, and `updated_at` = when we wrote the revocation."""
    return FakeSupabase(subscriptions=[{
        "id": "row-1", "user_id": _USER, "tier": "premium", "status": "revoked",
        "original_transaction_id": "txn-1", "updated_at": (now - timedelta(days=1)).isoformat(),
        "current_period_end": (_ANCHOR + timedelta(days=30)).isoformat(),
    }], credits={"total": 50, "used": 0})


def test_a_keyless_row_takes_the_first_key_even_below_its_updated_at():
    """A keyless row stores the first signedDate applied to it, wherever it falls. A floor at the
    client guard's `updated_at` fallback (tried 2026-10-13, reverted) left the row KEYLESS when
    the signedDate fell below it — and the notification guard, which has no such fallback, then
    let through what the stored key refuses: a late REFUND kept no key and a stale DID_RENEW
    re-entitled the refunded row; a DID_RENEW kept no key and an older EXPIRED demoted it."""
    now = datetime.now(timezone.utc)

    sb = _keyless_revoked_row(now)
    s = _service(sb)
    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now - timedelta(days=3))},
        _jws(signed_at=now - timedelta(days=3), revocationDate=_ms(now - timedelta(days=3))),
    )
    assert svc._parse_iso(_row(sb)["last_event_at"]) == svc._ms_to_dt(_ms(now - timedelta(days=3)))
    s.apply_notification({"notificationType": "DID_RENEW",
                          "signedDate": _ms(now - timedelta(days=4))},
                         _jws(signed_at=now - timedelta(days=4)))
    assert _row(sb)["status"] == "revoked" and sb._tier() == "free"

    sb = _keyless_revoked_row(now)
    s = _service(sb)
    s.apply_notification({"notificationType": "DID_RENEW",
                          "signedDate": _ms(now - timedelta(days=2))},
                         _jws(signed_at=now - timedelta(days=2)))
    assert svc._parse_iso(_row(sb)["last_event_at"]) == svc._ms_to_dt(_ms(now - timedelta(days=2)))
    s.apply_notification({"notificationType": "EXPIRED",
                          "signedDate": _ms(now - timedelta(days=3))},
                         _jws(signed_at=now - timedelta(days=3)))
    assert _row(sb)["status"] == "active" and sb._tier() == "premium"


def test_a_failed_settle_answers_503_and_leaves_the_revoked_row():
    """The settle before overwriting a revoked row is REQUIRED: once the row stops naming the
    refunded period, nothing can claw it back, so a failure answers 503 (retried) instead."""
    now = datetime.now(timezone.utc)
    revoked = {
        "id": "row-1", "user_id": _USER, "tier": "premium", "status": "revoked",
        "original_transaction_id": "txn-1", "updated_at": (now - timedelta(days=1)).isoformat(),
        "last_event_at": (now - timedelta(days=1)).isoformat(),
        "current_period_end": (_ANCHOR + timedelta(days=30)).isoformat(),
    }
    sb = FakeSupabase(subscriptions=[dict(revoked)], fail=("ensure_credit_period",))

    with pytest.raises(svc.IAPError):
        _service(sb).apply_verified_transaction(_USER, _jws(
            txn_id="txn-2", original="txn-1", signed_at=now, expires_in_days=31))

    # (198's backfill marks the pre-existing revoked row's high-water mark unknown; nothing else.)
    assert {k: v for k, v in _row(sb).items() if k != "max_period_end"} == revoked


def test_a_reversal_refused_under_a_later_key_is_flagged_for_a_person(caplog):
    """KNOWN GAP, made loud (SYSTEM_DESIGN_GUIDELINES §9b.3). The REFUND of the period in force
    (signed T2) fails delivery and is retried; Apple reverses it (REFUND_REVERSED, T3); an
    unrelated notification signed T4 lands first and sets the key; the REFUND retry then
    revokes (revocation wins) under the kept key T4, and the reversal (T3 < T4) is refused as
    stale. The old code rewound the key to T2 and let the reversal through — the same rewind
    that re-opened the pre-refund replay. Closing this needs the applied revocation's own
    signedDate stored (a migration); until then it is an ERROR with a restore-by-hand marker."""
    now = datetime.now(timezone.utc)
    t2, t3, t4 = (now - timedelta(hours=h) for h in (3, 2, 1))
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    s.apply_notification({"notificationType": "DID_CHANGE_RENEWAL_STATUS",
                          "subtype": "AUTO_RENEW_DISABLED", "signedDate": _ms(t4)},
                         _jws(signed_at=t4))
    _refund(s, at=t2)
    assert sb._tier() == "free"

    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        outcome, user = s.apply_notification(
            {"notificationType": "REFUND_REVERSED", "signedDate": _ms(t3)}, _jws(signed_at=t3))

    if sb.lineage:
        # Migration 198 CLOSES this gap: the reversal is compared with the refund it reverses
        # (T3 > T2, from the transaction register), not with the row's later key T4.
        assert (outcome, user) == ("applied:REFUND_REVERSED", _USER)
        assert _row(sb)["status"] == "active" and sb._tier() == "premium"
        assert [r["reason"] for r in sb.model.ledger].count("plan_reinstated") == 1
        assert not [r for r in caplog.records if "restore by hand" in r.getMessage()]
        return
    assert (outcome, user) == ("ignored_stale_refund_reversal", _USER)
    assert _row(sb)["status"] == "revoked" and sb._tier() == "free"
    flagged = [r.getMessage() for r in caplog.records
               if "IAP REFUND_REVERSED NOT APPLIED — restore by hand" in r.getMessage()]
    assert len(flagged) == 1 and f"user={_USER}" in flagged[0]


def test_a_reversal_that_applies_is_not_flagged(caplog):
    """The marker must not cry wolf: a reversal signed after the stored key applies as usual."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    _refund(s, at=now - timedelta(days=2))

    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        outcome, _ = s.apply_notification(
            {"notificationType": "REFUND_REVERSED", "signedDate": _ms(now - timedelta(days=1))},
            _jws(signed_at=now - timedelta(days=1)))

    assert outcome == "applied:REFUND_REVERSED" and sb._tier() == "premium"
    assert not [r for r in caplog.records if "restore by hand" in r.getMessage()]


def test_a_failed_tier_confirmation_answers_503_rather_than_keep_a_stale_paid_mirror():
    """The re-check exists because a mirror decided before a REFUND can land after it. If the
    confirming read itself fails, keeping the mirror is keeping exactly that stale paid tier
    (nothing re-reconciles a revoked row), so it raises like a failed mirror write: 503, and
    the retried delivery reconciles again."""
    now = datetime.now(timezone.utc)
    sb = _Racy(table="users")
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    outer_reads = {"n": 0}
    real_effective = s.effective_tier

    def effective_tier(user_id):
        if not sb._inside:                        # this reconcile's reads, not the refund's
            outer_reads["n"] += 1
            if outer_reads["n"] == 2:             # its confirmation, after the stale mirror
                raise svc.IAPError("could not read subscriptions")
        return real_effective(user_id)

    s.effective_tier = effective_tier
    sb.concurrent = lambda _sb: _refund(s, at=now - timedelta(days=1))

    with pytest.raises(svc.IAPError):
        s.reconcile_user_tier(_USER)
    assert outer_reads["n"] == 2 and sb._tier() == "premium", "the stale mirror, for now"
    assert s.reconcile_user_tier(_USER) == "free" and sb._tier() == "free", "the retry fixes it"


class _NoNewColumnsRacy(_NoNewColumns):
    """A database without migrations 114/196 AND another writer that lands between the failed
    full write and the retry without the new columns."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.concurrent = None

    def table(self, name):
        q = super().table(name)
        if name != "subscriptions":
            return q
        failing = q.execute

        def execute():
            try:
                return failing()
            except _PgError:
                if self.concurrent is not None:
                    concurrent, self.concurrent = self.concurrent, None
                    concurrent(self)
                raise

        q.execute = execute
        return q


def test_the_retry_without_the_new_columns_is_fenced_too(fence_backoffs):
    """Before migrations 114/196 every write takes the retry without the new columns. That retry
    carries the same fence: a writer landing between the failed write and the retry makes it
    miss, and the delivery reads again rather than overwrite what it never saw."""
    now = datetime.now(timezone.utc)
    sb = _NoNewColumnsRacy()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-old-db"))

    def foreign_write(_sb):
        _row(_sb)["updated_at"] = (now + timedelta(seconds=1)).isoformat()

    sb.concurrent = foreign_write
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-old-db"))

    assert fence_backoffs == [1], "the degraded retry wrote over a row it had not read"
    assert _row(sb)["tier"] == "premium" and "environment" not in _row(sb)

# ── ANY delivery about an older period never moves the newer row back (2026-10-13) ──
#
# Not only refunds: REFUND_REVERSED, REFUND_DECLINED and CONSUMPTION_REQUEST carry the
# transaction they are about, and the app relays old JWS from `Transaction.updates`. Their status
# is DERIVED from that old transaction — "expired" once it ended — and each one wrote the old
# period over the newer row, dropping the subscriber to free. An entitling HIGHER tier is an
# upgrade (any period length) and always applies; so does the Production refund of an upgrade
# bought inside the early-renewal overlap.

def _max_renewed_after_a_past_period(sb, *, renew_signed_at):
    """Max whose first period ENDED two days ago (real clock), renewed by DID_RENEW to +28 d.
    The renewal's credit period is in force: 4,000."""
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-1", expires_in_days=-2))
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(renew_signed_at)},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=28, originalTransactionId="txn-1"),
    )
    assert _row(sb)["status"] == "active" and sb.remaining == 4000
    return s


@pytest.mark.parametrize("source", [
    "CONSUMPTION_REQUEST", "REFUND_DECLINED", "REFUND_REVERSED", "client",
])
def test_a_delivery_about_an_ended_older_period_keeps_the_newer_row(source):
    """The old renewal ended two days ago, so its derived status is "expired"."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _max_renewed_after_a_past_period(sb, renew_signed_at=now - timedelta(days=3))
    sb.credits["used"] = 300
    before = dict(_row(sb))

    if source == "client":
        out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now,
                                                       expires_in_days=-2))
        assert out["was_stale"] is True and out["winning_tier"] == "premium"
    else:
        s.apply_notification(
            {"notificationType": source, "signedDate": _ms(now - timedelta(hours=1))},
            _txn(product=_MAX, txn_id="txn-1", expires_in_days=-2),
        )

    assert _row(sb) == before, "nothing is written — tier, status, period end, ordering key"
    assert sb._tier() == "premium" and sb.remaining == 3700
    # The refusal branch never reaches the clawback RPC.
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)


@pytest.mark.parametrize("source", ["client", "REFUND_DECLINED", "REFUND_REVERSED"])
def test_a_delivery_about_the_running_older_period_keeps_the_early_renewal(source):
    """Inside the early-renewal window the old period still runs, so its derived status is
    "active". Applied, it pulled the row's end back to the old period's, and the subscriber
    lapsed when that ended — with the renewal already paid."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-1", expires_in_days=1))
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(hours=2))},
        _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1"),
    )
    before = dict(_row(sb))

    if source == "client":
        s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now, expires_in_days=1))
    else:
        s.apply_notification(
            {"notificationType": source, "signedDate": _ms(now - timedelta(hours=1))},
            _txn(product=_MAX, txn_id="txn-1", expires_in_days=1),
        )

    assert _row(sb) == before
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=31)


def test_an_older_renewal_redelivered_without_a_stored_key_cannot_rewind_the_row():
    """A row the CLIENT path wrote carries no ordering key, so a late DID_RENEW for the period
    before it passed the staleness guard and rewound the row."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=2),
                                             expires_in_days=1))
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-2", original="txn-1", signed_at=now,
                                             expires_in_days=31))
    assert not _row(sb).get("last_event_at")

    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(days=2))},
        _txn(product=_MAX, txn_id="txn-1", expires_in_days=1),
    )

    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=31)


@pytest.mark.parametrize("gap_days", [10, 3], ids=["outside-the-overlap", "inside-the-overlap"])
@pytest.mark.parametrize("source", ["client", "DID_CHANGE_RENEWAL_PREF", "REFUND_DECLINED"])
def test_the_superseded_pro_without_isUpgraded_cannot_demote_max(source, gap_days):
    """Apple marks the superseded transaction `isUpgraded`; without the flag, the Pro period
    (same lineage, earlier end, LOWER tier) is still an older period and must not write Pro
    over Max — however close its end is to Max's."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-pro", original="txn-g",
                                             signed_at=now - timedelta(days=gap_days),
                                             expires_in_days=30 - gap_days))
    s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-max", original="txn-g",
                                             signed_at=now - timedelta(hours=1),
                                             expires_in_days=30))
    pro = _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now,
               expires_in_days=30 - gap_days)
    if source == "client":
        s.apply_verified_transaction(_USER, pro)
    else:
        s.apply_notification({"notificationType": source, "signedDate": _ms(now)}, pro)

    assert sb._tier() == "premium" and _row(sb)["tier"] == "premium"


def _pro_renewed_early(sb, now):
    """Pro whose period ends tomorrow, renewed EARLY (Apple charges up to 24 h ahead) to +31 d.
    The old period still runs and is the credit period in force."""
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-p1", expires_in_days=1,
                                    originalTransactionId="txn-g"))
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(hours=2))},
        _txn(product=_PRO, txn_id="txn-p2", expires_in_days=31, originalTransactionId="txn-g"),
    )
    assert _row(sb)["tier"] == "pro" and sb._tier() == "pro"
    return s


def _deliver(s, source, payload, signed_at):
    if source == "client":
        return s.apply_verified_transaction(_USER, payload)
    return s.apply_notification({"notificationType": source, "subtype": "UPGRADE",
                                 "signedDate": _ms(signed_at)}, payload)


@pytest.mark.parametrize("gap_days", [1, 4], ids=["next-day", "month-end-skew"])
@pytest.mark.parametrize("source", ["client", "DID_CHANGE_RENEWAL_PREF", "OFFER_REDEEMED"])
def test_an_upgrade_inside_the_early_renewal_overlap_applies_refunds_and_reverses(source, gap_days):
    """An upgrade starts a new period AT ONCE, so bought after an early renewal it ends a little
    BEFORE that renewal (a day, or up to ~4 with month lengths). Refused as an "older period",
    the user paid for Max and kept Pro. Its refund (an EQUAL end) still revokes, and Apple's
    reversal of that refund reinstates Max and its credits exactly once."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _pro_renewed_early(sb, now)
    days = 31 - gap_days

    _deliver(s, source, _jws(product=_MAX, txn_id="txn-m", original="txn-g", signed_at=now,
                             expires_in_days=days), now)
    assert sb._tier() == "premium" and _row(sb)["tier"] == "premium"
    assert sb.model.paid_period["tier"] == "premium"
    granted = sb.remaining

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now + timedelta(minutes=1))},
        _jws(product=_MAX, txn_id="txn-m", original="txn-g", signed_at=now,
             expires_in_days=days, revocationDate=_ms(now)),
    )
    assert sb._tier() == "free" and _row(sb)["status"] == "revoked"
    assert sb.model.paid_period is None and sb.remaining == 50

    for _ in range(2):
        s.apply_notification(
            {"notificationType": "REFUND_REVERSED", "signedDate": _ms(now + timedelta(minutes=2))},
            _jws(product=_MAX, txn_id="txn-m", original="txn-g", signed_at=now,
                 expires_in_days=days),
        )
    assert sb._tier() == "premium" and sb.remaining == granted
    assert [r["reason"] for r in sb.model.ledger].count("plan_reinstated") == 1


@pytest.mark.parametrize("pro_days, max_days", [(85, 30), (25, 7)],
                         ids=["out-of-a-3-month-pro-offer", "a-1-week-max-trial"])
@pytest.mark.parametrize("source", ["client", "DID_CHANGE_RENEWAL_PREF", "OFFER_REDEEMED"])
def test_an_upgrade_of_any_period_length_applies(source, pro_days, max_days):
    """An upgrade can end WEEKS before the stored Pro period: out of a long Pro intro offer, or
    as a short Max trial or offer code. It is still the newest purchase, and must apply."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-p", original="txn-g",
                                             signed_at=now - timedelta(days=5),
                                             expires_in_days=pro_days))

    _deliver(s, source, _jws(product=_MAX, txn_id="txn-m", original="txn-g", signed_at=now,
                             expires_in_days=max_days), now)

    assert sb._tier() == "premium" and _row(sb)["tier"] == "premium"
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=max_days)


def test_a_refund_of_an_upgrade_the_row_never_recorded_still_revokes():
    """The upgrade's own deliveries never reached us; its REFUND did. A revoked higher tier
    ending within the overlap is that upgrade, not an older period: the superseded Pro renewal,
    which Apple refunded pro rata at the upgrade, must not keep the account entitled."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _pro_renewed_early(sb, now)

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now)},
        _jws(product=_MAX, txn_id="txn-m", original="txn-g", signed_at=now,
             expires_in_days=30, revocationDate=_ms(now)),
    )

    assert sb._tier() == "free" and _row(sb)["status"] == "revoked"


def test_in_sandbox_a_revoked_higher_tier_is_not_read_as_an_overlap_upgrade():
    """A Sandbox month lasts minutes, so the 7-day overlap bound means nothing there: the refund
    of an older Max period must not drop a tester's later Pro period."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-p2", expires_in_days=10,
                                    originalTransactionId="txn-g", environment="Sandbox"))

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now)},
        _txn(product=_MAX, txn_id="txn-m1", expires_in_days=5, originalTransactionId="txn-g",
             environment="Sandbox", revocationDate=_ms(now)),
    )

    assert (_row(sb)["tier"], _row(sb)["status"]) == ("pro", "active") and sb._tier() == "pro"


@pytest.mark.parametrize("delivery", ["ended-jws", "refund"])
def test_the_last_max_month_cannot_overwrite_a_downgrade(delivery):
    """Max, then a DOWNGRADE that Apple applies at the renewal: the Pro renewal is the row. The
    last Max month is a whole period older — beyond the overlap bound — so neither its relayed
    (expired) JWS nor its refund may write it back over the paid Pro renewal."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-m1", expires_in_days=-1,
                                    originalTransactionId="txn-g"))
    s.apply_notification(
        {"notificationType": "DID_RENEW", "signedDate": _ms(now - timedelta(hours=2))},
        _txn(product=_PRO, txn_id="txn-p2", expires_in_days=29, originalTransactionId="txn-g"),
    )
    extra = {"revocationDate": _ms(now)} if delivery == "refund" else {}
    s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-m1", original="txn-g",
                                             signed_at=now, expires_in_days=-1, **extra))

    assert (_row(sb)["tier"], _row(sb)["status"]) == ("pro", "active")
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=29)
    assert sb._tier() == "pro"


def test_a_refund_whose_delivery_failed_until_a_pro_resubscribe_keeps_its_credits_accepted_gap():
    """ACCEPTED GAP (§9b.12), pinned so the fix flips it on purpose. Max is refunded, the REFUND
    delivery fails, and the user resubscribes to Pro in the same lineage before Apple's retry
    lands, more than the overlap bound later. The retry is about an older period, so it writes
    nothing — correctly for the tier — and the refunded Max period, still the credit period in
    force (a lower tier never cuts a running period short), keeps its unspent credits until it
    ends. Closing it needs a clawback keyed on (originalTransactionId, period end): a migration."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_MAX, txn_id="txn-m1", expires_in_days=20,
                                    originalTransactionId="txn-g"))
    sb.credits["used"] = 200
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-p1", original="txn-g",
                                             signed_at=now, expires_in_days=30))
    assert sb._tier() == "pro" and sb.model.paid_period["tier"] == "premium"

    s.apply_notification(
        {"notificationType": "REFUND", "signedDate": _ms(now - timedelta(days=1))},
        _txn(product=_MAX, txn_id="txn-m1", expires_in_days=20, originalTransactionId="txn-g",
             revocationDate=_ms(now - timedelta(days=1))),
    )

    assert sb._tier() == "pro" and _row(sb)["status"] == "active"
    if sb.lineage:
        # CLOSED by migration 198: the refunded Max period is clawed back by its own key, and the
        # paid Pro resubscribe's period starts.
        assert sb.model.paid_period["tier"] == "pro" and sb.remaining == 1200
        assert sb.periods[("txn-g", _ANCHOR + timedelta(days=20))]["revoked_at"] is not None
    else:
        assert sb.remaining == 3800 and sb.model.paid_period["tier"] == "premium"


# ── A same-period delivery cannot end Apple's billing grace period (2026-10-14) ──
#
# DID_FAIL_TO_RENEW/GRACE_PERIOD: Apple keeps serving while it retries the charge, and the row
# reads `grace_period`. Every later delivery about that SAME period carries its fixed expiresDate,
# now past, and the ones with no status of their own DERIVED "expired" from it — a Restore
# (`Transaction.currentEntitlements` returns a subscription in grace) and the notifications below
# — dropping the subscriber to free and lapsing the balance migration 196 was holding (design
# review finding #6, 2026-10-13). GRACE_PERIOD_EXPIRED, an explicit EXPIRED, a refund and a newer
# period still apply, and the hold ends with the grace period (`IAP_BILLING_GRACE_DAYS`), so a
# lost GRACE_PERIOD_EXPIRED cannot make it stick.

_GRACE_END = _ANCHOR + timedelta(days=31)        # txn-2's end: the renewal that failed


@pytest.fixture
def service_clock(monkeypatch):
    """`move(sb, at)` sets iap_service's clock AND the credit model's to `at`.

    The service reads `datetime.now` (the derived status, `winning_tier`, the hold's bound). A
    grace period needs a period that the model granted while it ran and whose end has passed by
    the service's clock, so the two clocks have to move together."""
    class _Clock(datetime):
        at = None

        @classmethod
        def now(cls, tz=None):
            if cls.at is None:
                return datetime.now(tz)
            return cls.at if tz is None else cls.at.astimezone(tz)

    monkeypatch.setattr(svc, "datetime", _Clock)

    def move(sb, at):
        _Clock.at = at
        sb.now = at

    return move


def _grace_txn(**extra):
    """txn-2 as Apple re-signs it during the grace period: its expiresDate never changes."""
    return _txn(product=_MAX, txn_id="txn-2", expires_in_days=31, originalTransactionId="txn-1",
                **extra)


def _max_in_grace(sb, move, *, days_in=2):
    """`_renewed_max` (txn-2 ends at +31 d) with 300 of its 4,000 spent; then its renewal FAILS
    and, `days_in` days later, Apple is still retrying inside the billing grace period. 196
    HOLDS the balance."""
    now = datetime.now(timezone.utc)
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    sb.credits["used"] = 300
    move(sb, _GRACE_END + timedelta(days=days_in))
    s.apply_notification(
        {"notificationType": "DID_FAIL_TO_RENEW", "subtype": "GRACE_PERIOD",
         "signedDate": _ms(_GRACE_END + timedelta(minutes=5))},
        _grace_txn(),
    )
    assert (_row(sb)["status"], sb._tier(), sb.remaining) == ("grace_period", "premium", 3700)
    return s


def _deliver_in_grace(s, source, subtype, signed_at, payload=None):
    """`source` "client" is a Restore posting the freshly signed JWS to /billing/verify."""
    if payload is None:
        payload = _grace_txn(signedDate=_ms(signed_at))
    if source == "client":
        return s.apply_verified_transaction(_USER, payload)
    notification = {"notificationType": source, "signedDate": _ms(signed_at)}
    if subtype:
        notification["subtype"] = subtype
    return s.apply_notification(notification, payload)


@pytest.mark.parametrize("source, subtype", [
    ("client", None),
    ("PRICE_INCREASE", "ACCEPTED"),
    ("DID_CHANGE_RENEWAL_PREF", "DOWNGRADE"),
    # Turning auto-renew back ON changes nothing about the period in grace. Turning it OFF ends
    # access at once (2026-10-15): `test_apples_own_word_ends_the_grace_period`.
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_ENABLED"),
    ("CONSUMPTION_REQUEST", None),
    ("REFUND_DECLINED", None),
])
def test_a_same_period_delivery_with_a_derived_status_keeps_the_grace_period(
        service_clock, source, subtype):
    """THE bug. The review's probe ended each of these at tier free, status expired and the free
    allowance (50): the derived "expired" was written over the grace row."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    before = dict(_row(sb))

    out = _deliver_in_grace(s, source, subtype, _GRACE_END + timedelta(days=2))

    assert _row(sb) == before, "nothing is written — tier, status, period end, ordering key"
    assert sb._tier() == "premium" and sb.remaining == 3700
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)
    if source == "client":
        assert out["was_stale"] is True and out["winning_tier"] == "premium"
        assert out["status"] == "grace_period", "the client is told the stored state"
    else:
        assert out == (f"unapplied:billing_grace:{source}", _USER)        # a 200: Apple must not redeliver it


def test_grace_period_expired_ends_the_grace_period(service_clock):
    """Must-still-expire twin: Apple's own word that the grace period is over. It used to derive
    its "expired" from the past end, which the hold now refuses, so it is explicit."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock, days_in=16)

    out = s.apply_notification(
        {"notificationType": "GRACE_PERIOD_EXPIRED",
         "signedDate": _ms(_GRACE_END + timedelta(days=16))},
        _grace_txn(),
    )

    assert out == ("applied:GRACE_PERIOD_EXPIRED", _USER)
    assert _row(sb)["status"] == "expired" and sb._tier() == "free"
    assert sb.credits == {"total": 50, "used": 0}, "the held leftovers lapse at plan end"
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs), "an expiry is not a refund"


@pytest.mark.parametrize("subtype", ["BILLING_RETRY", "VOLUNTARY", None])
def test_an_explicit_expired_still_ends_the_grace_period(service_clock, subtype):
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)

    _deliver_in_grace(s, "EXPIRED", subtype, _GRACE_END + timedelta(days=2))

    assert _row(sb)["status"] == "expired" and sb._tier() == "free"
    assert sb.credits == {"total": 50, "used": 0}


@pytest.mark.parametrize("source, subtype", [
    # A type the hold holds inside the window (AUTO_RENEW_DISABLED is never held, so it would
    # prove nothing about the bound).
    ("client", None), ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_ENABLED"),
])
def test_past_the_billing_grace_period_a_derived_expiry_applies(service_clock, source, subtype):
    """Must-still-expire twin: the bound keeps a LOST GRACE_PERIOD_EXPIRED from making the hold
    stick. Once the grace period is over, the same delivery ends the row as it always did."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    past = _GRACE_END + timedelta(days=settings.IAP_BILLING_GRACE_DAYS, seconds=1)
    service_clock(sb, past)

    _deliver_in_grace(s, source, subtype, past)

    assert _row(sb)["status"] == "expired" and sb._tier() == "free"
    assert sb.credits == {"total": 50, "used": 0}


@pytest.mark.parametrize("grace_days", [3, 16, 28])
def test_the_hold_lasts_exactly_the_configured_grace_period(service_clock, monkeypatch,
                                                            grace_days):
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", grace_days)
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock, days_in=1)
    grace_ends = _GRACE_END + timedelta(days=grace_days)

    service_clock(sb, grace_ends - timedelta(minutes=1))
    _deliver_in_grace(s, "client", None, grace_ends - timedelta(minutes=1))
    assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"

    service_clock(sb, grace_ends)
    _deliver_in_grace(s, "client", None, grace_ends)
    assert _row(sb)["status"] == "expired" and sb._tier() == "free"


def test_a_refund_during_the_grace_period_still_revokes_and_claws_back(service_clock):
    """Must-still-apply twin: a revocation is never held. The refunded transaction IS the credit
    period in force, so 196 claws it back too."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    at = _GRACE_END + timedelta(days=2)

    s.apply_notification({"notificationType": "REFUND", "signedDate": _ms(at)},
                         _grace_txn(revocationDate=_ms(at)))

    assert _row(sb)["status"] == "revoked" and sb._tier() == "free"
    assert sb.model.paid_period is None and sb.remaining == 0
    assert "tier_revoked" in [r["reason"] for r in sb.model.ledger]


@pytest.mark.parametrize("source", ["DID_RENEW", "client"])
def test_a_billing_recovery_still_ends_the_grace_period_with_a_new_period(service_clock, source):
    """A NEWER period applies: Apple's recovery keeps the renewal date, so the new period starts
    at the failed one's end and the account gets its fresh allocation."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    at = _GRACE_END + timedelta(days=2)
    recovered = _txn(product=_MAX, txn_id="txn-3", expires_in_days=61,
                     originalTransactionId="txn-1", signedDate=_ms(at))

    _deliver_in_grace(s, source, "BILLING_RECOVERY" if source != "client" else None, at,
                      payload=recovered)

    assert _row(sb)["status"] == "active" and sb._tier() == "premium"
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=61)
    assert sb.credits == {"total": 4000, "used": 0}


def test_a_held_delivery_records_no_ordering_key_so_an_earlier_signed_recovery_applies(
        service_clock):
    """Why the hold writes no `last_event_at`. Apple redelivers for hours in no order: had the
    held PRICE_INCREASE's signedDate become the key, this BILLING_RECOVERY — signed before it,
    delivered after it — would be refused as stale and the paying subscriber would stay in grace,
    then lapse."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    _deliver_in_grace(s, "PRICE_INCREASE", "ACCEPTED", _GRACE_END + timedelta(days=2))

    s.apply_notification(
        {"notificationType": "DID_RENEW", "subtype": "BILLING_RECOVERY",
         "signedDate": _ms(_GRACE_END + timedelta(days=1))},
        _txn(product=_MAX, txn_id="txn-3", expires_in_days=61, originalTransactionId="txn-1"),
    )

    assert _row(sb)["status"] == "active" and sb._tier() == "premium"
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=61)


def test_without_a_grace_period_a_failed_renewal_still_ends_the_period(service_clock):
    """Must-still-expire twin, and the common case: with Billing Grace Period off, DID_FAIL_TO_RENEW
    has no subtype and the row is still `active`, so its derived expiry applies as before."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    service_clock(sb, _GRACE_END + timedelta(minutes=10))

    s.apply_notification(
        {"notificationType": "DID_FAIL_TO_RENEW",
         "signedDate": _ms(_GRACE_END + timedelta(minutes=5))},
        _grace_txn(),
    )

    assert _row(sb)["status"] == "expired" and sb._tier() == "free"


def test_the_held_delivery_is_logged_with_both_dates_and_the_grace_end(service_clock, caplog):
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)

    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _deliver_in_grace(s, "client", None, _GRACE_END + timedelta(days=2))

    msgs = [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "billing grace" in r.getMessage()]
    assert len(msgs) == 1
    assert "txn=txn-2" in msgs[0] and f"user={_USER}" in msgs[0] and "source=client" in msgs[0]
    assert msgs[0].count(_GRACE_END.isoformat()) == 2       # the delivery's end and the row's
    assert (_GRACE_END + timedelta(days=settings.IAP_BILLING_GRACE_DAYS)).isoformat() in msgs[0]


def test_the_billing_grace_hold_edges():
    """Pure-function edges. Only a DERIVED "expired" about the SAME period of the SAME lineage,
    over a row in billing grace or retry, inside the grace period, is held."""
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "grace_period", "tier": "premium",
           "current_period_end": end.isoformat()}
    grace = timedelta(days=settings.IAP_BILLING_GRACE_DAYS)

    def reason(prior=row, *, status="expired", override=None, otid="otid", period_end=end,
               now=end + timedelta(days=2)):
        return svc._billing_grace_hold_reason(
            prior, status=status, status_override=override, original_transaction_id=otid,
            period_end=period_end, now=now,
        )

    for status in ("grace_period", "billing_retry", "Grace_Period", "BILLING_RETRY"):
        assert reason(prior={**row, "status": status}) is not None, status
    # Only a DERIVED "expired": an explicit expiry, a revocation or an entitling status applies.
    for status, override in (("expired", "expired"), ("revoked", None), ("revoked", "revoked"),
                             ("active", None), ("grace_period", "grace_period")):
        assert reason(status=status, override=override) is None, (status, override)
    # An explicit "active" about the ENDED period (an OFFER_REDEEMED during grace) says only that
    # it ended: held like a derived expiry. About a period still running it is held too while the
    # row is already in grace for it (Apple noticed the failed charge early: IAP audit F07), and
    # over an `active` row it applies.
    assert reason(status="active", override="active") is not None
    assert reason(status="active", override="active", now=end - timedelta(hours=1)) is not None
    assert reason(prior={**row, "status": "active"}, status="active", override="active",
                  now=end - timedelta(hours=1)) is None
    # The stored row must be in billing grace or retry (an `active` one is held only for 24 h
    # past its end, and `now` here is two days past it: `test_the_active_row_hold_edges`).
    for status in ("active", "expired", "revoked", "", None, "paused"):
        assert reason(prior={**row, "status": status}) is None, status
    assert reason(prior=None) is None and reason(prior={}) is None
    # Same lineage, same period (inside the drift margin, on either side).
    assert reason(otid="another") is None, "the owner fallback's row is ANOTHER lineage"
    assert reason(prior={**row, "original_transaction_id": None}, otid="") is None
    for drift in (timedelta(0), timedelta(seconds=59), -timedelta(seconds=59)):
        assert reason(period_end=end + drift) is not None, drift
    for drift in (timedelta(seconds=60), -timedelta(seconds=60), timedelta(days=30),
                  -timedelta(days=30)):
        assert reason(period_end=end + drift) is None, drift
    assert reason(period_end=None) is None
    for junk in (None, "", "not-a-date", 12345, "2026-13-45T99:99:99"):
        assert reason(prior={**row, "current_period_end": junk}) is None, junk
    assert reason(prior={**row, "current_period_end": end.replace(tzinfo=None).isoformat()})
    # Inside the grace period only: the stored end + IAP_BILLING_GRACE_DAYS.
    assert reason(now=end + grace - timedelta(seconds=1)) is not None
    assert reason(now=end + grace) is None
    assert reason(now=end + grace + timedelta(days=30)) is None
    # Both dates and the end of the grace period are in the reason (the WARNING carries it).
    text = reason(period_end=end + timedelta(seconds=30))
    assert (end + timedelta(seconds=30)).isoformat() in text and end.isoformat() in text
    assert (end + grace).isoformat() in text
    # GRACE_PERIOD_EXPIRED is explicit; DID_FAIL_TO_RENEW without a subtype stays derived.
    for ntype in ("GRACE_PERIOD_EXPIRED", "grace_period_expired"):
        assert svc._status_for_notification(ntype, "") == "expired"
    assert svc._status_for_notification("DID_FAIL_TO_RENEW", "") is None


def test_a_late_grace_period_expired_cannot_demote_a_recovered_subscriber(service_clock):
    """The verifier's sequence (2026-10-14). GRACE_PERIOD_EXPIRED is signed when the grace period
    ends and its delivery fails (a 5xx); billing recovers a day later, during Apple's retry; the
    app relays the new renewal (`Transaction.updates` → /billing/verify), which stores no
    ordering key; then Apple's retry of GRACE_PERIOD_EXPIRED lands, before the DID_RENEW
    webhook. Signed after the grace notification (the only stored key), it is not stale. Exempted
    from the older-period guard like EXPIRED, its explicit "expired" wrote the OLD period over
    the paid one: free until DID_RENEW arrived. It is about the period in grace, so it is refused."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    grace_ends = _GRACE_END + timedelta(days=settings.IAP_BILLING_GRACE_DAYS)
    recovered_at = grace_ends + timedelta(days=1)
    service_clock(sb, recovered_at)
    s.apply_verified_transaction(_USER, _txn(
        product=_MAX, txn_id="txn-3", originalTransactionId="txn-1",
        expires_in_days=31 + settings.IAP_BILLING_GRACE_DAYS + 1 + 30,
        signedDate=_ms(recovered_at),
    ))
    assert _row(sb)["status"] == "active" and sb.credits == {"total": 4000, "used": 0}
    before = dict(_row(sb))
    service_clock(sb, recovered_at + timedelta(hours=1))

    out = s.apply_notification(
        {"notificationType": "GRACE_PERIOD_EXPIRED", "signedDate": _ms(grace_ends)},
        _grace_txn(),
    )

    assert out == ("unapplied:older_period:GRACE_PERIOD_EXPIRED", _USER)      # a 200: nothing to retry
    assert _row(sb) == before and sb._tier() == "premium"
    assert sb.credits == {"total": 4000, "used": 0}


def test_a_grace_period_expired_that_loses_the_fence_to_a_recovery_is_refused_on_its_rerun(
        service_clock, fence_backoffs):
    """The same refusal on the fence's re-run: GRACE_PERIOD_EXPIRED read the grace row (the
    period it is about, so it applies), and the app's relay of the recovery wrote first. Read
    again, the row holds a NEWER paid period, and the re-run must still know it is a
    GRACE_PERIOD_EXPIRED — not an EXPIRED, which would be honoured whatever period it carries."""
    sb = _Racy()
    s = _max_in_grace(sb, service_clock)
    grace_ends = _GRACE_END + timedelta(days=settings.IAP_BILLING_GRACE_DAYS)
    service_clock(sb, grace_ends + timedelta(minutes=5))
    recovered_end = _ANCHOR + timedelta(days=31 + settings.IAP_BILLING_GRACE_DAYS + 30)

    def recovery_relayed_by_the_app(racy):
        racy.now = svc.datetime.at = grace_ends + timedelta(minutes=6)
        _service(racy).apply_verified_transaction(_USER, _txn(
            product=_MAX, txn_id="txn-3", originalTransactionId="txn-1",
            expires_in_days=31 + settings.IAP_BILLING_GRACE_DAYS + 30,
            signedDate=_ms(grace_ends + timedelta(minutes=6)),
        ))

    sb.concurrent = recovery_relayed_by_the_app
    s.apply_notification(
        {"notificationType": "GRACE_PERIOD_EXPIRED", "signedDate": _ms(grace_ends)},
        _grace_txn(),
    )

    assert fence_backoffs == [1], "the first write missed its fence and re-ran"
    assert _row(sb)["status"] == "active" and sb._tier() == "premium"
    assert svc._parse_iso(_row(sb)["current_period_end"]) == recovered_end


def test_the_older_period_guard_reads_grace_period_expired_as_about_one_period():
    """Pure edges. EXPIRED states the SUBSCRIPTION's condition and is honoured whatever period
    it carries, as before; GRACE_PERIOD_EXPIRED's equal "expired" is about the period in grace,
    so an older one is refused — and the one about the period in force still applies. Since
    2026-10-15 the same holds for DID_FAIL_TO_RENEW/GRACE_PERIOD's "grace_period"."""
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "premium",
           "current_period_end": end.isoformat()}

    def reason(ntype, *, override="expired", period_end=end - timedelta(days=30)):
        return svc._older_period_reason(
            row, status=override, status_override=override, tier="premium",
            environment="Production", original_transaction_id="otid", period_end=period_end,
            notification_type=ntype,
        )

    for ntype in ("GRACE_PERIOD_EXPIRED", "grace_period_expired"):
        assert reason(ntype) is not None, ntype
        assert reason(ntype, period_end=end) is None, "the period in force: it applies"
    assert reason("EXPIRED") is None
    assert reason(None) is None, "an override from a caller that names no type, as before"
    assert reason(None, override="grace_period") is None
    for ntype in ("DID_FAIL_TO_RENEW", "did_fail_to_renew"):
        assert reason(ntype, override="grace_period") is not None, ntype
        assert reason(ntype, override="grace_period", period_end=end) is None, "in force"
    assert svc._PERIOD_SCOPED_NOTIFICATIONS == frozenset(
        {"GRACE_PERIOD_EXPIRED", "DID_FAIL_TO_RENEW"})


def test_the_billing_grace_setting_defaults_to_apples_maximum_and_is_bounded():
    """It must EQUAL App Store Connect's Billing Grace Period (3, 16 or 28 days). Unknown, the
    default is the longest: set too short, the sweep would expire subscribers Apple still
    serves. Out of range, the deploy fails rather than moving either window."""
    from typing import Annotated

    from pydantic import TypeAdapter, ValidationError

    from app.config import Settings

    field = Settings.model_fields["IAP_BILLING_GRACE_DAYS"]
    assert field.default == 28
    bounded = TypeAdapter(Annotated[(int, *field.metadata)])
    for ok in (3, 16, 28):
        assert bounded.validate_python(ok) == ok
    for bad in (0, 2, 29, 60, -1):
        with pytest.raises(ValidationError):
            bounded.validate_python(bad)


# ── The grace notification is late, Apple's own word ends grace (2026-10-15) ───
#
# Gap A: Apple's DID_FAIL_TO_RENEW/GRACE_PERIOD delivery fails, so the row still reads `active`
# past its end until the retry (+1, +12, +24 h…). A derived expiry in between — a Restore, or a
# webhook with no status of its own — was written: Free, the held balance lapsed for good, and a
# webhook's later signedDate made Apple's retry stale (Free for the whole grace). Held now for the
# sweep's 24 h `active` window. Gap B: a cancellation during grace ends access at once (Apple), so
# AUTO_RENEW_DISABLED is never held; nor is DID_FAIL_TO_RENEW without a subtype, nor anything
# Apple's `data.status` marks expired / billing retry / revoked. And a late grace notification
# about an older period no longer rewinds a recovered row.

def _notify(s, ntype, subtype, signed_at, payload, apple_status=None):
    """A webhook; `apple_status` goes into `data.status` (the real attrs `Data` when it is a
    `Status`, a dict otherwise — both shapes the verifier and the tests produce)."""
    notification = {"notificationType": ntype, "signedDate": _ms(signed_at)}
    if subtype:
        notification["subtype"] = subtype
    if apple_status is not None:
        from appstoreserverlibrary.models.Data import Data
        from appstoreserverlibrary.models.Status import Status
        notification["data"] = (Data(status=apple_status) if isinstance(apple_status, Status)
                                else {"status": apple_status})
    return s.apply_notification(notification, payload)


def _max_past_its_end(sb, move, *, minutes=20):
    """`_renewed_max` with 300 spent; its period (txn-2) ENDED `minutes` ago and Apple's verdict
    on the renewal has not arrived (the grace notification's first delivery failed)."""
    now = datetime.now(timezone.utc)
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    sb.credits["used"] = 300
    move(sb, _GRACE_END + timedelta(minutes=minutes))
    return s


def _grace_notice_retried(s, *, signed=_GRACE_END + timedelta(minutes=5)):
    """Apple's retry of the DID_FAIL_TO_RENEW/GRACE_PERIOD it signed at the period's end."""
    return s.apply_notification(
        {"notificationType": "DID_FAIL_TO_RENEW", "subtype": "GRACE_PERIOD",
         "signedDate": _ms(signed)},
        _grace_txn(),
    )


@pytest.mark.parametrize("source, subtype", [
    ("client", None),
    ("PRICE_INCREASE", "ACCEPTED"),
    ("DID_CHANGE_RENEWAL_PREF", "DOWNGRADE"),
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_ENABLED"),
    ("CONSUMPTION_REQUEST", None),
    ("REFUND_DECLINED", None),
])
def test_a_derived_expiry_before_the_grace_notification_arrives_is_held(
        service_clock, source, subtype):
    """Gap A (both variants the 2026-10-14 probes found). Before: expired / free / 50, and for a
    webhook Apple's retry was then refused as stale, so the subscriber stayed Free for the whole
    grace. Now nothing is written: the tier is Free meanwhile exactly as for any `active` row
    past its end (`winning_tier`), the balance stays HELD, and the retry restores both."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock)
    before = dict(_row(sb))
    signed = _GRACE_END + timedelta(minutes=20)

    if source == "client":
        out = s.apply_verified_transaction(_USER, _grace_txn(signedDate=_ms(signed)))
        assert out["was_stale"] is True and out["refused_by"] == "billing_grace"
    else:
        assert _notify(s, source, subtype, signed, _grace_txn()) == (f"unapplied:billing_grace:{source}", _USER)

    assert _row(sb) == before, "nothing is written — status, period end, ordering key"
    assert sb.remaining == 3700, "196/198 HOLD the balance of an active row past its end"
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs)

    service_clock(sb, _GRACE_END + timedelta(hours=1))
    _grace_notice_retried(s)

    assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"
    assert sb.remaining == 3700


@pytest.mark.parametrize("held_until_minute", [24 * 60 - 1, 24 * 60])
def test_the_active_row_hold_ends_with_the_sweeps_window(service_clock, held_until_minute):
    """The bound equals the sweep's `active` window, so the hold changes nothing the sweep would
    not: past it, a derived expiry applies as before (expired, the balance lapses)."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=held_until_minute)
    signed = _GRACE_END + timedelta(minutes=held_until_minute)

    s.apply_verified_transaction(_USER, _grace_txn(signedDate=_ms(signed)))

    if held_until_minute < 24 * 60:
        assert _row(sb)["status"] == "active" and sb.remaining == 3700
    else:
        assert _row(sb)["status"] == "expired" and sb.credits == {"total": 50, "used": 0}


@pytest.mark.parametrize("grace_row", [True, False], ids=["in-grace", "grace-notice-late"])
@pytest.mark.parametrize("ntype, subtype, apple_status", [
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", None),
    ("DID_FAIL_TO_RENEW", None, None),
    ("PRICE_INCREASE", "ACCEPTED", 2),
    ("PRICE_INCREASE", "ACCEPTED", 3),
    ("DID_CHANGE_RENEWAL_PREF", "DOWNGRADE", 5),
    ("CONSUMPTION_REQUEST", None, "EXPIRED"),        # the real attrs Data object
    ("REFUND_DECLINED", None, "BILLING_RETRY"),
], ids=["cancel", "fail-no-grace", "status-2", "status-3", "status-5", "attrs-2", "attrs-3"])
def test_apples_own_word_ends_the_grace_period(service_clock, grace_row, ntype, subtype,
                                               apple_status):
    """Must-still-expire twins of both holds. A cancellation during grace ends access AT ONCE
    (Apple's failed-renewal testing guide); DID_FAIL_TO_RENEW without a subtype says there is no
    grace period; `data.status` 2 / 3 / 5 is Apple's word that the subscription is expired, in
    billing retry outside grace, or revoked. Each applies: expired, Free, the held balance
    lapses (no refund clawback), and its signedDate is stored, so an earlier-signed retry of the
    grace notification cannot bring the tier back."""
    if isinstance(apple_status, str):
        from appstoreserverlibrary.models.Status import Status
        apple_status = Status[apple_status]
    sb = FakeSupabase()
    if grace_row:
        s = _max_in_grace(sb, service_clock)
        signed = _GRACE_END + timedelta(days=2)
    else:
        s = _max_past_its_end(sb, service_clock)
        signed = _GRACE_END + timedelta(minutes=20)

    out = _notify(s, ntype, subtype, signed, _grace_txn(), apple_status=apple_status)

    assert out == (f"applied:{ntype}", _USER)
    assert _row(sb)["status"] == "expired" and sb._tier() == "free"
    assert sb.credits == {"total": 50, "used": 0}
    assert not any(n == "revoke_tier_credits" for n, _ in sb.rpcs), "an expiry is not a refund"
    _grace_notice_retried(s)
    assert _row(sb)["status"] == "expired" and sb._tier() == "free"


@pytest.mark.parametrize("apple_status", [1, 4, "ACTIVE", "BILLING_GRACE_PERIOD", 6, "4", True])
def test_a_status_that_is_not_an_end_changes_nothing(service_clock, apple_status):
    """1 (active) and 4 (grace) say nothing has ended; an unknown or malformed value is no
    status at all. Each delivery is still held."""
    if isinstance(apple_status, str) and not apple_status.isdigit():
        from appstoreserverlibrary.models.Status import Status
        apple_status = Status[apple_status]
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    before = dict(_row(sb))

    _notify(s, "PRICE_INCREASE", "ACCEPTED", _GRACE_END + timedelta(days=2), _grace_txn(),
            apple_status=apple_status)

    assert _row(sb) == before and sb._tier() == "premium" and sb.remaining == 3700


def test_a_late_grace_notice_cannot_rewind_a_recovered_subscriber(service_clock):
    """Apple can send DID_FAIL_TO_RENEW AFTER the BILLING_RECOVERY that superseded it, signed
    later (Apple staff, forums 745515). Exempt from the older-period guard, its "grace_period"
    rewound the row to the failed period: the recovered period's allocation never started, and
    the sweep would have expired the paying subscriber at the old end + grace + 24 h. The -7 h
    and +16 s offsets are that thread's real timings (a recovery 7 h before the period's end, the
    failure notice 16 s after it)."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=60)
    s.apply_notification(
        {"notificationType": "DID_RENEW", "subtype": "BILLING_RECOVERY",
         "signedDate": _ms(_GRACE_END - timedelta(hours=7))},
        _txn(product=_MAX, txn_id="txn-3", expires_in_days=61, originalTransactionId="txn-1"),
    )
    assert _row(sb)["status"] == "active" and sb.credits == {"total": 4000, "used": 0}
    before = dict(_row(sb))

    out = _grace_notice_retried(s, signed=_GRACE_END + timedelta(seconds=16))

    assert out == ("unapplied:older_period:DID_FAIL_TO_RENEW", _USER)               # a 200: nothing to retry
    assert _row(sb) == before and sb._tier() == "premium"
    assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=61)
    assert sb.credits == {"total": 4000, "used": 0}


def test_a_grace_notice_about_the_period_in_force_still_applies_to_an_active_row_past_its_end(
        service_clock):
    """Twin: the grace notification about the stored period itself is not an older period."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock)

    _grace_notice_retried(s)

    assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"
    assert sb.remaining == 3700


def test_a_grace_notice_after_the_row_expired_restores_the_tier_and_says_the_balance_lapsed(
        service_clock, caplog):
    """The residual of gap A, made visible: the row expired more than 24 h after its end (a
    derived expiry outside the hold, or the sweep) before Apple's retry landed. The tier comes
    back as before; the balance does not, and a WARNING says so."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=25 * 60)
    s.apply_verified_transaction(_USER, _grace_txn(
        signedDate=_ms(_GRACE_END + timedelta(hours=25))))
    assert _row(sb)["status"] == "expired" and sb.credits == {"total": 50, "used": 0}

    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _grace_notice_retried(s)

    assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"
    assert sb.credits == {"total": 50, "used": 0}, "accepted: nothing reinstates the lapse"
    msgs = [r.getMessage() for r in caplog.records
            if "billing grace arrived AFTER the row was expired" in r.getMessage()]
    assert len(msgs) == 1 and f"user={_USER}" in msgs[0] and _GRACE_END.isoformat() in msgs[0]


@pytest.mark.parametrize("ntype, subtype, apple_status", [
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", None),
    ("PRICE_INCREASE", "ACCEPTED", 3),
], ids=["subtype", "data-status"])
def test_the_fence_rerun_still_knows_apples_word(service_clock, fence_backoffs, ntype, subtype,
                                                 apple_status):
    """The write is fenced: another writer moves the row first, and the delivery is decided
    again from a fresh read. That re-run must still carry the subtype and `data.status`, or it
    reads as an ordinary derived expiry and is HELD — a cancelled user kept in grace."""
    sb = _Racy()
    s = _max_in_grace(sb, service_clock)

    def another_writer(racy):
        racy.db["subscriptions"][0]["updated_at"] = (
            _GRACE_END + timedelta(days=2, seconds=1)).isoformat()

    sb.concurrent = another_writer
    _notify(s, ntype, subtype, _GRACE_END + timedelta(days=2), _grace_txn(),
            apple_status=apple_status)

    assert fence_backoffs == [1], "the first write missed its fence and re-ran"
    assert _row(sb)["status"] == "expired" and sb._tier() == "free"


def test_the_active_row_hold_and_its_exemptions_edges():
    """Pure-function edges of the `active` branch and of `_ends_service_by_itself`."""
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "premium",
           "current_period_end": end.isoformat()}
    window = timedelta(hours=24)

    def reason(prior=row, *, ntype=None, subtype=None, apple_status=None, override=None,
               status="expired", otid="otid", period_end=end, now=end + timedelta(hours=1)):
        return svc._billing_grace_hold_reason(
            prior, status=status, status_override=override, original_transaction_id=otid,
            period_end=period_end, notification_type=ntype, notification_subtype=subtype,
            apple_status=apple_status, now=now,
        )

    assert reason() is not None
    for status in ("ACTIVE", "Active"):
        assert reason(prior={**row, "status": status}) is not None, status
    assert reason(now=end + window - timedelta(seconds=1)) is not None
    assert reason(now=end + window) is None and reason(now=end + window * 30) is None
    assert reason(otid="another") is None
    for drift in (timedelta(seconds=60), -timedelta(seconds=60), timedelta(days=30)):
        assert reason(period_end=end + drift) is None, drift
    assert reason(status="active") is None and reason(override="expired") is None
    assert window.total_seconds() == svc._sweep_windows()["active"].total_seconds()
    # Apple's own word, in every branch (grace rows included).
    grace_row = {**row, "status": "grace_period"}
    for kw in ({"ntype": "DID_FAIL_TO_RENEW"}, {"ntype": "did_fail_to_renew"},
               {"ntype": "DID_CHANGE_RENEWAL_STATUS", "subtype": "AUTO_RENEW_DISABLED"},
               {"ntype": "did_change_renewal_status", "subtype": "auto_renew_disabled"},
               {"apple_status": 2}, {"apple_status": 3}, {"apple_status": 5}):
        assert reason(**kw) is None, kw
        assert reason(prior=grace_row, **kw) is None, kw
        assert svc._ends_service_by_itself(
            kw.get("ntype"), kw.get("subtype"), kw.get("apple_status")) is not None, kw
    for kw in ({"ntype": "DID_CHANGE_RENEWAL_STATUS", "subtype": "AUTO_RENEW_ENABLED"},
               {"ntype": "DID_CHANGE_RENEWAL_STATUS"}, {"ntype": "PRICE_INCREASE"},
               {"apple_status": 1}, {"apple_status": 4}, {"apple_status": None},
               {"ntype": "DID_CHANGE_RENEWAL_PREF", "subtype": "AUTO_RENEW_DISABLED"}):
        assert reason(**kw) is not None, kw
        assert reason(prior=grace_row, **kw) is not None, kw
        assert svc._ends_service_by_itself(
            kw.get("ntype"), kw.get("subtype"), kw.get("apple_status")) is None, kw
    text = reason(period_end=end + timedelta(seconds=30))
    assert (end + timedelta(seconds=30)).isoformat() in text and end.isoformat() in text
    assert (end + window).isoformat() in text
    # Each branch names its own bound (the active one is not the grace setting).
    assert "IAP_BILLING_GRACE_DAYS" not in text and "active window" in text
    assert "IAP_BILLING_GRACE_DAYS" in reason(prior=grace_row)
    # A cancellation Apple signed while the subscription was ACTIVE only stops the next renewal.
    cancel = {"ntype": "DID_CHANGE_RENEWAL_STATUS", "subtype": "AUTO_RENEW_DISABLED"}
    assert reason(apple_status=1, **cancel) is not None
    assert reason(prior=grace_row, apple_status=1, **cancel) is not None
    assert svc._ends_service_by_itself(cancel["ntype"], cancel["subtype"], 1) is None
    for apple_status in (None, 2, 3, 4, 5):
        assert svc._ends_service_by_itself(
            cancel["ntype"], cancel["subtype"], apple_status) is not None, apple_status


# ── Review fixes (2026-10-15 adversarial review of the change above) ───────────

def test_a_recovery_delivered_after_its_own_late_grace_notice_still_applies(service_clock):
    """Forum 745515 the other way round: the grace notice (signed AFTER the recovery) is delivered
    first and stores its signedDate; the BILLING_RECOVERY, signed earlier, then read as stale. The
    row stayed in the failed period, the recovered allocation never started, and the sweep would
    have expired a paying subscriber a day before the next DID_RENEW. Since 198 a newer period of
    the lineage over a row in grace is the renewal, whatever its signedDate. Before 198 the old
    ordering stands (an accepted gap): a refunded renewal's saved JWS must not pass."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=60)
    _grace_notice_retried(s, signed=_GRACE_END + timedelta(seconds=16))
    assert _row(sb)["status"] == "grace_period"

    out = s.apply_notification(
        {"notificationType": "DID_RENEW", "subtype": "BILLING_RECOVERY",
         "signedDate": _ms(_GRACE_END - timedelta(hours=7))},
        _txn(product=_MAX, txn_id="txn-3", expires_in_days=61, originalTransactionId="txn-1"),
    )

    assert out == ("applied:DID_RENEW" if sb.lineage else "unapplied:stale:DID_RENEW", _USER)
    if sb.lineage:
        assert _row(sb)["status"] == "active" and sb._tier() == "premium"
        assert svc._parse_iso(_row(sb)["current_period_end"]) == _ANCHOR + timedelta(days=61)
        assert sb.credits == {"total": 4000, "used": 0}, "the recovered period's allocation"
    else:
        assert _row(sb)["status"] == "grace_period", "accepted before 198: refused as stale"


def test_the_apps_relay_of_the_recovery_after_a_late_grace_notice_applies_too(service_clock):
    """The same order, the recovery relayed by the app (`Transaction.updates`), signed before the
    grace notice. Lineage mode only, as above."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=60)
    _grace_notice_retried(s, signed=_GRACE_END + timedelta(seconds=16))

    out = s.apply_verified_transaction(_USER, _txn(
        product=_MAX, txn_id="txn-3", expires_in_days=61, originalTransactionId="txn-1",
        signedDate=_ms(_GRACE_END + timedelta(seconds=10))))

    if sb.lineage:
        assert out["was_stale"] is False and _row(sb)["status"] == "active"
        assert sb.credits == {"total": 4000, "used": 0}
    else:
        assert out["refused_by"] == "stale" and _row(sb)["status"] == "grace_period"


def test_the_grace_recovery_bypass_edges():
    """Pure edges of `_grace_recovery_reason`: only a NEWER, entitling, unrevoked period of the
    same lineage over a row in grace or billing retry."""
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "grace_period", "tier": "premium",
           "current_period_end": end.isoformat()}

    def reason(prior=row, *, status="active", revoked=False, otid="otid",
               period_end=end + timedelta(days=30), tier="premium", env="Production",
               purchase=None):
        return svc._grace_recovery_reason(
            prior, status=status, payload_revoked=revoked, tier=tier, environment=env,
            original_transaction_id=otid, period_end=period_end, purchase_date=purchase)

    assert reason() is not None and end.isoformat() in reason()
    for status in ("grace_period", "billing_retry", "Grace_Period"):
        assert reason(prior={**row, "status": status}) is not None, status
    for status in ("active", "expired", "revoked", "", None):
        assert reason(prior={**row, "status": status}) is None, status
    for status in ("expired", "revoked"):
        assert reason(status=status) is None, status
    assert reason(status="grace_period") is not None, "a newer period in grace is still newer"
    assert reason(revoked=True) is None
    assert reason(otid="another") is None and reason(otid="") is None
    assert reason(prior=None) is None and reason(period_end=None) is None
    assert reason(period_end=end + timedelta(seconds=59)) is None, "inside the drift margin"
    assert reason(period_end=end + timedelta(seconds=60)) is not None
    assert reason(period_end=end - timedelta(days=30)) is None, "an OLDER period: R1's business"
    assert reason(prior={**row, "current_period_end": "junk"}) is None
    # Rule D's lower-tier guard (IAP audit F03): a lower tier ending inside the overlap bound
    # after the period in grace is the renewal an upgrade superseded; a downgrade's recovery ends
    # a whole period later. A higher tier always counts; Sandbox never takes a lower one as newer.
    assert reason(tier="pro", period_end=end + timedelta(hours=12)) is None
    assert reason(tier="pro", period_end=end + timedelta(days=7)) is None
    assert reason(tier="pro", period_end=end + timedelta(days=7, seconds=1)) is not None
    assert reason(tier="pro", period_end=end + timedelta(days=30), env="Sandbox") is None
    assert reason(prior={**row, "tier": "pro"}, tier="premium",
                  period_end=end + timedelta(hours=12)) is not None
    # With Apple's purchaseDate (review round 2): a downgrade's recovery is charged after the period
    # in grace began to fail, whatever its offer length; the superseded renewal well before its end.
    assert reason(tier="pro", period_end=end + timedelta(days=3), purchase=end + timedelta(days=2)) \
        is not None, "a 3-day offer downgrade recovered in grace"
    assert reason(tier="pro", period_end=end + timedelta(hours=12),
                  purchase=end - timedelta(days=30)) is None
    assert reason(tier="pro", period_end=end + timedelta(days=30), purchase=end - timedelta(days=6)) \
        is None, "charged inside the Max period it is in grace for: superseded"
    # No transactionReason exemption here (review round 4): the bypass does not even take one, so
    # the saved JWS of the Pro PURCHASE an upgrade superseded stays refused over the Max in grace.
    assert "transaction_reason" not in inspect.signature(svc._grace_recovery_reason).parameters


def test_a_late_higher_tier_grace_notice_cannot_rewind_a_downgrade_recovery(service_clock):
    """A downgrade applies at the renewal, so the recovered period is Pro while the period that
    failed was Max. The late Max grace notice ranks higher, and R1 exempted every entitling higher
    tier as an upgrade: it rewound the Pro row to the failed Max period (and Max features). A grace
    notice is never an upgrade: refused, except inside the overlap bound."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=60)
    s.apply_notification(
        {"notificationType": "DID_RENEW", "subtype": "BILLING_RECOVERY",
         "signedDate": _ms(_GRACE_END - timedelta(hours=7))},
        _txn(product=_PRO, txn_id="txn-3", expires_in_days=61, originalTransactionId="txn-1"),
    )
    assert (_row(sb)["tier"], _row(sb)["status"]) == ("pro", "active")
    before = dict(_row(sb))

    _grace_notice_retried(s, signed=_GRACE_END + timedelta(seconds=16))

    assert _row(sb) == before and sb._tier() == "pro"


def test_the_period_scoped_higher_tier_exemption_is_bounded_by_the_overlap():
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "pro",
           "current_period_end": end.isoformat()}

    def reason(*, ntype="DID_FAIL_TO_RENEW", gap=timedelta(days=30), env="Production",
               override="grace_period"):
        return svc._older_period_reason(
            row, status=override, status_override=override, tier="premium", environment=env,
            original_transaction_id="otid", period_end=end - gap, notification_type=ntype)

    assert reason() is not None, "a downgrade's failed Max period, a whole period older"
    assert reason(gap=timedelta(days=7, seconds=1)) is not None
    for gap in (timedelta(minutes=5), timedelta(days=4), timedelta(days=7)):
        assert reason(gap=gap) is None, gap                       # an overlap upgrade's notice
        assert reason(gap=gap, env=None) is None, gap
        assert reason(gap=gap, env="Sandbox") is not None, gap
    # Not period-scoped: an entitling higher tier is an upgrade at any distance, as before.
    assert reason(ntype="OFFER_REDEEMED", override="active") is None
    assert reason(ntype=None, override="active") is None


@pytest.mark.parametrize("subtype", [None, "DOWNGRADE"])
def test_an_offer_redeemed_during_grace_keeps_the_grace_period(service_clock, subtype):
    """An offer redeemed for the next renewal while in grace carries the period in grace (past
    end) with an EXPLICIT "active". Written, the row read `active` with a past end, and
    `winning_tier` dropped the subscriber to Free. It says only that the period ended: held."""
    sb = FakeSupabase()
    s = _max_in_grace(sb, service_clock)
    before = dict(_row(sb))

    out = _notify(s, "OFFER_REDEEMED", subtype, _GRACE_END + timedelta(days=2), _grace_txn())

    assert out == ("unapplied:billing_grace:OFFER_REDEEMED", _USER)
    assert _row(sb) == before and sb._tier() == "premium" and sb.remaining == 3700


def test_an_offer_redeemed_for_a_running_period_still_applies(service_clock):
    """Twin: an explicit "active" about a period that is still running is not touched."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase()
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))

    _notify(s, "OFFER_REDEEMED", None, now - timedelta(hours=1), _grace_txn())

    assert _row(sb)["status"] == "active" and sb._tier() == "premium"


def test_a_cancellation_signed_while_active_and_delivered_late_is_held(service_clock):
    """Apple signed the AUTO_RENEW_DISABLED before the period ended (data.status 1) and its
    delivery was retried past the end, so it now derives "expired". It only stopped the next
    renewal; it does not end the period. Held, the grace retry then applies (Apple can enter grace
    after an earlier failed renewal attempt even with auto-renew turned off)."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock)
    before = dict(_row(sb))

    _notify(s, "DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED",
            _GRACE_END - timedelta(hours=2), _grace_txn(), apple_status=1)

    assert _row(sb) == before and sb.remaining == 3700
    _grace_notice_retried(s)
    assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"


@pytest.mark.parametrize("apple_status", [4, None], ids=["apple-says-grace", "no-status"])
def test_a_derived_expiry_past_the_window_stores_no_key_when_apple_says_grace(
        service_clock, apple_status):
    """Past the 24 h window a derived expiry applies. With Apple's data.status 4 (billing grace as
    of its signedDate) it no longer stores that signedDate, so Apple's grace notice — signed
    earlier, retried later — still brings the tier back (the held balance has lapsed: accepted).
    Without a status, the key is stored as before and the retry is stale."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=25 * 60)
    key_before = _row(sb).get("last_event_at")

    _notify(s, "PRICE_INCREASE", "ACCEPTED", _GRACE_END + timedelta(hours=25), _grace_txn(),
            apple_status=apple_status)
    assert _row(sb)["status"] == "expired" and sb.credits == {"total": 50, "used": 0}

    _grace_notice_retried(s)
    if apple_status == 4:
        assert _row(sb)["last_event_at"] != (_GRACE_END + timedelta(hours=25)).isoformat()
        assert _row(sb)["status"] == "grace_period" and sb._tier() == "premium"
    else:
        assert key_before != _row(sb)["last_event_at"]
        assert _row(sb)["status"] == "expired" and sb._tier() == "free"


_LATE_GRACE = "billing grace arrived AFTER the row was expired"


def test_the_late_grace_warning_is_logged_once_even_after_a_fence_miss(service_clock,
                                                                        fence_backoffs, caplog):
    sb = _Racy()
    s = _max_past_its_end(sb, service_clock, minutes=25 * 60)
    s.apply_verified_transaction(_USER, _grace_txn(
        signedDate=_ms(_GRACE_END + timedelta(hours=25))))
    assert _row(sb)["status"] == "expired"

    def another_writer(racy):
        racy.db["subscriptions"][0]["updated_at"] = (
            _GRACE_END + timedelta(hours=25, seconds=1)).isoformat()

    sb.concurrent = another_writer
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _grace_notice_retried(s)

    assert fence_backoffs == [1] and _row(sb)["status"] == "grace_period"
    assert len([r for r in caplog.records if _LATE_GRACE in r.getMessage()]) == 1


def test_the_late_grace_warning_stays_silent_when_nothing_lapsed(service_clock, caplog):
    """Twins: over an `active` row (the usual late notice) and over an expired row of ANOTHER
    period, nothing is said."""
    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _grace_notice_retried(s)
    assert _row(sb)["status"] == "grace_period"
    assert not [r for r in caplog.records if _LATE_GRACE in r.getMessage()]

    sb = FakeSupabase()
    s = _max_past_its_end(sb, service_clock, minutes=25 * 60)
    s.apply_verified_transaction(_USER, _grace_txn(
        signedDate=_ms(_GRACE_END + timedelta(hours=25))))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_notification(
            {"notificationType": "DID_FAIL_TO_RENEW", "subtype": "GRACE_PERIOD",
             "signedDate": _ms(_GRACE_END + timedelta(hours=26))},
            _txn(product=_MAX, txn_id="txn-3", expires_in_days=61,
                 originalTransactionId="txn-1"),
        )
    assert not [r for r in caplog.records if _LATE_GRACE in r.getMessage()]
