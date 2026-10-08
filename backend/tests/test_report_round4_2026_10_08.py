"""Paid-report fixes from review round 3 of the peer-benchmark change set (2026-10-08).

WORD   (RPT-WORD-1 / IOS-R3-4 / R3-CONTRACT-2 / XC-3) The report's "CARD VALUES (AS
       DISPLAYED TO USER)" block named an INDUSTRY median "sector avg" / "vs sector" (the
       wire name), while the 1.1 card next to the frozen narrative says "industry avg".
FS     (FS-GUARD-1 / RPT3-3 / XC-1) The drill-down drew the Financial Services SECTOR line
       of current ratio, quick ratio and interest coverage for SPGI / ICE / CME (a thin
       industry falls to the sector line) and pinned the sector TTM point onto it — the
       comparison the Health card refuses. Permanent: what is left of that sector for
       these metrics is shells, exchanges and developers.
VITAL  (R3-CARDS-1) An unrated bank Health card (one comparable row) with no Altman Z
       handed the persona health factor to the absolute D/E-band + FCF-sign fallback:
       D/E 2.9 and a negative FCF read 2.5 "critical" in every persona's headline score.
MOAT   (RPT3-5) A partial moat recompute turned the old-vintage pillars' real peer
       averages into the flat 5.0 placeholder until the next full run.

Hermetic: fake benchmark lookups and a fake Supabase read, no network.
"""

from __future__ import annotations

import ast
import inspect
import logging
import textwrap
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services import industry_moat_benchmark_service as imb
from app.utils import supabase_errors
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.persona_scoring import _vital_score, compute_quality_score
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE as CQ

_CUR = str(datetime.now(timezone.utc).year)


# ══════════════════════════════════════════════════════════════════════════════════════
# WORD — the card-values block names an industry median the industry's
# ══════════════════════════════════════════════════════════════════════════════════════


def _metric(name, value, level, key=None, score=None):
    return SnapshotMetricResponse(name=name, value=value, metric_key=key, score=score,
                                  peer_level=level)


def _out(**snaps):
    base = dict(snap_profitability=None, snap_growth=None, snap_valuation=None,
                snap_health=None)
    base.update(snaps)
    return SimpleNamespace(**base)


def test_an_industry_median_is_named_the_industrys_in_the_card_values_block():
    valuation = SnapshotItemResponse(category="Price", rating=3, metrics=[
        _metric("P/E (1.30x sector avg 22.4)", "29.1", "industry", "pe", 2),
        _metric("P/B (0.90x sector avg 4.10)", "3.7", "sector", "pb", 3),
        _metric("Earnings Yield", "3.4%", None, "earnings_yield"),
    ])
    health = SnapshotItemResponse(category="Financial Health", rating=4, metrics=[
        _metric("Debt-to-Equity (vs sector 1.50)", "1.20", "industry", "debt_to_equity", 4),
    ])
    text = C._format_snapshot_card_values(_out(snap_valuation=valuation, snap_health=health))
    assert "  P/E (1.30x industry avg 22.4): 29.1" in text
    assert "  Debt-to-Equity (vs industry 1.50): 1.20" in text
    # A SECTOR median keeps its word; a name with no peer median is untouched.
    assert "  P/B (0.90x sector avg 4.10): 3.7" in text
    assert "  Earnings Yield: 3.4%" in text
    # The industry medians never reach the model as "sector".
    assert "sector avg 22.4" not in text and "vs sector 1.50" not in text


def test_a_metric_without_a_peer_level_keeps_its_wire_name():
    """A snapshot cached before `peer_level` existed (or a SimpleNamespace double)."""
    snap = SimpleNamespace(rating=4, metrics=[SimpleNamespace(name="P/E (1.3x sector avg 22)",
                                                               value="29")])
    text = C._format_snapshot_card_values(_out(snap_valuation=snap))
    assert "  P/E (1.3x sector avg 22): 29" in text


# ══════════════════════════════════════════════════════════════════════════════════════
# FS — no Financial Services SECTOR line for the liquidity / coverage metrics
# ══════════════════════════════════════════════════════════════════════════════════════


