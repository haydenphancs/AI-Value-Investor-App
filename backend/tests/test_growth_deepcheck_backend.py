"""Growth deep-check (2026-09-30): the backend half of findings #4, #31, #33, #34, #73.

* #4  — YoY is paired by period-END DATE SPAN (350-385 days), never by year label, and an
        annual bar is labelled with its FISCAL year. 52/53-week filers (Disney, Cadence,
        Kellanova) used to get a 2-year "YoY", a null latest YoY, or duplicate/future
        labels.
* #31 — the growth snapshot reads only the LATEST annual point (no backward scan to an
        older year) and weights its rating over the metrics that have a value.
* #33 — `GrowthResponse.degraded` carries the failed FMP legs of the value actually
        served (fresh build, Tier-1 hit, in-flight join; empty on a Tier-2 hit).
* #34 — FINAL rule (calendar-quarter re-key, migration 184): quarterly peers are read
        from period_type ``calendar_quarter`` and every quarter joins on the CALENDAR
        quarter its period ends in, so an off-calendar fiscal year (Microsoft, Nvidia)
        meets its peers' SAME calendar quarter. The interim off-calendar hide is gone:
        the quarterly peer line is drawn for every company; annual is unchanged.
* #73 — `GrowthResponse.peer_group_levels` names the peer group (industry | sector) each
        series' dashed line comes from, by majority over the cells it draws.

Every case asserts the CORRECT DEGRADED behaviour (None / no key / not persisted), not
just the happy path. Hermetic: fake FMP, fake benchmark lookup, fake Supabase.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.integrations.fmp import FMPUnavailableException
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.services import growth_service as gmod
from app.services.growth_service import _compute_growth_points
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE


def _by_period(points: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for p in points:
        assert p["period"] not in out, f"duplicate label {p['period']!r}"
        out[p["period"]] = p
    return out


def _pct(cur: float, prev: float) -> float:
    return round((cur - prev) / abs(prev) * 100, 2)


# ── #4: date-span pairing ─────────────────────────────────────────────────────


def test_disney_q1_pairs_with_the_quarter_52_weeks_earlier():
    """Disney's fiscal Q1 ends on the Saturday nearest Dec 31, so two Q1s share the
    calendar year 2022 (2022-01-01 = FY22, 2022-12-31 = FY23). Year-label keying
    overwrote FY22 and compared FY23 with FY21: +44.68% instead of +7.75%."""
    rows = [
        {"date": "2019-12-28", "period": "Q1", "fiscalYear": "2020", "revenue": 20.86e9},
        {"date": "2021-01-02", "period": "Q1", "fiscalYear": "2021", "revenue": 16.25e9},
        {"date": "2022-01-01", "period": "Q1", "fiscalYear": "2022", "revenue": 21.82e9},
        {"date": "2022-12-31", "period": "Q1", "fiscalYear": "2023", "revenue": 23.51e9},
        {"date": "2023-12-30", "period": "Q1", "fiscalYear": "2024", "revenue": 23.55e9},
    ]
    by = _by_period(_compute_growth_points(rows, "revenue", is_quarterly=True))
    assert by["Q1 '23"]["yoy_change_percent"] == _pct(23.51e9, 21.82e9) == 7.75
    # Q1 FY21 (2021-01-02) is 371 days after Q1 FY20 (2019-12-28): a 53-week year,
    # computed — it used to be "—" because no (Q1, 2020) key existed.
    assert by["Q1 '21"]["yoy_change_percent"] == _pct(16.25e9, 20.86e9)
    assert by["Q1 '20"]["yoy_change_percent"] is None      # oldest: no base


@pytest.mark.parametrize("with_fiscal_year", [True, False])
def test_cadence_53_week_year_is_labelled_2025_with_a_real_yoy(with_fiscal_year):
    """FY2025 ended 2026-01-03. It was labelled "2026" (a year that has not ended) and
    its YoY was nulled as a '2024 -> 2026 year gap'."""
    annual = [
        {"date": "2022-12-31", "fiscalYear": "2022", "revenue": 3.56e9},
        {"date": "2023-12-30", "fiscalYear": "2023", "revenue": 4.09e9},
        {"date": "2024-12-28", "fiscalYear": "2024", "revenue": 4.64e9},
        {"date": "2026-01-03", "fiscalYear": "2025", "revenue": 5.30e9},
    ]
    if not with_fiscal_year:
        for r in annual:
            r.pop("fiscalYear")
    pts = _compute_growth_points(annual, "revenue", is_quarterly=False)
    assert [p["period"] for p in pts] == ["2022", "2023", "2024", "2025"]
    assert pts[-1]["yoy_change_percent"] == _pct(5.30e9, 4.64e9)
    # The benchmark JOIN key is the year of the period end minus 7 days
    # (annual_benchmark_key): FY2025 ended 2026-01-03 meets its December peers' "2025"
    # cell, not their not-yet-reported "2026" one. Shared with profit_power_service.
    assert pts[-1]["_match_period"] == "2025"
    assert pts[-1]["cal_year"] == "2026"


def test_cadence_q4_53_week_quarter_keeps_its_yoy():
    rows = [
        {"date": "2024-12-28", "period": "Q4", "fiscalYear": "2024", "revenue": 1.36e9},
        {"date": "2026-01-03", "period": "Q4", "fiscalYear": "2025", "revenue": 1.44e9},
    ]
    pts = _compute_growth_points(rows, "revenue", is_quarterly=True)
    assert pts[-1]["period"] == "Q4 '25"
    assert pts[-1]["yoy_change_percent"] == _pct(1.44e9, 1.36e9)


def test_kellanova_annual_labels_are_unique_and_fy2022_has_its_yoy():
    """FY2020 = 2021-01-02, FY2021 = 2022-01-01, FY2022 = 2022-12-31 with no fiscalYear:
    two bars both read "2022" and FY2022's real +8% became null."""
    rows = [
        {"date": "2021-01-02", "revenue": 13.77e9},
        {"date": "2022-01-01", "revenue": 14.18e9},
        {"date": "2022-12-31", "revenue": 15.32e9},
    ]
    by = _by_period(_compute_growth_points(rows, "revenue", is_quarterly=False))
    assert list(by) == ["2020", "2021", "2022"]
    assert by["2022"]["yoy_change_percent"] == _pct(15.32e9, 14.18e9)
    assert by["2021"]["yoy_change_percent"] == _pct(14.18e9, 13.77e9)


