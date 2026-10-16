"""Migration 198's credit branches, pinned on the executable model of its SQL.

`test_credit_pool_isolation._Credits` models `ensure_credit_period`, `revoke_tier_credits`,
`record_subscription_transaction`, `revoke_paid_period` and `reinstate_paid_carry` as migration
198 defines them (one `subscriptions` row per Apple lineage), and it was walked differentially
against that SQL on a local Postgres — random multi-lineage lifecycles, 0 mismatches. So the
model IS the spec CI can run, and two things are pinned on it here:

1. The SQL scenario list that passed 50/50 against 198 on Postgres, ported check for check to
   model calls (each assert message is the SQL check's own name). Its pure-SQL checks — table
   and EXECUTE grants, the `max_period_end` trigger, the register's raise on a missing owner
   row — have no model counterpart; the migration's SQL-text guards live in
   test_credit_paid_periods.py.
2. The 198 branches the 2026-10-15 review (finding #18) found UNPINNED: each was a surviving
   mutant — the model could be broken there and the whole suite stayed green. Every test in the
   second half kills at least one of them, and asserts concrete numbers (balances, the key in
   force, ledger reasons), never just "something changed".

If one of these goes red after a change to `_Credits`, the model no longer matches 198's SQL:
re-walk it against Postgres before touching an expectation here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from test_credit_pool_isolation import _PLAN_CREDITS, _Credits, _next_et_reset

UTC = timezone.utc
T0 = datetime(2026, 10, 20, 16, tzinfo=UTC)
MAX, PRO, FREE = _PLAN_CREDITS["premium"], _PLAN_CREDITS["pro"], _PLAN_CREDITS["free"]


def d(days: float = 0, hours: float = 0) -> datetime:
    return T0 + timedelta(days=days, hours=hours)


def fresh(comp: Optional[str] = None, tier: str = "free") -> _Credits:
    """The SQL script's `fresh()`: a users row (tier, comp_tier) and a 50/0 user_credits row with
    tier_alloc 50 and the next ET reset, at T0."""
    c = _Credits(total=50, tier=tier, comp_tier=comp, now=T0)
    c.tier_alloc = 50
    c.resets_at = _next_et_reset(T0)
    return c


def refund(c: _Credits, txn: str, otid: str, end: Optional[datetime], signed: datetime,
           kind: str = "revoked", user_id: object = "self") -> dict:
    """The SQL script's `refund()`: register a REFUND ('revoked', revocationDate = signedDate)
    or a REFUND_REVERSED ('reversed') of `txn`."""
    owner = c.user_id if user_id == "self" else user_id
    return c.record_subscription_transaction("Production", txn, otid, owner, "premium", end,
                                             signed if kind == "revoked" else None, kind, signed)


def revoke(c: _Credits, txn: str, otid: str, end: datetime) -> dict:
    return c.revoke_paid_period("Production", txn, otid, end)


def in_force(c: _Credits) -> Optional[tuple]:
    p = c.paid_period
    return None if p is None else (p["otid"], p["end"], p["tier"])


def rows(c: _Credits, *reasons: str) -> list[tuple[str, int]]:
    return [(r["reason"], r["delta"]) for r in c.ledger if r["reason"] in reasons]


def test_the_fixture_resets_where_the_sql_script_did():
    """The SQL script seeded resets_at = 2026-11-01 04:00 UTC (Nov 1, 00:00 EDT)."""
    assert fresh().resets_at == datetime(2026, 11, 1, 4, tzinfo=UTC)


# ══ Part 1: the SQL scenario list, ported ════════════════════════════════════════════════════

# ── G1: the refunded period in force after an early renewal ──────────────────────────────

def test_g1_early_renewal_the_refunded_period_is_clawed_back_and_the_renewal_starts():
    c = fresh()
    e1, e2 = d(1), d(31)
    c.put_subscription("A", "premium", "active", e1)
    c.ensure_credit_period()
    c.used += 300
    c.put_subscription("A", "premium", "active", e2)              # early DID_RENEW
    c.ensure_credit_period()
    assert c.paid_period["end"] == e1 and c.remaining == 3700, \
        "G1 early renewal: P1 still in force before the refund"

    refund(c, "p1", "A", e1, T0)
    r = revoke(c, "p1", "A", e1)
    assert r["outcome"] == "clawed_back" and c.paid_period["end"] == e2 \
        and (c.total, c.used) == (4000, 0), "G1 early renewal: P1 clawed back, P2 starts at once with 4000"
    p1 = c.periods[("A", e1)]
    assert p1["revoked_at"] is not None and p1["revoked_remaining"] == 3700, \
        "G1 early renewal: P1 marked revoked with what the refund took"
    assert revoke(c, "p1", "A", e1)["outcome"] == "already_revoked" and c.total == 4000, \
        "G1 duplicate refund: already_revoked, nothing moves"
    assert rows(c, "tier_revoked") == [("tier_revoked", -3700)]


# ── G1: the failed REFUND delivery + a same-lineage Pro resubscribe (the pinned accepted gap) ─

def test_g1_resubscribe_a_late_refund_of_max_claws_it_back_and_starts_the_pro_period():
    c = fresh()
    m1, p1e = d(20), d(30)
    c.put_subscription("G", "premium", "active", m1)
    c.ensure_credit_period()
    c.used += 200
    c.put_subscription("G", "pro", "active", p1e)
    c.ensure_credit_period()
    assert c.paid_period["tier"] == "premium", "G1 resubscribe: Max still in force under the Pro row"
    refund(c, "m1", "G", m1, T0)
    r = revoke(c, "m1", "G", m1)
    assert r["outcome"] == "clawed_back" and c.paid_period["tier"] == "pro" and c.remaining == 1200, \
        "G1 resubscribe: Max clawed back, the Pro period starts (1200)"


# ── G2: two lineages, the higher one refunded ──────────────────────────────────────────────

def test_g2_two_lineages_the_higher_one_refunded():
    c = fresh()
    c.put_subscription("B", "pro", "active", d(25))
    c.ensure_credit_period()
    c.put_subscription("A", "premium", "active", d(28))
    c.ensure_credit_period()
    assert in_force(c) == ("A", d(28), "premium"), "G2: A (Max, another Apple ID) starts at once over B"
    c.put_subscription("A", "premium", "revoked", d(28))
    refund(c, "a1", "A", d(28), T0)
    r = revoke(c, "a1", "A", d(28))
    assert r["outcome"] == "clawed_back" and c.paid_period is None and c.total == 50, \
        "G2: A clawed back; B's key was granted once already → calendar until B renews (D7)"
    c.put_subscription("B", "pro", "active", d(55))
    c.ensure_credit_period()
    assert c.paid_period["otid"] == "B" and c.remaining == 1200, "G2: B's renewal starts B's next period"


def test_g2_a_worse_lineage_never_cuts_the_better_ones_period_short():
    c = fresh()
    c.put_subscription("A", "premium", "active", d(20))
    c.ensure_credit_period()
    c.put_subscription("B", "pro", "active", d(30))
    c.ensure_credit_period()
    assert c.paid_period["otid"] == "A", "G2: a Pro lineage beside a running Max does not start"
    assert c.total == 4000 and ("B", d(30)) not in c.periods


# ── G4: the upgrade carry, clawed back once, reinstated once ──────────────────────────────

PE = d(10)                     # the superseded Pro period's end (granted 21 days before T0)


def test_g4_the_upgrade_carry_is_clawed_back_once_and_reinstated_once():
    c = fresh()
    c.now = d(-21)
    c.put_subscription("C", "pro", "active", PE)
    c.ensure_credit_period()                                         # granted 21 d ago
    c.now = T0
    c.used += 300
    c.put_subscription("C", "premium", "active", d(30))
    c.ensure_credit_period()
    upgrade = c.periods[("C", d(30))]
    assert upgrade["carried_from"] is not None and upgrade["carried"] > 0, \
        "G4: the upgrade records the period it carried from"
    # floor(1200 x 21/31) = 812, under the 900 left: the cap binds.
    assert upgrade["carried_from"] == c.periods[("C", PE)]["id"] and upgrade["carried"] == 812
    before = c.remaining
    assert before == 4812

    refund(c, "pro1", "C", PE, T0)
    r = revoke(c, "pro1", "C", PE)
    assert r == {"outcome": "carry_clawed_back", "carry": 812} and c.remaining == before - 812, \
        "G4: refund of the superseded Pro takes the carry back"
    assert revoke(c, "pro1", "C", PE)["outcome"] == "not_in_force" and c.remaining == before - 812, \
        "G4: a second delivery takes nothing more"
    refund(c, "pro1", "C", PE, d(hours=1), kind="reversed")
    r = c.reinstate_paid_carry("C", PE)
    assert r == {"outcome": "carry_reinstated", "carry": 812} and c.remaining == before, \
        "G4: REFUND_REVERSED gives the carry back once"
    r = c.reinstate_paid_carry("C", PE)
    assert r["outcome"] == "nothing_to_reinstate" and c.remaining == before, \
        "G4: a second reversal gives nothing"
    led = [x["reason"] for x in c.ledger]
    assert led.count("tier_revoked") == 1 and led.count("plan_reinstated") == 1, \
        "G4: ledger has one tier_revoked and one plan_reinstated for the carry"
    assert rows(c, "tier_revoked", "plan_reinstated") == [("tier_revoked", -812), ("plan_reinstated", 812)]


# ── G4 against a clawed-back upgrade (reviews F6/F12): carry moves on revoked_remaining ─────

def test_f6_the_superseded_pros_refund_after_the_upgrade_was_clawed_back_reduces_revoked_remaining():
    c = fresh()
    c.now = d(-21)
    c.put_subscription("D", "pro", "active", PE)
    c.ensure_credit_period()
    c.now = T0
    c.put_subscription("D", "premium", "active", d(30))
    c.ensure_credit_period()
    upgrade = c.periods[("D", d(30))]
    carried = upgrade["carried"]
    c.put_subscription("D", "premium", "revoked", d(30))
    c.ensure_credit_period()                                         # the upgrade's own refund
    rr = upgrade["revoked_remaining"]
    refund(c, "pro-d", "D", PE, T0)
    r = revoke(c, "pro-d", "D", PE)
    rr2 = upgrade["revoked_remaining"]
    assert r["outcome"] == "carry_clawed_back_from_revoked" and rr2 == rr - min(carried, rr), \
        "F6: refund of the Pro after the upgrade was clawed back reduces its revoked_remaining"
    assert (carried, rr, rr2, r["carry"]) == (812, 4812, 4000, 812)
    assert (c.total, c.used, c.paid_period) == (50, 0, None), "the balance itself does not move"


# ── HOLD / LAPSE on the in-force lineage (review F3) ───────────────────────────────────────
# s1 / s2 / s10 of the review's surviving-mutant list: HOLD is decided on the in-force
# period's OWN lineage row (`cur_sub`), never on the ORDER BY winner.

def test_f3_s1_max_in_grace_beside_an_active_sandbox_row_holds():
    c = fresh()
    c.put_subscription("A", "premium", "active", d(2))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(3)
    c.put_subscription("A", "premium", "grace_period", d(2))
    c.put_subscription("S", "pro", "active", d(30), "Sandbox")       # wins the ORDER BY
    c.ensure_credit_period()
    assert c.remaining == 3000, "F3: Max in grace beside an active Sandbox row HOLDS"
    assert in_force(c) == ("A", d(2), "premium") and not rows(c, "monthly_reset")


def test_f3_s2_max_in_grace_beside_a_comp_floor_pro_row_holds():
    c = fresh(comp="pro")
    c.put_subscription("A", "premium", "active", d(2))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(3)
    c.put_subscription("A", "premium", "grace_period", d(2))
    c.put_subscription("C", "pro", "active", d(30))                  # at the comp floor
    c.ensure_credit_period()
    assert c.remaining == 3000, "F3: Max in grace beside a comp-floor Pro row HOLDS"
    assert in_force(c) == ("A", d(2), "premium") and ("C", d(30)) not in c.periods


def test_f3_s10_an_expired_in_force_max_beside_another_lineage_in_grace_lapses():
    c = fresh()
    c.put_subscription("A", "premium", "active", d(2))
    c.ensure_credit_period()
    c.now = d(3)
    c.put_subscription("A", "premium", "expired", d(2))
    c.put_subscription("B", "pro", "grace_period", d(-1))             # wins the ORDER BY
    c.ensure_credit_period()
    assert c.total == 50, "F3: an expired in-force Max beside another lineage in grace LAPSES"
    assert c.paid_period is None and c.used == 0
    assert [(r["reason"], r["delta"]) for r in c.ledger[-2:]] == [
        ("monthly_expired", -4000), ("monthly_reset", 50)
    ], "the Max leftovers expire with the plan, then the calendar allowance"


# ── review F4: a late renewal of the in-force lineage, another lineage waiting ─────────────

def test_f4_another_lineage_waits_while_the_in_force_ones_renewal_is_pending():
    c = fresh()
    c.put_subscription("A", "premium", "active", d(1))
    c.ensure_credit_period()
    c.put_subscription("B", "pro", "active", d(20))
    c.now = d(1, hours=1)
    c.ensure_credit_period()
    assert c.paid_period["otid"] == "A", "F4: B (Pro) waits while A's renewal is pending (A active past its end)"
    assert c.remaining == 4000 and ("B", d(20)) not in c.periods
    c.put_subscription("A", "premium", "active", d(31))
    c.ensure_credit_period()
    assert c.paid_period["otid"] == "A" and c.total == 4000, \
        "F4: A's renewal starts with exactly 4000, no carry"
    assert c.periods[("A", d(31))]["carried"] == 0 and c.used == 0


# ── review F5: the reversal wins the race against the clawback ─────────────────────────────
# The SQL ran this with two connections and a held balance lock; here the reversal is simply
# recorded first, which is the order the lock produces.

def test_f5_a_reversal_recorded_before_the_clawback_keeps_the_credits():
    c = fresh()
    e1, e2 = d(1), d(31)
    c.put_subscription("A", "premium", "active", e1)
    c.ensure_credit_period()
    c.put_subscription("A", "premium", "active", e2)
    c.ensure_credit_period()
    refund(c, "p1", "A", e1, T0)
    refund(c, "p1", "A", e1, d(hours=1 / 12), kind="reversed")      # +5 min
    out = revoke(c, "p1", "A", e1)
    assert out == {"outcome": "reversed"} and c.paid_period["end"] == e1, \
        "race: a reversal committed first → 'reversed', credits kept"
    assert c.periods[("A", e1)].get("revoke_requested_at") is None and c.remaining == 4000


# ── the reinstatement guard ─────────────────────────────────────────────────────────────────

def test_guard_a_period_is_reinstated_only_once_the_register_holds_the_reversal():
    c = fresh()
    e2 = d(31)
    c.put_subscription("A", "premium", "active", e2)
    c.ensure_credit_period()
    refund(c, "x", "A", e2, T0)
    r = revoke(c, "x", "A", e2)                   # the row STILL entitles under the refunded key
    assert r["outcome"] == "clawed_back" and c.paid_period is None and c.ensure_credit_period() == 50, \
        "guard: a clawed-back period whose row still entitles is not reinstated (no claw/reinstate loop)"
    refund(c, "x", "A", e2, d(hours=2), kind="reversed")
    c.ensure_credit_period()
    assert c.paid_period["otid"] == "A", "guard: once the register holds the reversal, the period is reinstated"
    assert c.periods[("A", e2)]["revoke_requested_at"] is None, "guard: reinstatement clears revoke_requested_at"
    rem = c.remaining
    assert c.ensure_credit_period() == rem and c.paid_period["otid"] == "A", \
        "guard: the next call does not claw it back again"
    assert rem == 4000 and rows(c, "plan_reinstated") == [("plan_reinstated", 4000)]


# ── record_subscription_transaction ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind, signed", [("reversed", None), ("nope", T0)], ids=["no date", "bad kind"])
def test_register_a_malformed_record_raises_and_writes_nothing(kind, signed):
    c = fresh()
    with pytest.raises(ValueError):
        c.record_subscription_transaction("Production", "t", "o", c.user_id, "pro", None, None,
                                          kind, signed)
    assert c.register == {}


def test_register_a_token_naming_no_account_records_no_owner():
    """The SQL resolves an owner that is not a users row to NULL; the model takes the resolved
    owner, so it is ported as user_id None."""
    r1 = refund(fresh(), "t2", "o", None, T0, user_id=None)
    assert r1["user_id"] is None and r1["refunded"], "register: a token naming no account records no owner"


def test_register_signed_dates_decide_order_independently_and_never_rewind():
    c = fresh()
    refund(c, "t3", "o", None, d(hours=3), kind="reversed")
    b2 = refund(c, "t3", "o", None, d(hours=1))
    assert not b2["refunded"], "register: a reversal recorded BEFORE an older refund wins (order-independent)"
    again = refund(c, "t3", "o", None, d(hours=5))
    assert again["refunded"], "register: a second refund after the reversal is refunded again"
    old = refund(c, "t3", "o", None, T0)                             # an older duplicate never rewinds
    assert old["refunded"] and old["revoked_signed_at"] is not None, \
        "register: an older duplicate never rewinds the signedDates"
    assert (old["revoked_signed_at"], old["reversed_signed_at"]) == (d(hours=5), d(hours=3))


# ── review #14/#5b: the in-force lineage downgrades at renewal beside another Max lineage ──

def test_14_the_in_force_lineages_own_pro_renewal_starts_then_the_other_max_upgrades_over_it():
    c = fresh()
    c.put_subscription("A", "premium", "active", d(10))
    c.ensure_credit_period()                                         # P_A1 Max
    c.put_subscription("B", "premium", "active", d(25))
    c.ensure_credit_period()                                         # B waits (same tier)
    assert c.paid_period["otid"] == "A", "#14: B (same tier) waits beside a running A"
    c.put_subscription("A", "pro", "active", d(40))                  # A renews as Pro
    c.now = d(11)
    c.ensure_credit_period()
    assert in_force(c)[0::2] == ("A", "pro"), "#14: A's own Pro renewal starts (no freeze)"
    c.ensure_credit_period()
    assert in_force(c)[0::2] == ("B", "premium") and c.total >= 4000, \
        "#14: then B's Max starts as an upgrade over A's Pro: ONE allocation"
    assert [p["source"] for k, p in c.periods.items() if k[0] == "B"] == ["upgrade"], \
        "#14: B's period is an upgrade"


# ── review #5a: two same-tier lineages, the other ending later under early renewals ──────────

def test_5a_every_in_force_period_is_granted_while_a_same_tier_lineage_renews_later():
    c = fresh()
    a1 = d(2)
    c.put_subscription("A", "pro", "active", a1)
    c.ensure_credit_period()
    c.put_subscription("B", "pro", "active", a1 + timedelta(hours=12))
    c.ensure_credit_period()
    granted = []
    for k in range(1, 5):
        a_end = a1 + timedelta(days=30 * k)
        b_end = a1 + timedelta(hours=12) + timedelta(days=30 * k)
        c.now = a1 + timedelta(days=30 * (k - 1)) - timedelta(hours=20)   # both renew ~20 h early
        c.put_subscription("A", "pro", "active", a_end)
        c.put_subscription("B", "pro", "active", b_end)
        c.ensure_credit_period()
        c.now = a1 + timedelta(days=30 * (k - 1)) + timedelta(hours=1)
        c.ensure_credit_period()
        granted.append(c.paid_period["end"])
    ends = sorted(e for o, e in c.periods if o == "A")
    assert len(ends) == 5, "#5a: every A period is granted while B renews later"
    assert granted == ends[1:] and c.remaining == 1200
    assert not [k for k in c.periods if k[0] == "B"], "#5a: B never gets a second allocation"


# ── review #26/#5 grace: A recovers in billing grace after B (same tier) was bought ─────────

def test_26_a_recovery_in_billing_grace_starts_its_period_although_b_ends_later():
    c = fresh()
    c.put_subscription("A", "pro", "active", d(1))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(2)
    c.put_subscription("A", "pro", "grace_period", d(1))
    c.ensure_credit_period()
    assert c.paid_period["end"] == d(1) and c.remaining == 200, "#26: A in grace holds"
    c.now = d(5)
    c.put_subscription("B", "pro", "active", d(35))
    c.ensure_credit_period()
    assert c.paid_period["end"] == d(1) and c.remaining == 200, "#26: B (same tier) waits while A is in grace"
    c.now = d(9)
    c.put_subscription("A", "pro", "active", d(31))
    c.ensure_credit_period()
    assert c.paid_period["otid"] == "A" and c.paid_period["end"] == d(31) and c.remaining == 1200, \
        "#26: A's recovery starts A2 (anchor kept, ends before B)"
    assert ("B", d(35)) not in c.periods


# ══ Part 2: the 198 branches no test pinned (review 2026-10-15, finding #18) ═════════════════

# ── HOLD on the in-force lineage's own row: s11, a late renewal beside another lineage ──────

@pytest.mark.parametrize("comp, other_env", [(None, "Sandbox"), ("pro", "Production")],
                         ids=["beside-a-sandbox-row", "beside-a-comp-floor-row"])
def test_s11_a_late_renewal_of_the_in_force_lineage_holds_beside_another_lineage(comp, other_env):
    """A's Max period has ended and Apple's DID_RENEW is late (A's row still reads `active` with
    the old end). The other lineage wins the ORDER BY but cannot carry a period (Sandbox, or at
    the comp floor) — read off the winner, HOLD would lapse A's leftovers."""
    c = fresh(comp=comp)
    c.put_subscription("A", "premium", "active", d(2))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(2, hours=6)
    c.put_subscription("B", "pro", "active", d(30), other_env)
    assert c.ensure_credit_period() == 3000
    assert in_force(c) == ("A", d(2), "premium")
    assert ("monthly_expired", -3000) not in rows(c, "monthly_expired"), "the leftovers are held"

    c.put_subscription("A", "premium", "active", d(32))              # the renewal lands
    c.ensure_credit_period()
    assert in_force(c) == ("A", d(32), "premium") and (c.total, c.used) == (4000, 0)
    assert c.periods[("A", d(32))]["carried"] == 0
    assert rows(c, "monthly_expired", "plan_period")[-2:] == [("monthly_expired", -3000),
                                                              ("plan_period", 4000)]


