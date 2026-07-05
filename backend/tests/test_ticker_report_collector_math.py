"""
Unit tests for the deterministic math in TickerReportDataCollector.

These pin the edge-case behavior that the cross-view audit found
weakest: Earnings Yield with negative/zero PE, CAGR direction
regardless of FMP order, unit-aware forecast scaling, valuation vital
fallback when DCF is missing.

No network, no Supabase. Each test exercises a single helper in
isolation.
"""

from __future__ import annotations

import asyncio

import pytest

from datetime import datetime, timedelta, timezone

from app.services.agents.ticker_report_data_collector import (
    _INDUSTRY_PEERS_CACHE,
    _absolute_peer_score,
    _absolute_threshold_fallback,
    _apply_tam_source,
    _normalize_ai_tam_billions,
    _build_annual_timeline,
    _build_timeline_prices,
    _build_competitors,
    _directness_from_rank,
    _moat_multiplier,
    _relative_peer_score,
    _build_insider_sections,
    _build_macro_risk_factors_from_fred,
    _build_macro_risk_factors_from_indicators,
    _build_market_dynamics,
    _build_price_action,
    _build_revenue_forecast_partial,
    _build_valuation_vital,
    _build_wall_street_sections,
    _classify_news_catalyst,
    _extract_tam_relevant_excerpt,
    _industry_universe_peers,
    _merge_macro_risk_factors,
    _overlay_ai_guidance,
    _safe_cagr,
    compute_earnings_yield,
)
from app.services.sector_aggregates_service import (
    SectorAggregates,
    compute_hhi,
    compute_revenue_cagr_5y,
    compute_top_n_share,
)
from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
)


# ── Earnings Yield ────────────────────────────────────────────────────


def test_earnings_yield_negative_pe():
    """Negative PE (negative earnings) → None, never a negative percent."""
    c = {"pe_ratio": -12.5}
    assert compute_earnings_yield(c) is None


def test_earnings_yield_zero_pe():
    """Zero PE → None (would otherwise divide by zero)."""
    c = {"pe_ratio": 0}
    assert compute_earnings_yield(c) is None


def test_earnings_yield_missing_pe():
    """Missing PE → None."""
    c = {}
    assert compute_earnings_yield(c) is None
    assert compute_earnings_yield({"pe_ratio": None}) is None


def test_earnings_yield_positive_pe():
    """PE 25 → 4.0% earnings yield."""
    c = {"pe_ratio": 25.0}
    assert compute_earnings_yield(c) == 4.0


def test_earnings_yield_high_pe():
    """High PE (low yield) — 100 PE → 1.0%."""
    c = {"pe_ratio": 100.0}
    assert compute_earnings_yield(c) == 1.0


# ── CAGR direction safety ─────────────────────────────────────────────


def test_cagr_negative_endpoints_returns_none():
    """Negative start or end → None (CAGR undefined for sign flips)."""
    assert _safe_cagr(-10.0, 100.0, 3) is None
    assert _safe_cagr(100.0, -10.0, 3) is None


def test_cagr_zero_endpoint_returns_none():
    assert _safe_cagr(0.0, 100.0, 3) is None
    assert _safe_cagr(100.0, 0.0, 3) is None


def test_cagr_correct_direction():
    """100 → 200 over 3 periods (2 years of growth) → ~41.4% CAGR."""
    result = _safe_cagr(100.0, 200.0, 3)
    assert result is not None
    assert 41.0 < result < 42.0


# ── Forecast scaling ──────────────────────────────────────────────────


def test_forecast_picks_billions_for_large_revenue():
    """A $100B forecast should plot in Billions, not collapse to 0.0."""
    estimates = [
        {"date": "2024-12-31", "estimatedRevenueAvg": 100_000_000_000, "estimatedEpsAvg": 5.0},
        {"date": "2025-12-31", "estimatedRevenueAvg": 110_000_000_000, "estimatedEpsAvg": 5.5},
        {"date": "2026-12-31", "estimatedRevenueAvg": 121_000_000_000, "estimatedEpsAvg": 6.0},
    ]
    result = _build_revenue_forecast_partial(estimates, 10.0, 9.5)
    revs = [p["revenue"] for p in result["projections"]]
    assert revs == [100.0, 110.0, 121.0]


def test_forecast_picks_millions_for_small_revenue():
    """A $500M forecast should plot in Millions (not 0.5 in billions)."""
    estimates = [
        {"date": "2024-12-31", "estimatedRevenueAvg": 500_000_000, "estimatedEpsAvg": 1.0},
        {"date": "2025-12-31", "estimatedRevenueAvg": 600_000_000, "estimatedEpsAvg": 1.2},
        {"date": "2026-12-31", "estimatedRevenueAvg": 720_000_000, "estimatedEpsAvg": 1.5},
    ]
    result = _build_revenue_forecast_partial(estimates, 20.0, 22.0)
    revs = [p["revenue"] for p in result["projections"]]
    assert revs == [500.0, 600.0, 720.0]


def test_forecast_sorted_oldest_to_newest():
    """Even when FMP returns newest-first, the chart reads left→right by year."""
    estimates = [
        {"date": "2026-12-31", "estimatedRevenueAvg": 121_000_000_000, "estimatedEpsAvg": 6.0},
        {"date": "2024-12-31", "estimatedRevenueAvg": 100_000_000_000, "estimatedEpsAvg": 5.0},
        {"date": "2025-12-31", "estimatedRevenueAvg": 110_000_000_000, "estimatedEpsAvg": 5.5},
    ]
    result = _build_revenue_forecast_partial(estimates, 10.0, 9.5)
    periods = [p["period"] for p in result["projections"]]
    assert periods == ["2024", "2025", "2026"]


def test_forecast_annual_timeline_continuity():
    """annual_timeline is ONE GAPLESS yearly series: historical actuals
    (is_forecast=False) then ALL forward estimates after the last reported year
    (is_forecast=True) — including the 2026 the curated `projections` window
    skips. Sorted oldest→newest, scaled by one shared divisor."""
    estimates = [  # forward incl. 2026 (which the curated projections may skip)
        {"date": "2026-12-31", "estimatedRevenueAvg": 65_000_000_000, "estimatedEpsAvg": 5.0,
         "numAnalystsRevenue": 12, "numAnalystsEps": 10},
        {"date": "2027-12-31", "estimatedRevenueAvg": 88_000_000_000, "estimatedEpsAvg": 6.0},
        {"date": "2028-12-31", "estimatedRevenueAvg": 130_000_000_000, "estimatedEpsAvg": 8.0},
    ]
    income = [  # newest-first like FMP; reported through 2025
        {"date": "2025-12-31", "revenue": 57_000_000_000, "epsDiluted": 4.3},
        {"date": "2024-12-31", "revenue": 53_000_000_000, "epsDiluted": 3.7},
        {"date": "2023-12-31", "revenue": 50_000_000_000, "epsDiluted": 3.0},
    ]
    result = _build_revenue_forecast_partial(estimates, 10.0, 9.5, income)
    tl = result["annual_timeline"]
    # Gapless 2023..2028 — actuals then forecast, NO missing 2026.
    assert [t["period"] for t in tl] == ["2023", "2024", "2025", "2026", "2027", "2028"]
    assert [t["is_forecast"] for t in tl] == [False, False, False, True, True, True]
    # One shared 1e9 divisor across actuals + forecast → comparable billions.
    assert [t["revenue"] for t in tl] == [50.0, 53.0, 57.0, 65.0, 88.0, 130.0]
    assert tl[0]["revenue_yoy_pct"] is None     # oldest, no prior
    assert tl[3]["revenue_yoy_pct"] == 14.0     # 2026 vs 2025: (65-57)/57*100
    # Per-year analyst coverage rides each FORECAST row (FMP numAnalysts*);
    # actuals carry None, and forecast years without counts stay None.
    assert tl[2]["revenue_analyst_count"] is None and tl[2]["eps_analyst_count"] is None  # 2025 actual
    assert tl[3]["revenue_analyst_count"] == 12 and tl[3]["eps_analyst_count"] == 10      # 2026 forecast
    assert tl[4]["revenue_analyst_count"] is None and tl[4]["eps_analyst_count"] is None  # 2027, no counts
    # The curated module `projections` are independent + unchanged (all forecast).
    assert all(p["is_forecast"] is True for p in result["projections"])
    # Forecast attribution: nearest forecast year's analyst count (max rev/eps).
    assert result["forecast_analyst_count"] == 12


def test_forecast_annual_timeline_edge_cases():
    """Estimates-only → all-forecast timeline; nothing → empty."""
    estimates = [
        {"date": "2026-12-31", "estimatedRevenueAvg": 65_000_000_000, "estimatedEpsAvg": 5.0},
        {"date": "2027-12-31", "estimatedRevenueAvg": 88_000_000_000, "estimatedEpsAvg": 6.0},
    ]
    res1 = _build_revenue_forecast_partial(estimates, 10.0, 9.5)  # no income
    assert [t["is_forecast"] for t in res1["annual_timeline"]] == [True, True]
    res2 = _build_revenue_forecast_partial([], 10.0, 9.5)  # nothing
    assert res2["annual_timeline"] == []


def test_annual_timeline_trims_to_5_actuals_and_5_forecasts():
    """The Future Forecast window is the last 5 reported years + up to 5 forward
    estimate years (industry standard), regardless of how deep FMP goes — a
    company's whole history would swamp the forward view."""
    income = [  # 8 reported years (2017..2024), newest-first like FMP
        {"date": f"{y}-12-31", "revenue": (y - 2000) * 1_000_000_000,
         "epsDiluted": float(y - 2015)}
        for y in range(2024, 2016, -1)
    ]
    estimates = [  # 7 forward years (2025..2031)
        {"date": f"{y}-12-31", "revenueAvg": (y - 2000) * 1_000_000_000,
         "epsAvg": float(y - 2015), "numAnalystsRevenue": 9, "numAnalystsEps": 8}
        for y in range(2025, 2032)
    ]
    tl = _build_annual_timeline(income, estimates)
    actual_years = [t["period"] for t in tl if not t["is_forecast"]]
    forecast_years = [t["period"] for t in tl if t["is_forecast"]]
    # Last 5 actuals + first 5 forecasts; nothing older, nothing beyond 5 forward.
    assert actual_years == ["2020", "2021", "2022", "2023", "2024"]
    assert forecast_years == ["2025", "2026", "2027", "2028", "2029"]
    # The leftmost DISPLAYED actual still gets a YoY chip from the off-screen
    # (2019) anchor — the anchor itself is not emitted.
    assert tl[0]["period"] == "2020"
    assert tl[0]["revenue_yoy_pct"] is not None


def test_annual_timeline_missing_values_show_na():
    """A forecast year FMP only partly covered surfaces 'N/A' labels + None analyst
    counts (never a misleading $0 or a hidden blank), while keeping the numeric 0 so
    the chart math stays finite. The fully-covered year is untouched."""
    income = [{"date": "2024-12-31", "revenue": 10_000_000_000, "epsDiluted": 2.0}]
    estimates = [
        {"date": "2025-12-31", "revenueAvg": 11_000_000_000, "epsAvg": 2.2,
         "numAnalystsRevenue": 7, "numAnalystsEps": 6},
        # Far-out year: revenue estimate only — no EPS, no analyst counts.
        {"date": "2026-12-31", "revenueAvg": 12_000_000_000},
    ]
    tl = _build_annual_timeline(income, estimates)
    far = next(t for t in tl if t["period"] == "2026")
    assert far["eps_label"] == "N/A"
    assert far["eps"] == 0.0                       # numeric stays finite
    assert far["eps_yoy_pct"] is None
    assert far["eps_analyst_count"] is None
    assert far["revenue_analyst_count"] is None
    assert far["revenue_label"] != "N/A"           # revenue present that year
    near = next(t for t in tl if t["period"] == "2025")
    assert near["eps_label"] == "$2.20" and near["eps_analyst_count"] == 6


def test_annual_timeline_new_company_shows_only_what_exists():
    """A young company (2 reported years + 1 forecast) shows exactly those rows —
    no left-padding to 5, no crash; the leftmost actual has no YoY (no anchor)."""
    income = [
        {"date": "2023-12-31", "revenue": 2_000_000_000, "epsDiluted": 0.5},
        {"date": "2024-12-31", "revenue": 3_000_000_000, "epsDiluted": 0.8},
    ]
    estimates = [
        {"date": "2025-12-31", "revenueAvg": 4_000_000_000, "epsAvg": 1.1,
         "numAnalystsRevenue": 5, "numAnalystsEps": 4},
    ]
    tl = _build_annual_timeline(income, estimates)
    assert [t["period"] for t in tl] == ["2023", "2024", "2025"]
    assert [t["is_forecast"] for t in tl] == [False, False, True]
    assert tl[0]["revenue_yoy_pct"] is None


def test_annual_timeline_negative_eps_yoy_is_signed():
    """Loss companies (PLUG) must still get an EPS YoY %: abs(prior) keeps the sign
    tracking, so a NARROWING loss reads positive and a WIDENING loss negative — not
    the old null that hid the % entirely."""
    income = [
        {"date": "2023-12-31", "revenue": 600_000_000, "epsDiluted": -2.00},
        {"date": "2024-12-31", "revenue": 710_000_000, "epsDiluted": -1.41},  # loss narrowed
        {"date": "2025-12-31", "revenue": 800_000_000, "epsDiluted": -1.80},  # loss widened
    ]
    tl = _build_annual_timeline(income, [])
    by_year = {t["period"]: t for t in tl}
    # 2024: (-1.41 - (-2.00)) / |−2.00| * 100 = +29.5 (loss narrowed → positive)
    assert by_year["2024"]["eps_yoy_pct"] == 29.5
    # 2025: (-1.80 - (-1.41)) / |−1.41| * 100 ≈ -27.7 (loss widened → negative)
    assert by_year["2025"]["eps_yoy_pct"] == pytest.approx(-27.7, abs=0.1)


# ── Valuation vital with snapshot fallback ────────────────────────────


def _snap(rating: int) -> SnapshotItemResponse:
    """Build a minimal valuation snapshot at the given rating."""
    return SnapshotItemResponse(
        category="Price",
        rating=rating,
        metrics=[SnapshotMetricResponse(name="P/E", value="22.0")],
        full_report_available=True,
    )


def test_valuation_vital_no_dcf_uses_snapshot():
    """When DCF is missing, snapshot rating drives status."""
    result = _build_valuation_vital(
        current_price=100.0,
        fair_value=None,
        upside=None,
        valuation_snapshot=_snap(rating=5),
    )
    assert result["status"] == "underpriced"
    assert result["upside_potential"] == 10.0


def test_valuation_vital_no_dcf_no_snapshot_neutral():
    """No DCF + no snapshot → honest fair_value default."""
    result = _build_valuation_vital(
        current_price=100.0, fair_value=None, upside=None, valuation_snapshot=None,
    )
    assert result["status"] == "fair_value"
    assert result["upside_potential"] == 0.0


def test_valuation_vital_dcf_overrides_when_snapshot_agrees():
    """DCF + agreeing snapshot → status uses DCF, full upside preserved."""
    result = _build_valuation_vital(
        current_price=100.0,
        fair_value=140.0,
        upside=40.0,                      # +40% → deep_undervalued
        valuation_snapshot=_snap(rating=5),
    )
    assert result["status"] == "deep_undervalued"
    assert result["upside_potential"] == 40.0


def test_valuation_vital_snapshot_disagreement_softens_dcf():
    """DCF says deep_undervalued but multi-metric snapshot says overpriced
    → status downgrades one level milder rather than trusting stale DCF."""
    result = _build_valuation_vital(
        current_price=100.0,
        fair_value=140.0,
        upside=40.0,                      # DCF: deep_undervalued (level 4)
        valuation_snapshot=_snap(rating=2),  # Snapshot: overpriced (level 1) → diff 3
    )
    # diff 3 ≥ 2 → downgrade one level toward snapshot → underpriced (level 3)
    assert result["status"] == "underpriced"
    # Upside numeric stays as-is so the user sees the DCF-implied number.
    assert result["upside_potential"] == 40.0


