"""A company with NO revenue never gets a fabricated 0 margin on the company side (2026-10-11).

FMP answers a pre-revenue company (VKTX, live 2026-10-11) with grossProfitMargin =
operatingProfitMargin = netProfitMargin = 0, revenuePerShare 0, and priceToSalesRatio /
assetTurnover 0 — in /ratios and /ratios-ttm. The peer medians were fixed in cd8d3f30; a
read-only workflow then confirmed the same zeros on the company side, live:
  * the report's model evidence said "Net Margin: 0.0%" for a heavy loss-maker (both doors);
  * the Cathie Wood style-fit nudge was halved by a fabricated flat margin trend (-5 points);
  * the Profitability drill-down drew a 0.0% bar for every no-revenue year under a "—" row;
  * moat Brand Power (gross margin + P/S) and Cost Advantage (operating margin + asset
    turnover) published a deterministic 0.0/10 instead of falling back to the AI dimension,
    and the industry moat peer averages averaged those 0.0s in;
  * the degraded Overview Profitability card printed "Net Margin 0.00%".
One rule everywhere: `app.utils.margins`.
"""

import math
from datetime import datetime, timezone

import pytest

from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
    _fundamentals_history_for_period,
)
from app.services.industry_moat_benchmark_service import (
    MODEL_VERSION,
    _MODEL_VERSION_ORDER,
    _current_vintage_rows,
)
from app.services.moat_scoring_service import MoatScoringService
from app.services.sector_benchmark_service import METRIC_CONFIGS
from app.services.stock_overview_service import StockOverviewService
from app.services.ticker_report_cache import CACHE_SCHEMA_FLOOR
from app.utils.margins import revenue_backed_margin, revenue_scaled_ratio

# FMP's answer for a company with no revenue, and a revenue-backed control (IONS-shaped).
_VKTX_RATIOS = {
    "revenuePerShare": 0, "grossProfitMargin": 0, "operatingProfitMargin": 0,
    "netProfitMargin": 0, "priceToSalesRatio": 0, "assetTurnover": 0,
}
_IONS_RATIOS = {
    "revenuePerShare": 5.9, "grossProfitMargin": 0.983, "operatingProfitMargin": -0.405,
    "netProfitMargin": -0.404, "priceToSalesRatio": 8.6, "assetTurnover": 0.3,
}


# ── the shared helpers ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("margin, rps, expected", [
    (float("nan"), 5.0, None),
    (float("inf"), 5.0, None),
    (float("-inf"), 5.0, None),
    (0.3, float("nan"), 0.3),       # a non-finite revenue per share counts as missing
    (0.3, float("-inf"), 0.3),
    (0.3, 0, None),
    (-0.4, 5.9, -0.4),
])
def test_revenue_backed_margin_non_finite_inputs(margin, rps, expected):
    assert revenue_backed_margin(margin, rps) == expected


@pytest.mark.parametrize("value, expected", [
    (0, None), (0.0, None), (-1.5, None), (None, None),
    (float("nan"), None), (float("inf"), None),
    (1e-9, 1e-9), (8.6, 8.6), (250.0, 250.0),
])
def test_revenue_scaled_ratio(value, expected):
    assert revenue_scaled_ratio(value) == expected


# ── the report collector: the model's evidence and the persona style-fit ────────────────

def _collected(ratios_ttm, ratios):
    out = CollectedTickerData(ticker="VKTX", persona_key="cathie_wood")
    out.profile = {"symbol": "VKTX", "mktCap": 3.0e9, "price": 30.0}
    out.ratios_ttm = ratios_ttm
    out.ratios = ratios
    out.income = [{"date": "2025-12-31", "revenue": 0, "netIncome": -1.0e8}]
    return out


def _metrics(ratios_ttm, ratios):
    out = _collected(ratios_ttm, ratios)
    TickerReportDataCollector()._compute_metrics(out)
    return out.computed


def _ttm(row):
    return {
        "revenuePerShareTTM": row["revenuePerShare"],
        "grossProfitMarginTTM": row["grossProfitMargin"],
        "operatingProfitMarginTTM": row["operatingProfitMargin"],
        "netProfitMarginTTM": row["netProfitMargin"],
    }


def test_no_revenue_company_has_no_current_margins_for_the_model():
    c = _metrics([_ttm(_VKTX_RATIOS)], [dict(_VKTX_RATIOS, date="2025-12-31")])
    assert (c["gross_margin"], c["operating_margin"], c["net_margin"]) == (None, None, None)


def test_revenue_backed_current_margins_are_kept_including_losses():
    c = _metrics([_ttm(_IONS_RATIOS)], [dict(_IONS_RATIOS, date="2025-12-31")])
    assert (c["gross_margin"], c["operating_margin"], c["net_margin"]) == (98.3, -40.5, -40.4)


def test_ttm_without_revenue_never_falls_back_to_last_fiscal_year():
    # Revenue ended: TTM says none, the fiscal year still had some. The TTM row decides,
    # as on the Profitability card — the model must not get last year's margin as current.
    c = _metrics([_ttm(_VKTX_RATIOS)], [dict(_IONS_RATIOS, date="2025-12-31")])
    assert c["gross_margin"] is None and c["net_margin"] is None


