"""Snapshot cards + Health Check, review round 2 (2026-10-07).

HC-1  The financials gate is ONE module (`financials_metric_gate`) for the Health Check, the
      Financial Health snapshot (both paths) and the Overview fallback card — and insurance
      brokers (MMC, AON, AJG) KEEP interest coverage: a fee business whose lenders watch it.
      Only their current / quick ratio are distorted (fiduciary funds).
HC-2  A current / quick ratio / interest-coverage median that is the Financial Services
      SECTOR's pools bank and insurer values; a kept FS company whose industry is too thin
      for a mature cell ("Financial - Data & Stock Exchanges", n≈10) fell to it. Never a
      comparison — PERMANENT since round 3 (R3-CARDS-5): once the producer rebuilds the
      aggregate without the gated industries it is shells, exchanges and developers, still
      not a peer group (tests/test_cards_round4_2026_10_08.py pins that the guard stays).
SNAP-1 A Profitability card that SCORES nothing (only fiscal-year fallback margins, an
      "N/M" ROE) was persisted 24 h as a neutral "3/5" and labelled "In Line With Industry"
      in the paid report. It is rated 0, flagged "no_values", never persisted, left out of
      the report.
HC-4  The Overview's degraded Profitability card read ROE from ``km["roe"]`` (a v3 name
      /stable never sends), so it ALWAYS rated 1/5, and had no negative-equity test. It
      shows the values (``returnOnEquity``, "N/M" on negative equity) and rates nothing.
HC-3 / F1 A Financial Health card with fewer than two scored rows — every bank, insurer,
      lender — read Solid / Moderate / Soft on Debt-to-Equity alone. It is rated 0.

Hermetic: stubbed FMP, Profit Power, Health Check, Supabase and benchmark rows.
"""

from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import test_health_check_deepcheck as hcd
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.schemas.health_check import HealthCheckResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.services import financials_metric_gate as gate
from app.services import health_check_service as hc
from app.services import profitability_snapshot_service as ps
from app.services import sector_benchmark_lookup as sbl
from app.services.stock_overview_service import StockOverviewService

_GATED = {"interest_coverage", "current_ratio", "quick_ratio"}


# ── shared harness ───────────────────────────────────────────────────────────────────


class _RecordingLookup:
    """`get_current_benchmarks` from {metric: (value, level)}; records what was ASKED."""

    def __init__(self, table: Dict[str, tuple]) -> None:
        self.table = table
        self.asked: List[List[str]] = []

    def get_current_benchmarks(self, industry, sector, metrics):
        self.asked.append(list(metrics))
        return {
            m: ({"value": self.table[m][0], "level": self.table[m][1],
                 "peer_group_name": "Peers", "n": 60} if m in self.table else None)
            for m in metrics
        }


async def _hc_build(monkeypatch, answers, lookup) -> HealthCheckResponse:
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: lookup)
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = hcd._FakeFMP(answers)
    response, _next = await svc._build_health_check("TEST")
    return response


def _by_type(resp: HealthCheckResponse) -> Dict[str, Any]:
    return {m.type: m for m in resp.metrics}


def _counts_consistent(resp: HealthCheckResponse) -> None:
    passed = sum(1 for m in resp.metrics if m.status == "positive")
    neutrals = sum(1 for m in resp.metrics if m.status == "neutral")
    assert resp.passed_count == passed
    assert resp.total_count == len(resp.metrics)
    assert resp.overall_rating == hc._overall_rating(passed + 0.5 * neutrals, len(resp.metrics))


def _profile(sector: str, industry: Any, symbol: str = "TEST") -> Dict[str, Any]:
    return dict(hcd._PROFILE, symbol=symbol, sector=sector, industry=industry)


def _overview() -> StockOverviewService:
    return StockOverviewService.__new__(StockOverviewService)


# ══════════════════════════════════════════════════════════════════════════════════════
# HC-1 — one gate; insurance brokers keep interest coverage
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("industry,omitted", [
    ("Banks - Diversified", _GATED),
    ("Financial - Credit Services", _GATED),
    ("Financial - Capital Markets", _GATED),
    ("Insurance - Life", _GATED),
    ("Insurance - Brokers", {"current_ratio", "quick_ratio"}),
    ("insurance—brokers", {"current_ratio", "quick_ratio"}),     # spelling drift
    ("Financial - Data & Stock Exchanges", set()),
    ("REIT - Retail", set()),
    ("Software - Infrastructure", set()),
    ("", set()), (None, set()), (7, set()),
])
def test_omitted_financial_rows(industry, omitted):
    assert hc.omitted_financial_rows(industry) == frozenset(omitted)
    for metric in _GATED:
        assert gate.peer_metric_applicable(metric, industry) is (metric not in omitted)


