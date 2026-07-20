"""
Whale-tab deep-review hardening — outlier tests for the 2026-07-19 adversarial
review. Each test pins a CONFIRMED defect fix; all are pure transforms (no
network, no Supabase) exercised through WhaleService.__new__ or module helpers.

Covers:
- 13F stock-split fabricated as a huge BOUGHT trade (_diff_quarters split
  restatement + _suspicious_split_tickers + _split_ratio_in_window).
- _build_holdings duplicate-ticker (UNIQUE violation) + negative-value total
  (>100 allocation CHECK violation) + non-finite (NaN/Inf → 500) inputs.
- Congress: re-buy after a full-sale Close mis-typed "Increased"; fully-undated
  trades re-stamped "today"; unparseable amount fabricated as $8,000 / "$0"
  range; a pure wash labeled "Net buying".
- _alert_is_expired (expired followed-whale alert was shown) + _finite_float.

Run via `python -m pytest` from backend/.
"""

import math

import pytest

from app.services.whale_service import (
    WhaleService,
    _finite_float,
    _split_ratio_in_window,
    _quarter_end_date,
    _alert_is_expired,
)


def _svc() -> WhaleService:
    # Bypass __init__ (which builds an FMP client) — pure math only.
    return WhaleService.__new__(WhaleService)


def _h(symbol, value, shares):
    """A raw FMP 13F extract row."""
    return {"symbol": symbol, "value": value, "sharesNumber": shares}


def _craw(symbol, type_, amount, tx=None, disc=None):
    """A raw FMP congressional trade row (omit dates to simulate undated)."""
    row = {"symbol": symbol, "type": type_, "amount": amount,
           "assetDescription": f"{symbol} Corp"}
    if tx is not None:
        row["transactionDate"] = tx
    if disc is not None:
        row["disclosureDate"] = disc
    return row


# ── _finite_float ────────────────────────────────────────────────────

def test_finite_float_rejects_non_finite_and_garbage():
    assert _finite_float(float("nan")) == 0.0
    assert _finite_float(float("inf")) == 0.0
    assert _finite_float(float("-inf")) == 0.0
    assert _finite_float("NaN") == 0.0            # FMP token → parses to nan
    assert _finite_float(None) == 0.0
    assert _finite_float("abc") == 0.0
    assert _finite_float("5") == 5.0
    assert _finite_float(3.5) == 3.5
    assert _finite_float(None, default=-1.0) == -1.0


# ── 13F split adjustment (#1) ────────────────────────────────────────

def test_diff_quarters_without_split_ratio_fabricates_the_split_as_a_trade():
    # Documents the BUG the fix prevents: a held-through-10:1-split position
    # (raw share count 1000 -> 10000) with NO split_ratios looks like a huge buy.
    svc = _svc()
    current = [_h("NVDA", 1_100_000, 10_000)]
    previous = [_h("NVDA", 900_000, 1_000)]
    group = svc._diff_quarters(current, previous, "2024-06-30", 1_100_000)
    assert group is not None
    nvda = group["trades"][0]
    assert nvda["action"] == "BOUGHT"
    assert nvda["amount"] > 900_000  # ~ $990K fabricated from the split


def test_diff_quarters_with_split_ratio_suppresses_the_fabricated_trade():
    # Same holding, now told about the 10:1 → the split is restated away and no
    # trade is fabricated (only NVDA present → whole group collapses to None).
    svc = _svc()
    current = [_h("NVDA", 1_100_000, 10_000)]
    previous = [_h("NVDA", 900_000, 1_000)]
    group = svc._diff_quarters(
        current, previous, "2024-06-30", 1_100_000, {"NVDA": 10.0}
    )
    assert group is None


def test_diff_quarters_split_plus_real_buy_keeps_only_the_real_component():
    # Held 1000, 10:1 split -> 10000, then bought 500 more post-split -> 10500.
    # implied price ~ $110. The real buy is 500 sh (~$55K), NOT ~$1.05M.
    svc = _svc()
    current = [_h("NVDA", 1_155_000, 10_500)]   # 10500 * 110
    previous = [_h("NVDA", 900_000, 1_000)]
    group = svc._diff_quarters(
        current, previous, "2024-06-30", 1_155_000, {"NVDA": 10.0}
    )
    assert group is not None
    nvda = group["trades"][0]
    assert nvda["action"] == "BOUGHT"
    assert nvda["amount"] == pytest.approx(55_000, rel=0.02)