# ── Forecast: every projection is a forecast ──────────────────────────


def test_forecast_all_entries_are_forecasts():
    """FMP `analyst-estimates` is forward-looking only. Every entry must be
    flagged is_forecast=True — the iOS chart relies on this for styling
    and the prior `i > 0` rule miscategorized the oldest bar as 'actual'.
    """
    estimates = [
        {"date": "2026-12-31", "estimatedRevenueAvg": 120e9, "estimatedEpsAvg": 4.5},
        {"date": "2027-12-31", "estimatedRevenueAvg": 132e9, "estimatedEpsAvg": 5.1},
        {"date": "2028-12-31", "estimatedRevenueAvg": 145e9, "estimatedEpsAvg": 6.2},
    ]
    result = _build_revenue_forecast_partial(estimates, 15.0, 18.0)
    assert all(p["is_forecast"] is True for p in result["projections"])


# ── Price action: empty-state honesty ─────────────────────────────────


def test_price_action_no_history_returns_empty_array():
    """Without real history, prices must be [] — never a synthetic flat
    line at current_price. The iOS chart skips render on empty input."""
    result = _build_price_action(
        recent_prices=[], current_price=142.0, earnings_dates=[],
    )
    assert result["prices"] == []
    assert result["event"] is None


# ── Wall Street: last chart point pinned to current_price ─────────────


def test_wall_street_last_chart_point_equals_current_price():
    """The blue line in the Wall Street chart must terminate exactly at
    `current_price`. Otherwise the line ends at the prior month's close
    while the iOS `$<currentPrice>` badge sits at currentPrice.y, leaving
    a visible gap."""
    monthly_prices = [
        {"month": "06/2025", "price": 130.5},
        {"month": "07/2025", "price": 133.1},
        {"month": "08/2025", "price": 135.0},
        {"month": "09/2025", "price": 138.2},
        {"month": "10/2025", "price": 140.0},
        {"month": "11/2025", "price": 141.5},
        {"month": "12/2025", "price": 139.8},
        {"month": "01/2026", "price": 140.6},
        {"month": "02/2026", "price": 141.0},
        {"month": "03/2026", "price": 141.7},
        {"month": "04/2026", "price": 140.2},
        {"month": "05/2026", "price": 138.9},  # last full month close
    ]
    _, consensus = _build_wall_street_sections(
        analyst=None,
        holders=None,
        current_price=142.82,
        fair_value=None,
        monthly_prices=monthly_prices,
    )

    # `hedge_fund_price_data` = FMP 13F institutional data (UI label "Institutions")
    points = consensus["hedge_fund_price_data"]
    assert len(points) == 12
    # Last point's price must equal current_price (rounded to 2dp), not the
    # prior month's close (138.9). All other months remain untouched.
    assert points[-1]["price"] == 142.82
    assert points[-1]["month"] == "05/2026"
    assert points[0]["price"] == 130.5
    # Cents preserved — was rounded to whole dollars before Fix C.
    assert consensus["current_price"] == 142.82


def test_wall_street_empty_monthly_prices_is_safe():
    """No monthly history → empty hf_price_data; pin should no-op."""
    _, consensus = _build_wall_street_sections(
        analyst=None,
        holders=None,
        current_price=142.0,
        fair_value=None,
        monthly_prices=[],
    )
    assert consensus["hedge_fund_price_data"] == []


# ── Insider: Informative-only filter (parity with Holders tab) ────────


def _trade(
    *,
    transaction_type: str,
    days_ago: int = 30,
    securities: float = 1000,
    price: float = 100.0,
    security_name: str = "Common Stock",
    acq_disp: str = "",
) -> dict:
    """Build a minimal FMP insider-trade row for the Form-4 fields the
    classifier and aggregator look at."""
    when = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%d")
    return {
        "transactionType": transaction_type,
        "transactionDate": when,
        "securitiesTransacted": securities,
        "price": price,
        "securityName": security_name,
        "acquisitionOrDisposition": acq_disp,
    }


def test_insider_filter_drops_uninformative_award():
    """A-Award rows are uninformative (compensation grant, not a
    sentiment signal). Must NOT count as a buy."""
    trades = [_trade(transaction_type="A-Award")]
    insider, _ = _build_insider_sections(trades)
    counts = {t["type"]: t["count"] for t in insider["transactions"]}
    assert counts["Buys"] == 0
    assert counts["Sells"] == 0


def test_insider_filter_drops_uninformative_option_exercise_sale():
    """S-Sale+OE is a sale paired with an option exercise — mechanical
    compensation cash-out, not informative selling sentiment."""
    trades = [_trade(transaction_type="S-Sale+OE")]
    insider, _ = _build_insider_sections(trades)
    counts = {t["type"]: t["count"] for t in insider["transactions"]}
    assert counts["Sells"] == 0


def test_insider_filter_keeps_informative_purchase():
    """P-Purchase is an open-market buy — keep it."""
    trades = [_trade(transaction_type="P-Purchase", securities=5000, price=100.0)]
    insider, vital = _build_insider_sections(trades)
    counts = {t["type"]: t["count"] for t in insider["transactions"]}
    assert counts["Buys"] == 1
    assert insider["sentiment"] == "positive"
    assert vital["buy_count"] == 1


def test_insider_filter_keeps_informative_pure_sale():
    """S-Sale (no +OE/+DIS) is a pure open-market sale — keep it."""
    trades = [_trade(transaction_type="S-Sale", securities=5000, price=100.0)]
    insider, vital = _build_insider_sections(trades)
    counts = {t["type"]: t["count"] for t in insider["transactions"]}
    assert counts["Sells"] == 1
    assert insider["sentiment"] == "negative"
    assert vital["sell_count"] == 1


def test_insider_filter_drops_non_common_stock():
    """RSU rows are excluded by the securityName guard — matches
    HoldersService Smart Money pipeline."""
    trades = [
        _trade(transaction_type="P-Purchase", security_name="RSU"),
        _trade(transaction_type="S-Sale", security_name="Stock Option"),
    ]
    insider, _ = _build_insider_sections(trades)
    counts = {t["type"]: t["count"] for t in insider["transactions"]}
    assert counts["Buys"] == 0
    assert counts["Sells"] == 0


def test_insider_filter_mixed_set():
    """Mixed informative + uninformative — only the informative subset
    survives, and the buy/sell counts match the Holders tab convention."""
    trades = [
        _trade(transaction_type="P-Purchase", securities=2000, price=100),  # Inf Buy
        _trade(transaction_type="S-Sale", securities=1000, price=100),      # Inf Sell
        _trade(transaction_type="A-Award", securities=10000),               # Uninf
        _trade(transaction_type="M-Exempt", securities=5000),               # Uninf
        _trade(transaction_type="S-Sale+OE", securities=3000, price=100),   # Uninf
        _trade(transaction_type="F-Tax", securities=500, price=100),        # Uninf
    ]
    insider, vital = _build_insider_sections(trades)
    counts = {t["type"]: t["count"] for t in insider["transactions"]}
    assert counts["Buys"] == 1
    assert counts["Sells"] == 1
    assert vital["buy_count"] == 1
    assert vital["sell_count"] == 1


def test_insider_window_excludes_old_trades():
    """The 12-month window must drop trades older than the cutoff even when
    they pass the informative filter."""
    trades = [_trade(transaction_type="P-Purchase", days_ago=400)]
    insider, _ = _build_insider_sections(trades)
    counts = {t["type"]: t["count"] for t in insider["transactions"]}
    assert counts["Buys"] == 0


# ── Price Movement: news catalyst classifier ──────────────────────────


@pytest.mark.parametrize("title,expected_tag", [
    ("Pfizer announces FDA approval for new cancer drug", "FDA Approval"),
    ("Biotech receives complete response letter from FDA", "FDA Rejection"),
    ("Company agreed to be acquired by private equity firm",  "M&A"),
    ("Morgan Stanley raises price target on AAPL to $250", "Analyst Upgrade"),
    ("Goldman cuts price target citing slowing growth", "Analyst Downgrade"),
    ("Q3 results: company raises full-year guidance", "Guidance Raised"),
    ("Management lowers forecast as China demand slows", "Guidance Cut"),
    ("Company discloses SEC investigation into its accounting", "Legal/Regulatory"),
    # Routine plaintiff-firm litigation PR is noise (and lagging) — NOT tagged.
    ("Class action lawsuit filed over alleged disclosure failures", None),
    ("Board authorizes $10B share buyback program", "Buyback"),
    ("Special dividend of $2/share declared", "Dividend"),
    ("Tech giant announces 15,000 layoffs", "Layoffs"),
    ("Company reports Q3 earnings and reaffirms targets", None),  # no match
    ("", None),
])
def test_news_catalyst_classifier_keyword_matches(title, expected_tag):
    """Each catalyst tag has at least one keyword that triggers it; non-
    matching headlines return None so the price-action builder skips
    them rather than picking up noise."""
    assert _classify_news_catalyst(title, "") == expected_tag


def test_news_catalyst_classifier_uses_text_when_title_misses():
    """Some FMP feeds have stub titles with the catalyst phrase only in
    the body — make sure body matching still triggers."""
    assert _classify_news_catalyst(
        "Press release", "The board has authorized a $5B stock repurchase",
    ) == "Buyback"


# ── Price Movement: news-vs-earnings priority rule ────────────────────


def _today() -> "datetime.date":
    return datetime.now(timezone.utc).date()


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).strftime("%Y-%m-%d")


def test_news_catalyst_dominates_when_move_is_larger():
    """Earnings move 1%, FDA-approval move 5% → catalyst wins."""
    # Build a 20-point chart where the FDA-approval day has a sharp jump
    # and the earnings day is flat.
    prices = [100.0] * 20
    # Earnings happened ~10 days ago — flat ±0.2% around that index.
    prices[8] = 100.0
    prices[9] = 100.2
    prices[10] = 100.1  # earnings index
    prices[11] = 100.3
    # FDA approval ~3 days ago — sharp 5% jump.
    prices[15] = 100.0
    prices[16] = 100.1
    prices[17] = 100.5  # catalyst index
    prices[18] = 105.5  # +5% next day
    prices[19] = 106.0

    news = [{
        "title": "Company receives FDA approval for lead drug",
        "text": "",
        "publishedDate": _days_ago(3),
        "site": "Reuters",
        "url": "http://x",
    }]
    earnings_dates = [_days_ago(10)]

    result = _build_price_action(prices, 106.0, earnings_dates, news)
    assert result["event"]["tag"] == "FDA Approval"


def test_earnings_dominates_when_tied_with_catalyst():
    """When both moves are equal magnitude, earnings wins (the date is
    higher-confidence than headline keyword matching)."""
    prices = [100.0] * 20
    # Earnings ~10 days ago: +5% jump.
    prices[8] = 100.0
    prices[9] = 100.0
    prices[10] = 100.0
    prices[11] = 105.0
    # M&A headline ~3 days ago: also +5% jump.
    prices[15] = 100.0
    prices[16] = 100.0
    prices[17] = 100.0
    prices[18] = 105.0
    prices[19] = 106.0

    news = [{
        "title": "Company agreed to be acquired by rival",
        "text": "",
        "publishedDate": _days_ago(3),
    }]
    earnings_dates = [_days_ago(10)]

    result = _build_price_action(prices, 106.0, earnings_dates, news)
    assert result["event"]["tag"] in ("Earnings Beat", "Earnings Miss", "Earnings Reaction")


def test_news_window_excludes_old_headlines():
    """Headlines older than 30 days must be ignored even if their
    keywords match — otherwise stale catalysts pollute today's chart."""
    prices = [100.0 + i * 0.1 for i in range(20)]
    news = [{
        "title": "Big FDA approval announced",
        "text": "",
        "publishedDate": _days_ago(60),  # outside window
    }]
    result = _build_price_action(prices, 102.0, [], news)
    assert result["event"] is None


def test_no_news_no_earnings_yields_no_event():
    """When neither source matches, event=None — iOS hides the marker."""
    prices = [100.0 + i * 0.1 for i in range(20)]
    result = _build_price_action(prices, 102.0, [], [])
    assert result["event"] is None
    assert result["_news_headlines"] == []


def test_existing_earnings_path_still_works_without_news():
    """Backward compatibility: when news=None, the earnings-only path
    behaves identically to before."""
    # Earnings 8 days ago → idx 11. Change is computed as
    # (prices[idx+1] - prices[idx-1]) / prices[idx-1], so we set
    # prices[10] (before) low and prices[12] (after) high to land a beat.
    prices = [100.0] * 20
    prices[10] = 100.0  # before
    prices[12] = 106.0  # after (+6% → Earnings Beat)

    earnings_dates = [_days_ago(8)]
    result = _build_price_action(prices, 106.0, earnings_dates, news=None)
    assert result["event"] is not None
    assert result["event"]["tag"] == "Earnings Beat"


def test_news_headlines_emitted_for_narrative_grounding():
    """The builder must surface matched headlines in `_news_headlines`
    so Stage B can ground the narrative — top 5 most recent."""
    prices = [100.0 + i * 0.1 for i in range(20)]
    news = [
        {
            "title": "Company raises full-year guidance",
            "text": "",
            "publishedDate": _days_ago(2),
            "site": "Reuters",
        },
        {
            "title": "Analyst upgrade on improving margins",
            "text": "",
            "publishedDate": _days_ago(7),
            "site": "Bloomberg",
        },
    ]
    result = _build_price_action(prices, 102.0, [], news)
    assert len(result["_news_headlines"]) == 2
    # Sorted most recent first.
    assert result["_news_headlines"][0]["tag"] == "Guidance Raised"
    assert result["_news_headlines"][1]["tag"] == "Analyst Upgrade"


def test_news_headlines_pydantic_silently_dropped():
    """`_news_headlines` is internal — Pydantic must not include it in
    the iOS-facing payload. Confirm by validating the partial through
    PriceActionResponse and asserting the field is gone."""
    from app.schemas.ticker_report import PriceActionResponse
    prices = [100.0] * 20
    news = [{
        "title": "Company raises full-year guidance",
        "text": "",
        "publishedDate": _days_ago(2),
    }]
    partial = _build_price_action(prices, 100.0, [], news)
    # Sanity: the partial dict carries the internal field.
    assert "_news_headlines" in partial
    # `narrative` is filled by Stage B in production; supply a value here
    # so we can validate the Pydantic round-trip in isolation.
    partial["narrative"] = "Test narrative."
    # The validated Pydantic model strips `_news_headlines` (extra='ignore'
    # is the v2 default).
    validated = PriceActionResponse(**partial).model_dump()
    assert "_news_headlines" not in validated


# ── Price Movement: catalyst significance gate (≥1σ to anchor) ────────


def test_normal_move_skips_reason_hunt_even_with_catalyst():
    """Significance is decided FIRST: when the move is within ±1σ
    ("Typical"), the section must NOT hunt for a reason at all — even with a
    real catalyst headline sitting in the window. event=None, NO headlines
    are surfaced (the scan never ran), and the chart shows the adaptive
    window + tier badge. Encodes the rule "detect a big move first, only
    THEN find the reason — not the opposite".

    60-day flat zig-zag → daily σ ≈ 1% (>=30 closes, so σ is computed).
    Current price is only +0.5% over the 30-day window → z ≈ 0.1 → not a
    big move → reason hunt skipped, even though an SEC-probe headline is
    present in the window."""
    prices = [100.0 if i % 2 == 0 else 101.0 for i in range(60)]
    news = [{
        "title": "Company discloses SEC investigation into its accounting",
        "text": "",
        "publishedDate": _days_ago(2),
        "site": "Reuters",
        "url": "http://x",
    }]
    result = _build_price_action(prices, 101.5, [], news)

    assert result["event"] is None
    assert result["window_label"].startswith("Last")
    assert result["tag"] == result["tier"]          # window path: tag = tier
    # The catalyst was never even scanned — the significance gate stayed shut.
    assert result["_news_headlines"] == []


