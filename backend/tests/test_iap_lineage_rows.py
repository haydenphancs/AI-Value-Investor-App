"""Migration 198: one `subscriptions` row per Apple lineage, the transaction register, and the
keyed clawback — the six gaps SYSTEM_DESIGN_GUIDELINES §9b.12 listed as "Known, accepted" on
2026-10-13, the scenarios the two adversarial reviews of the design probed (2026-10-14), and the
service-level findings of the workflow review of the implementation (2026-10-15, review #N in the
test names and docstrings). The SQL's credit branches are pinned on the model in
tests/test_credit_lineage_model.py.

Each test drives the REAL `IAPService` over `FakeSupabase(schema="lineage")`, whose RPCs run the
ONE executable model of the SQL (`test_credit_pool_isolation._Credits`, walked against migration
198's real SQL on a local Postgres: model ≡ SQL, and one-row accounts ≡ migration 196). The
legacy half of every shared rule runs in tests/test_iap_entitlement.py, which is parametrized
over both schemas.

The weight is on the ways it goes wrong: a refunded period's credits left spendable, a paying
user dropped to Free, a lineage's webhooks lost, a replay of a refunded transaction granted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from test_iap_entitlement import (
    _ANCHOR, _GRACE_END, _MAX, _PRO, _USER, FakeSupabase, _Racy, _grace_notice_retried,
    _grace_txn, _jws, _max_past_its_end, _ms, _renewed_max, _row, _row_of, _service, _txn,
    service_clock,  # noqa: F401  (a fixture)
)
from test_iap_entitlement import _notify as _apple_notify

from app.config import settings
from app.services import iap_service as svc


def _lineage(**kw) -> FakeSupabase:
    return FakeSupabase(schema="lineage", **kw)


def _notify(s, ntype, payload, *, at, subtype=""):
    return s.apply_notification({"notificationType": ntype, "subtype": subtype,
                                 "signedDate": _ms(at)}, payload)


def _period(sb, otid, days):
    return sb.periods.get((otid, _ANCHOR + timedelta(days=days)))


def _ledger(sb, reason):
    return [r for r in sb.model.ledger if r["reason"] == reason]


def _register_began(sb, at):
    """198 was applied, and lineage-mode code recorded its first transaction, at `at` — as in
    production, where the register begins at the deploy, before the deliveries a test replays.
    Rules A″ and D read this instant (`IAPService._register_since`)."""
    sb.register[("Production", "txn-first-ever")] = {
        "otid": "txn-first-ever", "user": None, "tier": None, "period_end": None,
        "revocation_date": None, "applied_at": at, "rev": None, "rr": None, "created": at}


@pytest.fixture
def fence_backoffs(monkeypatch):
    calls: list = []
    monkeypatch.setattr(svc, "_fence_backoff", calls.append)
    return calls


# ── G1: the refunded period in force, after an early renewal ──────────────────────────────

# The renewal of a period ending tomorrow ends one CALENDAR month after it, as Apple's monthly
# renewals do (D2 vs D6 reads a renewal by that shape: `iap_service._resubscribed_while_it_ran`).
_EARLY_RENEWAL_END = svc._one_month_after(_ANCHOR + timedelta(days=1))


def _early_renewed_max(sb, now):
    """Max txn-1 ends tomorrow; Apple renews it EARLY (txn-2, one month on): the old period is still
    the credit period in force, and 300 of it is spent."""
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    sb.credits["used"] = 300
    _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-2", expires_in_days=None,
                                 expiresDate=_ms(_EARLY_RENEWAL_END),
                                 originalTransactionId="txn-1"), at=now - timedelta(hours=2))
    assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=1)
    return s


@pytest.mark.parametrize("source", ["notification", "client"])
def test_an_early_renewals_refunded_period_is_clawed_back_at_once(source):
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _early_renewed_max(sb, now)
    refund = _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                  revocationDate=_ms(now))
    if source == "notification":
        _notify(s, "REFUND", refund, at=now)
    else:
        s.apply_verified_transaction(_USER, refund)

    assert sb._tier() == "premium", "the paid renewal keeps the tier"
    assert _row_of(sb, "txn-1")["status"] == "active"
    assert svc._parse_iso(_row_of(sb, "txn-1")["current_period_end"]) == _EARLY_RENEWAL_END
    refunded = _period(sb, "txn-1", 1)
    assert refunded["revoked_at"] is not None and refunded["revoked_remaining"] == 3700
    assert sb.model.paid_period["end"] == _EARLY_RENEWAL_END, "the renewal starts now"
    assert sb.credits == {"total": 4000, "used": 0}

    # Apple redelivers the refund: nothing more moves.
    _notify(s, "REFUND", refund, at=now)
    s.apply_verified_transaction(_USER, refund)
    assert sb.credits == {"total": 4000, "used": 0} and len(_ledger(sb, "tier_revoked")) == 1


def test_reversing_a_clawed_back_period_that_is_no_longer_in_force_is_flagged(caplog):
    """D6 (owner, 2026-10-14): the next period already started, so the reversal restores by hand —
    loud, never silent, and nothing else moves."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _early_renewed_max(sb, now)
    _notify(s, "REFUND", _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                              revocationDate=_ms(now)), at=now)
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _jws(txn_id="txn-1", original="txn-1",
                                           signed_at=now + timedelta(hours=1), expires_in_days=1),
                at=now + timedelta(hours=1))
    assert sb._tier() == "premium" and sb.credits == {"total": 4000, "used": 0}
    flagged = [r.getMessage() for r in caplog.records
               if "credits NOT reinstated — restore by hand" in r.getMessage()]
    assert len(flagged) == 1 and "txn=txn-1" in flagged[0]


def test_a_refund_of_a_sandbox_period_finds_no_paid_period():
    """Sandbox renews every few minutes and never starts a paid period: the keyed clawback has
    nothing to take (`no_period`), and the tier rules are unchanged."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=1),
                                             environment="Sandbox"))
    before = (sb.credits["total"], sb.credits["used"])
    outcomes = []
    real = sb.model.revoke_paid_period

    def capture(*a, **k):
        outcomes.append(real(*a, **k)["outcome"])
        return {"outcome": outcomes[-1]}

    sb.model.revoke_paid_period = capture
    _notify(s, "REFUND", _jws(signed_at=now, environment="Sandbox", revocationDate=_ms(now)), at=now)
    assert sb._tier() == "free"
    assert outcomes and set(outcomes) == {"no_period"}, "the keyed clawback runs and finds nothing"
    assert sb.model.paid_period is None
    assert sb.periods == {}, "Sandbox never started a paid period"
    assert before == (4000, 0), "Sandbox keeps the pre-196 calendar sizing"
    assert sb.remaining == 50 and len(_ledger(sb, "tier_revoked")) == 1, \
        "and its pre-198 refund backstop (as in legacy mode)"


# ── G2: another lineage (a second Apple ID) ────────────────────────────────────────────────

def _two_apple_ids(sb, now):
    """Pro on Apple ID 2 (lineage B), then Max on Apple ID 1 (lineage A): two rows."""
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-b", signed_at=now - timedelta(days=5),
                                             expires_in_days=25, appAccountToken=_USER))
    s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-a", signed_at=now - timedelta(days=1),
                                             expires_in_days=28, appAccountToken=_USER))
    return s


def test_two_apple_ids_keep_two_rows_and_the_better_one_wins():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _two_apple_ids(sb, now)
    assert len(sb.db["subscriptions"]) == 2
    assert sb._tier() == "premium" and sb.model.paid_period["otid"] == "txn-a"

    # B's own renewal webhook still finds its account, and lands on B's row only.
    outcome, user = _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-b2", expires_in_days=55,
                                                 originalTransactionId="txn-b"), at=now)
    assert (outcome, user) == ("applied:DID_RENEW", _USER)
    assert svc._parse_iso(_row_of(sb, "txn-b")["current_period_end"]) == _ANCHOR + timedelta(days=55)
    assert _row_of(sb, "txn-a")["tier"] == "premium" and sb._tier() == "premium"


def test_paying_twice_through_two_apple_ids_is_logged_for_support(caplog):
    """D2 (owner, 2026-10-14, "One allocation"): the account gets the better plan and ONE
    allocation, and a WARNING names it so support can ask the user to cancel the duplicate."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _two_apple_ids(sb, now)
    hits = [r.getMessage() for r in caplog.records if "TWO PAID SUBSCRIPTIONS" in r.getMessage()]
    assert hits and f"user={_USER}" in hits[-1] and "txn-a" in hits[-1] and "txn-b" in hits[-1]
    assert sb.remaining == sb.model.paid_period["allocation"] + sb.model.paid_period["carried"]

    caplog.clear()
    single = _lineage()
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _service(single).apply_verified_transaction(_USER, _jws(signed_at=now))
    assert not [r for r in caplog.records if "TWO PAID SUBSCRIPTIONS" in r.getMessage()]

    # Review #29: a Sandbox (TestFlight) row beside a Production one is a tester, not a payer.
    caplog.clear()
    tester = _lineage()
    s = _service(tester)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-p", signed_at=now - timedelta(days=1),
                                                 expires_in_days=25))
        s.apply_verified_transaction(_USER, _jws(txn_id="txn-s", signed_at=now, environment="Sandbox"))
    assert len(tester.db["subscriptions"]) == 2
    assert not [r for r in caplog.records if "TWO PAID SUBSCRIPTIONS" in r.getMessage()]


@pytest.mark.parametrize("source", ["notification", "client"])
def test_refunding_the_other_lineage_claws_it_back_and_keeps_the_paying_one(source):
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _two_apple_ids(sb, now)
    refund = _jws(product=_MAX, txn_id="txn-a", signed_at=now, expires_in_days=28,
                  revocationDate=_ms(now), appAccountToken=_USER)
    if source == "notification":
        _notify(s, "REFUND", refund, at=now)
    else:
        s.apply_verified_transaction(_USER, refund)

    assert sb._tier() == "pro", "B still pays: Pro, never Free"
    assert _row_of(sb, "txn-b")["status"] == "active"
    assert _period(sb, "txn-a", 28)["revoked_at"] is not None, "A's refunded period is clawed back"
    # D7 (owner, 2026-10-14): B's key was granted once already (before A's upgrade superseded
    # it), so the account sits on the calendar allowance until B renews.
    assert sb.model.paid_period is None and sb.remaining == 50
    _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-b2", expires_in_days=55,
                                 originalTransactionId="txn-b"), at=now + timedelta(minutes=1))
    assert sb.model.paid_period["otid"] == "txn-b" and sb.remaining == 1200


def test_a_worse_lineage_never_demotes_the_paying_one_nor_steals_its_webhooks():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-a", signed_at=now - timedelta(days=5),
                                             expires_in_days=20))
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-b", signed_at=now - timedelta(days=1),
                                             expires_in_days=30))
    assert sb._tier() == "premium" and sb.model.paid_period["otid"] == "txn-a"
    assert len(sb.db["subscriptions"]) == 2

    outcome, user = _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-a2", expires_in_days=50,
                                                 originalTransactionId="txn-a"), at=now)
    assert (outcome, user) == ("applied:DID_RENEW", _USER), "A's webhooks still resolve"
    _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-b", signed_at=now, expires_in_days=30,
                              revocationDate=_ms(now)), at=now)
    assert sb._tier() == "premium" and _row_of(sb, "txn-a")["status"] == "active"


def test_max_ending_then_pro_on_another_apple_id_ends_on_pro():
    """The mirror image of the review's 'no worse' band-aid: the better lineage ends, the newer
    worse one must take over — tier AND its paid period."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-a", signed_at=now - timedelta(days=28),
                                             expires_in_days=1))
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-b", signed_at=now,
                                             expires_in_days=30))
    assert sb._tier() == "premium"
    sb.now = _ANCHOR + timedelta(days=1, seconds=1)
    _notify(s, "EXPIRED", _txn(product=_MAX, txn_id="txn-a", expires_in_days=1), at=now)
    assert _row_of(sb, "txn-a")["status"] == "expired"
    assert sb.model.paid_period["otid"] == "txn-b" and sb.remaining == 1200


# ── The in-force lineage's own renewal is never skipped (review #5/#14/#26, 2026-10-15) ─────
# 198's hold rule stops ANOTHER lineage's same-or-lower tier while the in-force period's lineage
# still entitles. Before the fix only the ORDER BY winner's key was ever evaluated, so when the
# other lineage outranked the in-force one (a higher tier after a downgrade, a later end) nothing
# started at all: the pool sat on the ended period's leftovers while the user paid twice.

def test_the_in_force_lineage_downgrading_beside_another_max_never_freezes_the_pool():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-a", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-b", signed_at=now - timedelta(days=5),
                                             expires_in_days=25))
    assert sb.model.paid_period["otid"] == "txn-a", "B (the same tier) waits: one allocation (D2)"
    _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-a2", expires_in_days=31,
                                 originalTransactionId="txn-a"), at=now)
    assert _row_of(sb, "txn-a")["tier"] == "pro" and sb._tier() == "premium"

    sb.now = _ANCHOR + timedelta(days=1, seconds=1)                   # A's Max period has ended
    sb.rpc("ensure_credit_period", {"p_user_id": _USER})
    assert (sb.model.paid_period["otid"], sb.model.paid_period["tier"]) == ("txn-a", "pro"), \
        "A's own Pro renewal starts — the winner B was held back, so it never did before"
    sb.rpc("ensure_credit_period", {"p_user_id": _USER})
    period = sb.model.paid_period
    assert (period["otid"], period["tier"], period["source"]) == ("txn-b", "premium", "upgrade")
    assert sb.remaining >= 4000, "then B's Max starts over it as an upgrade: ONE allocation, the better plan"
    for _ in range(3):                                                # and it is stable
        sb.rpc("ensure_credit_period", {"p_user_id": _USER})
    assert sb.model.paid_period is period


def _model(now):
    from test_credit_pool_isolation import _Credits
    c = _Credits(total=50, now=now)
    c.tier_alloc = 50
    return c


def test_same_tier_lineages_renewing_early_never_skip_the_in_force_ones_periods():
    """Two Pro lineages 12 h apart, both renewed ~20 h early (Apple's window): when the in-force
    lineage's period ends the other one's end is later, so it wins the ORDER BY."""
    a1 = datetime(2026, 10, 22, 16, tzinfo=timezone.utc)
    c = _model(a1 - timedelta(days=3))
    c.put_subscription("A", "pro", "active", a1)
    c.ensure_credit_period()
    c.put_subscription("B", "pro", "active", a1 + timedelta(hours=12))
    c.ensure_credit_period()
    for k in range(1, 5):
        c.now = a1 + timedelta(days=30 * (k - 1)) - timedelta(hours=20)
        c.put_subscription("A", "pro", "active", a1 + timedelta(days=30 * k))
        c.put_subscription("B", "pro", "active", a1 + timedelta(hours=12) + timedelta(days=30 * k))
        c.ensure_credit_period()
        c.now = a1 + timedelta(days=30 * (k - 1)) + timedelta(hours=1)
        c.ensure_credit_period()
        assert c.paid_period["otid"] == "A" and c.paid_period["end"] == a1 + timedelta(days=30 * k)
        assert c.remaining == 1200
    assert sorted(e for o, e in c.periods if o == "A") == [a1 + timedelta(days=30 * k) for k in range(5)]
    assert not [k for k in c.periods if k[0] == "B"], "B never gets a second allocation"


def test_a_recovery_in_billing_grace_starts_its_period_although_another_lineage_ends_later():
    """Apple keeps the renewal date on a recovery inside billing grace, so a same-tier lineage
    bought during the grace always ends later than the recovered period."""
    d = lambda n: datetime(2026, 10, 20, 16, tzinfo=timezone.utc) + timedelta(days=n)  # noqa: E731
    c = _model(d(0))
    c.put_subscription("A", "pro", "active", d(1))
    c.ensure_credit_period()
    c.used = 1000
    c.now = d(2)
    c.put_subscription("A", "pro", "grace_period", d(1))
    assert c.ensure_credit_period() == 200, "A in grace holds"
    c.now = d(5)
    c.put_subscription("B", "pro", "active", d(35))
    assert c.ensure_credit_period() == 200 and c.paid_period["end"] == d(1), "B waits"
    c.now = d(9)
    c.put_subscription("A", "pro", "active", d(31))                   # recovered, anchor kept
    assert c.ensure_credit_period() == 1200
    assert (c.paid_period["otid"], c.paid_period["end"]) == ("A", d(31))
    assert ("B", d(35)) not in c.periods


def test_a_lineage_displaced_before_198_resolves_through_its_credit_period():
    """Before 198 the owner fallback re-pointed the one row to a new lineage, and the old
    lineage's webhooks found no user. Once 198 is applied they resolve through the credit
    period granted to that account, and the lineage gets its own row."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(schema="legacy")
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-a", signed_at=now - timedelta(days=2),
                                             expires_in_days=20))
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-b", signed_at=now - timedelta(days=1),
                                             expires_in_days=30))
    assert [r["original_transaction_id"] for r in sb.db["subscriptions"]] == ["txn-b"]
    assert ("txn-a", _ANCHOR + timedelta(days=20)) in sb.periods

    sb.schema = "lineage"                       # the owner applies 198
    s = _service(sb)
    outcome, user = _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-a2", expires_in_days=50,
                                                 originalTransactionId="txn-a"), at=now)
    assert (outcome, user) == ("applied:DID_RENEW", _USER)
    assert {r["original_transaction_id"] for r in sb.db["subscriptions"]} == {"txn-a", "txn-b"}
    assert sb._tier() == "premium"


@pytest.mark.parametrize("token, expected", [(_USER, "applied:SUBSCRIBED"),
                                             ("99999999-0000-4000-8000-000000000000",
                                              "ignored_unknown_transaction"),
                                             (None, "ignored_unknown_transaction")],
                         ids=["names-the-account", "names-no-account", "no-token"])
def test_an_unknown_lineage_is_bound_by_its_app_account_token(token, expected):
    """D10 (owner, 2026-10-14): a paid purchase whose verify never landed is entitled from the
    webhook — only through Apple's signed appAccountToken naming an existing account."""
    sb = _lineage()
    s = _service(sb)
    extra = {"appAccountToken": token} if token else {}
    outcome, user = _notify(s, "SUBSCRIBED", _txn(product=_MAX, txn_id="txn-x", **extra),
                            at=datetime.now(timezone.utc))
    assert outcome == expected
    if token == _USER:
        assert user == _USER and sb._tier() == "premium" and sb.remaining == 4000
    else:
        assert sb.db["subscriptions"] == [] and sb._tier() == "free"


def test_a_token_alone_never_puts_a_strangers_lineage_over_a_paying_account(caplog):
    """Review #11 (2026-10-15): the token's VALUE is chosen by the purchasing client. Bound onto
    a paying Pro account, a stranger's Max started over its period (expiring the Pro credits)
    and its refund then clawed everything back. Now a token binds only an account with no
    entitling subscription; the paying account keeps its period, and the stranger's refund is
    recorded under the token's account (never bound: IAP audit F17)."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-v", signed_at=now - timedelta(days=5),
                                             expires_in_days=25))
    sb.credits["used"] = 100
    before = (sb.model.paid_period, sb.remaining)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        outcome, user = _notify(s, "SUBSCRIBED", _txn(product=_MAX, txn_id="txn-x", appAccountToken=_USER),
                                at=now)
    assert (outcome, user) == ("ignored_unknown_transaction", None)
    assert any("NOT bound by the token alone" in r.getMessage() for r in caplog.records)
    outcome, _ = _notify(s, "REFUND", _jws(txn_id="txn-x", signed_at=now, revocationDate=_ms(now),
                                           appAccountToken=_USER), at=now + timedelta(minutes=1))
    assert outcome == "recorded_refund_unknown_lineage"
    assert (sb.model.paid_period, sb.remaining) == before and sb._tier() == "pro"
    assert [r["original_transaction_id"] for r in sb.db["subscriptions"]] == ["txn-v"]


def test_f17_an_unbound_refund_whose_token_names_an_account_is_recorded_under_it(caplog):
    """IAP audit F17 (owner, 2026-10-21: "Purge them at deletion"): the refund of a lineage no
    account is bound to was recorded with owner NULL, so the account's deletion (FK cascade)
    left it behind. It is now owned by the account its token names — still never bound — and
    goes with that account; Rule A still refuses the pre-refund JWS."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-v", signed_at=now - timedelta(days=5),
                                             expires_in_days=25))
    before = (sb.model.paid_period, sb.remaining)
    pre_refund = _jws(txn_id="txn-x", signed_at=now - timedelta(hours=1), appAccountToken=_USER)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        outcome, user = _notify(s, "REFUND", _jws(txn_id="txn-x", signed_at=now, revocationDate=_ms(now),
                                                  appAccountToken=_USER), at=now)
    assert (outcome, user) == ("recorded_refund_unknown_lineage", None)
    assert sb.register[("Production", "txn-x")]["user"] == _USER
    assert any("UNBOUND txn=txn-x" in r.getMessage() and f"owner={_USER}" in r.getMessage()
               for r in caplog.records)
    assert [r["original_transaction_id"] for r in sb.db["subscriptions"]] == ["txn-v"], "not bound"
    assert (sb.model.paid_period, sb.remaining) == before and sb._tier() == "pro"
    out = s.apply_verified_transaction(_USER, dict(pre_refund))
    assert out["refused_by"] == "refunded_replay"


@pytest.mark.parametrize("token", ["deadbeef-0000-4000-8000-000000000000", None],
                         ids=["names-no-account", "no-token"])
def test_f17_an_unbound_refund_with_no_known_account_stays_ownerless(token):
    """The pin against over-reach: a token naming no account (a deleted one) or none keeps the
    owner NULL, as 198's `record_subscription_transaction` decides."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    extra = {"appAccountToken": token} if token else {}
    _notify(s, "REFUND", _jws(txn_id="txn-y", original="txn-y", signed_at=now,
                              revocationDate=_ms(now), **extra), at=now)
    assert sb.register[("Production", "txn-y")]["user"] is None


# ── Review #12 (2026-10-15): an unattributed REFUND racing the purchase's first verify ──────

def test_a_refund_recorded_just_before_the_first_verify_writes_its_row_revokes_it(caplog):
    """Order B: the REFUND (no token, no row yet) is recorded with no owner between the verify's
    register read and its INSERT. The verify's own 'applied' record sees it and revokes."""
    now = datetime.now(timezone.utc)
    sb = _Racy(schema="lineage")
    s = _service(sb)
    sb.concurrent = lambda _sb: _notify(s, "REFUND", _jws(txn_id="txn-r", signed_at=now,
                                                          revocationDate=_ms(now)), at=now)
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-r", signed_at=now - timedelta(hours=1)))
    assert out["status"] == "revoked" and _row_of(sb, "txn-r")["status"] == "revoked"
    assert sb._tier() == "free" and sb.model.paid_period is None
    assert any("was REFUNDED while it was being applied" in r.getMessage() for r in caplog.records)


def test_a_refund_whose_first_lookup_missed_the_new_row_finds_it_after_recording(monkeypatch):
    """Order A: the REFUND's owner lookup ran before the verify's INSERT, its record after it.
    It looks the owner up again after recording, finds the row and revokes it."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-r", signed_at=now - timedelta(hours=1)))
    assert sb._tier() == "premium"
    real, calls = svc.IAPService.user_id_for_transaction, []

    def first_lookup_misses(self, otid):
        calls.append(otid)
        return None if len(calls) == 1 else real(self, otid)

    monkeypatch.setattr(svc.IAPService, "user_id_for_transaction", first_lookup_misses)
    # At that instant no credit period existed either (it is granted after the INSERT).
    monkeypatch.setattr(svc.IAPService, "_lineage_owner", lambda self, otid, txn: None)
    outcome, user = _notify(s, "REFUND", _jws(txn_id="txn-r", signed_at=now, revocationDate=_ms(now)),
                            at=now)
    assert len(calls) == 2 and (outcome, user) == ("applied:REFUND", _USER)
    assert _row_of(sb, "txn-r")["status"] == "revoked" and sb._tier() == "free"
    assert sb.model.paid_period is None


# ── G2′: replays of refunded transactions, bound or not ────────────────────────────────────

@pytest.mark.parametrize("token", [None, _USER], ids=["no-token", "token"])
def test_a_refund_of_a_never_verified_purchase_blocks_its_replay(token):
    """V2/V3 of the review: a first purchase whose verify is blocked, refunded; the saved
    pre-refund JWS is then posted. Before 198 the REFUND found no user and was forgotten."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    extra = {"appAccountToken": token} if token else {}
    saved = _jws(txn_id="txn-v", signed_at=now - timedelta(days=1), **extra)
    outcome, _ = _notify(s, "REFUND", _jws(txn_id="txn-v", signed_at=now, revocationDate=_ms(now),
                                           **extra), at=now)
    assert outcome == ("recorded_refund_unknown_lineage" if token is None else "applied:REFUND")

    out = s.apply_verified_transaction(_USER, saved)
    assert out["was_stale"] is True
    assert sb._tier() == "free" and sb.remaining == 50


