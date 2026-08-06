"""A lapsed subscriber must stop being entitled, even if no notification ever arrives.

`reconcile_user_tier` is the only writer of `users.tier`, and it runs ONLY when a client
`POST /billing/verify` or an App Store Server Notification arrives. Nothing re-evaluated
entitlement on its own, so a single lost EXPIRED/REFUND left the account paid forever.

That is worse than "keeps access until someone notices": `ensure_credit_period` resolves the
monthly allocation from `users.tier` (migration 100:185), so a cancelled Max subscriber was
handed 4000 fresh credits on the 1st of every month indefinitely — real Gemini and FMP spend
against a subscription that stopped paying. The client only ever reports *purchases*, so
nothing in the product could detect it.

Ways this can go wrong in the other direction, which these tests pin just as hard:

* Expiring a PAYING customer early — clock skew and renewal latency both sit near the period
  boundary, hence the 24h grace.
* Demoting someone in `grace_period` / `billing_retry`. Those statuses mean "the period HAS
  ended and Apple still considers them entitled while it retries billing". Applying the short
  cutoff to them is exactly the demotion `winning_tier` documents avoiding.
* Dropping a user who holds a SECOND, still-valid subscription.

No network, no Supabase.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import iap_service as svc


_USER = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
_OTHER = "11110000-ffff-4eee-8ddd-ccccbbbbaaaa"


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class _Q:
    def __init__(self, db, table):
        self._db, self._table = db, table
        self._op = None
        self._values = None
        self._eq: dict = {}
        self._in: tuple | None = None
        self._lt: tuple | None = None

    def select(self, *_a, **_k):
        self._op = "select"; return self

    def update(self, values):
        self._op, self._values = "update", values; return self

    def eq(self, col, val):
        self._eq[col] = val; return self

    def in_(self, col, vals):
        self._in = (col, set(vals)); return self

    def lt(self, col, val):
        self._lt = (col, val); return self

    def limit(self, _n):
        return self

    @property
    def not_(self):
        return self

    def is_(self, _col, _val):
        return self

    def execute(self):
        rows = self._db.setdefault(self._table, [])
        if self._op == "select":
            out = []
            for r in rows:
                if self._in and (r.get(self._in[0]) or "").lower() not in self._in[1]:
                    continue
                if self._lt:
                    v = r.get(self._lt[0])
                    if v is None or str(v) >= self._lt[1]:
                        continue
                if not all(r.get(k) == v for k, v in self._eq.items()):
                    continue
                out.append(dict(r))
            return type("R", (), {"data": out})()
        if self._op == "update":
            for r in rows:
                if all(r.get(k) == v for k, v in self._eq.items()):
                    r.update(self._values)
            return type("R", (), {"data": []})()
        return type("R", (), {"data": []})()


class FakeSupabase:
    def __init__(self, subscriptions):
        self.db = {
            "subscriptions": [dict(r) for r in subscriptions],
            "users": [{"id": _USER, "tier": "premium"}, {"id": _OTHER, "tier": "pro"}],
        }
        self.rpcs: list = []

    def table(self, name):
        return _Q(self.db, name)

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        return type("R", (), {"execute": lambda *_a, **_k: None})()

    def tier_of(self, user_id):
        return next(r["tier"] for r in self.db["users"] if r["id"] == user_id)


def _service(sb):
    s = svc.IAPService.__new__(svc.IAPService)
    s.supabase = sb
    return s


def _row(user=_USER, status="active", end_delta=None, tier="premium", rid="s1"):
    return {
        "id": rid, "user_id": user, "tier": tier, "status": status,
        "current_period_end": _iso(**end_delta) if end_delta else None,
    }


# ── The leak this closes ──────────────────────────────────────────────────────

def test_long_lapsed_subscription_is_expired_and_the_tier_drops():
    """THE bug: cancelled Max subscriber, notification never arrived, still premium."""
    sb = FakeSupabase([_row(status="active", end_delta={"days": -10})])
    out = _service(sb).sweep_expired_subscriptions()

    assert out["expired"] == 1
    assert sb.db["subscriptions"][0]["status"] == "expired"
    assert sb.tier_of(_USER) == "free", "user kept a paid tier after their period ended"
    assert out["users_reconciled"] == 1


def test_the_sweep_is_idempotent():
    """A second pass must find nothing — otherwise an hourly job churns rows and re-logs
    forever."""
    sb = FakeSupabase([_row(status="active", end_delta={"days": -10})])
    s = _service(sb)
    s.sweep_expired_subscriptions()
    second = s.sweep_expired_subscriptions()
    assert second == {"scanned": 0, "expired": 0, "users_reconciled": 0, "errors": 0}


# ── Must not demote a paying customer ─────────────────────────────────────────

def test_an_active_subscription_in_its_period_is_untouched():
    sb = FakeSupabase([_row(status="active", end_delta={"days": 20})])
    out = _service(sb).sweep_expired_subscriptions()
    assert out["expired"] == 0
    assert sb.db["subscriptions"][0]["status"] == "active"


def test_a_renewal_just_past_the_boundary_is_not_expired():
    """Apple renews around the boundary and the notification lands slightly after. Expiring
    at exactly `current_period_end` would demote paying customers on every renewal."""
    sb = FakeSupabase([_row(status="active", end_delta={"hours": -2})])
    assert _service(sb).sweep_expired_subscriptions()["expired"] == 0


@pytest.mark.parametrize("status", ["grace_period", "billing_retry"])
def test_billing_retry_states_get_the_long_window(status):
    """These mean 'period ended, still entitled while Apple retries'. The short cutoff must
    not apply — that is the demotion winning_tier explicitly avoids."""
    sb = FakeSupabase([_row(status=status, end_delta={"days": -10})])
    assert _service(sb).sweep_expired_subscriptions()["expired"] == 0
    assert sb.db["subscriptions"][0]["status"] == status


@pytest.mark.parametrize("status", ["grace_period", "billing_retry"])
def test_billing_retry_states_do_eventually_expire(status):
    """60 days is Apple's maximum retry window; past it the row is genuinely dead."""
    sb = FakeSupabase([_row(status=status, end_delta={"days": -75})])
    assert _service(sb).sweep_expired_subscriptions()["expired"] == 1