_BROKER_RATIOS = dict(hcd._RATIOS, currentRatioTTM=1.05, quickRatioTTM=1.0,
                      interestCoverageRatioTTM=2.0, debtToEquityRatioTTM=1.4)


@pytest.mark.asyncio
async def test_an_aon_shaped_broker_keeps_interest_coverage_and_loses_current_and_quick(
    monkeypatch,
):
    """AON / AJG: acquisition debt, coverage 2.0x against an 8.0x industry median — the
    red debt-service row the first gate dropped. Current / quick ratio stay omitted."""
    lookup = _RecordingLookup({"debt_to_equity": (1.2, "industry"), "pe_ratio": (25.0, "industry"),
                               "roe": (0.2, "industry"), "interest_coverage": (8.0, "industry"),
                               "current_ratio": (1.1, "industry"), "quick_ratio": (1.0, "industry")})
    resp = await _hc_build(monkeypatch, hcd._answers(
        profile=_profile("Financial Services", "Insurance - Brokers", "AON"),
        ratios=[dict(_BROKER_RATIOS)],
    ), lookup)
    by = _by_type(resp)
    assert "interest_coverage" in by, "a broker's coverage row is the one its lenders watch"
    assert "current_ratio" not in by and "quick_ratio" not in by
    ic = by["interest_coverage"]
    assert ic.status == "negative" and ic.comparison_value == 8.0 and ic.peer_level == "industry"
    assert "altman_z_score" not in by, "the Altman gate (whole FS sector) is unchanged"
    assert resp.degraded == []
    _counts_consistent(resp)
    assert resp.total_count == 4            # D/E, P/E, ROE, IC
    # The lookup is asked for coverage, never for the rows that are not shown.
    assert lookup.asked and all("interest_coverage" in a for a in lookup.asked)
    assert all(not ({"current_ratio", "quick_ratio"} & set(a)) for a in lookup.asked)


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", ["Banks - Regional", "Financial - Credit Services",
                                      "Insurance - Property & Casualty"])
async def test_banks_lenders_and_insurers_still_omit_all_three(monkeypatch, industry):
    lookup = _RecordingLookup({m: (v, "industry") for m, v in hcd._BENCH.items()})
    resp = await _hc_build(monkeypatch, hcd._answers(
        profile=_profile("Financial Services", industry), ratios=[dict(_BROKER_RATIOS)],
    ), lookup)
    assert not (set(_by_type(resp)) & _GATED)
    assert all(not (set(a) & _GATED) for a in lookup.asked)


@pytest.mark.asyncio
async def test_health_snapshot_fallback_follows_the_broker_gate(monkeypatch):
    broker = {"symbol": "AON", "sector": "Financial Services", "industry": "Insurance - Brokers",
              "mktCap": 7e10}
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"), profile=broker,
        bench={"debt_to_equity": (1.0, "industry"), "interest_coverage": (8.0, "industry")},
    )
    snap, _ = await svc._compute_with_status("AON")
    keys = [m.metric_key for m in snap.metrics]
    assert keys == ["debt_to_equity", "interest_coverage"], keys


def _ov_health(industry: str, sector: str = "Financial Services") -> List[str]:
    card = _overview()._build_health_snapshot(
        dict(sph._HC_BS, totalDebt=20.0), {"revenue": 50.0, "operatingIncome": 6.0},
        {"freeCashFlow": 5.0}, {"interestCoverage": 3.1, "assetTurnover": 0.4}, {}, 1e9,
        sector=sector, industry=industry,
    )
    return [m.name for m in card.metrics]


def test_the_overview_fallback_health_card_follows_the_broker_gate():
    assert "Interest Coverage" in _ov_health("Insurance - Brokers")
    assert "Interest Coverage" not in _ov_health("Banks - Diversified")
    assert "Interest Coverage" in _ov_health("Software - Application", "Technology")


