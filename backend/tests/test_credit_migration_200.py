"""Migration 200's three credit fixes, pinned on the executable model of its SQL.

`test_credit_pool_isolation._Credits` models 200's `ensure_credit_period` and `refund_credits`.
Every scenario here was first run against the real SQL on a local Postgres 17 (the audit's
repro_F10_v2 / repro_F11_v2 / repro_F15_v2 plus an asserting edge file, 2026-10-21): the numbers
asserted below are the ones Postgres gave with 200 loaded, and each scenario's BUG account is
compared with a CTL twin given the same charges and Apple events.

  F10  refund_credits rolls the period first, so a charge and refund that straddle a boundary
       take the documented capped_to_zero path whether or not anything read the balance between.
  F11  a paid renewal no credit call saw while it was active starts once its lineage row is in
       grace or retry, or 'active' past its end — unless the register holds a refund of it.
  F15  a charge that an Apple-refunded allocation already paid for is absorbed by the clawback's
       cover instead of being paid back out of the calendar floor, and a reinstatement deducts
       the calendar spend of the refund window.

The model's `spend_credits` does not roll the period (the SQL's does; IAP audit F21), so every
spend here goes through `spend()`, which rolls first like the SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from test_credit_pool_isolation import _Credits, _next_et_reset

UTC = timezone.utc
E1 = datetime(2026, 11, 5, 10, tzinfo=UTC)          # P1's end
E2 = datetime(2026, 12, 5, 10, tzinfo=UTC)          # P2's end (the renewal)


def at(c: _Credits, ts: str) -> None:
    c.now = datetime.fromisoformat(ts).replace(tzinfo=UTC)


def account(start: str = "2026-10-05T10:00") -> _Credits:
    c = _Credits(total=50, now=datetime.fromisoformat(start).replace(tzinfo=UTC))
    c.tier_alloc = 50
    return c


def apply_active(c: _Credits, otid: str, txn: str, end: datetime) -> None:
    """The webhook applying an entitling delivery: the row, the register, then reconcile."""
    c.put_subscription(otid, "pro", "active", end, "Production")
    c.record_subscription_transaction("Production", txn, otid, c.user_id, "pro", end, None,
                                      "applied", c.now)
    c.tier = "pro"
    c.ensure_credit_period()


def refund_current(c: _Credits, otid: str, txn: str, end: datetime) -> None:
    """Apple REFUND of the period in force (lineage mode): row revoked under the same key."""
    c.record_subscription_transaction("Production", txn, otid, c.user_id, "pro", end, c.now,
                                      "revoked", c.now)
    c.put_subscription(otid, "pro", "revoked", end, "Production")
    c.tier = "free"
    c.ensure_credit_period()
    c.revoke_paid_period("Production", txn, otid, end)


def refund_older(c: _Credits, otid: str, txn: str, end: datetime) -> None:
    """Apple REFUND of an OLDER period: the row write is refused, then the keyed clawback."""
    c.record_subscription_transaction("Production", txn, otid, c.user_id, "pro", end, c.now,
                                      "revoked", c.now)
    c.ensure_credit_period()
    c.revoke_paid_period("Production", txn, otid, end)


def reverse(c: _Credits, otid: str, txn: str, end: datetime) -> None:
    """Apple REFUND_REVERSED of the refunded period: the row entitles again under its key."""
    c.record_subscription_transaction("Production", txn, otid, c.user_id, "pro", end, None,
                                      "reversed", c.now)
    c.ensure_credit_period()
    c.put_subscription(otid, "pro", "active", end, "Production")
    c.tier = "pro"
    c.ensure_credit_period()
    c.reinstate_paid_carry(otid, end)


def spend(c: _Credits, amount: int, reason: str, ref: str):
    c.ensure_credit_period()
    return c.spend_credits(amount, reason, ref)


def pairing_rows(c: _Credits) -> list[dict]:
    return [r for r in c.ledger if r["delta"] == 0 and r["reverses_id"] is not None]


def cover(c: _Credits, otid: str, end: datetime = E1):
    return c.periods[(otid, end)].get("revoked_cover")


# ── F10: the refund rolls the period first ──────────────────────────────────────────────────

def test_f10_a_refund_across_the_et_first_answers_capped_to_zero_with_or_without_a_read_between():
    """repro_F10 `cal`: a Free account charged at 23:58 ET on Nov 30 and refunded by the sweep at
    09:00Z. Before 200 the idle account answered `refunded` into the ended month's pool — no page,
    the card said Refunded — and the next spend expired those credits."""
    outcomes, balances = [], []
    for read_between in (False, True):
        c = account("2026-11-20T12:00")
        c.resets_at = datetime(2026, 12, 1, 5, tzinfo=UTC)
        at(c, "2026-12-01T04:58")
        spend(c, 20, "report_charge", "AAPL:r1")
        at(c, "2026-12-01T05:01")
        if read_between:
            c.ensure_credit_period()
        at(c, "2026-12-01T09:00")
        outcomes.append(c.refund_credits(20, "report_refund", "AAPL:r1")["outcome"])
        at(c, "2026-12-01T12:00")
        spend(c, 1, "chat_turn", "s:1")
        balances.append(c.spendable)
    assert outcomes == ["capped_to_zero", "capped_to_zero"]
    assert balances == [49, 49]


def test_f10_a_refund_across_a_paid_renewal_answers_capped_to_zero():
    """repro_F10 `paid`: P2 recorded early (DID_RENEW reconciled at 09:00), a report charged at
    09:58 against P1, the refund at 10:03 with nothing in between. P2 starts inside the refund."""
    c = account()
    apply_active(c, "ot-f10", "t1", E1)
    at(c, "2026-11-05T09:00")
    spend(c, 300, "chat_turn", "s:1")
    apply_active(c, "ot-f10", "t2", E2)                  # early renewal: P1 still runs
    at(c, "2026-11-05T09:58")
    spend(c, 20, "report_charge", "AAPL:r2")
    at(c, "2026-11-05T10:03")
    r = c.refund_credits(20, "report_refund", "AAPL:r2")
    assert r == {"outcome": "capped_to_zero", "refunded": 0, "spendable": 1200}
    assert c.paid_period["end"] == E2


def test_f10_a_refund_inside_the_period_keeps_142s_reply_shape():
    """No boundary, no clawback: exactly 142's reply — no `covered` key."""
    c = account()
    apply_active(c, "ot-f10b", "t1", E1)
    at(c, "2026-10-12T12:00")
    spend(c, 20, "report_charge", "AAPL:r3")
    assert c.refund_credits(20, "report_refund", "AAPL:r3") == {
        "outcome": "refunded", "refunded": 20, "spendable": 1200}


