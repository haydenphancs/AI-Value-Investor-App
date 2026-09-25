"""A snapshot build its upstream flagged as degraded must not reach the 24h tier.

GrowthService and HealthCheckService turn a failed FMP leg into an empty series / an empty
metric list, log "NOT persisted", and serve the result from their 5-minute memory tier
only. The Growth and Financial Health snapshot wrappers used to write that same build to
`snapshot_cache` for 24h: the missing metrics render as "—", every one scores the neutral
sentinel 3, and the card (plus the report's snap_growth / snap_health) showed a made-up
3/5 for a day. The profitability snapshot gated only the all-absent case, so a failed
profile or key-metrics leg (no sector context, ROE/ROA "—" scored 3) was pinned too.

Each snapshot now mirrors valuation_snapshot_service: serve the build, keep the 5-minute
memory tier, persist only when nothing upstream failed and at least one metric has a value.
Every case has a negative control that proves a complete build is still persisted, so the
"not persisted" assertions cannot pass by persisting nothing at all.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.integrations.fmp import FMPNotEntitledException, FMPRateLimitException
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse

_PROFILE = {"symbol": "AAPL", "sector": "Technology", "industry": "Consumer Electronics",
            "mktCap": 3.0e12}


# ── shared harness ────────────────────────────────────────────────────────────


class _StubLookup:
    def get_current_benchmark_values(self, industry, sector, metrics):
        return {m: None for m in metrics}


class _FakeFMP:
    """Per-method answers; a value that is an Exception is RAISED, like a failed leg."""

    def __init__(self, **answers: Any) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            answer = self._answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return _call


def _bare(cls, **attrs):
    svc = cls.__new__(cls)
    svc.supabase = None
    for key, value in attrs.items():
        setattr(svc, key, value)
    return svc


def _spy_persist(monkeypatch, svc, method: str = "_upsert_supabase_cache") -> List[str]:
    """Record the 24h-tier write deterministically.

    The services fire it with `loop.run_in_executor(None, ...)` and never await it, so a
    thread-side recorder could land after the assertion. Run exactly that call inline;
    every other executor job (asyncio.to_thread) still goes to the real pool."""
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


# ── Growth ────────────────────────────────────────────────────────────────────


def _series(yoy):
    if yoy is None:
        return []
    return [GrowthDataPointSchema(period="2025", value=10.0, yoy_change_percent=yoy,
                                  sector_average_yoy=8.0)]


def _growth(yoy=12.0) -> GrowthResponse:
    return GrowthResponse(
        symbol="AAPL", eps_annual=_series(yoy), eps_quarterly=[],
        revenue_annual=_series(yoy), revenue_quarterly=[],
        operating_profit_annual=_series(yoy), free_cash_flow_annual=_series(yoy),
    )


def _growth_snapshot(monkeypatch):
    from app.services import growth_snapshot_service as gs

    gs._cache.clear()
    gs._inflight.clear()
    svc = _bare(gs.GrowthSnapshotService)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    return gs, svc


def _use_growth_service(monkeypatch, growth_service):
    from app.services import growth_service as gmod

    # Function-local import inside `_compute_with_status`: patch the SOURCE module.
    monkeypatch.setattr(gmod, "get_growth_service", lambda: growth_service)


class _FakeGrowthService:
    def __init__(self, response: GrowthResponse, degraded: List[str]) -> None:
        self.response, self.degraded = response, degraded

    async def get_growth_with_status(self, ticker):
        return self.response, list(self.degraded)


@pytest.mark.asyncio
@pytest.mark.parametrize("response,degraded,should_persist", [
    # Every FMP leg failed: four em-dashes, rating 3 — the reported scenario.
    (_growth(None), ["annual_income", "annual_cashflow", "profile"], False),
    # One leg failed: real revenue/EPS, but FCF would be a pinned "—" scored 3.
    (_growth(12.0), ["annual_cashflow"], False),
    # Nothing failed, yet nothing measured (e.g. one filing, no YoY): sentinel rating.
    (_growth(None), [], False),
    # Negative control: a complete build IS persisted.
    (_growth(12.0), [], True),
    # A measured ZERO is a value, not an absence.
    (_growth(0.0), [], True),
])
async def test_growth_snapshot_persists_only_a_complete_build(
    monkeypatch, response, degraded, should_persist,
):
    gs, svc = _growth_snapshot(monkeypatch)
    _use_growth_service(monkeypatch, _FakeGrowthService(response, degraded))
    persisted = _spy_persist(monkeypatch, svc)

    result = await svc.get_growth_snapshot("AAPL")

    assert result.category == "Growth", "the build must still be SERVED"
    assert persisted == (["AAPL"] if should_persist else [])
    assert "growth_snapshot:AAPL" in gs._cache, "the 5-min memory tier must still absorb retries"
    gs._cache.clear()


def _real_growth_service(monkeypatch, build):
    from app.services import growth_service as gmod

    gmod._cache.clear()
    gmod._inflight.clear()
    gmod._degraded_by_key.clear()
    svc = _bare(gmod.GrowthService, fmp=None)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    monkeypatch.setattr(svc, "_next_earnings_date_safe", lambda ticker: None)
    monkeypatch.setattr(svc, "_build_growth", build)
    return gmod, svc


@pytest.mark.asyncio
async def test_growth_tier1_hit_still_reports_the_degraded_build(monkeypatch):
    """The Financials tab builds growth first (degraded, memory-only); the snapshot then
    reads it as a Tier-1 HIT. The hit must carry the degradation, or the snapshot pins it."""

    async def _degraded_build(ticker):
        return _growth(12.0), ["quarterly_income"]

    gmod, growth_svc = _real_growth_service(monkeypatch, _degraded_build)
    growth_persisted = _spy_persist(monkeypatch, growth_svc, "_upsert_supabase_cache_safe")
    await growth_svc.get_growth("AAPL")                      # the Financials tab
    assert growth_persisted == [], "GrowthService itself must not persist a degraded build"

    response, degraded = await growth_svc.get_growth_with_status("AAPL")   # Tier-1 hit
    assert degraded == ["quarterly_income"]
    assert response.symbol == "AAPL"

    gs, snap = _growth_snapshot(monkeypatch)
    _use_growth_service(monkeypatch, growth_svc)
    persisted = _spy_persist(monkeypatch, snap)
    await snap.get_growth_snapshot("AAPL")
    assert persisted == [], "a Tier-1 hit on a degraded growth build was persisted for 24h"
    gs._cache.clear()
    gmod._cache.clear()


@pytest.mark.asyncio
async def test_growth_joiner_receives_the_leaders_degradation(monkeypatch):
    release = asyncio.Event()

    async def _slow_degraded_build(ticker):
        await release.wait()
        return _growth(12.0), ["annual_cashflow"]

    gmod, growth_svc = _real_growth_service(monkeypatch, _slow_degraded_build)
    _spy_persist(monkeypatch, growth_svc, "_upsert_supabase_cache_safe")
    leader = asyncio.ensure_future(growth_svc.get_growth_with_status("AAPL"))
    for _ in range(3):
        await asyncio.sleep(0)
    assert "growth:AAPL" in gmod._inflight, "the leader never registered; no join happens"
    joiner = asyncio.ensure_future(growth_svc.get_growth_with_status("AAPL"))
    await asyncio.sleep(0)
    release.set()
    (_, leader_degraded), (_, joiner_degraded) = await asyncio.gather(leader, joiner)
    assert leader_degraded == joiner_degraded == ["annual_cashflow"]
    gmod._cache.clear()


@pytest.mark.asyncio
async def test_growth_clean_rebuild_and_tier2_hit_clear_the_memo(monkeypatch):
    builds = iter([(_growth(12.0), ["profile"]), (_growth(12.0), [])])

    async def _build(ticker):
        return next(builds)

    gmod, growth_svc = _real_growth_service(monkeypatch, _build)
    _spy_persist(monkeypatch, growth_svc, "_upsert_supabase_cache_safe")

    assert (await growth_svc.get_growth_with_status("AAPL"))[1] == ["profile"]
    gmod._cache.clear()                                     # memory tier expires
    assert (await growth_svc.get_growth_with_status("AAPL"))[1] == []
    assert (await growth_svc.get_growth_with_status("AAPL"))[1] == [], "stale memo on a hit"

    gmod._cache.clear()
    gmod._degraded_by_key["growth:AAPL"] = ["stale"]
    monkeypatch.setattr(growth_svc, "_check_supabase_cache", lambda ticker: _growth(12.0))
    assert (await growth_svc.get_growth_with_status("AAPL"))[1] == [], "Tier-2 rows are complete"
    assert (await growth_svc.get_growth_with_status("AAPL"))[1] == []
    gmod._cache.clear()


# ── Financial Health ─────────────────────────────────────────────────────────


def _hc_metric(kind: str, value: float) -> HealthCheckMetricSchema:
    return HealthCheckMetricSchema(type=kind, value=value, comparison_value=1.0,
                                   gauge_position=0.5, status="positive", insight_text="ok")


def _health_check(metrics) -> HealthCheckResponse:
    return HealthCheckResponse(symbol="AAPL", overall_rating="good",
                               passed_count=len(metrics), total_count=len(metrics),
                               metrics=metrics)


_FULL_HEALTH = [_hc_metric("debt_to_equity", 0.4), _hc_metric("current_ratio", 1.8),
                _hc_metric("altman_z_score", 4.2)]
_GOOD_BS = [{"totalDebt": 100.0, "totalStockholdersEquity": 400.0, "totalCurrentAssets": 300.0,
             "totalCurrentLiabilities": 150.0, "cashAndCashEquivalents": 80.0,
             "netReceivables": 40.0, "totalAssets": 1000.0, "totalLiabilities": 600.0,
             "retainedEarnings": 200.0}]
_GOOD_INC = [{"operatingIncome": 50.0, "interestExpense": 5.0, "revenue": 250.0}] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize("health,fmp_answers,should_persist", [
    # The health check returned no metrics (it refuses to persist that itself).
    (_health_check([]), {}, False),
    # The health check RAISED; the local fallback stands in over healthy legs.
    (RuntimeError("health fan-out failed"),
     {"get_balance_sheet": _GOOD_BS, "get_income_statement": _GOOD_INC,
      "get_company_profile": dict(_PROFILE)}, False),
    # The health check raised AND the fallback's own legs failed: "Financial Health —".
    (FMPRateLimitException("429"),
     {"get_balance_sheet": FMPRateLimitException("429"),
      "get_income_statement": FMPRateLimitException("429"),
      "get_company_profile": FMPRateLimitException("429")}, False),
    # Negative control: a complete health check IS persisted...
    (_health_check(_FULL_HEALTH), {}, True),
    # ...even when the fallback-only legs failed, since they change nothing on the card.
    (_health_check(_FULL_HEALTH), {"get_balance_sheet": FMPRateLimitException("429")}, True),
    # A permanent entitlement refusal is not a transient hole: the fallback is the answer.
    (FMPNotEntitledException("402"),
     {"get_balance_sheet": _GOOD_BS, "get_income_statement": _GOOD_INC,
      "get_company_profile": dict(_PROFILE)}, True),
])
async def test_health_snapshot_persists_only_a_complete_build(
    monkeypatch, health, fmp_answers, should_persist,
):
    from app.services import health_check_service
    from app.services import health_snapshot_service as hs

    class _HealthCheck:
        async def get_health_check(self, ticker):
            if isinstance(health, BaseException):
                raise health
            return health

    monkeypatch.setattr(health_check_service, "get_health_check_service", lambda: _HealthCheck())
    monkeypatch.setattr(hs, "get_sector_benchmark_lookup", lambda: _StubLookup())
    hs._cache.clear()
    hs._inflight.clear()
    svc = _bare(hs.HealthSnapshotService, fmp=_FakeFMP(**fmp_answers))
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, svc)

    result = await svc.get_health_snapshot("AAPL")

    assert result.category == "Financial Health", "the build must still be SERVED"
    assert persisted == (["AAPL"] if should_persist else [])
    assert "health_snapshot:AAPL" in hs._cache
    hs._cache.clear()


# ── Profitability ─────────────────────────────────────────────────────────────


_PP = SimpleNamespace(annual=[SimpleNamespace(gross_margin=45.0, operating_margin=30.0,
                                              net_margin=25.0)])
_KM = [{"returnOnEquityTTM": 0.35, "returnOnAssetsTTM": 0.12}]


@pytest.mark.asyncio
@pytest.mark.parametrize("pp,fmp_answers,should_persist", [
    # Failed profile leg: no sector context, every score absolute.
    (_PP, {"get_key_metrics_ttm": _KM, "get_company_profile": FMPRateLimitException("429")},
     False),
    # Failed key-metrics leg: ROE / ROA "—" scored as the neutral 3.
    (_PP, {"get_key_metrics_ttm": FMPRateLimitException("429"),
           "get_company_profile": dict(_PROFILE)}, False),
    # Failed profit_power leg: margins silently switch basis to TTM ratios.
    (RuntimeError("profit power exploded"),
     {"get_key_metrics_ttm": _KM, "get_company_profile": dict(_PROFILE),
      "get_ratios_ttm": [{"grossProfitMarginTTM": 0.45, "operatingProfitMarginTTM": 0.3,
                          "netProfitMarginTTM": 0.25}]}, False),
    # Negative control: nothing failed.
    (_PP, {"get_key_metrics_ttm": _KM, "get_company_profile": dict(_PROFILE)}, True),
    # A permanent entitlement refusal on the fallback-only ratios leg is not degradation.
    (_PP, {"get_key_metrics_ttm": _KM, "get_company_profile": dict(_PROFILE),
           "get_ratios_ttm": FMPNotEntitledException("402")}, True),
])
async def test_profitability_snapshot_persists_only_a_complete_build(
    monkeypatch, pp, fmp_answers, should_persist,
):
    from app.services import profit_power_service
    from app.services import profitability_snapshot_service as ps

    class _ProfitPower:
        async def get_profit_power(self, ticker):
            if isinstance(pp, BaseException):
                raise pp
            return pp

    monkeypatch.setattr(profit_power_service, "get_profit_power_service", lambda: _ProfitPower())
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup", lambda: _StubLookup())
    ps._cache.clear()
    ps._inflight.clear()
    svc = _bare(ps.ProfitabilitySnapshotService, fmp=_FakeFMP(**fmp_answers))
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, svc)

    result = await svc.get_profitability_snapshot("AAPL")

    assert result.category == "Profitability"
    assert any(m.value not in (None, "", "—") for m in result.metrics), (
        "every case here has real values; the all-absent gate is covered elsewhere"
    )
    assert persisted == (["AAPL"] if should_persist else [])
    assert "prof_snapshot:AAPL" in ps._cache, "a partial failure keeps the memory tier"
    ps._cache.clear()