# ── `cur_holds`: its Production condition and its comp-floor condition ─────────────────────

@pytest.mark.parametrize("env, holds", [(None, True), ("Production", True), ("Sandbox", False)],
                         ids=["env-null-is-production", "production", "sandbox"])
def test_the_in_force_lineage_holds_only_while_its_own_row_is_production(env, holds):
    """`cur_holds` reads the in-force lineage's OWN environment (NULL = written before 196 =
    Production). A row that now reads Sandbox renews every few minutes on TestFlight and must
    not hold a Production period's leftovers."""
    c = fresh()
    c.put_subscription("A", "premium", "active", d(2))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(3)
    c.put_subscription("A", "premium", "grace_period", d(2), env)
    c.ensure_credit_period()
    if holds:
        assert in_force(c) == ("A", d(2), "premium") and (c.total, c.used) == (4000, 1000)
    else:
        assert c.paid_period is None and (c.total, c.used) == (50, 0)
        assert rows(c, "monthly_expired")[-1] == ("monthly_expired", -3000)


def test_the_in_force_lineage_holds_only_while_its_own_tier_is_above_the_comp_floor():
    """Comp Pro account: Max in force, then A renews as Pro and the charge fails (billing
    retry). Pro is not above the Pro floor, so nothing paid remains to hold: the Max leftovers
    lapse to the comp allowance."""
    c = fresh(comp="pro")
    c.put_subscription("A", "premium", "active", d(2))
    c.ensure_credit_period()
    assert in_force(c) == ("A", d(2), "premium")
    c.used += 1000
    c.now = d(3)
    c.put_subscription("A", "pro", "billing_retry", d(2))
    c.ensure_credit_period()
    assert c.paid_period is None and (c.total, c.used, c.tier_alloc) == (1200, 0, 1200)
    assert c.ledger[-2]["reason"] == "monthly_expired" and c.ledger[-2]["delta"] == -3000
    assert c.ledger[-1]["reason"] == "monthly_reset" and c.ledger[-1]["delta"] == 1200


