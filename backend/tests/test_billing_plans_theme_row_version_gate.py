"""Build 1.0's paywall must not advertise the 5-company theme limit it does not apply.

`GET /home/themes/{slug}` keeps sending build 1.0 the FULL company list (1.0 cannot draw the
blurred rows or the upgrade prompt — `entitlements.THEME_LOCK_MIN_APP_VERSION`). So the plan
catalogue's `theme_companies` row ("Top 5 companies in each theme" on Free, "Every company in
every theme" on Pro/Max) would be false copy on 1.0's paywall (adversarial review 2026-10-06).
`GET /billing/plans` drops that row for a caller older than 1.1 — on a per-request COPY, since
the service memoises one catalogue for every caller.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.services import plan_features
from app.services.subscription_service import SubscriptionService

_ROW = plan_features.KEY_THEME_COMPANIES


_CREDITS = {"free": 50, "pro": 1200, "premium": 4000}


def _features(tier, credits):
    return plan_features.features_for_tier(tier, monthly_credits=credits, report_cost=20, chat_cost=1)


def _catalog():
    """The real row shape (`plan_features.features_for_tier`), so the test also proves the
    route's filter reads the keys the service actually emits."""
    plans = []
    for tier, name, credits, cents, label in (("free", "Free", 50, 0, "Free"),
                                              ("pro", "Pro", 1200, 1499, "$14.99"),
                                              ("premium", "Max", 4000, 3999, "$39.99")):
        features = _features(tier, credits)
        assert any(f.get("key") == _ROW for f in features), "the service no longer emits the theme row"
        plans.append({
            "tier": tier, "display_name": name, "monthly_credits": credits,
            "price_cents": cents, "price_label": label, "features": features,
        })
    return plans


@pytest.fixture
def memoised(monkeypatch):
    """Stands in for the service's memoised catalogue: the SAME list on every call."""
    shared = _catalog()
    monkeypatch.setattr(SubscriptionService, "get_plan_catalog", lambda self: shared)
    return shared


def _rows(resp):
    assert resp.status_code == 200, resp.text
    return {p["tier"]: [f["key"] for f in p["features"]] for p in resp.json()["plans"]}


def _get(header):
    from app.main import app

    return TestClient(app).get("/api/v1/billing/plans", headers=header)


@pytest.mark.parametrize("header, has_row", [
    ({"X-App-Version": "1.0"}, False),
    ({"X-App-Version": "1.0.3"}, False),
    ({"X-App-Version": "1.1"}, True),
    ({"X-App-Version": "1.01"}, True),   # the shipped name (owner, 2026-10-08)
    ({}, True),
    ({"X-App-Version": "garbage"}, True),
])
def test_the_theme_row_follows_the_app_version(memoised, header, has_row):
    rows = _rows(_get(header))
    for tier in ("free", "pro", "premium"):
        assert (_ROW in rows[tier]) is has_row, (header, tier, rows[tier])
        expected_others = [f["key"] for f in _features(tier, _CREDITS[tier]) if f["key"] != _ROW]
        assert [k for k in rows[tier] if k != _ROW] == expected_others  # every other row untouched


def test_the_memoised_catalogue_is_never_edited(memoised):
    before = copy.deepcopy(memoised)
    assert _ROW not in _rows(_get({"X-App-Version": "1.0"}))["free"]
    assert memoised == before, "the 1.0 filter edited the catalogue shared by every caller"
    assert _ROW in _rows(_get({"X-App-Version": "1.1"}))["free"]


def test_the_route_stays_public():
    """No credential is required — the paywall renders before sign-in."""
    from fastapi.routing import APIRoute
    from app.main import app
    from app.dependencies import get_current_user, get_current_user_id

    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == "/api/v1/billing/plans")
    calls = set()

    def walk(dependant):
        for dep in dependant.dependencies:
            calls.add(dep.call)
            walk(dep)

    walk(route.dependant)
    assert get_current_user not in calls and get_current_user_id not in calls
