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

import pytest

from datetime import datetime, timedelta, timezone

from app.services.agents.ticker_report_data_collector import (
    _apply_tam_source,
    _build_competitors,
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
    """The 90-day window must drop trades older than the cutoff even when
    they pass the informative filter."""
    trades = [_trade(transaction_type="P-Purchase", days_ago=120)]
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
    ("Class action lawsuit filed over alleged disclosure failures", "Lawsuit"),
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
    # Top firm has ~72% of the (focal + peers) total cap → monopoly.
    assert md["concentration"] == "monopoly"
    assert md["cagr_5yr"] is None
    assert md["industry"] == "Software"


def test_market_dynamics_monopoly_when_top1_above_50():
    """Single dominant firm → monopoly enum (highest priority rule)."""
    md = _build_market_dynamics({}, _agg(top1_share_pct=55.0, hhi=3500.0))
    assert md["concentration"] == "monopoly"


def test_market_dynamics_duopoly_when_top2_above_70():
    """Two dominant firms → duopoly enum (second-priority rule)."""
    md = _build_market_dynamics({}, _agg(top1_share_pct=40.0, top2_share_pct=75.0, hhi=2900.0))
    assert md["concentration"] == "duopoly"


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


def test_build_competitors_empty_when_no_peers():
    """No peers → empty list (don't fabricate competitors)."""
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=[],
    )
    assert out == []


def test_build_competitors_market_share_sums_to_100():
    """Surviving peer market shares must total ~100% — recomputed from
    the post-filter set so dropped micro-caps don't dilute the visible
    top-N's percentages."""
    peers = [
        _peer("MSFT", "Microsoft", 3_000_000_000_000),
        _peer("GOOGL", "Alphabet", 2_000_000_000_000),
        _peer("META", "Meta", 1_000_000_000_000),
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
    )
    total = sum(c["market_share_percent"] for c in out)
    assert abs(total - 100.0) < 0.5


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
    )
    assert all(c["ticker"] != "AAPL" for c in out)
    assert len(out) == 1


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
    )
    tickers = [c["ticker"] for c in out]
    assert "HPAI" not in tickers
    assert "MSFT" in tickers


def test_build_competitors_caps_at_top_5():
    """Even with many valid peers, only the top 5 by mktCap make the
    cut — iOS's competitor card list is sized for 5 entries."""
    peers = [
        _peer(f"P{i}", f"Peer {i}", (10 - i) * 50_000_000_000)
        for i in range(8)
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
    )
    assert len(out) == 5


def test_build_competitors_threat_level_assigned():
    """Each competitor gets a threat_level enum value. With no
    peer_ratios passed in, scores cluster (no signal to differentiate),
    so threat_level for all survivors lands as "moderate" — still a
    valid Swift enum member."""
    peers = [
        _peer("STRONG", "Strong Co", 100_000_000_000, change=10.0),
        _peer("AVG", "Average Co", 100_000_000_000, change=0.0),
        _peer("WEAK", "Weak Co", 100_000_000_000, change=-10.0),
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
    )
    assert all(c["threat_level"] in ("low", "moderate", "high") for c in out)


def test_build_competitors_moat_scores_in_0_10_range():
    """Moat scores must always land in [0, 10] (clamped by the iOS
    rendering layer otherwise)."""
    peers = [
        _peer("A", "A", 100_000_000_000, change=50.0),
        _peer("B", "B", 100_000_000_000, change=-50.0),
        _peer("C", "C", 100_000_000_000, change=0.0),
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
    )
    for c in out:
        assert 0.0 <= c["moat_score"] <= 10.0


def test_build_competitors_sorted_by_market_share_desc():
    """Largest competitor first — iOS renders top-N and we don't want
    the small fish above the whales. Micro-caps below the $5B floor
    are dropped entirely."""
    peers = [
        _peer("SMALL", "Small Co", 1_000_000),       # below $5B floor → dropped
        _peer("BIG", "Big Co", 1_000_000_000_000),
        _peer("MEDIUM", "Medium Co", 100_000_000),   # below floor → dropped
        _peer("MID", "Mid Co", 50_000_000_000),
    ]
    out = _build_competitors(
        my_ticker="AAPL", my_profile={}, my_ratios=[],
        my_revenue_growth=None, peer_profiles=peers,
    )
    shares = [c["market_share_percent"] for c in out]
    assert shares == sorted(shares, reverse=True)
    assert out[0]["ticker"] == "BIG"
    # Below-floor peers should be gone.
    tickers = [c["ticker"] for c in out]
    assert "SMALL" not in tickers
    assert "MEDIUM" not in tickers


