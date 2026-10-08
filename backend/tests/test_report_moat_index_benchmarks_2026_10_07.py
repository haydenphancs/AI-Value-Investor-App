"""Report collector, moat scorer and index P/E on the 2026-10-07 peer-benchmark rules.

Workstream D5 of the peer-benchmark fix (audit wf_1368dfa4-022). The lookup now serves an
annual / calendar-quarter row only once its period is complete (computed >= period end +
75 days), drops a TTM row older than 21 days, and merges industry and sector PER PERIOD
(`sector_benchmark_lookup.merge_peer_cells`). These are the readers that sat beside it:

1. The Fundamentals drill-down pinned the current year's peer point to ANY TTM median —
   a 13-company industry included. It now takes only a mature TTM cell (n >= 20), the
   same cell the Overview card shows (`get_current_benchmarks`), and never a growth metric.
2. Competitor scoring took ``max(period_label)`` of the raw annual map with no floor: the
   partial current year, from the early filers. It now reads `get_current_benchmarks`
   (sector TTM, else the newest complete mature year).
3. A FAILED benchmark read in the moat or competitor scoring silently scored on fixed
   bands / the AI dimension and was shared-cached. It now rides the report's
   `_degraded_sections`, so the report is delivered but never shared.
4. The moat scorer compared a company's latest fiscal year with the newest sector year
   with n >= 10 — a NEWER year for a stale filer, a thin cell otherwise — under a raw
   (un-normalized) sector name. Now: the same year (`annual_benchmark_key`), else the
   newest older mature year; the canonical sector name.
5. The index P/E averaged the newest calendar-quarter sector medians (Utilities Q1'26 15.4
   → Q2'26 23.7). It now averages the sector TTM medians through the serving gate,
   requiring >= 8 sectors with n >= 20.

Hermetic: every Supabase read is a local fake; the real lookup runs on top of it where the
serving gate is the thing under test.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.services.agents.ticker_report_data_collector as C
import app.services.index_service as idx
import app.services.moat_scoring_service as moat_mod
import app.services.sector_benchmark_lookup as sbl
from app.services.agents.narrative_prompts import stage_a_fallback
from app.services.moat_scoring_service import (
    PILLAR_BRAND,
    MoatScores,
    MoatScoringService,
    _pick_year_at_or_before,
)
from app.services.sector_benchmark_lookup import (
    MATURE_SAMPLE_FLOOR,
    BenchmarkLookupFailed,
    lookup_failed,
)

# The schema-parity builder and its autouse fixture, which stubs every live benchmark /
# moat lookup that `_build_sections` and `assemble_report` reach (importing an autouse
# fixture into a module makes it autouse here too). Tests below override the stub.
from test_ticker_report_schema_parity import (  # noqa: F401
    _make_collected_data,
    _no_live_benchmark_lookup,
)


_NOW = datetime.now(timezone.utc)
_CUR_YEAR = _NOW.year
# A year that is complete whatever today's date is: it ended more than a year ago, and its
# rows were computed well past its end + 75 days.
_DONE_YEAR = _CUR_YEAR - 2
_DONE_COMPUTED = f"{_CUR_YEAR - 1}-06-01T04:00:00+00:00"
_FRESH = (_NOW - timedelta(days=2)).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# A PostgREST fake under the REAL SectorBenchmarkLookup (so the serving gate and the
# per-period merge run exactly as in production)
# ═══════════════════════════════════════════════════════════════════════════


class _Query:
    def __init__(self, rows: List[Dict[str, Any]], log: List[Dict[str, Any]]):
        self._rows, self._log = rows, log
        self._eq: List[tuple] = []
        self._in: List[tuple] = []
        self._range = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in.append((col, list(vals)))
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def execute(self):
        self._log.append(dict(self._eq))
        out = [
            r for r in self._rows
            if all(r.get(c) == v for c, v in self._eq)
            and all(r.get(c) in vs for c, vs in self._in)
        ]
        out.sort(key=lambda r: r["id"])
        a, b = self._range or (0, len(out))
        return SimpleNamespace(data=out[a:b + 1])


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.log: List[Dict[str, Any]] = []
        for n, r in enumerate(self.rows):
            r.setdefault("id", n)

    def table(self, _name):
        return _Query(self.rows, self.log)


@pytest.fixture
def tag():
    """A unique peer-group suffix, and the lookup's process cache cleaned after."""
    t = uuid.uuid4().hex[:8]
    yield t
    for key in [k for k in list(sbl._cache) if t in str(k)]:
        sbl._cache.pop(key, None)


