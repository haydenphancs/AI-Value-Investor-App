"""Two-pool credit arithmetic: granted (monthly, expires) vs purchased (IAP, never expires).

Migration 117 split `user_credits` into two pools because App Store Review Guideline 3.1.1
says *"Any credits or in-game currencies purchased via in-app purchase may not expire"*, and
three shipped RPCs would each have destroyed or mishandled a purchased balance:

  * `ensure_credit_period` hard-resets `total`/`used` every ET month — it would DELETE them.
  * `grant_tier_upgrade` no-ops when `alloc <= total` — a user holding a big pack would buy
    Pro and receive nothing.
  * `revoke_tier_credits` floors `total` when a SUBSCRIPTION is refunded — it would erase a
    separately-purchased pack.

Migration 118 then taught `spend_credits` / `refund_credits` about both pools.

These tests MODEL THE SQL rather than stub it, in the same spirit as
`test_iap_entitlement.FakeSupabase`: a fake that returns bland success for every RPC makes a
credit test pass no matter what the balance does, which is exactly how a money bug survives a
green suite. **If you change the SQL in 100/112/114/117/118, change `_Credits` below.**

The weight here is on the ways this goes wrong:
  * a purchased balance surviving every granted-pool mutation (the 3.1.1 property),
  * spend draining granted FIRST so the never-expiring pool lasts longest,
  * refunds reversing the RECORDED split — because refunding purchased-first is a laundering
    loop and refunding granted-first expires credits someone paid for.
"""

from __future__ import annotations

from typing import Optional

import pytest


_PLAN_CREDITS = {"free": 50, "pro": 1200, "premium": 4000}