# ══════════════════════════════════════════════════════════════════════════════════════
# HC-2 — the bank-pooled Financial Services sector median is no comparison
# ══════════════════════════════════════════════════════════════════════════════════════


class _RowLookup(sbl.SectorBenchmarkLookup):
    """The REAL lookup (merge and current-benchmark picker) over in-memory rows:
    {("industry" | "sector", period_type): {metric: (median, n)}}."""

    def __init__(self, rows: Dict[tuple, Dict[str, tuple]]) -> None:
        self.rows = rows
        self.supabase = None

    def _fetch_rows(self, columns, sector, metrics, period_type, industry=""):
        layer = "industry" if industry else "sector"
        table = self.rows.get((layer, period_type), {})
        return [
            {"metric_name": m, "period_label": "TTM" if period_type == sbl.TTM_PERIOD_TYPE
             else "2025", "median_value": v, "sample_size": n}
            for m, (v, n) in table.items() if m in metrics
        ]


# SPGI-shaped: an exchange / data vendor with an operating balance sheet.
_EXCHANGE_RATIOS = dict(hcd._RATIOS, currentRatioTTM=0.9, quickRatioTTM=0.85,
                        interestCoverageRatioTTM=20.0, debtToEquityRatioTTM=1.3)
# The FS sector TTM aggregate (n≈2,000, ~95% banks / insurers / lenders): bank-shaped
# "current" ratios and funding-cost coverage.
_FS_SECTOR_TTM = {"current_ratio": (0.5, 2000), "quick_ratio": (0.4, 2000),
                  "interest_coverage": (1.2, 2000), "debt_to_equity": (1.0, 2000),
                  "pe_ratio": (14.0, 2000), "roe": (0.10, 2000)}


def _exchange_rows(industry_n: int) -> Dict[tuple, Dict[str, tuple]]:
    industry = {m: (v * 3, industry_n) for m, (v, _n) in _FS_SECTOR_TTM.items()}
    return {("sector", sbl.TTM_PERIOD_TYPE): dict(_FS_SECTOR_TTM),
            ("industry", sbl.TTM_PERIOD_TYPE): industry}


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_sector", ["Financial Services", "Financials"])
async def test_a_thin_exchange_industry_is_not_compared_with_the_bank_pooled_sector(
    monkeypatch, raw_sector,
):
    monkeypatch.setattr(sbl, "_cache", {})
    lookup = _RowLookup(_exchange_rows(industry_n=10))
    # Precondition: the real picker hands back the SECTOR cell for every metric.
    cells = lookup.get_current_benchmarks(
        "Financial - Data & Stock Exchanges", "Financial Services", sorted(_FS_SECTOR_TTM))
    assert all(c["level"] == "sector" and c["n"] == 2000 for c in cells.values()), cells

    monkeypatch.setattr(sbl, "_cache", {})
    resp = await _hc_build(monkeypatch, hcd._answers(
        profile=_profile(raw_sector, "Financial - Data & Stock Exchanges", "SPGI"),
        ratios=[dict(_EXCHANGE_RATIOS)],
    ), lookup)
    by = _by_type(resp)
    assert _GATED <= set(by), "an exchange keeps the three rows"
    for kind in _GATED:
        m = by[kind]
        assert m.comparison_value is None and m.percent_difference is None, kind
        assert m.peer_level is None, kind
        assert m.status == hc._absolute_status(kind, m.value), kind
        assert "average" not in hcd._rendered(m), (kind, hcd._rendered(m))
    # Not a gated metric: D/E and P/E still use the sector median.
    assert by["debt_to_equity"].comparison_value == 1.0
    assert by["debt_to_equity"].peer_level == "sector"
    assert by["pe_ratio"].peer_level == "sector"
    assert resp.degraded == []
    _counts_consistent(resp)
    json.dumps(resp.model_dump(), allow_nan=False)


@pytest.mark.asyncio
async def test_a_mature_exchange_industry_is_still_compared(monkeypatch):
    """Control: the company's OWN industry median is kept (it holds no banks)."""
    monkeypatch.setattr(sbl, "_cache", {})
    resp = await _hc_build(monkeypatch, hcd._answers(
        profile=_profile("Financial Services", "Financial - Data & Stock Exchanges", "SPGI"),
        ratios=[dict(_EXCHANGE_RATIOS)],
    ), _RowLookup(_exchange_rows(industry_n=25)))
    by = _by_type(resp)
    for kind in _GATED:
        assert by[kind].peer_level == "industry", kind
        assert by[kind].comparison_value is not None, kind


