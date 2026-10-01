"""Profit Power round 2 (2026-09-30) — backend half.

  R11  The interim off-calendar hide was a COMPANY-level vote on quarter NUMBERS, so a
       52/53-week calendar filer whose Q4 ends on Jan 1-7 (Cadence FY2025 Q4 ended
       2026-01-03) passed it while its Q4 joined the legacy cell "Q4'26" — peers'
       Oct-Dec 2026, or (while that cell is thin) the hold-back's Apr-Jun 2026. FINAL rule
       (calendar-quarter re-key): the join key is the CALENDAR quarter of the period end
       with a 1-7-day spill (`period_labels.calendar_quarter_label`), read against the
       period_type ``calendar_quarter`` rows. That Q4 joins "Q4'25"; Oracle's fiscal Q1
       (ends Aug 31) joins "Q3'25"; only an undated row joins nothing (""); there is no
       off-calendar hide, so a failed calendar-quarter read degrades every company.
  R36  A FAILED benchmark lookup (`BenchmarkLookupFailed`) was flattened by
       `hold_back_thin_benchmarks` into a plain dict, so a Supabase blip produced a
       peer-less build that was persisted for 24h and frozen into reports. It is now
       `degraded` ("benchmarks"): served, held 60 s in memory, never persisted.
  R38  Cay AI's profit summary read `annual[-1]`, which can now be an all-None revenue
       gap: "Latest annual margins for X (2025): ." or a lone sector figure that reads as
       the company's own.

Every test here fails on the first-pass code (checked by hand: the R11 tests against
`match_period = _quarterly_period_label(rec)`, the R36 tests without the `lookup_failed`
check, the R38 tests against the old formatting) and asserts the DEGRADED behaviour. The
R11/R36 tests were rewritten for the final calendar-quarter rule and fail on the interim
code as well (it answered "" for the Jan-3 Q4 and skipped Apple's quarterly read).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import pytest

from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services import profit_power_service as pp
from app.services.profit_power_service import _build_margin_points, _quarterly_join_key
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE,
    BenchmarkLookupFailed,
)
from app.utils.period_labels import calendar_quarter_label

_PROFILE = {"symbol": "CDNS", "sector": "Technology", "industry": "Software - Application"}


# ── harness (same shape as test_profit_power_deepcheck.py) ────────────────────


class _FakeFMP:
    def __init__(self, **answers: Any) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            key = f"{name}:{kwargs['period']}" if "period" in kwargs else name
            answer = self._answers.get(key, self._answers.get(name, []))
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return _call


class _FakeLookup:
    """`get_benchmarks` per period type; a period type listed in ``failed`` answers the
    real failure shape (`BenchmarkLookupFailed` of empty per-metric dicts). ``quarterly``
    holds the CALENDAR-quarter cells (period_type ``calendar_quarter``); the legacy
    ``"quarterly"`` type answers "no rows"."""

    def __init__(self, annual=None, quarterly=None, failed=()):
        self._by_type = {
            "annual": annual or {}, CALENDAR_QUARTER_PERIOD_TYPE: quarterly or {},
        }
        self._failed = set(failed)
        self.calls: List[str] = []

    def get_benchmarks(self, industry, sector, metrics, period_type):
        self.calls.append(period_type)
        if period_type in self._failed:
            return BenchmarkLookupFailed({m: {} for m in metrics})
        src = self._by_type.get(period_type, {})
        return {m: dict(src.get(m, {})) for m in metrics}


def _bare_service(fmp) -> pp.ProfitPowerService:
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.fmp = fmp
    svc.supabase = None
    return svc


def _cell(value, n, level="industry"):
    return {"value": value, "n": n, "level": level, "peer_group_name": "x"}


def _q(date, period, fy, net=10.0):
    return {"date": date, "period": period, "fiscalYear": fy, "revenue": 100.0,
            "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": net}


def _cdns_quarters():
    """A 52/53-week CALENDAR filer: quarters end on the Saturday nearest the quarter end,
    so Q4 FY2025 closes on 2026-01-03 and Q1 FY2026 on 2026-04-04."""
    return [
        _q("2024-12-28", "Q4", "2024"),
        _q("2025-03-29", "Q1", "2025"),
        _q("2025-06-28", "Q2", "2025"),
        _q("2025-09-27", "Q3", "2025"),
        _q("2026-01-03", "Q4", "2025"),
        _q("2026-04-04", "Q1", "2026"),
        _q("2026-07-04", "Q2", "2026"),
    ]


def _fmp(quarterly=None, **overrides):
    answers = {
        "get_company_profile": dict(_PROFILE),
        "get_income_statement:annual": [
            {"date": "2024-12-28", "fiscalYear": "2024", "period": "FY", "revenue": 100.0,
             "grossProfit": 80.0, "operatingIncome": 30.0, "netIncome": 20.0},
            {"date": "2026-01-03", "fiscalYear": "2025", "period": "FY", "revenue": 100.0,
             "grossProfit": 81.0, "operatingIncome": 31.0, "netIncome": 21.0},
        ],
        "get_income_statement:quarter": quarterly if quarterly is not None else _cdns_quarters(),
        "get_cash_flow_statement:annual": [],
        "get_cash_flow_statement:quarter": [],
        "get_earning_calendar_full": [],
    }
    answers.update(overrides)
    return _FakeFMP(**answers)


# ── R11: a Jan 1-7 Q4 joins its own calendar quarter, never the NEXT year's ───


@pytest.mark.parametrize("row,expected", [
    (_q("2026-01-03", "Q4", "2025"), "Q4'25"),    # Cadence: calendar Q4'25, never "Q4'26"
    (_q("2022-01-01", "Q4", "2021"), "Q4'21"),    # every historical Jan 1-7 Q4 too
    (_q("2026-01-07", "Q4", "2025"), "Q4'25"),    # day 7: still the spill
    (_q("2026-01-08", "Q4", "2025"), "Q1'26"),    # day 8: a real January end
    (_q("2024-12-28", "Q4", "2024"), "Q4'24"),    # a December Q4 keeps its join
    (_q("2026-04-04", "Q1", "2026"), "Q1'26"),    # a day-1-7 spill inside the year
    (_q("2025-09-27", "Q3", "2025"), "Q3'25"),
    (_q("2025-08-31", "Q1", "2026"), "Q3'25"),    # Oracle: fiscal Q1 = Jun-Aug → Q3'25
    (_q("2024-12-28", "Q1", "2025"), "Q4'24"),    # Apple: fiscal Q1 = Oct-Dec → Q4'24
    ({"date": None, "period": "Q2", "revenue": 1.0}, ""),        # undated: no key at all
    ({"date": "garbage", "period": "Q2", "fiscalYear": "2025"}, ""),
    ({"period": "Q2", "fiscalYear": "2025", "revenue": 1.0}, ""),
])
def test_quarterly_join_key_is_the_calendar_quarter_of_the_period_end(row, expected):
    assert _quarterly_join_key(row, "CDNS") == expected
    # One shared rule with the benchmark producer and growth_service.
    assert _quarterly_join_key(row, "CDNS") == calendar_quarter_label(row)


def test_an_undated_quarter_joins_nothing_and_says_so(caplog):
    with caplog.at_level(logging.INFO, logger=pp.logger.name):
        assert _quarterly_join_key({"date": None, "period": "Q2"}, "UNDATED") == ""
    assert "UNDATED" in caplog.text and "no usable period end" in caplog.text


def test_margin_points_carry_the_calendar_quarter_join_key():
    points = _build_margin_points(_cdns_quarters(), [], is_quarterly=True, ticker="CDNS")
    by_label = {p["period"]: p["_match_period"] for p in points}
    assert by_label["Q4 '25"] == "Q4'25", "Q4 FY2025 (Oct-Dec 2025) must not join Q4'26"
    assert by_label["Q4 '24"] == "Q4'24"
    assert by_label["Q1 '26"] == "Q1'26"
    assert by_label["Q3 '25"] == "Q3'25"
    # One distinct calendar quarter per fiscal quarter, in date order: no two Cadence
    # quarters can ever share (or skip) a peer cell.
    assert [p["_match_period"] for p in points] == [
        "Q4'24", "Q1'25", "Q2'25", "Q3'25", "Q4'25", "Q1'26", "Q2'26",
    ]


@pytest.mark.asyncio
async def test_cadence_q4_joins_the_q4_25_cell_never_a_year_later_one(monkeypatch):
    quarterly_cells = {"net_margin": {
        "Q4'24": _cell(0.05, 80),
        "Q1'25": _cell(0.051, 80), "Q2'25": _cell(0.052, 80), "Q3'25": _cell(0.053, 80),
        "Q4'25": _cell(0.06, 80),
        "Q1'26": _cell(0.08, 60), "Q2'26": _cell(0.09, 60),
        "Q4'26": _cell(0.30, 3),   # thin: built only from the Jan 1-7 filers
    }}
    lookup = _FakeLookup(quarterly=quarterly_cells)
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    result, _n, degraded = await _bare_service(_fmp())._build_profit_power("CDNS")

    assert degraded == []
    assert lookup.calls == ["annual", "calendar_quarter"]
    q = {p.period: p for p in result.quarterly}
    q4 = q["Q4 '25"]
    assert q4.sector_average_net_margin == 6.0, (
        f"Q4 '25 (Oct-Dec 2025) drew {q4.sector_average_net_margin} — 9.0 is peers' "
        f"Apr-Jun 2026 via the hold-back, 30.0 the thin Q4'26 cell, None the interim rule"
    )
    assert q4.net_margin == 10.0, "the company's own quarter still charts"
    # Every other quarter keeps its own calendar quarter's value.
    assert {label: p.sector_average_net_margin for label, p in q.items()} == {
        "Q4 '24": 5.0, "Q1 '25": 5.1, "Q2 '25": 5.2, "Q3 '25": 5.3,
        "Q4 '25": 6.0, "Q1 '26": 8.0, "Q2 '26": 9.0,
    }


# ── R36: a failed benchmark lookup is a degraded build ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [
    ("annual",),
    (CALENDAR_QUARTER_PERIOD_TYPE,),
    ("annual", CALENDAR_QUARTER_PERIOD_TYPE),
], ids=["annual", "calendar_quarter", "both"])
async def test_a_failed_benchmark_lookup_marks_the_build_degraded(monkeypatch, caplog, failed):
    lookup = _FakeLookup(failed=failed)
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    with caplog.at_level(logging.WARNING, logger=pp.logger.name):
        result, _n, degraded = await _bare_service(_fmp())._build_profit_power("CDNS")
    assert lookup.calls == ["annual", "calendar_quarter"]
    assert degraded == ["benchmarks"]
    assert result.degraded == ["benchmarks"], "the served value names its own hole"
    assert result.annual and result.quarterly, "the company's margins are still served"
    assert "benchmark lookup FAILED" in caplog.text and "CDNS" in caplog.text


@pytest.mark.asyncio
async def test_a_real_empty_peer_group_is_not_degraded(monkeypatch):
    """Negative control: a plain empty answer ("no rows for this peer group") is a fact
    about the company, not an outage."""
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: _FakeLookup())
    result, _n, degraded = await _bare_service(_fmp())._build_profit_power("CDNS")
    assert degraded == [] and result.degraded == []
    assert result.peer_group_level is None


_APPLE = [_q("2024-12-28", "Q1", "2025"), _q("2025-03-29", "Q2", "2025"),
          _q("2025-06-28", "Q3", "2025"), _q("2025-09-27", "Q4", "2025")]


@pytest.mark.asyncio
async def test_a_failed_calendar_quarter_read_degrades_an_off_calendar_company_too(monkeypatch):
    """The interim rule skipped Apple's quarterly read (decision 4's hide), so it could not
    fail. The read runs now and its line is drawn, so a failed ``calendar_quarter`` read is
    a real hole: degraded, the quarterly peer values missing, the annual line intact."""
    lookup = _FakeLookup(annual={"net_margin": {"2025": _cell(0.2, 40)}},
                         failed=(CALENDAR_QUARTER_PERIOD_TYPE,))
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    result, _n, degraded = await _bare_service(_fmp(quarterly=_APPLE))._build_profit_power("AAPL")
    assert lookup.calls == ["annual", "calendar_quarter"]
    assert degraded == ["benchmarks"] and result.degraded == ["benchmarks"]
    assert len(result.quarterly) == 4, "the company's own quarters still chart"
    assert all(p.sector_average_net_margin is None for p in result.quarterly)
    assert {p.period: p.sector_average_net_margin for p in result.annual}["2025"] == 20.0

    # Negative control: a healthy read is complete AND draws Apple's quarterly peers.
    healthy = _FakeLookup(annual={"net_margin": {"2025": _cell(0.2, 40)}},
                          quarterly={"net_margin": {"Q4'24": _cell(0.25, 40)}})
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: healthy)
    result, _n, degraded = await _bare_service(_fmp(quarterly=_APPLE))._build_profit_power("AAPL")
    assert degraded == []
    assert {p.period: p.sector_average_net_margin for p in result.quarterly}["Q1 '25"] == 25.0


def _spy_persist(monkeypatch, svc) -> List[Any]:
    persisted: List[Any] = []

    def _record(ticker, result, next_earnings):
        persisted.append((ticker, result))

    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", _record)
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
@pytest.mark.parametrize("failed,should_persist", [
    (("annual",), False), ((CALENDAR_QUARTER_PERIOD_TYPE,), False), ((), True),
])
async def test_a_failed_lookup_build_is_served_but_never_persisted(
    monkeypatch, failed, should_persist,
):
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: _FakeLookup(failed=failed))
    svc = _bare_service(_fmp())
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    pp._cache.clear()
    pp._inflight.clear()
    persisted = _spy_persist(monkeypatch, svc)
    try:
        result = await svc.get_profit_power("CDNS")
        assert result.annual, "served either way"
        assert (persisted != []) == should_persist
        # The 60 s Tier-1 TTL applies to the degraded build.
        assert bool(pp._cache["profit_power:CDNS"][1].degraded) == (not should_persist)
    finally:
        pp._cache.clear()


# ── R38: Cay AI's profit summary on a trailing revenue gap ────────────────────


def _gap_latest(**kw) -> ProfitPowerResponse:
    return ProfitPowerResponse(
        symbol="XBIO", quarterly=[],
        annual=[
            ProfitPowerDataPointSchema(period="2022", net_margin=15.0),
            ProfitPowerDataPointSchema(period="2023", gross_margin=60.0, operating_margin=25.0,
                                       net_margin=20.0, sector_average_net_margin=11.0),
            ProfitPowerDataPointSchema(period="2024", sector_average_net_margin=11.5),
            ProfitPowerDataPointSchema(period="2025", sector_average_net_margin=12.0),
        ],
        **kw,
    )


async def _summary(monkeypatch, data) -> str:
    from app.services import chat_service as cs

    class _PP:
        async def get_profit_power(self, ticker):
            return data

    # `_get_profit_summary` imports the getter function-scoped from its SOURCE module.
    monkeypatch.setattr(pp, "get_profit_power_service", lambda: _PP())
    svc = cs.ChatService.__new__(cs.ChatService)
    return await svc._get_profit_summary("XBIO")


@pytest.mark.asyncio
async def test_a_trailing_gap_year_says_not_available_and_names_the_last_real_year(monkeypatch):
    text = await _summary(monkeypatch, _gap_latest(peer_group_level="industry"))
    assert ": ." not in text and ":." not in text
    assert "XBIO (FY2025): not available" in text
    assert "Most recent year with margins: FY2023: Gross 60.0%, Operating 25.0%, Net 20.0%" in text
    # The peer figure is named as a PEER figure, under its own year, never bare.
    assert "Industry peer-group median net margin: 12.0% (peers, not XBIO; latest available peer reading; it may be from a year before FY2025)." in text
    assert "Sector avg net margin 12.0%" not in text


@pytest.mark.asyncio
async def test_an_all_gap_tail_without_any_margin_year_still_reads_honestly(monkeypatch):
    data = ProfitPowerResponse(symbol="XBIO", quarterly=[], annual=[
        ProfitPowerDataPointSchema(period="2025"),
    ])
    text = await _summary(monkeypatch, data)
    assert text == ("Latest annual margins for XBIO (FY2025): not available "
                    "(no positive revenue reported that year).")


@pytest.mark.asyncio
async def test_a_normal_latest_year_lists_its_margins_and_a_labelled_peer_figure(monkeypatch):
    data = ProfitPowerResponse(symbol="XBIO", quarterly=[], annual=[
        ProfitPowerDataPointSchema(period="2025", gross_margin=60.0, operating_margin=25.0,
                                   net_margin=-3.5, fcf_margin=12.0,
                                   sector_average_net_margin=12.0),
    ])
    text = await _summary(monkeypatch, data)
    assert text == ("Latest annual margins for XBIO (FY2025): Gross 60.0%, Operating 25.0%, "
                    "Net -3.5%, FCF 12.0%; Sector peer-group median net margin 12.0% (latest available peer reading; it may be from a year before FY2025).")


@pytest.mark.asyncio
async def test_a_failed_profit_power_fetch_yields_no_summary(monkeypatch, caplog):
    from app.services import chat_service as cs

    class _PP:
        async def get_profit_power(self, ticker):
            raise RuntimeError("boom")

    monkeypatch.setattr(pp, "get_profit_power_service", lambda: _PP())
    svc = cs.ChatService.__new__(cs.ChatService)
    with caplog.at_level(logging.WARNING, logger=cs.logger.name):
        assert await svc._get_profit_summary("XBIO") is None
    assert "RuntimeError: boom" in caplog.text and "XBIO" in caplog.text


# ── payload version: a first-pass (v2) row is rebuilt, not served for 24h ────


def test_a_first_pass_v2_row_is_rebuilt():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    body = ProfitPowerResponse(symbol="CDNS", annual=[], quarterly=[
        ProfitPowerDataPointSchema(period="Q4 '25", net_margin=10.0,
                                   sector_average_net_margin=9.0),   # the misjoined value
    ]).model_dump()

    def _supabase(json_data):
        class _Q:
            def select(self, *a, **k):
                return self

            eq = limit = select

            def execute(self):
                return SimpleNamespace(data=[{
                    "response_json": json_data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": None,
                }])

        return SimpleNamespace(table=lambda name: _Q())

    svc = _bare_service(None)
    assert pp._PP_PAYLOAD_VERSION >= 3
    svc.supabase = _supabase({**body, "payload_version": 2})
    assert svc._check_supabase_cache("CDNS") is None
    svc.supabase = _supabase({**body, "payload_version": pp._PP_PAYLOAD_VERSION})
    assert svc._check_supabase_cache("CDNS") is not None
