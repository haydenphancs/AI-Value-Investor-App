"""Paid credits follow the Apple PAID PERIOD, not the calendar month (migration 196).

The defect (2026-10-12 credits audit): `ensure_credit_period` reset the granted pool on the ET
calendar month from `users.tier`, and `grant_tier_upgrade` topped a new subscriber up to the
full allocation on the day they bought. One Apple period spans two calendar months, so every
new subscription got one allocation more than it paid for — Max bought on Oct 25 and
cancelled at once: +3,950 that day, +4,000 on Nov 1, nothing clawed back = 7,950 credits for
$39.99.

Owner decisions (2026-10-12): "(b) Per paid period" — one allocation per Apple charge;
"Expire at plan end" — a lapsed plan's leftovers end with it; and an upgrade carries the
unused credits on top of the new allocation ("400 + 4000 credits = 4400"), "Cap by Pro time
used" — at most the old allocation times the share of the old period already elapsed, since
Apple refunds the rest of that period.

These run against `_Credits`, the executable model of the SQL in test_credit_pool_isolation
(one model, never two). The SQL-text guards at the bottom pin the structural half of 196.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import test_credit_pool_isolation as pool
from test_credit_pool_isolation import _Credits, _next_et_reset, _PLAN_CREDITS

UTC = timezone.utc
MAX, PRO, FREE = _PLAN_CREDITS["premium"], _PLAN_CREDITS["pro"], _PLAN_CREDITS["free"]


def t(text: str) -> datetime:
    """'2026-10-25 16:00' as UTC."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def reconcile(c: _Credits) -> None:
    """iap_service.reconcile_user_tier: mirror the tier, then the two RPCs it calls."""
    sub = c.subscription
    paid = "free"
    if sub and sub["status"] in pool._ENTITLING and (sub["status"] != "active" or sub["end"] > c.now):
        paid = sub["tier"]
    comp = c.comp_tier or "free"
    c.tier = comp if pool._TIER_RANK[comp] > pool._TIER_RANK[paid] else paid
    c.ensure_credit_period()
    c.grant_tier_upgrade()


def refund(c: _Credits) -> None:
    """iap_service.apply_transaction on a REFUND: revoked row, reconcile, then the backstop."""
    c.subscription["status"] = "revoked"
    reconcile(c)
    if c.tier == "free":
        c.revoke_tier_credits()


def subscribe(c: _Credits, otid: str, tier: str, end: datetime, status: str = "active",
              environment: str | None = None) -> None:
    c.set_subscription(otid, tier, status, end, environment)
    reconcile(c)


def granted(c: _Credits) -> int:
    return sum(r["granted_delta"] for r in c.ledger if r["reason"] in ("plan_period", "tier_upgrade"))


def ties_out(c: _Credits, opening: int) -> bool:
    return opening + sum(r["delta"] for r in c.ledger) == c.spendable


# ── The defect, and the two shapes a calendar-month rule cannot fix ────────────────────


def test_max_bought_late_in_the_month_and_cancelled_is_one_allocation():
    c = _Credits(total=50, used=10, now=t("2026-10-25 16:00"), purchased_total=650, purchased_used=100)
    c.resets_at = t("2026-11-01 04:00")
    subscribe(c, "A", "premium", t("2026-11-25 16:00"))
    assert (c.total, c.used) == (MAX, 0), "a paid period starts at its full allocation"
    c.spend_credits(100, ref_id="r1")

    c.now = t("2026-11-01 04:00:01")           # Nov 1, 00:00:01 ET
    c.ensure_credit_period()
    assert c.remaining == MAX - 100, "the calendar month must not grant a second allocation"

    c.now = t("2026-11-25 16:00:01")
    c.set_subscription("A", "premium", "expired", t("2026-11-25 16:00"))
    reconcile(c)
    assert (c.total, c.used, c.paid_period) == (FREE, 0, None), "leftovers end with the plan"
    assert c.resets_at == t("2026-12-01 05:00"), "then the calendar allowance (Dec 1 00:00 EST)"
    assert granted(c) == MAX, f"one payment, one allocation — was 7,950 before 196 (got {granted(c)})"
    assert (c.purchased_total, c.purchased_used) == (650, 100), "App Store 3.1.1"


@pytest.mark.parametrize("start", ["2026-10-02 12:00", "2026-10-15 12:00", "2026-10-31 12:00"])
def test_any_start_day_buys_exactly_one_allocation(start):
    """Prorating the upgrade (the rejected option c) fixed only LATE starts: an Oct 2 start
    still got ~3,870 + 4,000. Here every start day gets one allocation for one payment."""
    begin = t(start)
    c = _Credits(total=50, now=begin)
    end = begin + timedelta(days=31)
    subscribe(c, "S", "premium", end)
    for day in range(1, 31):
        c.now = begin + timedelta(days=day)
        c.ensure_credit_period()
    c.now = end + timedelta(seconds=1)
    c.set_subscription("S", "premium", "expired", end)
    reconcile(c)
    assert granted(c) == MAX


def test_a_renewing_subscriber_gets_one_allocation_per_payment():
    c = _Credits(total=50, now=t("2026-10-25 16:00"))
    ends = [t("2026-11-25 16:00"), t("2026-12-25 16:00"), t("2027-01-25 16:00")]
    subscribe(c, "R", "pro", ends[0])
    for i, end in enumerate(ends[1:], start=1):
        c.now = ends[i - 1] - timedelta(hours=20)    # Apple's DID_RENEW lands before the end
        subscribe(c, "R", "pro", end)
        assert c.paid_period["end"] == ends[i - 1], "an early renewal waits for the period"
        c.now = ends[i - 1] + timedelta(seconds=1)
        c.ensure_credit_period()
        assert c.paid_period["end"] == end and c.used == 0
    assert granted(c) == PRO * 3, "3 payments, 3 allocations (the calendar rule paid 4)"