def test_a_refund_recorded_before_198_still_blocks_a_replay_after_it():
    """Review F5: a Max refunded (and clawed back) BEFORE 198, its row then re-pointed to a Pro
    on another Apple ID. After 198 there is no row and no register entry for the Max lineage —
    but its clawed-back credit period refuses the replay (Rule A′), and nothing is reinstated."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(schema="legacy")
    s = _service(sb)
    saved = _jws(txn_id="txn-a", signed_at=now - timedelta(days=3), expires_in_days=25)
    s.apply_verified_transaction(_USER, saved)
    _notify(s, "REFUND", _jws(txn_id="txn-a", signed_at=now - timedelta(days=2), expires_in_days=25,
                              revocationDate=_ms(now)), at=now - timedelta(days=2))
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-b", signed_at=now - timedelta(days=1),
                                             expires_in_days=30))
    assert sb._tier() == "pro" and _period(sb, "txn-a", 25)["revoked_at"] is not None

    sb.schema = "lineage"
    s = _service(sb)
    out = s.apply_verified_transaction(_USER, saved)
    assert out["was_stale"] is True and sb._tier() == "pro"
    assert all(r["original_transaction_id"] != "txn-a" for r in sb.db["subscriptions"])
    assert _ledger(sb, "plan_reinstated") == []


@pytest.mark.parametrize("history", ["refunded-before-196", "refund-never-clawed"])
@pytest.mark.parametrize("register", ["empty", "begun"])
@pytest.mark.parametrize("precursor", [False, True], ids=["no-row", "expired-precursor-row"])
def test_a_displaced_lineage_with_no_credit_period_is_still_ordered_against_the_account(
        history, register, precursor):
    """Review #7/#23 (Rule A″): the same replay when NOTHING remembers the refund — before 196
    there was no credit period to mark, and an early renewal refunded before it started was
    never granted. Before 198 the client path compared it with the account's one row (which
    inherited the refund's key on the re-point); it still does, against each of its rows.

    Fix-pass review: pinned with the register BEGUN (production: its first record predates every
    relay — the `client_signed_at < since` branch) as well as empty, and with the lineage's row
    first created by the client relaying an older, EXPIRED JWS of it (Rule A″ used to apply only
    to a lineage with no row, and Rule D then read the replay as a newer period)."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(schema="legacy")
    s = _service(sb)
    if history == "refunded-before-196":
        saved = _jws(txn_id="txn-a", signed_at=now - timedelta(days=3), expires_in_days=25)
        s.apply_verified_transaction(_USER, saved)
        _notify(s, "REFUND", _jws(txn_id="txn-a", signed_at=now - timedelta(days=2), expires_in_days=25,
                                  revocationDate=_ms(now)), at=now - timedelta(days=2))
        sb.periods.clear()                                   # 196 did not exist yet
    else:
        s.apply_verified_transaction(_USER, _jws(txn_id="txn-a", signed_at=now - timedelta(days=29),
                                                 expires_in_days=1))
        saved = _jws(txn_id="txn-a2", original="txn-a", signed_at=now - timedelta(days=3),
                     expires_in_days=31)
        _notify(s, "DID_RENEW", saved, at=now - timedelta(days=3))
        _notify(s, "REFUND", _jws(txn_id="txn-a2", original="txn-a", signed_at=now - timedelta(days=2),
                                  expires_in_days=31, revocationDate=_ms(now)), at=now - timedelta(days=2))
        assert _period(sb, "txn-a", 31) is None, "refunded before it started: never granted"
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-b", signed_at=now - timedelta(days=1),
                                             expires_in_days=30))
    assert sb._tier() == "pro" and [r["original_transaction_id"] for r in sb.db["subscriptions"]] == ["txn-b"]

    sb.schema = "lineage"                                    # the owner applies 198
    if register == "begun":
        _register_began(sb, now - timedelta(hours=1))
    s = _service(sb)
    if register == "begun":
        since = s._register_since()
        assert since is not None and svc._ms_to_dt(saved["signedDate"]) < since
    sb.now = _ANCHOR + timedelta(days=2)                     # the Max/old period still runs
    if precursor:
        out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-a0", original="txn-a",
                                                       signed_at=now - timedelta(days=40),
                                                       expires_in_days=-5))
        assert _row_of(sb, "txn-a")["status"] == "expired"
    out = s.apply_verified_transaction(_USER, saved)
    assert out["was_stale"] is True and sb._tier() == "pro"
    assert all(r["original_transaction_id"] != "txn-a" or r["status"] == "expired"
               for r in sb.db["subscriptions"])
    assert sb.model.paid_period is None or sb.model.paid_period["otid"] == "txn-b"


def test_a_new_apple_id_purchase_signed_after_the_register_began_is_never_stale():
    """The other half of Rule A″: before 198 a new-lineage purchase whose verify landed after a
    later notification about the OLD lineage read as stale (the accepted cost of the
    cross-lineage check). A JWS signed after the register began cannot hide an unrecorded
    refund, so it applies."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    sb.now = now - timedelta(hours=3)                        # the register's first record
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-a", signed_at=now - timedelta(hours=3),
                                             expires_in_days=20))
    _notify(s, "DID_CHANGE_RENEWAL_STATUS", _txn(product=_PRO, txn_id="txn-a", expires_in_days=20),
            at=now - timedelta(hours=1))                     # A's key: an hour ago
    # Another account's refund is recorded LATER (20 min ago): "the register began" is its FIRST
    # record, never its latest (fix-pass review: a desc order passed every test).
    sb.now = now - timedelta(minutes=20)
    _notify(s, "REFUND", _jws(txn_id="txn-zzz", signed_at=now - timedelta(minutes=20),
                              revocationDate=_ms(now)), at=now - timedelta(minutes=20))
    sb.now = None
    s = _service(sb)                                         # nothing cached
    assert s._register_since() == now - timedelta(hours=3)
    out = s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-b",
                                                   signed_at=now - timedelta(hours=2),
                                                   expires_in_days=30))
    assert out["was_stale"] is False and sb._tier() == "premium"
    assert {r["original_transaction_id"] for r in sb.db["subscriptions"]} == {"txn-a", "txn-b"}


def test_a_renewal_refunded_and_rewound_before_198_is_not_a_newer_period_after_it():
    """Review #17: before 198 an early renewal (+31 d) was refunded before it started, then the
    late refund of the period in force (+1 d) was written over the revoked row and REWOUND its
    end to +1 d (the old code allowed that over a non-entitling row). 198's backfill could not
    know the +31 d high-water mark — so it marks the row's mark unknown, and the renewal's saved
    pre-refund JWS stays refused as before 198, rather than "newer" under Rule D."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(schema="legacy")
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    saved = _jws(txn_id="txn-2", original="txn-1", signed_at=now - timedelta(hours=5), expires_in_days=31)
    _notify(s, "DID_RENEW", saved, at=now - timedelta(hours=5))
    _notify(s, "REFUND", _jws(txn_id="txn-2", original="txn-1", signed_at=now - timedelta(hours=4),
                              expires_in_days=31, revocationDate=_ms(now)), at=now - timedelta(hours=4))
    _notify(s, "REFUND", _jws(txn_id="txn-1", signed_at=now - timedelta(hours=3), expires_in_days=1,
                              revocationDate=_ms(now)), at=now - timedelta(hours=3))
    row = _row_of(sb, "txn-1")
    assert row["status"] == "revoked"
    assert svc._parse_iso(row["current_period_end"]) == _ANCHOR + timedelta(days=1), "rewound"
    assert _period(sb, "txn-1", 31) is None, "the renewal was never granted (no Rule A′)"

    sb.schema = "lineage"                                    # the owner applies 198
    assert _row_of(sb, "txn-1")["max_period_end"] == "infinity"
    out = _service(sb).apply_verified_transaction(_USER, saved)
    assert out["was_stale"] is True and sb._tier() == "free"
    assert _period(sb, "txn-1", 31) is None, "no paid period for the refunded renewal"


def test_a_re_pointed_row_starts_its_high_water_mark_again():
    """Review #0: code from before 198 (or the new backend inside its 300 s probe window)
    re-points the account's row to a new lineage. The trigger's mark describes ONE lineage's
    periods, so it restarts from the new lineage's own period end — an inherited later mark
    switched Rule D off for that row."""
    from test_iap_entitlement import _keep_max_period_end
    old = {"original_transaction_id": "L1", "current_period_end": "2027-09-01T00:00:00+00:00",
           "max_period_end": "2027-09-01T00:00:00+00:00"}
    new = {**old, "original_transaction_id": "L2", "current_period_end": "2026-11-15T00:00:00+00:00"}
    _keep_max_period_end(old, new)
    assert new["max_period_end"] == "2026-11-15T00:00:00+00:00"
    same = {**old, "current_period_end": "2026-11-15T00:00:00+00:00"}
    _keep_max_period_end(old, same)
    assert same["max_period_end"] == "2027-09-01T00:00:00+00:00", "same lineage: never lowered"
    unknown = {**old, "max_period_end": "infinity"}
    renewed = {**unknown, "current_period_end": "2028-01-01T00:00:00+00:00"}
    _keep_max_period_end(unknown, renewed)
    assert renewed["max_period_end"] == "infinity", "an unknown mark stays unknown"


# ── G3: a late or duplicate refund of a RECORDED Max within the 7-day bound ────────────────

def _max_refunded_then_pro(sb, now, *, deliver_refund=True):
    """Max (lineage g, +25 d) refunded; the user resubscribes to Pro in the same lineage (+30 d,
    so the Max period ends 5 days before it — inside the 7-day overlap bound)."""
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=now - timedelta(days=4), expires_in_days=25))
    sb.credits["used"] = 200
    if deliver_refund:
        _notify(s, "REFUND", _jws(txn_id="txn-m1", original="txn-g", signed_at=now - timedelta(days=2),
                                  expires_in_days=25, revocationDate=_ms(now)),
                at=now - timedelta(days=2))
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-p1", original="txn-g",
                                             signed_at=now - timedelta(days=1), expires_in_days=30))
    return s


@pytest.mark.parametrize("delivery", ["webhook-retry", "client-relay-signed-later"])
def test_a_duplicate_refund_of_a_recorded_max_keeps_the_pro_resubscriber(delivery):
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _max_refunded_then_pro(sb, now)
    assert sb._tier() == "pro"
    if delivery == "webhook-retry":
        _notify(s, "REFUND", _jws(txn_id="txn-m1", original="txn-g", signed_at=now - timedelta(days=2),
                                  expires_in_days=25, revocationDate=_ms(now)), at=now - timedelta(days=2))
    else:
        s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g", signed_at=now,
                                                 expires_in_days=25, revocationDate=_ms(now)))
    assert sb._tier() == "pro" and _row_of(sb, "txn-g")["tier"] == "pro"
    assert _row_of(sb, "txn-g")["status"] == "active"


def test_the_first_delivery_of_a_refund_that_failed_until_the_resubscribe_keeps_pro():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _max_refunded_then_pro(sb, now, deliver_refund=False)
    _notify(s, "REFUND", _jws(txn_id="txn-m1", original="txn-g", signed_at=now - timedelta(days=2),
                              expires_in_days=25, revocationDate=_ms(now)), at=now - timedelta(days=2))
    assert sb._tier() == "pro" and _row_of(sb, "txn-g")["status"] == "active"
    assert _period(sb, "txn-g", 25)["revoked_at"] is not None, "the Max period is clawed back"
    assert sb.model.paid_period["tier"] == "pro" and sb.remaining == 1200


def test_a_recorded_renewal_refunded_before_it_started_is_an_older_period():
    """Review F3: 'granted' is not 'recorded'. An early renewal recorded on the row but refunded
    before it started was never granted; its late relay must still read as an older period."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-2", expires_in_days=31,
                                 originalTransactionId="txn-1"), at=now - timedelta(hours=3))
    _notify(s, "REFUND", _jws(txn_id="txn-2", original="txn-1", signed_at=now - timedelta(hours=2),
                              expires_in_days=31, revocationDate=_ms(now)), at=now - timedelta(hours=2))
    assert _period(sb, "txn-1", 31) is None, "the renewal never started, so it was never granted"
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-4", original="txn-1",
                                             signed_at=now - timedelta(hours=1), expires_in_days=34))
    assert sb._tier() == "pro"

    s.apply_verified_transaction(_USER, _jws(txn_id="txn-2", original="txn-1", signed_at=now,
                                             expires_in_days=31, revocationDate=_ms(now)))
    assert sb._tier() == "pro" and _row_of(sb, "txn-1")["status"] == "active"


# ── Review #6 (2026-10-15): 'applied' is recorded only once the row write has landed ────────
# Recorded before the write, a refund whose write then failed (503) or missed the fence left
# applied_at set, and Apple's retry of the SAME refund read the never-recorded overlap upgrade as
# recorded: refused as an older period, the refunded user kept Pro (legacy mode ends Free).

class _FailWritesOnce(FakeSupabase):
    """The next `fail_writes` INSERT/UPDATE calls on `subscriptions` raise, as a transient
    PostgREST 520 does; reads are untouched."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fail_writes = 0

    def table(self, name):
        q = super().table(name)
        if name != "subscriptions":
            return q
        original = q.execute

        def execute():
            if q._op in ("insert", "update") and self.fail_writes > 0:
                self.fail_writes -= 1
                raise RuntimeError("subscriptions write failed (520)")
            return original()

        q.execute = execute
        return q


def _pro_renewed_early_lineage(sb, now):
    s = _service(sb)
    s.apply_transaction(_USER, _txn(product=_PRO, txn_id="txn-p1", expires_in_days=1,
                                    originalTransactionId="txn-g"))
    _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-p2", expires_in_days=31,
                                 originalTransactionId="txn-g"), at=now - timedelta(hours=2))
    assert _row_of(sb, "txn-g")["tier"] == "pro"
    return s


def _overlap_refund(now):
    """The REFUND of a Max overlap upgrade (+30 d, inside the Pro renewal's +31 d) that never
    reached the row."""
    return _jws(product=_MAX, txn_id="txn-m", original="txn-g", signed_at=now,
                expires_in_days=30, revocationDate=_ms(now))


def test_a_refund_of_an_unrecorded_upgrade_retried_after_a_failed_write_still_revokes():
    now = datetime.now(timezone.utc)
    sb = _FailWritesOnce(schema="lineage")
    s = _pro_renewed_early_lineage(sb, now)
    sb.fail_writes = 1
    with pytest.raises(svc.IAPError):
        _notify(s, "REFUND", _overlap_refund(now), at=now)
    assert _row_of(sb, "txn-g")["status"] == "active", "nothing landed"
    assert not sb.register[("Production", "txn-m")].get("applied_at"), \
        "a write that failed must not leave the transaction recorded"

    _notify(s, "REFUND", _overlap_refund(now), at=now)                  # Apple's retry
    assert _row_of(sb, "txn-g")["status"] == "revoked" and sb._tier() == "free"
    assert sb.register[("Production", "txn-m")].get("applied_at"), "recorded once it landed"


def test_a_failed_client_relay_of_the_refund_then_its_webhook_still_revokes():
    now = datetime.now(timezone.utc)
    sb = _FailWritesOnce(schema="lineage")
    s = _pro_renewed_early_lineage(sb, now)
    sb.fail_writes = 1
    with pytest.raises(svc.IAPError):
        s.apply_verified_transaction(_USER, _overlap_refund(now))
    _notify(s, "REFUND", _overlap_refund(now), at=now + timedelta(minutes=1))
    assert _row_of(sb, "txn-g")["status"] == "revoked" and sb._tier() == "free"


def test_a_refund_of_an_unrecorded_upgrade_whose_write_misses_the_fence_still_revokes(fence_backoffs):
    now = datetime.now(timezone.utc)
    sb = _Racy(schema="lineage")
    s = _pro_renewed_early_lineage(sb, now)

    def foreign_write(_sb):                     # e.g. the first relay after 198 filling transaction_id
        _row_of(_sb, "txn-g")["updated_at"] = (now + timedelta(seconds=7)).isoformat()

    sb.concurrent = foreign_write
    _notify(s, "REFUND", _overlap_refund(now), at=now)
    assert fence_backoffs == [1]
    assert _row_of(sb, "txn-g")["status"] == "revoked" and sb._tier() == "free"


# ── Review #8 (2026-10-15): a RECORDED higher tier is an older period, entitling or not ──────

def test_a_reversal_of_a_refunded_max_after_a_pro_resubscribe_keeps_the_paid_pro(caplog):
    """Max refunded, Pro resubscribed in the same lineage, then Apple reverses the Max refund.
    Written, the Max (+25 d) replaced the Pro (+30 d) and the account fell to Free at +25 d
    with Pro paid through +30 d. Now the Pro period keeps the row; the reversed Max days are
    restored by hand, and said so loudly."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _max_refunded_then_pro(sb, now)
    assert sb._tier() == "pro"
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _jws(txn_id="txn-m1", original="txn-g", signed_at=now,
                                           expires_in_days=25), at=now)
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("pro", "active")
    assert svc._parse_iso(row["current_period_end"]) == _ANCHOR + timedelta(days=30)
    sb.now = _ANCHOR + timedelta(days=26)
    assert sb._tier() == "pro", "still Pro after the Max period would have ended"
    flagged = [r.getMessage() for r in caplog.records
               if "REFUND_REVERSED tier NOT reinstated" in r.getMessage()]
    assert len(flagged) == 1 and "txn=txn-m1" in flagged[0]

    # A relay of the old Max JWS (no revocation) is refused the same way.
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=now + timedelta(minutes=5), expires_in_days=25))
    assert _row_of(sb, "txn-g")["tier"] == "pro"


@pytest.mark.parametrize("miss", [False, True], ids=["first-try", "after-a-fence-miss"])
def test_an_upgrade_the_row_never_recorded_still_applies(miss, fence_backoffs):
    """The twin: a genuine overlap upgrade (Max ending before the stored Pro renewal) was never
    on the row, so it still applies — also when its first write misses the fence."""
    now = datetime.now(timezone.utc)
    sb = _Racy(schema="lineage")
    s = _pro_renewed_early_lineage(sb, now)
    if miss:
        sb.concurrent = lambda _sb: _row_of(_sb, "txn-g").__setitem__(
            "updated_at", (now + timedelta(seconds=7)).isoformat())
    s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-m", original="txn-g",
                                             signed_at=now, expires_in_days=30))
    assert _row_of(sb, "txn-g")["tier"] == "premium" and sb._tier() == "premium"
    assert fence_backoffs == ([1] if miss else [])


# ── G4: the upgrade carry ──────────────────────────────────────────────────────────────────

def _pro_then_max_upgrade(sb, now):
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-pro", original="txn-g",
                                             signed_at=now - timedelta(days=10), expires_in_days=10))
    sb.credits["used"] = 300
    s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-max", original="txn-g",
                                             signed_at=now - timedelta(hours=1), expires_in_days=30))
    carried = sb.model.paid_period["carried"]
    assert sb.model.paid_period["tier"] == "premium" and carried > 0
    return s, carried


def test_apples_refund_of_the_superseded_pro_takes_the_carry_back_once():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, carried = _pro_then_max_upgrade(sb, now)
    held = sb.remaining
    refund = _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now, expires_in_days=10,
                  revocationDate=_ms(now), isUpgraded=True)
    _notify(s, "REFUND", refund, at=now)
    assert sb._tier() == "premium" and sb.remaining == held - carried
    _notify(s, "REFUND", refund, at=now)                         # Apple redelivers it
    assert sb.remaining == held - carried and len(_ledger(sb, "tier_revoked")) == 1

    reversal = _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now + timedelta(hours=1),
                    expires_in_days=10, isUpgraded=True)
    for _ in range(2):
        _notify(s, "REFUND_REVERSED", reversal, at=now + timedelta(hours=1))
    assert sb.remaining == held, "the reversal gives the carry back, once"
    assert len(_ledger(sb, "plan_reinstated")) == 1


def test_a_relayed_superseded_pro_with_a_revocation_stamp_keeps_the_carry():
    """Reviews F2/F6: StoreKit 1 receipts marked an upgraded transaction cancelled. A client relay
    of the superseded Pro carrying a revocation stamp must never take an upgrade's carry — only
    Apple's REFUND notification does."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, _carried = _pro_then_max_upgrade(sb, now)
    held = sb.remaining
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-pro", original="txn-g",
                                             signed_at=now, expires_in_days=10,
                                             revocationDate=_ms(now), isUpgraded=True))
    assert sb.remaining == held and _ledger(sb, "tier_revoked") == []
    assert not any(n == "revoke_paid_period" for n, _ in sb.rpcs)


def test_the_carry_is_gone_once_the_upgrade_period_has_renewed():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, _carried = _pro_then_max_upgrade(sb, now)
    sb.now = _ANCHOR + timedelta(days=30, seconds=1)
    _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-max2", expires_in_days=60,
                                 originalTransactionId="txn-g"), at=now)
    assert sb.credits == {"total": 4000, "used": 0}
    _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now,
                              expires_in_days=10, revocationDate=_ms(now), isUpgraded=True), at=now)
    assert sb.credits == {"total": 4000, "used": 0}, "the renewal's allocation is never touched"


# ── G5: delivery order ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["notification", "client"])
def test_a_renewal_retried_after_the_running_periods_refund_still_applies(source):
    """probe_a: the early renewal's DID_RENEW (signed T1) fails delivery; the REFUND of the
    running period (signed T2 > T1) revokes the row; the renewal's retry must still apply."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=30))
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    t1, t2 = now - timedelta(hours=3), now - timedelta(hours=1)
    _notify(s, "REFUND", _jws(txn_id="txn-1", signed_at=t2, expires_in_days=1,
                              revocationDate=_ms(t2)), at=t2)
    assert sb._tier() == "free"
    renewal = _jws(txn_id="txn-2", original="txn-1", signed_at=t1, expires_in_days=31)
    if source == "notification":
        _notify(s, "DID_RENEW", renewal, at=t1)
    else:
        s.apply_verified_transaction(_USER, renewal)
    assert sb._tier() == "premium"
    assert svc._parse_iso(_row_of(sb, "txn-1")["current_period_end"]) == _ANCHOR + timedelta(days=31)
    assert sb.model.paid_period["end"] == _ANCHOR + timedelta(days=31) and sb.remaining == 4000


def test_a_stale_renewal_of_a_refunded_later_period_stays_refused():
    """probe_s2: the early renewal P2 was refunded, then the late refund of P1 rewrote the row's
    end back to P1's; P2's stale DID_RENEW retry must not re-entitle, and P1's credits are clawed
    back. Rule A refuses it first (the register holds P2's refund, signed after the retry); the
    trigger-kept max_period_end with the signedDate check is the backstop, so this test alone does
    not pin Rule A (test_rule_a_refuses_at_and_before_the_refunds_signed_date does)."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-2", expires_in_days=31,
                                 originalTransactionId="txn-1"), at=now - timedelta(hours=5))
    _notify(s, "REFUND", _jws(txn_id="txn-2", original="txn-1", signed_at=now - timedelta(hours=3),
                              expires_in_days=31, revocationDate=_ms(now)), at=now - timedelta(hours=3))
    _notify(s, "REFUND", _jws(txn_id="txn-1", signed_at=now - timedelta(hours=2), expires_in_days=1,
                              revocationDate=_ms(now)), at=now - timedelta(hours=2))
    assert _period(sb, "txn-1", 1)["revoked_at"] is not None
    _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-2", expires_in_days=31,
                                 originalTransactionId="txn-1"), at=now - timedelta(hours=5))
    assert sb._tier() == "free" and sb.remaining == 50


def _pro_renewed_early_then_upgraded(sb, now):
    """Review F2: Pro T1 ends tomorrow; its early renewal T2's DID_RENEW fails delivery; the user
    upgrades to Max T3 (ends a few hours BEFORE T2 would have) and that upgrade is recorded."""
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-t1", original="txn-g",
                                             signed_at=now - timedelta(days=29), expires_in_days=1))
    upgrade = _jws(product=_MAX, txn_id="txn-t3", original="txn-g", signed_at=now - timedelta(hours=1),
                   expires_in_days=31)
    upgrade["expiresDate"] -= 4.5 * 3600 * 1000
    _notify(s, "DID_CHANGE_RENEWAL_PREF", upgrade, at=now - timedelta(hours=1), subtype="UPGRADE")
    assert sb._tier() == "premium"
    t2_retry = _txn(product=_PRO, txn_id="txn-t2", expires_in_days=31, originalTransactionId="txn-g")
    return s, upgrade, t2_retry


def test_a_superseded_renewal_retried_after_a_recorded_upgrade_stays_refused():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, _upgrade, t2_retry = _pro_renewed_early_then_upgraded(sb, now)
    _notify(s, "DID_RENEW", t2_retry, at=now - timedelta(hours=2))
    assert sb._tier() == "premium" and _row_of(sb, "txn-g")["tier"] == "premium"


def test_a_superseded_renewal_retried_after_the_upgrade_was_refunded_stays_refused():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, upgrade, t2_retry = _pro_renewed_early_then_upgraded(sb, now)
    _notify(s, "REFUND", {**upgrade, "revocationDate": _ms(now), "signedDate": _ms(now)}, at=now)
    assert sb._tier() == "free"
    _notify(s, "DID_RENEW", t2_retry, at=now - timedelta(hours=2))
    assert sb._tier() == "free", "the Pro renewal the upgrade superseded must not come back"


