"""Report collector, round 2 of the 2026-10-07 peer-benchmark review (workstream
"report collector + moat round 2").

1. The drill-down joined annual peer rows on calendarYear / date[:4] while the producer
   (and growth_service, profit_power_service, the moat scorer) key them on
   `period_labels.annual_benchmark_key` (the year of the period end minus 7 days). A
   52/53-week year ending Jan 1-7 drew the NEXT year's peer median.
2. The drill-down's peer LINES read the per-period merge (`get_benchmarks`), so a line
   switched population wherever an industry's n crossed 20. They now read
   `get_benchmark_series` (one peer group per metric line). The current-year TTM point is
   drawn only when it is mature AND from the line's own peer group (or the line is empty).
3. A failed industry moat peer-average read (`get_pillar_benchmarks`) fell silently into the
   5.0 baseline and was shared-cached. It now rides the report's degraded sections.
4. `_narrow_profit_power` stripped the peer fields but kept `peer_group_levels`.

Hermetic: every Supabase read is a local fake; the REAL `SectorBenchmarkLookup` runs on top
of it where the one-population line is the thing under test.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List

import pytest

import app.services.agents.ticker_report_data_collector as C
import app.services.industry_moat_benchmark_service as imb_mod
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services.agents.narrative_prompts import stage_a_fallback
from app.services.growth_service import _compute_growth_points
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE,
    MATURE_SAMPLE_FLOOR,
    BenchmarkLookupFailed,
    lookup_failed,
)
from app.utils.period_labels import annual_benchmark_key

# The PostgREST fake + REAL lookup harness of the D5 suite, and (through it) the schema
# parity builder with its autouse stub of every live benchmark / moat lookup.
from test_report_moat_index_benchmarks_2026_10_07 import (  # noqa: F401
    _CUR_YEAR,
    _DONE_COMPUTED,
    _DONE_YEAR,
    _FRESH,
    _collector,
    _install,
    _real_lookup,
    _RecordingLookup,
    _row,
    tag,
)
from test_ticker_report_schema_parity import (  # noqa: F401
    _make_collected_data,
    _no_live_benchmark_lookup,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Annual join key = annual_benchmark_key (display stays on the fiscal year)
# ═══════════════════════════════════════════════════════════════════════════


def test_a_year_ending_jan_3_joins_its_prior_years_peer_row_like_growth():
    """Cadence FY2025 closes 2026-01-03: the producer files it under "2025"."""
    rec = {"date": "2026-01-03", "fiscalYear": "2025", "revenue": 100.0}
    join_key, display, cal_year, quarter, sort_date = C._history_period_id(rec, quarterly=False)
    assert (join_key, display, cal_year, quarter) == ("2025", "2025", 2025, None)
    assert sort_date == "2026-01-03"
    # The same key growth_service joins its annual bars on.
    growth_key = _compute_growth_points([rec], "revenue", False)[0]["_match_period"]
    assert join_key == growth_key == annual_benchmark_key(rec)

    # End to end through the overlay: the bar draws the 2025 median, not 2026's.
    keys = C._period_calendar_keys([rec], [], quarterly=False)
    assert keys == {"2025": (2025, None)}
    sector_map = C._sector_period_map({"2025": 0.40, "2026": 0.99})
    series, has = C._aligned_sector_series(
        [{"period": "2025", "value": 42.0}], keys, sector_map, to_percent=True,
    )
    assert has and series == [{"period": "2025", "value": 40.0}]


def test_two_years_ending_in_one_calendar_year_keep_two_bars_and_their_own_peers():
    """Kellanova-style: FY2021 ends 2022-01-01, FY2022 ends 2022-12-31. The old key gave
    both "2022", so the index kept ONE of them; the YoY anchor is now the real prior year."""
    income = [  # FMP order: newest first
        {"date": "2022-12-31", "fiscalYear": "2022", "revenue": 110.0, "epsDiluted": 2.2},
        {"date": "2022-01-01", "fiscalYear": "2021", "revenue": 100.0, "epsDiluted": 2.0},
    ]
    ratios = [
        {"date": "2022-12-31", "fiscalYear": "2022", "grossProfitMargin": 0.50},
        {"date": "2022-01-01", "fiscalYear": "2021", "grossProfitMargin": 0.40},
    ]
    hist = C._fundamentals_history_for_period(income, [], [], [], ratios, {}, quarterly=False)
    assert [p["period"] for p in hist["gross_margin"]] == ["2021", "2022"]
    assert [p["value"] for p in hist["gross_margin"]] == [40.0, 50.0]
    assert hist["revenue_growth"] == [{"period": "2022", "value": 10.0}]

    keys = C._period_calendar_keys(income, ratios, quarterly=False)
    assert keys == {"2021": (2021, None), "2022": (2022, None)}
    series, _ = C._aligned_sector_series(
        hist["gross_margin"], keys, C._sector_period_map({"2021": 0.30, "2022": 0.35}),
        to_percent=True,
    )
    assert [p["value"] for p in series] == [30.0, 35.0]


@pytest.mark.parametrize(
    "rec, expected_key, expected_display",
    [
        ({"date": "2025-12-31", "fiscalYear": "2025"}, "2025", "2025"),
        ({"date": "2025-01-08", "fiscalYear": "2024"}, "2025", "2024"),   # day 8: no spill
        ({"date": "2026-01-07", "fiscalYear": "2025"}, "2025", "2025"),   # spill boundary
        ({"date": "2026-02-01", "fiscalYear": "2025"}, "2026", "2025"),   # Home Depot shape
        ({"calendarYear": "2024"}, "2024", "2024"),                       # undated → calendarYear
        ({"calendarYear": 2024, "date": "2024"}, "2024", "2024"),         # unparsable date
        ({"calendarYear": "2023", "date": "junk-date"}, "2023", "2023"),
        ({"date": "2024-09-30"}, "2024", "2024"),                         # no fiscalYear
    ],
)
def test_annual_join_key_edges(rec, expected_key, expected_display):
    join_key, display, cal_year, _q, _sort = C._history_period_id(rec, quarterly=False)
    assert (join_key, display, cal_year) == (expected_key, expected_display, int(expected_key))


@pytest.mark.parametrize("rec", [None, "x", {}, {"date": ""}, {"calendarYear": "ERR"},
                                 {"date": "abc"}])
def test_an_unkeyable_annual_row_is_still_dropped(rec):
    assert C._history_period_id(rec, quarterly=False) is None


def test_the_quarterly_key_is_unchanged():
    key, label, cal_year, q, _ = C._history_period_id(
        {"fiscalYear": 2025, "period": "Q4", "date": "2026-01-03"}, quarterly=True)
    assert (key, label, cal_year, q) == ("2025-Q4", "Q4 '25", 2025, 4)


# ═══════════════════════════════════════════════════════════════════════════
# 2. One peer group per drill-down line; the TTM point only on its own group's line
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_mature_industry_line_keeps_its_own_thin_older_years(monkeypatch, tag):
    """Industry mature at the newest year → the WHOLE line is the industry's, its thin
    older year included (never the sector's median for that year — a population step)."""
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    older = str(_DONE_YEAR - 1)
    _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "annual", older, 5.0, 12, _DONE_COMPUTED),
        _row(sector, industry, "pe_ratio", "annual", str(_DONE_YEAR), 7.0, 25, _DONE_COMPUTED),
        _row(sector, "", "pe_ratio", "annual", older, 50.0, 300, _DONE_COMPUTED),
        _row(sector, "", "pe_ratio", "annual", str(_DONE_YEAR), 60.0, 300, _DONE_COMPUTED),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert not lookup_failed(hist)
    assert hist["annual"]["pe_ratio"] == {older: 5.0, str(_DONE_YEAR): 7.0}