def test_routine_lawsuit_pr_is_not_a_catalyst():
    """Plaintiff-firm litigation PR ("class action filed") is noise and a
    lagging symptom — it must NOT classify as a catalyst even when a real
    move coincides, so the section never blames a routine suit for a drop.
    Material legal events (SEC/DOJ/indictment/verdict) still do — see the
    classifier parametrize test."""
    prices = [100.0 if i % 2 == 0 else 101.0 for i in range(60)]
    news = [{
        "title": "Rosen Law Firm announces class action lawsuit against ACME",
        "text": "Investors who lost money may be entitled to compensation.",
        "publishedDate": _days_ago(1),
    }]
    # Even with a clear -8% move, no catalyst is attributed to the suit.
    result = _build_price_action(prices, 92.0, [], news)
    assert result["event"] is None
    assert result["_news_headlines"] == []


def test_significant_catalyst_keeps_badge():
    """A catalyst whose since-event move clears ±1σ keeps its badge and the
    "Since <date>" anchor — the section is in explain mode. Same flat σ ≈ 1%
    baseline, but the stock is +8% since the FDA-approval day (z ≈ 8)."""
    prices = [100.0 if i % 2 == 0 else 101.0 for i in range(60)]
    news = [{
        "title": "Company receives FDA approval for lead drug",
        "text": "",
        "publishedDate": _days_ago(1),
        "site": "Reuters",
        "url": "http://x",
    }]
    # idx for "1 day ago" = 58 (even → 100.0); current price 108 = +8%.
    result = _build_price_action(prices, 108.0, [], news)

    assert result["event"] is not None
    assert result["window_label"].startswith("Since")
    assert result["event"]["tag"] == "FDA Approval"
    assert result["tag"] == result["event"]["tag"]


# ── Price Movement: chart minimum span (no flat line) ─────────────────


def test_chart_widens_short_move_to_min_span():
    """A big move over a SHORT window must still render ~1 month of closes
    (not a flat ~5-point line), while the %/label keep reflecting the short
    detection window. Guards the chart-span ↔ detection-window decoupling."""
    # 87 flat days (tiny noise so σ > 0) + a sharp +10% jump over the last 3.
    prices = [100.0 + (0.1 if i % 2 else -0.1) for i in range(87)]
    prices += [108.0, 109.0, 110.0]
    result = _build_price_action(prices, 110.0, [], [])

    assert len(result["prices"]) >= 25                       # chart widened to ~1 month
    assert result["window_label"] in ("Last 7 Days", "Last 15 Days")  # short detection
    assert result["change_pct"] > 5                          # the real short-window move


def test_60day_window_now_selectable():
    """A move that's been building over ~2 months is now detectable as a
    60-day window (eval set extended 45 → 60) and charted at that span."""
    prices = [100.0] * 30
    prices += [100.0 + (i + 1) * (20.0 / 60.0) for i in range(60)]  # steady +20% over 60d
    result = _build_price_action(prices, prices[-1], [], [])

    assert result["window_label"] == "Last 60 Days"
    assert len(result["prices"]) >= 60                       # full 2-month span shown


# ── Price Movement: intraday (hourly) chart for short windows ─────────


def test_intraday_closes_reverses_to_chronological():
    from app.services.agents.ticker_report_data_collector import _intraday_closes
    # FMP returns newest-first; a None close is skipped.
    rows = [{"close": 3.0}, {"close": 2.0}, {"close": None}, {"close": 1.0}]
    assert _intraday_closes(rows) == [1.0, 2.0, 3.0]


class _FakeIntradayFMP:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def get_intraday_prices(self, ticker, interval="5min", from_date=None, to_date=None):
        self.calls += 1
        return self.rows


def _collector_with(rows):
    from app.services.agents.ticker_report_data_collector import TickerReportDataCollector
    return TickerReportDataCollector(fmp=_FakeIntradayFMP(rows))


def _pa_out(tier, change_days, prices, event=None):
    from app.services.agents.ticker_report_data_collector import CollectedTickerData
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.price_action_partial = {
        "tier": tier, "_change_days": change_days, "prices": prices, "event": event,
    }
    return out


@pytest.mark.asyncio
async def test_intraday_chart_swaps_short_window():
    """A short-window big move swaps the daily sparkline for HOURLY closes
    (intraday texture) and drops the daily-indexed event marker."""
    rows = [{"date": f"2026-06-{d:02d} {h}:00:00", "close": float(100 + d + h)}
            for d in range(1, 5) for h in (10, 11, 12, 13, 14)]  # 20 hourly bars
    collector = _collector_with(rows)
    out = _pa_out("Unusual", 5, [10.0, 11.0, 12.0],
                  event={"tag": "x", "date": "Jun 1", "index": 2})
    await collector._apply_intraday_chart(out)
    assert collector.fmp.calls == 1
    assert len(out.price_action_partial["prices"]) == len(rows)  # now hourly
    assert out.price_action_partial["event"] is None             # marker dropped


@pytest.mark.asyncio
async def test_intraday_chart_keeps_daily_when_sparse():
    """Too few hourly bars (e.g. FMP intraday history too short) → keep daily."""
    collector = _collector_with([{"close": 100.0}, {"close": 101.0}])  # < min points
    out = _pa_out("Unusual", 5, [10.0, 11.0, 12.0])
    await collector._apply_intraday_chart(out)
    assert out.price_action_partial["prices"] == [10.0, 11.0, 12.0]   # unchanged


@pytest.mark.asyncio
async def test_intraday_chart_skipped_for_long_window():
    """Long windows (>15d) stay daily — no intraday fetch at all."""
    collector = _collector_with([{"close": 1.0}] * 40)
    out = _pa_out("Unusual", 30, [10.0, 11.0])
    await collector._apply_intraday_chart(out)
    assert collector.fmp.calls == 0
    assert out.price_action_partial["prices"] == [10.0, 11.0]


@pytest.mark.asyncio
async def test_intraday_chart_skipped_for_typical_move():
    """Quiet (Typical) moves never fetch intraday."""
    collector = _collector_with([{"close": 1.0}] * 40)
    out = _pa_out("Typical", 5, [10.0, 11.0])
    await collector._apply_intraday_chart(out)
    assert collector.fmp.calls == 0


# ── Macro snapshot: shared cross-ticker cache ─────────────────────────


class _FakeMacroFMP:
    """Minimal FMP stand-in that counts calls, so we can prove the macro
    snapshot is fetched once and reused — never per-ticker. (Testing rules:
    no live FMP — inject inline.)"""

    def __init__(self) -> None:
        self.change_calls = 0
        self.quote_calls = 0

    async def get_stock_price_change(self, sym: str) -> dict:
        self.change_calls += 1
        return {"5D": 1.0, "1M": 2.0, "3M": 3.0, "1Y": 4.0}

    async def get_stock_price_quote(self, sym: str) -> dict:
        self.quote_calls += 1
        return {"price": 100.0}


@pytest.mark.asyncio
async def test_macro_snapshot_shared_across_tickers():
    """The market-wide macro snapshot is fetched ONCE and reused — a second
    ticker's report hits the shared cache with zero new FMP calls. Guards
    the cross-ticker efficiency optimization (FMP calls + latency)."""
    from app.services.agents.ticker_report_data_collector import (
        TickerReportDataCollector,
        _MACRO_SYMBOLS,
        _macro_snapshot_cache,
        _macro_snapshot_inflight,
    )
    _macro_snapshot_cache.clear()
    _macro_snapshot_inflight.clear()

    fake = _FakeMacroFMP()
    collector = TickerReportDataCollector(fmp=fake)

    first = await collector._fetch_macro_indicators()
    assert len(first) == len(_MACRO_SYMBOLS)
    cold_calls = fake.change_calls
    assert cold_calls == len(_MACRO_SYMBOLS)          # one cold fetch

    # A different ticker's report — must reuse the snapshot, not re-fetch.
    second = await collector._fetch_macro_indicators()
    assert second == first
    assert fake.change_calls == cold_calls            # zero extra FMP calls

    _macro_snapshot_cache.clear()
    _macro_snapshot_inflight.clear()


@pytest.mark.asyncio
async def test_macro_snapshot_dedups_concurrent_fetches():
    """A burst of concurrent reports must trigger ONE underlying fetch via
    the `_inflight` dedup, not one per caller (thundering-herd guard)."""
    from app.services.agents.ticker_report_data_collector import (
        TickerReportDataCollector,
        _MACRO_SYMBOLS,
        _macro_snapshot_cache,
        _macro_snapshot_inflight,
    )
    _macro_snapshot_cache.clear()
    _macro_snapshot_inflight.clear()

    class _SlowFakeFMP(_FakeMacroFMP):
        async def get_stock_price_change(self, sym: str) -> dict:
            await asyncio.sleep(0)  # yield so callers overlap before caching
            return await super().get_stock_price_change(sym)

    fake = _SlowFakeFMP()
    collector = TickerReportDataCollector(fmp=fake)

    results = await asyncio.gather(
        *[collector._fetch_macro_indicators() for _ in range(5)]
    )
    # Five concurrent callers → exactly one full fetch, not five.
    assert fake.change_calls == len(_MACRO_SYMBOLS)
    assert all(r == results[0] for r in results)

    _macro_snapshot_cache.clear()
    _macro_snapshot_inflight.clear()


# ── Sector aggregates: HHI math ───────────────────────────────────────


def test_hhi_perfect_monopoly_is_10000():
    """A single firm holding the entire market scores HHI=10000 — the
    upper bound. Used by the concentration enum mapping in PR 2."""
    assert compute_hhi([1_000_000.0]) == 10000.0


def test_hhi_equal_duopoly_is_5000():
    """Two firms with 50/50 share each score HHI = 50² + 50² = 5000."""
    assert compute_hhi([500.0, 500.0]) == 5000.0


def test_hhi_perfectly_competitive_is_low():
    """100 equally-sized firms → HHI = 100 × 1² = 100 (highly fragmented)."""
    caps = [10.0] * 100
    assert compute_hhi(caps) == 100.0


def test_hhi_skipped_zeros():
    """Zero-cap entries are skipped — they shouldn't dilute the index."""
    assert compute_hhi([100.0, 100.0, 0.0, 0.0]) == 5000.0


def test_hhi_empty_returns_zero():
    """No data → 0.0 (caller treats this as 'unknown', not 'fragmented')."""
    assert compute_hhi([]) == 0.0
    assert compute_hhi([0.0, 0.0]) == 0.0


# ── Sector aggregates: top-N share ────────────────────────────────────


def test_top_n_share_picks_largest():
    """Top-1 of [10, 90, 5] is 90 / 105 ≈ 85.7%."""
    assert compute_top_n_share([10.0, 90.0, 5.0], n=1) == 85.7


def test_top_2_combined_share():
    """Top-2 of [10, 90, 5] is (90 + 10) / 105 ≈ 95.2%."""
    assert compute_top_n_share([10.0, 90.0, 5.0], n=2) == 95.2


def test_top_n_share_empty_returns_zero():
    assert compute_top_n_share([], n=1) == 0.0


# ── Sector aggregates: revenue CAGR ───────────────────────────────────


def test_revenue_cagr_simple_growth():
    """100 → 200 over 4 years (5 data points spanning 2020-2024) → ~18.9% CAGR."""
    history = [
        {"ticker": "X", "revenues": [
            (2020, 100.0), (2021, 120.0), (2022, 140.0),
            (2023, 170.0), (2024, 200.0),
        ]},
    ]
    cagr = compute_revenue_cagr_5y(history)
    assert cagr is not None
    assert 18.0 < cagr < 20.0


def test_revenue_cagr_aggregates_across_constituents():
    """CAGR is on summed sector revenue, not per-ticker. Two tickers
    each going 100→200 in the same period should still yield the same
    CAGR as one ticker doing the same."""
    history = [
        {"ticker": "A", "revenues": [(2020, 100.0), (2024, 200.0)]},
        {"ticker": "B", "revenues": [(2020, 100.0), (2024, 200.0)]},
    ]
    cagr = compute_revenue_cagr_5y(history)
    assert cagr is not None
    assert 18.0 < cagr < 20.0


def test_revenue_cagr_handles_empty():
    assert compute_revenue_cagr_5y([]) is None
    assert compute_revenue_cagr_5y([{"ticker": "X", "revenues": []}]) is None


def test_revenue_cagr_negative_for_shrinking_sector():
    """Sectors in secular decline → negative CAGR. Don't clamp it; the
    `lifecycle_phase` mapping needs the real sign."""
    history = [
        {"ticker": "X", "revenues": [(2020, 200.0), (2024, 100.0)]},
    ]
    cagr = compute_revenue_cagr_5y(history)
    assert cagr is not None
    assert cagr < 0


# ── _build_market_dynamics: HHI / top-share / lifecycle mapping ───────


