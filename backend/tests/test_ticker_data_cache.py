"""Tests for the persona-neutral ticker COLLECTION cache (ticker_data_cache).

The only fragile part is the fail-safe serialization round-trip: a
CollectedTickerData must survive serialize → (JSONB) → deserialize with every
field that assemble_report / build_financial_context RE-READS intact — dates
back as `date` objects (downstream does calendar math), the two flat dataclasses
(SectorAggregates incl. its datetime, IndustryDossier), and the Pydantic registry.
Any failure must degrade to a MISS (None), never a half-built / corrupt object.

Build every fixture with the type PRODUCTION puts on the field. `_sample()` used a
plain `IndustryTAM` for `industry_tam`, matching the wrong registry entry, while the
producer returns an `IndustryDossier` — so every cached collection for an equity with
an FMP industry read as a miss (2026-06-16 → 2026-10-01) with this file green.

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
from app.services.industry_dossier_service import IndustryDossier
from app.services.industry_tam_service import IndustryTAM
from app.services.sector_aggregates_service import SectorAggregates
from app.schemas.profit_power import ProfitPowerResponse, ProfitPowerDataPointSchema
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.schemas.dcf_fair_value import DcfFairValueResponse


def _field_names():
    return {f.name for f in dataclasses.fields(CollectedTickerData)}


_PLACEHOLDER_LABEL = "No public data available — FRED/Census unreachable at compute time"


def _dossier(**over) -> IndustryDossier:
    """The production `industry_tam` type, every field off its default."""
    kw = dict(
        current_tam=500.0, future_tam=900.0, current_year="2025", future_year="2030",
        source_label="BEA (via FRED)", cagr_5y_pct=12.5,
        industry="Software - Infrastructure", sector="Technology",
        lifecycle_phase="secular_growth", hhi=1834.5, top1_share_pct=31.2,
        top2_share_pct=44.9, concentration_label="oligopoly", constituent_count=56,
        source_grain="industry", tam_scope="global",
    )
    kw.update(over)
    return IndustryDossier(**kw)


# The shapes `get_or_compute_dossier` really returns (industry_dossier_service.py).
_DOSSIER_SHAPES = {
    # A stored row with a real TAM, returned untouched.
    "stored_real": _dossier(),
    # The July zero placeholder (138 of 158 production rows until the 2026-10-01 fix):
    # TAM 0, CAGR None, all-industry grain. Rows cached before that fix hold this.
    "zero_placeholder": _dossier(
        current_tam=0.0, future_tam=0.0, current_year="2026", future_year="2031",
        source_label=_PLACEHOLDER_LABEL, cagr_5y_pct=None, lifecycle_phase="mature",
        source_grain="all_industry", tam_scope="us",
    ),
    # No stored row: a live compute with no constituents → concentration None (never
    # the "fragmented" default) and no HHI / shares / count.
    "live_no_row": _dossier(
        current_tam=195.0, future_tam=270.9, current_year="2024", future_year="2029",
        source_label="US Census AIES (NAICS 335)", cagr_5y_pct=6.8,
        industry="Electrical Equipment & Parts", sector="Industrials",
        lifecycle_phase="mature", hhi=None, top1_share_pct=None, top2_share_pct=None,
        concentration_label=None, constituent_count=None, tam_scope="us",
    ),
}


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
    out.industry_tam = _dossier()
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
    # The Caydex Fair Value Estimate — populated because an unregistered one killed every
    # write on launch day (Sentry, 2026-09-26), and the stamp the read side checks.
    out.caydex_dcf = DcfFairValueResponse(
        symbol="ORCL", status="ok", fair_value=213.29, range_low=176.2, range_high=266.46,
        as_of="2026-09-26", model_version="dcf-v1",
    )
    out.wall_street_consensus_partial = {"dcf_source": "caydex"}
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
    assert type(back.industry_tam) is IndustryDossier
    assert back.industry_tam == _dossier()                 # every field, not just the TAM
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

    assert isinstance(back.caydex_dcf, DcfFairValueResponse)
    assert back.caydex_dcf == out.caydex_dcf
    assert back.caydex_dcf.model_dump()["range_low"] == 176.2   # what the collector reads


def test_no_field_hides_a_model_behind_any():
    """The guard above reads ANNOTATIONS, so a field typed `Any` is invisible to it. That is
    exactly how `caydex_dcf` (a DcfFairValueResponse) shipped unregistered and made every
    collection write fail on 2026-09-26. A bare `Any` field must be registered or listed
    here with a reason."""
    import typing

    allowed = {"industry_tam": "IndustryDossier, imported lazily — registered in _DATACLASS_FIELDS"}
    hints = typing.get_type_hints(CollectedTickerData)
    opaque = []
    for f in dataclasses.fields(CollectedTickerData):
        annotation = hints.get(f.name)
        args = typing.get_args(annotation) or (annotation,)
        if any(a is typing.Any for a in args) and typing.get_origin(annotation) in (None, typing.Union):
            if f.name not in allowed and f.name not in _PYDANTIC_FIELDS and f.name not in _DATACLASS_FIELDS:
                opaque.append(f.name)
    assert not opaque, f"fields typed Any that no registry covers: {opaque}"
    assert "industry_tam" in _DATACLASS_FIELDS, allowed["industry_tam"]


def _fake_supabase(row):
    class _Q:
        def __getattr__(self, _name):
            return lambda *a, **k: self
        def execute(self):
            return type("R", (), {"data": [row]})()
    return type("SB", (), {"table": lambda self, _n: _Q()})()


def test_a_collection_built_under_the_other_dcf_setting_is_a_miss(monkeypatch):
    """A collection carries its DCF (the estimate, the fair value derived from it, the
    stamp). Across a DCF_ENABLED flip it would build reports under the other setting for
    up to a day — the kill switch withdrawing nothing, or FMP's DCF beside the Caydex tab."""
    import asyncio
    import app.services.ticker_data_cache as tdc
    from app.config import settings

    blob = _serialize(_sample())                         # stamped "caydex"
    monkeypatch.setattr(tdc, "get_supabase", lambda: _fake_supabase(
        {"collected_data": blob, "cached_at": "2026-09-26T14:00:00+00:00"}))
    monkeypatch.setattr(tdc, "is_cache_fresh", lambda _at: True)

    monkeypatch.setattr(settings, "DCF_ENABLED", True)
    hit = asyncio.run(tdc.get_cached_collection("ORCL"))
    assert hit is not None and isinstance(hit.caydex_dcf, DcfFairValueResponse)   # anti-vacuity

    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    assert asyncio.run(tdc.get_cached_collection("ORCL")) is None

    # A row from before the stamp existed was built with FMP's DCF.
    legacy = dict(blob, wall_street_consensus_partial={})
    monkeypatch.setattr(tdc, "get_supabase", lambda: _fake_supabase(
        {"collected_data": legacy, "cached_at": "2026-09-26T14:00:00+00:00"}))
    assert asyncio.run(tdc.get_cached_collection("ORCL")) is not None
    monkeypatch.setattr(settings, "DCF_ENABLED", True)
    assert asyncio.run(tdc.get_cached_collection("ORCL")) is None


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


