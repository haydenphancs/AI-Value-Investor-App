"""Schema-parity: pin the account/billing/settings response shapes iOS decodes.

A drift here (renamed/removed field, wrong optionality) ships as a decode crash
in the iOS app. These tests assert the serialized JSON keys are EXACTLY what the
Swift Codable DTOs expect (snake_case), for both full and minimal/worst-case data.
"""

from app.schemas.subscription import (
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
    }
    assert dumped["report_cost"] == 20 and dumped["chat_cost"] == 1


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