def _cell(value, level, n=40):
    return {"value": value, "level": level, "peer_group_name": level, "n": n}


class _Lookup:
    """Series cells per period type; one TTM cell per metric through get_benchmarks."""

    def __init__(self, series, ttm):
        self.series, self.ttm = series, ttm

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        src = self.series.get(period_type, {})
        return {m: {k: dict(v) for k, v in src.get(m, {}).items()} for m in metrics}

    def get_benchmarks(self, industry, sector, metrics, period_type):
        assert period_type == "ttm"
        return {m: ({"TTM": dict(self.ttm[m])} if m in self.ttm else {}) for m in metrics}

    def get_current_benchmarks(self, industry, sector, metrics):
        return {m: dict(self.ttm[m]) for m in metrics if m in self.ttm}


async def _history(monkeypatch, series, ttm, *, industry, sector):
    lookup = _Lookup(series, ttm)
    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: lookup)
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    return await coll._fetch_sector_benchmark_history(industry, sector)


_GATED = ("current_ratio", "quick_ratio", "interest_coverage")
_EXCHANGES = "Financial - Data & Stock Exchanges"   # SPGI, ICE, CME, MCO: n≈10, not gated


def _sector_lines(level="sector"):
    """What `get_benchmark_series` answers for a thin FS industry: the SECTOR's lines."""
    annual = {m: {"2024": _cell(1.3, level, 400), "2025": _cell(1.4, level, 400)}
              for m in (*_GATED, "debt_to_equity")}
    quarterly = {m: {"Q2'26": _cell(1.2, level, 400)} for m in (*_GATED, "debt_to_equity")}
    return {"annual": annual, CQ: quarterly}


def _mature_sector_ttm():
    return {m: _cell(1.5, "sector", n=380) for m in (*_GATED, "debt_to_equity")}


@pytest.mark.asyncio
@pytest.mark.parametrize("sector", ["Financial Services", "Financials"])
async def test_a_kept_fs_company_gets_no_sector_liquidity_or_coverage_line(monkeypatch, caplog,
                                                                           sector):
    with caplog.at_level(logging.INFO, logger=C.logger.name):
        hist = await _history(monkeypatch, _sector_lines(), _mature_sector_ttm(),
                              industry=_EXCHANGES, sector=sector)
    for metric in _GATED:
        for granularity in ("annual", "quarterly"):
            assert metric not in hist[granularity], (metric, granularity)
            assert metric not in hist["levels"][granularity], (metric, granularity)
    # D/E is the company's honest comparison: its sector line stays, with the TTM point.
    assert hist["annual"]["debt_to_equity"] == {"2024": 1.3, "2025": 1.4, _CUR: 1.5}
    assert hist["levels"]["annual"]["debt_to_equity"] == "sector"
    assert hist["quarterly"]["debt_to_equity"] == {"Q2'26": 1.2}
    gate_logs = [r.getMessage() for r in caplog.records if "peer_line_gate" in r.getMessage()]
    assert len(gate_logs) == 1 and "annual:current_ratio[financial_services_sector]" in gate_logs[0]
    assert "ttm:interest_coverage[financial_services_sector]" in gate_logs[0]


@pytest.mark.asyncio
async def test_an_empty_line_never_takes_the_fs_sector_ttm_point(monkeypatch):
    """Without the guard, `_ttm_point_fits_line` lets a mature TTM point become an empty
    line's WHOLE line — the bank-pooled sector median, back by another door."""
    hist = await _history(monkeypatch, {"annual": {}, CQ: {}}, _mature_sector_ttm(),
                          industry=_EXCHANGES, sector="Financial Services")
    for metric in _GATED:
        assert metric not in hist["annual"] and metric not in hist["levels"]["annual"]
    assert hist["annual"]["debt_to_equity"] == {_CUR: 1.5}