def test_fiscal_year_end_change_never_uses_the_stub_as_a_base():
    """Dec year-end → a 6-month transition period → June year-end. The stub has no YoY
    (182 days after FY2022) and the first full June year is NOT compared with the stub
    (365 days apart, but the stub covers six months: a doubled 'growth')."""
    rows = [
        {"date": "2021-12-31", "revenue": 90.0},
        {"date": "2022-12-31", "revenue": 100.0},
        {"date": "2023-06-30", "revenue": 52.0},   # 6-month stub
        {"date": "2024-06-30", "revenue": 110.0},
        {"date": "2025-06-30", "revenue": 121.0},
    ]
    pts = {p["_match_period"]: p for p in _compute_growth_points(rows, "revenue", is_quarterly=False)}
    assert pts["2022"]["yoy_change_percent"] == _pct(100.0, 90.0)
    assert pts["2023"]["value"] == 52.0                      # the stub still charts
    assert pts["2023"]["yoy_change_percent"] is None          # 182 days: not a year
    assert pts["2024"]["yoy_change_percent"] is None          # never vs the stub
    assert pts["2025"]["yoy_change_percent"] == _pct(121.0, 110.0)   # clean again


@pytest.mark.parametrize("gap_days,expect_yoy", [
    (349, False), (350, True), (364, True), (371, True), (385, True), (386, False),
    (730, False),   # a missing year: a 2-year change is not a YoY
])
def test_window_boundaries(gap_days, expect_yoy):
    from datetime import datetime, timedelta

    end = datetime(2025, 12, 31)
    prev = end - timedelta(days=gap_days)
    rows = [
        {"date": prev.strftime("%Y-%m-%d"), "period": "Q4", "revenue": 100.0},
        {"date": end.strftime("%Y-%m-%d"), "period": "Q4", "revenue": 120.0},
    ]
    for is_quarterly in (True, False):
        pts = _compute_growth_points(rows, "revenue", is_quarterly=is_quarterly)
        latest = max(pts, key=lambda p: p["value"])
        assert (latest["yoy_change_percent"] is not None) is expect_yoy, (gap_days, is_quarterly)
        if expect_yoy:
            assert latest["yoy_change_percent"] == 20.0


def test_annual_duplicate_row_for_the_same_date_does_not_null_the_yoy():
    """FMP occasionally returns a restated duplicate of a year. The old `i-1` pairing
    compared the year with its own duplicate (gap 0) and nulled the YoY."""
    rows = [
        {"date": "2023-12-31", "revenue": 100.0},
        {"date": "2024-12-31", "revenue": 110.0},
        {"date": "2024-12-31", "revenue": 110.0},
        {"date": "2025-12-31", "revenue": 121.0},
    ]
    pts = _compute_growth_points(rows, "revenue", is_quarterly=False)
    assert pts[-1]["yoy_change_percent"] == 10.0
    assert all(p["yoy_change_percent"] == 10.0 for p in pts[1:])