# ── D2: another lineage's same-or-lower tier waits while the in-force lineage holds ─────────

@pytest.mark.parametrize("other_tier", ["pro", "premium"], ids=["lower-tier", "same-tier"])
def test_d2_another_lineage_does_not_start_while_the_in_force_max_is_in_billing_grace(other_tier):
    """Max (A) in billing retry; the user subscribes again on a second Apple ID (B). Starting B
    now would expire A's leftovers and grant a second allocation for one paid month."""
    c = fresh()
    c.put_subscription("A", "premium", "active", d(2))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(3)
    c.put_subscription("A", "premium", "billing_retry", d(2))
    c.put_subscription("B", other_tier, "active", d(33))
    assert c.ensure_credit_period() == 3000, "the leftovers are held"
    assert in_force(c) == ("A", d(2), "premium") and ("B", d(33)) not in c.periods
    assert rows(c, "monthly_expired") == [("monthly_expired", -50)], "only the opening calendar 50 expired"

    c.now = d(4)
    c.put_subscription("A", "premium", "active", d(32))              # A's charge recovers
    c.ensure_credit_period()
    assert in_force(c) == ("A", d(32), "premium") and (c.total, c.used) == (4000, 0), \
        "the in-force lineage's own renewal is never blocked by its own hold"
    assert ("B", d(33)) not in c.periods