@pytest.mark.asyncio
async def test_an_fs_line_of_unknown_level_is_withheld(monkeypatch):
    """Cells that do not name one level could be the sector's: fail closed."""
    series = {"annual": {"current_ratio": {"2025": {"value": 1.3, "n": 400}}}, CQ: {}}
    hist = await _history(monkeypatch, series, {}, industry=_EXCHANGES,
                          sector="Financial Services")
    assert "current_ratio" not in hist["annual"]


@pytest.mark.asyncio
async def test_the_industrys_own_line_is_kept_for_a_kept_fs_industry(monkeypatch):
    series = {"annual": {"interest_coverage": {"2024": _cell(18.0, "industry", 22),
                                               "2025": _cell(19.0, "industry", 22)}}, CQ: {}}
    ttm = {"interest_coverage": _cell(20.0, "industry", 22)}
    hist = await _history(monkeypatch, series, ttm, industry=_EXCHANGES,
                          sector="Financial Services")
    assert hist["annual"]["interest_coverage"] == {"2024": 18.0, "2025": 19.0, _CUR: 20.0}
    assert hist["levels"]["annual"]["interest_coverage"] == "industry"


@pytest.mark.asyncio
async def test_a_dropped_sector_line_may_take_the_industrys_own_ttm_point(monkeypatch):
    """The withheld line is the SECTOR's; a mature INDUSTRY TTM median is the company's own
    peers (the Health card compares against it too), so it may stand as the line."""
    ttm = {"current_ratio": _cell(1.1, "industry", 21)}
    hist = await _history(monkeypatch, _sector_lines(), ttm, industry=_EXCHANGES,
                          sector="Financial Services")
    assert hist["annual"]["current_ratio"] == {_CUR: 1.1}
    assert hist["levels"]["annual"]["current_ratio"] == "industry"


@pytest.mark.asyncio
@pytest.mark.parametrize("industry,withheld,kept", [
    # A bank: all three are meaningless for its own industry, even its industry's median.
    ("Banks - Regional", set(_GATED), {"debt_to_equity"}),
    ("Banks—Regional", set(_GATED), {"debt_to_equity"}),          # older feed's em dash
    # An insurance broker keeps interest coverage (a fee business that borrows).
    ("Insurance - Brokers", {"current_ratio", "quick_ratio"},
     {"interest_coverage", "debt_to_equity"}),
])
async def test_a_gated_industry_gets_no_line_for_a_metric_it_omits(monkeypatch, industry,
                                                                    withheld, kept):
    hist = await _history(monkeypatch, _sector_lines(level="industry"), {}, industry=industry,
                          sector="Financial Services")
    assert withheld.isdisjoint(hist["annual"]) and withheld.isdisjoint(hist["quarterly"])
    assert kept <= set(hist["annual"])


@pytest.mark.asyncio
@pytest.mark.parametrize("sector,industry", [("Technology", "Software - Infrastructure"),
                                             ("Real Estate", "REIT - Retail")])
async def test_every_other_sector_keeps_its_sector_lines(monkeypatch, sector, industry):
    hist = await _history(monkeypatch, _sector_lines(), _mature_sector_ttm(),
                          industry=industry, sector=sector)
    for metric in _GATED:
        assert hist["annual"][metric] == {"2024": 1.3, "2025": 1.4, _CUR: 1.5}
        assert hist["levels"]["annual"][metric] == "sector"