def _real_lookup(monkeypatch, rows):
    fake = _FakeSupabase(rows)
    lookup = sbl.SectorBenchmarkLookup.__new__(sbl.SectorBenchmarkLookup)
    lookup.supabase = fake
    monkeypatch.setattr(sbl, "get_sector_benchmark_lookup", lambda: lookup)
    return lookup, fake


def _row(sector, industry, metric, period_type, label, value, n, computed):
    return {"sector": sector, "industry": industry, "metric_name": metric,
            "period_type": period_type, "period_label": label, "median_value": value,
            "sample_size": n, "computed_at": computed}


def _collector():
    return C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Drill-down: the current-year point is a MATURE TTM median, equal to the card's
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_mature_industry_ttm_is_the_current_year_point_and_equals_the_card(
    monkeypatch, tag,
):
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    lookup, _ = _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "ttm", "TTM", 31.0, 25, _FRESH),
        _row(sector, "", "pe_ratio", "ttm", "TTM", 22.0, 300, _FRESH),
        _row(sector, industry, "pe_ratio", "annual", str(_DONE_YEAR), 28.0, 25, _DONE_COMPUTED),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert not lookup_failed(hist)
    annual = hist["annual"]["pe_ratio"]
    assert annual[str(_CUR_YEAR)] == 31.0
    assert annual[str(_DONE_YEAR)] == 28.0, "history keeps its own complete year"
    card = lookup.get_current_benchmarks(industry, sector, ["pe_ratio"])["pe_ratio"]
    assert card["value"] == annual[str(_CUR_YEAR)], (
        "the drill-down's newest peer point must be the Overview card's value"
    )


@pytest.mark.asyncio
async def test_ko_shape_thin_industry_ttm_injects_the_sector_ttm_and_hides_the_partial_year(
    monkeypatch, tag,
):
    """KO on 2026-10-07: Beverages TTM n=13 (thin), Consumer Defensive TTM mature, and a
    partial current-year sector cohort (n=39) computed during the year. The point is the
    sector TTM; the partial cohort never reaches the chart."""
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    lookup, _ = _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "ttm", "TTM", 35.0, 13, _FRESH),
        _row(sector, "", "pe_ratio", "ttm", "TTM", 20.6, 140, _FRESH),
        _row(sector, "", "pe_ratio", "annual", str(_CUR_YEAR), 99.0, 39, _NOW.isoformat()),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert hist["annual"]["pe_ratio"][str(_CUR_YEAR)] == 20.6
    card = lookup.get_current_benchmarks(industry, sector, ["pe_ratio"])["pe_ratio"]
    assert (card["value"], card["level"]) == (20.6, "sector")


@pytest.mark.asyncio
async def test_a_thin_ttm_on_both_levels_leaves_the_current_year_blank(monkeypatch, tag):
    """No mature TTM: nothing is injected (never a 13-company median, never an older year
    restated as this one). The card falls back to the newest complete mature year, which
    the chart already draws at THAT year."""
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    lookup, _ = _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "ttm", "TTM", 35.0, 13, _FRESH),
        _row(sector, "", "pe_ratio", "ttm", "TTM", 21.0, 12, _FRESH),
        _row(sector, "", "pe_ratio", "annual", str(_DONE_YEAR), 18.0, 140, _DONE_COMPUTED),
        _row(sector, "", "pe_ratio", "annual", str(_CUR_YEAR), 99.0, 39, _NOW.isoformat()),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    annual = hist["annual"]["pe_ratio"]
    assert str(_CUR_YEAR) not in annual, annual
    assert annual == {str(_DONE_YEAR): 18.0}
    card = lookup.get_current_benchmarks(industry, sector, ["pe_ratio"])["pe_ratio"]
    assert card["value"] == annual[str(_DONE_YEAR)]


