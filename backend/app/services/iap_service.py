"""
In-app purchase entitlement.

Turns an Apple-verified transaction into an entitlement:

    verified transaction
      -> product id -> tier
      -> upsert `subscriptions` (idempotent on original_transaction_id)
      -> mirror the WINNING tier onto `users.tier`
      -> ensure_credit_period() so the new tier's monthly credits land

Migration 100 built the data layer for exactly this and left the wiring to "a later
ENFORCEMENT phase": *"A server-side receipt validator / webhook updates it and mirrors the
winning tier onto users.tier."* This is that.

Two invariants worth stating up front, because both are easy to lose:

1. **Idempotency.** The same transaction arrives more than once by design — StoreKit's
   `Transaction.updates` replays on launch, restore-purchases re-submits, and Apple retries
   webhooks. Granting credits per delivery instead of per period would mint free credits on
   every app launch.

2. **The winning tier.** A user can hold several subscription rows (upgrade, re-subscribe,
   a lapsed one). `users.tier` must reflect the best CURRENTLY-ACTIVE entitlement, never
   just the most recently written row — otherwise a stale expired row can demote a paying
   customer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)


class IAPError(Exception):
    """Entitlement could not be applied."""


class UnknownProduct(IAPError):
    """A verified transaction for a product we don't recognise.

    Verified but unmapped is a genuine anomaly — someone bought something real that we
    can't price. Loud, and never silently treated as a free tier.
    """


class PurchaseBoundToAnotherAccount(IAPError):
    """The transaction is already owned by a different user, and ownership never moves.

    TERMINAL, not retryable — and that distinction is the whole reason this class exists.
    Sharing the generic `IAPError` meant `billing.py` answered it with `SYSTEM_BUSY` / 503 and
    "Reopen the app shortly and it will be applied." StoreKit reads a 5xx as "try again", so
    the client never calls `Transaction.finish()`, and `Transaction.updates` re-delivers the
    same transaction on every single launch — forever, against a condition that by definition
    can never clear. The user is told to wait for something that will not happen.

    Answering 409 instead lets the client finish the transaction and show something true.
    """


# Tier ordering, worst → best. Used to pick the winning entitlement, so a user holding both
# Pro and Max gets Max. Kept explicit rather than relying on the DB enum's declaration order.
_TIER_RANK: Dict[str, int] = {"free": 0, "pro": 1, "premium": 2}

# Statuses that count as entitling. Apple keeps a subscription "active" through the billing
# retry grace period, so a failed renewal does not instantly strip access.
_ENTITLING_STATUSES = {"active", "grace_period", "billing_retry"}


def product_tier_map() -> Dict[str, str]:
    """StoreKit product id → tier. Read from settings each call so a config change
    (or a test override) takes effect without a restart."""
    return {
        settings.IAP_PRODUCT_PRO_MONTHLY: "pro",
        settings.IAP_PRODUCT_MAX_MONTHLY: "premium",
    }


def tier_for_product(product_id: Optional[str]) -> str:
    """Resolve a product id to a tier, or raise."""
    if not product_id:
        raise UnknownProduct("transaction has no productId")
    tier = product_tier_map().get(product_id)
    if tier is None:
        raise UnknownProduct(
            f"productId {product_id!r} is not mapped to a tier — check "
            "IAP_PRODUCT_* settings against App Store Connect"
        )
    return tier


def _ms_to_dt(value: Any) -> Optional[datetime]:
    """Apple sends timestamps as milliseconds since epoch."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse a `timestamptz` string as Supabase returns it, tolerating junk.

    Always returns an AWARE datetime (or None) — comparing a naive one against the aware
    `expiresDate` derived from Apple's epoch-milliseconds raises TypeError, which on this
    path would 500 a webhook rather than degrade.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        # Postgres emits "+00:00"; some clients emit "Z". fromisoformat rejects "Z" < 3.11.
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _status_for_notification(notification_type: str, subtype: str) -> Optional[str]:
    """Subscription status implied by an App Store Server Notification, or None to derive it.

    The notification carries state the transaction alone cannot express. The costly case is
    `DID_FAIL_TO_RENEW` with subtype `GRACE_PERIOD`: the attached transaction's `expiresDate`
    has already passed, so `status_for_transaction` reads it as "expired" and the customer is
    demoted to free — while Apple explicitly keeps them entitled and is still retrying billing.
    A paying customer loses access for a failed card retry that Apple expects to succeed.

    `subtype` was already parsed and then never used. Returning None means "no better
    information than the payload", which is the correct answer for the client-verify path.
    """
    ntype = (notification_type or "").upper()
    stype = (subtype or "").upper()

    if ntype in {"REFUND", "REVOKE"}:
        return "revoked"
    if ntype == "EXPIRED":
        return "expired"
    if ntype in {"SUBSCRIBED", "DID_RENEW", "OFFER_REDEEMED"}:
        return "active"
    if ntype == "DID_FAIL_TO_RENEW":
        # GRACE_PERIOD → still entitled. No subtype → the grace period is over or was never
        # granted, so fall back to deriving from the payload rather than guessing entitlement.
        return "grace_period" if stype == "GRACE_PERIOD" else None
    if ntype == "DID_CHANGE_RENEWAL_STATUS":
        # Auto-renew turned on/off changes nothing about the CURRENT period's entitlement.
        return None
    return None