def test_annual_row_is_used_only_when_the_ttm_row_lacks_the_margin():
    c = _metrics([], [dict(_IONS_RATIOS, date="2025-12-31")])
    assert c["gross_margin"] == 98.3
    c = _metrics([], [dict(_VKTX_RATIOS, date="2025-12-31")])
    assert c["gross_margin"] is None


def test_missing_revenue_per_share_keeps_a_real_margin_and_drops_an_exact_zero():
    ttm = {"grossProfitMarginTTM": 0.62, "netProfitMarginTTM": 0, "operatingProfitMarginTTM": "0.2"}
    c = _metrics([ttm], [])
    assert c["gross_margin"] == 62.0
    assert c["net_margin"] is None
    assert c["operating_margin"] == 20.0


def test_prior_year_without_revenue_gives_no_margin_trend():
    ratios = [dict(_IONS_RATIOS, date="2025-12-31"), dict(_VKTX_RATIOS, date="2024-12-31")]
    c = _metrics([_ttm(_IONS_RATIOS)], ratios)
    assert c["gross_margin"] == 98.3
    assert c["gross_margin_prev"] is None      # persona_scoring then skips the trend sub
    ratios = [dict(_IONS_RATIOS, date="2025-12-31"), dict(_IONS_RATIOS, date="2024-12-31", grossProfitMargin=0.95)]
    assert _metrics([_ttm(_IONS_RATIOS)], ratios)["gross_margin_prev"] == 95.0
    # A non-zero margin beside a negative revenue is a sign flip, not a margin.
    ratios = [dict(_IONS_RATIOS, date="2025-12-31"), dict(_IONS_RATIOS, date="2024-12-31", grossProfitMargin=1.13, revenuePerShare=-1.0)]
    assert _metrics([_ttm(_IONS_RATIOS)], ratios)["gross_margin_prev"] is None


def test_the_persona_style_fit_sees_no_fabricated_trend():
    from app.services.agents.persona_scoring import style_fit_adjustment

    c = _metrics([_ttm(_VKTX_RATIOS)], [dict(_VKTX_RATIOS, date="2025-12-31"), dict(_VKTX_RATIOS, date="2024-12-31")])
    with_fake = style_fit_adjustment("cathie_wood", {"revenue_cagr": 25.0, "gross_margin": 0.0, "gross_margin_prev": 0.0})
    fixed = style_fit_adjustment("cathie_wood", {"revenue_cagr": 25.0, "gross_margin": c["gross_margin"], "gross_margin_prev": c["gross_margin_prev"]})
    alone = style_fit_adjustment("cathie_wood", {"revenue_cagr": 25.0})
    assert fixed == alone
    assert with_fake != alone      # the bug this pins: the 0.0/0.0 pair moved the nudge


# ── the report's Profitability drill-down history ───────────────────────────────────────

def _hist(rows):
    income = [{"calendarYear": int(r["date"][:4]), "period": "FY", "date": r["date"], "revenue": 1} for r in rows]
    return _fundamentals_history_for_period(income, [], [], [], rows, {}, quarterly=False)


def _values(hist, key):
    return {p["period"]: p["value"] for p in hist.get(key, [])}


def test_history_has_no_zero_bars_for_no_revenue_years():
    rows = [dict(_VKTX_RATIOS, date="2023-12-31"), dict(_VKTX_RATIOS, date="2024-12-31"),
            dict(_IONS_RATIOS, date="2025-12-31")]
    hist = _hist(rows)
    for key in ("gross_margin", "operating_margin", "net_margin"):
        vals = _values(hist, key)
        assert 0.0 not in vals.values(), (key, vals)
        assert [v for v in vals.values() if v is not None] == [
            {"gross_margin": 98.3, "operating_margin": -40.5, "net_margin": -40.4}[key]
        ]


def test_history_of_a_pre_revenue_company_holds_no_margin_value():
    hist = _hist([dict(_VKTX_RATIOS, date=f"{y}-12-31") for y in (2022, 2023, 2024, 2025)])
    for key in ("gross_margin", "operating_margin", "net_margin"):
        assert all(v is None for v in _values(hist, key).values()), key


# ── moat scoring: Brand Power and Cost Advantage ────────────────────────────────────────

_MEDIANS = {
    "gross_margin": {"median": 0.6, "period": "2024", "n": 120},
    "ps_ratio": {"median": 5.0, "period": "2024", "n": 120},
    "operating_margin": {"median": 0.1, "period": "2024", "n": 120},
    "asset_turnover": {"median": 0.5, "period": "2024", "n": 120},
    "sga_to_revenue": {"median": 20.0, "period": "2024", "n": 120},
}


def _moat():
    return MoatScoringService.__new__(MoatScoringService)


def test_brand_power_is_not_published_for_a_company_with_no_revenue():
    pillar = _moat()._score_brand_power(dict(_VKTX_RATIOS), _MEDIANS)
    assert pillar.score is None                 # falls back to the AI dimension
    assert all(d.sub_score is None for d in pillar.drivers)
    assert all(d.focal is None for d in pillar.drivers)