@pytest.mark.asyncio
async def test_a_thin_industry_at_the_newest_year_draws_the_sector_line_throughout(
    monkeypatch, tag,
):
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    older = str(_DONE_YEAR - 1)
    _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "annual", older, 7.0, 25, _DONE_COMPUTED),
        _row(sector, industry, "pe_ratio", "annual", str(_DONE_YEAR), 8.0, 12, _DONE_COMPUTED),
        _row(sector, "", "pe_ratio", "annual", older, 60.0, 300, _DONE_COMPUTED),
        _row(sector, "", "pe_ratio", "annual", str(_DONE_YEAR), 70.0, 300, _DONE_COMPUTED),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert hist["annual"]["pe_ratio"] == {older: 60.0, str(_DONE_YEAR): 70.0}


@pytest.mark.asyncio
async def test_the_calendar_quarter_line_is_one_population_too(monkeypatch, tag):
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    computed = f"{_DONE_YEAR + 1}-06-01T04:00:00+00:00"
    yy = str(_DONE_YEAR)[-2:]
    _real_lookup(monkeypatch, [
        _row(sector, industry, "roe", CALENDAR_QUARTER_PERIOD_TYPE, f"Q3'{yy}", 0.10, 9, computed),
        _row(sector, industry, "roe", CALENDAR_QUARTER_PERIOD_TYPE, f"Q4'{yy}", 0.12, 30, computed),
        _row(sector, "", "roe", CALENDAR_QUARTER_PERIOD_TYPE, f"Q3'{yy}", 0.50, 300, computed),
        _row(sector, "", "roe", CALENDAR_QUARTER_PERIOD_TYPE, f"Q4'{yy}", 0.60, 300, computed),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert hist["quarterly"]["roe"] == {f"Q3'{yy}": 0.10, f"Q4'{yy}": 0.12}


@pytest.mark.asyncio
async def test_a_sector_ttm_point_is_never_put_on_an_industry_line(monkeypatch, tag):
    """Industry line (mature at its newest year); industry TTM thin → the card's current
    value is the SECTOR TTM. The industry line ends at its last complete year."""
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    lookup, _ = _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "annual", str(_DONE_YEAR), 28.0, 25, _DONE_COMPUTED),
        _row(sector, industry, "pe_ratio", "ttm", "TTM", 35.0, 13, _FRESH),
        _row(sector, "", "pe_ratio", "ttm", "TTM", 22.0, 300, _FRESH),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert hist["annual"]["pe_ratio"] == {str(_DONE_YEAR): 28.0}
    card = lookup.get_current_benchmarks(industry, sector, ["pe_ratio"])["pe_ratio"]
    assert (card["value"], card["level"]) == (22.0, "sector")


@pytest.mark.asyncio
async def test_an_industry_ttm_point_is_never_put_on_a_sector_line(monkeypatch, tag):
    """Thin industry in the annual history (sector line), but the weekly TTM job reached
    20 industry members: the industry TTM is the card's value, not the line's point."""
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "annual", str(_DONE_YEAR), 30.0, 10, _DONE_COMPUTED),
        _row(sector, "", "pe_ratio", "annual", str(_DONE_YEAR), 18.0, 300, _DONE_COMPUTED),
        _row(sector, industry, "pe_ratio", "ttm", "TTM", 33.0, MATURE_SAMPLE_FLOOR, _FRESH),
        _row(sector, "", "pe_ratio", "ttm", "TTM", 21.0, 300, _FRESH),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert hist["annual"]["pe_ratio"] == {str(_DONE_YEAR): 18.0}


