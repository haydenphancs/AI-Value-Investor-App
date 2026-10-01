"""Round-2 fixes to the four snapshot services (growth / health / profitability / valuation).

R4 / R7 / R16 — a degraded snapshot build was refused by the 24h `snapshot_cache` tier but
still SERVED from the 5-minute memory tier with no trace of its degradation
(`SnapshotItemResponse` carries no such field). The report collector froze that partial
card into ticker_data_cache and the paid report — e.g. a health check whose ratios leg
429'd scored the Financial Health vital from Altman Z plus a neutral default. Each service
now exposes ``get_<name>_snapshot_with_status(ticker) -> (snapshot, degraded)`` and reports
the status of the EXACT object it hands out: on the build, on a Tier-1 hit and on an
in-flight join; ``[]`` for a clean build or a Supabase hit; ``["status_unknown"]`` (fail
closed) for an object it has no record of.

R26 — `get_current_benchmark_values` answers a FAILED DB lookup with
`BenchmarkLookupFailed` (all-None, the same shape as "no peer rows"). Only the health check
consulted `lookup_failed`; profitability, valuation and the health snapshot's local fallback
scored on absolute heuristics and persisted that for 24h. They now mark ``benchmarks``.

R37 / R52 — the profitability card's `snapshot_cache` rows carried no payload version, so a
row computed before the 2026-09-30 fixes (the sign-flipped "+113.6% net margin, rated 5/5")
was served for up to 24h after the deploy. Rows are now stamped ``_schema_v: 2`` and a
missing / older version is a miss.

Every "refused" assertion has a negative control, so none can pass by refusing everything.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.integrations.fmp import FMPNotEntitledException
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed

_PROFILE = {"symbol": "AAPL", "sector": "Technology", "industry": "Consumer Electronics",
            "mktCap": 3.0e12, "price": 200.0}


# ── shared harness ────────────────────────────────────────────────────────────


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


class _PlainLookup:
    """No peer rows: a plain dict, i.e. a SUCCESSFUL lookup that found nothing."""

    def get_current_benchmark_values(self, industry, sector, metrics):
        return {m: None for m in metrics}


class _FailedLookup:
    """The shape `sector_benchmark_lookup` returns when its DB call failed."""

    def get_current_benchmark_values(self, industry, sector, metrics):
        return BenchmarkLookupFailed({m: None for m in metrics})


class _RaisingLookup:
    def get_current_benchmark_values(self, industry, sector, metrics):
        raise RuntimeError("supabase down")


def _bare(cls, **attrs):
    svc = cls.__new__(cls)
    svc.supabase = None
    for key, value in attrs.items():
        setattr(svc, key, value)
    return svc


def _spy_persist(monkeypatch, svc) -> List[str]:
    """Run the fire-and-forget 24h-tier write inline so the assertion cannot race it."""
    persisted: List[str] = []

    def _record(ticker, *rest):
        persisted.append(ticker)

    monkeypatch.setattr(svc, "_upsert_supabase_cache", _record)
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


def _snapshot(category: str = "Growth", value: str = "+12.0%") -> SnapshotItemResponse:
    return SnapshotItemResponse(
        category=category, rating=4, full_report_available=True, weighted_score=4.0,
        metrics=[SnapshotMetricResponse(name="Metric", value=value, metric_key="m", score=4)],
    )


# name, module, class, public getter, cache-key prefix, category
_SERVICES = [
    ("growth", "app.services.growth_snapshot_service", "GrowthSnapshotService",
     "get_growth_snapshot", "growth_snapshot:", "Growth"),
    ("health", "app.services.health_snapshot_service", "HealthSnapshotService",
     "get_health_snapshot", "health_snapshot:", "Financial Health"),
    ("profitability", "app.services.profitability_snapshot_service",
     "ProfitabilitySnapshotService", "get_profitability_snapshot", "prof_snapshot:",
     "Profitability"),
    ("valuation", "app.services.valuation_snapshot_service", "ValuationSnapshotService",
     "get_valuation_snapshot", "val_snapshot:", "Price"),
]


def _reset(mod) -> None:
    mod._cache.clear()
    mod._inflight.clear()
    mod._degraded_by_key.clear()


@pytest.fixture(params=_SERVICES, ids=[s[0] for s in _SERVICES])
def snap_service(request, monkeypatch):
    name, modname, clsname, getter, prefix, category = request.param
    mod = importlib.import_module(modname)
    if name == "valuation":
        monkeypatch.setattr(mod.settings, "DCF_ENABLED", False)
        monkeypatch.setattr(mod.settings, "DCF_SHADOW", False)
    _reset(mod)
    svc = _bare(getattr(mod, clsname), fmp=None)
    tier2_calls: List[str] = []

    def _no_tier2(ticker):
        tier2_calls.append(ticker)
        return None

    monkeypatch.setattr(svc, "_check_supabase_cache", _no_tier2)
    yield SimpleNamespace(
        name=name, mod=mod, svc=svc, prefix=prefix, category=category,
        get=getattr(svc, getter),
        with_status=getattr(svc, f"{getter}_with_status"),
        tier2_calls=tier2_calls,
    )
    _reset(mod)


def _use_compute(monkeypatch, s, answers):
    """Replace the build with scripted `(snapshot, degraded)` answers; count the calls."""
    calls: List[str] = []
    answers = list(answers)

    async def _compute(ticker):
        calls.append(ticker)
        snapshot, degraded = answers.pop(0)
        return snapshot, list(degraded)

    monkeypatch.setattr(s.svc, "_compute_with_status", _compute)
    return calls


# ── R4 / R7 / R16: the status API, generically across the four services ──────


@pytest.mark.asyncio
async def test_a_degraded_build_reports_its_status_on_the_build_and_on_a_tier1_hit(
    monkeypatch, snap_service,
):
    s = snap_service
    calls = _use_compute(monkeypatch, s, [(_snapshot(s.category), ["leg_a", "leg_b"])])
    persisted = _spy_persist(monkeypatch, s.svc)

    snap, degraded = await s.with_status("AAPL")
    assert degraded == ["leg_a", "leg_b"]
    assert persisted == [], "a degraded build must not reach the 24h tier"

    # Tier-1 hit inside the 5-min TTL: same object, SAME status — the report path.
    snap2, degraded2 = await s.with_status("AAPL")
    assert calls == ["AAPL"], "the second call must be a Tier-1 hit, not a rebuild"
    assert snap2 is snap
    assert degraded2 == ["leg_a", "leg_b"], (
        f"{s.name}: a Tier-1 hit on a degraded build reported {degraded2!r}; the "
        "collector would freeze the partial card into ticker_data_cache"
    )


@pytest.mark.asyncio
async def test_a_clean_build_reports_empty_and_is_persisted(monkeypatch, snap_service):
    s = snap_service
    _use_compute(monkeypatch, s, [(_snapshot(s.category), [])])
    persisted = _spy_persist(monkeypatch, s.svc)

    _, degraded = await s.with_status("AAPL")
    assert degraded == []
    assert persisted == ["AAPL"], "negative control: a clean build IS persisted"
    assert (await s.with_status("AAPL"))[1] == [], "Tier-1 hit on a clean build"


@pytest.mark.asyncio
async def test_an_in_flight_joiner_reports_the_leaders_status(
    monkeypatch, snap_service, caplog,
):
    s = snap_service
    caplog.set_level(logging.INFO, logger=s.mod.__name__)
    release = asyncio.Event()
    calls: List[str] = []
    built = _snapshot(s.category)

    async def _slow_compute(ticker):
        calls.append(ticker)
        await release.wait()
        return built, ["leg_slow"]

    monkeypatch.setattr(s.svc, "_compute_with_status", _slow_compute)
    _spy_persist(monkeypatch, s.svc)

    leader = asyncio.ensure_future(s.with_status("AAPL"))
    for _ in range(200):
        if f"{s.prefix}AAPL" in s.mod._inflight:
            break
        await asyncio.sleep(0.005)
    assert f"{s.prefix}AAPL" in s.mod._inflight, "the leader never registered"

    joiner = asyncio.ensure_future(s.with_status("AAPL"))
    for _ in range(200):          # the joiner's Tier-2 read, then its join
        if len(s.tier2_calls) >= 2:
            break
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)
    release.set()

    (lsnap, ldeg), (jsnap, jdeg) = await asyncio.gather(leader, joiner)
    assert calls == ["AAPL"], "the second caller rebuilt instead of joining"
    assert "in-flight JOIN" in caplog.text
    assert jsnap is lsnap
    assert ldeg == jdeg == ["leg_slow"], (
        f"{s.name}: leader reported {ldeg!r}, joiner {jdeg!r}"
    )


@pytest.mark.asyncio
async def test_a_clean_rebuild_after_expiry_clears_the_degraded_status(
    monkeypatch, snap_service,
):
    s = snap_service
    _use_compute(monkeypatch, s, [(_snapshot(s.category), ["leg_a"]),
                                  (_snapshot(s.category), [])])
    _spy_persist(monkeypatch, s.svc)

    assert (await s.with_status("AAPL"))[1] == ["leg_a"]
    s.mod._cache.clear()                                   # the 5-min memory tier expires
    assert (await s.with_status("AAPL"))[1] == []
    assert (await s.with_status("AAPL"))[1] == [], "a stale degraded memo leaked into a hit"


@pytest.mark.asyncio
async def test_a_supabase_hit_is_clean_even_over_a_stale_memo(monkeypatch, snap_service):
    s = snap_service
    row = _snapshot(s.category)
    monkeypatch.setattr(s.svc, "_check_supabase_cache", lambda ticker: row)
    calls = _use_compute(monkeypatch, s, [])
    # A memo left behind for this key by an earlier (degraded) build of another object.
    s.mod._degraded_by_key[f"{s.prefix}AAPL"] = (_snapshot(s.category), ["stale"])

    snap, degraded = await s.with_status("AAPL")
    assert calls == [], "a Supabase hit must not rebuild"
    assert degraded == []
    assert (await s.with_status("AAPL"))[1] == [], "Tier-1 hit of a Supabase row"


@pytest.mark.asyncio
async def test_an_object_with_no_recorded_status_fails_closed(monkeypatch, snap_service):
    s = snap_service
    calls = _use_compute(monkeypatch, s, [])
    # Seeded straight into Tier 1, bypassing every path that notes a status.
    s.mod._cache_set(f"{s.prefix}AAPL", _snapshot(s.category))

    _, degraded = await s.with_status("AAPL")
    assert calls == []
    assert degraded == ["status_unknown"], (
        "a value of unknown provenance must never read as clean"
    )

    # The memo describes ANOTHER object for this key: still unknown, never borrowed.
    s.mod._degraded_by_key[f"{s.prefix}AAPL"] = (_snapshot(s.category), [])
    assert (await s.with_status("AAPL"))[1] == ["status_unknown"]


@pytest.mark.asyncio
async def test_the_public_getter_is_unchanged_and_tickers_normalise(
    monkeypatch, snap_service,
):
    s = snap_service
    _use_compute(monkeypatch, s, [(_snapshot(s.category), ["leg_a"])])
    _spy_persist(monkeypatch, s.svc)

    public = await s.get(" aapl ")
    assert isinstance(public, SnapshotItemResponse)
    snap, degraded = await s.with_status("aapl")
    assert snap is public, "both accessors must serve the same Tier-1 object"
    assert degraded == ["leg_a"], "a lower-case ticker must find the upper-case key"

    with pytest.raises(ValueError):
        await s.with_status("NOT A TICKER")


@pytest.mark.asyncio
async def test_the_memo_is_bounded(snap_service):
    s = snap_service
    cap = s.mod._DEGRADED_MAX_ENTRIES
    assert cap >= s.mod._CACHE_MAX_ENTRIES, "the memo must not evict before Tier 1 does"
    for i in range(cap + 50):
        s.mod._note_degraded(f"k{i}", object(), [])
    assert len(s.mod._degraded_by_key) <= cap
    assert f"k{cap + 49}" in s.mod._degraded_by_key and "k0" not in s.mod._degraded_by_key


# ── per-service status, through each service's REAL build ────────────────────


def _hc_metric(kind: str, value: float, status: str = "positive") -> HealthCheckMetricSchema:
    return HealthCheckMetricSchema(type=kind, value=value, comparison_value=1.0,
                                   gauge_position=0.5, status=status, insight_text="ok")


def _health_check(metrics, degraded=None) -> HealthCheckResponse:
    return HealthCheckResponse(symbol="AAPL", overall_rating="good",
                               passed_count=len(metrics), total_count=len(metrics),
                               metrics=metrics, degraded=list(degraded or []))


def _health_service(monkeypatch, health, fmp_answers=None, lookup=None):
    from app.services import health_check_service
    from app.services import health_snapshot_service as hs

    calls: List[str] = []

    class _HealthCheck:
        async def get_health_check(self, ticker):
            calls.append(ticker)
            if isinstance(health, BaseException):
                raise health
            return health

    monkeypatch.setattr(health_check_service, "get_health_check_service", lambda: _HealthCheck())
    monkeypatch.setattr(hs, "get_sector_benchmark_lookup", lambda: lookup or _PlainLookup())
    _reset(hs)
    svc = _bare(hs.HealthSnapshotService, fmp=_FakeFMP(**(fmp_answers or {})))
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    return hs, svc, calls


@pytest.mark.asyncio
async def test_r16_health_check_missing_its_ratios_leg_is_reported_on_every_path(monkeypatch):
    """The exact R16 path: /ratios-ttm 429s, the health check serves ROE + Altman Z only
    (degraded=['ratios']); the snapshot's weighted score is then 0.4*z + 0.6*3 — a neutral
    default standing in for D/E, CR, IC and QR. It must be reported, not frozen."""
    health = _health_check([_hc_metric("roe", 0.30), _hc_metric("altman_z_score", 4.2)],
                           degraded=["ratios"])
    hs, svc, calls = _health_service(monkeypatch, health)
    persisted = _spy_persist(monkeypatch, svc)

    snap, degraded = await svc.get_health_snapshot_with_status("AAPL")
    assert degraded == ["health_check:ratios"]
    assert persisted == []
    _, again = await svc.get_health_snapshot_with_status("AAPL")
    assert calls == ["AAPL"], "second read must be the snapshot's own Tier-1 hit"
    assert again == ["health_check:ratios"]
    _reset(hs)


@pytest.mark.asyncio
async def test_health_complete_check_is_clean(monkeypatch):
    health = _health_check([_hc_metric("debt_to_equity", 0.4), _hc_metric("current_ratio", 1.8),
                            _hc_metric("altman_z_score", 4.2)])
    hs, svc, _ = _health_service(monkeypatch, health)
    persisted = _spy_persist(monkeypatch, svc)
    assert (await svc.get_health_snapshot_with_status("AAPL"))[1] == []
    assert persisted == ["AAPL"]
    _reset(hs)


_PP_ROW = SimpleNamespace(gross_margin=45.0, operating_margin=30.0, net_margin=25.0)
_KM = [{"returnOnEquityTTM": 0.35, "returnOnAssetsTTM": 0.12}]


def _prof_service(monkeypatch, pp, fmp_answers, lookup=None):
    from app.services import profit_power_service
    from app.services import profitability_snapshot_service as ps

    class _ProfitPower:
        async def get_profit_power(self, ticker):
            if isinstance(pp, BaseException):
                raise pp
            return pp

    monkeypatch.setattr(profit_power_service, "get_profit_power_service", lambda: _ProfitPower())
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup", lambda: lookup or _PlainLookup())
    _reset(ps)
    svc = _bare(ps.ProfitabilitySnapshotService, fmp=_FakeFMP(**fmp_answers))
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    return ps, svc


@pytest.mark.asyncio
async def test_profitability_over_a_degraded_profit_power_is_reported(monkeypatch):
    pp = SimpleNamespace(annual=[_PP_ROW], degraded=["annual_income"])
    ps, svc = _prof_service(monkeypatch, pp, {"get_key_metrics_ttm": _KM,
                                             "get_company_profile": dict(_PROFILE)})
    persisted = _spy_persist(monkeypatch, svc)
    assert (await svc.get_profitability_snapshot_with_status("AAPL"))[1] == ["profit_power"]
    assert (await svc.get_profitability_snapshot_with_status("AAPL"))[1] == ["profit_power"]
    assert persisted == []
    _reset(ps)


@pytest.mark.asyncio
async def test_profitability_all_absent_build_reports_no_values_and_stays_uncached(monkeypatch):
    """Every leg answered, nothing measured: the 3/5 is the neutral sentinel. It is served
    and never cached; its status must still say so (it feeds the Profitability vital)."""
    ps, svc = _prof_service(monkeypatch, SimpleNamespace(annual=[], degraded=[]),
                            {"get_key_metrics_ttm": [], "get_company_profile": dict(_PROFILE)})
    persisted = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_profitability_snapshot_with_status("AAPL")
    assert all(m.value == "—" for m in snap.metrics)
    assert degraded == ["no_values"]
    assert persisted == [] and "prof_snapshot:AAPL" not in ps._cache
    _reset(ps)


@pytest.mark.asyncio
async def test_growth_snapshot_reports_the_growth_services_degradation(monkeypatch):
    from app.services import growth_service as gmod
    from app.services import growth_snapshot_service as gs

    point = [GrowthDataPointSchema(period="2025", value=10.0, yoy_change_percent=12.0,
                                   sector_average_yoy=8.0)]
    growth = GrowthResponse(symbol="AAPL", eps_annual=point, eps_quarterly=[],
                            revenue_annual=point, revenue_quarterly=[],
                            operating_profit_annual=point, free_cash_flow_annual=[])

    class _Growth:
        async def get_growth_with_status(self, ticker):
            return growth, ["annual_cashflow"]

    monkeypatch.setattr(gmod, "get_growth_service", lambda: _Growth())
    _reset(gs)
    svc = _bare(gs.GrowthSnapshotService)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, svc)
    assert (await svc.get_growth_snapshot_with_status("AAPL"))[1] == ["annual_cashflow"]
    assert (await svc.get_growth_snapshot_with_status("AAPL"))[1] == ["annual_cashflow"]
    assert persisted == []
    _reset(gs)


@pytest.mark.asyncio
async def test_valuation_status_survives_the_serve_time_estimate_copy(monkeypatch):
    """While DCF_ENABLED the served object is a COPY carrying today's estimate. The status
    must still come from the cached build, not be lost (or read as unknown) on the copy."""
    from app.services import valuation_snapshot_service as vss

    monkeypatch.setattr(vss.settings, "DCF_ENABLED", True)

    async def _no_estimate(ticker):
        return None

    monkeypatch.setattr(vss, "caydex_estimate_or_none", _no_estimate)
    _reset(vss)
    svc = _bare(vss.ValuationSnapshotService, fmp=None)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    built = _snapshot("Price", "25.0x")

    async def _compute(ticker):
        return built, ["ratios_ttm"]

    monkeypatch.setattr(svc, "_compute_with_status", _compute)
    _spy_persist(monkeypatch, svc)

    snap, degraded = await svc.get_valuation_snapshot_with_status("AAPL")
    assert snap is not built and snap.dcf is None, "DCF_ENABLED serves a copy"
    assert degraded == ["ratios_ttm"]
    assert (await svc.get_valuation_snapshot_with_status("AAPL"))[1] == ["ratios_ttm"]
    public = await svc.get_valuation_snapshot("AAPL")
    assert public.model_dump() == snap.model_dump(), "the public getter serves the same card"
    _reset(vss)


# ── R26: a FAILED benchmark lookup is degradation, not "no peers" ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup,expected,should_persist", [
    (_FailedLookup(), ["benchmarks"], False),
    (_RaisingLookup(), ["benchmarks"], False),
    (_PlainLookup(), [], True),            # negative control: no peer rows is an answer
])
async def test_profitability_failed_lookup_is_degraded(
    monkeypatch, lookup, expected, should_persist,
):
    ps, svc = _prof_service(monkeypatch, SimpleNamespace(annual=[_PP_ROW], degraded=[]),
                            {"get_key_metrics_ttm": _KM, "get_company_profile": dict(_PROFILE)},
                            lookup=lookup)
    persisted = _spy_persist(monkeypatch, svc)
    public = await svc.get_profitability_snapshot("AAPL")
    assert any(m.value not in (None, "", "—") for m in public.metrics), "still SERVED"
    assert persisted == (["AAPL"] if should_persist else [])
    assert (await svc.get_profitability_snapshot_with_status("AAPL"))[1] == expected
    _reset(ps)


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup,expected,should_persist", [
    (_FailedLookup(), ["benchmarks"], False),
    (_RaisingLookup(), ["benchmarks"], False),
    (_PlainLookup(), [], True),
])
async def test_valuation_failed_lookup_is_degraded(
    monkeypatch, lookup, expected, should_persist,
):
    from app.services import valuation_snapshot_service as vss

    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(vss.settings, "DCF_SHADOW", False)
    monkeypatch.setattr(vss, "get_sector_benchmark_lookup", lambda: lookup)
    _reset(vss)
    fmp = _FakeFMP(
        get_company_profile=dict(_PROFILE),
        get_ratios_ttm=[{"priceToEarningsRatioTTM": 25.0, "priceToSalesRatioTTM": 7.0}],
        get_key_metrics_ttm=[{"enterpriseValueMultipleTTM": 20.0}],
        get_dcf=[{"symbol": "AAPL", "date": "2026-09-29", "dcf": 180.0}],
    )
    svc = _bare(vss.ValuationSnapshotService, fmp=fmp)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, svc)

    public = await svc.get_valuation_snapshot("AAPL")
    assert public.category == "Price", "still SERVED"
    assert persisted == (["AAPL"] if should_persist else [])
    assert (await svc.get_valuation_snapshot_with_status("AAPL"))[1] == expected
    _reset(vss)


_GOOD_BS = [{"totalDebt": 100.0, "totalStockholdersEquity": 400.0, "totalCurrentAssets": 300.0,
             "totalCurrentLiabilities": 150.0, "cashAndCashEquivalents": 80.0,
             "netReceivables": 40.0, "totalAssets": 1000.0, "totalLiabilities": 600.0,
             "retainedEarnings": 200.0}]
_GOOD_INC = [{"operatingIncome": 50.0, "interestExpense": 5.0, "revenue": 250.0}] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup,expected,should_persist", [
    (_FailedLookup(), ["benchmarks"], False),
    (_RaisingLookup(), ["benchmarks"], False),
    (_PlainLookup(), [], True),
])
async def test_health_fallback_failed_lookup_is_degraded(
    monkeypatch, lookup, expected, should_persist,
):
    """A permanent 402 on the health check is not degradation (the local fallback IS the
    answer) — but a fallback scored without its peers because the lookup failed is."""
    hs, svc, _ = _health_service(
        monkeypatch, FMPNotEntitledException("402"),
        {"get_balance_sheet": _GOOD_BS, "get_income_statement": _GOOD_INC,
         "get_company_profile": dict(_PROFILE)},
        lookup=lookup,
    )
    persisted = _spy_persist(monkeypatch, svc)
    public = await svc.get_health_snapshot("AAPL")
    assert public.category == "Financial Health"
    assert persisted == (["AAPL"] if should_persist else [])
    assert (await svc.get_health_snapshot_with_status("AAPL"))[1] == expected
    _reset(hs)


def test_a_failed_lookup_still_reads_like_an_empty_one():
    """Anti-vacuity: the fixtures above only distinguish the two by the marker."""
    from app.services.sector_benchmark_lookup import lookup_failed

    failed = _FailedLookup().get_current_benchmark_values("i", "s", ["roe"])
    plain = _PlainLookup().get_current_benchmark_values("i", "s", ["roe"])
    assert dict(failed) == dict(plain) == {"roe": None}
    assert lookup_failed(failed) and not lookup_failed(plain)


# ── R37 / R52: the profitability card's 24h rows carry a payload version ──────


class _FakeTable:
    def __init__(self, rows, sink):
        self._rows, self._sink = rows, sink

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, payload, **k):
        self._sink.append(payload)
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.upserts: List[Dict[str, Any]] = []

    def table(self, name):
        assert name == "snapshot_cache"
        return _FakeTable(self.rows, self.upserts)


def _prof_svc_with_rows(rows):
    from app.services import profitability_snapshot_service as ps

    svc = _bare(ps.ProfitabilitySnapshotService, fmp=None)
    svc.supabase = _FakeSupabase(rows)
    return ps, svc


def _row(response_json, age=timedelta(hours=1)):
    return {"response_json": response_json,
            "cached_at": (datetime.now(timezone.utc) - age).isoformat()}


def test_a_profitability_row_without_a_version_is_rebuilt():
    """A row written before the 2026-09-30 fixes (here: the sign-flipped +113.6% net margin
    rated 5/5) must not be served for the rest of its 24h."""
    stale = _snapshot("Profitability", "113.64%").model_dump()
    _, svc = _prof_svc_with_rows([_row(stale)])
    assert svc._check_supabase_cache("AAPL") is None


def test_a_profitability_row_with_an_older_version_is_rebuilt():
    from app.services import profitability_snapshot_service as ps

    old = {**_snapshot("Profitability").model_dump(),
           ps._VERSION_KEY: ps._SNAPSHOT_PAYLOAD_VERSION - 1}
    _, svc = _prof_svc_with_rows([_row(old)])
    assert svc._check_supabase_cache("AAPL") is None


def test_a_current_profitability_row_round_trips_without_the_version_key():
    from app.services import profitability_snapshot_service as ps

    card = _snapshot("Profitability", "25.00%")
    stored = {**card.model_dump(), ps._VERSION_KEY: ps._SNAPSHOT_PAYLOAD_VERSION}
    row = _row(stored)
    _, svc = _prof_svc_with_rows([row])
    got = svc._check_supabase_cache("AAPL")
    assert got is not None, "negative control: a current row IS served"
    assert got.model_dump() == card.model_dump()
    assert row["response_json"][ps._VERSION_KEY] == ps._SNAPSHOT_PAYLOAD_VERSION, (
        "the SDK's row must not be mutated"
    )


def test_a_non_object_profitability_row_is_rebuilt():
    _, svc = _prof_svc_with_rows([_row(["not", "an", "object"])])
    assert svc._check_supabase_cache("AAPL") is None
    _, svc = _prof_svc_with_rows([_row(None)])
    assert svc._check_supabase_cache("AAPL") is None


def test_a_fresh_profitability_row_still_expires_at_24h():
    from app.services import profitability_snapshot_service as ps

    stored = {**_snapshot("Profitability").model_dump(),
              ps._VERSION_KEY: ps._SNAPSHOT_PAYLOAD_VERSION}
    _, svc = _prof_svc_with_rows([_row(stored, age=timedelta(hours=25))])
    assert svc._check_supabase_cache("AAPL") is None


def test_the_profitability_upsert_stamps_the_version():
    from app.services import profitability_snapshot_service as ps

    _, svc = _prof_svc_with_rows([])
    card = _snapshot("Profitability", "25.00%")
    svc._upsert_supabase_cache("AAPL", card)
    (payload,) = svc.supabase.upserts
    assert payload["category"] == "Profitability"
    assert payload["response_json"][ps._VERSION_KEY] == ps._SNAPSHOT_PAYLOAD_VERSION == 2

    # What it writes, it reads back.
    _, reader = _prof_svc_with_rows([_row(payload["response_json"])])
    assert reader._check_supabase_cache("AAPL").model_dump() == card.model_dump()
