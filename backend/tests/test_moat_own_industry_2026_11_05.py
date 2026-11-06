"""A financial company's moat is read against its own industry, 2026-11-05.

The pillars used to read the pooled Financial Services median while every other reader used the
company's own industry, so an insurer's Cost Advantage read 10/10 on asset turnover against
banks. Owner decisions, 2026-11-05: "All financials", "Unmeasured", "Stop counting it", "Leave
them as today". What is pinned here:

  * who moves (`own_industry_peers`), including the two groups the first brief missed: mortgage
    REITs join, and a listed non-lender stays on its sector read, because the Credit Services
    median is built from lenders only;
  * the Financial Services rule: 10 companies or no peer, the newest year alone, never an older
    one, and "no peer" is unmeasured rather than a neutral 5.0;
  * Brand Power stops counting where gross margin and P/S read N/M;
  * a pillar a financial company was not measured on takes no industry peer average, which is
    what keeps a row the refresh cannot rewrite off every report, so no cleanup step exists;
  * the app's words reach the Insight prompt, chat and the PDF for an unmeasured moat;
  * an operating company takes exactly the path it took before;
  * Berkshire's measured figures carry through to its score.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

# Imported here so its module-level lookup binding never lands on a test's stub.
import app.services.health_check_service  # noqa: F401
from app.services import chat_context_resolver as chat
from app.services import industry_moat_benchmark_service as imb
from app.services import moat_scoring_service as M
from app.services import sector_benchmark_lookup as sbl
from app.services.agents import narrative_prompts as np_
from app.services.agents import persona_scoring as scoring
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.narrative_prompts import stage_a_fallback
from app.services.agents.persona_config import get_persona_config
from app.services.financials_metric_gate import FS_INDUSTRY_PEER_FLOOR, NON_LENDER_MEMBERS
from app.services.pdf_report_service import build_context, render_html
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed, lookup_failed

# The schema-parity builder and its autouse fixture: stubs every live benchmark lookup
# `assemble_report` reaches. Tests below replace the moat service and the peer rows.
from test_ticker_report_schema_parity import (  # noqa: F401
    _make_collected_data,
    _no_live_benchmark_lookup,
)

FS = "Financial Services"
BANK = "Banks - Regional"
INSURER = "Insurance - Diversified"
CREDIT = "Financial - Credit Services"
CAPITAL_MARKETS = "Financial - Capital Markets"
MORTGAGE_REIT = "REIT - Mortgage"
ASSET_MANAGER = "Asset Management"
SOFTWARE = "Software - Infrastructure"

ALL_METRICS = (
    "gross_margin", "operating_margin", "ps_ratio", "asset_turnover", "revenue_yoy",
    "rd_to_revenue", "sga_to_revenue", "intangibles_to_assets", "deferred_revenue_to_revenue",
)
# Not 5.0-producing values: a read of the wrong group has to show in the numbers.
INDUSTRY_MEDIANS = {
    "gross_margin": 0.60, "operating_margin": 0.40, "ps_ratio": 4.0, "asset_turnover": 0.40,
    "revenue_yoy": 20.0, "sga_to_revenue": 20.0,
}
SECTOR_MEDIANS = {
    "gross_margin": 0.15, "operating_margin": 0.10, "ps_ratio": 1.0, "asset_turnover": 0.10,
    "revenue_yoy": 0.0, "sga_to_revenue": 5.0,
}


def _cells(medians: Dict[str, float], n: int = 50, year: str = "2025") -> Dict[str, Any]:
    return {metric: {year: {"median": value, "n": n}} for metric, value in medians.items()}


class _Lookup:
    """Both reads the scorer can make, and which one it made."""

    def __init__(self, *, sector: Any = None, industry: Any = None) -> None:
        self.sector = sector if sector is not None else {}
        self.industry = industry if industry is not None else {}
        self.calls: List[tuple] = []

    def get_sector_benchmarks_with_n(self, sector, metrics, period_type):
        self.calls.append(("sector", sector, period_type))
        return {m: dict(self.sector.get(m, {})) for m in metrics}

    def get_industry_benchmarks_with_n(self, industry, metrics, period_type):
        self.calls.append(("industry", industry, period_type))
        if isinstance(self.industry, Exception):
            raise self.industry
        if not isinstance(self.industry, dict):
            return self.industry
        result = {m: dict(self.industry.get(m, {})) for m in metrics}
        return (
            BenchmarkLookupFailed(result) if lookup_failed(self.industry) else result
        )


def _service(lookup: Any) -> M.MoatScoringService:
    svc = M.MoatScoringService.__new__(M.MoatScoringService)
    svc._lookup = lookup
    return svc


@dataclass
class _Tam:
    hhi: Optional[float] = 2000.0
    lifecycle_phase: Optional[str] = "mature"
    source_grain: Optional[str] = "industry"


def _statements(
    *, gross: float = 0.30, operating: float = 0.20, ps: float = 2.0, turnover: float = 0.20,
    sga: float = 10.0, revenue: float = 100e6, prior_revenue: float = 90e6,
    date: Any = "2025-12-31",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    income = [
        {"date": date, "revenue": revenue, "reportedCurrency": "USD",
         "sellingGeneralAndAdministrativeExpenses": revenue * sga / 100.0,
         "researchAndDevelopmentExpenses": 0.0},
        {"date": "2024-12-31", "revenue": prior_revenue, "reportedCurrency": "USD"},
    ]
    balance = [{"date": date, "totalAssets": 1e9, "goodwillAndIntangibleAssets": 2e7}]
    ratios = [{
        "date": date, "grossProfitMargin": gross, "operatingProfitMargin": operating,
        "priceToSalesRatio": ps, "assetTurnover": turnover, "revenuePerShare": 10.0,
    }]
    return income, balance, ratios


def _score(
    lookup: Any, *, sector: Any = FS, industry: Any = BANK, ticker: Any = "ACME",
    tam: Any = None, **figures: Any,
) -> M.MoatScores:
    income, balance, ratios = _statements(**figures)
    profile: Dict[str, Any] = {"sector": sector, "industry": industry}
    if ticker is not None:
        profile["symbol"] = ticker
    return _service(lookup).score(
        sector=sector, industry=industry, profile=profile, income=income, balance=balance,
        ratios=ratios, industry_tam=_Tam() if tam is None else tam,
    )


def _driver(result: M.PillarResult, metric: str) -> M.MetricDriver:
    return next(d for d in result.drivers if d.metric == metric)


def _vital(scores: M.MoatScores) -> Dict[str, Any]:
    dims = []
    for name, pillar in scores.items():
        if pillar.score is not None:
            dims.append({**pillar.to_dict(), "source": "deterministic", "name": name})
    return C._derive_moat_vital(dims)


# ── who moves ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sector,industry,ticker,expected,why", [
    (FS, BANK, "USB", True, "a bank"),
    (FS, INSURER, "BRK-B", True, "an insurer, Berkshire through its FMP industry"),
    ("Financials", BANK, "USB", True, "the other feed's sector spelling"),
    (" financial  services ", BANK, "USB", True, "case and spacing"),
    (FS, ASSET_MANAGER, "BLK", True, "All financials, not only the N/M group"),
    (FS, "Financial - Data & Stock Exchanges", "SPGI", True, "All financials"),
    (FS, "Insurance - Brokers", "AON", True, "All financials"),
    (FS, CREDIT, "AXP", True, "a lender of the mixed industry"),
    (FS, CAPITAL_MARKETS, "GS", True, "a listed broker"),
    (FS, CAPITAL_MARKETS, "MARA", True, "its peers are its FMP industry too"),
    ("Real Estate", MORTGAGE_REIT, "NLY", True, "on the N/M list, outside the sector rule"),
    (None, MORTGAGE_REIT, "NLY", True, "the industry decides this one, not the sector"),
    (FS, CREDIT, "V", False, "the Credit Services median holds lenders only"),
    (FS, CREDIT, " ma ", False, "normalised ticker"),
    (FS, "", "X", False, "no industry named: the sector read stays"),
    (FS, "   ", "X", False, "a blank industry is no industry"),
    (FS, None, "X", False, "no industry"),
    (FS, 123, "X", False, "a junk industry"),
    ("Technology", SOFTWARE, "MSFT", False, "an operating company"),
    ("Real Estate", "REIT - Residential", "EQR", False, "an equity REIT"),
    (None, None, None, False, "a failed profile"),
])
def test_who_moves(sector, industry, ticker, expected, why):
    assert M.own_industry_peers(sector, industry, ticker) is expected, why


def test_every_listed_non_lender_stays_on_its_sector_read():
    for ticker in NON_LENDER_MEMBERS:
        assert M.own_industry_peers(FS, CREDIT, ticker) is False, ticker


def test_an_explicit_ticker_spares_visa_when_the_profile_has_no_symbol():
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS))
    _service(lookup).score(
        sector=FS, industry=CREDIT, profile={}, income=[], balance=[], ratios=[], ticker="V",
    )
    assert [call[0] for call in lookup.calls] == ["sector"]


def test_a_financial_company_never_reads_the_sector():
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS))
    scores = _score(lookup)
    assert lookup.calls == [("industry", BANK, "annual")]
    cost = scores[M.PILLAR_COST]
    assert _driver(cost, "operating_margin").sector_median == 0.40
    assert _driver(cost, "operating_margin").sample_size == 50
    assert _driver(cost, "operating_margin").period_used == "2025"
    assert scores.lookup_failed is False


def test_an_operating_company_never_reads_an_industry():
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS))
    scores = _score(lookup, sector="Technology", industry=SOFTWARE, ticker="MSFT")
    assert lookup.calls == [("sector", "Technology", "annual")]
    assert _driver(scores[M.PILLAR_COST], "operating_margin").sector_median == 0.10


@pytest.mark.parametrize("sector,industry,ticker", [
    (FS, "", "X"), (FS, None, "X"), (FS, "   ", "X"), (FS, CREDIT, "V"),
])
def test_the_exceptions_keep_the_sector_read(sector, industry, ticker):
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS))
    _score(lookup, sector=sector, industry=industry, ticker=ticker)
    assert [call[0] for call in lookup.calls] == ["sector"]


def test_a_mortgage_reit_reads_its_industry_not_real_estate():
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS, n=17))
    scores = _score(lookup, sector="Real Estate", industry=MORTGAGE_REIT, ticker="NLY")
    assert lookup.calls == [("industry", MORTGAGE_REIT, "annual")]
    assert _driver(scores[M.PILLAR_COST], "operating_margin").sample_size == 17


def test_the_industry_name_is_read_trimmed():
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS))
    _score(lookup, industry=f"  {BANK} ")
    assert lookup.calls == [("industry", BANK, "annual")]


# ── ten companies or no peer ─────────────────────────────────────────────────

def test_the_floor_is_the_financial_services_rule():
    assert FS_INDUSTRY_PEER_FLOOR == 10


@pytest.mark.parametrize("n,reads", [(10, True), (13, True), (9, False), (5, False), (0, False)])
def test_ten_companies_or_no_peer(n, reads):
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS, n=n))
    driver = _driver(_score(lookup)[M.PILLAR_COST], "operating_margin")
    assert (driver.sector_median == 0.40) is reads
    assert (driver.sub_score is not None) is reads


def test_insurance_diversified_reads_where_the_old_floor_would_not():
    """13 companies: the sector rule's floor of 20 would leave Berkshire unmeasured."""
    assert M._pick_year_at_or_before({"2025": {"median": 1.0, "n": 13}}, "2025") is None
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS, n=13))
    assert _score(lookup, industry=INSURER)[M.PILLAR_COST].score is not None