def test_suspicious_split_tickers_flags_split_but_not_a_real_buy():
    svc = _svc()
    # Pure split: shares 10x, price /10, value preserved → suspicious.
    current = [_h("NVDA", 900_000, 10_000), _h("AAPL", 360_000, 2_000)]
    # AAPL: shares 2x but price flat ($180) → value doubled → a real buy.
    previous = [_h("NVDA", 900_000, 1_000), _h("AAPL", 180_000, 1_000)]
    suspects = svc._suspicious_split_tickers(current, previous)
    assert "NVDA" in suspects
    assert "AAPL" not in suspects


def test_split_ratio_in_window():
    splits = [{"date": "2024-06-10", "numerator": 10, "denominator": 1}]
    # split falls inside (Q1-end, Q2-end]
    assert _split_ratio_in_window(splits, "2024-03-31", "2024-06-30") == 10.0
    # outside the window → no adjustment
    assert _split_ratio_in_window(splits, "2024-06-30", "2024-09-30") == 1.0
    assert _split_ratio_in_window([], "2024-03-31", "2024-06-30") == 1.0
    assert _split_ratio_in_window(None, None, "2024-06-30") == 1.0
    # non-finite / zero-denominator ratios are dropped
    bad = [{"date": "2024-06-10", "numerator": 1, "denominator": 0}]
    assert _split_ratio_in_window(bad, "2024-03-31", "2024-06-30") == 1.0


def test_quarter_end_date():
    assert _quarter_end_date(2024, 2) == "2024-06-30"
    assert _quarter_end_date(2025, 4) == "2025-12-31"


# ── _build_holdings dedup / positive-total / finite (#5,#6,#11,NaN) ──

def test_build_holdings_dedups_duplicate_ticker():
    # FMP maps two CUSIP rows onto one symbol — must merge, not emit two rows
    # (the second would violate whale_holdings UNIQUE(whale_id, ticker)).
    svc = _svc()
    raw = [
        _h("GOOG", 500_000, 1_000),
        _h("GOOG", 300_000, 600),
        _h("MSFT", 200_000, 400),
    ]
    holdings = svc._build_holdings(raw)
    assert len(holdings) == 2
    goog = next(h for h in holdings if h["ticker"] == "GOOG")
    assert goog["value"] == 800_000
    assert goog["shares"] == 1_600
    # allocation computed off the merged value: 800k / 1.0M = 80%
    assert goog["allocation"] == pytest.approx(80.0)
    tickers = [h["ticker"] for h in holdings]
    assert len(tickers) == len(set(tickers))  # no duplicate ticker rows


def test_build_holdings_positive_only_total_bounds_allocation():
    # A negative FMP value must not deflate the denominator and push a real
    # holding's allocation past the numeric(7,4)/CHECK(0..100) column.
    svc = _svc()
    raw = [_h("AAPL", 1_000_000, 1_000), _h("XYZ", -990_000, 50)]
    holdings = svc._build_holdings(raw)
    assert len(holdings) == 1                      # XYZ (negative) dropped
    assert holdings[0]["ticker"] == "AAPL"
    assert holdings[0]["allocation"] == pytest.approx(100.0)
    assert all(0.0 <= h["allocation"] <= 100.0 for h in holdings)


def test_build_holdings_skips_non_finite_value():
    svc = _svc()
    raw = [_h("AAPL", float("nan"), 1_000), _h("MSFT", 200_000, 400)]
    holdings = svc._build_holdings(raw)
    assert [h["ticker"] for h in holdings] == ["MSFT"]
    assert all(math.isfinite(h["allocation"]) for h in holdings)


def test_build_holdings_all_non_positive_returns_empty():
    svc = _svc()
    assert svc._build_holdings([_h("A", 0, 0), _h("B", float("inf"), 5)]) == []


# ── Congress re-buy after a Close is "New" (#9) ─────────────────────

