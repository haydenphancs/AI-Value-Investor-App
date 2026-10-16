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

from app.config import settings
from app.services import iap_service as svc


_USER = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
_OTHER = "11110000-ffff-4eee-8ddd-ccccbbbbaaaa"


@pytest.fixture(autouse=True)
def _grace_default(monkeypatch):
    """The windows here assume the DEFAULT grace length; `settings` reads backend/.env, where an
    owner may set IAP_BILLING_GRACE_DAYS. A test that wants another length sets it itself."""
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 28)


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
        # postgrest-py sends `col=eq.None`, which Postgres rejects; Python's None == None would
        # quietly match and hide it.
        if val is None:
            raise RuntimeError(f"22P02 invalid input syntax: {col}=eq.None")
        self._eq[col] = val; return self

    def in_(self, col, vals):
        self._in = (col, set(vals)); return self

    def lt(self, col, val):
        self._lt = (col, val); return self

    def limit(self, n):
        self._limit = n; return self

    def order(self, col, *_a, **_k):
        self._order = col; return self

    @property
    def not_(self):
        return self

    def is_(self, _col, _val):
        return self

    def _matches(self, r) -> bool:
        if self._in and (r.get(self._in[0]) or "").lower() not in self._in[1]:
            return False
        if self._lt:
            v = r.get(self._lt[0])
            if v is None or str(v) >= self._lt[1]:
                return False
        return all(r.get(k) == v for k, v in self._eq.items())

    def execute(self):
        rows = self._db.setdefault(self._table, [])
        if self._op == "select":
            out = [dict(r) for r in rows if self._matches(r)]
            # Model the server: an ORDER BY when asked, else storage order, then the LIMIT.
            if getattr(self, "_order", None):
                out.sort(key=lambda r: str(r.get(self._order)))
            if getattr(self, "_limit", None) is not None:
                out = out[: self._limit]
            return type("R", (), {"data": out})()
        if self._op == "update":
            # The rows it touched, as PostgREST returns by default (return=representation):
            # the sweep counts and reconciles only rows its re-checking update actually wrote.
            # Every filter applies, as in the SQL WHERE (the sweep's update re-checks its
            # selection).
            touched = []
            for r in rows:
                if self._matches(r):
                    r.update(self._values)
                    touched.append(dict(r))
            return type("R", (), {"data": touched})()
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
    """Past its window (the billing grace period + 24 h, or Apple's 60-day retry window) the row
    is genuinely dead."""
    sb = FakeSupabase([_row(status=status, end_delta={"days": -75})])
    assert _service(sb).sweep_expired_subscriptions()["expired"] == 1


@pytest.mark.parametrize("grace_days", [3, 16, 28])
def test_a_grace_period_row_expires_a_day_after_the_billing_grace_period(monkeypatch, grace_days):
    """2026-10-14: `grace_period` got 60 days, but Apple's Billing Grace Period is at most 28, and
    its GRACE_PERIOD_EXPIRED is what ends it. When that notification was lost, an unpaid user kept
    the tier for up to ~32 days after Apple stopped serving. Now: the App Store Connect length
    (`IAP_BILLING_GRACE_DAYS`) + the same 24 h margin as an `active` row."""
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", grace_days)
    sb = FakeSupabase([
        _row(status="grace_period", end_delta={"days": -grace_days, "hours": -23}, rid="inside"),
        _row(status="grace_period", end_delta={"days": -grace_days, "hours": -25}, rid="past"),
    ])

    out = _service(sb).sweep_expired_subscriptions()

    assert out["expired"] == 1
    status = {r["id"]: r["status"] for r in sb.db["subscriptions"]}
    assert status == {"inside": "grace_period", "past": "expired"}


def test_billing_retry_keeps_apples_60_day_window():
    """`billing_retry` is not the grace period: it keeps the 60 days whatever the setting."""
    sb = FakeSupabase([_row(status="billing_retry", end_delta={"days": -40})])
    assert _service(sb).sweep_expired_subscriptions()["expired"] == 0
    assert sb.db["subscriptions"][0]["status"] == "billing_retry"