@pytest.mark.asyncio
async def test_a_same_group_ttm_point_is_drawn(monkeypatch, tag):
    sector, industry = f"Sec-{tag}", f"Ind-{tag}"
    _real_lookup(monkeypatch, [
        _row(sector, industry, "pe_ratio", "annual", str(_DONE_YEAR), 30.0, 10, _DONE_COMPUTED),
        _row(sector, "", "pe_ratio", "annual", str(_DONE_YEAR), 18.0, 300, _DONE_COMPUTED),
        _row(sector, industry, "pe_ratio", "ttm", "TTM", 33.0, 13, _FRESH),
        _row(sector, "", "pe_ratio", "ttm", "TTM", 21.0, 300, _FRESH),
    ])
    hist = await _collector()._fetch_sector_benchmark_history(industry, sector)
    assert hist["annual"]["pe_ratio"] == {str(_DONE_YEAR): 18.0, str(_CUR_YEAR): 21.0}


@pytest.mark.parametrize(
    "ttm_cell, line, line_level, fits",
    [
        # an empty line: the TTM point is the whole line
        ({"value": 20.0, "level": "sector", "n": 99}, None, None, True),
        ({"value": 20.0, "level": "sector", "n": 99}, {}, None, True),
        ({"value": 20.0, "level": "industry", "n": 99}, {"2024": None}, None, True),
        ({"value": 20.0, "level": "industry", "n": 99}, {"2024": float("nan")}, None, True),
        ({"value": 20.0, "level": None, "n": 99}, {}, None, True),
        # a drawn line: same group only
        ({"value": 20.0, "level": "sector", "n": 99}, {"2024": 18.0}, "sector", True),
        ({"value": 20.0, "level": "industry", "n": 99}, {"2024": 18.0}, "industry", True),
        ({"value": 20.0, "level": "industry", "n": 99}, {"2024": 18.0}, "sector", False),
        ({"value": 20.0, "level": "sector", "n": 99}, {"2024": 18.0}, "industry", False),
        ({"value": 20.0, "level": "sector", "n": 99}, {"2024": 18.0}, None, False),  # mixed
        ({"value": 20.0, "n": 99}, {"2024": 18.0}, "sector", False),               # no level
        ({"value": 20.0, "level": "peers", "n": 99}, {"2024": 18.0}, "peers", False),
        ("junk", {"2024": 18.0}, "sector", False),
        ({"value": 20.0, "level": "sector", "n": 99}, "junk", "sector", True),
    ],
)
def test_ttm_point_fits_line(ttm_cell, line, line_level, fits):
    assert C._ttm_point_fits_line(ttm_cell, line, line_level) is fits


