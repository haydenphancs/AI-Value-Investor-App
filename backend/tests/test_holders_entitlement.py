"""The Congress segment of a ticker's Holders tab is Pro/Max (TestFlight E3, 2026-09-17).

The route used to serve every congressional trade free while `/signals/{kind}/{ticker}`
and the whale profile withheld the same rows. Now `stocks.get_holders` resolves the
caller's tier and calls `HoldersService.get_holders_for_tier`, which redacts a COPY of
the cached payload for Free. Three properties matter and each has a test that drives the
real code rather than a leaf:

* redaction never mutates the shared cached instance (three `get_holders` exits return it);
* `congress_data` is never null on the wire (shipped iOS DTO requires it);
* the internal callers (report collector, ownership snapshot) stay ungated.
"""
from __future__ import annotations

import inspect
import logging

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import stocks
from app.dependencies import get_current_user, get_current_user_id
from app.main import app
from app.schemas.holders import (
    CongressActivitiesDataSchema,
    CongressActivitySchema,
    HoldersResponse,
    RecentActivitiesSchema,
    SmartMoneyDataSchema,
    SmartMoneyFlowDataPointSchema,
)
from app.services import holders_service as hs
from app.services.holders_service import HoldersService, redact_congress


def _rich() -> HoldersResponse:
    return HoldersResponse(
        symbol="TER",
        congress_data=SmartMoneyDataSchema(
            tab="Congress",
            flow_data=[SmartMoneyFlowDataPointSchema(month="05/2026", buy_volume=1.0, sell_volume=0.0)],
        ),
        recent_activities=RecentActivitiesSchema(
            congress_activities=CongressActivitiesDataSchema(
                activities=[CongressActivitySchema(name="A. Senator", date="2026-05-01")],
            ),
        ),
    )


def _svc_returning(resp: HoldersResponse) -> HoldersService:
    svc = HoldersService.__new__(HoldersService)

    async def fake_get_holders(ticker):
        return resp

    svc.get_holders = fake_get_holders  # instance attribute; the class stays untouched
    return svc


# ── service ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_free_caller_gets_empty_congress_and_the_flag():
    resp = _rich()
    out = await _svc_returning(resp).get_holders_for_tier("TER", "free")
    assert out.congress_locked is True
    assert out.congress_tier_required == "pro"
    assert out.congress_data.flow_data == []
    assert out.congress_data.tab == "Congress"
    assert out.recent_activities.congress_activities.activities == []
    # the free halves of the card are untouched
    assert out.insider_data == resp.insider_data
    assert out.hedge_funds_data == resp.hedge_funds_data
    assert out.shareholder_breakdown == resp.shareholder_breakdown


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["pro", "premium"])
async def test_paid_caller_gets_the_unredacted_build(tier):
    resp = _rich()
    out = await _svc_returning(resp).get_holders_for_tier("TER", tier)
    assert out is resp
    assert out.congress_locked is False and out.congress_tier_required is None


@pytest.mark.asyncio
async def test_redaction_does_not_mutate_the_cached_instance():
    resp = _rich()
    await _svc_returning(resp).get_holders_for_tier("TER", "free")
    assert len(resp.congress_data.flow_data) == 1
    assert len(resp.recent_activities.congress_activities.activities) == 1
    assert resp.congress_locked is False


def test_congress_data_is_never_null_on_the_wire():
    wire = redact_congress(_rich(), "pro").model_dump()
    assert isinstance(wire["congress_data"], dict)
    assert wire["congress_data"]["tab"] == "Congress"
    assert wire["congress_data"]["flow_data"] == []
    assert isinstance(wire["recent_activities"]["congress_activities"]["activities"], list)
    assert wire["congress_locked"] is True and wire["congress_tier_required"] == "pro"


def test_an_unversioned_holders_row_rehydrates_unlocked():
    """Pre-fix `holders_cache.response_json` rows carry neither field; they must read
    as UNLOCKED (the gate is applied on read, above every cache layer)."""
    old = _rich().model_dump()
    old.pop("congress_locked"); old.pop("congress_tier_required")
    assert HoldersResponse(**old).congress_locked is False


def test_internal_callers_stay_ungated():
    """The report collector and the ownership snapshot carry no tier; gating inside
    `get_holders` would strip Congress from every 20-credit report."""
    import ast
    from app.services import ownership_snapshot_service as oss
    from app.services.agents import ticker_report_data_collector as trdc
    for mod in (oss, trdc):
        tree = ast.parse(inspect.getsource(mod))
        attrs = [n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        assert "get_holders" in attrs, f"{mod.__name__} no longer calls get_holders"
        assert "get_holders_for_tier" not in attrs, f"{mod.__name__} would fold a None tier to Free"


# ── route ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    logging.disable(logging.CRITICAL)
    try:
        yield TestClient(app)   # no context manager: the lifespan reaches Supabase
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture
def holders_fake(monkeypatch):
    resp = _rich()
    monkeypatch.setattr(stocks, "get_holders_service", lambda: _svc_returning(resp))
    return resp


def _signed_in_as(tier):
    # The stocks router carries a router-level `get_current_user_id` guard AND the route's
    # own `get_watchlist_identity` (→ `get_current_user`); both must be satisfied.
    app.dependency_overrides[get_current_user_id] = lambda: "u-1"
    app.dependency_overrides[get_current_user] = lambda: {"id": "u-1", "tier": tier}


@pytest.mark.parametrize("tier, locked", [("free", True), ("pro", False), ("premium", False)])
def test_the_route_redacts_by_the_callers_tier(client, holders_fake, tier, locked):
    _signed_in_as(tier)
    try:
        r = client.get("/api/v1/stocks/TER/holders")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_id, None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["congress_locked"] is locked
    assert (body["congress_data"]["flow_data"] == []) is locked
    assert (body["recent_activities"]["congress_activities"]["activities"] == []) is locked
    assert body["congress_tier_required"] == ("pro" if locked else None)
    # the fake's instance was never modified by a Free read
    assert len(holders_fake.congress_data.flow_data) == 1


def test_the_route_signature_carries_the_identity():
    from app.dependencies import get_watchlist_identity
    params = inspect.signature(stocks.get_holders).parameters
    assert params["user"].default.dependency is get_watchlist_identity
    assert "get_holders_for_tier(" in inspect.getsource(stocks.get_holders)
