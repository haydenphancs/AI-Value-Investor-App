"""Profit Power deep-check fixes (2026-09-30) — backend half.

Each test asserts the CORRECT DEGRADED behaviour on an outlier input, not just the happy
path:

  #41  Negative revenue sign-flipped every margin: a $1.25B loss on −$1.1B revenue read
       +113.6% net margin and the profitability snapshot scored it 5/5. Now a gap (None),
       and the snapshot's TTM fallback cannot bring the flipped ratio back.
  #44  A zero / missing-revenue year was DROPPED, so `annual[-1]` was an older year that
       the snapshot presented and scored as current. Now kept as an all-None gap; only a
       LEADING run of gaps is trimmed. A TTM ratio of exactly 0 is not a margin.
  #17/#40  A tiny-revenue period's −40,000% is REAL data: kept (iOS pins it on the chart)
       and logged.
  #42  Thin (n < 20) benchmark cells were drawn as the latest "Industry Avg"; now held
       back to the latest mature median at or before them, like Growth.
  #76  Annual labels were the period-end calendar year: a 52/53-week filer read
       2019, 2021, 2022, 2022. Now the fiscal year.
  Decision 4  FINAL rule (calendar-quarter re-key): quarterly peers are read from period_type
       ``calendar_quarter`` and every quarter joins on the CALENDAR quarter its period ends
       in, so an off-calendar filer (Apple, Oracle) meets its peers' SAME calendar quarter.
       The interim off-calendar hide is gone; the quarterly read is never skipped.
  degraded  The build carries its own `degraded` list, is never persisted when partial,
       and expires from memory after a short TTL; old Supabase rows are rebuilt.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.integrations.fmp import FMPRateLimitException
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services import profit_power_service as pp
from app.services.profit_power_service import _build_margin_points, _compute_margin
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE

_MARGINS = ("gross_margin", "operating_margin", "fcf_margin", "net_margin")
_PROFILE = {"symbol": "ZZZ", "sector": "Technology", "industry": "Semiconductors"}


# ── shared harness ────────────────────────────────────────────────────────────


class _FakeFMP:
    """Per-method answers; an Exception answer is RAISED like a failed leg. Statement
    calls are keyed by (method, period)."""

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
    """`get_benchmarks` answers rich cells per period type and records each call.

    ``quarterly`` holds the CALENDAR-quarter cells (period_type ``calendar_quarter``); any
    other type — the legacy fiscal-keyed ``"quarterly"`` rows included — answers "no rows"
    unless a test plants decoys there via ``_by_type``."""

    def __init__(self, annual: Dict[str, Any], quarterly: Dict[str, Any] | None = None):
        self._by_type = {"annual": annual, CALENDAR_QUARTER_PERIOD_TYPE: quarterly or {}}
        self.calls: List[str] = []

    def get_benchmarks(self, industry, sector, metrics, period_type):
        self.calls.append(period_type)
        src = self._by_type.get(period_type, {})
        return {m: dict(src.get(m, {})) for m in metrics}

    def get_benchmark_values(self, *a, **k):  # pragma: no cover - must not be used
        raise AssertionError("profit_power must read RICH cells (sample size) now")


def _bare_service(fmp) -> pp.ProfitPowerService:
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.fmp = fmp
    svc.supabase = None
    return svc


def _cell(value, n, level="industry"):
    return {"value": value, "n": n, "level": level, "peer_group_name": "x"}


def _annual_row(date, fy, revenue=100.0, net=10.0, **extra):
    row = {"date": date, "fiscalYear": fy, "period": "FY", "revenue": revenue,
           "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": net}
    row.update(extra)
    return row


# ── #41: negative revenue never sign-flips a margin ───────────────────────────


@pytest.mark.parametrize(
    "numerator,revenue,expected",
    [
        (-50.0, -5.0, None),      # both negative: would read +1000%
        (5.0, -5.0, None),        # negative revenue: would read -100%
        (10.0, 0.0, None),
        (10.0, None, None),
        (None, 100.0, None),
        (10.0, 100.0, 10.0),
        (-8e7, 2e5, -40000.0),    # tiny but POSITIVE revenue: real data, kept
    ],
)
def test_compute_margin_refuses_non_positive_revenue(numerator, revenue, expected):
    assert _compute_margin(numerator, revenue) == expected


def test_negative_revenue_row_is_a_gap_not_a_flipped_margin(caplog):
    income = [
        _annual_row("2024-12-31", "2024", revenue=1.0e9, net=1.0e8),
        {"date": "2025-12-31", "fiscalYear": "2025", "revenue": -1.1e9,
         "grossProfit": -1.1e9, "operatingIncome": -1.2e9, "netIncome": -1.25e9},
    ]
    cash = [{"date": "2025-12-31", "freeCashFlow": -5e7}]
    with caplog.at_level(logging.WARNING, logger=pp.logger.name):
        points = _build_margin_points(income, cash, is_quarterly=False, ticker="MREIT")
    assert [p["period"] for p in points] == ["2024", "2025"], "the year is kept, not dropped"
    flipped = points[-1]
    assert all(flipped[k] is None for k in _MARGINS), flipped
    assert "NEGATIVE revenue" in caplog.text and "MREIT" in caplog.text


def test_negative_revenue_quarter_is_a_gap_too():
    income = [
        {"date": "2025-03-31", "period": "Q1", "fiscalYear": "2025", "revenue": 100.0,
         "netIncome": 10.0},
        {"date": "2025-06-30", "period": "Q2", "fiscalYear": "2025", "revenue": -40.0,
         "netIncome": -60.0},
    ]
    points = _build_margin_points(income, [], is_quarterly=True)
    assert len(points) == 2
    assert points[-1]["net_margin"] is None


# ── #44: a zero / missing-revenue year is kept, so the latest year is the latest ──


def test_trailing_no_revenue_years_are_kept_as_gaps():
    income = [
        _annual_row("2023-12-31", "2023", revenue=50.0, net=10.0),
        _annual_row("2024-12-31", "2024", revenue=0.0, net=-30.0),
        _annual_row("2025-12-31", "2025", revenue=None, net=-40.0),
    ]
    points = _build_margin_points(income, [], is_quarterly=False)
    assert [p["period"] for p in points] == ["2023", "2024", "2025"]
    assert points[0]["net_margin"] == 20.0
    for gap in points[1:]:
        assert all(gap[k] is None for k in _MARGINS), gap


def test_leading_no_revenue_years_are_trimmed_and_an_all_gap_history_is_empty():
    income = [
        _annual_row("2019-12-31", "2019", revenue=0.0),
        _annual_row("2020-12-31", "2020", revenue=None),
        _annual_row("2021-12-31", "2021", revenue=100.0, net=5.0),
        _annual_row("2022-12-31", "2022", revenue=0.0),
        _annual_row("2023-12-31", "2023", revenue=100.0, net=7.0),
    ]
    points = _build_margin_points(income, [], is_quarterly=False)
    # Leading pre-revenue years trimmed; the INTERIOR gap stays (the line breaks there).
    assert [p["period"] for p in points] == ["2021", "2022", "2023"]
    assert points[1]["net_margin"] is None
    # A pre-revenue company: nothing to chart → the card's "not available" state.
    assert _build_margin_points(income[:2], [], is_quarterly=False) == []


def test_a_row_without_any_usable_label_is_still_skipped():
    income = [{"date": None, "revenue": 100.0, "netIncome": 10.0},
              _annual_row("2024-12-31", "2024")]
    assert [p["period"] for p in _build_margin_points(income, [], is_quarterly=False)] == ["2024"]


# ── #17 / #40: an extreme but real margin is served unchanged (and logged) ────


def test_tiny_revenue_margin_reaches_the_tooltip_unchanged(caplog):
    income = [
        {"date": "2025-03-31", "period": "Q1", "fiscalYear": "2025", "revenue": 2.0e5,
         "grossProfit": 1.0e5, "operatingIncome": -7.8e7, "netIncome": -8.0e7},
    ]
    with caplog.at_level(logging.WARNING, logger=pp.logger.name):
        points = _build_margin_points(income, [], is_quarterly=True, ticker="BIO")
    assert points[0]["net_margin"] == -40000.0
    assert points[0]["operating_margin"] == -39000.0
    assert "|margin| >" in caplog.text and "BIO" in caplog.text


# ── #76: annual labels are the FISCAL year ────────────────────────────────────


_CDNS = [
    ("2019-12-28", "2019"),
    ("2021-01-02", "2020"),
    ("2022-01-01", "2021"),
    ("2022-12-31", "2022"),
]


@pytest.mark.parametrize("with_fiscal_year", [True, False])
def test_52_53_week_year_ends_get_one_label_per_fiscal_year(with_fiscal_year):
    income = [
        _annual_row(d, fy if with_fiscal_year else None, revenue=100.0 + i, net=10.0)
        for i, (d, fy) in enumerate(_CDNS)
    ]
    points = _build_margin_points(income, [], is_quarterly=False)
    assert [p["period"] for p in points] == ["2019", "2020", "2021", "2022"]
    # The benchmark join uses the same fiscal-year key, so the two rows ending in
    # calendar 2022 no longer join the same "2022" peer cell.
    assert [p["_match_period"] for p in points] == ["2019", "2020", "2021", "2022"]


def test_a_remaining_duplicate_annual_label_keeps_the_latest_row(caplog):
    income = [
        _annual_row("2024-06-30", "2024", revenue=100.0, net=5.0),
        _annual_row("2024-12-31", "2024", revenue=100.0, net=9.0),   # restated / FYE change
        _annual_row("2025-12-31", "2025", revenue=100.0, net=11.0),
    ]
    with caplog.at_level(logging.WARNING, logger=pp.logger.name):
        points = _build_margin_points(income, [], is_quarterly=False, ticker="DUP")
    assert [p["period"] for p in points] == ["2024", "2025"]
    assert points[0]["net_margin"] == 9.0, "the LATEST-dated 2024 row wins"
    assert "duplicate annual period" in caplog.text


def test_quarterly_labels_are_unchanged_fiscal_display_calendar_join():
    income = [{"date": "2025-08-31", "period": "Q1", "fiscalYear": "2026",
               "revenue": 100.0, "netIncome": 10.0}]
    (point,) = _build_margin_points(income, [], is_quarterly=True)
    assert point["period"] == "Q1 '26"
    # Oracle's fiscal Q1 FY26 is Jun-Aug 2025: it joins the CALENDAR quarter its period
    # ends in (Q3'25) — never the legacy "Q1'25" (peers' Jan-Mar 2025), and no longer ""
    # (the interim no-peer-value rule). test_profit_power_round2.py pins the cases.
    assert point["_match_period"] == "Q3'25"


# ── service-level harness: benchmarks, decision 4, degraded ───────────────────


def _calendar_quarters():
    return [
        {"date": "2025-12-31", "period": "Q4", "fiscalYear": "2025", "revenue": 100.0,
         "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": 10.0},
        {"date": "2026-03-31", "period": "Q1", "fiscalYear": "2026", "revenue": 100.0,
         "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": 12.0},
    ]


def _apple_quarters():
    # Fiscal Q1 = Oct-Dec: every quarter's number differs from its calendar quarter.
    return [
        {"date": "2024-12-28", "period": "Q1", "fiscalYear": "2025", "revenue": 100.0,
         "netIncome": 25.0},
        {"date": "2025-03-29", "period": "Q2", "fiscalYear": "2025", "revenue": 100.0,
         "netIncome": 24.0},
        {"date": "2025-06-28", "period": "Q3", "fiscalYear": "2025", "revenue": 100.0,
         "netIncome": 23.0},
        {"date": "2025-09-27", "period": "Q4", "fiscalYear": "2025", "revenue": 100.0,
         "netIncome": 26.0},
    ]


def _fmp(annual=None, quarterly=None, **overrides):
    answers = {
        "get_company_profile": dict(_PROFILE),
        "get_income_statement:annual": annual if annual is not None else [
            _annual_row("2025-06-30", "2025", revenue=100.0, net=30.0),
            _annual_row("2026-06-30", "2026", revenue=100.0, net=35.0),
        ],
        "get_income_statement:quarter": quarterly if quarterly is not None else _calendar_quarters(),
        "get_cash_flow_statement:annual": [],
        "get_cash_flow_statement:quarter": [],
        "get_earning_calendar_full": [],
    }
    answers.update(overrides)
    return _FakeFMP(**answers)


_ANNUAL_CELLS = {
    "net_margin": {"2025": _cell(0.12, 77), "2026": _cell(0.40, 9)},
    "gross_margin": {"2025": _cell(0.55, 77), "2026": _cell(0.70, 9)},
}
_QUARTERLY_CELLS = {
    "net_margin": {"Q4'25": _cell(0.10, 30), "Q1'26": _cell(0.50, 6)},
}


@pytest.mark.asyncio
async def test_thin_benchmark_cells_are_held_back_to_the_last_mature_median(monkeypatch):
    lookup = _FakeLookup(_ANNUAL_CELLS, _QUARTERLY_CELLS)
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    result, _next, degraded = await _bare_service(_fmp())._build_profit_power("ZZZ")

    assert degraded == [] and result.degraded == []
    by_period = {p.period: p for p in result.annual}
    # FY2026 (ends June 2026) joins the thin n=9 "2026" cell: NOT its 40%, the mature 12%.
    assert by_period["2026"].sector_average_net_margin == 12.0
    assert by_period["2026"].sector_average_gross_margin == 55.0
    assert by_period["2025"].sector_average_net_margin == 12.0
    # The quarterly twin across a year boundary: Q1'26 (n=6) holds back to Q4'25.
    q = {p.period: p for p in result.quarterly}
    assert q["Q1 '26"].sector_average_net_margin == 10.0
    assert q["Q4 '25"].sector_average_net_margin == 10.0
    # The peer-group vote still reads the un-held rich cells.
    assert result.peer_group_level == "industry"
    assert lookup.calls == ["annual", "calendar_quarter"]


@pytest.mark.asyncio
async def test_a_thin_cell_with_no_mature_cell_before_it_keeps_its_own_value(monkeypatch):
    lookup = _FakeLookup({"net_margin": {"2026": _cell(0.40, 9)}})
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    result, _n, _d = await _bare_service(_fmp())._build_profit_power("ZZZ")
    by_period = {p.period: p for p in result.annual}
    assert by_period["2026"].sector_average_net_margin == 40.0   # matches Growth
    assert by_period["2025"].sector_average_net_margin is None, "never a LATER period's value"


@pytest.mark.asyncio
async def test_a_nan_benchmark_cell_drops_one_peer_point_loudly(monkeypatch, caplog):
    """A NaN median would 500 the whole response at JSON encoding (allow_nan=False)."""
    lookup = _FakeLookup({"net_margin": {"2025": _cell(float("nan"), 77),
                                         "2026": _cell(0.12, 77)}})
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    with caplog.at_level(logging.WARNING, logger=pp.logger.name):
        result, _n, _d = await _bare_service(_fmp())._build_profit_power("ZZZ")
    by_period = {p.period: p for p in result.annual}
    assert by_period["2025"].sector_average_net_margin is None
    assert by_period["2026"].sector_average_net_margin == 12.0
    assert "unusable benchmark cell" in caplog.text
    result.model_dump_json()  # serialisable


# One DISTINCT net-margin median per calendar quarter (all mature), plus the two legacy
# keys Apple's rows used to join ("Q1'24", "Q4'25") as decoys: a point that joined the
# legacy "<fiscal Q>'<end year>" key would read a visibly wrong number.
_APPLE_CAL_CELLS = {"net_margin": {
    "Q4'24": _cell(0.21, 40), "Q1'25": _cell(0.22, 40), "Q2'25": _cell(0.23, 40),
    "Q3'25": _cell(0.24, 40),
    "Q1'24": _cell(0.91, 40), "Q4'25": _cell(0.94, 40),
}}
# Apple fiscal display label → the calendar-quarter cell its period end falls in.
_APPLE_JOIN = {"Q1 '25": 21.0, "Q2 '25": 22.0, "Q3 '25": 23.0, "Q4 '25": 24.0}


@pytest.mark.asyncio
async def test_off_calendar_quarters_join_their_same_calendar_quarter_peers(monkeypatch):
    """Apple's fiscal Q1 is Oct-Dec. The interim rule hid its whole quarterly peer line;
    the final rule draws, for each quarter, the median of the calendar quarter it ENDS in
    (fiscal Q1 FY25, ends 2024-12-28 → Q4'24), and the annual line is unchanged."""
    lookup = _FakeLookup(_ANNUAL_CELLS, _APPLE_CAL_CELLS)
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    result, _n, degraded = await _bare_service(_fmp(quarterly=_apple_quarters()))._build_profit_power(
        "ZZZ"
    )
    assert degraded == []
    assert len(result.quarterly) == 4, "the company's own quarters still chart"
    assert {p.period: p.sector_average_net_margin for p in result.quarterly} == _APPLE_JOIN
    for p in result.quarterly:
        assert p.sector_average_net_margin not in (91.0, 94.0), f"{p.period}: legacy-key join"
        # No cells for the other margins → no peer value, not a borrowed one.
        assert p.sector_average_gross_margin is None
        assert p.sector_average_operating_margin is None
        assert p.sector_average_fcf_margin is None
    assert any(p.sector_average_net_margin is not None for p in result.annual), (
        "the ANNUAL peer line stays"
    )
    assert lookup.calls == ["annual", "calendar_quarter"], (
        "the quarterly lookup runs for an off-calendar company too"
    )


@pytest.mark.asyncio
async def test_quarterly_lookup_reads_calendar_quarter_rows_never_legacy_quarterly(monkeypatch):
    """The quarterly read asks for ``calendar_quarter``; the legacy fiscal-keyed
    ``quarterly`` rows (planted here with decoy values under the SAME labels) are never
    read and never reach a point."""
    assert CALENDAR_QUARTER_PERIOD_TYPE == "calendar_quarter"
    lookup = _FakeLookup(_ANNUAL_CELLS, {"net_margin": {
        "Q4'25": _cell(0.10, 30), "Q1'26": _cell(0.11, 30),
    }})
    lookup._by_type["quarterly"] = {"net_margin": {
        "Q4'25": _cell(0.77, 30), "Q1'26": _cell(0.88, 30),
    }}
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    result, _n, degraded = await _bare_service(_fmp())._build_profit_power("ZZZ")
    assert degraded == []
    assert lookup.calls == ["annual", "calendar_quarter"]
    assert "quarterly" not in lookup.calls
    assert {p.period: p.sector_average_net_margin for p in result.quarterly} == {
        "Q4 '25": 10.0, "Q1 '26": 11.0,
    }


@pytest.mark.asyncio
async def test_calendar_quarters_keep_the_quarterly_peer_line(monkeypatch):
    lookup = _FakeLookup(_ANNUAL_CELLS, _QUARTERLY_CELLS)
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    result, _n, _d = await _bare_service(_fmp())._build_profit_power("ZZZ")
    assert all(p.sector_average_net_margin is not None for p in result.quarterly)


@pytest.mark.asyncio
async def test_a_failed_leg_is_named_on_the_served_response(monkeypatch):
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: _FakeLookup({}))
    fmp = _fmp(**{"get_cash_flow_statement:quarter": FMPRateLimitException("429")})
    result, _n, degraded = await _bare_service(fmp)._build_profit_power("ZZZ")
    assert degraded == ["quarterly_cashflow"]
    assert result.degraded == ["quarterly_cashflow"], "the field reflects the served build"
    assert all(p.fcf_margin is None for p in result.quarterly)