@pytest.mark.asyncio
async def test_another_sectors_sector_median_is_still_compared(monkeypatch):
    """Control: only the Financial Services aggregate pools banks."""
    lookup = _RecordingLookup({m: (v, "sector") for m, v in hcd._BENCH.items()})
    resp = await _hc_build(monkeypatch, hcd._answers(), lookup)
    for kind in _GATED:
        assert _by_type(resp)[kind].peer_level == "sector", kind


@pytest.mark.parametrize("metric,level,sector,refused", [
    ("current_ratio", "sector", "Financial Services", True),
    ("quick_ratio", "sector", "Financials", True),               # FMP's other spelling
    ("interest_coverage", "sector", " Financial Services ", True),
    ("interest_coverage", "industry", "Financial Services", False),   # own peers
    ("debt_to_equity", "sector", "Financial Services", False),        # not a gated metric
    ("pe_ratio", "sector", "Financial Services", False),
    ("current_ratio", "sector", "Real Estate", False),
    ("current_ratio", "sector", "Technology", False),
    ("current_ratio", None, "Financial Services", False),             # unknown level
    ("current_ratio", "sector", None, False),
    ("current_ratio", "sector", 42, False),
])
def test_bank_pooled_sector_cell(metric, level, sector, refused):
    assert hc._bank_pooled_sector_cell(metric, level, sector) is refused


@pytest.mark.asyncio
async def test_the_health_snapshot_fallback_refuses_the_bank_pooled_sector_cell(monkeypatch):
    exchange = {"symbol": "SPGI", "sector": "Financial Services",
                "industry": "Financial - Data & Stock Exchanges", "mktCap": 1.5e11}
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"), profile=exchange,
        bench={"debt_to_equity": (1.0, "sector"), "current_ratio": (0.5, "sector"),
               "interest_coverage": (1.2, "sector"), "quick_ratio": (0.4, "sector")},
    )
    snap, degraded = await svc._compute_with_status("SPGI")
    by = {m.metric_key: m for m in snap.metrics}
    for key in _GATED:
        assert by[key].value != "—", f"control: {key} is computed"
        assert "sector" not in by[key].name and by[key].peer_level is None, by[key]
        assert by[key].score is None, f"{key} was scored against a bank-pooled median"
    assert by["debt_to_equity"].name == "Debt-to-Equity (vs sector 1.00)"
    assert by["debt_to_equity"].peer_level == "sector"
    assert degraded == ["health_check"]


# ══════════════════════════════════════════════════════════════════════════════════════
# SNAP-1 — a Profitability card that scores nothing is not a "3/5"
# ══════════════════════════════════════════════════════════════════════════════════════


def _spy_persist(monkeypatch, svc) -> List[str]:
    """Run the fire-and-forget 24h-tier write inline so the assertion cannot race it."""
    written: List[str] = []

    def _record(ticker, *_rest):
        written.append(ticker)

    monkeypatch.setattr(svc, "_upsert_supabase_cache", _record)
    loop = asyncio.get_running_loop()
    real = loop.run_in_executor

    def _run(executor, fn, *args):
        if fn is _record:
            fn(*args)
            done = loop.create_future()
            done.set_result(None)
            return done
        return real(executor, fn, *args)

    monkeypatch.setattr(loop, "run_in_executor", _run)
    return written


def _fresh_prof_tiers(monkeypatch, svc) -> None:
    monkeypatch.setattr(ps, "_cache", {})
    monkeypatch.setattr(ps, "_inflight", {})
    monkeypatch.setattr(ps, "_degraded_by_key", {})
    monkeypatch.setattr(ps, "_fund_shape_by_key", {})
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)