# ── F11: a renewal no credit call saw while it was active ───────────────────────────────────

@pytest.mark.parametrize("status", ["grace_period", "billing_retry", "active"])
def test_f11_the_in_force_lineages_later_period_starts_whatever_its_status(status):
    """P2 recorded on the row while P1 ran, and no credit call during P2 while it was active; by
    the next touch the row is in grace or retry (or still 'active' past its end). Before 200 HOLD
    kept P1's leftovers and P2 was never granted."""
    c = account()
    apply_active(c, "ot-f11", "t1", E1)
    at(c, "2026-10-20T12:00")
    spend(c, 1200, "chat_turn", "s:1")                  # P1 spent out
    at(c, "2026-11-04T10:00")
    c.put_subscription("ot-f11", "pro", "active", E2, "Production")   # DID_RENEW, no credit call
    at(c, "2026-12-10T12:00")                           # P2 has ended too
    c.put_subscription("ot-f11", "pro", status, E2, "Production")
    assert c.ensure_credit_period() == 1200
    assert c.paid_period["end"] == E2
    assert [r["reason"] for r in c.ledger].count("plan_period") == 2
    assert spend(c, 20, "report_charge", "AAPL:f11") == 1180
    assert c.ensure_credit_period() == 1180, "a second touch neither re-grants nor lapses it"


def test_f11_a_later_period_the_register_holds_a_refund_of_is_not_started():
    """Edge E5 on Postgres: same shape, but Apple refunded P2."""
    c = account()
    apply_active(c, "ot-f11r", "t1", E1)
    at(c, "2026-11-04T10:00")
    c.put_subscription("ot-f11r", "pro", "grace_period", E2, "Production")
    c.record_subscription_transaction("Production", "t2", "ot-f11r", c.user_id, "pro", E2, None,
                                      "applied", c.now)
    c.record_subscription_transaction("Production", "t2", "ot-f11r", c.user_id, "pro", E2,
                                      c.now, "revoked", c.now)
    at(c, "2026-11-06T10:00")
    c.ensure_credit_period()
    assert ("ot-f11r", E2) not in c.periods


def test_f11_another_lineages_row_in_retry_does_not_start_through_the_new_rule():
    """Edge E6 (rewritten after the review: the first version ran while P1 still ran, so the rule
    never fired): P1 has ended and still holds; ANOTHER lineage's HIGHER-tier row in billing retry
    records a later end. Only the in-force lineage's own row may start through the new rule — a
    retry is a failed renewal, not a paid period — so P1 holds."""
    c = account()
    apply_active(c, "ot-a", "t1", E1)
    c.put_subscription("ot-b", "premium", "billing_retry", datetime(2026, 12, 20, 10, tzinfo=UTC),
                       "Production")
    at(c, "2026-11-06T10:00")
    assert c.ensure_credit_period() == 1200
    assert list(c.periods) == [("ot-a", E1)]