# ── get_profit_power: degraded builds are never persisted and expire fast ────


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
@pytest.mark.parametrize("failed_leg,should_persist", [
    ("get_income_statement:quarter", False),
    (None, True),     # negative control: a complete build IS persisted
])
async def test_degraded_build_is_served_but_never_persisted(
    monkeypatch, failed_leg, should_persist,
):
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: _FakeLookup({}))
    overrides = {failed_leg: FMPRateLimitException("429")} if failed_leg else {}
    svc = _bare_service(_fmp(**overrides))
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    pp._cache.clear()
    pp._inflight.clear()
    persisted = _spy_persist(monkeypatch, svc)
    try:
        result = await svc.get_profit_power("ZZZ")
        assert result.annual, "the build is still SERVED"
        assert (persisted != []) == should_persist
        assert bool(result.degraded) == (not should_persist)
        assert "profit_power:ZZZ" in pp._cache, "the memory tier still absorbs retries"
    finally:
        pp._cache.clear()


def test_a_degraded_memory_entry_expires_after_the_short_ttl(monkeypatch):
    pp._cache.clear()
    degraded = ProfitPowerResponse(symbol="ZZZ", annual=[], quarterly=[],
                                   degraded=["quarterly_income"])
    complete = ProfitPowerResponse(symbol="ZZZ", annual=[], quarterly=[])
    now = [1_000_000.0]
    monkeypatch.setattr(pp.time, "time", lambda: now[0])
    try:
        pp._cache_set("d", degraded)
        pp._cache_set("c", complete)
        now[0] += pp._DEGRADED_CACHE_TTL + 1
        assert pp._cache_get("d") is None, "a partial build must not linger for 5 minutes"
        assert pp._cache_get("c") is complete
        now[0] += pp._CACHE_TTL
        assert pp._cache_get("c") is None
    finally:
        pp._cache.clear()


