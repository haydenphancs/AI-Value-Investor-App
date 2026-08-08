"""Subscription service — reads the tier catalog, the credit-pack catalog, and a
user's current entitlement.

READ-ONLY. The tier catalog lives in `plan_credits` (migration 100), the consumable
credit-pack catalog in `credit_packs` (migration 117); a user's entitlement lives in
`subscriptions` (written server-side by receipt validation, never by the client). A
3-row config read is not "expensive", so there is no cache layer here (per CLAUDE.md
invariant #4).
"""

import logging
from typing import List, Optional

from app.database import get_supabase

logger = logging.getLogger(__name__)

# Fallback catalog if plan_credits is unreadable (mirrors the migration-100 seed).
# Keeps the paywall renderable even if the config table is briefly unavailable.
_FALLBACK_PLANS: List[dict] = [
    {"tier": "free", "monthly_credits": 50, "price_cents": 0, "display_name": "Free"},
    {"tier": "pro", "monthly_credits": 1200, "price_cents": 1499, "display_name": "Pro"},
    {"tier": "premium", "monthly_credits": 4000, "price_cents": 3999, "display_name": "Max"},
]

# Fallback consumable catalog if credit_packs is unreadable (mirrors the migration-117
# seed). Keeps the Buy Credits screen renderable if the config table is briefly
# unavailable. NOTE this is a DISPLAY fallback only — the grant amount is always read
# from the DB row inside the purchase path, never from here, so a stale constant can
# never cause the wrong number of credits to be granted.
_FALLBACK_PACKS: List[dict] = [
    {"product_id": "com.phan.caydex.credits.starter", "credits": 90,
     "price_cents": 199, "display_name": "Starter", "sort_order": 1},
    {"product_id": "com.phan.caydex.credits.plus", "credits": 250,
     "price_cents": 499, "display_name": "Plus", "sort_order": 2},
    {"product_id": "com.phan.caydex.credits.power", "credits": 550,
     "price_cents": 999, "display_name": "Power", "sort_order": 3},
    {"product_id": "com.phan.caydex.credits.mega", "credits": 1200,
     "price_cents": 1999, "display_name": "Mega", "sort_order": 4},
]

# Tie-break ordering when two tiers share a price (shouldn't happen, but keeps
# the catalog deterministic).
_TIER_ORDER = {"free": 0, "pro": 1, "premium": 2}

# tier enum value -> storefront display name. 'premium' is shown as "Max".
TIER_DISPLAY_NAMES = {"free": "Free", "pro": "Pro", "premium": "Max"}


def display_name_for_tier(tier: str) -> str:
    return TIER_DISPLAY_NAMES.get(tier, tier.capitalize())


def price_label(price_cents: int) -> str:
    """'Free' for $0, else '$14.99'-style label."""
    if price_cents <= 0:
        return "Free"
    return f"${price_cents / 100:.2f}"


def normalize_catalog(rows: List[dict]) -> List[dict]:
    """Pure: sort a plan_credits result cheapest → most expensive and stamp each
    row with a `price_label`. Tolerant of missing/None `price_cents` (treated as 0)
    and unknown tiers (sorted last). Mutates copies, returns a fresh list."""
    out = [dict(r) for r in rows]
    out.sort(
        key=lambda r: (
            int(r.get("price_cents") or 0),
            _TIER_ORDER.get(r.get("tier", ""), 99),
        )
    )
    for r in out:
        r["price_label"] = price_label(int(r.get("price_cents") or 0))
    return out


def normalize_pack_catalog(rows: List[dict]) -> List[dict]:
    """Pure: sort a credit_packs result cheapest → dearest and stamp each row with a
    `price_label`. Tolerant of missing/None `price_cents` / `sort_order` (treated as 0).
    Mutates copies, returns a fresh list.

    Sorted on price first and `sort_order` only as a tie-break, so a bad `sort_order`
    cannot present a more expensive pack as the cheaper one."""
    out = [dict(r) for r in rows]
    out.sort(
        key=lambda r: (
            int(r.get("price_cents") or 0),
            int(r.get("sort_order") or 0),
        )
    )
    for r in out:
        r["price_label"] = price_label(int(r.get("price_cents") or 0))
        r["sort_order"] = int(r.get("sort_order") or 0)
    return out


class SubscriptionService:
    def __init__(self):
        self.supabase = get_supabase()

    def get_plan_catalog(self) -> List[dict]:
        """Tier catalog ordered cheapest → most expensive, each row carrying a
        precomputed `price_label`. Falls back to the seeded values on any error."""
        try:
            result = (
                self.supabase.table("plan_credits")
                .select("tier, monthly_credits, price_cents, display_name")
                .execute()
            )
            rows = list(result.data or [])
            if not rows:
                logger.warning("plan_credits empty — serving fallback catalog")
                rows = [dict(r) for r in _FALLBACK_PLANS]
        except Exception as e:
            logger.error(
                "plan_credits read failed (%s: %s) — serving fallback catalog",
                type(e).__name__, e,
            )
            rows = [dict(r) for r in _FALLBACK_PLANS]

        return normalize_catalog(rows)

    def get_credit_pack_catalog(self) -> List[dict]:
        """Active consumable credit packs, cheapest → dearest, each carrying a
        precomputed `price_label`. Falls back to the seeded values on any error.

        Mirrors `get_plan_catalog`. Inactive packs are filtered out so retiring one is a
        DB flag rather than a deploy — but a retired pack must ALSO be removed from App
        Store Connect, or a client that still has it cached can buy something this
        catalog no longer lists (the purchase path resolves credits independently, so it
        would still be honoured — see `credit_pack_for_product`)."""
        try:
            result = (
                self.supabase.table("credit_packs")
                .select("product_id, credits, price_cents, display_name, sort_order")
                .eq("is_active", True)
                .execute()
            )
            rows = list(result.data or [])
            if not rows:
                logger.warning("credit_packs empty — serving fallback catalog")
                rows = [dict(r) for r in _FALLBACK_PACKS]
        except Exception as e:
            logger.error(
                "credit_packs read failed (%s: %s) — serving fallback catalog",
                type(e).__name__, e,
            )
            rows = [dict(r) for r in _FALLBACK_PACKS]

        return normalize_pack_catalog(rows)

    def get_user_subscription(self, user_id: str) -> Optional[dict]:
        """The user's subscription row, or None (caller falls back to Free).
        Uses limit(1) rather than single() so 'no row' is a clean None, not an error."""
        try:
            result = (
                self.supabase.table("subscriptions")
                .select("tier, status, store, current_period_end")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            rows = list(result.data or [])
            return rows[0] if rows else None
        except Exception as e:
            logger.error(
                "subscriptions read failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            return None
