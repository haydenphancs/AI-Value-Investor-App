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

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services import entitlements
from app.services import iap_service as svc
from test_iap_entitlement import FakeSupabase as _LedgerFake
from test_iap_entitlement import _Q as _LedgerQ
from test_iap_entitlement import service_clock  # noqa: F401  (a fixture)


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
        self._gte: tuple | None = None

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

    def gte(self, col, val):
        self._gte = (col, val); return self

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
        if self._gte:
            v = r.get(self._gte[0])
            if v is None or str(v) < self._gte[1]:
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


# ── A reconcile that fails AFTER the row expired is retried (IAP audit F02, 2026-10-17) ──────
#
# The sweep's UPDATE commits `expired`, then `reconcile_user_tier` hits a transient PostgREST
# error. No later pass selects an `expired` row, so before the fix nothing ever reconciled that
# user again: `users.tier` (the gate for paid features) kept the paid tier for good, and the ERROR
# promised a self-heal that never came. Driven through test_iap_entitlement's FakeSupabase — both
# schema modes, the credit model behind every rpc — with the filters the sweep sends.

class _SweepQ(_LedgerQ):
    """`_LedgerQ` plus the sweep's filters (`in_`, `lt`, `gte`, `not_.is_(col, "null")`), each
    evaluated per row on PARSED timestamps and narrowing a SELECT and an UPDATE alike, as the SQL
    WHERE does."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._extra = []
        self._neg = False

    def in_(self, col, vals):
        vals = list(vals)
        self._extra.append(lambda r, c=col, v=vals: r.get(c) in v)
        return self

    def _cmp(self, col, val, ok):
        def pred(r, c=col, v=val):
            a, b = svc._parse_iso(r.get(c)), svc._parse_iso(v)
            return a is not None and b is not None and ok(a, b)
        self._extra.append(pred)
        return self

    def lt(self, col, val):
        return self._cmp(col, val, lambda a, b: a < b)

    def gte(self, col, val):
        return self._cmp(col, val, lambda a, b: a >= b)

    @property
    def not_(self):
        self._neg = True
        return self

    def is_(self, col, val):
        assert self._neg and str(val).lower() == "null", "only .not_.is_(col, 'null') is modelled"
        self._neg = False
        self._extra.append(lambda r, c=col: r.get(c) is not None)
        return self

    def execute(self):
        if self._extra and self._op in ("select", "update"):
            real = self._db
            narrowed = [r for r in real.setdefault(self._table, [])
                        if all(p(r) for p in self._extra)]
            self._db = {self._table: narrowed}      # the same row dicts: an UPDATE writes through
            try:
                return super().execute()
            finally:
                self._db = real
        return super().execute()


class _Sweepable(_LedgerFake):
    """`down(table, op)` True → that call raises a transient error before executing.
    `lose_reply` = {(table, op)}: that write COMMITS, then raises (its reply lost).
    `after_write(table, op)` runs right after a write executed."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.down = lambda table, op: False
        self.after_write = None
        self.lose_reply: set = set()

    def table(self, name):
        q = _SweepQ(self.db, self.log, name, self._fail, owner=self)
        original = q.execute

        def execute():
            if self.down(name, q._op):
                raise RuntimeError(f"transient: {name} {q._op} -> 503 Service Unavailable")
            out = original()
            if q._op in ("insert", "update"):
                if self.after_write:
                    self.after_write(name, q._op)
                if (name, q._op) in self.lose_reply:
                    self.lose_reply.discard((name, q._op))
                    raise RuntimeError(f"transient: reply to {name} {q._op} lost (it committed)")
            return out

        q.execute = execute
        return q