def test_upsert_refuses_a_degraded_build_and_versions_a_complete_one():
    writes: List[Dict[str, Any]] = []

    class _Table:
        def upsert(self, row, on_conflict=None):
            writes.append(row)
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    svc = _bare_service(None)
    svc.supabase = SimpleNamespace(table=lambda name: _Table())
    svc._upsert_supabase_cache_safe(
        "ZZZ", ProfitPowerResponse(symbol="ZZZ", annual=[], quarterly=[], degraded=["profile"]),
        None,
    )
    assert writes == []
    svc._upsert_supabase_cache_safe(
        "ZZZ", ProfitPowerResponse(symbol="ZZZ", annual=[], quarterly=[]), None,
    )
    assert writes[0]["response_json"]["payload_version"] == pp._PP_PAYLOAD_VERSION


def _row(json_data):
    class _Q:
        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            from datetime import datetime, timezone
            return SimpleNamespace(data=[{
                "response_json": json_data,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "next_earnings_date": None,
            }])

    return SimpleNamespace(table=lambda name: _Q())


def test_a_row_written_before_the_payload_version_is_rebuilt():
    body = ProfitPowerResponse(
        symbol="ZZZ", quarterly=[],
        annual=[ProfitPowerDataPointSchema(period="2025", net_margin=113.64)],
    ).model_dump()
    svc = _bare_service(None)
    svc.supabase = _row(body)                       # no payload_version: old meaning
    assert svc._check_supabase_cache("ZZZ") is None
    svc.supabase = _row({**body, "payload_version": pp._PP_PAYLOAD_VERSION})
    served = svc._check_supabase_cache("ZZZ")
    assert served is not None and served.degraded == []


