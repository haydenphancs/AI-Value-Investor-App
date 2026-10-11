"""Peer margin medians never count FMP's "0 when there is no revenue" (2026-10-11).

FMP divides by revenue, so a company with none (a clinical-stage biotech, an explorer)
gets grossProfitMargin = operatingProfitMargin = netProfitMargin = 0 with revenuePerShare 0
— in /ratios-ttm and in every fiscal /ratios row (VKTX, live 2026-10-11). Both median
producers counted those zeros: on the 2026-10-11 Sunday run 72 of Biotechnology's 213
companies had no revenue, and its TTM net-margin median read exactly 0.0% (the
revenue-backed median is -76.9%), its gross margin 69.0% (85.5%), and the Healthcare
sector's operating margin 0.0% (4.0%). The company side has treated the same 0 as absent
since 2026-09-30 (`profitability_snapshot_service._ttm_margin`).

Covers the shared rule (`revenue_backed_margin`), the weekly TTM extractor and the fiscal
extractor the quarterly sector + industry recomputes share, and pins which metrics carry
the rule (ROE/ROA and the multiples must not).
"""

import asyncio

import pytest

import app.services.sector_benchmark_service as sbs
from app.services.industry_benchmark_service import (
    IndustryBenchmarkService,
    _TTM_METRICS,
    _TTM_REVENUE_BACKED,
    _extract_ttm,
)
from app.services.sector_benchmark_service import revenue_backed_margin

_ttm_median = IndustryBenchmarkService._ttm_median
_MARGINS = ("gross_margin", "operating_margin", "net_margin")


def _sector_svc() -> sbs.SectorBenchmarkService:
    svc = sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)
    svc.fmp = None
    svc.supabase = None
    svc._fmp_semaphore = asyncio.Semaphore(10)
    return svc


def _metric(name: str) -> dict:
    return next(mc for mc in sbs.METRIC_CONFIGS if mc["name"] == name)


# ── the shared rule ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "margin, rps, expected",
    [
        (0.0, 0.0, None),          # VKTX: the no-revenue answer
        (0.0, 5.9, None),          # an exact 0 is the vendor's 0, revenue or not
        (0, None, None),           # int 0, field missing
        (-0.0, 3.0, None),         # negative zero is still zero
        (-0.40, 5.9, -0.40),       # IONS: a real loss-making margin is KEPT
        (0.98, 5.9, 0.98),
        (0.25, 1e-9, 0.25),        # tiny positive revenue is still revenue
        (1.13, -2.0, None),        # negative revenue sign-flips a loss into +113%
        (0.30, 0.0, None),         # a non-zero margin beside zero revenue is not a margin
        (-15.0, 0.01, -15.0),      # extreme but real: kept (no cap on margins)
        (0.25, None, 0.25),        # field missing: kept, so a schema drop can't empty medians
        (None, 5.0, None),
        (None, None, None),
    ],
)
def test_revenue_backed_margin(margin, rps, expected):
    assert revenue_backed_margin(margin, rps) == expected


# ── weekly TTM extractor ─────────────────────────────────────────────────────────────────

def _vktx_ttm():
    return {
        "revenuePerShareTTM": 0,
        "grossProfitMarginTTM": 0,
        "operatingProfitMarginTTM": 0,
        "netProfitMarginTTM": 0,
        "debtToEquityRatioTTM": 0.02,
        "currentRatioTTM": 25.0,
    }


def test_ttm_zero_revenue_company_contributes_no_margin_but_keeps_its_other_metrics():
    out = _extract_ttm(_vktx_ttm(), {"returnOnEquityTTM": -0.31, "returnOnAssetsTTM": -0.29})
    assert all(out[m] is None for m in _MARGINS)
    # Equity- and asset-based metrics are real for a no-revenue company.
    assert out["roe"] == -0.31
    assert out["roa"] == -0.29
    assert out["current_ratio"] == 25.0
    assert out["debt_to_equity"] == 0.02


def test_ttm_loss_making_margins_with_revenue_are_kept():
    r0 = {
        "revenuePerShareTTM": 5.9,
        "grossProfitMarginTTM": 0.98,
        "operatingProfitMarginTTM": -0.68,
        "netProfitMarginTTM": -0.65,
    }
    out = _extract_ttm(r0, {})
    assert (out["gross_margin"], out["operating_margin"], out["net_margin"]) == (0.98, -0.68, -0.65)