@pytest.mark.asyncio
async def test_the_spgi_health_card_row_has_no_sector_overlay(monkeypatch):
    """End to end: fetch → `_build_fundamentals_history` → `_snapshot_to_card`."""
    out = C.CollectedTickerData(ticker="SPGI", persona_key="warren_buffett")
    out.profile = {"sector": "Financial Services", "industry": _EXCHANGES}
    out.income = [{"calendarYear": y, "date": f"{y}-12-31", "revenue": 1.4e10}
                  for y in ("2025", "2024")]
    out.ratios = [{"calendarYear": y, "date": f"{y}-12-31", "currentRatio": 0.9,
                   "debtToEquityRatio": 1.2} for y in ("2025", "2024")]
    out.sector_benchmark_history = await _history(
        monkeypatch, _sector_lines(), {}, industry=_EXCHANGES, sector="Financial Services")
    hist = C._build_fundamentals_history(out)
    assert "current_ratio" in hist and "debt_to_equity" in hist, sorted(hist)
    assert "sector_annual" not in hist["current_ratio"]
    assert hist["debt_to_equity"].get("sector_annual"), "the D/E overlay must survive"

    snap = SnapshotItemResponse(category="Financial Health", rating=0, metrics=[
        _metric("Debt-to-Equity (vs sector 1.50)", "1.20", "sector", "debt_to_equity", 3),
        _metric("Current Ratio", "0.90", None, "current_ratio"),
    ])
    card = C._snapshot_to_card("Health", snap, history_lookup=hist, peer_group_level="sector")
    rows = {m["label"]: m for m in card["metrics"]}
    assert "sector_annual_history" not in rows["Current Ratio"]
    assert "sector_annual_level" not in rows["Current Ratio"]
    assert rows["Debt-to-Equity (vs sector 1.50)"]["sector_annual_level"] == "sector"


@pytest.mark.parametrize("metric,level,industry,sector,expected", [
    ("current_ratio", "sector", _EXCHANGES, "Financial Services", "financial_services_sector"),
    ("current_ratio", None, _EXCHANGES, "Financial Services", "financial_services_sector"),
    ("current_ratio", "industry", _EXCHANGES, "Financial Services", None),
    ("debt_to_equity", "sector", _EXCHANGES, "Financial Services", None),
    ("pe_ratio", "sector", "Banks - Regional", "Financial Services", None),
    ("interest_coverage", "industry", "Banks - Regional", "Financial Services", "not_applicable"),
    ("current_ratio", "sector", "Software - Infrastructure", "Technology", None),
    ("current_ratio", "sector", None, None, None),                 # unknown: kept
    ("quick_ratio", "sector", "", "Financial Services", "financial_services_sector"),
])
def test_withheld_reason_table(metric, level, industry, sector, expected):
    assert C._withheld_peer_line_reason(metric, level, industry, sector) == expected


# ══════════════════════════════════════════════════════════════════════════════════════
# VITAL — an unrated bank card with no Altman Z leaves the health factor UNMEASURED
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("industry", ["Banks - Diversified", "Banks—Regional",
                                      "Insurance - Life", "Financial - Credit Services",
                                      "Financial - Capital Markets", "REIT - Mortgage"])
def test_the_real_bank_shape_is_unmeasured(industry):
    """Gated industry, Altman Z refused (None), D/E 2.9, FCF < 0, card unrated."""
    vital = C._build_health_vital(None, 2.9, True, card_weighted=None, industry=industry)
    assert vital["score"] == {"value": None, "status": "unmeasured"}
    assert _vital_score({"financial_health": vital}, "financial_health") is None


@pytest.mark.parametrize("industry", ["REIT - Retail", _EXCHANGES, None, ""])
def test_a_non_gated_twin_still_scores_on_the_absolute_fallback(industry):
    """Same inputs, an industry whose liquidity ratios mean something (or an unknown
    one): the documented fallback, 5.0 − 1.5 (D/E > 2.5) − 1.0 (FCF < 0)."""
    vital = C._build_health_vital(None, 2.9, True, card_weighted=None, industry=industry)
    assert vital["score"] == {"value": 2.5, "status": "critical"}


def test_a_rated_bank_card_or_a_z_score_still_scores():
    """Only the no-card AND no-Z case drops out: a rated card is the peer comparison."""
    rated = C._build_health_vital(None, 2.9, True, card_weighted=4.2, industry="Banks - Regional")
    assert rated["score"]["value"] == 8.0
    with_z = C._build_health_vital(3.5, 0.4, False, card_weighted=None,
                                   industry="Banks - Regional")
    assert with_z["score"]["value"] is not None and with_z["level"] == "strong"