# ── the profitability snapshot cannot revive a flipped or fabricated margin ───


class _StubCurrentLookup:
    def get_current_benchmark_values(self, industry, sector, metrics):
        return {m: 0.08 for m in metrics}


def _snapshot_service(monkeypatch, pp_result, ratios):
    from app.services import profit_power_service
    from app.services import profitability_snapshot_service as ps

    class _ProfitPower:
        async def get_profit_power(self, ticker):
            return pp_result

    monkeypatch.setattr(profit_power_service, "get_profit_power_service", lambda: _ProfitPower())
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup", lambda: _StubCurrentLookup())
    svc = ps.ProfitabilitySnapshotService.__new__(ps.ProfitabilitySnapshotService)
    svc.supabase = None
    svc.fmp = _FakeFMP(
        get_key_metrics_ttm=[{"returnOnEquityTTM": 0.2, "returnOnAssetsTTM": 0.05}],
        get_company_profile=dict(_PROFILE),
        get_ratios_ttm=ratios,
    )
    return ps, svc


def _gap_latest(**kw) -> ProfitPowerResponse:
    return ProfitPowerResponse(
        symbol="ZZZ", quarterly=[],
        annual=[
            ProfitPowerDataPointSchema(period="2023", gross_margin=60.0, operating_margin=25.0,
                                       net_margin=20.0),
            ProfitPowerDataPointSchema(period="2024"),          # no-revenue year: a gap
        ],
        **kw,
    )


