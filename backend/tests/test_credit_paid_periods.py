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
(one model, never two). The SQL-text guards at the bottom pin the structural half of 196 and 198
on each function's LIVE definition, read through the shared `tests/_migration_sql.py` reader.
"""

from __future__ import annotations

import inspect
import itertools
import random
import re
from datetime import datetime, timedelta, timezone

import pytest

import _migration_sql
from _migration_sql import MIGRATIONS as _MIGRATIONS
from _migration_sql import (if_block, insert_rows, latest_definition, migration_sources, norm_sql,
                            set_items, statements, strip_sql_comments)
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
#
# Every guard here reads a function through `_latest_body`, which is the shared reader
# (tests/_migration_sql.py): the last `CREATE [OR REPLACE] FUNCTION` in file order, with `--` and
# `/* */` comments stripped in one pass. Until 2026-10-21 (IAP audit F20) this file had its own
# regex, which matched only `CREATE OR REPLACE` and stripped only `--`. A later `DROP FUNCTION ...;
# CREATE FUNCTION` (the shape a return-type change forces; 142 did it to refund_credits) left all
# fifteen guards reading 198 while the live function changed. It was already wrong: it returned
# 139's refund_credits, not the live 142. The tests after the guards feed synthetic later
# migrations IN MEMORY (never a file in migrations/) and require the guards to turn red.

_RPCS = ["ensure_credit_period", "grant_tier_upgrade", "revoke_tier_credits", "revoke_paid_period",
         "reinstate_paid_carry", "record_subscription_transaction"]


def _latest_body(name: str) -> tuple[str, str]:
    """(file name, comment-stripped body) of the live definition of public.<name>."""
    d = latest_definition(name)
    return d.file, d.body


@pytest.mark.parametrize("name", _RPCS)
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
    """One migration file by name, comments stripped by the shared reader (`--` AND `/* */`)."""
    return strip_sql_comments((_MIGRATIONS / name).read_text(encoding="utf-8"))


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
    # 200 (IAP audit F15) also clears the clawback's cover and ledger mark: a stale mark would
    # deduct a LATER window's spend at the next reinstatement.
    assert clear and _norm(clear.group(1)) == (
        "revoked_at = NULL, revoked_remaining = NULL, revoke_requested_at = NULL, "
        "revoked_cover = NULL, revoked_ledger_id = NULL, reinstated_at = now()"), (
        "the reinstate branch must clear revoke_requested_at (or the next call claws it back "
        "again) and 200's revoked_cover / revoked_ledger_id")


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
    # EXACT, not "each clause is a substring": `NOT v_reinstate OR v_cur_holds` and `AND NOT
    # v_cur_holds` contain every old substring. Six conjuncts under one AND: the winner neither
    # starts nor reinstates, the in-force lineage holds, its own row records a LATER period than
    # the one in force (200, IAP audit F11: whatever its status — grace, retry, or 'active' past
    # its end; 198 required 'active' with a future end and so never granted such a renewal), and
    # it is not the winner already.
    expected = (
        "COALESCE(NOT v_start_paid AND NOT v_reinstate AND v_cur_holds "
        "AND v_cur_sub.current_period_end > v_cur.period_end "
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


# ── Migration 200 (IAP audit phase 2: F10, F11, F15) ───────────────────────────────────────────

def test_the_in_force_lineages_later_period_is_a_new_key_whatever_its_status():
    """F11: a renewal no credit call saw while its row was active must still be a new key once the
    row is in grace or retry, or 'active' past its end — but only the in-force lineage's OWN row,
    only a LATER period than the one in force, and never one the register holds a refund of."""
    source, body = _latest_body("ensure_credit_period")
    new_key = re.search(r"v_new_key\s*:=(.*?);", body, re.S)
    assert new_key and len(re.findall(r"v_new_key\s*:=", body)) == 1, source
    expected = (
        "COALESCE(v_paid_row AND ((v_sub.status = 'active' AND v_sub.current_period_end > now()) "
        "OR (v_cur_holds AND v_sub.original_transaction_id = v_cur.original_transaction_id "
        "AND v_sub.current_period_end > v_cur.period_end "
        "AND NOT EXISTS (SELECT 1 FROM public.subscription_transactions t "
        "WHERE t.original_transaction_id = v_sub.original_transaction_id "
        f"AND t.period_end = v_sub.current_period_end AND {_UNREVERSED_REFUND}))) "
        "AND COALESCE(v_sub.original_transaction_id, '') <> '' "
        "AND NOT (v_has_cur AND v_cur.original_transaction_id = v_sub.original_transaction_id "
        "AND v_cur.period_end = v_sub.current_period_end), FALSE)")
    assert _norm(new_key.group(1)) == expected, (
        f"{source}: the candidate key changed:\n  {_norm(new_key.group(1))}\n  expected {expected}")


def _branch(body: str, head: str, end: str) -> str:
    start = re.search(head, body)
    assert start, f"no {head!r}"
    return body[start.start():body.index(end, start.start())]


def test_the_refund_branch_records_what_the_clawback_covered_and_where_the_ledger_stood():
    """F15: the REFUND branch stamps revoked_cover (the write-off: what the refunded allocation
    paid for above the floor) and revoked_ledger_id (the user's last ledger id), read under the
    balance lock, BEFORE the clawback's own tier_revoked row — or that row would be inside the
    reinstatement window."""
    source, body = _latest_body("ensure_credit_period")
    branch = _branch(body, r"v_floor\s*:=\s*GREATEST\(v_alloc,\s*v_row\.used\)", "RETURN (v_floor - v_row.used);")
    n = _norm(branch)
    mark = "SELECT COALESCE(max(t.id), 0) INTO v_mark FROM public.credit_transactions t WHERE t.user_id = p_user_id;"
    assert n.count(mark) == 1, f"{source}: the ledger mark is gone or doubled"
    stamp = _PERIOD_UPDATE.search(n)
    assert stamp and stamp.group(2) == "id = v_cur.id", source
    assert set_items(stamp.group(1)) == {
        "revoked_at": "now()", "revoked_remaining": "v_remaining",
        "revoked_cover": "v_writeoff", "revoked_ledger_id": "v_mark"}, stamp.group(1)
    assert n.index(mark) < stamp.start() < n.index("'tier_revoked'"), (
        f"{source}: the mark must be read before the clawback writes its own ledger row")
    lock = re.search(r"FROM\s+public\.user_credits\s+WHERE\s+user_id\s*=\s*p_user_id\s+FOR\s+UPDATE", body)
    assert lock and lock.start() < body.index("INTO v_mark"), f"{source}: the mark must be read under the lock"


def test_a_reinstatement_deducts_the_granted_pool_spend_after_the_clawback():
    """F15 (c): REFUND_REVERSED gives back revoked_remaining less the window's spend — every
    granted-pool movement after the mark, up to the next paid period's start, that is not a system
    reason (the SQL's NOT IN list = the model's `_GRANTED_POOL_SYSTEM_REASONS`). A period with no
    mark (clawed back before 200) is reinstated exactly as before."""
    source, body = _latest_body("ensure_credit_period")
    branch = _branch(body, r"IF\s+v_reinstate\s+THEN", "RETURN v_restore;")
    n = _norm(branch)
    exclude = re.search(r"AND t\.reason NOT IN \((.*?)\);", n)
    assert exclude, f"{source}: the window's reason exclusion is gone"
    assert {r.strip().strip("'") for r in exclude.group(1).split(",")} == pool._GRANTED_POOL_SYSTEM_REASONS
    for piece in (
        "v_total := v_restore; v_used := 0;",
        "IF v_seen_row.revoked_ledger_id IS NOT NULL THEN",
        "SELECT min(t.id) INTO v_stop FROM public.credit_transactions t WHERE t.user_id = p_user_id "
        "AND t.id > v_seen_row.revoked_ledger_id AND t.reason IN ('plan_period', 'plan_reinstated');",
        # final review (2026-10-21): refunds of PRE-mark charges go back into the total; the
        # window's own charges and refunds net into `used`
        "SELECT COALESCE(SUM(t.granted_delta) FILTER (WHERE t.reverses_id IS NOT NULL "
        "AND t.reverses_id <= v_seen_row.revoked_ledger_id), 0), "
        "COALESCE(-SUM(t.granted_delta) FILTER (WHERE t.reverses_id IS NULL "
        "OR t.reverses_id > v_seen_row.revoked_ledger_id), 0) INTO v_pre_back, v_window "
        "FROM public.credit_transactions t "
        "WHERE t.user_id = p_user_id AND t.id > v_seen_row.revoked_ledger_id "
        "AND (v_stop IS NULL OR t.id < v_stop) AND t.reason NOT IN (",
        "v_total := v_restore + v_pre_back + GREATEST(-v_window, 0); "
        "v_used := LEAST(GREATEST(v_window, 0), v_total);",
        "v_restore := v_total - v_used;",
    ):
        assert n.count(piece) == 1, f"{source}: the reinstatement lost (or doubled) {piece!r}"
    balance = re.search(r"UPDATE public\.user_credits SET (.*?) WHERE user_id = p_user_id;", n)
    assert balance, source
    items = set_items(balance.group(1))
    assert (items["total"], items["used"]) == ("v_total", "v_used"), items
    assert n.index("v_restore := v_total - v_used;") < n.index("VALUES (p_user_id, v_restore, v_restore, 0, 'plan_reinstated'"), (
        f"{source}: the plan_reinstated row must carry what was actually restored")


def test_the_window_counts_no_system_reason_and_every_spend_or_refund():
    """The NOT IN list decides what a reinstatement deducts. A granted-pool system reason missing
    from it would be read as spend (a reset or an expiry deducted twice); a spend or refund in it
    would be given back for free. Every reason the history screen knows is classified here."""
    from app.services import credit_history_service as hist
    system = pool._GRANTED_POOL_SYSTEM_REASONS
    for reason, (kind, _title, _ref) in hist._REASONS.items():
        if reason.startswith("pack_"):
            continue                            # purchased pool only: granted_delta is 0 either way
        if kind in (hist.KIND_GRANT, hist.KIND_EXPIRE, hist.KIND_REVOKE):
            assert reason in system, f"{reason} ({kind}) moves the granted pool outside any spend"
        else:
            assert reason not in system, f"{reason} ({kind}) is a spend or a refund: window spend"
    # And every reason literal any migration's ledger INSERT writes (a new reason must be added to
    # the history table first — its own coverage guard — so the loop above classifies it by kind).
    written = set()
    for _name, text in migration_sources():
        for _at, stmt in statements(strip_sql_comments(text), r"\bINSERT INTO (?:public\.)?credit_transactions\b"):
            written |= set(re.findall(r"'([a-z_]+)'", stmt)) & set(hist._REASONS)
    assert {"grant", "monthly_reset", "monthly_expired", "plan_period", "tier_revoked"} <= written, written
    assert {r for r in written if not r.startswith("pack_")} <= system, written - system


def test_refund_credits_rolls_the_period_first():
    """F10: after its own FOR UPDATE and the no_credits_row return (ensure would INSERT a row for
    a deleted user and raise 23503), before the debit search, and the pools are re-read."""
    source, body = _latest_body("refund_credits")
    n = _norm(body)
    roll = "PERFORM public.ensure_credit_period(p_user_id); "
    reread = "SELECT used, purchased_used INTO v_old_used, v_old_pused FROM public.user_credits WHERE user_id = p_user_id;"
    assert n.count(roll) == 1, f"{source}: refund_credits must roll the period exactly once"
    assert n.index(roll + reread) >= 0, f"{source}: the pools must be re-read right after the roll"
    lock = n.index("FROM public.user_credits WHERE user_id = p_user_id FOR UPDATE;")
    no_row = n.index("'outcome', 'no_credits_row'")
    search = n.index("IF p_ref_id IS NOT NULL AND p_ref_id <> '' THEN")
    assert lock < no_row < n.index(roll) < search, f"{source}: the roll is out of place"


def test_a_period_start_records_its_ledger_order_mark():
    """Final review (2026-10-21): the start branch stamps applied_ledger_id with its own plan_period
    row's id, read under the balance lock right after that row — the bound the cover lookup uses."""
    source, body = _latest_body("ensure_credit_period")
    branch = _norm(_branch(body, r"IF\s+v_start_paid\s+THEN", "RETURN (v_plan_alloc + v_carry);"))
    piece = ("VALUES (p_user_id, v_plan_alloc, v_plan_alloc, 0, 'plan_period', "
             "v_sub.original_transaction_id, v_plan_alloc + v_carry); "
             "SELECT max(t.id) INTO v_applied FROM public.credit_transactions t WHERE t.user_id = p_user_id; "
             "UPDATE public.subscription_credit_periods SET applied_ledger_id = v_applied WHERE id = v_period_id;")
    assert branch.count(piece) == 1, f"{source}: the start's ledger-order mark changed:\n  {branch}"


def test_refund_credits_absorbs_a_covered_charge_before_the_caps():
    """F15: the cover lookup — a clawed-back period whose clawback came after the debit
    (revoked_ledger_id >= the debit) and which the debit was charged in (in LEDGER order, after the
    period's own plan_period row; periods started before 200 fall back to applied_at <= its
    created_at) — runs before the caps; a wholly or partly covered charge whose caps move nothing
    is paired by a 0/0/0 row and returns before 142's capped_to_zero relabel."""
    source, body = _latest_body("refund_credits")
    n = _norm(body)
    lookup = ("SELECT p.id, p.revoked_cover INTO v_cover_id, v_cover FROM public.subscription_credit_periods p "
              "WHERE p.user_id = p_user_id AND p.revoked_at IS NOT NULL AND p.revoked_cover > 0 "
              "AND p.revoked_ledger_id >= v_debit_id "
              "AND (CASE WHEN p.applied_ledger_id IS NOT NULL THEN p.applied_ledger_id < v_debit_id "
              "ELSE p.applied_at <= v_debit_at END) "
              "ORDER BY p.revoked_ledger_id, p.id LIMIT 1 FOR UPDATE;")
    assert n.count(lookup) == 1, f"{source}: the cover lookup changed"
    assert "t.created_at INTO v_debit_id, v_spent_granted, v_spent_purch, v_debit_at" in n, source
    absorb = ("v_absorb := LEAST(v_spent_granted, v_cover); UPDATE public.subscription_credit_periods "
              "SET revoked_cover = revoked_cover - v_absorb, revoked_remaining = COALESCE(revoked_remaining, 0) "
              "+ v_absorb WHERE id = v_cover_id; v_spent_granted := v_spent_granted - v_absorb;")
    assert n.count(absorb) == 1, f"{source}: the absorb step changed"
    caps = n.index("IF v_have_split THEN v_back_purch")
    zero = n.index("IF v_back_granted + v_back_purch = 0 THEN")
    assert n.index(lookup) < caps < zero, f"{source}: the cover must be taken before the caps"
    block = if_block(n, zero)
    covered = block.index("IF v_absorb > 0 THEN")
    assert covered < block.index("IF v_outcome = 'refunded' AND v_debit_id IS NOT NULL THEN"), (
        f"{source}: a covered charge must return before 142's capped_to_zero relabel")
    pairing = [r for r in insert_rows(block, "public.credit_transactions")]
    assert pairing == [{"user_id": "p_user_id", "delta": "0", "granted_delta": "0", "purchased_delta": "0",
                        "reason": "p_reason", "ref_id": "p_ref_id", "balance_after": "v_spendable",
                        "reverses_id": "v_debit_id", "covered": "v_absorb"}], pairing
    # Which of the two (review of 200, 2026-10-21): wholly covered → covered_by_clawback (settled);
    # any rest left → capped_to_zero (owed). An inverted or always-true test silences a real owed rest.
    branch = re.search(r"IF v_absorb > 0 THEN IF (.*?) THEN v_outcome := 'capped_to_zero'; RAISE WARNING "
                       r"[^;]*; ELSE v_outcome := 'covered_by_clawback'; END IF;", block)
    assert branch and branch.group(1) == "v_spent_granted + GREATEST(COALESCE(v_spent_purch, 0), 0) > 0", (
        f"{source}: the covered-vs-owed decision changed")
    assert ("RETURN jsonb_build_object('outcome', v_outcome, 'refunded', 0, 'covered', v_absorb, "
            "'spendable', v_spendable);") in block, f"{source}: the covered reply changed"
    assert n.count("|| CASE WHEN v_absorb > 0 THEN jsonb_build_object('covered', v_absorb) ELSE '{}'::jsonb END;") == 1, (
        f"{source}: a refunded reply must carry `covered` exactly when a clawback absorbed some of it")
    moved = [r for r in insert_rows(n, "public.credit_transactions") if r.get("delta") != "0"]
    assert [r.get("covered") for r in moved] == ["v_absorb"], (
        f"{source}: the refund row must record what the clawback covered: {moved}")


def test_a_replay_of_a_short_refund_reports_what_is_still_owed():
    """Review of 200 (2026-10-21): a lost reply's retry answers already_refunded. The earlier refund
    settled its rows' delta + covered; short of the charge, the replay returns `short` so
    credit_service pages it as partial instead of reading the owed rest as settled."""
    source, body = _latest_body("refund_credits")
    n = _norm(body)
    for piece in (
        "v_outcome := 'already_refunded'; SELECT COALESCE(SUM(r.delta), 0) + COALESCE(SUM(r.covered), 0) "
        "INTO v_settled FROM public.credit_transactions r WHERE r.reverses_id = v_reversed_id; "
        "v_short := GREATEST(p_amount - v_settled, 0);",
        "RETURN jsonb_build_object('outcome', v_outcome, 'refunded', 0, 'spendable', v_spendable) "
        "|| CASE WHEN v_short > 0 THEN jsonb_build_object('short', v_short) ELSE '{}'::jsonb END;",
    ):
        assert n.count(piece) == 1, f"{source}: the replay's shortfall lost (or doubled) {piece!r}"


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


_AFTER_200 = re.compile(
    r"IF\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+information_schema\.columns\s+WHERE\s+table_schema\s*=\s*'public'\s+"
    r"AND\s+table_name\s*=\s*'subscription_credit_periods'\s+AND\s+column_name\s*=\s*'revoked_ledger_id'\s*\)\s+"
    r"THEN\s+RAISE\s+EXCEPTION\s+'(\d{3}) must not be applied after 200")


def test_142_and_198_refuse_to_run_after_200_and_the_older_guards_name_it():
    """Re-applying 142 (DROP + CREATE refund_credits) or 198 (ensure_credit_period) after 200 would
    put back the bodies without the period roll, the late renewal and the clawback cover. Keyed on
    200's column, the one fact that survives a later redefinition. 194/196's refusals point at 200."""
    for name in ("142_refund_credits_reports_its_outcome.sql", _M198):
        ddl = _migration_text(name)
        guard = _AFTER_200.search(ddl)
        assert guard and guard.group(1) == name[:3], name
        first_ddl = min(i for i in (ddl.find("DROP FUNCTION"), ddl.find("CREATE OR REPLACE FUNCTION"),
                                    ddl.find("ALTER TABLE"), ddl.find("CREATE TABLE")) if i >= 0)
        assert guard.start() < first_ddl and ddl.index("BEGIN;") < guard.start(), name
    for name in ("194_monthly_expired_ledger_row.sql", "196_paid_period_credit_grants.sql"):
        refusal = re.search(r"RAISE\s+EXCEPTION\s+'([^']*(?:''[^']*)*)'", _migration_text(name)).group(1)
        assert "200" in refusal, f"{name}'s refusal must name 200: {refusal}"
    # 100 defines ensure_credit_period with the same signature and return type: re-run after 196, it
    # would silently put back the calendar-only body (review of 200, 2026-10-21).
    ddl = _migration_text("100_credit_tiers_and_monthly_allocation.sql")
    guard = re.search(r"IF\s+to_regclass\('public\.subscription_credit_periods'\)\s+IS\s+NOT\s+NULL\s+THEN\s+"
                      r"RAISE\s+EXCEPTION\s+'100 must not be applied after 196[^']*200", ddl)
    assert guard and guard.start() < ddl.index("CREATE TABLE IF NOT EXISTS public.plan_credits"), (
        "100 must refuse to run once 196's table exists, before any statement")


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
    ddl = _migration_text("196_paid_period_credit_grants.sql")
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


# ── The shared reader, and the guards above against a LATER redefinition (IAP audit F20) ──────
#
# All in memory: `_with_later` appends one synthetic migration to the real sources through
# `_migration_sql.migration_sources`, which `latest_definition` looks up at call time. Nothing is
# written to migrations/. A test that calls `_with_later` is a non-vacuity test, never a guard:
# `_guards_on_the_reader` relies on that to find the guards.

_SYNTHETIC = "999_synthetic_redefinition.sql"
_CREATE_SPELLINGS = ["CREATE FUNCTION", "create function", "CREATE OR REPLACE FUNCTION"]
_SEVEN = _RPCS + ["subscriptions_keep_max_period_end"]


def _redefinition(name: str, body: str, create: str) -> str:
    """A later migration that redefines public.<name> with `body` (header copied from the live
    one). A plain CREATE comes after a DROP, the shape a return-type change forces."""
    header = latest_definition(name).header
    drop = "" if "replace" in create.lower() else f"DROP FUNCTION IF EXISTS public.{name};\n"
    return f"BEGIN;\n{drop}{create} public.{name}({header}AS $fn${body}$fn$;\nCOMMIT;\n"


def _with_later(monkeypatch, later: str) -> None:
    real = migration_sources()
    monkeypatch.setattr(_migration_sql, "migration_sources", lambda: real + [(_SYNTHETIC, later)])


@pytest.mark.parametrize("create", _CREATE_SPELLINGS)
@pytest.mark.parametrize("name", _SEVEN + ["refund_credits"])
def test_the_reader_takes_a_later_redefinition_in_every_create_spelling(name, create, monkeypatch):
    later = _redefinition(name, "\nBEGIN\n    RETURN NULL;\nEND;\n", create)
    _with_later(monkeypatch, later)
    got = latest_definition(name)
    assert got.file == _SYNTHETIC, f"a later `{create}` of {name}() was not read; got {got.file}"
    assert norm_sql(got.body) == "BEGIN RETURN NULL; END;", got.body


@pytest.mark.parametrize("name,floor", [
    # `>=`, never a pinned number: migration 200+ redefines some of these. The old reader answered
    # 139 for refund_credits — a body that stopped running when 142 dropped and re-created it.
    ("refund_credits", 142), ("grant_tier_upgrade", 196), ("ensure_credit_period", 198),
    ("revoke_tier_credits", 198), ("revoke_paid_period", 198), ("reinstate_paid_carry", 198),
    ("record_subscription_transaction", 198), ("subscriptions_keep_max_period_end", 198),
])
def test_the_live_definitions_are_at_least_the_ones_these_guards_were_written_for(name, floor):
    source = latest_definition(name).file
    assert int(source[:3]) >= floor, f"{name}() is read from {source}, older than {floor}"


def test_a_block_commented_copy_after_the_live_definition_is_not_read(monkeypatch):
    """A rollback copy kept in /* */ AFTER the redefinition must not be taken for the live body."""
    name = "revoke_paid_period"
    d = latest_definition(name)
    live = f"CREATE OR REPLACE FUNCTION public.{name}({d.header}AS $$\nBEGIN RETURN NULL; END;\n$$;\n"
    rollback = (f"/* rollback copy:\nCREATE OR REPLACE FUNCTION public.{name}({d.header}AS $$\n"
                f"{d.body}\n$$;\n*/\n")
    _with_later(monkeypatch, "BEGIN;\n" + live + rollback + "COMMIT;\n")
    got = latest_definition(name)
    assert got.file == _SYNTHETIC and norm_sql(got.body) == "BEGIN RETURN NULL; END;", got.body


def test_a_slash_star_inside_a_line_comment_or_a_literal_opens_nothing():
    """K82's old reader removed /* */ BEFORE `--` and literals, so a `/*` in a `--` line (110, 157,
    163, 173 have one) or in a '...' literal (113) paired with any later `*/` and deleted every
    definition in between."""
    sql = ("-- see /api/v1/admin/* for the callers\n"
           "COMMENT ON TABLE public.t IS 'Grants access to /api/v1/admin/*. Set manually';\n"
           "CREATE FUNCTION public.f() RETURNS int LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END $$;\n"
           "/* a real block comment */\n")
    out = strip_sql_comments(sql)
    assert "CREATE FUNCTION public.f()" in out and "'Grants access to /api/v1/admin/*. Set manually'" in out
    assert "see /api" not in out and "real block" not in out
    assert latest_definition("f", [("200_x.sql", sql)]).file == "200_x.sql"


def test_literals_are_text_and_block_comments_nest():
    s = "RAISE WARNING 'a -- b /* c'; x := 1; -- gone\ny := 2; /* outer /* inner */ still */ z := 3;"
    out = strip_sql_comments(s)
    assert norm_sql(out) == "RAISE WARNING 'a -- b /* c'; x := 1; y := 2; z := 3;", out
    assert strip_sql_comments("a /* x\n y */ b") == "a \n b", "a block comment must keep its lines"
    for broken in ("a /* never closed", "a /* x /* y */ still open", "x AS $fn$ BEGIN RETURN 1;"):
        with pytest.raises(AssertionError, match="unterminated"):
            strip_sql_comments(broken)


def test_a_stray_apostrophe_in_dollar_quoted_prose_stays_in_its_region():
    """A `'` in dollar-quoted prose opens no literal. Read as one, it pairs with the next `'` in the
    file and keeps every comment in between as code — here a commented-out purchased_total."""
    sql = ("COMMENT ON FUNCTION public.f() IS $c$it's the old one$c$;\n"
           "CREATE FUNCTION public.f() RETURNS int LANGUAGE plpgsql AS $$\nBEGIN\n"
           "    -- RETURN purchased_total;\n    RETURN 1; -- it's fine\nEND $$;\n")
    body = latest_definition("f", [("200_x.sql", sql)]).body
    assert norm_sql(body) == "BEGIN RETURN 1; END", body


def test_a_drop_after_the_last_definition_fails_loudly(monkeypatch):
    """A function dropped and not re-created has no live body; handing back the dropped one would
    keep every guard green over nothing. A DROP + CREATE pair (142) and a DROP of a LONGER name
    still read as live definitions."""
    sql = ("CREATE FUNCTION public.f() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;\n"
           "DROP FUNCTION IF EXISTS public.f_v2(), public.g();\n")
    assert latest_definition("f", [("200_x.sql", sql)]).file == "200_x.sql"
    with pytest.raises(AssertionError, match="DROPPED in 201_y.sql"):
        latest_definition("f", [("200_x.sql", sql), ("201_y.sql", "DROP FUNCTION public.g(), f();")])
    _with_later(monkeypatch, "BEGIN;\nDROP FUNCTION IF EXISTS "
                             "public.revoke_paid_period(UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ);\nCOMMIT;\n")
    with pytest.raises(AssertionError, match="DROPPED"):
        latest_definition("revoke_paid_period")
    assert int(latest_definition("refund_credits").file[:3]) >= 142, "142's DROP + CREATE must read as live"


def _inject_purchased_write(body: str) -> str:
    mutated = re.sub(r"\bBEGIN\b", "BEGIN\n    UPDATE public.user_credits SET purchased_total = 0 "
                     "WHERE user_id = p_user_id;", body, count=1)
    assert mutated != body, "no BEGIN to inject after — re-derive this mutation"
    return mutated


@pytest.mark.parametrize("create", _CREATE_SPELLINGS)
@pytest.mark.parametrize("name", _RPCS)
def test_a_later_redefinition_naming_the_purchased_pool_turns_the_3_1_1_guard_red(name, create, monkeypatch):
    """F20's scenario: a later migration re-creates a granted-pool writer (as DROP + plain CREATE
    when its return type changes) and the new body names purchased_total. The 3.1.1 guard must read
    THAT body and fail. The old reader kept reading 198 and stayed green."""
    test_the_tier_rpcs_never_name_the_purchased_pool(name)          # green on the real tree first
    later = _redefinition(name, _inject_purchased_write(latest_definition(name).body), create)
    _with_later(monkeypatch, later)
    assert _latest_body(name)[0] == _SYNTHETIC
    with pytest.raises(AssertionError, match="names the purchased pool"):
        test_the_tier_rpcs_never_name_the_purchased_pool(name)


def test_a_later_plain_create_without_the_register_recheck_turns_its_guard_red(monkeypatch):
    test_the_keyed_clawback_rechecks_the_refund_under_the_balance_lock()
    body = latest_definition("revoke_paid_period").body
    mutated = re.sub(r"SELECT \* INTO v_t FROM public\.subscription_transactions.*?'reversed'\);\s*END IF;",
                     "", body, count=1, flags=re.S)
    assert mutated != body, "the register re-check block moved — re-derive this mutation"
    _with_later(monkeypatch, _redefinition("revoke_paid_period", mutated, "CREATE FUNCTION"))
    with pytest.raises(AssertionError):
        test_the_keyed_clawback_rechecks_the_refund_under_the_balance_lock()


def _param_sets(fn) -> list[dict]:
    """The keyword sets pytest runs `fn` with, from its own parametrize marks."""
    names, combos = [], [()]
    for mark in getattr(fn, "pytestmark", []):
        if mark.name != "parametrize":
            continue
        argnames = mark.args[0]
        argnames = [a.strip() for a in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
        values = [v.values if type(v).__name__ == "ParameterSet" else v for v in mark.args[1]]
        values = [tuple(v) if len(argnames) > 1 else (v,) for v in values]
        names += argnames
        combos = [c + v for c, v in itertools.product(combos, values)]
    params = list(inspect.signature(fn).parameters)
    assert sorted(params) == sorted(names), (
        f"{fn.__name__} takes {params}, not only parametrized arguments: extend _param_sets")
    return [dict(zip(names, c)) for c in combos]


def _guards_on_the_reader() -> list[tuple]:
    """Every test in this module that reads a function through `_latest_body`, except the
    non-vacuity tests (they call `_with_later`), with each argument set it runs with."""
    out = []
    for attr, fn in sorted(globals().items()):
        if not (attr.startswith("test_") and inspect.isfunction(fn)):
            continue
        src = inspect.getsource(fn)
        if "_latest_body(" in src and "_with_later(" not in src:
            out += [(fn, kw) for kw in _param_sets(fn)]
    return out


def _passes(fn, kw) -> bool:
    try:
        fn(**kw)
    except Exception:                       # a ValueError on a vanished anchor is red too
        return False
    return True


def _stub_body() -> str:
    return "\nBEGIN\n    UPDATE public.user_credits SET purchased_total = 0;\n    RETURN NULL;\nEND;\n"


@pytest.mark.parametrize("create", ["CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"])
def test_every_guard_on_the_reader_turns_red_when_a_later_migration_stubs_its_function(create, monkeypatch):
    """The F20 property for ALL of them at once, including guards added later: one synthetic
    migration redefines the seven functions with a stub that names the purchased pool and does
    nothing else. Each guard must then fail — the 3.1.1 guard because the stub names
    purchased_total, every other one because what it pins is gone. A guard still green on the stub
    is not reading the live definition, or checks only an absence the stub does not violate:
    extend `_stub_body` rather than exempting it."""
    guards = _guards_on_the_reader()
    assert len(guards) >= 15, f"the sweep found {len(guards)} guard runs: _guards_on_the_reader has rotted"
    red_at_baseline = [f"{fn.__name__}{kw}" for fn, kw in guards if not _passes(fn, kw)]
    assert not red_at_baseline, f"guards red on the real tree: {red_at_baseline}"
    # refund_credits too: 200's F10 / F15 pins read it (IAP audit phase 2).
    later = "".join(_redefinition(n, _stub_body(), create) for n in _SEVEN + ["refund_credits"])
    _with_later(monkeypatch, later)
    green = [f"{fn.__name__}{kw}" for fn, kw in guards if _passes(fn, kw)]
    assert not green, f"still green over a stubbed later `{create}`: {green}"
