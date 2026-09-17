"""The Valuation Meter's DCF row (TestFlight E9 — Street Estimates replaced, 2026-09-17).

FMP's `discounted-cash-flow` is an INTRINSIC-VALUE estimate as of today, never a price
forecast, and a mechanical one: it reads −59% on AAPL and −87% on TER while NVDA is +6%
and PLUG is negative. So the wire carries the model's value and status only; iOS labels it
as a model value, computes the gap against the LIVE price (the snapshot is cached 24h) and
adds a caveat past ±50%. A negative model (loss-maker) sends NO value — a negative "fair
value" beside a price would be a fabricated verdict — and a missing model sends no row.
"""
from __future__ import annotations

import math

import pytest

from app.schemas.stock_overview import DcfEstimateResponse, SnapshotItemResponse
from app.services import valuation_snapshot_service as vss
from app.services.valuation_snapshot_service import dcf_estimate_from_row


@pytest.mark.parametrize("row, status, value", [
    ({"symbol": "NVDA", "date": "2026-09-17", "dcf": 227.42762125883232, "Stock Price": 213.9}, "ok", 227.43),
    ({"symbol": "AAPL", "date": "2026-09-17", "dcf": 135.83321790260416, "Stock Price": 332.41}, "ok", 135.83),
    ({"symbol": "PLUG", "date": "2026-09-17", "dcf": -13.536523809785628, "Stock Price": 2.02}, "negative_cash_flow", None),
    ({"dcf": 0}, "negative_cash_flow", None),
    ({"dcf": "44.5", "date": "2026-09-17"}, "ok", 44.5),
])
def test_dcf_row_maps_to_a_status_and_a_value(row, status, value):
    out = dcf_estimate_from_row(row)
    assert out is not None and out.status == status and out.value == value
    assert out.as_of == (str(row.get("date"))[:10] if row.get("date") else None)


@pytest.mark.parametrize("row", [{}, None, [], "x", {"dcf": None}, {"dcf": float("nan")}, {"dcf": float("inf")},
                                 {"dcf": "n/a"}, {"dcf": True}, {"Stock Price": 100.0}])
def test_a_missing_or_garbage_model_is_no_row_at_all(row):
    assert dcf_estimate_from_row(row) is None


def test_the_gap_is_not_computed_server_side():
    """The snapshot is cached 24h while the header price is live; a stored gap would rot."""
    fields = set(DcfEstimateResponse.model_fields)
    assert fields == {"status", "value", "as_of"}, fields


def test_the_snapshot_field_is_optional_and_absent_by_default():
    snap = SnapshotItemResponse(category="Price", rating=3, metrics=[])
    assert snap.dcf is None
    # a legacy cached row (no key) still validates
    assert SnapshotItemResponse(**{"category": "Price", "rating": 3, "metrics": []}).dcf is None
    # and the wire shape carries the three fields only
    wire = SnapshotItemResponse(category="Price", rating=3, metrics=[], dcf=dcf_estimate_from_row({"dcf": 10.0})).model_dump()
    assert wire["dcf"] == {"status": "ok", "value": 10.0, "as_of": None}


def test_the_payload_version_was_bumped_for_the_dcf_row():
    assert vss._SNAPSHOT_PAYLOAD_VERSION >= 4


@pytest.mark.asyncio
async def test_compute_attaches_the_dcf_and_survives_its_failure(monkeypatch):
    """Drives the real `_compute` with every upstream faked (the harness the sibling
    valuation tests use), once with a model and once with the DCF call raising."""
    from tests.test_negative_earnings_display import _RatiosFMP, _StubLookup
    from tests._price_fakes import PriceFromFMPFake

    class _WithDcf(_RatiosFMP):
        def __init__(self, ratios, dcf):
            super().__init__(ratios); self._dcf = dcf
        async def get_dcf(self, t):
            if isinstance(self._dcf, Exception):
                raise self._dcf
            return self._dcf

    monkeypatch.setattr(vss, "get_sector_benchmark_lookup", lambda: _StubLookup())
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)

    svc.fmp = _WithDcf({"priceToEarningsRatioTTM": 20.0}, {"symbol": "AAPL", "date": "2026-09-17", "dcf": 135.83})
    svc.price = PriceFromFMPFake(svc.fmp)
    snap = await svc._compute("AAPL")
    assert snap.dcf is not None and snap.dcf.status == "ok" and snap.dcf.value == 135.83

    svc.fmp = _WithDcf({"priceToEarningsRatioTTM": 20.0}, {"dcf": -13.5})
    svc.price = PriceFromFMPFake(svc.fmp)
    snap = await svc._compute("PLUG")
    assert snap.dcf is not None and snap.dcf.status == "negative_cash_flow" and snap.dcf.value is None

    svc.fmp = _WithDcf({"priceToEarningsRatioTTM": 20.0}, RuntimeError("402"))
    svc.price = PriceFromFMPFake(svc.fmp)
    snap = await svc._compute("TER")
    assert snap.dcf is None, "a failed DCF fetch must not take the meter down — just the row"
    assert snap.metrics, "the multiples still render"


@pytest.mark.asyncio
async def test_a_transient_dcf_failure_is_served_but_never_persisted(monkeypatch):
    """A 429 on the DCF slot must not pin "no model" into the 24h tier for a day.
    Positive control first: a healthy build IS persisted, so the spy provably works."""
    import asyncio
    from tests.test_negative_earnings_display import _RatiosFMP, _StubLookup
    from tests._price_fakes import PriceFromFMPFake
    from app.integrations.fmp import FMPNotEntitledException, FMPRateLimitException

    class _WithDcf(_RatiosFMP):
        def __init__(self, ratios, dcf):
            super().__init__(ratios); self._dcf = dcf
        async def get_dcf(self, t):
            if isinstance(self._dcf, Exception):
                raise self._dcf
            return self._dcf

    monkeypatch.setattr(vss, "get_sector_benchmark_lookup", lambda: _StubLookup())

    async def run(dcf):
        svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
        svc.fmp = _WithDcf({"priceToEarningsRatioTTM": 20.0}, dcf)
        svc.price = PriceFromFMPFake(svc.fmp)
        writes = []
        monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)
        monkeypatch.setattr(svc, "_upsert_supabase_cache", lambda *a, **k: writes.append(a))
        vss._cache.clear(); vss._inflight.clear()
        out = await svc.get_valuation_snapshot("AAPL")
        for _ in range(50):
            if writes:
                break
            await asyncio.sleep(0.01)
        return out, writes

    out, writes = await run({"dcf": 135.83, "date": "2026-09-17"})
    assert out.dcf is not None and writes, "control: a healthy build must reach the 24h tier"

    out, writes = await run(FMPRateLimitException("429"))
    assert out.dcf is None and out.metrics
    assert writes == [], "a build whose DCF slot raised was written to the 24h tier"

    # a permanent 402 is not degradation — persist, so the read path does not rebuild forever
    out, writes = await run(FMPNotEntitledException("402"))
    assert out.dcf is None and writes