def _agg(**overrides) -> SectorAggregates:
    """Builder with sane defaults for the mapping tests."""
    base = dict(
        sector="Technology",
        total_revenue_usd=1_000_000_000_000.0,
        cagr_5yr_pct=8.0,
        hhi=1000.0,
        top1_share_pct=20.0,
        top2_share_pct=35.0,
        num_constituents=50,
        computed_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return SectorAggregates(**base)


def test_market_dynamics_falls_back_to_defaults_when_no_aggregates():
    """No batch + no peer profiles → honest placeholders. CAGR is None
    (not 0) so iOS renders "—" instead of misleading zeros; concentration
    defaults to `fragmented` because absence-of-data shouldn't look like
    presence-of-bad-data."""
    md = _build_market_dynamics({"industry": "Software"}, None)
    assert md["concentration"] == "fragmented"
    assert md["lifecycle_phase"] == "mature"
    assert md["cagr_5yr"] is None
    assert md["industry"] == "Software"


def test_market_dynamics_derives_concentration_from_peers_on_cache_miss():
    """When sector_aggregates is missing but peer profiles are in-hand,
    HHI / top-N concentration is computed inline so the UI still shows
    something better than the empty fallback. CAGR stays None because
    peer profiles alone don't carry historical revenue."""
    focal = {"industry": "Software", "mktCap": 800_000_000_000}
    peers = [
        {"mktCap": 3_000_000_000_000},   # one dominant peer
        {"mktCap": 200_000_000_000},
        {"mktCap": 100_000_000_000},
    ]
    md = _build_market_dynamics(focal, None, peers)
    # Top firm has ~72% of the (focal + peers) total MARKET CAP. That's cap
    # dominance, not a market-share monopoly, so the label is capped at
    # oligopoly (monopoly/duopoly are reserved for real share data).
    assert md["concentration"] == "oligopoly"
    assert md["cagr_5yr"] is None
    assert md["industry"] == "Software"


def test_market_dynamics_high_cap_concentration_caps_at_oligopoly():
    """A single firm with >50% of sector MARKET CAP is cap dominance, not a
    market-share monopoly — capped at oligopoly so a smaller constituent's
    report isn't mislabeled 'Monopoly'."""
    md = _build_market_dynamics({}, _agg(top1_share_pct=55.0, hhi=3500.0))
    assert md["concentration"] == "oligopoly"


def test_market_dynamics_top2_cap_concentration_caps_at_oligopoly():
    """Two firms with >70% of sector MARKET CAP → oligopoly (not duopoly),
    same market-cap-vs-market-share reasoning."""
    md = _build_market_dynamics({}, _agg(top1_share_pct=40.0, top2_share_pct=75.0, hhi=2900.0))
    assert md["concentration"] == "oligopoly"


def test_market_dynamics_oligopoly_when_hhi_high_but_no_dominant_firm():
    """No single/double dominator but HHI in the moderately-concentrated
    DOJ band → oligopoly."""
    md = _build_market_dynamics({}, _agg(top1_share_pct=25.0, top2_share_pct=45.0, hhi=1800.0))
    assert md["concentration"] == "oligopoly"


def test_market_dynamics_fragmented_when_hhi_low():
    md = _build_market_dynamics({}, _agg(top1_share_pct=5.0, top2_share_pct=10.0, hhi=400.0))
    assert md["concentration"] == "fragmented"


def test_market_dynamics_secular_growth_when_cagr_above_15():
    md = _build_market_dynamics({}, _agg(cagr_5yr_pct=18.0))
    assert md["lifecycle_phase"] == "secular_growth"


def test_market_dynamics_declining_when_cagr_negative():
    md = _build_market_dynamics({}, _agg(cagr_5yr_pct=-3.0))
    assert md["lifecycle_phase"] == "declining"


def test_market_dynamics_emerging_when_few_constituents():
    """Sectors with very few public constituents (e.g. brand-new niches)
    get the `emerging` label regardless of CAGR sign."""
    md = _build_market_dynamics({}, _agg(num_constituents=3, cagr_5yr_pct=5.0))
    assert md["lifecycle_phase"] == "emerging"


def test_market_dynamics_mature_default():
    md = _build_market_dynamics({}, _agg(cagr_5yr_pct=5.0, num_constituents=80))
    assert md["lifecycle_phase"] == "mature"


def test_market_dynamics_tam_remains_zero_until_pr3():
    """PR 2 keeps current_tam/future_tam at 0.0; PR 3 will fill them
    from 10-K MD&A extraction. iOS handles 0.0 as 'unavailable'."""
    md = _build_market_dynamics({}, _agg())
    assert md["current_tam"] == 0.0
    assert md["future_tam"] == 0.0


# ── _build_competitors: peer scoring + threat-level deltas ────────────


def _peer(symbol: str, name: str, mkt_cap: float, change: float = 0.0) -> dict:
    return {
        "symbol": symbol,
        "companyName": name,
        "mktCap": mkt_cap,
        "changes": change,
    }


def _default_ratios_for(peers: list) -> dict:
    """Minimal ratios for a list of peer profiles.

    Tests that exercise non-scoring behavior (floor, cap, sort, threat)
    use this so each peer ends up rankable (score_raw is not None).
    Each metric is set just above the sector-relative midpoint for the
    default test sector — that way peers land near a score of 5–6
    without any specific sector benchmarks supplied (falls back to
    absolute-threshold bands).
    """
    return {
        p["symbol"]: {
            "operatingProfitMargin": 0.20,
            "returnOnEquity": 0.15,
            "revenueGrowth": 0.10,
        }
        for p in peers
    }


def test_build_competitors_empty_when_no_peers():
    """No peers → empty list (don't fabricate competitors)."""
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=[],
    )
    assert out == []


def test_build_competitors_excludes_focal_ticker():
    """If the peer list mistakenly includes the focal ticker (FMP
    sometimes does), it must be filtered out — otherwise the company
    would compete with itself."""
    peers = [
        _peer("AAPL", "Apple", 3_500_000_000_000),  # focal — should be dropped
        _peer("MSFT", "Microsoft", 3_000_000_000_000),
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
        peer_ratios=_default_ratios_for(peers),
    )
    assert all(c["ticker"] != "AAPL" for c in out)
    assert len(out) == 1


def test_industry_universe_peers_sorts_by_mcap_and_excludes():
    """`_industry_universe_peers` returns same-industry tickers from
    the cached universe sorted by market cap desc, minus tickers in
    `exclude`. Stub the cache so the test doesn't depend on the live
    industry_universe.json file."""
    industry = "Test-Industry"
    # Pre-populate the cache as if the loader already ran. Order in
    # the cache reflects what `_load_industry_peers_from_universe`
    # would have produced (already mkt-cap sorted).
    _INDUSTRY_PEERS_CACHE[industry] = [
        "MSFT",  # largest
        "ORCL",
        "CRM",
        "NOW",
        "ADBE",
    ]
    try:
        # Caller passes focal ticker + already-known FMP peers in `exclude`.
        out = _industry_universe_peers(
            industry, exclude={"ORCL", "MSFT"}
        )
        # Returns the rest, in original (mkt-cap) order, capped at 20.
        assert out == ["CRM", "NOW", "ADBE"]

        # Empty industry → empty list, doesn't touch cache.
        assert _industry_universe_peers("", exclude=set()) == []

        # Unknown industry → empty list (loader would return []).
        # Stub to mimic the cached-miss-then-empty path.
        _INDUSTRY_PEERS_CACHE["No-Such-Industry"] = []
        assert _industry_universe_peers(
            "No-Such-Industry", exclude=set()
        ) == []
    finally:
        _INDUSTRY_PEERS_CACHE.pop(industry, None)
        _INDUSTRY_PEERS_CACHE.pop("No-Such-Industry", None)


def test_build_competitors_drops_micro_cap_misclassifications():
    """FMP's `stock-peers` endpoint sometimes returns obvious
    misclassifications (e.g., Helport AI listed as ORCL peer). The
    mkt-cap floor (max focal × 5%, $5B) drops them so the UI only shows
    real competitors."""
    peers = [
        _peer("MSFT", "Microsoft", 3_000_000_000_000),
        _peer("HPAI", "Helport AI", 30_000_000),  # micro-cap — should be dropped
    ]
    out = _build_competitors(
        my_ticker="ORCL",
        my_profile={"mktCap": 700_000_000_000},
        my_ratios=[], my_revenue_growth=None, peer_profiles=peers,
        peer_ratios=_default_ratios_for(peers),
    )
    tickers = [c["ticker"] for c in out]
    assert "HPAI" not in tickers
    assert "MSFT" in tickers


def test_build_competitors_caps_at_max_n():
    """When many peers pass the floor, the list is bounded at
    `_COMPETITOR_MAX_N` (5) — enough to surface real competitive depth
    without flooding the iOS card list. With absolute sector-relative
    scoring there's no min-max pathology forcing one peer to 10 or 0,
    so the cap is just a UI-list limit."""
    peers = [
        _peer(f"P{i}", f"Peer {i}", (10 - i) * 50_000_000_000)
        for i in range(10)  # 10 candidates all above $50B → all pass floor
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
        peer_ratios=_default_ratios_for(peers),
    )
    assert len(out) == 5


def test_build_competitors_returns_all_survivors_when_below_cap():
    """Variable count: when only e.g. 3 peers survive the floor, return
    all 3 — don't pad, don't truncate. This is the "dynamic" behavior
    so niche tickers show fewer competitors than mega-caps."""
    peers = [
        _peer("BIG1", "Big 1", 100_000_000_000),
        _peer("BIG2", "Big 2", 80_000_000_000),
        _peer("BIG3", "Big 3", 60_000_000_000),
        _peer("TINY", "Tiny", 100_000_000),  # below $5B floor → dropped
    ]
    out = _build_competitors(
        my_ticker="AAPL",
        my_profile={"mktCap": 500_000_000_000},
        my_ratios=[], my_revenue_growth=None, peer_profiles=peers,
        peer_ratios=_default_ratios_for(peers),
    )
    assert len(out) == 3
    assert {c["ticker"] for c in out} == {"BIG1", "BIG2", "BIG3"}


def test_build_competitors_threat_level_assigned():
    """Each competitor gets a threat_level enum value. With default
    ratios (all peers tied), threat_level lands as "moderate" — still
    a valid Swift enum member."""
    peers = [
        _peer("STRONG", "Strong Co", 100_000_000_000, change=10.0),
        _peer("AVG", "Average Co", 100_000_000_000, change=0.0),
        _peer("WEAK", "Weak Co", 100_000_000_000, change=-10.0),
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
        peer_ratios=_default_ratios_for(peers),
    )
    assert len(out) == 3
    assert all(c["threat_level"] in ("low", "moderate", "high") for c in out)


def test_build_competitors_competitive_scores_in_0_10_range():
    """Competitive scores must always land in [0, 10] (clamped by the
    iOS rendering layer otherwise)."""
    peers = [
        _peer("A", "A", 100_000_000_000, change=50.0),
        _peer("B", "B", 100_000_000_000, change=-50.0),
        _peer("C", "C", 100_000_000_000, change=0.0),
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
        peer_ratios=_default_ratios_for(peers),
    )
    for c in out:
        assert 0.0 <= c["competitive_score"] <= 10.0


def test_build_competitors_uses_peer_ratios_for_scoring():
    """When peer_ratios are provided, scoring uses real ratios — a peer
    with much higher margins/ROE earns a higher absolute competitive
    score than one with low margins/ROE, regardless of peer-set
    composition. Inputs deliberately omit ROIC so the absolute-path
    fallback (op_margin + ROE) is the codepath under test."""
    peers = [
        _peer("HIGHMARGIN", "High Margin Co", 100_000_000_000),
        _peer("LOWMARGIN", "Low Margin Co", 100_000_000_000),
    ]
    ratios = {
        "HIGHMARGIN": {"operatingProfitMargin": 0.40, "returnOnEquity": 0.35},
        "LOWMARGIN": {"operatingProfitMargin": 0.05, "returnOnEquity": 0.03},
    }
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers, peer_ratios=ratios,
    )
    scores = {c["ticker"]: c["competitive_score"] for c in out}
    assert scores["HIGHMARGIN"] > scores["LOWMARGIN"]


def test_build_competitors_drops_peer_with_all_none_ratios():
    """The Workday-style bug: a peer with no rankable ratio data
    (operatingProfitMargin/returnOnEquity/revenueGrowth all missing)
    must be dropped before min-max scaling. Otherwise it gets pinned
    to 0.0 and ships to iOS as "this real company is worthless" —
    a UX failure the user explicitly called out."""
    peers = [
        _peer("MSFT", "Microsoft", 3_000_000_000_000),
        _peer("WDAY", "Workday", 50_000_000_000),
    ]
    # MSFT has real ratios. WDAY has nothing — simulates FMP's
    # intermittent empty-ratios response.
    ratios = {
        "MSFT": {
            "operatingProfitMargin": 0.35,
            "returnOnEquity": 0.30,
            "revenueGrowth": 0.12,
            "revenue_ttm": 250_000_000_000,
        },
        # WDAY missing entirely
    }
    out = _build_competitors(
        my_ticker="ORCL",
        my_profile={"mktCap": 800_000_000_000},
        my_ratios=[], my_revenue_growth=None,
        peer_profiles=peers, peer_ratios=ratios,
    )
    tickers = [c["ticker"] for c in out]
    assert "WDAY" not in tickers
    assert "MSFT" in tickers
    # MSFT alone should not be pinned to 0.0 — single survivor → 5.0 neutral.
    assert out[0]["competitive_score"] > 0.0


# ── Absolute (sector-relative) competitive scoring ────────────────────


def test_absolute_peer_score_at_sector_median_returns_5():
    """A peer whose metrics match the sector medians exactly should
    land at the midpoint score (5.0). Sector medians are in raw
    fractional form for `operating_margin`/`roe` (the `is_fraction`
    convention); peer values are in percent form."""
    medians = {
        "operating_margin": 0.20,  # 20% as a fraction
        "roe":              0.15,  # 15%
        "revenue_yoy":      10.0,  # already a percent
    }
    score = _absolute_peer_score(20.0, 15.0, 10.0, medians)
    assert score == 5.0


def test_absolute_peer_score_at_2x_median_returns_10():
    """A peer at 2× sector median caps at the top of the 0-10 range."""
    medians = {
        "operating_margin": 0.20,
        "roe":              0.15,
        "revenue_yoy":      10.0,
    }
    score = _absolute_peer_score(40.0, 30.0, 20.0, medians)
    assert score == 10.0


def test_absolute_peer_score_at_zero_with_positive_median_returns_0():
    """A peer with zero on every metric while the sector median is
    positive should bottom at 0.0."""
    medians = {
        "operating_margin": 0.20,
        "roe":              0.15,
        "revenue_yoy":      10.0,
    }
    score = _absolute_peer_score(0.0, 0.0, 0.0, medians)
    assert score == 0.0


def test_absolute_peer_score_falls_back_to_threshold_bands_when_medians_missing():
    """When sector benchmarks are unavailable (empty dict), the score
    falls back to the absolute-threshold bands. A peer at
    op_margin=20%, roe=15%, growth=10% — landing at the "5" anchor of
    each band — should still come back near 5.0."""
    score = _absolute_peer_score(20.0, 15.0, 10.0, {})
    assert score is not None
    assert 4.5 <= score <= 5.5


def test_absolute_threshold_fallback_clamps_outside_bands():
    """Values above the top band map to 10, below the bottom band map
    to 0 — the iOS UI never sees out-of-range numbers."""
    assert _absolute_threshold_fallback("operating_margin", 100.0) == 10.0
    assert _absolute_threshold_fallback("operating_margin", -50.0) == 0.0
    assert _absolute_threshold_fallback("revenue_yoy", 100.0) == 10.0


# ── Relative (ROIC-delta + moat-multiplier) competitive scoring ───────


def test_moat_multiplier_neutral_when_unknown():
    """Cache miss on peer moat → 1.0× multiplier, neither boost nor
    penalty. Guarantees that peers without a cached moat score don't
    silently get dragged toward zero."""
    assert _moat_multiplier(None) == 1.0


def test_moat_multiplier_endpoints():
    """0 moat → 0.7×, 10 moat → 1.3×, 5 moat → 1.0× (neutral anchor)."""
    assert _moat_multiplier(0.0) == pytest.approx(0.7)
    assert _moat_multiplier(5.0) == pytest.approx(1.0)
    assert _moat_multiplier(10.0) == pytest.approx(1.3)


def test_relative_peer_score_equal_roic_neutral_moat_is_5():
    """Peer matches focal on ROIC and has neutral moat (5.0) → 5.0
    score, the "equal threat" anchor."""
    score = _relative_peer_score(
        peer_roic=0.15, focal_roic=0.15, peer_moat_avg=5.0,
    )
    assert score == pytest.approx(5.0)


def test_relative_peer_score_plus_10pp_roic_neutral_rank_lands_in_high_band():
    """Peer beats focal by +10pp ROIC, high moat (8.0), no Gemini rank
    passed (so directness defaults to 5.0 neutral).

    Math (new 15pp scale + 60/40 directness blend):
      financial = 5 + (10/15)×5 = 8.33
      directness = 5.0 (neutral)
      blended = 0.6×5.0 + 0.4×8.33 = 6.33
      moat_mult = 0.7 + 0.8×0.6 = 1.18
      score = 6.33 × 1.18 ≈ 7.47 → above 7.0 High threshold.
    """
    score = _relative_peer_score(
        peer_roic=0.25, focal_roic=0.15, peer_moat_avg=8.0,
    )
    assert score is not None
    assert score == pytest.approx(7.47, abs=0.05)
    assert score >= 7.0


def test_relative_peer_score_top_rank_plus_high_roic_high_moat_caps_at_10():
    """Worst-case threat: Gemini ranks peer #1, peer's ROIC is +10pp,
    and peer has a wide moat. All three signals point to maximum threat;
    the blended math overshoots 10 and the final clamp catches it.

    Math:
      financial = 5 + (10/15)×5 = 8.33
      directness = 10.0 (rank 1 of n)
      blended = 0.6×10.0 + 0.4×8.33 = 9.33
      moat_mult = 1.18
      product = 11.0 → clamped to 10.0
    """
    score = _relative_peer_score(
        peer_roic=0.25, focal_roic=0.15, peer_moat_avg=8.0,
        gemini_rank=1, n_peers=5,
    )
    assert score == pytest.approx(10.0)