def test_a_downgrades_next_period_retried_after_a_refund_applies():
    """The downgrade twin: Max M1 refunded, the scheduled Pro renewal (a whole period later, far
    beyond the 7-day bound) retried after it — paid, so it applies."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=30))
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=now - timedelta(days=29), expires_in_days=1))
    _notify(s, "REFUND", _jws(txn_id="txn-m1", original="txn-g", signed_at=now - timedelta(hours=1),
                              expires_in_days=1, revocationDate=_ms(now)), at=now - timedelta(hours=1))
    _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-p2", expires_in_days=31,
                                 originalTransactionId="txn-g"), at=now - timedelta(hours=3))
    assert sb._tier() == "pro"


# ── G6: REFUND_REVERSED, in either arrival order ──────────────────────────────────────────

def _max_then_unrelated_later_key(sb, now, t4):
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    _notify(s, "DID_CHANGE_RENEWAL_STATUS", _jws(signed_at=t4), at=t4, subtype="AUTO_RENEW_DISABLED")
    return s


def test_a_reversal_that_arrives_before_the_refunds_retry_wins():
    """T4 (unrelated), then T3 (REFUND_REVERSED), then T2 (the REFUND's retry): the retry must
    not revoke what Apple already reinstated (D4)."""
    now = datetime.now(timezone.utc)
    t2, t3, t4 = (now - timedelta(hours=h) for h in (3, 2, 1))
    sb = _lineage()
    s = _max_then_unrelated_later_key(sb, now, t4)
    _notify(s, "REFUND_REVERSED", _jws(signed_at=t3), at=t3)
    _notify(s, "REFUND", _jws(signed_at=t2, revocationDate=_ms(t2)), at=t2)
    assert sb._tier() == "premium" and _row_of(sb, "txn-1")["status"] == "active"
    assert sb.remaining == 4000 and _ledger(sb, "tier_revoked") == []


def test_a_second_refund_after_a_reversal_revokes_again():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    for ntype, hours in (("REFUND", 3), ("REFUND_REVERSED", 2)):
        at = now - timedelta(hours=hours)
        extra = {"revocationDate": _ms(at)} if ntype == "REFUND" else {}
        _notify(s, ntype, _jws(signed_at=at, **extra), at=at)
    assert sb._tier() == "premium"
    at = now - timedelta(hours=1)
    _notify(s, "REFUND", _jws(signed_at=at, revocationDate=_ms(at)), at=at)
    assert sb._tier() == "free" and sb.remaining == 50


# ── Review #13 (2026-10-15): "restore by hand" only for the signedDate refusal ─────────────

def _restore_by_hand(caplog):
    return [r for r in caplog.records if "REFUND_REVERSED NOT APPLIED" in r.getMessage()]


def test_a_redelivered_reversal_after_a_second_refund_is_not_flagged(caplog):
    """The second refund (signed after the reversal) left the row revoked; Apple redelivers the
    older reversal. It is refused as stale — correctly: the transaction is refunded again, so
    there is nothing to restore and no ERROR."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    t2, t3, t5 = (now - timedelta(hours=h) for h in (3, 2, 1))
    _notify(s, "REFUND", _jws(signed_at=t2, revocationDate=_ms(t2)), at=t2)
    _notify(s, "REFUND_REVERSED", _jws(signed_at=t3), at=t3)
    _notify(s, "REFUND", _jws(signed_at=t5, revocationDate=_ms(t5)), at=t5)
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        outcome, _ = _notify(s, "REFUND_REVERSED", _jws(signed_at=t3), at=t3)
    assert outcome == "unapplied:refunded_replay:REFUND_REVERSED" and _restore_by_hand(caplog) == []
    assert sb._tier() == "free"


def test_a_reversal_of_a_refund_recorded_before_198_is_still_flagged(caplog):
    """Review #9/#24: gap 6 stays open for a refund applied BEFORE 198 — the register does not
    hold it, so Rule B has nothing to compare with and the later key still wins. It must stay
    LOUD (the runbook's restore by hand), not be silenced by #13's gate."""
    now = datetime.now(timezone.utc)
    t2, t3, t4 = (now - timedelta(hours=h) for h in (3, 2, 1))
    sb = FakeSupabase(schema="legacy")
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    _notify(s, "DID_CHANGE_RENEWAL_STATUS", _jws(signed_at=t4), at=t4, subtype="AUTO_RENEW_DISABLED")
    _notify(s, "REFUND", _jws(signed_at=t2, revocationDate=_ms(t2)), at=t2)
    assert sb._tier() == "free"

    sb.schema = "lineage"                                    # the owner applies 198
    s = _service(sb)
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        outcome, user = _notify(s, "REFUND_REVERSED", _jws(signed_at=t3), at=t3)
    assert (outcome, user) == ("ignored_stale_refund_reversal", _USER)
    assert len(_restore_by_hand(caplog)) == 1 and sb._tier() == "free"


def test_the_reversal_of_a_superseded_pro_over_a_refunded_max_is_not_flagged(caplog):
    """Pro t1 upgraded to Max t2; Apple refunds t1 (the carry is clawed), then t2 (the row is
    revoked), then reverses t1. The carry is accounted for and the row is revoked because of
    t2 — handled, not lost: no "restore by hand"."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, _carried = _pro_then_max_upgrade(sb, now)
    _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=now,
                              expires_in_days=10, revocationDate=_ms(now), isUpgraded=True), at=now)
    t = now + timedelta(minutes=30)
    _notify(s, "REFUND", _jws(product=_MAX, txn_id="txn-max", original="txn-g", signed_at=t,
                              expires_in_days=30, revocationDate=_ms(t)), at=t)
    assert _row_of(sb, "txn-g")["status"] == "revoked"
    t = now + timedelta(hours=1)
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        outcome, _ = _notify(s, "REFUND_REVERSED", _jws(product=_PRO, txn_id="txn-pro", original="txn-g",
                                                         signed_at=t, expires_in_days=10, isUpgraded=True),
                             at=t)
    assert outcome == "unapplied:superseded_transaction:REFUND_REVERSED" and _restore_by_hand(caplog) == []
    assert sb._tier() == "free" and _row_of(sb, "txn-g")["status"] == "revoked"


def test_any_later_unrevoked_delivery_is_recorded_as_reversal_evidence():
    """Review F10: the REFUND_REVERSED is still failing, but a DID_CHANGE_RENEWAL_STATUS of the
    same transaction, signed after the refund and carrying no revocation, already re-entitles the
    row — and must lift the register's refund, or its credits could never be reinstated."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    t2 = now - timedelta(hours=2)
    _notify(s, "REFUND", _jws(signed_at=t2, revocationDate=_ms(t2)), at=t2)
    assert sb._tier() == "free"
    t6 = now - timedelta(hours=1)
    _notify(s, "DID_CHANGE_RENEWAL_STATUS", _jws(signed_at=t6), at=t6, subtype="AUTO_RENEW_ENABLED")
    assert sb._tier() == "premium"
    (rec,) = [r for (_env, txn), r in sb.register.items() if txn == "txn-1"]
    assert rec["rev"] == svc._ms_to_dt(_ms(t2)) and rec["rr"] == svc._ms_to_dt(_ms(t6))
    assert len(_ledger(sb, "plan_reinstated")) == 1


def test_a_reversal_whose_write_misses_the_fence_decides_the_same_way(fence_backoffs):
    """Review F4: the re-run reads the register the first attempt already updated; Rule B is
    decided on the merged row, so it still bypasses the stale check."""
    now = datetime.now(timezone.utc)
    t2, t3, t4 = (now - timedelta(hours=h) for h in (3, 2, 1))
    sb = _Racy(schema="lineage")
    s = _max_then_unrelated_later_key(sb, now, t4)
    _notify(s, "REFUND", _jws(signed_at=t2, revocationDate=_ms(t2)), at=t2)

    def foreign_write(_sb):
        _row_of(_sb, "txn-1")["updated_at"] = (now + timedelta(seconds=7)).isoformat()

    sb.concurrent = foreign_write
    outcome, _ = _notify(s, "REFUND_REVERSED", _jws(signed_at=t3), at=t3)
    assert fence_backoffs == [1] and outcome == "applied:REFUND_REVERSED"
    assert sb._tier() == "premium"


# ── Mode detection and the display row ─────────────────────────────────────────────────────

def test_the_schema_probe_switches_once_198_is_applied(monkeypatch):
    sb = FakeSupabase(schema="legacy")
    s = _service(sb)
    clock = [1000.0]
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock[0])
    assert s._lineage_schema() is False
    sb.schema = "lineage"
    assert s._lineage_schema() is False, "a miss is trusted for the re-probe interval"
    clock[0] += svc._LINEAGE_REPROBE_SECONDS + 1
    assert s._lineage_schema() is True
    sb.schema = "legacy"
    assert s._lineage_schema() is True, "'applied' is kept for good (198 is never rolled back)"


def test_a_transient_probe_failure_is_a_503_not_a_guess():
    sb = FakeSupabase(schema="lineage", fail=("subscription_transactions",))
    with pytest.raises(svc.IAPError):
        _service(sb).apply_verified_transaction(_USER, _jws(signed_at=datetime.now(timezone.utc)))


def test_the_displayed_subscription_is_the_winning_row():
    now = datetime.now(timezone.utc)
    rows = [
        {"tier": "premium", "status": "revoked", "current_period_end": (now + timedelta(days=9)).isoformat(),
         "updated_at": (now - timedelta(minutes=1)).isoformat()},
        {"tier": "pro", "status": "active", "current_period_end": (now + timedelta(days=20)).isoformat(),
         "updated_at": (now - timedelta(days=3)).isoformat()},
        {"tier": "premium", "status": "active", "current_period_end": (now - timedelta(days=1)).isoformat(),
         "updated_at": (now - timedelta(days=1)).isoformat()},
    ]
    assert svc.pick_display_subscription(rows, now)["tier"] == "pro"
    lapsed = [dict(rows[0]), dict(rows[2])]
    assert svc.pick_display_subscription(lapsed, now)["status"] == "revoked", "else the latest write"
    assert svc.pick_display_subscription([], now) is None
    grace = [{"tier": "premium", "status": "grace_period", "current_period_end": None, "updated_at": None}]
    assert svc.pick_display_subscription(grace, now)["status"] == "grace_period"


# ── Rule D's edges, as a pure function ─────────────────────────────────────────────────────

_END = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)


def _prior(status="revoked", tier="premium", end=_END, high=None):
    return {"status": status, "tier": tier, "current_period_end": end.isoformat(),
            "max_period_end": (high or end).isoformat()}


@pytest.mark.parametrize("prior, kw, newer", [
    (_prior(), dict(period_end=_END + timedelta(days=30)), True),
    (_prior(status="expired"), dict(period_end=_END + timedelta(days=30)), True),
    (_prior(status="active"), dict(period_end=_END + timedelta(days=30)), False),
    (_prior(status="grace_period"), dict(period_end=_END + timedelta(days=30)), False),
    (_prior(), dict(period_end=_END + timedelta(seconds=59)), False),
    (_prior(), dict(period_end=_END + timedelta(seconds=60)), True),
    (_prior(high=_END + timedelta(days=30)), dict(period_end=_END + timedelta(days=30)), False),
    (_prior(), dict(period_end=None), False),
    (_prior(), dict(period_end=_END + timedelta(days=30), payload_revoked=True), False),
    (_prior(), dict(period_end=_END + timedelta(days=30), status="expired"), False),
    (_prior(), dict(period_end=_END + timedelta(days=3), tier="pro"), False),
    (_prior(), dict(period_end=_END + timedelta(days=30), tier="pro"), True),
    (_prior(), dict(period_end=_END + timedelta(days=30), tier="pro", environment="Sandbox"), False),
    (None, dict(period_end=_END + timedelta(days=30)), False),
    ({"status": "revoked", "tier": "premium", "current_period_end": None}, dict(period_end=_END), False),
    # Review #17: a mark 198's backfill could not know (the row did not entitle when 198 was
    # applied — its end may have been rewound), or none at all, never takes the exemption.
    ({**_prior(), "max_period_end": "infinity"}, dict(period_end=_END + timedelta(days=30)), False),
    ({**_prior(), "max_period_end": None}, dict(period_end=_END + timedelta(days=30)), False),
    ({k: v for k, v in _prior().items() if k != "max_period_end"},
     dict(period_end=_END + timedelta(days=30)), False),
    # With Apple's purchaseDate (when it charged; review round 2) the lower-tier test reads the
    # charge, not the end distance: superseded when charged more than 48 h before the stored end.
    (_prior(), dict(period_end=_END + timedelta(days=30), tier="pro",
                    purchase_date=_END - timedelta(days=30)), False),
    (_prior(), dict(period_end=_END + timedelta(days=23), tier="pro",
                    purchase_date=_END - timedelta(days=6)), False),
    (_prior(), dict(period_end=_END + timedelta(days=3), tier="pro",
                    purchase_date=_END - timedelta(hours=1)), True),
    (_prior(), dict(period_end=_END + timedelta(days=3), tier="pro",
                    purchase_date=_END - timedelta(hours=48)), True),
    (_prior(), dict(period_end=_END + timedelta(days=3), tier="pro",
                    purchase_date=_END - timedelta(hours=48, seconds=1)), False),
    (_prior(), dict(period_end=_END + timedelta(days=3), tier="pro", environment="Sandbox",
                    purchase_date=_END - timedelta(hours=1)), False),
    (_prior(), dict(period_end=_END + timedelta(days=30), purchase_date=_END - timedelta(days=30)),
     True),
    # A PURCHASE charged at or after the stored transaction's recorded refund is a resubscription,
    # never the superseded renewal (review round 3). Charged BEFORE it (the lower-tier purchase an
    # upgrade superseded), or with no refund on record, or a RENEWAL, keeps the tests (round 4).
    (_prior(), dict(period_end=_END + timedelta(days=20), tier="pro",
                    purchase_date=_END - timedelta(days=10), transaction_reason="PURCHASE",
                    stored_revoked_at=_END - timedelta(days=11)), True),
    (_prior(), dict(period_end=_END + timedelta(days=20), tier="pro",
                    purchase_date=_END - timedelta(days=10), transaction_reason="purchase",
                    stored_revoked_at=_END - timedelta(days=10)), True),
    (_prior(), dict(period_end=_END + timedelta(hours=9), tier="pro",
                    purchase_date=_END - timedelta(days=30, hours=15), transaction_reason="PURCHASE",
                    stored_revoked_at=_END - timedelta(days=25)), False),
    (_prior(), dict(period_end=_END + timedelta(days=20), tier="pro",
                    purchase_date=_END - timedelta(days=10), transaction_reason="PURCHASE"), False),
    (_prior(), dict(period_end=_END + timedelta(days=3), tier="pro", transaction_reason="PURCHASE",
                    stored_revoked_at=_END - timedelta(days=11)), False),
    (_prior(), dict(period_end=_END + timedelta(days=20), tier="pro",
                    purchase_date=_END - timedelta(days=10), transaction_reason="RENEWAL",
                    stored_revoked_at=_END - timedelta(days=11)), False),
], ids=["revoked", "expired", "entitling-prior", "grace-prior", "inside-margin", "at-margin",
        "not-past-the-high-water-mark", "no-end", "revoked-payload", "expired-payload",
        "lower-tier-days-apart", "lower-tier-a-period-later", "sandbox-lower-tier", "no-row",
        "no-stored-end", "unknown-mark", "null-mark", "no-mark",
        "lower-tier-charged-a-period-early", "lower-tier-over-a-1-week-offer",
        "lower-tier-3-day-offer-charged-at-the-end", "lower-tier-charged-48h-early",
        "lower-tier-charged-past-48h", "sandbox-lower-tier-charged-at-the-end",
        "same-tier-charged-early", "purchase-after-the-refund", "purchase-at-the-refund",
        "purchase-before-the-refund-superseded", "purchase-no-refund-on-record",
        "purchase-without-a-purchase-date", "renewal-after-the-refund"])
def test_rule_d_edges(prior, kw, newer):
    args = dict(status="active", payload_revoked=False, tier="premium", environment="Production")
    args.update(kw)
    assert bool(svc._newer_period_reason(prior, **args)) is newer


# ── Review #19 / #22 / #25 (2026-10-15): branches the review found unpinned ───────────────

def test_a_client_relay_of_a_reversed_refund_under_a_later_key_applies():
    """Rule B on the CLIENT path: refund (t2), an unrelated later notification (t4), then the app
    relays the transaction with no revocation, signed t3 — after the refund. It reverses the
    refund (compared with the refund, not with t4)."""
    now = datetime.now(timezone.utc)
    t2, t3, t4 = (now - timedelta(hours=h) for h in (3, 2, 1))
    sb = _lineage()
    s = _max_then_unrelated_later_key(sb, now, t4)
    _notify(s, "REFUND", _jws(signed_at=t2, revocationDate=_ms(t2)), at=t2)
    assert sb._tier() == "free"
    out = s.apply_verified_transaction(_USER, _jws(signed_at=t3))
    assert out["was_stale"] is False and sb._tier() == "premium" and sb.remaining == 4000
    assert len(_ledger(sb, "plan_reinstated")) == 1


def test_an_unbound_refund_then_its_reversal_lets_the_buyer_verify():
    """An unbound REFUND then an unbound REFUND_REVERSED (no account found for either): the
    reversal is recorded AS a reversal, so the buyer's own verify of the original JWS applies."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    saved = _jws(txn_id="txn-u", signed_at=now - timedelta(days=1))
    assert _notify(s, "REFUND", _jws(txn_id="txn-u", signed_at=now - timedelta(hours=2),
                                      revocationDate=_ms(now)), at=now - timedelta(hours=2))[0] \
        == "recorded_refund_unknown_lineage"
    assert _notify(s, "REFUND_REVERSED", _jws(txn_id="txn-u", signed_at=now - timedelta(hours=1)),
                   at=now - timedelta(hours=1))[0] == "recorded_refund_unknown_lineage"
    kinds = [p["p_kind"] for n, p in sb.rpcs if n == "record_subscription_transaction"]
    assert kinds == ["revoked", "reversed"]
    out = s.apply_verified_transaction(_USER, saved)
    assert out["was_stale"] is False and sb._tier() == "premium"


@pytest.mark.parametrize("offset, refused", [(timedelta(0), True), (-timedelta(seconds=1), True),
                                             (timedelta(seconds=1), False)],
                         ids=["at-the-refund", "before-it", "after-it"])
def test_rule_a_refuses_at_and_before_the_refunds_signed_date(offset, refused):
    """Rule A is the only guard here (no row, nothing else to order against): an entitling
    payload signed AT or before the recorded refund is a replay; one signed after it is Apple's
    later word (reversal evidence) and applies."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sb = _lineage()
    s = _service(sb)
    _notify(s, "REFUND", _jws(txn_id="txn-v", signed_at=now, revocationDate=_ms(now)), at=now)
    out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-v", signed_at=now + offset))
    assert out["was_stale"] is refused
    assert sb._tier() == ("free" if refused else "premium")


def test_a_failed_register_write_is_a_503_and_changes_nothing():
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(schema="lineage", fail={"record_subscription_transaction"})
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=1)))   # 'applied' is best-effort
    assert sb._tier() == "premium"
    before = (sb.credits["total"], sb.credits["used"])
    with pytest.raises(svc.IAPError):
        _notify(s, "REFUND", _jws(signed_at=now, revocationDate=_ms(now)), at=now)
    assert _row_of(sb, "txn-1")["status"] == "active" and sb._tier() == "premium"
    assert (sb.credits["total"], sb.credits["used"]) == before


def test_a_failed_keyed_clawback_is_a_503_so_apple_retries():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _early_renewed_max(sb, now)
    sb._fail.add("revoke_paid_period")
    refund = _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                  revocationDate=_ms(now))
    with pytest.raises(svc.IAPError):
        _notify(s, "REFUND", refund, at=now)
    assert _period(sb, "txn-1", 1)["revoked_at"] is None, "nothing was clawed back"
    sb._fail.discard("revoke_paid_period")
    _notify(s, "REFUND", refund, at=now)                                # Apple's retry
    assert _period(sb, "txn-1", 1)["revoked_at"] is not None
    assert sb.credits == {"total": 4000, "used": 0}


def test_each_lineage_row_records_the_transaction_last_applied():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=1)))
    assert _row_of(sb, "txn-1")["transaction_id"] == "txn-1"
    _notify(s, "DID_RENEW", _txn(product=_MAX, txn_id="txn-2", expires_in_days=60,
                                 originalTransactionId="txn-1"), at=now)
    assert _row_of(sb, "txn-1")["transaction_id"] == "txn-2"


def _d6_lines(caplog):
    return [r for r in caplog.records if "credits NOT reinstated — restore by hand" in r.getMessage()]


def _d6_register_check(original: str, period_end: datetime) -> str:
    """The register half of the owner's "still owed?" test every D6 line hands over (final review
    of phase 2) — ensure_credit_period's own REINSTATE condition, as the owner pastes it."""
    return (f"SELECT transaction_id FROM subscription_transactions WHERE original_transaction_id = "
            f"'{original}' AND period_end = '{period_end.isoformat()}' AND revoked_signed_at IS NOT "
            f"NULL AND (reversed_signed_at IS NULL OR reversed_signed_at < revoked_signed_at) "
            f"returns no row (a row = Apple refunded it again: nothing is owed) AND no "
            f"credit_transactions row has ref_id 'd6:{original}:{period_end.isoformat()}' (the one "
            f"hand grant, however many lines name this period)")


def test_d6_is_silent_when_apple_refunded_the_transaction_again(caplog):
    """Review #25 (a): Max refunded, reversed (reinstated), refunded AGAIN, then the reversal is
    redelivered. Rule A refuses it, and the refund stands: nothing to restore, no D6 ERROR."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(signed_at=now - timedelta(days=3)))
    t2, t3, t5 = (now - timedelta(hours=h) for h in (3, 2, 1))
    _notify(s, "REFUND", _jws(signed_at=t2, revocationDate=_ms(t2)), at=t2)
    _notify(s, "REFUND_REVERSED", _jws(signed_at=t3), at=t3)
    _notify(s, "REFUND", _jws(signed_at=t5, revocationDate=_ms(t5)), at=t5)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _jws(signed_at=t3), at=t3)
    assert _d6_lines(caplog) == [] and sb._tier() == "free" and sb.remaining == 50
    assert not [r for r in caplog.records if "NO paid period in force" in r.getMessage()], \
        "not even the warning: the refund stands"


def test_d6_with_no_paid_period_in_force_is_a_warning_not_a_correction(caplog):
    """Review #25 (b): the clawed-back period was not reinstated and NO next period started (the
    balance is on the calendar): a Restore or the next credit read reinstates it once the row
    entitles again, so a hand correction would grant it twice — WARNING, never the ERROR."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=1),
                                             expires_in_days=20))
    _notify(s, "REFUND", _jws(txn_id="txn-1", signed_at=now, expires_in_days=20,
                              revocationDate=_ms(now)), at=now)
    assert sb.model.paid_period is None and _period(sb, "txn-1", 20)["revoked_at"] is not None
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s._warn_unreinstated_reversal(_USER, transaction_id="txn-1", original_txn_id="txn-1",
                                      period_end=_ANCHOR + timedelta(days=20))
    assert _d6_lines(caplog) == []
    assert any("NO paid period in force" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_d6_is_silent_for_a_period_that_has_already_ended(caplog, monkeypatch):
    """The reversed period is over: there is nothing left of it to restore."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _early_renewed_max(sb, now)
    _notify(s, "REFUND", _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                              revocationDate=_ms(now)), at=now)
    assert _period(sb, "txn-1", 1)["revoked_at"] is not None

    class _Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)

    monkeypatch.setattr(svc, "datetime", _Later)
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        s._warn_unreinstated_reversal(_USER, transaction_id="txn-1", original_txn_id="txn-1",
                                      period_end=_ANCHOR + timedelta(days=1))
    assert _d6_lines(caplog) == []


# ── Fix-pass review (2026-10-15, workflow wf_9d421e95-032): the fixes' own regressions ───────

def _pro_early_renewal_then_upgraded(sb, now):
    """Pro T1 ends tomorrow; Apple renews it early (T2, Pro, +31 d) but the DID_RENEW's first
    delivery fails; the user upgrades to Max T3 (ends 4.5 h before T2) and the app's verify lands."""
    s = _service(sb)
    _notify(s, "SUBSCRIBED", _txn(product=_PRO, txn_id="txn-t1", expires_in_days=1,
                                  originalTransactionId="txn-g", signedDate=_ms(now - timedelta(days=29))),
            at=now - timedelta(days=29))
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-t1", original="txn-g",
                                             signed_at=now - timedelta(days=29), expires_in_days=1,
                                             appAccountToken=_USER))
    upgrade = _jws(product=_MAX, txn_id="txn-t3", original="txn-g", signed_at=now - timedelta(hours=1),
                   expires_in_days=31)
    upgrade["expiresDate"] -= int(4.5 * 3600 * 1000)
    s.apply_verified_transaction(_USER, upgrade)
    assert _row_of(sb, "txn-g")["tier"] == "premium"
    return s, upgrade


@pytest.mark.parametrize("heal", ["restore", "upgrade-retry"])
def test_a_never_refunded_upgrade_overwritten_by_a_stale_renewal_still_heals(heal):
    """Fix #8 refused EVERY recorded higher tier as an older period. Once the superseded Pro T2's
    DID_RENEW retry (signed before the upgrade) overwrote the Max row — pre-existing — neither a
    Restore nor Apple's UPGRADE retry could put Max back: the user paid for Max and sat on Pro
    for a month. Rule E's entitling half now needs a refund on record (#8's actual case)."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, upgrade = _pro_early_renewal_then_upgraded(sb, now)
    _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-t2", expires_in_days=31,
                                 originalTransactionId="txn-g", signedDate=_ms(now - timedelta(hours=3))),
            at=now - timedelta(hours=3))
    assert _row_of(sb, "txn-g")["tier"] == "pro", "the pre-existing overwrite"
    if heal == "restore":
        fresh = dict(upgrade, signedDate=_ms(now))
        s.apply_verified_transaction(_USER, fresh)
    else:
        _notify(s, "DID_CHANGE_RENEWAL_PREF", upgrade, at=now - timedelta(minutes=50), subtype="UPGRADE")
    assert _row_of(sb, "txn-g")["tier"] == "premium" and sb._tier() == "premium"


def test_the_tier_line_needs_a_higher_reversed_tier(caplog):
    """The tier ERROR fired for EVERY refused older-period reversal: the D6 same-tier case
    (early-renewed Max) and a lower tier under a Max. Only a higher reversed tier loses days."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _early_renewed_max(sb, now)
    _notify(s, "REFUND", _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                              revocationDate=_ms(now)), at=now)
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _jws(txn_id="txn-1", original="txn-1",
                                           signed_at=now + timedelta(hours=1), expires_in_days=1),
                at=now + timedelta(hours=1))
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "credits NOT reinstated" in errors[0], errors

    caplog.clear()
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-p1", original="txn-g",
                                             signed_at=now - timedelta(days=4), expires_in_days=25))
    _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-p1", original="txn-g",
                              signed_at=now - timedelta(days=2), expires_in_days=25,
                              revocationDate=_ms(now)), at=now - timedelta(days=2))
    s.apply_verified_transaction(_USER, _jws(product=_MAX, txn_id="txn-m1", original="txn-g",
                                             signed_at=now - timedelta(days=1), expires_in_days=30))
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _jws(product=_PRO, txn_id="txn-p1", original="txn-g",
                                           signed_at=now, expires_in_days=25), at=now)
    assert not [r for r in caplog.records if "tier NOT reinstated" in r.getMessage()]
    assert sb._tier() == "premium"


