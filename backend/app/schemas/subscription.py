"""Subscription / plan-catalog schemas (Free / Pro / Max tiers).

Backs the paywall: `PlanCatalogResponse` is the public tier catalog (from the
`plan_credits` config table), `SubscriptionResponse` is the current user's
entitlement (from the `subscriptions` table). Read-only on the client — tier
changes are written server-side by receipt validation, never self-assigned.
"""

from pydantic import BaseModel, Field
from typing import List, Optional


class PlanResponse(BaseModel):
    tier: str                 # user_tier enum value: free | pro | premium
    display_name: str         # storefront label ("Free" | "Pro" | "Max")
    monthly_credits: int
    price_cents: int          # USD cents, storefront display only
    price_label: str          # precomputed ("Free" | "$14.99")


class PlanCatalogResponse(BaseModel):
    plans: List[PlanResponse]
    report_cost: int          # credits per report (settings.REPORT_CREDIT_COST)
    chat_cost: int            # credits per chat turn (settings.CHAT_CREDIT_COST)


class SubscriptionResponse(BaseModel):
    tier: str
    display_name: str
    status: str               # active | grace | expired | canceled
    current_period_end: Optional[str] = None  # ISO-8601
    store: Optional[str] = None               # apple | stripe | promo


class VerifyPurchaseRequest(BaseModel):
    """Client submits the JWS Apple handed it for a completed StoreKit 2 transaction.

    Only the signed blob is accepted — deliberately NOT a product id or tier. Anything the
    client asserts about what it bought is ignored; the tier is read from the payload after
    Apple's signature and certificate chain verify.
    """

    signed_transaction: str = Field(min_length=32)


class VerifyPurchaseResponse(BaseModel):
    """Entitlement state after applying a verified transaction."""

    # The WINNING tier across all the user's subscriptions, not just this transaction's —
    # so a Pro receipt replayed by a Max subscriber doesn't appear to demote them.
    tier: str
    status: str
    current_period_end: Optional[str] = None
    # True when this transaction had already been applied (StoreKit replays on launch,
    # restore re-submits). Useful for client logs; not an error.
    was_replay: bool = False