def test_d2_a_higher_tier_on_another_lineage_starts_at_once_over_a_held_period():
    c = fresh()
    c.put_subscription("A", "pro", "active", d(2))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(3)
    c.put_subscription("A", "pro", "grace_period", d(2))
    c.put_subscription("B", "premium", "active", d(33))
    c.ensure_credit_period()
    assert in_force(c) == ("B", d(33), "premium") and (c.total, c.used) == (4000, 0)
    b = c.periods[("B", d(33))]
    assert (b["source"], b["carried"]) == ("new_period", 0), "A's period had ended: nothing carries"
    assert rows(c, "monthly_expired")[-1] == ("monthly_expired", -200)


# ── The winner's ORDER BY, keys 2, 3 and 4 ──────────────────────────────────────────────────

def test_winner_key_2_a_row_that_can_carry_a_period_now_beats_an_entitling_grace_row():
    """No period in force; a higher-tier Max row in billing grace and a new Pro purchase. The
    Pro row is the only one that can carry a period NOW, so it wins and starts."""
    c = fresh()
    c.put_subscription("A", "premium", "grace_period", d(-1))
    c.put_subscription("B", "pro", "active", d(30))
    c.ensure_credit_period()
    assert in_force(c) == ("B", d(30), "pro") and (c.total, c.used) == (1200, 0)
    assert rows(c, "plan_period") == [("plan_period", 1200)]