def test_a_relay_of_a_refunded_transaction_its_row_still_entitles_from_revokes_the_row(caplog):
    """#12 was one-shot: when the race check failed once, the row stayed entitled under a
    refunded transaction and Rule A refused every later relay WITHOUT revoking — and the refused
    relay's own reconcile could grant the refunded period. Rule A's refusal now heals it."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    saved = _jws(txn_id="txn-r", signed_at=now - timedelta(hours=1))
    s.apply_verified_transaction(_USER, saved)
    assert _row_of(sb, "txn-r")["transaction_id"] == "txn-r"
    sb.register[("Production", "txn-r")]["rev"] = now - timedelta(minutes=30)   # the lost race
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        out = s.apply_verified_transaction(_USER, saved)
    assert out["status"] == "revoked" and _row_of(sb, "txn-r")["status"] == "revoked"
    assert sb._tier() == "free" and sb.model.paid_period is None
    assert any("still entitles its row — revoking" in r.getMessage() for r in caplog.records)


class _FailAppliedRecord(_Racy):
    """`_Racy`, and the 'applied' register record fails `fails` times (the other kinds work)."""

    def __init__(self, *a, fails=1, **k):
        super().__init__(*a, **k)
        self.fails = fails

    def rpc(self, name, params):
        if (name == "record_subscription_transaction" and params.get("p_kind") == "applied"
                and self.fails > 0):
            self.fails -= 1
            raise RuntimeError("rpc record_subscription_transaction unavailable (520)")
        return super().rpc(name, params)


def test_the_race_check_survives_a_failed_applied_record():
    """Order B of #12 with the verify's best-effort 'applied' record failing: the check falls back
    to a register read, so the refund that raced the first verify still revokes the row."""
    now = datetime.now(timezone.utc)
    sb = _FailAppliedRecord(schema="lineage")
    s = _service(sb)
    sb.concurrent = lambda _sb: _notify(s, "REFUND", _jws(txn_id="txn-r", signed_at=now,
                                                          revocationDate=_ms(now)), at=now)
    out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-r", signed_at=now - timedelta(hours=1)))
    assert out["status"] == "revoked" and sb._tier() == "free"


@pytest.mark.parametrize("signed, revoked", [(timedelta(seconds=0), True), (timedelta(seconds=1), False)],
                         ids=["signed-at-the-refund", "signed-after-it"])
def test_the_race_checks_boundary(signed, revoked):
    """The race check is Rule A's: a JWS signed AT the refund is a replay (revoked, keyed on the
    refund); one signed after it is Apple's later word and stays."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sb = _Racy(schema="lineage")
    s = _service(sb)
    sb.concurrent = lambda _sb: _notify(s, "REFUND", _jws(txn_id="txn-r", signed_at=now,
                                                          revocationDate=_ms(now)), at=now)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-r", signed_at=now + signed))
    row = _row_of(sb, "txn-r")
    assert (row["status"] == "revoked") is revoked
    if revoked:
        assert svc._parse_iso(row["last_event_at"]) == now


@pytest.mark.parametrize("source", ["client", "notification"])
def test_rule_d_trusts_only_deliveries_signed_after_the_register_began(source):
    """Fix-pass review of #17: a KNOWN mark can still miss pre-198 history — here a resubscribe
    on the same lineage moved the row's end BACK before 198, so the backfilled mark (+30 d) is
    below the refunded early renewal's (+31 d). That renewal's JWS was signed before the register
    began, so Rule D does not apply to it and the signedDate check refuses it. On the client path
    Rule A″ refuses it first; Apple's own late DID_RENEW retry (a webhook) only the cut stops."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(schema="legacy")
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    saved = _jws(txn_id="txn-2", original="txn-1", signed_at=now - timedelta(hours=5), expires_in_days=31)
    _notify(s, "DID_RENEW", saved, at=now - timedelta(hours=5))
    _notify(s, "REFUND", _jws(txn_id="txn-2", original="txn-1", signed_at=now - timedelta(hours=4),
                              expires_in_days=31, revocationDate=_ms(now)), at=now - timedelta(hours=4))
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-3", original="txn-1",
                                             signed_at=now - timedelta(hours=3), expires_in_days=30))
    assert _row_of(sb, "txn-1")["status"] == "active"

    sb.schema = "lineage"
    assert _row_of(sb, "txn-1")["max_period_end"] == _row_of(sb, "txn-1")["current_period_end"]
    _register_began(sb, now - timedelta(hours=2))
    s = _service(sb)
    _notify(s, "REFUND", _jws(txn_id="txn-3", original="txn-1", signed_at=now - timedelta(hours=1),
                              expires_in_days=30, revocationDate=_ms(now)), at=now - timedelta(hours=1))
    assert _row_of(sb, "txn-1")["status"] == "revoked"
    if source == "client":
        out = s.apply_verified_transaction(_USER, saved)
        assert out["was_stale"] is True
    else:
        _notify(s, "DID_RENEW", saved, at=now - timedelta(hours=5))
    assert _row_of(sb, "txn-1")["status"] == "revoked" and sb._tier() == "free"
    assert _period(sb, "txn-1", 31) is None


@pytest.mark.parametrize("order", ["refund-reversal-refund", "refund-refund-late-reversal"])
def test_the_upgrade_carry_follows_the_latest_word_on_its_period(order):
    """Apple refunds the superseded Pro (the carry is clawed back), reverses it, and refunds it
    AGAIN. Before: the reinstatement left carry_revoked_at set, so the second refund never clawed
    again — and a reversal arriving after the second refund gave the carry back anyway."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, carried = _pro_then_max_upgrade(sb, now)
    clawed = sb.remaining - carried
    t1, t2, t3 = now, now + timedelta(hours=1), now + timedelta(hours=2)

    def refund(at):
        _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-pro", original="txn-g", signed_at=at,
                                  expires_in_days=10, revocationDate=_ms(at), isUpgraded=True), at=at)

    def reversal(at):
        _notify(s, "REFUND_REVERSED", _jws(product=_PRO, txn_id="txn-pro", original="txn-g",
                                           signed_at=at, expires_in_days=10, isUpgraded=True), at=at)

    refund(t1)
    assert sb.remaining == clawed
    if order == "refund-reversal-refund":
        reversal(t2)
        assert sb.remaining == clawed + carried
        refund(t3)
    else:
        refund(t3)
        reversal(t2)
    assert sb.remaining == clawed, "Apple's latest word is a refund: the carry stays clawed back"
    reasons = [r["reason"] for r in sb.model.ledger]
    assert reasons.count("plan_reinstated") == reasons.count("tier_revoked") - 1


@pytest.mark.parametrize("lapse", ["expired", "revoked"])
def test_a_token_binds_an_account_whose_only_subscription_has_lapsed(lapse):
    """D10 for the users most likely to need it (fix-pass review of #11): a returning customer
    whose only row has lapsed buys on a new Apple ID and the verify never lands — their own
    token still binds the purchase. Only an ENTITLING row blocks a token-only binding."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(days=40),
                                             expires_in_days=20))
    if lapse == "expired":
        _notify(s, "EXPIRED", _txn(product=_PRO, txn_id="txn-old", expires_in_days=20), at=now - timedelta(days=1))
        _row_of(sb, "txn-old")["status"] = "expired"
    else:
        _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(days=1),
                                  expires_in_days=20, revocationDate=_ms(now)), at=now - timedelta(days=1))
    assert _row_of(sb, "txn-old")["status"] in ("expired", "revoked")
    outcome, user = _notify(s, "SUBSCRIBED", _txn(product=_MAX, txn_id="txn-new", appAccountToken=_USER),
                            at=now)
    assert (outcome, user) == ("applied:SUBSCRIBED", _USER) and sb._tier() == "premium"


@pytest.mark.parametrize("state", ["grace_period", "billing_retry"])
def test_a_token_never_binds_over_a_subscription_in_billing_grace(state):
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(days=5),
                                             expires_in_days=20))
    _row_of(sb, "txn-old")["status"] = state
    outcome, user = _notify(s, "SUBSCRIBED", _txn(product=_MAX, txn_id="txn-new", appAccountToken=_USER),
                            at=now)
    assert (outcome, user) == ("ignored_unknown_transaction", None)


def test_rule_a_double_prime_is_for_client_relays_only():
    """A webhook never takes Rule A″: Apple's notification of a new lineage bound by its token,
    beside a REVOKED row, is Apple's current word (with Rule A″ on webhooks it was refused)."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(days=5),
                                             expires_in_days=20))
    _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(hours=1),
                              expires_in_days=20, revocationDate=_ms(now)), at=now - timedelta(hours=1))
    outcome, _ = _notify(s, "SUBSCRIBED", _txn(product=_MAX, txn_id="txn-new", appAccountToken=_USER),
                         at=now)
    assert outcome == "applied:SUBSCRIBED" and sb._tier() == "premium"


def test_a_client_relay_with_no_signed_date_fails_closed_under_rule_a_double_prime():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(days=5),
                                             expires_in_days=20))
    _notify(s, "REFUND", _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(hours=1),
                              expires_in_days=20, revocationDate=_ms(now)), at=now - timedelta(hours=1))
    out = s.apply_verified_transaction(_USER, _txn(product=_MAX, txn_id="txn-unsigned"))
    assert out["was_stale"] is True and sb._tier() == "free"


@pytest.mark.parametrize("failing", ["subscriptions-by-user", "register-first-record"])
def test_rule_a_double_prime_never_guesses(failing, monkeypatch):
    """A read Rule A″ needs fails: 503 (Apple or the app retries), never an unordered write."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-old", signed_at=now - timedelta(days=5),
                                             expires_in_days=20))
    if failing == "subscriptions-by-user":
        monkeypatch.setattr(svc.IAPService, "_account_rows",
                            lambda self, uid: (_ for _ in ()).throw(svc.IAPError("could not read existing subscription")))
    else:
        sb._fail.add("subscription_transactions")
        monkeypatch.setattr(svc.IAPService, "_read_register", lambda self, env, txn: None)
    with pytest.raises(svc.IAPError):
        s.apply_verified_transaction(_USER, _jws(txn_id="txn-new", signed_at=now - timedelta(hours=1)))
    assert {r["original_transaction_id"] for r in sb.db["subscriptions"]} == {"txn-old"}


def test_the_register_start_is_read_once_per_service():
    now = datetime.now(timezone.utc)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=1))
    s = _service(sb)
    reads = []
    real = sb.table

    def counting(name):
        if name == "subscription_transactions":
            reads.append(name)
        return real(name)

    sb.table = counting
    first = s._register_since()
    second = s._register_since()
    assert first == second == now - timedelta(days=1) and len(reads) == 1


def test_a_sandbox_refund_beside_another_entitling_sandbox_lineage_keeps_its_balance():
    """The Sandbox backstop runs only when the refund leaves the account on Free."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-s1", signed_at=now - timedelta(days=1),
                                             environment="Sandbox"))
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-s2", signed_at=now - timedelta(hours=2),
                                             environment="Sandbox"))
    before = sb.remaining
    _notify(s, "REFUND", _jws(txn_id="txn-s1", signed_at=now, environment="Sandbox",
                              revocationDate=_ms(now)), at=now)
    assert sb._tier() == "premium" and sb.remaining == before and _ledger(sb, "tier_revoked") == []


def test_a_failed_balance_read_on_a_reversal_is_its_own_line_not_d6(caplog):
    """Fix-pass review of #25: the D6 line says the next period started — with the balance
    unread nobody knows that, and a correction with no period in force grants twice."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _early_renewed_max(sb, now)
    _notify(s, "REFUND", _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                              revocationDate=_ms(now)), at=now)
    sb._fail.add("user_credits")
    with caplog.at_level(logging.ERROR, logger="app.services.iap_service"):
        s._warn_unreinstated_reversal(_USER, transaction_id="txn-1", original_txn_id="txn-1",
                                      period_end=_ANCHOR + timedelta(days=1))
    assert _d6_lines(caplog) == []
    [line] = [r.getMessage() for r in caplog.records
              if "could not tell whether another paid period is in force" in r.getMessage()]
    # Final review: it names the period's own row and never sends the owner to a plan_reinstated
    # row (its ref_id is the lineage, so another month's reinstatement reads as this one).
    assert "subscription_credit_periods row (original txn-1" in line, line
    assert "never decide it by a plan_reinstated row" in line, line
    assert "check user_credits.paid_period_id and credit_transactions plan_reinstated" not in line
    assert line.startswith("IAP REFUND_REVERSED: could not tell whether another paid period is in "
                           "force"), "OWNER_TASKS quotes it verbatim"
    assert _d6_register_check("txn-1", _ANCHOR + timedelta(days=1)) in line, line
    # no period in force: a hand grant before the period's end doubles; after it nothing else will
    # (verification of the final fixes: "none = correct nothing" withheld it forever)
    end = (_ANCHOR + timedelta(days=1)).isoformat()
    assert (f"if it names none, correct nothing before {end} (until then a credit read reinstates "
            f"it), and after it send Claude this line: it is owed only if another paid period was "
            f"in force at this line's time") in line, line
    # ...and never a bare grant: with no period in force it is the no-period WARNING's case
    # (nothing owed) — last verification pass, 2026-10-22
    assert "from then on ask Claude for the one-off grant" not in line


# ── IAP audit, 2026-10-17 (phase 1): the composed code's own high findings ───────────────────
#
# A read-only audit of how five sessions' changes compose (.claude/session-state/9edb4eca-…-iap-
# audit.md) found these against the live 56dcb662. Each test is that audit's reproduction, turned
# around to assert the fixed outcome; the legacy twin runs where the shape exists before 198.

# F03 + F04 — the superseded pre-upgrade renewal. Pro P1 ends E1; Apple's EARLY renewal P2 (Pro,
# ends E2) is signed ~24 h before E1; the user upgrades to Max inside that overlap, so Max ends a
# little BEFORE E2. Apple refunds P2 pro rata at the upgrade, yet its saved JWS and its DID_RENEW
# retries still read "active", later than Max's end.

_F03_OTID = "txn-p1"
_F03_S1 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
_F03_E1 = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
_F03_P2_SIGNED = _F03_E1 - timedelta(hours=24)
_F03_E2 = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
_F03_U = _F03_E1 - timedelta(hours=12)
_F03_E_MAX = datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
_F03_GRACE_SIGNED = _F03_E_MAX + timedelta(minutes=5)


def _f03_payload(product, txn_id, expires, signed, **extra):
    return {"transactionId": txn_id, "originalTransactionId": _F03_OTID, "productId": product,
            "bundleId": settings.IAP_BUNDLE_ID, "environment": "Production",
            "expiresDate": _ms(expires), "signedDate": _ms(signed), **extra}


def _f03_row(sb) -> dict:
    rows = [r for r in sb.db["subscriptions"] if r.get("original_transaction_id") == _F03_OTID]
    assert len(rows) == 1, rows
    return rows[0]


def _f03_max_in_grace_after_an_overlap_upgrade(sb, move, monkeypatch, **p2_extra):
    """P1 bought → early renewal P2 applied (and saved by the app) → Max bought inside the
    overlap → 5000 spent → Max's renewal fails into billing grace (balance HELD). P2 carries NO
    `transactionReason`, so only the grace-recovery guard (F03) can refuse its relay. `p2_extra`
    adds fields to P2 (its `purchaseDate`)."""
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 28)
    s = _service(sb)
    move(sb, _F03_S1)
    s.apply_verified_transaction(_USER, _f03_payload(_PRO, "txn-p1", _F03_E1, _F03_S1))

    move(sb, _F03_P2_SIGNED + timedelta(minutes=1))
    p2 = _f03_payload(_PRO, "txn-p2", _F03_E2, _F03_P2_SIGNED, **p2_extra)
    s.apply_notification({"notificationType": "DID_RENEW", "signedDate": _ms(_F03_P2_SIGNED)},
                         dict(p2))
    s.apply_verified_transaction(_USER, dict(p2))
    assert _f03_row(sb)["tier"] == "pro"

    move(sb, _F03_U)
    m1 = _f03_payload(_MAX, "txn-m1", _F03_E_MAX, _F03_U)
    s.apply_verified_transaction(_USER, dict(m1))
    s.apply_notification({"notificationType": "DID_CHANGE_RENEWAL_PREF", "subtype": "UPGRADE",
                          "signedDate": _ms(_F03_U + timedelta(seconds=5))}, dict(m1))
    assert _f03_row(sb)["tier"] == "premium"
    assert (_F03_OTID, _F03_E2) not in sb.periods, "the superseded early renewal was never granted"
    sb.credits["used"] = 5000

    move(sb, _F03_E_MAX + timedelta(hours=1))
    s.apply_notification({"notificationType": "DID_FAIL_TO_RENEW", "subtype": "GRACE_PERIOD",
                          "signedDate": _ms(_F03_GRACE_SIGNED)},
                         {**m1, "signedDate": _ms(_F03_GRACE_SIGNED)})
    row = _f03_row(sb)
    assert (row["status"], row["tier"], sb._tier()) == ("grace_period", "premium", "premium")
    assert sb.model.paid_period["end"] == _F03_E_MAX, "the Max period is HELD"
    return s, p2


@pytest.mark.parametrize("charged", [False, True], ids=["no-purchaseDate", "purchaseDate"])
@pytest.mark.parametrize("schema", ["lineage", "legacy"])
def test_f03_a_saved_superseded_renewal_cannot_replace_a_max_row_in_grace(
        schema, charged, service_clock, monkeypatch):
    """F03: the grace-recovery bypass (lineage only) compared only the period end — not the tier.
    The saved JWS of the superseded Pro renewal skipped the signedDate check, replaced the Max row
    in grace, wrote off the held Max balance and started a fresh Pro allocation for a renewal Apple
    had refunded at the upgrade. Rule D's lower-tier guard now applies to the bypass too — on the
    end distance without a purchaseDate, on when Apple charged it with one (charged a whole Max
    period before the Max end)."""
    sb = FakeSupabase(schema=schema)
    assert sb.lineage is (schema == "lineage")
    extra = {"purchaseDate": _ms(_F03_P2_SIGNED)} if charged else {}
    s, p2 = _f03_max_in_grace_after_an_overlap_upgrade(sb, service_clock, monkeypatch, **extra)
    held = sb.remaining
    assert held == 180

    service_clock(sb, _F03_E_MAX + timedelta(hours=6))
    prior = dict(_f03_row(sb))
    assert svc._grace_recovery_reason(
        prior, status="active", payload_revoked=False, tier="pro", environment="Production",
        original_transaction_id=_F03_OTID, period_end=_F03_E2,
        purchase_date=_F03_P2_SIGNED if charged else None) is None
    out = s.apply_verified_transaction(_USER, dict(p2))

    assert out["refused_by"] == "stale"
    row = _f03_row(sb)
    assert (row["tier"], row["status"]) == ("premium", "grace_period") and sb._tier() == "premium"
    assert sb.remaining == held and sb.model.paid_period["end"] == _F03_E_MAX
    assert (_F03_OTID, _F03_E2) not in sb.periods


def test_the_grace_bypass_is_given_the_payloads_purchase_date(service_clock, monkeypatch):
    """Wiring: the grace bypass decides a lower tier by when Apple charged it (purchaseDate), so
    `apply_transaction` must hand it over — and no transactionReason (review round 4)."""
    sb = FakeSupabase(schema="lineage")
    charged = _F03_P2_SIGNED
    s, p2 = _f03_max_in_grace_after_an_overlap_upgrade(
        sb, service_clock, monkeypatch, purchaseDate=_ms(charged), transactionReason="RENEWAL")
    seen = []
    real = svc._grace_recovery_reason
    monkeypatch.setattr(svc, "_grace_recovery_reason",
                        lambda *a, **k: seen.append(k) or real(*a, **k))
    service_clock(sb, _F03_E_MAX + timedelta(hours=6))
    assert s.apply_verified_transaction(_USER, dict(p2))["refused_by"] == "stale"
    assert seen and seen[-1]["purchase_date"] == charged
    assert "transaction_reason" not in seen[-1]


# Review round 4: the lower-tier purchase an upgrade superseded is a PURCHASE too. Pro bought on
# Mar 30 18:00 renews Apr 30 18:00; the Max upgrade on Mar 31 09:00 is clamped to Apr 30 09:00, so
# the superseded Pro (refunded pro rata at the upgrade) ends 9 h AFTER the Max. Exempting every
# PURCHASE let its saved JWS back in after the Max refund (Rule D) or over the Max in grace.

_MC_OTID = "txn-mc"
_MC_S = datetime(2027, 3, 30, 18, 0, tzinfo=timezone.utc)
_MC_U = datetime(2027, 3, 31, 9, 0, tzinfo=timezone.utc)


def _mc_payload(product, txn, expires, signed, **extra):
    return {"transactionId": txn, "originalTransactionId": _MC_OTID, "productId": product,
            "bundleId": settings.IAP_BUNDLE_ID, "environment": "Production",
            "expiresDate": _ms(expires), "signedDate": _ms(signed), "appAccountToken": _USER,
            **extra}


def _mc_row(sb):
    rows = [r for r in sb.db["subscriptions"] if r.get("original_transaction_id") == _MC_OTID]
    assert len(rows) == 1, rows
    return rows[0]


def _mc_upgraded(sb, move, monkeypatch, *, record_pro):
    e_pro, e_max = svc._one_month_after(_MC_S), svc._one_month_after(_MC_U)
    assert e_pro - e_max == timedelta(hours=9)
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 16)
    _register_began(sb, _MC_S - timedelta(days=30))
    s = _service(sb)
    pro = _mc_payload(_PRO, "txn-mc-p", e_pro, _MC_S, purchaseDate=_ms(_MC_S),
                      transactionReason="PURCHASE")
    move(sb, _MC_S + timedelta(seconds=30))
    if record_pro:
        s.apply_verified_transaction(_USER, dict(pro))
        assert _mc_row(sb)["tier"] == "pro"
    move(sb, _MC_U + timedelta(seconds=30))
    m1 = _mc_payload(_MAX, "txn-mc-m", e_max, _MC_U, purchaseDate=_ms(_MC_U),
                     transactionReason="PURCHASE")
    s.apply_verified_transaction(_USER, dict(m1))
    s.apply_notification({"notificationType": "DID_CHANGE_RENEWAL_PREF", "subtype": "UPGRADE",
                          "signedDate": _ms(_MC_U + timedelta(seconds=5))}, dict(m1))
    assert _mc_row(sb)["tier"] == "premium" and sb._tier() == "premium"
    return s, pro, m1, e_max


@pytest.mark.parametrize("record_pro", [False, True], ids=["unrecorded-pro", "recorded-pro"])
def test_rule_d_refuses_the_superseded_pro_purchase_after_the_max_refund(
        service_clock, monkeypatch, record_pro):
    sb = FakeSupabase(schema="lineage")
    s, pro, m1, _ = _mc_upgraded(sb, service_clock, monkeypatch, record_pro=record_pro)
    t = _MC_U + timedelta(days=5)
    service_clock(sb, t)
    _notify(s, "REFUND", dict(m1, signedDate=_ms(t), revocationDate=_ms(t - timedelta(minutes=5))),
            at=t)
    assert _mc_row(sb)["status"] == "revoked" and sb._tier() == "free"
    service_clock(sb, t + timedelta(hours=1))
    out = s.apply_verified_transaction(_USER, dict(pro))
    assert out["refused_by"] == "stale"
    assert (_mc_row(sb)["tier"], _mc_row(sb)["status"]) == ("premium", "revoked")
    assert sb._tier() == "free" and sb.model.paid_period is None


@pytest.mark.parametrize("record_pro", [False, True], ids=["unrecorded-pro", "recorded-pro"])
def test_the_grace_bypass_refuses_the_superseded_pro_purchase(service_clock, monkeypatch, record_pro):
    sb = FakeSupabase(schema="lineage")
    s, pro, m1, e_max = _mc_upgraded(sb, service_clock, monkeypatch, record_pro=record_pro)
    sb.credits["used"] = 3800
    service_clock(sb, e_max + timedelta(minutes=30))
    grace_signed = e_max + timedelta(minutes=5)
    _notify(s, "DID_FAIL_TO_RENEW", dict(m1, signedDate=_ms(grace_signed)), at=grace_signed,
            subtype="GRACE_PERIOD")
    assert (_mc_row(sb)["status"], sb._tier()) == ("grace_period", "premium")
    held = sb.remaining
    service_clock(sb, e_max + timedelta(hours=2))
    reads = []
    real = s._read_register
    s._read_register = lambda env, txn: reads.append(txn) or real(env, txn)
    out = s.apply_verified_transaction(_USER, dict(pro))
    assert out["refused_by"] == "stale"
    assert (_mc_row(sb)["tier"], _mc_row(sb)["status"]) == ("premium", "grace_period")
    assert sb._tier() == "premium" and sb.remaining == held, "the held Max balance stays"
    # The refund record is read only over a REVOKED row: a read here could only fail (503) a
    # delivery it cannot change.
    assert "txn-mc-m" not in reads


def test_a_failed_read_of_the_refund_record_answers_503_and_writes_nothing(service_clock):
    """The resubscription check reads the refunded transaction's register row; when that read
    fails the delivery raises (Apple redelivers, the app retries) instead of guessing."""
    max_end = _RESUB_NOW + timedelta(days=10)
    sb = _lineage()
    service_clock(sb, _RESUB_NOW)
    _register_began(sb, _RESUB_NOW - timedelta(days=60))
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=max_end - timedelta(days=30),
                                             expires_in_days=None, expiresDate=_ms(max_end),
                                             appAccountToken=_USER))
    _notify(s, "REFUND", _jws(txn_id="txn-m1", original="txn-g",
                              signed_at=_RESUB_NOW - timedelta(hours=1), expires_in_days=None,
                              expiresDate=_ms(max_end), appAccountToken=_USER,
                              revocationDate=_ms(_RESUB_NOW - timedelta(hours=2))),
            at=_RESUB_NOW - timedelta(hours=1))
    before = dict(_row_of(sb, "txn-g"))
    real = s._read_register

    def flaky(environment, transaction_id):
        if transaction_id == "txn-m1":
            raise svc.IAPError("could not read the transaction register")
        return real(environment, transaction_id)

    s._read_register = flaky
    resub = _jws(product=_PRO, txn_id="txn-r1", original="txn-g", signed_at=_RESUB_NOW,
                 expires_in_days=None, expiresDate=_ms(svc._one_month_after(_RESUB_NOW)),
                 transactionReason="PURCHASE", purchaseDate=_ms(_RESUB_NOW), appAccountToken=_USER)
    with pytest.raises(svc.IAPError):
        s.apply_verified_transaction(_USER, dict(resub))
    assert _row_of(sb, "txn-g") == before and sb._tier() == "free"


def _f04_payloads(now, e1, e2, em, *, reason="RENEWAL", charged=False):
    extra = {"transactionReason": reason} if reason else {}
    if charged:
        extra["purchaseDate"] = _ms(e1 - timedelta(hours=24))   # Apple's early-renewal charge
    p1 = _jws(product=_PRO, txn_id="txn-t1", original="txn-g",
              signed_at=now - timedelta(days=29, hours=4), expires_in_days=None,
              expiresDate=_ms(e1), appAccountToken=_USER)
    p2 = _txn(product=_PRO, txn_id="txn-t2", expires_in_days=None, expiresDate=_ms(e2),
              originalTransactionId="txn-g", signedDate=_ms(now - timedelta(hours=3)),
              appAccountToken=_USER, **extra)
    m = _jws(product=_MAX, txn_id="txn-t3", original="txn-g", signed_at=now - timedelta(hours=1),
             expires_in_days=None, expiresDate=_ms(em), appAccountToken=_USER,
             transactionReason="PURCHASE")
    return p1, p2, m


def _f04_history(schema, now, *, reason="RENEWAL", heal_before_refund=False, charged=False):
    """P1 bought; P2's DID_RENEW first delivery fails; the user upgrades to Max inside the overlap
    (the app's verify lands, the UPGRADE webhook fails); Apple's retry of DID_RENEW(P2) lands; then
    Apple REFUNDs the Max. Returns (sb, the P2 retry's result, the refund's result)."""
    e1, e2 = _ANCHOR + timedelta(hours=20), _ANCHOR + timedelta(days=31)
    em = e2 - timedelta(hours=4, minutes=30)
    sb = FakeSupabase(schema=schema)
    if schema == "lineage":
        _register_began(sb, now - timedelta(days=40))
    s = _service(sb)
    p1, p2, m = _f04_payloads(now, e1, e2, em, reason=reason, charged=charged)
    s.apply_verified_transaction(_USER, dict(p1))
    s.apply_notification({"notificationType": "SUBSCRIBED", "signedDate": _ms(
        now - timedelta(days=29, hours=4) + timedelta(minutes=1)), "data": {"status": 1}}, dict(p1))
    sb.credits["used"] = 200
    s.apply_verified_transaction(_USER, dict(m))
    assert _row_of(sb, "txn-g")["tier"] == "premium" and sb._tier() == "premium"

    results = []
    real = s.apply_transaction
    s.apply_transaction = lambda *a, **k: results.append(real(*a, **k)) or results[-1]
    s.apply_notification({"notificationType": "DID_RENEW", "signedDate": _ms(
        now - timedelta(hours=3)), "data": {"status": 1}}, dict(p2))
    retry = results[-1]
    if heal_before_refund:
        s.apply_notification({"notificationType": "DID_CHANGE_RENEWAL_PREF", "subtype": "UPGRADE",
                              "signedDate": _ms(now - timedelta(hours=1))}, dict(m))
    s.apply_notification({"notificationType": "REFUND", "signedDate": _ms(now),
                          "data": {"status": 5}},
                         dict(m, revocationDate=_ms(now), signedDate=_ms(now)))
    return sb, retry, results[-1], (e2, em)


@pytest.mark.parametrize("charged", [False, True], ids=["no-purchaseDate", "purchaseDate"])
def test_f04_the_refund_of_a_recorded_overlap_upgrade_applies(caplog, charged):
    """F04: Apple's retry of the superseded Pro renewal (signed before the upgrade, a later end)
    overwrote the recorded Max; Rule E then refused Apple's REFUND of the Max as an older period,
    and its keyed clawback started the never-granted Pro key: a refunded user kept Pro and got a
    fresh 1200 allocation. A system RENEWAL of a lower tier ending inside the overlap after the
    higher tier's end (or, with a purchaseDate, charged a period before it) is now refused as
    superseded, so the Max stays on the row and its refund is about the period in force."""
    now = datetime.now(timezone.utc)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        sb, retry, refund, (e2, em) = _f04_history("lineage", now, charged=charged)

    assert retry["refused_by"] == "superseded_renewal"
    assert any("superseded" in r.getMessage() and "txn-t2" in r.getMessage()
               for r in caplog.records)
    assert refund["was_stale"] is False
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("premium", "revoked")
    assert svc._parse_iso(row["current_period_end"]) == em
    assert sb._tier() == "free"
    assert sb.model.paid_period is None and sb.remaining == 50
    assert ("txn-g", e2) not in sb.periods, "the superseded renewal's period never started"


def test_f04_legacy_twin_and_the_healed_control_end_free():
    now = datetime.now(timezone.utc)
    sb, _, refund, _ = _f04_history("legacy", now)
    assert refund["was_stale"] is False and _row_of(sb, "txn-g")["status"] == "revoked"
    assert sb._tier() == "free" and sb.model.paid_period is None and sb.remaining == 50

    sb, _, refund, _ = _f04_history("lineage", now, heal_before_refund=True)
    assert refund["was_stale"] is False and _row_of(sb, "txn-g")["status"] == "revoked"
    assert sb._tier() == "free" and sb.model.paid_period is None and sb.remaining == 50


def test_f04_a_renewal_without_a_transaction_reason_keeps_the_old_ordering():
    """No `transactionReason` (a payload this cannot classify) → no refusal: exactly as before,
    so the pre-existing overwrite still happens (SYSTEM_DESIGN_GUIDELINES §9b.12 known gaps)."""
    now = datetime.now(timezone.utc)
    sb, retry, _, _ = _f04_history("lineage", now, reason=None)
    assert retry.get("refused_by") is None


def test_the_superseded_renewal_edges():
    """Pure edges of `_superseded_renewal_reason`: only Apple's own RENEWAL of a LOWER tier of the
    same lineage, ending inside the overlap bound after an entitling higher-tier row, Production."""
    end = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "premium",
           "current_period_end": end.isoformat()}

    def reason(prior=row, *, status="active", revoked=False, tier="pro", env="Production",
               otid="otid", period_end=end + timedelta(hours=12), why="RENEWAL", purchase=None):
        return svc._superseded_renewal_reason(
            prior, status=status, payload_revoked=revoked, tier=tier, environment=env,
            original_transaction_id=otid, period_end=period_end, transaction_reason=why,
            purchase_date=purchase)

    # With Apple's purchaseDate (when it charged; review round 2): superseded when charged more
    # than 48 h before the stored end, whatever the periods' lengths.
    assert reason(purchase=end - timedelta(days=30)) is not None
    assert reason(purchase=end - timedelta(days=6), period_end=end + timedelta(days=23)) is not None, \
        "over a 1-week Max offer upgrade: ends weeks later, still the superseded renewal"
    for charged in (end - timedelta(hours=24), end - timedelta(hours=47), end, end + timedelta(hours=1)):
        assert reason(purchase=charged, period_end=end + timedelta(days=3)) is None, \
            "a downgrade's renewal (a 3-day offer) is charged at the stored end, ≤ 24 h early"
    assert reason(purchase=end - timedelta(hours=49)) is not None
    assert reason(purchase=end - timedelta(days=30), env="Sandbox") is None
    assert reason(purchase=end - timedelta(days=30), period_end=end - timedelta(seconds=60)) is None

    assert reason() is not None and end.isoformat() in reason()
    for status in ("grace_period", "billing_retry"):
        assert reason(prior={**row, "status": status}) is not None, status
    for status in ("expired", "revoked", "", None):
        assert reason(prior={**row, "status": status}) is None, status
    assert reason(status="expired") is None and reason(status="revoked") is None
    assert reason(revoked=True) is None
    # Apple: an upgrade (and any customer purchase) is PURCHASE; no reason → not classified.
    for why in ("PURCHASE", "", None, "renewal "):
        assert reason(why=why) is None, why
    assert reason(why="renewal") is not None, "Apple's enum, compared case-insensitively"
    assert reason(tier="premium") is None and reason(tier="free") is not None
    assert reason(prior={**row, "tier": "pro"}) is None, "same tier"
    assert reason(env="Sandbox") is None, "a Sandbox month lasts minutes: no overlap bound"
    assert reason(otid="another") is None and reason(otid="") is None
    assert reason(prior=None) is None and reason(period_end=None) is None
    # A downgrade renews on the renewal date: its period ends a whole period after the stored one.
    assert reason(period_end=end + timedelta(days=7)) is not None
    assert reason(period_end=end + timedelta(days=7, seconds=1)) is None
    assert reason(period_end=end - timedelta(seconds=59)) is not None, "same period, drift"
    assert reason(period_end=end - timedelta(seconds=60)) is None, "an OLDER period: R1's business"
    assert reason(prior={**row, "current_period_end": "junk"}) is None


def test_a_superseded_renewal_retry_cannot_overwrite_a_recorded_upgrade():
    """The RENEWAL twin of `test_a_never_refunded_upgrade_overwritten_by_a_stale_renewal_still_
    heals`: the overwrite that test sets up is now refused, so there is nothing to heal."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s, _ = _pro_early_renewal_then_upgraded(sb, now)
    out = _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-t2", expires_in_days=31,
                                       originalTransactionId="txn-g",
                                       signedDate=_ms(now - timedelta(hours=3)),
                                       transactionReason="RENEWAL"),
                  at=now - timedelta(hours=3))
    assert out[0] == "unapplied:superseded_renewal:DID_RENEW"
    assert _row_of(sb, "txn-g")["tier"] == "premium" and sb._tier() == "premium"


