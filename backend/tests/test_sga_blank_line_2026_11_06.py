"""SG&A: FMP's 0 is a blank, and it means nothing for insurers and utilities, 2026-11-06.

FMP writes 0 for a company whose statements do not use the line. The scorer always read that as
no reading while the median counted it as a real 0%, so six blanks took the Insurance -
Diversified median from 7.78% to 4.45% and marked Berkshire down against it. Owner decision,
2026-11-06: "Option 2, R&D as is". Pinned here:

  * the median and the scorer agree on what a reading is, for zero, null, negative and junk;
  * R&D keeps its zero on both sides, on purpose;
  * SG&A does not count for an insurer or a utility, whatever median is stored, so a median
    rebuilt without blanks cannot switch the driver on for them;
  * insurers and utilities leave their sector pools for this metric, so a recompute reaching
    production before this gate cannot switch the driver on for them;
  * Berkshire's figures against a real rebuilt cell.
"""

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.services import financials_metric_gate as gate
from app.services import industry_benchmark_service as ibs
from app.services import moat_scoring_service as M
from app.services import sector_benchmark_service as sbs
from app.utils.margins import sga_reading

_DATA = Path(__file__).resolve().parent.parent / "data"
SGA = "sellingGeneralAndAdministrativeExpenses"
RD = "researchAndDevelopmentExpenses"
INSURERS = (
    "Insurance - Brokers", "Insurance - Diversified", "Insurance - Life",
    "Insurance - Property & Casualty", "Insurance - Reinsurance", "Insurance - Specialty",
)
UTILITIES = (
    "Diversified Utilities", "Independent Power Producers", "Regulated Electric", "Regulated Gas",
    "Regulated Water", "Renewable Utilities",
)
MEDIANS = {"operating_margin": 0.20, "asset_turnover": 0.40, "sga_to_revenue": 10.0}


def _company(numerator_field: str, value: Any, *, revenue: Any = 1000.0) -> Dict[str, List]:
    row = {"date": "2025-12-31", "fiscalYear": "2025", "period": "FY", "revenue": revenue}
    if value != "absent":
        row[numerator_field] = value
    return {"income_annual": [row]}


def _bucket(metric: str, field: str, value: Any, *, revenue: Any = 1000.0) -> List[float]:
    out = sbs._compute_ratio_values([_company(field, value, revenue=revenue)], metric, "annual")
    return [v for values in out.values() for v in values]


class _Lookup:
    """Answers every metric for any sector or industry, at n=50, year 2025."""

    def __init__(self, medians: Dict[str, float]) -> None:
        self.medians = medians

    def _answer(self, metrics: List[str]) -> Dict[str, Any]:
        return {
            m: ({"2025": {"median": self.medians[m], "n": 50}} if m in self.medians else {})
            for m in metrics
        }

    def get_sector_benchmarks_with_n(self, sector, metrics, period_type):
        return self._answer(metrics)

    def get_industry_benchmarks_with_n(self, industry, metrics, period_type):
        return self._answer(metrics)


def _service(medians: Optional[Dict[str, float]] = None) -> M.MoatScoringService:
    svc = M.MoatScoringService.__new__(M.MoatScoringService)
    svc._lookup = _Lookup(MEDIANS if medians is None else medians)
    return svc


def _cost(
    sector: Any, industry: Any, *, sga: Any = 50.0, operating: Any = 0.20, turnover: Any = 0.40,
    medians: Optional[Dict[str, float]] = None, ticker: str = "TEST",
) -> M.PillarResult:
    income = [
        {"date": "2025-12-31", "revenue": 1000.0, SGA: sga, RD: 0.0},
        {"date": "2024-12-31", "revenue": 900.0},
    ]
    ratios = [{
        "date": "2025-12-31", "operatingProfitMargin": operating, "assetTurnover": turnover,
        "revenuePerShare": 10.0,
    }]
    scores = _service(medians).score(
        sector=sector, industry=industry, profile={"symbol": ticker}, income=income,
        balance=[{"date": "2025-12-31", "totalAssets": 2500.0}], ratios=ratios, ticker=ticker,
    )
    return scores[M.PILLAR_COST]


def _sga_driver(pillar: M.PillarResult) -> M.MetricDriver:
    return next(d for d in pillar.drivers if d.metric == "sga_to_revenue")


# ── the shared reading ──────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (0, None), (0.0, None), (-0.0, None),       # the blank
    (None, None),                               # the field is missing
    (-5.0, None), (-1e-9, None),                # Allstate's FY2025 SG&A is a small negative
    (math.nan, None), (math.inf, None), (-math.inf, None),
    (7.78, 7.78), (1e-9, 1e-9), (35071.7, 35071.7),   # extreme but positive stays a figure
])
def test_the_shared_reading(value, expected):
    assert sga_reading(value) == expected


# ── the median ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [0, 0.0, -0.0, None, "absent", -5.0, math.nan, math.inf, "n/a"])
def test_the_median_leaves_out_what_is_not_a_figure(value):
    assert _bucket("sga_to_revenue", SGA, value) == []