def test_the_sweep_windows():
    windows = svc._sweep_windows()
    assert set(windows) == svc._ENTITLING_STATUSES
    assert windows["active"] == timedelta(hours=24)
    assert windows["grace_period"] == timedelta(days=settings.IAP_BILLING_GRACE_DAYS, hours=24)
    assert windows["billing_retry"] == timedelta(days=60)
    from app.config import Settings
    assert Settings.model_fields["IAP_BILLING_GRACE_DAYS"].default == 28, "Apple's maximum"


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


def test_grace_rows_inside_their_window_cannot_starve_a_lapsed_active_row():
    """One unordered `.limit(500)` read with the 60-day grace filter applied in PYTHON: once
    500 grace/billing-retry rows were still inside their window, they filled every page and
    the lapsed `active` row behind them was never expired — the exact leak this sweep closes."""
    rows = [
        _row(user=_OTHER, status="grace_period", end_delta={"days": -3}, tier="pro", rid=f"g{i}")
        for i in range(600)
    ]
    rows.append(_row(status="active", end_delta={"days": -10}, rid="lapsed"))
    sb = FakeSupabase(rows)
    out = _service(sb).sweep_expired_subscriptions()
    assert out["expired"] == 1
    lapsed = next(r for r in sb.db["subscriptions"] if r["id"] == "lapsed")
    assert lapsed["status"] == "expired"
    assert sb.tier_of(_USER) == "free"
    assert all(r["status"] == "grace_period" for r in sb.db["subscriptions"] if r["id"] != "lapsed")


def test_the_oldest_lapsed_rows_are_expired_first_when_over_the_limit():
    rows = [_row(status="active", end_delta={"days": -(2 + i)}, rid=f"a{i}") for i in range(5)]
    sb = FakeSupabase(rows)
    out = _service(sb).sweep_expired_subscriptions(limit=2)
    assert out["expired"] == 2
    done = {r["id"] for r in sb.db["subscriptions"] if r["status"] == "expired"}
    assert done == {"a4", "a3"}, "a capped pass must drain the oldest backlog first"


# ── A write between the sweep's read and its update (2026-10-13) ──────────────

def _racing(sb, change):
    """`change(row)` lands on the stored row right before the sweep's UPDATE executes."""
    original_table = sb.table

    def table(name):
        q = original_table(name)
        if name == "subscriptions":
            execute = q.execute

            def racing_execute():
                if q._op == "update":
                    change(sb.db["subscriptions"][0])
                return execute()

            q.execute = racing_execute
        return q

    sb.table = table
    return sb


@pytest.mark.parametrize("change, why", [
    (lambda r: r.update(current_period_end=_iso(days=27), updated_at=_iso()), "renewed"),
    (lambda r: r.update(status="grace_period", updated_at=_iso()), "now in grace"),
])
def test_a_row_that_stopped_qualifying_after_the_read_is_not_expired(change, why):
    """The sweep reads a lapsed row, then expires it — and the UPDATE matched on `id` alone, so
    a late DID_RENEW landing in between was overwritten to `expired` and a subscriber who had
    just paid dropped to free. The UPDATE re-checks the selection now: no match, nothing counted,
    nobody reconciled."""
    sb = _racing(FakeSupabase([_row(status="active", end_delta={"days": -3})]), change)

    out = _service(sb).sweep_expired_subscriptions()

    assert out["expired"] == 0 and out["users_reconciled"] == 0 and out["errors"] == 0, why
    assert sb.db["subscriptions"][0]["status"] != "expired"
    assert sb.rpcs == []