def test_f11_the_in_force_lineages_late_renewal_starts_when_another_lineage_wins():
    """The two-lineage retarget: B (same tier, active, a later end) wins the ORDER BY but may not
    start while A's period holds; A's own row, now in grace, records P2. 198 needed A's row active
    with a future end, so HOLD kept P1's leftovers; 200 retargets to A and starts P2."""
    c = account()
    apply_active(c, "ot-a", "t1", E1)
    at(c, "2026-10-20T12:00")
    spend(c, 1000, "chat_turn", "s:1")
    at(c, "2026-11-04T10:00")
    c.put_subscription("ot-a", "pro", "active", E2, "Production")       # A renewed, unseen
    c.put_subscription("ot-b", "pro", "active", datetime(2026, 12, 25, 10, tzinfo=UTC), "Production")
    at(c, "2026-11-06T10:00")
    c.put_subscription("ot-a", "pro", "grace_period", E2, "Production")
    assert c.ensure_credit_period() == 1200
    assert (c.paid_period["otid"], c.paid_period["end"]) == ("ot-a", E2)
    assert ("ot-b", datetime(2026, 12, 25, 10, tzinfo=UTC)) not in c.periods


# ── F15: what the clawback already paid for ─────────────────────────────────────────────────

def _in_flight(bug_fails_after: bool, spends=(200,), reports=("AAPL",)):
    """repro_F15 (a)/(m): P1, chat spend, report(s) charged; the report fails before (CTL) or
    after (BUG) Apple refunds P1."""
    c = account()
    apply_active(c, "ot-a", "t1", E1)
    at(c, "2026-10-10T15:00")
    for n in spends:
        spend(c, n, "chat_turn", "s:1")
    at(c, "2026-10-12T12:00")
    for t in reports:
        spend(c, 20, "report_charge", f"{t}:r")
    outs = []
    if not bug_fails_after:
        at(c, "2026-10-12T12:03")
        outs = [c.refund_credits(20, "report_refund", f"{t}:r") for t in reports]
    at(c, "2026-10-12T12:05")
    refund_current(c, "ot-a", "t1", E1)
    if bug_fails_after:
        at(c, "2026-10-12T12:07")
        outs = [c.refund_credits(20, "report_refund", f"{t}:r") for t in reports]
    return c, outs


def test_f15_a_report_refunded_after_the_apple_refund_is_covered_not_paid_from_the_floor():
    """(a) on Postgres: BUG 20 vs CTL 0 before 200; now both 0."""
    bug, (r,) = _in_flight(True)
    ctl, _ = _in_flight(False)
    assert r == {"outcome": "covered_by_clawback", "refunded": 0, "covered": 20, "spendable": 0}
    assert bug.spendable == ctl.spendable == 0
    assert len(pairing_rows(bug)) == 1
    assert bug.periods[("ot-a", E1)]["revoked_remaining"] == 1000      # 1200 - 220 + 20
    assert cover(bug, "ot-a") == 150                                     # 170 written off - 20
    replay = bug.refund_credits(20, "report_refund", "AAPL:r")
    assert replay["outcome"] == "already_refunded" and cover(bug, "ot-a") == 150


def test_f15_three_reports_are_never_paid_back_out_of_the_floor():
    """(m): before 200 the floor paid back all three (50, capped by the floor)."""
    bug, outs = _in_flight(True, reports=("AAPL", "MSFT", "NVDA"))
    ctl, _ = _in_flight(False, reports=("AAPL", "MSFT", "NVDA"))
    assert [o["outcome"] for o in outs] == ["covered_by_clawback"] * 3
    assert bug.spendable == ctl.spendable == 0


def test_f15_covered_then_reversed_equals_the_account_apple_never_refunded():
    """Edge E1: the covered refund goes to revoked_remaining, so the reversal gives it back
    (before 200: 980 against 1000 — the floor's payback expired at the reinstatement)."""
    bug, _ = _in_flight(True)
    ctl, _ = _in_flight(False)
    for c in (bug, ctl):
        at(c, "2026-10-14T09:00")
        reverse(c, "ot-a", "t1", E1)
    assert bug.spendable == ctl.spendable == 1000
    p = bug.periods[("ot-a", E1)]
    assert p["revoked_cover"] is None and p["revoked_ledger_id"] is None


def test_f15_a_partly_covered_charge_pays_only_the_rest_from_the_floor():
    """Edge E2: written off 15 (65 used over the 50 floor), the 20 charge: 15 covered, 5 back."""
    bug, (r,) = _in_flight(True, spends=(45,))
    ctl, _ = _in_flight(False, spends=(45,))
    assert cover(ctl, "ot-a") == 0
    assert r == {"outcome": "refunded", "refunded": 5, "covered": 15, "spendable": 5}
    assert bug.spendable == ctl.spendable == 5
    for c in (bug, ctl):
        at(c, "2026-10-14T09:00")
        reverse(c, "ot-a", "t1", E1)
    assert bug.spendable == ctl.spendable == 1155


