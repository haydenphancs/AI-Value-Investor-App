"""Tests for the persona-neutral ticker COLLECTION cache (ticker_data_cache).

The only fragile part is the fail-safe serialization round-trip: a
CollectedTickerData must survive serialize → (JSONB) → deserialize with every
field that assemble_report / build_financial_context RE-READS intact — dates
back as `date` objects (downstream does calendar math), the two flat dataclasses
(SectorAggregates incl. its datetime, IndustryTAM), and the Pydantic registry.
Any failure must degrade to a MISS (None), never a half-built / corrupt object.

Pure / offline — no network, no Supabase.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timezone

from app.services.ticker_data_cache import (
    _PYDANTIC_FIELDS,
    _deserialize,
    _serialize,
)
from app.services.agents.ticker_report_data_collector import CollectedTickerData
from app.services.industry_tam_service import IndustryTAM
from app.services.sector_aggregates_service import SectorAggregates


def _field_names():
    return {f.name for f in dataclasses.fields(CollectedTickerData)}


def _sample() -> CollectedTickerData:
    out = CollectedTickerData(ticker="ORCL", persona_key="warren_buffett")
    out.profile = {
        "symbol": "ORCL", "companyName": "Oracle Corporation",
        "sector": "Technology", "mktCap": 5.3e11,
    }
    out.income = [{"date": "2024-05-31", "revenue": 5.3e10, "netIncome": 1.04e10}]
    out.ratios = [{"grossProfitMargin": 0.70}, {"grossProfitMargin": 0.68}]
    out.computed = {
        "current_price": 192.64,
        "roe": 120.5,
        "fcf": 1.1e10,
        "recent_prices": [180.0, 185.5, 192.64],
        "recent_price_dates": [date(2026, 6, 14), date(2026, 6, 15), date(2026, 6, 16)],
        "monthly_prices": [{"month": "06/2026", "price": 192.64}],
    }
    out.meta = {"symbol": "ORCL", "company_name": "Oracle Corporation", "agent": "buffett"}
    out.sector_aggregates = SectorAggregates(
        sector="Technology", total_revenue_usd=1.0e12, cagr_5yr_pct=8.5,
        hhi=0.12, top1_share_pct=20.0, top2_share_pct=15.0,
        num_constituents=60, computed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    out.industry_tam = IndustryTAM(
        current_tam=500.0, future_tam=900.0, current_year="2025",
        future_year="2030", source_label="BEA (via FRED)", cagr_5y_pct=12.5,
    )
    return out


def test_serialize_is_json_clean():
    blob = _serialize(_sample())
    assert blob is not None
    json.dumps(blob)  # must not raise (no stray non-JSON types)


def test_roundtrip_preserves_reread_fields():
    out = _sample()
    back = _deserialize(_serialize(out), _field_names())
    assert back is not None
    assert back.ticker == "ORCL"
    assert back.persona_key == "warren_buffett"
    assert back.profile["companyName"] == "Oracle Corporation"
    assert back.computed["current_price"] == 192.64
    assert back.income[0]["revenue"] == 5.3e10
    assert back.ratios[1]["grossProfitMargin"] == 0.68
    assert back.meta["agent"] == "buffett"


def test_roundtrip_recent_price_dates_are_date_objects():
    # Downstream does calendar math on these — they MUST come back as `date`.
    back = _deserialize(_serialize(_sample()), _field_names())
    rpd = back.computed["recent_price_dates"]
    assert rpd == [date(2026, 6, 14), date(2026, 6, 15), date(2026, 6, 16)]
    assert all(isinstance(d, date) for d in rpd)


def test_roundtrip_flat_dataclasses():
    back = _deserialize(_serialize(_sample()), _field_names())
    assert isinstance(back.sector_aggregates, SectorAggregates)
    assert back.sector_aggregates.computed_at == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert back.sector_aggregates.num_constituents == 60
    assert isinstance(back.industry_tam, IndustryTAM)
    assert back.industry_tam.future_tam == 900.0
    assert back.industry_tam.cagr_5y_pct == 12.5


def test_none_object_fields_stay_none():
    back = _deserialize(_serialize(_sample()), _field_names())
    assert back.analyst_analysis is None
    assert back.holders_response is None
    assert back.signal_of_confidence is None
    assert back.earnings is None


def test_pydantic_registry_classes_are_models():
    # The registry must map every field to a real Pydantic model so the
    # model_dump(mode="json") / model_validate round-trip works.
    for name, cls in _PYDANTIC_FIELDS.items():
        assert hasattr(cls, "model_validate") and hasattr(cls, "model_dump"), name


def test_deserialize_incomplete_returns_none():
    # Missing profile/computed → not trustworthy → MISS, not a half object.
    assert _deserialize(
        {"ticker": "ORCL", "persona_key": "warren_buffett"}, _field_names()
    ) is None


def test_deserialize_garbage_is_fail_safe():
    # A malformed dataclass blob must never raise — just miss.
    bad = {"sector_aggregates": "not-a-dict",
           "profile": {"x": 1}, "computed": {"current_price": 1.0}}
    assert _deserialize(bad, _field_names()) is None


def test_unknown_field_in_cached_data_is_ignored():
    # A field removed from the dataclass (stale blob) must be skipped, not crash.
    blob = _serialize(_sample())
    blob["some_removed_field"] = {"x": 1}
    back = _deserialize(blob, _field_names())
    assert back is not None
    assert not hasattr(back, "some_removed_field")