def test_build_competitors_uses_peer_ratios_for_scoring():
    """When peer_ratios are provided, scoring uses real ratios instead
    of the (now-removed) changes proxy — gives meaningful differentiation."""
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
    scores = {c["ticker"]: c["moat_score"] for c in out}
    assert scores["HIGHMARGIN"] > scores["LOWMARGIN"]


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
    assert md["current_tam"] == 150_000_000_000
    assert md["future_tam"] == 300_000_000_000
    assert md["future_year"] == "2030"
    assert "150B addressable market" in md["tam_source_quote"]
    assert md["tam_source_label"] == "Earnings call quote"


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
    assert md["current_tam"] == 75_000_000_000
    assert md["future_tam"] == 120_000_000_000


def test_apply_tam_ignores_invalid_future_year():
    """`future_year` only updates when AI returned a 4-digit numeric
    string; garbage values don't break the iOS chart's x-axis."""
    md = _md_with_zero_tam()
    _apply_tam_source(md, {
        "current_tam": 50_000_000_000,
        "future_tam": 0,
        "future_year": "soonish",
        "tam_source_quote": "Our market is $50B.",
    }, None)
    assert md["future_year"] == "2031"  # unchanged from default


def test_apply_tam_truncates_long_quotes():
    """Source quotes are capped at 200 chars so the iOS attribution
    bubble doesn't become a wall of text."""
    md = _md_with_zero_tam()
    long_quote = "x" * 500
    _apply_tam_source(md, {
        "current_tam": 10_000_000_000,
        "future_tam": 0,
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


def _ind(symbol: str, change_1m_pct: float) -> dict:
    """Minimal indicator row mirroring `_fetch_macro_indicators` output."""
    return {
        "symbol": symbol,
        "change_1m_pct": change_1m_pct,
        "change_1y_pct": None,
        "change_5d_pct": None,
    }


def test_macro_no_indicators_returns_empty():
    """Empty indicator list → empty risk factor list (no fabrication)."""
    assert _build_macro_risk_factors_from_indicators([]) == []


def test_macro_oil_spike_emits_high_severity_energy():
    """Oil up 18% MoM → high-severity energy risk."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("CLUSD", 18.0),
    ])
    assert len(factors) == 1
    f = factors[0]
    assert f["category"] == "energy"
    assert f["severity"] == "high"
    assert f["trend"] == "worsening"
    # Description must include the actual % so iOS users see the source number.
    assert "18.0%" in f["description"] or "18.0" in f["description"]


def test_macro_oil_drop_does_not_emit_high_risk():
    """Oil down 18% MoM is *good news* for energy consumers — should
    NOT emit a high-severity 'energy risk' card."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("CLUSD", -18.0),
    ])
    # Either the factor is downgraded to elevated/low or omitted entirely;
    # what matters is that we don't claim 'high' risk on a price drop.
    if factors:
        assert factors[0]["severity"] != "high"


def test_macro_oil_small_move_low_severity():
    """3% oil move is normal noise — low severity, neutral framing."""
    factors = _build_macro_risk_factors_from_indicators([_ind("CLUSD", 3.0)])
    assert factors[0]["severity"] == "low"


def test_macro_gold_rally_signals_flight_to_safety():
    """Gold up 5% MoM → flight-to-safety entry under category 'currency'."""
    factors = _build_macro_risk_factors_from_indicators([_ind("GCUSD", 5.0)])
    assert any(f["category"] == "currency" for f in factors)


def test_macro_gold_quiet_does_not_emit():
    """Sub-3% gold move is too quiet to justify a card."""
    factors = _build_macro_risk_factors_from_indicators([_ind("GCUSD", 1.0)])
    assert factors == []


def test_macro_copper_decline_signals_demand_weakness():
    """Copper down 10% MoM → industrial demand weakness, supply_chain category."""
    factors = _build_macro_risk_factors_from_indicators([_ind("HGUSD", -10.0)])
    assert any(f["category"] == "supply_chain" for f in factors)


def test_macro_copper_rally_does_not_emit():
    """Copper up is good for industrial demand; no risk card."""
    factors = _build_macro_risk_factors_from_indicators([_ind("HGUSD", 8.0)])
    assert factors == []


def test_macro_vix_spike_emits_volatility_card():
    """VIX up 30% MoM → volatility regime risk, severity high."""
    factors = _build_macro_risk_factors_from_indicators([_ind("^VIX", 30.0)])
    assert len(factors) == 1
    assert factors[0]["severity"] in ("high", "severe")