def test_f15_a_partly_covered_charge_whose_rest_is_capped_is_owed_and_paired():
    """Edge E3: the refund lands after the ET 1st, so the uncovered 5 finds `used` reset."""
    c = account()
    apply_active(c, "ot-e3", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 45, "chat_turn", "s:1")
    at(c, "2026-10-31T12:00")
    spend(c, 20, "report_charge", "AAPL:e3")
    at(c, "2026-10-31T12:05")
    refund_current(c, "ot-e3", "t1", E1)
    at(c, "2026-11-01T09:00")
    r = c.refund_credits(20, "report_refund", "AAPL:e3")
    assert (r["outcome"], r["refunded"], r["covered"]) == ("capped_to_zero", 0, 15)
    assert len(pairing_rows(c)) == 1
    assert c.refund_credits(20, "report_refund", "AAPL:e3")["outcome"] == "already_refunded"


def test_f15_a_charge_made_after_the_clawback_is_never_covered():
    """Edge E4: a debit on the calendar floor after the clawback is refunded normally."""
    c = account()
    apply_active(c, "ot-e4", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 200, "chat_turn", "s:1")
    at(c, "2026-10-12T12:05")
    refund_current(c, "ot-e4", "t1", E1)
    assert c.spendable == 0
    at(c, "2026-11-02T12:00")
    spend(c, 20, "report_charge", "AAPL:e4")
    at(c, "2026-11-02T12:05")
    r = c.refund_credits(20, "report_refund", "AAPL:e4")
    assert r == {"outcome": "refunded", "refunded": 20, "spendable": 50}
    assert cover(c, "ot-e4") == 150


def test_f15_after_a_keyed_clawback_started_p2_the_in_flight_refund_is_covered_not_owed():
    """(b): an early renewal, then Apple refunds P1; revoke_paid_period claws P1 back and starts
    P2. Before 200 the in-flight report's refund answered capped_to_zero — a false OWED page."""
    def run(fails_after: bool) -> tuple[_Credits, dict]:
        c = account()
        apply_active(c, "ot-b", "t1", E1)
        at(c, "2026-10-10T15:00")
        spend(c, 200, "chat_turn", "s:1")
        at(c, "2026-11-04T10:00")
        apply_active(c, "ot-b", "t2", E2)
        at(c, "2026-11-04T12:00")
        spend(c, 20, "report_charge", "AAPL:b")
        r = {}
        if not fails_after:
            at(c, "2026-11-04T12:03")
            r = c.refund_credits(20, "report_refund", "AAPL:b")
        at(c, "2026-11-04T12:05")
        refund_older(c, "ot-b", "t1", E1)
        if fails_after:
            at(c, "2026-11-04T12:07")
            r = c.refund_credits(20, "report_refund", "AAPL:b")
        return c, r
    bug, r = run(True)
    ctl, rc = run(False)
    assert rc["outcome"] == "refunded"
    assert r["outcome"] == "covered_by_clawback" and r["covered"] == 20
    assert bug.spendable == ctl.spendable == 1200
    assert bug.paid_period["end"] == E2


@pytest.mark.parametrize("refund_at,window_spends,reverse_at,expected", [
    # nothing spent while it stood refunded: the neutral check
    ("2026-10-12T12:05", (), "2026-10-14T09:00", 1190),
    # (c): 40 from the calendar floor
    ("2026-10-12T12:05", (("2026-10-13T09:00", 40),), "2026-10-14T09:00", 1150),
    # (c2): the window spans the ET 1st, a second calendar allowance
    ("2026-10-29T12:00", (("2026-10-30T12:00", 40), ("2026-11-02T12:00", 50)), "2026-11-03T12:00", 1100),
])
def test_f15_a_reversal_deducts_the_calendar_spend_of_the_refund_window(
        refund_at, window_spends, reverse_at, expected):
    """(c)/(c2): the CTL twin was never refunded and spent the same. Before 200 the BUG account
    got 1190 back whatever it spent from the floor in between."""
    def run(refunded: bool) -> _Credits:
        c = account()
        apply_active(c, "ot-c", "t1", E1)
        at(c, "2026-10-10T15:00")
        spend(c, 10, "chat_turn", "s:1")
        at(c, refund_at)
        if refunded:
            refund_current(c, "ot-c", "t1", E1)
        for i, (ts, n) in enumerate(window_spends):
            at(c, ts)
            assert spend(c, n, "chat_turn", f"s:w{i}") is not None
        at(c, reverse_at)
        if refunded:
            reverse(c, "ot-c", "t1", E1)
        else:
            c.ensure_credit_period()
        return c
    assert run(True).spendable == run(False).spendable == expected