def status_for_transaction(payload: Dict[str, Any]) -> str:
    """Derive a subscription status from a verified transaction.

    Revocation and expiry are read from the payload rather than assumed active: a refunded
    purchase carries `revocationDate`, and a lapsed one an `expiresDate` in the past. Taking
    "we received a transaction" to mean "entitled" would keep refunded users on a paid tier.
    """
    if payload.get("revocationDate") or payload.get("revocationReason") is not None:
        return "revoked"
    expires = _ms_to_dt(payload.get("expiresDate"))
    if expires and expires <= datetime.now(timezone.utc):
        return "expired"
    return "active"


# Column added by migration 114. The code must work BEFORE it is applied, because migrations
# are applied by hand and the deploy order is not guaranteed — see the write path below.
_EVENT_AT_COLUMN = "last_event_at"


def _stale_delivery_reason(
    prior: Optional[Dict[str, Any]],
    *,
    status: str,
    event_at: Optional[datetime],
) -> Optional[str]:
    """Return why this delivery is older news than what we already stored, or None to apply.

    ORDERING IS ON APPLE'S `signedDate`, not on `expiresDate`. That distinction is the whole
    design and it is not obvious:

    `expiresDate` describes WHICH PERIOD a transaction is about; `signedDate` describes WHEN
    APPLE SAID IT. They disagree precisely in the case that matters — an EXPIRED notification
    is signed *late* and carries an *early* expiresDate, because it is telling you about the
    period that just ended. Ordering on `expiresDate` therefore classifies every ordinary
    expiry as "stale" and silently keeps lapsed subscribers on a paid tier: the opposite of
    the bug, and more expensive. (`test_expiry_notification_drops_the_tier` catches exactly
    that, which is how this was found.)

    Deliberately CONSERVATIVE — dropping a genuine event is as bad as applying a stale one:

      1. **No prior row, or no stored ordering key** — nothing to compare against, so apply.
         This is also the pre-migration-114 state, where the whole guard is inert by design.
      2. **No `signedDate` on the incoming record** — the client-verify path has none (it
         submits a transaction, not a notification). Unorderable, so apply.
      3. **Revocation always wins.** A refund/chargeback is authoritative, and leaving a
         refunded user entitled is the expensive direction.
      4. **Equal timestamps apply.** Apple can re-sign an identical payload; re-applying it is
         idempotent, whereas dropping it on a tie could lose a real transition.
    """
    if not prior:
        return None
    if status == "revoked":
        return None
    if event_at is None:
        return None

    prior_event_at = _parse_iso(prior.get(_EVENT_AT_COLUMN))
    if prior_event_at is None:
        return None
    if event_at >= prior_event_at:
        return None

    return (
        f"incoming signedDate={event_at.isoformat()} predates the stored "
        f"{_EVENT_AT_COLUMN}={prior_event_at.isoformat()} "
        f"(stored status={prior.get('status')!r}, tier={prior.get('tier')!r})"
    )