def test_quarterly_base_must_be_the_same_fiscal_quarter():
    """A row exactly a year earlier but labelled a DIFFERENT fiscal quarter (a fiscal
    calendar shift) is not a same-quarter base."""
    rows = [
        {"date": "2024-03-31", "period": "Q2", "revenue": 100.0},
        {"date": "2025-03-31", "period": "Q1", "revenue": 120.0},
    ]
    pts = _compute_growth_points(rows, "revenue", is_quarterly=True)
    assert len(pts) == 2
    assert all(p["yoy_change_percent"] is None for p in pts)


@pytest.mark.parametrize("bad_date", [None, "", "garbage", "2025-13-45"])
def test_unparseable_date_charts_the_bar_with_a_null_yoy(bad_date):
    rows = [
        {"date": "2024-03-31", "period": "Q1", "calendarYear": "2024", "revenue": 100.0},
        {"date": bad_date, "period": "Q1", "calendarYear": "2025", "revenue": 120.0},
    ]
    pts = _compute_growth_points(rows, "revenue", is_quarterly=True)
    assert len(pts) == 2, "a bad date must not drop a chartable bar"
    assert all(p["yoy_change_percent"] is None for p in pts)

    annual = [
        {"date": "2024-12-31", "calendarYear": "2024", "revenue": 100.0},
        {"date": bad_date, "calendarYear": "2025", "revenue": 120.0},
    ]
    pts = _compute_growth_points(annual, "revenue", is_quarterly=False)
    assert len(pts) == 2
    assert all(p["yoy_change_percent"] is None for p in pts)


def test_equidistant_bases_tie_break_on_the_prior_fiscal_year():
    """Two candidates 357 and 371 days back are equally far from 364; the one whose
    fiscalYear is exactly one lower is the year-ago quarter."""
    rows = [
        {"date": "2023-12-27", "period": "Q4", "fiscalYear": "2022", "revenue": 50.0},
        {"date": "2024-01-10", "period": "Q4", "fiscalYear": "2023", "revenue": 100.0},
        {"date": "2025-01-01", "period": "Q4", "fiscalYear": "2024", "revenue": 120.0},
    ]
    pts = _compute_growth_points(rows, "revenue", is_quarterly=True)
    assert pts[-1]["yoy_change_percent"] == 20.0   # vs FY2023's 100, not FY2022's 50


# ── service harness ───────────────────────────────────────────────────────────


def _q(date: str, period: str, fy: str, **vals: float) -> Dict[str, Any]:
    return {"date": date, "period": period, "fiscalYear": fy, **vals}


def _a(date: str, fy: str, **vals: float) -> Dict[str, Any]:
    return {"date": date, "period": "FY", "fiscalYear": fy, **vals}


_VALS = {"revenue": 100.0, "epsDiluted": 1.0, "netIncome": 10.0, "operatingIncome": 15.0}

# Calendar-quarter company: fiscal Q-number == calendar quarter of the period end.
_CAL_QUARTERS = [
    _q("2024-03-31", "Q1", "2024", **_VALS), _q("2024-06-30", "Q2", "2024", **_VALS),
    _q("2024-09-30", "Q3", "2024", **_VALS), _q("2024-12-31", "Q4", "2024", **_VALS),
    _q("2025-03-31", "Q1", "2025", **{**_VALS, "revenue": 110.0}),
    _q("2025-06-30", "Q2", "2025", **{**_VALS, "revenue": 110.0}),
]
# Microsoft-style: fiscal Q1 = Jul-Sep.
_MSFT_QUARTERS = [
    _q("2024-09-30", "Q1", "2025", **_VALS), _q("2024-12-31", "Q2", "2025", **_VALS),
    _q("2025-03-31", "Q3", "2025", **_VALS), _q("2025-06-30", "Q4", "2025", **_VALS),
    _q("2025-09-30", "Q1", "2026", **{**_VALS, "revenue": 110.0}),
    _q("2025-12-31", "Q2", "2026", **{**_VALS, "revenue": 110.0}),
]
_ANNUAL = [
    _a("2023-12-31", "2023", **_VALS),
    _a("2024-12-31", "2024", **{**_VALS, "revenue": 110.0}),
    _a("2025-12-31", "2025", **{**_VALS, "revenue": 121.0}),
]


def _cf(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"date": r["date"], "period": r["period"], "fiscalYear": r["fiscalYear"],
             "freeCashFlow": 5.0} for r in rows]