# F08 — Rule E's refund evidence from BEFORE 198. Max M1 (lineage g) refunded while 198 was not
# applied (production until 2026-10-16): 196 clawed its credit period back, but no register row
# records the refund. The user resubscribed to Pro on the same lineage, then 198 was applied.

_F08_PRO_DAYS = 30


def _f08_refunded_max_then_pro(now, *, max_days, apply_198, cls=None):
    """`apply_198="after"`: the refund and the resubscribe happen in legacy mode, then the owner
    applies 198 (a fresh service). `"before"`: the control, 198 applied from the start."""
    sb = (cls or FakeSupabase)(schema="lineage" if apply_198 == "before" else "legacy")
    s = _service(sb)
    if apply_198 == "before":
        _register_began(sb, now - timedelta(days=5))
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=now - timedelta(days=4),
                                             expires_in_days=max_days))
    assert _period(sb, "txn-g", max_days) is not None
    sb.credits["used"] = 200
    refunded = now - timedelta(days=3)
    _notify(s, "REFUND", _jws(txn_id="txn-m1", original="txn-g", signed_at=refunded,
                              expires_in_days=max_days, revocationDate=_ms(refunded)), at=refunded)
    assert sb._tier() == "free" and _period(sb, "txn-g", max_days)["revoked_at"] is not None
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-p1", original="txn-g",
                                             signed_at=now - timedelta(days=2),
                                             expires_in_days=_F08_PRO_DAYS))
    assert sb._tier() == "pro"
    if apply_198 == "after":
        assert sb.register == {}, "no register before 198: nothing recorded the refund"
        sb.schema = "lineage"
        s = _service(sb)
        _register_began(sb, now - timedelta(days=1))
    return sb, s


@pytest.mark.parametrize("max_days", [25, 10], ids=["max-ends-5d-before-pro", "max-ends-20d-before-pro"])
@pytest.mark.parametrize("apply_198", ["after", "before"], ids=["refund-before-198", "refund-after-198"])
def test_f08_a_reversed_max_refund_never_overwrites_the_pro_resubscription(
        apply_198, max_days, caplog, service_clock):
    """F08: Rule E saw only refunds recorded after 198. The reversal of a Max refunded before 198
    found no refund evidence, so the higher tier counted as an unrecorded upgrade and overwrote
    the paid Pro row — and the account fell to Free when the Max period ended, with Pro paid for
    weeks more. 196's clawback mark on the Max's own credit period now counts as that evidence."""
    now = datetime.now(timezone.utc)
    sb, s = _f08_refunded_max_then_pro(now, max_days=max_days, apply_198=apply_198)
    pro_period = sb.model.paid_period
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        outcome, user = _notify(s, "REFUND_REVERSED", _jws(
            txn_id="txn-m1", original="txn-g", signed_at=now, expires_in_days=max_days), at=now)

    assert (outcome, user) == ("unapplied:older_period:REFUND_REVERSED", _USER)
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("pro", "active")
    assert svc._parse_iso(row["current_period_end"]) == _ANCHOR + timedelta(days=_F08_PRO_DAYS)
    msgs = [r.getMessage() for r in caplog.records]
    assert len([m for m in msgs if "REFUND_REVERSED tier NOT reinstated" in m]) == 1
    assert len([m for m in msgs if "IGNORING a delivery about an OLDER period" in m]) == 1
    assert sb.model.paid_period is pro_period
    # D2 (owner, 2026-10-17, "No, one allocation"): the Pro resubscription keeps the allocation;
    # the Max credits are NOT restored by hand beside it — a WARNING, never the D6 ERROR.
    assert _d6_lines(caplog) == []
    assert len([m for m in msgs if "one allocation (D2)" in m]) == 1

    service_clock(sb, _ANCHOR + timedelta(days=max_days + 1))
    assert s.reconcile_user_tier(_USER) == "pro" and sb._tier() == "pro"


@pytest.mark.parametrize("max_days", [25, 10], ids=["max-ends-5d-before-pro", "max-ends-20d-before-pro"])
@pytest.mark.parametrize("apply_198", ["after", "before"], ids=["refund-before-198", "refund-after-198"])
def test_f08_a_relay_of_the_reversed_max_never_overwrites_the_pro_resubscription(
        apply_198, max_days, service_clock):
    """The client-relay variant: the app relays the Max's post-reversal JWS (no revocation, signed
    after the clawback). With no reversal on record it logs no tier ERROR — Apple's own
    REFUND_REVERSED does — but it must not overwrite the paid Pro either."""
    now = datetime.now(timezone.utc)
    sb, s = _f08_refunded_max_then_pro(now, max_days=max_days, apply_198=apply_198)
    out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                                   signed_at=now + timedelta(minutes=5),
                                                   expires_in_days=max_days))
    assert out["was_stale"] is True and out["refused_by"] == "older_period"
    assert _row_of(sb, "txn-g")["tier"] == "pro"
    service_clock(sb, _ANCHOR + timedelta(days=max_days + 1))
    assert s.reconcile_user_tier(_USER) == "pro"


def test_f08_a_current_upgrade_never_pays_the_clawback_read():
    """Rule E's extra evidence is read only for a higher tier ending BEFORE the stored period (an
    older one): an upgrade bought now, ending later, adds no read beyond Rule A′'s own."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=5))
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(product=_PRO, txn_id="txn-p1", original="txn-g",
                                             signed_at=now - timedelta(days=2), expires_in_days=20))
    reads: list = []
    table = sb.table

    def counting(name):
        if name == "subscription_credit_periods":
            reads.append(name)
        return table(name)

    sb.table = counting
    out = s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                                   signed_at=now, expires_in_days=30))
    assert out["was_stale"] is False and _row_of(sb, "txn-g")["tier"] == "premium"
    assert len(reads) == 1, "Rule A′'s read only"


# F05 + F12 — `_warn_unreinstated_reversal`, the D6 check on a reversed refund.

class _FlakyPeriods(FakeSupabase):
    """The next `fail_next_period_reads` reads of subscription_credit_periods fail once each (a
    PostgREST 520 or timeout), unlike `sb._fail`, which fails every one. Or, counting from
    `period_reads = 0`, only the reads numbered in `fail_period_reads` (1-based) fail."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fail_next_period_reads = 0
        self.fail_period_reads: set = set()
        self.period_reads = 0

    def table(self, name):
        q = super().table(name)
        if name == "subscription_credit_periods":
            owner, inner = self, q.execute

            def execute():
                owner.period_reads += 1
                if owner.fail_next_period_reads > 0:
                    owner.fail_next_period_reads -= 1
                    raise RuntimeError("520 transient: upstream connect error")
                if owner.period_reads in owner.fail_period_reads:
                    raise RuntimeError("520 transient: upstream connect error")
                return inner()

            q.execute = execute
        return q


def _d6_period_refunded_after_its_early_renewal(sb, now):
    s = _early_renewed_max(sb, now)
    _notify(s, "REFUND", _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                              revocationDate=_ms(now)), at=now)
    assert _period(sb, "txn-1", 1)["revoked_remaining"] == 3700
    assert sb.model.paid_period["end"] == _EARLY_RENEWAL_END
    return s


def test_f05_a_failed_read_in_the_d6_check_answers_503_and_the_redelivery_flags_it(caplog):
    """F05: the D6 check swallowed a failed credit-period read and the REFUND_REVERSED answered
    200, so Apple never redelivered it and the hand restore never happened — the only trace was a
    generic lookup line with no user. Now it is its own ERROR and a 503."""
    now = datetime.now(timezone.utc)
    sb = _FlakyPeriods(schema="lineage")
    s = _d6_period_refunded_after_its_early_renewal(sb, now)
    reversal = _jws(txn_id="txn-1", original="txn-1", signed_at=now + timedelta(hours=1),
                    expires_in_days=1)
    sb.fail_next_period_reads = 1
    with caplog.at_level(logging.INFO, logger="app.services.iap_service"):
        with pytest.raises(svc.IAPError):
            _notify(s, "REFUND_REVERSED", dict(reversal), at=now + timedelta(hours=1))
    assert sb.fail_next_period_reads == 0
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    line = [m for lvl, m in msgs if lvl == logging.ERROR and "check could not run" in m]
    assert len(line) == 1 and f"user={_USER}" in line[0] and "txn=txn-1" in line[0]
    # The webhook's own line names the transaction too, not only the user (IAP audit F22).
    failed = [m for _, m in msgs if "failed to apply" in m]
    assert failed and "txn=txn-1" in failed[0] and "original=txn-1" in failed[0]
    assert _d6_lines(caplog) == []
    assert _period(sb, "txn-1", 1)["revoked_at"] is not None and _ledger(sb, "plan_reinstated") == []

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.services.iap_service"):
        assert _notify(s, "REFUND_REVERSED", dict(reversal), at=now + timedelta(hours=1)) == (
            "unapplied:older_period:REFUND_REVERSED", _USER)
    assert len(_d6_lines(caplog)) == 1


def test_f05_a_failed_read_on_a_client_relay_is_a_warning_not_a_failed_restore(caplog):
    """A Restore relaying the transaction after its refund is reversal evidence too; a failed
    diagnostic read there must not fail the user's Restore. Apple's REFUND_REVERSED runs it."""
    now = datetime.now(timezone.utc)
    sb = _FlakyPeriods(schema="lineage")
    s = _d6_period_refunded_after_its_early_renewal(sb, now)
    sb.fail_next_period_reads = 1
    with caplog.at_level(logging.INFO, logger="app.services.iap_service"):
        out = s.apply_verified_transaction(_USER, _jws(
            txn_id="txn-1", original="txn-1", signed_at=now + timedelta(hours=1), expires_in_days=1))
    assert out["refused_by"] == "older_period" and sb.fail_next_period_reads == 0
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(lvl == logging.WARNING and "check skipped on a client relay" in m
               and f"user={_USER}" in m for lvl, m in msgs)
    assert not [m for lvl, m in msgs if "check could not run" in m]
    assert _d6_lines(caplog) == []


def test_d2_a_downgrade_renewal_started_early_keeps_the_d6_error(caplog):
    """D2's WARNING is for a RESUBSCRIPTION that overlaps the reversed period. A Max period whose
    early renewal was a downgrade to Pro is D6's own case — the clawback only started the
    lineage's next period early — so it keeps the 'restore by hand' ERROR."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-1", signed_at=now - timedelta(days=29),
                                             expires_in_days=1))
    sb.credits["used"] = 300
    _notify(s, "DID_RENEW", _txn(product=_PRO, txn_id="txn-2", expires_in_days=None,
                                 expiresDate=_ms(_EARLY_RENEWAL_END),
                                 originalTransactionId="txn-1"), at=now - timedelta(hours=2))
    _notify(s, "REFUND", _jws(txn_id="txn-1", original="txn-1", signed_at=now, expires_in_days=1,
                              revocationDate=_ms(now)), at=now)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _jws(txn_id="txn-1", original="txn-1",
                                           signed_at=now + timedelta(hours=1), expires_in_days=1),
                at=now + timedelta(hours=1))
    msgs = [r.getMessage() for r in caplog.records]
    assert len([m for m in msgs if "REFUND_REVERSED tier NOT reinstated" in m]) == 1
    assert len(_d6_lines(caplog)) == 1
    assert not [m for m in msgs if "one allocation (D2)" in m]


# F12: two Production lineages on one account (D2). B is Pro on Apple ID 2 and renews on `b_day`;
# A is Max on Apple ID 1 and renews on the 20th. A's Oct 20 → Nov 20 period P_A is refunded on
# Oct 25 (B's held-back period P_B starts) and the refund is reversed on Oct 27.

_F12_OTA, _F12_OTB = "otA", "otB"


def _f12_at(m, d, h=12):
    return datetime(2026, m, d, h, 0, tzinfo=timezone.utc)


def _f12_sub(product, otid, txn, *, purchased, signed, expires, **extra):
    return {"transactionId": txn, "originalTransactionId": otid, "productId": product,
            "bundleId": settings.IAP_BUNDLE_ID, "environment": "Production",
            "type": "Auto-Renewable Subscription", "purchaseDate": _ms(purchased),
            "signedDate": _ms(signed), "expiresDate": _ms(expires), "appAccountToken": _USER,
            **extra}


def _f12_note(ntype, signed, *, status):
    return {"notificationType": ntype, "subtype": "", "signedDate": _ms(signed),
            "data": {"status": status}}


def _f12_two_lineages_then_reversed(b_day, move, caplog, *, before_reversal=None):
    """`before_reversal(s)` runs just before the REFUND_REVERSED; when it is given, a raise from
    the reversal is caught and returned as `logged`'s last element (the delivery's 503)."""
    sb = _lineage()
    s = _service(sb)
    _register_began(sb, _f12_at(8, 1))
    e_a1, e_a2 = _f12_at(10, 20), _f12_at(11, 20)
    if b_day < 20:
        b_ends, b_buy = [_f12_at(10, b_day), _f12_at(11, b_day), _f12_at(12, b_day)], _f12_at(9, b_day)
    else:
        b_ends = [_f12_at(9, b_day), _f12_at(10, b_day), _f12_at(11, b_day), _f12_at(12, b_day)]
        b_buy = _f12_at(8, b_day)
    plus = timedelta(minutes=1)

    move(sb, b_buy)
    s.apply_verified_transaction(_USER, _f12_sub(_PRO, _F12_OTB, "b-1", purchased=b_buy,
                                                 signed=b_buy, expires=b_ends[0]))
    move(sb, _f12_at(9, 20))
    s.apply_verified_transaction(_USER, _f12_sub(_MAX, _F12_OTA, "a-1", purchased=_f12_at(9, 20),
                                                 signed=_f12_at(9, 20), expires=e_a1))
    events = sorted([(b_ends[i], "B", i) for i in range(len(b_ends) - 1)
                     if b_ends[i] < _f12_at(10, 25)] + [(e_a1, "A", 1)])
    for t, who, i in events:
        move(sb, t + plus)
        if who == "B":
            s.apply_notification(_f12_note("DID_RENEW", t + plus, status=1), _f12_sub(
                _PRO, _F12_OTB, f"b-{i + 2}", purchased=b_ends[i], signed=t + plus,
                expires=b_ends[i + 1]))
        else:
            s.apply_notification(_f12_note("DID_RENEW", t + plus, status=1), _f12_sub(
                _MAX, _F12_OTA, "a-2", purchased=e_a1, signed=t + plus, expires=e_a2))
    b_held = next(e for e in b_ends if e > _f12_at(10, 25))
    move(sb, _f12_at(10, 23))
    sb.rpc("ensure_credit_period", {"p_user_id": _USER}).execute()
    assert sb.model.spend_credits(1000, reason="report_charge", ref_id="r") == 3000

    move(sb, _f12_at(10, 25))
    s.apply_notification(_f12_note("REFUND", _f12_at(10, 25), status=5), _f12_sub(
        _MAX, _F12_OTA, "a-2", purchased=e_a1, signed=_f12_at(10, 25), expires=e_a2,
        revocationDate=_ms(_f12_at(10, 25)), revocationReason=0))
    assert sb.model.paid_period["otid"] == _F12_OTB, "B's held-back period starts at the refund"

    move(sb, _f12_at(10, 27))
    caplog.clear()
    raised = None
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        if before_reversal is not None:
            before_reversal(s)
        try:
            s.apply_notification(_f12_note("REFUND_REVERSED", _f12_at(10, 27), status=1),
                                 _f12_sub(_MAX, _F12_OTA, "a-2", purchased=e_a1,
                                          signed=_f12_at(10, 27), expires=e_a2))
        except svc.IAPError as e:
            if before_reversal is None:
                raise
            raised = e
    logged = [(r.levelno, r.getMessage()) for r in caplog.records]
    if raised is not None:
        return sb, s, logged + [raised], b_held, e_a2
    assert _row_of(sb, _F12_OTA)["status"] == "active" and sb._tier() == "premium"
    return sb, s, logged, b_held, e_a2


def test_f12_another_lineages_period_ending_first_is_reinstated_by_itself(service_clock, caplog):
    """F12: the D6 ERROR fired although 198 reinstates P_A by itself once P_B ends — and a hand
    correction made per the runbook was then granted a second time. A WARNING now, naming when."""
    sb, s, logged, b_held, e_a2 = _f12_two_lineages_then_reversed(5, service_clock, caplog)
    assert not [m for lvl, m in logged if "credits NOT reinstated" in m]
    auto = [m for lvl, m in logged if lvl == logging.WARNING and "reinstates it by itself" in m]
    assert len(auto) == 1 and "do not correct by hand" in auto[0] and "txn=a-2" in auto[0]
    assert _ledger(sb, "plan_reinstated") == []

    service_clock(sb, b_held + timedelta(minutes=1))
    s.apply_notification(_f12_note("DID_RENEW", b_held + timedelta(minutes=1), status=1), _f12_sub(
        _PRO, _F12_OTB, "b-next", purchased=b_held, signed=b_held + timedelta(minutes=1),
        expires=b_held + timedelta(days=30)))
    sb.rpc("ensure_credit_period", {"p_user_id": _USER}).execute()
    assert [(r["delta"], r["ref_id"]) for r in _ledger(sb, "plan_reinstated")] == [(3000, _F12_OTA)]
    assert (sb.model.paid_period["otid"], sb.model.paid_period["end"]) == (_F12_OTA, e_a2)


def test_f12_an_in_force_period_that_outlasts_the_reversed_one_keeps_the_d6_error(
        service_clock, caplog):
    """The control: B's period ends after A's, so 198 does not reinstate P_A while B runs — the
    ERROR is true, and it now names the period in force."""
    sb, s, logged, b_held, e_a2 = _f12_two_lineages_then_reversed(22, service_clock, caplog)
    d6 = [m for lvl, m in logged if lvl == logging.ERROR and "credits NOT reinstated" in m]
    assert len(d6) == 1 and f"in force: lineage {_F12_OTB}" in d6[0]
    assert not [m for lvl, m in logged if "reinstates it by itself" in m]
    # Review rounds 4-5: a refund of B before A's end still lets 198 reinstate A by itself (the
    # test below), so the one safe time for a hand correction is after A's end — and the
    # same-lineage "purchase made after the refund: grant nothing" check is not offered here.
    assert (f"198 may still reinstate this by itself until {e_a2.isoformat()}" in d6[0]
            and "never after, so correct nothing before then" in d6[0])
    assert "grant nothing" not in d6[0] and "cannot reinstate" not in d6[0]
    assert "mark this period reinstated" not in d6[0], "a hand mark loses the credits (round 5)"
    # Round 6: what is owed is read from THIS period's own row (plan_reinstated names the lineage).
    assert (f"subscription_credit_periods row (original {_F12_OTA}, period_end {e_a2.isoformat()})"
            in d6[0] and "still has revoked_at set AND" in d6[0])
    assert "look for credit_transactions plan_reinstated" not in d6[0]
    # Final review of phase 2: revoked_at alone cannot tell this from a period reinstated by 198 and
    # then refunded AGAIN (the refund re-stamps it) — the line hands over the register check too.
    assert _d6_register_check(_F12_OTA, e_a2) in d6[0], d6[0]


def test_d6_every_line_one_reversal_logs_names_the_same_single_grant(service_clock, caplog):
    """Verification of the final fixes: one reversal logs its D6 line once per delivery — Apple's
    REFUND_REVERSED, then the app's relay (or a Restore) of the same transaction logs it again,
    word for word, and no hand grant changes the period's row or the register. So every line names
    ONE keyed grant (`d6:<original>:<period_end>`) whose existence the owed test checks."""
    sb, s, logged, b_held, e_a2 = _f12_two_lineages_then_reversed(22, service_clock, caplog)
    first = [m for lvl, m in logged if lvl == logging.ERROR and "credits NOT reinstated" in m]
    service_clock(sb, _f12_at(10, 28))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_verified_transaction(_USER, _f12_sub(_MAX, _F12_OTA, "a-2", purchased=_f12_at(10, 20),
                                                     signed=_f12_at(10, 28), expires=e_a2))
    again = [r.getMessage() for r in _d6_lines(caplog)]
    assert len(first) == 1 and len(again) == 1, (first, again)
    key = f"ref_id 'd6:{_F12_OTA}:{e_a2.isoformat()}'"
    assert key in first[0] and key in again[0]
    assert sb.periods[(_F12_OTA, e_a2)]["revoked_at"] is not None, "the relay restored nothing"


def test_d6_a_refund_of_the_other_lineage_reinstates_the_reversed_period_by_itself(
        service_clock, caplog):
    """Why the line says 'correct nothing before' A's end (review rounds 4-5): after the D6 ERROR,
    Apple refunds B's period before A's end, and 198 reinstates A's period by itself — once."""
    sb, s, logged, b_held, e_a2 = _f12_two_lineages_then_reversed(22, service_clock, caplog)
    assert b_held > e_a2, "B's period outlasts A's"
    when = _f12_at(11, 1)
    service_clock(sb, when)
    s.apply_notification(_f12_note("REFUND", when, status=5), _f12_sub(
        _PRO, _F12_OTB, "b-3", purchased=b_held - timedelta(days=31), signed=when, expires=b_held,
        revocationDate=_ms(when), revocationReason=0))
    sb.rpc("ensure_credit_period", {"p_user_id": _USER}).execute()
    assert (sb.model.paid_period["otid"], sb.model.paid_period["end"]) == (_F12_OTA, e_a2)
    assert [r["ref_id"] for r in _ledger(sb, "plan_reinstated")] == [_F12_OTA]