class _Credits:
    """Executable model of `user_credits` + the seven RPCs that write it.

    Column names match the SQL exactly so a reader can diff this against the migrations.
    `remaining` is the GRANTED half only (unchanged meaning); `spendable` is the real balance.
    """

    def __init__(self, total=0, used=0, purchased_total=0, purchased_used=0, tier="free"):
        self.total = total
        self.used = used
        self.purchased_total = purchased_total
        self.purchased_used = purchased_used
        # migration 139: the allocation actually granted for the CURRENT period. `total`
        # cannot answer that once revoke_tier_credits overwrites it with a high-water mark.
        self.tier_alloc = total
        self.tier = tier
        self.period_due = False
        self.ledger: list[dict] = []
        self.purchases: dict[tuple[str, str], dict] = {}   # (environment, txn) -> row

    # ── generated columns ───────────────────────────────────────────────────
    @property
    def remaining(self) -> int:
        return self.total - self.used

    @property
    def purchased_remaining(self) -> int:
        return self.purchased_total - self.purchased_used

    @property
    def spendable(self) -> int:
        return self.remaining + self.purchased_remaining

    def _check(self):
        """The CHECK constraints from migrations 041 and 117. Any writer that trips one of
        these has a sign or ordering bug, so the model asserts rather than clamping."""
        assert self.total >= 0, "user_credits_total_nonneg"
        assert self.used >= 0, "user_credits_used_nonneg"
        assert self.purchased_total >= 0, "user_credits_purchased_total_nonneg"
        assert self.purchased_used >= 0, "user_credits_purchased_used_nonneg"
        assert self.purchased_used <= self.purchased_total, \
            "user_credits_purchased_used_le_total"

    def _ledger(self, delta, granted_delta, purchased_delta, reason, ref_id, reverses_id=None):
        self.ledger.append({
            "id": len(self.ledger) + 1,
            "delta": delta,
            "granted_delta": granted_delta,
            "purchased_delta": purchased_delta,
            "reason": reason,
            "ref_id": ref_id,
            "balance_after": self.spendable,
            # migration 124: which DEBIT row this refund reverses. A debit with a row pointing
            # at it is retired and can never be paired again.
            "reverses_id": reverses_id,
        })

    # ── migration 100 ───────────────────────────────────────────────────────
    def ensure_credit_period(self) -> int:
        """HARD reset of the granted pool at the ET month boundary. Column-explicit: it names
        only `total`, `used`, `resets_at`, `updated_at`, so the purchased pool is untouched."""
        alloc = _PLAN_CREDITS.get(self.tier, 0)
        if self.period_due:
            self.total = alloc
            self.used = 0
            self.tier_alloc = alloc          # migration 139
            self.period_due = False
            self._ledger(alloc, alloc, 0, "monthly_reset", None)
        self._check()
        return self.remaining

    # ── migration 112 ───────────────────────────────────────────────────────
    def grant_tier_upgrade(self) -> int:
        alloc = _PLAN_CREDITS.get(self.tier, 0)
        # migration 139: guard on tier_alloc, NOT on total. revoke_tier_credits overwrites
        # total with GREATEST(free_alloc, used) — a high-water mark, not an allocation — so
        # guarding on it made a PAID re-subscribe after an Apple refund grant nothing.
        if alloc > self.tier_alloc:
            delta = max(alloc - self.total, 0)
            self.total = max(alloc, self.total)   # only ever raises the ceiling
            self.tier_alloc = alloc
            if delta:
                self._ledger(delta, delta, 0, "tier_upgrade", None)
        self._check()
        return self.remaining

    # ── migration 114 ───────────────────────────────────────────────────────
    def revoke_tier_credits(self) -> int:
        free = _PLAN_CREDITS["free"]
        floor = max(free, self.used)
        # migration 139: tier_alloc drops to the free allocation whether or not `total` moves,
        # or grant_tier_upgrade's guard would still see the pre-revoke allocation and refuse a
        # genuine re-subscribe.
        self.tier_alloc = min(self.tier_alloc, free)
        # migration 139: write off the spend the refunded subscription paid for, exactly as
        # revoke_purchased_credits does for a charged-back pack. Leaving `used` alone left a
        # high-water mark that survived into the NEXT subscription, so a paid re-subscribe
        # raised `total` but `remaining` stayed 0. `remaining` is unchanged here.
        writeoff = max(self.used - free, 0)
        if self.total > floor or writeoff:
            delta = floor - self.total       # <= 0
            self.total = floor - writeoff
            self.used -= writeoff
            if delta:
                self._ledger(delta, delta, 0, "tier_revoked", None)
        self._check()
        return self.remaining

    # ── migration 118 ───────────────────────────────────────────────────────
    def spend_credits(self, amount: int, reason="report_charge", ref_id=None) -> Optional[int]:
        """Granted-first, then purchased. One guarded check-and-debit: the sufficiency test
        and the split are evaluated against the SAME pre-update state."""
        if amount is None or amount <= 0:
            return self.spendable
        if self.spendable < amount:
            return None                      # caller -> HTTP 402; nothing mutates
        granted_avail = max(0, self.total - self.used)
        granted_taken = min(amount, granted_avail)
        purchased_taken = max(0, amount - granted_avail)
        self.used += granted_taken
        self.purchased_used += purchased_taken
        self._check()
        self._ledger(-amount, -granted_taken, -purchased_taken, reason, ref_id)
        return self.spendable

    def refund_credits(self, amount: int, reason="report_refund", ref_id=None) -> Optional[int]:
        """Reverse the RECORDED split of the spend this refunds."""
        if amount is None or amount <= 0:
            return self.spendable
        debit_id = None
        spent_granted = spent_purch = None
        if ref_id:
            # migration 124: newest debit of this amount on this ref_id that NOTHING has
            # reversed yet. The un-reversed filter is the whole fix — without it two refunds
            # can select the SAME debit, which either returns credits to the wrong pool or
            # (once that debit's split is already given back) silently refunds nothing.
            reversed_ids = {r["reverses_id"] for r in self.ledger if r["reverses_id"]}
            for row in reversed(self.ledger):
                # migration 139: a clawback is not a spend. `pack_revoked` / `tier_revoked`
                # both carry a negative delta AND a ref_id, so without this exclusion a refund
                # could "reverse" a revocation and hand back credits deliberately taken away.
                if row["reason"] in ("pack_revoked", "tier_revoked"):
                    continue
                if (row["ref_id"] == ref_id and row["delta"] == -amount
                        and row["id"] not in reversed_ids):
                    debit_id = row["id"]
                    spent_granted = -row["granted_delta"]
                    spent_purch = -row["purchased_delta"]
                    break
        have_split = (spent_granted, spent_purch) != (None, None) and (
            (spent_granted or 0) != 0 or (spent_purch or 0) != 0
        )
        if have_split:
            back_purch = min(max(spent_purch or 0, 0), amount, self.purchased_used)
            back_granted = min(max(spent_granted or 0, 0), amount - back_purch, self.used)
        elif debit_id is not None or not ref_id:
            # A debit was found whose split is unknown (a pre-117 row), OR there was no ref_id
            # to search by. Both are genuinely unknown: granted-first, the direction that
            # cannot mint permanent credits — and the one that cannot swallow a real refund.
            back_granted = min(amount, self.used)
            back_purch = min(amount - back_granted, self.purchased_used)
        else:
            # migration 139: NO un-reversed debit matched — already refunded, or never
            # charged. The old code paid out `min(amount, self.used)` here, bounded by the
            # user's CURRENT spend rather than by the debit, so a second refund of the same
            # charge MINTED credits out of unrelated activity.
            back_granted = back_purch = 0
        if back_granted + back_purch == 0:
            return self.spendable
        self.used -= back_granted
        self.purchased_used -= back_purch
        self._check()
        self._ledger(back_granted + back_purch, back_granted, back_purch, reason, ref_id,
                     reverses_id=debit_id)
        return self.spendable

    # ── migration 117 ───────────────────────────────────────────────────────
    def add_purchased_credits(self, txn: str, credits: int, environment="Production",
                              user_id="u1") -> dict:
        if credits is None or credits <= 0:
            return {"outcome": "invalid", "reason": "non_positive_credits"}
        key = (environment, txn)
        if key in self.purchases:
            row = self.purchases[key]
            if row["user_id"] != user_id:
                return {"outcome": "conflict", "owner_user_id": row["user_id"]}
            return {"outcome": "replay", "credits": row["credits"],
                    "spendable": self.spendable}
        self.purchases[key] = {"user_id": user_id, "credits": credits, "revoked_at": None}
        self.ensure_credit_period()
        self.purchased_total += credits
        self._check()
        self._ledger(credits, 0, credits, "pack_purchase", txn)
        return {"outcome": "granted", "credits": credits, "spendable": self.spendable}

    def revoke_purchased_credits(self, txn: str, environment="Production") -> dict:
        key = (environment, txn)
        row = self.purchases.get(key)
        if row is None:
            return {"outcome": "unknown"}
        if row["revoked_at"] is not None:
            return {"outcome": "already_revoked", "spendable": self.spendable}
        row["revoked_at"] = "now"
        new_total = max(self.purchased_used, self.purchased_total - row["credits"])
        delta = new_total - self.purchased_total
        # migration 139: how far the floor lifted the total — the already-spent portion of
        # this charged-back pack. Leaving `purchased_used` alone meant a LATER report refund
        # lowered it and RAISED spendable on a pack Apple had refunded: money back AND credits
        # kept. Reduce both by the write-off so the spent baseline is retired too.
        writeoff = min(
            max(new_total - (self.purchased_total - row["credits"]), 0),
            self.purchased_used,
        )
        if delta == 0 and writeoff == 0:
            return {"outcome": "revoked", "spendable": self.spendable, "reclaimed": 0}
        self.purchased_total = new_total - writeoff
        self.purchased_used -= writeoff
        self._check()
        if delta:
            self._ledger(delta, 0, delta, "pack_revoked", txn)
        return {"outcome": "revoked", "spendable": self.spendable, "reclaimed": -delta}