class _FakeFMP:
    """Statement legs by period; a value that is an Exception is RAISED (a failed leg)."""

    def __init__(self, *, annual=None, quarterly=None, cf_annual=None, cf_quarterly=None,
                 profile=None):
        self.answers = {
            ("income", "annual"): _ANNUAL if annual is None else annual,
            ("income", "quarter"): _CAL_QUARTERS if quarterly is None else quarterly,
            ("cash", "annual"): _cf(_ANNUAL) if cf_annual is None else cf_annual,
            ("cash", "quarter"): (
                _cf(_CAL_QUARTERS) if cf_quarterly is None else cf_quarterly
            ),
        }
        self.profile = (
            {"symbol": "TEST", "sector": "Technology", "industry": "Semiconductors"}
            if profile is None else profile
        )

    @staticmethod
    def _answer(value):
        if isinstance(value, BaseException):
            raise value
        return value

    async def get_company_profile(self, ticker):
        return self._answer(self.profile)

    async def get_income_statement(self, ticker, period="annual", limit=5):
        return self._answer(self.answers[("income", period)])

    async def get_cash_flow_statement(self, ticker, period="annual", limit=5):
        return self._answer(self.answers[("cash", period)])


def _cell(value: float, level: str, n: int = 50) -> Dict[str, Any]:
    return {"value": value, "level": level, "peer_group_name": "x", "n": n}


class _FakeLookup:
    """`get_benchmarks(industry, sector, metrics, period_type)` → rich cells.

    ``quarterly`` holds the CALENDAR-quarter cells (period_type ``calendar_quarter``).
    Any other period type — notably the legacy fiscal-keyed ``"quarterly"`` rows — answers
    "no rows", so a regression to the legacy read loses every quarterly peer value here
    instead of passing on the same cells."""

    def __init__(self, annual: Optional[Dict] = None, quarterly: Optional[Dict] = None):
        self.annual = annual or {}
        self.quarterly = quarterly or {}
        self.calls: List[Tuple[str, Tuple[str, ...]]] = []

    def get_benchmarks(self, industry, sector, metrics, period_type):
        self.calls.append((period_type, tuple(metrics)))
        source = {
            "annual": self.annual, CALENDAR_QUARTER_PERIOD_TYPE: self.quarterly,
        }.get(period_type, {})
        return {m: dict(source.get(m, {})) for m in metrics}


def _all_cells(labels: List[str], level: str, value: float = 7.0) -> Dict[str, Dict]:
    return {lab: _cell(value, level) for lab in labels}


_ANNUAL_KEYS = ["2023", "2024", "2025"]
_CAL_Q_KEYS = ["Q1'24", "Q2'24", "Q3'24", "Q4'24", "Q1'25", "Q2'25"]
# The MSFT rows' `_match_period` keys: the CALENDAR quarter each period ends in.
_MSFT_Q_KEYS = ["Q3'24", "Q4'24", "Q1'25", "Q2'25", "Q3'25", "Q4'25"]


def _service(monkeypatch, fmp: _FakeFMP, lookup: _FakeLookup) -> gmod.GrowthService:
    monkeypatch.setattr(gmod, "get_sector_benchmark_lookup", lambda: lookup)
    svc = gmod.GrowthService.__new__(gmod.GrowthService)
    svc.fmp = fmp
    svc.supabase = None
    return svc


def _yoy_metrics_lookup(annual_level="industry", quarterly_level="industry",
                        quarterly_keys=None) -> _FakeLookup:
    q_keys = quarterly_keys or sorted(set(_CAL_Q_KEYS) | set(_MSFT_Q_KEYS))
    metrics = ["eps_yoy", "revenue_yoy", "net_income_yoy", "operating_income_yoy", "fcf_yoy"]
    return _FakeLookup(
        annual={m: _all_cells(_ANNUAL_KEYS, annual_level) for m in metrics},
        quarterly={
            **{m: _all_cells(q_keys, quarterly_level) for m in metrics},
            **{m: _all_cells(q_keys, quarterly_level) for m in ("eps_qoq", "revenue_qoq")},
        },
    )


# ── #33: the degraded field ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_reports_a_failed_leg_on_the_response(monkeypatch):
    svc = _service(monkeypatch, _FakeFMP(quarterly=FMPUnavailableException("503")),
                   _yoy_metrics_lookup())
    response, degraded = await svc._build_growth("TEST")
    assert degraded == ["quarterly_income"]
    assert response.degraded == ["quarterly_income"]
    assert response.eps_quarterly == [] and response.revenue_quarterly == []
    assert response.revenue_annual, "the healthy legs still render"

    complete, none = await _service(monkeypatch, _FakeFMP(), _yoy_metrics_lookup())._build_growth("TEST")
    assert none == [] and complete.degraded == []


def _reset_tiers() -> None:
    gmod._cache.clear()
    gmod._inflight.clear()
    gmod._degraded_by_key.clear()


def _no_supabase(monkeypatch, svc, tier2=None) -> List[str]:
    persisted: List[str] = []
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: tier2)
    monkeypatch.setattr(svc, "_next_earnings_date_safe", lambda ticker: None)
    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe",
                        lambda ticker, *rest: persisted.append(ticker))
    return persisted


