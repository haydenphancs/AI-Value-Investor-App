"""Schema-parity: pin the account/billing/settings response shapes iOS decodes.

A drift here (renamed/removed field, wrong optionality) ships as a decode crash
in the iOS app. These tests assert the serialized JSON keys are EXACTLY what the
Swift Codable DTOs expect (snake_case), for both full and minimal/worst-case data.
"""

from app.schemas.subscription import (
    PlanFeatureResponse,
    PlanResponse,
    PlanCatalogResponse,
    SubscriptionResponse,
)
from app.schemas.settings import (
    UserSettingsResponse,
    DeviceRegisterResponse,
)


def test_plan_catalog_response_keys():
    resp = PlanCatalogResponse(
        plans=[
            PlanResponse(
                tier="free", display_name="Free", monthly_credits=50,
                price_cents=0, price_label="Free",
            ),
            PlanResponse(
                tier="premium", display_name="Max", monthly_credits=4000,
                price_cents=3999, price_label="$39.99",
            ),
        ],
        report_cost=20,
        chat_cost=1,
    )
    dumped = resp.model_dump()
    assert set(dumped.keys()) == {"plans", "report_cost", "chat_cost"}
    assert set(dumped["plans"][0].keys()) == {
        "tier", "display_name", "monthly_credits", "price_cents", "price_label",
        "features",
    }
    assert dumped["report_cost"] == 20 and dumped["chat_cost"] == 1


def test_plan_feature_response_keys():
    """The paywall row shape. `PlanFeatureDTO` on iOS decodes each of these; a rename
    here is a blank upgrade screen, which is worse than a crash because it looks fine."""
    resp = PlanCatalogResponse(
        plans=[
            PlanResponse(
                tier="pro", display_name="Pro", monthly_credits=1200,
                price_cents=1499, price_label="$14.99",
                features=[PlanFeatureResponse(
                    key="signals", title="Signal tickers", detail="See the ticker.",
                    icon="antenna.radiowaves.left.and.right", accent="signals",
                    included=True, group="plan",
                )],
            ),
        ],
        report_cost=20,
        chat_cost=1,
    )
    feature = resp.model_dump()["plans"][0]["features"][0]
    assert set(feature.keys()) == {
        "key", "title", "detail", "icon", "accent", "included", "group",
    }
    assert feature["included"] is True and feature["group"] == "plan"


def test_plan_response_accepts_a_pre_features_row():
    """`features` was ADDED, never required. A row built by a caller that predates
    plan_features — or read from a `plan_credits` SELECT that does not know about it —
    must still validate, and must serialize an empty list rather than null: the iOS
    fallback triggers on empty, and a null would have to be tolerated separately."""
    plan = PlanResponse.model_validate({
        "tier": "free", "display_name": "Free", "monthly_credits": 50,
        "price_cents": 0, "price_label": "Free",
    })
    assert plan.features == []
    assert plan.model_dump()["features"] == []


def test_subscription_response_full_and_minimal():
    # Full row (paid, active).
    full = SubscriptionResponse.model_validate({
        "tier": "pro", "display_name": "Pro", "status": "active",
        "current_period_end": "2026-08-01T00:00:00+00:00", "store": "apple",
    })
    assert full.tier == "pro" and full.store == "apple"

    # Minimal (Free fallback — no period end, no store). Optionals must default None.
    minimal = SubscriptionResponse.model_validate({
        "tier": "free", "display_name": "Free", "status": "active",
    })
    dumped = minimal.model_dump()
    assert set(dumped.keys()) == {
        "tier", "display_name", "status", "current_period_end", "store",
    }
    assert dumped["current_period_end"] is None and dumped["store"] is None


def test_user_settings_response_defaults_empty():
    # Worst case: a user with no synced settings → empty preferences dict, not null.
    resp = UserSettingsResponse()
    assert resp.model_dump() == {"preferences": {}}
    # Round-trips an arbitrary blob unchanged.
    blob = {"appearance_mode": "system", "notify_earnings_alerts": True}
    assert UserSettingsResponse(preferences=blob).model_dump()["preferences"] == blob


def test_device_register_response_key():
    assert DeviceRegisterResponse(registered=True).model_dump() == {"registered": True}