@pytest.mark.asyncio
async def test_fiscal_year_only_margins_rate_nothing_and_are_never_persisted(monkeypatch):
    """Both TTM legs answer empty (an annual-only filer, a recent IPO); Profit Power has
    the fiscal year. The margins are SHOWN, but nothing is scored — the old build rated it
    a neutral 3/5, cached it 24 h, and the report read "In Line With Industry"."""
    svc = sph._prof(monkeypatch, ratios=[], km=[])
    _fresh_prof_tiers(monkeypatch, svc)
    written = _spy_persist(monkeypatch, svc)

    snap, degraded = await svc.get_profitability_snapshot_with_status("MCD")
    shown = {m.metric_key: m.value for m in snap.metrics}
    assert (shown["gross_margin"], shown["operating_margin"], shown["net_margin"]) == (
        "55.00%", "44.00%", "30.00%")
    assert all(m.score is None for m in snap.metrics)
    assert snap.rating == 0 and snap.weighted_score is None, "a made-up neutral verdict"
    assert degraded == ["no_values"]
    assert written == [], "an unrated card was pinned in the 24 h tier"
    # Company state, not an outage: kept in MEMORY for the normal 5 min (no FMP storm),
    # and a Tier-1 hit reports the same status.
    svc.fmp.calls.clear()
    snap2, degraded2 = await svc.get_profitability_snapshot_with_status("MCD")
    assert snap2 is snap and degraded2 == ["no_values"] and svc.fmp.calls == []


@pytest.mark.asyncio
async def test_the_report_leaves_the_unrated_profitability_card_out(monkeypatch):
    from app.services.agents.card_verdict import generate_card_verdict
    from app.services.agents.ticker_report_data_collector import (
        _settle_snapshot_result,
        _snapshot_to_card,
    )

    svc = sph._prof(monkeypatch, ratios=[], km=[])
    _fresh_prof_tiers(monkeypatch, svc)
    _spy_persist(monkeypatch, svc)
    result = await svc.get_profitability_snapshot_with_status("MCD")

    out = SimpleNamespace(degraded_sections=[], snap_profitability="unset")
    _settle_snapshot_result(out, "snap_profitability", result, "MCD")
    assert out.snap_profitability is None, "an unrated card reached the report"
    assert out.degraded_sections == [], "company state must keep the report cacheable"
    card = _snapshot_to_card("Profitability", out.snap_profitability,
                             peer_group_level="industry")
    assert card["quality_label"] != "In Line With Industry"
    assert card["quality_label"] == "Data unavailable" and card["star_rating"] == 0
    # What the OLD build fed the verdict (rating 3, nothing scored):
    assert generate_card_verdict("Profitability", 3, "industry",
                                 [(m.metric_key, None) for m in result[0].metrics])[0] == (
        "In Line With Industry")


@pytest.mark.asyncio
async def test_nm_roe_with_refused_ttm_margins_and_no_roa_rates_nothing(monkeypatch):
    """The review's second shape: negative equity (ROE "N/M"), no ROA, and every TTM
    margin refused (no TTM revenue per share) — fiscal-year margins only."""
    svc = sph._prof(monkeypatch, ratios=[{"debtToEquityRatioTTM": -5.0}],
                    km=[{"returnOnEquityTTM": -2.0}], bs=[{"totalStockholdersEquity": -1e9}])
    snap, degraded = await svc._compute_with_status("MCD")
    assert next(m for m in snap.metrics if m.metric_key == "roe").value == "N/M"
    assert all(m.score is None for m in snap.metrics)
    assert snap.rating == 0 and snap.weighted_score is None
    assert degraded == ["no_values"]


@pytest.mark.asyncio
async def test_one_scored_row_still_rates_and_persists(monkeypatch):
    """Control: fiscal-year margins + a scored TTM ROE is a measurement (ROE only)."""
    svc = sph._prof(monkeypatch, ratios=[{"debtToEquityRatioTTM": 1.2}],
                    km=[{"returnOnEquityTTM": 0.30}])
    _fresh_prof_tiers(monkeypatch, svc)
    written = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_profitability_snapshot_with_status("MCD")
    roe = next(m for m in snap.metrics if m.metric_key == "roe")
    assert roe.score == 5 and snap.weighted_score == 5.0 and snap.rating == 5
    assert degraded == [] and written == ["MCD"]


