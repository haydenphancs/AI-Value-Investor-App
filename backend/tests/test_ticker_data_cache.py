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
    _DATACLASS_FIELDS,
    _PYDANTIC_FIELDS,
    _deserialize,
    _serialize,
)
from app.services.agents.ticker_report_data_collector import CollectedTickerData
from app.services.industry_tam_service import IndustryTAM
from app.services.sector_aggregates_service import SectorAggregates
from app.schemas.profit_power import ProfitPowerResponse, ProfitPowerDataPointSchema
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse


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
    # ⚠️ At least one REGISTERED PYDANTIC field must be populated, or this whole file
    # is vacuous with respect to `_PYDANTIC_FIELDS`. `_serialize` tests `if val is None`
    # BEFORE the registry lookup, so a sample that leaves every model field None never
    # executes the `model_dump` branch at all — which is precisely why `profit_power`
    # being unregistered went unnoticed for two months while these tests stayed green.
    out.profit_power = ProfitPowerResponse(
        symbol="ORCL",
        annual=[ProfitPowerDataPointSchema(
            period="2024", gross_margin=70.1, operating_margin=31.5,
            fcf_margin=26.7, net_margin=19.6, sector_average_net_margin=12.1,
        )],
        quarterly=[ProfitPowerDataPointSchema(period="Q1'25", gross_margin=71.0)],
        peer_group_level="industry",
    )
    out.snap_valuation = SnapshotItemResponse(
        category="Price", rating=3,
        metrics=[SnapshotMetricResponse(name="P/E (1.2x sector avg 22)", value="27.59")],
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
    """Fields the sample deliberately leaves unset. Kept pointed at UNSET fields on
    purpose — `_sample()` now populates `profit_power` and `snap_valuation`, and
    asserting those were None was the bug: it made the registry branch untested."""
    back = _deserialize(_serialize(_sample()), _field_names())
    assert back.analyst_analysis is None
    assert back.holders_response is None
    assert back.signal_of_confidence is None
    assert back.earnings is None


def test_roundtrip_reconstructs_registered_pydantic_models():
    """The branch that was never executed before. A model must come back TYPED, not as
    the raw dict JSONB stores — downstream reads attributes off it."""
    out = _sample()
    back = _deserialize(_serialize(out), _field_names())

    assert isinstance(back.profit_power, ProfitPowerResponse)
    assert back.profit_power == out.profit_power           # lossless, deep
    assert isinstance(back.profit_power.annual[0], ProfitPowerDataPointSchema)
    assert back.profit_power.annual[0].sector_average_net_margin == 12.1
    assert back.profit_power.peer_group_level == "industry"

    assert isinstance(back.snap_valuation, SnapshotItemResponse)
    assert back.snap_valuation.metrics[0].value == "27.59"


def test_a_populated_unregistered_model_field_kills_the_whole_write():
    """WHY the guard below matters, made executable.

    An unregistered field is harmless while None and fatal once populated — and the
    failure is a SILENT `None` return, not an exception. This pins that blast radius:
    one unregistered field does not degrade one field, it discards the ENTIRE row.
    """
    import app.services.ticker_data_cache as tdc

    original = dict(tdc._PYDANTIC_FIELDS)
    try:
        tdc._PYDANTIC_FIELDS.pop("profit_power")
        assert _serialize(_sample()) is None            # the whole write is dropped
    finally:
        tdc._PYDANTIC_FIELDS.clear()
        tdc._PYDANTIC_FIELDS.update(original)

    assert _serialize(_sample()) is not None            # restored


def test_pydantic_registry_classes_are_models():
    # The registry must map every field to a real Pydantic model so the
    # model_dump(mode="json") / model_validate round-trip works.
    #
    # NOTE the direction: this walks the REGISTRY. It can only see entries that are
    # present, so it is structurally incapable of catching a field that was never
    # added — which is the bug that actually happened. The inverse guard below is the
    # one that closes the class; keep both.
    for name, cls in _PYDANTIC_FIELDS.items():
        assert hasattr(cls, "model_validate") and hasattr(cls, "model_dump"), name


# ── The guard that would have caught the two-month outage ────────────────────

def _model_typed_fields():
    """Every CollectedTickerData field whose annotation is (or wraps) a BaseModel."""
    import typing
    from pydantic import BaseModel

    hints = typing.get_type_hints(CollectedTickerData)
    found = {}
    for f in dataclasses.fields(CollectedTickerData):
        annotation = hints.get(f.name)
        # Unwrap Optional[X] / Union[X, None]; a bare X has no args.
        args = typing.get_args(annotation) or (annotation,)
        for arg in args:
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                found[f.name] = arg
                break
    return found


def test_every_pydantic_field_is_registered():
    """EVERY Pydantic-typed field on CollectedTickerData must be in _PYDANTIC_FIELDS.

    WHY THIS EXISTS — a real two-month production outage. `profit_power`
    (Optional[ProfitPowerResponse]) was added to CollectedTickerData on 2026-06-23 and
    never registered. `_serialize` checks `if val is None` BEFORE the registry lookup,
    so it only breaks once the field is POPULATED — and the service behind it never
    returns None for a normal ticker, so from that day every write raised inside the
    `json.dumps` guard, `_serialize` returned None, and `store_collection` skipped the
    write. No exception surfaced; requests succeeded; the ENTIRE 24h Supabase tier
    silently stopped existing, and since there is no in-memory tier every report and
    every hourly pre-warm re-ran a 38-leg FMP fan-out plus Gemini calls.

    Nothing caught it: `_sample()` populated no model field, so the registry branch was
    never executed, and `test_pydantic_registry_classes_are_models` walks the registry
    and so cannot see an absent key. This test walks the DATACLASS instead — the only
    direction that can.
    """
    missing = sorted(set(_model_typed_fields()) - set(_PYDANTIC_FIELDS))
    assert not missing, (
        "CollectedTickerData fields typed as Pydantic models but absent from "
        f"_PYDANTIC_FIELDS: {missing}. Serialization will silently return None the "
        "moment any of them is populated, discarding the whole cache row. Register "
        "them in app/services/ticker_data_cache.py."
    )


def test_the_registry_guard_is_not_vacuous():
    """If the annotation walk found nothing, the guard above asserts nothing.

    Pins that it really does resolve model-typed fields — including the one that broke.
    """
    found = _model_typed_fields()
    assert len(found) >= 10, f"annotation walk resolved only {len(found)} fields"
    assert found.get("profit_power") is not None
    assert found["profit_power"].__name__ == "ProfitPowerResponse"


def test_every_dataclass_field_is_registered():
    """Same completeness rule for the flat-dataclass registry, which has the identical
    failure mode: unregistered → `else` branch → json.dumps raises → row discarded."""
    import typing

    hints = typing.get_type_hints(CollectedTickerData)
    missing = []
    for f in dataclasses.fields(CollectedTickerData):
        annotation = hints.get(f.name)
        args = typing.get_args(annotation) or (annotation,)
        for arg in args:
            if (isinstance(arg, type) and dataclasses.is_dataclass(arg)
                    and f.name not in _DATACLASS_FIELDS):
                missing.append(f.name)
                break
    assert not missing, (
        f"dataclass-typed fields absent from _DATACLASS_FIELDS: {sorted(missing)}"
    )


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