def _metric(snapshot, key):
    return next(m for m in snapshot.metrics if m.metric_key == key)


@pytest.mark.asyncio
async def test_snapshot_never_presents_an_older_year_as_current(monkeypatch):
    _ps, svc = _snapshot_service(monkeypatch, _gap_latest(), [])
    snap, _deg = await svc._compute_with_status("ZZZ")
    net = _metric(snap, "net_margin")
    assert net.value == "—", "the 2023 20% must not be shown as the current margin"
    assert net.score is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ratios,expected_value,expected_score_is_none", [
    # Negative TTM revenue: FMP's ratio is the same sign-flipped netIncome/revenue.
    ([{"netProfitMarginTTM": 1.136, "revenuePerShareTTM": -3.0}], "—", True),
    # Zero-revenue TTM: FMP answers 0 — a fabricated "0.00%", not a margin.
    ([{"netProfitMarginTTM": 0.0, "revenuePerShareTTM": 5.0}], "—", True),
    # Revenue/share unknown: refuse rather than guess the sign.
    ([{"netProfitMarginTTM": 0.12}], "—", True),
    # Non-finite ratio.
    ([{"netProfitMarginTTM": "NaN", "revenuePerShareTTM": 5.0}], "—", True),
    # A real TTM margin on positive TTM revenue still fills the gap.
    ([{"netProfitMarginTTM": 0.12, "revenuePerShareTTM": 5.0}], "12.00%", False),
])
async def test_snapshot_ttm_fallback_is_guarded(
    monkeypatch, ratios, expected_value, expected_score_is_none,
):
    _ps, svc = _snapshot_service(monkeypatch, _gap_latest(), ratios)
    snap, _deg = await svc._compute_with_status("ZZZ")
    net = _metric(snap, "net_margin")
    assert net.value == expected_value
    assert (net.score is None) == expected_score_is_none
    if expected_score_is_none:
        assert net.score != 5


@pytest.mark.asyncio
async def test_snapshot_marks_a_degraded_profit_power_build(monkeypatch):
    _ps, svc = _snapshot_service(
        monkeypatch, _gap_latest(degraded=["annual_income"]),
        [{"netProfitMarginTTM": 0.12, "revenuePerShareTTM": 5.0}],
    )
    _snap, degraded = await svc._compute_with_status("ZZZ")
    assert "profit_power" in degraded, "a partial Profit Power build must not be persisted"


@pytest.mark.asyncio
async def test_snapshot_complete_profit_power_build_is_not_degraded(monkeypatch):
    complete = ProfitPowerResponse(
        symbol="ZZZ", quarterly=[],
        annual=[ProfitPowerDataPointSchema(period="2025", gross_margin=60.0,
                                           operating_margin=25.0, net_margin=20.0)],
    )
    _ps, svc = _snapshot_service(monkeypatch, complete, [])
    snap, degraded = await svc._compute_with_status("ZZZ")
    assert degraded == []
    assert _metric(snap, "net_margin").value == "20.00%"
