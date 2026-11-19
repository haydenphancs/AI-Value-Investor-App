"""The free-cash-flow GROWTH peer median reads FMP's zero-filled cash-flow row as no reading.

FMP fills a cash-flow statement it does not have with zeros (HSBC, a half-year filer, reads
freeCashFlow = 0 for every quarter; JPM's not-yet-filed newest quarter read the same —
`app.utils.margins.free_cash_flow_reading`). On 2026-11-14 the owner decided ("Fix it too")
that such a row is a gap on the company's own Growth series (`growth_service`,
``reading=free_cash_flow_reading``). The peer side still computed `fcf_yoy` on the raw field
(`sector_benchmark_service._compute_yoy_for_records`), so a peer whose year or quarter was
zero-filled voted -100% into the industry and sector median the company line is compared
with. `METRIC_CONFIGS` now gives `fcf_yoy` the same reading, applied to the current period
AND its year-ago base; a blank base gives no figure and never reaches back to an older row.

Hermetic: inline FMP-shaped rows, the real producer functions, no Supabase, no FMP.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone

import pytest

import app.services.growth_service as gs
import app.services.industry_benchmark_service as ibs
import app.services.sector_benchmark_service as sbs
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE
from app.utils.margins import free_cash_flow_reading
from app.utils.period_labels import format_calendar_quarter

FCF = "freeCashFlow"
CQ = CALENDAR_QUARTER_PERIOD_TYPE
_NOW = datetime(2026, 11, 14, tzinfo=timezone.utc).isoformat()
_QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}

# Values FMP's zero fill arrives as (a number, a negative zero, or text).
_ZERO_FILLS = [0, 0.0, -0.0, "0", "0.00"]
# Values that are no figure at all.
_NOT_A_FIGURE = [None, float("nan"), float("inf"), float("-inf"), "nan", "", "n/a"]


def _annual(values, start=2019):
    """One Dec-31 annual cash-flow row per value, oldest first (`calendarYear` = its year)."""
    return [
        {"date": f"{start + i}-12-31", "calendarYear": str(start + i),
         "fiscalYear": str(start + i), "period": "FY", FCF: v}
        for i, v in enumerate(values)
    ]


def _quarterly(values, start=(2023, 1)):
    """One calendar-aligned quarterly row per value, oldest first."""
    year, q = start
    rows = []
    for v in values:
        rows.append({"date": f"{year}-{_QUARTER_END[q]}", "calendarYear": str(year),
                     "fiscalYear": str(year), "period": f"Q{q}", FCF: v})
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)
    return rows


def _fcf_config() -> dict:
    return next(mc for mc in sbs.METRIC_CONFIGS if mc["name"] == "fcf_yoy")


def _sector_svc() -> sbs.SectorBenchmarkService:
    return sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)


def _industry_svc() -> ibs.IndustryBenchmarkService:
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc._sb = _sector_svc()
    svc._calendar_quarter_blocked = False
    return svc


def _peer_yoy(rows, *, quarterly=False):
    return sbs._compute_yoy_for_records(
        rows, FCF, quarterly, reading=_fcf_config()["reading"],
    )


# Five real peers whose 2023 → 2024 FCF grew +10 … +50%, so the 2024 median is +30%.
_REAL_PEERS = [
    {"cashflow_annual": _annual([100.0, 100.0 + g], start=2023)} for g in (10, 20, 30, 40, 50)
]
_REAL_2024 = [10.0, 20.0, 30.0, 40.0, 50.0]


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The declaration
# ══════════════════════════════════════════════════════════════════════════════════════


def test_fcf_yoy_declares_the_free_cash_flow_reading():
    """The metric the Growth card's FCF line is compared with reads FCF like the company
    line does. Mutation: removing the key brings every -100% below back."""
    assert _fcf_config()["reading"] is free_cash_flow_reading


def test_every_reading_is_callable_and_sits_on_a_metric_with_a_field():
    """A `computed` metric has no field to read: a `reading` there would do nothing, which
    is the kind of key that looks like a fix and is not."""
    for mc in sbs.METRIC_CONFIGS:
        if "reading" in mc:
            assert callable(mc["reading"]), mc["name"]
            assert mc["type"] in ("yoy", "qoq", "direct") and mc.get("field"), mc["name"]


def test_only_fcf_yoy_reads_its_values_today():
    """Scope pin: the income-statement growth metrics still count every value as filed."""
    assert {mc["name"] for mc in sbs.METRIC_CONFIGS if "reading" in mc} == {"fcf_yoy"}


def test_the_helpers_default_to_the_values_as_filed():
    """Without a reading the helpers are unchanged (`moat_scoring_service` reads revenue
    growth through `_compute_yoy_for_records` with no reading)."""
    rows = _annual([100.0, 0.0], start=2023)
    assert sbs._compute_yoy_for_records(rows, FCF, False) == {"2024": -100.0}


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. One peer's series
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("zero", _ZERO_FILLS)
def test_a_zero_filled_current_year_gives_no_growth_figure(zero):
    """A real base and a zero-filled year: no figure, never -100%."""
    assert _peer_yoy(_annual([80.0, 100.0, zero], start=2022)) == {"2023": 25.0}


@pytest.mark.parametrize("zero", _ZERO_FILLS)
def test_a_zero_filled_base_gives_no_figure_and_never_reaches_back(zero):
    """2022 real, 2023 zero-filled, 2024 and 2025 real. 2024 must NOT be compared with
    2022 (+20%): its base is 2023, which is blank. The chain resumes at 2025."""
    out = _peer_yoy(_annual([100.0, zero, 120.0, 150.0], start=2022))
    assert out == {"2025": 25.0}


@pytest.mark.parametrize("bad", _NOT_A_FIGURE)
@pytest.mark.parametrize("side", ["current", "base"])
def test_nan_none_inf_and_text_are_no_reading(bad, side):
    """A non-figure on either side of a comparison gives that period no figure; the
    neighbouring comparisons are untouched."""
    values = [100.0, 110.0, 121.0, 133.1]          # +10% each year
    values[2 if side == "current" else 1] = bad
    out = _peer_yoy(_annual(values, start=2021))
    expected = {"2022": 10.0, "2023": 10.0, "2024": 10.0}
    if side == "current":
        expected.pop("2023")
        expected.pop("2024")                         # 2023 is 2024's base
    else:
        expected.pop("2022")
        expected.pop("2023")
    assert out == pytest.approx(expected)
    assert all(math.isfinite(v) for v in out.values())


def test_a_missing_field_is_no_reading():
    rows = _annual([100.0, 110.0, 121.0], start=2022)
    del rows[1][FCF]
    assert _peer_yoy(rows) == {}


@pytest.mark.parametrize("values,expected", [
    ([-50.0, 25.0], {"2024": 150.0}),        # a burn turned positive: real, kept
    ([25.0, -40.0], {"2024": -260.0}),       # a positive year turned into a burn
    ([-10.0, -30.0], {"2024": -200.0}),      # a deepening burn reads negative
    ([-30.0, -10.0], {"2024": 66.67}),       # a shrinking burn reads positive
    ([1e-6, 2e-6], {"2024": 100.0}),         # tiny but not zero: a figure
])
def test_a_real_negative_or_tiny_fcf_is_kept(values, expected):
    """Only an EXACT zero is the fill. A negative FCF is a cash burn, a real figure."""
    assert _peer_yoy(_annual(values, start=2023)) == pytest.approx(expected)


def test_the_jpm_shaped_unfiled_newest_quarter_gives_no_figure():
    """JPM: every quarter real, the newest not yet filed and zero-filled."""
    rows = _quarterly([100.0, 110.0, 120.0, 130.0, 110.0, 121.0, 132.0, 0.0])
    out = _peer_yoy(rows, quarterly=True)
    assert out == pytest.approx({
        format_calendar_quarter((2024, 1)): 10.0,
        format_calendar_quarter((2024, 2)): 10.0,
        format_calendar_quarter((2024, 3)): 10.0,
    })


def test_the_hsbc_shaped_series_gives_no_quarterly_figure_at_all():
    """HSBC: every quarter zero-filled."""
    assert _peer_yoy(_quarterly([0.0] * 8), quarterly=True) == {}


def test_the_qoq_helper_reads_both_sides_too():
    rows = _quarterly([100.0, 0.0, 120.0, 132.0])
    out = sbs._compute_qoq_for_records(rows, FCF, reading=free_cash_flow_reading)
    assert out == pytest.approx({format_calendar_quarter((2023, 4)): 10.0})
    # As filed, the zero-filled quarter reads -100%.
    raw = sbs._compute_qoq_for_records(rows, FCF)
    assert raw[format_calendar_quarter((2023, 2))] == -100.0


def test_a_direct_metric_with_a_reading_drops_the_zero_fill():
    """The documented `direct` behaviour of the key (no live metric uses it yet)."""
    config = {"name": "x", "source": "cashflow", "field": FCF, "type": "direct",
              "reading": free_cash_flow_reading}
    companies = [{"cashflow_annual": _annual([v], start=2024)} for v in (0.0, -5.0, 7.0)]
    out = _sector_svc()._collect_metric_values(companies, config, "annual")
    assert sorted(out["2024"]) == [-5.0, 7.0]


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. The peer median the live recompute writes
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("zero", _ZERO_FILLS)
def test_a_zero_filled_peer_casts_no_vote_in_the_annual_cell(zero):
    """Five real peers + one whose 2024 was zero-filled. Before: six votes, one -100%."""
    hsbc = {"cashflow_annual": _annual([100.0, zero], start=2023)}
    out = _sector_svc()._collect_metric_values(_REAL_PEERS + [hsbc], _fcf_config(), "annual")
    assert sorted(out["2024"]) == _REAL_2024


def test_the_live_recompute_writes_the_median_of_real_readings_only():
    """Through `IndustryBenchmarkService._value_lists_from` → `_rows_from_values` (the
    path `recompute_all` runs). HSBC-shaped (every row zero-filled) and JPM-shaped (the
    newest row zero-filled) peers join five real ones: the 2024 median stays +30%, the
    sample size 5. With the raw field it read +25% from 6, one of them -100%."""
    hsbc = {"cashflow_annual": _annual([0.0, 0.0], start=2023),
            "cashflow_quarterly": _quarterly([0.0] * 8)}
    jpm = {"cashflow_annual": _annual([100.0, 0.0], start=2023),
           "cashflow_quarterly": _quarterly([100.0, 110.0, 120.0, 130.0, 110.0, 121.0, 132.0, 0.0])}
    real_quarters = [
        {"cashflow_quarterly": _quarterly([100.0] * 4 + [100.0 + g] * 4)}
        for g in (10, 20, 30, 40, 50)
    ]
    companies = [dict(a, **q) for a, q in zip(_REAL_PEERS, real_quarters)] + [hsbc, jpm]
    svc = _industry_svc()
    values = svc._value_lists_from(companies)

    assert sorted(values[("fcf_yoy", "annual", "2024")]) == _REAL_2024
    q4 = format_calendar_quarter((2024, 4))
    assert sorted(values[("fcf_yoy", CQ, q4)]) == _REAL_2024    # JPM's unfiled Q4 is out
    assert all(v != -100.0 for (m, _, _), vs in values.items() if m == "fcf_yoy" for v in vs)

    rows = svc._rows_from_values("Financial Services", "Banks - Diversified", values, _NOW)
    cell = next(r for r in rows if (r["metric_name"], r["period_type"], r["period_label"])
                == ("fcf_yoy", "annual", "2024"))
    assert cell["median_value"] == pytest.approx(30.0)
    assert cell["sample_size"] == 5


def test_a_cell_left_under_the_sample_floor_is_not_written():
    """Four real peers + one zero fill: four readings is under MIN_SAMPLE_SIZE (5), so no
    row — never a median topped up by a -100% that is not a figure."""
    assert sbs.MIN_SAMPLE_SIZE == 5
    hsbc = {"cashflow_annual": _annual([100.0, 0.0], start=2023)}
    svc = _industry_svc()
    values = svc._value_lists_from(_REAL_PEERS[:4] + [hsbc])
    assert len(values[("fcf_yoy", "annual", "2024")]) == 4
    rows = svc._rows_from_values("Technology", "Software - Application", values, _NOW)
    assert not any(r["metric_name"] == "fcf_yoy" for r in rows)


def test_the_peer_floor_watch_counts_the_cell_the_way_the_median_reads_it():
    """`_cell_members` (the FS peer-floor watch) names a zero-filled peer as no member."""
    companies = {f"P{i}": c for i, c in enumerate(_REAL_PEERS)}
    companies["HSBC"] = {"cashflow_annual": _annual([100.0, 0.0], start=2023)}
    members = _industry_svc()._cell_members(companies, "fcf_yoy", 2024)
    assert members == {f"P{i}" for i in range(5)}


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. Company line and peer median agree
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("values", [
    [100.0, 0.0, 120.0, 150.0],          # a zero-filled middle year
    [100.0, 110.0, 0.0],                 # a zero-filled newest year
    [-50.0, 25.0, -40.0],                # real negatives
    [100.0, float("nan"), 121.0, 133.1],
])
def test_the_company_growth_series_and_the_peer_producer_agree(values):
    """The same annual rows through the company's Growth series and the peer producer: the
    same periods (joined on `_match_period`, the benchmark key) carry a YoY, with the same
    value. Mutation: dropping the peer reading adds a -100% the company line never draws."""
    rows = _annual(values, start=2021)
    company_yoy = {
        p["_match_period"]: p["yoy_change_percent"]
        for p in gs._compute_growth_points(rows, FCF, is_quarterly=False,
                                           reading=free_cash_flow_reading)
        if p["yoy_change_percent"] is not None
    }
    peer = _peer_yoy(rows)
    assert peer.keys() == company_yoy.keys()
    for year, v in peer.items():
        assert company_yoy[year] == pytest.approx(v, abs=0.01)


def test_the_median_is_over_finite_values():
    """Sanity: every value the producer collects is finite (the winsorizer's NaN trap)."""
    weird = {"cashflow_annual": _annual([float("nan"), 0.0, "x", 5.0, -0.0], start=2020)}
    out = _sector_svc()._collect_metric_values(_REAL_PEERS + [weird], _fcf_config(), "annual")
    for label, vals in out.items():
        assert all(math.isfinite(v) for v in vals), label
    assert statistics.median(out["2024"]) == pytest.approx(30.0)