class _SeriesAndMerged:
    """A lookup with both readers: the line must come from the series reader."""

    def __init__(self, series_answer):
        self.series_answer = series_answer
        self.calls: List[str] = []

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        self.calls.append(f"series:{period_type}")
        return self.series_answer

    def get_benchmarks(self, industry, sector, metrics, period_type):
        self.calls.append(f"merged:{period_type}")
        return {"pe_ratio": {"2024": {"value": 99.0, "level": "sector", "n": 999}}}


def test_read_peer_line_prefers_the_series_reader_and_reports_one_level():
    lookup = _SeriesAndMerged({
        "pe_ratio": {"2023": {"value": 5.0, "level": "industry", "n": 12},
                     "2024": {"value": 7.0, "level": "industry", "n": 25}},
        "roe": {},
        "pb_ratio": {"2024": {"value": None, "level": "sector", "n": 30}},
        "ps_ratio": "junk",
    })
    flat, levels, failed = C._read_peer_line(lookup, "I", "S", ["pe_ratio"], "annual")
    assert lookup.calls == ["series:annual"]
    assert failed is False
    assert flat["pe_ratio"] == {"2023": 5.0, "2024": 7.0}
    assert levels == {"pe_ratio": "industry", "roe": None, "pb_ratio": None}
    assert "ps_ratio" not in flat


@pytest.mark.parametrize("answer, failed", [
    (BenchmarkLookupFailed({"pe_ratio": {}}), True),
    (None, True),
    ([], True),
    ({"pe_ratio": {}}, False),
])
def test_read_peer_line_flags_a_failed_series_read(answer, failed):
    flat, levels, got = C._read_peer_line(_SeriesAndMerged(answer), "I", "S", ["pe_ratio"],
                                          "annual")
    assert got is failed
    assert levels == ({"pe_ratio": None} if isinstance(answer, dict) else {})