@pytest.mark.asyncio
async def test_a_stale_ttm_row_is_never_injected(monkeypatch, tag):
    """A 22-day-old TTM row belongs to a group the weekly job no longer writes."""
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    _real_lookup(monkeypatch, [
        _row(sector, "", "pe_ratio", "ttm", "TTM", 22.0, 300,
             (_NOW - timedelta(days=22)).isoformat()),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert str(_CUR_YEAR) not in hist["annual"].get("pe_ratio", {})


@pytest.mark.asyncio
async def test_a_failed_ttm_read_is_flagged_and_injects_nothing(monkeypatch, tag):
    class _Lookup:
        def get_benchmarks(self, industry, sector, metrics, period_type):
            if period_type == "ttm":
                return BenchmarkLookupFailed({m: {} for m in metrics})
            return {"pe_ratio": {str(_DONE_YEAR): {"value": 18.0, "level": "sector", "n": 99}}}

    monkeypatch.setattr(sbl, "get_sector_benchmark_lookup", lambda: _Lookup())
    hist = await _collector()._fetch_sector_benchmark_history(f"Ind-{tag}", f"Sec-{tag}")
    assert lookup_failed(hist)
    assert hist["annual"]["pe_ratio"] == {str(_DONE_YEAR): 18.0}


@pytest.mark.parametrize(
    "metric, cell, expected",
    [
        ("pe_ratio", {"value": 22.0, "n": MATURE_SAMPLE_FLOOR}, 22.0),     # boundary in
        ("pe_ratio", {"value": 22.0, "n": MATURE_SAMPLE_FLOOR - 1}, None),  # boundary out
        ("pe_ratio", {"value": 22.0}, None),                                # n unknown
        ("pe_ratio", {"value": 22.0, "n": None}, None),
        ("pe_ratio", {"value": 22.0, "n": True}, None),                     # bool is not a count
        ("pe_ratio", {"value": 22.0, "n": float("nan")}, None),
        ("pe_ratio", {"value": 22.0, "n": float("inf")}, None),
        ("pe_ratio", {"value": float("nan"), "n": 300}, None),
        ("pe_ratio", {"value": float("inf"), "n": 300}, None),
        ("pe_ratio", {"value": None, "n": 300}, None),
        ("pe_ratio", {"value": "abc", "n": 300}, None),
        ("pe_ratio", {"value": -3.0, "n": 300}, -3.0),                      # real negative median kept
        ("pe_ratio", None, None),
        ("pe_ratio", "22", None),
        ("revenue_yoy", {"value": 5.0, "n": 300}, None),                    # never a growth metric
        ("eps_qoq", {"value": 5.0, "n": 300}, None),
        ("fcf_growth", {"value": 5.0, "n": 300}, None),
    ],
)
def test_mature_ttm_value_outliers(metric, cell, expected):
    assert C._mature_ttm_value(metric, cell) == expected


@pytest.mark.parametrize(
    "answer, failed",
    [
        ({"pe_ratio": {"TTM": 20.0}}, False),
        (BenchmarkLookupFailed({"pe_ratio": {}}), True),
        (None, True),
    ],
)
def test_a_flat_only_lookup_yields_no_ttm_cell_but_keeps_its_failure(answer, failed):
    """A flat reader cannot say how many companies a median holds: inject nothing."""
    class _FlatOnly:
        def get_benchmark_values(self, industry, sector, metrics, period_type):
            assert period_type == "ttm"
            return answer

    cells, got = C._read_ttm_peer_cells(_FlatOnly(), "I", "S", ["pe_ratio"])
    assert cells == {}
    assert got is failed


def test_read_ttm_peer_cells_tolerates_malformed_cells():
    class _Rich:
        def get_benchmarks(self, industry, sector, metrics, period_type):
            return {
                "pe_ratio": {"TTM": {"value": 20.0, "n": 30}},
                "pb_ratio": {"ttm": {"value": 3.0, "n": 30}},   # another label spelling
                "ps_ratio": {"TTM": "junk"},
                "roe": "junk",
            }

    cells, failed = C._read_ttm_peer_cells(_Rich(), "I", "S", ["pe_ratio"])
    assert failed is False
    assert cells == {"pe_ratio": {"value": 20.0, "n": 30}, "pb_ratio": {"value": 3.0, "n": 30}}


def test_no_drill_down_metric_is_a_growth_metric_today():
    """The TTM injection is for LEVEL metrics; if a growth metric is ever added to the
    sector-history list, `_mature_ttm_value` already refuses it."""
    assert not [m for m in C._SECTOR_HISTORY_METRIC_NAMES
                if m.endswith(C._NO_TTM_POINT_SUFFIXES)]


# ═══════════════════════════════════════════════════════════════════════════
# 2 + 3. Competitor medians: the CURRENT mature median; a failed read degrades the report
# ═══════════════════════════════════════════════════════════════════════════


def test_current_sector_medians_maps_cells_and_rejects_junk():
    cells = {
        "operating_margin": {"value": 0.12, "level": "sector", "n": 300},
        "roe": {"value": float("nan"), "n": 300},
        "revenue_yoy": None,
    }
    assert C._current_sector_medians(cells) == {
        "operating_margin": 0.12, "roe": None, "revenue_yoy": None,
    }
    for junk in (None, [], "x", BenchmarkLookupFailed({})):
        assert C._current_sector_medians(junk) == {
            "operating_margin": None, "roe": None, "revenue_yoy": None,
        }


def test_competitor_medians_never_take_the_partial_current_year(monkeypatch, tag):
    """The old picker took max(label): the current year's partial cohort. The current
    benchmark is the sector TTM for op margin / ROE and the newest COMPLETE mature year
    for revenue growth (the TTM job computes no growth)."""
    sector = f"Sec-{tag}"
    lookup, _ = _real_lookup(monkeypatch, [
        _row(sector, "", "operating_margin", "ttm", "TTM", 0.15, 300, _FRESH),
        _row(sector, "", "roe", "ttm", "TTM", 0.18, 300, _FRESH),
        _row(sector, "", "operating_margin", "annual", str(_CUR_YEAR), 0.90, 39, _NOW.isoformat()),
        _row(sector, "", "revenue_yoy", "annual", str(_CUR_YEAR), 85.0, 21, _NOW.isoformat()),
        _row(sector, "", "revenue_yoy", "annual", str(_DONE_YEAR), 6.5, 300, _DONE_COMPUTED),
    ])
    cells = lookup.get_current_benchmarks("", sector, list(C._COMPETITOR_BENCHMARK_METRICS))
    assert C._current_sector_medians(cells) == {
        "operating_margin": 0.15, "roe": 0.18, "revenue_yoy": 6.5,
    }


class _RecordingLookup:
    """Stand-in lookup for one assembly; each read answers per the test."""

    def __init__(self, *, current=None, with_n=None):
        self.current_calls: List[tuple] = []
        self.with_n_calls: List[tuple] = []
        self._current = current
        self._with_n = with_n

    def get_current_benchmarks(self, industry, sector, metrics):
        self.current_calls.append((industry, sector, tuple(metrics)))
        if callable(self._current):
            return self._current(industry, sector, metrics)
        return {m: {"value": 0.2, "level": "sector", "n": 300} for m in metrics}

    def get_sector_benchmarks_with_n(self, sector, metrics, period_type="annual"):
        self.with_n_calls.append((sector, tuple(metrics), period_type))
        if callable(self._with_n):
            return self._with_n(sector, metrics, period_type)
        return {m: {} for m in metrics}

    def get_sector_benchmarks(self, *a, **k):
        raise AssertionError("competitor scoring must not read the raw annual map")

    def get_benchmarks(self, *a, **k):
        return {}

    def get_benchmark_values(self, *a, **k):
        return {}


def _install(monkeypatch, lookup):
    monkeypatch.setattr(sbl, "get_sector_benchmark_lookup", lambda: lookup)
    monkeypatch.setattr(moat_mod, "get_sector_benchmark_lookup", lambda: lookup)
    monkeypatch.setattr(moat_mod, "_service_singleton", None)


def _assemble(out):
    return C.TickerReportDataCollector().assemble_report(out, stage_a_fallback())


def test_a_clean_assembly_reads_current_sector_medians_and_is_not_degraded(monkeypatch):
    out = _make_collected_data()
    lookup = _RecordingLookup()
    _install(monkeypatch, lookup)
    report = _assemble(out)
    assert C.DEGRADED_SECTIONS_KEY not in report
    assert ("", "Technology", tuple(C._COMPETITOR_BENCHMARK_METRICS)) in lookup.current_calls, (
        "the competitor read must be the sector-level (industry='') current benchmark"
    )
    assert lookup.with_n_calls and lookup.with_n_calls[0][0] == "Technology"


@pytest.mark.parametrize("mode", ["failed", "raises"])
def test_a_failed_competitor_read_degrades_the_report_not_the_collection(monkeypatch, mode):
    def _answer(industry, sector, metrics):
        if industry == "" and set(metrics) == set(C._COMPETITOR_BENCHMARK_METRICS):
            if mode == "raises":
                raise RuntimeError("Cloudflare 520")
            return BenchmarkLookupFailed({m: None for m in metrics})
        return {m: {"value": 20.0, "level": "industry", "n": 50} for m in metrics}

    out = _make_collected_data()
    before = list(out.degraded_sections)
    _install(monkeypatch, _RecordingLookup(current=_answer))
    report = _assemble(out)
    assert C._COMPETITOR_BENCHMARKS_DEGRADED in report[C.DEGRADED_SECTIONS_KEY]
    assert C._MOAT_BENCHMARKS_DEGRADED not in report[C.DEGRADED_SECTIONS_KEY]
    assert out.degraded_sections == before, (
        "the collection is shared by every persona and already cached: never mutate it"
    )


@pytest.mark.parametrize("mode", ["failed", "raises", "not_a_mapping"])
def test_a_failed_moat_read_degrades_the_report(monkeypatch, mode):
    def _answer(sector, metrics, period_type):
        if mode == "raises":
            raise RuntimeError("Server disconnected")
        if mode == "not_a_mapping":
            return None
        return BenchmarkLookupFailed({m: {} for m in metrics})

    out = _make_collected_data()
    _install(monkeypatch, _RecordingLookup(with_n=_answer))
    report = _assemble(out)
    assert report[C.DEGRADED_SECTIONS_KEY] == [C._MOAT_BENCHMARKS_DEGRADED]
    assert out.degraded_sections == []


def test_two_assemblies_from_one_collection_do_not_accumulate(monkeypatch):
    """Personas share one collection; a failure in one assembly must not leak into the
    next one's report, and a repeated failure must not repeat its entry."""
    out = _make_collected_data()
    _install(monkeypatch, _RecordingLookup(
        with_n=lambda s, m, p: BenchmarkLookupFailed({x: {} for x in m})))
    first = _assemble(out)
    second = _assemble(out)
    assert first[C.DEGRADED_SECTIONS_KEY] == second[C.DEGRADED_SECTIONS_KEY] == [
        C._MOAT_BENCHMARKS_DEGRADED]
    _install(monkeypatch, _RecordingLookup())
    assert C.DEGRADED_SECTIONS_KEY not in _assemble(out)


def test_collection_losses_and_assembly_losses_are_both_carried(monkeypatch):
    out = _make_collected_data()
    out.degraded_sections = ["sector_history:benchmarks", "moat:benchmarks"]
    _install(monkeypatch, _RecordingLookup(
        with_n=lambda s, m, p: BenchmarkLookupFailed({x: {} for x in m})))
    report = _assemble(out)
    assert report[C.DEGRADED_SECTIONS_KEY] == ["sector_history:benchmarks", "moat:benchmarks"]


def test_a_malformed_peer_profile_does_not_degrade_the_report(monkeypatch):
    """A vendor-shape quirk is not an outage: skipped, not raised into the degrade path
    (that would keep the ticker out of every shared cache for good)."""
    out = _make_collected_data()
    # Dict-shaped quirks only: `_build_market_dynamics` (unrelated) already assumes dict
    # peer profiles, which is what the peer fetch returns.
    out.peer_profiles = [{"sector": 7}, {"sector": "   "}, {"symbol": "X"}, {"sector": None}]
    lookup = _RecordingLookup()
    _install(monkeypatch, lookup)
    report = _assemble(out)
    assert C.DEGRADED_SECTIONS_KEY not in report
    sectors = {sector for _industry, sector, _m in lookup.current_calls if _industry == ""}
    assert "Technology" in sectors and "   " not in sectors and 7 not in sectors


# ═══════════════════════════════════════════════════════════════════════════
# 4. Moat: the same year, else the newest older mature year; canonical sector name
# ═══════════════════════════════════════════════════════════════════════════


class _MoatLookup:
    def __init__(self, data=None, answer=None):
        self.data = data or {}
        self.answer = answer
        self.sectors: List[str] = []

    def get_sector_benchmarks_with_n(self, sector, metrics, period_type="annual"):
        assert period_type == "annual"
        self.sectors.append(sector)
        if self.answer is not None:
            return self.answer(sector, metrics)
        return {m: dict(self.data.get(m, {})) for m in metrics}


def _svc(lookup):
    svc = MoatScoringService.__new__(MoatScoringService)
    svc._lookup = lookup
    return svc


def _brand(svc, ratios_date, sector="Technology"):
    return svc.score(
        sector=sector, industry="Software", profile={},
        income=[], balance=[],
        ratios=[{"date": ratios_date, "grossProfitMargin": 0.80, "priceToSalesRatio": 8.0}],
        industry_tam=None,
    )


def test_moat_uses_the_focal_years_median_not_a_newer_one():
    lookup = _MoatLookup({
        "gross_margin": {"2024": {"median": 0.30, "n": 100}, "2025": {"median": 0.40, "n": 100},
                         "2026": {"median": 0.99, "n": 100}},
        "ps_ratio": {"2024": {"median": 3.0, "n": 100}, "2025": {"median": 4.0, "n": 100},
                     "2026": {"median": 50.0, "n": 100}},
    })
    drivers = {d.metric: d for d in _brand(_svc(lookup), "2025-12-31")[PILLAR_BRAND].drivers}
    assert (drivers["gross_margin"].period_used, drivers["gross_margin"].sector_median) == (
        "2025", 0.40)
    assert drivers["ps_ratio"].period_used == "2025"


def test_a_stale_filer_is_never_compared_with_a_newer_year():
    """Latest filing FY2024, sector rows only for 2025: unscored, not scored on 2025."""
    lookup = _MoatLookup({
        "gross_margin": {"2025": {"median": 0.40, "n": 100}},
        "ps_ratio": {"2025": {"median": 4.0, "n": 100}},
    })
    brand = _brand(_svc(lookup), "2024-12-31")[PILLAR_BRAND]
    assert brand.score is None
    assert all(d.period_used is None for d in brand.drivers)


def test_a_thin_same_year_falls_to_the_newest_older_mature_year_at_the_floor():
    lookup = _MoatLookup({
        "gross_margin": {
            "2025": {"median": 0.99, "n": MATURE_SAMPLE_FLOOR - 1},   # thin
            "2024": {"median": 0.40, "n": MATURE_SAMPLE_FLOOR},       # exactly mature
            "2023": {"median": 0.10, "n": 500},
        },
        "ps_ratio": {"2025": {"median": 4.0, "n": 300}},
    })
    drivers = {d.metric: d for d in _brand(_svc(lookup), "2025-12-31")[PILLAR_BRAND].drivers}
    assert (drivers["gross_margin"].period_used, drivers["gross_margin"].sample_size) == (
        "2024", MATURE_SAMPLE_FLOOR)
    assert drivers["ps_ratio"].period_used == "2025"


def test_a_52_53_week_year_ending_jan_3_is_its_prior_year():
    """Cadence-style FY2025 closes 2026-01-03; peers key it "2025" (annual_benchmark_key)."""
    lookup = _MoatLookup({
        "gross_margin": {"2025": {"median": 0.40, "n": 100}, "2026": {"median": 0.99, "n": 100}},
        "ps_ratio": {"2025": {"median": 4.0, "n": 100}, "2026": {"median": 50.0, "n": 100}},
    })
    drivers = {d.metric: d for d in _brand(_svc(lookup), "2026-01-03")[PILLAR_BRAND].drivers}
    assert drivers["gross_margin"].period_used == "2025"


@pytest.mark.parametrize("raw, canonical", [
    ("Information Technology", "Technology"),
    ("Financials", "Financial Services"),
    ("Health Care", "Healthcare"),
    ("  Consumer Staples  ", "Consumer Defensive"),
    ("Technology", "Technology"),
])
def test_moat_reads_the_canonical_sector_name(raw, canonical):
    lookup = _MoatLookup({})
    _brand(_svc(lookup), "2025-12-31", sector=raw)
    assert lookup.sectors == [canonical]


@pytest.mark.parametrize("raw", [None, "", "   ", 42])
def test_moat_with_no_usable_sector_reads_nothing_and_is_not_a_failure(raw):
    lookup = _MoatLookup({})
    result = _brand(_svc(lookup), "2025-12-31", sector=raw)
    assert lookup.sectors == []
    assert isinstance(result, MoatScores) and not lookup_failed(result)


@pytest.mark.parametrize("answer, failed", [
    (lambda s, m: BenchmarkLookupFailed({x: {} for x in m}), True),
    (lambda s, m: (_ for _ in ()).throw(RuntimeError("520")), True),
    (lambda s, m: None, True),
    (lambda s, m: [], True),
    (lambda s, m: {x: {} for x in m}, False),     # a sector with no rows is an answer
])
def test_moat_flags_a_failed_read_and_only_a_failed_read(answer, failed):
    result = _brand(_svc(_MoatLookup(answer=answer)), "2025-12-31")
    assert lookup_failed(result) is failed
    assert list(result) == moat_mod.PILLAR_ORDER


@pytest.mark.parametrize("payload", [
    {"median": float("nan"), "n": 100},
    {"median": float("inf"), "n": 100},
    {"median": None, "n": 100},
    {"median": True, "n": 100},
    {"median": "abc", "n": 100},
    {"median": 0.4, "n": None},
    {"median": 0.4, "n": True},
    {"median": 0.4, "n": float("nan")},
    {"median": 0.4},
    "junk",
    None,
])
def test_pick_year_skips_malformed_payloads(payload):
    assert _pick_year_at_or_before({"2025": payload}, "2025") is None
    # …and keeps walking to an older usable year
    got = _pick_year_at_or_before({"2025": payload, "2023": {"median": 0.3, "n": 50}}, "2025")
    assert got == {"median": 0.3, "period": "2023", "n": 50}


@pytest.mark.parametrize("target", ["", None, "FY25", "20250", 2025.5, "TTM"])
def test_pick_year_without_a_usable_target_year_is_none(target):
    assert _pick_year_at_or_before({"2025": {"median": 0.4, "n": 50}}, target) is None


def test_pick_year_ignores_non_year_labels():
    data = {"TTM": {"median": 9.0, "n": 900}, "Q3'25": {"median": 9.0, "n": 900},
            "20250": {"median": 9.0, "n": 900}, "2024": {"median": 0.4, "n": 50}}
    assert _pick_year_at_or_before(data, "2025") == {"median": 0.4, "period": "2024", "n": 50}


def test_deferred_revenue_is_compared_with_the_older_of_its_two_statements():
    lookup = _MoatLookup({
        "deferred_revenue_to_revenue": {"2025": {"median": 99.0, "n": 100},
                                        "2024": {"median": 10.0, "n": 100}},
    })
    res = _svc(lookup).score(
        sector="Technology", industry="Software", profile={},
        income=[{"date": "2024-12-31", "revenue": 100.0}],
        balance=[{"date": "2025-12-31", "deferredRevenue": 20.0}],
        ratios=[], industry_tam=None,
    )
    driver = next(d for d in res[moat_mod.PILLAR_SWITCHING].drivers
                  if d.metric == "deferred_revenue_to_revenue")
    assert (driver.period_used, driver.sector_median) == ("2024", 10.0)


def test_msft_shape_an_incomplete_sector_year_is_hidden_by_the_real_lookup(monkeypatch, tag):
    """Through the REAL lookup: MSFT's FY2026 (June 2026) is compared with 2025, because
    the "2026" sector cell computed 2026-10-04 (a partial cohort) is never served."""
    sector = f"Sec-{tag}"
    lookup, _ = _real_lookup(monkeypatch, [
        _row(sector, "", "gross_margin", "annual", "2026", 0.99, 39, "2026-10-04T04:00:00+00:00"),
        _row(sector, "", "gross_margin", "annual", "2025", 0.40, 140, "2026-04-05T04:00:00+00:00"),
        _row(sector, "", "ps_ratio", "annual", "2026", 50.0, 39, "2026-10-04T04:00:00+00:00"),
        _row(sector, "", "ps_ratio", "annual", "2025", 4.0, 140, "2026-04-05T04:00:00+00:00"),
    ])
    svc = _svc(lookup)
    result = _brand(svc, "2026-06-30", sector=sector)
    drivers = {d.metric: d for d in result[PILLAR_BRAND].drivers}
    assert (drivers["gross_margin"].period_used, drivers["gross_margin"].sector_median) == (
        "2025", 0.40)
    assert result[PILLAR_BRAND].score == 7.5
    assert not lookup_failed(result)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Index P/E: sector TTM medians through the serving gate, >= 8 mature sectors
# ═══════════════════════════════════════════════════════════════════════════


def _pe_rows(values, *, n=200, computed=_FRESH, industry=""):
    return [_row(f"S{i}", industry, "pe_ratio", "ttm", "TTM", v, n, computed)
            for i, v in enumerate(values)]


def _index_pe(monkeypatch, rows, now=None):
    fake = _FakeSupabase(rows)
    monkeypatch.setattr(idx, "get_supabase", lambda: fake)
    return idx._compute_index_pe_from_sectors(now=now), fake


def test_index_pe_is_the_mean_of_the_sector_ttm_medians(monkeypatch):
    value, fake = _index_pe(monkeypatch, _pe_rows([20.0] * 8 + [24.0] * 3))
    assert value == round((20.0 * 8 + 24.0 * 3) / 11, 2)
    assert {"metric_name": "pe_ratio", "period_type": "ttm", "industry": ""}.items() <= (
        fake.log[0].items())


def test_exactly_eight_mature_sectors_is_enough_and_seven_is_not(monkeypatch):
    assert _index_pe(monkeypatch, _pe_rows([20.0] * 8))[0] == 20.0
    assert _index_pe(monkeypatch, _pe_rows([20.0] * 7))[0] is None


def test_a_thin_sector_does_not_count(monkeypatch):
    rows = _pe_rows([20.0] * 7)
    rows += [_row("Thin", "", "pe_ratio", "ttm", "TTM", 90.0, MATURE_SAMPLE_FLOOR - 1, _FRESH)]
    assert _index_pe(monkeypatch, rows)[0] is None
    rows[-1]["sample_size"] = MATURE_SAMPLE_FLOOR
    assert _index_pe(monkeypatch, rows)[0] == round((20.0 * 7 + 90.0) / 8, 2)


def test_a_stale_ttm_row_is_ignored(monkeypatch):
    """21 days is the serving gate's TTM age limit: a 22-day-old row is a zombie group."""
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    fresh = (now - timedelta(days=2)).isoformat()
    stale = (now - timedelta(days=22)).isoformat()
    rows = _pe_rows([20.0] * 8, computed=fresh)
    rows += [_row("Zombie", "", "pe_ratio", "ttm", "TTM", 90.0, 300, stale)]
    assert _index_pe(monkeypatch, rows, now=now)[0] == 20.0
    rows = _pe_rows([20.0] * 7, computed=fresh)
    rows += [_row("Zombie", "", "pe_ratio", "ttm", "TTM", 90.0, 300, stale)]
    assert _index_pe(monkeypatch, rows, now=now)[0] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -15.0, 0.0, None, True, "abc"])
