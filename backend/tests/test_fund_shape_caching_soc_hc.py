"""Fund-shaped empty builds are cacheable — Signal of Confidence + Health Check (2026-10-01).

A fund (VB) that reaches TickerDetailView has no statements and no TTM ratios: FMP answers
a genuine 200 [] on every statement leg. Signal of Confidence then has no data points and
Health Check scores nothing (`no_metrics`); both getters refused those builds as a possible
outage, so every view of a fund re-ran ~12 FMP calls (SoC every 60 s, Health Check every
5 min). The fix admits EXACTLY the fund's empty build, decided from a POSITIVE fund flag on
the FMP profile fetched in the SAME build (`asset_class.profile_is_fund`) plus raw-list legs
— never from the empty answer itself — and marks the row `security_kind: "fund"`; the
readers admit the empty shape only with that marker. Responses are unchanged (SoC [] / [],
Health Check `degraded == ["no_metrics"]`), so iOS and the report collector see nothing new.

Every outlier asserts the CORRECT degraded behaviour: a leg that raised, answered an error
DICT (coerced silently to [] / {} by the builders), answered junk rows, or a profile that
does not positively say fund keeps today's refusal (not written; SoC in memory 60 s).

Hermetic (testing.md): FMP, Supabase and the peer lookup are in-test fakes; no network.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.integrations.fmp import (
    FMPNotEntitledException,
    FMPRateLimitException,
)
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.schemas.signal_of_confidence import (
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services import health_check_service as hc
from app.services import signal_of_confidence_service as sos
from app.services.asset_class import profile_is_fund

_VB_PROFILE = {"symbol": "VB", "companyName": "Vanguard Small-Cap ETF", "isEtf": True,
               "isFund": False, "sector": "", "industry": "", "marketCap": 6.0e10}
_OPCO_PROFILE = {"symbol": "VB", "companyName": "Some Company", "isEtf": False,
                 "isFund": False, "sector": "", "industry": "", "marketCap": 6.0e10}
_ERROR_DICT = {"Error Message": "Limit Reach . Please upgrade your plan"}


# ── shared fakes ─────────────────────────────────────────────────────────────


class _FakeTable:
    def __init__(self, sb: "_FakeSupabase", name: str) -> None:
        self._sb, self._name = sb, name
        self._pending: Optional[Dict[str, Any]] = None

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, row, on_conflict=None):
        # JSON round-trip, exactly as the SDK encodes it: a non-JSON value would raise here.
        self._pending = json.loads(json.dumps(row))
        return self

    def execute(self):
        if self._pending is not None:
            row = self._pending
            self._sb.upserts.append((self._name, row))
            self._sb.rows[self._name] = [row]
            return SimpleNamespace(data=[row])
        return SimpleNamespace(data=copy.deepcopy(self._sb.rows.get(self._name, [])))


class _FakeSupabase:
    def __init__(self, rows: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self.rows: Dict[str, List[Dict[str, Any]]] = rows or {}
        self.upserts: List[tuple] = []

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self, name)


def _run_upserts_inline(monkeypatch) -> None:
    """The getters fire the write with `run_in_executor` and never await it: run exactly
    that call inline so no assertion can race it."""
    loop = asyncio.get_running_loop()
    real = loop.run_in_executor

    def _run_in_executor(executor, fn, *args):
        if getattr(fn, "__name__", None) == "_upsert_supabase_cache_safe":
            fn(*args)
            done = loop.create_future()
            done.set_result(None)
            return done
        return real(executor, fn, *args)

    monkeypatch.setattr(loop, "run_in_executor", _run_in_executor)


def _row(response_json: Dict[str, Any], next_date: Optional[str] = None) -> Dict[str, Any]:
    return {"response_json": response_json,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "next_earnings_date": next_date}


def _infos(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


def _warnings(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ── profile_is_fund: the only fund signal the gates trust ─────────────────────


@pytest.mark.parametrize("profile, expected", [
    ({"isEtf": True}, True),
    ({"isFund": True}, True),
    ({"isEtf": False, "isFund": True}, True),
    ({"isEtf": "true"}, False),
    ({"isEtf": 1}, False),
    ({"isEtf": False, "isFund": False}, False),
    ({"isEtf": None}, False),
    ({}, False),
    (None, False),
    ([], False),
    ([{"isEtf": True}], False),
    (FMPRateLimitException("429"), False),
])
def test_profile_is_fund_trusts_only_a_real_true_on_a_dict(profile, expected):
    assert profile_is_fund(profile) is expected


# ══════════════════════════════════════════════════════════════════════════════
# Signal of Confidence
# ══════════════════════════════════════════════════════════════════════════════

#: Twenty calendar quarter ends, oldest first (the BDC / mislabelled-company outlier).
_QEND = [f"{y}-{md}" for y in range(2021, 2026) for md in ("03-31", "06-30", "09-30", "12-31")]


def _q(d: str) -> str:
    return f"Q{(int(d[5:7]) - 1) // 3 + 1}"


class _SocFMP:
    """VB-shaped FMP by default: every statement leg answers 200 []. An answer that is an
    Exception is RAISED, like a failed leg. Every call is counted."""

    def __init__(self, *, profile: Any = None, income: Any = None, cashflow: Any = None,
                 ratios: Any = None, calendar: Any = None, cap: float = 6.0e10) -> None:
        self.answers = {
            "get_company_profile": dict(_VB_PROFILE) if profile is None else profile,
            "get_income_statement": [] if income is None else income,
            "get_cash_flow_statement": [] if cashflow is None else cashflow,
            "get_financial_ratios": [] if ratios is None else ratios,
            "get_earning_calendar_full": [] if calendar is None else calendar,
            "get_historical_market_cap": [],
            "get_stock_price_quote": {"symbol": "VB", "marketCap": cap, "price": 250.0},
        }
        self.calls: List[str] = []

    def __getattr__(self, name: str):
        if name not in self.__dict__.get("answers", {}):
            raise AttributeError(name)

        async def _call(*args, **kwargs):
            self.calls.append(name)
            answer = self.answers[name]
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


class _NoSpinOff:
    async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
        return []

    async def unclassified_adjustment_or_none(self, symbol, from_date=None, to_date=None, *,
                                              effective_from=None, effective_to=None):
        return False


def _soc(fmp: _SocFMP, supabase: Optional[_FakeSupabase] = None):
    from tests._price_fakes import PriceFromFMPFake

    svc = sos.SignalOfConfidenceService.__new__(sos.SignalOfConfidenceService)
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = _NoSpinOff()
    svc.supabase = supabase if supabase is not None else _FakeSupabase()
    return svc


def _soc_reset() -> None:
    sos._cache.clear()
    sos._inflight.clear()


def _soc_written(svc) -> List[Dict[str, Any]]:
    return [row for table, row in svc.supabase.upserts if table == "signal_of_confidence_cache"]


@pytest.mark.asyncio
async def test_soc_fund_empty_build_is_written_once_marked_and_served_from_tier2(
    monkeypatch, caplog,
):
    _soc_reset()
    _run_upserts_inline(monkeypatch)
    fmp = _SocFMP()
    svc = _soc(fmp)
    with caplog.at_level(logging.INFO, logger=sos.logger.name):
        resp = await svc.get_signal_of_confidence("VB")

    # The response is exactly what a fund served before: no points, nothing degraded.
    assert resp.data_points == [] and resp.degraded == []
    rows = _soc_written(svc)
    assert len(rows) == 1, "the fund's empty build must be persisted exactly once"
    body = rows[0]["response_json"]
    assert body["security_kind"] == "fund"
    assert body["payload_version"] == sos._PAYLOAD_VERSION
    assert body["data_points"] == [] and body["degraded"] == []
    # Normal memory TTL, not the 60 s degraded one.
    _ts, cached, ttl = sos._cache["signal_of_confidence:VB"]
    assert cached is resp and ttl == sos._CACHE_TTL
    assert "[fund-shape] ticker=VB step=signal_of_confidence_write" in _infos(caplog)

    # Memory gone: the Tier-2 row answers with ZERO FMP calls and the marker stripped.
    sos._cache.clear()
    fmp.calls.clear()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=sos.logger.name):
        again = await svc.get_signal_of_confidence("VB")
    assert fmp.calls == [], f"a fund's Tier-2 hit still called FMP: {fmp.calls}"
    assert again.data_points == [] and again.degraded == []
    assert "security_kind" not in again.model_dump()
    assert len(_soc_written(svc)) == 1, "a Tier-2 hit must not rewrite the row"
    assert "[fund-shape] ticker=VB step=signal_of_confidence_read" in _infos(caplog)
    _soc_reset()


_SOC_REFUSED = [
    pytest.param(dict(profile=dict(_OPCO_PROFILE)), id="operating-company-profile"),
    pytest.param(dict(profile={}), id="empty-profile"),
    pytest.param(dict(profile={**_VB_PROFILE, "isEtf": "true"}), id="string-true-flag"),
    pytest.param(dict(profile=[dict(_VB_PROFILE)]), id="list-wrapped-profile"),
    pytest.param(dict(profile=FMPRateLimitException("429")), id="profile-raised"),
    pytest.param(dict(income=FMPRateLimitException("429")), id="income-raised"),
    # The case the auditor missed: `_as_list` coerces an error DICT to [] with no reason.
    pytest.param(dict(income=dict(_ERROR_DICT)), id="income-answered-a-dict"),
    pytest.param(dict(cashflow=dict(_ERROR_DICT)), id="cashflow-answered-a-dict"),
    pytest.param(dict(cashflow=FMPRateLimitException("429")), id="cashflow-raised"),
    # Junk rows `_as_list` drops are malformed upstream data, not a fund's [].
    pytest.param(dict(income=["x", 3, None]), id="income-junk-rows"),
    pytest.param(dict(cashflow=["x", 3, None]), id="cashflow-junk-rows"),
    pytest.param(dict(ratios=FMPRateLimitException("429")), id="annual-ratios-raised"),
    # The ratios still feed the fund's dividend history; an error dict there is coerced to
    # [] with no reason, so it must not be pinned for 24h either.
    pytest.param(dict(ratios=dict(_ERROR_DICT)), id="annual-ratios-answered-a-dict"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", _SOC_REFUSED)
async def test_soc_an_empty_build_that_is_not_a_proven_fund_is_not_written(
    monkeypatch, caplog, overrides,
):
    _soc_reset()
    _run_upserts_inline(monkeypatch)
    svc = _soc(_SocFMP(**overrides))
    with caplog.at_level(logging.INFO, logger=sos.logger.name):
        resp = await svc.get_signal_of_confidence("VB")
    assert resp.data_points == []
    assert _soc_written(svc) == [], "an unproven empty build reached the 24h tier"
    _ts, _cached, ttl = sos._cache["signal_of_confidence:VB"]
    assert ttl == sos._DEGRADED_CACHE_TTL, "it must keep today's 60 s memory entry"
    assert not any(m.startswith("[fund-shape] ") for m in _infos(caplog)), (
        "nothing was admitted, so no admission may be logged"
    )
    assert any("NOT persisted" in m and "VB" in m for m in _warnings(caplog))
    _soc_reset()


@pytest.mark.asyncio
async def test_soc_fund_with_an_unread_calendar_keeps_normal_memory_but_is_not_written(
    monkeypatch, caplog,
):
    _soc_reset()
    _run_upserts_inline(monkeypatch)
    svc = _soc(_SocFMP(calendar=FMPRateLimitException("429")))
    with caplog.at_level(logging.INFO, logger=sos.logger.name):
        resp = await svc.get_signal_of_confidence("VB")
    assert resp.data_points == [] and resp.degraded == []
    assert _soc_written(svc) == [], "a row with no report-day bound must not be written"
    _ts, _cached, ttl = sos._cache["signal_of_confidence:VB"]
    assert ttl == sos._CACHE_TTL
    assert "[fund-shape] ticker=VB step=signal_of_confidence_memory" in _infos(caplog)
    _soc_reset()


def _bdc_fmp() -> _SocFMP:
    """isEtf=True on a symbol that HAS statements (a BDC or a mislabelled company)."""
    fmp = _SocFMP(
        profile={**_VB_PROFILE, "lastDividend": 0},
        income=[{"date": d, "period": _q(d), "fiscalYear": d[:4],
                 "weightedAverageShsOut": 4.3e9} for d in _QEND],
        cashflow=[{"date": d, "commonStockRepurchased": -1e9} for d in _QEND],
        ratios=[{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0}],
        cap=100e9,
    )
    fmp.answers["get_historical_market_cap"] = [{"date": d, "marketCap": 100e9} for d in _QEND]
    return fmp


@pytest.mark.asyncio
async def test_soc_a_fund_flag_on_a_symbol_with_statements_takes_the_normal_path(monkeypatch):
    _soc_reset()
    _run_upserts_inline(monkeypatch)
    svc = _soc(_bdc_fmp())
    build = await svc._build_signal_of_confidence_full("VB")
    assert build.response.data_points and build.degraded == []   # precondition: a clean build
    assert build.fund_shape is False

    resp = await svc.get_signal_of_confidence("VB")
    rows = _soc_written(svc)
    assert len(rows) == 1 and resp.data_points
    assert "security_kind" not in rows[0]["response_json"], (
        "only the empty fund build is marked"
    )
    _soc_reset()


@pytest.mark.asyncio
async def test_soc_builder_adapter_keeps_its_three_tuple():
    svc = _soc(_SocFMP())
    out = await svc._build_signal_of_confidence("VB")
    assert isinstance(out, tuple) and len(out) == 3
    resp, _next, degraded = out
    assert resp.data_points == [] and degraded == []
    full = await svc._build_signal_of_confidence_full("VB")
    assert full.fund_shape is True


# ── SoC reader ────────────────────────────────────────────────────────────────


def _soc_empty_body() -> Dict[str, Any]:
    """The body a real empty build dumps (`_build_summary`'s empty branch)."""
    svc = sos.SignalOfConfidenceService.__new__(sos.SignalOfConfidenceService)
    summary = svc._build_summary([], None)
    assert summary.share_count_change_known is False      # the empty-build shape
    resp = SignalOfConfidenceResponse(symbol="VB", data_points=[], summary=summary)
    return {**resp.model_dump(), "payload_version": sos._PAYLOAD_VERSION}


def _soc_reader(body: Dict[str, Any]):
    svc = sos.SignalOfConfidenceService.__new__(sos.SignalOfConfidenceService)
    svc.supabase = _FakeSupabase({"signal_of_confidence_cache": [_row(body)]})
    return svc


def test_soc_reader_refuses_an_unmarked_empty_build_row(caplog):
    with caplog.at_level(logging.WARNING, logger=sos.logger.name):
        assert _soc_reader(_soc_empty_body())._check_supabase_cache("VB") is None
    assert any("VB" in m and "EMPTY build" in m for m in _warnings(caplog)), (
        "the refusal must be logged with the ticker"
    )


def test_soc_reader_serves_a_marked_empty_build_without_the_marker(caplog):
    body = {**_soc_empty_body(), "security_kind": "fund"}
    with caplog.at_level(logging.INFO, logger=sos.logger.name):
        hit = _soc_reader(body)._check_supabase_cache("VB")
    assert isinstance(hit, SignalOfConfidenceResponse)
    assert hit.data_points == [] and hit.degraded == []
    assert "security_kind" not in hit.model_dump()
    assert "[fund-shape] ticker=VB step=signal_of_confidence_read" in _infos(caplog)


@pytest.mark.parametrize("extra", [
    pytest.param({"security_kind": "fund", "degraded": ["cash_flow"]}, id="marker+degraded"),
    pytest.param({"security_kind": "FUND"}, id="wrong-case-marker"),
    pytest.param({"security_kind": "etf"}, id="other-marker"),
    pytest.param({"security_kind": True}, id="bool-marker"),
    pytest.param({"security_kind": "fund", "payload_version": 9}, id="marker-on-stale-version"),
])
def test_soc_reader_refuses_a_bad_marker_or_a_degraded_marked_row(extra):
    assert _soc_reader({**_soc_empty_body(), **extra})._check_supabase_cache("VB") is None


def test_soc_reader_still_serves_a_row_with_data_points_and_no_marker():
    """Anti-vacuity: the marker gate must not refuse ordinary rows."""
    point = SignalOfConfidenceDataPointSchema(
        period="Q4 '25", dividend_yield=0.0, buyback_yield=4.0,
        dividend_amount=0.0, buyback_amount=1000.0, shares_outstanding=4.3e9,
    )
    resp = SignalOfConfidenceResponse(
        symbol="AAPL", data_points=[point],
        summary=SignalOfConfidenceSummarySchema(total_yield=4.0, buyback_yield=4.0,
                                                share_count_change_known=False),
    )
    body = {**resp.model_dump(), "payload_version": sos._PAYLOAD_VERSION}
    hit = _soc_reader(body)._check_supabase_cache("AAPL")
    assert hit is not None and len(hit.data_points) == 1


# ── SoC writer ────────────────────────────────────────────────────────────────


def test_soc_writer_marks_only_a_real_empty_build(caplog):
    svc = sos.SignalOfConfidenceService.__new__(sos.SignalOfConfidenceService)
    svc.supabase = _FakeSupabase()
    empty = SignalOfConfidenceResponse(**{k: v for k, v in _soc_empty_body().items()
                                          if k != "payload_version"})
    svc._upsert_supabase_cache_safe("VB", empty, None, True)
    assert svc.supabase.upserts[-1][1]["response_json"]["security_kind"] == "fund"

    # A fund verdict handed a build WITH points is written, but never marked.
    point = SignalOfConfidenceDataPointSchema(period="Q4 '25", dividend_yield=0.0,
                                              buyback_yield=1.0)
    full = SignalOfConfidenceResponse(symbol="VB", data_points=[point],
                                      summary=SignalOfConfidenceSummarySchema())
    with caplog.at_level(logging.WARNING, logger=sos.logger.name):
        svc._upsert_supabase_cache_safe("VB", full, None, True)
    assert "security_kind" not in svc.supabase.upserts[-1][1]["response_json"]
    assert any("fund-shape-refused" in m and "VB" in m for m in _warnings(caplog))

    # Round trip: an unmarked empty build that slipped past the getter is refused on read.
    svc._upsert_supabase_cache_safe("VB", empty, None)
    assert "security_kind" not in svc.supabase.upserts[-1][1]["response_json"]
    assert svc._check_supabase_cache("VB") is None


# ══════════════════════════════════════════════════════════════════════════════
# Health Check
# ══════════════════════════════════════════════════════════════════════════════

_RATIOS = {"debtToEquityRatioTTM": 0.5, "priceToEarningsRatioTTM": 25.0,
           "currentRatioTTM": 1.5, "interestCoverageRatioTTM": 20.0, "quickRatioTTM": 1.2}
_KEY_METRICS = {"returnOnEquityTTM": 0.30}
_BS = {"totalAssets": 500e9, "totalLiabilities": 250e9, "totalCurrentAssets": 150e9,
       "totalCurrentLiabilities": 100e9, "retainedEarnings": 100e9,
       "totalStockholdersEquity": 250e9}
_QUARTER = {"operatingIncome": 30e9, "interestExpense": 1e9, "revenue": 80e9,
            "netIncome": 25e9, "ebitda": 35e9}


class _HcFMP:
    """VB-shaped by default (every leg 200 []). An Exception answer is RAISED."""

    def __init__(self, **overrides: Any) -> None:
        self.answers: Dict[str, Any] = {
            "get_company_profile": dict(_VB_PROFILE),
            "get_ratios_ttm": [],
            "get_key_metrics_ttm": [],
            "get_earning_calendar_full": [],
            "get_balance_sheet": [],
            "get_income_statement": [],
        }
        self.answers.update(overrides)
        self.calls: List[str] = []

    def __getattr__(self, name: str):
        if name not in self.__dict__.get("answers", {}):
            raise AttributeError(name)

        async def _call(*args, **kwargs):
            self.calls.append(name)
            answer = self.answers[name]
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


class _Lookup:
    def __init__(self, error: Optional[BaseException] = None) -> None:
        self.error = error

    # The health check reads the rich cells (2026-10-07); the health / profitability
    # snapshots' local fallbacks still read the flat values.
    def get_current_benchmarks(self, industry, sector, metrics):
        return self.get_current_benchmark_values(industry, sector, metrics)

    def get_current_benchmark_values(self, industry, sector, metrics):
        if self.error is not None:
            raise self.error
        return {m: None for m in metrics}


def _hc(monkeypatch, fmp: _HcFMP, *, lookup: Optional[_Lookup] = None,
        supabase: Optional[_FakeSupabase] = None):
    # Module-level `from … import get_sector_benchmark_lookup`: patch the CALLER's binding.
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: lookup or _Lookup())
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.fmp = fmp
    svc.supabase = supabase if supabase is not None else _FakeSupabase()
    return svc