# ── The 3.1.1 property: purchased credits survive every granted-pool mutation ──────────────

def test_monthly_reset_wipes_granted_and_leaves_purchased_intact():
    """THE App Store Guideline 3.1.1 test. If this ever fails, the app is destroying credits
    users paid cash for on the 1st of every month."""
    c = _Credits(total=50, used=50, purchased_total=250, purchased_used=0, tier="free")
    c.period_due = True
    c.ensure_credit_period()

    assert c.remaining == 50              # granted refilled
    assert c.purchased_remaining == 250   # untouched
    assert c.spendable == 300


def test_monthly_reset_does_not_zero_purchased_used_either():
    """`used = 0` in the reset must not be mistaken for "zero both used counters" — that would
    silently re-gift already-spent purchased credits."""
    c = _Credits(total=50, used=50, purchased_total=250, purchased_used=100, tier="free")
    c.period_due = True
    c.ensure_credit_period()

    assert c.purchased_used == 100
    assert c.spendable == 50 + 150


def test_tier_upgrade_still_grants_when_a_large_pack_is_held():
    """The bug this design exists to prevent: `grant_tier_upgrade` no-ops when
    `alloc <= total`. With one shared column, a user holding a purchased balance equal to
    Pro's allocation (1,200) sits at total=1250, so buying Pro grants NOTHING — $14.99 for
    zero credits.

    The 1200 below is load-bearing and must keep matching Pro's allocation, not any
    particular pack's size — it is what reaches the no-op branch. (Mega was 1,200 credits
    under migration 117's ladder and is 1,300 under 138's; the coincidence is gone, the
    test is not about Mega.)"""
    c = _Credits(total=50, used=50, purchased_total=1200, purchased_used=0, tier="free")
    c.tier = "pro"                        # reconcile_user_tier mirrors the purchased tier
    c.grant_tier_upgrade()

    assert c.total == 1200, "the upgrade must raise the GRANTED ceiling"
    assert c.remaining == 1150            # `used` is preserved (migration 112 §3)
    assert c.purchased_remaining == 1200  # and the pack is still there
    assert c.spendable == 2350


def test_subscription_refund_clawback_does_not_touch_a_separately_bought_pack():
    """`revoke_tier_credits` floors the GRANTED pool when a subscription is refunded. It must
    not reach a pack the user bought separately and did not get their money back for."""
    c = _Credits(total=4000, used=0, purchased_total=550, purchased_used=0, tier="premium")
    c.revoke_tier_credits()

    assert c.total == 50                  # floored to the free allocation
    assert c.purchased_remaining == 550
    assert c.spendable == 600


# ── Spend ordering ────────────────────────────────────────────────────────────────────────

def test_spend_drains_granted_before_purchased():
    """Granted-first is what makes "your purchased credits never expire" literally true: the
    expiring pool is consumed first so the permanent one survives as long as possible."""
    c = _Credits(total=50, used=0, purchased_total=250, purchased_used=0)
    c.spend_credits(20, ref_id="AAPL")

    assert c.used == 20
    assert c.purchased_used == 0
    assert c.ledger[-1]["granted_delta"] == -20
    assert c.ledger[-1]["purchased_delta"] == 0


def test_spend_straddles_both_pools_when_granted_runs_short():
    """A user with 15 granted and 1000 purchased must NOT get a 402 for a 20-credit report."""
    c = _Credits(total=50, used=35, purchased_total=1000, purchased_used=0)
    out = c.spend_credits(20, ref_id="MSFT")

    assert out is not None, "must not refuse a report the user can clearly afford"
    assert c.used == 50 and c.purchased_used == 5
    assert c.ledger[-1]["granted_delta"] == -15
    assert c.ledger[-1]["purchased_delta"] == -5