def _plain(degraded_on_object: List[str]) -> GrowthResponse:
    return GrowthResponse(
        symbol="TEST", eps_annual=[], eps_quarterly=[], revenue_annual=[
            GrowthDataPointSchema(period="2025", value=1.0, yoy_change_percent=5.0)],
        revenue_quarterly=[], degraded=degraded_on_object,
    )


@pytest.mark.asyncio
async def test_degraded_field_matches_the_value_served_on_every_path(monkeypatch):
    _reset_tiers()
    release = asyncio.Event()

    async def _slow_build(ticker):
        await release.wait()
        # The builder's object says nothing; the service must stamp the field itself.
        return _plain([]), ["annual_cashflow"]

    svc = _service(monkeypatch, _FakeFMP(), _FakeLookup())
    persisted = _no_supabase(monkeypatch, svc)
    monkeypatch.setattr(svc, "_build_growth", _slow_build)

    leader = asyncio.ensure_future(svc.get_growth_with_status("TEST"))
    for _ in range(3):
        await asyncio.sleep(0)
    assert "growth:TEST" in gmod._inflight, "no leader registered — the join is untested"
    joiner = asyncio.ensure_future(svc.get_growth("TEST"))          # the endpoint path
    await asyncio.sleep(0)
    release.set()
    (fresh, fresh_list), joined = await asyncio.gather(leader, joiner)
    assert fresh.degraded == fresh_list == ["annual_cashflow"]
    assert joined.degraded == ["annual_cashflow"], "in-flight join lost the flag"

    hit = await svc.get_growth("TEST")                                  # Tier-1 hit
    assert hit.degraded == ["annual_cashflow"]
    hit.degraded.append("mutated by a caller")
    again = await svc.get_growth("TEST")
    assert again.degraded == ["annual_cashflow"], "a caller mutated the shared Tier-1 object"
    await asyncio.sleep(0)
    assert persisted == [], "a degraded build reached the 24h tier"

    # A complete rebuild clears it; a Tier-2 row is complete by construction even if a
    # stray value sits in its JSON.
    _reset_tiers()

    async def _clean_build(ticker):
        return _plain(["stale-from-builder"]), []

    monkeypatch.setattr(svc, "_build_growth", _clean_build)
    assert (await svc.get_growth("TEST")).degraded == []

    _reset_tiers()
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: _plain(["stray"]))
    assert (await svc.get_growth("TEST")).degraded == []
    assert (await svc.get_growth("TEST")).degraded == []                # its Tier-1 hit
    _reset_tiers()


class _FakeTable:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows
        self.upserted: List[Dict[str, Any]] = []

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, payload, **_k):
        self.upserted.append(payload)
        return self

    def execute(self):
        class _R:
            pass

        r = _R()
        r.data = self.rows
        return r


def _row(response_json: Any) -> Dict[str, Any]:
    from datetime import datetime, timezone

    return {"response_json": response_json, "cached_at": datetime.now(timezone.utc).isoformat(),
            "next_earnings_date": None}


def test_tier2_rows_from_before_the_fix_are_rebuilt_and_new_rows_are_stamped():
    svc = gmod.GrowthService.__new__(gmod.GrowthService)
    body = _plain([]).model_dump()

    svc.supabase = _FakeTable([_row(body)])                       # pre-deploy row
    assert svc._check_supabase_cache("TEST") is None
    svc.supabase = _FakeTable([_row({**body, "payload_version": 1})])
    assert svc._check_supabase_cache("TEST") is None
    svc.supabase = _FakeTable([_row("not an object")])
    assert svc._check_supabase_cache("TEST") is None
    svc.supabase = _FakeTable([_row({**body, "payload_version": gmod._GROWTH_PAYLOAD_VERSION})])
    hit = svc._check_supabase_cache("TEST")
    assert isinstance(hit, GrowthResponse) and hit.symbol == "TEST"

    table = _FakeTable([])
    svc.supabase = table
    svc._upsert_supabase_cache_safe("TEST", _plain([]), None)
    assert table.upserted[0]["response_json"]["payload_version"] == gmod._GROWTH_PAYLOAD_VERSION


# ── #34: quarterly peers join on the CALENDAR quarter (final rule) ────────────


def _quarterly_series(r: GrowthResponse) -> Dict[str, List[GrowthDataPointSchema]]:
    return {
        "eps_quarterly": r.eps_quarterly, "revenue_quarterly": r.revenue_quarterly,
        "net_income_quarterly": r.net_income_quarterly,
        "operating_profit_quarterly": r.operating_profit_quarterly,
        "fcf_quarterly": r.free_cash_flow_quarterly,
    }


