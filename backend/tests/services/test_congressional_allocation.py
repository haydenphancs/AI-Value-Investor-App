"""
Tests for the chronological allocation reconstruction in
_aggregate_congressional_trades.

The walk must produce consistent previous/new allocations that reflect
what portfolio state looked like at each trade time. Bucket midpoints are
used for dollar estimates — allocations are directional, not exact.

Test dates are kept within the last 90 days (the ``recent`` filter window)
of today (2026-04-20) so trades aren't dropped from the returned group.
"""

from unittest.mock import MagicMock

import pytest

from app.services.whale_service import WhaleService


def _make_trade(symbol: str, ttype: str, amount: str, date: str) -> dict:
    """Shape matches FMP senate-latest / house-latest response."""
    return {
        "symbol": symbol,
        "type": ttype,
        "amount": amount,
        "transactionDate": date,
        "assetDescription": f"{symbol} Inc.",
    }


@pytest.fixture
def service():
    # _aggregate_congressional_trades does not touch any async deps,
    # so we don't need a real FMP client or Supabase.
    svc = WhaleService.__new__(WhaleService)
    svc.fmp = MagicMock()
    return svc


def test_single_buy_is_full_portfolio(service):
    raw = [_make_trade("AAPL", "purchase", "$1,001 - $15,000", "2026-03-10")]
    _, trade_group, _ = service._aggregate_congressional_trades(
        raw, "2026-04-20"
    )
    trades = trade_group["trades"]
    assert len(trades) == 1
    assert trades[0]["previous_allocation"] == 0.0
    assert trades[0]["new_allocation"] == 100.0
    assert trades[0]["trade_type"] == "New"
    assert trades[0]["amount_range"] == "$1,001 - $15,000"


def test_second_buy_same_ticker_chains_allocations(service):
    # Two identical buys of AAPL → second trade's prev == first's new (100%)
    raw = [
        _make_trade("AAPL", "purchase", "$1,001 - $15,000", "2026-02-10"),
        _make_trade("AAPL", "purchase", "$1,001 - $15,000", "2026-03-10"),
    ]
    _, trade_group, _ = service._aggregate_congressional_trades(
        raw, "2026-04-20"
    )
    trades = sorted(trade_group["trades"], key=lambda t: t["date"])
    assert trades[0]["previous_allocation"] == 0.0
    assert trades[0]["new_allocation"] == 100.0
    assert trades[1]["previous_allocation"] == 100.0
    assert trades[1]["new_allocation"] == 100.0
    assert trades[1]["trade_type"] == "Increased"


def test_two_different_tickers_shrink_each_other(service):
    # Equal buys of AAPL then MSFT → AAPL was 100%, now 50%
    raw = [
        _make_trade("AAPL", "purchase", "$1,001 - $15,000", "2026-02-10"),
        _make_trade("MSFT", "purchase", "$1,001 - $15,000", "2026-03-10"),
    ]
    _, trade_group, _ = service._aggregate_congressional_trades(
        raw, "2026-04-20"
    )
    by_ticker = {t["ticker"]: t for t in trade_group["trades"]}
    # MSFT trade sees AAPL holding 100%, adds itself → 50/50 split
    assert by_ticker["MSFT"]["previous_allocation"] == 0.0
    assert by_ticker["MSFT"]["new_allocation"] == 50.0
    # AAPL trade was when portfolio was empty
    assert by_ticker["AAPL"]["previous_allocation"] == 0.0
    assert by_ticker["AAPL"]["new_allocation"] == 100.0


def test_full_sale_zeros_allocation(service):
    raw = [
        _make_trade("AAPL", "purchase", "$15,001 - $50,000", "2026-02-10"),
        _make_trade("MSFT", "purchase", "$15,001 - $50,000", "2026-02-15"),
        _make_trade("AAPL", "sale_full", "$15,001 - $50,000", "2026-03-10"),
    ]
    _, trade_group, _ = service._aggregate_congressional_trades(
        raw, "2026-04-20"
    )
    sale = next(
        t for t in trade_group["trades"]
        if t["ticker"] == "AAPL" and t["action"] == "SOLD"
    )
    assert sale["trade_type"] == "Closed"
    assert sale["new_allocation"] == 0.0
    # Before sale, AAPL was 50% (equal to MSFT)
    assert sale["previous_allocation"] == 50.0


def test_oversell_clamps_no_negative_allocation(service):
    # Sell more than we bought → position clamps at 0, no negative allocation
    raw = [
        _make_trade("AAPL", "purchase", "$1,001 - $15,000", "2026-02-10"),
        _make_trade("AAPL", "sale_full", "$1,000,001 - $5,000,000", "2026-03-10"),
    ]
    _, trade_group, _ = service._aggregate_congressional_trades(
        raw, "2026-04-20"
    )
    sale = next(t for t in trade_group["trades"] if t["action"] == "SOLD")
    assert sale["new_allocation"] == 0.0
    assert sale["previous_allocation"] >= 0.0  # never negative


def test_unsorted_input_produces_date_ordered_walk(service):
    # Feed trades out of chronological order — allocations must still be
    # computed in true time order
    raw = [
        _make_trade("MSFT", "purchase", "$1,001 - $15,000", "2026-03-10"),
        _make_trade("AAPL", "purchase", "$1,001 - $15,000", "2026-02-10"),
    ]
    _, trade_group, _ = service._aggregate_congressional_trades(
        raw, "2026-04-20"
    )
    by_ticker = {t["ticker"]: t for t in trade_group["trades"]}
    # AAPL was first in time → 100% at that moment
    assert by_ticker["AAPL"]["new_allocation"] == 100.0
    # MSFT second → 50% split
    assert by_ticker["MSFT"]["new_allocation"] == 50.0


def test_holdings_sum_to_roughly_100_percent(service):
    raw = [
        _make_trade("AAPL", "purchase", "$15,001 - $50,000", "2026-02-10"),
        _make_trade("MSFT", "purchase", "$15,001 - $50,000", "2026-02-15"),
        _make_trade("GOOG", "purchase", "$50,001 - $100,000", "2026-03-01"),
    ]
    holdings, _, _ = service._aggregate_congressional_trades(
        raw, "2026-04-20"
    )
    total_allocation = sum(h["allocation"] for h in holdings)
    assert 99.0 <= total_allocation <= 101.0  # rounding tolerance