def test_a_merged_only_double_with_mixed_levels_has_no_line_level():
    class _MergedOnly:
        def get_benchmarks(self, industry, sector, metrics, period_type):
            return {"pe_ratio": {"2023": {"value": 50.0, "level": "sector", "n": 300},
                                 "2024": {"value": 7.0, "level": "industry", "n": 25}}}

    flat, levels, failed = C._read_peer_line(_MergedOnly(), "I", "S", ["pe_ratio"], "annual")
    assert flat == {"pe_ratio": {"2023": 50.0, "2024": 7.0}}
    assert levels == {"pe_ratio": None} and failed is False


def test_a_malformed_level_reads_as_no_level_never_as_a_failure():
    lookup = _SeriesAndMerged({
        "pe_ratio": {"2023": {"value": 5.0, "level": ["industry"], "n": 30},
                     "2024": {"value": 7.0, "level": ["industry"], "n": 30}},
        "pb_ratio": {"2024": {"value": 2.0, "level": {"x": 1}, "n": 30}},
        "roe": {"2024": {"value": 0.1, "n": 30}},
    })
    flat, levels, failed = C._read_peer_line(lookup, "I", "S", ["pe_ratio"], "annual")
    assert failed is False and flat["pe_ratio"] == {"2023": 5.0, "2024": 7.0}
    assert levels == {"pe_ratio": None, "pb_ratio": None, "roe": None}


@pytest.mark.asyncio
async def test_a_failed_series_read_flags_the_history(monkeypatch, tag):
    class _Lookup:
        def get_benchmark_series(self, industry, sector, metrics, period_type):
            if period_type == "annual":
                return BenchmarkLookupFailed({m: {} for m in metrics})
            return {m: {} for m in metrics}

        def get_benchmarks(self, industry, sector, metrics, period_type):
            return {"pe_ratio": {"TTM": {"value": 20.0, "level": "sector", "n": 300}}}

    import app.services.sector_benchmark_lookup as sbl

    monkeypatch.setattr(sbl, "get_sector_benchmark_lookup", lambda: _Lookup())
    hist = await _collector()._fetch_sector_benchmark_history(f"Ind-{tag}", f"Sec-{tag}")
    assert lookup_failed(hist)
    # The annual line loaded nothing, so the TTM point is the whole (one-group) line.
    assert hist["annual"]["pe_ratio"] == {str(_CUR_YEAR): 20.0}


# ═══════════════════════════════════════════════════════════════════════════
# 3. A failed industry moat peer-average read degrades the REPORT (never the collection)
# ═══════════════════════════════════════════════════════════════════════════


class _PillarLookup:
    def __init__(self, answer):
        self.answer = answer
        self.industries: List[Any] = []

    def get_pillar_benchmarks(self, industry):
        self.industries.append(industry)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def _assemble_with_pillars(monkeypatch, answer, *, out=None, lookup=None):
    out = out if out is not None else _make_collected_data()
    _install(monkeypatch, lookup or _RecordingLookup())
    pillars = _PillarLookup(answer)
    monkeypatch.setattr(imb_mod, "_lookup_singleton", pillars)
    report = C.TickerReportDataCollector().assemble_report(out, stage_a_fallback())
    return report, out, pillars


def _peer_scores(report) -> Dict[str, float]:
    return {d["name"]: d["peer_score"] for d in report["moat_competition"]["dimensions"]}


@pytest.mark.parametrize("answer", [
    BenchmarkLookupFailed(),
    RuntimeError("Cloudflare 520"),
    None,
    ["not", "a", "mapping"],
])
def test_a_failed_pillar_read_degrades_the_report(monkeypatch, caplog, answer):
    with caplog.at_level(logging.WARNING, logger=C.logger.name):
        report, out, pillars = _assemble_with_pillars(monkeypatch, answer)
    assert pillars.industries == ["Consumer Electronics"]
    assert report[C.DEGRADED_SECTIONS_KEY] == [C._MOAT_BENCHMARKS_DEGRADED]
    assert out.degraded_sections == [], "the shared collection is never written"
    assert set(_peer_scores(report).values()) == {5.0}, "the 5.0 baseline, as before"
    assert any("moat_peer_averages" in r.getMessage() and "Consumer Electronics"
               in r.getMessage() for r in caplog.records)


