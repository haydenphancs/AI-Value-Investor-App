"""Math/transform tests for the subscription/plan-catalog service.

Pure functions only — no Supabase, no network. Covers catalog ordering,
price-label formatting, tier display names, and outlier inputs (empty,
out-of-order, missing/None price, unknown tier).
"""

from app.services.subscription_service import (
    normalize_catalog,
    price_label,
    display_name_for_tier,
)


def test_price_label_free_and_paid():
    assert price_label(0) == "Free"
    assert price_label(1499) == "$14.99"
    assert price_label(3999) == "$39.99"


def test_price_label_negative_is_free():
    # A bad/negative price must not render "$-0.01" — clamp to Free.
    assert price_label(-100) == "Free"


def test_display_name_for_tier():
    assert display_name_for_tier("free") == "Free"
    assert display_name_for_tier("pro") == "Pro"
    # 'premium' enum is surfaced as "Max".
    assert display_name_for_tier("premium") == "Max"
    # Unknown tier falls back to a capitalized label rather than crashing.
    assert display_name_for_tier("enterprise") == "Enterprise"


def test_normalize_catalog_orders_cheapest_first():
    rows = [
        {"tier": "premium", "monthly_credits": 4000, "price_cents": 3999, "display_name": "Max"},
        {"tier": "free", "monthly_credits": 50, "price_cents": 0, "display_name": "Free"},
        {"tier": "pro", "monthly_credits": 1200, "price_cents": 1499, "display_name": "Pro"},
    ]
    out = normalize_catalog(rows)
    assert [r["tier"] for r in out] == ["free", "pro", "premium"]


def test_normalize_catalog_stamps_price_label():
    rows = [
        {"tier": "free", "monthly_credits": 50, "price_cents": 0, "display_name": "Free"},
        {"tier": "pro", "monthly_credits": 1200, "price_cents": 1499, "display_name": "Pro"},
    ]
    out = normalize_catalog(rows)
    assert out[0]["price_label"] == "Free"
    assert out[1]["price_label"] == "$14.99"


def test_normalize_catalog_empty_is_empty():
    assert normalize_catalog([]) == []


def test_normalize_catalog_missing_price_cents_treated_as_zero():
    # A malformed row with no price_cents must not raise; treat as 0 / Free / sort first.
    rows = [
        {"tier": "pro", "monthly_credits": 1200, "price_cents": 1499, "display_name": "Pro"},
        {"tier": "mystery", "monthly_credits": 10, "display_name": "Mystery"},  # no price_cents
    ]
    out = normalize_catalog(rows)
    assert out[0]["tier"] == "mystery"
    assert out[0]["price_label"] == "Free"


def test_normalize_catalog_does_not_mutate_input():
    rows = [{"tier": "free", "price_cents": 0, "monthly_credits": 50, "display_name": "Free"}]
    _ = normalize_catalog(rows)
    assert "price_label" not in rows[0]  # input untouched; copies returned