# ── industry_tam: the production type, end to end (2026-10-01) ───────────────
#
# The producer (`get_or_compute_dossier`) returns an IndustryDossier; the registry said
# IndustryTAM. The write succeeded, `IndustryTAM(**d)` raised on the dossier's extra keys,
# and every read was a MISS — while `is_cached_collection_fresh` reported the row fresh,
# so the pre-warmer skipped it and every report re-ran the cold collection.

import ast
import asyncio
import inspect
import typing

import pytest

import app.services.ticker_data_cache as tdc


@pytest.mark.parametrize("shape", sorted(_DOSSIER_SHAPES))
def test_every_real_dossier_shape_round_trips_exactly(shape):
    """Same TYPE and every field back — the zero placeholder and a None concentration
    included (`==` on a dataclass also requires the same class)."""
    out = _sample()
    out.industry_tam = _DOSSIER_SHAPES[shape]
    blob = _serialize(out)
    assert blob is not None
    back = _deserialize(json.loads(json.dumps(blob)), _field_names())   # through JSONB
    assert back is not None, f"{shape}: a cached collection read as a MISS"
    assert type(back.industry_tam) is IndustryDossier
    assert back.industry_tam == out.industry_tam


def test_a_narrower_compatible_dataclass_is_refused_at_write(caplog):
    """IndustryDossier is a SUPERSET of IndustryTAM, so a TAM-shaped dict would construct
    one — silently gaining source_grain='industry', lifecycle 'mature' and tam_scope 'us'
    from the defaults. The writer refuses anything but the registered type instead."""
    out = _sample()
    out.industry_tam = IndustryTAM(
        current_tam=500.0, future_tam=900.0, current_year="2025",
        future_year="2030", source_label="BEA (via FRED)", cagr_5y_pct=12.5,
    )
    assert _serialize(out) is None
    assert "industry_tam holds IndustryTAM; _DATACLASS_FIELDS registers IndustryDossier" in caplog.text


def test_a_dossier_blob_with_an_unknown_key_is_a_miss_not_a_crash():
    blob = _serialize(_sample())
    blob["industry_tam"]["field_removed_later"] = 1
    assert _deserialize(blob, _field_names()) is None