def test_relative_peer_score_last_rank_minus_5pp_roic_low_moat_is_low_threat():
    """Peer trails focal by 5pp ROIC, weak moat (2.0), AND is Gemini's
    last pick — all three signals point to low threat.

    Math:
      financial = 5 + (-5/15)×5 = 3.33
      directness = 10×(5−5+1)/5 = 2.0 (rank 5 of 5)
      blended = 0.6×2.0 + 0.4×3.33 = 2.53
      moat_mult = 0.7 + 0.2×0.6 = 0.82
      score ≈ 2.07 — well under the 3.0 Low threshold.
    """
    score = _relative_peer_score(
        peer_roic=0.10, focal_roic=0.15, peer_moat_avg=2.0,
        gemini_rank=5, n_peers=5,
    )
    assert score is not None
    assert score == pytest.approx(2.07, abs=0.05)
    assert score <= 3.0  # threat-label cutoff


def test_relative_peer_score_returns_none_when_either_roic_missing():
    """A missing ROIC on either side disables the relative path so
    `_build_competitors` can fall back to the absolute composite
    rather than fabricate a 5.0 anchor for a coverage gap."""
    assert _relative_peer_score(None, 0.15, 5.0) is None
    assert _relative_peer_score(0.15, None, 5.0) is None
    assert _relative_peer_score(None, None, 5.0) is None


def test_relative_peer_score_extreme_negative_delta_clamped_above_zero():
    """A 50pp ROIC trail (peer 5% vs focal 55%) drives the financial
    component to its 0.0 floor. With neutral directness (no rank) the
    blended math still lands above zero because directness contributes
    60% of the pre-multiplier score — but the high-moat multiplier
    can't lift the result above the moderate band.

    Math:
      financial = clamp(5 + (-50/15)×5, 0, 10) = 0.0 (clamped)
      directness = 5.0 (neutral, no rank)
      blended = 0.6×5.0 + 0.4×0.0 = 3.0
      moat_mult = 0.7 + 1.0×0.6 = 1.3
      score = 3.0 × 1.3 = 3.9
    Demonstrates the floor still works (financial=0 doesn't make the
    whole score 0 because of directness) and that the formula is
    stable under extreme inputs.
    """
    score = _relative_peer_score(
        peer_roic=0.05, focal_roic=0.55, peer_moat_avg=10.0,
    )
    assert score == pytest.approx(3.9, abs=0.05)


def test_directness_from_rank_top_pick_is_10():
    """Rank 1 (Gemini's top pick) maps to 10.0 directness — the most
    central competitor per grounded research."""
    assert _directness_from_rank(1, 5) == pytest.approx(10.0)
    assert _directness_from_rank(1, 7) == pytest.approx(10.0)


def test_directness_from_rank_last_pick_is_10_over_n():
    """Rank n (Gemini's last pick) maps to 10/n — non-zero but minimal."""
    assert _directness_from_rank(5, 5) == pytest.approx(2.0)
    assert _directness_from_rank(7, 7) == pytest.approx(10 / 7, abs=0.01)


def test_directness_from_rank_neutral_when_missing():
    """Missing rank or zero n_peers (Phase 1 fallback, or peers we can't
    position in Gemini's list) → 5.0 neutral anchor, not a silent
    penalty."""
    assert _directness_from_rank(None, 5) == 5.0
    assert _directness_from_rank(3, 0) == 5.0
    assert _directness_from_rank(None, 0) == 5.0


def test_directness_from_rank_clamps_out_of_range():
    """Rank > n or rank < 1 gets clamped — defensive against caller
    bugs without silently producing weird values."""
    assert _directness_from_rank(0, 5) == pytest.approx(10.0)   # clamps to rank 1
    assert _directness_from_rank(10, 5) == pytest.approx(2.0)   # clamps to rank 5
    assert _directness_from_rank(-3, 5) == pytest.approx(10.0)  # negatives → rank 1


def test_relative_peer_score_rank_outweighs_higher_roic_at_60pct_blend():
    """The key end-to-end claim of the blend: a peer Gemini ranks #1
    with neutral ROIC should outscore a peer Gemini ranks last with
    much higher ROIC. Captures the user-facing intent ("MSFT is the
    bigger competitor even though GOOGL has higher ROIC").

    Peer A (Gemini #1, equal ROIC, neutral moat):
      financial = 5.0, directness = 10.0
      blended = 0.6×10 + 0.4×5 = 8.0
      score = 8.0 × 1.0 = 8.0  → High
    Peer B (Gemini last, +10pp ROIC, neutral moat):
      financial = 8.33, directness = 2.0
      blended = 0.6×2 + 0.4×8.33 = 4.53
      score = 4.53 × 1.0 = 4.53  → Moderate
    """
    score_top = _relative_peer_score(
        peer_roic=0.15, focal_roic=0.15, peer_moat_avg=5.0,
        gemini_rank=1, n_peers=5,
    )
    score_last = _relative_peer_score(
        peer_roic=0.25, focal_roic=0.15, peer_moat_avg=5.0,
        gemini_rank=5, n_peers=5,
    )
    assert score_top is not None and score_last is not None
    assert score_top > score_last
    assert score_top == pytest.approx(8.0, abs=0.05)
    assert score_last == pytest.approx(4.53, abs=0.05)
    assert score_top >= 7.0    # High threat
    assert score_last < 7.0    # Moderate


def test_build_competitors_uses_relative_path_when_roic_present():
    """End-to-end: when both focal and peer have ROIC, _build_competitors
    uses the ROIC-relative path with moat-as-multiplier. Peer matches
    focal on ROIC with neutral moat → ~5.0 score → "moderate" threat,
    even when the peer's sector-relative absolute composite would have
    produced a much higher number."""
    peers = [
        {**_peer("PEER", "Peer Co", 100_000_000_000), "sector": "Technology"},
    ]
    # Peer crushes sector medians on op_margin/ROE/growth — would score
    # ~10 on the absolute path. But ROIC matches focal, so the relative
    # path should anchor at 5.0 with neutral moat.
    ratios = {
        "PEER": {
            "operatingProfitMargin": 0.40,
            "returnOnEquity": 0.35,
            "revenueGrowth": 0.20,
            "returnOnCapitalEmployed": 0.15,
        },
    }
    medians = {
        "Technology": {"operating_margin": 0.10, "roe": 0.10, "revenue_yoy": 5.0},
    }
    out = _build_competitors(
        my_ticker="FOCAL",
        my_profile={"sector": "Technology"},
        my_ratios=[{
            "operatingProfitMargin": 0.10, "returnOnEquity": 0.10,
            "returnOnCapitalEmployed": 0.15,
        }],
        my_revenue_growth=5.0,
        peer_profiles=peers, peer_ratios=ratios,
        sector_medians_by_sector=medians,
        peer_moats={},  # cache miss → neutral
    )
    assert len(out) == 1
    assert out[0]["competitive_score"] == pytest.approx(5.0, abs=0.1)
    assert out[0]["threat_level"] == "moderate"


def test_build_competitors_falls_back_to_absolute_when_focal_roic_missing():
    """If focal lacks ROIC, every peer scores via the absolute composite
    regardless of whether the peer has ROIC. Peer is dropped only if
    both paths come back None."""
    peers = [_peer("MSFT", "Microsoft", 3_000_000_000_000)]
    ratios = {
        "MSFT": {
            "operatingProfitMargin": 0.35,
            "returnOnEquity": 0.30,
            "revenueGrowth": 0.12,
            "returnOnCapitalEmployed": 0.20,  # peer has ROIC
        },
    }
    # Focal has op_margin/ROE/growth but NO ROIC → relative path returns
    # None for every peer, absolute path takes over.
    out = _build_competitors(
        my_ticker="ORCL",
        my_profile={"mktCap": 800_000_000_000},
        my_ratios=[{"operatingProfitMargin": 0.30, "returnOnEquity": 0.25}],
        my_revenue_growth=10.0,
        peer_profiles=peers, peer_ratios=ratios,
    )
    assert len(out) == 1
    # MSFT scores high on the absolute path against generic bands.
    assert out[0]["competitive_score"] > 0.0


def test_build_competitors_peer_moats_amplify_threat_score():
    """End-to-end: two peers with identical ROIC delta vs focal and
    identical neutral directness (no Gemini ranks passed). The one with
    higher cached moat should score higher because the durability
    multiplier scales up its threat score.

    Math (new 15pp scale, 60/40 blend, neutral directness):
      financial   = 5 + (5/15)×5 = 6.67
      directness  = 5.0 (neutral, no rank)
      blended     = 0.6×5.0 + 0.4×6.67 = 5.67
      MOATY moat=9.0  → mult 1.24 → score ≈ 7.03 (just above 7.0 High)
      NOMOAT moat=2.0 → mult 0.82 → score ≈ 4.65 (Moderate)
    """
    peers = [
        _peer("MOATY",   "Moaty Co",   100_000_000_000),
        _peer("NOMOAT",  "No Moat Co", 100_000_000_000),
    ]
    # Both peers beat focal by +5pp ROIC.
    ratios = {
        "MOATY":  {"returnOnCapitalEmployed": 0.20},
        "NOMOAT": {"returnOnCapitalEmployed": 0.20},
    }
    out = _build_competitors(
        my_ticker="FOCAL", my_profile={"mktCap": 800_000_000_000},
        my_ratios=[{"returnOnCapitalEmployed": 0.15}],
        my_revenue_growth=None,
        peer_profiles=peers, peer_ratios=ratios,
        peer_moats={"MOATY": 9.0, "NOMOAT": 2.0},
    )
    scores = {c["ticker"]: c["competitive_score"] for c in out}
    assert scores["MOATY"] > scores["NOMOAT"]
    assert scores["MOATY"] >= 7.0  # high threat under new threshold
    assert scores["NOMOAT"] < 7.0  # not high


def test_build_competitors_gemini_rank_shifts_order():
    """End-to-end: two peers identical on ROIC, moat, mkt cap. The one
    Gemini ranks #1 should score higher than the one ranked last —
    captures the user-facing fix where MSFT (Gemini's #1 for ORCL)
    must lead even when GOOGL has higher ROIC.
    """
    peers = [
        _peer("TOP",  "Top Pick",  100_000_000_000),
        _peer("LAST", "Last Pick", 100_000_000_000),
    ]
    # Identical ROIC vs focal (equal financial score = 5.0).
    ratios = {
        "TOP":  {"returnOnCapitalEmployed": 0.15},
        "LAST": {"returnOnCapitalEmployed": 0.15},
    }
    out = _build_competitors(
        my_ticker="FOCAL", my_profile={"mktCap": 800_000_000_000},
        my_ratios=[{"returnOnCapitalEmployed": 0.15}],
        my_revenue_growth=None,
        peer_profiles=peers, peer_ratios=ratios,
        peer_moats={"TOP": 5.0, "LAST": 5.0},
        peer_ranks={"TOP": 1, "LAST": 5},
        n_total_peers=5,
    )
    scores = {c["ticker"]: c["competitive_score"] for c in out}
    # TOP: directness=10, financial=5, blended=7, moat=1.0 → 7.0 High
    # LAST: directness=2, financial=5, blended=3.2, moat=1.0 → 3.2 Mod
    assert scores["TOP"] > scores["LAST"]
    assert scores["TOP"] >= 7.0
    assert scores["LAST"] < 7.0
    # Output sorts by score desc, so TOP renders first on iOS.
    assert out[0]["ticker"] == "TOP"


def test_build_competitors_sector_relative_scoring_uses_passed_medians():
    """When `sector_medians_by_sector` is supplied, each peer is scored
    against ITS OWN sector's medians (not the focal's). A peer in a
    weak sector that beats its sector by a lot can outscore a peer in
    a strong sector that's average for theirs."""
    peers = [
        # Strong peer in Technology (median op_margin 25%, roe 20%)
        {**_peer("STRONG_TECH", "Strong Tech", 100_000_000_000), "sector": "Technology"},
        # Average peer in Consumer (median op_margin 8%, roe 12%) —
        # peer is at sector median = score ~5
        {**_peer("AVG_CONSUMER", "Avg Consumer", 100_000_000_000), "sector": "Consumer Cyclical"},
    ]
    ratios = {
        "STRONG_TECH":  {"operatingProfitMargin": 0.45, "returnOnEquity": 0.40, "revenueGrowth": 0.20},
        "AVG_CONSUMER": {"operatingProfitMargin": 0.08, "returnOnEquity": 0.12, "revenueGrowth": 0.10},
    }
    medians = {
        "Technology":        {"operating_margin": 0.25, "roe": 0.20, "revenue_yoy": 10.0},
        "Consumer Cyclical": {"operating_margin": 0.08, "roe": 0.12, "revenue_yoy": 10.0},
    }
    # Focal has no mktCap → floor falls back to the $5B absolute, so
    # the $100B peers easily survive.
    out = _build_competitors(
        my_ticker="AAPL", my_profile={},
        my_ratios=[], my_revenue_growth=None,
        peer_profiles=peers, peer_ratios=ratios,
        sector_medians_by_sector=medians,
    )
    scores = {c["ticker"]: c["competitive_score"] for c in out}
    # STRONG_TECH beats its sector median substantially → high score
    assert scores["STRONG_TECH"] >= 8.0
    # AVG_CONSUMER sits AT its sector medians → near 5.0
    assert 4.5 <= scores["AVG_CONSUMER"] <= 5.5


def test_build_competitors_focal_growth_does_not_clamp_at_50():
    """The previous bug: `my_revenue_growth` (already in percent form)
    was multiplied by 100 again inside `_build_competitors`, pushing
    the focal's growth contribution to the clamp ceiling and inflating
    its raw score — which then forced every peer to "Low" threat. The
    fix removes that ×100; growth of 15% should pass through unchanged
    and result in at least one peer earning a non-"Low" threat."""
    peers = [
        _peer("STRONG", "Strong Co", 100_000_000_000),
    ]
    ratios = {
        # Strong peer easily exceeds sector medians on all three metrics
        "STRONG": {"operatingProfitMargin": 0.40, "returnOnEquity": 0.35, "revenueGrowth": 0.20},
    }
    medians = {
        "Technology": {"operating_margin": 0.20, "roe": 0.15, "revenue_yoy": 10.0},
    }
    # Focal at sector median (would score 5.0). If the old bug were
    # back, focal_score would inflate to ~10 and the peer at ~8 would
    # show "low" threat (delta = 8 - 10 = -2). No mktCap on focal so
    # the floor falls to the $5B absolute and the peer survives.
    out = _build_competitors(
        my_ticker="AAPL",
        my_profile={"sector": "Technology"},
        my_ratios=[{"operatingProfitMargin": 0.20, "returnOnEquity": 0.15}],
        my_revenue_growth=10.0,   # already percent
        peer_profiles=[{**peers[0], "sector": "Technology"}],
        peer_ratios=ratios,
        sector_medians_by_sector=medians,
    )
    # Single strong peer should be "high" threat, not "low".
    assert out[0]["threat_level"] == "high"


def test_build_competitors_sorted_by_competitive_score_desc():
    """The output list is sorted by competitive_score desc — the
    strongest threat renders first, matching the visual hierarchy on
    iOS."""
    peers = [
        _peer("WEAK",   "Weak Co",   100_000_000_000),
        _peer("MID",    "Mid Co",    100_000_000_000),
        _peer("STRONG", "Strong Co", 100_000_000_000),
    ]
    ratios = {
        "STRONG": {"operatingProfitMargin": 0.40, "returnOnEquity": 0.35, "revenueGrowth": 0.20},
        "MID":    {"operatingProfitMargin": 0.20, "returnOnEquity": 0.15, "revenueGrowth": 0.10},
        "WEAK":   {"operatingProfitMargin": 0.05, "returnOnEquity": 0.03, "revenueGrowth": 0.02},
    }
    out = _build_competitors(
        my_ticker="AAPL", my_profile={},
        my_ratios=[], my_revenue_growth=None,
        peer_profiles=peers, peer_ratios=ratios,
    )
    tickers_in_order = [c["ticker"] for c in out]
    assert tickers_in_order == ["STRONG", "MID", "WEAK"]


