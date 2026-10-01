"""Growth round 2 (2026-09-30), backend half — findings R11, R12, R27.

* R11 — a 52/53-week calendar filer's Q4 that closes on Jan 1-7 (Cadence FY2025 Q4 ended
        2026-01-03) joined the legacy quarterly cell "Q4'26" (fiscal Q-number + year of
        the period end, no spill): peers' Oct-Dec 2026, or — held back while that cell is
        thin — their Apr-Jun 2026. The interim fix drew NO peer value for it. FINAL rule
        (calendar-quarter re-key): the join key is the CALENDAR quarter of the period end
        with a 1-7-day spill (`period_labels.calendar_quarter_label`), read against the
        period_type ``calendar_quarter`` rows — so that Q4 joins "Q4'25" (never "Q4'26"
        or the held-back Q2'26), a 2024-12-28 Q4 still joins "Q4'24", Microsoft's fiscal
        Q1 (ends Sep 30) joins "Q3'25", and only an undated row joins nothing ("").
* R12 — a failed benchmark read (`BenchmarkLookupFailed`) was flattened into "no peer
        line" and the build persisted for 24h. It is now `degraded=["benchmarks"]` and
        not written to growth_cache — for EVERY company, an off-calendar one included
        (its quarterly line is drawn now, so a failed ``calendar_quarter`` read is a real
        hole). A plain empty lookup ("no rows") is not degraded.
* R27 — profit_power writes TODAY as the next earnings date on report day; growth
        dropped it (`> today`) and kept the pre-release morning build for 24h. It now
        accepts `>= today`, so the row is stale for the rest of report day.

Each test FAILS on the first-pass code and passes after (the R11/R12 ones were rewritten
for the final calendar-quarter rule and fail on the interim code too). Hermetic: fake FMP, fake
benchmark lookup, fake Supabase (reused from test_growth_deepcheck_backend).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.growth import GrowthResponse
from app.services import growth_service as gmod
from app.services.growth_service import _compute_growth_points, _quarterly_join_key
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE,
    BenchmarkLookupFailed,
)
from app.utils.period_labels import calendar_quarter_label
from tests.test_growth_deepcheck_backend import (
    _MSFT_QUARTERS,
    _FakeFMP,
    _FakeLookup,
    _VALS,
    _cell,
    _cf,
    _no_supabase,
    _plain,
    _q,
    _reset_tiers,
    _service,
    _yoy_metrics_lookup,
)


# ── R11: the join key is the CALENDAR quarter of the period end ──────────────


@pytest.mark.parametrize("record, key", [
    # Cadence FY2025 Q4 closes 2026-01-03: calendar Q4'25 (the 1-7-day spill), never the
    # legacy "Q4'26" and never "" any more.
    ({"date": "2026-01-03", "period": "Q4", "fiscalYear": "2025"}, "Q4'25"),
    ({"date": "2022-01-01", "period": "Q4", "fiscalYear": "2021"}, "Q4'21"),
    # Cadence FY2024 Q4 closed in December → joins Q4'24 as before.
    ({"date": "2024-12-28", "period": "Q4", "fiscalYear": "2024"}, "Q4'24"),
    # A 52/53-week Q1/Q2 spilling 1-7 days into the next month stays in its quarter.
    ({"date": "2026-04-04", "period": "Q1", "fiscalYear": "2026"}, "Q1'26"),
    ({"date": "2026-07-04", "period": "Q2", "fiscalYear": "2026"}, "Q2'26"),
    ({"date": "2025-03-31", "period": "Q1", "fiscalYear": "2025"}, "Q1'25"),
    # Spill boundary: day 7 still belongs to the previous month, day 8 does not.
    ({"date": "2026-01-07", "period": "Q4", "fiscalYear": "2025"}, "Q4'25"),
    ({"date": "2026-01-08", "period": "Q4", "fiscalYear": "2025"}, "Q1'26"),
    # Off-calendar fiscal quarters now JOIN — on their end date's calendar quarter,
    # whatever their fiscal Q-number says.
    ({"date": "2025-09-30", "period": "Q1", "fiscalYear": "2026"}, "Q3'25"),   # Microsoft
    ({"date": "2025-01-26", "period": "Q4", "fiscalYear": "2025"}, "Q1'25"),   # Nvidia
    ({"date": "2024-10-27", "period": "Q3", "fiscalYear": "2025"}, "Q4'24"),   # Nvidia
    # Only an undated / unparseable row joins nothing — a calendarYear / fiscal period
    # alone is never turned into a key (that invention is how the mis-joins began).
    ({"date": None, "period": "Q2", "calendarYear": "2025"}, ""),
    ({"date": "", "period": "Q2", "fiscalYear": "2025"}, ""),
    ({"date": "garbage", "period": "Q2", "fiscalYear": "2025"}, ""),
    ({"date": "2025-13-45", "period": "Q2", "fiscalYear": "2025"}, ""),
    ({"period": "Q2", "fiscalYear": "2025"}, ""),
])
def test_quarterly_join_key_is_the_calendar_quarter_of_the_period_end(record, key):
    assert _quarterly_join_key(record) == key
    # One shared rule with the benchmark producer and profit_power.
    assert _quarterly_join_key(record) == calendar_quarter_label(record)


def test_compute_points_carry_the_calendar_quarter_join_key():
    rows = [
        _q("2024-12-28", "Q4", "2024", revenue=1.36e9),
        _q("2026-01-03", "Q4", "2025", revenue=1.44e9),
        # Undated, but it has a year: the bar charts, its join key is "".
        {"date": None, "period": "Q1", "calendarYear": "2026", "revenue": 1.5e9},
    ]
    by = {p["period"]: p for p in _compute_growth_points(rows, "revenue", is_quarterly=True)}
    assert by["Q4 '24"]["_match_period"] == "Q4'24"
    assert by["Q4 '25"]["_match_period"] == "Q4'25", "the Jan-3 Q4 must join Q4'25, not Q4'26"
    assert by["Q4 '25"]["yoy_change_percent"] is not None, "its own YoY is untouched"
    assert by["Q1 '26"]["_match_period"] == "", "an undated row has no key to join on"
    assert by["Q1 '26"]["value"] == 1.5e9, "…but its bar still charts"


# Cadence-style 52/53-week calendar filer: quarters close on the Saturday nearest the
# calendar quarter end, so FY2025 Q4 closes on 2026-01-03 and FY2026 Q1/Q2 on Apr 4/Jul 4.
_CDNS = [
    _q("2024-12-28", "Q4", "2024", **_VALS),
    _q("2025-03-29", "Q1", "2025", **_VALS),
    _q("2025-06-28", "Q2", "2025", **_VALS),
    _q("2025-09-27", "Q3", "2025", **_VALS),
    _q("2026-01-03", "Q4", "2025", **{**_VALS, "revenue": 120.0}),
    _q("2026-04-04", "Q1", "2026", **{**_VALS, "revenue": 125.0}),
    _q("2026-07-04", "Q2", "2026", **{**_VALS, "revenue": 130.0}),
]

_YOY = ["eps_yoy", "revenue_yoy", "net_income_yoy", "operating_income_yoy", "fcf_yoy"]


def _cdns_lookup() -> _FakeLookup:
    cells = {
        "Q4'24": _cell(5.0, "industry", n=80),
        "Q1'25": _cell(5.5, "industry", n=80),
        "Q2'25": _cell(5.6, "industry", n=80),
        "Q3'25": _cell(5.8, "industry", n=80),
        "Q4'25": _cell(6.0, "industry", n=80),
        "Q1'26": _cell(8.0, "industry", n=70),
        "Q2'26": _cell(9.0, "industry", n=60),
        "Q4'26": _cell(30.0, "industry", n=3),   # only the Jan 1-7 filers pool here
    }
    return _FakeLookup(
        annual={},
        quarterly={m: dict(cells) for m in _YOY + ["eps_qoq", "revenue_qoq"]},
    )


# Cadence fiscal display label → the calendar-quarter cell it must draw.
_CDNS_JOIN = {
    "Q4 '24": "Q4'24", "Q1 '25": "Q1'25", "Q2 '25": "Q2'25", "Q3 '25": "Q3'25",
    "Q4 '25": "Q4'25", "Q1 '26": "Q1'26", "Q2 '26": "Q2'26",
}


@pytest.mark.asyncio
async def test_cadence_jan_q4_joins_the_q4_25_cell_and_december_q4_still_joins(monkeypatch):
    svc = _service(monkeypatch, _FakeFMP(quarterly=_CDNS, cf_quarterly=_cf(_CDNS)), _cdns_lookup())
    response, degraded = await svc._build_growth("CDNS")
    assert degraded == []
    cells = _cdns_lookup().quarterly["revenue_yoy"]
    for name, series in {
        "eps_quarterly": response.eps_quarterly,
        "revenue_quarterly": response.revenue_quarterly,
        "net_income_quarterly": response.net_income_quarterly,
        "operating_profit_quarterly": response.operating_profit_quarterly,
        "fcf_quarterly": response.free_cash_flow_quarterly,
    }.items():
        by = {p.period: p for p in series}
        assert set(by) == set(_CDNS_JOIN), name
        jan_q4 = by["Q4 '25"]
        # Legacy key "Q4'26" → thin (n=3) → held back to Q2'26 = 9.0; the raw thin cell
        # is 30.0; the interim rule drew None. Final rule: Oct-Dec 2025 peers = 6.0.
        assert jan_q4.sector_average_yoy == 6.0, (name, jan_q4.sector_average_yoy)
        assert jan_q4.sector_average_yoy not in (9.0, 30.0), name
        if name in ("eps_quarterly", "revenue_quarterly"):
            assert jan_q4.sector_average_qoq == 6.0, name
        else:
            assert jan_q4.sector_average_qoq is None, f"{name} has no QoQ benchmark"
        assert by["Q4 '24"].sector_average_yoy == 5.0, f"{name}: a December Q4 still joins"
        for label, cell_key in _CDNS_JOIN.items():
            assert by[label].sector_average_yoy == cells[cell_key]["value"], (name, label)
        # The line exists and is named for this series.
        assert response.peer_group_levels.get(name) == "industry", name


# ── R12: a failed benchmark read is degraded, never persisted ────────────────


class _FailingLookup(_FakeLookup):
    """Answers like `SectorBenchmarkLookup.get_benchmarks` after a DB error, for the
    period types named in ``fail``."""

    def __init__(self, base: _FakeLookup, fail: set):
        super().__init__(base.annual, base.quarterly)
        self.fail = fail

    def get_benchmarks(self, industry, sector, metrics, period_type):
        result = super().get_benchmarks(industry, sector, metrics, period_type)
        if period_type in self.fail:
            return BenchmarkLookupFailed({m: {} for m in metrics})
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [
    {"annual"},
    {CALENDAR_QUARTER_PERIOD_TYPE},
    {"annual", CALENDAR_QUARTER_PERIOD_TYPE},
], ids=["annual", "calendar_quarter", "both"])
async def test_failed_benchmark_lookup_marks_the_build_degraded(monkeypatch, fail):
    svc = _service(monkeypatch, _FakeFMP(), _FailingLookup(_yoy_metrics_lookup(), fail))
    response, degraded = await svc._build_growth("TEST")
    assert degraded == ["benchmarks"]
    assert response.degraded == ["benchmarks"]
    assert response.revenue_annual and response.revenue_quarterly, "the bars still render"
    if "annual" in fail:
        assert all(p.sector_average_yoy is None for p in response.revenue_annual)
        assert "revenue_annual" not in response.peer_group_levels
    else:
        assert all(p.sector_average_yoy == 7.0 for p in response.revenue_annual)
    if CALENDAR_QUARTER_PERIOD_TYPE in fail:
        assert all(p.sector_average_yoy is None for p in response.revenue_quarterly)
        assert all(p.sector_average_qoq is None for p in response.revenue_quarterly)
        assert "revenue_quarterly" not in response.peer_group_levels
    else:
        assert all(p.sector_average_yoy == 7.0 for p in response.revenue_quarterly)


@pytest.mark.asyncio
async def test_failed_lookup_build_is_served_but_not_persisted(monkeypatch):
    _reset_tiers()
    svc = _service(monkeypatch, _FakeFMP(), _FailingLookup(_yoy_metrics_lookup(), {"annual"}))
    persisted = _no_supabase(monkeypatch, svc)
    response, degraded = await svc.get_growth_with_status("TEST")
    assert degraded == ["benchmarks"] and response.degraded == ["benchmarks"]
    hit, hit_degraded = await svc.get_growth_with_status("TEST")         # Tier-1 hit
    assert hit_degraded == ["benchmarks"] and hit.degraded == ["benchmarks"]
    assert persisted == [], "a build with a failed peer read reached growth_cache"
    _reset_tiers()


@pytest.mark.asyncio
async def test_plain_empty_lookup_is_an_answer_not_a_failure(monkeypatch):
    """"No rows for this peer group" is a complete build: persisted, not degraded."""
    _reset_tiers()
    svc = _service(monkeypatch, _FakeFMP(), _FakeLookup())
    persisted = _no_supabase(monkeypatch, svc)
    response, degraded = await svc.get_growth_with_status("TEST")
    assert degraded == [] and response.degraded == []
    assert response.peer_group_levels == {}
    import asyncio

    for _ in range(5):        # the write-through runs on the default executor
        if persisted:
            break
        await asyncio.sleep(0.01)
    assert persisted == ["TEST"]
    _reset_tiers()


@pytest.mark.asyncio
async def test_failed_calendar_quarter_read_degrades_an_off_calendar_company_too(monkeypatch):
    """The interim rule exempted an off-calendar company (its quarterly line was hidden
    either way). It is drawn now, so a failed ``calendar_quarter`` read is a real hole for
    Microsoft as for anyone: degraded, served without the quarterly peer line, NOT
    persisted. The annual line, read separately, is unaffected."""
    _reset_tiers()
    fmp = _FakeFMP(quarterly=_MSFT_QUARTERS, cf_quarterly=_cf(_MSFT_QUARTERS))
    svc = _service(
        monkeypatch, fmp, _FailingLookup(_yoy_metrics_lookup(), {CALENDAR_QUARTER_PERIOD_TYPE}),
    )
    persisted = _no_supabase(monkeypatch, svc)
    response, degraded = await svc.get_growth_with_status("MSFT")
    assert degraded == ["benchmarks"] and response.degraded == ["benchmarks"]
    assert response.revenue_quarterly, "the company's own quarters still chart"
    assert all(p.sector_average_yoy is None for p in response.revenue_quarterly)
    assert "revenue_quarterly" not in response.peer_group_levels
    assert all(p.sector_average_yoy == 7.0 for p in response.revenue_annual), "annual stays"
    assert persisted == [], "a build with a failed calendar-quarter read reached growth_cache"
    _reset_tiers()

    # A failed ANNUAL read still degrades it too.
    svc = _service(monkeypatch, fmp, _FailingLookup(_yoy_metrics_lookup(), {"annual"}))
    response, degraded = await svc._build_growth("MSFT")
    assert degraded == ["benchmarks"]
    assert all(p.sector_average_yoy == 7.0 for p in response.revenue_quarterly), (
        "a healthy calendar-quarter read keeps MSFT's quarterly peer line"
    )


@pytest.mark.asyncio
async def test_failed_lookup_rides_beside_failed_fmp_legs(monkeypatch):
    from app.integrations.fmp import FMPUnavailableException

    svc = _service(
        monkeypatch,
        _FakeFMP(annual=FMPUnavailableException("503")),
        _FailingLookup(_yoy_metrics_lookup(), {CALENDAR_QUARTER_PERIOD_TYPE}),
    )
    _, degraded = await svc._build_growth("TEST")
    assert degraded == ["annual_income", "benchmarks"]


# ── R27: report-day next-earnings date ───────────────────────────────────────


class _Tables:
    """Minimal multi-table fake of the supabase-py chain used by growth_service."""

    def __init__(self, rows: Dict[str, List[Dict[str, Any]]]):
        self.rows = rows
        self.upserts: Dict[str, List[Dict[str, Any]]] = {}
        self._table: Optional[str] = None

    def table(self, name):
        self._table = name
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, payload, **_k):
        self.upserts.setdefault(self._table, []).append(payload)
        self.rows[self._table] = [payload]
        return self

    def execute(self):
        class _R:
            pass

        r = _R()
        r.data = list(self.rows.get(self._table) or [])
        return r


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bare_service(supabase) -> gmod.GrowthService:
    svc = gmod.GrowthService.__new__(gmod.GrowthService)
    svc.fmp = None
    svc.supabase = supabase
    return svc


def test_report_day_date_is_kept_and_makes_the_row_stale_for_the_day():
    today = _today()
    tables = _Tables({"profit_power_cache": [{"next_earnings_date": today}]})
    svc = _bare_service(tables)

    # First pass: `> today` → None → the plain 24h TTL kept the morning build all day.
    next_date = svc._next_earnings_date_safe("CDNS")
    assert next_date == today

    svc._upsert_supabase_cache_safe("CDNS", _plain([]), next_date)
    written = tables.upserts["growth_cache"][0]
    assert written["next_earnings_date"] == today
    # The freshness check rejects that row on the very next read: Growth rebuilds after
    # each 5-minute in-memory window on report day, like the other Financials services.
    assert svc._check_supabase_cache("CDNS") is None


@pytest.mark.parametrize("offset_days, kept", [(-1, False), (0, True), (1, True), (40, True)])
def test_only_a_past_date_is_rejected(offset_days, kept):
    from datetime import timedelta

    day = (datetime.now(timezone.utc) + timedelta(days=offset_days)).strftime("%Y-%m-%d")
    svc = _bare_service(_Tables({"profit_power_cache": [{"next_earnings_date": day}]}))
    assert (svc._next_earnings_date_safe("CDNS") == day) is kept
    if not kept:
        assert svc._next_earnings_date_safe("CDNS") is None


def test_future_dated_row_is_still_a_tier2_hit():
    """Negative control: a row whose next earnings is still ahead serves from Tier 2."""
    from datetime import timedelta

    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    tables = _Tables({})
    svc = _bare_service(tables)
    svc._upsert_supabase_cache_safe("CDNS", _plain([]), future)
    hit = svc._check_supabase_cache("CDNS")
    assert isinstance(hit, GrowthResponse) and hit.symbol == "TEST"