def _hc_written(svc) -> List[Dict[str, Any]]:
    return [row for table, row in svc.supabase.upserts if table == "health_check_cache"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legs", [
    pytest.param({}, id="every-leg-empty"),
    # Non-empty but all-null rows: still lists, still nothing to score.
    pytest.param({"get_ratios_ttm": [{k: None for k in _RATIOS}],
                  "get_key_metrics_ttm": [{"returnOnEquityTTM": None}],
                  "get_balance_sheet": [{"totalAssets": None}]}, id="all-null-rows"),
    pytest.param({"get_company_profile": {**_VB_PROFILE, "isEtf": False, "isFund": True}},
                 id="isFund-flag"),
])
async def test_hc_fund_no_metrics_build_is_written_marked_and_served_from_tier2(
    monkeypatch, caplog, legs,
):
    _run_upserts_inline(monkeypatch)
    fmp = _HcFMP(**legs)
    svc = _hc(monkeypatch, fmp)
    with caplog.at_level(logging.INFO, logger=hc.logger.name):
        resp = await svc.get_health_check("VB")

    # The served response is unchanged: still `no_metrics`.
    assert resp.degraded == ["no_metrics"] and resp.metrics == []
    rows = _hc_written(svc)
    assert len(rows) == 1
    body = rows[0]["response_json"]
    assert body["security_kind"] == "fund" and body["degraded"] == ["no_metrics"]
    assert body["payload_version"] == hc._HC_PAYLOAD_VERSION
    assert "[fund-shape] ticker=VB step=health_check_write" in _infos(caplog)

    hc._cache.clear()
    fmp.calls.clear()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=hc.logger.name):
        again = await svc.get_health_check("VB")
    assert fmp.calls == [], f"a fund's Tier-2 hit still called FMP: {fmp.calls}"
    assert again.degraded == ["no_metrics"] and again.metrics == []
    assert "security_kind" not in again.model_dump()
    assert len(_hc_written(svc)) == 1
    assert "[fund-shape] ticker=VB step=health_check_read" in _infos(caplog)
    hc._cache.clear()