def test_winner_key_3_an_entitling_row_beats_a_higher_tier_expired_one():
    """Key 3 is visible only where the winner decides HOLD: the in-force period's lineage has
    no row any more (the one row was re-pointed before 198), so 196's rule holds on the winning
    row. Pro in billing grace must win over an expired Max and hold the leftovers."""
    c = fresh()
    c.set_subscription("OLD", "premium", "active", d(2))
    c.ensure_credit_period()
    c.used += 1000
    c.now = d(3)
    c.set_subscription("A", "premium", "expired", d(-5))             # re-pointed: OLD's row is gone
    c.put_subscription("B", "pro", "grace_period", d(-1))
    assert c.ensure_credit_period() == 3000
    assert in_force(c) == ("OLD", d(2), "premium") and c.total == 4000


def test_winner_key_4_production_beats_a_higher_tier_sandbox_row():
    c = fresh()
    c.put_subscription("S", "premium", "active", d(30), "Sandbox")
    c.put_subscription("B", "pro", "active", d(30))
    c.ensure_credit_period()
    assert in_force(c) == ("B", d(30), "pro") and (c.total, c.used) == (1200, 0)


# ── The register guard on reinstatement ─────────────────────────────────────────────────────

def test_a_period_the_register_still_holds_a_refund_of_is_never_reinstated():
    """The Max refund claws the period back; Apple's row then entitles again under the SAME key
    (the row was never marked revoked here). While the register holds the refund — no reversal,
    or a reversal signed BEFORE the refund — no ensure reinstates it, however often it runs."""
    c = fresh()
    c.put_subscription("A", "premium", "active", d(30))
    c.ensure_credit_period()
    c.used += 500
    refund(c, "a1", "A", d(30), d(hours=1))
    assert revoke(c, "a1", "A", d(30)) == {"outcome": "clawed_back"}
    period = c.periods[("A", d(30))]
    assert period["revoked_remaining"] == 3500
    refund(c, "a1", "A", d(30), T0, kind="reversed")                 # signed BEFORE the refund
    for _ in range(3):
        assert c.ensure_credit_period() == 0                         # floor 500 - writeoff 450 = 50/50
        assert c.paid_period is None and period["revoked_at"] is not None
    assert not rows(c, "plan_reinstated")

    refund(c, "a1", "A", d(30), d(hours=2), kind="reversed")         # the real reversal
    assert c.ensure_credit_period() == 3500
    assert c.paid_period is period and (c.total, c.used, c.tier_alloc) == (3500, 0, 4000)
    assert rows(c, "plan_reinstated") == [("plan_reinstated", 3500)]


def test_the_reinstated_period_is_not_clawed_back_again_by_its_own_request():
    """revoke_paid_period stamps `revoke_requested_at`; the reinstatement must clear it, or the
    next ensure reads the stale request and claws the period back a second time."""
    c = fresh()
    c.put_subscription("A", "premium", "active", d(30))
    c.ensure_credit_period()
    refund(c, "a1", "A", d(30), T0)
    revoke(c, "a1", "A", d(30))
    assert c.periods[("A", d(30))]["revoke_requested_at"] is not None
    refund(c, "a1", "A", d(30), d(hours=1), kind="reversed")
    for _ in range(3):
        assert c.ensure_credit_period() == 4000
    assert in_force(c) == ("A", d(30), "premium") and c.periods[("A", d(30))]["revoked_at"] is None
    assert rows(c, "tier_revoked") == [("tier_revoked", -3950)], "clawed back exactly once"


# ── revoke_paid_period: the register re-check and the ownership check ──────────────────────

def test_revoke_paid_period_rechecks_the_register_and_keeps_a_reversed_period():
    c = fresh()
    c.put_subscription("A", "premium", "active", d(30))
    c.ensure_credit_period()
    c.used += 100
    assert revoke(c, "never", "A", d(30)) == {"outcome": "not_recorded"}
    refund(c, "a1", "A", d(30), T0)
    refund(c, "a1", "A", d(30), d(hours=1), kind="reversed")
    assert revoke(c, "a1", "A", d(30)) == {"outcome": "reversed"}
    assert in_force(c) == ("A", d(30), "premium") and c.remaining == 3900
    assert c.periods[("A", d(30))].get("revoke_requested_at") is None and not rows(c, "tier_revoked")
    refund(c, "b1", "A", d(60), T0)
    assert revoke(c, "b1", "A", d(60)) == {"outcome": "no_period"}


def test_revoke_paid_period_never_claws_back_a_period_granted_to_another_account():
    """Two accounts share the period table's UNIQUE key and the register (one Postgres)."""
    periods: dict = {}
    register: dict = {}
    owner = _Credits(total=50, now=T0, periods=periods, register=register)
    other = _Credits(total=50, now=T0, periods=periods, register=register)
    for c in (owner, other):
        c.tier_alloc, c.resets_at = 50, _next_et_reset(T0)
    owner.put_subscription("A", "premium", "active", d(30))
    owner.ensure_credit_period()
    owner.used += 100
    refund(other, "a1", "A", d(30), T0)                              # recorded against `other`
    assert revoke(other, "a1", "A", d(30)) == {"outcome": "other_owner"}
    assert in_force(owner) == ("A", d(30), "premium") and owner.remaining == 3900
    p = periods[("A", d(30))]
    assert p["revoked_at"] is None and p.get("revoke_requested_at") is None
    assert (other.total, other.used, other.paid_period) == (50, 0, None) and other.ledger == []
    assert other.reinstate_paid_carry("A", d(30)) == {"outcome": "no_period"}


# ── The upgrade carry: clawed back from / reinstated into a clawed-back upgrade, and x = 0 ──

MAX_END = d(30)


def upgraded(spent_on_pro: int = 0) -> _Credits:
    """Pro (lineage C) granted 21 days before T0 and ending at PE, upgraded to Max at T0. The
    carry is min(what is left, floor(1200 x 21/31) = 812)."""
    c = fresh()
    c.now = d(-21)
    c.put_subscription("C", "pro", "active", PE)
    c.ensure_credit_period()
    c.now = T0
    c.used += spent_on_pro
    c.put_subscription("C", "premium", "active", MAX_END)
    c.ensure_credit_period()
    assert in_force(c) == ("C", MAX_END, "premium")
    return c


def refund_the_upgrade(c: _Credits) -> dict:
    """Apple refunds the Max: the row reads revoked under its key; ensure claws it back."""
    refund(c, "max1", "C", MAX_END, d(hours=1))
    c.put_subscription("C", "premium", "revoked", MAX_END)
    c.ensure_credit_period()
    assert c.paid_period is None
    return c.periods[("C", MAX_END)]