def test_an_upgrade_on_the_last_minute_of_the_month_is_not_doubled_on_the_1st():
    c = _Credits(total=50, now=t("2026-11-01 03:59"))      # Oct 31, 23:59 EDT
    c.resets_at = t("2026-11-01 04:00")
    subscribe(c, "L", "premium", t("2026-12-01 03:59"))
    c.now = t("2026-11-01 04:00:01")                          # one minute later, Nov 1 ET
    c.ensure_credit_period()
    assert granted(c) == MAX and c.remaining == MAX


# ── Upgrades: leftovers carry, capped by the old period's elapsed share ────────────────


def test_the_owners_example_400_left_becomes_4400():
    c = _Credits(total=50, now=t("2026-10-01 04:00"))
    subscribe(c, "U", "pro", t("2026-11-01 04:00"))
    c.spend_credits(800, ref_id="r")
    c.now = t("2026-10-21 04:00")                             # 20 of 31 days used
    subscribe(c, "U", "premium", t("2026-11-21 04:00"))
    assert c.remaining == 4400
    assert (c.tier_alloc, c.paid_period["carried"]) == (MAX, 400)


@pytest.mark.parametrize("elapsed,spent,expected_carry", [
    (timedelta(hours=1), 0, 1),                  # instant upgrade: Apple refunds ~all of Pro
    (timedelta(days=10), 0, 387),                # the cap binds: floor(1200 * 10/31)
    (timedelta(days=10), 1000, 200),             # less left than the cap: all of it carries
    (timedelta(days=20), 1200, 0),               # nothing left, nothing to carry
    (timedelta(days=31) - timedelta(seconds=1), 0, 1199),   # last second of the Pro period
])
def test_the_carry_is_min_of_leftovers_and_the_used_share(elapsed, spent, expected_carry):
    start = t("2026-10-01 04:00")
    c = _Credits(total=50, now=start)
    subscribe(c, "C", "pro", t("2026-11-01 04:00"))
    if spent:
        c.spend_credits(spent, ref_id="r")
    c.now = start + elapsed
    subscribe(c, "C", "premium", c.now + timedelta(days=30))
    assert c.paid_period["carried"] == expected_carry
    assert c.remaining == MAX + expected_carry


def test_an_upgrade_from_free_starts_fresh():
    """The carry exists because Apple refunds the rest of a PAID period. Free credits were not
    paid for, so a first subscription starts at exactly its allocation."""
    c = _Credits(total=50, used=30, now=t("2026-10-20 12:00"))
    subscribe(c, "F", "pro", t("2026-11-20 12:00"))
    assert (c.total, c.used) == (PRO, 0)


def test_a_degenerate_old_period_carries_nothing():
    """A zero-length period cannot divide by zero, and its share counts as 0."""
    c = _Credits(total=50, now=t("2026-10-10 12:00"))
    subscribe(c, "Z", "pro", t("2026-11-10 12:00"))
    c.paid_period["start"] = c.paid_period["end"]
    c.now = t("2026-10-11 12:00")
    subscribe(c, "Z", "premium", t("2026-11-11 12:00"))
    assert c.paid_period["carried"] == 0 and c.remaining == MAX


# ── Replays, downgrades, refunds ───────────────────────────────────────────────────────


def test_a_period_is_granted_at_most_once_however_often_it_is_replayed():
    c = _Credits(total=50, now=t("2026-10-01 04:00"))
    subscribe(c, "P", "pro", t("2026-11-01 04:00"))
    c.now = t("2026-10-01 05:00")
    subscribe(c, "P", "premium", t("2026-11-01 05:00"))
    before = (c.total, c.used, len(c.ledger))
    for _ in range(3):
        subscribe(c, "P", "pro", t("2026-11-01 04:00"))       # the superseded Pro JWS
        subscribe(c, "P", "premium", t("2026-11-01 05:00"))   # and the current one
    assert (c.total, c.used, len(c.ledger)) == before


def test_one_apple_period_cannot_fund_two_accounts():
    shared: dict = {}
    a = _Credits(total=50, now=t("2026-10-10 04:00"), periods=shared)
    b = _Credits(total=50, now=t("2026-10-10 04:00"), periods=shared)
    subscribe(a, "X", "premium", t("2026-11-10 04:00"))
    subscribe(b, "X", "premium", t("2026-11-10 04:00"))
    assert (a.remaining, b.remaining) == (MAX, 50)


def test_a_downgrade_waits_for_the_period_it_replaces():
    c = _Credits(total=50, now=t("2026-10-20 04:00"))
    subscribe(c, "D", "premium", t("2026-11-20 04:00"))
    c.spend_credits(1000, ref_id="r")
    c.now = t("2026-11-19 04:00")
    subscribe(c, "D", "pro", t("2026-12-20 04:00"))
    assert c.remaining == MAX - 1000, "a lower tier never cuts a running period short"
    c.now = t("2026-11-20 04:00:01")
    c.ensure_credit_period()
    assert (c.total, c.used) == (PRO, 0)


def test_a_refund_claws_back_and_a_new_subscription_grants_again():
    c = _Credits(total=50, now=t("2026-10-10 04:00"), purchased_total=130)
    subscribe(c, "E", "premium", t("2026-11-10 04:00"))
    c.spend_credits(3000, ref_id="r")
    c.now = t("2026-10-12 04:00")
    refund(c)
    assert (c.remaining, c.paid_period) == (0, None)
    assert c.purchased_remaining == 130
    c.ensure_credit_period()
    assert c.remaining == 0, "a refunded period is not re-granted by a later read"
    c.now = t("2026-10-15 04:00")
    subscribe(c, "E", "premium", t("2026-11-15 04:00"))
    assert c.remaining == MAX, "a NEW paid period after a refund grants in full"


def test_a_refund_is_a_no_op_for_the_backstop_after_ensure():
    """iap_service calls revoke_tier_credits after reconcile; it must not take a second bite."""
    c = _Credits(total=50, now=t("2026-10-10 04:00"))
    subscribe(c, "N", "pro", t("2026-11-10 04:00"))
    c.spend_credits(20, ref_id="r")
    c.subscription["status"] = "revoked"
    reconcile(c)
    after_ensure = (c.total, c.used, len(c.ledger))
    c.revoke_tier_credits()
    assert (c.total, c.used, len(c.ledger)) == after_ensure