def test_d6_after_the_reversed_periods_end_198_never_reinstates_it(service_clock, caplog):
    """The line's 'never after' (review round 5): once A's own period has ended, a refund of B no
    longer brings A's credits back, so a hand grant after that end cannot be doubled."""
    sb, s, logged, b_held, e_a2 = _f12_two_lineages_then_reversed(22, service_clock, caplog)
    when = e_a2 + timedelta(hours=1)
    service_clock(sb, when)
    s.apply_notification(_f12_note("REFUND", when, status=5), _f12_sub(
        _PRO, _F12_OTB, "b-3", purchased=b_held - timedelta(days=31), signed=when, expires=b_held,
        revocationDate=_ms(when), revocationReason=0))
    sb.rpc("ensure_credit_period", {"p_user_id": _USER}).execute()
    assert _ledger(sb, "plan_reinstated") == []


def test_d6_an_unidentified_period_in_force_says_so(service_clock, caplog):
    """The credit period `paid_period_id` names cannot be found: the ERROR says the period in force
    is unknown and offers neither lineage's guidance (review round 3)."""
    def unread(s):
        s._credit_period_by_id = lambda _id: None
    sb, s, logged, b_held, e_a2 = _f12_two_lineages_then_reversed(22, service_clock, caplog,
                                                                  before_reversal=unread)
    d6 = [m for lvl, m in logged if lvl == logging.ERROR and "credits NOT reinstated" in m]
    assert len(d6) == 1 and "in force: lineage unknown, end unknown" in d6[0]
    assert "The period in force could not be identified" in d6[0], "OWNER_TASKS quotes it verbatim"
    assert "grant nothing" not in d6[0] and "198 cannot" not in d6[0]
    assert "own subscription_credit_periods row" in d6[0], d6[0]
    assert "never decide it by a plan_reinstated row" in d6[0], d6[0]
    assert _d6_register_check(_F12_OTA, e_a2) in d6[0], d6[0]


# F01 — the K02 window (198 applied; a client JWS signed before the register's first record).
# Account U pays for Pro on Apple ID 1 (lineage L1) and buys Max on Apple ID 2 (lineage L2). L2's
# SUBSCRIBED is not bound by its token while L1 entitles (review #11), and once L1 gets a
# later-signed notification Rule A″ refuses L2's delayed verify as stale: a paid purchase applied
# by neither path. The refusal stays (owner, 2026-10-17: "Log + docs only"); it is now an ERROR
# a person can act on, instead of a WARNING that read like routine ordering.

def _f01_pro_l1_then_198(now, cls=None):
    sb = (cls or FakeSupabase)(schema="legacy")
    s = _service(sb)
    bought = now - timedelta(days=10)
    l1 = _jws(product=_PRO, txn_id="L1", signed_at=bought, expires_in_days=20,
              appAccountToken=_USER)
    s.apply_verified_transaction(_USER, dict(l1))
    _notify(s, "SUBSCRIBED", dict(l1), at=bought + timedelta(minutes=1))
    sb.schema = "lineage"
    s = _service(sb)
    assert s._register_since() is None
    return sb, s


def _f01_lineages(sb):
    return sorted(r["original_transaction_id"] for r in sb.db["subscriptions"])


def _f01_cancel_l1(s, at):
    _notify(s, "DID_CHANGE_RENEWAL_STATUS", _jws(product=_PRO, txn_id="L1", signed_at=at,
                                                 expires_in_days=20, appAccountToken=_USER),
            at=at, subtype="AUTO_RENEW_DISABLED")


def test_f01_a_new_lineage_purchase_refused_in_the_k02_window_is_an_error(caplog):
    now = datetime.now(timezone.utc)
    sb, s = _f01_pro_l1_then_198(now)
    t0 = now - timedelta(hours=3)
    l2 = _jws(product=_MAX, txn_id="L2", signed_at=t0, expires_in_days=30, appAccountToken=_USER,
              purchaseDate=_ms(t0))
    sb._fail.add("subscriptions")
    with pytest.raises(svc.IAPError):
        s.apply_verified_transaction(_USER, dict(l2))          # the purchase's own verify: a 503
    sb._fail.discard("subscriptions")
    assert _notify(s, "SUBSCRIBED", dict(l2), at=t0 + timedelta(seconds=30)) == (
        "ignored_unknown_transaction", None)
    _f01_cancel_l1(s, now - timedelta(hours=1))
    assert t0 < s._register_since()

    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        out = s.apply_verified_transaction(_USER, dict(l2))

    assert out["was_stale"] is True and out["refused_by"] == "stale"      # unchanged: refused
    assert _f01_lineages(sb) == ["L1"] and sb._tier() == "pro"
    errors = [r.getMessage() for r in caplog.records
              if r.levelno == logging.ERROR and "IAP PURCHASE NOT APPLIED" in r.getMessage()]
    assert len(errors) == 1
    for needle in (f"user={_USER}", "txn=L2", "original=L2", "lineage L1", t0.isoformat(),
                   "App Store Connect"):
        assert needle in errors[0], needle
    # Review round 2: what a bounded hand remedy needs — the plan bought, its period end, and the
    # row that blocked it.
    l2_end = svc._ms_to_dt(l2["expiresDate"]).isoformat()
    for needle in ("plan=premium", f"expiresDate={l2_end}", "tier=pro", "status=active"):
        assert needle in errors[0], needle


def test_f01_a_refused_relay_of_a_known_lineage_stays_a_warning(caplog):
    """Rule A″ refusing an older JWS of a lineage the account already holds is ordinary ordering:
    no purchase is missing, so no ERROR."""
    now = datetime.now(timezone.utc)
    sb, s = _f01_pro_l1_then_198(now)
    _f01_cancel_l1(s, now - timedelta(hours=1))
    old = _jws(product=_PRO, txn_id="L1-old", original="L1", signed_at=now - timedelta(hours=3),
               expires_in_days=20, appAccountToken=_USER)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        out = s.apply_verified_transaction(_USER, old)
    assert out["refused_by"] == "stale"
    assert any("signed before the transaction register began" in r.getMessage()
               for r in caplog.records)
    assert not [r for r in caplog.records if "PURCHASE NOT APPLIED" in r.getMessage()]


def test_the_auto_reinstatement_edges():
    """Pure edges of `_reinstated_automatically` (F12): only when 198's winner must be the reversed
    lineage's row, still at the reversed period, once the other lineage's period has ended."""
    end = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    period = {"id": 7, "user_id": _USER, "revoked_at": "2026-10-25T12:00:00+00:00"}
    a = {"original_transaction_id": "otA", "status": "active", "tier": "premium",
         "environment": "Production", "current_period_end": end.isoformat()}
    b = {"original_transaction_id": "otB", "status": "active", "tier": "pro",
         "environment": "Production", "current_period_end": "2026-11-05T12:00:00+00:00"}
    s = _service(_lineage())

    def auto(rows=None, *, comp="free", who=_USER, in_force_end=datetime(2026, 11, 5, 12, 0,
                                                                        tzinfo=timezone.utc)):
        s._account_rows = lambda _uid: [dict(r) for r in (rows if rows is not None else [a, b])]
        s.comp_tier = lambda _uid: comp
        return s._reinstated_automatically(
            _USER, period={**period, "user_id": who}, original_txn_id="otA", period_end=end,
            in_force_end=in_force_end)

    assert auto() is True
    assert auto([a, {**b, "tier": "premium"}]) is False, "a tie: the ORDER BY falls to the ends"
    assert auto([a, b, {**b, "original_transaction_id": "otC", "status": "expired",
                        "tier": "premium"}]) is False, "any other row of that tier may come back"
    assert auto(comp="premium") is False and auto(comp="pro") is True
    assert auto(who="someone-else") is False
    for change in ({"status": "grace_period"}, {"environment": "Sandbox"},
                   {"current_period_end": (end + timedelta(days=30)).isoformat()},
                   {"current_period_end": None}):
        assert auto([{**a, **change}, b]) is False, change
    assert auto([b]) is False, "the reversed lineage's row is gone"
    # A credit read must have a real window between the in-force end and Apple's early renewal of
    # this lineage (≤ 24 h ahead): a day at least (review round 2: at 24 h the window was empty).
    assert auto(in_force_end=end - timedelta(hours=48)) is True
    assert auto(in_force_end=end - timedelta(hours=47)) is False
    assert auto(in_force_end=end - timedelta(hours=24)) is False, "a zero-length window"


# ── IAP audit, review round 2 (2026-10-20): what the review of the phase-1 fixes found ───────
#
# D2 (owner: "No, one allocation"; 2026-10-20 "Yes, any tier") is now decided from the periods'
# SHAPE (`iap_service._resubscribed_while_it_ran`): the same lineage's period in force ends after
# the reversed one but before that period's own renewal would (its end + one calendar month), so
# it is a purchase made while the reversed period still ran. The lineage's own renewal ends a month
# or more after it: D6's case, still the ERROR. Delivery order never enters it (both orders below).

def _d2_lines(caplog):
    return [r for r in caplog.records if "one allocation (D2)" in r.getMessage()]


def _tier_lines(caplog):
    return [r for r in caplog.records if "REFUND_REVERSED tier NOT reinstated" in r.getMessage()]


# D2's tests run on a fixed mid-month clock with calendar-month periods, as Apple's are: D2 vs D6
# reads a renewal by its shape (one calendar month after the reversed end), so a day-offset fixture
# would make them depend on the month they run in.
_D2_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def _d2_max(end, *, signed, txn_id="txn-m1", **extra):
    return _jws(txn_id=txn_id, original="txn-g", signed_at=signed, expires_in_days=None,
                expiresDate=_ms(end), **extra)


def _refunded_then_resubscribed(move, *, max_end, product=_PRO, refund_first=True):
    """Max txn-m1 on lineage txn-g (ends `max_end`) is refunded two hours before `_D2_NOW`, and
    `product` is bought again on the same lineage an hour before it (ends one month later). With
    `refund_first=False` the REFUND's first delivery failed: the resubscription is relayed first,
    and Apple's retry of the refund lands after it (review round 2)."""
    sb = _lineage()
    move(sb, _D2_NOW)
    _register_began(sb, _D2_NOW - timedelta(days=40))
    s = _service(sb)
    s.apply_verified_transaction(_USER, _d2_max(max_end, signed=_D2_NOW - timedelta(days=29)))
    sb.credits["used"] = 200
    refunded = _D2_NOW - timedelta(hours=2)
    refund = _d2_max(max_end, signed=refunded, revocationDate=_ms(refunded))
    resub = _jws(product=product, txn_id="txn-r1", original="txn-g",
                 signed_at=_D2_NOW - timedelta(hours=1), expires_in_days=None,
                 expiresDate=_ms(svc._one_month_after(_D2_NOW - timedelta(hours=1))))
    if refund_first:
        _notify(s, "REFUND", refund, at=refunded)
        assert sb._tier() == "free"
        s.apply_verified_transaction(_USER, resub)
    else:
        s.apply_verified_transaction(_USER, resub)
        _notify(s, "REFUND", refund, at=refunded)
    assert sb.periods[("txn-g", max_end)]["revoked_at"] is not None
    return sb, s


def _d2_reversal(s, max_end):
    return _notify(s, "REFUND_REVERSED", _d2_max(max_end, signed=_D2_NOW), at=_D2_NOW)


def test_d2_a_resubscription_in_the_refunded_periods_last_day_keeps_one_allocation(
        service_clock, caplog):
    """Review round 2: D2 tested `period_start < reversed end − 3 days`, so a resubscription in the
    last 3 days of the refunded period fell to the D6 'restore by hand' ERROR — the second
    allocation the owner refused."""
    max_end = _D2_NOW + timedelta(days=1)
    sb, s = _refunded_then_resubscribed(service_clock, max_end=max_end)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _d2_reversal(s, max_end)
    assert _row_of(sb, "txn-g")["tier"] == "pro" and sb._tier() == "pro"
    assert len(_tier_lines(caplog)) == 1
    assert _d6_lines(caplog) == [] and len(_d2_lines(caplog)) == 1


@pytest.mark.parametrize("refund_first", [True, False], ids=["refund-first", "resubscribe-first"])
@pytest.mark.parametrize("product", [_MAX, _PRO], ids=["same-tier", "lower-tier"])
def test_d2_covers_a_resubscription_of_any_tier_in_either_delivery_order(
        product, refund_first, service_clock, caplog):
    """Owner, 2026-10-20 ("Yes, any tier"): a same- or lower-tier resubscription on the lineage
    keeps the one allocation too — never a hand-granted second one — whichever reached us first,
    the refund or the purchase (round 2's review: a purchase relayed before the refund's retry is
    started in the clawback's own transaction, so a delivery-order test read it as the renewal).
    The tier line fires only when the reversed tier ranks above the period in force."""
    max_end = _D2_NOW + timedelta(days=20)
    sb, s = _refunded_then_resubscribed(service_clock, max_end=max_end, product=product,
                                        refund_first=refund_first)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _d2_reversal(s, max_end)
    assert _d6_lines(caplog) == [] and len(_d2_lines(caplog)) == 1
    assert len(_tier_lines(caplog)) == (1 if product == _PRO else 0)


def test_d6_the_subscriptions_own_renewal_delivered_after_the_refund_keeps_the_error(
        service_clock, caplog):
    """The other delivery order (round 2's review): Apple charged Max's renewal early, but its
    DID_RENEW reached us only after the refund was clawed back, so its period was granted after the
    clawback. It is still the subscription's own renewal — one month after the refunded period —
    and D6's 'restore by hand' ERROR, never D2's 'grant nothing'."""
    sb = _lineage()
    service_clock(sb, _D2_NOW)
    _register_began(sb, _D2_NOW - timedelta(days=40))
    s = _service(sb)
    m1_end = _D2_NOW + timedelta(hours=12)
    s.apply_verified_transaction(_USER, _d2_max(m1_end, signed=_D2_NOW - timedelta(days=29)))
    sb.credits["used"] = 300
    refunded = _D2_NOW - timedelta(hours=1)
    _notify(s, "REFUND", _d2_max(m1_end, signed=refunded, revocationDate=_ms(refunded)),
            at=refunded)
    renewal = _d2_max(svc._one_month_after(m1_end), signed=_D2_NOW - timedelta(hours=12),
                      txn_id="txn-m2", purchaseDate=_ms(_D2_NOW - timedelta(hours=12)),
                      transactionReason="RENEWAL")
    _notify(s, "DID_RENEW", renewal, at=_D2_NOW - timedelta(hours=12))
    assert _row_of(sb, "txn-g")["transaction_id"] == "txn-m2"
    assert sb.model.paid_period["end"] == svc._one_month_after(m1_end)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _d2_max(m1_end, signed=_D2_NOW + timedelta(minutes=10)),
                at=_D2_NOW + timedelta(minutes=10))
    assert len(_d6_lines(caplog)) == 1 and _d2_lines(caplog) == []
    # The same lineage: the line asks for the App Store Connect check D2 would have made.
    assert "not a purchase made after the refund" in _d6_lines(caplog)[0].getMessage()
    assert _d6_register_check("txn-g", m1_end) in _d6_lines(caplog)[0].getMessage()


def test_d2_and_the_tier_line_on_a_reversal_refused_as_stale(caplog):
    """Review round 2: a Max refunded before 198 (no register refund, so no Rule B bypass), a Pro
    resubscription, then a Pro notification signed AFTER the reversal lands first: the reversal is
    refused as stale before Rule E. The tier line never fired and D6's ERROR asked for a second
    allocation. Both are decided from the data now, on every refusal path."""
    now = datetime.now(timezone.utc)
    sb, s = _f08_refunded_max_then_pro(now, max_days=25, apply_198="after")
    _notify(s, "DID_CHANGE_RENEWAL_STATUS", _jws(
        product=_PRO, txn_id="txn-p1", original="txn-g", signed_at=now - timedelta(minutes=5),
        expires_in_days=_F08_PRO_DAYS), at=now - timedelta(minutes=5), subtype="AUTO_RENEW_DISABLED")
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _notify(s, "REFUND_REVERSED", _jws(txn_id="txn-m1", original="txn-g",
                                           signed_at=now - timedelta(minutes=10),
                                           expires_in_days=25), at=now - timedelta(minutes=10))
    assert any("IGNORING stale delivery" in r.getMessage() for r in caplog.records)
    assert _row_of(sb, "txn-g")["tier"] == "pro"
    assert len(_tier_lines(caplog)) == 1
    assert _d6_lines(caplog) == [] and len(_d2_lines(caplog)) == 1


def test_the_d2_shape_rule_edges():
    """Pure edges of `_one_month_after` and `_resubscribed_while_it_ran`: a renewal of the reversed
    period ends a calendar month (or more) after it, month-end clamped like Apple's and Postgres'
    monthly step; a purchase made while it ran ends earlier by its lead."""
    utc = timezone.utc
    assert svc._one_month_after(datetime(2027, 1, 31, 9, tzinfo=utc)) == datetime(2027, 2, 28, 9, tzinfo=utc)
    assert svc._one_month_after(datetime(2028, 1, 30, 9, tzinfo=utc)) == datetime(2028, 2, 29, 9, tzinfo=utc)
    assert svc._one_month_after(datetime(2026, 12, 15, 9, tzinfo=utc)) == datetime(2027, 1, 15, 9, tzinfo=utc)
    assert svc._one_month_after(datetime(2027, 3, 31, 9, tzinfo=utc)) == datetime(2027, 4, 30, 9, tzinfo=utc)

    def resub(reversed_end, in_force_end):
        return svc._resubscribed_while_it_ran(in_force_end, reversed_end)

    end = datetime(2027, 1, 31, 9, tzinfo=utc)
    renewal = datetime(2027, 2, 28, 9, tzinfo=utc)              # 28 days: a 30-day month is wrong
    assert resub(end, renewal) is False
    assert resub(end, renewal - timedelta(minutes=59)) is False, "inside the 1 h tolerance"
    assert resub(end, renewal - timedelta(hours=1, seconds=1)) is True
    assert resub(end, datetime(2027, 2, 20, 9, tzinfo=utc)) is True, "bought on Jan 20"
    assert resub(end, end + timedelta(seconds=60)) is False, "the same period, drift"
    assert resub(end, end + timedelta(seconds=61)) is True
    assert resub(end, end - timedelta(days=1)) is False, "not a later period"
    assert resub(end, renewal + timedelta(days=30)) is False, "a renewal, later still"
    assert resub(None, renewal) is False and resub(end, None) is False


def test_f12_a_zero_length_reinstatement_window_keeps_the_d6_error(service_clock, caplog):
    """Review round 2: B's period ending exactly 24 h before A's leaves no time for the credit read
    that would reinstate A before A's own early renewal moves its row, so the WARNING's promise
    could not hold. It is the D6 ERROR there, which promises nothing: 198 MAY still reinstate A
    (B refunded first), so no hand correction before A's end."""
    sb, s, logged, b_held, e_a2 = _f12_two_lineages_then_reversed(19, service_clock, caplog)
    assert e_a2 - b_held == timedelta(hours=24)
    d6 = [m for lvl, m in logged if lvl == logging.ERROR and "credits NOT reinstated" in m]
    assert len(d6) == 1 and f"in force: lineage {_F12_OTB}" in d6[0]
    assert not [m for lvl, m in logged if "reinstates it by itself" in m]
    # Review rounds 3-5: another Apple ID's period is in force — never D2's same-lineage check.
    assert (f"198 may still reinstate this by itself until {e_a2.isoformat()}" in d6[0]
            and "correct nothing before then" in d6[0])
    assert "grant nothing" not in d6[0] and "mark this period reinstated" not in d6[0]


# F05: the second read of the D6 check — the credit period in force — and the reads inside
# `_reinstated_automatically` fail the same way as the first (review round 2: no test ran them).

def test_f05_a_failed_read_of_the_period_in_force_answers_503(caplog):
    now = datetime.now(timezone.utc)
    sb = _FlakyPeriods(schema="lineage")
    s = _d6_period_refunded_after_its_early_renewal(sb, now)
    sb.period_reads, sb.fail_period_reads = 0, {2}          # 1 = the reversed period, 2 = in force
    with caplog.at_level(logging.INFO, logger="app.services.iap_service"):
        with pytest.raises(svc.IAPError):
            _notify(s, "REFUND_REVERSED", _jws(txn_id="txn-1", original="txn-1",
                                               signed_at=now + timedelta(hours=1),
                                               expires_in_days=1), at=now + timedelta(hours=1))
    assert sb.period_reads == 2
    line = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
            and "check could not run: could not read the credit period in force" in r.getMessage()]
    assert len(line) == 1 and f"user={_USER}" in line[0] and "txn=txn-1" in line[0]
    assert _d6_lines(caplog) == []


def test_f05_a_failed_read_of_the_period_in_force_on_a_client_relay_is_a_warning(caplog):
    now = datetime.now(timezone.utc)
    sb = _FlakyPeriods(schema="lineage")
    s = _d6_period_refunded_after_its_early_renewal(sb, now)
    sb.period_reads, sb.fail_period_reads = 0, {2}
    with caplog.at_level(logging.INFO, logger="app.services.iap_service"):
        out = s.apply_verified_transaction(_USER, _jws(
            txn_id="txn-1", original="txn-1", signed_at=now + timedelta(hours=1), expires_in_days=1))
    assert out["refused_by"] == "older_period" and sb.period_reads == 2
    needle = "check skipped on a client relay: could not read the credit period in force"
    assert any(r.levelno == logging.WARNING and needle in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if "check could not run" in r.getMessage()]
    assert _d6_lines(caplog) == []