def test_brand_power_still_scores_a_company_with_revenue():
    pillar = _moat()._score_brand_power(dict(_IONS_RATIOS), _MEDIANS)
    assert pillar.score is not None and pillar.score > 0


def test_cost_advantage_has_no_fabricated_drivers_without_revenue():
    pillar = _moat()._score_cost_advantage({"revenue": 0}, dict(_VKTX_RATIOS), _MEDIANS)
    by = {d.metric: d for d in pillar.drivers}
    assert by["operating_margin"].focal is None and by["operating_margin"].sub_score is None
    assert by["asset_turnover"].focal is None and by["asset_turnover"].sub_score is None
    assert pillar.score is None


def test_moat_margin_readers_keep_real_losses_and_drop_vendor_zeros():
    m = _moat()
    assert m._gross_margin_ratio(_IONS_RATIOS) == 0.983
    assert m._operating_margin_ratio(_IONS_RATIOS) == -0.405
    assert m._gross_margin_ratio(_VKTX_RATIOS) is None
    assert m._gross_margin_ratio({"grossProfitMargin": 0.3, "revenuePerShare": 0}) is None
    assert m._gross_margin_ratio({"grossProfitMargin": 1.13, "revenuePerShare": -2.0}) is None
    assert m._operating_margin_ratio({"operatingProfitMargin": "0.2"}) == 0.2   # no revenue field: kept
    assert m._operating_margin_ratio({"operatingProfitMargin": 0.2, "revenuePerShare": -1}) is None
    assert m._gross_margin_ratio(None) is None


def test_asset_turnover_peer_median_drops_no_revenue_companies():
    cfg = next(mc for mc in METRIC_CONFIGS if mc["name"] == "asset_turnover")
    assert cfg.get("positive_only") is True


# ── moat peer averages: a new scorer vintage ────────────────────────────────────────────

def test_moat_model_version_is_the_newest_vintage_and_older_rows_are_pending():
    # This vintage stays KNOWN (its rows rank as an older vintage) after a later bump.
    assert "moat_v1.2026-10-11" in _MODEL_VERSION_ORDER
    assert _MODEL_VERSION_ORDER[-1] == MODEL_VERSION
    assert _MODEL_VERSION_ORDER.index("moat_v1.2026-10-11") <= _MODEL_VERSION_ORDER.index(MODEL_VERSION)
    assert _MODEL_VERSION_ORDER.index("moat_v1.2026-10") < _MODEL_VERSION_ORDER.index(MODEL_VERSION)
    rows = [
        {"pillar_name": "Brand Power", "model_version": "moat_v1.2026-10", "peer_average_score": 4.6},
        {"pillar_name": "Cost Advantage", "model_version": "moat_v1.2026-05", "peer_average_score": 4.2},
    ]
    kept = _current_vintage_rows("Biotechnology", rows)
    assert {r["pillar_name"] for r in kept} == {"Brand Power", "Cost Advantage"}   # still served


# ── the degraded Overview Profitability card ────────────────────────────────────────────

def _fallback(fr, km=None):
    svc = StockOverviewService.__new__(StockOverviewService)
    card = svc._build_profitability_snapshot(km or {}, fr, {})
    return {m.name: m.value for m in card.metrics}


def test_fallback_card_shows_a_dash_not_zero_for_a_company_with_no_revenue():
    vals = _fallback(dict(_VKTX_RATIOS))
    assert vals["Operating Margin"] == "—"
    assert vals["Net Margin"] == "—"


def test_fallback_card_scales_every_fmp_decimal_including_big_losses():
    vals = _fallback({"revenuePerShare": 0.4, "operatingProfitMargin": -2.5, "netProfitMargin": -1.2})
    assert vals["Operating Margin"] == "-250.00%"     # was "-2.50%" (abs < 1 heuristic)
    assert vals["Net Margin"] == "-120.00%"
    vals = _fallback(dict(_IONS_RATIOS))
    assert vals["Operating Margin"] == "-40.50%"
    assert vals["Net Margin"] == "-40.40%"


def test_fallback_card_reads_key_metrics_only_when_ratios_lack_the_margin():
    vals = _fallback({}, {"netProfitMargin": 0.15})
    assert vals["Net Margin"] == "15.00%"
    vals = _fallback({"netProfitMargin": 0, "revenuePerShare": 0}, {"netProfitMargin": 0.15})
    assert vals["Net Margin"] == "—"                  # the ratios row decides (no revenue)
    vals = _fallback({"netProfitMargin": True}, {})
    assert vals["Net Margin"] == "—"                  # a bool is junk, not 100%


# ── report caches rebuild ───────────────────────────────────────────────────────────────

def test_report_cache_floor_moved_past_this_change():
    assert CACHE_SCHEMA_FLOOR >= datetime(2026, 10, 11, 19, 22, tzinfo=timezone.utc)
    assert math.isfinite(CACHE_SCHEMA_FLOOR.timestamp())