# ── Hold, lapse, comp ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["grace_period", "billing_retry", "active"])
def test_a_period_that_ended_while_entitled_holds_without_granting(status):
    c = _Credits(total=50, now=t("2026-10-05 04:00"))
    subscribe(c, "H", "pro", t("2026-11-05 04:00"))
    c.spend_credits(200, ref_id="r")
    c.now = t("2026-11-06 04:00")
    c.set_subscription("H", "pro", status, t("2026-11-05 04:00"))
    reconcile(c)
    assert c.remaining == PRO - 200 and c.paid_period is not None
    c.set_subscription("H", "pro", "expired", t("2026-11-05 04:00"))
    reconcile(c)
    assert (c.remaining, c.paid_period) == (FREE, None)


def test_a_comp_account_buys_a_plan_and_lapses_back_to_its_comp_allowance():
    c = _Credits(total=PRO, tier="pro", comp_tier="pro", now=t("2026-10-10 04:00"))
    subscribe(c, "G", "premium", t("2026-11-10 04:00"))
    assert c.remaining == MAX
    c.now = t("2026-11-10 04:00:01")
    c.set_subscription("G", "premium", "expired", t("2026-11-10 04:00"))
    reconcile(c)
    assert c.remaining == PRO


def test_a_raised_comp_tier_is_topped_up_mid_month():
    """scripts/set_comp_tier.py then `SELECT grant_tier_upgrade(...)` (migration 177)."""
    c = _Credits(total=PRO, used=100, tier="pro", comp_tier="pro", now=t("2026-10-10 04:00"))
    c.resets_at = t("2026-11-01 04:00")
    c.comp_tier = c.tier = "premium"
    c.grant_tier_upgrade()
    assert (c.total, c.used) == (MAX, 100)


def test_a_hand_set_tier_with_no_subscription_keeps_its_calendar_allowance():
    c = _Credits(total=MAX, used=MAX, tier="premium", now=t("2026-11-01 04:00:01"))
    c.resets_at = t("2026-11-01 04:00")
    c.ensure_credit_period()
    assert c.remaining == MAX


def test_users_tier_never_sizes_the_calendar_once_a_subscription_row_exists():
    """The leak's root: a lapsed row with users.tier still paid (expiry not yet swept) must
    not mint a paid calendar allocation."""
    c = _Credits(total=50, tier="premium", now=t("2026-11-01 04:00:01"))
    c.set_subscription("Q", "premium", "expired", t("2026-10-20 04:00"))
    c.resets_at = t("2026-11-01 04:00")
    c.ensure_credit_period()
    assert c.remaining == FREE


# ── Review round (2026-10-12): comp floor, Sandbox, older refunds, REFUND_REVERSED ────


def test_a_comp_max_account_that_buys_pro_keeps_its_comp_allowance():
    """The App Review demo account is comp Max; a reviewer buys a sandbox Pro on it. A paid
    period starts only ABOVE the complimentary floor — this one must change nothing."""
    c = _Credits(total=MAX, used=300, tier="premium", comp_tier="premium", now=t("2026-10-10 04:00"))
    before = (c.total, c.used, len(c.ledger))
    subscribe(c, "RV", "pro", t("2026-11-10 04:00"))
    assert (c.total, c.used, len(c.ledger)) == before and c.paid_period is None
    refund(c)
    assert (c.total, c.used) == before[:2], "refunding the ignored plan must not touch the comp pool"


@pytest.mark.parametrize("environment", ["Sandbox", "Xcode", "LocalTesting"])
def test_sandbox_renewals_keep_the_pre_196_calendar_sizing(environment):
    """Sandbox renews every few minutes. Each renewal used to be a new key and a fresh 4,000;
    a non-Production row now never starts a paid period and is sized by users.tier on the
    calendar, exactly as every row was before 196 (one top-up, then the monthly reset)."""
    start = t("2026-10-10 04:00")
    c = _Credits(total=50, now=start)
    for i in range(12):                                   # a full sandbox renewal chain
        c.now = start + timedelta(minutes=5 * i)
        subscribe(c, "SBX", "premium", c.now + timedelta(minutes=5), environment=environment)
        c.spend_credits(100, ref_id=f"s{i}")
    assert c.paid_period is None and not c.periods
    assert granted(c) == MAX - 50, "one top-up to the sandbox tier, not one grant per renewal"


def test_a_refund_of_an_older_renewal_leaves_the_period_in_force_alone():
    c = _Credits(total=50, now=t("2026-09-01 12:00"))
    subscribe(c, "OLD", "premium", t("2026-10-01 12:00"))
    c.now = t("2026-10-01 12:00:01")
    subscribe(c, "OLD", "premium", t("2026-11-01 12:00"))   # renewal: the period in force
    c.spend_credits(200, ref_id="r")
    c.now = t("2026-10-03 12:00")
    # Apple refunds the SEPTEMBER charge: the row now carries that transaction, revoked.
    c.set_subscription("OLD", "premium", "revoked", t("2026-10-01 12:00"))
    reconcile(c)
    c.revoke_tier_credits()                                  # iap_service's backstop
    assert c.remaining == MAX - 200, "October was paid for and not refunded"
    assert c.paid_period["end"] == t("2026-11-01 12:00")
    subscribe(c, "OLD", "premium", t("2026-11-01 12:00"))   # Restore re-applies October
    assert c.remaining == MAX - 200


def test_refund_reversed_reinstates_exactly_what_the_refund_took_once():
    c = _Credits(total=50, now=t("2026-10-05 12:00"))
    subscribe(c, "RR", "premium", t("2026-11-05 12:00"))
    c.spend_credits(100, ref_id="r")
    c.now = t("2026-10-07 12:00")
    refund(c)
    assert (c.remaining, c.paid_period) == (0, None)
    c.now = t("2026-10-09 12:00")
    subscribe(c, "RR", "premium", t("2026-11-05 12:00"))   # REFUND_REVERSED: active again
    assert c.remaining == MAX - 100, "the refund took 3,900; the reversal gives exactly that back"
    assert c.paid_period["reinstated_at"] == c.now and c.paid_period["revoked_at"] is None
    for _ in range(3):
        subscribe(c, "RR", "premium", t("2026-11-05 12:00"))
    assert c.remaining == MAX - 100, "a replay of the reversal must not reinstate twice"
    assert [r["reason"] for r in c.ledger].count("plan_reinstated") == 1