_HC_REFUSED = [
    # The outlier the builder coerces silently: an error DICT becomes {} with no reason.
    pytest.param(dict(get_ratios_ttm=dict(_ERROR_DICT)), None, id="ratios-answered-a-dict"),
    pytest.param(dict(get_key_metrics_ttm=dict(_ERROR_DICT)), None, id="key-metrics-dict"),
    pytest.param(dict(get_balance_sheet=dict(_ERROR_DICT)), None, id="balance-sheet-dict"),
    pytest.param(dict(get_income_statement=dict(_ERROR_DICT)), None, id="income-dict"),
    pytest.param(dict(get_ratios_ttm=None), None, id="ratios-null-body"),
    pytest.param(dict(get_ratios_ttm=FMPRateLimitException("429")), None, id="ratios-raised"),
    # Not degradation (permanent), but a raised leg is never a fund's empty answer.
    pytest.param(dict(get_ratios_ttm=FMPNotEntitledException("402")), None,
                 id="ratios-not-entitled"),
    pytest.param(dict(get_company_profile=FMPRateLimitException("429")), None,
                 id="profile-raised"),
    pytest.param(dict(get_company_profile=dict(_OPCO_PROFILE)), None, id="operating-company"),
    pytest.param(dict(get_company_profile={**_VB_PROFILE, "isEtf": 1}), None, id="int-flag"),
    pytest.param(dict(get_company_profile=[dict(_VB_PROFILE)]), None, id="list-profile"),
    # `no_metrics` beside another reason (the peer lookup failed): not the fund shape.
    pytest.param(dict(get_company_profile={**_VB_PROFILE, "sector": "Financial Services"}),
                 RuntimeError("supabase 520"), id="benchmarks-failed"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("answers, lookup_error", _HC_REFUSED)
async def test_hc_a_no_metrics_build_that_is_not_a_proven_fund_is_not_written(
    monkeypatch, caplog, answers, lookup_error,
):
    _run_upserts_inline(monkeypatch)
    svc = _hc(monkeypatch, _HcFMP(**answers), lookup=_Lookup(lookup_error))
    with caplog.at_level(logging.INFO, logger=hc.logger.name):
        resp = await svc.get_health_check("VB")
    assert "no_metrics" in resp.degraded and resp.metrics == []
    assert _hc_written(svc) == [], "an unproven no_metrics build reached the 24h tier"
    assert not any(m.startswith("[fund-shape] ") for m in _infos(caplog))
    assert any("NOT persisted" in m and "VB" in m for m in _warnings(caplog))
    hc._cache.clear()


@pytest.mark.asyncio
async def test_hc_fund_with_an_unread_calendar_is_not_written(monkeypatch):
    _run_upserts_inline(monkeypatch)
    svc = _hc(monkeypatch, _HcFMP(get_earning_calendar_full=FMPRateLimitException("429")))
    resp = await svc.get_health_check("VB")
    assert resp.degraded == ["no_metrics"]
    assert _hc_written(svc) == []
    hc._cache.clear()


def _quarters() -> List[Dict[str, Any]]:
    return [dict(_QUARTER, date=d)
            for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")]


@pytest.mark.asyncio
async def test_hc_a_fund_flag_on_a_symbol_with_metrics_takes_the_normal_path(monkeypatch):
    _run_upserts_inline(monkeypatch)
    svc = _hc(monkeypatch, _HcFMP(
        get_ratios_ttm=[dict(_RATIOS)], get_key_metrics_ttm=[dict(_KEY_METRICS)],
        get_balance_sheet=[dict(_BS)], get_income_statement=_quarters(),
    ))
    build = await svc._build_health_check_full("VB")
    assert build.response.metrics and build.response.degraded == []   # precondition
    assert build.fund_shape is False

    resp = await svc.get_health_check("VB")
    rows = _hc_written(svc)
    assert len(rows) == 1 and resp.metrics
    assert "security_kind" not in rows[0]["response_json"]
    hc._cache.clear()


@pytest.mark.asyncio
async def test_hc_builder_adapter_keeps_its_two_tuple(monkeypatch):
    svc = _hc(monkeypatch, _HcFMP())
    out = await svc._build_health_check("VB")
    assert isinstance(out, tuple) and len(out) == 2
    assert out[0].degraded == ["no_metrics"]
    assert (await svc._build_health_check_full("VB")).fund_shape is True


# ── Health Check reader ───────────────────────────────────────────────────────


def _hc_body(**over) -> Dict[str, Any]:
    resp = HealthCheckResponse(symbol="VB", overall_rating="poor", passed_count=0,
                               total_count=0, metrics=[], degraded=["no_metrics"])
    return {**resp.model_dump(), "payload_version": hc._HC_PAYLOAD_VERSION, **over}


def _hc_reader(body: Dict[str, Any]):
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = _FakeSupabase({"health_check_cache": [_row(body)]})
    return svc


def test_hc_reader_refuses_an_unmarked_no_metrics_row(caplog):
    with caplog.at_level(logging.WARNING, logger=hc.logger.name):
        assert _hc_reader(_hc_body())._check_supabase_cache("VB") is None
    assert any("VB" in m and "no_metrics" in m for m in _warnings(caplog))


def test_hc_reader_serves_a_marked_fund_row_with_its_reason_intact(caplog):
    with caplog.at_level(logging.INFO, logger=hc.logger.name):
        hit = _hc_reader(_hc_body(security_kind="fund"))._check_supabase_cache("VB")
    assert isinstance(hit, HealthCheckResponse)
    assert hit.degraded == ["no_metrics"] and hit.metrics == []
    assert "security_kind" not in hit.model_dump()
    assert "[fund-shape] ticker=VB step=health_check_read" in _infos(caplog)


def _a_metric() -> Dict[str, Any]:
    return HealthCheckMetricSchema(
        type="debt_to_equity", value="0.50", sector_average="0.60",
        comparison_value=0.6, percent_difference=-16.7, gauge_position=0.4,
        status="positive", insight_text="x", highlighted_value="0.50",
        highlighted_label="x",
    ).model_dump()


@pytest.mark.parametrize("over", [
    pytest.param({"security_kind": "fund", "degraded": ["no_metrics", "ratios"]},
                 id="marker+extra-reason"),
    pytest.param({"security_kind": "fund", "degraded": ["ratios"]}, id="marker+other-reason"),
    pytest.param({"security_kind": "fund", "metrics": "METRIC"}, id="marker+metrics"),
    pytest.param({"security_kind": "Fund"}, id="wrong-case-marker"),
    pytest.param({"security_kind": None}, id="null-marker"),
    pytest.param({"security_kind": "fund", "payload_version": 3}, id="marker-on-stale-version"),
])
def test_hc_reader_refuses_any_other_degraded_or_marked_shape(over):
    if over.get("metrics") == "METRIC":
        over = {**over, "metrics": [_a_metric()]}
    assert _hc_reader(_hc_body(**over))._check_supabase_cache("VB") is None


def test_hc_reader_still_serves_a_clean_row_and_strips_a_stray_marker():
    clean = _hc_body(degraded=[], metrics=[_a_metric()], total_count=1, passed_count=1)
    assert _hc_reader(clean)._check_supabase_cache("VB") is not None
    hit = _hc_reader({**clean, "security_kind": "fund"})._check_supabase_cache("VB")
    assert hit is not None and "security_kind" not in hit.model_dump()


def test_hc_writer_marks_only_the_fund_shape(caplog):
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = _FakeSupabase()
    fund = HealthCheckResponse(**{k: v for k, v in _hc_body().items() if k != "payload_version"})
    svc._upsert_supabase_cache_safe("VB", fund, None, True)
    assert svc.supabase.upserts[-1][1]["response_json"]["security_kind"] == "fund"

    other = HealthCheckResponse(symbol="VB", overall_rating="poor", passed_count=0,
                                total_count=0, metrics=[], degraded=["ratios", "no_metrics"])
    with caplog.at_level(logging.WARNING, logger=hc.logger.name):
        svc._upsert_supabase_cache_safe("VB", other, None, True)
    assert "security_kind" not in svc.supabase.upserts[-1][1]["response_json"]
    assert any("fund-shape-refused" in m and "VB" in m for m in _warnings(caplog))
    assert svc._check_supabase_cache("VB") is None, "an unmarked degraded row is refused"


# ══════════════════════════════════════════════════════════════════════════════
# Collector parity: VB-shaped builds served from the new fund rows
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_collector_parity_for_a_fund_served_from_the_new_rows(monkeypatch):
    from app.schemas.profit_power import ProfitPowerResponse
    from app.services import health_snapshot_service as hs
    from app.services import profit_power_service as pp
    from app.services import profitability_snapshot_service as ps
    from app.services.agents.ticker_report_data_collector import (
        _refuse_degraded_financials,
        _settle_snapshot_result,
    )

    _run_upserts_inline(monkeypatch)

    # Signal of Confidence: build once (written, marked), then served from Tier 2.
    _soc_reset()
    soc_fmp = _SocFMP()
    soc_svc = _soc(soc_fmp)
    await soc_svc.get_signal_of_confidence("VB")
    sos._cache.clear()
    soc_fmp.calls.clear()
    soc_resp = await soc_svc.get_signal_of_confidence("VB")
    assert soc_fmp.calls == [] and soc_resp.data_points == [] and soc_resp.degraded == []

    # Health Check: build once (written, marked), then served from Tier 2.
    hc_fmp = _HcFMP()
    hc_svc = _hc(monkeypatch, hc_fmp)
    await hc_svc.get_health_check("VB")
    hc._cache.clear()
    hc_fmp.calls.clear()
    monkeypatch.setattr(hc, "get_health_check_service", lambda: hc_svc)

    # Health snapshot over that Health Check (function-scoped import: patch the source).
    monkeypatch.setattr(hs, "get_sector_benchmark_lookup", lambda: _Lookup())
    hs._cache.clear()
    hs._inflight.clear()
    hsnap = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    hsnap.supabase = None
    hsnap.fmp = _HcFMP()
    monkeypatch.setattr(hsnap, "_check_supabase_cache", lambda ticker: None)
    hs_written: List[str] = []
    monkeypatch.setattr(hsnap, "_upsert_supabase_cache", lambda t, *_r: hs_written.append(t))
    health_result = await hsnap.get_health_snapshot_with_status("VB")
    assert hc_fmp.calls == [], "the health card's Health Check must come from the fund row"

    # Profitability snapshot over an empty Profit Power (what a fund builds).
    class _PP:
        async def get_profit_power(self, ticker):
            return ProfitPowerResponse(symbol=ticker, annual=[], quarterly=[])

    monkeypatch.setattr(pp, "get_profit_power_service", lambda: _PP())
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup", lambda: _Lookup())
    ps._cache.clear()
    ps._inflight.clear()
    psnap = ps.ProfitabilitySnapshotService.__new__(ps.ProfitabilitySnapshotService)
    psnap.supabase = None
    psnap.fmp = _HcFMP()
    monkeypatch.setattr(psnap, "_check_supabase_cache", lambda ticker: None)
    ps_written: List[str] = []
    monkeypatch.setattr(psnap, "_upsert_supabase_cache", lambda t, *_r: ps_written.append(t))
    prof_result = await psnap.get_profitability_snapshot_with_status("VB")
    # Precondition: both cards carry ONLY the company-state statuses a fund produces.
    assert health_result[1] == ["health_check:no_metrics", "no_values"], health_result[1]
    assert prof_result[1] == ["no_values"], prof_result[1]

    out = SimpleNamespace(
        ticker="VB", degraded_sections=[], growth_chart=None, profit_power=None,
        earnings=None, signal_of_confidence=soc_resp, revenue_breakdown=None,
        snap_profitability="unset", snap_health="unset",
    )
    _settle_snapshot_result(out, "snap_health", health_result, "VB")
    _settle_snapshot_result(out, "snap_profitability", prof_result, "VB")
    _refuse_degraded_financials(out)

    assert out.degraded_sections == [], out.degraded_sections
    assert out.snap_health is None and out.snap_profitability is None
    # The fund's SoC section is kept exactly as before (nothing degraded).
    assert out.signal_of_confidence is soc_resp
    # Neither snapshot reached its own 24h tier (company-state, sentinel rating).
    assert hs_written == [] and ps_written == []
    sos._cache.clear()
    hc._cache.clear()
    hs._cache.clear()
    ps._cache.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Contract comments: the one persisted degraded shape is written down where readers look
# ══════════════════════════════════════════════════════════════════════════════
#
# The behaviour above admits ONE degraded build into health_check_cache (a fund's
# `["no_metrics"]`, marked) and one empty build into signal_of_confidence_cache (a fund's
# no-points build, marked, `degraded == []`). Two contract texts still promised "a degraded
# build is never written": `HealthCheckResponse.degraded` and the SYSTEM_DESIGN_GUIDELINES
# paragraph "A partial build is served, never stored". Both now name the fund exception,
# and the two guards below keep them from drifting back. A moved anchor (class, field,
# paragraph) is `pytest.fail`, so a refactor cannot make a guard pass vacuously.
#
# `SignalOfConfidenceResponse.degraded` ("Non-empty means it was never written") needs no
# guard of its own: it stays true because the fund row carries `degraded == []`, which
# `test_soc_fund_empty_build_is_written_once_marked_and_served_from_tier2` pins.

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_HC_SCHEMA_PATH = _BACKEND_DIR / "app" / "schemas" / "health_check.py"
_DESIGN_DOC_PATH = (
    _BACKEND_DIR.parent / "documents" / "System Design" / "SYSTEM_DESIGN_GUIDELINES.md"
)
_DOC_ANCHOR = "**A partial build is served, never stored"
_FUND_WORD = re.compile(r"\bfunds?\b", re.IGNORECASE)


def _field_comment(source: str, class_name: str, field: str) -> str:
    """The `#` block directly above `field:` inside `class class_name(` — bound to that
    class body (it ends at the next column-0 statement), so a same-named field in another
    class cannot answer for it. A missing anchor is `pytest.fail`, never an assertion."""
    lines = source.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"class {class_name}(")), None,
    )
    if start is None:
        pytest.fail(f"class {class_name} not found — re-point this guard")
    end = next(
        (i for i in range(start + 1, len(lines))
         if lines[i].strip() and not lines[i][0].isspace()),
        len(lines),
    )
    field_re = re.compile(rf"^\s+{re.escape(field)}\s*:")
    at = next((i for i in range(start + 1, end) if field_re.match(lines[i])), None)
    if at is None:
        pytest.fail(f"{class_name}.{field} not found in its class body — re-point this guard")
    block: List[str] = []
    j = at - 1
    while j > start and lines[j].strip().startswith("#"):
        block.append(lines[j].strip().lstrip("#").strip())
        j -= 1
    if not block:
        pytest.fail(f"{class_name}.{field} lost its contract comment — re-point this guard")
    return " ".join(reversed(block))