@pytest.mark.asyncio
async def test_a_failed_leg_and_no_values_are_both_reported_once(monkeypatch):
    """A raised key-metrics leg beside fiscal-year margins: the outage reason first,
    "no_values" once and last — never duplicated by the gate."""
    from app.integrations.fmp import FMPRateLimitException

    svc = sph._prof(monkeypatch, ratios=[], km=FMPRateLimitException("429"))
    _fresh_prof_tiers(monkeypatch, svc)
    written = _spy_persist(monkeypatch, svc)
    _snap, degraded = await svc.get_profitability_snapshot_with_status("MCD")
    assert degraded == ["key_metrics_ttm", "no_values"]
    assert written == []


@pytest.mark.parametrize("given,expected", [
    ([], ["no_values"]),
    (["no_values"], ["no_values"]),
    (["benchmarks"], ["benchmarks", "no_values"]),
    (["no_values", "profile"], ["profile", "no_values"]),
])
def test_with_no_values(given, expected):
    assert ps._with_no_values(given) == expected


# ══════════════════════════════════════════════════════════════════════════════════════
# HC-4 / SNAP-3 — the Overview's degraded Profitability card
# ══════════════════════════════════════════════════════════════════════════════════════


def _ov_prof(km=None, fr=None, bs=None) -> SnapshotItemResponse:
    return _overview()._build_profitability_snapshot(km or {}, fr or {}, {}, bs=bs)


def _ov_value(card: SnapshotItemResponse, name: str) -> str:
    return next(m.value for m in card.metrics if m.name == name)


@pytest.mark.parametrize("km,fr", [
    ({"returnOnEquity": 0.30, "returnOnAssets": 0.12},
     {"operatingProfitMargin": 0.46, "netProfitMargin": 0.32}),   # would have rated 5
    ({"returnOnEquity": 0.02}, {"netProfitMargin": 0.01}),         # would have rated 2
    ({}, {}),                                                      # always rated 1 before
])
def test_the_fallback_profitability_card_never_rates_or_scores(km, fr):
    card = _ov_prof(km, fr)
    assert card.rating == 0 and card.weighted_score is None
    assert all(m.score is None for m in card.metrics)
    assert card.computed_at


def test_the_fallback_reads_stable_return_on_equity_and_assets():
    """/stable key-metrics sends `returnOnEquity` (decimal); `km["roe"]` was never there,
    so ROE was always absent and the card always rated 1/5."""
    card = _ov_prof({"returnOnEquity": 1.54, "returnOnAssets": 0.31})
    assert _ov_value(card, "Return on Equity (ROE)") == "154.00%"   # >= 5 % still scaled
    assert _ov_value(card, "Return on Assets (ROA)") == "31.00%"
    # The legacy names still read when /stable's are absent.
    legacy = _ov_prof({"roe": 0.25, "returnOnTangibleAssets": 0.1})
    assert _ov_value(legacy, "Return on Equity (ROE)") == "25.00%"
    assert _ov_value(legacy, "Return on Assets (ROA)") == "10.00%"


@pytest.mark.parametrize("label,fr,bs,roe,shown", [
    ("mcd_negative_de", {"debtToEquityRatio": -10.0}, None, -2.16, "N/M"),
    ("boeing_loss_on_negative_equity", {"debtToEquityRatio": -13.0}, None, 3.03, "N/M"),
    ("balance_sheet_witness", {}, {"totalStockholdersEquity": -4e9}, -2.16, "N/M"),
    ("zero_equity_no_de", {}, {"totalStockholdersEquity": 0.0}, 0.8, "N/M"),
    ("zero_equity_beside_positive_de", {"debtToEquityRatio": 0.8},
     {"totalStockholdersEquity": 0.0}, 0.8, "80.00%"),
    ("positive_equity", {"debtToEquityRatio": 0.8}, {"totalStockholdersEquity": 5e9}, 0.3,
     "30.00%"),
    ("no_witness_at_all", {}, None, 0.3, "30.00%"),
])
def test_the_fallback_roe_on_negative_equity_is_not_meaningful(label, fr, bs, roe, shown):
    card = _ov_prof({"returnOnEquity": roe}, fr, bs)
    assert _ov_value(card, "Return on Equity (ROE)") == shown, label
    # Same verdict as the primary card's (and the Health Check's) equity test.
    de = fr.get("debtToEquityRatio")
    equity = (bs or {}).get("totalStockholdersEquity")
    assert (shown == "N/M") == (ps._equity_state(de, equity) is not None), label