def test_a_real_sga_is_bucketed_as_a_percentage():
    assert _bucket("sga_to_revenue", SGA, 77.8) == [pytest.approx(7.78)]


@pytest.mark.parametrize("revenue", [0, -100.0, None, math.nan])
def test_no_revenue_gives_no_sga_ratio(revenue):
    assert _bucket("sga_to_revenue", SGA, 77.8, revenue=revenue) == []


def test_rd_keeps_its_zero():
    """A company can have no R&D, and the scorer reads its 0 as a figure too. Owner, 2026-11-06:
    "R&D as is". Mutation: applying the SG&A rule to both metrics fails this."""
    assert _bucket("rd_to_revenue", RD, 0.0) == [0.0]
    assert _bucket("rd_to_revenue", RD, 150.0) == [pytest.approx(15.0)]


@pytest.mark.parametrize("value", [None, "absent", -5.0, math.nan])
def test_rd_still_drops_what_it_always_dropped(value):
    assert _bucket("rd_to_revenue", RD, value) == []


def test_a_group_of_blanks_has_no_bucket_at_all():
    companies = [_company(SGA, 0.0) for _ in range(12)]
    assert sbs._compute_ratio_values(companies, "sga_to_revenue", "annual") == {}


def test_berkshires_cell_rebuilt_without_its_blanks():
    """FY2025 Insurance - Diversified, as FMP filed it on 2026-11-06: 7 figures and 6 blanks."""
    figures = [4.4, 7.2, 7.3, 7.7791, 10.7, 18.9, 25.9]
    companies = [_company(SGA, pct * 10) for pct in figures]
    companies += [_company(SGA, 0.0) for _ in range(6)]
    values = sbs._compute_ratio_values(companies, "sga_to_revenue", "annual")["2025"]
    assert len(values) == 7
    assert statistics.median(values) == pytest.approx(7.7791)
    assert statistics.median(values + [0.0] * 6) == pytest.approx(4.4)  # what production stored


# ── the scorer ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [0, 0.0, -0.0, None, "absent", -5.0, math.nan, math.inf, "n/a"])
def test_the_scorer_reads_no_figure_either(value):
    svc = _service()
    income = {"revenue": 1000.0}
    if value != "absent":
        income[SGA] = value
    assert svc._sga_to_revenue_pct(income) is None


@pytest.mark.parametrize("value", [0.0, None, -5.0, math.nan])
def test_a_blank_sga_leaves_the_driver_empty_for_an_operating_company(value):
    pillar = _cost("Technology", "Software - Infrastructure", sga=value)
    driver = _sga_driver(pillar)
    assert driver.focal is None and driver.sub_score is None
    assert pillar.score == 5.0        # the two others, never a 10.0 from the blank


@pytest.mark.parametrize("value", [
    0, 0.0, -0.0, None, "absent", -5.0, -1e-9, math.nan, math.inf, 1e-9, 7.78, 200.0, 35071.7,
])
def test_the_median_and_the_scorer_agree_on_every_value(value):
    """The bug was these two disagreeing: this is the test that keeps them one rule."""
    income = {"revenue": 1000.0}
    if value != "absent":
        income[SGA] = value
    assert bool(_bucket("sga_to_revenue", SGA, value)) == (
        _service()._sga_to_revenue_pct(income) is not None
    )


@pytest.mark.parametrize("value", [0.0, 150.0])
def test_rd_reads_the_same_on_both_sides(value):
    income = {"revenue": 1000.0, RD: value}
    assert _bucket("rd_to_revenue", RD, value) == [
        pytest.approx(_service()._rd_to_revenue_pct(income))
    ]


# ── insurers and utilities ──────────────────────────────────────────────────

@pytest.mark.parametrize("industry", INSURERS)
def test_sga_does_not_count_for_an_insurer(industry):
    """Whatever the stored median says: a rebuilt, positive one must not switch it on."""
    pillar = _cost("Financial Services", industry)
    driver = _sga_driver(pillar)
    assert driver.focal is None and driver.sector_median is None and driver.sub_score is None
    assert driver.sample_size is None and driver.period_used is None
    assert pillar.score == 5.0
    assert pillar.confidence == M._CONFIDENCE_MEDIUM


@pytest.mark.parametrize("industry", UTILITIES + ("A Name FMP Adds Next Year", None, ""))
def test_sga_does_not_count_for_a_utility(industry):
    pillar = _cost("Utilities", industry)
    assert _sga_driver(pillar).sub_score is None
    assert pillar.score == 5.0


@pytest.mark.parametrize("sector, industry", [
    ("Technology", "Software - Infrastructure"),
    ("Financial Services", "Banks - Diversified"),
    ("Financial Services", "Asset Management"),
    ("Financial Services", "Financial - Credit Services"),
    ("Real Estate", "REIT - Mortgage"),
    ("Healthcare", "Medical - Healthcare Plans"),
    ("Energy", "Oil & Gas Integrated"),
])
def test_every_other_company_keeps_the_driver(sector, industry):
    driver = _sga_driver(_cost(sector, industry))
    assert driver.focal == pytest.approx(5.0)
    assert driver.sub_score == pytest.approx(7.5)     # half the 10% median, lower is better