class IAPService:
    def __init__(self):
        self.supabase = get_supabase()

    # ── Entitlement application ───────────────────────────────────────────────

    def apply_transaction(
        self,
        user_id: str,
        payload: Dict[str, Any],
        *,
        status_override: Optional[str] = None,
        event_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Record a verified transaction and reconcile the user's entitlement.

        `payload` MUST already be Apple-verified (integrations/app_store.py). This method
        does not re-check signatures — it trusts its caller to have done so, which is why
        nothing but the verified path may call it.

        Returns a summary: the resolved tier, the winning tier actually applied, and whether
        this delivery was new or a replay.
        """
        tier = tier_for_product(payload.get("productId"))
        # An App Store Server Notification knows things the transaction alone does not: a
        # DID_FAIL_TO_RENEW/GRACE_PERIOD carries a transaction whose expiresDate has already
        # passed, so deriving status from the payload reads it as "expired" — exactly when
        # Apple says the customer IS still entitled. The webhook passes the subtype's meaning
        # in here; the client-verify path leaves it None and derives as before.
        status = status_override or status_for_transaction(payload)
        original_txn_id = str(
            payload.get("originalTransactionId") or payload.get("transactionId")
        )
        period_end = _ms_to_dt(payload.get("expiresDate"))

        row = {
            "user_id": user_id,
            "tier": tier,
            "status": status,
            "store": "app_store",
            "original_transaction_id": original_txn_id,
            "current_period_end": period_end.isoformat() if period_end else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # When Apple told us WHEN it said this, persist it as the ordering key. Absent on the
        # client-verify path (a transaction has no signedDate), which is fine: that path is
        # user-initiated and never arrives out of order relative to itself.
        if event_at is not None:
            row[_EVENT_AT_COLUMN] = event_at.isoformat()

        # Idempotent on original_transaction_id: a replayed delivery updates the existing
        # row rather than inserting a duplicate entitlement.
        try:
            existing = (
                self.supabase.table("subscriptions")
                .select("*")
                .eq("original_transaction_id", original_txn_id)
                .limit(1)
                .execute()
            )
            prior = (existing.data or [None])[0]
        except Exception as e:
            logger.error(
                "IAP: subscriptions lookup failed for txn=%s user=%s: %s: %s",
                original_txn_id, user_id, type(e).__name__, e,
            )
            raise IAPError("could not read existing subscription") from e

        # "Replay" means specifically: we have seen THIS transaction before. Captured now,
        # before the owner-fallback below can also set `prior` — otherwise a genuinely new
        # re-subscribe would be reported to the client as a replay.
        txn_replay = bool(prior)

        # REFUSE A CROSS-ACCOUNT REBIND. The lookup above matches on the transaction alone,
        # while `row` carries `user_id` — so submitting a transaction that already belongs to
        # someone else used to UPDATE their row's user_id, silently MOVING the entitlement:
        # the original owner drops to free at the next reconcile and the submitter is granted
        # the tier and its credit allocation. `subscriptions_user_id_key` does not catch it,
        # because the recipient had no row of their own. An Apple-signed transaction proves a
        # purchase happened, never who is entitled to it, so ownership is decided here.
        if prior and prior.get("user_id") and prior["user_id"] != user_id:
            logger.error(
                "IAP: txn=%s is already bound to user=%s — refusing to rebind to user=%s",
                original_txn_id, prior["user_id"], user_id,
            )
            raise PurchaseBoundToAnotherAccount(
                "this purchase is already linked to another account"
            )

        # A user holds AT MOST ONE subscriptions row (`subscriptions_user_id_key UNIQUE
        # (user_id)`). So when the transaction is new to us but the user already has a row —
        # a re-subscribe after a lapse, or a move to a different subscription group, both of
        # which mint a fresh originalTransactionId — an INSERT violates that constraint and
        # the whole apply fails with 503. The user has already been charged by Apple at that
        # point. Update their existing row instead.
        if not prior:
            try:
                by_user = (
                    self.supabase.table("subscriptions")
                    .select("*")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
                owned = (by_user.data or [None])[0]
            except Exception as e:
                logger.error(
                    "IAP: subscriptions owner lookup failed for user=%s: %s: %s",
                    user_id, type(e).__name__, e,
                )
                raise IAPError("could not read existing subscription") from e
            if owned:
                logger.info(
                    "IAP: new txn=%s for user=%s replaces existing subscription row %s",
                    original_txn_id, user_id, owned["id"],
                )
                prior = owned

        # REFUSE A STALE, OUT-OF-ORDER DELIVERY. Apple does not guarantee notification
        # ordering and retries aggressively for up to 5 attempts over ~8 hours, so a
        # redelivered EXPIRED can easily land AFTER the DID_RENEW that superseded it. The
        # write below is a blind overwrite of tier/status/current_period_end, so the LAST
        # delivery won regardless of which one described the newer state — silently demoting
        # a paying subscriber (and, symmetrically, able to re-entitle a refunded one).
        #
        # `current_period_end` is the ordering key: a renewal always carries a LATER
        # expiresDate than the period it replaces, so "incoming period ends before the one we
        # already stored" is exactly "this describes older news".
        stale = _stale_delivery_reason(prior, status=status, event_at=event_at)
        if stale:
            logger.warning(
                "IAP: IGNORING stale delivery for txn=%s user=%s (%s) — stored state is "
                "newer; tier/status/current_period_end left unchanged",
                original_txn_id, user_id, stale,
            )
            winning_tier = self.reconcile_user_tier(user_id)
            return {
                "tier": prior.get("tier") or tier,
                "status": prior.get("status") or status,
                "winning_tier": winning_tier,
                "original_transaction_id": original_txn_id,
                "current_period_end": prior.get("current_period_end"),
                "was_replay": txn_replay,
                "was_stale": True,
            }

        def _write(payload_row: Dict[str, Any]) -> None:
            if prior:
                self.supabase.table("subscriptions").update(payload_row).eq(
                    "id", prior["id"]
                ).execute()
            else:
                self.supabase.table("subscriptions").insert(payload_row).execute()

        try:
            _write(row)
        except Exception as e:
            # Migration 114 adds `last_event_at`, and migrations here are applied BY HAND —
            # so a deploy can legitimately run this code against a database that does not yet
            # have the column, and PostgREST answers an unknown column with an error. Failing
            # the whole apply there would reject a purchase Apple has ALREADY charged for, to
            # protect an ordering guard that is merely an optimisation. Retry once without it.
            if _EVENT_AT_COLUMN in row:
                logger.warning(
                    "IAP: subscriptions write with %s failed (%s: %s) — retrying without it. "
                    "Apply migration 114 to enable out-of-order notification protection.",
                    _EVENT_AT_COLUMN, type(e).__name__, e,
                )
                degraded_row = {k: v for k, v in row.items() if k != _EVENT_AT_COLUMN}
                try:
                    _write(degraded_row)
                except Exception as e2:
                    logger.error(
                        "IAP: subscriptions write failed for txn=%s user=%s: %s: %s",
                        original_txn_id, user_id, type(e2).__name__, e2,
                    )
                    raise IAPError("could not record the subscription") from e2
            else:
                logger.error(
                    "IAP: subscriptions write failed for txn=%s user=%s: %s: %s",
                    original_txn_id, user_id, type(e).__name__, e,
                )
                raise IAPError("could not record the subscription") from e

        winning_tier = self.reconcile_user_tier(user_id)

        # A REFUND must take the unspent credits back. `reconcile_user_tier` drops the tier,
        # but the allocation is granted by `grant_tier_upgrade`, which never lowers `total` —
        # correct for a voluntary downgrade (you keep what you paid for until the next reset),
        # wrong for a revocation, where the money went back. A refunded Max subscriber
        # otherwise keeps 4000 credits, ~200 AI reports, having paid nothing.
        #
        # Gated on `winning_tier == "free"` so a user who holds a SECOND, still-active
        # subscription is not stripped because an old one was refunded.
        if status == "revoked" and winning_tier == "free":
            self._revoke_tier_credits(user_id, original_txn_id)
        return {
            "tier": tier,
            "status": status,
            "winning_tier": winning_tier,
            "original_transaction_id": original_txn_id,
            "current_period_end": row["current_period_end"],
            "was_replay": txn_replay,
            "was_stale": False,
        }

    # ── Tier reconciliation ───────────────────────────────────────────────────

    def winning_tier(self, user_id: str) -> str:
        """Best currently-active tier across all of the user's subscription rows.

        Expired and revoked rows are ignored, and so is any row whose period has already
        ended — a stale row must never hold a user on a paid tier, nor demote them.
        """
        try:
            result = (
                self.supabase.table("subscriptions")
                .select("tier, status, current_period_end")
                .eq("user_id", user_id)
                .execute()
            )
            rows = result.data or []
        except Exception as e:
            # Fail SAFE for the user: on a read error, leave the tier alone rather than
            # guessing "free" and stripping a paying customer's access.
            logger.error(
                "IAP: could not read subscriptions for user=%s (%s: %s) — tier unchanged",
                user_id, type(e).__name__, e,
            )
            raise IAPError("could not read subscriptions") from e

        now = datetime.now(timezone.utc)
        best = "free"
        for row in rows:
            if (row.get("status") or "").lower() not in _ENTITLING_STATUSES:
                continue
            row_status = (row.get("status") or "").lower()
            # `grace_period` and `billing_retry` mean precisely "the paid period HAS ended and
            # the customer is still entitled while Apple retries billing". Applying the
            # period-end filter to them dropped the row a second time and demoted a customer
            # Apple considers active — the exact demotion this method exists to prevent.
            end = row.get("current_period_end")
            if end and row_status not in {"grace_period", "billing_retry"}:
                try:
                    end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                    if end_dt <= now:
                        continue
                except ValueError:
                    # Unparseable date: treat as NOT entitling. Better to under-grant and
                    # have the user contact support than to grant on malformed data.
                    logger.warning(
                        "IAP: unparseable current_period_end %r for user=%s — ignoring row",
                        end, user_id,
                    )
                    continue
            tier = (row.get("tier") or "free").lower()
            if _TIER_RANK.get(tier, 0) > _TIER_RANK.get(best, 0):
                best = tier
        return best

    def _revoke_tier_credits(self, user_id: str, original_txn_id: str = "") -> None:
        """Claw back the UNSPENT portion of a revoked tier's allocation. Best-effort.

        Separate from `grant_tier_upgrade` on purpose. That function never lowers `total`
        (migration 112 §2) because a voluntary downgrade must not delete credits the user
        already paid for — but a refund is not a downgrade, and routing revocation through the
        same no-op left a refunded Max subscriber holding 4000 credits for the rest of the
        month. `revoke_tier_credits` floors at `GREATEST(free_allocation, used)` so `remaining`
        (a GENERATED column, `total - used`) can never go negative.

        Never raises: the entitlement itself is already recorded and the tier has already
        dropped, so a failure here costs credits, not correctness — and raising would make the
        webhook return non-2xx, which makes Apple redeliver a notification we DID apply.
        """
        try:
            self.supabase.rpc("revoke_tier_credits", {"p_user_id": user_id}).execute()
            logger.info(
                "IAP: revoked unspent credits for user=%s after txn=%s was refunded",
                user_id, original_txn_id,
            )
        except Exception as e:
            logger.error(
                "IAP: entitlement REVOKED for user=%s (txn=%s) but revoke_tier_credits FAILED "
                "(%s: %s) — the user keeps their unspent credits until the next monthly reset. "
                "If this is PGRST202/undefined function, migration 114 has not been applied.",
                user_id, original_txn_id, type(e).__name__, e,
            )

    def reconcile_user_tier(self, user_id: str) -> str:
        """Mirror the winning tier onto `users.tier` and refresh the credit period.

        `users.tier` is what `ensure_credit_period` reads to decide the monthly allocation
        (migration 100), so the mirror has to happen before the allocation call or the user
        gets last tier's credits.
        """
        tier = self.winning_tier(user_id)

        try:
            self.supabase.table("users").update({"tier": tier}).eq("id", user_id).execute()
        except Exception as e:
            logger.error(
                "IAP: could not mirror tier=%s onto users for user=%s: %s: %s",
                tier, user_id, type(e).__name__, e,
            )
            raise IAPError("could not update the account tier") from e

        # Roll the period over if it is due, and create the balance row on a first touch.
        # Best-effort: the entitlement itself is already recorded, and the same RPC runs
        # lazily on the next credit read, so a failure here delays credits rather than
        # losing the purchase.
        try:
            self.supabase.rpc("ensure_credit_period", {"p_user_id": user_id}).execute()
        except Exception as e:
            logger.error(
                "IAP: tier=%s applied for user=%s but ensure_credit_period FAILED "
                "(%s: %s) — credits will land on the next read",
                tier, user_id, type(e).__name__, e,
            )

        # THEN grant the new tier's allocation. `ensure_credit_period` alone is NOT enough and
        # this call is the whole reason migration 112 exists: it only grants when the monthly
        # boundary has passed, so an upgrade bought on the 10th used to leave the buyer on
        # their old (often exhausted) balance until the 1st — paid, tier flipped, zero credits,
        # 402 on the next tap. `grant_tier_upgrade` raises `total` to the new allocation and is
        # idempotent by construction (it no-ops once total >= allocation), so replays from
        # `Transaction.updates` on every app launch cannot farm credits, and a downgrade never
        # claws back. See migrations/112_grant_tier_upgrade.sql for the full reasoning.
        try:
            self.supabase.rpc("grant_tier_upgrade", {"p_user_id": user_id}).execute()
        except Exception as e:
            # Degrades to exactly the old behaviour (credits at the next monthly reset), which
            # is also what happens if 112 has not been applied yet — so this is warned, not
            # raised, but it IS the difference between a working purchase and a silent one.
            logger.error(
                "IAP: tier=%s applied for user=%s but grant_tier_upgrade FAILED (%s: %s) — "
                "the upgrade's credits will NOT land until the next monthly reset. If this is "
                "PGRST202/undefined function, migration 112 has not been applied.",
                tier, user_id, type(e).__name__, e,
            )

        return tier

    def sweep_expired_subscriptions(self, limit: int = 500) -> Dict[str, int]:
        """Expire subscription rows whose paid period ended, and re-reconcile those users.

        WHY THIS EXISTS: `reconcile_user_tier` is the only writer of `users.tier`, and it runs
        only when a client `POST /billing/verify` or an App Store Server Notification arrives.
        Nothing re-evaluated entitlement on its own. So a single lost EXPIRED or REFUND
        notification — Apple stops retrying, the webhook URL is an unticked checklist item, a
        deploy eats the delivery window — left the account on the paid tier permanently. Worse
        than "keeps access": `ensure_credit_period` reads `users.tier` at every monthly
        boundary (migration 100:185), so a cancelled Max subscriber keeps getting 4000 fresh
        credits on the 1st of every month, forever, at real Gemini and FMP cost. The client
        only ever reports *purchases*, so nothing else can notice.

        This makes entitlement self-correcting with zero notifications delivered.

        Grace windows are deliberately generous, because the cost of expiring a PAYING
        customer early is far worse than carrying a lapsed one for another day:

        * `active` — 24h past `current_period_end`. Apple renews slightly before the boundary,
          and clock skew plus notification latency both push the other way.
        * `grace_period` / `billing_retry` — 60 days, which is Apple's maximum billing-retry
          window. These statuses mean "the period HAS ended and the customer is still entitled
          while Apple retries", so the period-end filter must not apply to them at the short
          window; that is exactly the demotion `winning_tier` documents avoiding.

        Idempotent: a row already `expired` is not selected, so a re-run is a no-op.
        """
        now = datetime.now(timezone.utc)
        short_cutoff = (now - timedelta(hours=24)).isoformat()
        long_cutoff = (now - timedelta(days=60)).isoformat()

        try:
            result = (
                self.supabase.table("subscriptions")
                .select("id, user_id, status, current_period_end, tier")
                .in_("status", sorted(_ENTITLING_STATUSES))
                .not_.is_("current_period_end", "null")
                .lt("current_period_end", short_cutoff)
                .limit(limit)
                .execute()
            )
            rows = result.data or []
        except Exception as e:
            logger.error(
                "IAP sweep: could not read subscriptions (%s: %s) — no rows expired this pass",
                type(e).__name__, e,
            )
            return {"scanned": 0, "expired": 0, "users_reconciled": 0, "errors": 1}

        stale: list[dict] = []
        for row in rows:
            status = (row.get("status") or "").lower()
            cutoff = long_cutoff if status in {"grace_period", "billing_retry"} else short_cutoff
            end = row.get("current_period_end")
            if end and str(end) < cutoff:
                stale.append(row)

        expired = 0
        errors = 0
        affected: set[str] = set()
        for row in stale:
            try:
                (
                    self.supabase.table("subscriptions")
                    .update({"status": "expired", "updated_at": now.isoformat()})
                    .eq("id", row["id"])
                    .execute()
                )
                expired += 1
                if row.get("user_id"):
                    affected.add(str(row["user_id"]))
            except Exception as e:
                errors += 1
                logger.error(
                    "IAP sweep: could not expire subscription id=%s user=%s (%s: %s)",
                    row.get("id"), row.get("user_id"), type(e).__name__, e,
                )

        reconciled = 0
        for user_id in affected:
            try:
                # Recomputes from the user's REMAINING rows, so someone holding a second,
                # still-valid subscription keeps their tier rather than being dropped to free.
                new_tier = self.reconcile_user_tier(user_id)
                reconciled += 1
                logger.info(
                    "IAP sweep: user=%s reconciled to tier=%s after expiry", user_id, new_tier
                )
            except Exception as e:
                errors += 1
                logger.error(
                    "IAP sweep: expired rows for user=%s but reconcile FAILED (%s: %s) — "
                    "users.tier still shows the lapsed tier and will keep allocating its "
                    "credits until the next sweep",
                    user_id, type(e).__name__, e,
                )

        if expired or errors:
            logger.info(
                "IAP sweep: scanned=%d expired=%d users_reconciled=%d errors=%d",
                len(rows), expired, reconciled, errors,
            )
        return {
            "scanned": len(rows),
            "expired": expired,
            "users_reconciled": reconciled,
            "errors": errors,
        }

    # ── Webhook handling ──────────────────────────────────────────────────────

    def user_id_for_transaction(self, original_transaction_id: str) -> Optional[str]:
        """Find which user a transaction belongs to.

        Server notifications identify the transaction, not our user. The mapping only exists
        because the client's verified purchase created the row first — so a notification for
        an unknown transaction is expected (e.g. it arrived before the client call) and is
        reported rather than guessed at.
        """
        try:
            result = (
                self.supabase.table("subscriptions")
                .select("user_id")
                .eq("original_transaction_id", original_transaction_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            return rows[0]["user_id"] if rows else None
        except Exception as e:
            logger.error(
                "IAP: user lookup failed for txn=%s: %s: %s",
                original_transaction_id, type(e).__name__, e,
            )
            return None

    def apply_notification(
        self, notification: Dict[str, Any], transaction: Optional[Dict[str, Any]]
    ) -> Tuple[str, Optional[str]]:
        """Apply a verified App Store Server Notification.

        Returns `(outcome, user_id)` where outcome describes what was done, for the webhook's
        response and logs. Never raises for an unknown transaction: Apple retries on
        non-2xx, and retrying forever on a notification we can't map helps nobody.
        """
        notification_type = str(notification.get("notificationType") or "")
        subtype = str(notification.get("subtype") or "")

        if not transaction:
            logger.info(
                "IAP webhook %s/%s carried no transaction — nothing to apply",
                notification_type, subtype,
            )
            return "ignored_no_transaction", None

        original_txn_id = str(
            transaction.get("originalTransactionId") or transaction.get("transactionId") or ""
        )
        if not original_txn_id:
            return "ignored_no_transaction_id", None

        user_id = self.user_id_for_transaction(original_txn_id)
        if not user_id:
            # Expected race: the notification can beat the client's verify call.
            logger.info(
                "IAP webhook %s/%s for unknown txn=%s — no user mapping yet",
                notification_type, subtype, original_txn_id,
            )
            return "ignored_unknown_transaction", None

        status_override = _status_for_notification(notification_type, subtype)

        # Apple's `signedDate` — when Apple ASSERTED this, which is the only correct ordering
        # key for out-of-order redeliveries (Apple retries up to 5 times over ~8 hours and
        # guarantees no ordering, so a redelivered EXPIRED can land after the DID_RENEW that
        # superseded it). NOT `expiresDate`: that says which period the transaction is about,
        # and an EXPIRED notification is signed late while carrying an early expiresDate.
        event_at = _ms_to_dt(notification.get("signedDate"))

        try:
            self.apply_transaction(
                user_id, transaction,
                status_override=status_override,
                event_at=event_at,
            )
        except UnknownProduct as e:
            logger.error("IAP webhook: %s", e)
            return "ignored_unknown_product", user_id
        except IAPError as e:
            logger.error(
                "IAP webhook %s/%s failed to apply for user=%s: %s",
                notification_type, subtype, user_id, e,
            )
            raise

        logger.info(
            "IAP webhook applied: type=%s subtype=%s user=%s txn=%s",
            notification_type, subtype, user_id, original_txn_id,
        )
        return f"applied:{notification_type}", user_id


_iap_service: Optional[IAPService] = None


def get_iap_service() -> IAPService:
    """Process-wide singleton (matches the get_supabase() lifecycle)."""
    global _iap_service
    if _iap_service is None:
        _iap_service = IAPService()
    return _iap_service