def test_a_thin_newest_year_is_no_peer_never_an_older_one():
    industry = {
        metric: {"2025": {"median": value, "n": 9}, "2024": {"median": value * 2, "n": 40}}
        for metric, value in INDUSTRY_MEDIANS.items()
    }
    driver = _driver(_score(_Lookup(industry=industry))[M.PILLAR_COST], "operating_margin")
    assert driver.sector_median is None and driver.sub_score is None


def test_the_sector_read_still_reaches_back():
    """The old rule stands for everyone else: the two year walks are not one rule."""
    years = {"2025": {"median": 1.0, "n": 9}, "2024": {"median": 2.0, "n": 40}}
    assert M._pick_year_at_or_before(years, "2025")["period"] == "2024"
    assert M._pick_year_at_or_before(years, "2025", 10, newest_only=True) is None


def test_a_newer_year_is_never_read():
    industry = {m: {"2026": {"median": v, "n": 90}} for m, v in INDUSTRY_MEDIANS.items()}
    driver = _driver(_score(_Lookup(industry=industry))[M.PILLAR_COST], "operating_margin")
    assert driver.sector_median is None


def test_the_newest_year_at_or_before_is_read():
    """An older filing, or an industry whose newest stored year is behind the company."""
    industry = _cells(INDUSTRY_MEDIANS, n=12, year="2023")
    driver = _driver(_score(_Lookup(industry=industry))[M.PILLAR_COST], "operating_margin")
    assert driver.sector_median == 0.40 and driver.period_used == "2023"