def test_dataclass_registry_matches_each_producer():
    """Each `_DATACLASS_FIELDS` class must be EXACTLY what the collector's producer returns.

    Two halves, both needed: the producers' return annotations name the registered class,
    and the collector really calls those producers for those fields (AST, so a comment
    cannot satisfy it). The old registry tests checked only that entries exist."""
    from app.services.industry_dossier_service import IndustryDossierService
    from app.services.sector_aggregates_service import get_sector_aggregates
    from app.services.agents import ticker_report_data_collector as collector

    producers = {
        "industry_tam": ("industry_tam_task", IndustryDossierService.get_or_compute_dossier),
        "sector_aggregates": ("sector_agg_task", get_sector_aggregates),
    }
    assert set(producers) == set(_DATACLASS_FIELDS)

    called_by_task: dict = {}
    for node in ast.walk(ast.parse(inspect.getsource(collector))):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            names = {
                (c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None))
                for c in ast.walk(node.value) if isinstance(c, ast.Call)
            }
            called_by_task.setdefault(node.targets[0].id, []).append(names)

    for field_name, (task, fn) in producers.items():
        assert len(called_by_task.get(task, [])) == 1, f"{task} assigned {called_by_task.get(task)}"
        assert fn.__name__ in called_by_task[task][0], f"{task} no longer calls {fn.__name__}"
        ret = typing.get_type_hints(fn)["return"]
        returned = [a for a in typing.get_args(ret) if a is not type(None)] or [ret]
        assert returned == [_DATACLASS_FIELDS[field_name][0]], (
            f"{fn.__qualname__} returns {returned}, but _DATACLASS_FIELDS[{field_name!r}] "
            f"registers {_DATACLASS_FIELDS[field_name][0].__name__}"
        )


class _FakeCacheDB:
    """One-row `ticker_data_cache`: upsert stores (through a JSON round-trip, like JSONB),
    select returns the selected columns."""

    def __init__(self):
        self.row = None
        self.upserts = 0

    def table(self, _name):
        return _FakeCacheQuery(self)


class _FakeCacheQuery:
    def __init__(self, db):
        self.db, self._upsert, self._cols = db, None, None

    def upsert(self, row, **_kw):
        self._upsert = json.loads(json.dumps(row))
        return self

    def select(self, cols):
        self._cols = [c.strip() for c in cols.split(",")]
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self._upsert is not None:
            self.db.row, self.db.upserts = self._upsert, self.db.upserts + 1
            return type("R", (), {"data": []})()
        if self.db.row is None:
            return type("R", (), {"data": []})()
        return type("R", (), {"data": [{c: self.db.row.get(c) for c in self._cols}]})()


@pytest.mark.parametrize("shape", sorted(_DOSSIER_SHAPES))
def test_a_stored_collection_that_probes_fresh_is_a_hit(monkeypatch, shape):
    """The invariant the outage broke: the pre-warmer's probe says fresh ⇒ the reader hits.
    Real store_collection → JSONB → real probe and reader, with the REAL close-cycle
    freshness (a row written now is fresh)."""
    from app.config import settings

    db = _FakeCacheDB()
    monkeypatch.setattr(tdc, "get_supabase", lambda: db)
    monkeypatch.setattr(settings, "DCF_ENABLED", True)      # _sample() is stamped "caydex"
    out = _sample()
    out.industry_tam = _DOSSIER_SHAPES[shape]

    asyncio.run(tdc.store_collection("plug", out))
    assert db.upserts == 1
    assert asyncio.run(tdc.is_cached_collection_fresh("PLUG")) is True
    hit = asyncio.run(tdc.get_cached_collection("PLUG"))
    assert hit is not None, f"{shape}: fresh row, but the reader MISSED"
    assert hit.industry_tam == out.industry_tam


def _incomplete(out):
    out.computed = {}                        # serializes, but `_deserialize` refuses it


def _pydantic_registered_under_the_wrong_class(monkeypatch):
    # The Pydantic twin of the industry_tam bug: model_dump uses the INSTANCE, so the
    # write succeeds; model_validate uses the REGISTERED class, so the read fails.
    monkeypatch.setitem(tdc._PYDANTIC_FIELDS, "profit_power", SnapshotItemResponse)


@pytest.mark.parametrize("case", ["incomplete", "pydantic_wrong_class"])
def test_store_never_writes_a_row_that_would_read_as_a_miss(monkeypatch, caplog, case):
    """A fresh-but-unreadable row is worse than no row: no row lets the pre-warmer warm it."""
    db = _FakeCacheDB()
    monkeypatch.setattr(tdc, "get_supabase", lambda: db)
    out = _sample()
    if case == "incomplete":
        _incomplete(out)
    else:
        _pydantic_registered_under_the_wrong_class(monkeypatch)
    assert _serialize(out) is not None       # anti-vacuity: only the READ-BACK can stop it

    asyncio.run(tdc.store_collection("ORCL", out))
    assert db.upserts == 0
    assert asyncio.run(tdc.is_cached_collection_fresh("ORCL")) is False
    assert "does not read back" in caplog.text