# One DISTINCT YoY median per calendar quarter, so a point that joined the wrong quarter
# (e.g. the legacy "<fiscal Q>'<end year>" key: MSFT's fiscal Q1 FY26, Jul-Sep 2025 →
# "Q1'25" = peers' Jan-Mar 2025) reads a visibly different number.
_CAL_VALUES = {
    "Q1'24": 1.0, "Q2'24": 2.0, "Q3'24": 3.0, "Q4'24": 4.0,
    "Q1'25": 5.0, "Q2'25": 6.0, "Q3'25": 7.5, "Q4'25": 8.0,
}
_QOQ_SCALE = 10.0   # QoQ medians = YoY × 10, so the two tables cannot be confused
# MSFT fiscal display label → the calendar quarter its period ends in.
_MSFT_JOIN = {
    "Q1 '25": "Q3'24", "Q2 '25": "Q4'24", "Q3 '25": "Q1'25",
    "Q4 '25": "Q2'25", "Q1 '26": "Q3'25", "Q2 '26": "Q4'25",
}


def _calendar_distinct_lookup() -> _FakeLookup:
    metrics = ["eps_yoy", "revenue_yoy", "net_income_yoy", "operating_income_yoy", "fcf_yoy"]
    return _FakeLookup(
        annual={m: _all_cells(_ANNUAL_KEYS, "industry") for m in metrics},
        quarterly={
            **{m: {k: _cell(v, "industry") for k, v in _CAL_VALUES.items()} for m in metrics},
            **{m: {k: _cell(v * _QOQ_SCALE, "industry") for k, v in _CAL_VALUES.items()}
               for m in ("eps_qoq", "revenue_qoq")},
        },
    )


@pytest.mark.asyncio
async def test_off_calendar_company_gets_its_same_calendar_quarter_peers_and_keeps_annual(
    monkeypatch,
):
    """Microsoft (fiscal Q1 = Jul-Sep) is no longer hidden: every quarter draws the peer
    median of the calendar quarter it ENDS in — fiscal Q1 FY26 (ends 2025-09-30) gets
    Q3'25 (7.5), never the legacy "Q1'25" cell (5.0) — and the annual line is unchanged."""
    svc = _service(monkeypatch, _FakeFMP(quarterly=_MSFT_QUARTERS,
                                         cf_quarterly=_cf(_MSFT_QUARTERS)),
                   _calendar_distinct_lookup())
    response, degraded = await svc._build_growth("MSFT")
    assert degraded == []
    for name, series in _quarterly_series(response).items():
        assert [p.period for p in series] == list(_MSFT_JOIN), f"{name}: every bar charts"
        for p in series:
            expected = _CAL_VALUES[_MSFT_JOIN[p.period]]
            assert p.sector_average_yoy == expected, (name, p.period, p.sector_average_yoy)
            if name in ("eps_quarterly", "revenue_quarterly"):
                assert p.sector_average_qoq == expected * _QOQ_SCALE, (name, p.period)
            else:
                assert p.sector_average_qoq is None, f"{name} has no QoQ benchmark"
        assert response.peer_group_levels.get(name) == "industry", name
    by = {p.period: p for p in response.revenue_quarterly}
    assert by["Q1 '26"].sector_average_yoy != _CAL_VALUES["Q1'25"], "legacy-key mis-join"
    assert all(p.sector_average_yoy == 7.0 for p in response.revenue_annual), "annual stays"
    assert response.peer_group_levels.get("revenue_annual") == "industry"


@pytest.mark.asyncio
async def test_calendar_company_keeps_its_quarterly_peer_line(monkeypatch):
    """Negative control — a calendar-quarter filer joins each quarter to itself."""
    svc = _service(monkeypatch, _FakeFMP(), _calendar_distinct_lookup())
    response, _ = await svc._build_growth("TEST")
    for name, series in _quarterly_series(response).items():
        assert {p.period: p.sector_average_yoy for p in series} == {
            "Q1 '24": 1.0, "Q2 '24": 2.0, "Q3 '24": 3.0, "Q4 '24": 4.0,
            "Q1 '25": 5.0, "Q2 '25": 6.0,
        }, name
        assert response.peer_group_levels.get(name) == "industry", name