def test_no_usable_date_is_no_peer():
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS))
    scores = _score(lookup, date=None)
    assert _driver(scores[M.PILLAR_COST], "operating_margin").sector_median is None


@pytest.mark.parametrize("cell", [
    {"median": None, "n": 50},
    {"median": float("nan"), "n": 50},
    {"median": float("inf"), "n": 50},
    {"median": True, "n": 50},
    {"median": "n/a", "n": 50},
    {"median": 0.4, "n": None},
    {"median": 0.4, "n": float("nan")},
    {"median": 0.4, "n": True},
    {"median": 0.4, "n": "many"},
    {"median": 0.4},
    {"n": 50},
    "junk",
    None,
    [],
])
def test_a_malformed_cell_is_no_reading(cell):
    industry = {m: {"2025": cell} for m in ALL_METRICS}
    scores = _score(_Lookup(industry=industry))
    assert all(pillar.score is None for pillar in scores.values())


@pytest.mark.parametrize("label", ["TTM", "FY25", "", "20255", "Q4'25"])
def test_a_label_that_names_no_year_is_skipped(label):
    industry = {m: {label: {"median": v, "n": 50}} for m, v in INDUSTRY_MEDIANS.items()}
    driver = _driver(_score(_Lookup(industry=industry))[M.PILLAR_COST], "operating_margin")
    assert driver.sector_median is None


@pytest.mark.parametrize("median", [0.0, -0.3])
def test_a_zero_or_negative_median_scores_nothing(median):
    """Life and P&C insurers' SG&A median is 0 for 2025: read, and no score from it."""
    industry = _cells({**INDUSTRY_MEDIANS, "sga_to_revenue": median})
    driver = _driver(_score(_Lookup(industry=industry))[M.PILLAR_COST], "sga_to_revenue")
    assert driver.sub_score is None


@pytest.mark.parametrize("gross,turnover,sga", [
    (1e9, 1e9, 1e-9), (1e-9, 1e-9, 1e9),
])
def test_extreme_figures_stay_on_the_scale(gross, turnover, sga):
    scores = _score(
        _Lookup(industry=_cells(INDUSTRY_MEDIANS)), gross=gross, turnover=turnover, sga=sga,
        operating=gross,
    )
    for pillar in scores.values():
        for driver in pillar.drivers:
            assert driver.sub_score is None or 0.0 <= driver.sub_score <= 10.0


def test_a_thin_metric_goes_unscored_while_its_neighbours_stay():
    industry = _cells(INDUSTRY_MEDIANS)
    industry["operating_margin"] = {"2025": {"median": 0.4, "n": 6}}
    cost = _score(_Lookup(industry=industry))[M.PILLAR_COST]
    assert _driver(cost, "operating_margin").sub_score is None
    assert _driver(cost, "asset_turnover").sub_score is not None


def test_each_metric_judges_its_own_newest_year_as_the_cards_do():
    """`get_current_benchmarks` takes the newest year per metric. A stricter moat rule would
    make the moat and the cards disagree about one company's peers."""
    industry = _cells(INDUSTRY_MEDIANS)
    industry["operating_margin"] = {"2024": {"median": 0.4, "n": 30}}
    cost = _score(_Lookup(industry=industry))[M.PILLAR_COST]
    assert _driver(cost, "operating_margin").period_used == "2024"
    assert _driver(cost, "asset_turnover").period_used == "2025"


# ── unmeasured stays unmeasured ──────────────────────────────────────────────

@pytest.mark.parametrize("industry", [{}, _cells(INDUSTRY_MEDIANS, n=5)])
def test_an_industry_too_small_is_unmeasured(industry):
    scores = _score(_Lookup(industry=industry), industry="Insurance - Reinsurance")
    assert all(pillar.score is None for pillar in scores.values())
    assert scores.lookup_failed is False, "a state of the company, not an outage"