def test_spend_refused_only_when_the_combined_balance_is_short():
    c = _Credits(total=50, used=45, purchased_total=10, purchased_used=0)
    assert c.spend_credits(20, ref_id="X") is None
    assert (c.used, c.purchased_used) == (45, 0), "a refused spend must mutate nothing"

    assert c.spend_credits(15, ref_id="X") is not None


def test_spend_from_purchased_only():
    c = _Credits(total=50, used=50, purchased_total=250, purchased_used=0)
    c.spend_credits(20, ref_id="NVDA")

    assert c.used == 50 and c.purchased_used == 20
    assert c.spendable == 230


def test_non_positive_spend_is_a_noop_not_a_mint():
    """A negative amount would MINT credits through the debit expression."""
    c = _Credits(total=50, used=0, purchased_total=100, purchased_used=0)
    before = c.spendable
    c.spend_credits(-20, ref_id="X")
    c.spend_credits(0, ref_id="X")
    assert c.spendable == before
    assert c.ledger == []


# ── Refund ordering — the laundering / expiry trap ─────────────────────────────────────────

def test_refund_reverses_the_recorded_split():
    c = _Credits(total=50, used=35, purchased_total=1000, purchased_used=0)
    c.spend_credits(20, ref_id="MSFT")          # 15 granted + 5 purchased
    c.refund_credits(20, ref_id="MSFT")

    assert c.used == 35, "the granted portion goes back to granted"
    assert c.purchased_used == 0, "the purchased portion goes back to purchased"


def test_refund_cannot_launder_expiring_credits_into_permanent_ones():
    """The reason refunds are NOT purchased-first.

    Granted 50 unspent, purchased 100 fully spent. A 20-credit report drains GRANTED and then
    fails. A purchased-first refund would hand the 20 back to the purchased pool — converting
    20 expiring credits into 20 permanent ones, free, repeatable on any failure the user can
    induce, draining the whole monthly allocation into the permanent pool every month.
    """
    c = _Credits(total=50, used=0, purchased_total=100, purchased_used=100)
    c.spend_credits(20, ref_id="TSLA")
    assert (c.used, c.purchased_used) == (20, 100)

    c.refund_credits(20, ref_id="TSLA")

    assert c.used == 0, "the granted credits must come back as GRANTED"
    assert c.purchased_used == 100, "the permanent pool must not grow"
    assert c.purchased_remaining == 0


def test_refund_cannot_expire_credits_the_user_paid_for():
    """The mirror trap, and the reason refunds are not granted-first either.

    Granted exhausted, so the spend came from PURCHASED. A granted-first refund would return
    it to the granted pool, which `ensure_credit_period` then wipes on the 1st — literally
    expiring a purchased credit, the 3.1.1 violation this whole design prevents.
    """
    c = _Credits(total=50, used=50, purchased_total=250, purchased_used=0, tier="free")
    c.spend_credits(20, ref_id="AMZN")
    assert (c.used, c.purchased_used) == (50, 20)

    c.refund_credits(20, ref_id="AMZN")
    assert c.purchased_used == 0, "must go back to the pool it came from"
    assert c.used == 50

    c.period_due = True
    c.ensure_credit_period()
    assert c.purchased_remaining == 250, "the refunded credit survived the month boundary"


def test_a_refund_keyed_differently_from_its_charge_now_no_ops_instead_of_expiring_them():
    """Why `ref_id` on a refund MUST still equal the one its charge used — and what changed.

    This is the shape of a real bug: `research_reconciliation_service.claim_and_mark_failed`
    refunded with `ref_id=report_id` while `research.py` charged with `ref_id=ticker`. The
    split lookup never matched, so every reconciled report failure took the granted-first
    fallback and a report paid for out of PURCHASED credits was refunded into the GRANTED
    pool — which the next monthly reset wipes. Paid credits, destroyed.

    Migration 139 changed the outcome, and the trade-off is worth stating plainly. The
    fallback now fires only when a debit was FOUND with an unknown split, or when there was no
    ref_id to search by at all. A mismatched key means we searched and found nothing, so the
    refund is a NO-OP: nothing is destroyed and nothing is minted, but the user is still owed.
    That is strictly better than expiring paid credits, and strictly worse than being correct
    — which is why the SQL raises a WARNING on this branch. It is the only signal a mismatched
    key produces, since by construction there is no row to point at.

    The mismatch itself is fixed at every call site and pinned by
    `test_research_reconciliation.test_refund_is_keyed_by_ticker_to_match_the_charge`.
    """
    c = _Credits(total=50, used=50, purchased_total=500, purchased_used=0, tier="free")
    c.spend_credits(20, ref_id="NVDA")
    assert c.purchased_used == 20

    before = c.spendable
    c.refund_credits(20, ref_id="report-uuid-not-the-ticker")   # the bug
    assert c.used == 50 and c.purchased_used == 20, (
        "a mismatched ref_id must not move either pool — the pre-139 code refunded into "
        "granted, which the monthly reset then wiped"
    )
    assert c.spendable == before, "nothing minted, nothing destroyed"

    c.period_due = True
    c.ensure_credit_period()
    assert c.purchased_remaining == 480, "20 purchased credits were destroyed by the reset"

    # Keyed correctly, the same failure is a clean round trip.
    d = _Credits(total=50, used=50, purchased_total=500, purchased_used=0, tier="free")
    d.spend_credits(20, ref_id="NVDA")
    d.refund_credits(20, ref_id="NVDA")
    d.period_due = True
    d.ensure_credit_period()
    assert d.purchased_remaining == 500