def test_f05_a_failed_read_inside_the_automatic_reinstatement_check_answers_503(service_clock, caplog):
    """F12's path reads the account's rows and comp floor; a failure there is its own line now
    (it used to say "the credit period in force")."""
    import inspect

    def fail_inside_the_check(s):
        real = s._account_rows

        def rows(uid):
            if any(f.function == "_reinstated_automatically" for f in inspect.stack()):
                raise svc.IAPError("could not read existing subscription")
            return real(uid)
        s._account_rows = rows

    sb, s, logged, _, _ = _f12_two_lineages_then_reversed(
        5, service_clock, caplog, before_reversal=fail_inside_the_check)
    assert isinstance(logged[-1], svc.IAPError)
    line = [m for lvl, m in logged[:-1] if lvl == logging.ERROR and "check could not run" in m]
    assert len(line) == 1 and "the account's subscription rows or its comp floor" in line[0]
    assert not [m for lvl, m in logged[:-1] if "reinstates it by itself" in m]


# F08: the Rule E evidence read fails closed (review round 2: swallowing it reopened F08 with the
# suite green).

def test_f08_a_failed_evidence_read_answers_503_and_writes_nothing():
    now = datetime.now(timezone.utc)
    sb, s = _f08_refunded_max_then_pro(now, max_days=25, apply_198="after", cls=_FlakyPeriods)
    sb.period_reads, sb.fail_period_reads = 0, {1}
    reversal = _jws(txn_id="txn-m1", original="txn-g", signed_at=now, expires_in_days=25)
    with pytest.raises(svc.IAPError):
        _notify(s, "REFUND_REVERSED", dict(reversal), at=now)
    assert sb.period_reads == 1
    assert _row_of(sb, "txn-g")["tier"] == "pro" and sb._tier() == "pro"
    assert sb.register[("Production", "txn-m1")]["applied_at"] is None

    sb.fail_period_reads = set()
    assert _notify(s, "REFUND_REVERSED", dict(reversal), at=now) == ("unapplied:older_period:REFUND_REVERSED", _USER)
    assert _row_of(sb, "txn-g")["tier"] == "pro"


# F01: each "known lineage" guard on its own, and the unreadable credit periods (review round 2).

def _f01_refusal_ready(now, *, cls=None):
    """The K02 shape of test_f01_a_new_lineage_purchase_refused_in_the_k02_window_is_an_error, up
    to the delayed verify: returns (sb, s, l2)."""
    sb, s = _f01_pro_l1_then_198(now, cls=cls)
    t0 = now - timedelta(hours=3)
    l2 = _jws(product=_MAX, txn_id="L2", signed_at=t0, expires_in_days=30, appAccountToken=_USER,
              purchaseDate=_ms(t0))
    _f01_cancel_l1(s, now - timedelta(hours=1))
    assert t0 < s._register_since()
    return sb, s, l2


def _purchase_not_applied(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.ERROR and "IAP PURCHASE NOT APPLIED" in r.getMessage()]


def test_f01_a_lineage_with_a_credit_period_but_no_row_stays_a_warning(caplog):
    now = datetime.now(timezone.utc)
    sb, s, l2 = _f01_refusal_ready(now)
    sb.periods[("L2", _ANCHOR - timedelta(days=60))] = {"id": 990001, "tier": "premium"}
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        assert s.apply_verified_transaction(_USER, dict(l2))["refused_by"] == "stale"
    assert _purchase_not_applied(caplog) == []


def test_f01_a_lineage_with_a_row_but_no_credit_period_stays_a_warning(caplog):
    now = datetime.now(timezone.utc)
    sb, s, l2 = _f01_refusal_ready(now)
    sb.db["subscriptions"].append({
        "id": "row-l2", "user_id": _USER, "original_transaction_id": "L2", "tier": "premium",
        "status": "expired", "store": "app_store", "environment": "Production",
        "current_period_end": (_ANCHOR - timedelta(days=30)).isoformat(),
        "updated_at": (now - timedelta(days=30)).isoformat()})
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        assert s.apply_verified_transaction(_USER, dict(l2))["refused_by"] == "stale"
    assert _purchase_not_applied(caplog) == []


def test_f01_unreadable_credit_periods_still_raise_the_error_and_answer_200(caplog):
    now = datetime.now(timezone.utc)
    sb, s, l2 = _f01_refusal_ready(now, cls=_FlakyPeriods)
    sb.period_reads, sb.fail_period_reads = 0, {2}          # 1 = Rule A′, 2 = the lineage check
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        out = s.apply_verified_transaction(_USER, dict(l2))
    assert out["refused_by"] == "stale" and sb.period_reads == 2
    errors = _purchase_not_applied(caplog)
    assert len(errors) == 1 and "could not be read" in errors[0]


# F04: which period a lower-tier RENEWAL belongs to is read from when Apple charged it (review
# round 2, the two contested findings).

def test_f04_an_offer_code_downgrade_renewal_applies():
    """A Max subscriber redeems a Pro offer code (3 days free): it starts at the renewal, a RENEWAL
    ending days after the Max. The end-distance rule read it as the superseded renewal and kept
    Max; Apple charged it at the Max period's end (12 h early here), so it applies."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=40))
    s = _service(sb)
    e_max = _ANCHOR + timedelta(hours=12)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=now - timedelta(days=29), expires_in_days=None,
                                             expiresDate=_ms(e_max), transactionReason="PURCHASE"))
    assert _row_of(sb, "txn-g")["tier"] == "premium"
    downgrade = _txn(product=_PRO, txn_id="txn-p1", expires_in_days=None,
                     expiresDate=_ms(e_max + timedelta(days=3)), originalTransactionId="txn-g",
                     signedDate=_ms(now), purchaseDate=_ms(e_max - timedelta(hours=12)),
                     transactionReason="RENEWAL", offerType=3, offerPeriod="P3D")
    assert _notify(s, "DID_RENEW", downgrade, at=now)[0] == "applied:DID_RENEW"
    assert _row_of(sb, "txn-g")["tier"] == "pro"


def test_f04_a_superseded_renewal_over_a_one_week_max_offer_upgrade_is_refused():
    """The upgrade inside the early-renewal overlap was a 1-week Max offer: the superseded Pro
    renewal ends weeks after it, past the end-distance bound, and overwrote it. Apple charged that
    renewal before the upgrade even started, so it is refused."""
    now = datetime.now(timezone.utc)
    e1 = _ANCHOR + timedelta(hours=20)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=40))
    s = _service(sb)
    p1 = _jws(product=_PRO, txn_id="txn-t1", original="txn-g", signed_at=now - timedelta(days=29),
              expires_in_days=None, expiresDate=_ms(e1), appAccountToken=_USER)
    s.apply_verified_transaction(_USER, dict(p1))
    _notify(s, "SUBSCRIBED", dict(p1), at=now - timedelta(days=29) + timedelta(minutes=1))
    s.apply_verified_transaction(_USER, _jws(
        txn_id="txn-m1", original="txn-g", signed_at=now - timedelta(hours=1), expires_in_days=None,
        expiresDate=_ms(_ANCHOR + timedelta(days=7)), appAccountToken=_USER,
        transactionReason="PURCHASE", offerType=2, offerPeriod="P1W"))
    assert _row_of(sb, "txn-g")["tier"] == "premium"
    p2 = _txn(product=_PRO, txn_id="txn-t2", expires_in_days=None,
              expiresDate=_ms(e1 + timedelta(days=30)), originalTransactionId="txn-g",
              signedDate=_ms(now - timedelta(hours=3)), purchaseDate=_ms(e1 - timedelta(hours=24)),
              transactionReason="RENEWAL", appAccountToken=_USER)
    _notify(s, "DID_RENEW", p2, at=now - timedelta(hours=3))
    assert _row_of(sb, "txn-g")["tier"] == "premium" and sb._tier() == "premium"


@pytest.mark.parametrize("charged", [True, False], ids=["purchaseDate", "no-purchaseDate"])
def test_rule_d_refuses_a_superseded_renewal_over_a_refunded_one_week_max_offer(charged):
    """The Rule D twin of the test above: the 1-week Max offer upgrade is REFUNDED before Apple
    retries the superseded Pro renewal. The row no longer entitles, so Rule D decides. That renewal
    ends weeks after the Max, past the end-distance bound, so it passed as a newer period: a
    refunded subscriber got Pro back from a renewal Apple prorated away. Apple charged it a whole
    Max period before the Max end, so with purchaseDate it is refused as stale. Without one (a
    payload this cannot classify) the end-distance rule stands, as documented."""
    now = datetime.now(timezone.utc)
    e1 = _ANCHOR + timedelta(hours=20)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=40))
    s = _service(sb)
    p1 = _jws(product=_PRO, txn_id="txn-t1", original="txn-g", signed_at=now - timedelta(days=29),
              expires_in_days=None, expiresDate=_ms(e1), appAccountToken=_USER)
    s.apply_verified_transaction(_USER, dict(p1))
    _notify(s, "SUBSCRIBED", dict(p1), at=now - timedelta(days=29) + timedelta(minutes=1))
    m = _jws(txn_id="txn-m1", original="txn-g", signed_at=now - timedelta(hours=2),
             expires_in_days=None, expiresDate=_ms(_ANCHOR + timedelta(days=7)), appAccountToken=_USER,
             transactionReason="PURCHASE", offerType=2, offerPeriod="P1W")
    s.apply_verified_transaction(_USER, dict(m))
    _notify(s, "REFUND", dict(m, revocationDate=_ms(now - timedelta(hours=1)),
                              signedDate=_ms(now - timedelta(hours=1))),
            at=now - timedelta(hours=1))
    assert _row_of(sb, "txn-g")["status"] == "revoked" and sb._tier() == "free"
    extra = {"purchaseDate": _ms(e1 - timedelta(hours=24))} if charged else {}
    p2 = _txn(product=_PRO, txn_id="txn-t2", expires_in_days=None,
              expiresDate=_ms(e1 + timedelta(days=30)), originalTransactionId="txn-g",
              signedDate=_ms(now - timedelta(hours=3)), transactionReason="RENEWAL",
              appAccountToken=_USER, **extra)
    _notify(s, "DID_RENEW", p2, at=now - timedelta(hours=3))
    row = _row_of(sb, "txn-g")
    if charged:
        assert (row["tier"], row["status"]) == ("premium", "revoked") and sb._tier() == "free"
    else:
        assert (row["tier"], row["status"]) == ("pro", "active"), "the documented residual"


_RESUB_NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("days_left", [10, 25], ids=["20-days-into-the-max", "5-days-into-the-max"])
def test_rule_d_applies_a_lower_tier_resubscription_after_the_refund(service_clock, days_left):
    """A Max is refunded, and the user buys Pro on the same Apple ID (Apple's PURCHASE). StoreKit's
    re-signed copy of the revoked Max is relayed just after the purchase, so the revoked row holds a
    key signed after it and both of the Pro's own deliveries are stale; only Rule D can apply them.
    The purchaseDate rule read a purchase charged days before the Max end as the superseded renewal
    (review round 3, 20 days in), as the end-distance rule already did 5 days in: the paying Pro
    user stayed on Free. A PURCHASE is never a superseded renewal, so it applies."""
    max_end = _RESUB_NOW + timedelta(days=days_left)
    sb = _lineage()
    service_clock(sb, _RESUB_NOW)
    _register_began(sb, _RESUB_NOW - timedelta(days=60))
    s = _service(sb)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=max_end - timedelta(days=30),
                                             expires_in_days=None, expiresDate=_ms(max_end),
                                             appAccountToken=_USER))
    revoked = dict(revocationDate=_ms(_RESUB_NOW - timedelta(hours=2)))
    _notify(s, "REFUND", _jws(txn_id="txn-m1", original="txn-g",
                              signed_at=_RESUB_NOW - timedelta(hours=1), expires_in_days=None,
                              expiresDate=_ms(max_end), appAccountToken=_USER, **revoked),
            at=_RESUB_NOW - timedelta(hours=1))
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=_RESUB_NOW + timedelta(seconds=5),
                                             expires_in_days=None, expiresDate=_ms(max_end),
                                             appAccountToken=_USER, **revoked))
    assert _row_of(sb, "txn-g")["status"] == "revoked" and sb._tier() == "free"
    resub = _jws(product=_PRO, txn_id="txn-r1", original="txn-g", signed_at=_RESUB_NOW,
                 expires_in_days=None, expiresDate=_ms(svc._one_month_after(_RESUB_NOW)),
                 transactionReason="PURCHASE", purchaseDate=_ms(_RESUB_NOW), appAccountToken=_USER)
    out = s.apply_verified_transaction(_USER, dict(resub))
    assert out.get("refused_by") is None
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("pro", "active") and sb._tier() == "pro"


def test_rule_d_lets_an_offer_code_downgrade_renewal_through_after_the_max_expired():
    """Rule D shares the purchaseDate test: a 3-day Pro offer-code downgrade's renewal, signed
    before the Max's EXPIRED but delivered after it, is a newer period (Apple charged it at the Max
    end), so it skips the signedDate check. On the end-distance rule it was refused as stale."""
    now = datetime.now(timezone.utc)
    sb = _lineage()
    _register_began(sb, now - timedelta(days=40))
    s = _service(sb)
    e_max = now - timedelta(hours=1)
    s.apply_verified_transaction(_USER, _jws(txn_id="txn-m1", original="txn-g",
                                             signed_at=now - timedelta(days=29), expires_in_days=None,
                                             expiresDate=_ms(e_max)))
    _notify(s, "EXPIRED", _jws(txn_id="txn-m1", original="txn-g", signed_at=now - timedelta(minutes=30),
                               expires_in_days=None, expiresDate=_ms(e_max)),
            at=now - timedelta(minutes=30), subtype="VOLUNTARY")
    assert _row_of(sb, "txn-g")["status"] == "expired"
    renewal = _txn(product=_PRO, txn_id="txn-p1", expires_in_days=None,
                   expiresDate=_ms(e_max + timedelta(days=3)), originalTransactionId="txn-g",
                   signedDate=_ms(e_max - timedelta(hours=12)), purchaseDate=_ms(e_max - timedelta(hours=12)),
                   transactionReason="RENEWAL", offerType=3, offerPeriod="P3D")
    _notify(s, "DID_RENEW", renewal, at=e_max - timedelta(hours=12))
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("pro", "active") and sb._tier() == "pro"


def test_a_downgrade_recovery_in_grace_with_an_offer_period_applies(service_clock, monkeypatch):
    """The grace bypass shares it too: Apple charged the recovery after the Max period in grace
    ended, so a 3-day Pro offer recovery delivered before its own late grace notice is the
    recovery, not a superseded renewal (and the F04 refusal leaves it alone)."""
    sb = FakeSupabase(schema="lineage")
    s, _ = _f03_max_in_grace_after_an_overlap_upgrade(sb, service_clock, monkeypatch)
    recovered = _F03_E_MAX + timedelta(minutes=2)          # signed before the grace notice
    recovery = _f03_payload(_PRO, "txn-p3", _F03_E_MAX + timedelta(days=3), recovered,
                            purchaseDate=_ms(recovered), transactionReason="RENEWAL",
                            offerType=3, offerPeriod="P3D")
    service_clock(sb, _F03_E_MAX + timedelta(hours=2))
    s.apply_notification({"notificationType": "DID_RENEW", "subtype": "BILLING_RECOVERY",
                          "signedDate": _ms(recovered)}, recovery)
    row = _f03_row(sb)
    assert (row["tier"], row["status"]) == ("pro", "active")


def test_the_tier_line_condition_edges():
    """Pure edges of `_newer_period_keeps_the_row` (the 'tier NOT reinstated' line's condition)."""
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = now + timedelta(days=10)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "pro",
           "current_period_end": (end + timedelta(days=20)).isoformat()}

    def keeps(prior=row, *, status="active", tier="premium", otid="otid", period_end=end):
        return svc._newer_period_keeps_the_row(prior, status=status, tier=tier,
                                               original_transaction_id=otid,
                                               period_end=period_end, now=now)

    assert keeps() is True
    assert keeps(prior=None) is False and keeps(status="revoked") is False
    assert keeps(tier="pro") is False and keeps(tier="free") is False, "only a HIGHER tier"
    assert keeps(otid="another") is False
    assert keeps(period_end=now) is False, "the reversed period no longer runs"
    assert keeps(prior={**row, "status": "expired"}) is False, "no newer ENTITLING period"
    assert keeps(prior={**row, "current_period_end": end.isoformat()}) is False, "the same period"
    assert keeps(prior={**row, "current_period_end": (end + timedelta(seconds=60)).isoformat()})


# ═══ IAP audit, PHASE 2 (2026-10-21) ════════════════════════════════════════════════════════
#
# F04b — F04's part 2: a row a stale lower-tier renewal ALREADY overwrote (before the phase-1
# refusal shipped, or by a payload without `transactionReason`). Rule E refused Apple's refund
# of the recorded Max as an older period, and the keyed clawback started the never-paid Pro
# period. The stored row's own register record now tells the superseded renewal apart: applied
# before Apple's revocationDate and more than 48 h before the refunded end.

_F04B_E1 = _ANCHOR + timedelta(hours=20)
_F04B_E2 = _ANCHOR + timedelta(days=31)
_F04B_EM = _F04B_E2 - timedelta(hours=4, minutes=30)


def _f04b_note(ntype, at, status=None):
    note = {"notificationType": ntype, "subtype": "", "signedDate": _ms(at)}
    if status is not None:
        note["data"] = {"status": status}
    return note


def _f04b_overwritten(move, monkeypatch, *, how, now):
    """P1, the Max upgrade M (client verify), then the superseded Pro renewal P2 overwrites it.
    how = 'no-reason': a payload the phase-1 refusal cannot classify; 'pre-refusal': the code
    deployed before 2026-10-17 (the refusal switched off for that one delivery)."""
    sb = FakeSupabase(schema="lineage")
    _register_began(sb, now - timedelta(days=40))
    s = _service(sb)
    p1, p2, m = _f04_payloads(now, _F04B_E1, _F04B_E2, _F04B_EM,
                              reason=None if how == "no-reason" else "RENEWAL")
    move(sb, now - timedelta(hours=2))
    s.apply_verified_transaction(_USER, dict(p1))
    s.apply_notification(_f04b_note("SUBSCRIBED", now - timedelta(days=29, hours=4) + timedelta(minutes=1), 1),
                         dict(p1))
    sb.credits["used"] = 200
    move(sb, now - timedelta(minutes=55))
    s.apply_verified_transaction(_USER, dict(m))
    assert _row_of(sb, "txn-g")["tier"] == "premium"
    move(sb, now - timedelta(minutes=30))
    with monkeypatch.context() as mp:
        if how == "pre-refusal":
            mp.setattr(svc, "_superseded_renewal_reason", lambda *a, **k: None)
        s.apply_notification(_f04b_note("DID_RENEW", now - timedelta(hours=3), 1), dict(p2))
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("pro", "active"), "the overwrite F04b is about"
    assert sb.register[("Production", "txn-t2")]["applied_at"] == now - timedelta(minutes=30)
    return sb, s, m


def _f04b_refund(s, m, *, at, client=False):
    refund = dict(m, revocationDate=_ms(at), signedDate=_ms(at))
    if client:
        return s.apply_verified_transaction(_USER, refund)
    return s.apply_notification(_f04b_note("REFUND", at, 5), refund)


def _f04b_spy(s):
    results = []
    real = s.apply_transaction
    s.apply_transaction = lambda *a, **k: results.append(real(*a, **k)) or results[-1]
    return results


def _f04b_heals(caplog):
    return [r for r in caplog.records if "IAP audit F04b" in r.getMessage()]


@pytest.mark.parametrize("client", [False, True], ids=["webhook", "client-relay"])
@pytest.mark.parametrize("how", ["no-reason", "pre-refusal"])
def test_f04b_the_refund_of_an_upgrade_a_superseded_renewal_overwrote_applies(
        how, client, service_clock, monkeypatch, caplog):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sb, s, m = _f04b_overwritten(service_clock, monkeypatch, how=how, now=now)
    service_clock(sb, now)
    results = _f04b_spy(s)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _f04b_refund(s, m, at=now, client=client)
    assert results[-1]["was_stale"] is False and results[-1].get("refused_by") is None, results[-1]
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("premium", "revoked")
    assert svc._parse_iso(row["current_period_end"]) == _F04B_EM
    assert sb._tier() == "free" and sb.model.paid_period is None and sb.remaining == 50
    assert ("txn-g", _F04B_E2) not in sb.periods, "the never-paid Pro period never starts"
    assert len(_f04b_heals(caplog)) == 1
    # The superseded renewal cannot come back afterwards.
    _, p2, _ = _f04_payloads(now, _F04B_E1, _F04B_E2, _F04B_EM, reason=None)
    s.apply_notification(_f04b_note("DID_RENEW", now - timedelta(hours=3), 1), dict(p2))
    assert results[-1]["refused_by"] == "stale"
    s.apply_verified_transaction(_USER, dict(p2))
    assert results[-1]["was_stale"] is True and sb._tier() == "free"


