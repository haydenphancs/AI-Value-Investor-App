"""The report drill-down's two-line legend (owner decision 2026-11-13, "Replace it").

THE BUG: `_fetch_sector_benchmark_history` read the TTM peer cells and drew the mature TTM
median as the CURRENT calendar year's annual peer point (the 2026-06-24 rule "keep fiscal
history line + TTM current point"). A stored current-year annual row is never served before
year end + 75 days, so the only companies that ever had a bar at that key were off-calendar
filers whose fiscal year had already CLOSED (ORCL May, MSFT June, AAPL September, NVDA
January, and a Home Depot-shaped year ending early February), and the legend compared that
closed year's ratio with TODAY's median: ORCL read "1.42x vs industry" in red, against the
card's own TTM-to-TTM 0.62x.

THE FIX, pinned here:

A. the history makes exactly two reads (annual, calendar quarter) and never asks for TTM;
   every annual peer line ends at its last served complete period, for every fiscal-year
   shape (and the Earnings Yield reciprocal line with it); a failed read is still flagged
   and is never filled from a TTM cell; a withheld line stays withheld;
B. each Price-card row carries its own comparison as numbers (`numeric_value`,
   `peer_median`, display units), and they agree with the row's name to the digit;
C. the same on the Financial Health card (`_row_numbers`, both build paths);
D. `_snapshot_to_card` maps them onto the charted rows as `current_value` /
   `current_peer_median` / `current_score` (Earnings Yield takes the P/E row's score),
   finite-only, and the card survives Pydantic and strict JSON;
E. the numbers survive the collection cache and the snapshot cache; a legacy payload
   decodes with None and draws the period line only.
F. the Growth card (owner decision 2026-11-13, "Name the periods" + "Say not out yet"):
   each valued row carries the newest annual YoY, that SAME year's peer YoY (None while the
   year is not reported) and its "FY…" label; `_snapshot_to_card` turns them into
   `current_value` / `current_peer_median` / `current_score` / `current_period`, the score
   riding with any number (Earnings Yield excepted, unchanged); the N/M and "—" rows carry
   nothing; NaN/inf never reach a row; ORCL's -5911.7% FCF YoY is kept exactly.

Hermetic: every benchmark read is a local double, every FMP / Health Check call a stub.
Dates are relative to the year the TEST runs in (with 2026 they are exactly the period
ends named above), so the "current year" asserts never go vacuous across New Year.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.services.agents.ticker_report_data_collector as C
import app.services.growth_service as growth_mod
import app.services.growth_snapshot_service as gss
import app.services.health_snapshot_service as hs
import app.services.sector_benchmark_lookup as sbl
import app.services.valuation_snapshot_service as vss
from app.config import settings
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.schemas.health_check import HealthCheckResponse
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.schemas.ticker_report import DeepDiveMetricCardResponse, DeepDiveMetricResponse
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed, lookup_failed
from app.services.ticker_data_cache import _deserialize, _serialize

# Reused harnesses (imported, never copied): the Price-card builder and row picker, the
# Health Check / FMP stubs and the snapshot_cache fake, and the collection-cache sample.
from test_price_card_earnings_yield_2026_10_09 import _card, _m
from test_snapshot_cards_2026_10_07_profitability_health import (
    _Supabase as _SnapshotCacheTable,
    _hc_metric,
    _health_service,
)
from test_ticker_data_cache import _field_names, _sample


_CQ = sbl.CALENDAR_QUARTER_PERIOD_TYPE
_TTM = sbl.TTM_PERIOD_TYPE
_IND, _SEC = "Software - Infrastructure", "Technology"
# ORCL, recorded 2026-11-13: TTM P/E 18.2134 against the Software - Infrastructure TTM
# median 29.3172 (0.62x), while its closed FY2026 annual P/E read 1.42x that median.
_ORCL_PE_TTM = 18.2134
_TTM_PE_MEDIAN = 29.3172
_CLOSED_YEAR_PE = 41.63          # 1.42 x 29.3172: the mispaired bar the old line sat on
_ANNUAL_MEDIANS = {-2: 24.0, -1: 26.0}   # by offset from the current year: complete years


def _cur_year() -> int:
    """The calendar year the TEST runs in (the key the removed TTM point was labelled with)."""
    return datetime.now(timezone.utc).year


def _collector() -> C.TickerReportDataCollector:
    return C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)


def _cell(value: float, level: str = "industry", n: int = 25) -> Dict[str, Any]:
    return {"value": value, "level": level, "n": n, "peer_group_name": "Peers"}


# ═══════════════════════════════════════════════════════════════════════════
# A benchmark double that answers EVERY reader a drill-down ever used — the series and
# merged readers, the flat view and the current-snapshot reader — and records each
# (reader, period_type) it was asked for. It holds mature TTM cells, so the removed
# injection would have found one to draw.
# ═══════════════════════════════════════════════════════════════════════════


class _PeerTable:
    def __init__(
        self, *, annual: Dict[str, Dict[str, Any]], quarterly: Optional[Dict] = None,
        ttm: Optional[Dict[str, Dict[str, Any]]] = None,
        fail: Tuple[str, ...] = (), raise_on: Tuple[str, ...] = (),
    ) -> None:
        self.lines = {"annual": annual, _CQ: quarterly or {}, _TTM: ttm or {}}
        self.fail, self.raise_on = set(fail), set(raise_on)
        self.asked: List[Tuple[str, str]] = []

    def _cells(self, reader: str, period_type: str, metrics: List[str]):
        self.asked.append((reader, period_type))
        if period_type in self.raise_on:
            raise RuntimeError("Server disconnected")
        if period_type in self.fail:
            return BenchmarkLookupFailed({m: {} for m in metrics})
        src = self.lines.get(period_type, {})
        return {
            m: {label: dict(cell) for label, cell in src.get(m, {}).items()} for m in metrics
        }

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        return self._cells("get_benchmark_series", period_type, metrics)

    def get_benchmarks(self, industry, sector, metrics, period_type):
        return self._cells("get_benchmarks", period_type, metrics)

    def get_benchmark_values(self, industry, sector, metrics, period_type):
        rich = self._cells("get_benchmark_values", period_type, metrics)
        flat = {m: {lab: c["value"] for lab, c in p.items()} for m, p in rich.items()}
        return BenchmarkLookupFailed(flat) if lookup_failed(rich) else flat

    def get_current_benchmarks(self, industry, sector, metrics):
        self.asked.append(("get_current_benchmarks", _TTM))
        ttm = self.lines[_TTM]
        return {m: (dict(ttm[m]["TTM"]) if m in ttm else None) for m in metrics}

    def period_types(self) -> List[str]:
        return [period_type for _reader, period_type in self.asked]


def _table(y: int, **kw) -> _PeerTable:
    """Complete Y-2 / Y-1 industry P/E years, a Y-1 SECTOR gross-margin line, one quarter,
    and a MATURE industry TTM cell for every drill-down metric (ROE has nothing else)."""
    yy = str(y - 1)[-2:]
    return _PeerTable(
        annual={
            "pe_ratio": {str(y + off): _cell(v) for off, v in _ANNUAL_MEDIANS.items()},
            "gross_margin": {str(y - 1): _cell(0.55, "sector", 300)},
        },
        quarterly={"pe_ratio": {f"Q2'{yy}": _cell(25.0)}},
        ttm={
            m: {"TTM": _cell(_TTM_PE_MEDIAN if m == "pe_ratio" else 1.5)}
            for m in C._SECTOR_HISTORY_METRIC_NAMES
        },
        **kw,
    )


async def _history(monkeypatch, table: _PeerTable, industry: str = _IND, sector: str = _SEC):
    monkeypatch.setattr(sbl, "get_sector_benchmark_lookup", lambda: table)
    return await _collector()._fetch_sector_benchmark_history(industry, sector)


# ═══════════════════════════════════════════════════════════════════════════
# A. The peer line ends at its last complete period; no TTM read is made
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_peer_history_makes_two_line_reads_and_never_asks_for_ttm(monkeypatch):
    y = _cur_year()
    table = _table(y)
    hist = await _history(monkeypatch, table)

    assert not lookup_failed(hist)
    assert sorted(table.asked) == sorted(
        [("get_benchmark_series", "annual"), ("get_benchmark_series", _CQ)]), table.asked
    assert _TTM not in table.period_types(), table.asked
    assert ("get_current_benchmarks", _TTM) not in table.asked

    assert hist["annual"]["pe_ratio"] == {str(y - 2): 24.0, str(y - 1): 26.0}
    assert hist["levels"]["annual"]["pe_ratio"] == "industry"
    assert hist["annual"]["gross_margin"] == {str(y - 1): 0.55}
    assert hist["levels"]["annual"]["gross_margin"] == "sector", "one peer group per line"
    assert hist["quarterly"]["pe_ratio"] == {f"Q2'{str(y - 1)[-2:]}": 25.0}
    for metric, line in hist["annual"].items():
        assert str(y) not in line, (metric, line)
        assert _TTM_PE_MEDIAN not in line.values() and 1.5 not in line.values(), (metric, line)
    # A metric with ONLY a mature TTM cell used to get that point as its whole line (and the
    # TTM cell's level): it now has no line and no level.
    assert not hist["annual"].get("roe")
    assert hist["levels"]["annual"].get("roe") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["annual", _CQ])
@pytest.mark.parametrize("mode", ["failed", "raises"])
async def test_a_failed_line_read_is_flagged_and_never_filled_from_a_ttm_cell(
    monkeypatch, failing, mode,
):
    """A failed annual read left an empty line that the old injection filled with the TTM
    median of EVERY metric (an empty line "fits" any group). Now nothing is drawn there,
    the other line is kept, and the history stays flagged for the shared caches."""
    y = _cur_year()
    table = _table(y, **({"fail": (failing,)} if mode == "failed" else {"raise_on": (failing,)}))
    hist = await _history(monkeypatch, table)

    assert lookup_failed(hist)
    assert _TTM not in table.period_types(), table.asked
    assert sorted(table.period_types()) == sorted(["annual", _CQ])
    failed_tag, kept_tag = ("annual", "quarterly") if failing == "annual" else ("quarterly", "annual")
    assert not any(hist[failed_tag].values()), hist[failed_tag]
    assert all(level is None for level in hist["levels"][failed_tag].values())
    if kept_tag == "annual":
        assert hist["annual"]["pe_ratio"] == {str(y - 2): 24.0, str(y - 1): 26.0}
    else:
        assert hist["quarterly"]["pe_ratio"] == {f"Q2'{str(y - 1)[-2:]}": 25.0}
    assert str(y) not in hist["annual"].get("pe_ratio", {})


@pytest.mark.asyncio
async def test_a_withheld_line_stays_withheld_and_no_ttm_cell_takes_its_place(monkeypatch):
    """An exchange's current ratio: the Financial Services SECTOR line is no peer group
    (PERMANENT, 2026-10-08). The old code also refused the TTM point for it; with no TTM
    read the line and its level are simply gone, while the industry's own P/E line stays."""
    y = _cur_year()
    industry, sector = "Financial - Data & Stock Exchanges", "Financial Services"
    assert C._withheld_peer_line_reason("current_ratio", "sector", industry, sector) == (
        "financial_services_sector")
    assert C._withheld_peer_line_reason("pe_ratio", "industry", industry, sector) is None
    table = _PeerTable(
        annual={"current_ratio": {str(y - 1): _cell(1.3, "sector", 300)},
                "pe_ratio": {str(y - 1): _cell(22.0, "industry", 12)}},
        ttm={"current_ratio": {"TTM": _cell(1.25, "sector", 300)},
             "pe_ratio": {"TTM": _cell(23.0, "industry", 12)}},
    )
    hist = await _history(monkeypatch, table, industry, sector)
    assert not lookup_failed(hist)
    assert "current_ratio" not in hist["annual"]
    assert "current_ratio" not in hist["levels"]["annual"]
    assert hist["annual"]["pe_ratio"] == {str(y - 1): 22.0}
    assert hist["levels"]["annual"]["pe_ratio"] == "industry"
    assert _TTM not in table.period_types()