def test_a_second_refund_after_a_reversal_claws_back_again():
    c = _Credits(total=50, now=t("2026-10-05 12:00"))
    subscribe(c, "R2", "pro", t("2026-11-05 12:00"))
    for day in (7, 9, 11):
        c.now = t(f"2026-10-{day:02d} 12:00")
        if day == 9:
            subscribe(c, "R2", "pro", t("2026-11-05 12:00"))
        else:
            refund(c)
    # Nothing was spent, so the refund floors at the calendar allowance (the free 50).
    assert (c.remaining, c.paid_period) == (FREE, None)
    assert [r["reason"] for r in c.ledger].count("tier_revoked") == 2


def test_the_backstop_refuses_a_refund_that_is_not_for_the_period_in_force():
    c = _Credits(total=50, now=t("2026-10-10 04:00"))
    subscribe(c, "BS", "premium", t("2026-11-10 04:00"))
    c.set_subscription("BS", "premium", "revoked", t("2026-10-10 04:00"))  # an older period
    before = (c.total, c.used, len(c.ledger))
    c.revoke_tier_credits()
    assert (c.total, c.used, len(c.ledger)) == before


def test_refunding_the_superseded_pro_of_an_upgrade_keeps_max():
    c = _Credits(total=50, now=t("2026-10-01 04:00"))
    subscribe(c, "UP", "pro", t("2026-11-01 04:00"))
    c.now = t("2026-10-10 04:00")
    subscribe(c, "UP", "premium", t("2026-11-10 04:00"))
    held = c.remaining
    c.set_subscription("UP", "pro", "revoked", t("2026-11-01 04:00"))
    reconcile(c)
    c.revoke_tier_credits()
    assert c.remaining == held and c.paid_period["tier"] == "premium"


# ── The ET calendar boundary across both DST changes ───────────────────────────────────


@pytest.mark.parametrize("now,expected", [
    ("2026-10-15 12:00", "2026-11-01 04:00"),     # EDT: midnight ET = 04:00 UTC
    ("2026-11-01 04:00", "2026-12-01 05:00"),     # Nov 1 00:00 EDT -> Dec 1 00:00 EST
    ("2026-12-31 23:00", "2027-01-01 05:00"),     # Dec 31 18:00 EST -> year rollover
    ("2027-03-01 05:00", "2027-04-01 04:00"),     # Mar 1 EST -> Apr 1 EDT (DST began Mar 14)
    ("2026-11-01 03:59", "2026-11-01 04:00"),     # Oct 31 23:59 EDT is still October
])
def test_the_calendar_boundary_is_midnight_eastern(now, expected):
    assert _next_et_reset(t(now)) == t(expected)


def test_the_calendar_rolls_exactly_at_eastern_midnight():
    c = _Credits(total=50, used=50, now=t("2026-11-01 03:59:59"))
    c.resets_at = t("2026-11-01 04:00")
    c.ensure_credit_period()
    assert c.remaining == 0
    c.now = t("2026-11-01 04:00")
    c.ensure_credit_period()
    assert (c.remaining, c.resets_at) == (FREE, t("2026-12-01 05:00"))


# ── Property walk: random event sequences keep every invariant ─────────────────────────


@pytest.mark.parametrize("seed", range(60))
def test_random_lifecycles_keep_every_invariant(seed):
    """Random Apple event sequences, including the review round's paths: Sandbox rows, a refund
    of an OLDER period, a reversed refund, a comp floor."""
    rng = random.Random(seed)
    shared: dict = {}
    c = _Credits(total=50, now=t("2026-10-01 12:00"), purchased_total=rng.choice([0, 130, 650]),
                 periods=shared, comp_tier=rng.choice([None, None, None, "pro"]))
    c.resets_at = t("2026-11-01 04:00")
    opening = c.spendable
    otid, payments, env = f"W{seed}", 0, None
    refunded_key = None                          # the last period whose refund Apple may reverse
    older_ends: list = []                        # ends of earlier periods on this subscription
    purchased_before = c.purchased_total
    for _ in range(70):
        c.now += timedelta(hours=rng.choice([1, 6, 30, 200]))
        event = rng.choice(["subscribe", "renew", "upgrade", "downgrade", "spend", "refund",
                            "lapse", "grace", "read", "replay", "refund_older", "reverse"])
        sub = c.subscription
        tier = (sub or {}).get("tier", "pro")
        active = sub is not None and sub["status"] == "active"
        if event == "subscribe" and not active:
            env = rng.choice([None, None, None, "Sandbox"])
            if sub is not None and sub["end"] is not None:
                older_ends.append(sub["end"])
            subscribe(c, otid, rng.choice(["pro", "premium"]), c.now + timedelta(days=31),
                      environment=env); payments += 1
        elif event == "renew" and active:
            older_ends.append(sub["end"])
            subscribe(c, otid, tier, sub["end"] + timedelta(days=31), environment=env); payments += 1
        elif event == "upgrade" and active and sub["end"] > c.now and tier == "pro":
            older_ends.append(sub["end"])
            subscribe(c, otid, "premium", c.now + timedelta(days=31), environment=env); payments += 1
        elif event == "downgrade" and active and tier == "premium":
            older_ends.append(sub["end"])
            subscribe(c, otid, "pro", sub["end"] + timedelta(days=31), environment=env); payments += 1
        elif event == "spend":
            c.ensure_credit_period()
            c.spend_credits(rng.choice([1, 20, 300]), ref_id=f"s{rng.random()}")
        elif event == "refund" and active:
            refunded_key = (sub["tier"], sub["end"])
            refund(c)
        elif event == "refund_older" and active and older_ends:
            c.set_subscription(otid, tier, "revoked", rng.choice(older_ends), env); reconcile(c)
            if c.tier == "free":
                c.revoke_tier_credits()
        elif event == "reverse" and refunded_key and sub is not None and sub["status"] == "revoked":
            subscribe(c, otid, refunded_key[0], refunded_key[1], environment=env)
            refunded_key = None
        elif event == "lapse" and active:
            c.set_subscription(otid, tier, "expired", sub["end"], env); reconcile(c)
        elif event == "grace" and active:
            c.set_subscription(otid, tier, "grace_period", sub["end"], env); reconcile(c)
        elif event == "replay" and sub:
            reconcile(c)
        else:
            c.ensure_credit_period()

        assert c.purchased_total == purchased_before, "App Store 3.1.1: never touched"
        assert ties_out(c, opening), "SUM(delta) drifted from the balance"
        plan_rows = [r for r in c.ledger if r["reason"] == "plan_period"]
        assert len(plan_rows) == len(shared), "a ledger grant without its period row, or twice"
        assert len(shared) <= payments, f"more paid allocations ({len(shared)}) than payments ({payments})"
        assert all(p.get("revoked_at") is None or p is not c.paid_period for p in shared.values()), (
            "a revoked period is still the one in force"
        )
        reinstated = sum(r["delta"] for r in c.ledger if r["reason"] == "plan_reinstated")
        clawed = -sum(r["delta"] for r in c.ledger if r["reason"] == "tier_revoked")
        assert reinstated <= clawed + sum(p["allocation"] for p in shared.values()), (
            "a reversal gave back more than refunds ever took"
        )
        carried = sum(p["carried"] for p in shared.values())
        assert carried <= sum(p["allocation"] for p in shared.values()), "carry exceeds what was granted"