def test_network_effects_does_not_survive_on_industry_wide_inputs_alone():
    """HHI and the lifecycle are the same for every company: no growth reading, no pillar."""
    network = _score(_Lookup())[M.PILLAR_NETWORK]
    assert _driver(network, "industry_hhi").sub_score is not None
    assert network.score is None


class _PeerRows:
    def __init__(self, rows: Dict[str, float]) -> None:
        self.rows = rows

    def get_pillar_benchmarks(self, industry):
        return dict(self.rows)


_INDUSTRY_ROW = 7.7


def _report(
    monkeypatch, lookup: Any, *, ticker: str = "USB", sector: str = FS, industry: str = BANK,
    ai_score: float = 6.0, ai_peer: float = 4.0, symbol: bool = True, **figures: Any,
) -> Dict[str, Any]:
    """The real `assemble_report`, Stage A carrying all five AI pillars, with an industry peer
    row on every pillar: the shape of a row the refresh left behind."""
    monkeypatch.setattr(M, "get_moat_scoring_service", lambda: _service(lookup))
    monkeypatch.setattr(
        imb, "_lookup_singleton", _PeerRows({name: _INDUSTRY_ROW for name in M.PILLAR_ORDER}),
    )
    out = _make_collected_data(ticker=ticker)
    out.profile.update(sector=sector, industry=industry)
    if symbol:
        out.profile["symbol"] = ticker
    else:
        out.profile.pop("symbol", None)
    out.income, out.balance, out.ratios = _statements(**figures)
    out.industry_tam = _Tam()
    shell = stage_a_fallback()
    shell["moat_competition"]["dimensions"] = [
        {"name": name, "score": ai_score, "peer_score": ai_peer} for name in M.PILLAR_ORDER
    ]
    return C.TickerReportDataCollector().assemble_report(out, shell)


def _dims(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {d["name"]: d for d in report["moat_competition"]["dimensions"]}


def _moat_vital(report: Dict[str, Any]) -> Dict[str, Any]:
    return report["_scoring_inputs"]["moat"]


def test_an_unmeasured_moat_is_not_a_neutral_one(monkeypatch):
    report = _report(monkeypatch, _Lookup(), ai_score=9.0)
    assert all(d["source"] == "ai_legacy" for d in _dims(report).values())
    vital = _moat_vital(report)
    assert vital["score"]["value"] is None, "an estimate of 9.0 sets nothing"
    assert vital["overall_rating"] == "none"

    def scored(moat):
        return {"_scoring_inputs": {
            "moat": moat, "profitability": {"score": {"value": 8.0}},
            "valuation": {"score": {"value": 8.0}},
        }}

    unmeasured = scoring.compute_quality_score("warren_buffett", scored(vital))
    neutral = scoring.compute_quality_score(
        "warren_buffett", scored({"score": {"value": 5.0}}),
    )
    assert unmeasured > neutral, "the weight spreads over what was measured"
    assert unmeasured == pytest.approx(80.0, abs=0.01)


def test_an_ai_brand_power_never_moves_a_banks_moat(monkeypatch):
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS))
    low = _moat_vital(_report(monkeypatch, lookup, ai_score=1.0))
    high = _moat_vital(_report(monkeypatch, lookup, ai_score=9.9))
    assert low["score"]["value"] == high["score"]["value"] is not None
    assert low["overall_rating"] == high["overall_rating"]


def test_a_measured_pillar_takes_its_industry_row_and_an_estimate_does_not(monkeypatch):
    dims = _dims(_report(monkeypatch, _Lookup(industry=_cells(INDUSTRY_MEDIANS))))
    cost = dims[M.PILLAR_COST]
    assert cost["source"] == "deterministic"
    assert (cost["peer_score"], cost["peer_source"]) == (_INDUSTRY_ROW, C.MOAT_PEER_SOURCE_INDUSTRY)
    brand = dims[M.PILLAR_BRAND]
    assert brand["source"] == "ai_legacy"
    assert (brand["peer_score"], brand["peer_source"]) == (4.0, C.MOAT_PEER_SOURCE_AI)


@pytest.mark.parametrize("cells,why", [
    ({}, "no median at all"),
    (_cells(INDUSTRY_MEDIANS, n=9), "an industry that fell under 10 in a later quarter"),
])
def test_a_row_left_behind_reaches_no_report(monkeypatch, cells, why):
    dims = _dims(_report(monkeypatch, _Lookup(industry=cells), industry="Insurance - Reinsurance"))
    assert dims, why
    assert all(d["peer_source"] != C.MOAT_PEER_SOURCE_INDUSTRY for d in dims.values()), why
    assert all(d["peer_score"] != _INDUSTRY_ROW for d in dims.values()), why


@pytest.mark.parametrize("ticker,sector,industry,read", [
    ("MSFT", "Technology", SOFTWARE, "sector"),
    ("V", FS, CREDIT, "sector"),
    ("EQR", "Real Estate", "REIT - Residential", "sector"),
])
def test_every_other_company_keeps_its_overlay_on_an_estimate(monkeypatch, ticker, sector, industry, read):
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS))
    dims = _dims(_report(monkeypatch, lookup, ticker=ticker, sector=sector, industry=industry))
    switching = dims[M.PILLAR_SWITCHING]
    assert switching["source"] == "ai_legacy"
    assert switching["peer_score"] == _INDUSTRY_ROW
    assert switching["peer_source"] == C.MOAT_PEER_SOURCE_INDUSTRY
    assert {call[0] for call in lookup.calls} == {read}