def test_the_unmeasured_health_factor_renormalizes_out_of_the_headline():
    bank = C._build_health_vital(None, 2.9, True, card_weighted=None,
                                 industry="Banks - Diversified")
    fallback = C._build_health_vital(None, 2.9, True, card_weighted=None, industry="REIT - Retail")
    base = {"valuation": {"score": {"value": 7.0, "status": "good"}}}
    only_valuation = compute_quality_score("warren_buffett", {"_scoring_inputs": base},
                                           signals={})
    with_bank = compute_quality_score(
        "warren_buffett", {"_scoring_inputs": dict(base, financial_health=bank)}, signals={})
    with_fallback = compute_quality_score(
        "warren_buffett", {"_scoring_inputs": dict(base, financial_health=fallback)},
        signals={})
    assert with_bank == only_valuation
    assert with_fallback < only_valuation, "the twin's 2.5 must still vote"


def _build_sections_source() -> ast.AST:
    src = textwrap.dedent(inspect.getsource(C.TickerReportDataCollector._build_sections))
    return ast.parse(src)


def test_the_report_passes_the_companys_industry_to_the_health_vital():
    """The builder's gate is inert unless its one call site hands it the industry."""
    calls = [
        node for node in ast.walk(_build_sections_source())
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_build_health_vital"
    ]
    assert len(calls) == 1
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert "industry" in kw and "industry" in kw["industry"] and "profile" in kw["industry"]


def _collected(profile: Dict[str, Any], *, de: float, fcf: float, snap_health):
    """A minimal collection the real `_compute_metrics` + `_build_sections` accept."""
    out = C.CollectedTickerData(ticker="C", persona_key="warren_buffett")
    out.profile = dict({"companyName": "X", "exchangeShortName": "NYSE", "mktCap": 1.4e11},
                       **profile)
    out.quote = {"price": 70.0}
    out.income = [{"calendarYear": 2025, "revenue": 8.0e10, "netIncome": 1.2e10,
                   "operatingIncome": 1.6e10},
                  {"calendarYear": 2024, "revenue": 7.8e10, "netIncome": 1.0e10,
                   "operatingIncome": 1.4e10}]
    out.balance = [{"totalAssets": 2.4e12, "totalLiabilities": 2.2e12,
                    "totalCurrentAssets": 1.0e12, "totalCurrentLiabilities": 1.9e12,
                    "retainedEarnings": 2.0e11, "totalDebt": 5.8e11,
                    "cashAndCashEquivalents": 3.0e11}]
    out.cash_flow = [{"freeCashFlow": fcf, "operatingCashFlow": fcf + 1e9}]
    out.ratios = [{"debtEquityRatio": de, "priceEarningsRatio": 11.0}]
    out.historical = {"historical": [{"date": f"2026-09-{d:02d}", "close": 70.0}
                                     for d in range(1, 21)]}
    out.snap_health = snap_health
    return out


def _unrated_bank_card() -> SnapshotItemResponse:
    return SnapshotItemResponse(category="Financial Health", rating=0, weighted_score=None,
                                metrics=[_metric("Debt-to-Equity (vs sector 1.50)", "2.90",
                                                 "industry", "debt_to_equity", 4)])


@pytest.mark.parametrize("profile,expected", [
    ({"sector": "Financial Services", "industry": "Banks - Diversified"}, None),
    ({"sector": "Real Estate", "industry": "REIT - Retail"}, 2.5),
])
def test_the_collector_builds_the_bank_vital_unmeasured_and_the_twin_scored(monkeypatch, profile,
                                                                        expected):
    """Through the real `_compute_metrics` → `_build_sections` call site: Altman Z is
    refused for both sectors, the card is unrated, D/E 2.9, FCF < 0."""
    # `_build_sections` reads the card-wide peer level through a lazy import of the lookup.
    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: _Lookup({}, {}))
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    out = _collected(profile, de=2.9, fcf=-4.0e9, snap_health=_unrated_bank_card())
    coll._compute_metrics(out)
    assert out.computed["altman_z"] is None and out.computed["fcf_negative"] is True
    assert out.computed["debt_equity"] == 2.9
    coll._build_sections(out)
    assert out.financial_health_vital["score"]["value"] == expected