# Annual period ends as (date-year offset, "MM-DD", fiscalYear offset) from the current
# year, OLDEST first. With 2026: ORCL 2025-05-31 + 2026-05-31, MSFT 2025-06-30 + 2026-06-30,
# AAPL 2025-09-27 + 2026-09-26, NVDA 2025-01-26 + 2026-01-25, Home Depot's FY2025 ending
# 2026-02-01 (label "2025", joined to calendar 2026), and KO's December years 2024 + 2025.
# The flag says whether the newer year's JOIN key is the current year (so a check of the
# shape itself guards the asserts below against going vacuous).
_FISCAL_SHAPES = [
    pytest.param([(-1, "05-31", -1), (0, "05-31", 0)], True, id="ORCL-may"),
    pytest.param([(-1, "06-30", -1), (0, "06-30", 0)], True, id="MSFT-june"),
    pytest.param([(-1, "09-27", -1), (0, "09-26", 0)], True, id="AAPL-september"),
    pytest.param([(-1, "01-26", -1), (0, "01-25", 0)], True, id="NVDA-january"),
    pytest.param([(-1, "02-02", -2), (0, "02-01", -1)], True, id="HD-start-year-named"),
    pytest.param([(-2, "12-31", -2), (-1, "12-31", -1)], False, id="KO-december"),
]


def _join_year(period_end: str) -> int:
    """The producer's annual join key, computed here independently: the year of the
    period end minus 7 days (`annual_benchmark_key`)."""
    return (date.fromisoformat(period_end) - timedelta(days=7)).year


def _annual_rows(y: int, ends) -> List[Tuple[str, str]]:
    """[(period end, fiscal-year label)] NEWEST first, as FMP lists them."""
    return [(f"{y + dy}-{md}", str(y + fy)) for dy, md, fy in reversed(ends)]