def test_f15_a_clawback_before_200_has_no_mark_and_is_reinstated_as_before():
    """A period clawed back by 198's body (no revoked_ledger_id) restores exactly
    revoked_remaining, and refund_credits finds no cover on it."""
    c = account()
    apply_active(c, "ot-old", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 10, "chat_turn", "s:1")
    at(c, "2026-10-12T12:05")
    refund_current(c, "ot-old", "t1", E1)
    p = c.periods[("ot-old", E1)]
    p["revoked_cover"], p["revoked_ledger_id"] = None, None           # as 198 left it
    at(c, "2026-10-13T09:00")
    spend(c, 40, "chat_turn", "s:2")
    at(c, "2026-10-14T09:00")
    reverse(c, "ot-old", "t1", E1)
    assert c.spendable == 1190


def test_f15_a_charge_made_before_the_period_was_granted_is_never_covered():
    """The cover's `applied_at <= the debit's created_at` bound: a Free-month charge, then a Pro
    purchase, then Apple refunds that Pro. The charge was never paid for by the refunded allocation,
    so its refund is not absorbed (it pays back from the floor, as before 200)."""
    c = account("2026-10-04T10:00")
    c.resets_at = datetime(2026, 11, 1, 4, tzinfo=UTC)
    spend(c, 20, "report_charge", "AAPL:pre")
    at(c, "2026-10-05T10:00")
    apply_active(c, "ot-pre", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 200, "chat_turn", "s:1")
    at(c, "2026-10-12T12:05")
    refund_current(c, "ot-pre", "t1", E1)
    assert cover(c, "ot-pre") == 150
    r = c.refund_credits(20, "report_refund", "AAPL:pre")
    assert r == {"outcome": "refunded", "refunded": 20, "spendable": 20}
    assert cover(c, "ot-pre") == 150


