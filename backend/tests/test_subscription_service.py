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
    assert "features" not in rows[0]


# ── Paywall features are stamped here, so every path that serves a catalog carries them ──


def test_normalize_catalog_stamps_paywall_features():
    rows = [{"tier": "pro", "price_cents": 1499, "monthly_credits": 1200, "display_name": "Pro"}]
    out = normalize_catalog(rows, report_cost=20, chat_cost=1)
    features = out[0]["features"]
    assert features
    by_key = {f["key"]: f for f in features}
    assert "1,200 credits a month" == by_key["credits"]["title"]
    assert "15 watchlist tickers in Updates" == by_key["updates_tickers"]["title"]


def test_normalize_catalog_costs_default_to_settings():
    """The seven pre-existing call sites pass no costs. They must still produce a usable
    catalog rather than a feature list with the credit maths silently missing."""
    rows = [{"tier": "free", "price_cents": 0, "monthly_credits": 50, "display_name": "Free"}]
    out = normalize_catalog(rows)
    credits = next(f for f in out[0]["features"] if f["key"] == "credits")
    assert "AI research reports" in credits["detail"]
    assert "Cay AI replies" in credits["detail"]


def test_an_unknown_tier_gets_no_features_rather_than_free_s():
    """A tier this build does not recognise must not be described with Free's limits —
    that would state a limit the server is not enforcing for that plan. The empty list
    is the client's signal to fall back to its own bundled table."""
    rows = [{"tier": "mystery", "monthly_credits": 10, "display_name": "Mystery"}]
    out = normalize_catalog(rows)
    assert out[0]["features"] == []


def test_a_null_monthly_credits_is_coerced_instead_of_raising():
    """This column is edited by hand in Supabase Studio. It used to only fail
    `PlanResponse` validation; now it also feeds a division inside the copy builder."""
    rows = [{"tier": "free", "price_cents": 0, "monthly_credits": None, "display_name": "Free"}]
    out = normalize_catalog(rows)
    assert out[0]["monthly_credits"] == 0
    credits = next(f for f in out[0]["features"] if f["key"] == "credits")
    assert credits["title"] == "0 credits a month"


def test_the_fallback_catalog_carries_features_too(monkeypatch):
    """The whole reason features are stamped in `normalize_catalog` and not in the
    endpoint: `_FALLBACK_PLANS` holds no feature data, yet a Supabase outage must still
    render a complete paywall rather than three bare price tags."""
    import app.services.subscription_service as svc

    class _Boom:
        def table(self, *_a, **_k):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(svc, "get_supabase", lambda: _Boom())
    catalog = svc.SubscriptionService().get_plan_catalog()

    assert [p["tier"] for p in catalog] == ["free", "pro", "premium"]
    for plan in catalog:
        assert plan["features"], f"{plan['tier']} served no features on the fallback path"
    assert all("features" not in row for row in svc._FALLBACK_PLANS)