# ── The SQL itself: structural guards over the LATEST migration definitions ────────────

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"


def _latest_body(name: str) -> tuple[str, str]:
    pattern = re.compile(
        rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(?:public\.)?{name}\s*\(.*?AS\s+(\$\w*\$)(.*?)\1",
        re.IGNORECASE | re.DOTALL,
    )
    found = []
    for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        text = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))   # comments out
        found += [(path.name, m.group(2)) for m in pattern.finditer(text)]
    assert found, f"no definition of {name}() in any migration"
    return found[-1]


@pytest.mark.parametrize("name", ["ensure_credit_period", "grant_tier_upgrade", "revoke_tier_credits",
                                  "revoke_paid_period", "reinstate_paid_carry",
                                  "record_subscription_transaction"])
def test_the_tier_rpcs_never_name_the_purchased_pool(name):
    """App Store 3.1.1 made structural (117): the granted-pool writers name only granted
    columns, so no branch of theirs can expire a credit someone bought."""
    source, body = _latest_body(name)
    assert not re.search(r"\bpurchased_(total|used)\b", body), f"{name}() in {source} names the purchased pool"


def test_grant_tier_upgrade_cannot_grant_on_its_own_any_more():
    """Its old top-up to users.tier's allocation was the first half of the double grant."""
    source, body = _latest_body("grant_tier_upgrade")
    assert "ensure_credit_period" in body and "plan_credits" not in body and "UPDATE" not in body.upper(), source


def test_a_paid_period_is_keyed_once_and_the_paid_allocation_never_reads_users_tier():
    source, body = _latest_body("ensure_credit_period")
    assert "subscription_credit_periods" in body, source
    assert re.search(r"WHERE\s+tier\s*=\s*v_sub\.tier", body), "the paid allocation must come from the subscription row"
    assert re.search(r"CASE\s+WHEN\s+v_prod_row\s+THEN\s+COALESCE\(v_comp_tier,\s*'free'\)", body), (
        "users.tier may size only the calendar allowance of an account with no PRODUCTION row"
    )
    # 198: one row per lineage. Calendar sizing asks whether ANY Production row exists; a paid
    # period needs the WINNING row itself to be Production (a Sandbox winner beside a Production
    # row must not count as paid).
    assert re.search(
        r"v_prod_row\s*:=\s*EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+public\.subscriptions\s+\w+\s+"
        r"WHERE\s+\w+\.user_id\s*=\s*p_user_id\s+AND\s+COALESCE\(\w+\.environment,\s*'Production'\)\s*=\s*'Production'",
        body), "users.tier may size the calendar only while the account has NO Production row"
    assert re.search(
        r"v_sub_prod\s*:=\s*v_has_sub\s+AND\s+COALESCE\(v_sub\.environment,\s*'Production'\)\s*=\s*'Production'",
        body), "a Sandbox row must never start a paid period (it renews every few minutes)"
    paid_row = re.search(r"v_paid_row\s*:=(.*?);", body, re.S)
    assert paid_row and "v_sub_prod" in paid_row.group(1) and "v_tier" in paid_row.group(1), (
        "a paid period needs a Production row ABOVE the complimentary floor"
    )


def test_a_refund_claws_back_only_the_period_it_was_for():
    """A refund of an OLDER renewal shares the original transaction id with the period in force,
    so the match must include the period end — in ensure_credit_period AND in the backstop."""
    for name in ("ensure_credit_period", "revoke_tier_credits"):
        source, body = _latest_body(name)
        # 198: the in-force period's OWN lineage row (v_cur_sub), never "the" row.
        assert re.search(
            r"v_cur_sub\.original_transaction_id\s*=\s*v_cur\.original_transaction_id\s*"
            r"AND\s+v_cur_sub\.current_period_end\s*=\s*v_cur\.period_end", body), (
            f"{name}() in {source} claws back without checking the refunded period IS the one in force"
        )


def _migration_text(name: str) -> str:
    return re.sub(r"--[^\n]*", "", (_MIGRATIONS / name).read_text(encoding="utf-8"))


def _norm(sql: str) -> str:
    """Whitespace-normalised SQL (one space; none just inside parentheses), so a guard can pin an
    expression EXACTLY. A substring guard ("v_cur_holds" in cond) passes `NOT v_cur_holds`, an
    `OR` for an `AND` and an extra conjunct — review of the fix pass, 2026-10-15."""
    sql = re.sub(r"\s+", " ", sql).strip()
    return re.sub(r"\s+\)", ")", re.sub(r"\(\s+", "(", sql))