def test_a_listed_network_keeps_it_on_its_sector_read():
    driver = _sga_driver(_cost("Financial Services", "Financial - Credit Services", ticker="V"))
    assert driver.sub_score == pytest.approx(7.5)


@pytest.mark.parametrize("sector, industry, expected", [
    ("Financial Services", "INSURANCE - LIFE", False),
    ("Financial Services", "insurance—life", False),
    ("Financial Services", "  Insurance  -  Specialty ", False),
    ("Utilities", "Regulated Electric", False),
    ("  utilities ", None, False),
    ("UTILITIES", "", False),
    (None, "Insurance - Brokers", False),
    (None, None, True),
    ("", "", True),
    (12, 12, True),
    ("Utilities Holdings", "Software - Application", True),   # an unknown sector keeps it
    ("Financial Services", "Insurance", True),            # not an FMP industry: fails open
])
def test_the_gate_spellings(sector, industry, expected):
    assert gate.sga_line_applicable(sector, industry) is expected


def test_an_insurer_short_of_a_second_input_is_unmeasured():
    """Two inputs left, and the pillar needs both: it must fall to the AI estimate, never score
    on one figure. Before this change its SG&A would have made a second."""
    pillar = _cost("Financial Services", "Insurance - Life", operating=None)
    assert pillar.score is None and pillar.confidence == M._CONFIDENCE_LOW


def test_a_stored_zero_median_still_scores_nothing_outside_the_list():
    medians = {**MEDIANS, "sga_to_revenue": 0.0}
    assert _sga_driver(_cost("Technology", "Software - Application", medians=medians)).sub_score is None


def test_the_list_holds_every_fmp_insurance_and_utility_industry():
    """A new FMP industry in either group fails here until someone decides which side it is on.
    The sector check would catch a utility on the scorer, but not in the sector pool."""
    rows = []
    for name in ("industry_universe.json", "benchmark_universe.json"):
        path = _DATA / name
        if path.exists():
            rows += json.loads(path.read_text())["industries"]
    assert len(rows) > 100, "the universe files did not load: the walk proves nothing"
    found = {
        gate.industry_key(r.get("industry")) for r in rows
        if "insurance" in str(r.get("industry")).lower() or r.get("sector") == "Utilities"
    }
    assert found == gate._NO_SGA_INDUSTRIES


# ── the sector pool ─────────────────────────────────────────────────────────

def test_insurers_leave_the_sector_pool_and_banks_stay():
    pool: Dict[Any, List[float]] = defaultdict(list)
    left_out: Dict[str, set] = defaultdict(set)
    key = ("sga_to_revenue", "annual", "2025")
    other = ("operating_margin", "annual", "2025")
    pool_into = ibs.IndustryBenchmarkService._pool_into_sector
    pool_into(pool, {key: [12.0], other: [0.1]}, "Insurance - Life", left_out)
    pool_into(pool, {key: [25.0], other: [0.2]}, "Banks - Regional", left_out)
    assert pool[key] == [25.0]
    assert pool[other] == [0.1, 0.2]
    assert left_out == {"sga_to_revenue": {"Insurance - Life"}}


def test_the_utilities_row_is_not_rebuilt_from_half_a_line():
    """Its stored 0.0 reads as no peer on every scorer, the live one included, so a recompute
    reaching production before this gate cannot switch the driver on for 40 utilities."""
    pool: Dict[Any, List[float]] = defaultdict(list)
    left_out: Dict[str, set] = defaultdict(set)
    key = ("sga_to_revenue", "annual", "2025")
    for industry in UTILITIES:
        ibs.IndustryBenchmarkService._pool_into_sector(pool, {key: [9.0]}, industry, left_out)
    assert key not in pool


@pytest.mark.parametrize("industry", INSURERS + UTILITIES)
def test_peer_metric_applicable_says_no_for_the_two_groups(industry):
    assert gate.peer_metric_applicable("sga_to_revenue", industry) is False
    assert gate.peer_metric_applicable("operating_margin", industry) is True
    assert gate.peer_metric_applicable("rd_to_revenue", industry) is True


@pytest.mark.parametrize("industry", ["Banks - Regional", "Software - Application", None, ""])
def test_peer_metric_applicable_keeps_everyone_else(industry):
    assert gate.peer_metric_applicable("sga_to_revenue", industry) is True


def test_sga_is_not_one_of_the_rows_the_model_is_told_about():
    """`GATED_METRICS` drive the report's "not meaningful" lines and every Health Check row.
    SG&A is on neither surface, so it must stay out of that set."""
    assert gate.SGA_TO_REVENUE not in gate.GATED_METRICS