def test_build_competitors_emits_zero_market_share_for_schema_compat():
    """Market Share is no longer rendered on iOS, but the DTO field is
    kept in the schema for backwards compatibility with cached
    reports. Backend always emits 0.0."""
    peers = [_peer("MSFT", "Microsoft", 3_000_000_000_000)]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
        peer_ratios=_default_ratios_for(peers),
    )
    assert all(c["market_share_percent"] == 0.0 for c in out)


# ── PR 3: TAM overlay (AI extraction → market_dynamics) ───────────────


def _md_with_zero_tam() -> dict:
    """Fresh market_dynamics dict in the deterministic-default state
    (TAM=0, no source quote, no source label) — the input shape
    `_apply_tam_source` operates on."""
    return {
        "industry": "Software",
        "concentration": "oligopoly",
        "cagr_5yr": 12.5,
        "current_tam": 0.0,
        "future_tam": 0.0,
        "current_year": "2026",
        "future_year": "2031",
        "lifecycle_phase": "secular_growth",
        "tam_source_quote": None,
        "tam_source_label": None,
        "source_grain": None,
        "tam_scope": None,
    }


def test_apply_tam_ai_quote_wins_when_provided():
    """The happy path: AI returns a positive TAM AND a verbatim source
    quote → both numbers and the "Earnings call quote" label land on
    market_dynamics. AI quote is highest priority — beats any FRED
    proxy that was passed in."""
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 150_000_000_000,
        "future_tam": 300_000_000_000,
        "future_year": "2030",
        "tam_source_quote": "We see a $150B addressable market today expanding to $300B by 2030.",
    }, None)
    # Normalized to BILLIONS (raw USD ÷ 1e9) — the unit the schema + iOS expect.
    # (Storing raw dollars is what produced "$100000000000.0T" on iOS.)
    assert md["current_tam"] == 150.0
    assert md["future_tam"] == 300.0
    assert md["future_year"] == "2030"
    assert "150B addressable market" in md["tam_source_quote"]
    assert md["tam_source_label"] == "Earnings call quote"


def test_apply_tam_partial_ai_quote_falls_back_to_dossier():
    """The reported Moat bug: AI extracts a future TAM ("$3T by 2030") but NO
    current figure. Applied alone that renders "$0B → $3T" and the CAGR cell
    goes "—" (the old early `return` skipped the dossier). A one-sided AI quote
    must now fall through to the industry dossier, which supplies a COMPLETE
    current+future pair AND the CAGR."""
    from app.services.industry_dossier_service import IndustryDossier

    md = _md_with_zero_tam()
    md["cagr_5yr"] = None        # sector_aggregates produced nothing
    md["current_tam"] = 0.0
    dossier = IndustryDossier(
        current_tam=1700.0, future_tam=2100.0,
        current_year="2024", future_year="2029",
        source_label="BEA Information Sector GDP (via FRED)",
        cagr_5y_pct=4.3,
        industry="Software - Infrastructure", sector="Technology",
        concentration_label="oligopoly", source_grain="sector",
    )
    _apply_tam_source(md, {
        # Only a future figure + a quote — no current_tam (the bug input).
        "future_tam": 3_000_000_000_000,
        "future_year": "2030",
        "tam_source_quote": "We see a $3 trillion TAM by 2030.",
    }, dossier)
    # The half AI quote is rejected; the dossier's complete pair wins.
    assert md["current_tam"] == 1700.0      # not $0B
    assert md["future_tam"] == 2100.0       # not the AI's lone $3T
    assert md["cagr_5yr"] == 4.3            # not "—"
    assert md["tam_source_label"] == "BEA Information Sector GDP (via FRED)"
    assert md["tam_source_quote"] is None   # the one-sided AI quote was NOT used


def test_apply_tam_dossier_cagr_applies_even_when_ai_tam_wins():
    """A COMPLETE AI TAM quote wins the TAM PAIR, but the dossier's CAGR must
    STILL fill the cagr cell — previously the AI path returned early and the
    CAGR rendered "—" whenever AI quoted a TAM."""
    from app.services.industry_dossier_service import IndustryDossier

    md = _md_with_zero_tam()
    md["cagr_5yr"] = None        # sector_aggregates produced nothing
    dossier = IndustryDossier(
        current_tam=1700.0, future_tam=2100.0,
        current_year="2024", future_year="2029",
        source_label="BEA Information Sector GDP (via FRED)",
        cagr_5y_pct=6.5,
        industry="Software - Infrastructure", sector="Technology",
        source_grain="sector",
    )
    _apply_tam_source(md, {
        "current_tam": 150_000_000_000,
        "future_tam": 300_000_000_000,
        "tam_source_quote": "We see a $150B market today expanding to $300B.",
    }, dossier)
    assert md["current_tam"] == 150.0                       # AI pair won the TAM
    assert md["future_tam"] == 300.0
    assert md["tam_source_label"] == "Earnings call quote"
    assert md["cagr_5yr"] == 6.5                            # dossier CAGR still applied


def test_apply_tam_scope_from_dossier_us_and_global():
    """The report must explicitly label TAM as US vs Global. Scope follows the
    industry's resolved data source — Census/FRED dossiers are 'us', Phase B
    global-research overrides are 'global' — and is applied regardless of
    whether the proxy or a complete AI quote won the TAM pair."""
    from app.services.industry_dossier_service import IndustryDossier

    def _dossier(scope: str) -> IndustryDossier:
        return IndustryDossier(
            current_tam=1700.0, future_tam=2100.0,
            current_year="2024", future_year="2029",
            source_label="src", cagr_5y_pct=4.3,
            industry="X", sector="Technology", tam_scope=scope,
        )

    # US dossier (proxy wins the pair) → "us".
    md = _md_with_zero_tam()
    _apply_tam_source(md, {"tam_source_quote": ""}, _dossier("us"))
    assert md["tam_scope"] == "us"

    # Global dossier (proxy wins) → "global".
    md = _md_with_zero_tam()
    _apply_tam_source(md, {"tam_source_quote": ""}, _dossier("global"))
    assert md["tam_scope"] == "global"

    # A complete AI quote wins the PAIR, but scope still follows the industry.
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 150_000_000_000,
        "future_tam": 300_000_000_000,
        "tam_source_quote": "$150B today to $300B.",
    }, _dossier("global"))
    assert md["current_tam"] == 150.0          # AI pair won
    assert md["tam_scope"] == "global"         # ...but scope is the industry's

    # No dossier → scope stays unset (iOS shows no pill).
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 150_000_000_000,
        "future_tam": 300_000_000_000,
        "tam_source_quote": "$150B to $300B.",
    }, None)
    assert md.get("tam_scope") is None


def test_normalize_ai_tam_billions_converts_and_clamps():
    """The AI returns TAM as raw USD (per the Stage-A prompt); the schema +
    iOS expect BILLIONS. Convert, tolerate an AI that already answered in
    billions, and drop implausible magnitudes so a hallucinated $100T never
    renders as "$100000000000.0T"."""
    # Raw USD → billions.
    assert _normalize_ai_tam_billions(150_000_000_000) == 150.0      # $150B
    assert _normalize_ai_tam_billions(1_500_000_000_000) == 1500.0   # $1.5T
    assert _normalize_ai_tam_billions("75000000000") == 75.0         # string coerced
    # Already in billions (AI deviating from the raw-USD contract) → kept as-is.
    assert _normalize_ai_tam_billions(150) == 150.0
    # Implausible: a hallucinated $100T (the real ORCL Moat bug) → dropped.
    assert _normalize_ai_tam_billions(100_000_000_000_000) is None
    # Non-positive / non-numeric → dropped.
    assert _normalize_ai_tam_billions(0) is None
    assert _normalize_ai_tam_billions(-5) is None
    assert _normalize_ai_tam_billions("abc") is None
    assert _normalize_ai_tam_billions(None) is None


def test_apply_tam_drops_implausible_ai_value():
    """The ORCL Moat bug: a hallucinated $100T future_tam (with a quote) must
    NOT overlay an absurd number — it's dropped so iOS hides the TAM cell
    rather than showing "$100000000000.0T"."""
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 0,
        "future_tam": 100_000_000_000_000,   # $100T — implausible
        "tam_source_quote": "A hundred-trillion-dollar opportunity.",
    }, None)
    assert md["current_tam"] == 0.0
    assert md["future_tam"] == 0.0            # dropped, not 100000.0
    assert md["tam_source_label"] is None     # no overlay happened


def test_build_timeline_prices_weekly_frozen_series():
    """The Earnings Timeline price overlay is EMBEDDED (frozen) at generation —
    a WEEKLY close series (last trading day per ISO week) over the timeline's
    ACTUAL years, built from the `historical` the collector already has. Weekly
    (~52 pts/yr) is finer than the earlier monthly downsample for a smoother line.
    (The iOS panel previously fetched /earnings live, leaking today's prices.)"""
    annual_timeline = [
        {"period": "2024", "is_forecast": False},
        {"period": "2025", "is_forecast": False},
        {"period": "2026", "is_forecast": True},   # forecast year — no price
    ]
    historical = {"historical": [
        {"date": "2023-12-29", "close": 100.0},   # before first actual year → dropped
        # ISO week 2 of 2024 (Mon 2024-01-08 … Sun 2024-01-14): latest kept.
        {"date": "2024-01-09", "close": 108.0},   # same ISO week, earlier → not kept
        {"date": "2024-01-12", "close": 110.0},   # latest in its ISO week → kept
        {"date": "2024-01-19", "close": 115.0},   # ISO week 3 → its own point
        {"date": "2025-06-30", "close": 200.0},
    ]}
    pts = _build_timeline_prices(historical, annual_timeline)
    # Oldest-first; pre-2024 dropped; one (latest) close per ISO week.
    assert [p["date"] for p in pts] == ["2024-01-12", "2024-01-19", "2025-06-30"]
    assert [p["price"] for p in pts] == [110.0, 115.0, 200.0]
    # No actual years (forecast-only) or no history → empty.
    assert _build_timeline_prices(historical, [{"period": "2026", "is_forecast": True}]) == []
    assert _build_timeline_prices({}, annual_timeline) == []


def test_build_timeline_prices_spans_to_year_min_when_history_reaches():
    """When the (now ~6y) historical fetch reaches the leftmost actual bar, the
    weekly price line starts in that year — no more gap between the first bar and
    the price line's start."""
    annual_timeline = [
        {"period": "2020", "is_forecast": False},
        {"period": "2021", "is_forecast": False},
        {"period": "2022", "is_forecast": False},
        {"period": "2023", "is_forecast": True},
    ]
    historical = {"historical": [
        {"date": "2019-11-01", "close": 90.0},    # before year_min (2020) → dropped
        {"date": "2020-01-06", "close": 100.0},   # first ISO week of year_min → kept
        {"date": "2021-07-01", "close": 150.0},
        {"date": "2022-07-01", "close": 175.0},
    ]}
    pts = _build_timeline_prices(historical, annual_timeline)
    assert pts, "expected a price series"
    assert int(pts[0]["date"][:4]) == 2020        # line begins at the leftmost bar
    assert all(int(p["date"][:4]) >= 2020 for p in pts)   # nothing before year_min
    assert all(int(p["date"][:4]) <= 2022 for p in pts)   # no forecast-year price leak


def test_build_timeline_prices_new_company_starts_at_first_price():
    """A young/IPO'd name whose price history begins AFTER the earliest declared
    bar year is NOT padded left — the line starts at the first available price."""
    annual_timeline = [
        {"period": "2020", "is_forecast": False},   # year_min, but no price this early
        {"period": "2021", "is_forecast": False},
        {"period": "2022", "is_forecast": False},
    ]
    historical = {"historical": [
        {"date": "2022-03-10", "close": 30.0},      # IPO'd 2022 — nothing before
        {"date": "2022-09-15", "close": 42.0},
    ]}
    pts = _build_timeline_prices(historical, annual_timeline)
    assert pts, "expected a price series"
    assert int(pts[0]["date"][:4]) == 2022          # starts at first trade, not padded to 2020


def test_apply_tam_rejects_number_without_source_quote():
    """No quote → reject the AI-provided number. Primary anti-fabrication
    guard. With no FRED fallback, TAM stays at 0 and label stays None."""
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 999_000_000_000,
        "future_tam": 0,
        "tam_source_quote": "",
    }, None)
    assert md["current_tam"] == 0.0
    assert md["future_tam"] == 0.0
    assert md["tam_source_quote"] is None
    assert md["tam_source_label"] is None


def test_apply_tam_rejects_zero_or_negative_numbers():
    """Zero/negative TAM with a quote → no overlay (no meaningful value
    to apply). FRED fallback is also None so labels stay None."""
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 0,
        "future_tam": -1,
        "tam_source_quote": "We have a market.",
    }, None)
    assert md["current_tam"] == 0.0
    assert md["future_tam"] == 0.0
    assert md["tam_source_label"] is None


def test_apply_tam_handles_string_numbers():
    """Some AI runs return numbers as strings; coerce rather than drop."""
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": "75000000000",
        "future_tam": "120000000000",
        "tam_source_quote": "$75B today, growing to $120B.",
    }, None)
    # Coerced from string AND normalized to billions: "$75B" / "$120B".
    assert md["current_tam"] == 75.0
    assert md["future_tam"] == 120.0


def test_apply_tam_ignores_invalid_future_year():
    """`future_year` only updates when AI returned a 4-digit numeric
    string; garbage values don't break the iOS chart's x-axis. (TAM pair is
    complete so the AI path activates — a one-sided pair is rejected.)"""
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 50_000_000_000,
        "future_tam": 90_000_000_000,
        "future_year": "soonish",
        "tam_source_quote": "Our market is $50B today, $90B ahead.",
    }, None)
    assert md["current_tam"] == 50.0    # AI pair applied...
    assert md["future_year"] == "2031"  # ...but the garbage future_year is ignored


def test_apply_tam_truncates_long_quotes():
    """Source quotes are capped at 200 chars so the iOS attribution
    bubble doesn't become a wall of text."""
    md = _md_with_zero_tam()
    long_quote = "x" * 500
    _apply_tam_source(md, {
        "current_tam": 10_000_000_000,
        "future_tam": 20_000_000_000,
        "tam_source_quote": long_quote,
    }, None)
    assert len(md["tam_source_quote"]) == 200


def test_apply_tam_no_op_when_both_sources_missing():
    """No AI input + no FRED fallback → no mutations. Defensive against
    the AI returning null `market_dynamics` (valid per Stage A prompt
    when the transcript was unavailable)."""
    md = _md_with_zero_tam()
    snapshot = dict(md)
    _apply_tam_source(md, None, None)
    assert md == snapshot
    _apply_tam_source(md, {}, None)
    assert md == snapshot
    _apply_tam_source(md, "not a dict", None)  # type: ignore[arg-type]
    assert md == snapshot


def test_apply_tam_falls_back_to_industry_proxy_when_ai_has_no_quote():
    """When AI didn't extract a transcript quote, the industry proxy
    fills in — TAM numbers come from the source's data, label attributes
    it so users know it's a proxy, not company TAM."""
    from app.services.industry_tam_service import IndustryTAM

    md = _md_with_zero_tam()
    md["cagr_5yr"] = None     # simulate empty sector_aggregates
    proxy = IndustryTAM(
        current_tam=1700.0,
        future_tam=2100.0,
        current_year="2024",
        future_year="2029",
        source_label="BEA Information Sector GDP (via FRED)",
        cagr_5y_pct=4.3,
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, proxy)
    assert md["current_tam"] == 1700.0
    assert md["future_tam"] == 2100.0
    assert md["current_year"] == "2024"
    assert md["future_year"] == "2029"
    assert "BEA Information Sector" in md["tam_source_label"]
    assert md["cagr_5yr"] == 4.3