# ══════════════════════════════════════════════════════════════════════════════════════
# MOAT — a pillar with no current-vintage row keeps its old-vintage peer average
# ══════════════════════════════════════════════════════════════════════════════════════


class _ReadSB:
    def __init__(self, answers):
        self.answers = list(answers)

    def table(self, _name):
        sb = self

        class _Q:
            def select(self, *_a, **_k):
                return self

            def eq(self, *_a):
                return self

            def execute(self):
                return SimpleNamespace(data=sb.answers.pop(0))

        return _Q()


@pytest.fixture
def industry():
    name = f"Test Industry {uuid.uuid4().hex[:8]}"
    yield name
    imb._lookup_cache.pop(name, None)


def _read(monkeypatch, rows, industry):
    monkeypatch.setattr(supabase_errors.time, "sleep", lambda _s: None)
    lk = imb.IndustryMoatBenchmarkLookup.__new__(imb.IndustryMoatBenchmarkLookup)
    lk.supabase = _ReadSB([rows])
    return lk.get_pillar_benchmarks(industry)


_V = imb.MODEL_VERSION
_OLD = "moat_v1.2026-05"


def _rows(version, scores) -> List[Dict[str, Any]]:
    return [{"pillar_name": p, "peer_average_score": s, "model_version": version}
            for p, s in scores.items()]


def test_a_partial_recompute_keeps_the_old_pillars_real_averages(monkeypatch, caplog, industry):
    rows = _rows(_V, {"Brand": 6.0, "Cost": 4.0}) + _rows(_OLD, {"Network": 9.0,
                                                                  "Switching": 3.5})
    with caplog.at_level(logging.INFO, logger=imb.logger.name):
        out = _read(monkeypatch, rows, industry)
    assert out == {"Brand": 6.0, "Cost": 4.0, "Network": 9.0, "Switching": 3.5}
    pending = [r.getMessage() for r in caplog.records if "recompute pending" in r.getMessage()]
    assert len(pending) == 1 and industry in pending[0]
    assert f"Network={_OLD!r}" in pending[0] and f"Switching={_OLD!r}" in pending[0]
    assert "Brand=" not in pending[0] and "2 of 4" in pending[0]


def test_a_current_industry_logs_nothing_pending(monkeypatch, caplog, industry):
    with caplog.at_level(logging.INFO, logger=imb.logger.name):
        out = _read(monkeypatch, _rows(_V, {"Brand": 6.0}), industry)
    assert out == {"Brand": 6.0}
    assert not [r for r in caplog.records if "recompute pending" in r.getMessage()]


@pytest.mark.parametrize("order", ["old_first", "new_first"])
def test_a_duplicate_pillar_resolves_to_its_newest_vintage(monkeypatch, industry, order):
    old, new = _rows(_OLD, {"Brand": 9.0}), _rows(_V, {"Brand": 6.0})
    rows = old + new if order == "old_first" else new + old
    assert _read(monkeypatch, rows, industry) == {"Brand": 6.0}


def test_an_unknown_stamp_is_served_as_pending_never_dropped(monkeypatch, caplog, industry):
    rows = _rows(None, {"Brand": 5.5}) + _rows(_V, {"Cost": 4.0})
    with caplog.at_level(logging.INFO, logger=imb.logger.name):
        out = _read(monkeypatch, rows, industry)
    assert out == {"Brand": 5.5, "Cost": 4.0}
    assert any("Brand=None" in r.getMessage() for r in caplog.records)


def test_malformed_rows_are_dropped_not_raised(monkeypatch, industry):
    rows = (_rows(_V, {"Brand": 6.0}) + ["junk", None]
            + [{"pillar_name": ["Network"], "peer_average_score": 3.0, "model_version": _V},
               {"pillar_name": "", "peer_average_score": 3.0, "model_version": _OLD},
               {"pillar_name": "Cost", "peer_average_score": float("nan"), "model_version": _V}])
    assert _read(monkeypatch, rows, industry) == {"Brand": 6.0}