def test_a_shared_ref_id_cannot_launder_across_two_different_costs():
    """`ref_id` is not unique per spend. Matching on the amount as well removes the case where
    a cheap chat turn's refund adopts an expensive report's split."""
    c = _Credits(total=50, used=30, purchased_total=100, purchased_used=0)
    c.spend_credits(20, ref_id="SESSION-1")   # 20 granted
    c.spend_credits(1, ref_id="SESSION-1")    # granted exhausted -> 1 purchased
    assert (c.used, c.purchased_used) == (50, 1)

    c.refund_credits(20, ref_id="SESSION-1")  # must match the 20, not the 1
    assert c.used == 30
    assert c.purchased_used == 1, "the permanent pool must not grow"


def test_two_same_cost_charges_on_one_ref_id_both_get_refunded():
    """The defect migration 124 fixes, and the one the earlier same-cost gap missed.

    `ref_id` is NOT unique per charge — `research.py` charges `ref_id=<TICKER>` at a fixed 20
    credits and the per-user cap explicitly allows 4 personas on one ticker at once. Before 124
    nothing stopped two refunds selecting the SAME debit row: the second one found that debit's
    split already returned, both caps collapsed to zero, and it wrote NO ledger row while
    returning non-NULL — so `CreditService.refund_ledgered` logged "Refunded 20 credits" for a
    refund that paid out nothing. The user was charged for an undelivered report and got nothing
    back, silently. A Gemini outage failing several in-flight same-ticker reports makes this the
    ordinary case, not a corner.
    """
    c = _Credits(total=50, used=10, purchased_total=100, purchased_used=0)
    start = c.spendable                                   # 40 granted + 100 purchased = 140

    c.spend_credits(20, ref_id="AAPL")                    # A: 20 granted
    c.spend_credits(20, ref_id="AAPL")                    # B: 20 granted
    c.spend_credits(20, ref_id="AAPL")                    # C: granted exhausted -> 20 purchased
    assert c.spendable == start - 60

    refund_rows_before = len([r for r in c.ledger if r["delta"] > 0])
    c.refund_credits(20, ref_id="AAPL")
    c.refund_credits(20, ref_id="AAPL")
    refunds = [r for r in c.ledger if r["delta"] > 0]

    assert len(refunds) - refund_rows_before == 2, \
        "each refund must pay out and write its own ledger row — a silent no-op is the bug"
    assert sum(r["delta"] for r in refunds) == 40
    assert c.spendable == start - 20, "60 charged, 40 refunded, one charge still outstanding"
    assert {r["reverses_id"] for r in refunds} == {3, 2}, \
        "the two refunds must pair with DIFFERENT debits"


def test_a_debit_can_never_be_reversed_twice():
    """The pairing constraint itself. This is what makes over-returning to either pool
    impossible regardless of the order refunds arrive in: a reversal returns at most what its
    paired debit took, and every debit is paired at most once."""
    c = _Credits(total=50, used=0, purchased_total=100, purchased_used=0)
    c.spend_credits(20, ref_id="X")
    c.refund_credits(20, ref_id="X")
    after_first = c.spendable

    # A contract-violating second refund of the same single charge must not pay out again.
    c.refund_credits(20, ref_id="X")
    assert c.spendable == after_first, "double refund of one charge minted credits"


def test_no_refund_sequence_can_return_more_to_purchased_than_was_taken():
    """The anti-laundering invariant, asserted directly rather than inferred.

    Across ANY interleaving of same-cost charges and refunds on a shared ref_id, the permanent
    pool can never grow: sum(returned to purchased) <= sum(taken from purchased). That property
    comes from pairing-at-most-once, not from the ORDER BY, which is why the residual
    attribution ambiguity documented in migration 124 is safe.
    """
    c = _Credits(total=50, used=30, purchased_total=100, purchased_used=0)
    for _ in range(4):
        c.spend_credits(20, ref_id="NVDA")
    for _ in range(4):
        c.refund_credits(20, ref_id="NVDA")

    taken = -sum(r["purchased_delta"] for r in c.ledger if r["purchased_delta"] < 0)
    given = sum(r["purchased_delta"] for r in c.ledger if r["purchased_delta"] > 0)
    assert given <= taken, f"returned {given} to the permanent pool but only took {taken}"
    assert c.purchased_total == 100, "purchased_total must never move on a spend/refund"
    assert c.spendable == 20 + 100, "a full charge/refund round trip conserves the balance"