@pytest.mark.asyncio
async def test_off_calendar_cash_flow_quarters_still_join_when_the_income_leg_failed(
    monkeypatch,
):
    """The join is decided PER ROW from its own period end — there is no company-level
    vote that a failed income leg could starve. MSFT's cash-flow-only FCF series still
    meets its same-calendar-quarter peers, and only the FMP leg is degraded."""
    svc = _service(
        monkeypatch,
        _FakeFMP(quarterly=FMPUnavailableException("503"), cf_quarterly=_cf(_MSFT_QUARTERS)),
        _calendar_distinct_lookup(),
    )
    response, degraded = await svc._build_growth("MSFT")
    assert degraded == ["quarterly_income"], "a healthy benchmark read is not degraded"
    assert response.revenue_quarterly == [] and response.eps_quarterly == []
    assert "revenue_quarterly" not in response.peer_group_levels, "no bars → no line to name"
    fcf = response.free_cash_flow_quarterly
    assert [p.period for p in fcf] == list(_MSFT_JOIN)
    for p in fcf:
        assert p.sector_average_yoy == _CAL_VALUES[_MSFT_JOIN[p.period]], p.period
    assert response.peer_group_levels.get("fcf_quarterly") == "industry"


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker, fmp", [
    ("TEST", _FakeFMP()),
    ("MSFT", _FakeFMP(quarterly=_MSFT_QUARTERS, cf_quarterly=_cf(_MSFT_QUARTERS))),
])
async def test_quarterly_benchmark_reads_use_the_calendar_quarter_period_type(
    monkeypatch, ticker, fmp,
):
    """Both quarterly reads (YoY and QoQ) ask for ``calendar_quarter`` — never the legacy
    fiscal-keyed ``quarterly`` rows — for a calendar filer AND an off-calendar one (the
    read is no longer skipped for the latter)."""
    assert CALENDAR_QUARTER_PERIOD_TYPE == "calendar_quarter"
    lookup = _calendar_distinct_lookup()
    svc = _service(monkeypatch, fmp, lookup)
    await svc._build_growth(ticker)
    yoy = ("eps_yoy", "revenue_yoy", "net_income_yoy", "operating_income_yoy", "fcf_yoy")
    assert lookup.calls == [
        ("annual", yoy),
        ("calendar_quarter", yoy),
        ("calendar_quarter", ("eps_qoq", "revenue_qoq")),
    ]
    assert "quarterly" not in {period_type for period_type, _ in lookup.calls}


# ── #73: per-series peer group level ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_peer_group_levels_are_voted_per_series(monkeypatch):
    lookup = _FakeLookup(annual={
        # EPS: industry everywhere.
        "eps_yoy": _all_cells(_ANNUAL_KEYS, "industry"),
        # Revenue: industry carries only one year; sector fills the other two.
        "revenue_yoy": {"2023": _cell(4.0, "sector"), "2024": _cell(5.0, "sector"),
                        "2025": _cell(6.0, "industry")},
        # Net income: a 1-1 tie between the two drawn points → industry.
        "net_income_yoy": {"2024": _cell(5.0, "sector"), "2025": _cell(6.0, "industry")},
        # Operating income: no benchmark at all → no key (no line to name).
        "operating_income_yoy": {},
        # FCF: cells exist but declare no level → no vote → no key.
        "fcf_yoy": {lab: {"value": 3.0, "n": 50} for lab in _ANNUAL_KEYS},
    })
    svc = _service(monkeypatch, _FakeFMP(), lookup)
    response, _ = await svc._build_growth("TEST")
    levels = response.peer_group_levels
    assert levels["eps_annual"] == "industry"
    assert levels["revenue_annual"] == "sector"
    assert levels["net_income_annual"] == "industry"
    assert "operating_profit_annual" not in levels
    assert "fcf_annual" not in levels
    assert set(levels.values()) <= {"industry", "sector"}
    assert len(lookup.calls) == 3, "levels must come from the same three lookups"


@pytest.mark.asyncio
async def test_peer_level_follows_the_hold_back_donor(monkeypatch):
    """A THIN industry cell is drawn with the latest MATURE value at or before it — here
    a SECTOR cell. The legend must name the group the plotted value came from."""
    lookup = _FakeLookup(annual={
        "revenue_yoy": {
            "2024": _cell(5.0, "sector", n=60),
            "2025": _cell(80.0, "industry", n=4),   # thin → shows 2024's sector 5.0
        },
    })
    svc = _service(monkeypatch, _FakeFMP(), lookup)
    response, _ = await svc._build_growth("TEST")
    by = {p.period: p for p in response.revenue_annual}
    assert by["2025"].sector_average_yoy == 5.0
    assert response.peer_group_levels["revenue_annual"] == "sector"


@pytest.mark.asyncio
async def test_no_sector_means_no_peer_levels(monkeypatch):
    svc = _service(monkeypatch, _FakeFMP(profile={"symbol": "TEST"}), _yoy_metrics_lookup())
    response, _ = await svc._build_growth("TEST")
    assert response.peer_group_levels == {}
    assert all(p.sector_average_yoy is None for p in response.revenue_annual)