def test_the_report_names_the_ticker_itself(monkeypatch):
    """The scorer and the overlay must agree on who Visa is, with or without a `symbol`."""
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS))
    dims = _dims(_report(monkeypatch, lookup, ticker="V", industry=CREDIT, symbol=False))
    assert {call[0] for call in lookup.calls} == {"sector"}
    assert dims[M.PILLAR_SWITCHING]["peer_source"] == C.MOAT_PEER_SOURCE_INDUSTRY


# ── what an unmeasured moat says ─────────────────────────────────────────────

def test_chat_never_quotes_an_estimate_as_a_peer_comparison(monkeypatch):
    report = _report(monkeypatch, _Lookup(industry=_cells(INDUSTRY_MEDIANS)))
    head, items, _cap = chat._moat_figures(report)[0]
    assert head == "Moat pillars (score out of 10): "
    brand = next(item for item in items if item.startswith(M.PILLAR_BRAND))
    assert "vs peers" not in brand
    cost = next(item for item in items if item.startswith(M.PILLAR_COST))
    assert f"vs peers {_INDUSTRY_ROW}" in cost


def test_chat_is_told_an_unmeasured_moat_is_not_measured(monkeypatch):
    report = _report(monkeypatch, _Lookup())
    head, items, _cap = chat._moat_figures(report)[0]
    assert head.startswith("Moat not measured")
    assert items, "the estimates stay, labelled by the line in front of them"


@pytest.mark.parametrize("source", [None, "grounded"])
def test_a_report_from_before_the_tags_reads_as_before(source):
    """A retired tier is no estimate of today's kind, and old reports stay readable."""
    report = {"moat_competition": {"dimensions": [
        {"name": M.PILLAR_BRAND, "score": 7.0, "source": source},
    ]}}
    assert chat._moat_figures(report)[0][0] == "Moat pillars (score out of 10): "


PERSONA = get_persona_config("warren_buffett")


def _durability(dims: List[Dict[str, Any]]) -> str:
    shell = {"moat_competition": {"dimensions": dims, "competitors": []}}
    return np_._moat_durability_note_prompt(PERSONA, "EVIDENCE", shell)


def test_the_insight_prompt_makes_no_claim_about_an_unmeasured_moat():
    prompt = _durability([{
        "name": M.PILLAR_BRAND, "score": 9.0, "peer_score": 5.0, "source": "ai_legacy",
        "peer_source": C.MOAT_PEER_SOURCE_AI,
    }])
    assert "NOT MEASURED" in prompt
    for line in ("not yet visible in the numbers", "OUT-DEFENDS PEERS", "TRAILS PEERS",
                 "use the edge above", "how durable its moat is", "Sentence 1 — judge"):
        assert line not in prompt, line
    assert "biggest threat" in prompt, "it still reads how the company competes"


def test_the_insight_prompt_overrides_a_lens_about_the_moat():
    """Buffett's lens asks for moat durability. The prompt has to say which one wins."""
    assert "whatever a lens above asks" in _durability([])


def test_the_insight_prompt_names_no_company_with_no_competitors_listed():
    assert "name no company" in _durability([])


def _competitive(dims: List[Dict[str, Any]]) -> str:
    shell = {"moat_competition": {"dimensions": dims, "competitors": []}}
    return np_._moat_competitive_insight_prompt(PERSONA, "EVIDENCE", shell)


def test_the_competitive_line_gets_the_same_guard():
    """The only moat prose the PDF prints, right under its "Moat not measured" line."""
    estimate = {"name": M.PILLAR_BRAND, "score": 9.0, "source": "ai_legacy"}
    measured = {"name": M.PILLAR_COST, "score": 7.5, "source": "deterministic"}
    assert "The moat was not measured" in _competitive([estimate])
    assert "The moat was not measured" not in _competitive([estimate, measured])


def test_chat_keeps_all_five_pillars_inside_its_cap():
    """The not-measured lead must not push the fifth pillar past `_FIG_MOAT_CAP`."""
    report = {"moat_competition": {"dimensions": [
        {"name": name, "score": 8.8, "peer_score": 7.7, "source": "ai_legacy",
         "peer_source": C.MOAT_PEER_SOURCE_INDUSTRY}
        for name in M.PILLAR_ORDER
    ]}}
    head, items, cap = chat._moat_figures(report)[0]
    assert len(head) + len("; ".join(items)) <= cap


def test_the_insight_prompt_keeps_its_comparison_for_a_measured_moat():
    prompt = _durability([{
        "name": M.PILLAR_COST, "score": 7.5, "peer_score": 5.0, "source": "deterministic",
        "peer_source": C.MOAT_PEER_SOURCE_INDUSTRY,
    }])
    assert "NOT MEASURED" not in prompt
    for line in ("OUT-DEFENDS PEERS", "TRAILS PEERS", "use the edge above",
                 "how durable its moat is", "Sentence 1 — judge"):
        assert line in prompt, line


def _pdf(report: Dict[str, Any]) -> str:
    return render_html(build_context(report, None))


def test_the_pdf_says_so_and_prints_no_peer_figure(monkeypatch):
    report = _report(monkeypatch, _Lookup(), ai_score=8.8, ai_peer=6.0)
    assert "stroke-dasharray" not in build_context(report, None)["charts"]["radar"]
    html = _pdf(report)
    assert "Moat not measured" in html
    assert "Industry avg" not in html
    assert ">6.0<" not in html, "the AI's peer guess is not a peer figure"
    assert ">8.8<" in html, "the estimates stay, the owner's 2026-10-28 call"


def test_the_pdf_of_a_measured_moat_is_as_it_was(monkeypatch):
    report = _report(monkeypatch, _Lookup(industry=_cells(INDUSTRY_MEDIANS)))
    assert "stroke-dasharray" in build_context(report, None)["charts"]["radar"]
    html = _pdf(report)
    assert "Moat not measured" not in html
    assert "Industry avg" in html and ">Peer<" in html