def test_refund_without_a_recorded_split_falls_back_to_granted_first():
    """Ledger rows written before migration 117 carry granted_delta = purchased_delta = 0 with
    a non-zero delta: the split is UNKNOWN, not zero. Those spends were necessarily 100%
    granted (the purchased pool did not exist), and granted-first is the direction that cannot
    mint permanent credits."""
    c = _Credits(total=50, used=20, purchased_total=100, purchased_used=10)
    c.ledger.append({"id": 1, "delta": -20, "granted_delta": 0, "purchased_delta": 0,
                     "reason": "report_charge", "ref_id": "LEGACY", "balance_after": 0,
                     "reverses_id": None})

    c.refund_credits(20, ref_id="LEGACY")
    assert c.used == 0
    assert c.purchased_used == 10, "the permanent pool must not grow on an unknown split"


def test_refund_with_no_ref_id_falls_back_and_still_overflows_rather_than_losing_credits():
    """If granted cannot absorb the whole refund, the remainder must land in purchased —
    otherwise the credits simply vanish."""
    c = _Credits(total=50, used=5, purchased_total=100, purchased_used=15)
    c.refund_credits(20, ref_id=None)

    assert c.used == 0
    assert c.purchased_used == 0
    assert c.spendable == 50 + 100


def test_refund_is_capped_by_what_each_pool_shows_as_spent():
    """A contract-forbidden over-refund must not drive either counter negative, and the ledger
    must record the ACTUAL movement so sum(delta) still ties out to the balance."""
    c = _Credits(total=50, used=5, purchased_total=100, purchased_used=0)
    c.refund_credits(999, ref_id=None)

    assert c.used == 0 and c.purchased_used == 0
    assert c.ledger[-1]["delta"] == 5, "ledger records what moved, not what was asked for"


def test_non_positive_refund_is_a_noop_not_a_debit():
    c = _Credits(total=50, used=20, purchased_total=100, purchased_used=10)
    before = c.spendable
    c.refund_credits(-5, ref_id="X")
    c.refund_credits(0, ref_id="X")
    assert c.spendable == before


# ── Purchase grant / revoke ───────────────────────────────────────────────────────────────

def test_pack_grant_is_exactly_once_across_replays():
    """StoreKit replays `Transaction.updates` on EVERY launch. If a replay minted credits,
    relaunching the app would be a free credit generator."""
    c = _Credits(total=50, used=0, tier="free")
    first = c.add_purchased_credits("TXN-1", 250)
    assert first["outcome"] == "granted"

    for _ in range(5):
        again = c.add_purchased_credits("TXN-1", 250)
        assert again["outcome"] == "replay"

    assert c.purchased_total == 250, "five replays must not grant five packs"


def test_two_distinct_purchases_of_the_same_pack_both_grant():
    """Each consumable purchase mints its own `transactionId`. Keying on
    `originalTransactionId` — as the subscription path does — would collapse them into one."""
    c = _Credits(total=50, used=0)
    c.add_purchased_credits("TXN-1", 250)
    c.add_purchased_credits("TXN-2", 250)
    assert c.purchased_total == 500


def test_same_transaction_id_in_a_different_environment_is_a_different_purchase():
    """Sandbox and Production transaction-id spaces are not guaranteed disjoint, which is why
    `environment` is part of the unique key: without it a tester's sandbox id could
    permanently block a real production purchase from ever being granted."""
    c = _Credits(total=50, used=0)
    a = c.add_purchased_credits("1000", 90, environment="Sandbox")
    b = c.add_purchased_credits("1000", 90, environment="Production")
    assert a["outcome"] == "granted" and b["outcome"] == "granted"
    assert c.purchased_total == 180


def test_pack_bound_to_another_account_is_refused_without_mutating():
    c = _Credits(total=50, used=0)
    c.add_purchased_credits("TXN-1", 250, user_id="owner")
    before = c.purchased_total

    out = c.add_purchased_credits("TXN-1", 250, user_id="someone-else")
    assert out["outcome"] == "conflict"
    assert c.purchased_total == before


def test_non_positive_pack_credits_are_refused_before_the_dedup_row_is_written():
    """A corrupt catalog row must not burn the transaction id. If it did, every redelivery
    afterwards would report "replay" and the purchase could never be repaired."""
    c = _Credits(total=50, used=0)
    out = c.add_purchased_credits("TXN-1", 0)
    assert out["outcome"] == "invalid"
    assert ("Production", "TXN-1") not in c.purchases

    assert c.add_purchased_credits("TXN-1", 250)["outcome"] == "granted"


def test_pack_revoke_reclaims_only_the_unspent_portion():
    c = _Credits(total=50, used=0)
    c.add_purchased_credits("TXN-1", 250)
    c.spend_credits(50, ref_id="X")       # 50 granted
    c.spend_credits(100, ref_id="Y")      # granted exhausted -> 100 purchased

    out = c.revoke_purchased_credits("TXN-1")
    assert out["reclaimed"] == 150, "only the UNSPENT 150 of the 250 pack is reclaimable"
    # migration 139: the 100 already spent from this charged-back pack is WRITTEN OFF —
    # both columns drop by it — so a later report refund cannot lower `purchased_used` and
    # resurrect credits Apple has refunded. `purchased_remaining` is 0 either way.
    assert c.purchased_total == 0 and c.purchased_used == 0
    assert c.purchased_remaining == 0


