"""P20 — a failed earnings calendar is never persisted as "no announcements".

Health Check, Profit Power and Signal of Confidence fetch the per-symbol earnings calendar
ONLY to stamp their 24h Supabase row with ``next_earnings_date``; the reader drops the row
once ``today >= next_earnings_date``, which is what rebuilds the card on report day. They
used to call ``get_earning_calendar_full`` with the swallowing default, so a 429 / 5xx /
non-list body came back as ``[]``, the stamp read None and a CLEAN 24h row was written with
no report-day bound (OWNER_TASKS recorded it as an accepted residual; a Health Check test
pinned it).

Design A (owner decision, 2026-10-01): the three call with ``raise_errors=True``; a failed
fetch is ``_earnings_common.CALENDAR_UNKNOWN`` — NOT a ``degraded`` reason (the calendar
feeds no served value, so the response, the report and iOS are unaffected) — and the getter
serves that build from its normal memory tier but never writes it to Supabase. Growth copies
Profit Power's column, so a lookup that RAISED is CALENDAR_UNKNOWN there too (an absent row
is still the documented 24h fallback — the accepted cold-ticker residual).

Every getter test drives the PUBLIC getter with the REAL ``FMPClient.get_earning_calendar_full``
over a fake ``_make_request`` (the client's own swallow-vs-raise logic is part of the defect),
and lets the REAL ``_upsert_supabase_cache_safe`` write into an in-memory Supabase. Negative
controls prove a real answer — including ``[]`` — is still persisted, so the "not persisted"
assertions cannot pass by persisting nothing at all.

Hermetic: no FMP, Supabase or network call.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import logging
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest

import test_health_check_deepcheck as hcd
import test_profit_power_deepcheck as ppd
import test_soc_deepcheck as socd
from app.integrations.fmp import (
    FMPClient,
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.schemas.health_check import HealthCheckResponse
from app.schemas.profit_power import ProfitPowerResponse
from app.schemas.signal_of_confidence import (
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services import _earnings_common as ec
from app.services import growth_service as gmod
from app.services import health_check_service as hc
from app.services import profit_power_service as pp
from app.services import signal_of_confidence_service as sos
from app.services._earnings_common import (
    CALENDAR_UNKNOWN,
    next_earnings_stamp,
    stamp_is_persistable,
)

_APP = Path(__file__).resolve().parents[1] / "app"


def _day(offset: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


# ── shared harness ────────────────────────────────────────────────────────────


class _FakeSupabase:
    """In-memory stand-in for the sync SDK: `select` chains answer `rows[table]` (or raise
    `select_error`); `upsert` JSON-encodes the row exactly as the SDK would — a sentinel
    that leaked into a row would raise here — and records it."""

    def __init__(self, rows: Optional[Dict[str, List[Dict]]] = None,
                 select_error: Optional[BaseException] = None) -> None:
        self.rows = rows or {}
        self.select_error = select_error
        self.upserts: List[tuple] = []

    def table(self, name: str) -> "_FakeTable":
        return _FakeTable(self, name)


class _FakeTable:
    def __init__(self, sb: _FakeSupabase, name: str) -> None:
        self._sb, self._name, self._op = sb, name, None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, row, on_conflict=None):
        json.dumps(row)
        self._op = "upsert"
        self._sb.upserts.append((self._name, row))
        return self

    def execute(self):
        if self._op == "select":
            if self._sb.select_error is not None:
                raise self._sb.select_error
            return SimpleNamespace(data=list(self._sb.rows.get(self._name, [])))
        return SimpleNamespace(data=[])


class _RealCalendar:
    """The REAL `FMPClient.get_earning_calendar_full` over a fake `_make_request` that
    raises (an Exception answer) or returns `answer`. Records the `raise_errors` flag of
    every call; an optional `gate` holds the call open (in-flight join tests)."""

    def __init__(self, answer: Any, gate: Optional[asyncio.Event] = None) -> None:
        self.flags: List[bool] = []
        self.entered = asyncio.Event()
        client = FMPClient.__new__(FMPClient)

        async def _make_request(endpoint, params=None):
            assert endpoint == "earnings", endpoint
            self.entered.set()
            if gate is not None:
                await gate.wait()
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        client._make_request = _make_request   # instance attribute on a bare object
        self._client = client

    async def __call__(self, ticker, raise_errors=False):
        self.flags.append(raise_errors)
        return await FMPClient.get_earning_calendar_full(
            self._client, ticker, raise_errors=raise_errors,
        )


class _AnswersFMP:
    """HC / PP legs answer from a dict (statement calls may key on `name:period`; an
    Exception answer is RAISED, like a failed leg); the calendar is `_RealCalendar`."""

    def __init__(self, answers: Dict[str, Any], calendar: _RealCalendar) -> None:
        self._answers = answers
        self.calendar = calendar

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return await self.calendar(ticker, raise_errors=raise_errors)

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            key = f"{name}:{kwargs['period']}" if "period" in kwargs else name
            answer = self._answers.get(key, self._answers.get(name, []))
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


class _SocFMP(socd._FMP):
    """The SoC deep-check fake with the REAL calendar, and an optionally failing ratios leg."""

    def __init__(self, calendar: _RealCalendar, ratios_error: Optional[BaseException] = None,
                 **kw: Any) -> None:
        super().__init__(**kw)
        self.calendar = calendar
        self._ratios_error = ratios_error

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return await self.calendar(ticker, raise_errors=raise_errors)

    async def get_financial_ratios(self, ticker, period="annual", limit=10):
        if self._ratios_error is not None:
            raise self._ratios_error
        return await super().get_financial_ratios(ticker, period=period, limit=limit)


def _run_upserts_inline(monkeypatch) -> None:
    """The getters fire the write with `loop.run_in_executor(None, …)` and never await it.
    Run exactly that call inline so the assertion cannot race it; every other executor job
    (`asyncio.to_thread`: the cache read, the benchmark lookup) still goes to the pool."""
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


class _Case:
    """One Financials service wired for a getter test."""

    def __init__(self, name, module, svc, fmp, ticker, cache_key, get, table):
        self.name, self.module, self.svc, self.fmp = name, module, svc, fmp
        self.ticker, self.cache_key, self.get, self.table = ticker, cache_key, get, table


def _health_check(monkeypatch, calendar, leg_error=None) -> _Case:
    answers = hcd._answers(income=leg_error)          # None = the healthy default
    module, svc = hcd._service(monkeypatch, answers)
    svc.fmp = _AnswersFMP(answers, calendar)
    return _Case("health_check", module, svc, svc.fmp, "TEST", "health_check:TEST",
                 svc.get_health_check, "health_check_cache")


def _profit_power(monkeypatch, calendar, leg_error=None) -> _Case:
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup",
                        lambda: ppd._FakeLookup(ppd._ANNUAL_CELLS, ppd._QUARTERLY_CELLS))
    pp._cache.clear()
    pp._inflight.clear()
    answers = dict(ppd._fmp()._answers)
    if leg_error is not None:
        answers["get_company_profile"] = leg_error
    fmp = _AnswersFMP(answers, calendar)
    svc = ppd._bare_service(fmp)
    return _Case("profit_power", pp, svc, fmp, "ZZZ", "profit_power:ZZZ",
                 svc.get_profit_power, "profit_power_cache")


def _signal_of_confidence(monkeypatch, calendar, leg_error=None) -> _Case:
    sos._cache.clear()
    sos._inflight.clear()
    dates = socd._QEND
    fmp = _SocFMP(calendar, ratios_error=leg_error,
                  cashflow=socd._aapl_like_cf(dates), income=[socd._inc(d) for d in dates])
    svc = socd._wire(fmp)
    return _Case("signal_of_confidence", sos, svc, fmp, "AAPL", "signal_of_confidence:AAPL",
                 svc.get_signal_of_confidence, "signal_of_confidence_cache")


_SERVICES = {
    "health_check": _health_check,
    "profit_power": _profit_power,
    "signal_of_confidence": _signal_of_confidence,
}
#: The leg each "calendar AND another leg failed" case breaks, and its degraded name.
_OTHER_LEG = {
    "health_check": "income",
    "profit_power": "profile",
    "signal_of_confidence": "annual_ratios",
}


def _wire(monkeypatch, service: str, calendar_answer: Any, *, leg_error=None,
          gate: Optional[asyncio.Event] = None) -> _Case:
    case = _SERVICES[service](monkeypatch, _RealCalendar(calendar_answer, gate), leg_error)
    case.svc.supabase = _FakeSupabase()
    monkeypatch.setattr(case.svc, "_check_supabase_cache", lambda ticker: None)
    _run_upserts_inline(monkeypatch)
    return case


def _written(case: _Case) -> List[Dict]:
    return [row for table, row in case.svc.supabase.upserts if table == case.table]


def _memory_is_the_normal_tier(case: _Case, value: Any) -> None:
    """A calendar failure is not degradation: the memory tier keeps its NORMAL TTL."""
    if case.name == "signal_of_confidence":
        ts, cached, ttl = sos._cache[case.cache_key]
        assert cached is value and ttl == sos._CACHE_TTL
        return
    ts, cached = case.module._cache[case.cache_key]
    assert cached is value
    # 61 s later: Profit Power holds a DEGRADED build for 60 s only; Health Check always 300 s.
    case.module._cache[case.cache_key] = (ts - 61, cached)
    assert case.module._cache_get(case.cache_key) is value


def _warnings(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def _http_500() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://fmp.invalid/stable/earnings")
    return httpx.HTTPStatusError(
        "500 Internal Server Error", request=request,
        response=httpx.Response(500, request=request),
    )


#: Calendars that exercise the stale-reschedule rule (``_earnings_common.next_pending_earnings``)
#: — shared by the getter persistence controls and the stamp table below.
_STALE_RESCHEDULE_LEFTOVER = [{"date": _day(-3), "epsActual": 1.2},
                              {"date": _day(4)}, {"date": _day(90)}]
_LONE_SUSPECT_ROW = [{"date": _day(-3), "epsActual": 1.2}, {"date": _day(4)}]
_LATE_Q4_THEN_REAL_NEXT = [{"date": _day(-10), "epsActual": 0.2},
                           {"date": _day(34)}, {"date": _day(125)}]


_CALENDAR_FAILURES = [
    pytest.param(FMPRateLimitException("429"), id="rate_limited"),
    pytest.param(FMPUnavailableException("503"), id="unavailable"),
    pytest.param(_http_500(), id="http_500"),
    pytest.param({"Error Message": "Limit Reach . Please upgrade your plan"}, id="error_body"),
]


# ── the defect: a failed calendar is served, NOT degraded, and NOT persisted ──


@pytest.mark.asyncio
@pytest.mark.parametrize("service", list(_SERVICES))
@pytest.mark.parametrize("failure", _CALENDAR_FAILURES)
async def test_a_failed_calendar_is_served_but_never_persisted(
    monkeypatch, caplog, service, failure,
):
    case = _wire(monkeypatch, service, failure)
    with caplog.at_level(logging.WARNING):
        resp = await case.get(case.ticker)

    assert case.fmp.calendar.flags == [True], "the calendar must be fetched with raise_errors=True"
    assert resp.symbol == case.ticker, "the build must still be SERVED"
    assert resp.degraded == [], "the calendar feeds no served value: it is never a degraded reason"
    assert _written(case) == [], "a build with an unread calendar reached the 24h tier"
    _memory_is_the_normal_tier(case, resp)

    msgs = _warnings(caplog)
    assert any(case.ticker in m and "step=earnings_calendar" in m for m in msgs), msgs
    # The GETTER's gate must be what stops the write — the upsert's own refusal is only a
    # backstop, and a build that reaches it has already lost the gate.
    assert any(case.ticker in m and "NOT persisted" in m and "earnings calendar unavailable" in m
               for m in msgs), msgs
    assert not any("REFUSED" in m for m in msgs), msgs


@pytest.mark.asyncio
@pytest.mark.parametrize("service", list(_SERVICES))
@pytest.mark.parametrize("answer, expected", [
    # An ETF / unknown symbol: a real "no announcements" answer — cacheable on the 24h TTL.
    pytest.param([], None, id="empty_etf"),
    # History only: nothing pending.
    pytest.param([{"date": "2025-01-30", "epsActual": 2.4, "revenueActual": 1.2e11,
                   "epsEstimated": 2.35}], None, id="history_only"),
    # A future pending report is the bound.
    pytest.param([{"date": "2025-01-30", "epsActual": 2.4},
                  {"date": _day(40), "epsEstimated": 2.5}], _day(40), id="future_pending"),
    # Report DAY: today's pending report (the reader then rebuilds all day).
    pytest.param([{"date": _day(0), "epsEstimated": 2.5},
                  {"date": _day(90), "epsEstimated": 2.6}], _day(0), id="pending_today"),
    # Malformed rows: non-dicts dropped by the client, undated / bad dates skipped.
    pytest.param(["junk", None, 5, {"epsEstimated": 1.0}, {"date": "not-a-date"},
                  {"date": None, "epsEstimated": 1.0}], None, id="malformed_rows"),
    # The reschedule rule, through the stamp these three builders actually use
    # (`next_earnings_stamp`; their old `_find_next_earnings_date*` wrappers were
    # deleted 2026-10-01). Reported EARLY 3 days ago; the original date is still listed
    # pending 4 days out: a leftover. The row is invalidated on the REAL next report.
    pytest.param(_STALE_RESCHEDULE_LEFTOVER, _day(90), id="stale_reschedule_leftover"),
    # ...but a lone pending row is never dropped (no later row to prefer).
    pytest.param(_LONE_SUSPECT_ROW, _day(4), id="lone_suspect_row_kept"),
    # A late Q4 then the real next report 44 days later — outside the 21-day window, so it
    # is the bound, not the quarter after it.
    pytest.param(_LATE_Q4_THEN_REAL_NEXT, _day(34), id="late_q4_real_next_report"),
])
async def test_a_real_calendar_answer_is_still_persisted(monkeypatch, service, answer, expected):
    case = _wire(monkeypatch, service, answer)
    resp = await case.get(case.ticker)

    assert case.fmp.calendar.flags == [True]
    assert resp.degraded == []
    rows = _written(case)
    assert len(rows) == 1, "control: a complete build with a readable calendar must persist"
    assert rows[0]["ticker"] == case.ticker
    assert rows[0]["next_earnings_date"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("service", list(_SERVICES))
async def test_a_not_entitled_calendar_is_persisted_without_a_bound(monkeypatch, caplog, service):
    """A 402 is permanent: refusing to persist it would make the card permanently
    uncacheable (a rebuild every 5 minutes for ever). It keeps the plain 24h TTL — loudly."""
    case = _wire(monkeypatch, service, FMPNotEntitledException("earnings not in package"))
    with caplog.at_level(logging.WARNING):
        resp = await case.get(case.ticker)

    assert resp.degraded == []
    rows = _written(case)
    assert len(rows) == 1 and rows[0]["next_earnings_date"] is None
    assert any(case.ticker in m and "step=earnings_calendar" in m for m in _warnings(caplog))


# ── outliers ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("service", list(_SERVICES))
async def test_calendar_and_another_leg_failing_names_only_that_leg(monkeypatch, service):
    case = _wire(monkeypatch, service, FMPRateLimitException("429"),
                 leg_error=FMPRateLimitException("429 on another leg"))
    resp = await case.get(case.ticker)

    assert resp.degraded == [_OTHER_LEG[service]], "the calendar must not add a reason"
    assert _written(case) == []
    assert case.fmp.calendar.flags == [True]
    if service == "signal_of_confidence":
        # A REAL degraded build keeps the short TTL (unchanged by P20).
        assert sos._cache[case.cache_key][2] == sos._DEGRADED_CACHE_TTL


@pytest.mark.asyncio
@pytest.mark.parametrize("service", list(_SERVICES))
async def test_a_joiner_gets_the_leaders_calendar_failed_build(monkeypatch, caplog, service):
    gate = asyncio.Event()
    case = _wire(monkeypatch, service, FMPRateLimitException("429"), gate=gate)
    with caplog.at_level(logging.INFO):
        leader = asyncio.ensure_future(case.get(case.ticker))
        await asyncio.wait_for(case.fmp.calendar.entered.wait(), timeout=5)
        assert case.cache_key in case.module._inflight
        joiner = asyncio.ensure_future(case.get(case.ticker))
        for _ in range(500):
            if any("in-flight JOIN" in r.getMessage() for r in caplog.records):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("the second caller never joined the in-flight build")
        gate.set()
        lead, joined = await asyncio.wait_for(asyncio.gather(leader, joiner), timeout=10)

    assert joined is lead
    assert lead.degraded == []
    assert case.fmp.calendar.flags == [True], "one build for both callers"
    assert _written(case) == []


@pytest.mark.parametrize("slot, expected", [
    ([], None),
    ([{"date": "2999-01-01"}], "2999-01-01"),
    ([{"date": _day(0)}], _day(0)),
    ([{"date": "2020-01-01", "epsActual": 1.0}], None),
    pytest.param(_STALE_RESCHEDULE_LEFTOVER, _day(90), id="stale_reschedule_leftover"),
    pytest.param(_LONE_SUSPECT_ROW, _day(4), id="lone_suspect_row_kept"),
    pytest.param(_LATE_Q4_THEN_REAL_NEXT, _day(34), id="late_q4_real_next_report"),
    (FMPNotEntitledException("402"), None),
    (FMPRateLimitException("429"), CALENDAR_UNKNOWN),
    (FMPUnavailableException("503"), CALENDAR_UNKNOWN),
    (RuntimeError("bug"), CALENDAR_UNKNOWN),
    # gather(return_exceptions=True) puts a CancelledError INSTANCE (a BaseException,
    # not an Exception) in the slot when the child is cancelled.
    (asyncio.CancelledError(), CALENDAR_UNKNOWN),
    ({"Error Message": "x"}, CALENDAR_UNKNOWN),
    (None, CALENDAR_UNKNOWN),
    ("2026-10-01", CALENDAR_UNKNOWN),
])
def test_next_earnings_stamp_table(caplog, slot, expected):
    with caplog.at_level(logging.WARNING):
        got = next_earnings_stamp(slot, ticker="ZZZ", service="unit")
    if expected is None or expected is CALENDAR_UNKNOWN:
        assert got is expected
    else:
        assert got == expected
    if not isinstance(slot, list):
        msgs = _warnings(caplog)
        assert any("ticker=ZZZ" in m and "step=earnings_calendar" in m
                   for m in msgs), "a failed or refused calendar must be logged"
        if isinstance(slot, BaseException):
            # An exception (a CancelledError instance included) is named as one, with its
            # message — never misreported as an odd BODY.
            assert any(type(slot).__name__ in m and "expected a list" not in m
                       for m in msgs), msgs


def test_an_unexpected_exception_logs_its_stack_and_a_typed_fmp_one_does_not(caplog):
    with caplog.at_level(logging.WARNING):
        next_earnings_stamp(RuntimeError("bug"), ticker="ZZZ", service="unit")
        next_earnings_stamp(FMPRateLimitException("429"), ticker="YYY", service="unit")
    by_ticker = {("ZZZ" if "ZZZ" in r.getMessage() else "YYY"): r for r in caplog.records}
    assert by_ticker["ZZZ"].exc_info is not None
    assert not by_ticker["YYY"].exc_info


def test_the_sentinel_is_not_a_string_and_keeps_its_identity():
    assert not isinstance(CALENDAR_UNKNOWN, str), (
        "a str sentinel would be STORED, and `today >= '<sentinel>'` never goes stale"
    )
    assert copy.copy(CALENDAR_UNKNOWN) is CALENDAR_UNKNOWN
    assert copy.deepcopy({"k": CALENDAR_UNKNOWN})["k"] is CALENDAR_UNKNOWN
    assert pickle.loads(pickle.dumps(CALENDAR_UNKNOWN)) is CALENDAR_UNKNOWN
    assert type(CALENDAR_UNKNOWN)() is CALENDAR_UNKNOWN
    with pytest.raises(TypeError):
        json.dumps({"next_earnings_date": CALENDAR_UNKNOWN})
    assert stamp_is_persistable(None) and stamp_is_persistable("2026-10-01")
    for bad in (CALENDAR_UNKNOWN, 0, b"2026-10-01", ["2026-10-01"], object()):
        assert not stamp_is_persistable(bad), bad


# ── the upsert itself refuses the sentinel (belt and braces), in all four services ──


def _growth_response() -> GrowthResponse:
    point = [GrowthDataPointSchema(period="2025", value=10.0, yoy_change_percent=12.0,
                                   sector_average_yoy=8.0)]
    return GrowthResponse(symbol="AAPL", eps_annual=point, eps_quarterly=[],
                          revenue_annual=point, revenue_quarterly=[],
                          operating_profit_annual=point, free_cash_flow_annual=point)


_UPSERT_TARGETS = [
    pytest.param(hc.HealthCheckService, "health_check_cache",
                 lambda: HealthCheckResponse(symbol="AAPL", overall_rating="good",
                                             passed_count=0, total_count=0, metrics=[]),
                 id="health_check"),
    pytest.param(pp.ProfitPowerService, "profit_power_cache",
                 lambda: ProfitPowerResponse(symbol="AAPL", annual=[], quarterly=[]),
                 id="profit_power"),
    pytest.param(sos.SignalOfConfidenceService, "signal_of_confidence_cache",
                 lambda: SignalOfConfidenceResponse(
                     symbol="AAPL", data_points=[], summary=SignalOfConfidenceSummarySchema()),
                 id="signal_of_confidence"),
    pytest.param(gmod.GrowthService, "growth_cache", _growth_response, id="growth"),
]


@pytest.mark.parametrize("cls, table, make", _UPSERT_TARGETS)
def test_the_upsert_refuses_an_unknown_stamp(caplog, cls, table, make):
    svc = cls.__new__(cls)
    svc.supabase = _FakeSupabase()
    with caplog.at_level(logging.WARNING):
        svc._upsert_supabase_cache_safe("AAPL", make(), CALENDAR_UNKNOWN)
    assert svc.supabase.upserts == []
    assert any("AAPL" in m and "REFUSED" in m for m in _warnings(caplog)), (
        "the refusal must be the explicit guard, not the SDK's JSON encode failing"
    )

    for stamp in (None, _day(30)):           # controls: both persistable stamps are written
        svc._upsert_supabase_cache_safe("AAPL", make(), stamp)
    assert [(t, row["next_earnings_date"]) for t, row in svc.supabase.upserts] == [
        (table, None), (table, _day(30)),
    ]


# ── payload versions: a row that may hold a swallowed failure is rebuilt ──────


def _row(response_json: Dict[str, Any], next_date: Optional[str] = None) -> Dict[str, Any]:
    return {"response_json": response_json,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "next_earnings_date": next_date}


_VERSIONED = [
    pytest.param(hc.HealthCheckService, "health_check_cache", "_HC_PAYLOAD_VERSION", 4,
                 lambda: HealthCheckResponse(symbol="AAPL", overall_rating="good",
                                             passed_count=0, total_count=0, metrics=[]),
                 id="health_check"),
    pytest.param(pp.ProfitPowerService, "profit_power_cache", "_PP_PAYLOAD_VERSION", 5,
                 lambda: ProfitPowerResponse(symbol="AAPL", annual=[], quarterly=[]),
                 id="profit_power"),
    pytest.param(sos.SignalOfConfidenceService, "signal_of_confidence_cache",
                 "_PAYLOAD_VERSION", 10,
                 lambda: SignalOfConfidenceResponse(
                     symbol="AAPL", data_points=[], summary=SignalOfConfidenceSummarySchema()),
                 id="signal_of_confidence"),
]


@pytest.mark.parametrize("cls, table, const, bumped_to, make", _VERSIONED)
def test_pre_p20_rows_are_rebuilt_and_current_rows_served(cls, table, const, bumped_to, make):
    module = {hc.HealthCheckService: hc, pp.ProfitPowerService: pp,
              sos.SignalOfConfidenceService: sos}[cls]
    current = getattr(module, const)
    assert current >= bumped_to, f"{const} was not bumped for P20"
    body = make().model_dump()
    svc = cls.__new__(cls)

    svc.supabase = _FakeSupabase({table: [_row({**body, "payload_version": bumped_to - 1})]})
    assert svc._check_supabase_cache("AAPL") is None, (
        "a pre-P20 row's NULL next_earnings_date may be a swallowed calendar failure"
    )
    svc.supabase = _FakeSupabase({table: [_row({**body, "payload_version": current})]})
    served = svc._check_supabase_cache("AAPL")
    assert served is not None and served.symbol == "AAPL"


# ── Growth: a lookup that RAISED is not a "no bound" answer ────────────────────


async def _drive_growth(monkeypatch, supabase: _FakeSupabase):
    gmod._cache.clear()
    gmod._inflight.clear()
    gmod._degraded_by_key.clear()
    svc = gmod.GrowthService.__new__(gmod.GrowthService)
    svc.fmp = None
    svc.supabase = supabase

    async def _clean_build(ticker):
        return _growth_response(), []

    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    monkeypatch.setattr(svc, "_build_growth", _clean_build)
    _run_upserts_inline(monkeypatch)
    resp = await svc.get_growth("AAPL")
    return svc, resp


@pytest.mark.asyncio
async def test_growth_with_a_failed_next_earnings_lookup_is_served_not_persisted(
    monkeypatch, caplog,
):
    supabase = _FakeSupabase(select_error=RuntimeError("supabase 520"))
    with caplog.at_level(logging.WARNING):
        svc, resp = await _drive_growth(monkeypatch, supabase)

    assert resp.symbol == "AAPL" and resp.degraded == []
    assert supabase.upserts == []
    assert "growth:AAPL" in gmod._cache, "the memory tier still absorbs retries"
    msgs = _warnings(caplog)
    assert any("AAPL" in m and "step=next_earnings_lookup" in m for m in msgs), msgs
    # The getter's gate, not the upsert's backstop refusal.
    assert any("Growth NOT persisted for AAPL" in m and "lookup failed" in m for m in msgs), msgs
    assert not any("REFUSED" in m for m in msgs), msgs
    gmod._cache.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("pp_rows, expected", [
    # No profit_power row (cold ticker): the documented 24h fallback — the accepted
    # residual during a calendar outage, when Profit Power stops writing.
    pytest.param([], None, id="absent_row"),
    pytest.param([{"next_earnings_date": None}], None, id="null_column"),
    # A PP row dated before today is old, not a bound: ignored, never copied born-stale.
    pytest.param([{"next_earnings_date": _day(-3)}], None, id="past_date_ignored"),
    pytest.param([{"next_earnings_date": _day(0)}], _day(0), id="report_day"),
    pytest.param([{"next_earnings_date": _day(30)}], _day(30), id="future"),
])
async def test_growth_still_persists_a_real_lookup_answer(monkeypatch, pp_rows, expected):
    supabase = _FakeSupabase({"profit_power_cache": pp_rows})
    _svc, resp = await _drive_growth(monkeypatch, supabase)
    assert resp.degraded == []
    assert [(t, row["next_earnings_date"]) for t, row in supabase.upserts] == [
        ("growth_cache", expected),
    ]
    gmod._cache.clear()


# ── report proportionality: the calendar never reaches `degraded` downstream ──


@pytest.mark.asyncio
async def test_calendar_failed_builds_keep_their_report_and_snapshot_sections(monkeypatch):
    from app.services import health_snapshot_service as hs
    from app.services import profitability_snapshot_service as ps
    from app.services.agents.ticker_report_data_collector import _refuse_degraded_financials
    import test_snapshot_degraded_not_persisted as snapd

    boom = FMPRateLimitException("429")

    # Health snapshot over the REAL Health Check getter whose calendar failed.
    hcase = _wire(monkeypatch, "health_check", boom)
    monkeypatch.setattr(hc, "get_health_check_service", lambda: hcase.svc)
    monkeypatch.setattr(hs, "get_sector_benchmark_lookup", lambda: snapd._StubLookup())
    hs._cache.clear()
    hs._inflight.clear()
    hsnap = snapd._bare(hs.HealthSnapshotService, fmp=snapd._FakeFMP(
        get_balance_sheet=snapd._GOOD_BS, get_income_statement=snapd._GOOD_INC,
        get_company_profile=dict(snapd._PROFILE)))
    snapshot, degraded = await hsnap._compute_with_status("TEST")
    assert degraded == [], f"a calendar failure leaked into snap_health: {degraded}"
    assert snapshot.category == "Financial Health"

    # Profitability snapshot over the REAL Profit Power getter whose calendar failed.
    pcase = _wire(monkeypatch, "profit_power", boom)
    monkeypatch.setattr(pp, "get_profit_power_service", lambda: pcase.svc)
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup", lambda: snapd._StubLookup())
    ps._cache.clear()
    ps._inflight.clear()
    psnap = snapd._bare(ps.ProfitabilitySnapshotService, fmp=snapd._FakeFMP(
        get_key_metrics_ttm=snapd._KM, get_company_profile=dict(snapd._PROFILE)))
    _snap, degraded = await psnap._compute_with_status("ZZZ")
    assert degraded == [], f"a calendar failure leaked into snap_profitability: {degraded}"

    # The collector's frozen-Financials gate keeps both sections, unrecorded.
    pp_resp = await pcase.get("ZZZ")
    scase = _wire(monkeypatch, "signal_of_confidence", boom)
    soc_resp = await scase.get("AAPL")
    out = SimpleNamespace(ticker="ZZZ", degraded_sections=[], growth_chart=None,
                          profit_power=pp_resp, earnings=None,
                          signal_of_confidence=soc_resp, revenue_breakdown=None)
    _refuse_degraded_financials(out)
    assert out.profit_power is pp_resp and out.signal_of_confidence is soc_resp
    assert out.degraded_sections == []
    assert _written(hcase) == _written(pcase) == _written(scase) == []
    hs._cache.clear()
    ps._cache.clear()


# ── source guard: every Financials service that stamps next_earnings_date raises ──


#: The services whose cache row (or build) is stamped from the per-symbol calendar. Other
#: callers in app/ may legitimately swallow — this guard is scoped to these five.
_STAMPING_SERVICES = (
    "earnings_service",
    "health_check_service",
    "profit_power_service",
    "signal_of_confidence_service",
    "revenue_breakdown_service",
)


def _calendar_calls(source: str) -> List[ast.Call]:
    calls = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "get_earning_calendar_full":
            calls.append(node)
    return calls


@pytest.mark.parametrize("module", _STAMPING_SERVICES)
def test_every_calendar_call_in_a_stamping_service_raises_errors(module):
    """AST, so a comment or docstring that names the call cannot satisfy (or trip) it, and a
    multi-line call is still one node. A literal `raise_errors=True` keyword is required —
    a `**kwargs` splat or a variable cannot be verified here and is refused."""
    path = _APP / "services" / f"{module}.py"
    calls = _calendar_calls(path.read_text())
    assert calls, f"{module}: no get_earning_calendar_full call found — the guard is vacuous"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert None not in kw, f"{module}:{call.lineno}: a **splat hides raise_errors"
        flag = kw.get("raise_errors")
        assert isinstance(flag, ast.Constant) and flag.value is True, (
            f"{module}:{call.lineno}: get_earning_calendar_full without raise_errors=True "
            "swallows a 429 into [] ('no announcements') and persists a row with no "
            "report-day bound"
        )


def test_the_guard_sees_through_comments_and_multiline_calls():
    """The scanner itself: a commented-out call is not a call; a multi-line one is."""
    src = (
        "# self.fmp.get_earning_calendar_full(t)\n"
        "async def f(self, t):\n"
        "    '''get_earning_calendar_full(t) in prose'''\n"
        "    return await self.fmp.get_earning_calendar_full(\n"
        "        t,\n"
        "        raise_errors=True,\n"
        "    )\n"
    )
    calls = _calendar_calls(src)
    assert len(calls) == 1 and calls[0].keywords[0].arg == "raise_errors"
