"""
Tests for the Deep-Dive additions:
  - Capital Allocation block + 9th scoring vital (buybacks + dividends),
  - Earnings Track Record (beat/miss),
  - Hidden Market Signals (congress reused from holders + short interest).

All three are built from services the app already runs; these lock the
transformation math so the report stays consistent and correct.
"""

from __future__ import annotations

from app.schemas.earnings import EarningsQuarterSchema, EarningsResponse
from app.schemas.signal_of_confidence import (
    DividendInfoSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services.agents.ticker_report_data_collector import (
    _attach_earnings_track_record,
    _build_capital_allocation_block,
    _build_capital_allocation_vital,
    _build_hidden_market_signals,
)


def _soc(
    *, dividend_yield=0.0, buyback_yield=0.0, share_count_change=0.0,
    buyback_status="Low", dividend_status="Fair",
) -> SignalOfConfidenceResponse:
    return SignalOfConfidenceResponse(
        symbol="TEST",
        data_points=[],
        summary=SignalOfConfidenceSummarySchema(
            total_yield=dividend_yield + buyback_yield,
            dividend_yield=dividend_yield,
            buyback_yield=buyback_yield,
            share_count_change=share_count_change,
        ),
        dividend_info=DividendInfoSchema(
            status=dividend_status, buyback_status=buyback_status,
        ),
    )


# ── Capital Allocation ──────────────────────────────────────────────


def test_capital_allocation_vital_rewards_buybacks_and_yield():
    disciplined = _build_capital_allocation_vital(
        _soc(dividend_yield=2.0, buyback_yield=4.0, share_count_change=-4.0)
    )
    diluting = _build_capital_allocation_vital(
        _soc(dividend_yield=0.0, buyback_yield=0.0, share_count_change=6.0)
    )
    assert disciplined["score"]["value"] > diluting["score"]["value"]
    assert disciplined["score"]["value"] >= 6.5  # reads "good"
    assert diluting["score"]["value"] < 4.0       # reads "critical"


def test_capital_allocation_vital_none_when_no_data():
    # None → the persona scorer renormalizes it out (no deflation).
    assert _build_capital_allocation_vital(None) is None
    assert _build_capital_allocation_block(None) is None


def test_capital_allocation_block_surfaces_status():
    block = _build_capital_allocation_block(
        _soc(dividend_yield=1.3, buyback_status="Very High", share_count_change=-3.2)
    )
    assert block["buyback_status"] == "Very High"
    assert block["dividend_yield"] == 1.3
    assert block["share_count_change"] == -3.2


# ── Earnings Track Record ───────────────────────────────────────────


def _earnings(surprises) -> EarningsResponse:
    quarters = [
        EarningsQuarterSchema(
            quarter=f"Q{i + 1} '24", actual_value=10.0, estimate_value=9.0,
            surprise_percent=s, fiscal_date=f"2024-0{i + 1}-01",
        )
        for i, s in enumerate(surprises)
    ]
    return EarningsResponse(
        symbol="TEST", eps_quarters=quarters, revenue_quarters=[], price_history=[],
    )


def test_earnings_track_record_beat_summary():
    rf: dict = {}
    _attach_earnings_track_record(rf, _earnings([5.0, -2.0, 3.0, 1.0]))
    assert len(rf["earnings_track_record"]) == 4
    assert rf["beat_summary"] == "Beat 3 of 4"
    assert rf["earnings_track_record"][0]["beat"] is True
    assert rf["earnings_track_record"][1]["beat"] is False


def test_earnings_track_record_caps_at_six_most_recent():
    rf: dict = {}
    _attach_earnings_track_record(rf, _earnings([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]))
    assert len(rf["earnings_track_record"]) == 6  # last 6 reported


def test_earnings_track_record_empty_when_none():
    rf: dict = {}
    _attach_earnings_track_record(rf, None)
    assert rf["earnings_track_record"] == []
    assert rf["beat_summary"] is None


# ── Hidden Market Signals ───────────────────────────────────────────


def test_hidden_market_signals_short_interest_only():
    si = {
        "shares_short": 5_000_000, "short_ratio": 1.8, "short_change_3m": 12.0,
        "settlement_date": "2026-05-15",
        "history": [
            {"settlement_date": "2026-04-15", "shares_short": 4_500_000, "days_to_cover": 1.6},
            {"settlement_date": "2026-05-15", "shares_short": 5_000_000, "days_to_cover": 1.8},
        ],
    }
    hms = _build_hidden_market_signals(None, si, shares_outstanding=100_000_000)
    assert hms["congress"] is None
    assert hms["short_interest"]["percent_of_float"] == 5.0  # 5M / 100M
    assert hms["short_interest"]["days_to_cover"] == 1.8
    assert len(hms["short_interest"]["history"]) == 2


def test_hidden_market_signals_none_when_both_absent():
    # No congress, no short interest → module hidden.
    assert _build_hidden_market_signals(None, None, None) is None