def test_a_malformed_median_is_skipped_not_averaged(monkeypatch, bad):
    rows = _pe_rows([20.0] * 8)
    rows += [_row("Bad", "", "pe_ratio", "ttm", "TTM", bad, 300, _FRESH)]
    value = _index_pe(monkeypatch, rows)[0]
    assert value == 20.0 and math.isfinite(value)


@pytest.mark.parametrize("bad_n", [None, True, float("nan"), "lots", -40])
def test_a_malformed_sample_size_does_not_count(monkeypatch, bad_n):
    rows = _pe_rows([20.0] * 7)
    rows += [_row("BadN", "", "pe_ratio", "ttm", "TTM", 20.0, bad_n, _FRESH)]
    assert _index_pe(monkeypatch, rows)[0] is None


def test_a_duplicate_sector_counts_once_with_its_newest_row(monkeypatch):
    rows = _pe_rows([20.0] * 7)
    rows += [
        _row("Dup", "", "pe_ratio", "ttm", "TTM", 10.0, 300, (_NOW - timedelta(days=5)).isoformat()),
        _row("Dup", "", "pe_ratio", "ttm", "TTM", 36.0, 300, (_NOW - timedelta(days=1)).isoformat()),
    ]
    assert _index_pe(monkeypatch, rows)[0] == round((20.0 * 7 + 36.0) / 8, 2)