def reverse_the_upgrades_refund(c: _Credits) -> int:
    refund(c, "max1", "C", MAX_END, d(hours=3), kind="reversed")
    c.put_subscription("C", "premium", "active", MAX_END)
    c.ensure_credit_period()
    assert in_force(c) == ("C", MAX_END, "premium")
    return c.remaining


def test_order_1_the_upgrade_is_refunded_first_then_the_superseded_pro():
    """The carry is taken out of the clawed-back upgrade's revoked_remaining, so a later reversal
    of the Max refund gives back the Max allocation WITHOUT the refunded Pro's carry."""
    c = upgraded()
    up = refund_the_upgrade(c)
    assert up["revoked_remaining"] == 4812
    refund(c, "pro1", "C", PE, d(hours=2))
    assert revoke(c, "pro1", "C", PE) == {"outcome": "carry_clawed_back_from_revoked", "carry": 812}
    assert up["revoked_remaining"] == 4000 and (c.total, c.used) == (50, 0)
    assert revoke(c, "pro1", "C", PE) == {"outcome": "not_in_force"}, "taken once"
    assert reverse_the_upgrades_refund(c) == 4000
    assert rows(c, "plan_reinstated") == [("plan_reinstated", 4000)]


def test_order_1_then_the_pros_reversal_puts_the_carry_back_into_revoked_remaining():
    c = upgraded()
    up = refund_the_upgrade(c)
    refund(c, "pro1", "C", PE, d(hours=2))
    revoke(c, "pro1", "C", PE)
    refund(c, "pro1", "C", PE, d(hours=2, days=1), kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "carry_reinstated_to_revoked", "carry": 812}
    assert up["revoked_remaining"] == 4812 and (c.total, c.used) == (50, 0)
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "nothing_to_reinstate"}
    assert reverse_the_upgrades_refund(c) == 4812, "both refunds reversed: everything back"


def test_order_2_the_pro_is_refunded_while_the_upgrade_runs_then_the_upgrade():
    c = upgraded()
    refund(c, "pro1", "C", PE, d(hours=1))
    assert revoke(c, "pro1", "C", PE) == {"outcome": "carry_clawed_back", "carry": 812}
    assert (c.total, c.used) == (4000, 0)
    up = refund_the_upgrade(c)
    assert up["revoked_remaining"] == 4000, "the refund took what was left — the carry already gone"
    refund(c, "pro1", "C", PE, d(hours=2), kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "carry_reinstated_to_revoked", "carry": 812}
    assert up["revoked_remaining"] == 4812 and (c.total, c.used) == (50, 0)
    assert reverse_the_upgrades_refund(c) == 4812
    assert rows(c, "tier_revoked")[0] == ("tier_revoked", -812)


@pytest.mark.parametrize("spent_since_upgrade, taken", [(0, 812), (4500, 312), (4812, 0)],
                         ids=["full-carry", "partly-spent", "all-spent-x0"])
def test_the_carry_clawback_never_takes_more_than_is_left(spent_since_upgrade, taken):
    """x = min(carried, total - used): credits already spent are a loss, never a negative pool.
    A clawback of 0 writes no ledger row but is still recorded, so its reversal gives back 0 —
    never the recorded carry."""
    c = upgraded()
    c.used += spent_since_upgrade
    refund(c, "pro1", "C", PE, d(hours=1))
    assert revoke(c, "pro1", "C", PE) == {"outcome": "carry_clawed_back", "carry": taken}
    up = c.periods[("C", MAX_END)]
    assert (up["carry_revoked"], up["carry_revoked_at"] is not None) == (taken, True)
    assert (c.total, c.used) == (4812 - taken, spent_since_upgrade)
    assert rows(c, "tier_revoked") == ([("tier_revoked", -taken)] if taken else [])
    refund(c, "pro1", "C", PE, d(hours=2), kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "carry_reinstated", "carry": taken}
    assert (c.total, c.used) == (4812, spent_since_upgrade)
    assert rows(c, "plan_reinstated") == ([("plan_reinstated", taken)] if taken else [])


def test_a_carry_of_zero_is_clawed_back_and_reinstated_as_zero():
    """The whole Pro was spent before the upgrade: the upgrade carried 0 but still points at
    the Pro period, so its refund and reversal resolve (once each) without moving a credit."""
    c = upgraded(spent_on_pro=1200)
    up = c.periods[("C", MAX_END)]
    assert (up["carried"], c.total, c.used) == (0, 4000, 0)
    refund(c, "pro1", "C", PE, d(hours=1))
    assert revoke(c, "pro1", "C", PE) == {"outcome": "carry_clawed_back", "carry": 0}
    refund(c, "pro1", "C", PE, d(hours=2), kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "carry_reinstated", "carry": 0}
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "nothing_to_reinstate"}
    assert (c.total, c.used) == (4000, 0) and not rows(c, "tier_revoked", "plan_reinstated")


def test_a_carry_clawed_back_from_an_emptied_upgrade_leaves_revoked_remaining_at_zero():
    c = upgraded()
    c.used += 4812                                                   # spent it all
    up = refund_the_upgrade(c)
    assert up["revoked_remaining"] == 0 and (c.total, c.used) == (50, 50)
    refund(c, "pro1", "C", PE, d(hours=2))
    assert revoke(c, "pro1", "C", PE) == {"outcome": "carry_clawed_back_from_revoked", "carry": 0}
    assert up["revoked_remaining"] == 0, "never below zero"
    refund(c, "pro1", "C", PE, d(hours=2, days=1), kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "carry_reinstated_to_revoked", "carry": 0}
    assert up["revoked_remaining"] == 0


# ── The retarget loop (one more model-level case beside the three in test_iap_lineage_rows) ──

def test_the_retarget_starts_the_in_force_lineages_renewal_when_a_later_ending_same_tier_wins():
    """Max A in force; Max B (same tier, second Apple ID) waits. A renews ON TIME with an end
    before B's, so B still wins the ORDER BY (the later end) but may not start: A's own next key
    is evaluated and starts. Without it the pool would HOLD A's ended period."""
    c = fresh()
    c.put_subscription("A", "premium", "active", d(5))
    c.ensure_credit_period()
    c.used += 2500
    c.put_subscription("B", "premium", "active", d(40))
    c.ensure_credit_period()
    assert in_force(c) == ("A", d(5), "premium")
    c.now = d(5, hours=1)
    c.put_subscription("A", "premium", "active", d(35))
    assert c.ensure_credit_period() == 4000
    assert in_force(c) == ("A", d(35), "premium") and ("B", d(40)) not in c.periods
    assert rows(c, "monthly_expired")[-1] == ("monthly_expired", -1500)