def test_apply_tam_preserves_sector_cagr_over_industry_proxy_cagr():
    """When sector_aggregates already produced a CAGR (highest trust —
    S&P 500-derived), the industry proxy's CAGR must NOT overwrite it."""
    from app.services.industry_tam_service import IndustryTAM

    md = _md_with_zero_tam()
    md["cagr_5yr"] = 12.5     # sector_aggregates already set this
    proxy = IndustryTAM(
        current_tam=1700.0,
        future_tam=2100.0,
        current_year="2024",
        future_year="2029",
        source_label="BEA Information Sector GDP (via FRED)",
        cagr_5y_pct=4.3,      # would lose to sector value
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, proxy)
    assert md["cagr_5yr"] == 12.5    # untouched


def test_apply_tam_promotes_lifecycle_to_secular_growth_on_high_industry_cagr():
    """When industry proxy CAGR > 15% and lifecycle was the "mature"
    default, promote to "secular_growth" so the UI reflects the tailwind."""
    from app.services.industry_tam_service import IndustryTAM

    md = _md_with_zero_tam()
    md["cagr_5yr"] = None
    md["lifecycle_phase"] = "mature"
    proxy = IndustryTAM(
        current_tam=1156.0,
        future_tam=2267.0,
        current_year="2023",
        future_year="2028",
        source_label="US Census AIES — Electronic shopping (NAICS 4541)",
        cagr_5y_pct=14.4,   # below 15% threshold → stays mature
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, proxy)
    assert md["lifecycle_phase"] == "mature"   # 14.4% < 15% cutoff

    md["lifecycle_phase"] = "mature"
    md["cagr_5yr"] = None
    proxy_fast = IndustryTAM(
        current_tam=100.0,
        future_tam=200.0,
        current_year="2023",
        future_year="2028",
        source_label="test",
        cagr_5y_pct=18.0,   # clears the 15% threshold
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, proxy_fast)
    assert md["lifecycle_phase"] == "secular_growth"


def test_apply_tam_promotes_lifecycle_to_declining_on_negative_industry_cagr():
    """Negative industry CAGR + default 'mature' lifecycle → 'declining'."""
    from app.services.industry_tam_service import IndustryTAM

    md = _md_with_zero_tam()
    md["cagr_5yr"] = None
    md["lifecycle_phase"] = "mature"
    shrinking = IndustryTAM(
        current_tam=50.0,
        future_tam=40.0,
        current_year="2023",
        future_year="2028",
        source_label="test",
        cagr_5y_pct=-4.0,
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, shrinking)
    assert md["lifecycle_phase"] == "declining"


def test_apply_tam_does_not_override_emerging_lifecycle():
    """`emerging` (set by low-constituent-count signal) outranks the
    CAGR-based promotion. A new niche with 3 public players stays
    'emerging' even if its CAGR is rocketing."""
    from app.services.industry_tam_service import IndustryTAM

    md = _md_with_zero_tam()
    md["cagr_5yr"] = None
    md["lifecycle_phase"] = "emerging"
    rocket = IndustryTAM(
        current_tam=10.0, future_tam=25.0,
        current_year="2023", future_year="2028",
        source_label="test", cagr_5y_pct=20.0,
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, rocket)
    assert md["lifecycle_phase"] == "emerging"


# ── Industry dossier overlay (replaces FRED/Census live path) ─────────


def test_apply_tam_writes_source_grain_from_dossier():
    """The dossier carries `source_grain` ('industry' | 'sector' |
    'all_industry') so iOS can decide whether to show the
    "⚠ Broader than industry" chip. `_apply_tam_source` must surface it
    on market_dynamics. Plain `IndustryTAM` (no dossier) → no chip."""
    from app.services.industry_dossier_service import IndustryDossier
    from app.services.industry_tam_service import IndustryTAM

    # Industry-grain dossier → 'industry' (no chip on iOS).
    md = _md_with_zero_tam()
    dossier = IndustryDossier(
        current_tam=526.0, future_tam=820.0,
        current_year="2023", future_year="2028",
        source_label="US Census AIES — Software publishers (NAICS 5112)",
        cagr_5y_pct=9.2,
        industry="Software - Infrastructure",
        sector="Technology",
        source_grain="industry",
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, dossier)
    assert md["source_grain"] == "industry"

    # Sector-grain (fallback) → 'sector' (iOS shows chip).
    md2 = _md_with_zero_tam()
    sector_dossier = IndustryDossier(
        current_tam=1700.0, future_tam=2100.0,
        current_year="2023", future_year="2028",
        source_label="BEA Information sector GDP — broader than X",
        cagr_5y_pct=4.3,
        industry="Some Niche Industry",
        sector="Technology",
        source_grain="sector",
    )
    _apply_tam_source(md2, {"tam_source_quote": ""}, sector_dossier)
    assert md2["source_grain"] == "sector"

    # Plain IndustryTAM (live-path fallback) → leaves source_grain unset.
    md3 = _md_with_zero_tam()
    plain = IndustryTAM(
        current_tam=1700.0, future_tam=2100.0,
        current_year="2024", future_year="2029",
        source_label="FRED", cagr_5y_pct=4.3,
    )
    _apply_tam_source(md3, {"tam_source_quote": ""}, plain)
    assert md3.get("source_grain") is None  # unset → iOS treats as no warning


def test_apply_tam_dossier_concentration_overrides_peer_derived():
    """When the dossier carries `concentration_label` (computed weekly
    from ALL constituents in the industry), it must override whatever
    `_build_market_dynamics` set from the focal ticker's top-5 peers —
    the industry-wide number is more authoritative."""
    from app.services.industry_dossier_service import IndustryDossier

    md = _md_with_zero_tam()
    md["concentration"] = "oligopoly"     # set by peer-derived earlier
    dossier = IndustryDossier(
        current_tam=100.0, future_tam=120.0,
        current_year="2023", future_year="2028",
        source_label="test", cagr_5y_pct=5.0,
        industry="Foo", sector="Technology",
        concentration_label="fragmented",
        source_grain="industry",
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, dossier)
    assert md["concentration"] == "fragmented"


def test_apply_tam_dossier_lifecycle_overrides_default_mature():
    """Same idea for lifecycle — the dossier's classification (which
    factors industry-wide CAGR + constituent count) wins over the
    `_build_market_dynamics` default. Only non-default ('mature')
    classifications override."""
    from app.services.industry_dossier_service import IndustryDossier

    md = _md_with_zero_tam()
    md["lifecycle_phase"] = "mature"
    dossier = IndustryDossier(
        current_tam=100.0, future_tam=200.0,
        current_year="2023", future_year="2028",
        source_label="test", cagr_5y_pct=18.0,
        industry="Foo", sector="Technology",
        lifecycle_phase="secular_growth",
        source_grain="industry",
    )
    _apply_tam_source(md, {"tam_source_quote": ""}, dossier)
    assert md["lifecycle_phase"] == "secular_growth"


def test_dossier_classification_helpers_match_collector_thresholds():
    """`industry_dossier_service.classify_concentration` /
    `classify_lifecycle` are local mirrors of the collector helpers — pin
    parity so the two paths can't silently diverge."""
    from app.services.industry_dossier_service import (
        classify_concentration as ds_concentration,
        classify_lifecycle as ds_lifecycle,
    )
    from app.services.agents.ticker_report_data_collector import (
        _classify_concentration as collector_concentration,
        _classify_lifecycle as collector_lifecycle,
    )

    # Concentration is MARKET-CAP based, so high concentration caps at
    # oligopoly (monopoly/duopoly are reserved for real share data). Parity is
    # what this test pins: both mirrors must agree on every case.
    cases = [
        (55.0, 80.0, 2200.0),   # high cap conc → oligopoly (capped, not monopoly)
        (35.0, 75.0, 1800.0),   # high cap conc → oligopoly (capped, not duopoly)
        (20.0, 35.0, 1600.0),   # oligopoly (HHI >= 1500)
        (10.0, 18.0, 800.0),    # fragmented
    ]
    for top1, top2, hhi in cases:
        assert ds_concentration(top1, top2, hhi) == collector_concentration(top1, top2, hhi)

    # Lifecycle: emerging / secular_growth / declining / mature.
    for cagr, n in [(None, 3), (None, 10), (20.0, 10), (-2.0, 10), (8.0, 10)]:
        assert ds_lifecycle(cagr, n) == collector_lifecycle(cagr, n)


# ── PR 3: transcript excerpt builder ───────────────────────────────


def test_excerpt_returns_empty_for_empty_transcript():
    assert _extract_tam_relevant_excerpt("") == ""


def test_excerpt_includes_head_chars():
    """First 2K chars are always included — that's where prepared
    remarks live and where TAM is most often quoted."""
    head = "Prepared remarks. " * 200
    tail = "\n\nUnrelated Q&A content. " * 100
    transcript = head + tail
    out = _extract_tam_relevant_excerpt(transcript)
    assert out.startswith("Prepared remarks.")


def test_excerpt_grabs_tam_paragraphs_from_qa():
    """TAM mentions in the Q&A section are pulled into a separate
    block so AI extraction doesn't miss them when the head cap excludes
    that part of the call."""
    head = "Filler. " * 300  # ~2400 chars — exceeds head_chars
    tam_para = (
        "We continue to see our total addressable market growing to "
        "$200B by 2030, up from $80B today."
    )
    transcript = head + "\n\n" + tam_para
    out = _extract_tam_relevant_excerpt(transcript)
    assert "TAM-mention paragraphs" in out
    assert "$200B" in out


def test_excerpt_caps_at_5k_chars():
    """Excerpt must stay below 5K chars total so the Stage A prompt
    budget isn't blown by a long earnings call."""
    transcript = "long content with addressable market mentions. " * 1000
    out = _extract_tam_relevant_excerpt(transcript)
    assert len(out) <= 5000


# ── PR 4: Macro risk factor derivation ────────────────────────────────


def _ind(
    symbol: str,
    change_1m_pct: float | None = None,
    *,
    level: float | None = None,
    change_3m_pct: float | None = None,
) -> dict:
    """Minimal indicator row mirroring `_fetch_macro_indicators` output.

    Post-threat-level-rework: `level` (current price) and
    `change_3m_pct` (3-month window) are the primary signals for VIX
    and oil/gold/DXY respectively. The 1M change remains as a
    fallback for back-compat with older callers.
    """
    return {
        "symbol": symbol,
        "level": level,
        "change_1m_pct": change_1m_pct,
        "change_3m_pct": change_3m_pct,
        "change_1y_pct": None,
        "change_5d_pct": None,
    }


def test_macro_no_indicators_returns_empty():
    """Empty indicator list → empty risk factor list (no fabrication)."""
    assert _build_macro_risk_factors_from_indicators([]) == []


def test_macro_oil_spike_emits_high_severity_energy():
    """Oil up 25% over 3 months → HIGH-severity energy risk (bands
    10/20/35/50 → 25 lands in HIGH)."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("CLUSD", change_3m_pct=25.0),
    ])
    assert len(factors) == 1
    f = factors[0]
    assert f["category"] == "energy"
    assert f["severity"] == "high"
    assert f["trend"] == "worsening"
    # Description must include the actual % so iOS users see the source number.
    assert "25.0%" in f["description"] or "25.0" in f["description"]


def test_macro_oil_drop_emits_under_magnitude_band():
    """Oil DOWN 25% is itself a regime event — the magnitude-based
    bands surface it as a risk factor (downside oil shocks signal
    demand collapse / disinflation surprise, both market-moving)."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("CLUSD", change_3m_pct=-25.0),
    ])
    assert len(factors) == 1
    assert factors[0]["severity"] == "high"
    assert factors[0]["trend"] == "improving"


def test_macro_oil_small_move_low_severity():
    """3% oil move is normal noise — no card emitted (dead-band)."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("CLUSD", change_3m_pct=3.0),
    ])
    assert factors == []


def test_macro_gold_rally_signals_flight_to_safety():
    """Gold up 5% over 3 months → flight-to-safety, currency category."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("GCUSD", change_3m_pct=5.0),
    ])
    assert any(f["category"] == "currency" for f in factors)


def test_macro_gold_quiet_does_not_emit():
    """Sub-3% 3M gold move is too quiet to justify a card."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("GCUSD", change_3m_pct=1.0),
    ])
    assert factors == []


def test_macro_copper_decline_signals_demand_weakness():
    """Copper down 10% MoM → industrial demand weakness, supply_chain category."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("HGUSD", change_1m_pct=-10.0),
    ])
    assert any(f["category"] == "supply_chain" for f in factors)


def test_macro_copper_rally_does_not_emit():
    """Copper up is good for industrial demand; no risk card."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("HGUSD", change_1m_pct=8.0),
    ])
    assert factors == []


def test_macro_vix_level_emits_volatility_card():
    """VIX at 32 → HIGH-stress regime (bands 16/22/30/40 → 32 ≥ 30).
    Note: the rework switched from % Δ to absolute level — a 35→36
    reading is HIGH stress even though the delta is invisible."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("^VIX", level=32.0, change_1m_pct=10.0),
    ])
    assert len(factors) == 1
    assert factors[0]["severity"] in ("high", "severe")


def test_macro_treasury_yield_jump_emits_rate_risk():
    """10Y Treasury 3-mo move ≥8% trips the FMP-side rate factor."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("^TNX", change_3m_pct=10.0),
    ])
    assert any(f["category"] == "interest_rates" for f in factors)


def test_macro_dxy_strength_signals_translation_drag():
    """Dollar up 4% over 3 months → ELEV multinational FX risk."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("DXY", change_3m_pct=4.0),
    ])
    assert any(f["category"] == "currency" for f in factors)


def test_macro_indicator_with_missing_change_skipped():
    """Indicator without any change/level fields is silently skipped —
    better than emitting a fake risk."""
    factors = _build_macro_risk_factors_from_indicators([
        {"symbol": "CLUSD", "level": None, "change_1m_pct": None,
         "change_3m_pct": None, "change_1y_pct": None, "change_5d_pct": None},
    ])
    assert factors == []


def test_macro_full_basket_realistic_scenario():
    """End-to-end: a realistic macro environment produces multiple
    risk factors covering energy, rates, vol — confirming the basket
    fans out across categories rather than collapsing to one."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("CLUSD", change_3m_pct=22.0),       # oil bid → HIGH
        _ind("GCUSD", change_3m_pct=10.0),       # gold flight → HIGH
        _ind("HGUSD", change_1m_pct=-12.0),      # copper weakness → HIGH
        _ind("^VIX", level=28.0,                # vol elevated
             change_1m_pct=20.0),
        _ind("^TNX", change_3m_pct=18.0),        # rates higher → HIGH
        _ind("DXY", change_3m_pct=6.0),          # USD stronger → HIGH
        _ind("SIUSD", change_1m_pct=5.0),        # silver — no rule
    ])
    assert 4 <= len(factors) <= 6
    categories = {f["category"] for f in factors}
    assert "energy" in categories
    assert "interest_rates" in categories
    assert "currency" in categories


# ── PR 4: macro factor merge (deterministic wins) ────────────────────


def test_merge_deterministic_factors_kept_first():
    deterministic = [
        {"category": "energy", "title": "Oil Price Pressure", "severity": "high"},
    ]
    ai_factors = [
        {"category": "geopolitical", "title": "Mideast Tensions", "severity": "elevated"},
    ]
    merged = _merge_macro_risk_factors(deterministic, ai_factors)
    assert merged[0]["title"] == "Oil Price Pressure"
    assert any(f["category"] == "geopolitical" for f in merged)


def test_merge_ai_factor_dropped_when_category_overlaps():
    """If the AI returns an `energy` factor and we already have a real
    one, the AI's gets dropped — real numbers win on category collision."""
    deterministic = [
        {"category": "energy", "title": "Oil Price Pressure", "severity": "high"},
    ]
    ai_factors = [
        {"category": "energy", "title": "AI's vague energy take", "severity": "low"},
        {"category": "regulation", "title": "New SEC rules", "severity": "elevated"},
    ]
    merged = _merge_macro_risk_factors(deterministic, ai_factors)
    titles = [f["title"] for f in merged]
    assert "Oil Price Pressure" in titles
    assert "AI's vague energy take" not in titles
    assert "New SEC rules" in titles