@pytest.mark.parametrize("source", [None, "grounded"])
def test_the_pdf_of_a_report_from_before_the_tags_is_as_it_was(source):
    report = {"symbol": "OLD", "moat_competition": {"dimensions": [
        {"name": name, "score": 6.0, "peer_score": 5.0, "source": source}
        for name in M.PILLAR_ORDER
    ]}}
    context = build_context(report, None)
    assert context["moat"]["not_measured"] is False
    assert "Industry avg" in render_html(context)


def test_an_estimate_never_reads_flat_for_a_financial_company(monkeypatch):
    """Stage A leaves a peer at 0, it lands on the placeholder, and the coverage note would call
    an 8.8 estimate "flat". Operating companies keep their note, unchanged."""
    bank = _report(monkeypatch, _Lookup(), ai_score=8.8, ai_peer=0.0)
    assert bank["moat_competition"]["_coverage_note"] is None

    monkeypatch.setattr(imb, "_lookup_singleton", _PeerRows({}))
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS))
    monkeypatch.setattr(M, "get_moat_scoring_service", lambda: _service(lookup))
    out = _make_collected_data(ticker="MSFT")
    out.profile.update(symbol="MSFT", sector="Technology", industry=SOFTWARE)
    shell = stage_a_fallback()
    shell["moat_competition"]["dimensions"] = [
        {"name": name, "score": 8.8, "peer_score": 0.0} for name in M.PILLAR_ORDER
    ]
    report = C.TickerReportDataCollector().assemble_report(out, shell)
    assert report["moat_competition"]["_coverage_note"]


# ── a failed read is an outage ───────────────────────────────────────────────

@pytest.mark.parametrize("answer", [
    RuntimeError("boom"), ["not", "a", "mapping"], BenchmarkLookupFailed({}),
])
def test_a_failed_read_is_flagged_never_read_as_small(answer):
    scores = _score(_Lookup(industry=answer))
    assert scores.lookup_failed is True
    assert lookup_failed(scores)


def test_a_partly_failed_read_scores_what_loaded_and_says_so():
    loaded = BenchmarkLookupFailed(_cells(INDUSTRY_MEDIANS))
    scores = _score(_Lookup(industry=loaded))
    assert scores.lookup_failed is True
    assert _driver(scores[M.PILLAR_COST], "operating_margin").sector_median == 0.40


def test_a_failed_read_names_the_industry(caplog):
    with caplog.at_level("WARNING"):
        _score(_Lookup(industry=RuntimeError("boom")))
    assert BANK in caplog.text and "RuntimeError" in caplog.text


def test_a_small_industry_is_logged_at_info(caplog):
    with caplog.at_level("INFO"):
        _score(_Lookup(), industry="Insurance - Reinsurance")
    records = [r for r in caplog.records if "stay unmeasured" in r.getMessage()]
    assert [r.levelname for r in records] == ["INFO"]


# ── the real lookup ──────────────────────────────────────────────────────────

@pytest.fixture
def real_lookup():
    sbl.clear_cache()
    lookup = sbl.SectorBenchmarkLookup.__new__(sbl.SectorBenchmarkLookup)
    yield lookup
    sbl.clear_cache()


def _rows(industry: Dict[str, Dict[str, tuple]]) -> List[Dict[str, Any]]:
    return [
        {"metric_name": metric, "period_label": label, "median_value": value, "sample_size": n}
        for metric, cells in industry.items() for label, (value, n) in cells.items()
    ]


def test_the_lookup_keeps_thin_cells(real_lookup):
    real_lookup._fetch_rows = lambda *a, **k: _rows(
        {"gross_margin": {"2025": (0.4, 5), "2024": (0.5, 40)}},
    )
    out = real_lookup.get_industry_benchmarks_with_n(BANK, ["gross_margin", "ps_ratio"], "annual")
    assert out == {
        "gross_margin": {"2025": {"median": 0.4, "n": 5}, "2024": {"median": 0.5, "n": 40}},
        "ps_ratio": {},
    }
    assert not lookup_failed(out)


def test_the_lookup_reads_one_industry_and_never_a_sector(real_lookup):
    seen = []

    def fetch(columns, sector, metrics, period_type, industry=""):
        seen.append((sector, industry, period_type))
        return []

    real_lookup._fetch_rows = fetch
    real_lookup.get_industry_benchmarks_with_n(BANK, ["gross_margin"], "annual")
    assert seen == [("", BANK, "annual")]


def test_a_missing_sample_size_counts_as_none(real_lookup):
    real_lookup._fetch_rows = lambda *a, **k: [{
        "metric_name": "gross_margin", "period_label": "2025",
        "median_value": 0.4, "sample_size": None,
    }]
    out = real_lookup.get_industry_benchmarks_with_n(BANK, ["gross_margin"], "annual")
    assert out["gross_margin"]["2025"]["n"] == 0


def test_a_failed_read_is_flagged_and_never_cached(real_lookup):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    real_lookup._fetch_rows = boom
    failed = real_lookup.get_industry_benchmarks_with_n(BANK, ["gross_margin"], "annual")
    assert lookup_failed(failed) and failed == {"gross_margin": {}}

    real_lookup._fetch_rows = lambda *a, **k: _rows({"gross_margin": {"2025": (0.4, 40)}})
    healed = real_lookup.get_industry_benchmarks_with_n(BANK, ["gross_margin"], "annual")
    assert not lookup_failed(healed)
    assert healed["gross_margin"]["2025"]["median"] == 0.4