def test_f15_the_window_stops_at_the_next_paid_periods_start():
    """A refunded period's reinstatement deducts the calendar spend of ITS window only: B's period
    started (its plan_period row) and was refunded in turn before Apple reversed A's refund, and
    the 100 spent in B's period came out of B's allocation, not A's floor."""
    c = account()
    apply_active(c, "ot-a", "ta1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 10, "chat_turn", "s:1")
    at(c, "2026-10-12T12:05")
    refund_current(c, "ot-a", "ta1", E1)
    at(c, "2026-10-13T09:00")
    spend(c, 40, "chat_turn", "s:2")                      # A's window, from the floor
    at(c, "2026-10-14T09:00")
    qb = datetime(2026, 11, 14, 9, tzinfo=UTC)
    apply_active(c, "ot-b", "tb1", qb)                    # B's period starts: the stop row
    at(c, "2026-10-15T09:00")
    spend(c, 100, "chat_turn", "s:3")                     # inside B's period
    at(c, "2026-10-16T09:00")
    refund_current(c, "ot-b", "tb1", qb)
    at(c, "2026-10-17T09:00")
    reverse(c, "ot-a", "ta1", E1)
    assert (c.paid_period["otid"], c.paid_period["end"]) == ("ot-a", E1)
    assert c.spendable == 1150                            # 1190 restored - the window's 40


def test_a_replay_of_a_partly_covered_refund_whose_rest_was_capped_is_still_short():
    """Review of 200: the first reply (capped_to_zero, covered 15) is lost; the retry finds the
    debit paired and answers already_refunded — with `short` 5, so it still pages."""
    c = account()
    apply_active(c, "ot-e3", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 45, "chat_turn", "s:1")
    at(c, "2026-10-31T12:00")
    spend(c, 20, "report_charge", "AAPL:e3")
    at(c, "2026-10-31T12:05")
    refund_current(c, "ot-e3", "t1", E1)
    at(c, "2026-11-01T09:00")
    first = c.refund_credits(20, "report_refund", "AAPL:e3")
    assert (first["outcome"], first["covered"]) == ("capped_to_zero", 15)
    assert pairing_rows(c)[0]["covered"] == 15
    replay = c.refund_credits(20, "report_refund", "AAPL:e3")
    assert (replay["outcome"], replay.get("short")) == ("already_refunded", 5)


@pytest.mark.parametrize("covered_first", [False, True])
def test_a_replay_of_a_whole_refund_has_no_short_key(covered_first):
    """Control: a refund that settled the whole charge (moved it, or a clawback covered it) replays
    exactly as 142 did."""
    bug, (r,) = _in_flight(covered_first)
    assert r["outcome"] in ("covered_by_clawback", "refunded")
    replay = bug.refund_credits(20, "report_refund", "AAPL:r")
    assert replay == {"outcome": "already_refunded", "refunded": 0, "spendable": bug.spendable}


def test_a_replay_of_a_plain_partial_refund_is_short_too():
    """The same hole before 200 (inherited from 124/142): a refund cut short at the ET 1st (the new
    month's `used` is 1), its reply lost, the retry answered a settled already_refunded."""
    c = account("2026-11-20T12:00")
    c.resets_at = datetime(2026, 12, 1, 5, tzinfo=UTC)
    at(c, "2026-12-01T04:58")
    spend(c, 20, "report_charge", "AAPL:p")
    at(c, "2026-12-01T05:01")
    spend(c, 1, "chat_turn", "s:1")                      # the new month: used = 1
    first = c.refund_credits(20, "report_refund", "AAPL:p")
    assert (first["outcome"], first["refunded"]) == ("refunded", 1)
    replay = c.refund_credits(20, "report_refund", "AAPL:p")
    assert (replay["outcome"], replay.get("short")) == ("already_refunded", 19)


# ── final review (2026-10-21) ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("refunded", [True, False], ids=["apple-refunded", "never-refunded-twin"])
def test_a_window_charge_refunded_after_the_reversal_matches_the_twin(refunded):
    """Final review #0: R1 charged before the Apple refund and refunded during the window (from the
    floor), R2 charged during the window, Apple reverses the refund, then R2 is refunded. Netting
    R1's refund against R2's charge left `used` at 0, so R2's refund was capped (a false OWED);
    R1's refund now goes back into the restored total and R2 stays in `used`."""
    c = account()
    apply_active(c, "ot-w", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 20, "report_charge", "AAPL:w1")
    at(c, "2026-10-12T12:05")
    if refunded:
        refund_current(c, "ot-w", "t1", E1)
        assert cover(c, "ot-w") == 0
    at(c, "2026-10-12T12:07")
    assert c.refund_credits(20, "report_refund", "AAPL:w1")["outcome"] == "refunded"
    at(c, "2026-10-13T09:00")
    spend(c, 20, "report_charge", "AAPL:w2")
    at(c, "2026-10-14T09:00")
    if refunded:
        reverse(c, "ot-w", "t1", E1)
    else:
        c.ensure_credit_period()
    assert (c.total, c.used, c.spendable) == (1200, 20, 1180)
    at(c, "2026-10-15T09:00")
    r = c.refund_credits(20, "report_refund", "AAPL:w2")
    assert (r["outcome"], r["refunded"]) == ("refunded", 20)
    assert c.spendable == 1200


@pytest.mark.parametrize("refunded", [True, False], ids=["apple-refunded", "never-refunded-twin"])
def test_a_charge_and_its_refund_inside_the_window_net_out(refunded):
    """Control for #0: a window charge refunded inside the window nets out — the reinstated pool
    spends exactly like the twin's (1170), and `used` is not inflated by the pair (a gross count
    would leave 20 more in it and overstate a later clawback's cover). The reinstated pool has no
    pre-clawback spend in `used` (198's restore shape), the twin keeps its 30."""
    c = account()
    apply_active(c, "ot-p", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 30, "chat_turn", "s:1")
    at(c, "2026-10-12T12:05")
    if refunded:
        refund_current(c, "ot-p", "t1", E1)
    at(c, "2026-10-13T09:00")
    spend(c, 20, "report_charge", "AAPL:p1")
    at(c, "2026-10-13T09:05")
    c.refund_credits(20, "report_refund", "AAPL:p1")
    at(c, "2026-10-14T09:00")
    if refunded:
        reverse(c, "ot-p", "t1", E1)
    else:
        c.ensure_credit_period()
    assert (c.total, c.used, c.spendable) == (1170, 0, 1170) if refunded else (1200, 30, 1170)


def hand_correction(c: _Credits, amount: int, reason: str, ref: str, reverses_id=None) -> None:
    """A one-off granted-pool correction as Claude writes it (SYSTEM_DESIGN_GUIDELINES "Migration
    200 leaves"): roll the period first (as refund_credits does since 200), then one ledger row
    with the pool change it records — `used` down as a refund does, `total` up for what `used`
    cannot take."""
    c.ensure_credit_period()
    back = min(amount, c.used)
    c.used -= back
    c.total += amount - back
    c._ledger(amount, amount, 0, reason, ref, reverses_id=reverses_id)


def _correction_twins(*, lost_charge: str = "", correction: int = 25, reason: str = "manual_correction",
                      spent: int = 0) -> dict:
    """Refunded account vs its never-refunded twin: chat 30, then (optionally) a 20-credit report
    whose refund never landed — charged before the clawback or inside the window — Apple refunds P1,
    a correction is written while it stands, Apple reverses the refund. {refunded: _Credits}."""
    accounts = {}
    for refunded in (True, False):
        c = account()
        apply_active(c, "ot-p", "t1", E1)
        at(c, "2026-10-10T15:00")
        spend(c, 30, "chat_turn", "s:1")
        debit = None
        if lost_charge == "before-the-clawback":
            spend(c, 20, "report_charge", "AAPL:lost")
            debit = c.ledger[-1]["id"]
        at(c, "2026-10-12T12:05")
        if refunded:
            refund_current(c, "ot-p", "t1", E1)
        at(c, "2026-10-13T09:00")
        if lost_charge == "in-the-window":
            spend(c, 20, "report_charge", "AAPL:lost")
            debit = c.ledger[-1]["id"]
        at(c, "2026-10-13T10:00")
        hand_correction(c, correction, reason, "AAPL:lost" if debit else "AAPL:fix", reverses_id=debit)
        if spent:
            spend(c, spent, "report_charge", "AAPL:p2")
        at(c, "2026-10-14T09:00")
        if refunded:
            reverse(c, "ot-p", "t1", E1)
        else:
            c.ensure_credit_period()
        assert 50 + sum(r["delta"] for r in c.ledger) == c.spendable    # account() opens unledgered at 50
        accounts[refunded] = c
    return accounts


@pytest.mark.parametrize("charged", ["before-the-clawback", "in-the-window"])
def test_a_correction_written_as_a_refund_of_its_charge_survives_the_reinstatement(charged):
    """The documented shape of a REFUND LEAK step-4 correction (final review of phase 2): one row
    with the charge's refund reason and reverses_id = the charge. Charged before the clawback's
    mark, it is added back (`pre_back`); charged in the window, it nets against its own debit.
    Both match the never-refunded twin (1200 − 30 − 20 + 20). PG: edge_200_all.sql R7b."""
    acc = _correction_twins(lost_charge=charged, correction=20, reason="report_refund")
    assert acc[True].spendable == acc[False].spendable == 1170


@pytest.mark.parametrize("spent", [0, 40], ids=["no-window-spend", "spend-above-it"])
def test_any_other_positive_row_in_the_window_survives_the_reinstatement(spent):
    """A positive granted row with a non-system reason and no reverses_id is window spend netted
    the other way. With no spend in the window the reinstated total carries it (`max(-window,
    0)`); without that term it expired with the floor (1170, not 1195 — the final mutation batch's
    survivor). With more spend than it, `used` is net of it. Both match the twin. PG: R7."""
    acc = _correction_twins(spent=spent)
    assert acc[True].spendable == acc[False].spendable == 1200 - 30 - spent + 25


@pytest.mark.parametrize("reverses", [True, False], ids=["reverses-the-charge", "no-reverses-id"])
def test_a_correction_that_reverses_its_charge_retires_it(reverses):
    """What `reverses_id` buys (verification of the final fixes): with it, a later refund attempt
    of the same charge (a retry, the reconciliation loop, a second hand pass) is the idempotent
    replay and pays nothing; without it the debit is still unpaired and is paid a second time
    (with nothing left in `used` to lower, the attempt answers capped_to_zero instead: a false
    REFUND LEAK page that invites a second correction)."""
    c = account()
    apply_active(c, "ot-p", "t1", E1)
    at(c, "2026-10-10T15:00")
    spend(c, 20, "report_charge", "AAPL:lost")
    debit = c.ledger[-1]["id"]
    at(c, "2026-10-11T10:00")
    hand_correction(c, 20, "report_refund", "AAPL:lost", reverses_id=debit if reverses else None)
    spend(c, 30, "chat_turn", "s:2")              # room in `used` for a second payment
    before = c.spendable
    at(c, "2026-10-11T10:05")
    out = c.refund_credits(20, "report_refund", "AAPL:lost")
    if reverses:
        assert out["outcome"] == "already_refunded" and not out.get("short"), out
        assert c.spendable == before
    else:
        assert out["outcome"] == "refunded" and c.spendable == before + 20, "paid twice"


def test_a_correction_rolls_the_period_first():
    """Verification of the final fixes: like refund_credits since 200 (F10), a correction rolls the
    credit period before it writes. Written into a month that already ended (no credit read since
    the ET 1st), the next roll expired it with that month's leftovers: 49, not 69."""
    c = account("2026-10-30T10:00")
    c.resets_at = _next_et_reset(c.now)
    spend(c, 20, "report_charge", "AAPL:lost")
    debit = c.ledger[-1]["id"]
    at(c, "2026-11-02T10:00")                 # the ET 1st passed with no credit read
    hand_correction(c, 20, "report_refund", "AAPL:lost", reverses_id=debit)
    spend(c, 1, "chat_turn", "s:1")           # the next read
    assert c.spendable == 50 + 20 - 1
    assert 50 + sum(r["delta"] for r in c.ledger) == c.spendable


def test_step4_starts_with_refund_credits_so_a_clawback_cover_is_not_paid_twice():
    """Last verification pass (2026-10-22): a report whose refund wrote no row (retry_rpc_failed,
    unknown) in a period Apple then refunds, with spend above the floor — the clawback's
    `revoked_cover` already wrote that spend off. Step 4 first calls refund_credits with the
    charge's own ref and amount: covered_by_clawback, nothing to hand-correct, the same as the twin
    whose refund landed in time, while the refund stands and after Apple reverses it. A hand
    correction of step 2's "owed" amount instead pays it twice while the refund stands."""
    accounts = {}
    for who in ("procedure", "twin", "naive"):
        c = account()
        apply_active(c, "ot-p", "t1", E1)
        at(c, "2026-10-10T15:00")
        spend(c, 200, "chat_turn", "s:1")
        spend(c, 20, "report_charge", "AAPL:lost")
        debit = c.ledger[-1]["id"]
        if who == "twin":
            c.refund_credits(20, "report_refund", "AAPL:lost")        # it landed in time
        at(c, "2026-10-12T12:05")
        refund_current(c, "ot-p", "t1", E1)
        at(c, "2026-10-13T09:00")
        if who == "procedure":
            out = c.refund_credits(20, "report_refund", "AAPL:lost")
            assert out["outcome"] == "covered_by_clawback", out
        elif who == "naive":
            hand_correction(c, 20, "report_refund", "AAPL:lost", reverses_id=debit)
        accounts[who] = c
    stands = {who: c.spendable for who, c in accounts.items()}
    assert stands == {"procedure": 0, "twin": 0, "naive": 20}, stands
    for c in accounts.values():
        at(c, "2026-10-14T09:00")
        reverse(c, "ot-p", "t1", E1)
    assert accounts["procedure"].spendable == accounts["twin"].spendable == 1000


@pytest.mark.parametrize("reason", ["grant", "tester_grant", "tier_upgrade"])
def test_a_correction_under_a_system_reason_is_lost_by_the_reinstatement(reason):
    """Why the runbook pins the shape: a system reason is invisible to the window, so a
    correction written under one expires with the floor when Apple reverses the refund — 25
    short of the twin, silently (PG: R7 with 'grant' gives 1170, not 1195; review of the final
    stretch). Pinned so a change to the system-reason list is decided, not drifted into."""
    acc = _correction_twins(reason=reason)
    assert acc[False].spendable == 1195 and acc[True].spendable == 1195 - 25


def _covered_setup(shift_debit_before_start: bool):
    """A period P starts (its plan_period row), then a report is charged — its debit's created_at
    optionally moved BEFORE P's applied_at (the start-time race: the spend's transaction began
    earlier but took the lock after P's start). Spend above the floor, Apple refunds P, then the
    report is refunded."""
    c = account()
    apply_active(c, "ot-r", "t1", E1)
    p = c.periods[("ot-r", E1)]
    at(c, "2026-10-05T10:00")
    spend(c, 20, "report_charge", "AAPL:race")
    if shift_debit_before_start:
        c.ledger[-1]["created_at"] = p["applied_at"] - timedelta(milliseconds=3)
    at(c, "2026-10-10T15:00")
    spend(c, 200, "chat_turn", "s:1")
    at(c, "2026-10-12T12:05")
    refund_current(c, "ot-r", "t1", E1)
    at(c, "2026-10-12T12:07")
    return c, c.refund_credits(20, "report_refund", "AAPL:race")


@pytest.mark.parametrize("raced", [False, True], ids=["in-order", "start-time-race"])
def test_charged_inside_the_period_is_decided_by_ledger_order(raced):
    """Final review #1: the debit's ledger id is after P's plan_period row in both cases, so it was
    charged in P and is covered — even when its transaction START time is before P's applied_at."""
    c, r = _covered_setup(raced)
    assert (r["outcome"], r["covered"]) == ("covered_by_clawback", 20)


def test_a_period_without_a_ledger_mark_keeps_the_timestamp_rule():
    """Periods started before 200 (and 196's backfill) have no applied_ledger_id: applied_at
    decides, exactly as before."""
    c = account()
    apply_active(c, "ot-o", "t1", E1)
    p = c.periods[("ot-o", E1)]
    p["applied_ledger_id"] = None
    at(c, "2026-10-05T10:00")
    spend(c, 20, "report_charge", "AAPL:old")
    c.ledger[-1]["created_at"] = p["applied_at"] - timedelta(milliseconds=3)
    at(c, "2026-10-10T15:00")
    spend(c, 200, "chat_turn", "s:1")
    at(c, "2026-10-12T12:05")
    refund_current(c, "ot-o", "t1", E1)
    at(c, "2026-10-12T12:07")
    r = c.refund_credits(20, "report_refund", "AAPL:old")
    assert r["outcome"] == "refunded" and "covered" not in r