# ── The <<pick>> loop's two other exits: the winner STARTS, or the winner is REINSTATED ───────
# The retarget fires only when the winner does neither. Drop either exit and the in-force
# lineage's own renewal is started instead — the winner's period is skipped (start) or can never
# come back while that renewal runs (reinstate needs nothing running).

def test_the_pick_loop_keeps_a_winner_that_can_start_over_the_held_lineages_renewal():
    """Pro lineage A is in force, its period has ended and Apple's renewal has landed (active,
    new end: A HOLDS); a Max on a second Apple ID (B) is active too and wins the ORDER BY on
    tier. B can start — a higher tier than A's period — so the loop exits on `start_paid`: B's
    Max starts ("One allocation", 2026-10-14), and A's renewal is not granted on top of it."""
    c = fresh()
    c.put_subscription("A", "pro", "active", d(2))
    c.ensure_credit_period()
    c.used += 200
    c.now = d(3)                                                     # A's period has ended
    c.put_subscription("A", "pro", "active", d(32))                  # A's renewal
    c.put_subscription("B", "premium", "active", d(33))              # B wins on tier
    assert c.ensure_credit_period() == 4000
    assert in_force(c) == ("B", d(33), "premium") and (c.total, c.used, c.tier_alloc) == (4000, 0, 4000)
    b = c.periods[("B", d(33))]
    assert (b["source"], b["carried"], b["carried_from"]) == ("new_period", 0, None), \
        "A's period had ended: B is a new period, nothing carries"
    assert ("A", d(32)) not in c.periods, "A's own next key was not evaluated instead of B's"
    assert rows(c, "monthly_expired", "plan_period") == [
        ("monthly_expired", -50), ("plan_period", 1200),             # A's period
        ("monthly_expired", -1000), ("plan_period", 4000),           # A's leftovers end; B starts
    ]
    assert c.ensure_credit_period() == 4000 and len(c.periods) == 2, "and it stays that way"


def test_the_pick_loop_keeps_a_winner_that_is_reinstated_over_the_held_lineages_renewal():
    """Max lineage B was refunded (clawed back, keyed); a Pro lineage A then ran a period, which
    has ended with A's renewal landed (A HOLDS). Apple now reverses B's refund — the register
    holds the reversal and B's row entitles again under the same key — so B wins on tier and is
    REINSTATED with exactly what the refund took: the loop exits on `reinstate`. Retargeted to
    A's renewal instead, B could not come back while A ran (a reinstatement needs nothing running)."""
    c = fresh()
    c.put_subscription("B", "premium", "active", d(30))
    c.ensure_credit_period()
    c.used += 100
    c.now = d(1)
    c.put_subscription("B", "premium", "revoked", d(30))
    refund(c, "b1", "B", d(30), d(1))
    assert revoke(c, "b1", "B", d(30)) == {"outcome": "clawed_back"}
    assert (c.total, c.used, c.paid_period) == (50, 50, None)        # floor 100, 50 written off
    assert c.periods[("B", d(30))]["revoked_remaining"] == 3900

    c.now = d(2)
    c.put_subscription("A", "pro", "active", d(5))
    c.ensure_credit_period()
    assert in_force(c) == ("A", d(5), "pro") and (c.total, c.used) == (1200, 0)
    c.used += 300

    c.now = d(6)                                                     # A's period has ended
    c.put_subscription("A", "pro", "active", d(35))                  # A's renewal
    refund(c, "b1", "B", d(30), d(6), kind="reversed")               # REFUND_REVERSED of B
    c.put_subscription("B", "premium", "active", d(30))
    assert c.ensure_credit_period() == 3900
    assert in_force(c) == ("B", d(30), "premium")
    assert (c.total, c.used, c.tier_alloc, c.resets_at) == (3900, 0, 4000, d(30))
    b = c.periods[("B", d(30))]
    assert (b["revoked_at"], b["revoked_remaining"], b["revoke_requested_at"], b["reinstated_at"]) == \
        (None, None, None, d(6))
    assert ("A", d(35)) not in c.periods, "A's own next key was not evaluated instead of B's"
    assert rows(c, "monthly_expired", "plan_period", "tier_revoked", "plan_reinstated") == [
        ("monthly_expired", -50), ("plan_period", 4000), ("tier_revoked", -3900),
        ("plan_period", 1200), ("monthly_expired", -900), ("plan_reinstated", 3900),
    ]
    assert c.ensure_credit_period() == 3900 and in_force(c)[0] == "B"


# ── The carry state machine: claw once per refund, give back once per reversal ────────────────
# carry_revoked_at is set while the carry is clawed back: revoke_paid_period claws only while it
# is NULL, every reinstatement outcome clears it, and reinstate_paid_carry re-checks the register
# under the balance lock. Run in both places the carry can sit: the balance while the upgrade is
# in force, the upgrade's revoked_remaining once the upgrade itself was clawed back. 4812 = the
# Max allocation + the 812 carry in both.

_CARRY_MODES = pytest.mark.parametrize("upgrade_clawed_back", [False, True],
                                       ids=["upgrade-in-force", "upgrade-clawed-back"])


def carry_cycle(upgrade_clawed_back: bool):
    """(c, upgrade period, held(), claw outcome, give-back outcome) for one mode."""
    c = upgraded()
    up = c.periods[("C", MAX_END)]
    if upgrade_clawed_back:
        refund_the_upgrade(c)                                        # Max refund: -4762, rr 4812
        return (c, up, lambda: up["revoked_remaining"],
                "carry_clawed_back_from_revoked", "carry_reinstated_to_revoked")
    return c, up, lambda: c.total, "carry_clawed_back", "carry_reinstated"


def at(c: _Credits, hours: float) -> datetime:
    """Advance the clock to T0 + `hours` (deliveries arrive in order) and return it."""
    c.now = d(hours=hours)
    return c.now


