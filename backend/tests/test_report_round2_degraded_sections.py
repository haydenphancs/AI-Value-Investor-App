"""Round 2 (G2a) — a report that lost Financials data to a DEGRADED upstream build.

Owner decision (2026-09-30): such a report is DELIVERED and BILLED as today (no refund, no
`REPORT_DEGRADED_KEY`) but is NEVER written to a shared cache — ticker_data_cache,
ticker_report_cache, or the deep door's `_lookup_shared_cache` reuse.

Pins, each failing on the first-pass code:
  * R2  — Growth / Profit Power keep their healthy legs (a quarterly-income 429 no longer
          drops the annual chart; a profile failure only strips the peer overlay).
  * R3/R5 — `out.degraded_sections` rides onto the assembled report as
          `_degraded_sections`; both doors skip `upsert_cached_report`, and
          `_lookup_shared_cache` refuses such a row.
  * R4/R7/R16 (collector half) — the snapshot cards are fetched WITH their build status
          and a degraded one is refused (None + `snap_<name>:<reasons>`).

Hermetic: stubs only, no FMP / Supabase / Gemini.
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.ticker_report_data_collector import (
    DEGRADED_SECTIONS_KEY,
    CollectedTickerData,
    _refuse_degraded_financials,
    _settle_pass1_result,
    _settle_snapshot_result,
    report_degraded_sections,
)


def _gp(period, value, sector=None):
    return GrowthDataPointSchema(
        period=period, value=value, yoy_change_percent=5.0,
        sector_average_yoy=sector, sector_average_qoq=sector,
    )


def _growth(degraded, *, quarterly=True):
    q = [_gp("Q1 '26", 1.5, 4.0)] if quarterly else []
    return GrowthResponse(
        symbol="AAPL",
        eps_annual=[_gp("2025", 6.1, 8.0)],
        eps_quarterly=list(q),
        revenue_annual=[_gp("2025", 4.0e11, 6.0)],
        revenue_quarterly=list(q),
        net_income_annual=[_gp("2025", 9.0e10, 7.0)],
        net_income_quarterly=list(q),
        operating_profit_annual=[_gp("2025", 1.1e11, 7.0)],
        operating_profit_quarterly=list(q),
        free_cash_flow_annual=[_gp("2025", 1.0e11, 9.0)],
        free_cash_flow_quarterly=list(q),
        degraded=list(degraded),
        peer_group_levels={
            "eps_annual": "industry", "eps_quarterly": "industry",
            "revenue_quarterly": "sector", "fcf_quarterly": "industry",
        },
    )


def _out(**attrs):
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    for k, v in attrs.items():
        setattr(out, k, v)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# R2 — narrow, don't drop
# ═══════════════════════════════════════════════════════════════════════════


def test_quarterly_income_failure_keeps_the_annual_chart_and_the_fcf_quarterly_series():
    out = _out(growth_chart=_growth(["quarterly_income"]))
    _refuse_degraded_financials(out)
    g = out.growth_chart
    assert g is not None, "a quarterly-income 429 must not drop the ANNUAL chart"
    assert [p.value for p in g.eps_annual] == [6.1]
    assert [p.value for p in g.revenue_annual] == [4.0e11]
    for s in ("eps_quarterly", "revenue_quarterly", "net_income_quarterly",
              "operating_profit_quarterly"):
        assert getattr(g, s) == [], s
    # The cash-flow leg did not fail: its quarterly series stays.
    assert [p.value for p in g.free_cash_flow_quarterly] == [1.5]
    # Peer labels of emptied series go; the others stay.
    assert "eps_quarterly" not in g.peer_group_levels
    assert "revenue_quarterly" not in g.peer_group_levels
    assert g.peer_group_levels.get("fcf_quarterly") == "industry"
    assert g.peer_group_levels.get("eps_annual") == "industry"
    assert out.degraded_sections == ["growth_chart:quarterly_income"]


def test_profile_failure_strips_only_the_peer_overlay():
    out = _out(growth_chart=_growth(["profile"]))
    _refuse_degraded_financials(out)
    g = out.growth_chart
    assert g is not None
    assert [p.value for p in g.eps_annual] == [6.1]
    assert [p.value for p in g.eps_quarterly] == [1.5]
    every_point = [p for s in C._GROWTH_SERIES for p in getattr(g, s)]
    assert every_point and all(
        p.sector_average_yoy is None and p.sector_average_qoq is None for p in every_point
    )
    assert g.peer_group_levels == {}
    assert out.degraded_sections == ["growth_chart:profile"]


def test_unknown_growth_reason_fails_closed_and_all_legs_failed_drops_the_section():
    out = _out(growth_chart=_growth(["status_unknown"]))
    _refuse_degraded_financials(out)
    assert out.growth_chart is None
    assert out.degraded_sections == ["growth_chart:status_unknown"]

    out = _out(growth_chart=_growth(
        ["annual_income", "quarterly_income", "annual_cashflow", "quarterly_cashflow"]
    ))
    _refuse_degraded_financials(out)
    assert out.growth_chart is None, "nothing measured is left — the section is unavailable"


def _pp_point(period, *, peer=12.0):
    return ProfitPowerDataPointSchema(
        period=period, gross_margin=45.0, operating_margin=30.0, fcf_margin=25.0,
        net_margin=24.0, sector_average_net_margin=peer, sector_average_gross_margin=peer,
        sector_average_operating_margin=peer, sector_average_fcf_margin=peer,
    )


def _pp(degraded):
    return ProfitPowerResponse(
        symbol="AAPL", annual=[_pp_point("2025")], quarterly=[_pp_point("Q1 '26")],
        peer_group_level="industry", degraded=list(degraded),
    )


def test_profit_power_quarterly_income_failure_keeps_the_annual_margins():
    out = _out(profit_power=_pp(["quarterly_income"]))
    _refuse_degraded_financials(out)
    pp = out.profit_power
    assert pp is not None and pp.quarterly == []
    assert pp.annual[0].net_margin == 24.0 and pp.annual[0].sector_average_net_margin == 12.0
    assert pp.peer_group_level == "industry"
    assert out.degraded_sections == ["profit_power:quarterly_income"]


def test_profit_power_cashflow_failure_blanks_only_that_periods_fcf_margin():
    out = _out(profit_power=_pp(["annual_cashflow"]))
    _refuse_degraded_financials(out)
    a = out.profit_power.annual[0]
    assert a.fcf_margin is None and a.sector_average_fcf_margin is None
    assert a.net_margin == 24.0 and a.gross_margin == 45.0
    assert out.profit_power.quarterly[0].fcf_margin == 25.0


def test_profit_power_profile_failure_blanks_every_peer_field():
    out = _out(profit_power=_pp(["profile"]))
    _refuse_degraded_financials(out)
    pp = out.profit_power
    for p in pp.annual + pp.quarterly:
        for f in C._PROFIT_POWER_PEER_FIELDS:
            assert getattr(p, f) is None
        assert p.net_margin == 24.0
    assert pp.peer_group_level is None


def test_a_narrower_that_raises_drops_the_section_and_still_records_it(monkeypatch):
    def _boom(resp, blocking):
        raise RuntimeError("malformed build")

    monkeypatch.setitem(C._PARTIAL_SECTION_NARROWERS, "growth_chart", _boom)
    out = _out(growth_chart=_growth(["quarterly_income"]))
    _refuse_degraded_financials(out)
    assert out.growth_chart is None
    assert out.degraded_sections == ["growth_chart:quarterly_income"]


# ═══════════════════════════════════════════════════════════════════════════
# R4 / R7 / R16 — snapshot cards WITH their build status
# ═══════════════════════════════════════════════════════════════════════════


def _snap(category="Financial Health"):
    return SnapshotItemResponse(category=category, rating=4, metrics=[], weighted_score=3.8)


def test_a_degraded_health_snapshot_is_refused_and_recorded():
    out = _out()
    _settle_pass1_result(out, "snap_health", (_snap(), ["health_check:ratios"]), None, "AAPL")
    assert out.snap_health is None
    assert out.degraded_sections == ["snap_health:health_check:ratios"]


def test_a_clean_snapshot_is_kept():
    out = _out()
    snap = _snap("Profitability")
    _settle_pass1_result(out, "snap_profitability", (snap, []), None, "AAPL")
    assert out.snap_profitability is snap and out.degraded_sections == []


@pytest.mark.parametrize(
    "result, section",
    [
        (_snap(), "snap_growth:status_unknown"),            # a bare value: no status
        ((_snap(), "health_check:ratios"), "snap_growth:status_malformed"),
        ((_snap(), None), "snap_growth:status_malformed"),
    ],
)
def test_a_snapshot_without_a_readable_status_fails_closed(result, section):
    out = _out()
    _settle_snapshot_result(out, "snap_growth", result, "AAPL")
    assert out.snap_growth is None and out.degraded_sections == [section]


def test_a_failed_snapshot_fetch_is_none_without_a_section():
    """A raised fetch is the long-accepted failed-fetch path (None, cached as today)."""
    out = _out()
    _settle_pass1_result(out, "snap_valuation", RuntimeError("503"), None, "AAPL")
    assert out.snap_valuation is None and out.degraded_sections == []


@pytest.mark.asyncio
async def test_await_with_status_defers_the_lookup_into_the_coroutine():
    """Building the pass-1 task list must never raise: a service whose `_with_status` is
    missing (or whose factory fails) becomes an exception INSIDE gather."""
    class _Svc:
        pass

    coro = C._await_with_status(lambda: _Svc().get_health_snapshot_with_status("AAPL"))
    with pytest.raises(AttributeError):
        await coro


def _fetch_all_code() -> str:
    src = inspect.getsource(C.TickerReportDataCollector._fetch_all)
    return "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())


@pytest.mark.parametrize("name", ["profitability", "health", "growth", "valuation"])
def test_fetch_all_reads_every_snapshot_with_its_status(name):
    code = _fetch_all_code()
    assert f"get_{name}_snapshot_with_status(ticker)" in code
    assert not re.search(rf"get_{name}_snapshot\(ticker\)", code), (
        f"snap_{name} is fetched without its build status again"
    )


def test_the_snapshot_attrs_route_through_the_status_settle():
    assert C._SNAPSHOT_STATUS_ATTRS == {
        "snap_profitability", "snap_health", "snap_growth", "snap_valuation",
    }


@pytest.mark.asyncio
async def test_a_collection_with_a_refused_snapshot_is_not_stored(monkeypatch):
    from app.services import ticker_data_cache as tdc

    writes = []

    async def _get_cached(_t):
        return None

    async def _store(ticker, out):
        writes.append(ticker)

    monkeypatch.setattr(tdc, "get_cached_collection", _get_cached)
    monkeypatch.setattr(tdc, "store_collection", _store)
    out = _out()
    _settle_snapshot_result(out, "snap_health", (_snap(), ["health_check:benchmarks"]), "AAPL")

    async def _fetch():
        return out

    assert await tdc.get_or_collect("ZZR2", _fetch) is out
    assert writes == []


# ═══════════════════════════════════════════════════════════════════════════
# R3 / R5 — the report carries the loss; both doors keep it out of shared caches
# ═══════════════════════════════════════════════════════════════════════════


def test_report_degraded_sections_reads_the_key_and_tolerates_junk():
    assert report_degraded_sections({DEGRADED_SECTIONS_KEY: ["growth_chart:profile"]}) == [
        "growth_chart:profile"
    ]
    assert report_degraded_sections({DEGRADED_SECTIONS_KEY: "earnings:earnings_feed"}) == [
        "earnings:earnings_feed"
    ]
    assert report_degraded_sections({DEGRADED_SECTIONS_KEY: []}) == []
    assert report_degraded_sections({"quality_score": 50}) == []
    assert report_degraded_sections(None) == []


def test_the_key_is_internal_and_not_the_refund_marker():
    from app.schemas.ticker_report import TickerReportResponse
    from app.services.report_degradation import REPORT_DEGRADED_KEY, report_degraded_reason

    assert DEGRADED_SECTIONS_KEY.startswith("_")
    assert DEGRADED_SECTIONS_KEY not in TickerReportResponse.model_fields
    assert DEGRADED_SECTIONS_KEY != REPORT_DEGRADED_KEY
    # A partial report is DELIVERED: it must not read as degraded (= refund + no delivery).
    assert report_degraded_reason({DEGRADED_SECTIONS_KEY: ["growth_chart:profile"]}) is None


class _FakeCollector:
    def __init__(self, report):
        self._report = report

    async def collect(self, ticker, persona_key):
        return SimpleNamespace(ticker=ticker)

    def assemble_report(self, out, shell):
        return dict(self._report)


async def _run_direct(monkeypatch, report):
    from app.services import ticker_report_service as trs

    svc = object.__new__(trs.TickerReportService)
    svc.collector = _FakeCollector(report)
    svc.gemini = MagicMock()

    async def _stage_a(out, persona, evidence):
        return {"core_thesis": {"bull_case": ["x"]}}

    svc._generate_stage_a = _stage_a
    monkeypatch.setattr(trs, "get_persona_config", lambda key: MagicMock())
    monkeypatch.setattr(trs, "build_financial_context", lambda out: "evidence")
    monkeypatch.setattr(trs, "build_narrative_jobs", lambda *a, **k: [])
    monkeypatch.setattr(trs, "run_narrative_jobs", AsyncMock())
    monkeypatch.setattr(trs, "synthesize_core_thesis", AsyncMock())
    monkeypatch.setattr(trs, "synthesize_critical_factors", AsyncMock())
    upsert = AsyncMock()
    monkeypatch.setattr(trs, "upsert_cached_report", upsert)
    result = await svc._generate_uncontended("AAPL", "warren_buffett")
    return result, upsert


@pytest.mark.asyncio
async def test_direct_door_delivers_a_partial_report_but_never_caches_it(monkeypatch):
    report = {"symbol": "AAPL", DEGRADED_SECTIONS_KEY: ["growth_chart:quarterly_income"]}
    result, upsert = await _run_direct(monkeypatch, report)
    upsert.assert_not_awaited()
    assert result["symbol"] == "AAPL"
    assert "_degraded" not in result, "a partial report is delivered, not refunded"


@pytest.mark.asyncio
async def test_direct_door_still_caches_a_complete_report(monkeypatch):
    result, upsert = await _run_direct(monkeypatch, {"symbol": "AAPL"})
    upsert.assert_awaited_once()


def _deep_service(monkeypatch, blob):
    import app.services.research_service as rs
    from app.services.research_service import ResearchService

    q = MagicMock()
    for m in ("table", "update", "eq", "in_"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=[{"id": "rid"}])
    svc = object.__new__(ResearchService)
    svc.supabase = q
    monkeypatch.setattr(svc, "_update_status", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_lookup_shared_cache", AsyncMock(return_value=blob))
    monkeypatch.setattr(rs, "compute_quality_score", lambda persona, data: 70)
    upsert = AsyncMock()
    monkeypatch.setattr(rs, "upsert_cached_report", upsert)
    notify = AsyncMock(return_value=1)
    monkeypatch.setattr(
        "app.services.push_dispatch_service.get_push_dispatch_service",
        lambda: MagicMock(notify_users=notify),
    )
    return svc, upsert, q


_BLOB = {
    "company_name": "Apple Inc.",
    "executive_summary_text": "ok",
    "executive_summary_bullets": [],
    "core_thesis": {"bull_case": [], "bear_case": []},
    "macro_data": {},
    "critical_factors": [],
    "quality_score": 70,
}


@pytest.mark.asyncio
async def test_deep_door_completes_a_partial_report_but_never_seeds_the_shared_cache(monkeypatch):
    blob = {**_BLOB, DEGRADED_SECTIONS_KEY: ["earnings:earnings_feed"]}
    svc, upsert, q = _deep_service(monkeypatch, blob)
    assert await svc.generate_report("rid", "AAPL", "warren_buffett", "u1") is True
    upsert.assert_not_called()
    written = q.update.call_args_list[-1].args[0]
    assert written["status"] == "completed", "the buyer's own row is still delivered"


@pytest.mark.asyncio
async def test_deep_door_still_seeds_a_complete_report(monkeypatch):
    svc, upsert, _q = _deep_service(monkeypatch, dict(_BLOB))
    assert await svc.generate_report("rid", "AAPL", "warren_buffett", "u1") is True
    upsert.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost, reused", [(["snap_health:health_check:ratios"], False), ([], True)])
async def test_shared_cache_lookup_refuses_a_partial_row(monkeypatch, lost, reused):
    import app.services.research_service as rs
    from app.services.research_service import ResearchService

    blob = dict(_BLOB)
    if lost:
        blob[DEGRADED_SECTIONS_KEY] = lost
    q = MagicMock()
    for m in ("table", "select", "eq", "gte", "not_", "is_", "order", "limit"):
        getattr(q, m).return_value = q
    q.not_ = q
    q.execute.return_value = MagicMock(
        data=[{"ticker_report_data": blob, "completed_at": "2026-09-30T10:00:00+00:00"}]
    )
    svc = object.__new__(ResearchService)
    svc.supabase = q
    monkeypatch.setattr(rs, "report_dcf_source_matches", lambda b: True)
    got = await svc._lookup_shared_cache("AAPL", "warren_buffett")
    assert (got is not None) is reused