def test_congress_rebuy_after_full_sale_is_new_not_increased():
    svc = _svc()
    raw = [
        _craw("AAPL", "Purchase", "$15,001 - $50,000", "2026-01-01", "2026-01-05"),
        _craw("AAPL", "sale_full", "$15,001 - $50,000", "2026-02-01", "2026-02-05"),
        _craw("AAPL", "Purchase", "$15,001 - $50,000", "2026-03-01", "2026-03-05"),
    ]
    _h_out, groups, _s = svc._aggregate_congressional_trades(raw, "2026-04-01")
    all_trades = [t for g in groups for t in g["trades"]]
    first_buy = next(t for t in all_trades if t["date"] == "2026-01-01")
    close = next(t for t in all_trades if t["date"] == "2026-02-01")
    rebuy = next(t for t in all_trades if t["date"] == "2026-03-01")
    assert first_buy["trade_type"] == "New"
    assert close["trade_type"] == "Closed"
    assert rebuy["trade_type"] == "New"   # was wrongly "Increased"


# ── Congress fully-undated trades are dropped (#10) ─────────────────

def test_congress_fully_undated_trade_is_dropped():
    svc = _svc()
    raw = [
        _craw("AAPL", "Purchase", "$15,001 - $50,000", "2026-03-01", "2026-03-10"),
        _craw("MSFT", "Purchase", "$15,001 - $50,000"),  # no tx, no disclosure
    ]
    _h_out, groups, _s = svc._aggregate_congressional_trades(raw, "2026-04-01")
    tickers = {t["ticker"] for g in groups for t in g["trades"]}
    assert "AAPL" in tickers
    assert "MSFT" not in tickers                     # undated → dropped
    dates = {g["date"] for g in groups}
    assert "2026-04-01" not in dates                 # never re-stamped "today"


# ── Congress unparseable amount: no $8,000, no "$0" range (#3) ───────

def test_congress_unparseable_amount_is_not_fabricated():
    svc = _svc()
    raw = [_craw("BRK-A", "Purchase", "$50,000,000+", "2026-03-01", "2026-03-10")]
    _h_out, groups, _s = svc._aggregate_congressional_trades(raw, "2026-04-01")
    assert len(groups) == 1
    g = groups[0]
    trade = g["trades"][0]
    assert trade["amount"] == 0.0                    # NOT the $8,000 midpoint
    assert trade["amount_range"] == "$50,000,000+"   # honest raw bucket kept
    assert g["net_amount_range"] is None             # no fabricated "$0" range
    assert not any("$0" in ins for ins in g["insights"])


# ── Congress pure wash: no "Net buying" range (#4) ──────────────────

def test_congress_pure_wash_suppresses_net_range():
    svc = _svc()
    # Same disclosure filing: buy and sell of the same bucket → net zero.
    raw = [
        _craw("AAPL", "Purchase", "$100,001 - $250,000", "2026-03-01", "2026-03-10"),
        _craw("AAPL", "sale_partial", "$100,001 - $250,000", "2026-03-02", "2026-03-10"),
    ]
    _h_out, groups, _s = svc._aggregate_congressional_trades(raw, "2026-04-01")
    assert len(groups) == 1
    g = groups[0]
    assert g["net_amount"] == 0.0
    assert g["net_amount_range"] is None             # not "Net buying of $100K–$250K"
    assert not any("Net buying" in ins for ins in g["insights"])


def test_congress_single_direction_still_shows_range():
    # Regression guard: a genuinely one-directional filing must KEEP its range.
    svc = _svc()
    raw = [
        _craw("ORCL", "Sale", "$250,001 - $500,000", "2026-06-01", "2026-06-30"),
        _craw("NVDA", "Sale", "$1,001 - $15,000", "2026-06-02", "2026-06-30"),
    ]
    _h_out, groups, _s = svc._aggregate_congressional_trades(raw, "2026-07-01")
    assert groups[0]["net_action"] == "SOLD"
    assert groups[0]["net_amount_range"] == "$251K – $515K"


# ── _alert_is_expired (#12) ─────────────────────────────────────────

def test_alert_is_expired():
    assert _alert_is_expired({"expires_at": "2000-01-01T00:00:00+00:00"}) is True
    assert _alert_is_expired({"expires_at": "2999-01-01T00:00:00+00:00"}) is False
    # missing / null / unparseable → treated as non-expiring (never hides a live alert)
    assert _alert_is_expired({}) is False
    assert _alert_is_expired({"expires_at": None}) is False
    assert _alert_is_expired({"expires_at": "garbage"}) is False
    # tolerates the trailing-Z form
    assert _alert_is_expired({"expires_at": "2000-01-01T00:00:00Z"}) is True