def test_old_payload_without_the_new_fields_still_validates():
    """A frozen report's growth_chart (and any pre-deploy dict) has neither field."""
    legacy = {"symbol": "X", "eps_annual": [], "eps_quarterly": [], "revenue_annual": [],
              "revenue_quarterly": []}
    r = GrowthResponse.model_validate(legacy)
    assert r.degraded == [] and r.peer_group_levels == {}
    dumped = r.model_dump()
    assert dumped["degraded"] == [] and dumped["peer_group_levels"] == {}


# ── #31: the snapshot reads only the latest point ────────────────────────────


def _pt(period: str, yoy: Optional[float], sector: Optional[float] = None) -> GrowthDataPointSchema:
    return GrowthDataPointSchema(period=period, value=10.0, yoy_change_percent=yoy,
                                 sector_average_yoy=sector)


class _FakeGrowthService:
    def __init__(self, response: GrowthResponse, degraded: Optional[List[str]] = None):
        self.response, self.degraded = response, degraded or []

    async def get_growth_with_status(self, ticker):
        return self.response, list(self.degraded)


async def _snapshot(monkeypatch, response: GrowthResponse):
    from app.services import growth_snapshot_service as gs

    monkeypatch.setattr(gmod, "get_growth_service", lambda: _FakeGrowthService(response))
    svc = gs.GrowthSnapshotService.__new__(gs.GrowthSnapshotService)
    svc.supabase = None
    return await svc._compute_with_status("TEST")


def _growth(rev, eps, fcf, op) -> GrowthResponse:
    return GrowthResponse(symbol="TEST", eps_annual=eps, eps_quarterly=[],
                          revenue_annual=rev, revenue_quarterly=[],
                          free_cash_flow_annual=fcf, operating_profit_annual=op)


@pytest.mark.asyncio
async def test_snapshot_never_backfills_an_older_years_growth(monkeypatch):
    """Latest annual YoY is n/m: the card showed +13.4% (last year's) as current."""
    older_then_null = [_pt("2024", 13.4, 9.0), _pt("2025", None, 8.0)]
    snap, degraded = await _snapshot(
        monkeypatch, _growth(older_then_null, older_then_null, [_pt("2025", 12.0)],
                             [_pt("2025", 12.0)]),
    )
    by = {m.metric_key: m for m in snap.metrics}
    assert by["revenue_growth"].value == "—" and by["revenue_growth"].score is None
    assert by["eps_growth"].value == "—" and by["eps_growth"].score is None
    assert by["fcf_growth"].value == "+12.0%"
    assert degraded == [], "two metrics still have values: this build may be persisted"


@pytest.mark.asyncio
async def test_snapshot_rating_is_weighted_over_present_metrics_only(monkeypatch):
    """Revenue +30% (score 5) alone. The absent three used to vote a neutral 3 at 70%
    weight: 5*0.3 + 3*0.7 = 3.6 → rating 4 for a single strong, unopposed signal."""
    snap, _ = await _snapshot(
        monkeypatch, _growth([_pt("2025", 30.0)], [_pt("2025", None)], [], []),
    )
    assert snap.weighted_score == 5.0
    assert snap.rating == 5

    # Two present metrics re-normalise by their own weights: (5*0.3 + 1*0.2) / 0.5 = 3.4.
    snap, _ = await _snapshot(
        monkeypatch, _growth([_pt("2025", 30.0)], [], [_pt("2025", -50.0)], []),
    )
    assert snap.weighted_score == 3.4
    assert 1.0 <= snap.weighted_score <= 5.0


@pytest.mark.asyncio
async def test_snapshot_with_nothing_measured_is_neutral_and_flagged(monkeypatch):
    snap, degraded = await _snapshot(
        monkeypatch, _growth([_pt("2025", None)], [], [], []),
    )
    assert snap.weighted_score == 3.0 and snap.rating == 3
    assert "no_values" in degraded
    assert all(m.score is None and m.value == "—" for m in snap.metrics)


def test_snapshot_cache_rows_from_before_the_fix_are_rebuilt():
    from app.services import growth_snapshot_service as gs
    from app.schemas.stock_overview import SnapshotItemResponse

    svc = gs.GrowthSnapshotService.__new__(gs.GrowthSnapshotService)
    body = SnapshotItemResponse(category="Growth", rating=4, metrics=[]).model_dump()
    svc.supabase = _FakeTable([_row(body)])
    assert svc._check_supabase_cache("TEST") is None
    svc.supabase = _FakeTable([_row({**body, "_schema_v": gs._SNAPSHOT_PAYLOAD_VERSION})])
    assert svc._check_supabase_cache("TEST").rating == 4

    table = _FakeTable([])
    svc.supabase = table
    svc._upsert_supabase_cache("TEST", SnapshotItemResponse(**body))
    assert table.upserted[0]["response_json"]["_schema_v"] == gs._SNAPSHOT_PAYLOAD_VERSION