def test_f04b_no_heal_when_the_overwrite_landed_after_the_revocation(
        service_clock, monkeypatch, caplog):
    """The residual: an overwrite applied AFTER Apple's revocationDate reads like a purchase made
    after the refund, so the refund stays refused (today's behaviour)."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sb, s, m = _f04b_overwritten(service_clock, monkeypatch, how="no-reason", now=now)
    service_clock(sb, now)
    results = _f04b_spy(s)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _f04b_refund(s, m, at=now - timedelta(minutes=45))
    assert results[-1]["refused_by"] == "older_period" and sb._tier() == "pro"
    assert _f04b_heals(caplog) == []


def test_f04b_a_failed_register_read_answers_503_and_the_retry_heals(service_clock, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sb, s, m = _f04b_overwritten(service_clock, monkeypatch, how="no-reason", now=now)
    service_clock(sb, now)
    before = dict(_row_of(sb, "txn-g"))
    n_rpcs = len(sb.rpcs)
    sb._fail.add("subscription_transactions")
    with pytest.raises(svc.IAPError):
        _f04b_refund(s, m, at=now)
    assert _row_of(sb, "txn-g") == before
    assert "revoke_paid_period" not in [n for n, _ in sb.rpcs[n_rpcs:]]
    sb._fail.discard("subscription_transactions")
    _f04b_refund(s, m, at=now)
    assert sb._tier() == "free" and sb.remaining == 50 and ("txn-g", _F04B_E2) not in sb.periods


def _f04b_max(u, em):
    return _jws(product=_MAX, txn_id="txn-m", original="txn-g", signed_at=u, expires_in_days=None,
                expiresDate=_ms(em), appAccountToken=_USER, transactionReason="PURCHASE",
                purchaseDate=_ms(u))


def test_f04b_a_pro_bought_after_the_max_refund_keeps_the_row(service_clock, caplog):
    """K19 inside the overlap bound: the Max's REFUND fails, the user buys Pro on the same lineage
    after it, then the REFUND retry arrives — it stays refused and the paid Pro keeps the row (it
    was applied AFTER Apple's revocationDate)."""
    u = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
    em = svc._one_month_after(u)
    refunded = u + timedelta(days=1)
    bought = refunded + timedelta(hours=1)
    sb = FakeSupabase(schema="lineage")
    _register_began(sb, u - timedelta(days=40))
    s = _service(sb)
    service_clock(sb, u)
    m = _f04b_max(u, em)
    s.apply_verified_transaction(_USER, dict(m))
    sb.credits["used"] = 300
    service_clock(sb, bought)
    pro = _jws(product=_PRO, txn_id="txn-r", original="txn-g", signed_at=bought,
               expires_in_days=None, expiresDate=_ms(svc._one_month_after(bought)),
               appAccountToken=_USER, transactionReason="PURCHASE", purchaseDate=_ms(bought))
    s.apply_verified_transaction(_USER, dict(pro))
    assert _row_of(sb, "txn-g")["tier"] == "pro"
    assert timedelta(0) < svc._one_month_after(bought) - em <= timedelta(days=7), "inside the bound"
    service_clock(sb, bought + timedelta(hours=1))
    results = _f04b_spy(s)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_notification(_f04b_note("REFUND", refunded + timedelta(minutes=5), 5),
                             dict(m, revocationDate=_ms(refunded),
                                  signedDate=_ms(refunded + timedelta(minutes=5))))
    assert results[-1]["refused_by"] == "older_period"
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"]) == ("pro", "active") and sb._tier() == "pro"
    assert sb.periods[("txn-g", em)]["revoked_at"] is not None, "the Max period is clawed back"
    assert sb.model.paid_period["tier"] == "pro"
    assert _f04b_heals(caplog) == []


def test_f04b_a_second_refund_after_a_reversal_keeps_the_resubscribers_row(service_clock, caplog):
    """Review of phase 2: the Max is refunded (applied), the user buys Pro on the same lineage, Apple
    reverses the Max refund (refused: an older period) and then refunds it AGAIN. The second
    revocationDate is after the Pro was applied, so without the guard the heal read the paid Pro as
    the superseded overwrite and demoted the subscriber to Free (K19 through the back door)."""
    u = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
    em = svc._one_month_after(u)
    refunded = u + timedelta(days=1)
    bought = refunded + timedelta(hours=1)
    sb = FakeSupabase(schema="lineage")
    _register_began(sb, u - timedelta(days=40))
    s = _service(sb)
    service_clock(sb, u)
    m = _f04b_max(u, em)
    s.apply_verified_transaction(_USER, dict(m))
    service_clock(sb, refunded + timedelta(minutes=1))
    s.apply_notification(_f04b_note("REFUND", refunded, 5),
                         dict(m, revocationDate=_ms(refunded), signedDate=_ms(refunded)))
    assert _row_of(sb, "txn-g")["status"] == "revoked"
    service_clock(sb, bought)
    pro = _jws(product=_PRO, txn_id="txn-r", original="txn-g", signed_at=bought,
               expires_in_days=None, expiresDate=_ms(svc._one_month_after(bought)),
               appAccountToken=_USER, transactionReason="PURCHASE", purchaseDate=_ms(bought))
    s.apply_verified_transaction(_USER, dict(pro))
    assert (_row_of(sb, "txn-g")["tier"], _row_of(sb, "txn-g")["status"]) == ("pro", "active")
    reversed_at = bought + timedelta(hours=1)
    service_clock(sb, reversed_at)
    s.apply_notification(_f04b_note("REFUND_REVERSED", reversed_at, 1),
                         dict(m, signedDate=_ms(reversed_at)))
    again = reversed_at + timedelta(hours=1)
    service_clock(sb, again + timedelta(minutes=1))
    results = _f04b_spy(s)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_notification(_f04b_note("REFUND", again, 5),
                             dict(m, revocationDate=_ms(again), signedDate=_ms(again)))
    assert results[-1].get("refused_by") is not None, results[-1]
    row = _row_of(sb, "txn-g")
    assert (row["tier"], row["status"], row["transaction_id"]) == ("pro", "active", "txn-r")
    assert sb._tier() == "pro"
    assert _f04b_heals(caplog) == []


def test_f04b_a_short_offer_downgrade_renewal_keeps_the_row(service_clock, caplog):
    """A 6-day Pro offer downgrade renews at the Max end (charged 24 h early, ends inside the
    bound); Apple then refunds the Max while it still runs: the paid Pro keeps the row (it was
    applied less than 48 h before the Max end)."""
    u = datetime.now(timezone.utc).replace(microsecond=0)
    em = svc._one_month_after(u)
    sb = FakeSupabase(schema="lineage")
    _register_began(sb, u - timedelta(days=40))
    s = _service(sb)
    service_clock(sb, u)
    m = _f04b_max(u, em)
    s.apply_verified_transaction(_USER, dict(m))
    charged = em - timedelta(hours=24)
    service_clock(sb, charged)
    downgrade = _txn(product=_PRO, txn_id="txn-d", expires_in_days=None,
                     expiresDate=_ms(em + timedelta(days=6)), originalTransactionId="txn-g",
                     signedDate=_ms(charged), appAccountToken=_USER,
                     transactionReason="RENEWAL", purchaseDate=_ms(charged))
    s.apply_notification(_f04b_note("DID_RENEW", charged, 1), dict(downgrade))
    assert _row_of(sb, "txn-g")["tier"] == "pro", "the downgrade renewal applies"
    refunded = em - timedelta(hours=12)
    service_clock(sb, refunded + timedelta(minutes=1))
    results = _f04b_spy(s)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_notification(_f04b_note("REFUND", refunded, 5),
                             dict(m, revocationDate=_ms(refunded), signedDate=_ms(refunded)))
    assert results[-1]["refused_by"] == "older_period"
    assert _row_of(sb, "txn-g")["status"] == "active" and sb._tier() == "pro"
    assert _f04b_heals(caplog) == []


def test_the_superseded_overwrite_edges():
    """Pure edges of `_superseded_overwrite_reason` (and its record-free `_shape` half)."""
    now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    em = now + timedelta(days=20)
    row_end = em + timedelta(hours=4)
    revoked = now - timedelta(minutes=5)
    applied = now - timedelta(hours=1)
    prior = {"original_transaction_id": "otid", "status": "active", "tier": "pro",
             "current_period_end": row_end.isoformat(), "transaction_id": "txn-p2"}
    rec = {"transaction_id": "txn-p2", "original_transaction_id": "otid", "tier": "pro",
           "period_end": row_end.isoformat(), "applied_at": applied.isoformat()}

    def why(prior=prior, rec=rec, *, tier="premium", env="Production", period_end=em,
            revocation=revoked, at=now):
        return svc._superseded_overwrite_reason(prior, rec, tier=tier, environment=env,
                                                period_end=period_end, revocation_date=revocation,
                                                now=at)

    assert why() is not None and "txn-p2" in why()
    assert why(env="Sandbox") is None
    assert why(period_end=now) is None and why(at=em) is None, "the refunded period still runs"
    assert why(revocation=None) is None and why(rec=None) is None
    assert why(rec={**rec, "applied_at": None}) is None
    assert why(rec={**rec, "tier": "premium"}) is None and why(rec={**rec, "original_transaction_id": "x"}) is None
    assert why(rec={**rec, "period_end": (row_end + timedelta(seconds=60)).isoformat()}) is None
    assert why(rec={**rec, "period_end": (row_end + timedelta(seconds=59)).isoformat()}) is not None
    assert why(tier="pro") is None and why(prior={**prior, "tier": "premium"}) is None
    for status in ("expired", "revoked"):
        assert why(prior={**prior, "status": status}) is None, status
    # The overlap bound on the row's end: 60 s .. 7 d after the refunded period's.
    for later, ok in ((timedelta(seconds=59), False), (timedelta(seconds=60), True),
                      (timedelta(days=7), True), (timedelta(days=7, seconds=1), False)):
        end = em + later
        assert (why(prior={**prior, "current_period_end": end.isoformat()},
                    rec={**rec, "period_end": end.isoformat()}) is not None) is ok, later
    # Applied at least a minute before Apple's revocationDate...
    assert why(rec={**rec, "applied_at": (revoked - timedelta(seconds=60)).isoformat()}) is not None
    assert why(rec={**rec, "applied_at": (revoked - timedelta(seconds=59)).isoformat()}) is None
    # ...and more than 48 h before the refunded end (a downgrade renewal is charged ≤ 24 h early).
    late = em - timedelta(hours=48)
    assert why(revocation=em, rec={**rec, "applied_at": (late - timedelta(seconds=1)).isoformat()}) \
        is not None
    assert why(revocation=em, rec={**rec, "applied_at": late.isoformat()}) is None


# F06 (owner, 2026-10-21: "Refuse it") — a late EXPIRED retry overwrote a resubscription that the
# app's verify had applied (a client write stores no ordering key), dropping the payer to Free.
# In lineage mode an EXPIRED is now refused as older news when the row holds ANOTHER Apple
# transaction ending later. Legacy mode keeps the exemption (documented residual).

_F06_OTID = "txn-1"
_F06_S1 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
_F06_E1 = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
_F06_K = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
_F06_TE = _F06_E1 + timedelta(minutes=1)
_F06_R = _F06_E1 + timedelta(minutes=20)
_F06_E5 = datetime(2027, 1, 1, 12, 20, tzinfo=timezone.utc)


@pytest.fixture
def _f06_production(monkeypatch):
    monkeypatch.setattr(settings, "IAP_ENVIRONMENT", "Production")
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 28)


def _f06_jws(txn_id, *, product=_PRO, purchased, expires, signed, env="Production", **extra):
    return {"transactionId": txn_id, "originalTransactionId": _F06_OTID, "productId": product,
            "bundleId": settings.IAP_BUNDLE_ID, "environment": env, "appAccountToken": _USER,
            "purchaseDate": _ms(purchased), "expiresDate": _ms(expires), "signedDate": _ms(signed),
            **extra}


def _f06_p1(signed, product=_PRO):
    return _f06_jws("txn-1", product=product, purchased=_F06_S1, expires=_F06_E1, signed=signed)


def _f06_p5(signed, product=_PRO, expires=_F06_E5):
    return _f06_jws("txn-5", product=product, purchased=_F06_R, expires=expires, signed=signed,
                    transactionReason="PURCHASE")


def _f06_note(ntype, subtype, signed, status):
    return {"notificationType": ntype, "subtype": subtype, "signedDate": _ms(signed),
            "data": {"status": status, "environment": "Production"}}


def _f06_sb(mode):
    sb = FakeSupabase(schema=mode)
    if mode == "lineage":
        _register_began(sb, _F06_S1 - timedelta(days=30))
    return sb


def _f06_credits(sb):
    m = sb.model
    return (m.total, m.used, m.paid_period["otid"], m.paid_period["end"], len(m.ledger))


def _f06_until_resubscribe(s, sb, move, *, p1_product=_PRO):
    """P1 bought; AUTO_RENEW_DISABLED stores key K; EXPIRED(P1)'s first delivery fails; the user
    resubscribes on the same Apple ID and the app's verify applies P5 (no key stored)."""
    move(sb, _F06_S1)
    s.apply_verified_transaction(_USER, _f06_p1(_F06_S1, p1_product))
    move(sb, _F06_K)
    s.apply_notification(_f06_note("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", _F06_K, 1),
                         _f06_p1(_F06_K, p1_product))
    move(sb, _F06_TE)
    sb._fail.add("subscriptions")
    with pytest.raises(svc.IAPError):
        s.apply_notification(_f06_note("EXPIRED", "VOLUNTARY", _F06_TE, 2), _f06_p1(_F06_TE, p1_product))
    sb._fail.discard("subscriptions")
    move(sb, _F06_R)
    assert s.apply_verified_transaction(_USER, _f06_p5(_F06_R))["was_stale"] is False
    row = _row_of(sb, _F06_OTID)
    assert row["status"] == "active" and svc._parse_iso(row["current_period_end"]) == _F06_E5
    assert svc._parse_iso(row["last_event_at"]) == _F06_K, "a client write stores no key"
    move(sb, _F06_R + timedelta(minutes=10))
    sb.model.spend_credits(100, "report", "r-1")
    return _f06_credits(sb)


@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_f06_a_late_expired_retry_never_overwrites_a_resubscription_the_app_relayed(
        mode, _f06_production, service_clock, caplog):
    sb = _f06_sb(mode)
    s = _service(sb)
    paid = _f06_until_resubscribe(s, sb, service_clock)
    service_clock(sb, _F06_TE + timedelta(hours=1))
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        s.apply_notification(_f06_note("EXPIRED", "VOLUNTARY", _F06_TE, 2), _f06_p1(_F06_TE))
    row = dict(_row_of(sb, _F06_OTID))
    if mode == "legacy":
        assert row["status"] == "expired" and sb._tier() == "free", "the documented residual"
        return
    assert row["status"] == "active" and svc._parse_iso(row["current_period_end"]) == _F06_E5
    assert row["transaction_id"] == "txn-5"
    assert svc._parse_iso(row["last_event_at"]) == _F06_K, "nothing written, not even the key"
    assert sb._tier() == "pro" and _f06_credits(sb) == paid
    warned = [r.getMessage() for r in caplog.records if "OLDER period" in r.getMessage()]
    assert warned and "txn='txn-1'" in warned[-1] and "txn='txn-5'" in warned[-1]
    # Apple's SUBSCRIBED retry still lands afterwards and stores its key.
    service_clock(sb, _F06_R + timedelta(hours=1))
    s.apply_notification(_f06_note("SUBSCRIBED", "RESUBSCRIBE", _F06_R, 1), _f06_p5(_F06_R))
    assert svc._parse_iso(_row_of(sb, _F06_OTID)["last_event_at"]) == _F06_R
    assert sb._tier() == "pro"


def test_f06_a_late_expired_signed_after_the_resubscribe_is_still_refused(
        _f06_production, service_clock):
    """The alternate order: SUBSCRIBED lands first, then an EXPIRED(P1) signed LATER — the stale
    check cannot refuse it; the transaction test does."""
    sb = _f06_sb("lineage")
    s = _service(sb)
    _f06_until_resubscribe(s, sb, service_clock)
    service_clock(sb, _F06_R + timedelta(minutes=11))
    s.apply_notification(_f06_note("SUBSCRIBED", "RESUBSCRIBE", _F06_R, 1), _f06_p5(_F06_R))
    late = _F06_R + timedelta(minutes=40)
    service_clock(sb, late)
    s.apply_notification(_f06_note("EXPIRED", "VOLUNTARY", late, 2), _f06_p1(late))
    assert (_row_of(sb, _F06_OTID)["status"], sb._tier()) == ("active", "pro")


def test_f06_a_lost_max_expired_never_overwrites_a_pro_resubscription(_f06_production, service_clock):
    """A higher tier's EXPIRED a month before the stored end is no overlap upgrade: refused."""
    sb = _f06_sb("lineage")
    s = _service(sb)
    _f06_until_resubscribe(s, sb, service_clock, p1_product=_MAX)
    service_clock(sb, _F06_TE + timedelta(hours=1))
    s.apply_notification(_f06_note("EXPIRED", "VOLUNTARY", _F06_TE, 2), _f06_p1(_F06_TE, _MAX))
    assert (_row_of(sb, _F06_OTID)["status"], sb._tier()) == ("active", "pro")


@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_f06_a_genuine_expiry_of_the_stored_transaction_still_demotes(
        mode, _f06_production, service_clock):
    sb = _f06_sb(mode)
    s = _service(sb)
    service_clock(sb, _F06_S1)
    s.apply_verified_transaction(_USER, _f06_p1(_F06_S1))
    service_clock(sb, _F06_TE)
    s.apply_notification(_f06_note("EXPIRED", "VOLUNTARY", _F06_TE, 2), _f06_p1(_F06_TE))
    assert _row_of(sb, _F06_OTID)["status"] == "expired" and sb._tier() == "free"
    assert sb.model.total == 50


@pytest.mark.parametrize("env", ["Production", "Sandbox"])
def test_f06_an_overlap_upgrades_expiry_over_its_superseded_renewal(env, service_clock, monkeypatch):
    """The one shape the exemption keeps: a Max EXPIRED ending 12 h before the Pro row that the
    renewal it superseded wrote — Production only (a Sandbox month lasts minutes)."""
    monkeypatch.setattr(settings, "IAP_ENVIRONMENT", env)
    sb = _f06_sb("lineage")
    s = _service(sb)
    e2 = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
    em = e2 - timedelta(hours=12)
    sb.db["subscriptions"].append({
        "id": "row-1", "user_id": _USER, "tier": "pro", "status": "active",
        "original_transaction_id": _F06_OTID, "current_period_end": e2.isoformat(),
        "last_event_at": (_F06_E1 - timedelta(hours=30)).isoformat(), "environment": env,
        "store": "app_store", "transaction_id": "txn-2", "max_period_end": e2.isoformat()})
    service_clock(sb, em + timedelta(minutes=1))
    max_txn = _f06_jws("txn-m", product=_MAX, purchased=_F06_E1 - timedelta(hours=12),
                       expires=em, signed=em + timedelta(minutes=1), env=env)
    s.apply_notification(_f06_note("EXPIRED", "VOLUNTARY", em + timedelta(minutes=1), 2), max_txn)
    assert _row_of(sb, _F06_OTID)["status"] == ("expired" if env == "Production" else "active")


def test_the_f06_older_period_edges():
    """Pure edges of `_older_period_reason`'s EXPIRED exception."""
    end = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "active", "tier": "pro",
           "current_period_end": end.isoformat(), "transaction_id": "t5"}
    kw = dict(status="expired", status_override="expired", tier="pro", environment="Production",
              original_transaction_id="otid", period_end=end - timedelta(days=30),
              notification_type="EXPIRED")

    def why(prior=row, **over):
        return svc._older_period_reason(prior, **{**kw, "transaction_id": "t1", **over})

    assert why() is not None and "t1" in why() and "t5" in why()
    assert why(transaction_id="t5") is None, "the stored transaction's own EXPIRED applies"
    assert why(prior={**row, "transaction_id": None}) is None, "a row not rewritten since 198"
    assert why(transaction_id=None) is None, "legacy mode passes none"
    assert why(notification_type="DID_CHANGE_RENEWAL_STATUS") is None, "only EXPIRED"
    assert why(period_end=end - timedelta(seconds=59)) is None, "inside the margin"
    assert why(prior={**row, "status": "expired"}) is None, "no entitling row to protect"
    # A higher tier inside the Production overlap bound keeps the exemption...
    assert why(tier="premium", period_end=end - timedelta(days=7)) is None
    assert why(tier="premium", period_end=end - timedelta(days=7, seconds=1)) is not None
    assert why(tier="premium", period_end=end - timedelta(hours=12), environment="Sandbox") is not None
    assert why(tier="premium", period_end=end - timedelta(hours=12), unrecorded=False) is not None
    # ...and GRACE_PERIOD_EXPIRED keeps its own period-scoped rule.
    assert why(notification_type="GRACE_PERIOD_EXPIRED", transaction_id=None) is not None


# F07 — Apple put the row in grace BEFORE the period ended (it charges the renewal up to 24 h
# early); a later same-period delivery derived "active", rewrote the row, and at the period end
# the subscriber Apple still served dropped to Free. Dormant while billing grace is OFF.

def _f07_grace_noticed_early(sb, move, *, hours_before=20):
    now = datetime.now(timezone.utc)
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    sb.credits["used"] = 300
    noticed = _GRACE_END - timedelta(hours=hours_before)
    move(sb, noticed + timedelta(minutes=1))
    _grace_notice_retried(s, signed=noticed)
    row = _row(sb)
    assert (row["status"], svc._parse_iso(row["current_period_end"]), sb._tier(),
            sb.remaining) == ("grace_period", _GRACE_END, "premium", 3700)
    return s


@pytest.mark.parametrize("source, subtype", [
    ("client", None),
    ("PRICE_INCREASE", "ACCEPTED"),
    ("DID_CHANGE_RENEWAL_PREF", "DOWNGRADE"),
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_ENABLED"),
    ("CONSUMPTION_REQUEST", None),
    ("REFUND_DECLINED", None),
    ("OFFER_REDEEMED", None),
    ("OFFER_REDEEMED", "DOWNGRADE"),
])
@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_f07_a_grace_notice_signed_before_the_period_ends_survives_a_same_period_delivery(
        mode, source, subtype, service_clock, monkeypatch):
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 28)
    sb = FakeSupabase(schema=mode)
    s = _f07_grace_noticed_early(sb, service_clock)
    before = dict(_row(sb))
    signed = _GRACE_END - timedelta(hours=6)
    service_clock(sb, signed + timedelta(minutes=1))
    if source == "client":
        out = s.apply_verified_transaction(_USER, _grace_txn(signedDate=_ms(signed)))
        assert out["was_stale"] is True and out["refused_by"] == "billing_grace"
        assert out["status"] == "grace_period" and out["winning_tier"] == "premium"
    else:
        _apple_notify(s, source, subtype, signed, _grace_txn(), apple_status=4)
    assert _row(sb) == before, "nothing written: status, period end, ordering key"
    service_clock(sb, _GRACE_END + timedelta(hours=1))
    assert s.winning_tier(_USER) == "premium", "Apple serves the grace period"
    out = s.apply_verified_transaction(
        _USER, _grace_txn(signedDate=_ms(_GRACE_END + timedelta(hours=1))))
    assert (out["refused_by"], out["winning_tier"], sb.remaining) == ("billing_grace", "premium", 3700)
    assert _row(sb)["status"] == "grace_period"


@pytest.mark.parametrize("ntype, subtype, apple_status", [
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", 1),
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", 4),
    ("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED", None),
    ("DID_FAIL_TO_RENEW", None, None),
    ("PRICE_INCREASE", "ACCEPTED", 2),
    ("PRICE_INCREASE", "ACCEPTED", 3),
    ("DID_CHANGE_RENEWAL_PREF", "DOWNGRADE", 5),
])
@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_f07_after_an_early_grace_notice_apples_word_still_ends_service_at_the_period_end(
        mode, ntype, subtype, apple_status, service_clock, monkeypatch):
    """The over-holding guard: a cancellation, a DID_FAIL_TO_RENEW without a grace subtype and
    Apple's data.status 2/3/5 still apply — service ends at the period end."""
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 28)
    sb = FakeSupabase(schema=mode)
    s = _f07_grace_noticed_early(sb, service_clock)
    signed = _GRACE_END - timedelta(hours=6)
    service_clock(sb, signed + timedelta(minutes=1))
    _apple_notify(s, ntype, subtype, signed, _grace_txn(), apple_status=apple_status)
    row = _row(sb)
    assert row["status"] == "active" and svc._parse_iso(row["current_period_end"]) == _GRACE_END
    service_clock(sb, _GRACE_END + timedelta(hours=1))
    assert s.winning_tier(_USER) == "free"


def test_the_early_grace_notice_hold_edges():
    end = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    row = {"original_transaction_id": "otid", "status": "grace_period", "tier": "premium",
           "current_period_end": end.isoformat()}

    def reason(prior=row, *, status="active", override=None, otid="otid", period_end=end,
               now=end - timedelta(hours=6), ntype=None, subtype=None, apple_status=None):
        return svc._billing_grace_hold_reason(
            prior, status=status, status_override=override, original_transaction_id=otid,
            period_end=period_end, notification_type=ntype, notification_subtype=subtype,
            apple_status=apple_status, now=now)

    for st in ("grace_period", "billing_retry", "Grace_Period"):
        assert reason(prior={**row, "status": st}) is not None, st
        assert reason(prior={**row, "status": st}, override="active") is not None, st
    for st in ("active", "expired", "revoked", "", None):
        assert reason(prior={**row, "status": st}) is None, st
        assert reason(prior={**row, "status": st}, override="active") is None, st
    assert reason(status="grace_period", override="grace_period") is None
    assert reason(status="revoked") is None and reason(otid="another") is None
    assert reason(now=end) is None, "the period has ended: the derived-expiry rules decide"
    for drift in (timedelta(0), timedelta(seconds=59), -timedelta(seconds=59)):
        assert reason(period_end=end + drift) is not None, drift
    for drift in (timedelta(seconds=60), -timedelta(seconds=60), timedelta(days=30)):
        assert reason(period_end=end + drift) is None, drift
    for kw in ({"ntype": "DID_CHANGE_RENEWAL_STATUS", "subtype": "AUTO_RENEW_DISABLED"},
               {"ntype": "DID_CHANGE_RENEWAL_STATUS", "subtype": "AUTO_RENEW_DISABLED",
                "apple_status": 1},
               {"ntype": "DID_FAIL_TO_RENEW"}, {"apple_status": 2}, {"apple_status": 3},
               {"apple_status": 5}):
        assert reason(**kw) is None, kw
    for kw in ({"apple_status": 1}, {"apple_status": 4},
               {"ntype": "DID_CHANGE_RENEWAL_STATUS", "subtype": "AUTO_RENEW_ENABLED"}):
        assert reason(**kw) is not None, kw
    text = reason()
    assert end.isoformat() in text and "IAP_BILLING_GRACE_DAYS" in text
    assert (end + timedelta(days=settings.IAP_BILLING_GRACE_DAYS)).isoformat() in text


# F14 — past the hold's window, an explicit-"active" OFFER_REDEEMED about the period in grace,
# carrying Apple's data.status 4, stored its signedDate, so Apple's late grace notice was refused
# as stale. The hold already read it as a derived expiry; now the no-key rule does too.

_F14_OFFER_AT = _GRACE_END + timedelta(hours=25)


@pytest.mark.parametrize("subtype", [None, "DOWNGRADE"])
@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_f14_an_offer_redeemed_past_the_window_stores_no_key_when_apple_says_grace(
        mode, subtype, service_clock, monkeypatch, caplog):
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 28)
    sb = FakeSupabase(schema=mode)
    s = _max_past_its_end(sb, service_clock, minutes=25 * 60)
    with caplog.at_level(logging.WARNING, logger="app.services.iap_service"):
        _apple_notify(s, "OFFER_REDEEMED", subtype, _F14_OFFER_AT, _grace_txn(), apple_status=4)
    assert _row(sb).get("last_event_at") != _F14_OFFER_AT.isoformat()
    assert any("WITHOUT an ordering key" in r.getMessage() and "override=active" in r.getMessage()
               for r in caplog.records)
    _grace_notice_retried(s)
    assert (_row(sb)["status"], sb._tier(), sb.remaining) == ("grace_period", "premium", 3700)


@pytest.mark.parametrize("apple_status", [None, 1])
@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_f14_without_apples_grace_status_the_offer_still_stores_its_key(
        mode, apple_status, service_clock, monkeypatch):
    """K86's scope stays: without data.status 4 the explicit 'active' stores its key."""
    monkeypatch.setattr(settings, "IAP_BILLING_GRACE_DAYS", 28)
    sb = FakeSupabase(schema=mode)
    s = _max_past_its_end(sb, service_clock, minutes=25 * 60)
    _apple_notify(s, "OFFER_REDEEMED", None, _F14_OFFER_AT, _grace_txn(), apple_status=apple_status)
    assert _row(sb).get("last_event_at") == _F14_OFFER_AT.isoformat()
    _grace_notice_retried(s)
    assert _row(sb)["status"] == "active" and sb._tier() == "free"


@pytest.mark.parametrize("mode", ["legacy", "lineage"])
def test_f14_an_offer_redeemed_for_a_running_period_still_stores_its_key(mode, service_clock):
    """The `period_end <= now` half: about a period still running, the key is stored."""
    now = datetime.now(timezone.utc)
    sb = FakeSupabase(schema=mode)
    s = _renewed_max(sb, renew_signed_at=now - timedelta(days=3))
    _apple_notify(s, "OFFER_REDEEMED", None, now - timedelta(hours=1), _grace_txn(), apple_status=4)
    assert _row(sb)["status"] == "active" and sb._tier() == "premium"
    assert _row(sb).get("last_event_at") == (now - timedelta(hours=1)).isoformat()


# F13 — the webhook answered every refused delivery "applied:{type}", so a refund that was
# ignored, held or only clawed back by key read exactly like one that landed.

def test_f13_a_refused_refund_is_answered_unapplied_and_names_the_rule(
        _f06_production, service_clock, caplog):
    sb = _f06_sb("lineage")
    s = _service(sb)
    _f06_until_resubscribe(s, sb, service_clock)
    at = _F06_R + timedelta(hours=2)
    service_clock(sb, at)
    with caplog.at_level(logging.INFO, logger="app.services.iap_service"):
        out = s.apply_notification(_f06_note("REFUND", "", at, 5),
                                   dict(_f06_p1(at), revocationDate=_ms(at)))
    assert out == ("unapplied:older_period:REFUND", _USER)
    msgs = [r.getMessage() for r in caplog.records]
    assert [m for m in msgs if m.startswith("IAP webhook NOT applied:")
            and "type=REFUND " in m and "refused_by=older_period" in m and "txn=txn-1" in m]
    assert not [m for m in msgs if m.startswith("IAP webhook applied:")]
    assert (_row_of(sb, _F06_OTID)["status"], sb._tier()) == ("active", "pro")
    # The control: the refund of the period in force lands, and says so.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.services.iap_service"):
        out = s.apply_notification(_f06_note("REFUND", "", at, 5),
                                   dict(_f06_p5(at), revocationDate=_ms(at)))
    assert out == ("applied:REFUND", _USER) and sb._tier() == "free"
    assert not [r for r in caplog.records if "NOT applied" in r.getMessage()]
    # The applied line also names the delivery's own transaction (txn= is the lineage), so the
    # F04b one-off check can tie it to a register row (verification of the final fixes).
    p5 = _f06_p5(at)["transactionId"]
    assert [r for r in caplog.records if r.getMessage().startswith("IAP webhook applied: type=REFUND ")
            and r.getMessage().endswith(f" transaction={p5}")], [r.getMessage() for r in caplog.records]