def test_ttm_negative_revenue_and_string_fields():
    out = _extract_ttm({"revenuePerShareTTM": "-1.5", "netProfitMarginTTM": "1.13"}, {})
    assert out["net_margin"] is None
    out = _extract_ttm({"revenuePerShareTTM": "2.0", "netProfitMarginTTM": "0.12"}, {})
    assert out["net_margin"] == 0.12
    # NaN / inf revenue reads as missing (not as "no revenue"), so a real margin survives.
    out = _extract_ttm({"revenuePerShareTTM": float("nan"), "netProfitMarginTTM": 0.12}, {})
    assert out["net_margin"] == 0.12
    out = _extract_ttm({"revenuePerShareTTM": float("inf"), "netProfitMarginTTM": float("inf")}, {})
    assert out["net_margin"] is None


def test_ttm_biotech_median_is_the_revenue_backed_one_not_zero():
    # The 2026-10-11 shape at small scale: 6 with revenue (mostly losses), 5 without.
    with_rev = [-0.9, -0.8, -0.77, -0.5, 0.1, 0.3]
    rows = [{"revenuePerShareTTM": 3.0, "netProfitMarginTTM": m} for m in with_rev]
    rows += [_vktx_ttm() for _ in range(5)]
    values = [_extract_ttm(r, {})["net_margin"] for r in rows]
    med, n = _ttm_median("net_margin", [v for v in values if v is not None])
    assert n == 6
    assert med == pytest.approx(-0.635)
    # Before the fix the five zeros pulled the median to exactly 0.
    raw = [r["netProfitMarginTTM"] for r in rows]
    assert _ttm_median("net_margin", raw)[0] == 0.0


def test_ttm_all_no_revenue_industry_writes_no_margin_row():
    values = [_extract_ttm(_vktx_ttm(), {})["gross_margin"] for _ in range(30)]
    assert _ttm_median("gross_margin", [v for v in values if v is not None]) == (None, 0)


def test_ttm_rule_covers_exactly_the_three_margins():
    assert _TTM_REVENUE_BACKED == frozenset(_MARGINS)
    assert _TTM_REVENUE_BACKED <= set(_TTM_METRICS)
    # A multiple of 0 / an ROE of 0 is a different question; the rule must not reach them.
    out = _extract_ttm({"revenuePerShareTTM": 0, "priceToBookRatioTTM": 1.2}, {"returnOnEquityTTM": 0})
    assert out["pb_ratio"] == 1.2
    assert out["roe"] == 0.0


# ── fiscal extractor (quarterly sector + industry recomputes) ───────────────────────────

def test_fiscal_configs_flag_exactly_the_three_margins():
    flagged = {mc["name"] for mc in sbs.METRIC_CONFIGS if mc.get("revenue_backed")}
    assert flagged == set(_MARGINS)
    for name in _MARGINS:
        assert _metric(name)["source"] == "ratios"   # the field lives beside revenuePerShare


@pytest.mark.parametrize("metric, field", [
    ("gross_margin", "grossProfitMargin"),
    ("operating_margin", "operatingProfitMargin"),
    ("net_margin", "netProfitMargin"),
])
def test_fiscal_annual_drops_no_revenue_years_and_keeps_real_ones(metric, field):
    vktx = [
        {"date": "2025-12-31", "revenuePerShare": 0, field: 0},
        {"date": "2024-12-31", "revenuePerShare": 0, field: 0},
    ]
    ions = [
        {"date": "2025-12-31", "revenuePerShare": 5.9, field: -0.40},
        {"date": "2024-12-31", "revenuePerShare": 4.7, field: -0.64},
    ]
    flipped = [{"date": "2025-12-31", "revenuePerShare": -1.0, field: 1.13}]
    data = [{"ratios_annual": vktx}, {"ratios_annual": ions}, {"ratios_annual": flipped}]
    pooled = _sector_svc()._collect_metric_values(data, _metric(metric), "annual")
    assert pooled == {"2025": [-0.40], "2024": [-0.64]}


def test_fiscal_quarterly_drops_no_revenue_quarters():
    rows = [
        {"date": "2026-06-30", "revenuePerShare": 0, "netProfitMargin": 0},
        {"date": "2026-03-31", "revenuePerShare": 1.49, "netProfitMargin": -0.376},
    ]
    pooled = _sector_svc()._collect_metric_values([{"ratios_quarterly": rows}], _metric("net_margin"), "quarterly")
    assert list(pooled.values()) == [[-0.376]]


def test_fiscal_rule_does_not_touch_other_ratio_metrics():
    # A no-revenue company's liquidity and leverage are real readings.
    rows = [{"date": "2025-12-31", "revenuePerShare": 0, "currentRatio": 25.0, "debtToEquityRatio": 0}]
    svc = _sector_svc()
    assert svc._collect_metric_values([{"ratios_annual": rows}], _metric("current_ratio"), "annual") == {"2025": [25.0]}
    assert svc._collect_metric_values([{"ratios_annual": rows}], _metric("debt_to_equity"), "annual") == {"2025": [0.0]}