def _collected(rows: List[Tuple[str, str]], hist: Dict[str, Any], ticker: str = "T"):
    pes = [_CLOSED_YEAR_PE, 30.0]       # newest year first
    income = [{"date": d, "fiscalYear": fy, "period": "FY", "revenue": 5.0e10}
              for d, fy in rows]
    ratios = [{"date": d, "fiscalYear": fy, "period": "FY", "priceToEarningsRatio": pe}
              for (d, fy), pe in zip(rows, pes)]
    return C.CollectedTickerData(
        ticker=ticker, persona_key="warren_buffett", income=income, ratios=ratios,
        sector_benchmark_history=hist,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ends, newest_is_current_year", _FISCAL_SHAPES)
async def test_every_fiscal_shape_ends_its_peer_line_at_the_last_complete_year(
    monkeypatch, ends, newest_is_current_year,
):
    y = _cur_year()
    hist = await _history(monkeypatch, _table(y))
    rows = _annual_rows(y, ends)
    assert (_join_year(rows[0][0]) == y) is newest_is_current_year, rows

    history = C._build_fundamentals_history(_collected(rows, hist))
    medians = {y + off: v for off, v in _ANNUAL_MEDIANS.items()}
    oldest_first = list(reversed(rows))

    for key, peer_point in (
        ("pe", lambda med: round(med, 2)),
        # 100 / the P/E line of the same period and peer group.
        ("earnings_yield", lambda med: round(100.0 / med, 2)),
    ):
        payload = history[key]
        assert [p["period"] for p in payload["annual"]] == [fy for _d, fy in oldest_first]
        expected = [
            {"period": fy,
             "value": peer_point(medians[_join_year(d)]) if _join_year(d) in medians else None}
            for d, fy in oldest_first
        ]
        assert payload["sector_annual"] == expected, (key, payload["sector_annual"])
        assert payload["sector_annual_level"] == "industry"
        drawn = [p["value"] for p in payload["sector_annual"]]
        assert round(_TTM_PE_MEDIAN, 2) not in drawn
        assert round(100.0 / _TTM_PE_MEDIAN, 2) not in drawn
        for (d, fy), point in zip(oldest_first, payload["sector_annual"]):
            if _join_year(d) == y:
                assert point == {"period": fy, "value": None}, (key, point)
        # The company's own bar for the closed year is still drawn: only the peer line ends.
        assert payload["annual"][-1]["value"] is not None

    if newest_is_current_year:
        assert history["pe"]["sector_annual"][-1]["value"] is None
        assert history["pe"]["sector_annual"][-2]["value"] == medians[y - 1]
        assert history["pe"]["annual"][-1]["value"] == _CLOSED_YEAR_PE
    else:
        assert [p["value"] for p in history["pe"]["sector_annual"]] == [24.0, 26.0]


# ═══════════════════════════════════════════════════════════════════════════
# B. Price card: numeric_value / peer_median
# ═══════════════════════════════════════════════════════════════════════════

_RATIO_RE = re.compile(r"\((-?[\d.]+)x sector avg ")
_MEDIAN_RE = re.compile(r"sector avg (-?[\d.]+)(%?)\)$")
_PLACEHOLDERS = ("—", "Neg.", "N/A", "N/M")


def _assert_price_row(m: SnapshotMetricResponse) -> None:
    nv, pm = m.numeric_value, m.peer_median
    prints_median = "avg" in m.name
    assert (pm is None) is (not prints_median), m
    if m.peer_level is not None:
        assert m.peer_level in ("industry", "sector") and pm is not None, m
    if m.value in _PLACEHOLDERS or not m.value[:1].isdigit():
        assert nv is None, m
    for number in (nv, pm):
        assert number is None or (math.isfinite(number) and number > 0), m
    pct = m.metric_key == "earnings_yield"
    if pm is not None:
        printed, pct_sign = _MEDIAN_RE.search(m.name).groups()
        assert bool(pct_sign) is pct, m
        assert printed == (f"{pm:.2f}" if pct else vss._fmt_median(pm)), m
    if nv is not None:
        shown = float(m.value.rstrip("%"))
        assert shown == pytest.approx(nv, abs=0.005 + 1e-9), m
    # The name prints an "N.NNx" exactly when both numbers are there and their ratio does not
    # round to 0.00 (the yield row then still carries the printed yield — review 2026-11-13:
    # a "0.02%" yield beside a 5.00% median is a real reading, not an undefined one, and the
    # drill-down leaves out a "0.00×" just as the label does), and it is their ratio.
    ratio = _RATIO_RE.search(m.name)
    zero_ratio = (nv is not None and pm is not None and pct
                  and round((nv / 100) / (pm / 100), 2) <= 0)
    assert (ratio is not None) is (nv is not None and pm is not None and not zero_ratio), m
    if ratio is not None:
        quotient = nv / pm
        assert math.isfinite(quotient), m
        if pct:
            assert float(ratio.group(1)) == pytest.approx(quotient, abs=0.005 + 1e-9), m
        else:
            assert ratio.group(1) == f"{quotient:.2f}", m


def test_orcl_pe_row_carries_the_ttm_comparison_the_card_printed():
    snap = _card(fr={"priceToEarningsRatioTTM": _ORCL_PE_TTM},
                 bench={"pe_ratio": _TTM_PE_MEDIAN}, levels={"pe_ratio": "industry"},
                 ticker="ORCL")
    pe = _m(snap, "pe")
    assert pe.name == "P/E (0.62x sector avg 29.3)" and pe.value == "18.21"
    assert pe.numeric_value == _ORCL_PE_TTM, "the raw multiple the label divides, not 18.21"
    assert pe.peer_median == _TTM_PE_MEDIAN, "the unrounded median, not the printed 29.3"
    assert pe.peer_level == "industry"
    assert f"{pe.numeric_value / pe.peer_median:.2f}" == _RATIO_RE.search(pe.name).group(1)
    assert pe.score == 5        # 0.62 <= 0.7: the score the legend line is coloured by
    _assert_price_row(pe)

    ey = _m(snap, "earnings_yield")
    assert ey.value == "5.49%" and ey.peer_level == "industry"
    assert ey.numeric_value == pytest.approx(100 / 18.21, rel=1e-12), "1 / the DISPLAYED P/E"
    assert abs(ey.numeric_value - 100 / _ORCL_PE_TTM) > 1e-4
    assert ey.peer_median == pytest.approx(100 / _TTM_PE_MEDIAN, rel=1e-12)
    assert ey.name == "Earnings Yield (1.61x sector avg 3.41%)"
    assert f"{ey.numeric_value / ey.peer_median:.2f}" == "1.61"
    assert ey.score is None
    _assert_price_row(ey)


_PRICE_KEYS = ("priceToEarningsRatioTTM", "priceToBookRatioTTM", "priceToSalesRatioTTM",
               "priceToFreeCashFlowRatioTTM", "enterpriseValueMultipleTTM")
_BENCH_KEYS = ("pe_ratio", "pb_ratio", "ps_ratio", "pfcf_ratio", "ev_ebitda")


@pytest.mark.parametrize("value", [
    12.5, _ORCL_PE_TTM, 1500.0, 1e6, -7.0, 0, None, "NaN", float("nan"), float("inf"),
])
@pytest.mark.parametrize("median", [
    # 50000: a usable multiple median whose inverse (the yield row's 0.002%) prints "0.00%",
    # so the P/E row prints a median and the Earnings Yield row must print — and carry — none.
    _TTM_PE_MEDIAN, 4.25, 50000.0, 0, -3.0, 0.001, None, float("nan"), float("inf"),
])
@pytest.mark.parametrize("level", ["industry", None])
def test_every_price_row_number_agrees_with_its_name(value, median, level):
    """`peer_median` is None exactly when the name prints no median (0, negative, NaN, inf
    and a 0.001 that prints "0.00" are no median); `peer_level` implies it; a placeholder
    ("—", "Neg.", "N/A") has no `numeric_value`; and nothing is ever NaN or inf."""
    fr = {k: value for k in _PRICE_KEYS} if value is not None else {}
    bench = {k: median for k in _BENCH_KEYS}
    levels = {k: level for k in _BENCH_KEYS}
    snap = vss.build_price_snapshot(fr=fr, km={}, cf={}, inc={}, bs={}, profile={},
                                    bench=bench, bench_levels=levels, ticker="GRID")
    assert len(snap.metrics) == 6
    for m in snap.metrics:
        _assert_price_row(m)
        if level is None:
            assert m.peer_level is None
    by = {m.metric_key: m for m in snap.metrics}
    usable = isinstance(median, float) and math.isfinite(median) and round(median, 2) > 0
    positive = isinstance(value, float) and math.isfinite(value) and value > 0
    assert (by["pe"].peer_median is not None) is usable
    assert (by["pe"].numeric_value is not None) is positive
    if positive:
        assert by["pe"].numeric_value == value
    if usable:
        assert by["pe"].peer_median == median


def test_not_meaningful_rows_carry_no_numbers():
    """A bank's P/S, P/FCF and EV/EBITDA read "N/M" (`score_excluded_rows`): no value, no
    median, no level — even with every median present."""
    snap = vss.build_price_snapshot(
        fr={k: 9.5 for k in _PRICE_KEYS}, km={}, cf={}, inc={}, bs={}, profile={},
        bench={k: 12.0 for k in _BENCH_KEYS}, bench_levels={k: "industry" for k in _BENCH_KEYS},
        ticker="JPM", excluded_rows=frozenset({"ps", "pfcf", "ev_ebitda"}),
    )
    for key in ("ps", "pfcf", "ev_ebitda"):
        row = _m(snap, key)
        assert row.value == "N/M" and "avg" not in row.name
        assert (row.numeric_value, row.peer_median, row.peer_level) == (None, None, None)
    for key in ("pe", "pb"):
        row = _m(snap, key)
        assert (row.numeric_value, row.peer_median, row.peer_level) == (9.5, 12.0, "industry")
    for m in snap.metrics:
        _assert_price_row(m)


@pytest.mark.parametrize("withheld", [True, False])
def test_withheld_or_missing_peers_carry_no_median(withheld):
    """No median (a listed non-lender member's withheld peers, or an empty lookup): no
    `peer_median` and no level on any row. The value alone stays — the legend's current
    line can show "18.21x" with no peer figure."""
    snap = _card(fr={"priceToEarningsRatioTTM": _ORCL_PE_TTM}, bench={}, withheld=withheld)
    for m in snap.metrics:
        assert m.peer_median is None and m.peer_level is None, m
        _assert_price_row(m)
    assert _m(snap, "pe").numeric_value == _ORCL_PE_TTM
    assert _m(snap, "earnings_yield").numeric_value == pytest.approx(100 / 18.21, rel=1e-12)
    assert _m(snap, "pb").numeric_value is None


# ═══════════════════════════════════════════════════════════════════════════
# C. Financial Health card: numeric_value / peer_median
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("value, numeric", [
    (None, None), (float("nan"), None), (float("inf"), None), (True, None),
    (-3.5, -3.5), (0.0, 0.0), (1.8, 1.8), (2, 2.0),
])
@pytest.mark.parametrize("comparison", [None, 1.4, float("nan")])
def test_row_numbers_follow_the_rule_that_prints_the_median(value, numeric, comparison):
    got = hs._row_numbers(value, comparison)
    assert set(got) == {"numeric_value", "peer_median"}
    assert got["numeric_value"] == numeric
    if numeric is not None:
        assert isinstance(got["numeric_value"], float)
    # The median only where `_compared` prints one — and never a non-finite one.
    expected_median = 1.4 if (value is not None and comparison == 1.4) else None
    assert got["peer_median"] == expected_median
    for number in got.values():
        assert number is None or math.isfinite(number)
    if comparison != comparison:        # NaN: never reaches here (`split_peer_cells`)
        return
    name = hs._metric_name("current_ratio", value, comparison)
    assert ("(vs sector " in name) is (got["peer_median"] is not None), name


_VS_SECTOR = re.compile(r"\(vs sector (-?[\d.]+)\)$")


def _assert_health_row(m: SnapshotMetricResponse) -> None:
    printed = _VS_SECTOR.search(m.name)
    assert (printed is not None) is (m.peer_median is not None), m
    if printed is not None:
        assert printed.group(1) == f"{m.peer_median:.2f}", m
    if m.peer_level is not None:
        assert m.peer_median is not None, m
    if m.value in ("—", "N/M"):
        assert m.numeric_value is None, m
    for number in (m.numeric_value, m.peer_median):
        assert number is None or math.isfinite(number), m


@pytest.mark.asyncio
async def test_health_main_path_rows_carry_the_health_checks_own_numbers(monkeypatch):
    metrics = [
        _hc_metric("debt_to_equity", 0.4, 0.6, level="industry"),
        _hc_metric("current_ratio", 1.8, 1.4, level="sector"),
        _hc_metric("quick_ratio", 1.2, None, level="sector"),         # no comparison
        _hc_metric("interest_coverage", -3.5, 15.0, status="negative", level="sector"),
        _hc_metric("altman_z_score", 4.2),
        _hc_metric("pe_ratio", 25.0, 22.0, level="industry"),         # hidden on this card
        _hc_metric("roe", 30.0, 18.0, level="industry"),              # hidden on this card
    ]
    health = HealthCheckResponse(symbol="TEST", overall_rating="good", passed_count=3,
                                 total_count=4, metrics=metrics)
    _hs, svc = _health_service(monkeypatch, health)
    snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == []
    source = {hs._VERDICT_KEY.get(m.type): m for m in metrics}
    by = {m.metric_key: m for m in snap.metrics}
    assert set(by) == {"debt_to_equity", "current_ratio", "quick_ratio", "interest_coverage",
                       "altman_z"}
    for key, row in by.items():
        _assert_health_row(row)
        hc = source[key]
        assert row.numeric_value == hc.value, row
        assert row.peer_median == hc.comparison_value, row
    assert by["interest_coverage"].numeric_value == -3.5, "a negative coverage is a reading"
    assert by["interest_coverage"].name == "Interest Coverage (vs sector 15.00)"
    assert by["altman_z"].peer_median is None and by["altman_z"].numeric_value == 4.2
    assert by["quick_ratio"].peer_median is None and by["quick_ratio"].peer_level is None


@pytest.mark.asyncio
async def test_health_fallback_rows_carry_only_the_medians_they_print(monkeypatch):
    """The local fallback (Health Check down): a non-positive median is no benchmark, so no
    "vs sector" and no `peer_median`; Altman Z never has one."""
    _hs, svc = _health_service(
        monkeypatch, RuntimeError("health check exploded"),
        bench={"debt_to_equity": (0.6, "industry"), "current_ratio": (1.4, "sector"),
               "interest_coverage": (-2.0, "sector"), "quick_ratio": (0.0, "industry")},
    )
    snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == ["health_check"]
    by = {m.metric_key: m for m in snap.metrics}
    for row in snap.metrics:
        _assert_health_row(row)
        if row.numeric_value is not None and row.metric_key != "altman_z":
            assert row.value == f"{row.numeric_value:.2f}", row
    assert by["debt_to_equity"].peer_median == 0.6
    assert by["current_ratio"].peer_median == 1.4
    for key in ("interest_coverage", "quick_ratio"):
        assert by[key].peer_median is None, by[key]
        assert by[key].numeric_value is not None, "the value still prints"
    z = by["altman_z"]
    assert z.peer_median is None
    assert z.value == "—" or float(z.value) == pytest.approx(z.numeric_value)


# ═══════════════════════════════════════════════════════════════════════════
# D. _current_comparison + _snapshot_to_card
# ═══════════════════════════════════════════════════════════════════════════

_HISTORY_POINT = {"unit": "x", "annual": [{"period": "2025", "value": 20.0}], "quarterly": []}


def _history_lookup(*keys: str) -> Dict[str, Dict[str, Any]]:
    return {k: dict(_HISTORY_POINT) for k in keys}


def _row(**kw) -> SnapshotMetricResponse:
    return SnapshotMetricResponse(**kw)


def _price_snapshot(rows: List[SnapshotMetricResponse]) -> SnapshotItemResponse:
    return SnapshotItemResponse(category="Price", rating=4, metrics=rows,
                                full_report_available=True)


_CURRENT_KEYS = ("current_value", "current_peer_median", "current_score", "current_period")


def _current(md: Dict[str, Any]) -> Dict[str, Any]:
    return {k: md[k] for k in _CURRENT_KEYS if k in md}


def _validated(card: Dict[str, Any]) -> Dict[str, Any]:
    dump = DeepDiveMetricCardResponse.model_validate(card).model_dump()
    json.dumps(dump, allow_nan=False)
    return dump


def test_the_card_rows_carry_the_current_comparison_and_ey_takes_the_pe_score():
    rows = [
        _row(name="P/E (0.62x sector avg 29.3)", value="18.21", metric_key="pe", score=5,
             peer_level="industry", numeric_value=_ORCL_PE_TTM, peer_median=_TTM_PE_MEDIAN),
        # "Neg." beside a printed median: the median (and its score, when scored) only.
        _row(name="P/B (sector avg 4.25)", value="Neg.", metric_key="pb", score=None,
             peer_level="industry", numeric_value=None, peer_median=4.25),
        # A value with no median: no median, but the row's own score travels with the value
        # (the Growth "not out yet" rule, 2026-11-13: the line is coloured as the card judged
        # the row).
        _row(name="P/S", value="7.50", metric_key="ps", score=4, numeric_value=7.5),
        # Non-finite numbers (never produced by the services; a corrupted row): no key.
        _row(name="EV/EBITDA (1.10x sector avg 16.0)", value="17.60", metric_key="ev_ebitda",
             score=3, numeric_value=float("nan"), peer_median=float("inf")),
        # Unscored by design; its own score is ignored for the P/E row's.
        _row(name="Earnings Yield (1.61x sector avg 3.41%)", value="5.49%",
             metric_key="earnings_yield", score=2, peer_level="industry",
             numeric_value=100 / 18.21, peer_median=100 / _TTM_PE_MEDIAN),
    ]
    card = C._snapshot_to_card(
        "Valuation", _price_snapshot(rows),
        history_lookup=_history_lookup("pe", "pb", "ps", "ev_ebitda", "earnings_yield"),
        peer_group_level="industry",
    )
    by = {md["history_key"]: md for md in card["metrics"]}
    assert _current(by["pe"]) == {"current_value": _ORCL_PE_TTM,
                                  "current_peer_median": _TTM_PE_MEDIAN, "current_score": 5}
    assert _current(by["pb"]) == {"current_peer_median": 4.25}
    assert _current(by["ps"]) == {"current_value": 7.5, "current_score": 4}
    assert _current(by["ev_ebitda"]) == {}
    assert _current(by["earnings_yield"]) == {
        "current_value": 100 / 18.21, "current_peer_median": 100 / _TTM_PE_MEDIAN,
        "current_score": 5,
    }

    dump = _validated(card)
    dumped = {md["history_key"]: md for md in dump["metrics"]}
    for key, md in by.items():
        for field in _CURRENT_KEYS:
            assert dumped[key][field] == md.get(field), (key, field)
    assert dumped["ev_ebitda"]["current_value"] is None


def test_a_row_without_history_or_an_nm_row_carries_no_current_line():
    rows = [
        # No history entry for P/E here: never charted, so no legend line either.
        _row(name="P/E (0.62x sector avg 29.3)", value="18.21", metric_key="pe", score=5,
             peer_level="industry", numeric_value=_ORCL_PE_TTM, peer_median=_TTM_PE_MEDIAN),
        # "N/M" (`score_excluded_rows`) is never charted, whatever numbers it holds.
        _row(name="P/FCF", value="N/M", metric_key="pfcf", score=None,
             numeric_value=12.0, peer_median=20.0),
        _row(name="P/B (1.10x sector avg 4.25)", value="4.68", metric_key="pb", score=3,
             peer_level="industry", numeric_value=4.68, peer_median=4.25),
    ]
    card = C._snapshot_to_card("Valuation", _price_snapshot(rows),
                               history_lookup=_history_lookup("pfcf", "pb"))
    by = {md["label"]: md for md in card["metrics"]}
    assert "history_key" not in by["P/E (0.62x sector avg 29.3)"]
    assert _current(by["P/E (0.62x sector avg 29.3)"]) == {}
    assert "history_key" not in by["P/FCF"] and _current(by["P/FCF"]) == {}
    assert _current(by["P/B (1.10x sector avg 4.25)"]) == {
        "current_value": 4.68, "current_peer_median": 4.25, "current_score": 3}
    _validated(card)


def test_no_history_lookup_at_all_carries_no_current_line():
    rows = [_row(name="P/E (0.62x sector avg 29.3)", value="18.21", metric_key="pe", score=5,
                 numeric_value=_ORCL_PE_TTM, peer_median=_TTM_PE_MEDIAN)]
    card = C._snapshot_to_card("Valuation", _price_snapshot(rows), history_lookup=None)
    assert _current(card["metrics"][0]) == {}


def test_a_legacy_snapshot_row_yields_the_period_line_only():
    legacy = SnapshotMetricResponse.model_validate(
        {"name": "P/E (0.62x sector avg 29.3)", "value": "18.21", "metric_key": "pe",
         "score": 5, "peer_level": "industry"})
    assert (legacy.numeric_value, legacy.peer_median) == (None, None)
    card = C._snapshot_to_card("Valuation", _price_snapshot([legacy]),
                               history_lookup=_history_lookup("pe"))
    md = card["metrics"][0]
    assert md["history_key"] == "pe" and _current(md) == {}
    dumped = _validated(card)["metrics"][0]
    assert all(dumped[k] is None for k in _CURRENT_KEYS)
    # An object from before the fields existed at all (no attribute): nothing either.
    old = SimpleNamespace(name="P/E", value="18.21", metric_key="pe", score=5)
    assert C._current_comparison(old, [old]) == {}


@pytest.mark.parametrize("row, others, expected", [
    # A bool is not a score (nor a number).
    (dict(metric_key="pe", numeric_value=10.0, peer_median=20.0, score=True), [],
     {"current_value": 10.0, "current_peer_median": 20.0}),
    (dict(metric_key="pe", numeric_value=True, peer_median=False, score=4), [], {}),
    # Earnings Yield with no P/E row, or an unscored one: no score.
    (dict(metric_key="earnings_yield", numeric_value=5.0, peer_median=3.4, score=4), [],
     {"current_value": 5.0, "current_peer_median": 3.4}),
    (dict(metric_key="earnings_yield", numeric_value=5.0, peer_median=3.4, score=None),
     [dict(metric_key="pe", score=None)], {"current_value": 5.0, "current_peer_median": 3.4}),
    (dict(metric_key="earnings_yield", numeric_value=5.0, peer_median=3.4, score=None),
     [dict(metric_key="pe", score=1)],
     {"current_value": 5.0, "current_peer_median": 3.4, "current_score": 1}),
    # Earnings Yield with no median: no score even when the P/E row has one.
    (dict(metric_key="earnings_yield", numeric_value=5.0, peer_median=None, score=None),
     [dict(metric_key="pe", score=5)], {"current_value": 5.0}),
    (dict(metric_key="pe", numeric_value=float("-inf"), peer_median=float("nan"), score=2),
     [], {}),
    (dict(metric_key="pe", numeric_value=None, peer_median=None, score=2), [], {}),
])
def test_current_comparison_emits_only_finite_numbers_and_scores(row, others, expected):
    m = SimpleNamespace(**row)
    rows = [m, *(SimpleNamespace(**o) for o in others)]
    got = C._current_comparison(m, rows)
    assert got == expected
    for v in got.values():
        assert not isinstance(v, bool) and math.isfinite(v)


def test_the_deep_dive_metric_decodes_without_the_new_fields():
    m = DeepDiveMetricResponse.model_validate({"label": "P/E", "value": "20"})
    assert (m.current_value, m.current_peer_median, m.current_score, m.current_period) == (
        None, None, None, None)
    assert set(_CURRENT_KEYS) <= set(m.model_dump())


@pytest.mark.asyncio
async def test_orcl_end_to_end_the_line_ends_at_fy_minus_one_and_the_legend_reads_0_62(
    monkeypatch,
):
    """The reported case, end to end: the closed FY(current) bar keeps its own value, its
    peer point is gone (no more 41.63 / 29.32 = "1.42x vs industry"), and the legend's
    second line carries the card's own TTM comparison, 18.2134 vs 29.3172 = 0.62x, scored 5."""
    y = _cur_year()
    hist = await _history(monkeypatch, _table(y))
    rows = _annual_rows(y, [(-1, "05-31", -1), (0, "05-31", 0)])
    history = C._build_fundamentals_history(_collected(rows, hist, ticker="ORCL"))
    snap = _card(fr={"priceToEarningsRatioTTM": _ORCL_PE_TTM},
                 bench={"pe_ratio": _TTM_PE_MEDIAN}, levels={"pe_ratio": "industry"},
                 ticker="ORCL")
    card = C._snapshot_to_card("Valuation", snap, history_lookup=history,
                               peer_group_level="industry")
    by = {md.get("history_key"): md for md in card["metrics"]}

    pe = by["pe"]
    assert pe["annual_history"][-1] == {"period": str(y), "value": _CLOSED_YEAR_PE}
    assert pe["sector_annual_history"][-1] == {"period": str(y), "value": None}
    assert pe["sector_annual_history"][-2] == {"period": str(y - 1), "value": 26.0}
    assert pe["sector_annual_level"] == "industry"
    assert _current(pe) == {"current_value": _ORCL_PE_TTM,
                            "current_peer_median": _TTM_PE_MEDIAN,
                            "current_score": _m(snap, "pe").score}
    assert f"{pe['current_value'] / pe['current_peer_median']:.2f}" == "0.62"

    ey = by["earnings_yield"]
    assert ey["sector_annual_history"][-1]["value"] is None
    assert ey["current_score"] == _m(snap, "pe").score
    assert ey["current_value"] == pytest.approx(100 / 18.21, rel=1e-12)
    assert ey["current_peer_median"] == pytest.approx(100 / _TTM_PE_MEDIAN, rel=1e-12)

    dump = _validated(card)
    dumped = {md["history_key"]: md for md in dump["metrics"] if md["history_key"]}
    assert dumped["pe"]["current_peer_median"] == _TTM_PE_MEDIAN
    assert dumped["earnings_yield"]["current_score"] == 5


# ═══════════════════════════════════════════════════════════════════════════
# E. The numbers survive the caches; a legacy payload decodes with None
# ═══════════════════════════════════════════════════════════════════════════


def _numbered_snapshots() -> Tuple[SnapshotItemResponse, SnapshotItemResponse]:
    valuation = _card(fr={"priceToEarningsRatioTTM": _ORCL_PE_TTM},
                      bench={"pe_ratio": _TTM_PE_MEDIAN}, levels={"pe_ratio": "industry"},
                      ticker="ORCL")
    health = SnapshotItemResponse(
        category="Financial Health", rating=4, full_report_available=True, metrics=[
            _row(name="Debt-to-Equity (vs sector 0.60)", value="0.40",
                 metric_key="debt_to_equity", score=4, peer_level="industry",
                 numeric_value=0.4, peer_median=0.6),
            _row(name="Altman Z-Score", value="4.20", metric_key="altman_z", score=4,
                 numeric_value=4.2),
        ])
    return valuation, health


def test_the_collection_cache_round_trips_the_row_numbers():
    out = _sample()
    out.snap_valuation, out.snap_health = _numbered_snapshots()
    blob = _serialize(out)
    assert blob is not None
    back = _deserialize(json.loads(json.dumps(blob, allow_nan=False)), _field_names())
    assert back is not None
    for name in ("snap_valuation", "snap_health"):
        before = [(m.numeric_value, m.peer_median) for m in getattr(out, name).metrics]
        after = [(m.numeric_value, m.peer_median) for m in getattr(back, name).metrics]
        assert after == before, name
    assert _m(back.snap_valuation, "pe").peer_median == _TTM_PE_MEDIAN
    assert _m(back.snap_valuation, "pe").numeric_value == _ORCL_PE_TTM


def test_a_legacy_collection_without_the_numbers_decodes_with_none():
    out = _sample()
    out.snap_valuation, out.snap_health = _numbered_snapshots()
    blob = json.loads(json.dumps(_serialize(out)))
    for name in ("snap_valuation", "snap_health"):
        for row in blob[name]["metrics"]:
            row.pop("numeric_value")
            row.pop("peer_median")
    back = _deserialize(blob, _field_names())
    assert back is not None
    for name in ("snap_valuation", "snap_health"):
        for m in getattr(back, name).metrics:
            assert (m.numeric_value, m.peer_median) == (None, None), m
    card = C._snapshot_to_card("Valuation", back.snap_valuation,
                               history_lookup=_history_lookup("pe", "earnings_yield"))
    assert all(_current(md) == {} for md in card["metrics"])


@pytest.mark.parametrize("which", ["valuation", "health"])
def test_the_snapshot_cache_round_trips_the_row_numbers_and_rebuilds_an_older_version(which):
    valuation, health = _numbered_snapshots()
    mod, snap = (vss, valuation) if which == "valuation" else (hs, health)
    cls = vss.ValuationSnapshotService if which == "valuation" else hs.HealthSnapshotService
    svc = cls.__new__(cls)
    svc.supabase = _SnapshotCacheTable()
    svc._upsert_supabase_cache("ORCL", snap)
    (written,) = svc.supabase.upserts
    stored = json.loads(json.dumps(written["response_json"], allow_nan=False))
    assert stored[mod._VERSION_KEY] == mod._SNAPSHOT_PAYLOAD_VERSION
    if which == "valuation":
        assert stored[vss._DCF_SOURCE_KEY] == bool(settings.DCF_ENABLED)

    svc.supabase = _SnapshotCacheTable([
        {"response_json": dict(stored), "cached_at": written["cached_at"]}])
    got = svc._check_supabase_cache("ORCL")
    assert got is not None
    assert [(m.numeric_value, m.peer_median) for m in got.metrics] == [
        (m.numeric_value, m.peer_median) for m in snap.metrics]

    # A row from the build before the numbers existed is rebuilt, never served.
    older = {**stored, mod._VERSION_KEY: mod._SNAPSHOT_PAYLOAD_VERSION - 1}
    svc.supabase = _SnapshotCacheTable([
        {"response_json": older, "cached_at": written["cached_at"]}])
    assert svc._check_supabase_cache("ORCL") is None


# ═══════════════════════════════════════════════════════════════════════════
# Review round (2026-11-13): the zero-ratio yield and a Health card end to end
# ═══════════════════════════════════════════════════════════════════════════

def test_a_yield_whose_ratio_rounds_to_zero_still_carries_its_printed_value():
    """P/E 5000 against a 20.0 median: the label prints "0.02%" with no "0.00x", and the
    drill-down line must still show 0.02% against 5.00%, coloured by the P/E row's score (1,
    expensive = low yield) — it read "N/A vs industry", muted, before the review."""
    snap = vss.build_price_snapshot(
        fr={"priceToEarningsRatioTTM": 5000.0}, km={}, cf={}, inc={}, bs={}, profile={},
        bench={"pe_ratio": 20.0}, bench_levels={"pe_ratio": "industry"}, ticker="HUGE")
    ey = _m(snap, "earnings_yield")
    assert ey.value == "0.02%" and "x sector avg" not in ey.name
    assert ey.numeric_value == pytest.approx(0.02) and ey.peer_median == pytest.approx(5.0)
    card = C._snapshot_to_card("Valuation", snap, history_lookup=_history_lookup(
        "pe", "earnings_yield"))
    rows = {m["label"].split(" (")[0]: m for m in _validated(card)["metrics"]}
    assert rows["Earnings Yield"]["current_value"] == pytest.approx(0.02)
    assert rows["Earnings Yield"]["current_score"] == rows["P/E"]["current_score"] == 1


def test_a_health_card_row_carries_its_own_comparison_and_altman_z_none():
    """The Health card through `_snapshot_to_card`: a D/E row judged worse than its peers
    (score 2) carries the trio; Altman Z (absolute bands, no peer) carries its value and its
    own score but no median, so the sheet draws its zone line and no peer comparison
    (2026-11-13: the score rides with any number, not only with a median). Neither is a
    closed period: no `current_period`."""
    snap = SnapshotItemResponse(category="Financial Health", rating=2, full_report_available=True,
                                metrics=[
        _row(name="Debt-to-Equity (vs sector 0.90)", value="1.52", metric_key="debt_to_equity",
             score=2, peer_level="industry", numeric_value=1.52, peer_median=0.9),
        _row(name="Altman Z-Score", value="3.25", metric_key="altman_z", score=4,
             numeric_value=3.25),
    ])
    lookup = {"debt_to_equity": dict(_HISTORY_POINT),
              "altman_z": {"unit": "score", "annual": [{"period": "2025", "value": 3.1}],
                           "quarterly": []}}
    card = _validated(C._snapshot_to_card("Health", snap, history_lookup=lookup))
    by = {m["label"].split(" (")[0]: m for m in card["metrics"]}
    de = {k: v for k, v in _current(by["Debt-to-Equity"]).items() if v is not None}
    assert de == {"current_value": 1.52, "current_peer_median": 0.9, "current_score": 2}
    assert by["Debt-to-Equity"]["peer_level"] == "industry"
    altman = {k: v for k, v in _current(by["Altman Z-Score"]).items() if v is not None}
    assert altman == {"current_value": 3.25, "current_score": 4}
    assert all(m["current_period"] is None for m in card["metrics"])


# ═══════════════════════════════════════════════════════════════════════════
# F. The Growth card (owner decisions 2026-11-13: "Name the periods", "Say not out yet",
#    "Keep the exact figure"): each valued row names its fiscal year and carries that
#    year's own peer YoY; the drill-down's second line never says "Current" on Growth.
# ═══════════════════════════════════════════════════════════════════════════

_GROWTH_KEYS = ("revenue_growth", "eps_growth", "fcf_growth", "operating_income_growth")
_GROWTH_NUMBERS = ("numeric_value", "peer_median", "period_label")


def _gpt(period: str, yoy: Optional[float], peer: Optional[float] = None) -> GrowthDataPointSchema:
    return GrowthDataPointSchema(period=period, value=10.0, yoy_change_percent=yoy,
                                 sector_average_yoy=peer)


class _GrowthStub:
    def __init__(self, response: GrowthResponse):
        self.response = response

    async def get_growth_with_status(self, ticker):
        return self.response, []


def _growth_resp(*, rev=(), eps=(), fcf=(), op=(),
                 industry: Optional[str] = "Software - Infrastructure") -> GrowthResponse:
    return GrowthResponse(symbol="ORCL", industry=industry,
                          revenue_annual=list(rev), revenue_quarterly=[],
                          eps_annual=list(eps), eps_quarterly=[],
                          free_cash_flow_annual=list(fcf), operating_profit_annual=list(op))


async def _growth_snapshot(monkeypatch, response: GrowthResponse, ticker: str = "ORCL"):
    # growth_snapshot_service imports `get_growth_service` inside the function, so the
    # source module's binding is the one it resolves on every call.
    monkeypatch.setattr(growth_mod, "get_growth_service", lambda: _GrowthStub(response))
    svc = gss.GrowthSnapshotService.__new__(gss.GrowthSnapshotService)
    svc.supabase = None
    return await svc._compute_with_status(ticker)


def _growth_lookup() -> Dict[str, Dict[str, Any]]:
    return {k: {"unit": "percent", "annual": [{"period": "2026", "value": 1.0}],
                "quarterly": []} for k in _GROWTH_KEYS}


def _growth_card(snap: SnapshotItemResponse) -> Dict[str, Any]:
    return C._snapshot_to_card("Growth", snap, history_lookup=_growth_lookup(),
                               peer_group_level="industry")


def _by_history_key(card: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {md["history_key"]: md for md in card["metrics"] if md.get("history_key")}


def _orcl_growth() -> GrowthResponse:
    """ORCL's shape on 2026-11-13: FY2026 closed; its revenue year has no industry YoY yet,
    the older year has one (never borrowed); EPS has a same-year median; FCF went -$0.39B ->
    -$23.7B (a deepening negative base, -5911.7%, kept exactly); operating income well ahead."""
    return _growth_resp(
        rev=[_gpt("2025", 8.4, 9.1), _gpt("2026", 34.33, None)],
        eps=[_gpt("2025", 4.0, 6.0), _gpt("2026", 21.85, 27.50)],
        fcf=[_gpt("2025", -110.0, 3.0), _gpt("2026", -5911.72, 4.0)],
        op=[_gpt("2025", 12.0, 8.0), _gpt("2026", 50.0, 10.0)],
    )


@pytest.mark.asyncio
async def test_orcl_growth_rows_name_their_fiscal_year_and_carry_that_years_median(monkeypatch):
    snap, degraded = await _growth_snapshot(monkeypatch, _orcl_growth())
    assert degraded == []
    rev, eps, fcf, op = (_m(snap, k) for k in _GROWTH_KEYS)

    # Revenue: FY2026 34.33%, no industry figure for FY2026 yet ("not out yet") — and the
    # older year's 9.1 is never borrowed. Scored on the absolute bands: > 20% -> 5.
    assert (rev.value, rev.numeric_value, rev.peer_median, rev.period_label, rev.score) == (
        "+34.3%", 34.33, None, "FY2026", 5)
    # EPS: the same year's median. 21.85 vs 27.50 = 5.65 pp below (2), floored at 3 (>= 20%).
    assert (eps.numeric_value, eps.peer_median, eps.period_label, eps.score) == (
        21.85, 27.50, "FY2026", 3)
    # FCF: the exact -5911.72 (unrounded), against its own year's 4.0, scored 1.
    assert (fcf.value, fcf.numeric_value, fcf.peer_median, fcf.period_label, fcf.score) == (
        "-5911.7%", -5911.72, 4.0, "FY2026", 1)
    assert (op.numeric_value, op.peer_median, op.score) == (50.0, 10.0, 5)
    # The rows print no median in their names (Growth never did), and no peer_level.
    assert all("avg" not in m.name and m.peer_level is None for m in snap.metrics)
    # The rating is untouched by the new fields: (5*.3 + 3*.3 + 1*.2 + 5*.2) / 1.0 = 3.6.
    assert (snap.weighted_score, snap.rating) == (3.6, 4)

    card = _growth_card(snap)
    by = _by_history_key(card)
    assert set(by) == set(_GROWTH_KEYS)
    assert _current(by["revenue_growth"]) == {
        "current_value": 34.33, "current_score": 5, "current_period": "FY2026"}
    assert _current(by["eps_growth"]) == {
        "current_value": 21.85, "current_peer_median": 27.50, "current_score": 3,
        "current_period": "FY2026"}
    assert f"{21.85 / 27.50:.2f}" == "0.79"            # the legend's "0.79× vs industry"
    assert _current(by["fcf_growth"]) == {
        "current_value": -5911.72, "current_peer_median": 4.0, "current_score": 1,
        "current_period": "FY2026"}

    dumped = {md["history_key"]: md for md in _validated(card)["metrics"]}
    assert dumped["revenue_growth"]["current_peer_median"] is None
    assert dumped["revenue_growth"]["current_period"] == "FY2026"
    assert dumped["revenue_growth"]["current_score"] == 5
    for key in _GROWTH_KEYS:
        for field in _CURRENT_KEYS:
            assert dumped[key][field] == by[key].get(field), (key, field)
    # The strict-JSON round trip iOS decodes keeps every field (and its snake_case key).
    blob = json.dumps(DeepDiveMetricCardResponse.model_validate(card).model_dump(mode="json"),
                      allow_nan=False)
    assert '"current_period": "FY2026"' in blob
    back = DeepDiveMetricCardResponse.model_validate(json.loads(blob))
    assert {m.history_key: m.current_period for m in back.metrics} == {
        k: "FY2026" for k in _GROWTH_KEYS}


@pytest.mark.asyncio
async def test_a_growth_row_with_its_years_median_carries_all_four(monkeypatch):
    snap, _ = await _growth_snapshot(monkeypatch, _growth_resp(
        rev=[_gpt("2026", 34.33, 27.50)]))
    rev = _m(snap, "revenue_growth")
    assert rev.score == 4      # 6.83 pp above -> 4; the absolute floor at 20% is 3
    md = _by_history_key(_growth_card(snap))["revenue_growth"]
    assert _current(md) == {"current_value": 34.33, "current_peer_median": 27.50,
                            "current_score": 4, "current_period": "FY2026"}


@pytest.mark.asyncio
async def test_the_nm_fcf_row_and_the_dash_rows_carry_nothing(monkeypatch):
    """A bank's FCF growth is a fixed "N/M" (`score_excluded_rows`): no number, median or
    year even though its series has one; a newest year with an n/m YoY prints "—" and
    carries nothing, though that year has a median and the year before has a value."""
    snap, degraded = await _growth_snapshot(monkeypatch, _growth_resp(
        industry="Banks - Diversified",
        rev=[_gpt("2025", 13.4, 9.0), _gpt("2026", None, 8.0)],
        eps=[],
        fcf=[_gpt("2026", 12.0, 5.0)],
        op=[_gpt("2026", 7.5, 6.0)],
    ), ticker="JPM")
    assert degraded == []
    by = {m.metric_key: m for m in snap.metrics}
    assert by["fcf_growth"].value == "N/M"
    for key in ("fcf_growth", "revenue_growth", "eps_growth"):
        assert by[key].score is None, key
        assert all(getattr(by[key], f) is None for f in _GROWTH_NUMBERS), by[key]
    assert by["revenue_growth"].value == by["eps_growth"].value == "—"
    op = by["operating_income_growth"]
    assert (op.numeric_value, op.peer_median, op.period_label) == (7.5, 6.0, "FY2026")

    card = _growth_card(snap)
    rows = {md["label"]: md for md in card["metrics"]}
    nm = rows["Free Cash Flow Growth (YoY)"]
    assert "history_key" not in nm and _current(nm) == {}, "an N/M row is never charted"
    for label in ("Revenue Growth (YoY)", "EPS Growth"):
        assert _current(rows[label]) == {}, label
    assert _current(rows["Operating Income Growth"])["current_period"] == "FY2026"
    _validated(card)


@pytest.mark.asyncio
@pytest.mark.parametrize("yoy, peer, value, numeric, median, score", [
    # A non-finite YoY is no value: "—", nothing carried, unscored (it printed "nan%").
    (float("nan"), 5.0, "—", None, None, None),
    (float("inf"), 5.0, "—", None, None, None),
    (float("-inf"), None, "—", None, None, None),
    # A non-finite median is no median: absolute bands (12% -> 4), never the 1/5 that every
    # NaN / -inf comparison fell to.
    (12.0, float("nan"), "+12.0%", 12.0, None, 4),
    (12.0, float("inf"), "+12.0%", 12.0, None, 4),
    (12.0, float("-inf"), "+12.0%", 12.0, None, 4),
    # Boundaries: a zero YoY, a zero median, a huge magnitude, a tiny negative.
    (0.0, 0.0, "+0.0%", 0.0, 0.0, 3),
    (-0.0, -0.0, "+0.0%", 0.0, 0.0, 3),       # a negative zero printed "+-0.0%"
    (1e9, 2.0, "+1000000000.0%", 1e9, 2.0, 5),
    (-0.04, None, "-0.0%", -0.04, None, 2),
])
async def test_growth_rows_never_carry_a_non_finite_number(
    monkeypatch, yoy, peer, value, numeric, median, score,
):
    snap, _ = await _growth_snapshot(monkeypatch, _growth_resp(rev=[_gpt("2026", yoy, peer)]))
    rev = _m(snap, "revenue_growth")
    assert (rev.value, rev.numeric_value, rev.peer_median, rev.score) == (
        value, numeric, median, score)
    assert rev.period_label == ("FY2026" if numeric is not None else None)
    assert "-0.0" not in (repr(rev.numeric_value), repr(rev.peer_median)), "a negative zero"
    json.dumps(snap.model_dump(), allow_nan=False)
    card = _growth_card(snap)
    _validated(card)
    md = _by_history_key(card)["revenue_growth"]
    if numeric is None:
        assert _current(md) == {}
    else:
        assert md["current_value"] == numeric and md["current_score"] == score
        assert md.get("current_peer_median") == median


@pytest.mark.parametrize("period, label", [
    ("2026", "FY2026"), (" 2026 ", "FY2026"), ("1999", "FY1999"),
    ("", None), ("   ", None), (None, None), (2026, None), ("FY2026", None),
    ("Q1'26", None), ("Q4 '26", None), ("20266", None), ("226", None),
    ("２０２６", None),                 # fullwidth digits are not a fiscal year label
])
def test_the_fiscal_year_label_names_only_a_four_digit_year(period, label):
    assert gss._fiscal_year_label(period) == label


@pytest.mark.parametrize("raw, finite", [
    (None, None), (True, None), (False, None), ("12", 12.0), ("x", None), (float("nan"), None),
    (float("inf"), None), (-5911.72, -5911.72), (0, 0.0),
])
def test_the_growth_finite_guard(raw, finite):
    assert gss._finite(raw) == finite
    assert repr(gss._finite(-0.0)) == "0.0"


@pytest.mark.asyncio
async def test_a_valued_row_whose_period_is_not_a_year_keeps_its_number_and_names_no_year(
    monkeypatch, caplog,
):
    caplog.set_level("WARNING", logger=gss.logger.name)
    snap, degraded = await _growth_snapshot(monkeypatch, _growth_resp(
        rev=[_gpt("FY2026", 34.33, 27.5)]))
    rev = _m(snap, "revenue_growth")
    assert (rev.numeric_value, rev.peer_median, rev.period_label) == (34.33, 27.5, None)
    assert degraded == []
    assert "[growth-snapshot-period-label]" in caplog.text and "ORCL" in caplog.text
    md = _by_history_key(_growth_card(snap))["revenue_growth"]
    assert "current_period" not in md and md["current_value"] == 34.33


@pytest.mark.parametrize("row, expected", [
    # The score is an int, never a bool, and rides with any number.
    (dict(metric_key="revenue_growth", numeric_value=34.33, peer_median=None, score=True,
          period_label="FY2026"),
     {"current_value": 34.33, "current_period": "FY2026"}),
    (dict(metric_key="revenue_growth", numeric_value=34.33, peer_median=None, score=5,
          period_label="FY2026"),
     {"current_value": 34.33, "current_score": 5, "current_period": "FY2026"}),
    # A named period travels on its own (review round 2026-11-13: a Price row on a
    # last-fiscal-year basis names its year with no value or median, so the sheet never calls
    # it TTM); the score still rides only with a number.
    (dict(metric_key="revenue_growth", numeric_value=None, peer_median=27.5, score=3,
          period_label="FY2026"),
     {"current_peer_median": 27.5, "current_score": 3, "current_period": "FY2026"}),
    (dict(metric_key="revenue_growth", numeric_value=float("nan"), peer_median=None, score=5,
          period_label="FY2026"), {"current_period": "FY2026"}),
    (dict(metric_key="revenue_growth", numeric_value=True, peer_median=None, score=5,
          period_label="FY2026"), {"current_period": "FY2026"}),
    # Junk labels are no label; a padded one is trimmed.
    (dict(metric_key="eps_growth", numeric_value=1.0, peer_median=None, score=3,
          period_label="   "), {"current_value": 1.0, "current_score": 3}),
    (dict(metric_key="eps_growth", numeric_value=1.0, peer_median=None, score=3,
          period_label=2026), {"current_value": 1.0, "current_score": 3}),
    (dict(metric_key="eps_growth", numeric_value=1.0, peer_median=None, score=3,
          period_label=" FY2026 "),
     {"current_value": 1.0, "current_score": 3, "current_period": "FY2026"}),
    # Nothing to compare -> no number and no score; a named period still travels.
    (dict(metric_key="eps_growth", numeric_value=None, peer_median=None, score=4,
          period_label="FY2026"), {"current_period": "FY2026"}),
    (dict(metric_key="eps_growth", numeric_value=None, peer_median=None, score=4,
          period_label=None), {}),
    # A legacy row with no such attributes at all.
    (dict(metric_key="eps_growth", score=4), {}),
])
def test_current_comparison_growth_rules(row, expected):
    m = SimpleNamespace(**row)
    got = C._current_comparison(m, [m])
    assert got == expected
    if "current_score" in got:
        assert type(got["current_score"]) is int


@pytest.mark.parametrize("ey_score, pe_score, median, expected_score", [
    # Earnings Yield is unchanged: the P/E row's score, and only beside a median — never
    # its own score under the "score rides with any number" rule.
    (4, 5, None, None),
    (None, 5, None, None),
    (4, 2, 3.4, 2),
    (4, None, 3.4, None),
    (4, True, 3.4, None),
])
def test_earnings_yield_keeps_the_pe_score_rule(ey_score, pe_score, median, expected_score):
    ey = SimpleNamespace(metric_key="earnings_yield", numeric_value=5.0, peer_median=median,
                         score=ey_score, period_label=None)
    pe = SimpleNamespace(metric_key="pe", score=pe_score)
    got = C._current_comparison(ey, [pe, ey])
    assert got.get("current_score") == expected_score
    assert got["current_value"] == 5.0 and "current_period" not in got


def test_legacy_and_in_between_growth_rows():
    """A Growth row cached before the numbers existed draws the period line only; a row with
    a value but no label (a build that never named the year) still carries its value and
    score, and names no year — iOS never invents one."""
    legacy = SnapshotMetricResponse.model_validate(
        {"name": "Revenue Growth (YoY)", "value": "+34.3%", "metric_key": "revenue_growth",
         "score": 5})
    assert all(getattr(legacy, f) is None for f in _GROWTH_NUMBERS)
    unnamed = SnapshotMetricResponse(name="EPS Growth", value="+21.9%", metric_key="eps_growth",
                                     score=3, numeric_value=21.85, peer_median=27.5)
    snap = SnapshotItemResponse(category="Growth", rating=4, metrics=[legacy, unnamed])
    by = _by_history_key(_growth_card(snap))
    assert _current(by["revenue_growth"]) == {}
    assert _current(by["eps_growth"]) == {
        "current_value": 21.85, "current_peer_median": 27.5, "current_score": 3}
    _validated(_growth_card(snap))


@pytest.mark.parametrize("which", ["valuation", "health"])
def test_valuation_and_health_rows_never_name_a_closed_period(which):
    """Their rows are TTM / current: no `period_label`, so the sheet keeps "Current (TTM)"."""
    valuation, health = _numbered_snapshots()
    snap = valuation if which == "valuation" else health
    assert all(m.period_label is None for m in snap.metrics)
    title = "Valuation" if which == "valuation" else "Health"
    lookup = {k: dict(_HISTORY_POINT) for k in (
        "pe", "pb", "ps", "pfcf", "ev_ebitda", "earnings_yield", "debt_to_equity", "altman_z")}
    card = _validated(C._snapshot_to_card(title, snap, history_lookup=lookup))
    assert all(m["current_period"] is None for m in card["metrics"])
    assert any(m["current_value"] is not None for m in card["metrics"]), "not vacuous"


def test_a_price_snapshot_built_by_the_service_names_no_period():
    snap = vss.build_price_snapshot(
        fr={"priceToEarningsRatioTTM": _ORCL_PE_TTM}, km={}, cf={}, inc={}, bs={}, profile={},
        bench={"pe_ratio": _TTM_PE_MEDIAN}, bench_levels={"pe_ratio": "industry"},
        ticker="ORCL")
    assert all(m.period_label is None for m in snap.metrics)


@pytest.mark.asyncio
async def test_the_growth_numbers_survive_both_caches(monkeypatch):
    snap, _ = await _growth_snapshot(monkeypatch, _orcl_growth())
    expected = [tuple(getattr(m, f) for f in _GROWTH_NUMBERS) for m in snap.metrics]
    assert ("FY2026" in {e[2] for e in expected}) and (None in {e[1] for e in expected})

    # snapshot_cache (whatever the payload version is: the main session bumps it).
    svc = gss.GrowthSnapshotService.__new__(gss.GrowthSnapshotService)
    svc.supabase = _SnapshotCacheTable()
    svc._upsert_supabase_cache("ORCL", snap)
    (written,) = svc.supabase.upserts
    stored = json.loads(json.dumps(written["response_json"], allow_nan=False))
    assert stored[gss._VERSION_KEY] == gss._SNAPSHOT_PAYLOAD_VERSION
    svc.supabase = _SnapshotCacheTable([
        {"response_json": dict(stored), "cached_at": written["cached_at"]}])
    got = svc._check_supabase_cache("ORCL")
    assert got is not None
    assert [tuple(getattr(m, f) for f in _GROWTH_NUMBERS) for m in got.metrics] == expected
    older = {**stored, gss._VERSION_KEY: gss._SNAPSHOT_PAYLOAD_VERSION - 1}
    svc.supabase = _SnapshotCacheTable([
        {"response_json": older, "cached_at": written["cached_at"]}])
    assert svc._check_supabase_cache("ORCL") is None

    # The report's collection cache.
    out = _sample()
    out.snap_growth = snap
    back = _deserialize(json.loads(json.dumps(_serialize(out), allow_nan=False)), _field_names())
    assert back is not None
    assert [tuple(getattr(m, f) for f in _GROWTH_NUMBERS) for m in back.snap_growth.metrics] == \
        expected

    # A legacy collection (no such keys) decodes with None and draws the period line only.
    blob = json.loads(json.dumps(_serialize(out)))
    for row in blob["snap_growth"]["metrics"]:
        for f in _GROWTH_NUMBERS:
            row.pop(f)
    legacy = _deserialize(blob, _field_names())
    assert legacy is not None
    assert all(_current(md) == {} for md in _growth_card(legacy.snap_growth)["metrics"])


@pytest.mark.asyncio
async def test_the_growth_card_through_the_report_builder(monkeypatch):
    """The production entry point (`_build_fundamental_metrics_from_snapshots`) with the
    Growth snapshot: the card validates and its rows carry the line's numbers."""
    snap, _ = await _growth_snapshot(monkeypatch, _orcl_growth())
    cards = C._build_fundamental_metrics_from_snapshots(
        None, snap, None, None, history_lookup=_growth_lookup())
    growth = next(c for c in cards if c["title"] == "Growth")
    dumped = _validated(growth)
    periods = {m["history_key"]: m["current_period"] for m in dumped["metrics"]}
    assert periods == {k: "FY2026" for k in _GROWTH_KEYS}


def test_compute_yoy_keeps_orcls_exact_figure_and_is_null_only_without_a_base():
    """The docstring's example, pinned: -$0.39B -> -$23.7B (a deepening negative base) reads
    -5911.7%, shown verbatim; None only for a missing value or a zero base."""
    got = growth_mod._compute_yoy(-23.70e9, -0.39423e9)
    assert got == pytest.approx(-5911.72, abs=0.01)
    assert growth_mod._compute_yoy(0.4e9, -0.4e9) == 200.0      # loss -> profit reads up
    assert growth_mod._compute_yoy(-0.4e9, 0.4e9) == -200.0     # profit -> loss reads down
    for cur, prev in ((None, 1.0), (1.0, None), (1.0, 0.0), (0.0, 0.0), (None, None)):
        assert growth_mod._compute_yoy(cur, prev) is None, (cur, prev)
    assert growth_mod._compute_yoy(1e12, 1e-3) is not None, "a huge magnitude is kept"