def test_merge_caps_at_six_factors():
    """iOS shows up to 6 — the merge must not return more."""
    deterministic = [
        {"category": f"cat_{i}", "title": f"D{i}", "severity": "low"}
        for i in range(4)
    ]
    ai_factors = [
        {"category": f"ai_cat_{i}", "title": f"A{i}", "severity": "low"}
        for i in range(10)
    ]
    merged = _merge_macro_risk_factors(deterministic, ai_factors)
    assert len(merged) == 6


def test_merge_handles_invalid_ai_entries():
    """AI sometimes returns null / non-dict items; those must be skipped
    rather than crashing the merge."""
    deterministic = [
        {"category": "energy", "title": "Oil", "severity": "high"},
    ]
    ai_factors = [
        None,
        "not a dict",
        {"category": "regulation", "title": "OK entry", "severity": "low"},
    ]
    merged = _merge_macro_risk_factors(deterministic, ai_factors)
    titles = [f["title"] for f in merged]
    assert "Oil" in titles
    assert "OK entry" in titles
    assert len(merged) == 2


# ── PR 5: FRED-derived macro risk factors ─────────────────────────────


def _fred(series_id: str, *, latest=None, yoy_pct=None,
          change_6mo_pct=None, change_6mo_relative_pct=None,
          as_of: str = "2026-04-01") -> dict:
    return {
        "series_id": series_id,
        "latest": latest,
        "as_of": as_of,
        "yoy_pct": yoy_pct,
        "change_6mo_pct": change_6mo_pct,
        "change_6mo_relative_pct": change_6mo_relative_pct,
    }


def test_fred_no_indicators_returns_empty():
    """No FRED data → no factors. The macro module then renders only
    FMP-derived and AI factors."""
    assert _build_macro_risk_factors_from_fred([]) == []


def test_fred_cpi_high_emits_severe_inflation():
    """CPI YoY at 5.5% → SEVERE severity under bands (2,3,5,8)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL", latest=315.0, yoy_pct=5.5),
    ])
    assert len(factors) == 1
    f = factors[0]
    assert f["category"] == "inflation"
    assert f["severity"] == "severe"
    assert "5.5%" in f["description"]


def test_fred_cpi_above_target_reads_high():
    """CPI YoY at 3.0% → HIGH under tighter post-rework bands."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL", latest=305.0, yoy_pct=3.0),
    ])
    assert factors[0]["severity"] == "high"


def test_fred_cpi_at_target_no_factor():
    """CPI YoY below 2% → no factor (LOW silenced)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL", latest=300.0, yoy_pct=1.8),
    ])
    assert factors == []


def test_fred_fed_funds_rapid_tightening_emits_factor():
    """Fed Funds up 1.5pp in 6 months → HIGH tightening pace factor.
    Note: post-rework also emits a separate level factor when the
    level is itself ≥2%, so we check for any interest_rates with
    worsening trend (the Δ factor)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("FEDFUNDS", latest=5.25, change_6mo_pct=1.5),
    ])
    rate_factors = [f for f in factors if f["category"] == "interest_rates"]
    assert rate_factors
    assert any(f["trend"] == "worsening" for f in rate_factors)


def test_fred_fed_funds_easing_signals_improving():
    """Fed Funds down 1.5pp in 6mo → Δ factor trend=improving.
    Level factor (4.0% level) also emits at HIGH but with stable
    trend; we look up the Δ-specific factor by title."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("FEDFUNDS", latest=4.0, change_6mo_pct=-1.5),
    ])
    easing = next(f for f in factors if "Easing" in f["title"])
    assert easing["trend"] == "improving"


def test_fred_fed_funds_steady_level_emits_only_level():
    """Sub-0.5pp 6mo move = steady policy → Δ factor silenced; only
    the level-based factor remains (5.25% is in the ELEVATED band)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("FEDFUNDS", latest=5.25, change_6mo_pct=0.3),
    ])
    # No 'Tightening' / 'Easing' Δ factor; level factor present.
    titles = [f["title"] for f in factors]
    assert not any("Tightening" in t or "Easing" in t for t in titles)
    assert any("Restrictive Policy Rate" in t for t in titles)


def test_fred_yield_curve_inversion_severe():
    """T10Y2Y at -0.45 → SEVERE under reverse bands (1,0.3,-0.3,-1)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("T10Y2Y", latest=-0.45),
    ])
    assert len(factors) == 1
    assert factors[0]["severity"] == "severe"
    assert factors[0]["category"] == "interest_rates"
    assert "Inverted" in factors[0]["title"]


def test_fred_yield_curve_flattening_high():
    """T10Y2Y at 0.30 → HIGH under reverse bands (≤0.3 trips HIGH)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("T10Y2Y", latest=0.30),
    ])
    assert factors[0]["severity"] == "high"


def test_fred_yield_curve_normal_no_factor():
    """T10Y2Y > 1.0 → LOW skipped (>1 is the new normal-curve band)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("T10Y2Y", latest=1.50),
    ])
    assert factors == []


def test_fred_high_10y_yield_emits_factor():
    """10Y at 5.5% → SEVERE long-rate pressure (bands 3/4.5/5.5/7)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("DGS10", latest=5.50),
    ])
    assert any(f["title"] == "Elevated Long-Term Rates" for f in factors)


def test_fred_low_10y_yield_no_factor():
    """10Y below 3% → no factor (LOW band)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("DGS10", latest=2.50),
    ])
    assert factors == []


def test_fred_full_macro_environment():
    """End-to-end: simulated stagflation environment produces multiple
    factors covering inflation + rates."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL", latest=320.0, yoy_pct=4.5),
        _fred("FEDFUNDS", latest=5.50, change_6mo_pct=1.25),
        _fred("DGS10", latest=5.10),
        _fred("T10Y2Y", latest=-0.30),
    ])
    categories = [f["category"] for f in factors]
    assert "inflation" in categories
    # interest_rates should appear at least once (multiple sources contribute)
    assert categories.count("interest_rates") >= 1
    severities = {f["severity"] for f in factors}
    # yield-curve inversion at -0.3 → SEVERE under reverse bands
    assert "severe" in severities


def test_fred_indicator_with_missing_value_skipped():
    """An indicator with no `latest` and no `yoy_pct` is skipped — better
    than emitting a fake factor with zero values."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL"),  # all None
    ])
    assert factors == []


# ── PR 5: FRED + FMP merge under the deterministic-wins rule ──────────


def test_fred_blocks_fmp_yield_curve_when_both_emit():
    """When both FRED (T10Y2Y) and FMP (^TNX) emit interest_rates risks,
    the merge keeps FRED's because it ran through the merge first."""
    fred = _build_macro_risk_factors_from_fred([
        _fred("T10Y2Y", latest=-0.45),
    ])
    fmp = _build_macro_risk_factors_from_indicators([
        _ind("^TNX", 8.0),
    ])
    merged = _merge_macro_risk_factors(fred, fmp)
    titles = [f["title"] for f in merged]
    assert "Inverted Yield Curve" in titles
    assert "Yield Curve Move" not in titles  # FMP one was de-duped


# ── PR 6: Forecast guidance overlay (anti-fabrication) ────────────────


def _rf_with_defaults() -> dict:
    """Fresh revenue_forecast partial in collector-default state (the
    input shape `_overlay_ai_guidance` operates on)."""
    return {
        "cagr": 12.0,
        "eps_growth": 15.0,
        "management_guidance": "maintained",
        "projections": [],
        "guidance_quote": None,
        "guidance_speaker": None,
        "guidance_period": None,
    }


def test_guidance_overlay_happy_path():
    """AI returned all 4 fields with a real quote → all land verbatim."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "raised",
        "guidance_quote": "We are raising our full-year revenue outlook to $58-60B.",
        "guidance_speaker": "CFO",
        "guidance_period": "FY 2026",
    })
    assert rf["management_guidance"] == "raised"
    assert "raising our full-year" in rf["guidance_quote"]
    assert rf["guidance_speaker"] == "CFO"
    assert rf["guidance_period"] == "FY 2026"


def test_guidance_overlay_rejects_raised_without_quote():
    """`raised` without a quote is a hallucination — coerce to `maintained`
    and null the attribution. This is the strongest anti-fabrication guard."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "raised",
        "guidance_quote": None,
        "guidance_speaker": "CFO",
        "guidance_period": "Q3 2026",
    })
    assert rf["management_guidance"] == "maintained"
    assert rf["guidance_quote"] is None
    assert rf["guidance_speaker"] is None
    assert rf["guidance_period"] is None


def test_guidance_overlay_rejects_lowered_with_empty_quote_string():
    """Empty-string quote is treated the same as missing — the iOS
    'lowered' chip would be misleading without a source citation."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "lowered",
        "guidance_quote": "   ",
        "guidance_speaker": "CEO",
        "guidance_period": "Q4 2025",
    })
    assert rf["management_guidance"] == "maintained"
    assert rf["guidance_quote"] is None
    assert rf["guidance_speaker"] is None


def test_guidance_overlay_rejects_string_null_quote():
    """Some AI runs return the literal string 'null' / 'None' / 'N/A'.
    Treat those as missing rather than letting them flow into the iOS
    quote bubble."""
    for sentinel in ("null", "None", "n/a", "NULL"):
        rf = _rf_with_defaults()
        _overlay_ai_guidance(rf, {
            "management_guidance": "raised",
            "guidance_quote": sentinel,
            "guidance_speaker": "CFO",
        })
        assert rf["management_guidance"] == "maintained"
        assert rf["guidance_quote"] is None


def test_guidance_overlay_truncates_long_quote():
    """Quotes are capped at 280 chars so the iOS bubble doesn't overflow."""
    rf = _rf_with_defaults()
    long_quote = "Something we said. " * 100
    _overlay_ai_guidance(rf, {
        "management_guidance": "raised",
        "guidance_quote": long_quote,
        "guidance_speaker": "CFO",
    })
    assert rf["guidance_quote"] is not None
    assert len(rf["guidance_quote"]) == 280


def test_guidance_overlay_invalid_status_falls_back_to_maintained():
    """AI sometimes invents enum values like 'mixed' / 'cautious'. Those
    must coerce to maintained — the iOS enum has only 3 valid cases."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "cautiously optimistic",
        "guidance_quote": "We are cautiously optimistic about the second half.",
        "guidance_speaker": "CEO",
    })
    assert rf["management_guidance"] == "maintained"


def test_guidance_overlay_normalizes_speaker():
    """Lowercase / whitespace AI output is normalized to the iOS-supported
    speaker labels."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "raised",
        "guidance_quote": "We are raising our outlook.",
        "guidance_speaker": "  cfo  ",
    })
    assert rf["guidance_speaker"] == "CFO"


def test_guidance_overlay_rejects_unknown_speaker():
    """Speakers outside CFO/CEO/IR are dropped to null. Quote stays;
    the iOS view just hides the speaker line."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "raised",
        "guidance_quote": "We are raising guidance.",
        "guidance_speaker": "Treasurer",
    })
    assert rf["guidance_quote"] == "We are raising guidance."
    assert rf["guidance_speaker"] is None


def test_guidance_overlay_no_op_when_ai_missing():
    """Stage A may return null `revenue_forecast` (e.g. when the
    transcript was unavailable). Overlay must be a no-op."""
    rf = _rf_with_defaults()
    snap = dict(rf)
    _overlay_ai_guidance(rf, None)
    assert rf == snap
    _overlay_ai_guidance(rf, {})
    assert rf["management_guidance"] == "maintained"
    assert rf["guidance_quote"] is None


def test_guidance_overlay_period_truncated_at_30_chars():
    """Period strings are capped at 30 chars to keep the iOS attribution
    line a single row."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "raised",
        "guidance_quote": "We see strength.",
        "guidance_speaker": "CFO",
        "guidance_period": "Fiscal Year 2026 ending December 31, 2026 inclusive",
    })
    assert len(rf["guidance_period"]) == 30


def test_guidance_overlay_maintained_status_clears_attribution():
    """When AI returns 'maintained' we don't need a quote — but the
    overlay must still null speaker / period so the iOS view doesn't
    show "— CFO, FY 2026" with no actual quote text."""
    rf = _rf_with_defaults()
    _overlay_ai_guidance(rf, {
        "management_guidance": "maintained",
        "guidance_quote": None,
        "guidance_speaker": "CFO",
        "guidance_period": "FY 2026",
    })
    assert rf["management_guidance"] == "maintained"
    assert rf["guidance_quote"] is None
    assert rf["guidance_speaker"] is None
    assert rf["guidance_period"] is None


# ── Persona isolation: collect() must not alias the shared neutral base ──


@pytest.mark.asyncio
async def test_collect_deepcopies_shared_base_across_personas(monkeypatch):
    """Concurrent same-ticker callers share ONE persona-neutral base via the
    ticker-keyed _INFLIGHT dedup. collect() must hand each persona its OWN deep
    copy so an in-place mutation in one persona's assemble_report (e.g. the
    grounded moat-pillar `source` / `peer_score` writes) can never bleed into a
    concurrent persona's report — guards the shallow-`dataclasses.replace`
    aliasing footgun.
    """
    from app.services.agents.ticker_report_data_collector import (
        CollectedTickerData,
        TickerReportDataCollector,
        _AGENT_MAP,
    )

    # One base instance, exactly as the _INFLIGHT future hands to every
    # concurrent first-caller for the same ticker.
    shared_base = CollectedTickerData(
        ticker="AAPL",
        persona_key="warren_buffett",
        profile={"symbol": "AAPL"},
        computed={"roe": 0.5},
        moat_grounded_pillars={"Brand": {"score": 7.0}},
        meta={"symbol": "AAPL"},
    )

    async def _fake_get_or_collect(ticker, fetch_fresh):
        return shared_base

    monkeypatch.setattr(
        "app.services.ticker_data_cache.get_or_collect", _fake_get_or_collect
    )

    collector = TickerReportDataCollector(fmp=object())
    a = await collector.collect("AAPL", "warren_buffett")
    b = await collector.collect("AAPL", "cathie_wood")

    # Persona correctly stamped on each independent copy.
    assert a.persona_key == "warren_buffett"
    assert b.persona_key == "cathie_wood"
    assert a.meta["agent"] == _AGENT_MAP.get("warren_buffett", "buffett")
    assert b.meta["agent"] == _AGENT_MAP.get("cathie_wood", "buffett")

    # Each persona owns its object graph — no aliasing to the base or each other.
    assert a is not shared_base and b is not shared_base
    assert a.moat_grounded_pillars is not shared_base.moat_grounded_pillars
    assert a.moat_grounded_pillars is not b.moat_grounded_pillars
    assert a.moat_grounded_pillars["Brand"] is not b.moat_grounded_pillars["Brand"]
    assert a.computed is not b.computed

    # Concrete proof: the exact in-place write assemble_report does on a grounded
    # pillar must NOT leak to the shared base or the other persona.
    a.moat_grounded_pillars["Brand"]["source"] = "grounded"
    assert "source" not in shared_base.moat_grounded_pillars["Brand"]
    assert "source" not in b.moat_grounded_pillars["Brand"]