_M198 = "198_subscriptions_per_lineage_transaction_register.sql"

# The register's "still refunded" predicate (alias t): a refund newer than any reversal of it.
# One spelling, shared by ensure_credit_period's reinstate guard and reinstate_paid_carry.
_UNREVERSED_REFUND = ("t.revoked_signed_at IS NOT NULL AND (t.reversed_signed_at IS NULL "
                      "OR t.reversed_signed_at < t.revoked_signed_at)")


def test_the_keyed_clawback_matches_both_the_lineage_and_the_period_end():
    """198's revoke_paid_period claws back ONE credit period: the refund of an older renewal
    shares the original transaction with the period in force, so the key must include the end."""
    source, body = _latest_body("revoke_paid_period")
    assert re.search(r"WHERE\s+original_transaction_id\s*=\s*p_original_transaction_id\s+"
                     r"AND\s+period_end\s*=\s*p_period_end", body), source


def test_the_keyed_clawback_rechecks_the_refund_under_the_balance_lock():
    """A REFUND_REVERSED recorded after the caller decided must keep the credits: the register is
    read AFTER `user_credits ... FOR UPDATE`, and a reversed refund returns before any write."""
    source, body = _latest_body("revoke_paid_period")
    lock = re.search(r"FROM\s+public\.user_credits\s+WHERE\s+user_id\s*=\s*p_user_id\s+FOR\s+UPDATE", body)
    register = body.find("FROM public.subscription_transactions")
    assert lock and 0 <= lock.start() < register, source
    reversed_return = body.find("'reversed'")
    first_write = min(i for i in (body.find("UPDATE public."), body.find("INSERT INTO public.")) if i >= 0)
    assert 0 <= reversed_return < first_write, "a reversed refund must return before anything is written"


def test_a_refunded_period_is_never_reinstated_while_the_register_holds_its_refund():
    """Pinned EXACTLY: an inverted comparison (`reversed_signed_at > revoked_signed_at`) reads a
    reversed refund as still refunded and a re-refunded period as reversed — it still names the
    register, so the old "subscription_transactions in the expression" guard passed it."""
    source, body = _latest_body("ensure_credit_period")
    assert len(re.findall(r"v_reinstate\s*:=", body)) == 1, source
    reinstate = re.search(r"v_reinstate\s*:=(.*?);", body, re.S)
    assert reinstate, source
    expected = (
        "COALESCE(v_new_key AND v_seen AND v_seen_row.user_id = p_user_id "
        "AND v_seen_row.revoked_at IS NOT NULL AND NOT v_running "
        "AND NOT EXISTS (SELECT 1 FROM public.subscription_transactions t "
        "WHERE t.original_transaction_id = v_seen_row.original_transaction_id "
        f"AND t.period_end = v_seen_row.period_end AND {_UNREVERSED_REFUND}), FALSE)")
    assert _norm(reinstate.group(1)) == expected, (
        f"{source}: the reinstate guard changed:\n  {_norm(reinstate.group(1))}\n  expected {expected}")
    # The reinstate BRANCH (brace-bound, not the whole body) clears the keyed request, or the next
    # call reads the stale revoke_requested_at and claws the period back again.
    branch = re.search(r"IF\s+v_reinstate\s+THEN(.*?)RETURN\s+v_restore\s*;", body, re.S)
    assert branch, f"{source}: the REFUND_REVERSED branch is gone"
    clear = re.search(r"UPDATE\s+public\.subscription_credit_periods\s+SET(.*?)WHERE\s+id\s*=\s*v_seen_row\.id\s*;",
                      branch.group(1), re.S)
    assert clear and _norm(clear.group(1)) == (
        "revoked_at = NULL, revoked_remaining = NULL, revoke_requested_at = NULL, reinstated_at = now()"), (
        "the reinstate branch must clear revoke_requested_at, or the next call claws it back again")


_CARRY_UPGRADE_LOOKUP = ("SELECT * INTO v_u FROM public.subscription_credit_periods "
                         "WHERE carried_from_period_id = v_p.id AND user_id = p_user_id "
                         "ORDER BY id DESC LIMIT 1 FOR UPDATE;")
_PERIOD_UPDATE = re.compile(r"UPDATE public\.subscription_credit_periods SET (.*?) WHERE (.*?);")


def _sets(assignments: str, column_value: str) -> bool:
    """`column = value` is one of the comma-separated SET items (never a substring of another)."""
    return re.search(rf"(?:^|, ){re.escape(column_value)}(?:,|$)", assignments) is not None