@pytest.mark.parametrize("raw", [None, float("nan"), float("inf"), "abc", True])
def test_an_absent_or_junk_roe_prints_a_dash_not_zero(raw):
    km = {} if raw is None else {"returnOnEquity": raw}
    card = _ov_prof(km)
    assert _ov_value(card, "Return on Equity (ROE)") == "—"
    json.dumps(card.model_dump(), allow_nan=False)


def test_build_snapshots_hands_the_profitability_fallback_its_balance_sheet():
    """The annual balance sheet is the second negative-equity witness (D/E absent)."""
    snaps = _overview()._build_snapshots(
        [{"returnOnEquity": -2.16}], [{}], [{}], [{"totalStockholdersEquity": -4e9}], [{}],
        100.0, 1e10, "Consumer Cyclical", industry="Restaurants",
    )
    prof = next(s for s in snaps if s.category == "Profitability")
    assert _ov_value(prof, "Return on Equity (ROE)") == "N/M"
    assert prof.rating == 0


# ══════════════════════════════════════════════════════════════════════════════════════
# HC-3 / F1 — a Financial Health card on fewer than two scored rows is unrated
# ══════════════════════════════════════════════════════════════════════════════════════


_BANK = {"symbol": "C", "sector": "Financial Services", "industry": "Banks - Diversified",
         "mktCap": 1.4e11}


def _bank_health(de_status: str) -> HealthCheckResponse:
    """What the Health Check emits for a bank: D/E, P/E and ROE (Z and the three
    liquidity rows omitted). The snapshot hides P/E and ROE."""
    return HealthCheckResponse(
        symbol="C", overall_rating="mix", passed_count=1, total_count=3, metrics=[
            sph._hc_metric("debt_to_equity", 1.2, 1.5, status=de_status, level="industry"),
            sph._hc_metric("pe_ratio", 9.0, 12.0, status="negative", level="industry"),
            sph._hc_metric("roe", -3.0, 11.0, status="negative", level="industry"),
        ])


@pytest.mark.asyncio
@pytest.mark.parametrize("de_status,de_score", [("positive", 4), ("neutral", 3),
                                                 ("negative", 2)])
async def test_a_banks_card_is_not_rated_on_debt_to_equity_alone(monkeypatch, de_status,
                                                                  de_score):
    """It read Solid 4 / Moderate 3 / Soft 2 (weighted 4.2 / 3.0 / 1.8) on one ratio."""
    _hs, svc = sph._health_service(monkeypatch, _bank_health(de_status), profile=_BANK)
    snap, degraded = await svc._compute_with_status("C")
    assert [m.metric_key for m in snap.metrics] == ["debt_to_equity"]
    assert snap.rating == 0 and snap.weighted_score is None
    # The comparison itself is real and keeps its row score.
    assert snap.metrics[0].score == de_score
    assert snap.metrics[0].name == "Debt-to-Equity (vs sector 1.50)"
    assert degraded == [], "a bank's card is company state, not an outage"


@pytest.mark.asyncio
async def test_the_unrated_bank_card_is_persisted_and_reaches_the_report_unrated(monkeypatch):
    from app.services.agents.ticker_report_data_collector import (
        _card_weighted_to_score10,
        _settle_snapshot_result,
        _snapshot_to_card,
    )

    hs, svc = sph._health_service(monkeypatch, _bank_health("positive"), profile=_BANK)
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_inflight", {})
    monkeypatch.setattr(hs, "_degraded_by_key", {})
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    written = _spy_persist(monkeypatch, svc)
    result = await svc.get_health_snapshot_with_status("C")
    assert result[1] == [] and written == ["C"], "a stable company state is cached 24 h"

    out = SimpleNamespace(degraded_sections=[], snap_health="unset")
    _settle_snapshot_result(out, "snap_health", result, "C")
    assert out.snap_health is result[0]
    card = _snapshot_to_card("Health", out.snap_health, peer_group_level="industry")
    assert card["star_rating"] == 0, "no star verdict from one ratio"
    # The persona health factor no longer votes on one ratio: its card input is None.
    assert _card_weighted_to_score10(out.snap_health.weighted_score) is None


