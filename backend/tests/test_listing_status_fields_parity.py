"""The four "Delisted" wire fields are declared the SAME way on every response that carries them.

Contract (owner D1-D4, 2026-11-13): `listing_status` ("delisted" | null), `delisted_date`,
`delisted_exchange`, `last_trade_date` ("YYYY-MM-DD" | null) — all null unless a delisting is
confirmed, then all set together. Every one is Optional with a None default (a required key
would fail every shipped iOS build's decode: on Home it blanks the whole dashboard), and they
are appended LAST. `DelistedListing.wire()` is the only producer, so its keys must be exactly
`LISTING_FIELDS`.
"""

from __future__ import annotations

import typing
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.chat import StockChartWidget
from app.schemas.home_dashboard import MarketPulseItemResponse
from app.schemas.listing_status import (
    LISTING_FIELDS,
    LISTING_STATUS_DELISTED,
    NULL_LISTING_FIELDS,
    null_listing_fields,
)
from app.schemas.stock_overview import StockOverviewCoreResponse, StockOverviewResponse
from app.schemas.tracking import TrackedAssetResponse
from app.services.listing_status_service import DelistedListing

SCHEMAS = [StockOverviewCoreResponse, StockOverviewResponse, TrackedAssetResponse,
           MarketPulseItemResponse, StockChartWidget]


@pytest.mark.parametrize("model", SCHEMAS, ids=lambda m: m.__name__)
def test_every_schema_declares_the_four_optional_fields_last(model):
    fields = model.model_fields
    assert list(fields)[-4:] == list(LISTING_FIELDS), f"{model.__name__}: not appended last"
    for name in LISTING_FIELDS:
        info = fields[name]
        assert not info.is_required(), f"{model.__name__}.{name} must be Optional"
        assert info.default is None, f"{model.__name__}.{name} must default to None"
        args = set(typing.get_args(info.annotation))
        assert type(None) in args, f"{model.__name__}.{name} must accept null"
    status_args = typing.get_args(fields["listing_status"].annotation)
    literal = next(a for a in status_args if a is not type(None))
    assert typing.get_args(literal) == (LISTING_STATUS_DELISTED,), (
        f"{model.__name__}.listing_status must be Optional[Literal['delisted']]")
    for name in LISTING_FIELDS[1:]:
        assert set(typing.get_args(fields[name].annotation)) == {str, type(None)}


@pytest.mark.parametrize("model", [StockOverviewCoreResponse, StockOverviewResponse,
                                   TrackedAssetResponse, MarketPulseItemResponse,
                                   StockChartWidget], ids=lambda m: m.__name__)
def test_an_unknown_status_is_refused_at_the_boundary(model):
    """Only "delisted" may go out: iOS reads anything else as "not delisted", so a second
    value would be a silent contract change."""
    with pytest.raises(ValidationError):
        model.model_validate({**_minimal(model), "listing_status": "suspended"})
    ok = model.model_validate({**_minimal(model), "listing_status": "delisted"})
    assert ok.listing_status == "delisted"


def _minimal(model):
    from app.services.stock_overview_service import _get_market_status

    base = {
        StockOverviewCoreResponse: dict(symbol="X", company_name="X", current_price=1.0,
                                        price_change=0.0, price_change_percent=0.0,
                                        market_status=_get_market_status(), chart_data=[]),
        TrackedAssetResponse: dict(ticker="X", company_name="X"),
        MarketPulseItemResponse: dict(symbol="X", name="X", type="stock", price=1.0,
                                      change_percent=0.0, spark=[]),
        StockChartWidget: dict(ticker="X", company_name="X", current_price=1.0, change=0.0,
                               change_percent=0.0, day_high=0.0, day_low=0.0, volume=0,
                               avg_volume=0),
    }
    if model is StockOverviewResponse:
        from app.schemas.stock_overview import CompanyProfileResponse, SectorIndustryResponse

        core = base[StockOverviewCoreResponse]
        return {**core, "key_statistics": [], "key_statistics_groups": [],
                "performance_periods": [], "snapshots": [],
                "sector_industry": SectorIndustryResponse.model_construct(),
                "company_profile": CompanyProfileResponse.model_construct(),
                "related_tickers": []}
    return base[model]


def test_the_core_fields_are_a_subset_of_the_full_overview():
    assert set(StockOverviewCoreResponse.model_fields) <= set(StockOverviewResponse.model_fields)


@pytest.mark.parametrize("last", [date(2026, 11, 6), None])
@pytest.mark.parametrize("exchange", ["NASDAQ", None])
def test_wire_keys_are_exactly_the_listing_fields(last, exchange):
    wire = DelistedListing("ROKU", date(2026, 11, 9), exchange, last).wire()
    assert tuple(wire) == LISTING_FIELDS
    assert wire["listing_status"] == "delisted" and wire["delisted_date"] == "2026-11-09"
    assert wire["delisted_exchange"] == exchange
    assert wire["last_trade_date"] == (last.isoformat() if last else None)
    for model in SCHEMAS:
        model.model_validate({**_minimal(model), **wire})


def test_the_null_fields_are_read_only_and_all_none():
    assert dict(NULL_LISTING_FIELDS) == {k: None for k in LISTING_FIELDS}
    with pytest.raises(TypeError):
        NULL_LISTING_FIELDS["listing_status"] = "delisted"  # type: ignore[index]
    fresh = null_listing_fields()
    fresh["listing_status"] = "delisted"
    assert null_listing_fields()["listing_status"] is None, "a fresh dict per call"