def test_a_grace_row_that_moved_to_billing_retry_after_the_read_waits_for_its_own_window():
    """Each row is expired under the window of the status that SELECTED it: past the grace
    period's but inside billing_retry's 60 days, a row that changed status meanwhile is left
    for the next pass to judge."""
    sb = _racing(FakeSupabase([_row(status="grace_period", end_delta={"days": -40})]),
                 lambda r: r.update(status="billing_retry", updated_at=_iso()))

    out = _service(sb).sweep_expired_subscriptions()

    assert out["expired"] == 0 and out["users_reconciled"] == 0
    assert sb.db["subscriptions"][0]["status"] == "billing_retry"


def test_a_row_still_overdue_after_an_unrelated_write_is_still_expired():
    """The re-check is on what selected the row, not on whether anything at all changed: a
    write that leaves it lapsed (here, only the environment backfilled) still lets it expire."""
    sb = _racing(FakeSupabase([_row(status="active", end_delta={"days": -3})]),
                 lambda r: r.update(environment="Production", updated_at=_iso()))

    out = _service(sb).sweep_expired_subscriptions()

    assert out["expired"] == 1 and out["users_reconciled"] == 1
    assert sb.db["subscriptions"][0]["status"] == "expired"


def test_an_expiry_whose_reply_was_lost_still_reconciles_the_user():
    """The UPDATE can COMMIT and lose its reply. Then no later pass selects the row (it is
    `expired`), so skipping the reconcile left users.tier on the paid tier for good. The
    reconcile reads the rows as they are, so it runs whatever the UPDATE's fate."""
    sb = FakeSupabase([_row(status="active", end_delta={"days": -3})])
    original_table = sb.table

    def table(name):
        q = original_table(name)
        if name == "subscriptions":
            execute = q.execute

            def committed_then_lost():
                out = execute()
                if q._op == "update":
                    raise TimeoutError("read timeout after the commit")
                return out

            q.execute = committed_then_lost
        return q

    sb.table = table
    out = _service(sb).sweep_expired_subscriptions()

    assert out["errors"] == 1 and out["users_reconciled"] == 1
    assert sb.db["subscriptions"][0]["status"] == "expired"
    assert sb.tier_of(_USER) == "free"


# ── Every row the sweep expires is a lost notification: logged per row (2026-10-15) ──

@pytest.mark.parametrize("status, end_days, never", [
    ("active", -3, "DID_RENEW, EXPIRED or DID_FAIL_TO_RENEW"),
    ("grace_period", -40, "GRACE_PERIOD_EXPIRED, EXPIRED or BILLING_RECOVERY"),
    ("billing_retry", -75, "EXPIRED or BILLING_RECOVERY"),
])
def test_each_expired_row_is_logged_with_what_never_arrived(caplog, status, end_days, never):
    """Apple sends a notification for every way a period ends and retries a failed one for
    ~6.5 days (Sandbox once), so a row the SWEEP expires is one whose notification never
    arrived. A per-row WARNING makes them countable; repeated ones are the trigger for an App
    Store Server API reconcile (SYSTEM_DESIGN_GUIDELINES §9b.12)."""
    import logging

    row = {**_row(status=status, end_delta={"days": end_days}, rid="r1"),
           "original_transaction_id": "otid-1", "environment": "Sandbox"}
    sb = FakeSupabase([row])
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        assert _service(sb).sweep_expired_subscriptions()["expired"] == 1

    msgs = [r.getMessage() for r in caplog.records if "IAP sweep: EXPIRED" in r.getMessage()]
    assert len(msgs) == 1
    assert "id=r1" in msgs[0] and f"user={_USER}" in msgs[0] and f"status={status}" in msgs[0]
    assert "original_txn=otid-1" in msgs[0] and "env=Sandbox" in msgs[0]
    assert f"no {never} arrived" in msgs[0]


def test_a_row_that_no_longer_qualified_is_not_logged_as_expired(caplog):
    import logging

    sb = _racing(FakeSupabase([_row(status="active", end_delta={"days": -3})]),
                 lambda r: r.update(current_period_end=_iso(days=27), updated_at=_iso()))
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _service(sb).sweep_expired_subscriptions()
    assert not any("IAP sweep: EXPIRED" in r.getMessage() for r in caplog.records)