def test_peer_averages_overlay_the_radar_and_do_not_degrade(monkeypatch):
    from app.services.moat_scoring_service import PILLAR_ORDER

    answer = {PILLAR_ORDER[0]: 6.4, PILLAR_ORDER[1]: 3.1}
    report, _out, _p = _assemble_with_pillars(monkeypatch, answer)
    assert C.DEGRADED_SECTIONS_KEY not in report
    scores = _peer_scores(report)
    assert scores[PILLAR_ORDER[0]] == 6.4 and scores[PILLAR_ORDER[1]] == 3.1


def test_an_industry_with_no_rows_is_not_degraded(monkeypatch):
    report, _out, _p = _assemble_with_pillars(monkeypatch, {})
    assert C.DEGRADED_SECTIONS_KEY not in report


def test_both_moat_reads_failing_record_the_marker_once(monkeypatch):
    lookup = _RecordingLookup(with_n=lambda s, m, p: BenchmarkLookupFailed({x: {} for x in m}))
    report, _out, _p = _assemble_with_pillars(
        monkeypatch, BenchmarkLookupFailed(), lookup=lookup,
    )
    assert report[C.DEGRADED_SECTIONS_KEY] == [C._MOAT_BENCHMARKS_DEGRADED]


@pytest.mark.parametrize("industry", [None, "", "   ", 7, ["Semis"]])
def test_a_malformed_industry_is_not_read_and_not_degraded(monkeypatch, industry):
    """A vendor-shape quirk is not an outage: a raise on it would keep the ticker out of
    every shared cache for good."""
    out = _make_collected_data()
    out.profile = dict(out.profile, industry=industry)
    report, _out, pillars = _assemble_with_pillars(monkeypatch, RuntimeError("boom"), out=out)
    assert pillars.industries == []
    assert C.DEGRADED_SECTIONS_KEY not in report


def test_repeated_assemblies_do_not_accumulate_the_marker(monkeypatch):
    out = _make_collected_data()
    first, _o, _p = _assemble_with_pillars(monkeypatch, BenchmarkLookupFailed(), out=out)
    second, _o, _p = _assemble_with_pillars(monkeypatch, BenchmarkLookupFailed(), out=out)
    assert first[C.DEGRADED_SECTIONS_KEY] == second[C.DEGRADED_SECTIONS_KEY] == [
        C._MOAT_BENCHMARKS_DEGRADED]
    clean, _o, _p = _assemble_with_pillars(monkeypatch, {}, out=out)
    assert C.DEGRADED_SECTIONS_KEY not in clean


# ═══════════════════════════════════════════════════════════════════════════
# 4. `_narrow_profit_power` keeps peer_group_levels in step with the peer lines
# ═══════════════════════════════════════════════════════════════════════════


def _pp_point(period, *, peer=12.0):
    return ProfitPowerDataPointSchema(
        period=period, gross_margin=40.0, operating_margin=20.0, fcf_margin=15.0,
        net_margin=10.0, sector_average_net_margin=peer, sector_average_gross_margin=peer,
        sector_average_operating_margin=peer, sector_average_fcf_margin=peer,
    )


def _profit_power(degraded, *, levels=None, level="industry"):
    return ProfitPowerResponse(
        symbol="AAPL",
        annual=[_pp_point("2024"), _pp_point("2025")],
        quarterly=[_pp_point("Q1 '26")],
        peer_group_level=level,
        peer_group_levels=(
            {"annual": "industry", "quarterly": "sector"} if levels is None else levels
        ),
        degraded=list(degraded),
    )


def _narrowed(degraded, **kw):
    out = C.CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.profit_power = _profit_power(degraded, **kw)
    C._refuse_degraded_financials(out)
    return out


