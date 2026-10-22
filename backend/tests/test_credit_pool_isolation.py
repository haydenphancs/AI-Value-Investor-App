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
green suite. **If you change the SQL in 100/112/114/117/118/142/196/198/200, change `_Credits`
below.**

The weight here is on the ways this goes wrong:
  * a purchased balance surviving every granted-pool mutation (the 3.1.1 property),
  * spend draining granted FIRST so the never-expiring pool lasts longest,
  * refunds reversing the RECORDED split — because refunding purchased-first is a laundering
    loop and refunding granted-first expires credits someone paid for.
"""

from __future__ import annotations

import calendar
import itertools
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pytest


_PLAN_CREDITS = {"free": 50, "pro": 1200, "premium": 4000}

# migration 196 — explicit, like iap_service._TIER_RANK, never the enum's declaration order.
_TIER_RANK = {"free": 0, "pro": 1, "premium": 2}
_ENTITLING = frozenset({"active", "grace_period", "billing_retry"})
_ET = ZoneInfo("America/New_York")
_USER_IDS = itertools.count(1)
_PERIOD_IDS = itertools.count(1)   # subscription_credit_periods.id

# migration 200 (IAP audit F15): ledger reasons that are NOT a spend or a refund of the granted
# pool. A reinstatement deducts the granted-pool movement of every OTHER reason after the
# clawback's mark (the window's calendar spend, net of refunds) — the SQL's NOT IN list.
_GRANTED_POOL_SYSTEM_REASONS = frozenset({
    "grant", "monthly_reset", "monthly_expired", "tier_upgrade", "tier_revoked", "plan_period",
    "plan_reinstated", "tester_grant",
})


def _minus_one_month(ts: datetime) -> datetime:
    """Postgres `timestamptz - INTERVAL '1 month'` in a UTC session: the day clamps to the
    shorter month (Mar 31 -> Feb 28)."""
    year, month = (ts.year, ts.month - 1) if ts.month > 1 else (ts.year - 1, 12)
    return ts.replace(year=year, month=month, day=min(ts.day, calendar.monthrange(year, month)[1]))


def _next_et_reset(now: datetime) -> datetime:
    """`(date_trunc('month', now AT TIME ZONE 'America/New_York') + INTERVAL '1 month')
    AT TIME ZONE 'America/New_York'` — midnight ET on the next 1st, in UTC. ET changes clocks at
    2am, so midnight on a 1st is never ambiguous."""
    local = now.astimezone(_ET)
    year, month = (local.year, local.month + 1) if local.month < 12 else (local.year + 1, 1)
    return datetime(year, month, 1, tzinfo=_ET).astimezone(timezone.utc)


class _Credits:
    """Executable model of `user_credits` + the seven RPCs that write it.

    Column names match the SQL exactly so a reader can diff this against the migrations.
    `remaining` is the GRANTED half only (unchanged meaning); `spendable` is the real balance.

    Migration 196 gave the granted pool two modes. With no `subscription` (Free, comp and
    hand-set accounts) it is the calendar month, sized by `comp_tier` — or by `tier` when the
    account has no subscriptions row at all — and `period_due` / a `resets_at` the `now`
    clock has passed rolls it. With a `subscription` (the ONE `subscriptions` row iap_service
    writes) each Apple paid period is granted once, from `periods`: pass one dict to several
    models to share the table's UNIQUE (original_transaction_id, period_end) key.

    Migration 198 made `subscriptions` one row per Apple LINEAGE: `subscriptions` is the list,
    `subscription` reads and writes a one-row account exactly as before, and `put_subscription`
    upserts one lineage's row. `register` models `subscription_transactions` (share one dict
    like `periods`), and the three keyed RPCs are `record_subscription_transaction`,
    `revoke_paid_period` and `reinstate_paid_carry`.
    """

    def __init__(self, total=0, used=0, purchased_total=0, purchased_used=0, tier="free",
                 *, comp_tier=None, now=None, periods=None, register=None):
        self.total = total
        self.used = used
        self.purchased_total = purchased_total
        self.purchased_used = purchased_used
        # migration 139: the allocation actually granted for the CURRENT period. `total`
        # cannot answer that once revoke_tier_credits overwrites it with a high-water mark.
        self.tier_alloc = total
        self.tier = tier
        self.period_due = False
        # migration 196
        self.comp_tier = comp_tier
        self.now = now or datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)
        self.resets_at: Optional[datetime] = None
        # migration 198: one row per lineage — otid, tier, status, end, environment, updated, id
        self.subscriptions: list[dict] = []
        self.paid_period: Optional[dict] = None    # the subscription_credit_periods row in force
        self.periods: dict = {} if periods is None else periods
        # migration 198: subscription_transactions, (environment, transaction_id) -> row
        self.register: dict = {} if register is None else register
        self.user_id = next(_USER_IDS)
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
        """The CHECK constraints from migrations 041, 117 and 140. Any writer that trips one
        of these has a sign or ordering bug, so the model asserts rather than clamping."""
        assert self.total >= 0, "user_credits_total_nonneg"
        assert self.used >= 0, "user_credits_used_nonneg"
        assert self.purchased_total >= 0, "user_credits_purchased_total_nonneg"
        assert self.purchased_used >= 0, "user_credits_purchased_used_nonneg"
        assert self.purchased_used <= self.purchased_total, \
            "user_credits_purchased_used_le_total"
        # migration 140. The granted pool's half of the same tripwire — the purchased pool
        # has had it since 117, and the asymmetry is why `spend_credits` still carries a
        # defensive `GREATEST(0, total - used)` clamp (118 names `used > total` explicitly as
        # "not forbidden by any CHECK" and the route to "a silent credit mint"). `<=`, not
        # `<`: revoke_tier_credits deliberately lands on total == used == free_alloc.
        # `_check` runs at the tail of every modelled RPC, so this arms every case in the file.
        assert self.used <= self.total, "user_credits_used_le_total"

    def _ledger(self, delta, granted_delta, purchased_delta, reason, ref_id, reverses_id=None,
                covered=0):
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
            # migration 200 reads it: a debit charged inside a period (applied_at <= created_at)
            # can be covered by that period's clawback.
            "created_at": self.now,
            # migration 200: on a refund row, what a clawback had already paid for of the charge.
            "covered": covered,
        })

    # ── migration 196: the ONE subscriptions row, as iap_service writes it ─────
    @property
    def subscription(self) -> Optional[dict]:
        """A one-row account's row (the live dict: a test may mutate it), as before 198."""
        return self.subscriptions[0] if self.subscriptions else None

    @subscription.setter
    def subscription(self, value: Optional[dict]) -> None:
        self.subscriptions = [] if value is None else [value]

    def set_subscription(self, otid, tier, status, end, environment=None):
        """The account's ONE row, replacing any others (the pre-198 shape). `environment` None =
        a row written before 196 or by older code: Production."""
        self.subscriptions = [self._sub_row(otid, tier, status, end, environment)]

    def put_subscription(self, otid, tier, status, end, environment=None):
        """migration 198: upsert THIS lineage's row; other lineages' rows stay."""
        row = self._sub_row(otid, tier, status, end, environment)
        for i, existing in enumerate(self.subscriptions):
            if existing["otid"] == otid:
                self.subscriptions[i] = {**row, "id": existing["id"]}
                return
        self.subscriptions.append(row)

    _row_seq = itertools.count(1)

    def _sub_row(self, otid, tier, status, end, environment, updated=None, row_id=None):
        seq = next(self._row_seq)
        return {"otid": otid, "tier": tier, "status": status, "end": end,
                "environment": environment, "updated": seq if updated is None else updated,
                "id": f"{seq:012d}" if row_id is None else row_id}

    def _winner(self) -> Optional[dict]:
        """198's ORDER BY, key for key: a non-empty lineage, one that can carry a period now,
        one that entitles, Production, the higher tier, the later end (NULLS LAST), the later
        write, then id ascending. Stable sorts, last key first."""
        rows = sorted(self.subscriptions, key=lambda r: str(r.get("id") or ""))
        for key in (
            lambda r: r.get("updated") or 0,
            lambda r: (r["end"] is not None, r["end"] or datetime.min.replace(tzinfo=timezone.utc)),
            lambda r: _TIER_RANK.get(r["tier"], 0),
            lambda r: (r.get("environment") or "Production") == "Production",
            lambda r: r["status"] in _ENTITLING,
            lambda r: (r["status"] == "active" and r["end"] is not None and r["end"] > self.now),
            lambda r: bool(r["otid"]),
        ):
            rows.sort(key=key, reverse=True)
        return rows[0] if rows else None

    def _lineage_row(self, otid) -> Optional[dict]:
        return next((r for r in self.subscriptions if r["otid"] == otid), None)

    def _prod_row(self) -> bool:
        """198: ANY Production row (any status) — calendar sizing, 196's rule kept."""
        return any((r.get("environment") or "Production") == "Production"
                   for r in self.subscriptions)

    # ── migration 198: subscription_transactions ───────────────────────────
    @staticmethod
    def _refunded(rec: dict) -> bool:
        return rec.get("rev") is not None and (rec.get("rr") is None or rec["rr"] < rec["rev"])

    def _register_refunded(self, otid, end) -> bool:
        return any(rec["otid"] == otid and rec.get("period_end") == end and self._refunded(rec)
                   for rec in self.register.values())

    def _calendar_tier(self) -> str:
        """users.tier never sizes a PRODUCTION paid allocation. It sizes the calendar
        allowance only without a Production subscriptions row: hand-set testers (no row) and
        Sandbox / TestFlight subscribers (renewing every few minutes, kept on pre-196 sizing)."""
        if self._prod_row():
            return self.comp_tier or "free"
        return self.tier or self.comp_tier or "free"

    def _calendar_alloc(self) -> int:
        return _PLAN_CREDITS.get(self._calendar_tier(), 0)

    def _calendar_roll(self, alloc: int) -> None:
        """194's reset. Ledger what the old period still held BEFORE the wipe, so the ledger
        keeps tying out to the balance across periods: GRANTED only, ref_id NULL (no refund can
        pair with it), and only when something expired. The SQL writes balance_after = 0 (the
        granted-pool convention) where `_ledger` stamps `spendable`; nothing here asserts
        balance_after. Column-explicit like the SQL, so the purchased pool is untouched."""
        if self.remaining > 0:
            self._ledger(-self.remaining, -self.remaining, 0, "monthly_expired", None)
        self.total, self.used, self.tier_alloc = alloc, 0, alloc
        self.period_due = False
        self.resets_at = _next_et_reset(self.now)
        self._ledger(alloc, alloc, 0, "monthly_reset", None)

    # ── migrations 100 → 194 → 196 → 198 ────────────────────────────────────
    def ensure_credit_period(self) -> int:
        """Branch for branch with 198's SQL, in its order: the calendar block (194's reset or
        the mid-month top-up), then the refund, the REFUND_REVERSED reinstatement, a new paid
        period, a running one, hold, lapse. `resets_at` None here means a FUTURE boundary
        (rows in these tests start mid-period); the SQL treats a NULL resets_at as due.

        198 over 196: `sub` is the WINNING row (`_winner`); `cur_sub` is the in-force period's
        own lineage row, which decides the refund backstop and HOLD; while it holds, another
        lineage's same-or-lower tier does not start; a period the register still holds a refund
        of is never reinstated; `revoke_requested_at` (revoke_paid_period) fires the refund.

        200 over 198: (F11) while the in-force lineage holds, its own row's LATER period is a new
        key whatever the row's status (unless the register holds a refund of it), and the retarget
        needs only that later period; (F15) the refund stamps `revoked_cover` / `revoked_ledger_id`
        and a reinstatement deducts the granted-pool spend after that mark."""
        cal_tier = self._calendar_tier()
        cal_alloc = _PLAN_CREDITS.get(cal_tier, 0)
        sub, cur = self._winner(), self.paid_period
        running = cur is not None and cur["end"] > self.now
        sub_prod = sub is not None and (sub.get("environment") or "Production") == "Production"
        paid_row = sub_prod and _TIER_RANK[sub["tier"]] > _TIER_RANK[cal_tier]
        cur_sub = self._lineage_row(cur["otid"]) if cur is not None else None
        cur_holds = (cur is not None and not running and cur_sub is not None
                     and (cur_sub.get("environment") or "Production") == "Production"
                     and cur_sub["status"] in _ENTITLING
                     and _TIER_RANK[cur_sub["tier"]] > _TIER_RANK[cal_tier])
        while True:                                     # the SQL's <<pick>> loop: at most twice
            new_key = (paid_row and sub["end"] is not None and bool(sub["otid"])
                       and ((sub["status"] == "active" and sub["end"] > self.now)
                            # 200 (IAP audit F11): the in-force lineage's own later period
                            or (cur_holds and sub["otid"] == cur["otid"] and sub["end"] > cur["end"]
                                and not self._register_refunded(sub["otid"], sub["end"])))
                       and not (cur is not None
                                and (cur["otid"], cur["end"]) == (sub["otid"], sub["end"])))
            seen = self.periods.get((sub["otid"], sub["end"])) if new_key else None
            reinstate = (new_key and seen is not None and seen["user"] == self.user_id
                         and seen.get("revoked_at") is not None and not running
                         and not self._register_refunded(seen["otid"], seen["end"]))
            start_paid = new_key and seen is None and (
                (not running and not (cur_holds and sub["otid"] != cur["otid"]
                                      and _TIER_RANK[sub["tier"]] <= _TIER_RANK[cur["tier"]]))
                or _TIER_RANK[sub["tier"]] > _TIER_RANK[(cur or {}).get("tier") or "free"])
            # 198: the winner neither starts nor reinstates while the in-force lineage holds →
            # that lineage's own row is evaluated once instead (its renewal is never skipped).
            # 200 (IAP audit F11): when it records a later period than the one in force, whatever
            # its status (cur_holds already requires an entitling Production row).
            if (start_paid or reinstate or not cur_holds
                    or cur_sub["end"] is None or not cur_sub["end"] > cur["end"]
                    or not cur_sub["otid"] or sub["otid"] == cur_sub["otid"]):
                break
            sub = cur_sub
            sub_prod = (sub.get("environment") or "Production") == "Production"
            paid_row = sub_prod and _TIER_RANK[sub["tier"]] > _TIER_RANK[cal_tier]

        if cur is None and not start_paid and not reinstate:          # CALENDAR MODE
            if self.period_due or (self.resets_at is not None and self.now >= self.resets_at):
                self._calendar_roll(cal_alloc)
            elif cal_alloc > self.tier_alloc:
                # migration 139: guard on tier_alloc, NOT on total. A raised complimentary (or
                # Sandbox) tier is topped up mid-month; total only ever rises here.
                delta = max(cal_alloc - self.total, 0)
                self.total = max(cal_alloc, self.total)
                self.tier_alloc = cal_alloc
                if delta:
                    self._ledger(delta, delta, 0, "tier_upgrade", None)
            self._check()
            return self.remaining

        remaining = self.remaining

        # REFUND of the period in force: its lineage row is revoked under its key, or 198's
        # revoke_paid_period asked for exactly this period.
        if cur is not None and (
                cur.get("revoke_requested_at") is not None
                or (cur_sub is not None and cur_sub["status"] == "revoked"
                    and cur_sub["otid"] == cur["otid"] and cur_sub["end"] == cur["end"])):
            floor = max(cal_alloc, self.used)
            writeoff = max(self.used - cal_alloc, 0)
            delta = floor - self.total
            cur["revoked_at"], cur["revoked_remaining"] = self.now, remaining
            # 200 (IAP audit F15): what the clawback covered, and where the ledger stood.
            cur["revoked_cover"] = writeoff
            cur["revoked_ledger_id"] = max((r["id"] for r in self.ledger), default=0)
            self.total, self.used = floor - writeoff, self.used - writeoff
            self.tier_alloc = min(self.tier_alloc, cal_alloc)
            self.paid_period, self.resets_at, self.period_due = None, _next_et_reset(self.now), False
            if delta:
                self._ledger(delta, delta, 0, "tier_revoked", None)
            self._check()
            return self.remaining

        # REFUND_REVERSED: the revoked period back, once, with what the refund took — less (200)
        # the granted-pool spend after the clawback's mark, up to the next paid period's start.
        if reinstate:
            restore = seen.get("revoked_remaining") or 0
            total, used = restore, 0
            mark = seen.get("revoked_ledger_id")
            if mark is not None:
                stop = min((r["id"] for r in self.ledger if r["id"] > mark
                            and r["reason"] in ("plan_period", "plan_reinstated")), default=None)
                rows = [r for r in self.ledger
                        if r["id"] > mark and (stop is None or r["id"] < stop)
                        and r["reason"] not in _GRANTED_POOL_SYSTEM_REASONS]
                # refunds of charges made before the clawback go back into the total; the rest
                # nets into `used` (final review, 2026-10-21)
                pre = [r for r in rows if r["reverses_id"] is not None and r["reverses_id"] <= mark]
                pre_back = sum(r["granted_delta"] for r in pre)
                window = -sum(r["granted_delta"] for r in rows if r not in pre)
                total = restore + pre_back + max(-window, 0)
                used = min(max(window, 0), total)
            if remaining > 0:
                self._ledger(-remaining, -remaining, 0, "monthly_expired", None)
            seen["revoked_at"], seen["revoked_remaining"], seen["reinstated_at"] = None, None, self.now
            seen["revoke_requested_at"] = None
            seen["revoked_cover"], seen["revoked_ledger_id"] = None, None
            restore = total - used
            self.total, self.used, self.tier_alloc = total, used, seen["allocation"]
            self.paid_period, self.resets_at = seen, seen["end"]
            if restore > 0:
                self._ledger(restore, restore, 0, "plan_reinstated", seen["otid"])
            self._check()
            return self.remaining

        if start_paid:                                                 # A NEW PAID PERIOD
            alloc = _PLAN_CREDITS.get(sub["tier"], 0)
            carry = 0
            if running:
                # floor(allocation x elapsed / span) in exact integer microseconds, multiplied
                # before dividing like the SQL (a float share can round either way at an exact
                # boundary); clamped to [0, allocation].
                us = timedelta(microseconds=1)
                span = (cur["end"] - cur["start"]) // us
                cap = (cur["allocation"] * ((self.now - cur["start"]) // us)) // span if span > 0 else 0
                carry = min(remaining, min(max(cap, 0), cur["allocation"]))
            row = {"id": next(_PERIOD_IDS), "user": self.user_id, "otid": sub["otid"],
                   "end": sub["end"],
                   "start": min(_minus_one_month(sub["end"]), self.now), "tier": sub["tier"],
                   "allocation": alloc, "carried": carry,
                   "source": "upgrade" if running else "new_period",
                   "revoked_at": None, "revoked_remaining": None, "reinstated_at": None,
                   # migration 198
                   "revoke_requested_at": None,
                   "carried_from": cur.get("id") if running else None,
                   "carry_revoked": None, "carry_revoked_at": None, "carry_reinstated_at": None,
                   # migration 196's applied_at (NOT NULL DEFAULT now()); 200's clawback stamps
                   "applied_at": self.now, "revoked_cover": None, "revoked_ledger_id": None,
                   "applied_ledger_id": None}
            self.periods[(sub["otid"], sub["end"])] = row
            if remaining - carry > 0:
                self._ledger(-(remaining - carry), -(remaining - carry), 0, "monthly_expired", None)
            self.total, self.used, self.tier_alloc = alloc + carry, 0, alloc
            self.paid_period, self.resets_at = row, sub["end"]
            self._ledger(alloc, alloc, 0, "plan_period", sub["otid"])
            row["applied_ledger_id"] = self.ledger[-1]["id"]          # 200: ledger-order start mark
            self._check()
            return self.remaining

        if running:                                                    # paid, in force
            self._check()
            return self.remaining

        # HOLD — 198: on the in-force period's own lineage row when it exists, else (196) on the
        # winning row.
        if (cur_holds if cur_sub is not None
                else (paid_row and sub["status"] in _ENTITLING)):
            self._check()
            return self.remaining
        self.paid_period = None                                        # LAPSE: "Expire at plan end"
        self._calendar_roll(cal_alloc)
        self._check()
        return self.remaining

    # ── migrations 112 → 196 ────────────────────────────────────────────────
    def grant_tier_upgrade(self) -> int:
        """Since 196 the same reconciliation: a paid allocation comes only from a paid
        period, and the calendar branch keeps 139's top-up for a raised tier."""
        return self.ensure_credit_period()

    # ── migrations 114 → 139 → 196 ──────────────────────────────────────────
    def revoke_tier_credits(self) -> int:
        free = _PLAN_CREDITS["free"]
        # migration 196: in paid mode, act only when the refunded transaction IS the period in
        # force (ensure_credit_period has normally done it already and left paid mode); a
        # refund of another period changes nothing. The period is marked for a reversal.
        # migration 198: the in-force period's own lineage row decides.
        cur = self.paid_period
        sub = self._lineage_row(cur["otid"]) if cur is not None else None
        if cur is not None:
            if not (sub is not None and sub["status"] == "revoked"
                    and sub["otid"] == cur["otid"] and sub["end"] == cur["end"]):
                self._check()
                return self.remaining
            if cur.get("revoked_at") is None:
                cur["revoked_at"], cur["revoked_remaining"] = self.now, self.remaining
        floor = max(free, self.used)
        # migration 139: tier_alloc drops to the free allocation whether or not `total` moves,
        # or the replay guard would still see the pre-revoke allocation and refuse a genuine
        # re-subscribe.
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
        # migration 196: a refund also ends paid mode; the pool follows the ET calendar again.
        if self.paid_period is not None:
            self.paid_period, self.resets_at, self.period_due = None, _next_et_reset(self.now), False
        self._check()
        return self.remaining

    # ── migration 198: the transaction register and the keyed clawback ───────
    def record_subscription_transaction(self, environment, transaction_id, original_transaction_id,
                                        user_id, tier, period_end, revocation_date, kind,
                                        signed_at) -> dict:
        """Every column only moves forward (GREATEST / COALESCE); a reversal needs Apple's date;
        a refund with none is stamped `now`."""
        if kind not in ("applied", "revoked", "reversed"):
            raise ValueError(f"unknown kind {kind!r}")
        if not environment or not transaction_id or not original_transaction_id:
            raise ValueError("environment, transaction_id and original_transaction_id are required")
        if kind == "reversed" and signed_at is None:
            raise ValueError("a reversal needs Apple's signedDate")
        at = signed_at if signed_at is not None else self.now
        rec = self.register.setdefault((environment, transaction_id), {
            "otid": original_transaction_id, "user": user_id, "tier": tier,
            "period_end": period_end, "revocation_date": None,
            "applied_at": None, "rev": None, "rr": None, "created": self.now})
        rec["user"] = rec["user"] if rec["user"] is not None else user_id
        rec["tier"] = rec["tier"] if rec["tier"] is not None else tier
        rec["period_end"] = rec["period_end"] if rec["period_end"] is not None else period_end
        rec["revocation_date"] = revocation_date if revocation_date is not None else rec["revocation_date"]
        if kind == "applied" and rec["applied_at"] is None:
            rec["applied_at"] = self.now
        if kind == "revoked":
            rec["rev"] = at if rec["rev"] is None else max(rec["rev"], at)
        if kind == "reversed":
            rec["rr"] = at if rec["rr"] is None else max(rec["rr"], at)
        return {"applied_at": rec["applied_at"], "revoked_signed_at": rec["rev"],
                "reversed_signed_at": rec["rr"], "user_id": rec["user"],
                "refunded": self._refunded(rec)}

    def _period_by_id(self, period_id) -> Optional[dict]:
        return next((p for p in self.periods.values() if p.get("id") == period_id), None)

    def _carried_from(self, period: dict) -> Optional[dict]:
        """The newest upgrade period of this account that carried leftovers from `period`."""
        hits = [p for p in self.periods.values()
                if p.get("carried_from") is not None and p.get("carried_from") == period.get("id")
                and p["user"] == self.user_id]
        return max(hits, key=lambda p: p["id"]) if hits else None

    def revoke_paid_period(self, environment, transaction_id, original_transaction_id,
                           period_end) -> dict:
        rec = self.register.get((environment, transaction_id))
        if rec is None:
            return {"outcome": "not_recorded"}
        if not self._refunded(rec):
            return {"outcome": "reversed"}
        period = self.periods.get((original_transaction_id, period_end))
        if period is None:
            return {"outcome": "no_period"}
        if period["user"] != self.user_id:
            return {"outcome": "other_owner"}
        if self.paid_period is period:
            if period.get("revoke_requested_at") is None:
                period["revoke_requested_at"] = self.now
            self.ensure_credit_period()
            self.ensure_credit_period()
            if self.paid_period is period:
                return {"outcome": "still_in_force"}
            return {"outcome": "clawed_back"}
        if period.get("revoked_at") is not None:
            return {"outcome": "already_revoked"}
        upgrade = self._carried_from(period)
        if upgrade is not None and upgrade.get("carry_revoked_at") is None:
            if self.paid_period is upgrade:
                x = min(upgrade["carried"], max(self.total - self.used, 0))
                if x > 0:
                    self.total -= x
                    self._ledger(-x, -x, 0, "tier_revoked", None)
                upgrade["carry_revoked"], upgrade["carry_revoked_at"] = x, self.now
                self._check()
                return {"outcome": "carry_clawed_back", "carry": x}
            if upgrade.get("revoked_at") is not None:
                x = min(upgrade["carried"], upgrade.get("revoked_remaining") or 0)
                upgrade["revoked_remaining"] = (upgrade.get("revoked_remaining") or 0) - x
                upgrade["carry_revoked"], upgrade["carry_revoked_at"] = x, self.now
                return {"outcome": "carry_clawed_back_from_revoked", "carry": x}
        return {"outcome": "not_in_force"}

    def reinstate_paid_carry(self, original_transaction_id, period_end) -> dict:
        period = self.periods.get((original_transaction_id, period_end))
        if period is None or period["user"] != self.user_id:
            return {"outcome": "no_period"}
        upgrade = self._carried_from(period)
        # carry_revoked_at is the "clawed back NOW" stamp: a reinstatement clears it, so a later
        # refund of the same period takes the carry again (198, fix-pass review 2026-10-15).
        if upgrade is None or upgrade.get("carry_revoked_at") is None:
            return {"outcome": "nothing_to_reinstate"}
        # Under the lock: a refund signed after the reversal keeps the carry clawed back.
        if self._register_refunded(original_transaction_id, period_end):
            return {"outcome": "still_refunded"}
        x = upgrade.get("carry_revoked") or 0
        if self.paid_period is upgrade:
            if x > 0:
                self.total += x
                self._ledger(x, x, 0, "plan_reinstated", original_transaction_id)
            upgrade["carry_reinstated_at"], upgrade["carry_revoked_at"] = self.now, None
            self._check()
            return {"outcome": "carry_reinstated", "carry": x}
        if upgrade.get("revoked_at") is not None:
            upgrade["revoked_remaining"] = (upgrade.get("revoked_remaining") or 0) + x
            upgrade["carry_reinstated_at"], upgrade["carry_revoked_at"] = self.now, None
            return {"outcome": "carry_reinstated_to_revoked", "carry": x}
        upgrade["carry_reinstated_at"], upgrade["carry_revoked_at"] = self.now, None
        return {"outcome": "carry_expired"}

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

    def refund_credits(self, amount: int, reason="report_refund", ref_id=None) -> dict:
        """Reverse the RECORDED split of the spend this refunds.

        migration 142: returns {outcome, refunded, spendable} instead of a bare `spendable`.
        Every branch of the SQL used to return the same integer, so a refund that moved ZERO
        was indistinguishable from one that worked — and the caller logged success either way.

        migration 200: it rolls the period first (F10), and a charge an Apple-refunded allocation
        already paid for is absorbed by that period's `revoked_cover` (F15): `covered` in the
        reply, `covered_by_clawback` when that is all of it.
        """
        if amount is None or amount <= 0:
            return {"outcome": "invalid", "reason": "non_positive_amount",
                    "refunded": 0, "spendable": self.spendable}
        # 200 (IAP audit F10): as spend_credits does, so a boundary is seen whether or not
        # anything rolled the period in between.
        self.ensure_credit_period()
        debit_id = debit_at = None
        spent_granted = spent_purch = None
        # Mirrors the SQL's `v_outcome TEXT := 'refunded'`: only the no-match branch overrides
        # it, so a genuine zero-cap refund still reports success-that-moved-nothing.
        outcome = "refunded"
        short = 0                            # migration 200: a replay's unsettled rest
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
                    debit_at = row.get("created_at")
                    break
        have_split = (spent_granted, spent_purch) != (None, None) and (
            (spent_granted or 0) != 0 or (spent_purch or 0) != 0
        )
        # 200 (IAP audit F15): the earliest clawback after this debit of a period the debit was
        # charged in, while its cover lasts.
        absorb = 0
        if have_split and (spent_granted or 0) > 0 and debit_at is not None:
            covers = [p for p in self.periods.values()
                      if p["user"] == self.user_id and p.get("revoked_at") is not None
                      and (p.get("revoked_cover") or 0) > 0
                      and p.get("revoked_ledger_id") is not None and p["revoked_ledger_id"] >= debit_id
                      and (p["applied_ledger_id"] < debit_id if p.get("applied_ledger_id") is not None
                           else p.get("applied_at") is not None and p["applied_at"] <= debit_at)]
            if covers:
                p = min(covers, key=lambda p: (p["revoked_ledger_id"], p["id"]))
                absorb = min(spent_granted, p["revoked_cover"])
                p["revoked_cover"] -= absorb
                p["revoked_remaining"] = (p.get("revoked_remaining") or 0) + absorb
                spent_granted -= absorb
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
            # migration 142: say WHICH of the two it was. Re-scan WITHOUT the un-reversed
            # filter: a hit means the debit exists but was already given back (benign replay);
            # a miss means the ref_id/amount matches no charge at all and the user is OWED.
            outcome = "no_matching_debit"
            for row in reversed(self.ledger):
                if row["reason"] in ("pack_revoked", "tier_revoked"):
                    continue
                if row["ref_id"] == ref_id and row["delta"] == -amount:
                    outcome = "already_refunded"
                    # migration 200: what the earlier refund settled (moved + covered); short of
                    # the charge, the replay says so.
                    settled = sum(r["delta"] + r.get("covered", 0) for r in self.ledger
                                  if r["reverses_id"] == row["id"])
                    short = max(amount - settled, 0)
                    break
        if back_granted + back_purch == 0:
            # 200: covered by a clawback — a 0/0/0 row pairs the debit, so a replay answers
            # already_refunded; the rest, if any, is owed (capped_to_zero).
            if absorb > 0:
                rest = spent_granted + max(spent_purch or 0, 0)
                self._ledger(0, 0, 0, reason, ref_id, reverses_id=debit_id, covered=absorb)
                return {"outcome": "capped_to_zero" if rest > 0 else "covered_by_clawback",
                        "refunded": 0, "covered": absorb, "spendable": self.spendable}
            # migration 142: a MATCHED debit whose caps collapsed to zero is not a success —
            # the reachable path is a charge and a refund straddling ensure_credit_period's
            # monthly reset of `used`. Not applied without a matched debit: unprovable.
            if outcome == "refunded" and debit_id is not None:
                outcome = "capped_to_zero"
            reply = {"outcome": outcome, "refunded": 0, "spendable": self.spendable}
            return {**reply, "short": short} if outcome == "already_refunded" and short > 0 else reply
        self.used -= back_granted
        self.purchased_used -= back_purch
        self._check()
        self._ledger(back_granted + back_purch, back_granted, back_purch, reason, ref_id,
                     reverses_id=debit_id, covered=absorb)
        reply = {"outcome": "refunded", "refunded": back_granted + back_purch,
                 "spendable": self.spendable}
        return {**reply, "covered": absorb} if absorb > 0 else reply

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


def test_no_writer_can_drive_used_past_total_on_the_granted_pool():
    """Migration 140's tripwire, asserted by name rather than only via `_check`.

    The purchased pool has had `purchased_used <= purchased_total` since 117; the granted pool
    had no equivalent, which is why `spend_credits` still carries a defensive
    `GREATEST(0, total - used)` — migration 118 names `used > total` as "not forbidden by any
    CHECK" and the route to "a silent credit mint".

    The interesting writer is `revoke_tier_credits`, the only one that LOWERS `total`. It moves
    both columns by the same write-off, so a heavy spender lands exactly on equality — which is
    why the constraint has to be `<=`. A `<` would reject every Apple subscription refund.
    """
    # Spend far past the free allocation, then take the subscription away.
    c = _Credits(total=1200, used=900, tier="pro")
    c.revoke_tier_credits()
    assert c.used <= c.total, "revoke_tier_credits drove used past total"
    assert (c.total, c.used) == (50, 50), (
        "a clawback must land on the free allocation with the spend written off, not on a "
        "high-water mark"
    )
    assert c.remaining == 0

    # The light spender: writeoff is 0 and `used` must simply survive under the new total.
    c2 = _Credits(total=1200, used=10, tier="pro")
    c2.revoke_tier_credits()
    assert (c2.total, c2.used) == (50, 10)
    assert c2.used <= c2.total

    # And a full drain-then-refund round trip stays inside the constraint at every step.
    c3 = _Credits(total=50, used=0)
    c3.spend_credits(50, ref_id="AAPL")
    assert (c3.used, c3.total) == (50, 50)
    c3.refund_credits(50, ref_id="AAPL")
    assert c3.used == 0 and c3.used <= c3.total


def test_a_fresh_signup_row_records_the_allocation_it_was_granted():
    """Migration 140(B). 139 added `tier_alloc` and backfilled it, but only for rows that
    existed at apply time — `create_user_credits` still inserted the pre-139 column list, so
    every account created afterwards carried `tier_alloc = 0` beside a non-zero `total`.

    `_Credits.__init__` models the post-140 trigger: the opening row states the allocation it
    was actually granted, which is what `grant_tier_upgrade`'s replay guard reads.
    """
    fresh = _Credits(total=50)
    assert fresh.tier_alloc == 50, (
        "a newly seeded row leaves tier_alloc at 0, so the replay guard compares against a "
        "number that never described a grant"
    )
    # And the guard still behaves: the first paid upgrade grants in full.
    fresh.tier = "pro"
    fresh.grant_tier_upgrade()
    assert (fresh.total, fresh.tier_alloc) == (1200, 1200)
    # A replayed delivery of the same purchase grants nothing further.
    fresh.grant_tier_upgrade()
    assert (fresh.total, fresh.tier_alloc) == (1200, 1200)
    assert len([r for r in fresh.ledger if r["reason"] == "tier_upgrade"]) == 1, (
        "a replayed upgrade wrote a second grant row"
    )


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


# ── migration 142: a refund that moved nothing must say WHY ───────────────────────────


def test_a_replayed_refund_reports_already_refunded_not_a_failure():
    """The benign half. A second settlement of the same charge is idempotent, not a fault —
    reporting it as one would page on every double-settled report, and alert fatigue is how
    the genuine `no_matching_debit` ends up ignored."""
    c = _Credits(total=50, used=0)
    c.spend_credits(20, ref_id="AAPL")
    first = c.refund_credits(20, ref_id="AAPL")
    assert first["outcome"] == "refunded" and first["refunded"] == 20

    second = c.refund_credits(20, ref_id="AAPL")
    assert second["outcome"] == "already_refunded", (
        "a replayed refund must be distinguishable from one that found no charge at all"
    )
    assert second["refunded"] == 0
    assert c.used == 0, "the replay must not move credits a second time"


def test_a_mismatched_ref_id_reports_no_matching_debit():
    """The loud half, and the reason this outcome exists. A refund keyed differently from its
    charge refunds nothing (139 stopped it MINTING), and the one-shot is_refunded CAS is
    already spent — so the user is permanently short and nobody finds out unless this says so.
    """
    c = _Credits(total=50, used=0)
    c.spend_credits(20, ref_id="NVDA")
    out = c.refund_credits(20, ref_id="r1-report-id")   # the historical bug's wrong key

    assert out["outcome"] == "no_matching_debit"
    assert out["refunded"] == 0
    assert c.used == 20, "nothing was refunded — the user is still owed"


def test_a_refund_whose_caps_resolve_to_zero_is_still_a_success():
    """Not every zero is a failure: with nothing recorded as spent there is nothing to give
    back. That must stay `refunded`, or the loud outcome loses its meaning."""
    out = _Credits(total=50, used=0).refund_credits(20, ref_id=None)
    assert out["outcome"] == "refunded" and out["refunded"] == 0


def test_a_matched_debit_that_refunds_nothing_is_not_reported_as_success():
    """migration 142. `ensure_credit_period` zeroes `used` at the month boundary, so a report
    charged in month M and refunded in M+1 — which the reconciliation sweep does on its own
    schedule — matches its debit but can absorb none of it. The user is owed and the one-shot
    is_refunded guard is already spent, so labelling this `refunded` hides the exact
    silent-money case this migration exists to surface."""
    c = _Credits(total=50, used=0)
    c.spend_credits(20, ref_id="AAPL")
    assert c.used == 20

    c.period_due = True            # the monthly reset lands between charge and refund
    c.ensure_credit_period()
    assert c.used == 0, "precondition: the reset zeroed the granted pool"

    out = c.refund_credits(20, ref_id="AAPL")
    assert out["refunded"] == 0
    assert out["outcome"] == "capped_to_zero", (
        "a matched debit that moved nothing was reported as a successful refund"
    )

    from app.services.credit_service import refund_did_not_happen
    assert refund_did_not_happen(out), "the call sites would not log a REFUND LEAK for this"


# ── 2026-10-12: one debit per ref is what makes a refund replay-safe ───────────────────
#
# The deep door now charges `{TICKER}:{report_id}` and the direct door
# `{TICKER}:{persona}:{uuid}`; chat already charged `{session}:{uuid}`. The CreditService
# retry, both doors' precharge compensations, the insert-failure refund and a raised claim's
# re-read refund are all safe ONLY because of the property these two tests pin.


def test_unique_refs_make_a_replayed_refund_converge():
    c = _Credits(total=100, used=0)
    c.spend_credits(20, ref_id="AAPL:r1")
    c.spend_credits(20, ref_id="AAPL:r2")
    first = c.refund_credits(20, ref_id="AAPL:r1")
    replay = c.refund_credits(20, ref_id="AAPL:r1")   # a retry after a lost reply
    assert first["outcome"] == "refunded" and first["refunded"] == 20
    assert replay["outcome"] == "already_refunded" and replay["refunded"] == 0
    assert c.spendable == 100 - 40 + 20, "spendable must rise by exactly ONE refund"
    r2_debit = next(r for r in c.ledger if r["ref_id"] == "AAPL:r2" and r["delta"] < 0)
    assert r2_debit["id"] not in {r["reverses_id"] for r in c.ledger}, (
        "the sibling report's debit was reversed by a replay"
    )

    # Contrast — the bare shared ref this replaced: the SAME replay reverses the sibling's
    # debit (a delivered report's), i.e. it mints 20 credits.
    legacy = _Credits(total=100, used=0)
    legacy.spend_credits(20, ref_id="AAPL")
    legacy.spend_credits(20, ref_id="AAPL")
    legacy.refund_credits(20, ref_id="AAPL")
    second = legacy.refund_credits(20, ref_id="AAPL")
    assert second["outcome"] == "refunded" and legacy.spendable == 100, (
        "on a shared ref the replay pays out a second time — the bug unique refs close"
    )


def test_the_legacy_fallback_never_pairs_with_a_new_ref_debit():
    """`refund_report_charge`'s CAS-winner fallback: a legacy row (charged on the bare ticker)
    misses on its per-report ref, then refunds on the bare ticker — which can reach only
    LEGACY debits, never a new per-report one."""
    c = _Credits(total=100, used=0)
    c.spend_credits(20, ref_id="AAPL")          # a legacy row's charge
    c.spend_credits(20, ref_id="AAPL:r2")       # a new row's charge
    miss = c.refund_credits(20, ref_id="AAPL:legacy-row-id")
    assert miss["outcome"] == "no_matching_debit", "the per-report ref must miss a legacy row"
    hit = c.refund_credits(20, ref_id="AAPL")
    assert hit["outcome"] == "refunded"
    reversed_ids = {r["reverses_id"] for r in c.ledger if r["reverses_id"]}
    new_debit = next(r for r in c.ledger if r["ref_id"] == "AAPL:r2" and r["delta"] < 0)
    legacy_debit = next(r for r in c.ledger if r["ref_id"] == "AAPL" and r["delta"] < 0)
    assert legacy_debit["id"] in reversed_ids and new_debit["id"] not in reversed_ids
    # And once every legacy debit is reversed, the bare ticker matches nothing ever again —
    # the legacy namespace only shrinks.
    again = c.refund_credits(20, ref_id="AAPL")
    assert again["outcome"] == "already_refunded" and again["refunded"] == 0


def test_a_compensation_for_a_charge_that_never_landed_moves_nothing():
    """Both report doors now compensate a precharge whose reply was lost. When the debit never
    committed, the per-charge ref matches no row at all: no_matching_debit, nothing moved —
    even with an earlier DELIVERED report of the same ticker+persona in the ledger."""
    c = _Credits(total=100, used=0)
    c.spend_credits(20, ref_id="AAPL:warren_buffett:" + "a" * 32)   # delivered last week
    out = c.refund_credits(20, ref_id="AAPL:warren_buffett:" + "b" * 32)
    assert out["outcome"] == "no_matching_debit" and out["refunded"] == 0
    assert c.used == 20, "a compensation reversed a delivered report's debit (a mint)"


# ── Migration 194: the monthly reset ledgers what expired ──────────────────────────────
#
# Before 194 the reset was the one balance change with no ledger row: `total - used` simply
# vanished, so SUM(delta) overstated the balance by every month's unspent allocation and the
# Credit History screen stopped adding up after the first reset.


def _ledger_sum(c: _Credits) -> int:
    return sum(r["delta"] for r in c.ledger)


def test_the_ledger_sums_to_the_balance_across_month_rolls():
    """Every balance-changing step, including three month rolls (one after a tier upgrade,
    one after a subscription refund), must keep SUM(delta) == spendable."""
    c = _Credits(total=0, used=0, tier="free")
    c.period_due = True
    c.ensure_credit_period()                       # a ledgered origin: +50 monthly_reset
    steps = []

    def check(step):
        steps.append(step)
        assert _ledger_sum(c) == c.spendable, (
            f"after {steps}: ledger sums to {_ledger_sum(c)} but the balance is {c.spendable}"
        )

    check("origin")
    c.spend_credits(20, ref_id="AAPL:r1")
    check("spend 20")
    assert c.add_purchased_credits("txn-1", 250)["outcome"] == "granted"
    check("buy 250")
    c.spend_credits(40, ref_id="NVDA:r2")          # 30 granted + 10 purchased
    check("spend 40 across both pools")
    assert c.refund_credits(40, ref_id="NVDA:r2")["outcome"] == "refunded"
    check("refund it")
    c.period_due = True
    c.ensure_credit_period()                       # 30 granted expire, +50
    check("month roll with 30 unspent")
    c.tier = "pro"
    c.period_due = True
    c.ensure_credit_period()                       # 50 expire, +1200
    check("roll into pro")
    c.tier = "free"
    c.revoke_tier_credits()
    check("subscription refunded")
    c.period_due = True
    c.ensure_credit_period()
    check("roll after the revoke")

    expiries = [r for r in c.ledger if r["reason"] == "monthly_expired"]
    assert [r["delta"] for r in expiries] == [-30, -50, -50], expiries
    resets = [r for r in c.ledger if r["reason"] == "monthly_reset"]
    for exp in expiries:
        # Written immediately BEFORE its reset (id order = the history's keyset order).
        nxt = next(r for r in c.ledger if r["id"] == exp["id"] + 1)
        assert nxt["reason"] == "monthly_reset", nxt
    assert len(resets) == 4


def test_a_fully_spent_month_ledgers_no_expiry():
    c = _Credits(total=50, used=0, tier="free")
    c.spend_credits(50, ref_id="AAPL:r1")
    c.period_due = True
    c.ensure_credit_period()
    assert not [r for r in c.ledger if r["reason"] == "monthly_expired"]
    assert all(r["delta"] != 0 for r in c.ledger), "a zero-delta row was written"


def test_an_expiry_never_touches_purchased_credits():
    """App Store 3.1.1: purchased credits never expire — not in the balance and not in the
    ledger's split either."""
    c = _Credits(total=50, used=10, purchased_total=250, purchased_used=40, tier="free")
    before = c.purchased_remaining
    c.period_due = True
    c.ensure_credit_period()
    assert c.purchased_remaining == before == 210
    expiry = next(r for r in c.ledger if r["reason"] == "monthly_expired")
    assert expiry["delta"] == expiry["granted_delta"] == -40
    assert expiry["purchased_delta"] == 0
    assert expiry["ref_id"] is None


def test_an_expiry_row_can_never_be_paired_by_a_refund():
    """ref_id NULL: `refund_credits` pairs on (ref_id, -amount) and NULL matches nothing, so no
    refund can ever reverse an expiry and hand the expired credits back."""
    c = _Credits(total=50, used=20, tier="free")
    c.period_due = True
    c.ensure_credit_period()                       # 30 expire
    expiry = next(r for r in c.ledger if r["reason"] == "monthly_expired")
    for ref in (None, "", "2026-10"):
        c.refund_credits(30, ref_id=ref)
    assert expiry["id"] not in {r["reverses_id"] for r in c.ledger if r["reverses_id"]}