def test_the_upgrade_carry_is_clawed_back_once_per_refund_and_given_back_once_per_reversal():
    """The carry state machine (fix-pass review, 2026-10-15): carry_revoked_at is set while the
    carry is clawed back. revoke_paid_period claws only while it is NULL (a duplicate refund takes
    nothing); every reinstatement outcome clears it (so Apple refunding the period AGAIN after a
    reversal claws again); and reinstate_paid_carry re-checks the register under the balance lock,
    so a reversal that a later refund superseded gives nothing back ('still_refunded')."""
    source, body = _latest_body("reinstate_paid_carry")
    n = _norm(body)
    lock = "SELECT * INTO v_row FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;"
    nothing = ("IF NOT FOUND OR v_u.carry_revoked_at IS NULL THEN "
               "RETURN jsonb_build_object('outcome', 'nothing_to_reinstate'); END IF;")
    still = ("IF EXISTS (SELECT 1 FROM public.subscription_transactions t "
             "WHERE t.original_transaction_id = p_original_transaction_id AND t.period_end = p_period_end "
             f"AND {_UNREVERSED_REFUND}) THEN RETURN jsonb_build_object('outcome', 'still_refunded'); END IF;")
    for piece in (lock, _CARRY_UPGRADE_LOOKUP, nothing, still):
        assert n.count(piece) == 1, f"{source}: reinstate_paid_carry lost (or doubled) {piece!r}"
    first_write = min(i for i in (n.find("UPDATE public."), n.find("INSERT INTO public.")) if i >= 0)
    assert n.index(lock) < n.index(still) < first_write, (
        f"{source}: the register must be re-read UNDER the balance lock and before any write")
    gives = _PERIOD_UPDATE.findall(n)
    assert len(gives) == 3, f"{source}: three reinstatement outcomes, three period writes: {gives}"
    for assignments, where in gives:
        assert _sets(assignments, "carry_revoked_at = NULL"), (
            f"{source}: a reinstatement must clear carry_revoked_at, or a later refund never claws "
            f"again and a duplicate reversal gives the carry back twice: {assignments}")
        assert _sets(assignments, "carry_reinstated_at = now()"), assignments
        assert where == "id = v_u.id AND carry_revoked_at IS NOT NULL", where

    source, body = _latest_body("revoke_paid_period")
    n = _norm(body)
    gate = "IF FOUND AND v_u.carry_revoked_at IS NULL THEN"
    assert n.count(_CARRY_UPGRADE_LOOKUP) == 1 and n.count(gate) == 1, (
        f"{source}: the carry branch must claw only while the carry is not clawed back already")
    assert n.index(_CARRY_UPGRADE_LOOKUP) < n.index(gate)
    claws = _PERIOD_UPDATE.findall(n[n.index(gate):])
    assert len(claws) == 2, f"{source}: two clawback paths (in force / clawed-back upgrade): {claws}"
    for assignments, where in claws:
        assert _sets(assignments, "carry_revoked_at = now()") and _sets(assignments, "carry_revoked = v_x"), assignments
        assert where == "id = v_u.id AND carry_revoked_at IS NULL", where


def test_the_in_force_lineages_own_next_key_is_evaluated_when_the_winner_cannot_start():
    """Review #5/#14/#26 (2026-10-15): only the ORDER BY winner's key used to be evaluated, so a
    winner held back by the same-or-lower rule left nothing but a HOLD — a paid renewal of the
    in-force lineage was never granted (permanently after a downgrade beside another Max)."""
    source, body = _latest_body("ensure_credit_period")
    loop = re.search(r"<<pick>>\s*LOOP(.*?)END\s+LOOP\s+pick;", body, re.S)
    assert loop, f"{source}: the candidate key must be evaluated in the <<pick>> loop"
    assert len(re.findall(r"v_retarget\s*:=", body)) == 1, source
    retarget = re.search(r"v_retarget\s*:=(.*?);", loop.group(1), re.S)
    assert retarget, source
    # EXACT, not "each clause is a substring": `NOT v_reinstate OR v_cur_holds`, `AND NOT
    # v_cur_holds` and `AND NOT v_cur_sub.status = 'active'` all contain every old substring.
    # Seven conjuncts under one AND: the winner neither starts nor reinstates, the in-force
    # lineage holds, its own row can carry a period now, and it is not the winner already.
    expected = (
        "COALESCE(NOT v_start_paid AND NOT v_reinstate AND v_cur_holds "
        "AND v_cur_sub.status = 'active' AND v_cur_sub.current_period_end > now() "
        "AND COALESCE(v_cur_sub.original_transaction_id, '') <> '' "
        "AND v_sub.original_transaction_id IS DISTINCT FROM v_cur_sub.original_transaction_id, FALSE)")
    assert _norm(retarget.group(1)) == expected, (
        f"{source}: the retarget condition changed:\n  {_norm(retarget.group(1))}\n  expected {expected}")
    # Then, in this order: leave unless retargeting; else re-evaluate the in-force lineage's own
    # row with ITS environment and tier (a stale v_paid_row would start or skip the wrong key).
    tail = loop.group(1)[retarget.end():]
    assert _norm(tail) == (
        "EXIT pick WHEN NOT v_retarget; v_sub := v_cur_sub; "
        "v_sub_prod := COALESCE(v_sub.environment, 'Production') = 'Production'; "
        "v_paid_row := v_sub_prod AND (CASE v_sub.tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END) "
        "> (CASE v_tier WHEN 'premium' THEN 2 WHEN 'pro' THEN 1 ELSE 0 END); v_seen := FALSE;"), (
        f"{source}: the retarget step changed: {_norm(tail)}")
    # Every key decision is inside the loop, so the retargeted row gets all of them.
    for name in ("v_new_key", "v_reinstate", "v_start_paid"):
        assert re.search(rf"{name}\s*:=", loop.group(1)), name
        assert not re.search(rf"{name}\s*:=", body[:loop.start()] + body[loop.end():]), name


def test_the_max_period_end_high_water_mark_is_a_trigger_never_a_check():
    """Design v1 had a CHECK (max_period_end >= current_period_end) and no trigger: that failed
    every write of code that does not set the column (a 23514 → 503 on every renewal). The
    trigger keeps it right for every writer (a CHECK would only bite with it disabled) and must
    never touch updated_at, which the iap_service write fence compares."""
    ddl = _migration_text(_M198)
    assert not re.search(r"CHECK\s*\([^;]*max_period_end", ddl), "no CHECK on max_period_end"
    source, fn = _latest_body("subscriptions_keep_max_period_end")
    fn = _norm(fn)
    assert "updated_at" not in fn, f"{source}: the trigger must never touch the write fence"
    # Review #0: a re-point to another lineage restarts the mark from the new lineage's own end
    # (NEW.max_period_end holds OLD's when the writer did not set it, so it is never read there).
    repoint = ("IF TG_OP = 'UPDATE' AND NEW.original_transaction_id IS DISTINCT FROM "
               "OLD.original_transaction_id THEN NEW.max_period_end := NEW.current_period_end;")
    # The same lineage: the mark only rises — over what it was, what the row held and what it
    # now holds. `:= NEW.current_period_end` here would let an overlap upgrade (or the late
    # refund of an older period) rewind it, and Rule D would read a refunded renewal as newer.
    same_lineage = ("ELSIF TG_OP = 'UPDATE' THEN NEW.max_period_end := GREATEST(NEW.max_period_end, "
                    "OLD.max_period_end, OLD.current_period_end, NEW.current_period_end);")
    insert = "ELSE NEW.max_period_end := GREATEST(NEW.max_period_end, NEW.current_period_end); END IF;"
    assert repoint in fn, "a re-pointed row must not inherit the previous lineage's mark"
    assert same_lineage in fn, f"{source}: the same-lineage branch must only ever raise the mark: {fn}"
    assert insert in fn, f"{source}: an INSERT must start the mark at the row's own end: {fn}"
    assert fn == f"BEGIN {repoint} {same_lineage} {insert} RETURN NEW; END;", f"{source}: {fn}"
    # A ROW trigger, BEFORE both writes: a STATEMENT trigger's NEW is NULL (nothing is kept), and
    # an AFTER trigger cannot change the row. Dropped first, so a re-run recreates it.
    triggers = re.findall(r"CREATE\s+TRIGGER\s+subscriptions_max_period_end\s+(.*?);", ddl, re.S)
    assert [_norm(t) for t in triggers] == [
        "BEFORE INSERT OR UPDATE ON public.subscriptions FOR EACH ROW "
        "EXECUTE FUNCTION public.subscriptions_keep_max_period_end()"], triggers
    drop = re.search(r"DROP\s+TRIGGER\s+IF\s+EXISTS\s+subscriptions_max_period_end\s+ON\s+public\.subscriptions\s*;",
                     ddl)
    assert drop and drop.start() < ddl.index("CREATE TRIGGER subscriptions_max_period_end")