@_CARRY_MODES
def test_a_refund_after_a_reversal_takes_the_carry_back_again(upgrade_clawed_back):
    """Apple refunds the Pro the upgrade carried from (T1), reverses that refund (T2), then
    refunds it AGAIN (T3), and reverses again (T4): the carry goes, comes back, goes again and
    comes back again — never stuck clawed, never stuck given back."""
    c, up, held, claw, give = carry_cycle(upgrade_clawed_back)
    assert held() == 4812
    t1 = at(c, 2)
    refund(c, "pro1", "C", PE, t1)
    assert revoke(c, "pro1", "C", PE) == {"outcome": claw, "carry": 812} and held() == 4000
    t2 = at(c, 4)
    refund(c, "pro1", "C", PE, t2, kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": give, "carry": 812} and held() == 4812
    assert (up["carry_revoked_at"], up["carry_reinstated_at"]) == (None, t2)
    t3 = at(c, 6)
    assert refund(c, "pro1", "C", PE, t3)["refunded"]                # refunded again
    assert revoke(c, "pro1", "C", PE) == {"outcome": claw, "carry": 812} and held() == 4000, \
        "a refund after a reversal claws the carry back AGAIN"
    assert (up["carry_revoked"], up["carry_revoked_at"]) == (812, t3)
    t4 = at(c, 8)
    refund(c, "pro1", "C", PE, t4, kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": give, "carry": 812} and held() == 4812
    assert (up["carry_revoked_at"], up["carry_reinstated_at"]) == (None, t4)
    if upgrade_clawed_back:
        assert (c.total, c.used, c.paid_period) == (50, 0, None), "the balance never moved"
        assert rows(c, "tier_revoked", "plan_reinstated") == [("tier_revoked", -4762)]
        assert reverse_the_upgrades_refund(c) == 4812, "the Max reversal then gives back the carry too"
    else:
        assert (c.total, c.used) == (4812, 0)
        assert rows(c, "tier_revoked", "plan_reinstated") == [
            ("tier_revoked", -812), ("plan_reinstated", 812)] * 2


@_CARRY_MODES
def test_a_reversal_that_arrives_after_a_later_refund_gives_nothing_back(upgrade_clawed_back):
    """Apple signs refund T1, reversal T2, refund T3, but they ARRIVE T1, T3, T2. At T3's arrival
    the carry is already clawed back (nothing more is taken); the T2 reversal is older than T3, so
    the register still holds a refund and reinstate_paid_carry answers 'still_refunded' — even if
    a caller asks. Only a reversal signed after T3 gives the carry back."""
    c, up, held, claw, give = carry_cycle(upgrade_clawed_back)
    t1, t2, t3 = d(hours=2), d(hours=4), d(hours=6)                  # Apple's signedDates
    at(c, 2)
    refund(c, "pro1", "C", PE, t1)
    assert revoke(c, "pro1", "C", PE) == {"outcome": claw, "carry": 812} and held() == 4000
    at(c, 7)
    refund(c, "pro1", "C", PE, t3)                                   # the second refund, early
    assert revoke(c, "pro1", "C", PE) == {"outcome": "not_in_force"} and held() == 4000, \
        "clawed back already: not twice"
    at(c, 8)
    assert refund(c, "pro1", "C", PE, t2, kind="reversed")["refunded"], "T3 is newer than T2"
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "still_refunded"} and held() == 4000
    assert (up["carry_revoked"], up["carry_revoked_at"], up["carry_reinstated_at"]) == (812, d(hours=2), None)
    assert not rows(c, "plan_reinstated")
    at(c, 9)
    refund(c, "pro1", "C", PE, d(hours=9), kind="reversed")          # a reversal of T3
    assert c.reinstate_paid_carry("C", PE) == {"outcome": give, "carry": 812} and held() == 4812


@_CARRY_MODES
def test_every_duplicate_delivery_in_the_carry_cycle_moves_nothing(upgrade_clawed_back):
    """Apple retries each notification: a second claw while clawed back takes nothing, a second
    reinstatement after a reinstatement gives nothing ('nothing_to_reinstate'), at every turn."""
    c, up, held, claw, give = carry_cycle(upgrade_clawed_back)
    for turn, hours in enumerate((2, 6)):                            # refund, reversal; twice over
        t_refund, t_reversal = d(hours=hours), d(hours=hours + 2)
        at(c, hours)
        for delivery in range(2):
            refund(c, "pro1", "C", PE, t_refund)
            expected = {"outcome": claw, "carry": 812} if delivery == 0 else {"outcome": "not_in_force"}
            assert revoke(c, "pro1", "C", PE) == expected and held() == 4000, (turn, delivery)
            assert (up["carry_revoked"], up["carry_revoked_at"]) == (812, t_refund)
        at(c, hours + 2)
        for delivery in range(2):
            refund(c, "pro1", "C", PE, t_reversal, kind="reversed")
            expected = {"outcome": give, "carry": 812} if delivery == 0 else {"outcome": "nothing_to_reinstate"}
            assert c.reinstate_paid_carry("C", PE) == expected and held() == 4812, (turn, delivery)
            assert (up["carry_revoked_at"], up["carry_reinstated_at"]) == (None, t_reversal)
    expected_rows = [] if upgrade_clawed_back else [("tier_revoked", -812), ("plan_reinstated", 812)] * 2
    assert rows(c, "tier_revoked", "plan_reinstated")[1 if upgrade_clawed_back else 0:] == expected_rows


def test_a_reversal_after_the_upgrade_ended_closes_the_clawback_once_without_moving_credits():
    """The carry was clawed back while the upgrade ran; then the upgrade's period ended and the
    next period started. The carry would have expired with the upgrade, so the Pro's reversal
    gives nothing back ('carry_expired') — but it does close the clawback: a duplicate gives
    'nothing_to_reinstate', and a later refund of the Pro finds no upgrade in force to claw."""
    c = upgraded()
    refund(c, "pro1", "C", PE, d(hours=1))
    assert revoke(c, "pro1", "C", PE) == {"outcome": "carry_clawed_back", "carry": 812}
    up = c.periods[("C", MAX_END)]
    c.now = MAX_END + timedelta(hours=1)
    c.put_subscription("C", "premium", "active", MAX_END + timedelta(days=30))
    assert c.ensure_credit_period() == 4000 and in_force(c)[1] == MAX_END + timedelta(days=30)
    refund(c, "pro1", "C", PE, d(hours=2), kind="reversed")
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "carry_expired"}
    assert (up["carry_revoked_at"], up["carry_reinstated_at"]) == (None, c.now)
    assert c.reinstate_paid_carry("C", PE) == {"outcome": "nothing_to_reinstate"}
    refund(c, "pro1", "C", PE, d(hours=3))
    assert revoke(c, "pro1", "C", PE) == {"outcome": "not_in_force"} and up["carry_revoked_at"] is None
    assert (c.total, c.used) == (4000, 0)
    assert rows(c, "tier_revoked", "plan_reinstated") == [("tier_revoked", -812)]


# ── revoke_tier_credits: the backstop reads the in-force lineage's own row ──────────────────

def test_the_refund_backstop_acts_on_the_in_force_lineages_own_revoked_row():
    """B (Pro) still active beside the refunded Max A wins the ORDER BY; the backstop must still
    see A's revoked row under the in-force key and claw the Max back."""
    c = fresh()
    c.put_subscription("B", "pro", "active", d(25))
    c.ensure_credit_period()
    c.put_subscription("A", "premium", "active", d(28))
    c.ensure_credit_period()
    assert in_force(c) == ("A", d(28), "premium")
    c.put_subscription("A", "premium", "revoked", d(28))
    assert c.revoke_tier_credits() == 50
    assert c.paid_period is None and (c.total, c.used) == (50, 0)
    assert c.periods[("A", d(28))]["revoked_at"] is not None