@pytest.mark.asyncio
async def test_a_broker_with_two_scored_rows_is_rated(monkeypatch):
    broker = dict(_BANK, symbol="AON", industry="Insurance - Brokers")
    health = HealthCheckResponse(
        symbol="AON", overall_rating="mix", passed_count=1, total_count=2, metrics=[
            sph._hc_metric("debt_to_equity", 1.2, 1.5, status="positive", level="industry"),
            sph._hc_metric("interest_coverage", 2.0, 8.0, status="negative", level="industry"),
        ])
    _hs, svc = sph._health_service(monkeypatch, health, profile=broker)
    snap, _ = await svc._compute_with_status("AON")
    # 0.4 * 3 (no Z) + 0.6 * pass(1 of 2 → 0.5 → 3) = 3.0
    assert snap.rating == 3 and snap.weighted_score == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_an_operating_company_is_rated_exactly_as_before(monkeypatch):
    health = HealthCheckResponse(
        symbol="TEST", overall_rating="good", passed_count=4, total_count=5, metrics=[
            sph._hc_metric("debt_to_equity", 0.4, 0.6),
            sph._hc_metric("current_ratio", 1.8, 1.4),
            sph._hc_metric("interest_coverage", 20.0, 15.0),
            sph._hc_metric("quick_ratio", 1.2, 1.0, status="neutral"),
            sph._hc_metric("altman_z_score", 4.2),
        ])
    _hs, svc = sph._health_service(monkeypatch, health)
    snap, degraded = await svc._compute_with_status("TEST")
    # pass = (3 + 0.5) / 4 = 0.875 → 4; Z 4.2 → 5: 0.4*5 + 0.6*4 = 4.4
    assert snap.rating == 4 and snap.weighted_score == pytest.approx(4.4)
    assert degraded == []


@pytest.mark.asyncio
async def test_z_alone_is_not_a_rating_either(monkeypatch):
    """One scored row is one row, whichever it is (Z with every ratio missing)."""
    health = HealthCheckResponse(symbol="TEST", overall_rating="excellent", passed_count=1,
                                 total_count=1, metrics=[sph._hc_metric("altman_z_score", 4.2)])
    _hs, svc = sph._health_service(monkeypatch, health)
    snap, _ = await svc._compute_with_status("TEST")
    assert snap.rating == 0 and snap.weighted_score is None


@pytest.mark.asyncio
async def test_the_fallback_bank_card_is_unrated(monkeypatch):
    _hs, svc = sph._health_service(monkeypatch, RuntimeError("health check exploded"),
                                   profile=_BANK, bench={"debt_to_equity": (1.5, "industry")})
    snap, degraded = await svc._compute_with_status("C")
    assert [m.metric_key for m in snap.metrics] == ["debt_to_equity"]
    assert snap.rating == 0 and snap.weighted_score is None
    assert degraded == ["health_check"]


@pytest.mark.asyncio
async def test_the_fallbacks_dash_z_row_scores_nothing(monkeypatch):
    """A Z of "—" (three income quarters: no TTM) scored the neutral 3 as if measured."""
    quarters = [dict(sph._HC_QUARTER, date=d) for d in ("2026-06-30", "2026-03-31",
                                                       "2025-12-31")]
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"), income=quarters,
        bench={"debt_to_equity": (0.6, "industry"), "current_ratio": (1.4, "industry"),
               "quick_ratio": (1.0, "industry")},
    )
    snap, _ = await svc._compute_with_status("TEST")
    z = next(m for m in snap.metrics if m.metric_key == "altman_z")
    assert z.value == "—" and z.score is None
    # D/E, current and quick ratio are scored: the card is still rated.
    assert snap.rating >= 1 and snap.weighted_score is not None


@pytest.mark.asyncio
async def test_a_health_check_with_no_metrics_is_unrated_not_moderate(monkeypatch):
    """The "Financial Health —" placeholder read "Moderate" (3, weighted 3.0)."""
    empty = HealthCheckResponse(symbol="C", overall_rating="mix", passed_count=0,
                                total_count=0, metrics=[], degraded=["no_metrics"])
    _hs, svc = sph._health_service(monkeypatch, empty, profile=_BANK)
    snap, degraded = await svc._compute_with_status("C")
    assert [m.name for m in snap.metrics] == ["Financial Health"]
    assert snap.rating == 0 and snap.weighted_score is None
    assert degraded == ["health_check:no_metrics", "no_values"]
    assert not any(isinstance(v, float) and math.isnan(v)
                   for v in (snap.weighted_score,) if v is not None)