def test_pack_revoke_after_full_consumption_reclaims_nothing_and_stays_non_negative():
    """Arithmetically correct — spent credits are a business loss, and `spendable` must never
    go negative — but a real exposure, which `CreditService.revoke_purchased` logs distinctly."""
    c = _Credits(total=50, used=50)
    c.add_purchased_credits("TXN-1", 250)
    c.spend_credits(250, ref_id="X")
    assert c.purchased_used == 250

    out = c.revoke_purchased_credits("TXN-1")
    assert out["reclaimed"] == 0
    assert c.spendable >= 0


def test_pack_revoke_is_idempotent_across_apples_retries():
    """Apple retries a REFUND up to 5 times over ~8 hours."""
    c = _Credits(total=50, used=0)
    c.add_purchased_credits("TXN-1", 250)

    first = c.revoke_purchased_credits("TXN-1")
    assert first["outcome"] == "revoked" and first["reclaimed"] == 250
    for _ in range(4):
        again = c.revoke_purchased_credits("TXN-1")
        assert again["outcome"] == "already_revoked"
    assert c.purchased_total == 0


def test_revoke_for_an_unknown_transaction_is_reported_not_raised():
    """The webhook must answer 200 — Apple retries non-2xx for days."""
    assert _Credits().revoke_purchased_credits("NEVER-SEEN")["outcome"] == "unknown"


# ── End-to-end: the full lifecycle a real user goes through ────────────────────────────────

def test_free_user_buys_a_pack_spends_it_and_rolls_into_a_new_month():
    c = _Credits(total=50, used=0, tier="free")

    # Two reports exhaust the free allocation.
    assert c.spend_credits(20, ref_id="A") is not None
    assert c.spend_credits(20, ref_id="B") is not None
    assert c.spend_credits(20, ref_id="C") is None, "50 credits is 2 reports, not 3"

    # Buys the $4.99 Plus pack.
    c.add_purchased_credits("TXN-PLUS", 250)
    assert c.spendable == 10 + 250

    # The blocked report now goes through: 10 granted + 10 purchased.
    assert c.spend_credits(20, ref_id="C") is not None
    assert (c.used, c.purchased_used) == (50, 10)

    # It fails and is refunded — exactly reversed.
    c.refund_credits(20, ref_id="C")
    assert (c.used, c.purchased_used) == (40, 0)

    # New month: granted resets, purchased is untouched.
    c.period_due = True
    c.ensure_credit_period()
    assert c.remaining == 50
    assert c.purchased_remaining == 250
    assert c.spendable == 300


@pytest.mark.parametrize("amount", [1, 19, 20, 21, 300, 349, 350])
def test_ledger_deltas_always_tie_out_to_the_balance(amount):
    """`delta` must equal `granted_delta + purchased_delta` on every row, or the audit ledger
    stops reconciling against the balance it claims to explain."""
    c = _Credits(total=50, used=0, purchased_total=300, purchased_used=0)
    c.spend_credits(amount, ref_id="R")
    c.refund_credits(amount, ref_id="R")

    for row in c.ledger:
        assert row["delta"] == row["granted_delta"] + row["purchased_delta"], row
    assert c.spendable == 350, "a spend followed by its refund is a round trip"


# ── Migration 139: the three defects the adversarial review confirmed ──────────────────


def test_refunding_the_same_charge_twice_mints_nothing():
    """DEFECT A. The fallback used to pay `min(amount, used)` whenever no un-reversed debit
    was found — bounded by the user's CURRENT spend, not by the debit. So a second refund of
    an already-reversed charge paid out again from unrelated activity.

    Reachable without attacker control: a Cloudflare 520 or read timeout AFTER Postgres
    commits makes research.py refund a row that IS in the table, and the reconciliation sweep
    later refunds the same charge again.
    """
    c = _Credits(total=1200, used=0, tier="pro")
    c.spend_credits(60, ref_id="OTHER")      # unrelated spend the fallback used to raid
    c.spend_credits(20, ref_id="TSLA")
    assert c.used == 80

    c.refund_credits(20, ref_id="TSLA")
    assert c.used == 60, "first refund reverses the paired debit"
    after_first = c.spendable

    c.refund_credits(20, ref_id="TSLA")      # the double refund
    assert c.used == 60, "second refund minted 20 granted credits from unrelated spend"
    assert c.spendable == after_first


def test_a_clawback_row_is_never_paired_by_a_refund():
    """`pack_revoked` and `tier_revoked` both carry a negative delta AND a ref_id, so before
    139 they were pairable debits: a refund could 'reverse' a revocation and hand back credits
    that were deliberately taken away."""
    # TWO packs, so a live purchased_used survives the revoke — otherwise back_purch clamps to
    # 0 and the test passes whether or not the exclusion exists.
    c = _Credits(total=0, used=0)
    c.add_purchased_credits("TXN-1", 200)
    c.add_purchased_credits("TXN-2", 200)
    c.spend_credits(150, ref_id="X")         # purchased_used = 150
    c.revoke_purchased_credits("TXN-1")      # writes a pack_revoked row keyed on TXN-1
    assert c.purchased_used > 0, "precondition: a refund must have something to give back"
    before = c.spendable

    # Refund keyed on the transaction id, amount matching the clawback's magnitude.
    revoke_delta = next(r["delta"] for r in c.ledger if r["reason"] == "pack_revoked")
    assert revoke_delta < 0
    c.refund_credits(-revoke_delta, ref_id="TXN-1")

    assert c.spendable == before, "a refund reversed a revocation and handed the credits back"


