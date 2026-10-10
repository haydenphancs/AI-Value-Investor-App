"""Round 3 (H2) — report snapshots, sector history and the shared caches.

Owner decisions (2026-09-30): a snapshot or section reason that describes the COMPANY (no
failed leg) never blocks report / collection caching; a permanent vendor gap must not make
a report uncacheable forever. A real outage still fails closed.

Pins, each failing on the code before this round:
  * P3/P4 — a snapshot whose only reasons are company state ("no_values",
            "health_check:no_metrics") is left out (None) but NOT recorded on
            `degraded_sections`, so the collection and the report stay cacheable.
  * SoC contract — "cash_flow_statement_missing" (the cash-flow leg answered but matched
            no displayed quarter) drops the Signal of Confidence section without
            recording it; "cash_flow" (a raised leg) still blocks.
  * P6/P23 — a FAILED sector-history benchmark read (not an empty one) records
            "sector_history:benchmarks", keeping the collection and report out of the
            shared caches; what loaded is kept for this caller.
  * P24 — the growth snapshot card (annual series only) no longer inherits the
            quarterly-only legs of a growth build.
  * P25 — the valuation snapshot payload version is bumped, so a pre-deploy v4 row is
            rebuilt once.

Hermetic: stubs only, no FMP / Supabase / Gemini.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    _read_sector_history,
    _refuse_degraded_financials,
    _settle_sector_history,
    _settle_snapshot_result,
)
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE,
    BenchmarkLookupFailed,
    lookup_failed,
)


def _out(**attrs):
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    for k, v in attrs.items():
        setattr(out, k, v)
    return out


def _snap(category="Growth"):
    return SnapshotItemResponse(category=category, rating=3, metrics=[], weighted_score=3.0)


async def _stored_by_get_or_collect(monkeypatch, out, ticker) -> List[str]:
    """Run `ticker_data_cache.get_or_collect` over a ready collection; return the tickers
    it STORED (the cache tiers stubbed)."""
    from app.services import ticker_data_cache as tdc

    writes: List[str] = []

    async def _get_cached(_t):
        return None

    async def _store(t, _out):
        writes.append(t)

    monkeypatch.setattr(tdc, "get_cached_collection", _get_cached)
    monkeypatch.setattr(tdc, "store_collection", _store)

    async def _fetch():
        return out

    assert await tdc.get_or_collect(ticker, _fetch) is out
    return writes


# ═══════════════════════════════════════════════════════════════════════════
# P3 / P4 — a company-state snapshot status never blocks the caches
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "attr, reasons",
    [
        ("snap_growth", ["no_values"]),
        ("snap_profitability", ["no_values"]),
        ("snap_health", ["health_check:no_metrics"]),
        ("snap_health", ["health_check:no_metrics", "no_values"]),
    ],
)
def test_a_company_state_snapshot_is_left_out_but_not_recorded(attr, reasons):
    out = _out()
    _settle_snapshot_result(out, attr, (_snap(), list(reasons)), "NEWCO")
    assert getattr(out, attr) is None, (
        "the all-absent card carries a made-up neutral rating — it must stay out"
    )
    assert out.degraded_sections == [], (
        f"{reasons} describes the company, not an outage: recording it keeps the ticker "
        "out of every shared cache forever"
    )


@pytest.mark.parametrize(
    "reasons",
    [
        ["annual_income", "no_values"],
        ["benchmarks", "no_values"],
        ["profit_power", "no_values"],
        ["health_check", "health_check:no_metrics"],
        ["health_check:income", "health_check:no_metrics"],
        ["status_unknown"],
        ["no_metrics"],          # not in the set: an unknown spelling fails closed
    ],
)
def test_an_outage_beside_a_company_state_reason_still_blocks(reasons):
    out = _out()
    _settle_snapshot_result(out, "snap_health", (_snap(), list(reasons)), "AAPL")
    assert out.snap_health is None
    assert out.degraded_sections == [f"snap_health:{'+'.join(reasons)}"]


def test_a_malformed_status_that_spells_a_company_reason_still_blocks():
    """A bare string is not a status list: it reads as malformed, never as company state."""
    out = _out()
    _settle_snapshot_result(out, "snap_growth", (_snap(), "no_values"), "AAPL")
    assert out.snap_growth is None
    assert out.degraded_sections == ["snap_growth:status_malformed"]


def _growth_from_statement(rows) -> GrowthResponse:
    """A GrowthResponse built from REAL `_compute_growth_points` output."""
    from app.services.growth_service import _compute_growth_points

    def _series(field):
        return [
            GrowthDataPointSchema(period=p["period"], value=p["value"],
                                  yoy_change_percent=p["yoy_change_percent"])
            for p in _compute_growth_points(rows, field, is_quarterly=False)
        ]

    return GrowthResponse(
        symbol="NEWCO", eps_annual=_series("epsDiluted"), eps_quarterly=[],
        revenue_annual=_series("revenue"), revenue_quarterly=[],
        operating_profit_annual=_series("operatingIncome"),
        free_cash_flow_annual=_series("freeCashFlow"),
    )


def _growth_snapshot_service(monkeypatch, growth_answer):
    from app.services import growth_service as gmod
    from app.services import growth_snapshot_service as gs

    gs._cache.clear()
    gs._inflight.clear()
    gs._degraded_by_key.clear()

    class _Growth:
        async def get_growth_with_status(self, ticker):
            response, degraded = growth_answer
            return response, list(degraded)

    # Function-local import inside `_compute_with_status`: patch the SOURCE module.
    monkeypatch.setattr(gmod, "get_growth_service", lambda: _Growth())
    svc = gs.GrowthSnapshotService.__new__(gs.GrowthSnapshotService)
    svc.supabase = None
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    return gs, svc


def _spy_persist(monkeypatch, svc, method: str = "_upsert_supabase_cache") -> List[str]:
    """Record the fire-and-forget 24h-tier write inline (it is never awaited)."""
    persisted: List[str] = []

    def _record(ticker, *rest):
        persisted.append(ticker)

    monkeypatch.setattr(svc, method, _record)
    loop = asyncio.get_running_loop()
    real = loop.run_in_executor

    def _run_in_executor(executor, fn, *args):
        if fn is _record:
            fn(*args)
            done = loop.create_future()
            done.set_result(None)
            return done
        return real(executor, fn, *args)

    monkeypatch.setattr(loop, "run_in_executor", _run_in_executor)
    return persisted


@pytest.mark.asyncio
async def test_a_fiscal_year_change_growth_card_keeps_the_collection_cacheable(monkeypatch):
    """The reported scenario end to end: a fiscal-year-end change (FY 2024-12-31 then
    2025-09-30) leaves every latest annual YoY null with NO failed leg. The card is
    'no_values' on every rebuild; the collection must still be stored."""
    rows = [
        {"date": "2025-09-30", "revenue": 120.0, "epsDiluted": 1.2,
         "operatingIncome": 30.0, "freeCashFlow": 20.0},
        {"date": "2024-12-31", "revenue": 100.0, "epsDiluted": 1.0,
         "operatingIncome": 25.0, "freeCashFlow": 18.0},
    ]
    growth = _growth_from_statement(rows)
    assert growth.revenue_annual and growth.revenue_annual[-1].yoy_change_percent is None

    gs, svc = _growth_snapshot_service(monkeypatch, (growth, []))
    persisted = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_growth_snapshot_with_status("NEWCO")
    assert degraded == ["no_values"] and persisted == []  # the service still never pins it

    out = _out(ticker="NEWCO")
    _settle_snapshot_result(out, "snap_growth", (snap, degraded), "NEWCO")
    assert out.snap_growth is None and out.degraded_sections == []
    assert await _stored_by_get_or_collect(monkeypatch, out, "R3FYCHG") == ["R3FYCHG"]
    gs._cache.clear()
    gs._degraded_by_key.clear()


@pytest.mark.asyncio
async def test_an_outage_snapshot_still_keeps_the_collection_out_of_the_cache(monkeypatch):
    out = _out()
    _settle_snapshot_result(out, "snap_growth", (_snap(), ["annual_income", "no_values"]), "AAPL")
    assert await _stored_by_get_or_collect(monkeypatch, out, "R3OUTAGE") == []


# ═══════════════════════════════════════════════════════════════════════════
# CONTRACT — Signal of Confidence "cash_flow_statement_missing"
# ═══════════════════════════════════════════════════════════════════════════


def _soc_out(attr, reasons):
    out = SimpleNamespace(ticker="T", degraded_sections=[])
    for other in ("growth_chart", "profit_power", "earnings", "signal_of_confidence",
                  "revenue_breakdown"):
        setattr(out, other, None)
    setattr(out, attr, SimpleNamespace(degraded=list(reasons)))
    return out


@pytest.mark.parametrize(
    "reasons",
    [["cash_flow_statement_missing"], ["cash_flow_statement_missing", "cash_flow_row"]],
)
def test_soc_cash_flow_statement_missing_drops_the_section_but_stays_cacheable(reasons):
    out = _soc_out("signal_of_confidence", reasons)
    _refuse_degraded_financials(out)
    assert out.signal_of_confidence is None, "every yield would be an unknown charted as $0"
    assert out.degraded_sections == [], "a permanent vendor gap must not block the caches"


@pytest.mark.parametrize(
    "reasons",
    [["cash_flow"], ["cash_flow_statement_missing", "income"],
     ["cash_flow_statement_missing", "market_cap"]],
)
def test_soc_failed_legs_still_block(reasons):
    out = _soc_out("signal_of_confidence", reasons)
    _refuse_degraded_financials(out)
    assert out.signal_of_confidence is None
    assert out.degraded_sections == [f"signal_of_confidence:{'+'.join(reasons)}"]


@pytest.mark.parametrize("attr", ["earnings", "revenue_breakdown"])
def test_the_company_state_reason_is_scoped_to_the_soc_section(attr):
    out = _soc_out(attr, ["cash_flow_statement_missing"])
    _refuse_degraded_financials(out)
    assert getattr(out, attr) is None
    assert out.degraded_sections == [f"{attr}:cash_flow_statement_missing"]


@pytest.mark.asyncio
async def test_a_collection_that_lost_only_the_soc_to_a_vendor_gap_is_stored(monkeypatch):
    out = _out(signal_of_confidence=SimpleNamespace(degraded=["cash_flow_statement_missing"]))
    _refuse_degraded_financials(out)
    assert out.signal_of_confidence is None
    assert await _stored_by_get_or_collect(monkeypatch, out, "R3SOC") == ["R3SOC"]


# ═══════════════════════════════════════════════════════════════════════════
# P6 / P23 — a FAILED sector-history read is recorded
# ═══════════════════════════════════════════════════════════════════════════

_R3_INDUSTRY = "Round3 H2 Test Industry"


def _real_lookup(monkeypatch, *, fail_period_types=(), rows_by_period=None):
    """A REAL SectorBenchmarkLookup (so the real get_benchmarks / flatten run) whose
    paginated row read raises for the named period types."""
    import app.services.sector_benchmark_lookup as sbl

    rows_by_period = rows_by_period or {}
    requested: List[str] = []

    def _fetch_rows(self, columns, sector, metrics, period_type, industry=""):
        requested.append(period_type)
        if period_type in fail_period_types:
            raise RuntimeError(f"Cloudflare 520 on {period_type}")
        if industry:
            return []
        return list(rows_by_period.get(period_type, []))

    monkeypatch.setattr(sbl.SectorBenchmarkLookup, "_fetch_rows", _fetch_rows)
    lookup = sbl.SectorBenchmarkLookup.__new__(sbl.SectorBenchmarkLookup)
    lookup.supabase = None
    monkeypatch.setattr(sbl, "get_sector_benchmark_lookup", lambda: lookup)
    return sbl, requested


def _clear_lookup_cache(sbl):
    for key in [k for k in list(sbl._cache) if _R3_INDUSTRY in str(k)]:
        sbl._cache.pop(key, None)


def _row(metric, label, value):
    return {"metric_name": metric, "period_label": label, "median_value": value,
            "sample_size": 40}


@pytest.mark.asyncio
async def test_a_failed_calendar_quarter_read_is_flagged_and_the_annual_line_kept(monkeypatch):
    metric = C._SECTOR_HISTORY_METRIC_NAMES[0]
    sbl, requested = _real_lookup(
        monkeypatch, fail_period_types={CALENDAR_QUARTER_PERIOD_TYPE},
        rows_by_period={"annual": [_row(metric, "2023", 11.0)]},
    )
    try:
        coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
        hist = await coll._fetch_sector_benchmark_history(_R3_INDUSTRY, "Technology")
        assert lookup_failed(hist), (
            "a Supabase error on the calendar-quarter read reads like 'no rows' again"
        )
        assert hist["annual"][metric]["2023"] == 11.0, "what loaded must be kept"
        assert all(v == {} for v in hist["quarterly"].values())
        assert set(requested) == {"annual", CALENDAR_QUARTER_PERIOD_TYPE, "ttm"}
        assert "quarterly" not in requested, "never the legacy fiscal-keyed rows"
    finally:
        _clear_lookup_cache(sbl)


@pytest.mark.asyncio
async def test_a_clean_empty_read_is_not_a_failure(monkeypatch):
    sbl, _requested = _real_lookup(monkeypatch)
    try:
        coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
        hist = await coll._fetch_sector_benchmark_history(_R3_INDUSTRY, "Technology")
        assert not lookup_failed(hist), "an empty peer group is the company's shape"
        # `levels` (2026-10-07): each metric line's peer group, for the drill-down legend.
        assert set(hist) == {"annual", "quarterly", "levels"}
        assert set(hist["levels"]) == {"annual", "quarterly"}
    finally:
        _clear_lookup_cache(sbl)


@pytest.mark.asyncio
async def test_a_failed_ttm_read_is_flagged_too(monkeypatch):
    sbl, _requested = _real_lookup(monkeypatch, fail_period_types={"ttm"})
    try:
        coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
        assert lookup_failed(
            await coll._fetch_sector_benchmark_history(_R3_INDUSTRY, "Technology")
        )
    finally:
        _clear_lookup_cache(sbl)


@pytest.mark.asyncio
async def test_a_read_that_raises_is_a_failure_not_an_empty_answer(monkeypatch):
    class _Raising:
        def get_benchmarks(self, industry, sector, metrics, period_type):
            if period_type == "annual":
                raise KeyError("value")
            return {m: {} for m in metrics}

    monkeypatch.setattr(
        "app.services.sector_benchmark_lookup.get_sector_benchmark_lookup", lambda: _Raising(),
    )
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    hist = await coll._fetch_sector_benchmark_history(_R3_INDUSTRY, "Technology")
    assert lookup_failed(hist)
    assert hist["annual"] == {}


def test_read_sector_history_flattens_like_get_benchmark_values():
    class _Rich:
        def get_benchmarks(self, industry, sector, metrics, period_type):
            return {
                "gross_margin": {"2024": {"value": 40.0, "level": "industry", "n": 30},
                                 "2023": {"value": None, "level": "sector", "n": 25}},
                "pe": {},
            }

    flat, failed = _read_sector_history(_Rich(), "I", "Technology", ["gross_margin", "pe"],
                                        "annual")
    assert flat == {"gross_margin": {"2024": 40.0, "2023": None}, "pe": {}}
    assert failed is False


@pytest.mark.parametrize(
    "answer, failed",
    [
        ({"pe": {"2024": 20.0}}, False),
        (BenchmarkLookupFailed({"pe": {}}), True),
        (None, True),                       # not a mapping: a broken read
    ],
)
def test_a_lookup_with_only_the_flat_reader_is_honoured(answer, failed):
    class _FlatOnly:
        def get_benchmark_values(self, industry, sector, metrics, period_type):
            return answer

    flat, got = _read_sector_history(_FlatOnly(), "I", "Technology", ["pe"], "annual")
    assert got is failed
    assert flat == ({"pe": {"2024": 20.0}} if not failed else ({"pe": {}} if answer else {}))


def test_settle_records_a_failed_read_and_keeps_what_loaded():
    out = _out()
    loaded = {"annual": {"pe": {"2024": 20.0}}, "quarterly": {}}
    _settle_sector_history(out, BenchmarkLookupFailed(loaded), "AAPL")
    assert out.sector_benchmark_history == loaded
    assert type(out.sector_benchmark_history) is dict, "store a plain mapping"
    assert out.degraded_sections == ["sector_history:benchmarks"]


def test_settle_records_a_raised_fetch():
    out = _out()
    _settle_sector_history(out, RuntimeError("boom"), "AAPL")
    assert out.sector_benchmark_history == {}
    assert out.degraded_sections == ["sector_history:benchmarks"]


@pytest.mark.parametrize("result", [{"annual": {}, "quarterly": {}}, {}, None])
def test_settle_does_not_record_a_clean_or_empty_read(result):
    out = _out()
    _settle_sector_history(out, result, "AAPL")
    assert out.sector_benchmark_history == (result or {})
    assert out.degraded_sections == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failed, stored", [(True, False), (False, True)])
async def test_pass_two_records_a_failed_sector_history_and_skips_the_cache(
    monkeypatch, failed, stored,
):
    """`_fetch_dependent` end to end (every other leg stubbed): a failed sector-history
    read keeps the collection out of ticker_data_cache; a clean one does not."""
    import app.services.ip_intel_service as ips

    class _NoIp:
        async def get_ip_intel(self, ticker, profile):
            return None

    async def _no_aggregates(_sector):
        return None

    monkeypatch.setattr(ips, "get_ip_intel_service", lambda: _NoIp())
    monkeypatch.setattr(C, "get_sector_aggregates", _no_aggregates)

    history = {"annual": {"pe": {"2024": 20.0}}, "quarterly": {}}

    async def _history(industry, sector):
        return BenchmarkLookupFailed(history) if failed else dict(history)

    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    coll.fmp = None
    monkeypatch.setattr(coll, "_fetch_sector_benchmark_history", _history)

    out = _out(ticker="R3SECT", profile={"sector": "Technology"})
    await coll._fetch_dependent(out)
    assert out.sector_benchmark_history == history
    assert out.degraded_sections == (["sector_history:benchmarks"] if failed else [])
    writes = await _stored_by_get_or_collect(monkeypatch, out, "R3SECT")
    assert writes == (["R3SECT"] if stored else [])


# ═══════════════════════════════════════════════════════════════════════════
# P24 — the annual-only growth card ignores quarterly-only legs
# ═══════════════════════════════════════════════════════════════════════════


def _point(yoy=12.0):
    return [GrowthDataPointSchema(period="2025", value=10.0, yoy_change_percent=yoy,
                                  sector_average_yoy=8.0)]


def _measured_growth() -> GrowthResponse:
    return GrowthResponse(
        symbol="AAPL", eps_annual=_point(), eps_quarterly=[], revenue_annual=_point(),
        revenue_quarterly=[], operating_profit_annual=_point(),
        free_cash_flow_annual=_point(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream, expected",
    [
        (["quarterly_income"], []),
        (["quarterly_cashflow"], []),
        (["quarterly_income", "quarterly_cashflow"], []),
        (["quarterly_cashflow", "annual_cashflow"], ["annual_cashflow"]),
        (["annual_income"], ["annual_income"]),
        (["profile"], ["profile"]),
        (["benchmarks"], ["benchmarks"]),      # annual vs quarterly unknown: fail closed
        (["status_unknown"], ["status_unknown"]),
    ],
)
async def test_growth_card_drops_only_the_quarterly_only_legs(monkeypatch, upstream, expected):
    gs, svc = _growth_snapshot_service(monkeypatch, (_measured_growth(), upstream))
    persisted = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_growth_snapshot_with_status("AAPL")
    assert degraded == expected
    assert snap.rating >= 1 and all(m.value != "—" for m in snap.metrics)
    assert persisted == ([] if expected else ["AAPL"]), (
        "an annual-only card measured exactly as a clean build must be persisted"
    )
    # ...and the report keeps it.
    out = _out()
    _settle_snapshot_result(out, "snap_growth", (snap, degraded), "AAPL")
    assert (out.snap_growth is snap) is (not expected)
    gs._cache.clear()
    gs._degraded_by_key.clear()


@pytest.mark.asyncio
async def test_growth_tier1_hit_on_an_annual_leg_failure_is_still_not_persisted(monkeypatch):
    """The Tier-1 propagation guard, re-pinned on a leg the card actually reads (the older
    pin used a quarterly-only leg, which the card now rightly ignores)."""
    from app.services import growth_service as gmod
    from app.services import growth_snapshot_service as gs

    gmod._cache.clear()
    gmod._inflight.clear()
    gmod._degraded_by_key.clear()
    growth_svc = gmod.GrowthService.__new__(gmod.GrowthService)
    growth_svc.supabase = None
    growth_svc.fmp = None

    async def _build(ticker):
        return _measured_growth(), ["annual_income"]

    monkeypatch.setattr(growth_svc, "_check_supabase_cache", lambda ticker: None)
    monkeypatch.setattr(growth_svc, "_next_earnings_date_safe", lambda ticker: None)
    monkeypatch.setattr(growth_svc, "_build_growth", _build)
    growth_persisted = _spy_persist(monkeypatch, growth_svc, "_upsert_supabase_cache_safe")
    await growth_svc.get_growth("AAPL")
    assert growth_persisted == []

    gs._cache.clear()
    gs._inflight.clear()
    gs._degraded_by_key.clear()
    monkeypatch.setattr(gmod, "get_growth_service", lambda: growth_svc)
    snap_svc = gs.GrowthSnapshotService.__new__(gs.GrowthSnapshotService)
    snap_svc.supabase = None
    monkeypatch.setattr(snap_svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, snap_svc)
    _snap_value, degraded = await snap_svc.get_growth_snapshot_with_status("AAPL")
    assert degraded == ["annual_income"]
    assert persisted == []
    gs._cache.clear()
    gs._degraded_by_key.clear()
    gmod._cache.clear()
    gmod._degraded_by_key.clear()


# ═══════════════════════════════════════════════════════════════════════════
# P25 — valuation payload version bumped past the pre-deploy rows
# ═══════════════════════════════════════════════════════════════════════════


class _FakeSnapshotTable:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def table(self, _):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        from datetime import datetime, timezone

        return SimpleNamespace(data=[{
            "response_json": self._payload,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }])


def test_a_pre_deploy_v4_valuation_row_is_rebuilt():
    from app.config import settings
    import app.services.valuation_snapshot_service as vss

    # 7 since 2026-10-09 (NET-4: a listed non-lender member's Price card is peer-free);
    # 8 the same day (Earnings Yield = 1 / the card's displayed P/E).
    assert vss._SNAPSHOT_PAYLOAD_VERSION == 8
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    row = {"category": "Price", "rating": 3, "full_report_available": True, "metrics": [],
           vss._DCF_SOURCE_KEY: bool(settings.DCF_ENABLED)}
    svc.supabase = _FakeSnapshotTable({**row, vss._VERSION_KEY: 4})
    assert svc._check_supabase_cache("AAPL") is None, (
        "a v4 row may be heuristic-only from a swallowed lookup failure; it must be rebuilt"
    )
    svc.supabase = _FakeSnapshotTable({**row, vss._VERSION_KEY: vss._SNAPSHOT_PAYLOAD_VERSION})
    assert svc._check_supabase_cache("AAPL") is not None, "the current version is served"


# ═══════════════════════════════════════════════════════════════════════════
# Wiring — the pass-2 settle is the one the collector runs
# ═══════════════════════════════════════════════════════════════════════════


def test_fetch_dependent_lands_sector_history_through_the_settle():
    src = inspect.getsource(C.TickerReportDataCollector._fetch_dependent)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "_settle_sector_history(out, sector_bench, ticker)" in code
    assert "out.sector_benchmark_history = sector_bench" not in code