@pytest.mark.parametrize("sector", ["", "   ", None, 5])
def test_a_row_without_a_sector_name_is_skipped(monkeypatch, sector):
    rows = _pe_rows([20.0] * 7)
    rows += [_row(sector, "", "pe_ratio", "ttm", "TTM", 20.0, 300, _FRESH)]
    assert _index_pe(monkeypatch, rows)[0] is None


def test_industry_and_quarterly_rows_never_count(monkeypatch):
    rows = _pe_rows([20.0] * 7)
    rows += _pe_rows([90.0] * 11, industry="Some Industry")
    rows += [_row("S9", "", "pe_ratio", "calendar_quarter", "Q2'26", 23.7, 300, _FRESH)]
    assert _index_pe(monkeypatch, rows)[0] is None


def test_a_failed_read_is_none_and_logged(monkeypatch, caplog):
    class _Boom:
        def table(self, _n):
            raise RuntimeError("Server disconnected")

    monkeypatch.setattr(idx, "get_supabase", lambda: _Boom())
    with caplog.at_level("WARNING", logger=idx.logger.name):
        assert idx._compute_index_pe_from_sectors() is None
    assert any("index P/E" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_get_pe_reports_an_unknown_pe_as_zero_and_does_not_cache_it(monkeypatch):
    """The wire sentinel shipped builds read as unknown (`pe_known=False`)."""
    monkeypatch.setattr(idx, "_compute_index_pe_from_sectors", lambda: None)
    idx._cache.pop("idx:pe", None)
    svc = idx.IndexService.__new__(idx.IndexService)
    assert await svc._get_pe() == 0
    assert idx._cache_get("idx:pe") is None
