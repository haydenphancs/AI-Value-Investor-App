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
from datetime import datetime, timezone
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


class IAPService:
    def __init__(self):
        self.supabase = get_supabase()

    # ── Entitlement application ───────────────────────────────────────────────

    def apply_transaction(
        self, user_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Record a verified transaction and reconcile the user's entitlement.

        `payload` MUST already be Apple-verified (integrations/app_store.py). This method
        does not re-check signatures — it trusts its caller to have done so, which is why
        nothing but the verified path may call it.

        Returns a summary: the resolved tier, the winning tier actually applied, and whether
        this delivery was new or a replay.
        """
        tier = tier_for_product(payload.get("productId"))
        status = status_for_transaction(payload)
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

        # Idempotent on original_transaction_id: a replayed delivery updates the existing
        # row rather than inserting a duplicate entitlement.
        try:
            existing = (
                self.supabase.table("subscriptions")
                .select("id, status, current_period_end")
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

        try:
            if prior:
                self.supabase.table("subscriptions").update(row).eq(
                    "id", prior["id"]
                ).execute()
            else:
                self.supabase.table("subscriptions").insert(row).execute()
        except Exception as e:
            logger.error(
                "IAP: subscriptions write failed for txn=%s user=%s: %s: %s",
                original_txn_id, user_id, type(e).__name__, e,
            )
            raise IAPError("could not record the subscription") from e

        winning_tier = self.reconcile_user_tier(user_id)

        logger.info(
            "IAP applied: user=%s txn=%s product=%s tier=%s status=%s winning=%s replay=%s",
            user_id, original_txn_id, payload.get("productId"), tier, status,
            winning_tier, bool(prior),
        )
        return {
            "tier": tier,
            "status": status,
            "winning_tier": winning_tier,
            "original_transaction_id": original_txn_id,
            "current_period_end": row["current_period_end"],
            "was_replay": bool(prior),
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
            end = row.get("current_period_end")
            if end:
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

        # Grant the tier's allocation. Best-effort: the entitlement itself is already
        # recorded, and the same RPC runs lazily on the next credit read, so a failure here
        # delays credits rather than losing the purchase.
        try:
            self.supabase.rpc("ensure_credit_period", {"p_user_id": user_id}).execute()
        except Exception as e:
            logger.error(
                "IAP: tier=%s applied for user=%s but ensure_credit_period FAILED "
                "(%s: %s) — credits will land on the next read",
                tier, user_id, type(e).__name__, e,
            )

        return tier

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

        try:
            self.apply_transaction(user_id, transaction)
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