def test_credits_apple_charged_back_cannot_be_resurrected_by_a_later_refund():
    """DEFECT B. `spendable` subtracts `purchased_used`, so lowering it RAISES the balance.
    revoke_purchased_credits floored `purchased_total` at `purchased_used` but left
    `purchased_used` alone — so a report refund landing after the chargeback handed back
    credits on a pack Apple had already refunded: money back AND credits kept."""
    c = _Credits(total=50, used=50)          # granted exhausted
    c.add_purchased_credits("TXN-1", 130)
    c.spend_credits(20, ref_id="AAPL")       # funded from purchased
    assert c.purchased_used == 20

    c.revoke_purchased_credits("TXN-1")      # Apple refunds the pack
    assert c.spendable == 0

    c.refund_credits(20, ref_id="AAPL")      # the report then fails and is refunded
    assert c.spendable == 0, "charged-back credits came back to life"
    assert c.purchased_remaining == 0


def test_a_paid_resubscribe_after_an_apple_refund_grants_the_full_allocation():
    """DEFECT C. revoke_tier_credits sets `total = GREATEST(free, used)` — a high-water mark,
    not an allocation — and grant_tier_upgrade's replay guard read that as 'already granted'.
    A Max subscriber who had spent >1200, got refunded, then subscribed to Pro paid $14.99 and
    received ZERO credits until the ET month rolled."""
    c = _Credits(total=4000, used=0, tier="premium")
    c.tier_alloc = 4000
    c.spend_credits(1500, ref_id="X")

    c.tier = "free"
    c.revoke_tier_credits()                  # Apple refunds the Max subscription
    assert c.remaining == 0

    c.tier = "pro"                           # a NEW paid subscription
    c.grant_tier_upgrade()
    assert c.remaining > 0, "a paid subscription granted nothing"
    assert c.tier_alloc == 1200


def test_a_same_tier_resubscribe_after_a_refund_also_grants():
    """The narrower half of DEFECT C: a Pro subscriber who spent the full 1,200 has `total`
    floored to exactly 1,200 on the revoke, so `1200 <= 1200` short-circuited too."""
    c = _Credits(total=1200, used=0, tier="pro")
    c.tier_alloc = 1200
    c.spend_credits(1200, ref_id="X")

    c.tier = "free"
    c.revoke_tier_credits()
    c.tier = "pro"
    c.grant_tier_upgrade()

    assert c.tier_alloc == 1200
    assert c.remaining > 0, "the same-tier re-purchase granted nothing"


def test_replaying_a_grant_within_one_period_still_no_ops():
    """The guard must still do its original job: StoreKit replays `Transaction.updates` on
    every launch, and each replay calls grant_tier_upgrade."""
    c = _Credits(total=1200, used=200, tier="pro")
    c.tier_alloc = 1200
    before_total, before_ledger = c.total, len(c.ledger)

    for _ in range(5):
        c.grant_tier_upgrade()

    assert c.total == before_total
    assert len(c.ledger) == before_ledger, "a replay wrote a ledger row"


def test_every_ledger_row_carries_an_honest_split():
    """The schema comment claims `delta = granted_delta + purchased_delta` for every row from
    118 onward. Three writers left both at 0 with a non-zero delta, making their rows
    indistinguishable from pre-117 unknown-split rows — which is what fed DEFECT A."""
    c = _Credits(total=0, used=0, tier="pro")
    c.period_due = True
    c.ensure_credit_period()
    c.spend_credits(20, ref_id="X")
    c.refund_credits(20, ref_id="X")
    c.tier = "free"
    c.revoke_tier_credits()
    c.tier = "premium"
    c.grant_tier_upgrade()

    assert c.ledger, "no ledger rows written"
    for row in c.ledger:
        assert row["delta"] == row["granted_delta"] + row["purchased_delta"], (
            f"{row['reason']} row has delta={row['delta']} but split "
            f"{row['granted_delta']}/{row['purchased_delta']}"
        )


def test_a_total_inflated_by_anything_but_an_allocation_cannot_block_a_grant():
    """What `tier_alloc` protects that `total` cannot.

    `total` is not an allocation — any writer may raise it (a pre-139 revoke high-water mark
    still on a live row, a future clawback, a hand-correction in Studio). Guarding the replay
    on it means any such value silently blocks a PAID grant. `tier_alloc` records only what a
    grant actually issued, so it cannot be inflated by anything else.

    This is the case the revoke write-off no longer produces on its own, which is exactly why
    it needs its own test: rows written before migration 139 still carry the old shape.
    """
    c = _Credits(total=4000, used=0, tier="pro")   # legacy row: total inflated, never granted
    c.tier_alloc = 0

    c.grant_tier_upgrade()

    assert c.tier_alloc == 1200, "the grant did not record the allocation it issued"
    assert c.remaining >= 1200, "a paid grant was blocked by a total it did not set"