def _lapsed_max(mode, now, *, end_ago=timedelta(days=3)):
    """A Production Max row whose EXPIRED never arrived, past the sweep's 24 h window; users.tier
    still mirrors the paid tier it had while the period ran."""
    end = now - end_ago
    row = {"id": "sub-1", "user_id": _USER, "tier": "premium", "status": "active",
           "current_period_end": end.isoformat(), "original_transaction_id": "otx-1",
           "environment": "Production", "updated_at": (end - timedelta(days=30)).isoformat()}
    sb = _Sweepable(subscriptions=[row], schema=mode)
    assert sb.lineage is (mode == "lineage")
    sb.db["users"][0]["tier"] = "premium"
    return sb


def _outage_after_the_expiry(sb, *, hits):
    """The blip starts right after the sweep's subscriptions UPDATE committed; `hits(table, op)`
    says which later calls it breaks. Returns the callable that ends it."""
    state = {"on": False}

    def after_write(table, op):
        if table == "subscriptions" and op == "update":
            state["on"] = True

    sb.after_write = after_write
    sb.down = lambda table, op: state["on"] and hits(table, op)

    def clear():
        state["on"] = False
        sb.after_write = None
        sb.down = lambda table, op: False
    return clear


def _ledger_service(sb):
    s = svc.IAPService.__new__(svc.IAPService)
    s.supabase = sb
    return s


def _users_writes(sb):
    return [e for e in sb.log if e[:2] == ("update", "users")]


_RECONCILE_FAILURE_POINTS = {
    # reconcile_user_tier's FIRST effective_tier: winning_tier's subscriptions read ...
    "subs_read": lambda t, op: t == "subscriptions" and op == "select",
    # ... then comp_tier's users read ...
    "comp_tier_read": lambda t, op: t == "users" and op == "select",
    # ... then the users.tier mirror write.
    "users_write": lambda t, op: t == "users" and op == "update",
}


@pytest.mark.parametrize("point", sorted(_RECONCILE_FAILURE_POINTS))
@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_a_reconcile_that_failed_after_the_expiry_is_retried_by_the_next_pass(
        mode, point, service_clock, caplog):
    caplog.set_level(logging.INFO)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    sb = _lapsed_max(mode, start)
    service_clock(sb, start)
    s = _ledger_service(sb)
    clear = _outage_after_the_expiry(sb, hits=_RECONCILE_FAILURE_POINTS[point])

    first = s.sweep_expired_subscriptions()
    assert sb.db["subscriptions"][0]["status"] == "expired"           # the UPDATE committed
    # The same outage also fails the pass's own re-check read when it breaks subscriptions reads.
    errors = 2 if point == "subs_read" else 1
    assert first == {"scanned": 1, "expired": 1, "users_reconciled": 0, "errors": errors}, first
    assert sb.db["users"][0]["tier"] == "premium"
    failed = [r.getMessage() for r in caplog.records if "reconcile FAILED" in r.getMessage()]
    assert len(failed) == 1
    # The old line promised "until the next sweep" — no later pass ever selected the row again.
    assert "until the next sweep" not in failed[0]
    assert "re-checks" in failed[0] and "last 7 days" in failed[0]

    clear()                                                            # the blip is over
    caplog.clear()
    service_clock(sb, start + timedelta(hours=1))
    second = s.sweep_expired_subscriptions()
    assert second == {"scanned": 0, "expired": 0, "users_reconciled": 1, "errors": 0}, second
    assert s.effective_tier(_USER) == "free" and sb.db["users"][0]["tier"] == "free"
    assert not entitlements.whale_detail_unlocked(sb.db["users"][0]["tier"])
    assert any("differed from the effective tier" in r.getMessage() for r in caplog.records)

    # Settled: the next passes re-read it and write nothing.
    writes = len(_users_writes(sb))
    for later in (timedelta(hours=2), timedelta(days=6)):
        service_clock(sb, start + later)
        assert s.sweep_expired_subscriptions() == {
            "scanned": 0, "expired": 0, "users_reconciled": 0, "errors": 0}
    assert len(_users_writes(sb)) == writes