def test_the_scorer_reads_the_lookups_real_shape(real_lookup):
    """The contract between them: a fake of either side could hide a renamed key."""
    real_lookup._fetch_rows = lambda *a, **k: _rows({
        "operating_margin": {"2025": (0.4, 13)},
        "asset_turnover": {"2025": (0.4, 9), "2024": (0.4, 40)},
    })
    cost = _score(real_lookup, industry=INSURER)[M.PILLAR_COST]
    assert _driver(cost, "operating_margin").sample_size == 13
    assert _driver(cost, "asset_turnover").sector_median is None


# ── Brand Power ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sector,industry,ticker", [
    (FS, BANK, "USB"),
    (FS, "Banks - Diversified", "JPM"),
    (FS, INSURER, "BRK-B"),
    (FS, "Insurance - Life", "MET"),
    (FS, CREDIT, "AXP"),
    (FS, CAPITAL_MARKETS, "GS"),
    ("Real Estate", MORTGAGE_REIT, "NLY"),
])
def test_brand_power_stops_counting_where_its_rows_read_nm(sector, industry, ticker):
    scores = _score(
        _Lookup(industry=_cells(INDUSTRY_MEDIANS)),
        sector=sector, industry=industry, ticker=ticker,
    )
    brand = scores[M.PILLAR_BRAND]
    assert brand.score is None and brand.confidence == "low"
    assert all(driver.sub_score is None for driver in brand.drivers)
    assert _driver(brand, "gross_margin").focal == 0.30, "the figure is kept, unscored"
    assert scores[M.PILLAR_COST].score is not None, "only this pillar goes"


@pytest.mark.parametrize("sector,industry,ticker", [
    (FS, ASSET_MANAGER, "BLK"),
    (FS, "Insurance - Brokers", "AON"),
    (FS, "Financial - Data & Stock Exchanges", "SPGI"),
    (FS, CAPITAL_MARKETS, "MARA"),
])
def test_brand_power_keeps_counting_for_fee_businesses(sector, industry, ticker):
    scores = _score(
        _Lookup(industry=_cells(INDUSTRY_MEDIANS)),
        sector=sector, industry=industry, ticker=ticker,
    )
    assert scores[M.PILLAR_BRAND].score is not None


def test_a_listed_network_is_scored_exactly_as_before():
    scores = _score(
        _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS)),
        industry=CREDIT, ticker="V",
    )
    brand = scores[M.PILLAR_BRAND]
    assert brand.score is not None
    assert _driver(brand, "ps_ratio").sector_median == 1.0


def test_brand_power_counts_by_default():
    """A direct caller outside `score` is an operating company's path."""
    svc = _service(_Lookup())
    medians = {
        "gross_margin": {"median": 0.3, "period": "2025", "n": 50},
        "ps_ratio": {"median": 2.0, "period": "2025", "n": 50},
    }
    ratios = {"grossProfitMargin": 0.3, "priceToSalesRatio": 2.0, "revenuePerShare": 1.0}
    assert svc._score_brand_power(ratios, medians).score == 5.0


# ── Berkshire, on the figures measured 2026-11-05 ────────────────────────────

# Production's Insurance - Diversified 2025 medians (n=13) and FMP's FY2025 figures.
_BRK_MEDIANS = {
    "gross_margin": 0.4608, "operating_margin": 0.1585, "ps_ratio": 1.3771,
    "asset_turnover": 0.1905, "revenue_yoy": 7.1, "sga_to_revenue": 4.4471,
}


def _berkshire() -> M.MoatScores:
    return _score(
        _Lookup(industry=_cells(_BRK_MEDIANS, n=13)), industry=INSURER, ticker="BRK-B",
        gross=0.236, operating=0.159, ps=2.919, turnover=0.304, sga=7.779,
        revenue=371e9, prior_revenue=371e9, tam=_Tam(hhi=3714.68),
    )


def test_berkshires_pillars():
    scores = _berkshire()
    assert scores[M.PILLAR_NETWORK].score == 5.2
    assert scores[M.PILLAR_COST].score == 4.9
    assert scores[M.PILLAR_BRAND].score is None
    assert scores[M.PILLAR_SWITCHING].score is None
    assert scores[M.PILLAR_INTANGIBLE].score is None


def test_berkshires_moat_and_score():
    vital = _vital(_berkshire())
    assert vital["score"]["value"] == 5.1
    assert vital["overall_rating"] == "none"

    def row(value):
        return {"score": {"value": value}}

    signals = {
        "fcf": None, "roe": 11.9, "roic": None, "mkt_cap": 1223929379887.0, "mos_pct": None,
        "eps_cagr": 1.3, "pe_ratio": 14.35, "moat_score": vital["score"]["value"],
        "net_income": 66968000000.0, "debt_equity": None, "gross_margin": None,
        "revenue_cagr": 2.2, "revenue_growth": 0.0, "gross_margin_prev": None,
        "excluded_rows": ["ev_ebitda", "fcf_growth", "gross_margin", "pfcf", "ps"],
    }
    report = {"_scoring_inputs": {
        "moat": vital, "macro": row(3.6), "insider": row(4.9), "revenue": row(0.9),
        "forecast": row(5.3), "valuation": row(2.5), "profitability": row(5.9),
        "financial_health": row(None), "wall_street": row(None),
        "capital_allocation": row(5.2), "_style_signals": signals,
    }}
    assert scoring.compute_quality_score("warren_buffett", report) == 40.4


# ── the operating path, unchanged ────────────────────────────────────────────