@pytest.mark.parametrize("reason", ["profile", "benchmarks"])
def test_a_stripped_peer_overlay_names_no_peer_group(reason):
    out = _narrowed([reason])
    pp = out.profit_power
    assert pp is not None
    assert pp.peer_group_levels == {} and pp.peer_group_level is None
    for p in pp.annual + pp.quarterly:
        assert all(getattr(p, f) is None for f in C._PROFIT_POWER_PEER_FIELDS)
        assert p.net_margin == 10.0, "the company's own margins stay"
    assert out.degraded_sections == [f"profit_power:{reason}"]


def test_an_emptied_quarterly_list_loses_its_level_only():
    pp = _narrowed(["quarterly_income"]).profit_power
    assert pp.quarterly == []
    assert pp.peer_group_levels == {"annual": "industry"}
    assert pp.peer_group_level == "industry"


def test_an_emptied_annual_list_loses_its_level_and_the_single_level_follows():
    pp = _narrowed(["annual_income"]).profit_power
    assert pp.annual == [] and len(pp.quarterly) == 1
    assert pp.peer_group_levels == {"quarterly": "sector"}
    assert pp.peer_group_level == "sector", "the quarterly line is the only one left"


def test_a_cash_flow_leg_keeps_every_level():
    pp = _narrowed(["annual_cashflow"]).profit_power
    assert all(p.fcf_margin is None and p.sector_average_fcf_margin is None for p in pp.annual)
    assert pp.peer_group_levels == {"annual": "industry", "quarterly": "sector"}
    assert pp.peer_group_level == "industry"


def test_an_income_leg_plus_a_profile_failure_clears_everything():
    pp = _narrowed(["quarterly_income", "profile"]).profit_power
    assert pp.quarterly == [] and pp.peer_group_levels == {} and pp.peer_group_level is None


def test_both_income_legs_drop_the_section():
    out = _narrowed(["annual_income", "quarterly_income"])
    assert out.profit_power is None


def test_a_build_without_per_series_levels_keeps_its_single_level():
    """A build with no per-series levels has nothing to prune; the single level is not
    guessed at."""
    pp = _narrowed(["quarterly_income"], levels={}).profit_power
    assert pp.peer_group_levels == {} and pp.peer_group_level == "industry"


def test_the_narrowed_build_still_validates():
    pp = _narrowed(["annual_income"]).profit_power
    again = ProfitPowerResponse.model_validate(pp.model_dump())
    assert again.peer_group_levels == {"quarterly": "sector"}
    assert not any(math.isnan(p.net_margin) for p in again.quarterly if p.net_margin)


# Profit Power v7 (2026-10-08) also carries per-margin keys ("annual.fcf_margin"): a frozen
# report must not keep a level for a margin line the narrower emptied or blanked.
_V7_LEVELS = {
    "annual": "industry", "quarterly": "sector",
    "annual.net_margin": "industry", "annual.fcf_margin": "sector",
    "quarterly.net_margin": "sector", "quarterly.fcf_margin": "sector",
}


def test_an_emptied_period_drops_its_per_margin_keys_too():
    pp = _narrowed(["quarterly_income"], levels=dict(_V7_LEVELS)).profit_power
    assert pp.peer_group_levels == {
        "annual": "industry", "annual.net_margin": "industry", "annual.fcf_margin": "sector",
    }


def test_a_blanked_fcf_line_drops_only_its_own_key():
    pp = _narrowed(["annual_cashflow"], levels=dict(_V7_LEVELS)).profit_power
    assert all(p.fcf_margin is None and p.sector_average_fcf_margin is None for p in pp.annual)
    assert "annual.fcf_margin" not in pp.peer_group_levels
    assert pp.peer_group_levels["annual.net_margin"] == "industry"
    assert pp.peer_group_levels["quarterly.fcf_margin"] == "sector"
    assert pp.peer_group_levels["annual"] == "industry"