def _doc_paragraph(text: str, anchor: str) -> str:
    """The paragraph (blank-line bounded) that opens with `anchor`."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(anchor)), None)
    if start is None:
        pytest.fail(f"design-doc paragraph {anchor!r} not found — re-point this guard")
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return " ".join(lines[start:end])


def _missing_fund_exception(text: str, *, need_marker: bool) -> List[str]:
    """What a contract text lacks to describe the fund exception: the word fund, the HC
    reason it persists (`no_metrics`) and, for the schema comment, the row marker."""
    missing: List[str] = []
    if not _FUND_WORD.search(text):
        missing.append("fund")
    if "no_metrics" not in text:
        missing.append("no_metrics")
    if need_marker and "security_kind" not in text:
        missing.append("security_kind")
    return missing


def test_hc_schema_degraded_comment_names_the_fund_exception():
    comment = _field_comment(
        _HC_SCHEMA_PATH.read_text(encoding="utf-8"), "HealthCheckResponse", "degraded",
    )
    # Non-vacuity: this is the persistence contract, not some other comment.
    assert "health_check_cache" in comment, comment
    missing = _missing_fund_exception(comment, need_marker=True)
    assert not missing, (
        f"HealthCheckResponse.degraded promises a degraded build is never written, but a "
        f"fund's [\"no_metrics\"] build IS (marked `security_kind: \"fund\"`); the comment "
        f"lacks {missing}: {comment!r}"
    )


def test_design_doc_partial_build_paragraph_names_the_fund_exception():
    paragraph = _doc_paragraph(_DESIGN_DOC_PATH.read_text(encoding="utf-8"), _DOC_ANCHOR)
    missing = _missing_fund_exception(paragraph, need_marker=False)
    assert not missing, (
        f"the design doc says a degraded build is never written to its Supabase table, but "
        f"a fund's Health Check (no_metrics) and Signal of Confidence (empty) builds are; "
        f"the paragraph lacks {missing}"
    )
