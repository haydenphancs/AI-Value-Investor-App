"""The synchronous sector-benchmark lookup must never run on the event loop.

`SectorBenchmarkLookup` drives the SYNC supabase-py client: a cold key costs one or two
paginated PostgREST reads per peer group, and a transient blip adds a blocking
`time.sleep` retry. Railway runs ONE uvicorn worker, so a direct call from an `async def`
stalls every other in-flight request (including `/overview/core`) for the whole round
trip. Growth, profit power, the snapshot services, the health check and the report
collector all called it inline; they now dispatch it through `asyncio.to_thread`.

Asserted by THREAD IDENTITY, not by grepping for `to_thread`: a source scan passes on a
comment. Each stubbed lookup method records `threading.get_ident()`, and the test compares
it with the loop's own thread.

`stock_overview_service`'s degraded Price card (built when the valuation snapshot fails)
used to call `get_current_benchmark_values` inline from the synchronous
`_build_full_response`; `get_overview` now prefetches it on a worker thread and hands it in.
The deep-research door (`ResearchAgent.run`) now dispatches `assemble_report` the same way
the direct door does.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List

import pytest

from app.services import sector_benchmark_lookup as sbl

_PROFILE = {
    "symbol": "AAPL",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "mktCap": 3.0e12,
}


class _FakeFMP:
    """Every FMP method answers: the profile carries a sector (so the lookup runs), and
    every statement / ratio call returns an empty list."""

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            if name == "get_company_profile":
                return dict(_PROFILE)
            return []

        return _call


@pytest.fixture
def lookup_threads(monkeypatch) -> List[tuple]:
    """Replace every public lookup method with a thread recorder and install a
    Supabase-free singleton, so `get_sector_benchmark_lookup()` resolves to it from
    every module (they all bind the factory, which reads `sbl._lookup`)."""
    calls: List[tuple] = []

    def _recorder(name, answer):
        def _method(self, *args, **kwargs):
            calls.append((name, threading.get_ident()))
            return answer(*args, **kwargs)
        return _method

    def _empty_series(industry, sector, metrics, period_type):
        return {m: {} for m in metrics}

    def _empty_current(industry, sector, metrics):
        return {m: None for m in metrics}

    def _empty_sector(sector, metrics, period_type):
        return {m: {} for m in metrics}

    cls = sbl.SectorBenchmarkLookup
    monkeypatch.setattr(cls, "get_benchmarks", _recorder("get_benchmarks", _empty_series))
    monkeypatch.setattr(cls, "get_benchmark_values", _recorder("get_benchmark_values", _empty_series))
    monkeypatch.setattr(cls, "get_current_benchmarks", _recorder("get_current_benchmarks", _empty_current))
    monkeypatch.setattr(
        cls, "get_current_benchmark_values", _recorder("get_current_benchmark_values", _empty_current),
    )
    monkeypatch.setattr(cls, "get_sector_benchmarks", _recorder("get_sector_benchmarks", _empty_sector))
    monkeypatch.setattr(
        cls, "get_sector_benchmarks_with_n", _recorder("get_sector_benchmarks_with_n", _empty_sector),
    )
    instance = cls.__new__(cls)
    instance.supabase = None
    monkeypatch.setattr(sbl, "_lookup", instance)
    return calls


def _assert_off_loop(calls: List[tuple], loop_thread: int, expected: set) -> None:
    names = {name for name, _ in calls}
    assert expected <= names, f"the lookup was never reached ({names}); the test proves nothing"
    on_loop = [name for name, ident in calls if ident == loop_thread]
    assert on_loop == [], f"sync benchmark lookup ran ON the event loop: {on_loop}"


def _bare(cls, **attrs):
    svc = cls.__new__(cls)
    svc.supabase = None
    for key, value in attrs.items():
        setattr(svc, key, value)
    return svc


@pytest.mark.asyncio
async def test_growth_build_reads_benchmarks_off_the_loop(lookup_threads):
    from app.services.growth_service import GrowthService

    svc = _bare(GrowthService, fmp=_FakeFMP())
    await svc._build_growth("AAPL")
    _assert_off_loop(lookup_threads, threading.get_ident(), {"get_benchmarks"})
    assert sum(1 for n, _ in lookup_threads if n == "get_benchmarks") == 3


@pytest.mark.asyncio
async def test_profit_power_build_reads_benchmarks_off_the_loop(lookup_threads):
    from app.services.profit_power_service import ProfitPowerService

    svc = _bare(ProfitPowerService, fmp=_FakeFMP())
    await svc._build_profit_power("AAPL")
    _assert_off_loop(
        lookup_threads, threading.get_ident(), {"get_benchmark_values", "get_benchmarks"},
    )


@pytest.mark.asyncio
async def test_valuation_snapshot_reads_benchmarks_off_the_loop(lookup_threads):
    from app.services.valuation_snapshot_service import ValuationSnapshotService

    svc = _bare(ValuationSnapshotService, fmp=_FakeFMP())
    await svc._compute_with_status("AAPL")
    _assert_off_loop(lookup_threads, threading.get_ident(), {"get_current_benchmark_values"})


@pytest.mark.asyncio
async def test_profitability_snapshot_reads_benchmarks_off_the_loop(lookup_threads, monkeypatch):
    from app.services import profit_power_service
    from app.services.profitability_snapshot_service import ProfitabilitySnapshotService

    class _NoProfitPower:
        async def get_profit_power(self, ticker):
            return None

    # Function-local import in `_compute_with_status`: patch the SOURCE module.
    monkeypatch.setattr(profit_power_service, "get_profit_power_service", lambda: _NoProfitPower())
    svc = _bare(ProfitabilitySnapshotService, fmp=_FakeFMP())
    await svc._compute_with_status("AAPL")
    _assert_off_loop(lookup_threads, threading.get_ident(), {"get_current_benchmark_values"})


@pytest.mark.asyncio
async def test_health_check_build_reads_benchmarks_off_the_loop(lookup_threads):
    from app.services.health_check_service import HealthCheckService

    svc = _bare(HealthCheckService, fmp=_FakeFMP())
    await svc._build_health_check("AAPL")
    _assert_off_loop(lookup_threads, threading.get_ident(), {"get_current_benchmark_values"})


@pytest.mark.asyncio
async def test_health_snapshot_fallback_reads_benchmarks_off_the_loop(lookup_threads, monkeypatch):
    from app.services import health_check_service
    from app.services.health_snapshot_service import HealthSnapshotService

    class _FailingHealthCheck:
        async def get_health_check(self, ticker):
            raise RuntimeError("health check exploded")   # forces the local fallback

    monkeypatch.setattr(health_check_service, "get_health_check_service", lambda: _FailingHealthCheck())
    svc = _bare(HealthSnapshotService, fmp=_FakeFMP())
    await svc._compute_with_status("AAPL")
    _assert_off_loop(lookup_threads, threading.get_ident(), {"get_current_benchmark_values"})


@pytest.mark.asyncio
async def test_report_collection_builds_sections_off_the_loop(monkeypatch):
    """`_build_sections` is synchronous and reads `get_current_benchmarks` for the
    peer-group label; `_collect_fresh` must hand the whole build to a worker thread."""
    from app.services.agents import ticker_report_data_collector as trdc

    seen: Dict[str, Any] = {}
    coll = trdc.TickerReportDataCollector.__new__(trdc.TickerReportDataCollector)

    async def _fetch_all(out):
        out.profile = dict(_PROFILE)

    async def _noop(out):
        return None

    def _build_sections(out):
        seen["thread"] = threading.get_ident()

    monkeypatch.setattr(coll, "_fetch_all", _fetch_all)
    monkeypatch.setattr(coll, "_compute_metrics", lambda out: None)
    monkeypatch.setattr(coll, "_build_sections", _build_sections)
    for name in ("_precompute_price_catalyst", "_precompute_geopolitical", "_apply_intraday_chart"):
        monkeypatch.setattr(coll, name, _noop)

    out = await coll._collect_fresh("AAPL")
    assert out.profile["symbol"] == "AAPL"
    assert "thread" in seen, "_build_sections never ran; the test proves nothing"
    assert seen["thread"] != threading.get_ident(), (
        "_build_sections (and its sync benchmark lookup) ran ON the event loop"
    )


@pytest.mark.asyncio
async def test_direct_report_assembles_off_the_loop(monkeypatch):
    """`assemble_report` is synchronous and makes blocking Supabase reads (competitor
    sector benchmarks, moat sector medians, peer moats)."""
    from app.services import ticker_report_service as trs

    seen: Dict[str, Any] = {}

    class _Collector:
        async def collect(self, ticker, persona_key):
            return object()

        def assemble_report(self, out, shell):
            seen["thread"] = threading.get_ident()
            return {"ticker": "AAPL"}

    async def _async_noop(*args, **kwargs):
        return None

    async def _stage_a(out, persona, evidence):
        return {}

    monkeypatch.setattr(trs, "get_persona_config", lambda key: object())
    monkeypatch.setattr(trs, "build_financial_context", lambda out: "")
    monkeypatch.setattr(trs, "build_narrative_jobs", lambda persona, evidence, report: [])
    monkeypatch.setattr(trs, "run_narrative_jobs", _async_noop)
    monkeypatch.setattr(trs, "synthesize_core_thesis", _async_noop)
    monkeypatch.setattr(trs, "synthesize_critical_factors", _async_noop)
    monkeypatch.setattr(trs, "_degraded_reason", lambda shell: "stubbed")   # skip the cache write
    monkeypatch.setattr(trs, "upsert_cached_report", _async_noop)

    svc = trs.TickerReportService.__new__(trs.TickerReportService)
    svc.collector = _Collector()
    svc.gemini = None
    monkeypatch.setattr(svc, "_generate_stage_a", _stage_a)

    report = await svc._generate_uncontended("AAPL", "warren_buffett")
    assert report["ticker"] == "AAPL"
    assert "thread" in seen, "assemble_report never ran; the test proves nothing"
    assert seen["thread"] != threading.get_ident(), "assemble_report ran ON the event loop"


@pytest.mark.asyncio
async def test_harness_detects_an_on_loop_call(lookup_threads):
    """Positive control: a direct call IS recorded on the loop thread."""
    sbl.get_sector_benchmark_lookup().get_benchmarks("", "Technology", ["eps_yoy"], "annual")
    with pytest.raises(AssertionError, match="ON the event loop"):
        _assert_off_loop(lookup_threads, threading.get_ident(), {"get_benchmarks"})


# ── stock overview: the degraded Price card's benchmark read ───────────────────────


class _NoSnapshots:
    """The five snapshot services; the valuation one FAILS unless told otherwise, which
    is what sends `_build_snapshots` down the degraded Price-card path."""

    def __init__(self, valuation_fails: bool = True):
        self.valuation_fails = valuation_fails

    async def _none(self, ticker):
        return None

    async def get_valuation_snapshot(self, ticker):
        if self.valuation_fails:
            raise RuntimeError("valuation snapshot exploded")
        from app.schemas.stock_overview import SnapshotItemResponse
        return SnapshotItemResponse(category="Price", rating=3, metrics=[])

    get_profitability_snapshot = _none
    get_growth_snapshot = _none
    get_health_snapshot = _none
    get_ownership_snapshot = _none


def _overview_service(monkeypatch, snapshots: _NoSnapshots):
    """A real `StockOverviewService` with every upstream of `get_overview` stubbed.

    The snapshot factories and `get_short_interest` are imported INSIDE `get_overview`, so
    they resolve from their SOURCE modules on every call and are patched there; the module
    binding of `get_short_interest` is patched as well (`.claude/rules/testing.md`)."""
    from app.services import stock_overview_service as sos

    for module_name, factory in (
        ("profitability_snapshot_service", "get_profitability_snapshot_service"),
        ("growth_snapshot_service", "get_growth_snapshot_service"),
        ("valuation_snapshot_service", "get_valuation_snapshot_service"),
        ("health_snapshot_service", "get_health_snapshot_service"),
        ("ownership_snapshot_service", "get_ownership_snapshot_service"),
    ):
        module = __import__(f"app.services.{module_name}", fromlist=[factory])
        monkeypatch.setattr(module, factory, lambda: snapshots)

    async def _no_short_interest(ticker):
        return {}

    monkeypatch.setattr(sos, "get_short_interest", _no_short_interest)
    monkeypatch.setattr("app.integrations.finra_short_interest.get_short_interest",
                        _no_short_interest)

    class _NoMovers:
        async def get_sector_performance(self):
            return []

        async def get_industry_performance(self):
            return []

    monkeypatch.setattr(sos, "get_market_movers_service", lambda: _NoMovers())
    sos._cache.clear()
    svc = sos.StockOverviewService.__new__(sos.StockOverviewService)
    svc.fmp = _FakeFMP()
    svc.supabase = None

    async def _fundamentals(ticker):
        return {
            "profile": dict(_PROFILE),
            "fin_ratios": [{"priceToEarningsRatioTTM": 35.0}],
        }

    async def _volatile(ticker, chart_range, interval, extended_hours, **kwargs):
        return {"quote": {"price": 300.0}, "chart_data": []}

    async def _no_ohl(ticker, volatile=None, chart_data=None):
        return {}

    async def _no_related(ticker):
        return []

    monkeypatch.setattr(svc, "_get_fundamentals", _fundamentals)
    monkeypatch.setattr(svc, "_get_volatile", _volatile)
    monkeypatch.setattr(svc, "_get_session_ohl", _no_ohl)
    monkeypatch.setattr(svc, "_build_related_tickers", _no_related)
    monkeypatch.setattr(svc, "_upsert_company_profile_db", lambda ticker, payload: None)
    return svc


def _price_card(response):
    return next(s for s in response.snapshots if s.category == "Price")


@pytest.mark.asyncio
async def test_stock_overview_fallback_price_card_reads_benchmarks_off_the_loop(
    lookup_threads, monkeypatch,
):
    svc = _overview_service(monkeypatch, _NoSnapshots(valuation_fails=True))
    response = await svc.get_overview("AAPL", "1D", "5min", False)
    assert _price_card(response).metrics, "the degraded Price card was not built"
    _assert_off_loop(lookup_threads, threading.get_ident(), {"get_current_benchmark_values"})
    assert sum(1 for n, _ in lookup_threads if n == "get_current_benchmark_values") == 1


@pytest.mark.asyncio
async def test_stock_overview_reads_no_benchmark_when_the_valuation_snapshot_succeeds(
    lookup_threads, monkeypatch,
):
    """Negative control: the prefetch is for the DEGRADED card only — a healthy overview
    must not pay a sector-benchmark read it will not use."""
    svc = _overview_service(monkeypatch, _NoSnapshots(valuation_fails=False))
    await svc.get_overview("AAPL", "1D", "5min", False)
    assert [n for n, _ in lookup_threads if n == "get_current_benchmark_values"] == []


def test_the_synchronous_overview_builder_uses_the_prefetched_bench_and_reads_nothing(
    lookup_threads,
):
    """`_build_full_response` runs on the loop; handed a prefetched bench it must do no
    lookup of its own, and the card must be scored against exactly those medians."""
    from app.services import stock_overview_service as sos

    svc = sos.StockOverviewService.__new__(sos.StockOverviewService)
    response = svc._build_full_response(
        "AAPL",
        {"profile": dict(_PROFILE), "fin_ratios": [{"priceToEarningsRatioTTM": 35.0}]},
        {"quote": {"price": 300.0}, "chart_data": []},
        "1D", "5min", False,
        valuation_bench={"pe_ratio": 22.0},
    )
    pe_name = next(m.name for m in _price_card(response).metrics if m.metric_key == "pe")
    assert "sector avg 22" in pe_name
    assert lookup_threads == [], f"the sync builder did its own lookup: {lookup_threads}"


@pytest.mark.asyncio
async def test_stock_overview_bench_prefetch_never_raises(monkeypatch):
    from app.services import stock_overview_service as sos

    calls: List[tuple] = []

    class _Exploding:
        def get_current_benchmark_values(self, industry, sector, metrics):
            calls.append((industry, sector))
            raise RuntimeError("supabase down")

    monkeypatch.setattr(sos, "get_sector_benchmark_lookup", lambda: _Exploding())
    svc = sos.StockOverviewService.__new__(sos.StockOverviewService)
    assert await svc._fetch_valuation_bench("AAPL", dict(_PROFILE)) == {}
    assert calls == [("Consumer Electronics", "Technology")]
    # No sector (or no profile at all): nothing to look up, and nothing is read.
    for profile in ({}, {"sector": None}, None, "junk"):
        assert await svc._fetch_valuation_bench("AAPL", profile) == {}
    assert len(calls) == 1


# ── deep research: `assemble_report` ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deep_research_assembles_off_the_loop(monkeypatch):
    """`ResearchAgent.run` (the 20-credit door) called the synchronous `assemble_report` —
    blocking Supabase reads for competitor benchmarks, moat medians and peer moats — on the
    loop, while the direct door already dispatched it to a thread."""
    from types import SimpleNamespace

    from app.services.agents import research_agent as ra
    from app.services.report_degradation import REPORT_DEGRADED_KEY, _DEGRADED_KEY

    seen: Dict[str, Any] = {}

    class _Collector:
        async def collect(self, ticker, persona_key):
            return SimpleNamespace(ticker=ticker)

        def assemble_report(self, out, shell):
            seen["thread"] = threading.get_ident()
            seen["args"] = (out.ticker, shell)
            return {"ticker": out.ticker}

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(ra, "build_financial_context", lambda out: "")
    monkeypatch.setattr(ra, "build_narrative_jobs", lambda persona, evidence, report: [])
    monkeypatch.setattr(ra, "run_narrative_jobs", _noop)
    monkeypatch.setattr(ra, "synthesize_core_thesis", _noop)
    monkeypatch.setattr(ra, "synthesize_critical_factors", _noop)

    agent = ra.ResearchAgent.__new__(ra.ResearchAgent)
    agent.persona = SimpleNamespace(key="warren_buffett", agent_label="Value")
    agent.collector = _Collector()
    agent.gemini = None
    agent.fmp = None
    agent.research_findings = ""

    async def _research(out, evidence):
        return "findings"

    shell = {"score": 7, _DEGRADED_KEY: "stage_a_fallback"}

    async def _stage_a(out, evidence, research_text):
        return shell

    monkeypatch.setattr(agent, "_agentic_research", _research)
    monkeypatch.setattr(agent, "_generate_stage_a", _stage_a)

    report = await agent.run("aapl")
    assert "thread" in seen, "assemble_report never ran; the test proves nothing"
    assert seen["thread"] != threading.get_ident(), "assemble_report ran ON the event loop"
    assert seen["args"] == ("AAPL", shell), "the thread got different inputs"
    assert report["ticker"] == "AAPL"
    # The degradation marker must still cross the (now threaded) merge.
    assert report[REPORT_DEGRADED_KEY] == "stage_a_fallback"