def test_rows_with_no_period_end_are_left_alone():
    """Non-renewing purchases carry no expiresDate. A NULL must never read as 'long ago'."""
    sb = FakeSupabase([_row(status="active", end_delta=None)])
    assert _service(sb).sweep_expired_subscriptions()["expired"] == 0


def test_already_expired_and_revoked_rows_are_not_rescanned():
    sb = FakeSupabase([
        _row(status="expired", end_delta={"days": -30}, rid="s1"),
        _row(status="revoked", end_delta={"days": -30}, rid="s2"),
    ])
    assert _service(sb).sweep_expired_subscriptions()["scanned"] == 0


def test_a_second_still_valid_subscription_keeps_the_user_entitled():
    """Someone who upgraded holds an old lapsed row AND a live one. Expiring the dead row
    must not drop them to free — reconcile recomputes from what remains."""
    sb = FakeSupabase([
        _row(status="active", end_delta={"days": -10}, tier="pro", rid="old"),
        _row(status="active", end_delta={"days": 20}, tier="premium", rid="live"),
    ])
    out = _service(sb).sweep_expired_subscriptions()
    assert out["expired"] == 1
    assert sb.db["subscriptions"][1]["status"] == "active"
    assert sb.tier_of(_USER) == "premium", "expiring a stale row demoted a paying customer"


# ── Failure handling ──────────────────────────────────────────────────────────

def test_a_read_failure_is_reported_not_raised():
    """An hourly background loop must not die on a transient Supabase blip."""
    class _Broken(FakeSupabase):
        def table(self, name):
            raise RuntimeError("supabase down")

    out = _service(_Broken([])).sweep_expired_subscriptions()
    assert out["errors"] == 1 and out["expired"] == 0


def test_each_affected_user_is_reconciled_once():
    """Three dead rows for one user is one reconcile, not three."""
    sb = FakeSupabase([
        _row(status="active", end_delta={"days": -10}, rid=f"s{i}") for i in range(3)
    ])
    out = _service(sb).sweep_expired_subscriptions()
    assert out["expired"] == 3
    assert out["users_reconciled"] == 1