def test_an_operating_companys_figures_are_pinned():
    """Hand-worked on the geometric scale, so a change to this path cannot pass here."""
    scores = _score(
        _Lookup(sector=_cells({
            "gross_margin": 0.30, "ps_ratio": 1.0, "operating_margin": 0.20,
            "asset_turnover": 0.40, "sga_to_revenue": 10.0, "revenue_yoy": 11.11,
        }, n=20)),
        sector="Technology", industry=SOFTWARE, ticker="MSFT",
    )
    assert scores[M.PILLAR_BRAND].score == 6.2      # 5.0 and 7.5 (P/S twice the median)
    assert scores[M.PILLAR_COST].score == 4.2       # 5.0, 2.5, 5.0
    assert scores.lookup_failed is False


def test_an_operating_company_keeps_the_sector_floor_and_year_walk():
    years = {
        metric: {"2025": {"median": value, "n": 19}, "2024": {"median": value, "n": 20}}
        for metric, value in INDUSTRY_MEDIANS.items()
    }
    driver = _driver(
        _score(_Lookup(sector=years), sector="Technology", industry=SOFTWARE, ticker="MSFT")[
            M.PILLAR_COST
        ],
        "operating_margin",
    )
    assert driver.period_used == "2024" and driver.sample_size == 20


# ── the peer averages and the stamp ──────────────────────────────────────────

class _PeerFMP:
    """No `symbol` in the profile: `_score_one_ticker` must hand the scorer the ticker itself."""

    def __init__(self, sector: str, industry: str) -> None:
        self.sector, self.industry = sector, industry

    async def get_company_profile(self, ticker):
        return {"sector": self.sector, "industry": self.industry}

    async def get_income_statement(self, *_a):
        return _statements()[0]

    async def get_balance_sheet(self, *_a):
        return _statements()[1]

    async def get_financial_ratios(self, *_a):
        return _statements()[2]

    async def get_earning_call_transcript(self, *_a):
        return ""


class _Upserts:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def table(self, _name):
        return self

    def upsert(self, row, on_conflict=None):
        self.rows.append(row)
        return self

    def execute(self):
        return SimpleNamespace(data=[])


def _recompute(monkeypatch, lookup: Any, industry: str, tickers: List[str], sector: str = FS):
    """The real `compute_for_industry` and `_score_one_ticker`, FMP and the table faked."""
    monkeypatch.setattr(M, "get_moat_scoring_service", lambda: _service(lookup))
    monkeypatch.setattr(
        imb, "_load_universe_industries", lambda: [(industry, [(t, 1e9) for t in tickers])],
    )

    async def _tam(_self, *_a, **_k):
        return _Tam()

    monkeypatch.setattr(imb.IndustryMoatBenchmarkService, "_fetch_industry_tam", _tam)
    svc = imb.IndustryMoatBenchmarkService.__new__(imb.IndustryMoatBenchmarkService)
    svc.fmp, svc.supabase = _PeerFMP(sector, industry), _Upserts()
    stats: Dict[str, int] = {}
    written = asyncio.run(svc.compute_for_industry(industry, stats=stats))
    return written, svc.supabase.rows, stats


def test_a_small_industry_writes_nothing_and_is_no_failure(monkeypatch):
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS, n=5))
    tickers = [f"T{i}" for i in range(8)]
    written, rows, stats = _recompute(monkeypatch, lookup, "Insurance - Reinsurance", tickers)
    assert written == {} and rows == []
    assert stats["scored"] == 8, "the peers were read: they have no median, not no data"
    assert imb._zero_write_failure(0, stats) is None


def test_the_refresh_stamps_a_large_industry_with_no_brand_row(monkeypatch):
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS))
    written, rows, _stats = _recompute(monkeypatch, lookup, BANK, [f"T{i}" for i in range(8)])
    assert M.PILLAR_COST in written and M.PILLAR_BRAND not in written
    assert {row["model_version"] for row in rows} == {M.SCORER_VERSION}


def test_the_refresh_keeps_a_listed_network_on_its_sector_read(monkeypatch):
    lookup = _Lookup(sector=_cells(SECTOR_MEDIANS), industry=_cells(INDUSTRY_MEDIANS))
    _recompute(monkeypatch, lookup, CREDIT, ["V", "MA", "AXP", "COF", "SYF", "ALLY"])
    reads = [call[0] for call in lookup.calls]
    assert reads.count("sector") == 2 and reads.count("industry") == 4


def test_the_vintage_moved():
    assert M.SCORER_VERSION == "moat_v1.2026-11-05"
    assert imb.MODEL_VERSION == M.SCORER_VERSION
    assert imb._MODEL_VERSION_ORDER[-1] == M.SCORER_VERSION
    assert imb._vintage_rank("moat_v1.2026-10-28") == len(imb._MODEL_VERSION_ORDER) - 2


def test_a_paid_reuse_refuses_the_old_vintage():
    stale = {"moat_competition": {"model_version": "moat_v1.2026-10-28"}}
    fresh = {"moat_competition": {"model_version": M.SCORER_VERSION}}
    assert M.report_moat_model_is_current(stale) is False
    assert M.report_moat_model_is_current(fresh) is True


# ── the note is gone ─────────────────────────────────────────────────────────

def test_the_peer_note_left_with_the_comparison_it_described():
    assert not hasattr(C, "_moat_peer_note")
    source = Path(C.__file__).read_text(encoding="utf-8")
    assert "compared with all" not in source


def test_no_report_says_it(monkeypatch):
    report = _report(monkeypatch, _Lookup(industry=_cells(INDUSTRY_MEDIANS)))
    assert "compared with all" not in (report["moat_competition"].get("_coverage_note") or "")


def test_nothing_above_is_a_vacuous_float_comparison():
    """`_vital` must be able to return a real number, or the None asserts prove nothing."""
    lookup = _Lookup(industry=_cells(INDUSTRY_MEDIANS))
    value = _vital(_score(lookup, industry=ASSET_MANAGER, ticker="BLK"))["score"]["value"]
    assert isinstance(value, float) and not math.isnan(value)
