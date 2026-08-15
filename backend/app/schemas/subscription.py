"""Subscription / plan-catalog schemas (Free / Pro / Max tiers).

Backs the paywall: `PlanCatalogResponse` is the public tier catalog (from the
`plan_credits` config table), `SubscriptionResponse` is the current user's
entitlement (from the `subscriptions` table). Read-only on the client — tier
changes are written server-side by receipt validation, never self-assigned.
"""

from pydantic import BaseModel, Field
from typing import List, Optional


class PlanFeatureResponse(BaseModel):
    """One row on the paywall, built by `services/plan_features.py` from `entitlements.py`.

    EVERY FIELD IS DEFAULTED, for the same reason spelled out on `VerifyPurchaseResponse`
    below: a shipped iOS build decodes this shape, and the app and the backend deploy
    independently. Adding, never changing, is the rule.

    `icon` is an SF Symbol name and `accent` a SEMANTIC key ("credits", "whales", …) —
    deliberately NOT a hex colour. iOS maps the accent to an audited text-role `AppColors`
    token, so the launch contrast audit still covers every glyph; a colour on the wire
    would be an unaudited value on a surface that audit cannot see.

    `included=False` renders the row as a locked/paid capability rather than a quantity.
    `group` is "plan" (a reason to pay — its value changes between tiers) or "always"
    (free on every plan; rendered identically on all three cards).
    """

    key: str = ""             # stable machine id — the client's highlight + fallback join
    title: str = ""           # one line; carries the number
    detail: str = ""          # one or two sentences
    icon: str = ""            # SF Symbol name; the client validates before rendering
    accent: str = "neutral"   # semantic key, never a hex — see above
    included: bool = True
    group: str = "plan"       # "plan" | "always"


class PlanResponse(BaseModel):
    tier: str                 # user_tier enum value: free | pro | premium
    display_name: str         # storefront label ("Free" | "Pro" | "Max")
    monthly_credits: int
    price_cents: int          # USD cents, storefront display only
    price_label: str          # precomputed ("Free" | "$14.99")
    # Defaulted so a pre-features row (or a caller that predates plan_features) still
    # validates; an empty list is the client's signal to use its bundled fallback table.
    features: List[PlanFeatureResponse] = Field(default_factory=list)


class PlanCatalogResponse(BaseModel):
    plans: List[PlanResponse]
    report_cost: int          # credits per report (settings.REPORT_CREDIT_COST)
    chat_cost: int            # credits per chat turn (settings.CHAT_CREDIT_COST)


class CreditPackResponse(BaseModel):
    """One consumable credit pack, from the `credit_packs` config table.

    `credits` is authoritative — it is the same value `add_purchased_credits` grants, so what
    the screen promises can never disagree with what lands in the balance. `price_cents` /
    `price_label` are a FALLBACK only: the price actually charged is Apple's localized
    `displayPrice`, which the client reads from StoreKit.
    """

    product_id: str           # must match App Store Connect exactly
    display_name: str         # storefront label ("Starter" | "Plus" | ...)
    credits: int
    price_cents: int          # USD cents, display fallback only
    price_label: str          # precomputed ("$4.99")
    sort_order: int = 0


class CreditPackCatalogResponse(BaseModel):
    packs: List[CreditPackResponse]
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

    # ── Consumable credit packs ────────────────────────────────────────────────────────────
    # EVERY field below is DEFAULTED, because a shipped iOS build decodes this response and
    # `VerifyPurchaseResponse` in SubscriptionModels.swift synthesizes its init — an
    # undefaulted addition would make older clients fail to decode a purchase they just paid
    # for. Adding, never changing, is the rule for this shape.
    #
    # `tier` above stays the user's real WINNING tier even on the pack path: the client
    # surfaces it as the purchase result, so returning "free" for a pack bought by a Pro
    # subscriber would render as a demotion.
    kind: str = "subscription"          # "subscription" | "credit_pack"
    # Credits actually added by THIS delivery. 0 on a replay — the client must not claim
    # "1,200 credits added" for something the user can check against their balance.
    credits_granted: int = 0
    # The user's full spendable balance after applying this transaction (granted + purchased),
    # so the success screen can show a number without racing the balance refresh.
    credits_spendable: Optional[int] = None