@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_a_lost_expiry_reply_and_the_same_outage_heal_on_the_next_pass(
        mode, service_clock, caplog):
    """K75's case: the UPDATE commits, its reply is lost, the except arm reconciles anyway, and
    that reconcile hits the SAME outage (every call fails until it clears)."""
    caplog.set_level(logging.INFO)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    sb = _lapsed_max(mode, start)
    service_clock(sb, start)
    s = _ledger_service(sb)
    sb.lose_reply = {("subscriptions", "update")}
    clear = _outage_after_the_expiry(sb, hits=lambda t, op: True)

    first = s.sweep_expired_subscriptions()
    assert sb.db["subscriptions"][0]["status"] == "expired"
    # The lost reply, the reconcile, and the pass's own re-check read: all inside the outage.
    assert first == {"scanned": 1, "expired": 0, "users_reconciled": 0, "errors": 3}, first
    assert sb.db["users"][0]["tier"] == "premium"

    clear()
    service_clock(sb, start + timedelta(hours=1))
    second = s.sweep_expired_subscriptions()
    assert second["users_reconciled"] == 1 and second["errors"] == 0, second
    assert sb.db["users"][0]["tier"] == "free"


@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_the_recheck_leaves_a_settled_paid_tier_alone(mode, service_clock):
    """A complimentary floor keeps the account on Pro after its Max row expired: users.tier is
    already the effective tier, so the re-check reads it and writes nothing."""
    start = datetime.now(timezone.utc).replace(microsecond=0)
    sb = _lapsed_max(mode, start)
    sb.db["users"][0]["comp_tier"] = "pro"
    service_clock(sb, start)
    s = _ledger_service(sb)
    first = s.sweep_expired_subscriptions()
    assert first["users_reconciled"] == 1 and sb.db["users"][0]["tier"] == "pro"

    writes = len(_users_writes(sb))
    service_clock(sb, start + timedelta(hours=1))
    assert s.sweep_expired_subscriptions() == {
        "scanned": 0, "expired": 0, "users_reconciled": 0, "errors": 0}
    assert len(_users_writes(sb)) == writes and sb.db["users"][0]["tier"] == "pro"


def test_the_recheck_waits_for_a_renewal_verdict_that_is_not_in_yet(service_clock):
    """`winning_tier` drops an `active` row the moment its period ends, while the sweep gives
    Apple 24 h for the renewal. The re-check must not demote an account early because of such a
    row (its own reconcile would, and nothing does that today): it waits, as the sweep does."""
    start = datetime.now(timezone.utc).replace(microsecond=0)
    sb = _lapsed_max("lineage", start)
    pending_end = start + timedelta(minutes=30)
    sb.db["subscriptions"].append({
        "id": "sub-2", "user_id": _USER, "tier": "pro", "status": "active",
        "current_period_end": pending_end.isoformat(), "original_transaction_id": "otx-2",
        "environment": "Production", "updated_at": (start - timedelta(days=29)).isoformat()})
    service_clock(sb, start)
    s = _ledger_service(sb)
    clear = _outage_after_the_expiry(sb, hits=_RECONCILE_FAILURE_POINTS["users_write"])
    assert s.sweep_expired_subscriptions()["errors"] == 1
    assert sb.db["users"][0]["tier"] == "premium"
    clear()

    # Pro's period ended 30 min ago and its DID_RENEW is not in yet: held, as the sweep holds it.
    service_clock(sb, pending_end + timedelta(minutes=30))
    assert s.sweep_expired_subscriptions()["users_reconciled"] == 0
    assert sb.db["users"][0]["tier"] == "premium"

    # Past the sweep's own window the sweep expires Pro and reconciles the account itself.
    service_clock(sb, pending_end + timedelta(hours=25))
    out = s.sweep_expired_subscriptions()
    assert out["expired"] == 1 and out["users_reconciled"] == 1
    assert sb.db["users"][0]["tier"] == "free"