def test_the_backfill_knows_the_mark_only_for_a_row_that_entitles_when_198_is_applied():
    """Review #17: a row revoked or expired before 198 may have been REWOUND (a late refund of an
    older period wrote that period's end over it), so copying its current_period_end let Rule D
    read a refunded renewal's saved JWS as a newer period. Its mark is 'infinity' (unknown)."""
    ddl = _migration_text(_M198)
    backfills = re.findall(r"UPDATE\s+public\.subscriptions\s+SET\s+max_period_end\s*=.*?;", ddl, re.S)
    assert len(backfills) == 1, f"exactly one backfill statement, found {len(backfills)}"
    # The WHOLE statement, WHERE clause included: a re-run touches only rows still unmarked, and
    # every row that has an end or does not entitle is marked — an extra conjunct (`AND status IS
    # NULL`) would leave revoked/expired rows unmarked, and the trigger's GREATEST would then
    # adopt their possibly-rewound end as a KNOWN mark on their next write.
    assert _norm(backfills[0]) == (
        "UPDATE public.subscriptions "
        "SET max_period_end = CASE WHEN status IN ('active', 'grace_period', 'billing_retry') "
        "THEN current_period_end ELSE 'infinity'::timestamptz END "
        "WHERE max_period_end IS NULL "
        "AND (current_period_end IS NOT NULL OR status NOT IN ('active', 'grace_period', 'billing_retry'));"
    ), _norm(backfills[0])


def test_198_drops_the_index_its_unique_index_duplicates():
    ddl = _migration_text(_M198)
    unique = ddl.index("CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_original_transaction_id_key")
    dropped = re.search(r"DROP\s+INDEX\s+IF\s+EXISTS\s+public\.idx_subscriptions_txn\s*;", ddl)
    assert dropped and dropped.start() > unique, "drop the duplicate only after its replacement exists"


def test_196_and_194_refuse_to_run_after_198():
    """Re-applying 196 would put back the one-row bodies while iap_service runs per lineage."""
    for name, table in (("196_paid_period_credit_grants.sql", "subscription_transactions"),
                        ("194_monthly_expired_ledger_row.sql", "subscription_credit_periods")):
        ddl = _migration_text(name)
        guard = re.search(r"IF\s+to_regclass\('public\.(\w+)'\)\s+IS\s+NOT\s+NULL\s+THEN\s+RAISE\s+EXCEPTION",
                          ddl)
        assert guard and guard.group(1) == table, name
        assert ddl.index("RAISE EXCEPTION") < ddl.index("CREATE OR REPLACE FUNCTION"), name


def test_198_drops_one_row_per_account_and_keys_rows_by_lineage():
    ddl = _migration_text(_M198)
    assert re.search(r"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+subscriptions_user_id_key", ddl)
    assert re.search(r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+subscriptions_original_transaction_id_key\s+"
                     r"ON\s+public\.subscriptions\s*\(\s*original_transaction_id\s*\)", ddl)
    assert re.search(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+idx_subscriptions_user\s+ON\s+public\.subscriptions"
                     r"\s*\(\s*user_id\s*\)", ddl), "every per-user read lost its index with the constraint"
    # The preflight refuses to run over two rows sharing a lineage.
    assert ddl.index("HAVING count(*) > 1") < ddl.index("DROP CONSTRAINT IF EXISTS subscriptions_user_id_key")


def test_the_backfill_runs_once_and_only_for_periods_the_old_code_paid():
    ddl = (_MIGRATIONS / "196_paid_period_credit_grants.sql").read_text(encoding="utf-8")
    ddl = re.sub(r"--[^\n]*", "", ddl)
    # The DO block that INSERTs the backfill rows (196 also opens with its after-198 guard).
    blocks = re.findall(r"DO \$\$(.*?)END \$\$;", ddl, re.S)
    backfill = next(b for b in blocks if "INSERT INTO public.subscription_credit_periods" in b)
    assert re.search(r"IF\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+public\.subscription_credit_periods\s+"
                     r"WHERE\s+source\s*<>\s*'backfill'\s*\)", backfill), "a re-run must not backfill"
    assert re.search(r"uc\.tier_alloc\s*>=\s*COALESCE\(pc\.monthly_credits,\s*0\)", backfill), (
        "a period the old code never paid (a failed grant) must be left for ensure_credit_period"
    )
    assert "comp_tier" in backfill, "a comp tier at or above the plan must stay on the calendar"
    ddl = "\n".join(p.read_text(encoding="utf-8") for p in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")))
    assert re.search(r"UNIQUE\s*\(\s*original_transaction_id\s*,\s*period_end\s*\)", ddl), "the exactly-once key"