def test_macro_treasury_yield_jump_emits_rate_risk():
    """10Y Treasury up 8% MoM → interest_rates risk."""
    factors = _build_macro_risk_factors_from_indicators([_ind("^TNX", 8.0)])
    assert any(f["category"] == "interest_rates" for f in factors)


def test_macro_dxy_strength_signals_translation_drag():
    """Dollar up 4% MoM → multinational FX translation risk."""
    factors = _build_macro_risk_factors_from_indicators([_ind("DXY", 4.0)])
    assert any(f["category"] == "currency" for f in factors)


def test_macro_indicator_with_missing_change_skipped():
    """Indicator without `change_1m_pct` is silently skipped — better
    than emitting a fake risk."""
    factors = _build_macro_risk_factors_from_indicators([
        {"symbol": "CLUSD", "change_1m_pct": None,
         "change_1y_pct": None, "change_5d_pct": None},
    ])
    assert factors == []


def test_macro_full_basket_realistic_scenario():
    """End-to-end: a realistic macro environment produces 4-6 risk
    factors covering energy, rates, vol — confirming the basket fans
    out across categories rather than collapsing to one."""
    factors = _build_macro_risk_factors_from_indicators([
        _ind("CLUSD", 12.0),  # oil bid
        _ind("GCUSD", 6.0),   # gold flight-to-safety
        _ind("HGUSD", -8.0),  # copper weakness
        _ind("^VIX", 25.0),   # vol spike
        _ind("^TNX", 10.0),   # rates higher
        _ind("DXY", 3.5),     # USD stronger
        _ind("SIUSD", 5.0),   # silver — currently no rule, ignored
    ])
    # Should produce one factor per applicable indicator (6 enabled rules
    # above, silver has no rule).
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


def test_fred_cpi_high_emits_high_severity_inflation():
    """CPI YoY at 5.5% → high severity; inflation category."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL", latest=315.0, yoy_pct=5.5),
    ])
    assert len(factors) == 1
    f = factors[0]
    assert f["category"] == "inflation"
    assert f["severity"] == "high"
    assert "5.5%" in f["description"]


def test_fred_cpi_above_target_but_not_high():
    """CPI YoY in the 2.5-4% band → elevated, not high."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL", latest=305.0, yoy_pct=3.0),
    ])
    assert factors[0]["severity"] == "elevated"


def test_fred_cpi_at_target_no_factor():
    """CPI YoY in the 0-2% band → no factor (normal range)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("CPIAUCSL", latest=300.0, yoy_pct=1.8),
    ])
    assert factors == []


def test_fred_fed_funds_rapid_tightening_emits_factor():
    """Fed Funds up 1.5pp in 6 months → elevated tightening signal."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("FEDFUNDS", latest=5.25, change_6mo_pct=1.5),
    ])
    assert any(f["category"] == "interest_rates" for f in factors)
    f = next(f for f in factors if f["category"] == "interest_rates")
    assert f["trend"] == "worsening"


def test_fred_fed_funds_easing_signals_improving():
    """Fed Funds down 1.5pp in 6mo → trend improving."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("FEDFUNDS", latest=4.0, change_6mo_pct=-1.5),
    ])
    f = factors[0]
    assert f["trend"] == "improving"


def test_fred_fed_funds_steady_no_factor():
    """Sub-1pp 6mo move = steady policy → no factor."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("FEDFUNDS", latest=5.25, change_6mo_pct=0.5),
    ])
    assert factors == []


def test_fred_yield_curve_inversion_severe():
    """T10Y2Y < 0 → severe (recession signal)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("T10Y2Y", latest=-0.45),
    ])
    assert len(factors) == 1
    assert factors[0]["severity"] == "severe"
    assert factors[0]["category"] == "interest_rates"
    assert "Inverted" in factors[0]["title"]


def test_fred_yield_curve_flattening_elevated():
    """T10Y2Y between 0 and 0.5% → elevated warning."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("T10Y2Y", latest=0.30),
    ])
    assert factors[0]["severity"] == "elevated"


def test_fred_yield_curve_normal_no_factor():
    """T10Y2Y > 0.5% → normal, no factor."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("T10Y2Y", latest=1.50),
    ])
    assert factors == []


def test_fred_high_10y_yield_emits_factor():
    """10Y at 5.5% → elevated discount-rate pressure."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("DGS10", latest=5.50),
    ])
    assert any(f["title"] == "High Long-Term Rates" for f in factors)


def test_fred_low_10y_yield_no_factor():
    """10Y below 5% → no factor (it's a level, not a delta)."""
    factors = _build_macro_risk_factors_from_fred([
        _fred("DGS10", latest=4.20),
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
    assert "severe" in severities  # yield-curve inversion


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
